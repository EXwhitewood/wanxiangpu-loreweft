"""方案16：DAG 可观测性补全测试套件。

覆盖文档 §16 定义的 5 个验证场景：
1. 进度广播测试：layer_start / layer_complete / layer_failed 事件
2. 数据血缘测试：inputs_from / output_to 基于 DAG 声明依赖
3. skip 原因测试：未注册 handler 的节点 skip 原因被记录
4. 失败节点测试：并行层失败节点 ID 和 error / error_type 被记录
5. phase trace 测试：所有节点（含 skipped / failed）都有 phase trace
"""
from __future__ import annotations

import asyncio

from app.services.editor_generation_dag import EditorDagNode, EditorGenerationDAG
from app.services.editor_generation_dag_runtime import (
    EditorGenerationDAGRuntime,
)


def _make_dag(
    nodes: list[EditorDagNode] | None = None,
) -> EditorGenerationDAG:
    """构造测试用 DAG。"""
    if nodes is None:
        nodes = [
            EditorDagNode(
                "node_a", "Node A", "scene_prepare",
                dependencies=[],
            ),
            EditorDagNode(
                "node_b", "Node B", "scene_generate",
                dependencies=["node_a"],
            ),
            EditorDagNode(
                "node_c", "Node C", "scene_accept",
                dependencies=["node_b"],
            ),
        ]
    return EditorGenerationDAG(
        dag_version="test_v16",
        mode="test",
        scene_count=1,
        nodes=nodes,
    )


# ---------------------------------------------------------------------------
# 步骤3：skip 原因记录
# ---------------------------------------------------------------------------


def test_skip_reason_recorded_when_handler_not_registered():
    """未注册 handler 的节点 → skipped_nodes 包含 dict 含 node_id + reason。"""

    async def run_case():
        dag = _make_dag()
        runtime = EditorGenerationDAGRuntime(dag)

        # 只注册 node_a 的 handler，node_b / node_c 无 handler
        async def handler(node, context):
            return {"ok": True}

        report = await runtime.run({"scene_prepare": handler})

        assert len(report.skipped_nodes) == 2
        for item in report.skipped_nodes:
            assert isinstance(item, dict)
            assert "node_id" in item
            assert "reason" in item
            assert "handler_not_registered" in item["reason"]
        skipped_ids = {item["node_id"] for item in report.skipped_nodes}
        assert skipped_ids == {"node_b", "node_c"}

    asyncio.run(run_case())


def test_skipped_node_ids_helper():
    """report.skipped_node_ids() 返回所有被跳过节点的 ID 集合。"""

    async def run_case():
        dag = _make_dag()
        runtime = EditorGenerationDAGRuntime(dag)

        report = await runtime.run({})  # 无 handler，全部 skip

        assert report.skipped_node_ids() == {"node_a", "node_b", "node_c"}

    asyncio.run(run_case())


# ---------------------------------------------------------------------------
# 步骤4：并行层失败节点详情
# ---------------------------------------------------------------------------


def test_failed_node_records_error_and_error_type():
    """handler 抛异常 → failed_nodes 包含 dict 含 node_id + error + error_type。"""

    async def run_case():
        dag = _make_dag()
        runtime = EditorGenerationDAGRuntime(dag)

        async def good_handler(node, context):
            return None

        async def bad_handler(node, context):
            raise RuntimeError("simulated failure")

        report = await runtime.run({
            "scene_prepare": good_handler,
            "scene_generate": bad_handler,  # node_b 会失败
            "scene_accept": good_handler,
        })

        assert len(report.failed_nodes) == 1
        failure = report.failed_nodes[0]
        assert isinstance(failure, dict)
        assert failure["node_id"] == "node_b"
        assert "simulated failure" in failure["error"]
        assert failure["error_type"] == "RuntimeError"

    asyncio.run(run_case())


def test_failed_node_ids_helper():
    """report.failed_node_ids() 返回所有失败节点的 ID 集合。"""

    async def run_case():
        dag = _make_dag()
        runtime = EditorGenerationDAGRuntime(dag)

        async def bad_handler(node, context):
            raise ValueError("fail")

        report = await runtime.run({
            "scene_prepare": bad_handler,
            "scene_generate": bad_handler,
            "scene_accept": bad_handler,
        })

        assert report.failed_node_ids() == {"node_a", "node_b", "node_c"}

    asyncio.run(run_case())


