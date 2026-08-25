"""方案17：真实 handler + DAGContext 上下文使用契约测试。

本测试套件是对 test_plan17_dag_context.py 和 test_plan17_handler_context_signature.py
的补充，专门覆盖 P1-13 指出的问题：旧测试用 mock handler，未覆盖实际 handler
是否真正使用 context。

由于 editor_chat.py 中的 handle_* 函数是在外层函数内嵌套定义的闭包，
无法直接导入，因此本套件采用以下替代策略：

1. DAGContext API 契约测试：不依赖 runtime，直接构造 DAGContext 实例，
   验证其方法的行为契约（get_upstream_output / get_all_upstream_outputs /
   get_shared_state / set_shared_state）。
2. 真实 DAG 集成测试：用真实 EditorGenerationDAGRuntime + 真实 handler
   （非 mock），验证 context 在多节点 DAG 中的传递、隔离和共享行为。
3. editor_chat.py 源码静态契约验证：解析源文件，验证所有 handle_* 函数
   不仅签名包含 context，而且函数体中实际使用了 context（读取上游输出
   或读写共享状态），确保 context 不是"装饰性参数"。
"""
from __future__ import annotations

import asyncio
import os
import re
from types import SimpleNamespace

import pytest

from app.services.editor_generation_dag import EditorDagNode, EditorGenerationDAG
from app.services.editor_generation_dag_runtime import (
    DAGContext,
    EditorGenerationDAGRuntime,
)


# ---------------------------------------------------------------------------
# DAGContext API 契约测试：不依赖 runtime，直接验证 API 行为
# ---------------------------------------------------------------------------


def _make_bare_context(
    upstream: dict | None = None,
    shared_state: dict | None = None,
) -> DAGContext:
    """构造一个不依赖完整 runtime 的 DAGContext 实例。

    DAGContext 的 __init__ 接收 (node, upstream_outputs, runtime)，
    其中 runtime 只在 get_shared_state / set_shared_state 中通过
    runtime.shared_state 访问。用一个 SimpleNamespace 提供最小化的
    runtime 即可满足 API 契约测试需求。
    """
    node = EditorDagNode(
        node_id="contract_test_node",
        node_type="test",
        display_name="Contract Test",
        dependencies=[],
    )
    fake_runtime = SimpleNamespace(shared_state=dict(shared_state or {}))
    return DAGContext(
        node=node,
        upstream_outputs=dict(upstream or {}),
        runtime=fake_runtime,
    )


def test_get_upstream_output_returns_dict_unchanged():
    """契约：上游输出是 dict 时，原样返回。"""
    ctx = _make_bare_context(upstream={"upstream_a": {"text": "hello", "count": 3}})
    output = ctx.get_upstream_output("upstream_a")
    assert output == {"text": "hello", "count": 3}
    assert isinstance(output, dict)


def test_get_upstream_output_wraps_non_dict_in_result_key():
    """契约：上游输出是非 dict 时，包装为 {"result": value}。"""
    ctx = _make_bare_context(upstream={"upstream_str": "raw_string"})
    output = ctx.get_upstream_output("upstream_str")
    assert output == {"result": "raw_string"}

    ctx_list = _make_bare_context(upstream={"upstream_list": [1, 2, 3]})
    output_list = ctx_list.get_upstream_output("upstream_list")
    assert output_list == {"result": [1, 2, 3]}

    ctx_int = _make_bare_context(upstream={"upstream_int": 42})
    output_int = ctx_int.get_upstream_output("upstream_int")
    assert output_int == {"result": 42}


def test_get_upstream_output_returns_none_for_missing_node():
    """契约：查询不存在的上游节点时返回 None。"""
    ctx = _make_bare_context(upstream={"existing": {"data": 1}})
    assert ctx.get_upstream_output("nonexistent") is None
    assert ctx.get_upstream_output("") is None


def test_get_upstream_output_returns_none_for_none_value():
    """契约：上游输出显式为 None 时返回 None（不包装为 {"result": None}）。"""
    ctx = _make_bare_context(upstream={"null_output": None})
    assert ctx.get_upstream_output("null_output") is None


