"""方案17：数据传递改为 DAG 上下文测试套件。

覆盖文档 §17 定义的关键验证场景：
1. 上下文测试：下游 handler 能通过 context 获取上游输出
2. 向后兼容测试：不接收 context 的 handler 仍能正常工作
3. node_outputs 存储测试：handler 返回值被存储到 runtime.node_outputs
4. 共享状态测试：过渡期 shared_state get/set 可用
"""
from __future__ import annotations

import asyncio

from app.services.editor_generation_dag import EditorDagNode, EditorGenerationDAG
from app.services.editor_generation_dag_runtime import (
    DAGContext,
    EditorGenerationDAGRuntime,
)


def _make_linear_dag() -> EditorGenerationDAG:
    """构造 A → B → C 线性 DAG。"""
    nodes = [
        EditorDagNode("node_a", "Node A", "step_one", dependencies=[]),
        EditorDagNode("node_b", "Node B", "step_two", dependencies=["node_a"]),
        EditorDagNode("node_c", "Node C", "step_three", dependencies=["node_b"]),
    ]
    return EditorGenerationDAG(dag_version="test_v17", mode="test", scene_count=1, nodes=nodes)


# ---------------------------------------------------------------------------
# 向后兼容性测试
# ---------------------------------------------------------------------------


def test_handler_without_context_still_works():
    """不接收 context 参数的 handler 仍能正常工作（向后兼容）。"""

    async def run_case():
        dag = _make_linear_dag()
        runtime = EditorGenerationDAGRuntime(dag)

        # handler 只接收 node 参数（旧风格）
        async def legacy_handler(node, context):
            return {"node_id": node.node_id, "data": "legacy"}

        report = await runtime.run({
            "step_one": legacy_handler,
            "step_two": legacy_handler,
            "step_three": legacy_handler,
        })

        assert len(report.failed_nodes) == 0
        assert len(report.executed_nodes) == 3

    asyncio.run(run_case())


def test_handler_with_context_receives_dag_context():
    """接收 context 参数的 handler 会收到 DAGContext 实例。"""

    async def run_case():
        dag = _make_linear_dag()
        runtime = EditorGenerationDAGRuntime(dag)
        received_contexts: dict[str, DAGContext] = {}

        async def handler_with_context(node, context: DAGContext):
            received_contexts[node.node_id] = context
            return {"node_id": node.node_id, "value": 42}

        report = await runtime.run({
            "step_one": handler_with_context,
            "step_two": handler_with_context,
            "step_three": handler_with_context,
        })

        assert len(report.failed_nodes) == 0
        # 所有 handler 都收到了 context
        assert len(received_contexts) == 3
        for ctx in received_contexts.values():
            assert isinstance(ctx, DAGContext)

    asyncio.run(run_case())


# ---------------------------------------------------------------------------
# 上下文数据传递测试
# ---------------------------------------------------------------------------


def test_downstream_handler_gets_upstream_output():
    """下游 handler 能通过 context.get_upstream_output() 获取上游输出。"""

    async def run_case():
        dag = _make_linear_dag()
        runtime = EditorGenerationDAGRuntime(dag)
        results: dict[str, dict] = {}

        async def producer(node, context: DAGContext):
            return {"text": f"output_from_{node.node_id}", "count": len(node.node_id)}

        async def consumer(node, context: DAGContext):
            upstream = context.get_all_upstream_outputs()
            results[node.node_id] = {
                "upstream_keys": list(upstream.keys()),
                "upstream_data": upstream,
            }
            return {"consumed": True}

        report = await runtime.run({
            "step_one": producer,
            "step_two": consumer,
            "step_three": consumer,
        })

        assert len(report.failed_nodes) == 0

        # node_b 的上游是 node_a
        assert "node_b" in results
        assert "node_a" in results["node_b"]["upstream_keys"]
        upstream_a = results["node_b"]["upstream_data"]["node_a"]
        assert upstream_a["text"] == "output_from_node_a"
        assert upstream_a["count"] == len("node_a")

        # node_c 的上游是 node_b
        assert "node_c" in results
        assert "node_b" in results["node_c"]["upstream_keys"]
        upstream_b = results["node_c"]["upstream_data"]["node_b"]
        assert upstream_b["consumed"] is True

    asyncio.run(run_case())


def test_get_upstream_output_returns_none_for_missing_dependency():
    """上游节点未执行或无输出时，get_upstream_output 返回 None。"""

    async def run_case():
        nodes = [
            EditorDagNode("root", "Root", "step_one", dependencies=[]),
            EditorDagNode("child", "Child", "step_two", dependencies=["root"]),
        ]
        dag = EditorGenerationDAG(dag_version="test", mode="test", scene_count=1, nodes=nodes)
        runtime = EditorGenerationDAGRuntime(dag)
        received_none = False

        async def root_handler(node, context):
            return None  # 返回 None

        async def child_handler(node, context: DAGContext):
            nonlocal received_none
            output = context.get_upstream_output("root")
            if output is None:
                received_none = True
            return {}

        await runtime.run({
            "step_one": root_handler,
            "step_two": child_handler,
        })

        assert received_none is True

    asyncio.run(run_case())