def test_executed_nodes_excludes_failed():
    """report.executed_nodes 不包含失败节点 ID。"""

    async def run_case():
        dag = _make_dag()
        runtime = EditorGenerationDAGRuntime(dag)

        async def good_handler(node, context):
            return None

        async def bad_handler(node, context):
            raise RuntimeError("fail")

        report = await runtime.run({
            "scene_prepare": good_handler,
            "scene_generate": bad_handler,
            "scene_accept": good_handler,
        })

        assert "node_a" in report.executed_nodes
        assert "node_b" not in report.executed_nodes
        # node_c 依赖 node_b，但当前实现不检查依赖满足性，
        # node_c 有 handler 所以会执行（可能读到不完整数据，但不在本方案范围内）

    asyncio.run(run_case())


# ---------------------------------------------------------------------------
# 步骤1：层级进度广播
# ---------------------------------------------------------------------------


def test_layer_events_in_report():
    """report.layer_events 包含 layer_start 和 layer_complete 事件。"""

    async def run_case():
        dag = _make_dag()
        runtime = EditorGenerationDAGRuntime(dag)

        async def handler(node, context):
            return None

        report = await runtime.run({
            "scene_prepare": handler,
            "scene_generate": handler,
            "scene_accept": handler,
        })

        # 3 层，每层有 layer_start + layer_complete = 6 个事件
        assert len(report.layer_events) == 6
        types = [e["type"] for e in report.layer_events]
        assert "layer_start" in types
        assert "layer_complete" in types
        # layer_start 应该在 layer_complete 之前
        first_start_idx = types.index("layer_start")
        first_complete_idx = types.index("layer_complete")
        assert first_start_idx < first_complete_idx

    asyncio.run(run_case())


def test_layer_failed_event_on_node_failure():
    """节点失败时，该层广播 layer_failed 事件。"""

    async def run_case():
        dag = _make_dag()
        runtime = EditorGenerationDAGRuntime(dag)

        async def good_handler(node, context):
            return None

        async def bad_handler(node, context):
            raise RuntimeError("fail")

        report = await runtime.run({
            "scene_prepare": good_handler,
            "scene_generate": bad_handler,
            "scene_accept": good_handler,
        })

        failed_events = [e for e in report.layer_events if e["type"] == "layer_failed"]
        assert len(failed_events) >= 1
        # layer_failed 事件应包含 failed_nodes 详情
        for event in failed_events:
            assert "failed_nodes" in event
            for fn in event["failed_nodes"]:
                assert "node_id" in fn
                assert "error" in fn

    asyncio.run(run_case())


def test_progress_callback_invoked():
    """progress_callback 接收到所有层级事件。"""

    async def run_case():
        dag = _make_dag()
        received_events: list[dict] = []

        def callback(event: dict) -> None:
            received_events.append(event)

        runtime = EditorGenerationDAGRuntime(dag, progress_callback=callback)

        async def handler(node, context):
            return None

        report = await runtime.run({
            "scene_prepare": handler,
            "scene_generate": handler,
            "scene_accept": handler,
        })

        # callback 收到的事件数应与 report.layer_events 一致
        assert len(received_events) == len(report.layer_events)
        received_types = [e["type"] for e in received_events]
        assert "layer_start" in received_types
        assert "layer_complete" in received_types

    asyncio.run(run_case())


def test_progress_callback_not_required():
    """无 progress_callback 时正常运行，不报错。"""

    async def run_case():
        dag = _make_dag()
        runtime = EditorGenerationDAGRuntime(dag)  # 无 callback

        async def handler(node, context):
            return None

        report = await runtime.run({
            "scene_prepare": handler,
            "scene_generate": handler,
            "scene_accept": handler,
        })

        assert len(report.layer_events) == 6  # 仍然记录在 report 中

    asyncio.run(run_case())


# ---------------------------------------------------------------------------
# 步骤5：所有节点统一 phase trace
# ---------------------------------------------------------------------------