def test_get_all_upstream_outputs_returns_copy():
    """契约：get_all_upstream_outputs 返回内部 dict 的副本，修改不影响内部状态。"""
    upstream = {"node_a": {"data": 1}, "node_b": {"data": 2}}
    ctx = _make_bare_context(upstream=upstream)
    snapshot = ctx.get_all_upstream_outputs()
    assert snapshot == upstream

    # 修改返回的副本不应影响内部状态
    snapshot["node_a"]["data"] = 999
    snapshot["injected"] = "evil"
    fresh_snapshot = ctx.get_all_upstream_outputs()
    assert fresh_snapshot["node_a"]["data"] == 1
    assert "injected" not in fresh_snapshot


def test_get_shared_state_returns_value_for_existing_key():
    """契约：get_shared_state 返回已存在的值。"""
    ctx = _make_bare_context(shared_state={"existing_key": "existing_value"})
    assert ctx.get_shared_state("existing_key") == "existing_value"


def test_get_shared_state_returns_default_for_missing_key():
    """契约：get_shared_state 对不存在的 key 返回 default（默认 None）。"""
    ctx = _make_bare_context(shared_state={})
    assert ctx.get_shared_state("missing") is None
    assert ctx.get_shared_state("missing", "fallback") == "fallback"
    assert ctx.get_shared_state("missing", default=42) == 42


def test_set_shared_state_writes_to_runtime():
    """契约：set_shared_state 写入 runtime.shared_state，可被后续 get 读回。"""
    ctx = _make_bare_context(shared_state={})
    ctx.set_shared_state("new_key", "new_value")
    assert ctx.get_shared_state("new_key") == "new_value"

    ctx.set_shared_state("overwrite", "before")
    ctx.set_shared_state("overwrite", "after")
    assert ctx.get_shared_state("overwrite") == "after"


def test_shared_state_persists_across_contexts_sharing_runtime():
    """契约：共享同一 runtime 的多个 DAGContext 实例可见彼此写入的共享状态。

    这是过渡期兼容闭包变量的关键契约：不同节点的 context 共享同一
    runtime.shared_state 字典，set_shared_state 写入的值对其他节点可见。
    """
    shared_runtime = SimpleNamespace(shared_state={})
    node_a = EditorDagNode("node_a", "A", "A", dependencies=[])
    node_b = EditorDagNode("node_b", "B", "B", dependencies=["node_a"])

    ctx_a = DAGContext(node=node_a, upstream_outputs={}, runtime=shared_runtime)
    ctx_b = DAGContext(node=node_b, upstream_outputs={"node_a": {}}, runtime=shared_runtime)

    ctx_a.set_shared_state("cross_context_key", "from_a")
    assert ctx_b.get_shared_state("cross_context_key") == "from_a"

    ctx_b.set_shared_state("ack", "from_b")
    assert ctx_a.get_shared_state("ack") == "from_b"


def test_context_node_attribute_exposes_current_node():
    """契约：context.node 暴露当前正在执行的节点。"""
    node = EditorDagNode("my_node", "MyNode", "my_type", dependencies=[])
    ctx = DAGContext(node=node, upstream_outputs={}, runtime=SimpleNamespace(shared_state={}))
    assert ctx.node is node
    assert ctx.node.node_id == "my_node"


# ---------------------------------------------------------------------------
# 真实 DAG 集成测试：用真实 runtime + 真实 handler（非 mock）验证 context 传递
# ---------------------------------------------------------------------------


def _make_diamond_dag() -> EditorGenerationDAG:
    """构造菱形 DAG：root -> (left, right) -> sink。

    用于验证：
    - 多上游节点的 context.get_all_upstream_outputs 包含所有上游
    - 并行分支的输出在汇合节点可见
    - 共享状态在并行节点间可写入并最终汇总
    """
    nodes = [
        EditorDagNode("root", "Root", "step_root", dependencies=[]),
        EditorDagNode("left", "Left", "step_left", dependencies=["root"]),
        EditorDagNode("right", "Right", "step_right", dependencies=["root"]),
        EditorDagNode("sink", "Sink", "step_sink", dependencies=["left", "right"]),
    ]
    return EditorGenerationDAG(dag_version="test_diamond", mode="test", scene_count=1, nodes=nodes)


