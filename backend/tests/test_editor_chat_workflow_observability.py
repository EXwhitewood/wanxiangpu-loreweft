from types import SimpleNamespace
import asyncio
import pytest

from app.api.editor_chat import (
    _await_workflow_operation,
    _final_acceptance_cycle_progress,
    _final_acceptance_failure_summary,
    _final_acceptance_repair_budget,
    _is_blocking_repair_order_failure,
    _quality_policy_enforcement_summary,
    _repair_order_failure_scope,
    _repair_order_failure_disposition,
)


@pytest.mark.asyncio
async def test_workflow_operation_timeout_does_not_leave_indefinite_wait():
    async def slow_operation():
        await asyncio.sleep(1)
        return "late"

    with pytest.raises(TimeoutError):
        await _await_workflow_operation(
            "wf-test",
            "chapter_writer",
            slow_operation(),
            timeout_seconds=0.01,
        )


def test_quality_policy_enforcement_summary_extracts_packet_provenance():
    packet = SimpleNamespace(
        scene_index=1,
        quality_gate_report={
            "reports": {
                "quality_policy_enforcement": {
                    "enforced_count": 1,
                    "violations": [
                        {
                            "type": "low_conflict_density",
                            "severity": "high",
                            "source": "quality_policy_enforcement",
                            "detail": "Repeated quality memory candidate matched current advisory.",
                            "expected_behavior": "Raise scene tension.",
                            "evidence": {
                                "enforcement_reason": "quality_memory_candidate_matched_current_advisory",
                                "quality_policy_candidate": {
                                    "type": "low_conflict_density_trend",
                                    "level": "quality_gate_candidate",
                                    "count": 5,
                                },
                                "advisory": {
                                    "type": "low_conflict_density",
                                    "severity": "high",
                                },
                            },
                        }
                    ],
                }
            }
        },
    )

    summary = _quality_policy_enforcement_summary([packet])

    assert summary["enforced_count"] == 1
    assert summary["samples"] == [
        {
            "scene_index": 1,
            "type": "low_conflict_density",
            "severity": "high",
            "source": "quality_policy_enforcement",
            "detail": "Repeated quality memory candidate matched current advisory.",
            "candidate_type": "low_conflict_density_trend",
            "candidate_level": "quality_gate_candidate",
            "candidate_count": 5,
            "advisory_type": "low_conflict_density",
            "advisory_severity": "high",
            "enforcement_reason": "quality_memory_candidate_matched_current_advisory",
            "expected_behavior": "Raise scene tension.",
        }
    ]


def test_recheck_auto_repair_accepts_audited_skipped_orders():
    assert _is_blocking_repair_order_failure(SimpleNamespace(status="failed", repair_audit={})) is True
    assert _is_blocking_repair_order_failure(SimpleNamespace(status="skipped", repair_audit={})) is True
    assert _is_blocking_repair_order_failure(
        SimpleNamespace(status="skipped", repair_audit={"accepted": True})
    ) is False
    assert _is_blocking_repair_order_failure(SimpleNamespace(status="succeeded", repair_audit={})) is False
    assert _is_blocking_repair_order_failure(
        SimpleNamespace(
            status="failed",
            repair_type="local_patch",
            repair_audit={"failures": ["protected_obligation_removed"]},
            violation_details=[{
                "type": "low_conflict_density",
                "severity": "medium",
                "blocks_commit": True,
            }],
        )
    ) is False
    assert _repair_order_failure_disposition(
        SimpleNamespace(
            status="failed",
            repair_type="local_patch",
            repair_audit={"failures": ["protected_obligation_removed"]},
            violation_details=[{
                "type": "fact_conflict",
                "severity": "high",
                "blocks_commit": True,
            }],
        )
    ) == "recoverable_delta"
    assert _is_blocking_repair_order_failure(
        SimpleNamespace(
            status="failed",
            repair_type="local_patch",
            repair_audit={"failures": ["protected_obligation_removed"]},
            violation_details=[{
                "type": "fact_conflict",
                "severity": "high",
                "blocks_commit": True,
            }],
        )
    ) is False
    assert _repair_order_failure_disposition(
        SimpleNamespace(
            status="failed",
            repair_type="cross_scene_alignment",
            repair_audit={"failures": ["unchanged_result"]},
            violation_details=[{
                "type": "fact_conflict",
                "severity": "high",
                "blocks_commit": True,
            }],
        )
    ) == "recoverable_delta"
    assert _is_blocking_repair_order_failure(
        SimpleNamespace(
            status="failed",
            repair_type="contract_patch",
            repair_audit={},
            violation_details=[{"type": "contract_field_conflict", "severity": "high"}],
        )
    ) is True
    assert _repair_order_failure_disposition(
        SimpleNamespace(
            status="failed",
            repair_type="local_patch",
            repair_audit={"failures": ["failed_required_dependencies"]},
            violation_details=[{"type": "low_conflict_density", "severity": "medium"}],
        )
    ) == "recoverable_delta"
    assert _repair_order_failure_disposition(
        SimpleNamespace(
            status="failed",
            repair_type="local_patch",
            repair_audit={
                "failures": ["needs_blueprint_completion"],
                "tool_failures": [{
                    "reason": "work_unit_postcheck_failed",
                    "failures": ["target_metric_not_improved"],
                    "failure_evidence": {"failed_spans": []},
                }],
            },
            violation_details=[{
                "type": "ai_simile_overuse",
                "severity": "high",
                "blocks_commit": True,
            }],
        )
    ) == "recoverable_delta"
    assert _repair_order_failure_disposition(
        SimpleNamespace(
            status="failed",
            repair_type="local_patch",
            repair_audit={"failures": ["missing_target_scene"]},
            violation_details=[{"type": "low_conflict_density", "severity": "medium"}],
        )
    ) == "recoverable_delta"


