import asyncio
import sqlite3

from app.models.chapter_review import (
    ChapterRepairOrder,
    ChapterRepairPlan,
    RepairWorkUnit,
    ToolCommand,
    ToolCommandBatch,
)
from app.models.fbi_diagnosis import (
    FBIConflictDecision,
    FBIRevisionBlueprint,
    FBIReviewDiagnosis,
    FBIReviewDiagnosisSet,
)
from app.agents.fbi.review_blueprint_agent import FBIReviewBlueprintAgent
from app.services.fbi.chapter_case_intake import (
    FBIChapterCaseIntakeService,
    FBIChapterRepairPlanner,
)
from app.services.fbi.chapter_repair_executor import ChapterRepairExecutor
from app.models.chapter_review import ChapterReviewCase, SceneReviewPacket
from app.services.review_case_file import ReviewCaseFileBuilder
from app.services.fbi.tool_executor import ToolExecutor
from app.services.skill_metric_repair_registry import all_skill_metric_specs


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


def test_tool_executor_applies_atomic_replace_work_unit():
    unit = RepairWorkUnit(
        work_unit_id="wu-1",
        owner_scene=0,
        target_scenes=[0],
        source_order_ids=["order-1"],
        tool_batch=ToolCommandBatch(
            batch_id="batch-1",
            target_scenes=[0],
            source_order_ids=["order-1"],
            commands=[
                    ToolCommand(
                        command_id="cmd-1",
                        operation="replace_exact",
                        scene_index=0,
                        target_span="old claim",
                        replacement="new claim",
                        anchor_text="old claim",
                        old_text="old claim",
                        new_text="new claim",
                        source_issue_ids=["issue-1"],
                        expected_metric_delta={"direction": "resolve"},
                        guards={"preserve_facts": True},
                        postconditions={
                            "forbidden_spans": ["old claim"],
                            "required_spans": ["new claim"],
                    },
                )
            ],
        ),
    )

    updated, audits = _execute_via_patches([unit], {0: "This has old claim."})

    assert updated[0] == "This has new claim."
    assert audits["wu-1"]["accepted"] is True
    assert audits["wu-1"]["changed"] is True


def test_tool_executor_uses_whitespace_insensitive_anchor_fallback_for_insert():
    unit = RepairWorkUnit(
        work_unit_id="wu-anchor-fallback",
        owner_scene=0,
        target_scenes=[0],
        source_order_ids=["order-anchor"],
        tool_batch=ToolCommandBatch(
            batch_id="batch-anchor",
            target_scenes=[0],
            source_order_ids=["order-anchor"],
            commands=[
                    ToolCommand(
                        command_id="cmd-anchor",
                        operation="insert_after_anchor",
                        scene_index=0,
                        anchor_text="He breathed slowly.",
                        after_span="He breathed slowly.",
                        replacement="\n\nThe sound outside came closer.",
                        new_text="\n\nThe sound outside came closer.",
                        source_issue_ids=["issue-anchor"],
                        expected_metric_delta={"direction": "resolve"},
                        guards={"preserve_facts": True},
                        postconditions={"required_spans": ["The sound outside came closer."]},
                    )
            ],
        ),
    )
    text = "She pulled him behind the tree.\n\nHe breathed slowly."

    updated, audits = _execute_via_patches([unit], {0: text})

    assert audits["wu-anchor-fallback"]["accepted"] is True
    assert "The sound outside came closer." in updated[0]


def test_tool_executor_rejects_mid_sentence_insert_anchor():
    unit = RepairWorkUnit(
        work_unit_id="wu-mid-sentence-insert",
        owner_scene=0,
        target_scenes=[0],
        source_order_ids=["order-mid-sentence"],
        tool_batch=ToolCommandBatch(
            commands=[ToolCommand(
                operation="insert_after_anchor",
                scene_index=0,
                anchor_text="She turned",
                after_span="She turned",
                new_text=" The warning arrived.",
                replacement=" The warning arrived.",
                source_issue_ids=["issue-mid-sentence"],
            )],
        ),
    )

    updated, audits = _execute_via_patches(
        [unit], {0: "She turned toward the door."}
    )

    assert updated[0] == "She turned toward the door."
    assert audits[unit.work_unit_id]["accepted"] is False
    assert audits[unit.work_unit_id]["reason"] == "insert_anchor_not_at_narrative_boundary"


def test_tool_executor_rejects_control_plane_text_as_narrative_patch():
    unit = RepairWorkUnit(
        work_unit_id="wu-meta-prose",
        owner_scene=0,
        target_scenes=[0],
        source_order_ids=["order-meta-prose"],
        tool_batch=ToolCommandBatch(
            commands=[ToolCommand(
                operation="replace_exact",
                scene_index=0,
                old_text="She stopped.",
                target_span="She stopped.",
                anchor_text="She stopped.",
                new_text="本段落需要增加动作细节。",
                replacement="本段落需要增加动作细节。",
                source_issue_ids=["issue-meta-prose"],
            )],
        ),
    )

    updated, audits = _execute_via_patches([unit], {0: "She stopped."})

    assert updated[0] == "She stopped."
    assert audits[unit.work_unit_id]["accepted"] is False
    assert audits[unit.work_unit_id]["reason"] == "narrative_meta_text_rejected"


def test_tool_executor_uses_whitespace_insensitive_anchor_fallback_for_replace():
    unit = RepairWorkUnit(
        work_unit_id="wu-replace-fallback",
        owner_scene=0,
        target_scenes=[0],
        source_order_ids=["order-replace"],
        tool_batch=ToolCommandBatch(
            batch_id="batch-replace",
            target_scenes=[0],
            source_order_ids=["order-replace"],
            commands=[
                    ToolCommand(
                        command_id="cmd-replace",
                        operation="replace_exact",
                        scene_index=0,
                        target_span="The sky was already\nbright.",
                        replacement="The sky was still dark.",
                        anchor_text="The sky was already\nbright.",
                        old_text="The sky was already\nbright.",
                        new_text="The sky was still dark.",
                        source_issue_ids=["issue-replace"],
                        expected_metric_delta={"direction": "resolve"},
                        guards={"preserve_facts": True},
                        postconditions={
                            "required_spans": ["The sky was still dark."],
                            "forbidden_spans": ["The sky was already bright."],
                    },
                )
            ],
        ),
    )
    text = "The sky was already\nbright."

    updated, audits = _execute_via_patches([unit], {0: text})

    assert audits["wu-replace-fallback"]["accepted"] is True
    assert updated[0] == "The sky was still dark."


def test_low_conflict_density_is_advisory_and_not_auto_repaired():
    text = (
        "She pulled him behind the tree. "
        "He breathed slowly and looked at the herbs. "
        "\"Where are we?\" she asked. "
        "She opened the packet and waited."
    )
    case = ChapterReviewCase(
        project_id="project",
        chapter_number=19,
        scene_packets=[
            SceneReviewPacket(
                scene_index=0,
                candidate_text=text,
                blocking_violations=[
                    {
                        "type": "low_conflict_density",
                        "metric": "low_conflict_density",
                        "source": "final_acceptance",
                        "severity": "high",
                        "detail": "Conflict density is too low.",
                        "expected_behavior": "让阻碍通过人物互动、时间限制、资源代价或现场变化出现。",
                        "blocks_commit": True,
                    }
                ],
            )
        ],
    )

    validated = asyncio.run(FBIChapterCaseIntakeService().intake(case))
    plan = asyncio.run(FBIChapterRepairPlanner().plan(validated))

    packet = validated.scene_packets[0]
    assert packet.blocking_violations == []
    assert packet.advisory_violations[0]["metric"] == "low_conflict_density"
    assert packet.advisory_violations[0]["blocks_commit"] is False
    assert plan.status == "clean"
    assert plan.orders == []
    assert plan.work_units == []


def test_missing_micro_payoff_is_advisory_and_not_auto_repaired():
    text = (
        "She mixed the herbs and watched the color settle. "
        "The old man finally opened his eyes. "
        "She held her breath."
    )
    case = ChapterReviewCase(
        project_id="project",
        chapter_number=19,
        scene_packets=[
            SceneReviewPacket(
                scene_index=0,
                candidate_text=text,
                blocking_violations=[
                    {
                        "type": "missing_micro_payoff",
                        "metric": "missing_micro_payoff",
                        "source": "final_acceptance",
                        "severity": "high",
                        "detail": "A local attempt lacks a small visible payoff.",
                        "blocks_commit": True,
                    }
                ],
            )
        ],
    )

    validated = asyncio.run(FBIChapterCaseIntakeService().intake(case))
    plan = asyncio.run(FBIChapterRepairPlanner().plan(validated))

    packet = validated.scene_packets[0]
    assert packet.blocking_violations == []
    assert packet.advisory_violations[0]["metric"] == "missing_micro_payoff"
    assert packet.advisory_violations[0]["blocks_commit"] is False
    assert plan.status == "clean"
    assert plan.orders == []
    assert plan.work_units == []