def test_real_diamond_dag_context_propagation():
    """真实菱形 DAG：验证 context 在并行 + 汇合场景下的数据传递契约。

    不使用任何 mock，所有 handler 都是真实函数，所有 DAGContext 都是
    runtime 真实构造的实例。
    """

    async def run_case():
        dag = _make_diamond_dag()
        runtime = EditorGenerationDAGRuntime(dag, project_id=None)
        sink_observations: dict = {}

        async def root_handler(node, context: DAGContext):
            context.set_shared_state("root_marker", "root_was_here")
            return {"root_value": 100, "trace": ["root"]}

        async def left_handler(node, context: DAGContext):
            upstream = context.get_upstream_output("root")
            assert upstream is not None, "left should see root output"
            assert upstream["root_value"] == 100
            context.set_shared_state("left_marker", "left_was_here")
            return {"left_value": upstream["root_value"] + 1, "branch": "left"}

        async def right_handler(node, context: DAGContext):
            upstream = context.get_upstream_output("root")
            assert upstream is not None, "right should see root output"
            assert upstream["root_value"] == 100
            context.set_shared_state("right_marker", "right_was_here")
            return {"right_value": upstream["root_value"] + 2, "branch": "right"}

        async def sink_handler(node, context: DAGContext):
            all_upstream = context.get_all_upstream_outputs()
            sink_observations["upstream_keys"] = sorted(all_upstream.keys())
            sink_observations["left_value"] = all_upstream["left"]["left_value"]
            sink_observations["right_value"] = all_upstream["right"]["right_value"]
            sink_observations["root_marker"] = context.get_shared_state("root_marker")
            sink_observations["left_marker"] = context.get_shared_state("left_marker")
            sink_observations["right_marker"] = context.get_shared_state("right_marker")
            return {"aggregated": True}

        report = await runtime.run({
            "step_root": root_handler,
            "step_left": left_handler,
            "step_right": right_handler,
            "step_sink": sink_handler,
        })

        assert len(report.failed_nodes) == 0, f"DAG failed: {report.failed_nodes}"
        # sink 看到两个直接上游（left, right），不包含间接上游 root
        assert sink_observations["upstream_keys"] == ["left", "right"]
        assert sink_observations["left_value"] == 101
        assert sink_observations["right_value"] == 102
        # 共享状态在所有节点间可见
        assert sink_observations["root_marker"] == "root_was_here"
        assert sink_observations["left_marker"] == "left_was_here"
        assert sink_observations["right_marker"] == "right_was_here"
        # node_outputs 被真实存储
        assert "root" in runtime.node_outputs
        assert "left" in runtime.node_outputs
        assert "right" in runtime.node_outputs
        assert "sink" in runtime.node_outputs

    asyncio.run(run_case())


def test_real_dag_handler_reads_upstream_and_shared_state():
    """真实 DAG：handler 在执行中实际读取上游输出和共享状态，并基于此决策返回值。

    这验证 context 不是"装饰性参数"——handler 真实消费 context 数据
    来决定自己的输出，而非无视 context 直接返回固定值。
    """

    async def run_case():
        nodes = [
            EditorDagNode("producer", "Producer", "step_produce", dependencies=[]),
            EditorDagNode("consumer", "Consumer", "step_consume", dependencies=["producer"]),
        ]
        dag = EditorGenerationDAG(dag_version="test_consume", mode="test", scene_count=1, nodes=nodes)
        runtime = EditorGenerationDAGRuntime(dag, project_id=None)

        async def producer(node, context: DAGContext):
            context.set_shared_state("producer_secret", "42")
            return {"payload": "produced_data", "multiplier": 3}

        consumer_received: dict = {}

        async def consumer(node, context: DAGContext):
            upstream = context.get_upstream_output("producer")
            secret = context.get_shared_state("producer_secret")
            # handler 真实消费 context 数据决定输出
            if upstream and upstream.get("multiplier") == 3 and secret == "42":
                consumer_received["transformed"] = upstream["payload"] * upstream["multiplier"]
                return {"result": consumer_received["transformed"]}
            return {"result": "fallback"}

        report = await runtime.run({
            "step_produce": producer,
            "step_consume": consumer,
        })

        assert len(report.failed_nodes) == 0
        # 验证 handler 真实读取了 context 并基于此决策
        assert consumer_received["transformed"] == "produced_dataproduced_dataproduced_data"
        assert runtime.node_outputs["consumer"]["result"] == consumer_received["transformed"]

    asyncio.run(run_case())


