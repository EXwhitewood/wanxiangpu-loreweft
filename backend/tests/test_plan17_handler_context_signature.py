"""方案17：handler (node, context) 签名统一测试。"""
import asyncio
import inspect
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.editor_generation_dag import EditorDagNode, EditorGenerationDAG
from app.services.editor_generation_dag_runtime import (
    EditorGenerationDAGRuntime,
    DAGContext,
    EditorDAGAbort,
)


def _make_dag_one_node():
    """构建单节点DAG。"""
    node = EditorDagNode(
        node_id="node_a",
        node_type="test_a",
        display_name="Test A",
        dependencies=[],
    )
    return EditorGenerationDAG(
        nodes=[node],
        dag_version="test",
        mode="parallel_review",
        scene_count=1,
    )


def _make_dag_two_nodes():
    """构建两节点DAG（有依赖关系）。"""
    node_a = EditorDagNode(
        node_id="node_a",
        node_type="test_a",
        display_name="Test A",
        dependencies=[],
    )
    node_b = EditorDagNode(
        node_id="node_b",
        node_type="test_b",
        display_name="Test B",
        dependencies=["node_a"],
    )
    return EditorGenerationDAG(
        nodes=[node_a, node_b],
        dag_version="test",
        mode="parallel_review",
        scene_count=1,
    )


# ---- 测试1：handler接收context参数被正确调用 ----

@pytest.mark.asyncio
async def test_handler_receives_context_parameter():
    """run()应将context参数传递给handler。"""
    dag = _make_dag_one_node()
    runtime = EditorGenerationDAGRuntime(dag=dag, project_id=None)  # project_id=None避免DB操作

    received_context = []

    async def handler(node, context):
        received_context.append(context)
        return {"result": "ok"}

    report = await runtime.run({"test_a": handler})
    assert len(received_context) == 1
    assert isinstance(received_context[0], DAGContext)
    assert "node_a" in report.executed_nodes


# ---- 测试2：handler通过context获取上游输出 ----

@pytest.mark.asyncio
async def test_handler_gets_upstream_output_via_context():
    """下游handler应能通过context.get_upstream_output获取上游输出。"""
    dag = _make_dag_two_nodes()
    runtime = EditorGenerationDAGRuntime(dag=dag, project_id=None)

    upstream_output_received = []

    async def handler_a(node, context):
        return {"data": "from_a"}

    async def handler_b(node, context):
        upstream = context.get_upstream_output("node_a")
        upstream_output_received.append(upstream)
        return {"data": "from_b"}

    report = await runtime.run({"test_a": handler_a, "test_b": handler_b})
    assert len(upstream_output_received) == 1
    assert upstream_output_received[0] == {"data": "from_a"}


# ---- 测试3：context包含正确的node引用 ----

@pytest.mark.asyncio
async def test_context_contains_correct_node():
    """context.node应指向当前执行的节点。"""
    dag = _make_dag_one_node()
    runtime = EditorGenerationDAGRuntime(dag=dag, project_id=None)

    received_node_ids = []

    async def handler(node, context):
        received_node_ids.append(context.node.node_id)
        return {"result": "ok"}

    await runtime.run({"test_a": handler})
    assert received_node_ids == ["node_a"]


# ---- 测试4：context的get_all_upstream_outputs返回所有上游 ----

@pytest.mark.asyncio
async def test_context_get_all_upstream_outputs():
    """context.get_all_upstream_outputs应返回所有上游输出。"""
    dag = _make_dag_two_nodes()
    runtime = EditorGenerationDAGRuntime(dag=dag, project_id=None)

    all_upstream_received = []

    async def handler_a(node, context):
        return {"data": "from_a"}

    async def handler_b(node, context):
        all_upstream_received.append(context.get_all_upstream_outputs())
        return {"data": "from_b"}

    await runtime.run({"test_a": handler_a, "test_b": handler_b})
    assert len(all_upstream_received) == 1
    assert "node_a" in all_upstream_received[0]


# ---- 测试5：context的get_shared_state和set_shared_state ----

@pytest.mark.asyncio
async def test_context_shared_state():
    """context应支持共享状态的读写。"""
    dag = _make_dag_two_nodes()
    runtime = EditorGenerationDAGRuntime(dag=dag, project_id=None)

    shared_value_received = []

    async def handler_a(node, context):
        context.set_shared_state("key_a", "value_a")
        return {"data": "from_a"}

    async def handler_b(node, context):
        shared_value_received.append(context.get_shared_state("key_a"))
        return {"data": "from_b"}

    await runtime.run({"test_a": handler_a, "test_b": handler_b})
    assert shared_value_received == ["value_a"]


