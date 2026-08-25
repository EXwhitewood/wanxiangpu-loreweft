import asyncio

import pytest

from app.models.chapter_review import ChapterReviewCase, SceneReviewPacket
from app.agents.fbi.review_blueprint_agent import FBIReviewBlueprintAgent
from app.services.fbi.chapter_case_intake import (
    FBIChapterCaseIntakeService,
    FBIChapterRepairPlanner,
    _compare_state_claims,
)


@pytest.fixture(autouse=True)
def _isolate_case_intake_tests_from_live_blueprint_llm(monkeypatch):
    """Keep case-intake unit tests deterministic and offline.

    The blueprint agent's live multi-round LLM protocol is covered by its own
    focused tests.  These tests exercise intake, routing, and repair-plan
    semantics, so an external model call would make their result depend on
    network availability and retry latency instead of the code under test.
    """

    async def _offline_blueprint(self, context):
        case = context["case"]
        return {
            "status": "degraded",
            "revision_blueprint": {
                "blueprint_id": f"case_intake_test_{case.case_id}",
                "case_id": case.case_id,
                "work_units": [],
                "planning_trace": [{"status": "live_llm_isolated_for_unit_test"}],
                "status": "degraded",
            },
            "trace": {
                "mode": "unit_test_offline",
                "session_status": "skipped",
                "failure_reason": "live_llm_isolated_for_unit_test",
            },
        }

    monkeypatch.setattr(FBIReviewBlueprintAgent, "execute", _offline_blueprint)


def test_intake_preserves_violations_without_source_scene():
    case = ChapterReviewCase(
        project_id="project-1",
        chapter_number=5,
        scene_packets=[
            SceneReviewPacket(
                scene_index=0,
                repairability="auto_fixable",
                blocking_violations=[
                    {
                        "type": "ai_flavor",
                        "severity": "high",
                        "detail": "template phrase",
                        "target_span": "some phrase",
                        "blocks_commit": True,
                    }
                ],
            ),
            SceneReviewPacket(
                scene_index=1,
                repairability="cross_scene_fixable",
                blocking_violations=[
                    {
                        "type": "fact_conflict",
                        "severity": "blocking",
                        "detail": "state mismatch",
                        "target_span": "state:location",
                        "blocks_commit": True,
                    }
                ],
            ),
        ],
    )

    intake = FBIChapterCaseIntakeService()
    planner = FBIChapterRepairPlanner()

    validated = asyncio.run(intake.intake(case))
    plan = asyncio.run(planner.plan(validated))

    assert sum(len(p.blocking_violations) for p in validated.scene_packets) == 1
    assert sum(len(p.advisory_violations) for p in validated.scene_packets) == 1
    assert plan.status != "clean"
    assert plan.total_violations == 2
    assert len(plan.orders) == 1
    assert plan.orders[0].target_scenes == [1]


def test_repair_order_uses_review_minister_issue_id_when_aliases_differ():
    case = ChapterReviewCase(
        case_id="case-issue-alias",
        project_id="project-1",
        chapter_number=1,
    )
    order = FBIChapterRepairPlanner()._violation_to_order(
        {
            "issue_id": "iss_canonical",
            "violation_id": "validator_alias",
            "type": "dash_per_1000",
            "severity": "high",
            "blocks_commit": True,
            "source_scene": 0,
            "target_span": "——",
        },
        case,
        0,
        is_blocking=True,
    )

    assert order is not None
    assert order.source_violation_ids == ["iss_canonical"]
    assert order.violation_details[0]["violation_id"] == "validator_alias"