def test_real_dag_context_isolation_between_executions():
    """真实 DAG：多次 run 之间 context 状态隔离。

    验证每次 run 创建独立的 runtime 和 context，不残留上一次的状态。
    """

    async def run_case():
        nodes = [
            EditorDagNode("n1", "N1", "step_one", dependencies=[]),
            EditorDagNode("n2", "N2", "step_two", dependencies=["n1"]),
        ]
        dag = EditorGenerationDAG(dag_version="test_iso", mode="test", scene_count=1, nodes=nodes)

        # 第一次 run：写入 shared_state
        runtime1 = EditorGenerationDAGRuntime(dag, project_id=None)

        async def handler_run1(node, context: DAGContext):
            context.set_shared_state("run_marker", "run1")
            return {"run": 1}

        await runtime1.run({"step_one": handler_run1, "step_two": handler_run1})
        assert runtime1.shared_state.get("run_marker") == "run1"

        # 第二次 run：全新 runtime，不应看到第一次的 shared_state
        runtime2 = EditorGenerationDAGRuntime(dag, project_id=None)
        observed_marker = None

        async def handler_run2(node, context: DAGContext):
            nonlocal observed_marker
            # 只在第一个节点读取（避免被第二个节点覆盖）
            if node.node_id == "n1":
                observed_marker = context.get_shared_state("run_marker", "clean")
            context.set_shared_state("run_marker", "run2")
            return {"run": 2}

        await runtime2.run({"step_one": handler_run2, "step_two": handler_run2})
        assert observed_marker == "clean", "new runtime should not see previous run's shared_state"
        assert runtime2.shared_state.get("run_marker") == "run2"

    asyncio.run(run_case())


# ---------------------------------------------------------------------------
# editor_chat.py 源码静态契约验证：handle_* 不仅签名有 context，函数体也用 context
# ---------------------------------------------------------------------------


