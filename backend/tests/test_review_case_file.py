import pytest

from app.models.agent_skill import CompiledSkillPacket
from app.models.chapter_review import ChapterReviewCase, SceneReviewPacket
from app.services.fbi.chapter_case_intake import (
    FBIChapterCaseIntakeService,
    FBIChapterRepairPlanner,
)
from app.services.review_case_file import (
    ReviewCaseFileBuilder,
    case_file_issues_to_violations,
    combine_chapter_review_validations,
)
from app.services.fbi.final_acceptance import FBIFinalAcceptanceService
from app.services.fbi.final_acceptance_delta import (
    build_commit_health_case_delta,
    build_final_acceptance_case_delta,
)


def test_review_case_file_collects_scene_and_skill_issues():
    packet = SceneReviewPacket(
        scene_index=0,
        blocking_violations=[
            {
                "type": "ending_state_not_reached",
                "severity": "high",
                "detail": "ending state missing",
                "target_span": "ending_state",
            }
        ],
        advisory_violations=[
            {
                "type": "ai_punctuation_artifact",
                "severity": "medium",
                "detail": "dash density high",
                "target_span": "dash",
            }
        ],
    )
    skill_validation = {
        "passed": False,
        "failures": [
            {
                "skill_id": "anti_ai_prose",
                "validator": "ai_flavor",
                "metric": "dash_per_1000",
                "actual": 8.0,
                "expected": 2.5,
                "severity": "high",
            }
        ],
    }

    case_file = ReviewCaseFileBuilder().build(
        project_id="project",
        chapter_number=1,
        draft_text="draft",
        scene_packets=[packet],
        skill_validation=skill_validation,
    )

    assert case_file.case_id.startswith("rcf_")
    assert len(case_file.scene_issues) == 1
    assert len(case_file.quality_advisories) == 1
    assert len(case_file.skill_failures) == 1
    assert case_file.skill_failures[0].source == "skill_validator"
    assert case_file.skill_failures[0].repair_domain == "anti_ai"
    assert case_file.summary()["blocking_issues"] == 2


def test_review_case_file_dedupes_cross_source_anti_ai_punctuation_intent():
    packet = SceneReviewPacket(
        scene_index=0,
        advisory_violations=[
            {
                "type": "ai_punctuation_artifact",
                "severity": "medium",
                "detail": "dash artifact from scene review",
            }
        ],
    )
    skill_validation = {
        "passed": False,
        "failures": [
            {
                "skill_id": "anti_ai_prose",
                "validator": "ai_flavor",
                "metric": "dash_per_1000",
                "actual": 8.0,
                "expected": 2.5,
                "severity": "high",
                "scene_index": 0,
            }
        ],
    }

    case_file = ReviewCaseFileBuilder().build(
        project_id="project",
        chapter_number=1,
        draft_text="draft",
        scene_packets=[packet],
        skill_validation=skill_validation,
    )

    issues = case_file.all_issues()
    assert len(issues) == 1
    assert case_file.summary()["blocking_issues"] == 1
    assert case_file.skill_failures[0].repair_domain == "anti_ai"
    assert case_file.skill_failures[0].dedupe_key == "scene:0:anti_ai:punctuation_density"
    assert set(case_file.skill_failures[0].sources) == {"scene_review", "skill_validator"}


def test_review_case_file_preserves_manual_route_for_high_ai_advisories():
    case_file = ReviewCaseFileBuilder().build(
        project_id="project",
        chapter_number=1,
        draft_text="draft",
        skill_validation={
            "passed": False,
            "failures": [
                {
                    "skill_id": "anti_ai_prose",
                    "validator": "ai_flavor",
                    "metric": "high_advisories",
                    "type": "high_advisories",
                    "actual": 1,
                    "expected_max": 0,
                    "severity": "high",
                    "repairability": "human_review_required",
                    "issue_classification": "manual_only",
                    "blocks_commit": True,
                }
            ],
        },
    )

    issue = case_file.skill_failures[0]
    assert issue.repairability == "manual_review"
    assert case_file_issues_to_violations(case_file)[0]["repairability"] == "human_review_required"
    assert case_file_issues_to_violations(case_file)[0]["issue_classification"] == "manual_only"