def test_chapter_level_untyped_anti_ai_failures_remain_advisory():
    text = (
        "凤溪把丹药压在舌下，药力像微光一样亮了一下。"
        "她仿佛听见溪水深处有东西擦过石面。"
        "五师兄没有说话，只把剑鞘往怀里收紧。"
        "凤溪低头确认布袋还在，指尖按住袋口。"
        "她抬眼看向水面，呼吸放得更轻。"
    )
    case_file = ReviewCaseFileBuilder().build(
        project_id="project",
        chapter_number=19,
        draft_text=text,
        skill_validation={
            "failures": [
                {
                    "validator": "ai_flavor",
                    "severity": "high",
                    "detail": "Tier 1 AI-flavor vocabulary exceeds the skill contract.",
                    "blocks_commit": True,
                },
                {
                    "validator": "rhythm_metrics",
                    "severity": "high",
                    "detail": "Too many adjacent sentences stay in the same length band.",
                    "blocks_commit": True,
                },
            ]
        },
    )
    case = ChapterReviewCase(
        case_id=case_file.case_id,
        project_id="project",
        chapter_number=19,
        review_case_file=case_file.model_dump(),
        scene_packets=[
            SceneReviewPacket(
                scene_index=0,
                candidate_text=text,
                blocking_violations=[],
            )
        ],
    )

    validated = asyncio.run(FBIChapterCaseIntakeService().intake(case))
    plan = asyncio.run(FBIChapterRepairPlanner().plan(validated))

    packet = validated.scene_packets[0]
    assert packet.blocking_violations == []
    assert len(packet.advisory_violations) == 2
    assert all(item["blocks_commit"] is False for item in packet.advisory_violations)
    assert plan.status == "clean"
    assert plan.orders == []
    assert plan.work_units == []


def test_emotion_label_remains_localizable_advisory_without_auto_repair():
    text = "凤溪停在溪边。五师兄很害怕，手一直没有松开树根。"
    case = ChapterReviewCase(
        project_id="project",
        chapter_number=19,
        scene_packets=[
            SceneReviewPacket(
                scene_index=0,
                candidate_text=text,
                blocking_violations=[
                    {
                        "type": "unclassified_review_issue",
                        "metric": "emotion_label_count",
                        "validator": "scene_evidence",
                        "severity": "high",
                        "target_span": "五师兄很害怕",
                        "detail": "Emotion labels remain where observable evidence is required.",
                        "blocks_commit": True,
                    }
                ],
            )
        ],
    )

    validated = asyncio.run(FBIChapterCaseIntakeService().intake(case))
    plan = asyncio.run(FBIChapterRepairPlanner().plan(validated))

    packet = validated.scene_packets[0]
    assert packet.blocking_violations == []
    assert packet.advisory_violations[0]["type"] == "emotion_label_count"
    assert packet.advisory_violations[0]["target_span"] == "五师兄很害怕"
    assert packet.advisory_violations[0]["blocks_commit"] is False
    assert plan.status == "clean"
    assert plan.work_units == []


def test_review_minister_blueprints_non_pov_inner_access_as_voice_tool():
    text = "凤溪扶住树根。五师兄心里一沉，慢慢睁开眼。"
    case = ChapterReviewCase(
        project_id="project",
        chapter_number=19,
        scene_packets=[
            SceneReviewPacket(
                scene_index=0,
                candidate_text=text,
                blocking_violations=[
                    {
                        "type": "unclassified_review_issue",
                        "metric": "head_hopping_count",
                        "validator": "pov_consistency",
                        "severity": "high",
                        "target_span": "五师兄心里一沉",
                        "detail": "Inner access to non-POV characters exceeds the skill contract.",
                        "blocks_commit": True,
                    }
                ],
            )
        ],
    )

    validated = asyncio.run(FBIChapterCaseIntakeService().intake(case))
    plan = asyncio.run(FBIChapterRepairPlanner().plan(validated))

    command = plan.work_units[0].tool_batch.commands[0]
    assert command.operation == "llm_creative_rewrite"
    assert command.target_span == ""
    assert plan.work_units[0].placement_status == "required"
    assert not command.new_text


def test_review_minister_blueprints_goal_present_as_contract_completion_tool():
    text = "凤溪把布袋系紧，看向裂隙。五师兄站到她身侧。"
    case = ChapterReviewCase(
        project_id="project",
        chapter_number=19,
        scene_packets=[
            SceneReviewPacket(
                scene_index=0,
                candidate_text=text,
                blocking_violations=[
                    {
                        "type": "goal_present",
                        "metric": "goal_present",
                        "validator": "scene_contract",
                        "severity": "high",
                        "detail": "场景结束状态要求与五师兄配合，利用前世记忆中的药草炼丹，但正文尚未开始炼丹。",
                        "target_span": "五师兄站到她身侧。",
                        "blocks_commit": True,
                    }
                ],
            )
        ],
    )

    validated = asyncio.run(FBIChapterCaseIntakeService().intake(case))
    plan = asyncio.run(FBIChapterRepairPlanner().plan(validated))

    assert plan.work_units
    command = plan.work_units[0].tool_batch.commands[0]
    assert command.operation == "llm_creative_rewrite"
    assert not command.replacement
    assert "炼丹" not in command.new_text


def test_review_minister_blueprints_missing_temporal_anchor_as_insert_time_anchor_tool():
    text = "凤溪扶着树根站稳。她把药袋塞进口袋，继续往前走。"
    case = ChapterReviewCase(
        project_id="project",
        chapter_number=19,
        scene_packets=[
            SceneReviewPacket(
                scene_index=0,
                candidate_text=text,
                blocking_violations=[
                    {
                        "type": "unclassified_review_issue",
                        "metric": "temporal_anchor_count",
                        "validator": "rhythm_metrics",
                        "severity": "high",
                        "detail": "Scene lacks any observable temporal anchor.",
                        "blocks_commit": True,
                    }
                ],
            )
        ],
    )

    validated = asyncio.run(FBIChapterCaseIntakeService().intake(case))
    plan = asyncio.run(FBIChapterRepairPlanner().plan(validated))

    command = plan.work_units[0].tool_batch.commands[0]
    assert command.operation == "llm_creative_rewrite"
    assert command.anchor_text == ""
    assert plan.work_units[0].placement_status == "required"
    assert not command.new_text


def test_review_minister_blueprints_pure_exposition_block_as_split_tool():
    text = "这说明她必须重新判断局面，因此她意识到过去的安排已经失效，所以眼前的选择只剩下一条。她把药袋攥紧。"
    case = ChapterReviewCase(
        project_id="project",
        chapter_number=19,
        scene_packets=[
            SceneReviewPacket(
                scene_index=0,
                candidate_text=text,
                blocking_violations=[
                    {
                        "type": "unclassified_review_issue",
                        "metric": "pure_exposition_block_chars",
                        "validator": "scene_structure",
                        "severity": "high",
                        "target_span": "因此她意识到过去的安排已经失效",
                        "detail": "Pure exposition block exceeds the scene structure contract.",
                        "blocks_commit": True,
                    }
                ],
            )
        ],
    )

    validated = asyncio.run(FBIChapterCaseIntakeService().intake(case))
    plan = asyncio.run(FBIChapterRepairPlanner().plan(validated))

    command = plan.work_units[0].tool_batch.commands[0]
    assert command.operation == "llm_creative_rewrite"
    assert not command.new_text


