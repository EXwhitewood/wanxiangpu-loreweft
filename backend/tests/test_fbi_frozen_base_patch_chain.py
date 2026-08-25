import pytest

from app.models.chapter_review import (
    ChapterRepairOrder,
    ChapterRepairPlan,
    RepairWorkUnit,
    ToolCommand,
    ToolCommandBatch,
)
from app.models.fbi_repair import RepairPatch, TextSpan
from app.services.fbi.chapter_repair_executor import (
    ChapterRepairExecutor,
    _new_diagnostic_instruction_leaks,
)
from app.services.fbi.edit_window_planner import EditWindowPlanner
from app.services.fbi.patch_merger import PatchMerger
from app.services.fbi.tool_executor import ToolExecutor
from app.services.fbi.work_unit_builder import build_revision_blueprint


def _unit(unit_id: str, command: ToolCommand, issue_id: str, order_id: str = "") -> RepairWorkUnit:
    order_id = order_id or f"order-{unit_id}"
    if not command.expected_metric_delta:
        command.expected_metric_delta = {"metric": issue_id, "direction": "resolve"}
    if not command.guards:
        command.guards = {"preserve_facts": True, "forbid_unrelated_edits": True}
    return RepairWorkUnit(
        work_unit_id=unit_id,
        owner_scene=0,
        target_scenes=[0],
        source_order_ids=[order_id],
        source_violation_ids=[issue_id],
        issue_summary=issue_id,
        blueprint_source="review_minister_native",
        tool_batch=ToolCommandBatch(
            batch_id=f"{unit_id}:batch",
            source_work_unit_id=unit_id,
            commands=[command],
            target_scenes=[0],
            source_order_ids=[order_id],
        ),
    )


def test_edit_window_planner_merges_same_anchor_insertions_into_compound_unit():
    text = "Fengxi checked the wound. The trees moved.\n\nThe senior brother stayed silent."
    command_a = ToolCommand(
        command_id="a",
        operation="insert_after_anchor",
        scene_index=0,
        anchor_text="Fengxi checked the wound.",
        after_span="Fengxi checked the wound.",
        new_text=" A cost appeared.",
        replacement=" A cost appeared.",
        source_issue_ids=["low_conflict_density"],
        repair_family="commercial_pressure",
    )
    command_b = ToolCommand(
        command_id="b",
        operation="insert_after_anchor",
        scene_index=0,
        anchor_text="Fengxi checked the wound.",
        after_span="Fengxi checked the wound.",
        new_text=" A new question remained.",
        replacement=" A new question remained.",
        source_issue_ids=["weak_curiosity_engine"],
        repair_family="commercial_hook",
    )

    planned, trace = EditWindowPlanner().plan_work_units(
        [
            _unit("unit-a", command_a, "low_conflict_density", "order-a"),
            _unit("unit-b", command_b, "weak_curiosity_engine", "order-b"),
        ],
        {0: text},
        case_id="case",
    )

    assert trace["compound_windows"] == 1
    assert len(planned) == 1
    assert planned[0].merge_policy == "compound_patch"
    assert set(planned[0].source_violation_ids) == {
        "low_conflict_density",
        "weak_curiosity_engine",
    }
    assert len(planned[0].tool_batch.commands) == 1
    assert "A cost appeared." in planned[0].tool_batch.commands[0].replacement
    assert "A new question remained." in planned[0].tool_batch.commands[0].replacement


def test_tool_executor_patch_dry_run_does_not_mutate_base_scene_text():
    text = "old phrase. stays here."
    command = ToolCommand(
        command_id="replace",
        operation="replace_exact",
        scene_index=0,
        old_text="old phrase",
        target_span="old phrase",
        anchor_text="old phrase",
        new_text="new phrase",
        replacement="new phrase",
        source_issue_ids=["fact_conflict"],
    )
    unit = _unit("unit-replace", command, "fact_conflict")
    scene_texts = {0: text}

    patches, audits = ToolExecutor().execute_work_units_as_patches(
        [unit],
        scene_texts,
        case_id="case",
    )

    assert scene_texts[0] == text
    assert audits["unit-replace"]["accepted"] is True
    assert len(patches) == 1
    assert patches[0].original_text == "old phrase"
    assert patches[0].replacement_text == "new phrase"