def test_review_case_file_routes_localized_high_ai_advisories_to_text_repair():
    case_file = ReviewCaseFileBuilder().build(
        project_id="project",
        chapter_number=1,
        draft_text="这说明局势已经发生了某种意义上的变化。",
        skill_validation={
            "passed": False,
            "failures": [
                {
                    "skill_id": "anti_ai_prose",
                    "validator": "ai_flavor",
                    "metric": "high_advisories",
                    "type": "high_advisories",
                    "actual": 1,
                    "expected_max": 0,
                    "severity": "high",
                    "repairability": "human_review_required",
                    "issue_classification": "manual_only",
                    "blocks_commit": True,
                    "evidence_spans": [
                        {"span": "这说明局势已经发生了某种意义上的变化。"}
                    ],
                }
            ],
        },
    )

    issue = case_file.skill_failures[0]
    violation = case_file_issues_to_violations(case_file)[0]
    assert issue.repairability == "local_patch"
    assert violation["repairability"] == "auto_fixable"
    assert violation["issue_classification"] == "text_repairable"


def test_review_case_file_dedupes_duplicate_fact_conflicts_by_authority_fact():
    packet = SceneReviewPacket(
        scene_index=1,
        blocking_violations=[
            {
                "type": "fact_conflict",
                "severity": "critical",
                "detail": (
                    "Text claim 'only one herb is used' conflicts with established fact "
                    "'the medicine requires herb A, herb B, and herb C'"
                ),
                "target_span": "only one herb is used",
                "source": "consistency",
            },
            {
                "type": "fact_conflict",
                "severity": "high",
                "detail": (
                    "Current prose omits two ingredients; established fact: "
                    "the medicine requires herb A, herb B, and herb C"
                ),
                "target_span": "omits two ingredients",
                "source": "critic",
            },
        ],
    )

    case_file = ReviewCaseFileBuilder().build(
        project_id="project",
        chapter_number=1,
        draft_text="draft",
        scene_packets=[packet],
    )

    issues = case_file.all_issues()
    assert len(issues) == 1
    assert issues[0].repair_domain == "fact"
    assert issues[0].type == "fact_conflict"
    assert issues[0].severity == "critical"
    assert "only one herb is used" in issues[0].detail
    assert "omits two ingredients" in issues[0].detail
    assert issues[0].dedupe_key.startswith("scene:1:fact:fact_conflict:")


def test_review_case_file_merges_single_pov_and_head_hopping_into_one_intent():
    skill_validation = {
        "passed": False,
        "failures": [
            {
                "skill_id": "narrative_writing",
                "validator": "pov_consistency",
                "metric": "single_pov_per_scene",
                "severity": "high",
                "scene_index": 1,
                "reason": "Scene output appears to leave the declared single-POV boundary.",
            },
            {
                "skill_id": "narrative_writing",
                "validator": "pov_consistency",
                "metric": "head_hopping_count",
                "severity": "high",
                "scene_index": 1,
                "reason": "Inner access to non-POV characters exceeds the skill contract.",
                "target_span": "大师兄心里一沉",
            },
        ],
    }

    case_file = ReviewCaseFileBuilder().build(
        project_id="project",
        chapter_number=1,
        draft_text="draft",
        skill_validation=skill_validation,
    )

    issues = case_file.all_issues()
    assert len(issues) == 1
    assert issues[0].dedupe_key == "scene:1:pov:boundary"
    assert issues[0].repair_domain == "pov"
    assert "Inner access to non-POV characters" in issues[0].detail


def test_combine_chapter_review_validations_collects_all_review_nodes():
    combined = combine_chapter_review_validations({
        "chapter_skill_review": {
            "validation": {
                "passed": False,
                "failures": [
                    {
                        "validator": "ai_flavor",
                        "metric": "dash_per_1000",
                        "severity": "high",
                    }
                ],
            }
        },
        "chapter_rhythm_review": {
            "validation": {
                "passed": False,
                "failures": [
                    {
                        "validator": "chapter_rhythm_review",
                        "metric": "review_unavailable",
                        "severity": "high",
                    }
                ],
            },
            "unavailable": True,
        },
    })

    assert combined is not None
    assert combined["passed"] is False
    assert len(combined["failures"]) == 2
    assert {failure["chapter_review_node"] for failure in combined["failures"]} == {
        "chapter_skill_review",
        "chapter_rhythm_review",
    }