def test_review_minister_localizes_fact_diagnostic_into_replace_literal_work_unit():
    text = "凤溪在后山回廊停了一息。\n\n外面的天已经亮了，林子里潮气很重。"
    case = ChapterReviewCase(
        project_id="project",
        chapter_number=19,
        scene_packets=[
            SceneReviewPacket(
                scene_index=0,
                candidate_text=text,
                blocking_violations=[
                    {
                        "type": "temporal_conflict",
                        "metric": "temporal_conflict",
                        "severity": "critical",
                        "detail": (
                            "Text claim '外面的天已经亮了' conflicts with established fact "
                            "'夜半至黎明前'; suggested correction: '外面的天还没亮透'"
                        ),
                        "target_span": "Text claim '外面的天已经亮了' conflicts with established fact.",
                        "blocks_commit": True,
                    }
                ],
            )
        ],
    )

    validated = asyncio.run(FBIChapterCaseIntakeService().intake(case))
    plan = asyncio.run(FBIChapterRepairPlanner().plan(validated))

    command = plan.work_units[0].tool_batch.commands[0]
    assert command.operation == "replace_exact"
    assert command.old_text == "外面的天已经亮了"
    assert command.new_text == "外面的天还没亮透"

    updated, updated_plan = asyncio.run(
        ChapterRepairExecutor().execute(
            plan,
            {0: text},
            context={},
        )
    )

    assert "外面的天还没亮透" in updated[0]
    assert "外面的天已经亮了" not in updated[0]
    assert updated_plan.orders[0].status == "succeeded"


def test_structure_word_cluster_is_advisory_without_auto_cleanup():
    text = "这说明她必须继续。因此她抬头看向林子。与此同时风从洞口压进来。"
    case = ChapterReviewCase(
        project_id="project",
        chapter_number=19,
        scene_packets=[
            SceneReviewPacket(
                scene_index=0,
                candidate_text=text,
                blocking_violations=[
                    {
                        "type": "structure_word_cluster_count",
                        "metric": "structure_word_cluster_count",
                        "validator": "ai_flavor",
                        "severity": "high",
                        "target_span": text,
                        "detail": "Structure-word clusters exceed the skill contract.",
                        "blocks_commit": True,
                    }
                ],
            )
        ],
    )

    validated = asyncio.run(FBIChapterCaseIntakeService().intake(case))
    plan = asyncio.run(FBIChapterRepairPlanner().plan(validated))

    packet = validated.scene_packets[0]
    assert packet.blocking_violations == []
    assert packet.advisory_violations[0]["metric"] == "structure_word_cluster_count"
    assert packet.advisory_violations[0]["blocks_commit"] is False
    assert plan.status == "clean"
    assert plan.work_units == []


def test_tool_executor_inserts_hook_and_micro_payoff_beats():
    text = "她把药草压进石臼里。五师兄的呼吸仍旧很浅。"
    units = [
        RepairWorkUnit(
            work_unit_id="wu-hook",
            owner_scene=0,
            target_scenes=[0],
            source_order_ids=["order-hook"],
            tool_batch=ToolCommandBatch(
                commands=[
                    ToolCommand(
                        operation="insert_after_anchor",
                        scene_index=0,
                        target_span="五师兄的呼吸仍旧很浅。",
                        anchor_text="五师兄的呼吸仍旧很浅。",
                        after_span="五师兄的呼吸仍旧很浅。",
                        replacement="更麻烦的是，洞外还有一道脚步声没有停。",
                        new_text="更麻烦的是，洞外还有一道脚步声没有停。",
                        source_issue_ids=["issue-hook"],
                        expected_metric_delta={"direction": "resolve"},
                        guards={"preserve_facts": True},
                        postconditions={"required_spans": ["更麻烦的是，洞外还有一道脚步声没有停。"]},
                    )
                ]
            ),
        ),
        RepairWorkUnit(
            work_unit_id="wu-payoff",
            owner_scene=0,
            target_scenes=[0],
            source_order_ids=["order-payoff"],
            dependencies=["wu-hook"],
            tool_batch=ToolCommandBatch(
                commands=[
                    ToolCommand(
                        operation="insert_after_anchor",
                        scene_index=0,
                        target_span="她把药草压进石臼里。",
                        anchor_text="她把药草压进石臼里。",
                        after_span="她把药草压进石臼里。",
                        replacement="药汁一落下，五师兄的指尖终于回了一点温度。",
                        new_text="药汁一落下，五师兄的指尖终于回了一点温度。",
                        source_issue_ids=["issue-payoff"],
                        expected_metric_delta={"direction": "resolve"},
                        guards={"preserve_facts": True},
                        postconditions={"required_spans": ["药汁一落下，五师兄的指尖终于回了一点温度。"]},
                    )
                ]
            ),
        ),
    ]

    updated, audits = _execute_via_patches(units, {0: text})

    assert audits["wu-hook"]["accepted"] is True
    assert audits["wu-payoff"]["accepted"] is True
    assert "脚步声没有停" in updated[0]
    assert "指尖终于回了一点温度" in updated[0]


def test_tool_executor_split_paragraph_can_find_target_without_index():
    unit = RepairWorkUnit(
        work_unit_id="wu-split",
        owner_scene=0,
        target_scenes=[0],
        source_order_ids=["order-split"],
        tool_batch=ToolCommandBatch(
            batch_id="batch-split",
            target_scenes=[0],
            source_order_ids=["order-split"],
            commands=[
                ToolCommand(
                    command_id="cmd-split",
                    operation="replace_exact",
                    scene_index=0,
                    target_span="She finally stopped.",
                    anchor_text="She finally stopped.",
                    old_text="She finally stopped.",
                    new_text="She finally stopped.\n\n",
                    replacement="She finally stopped.\n\n",
                    source_issue_ids=["issue-split"],
                    expected_metric_delta={"direction": "resolve"},
                    guards={"preserve_facts": True},
                )
            ],
        ),
    )

    updated, audits = _execute_via_patches(
        [unit],
        {0: "She ran through the hall. She finally stopped. The door opened."},
    )

    assert audits["wu-split"]["accepted"] is True
    assert "She finally stopped.\n\n The door opened." in updated[0]


def test_executor_does_not_run_work_unit_with_missing_dependency():
    unit = RepairWorkUnit(
        work_unit_id="wu-dependent",
        owner_scene=0,
        target_scenes=[0],
        source_order_ids=["order-1"],
        dependencies=["wu-missing"],
        tool_batch=ToolCommandBatch(commands=[ToolCommand(
            command_id="cmd-1",
            operation="replace_exact",
            scene_index=0,
            target_span="old claim",
            replacement="new claim",
            anchor_text="old claim",
            old_text="old claim",
            new_text="new claim",
            source_issue_ids=["issue-1"],
            expected_metric_delta={"direction": "resolve"},
            guards={"preserve_facts": True},
        )]),
    )
    order = ChapterRepairOrder(
        order_id="order-1",
        target_scenes=[0],
        owner_scene=0,
        repair_type="manual_review",
    )
    plan = ChapterRepairPlan(
        case_id="case-dependency",
        status="needs_repair",
        orders=[order],
        work_units=[unit],
    )

    updated, updated_plan = asyncio.run(
        ChapterRepairExecutor().execute(plan, {0: "old claim"})
    )

    assert updated[0] == "old claim"
    audit = updated_plan.repair_execution_summary["tool_execution"]["audits"]["wu-dependent"]
    assert audit["reason"] == "missing_required_dependencies"


def test_blueprint_translates_order_dependencies_to_work_unit_dependencies():
    first = ChapterRepairOrder(
        order_id="order-a",
        target_scenes=[0],
        owner_scene=0,
        repair_brief={"patch_plan": [{"from": "A", "to": "B"}]},
    )
    second = ChapterRepairOrder(
        order_id="order-b",
        target_scenes=[1],
        owner_scene=1,
        dependencies=["order-a"],
        repair_brief={"patch_plan": [{"from": "C", "to": "D"}]},
    )
    from app.services.fbi.work_unit_builder import build_revision_blueprint

    blueprint = build_revision_blueprint("case-dependency-map", [first, second])
    unit_by_order = {
        order_id: unit
        for unit in blueprint.work_units
        for order_id in unit.source_order_ids
    }

    assert unit_by_order["order-b"].dependencies == [unit_by_order["order-a"].work_unit_id]