def test_phase_trace_for_executed_node():
    """成功执行的节点有完整的 phase trace（start → handler_registered → executed）。"""

    async def run_case():
        dag = _make_dag()
        runtime = EditorGenerationDAGRuntime(dag)

        async def handler(node, context):
            return None

        report = await runtime.run({
            "scene_prepare": handler,
            "scene_generate": handler,
            "scene_accept": handler,
        })

        for node_id in ["node_a", "node_b", "node_c"]:
            assert node_id in report.node_traces
            trace = report.node_traces[node_id]
            phases = [p["phase"] for p in trace]
            assert "start" in phases
            assert "handler_registered" in phases
            assert "executed" in phases

    asyncio.run(run_case())


def test_phase_trace_for_skipped_node():
    """被跳过的节点有 phase trace（start → skipped）。"""

    async def run_case():
        dag = _make_dag()
        runtime = EditorGenerationDAGRuntime(dag)

        async def handler(node, context):
            return None

        report = await runtime.run({"scene_prepare": handler})  # node_b/c 无 handler

        for node_id in ["node_b", "node_c"]:
            assert node_id in report.node_traces
            trace = report.node_traces[node_id]
            phases = [p["phase"] for p in trace]
            assert "start" in phases
            assert "skipped" in phases
            # skipped phase 应包含 reason
            skipped_phase = next(p for p in trace if p["phase"] == "skipped")
            assert "reason" in skipped_phase

    asyncio.run(run_case())


def test_phase_trace_for_failed_node():
    """失败的节点有 phase trace（start → handler_registered → failed）。"""

    async def run_case():
        dag = _make_dag()
        runtime = EditorGenerationDAGRuntime(dag)

        async def good_handler(node, context):
            return None

        async def bad_handler(node, context):
            raise ValueError("boom")

        report = await runtime.run({
            "scene_prepare": good_handler,
            "scene_generate": bad_handler,
            "scene_accept": good_handler,
        })

        assert "node_b" in report.node_traces
        trace = report.node_traces["node_b"]
        phases = [p["phase"] for p in trace]
        assert "start" in phases
        assert "handler_registered" in phases
        assert "failed" in phases
        failed_phase = next(p for p in trace if p["phase"] == "failed")
        assert "error" in failed_phase
        assert "error_type" in failed_phase
        assert failed_phase["error_type"] == "ValueError"

    asyncio.run(run_case())


# ---------------------------------------------------------------------------
# 步骤2：数据血缘
# ---------------------------------------------------------------------------


def test_output_to_map_built_correctly():
    """_build_output_to_map 正确计算每个节点的下游节点列表。"""

    async def run_case():
        dag = _make_dag()
        runtime = EditorGenerationDAGRuntime(dag)

        # node_a → node_b → node_c
        assert runtime._output_to_map["node_a"] == ["node_b"]
        assert runtime._output_to_map["node_b"] == ["node_c"]
        assert runtime._output_to_map["node_c"] == []

    asyncio.run(run_case())


def test_get_inputs_from_returns_declared_dependencies():
    """_get_inputs_from 返回节点声明的上游依赖。"""

    async def run_case():
        dag = _make_dag()
        runtime = EditorGenerationDAGRuntime(dag)

        inputs_b = runtime._get_inputs_from(dag.nodes[1])  # node_b
        assert "node_a" in inputs_b
        assert inputs_b["node_a"]["declared"] is True

        inputs_a = runtime._get_inputs_from(dag.nodes[0])  # node_a
        assert inputs_a == {}  # 无上游

    asyncio.run(run_case())


def test_get_output_to_returns_downstream_nodes():
    """_get_output_to 返回节点的下游节点列表。"""

    async def run_case():
        dag = _make_dag()
        runtime = EditorGenerationDAGRuntime(dag)

        assert runtime._get_output_to(dag.nodes[0]) == ["node_b"]  # node_a → node_b
        assert runtime._get_output_to(dag.nodes[1]) == ["node_c"]  # node_b → node_c
        assert runtime._get_output_to(dag.nodes[2]) == []           # node_c 无下游

    asyncio.run(run_case())


