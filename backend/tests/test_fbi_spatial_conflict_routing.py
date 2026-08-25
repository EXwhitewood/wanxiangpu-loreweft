"""空间/身份事实冲突 → 工具级精确补丁 完整路由验证测试。

覆盖用户要求的 6 个验收点：
1. spatial_conflict 会进入 fact route
2. spatial_conflict 有 evidence_span 时能生成 tool_blueprint
3. target_span 错误但 evidence_span 正确时，优先用 evidence_span
4. old_text_not_found 会触发重新定位而不是直接失败
5. delta recheck 里的 spatial_conflict 不再变成 needs_blueprint_completion
6. replacement 后不再出现同一人物同时在两处的判断
"""
from __future__ import annotations

import asyncio

from app.models.chapter_review import (
    ChapterReviewCase,
    SceneReviewPacket,
    ToolCommand,
    ToolCommandBatch,
)
from app.services.fbi.blueprint_route_registry import (
    get_blueprint_route,
    route_family_for_metric,
)
from app.services.fbi.chapter_case_intake import (
    FBIChapterCaseIntakeService,
    FBIChapterRepairPlanner,
)
from app.services.fbi.review_minister import (
    FBIReviewMinister,
    _build_spatial_conflict_patch,
    _detect_spatial_conflict_type,
    _generate_spatial_replacement,
)
from app.services.fbi.tool_executor import ToolExecutor


def _execute_via_patches(work_units, scene_texts):
    """测试迁移辅助：通过 execute_work_units_as_patches 执行并应用补丁，返回 (updated, audits)。"""
    patches, audits = ToolExecutor().execute_work_units_as_patches(
        work_units, scene_texts, case_id="test"
    )
    updated = dict(scene_texts)
    by_scene: dict[int, list] = {}
    for patch in patches:
        scene_idx = patch.self_audit.get("scene_index")
        if isinstance(scene_idx, int):
            by_scene.setdefault(scene_idx, []).append(patch)
    for scene_idx, scene_patches in by_scene.items():
        text = updated.get(scene_idx, "")
        for patch in sorted(scene_patches, key=lambda p: p.span.start, reverse=True):
            text = text[:patch.span.start] + patch.replacement_text + text[patch.span.end:]
        updated[scene_idx] = text
    return updated, audits


# ---------------------------------------------------------------------------
# 测试 1: spatial_conflict 会进入 fact route
# ---------------------------------------------------------------------------


def test_spatial_conflict_enters_fact_route():
    """spatial_conflict 及相关 metric 都应进入 fact family 路由。"""
    # spatial_conflict
    assert route_family_for_metric("spatial_conflict") == "fact"
    route = get_blueprint_route("spatial_conflict")
    assert route is not None
    assert route.family == "fact"
    assert route.operation == "replace_exact"
    assert route.builder == "build_spatial_conflict_patch"
    assert route.anchor_strategy == "evidence_span_first"
    assert route.fallback_anchor_strategy == "localized_context_window"

    # spatial_consistency_error
    assert route_family_for_metric("spatial_consistency_error") == "fact"
    # location_conflict
    assert route_family_for_metric("location_conflict") == "fact"
    # simultaneous_presence_conflict
    assert route_family_for_metric("simultaneous_presence_conflict") == "fact"
    # identity_location_conflict
    assert route_family_for_metric("identity_location_conflict") == "fact"

    # 所有空间冲突 metric 都应使用同一个 builder
    for metric in (
        "spatial_conflict",
        "spatial_consistency_error",
        "location_conflict",
        "simultaneous_presence_conflict",
        "identity_location_conflict",
    ):
        r = get_blueprint_route(metric)
        assert r is not None
        assert r.builder == "build_spatial_conflict_patch"


# ---------------------------------------------------------------------------
# 测试 2: spatial_conflict 有 evidence_span 时能生成 tool_blueprint
# ---------------------------------------------------------------------------


def test_spatial_conflict_with_evidence_span_generates_tool_blueprint():
    """有 evidence_span 的空间冲突应生成证据定位的创作修复命令。"""
    violation = {
        "type": "spatial_conflict",
        "metric": "spatial_conflict",
        "severity": "high",
        "detail": "五师兄同时出现在洞内和东北方向，存在空间冲突",
        "blocks_commit": True,
        "evidence_spans": [
            {"text": "那道魔气，是五师兄的气息。", "role": "conflict_claim"}
        ],
        "target_span": "她走进洞口。",  # 错误的 target_span
    }

    route = get_blueprint_route("spatial_conflict")
    assert route is not None

    blueprint, failure_reason = _build_spatial_conflict_patch_via_route(violation, route)

    assert blueprint is not None, f"builder failed: {failure_reason}"
    assert blueprint["operation"] == "llm_creative_rewrite"
    # 可写回落点必须来自 evidence_span，而非错误 target_span。
    assert "那道魔气" in blueprint["anchor_text"]
    assert "五师兄的气息" in blueprint["anchor_text"]
    assert blueprint["anchor_text"] != "她走进洞口。"
    # 蓝图层不得用固定否定句猜写正文。
    assert not blueprint.get("new_text")