def test_short_text_replacement_uses_explicit_span_without_guessing():
    text = "他总是低头。\n\n她没有说话。\n\n他总是避开她的目光。"
    start = text.rfind("他总是")
    command = ToolCommand(
        command_id="replace-second",
        operation="replace_exact",
        scene_index=0,
        old_text="他总是",
        target_span="他总是",
        new_text="他偶尔",
        replacement="他偶尔",
        span_start=start,
        span_end=start + len("他总是"),
        source_issue_ids=["style_repeat"],
    )
    unit = _unit("unit-short-span", command, "style_repeat")

    patches, audits = ToolExecutor().execute_work_units_as_patches(
        [unit],
        {0: text},
        case_id="case",
    )

    assert audits["unit-short-span"]["accepted"] is True
    assert len(patches) == 1
    assert patches[0].span.start == start
    assert patches[0].original_text == "他总是"
    assert patches[0].replacement_text == "他偶尔"


def test_short_text_pure_search_is_rejected_without_precise_locator():
    text = "他总是低头。\n\n她没有说话。\n\n他总是避开她的目光。"
    command = ToolCommand(
        command_id="replace-short-search",
        operation="replace_exact",
        scene_index=0,
        old_text="他总是",
        target_span="他总是",
        new_text="他偶尔",
        replacement="他偶尔",
        source_issue_ids=["style_repeat"],
    )
    unit = _unit("unit-short-search", command, "style_repeat")

    patches, audits = ToolExecutor().execute_work_units_as_patches(
        [unit],
        {0: text},
        case_id="case",
    )

    assert patches == []
    assert audits["unit-short-search"]["accepted"] is False
    assert audits["unit-short-search"]["reason"] == "insufficient_precise_locator"


def test_short_text_occurrence_locator_can_target_repeated_phrase():
    text = "他总是低头。\n\n她没有说话。\n\n他总是避开她的目光。"
    second = text.rfind("他总是")
    command = ToolCommand(
        command_id="replace-second-occurrence",
        operation="replace_exact",
        scene_index=0,
        old_text="他总是",
        target_span="他总是",
        new_text="他偶尔",
        replacement="他偶尔",
        anchor_occurrence=2,
        source_issue_ids=["style_repeat"],
    )
    unit = _unit("unit-short-occurrence", command, "style_repeat")

    patches, audits = ToolExecutor().execute_work_units_as_patches(
        [unit],
        {0: text},
        case_id="case",
    )

    assert audits["unit-short-occurrence"]["accepted"] is True
    assert len(patches) == 1
    assert patches[0].span.start == second


def test_fact_goal_compatibility_does_not_emit_stone_fragment_replacement():
    order = ChapterRepairOrder(
        order_id="order-fragment",
        owner_scene=0,
        target_scenes=[0],
        source_violation_ids=["fact_conflict"],
        repair_type="local_patch",
        reason="事实层要求五师兄轻伤，但正文有明显重伤和岩洞石壁描述。",
        repair_brief={
            "fact_repair_goal": {
                "conflict_type": "missing_required_components",
                "required_entities": ["五师兄为轻伤"],
                "old_error_signatures": [
                    "走到尽头时，石壁上裂开一道窄缝，空气中带着石头和尘土的气味。"
                ],
            }
        },
    )

    blueprint = build_revision_blueprint("case", [order])

    assert blueprint.work_units == []
    assert blueprint.status == "empty"


def test_patch_merger_deduplicates_identical_surface_patches():
    patch_a = RepairPatch(
        patch_id="a",
        case_id="case",
        intent_id="intent-a",
        base_text_hash="hash",
        span=TextSpan(start=2, end=4),
        original_text="——",
        replacement_text="，",
    )
    patch_b = RepairPatch(
        patch_id="b",
        case_id="case",
        intent_id="intent-b",
        base_text_hash="hash",
        span=TextSpan(start=2, end=4),
        original_text="——",
        replacement_text="，",
    )

    result = PatchMerger().merge([patch_a, patch_b], "她——停住。")

    assert len(result["merged_patches"]) == 1
    assert result["conflicts"] == []