def test_planner_builds_auditable_work_unit_for_hard_contract_violation():
    # 硬阻断禁令必须形成可审计 work unit；语义改写不得伪装成确定性替换。
    case = ChapterReviewCase(
        project_id="project-1",
        chapter_number=7,
        scene_packets=[
            SceneReviewPacket(
                scene_index=0,
                repairability="auto_fixable",
                blocking_violations=[
                    {
                        "type": "forbidden_triggered",
                        "severity": "high",
                        "detail": "Forbidden word 'red herb' detected.",
                        "target_span": "red herb",
                        "suggestion": "blue herb",
                        "blocks_commit": True,
                        "repair_intent": {
                            "repair_lane": "style_local_patch",
                            "patch_plan": [
                                {
                                    "operation": "replace_phrase",
                                    "from": "red herb",
                                    "to": "blue herb",
                                }
                            ],
                        },
                    }
                ],
            )
        ],
    )

    validated = asyncio.run(FBIChapterCaseIntakeService().intake(case))
    plan = asyncio.run(FBIChapterRepairPlanner().plan(validated))

    assert plan.work_units
    assert plan.revision_blueprint.status == "ready"
    assert plan.repair_strategy_summary["revision_blueprint"]["executable_blueprint_rate"] == 1.0
    assert plan.repair_strategy_summary["revision_blueprint"]["blueprint_completion_required_count"] == 0
    command = plan.work_units[0].tool_batch.commands[0]
    assert command.operation == "llm_creative_rewrite"
    assert command.source_issue_ids


def test_blueprint_builds_tool_command_from_review_minister_conflict_decision():
    order = ChapterRepairOrder(
        order_id="order-conflict",
        target_scenes=[0],
        owner_scene=0,
        repair_type="local_patch",
        repair_brief={
            "review_minister": {
                "conflict_decisions": [
                    {
                        "current_claim": "red herb",
                        "authority_claim": "blue herb",
                        "decision": "replace_current_with_authority",
                        "rationale": "Authority fact wins.",
                    }
                ]
            }
        },
        source_violation_ids=["v-conflict"],
    )
    from app.services.fbi.work_unit_builder import build_revision_blueprint

    blueprint = build_revision_blueprint("case-conflict", [order])

    assert blueprint.status == "ready"
    assert len(blueprint.work_units) == 1
    command = blueprint.work_units[0].tool_batch.commands[0]
    assert command.operation == "replace_exact"
    assert command.old_text == "red herb"
    assert command.new_text == "blue herb"
    assert command.postconditions["forbidden_spans"] == ["red herb"]
    assert command.postconditions["required_spans"] == ["blue herb"]


def test_native_review_minister_blueprint_builds_work_unit_without_order_projection():
    from app.services.fbi.work_unit_builder import build_revision_blueprint_from_diagnoses

    diagnosis = FBIReviewDiagnosis(
        diagnosis_id="diag-1",
        issue_ids=["iss-1"],
        scene_index=0,
        issue_family="fact",
        problem_statement="Use the authority herb name.",
        conflict_decisions=[
            FBIConflictDecision(
                current_claim="red herb",
                authority_claim="blue herb",
                decision="replace_current_with_authority",
            )
        ],
    )

    blueprint = build_revision_blueprint_from_diagnoses(
        "case-native",
        [diagnosis],
        issue_to_order_ids={"iss-1": ["order-1"]},
    )

    assert blueprint.status == "ready"
    assert blueprint.work_units[0].source_order_ids == ["order-1"]
    assert blueprint.work_units[0].edit_window["source"] == "review_minister_native"
    command = blueprint.work_units[0].tool_batch.commands[0]
    assert command.operation == "replace_exact"
    assert command.old_text == "red herb"
    assert command.new_text == "blue herb"


def test_review_minister_emits_bounded_split_for_localized_rhythm_issue():
    from app.services.fbi.review_minister import FBIReviewMinister
    from app.services.fbi.work_unit_builder import build_revision_blueprint_from_diagnoses

    case = ChapterReviewCase(project_id="project-1", chapter_number=1, case_id="case-rhythm")
    diagnosis_set = FBIReviewMinister().diagnose_violations(case, [{
        "issue_id": "issue-rhythm",
        "type": "dense_paragraph",
        "metric": "reader_breathing",
        "source_scene": 0,
        "target_span": "She finally stopped.",
        "detail": "Dense paragraph runs too long without a reader-breathing beat.",
        "blocks_commit": True,
    }])

    blueprint = build_revision_blueprint_from_diagnoses(
        "case-rhythm",
        diagnosis_set.diagnoses,
        issue_to_order_ids={"issue-rhythm": ["order-rhythm"]},
    )

    assert blueprint.status == "ready"
    command = blueprint.work_units[0].tool_batch.commands[0]
    assert command.operation == "replace_exact"
    assert command.old_text == "She finally stopped."


def test_blueprint_marks_mixed_tool_and_legacy_plan_as_degraded():
    structured = ChapterRepairOrder(
        order_id="order-structured",
        target_scenes=[0],
        owner_scene=0,
        repair_brief={"patch_plan": [{"from": "A", "to": "B"}]},
    )
    unstructured = ChapterRepairOrder(
        order_id="order-unstructured",
        target_scenes=[0],
        owner_scene=0,
        repair_type="scene_rewrite",
        reason="Improve the scene without a bounded command.",
    )
    from app.services.fbi.work_unit_builder import build_revision_blueprint

    blueprint = build_revision_blueprint("case-degraded", [structured, unstructured])

    assert blueprint.status == "degraded"
    assert len(blueprint.work_units) == 1
    assert any(
        item.get("order_id") == "order-unstructured"
        and item.get("status") == "legacy_executor_required"
        for item in blueprint.planning_trace
    )


def test_executor_uses_tool_work_unit_before_legacy_repairer():
    order = ChapterRepairOrder(
        order_id="order-1",
        target_scenes=[0],
        owner_scene=0,
        repair_type="local_patch",
        repair_domain="fact",
        repair_lane="fact_local_patch",
        repair_brief={
            "patch_plan": [
                {
                    "operation": "replace_phrase",
                    "from": "red herb",
                    "to": "blue herb",
                }
            ]
        },
        source_violation_ids=["v-1"],
        violation_details=[{"type": "fact_conflict", "target_span": "red herb"}],
    )
    plan = ChapterRepairPlan(
        case_id="case-1",
        status="needs_repair",
        orders=[order],
    )

    updated, updated_plan = asyncio.run(
        ChapterRepairExecutor().execute(plan, {0: "She picked the red herb."})
    )

    assert updated[0] == "She picked the blue herb."
    assert updated_plan.orders[0].status == "succeeded"
    assert updated_plan.orders[0].repair_audit["executor_path"] == "fbi_tool_work_unit"
    assert updated_plan.repair_execution_summary["tool_execution"]["accepted"] == 1


def test_executor_generates_same_scene_non_overlapping_candidates_in_one_layer():
    first = ChapterRepairOrder(
        order_id="order-a",
        target_scenes=[0],
        owner_scene=0,
        repair_type="local_patch",
        repair_brief={"patch_plan": [{"from": "red herb", "to": "blue herb"}]},
        source_violation_ids=["issue-a"],
    )
    second = ChapterRepairOrder(
        order_id="order-b",
        target_scenes=[0],
        owner_scene=0,
        repair_type="local_patch",
        repair_brief={"patch_plan": [{"from": "old bowl", "to": "clean bowl"}]},
        source_violation_ids=["issue-b"],
    )
    plan = ChapterRepairPlan(
        case_id="case-same-scene-layer",
        status="needs_repair",
        orders=[first, second],
    )

    updated, updated_plan = asyncio.run(
        ChapterRepairExecutor().execute(
            plan,
            {0: "She picked the red herb and set it beside the old bowl."},
            context={},
        )
    )

    assert updated[0] == "She picked the blue herb and set it beside the clean bowl."
    layers = updated_plan.repair_execution_summary["tool_execution"]["execution_layers"]
    assert len(layers) == 1
    assert set(layers[0]) == {order.work_unit_id for order in updated_plan.orders}
    for unit_audit in updated_plan.repair_execution_summary["tool_execution"]["audits"].values():
        assert unit_audit["candidate_generation"]["base"] == "layer_base_revision"
        assert unit_audit["candidate_generation"]["merge"] == "centralized_replay"
    assert updated_plan.repair_execution_summary["blueprint_coverage"]["executable_order_rate"] == 1.0


def test_executor_marks_unstructured_order_when_legacy_executor_disabled():
    order = ChapterRepairOrder(
        order_id="order-unstructured",
        target_scenes=[0],
        owner_scene=0,
        repair_type="scene_rewrite",
        reason="Improve the scene without a bounded command.",
    )
    plan = ChapterRepairPlan(
        case_id="case-no-legacy",
        status="needs_repair",
        orders=[order],
    )

    updated, updated_plan = asyncio.run(
        ChapterRepairExecutor().execute(
            plan,
            {0: "Original scene text."},
            context={},
        )
    )

    assert updated[0] == "Original scene text."
    assert updated_plan.orders[0].status == "failed"
    assert updated_plan.orders[0].repair_audit["reason"] == "needs_blueprint_completion"
    assert updated_plan.orders[0].repair_audit["executor_path"] == "legacy_executor_disabled"
    assert updated_plan.repair_execution_summary["tool_execution"]["legacy_executor_allowed"] is False


