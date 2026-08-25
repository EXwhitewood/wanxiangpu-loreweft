"""方案16：前端DAG事件SSE消费+类型扩展测试。"""
import os
import pytest


# ---- 测试1：前端 LayerTrace 类型包含 DAG 可观测性字段 ----

def test_frontend_layer_trace_has_dag_fields():
    """验证 frontend/src/types/editorV2.ts 的 LayerTrace 包含 node_traces/phase_trace/skip_reason。"""
    ts_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        "frontend", "src", "types", "editorV2.ts"
    )
    if not os.path.exists(ts_path):
        pytest.skip(f"editorV2.ts not found at {ts_path}")

    with open(ts_path, "r", encoding="utf-8") as f:
        content = f.read()

    assert "node_traces" in content, (
        "LayerTrace 缺少 node_traces 字段（方案16要求）"
    )
    assert "phase_trace" in content, (
        "LayerTrace 缺少 phase_trace 字段（方案16要求）"
    )
    assert "skip_reason" in content, (
        "LayerTrace 缺少 skip_reason 字段（方案16要求）"
    )
    assert "PhaseTraceEntry" in content, (
        "缺少 PhaseTraceEntry 接口定义（方案16要求）"
    )


# ---- 测试2：前端 WorkflowSSEEvent 类型包含 DAG 事件字段 ----

def test_frontend_workflow_sse_event_has_dag_fields():
    """验证 frontend/src/api/client.ts 的 WorkflowSSEEvent 包含 DAG 事件字段。"""
    client_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        "frontend", "src", "api", "client.ts"
    )
    if not os.path.exists(client_path):
        pytest.skip(f"client.ts not found at {client_path}")

    with open(client_path, "r", encoding="utf-8") as f:
        content = f.read()

    required_fields = [
        "layer_index",
        "total_layers",
        "layer_nodes",
        "executed",
        "skipped",
        "failed_nodes",
        "dag_execution_id",
    ]
    for field in required_fields:
        assert field in content, (
            f"WorkflowSSEEvent 缺少 {field} 字段（方案16要求）"
        )


# ---- 测试3：前端 WorkflowMonitor 消费 layer_* 事件 ----

def test_frontend_workflow_monitor_consumes_layer_events():
    """验证 WorkflowMonitor.tsx 消费 layer_start/layer_complete/layer_failed 事件。"""
    monitor_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        "frontend", "src", "components", "Editor", "WorkflowMonitor.tsx"
    )
    if not os.path.exists(monitor_path):
        pytest.skip(f"WorkflowMonitor.tsx not found at {monitor_path}")

    with open(monitor_path, "r", encoding="utf-8") as f:
        content = f.read()

    assert '"layer_start"' in content, (
        "WorkflowMonitor.tsx 未消费 layer_start 事件（方案16要求）"
    )
    assert '"layer_complete"' in content, (
        "WorkflowMonitor.tsx 未消费 layer_complete 事件（方案16要求）"
    )
    assert '"layer_failed"' in content, (
        "WorkflowMonitor.tsx 未消费 layer_failed 事件（方案16要求）"
    )
    assert "setDagLayerProgress" in content, (
        "WorkflowMonitor.tsx 未定义 dagLayerProgress 状态（方案16要求）"
    )


# ---- 测试4：后端 LayerTrace 模型包含 DAG 可观测性字段 ----

def test_backend_layer_trace_model_has_dag_fields():
    """验证 backend/app/models/editor_trace.py 的 LayerTrace 包含 DAG 字段。"""
    from app.models.editor_trace import LayerTrace

    layer = LayerTrace(
        layer="editor_planning",
        status="ok",
        duration_ms=100,
        detail="test",
        error="",
        node_traces={"node_a": [{"phase": "start"}]},
        phase_trace=[{"phase": "executed"}],
        skip_reason="handler_not_registered",
    )
    assert layer.node_traces == {"node_a": [{"phase": "start"}]}
    assert layer.phase_trace == [{"phase": "executed"}]
    assert layer.skip_reason == "handler_not_registered"


# ---- 测试5：后端 LayerTrace 模型 DAG 字段有默认值 ----

def test_backend_layer_trace_dag_fields_have_defaults():
    """验证 LayerTrace 的 DAG 字段有默认值（不传也不报错）。"""
    from app.models.editor_trace import LayerTrace

    layer = LayerTrace(
        layer="editor_planning",
        status="ok",
    )
    assert layer.node_traces == {}
    assert layer.phase_trace == []
    assert layer.skip_reason == ""


# ---- 测试6：前端 DagFailedNode/DagSkippedNode 类型定义存在 ----

def test_frontend_dag_node_types_exist():
    """验证 client.ts 中定义了 DagFailedNode 和 DagSkippedNode 类型。"""
    client_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        "frontend", "src", "api", "client.ts"
    )
    if not os.path.exists(client_path):
        pytest.skip(f"client.ts not found at {client_path}")

    with open(client_path, "r", encoding="utf-8") as f:
        content = f.read()

    assert "DagFailedNode" in content, (
        "client.ts 缺少 DagFailedNode 类型定义（方案16要求）"
    )
    assert "DagSkippedNode" in content, (
        "client.ts 缺少 DagSkippedNode 类型定义（方案16要求）"
    )