def test_get_upstream_output_wraps_non_dict_result():
    """上游返回非 dict 时，get_upstream_output 包装为 {"result": ...}。"""

    async def run_case():
        nodes = [
            EditorDagNode("root", "Root", "step_one", dependencies=[]),
            EditorDagNode("child", "Child", "step_two", dependencies=["root"]),
        ]
        dag = EditorGenerationDAG(dag_version="test", mode="test", scene_count=1, nodes=nodes)
        runtime = EditorGenerationDAGRuntime(dag)
        wrapped_result = None

        async def root_handler(node, context):
            return "raw_string_output"  # 非 dict

        async def child_handler(node, context: DAGContext):
            nonlocal wrapped_result
            output = context.get_upstream_output("root")
            wrapped_result = output
            return {}

        await runtime.run({
            "step_one": root_handler,
            "step_two": child_handler,
        })

        assert wrapped_result == {"result": "raw_string_output"}

    asyncio.run(run_case())


# ---------------------------------------------------------------------------
# node_outputs 存储测试
# ---------------------------------------------------------------------------


def test_node_outputs_stored_after_execution():
    """handler 返回值被存储到 runtime.node_outputs。"""

    async def run_case():
        dag = _make_linear_dag()
        runtime = EditorGenerationDAGRuntime(dag)

        async def handler(node, context):
            return {"id": node.node_id, "payload": "test_data"}

        await runtime.run({
            "step_one": handler,
            "step_two": handler,
            "step_three": handler,
        })

        assert "node_a" in runtime.node_outputs
        assert runtime.node_outputs["node_a"]["id"] == "node_a"
        assert runtime.node_outputs["node_a"]["payload"] == "test_data"
        assert "node_b" in runtime.node_outputs
        assert "node_c" in runtime.node_outputs

    asyncio.run(run_case())


def test_node_outputs_not_stored_for_skipped_nodes():
    """被跳过的节点不写入 node_outputs。"""

    async def run_case():
        dag = _make_linear_dag()
        runtime = EditorGenerationDAGRuntime(dag)

        async def handler(node, context):
            return {"ok": True}

        # 只注册 step_one，step_two/step_three 被 skip
        await runtime.run({"step_one": handler})

        assert "node_a" in runtime.node_outputs
        assert "node_b" not in runtime.node_outputs
        assert "node_c" not in runtime.node_outputs

    asyncio.run(run_case())


def test_node_outputs_not_stored_for_failed_nodes():
    """失败的节点不写入 node_outputs。"""

    async def run_case():
        dag = _make_linear_dag()
        runtime = EditorGenerationDAGRuntime(dag)

        async def good_handler(node, context):
            return {"ok": True}

        async def bad_handler(node, context):
            raise RuntimeError("fail")

        await runtime.run({
            "step_one": good_handler,
            "step_two": bad_handler,  # 失败
            "step_three": good_handler,
        })

        assert "node_a" in runtime.node_outputs
        assert "node_b" not in runtime.node_outputs  # 失败，不存储

    asyncio.run(run_case())


# ---------------------------------------------------------------------------
# 共享状态测试（过渡期兼容）
# ---------------------------------------------------------------------------


def test_shared_state_get_set():
    """DAGContext.get_shared_state / set_shared_state 可读写运行时共享状态。"""

    async def run_case():
        dag = _make_linear_dag()
        runtime = EditorGenerationDAGRuntime(dag)
        runtime.shared_state["init_value"] = "hello"

        read_value = None

        async def handler(node, context: DAGContext):
            nonlocal read_value
            read_value = context.get_shared_state("init_value")
            context.set_shared_state("new_key", "world")
            return {}

        await runtime.run({
            "step_one": handler,
            "step_two": handler,
            "step_three": handler,
        })

        assert read_value == "hello"
        assert runtime.shared_state["new_key"] == "world"

    asyncio.run(run_case())


def test_get_shared_state_returns_default_for_missing_key():
    """get_shared_state 对不存在的 key 返回 default。"""

    async def run_case():
        dag = _make_linear_dag()
        runtime = EditorGenerationDAGRuntime(dag)
        default_received = None

        async def handler(node, context: DAGContext):
            nonlocal default_received
            default_received = context.get_shared_state("nonexistent", "fallback")
            return {}

        await runtime.run({"step_one": handler, "step_two": handler, "step_three": handler})

        assert default_received == "fallback"

    asyncio.run(run_case())


# ---------------------------------------------------------------------------
# 混合 handler 测试
# ---------------------------------------------------------------------------


def test_mixed_handlers_with_and_without_context():
    """同一 DAG 中混合使用旧式和新式 handler。"""

    async def run_case():
        dag = _make_linear_dag()
        runtime = EditorGenerationDAGRuntime(dag)

        # node_a: 旧式 handler（不接收 context）
        async def legacy_producer(node, context):
            return {"text": "from_legacy"}

        # node_b: 新式 handler（接收 context），从 context 获取上游输出
        b_received = {}

        async def context_consumer(node, context: DAGContext):
            upstream = context.get_upstream_output("node_a")
            b_received["upstream"] = upstream
            return {"text": "from_context_consumer"}

        # node_c: 新式 handler，从 context 获取 node_b 输出
        c_received = {}

        async def final_consumer(node, context: DAGContext):
            upstream = context.get_upstream_output("node_b")
            c_received["upstream"] = upstream
            return {"final": True}

        report = await runtime.run({
            "step_one": legacy_producer,
            "step_two": context_consumer,
            "step_three": final_consumer,
        })

        assert len(report.failed_nodes) == 0
        # node_b 能获取 node_a（旧式 handler）的输出
        assert b_received["upstream"]["text"] == "from_legacy"
        # node_c 能获取 node_b 的输出
        assert c_received["upstream"]["text"] == "from_context_consumer"

    asyncio.run(run_case())