def test_executor_does_not_fallback_after_tool_failure_when_legacy_disabled():
    order = ChapterRepairOrder(
        order_id="order-tool-fails",
        target_scenes=[0],
        owner_scene=0,
        repair_type="local_patch",
        repair_brief={"patch_plan": [{"from": "missing span", "to": "new span"}]},
        source_violation_ids=["v-tool"],
    )
    plan = ChapterRepairPlan(
        case_id="case-tool-fails-no-legacy",
        status="needs_repair",
        orders=[order],
    )

    updated, updated_plan = asyncio.run(
        ChapterRepairExecutor().execute(
            plan,
            {0: "Original scene text."},
            context={},
        )
    )

    assert updated[0] == "Original scene text."
    audit = updated_plan.orders[0].repair_audit
    assert updated_plan.orders[0].status == "failed"
    assert audit["reason"] == "needs_blueprint_completion"
    assert audit["had_tool_work_unit"] is True
    assert audit["executor_path"] == "legacy_executor_disabled"


def test_validator_protocol_compares_ai_discourse_metric():
    from app.services.validator_protocol import ValidatorProtocolAdapter

    before = "\n\n".join(
        "她把药草放进布袋，又把绳结重新勒紧，指节贴着粗糙的布纹。这说明她必须继续向前。"
        for _ in range(12)
    )
    after = "她把药草放进布袋，重新勒紧绳结。\n\n山风从洞口压进来，她停了一息，才继续往前。"

    result = ValidatorProtocolAdapter().compare(
        "paragraph_shape_repeat_count",
        before,
        after,
        expected=1,
    )

    assert result["supported"] is True
    assert result["validator"] == "ai_discourse"
    assert result["source"] == "ai_discourse_checker"
    assert result["passed"] is True


def test_validator_protocol_compares_rhythm_metric():
    from app.services.validator_protocol import ValidatorProtocolAdapter

    before = "她走。她停。她看。她听。她等。她退。"
    after = "她停在洞口，等风声从石缝里散尽，才把那只布袋重新背稳。"

    result = ValidatorProtocolAdapter().compare(
        "uniform_sentence_streak_max",
        before,
        after,
    )

    assert result["supported"] is True
    assert result["validator"] == "rhythm_metrics"
    assert result["source"] == "agent_skill_validator.rhythm_metrics"


def test_validator_protocol_treats_flashback_correctness_as_positive_boolean():
    from app.services.validator_protocol import ValidatorProtocolAdapter

    adapter = ValidatorProtocolAdapter()

    assert adapter.default_direction("flashback_tense_correct") == "increase"
    assert adapter.evaluate_progress(
        "flashback_tense_correct",
        False,
        True,
        True,
        direction="increase",
    ) is True


def test_validator_protocol_compares_ai_flavor_metric():
    from app.services.validator_protocol import ValidatorProtocolAdapter

    before = "She paused鈥斺€攖hen waited鈥斺€攖hen turned鈥斺€攖hen left."
    after = "She paused, then waited. Then she turned and left."

    result = ValidatorProtocolAdapter().compare(
        "dash_per_1000",
        before,
        after,
        expected=2.5,
    )

    assert result["supported"] is True
    assert result["validator"] == "ai_flavor"
    assert result["source"] == "ai_flavor_checker"
    assert result["passed"] is True


def test_validator_protocol_measures_long_tail_skill_validators():
    from app.services.validator_protocol import ValidatorProtocolAdapter

    adapter = ValidatorProtocolAdapter()
    text = "她把药草放进布袋。山风压过洞口，她停了一息，才继续往前。"
    context = {
        "scene_contract": {
            "pov": "她",
            "goal": "采药",
            "ending_state": "继续往前",
        },
        "pov_character_card": {
            "name": "她",
            "voice_fingerprint": {
                "preferred_markers": ["药草"],
                "avoided_words": ["莫名的"],
            },
        },
        "narrative_config": {"pov_type": "third_person_limited"},
        "scene_elements": ["药草", "洞口"],
    }

    checks = [
        ("voice_fingerprint", "generic_voice_density"),
        ("literary_quality", "specificity"),
        ("scene_structure", "goal_present"),
        ("narrative_experience", "mode_fit"),
        ("pov_consistency", "head_hopping_count"),
        ("tense_consistency", "tense_drift_count"),
        ("scene_evidence", "concrete_evidence_count"),
        ("specificity_budget", "detail_hit_count"),
    ]
    for validator, metric in checks:
        measurement = adapter.measure(validator, metric, text, context)
        assert measurement.status != "unsupported", (validator, metric)
        assert measurement.validator == validator
        assert measurement.source


def test_validator_protocol_evaluate_and_localize():
    from app.services.validator_protocol import ValidatorProtocolAdapter

    text = "她停在洞口——不是害怕，而是在等风声过去。"
    adapter = ValidatorProtocolAdapter()

    evaluation = adapter.evaluate(
        "ai_discourse",
        "sentence_shell_count",
        text,
        expected=1,
        operator="lte",
    )
    localized = adapter.localize(
        {"type": "dash_per_1000", "detail": "dash density high"},
        text,
    )

    assert evaluation["supported"] is True
    assert "confidence" in evaluation
    assert localized.get("localization_status") == "localized"
    assert localized.get("target_span")


def test_validator_protocol_measure_cache_stats():
    from app.services.validator_protocol import ValidatorProtocolAdapter

    adapter = ValidatorProtocolAdapter()
    text = "她把药草放进布袋，又把绳结重新勒紧。"

    first = adapter.measure("ai_discourse", "paragraph_shape_repeat_count", text)
    second = adapter.measure("ai_discourse", "paragraph_shape_repeat_count", text)

    assert first == second
    assert adapter.cache_stats()["entries"] == 1
    assert adapter.cache_stats()["hits"] == 1
    assert adapter.cache_stats()["misses"] == 1


def test_validator_protocol_snapshot_changes_measurement_cache_key():
    from app.services.validator_protocol import ValidatorProtocolAdapter

    adapter = ValidatorProtocolAdapter()
    text = "濂规妸鑽崏鏀捐繘甯冭锛屽張鎶婄怀缁撻噸鏂板嫆绱с€?"
    default = adapter.measure("ai_discourse", "paragraph_shape_repeat_count", text)
    snapshot = adapter.build_snapshot(criteria=[{
        "validator": "ai_discourse",
        "metric": "paragraph_shape_repeat_count",
        "operator": "lte",
        "expected": 1,
    }])
    scoped = adapter.measure(
        "ai_discourse",
        "paragraph_shape_repeat_count",
        text,
        context={"validator_snapshot": snapshot},
    )

    assert default == scoped
    assert adapter.cache_stats()["entries"] == 2
    assert snapshot["contract_hash"]


def test_blueprint_carries_validator_snapshot_for_work_units():
    from app.services.fbi.work_unit_builder import build_revision_blueprint

    order = ChapterRepairOrder(
        order_id="order-dash",
        target_scenes=[0],
        owner_scene=0,
        repair_lane="deterministic_surface_cleanup",
        expected_after_repair={"max_dash_count": 1},
        violation_details=[{"metric": "dash_per_1000", "target_span": "鈥斺€?"}],
    )

    blueprint = build_revision_blueprint("case-snapshot", [order])

    assert blueprint.validator_snapshot["protocol"] == "loreweft_validator_protocol"
    assert blueprint.validator_snapshot["contract_hash"]
    assert blueprint.work_units[0].validator_snapshot["contract_hash"] == blueprint.validator_snapshot["contract_hash"]
    assert blueprint.completion_summary["work_unit_count"] == 1


