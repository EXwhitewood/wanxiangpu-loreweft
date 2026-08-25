import asyncio
import json

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select, or_

from app.db.db_models import async_session, WorkflowExecution, WorkflowStep
from app.dag.persistent_engine import (
    subscribe_execution,
    unsubscribe_execution,
    request_pause,
    request_resume,
    is_paused,
    PersistentDAG,
)
from app.services.workflow_performance_service import summarize_workflow_performance

router = APIRouter(prefix="/workflows", tags=["工作流"])


@router.get("")
async def list_executions(project_id: str | None = None, limit: int = 20, offset: int = 0):
    async with async_session() as session:
        query = select(WorkflowExecution).order_by(WorkflowExecution.created_at.desc())
        if project_id:
            query = query.where(WorkflowExecution.project_id == project_id)
        result = await session.execute(
            query.offset(offset).limit(limit)
        )
        executions = result.scalars().all()
        return [
            {
                "id": str(e.id),
                "project_id": str(e.project_id),
                "status": e.status,
                "trigger_type": e.trigger_type,
                "current_layer": e.current_layer,
                "total_layers": e.total_layers,
                "error_message": e.error_message,
                "created_at": e.created_at.isoformat() if e.created_at else None,
                "updated_at": e.updated_at.isoformat() if e.updated_at else None,
            }
            for e in executions
        ]


@router.get("/performance-summary")
async def get_workflow_performance_summary(
    project_id: str | None = None,
    trigger_type: str | None = None,
    completed_samples: int = Query(default=20, ge=10, le=100),
):
    """Return a read-only latency/outcome baseline for recent terminal workflows.

    This static route must remain above ``/{execution_id}`` so FastAPI does not
    interpret ``performance-summary`` as an execution identifier.
    """

    async with async_session() as session:
        return await summarize_workflow_performance(
            session,
            project_id=project_id,
            trigger_type=trigger_type,
            completed_samples=completed_samples,
        )


@router.get("/{execution_id}")
async def get_execution(execution_id: str):
    async with async_session() as session:
        result = await session.execute(
            select(WorkflowExecution).where(WorkflowExecution.id == execution_id)
        )
        execution = result.scalar_one_or_none()
        if not execution:
            raise HTTPException(status_code=404, detail="Execution not found")

        step_result = await session.execute(
            select(WorkflowStep)
            .where(WorkflowStep.execution_id == execution_id)
            .order_by(WorkflowStep.layer, WorkflowStep.agent_name)
        )
        steps = step_result.scalars().all()

        layers_dict: dict[int, list] = {}
        for s in steps:
            layer = s.layer
            if layer not in layers_dict:
                layers_dict[layer] = []
            layers_dict[layer].append({
                "id": str(s.id),
                "agent_name": s.agent_name,
                "status": s.status,
                "duration_ms": s.duration_ms,
                "error_message": s.error_message,
                "started_at": s.started_at.isoformat() if s.started_at else None,
                "completed_at": s.completed_at.isoformat() if s.completed_at else None,
            })

        return {
            "id": str(execution.id),
            "project_id": str(execution.project_id),
            "status": execution.status,
            "trigger_type": execution.trigger_type,
            "current_layer": execution.current_layer,
            "total_layers": execution.total_layers,
            "error_message": execution.error_message,
            "created_at": execution.created_at.isoformat() if execution.created_at else None,
            "updated_at": execution.updated_at.isoformat() if execution.updated_at else None,
            "layers": layers_dict,
        }


@router.get("/{execution_id}/steps/{step_id}")
async def get_step_detail(execution_id: str, step_id: str):
    async with async_session() as session:
        result = await session.execute(
            select(WorkflowStep).where(
                WorkflowStep.id == step_id,
                WorkflowStep.execution_id == execution_id,
            )
        )
        step = result.scalar_one_or_none()
        if not step:
            raise HTTPException(status_code=404, detail="Step not found")

        return {
            "id": str(step.id),
            "execution_id": str(step.execution_id),
            "agent_name": step.agent_name,
            "layer": step.layer,
            "status": step.status,
            "input_snapshot": step.input_snapshot,
            "output_snapshot": step.output_snapshot,
            "error_message": step.error_message,
            "duration_ms": step.duration_ms,
            "started_at": step.started_at.isoformat() if step.started_at else None,
            "completed_at": step.completed_at.isoformat() if step.completed_at else None,
        }


@router.post("/{execution_id}/pause")
async def pause_execution(execution_id: str):
    request_pause(execution_id)
    async with async_session() as session:
        from sqlalchemy import update
        await session.execute(
            update(WorkflowExecution)
            .where(WorkflowExecution.id == execution_id)
            .values(status="paused")
        )
        await session.commit()
    return {"status": "paused", "execution_id": execution_id}


@router.post("/{execution_id}/resume")
async def resume_execution(execution_id: str):
    request_resume(execution_id)
    async with async_session() as session:
        from sqlalchemy import update
        await session.execute(
            update(WorkflowExecution)
            .where(WorkflowExecution.id == execution_id)
            .values(status="running")
        )
        await session.commit()
    return {"status": "running", "execution_id": execution_id}


@router.get("/{execution_id}/stream")
async def stream_execution(execution_id: str):
    from fastapi.responses import StreamingResponse

    queue = subscribe_execution(execution_id)

    async def event_generator():
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30)
                    yield f"data: {json.dumps(event, default=str)}\n\n"
                    if event.get("type") == "execution_complete":
                        break
                except asyncio.TimeoutError:
                    yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"

                    async with async_session() as session:
                        result = await session.execute(
                            select(WorkflowExecution.status).where(
                                WorkflowExecution.id == execution_id
                            )
                        )
                        row = result.scalar_one_or_none()
                        if row and row in ("completed", "failed", "interrupted"):
                            yield f"data: {json.dumps({'type': 'execution_complete', 'status': row})}\n\n"
                            break
        finally:
            unsubscribe_execution(execution_id, queue)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.post("/recover")
async def recover_interrupted():
    recovered = await PersistentDAG.recover_interrupted()
    return {"recovered": recovered}