def test_final_gate_chapter_skill_delta_is_distributed_to_scene_targets():
    case = ChapterReviewCase(
        project_id="project-1",
        chapter_number=5,
        scene_packets=[
            SceneReviewPacket(scene_index=0, repairability="clean"),
            SceneReviewPacket(scene_index=1, repairability="clean"),
        ],
        case_file_deltas=[
            {
                "project_id": "project-1",
                "chapter_number": 5,
                "draft_hash": "draft",
                "created_from": "final_acceptance",
                "skill_failures": [
                    {
                        "issue_id": "dash-final",
                        "source": "final_acceptance",
                        "source_validator": "ai_flavor",
                        "skill_id": "anti_ai_prose",
                        "scope": "chapter",
                        "type": "dash_per_1000",
                        "metric": "dash_per_1000",
                        "severity": "high",
                        "blocks_commit": True,
                        "repairability": "deterministic",
                        "repair_domain": "anti_ai",
                        "detail": "Dash density exceeds the skill contract.",
                    }
                ],
            }
        ],
    )

    intake = FBIChapterCaseIntakeService()
    planner = FBIChapterRepairPlanner()

    validated = asyncio.run(intake.intake(case))
    plan = asyncio.run(planner.plan(validated))

    assert len(validated.scene_packets[0].blocking_violations) == 1
    assert len(validated.scene_packets[1].blocking_violations) == 1
    # One chapter-level obligation is deduplicated before placement; findings
    # distributed to scene packets no longer multiply semantic orders.
    assert len(plan.repair_goals) == 1
    assert len(plan.orders) == 1
    assert [order.target_scenes for order in plan.orders] == [[0, 1]]
    assert {order.repair_type for order in plan.orders} == {"local_patch"}
    assert {order.repair_lane for order in plan.orders} == {"deterministic_surface_cleanup"}
    assert all(order.repair_brief["target_metrics"][0]["metric"] == "dash_per_1000" for order in plan.orders)
    assert plan.repair_strategy_summary["deterministic_first"] == 1


def test_unlocalized_chapter_style_delta_is_not_distributed_to_every_scene():
    case = ChapterReviewCase(
        project_id="project-1",
        chapter_number=5,
        scene_packets=[
            SceneReviewPacket(scene_index=0, repairability="clean"),
            SceneReviewPacket(scene_index=1, repairability="clean"),
            SceneReviewPacket(scene_index=2, repairability="clean"),
        ],
        case_file_deltas=[
            {
                "project_id": "project-1",
                "chapter_number": 5,
                "draft_hash": "draft",
                "created_from": "final_acceptance",
                "skill_failures": [
                    {
                        "issue_id": "shape-final",
                        "source": "final_acceptance",
                        "source_validator": "ai_discourse",
                        "skill_id": "anti_ai_prose",
                        "scope": "chapter",
                        "type": "paragraph_shape_repeat_count",
                        "metric": "paragraph_shape_repeat_count",
                        "severity": "high",
                        "blocks_commit": True,
                        "repairability": "manual_review",
                        "repair_domain": "anti_ai",
                        "detail": "Chapter-level shape metric failed without local evidence.",
                        "evidence": {"localization_status": "pending"},
                    }
                ],
            }
        ],
    )

    validated = asyncio.run(FBIChapterCaseIntakeService().intake(case))
    plan = asyncio.run(FBIChapterRepairPlanner().plan(validated))

    assert [len(packet.blocking_violations) for packet in validated.scene_packets] == [0, 0, 0]
    assert [len(packet.advisory_violations) for packet in validated.scene_packets] == [1, 0, 0]
    assert plan.status == "clean"
    assert plan.orders == []


def test_planner_does_not_schedule_specialized_lanes_for_advisory_findings():
    case = ChapterReviewCase(
        project_id="project-1",
        chapter_number=5,
        scene_packets=[
            SceneReviewPacket(
                scene_index=0,
                repairability="auto_fixable",
                blocking_violations=[
                    {
                        "type": "paragraph_shape_repeat_count",
                        "metric": "paragraph_shape_repeat_count",
                        "validator": "ai_discourse",
                        "severity": "high",
                        "actual": 5,
                        "expected": 2,
                        "detail": "Paragraph openings repeat.",
                        "blocks_commit": True,
                    },
                    {
                        "type": "voice_fingerprint",
                        "metric": "voice_fingerprint",
                        "validator": "voice_fingerprint",
                        "severity": "high",
                        "detail": "Voice drifted from character card.",
                        "blocks_commit": True,
                    },
                ],
            )
        ],
    )

    validated = asyncio.run(FBIChapterCaseIntakeService().intake(case))
    plan = asyncio.run(FBIChapterRepairPlanner().plan(validated))

    assert len(validated.scene_packets[0].advisory_violations) == 2
    assert validated.scene_packets[0].blocking_violations == []
    assert plan.status == "clean"
    assert plan.orders == []


