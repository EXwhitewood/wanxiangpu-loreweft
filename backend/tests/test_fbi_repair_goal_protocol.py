import asyncio

from app.agents.fbi.review_blueprint_agent import FBIReviewBlueprintAgent
from app.agents.scene_repairer import SceneRepairer
from app.models.chapter_review import (
    ChapterRepairOrder,
    ChapterReviewCase,
    RepairWorkUnit,
    SceneReviewPacket,
    ToolCommand,
    ToolCommandBatch,
)
from app.models.fbi_diagnosis import FBIRepairGoal, FBIReviewDiagnosisSet
from app.services.fbi.chapter_case_intake import FBIChapterRepairPlanner
from app.services.fbi.chapter_case_intake import FBIChapterCaseIntakeService
from app.services.fbi.chapter_repair_executor import ChapterRepairExecutor
from app.services.fbi.edit_window_planner import EditWindowPlanner
from app.services.fbi.repair_goal_grouper import RepairGoalGrouper
from app.services.fbi.review_minister import FBIReviewMinister


def _unit(
    unit_id: str,
    issue_id: str,
    anchor: str,
    replacement: str,
) -> RepairWorkUnit:
    command = ToolCommand(
        command_id=f"{unit_id}:command",
        operation="replace_exact",
        scene_index=0,
        target_span=anchor,
        old_text=anchor,
        new_text=replacement,
        replacement=replacement,
        anchor_text=anchor,
        source_issue_ids=[issue_id],
        repair_family="generic",
    )
    return RepairWorkUnit(
        work_unit_id=unit_id,
        owner_scene=0,
        target_scenes=[0],
        source_violation_ids=[issue_id],
        source_goal_ids=[f"goal-{issue_id}"],
        write_anchor={"text": anchor, "validated": True, "scene_index": 0},
        placement_status="resolved",
        tool_batch=ToolCommandBatch(
            batch_id=f"{unit_id}:batch",
            source_work_unit_id=unit_id,
            commands=[command],
            target_scenes=[0],
        ),
    )


def test_duplicate_findings_create_one_stable_repair_goal_and_diagnosis():
    text = "opening evidence\n\nclosing evidence"
    findings = [
        {
            "issue_id": "finding-a",
            "validator": "validator-a",
            "authority_ref": "contract:obligation-1",
            "owner_scene": 0,
            "mutation_kind": "insert",
            "desired_state": "required outcome is shown",
            "target_span": "opening evidence",
            "blocks_commit": True,
        },
        {
            "issue_id": "finding-b",
            "validator": "validator-b",
            "authority_ref": "contract:obligation-1",
            "owner_scene": 0,
            "mutation_kind": "insert",
            "desired_state": "required outcome is shown",
            "target_span": "closing evidence",
            "blocks_commit": True,
        },
    ]
    case = ChapterReviewCase(
        case_id="case-goal",
        project_id="project",
        chapter_number=1,
        scene_packets=[SceneReviewPacket(scene_index=0, candidate_text=text)],
    )

    first = FBIReviewMinister().diagnose_violations(case, findings)
    second = FBIReviewMinister().diagnose_violations(case, list(reversed(findings)))

    assert len(first.repair_goals) == 1
    assert len(first.diagnoses) == 1
    assert set(first.diagnoses[0].issue_ids) == {"finding-a", "finding-b"}
    assert first.repair_goals[0].goal_id == second.repair_goals[0].goal_id
    assert first.diagnoses[0].diagnosis_id == second.diagnoses[0].diagnosis_id
    assert first.diagnoses[0].placement.status == "required"
    assert first.diagnoses[0].placement.write_anchor == {}


def test_conflicting_validated_anchors_require_fresh_placement():
    text = "alpha paragraph\n\nbeta paragraph"
    findings = [
        {
            "issue_id": "finding-alpha",
            "authority_ref": "contract:shared-goal",
            "source_scene": 0,
            "mutation_kind": "insert",
            "desired_state": "show the shared outcome",
            "write_anchor": {
                "scene_index": 0,
                "text": "alpha paragraph",
                "validated": True,
            },
        },
        {
            "issue_id": "finding-beta",
            "authority_ref": "contract:shared-goal",
            "source_scene": 0,
            "mutation_kind": "insert",
            "desired_state": "show the shared outcome",
            "write_anchor": {
                "scene_index": 0,
                "text": "beta paragraph",
                "validated": True,
            },
        },
    ]
    goals, _ = RepairGoalGrouper().group_findings(findings)

    placement = RepairGoalGrouper().placement_for_goal(goals[0], {0: text})

    assert placement.status == "required"
    assert placement.write_anchor == {}
    assert placement.reason == "conflicting_validated_write_anchors_require_placement"