def test_failed_work_unit_group_expands_to_overlapping_patch_only():
    failed = RepairWorkUnit(work_unit_id="unit-failed")
    overlapping = RepairWorkUnit(work_unit_id="unit-overlapping")
    independent = RepairWorkUnit(work_unit_id="unit-independent")
    patches_by_unit = {
        "unit-failed": [RepairPatch(
            patch_id="failed-patch",
            case_id="case",
            intent_id="failed",
            base_text_hash="hash",
            span=TextSpan(start=2, end=6),
            original_text="2345",
            replacement_text="XX",
        )],
        "unit-overlapping": [RepairPatch(
            patch_id="overlap-patch",
            case_id="case",
            intent_id="overlap",
            base_text_hash="hash",
            span=TextSpan(start=5, end=8),
            original_text="567",
            replacement_text="YY",
        )],
        "unit-independent": [RepairPatch(
            patch_id="independent-patch",
            case_id="case",
            intent_id="independent",
            base_text_hash="hash",
            span=TextSpan(start=9, end=12),
            original_text="9ab",
            replacement_text="ZZ",
        )],
    }

    rejected = ChapterRepairExecutor._expand_failed_work_unit_group(
        {"unit-failed"},
        [failed, overlapping, independent],
        patches_by_unit,
    )

    assert rejected == {"unit-failed", "unit-overlapping"}


@pytest.mark.asyncio
async def test_chapter_repair_executor_applies_all_patches_against_frozen_base():
    text = "old phrase. stays here."
    replace = ToolCommand(
        command_id="replace",
        operation="replace_exact",
        scene_index=0,
        old_text="old phrase",
        target_span="old phrase",
        anchor_text="old phrase",
        new_text="new phrase",
        replacement="new phrase",
        source_issue_ids=["fact_conflict"],
    )
    insert = ToolCommand(
        command_id="insert",
        operation="insert_after_anchor",
        scene_index=0,
        anchor_text="old phrase.",
        after_span="old phrase.",
        new_text=" inserted beat.",
        replacement=" inserted beat.",
        source_issue_ids=["weak_curiosity_engine"],
    )
    plan = ChapterRepairPlan(
        case_id="case",
        status="needs_repair",
        orders=[
            ChapterRepairOrder(
                order_id="order-replace",
                owner_scene=0,
                target_scenes=[0],
                source_violation_ids=["fact_conflict"],
                repair_type="local_patch",
            ),
            ChapterRepairOrder(
                order_id="order-insert",
                owner_scene=0,
                target_scenes=[0],
                source_violation_ids=["weak_curiosity_engine"],
                repair_type="local_patch",
            ),
        ],
        work_units=[
            _unit("unit-replace", replace, "fact_conflict", "order-replace"),
            _unit("unit-insert", insert, "weak_curiosity_engine", "order-insert"),
        ],
    )

    updated, updated_plan = await ChapterRepairExecutor().execute(
        plan,
        {0: text},
        context={"case_id": "case"},
        case_file={},
    )

    assert updated[0] == "new phrase. inserted beat. stays here."
    assert {order.status for order in updated_plan.orders} == {"succeeded"}
    assert updated_plan.repair_execution_summary["tool_execution"]["frozen_base_patch_chain"] is True
    assert updated_plan.repair_execution_summary["tool_execution"]["patches_applied"] == 2