def test_prose_contract_violation_uses_scene_repair_not_contract_patch():
    case = ChapterReviewCase(
        project_id="project-1",
        chapter_number=10,
        scene_packets=[
            SceneReviewPacket(
                scene_index=0,
                repairability="contract_repair_required",
                blocking_violations=[
                    {
                        "type": "required_ambiguity_broken",
                        "severity": "high",
                        "detail": "The prose confirms a hidden identity too strongly.",
                        "target_span": "hidden identity",
                        "blocks_commit": True,
                    }
                ],
            )
        ],
    )

    intake = FBIChapterCaseIntakeService()
    planner = FBIChapterRepairPlanner()

    validated = asyncio.run(intake.intake(case))
    plan = asyncio.run(planner.plan(validated))

    assert plan.status == "needs_repair"
    assert len(plan.orders) == 1
    assert plan.orders[0].repair_type in {"local_patch", "scene_rewrite"}
    assert plan.orders[0].repair_type != "contract_patch"


def test_advisory_only_findings_are_recorded_without_automatic_repair_orders():
    case = ChapterReviewCase(
        project_id="project-1",
        chapter_number=10,
        scene_packets=[
            SceneReviewPacket(
                scene_index=0,
                repairability="contract_repair_required",
                advisory_violations=[
                    {
                        "type": "must_show_overload",
                        "severity": "medium",
                        "detail": "The scene contract has too many must_show items.",
                        "source_scenes": [0, 1],
                        "blocks_commit": False,
                    }
                ],
            )
        ],
    )

    intake = FBIChapterCaseIntakeService()
    planner = FBIChapterRepairPlanner()

    validated = asyncio.run(intake.intake(case))
    plan = asyncio.run(planner.plan(validated))

    assert plan.status == "clean"
    assert plan.total_violations == 1
    assert plan.blocking_violations == 0
    assert plan.orders == []
    assert "excluded from automatic repair convergence" in " ".join(plan.global_notes)


def test_hard_correctness_advisory_is_promoted_to_blocking_order():
    case = ChapterReviewCase(
        project_id="project-1",
        chapter_number=19,
        scene_packets=[
            SceneReviewPacket(
                scene_index=0,
                repairability="cross_scene_fixable",
                advisory_violations=[
                    {
                        "type": "fact_conflict",
                        "severity": "medium",
                        "detail": (
                            "Established fact says Fengxi has one antidote pill, "
                            "but the prose says half a bottle."
                        ),
                        "target_span": "half a bottle",
                        "blocks_commit": False,
                    }
                ],
            )
        ],
    )

    intake = FBIChapterCaseIntakeService()
    planner = FBIChapterRepairPlanner()

    validated = asyncio.run(intake.intake(case))
    plan = asyncio.run(planner.plan(validated))

    assert len(validated.scene_packets[0].blocking_violations) == 1
    assert validated.scene_packets[0].advisory_violations == []
    promoted = validated.scene_packets[0].blocking_violations[0]
    assert promoted["type"] == "fact_conflict"
    assert promoted["blocks_commit"] is True
    assert promoted["severity"] == "high"
    assert promoted["promotion_reason"] == "hard_correctness_advisory"
    assert plan.status == "needs_repair"
    assert plan.blocking_violations == 1
    assert len(plan.orders) == 1
    assert plan.orders[0].repair_type == "local_patch"
    assert plan.orders[0].owner_scene == 0


def test_advisory_findings_do_not_create_repair_orders_after_intake_demotion():
    case = ChapterReviewCase(
        project_id="project-1",
        chapter_number=10,
        scene_packets=[
            SceneReviewPacket(
                scene_index=0,
                repairability="auto_fixable",
                blocking_violations=[
                    {
                        "type": "narration_explanation_artifact",
                        "severity": "high",
                        "detail": "The prose explains the mechanic instead of showing it.",
                        "target_span": "not to trap her, but to prevent leakage",
                        "blocks_commit": True,
                    }
                ],
                advisory_violations=[
                    {
                        "type": "must_show_overload",
                        "severity": "medium",
                        "detail": "The scene contract has too many must_show items.",
                        "source_scenes": [0, 1],
                        "blocks_commit": False,
                    }
                ],
            )
        ],
    )

    intake = FBIChapterCaseIntakeService()
    planner = FBIChapterRepairPlanner()

    validated = asyncio.run(intake.intake(case))
    plan = asyncio.run(planner.plan(validated))

    assert plan.status == "clean"
    assert plan.total_violations == 2
    assert plan.blocking_violations == 0
    assert plan.orders == []


def test_missing_opening_state_key_is_not_cross_scene_gap():
    conflicts = _compare_state_claims(
        {
            "narrative_time": "morning",
            "objective_state": {"Fengxi": {"location": "discipline hall"}},
        },
        {},
        0,
        1,
    )

    assert conflicts == []