def test_same_sentence_duplicate_goal_can_use_protocol_fast_path():
    findings = [
        {
            "issue_id": issue_id,
            "type": "missing_must_show",
            "metric": "missing_must_show",
            "authority_ref": "contract:same-sentence-goal",
            "source_scene": 0,
            "paragraph_index": 0,
            "sentence_index": 0,
            "mutation_kind": "insert",
            "desired_state": "show the required beat",
            "blocks_commit": True,
        }
        for issue_id in ("finding-a", "finding-b")
    ]
    case = ChapterReviewCase(
        case_id="case-fast-path",
        project_id="project",
        chapter_number=1,
        scene_packets=[SceneReviewPacket(scene_index=0, candidate_text="scene text")],
    )
    annotated, diagnosis_set = FBIReviewMinister().annotate_violations(case, findings)

    assert FBIReviewBlueprintAgent()._can_skip_llm_session(
        annotated,
        diagnosis_set,
    ) is True


def test_duplicate_missing_content_findings_plan_one_creative_work_unit():
    text = "opening beat\n\nintermediate beat\n\nending beat"
    findings = [
        {
            "issue_id": "validator-a-finding",
            "type": "missing_must_show",
            "metric": "missing_must_show",
            "validator": "validator-a",
            "authority_ref": "contract:required-outcome",
            "source_scene": 0,
            "mutation_kind": "insert",
            "desired_state": "the required outcome is made observable",
            "target_span": "opening beat",
            "blocks_commit": True,
        },
        {
            "issue_id": "validator-b-finding",
            "type": "ending_state_not_reached",
            "metric": "ending_state_not_reached",
            "validator": "validator-b",
            "authority_ref": "contract:required-outcome",
            "source_scene": 0,
            "mutation_kind": "insert",
            "desired_state": "the required outcome is made observable",
            "target_span": "ending beat",
            "blocks_commit": True,
        },
    ]
    case = ChapterReviewCase(
        case_id="case-missing-content",
        project_id="project",
        chapter_number=1,
        scene_packets=[SceneReviewPacket(
            scene_index=0,
            candidate_text=text,
            repairability="auto_fixable",
            blocking_violations=findings,
        )],
    )

    validated = asyncio.run(FBIChapterCaseIntakeService().intake(case))
    plan = asyncio.run(FBIChapterRepairPlanner().plan(validated))

    assert len(plan.repair_goals) == 1
    assert len(plan.work_units) == 1
    assert len(plan.orders) == 1
    unit = plan.work_units[0]
    assert unit.placement_status == "required"
    assert unit.write_anchor == {}
    assert unit.tool_batch.commands[0].operation == "llm_creative_rewrite"
    assert unit.tool_batch.commands[0].target_span == ""
    assert set(plan.orders[0].source_violation_ids) == {
        "validator-a-finding",
        "validator-b-finding",
    }


def test_repair_goal_grouper_preserves_distinct_owner_scopes():
    findings = [
        {
            "issue_id": "shared-id",
            "authority_ref": "contract:shared",
            "owner_scene": scene,
            "mutation_kind": "insert",
            "desired_state": "required state",
        }
        for scene in (0, 1)
    ]
    goals, _ = RepairGoalGrouper().group_findings(findings)
    assert len(goals) == 2
    assert {goal.owner_scope.get("scene_index") for goal in goals} == {0, 1}


def test_edit_window_grouping_uses_validated_write_anchor_only():
    text = "alpha paragraph\n\nbeta paragraph"
    planner = EditWindowPlanner()

    distinct, _ = planner.plan_work_units(
        [_unit("unit-a", "issue-a", "alpha", "A"), _unit("unit-b", "issue-b", "beta", "B")],
        {0: text},
        case_id="case-window",
    )
    assert len(distinct) == 2

    shared, _ = planner.plan_work_units(
        [_unit("unit-c", "issue-c", "alpha", "A1"), _unit("unit-d", "issue-d", "alpha", "A2")],
        {0: text},
        case_id="case-window",
    )
    assert len(shared) == 1
    assert set(shared[0].source_violation_ids) == {"issue-c", "issue-d"}


