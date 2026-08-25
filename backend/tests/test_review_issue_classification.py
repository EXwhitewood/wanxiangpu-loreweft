from app.models.chapter_review import ChapterReviewCase, SceneReviewPacket
from app.services.fbi.chapter_case_intake import FBIChapterCaseIntakeService, FBIChapterRepairPlanner
from app.services.review_issue_semantics import normalize_violation_semantics


def test_issue_semantics_classifies_text_contract_and_planning_lanes():
    text_issue = normalize_violation_semantics({
        "type": "fact_conflict",
        "severity": "high",
        "detail": "text conflicts with established fact",
        "repair_scope": "prose_text",
    })
    contract_issue = normalize_violation_semantics({
        "type": "contract_internal_conflict",
        "severity": "high",
        "detail": "scene contract layers conflict",
    })
    scene_contract_issue = normalize_violation_semantics({
        "type": "fact_conflict",
        "severity": "high",
        "detail": "contract source needs repair",
        "repair_scope": "scene_contract",
    })

    assert text_issue["issue_classification"] == "text_repairable"
    assert contract_issue["issue_classification"] == "planning_conflict"
    assert scene_contract_issue["issue_classification"] == "contract_repair_required"


def test_fbi_intake_routes_contract_classification_to_contract_patch():
    case = ChapterReviewCase(
        project_id="project-1",
        chapter_number=1,
        scene_packets=[
            SceneReviewPacket(
                scene_index=0,
                repairability="contract_repair_required",
                blocking_violations=[{
                    "type": "fact_conflict",
                    "severity": "high",
                    "detail": "contract source needs repair",
                    "repair_scope": "scene_contract",
                    "issue_classification": "contract_repair_required",
                    "blocks_commit": True,
                }],
            )
        ],
    )

    import asyncio

    validated = asyncio.run(FBIChapterCaseIntakeService().intake(case))
    plan = asyncio.run(
        FBIChapterRepairPlanner().plan(validated, run_blueprint_agent=False)
    )

    assert len(plan.orders) == 1
    assert plan.orders[0].repair_type == "contract_patch"


def test_fbi_intake_excludes_validator_failure_from_text_repair_plan():
    case = ChapterReviewCase(
        project_id="project-validator-isolation",
        chapter_number=7,
        scene_packets=[
            SceneReviewPacket(
                scene_index=0,
                repairability="human_review_required",
                candidate_text="守门人后退。\n\n“别过来。”\n\n他松开钥匙。",
                blocking_violations=[
                    {
                        "type": "proposition_extractor_unavailable",
                        "severity": "high",
                        "detail": "Extractor returned no auditable propositions.",
                        "blocks_commit": True,
                        "repairability": "degraded_system_issue",
                        "repair_scope": "system",
                    },
                    {
                        "type": "missing_must_show",
                        "severity": "high",
                        "detail": "守门人松手前缺少钥匙用途，只说了'别过来'。",
                        "evidence_span": "“别过来。”",
                        "blocks_commit": True,
                        "repairability": "contract_repair_required",
                    },
                ],
            )
        ],
    )

    import asyncio

    validated = asyncio.run(FBIChapterCaseIntakeService().intake(case))
    packet = validated.scene_packets[0]
    assert [v["type"] for v in packet.blocking_violations] == ["missing_must_show"]
    system_findings = [
        v for v in packet.advisory_violations
        if v.get("type") == "proposition_extractor_unavailable"
    ]
    assert len(system_findings) == 1
    assert system_findings[0]["blocks_commit"] is False

    plan = asyncio.run(
        FBIChapterRepairPlanner().plan(validated, run_blueprint_agent=False)
    )
    assert any(
        detail.get("type") == "missing_must_show"
        for order in plan.orders
        for detail in order.violation_details
    )
    assert not any(
        detail.get("type") == "proposition_extractor_unavailable"
        for order in plan.orders
        for detail in order.violation_details
    )
