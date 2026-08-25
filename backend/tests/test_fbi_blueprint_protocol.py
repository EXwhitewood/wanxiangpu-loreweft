import asyncio

from app.agents.fbi.review_blueprint_agent import FBIReviewBlueprintAgent
from app.models.chapter_review import (
    ChapterRepairOrder,
    ChapterRepairPlan,
    RepairWorkUnit,
    RevisionBlueprint,
    ToolCommand,
    ToolCommandBatch,
)
from app.services.fbi.blueprint_protocol_validator import BlueprintProtocolValidator
from app.services.fbi.chapter_repair_executor import ChapterRepairExecutor


def test_blueprint_protocol_accepts_bounded_replace_command():
    unit = RepairWorkUnit(
        work_unit_id="wu-valid",
        owner_scene=0,
        target_scenes=[0],
        tool_batch=ToolCommandBatch(
            commands=[
                ToolCommand(
                        command_id="cmd-valid",
                        operation="replace_exact",
                        scene_index=0,
                        target_span="old herb",
                        replacement="new herb",
                        anchor_text="old herb",
                        old_text="old herb",
                        new_text="new herb",
                        source_issue_ids=["issue-valid"],
                        expected_metric_delta={"direction": "resolve"},
                        guards={"preserve_facts": True},
                    )
                ],
            ),
    )

    result = BlueprintProtocolValidator().validate_work_units([unit])

    assert result["accepted"] is True
    assert result["failed_work_units"] == 0


def test_blueprint_protocol_rejects_missing_replacement():
    unit = RepairWorkUnit(
        work_unit_id="wu-invalid",
        owner_scene=0,
        target_scenes=[0],
        source_order_ids=["order-invalid"],
        tool_batch=ToolCommandBatch(
            commands=[
                ToolCommand(
                        command_id="cmd-invalid",
                        operation="replace_exact",
                        scene_index=0,
                        target_span="old herb",
                        anchor_text="old herb",
                        old_text="old herb",
                        source_issue_ids=["issue-invalid"],
                        expected_metric_delta={"direction": "resolve"},
                        guards={"preserve_facts": True},
                    )
                ],
            ),
    )

    result = BlueprintProtocolValidator().validate_work_units([unit])

    assert result["accepted"] is False
    assert result["invalid_work_unit_ids"] == ["wu-invalid"]
    assert "missing_new_text" in result["failures"]


def test_executor_rejects_invalid_work_unit_before_tool_execution():
    order = ChapterRepairOrder(
        order_id="order-invalid-blueprint",
        target_scenes=[0],
        owner_scene=0,
        repair_brief={
            "tool_commands": [
                {
                    "operation": "replace_span",
                    "target_span": "old herb",
                }
            ],
        },
    )
    plan = ChapterRepairPlan(
        case_id="case-invalid-blueprint",
        status="needs_repair",
        orders=[order],
    )

    updated, updated_plan = asyncio.run(
        ChapterRepairExecutor().execute(
            plan,
            {0: "old herb remains."},
            context={},
        )
    )

    assert updated[0] == "old herb remains."
    assert updated_plan.orders[0].status == "failed"
    assert updated_plan.orders[0].repair_audit["reason"] == "needs_blueprint_completion"
    assert updated_plan.repair_execution_summary["blueprint_protocol"]["accepted"] is True
    assert updated_plan.repair_execution_summary["blueprint_coverage"]["blueprint_completion_required_count"] == 1
    assert updated_plan.repair_execution_summary["tool_execution"]["legacy_executor_allowed"] is False


def test_executor_default_does_not_fallback_to_legacy_llm_executor():
    order = ChapterRepairOrder(
        order_id="order-unstructured-default",
        target_scenes=[0],
        owner_scene=0,
        repair_type="scene_rewrite",
        reason="Unbounded rewrite request.",
    )
    plan = ChapterRepairPlan(
        case_id="case-default-no-legacy",
        status="needs_repair",
        orders=[order],
    )

    updated, updated_plan = asyncio.run(
        ChapterRepairExecutor().execute(
            plan,
            {0: "Original text."},
            context={},
        )
    )

    assert updated[0] == "Original text."
    assert updated_plan.orders[0].status == "failed"
    assert updated_plan.orders[0].repair_audit["reason"] == "needs_blueprint_completion"
    assert updated_plan.repair_execution_summary["tool_execution"]["legacy_executor_allowed"] is False


def test_blueprint_agent_validates_revision_blueprint_without_mutating_text():
    blueprint = RevisionBlueprint(
        blueprint_id="bp",
        case_id="case",
        status="ready",
        work_units=[
            RepairWorkUnit(
                work_unit_id="wu",
                owner_scene=0,
                target_scenes=[0],
                tool_batch=ToolCommandBatch(
                    commands=[
                        ToolCommand(
                                command_id="cmd",
                                operation="replace_exact",
                                scene_index=0,
                                target_span="old",
                                replacement="new",
                                anchor_text="old",
                                old_text="old",
                                new_text="new",
                                span_start=0,
                                span_end=3,
                                source_issue_ids=["issue"],
                                expected_metric_delta={"direction": "resolve"},
                                guards={"preserve_facts": True},
                            )
                        ],
                    ),
            )
        ],
    )

    result = asyncio.run(
        FBIReviewBlueprintAgent().execute({"revision_blueprint": blueprint})
    )

    assert result["status"] == "ready"
    assert result["mutates_text"] is False
    assert result["protocol_validation"]["accepted"] is True
    assert "anti_ai_prose" in result["mounted_skills"]