@pytest.mark.asyncio
async def test_fbi_intake_accepts_review_case_file_skill_failures():
    packet = SceneReviewPacket(scene_index=0, candidate_text="text")
    case_file = ReviewCaseFileBuilder().build(
        project_id="project",
        chapter_number=1,
        draft_text="text",
        skill_validation={
            "passed": False,
            "failures": [
                {
                    "skill_id": "anti_ai_prose",
                    "validator": "ai_flavor",
                    "metric": "dash_per_1000",
                    "actual": 9.0,
                    "expected": 2.5,
                    "severity": "high",
                    "scene_index": 0,
                }
            ],
        },
    )
    case = ChapterReviewCase(
        project_id="project",
        chapter_number=1,
        scene_packets=[packet],
        review_case_file=case_file.model_dump(),
    )

    validated = await FBIChapterCaseIntakeService().intake(case)
    assert validated.scene_packets[0].blocking_violations

    plan = await FBIChapterRepairPlanner().plan(validated, run_blueprint_agent=False)
    assert plan.orders
    assert plan.orders[0].repair_domain == "anti_ai"
    assert plan.orders[0].write_scope == ["scene:0"]
    assert plan.execution_layers


def test_final_acceptance_delta_preserves_metric_and_localizes_issue():
    final_text = (
        "She picked up the herb and set it on the stone.\n\n"
        "She picked up the root and set it on the stone.\n\n"
        "He looked at the flame and waited.\n\n"
        "He looked at the ash and waited."
    )
    gate_result = {
        "allowed": False,
        "reason": "final skill validation failed",
        "trace": {
            "initial_validation": {
                "passed": False,
                "failures": [
                    {
                        "skill_id": "anti_ai_prose",
                        "validator": "ai_discourse",
                        "metric": "paragraph_shape_repeat_count",
                        "actual": 4,
                        "expected_max": 1,
                        "severity": "high",
                        "reason": "paragraph_shape_repeat_count exceeds the ai_discourse skill contract.",
                        "action": "paragraph_reconstruction",
                    }
                ],
            }
        },
    }

    delta = build_final_acceptance_case_delta(
        project_id="project",
        chapter_number=1,
        final_text=final_text,
        final_gate_result=gate_result,
        acceptance_round=1,
        parent_case_id="rcf_parent",
        scene_texts={
            0: (
                "She picked up the herb and set it on the stone.\n\n"
                "She picked up the root and set it on the stone."
            ),
            1: (
                "He looked at the flame and waited.\n\n"
                "He looked at the ash and waited."
            ),
        },
    )

    issue = delta["skill_failures"][0]
    assert issue["source"] == "final_acceptance"
    assert issue["source_validator"] == "ai_discourse"
    assert issue["metric"] == "paragraph_shape_repeat_count"
    assert issue["evidence"]["actual"] == 4
    assert issue["evidence"]["expected_max"] == 1
    assert issue["evidence"]["failure_signature"]
    assert issue["evidence"]["origin_step"] == "fbi_final_acceptance"
    assert issue["evidence"]["localization_status"] == "localized"
    assert issue["target_span"]
    assert issue["scene_index"] == 0
    assert issue["blocks_commit"] is False

    # Advisory skill findings stay in the case file for repair/trace, but the
    # blocking-only violation projection must be empty.
    violations = case_file_issues_to_violations(delta)
    assert violations == []