@pytest.mark.asyncio
async def test_chapter_repair_executor_replays_independent_peer_when_postcheck_fails():
    text = "Alpha. Beta."
    first = ToolCommand(
        command_id="replace-alpha",
        operation="replace_exact",
        scene_index=0,
        old_text="Alpha",
        target_span="Alpha",
        anchor_text="Alpha",
        span_start=0,
        span_end=5,
        new_text="Gamma",
        replacement="Gamma",
        source_issue_ids=["issue-alpha"],
    )
    second = ToolCommand(
        command_id="replace-beta",
        operation="replace_exact",
        scene_index=0,
        old_text="Beta",
        target_span="Beta",
        anchor_text="Beta",
        span_start=7,
        span_end=11,
        new_text="Delta",
        replacement="Delta",
        source_issue_ids=["issue-beta"],
        postconditions={"required_spans": ["a phrase that is absent"]},
    )
    plan = ChapterRepairPlan(
        case_id="case-rollback",
        status="needs_repair",
        orders=[
            ChapterRepairOrder(
                order_id="order-alpha",
                owner_scene=0,
                target_scenes=[0],
                source_violation_ids=["issue-alpha"],
                repair_type="local_patch",
            ),
            ChapterRepairOrder(
                order_id="order-beta",
                owner_scene=0,
                target_scenes=[0],
                source_violation_ids=["issue-beta"],
                repair_type="local_patch",
            ),
        ],
        work_units=[
            _unit("unit-alpha", first, "issue-alpha", "order-alpha"),
            _unit("unit-beta", second, "issue-beta", "order-beta"),
        ],
    )

    updated, updated_plan = await ChapterRepairExecutor().execute(
        plan,
        {0: text},
        context={"case_id": "case-rollback"},
        case_file={},
    )

    assert updated[0] == "Gamma. Beta."
    orders = {order.order_id: order for order in updated_plan.orders}
    assert orders["order-alpha"].status == "succeeded"
    assert orders["order-beta"].status == "failed"
    tool_failure = orders["order-beta"].repair_audit["tool_failures"][-1]
    assert tool_failure["work_unit_id"] == "unit-beta"
    assert tool_failure["failure_evidence"]["failed_spans"][0]["before_text"] == "Beta"
    units = {unit.work_unit_id: unit for unit in updated_plan.work_units}
    assert units["unit-alpha"].status == "succeeded"
    assert units["unit-alpha"].result_audit["replayed_after_peer_failure"] is True
    assert units["unit-beta"].status == "failed"
    assert units["unit-beta"].result_audit["reason"] == "work_unit_postcheck_failed"
    evidence = units["unit-beta"].result_audit["failure_evidence"]
    assert evidence["scene_index"] == 0
    assert evidence["failed_spans"] == [{
        "patch_id": evidence["failed_spans"][0]["patch_id"],
        "start": 7,
        "end": 11,
        "before_text": "Beta",
        "after_text": "Delta",
    }]
    isolation = updated_plan.repair_execution_summary["tool_execution"]["postcheck_isolation"][0]
    assert isolation["rolled_back_unit_ids"] == ["unit-beta"]
    assert isolation["retained_unit_ids"] == ["unit-alpha"]


@pytest.mark.asyncio
async def test_chapter_repair_executor_rolls_back_failed_unit_and_dependent_peer():
    text = "Alpha. Beta."
    dependent = _unit(
        "unit-dependent",
        ToolCommand(
            command_id="replace-alpha",
            operation="replace_exact",
            scene_index=0,
            old_text="Alpha",
            target_span="Alpha",
            anchor_text="Alpha",
            span_start=0,
            span_end=5,
            new_text="Gamma",
            replacement="Gamma",
            source_issue_ids=["issue-alpha"],
        ),
        "issue-alpha",
        "order-alpha",
    )
    dependent.dependencies = ["unit-trigger"]
    trigger = _unit(
        "unit-trigger",
        ToolCommand(
            command_id="replace-beta",
            operation="replace_exact",
            scene_index=0,
            old_text="Beta",
            target_span="Beta",
            anchor_text="Beta",
            span_start=7,
            span_end=11,
            new_text="Delta",
            replacement="Delta",
            source_issue_ids=["issue-beta"],
            postconditions={"required_spans": ["a phrase that is absent"]},
        ),
        "issue-beta",
        "order-beta",
    )
    plan = ChapterRepairPlan(
        case_id="case-dependent-rollback",
        status="needs_repair",
        orders=[
            ChapterRepairOrder(
                order_id="order-alpha",
                owner_scene=0,
                target_scenes=[0],
                source_violation_ids=["issue-alpha"],
                repair_type="local_patch",
            ),
            ChapterRepairOrder(
                order_id="order-beta",
                owner_scene=0,
                target_scenes=[0],
                source_violation_ids=["issue-beta"],
                repair_type="local_patch",
            ),
        ],
        work_units=[dependent, trigger],
    )

    updated, updated_plan = await ChapterRepairExecutor().execute(
        plan,
        {0: text},
        context={"case_id": "case-dependent-rollback"},
        case_file={},
    )

    assert updated[0] == text
    units = {unit.work_unit_id: unit for unit in updated_plan.work_units}
    assert units["unit-trigger"].result_audit["reason"] == "work_unit_postcheck_failed"
    assert (
        units["unit-dependent"].result_audit["reason"]
        == "dependent_or_overlapping_work_unit_rollback"
    )
    scope = units["unit-dependent"].result_audit["rollback_scope"]
    assert scope["rolled_back_work_unit_ids"] == ["unit-dependent", "unit-trigger"]


