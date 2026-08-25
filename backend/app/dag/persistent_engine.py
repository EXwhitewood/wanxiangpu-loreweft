import asyncio
import json
import logging
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import select, update

from app.agents.base import BaseAgent
from app.dag.engine import AgentNode, SimpleDAG
from app.db.db_models import async_session, WorkflowExecution, WorkflowStep
from app.utils.json_safety import to_json_safe

logger = logging.getLogger(__name__)

_paused_executions: set[str] = set()
_sse_listeners: dict[str, list[asyncio.Queue]] = {}


def subscribe_execution(execution_id: str) -> asyncio.Queue:
    queue = asyncio.Queue()
    if execution_id not in _sse_listeners:
        _sse_listeners[execution_id] = []
    _sse_listeners[execution_id].append(queue)
    return queue


def unsubscribe_execution(execution_id: str, queue: asyncio.Queue):
    if execution_id in _sse_listeners:
        _sse_listeners[execution_id] = [q for q in _sse_listeners[execution_id] if q is not queue]


async def _broadcast_event(execution_id: str, event: dict):
    if execution_id in _sse_listeners:
        for queue in _sse_listeners[execution_id]:
            await queue.put(event)


def request_pause(execution_id: str):
    _paused_executions.add(execution_id)


def request_resume(execution_id: str):
    _paused_executions.discard(execution_id)


def is_paused(execution_id: str) -> bool:
    return execution_id in _paused_executions


class PersistentDAG:
    def __init__(self, project_id: str, trigger_type: str = ""):
        self.nodes: dict[str, AgentNode] = {}
        self.project_id = project_id
        self.trigger_type = trigger_type
        self._execution_id: str | None = None

    def add_node(self, node: AgentNode) -> None:
        self.nodes[node.name] = node

    async def run(self, initial_context: dict) -> dict:
        async with async_session() as session:
            execution = WorkflowExecution(
                project_id=self.project_id,
                status="running",
                trigger_type=self.trigger_type,
                input_context=initial_context,
            )
            session.add(execution)
            await session.commit()
            await session.refresh(execution)
            self._execution_id = str(execution.id)

        layers = SimpleDAG._topological_sort(self)

        async with async_session() as session:
            execution.total_layers = len(layers)
            session.add(execution)
            await session.commit()

        for step_name in self.nodes:
            async with async_session() as session:
                step = WorkflowStep(
                    execution_id=execution.id,
                    agent_name=step_name,
                    layer=0,
                    status="pending",
                )
                session.add(step)
                await session.commit()

        layer_idx = 0
        for layer in layers:
            for name in layer:
                if name in self.nodes:
                    async with async_session() as session:
                        await session.execute(
                            update(WorkflowStep)
                            .where(
                                WorkflowStep.execution_id == execution.id,
                                WorkflowStep.agent_name == name,
                            )
                            .values(layer=layer_idx)
                        )
                        await session.commit()

            while is_paused(self._execution_id):
                async with async_session() as session:
                    await session.execute(
                        update(WorkflowExecution)
                        .where(WorkflowExecution.id == execution.id)
                        .values(status="paused")
                    )
                    await session.commit()
                await asyncio.sleep(1)

            async with async_session() as session:
                await session.execute(
                    update(WorkflowExecution)
                    .where(WorkflowExecution.id == execution.id)
                    .values(status="running", current_layer=layer_idx)
                )
                await session.commit()

            await _broadcast_event(self._execution_id, {
                "type": "layer_start",
                "layer": layer_idx,
                "agents": list(layer),
            })

            tasks = []
            for name in layer:
                if name in self.nodes:
                    node = self.nodes[name]
                    tasks.append(self._execute_persistent_node(node, initial_context, execution.id, layer_idx))

            if tasks:
                results = await asyncio.gather(*tasks)
                for name, result in zip(layer, results):
                    if result is not None:
                        initial_context.update(result)

            layer_idx += 1

            await _broadcast_event(self._execution_id, {
                "type": "layer_complete",
                "layer": layer_idx - 1,
            })

        async with async_session() as session:
            await session.execute(
                update(WorkflowExecution)
                .where(WorkflowExecution.id == execution.id)
                .values(status="completed", result_context=initial_context, current_layer=layer_idx)
            )
            await session.commit()

        await _broadcast_event(self._execution_id, {
            "type": "execution_complete",
            "status": "completed",
        })

        return initial_context

    async def _execute_persistent_node(
        self, node: AgentNode, context: dict, execution_id, layer: int
    ) -> dict | None:
        started_at = datetime.now(timezone.utc)

        async with async_session() as session:
            await session.execute(
                update(WorkflowStep)
                .where(
                    WorkflowStep.execution_id == execution_id,
                    WorkflowStep.agent_name == node.name,
                )
                .values(status="running", started_at=started_at, input_snapshot=to_json_safe(context))
            )
            await session.commit()

        await _broadcast_event(str(execution_id), {
            "type": "step_start",
            "agent_name": node.name,
            "layer": layer,
        })

        try:
            result = await node.agent.execute(context)
            completed_at = datetime.now(timezone.utc)
            duration_ms = int((completed_at - started_at).total_seconds() * 1000)

            async with async_session() as session:
                await session.execute(
                    update(WorkflowStep)
                    .where(
                        WorkflowStep.execution_id == execution_id,
                        WorkflowStep.agent_name == node.name,
                    )
                    .values(
                        status="completed",
                        output_snapshot=to_json_safe(result or {}),
                        completed_at=completed_at,
                        duration_ms=duration_ms,
                    )
                )
                await session.commit()

            await _broadcast_event(str(execution_id), {
                "type": "step_complete",
                "agent_name": node.name,
                "layer": layer,
                "duration_ms": duration_ms,
            })

            return result

        except Exception as e:
            completed_at = datetime.now(timezone.utc)
            duration_ms = int((completed_at - started_at).total_seconds() * 1000)

            async with async_session() as session:
                await session.execute(
                    update(WorkflowStep)
                    .where(
                        WorkflowStep.execution_id == execution_id,
                        WorkflowStep.agent_name == node.name,
                    )
                    .values(
                        status="failed",
                        error_message=str(e),
                        completed_at=completed_at,
                        duration_ms=duration_ms,
                    )
                )
                await session.commit()

            await _broadcast_event(str(execution_id), {
                "type": "step_failed",
                "agent_name": node.name,
                "layer": layer,
                "error": str(e),
            })

            return {"error": {node.name: str(e)}}

    @staticmethod
    async def recover_interrupted():
        async with async_session() as session:
            result = await session.execute(
                select(WorkflowExecution).where(WorkflowExecution.status == "running")
            )
            executions = result.scalars().all()
            recovered = 0
            for execution in executions:
                execution.status = "interrupted"
                session.add(execution)
                recovered += 1
            await session.commit()
        return recovered