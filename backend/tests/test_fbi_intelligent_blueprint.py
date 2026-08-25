"""FBI 智能蓝图能力落地验证测试。

覆盖审查报告 9.1-9.7 各阶段的关键能力：
- 9.2.1: compound replacement（同窗多 issue 合并）
- 9.2.2: 单 patch 安全网
- 9.2.3: family 兼容性拆分
- 9.3.1: PatchMerger 冲突分类和自动仲裁
- 9.3.2: 冲突回流到 EditWindowPlanner
- 9.4.1: LLM 蓝图官接入
- 9.5: 英文系统诊断句禁止写入正文
- 9.6: 路由表扩展
- 9.7: dependency_policy 放宽
- 失败原因可观测性
"""
from __future__ import annotations

import asyncio

from app.models.chapter_review import (
    ChapterReviewCase,
    RepairWorkUnit,
    SceneReviewPacket,
    ToolCommand,
    ToolCommandBatch,
)
from app.models.fbi_repair import RepairPatch
from app.services.fbi.blueprint_protocol_validator import BlueprintProtocolValidator
from app.services.fbi.blueprint_route_registry import (
    MetricBlueprintRoute,
    get_blueprint_route,
    route_family_for_metric,
)
from app.services.fbi.chapter_case_intake import (
    FBIChapterCaseIntakeService,
    FBIChapterRepairPlanner,
)
from app.services.fbi.edit_window_planner import EditWindowPlanner
from app.services.fbi.patch_merger import ConflictType, PatchMerger
from app.services.fbi.review_minister import FBIReviewMinister
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
# 9.2.1: compound replacement
# ---------------------------------------------------------------------------


def test_compound_replace_merges_same_window_inserts_into_replace():
    """同窗的 replace_exact + insert_after_anchor 应合并为单个 compound replace_exact。"""
    planner = EditWindowPlanner()
    base_text = "凤溪低头确认布袋还在，指尖按住袋口。"
    replace_cmd = ToolCommand(
        operation="replace_exact",
        scene_index=0,
        target_span="布袋",
        old_text="布袋",
        new_text="药袋",
        replacement="药袋",
        anchor_text="布袋",
        source_issue_ids=["iss-1"],
    )
    insert_cmd = ToolCommand(
        operation="insert_after_anchor",
        scene_index=0,
        target_span="袋口",
        after_span="袋口",
        new_text="她屏住呼吸。",
        replacement="她屏住呼吸。",
        source_issue_ids=["iss-2"],
    )
    units = [
        RepairWorkUnit(
            work_unit_id="wu-1",
            owner_scene=0,
            target_scenes=[0],
            tool_batch=ToolCommandBatch(batch_id="b-1", target_scenes=[0], commands=[replace_cmd]),
            source_violation_ids=["iss-1"],
        ),
        RepairWorkUnit(
            work_unit_id="wu-2",
            owner_scene=0,
            target_scenes=[0],
            tool_batch=ToolCommandBatch(batch_id="b-2", target_scenes=[0], commands=[insert_cmd]),
            source_violation_ids=["iss-2"],
        ),
    ]
    planned, trace = planner.plan_work_units(units, {0: base_text})
    assert len(planned) == 1
    assert trace["compound_windows"] >= 1
    cmds = planned[0].tool_batch.commands
    assert len(cmds) == 1
    assert cmds[0].operation == "replace_exact"
    assert "药袋" in cmds[0].new_text
    assert "她屏住呼吸" in cmds[0].new_text