@pytest.mark.asyncio
async def test_chapter_repair_executor_keeps_explicit_edit_window_atomic():
    text = "Alpha. Beta."
    peer = _unit(
        "unit-window-peer",
        ToolCommand(
            command_id="replace-alpha-window",
            operation="replace_exact",
            scene_index=0,
            old_text="Alpha",
            target_span="Alpha",
            span_start=0,
            span_end=5,
            new_text="Gamma",
            replacement="Gamma",
            source_issue_ids=["issue-alpha"],
        ),
        "issue-alpha",
        "order-alpha",
    )
    trigger = _unit(
        "unit-window-trigger",
        ToolCommand(
            command_id="replace-beta-window",
            operation="replace_exact",
            scene_index=0,
            old_text="Beta",
            target_span="Beta",
            span_start=7,
            span_end=11,
            new_text="Delta",
            replacement="Delta",
            source_issue_ids=["issue-beta"],
            postconditions={"required_spans": ["a phrase that is absent"]},
        ),
        "issue-beta",
        "order-beta",
    )
    peer.edit_window_id = "shared-window"
    trigger.edit_window_id = "shared-window"
    plan = ChapterRepairPlan(
        case_id="case-window-rollback",
        status="needs_repair",
        orders=[
            ChapterRepairOrder(
                order_id="order-alpha",
                owner_scene=0,
                target_scenes=[0],
                source_violation_ids=["issue-alpha"],
                repair_type="local_patch",
            ),
            ChapterRepairOrder(
                order_id="order-beta",
                owner_scene=0,
                target_scenes=[0],
                source_violation_ids=["issue-beta"],
                repair_type="local_patch",
            ),
        ],
        work_units=[peer, trigger],
    )

    updated, updated_plan = await ChapterRepairExecutor().execute(
        plan,
        {0: text},
        context={"case_id": "case-window-rollback"},
        case_file={},
    )

    assert updated[0] == text
    isolation = updated_plan.repair_execution_summary["tool_execution"]["postcheck_isolation"][0]
    assert isolation["rolled_back_unit_ids"] == [
        "unit-window-peer",
        "unit-window-trigger",
    ]


def test_diagnostic_instruction_leak_guard_is_order_evidence_based():
    instruction = "请在这一段中增加一个具体动作，并保持人物视角一致。"
    order = ChapterRepairOrder(
        order_id="order-diagnostic-leak",
        owner_scene=0,
        target_scenes=[0],
        violation_details=[{"expected_behavior": instruction}],
        repair_type="local_patch",
    )

    assert _new_diagnostic_instruction_leaks(
        order,
        "她停在门边。",
        f"她停在门边。{instruction}",
    ) == [instruction]
    # 只拒绝本次新增的控制面文本，不使用题材/词汇黑名单误伤原文。
    assert not _new_diagnostic_instruction_leaks(
        order,
        f"引用中原本已有：{instruction}",
        f"引用中原本已有：{instruction}他合上书。",
    )
