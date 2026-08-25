"""Runtime scheduler for editor chapter-generation DAGs.

方案12：增加节点级持久化能力，复用 WorkflowExecution/WorkflowStep 表。
        R4-1：resume_from 已接入 _run_editor_generation，崩溃恢复可跳过已完成 DAG 节点。
方案16：DAG 可观测性补全——层级进度广播、节点数据血缘、skip 原因、
        并行层失败节点详情、所有节点统一 phase trace。
方案17：数据传递改为 DAG 上下文——DAGContext + node_outputs 存储，
        所有 handler 必须接收 context 参数（双签名宽容设计已移除）。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Iterable

from sqlalchemy import select, update

from app.db.db_models import async_session, WorkflowExecution, WorkflowStep
from app.services.alert_service import get_alert_service
from app.services.editor_generation_dag import EditorDagNode, EditorGenerationDAG
from app.utils.json_safety import to_json_safe

logger = logging.getLogger(__name__)

NodeHandler = Callable[..., Awaitable[object]]
ProgressCallback = Callable[[dict], None]


class DAGContext:
    """方案17：handler 执行上下文，提供上游数据访问。

    逐步替代闭包共享变量。handler 可通过 context.get_upstream_output()
    获取上游节点的输出，而非从闭包变量读取。

    R4-8：双签名宽容设计已移除，所有 handler 必须声明为
    ``async def handler(node, context: DAGContext)`` 接收 context 参数。
    """

    def __init__(
        self,
        node: EditorDagNode,
        upstream_outputs: dict[str, object],
        runtime: "EditorGenerationDAGRuntime",
    ):
        self.node = node
        self._upstream = upstream_outputs
        self._runtime = runtime

    def get_upstream_output(self, node_id: str) -> dict | None:
        """获取指定上游节点的输出。"""
        output = self._upstream.get(node_id)
        if output is None:
            return None
        if isinstance(output, dict):
            return output
        return {"result": output}

    def get_all_upstream_outputs(self) -> dict[str, object]:
        """获取所有上游节点的输出（深拷贝，修改不影响内部状态）。"""
        import copy
        return copy.deepcopy(self._upstream)

    def get_shared_state(self, key: str, default=None):
        """获取运行时共享状态（用于过渡期兼容闭包变量）。

        方案17 过渡阶段：handler 仍可通过此方法访问运行时共享状态，
        逐步迁移到纯 context 数据传递后可移除。
        """
        return self._runtime.shared_state.get(key, default)

    def set_shared_state(self, key: str, value) -> None:
        """设置运行时共享状态（用于过渡期兼容闭包变量）。"""
        self._runtime.shared_state[key] = value


# 方案12 步骤5：节点超时控制（秒）
# P1-F5/W3/W4 修复：键名与 DAG node_type 对齐，补全缺失条目，删除孤儿条目
NODE_TIMEOUTS: dict[str, int] = {
    # safe mode 节点
    "chapter_planning": 120,
    "context_compile": 60,
    "chapter_writer": 600,
    "scene_alignment": 60,
    "scene_prepare": 60,
    "scene_generate": 600,
    "scene_quality_fanout": 180,
    "scene_post_extract_fanout": 180,
    "scene_accept": 60,
    "scene_state_commit": 180,
    "chapter_fact_finalize": 120,
    "style_polish": 120,
    "write_chapter": 60,
    # parallel_review mode 节点
    # SceneReviewWorker has its own 300s bounded review timeout. The DAG
    # wrapper must remain strictly larger so the handler can persist the real
    # failure state before the outer scheduler decides the node outcome.
    "parallel_scene_review": 540,
    "chapter_review": 120,
    # FBI 受理节点内部调用 FBIReviewBlueprintAgent._run_session（3 轮 LLM 调用，
    # 每轮 timeout=120s，最坏 360s）+ 归一化/工具执行开销。
    # 节点超时必须 > 内层 3 轮 LLM 总耗时，否则外层先杀掉内层，导致 fbi_case_intake step 卡在 running
    "fbi_chapter_case_intake": 1500,
    "parallel_scene_repair": 900,
    # parallel_scene_recheck 内部是多轮修复闭环（MAX_REPAIR_ROUNDS=2）：
    #   初始 worker.review（无 timeout 包裹，最坏 ~120s）
    #   + 每轮 _auto_repair_scene(timeout=240s) + worker.review(timeout=180s) = 420s
    #   2 轮 = 840s，加初始 review 最坏 960s+。
    # 节点超时必须 > 内层最坏总耗时，否则外层先杀掉内层，导致 parallel_recheck_{N} step 卡 running
    "parallel_scene_recheck": 1200,
    # review_case_delta_merge：合并多场景的 delta 修复结果，涉及 LLM 调用和冲突归并，
    # 60s 不足。调大到 600s 覆盖最坏情况（多场景合并 + LLM 冲突判定）。
    "review_case_delta_merge": 600,
    "ordered_state_commit": 180,
    # final_acceptance 4 个节点共用同一 handler（FinalDeltaRepairRuntime._handler）。
    # 如果 intake 节点被取消/异常，后续节点（blueprint/execute/recheck）会因 self.result is None
    # 重新执行 run()，需要与 intake 相同的超时。4 个节点统一设为 3000s 覆盖最坏情况：
    #   3 轮 delta 循环 × FBIReviewBlueprintAgent（3 轮 LLM × 120s × 3 次重试 = 1080s）= 3240s 最坏
    #   （第 9 次验证 3 轮 × 10 orders 超时 2400s，已提升至 3000s + orders 过滤减少负载）
    "final_acceptance_delta_intake": 3000,
    "final_acceptance_delta_blueprint": 3000,
    "final_acceptance_delta_execute": 3000,
    "final_acceptance_delta_recheck": 3000,
}
DEFAULT_NODE_TIMEOUT = 300

# R4-7：持久化失败超过阈值时标记 persistence_degraded
PERSIST_DEGRADED_THRESHOLD = 3

# F7：可重试异常类型——仅临时性故障（网络/连接）才重试，逻辑错误不重试
# 注意：不包含 TimeoutError（超时可能意味着 handler 已有副作用，重试不安全）
_RETRYABLE_EXCEPTIONS: tuple[type[Exception], ...] = (
    ConnectionError,
    ConnectionResetError,
    ConnectionAbortedError,
    BrokenPipeError,
)
# F7：节点最大重试次数（1 次重试 = 总共 2 次尝试）
MAX_NODE_RETRIES = 1
# F7：重试前延迟（秒）
NODE_RETRY_DELAY_SECONDS = 1.0


def _is_retryable_exception(exc: Exception) -> bool:
    """F7：判断异常是否可重试（仅临时性网络/连接故障）。"""
    if isinstance(exc, _RETRYABLE_EXCEPTIONS):
        return True
    # OSError 中仅网络相关才重试（排除 FileNotFoundError/PermissionError 等）
    if isinstance(exc, OSError) and exc.errno in (
        101,  # Network unreachable
        102,  # Network reset
        103,  # Connection aborted
        104,  # Connection reset by peer
        110,  # Connection timed out
        111,  # Connection refused
    ):
        return True
    return False

# P1-31：并发分组——轻量节点（非 LLM 调用）使用更高并发上限，LLM 节点使用更严格上限
_LIGHT_NODE_TYPES: frozenset[str] = frozenset({
    "chapter_planning",
    "context_compile",
    "scene_prepare",
    "scene_accept",
    "write_chapter",
})


class EditorDAGAbort(Exception):
    """Raised by a handler when workflow execution should stop cleanly."""
    def __init__(self, result: object | None = None):
        super().__init__("editor DAG execution aborted")
        self.result = result


_DAG_ABORT_TERMINAL_STATUSES: frozenset[str] = frozenset({
    "waiting_review",
    "pending_validator_retry",
    "waiting_human_content_review",
    "waiting_human_system_review",
    "waiting_contract_repair",
    "waiting_alias_decision",
    "waiting_retcon_decision",
    "cancelled",
    "failed",
})


def _dag_abort_status(result: object | None) -> str:
    """Map a handler abort result to a persisted terminal status."""
    if isinstance(result, dict):
        result_status = result.get("status")
        if result_status in _DAG_ABORT_TERMINAL_STATUSES:
            return str(result_status)
    return "aborted"


class EditorDAGSafetyError(ValueError):
    """Raised when a DAG layer violates read/write safety rules."""


@dataclass
class EditorDAGRunReport:
    executed_nodes: list[str] = field(default_factory=list)
    # 方案16 步骤3：skipped_nodes 改为 list[dict]，含 node_id + reason
    skipped_nodes: list[dict] = field(default_factory=list)
    # 方案16 步骤4：failed_nodes 改为 list[dict]，含 node_id + error + error_type
    failed_nodes: list[dict] = field(default_factory=list)
    layer_widths: list[int] = field(default_factory=list)
    max_parallel_width: int = 1
    execution_id: str | None = None
    # 方案16 步骤1：层级进度事件流（layer_start / layer_complete / layer_failed）
    layer_events: list[dict] = field(default_factory=list)
    # 方案16 步骤5：每个节点的 phase trace
    node_traces: dict[str, list[dict]] = field(default_factory=dict)
    # P1-33：持久化失败计数（_persist_node_* 写入 DB 失败次数）
    persist_failure_count: int = 0

    def failed_node_ids(self) -> set[str]:
        """便捷方法：返回 failed_nodes 中所有 node_id 的集合。

        P0-10：hard 失败传播时，被 upstream_hard_failure 跳过的下游节点也算作"失败"，
        因为它们的上游失败了，导致这些节点无法执行。
        """
        ids = {item["node_id"] for item in self.failed_nodes}
        ids.update(
            item["node_id"] for item in self.skipped_nodes
            if item.get("reason") == "upstream_hard_failure"
        )
        return ids

    def skipped_node_ids(self) -> set[str]:
        """便捷方法：返回 skipped_nodes 中所有 node_id 的集合。"""
        return {item["node_id"] for item in self.skipped_nodes}


class EditorGenerationDAGRuntime:
    """Layered asyncio scheduler for the editor generation DAG.

    方案12：每个节点的状态/输入/输出持久化到 WorkflowExecution/WorkflowStep 表。
    方案14 Part B：引入 asyncio.Semaphore 限制 LLM 并发，避免压垮 API。
    方案16：DAG 可观测性补全——广播层级进度、记录 skip 原因与失败节点详情、
            所有节点统一 phase trace、记录节点数据血缘。
    """

    def __init__(
        self,
        dag: EditorGenerationDAG,
        project_id: str | None = None,
        chapter_number: int | None = None,
        max_concurrent: int = 5,
        progress_callback: ProgressCallback | None = None,
        execution_id: str | None = None,
    ):
        self.dag = dag
        self.project_id = project_id
        self.chapter_number = chapter_number
        self.nodes_by_id = {node.node_id: node for node in dag.nodes}
        self.layers = dag.layers()
        # R4-1：如果传入 execution_id 则复用已有记录（resume 场景），否则 run() 时创建新记录
        self._execution_id: str | None = execution_id
        # P1-31：并发分组——LLM 节点与轻量节点分别使用不同 semaphore
        self._llm_semaphore = asyncio.Semaphore(max_concurrent)
        self._light_semaphore = asyncio.Semaphore(max_concurrent * 2)
        # 向后兼容：旧代码/测试通过 runtime.semaphore 访问主信号量
        self.semaphore = self._llm_semaphore
        # 方案16 步骤1：进度广播回调
        self._progress_callback = progress_callback
        # 方案16 步骤2：预计算 output_to 血缘映射（node_id -> 下游 node_id 列表）
        self._output_to_map: dict[str, list[str]] = self._build_output_to_map()
        # 方案17：节点输出存储（node_id -> output），供下游 handler 通过 DAGContext 获取
        self.node_outputs: dict[str, object] = {}
        # 方案17：运行时共享状态（过渡期兼容闭包变量，逐步迁移到纯 context 传递）
        self.shared_state: dict[str, object] = {}
        # P1-33：持久化失败计数（_persist_node_* 写入 DB 失败时递增）
        self._persist_failure_count: int = 0
        # S1：persistence_degraded 告警去重标志（每次 run/resume 重置）
        self._persistence_degraded_alerted: bool = False

    def _get_semaphore(self, node_type: str) -> asyncio.Semaphore:
        """P1-31：按节点类型选择 semaphore——轻量节点用 _light_semaphore，其余用 _llm_semaphore。"""
        if node_type in _LIGHT_NODE_TYPES:
            return self._light_semaphore
        return self._llm_semaphore

    async def _invoke_handler_with_metric_context(
        self,
        node: EditorDagNode,
        context: DAGContext,
        handler: NodeHandler,
        timeout: float,
        layer: int,
    ) -> object:
        """Attribute downstream LLM usage to the concrete DAG node."""
        from app.services.llm_metrics import (
            pop_llm_metric_context,
            push_llm_metric_context,
        )

        token = push_llm_metric_context(
            workflow_step=node.workflow_step or node.node_id,
            dag_node=node.node_id,
            scene_index=node.scene_index,
            layer=layer,
        )
        try:
            return await asyncio.wait_for(handler(node, context), timeout=timeout)
        finally:
            pop_llm_metric_context(token)

    async def run(
        self,
        handlers: dict[str, NodeHandler],
        *,
        node_types: Iterable[str] | None = None,
    ) -> EditorDAGRunReport:
        allowed_types = set(node_types) if node_types is not None else None
        report = EditorDAGRunReport()

        # P1-32：状态隔离——每次 run() 开始时清空节点输出，避免跨运行污染。
        # 注意：不清空 shared_state——它可能包含 run 前由调用方设置的初始值（如 init_value）。
        # shared_state 的跨运行隔离由"每次创建新 runtime 实例"保证。
        self.node_outputs.clear()
        # P1-33：重置持久化失败计数
        self._persist_failure_count = 0
        self._persistence_degraded_alerted = False

        # 方案12 步骤1：创建 WorkflowExecution 记录
        # R4-1：如果 _execution_id 已设置（resume 场景），复用已有记录，不创建新记录
        if self.project_id and not self._execution_id:
            await self._create_execution()

        for layer_idx, layer in enumerate(self.layers):
            runnable = [
                self.nodes_by_id[node_id]
                for node_id in layer
                if node_id in self.nodes_by_id
                and (allowed_types is None or self.nodes_by_id[node_id].node_type in allowed_types)
            ]
            if not runnable:
                continue

            self._validate_parallel_layer(runnable)

            report.layer_widths.append(len(runnable))
            report.max_parallel_width = max(report.max_parallel_width, len(runnable))

            # 方案12：更新当前层
            if self._execution_id:
                await self._update_execution_layer(layer_idx)

            # 方案16 步骤1：广播层级开始
            layer_start_event = {
                "type": "layer_start",
                "layer_index": layer_idx,
                "layer_nodes": [n.node_id for n in runnable],
                "total_layers": len(self.layers),
                "execution_id": self._execution_id,
            }
            report.layer_events.append(layer_start_event)
            self._broadcast_progress(layer_start_event)

            async def _run_node(node: EditorDagNode) -> tuple[str, object]:
                # 方案16 步骤5：每个节点都有 phase trace
                phase_trace: list[dict] = []
                _now = lambda: time.perf_counter()
                phase_trace.append({"phase": "start", "time": _now(), "ts": datetime.now(timezone.utc).isoformat()})

                progress_status = await self._execution_progress_status()
                if progress_status in _DAG_ABORT_TERMINAL_STATUSES:
                    raise EditorDAGAbort({"status": progress_status})

                handler = handlers.get(node.node_type)
                if handler is None:
                    # 方案16 步骤3：记录 skip 原因
                    reason = f"handler_not_registered: {node.node_type}"
                    phase_trace.append({"phase": "skipped", "time": _now(), "ts": datetime.now(timezone.utc).isoformat(), "reason": reason})
                    report.skipped_nodes.append({"node_id": node.node_id, "reason": reason})
                    report.node_traces[node.node_id] = phase_trace
                    # 方案12：持久化 skipped 状态；方案16：写入 skip_reason
                    if self._execution_id:
                        await self._persist_node_skipped(node, layer_idx, reason, phase_trace)
                    return node.node_id, None

                phase_trace.append({"phase": "handler_registered", "time": _now(), "ts": datetime.now(timezone.utc).isoformat()})

                # 方案12：持久化节点开始；方案16 步骤2：写入 inputs_from 血缘
                step_started_at = datetime.now(timezone.utc) if self._execution_id else None
                if self._execution_id:
                    await self._persist_node_start(node, layer_idx)

                # 方案17：构建 DAGContext，包含上游节点输出（所有 handler 必须接收 context 参数）
                upstream_outputs = self._get_upstream_outputs(node)
                context = DAGContext(node, upstream_outputs, self)

                # 方案12 步骤5：超时控制；方案14 Part B：semaphore 限制并发
                # F7：可重试异常（网络/连接故障）自动重试一次
                timeout = NODE_TIMEOUTS.get(node.node_type, DEFAULT_NODE_TIMEOUT)
                result = None
                _handler_succeeded = False
                _last_exception: Exception | None = None
                _last_error_type = ""
                for _attempt in range(MAX_NODE_RETRIES + 1):
                    try:
                        async with self._get_semaphore(node.node_type):
                            result = await self._invoke_handler_with_metric_context(
                                node,
                                context,
                                handler,
                                timeout,
                                layer_idx,
                            )
                        _handler_succeeded = True
                        break
                    except asyncio.TimeoutError:
                        error_msg = f"timeout after {timeout}s"
                        logger.error(f"node {node.node_id} ({node.node_type}) timed out after {timeout}s")
                        phase_trace.append({"phase": "timeout", "time": _now(), "ts": datetime.now(timezone.utc).isoformat(), "error": error_msg})
                        report.failed_nodes.append({"node_id": node.node_id, "error": error_msg, "error_type": "TimeoutError"})
                        report.node_traces[node.node_id] = phase_trace
                        if self._execution_id:
                            await self._persist_node_timeout(node, layer_idx, step_started_at, phase_trace)
                        # S1：DAG 节点超时告警
                        get_alert_service().alert(
                            "warning", "dag_node_timeout",
                            f"node={node.node_id} type={node.node_type} timeout={timeout}s execution={self._execution_id}",
                        )
                        return node.node_id, None
                    except EditorDAGAbort as abort_exc:
                        abort_status = _dag_abort_status(abort_exc.result)
                        phase_trace.append({
                            "phase": "aborted",
                            "time": _now(),
                            "ts": datetime.now(timezone.utc).isoformat(),
                            "status": abort_status,
                        })
                        report.node_traces[node.node_id] = phase_trace
                        if self._execution_id:
                            await self._persist_node_abort(
                                node,
                                layer_idx,
                                step_started_at,
                                abort_status,
                                abort_exc.result,
                                phase_trace,
                            )
                        raise
                    except Exception as e:
                        _last_exception = e
                        _last_error_type = type(e).__name__
                        # F7：仅可重试异常且还有重试次数时重试
                        if _attempt < MAX_NODE_RETRIES and _is_retryable_exception(e):
                            logger.warning(
                                "node %s (%s) attempt %d failed with retryable error %s, retrying...",
                                node.node_id, node.node_type, _attempt + 1, type(e).__name__,
                            )
                            phase_trace.append({
                                "phase": "retry",
                                "time": _now(),
                                "ts": datetime.now(timezone.utc).isoformat(),
                                "attempt": _attempt + 1,
                                "error": str(e),
                                "error_type": type(e).__name__,
                            })
                            await asyncio.sleep(NODE_RETRY_DELAY_SECONDS)
                            continue
                        error_msg = str(e)
                        logger.error(f"node {node.node_id} ({node.node_type}) failed: {e}")
                        phase_trace.append({"phase": "failed", "time": _now(), "ts": datetime.now(timezone.utc).isoformat(), "error": error_msg, "error_type": type(e).__name__})
                        # 方案16 步骤4：记录失败节点详情（含 error_type）
                        report.failed_nodes.append({"node_id": node.node_id, "error": error_msg, "error_type": type(e).__name__})
                        report.node_traces[node.node_id] = phase_trace
                        if self._execution_id:
                            await self._persist_node_failed(node, layer_idx, step_started_at, error_msg, type(e).__name__, phase_trace)
                        # S1：DAG 节点失败告警
                        get_alert_service().alert(
                            "critical", "dag_node_failed",
                            f"node={node.node_id} type={node.node_type} error={type(e).__name__}: {error_msg[:200]} execution={self._execution_id}",
                        )
                        return node.node_id, None
                if not _handler_succeeded:
                    # 理论上不会走到这里（重试耗尽后会在 except 块 return），但防御性处理
                    error_msg = str(_last_exception) if _last_exception else "unknown error after retries"
                    logger.error(f"node {node.node_id} ({node.node_type}) exhausted retries: {error_msg}")
                    phase_trace.append({"phase": "failed", "time": _now(), "ts": datetime.now(timezone.utc).isoformat(), "error": error_msg, "error_type": _last_error_type})
                    report.failed_nodes.append({"node_id": node.node_id, "error": error_msg, "error_type": _last_error_type})
                    report.node_traces[node.node_id] = phase_trace
                    if self._execution_id:
                        await self._persist_node_failed(node, layer_idx, step_started_at, error_msg, _last_error_type, phase_trace)
                    # S1：DAG 节点重试耗尽告警
                    get_alert_service().alert(
                        "critical", "dag_node_retries_exhausted",
                        f"node={node.node_id} type={node.node_type} error={_last_error_type}: {error_msg[:200]} execution={self._execution_id}",
                    )
                    return node.node_id, None

                progress_status = await self._execution_progress_status()
                if progress_status in _DAG_ABORT_TERMINAL_STATUSES:
                    raise EditorDAGAbort({"status": progress_status})
                phase_trace.append({"phase": "executed", "time": _now(), "ts": datetime.now(timezone.utc).isoformat()})

                # 方案17：存储节点输出供下游 handler 通过 DAGContext 获取
                self.node_outputs[node.node_id] = result

                # 方案12：持久化节点完成；方案16 步骤5：写入 phase_trace
                if self._execution_id:
                    await self._persist_node_complete(node, layer_idx, step_started_at, result, phase_trace)

                report.node_traces[node.node_id] = phase_trace
                return node.node_id, result

            try:
                results = await asyncio.gather(*(_run_node(node) for node in runnable))
            except EditorDAGAbort as _abort_exc:
                # 方案12：持久化中断状态
                # 问题 16：尊重 handler 通过 EditorDAGAbort.result 设置的 status
                # （如 waiting_review / waiting_human_content_review），
                # 不能无脑覆盖为 aborted，否则前端审核面板无法弹出。
                _abort_status = _dag_abort_status(_abort_exc.result)
                if self._execution_id:
                    await self._update_execution_status(_abort_status)
                # 方案16 步骤1：广播层级失败
                layer_fail_event = {
                    "type": "layer_failed",
                    "layer_index": layer_idx,
                    "reason": _abort_status,
                    "execution_id": self._execution_id,
                }
                report.layer_events.append(layer_fail_event)
                self._broadcast_progress(layer_fail_event)
                raise

            # 方案16 步骤4：收集本层失败节点和跳过节点
            failed_ids = report.failed_node_ids()
            skipped_ids = report.skipped_node_ids()
            layer_has_failures = any(nid in failed_ids for nid, _ in results)

            # 方案16 步骤1：广播层级完成（或失败）
            if layer_has_failures:
                layer_failed_in_layer = [
                    item for item in report.failed_nodes
                    if item["node_id"] in {n.node_id for n in runnable}
                ]
                layer_event = {
                    "type": "layer_failed",
                    "layer_index": layer_idx,
                    "failed_nodes": layer_failed_in_layer,
                    "execution_id": self._execution_id,
                }
            else:
                layer_event = {
                    "type": "layer_complete",
                    "layer_index": layer_idx,
                    "executed": [nid for nid, _ in results if nid not in failed_ids and nid not in skipped_ids],
                    "skipped": [item for item in report.skipped_nodes if item["node_id"] in {n.node_id for n in runnable}],
                    "execution_id": self._execution_id,
                    # R4-7：持久化失败超阈值时标记降级，供 progress_callback 消费方感知
                    "persistence_degraded": self._persist_failure_count >= PERSIST_DEGRADED_THRESHOLD,
                }
            report.layer_events.append(layer_event)
            self._broadcast_progress(layer_event)

            # 方案16：executed_nodes 排除失败和跳过的节点
            for node_id, _ in results:
                if node_id not in failed_ids and node_id not in skipped_ids:
                    report.executed_nodes.append(node_id)

            # P0-10: hard 失败传播——检测 blocking_policy == "hard" 的失败节点，
            # 标记下游依赖节点为 skipped（reason=upstream_hard_failure），break 跳出层循环
            hard_failed_ids = {
                item["node_id"] for item in report.failed_nodes
                if item.get("node_id") in {n.node_id for n in runnable}
                and self.nodes_by_id.get(item.get("node_id"))
                and self.nodes_by_id[item["node_id"]].blocking_policy == "hard"
            }
            if hard_failed_ids:
                # 计算所有下游传递依赖节点（基于 _output_to_map）
                downstream_set: set[str] = set()
                queue = list(hard_failed_ids)
                while queue:
                    current = queue.pop()
                    for downstream in self._output_to_map.get(current, []):
                        if downstream not in downstream_set and downstream not in hard_failed_ids:
                            downstream_set.add(downstream)
                            queue.append(downstream)
                # 标记下游节点为 skipped（避免重复）
                existing_skipped = {s["node_id"] for s in report.skipped_nodes}
                for node_id in downstream_set:
                    if node_id not in existing_skipped:
                        report.skipped_nodes.append({
                            "node_id": node_id,
                            "reason": "upstream_hard_failure",
                        })
                        existing_skipped.add(node_id)
                # break 跳出层循环，不再执行后续层
                break

        # 方案12：持久化完成状态
        # P0-10: 有 failed_nodes 时标记为 "partial"
        if self._execution_id:
            if report.failed_nodes:
                await self._update_execution_status("partial")
            else:
                await self._update_execution_status("completed")

        report.execution_id = self._execution_id
        report.persist_failure_count = self._persist_failure_count
        return report

    # ---- 方案16：可观测性辅助方法 ----

    def _broadcast_progress(self, event: dict) -> None:
        """方案16 步骤1：广播进度事件到回调（如 editor_chat 的 _workflow_events）。"""
        if self._progress_callback is None:
            return
        try:
            self._progress_callback(event)
        except Exception as e:
            logger.warning(f"progress broadcast failed: {e}")

    def _build_output_to_map(self) -> dict[str, list[str]]:
        """方案16 步骤2：预计算每个节点的下游节点列表（基于 DAG 声明的 dependencies）。

        Returns:
            {node_id: [downstream_node_id, ...]}
        """
        output_to: dict[str, list[str]] = {node.node_id: [] for node in self.dag.nodes}
        for node in self.dag.nodes:
            for dep_id in node.dependencies:
                if dep_id in output_to:
                    output_to[dep_id].append(node.node_id)
        return output_to

    def _get_inputs_from(self, node: EditorDagNode) -> dict:
        """方案16 步骤2：获取节点的输入血缘（来自 DAG 声明的 dependencies）。"""
        return {dep_id: {"declared": True} for dep_id in node.dependencies if dep_id in self.nodes_by_id}

    def _get_output_to(self, node: EditorDagNode) -> list[str]:
        """方案16 步骤2：获取节点的输出血缘（下游节点列表）。"""
        return list(self._output_to_map.get(node.node_id, []))

    def _get_upstream_outputs(self, node: EditorDagNode) -> dict[str, object]:
        """方案17：获取上游节点的输出数据，用于构建 DAGContext。

        从 node_outputs 中提取节点声明的所有上游依赖的输出。
        """
        upstream = {}
        for dep_id in node.dependencies:
            if dep_id in self.node_outputs:
                upstream[dep_id] = self.node_outputs[dep_id]
        return upstream

    # ---- 持久化辅助方法 ----

    def _check_persistence_degraded(self) -> None:
        """S1：检查持久化是否降级，首次降级时发出告警。"""
        if (
            self._persist_failure_count >= PERSIST_DEGRADED_THRESHOLD
            and not self._persistence_degraded_alerted
        ):
            self._persistence_degraded_alerted = True
            get_alert_service().alert(
                "warning", "persistence_degraded",
                f"persist_failures={self._persist_failure_count} execution={self._execution_id}",
            )

    async def _create_execution(self) -> None:
        """创建 WorkflowExecution 记录。"""
        try:
            async with async_session() as session:
                execution = WorkflowExecution(
                    project_id=self.project_id,
                    status="running",
                    trigger_type="editor_dag",
                    input_context={"chapter_number": self.chapter_number} if self.chapter_number else {},
                    total_layers=len(self.layers),
                )
                session.add(execution)
                await session.commit()
                await session.refresh(execution)
                self._execution_id = str(execution.id)
        except Exception as e:
            logger.warning(f"failed to create workflow execution: {e}")
            self._execution_id = None

    async def _update_execution_layer(self, layer_idx: int) -> None:
        try:
            async with async_session() as session:
                await session.execute(
                    update(WorkflowExecution)
                    .where(
                        WorkflowExecution.id == self._execution_id,
                        WorkflowExecution.status == "running",
                    )
                    .values(current_layer=layer_idx)
                )
                await session.commit()
        except Exception as e:
            logger.warning(f"failed to update execution layer: {e}")

    async def _update_execution_status(self, status: str) -> None:
        try:
            async with async_session() as session:
                current_result = await session.execute(
                    select(WorkflowExecution.status).where(
                        WorkflowExecution.id == self._execution_id
                    )
                )
                current_status = current_result.scalar_one_or_none()
                if (
                    current_status in _DAG_ABORT_TERMINAL_STATUSES
                    and status != current_status
                    and status != "cancelled"
                ):
                    logger.info(
                        "keeping terminal execution status '%s'; ignoring stale transition to '%s' "
                        "(execution_id=%s)",
                        current_status,
                        status,
                        self._execution_id,
                    )
                    return
                # 附录4问题32修复：第二次 DAG run 不覆盖第一次的 partial 状态
                # 如果当前状态是 "partial"（第一次 run 有失败节点）且新状态是 "completed"，
                # 保持 "partial"——复合状态应以更严重的为准。
                if status == "completed":
                    if current_status == "partial":
                        logger.info(
                            "附录4问题32: keeping existing 'partial' status, "
                            "not overwriting with 'completed' (execution_id=%s)",
                            self._execution_id,
                        )
                        return
                await session.execute(
                    update(WorkflowExecution)
                    .where(WorkflowExecution.id == self._execution_id)
                    .values(status=status)
                )
                await session.commit()
        except Exception as e:
            logger.warning(f"failed to update execution status: {e}")

    async def _execution_progress_status(self) -> str | None:
        if not self._execution_id:
            return None
        try:
            async with async_session() as session:
                result = await session.execute(
                    select(WorkflowExecution.status).where(
                        WorkflowExecution.id == self._execution_id
                    )
                )
                return result.scalar_one_or_none()
        except Exception as exc:
            logger.warning("failed to read execution progress status: %s", exc)
            return None

    async def _persist_node_start(self, node: EditorDagNode, layer_idx: int) -> None:
        try:
            # F1/F6 修复：使用 UPSERT 模式，避免第二次 DAG run（final_acceptance_delta_*）
            # 或 resume_from 场景下创建重复 WorkflowStep 记录。
            # 先尝试 UPDATE 已有记录（由 _create_workflow_execution 创建）为 "running"，
            # 若无匹配则 INSERT 新记录。
            inputs_from = self._get_inputs_from(node)
            output_to = self._get_output_to(node)
            now = datetime.now(timezone.utc)
            async with async_session() as session:
                result = await session.execute(
                    update(WorkflowStep)
                    .where(
                        WorkflowStep.execution_id == self._execution_id,
                        WorkflowStep.agent_name == node.node_id,
                    )
                    .values(
                        status="running",
                        started_at=now,
                        layer=layer_idx,
                        inputs_from=inputs_from,
                        output_to=output_to,
                        completed_at=None,
                        duration_ms=None,
                        error_message="",
                        skip_reason=None,  # 附录4问题31修复：清除 stale skip_reason
                    )
                )
                if result.rowcount == 0:
                    step = WorkflowStep(
                        execution_id=self._execution_id,
                        agent_name=node.node_id,
                        layer=layer_idx,
                        status="running",
                        started_at=now,
                        inputs_from=inputs_from,
                        output_to=output_to,
                    )
                    session.add(step)
                await session.commit()
        except Exception as e:
            self._persist_failure_count += 1
            self._check_persistence_degraded()
            logger.warning(f"failed to persist node start {node.node_id}: {e}")

    async def _persist_node_complete(self, node: EditorDagNode, layer_idx: int, started_at, result, phase_trace: list[dict] | None = None) -> None:
        try:
            completed_at = datetime.now(timezone.utc)
            duration_ms = int((completed_at - (started_at or completed_at)).total_seconds() * 1000)
            values = {
                "status": "completed",
                "output_snapshot": to_json_safe(result) if result is not None else {},
                "completed_at": completed_at,
                "duration_ms": duration_ms,
            }
            # 方案16 步骤5：写入 phase_trace
            if phase_trace is not None:
                values["phase_trace"] = to_json_safe(phase_trace)
            async with async_session() as session:
                await session.execute(
                    update(WorkflowStep)
                    .where(
                        WorkflowStep.execution_id == self._execution_id,
                        WorkflowStep.agent_name == node.node_id,
                    )
                    .values(**values)
                )
                await session.commit()
        except Exception as e:
            self._persist_failure_count += 1
            self._check_persistence_degraded()
            logger.warning(f"failed to persist node complete {node.node_id}: {e}")

    async def _persist_node_failed(self, node: EditorDagNode, layer_idx: int, started_at, error_msg: str, error_type: str = "", phase_trace: list[dict] | None = None) -> None:
        try:
            completed_at = datetime.now(timezone.utc)
            duration_ms = int((completed_at - (started_at or completed_at)).total_seconds() * 1000)
            values = {
                "status": "failed",
                "error_message": error_msg[:2000],
                "completed_at": completed_at,
                "duration_ms": duration_ms,
            }
            # 方案16 步骤4：记录 error_type；步骤5：记录 phase_trace
            if error_type:
                values["error_message"] = f"[{error_type}] {error_msg}"[:2000]
            if phase_trace is not None:
                values["phase_trace"] = to_json_safe(phase_trace)
            async with async_session() as session:
                await session.execute(
                    update(WorkflowStep)
                    .where(
                        WorkflowStep.execution_id == self._execution_id,
                        WorkflowStep.agent_name == node.node_id,
                    )
                    .values(**values)
                )
                await session.commit()
        except Exception as e:
            self._persist_failure_count += 1
            self._check_persistence_degraded()
            logger.warning(f"failed to persist node failed {node.node_id}: {e}")

    async def _persist_node_timeout(self, node: EditorDagNode, layer_idx: int, started_at, phase_trace: list[dict] | None = None) -> None:
        await self._persist_node_failed(
            node, layer_idx, started_at,
            f"timeout after {NODE_TIMEOUTS.get(node.node_type, DEFAULT_NODE_TIMEOUT)}s",
            error_type="TimeoutError",
            phase_trace=phase_trace,
        )

    async def _persist_node_abort(
        self,
        node: EditorDagNode,
        layer_idx: int,
        started_at,
        status: str,
        result: object | None,
        phase_trace: list[dict] | None = None,
    ) -> None:
        """Persist the concrete node that raised ``EditorDAGAbort``.

        Handlers can update compatibility/alias steps themselves. The runtime
        still owns the concrete DAG node and must not leave it in ``running``
        after a clean workflow pause or terminal abort.
        """
        try:
            completed_at = datetime.now(timezone.utc)
            duration_ms = int((completed_at - (started_at or completed_at)).total_seconds() * 1000)
            error_message = ""
            if isinstance(result, dict):
                error_message = str(result.get("error") or result.get("message") or "")[:2000]
            values = {
                "layer": layer_idx,
                "status": status,
                "output_snapshot": to_json_safe(result) if result is not None else {},
                "error_message": error_message,
                "completed_at": completed_at,
                "duration_ms": duration_ms,
            }
            if phase_trace is not None:
                values["phase_trace"] = to_json_safe(phase_trace)
            async with async_session() as session:
                update_result = await session.execute(
                    update(WorkflowStep)
                    .where(
                        WorkflowStep.execution_id == self._execution_id,
                        WorkflowStep.agent_name == node.node_id,
                    )
                    .values(**values)
                )
                if update_result.rowcount == 0:
                    session.add(WorkflowStep(
                        execution_id=self._execution_id,
                        agent_name=node.node_id,
                        inputs_from=self._get_inputs_from(node),
                        output_to=self._get_output_to(node),
                        **values,
                    ))
                await session.commit()
        except Exception as e:
            self._persist_failure_count += 1
            self._check_persistence_degraded()
            logger.warning(f"failed to persist node abort {node.node_id}: {e}")

    async def _persist_node_skipped(self, node: EditorDagNode, layer_idx: int, reason: str = "", phase_trace: list[dict] | None = None) -> None:
        try:
            # F1/F6 修复：使用 UPSERT 模式，避免重复记录
            values = {
                "layer": layer_idx,
                "status": "skipped",
            }
            # 方案16 步骤3：写入 skip_reason；步骤2：写入血缘；步骤5：写入 phase_trace
            if reason:
                values["skip_reason"] = reason[:2000]
            values["inputs_from"] = self._get_inputs_from(node)
            values["output_to"] = self._get_output_to(node)
            if phase_trace is not None:
                values["phase_trace"] = to_json_safe(phase_trace)
            async with async_session() as session:
                result = await session.execute(
                    update(WorkflowStep)
                    .where(
                        WorkflowStep.execution_id == self._execution_id,
                        WorkflowStep.agent_name == node.node_id,
                    )
                    .values(**values)
                )
                if result.rowcount == 0:
                    step = WorkflowStep(
                        execution_id=self._execution_id,
                        agent_name=node.node_id,
                        **values,
                    )
                    session.add(step)
                await session.commit()
        except Exception as e:
            self._persist_failure_count += 1
            self._check_persistence_degraded()
            logger.warning(f"failed to persist node skipped {node.node_id}: {e}")

    @staticmethod
    async def recover_interrupted(
        *,
        project_id: str | None = None,
        stale_threshold_seconds: int | None = None,
    ) -> int:
        """恢复中断的工作流（方案12）。

        将 status=running 的执行标记为 interrupted。
        附录4问题29修复：覆盖所有 trigger_type，与 PersistentDAG.recover_interrupted 策略一致，
        避免因 trigger_type 过滤导致非 editor_ 前缀的卡死执行无法被清理。
        真正的断点续跑需方案13 DAG对齐后实现。

        P0-2 修复（循环 #5）：新增过滤参数，避免新工作流启动时误伤其他正常运行的工作流。
        - project_id: 限定项目。None 表示全系统（冷启动场景）。
        - stale_threshold_seconds: 心跳超时秒数。只清理 updated_at 早于 (now - threshold) 的工作流。
          None 表示不按心跳过滤（冷启动场景：所有 running 都是真崩溃）。

        使用场景：
        - 应用启动（main.py）：不传参数，全系统清理（冷启动所有 running 都是真崩溃）。
        - 新工作流启动前（editor_chat.py）：传入 project_id 和 stale_threshold_seconds=300，
          只清理同项目且心跳超时（崩溃）的工作流，不影响其他项目或其他章节的正常工作流。
        """
        try:
            async with async_session() as session:
                query = select(WorkflowExecution).where(
                    WorkflowExecution.status == "running",
                )
                if project_id is not None:
                    query = query.where(WorkflowExecution.project_id == project_id)
                if stale_threshold_seconds is not None:
                    cutoff = datetime.now(timezone.utc) - timedelta(seconds=stale_threshold_seconds)
                    query = query.where(WorkflowExecution.updated_at < cutoff)
                result = await session.execute(query)
                executions = result.scalars().all()
                recovered = 0
                recovered_ids: list[str] = []
                for execution in executions:
                    execution.status = "interrupted"
                    session.add(execution)
                    recovered += 1
                    recovered_ids.append(str(execution.id))
                # 附录4问题6修复：清理卡死的 running 步骤
                if recovered_ids:
                    result_steps = await session.execute(
                        update(WorkflowStep)
                        .where(WorkflowStep.execution_id.in_(recovered_ids))
                        .where(WorkflowStep.status == "running")
                        .values(status="failed", error_message="interrupted by system recovery", completed_at=datetime.now(timezone.utc))
                    )
                    logger.info("recover_interrupted: cleaned up %d stuck running steps", result_steps.rowcount)
                # execution 与 step 必须在同一个事务中持久化，避免出现
                # execution=interrupted 但 step 仍为 running 的半恢复状态。
                await session.commit()
            if recovered:
                logger.info(
                    "recovered %d interrupted editor executions (project_id=%s stale_threshold=%ss)",
                    recovered, project_id, stale_threshold_seconds,
                )
            return recovered
        except Exception as e:
            logger.error(f"failed to recover interrupted executions: {e}")
            return 0

    async def load_execution_state(self, execution_id: str) -> dict | None:
        """方案12：从DB加载历史执行状态。

        返回 {"execution": ..., "steps": [...], "completed_node_ids": set[str], "failed_node_ids": set[str]}。
        用于resume_from判断哪些节点已 完成/失败，避免重复执行。
        """
        try:
            async with async_session() as session:
                exec_result = await session.execute(
                    select(WorkflowExecution).where(WorkflowExecution.id == execution_id)
                )
                execution = exec_result.scalar_one_or_none()
                if execution is None:
                    return None
                steps_result = await session.execute(
                    select(WorkflowStep).where(WorkflowStep.execution_id == execution_id)
                )
                steps = steps_result.scalars().all()
                completed_node_ids: set[str] = set()
                failed_node_ids: set[str] = set()
                for step in steps:
                    if step.status == "completed":
                        completed_node_ids.add(step.agent_name)
                        # 加载已完成节点的output_snapshot到node_outputs，供下游handler通过DAGContext获取
                        if step.output_snapshot is not None:
                            self.node_outputs[step.agent_name] = step.output_snapshot
                    elif step.status == "failed":
                        failed_node_ids.add(step.agent_name)
                self._execution_id = execution_id
                return {
                    "execution": execution,
                    "steps": steps,
                    "completed_node_ids": completed_node_ids,
                    "failed_node_ids": failed_node_ids,
                }
        except Exception as e:
            logger.warning(f"failed to load execution state {execution_id}: {e}")
            return None

    async def resume_from(
        self,
        execution_id: str,
        handlers: dict[str, NodeHandler],
        *,
        node_types: Iterable[str] | None = None,
        skip_completed: bool = True,
    ) -> EditorDAGRunReport:
        """方案12：从指定execution_id恢复执行。

        - 加载DB中的WorkflowStep状态
        - 跳过已完成的节点（skip_completed=True时）
        - 重新执行未完成或失败的节点
        - 已完成节点的output_snapshot加载到node_outputs，供下游handler通过DAGContext获取
        """
        state = await self.load_execution_state(execution_id)
        if state is None:
            report = EditorDAGRunReport()
            report.failed_nodes.append({
                "node_id": "_load_state",
                "error": f"execution {execution_id} not found or load failed",
                "error_type": "StateNotFoundError",
            })
            return report

        raw_execution_status = state["execution"].status
        execution_status = (
            str(raw_execution_status or "")
            if isinstance(raw_execution_status, str)
            else "running"
        )
        if execution_status != "running":
            report = EditorDAGRunReport()
            report.skipped_nodes.append({
                "node_id": "_resume_guard",
                "reason": f"execution_not_claimed_for_resume:{execution_status}",
            })
            return report

        completed_node_ids = state["completed_node_ids"] if skip_completed else set()
        failed_node_ids = state["failed_node_ids"]

        # P1-F4：标记为 running，让监控能区分"恢复中"和"已中断"
        # P2-W6：重置持久化失败计数器，避免跨 run 累积
        if self._execution_id:
            await self._update_execution_status("running")
        self._persist_failure_count = 0
        self._persistence_degraded_alerted = False

        # 不重新创建execution，复用已有的execution_id
        # 不调用_create_execution，因为_execution_id已在load_execution_state中设置

        allowed_types = set(node_types) if node_types is not None else None
        report = EditorDAGRunReport()

        for layer_idx, layer in enumerate(self.layers):
            runnable = [
                self.nodes_by_id[node_id]
                for node_id in layer
                if node_id in self.nodes_by_id
                and (allowed_types is None or self.nodes_by_id[node_id].node_type in allowed_types)
            ]
            if not runnable:
                continue

            # 过滤掉已完成的节点（resume时跳过）
            if skip_completed:
                pending = [n for n in runnable if n.node_id not in completed_node_ids]
                if not pending:
                    # 本层全部已完成，跳过
                    continue
                runnable = pending

            self._validate_parallel_layer(runnable)

            report.layer_widths.append(len(runnable))
            report.max_parallel_width = max(report.max_parallel_width, len(runnable))

            if self._execution_id:
                await self._update_execution_layer(layer_idx)

            layer_start_event = {
                "type": "layer_start",
                "layer_index": layer_idx,
                "layer_nodes": [n.node_id for n in runnable],
                "total_layers": len(self.layers),
                "execution_id": self._execution_id,
                "resume": True,
            }
            report.layer_events.append(layer_start_event)
            self._broadcast_progress(layer_start_event)

            async def _run_node(node: EditorDagNode) -> tuple[str, object]:
                phase_trace: list[dict] = []
                _now = lambda: time.perf_counter()
                phase_trace.append({"phase": "start", "time": _now(), "ts": datetime.now(timezone.utc).isoformat(), "resume": True})

                progress_status = await self._execution_progress_status()
                if progress_status in _DAG_ABORT_TERMINAL_STATUSES:
                    raise EditorDAGAbort({"status": progress_status})

                handler = handlers.get(node.node_type)
                if handler is None:
                    reason = f"handler_not_registered: {node.node_type}"
                    phase_trace.append({"phase": "skipped", "time": _now(), "ts": datetime.now(timezone.utc).isoformat(), "reason": reason})
                    report.skipped_nodes.append({"node_id": node.node_id, "reason": reason})
                    report.node_traces[node.node_id] = phase_trace
                    if self._execution_id:
                        await self._persist_node_skipped(node, layer_idx, reason, phase_trace)
                    return node.node_id, None

                phase_trace.append({"phase": "handler_registered", "time": _now(), "ts": datetime.now(timezone.utc).isoformat()})

                step_started_at = datetime.now(timezone.utc) if self._execution_id else None
                if self._execution_id:
                    await self._persist_node_start(node, layer_idx)

                upstream_outputs = self._get_upstream_outputs(node)
                context = DAGContext(node, upstream_outputs, self)

                timeout = NODE_TIMEOUTS.get(node.node_type, DEFAULT_NODE_TIMEOUT)
                # F7：可重试异常自动重试（与 run() 保持一致）
                result = None
                _handler_succeeded = False
                _last_exception: Exception | None = None
                _last_error_type = ""
                for _attempt in range(MAX_NODE_RETRIES + 1):
                    try:
                        async with self._get_semaphore(node.node_type):
                            result = await self._invoke_handler_with_metric_context(
                                node,
                                context,
                                handler,
                                timeout,
                                layer_idx,
                            )
                        _handler_succeeded = True
                        break
                    except asyncio.TimeoutError:
                        error_msg = f"timeout after {timeout}s"
                        logger.error(f"node {node.node_id} ({node.node_type}) timed out after {timeout}s (resume)")
                        phase_trace.append({"phase": "timeout", "time": _now(), "ts": datetime.now(timezone.utc).isoformat(), "error": error_msg})
                        report.failed_nodes.append({"node_id": node.node_id, "error": error_msg, "error_type": "TimeoutError"})
                        report.node_traces[node.node_id] = phase_trace
                        if self._execution_id:
                            await self._persist_node_timeout(node, layer_idx, step_started_at, phase_trace)
                        # S1：DAG 节点超时告警（resume）
                        get_alert_service().alert(
                            "warning", "dag_node_timeout",
                            f"node={node.node_id} type={node.node_type} timeout={timeout}s execution={self._execution_id} resume=True",
                        )
                        return node.node_id, None
                    except EditorDAGAbort as abort_exc:
                        abort_status = _dag_abort_status(abort_exc.result)
                        phase_trace.append({
                            "phase": "aborted",
                            "time": _now(),
                            "ts": datetime.now(timezone.utc).isoformat(),
                            "status": abort_status,
                            "resume": True,
                        })
                        report.node_traces[node.node_id] = phase_trace
                        if self._execution_id:
                            await self._persist_node_abort(
                                node,
                                layer_idx,
                                step_started_at,
                                abort_status,
                                abort_exc.result,
                                phase_trace,
                            )
                        raise
                    except Exception as e:
                        _last_exception = e
                        _last_error_type = type(e).__name__
                        if _attempt < MAX_NODE_RETRIES and _is_retryable_exception(e):
                            logger.warning(
                                "node %s (%s) resume attempt %d failed with retryable error %s, retrying...",
                                node.node_id, node.node_type, _attempt + 1, type(e).__name__,
                            )
                            phase_trace.append({
                                "phase": "retry",
                                "time": _now(),
                                "ts": datetime.now(timezone.utc).isoformat(),
                                "attempt": _attempt + 1,
                                "error": str(e),
                                "error_type": type(e).__name__,
                            })
                            await asyncio.sleep(NODE_RETRY_DELAY_SECONDS)
                            continue
                        error_msg = str(e)
                        logger.error(f"node {node.node_id} ({node.node_type}) failed during resume: {e}")
                        phase_trace.append({"phase": "failed", "time": _now(), "ts": datetime.now(timezone.utc).isoformat(), "error": error_msg, "error_type": type(e).__name__})
                        report.failed_nodes.append({"node_id": node.node_id, "error": error_msg, "error_type": type(e).__name__})
                        report.node_traces[node.node_id] = phase_trace
                        if self._execution_id:
                            await self._persist_node_failed(node, layer_idx, step_started_at, error_msg, type(e).__name__, phase_trace)
                        # S1：DAG 节点失败告警（resume）
                        get_alert_service().alert(
                            "critical", "dag_node_failed",
                            f"node={node.node_id} type={node.node_type} error={type(e).__name__}: {error_msg[:200]} execution={self._execution_id} resume=True",
                        )
                        return node.node_id, None
                if not _handler_succeeded:
                    error_msg = str(_last_exception) if _last_exception else "unknown error after retries"
                    logger.error(f"node {node.node_id} ({node.node_type}) exhausted retries during resume: {error_msg}")
                    phase_trace.append({"phase": "failed", "time": _now(), "ts": datetime.now(timezone.utc).isoformat(), "error": error_msg, "error_type": _last_error_type})
                    report.failed_nodes.append({"node_id": node.node_id, "error": error_msg, "error_type": _last_error_type})
                    report.node_traces[node.node_id] = phase_trace
                    if self._execution_id:
                        await self._persist_node_failed(node, layer_idx, step_started_at, error_msg, _last_error_type, phase_trace)
                    # S1：DAG 节点重试耗尽告警（resume）
                    get_alert_service().alert(
                        "critical", "dag_node_retries_exhausted",
                        f"node={node.node_id} type={node.node_type} error={_last_error_type}: {error_msg[:200]} execution={self._execution_id} resume=True",
                    )
                    return node.node_id, None

                progress_status = await self._execution_progress_status()
                if progress_status in _DAG_ABORT_TERMINAL_STATUSES:
                    raise EditorDAGAbort({"status": progress_status})
                phase_trace.append({"phase": "executed", "time": _now(), "ts": datetime.now(timezone.utc).isoformat(), "resume": True})
                self.node_outputs[node.node_id] = result
                if self._execution_id:
                    await self._persist_node_complete(node, layer_idx, step_started_at, result, phase_trace)
                report.node_traces[node.node_id] = phase_trace
                return node.node_id, result

            try:
                results = await asyncio.gather(*(_run_node(node) for node in runnable))
            except EditorDAGAbort as _abort_exc:
                # 问题 16：尊重 handler 通过 EditorDAGAbort.result 设置的 status
                _abort_status = _dag_abort_status(_abort_exc.result)
                if self._execution_id:
                    await self._update_execution_status(_abort_status)
                layer_fail_event = {
                    "type": "layer_failed",
                    "layer_index": layer_idx,
                    "reason": _abort_status,
                    "execution_id": self._execution_id,
                    "resume": True,
                }
                report.layer_events.append(layer_fail_event)
                self._broadcast_progress(layer_fail_event)
                raise

            failed_ids = report.failed_node_ids()
            skipped_ids = report.skipped_node_ids()

            if any(nid in failed_ids for nid, _ in results):
                layer_failed_in_layer = [
                    item for item in report.failed_nodes
                    if item["node_id"] in {n.node_id for n in runnable}
                ]
                layer_event = {
                    "type": "layer_failed",
                    "layer_index": layer_idx,
                    "failed_nodes": layer_failed_in_layer,
                    "execution_id": self._execution_id,
                    "resume": True,
                }
            else:
                layer_event = {
                    "type": "layer_complete",
                    "layer_index": layer_idx,
                    "executed": [nid for nid, _ in results if nid not in failed_ids and nid not in skipped_ids],
                    "skipped": [item for item in report.skipped_nodes if item["node_id"] in {n.node_id for n in runnable}],
                    "execution_id": self._execution_id,
                    "resume": True,
                    # R4-7：持久化失败超阈值时标记降级，供 progress_callback 消费方感知
                    "persistence_degraded": self._persist_failure_count >= PERSIST_DEGRADED_THRESHOLD,
                }
            report.layer_events.append(layer_event)
            self._broadcast_progress(layer_event)

            for node_id, _ in results:
                if node_id not in failed_ids and node_id not in skipped_ids:
                    report.executed_nodes.append(node_id)

            # P0-F1：hard 失败传播——与 run() 保持一致
            hard_failed_ids = {
                item["node_id"] for item in report.failed_nodes
                if item.get("node_id") in {n.node_id for n in runnable}
                and self.nodes_by_id.get(item.get("node_id"))
                and self.nodes_by_id[item["node_id"]].blocking_policy == "hard"
            }
            if hard_failed_ids:
                downstream_set: set[str] = set()
                queue = list(hard_failed_ids)
                while queue:
                    current = queue.pop()
                    for downstream in self._output_to_map.get(current, []):
                        if downstream not in downstream_set and downstream not in hard_failed_ids:
                            downstream_set.add(downstream)
                            queue.append(downstream)
                existing_skipped = {s["node_id"] for s in report.skipped_nodes}
                for node_id in downstream_set:
                    if node_id not in existing_skipped:
                        report.skipped_nodes.append({
                            "node_id": node_id,
                            "reason": "upstream_hard_failure",
                        })
                        existing_skipped.add(node_id)
                break

        # P0-F2：有失败节点时标记 partial，与 run() 保持一致
        if self._execution_id:
            if report.failed_nodes:
                await self._update_execution_status("partial")
            else:
                await self._update_execution_status("completed")

        report.execution_id = self._execution_id
        report.persist_failure_count = self._persist_failure_count
        return report

    @staticmethod
    def _validate_parallel_layer(nodes: list[EditorDagNode]) -> None:
        readonly_types = {
            "parallel_scene_review",
            "parallel_scene_recheck",
            "final_acceptance_delta_intake",
            "final_acceptance_delta_blueprint",
            "final_acceptance_delta_recheck",
        }
        for node in nodes:
            if node.node_type in readonly_types and node.can_mutate_text:
                raise EditorDAGSafetyError(
                    f"{node.node_id} ({node.node_type}) is read-only but can_mutate_text=True"
                )

        writers = [node for node in nodes if node.can_mutate_text]
        for index, left in enumerate(writers):
            left_scope = set(left.write_scope)
            if not left_scope:
                continue
            for right in writers[index + 1:]:
                overlap = left_scope & set(right.write_scope)
                if overlap:
                    raise EditorDAGSafetyError(
                        "parallel write_scope conflict: "
                        f"{left.node_id} and {right.node_id} both write {sorted(overlap)}"
                    )

        # 方案14 Part D：read-write 竞态检查
        # 同层中 reader 的 read_scope 不能与 writer 的 write_scope 重叠，
        # 否则 reader 可能读到 writer 写到一半的中间状态。
        readers = [node for node in nodes if not node.can_mutate_text]
        for reader in readers:
            read_scope = set(reader.read_scope or [])
            if not read_scope:
                continue
            for writer in writers:
                write_scope = set(writer.write_scope or [])
                overlap = read_scope & write_scope
                if overlap:
                    raise EditorDAGSafetyError(
                        f"read-write conflict: {reader.node_id} reads {sorted(overlap)} "
                        f"while {writer.node_id} writes them"
                    )