def test_fbi_workflow_metrics_aggregate_plan_and_final_delta_cycle():
    from app.services.fbi.workflow_metrics import FBIWorkflowMetrics

    order = ChapterRepairOrder(
        order_id="order-1",
        target_scenes=[0],
        owner_scene=0,
        repair_brief={"patch_plan": [{"from": "A", "to": "B", "span_start": 0, "span_end": 1}]},
    )
    plan = ChapterRepairPlan(case_id="case-metrics", status="needs_repair", orders=[order])
    updated, updated_plan = asyncio.run(
        ChapterRepairExecutor().execute(
            plan,
            {0: "A"},
            context={},
        )
    )

    metrics = FBIWorkflowMetrics.combine(
        plan=updated_plan,
        final_delta_cycle={
            "attempted": True,
            "delta_only": True,
            "initial_issue_count": 1,
            "auto_repairable_issue_count": 1,
            "cycles": [{"changed_scenes": [0], "failed_orders": 0}],
        },
    )

    assert updated[0] == "B"
    assert metrics["repair_plan"]["available"] is True
    assert metrics["repair_plan"]["executable_blueprint_rate"] == 1.0
    assert metrics["repair_plan"]["blueprint_completion_rate"] == 1.0
    assert metrics["repair_plan"]["false_positive_suppression_rate"] == 0.0
    assert metrics["final_delta_cycle"]["cycle_count"] == 1
    assert metrics["final_delta_cycle"]["changed_scene_count"] == 1


def test_fbi_workflow_metrics_reports_false_positive_suppression_rate():
    from app.services.fbi.workflow_metrics import FBIWorkflowMetrics

    order = ChapterRepairOrder(
        order_id="order-fp",
        target_scenes=[0],
        owner_scene=0,
        repair_brief={"tool_commands": [{"operation": "suppress_false_positive", "scene_index": 0}]},
    )
    plan = ChapterRepairPlan(case_id="case-fp", status="needs_repair", orders=[order])

    _updated, updated_plan = asyncio.run(
        ChapterRepairExecutor().execute(
            plan,
            {0: "unchanged text"},
            context={},
        )
    )

    metrics = FBIWorkflowMetrics.combine(plan=updated_plan)

    assert metrics["repair_plan"]["false_positive_suppressed_count"] == 0
    assert metrics["repair_plan"]["false_positive_suppression_rate"] == 0.0


def test_fbi_workflow_metrics_can_summarize_sqlite_workflows():
    from app.services.fbi.workflow_metrics import FBIWorkflowMetrics

    con = sqlite3.connect(":memory:")
    con.execute(
        "create table workflow_executions ("
        "id text primary key, status text, trigger_type text, created_at text, updated_at text, error_message text)"
    )
    con.executemany(
        "insert into workflow_executions values (?, ?, ?, ?, ?, ?)",
        [
            ("wf-1", "completed", "editor_generate", "2026-06-23 00:00:00", "2026-06-23 00:01:00", ""),
            ("wf-2", "waiting_review", "editor_generate", "2026-06-23 00:02:00", "2026-06-23 00:05:00", "needs review"),
        ],
    )

    report = FBIWorkflowMetrics.summarize_sqlite_workflows(con, limit=10)

    assert report["sample_size"] == 2
    assert report["status_counts"]["completed"] == 1
    assert report["status_counts"]["waiting_review"] == 1
    assert report["success_rate"] == 0.5
    assert report["average_duration_seconds"] == 120.0


def test_fact_goal_blueprint_emits_required_span_tool_command():
    from app.services.fbi.work_unit_builder import build_revision_blueprint

    order = ChapterRepairOrder(
        order_id="order-fact-materials",
        target_scenes=[1],
        owner_scene=1,
        repair_type="local_patch",
        repair_domain="fact",
        repair_brief={
            "fact_repair_goal": {
                "required_entities": ["紫蕴草", "赤鳞果", "百年茯苓"],
                "old_error_signatures": ["正文中使用寒髓花和赤精果作为替代材料"],
                "suggested_correction": "统一炼丹材料为紫蕴草、赤鳞果、百年茯苓",
            }
        },
        source_violation_ids=["v-materials"],
    )

    blueprint = build_revision_blueprint("case-materials", [order])

    assert blueprint.status == "ready"
    command = blueprint.work_units[0].tool_batch.commands[0]
    assert command.operation == "replace_exact"
    assert command.old_text
    assert command.new_text
    assert command.postconditions["required_spans"]


def test_executor_repairs_fact_goal_entity_mismatch_with_tool_layer():
    order = ChapterRepairOrder(
        order_id="order-fact-materials",
        target_scenes=[1],
        owner_scene=1,
        repair_type="local_patch",
        repair_domain="fact",
        repair_brief={
            "fact_repair_goal": {
                "required_entities": ["紫蕴草", "赤鳞果", "百年茯苓"],
                "old_error_signatures": ["炼丹需要寒髓花和赤精果"],
            }
        },
        source_violation_ids=["v-materials"],
    )
    plan = ChapterRepairPlan(case_id="case-materials", status="needs_repair", orders=[order])

    updated, updated_plan = asyncio.run(
        ChapterRepairExecutor().execute(
            plan,
            {1: "她把紫蕴草放在石桌上。炼丹需要寒髓花和赤精果。"},
            context={},
        )
    )

    assert "紫蕴草" in updated[1]
    assert "赤鳞果" in updated[1]
    assert "百年茯苓" in updated[1]
    assert "寒髓花" not in updated[1]
    assert "赤精果" not in updated[1]
    assert updated_plan.orders[0].status == "succeeded"
    assert updated_plan.orders[0].repair_audit["executor_path"] == "fbi_tool_work_unit"


def test_active_skill_metrics_have_registry_specs_for_fbi_routing():
    specs = all_skill_metric_specs()
    required = {
        "tier2_cluster_count",
        "false_range_count",
        "vague_attribution_count",
        "chapter_end_moral_count",
        "overexplain_count",
        "semantic_restatement_count",
        "action_then_explanation_count",
        "breathing_paragraph_ratio",
        "transition_paragraph_present",
        "abrupt_shift_count",
        "voice_fingerprint_score",
        "high_advisories",
        "validator_available",
    }

    assert required <= set(specs)
    for metric in required:
        spec = specs[metric]
        assert spec.status in {
            "implemented_tool",
            "implemented_candidate_tool",
            "manual_with_reason",
            "degraded_system_issue",
        }
        if spec.status in {"implemented_tool", "implemented_candidate_tool"}:
            assert spec.operation


def test_subjective_skill_metrics_stay_advisory_until_user_authorizes_repair():
    text = (
        "凤溪把丹药压在舌下，药力像微光一样亮了一下。"
        "她仿佛听见溪水深处有东西擦过石面。"
        "五师兄没有说话，只把剑鞘往怀里收紧。"
        "这说明局势已经发生了某种意义上的变化。"
        "真正重要的不是眼前的困难，而是她终于明白了自己的命运。"
    )
    advisory_metrics = {
        "tier2_cluster_count",
        "false_range_count",
        "semantic_restatement_count",
        "breathing_paragraph_ratio",
        "transition_paragraph_present",
        "abrupt_shift_count",
        "voice_fingerprint_score",
        "high_advisories",
    }

    async def build(metric: str):
        validator = "ai_flavor"
        if metric in {"semantic_restatement_count"}:
            validator = "ai_discourse"
        if metric in {"breathing_paragraph_ratio", "transition_paragraph_present", "abrupt_shift_count"}:
            validator = "rhythm_metrics"
        if metric == "voice_fingerprint_score":
            validator = "voice_fingerprint"
        case_file = ReviewCaseFileBuilder().build(
            project_id="project",
            chapter_number=19,
            draft_text=text,
            skill_validation={
                "failures": [{
                    "validator": validator,
                    "metric": metric,
                    "severity": "high",
                    "reason": f"{metric} exceeds the skill contract.",
                    "blocks_commit": True,
                }]
            },
        )
        case = ChapterReviewCase(
            case_id=case_file.case_id,
            project_id="project",
            chapter_number=19,
            review_case_file=case_file.model_dump(),
            scene_packets=[SceneReviewPacket(scene_index=0, candidate_text=text, blocking_violations=[])],
        )
        validated = await FBIChapterCaseIntakeService().intake(case)
        plan = await FBIChapterRepairPlanner().plan(validated)
        return validated.scene_packets[0], plan

    for metric in advisory_metrics:
        packet, plan = asyncio.run(build(metric))
        assert packet.blocking_violations == []
        assert len(packet.advisory_violations) == 1
        assert packet.advisory_violations[0]["blocks_commit"] is False
        assert plan.status == "clean"
        assert plan.orders == []
        assert plan.work_units == []