def test_edit_window_with_creative_goal_becomes_one_creative_unit():
    text = "alpha paragraph\n\nbeta paragraph"
    deterministic = _unit("det-unit", "issue-det", "alpha", "A")
    deterministic.source_goal_ids = ["goal-det"]
    creative = _unit("creative-unit", "issue-creative", "alpha", "unused")
    creative.source_goal_ids = ["goal-creative"]
    creative.tool_batch.commands[0].operation = "llm_creative_rewrite"

    planned, _ = EditWindowPlanner().plan_work_units(
        [deterministic, creative],
        {0: text},
        case_id="case-creative-window",
    )

    assert len(planned) == 1
    assert set(planned[0].source_goal_ids) == {"goal-det", "goal-creative"}
    assert set(planned[0].source_violation_ids) == {"issue-det", "issue-creative"}
    assert [command.operation for command in planned[0].tool_batch.commands] == [
        "llm_creative_rewrite"
    ]


def test_canonical_projection_is_one_order_per_work_unit():
    orders = [
        ChapterRepairOrder(
            order_id=f"projection-{index}",
            owner_scene=0,
            target_scenes=[0],
            source_violation_ids=[f"finding-{index}"],
            source_goal_ids=["goal-shared"],
            violation_details=[{"issue_id": f"finding-{index}", "blocks_commit": True}],
        )
        for index in range(3)
    ]
    unit = _unit("atomic-unit", "finding-0", "alpha", "A")
    unit.source_order_ids = [order.order_id for order in orders]
    unit.source_violation_ids = [f"finding-{index}" for index in range(3)]
    unit.source_goal_ids = ["goal-shared"]

    canonical, trace = FBIChapterRepairPlanner._canonicalize_orders_for_work_units(
        orders,
        [unit],
    )

    assert len(canonical) == 1
    assert trace["executable_order_count"] == trace["executable_work_unit_count"] == 1
    assert unit.source_order_ids == [canonical[0].order_id]
    assert len(canonical[0].violation_details) == 3


def test_same_goal_work_units_are_coalesced_before_window_planning():
    deterministic = _unit("det-unit", "finding-a", "alpha", "A")
    deterministic.source_goal_ids = ["goal-shared"]
    creative = _unit("creative-unit", "finding-b", "beta", "B")
    creative.source_goal_ids = ["goal-shared"]
    creative.tool_batch.commands[0].operation = "llm_creative_rewrite"

    units, trace = FBIChapterRepairPlanner._coalesce_goal_work_units(
        [deterministic, creative]
    )

    assert len(units) == 1
    assert trace["invariant_satisfied"] is True
    assert units[0].source_goal_ids == ["goal-shared"]
    assert set(units[0].source_violation_ids) == {"finding-a", "finding-b"}
    assert [command.operation for command in units[0].tool_batch.commands] == [
        "llm_creative_rewrite"
    ]


def test_global_goal_expansion_crops_scene_owned_protocol_spans():
    command = ToolCommand(
        command_id="global:command",
        operation="llm_creative_rewrite",
        source_issue_ids=["finding-0", "finding-1"],
    )
    unit = RepairWorkUnit(
        work_unit_id="global-unit",
        source_goal_ids=["goal-global"],
        source_order_ids=["global-order"],
        source_violation_ids=["finding-0", "finding-1"],
        target_scenes=[0, 1],
        read_context_spans=[
            {"scene_index": 0, "start": 0, "end": 10},
            {"scene_index": 1, "start": 0, "end": 20},
        ],
        diagnostic_evidence_spans=[
            {"scene_index": 0, "text": "evidence-0"},
            {"scene_index": 1, "text": "evidence-1"},
        ],
        tool_batch=ToolCommandBatch(
            batch_id="global:batch",
            commands=[command],
            source_order_ids=["global-order"],
            target_scenes=[0, 1],
        ),
    )
    diagnosis_set = FBIReviewDiagnosisSet(
        repair_goals=[FBIRepairGoal(
            goal_id="goal-global",
            owner_scope={"scope": "chapter", "scene_indices": [0, 1]},
            mutation_kind="global_transform",
            source_issue_ids=["finding-0", "finding-1"],
            source_findings=[
                {"issue_id": "finding-0", "source_scene": 0},
                {"issue_id": "finding-1", "source_scene": 1},
            ],
        )]
    )
    order = ChapterRepairOrder(
        order_id="global-order",
        source_goal_ids=["goal-global"],
    )

    expanded, trace = FBIChapterRepairPlanner._expand_global_goal_work_units(
        [unit],
        diagnosis_set,
        scene_indices=[0, 1],
        orders=[order],
    )

    assert len(expanded) == 2
    assert trace["output_work_units"] == 2
    for child in expanded:
        assert child.target_scenes == [child.owner_scene]
        assert {span["scene_index"] for span in child.read_context_spans} == {
            child.owner_scene
        }
        assert {
            span["scene_index"] for span in child.diagnostic_evidence_spans
        } == {child.owner_scene}
        assert child.tool_batch.commands[0].source_issue_ids == child.source_violation_ids


