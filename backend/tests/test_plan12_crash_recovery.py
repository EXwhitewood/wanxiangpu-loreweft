"""方案12：崩溃恢复接入入口测试。"""
import asyncio
import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.editor_generation_dag import EditorDagNode, EditorGenerationDAG
from app.services.editor_generation_dag_runtime import (
    EditorGenerationDAGRuntime,
    EditorDAGAbort,
    NODE_TIMEOUTS,
    DEFAULT_NODE_TIMEOUT,
)


def _make_dag_two_nodes() -> EditorGenerationDAG:
    """构造 A → B 两节点 DAG。"""
    nodes = [
        EditorDagNode("node_a", "Node A", "test_a", dependencies=[]),
        EditorDagNode("node_b", "Node B", "test_b", dependencies=["node_a"]),
    ]
    return EditorGenerationDAG(
        dag_version="test_v12",
        mode="test",
        scene_count=1,
        nodes=nodes,
    )


def _make_dag_one_node() -> EditorGenerationDAG:
    """构造单节点 DAG。"""
    nodes = [EditorDagNode("node_a", "Node A", "test_a", dependencies=[])]
    return EditorGenerationDAG(
        dag_version="test_v12",
        mode="test",
        scene_count=1,
        nodes=nodes,
    )


# ---- 测试1：recover_interrupted将running标记为interrupted ----

@pytest.mark.asyncio
async def test_recover_interrupted_marks_running_as_interrupted(tmp_path):
    """recover_interrupted应将status=running的editor_dag执行标记为interrupted。"""
    # 此测试需要DB fixture，使用mock或in-memory DB
    # 简化：mock async_session
    with patch("app.services.editor_generation_dag_runtime.async_session") as mock_session_cls:
        mock_session = AsyncMock()
        mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=None)
        mock_session.add = MagicMock()

        # mock execution对象
        mock_execution = MagicMock()
        mock_execution.status = "running"
        mock_execution.trigger_type = "editor_dag"
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [mock_execution]
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        mock_session.execute = AsyncMock(return_value=mock_result)

        recovered = await EditorGenerationDAGRuntime.recover_interrupted()
        assert recovered == 1
        assert mock_execution.status == "interrupted"
        mock_session.commit.assert_awaited_once()
        assert mock_session.execute.await_count == 2


# ---- 测试2：recover_interrupted无running时返回0 ----

@pytest.mark.asyncio
async def test_recover_interrupted_returns_zero_when_no_running(tmp_path):
    with patch("app.services.editor_generation_dag_runtime.async_session") as mock_session_cls:
        mock_session = AsyncMock()
        mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        mock_session.execute = AsyncMock(return_value=mock_result)

        recovered = await EditorGenerationDAGRuntime.recover_interrupted()
        assert recovered == 0


# ---- 测试3：load_execution_state加载已完成节点的output_snapshot ----

@pytest.mark.asyncio
async def test_load_execution_state_loads_completed_outputs():
    """load_execution_state应将已完成节点的output_snapshot加载到node_outputs。"""
    dag = _make_dag_two_nodes()
    runtime = EditorGenerationDAGRuntime(dag=dag, project_id="test_proj", chapter_number=1)

    with patch("app.services.editor_generation_dag_runtime.async_session") as mock_session_cls:
        mock_session = AsyncMock()
        mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        # mock execution
        mock_execution = MagicMock()
        mock_execution.id = "exec-123"
        mock_exec_scalars = MagicMock()
        mock_exec_scalars.scalar_one_or_none.return_value = mock_execution
        mock_exec_result = MagicMock()
        mock_exec_result.scalars.return_value = mock_exec_scalars

        # mock steps
        mock_step_a = MagicMock()
        mock_step_a.status = "completed"
        mock_step_a.agent_name = "node_a"
        mock_step_a.output_snapshot = {"result": "output_a"}
        mock_step_b = MagicMock()
        mock_step_b.status = "failed"
        mock_step_b.agent_name = "node_b"
        mock_step_b.output_snapshot = None
        mock_steps_scalars = MagicMock()
        mock_steps_scalars.all.return_value = [mock_step_a, mock_step_b]
        mock_steps_result = MagicMock()
        mock_steps_result.scalars.return_value = mock_steps_scalars

        # execute第一次返回execution结果，第二次返回steps结果
        mock_session.execute = AsyncMock(side_effect=[mock_exec_result, mock_steps_result])

        state = await runtime.load_execution_state("exec-123")
        assert state is not None
        assert state["completed_node_ids"] == {"node_a"}
        assert state["failed_node_ids"] == {"node_b"}
        assert runtime.node_outputs["node_a"] == {"result": "output_a"}
        assert runtime._execution_id == "exec-123"


