"""T10.9 修复验证测试。

验证两个修复：
- 修复 B：_validate_gate1 同句一致性"已强制改路由"改为 warning，不让 gate1 fail
- 修复 A：JSON 路径 gate1/gate2 失败重试一次（通过 trace 状态验证）
"""
from __future__ import annotations

from app.agents.fbi.review_blueprint_agent import FBIReviewBlueprintAgent


# ---------------------------------------------------------------------------
# 修复 B：同句一致性"已强制改路由"不再让 gate1 fail
# ---------------------------------------------------------------------------


def _make_annotated_two_issues_same_sentence():
    """构造两个 issue 在同一句子的 annotated 数据。"""
    return [
        {
            "issue_id": "iss_aaa",
            "scene_index": 1,
            "source_scene": 1,
            "paragraph_index": 2,
            "sentence_index": 3,
            "metric": "dash_per_1000",
            "validator": "ai_flavor",
        },
        {
            "issue_id": "iss_bbb",
            "scene_index": 1,
            "source_scene": 1,
            "paragraph_index": 2,
            "sentence_index": 3,
            "metric": "tier1_hit_count",
            "validator": "ai_flavor",
        },
    ]


def test_gate1_routes_inconsistent_is_warning_not_error():
    """T10.9 修复 B：同句多 issue 路由不一致时，强制改路由后应记为 warning，gate1 应通过。"""
    agent = FBIReviewBlueprintAgent()
    annotated = _make_annotated_two_issues_same_sentence()

    # 两个 issue 在同一句子，路由不一致：一个 deterministic，一个 llm
    review_results = [
        {"issue_id": "iss_aaa", "action": "agree", "route": "deterministic"},
        {"issue_id": "iss_bbb", "action": "agree", "route": "llm"},
    ]

    gate1 = agent._validate_gate1({"review_results": review_results}, annotated)

    # 修复 B 后：gate1 应通过（passed=True）
    assert gate1["passed"] is True, (
        f"T10.9 修复 B 失败：同句路由不一致已强制改路由，gate1 应通过，"
        f"但 errors={gate1['errors']}"
    )
    # warnings 应包含 "routes inconsistent"
    warnings = gate1.get("warnings", [])
    assert any("routes inconsistent" in w for w in warnings), (
        f"warnings 应包含 'routes inconsistent'，实际 warnings={warnings}"
    )
    # 两个 issue 的 route 都应被强制改为 "llm"
    assert review_results[0]["route"] == "llm", (
        f"iss_aaa 的 route 应被强制改为 llm，实际={review_results[0]['route']}"
    )
    assert review_results[1]["route"] == "llm", (
        f"iss_bbb 的 route 应被强制改为 llm，实际={review_results[1]['route']}"
    )


def test_gate1_missing_issues_still_fails():
    """T10.9 修复 B 不应弱化其他 gate1 校验：missing issues 仍应 fail。"""
    agent = FBIReviewBlueprintAgent()
    annotated = _make_annotated_two_issues_same_sentence()

    # review_results 缺少 iss_bbb
    review_results = [
        {"issue_id": "iss_aaa", "action": "agree", "route": "llm"},
    ]

    gate1 = agent._validate_gate1({"review_results": review_results}, annotated)

    # missing issues 应让 gate1 fail
    assert gate1["passed"] is False, "missing issues 应让 gate1 fail"
    assert any("missing issues" in e for e in gate1["errors"]), (
        f"errors 应包含 'missing issues'，实际={gate1['errors']}"
    )


def test_gate1_consistent_routes_pass_without_warning():
    """T10.9 修复 B：同句多 issue 路由一致时，gate1 应通过且无 warning。"""
    agent = FBIReviewBlueprintAgent()
    annotated = _make_annotated_two_issues_same_sentence()

    # 两个 issue 路由一致（都 llm）
    review_results = [
        {"issue_id": "iss_aaa", "action": "agree", "route": "llm"},
        {"issue_id": "iss_bbb", "action": "agree", "route": "llm"},
    ]

    gate1 = agent._validate_gate1({"review_results": review_results}, annotated)

    assert gate1["passed"] is True, f"路由一致时 gate1 应通过，errors={gate1['errors']}"
    warnings = gate1.get("warnings", [])
    assert not any("routes inconsistent" in w for w in warnings), (
        f"路由一致时不应有 'routes inconsistent' warning，实际={warnings}"
    )


# ---------------------------------------------------------------------------
# 修复 A：JSON 路径 gate1/gate2 失败重试（通过代码结构验证）
# ---------------------------------------------------------------------------


def test_round1_json_gate1_retry_trace_statuses_exist():
    """T10.9 修复 A：_run_round1 应包含 json_gate1_failed_retry 和 json_gate1_failed_final 状态。

    通过检查源码中包含这些状态字符串，验证重试逻辑已实现。
    （完整的功能测试需要 mock LLM，这里做结构验证。）
    """
    import inspect

    source = inspect.getsource(FBIReviewBlueprintAgent._run_round1)
    assert "json_gate1_failed_retry" in source, (
        "_run_round1 应包含 json_gate1_failed_retry 状态（T10.9 修复 A）"
    )
    assert "json_gate1_failed_final" in source, (
        "_run_round1 应包含 json_gate1_failed_final 状态（T10.9 修复 A）"
    )


def test_round3_json_gate2_retry_trace_statuses_exist():
    """T10.9 修复 A：_run_round3 应包含 json_gate2_failed_retry 和 json_gate2_failed_final 状态。"""
    import inspect

    source = inspect.getsource(FBIReviewBlueprintAgent._run_round3)
    assert "json_gate2_failed_retry" in source, (
        "_run_round3 应包含 json_gate2_failed_retry 状态（T10.9 修复 A）"
    )
    assert "json_gate2_failed_final" in source, (
        "_run_round3 应包含 json_gate2_failed_final 状态（T10.9 修复 A）"
    )