def test_lineage_with_parallel_nodes():
    """并行节点（同层无依赖）的血缘正确。"""

    async def run_case():
        # 构造 DAG: root → [fanout_a, fanout_b] → sink
        nodes = [
            EditorDagNode("root", "Root", "scene_prepare", dependencies=[]),
            EditorDagNode("fanout_a", "Fanout A", "scene_quality_fanout", dependencies=["root"]),
            EditorDagNode("fanout_b", "Fanout B", "scene_post_extract_fanout", dependencies=["root"]),
            EditorDagNode("sink", "Sink", "scene_accept", dependencies=["fanout_a", "fanout_b"]),
        ]
        dag = EditorGenerationDAG(dag_version="test", mode="test", scene_count=1, nodes=nodes)
        runtime = EditorGenerationDAGRuntime(dag)

        # root 的下游应包含 fanout_a 和 fanout_b
        root_output = set(runtime._get_output_to(nodes[0]))
        assert root_output == {"fanout_a", "fanout_b"}

        # sink 的上游应包含 fanout_a 和 fanout_b
        sink_inputs = runtime._get_inputs_from(nodes[3])
        assert set(sink_inputs.keys()) == {"fanout_a", "fanout_b"}

        # fanout_a 和 fanout_b 的下游都是 sink
        assert runtime._get_output_to(nodes[1]) == ["sink"]
        assert runtime._get_output_to(nodes[2]) == ["sink"]

    asyncio.run(run_case())


# ---------------------------------------------------------------------------
# 综合测试：并行层多节点场景
# ---------------------------------------------------------------------------


def test_parallel_layer_with_mixed_results():
    """并行层中一个成功、一个失败、一个跳过 → 三种结果都被正确记录。"""

    async def run_case():
        # 构造同层 3 节点（无相互依赖）
        nodes = [
            EditorDagNode("ok_node", "OK", "scene_quality_fanout", dependencies=[]),
            EditorDagNode("fail_node", "Fail", "scene_post_extract_fanout", dependencies=[]),
            EditorDagNode("skip_node", "Skip", "consistency_check", dependencies=[]),
        ]
        dag = EditorGenerationDAG(dag_version="test", mode="test", scene_count=1, nodes=nodes)
        runtime = EditorGenerationDAGRuntime(dag)

        async def ok_handler(node, context):
            return {"done": True}

        async def fail_handler(node, context):
            raise RuntimeError("parallel failure")

        report = await runtime.run({
            "scene_quality_fanout": ok_handler,
            "scene_post_extract_fanout": fail_handler,
            # consistency_check 无 handler → skip
        })

        # 验证 executed
        assert "ok_node" in report.executed_nodes
        assert "fail_node" not in report.executed_nodes
        assert "skip_node" not in report.executed_nodes

        # 验证 failed
        assert len(report.failed_nodes) == 1
        assert report.failed_nodes[0]["node_id"] == "fail_node"
        assert report.failed_nodes[0]["error_type"] == "RuntimeError"

        # 验证 skipped
        assert len(report.skipped_nodes) == 1
        assert report.skipped_nodes[0]["node_id"] == "skip_node"
        assert "handler_not_registered" in report.skipped_nodes[0]["reason"]

        # 验证 layer 事件
        layer_events = report.layer_events
        # 应有 1 个 layer_start + 1 个 layer_failed（因为有失败节点）
        starts = [e for e in layer_events if e["type"] == "layer_start"]
        fails = [e for e in layer_events if e["type"] == "layer_failed"]
        assert len(starts) == 1
        assert len(fails) == 1
        # layer_failed 应包含失败节点详情
        assert len(fails[0]["failed_nodes"]) == 1
        assert fails[0]["failed_nodes"][0]["node_id"] == "fail_node"

        # 验证 phase trace
        assert "ok_node" in report.node_traces
        assert "fail_node" in report.node_traces
        assert "skip_node" in report.node_traces
        ok_phases = [p["phase"] for p in report.node_traces["ok_node"]]
        assert "executed" in ok_phases
        fail_phases = [p["phase"] for p in report.node_traces["fail_node"]]
        assert "failed" in fail_phases
        skip_phases = [p["phase"] for p in report.node_traces["skip_node"]]
        assert "skipped" in skip_phases

    asyncio.run(run_case())