def test_localized_high_advisory_preserves_evidence_without_auto_repair():
    text = (
        "She looked at the path. "
        "这说明局势已经发生了某种意义上的变化。 "
        "She moved on."
    )
    case_file = ReviewCaseFileBuilder().build(
        project_id="project",
        chapter_number=19,
        draft_text=text,
        skill_validation={
            "failures": [{
                "validator": "ai_flavor",
                "metric": "high_advisories",
                "type": "high_advisories",
                "severity": "high",
                "reason": "High-severity AI-flavor advisories exceed the skill contract.",
                "repairability": "human_review_required",
                "issue_classification": "manual_only",
                "blocks_commit": True,
                "evidence_spans": [{
                    "span": "这说明局势已经发生了某种意义上的变化。",
                    "role": "evidence_sentence",
                }],
            }]
        },
    )
    case = ChapterReviewCase(
        case_id=case_file.case_id,
        project_id="project",
        chapter_number=19,
        review_case_file=case_file.model_dump(),
        scene_packets=[SceneReviewPacket(scene_index=0, candidate_text=text, blocking_violations=[])],
    )

    validated = asyncio.run(FBIChapterCaseIntakeService().intake(case))
    plan = asyncio.run(FBIChapterRepairPlanner().plan(validated))

    packet = validated.scene_packets[0]
    assert packet.blocking_violations == []
    assert packet.advisory_violations[0]["metric"] == "high_advisories"
    assert packet.advisory_violations[0]["evidence_spans"][0]["span"] == "这说明局势已经发生了某种意义上的变化。"
    assert packet.advisory_violations[0]["blocks_commit"] is False
    assert plan.status == "clean"
    assert plan.orders == []
    assert plan.work_units == []


def test_direct_packet_localized_high_advisory_overrides_stale_manual_metadata():
    text = "She paused. 这说明局势已经发生了某种意义上的变化。 She moved on."
    span = "这说明局势已经发生了某种意义上的变化。"
    case = ChapterReviewCase(
        project_id="project",
        chapter_number=24,
        scene_packets=[SceneReviewPacket(
            scene_index=1,
            candidate_text=text,
            blocking_violations=[{
                "issue_id": "iss-localized-advisory",
                "type": "high_advisories",
                "metric": "high_advisories",
                "severity": "high",
                "blocks_commit": True,
                "repairability": "human_review_required",
                "issue_classification": "manual_only",
                "suggested_strategy": "manual_review",
                "source_scene": 1,
                "target_span": span,
                "evidence": {
                    "localization_status": "localized",
                    "evidence_spans": [{"span": span}],
                },
            }],
        )],
    )

    validated = asyncio.run(FBIChapterCaseIntakeService().intake(case))
    blocking = [
        violation
        for packet in validated.scene_packets
        for violation in [
            *(packet.blocking_violations or []),
            *(packet.advisory_violations or []),
        ]
    ]
    orders = FBIChapterRepairPlanner()._generate_orders(validated, blocking)

    order = next(
        item for item in orders
        if any(
            detail.get("issue_id") == "iss-localized-advisory"
            for detail in item.violation_details
        )
    )
    assert order.repair_type == "local_patch"
    assert order.repair_lane != "manual_review"


def test_top_level_localized_advisory_with_exact_evidence_overrides_stale_manual_metadata():
    text = "She paused. 这说明局势已经发生了某种意义上的变化。 She moved on."
    span = "这说明局势已经发生了某种意义上的变化。"
    case = ChapterReviewCase(
        project_id="project",
        chapter_number=24,
        scene_packets=[SceneReviewPacket(
            scene_index=0,
            candidate_text=text,
            blocking_violations=[{
                "issue_id": "iss-top-localized-advisory",
                "type": "high_advisories",
                "metric": "high_advisories",
                "severity": "high",
                "blocks_commit": True,
                "repairability": "human_review_required",
                "issue_classification": "manual_only",
                "suggested_strategy": "manual_review",
                "source_scene": 0,
                "target_span": span,
                "localization_status": "localized",
                "evidence_spans": [{
                    "span": span,
                    "role": "evidence_sentence",
                }],
                "evidence": {"actual": 1, "expected_max": 0},
            }],
        )],
    )

    validated = asyncio.run(FBIChapterCaseIntakeService().intake(case))
    blocking = [
        *(validated.scene_packets[0].blocking_violations or []),
        *(validated.scene_packets[0].advisory_violations or []),
    ]
    orders = FBIChapterRepairPlanner()._generate_orders(validated, blocking)
    order = next(
        item for item in orders
        if any(
            detail.get("issue_id") == "iss-top-localized-advisory"
            for detail in item.violation_details
        )
    )

    assert order.repair_type == "local_patch"
    assert order.repair_lane != "manual_review"


def test_blueprint_fast_path_rejects_uncompiled_candidate_tool_intent():
    diagnosis = FBIReviewDiagnosis(
        diagnosis_id="diag-candidate-tool",
        issue_ids=["iss-candidate-tool"],
        scene_index=0,
        issue_family="high_advisories",
        revision_blueprint=FBIRevisionBlueprint(
            status="ready",
            tool_blueprint={
                "operation": "cleanup_ai_flavor_window",
                "target_span": "A concrete sentence with no removable stock phrase.",
            },
        ),
    )
    diagnoses = FBIReviewDiagnosisSet(diagnoses=[diagnosis])
    annotated = [{
        "issue_id": "iss-candidate-tool",
        "scene_index": 0,
        "metric": "high_advisories",
    }]

    assert FBIReviewBlueprintAgent()._can_skip_llm_session(annotated, diagnoses) is False


def test_review_minister_blueprints_ending_state_as_contract_completion_tool():
    text = "She packed the herbs and looked for shelter."
    case = ChapterReviewCase(
        project_id="project",
        chapter_number=19,
        scene_packets=[
            SceneReviewPacket(
                scene_index=0,
                candidate_text=text,
                blocking_violations=[{
                    "type": "ending_state_not_reached",
                    "metric": "ending_state_not_reached",
                    "source": "scene_review",
                    "severity": "high",
                    "detail": "Scene ending state should be 'begin refining medicine with the fifth senior brother'.",
                    "blocks_commit": True,
                }],
            )
        ],
    )

    validated = asyncio.run(FBIChapterCaseIntakeService().intake(case))
    plan = asyncio.run(FBIChapterRepairPlanner().plan(validated))

    assert plan.work_units
    command = plan.work_units[0].tool_batch.commands[0]
    assert command.operation == "llm_creative_rewrite"
    # 结构合同缺失需要创作新正文，蓝图阶段不得用固定句式
    # 翻译或照抄诊断语；候选文本由 SceneRepairer 生成并复检。
    assert "begin refining medicine" not in command.new_text
    assert not command.new_text


def test_registry_tool_executor_runs_ai_discourse_rhythm_and_voice_operations():
    from app.services.fbi.work_unit_builder import _determinize_command

    ai_text = "\u8fd9\u8bf4\u660e\u5979\u7684\u547d\u8fd0\u88ab\u91cd\u65b0\u70d9\u5370\u5728\u7075\u9b42\u6df1\u5904\u3002"
    discourse_text = "\u5979\u7ec8\u4e8e\u660e\u767d\u4e86\u81ea\u5df1\u7684\u547d\u8fd0\u3002"
    voice_text = "\u5979\u4eff\u4f5b\u542c\u89c1\u6eaa\u6c34\u6df1\u5904\u6709\u4e1c\u897f\u64e6\u8fc7\u77f3\u9762\u3002"
    ai_command = _determinize_command(ToolCommand(
        operation="cleanup_ai_flavor_window",
        scene_index=0,
        target_span=ai_text,
        max_replacements=4,
        source_issue_ids=["issue-ai"],
    ))
    discourse_command = _determinize_command(ToolCommand(
        operation="trim_discourse_window",
        scene_index=1,
        target_span=discourse_text,
        max_replacements=2,
        source_issue_ids=["issue-discourse"],
    ))
    voice_command = _determinize_command(ToolCommand(
        operation="rewrite_voice_window",
        scene_index=2,
        target_span=voice_text,
        max_replacements=2,
        source_issue_ids=["issue-voice"],
    ))
    assert ai_command is not None
    assert discourse_command is not None
    assert voice_command is not None
    units = [
        RepairWorkUnit(
            work_unit_id="wu-ai",
            owner_scene=0,
            target_scenes=[0],
            tool_batch=ToolCommandBatch(
                batch_id="b-ai",
                target_scenes=[0],
                commands=[ai_command],
            ),
        ),
        RepairWorkUnit(
            work_unit_id="wu-discourse",
            owner_scene=1,
            target_scenes=[1],
            tool_batch=ToolCommandBatch(
                batch_id="b-discourse",
                target_scenes=[1],
                commands=[discourse_command],
            ),
        ),
        RepairWorkUnit(
            work_unit_id="wu-voice",
            owner_scene=2,
            target_scenes=[2],
            tool_batch=ToolCommandBatch(
                batch_id="b-voice",
                target_scenes=[2],
                commands=[voice_command],
            ),
        ),
    ]

    updated, audits = _execute_via_patches(
        units,
        {0: ai_text, 1: discourse_text, 2: voice_text},
    )

    assert all(audit["accepted"] for audit in audits.values())
    assert updated[0] != ai_text
    assert updated[1] != discourse_text
    assert updated[2] != voice_text
    assert "\u8fd9\u8bf4\u660e" not in updated[0]
    assert "\u7ec8\u4e8e\u660e\u767d" not in updated[1]
    assert "\u4eff\u4f5b" not in updated[2]