def test_core_context_tail_is_not_silently_truncated():
    sentinel = "TAIL_SENTINEL_MUST_SURVIVE"
    long_scene = "x" * 26000 + sentinel
    scene_prompt = SceneRepairer()._build_patch_user_prompt(
        {"chapter_scene_context": long_scene, "repair_brief": {}},
        "draft",
        ["repair target"],
        [],
        [],
        True,
        {},
    )
    assert sentinel in scene_prompt

    case = ChapterReviewCase(
        case_id="case-context",
        project_id="project",
        chapter_number=1,
        scene_packets=[SceneReviewPacket(scene_index=0, candidate_text=long_scene)],
    )
    blueprint_prompt = FBIReviewBlueprintAgent()._build_session_user_prompt(
        {"case": case, "scene_texts": {0: long_scene}},
        [],
        FBIReviewDiagnosisSet(case_id="case-context"),
    )
    assert sentinel in blueprint_prompt


def test_hard_unsupported_acceptance_fails_closed():
    order = ChapterRepairOrder(
        order_id="hard-order",
        owner_scene=0,
        target_scenes=[0],
        repair_type="local_patch",
        violation_details=[{
            "issue_id": "hard-finding",
            "metric": "unregistered_hard_metric",
            "blocks_commit": True,
        }],
        repair_brief={
            "target_metrics": [{"metric": "unregistered_hard_metric"}],
            "repair_goals": [{
                "goal_id": "hard-goal",
                "authority_ref": "contract:hard",
                "blocks_commit": True,
                "acceptance_criteria": [{
                    "metric": "unregistered_hard_metric",
                    "operator": "resolved",
                }],
            }],
        },
    )
    audit = ChapterRepairExecutor()._audit_repair_result(
        order,
        "before",
        "after",
        0,
        {"_structured_execution_required": True, "scene_contracts": {}},
        {},
    )
    assert audit["accepted"] is False
    assert "unsupported_target_acceptance" in audit["failures"]
    assert audit["goal_acceptance_checks"][0]["supported"] is False
    assert audit["goal_acceptance_checks"][0]["passed"] is False


def test_registered_semantic_contract_repair_defers_to_mandatory_recheck():
    order = ChapterRepairOrder(
        order_id="semantic-order",
        owner_scene=0,
        target_scenes=[0],
        repair_type="contract_completion_patch",
        violation_details=[{
            "issue_id": "missing-clue",
            "metric": "missing_must_show",
            "blocks_commit": True,
        }],
        repair_brief={
            "target_metrics": [{"metric": "missing_must_show"}],
            "repair_goals": [{
                "goal_id": "semantic-goal",
                "authority_ref": "scene_contract:0:clues[1].description",
                "blocks_commit": True,
                "acceptance_criteria": [{
                    "metric": "missing_must_show",
                    "operator": "resolved",
                }],
            }],
        },
    )

    audit = ChapterRepairExecutor()._audit_repair_result(
        order,
        "大师伯掌心里亮起暗红色的光。",
        "大师伯掌心和颈部浮现黑色纹路，暗红色的光从指缝溢出。",
        0,
        {"_structured_execution_required": True, "scene_contracts": {}},
        {},
    )

    assert audit["accepted"] is True
    assert audit["target_self_check"]["deferred_to_recheck"] is True
    assert audit["target_self_check"]["source"] == "mandatory_semantic_recheck"
    assert audit["goal_acceptance_checks"][0]["passed"] is True
    assert "unsupported_target_acceptance" not in audit["failures"]
    assert "unsupported_goal_acceptance" not in audit["failures"]