def test_final_acceptance_delta_downgrades_unlocalized_broad_style_issue():
    gate_result = {
        "allowed": False,
        "reason": "final skill validation failed",
        "trace": {
            "initial_validation": {
                "passed": False,
                "failures": [
                    {
                        "skill_id": "anti_ai_prose",
                        "validator": "ai_discourse",
                        "metric": "paragraph_shape_repeat_count",
                        "actual": 98,
                        "expected_max": 1,
                        "severity": "high",
                        "reason": "chapter-level paragraph shape metric failed",
                        "action": "paragraph_reconstruction",
                    }
                ],
            }
        },
    }

    delta = build_final_acceptance_case_delta(
        project_id="project",
        chapter_number=1,
        final_text="A long chapter without a localizable paragraph target.",
        final_gate_result=gate_result,
        acceptance_round=1,
        scene_texts={0: "A long chapter without a localizable paragraph target."},
    )

    issue = delta["skill_failures"][0]
    assert issue["blocks_commit"] is False
    assert issue["evidence"]["enforcement"] == "advisory_trace"
    assert issue["evidence"]["reliability_policy"] == "unlocalized_or_low_confidence_not_auto_fixable"
    assert case_file_issues_to_violations(delta) == []


def test_final_acceptance_delta_keeps_deterministic_dash_issue_blocking():
    gate_result = {
        "allowed": False,
        "reason": "final skill validation failed",
        "trace": {
            "initial_validation": {
                "passed": False,
                "failures": [
                    {
                        "skill_id": "anti_ai_prose",
                        "validator": "ai_flavor",
                        "metric": "dash_per_1000",
                        "actual": 8.0,
                        "expected_max": 2.5,
                        "severity": "high",
                        "reason": "dash density failed",
                        "action": "style_repair",
                    }
                ],
            }
        },
    }

    delta = build_final_acceptance_case_delta(
        project_id="project",
        chapter_number=1,
        final_text="她停住——又往前走——灯影一晃——门缝里没有声音——",
        final_gate_result=gate_result,
        acceptance_round=1,
        scene_texts={0: "她停住——又往前走——灯影一晃——门缝里没有声音——"},
    )

    issue = delta["skill_failures"][0]
    assert issue["blocks_commit"] is True
    assert issue["evidence"]["enforcement"] == "commit_blocking"
    assert case_file_issues_to_violations(delta)


def test_commit_health_delta_uses_final_acceptance_adapter():
    delta = build_commit_health_case_delta(
        project_id="project",
        chapter_number=1,
        final_text="[[SCENE:scene_1]]\ntext",
        health_result={
            "allowed": False,
            "hard_issues": [
                {
                    "code": "residual_scene_marker",
                    "severity": "hard",
                    "detail": "Scene text still contains scene/chapter markers after alignment.",
                    "scene_index": 0,
                }
            ],
        },
        acceptance_round=1,
        parent_case_id="rcf_parent",
        scene_texts={0: "[[SCENE:scene_1]]\ntext"},
    )

    issue = delta["scene_issues"][0]
    assert issue["source"] == "final_acceptance"
    assert issue["source_validator"] == "commit_health"
    assert issue["type"] == "residual_scene_marker"
    assert issue["repairability"] == "deterministic"
    assert issue["repair_domain"] == "structure"
    assert issue["target_span"] == "[[SCENE:scene_1]]"
    assert issue["evidence"]["failure_signature"]

    violations = case_file_issues_to_violations(delta)
    assert violations[0]["validator"] == "commit_health"
    assert violations[0]["metric"] == "residual_scene_marker"
    assert violations[0]["evidence_spans"]


@pytest.mark.asyncio
async def test_fbi_final_acceptance_service_keeps_style_review_advisory():
    packet = CompiledSkillPacket(
        agent_name="core_generation",
        active_skills=[],
        validators=[],
    )

    result = await FBIFinalAcceptanceService().evaluate(
        project_id="project",
        chapter_number=1,
        db=None,
        final_text="Clean chapter text." * 20,
        chapter_state={"established_facts": ["fact"]},
        scene_texts={0: "Clean chapter text."},
        style_result={"requires_human_review": True, "style_score": 50},
        packet=packet,
        acceptance_round=1,
    )

    assert result["allowed"] is True
    assert result["reason"] == ""
    assert result["commit_health"]["hard_issues"] == []
    assert result["commit_health"]["issues"][0]["code"] == "style_requires_human_review"
    assert result.get("case_file_delta") is None