def test_compound_replace_does_not_merge_different_old_texts():
    """9.2.1 修复：多个 replace_exact 且 old_text 不同时不做 compound 合并。"""
    planner = EditWindowPlanner()
    base_text = "凤溪低头确认布袋还在，指尖按住袋口。她抬眼看向水面。"
    cmd1 = ToolCommand(
        operation="replace_exact",
        scene_index=0,
        target_span="布袋",
        old_text="布袋",
        new_text="药袋",
        replacement="药袋",
        anchor_text="布袋",
        source_issue_ids=["iss-1"],
    )
    cmd2 = ToolCommand(
        operation="replace_exact",
        scene_index=0,
        target_span="水面",
        old_text="水面",
        new_text="溪面",
        replacement="溪面",
        anchor_text="水面",
        source_issue_ids=["iss-2"],
    )
    units = [
        RepairWorkUnit(
            work_unit_id="wu-1",
            owner_scene=0,
            target_scenes=[0],
            tool_batch=ToolCommandBatch(batch_id="b-1", target_scenes=[0], commands=[cmd1]),
            source_violation_ids=["iss-1"],
        ),
        RepairWorkUnit(
            work_unit_id="wu-2",
            owner_scene=0,
            target_scenes=[0],
            tool_batch=ToolCommandBatch(batch_id="b-2", target_scenes=[0], commands=[cmd2]),
            source_violation_ids=["iss-2"],
        ),
    ]
    planned, _ = planner.plan_work_units(units, {0: base_text})
    # 两个不同 old_text 的 replace_exact 不应合并为单个 compound
    all_cmds = [c for u in planned for c in u.tool_batch.commands]
    assert len(all_cmds) == 2
    assert all(c.operation == "replace_exact" for c in all_cmds)


# ---------------------------------------------------------------------------
# 9.2.3: family 兼容性拆分
# ---------------------------------------------------------------------------


def test_family_compatibility_split_separates_incompatible_families():
    """fact 和 anti_ai_discourse 不兼容，应拆分到不同子窗口。"""
    planner = EditWindowPlanner()
    # fact family 不允许与 anti_ai_discourse 同窗
    unit_a = RepairWorkUnit(
        work_unit_id="wu-fact",
        owner_scene=0,
        target_scenes=[0],
        compound_issue_families=["fact"],
        tool_batch=ToolCommandBatch(
            batch_id="b-fact",
            target_scenes=[0],
            commands=[ToolCommand(
                operation="replace_exact",
                scene_index=0,
                old_text="右手",
                new_text="左手",
                target_span="右手",
                anchor_text="右手",
                source_issue_ids=["iss-fact"],
            )],
        ),
        source_violation_ids=["iss-fact"],
    )
    unit_b = RepairWorkUnit(
        work_unit_id="wu-discourse",
        owner_scene=0,
        target_scenes=[0],
        compound_issue_families=["anti_ai_discourse"],
        tool_batch=ToolCommandBatch(
            batch_id="b-discourse",
            target_scenes=[0],
            commands=[ToolCommand(
                operation="replace_exact",
                scene_index=0,
                old_text="命运",
                new_text="前路",
                target_span="命运",
                anchor_text="命运",
                source_issue_ids=["iss-discourse"],
            )],
        ),
        source_violation_ids=["iss-discourse"],
    )
    base_text = "她右手按住命运的方向。"
    planned, trace = planner.plan_work_units([unit_a, unit_b], {0: base_text})
    # family 不兼容时应拆分为 2 个独立 unit
    assert len(planned) == 2
    assert trace.get("family_split_windows", 0) >= 1


def test_family_compatibility_allows_anti_ai_variants_together():
    """anti_ai_punctuation 和 anti_ai_local 兼容，可同窗。"""
    planner = EditWindowPlanner()
    unit_a = RepairWorkUnit(
        work_unit_id="wu-punct",
        owner_scene=0,
        target_scenes=[0],
        compound_issue_families=["anti_ai_punctuation"],
        tool_batch=ToolCommandBatch(
            batch_id="b-punct",
            target_scenes=[0],
            commands=[ToolCommand(
                operation="replace_exact",
                scene_index=0,
                old_text="——",
                new_text="，",
                target_span="——",
                anchor_text="——",
                source_issue_ids=["iss-punct"],
            )],
        ),
        source_violation_ids=["iss-punct"],
    )
    unit_b = RepairWorkUnit(
        work_unit_id="wu-local",
        owner_scene=0,
        target_scenes=[0],
        compound_issue_families=["anti_ai_local"],
        tool_batch=ToolCommandBatch(
            batch_id="b-local",
            target_scenes=[0],
            commands=[ToolCommand(
                operation="insert_after_anchor",
                scene_index=0,
                target_span="——",
                after_span="——",
                new_text="她顿了顿。",
                replacement="她顿了顿。",
                source_issue_ids=["iss-local"],
            )],
        ),
        source_violation_ids=["iss-local"],
    )
    base_text = "她走了——然后停下。"
    planned, trace = planner.plan_work_units([unit_a, unit_b], {0: base_text})
    # 兼容 family 应合并为 1 个 compound unit
    assert len(planned) == 1
    assert trace.get("compound_windows", 0) >= 1