def test_creative_work_unit_bypasses_tool_executor_and_stays_atomic():
    command = ToolCommand(
        command_id="creative:command",
        operation="llm_creative_rewrite",
        scene_index=0,
        source_issue_ids=["finding-a", "finding-b"],
    )
    unit = RepairWorkUnit(
        work_unit_id="creative-unit",
        owner_scene=0,
        target_scenes=[0],
        source_order_ids=["creative-order"],
        source_violation_ids=["finding-a", "finding-b"],
        source_goal_ids=["goal-a"],
        tool_batch=ToolCommandBatch(
            batch_id="creative:batch",
            commands=[command],
            target_scenes=[0],
            source_order_ids=["creative-order"],
        ),
    )
    order = ChapterRepairOrder(
        order_id="creative-order",
        owner_scene=0,
        target_scenes=[0],
        source_violation_ids=["finding-a", "finding-b"],
    )
    summary = asyncio.run(ChapterRepairExecutor()._execute_tool_work_units(
        [unit],
        {0: "base text"},
        {order.order_id: order},
        context={},
        case_file={},
    ))
    assert summary["creative_work_units"] == 1
    assert summary["failed"] == 0
    assert unit.status == "pending"
    assert unit.result_audit["executor_path"] == "creative_work_unit_router"


def test_creative_order_has_one_candidate_and_no_hidden_protection_retry():
    order = ChapterRepairOrder(
        order_id="creative-order",
        repair_type="contract_completion_patch",
        candidate_attempt_budget=3,
        tool_commands=[{"operation": "llm_creative_rewrite"}],
    )
    executor = ChapterRepairExecutor()

    assert executor._candidate_attempt_budget(order) == 1
    assert executor._should_retry_for_protection(
        order,
        {
            "failures": ["protected_obligation_removed"],
            "removed_protected_terms": ["protected fact"],
        },
    ) is False


def test_creative_consistency_bridge_defers_to_mandatory_recheck():
    order = ChapterRepairOrder(
        order_id="creative-consistency-bridge",
        owner_scene=0,
        target_scenes=[0],
        repair_type="local_patch",
        tool_commands=[{
            "operation": "llm_creative_rewrite",
            "scene_index": 0,
        }],
        repair_brief={
            "fact_repair_goal": {
                "conflict_type": "missing_context_bridge",
                "required_entities": [],
                "forbidden_claims": [],
                "old_error_signatures": [],
            },
            "target_metrics": [{
                "metric": "internal_conflict",
                "direction": "resolve",
            }],
            "repair_goals": [{
                "goal_id": "bridge-goal",
                "blocks_commit": True,
                "acceptance_criteria": [{
                    "metric": "internal_conflict",
                    "operator": "resolve",
                }],
            }],
        },
    )

    audit = ChapterRepairExecutor()._audit_repair_result(
        order,
        "她认出了他们。\n\n你们是谁？",
        "她认出了他们。\n\n灵魂重塑带走了刚才的记忆。\n\n你们是谁？",
        0,
        {"_structured_execution_required": True},
        {},
    )

    assert audit["accepted"] is True
    assert audit["failures"] == []
    assert audit["target_self_check"]["source"] == "mandatory_semantic_recheck"
    assert audit["target_self_check"]["deferred_to_recheck"] is True
    assert audit["target_self_check"]["superseded_check"]["source"] == "fact_goal_missing"
    assert audit["goal_acceptance_checks"][0]["passed"] is True


def test_creative_workbench_postcondition_rejects_removed_user_preserve_span():
    order = ChapterRepairOrder(
        order_id="creative-preserve-target",
        owner_scene=0,
        target_scenes=[0],
        repair_type="local_patch",
        tool_commands=[{
            "operation": "llm_creative_rewrite",
            "scene_index": 0,
            "postconditions": {"required_spans": ["你们是谁？"]},
        }],
        repair_brief={
            "fact_repair_goal": {
                "conflict_type": "missing_context_bridge",
                "required_entities": [],
                "forbidden_claims": [],
                "old_error_signatures": [],
            },
            "target_metrics": [{"metric": "internal_conflict", "direction": "resolve"}],
        },
    )

    audit = ChapterRepairExecutor()._audit_repair_result(
        order,
        "你们是谁？",
        "她一一唤出了他们的名字。",
        0,
        {"_structured_execution_required": True},
        {},
    )

    assert audit["accepted"] is False
    assert "target_metric_not_improved" in audit["failures"]
    assert audit["target_self_check"]["source"] == "tool_command_postconditions"
    assert audit["target_self_check"]["checks"][0]["missing_required"] == ["你们是谁？"]
