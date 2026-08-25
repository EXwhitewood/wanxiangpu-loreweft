from app.models.chapter_review import ChapterReviewCase, SceneReviewPacket
from app.models.review_case_file import ReviewCaseFile, ReviewCaseIssue
from app.services.fbi.chapter_case_intake import FBIChapterCaseIntakeService, FBIChapterRepairPlanner
from app.services.fbi.review_minister import FBIReviewMinister

import asyncio


def test_review_minister_diagnoses_fact_conflict_with_patch_plan():
    case = ChapterReviewCase(
        case_id="case-review-minister",
        project_id="project",
        chapter_number=1,
        scene_packets=[
            SceneReviewPacket(
                scene_index=0,
                blocking_violations=[
                    {
                        "type": "fact_conflict",
                        "severity": "high",
                        "detail": "Text says replace 'right arm' with 'left arm'.",
                        "target_span": "right arm",
                        "blocks_commit": True,
                        "evidence": {
                            "current_claim": "right arm",
                            "authority_claim": "left arm",
                        },
                    }
                ],
            )
        ],
    )

    diagnosis_set = FBIReviewMinister().diagnose_case(case)

    assert len(diagnosis_set.diagnoses) == 1
    diagnosis = diagnosis_set.diagnoses[0]
    assert diagnosis.issue_family == "fact"
    assert diagnosis.repair_intent.repair_lane == "fact_local_patch"
    # P2-2: fact_conflict 改为 requires_llm=True 后，revision_blueprint 不再产出
    # 确定性 tool_blueprint（status=blueprint_incomplete），但 repair_intent.patch_plan
    # 仍由 _patch_plan 从 evidence 构造，键名为 from/to（非 old_text/new_text）。
    assert diagnosis.repair_intent.patch_plan[0]["from"] == "right arm"
    assert diagnosis.repair_intent.patch_plan[0]["to"] == "left arm"
    assert diagnosis.acceptance_criteria[0].metric == "fact_conflict"


def test_review_minister_diagnoses_dash_density_as_surface_cleanup():
    case = ChapterReviewCase(case_id="case-dash", project_id="project", chapter_number=1)
    violation = {
        "type": "dash_per_1000",
        "metric": "dash_per_1000",
        "validator": "ai_flavor",
        "severity": "high",
        "blocks_commit": True,
        "source_scene": 0,
        "expected_max": 2.5,
    }

    annotated, diagnosis_set = FBIReviewMinister().annotate_violations(case, [violation])

    assert annotated[0]["repair_intent"]["repair_lane"] == "deterministic_surface_cleanup"
    assert annotated[0]["repair_intent"]["preferred_operation"] == "surface_cleanup"
    assert diagnosis_set.trace["families"]["anti_ai_punctuation"] == 1
    assert diagnosis_set.diagnoses[0].acceptance_criteria[-1].metric == "dash_density"


def test_planner_merges_review_minister_brief_into_orders():
    case = ChapterReviewCase(
        project_id="project",
        chapter_number=1,
        scene_packets=[
            SceneReviewPacket(
                scene_index=0,
                blocking_violations=[
                    {
                        "type": "fact_conflict",
                        "severity": "high",
                        "detail": "Text says replace 'right arm' with 'left arm'.",
                        "target_span": "right arm",
                        "blocks_commit": True,
                        "evidence": {
                            "current_claim": "right arm",
                            "authority_claim": "left arm",
                        },
                    }
                ],
            )
        ],
    )

    validated = asyncio.run(FBIChapterCaseIntakeService().intake(case))
    plan = asyncio.run(FBIChapterRepairPlanner().plan(validated))

    order = plan.orders[0]
    assert order.repair_lane == "fact_local_patch"
    assert order.repair_brief["diagnosis_id"].startswith("diag_")
    assert order.repair_brief["repair_intent"]["preferred_operation"] == "replace_conflicting_phrase"
    # P2-2: fact_conflict 改为 requires_llm=True，patch_plan 键名为 from/to
    assert order.repair_brief["patch_plan"][0]["from"] == "right arm"
    assert plan.repair_strategy_summary["review_minister"]["diagnoses"] == 1


def test_review_case_file_summary_includes_issue_lifecycle_counts():
    case_file = ReviewCaseFile(
        case_id="case",
        project_id="project",
        chapter_number=1,
        issue_status={"iss-one": "resolved"},
        diagnoses=[{"diagnosis_id": "diag-one"}],
        repair_intents=[{"intent_id": "intent-one"}],
    )
    case_file.skill_failures.append(
        ReviewCaseIssue(
            issue_id="iss-one",
            source="skill_validator",
            type="dash_per_1000",
            blocks_commit=True,
            repair_domain="anti_ai",
        )
    )

    summary = case_file.summary()

    assert summary["resolved_issues"] == 1
    assert summary["current_issues"] == 0
    assert summary["diagnoses"] == 1
    assert summary["repair_intents"] == 1