def test_explicit_state_mismatch_still_blocks():
    conflicts = _compare_state_claims(
        {"narrative_time": "morning"},
        {"narrative_time": "night"},
        0,
        1,
    )

    assert len(conflicts) == 1
    assert conflicts[0]["type"] == "cross_scene_state_mismatch"


def test_fact_conflict_uses_owner_scene_local_patch_even_with_source_scenes():
    case = ChapterReviewCase(
        project_id="project-1",
        chapter_number=16,
        scene_packets=[
            SceneReviewPacket(
                scene_index=1,
                repairability="auto_fixable",
                blocking_violations=[
                    {
                        "type": "fact_conflict",
                        "severity": "high",
                        "detail": "Fact conflict in scene provenance.",
                        "source_scene": 1,
                        "source_scenes": [0, 1],
                        "blocks_commit": True,
                    }
                ],
            )
        ],
    )

    intake = FBIChapterCaseIntakeService()
    planner = FBIChapterRepairPlanner()

    validated = asyncio.run(intake.intake(case))
    plan = asyncio.run(planner.plan(validated))

    assert len(plan.orders) == 1
    assert plan.orders[0].repair_type == "local_patch"
    assert plan.orders[0].owner_scene == 1
    assert plan.orders[0].target_scenes == [1]


def test_fact_conflict_with_suggested_correction_overrides_rewrite_to_local_patch():
    case = ChapterReviewCase(
        project_id="project-1",
        chapter_number=16,
        scene_packets=[
            SceneReviewPacket(
                scene_index=1,
                repairability="cross_scene_fixable",
                blocking_violations=[
                    {
                        "type": "fact_conflict",
                        "severity": "high",
                        "detail": (
                            "Text claim 'the apprentice entered the inner hall' "
                            "conflicts with established fact 'the apprentice remains outside'; "
                            "suggested correction: 'the apprentice stopped at the outer gate'"
                        ),
                        "source_scene": 1,
                        "source_scenes": [1],
                        "suggested_strategy": "rewrite_scene",
                        "blocks_commit": True,
                    }
                ],
            )
        ],
    )

    intake = FBIChapterCaseIntakeService()
    planner = FBIChapterRepairPlanner()

    validated = asyncio.run(intake.intake(case))
    plan = asyncio.run(planner.plan(validated))

    assert len(plan.orders) == 1
    assert plan.orders[0].repair_type == "local_patch"
    assert plan.orders[0].owner_scene == 1


def test_fact_conflict_with_missing_required_components_gets_fact_goal_and_stronger_lane():
    case = ChapterReviewCase(
        project_id="project-1",
        chapter_number=19,
        scene_packets=[
            SceneReviewPacket(
                scene_index=1,
                repairability="cross_scene_fixable",
                blocking_violations=[
                    {
                        "type": "fact_conflict",
                        "severity": "critical",
                        "detail": (
                            "Text claim '凤溪只找到一株赤翎草，并用其单独炼制丹药' "
                            "conflicts with established fact '炼制破境丹需要三味主药：赤阳草、凝露花、地脉根茎'; "
                            "suggested correction: '按照合同补充其他两味主药的获取或调整丹方说明'"
                        ),
                        "target_span": "凤溪只找到一株赤翎草，并用其单独炼制丹药",
                        "source_scene": 1,
                        "source_scenes": [1],
                        "blocks_commit": True,
                    }
                ],
            )
        ],
    )

    intake = FBIChapterCaseIntakeService()
    planner = FBIChapterRepairPlanner()

    validated = asyncio.run(intake.intake(case))
    plan = asyncio.run(planner.plan(validated))

    order = plan.orders[0]
    assert order.repair_type == "local_patch"
    assert order.repair_lane == "fact_bridge_patch"
    assert order.repair_strength == "S3"
    assert order.allowed_max_strength == "S4"
    assert order.candidate_attempt_budget == 3
    fact_goal = order.repair_brief["fact_repair_goal"]
    assert fact_goal["conflict_type"] == "missing_required_components"
    assert {"赤阳草", "凝露花", "地脉根茎"} <= set(fact_goal["required_entities"])
    assert "凤溪只找到一株赤翎草" in fact_goal["text_claim"]
    assert "insert_missing_fact_components" in order.repair_brief["edit_scope"]["allowed_operations"]