# ---- 测试4：load_execution_state在execution不存在时返回None ----

@pytest.mark.asyncio
async def test_load_execution_state_returns_none_when_not_found():
    dag = _make_dag_one_node()
    runtime = EditorGenerationDAGRuntime(dag=dag, project_id="test_proj", chapter_number=1)

    with patch("app.services.editor_generation_dag_runtime.async_session") as mock_session_cls:
        mock_session = AsyncMock()
        mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        # 代码直接调用 exec_result.scalar_one_or_none()，而非 .scalars().scalar_one_or_none()
        mock_exec_result = MagicMock()
        mock_exec_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_exec_result)

        state = await runtime.load_execution_state("nonexistent")
        assert state is None


# ---- 测试5：resume_from跳过已完成节点 ----

@pytest.mark.asyncio
async def test_resume_from_skips_completed_nodes():
    """resume_from应跳过已完成的节点，只执行未完成节点。"""
    dag = _make_dag_two_nodes()
    runtime = EditorGenerationDAGRuntime(dag=dag, project_id="test_proj", chapter_number=1)

    # mock load_execution_state返回node_a已完成
    with patch.object(runtime, "load_execution_state", new=AsyncMock()) as mock_load:
        mock_load.return_value = {
            "execution": MagicMock(),
            "steps": [],
            "completed_node_ids": {"node_a"},
            "failed_node_ids": set(),
        }
        # 预填node_a的output（模拟load_execution_state的副作用）
        runtime.node_outputs["node_a"] = {"result": "output_a"}
        runtime._execution_id = "exec-123"

        # mock持久化方法，避免DB操作
        with patch.object(runtime, "_update_execution_layer", new=AsyncMock()):
            with patch.object(runtime, "_persist_node_start", new=AsyncMock()):
                with patch.object(runtime, "_persist_node_complete", new=AsyncMock()):
                    with patch.object(runtime, "_update_execution_status", new=AsyncMock()):
                        # handler_b应被调用，handler_a不应被调用
                        handler_a_called = []
                        handler_b_called = []

                        async def handler_a(node, context):
                            handler_a_called.append(node.node_id)
                            return {"result": "a"}

                        async def handler_b(node, context):
                            handler_b_called.append(node.node_id)
                            return {"result": "b"}

                        handlers = {"test_a": handler_a, "test_b": handler_b}

                        report = await runtime.resume_from("exec-123", handlers)
                        assert not handler_a_called  # node_a已完成，不应执行
                        assert len(handler_b_called) == 1  # node_b未完成，应执行
                        assert "node_b" in report.executed_nodes
                        assert "node_a" not in report.executed_nodes


# ---- 测试6：resume_from在execution不存在时返回失败报告 ----

@pytest.mark.asyncio
async def test_resume_from_returns_failure_when_state_not_found():
    dag = _make_dag_one_node()
    runtime = EditorGenerationDAGRuntime(dag=dag, project_id="test_proj", chapter_number=1)

    with patch.object(runtime, "load_execution_state", new=AsyncMock(return_value=None)):
        report = await runtime.resume_from("nonexistent", {})
        assert len(report.failed_nodes) == 1
        assert report.failed_nodes[0]["node_id"] == "_load_state"
        assert "not found" in report.failed_nodes[0]["error"]


@pytest.mark.asyncio
async def test_resume_from_refuses_cancelled_execution_without_running_handlers():
    dag = _make_dag_one_node()
    runtime = EditorGenerationDAGRuntime(dag=dag, project_id="test_proj", chapter_number=1)
    execution = MagicMock()
    execution.status = "cancelled"
    handler_calls = []

    async def handler(node, context):
        handler_calls.append(node.node_id)
        return {"result": "should_not_run"}

    with patch.object(runtime, "load_execution_state", new=AsyncMock(return_value={
        "execution": execution,
        "steps": [],
        "completed_node_ids": set(),
        "failed_node_ids": set(),
    })):
        report = await runtime.resume_from("exec-cancelled", {"test_a": handler})

    assert handler_calls == []
    assert report.skipped_nodes == [{
        "node_id": "_resume_guard",
        "reason": "execution_not_claimed_for_resume:cancelled",
    }]


# ---- 测试7：resume_from执行所有节点当skip_completed=False ----