# ---------------------------------------------------------------------------
# 9.3.1: PatchMerger 冲突分类和自动仲裁
# ---------------------------------------------------------------------------


def test_patch_merger_resolves_duplicate_patch():
    """完全相同的补丁应被仲裁为 DUPLICATE_PATCH，只保留一个。"""
    merger = PatchMerger()
    base_text = "她走了过去。"
    patch_a = RepairPatch(
        patch_id="p-a",
        case_id="case-1",
        intent_id="intent-1",
        base_text_hash="hash-1",
        strategy="replace_exact",
        span={"start": 0, "end": 2},
        original_text="她走",
        replacement_text="她跑",
        resolves_issue_ids=["iss-1"],
    )
    patch_b = RepairPatch(
        patch_id="p-b",
        case_id="case-1",
        intent_id="intent-1",
        base_text_hash="hash-1",
        strategy="replace_exact",
        span={"start": 0, "end": 2},
        original_text="她走",
        replacement_text="她跑",
        resolves_issue_ids=["iss-2"],
    )
    result = merger.merge([patch_a, patch_b], base_text)
    merged = result.get("merged_patches", [])
    assert len(merged) == 1


def test_patch_merger_resolves_contained_patch():
    """被包含的补丁应被仲裁为 CONTAINED_PATCH，吸收到外层补丁。"""
    merger = PatchMerger()
    base_text = "她走了过去然后停下。"
    outer = RepairPatch(
        patch_id="p-outer",
        case_id="case-1",
        intent_id="intent-1",
        base_text_hash="hash-1",
        strategy="replace_exact",
        span={"start": 0, "end": 6},
        original_text="她走了过去",
        replacement_text="她跑了过来",
        resolves_issue_ids=["iss-1"],
    )
    inner = RepairPatch(
        patch_id="p-inner",
        case_id="case-1",
        intent_id="intent-1",
        base_text_hash="hash-1",
        strategy="replace_exact",
        span={"start": 0, "end": 2},
        original_text="她走",
        replacement_text="她跑",
        resolves_issue_ids=["iss-2"],
    )
    result = merger.merge([outer, inner], base_text)
    merged = result.get("merged_patches", [])
    assert len(merged) == 1
    assert "她跑了过来" in merged[0].replacement_text


def test_patch_merger_classifies_unsafe_conflict_for_reflow():
    """不同 family 的重叠补丁不可仲裁，应回流到 compound_reflow。"""
    merger = PatchMerger()
    base_text = "她走了过去。"
    patch_a = RepairPatch(
        patch_id="p-a",
        case_id="case-1",
        intent_id="intent-1",
        base_text_hash="hash-1",
        strategy="replace_exact",
        span={"start": 0, "end": 4},
        original_text="她走了过",
        replacement_text="她跑了过",
        resolves_issue_ids=["iss-1"],
    )
    patch_b = RepairPatch(
        patch_id="p-b",
        case_id="case-1",
        intent_id="intent-1",
        base_text_hash="hash-1",
        strategy="replace_exact",
        span={"start": 2, "end": 6},
        original_text="了过去。",
        replacement_text="了过来。",
        resolves_issue_ids=["iss-2"],
    )
    result = merger.merge([patch_a, patch_b], base_text)
    reflow = result.get("compound_reflow", [])
    # 重叠且不可仲裁时应产生 compound_reflow
    assert len(reflow) >= 1
    assert reflow[0].get("needs_compound_patch") is True


# ---------------------------------------------------------------------------
# 9.5: 英文系统诊断句禁止写入正文
# ---------------------------------------------------------------------------


def test_blueprint_protocol_validator_rejects_english_system_sentence():
    """包含英文系统诊断句的 command 应被 language_gate 拒绝。"""
    validator = BlueprintProtocolValidator()
    cmd = ToolCommand(
        operation="insert_after_anchor",
        scene_index=0,
        target_span="anchor",
        after_span="anchor",
        new_text="This beat makes the required state explicit.",
        replacement="This beat makes the required state explicit.",
        anchor_text="anchor",
        source_issue_ids=["iss-1"],
        expected_metric_delta={"metric": "test", "delta": 1},
        guards={"protected_spans": []},
    )
    unit = RepairWorkUnit(
        work_unit_id="wu-1",
        owner_scene=0,
        target_scenes=[0],
        tool_batch=ToolCommandBatch(batch_id="b-1", target_scenes=[0], commands=[cmd]),
    )
    result = validator.validate_work_unit(unit)
    assert not result.get("accepted")
    assert "replacement_contains_system_language" in result.get("failures", [])