def test_chinese_contract_anchor_fact_goal_extracts_expected_value_not_marker_fragment():
    case = ChapterReviewCase(
        project_id="project-1",
        chapter_number=19,
        scene_packets=[
            SceneReviewPacket(
                scene_index=0,
                repairability="auto_fixable",
                blocking_violations=[
                    {
                        "type": "fact_conflict",
                        "severity": "high",
                        "detail": "正文中描述时间为'错误时段'，但合同时间锚点明确为'目标时段'，存在严重的时间矛盾。",
                        "target_span": "林中的光变暗，时值错误时段。",
                        "expected_behavior": "时间应与合同一致的目标时段，如'时值目标时段'或类似表述。",
                        "source_scene": 0,
                        "source_scenes": [0],
                        "blocks_commit": True,
                    }
                ],
            )
        ],
    )

    validated = asyncio.run(FBIChapterCaseIntakeService().intake(case))
    plan = asyncio.run(FBIChapterRepairPlanner().plan(validated))

    order = plan.orders[0]
    assert order.repair_type == "local_patch"
    assert order.repair_lane == "fact_local_patch"
    fact_goal = order.repair_brief["fact_repair_goal"]
    assert fact_goal["authority_fact"] == "目标时段"
    assert "目标时段" in fact_goal["required_entities"]
    assert "点明确为" not in fact_goal["required_entities"]
    assert "错误时段" in fact_goal["forbidden_claims"]


def test_clue_provenance_authority_is_read_from_structured_evidence():
    from app.services.fbi.chapter_case_intake import _compile_fact_repair_goal

    goal = _compile_fact_repair_goal({
        "type": "clue_provenance_error",
        "target_span": "桌上多出一张没有署名的纸条。",
        "expected_behavior": "不得编造放置者，应保持来源未知并补足发现条件。",
        "evidence": {
            "authority_fact": "线索「无署名纸条」的来源尚未建立",
            "authority_status": "missing_provenance",
            "text_claim": "无署名纸条 被发现",
        },
    })

    assert goal["authority_fact"] == "线索「无署名纸条」的来源尚未建立"
    assert goal["authority_status"] == "missing_provenance"


def test_internal_conflict_missing_context_bridge_uses_fact_bridge_lane():
    case = ChapterReviewCase(
        project_id="project-1",
        chapter_number=19,
        scene_packets=[
            SceneReviewPacket(
                scene_index=1,
                repairability="auto_fixable",
                blocking_violations=[
                    {
                        "type": "internal_conflict",
                        "severity": "critical",
                        "detail": (
                            "Text claim '主角扫描院外，只感知到甲乙两人，未提及屋内同伴' "
                            "conflicts with established fact '同伴在屋内'; "
                            "suggested correction: '扫描结果应包含同伴，或说明为何无法感知'"
                        ),
                        "target_span": "主角扫描院外，只感知到甲乙两人，未提及屋内同伴",
                        "expected_behavior": "扫描结果应包含同伴，或说明为何无法感知",
                        "source_scene": 1,
                        "source_scenes": [1],
                        "blocks_commit": True,
                    }
                ],
            )
        ],
    )

    validated = asyncio.run(FBIChapterCaseIntakeService().intake(case))
    plan = asyncio.run(FBIChapterRepairPlanner().plan(validated))

    order = plan.orders[0]
    assert order.repair_type == "local_patch"
    assert order.repair_lane == "fact_bridge_patch"
    assert order.repair_strength == "S3"
    assert order.allowed_max_strength == "S4"
    assert order.candidate_attempt_budget == 3
    assert order.repair_brief["target_metrics"][0]["direction"] == "resolve"
    assert order.repair_brief["fact_repair_goal"]["conflict_type"] == "missing_context_bridge"


def test_internal_conflict_with_transition_or_foreshadowing_is_context_bridge():
    from app.services.fbi.chapter_case_intake import _compile_fact_repair_goal

    goal = _compile_fact_repair_goal({
        "type": "internal_conflict",
        "target_span": "你们是谁？",
        "detail": (
            "Text claim '她醒来后问你们是谁' conflicts with established fact "
            "'她此前认出了众人'; suggested correction: "
            "'删除问句，或铺垫禁术造成的记忆丧失。'"
        ),
        "expected_behavior": "保留问句时，补足失忆的因果过渡。",
    })

    assert goal["conflict_type"] == "missing_context_bridge"
    assert goal["old_error_signatures"] == []
    assert goal["required_entities"] == []