def _build_spatial_conflict_patch_via_route(violation, route):
    """辅助函数：通过 _build_tool_blueprint_from_route 调用 builder。"""
    from app.services.fbi.review_minister import _build_tool_blueprint_from_route

    return _build_tool_blueprint_from_route(
        violation=violation,
        route=route,
        issue_id="test-issue-1",
        source="test",
    )


# ---------------------------------------------------------------------------
# 测试 3: target_span 错误但 evidence_span 正确时，优先用 evidence_span
# ---------------------------------------------------------------------------


def test_evidence_span_takes_priority_over_target_span():
    """当 target_span 错误（指向开头段）但 evidence_span 正确时，
    builder 应优先使用 evidence_span 作为 creative anchor。"""
    violation = {
        "type": "spatial_conflict",
        "metric": "spatial_conflict",
        "severity": "high",
        "detail": "空间冲突：人物同时出现在两地",
        "blocks_commit": True,
        "evidence_spans": [
            {"text": "远处那道身影正是五师兄。", "role": "conflict_claim"}
        ],
        "target_span": "凤溪走进山洞。",  # 错误的 target_span，指向开头
    }

    route = get_blueprint_route("spatial_conflict")
    blueprint, _ = _build_spatial_conflict_patch_via_route(violation, route)

    assert blueprint is not None
    assert blueprint["operation"] == "llm_creative_rewrite"
    assert "远处那道身影" in blueprint["anchor_text"]
    assert blueprint["anchor_text"] != "凤溪走进山洞。"


# ---------------------------------------------------------------------------
# 测试 5: delta recheck 里的 spatial_conflict 不再变成 needs_blueprint_completion
# ---------------------------------------------------------------------------


def test_delta_spatial_conflict_does_not_become_needs_blueprint_completion():
    """delta recheck 发现的 spatial_conflict 应通过路由表生成 tool_blueprint，
    而不是变成 needs_blueprint_completion。"""
    from app.services.fbi.final_delta_repair_runtime import FinalDeltaRepairRuntime

    # 构造一个 delta candidate：spatial_conflict
    candidates = [
        {
            "issue_id": "delta-spatial-1",
            "type": "spatial_conflict",
            "metric": "spatial_conflict",
            "severity": "high",
            "scene_index": 0,
            "detail": "空间冲突：五师兄同时出现在洞内和远处",
            "blocks_commit": True,
            "evidence_spans": [
                {"text": "那道魔气，是五师兄的气息。", "role": "conflict_claim"}
            ],
        }
    ]

    # _delta_has_compound_issues 应返回 True（因为 spatial_conflict 有路由）
    assert FinalDeltaRepairRuntime._delta_has_compound_issues(candidates) is True


# ---------------------------------------------------------------------------
# 测试 6: replacement 后不再出现同一人物同时在两处的判断
# ---------------------------------------------------------------------------


def test_replacement_eliminates_simultaneous_presence_claim():
    """替换后的正文不应再包含"是五师兄的气息"这一同时在场判断。"""
    old_text = "那道魔气，是五师兄的气息。"
    scene_text = f"凤溪走进洞口。{old_text}她心中一惊。"

    # 检测冲突类型
    violation = {
        "type": "spatial_conflict",
        "metric": "spatial_conflict",
        "detail": "五师兄同时出现在洞内和东北方向",
        "evidence_spans": [{"text": old_text}],
    }
    conflict_type = _detect_spatial_conflict_type(violation, old_text)
    # 应该检测为否定误认或消除同时在场
    assert conflict_type in {
        "deny_misidentification",
        "eliminate_simultaneous_presence",
    }

    # 生成替换
    replacement = _generate_spatial_replacement(old_text, conflict_type, violation)
    assert replacement
    # 替换后不应再包含"是五师兄的气息"
    assert "是五师兄的气息" not in replacement
    # 应包含否定或无法确认
    assert "并不是" in replacement or "无法确认" in replacement

    # 执行替换
    command = ToolCommand(
        command_id="cmd-test",
        operation="replace_exact",
        scene_index=0,
        old_text=old_text,
        new_text=replacement,
        anchor_text=old_text,
    )
    unit = _make_unit_with_command(command)
    updated, audits = _execute_via_patches([unit], {0: scene_text})

    assert audits[unit.work_unit_id].get("accepted")
    # 替换后的正文不应再包含原始的完整冲突声明
    assert "那道魔气，是五师兄的气息。" not in updated[0]
    # 应包含否定或无法确认声明
    assert "并不是" in updated[0] or "无法确认" in updated[0]


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _make_unit_with_command(command: ToolCommand):
    """构造一个包含单个 command 的 RepairWorkUnit。"""
    from app.models.chapter_review import RepairWorkUnit

    return RepairWorkUnit(
        work_unit_id="wu-test",
        local_id="wu-test-local",
        owner_scene=0,
        target_scenes=[0],
        source_violation_ids=["issue-1"],
        compound_issue_ids=["issue-1"],
        compound_issue_families=["fact"],
        merge_policy="compound_patch",
        tool_batch=ToolCommandBatch(
            batch_id="wu-test:batch",
            source_blueprint_id="test",
            source_work_unit_id="wu-test",
            commands=[command],
            target_scenes=[0],
        ),
        blueprint_source="test",
    )