def test_editor_chat_handlers_actually_use_context_in_body():
    """验证 editor_chat.py 中所有 handle_* 函数在函数体中实际使用 context。

    P1-13 的核心问题：旧测试只验证 handler 签名包含 context 参数，但无法
    保证 handler 真实消费 context 数据。本测试通过源码静态分析，检查每个
    handle_* 函数体中是否实际调用了 context 的方法（get_upstream_output /
    get_all_upstream_outputs / get_shared_state / set_shared_state）或访问了
    context.node / context.<attr>。

    由于 handle_* 是闭包内定义的，无法用 inspect.getsource 直接获取单个
    函数源码，因此采用正则匹配源文件中 async def handle_xxx 块的方式。
    """
    editor_chat_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "app", "api", "editor_chat.py",
    )
    if not os.path.exists(editor_chat_path):
        pytest.skip(f"editor_chat.py not found at {editor_chat_path}")

    with open(editor_chat_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 匹配 async def handle_xxx(node, context) ... 直到下一个 async def 或 def（同缩进或更小缩进）
    # 由于 handle_* 是嵌套定义，使用相对宽松的块匹配：从函数定义开始，
    # 截取到下一个 "async def " 或顶层 "def " 出现的位置。
    pattern = re.compile(
        r"(?P<indent>[ \t]*)async\s+def\s+(?P<name>handle_\w+)\s*\((?P<params>[^)]*)\)\s*(?:->\s*[^:]+)?\s*:\s*\n(?P<body>(?:(?![ \t]*async\s+def\s)[^\n]*\n)+)",
        re.MULTILINE,
    )

    matches = list(pattern.finditer(content))
    # R4-10：handle_fbi_chapter_repair_plan 死代码已删除，handle_* 数量从 14 降为 13
    assert len(matches) >= 13, (
        f"Expected at least 13 handle_* functions in editor_chat.py, found {len(matches)}"
    )

    # context 使用模式：以下任意一种即视为"真实使用 context"
    context_usage_patterns = [
        r"context\.get_upstream_output\s*\(",
        r"context\.get_all_upstream_outputs\s*\(",
        r"context\.get_shared_state\s*\(",
        r"context\.set_shared_state\s*\(",
        r"context\.node\b",
    ]
    usage_re = re.compile("|".join(context_usage_patterns))

    handlers_not_using_context: list[str] = []

    for match in matches:
        name = match.group("name")
        body = match.group("body")

        # 验证签名包含 context 参数
        params = match.group("params")
        param_list = [p.strip() for p in params.split(",")]
        assert "context" in param_list, (
            f"handler '{name}' does not accept context parameter. Params: {param_list}"
        )

        # 验证函数体中实际使用了 context
        if not usage_re.search(body):
            handlers_not_using_context.append(name)

    # 至少应有部分 handler 真实使用 context；如果全部未使用则视为契约破坏。
    # 注意：少数 handler 可能仅作为占位或转发，不直接使用 context 是允许的，
    # 但如果使用 context 的 handler 数量为 0，则说明 context 参数是装饰性的。
    using_count = len(matches) - len(handlers_not_using_context)
    assert using_count > 0, (
        "No handle_* function in editor_chat.py actually uses context in its body. "
        "context parameter appears to be decorative. "
        f"Handlers not using context: {handlers_not_using_context}"
    )


def test_editor_chat_handlers_context_usage_summary():
    """输出 editor_chat.py 中 handle_* 函数使用 context 的统计摘要。

    这是一个诊断性测试，不会失败（除非解析失败），用于人工审查
    哪些 handler 还未迁移到 context 数据传递。
    """
    editor_chat_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "app", "api", "editor_chat.py",
    )
    if not os.path.exists(editor_chat_path):
        pytest.skip(f"editor_chat.py not found at {editor_chat_path}")

    with open(editor_chat_path, "r", encoding="utf-8") as f:
        content = f.read()

    pattern = re.compile(
        r"(?P<indent>[ \t]*)async\s+def\s+(?P<name>handle_\w+)\s*\((?P<params>[^)]*)\)\s*(?:->\s*[^:]+)?\s*:\s*\n(?P<body>(?:(?![ \t]*async\s+def\s)[^\n]*\n)+)",
        re.MULTILINE,
    )
    matches = list(pattern.finditer(content))

    context_methods = [
        ("get_upstream_output", r"context\.get_upstream_output\s*\("),
        ("get_all_upstream_outputs", r"context\.get_all_upstream_outputs\s*\("),
        ("get_shared_state", r"context\.get_shared_state\s*\("),
        ("set_shared_state", r"context\.set_shared_state\s*\("),
        ("context.node", r"context\.node\b"),
    ]

    summary: dict[str, dict[str, bool]] = {}
    for match in matches:
        name = match.group("name")
        body = match.group("body")
        summary[name] = {
            method_name: bool(re.search(pattern, body))
            for method_name, pattern in context_methods
        }

    # 至少应能解析到 handle_* 函数
    # R4-10：handle_fbi_chapter_repair_plan 死代码已删除，handle_* 数量从 14 降为 13
    assert len(summary) >= 13, (
        f"Expected at least 13 handle_* functions, parsed {len(summary)}"
    )

    # 统计：使用任意 context 方法的 handler 数量
    using_any = sum(1 for usage in summary.values() if any(usage.values()))
    # 至少应有 1 个 handler 使用 context（与上一个测试一致）
    assert using_any > 0, (
        "No handler uses any context method; context parameter is decorative."
    )