def test_fact_conflict_identity_chase_error_stays_local_patch_even_if_rewrite_suggested():
    case = ChapterReviewCase(
        project_id="project-1",
        chapter_number=19,
        scene_packets=[
            SceneReviewPacket(
                scene_index=1,
                repairability="cross_scene_fixable",
                blocking_violations=[
                    {
                        "type": "review_issue",
                        "semantic_type": "fact_conflict",
                        "violation_type": "semantic_quality_error",
                        "severity": "critical",
                        "detail": (
                            "Text claim 'the herb is already refined' "
                            "conflicts with established fact 'the herb has not been refined yet'; "
                            "suggested correction: 'the herb is still raw'"
                        ),
                        "target_span": "the herb is already refined",
                        "source_scene": 1,
                        "source_scenes": [0, 1],
                        "suggested_strategy": "rewrite_scene",
                        "blocks_commit": True,
                    }
                ],
            )
        ],
    )

    intake = FBIChapterCaseIntakeService()
    planner = FBIChapterRepairPlanner()

    validated = asyncio.run(intake.intake(case))
    plan = asyncio.run(planner.plan(validated))

    assert len(plan.orders) == 1
    assert plan.orders[0].repair_type == "local_patch"
    assert plan.orders[0].owner_scene == 1
    assert plan.orders[0].target_scenes == [1]


def test_same_local_text_issue_in_different_scenes_is_not_deduplicated():
    case = ChapterReviewCase(
        project_id="project-1",
        chapter_number=18,
        scene_packets=[
            SceneReviewPacket(
                scene_index=0,
                repairability="auto_fixable",
                blocking_violations=[
                    {
                        "type": "ai_punctuation_artifact",
                        "severity": "high",
                        "detail": "Dash artifact remains too dense.",
                        "target_span": "dash",
                        "blocks_commit": True,
                    }
                ],
            ),
            SceneReviewPacket(
                scene_index=1,
                repairability="auto_fixable",
                blocking_violations=[
                    {
                        "type": "ai_punctuation_artifact",
                        "severity": "high",
                        "detail": "Dash artifact remains too dense.",
                        "target_span": "dash",
                        "blocks_commit": True,
                    }
                ],
            ),
        ],
    )

    intake = FBIChapterCaseIntakeService()
    planner = FBIChapterRepairPlanner()

    validated = asyncio.run(intake.intake(case))
    plan = asyncio.run(planner.plan(validated))

    assert plan.blocking_violations == 2
    assert len(plan.orders) == 2
    assert {order.owner_scene for order in plan.orders} == {0, 1}
    assert {tuple(order.target_scenes) for order in plan.orders} == {(0,), (1,)}


def test_same_scene_pov_findings_without_shared_authority_remain_distinct_goals():
    case = ChapterReviewCase(
        project_id="project-1",
        chapter_number=18,
        scene_packets=[
            SceneReviewPacket(
                scene_index=1,
                repairability="auto_fixable",
                blocking_violations=[
                    {
                        "type": "pov_consistency",
                        "metric": "single_pov_per_scene",
                        "severity": "high",
                        "detail": "Scene output appears to leave the declared single-POV boundary.",
                        "blocks_commit": True,
                    },
                    {
                        "type": "pov_consistency",
                        "metric": "head_hopping_count",
                        "severity": "high",
                        "detail": "Inner access to non-POV characters exceeds the skill contract.",
                        "target_span": "大师兄心里一沉",
                        "blocks_commit": True,
                    },
                ],
            )
        ],
    )

    validated = asyncio.run(FBIChapterCaseIntakeService().intake(case))
    plan = asyncio.run(FBIChapterRepairPlanner().plan(validated))

    assert len(plan.orders) == 2
    assert len(plan.repair_goals) == 2
    assert {order.owner_scene for order in plan.orders} == {1}
    assert {order.repair_domain for order in plan.orders} == {"pov"}
    assert {
        detail.get("metric")
        for order in plan.orders
        for detail in order.violation_details
    } == {"head_hopping_count", "single_pov_per_scene"}