# ---------------------------------------------------------------------------
# FBI 蓝图路由统一修改指南 §13 测试
# ---------------------------------------------------------------------------

def test_blueprint_route_registry_family_consistency():
    """§13.1 路由一致性测试：_issue_family(metric) == route.family"""
    from app.services.fbi.blueprint_route_registry import (
        get_blueprint_route,
        route_family_for_metric,
    )

    test_metrics = [
        "abstraction_over_budget",
        "low_conflict_density",
        "ending_state_not_reached",
        "goal_present",
        "conflict_present",
        "value_change",
        "missing_must_show",
        "scene_goal_missing",
        "standalone_abstract_claims",
        "abstract_bare_count",
        "flat_pressure_ramp",
        "weak_curiosity_engine",
        "weak_opening_hook",
        "weak_chapter_end_hook",
        "missing_micro_payoff",
    ]
    for metric in test_metrics:
        route = get_blueprint_route(metric)
        assert route is not None, f"no route for {metric}"
        family = route_family_for_metric(metric)
        assert family == route.family, f"{metric}: family mismatch {family} != {route.family}"


def test_blueprint_route_abstraction_over_budget_not_intercepted_by_anti_ai_local():
    """§13.1 abstraction_over_budget 必须走 show_evidence，不被 anti_ai_local 截走"""
    from app.services.fbi.review_minister import FBIReviewMinister

    violation = {
        "type": "abstraction_over_budget",
        "metric": "abstraction_over_budget",
        "severity": "high",
        "detail": "抽象解释超过预算：3 处，高于 2。",
        "blocks_commit": True,
        "_candidate_text": "她弯腰捡起一根断掉的粗树枝，握在手里掂了掂。她看向远处的山影。",
    }
    minister = FBIReviewMinister()
    values = minister._semantic_values(violation)
    family = minister._issue_family(values, violation)
    assert family == "show_evidence", f"abstraction_over_budget family should be show_evidence, got {family}"


def test_blueprint_route_low_conflict_density_fallback_without_target_span():
    """方案5 B3b：low_conflict_density (requires_llm=True) 不生成确定性 tool_blueprint。"""
    from app.services.fbi.review_minister import FBIReviewMinister

    violation = {
        "type": "low_conflict_density",
        "metric": "low_conflict_density",
        "severity": "high",
        "detail": "冲突密度不足，阻碍没有被现场化。",
        "blocks_commit": True,
    }
    minister = FBIReviewMinister()
    bp = minister._tool_blueprint_command(
        violation=violation,
        family="rhythm",
        metric="low_conflict_density",
        anchor="某个锚点句。",
        issue_id="test_issue",
    )
    assert bp is None
    assert minister._last_route_failure_reason == "requires_llm_creative"


def test_blueprint_route_abstraction_over_budget_fallback_without_target_span():
    """方案5 B3b：abstraction_over_budget (requires_llm=True) 不生成确定性 tool_blueprint。"""
    from app.services.fbi.review_minister import FBIReviewMinister

    violation = {
        "type": "abstraction_over_budget",
        "metric": "abstraction_over_budget",
        "severity": "high",
        "detail": "抽象解释超过预算：3 处，高于 2。",
        "blocks_commit": True,
    }
    minister = FBIReviewMinister()
    bp = minister._tool_blueprint_command(
        violation=violation,
        family="show_evidence",
        metric="abstraction_over_budget",
        anchor="某个锚点句。",
        issue_id="test_issue",
    )
    assert bp is None
    assert minister._last_route_failure_reason == "requires_llm_creative"


def test_blueprint_route_contract_completion_never_writes_diagnostic_sentence():
    """创作型合同修复只生成执行指令，不在蓝图阶段拼装正文。"""
    from app.services.fbi.review_minister import FBIReviewMinister

    violation = {
        "type": "ending_state_not_reached",
        "metric": "ending_state_not_reached",
        "severity": "high",
        "detail": "场景结束状态要求与五师兄配合，利用前世记忆中的药草炼丹。正文末尾凤溪和五师兄刚到达裂隙入口，尚未开始炼丹，目标未达成。",
        "target_span": "凤溪把布袋系紧，看向裂隙。五师兄站到她身侧。",
        "blocks_commit": True,
        "_candidate_text": "凤溪把布袋系紧，看向裂隙。五师兄站到她身侧。",
    }
    minister = FBIReviewMinister()
    bp = minister._tool_blueprint_command(
        violation=violation,
        family="structure",
        metric="ending_state_not_reached",
        anchor=violation["target_span"],
        issue_id="test_issue",
    )
    assert bp is not None, "ending_state_not_reached must produce a blueprint"
    assert bp["operation"] == "llm_creative_rewrite"
    diagnostic = "与五师兄配合，利用前世记忆中的药草炼丹"
    assert diagnostic not in str(bp.get("new_text") or bp.get("replacement") or "")
    assert not bp.get("postconditions")


def test_blueprint_route_failure_reason_observable_when_anchor_unresolved():
    """§12 蓝图失败必须可观测：anchor 无法解析时 rejection_reason 应包含失败原因。

    方案5 B3b：使用 ending_state_not_reached (requires_llm=False) 测试 anchor 解析失败，
    因为 low_conflict_density 现在 requires_llm=True，会在 anchor 解析前就被拦截。
    """
    from app.services.fbi.review_minister import FBIReviewMinister

    # 构造一个没有 _candidate_text 也没有 target_span 的 violation，
    # 让 anchor resolver 无法生成任何 anchor
    violation = {
        "type": "ending_state_not_reached",
        "metric": "ending_state_not_reached",
        "severity": "high",
        "detail": "Scene ending state should be 'begin refining medicine'.",
        "blocks_commit": True,
        # 故意不提供 _candidate_text 和 target_span
    }
    minister = FBIReviewMinister()
    bp = minister._tool_blueprint_command(
        violation=violation,
        family="structure",
        metric="ending_state_not_reached",
        anchor="",
        issue_id="test_issue",
    )
    # 蓝图应为 None
    assert bp is None
    # 失败原因应被记录到实例变量（builder 返回 None 因为 anchor 为空）
    assert minister._last_route_failure_reason


def test_blueprint_route_failure_reason_propagates_to_rejection_reason():
    """§12 蓝图失败原因应传递到 FBIRevisionBlueprint.rejection_reason。

    方案5 B3b：使用 ending_state_not_reached (requires_llm=False) 测试失败原因传递。
    """
    from app.services.fbi.review_minister import FBIReviewMinister
    from app.models.chapter_review import ChapterReviewCase, SceneReviewPacket

    # 构造一个空场景文本的 case，让 ending_state_not_reached 无法解析 anchor
    case = ChapterReviewCase(
        project_id="project",
        chapter_number=19,
        scene_packets=[
            SceneReviewPacket(
                scene_index=0,
                candidate_text="",  # 空文本，fallback anchor 无法生成
                blocking_violations=[
                    {
                        "type": "ending_state_not_reached",
                        "metric": "ending_state_not_reached",
                        "severity": "high",
                        "detail": "Scene ending state should be 'begin refining medicine'.",
                        "blocks_commit": True,
                    }
                ],
            )
        ],
    )

    minister = FBIReviewMinister()
    diagnosis_set = minister.diagnose_violations(case, case.scene_packets[0].blocking_violations)
    assert len(diagnosis_set.diagnoses) == 1
    diagnosis = diagnosis_set.diagnoses[0]
    blueprint = diagnosis.revision_blueprint
    # 蓝图应失败
    assert blueprint.status == "blueprint_incomplete"
    # rejection_reason 应包含具体失败原因
    assert blueprint.rejection_reason, \
        f"rejection_reason should not be empty, got: {blueprint.rejection_reason}"