def test_contract_completion_english_required_is_deferred_to_creative_repair():
    """英文 required state 不得在蓝图阶段被模板翻译或写入正文。"""
    case = ChapterReviewCase(
        project_id="project",
        chapter_number=1,
        scene_packets=[
            SceneReviewPacket(
                scene_index=0,
                candidate_text="She packed the herbs.",
                blocking_violations=[
                    {
                        "type": "ending_state_not_reached",
                        "metric": "ending_state_not_reached",
                        "source": "scene_review",
                        "severity": "high",
                        "detail": "Scene ending state should be 'begin refining medicine with the fifth senior brother'.",
                        "blocks_commit": True,
                    }
                ],
            )
        ],
    )
    validated = asyncio.run(FBIChapterCaseIntakeService().intake(case))
    plan = asyncio.run(FBIChapterRepairPlanner().plan(validated))

    assert plan.work_units
    cmd = plan.work_units[0].tool_batch.commands[0]
    assert cmd.operation == "llm_creative_rewrite"
    # 不应包含系统诊断句
    assert "This beat" not in cmd.new_text
    assert "required state" not in cmd.new_text
    # 不应包含英文污染（英文 required 不应直接写入中文正文）
    assert "begin refining medicine" not in cmd.new_text
    assert "They take the next step" not in cmd.new_text
    assert not cmd.new_text


# ---------------------------------------------------------------------------
# 9.6: 路由表扩展
# ---------------------------------------------------------------------------


def test_route_registry_has_contract_completion_routes():
    """路由表应包含 6 个结构合同类 metric 路由。"""
    structure_metrics = [
        "ending_state_not_reached",
        "missing_must_show",
        "goal_present",
        "conflict_present",
        "value_change",
        "scene_goal_missing",
    ]
    for metric in structure_metrics:
        route = get_blueprint_route(metric)
        assert route is not None, f"Missing route for {metric}"
        assert route.family == "structure"
        assert route.builder == "build_contract_completion_insert"
        assert route.operation == "insert_after_anchor"


def test_route_registry_has_show_evidence_routes():
    """路由表应包含抽象具体化类 metric 路由。"""
    show_evidence_metrics = [
        "abstraction_over_budget",
        "standalone_abstract_claims",
        "abstract_bare_count",
    ]
    for metric in show_evidence_metrics:
        route = get_blueprint_route(metric)
        assert route is not None, f"Missing route for {metric}"
        assert route.family == "show_evidence"
        assert route.builder == "build_show_evidence_patch"


def test_route_family_for_metric_returns_correct_family():
    """route_family_for_metric 应返回正确的 family。"""
    assert route_family_for_metric("ending_state_not_reached") == "structure"
    assert route_family_for_metric("abstraction_over_budget") == "show_evidence"
    assert route_family_for_metric("fact_conflict") == "fact"


# ---------------------------------------------------------------------------
# 9.7: dependency_policy 放宽
# ---------------------------------------------------------------------------


def test_dependency_policy_allows_anti_ai_batching():
    """anti_ai family 的 dependency_policy 应允许同窗批处理。"""
    case = ChapterReviewCase(
        project_id="project",
        chapter_number=1,
        scene_packets=[
            SceneReviewPacket(
                scene_index=0,
                candidate_text="她走了过去。然后停下。",
                blocking_violations=[
                    {
                        "type": "dash_per_1000",
                        "metric": "dash_per_1000",
                        "validator": "ai_flavor",
                        "severity": "high",
                        "blocks_commit": True,
                        "source_scene": 0,
                        "expected_max": 2.5,
                    }
                ],
            )
        ],
    )
    ds = FBIReviewMinister().diagnose_case(case)
    for d in ds.diagnoses:
        policy = d.dependency_policy
        # anti_ai_punctuation 应允许同窗批处理
        if d.issue_family == "anti_ai_punctuation":
            assert policy.can_batch_with is not None
            assert "anti_ai_punctuation" in (policy.can_batch_with or [])


# ---------------------------------------------------------------------------
# 失败原因可观测性
# ---------------------------------------------------------------------------