def test_scene_outcome_gaps_use_contract_completion_patch():
    case = ChapterReviewCase(
        project_id="project-1",
        chapter_number=18,
        scene_packets=[
            SceneReviewPacket(
                scene_index=1,
                repairability="auto_fixable",
                blocking_violations=[
                    {
                        "type": "missing_must_show",
                        "severity": "blocking",
                        "detail": "The required joint action never appears in prose.",
                        "target_span": "must_show:joint_action",
                        "blocks_commit": True,
                    },
                    {
                        "type": "ending_state_not_reached",
                        "severity": "blocking",
                        "detail": "The scene ends blocked instead of reaching breakthrough.",
                        "target_span": "ending_state",
                        "blocks_commit": True,
                    },
                ],
            )
        ],
    )

    intake = FBIChapterCaseIntakeService()
    planner = FBIChapterRepairPlanner()

    validated = asyncio.run(intake.intake(case))
    plan = asyncio.run(planner.plan(validated))

    assert len(plan.orders) == 2
    assert {order.repair_type for order in plan.orders} == {"contract_completion_patch"}
    assert {order.owner_scene for order in plan.orders} == {1}


def test_cross_scene_timeline_conflict_keeps_both_sources_and_explicit_owner():
    case = ChapterReviewCase(
        project_id="project-1",
        chapter_number=28,
        scene_packets=[
            SceneReviewPacket(
                scene_index=1,
                repairability="cross_scene_fixable",
                blocking_violations=[{
                    "type": "timeline_conflict",
                    "severity": "critical",
                    "detail": "The prior scene already completed the event repeated here.",
                    "source_scene": 1,
                    "source_scenes": [0, 1],
                    "affected_scene": 0,
                    "target_span": "the repeated event",
                    "repairable_by_text": True,
                    "blocks_commit": True,
                }],
            )
        ],
    )

    validated = asyncio.run(FBIChapterCaseIntakeService().intake(case))
    plan = asyncio.run(FBIChapterRepairPlanner().plan(validated))

    assert len(plan.orders) == 1
    order = plan.orders[0]
    assert order.owner_scene == 0
    assert order.target_scenes == [0, 1]
    assert order.repair_type == "cross_scene_alignment"
    assert order.read_scope == ["review_case_file", "chapter_text", "scene:0", "scene:1"]
    assert order.write_scope == ["scene:0"]


def test_duplicate_scene_outcome_reports_share_one_contract_completion_order():
    case = ChapterReviewCase(
        project_id="project-1",
        chapter_number=19,
        scene_packets=[
            SceneReviewPacket(
                scene_index=0,
                repairability="auto_fixable",
                blocking_violations=[
                    {
                        "type": "missing_must_show",
                        "severity": "blocking",
                        "detail": "场景结束状态要求'与五师兄配合，利用前世记忆中的药草炼丹'，但正文仅完成采集金线苔。",
                        "target_span": "ending_state",
                        "blocks_commit": True,
                    },
                    {
                        "type": "ending_state_not_reached",
                        "severity": "blocking",
                        "detail": "场景结束状态未达成：合同明确要求'与五师兄配合，利用前世记忆中的药草炼丹'，正文中未体现炼丹。",
                        "target_span": "ending_state",
                        "blocks_commit": True,
                    },
                ],
            )
        ],
    )

    validated = asyncio.run(FBIChapterCaseIntakeService().intake(case))
    plan = asyncio.run(FBIChapterRepairPlanner().plan(validated))

    assert len(plan.orders) == 1
    order = plan.orders[0]
    assert order.repair_type == "contract_completion_patch"
    assert order.owner_scene == 0
    assert order.target_scenes == [0]
    assert sorted(
        detail.get("type") for detail in order.violation_details
    ) == ["ending_state_not_reached", "missing_must_show"]


def test_obligation_owner_scene_overrides_source_scene():
    case = ChapterReviewCase(
        project_id="project-1",
        chapter_number=18,
        scene_packets=[
            SceneReviewPacket(
                scene_index=0,
                repairability="auto_fixable",
                blocking_violations=[
                    {
                        "type": "missing_must_show",
                        "severity": "blocking",
                        "detail": "The required beat never appears.",
                        "source_scene": 0,
                        "source_scenes": [0],
                        "obligation_id": "obl_scene_2_beat",
                        "obligation_scope": "scene",
                        "obligation_owner_scene": 2,
                        "obligation_binding_status": "bound",
                        "obligation_ownership_explicit": True,
                        "blocks_commit": True,
                    }
                ],
            )
        ],
    )

    validated = asyncio.run(FBIChapterCaseIntakeService().intake(case))
    plan = asyncio.run(FBIChapterRepairPlanner().plan(validated))

    assert len(plan.orders) == 1
    assert plan.orders[0].owner_scene == 2
    assert plan.orders[0].target_scenes == [2]
    assert plan.orders[0].repair_type == "contract_completion_patch"