# ---- 测试6：handler异常时context仍正确传递 ----

@pytest.mark.asyncio
async def test_context_passed_even_when_handler_fails():
    """handler抛异常时context应已正确传递（异常前的context交互有效）。"""
    dag = _make_dag_one_node()
    runtime = EditorGenerationDAGRuntime(dag=dag, project_id=None)

    context_received_before_failure = []

    async def handler(node, context):
        context_received_before_failure.append(context)
        raise ValueError("test failure")

    report = await runtime.run({"test_a": handler})
    assert len(context_received_before_failure) == 1
    assert isinstance(context_received_before_failure[0], DAGContext)
    assert len(report.failed_nodes) == 1
    assert report.failed_nodes[0]["node_id"] == "node_a"


# ---- 测试7：resume_from也传递context ----

@pytest.mark.asyncio
async def test_resume_from_passes_context():
    """resume_from应将context参数传递给handler。"""
    dag = _make_dag_one_node()
    runtime = EditorGenerationDAGRuntime(dag=dag, project_id="test_proj", chapter_number=1)

    received_context = []

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
                with patch.object(runtime, "_persist_node_complete", new=AsyncMock()):
                    with patch.object(runtime, "_update_execution_status", new=AsyncMock()):

                        async def handler(node, context):
                            received_context.append(context)
                            return {"result": "ok"}

                        report = await runtime.resume_from("exec-123", {"test_a": handler})
                        assert len(received_context) == 1
                        assert isinstance(received_context[0], DAGContext)


# ---- 测试8：所有editor_chat.py中的handler都接收context参数 ----

def test_all_editor_chat_handlers_accept_context():
    """验证editor_chat.py中所有handle_*函数都接收(node, context)签名。

    通过解析源文件，检查每个handle_*函数的参数列表包含context。
    """
    import re
    import os

    editor_chat_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "app", "api", "editor_chat.py"
    )
    if not os.path.exists(editor_chat_path):
        pytest.skip(f"editor_chat.py not found at {editor_chat_path}")

    with open(editor_chat_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 匹配 async def handle_xxx(node, context) 或 async def handle_xxx(node, context) -> None:
    # 也匹配缩进的函数定义
    pattern = r"async\s+def\s+(handle_\w+)\s*\(([^)]*)\)"
    matches = re.findall(pattern, content)

    handle_funcs = [(name, params) for name, params in matches if name.startswith("handle_")]
    assert len(handle_funcs) >= 13, f"Expected at least 13 handle_* functions, found {len(handle_funcs)}"

    for name, params in handle_funcs:
        param_list = [p.strip() for p in params.split(",")]
        assert "context" in param_list, (
            f"handler '{name}' does not accept context parameter. "
            f"Params: {param_list}"
        )


# ---- 测试9：run()不再使用inspect检测handler签名 ----

def test_run_does_not_use_inspect_for_signature_detection():
    """验证run()方法不再使用inspect.signature检测handler签名。

    方案17移除了双签名宽容设计，所有handler必须接收context参数。
    """
    import os

    runtime_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "app", "services", "editor_generation_dag_runtime.py"
    )
    if not os.path.exists(runtime_path):
        pytest.skip(f"editor_generation_dag_runtime.py not found at {runtime_path}")

    with open(runtime_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 不应存在accepts_context相关的检测逻辑
    assert "accepts_context" not in content, (
        "editor_generation_dag_runtime.py still contains 'accepts_context' logic. "
        "方案17要求移除双签名宽容设计。"
    )


# ---- 测试10：handler接收context时node_outputs正确填充 ----

@pytest.mark.asyncio
async def test_node_outputs_populated_after_handler_execution():
    """handler执行后，其输出应存储到node_outputs供下游handler通过context获取。"""
    dag = _make_dag_two_nodes()
    runtime = EditorGenerationDAGRuntime(dag=dag, project_id=None)

    async def handler_a(node, context):
        return {"output": "a_result"}

    async def handler_b(node, context):
        # 通过context获取上游输出（应来自node_outputs）
        upstream = context.get_upstream_output("node_a")
        return {"upstream": upstream}

    await runtime.run({"test_a": handler_a, "test_b": handler_b})
    # 验证node_outputs被正确填充
    assert "node_a" in runtime.node_outputs
    assert runtime.node_outputs["node_a"] == {"output": "a_result"}
    assert "node_b" in runtime.node_outputs