def test_repair_failure_scope_uses_only_failed_goal_subset():
    order = SimpleNamespace(
        repair_brief={
            "repair_goals": [
                {
                    "goal_id": "goal-hard",
                    "source_issue_ids": ["issue-hard"],
                    "blocks_commit": True,
                },
                {
                    "goal_id": "goal-advisory",
                    "source_issue_ids": ["issue-advisory"],
                    "blocks_commit": False,
                },
            ]
        },
        repair_audit={
            "failures": ["needs_blueprint_completion"],
            "tool_failures": [{
                "reason": "goal_acceptance_failed",
                "failures": ["goal_acceptance_failed"],
                "failed_covered_goal_ids": ["goal-advisory"],
                "failed_covered_issue_ids": ["issue-advisory"],
            }],
        },
        repair_type="local_patch",
        status="failed",
        violation_details=[
            {
                "issue_id": "issue-hard",
                "type": "fact_conflict",
                "severity": "high",
                "blocks_commit": True,
            },
            {
                "issue_id": "issue-advisory",
                "type": "low_conflict_density",
                "severity": "medium",
                "blocks_commit": False,
            },
        ],
    )

    scope = _repair_order_failure_scope(order)

    assert scope["failed_goal_ids"] == ["goal-advisory"]
    assert scope["failed_issue_ids"] == ["issue-advisory"]
    assert [item["issue_id"] for item in scope["violation_details"]] == ["issue-advisory"]
    assert scope["scoped"] is True
    assert _repair_order_failure_disposition(order) == "recoverable_delta"


def test_final_acceptance_cycle_progress_detects_reduced_failure_count():
    before = _final_acceptance_failure_summary({
        "allowed": False,
        "trace": {
            "initial_validation": {
                "failures": [
                    {
                        "validator": "ai_flavor",
                        "metric": "dash_per_1000",
                        "severity": "high",
                    },
                    {
                        "validator": "contract",
                        "metric": "contract_field_conflict",
                        "repair_domain": "contract",
                        "severity": "high",
                    },
                ]
            }
        },
    })
    after = _final_acceptance_failure_summary({
        "allowed": False,
        "trace": {
            "initial_validation": {
                "failures": [
                    {
                        "validator": "ai_flavor",
                        "metric": "dash_per_1000",
                        "severity": "high",
                    }
                ]
            }
        },
    })

    progress = _final_acceptance_cycle_progress(before, after)

    assert progress["has_progress"] is True
    assert progress["blocking_failure_delta"] == -1
    assert progress["critical_high_contract_delta"] == -1


def test_final_acceptance_cycle_progress_rejects_unchanged_failures():
    before = _final_acceptance_failure_summary({
        "allowed": False,
        "trace": {
            "initial_validation": {
                "failures": [
                    {
                        "validator": "ai_flavor",
                        "metric": "dash_per_1000",
                        "severity": "high",
                    }
                ]
            }
        },
    }, content_blocking_count=1)
    after = _final_acceptance_failure_summary({
        "allowed": False,
        "trace": {
            "initial_validation": {
                "failures": [
                    {
                        "validator": "ai_flavor",
                        "metric": "dash_per_1000",
                        "severity": "high",
                    }
                ]
            }
        },
    }, content_blocking_count=1)

    progress = _final_acceptance_cycle_progress(before, after)

    assert progress["has_progress"] is False
    assert progress["blocking_failure_delta"] == 0
    assert progress["before_signature"] == progress["after_signature"]


def test_final_acceptance_budget_allows_two_cycles_for_localized_deterministic_failure():
    summary = _final_acceptance_failure_summary({
        "allowed": False,
        "trace": {
            "initial_validation": {
                "failures": [{
                    "type": "ai_punctuation_artifact",
                    "metric": "dash_per_1000",
                    "severity": "high",
                    "scene_index": 0,
                    "target_span": "——",
                }]
            }
        },
    })

    assert summary["localized_deterministic_count"] == 1
    assert _final_acceptance_repair_budget(summary) == 2


def test_final_acceptance_budget_limits_unlocalized_failure_to_one_cycle():
    summary = _final_acceptance_failure_summary({
        "allowed": False,
        "trace": {
            "initial_validation": {
                "failures": [{
                    "type": "literary_quality",
                    "metric": "overall_style",
                    "severity": "high",
                }]
            }
        },
    })

    assert summary["unlocalized_count"] == 1
    assert _final_acceptance_repair_budget(summary) == 1