def test_chapter_scoped_obligation_requires_human_review():
    case = ChapterReviewCase(
        project_id="project-1",
        chapter_number=18,
        scene_packets=[
            SceneReviewPacket(
                scene_index=1,
                repairability="auto_fixable",
                blocking_violations=[
                    {
                        "type": "missing_must_show",
                        "severity": "blocking",
                        "detail": "The chapter-level obligation is still unresolved.",
                        "source_scene": 1,
                        "source_scenes": [1],
                        "obligation_id": "obl_chapter_arc",
                        "obligation_scope": "chapter",
                        "obligation_owner_scene": None,
                        "obligation_binding_status": "bound",
                        "obligation_ownership_explicit": True,
                        "blocks_commit": True,
                    }
                ],
            )
        ],
    )

    validated = asyncio.run(FBIChapterCaseIntakeService().intake(case))
    plan = asyncio.run(FBIChapterRepairPlanner().plan(validated))

    assert plan.status == "needs_human_review"
    assert len(plan.orders) == 1
    assert plan.orders[0].repair_type == "manual_review"


def test_review_round_limit_does_not_block_executable_text_orders_in_planner():
    case = ChapterReviewCase(
        project_id="project-1",
        chapter_number=19,
        review_round=2,
        scene_packets=[
            SceneReviewPacket(
                scene_index=1,
                repairability="auto_fixable",
                blocking_violations=[
                    {
                        "type": "fact_conflict",
                        "severity": "blocking",
                        "detail": (
                            "Text claim 'four signals' conflicts with established "
                            "fact 'three plus two equals five'; suggested correction: "
                            "make the number consistent."
                        ),
                        "source_scene": 1,
                        "source_scenes": [1],
                        "repair_scope": "prose_text",
                        "repairable_by_text": True,
                        "blocks_commit": True,
                    }
                ],
            )
        ],
    )

    validated = asyncio.run(FBIChapterCaseIntakeService().intake(case))
    plan = asyncio.run(FBIChapterRepairPlanner().plan(validated))

    assert plan.status == "needs_repair"
    assert len(plan.orders) == 1
    assert plan.orders[0].repair_type == "local_patch"


def test_mixed_manual_review_plan_keeps_executable_text_orders():
    case = ChapterReviewCase(
        project_id="project-1",
        chapter_number=19,
        scene_packets=[
            SceneReviewPacket(
                scene_index=0,
                repairability="auto_fixable",
                blocking_violations=[
                    {
                        "type": "fact_conflict",
                        "severity": "blocking",
                        "detail": (
                            "Text claim 'four signals' conflicts with established "
                            "fact 'five signals'; suggested correction: align the count."
                        ),
                        "source_scene": 0,
                        "source_scenes": [0],
                        "repair_scope": "prose_text",
                        "repairable_by_text": True,
                        "blocks_commit": True,
                    }
                ],
            ),
            SceneReviewPacket(
                scene_index=1,
                repairability="human_review_required",
                blocking_violations=[
                    {
                        "type": "ambiguous_authorial_intent",
                        "severity": "blocking",
                        "detail": "Requires an editorial decision.",
                        "source_scene": 1,
                        "source_scenes": [1],
                        "repair_scope": "prose_text",
                        "blocks_commit": True,
                    }
                ],
            ),
        ],
    )

    validated = asyncio.run(FBIChapterCaseIntakeService().intake(case))
    plan = asyncio.run(FBIChapterRepairPlanner().plan(validated))

    assert plan.status == "needs_human_review"
    assert [order.repair_type for order in plan.orders] == [
        "local_patch",
        "manual_review",
    ]


def test_temporal_conflict_prefers_local_patch_even_when_rewrite_suggested():
    case = ChapterReviewCase(
        project_id="project-1",
        chapter_number=19,
        scene_packets=[
            SceneReviewPacket(
                scene_index=1,
                repairability="auto_fixable",
                blocking_violations=[
                    {
                        "type": "temporal_conflict",
                        "severity": "high",
                        "detail": "Flashback markers are mixed into the current scene.",
                        "suggested_strategy": "rewrite_scene",
                        "repair_scope": "prose_text",
                        "repairable_by_text": True,
                        "blocks_commit": True,
                    }
                ],
            )
        ],
    )

    validated = asyncio.run(FBIChapterCaseIntakeService().intake(case))
    plan = asyncio.run(FBIChapterRepairPlanner().plan(validated))

    assert plan.status == "needs_repair"
    assert len(plan.orders) == 1
    assert plan.orders[0].repair_type == "local_patch"