@pytest.mark.asyncio
async def test_resume_from_executes_all_when_skip_completed_false():
    """skip_completed=False时，即使节点已完成也应重新执行。"""
    dag = _make_dag_one_node()
    runtime = EditorGenerationDAGRuntime(dag=dag, project_id="test_proj", chapter_number=1)

    with patch.object(runtime, "load_execution_state", new=AsyncMock()) as mock_load:
        mock_load.return_value = {
            "execution": MagicMock(),
            "steps": [],
            "completed_node_ids": {"node_a"},
            "failed_node_ids": set(),
        }
        runtime._execution_id = "exec-123"

        with patch.object(runtime, "_update_execution_layer", new=AsyncMock()):
            with patch.object(runtime, "_persist_node_start", new=AsyncMock()):
                with patch.object(runtime, "_persist_node_complete", new=AsyncMock()):
                    with patch.object(runtime, "_update_execution_status", new=AsyncMock()):
                        handler_a_called = []

                        async def handler_a(node, context):
                            handler_a_called.append(node.node_id)
                            return {"result": "a_reexecuted"}

                        handlers = {"test_a": handler_a}
                        report = await runtime.resume_from("exec-123", handlers, skip_completed=False)
                        assert len(handler_a_called) == 1
                        assert "node_a" in report.executed_nodes


# ---- 测试8：resume_from处理handler未注册 ----

@pytest.mark.asyncio
async def test_resume_from_handles_missing_handler():
    """resume_from在handler未注册时应记录skipped而非崩溃。"""
    dag = _make_dag_one_node()
    runtime = EditorGenerationDAGRuntime(dag=dag, project_id="test_proj", chapter_number=1)

    with patch.object(runtime, "load_execution_state", new=AsyncMock()) as mock_load:
        mock_load.return_value = {
            "execution": MagicMock(),
            "steps": [],
            "completed_node_ids": set(),
            "failed_node_ids": set(),
        }
        runtime._execution_id = "exec-123"

        with patch.object(runtime, "_update_execution_layer", new=AsyncMock()):
            with patch.object(runtime, "_persist_node_skipped", new=AsyncMock()):
                report = await runtime.resume_from("exec-123", {})  # 空handlers
                assert len(report.skipped_nodes) == 1
                assert report.skipped_nodes[0]["node_id"] == "node_a"
                assert "handler_not_registered" in report.skipped_nodes[0]["reason"]


# ---- 测试9：resume_from处理handler异常 ----

@pytest.mark.asyncio
async def test_resume_from_handles_handler_exception():
    """resume_from在handler抛异常时应记录failed而非崩溃。"""
    dag = _make_dag_one_node()
    runtime = EditorGenerationDAGRuntime(dag=dag, project_id="test_proj", chapter_number=1)

    with patch.object(runtime, "load_execution_state", new=AsyncMock()) as mock_load:
        mock_load.return_value = {
            "execution": MagicMock(),
            "steps": [],
            "completed_node_ids": set(),
            "failed_node_ids": set(),
        }
        runtime._execution_id = "exec-123"

        with patch.object(runtime, "_update_execution_layer", new=AsyncMock()):
            with patch.object(runtime, "_persist_node_start", new=AsyncMock()):
                with patch.object(runtime, "_persist_node_failed", new=AsyncMock()):

                    async def failing_handler(node, context):
                        raise ValueError("test error")

                    handlers = {"test_a": failing_handler}
                    report = await runtime.resume_from("exec-123", handlers)
                    assert len(report.failed_nodes) == 1
                    assert report.failed_nodes[0]["node_id"] == "node_a"
                    assert "test error" in report.failed_nodes[0]["error"]


# ---- 测试10：resume_from支持handler接收context参数 ----

@pytest.mark.asyncio
async def test_resume_from_supports_context_handler():
    """resume_from应支持(node, context)签名的handler。"""
    dag = _make_dag_two_nodes()
    runtime = EditorGenerationDAGRuntime(dag=dag, project_id="test_proj", chapter_number=1)

    with patch.object(runtime, "load_execution_state", new=AsyncMock()) as mock_load:
        mock_load.return_value = {
            "execution": MagicMock(),
            "steps": [],
            "completed_node_ids": {"node_a"},
            "failed_node_ids": set(),
        }
        runtime.node_outputs["node_a"] = {"result": "output_a"}
        runtime._execution_id = "exec-123"

        with patch.object(runtime, "_update_execution_layer", new=AsyncMock()):
            with patch.object(runtime, "_persist_node_start", new=AsyncMock()):
                with patch.object(runtime, "_persist_node_complete", new=AsyncMock()):
                    with patch.object(runtime, "_update_execution_status", new=AsyncMock()):
                        received_context_output = []

                        async def handler_b(node, context):
                            upstream = context.get_upstream_output("node_a")
                            received_context_output.append(upstream)
                            return {"result": "b"}

                        handlers = {"test_b": handler_b}
                        report = await runtime.resume_from("exec-123", handlers)
                        assert len(received_context_output) == 1
                        assert received_context_output[0] == {"result": "output_a"}