def test_route_failure_reason_is_observable():
    """当路由命中但 builder 失败时，失败原因应记录在 blueprint 中。"""
    # 构造一个 fact_conflict 但没有 target_span 的 violation，让 builder 失败
    case = ChapterReviewCase(
        project_id="project",
        chapter_number=1,
        scene_packets=[
            SceneReviewPacket(
                scene_index=0,
                candidate_text="她走了过去。",
                blocking_violations=[
                    {
                        "type": "fact_conflict",
                        "metric": "fact_conflict",
                        "severity": "high",
                        "detail": "Text has a fact conflict.",
                        "blocks_commit": True,
                        # 没有 target_span 和 evidence，builder 应失败
                    }
                ],
            )
        ],
    )
    ds = FBIReviewMinister().diagnose_case(case)
    assert len(ds.diagnoses) == 1
    bp = ds.diagnoses[0].revision_blueprint
    # builder 失败时 status 应为 blueprint_incomplete
    assert bp.status == "blueprint_incomplete"


# ---------------------------------------------------------------------------
# 9.2.2: 单 patch 安全网
# ---------------------------------------------------------------------------


def test_enforce_single_patch_per_window_keeps_non_overlapping():
    """安全网不应拆分非重叠的 command。"""
    planner = EditWindowPlanner()
    cmd1 = ToolCommand(
        operation="replace_exact",
        scene_index=0,
        old_text="A",
        new_text="B",
        target_span="A",
        anchor_text="A",
        span_start=0,
        span_end=1,
        source_issue_ids=["iss-1"],
    )
    cmd2 = ToolCommand(
        operation="replace_exact",
        scene_index=0,
        old_text="C",
        new_text="D",
        target_span="C",
        anchor_text="C",
        span_start=10,
        span_end=11,
        source_issue_ids=["iss-2"],
    )
    unit = RepairWorkUnit(
        work_unit_id="wu-1",
        owner_scene=0,
        target_scenes=[0],
        tool_batch=ToolCommandBatch(batch_id="b-1", target_scenes=[0], commands=[cmd1, cmd2]),
    )
    result = planner._enforce_single_patch_per_window([unit])
    assert len(result) == 1
    assert len(result[0].tool_batch.commands) == 2


# ---------------------------------------------------------------------------
# 端到端：compound replacement + ToolExecutor
# ---------------------------------------------------------------------------


def test_compound_replace_end_to_end_executes_correctly():
    """compound replace 命令应被 ToolExecutor 正确执行。"""
    base_text = "她走了过去。然后停下。"
    cmd = ToolCommand(
        operation="replace_exact",
        scene_index=0,
        old_text="她走了过去。",
        new_text="她跑了过来。她屏住呼吸。",
        replacement="她跑了过来。她屏住呼吸。",
        target_span="她走了过去。",
        anchor_text="她走了过去。",
        source_issue_ids=["iss-1", "iss-2"],
    )
    unit = RepairWorkUnit(
        work_unit_id="wu-compound",
        owner_scene=0,
        target_scenes=[0],
        tool_batch=ToolCommandBatch(batch_id="b-compound", target_scenes=[0], commands=[cmd]),
    )
    updated, audits = _execute_via_patches([unit], {0: base_text})
    assert "她跑了过来" in updated[0]
    assert "她屏住呼吸" in updated[0]
    assert "她走了过去" not in updated[0]


def test_llm_blueprint_issue_ids_are_mapped_to_order_ids():
    """LLM blueprint units must be mapped back to repair orders before execution."""
    cmd = ToolCommand(
        operation="replace_exact",
        scene_index=0,
        old_text="A",
        new_text="B",
        target_span="A",
        anchor_text="A",
        source_issue_ids=["issue-1"],
    )
    unit = RepairWorkUnit(
        work_unit_id="llm-wu",
        owner_scene=0,
        target_scenes=[0],
        source_violation_ids=["issue-1"],
        tool_batch=ToolCommandBatch(batch_id="llm-batch", target_scenes=[0], commands=[cmd]),
    )

    FBIChapterRepairPlanner._attach_source_order_ids_to_units(
        [unit],
        {"issue-1": ["order-1"]},
    )

    assert unit.source_order_ids == ["order-1"]
    assert unit.tool_batch.source_order_ids == ["order-1"]
