import asyncio

import pytest

from app.models.chapter_review import (
    ChapterRepairOrder,
    ChapterRepairPlan,
    SceneReviewPacket,
)
from app.agents.scene_repairer import SceneRepairer
from app.services.fbi.chapter_repair_executor import ChapterRepairExecutor
from app.services.fbi.final_gate_text_repair import repair_final_gate_text
from app.utils.dash_artifacts import count_dash_artifacts


class _CapturingRepairer:
    def __init__(self):
        self.context = None

    async def execute(self, context: dict) -> dict:
        self.context = context
        return {
            "success": True,
            "repaired_text": context["generated_text"].replace("old phrase", "new phrase"),
        }


class _StaticRepairer:
    def __init__(self, repaired_text: str):
        self.repaired_text = repaired_text
        self.contexts = []

    async def execute(self, context: dict) -> dict:
        self.contexts.append(context)
        return {"success": True, "repaired_text": self.repaired_text}


class _NoopRepairer:
    def __init__(self):
        self.contexts = []

    async def execute(self, context: dict) -> dict:
        self.contexts.append(context)
        return {"success": True, "repaired_text": context["generated_text"]}


class _NoopThenPatchRepairer:
    def __init__(self):
        self.contexts = []

    async def execute(self, context: dict) -> dict:
        self.contexts.append(context)
        if len(self.contexts) == 1:
            return {
                "success": False,
                "repaired_text": context["generated_text"],
                "error": "repair_result_parse_failed",
            }
        return {
            "success": True,
            "repaired_text": context["generated_text"].replace("old phrase", "new phrase"),
        }


class _CountingAppendRepairer:
    def __init__(self, suffix: str):
        self.suffix = suffix
        self.contexts = []

    async def execute(self, context: dict) -> dict:
        self.contexts.append(context)
        return {
            "success": True,
            "repaired_text": context["generated_text"] + self.suffix,
        }


@pytest.mark.asyncio
async def test_repair_executor_honors_workflow_cancellation_before_repair():
    plan = ChapterRepairPlan(
        case_id="cancelled-case",
        status="needs_repair",
        orders=[ChapterRepairOrder(
            order_id="repair-1",
            target_scenes=[0],
            owner_scene=0,
            repair_type="local_patch",
            reason="blocking issue",
            instruction="repair it",
        )],
    )

    async def cancellation_check():
        return True

    with pytest.raises(asyncio.CancelledError):
        await ChapterRepairExecutor().execute(
            plan,
            {0: "original text"},
            context={"cancellation_check": cancellation_check},
        )


def test_repair_executor_groups_respect_dependencies_and_broad_write_scope():
    plan = ChapterRepairPlan(
        case_id="case",
        status="needs_repair",
        orders=[
            ChapterRepairOrder(
                order_id="cross",
                target_scenes=[0, 1],
                owner_scene=0,
                repair_type="cross_scene_alignment",
                write_scope=["scene:0", "scene:1"],
            ),
            ChapterRepairOrder(
                order_id="local",
                target_scenes=[1],
                owner_scene=1,
                repair_type="local_patch",
                write_scope=["scene:1"],
                dependencies=["cross"],
            ),
            ChapterRepairOrder(
                order_id="surface",
                target_scenes=[2],
                owner_scene=2,
                repair_type="local_patch",
                write_scope=["scene:2"],
            ),
        ],
    )

    groups = ChapterRepairExecutor()._get_execution_groups(plan)
    group_ids = [[order.order_id for order in group] for group in groups]

    assert group_ids[0] == ["cross"]
    assert set(group_ids[1]) == {"local", "surface"}


def test_repair_executor_marks_missing_dependency_orders_failed_without_execution_group():
    plan = ChapterRepairPlan(
        case_id="case-missing-dependency",
        status="needs_repair",
        orders=[
            ChapterRepairOrder(
                order_id="local",
                target_scenes=[0],
                owner_scene=0,
                repair_type="local_patch",
                write_scope=["scene:0"],
                dependencies=["missing"],
            )
        ],
    )

    groups = ChapterRepairExecutor()._get_execution_groups(plan)

    assert groups == []
    assert plan.orders[0].status == "failed"
    assert plan.orders[0].repair_audit["reason"] == "missing_required_dependencies"
    assert plan.orders[0].repair_audit["missing_dependencies"] == ["missing"]


def test_repair_executor_marks_cyclic_dependency_orders_failed_without_execution_group():
    plan = ChapterRepairPlan(
        case_id="case-cyclic-dependency",
        status="needs_repair",
        orders=[
            ChapterRepairOrder(
                order_id="a",
                target_scenes=[0],
                owner_scene=0,
                repair_type="local_patch",
                write_scope=["scene:0"],
                dependencies=["b"],
            ),
            ChapterRepairOrder(
                order_id="b",
                target_scenes=[1],
                owner_scene=1,
                repair_type="local_patch",
                write_scope=["scene:1"],
                dependencies=["a"],
            ),
        ],
    )

    groups = ChapterRepairExecutor()._get_execution_groups(plan)

    assert groups == []
    assert {order.status for order in plan.orders} == {"failed"}
    assert {order.repair_audit["reason"] for order in plan.orders} == {
        "cyclic_or_unresolved_dependencies"
    }


def test_executor_target_self_check_rejects_unimproved_paragraph_shape_candidate():
    executor = ChapterRepairExecutor()
    order = ChapterRepairOrder(
        order_id="shape-order",
        target_scenes=[0],
        owner_scene=0,
        repair_type="local_patch",
        repair_domain="anti_ai",
        repair_lane="paragraph_reconstruction",
        repair_strength="S3",
        allowed_max_strength="S4",
        candidate_attempt_budget=2,
        repair_brief={
            "target_metrics": [{
                "validator": "ai_discourse",
                "metric": "paragraph_shape_repeat_count",
                "actual": 2,
                "expected": 0,
                "direction": "decrease",
            }]
        },
        violation_details=[{
            "type": "paragraph_shape_repeat_count",
            "metric": "paragraph_shape_repeat_count",
            "severity": "high",
        }],
    )
    repeated = "她把药草放进布袋，又把绳结重新勒紧，指节贴着粗糙的布纹。这说明她必须继续向前。"
    text = "\n\n".join([repeated] * 8)

    audit = executor._audit_repair_result(
        order,
        text,
        text.replace("。", "！", 1),
        scene_index=0,
        context={},
        case_file={},
    )

    assert audit["accepted"] is False
    assert "target_metric_not_improved" in audit["failures"]
    assert audit["target_self_check"]["supported"] is True
    assert audit["target_self_check"]["source"] == "ai_discourse_checker"
    assert audit["candidate_attempts"][0]["lane"] == "paragraph_reconstruction"


def test_repair_audit_rejects_new_hard_prose_defect_after_pov_fix():
    executor = ChapterRepairExecutor()
    order = ChapterRepairOrder(
        order_id="pov-integrity-order",
        target_scenes=[0],
        owner_scene=0,
        repair_type="local_patch",
        repair_domain="pov",
        repair_brief={
            "target_metrics": [{
                "validator": "pov_consistency",
                "metric": "head_hopping_count",
                "direction": "decrease",
            }],
        },
        violation_details=[{
            "type": "head_hopping_count",
            "metric": "head_hopping_count",
            "blocks_commit": True,
        }],
    )
    before = "凤溪看着五师兄。他觉得掌心里的分量太轻。"
    after = "凤溪看着五师兄。五师兄的动作顿了一下了。"

    audit = executor._audit_repair_result(
        order,
        before,
        after,
        scene_index=0,
        context={"scene_contracts": {0: {"pov_character": "凤溪"}}},
        case_file={},
    )

    assert audit["accepted"] is False
    assert "prose_integrity_regression" in audit["failures"]
    assert audit["failure_stage"] == "prose_integrity_check_failed"
    assert audit["prose_integrity_regressions"] == [{
        "type": "malformed_aspect_particle",
        "target_span": "顿了一下了",
        "new_count": 1,
    }]


def test_executor_target_self_check_handles_pov_voice_and_hook_metrics():
    executor = ChapterRepairExecutor()
    pov_order = ChapterRepairOrder(
        order_id="pov-order",
        repair_brief={
            "target_metrics": [{
                "validator": "narrative",
                "metric": "head_hopping_count",
                "direction": "decrease",
            }]
        },
    )
    pov_check = executor._target_self_check(
        pov_order,
        "\u4e3b\u89d2\u770b\u7740\u914d\u89d2\u3002\u914d\u89d2\u5fc3\u91cc\u4e00\u6c89\u3002",
        "\u4e3b\u89d2\u770b\u7740\u914d\u89d2\u3002\u914d\u89d2\u540e\u9000\u534a\u6b65\u3002",
        dash_density_check=None,
        target_span_checks=[],
        pov_name="\u4e3b\u89d2",
    )
    assert pov_check["supported"] is True
    assert pov_check["passed"] is True
    assert pov_check["source"] == "agent_skill_validator.pov_consistency"
    assert pov_check["before"] > pov_check["after"]

    voice_order = ChapterRepairOrder(
        order_id="voice-order",
        repair_brief={
            "target_metrics": [{
                "validator": "voice_fingerprint",
                "metric": "voice_fingerprint",
                "direction": "decrease",
            }]
        },
    )
    voice_check = executor._target_self_check(
        voice_order,
        "\u5979\u6df1\u5438\u4e00\u53e3\u6c14\uff0c\u5fc3\u4e2d\u4e00\u7d27\u3002",
        "\u5979\u6309\u4f4f\u8896\u53e3\uff0c\u505c\u5728\u95e8\u524d\u3002",
        dash_density_check=None,
        target_span_checks=[],
    )
    assert voice_check["supported"] is True
    assert voice_check["passed"] is True
    assert voice_check["source"] == "generic_voice_package_score"
    assert voice_check["before"] > voice_check["after"]

    hook_order = ChapterRepairOrder(
        order_id="hook-order",
        repair_brief={
            "target_metrics": [{
                "validator": "rhythm",
                "metric": "weak_opening_hook",
                "direction": "decrease",
            }]
        },
    )
    weak_opening = "\u5979\u5e73\u9759\u5730\u5f80\u524d\u8d70" * 30
    hook_check = executor._target_self_check(
        hook_order,
        weak_opening,
        "\u95e8\u5916\u5ffd\u7136\u54cd\u4e86\u4e00\u58f0\u3002\n\n\u5979\u505c\u4f4f\u3002",
        dash_density_check=None,
        target_span_checks=[],
    )
    assert hook_check["supported"] is True
    assert hook_check["passed"] is True
    assert hook_check["source"] == "hook_weakness_score"
    assert hook_check["before"] > hook_check["after"]


def test_executor_fact_target_self_check_requires_entities_and_old_claim_removal():
    executor = ChapterRepairExecutor()
    order = ChapterRepairOrder(
        order_id="fact-goal",
        repair_lane="fact_bridge_patch",
        repair_brief={
            "target_metrics": [{"metric": "fact_conflict", "direction": "resolve"}],
            "fact_repair_goal": {
                "conflict_type": "missing_required_components",
                "authority_fact": "炼制破境丹需要三味主药：赤阳草、凝露花、地脉根茎",
                "text_claim": "凤溪只找到一株赤翎草，并用其单独炼制丹药",
                "required_entities": ["赤阳草", "凝露花", "地脉根茎"],
                "forbidden_claims": ["凤溪只找到一株赤翎草，并用其单独炼制丹药"],
                "old_error_signatures": [
                    "凤溪在那株赤翎草前蹲下时...凤溪把半凝固的药膏从石面上刮下来，揉成丹丸。"
                ],
            },
        },
    )
    before = (
        "凤溪只找到一株赤翎草，并用其单独炼制丹药。"
        "凤溪在那株赤翎草前蹲下时，顺手连根拔起。"
        "凤溪把半凝固的药膏从石面上刮下来，揉成丹丸。"
    )
    changed_but_unfixed = before + "她把火候压低了一点。"

    failed = executor._target_self_check(
        order,
        before,
        changed_but_unfixed,
        dash_density_check=None,
        target_span_checks=[],
    )
    assert failed["supported"] is True
    assert failed["passed"] is False
    assert failed["source"] == "fact_target_self_check"
    assert set(failed["missing_required_entities"]) == {"赤阳草", "凝露花", "地脉根茎"}
    assert failed["lingering_old_error_signatures"]

    repaired = (
        "凤溪先确认赤翎草正是丹方里的赤阳草，又在溪边石缝取到凝露花，"
        "从湿土下挖出地脉根茎。三味主药被她按前世记忆分次入火，"
        "药性合拢后才凝成破境丹。"
    )
    passed = executor._target_self_check(
        order,
        before,
        repaired,
        dash_density_check=None,
        target_span_checks=[],
    )
    assert passed["supported"] is True
    assert passed["passed"] is True
    assert passed["missing_required_entities"] == []
    assert passed["lingering_old_error_signatures"] == []


def test_scene_repairer_prompt_includes_lane_brief_and_retry_context():
    repairer = SceneRepairer()
    lane_instruction = repairer._lane_system_instruction({
        "repair_lane": "paragraph_reconstruction",
        "repair_strength": "S4",
    })
    assert "FBI repair lane: paragraph_reconstruction / S4" in lane_instruction
    assert "S4" in lane_instruction

    prompt = repairer._build_patch_user_prompt(
        {
            "repair_lane": "paragraph_reconstruction",
            "repair_strength": "S4",
            "repair_attempt": {"attempt": 2},
            "repair_brief": {
                "repair_lane": "paragraph_reconstruction",
                "repair_strength": "S4",
                "target_metrics": [{
                    "validator": "ai_discourse",
                    "metric": "paragraph_shape_repeat_count",
                    "actual": 3,
                    "expected": 0,
                    "direction": "decrease",
                }],
                "target_behavior": "Break mirrored paragraph openings.",
                "edit_scope": {"allowed_operations": ["split_paragraph", "merge_sentences"]},
                "preserve": {"protected_spans": ["keep-this-span"]},
                "fact_repair_goal": {
                    "conflict_type": "missing_required_components",
                    "authority_fact": "Fact requires item A and item B.",
                    "text_claim": "Only item A appears.",
                    "required_entities": ["item A", "item B"],
                    "forbidden_claims": ["Only item A appears."],
                    "required_relation": "Both items must participate.",
                },
            },
        },
        "original text",
        ["- [HIGH] paragraph_shape_repeat_count"],
        [],
        [],
        False,
        {},
    )
    assert "target_metric: ai_discourse:paragraph_shape_repeat_count" in prompt
    assert "target_behavior: Break mirrored paragraph openings." in prompt
    assert "allowed_operations: split_paragraph, merge_sentences" in prompt
    assert "keep-this-span" in prompt
    assert "retry_reason" in prompt
    assert "fact_repair_goal" in prompt
    assert "required_entities: item A, item B" in prompt
    assert "old_claims_to_remove_or_rewrite" in prompt


@pytest.mark.asyncio
async def test_executor_retries_candidate_with_stronger_strength_after_no_target_progress():
    repeated = "她把药草放进布袋，又把绳结重新勒紧，指节贴着粗糙的布纹。这说明她必须继续向前。"
    repeated_text = "\n\n".join([repeated] * 8)

    class _StrengthAwareRepairer:
        def __init__(self):
            self.contexts = []

        async def execute(self, context: dict) -> dict:
            self.contexts.append(context)
            if context.get("repair_strength") == "S4":
                return {
                    "success": True,
                    "repaired_text": "风声近了。\n\n她握紧剑柄。",
                }
            return {
                "success": True,
                "repaired_text": repeated_text.replace("。", "！", 1),
            }

    repairer = _StrengthAwareRepairer()
    executor = ChapterRepairExecutor()
    executor._scene_repairer = repairer
    executor._deterministic_local_patch = lambda *args, **kwargs: None
    order = ChapterRepairOrder(
        order_id="shape-retry",
        target_scenes=[0],
        owner_scene=0,
        repair_type="local_patch",
        repair_domain="anti_ai",
        repair_lane="paragraph_reconstruction",
        repair_strength="S3",
        allowed_max_strength="S4",
        candidate_attempt_budget=2,
        repair_brief={
            "target_metrics": [{
                "validator": "ai_discourse",
                "metric": "paragraph_shape_repeat_count",
                "actual": 2,
                "expected": 0,
                "direction": "decrease",
            }]
        },
        violation_details=[{
            "type": "paragraph_shape_repeat_count",
            "metric": "paragraph_shape_repeat_count",
            "severity": "high",
        }],
    )

    updated_texts, updated_plan = await executor.execute(
        ChapterRepairPlan(case_id="case-shape-retry", status="needs_repair", orders=[order]),
        {0: repeated_text},
        context={},
    )

    assert updated_texts[0] == "风声近了。\n\n她握紧剑柄。"
    assert updated_plan.orders[0].status == "succeeded"
    audit = updated_plan.orders[0].repair_audit
    assert audit["strength_retry"]["attempted"] is True
    assert [attempt["strength"] for attempt in audit["candidate_attempts"]] == ["S3", "S4"]
    assert audit["candidate_attempts"][0]["decision"] == "rejected"
    assert audit["candidate_attempts"][1]["decision"] == "accepted"
    assert [context["repair_strength"] for context in repairer.contexts] == ["S3", "S4"]
    summary = updated_plan.repair_execution_summary
    assert summary["lane_counts"]["paragraph_reconstruction"] == 1
    assert summary["strength_retry_attempted"] == 1
    assert summary["strength_retry_succeeded"] == 1
    assert summary["target_check_supported"] == 2
    assert summary["target_check_passed"] == 1
    assert summary["target_improved_rate"] == 0.5
    assert summary["no_progress_candidate_count"] == 1


@pytest.mark.asyncio
async def test_executor_fact_no_progress_retry_switches_to_fallback_lane():
    class _LaneAwareRepairer:
        def __init__(self):
            self.contexts = []

        async def execute(self, context: dict) -> dict:
            self.contexts.append(context)
            if context.get("repair_lane") == "fact_bridge_patch":
                return {
                    "success": True,
                    "repaired_text": "目标事实已经被补入，新表述完成。",
                }
            return {
                "success": True,
                "repaired_text": context["generated_text"],
            }

    repairer = _LaneAwareRepairer()
    executor = ChapterRepairExecutor()
    executor._scene_repairer = repairer
    executor._deterministic_local_patch = lambda *args, **kwargs: None
    order = ChapterRepairOrder(
        order_id="fact-lane-retry",
        target_scenes=[0],
        owner_scene=0,
        repair_type="local_patch",
        repair_domain="fact",
        repair_lane="fact_local_patch",
        repair_strength="S2",
        allowed_max_strength="S3",
        candidate_attempt_budget=2,
        repair_brief={
            "repair_domain": "fact",
            "repair_type": "local_patch",
            "repair_lane": "fact_local_patch",
            "repair_strength": "S2",
            "allowed_max_strength": "S3",
            "target_metrics": [{"metric": "fact_conflict", "direction": "resolve"}],
            "fact_repair_goal": {
                "conflict_type": "direct_fact_conflict",
                "authority_fact": "目标事实",
                "text_claim": "旧错误",
                "required_entities": ["目标事实"],
                "forbidden_claims": ["旧错误"],
                "old_error_signatures": ["旧错误"],
                "required_relation": "正文必须改为目标事实",
            },
            "edit_scope": {
                "allowed_operations": ["replace_conflicting_phrase"],
            },
            "repair_strategy": {
                "preferred_lane": "fact_local_patch",
                "fallback_lane": "fact_bridge_patch",
            },
        },
        violation_details=[{
            "type": "fact_conflict",
            "metric": "fact_conflict",
            "severity": "high",
        }],
    )

    updated_texts, updated_plan = await executor.execute(
        ChapterRepairPlan(case_id="case-fact-lane-retry", status="needs_repair", orders=[order]),
        {0: "旧错误仍在。"},
        context={},
    )

    assert updated_texts[0] == "目标事实已经被补入，新表述完成。"
    audit = updated_plan.orders[0].repair_audit
    assert updated_plan.orders[0].status == "succeeded"
    assert audit["lane_retry"]["attempted"] is True
    assert [attempt["lane"] for attempt in audit["candidate_attempts"]] == [
        "fact_local_patch",
        "fact_bridge_patch",
    ]
    assert repairer.contexts[1]["repair_brief"]["repair_lane"] == "fact_bridge_patch"
    assert "insert_missing_fact_components" in repairer.contexts[1]["repair_brief"]["edit_scope"]["allowed_operations"]


@pytest.mark.asyncio
async def test_executor_does_not_mutate_text_when_dependency_is_unresolved():
    repairer = _CountingAppendRepairer(" mutated")
    executor = ChapterRepairExecutor()
    executor._scene_repairer = repairer
    plan = ChapterRepairPlan(
        case_id="case-unresolved-execute",
        status="needs_repair",
        orders=[
            ChapterRepairOrder(
                order_id="local",
                target_scenes=[0],
                owner_scene=0,
                repair_type="local_patch",
                write_scope=["scene:0"],
                dependencies=["missing"],
            )
        ],
    )

    updated_texts, updated_plan = await executor.execute(
        plan,
        {0: "original scene"},
        context={},
    )

    assert updated_texts == {0: "original scene"}
    assert repairer.contexts == []
    assert updated_plan.orders[0].status == "failed"
    assert updated_plan.orders[0].repair_audit["reason"] == "missing_required_dependencies"


@pytest.mark.asyncio
async def test_executor_does_not_run_order_after_dependency_fails_at_runtime():
    repairer = _NoopRepairer()
    executor = ChapterRepairExecutor()
    executor._scene_repairer = repairer
    plan = ChapterRepairPlan(
        case_id="case-runtime-dependency-failure",
        status="needs_repair",
        orders=[
            ChapterRepairOrder(
                order_id="root",
                target_scenes=[0],
                owner_scene=0,
                repair_type="local_patch",
                write_scope=["scene:0"],
                reason="Root order will produce no text change.",
                instruction="Change the scene.",
            ),
            ChapterRepairOrder(
                order_id="dependent",
                target_scenes=[1],
                owner_scene=1,
                repair_type="local_patch",
                write_scope=["scene:1"],
                dependencies=["root"],
                reason="Dependent order must not run after root fails.",
                instruction="Change the dependent scene.",
            ),
        ],
    )

    updated_texts, updated_plan = await executor.execute(
        plan,
        {0: "root scene", 1: "dependent scene"},
        context={},
    )

    orders = {order.order_id: order for order in updated_plan.orders}
    assert updated_texts == {0: "root scene", 1: "dependent scene"}
    assert len(repairer.contexts) == 1
    assert repairer.contexts[0]["generated_text"] == "root scene"
    assert orders["root"].status == "failed"
    assert orders["dependent"].status == "failed"
    assert orders["dependent"].repair_audit["reason"] == "failed_required_dependencies"
    assert orders["dependent"].repair_audit["failed_dependencies"] == ["root"]


@pytest.mark.asyncio
async def test_executor_runs_contract_completion_patch_as_minimal_patch():
    repairer = _CapturingRepairer()
    executor = ChapterRepairExecutor()
    executor._scene_repairer = repairer

    plan = ChapterRepairPlan(
        case_id="case-contract-completion",
        status="needs_repair",
        orders=[
            ChapterRepairOrder(
                order_id="order-contract-completion",
                target_scenes=[0],
                owner_scene=0,
                repair_type="contract_completion_patch",
                priority="high",
                reason="Scene ending state is not reached.",
                instruction="Complete the missing contract beat.",
                violation_details=[
                    {
                        "type": "ending_state_not_reached",
                        "severity": "high",
                        "detail": "Scene ending state is not reached.",
                        "target_span": "old phrase",
                    }
                ],
            )
        ],
    )

    updated_texts, updated_plan = await executor.execute(
        plan,
        {0: "scene keeps protected fact and old phrase"},
        context={
            "scene_contracts": {
                0: {
                    "hard_facts": ["protected fact"],
                    "ending_state": "new phrase completes the beat",
                }
            }
        },
    )

    assert updated_plan.orders[0].status == "succeeded"
    assert updated_texts[0] == "scene keeps protected fact and new phrase"
    assert repairer.context["repair_strategy"] == "patch"
    assert repairer.context["revision_hints"][0]["strategy"] == "contract_completion_patch"
    envelope = repairer.context["skill_contract_envelope"]
    assert envelope["role"] == "fbi_repair_skill_contract_envelope"
    assert envelope["active_skills"] == ["anti_ai_prose"]
    assert envelope["skill_contracts"][0]["skill_id"] == "anti_ai_prose"
    assert envelope["skill_contracts"][0]["not_repairer"] is True
    assert "protected fact" in updated_texts[0]


@pytest.mark.asyncio
async def test_executor_applies_contract_patch_without_mutating_text():
    executor = ChapterRepairExecutor()
    scene_contracts = {
        0: {
            "must_show": ["old item", "extra item"],
            "nice_to_have": [],
        }
    }
    plan = ChapterRepairPlan(
        case_id="case-contract-patch",
        status="needs_contract_repair",
        orders=[
            ChapterRepairOrder(
                order_id="order-contract-patch",
                target_scenes=[0],
                owner_scene=0,
                repair_type="contract_patch",
                priority="high",
                reason="Contract must_show is overloaded.",
                expected_after_repair={
                    "must_show": ["old item"],
                    "nice_to_have": ["extra item"],
                    "_budget_note": "demoted optional item",
                },
            )
        ],
    )

    updated_texts, updated_plan = await executor.execute(
        plan,
        {0: "scene text stays untouched"},
        context={"scene_contracts": scene_contracts},
        case_file={"all_scene_contracts": scene_contracts},
    )

    order = updated_plan.orders[0]
    assert updated_texts == {0: "scene text stays untouched"}
    assert order.status == "succeeded"
    assert order.repair_audit["accepted"] is True
    assert order.repair_audit["executor_path"] == "contract_patch"
    assert order.repair_audit["contract_patch"]["must_show"] == ["old item"]
    assert scene_contracts[0]["must_show"] == ["old item"]
    assert scene_contracts[0]["nice_to_have"] == ["extra item"]


@pytest.mark.asyncio
async def test_executor_routes_untargeted_contract_patch_to_chapter_contract():
    executor = ChapterRepairExecutor()
    scene_contracts = {
        0: {
            "must_show": ["old item", "extra item"],
            "nice_to_have": [],
        }
    }
    plan = ChapterRepairPlan(
        case_id="case-untargeted-contract-patch",
        status="needs_contract_repair",
        orders=[
            ChapterRepairOrder(
                order_id="order-untargeted-contract-patch",
                repair_type="contract_patch",
                priority="high",
                reason="Chapter-level contract is overloaded.",
                expected_after_repair={
                    "must_show": ["old item"],
                    "nice_to_have": ["extra item"],
                },
            )
        ],
    )

    updated_texts, updated_plan = await executor.execute(
        plan,
        {0: "scene text stays untouched"},
        context={"scene_contracts": scene_contracts},
        case_file={"all_scene_contracts": scene_contracts},
    )

    order = updated_plan.orders[0]
    assert updated_texts == {0: "scene text stays untouched"}
    assert order.status == "succeeded"
    assert order.owner_scene is None
    assert order.target_scenes == []
    assert order.repair_audit["executor_path"] == "contract_patch"
    assert scene_contracts[0]["must_show"] == ["old item"]
    assert scene_contracts[0]["nice_to_have"] == ["extra item"]


@pytest.mark.asyncio
async def test_legacy_contract_patch_entrypoint_applies_patch_without_exception():
    executor = ChapterRepairExecutor()
    scene_contracts = {0: {"must_show": ["old item", "extra item"]}}
    order = ChapterRepairOrder(
        order_id="order-legacy-contract-patch",
        target_scenes=[0],
        owner_scene=0,
        repair_type="contract_patch",
        expected_after_repair={"must_show": ["old item"]},
    )

    result = await executor._repair_contract_patch(
        order,
        "scene text stays untouched",
        0,
        {"scene_contracts": scene_contracts},
        {"all_scene_contracts": scene_contracts},
    )

    assert result == "scene text stays untouched"
    assert order.status == "succeeded"
    assert order.repair_audit["executor_path"] == "contract_patch"
    assert scene_contracts[0]["must_show"] == ["old item"]


@pytest.mark.asyncio
async def test_executor_marks_untargeted_manual_review_explicitly_skipped():
    executor = ChapterRepairExecutor()
    plan = ChapterRepairPlan(
        case_id="case-untargeted-manual-review",
        status="needs_human_review",
        orders=[
            ChapterRepairOrder(
                order_id="order-untargeted-manual-review",
                repair_type="manual_review",
                priority="high",
                reason="System-level judgment required.",
            )
        ],
    )

    updated_texts, updated_plan = await executor.execute(
        plan,
        {0: "scene text stays untouched"},
        context={},
    )

    order = updated_plan.orders[0]
    assert updated_texts == {0: "scene text stays untouched"}
    assert order.status == "skipped"
    assert order.repair_audit["accepted"] is False
    assert order.repair_audit["failures"] == ["manual_review_required"]
    assert order.repair_audit["executor_path"] == "manual_review"


@pytest.mark.asyncio
async def test_executor_marks_untargeted_text_order_failed_without_mutation():
    repairer = _CountingAppendRepairer(" mutated")
    executor = ChapterRepairExecutor()
    executor._scene_repairer = repairer
    plan = ChapterRepairPlan(
        case_id="case-untargeted-local-patch",
        status="needs_repair",
        orders=[
            ChapterRepairOrder(
                order_id="order-untargeted-local-patch",
                repair_type="local_patch",
                priority="high",
                reason="Malformed order has no write target.",
            )
        ],
    )

    updated_texts, updated_plan = await executor.execute(
        plan,
        {0: "scene text stays untouched"},
        context={},
    )

    order = updated_plan.orders[0]
    assert updated_texts == {0: "scene text stays untouched"}
    assert repairer.contexts == []
    assert order.status == "failed"
    assert order.repair_audit["reason"] == "missing_target_scene"
    assert order.repair_audit["executor_path"] == "scene_router"


@pytest.mark.asyncio
async def test_executor_contract_completion_uses_one_scene_repairer_call():
    repairer = _NoopThenPatchRepairer()
    executor = ChapterRepairExecutor()
    executor._scene_repairer = repairer

    plan = ChapterRepairPlan(
        case_id="case-contract-retry",
        status="needs_repair",
        orders=[
            ChapterRepairOrder(
                order_id="order-contract-retry",
                target_scenes=[0],
                owner_scene=0,
                repair_type="contract_completion_patch",
                priority="high",
                reason="Scene ending state is not reached.",
                instruction="Complete the missing contract beat.",
                repair_brief={
                    "repair_goals": [{
                        "goal_id": "goal-contract",
                        "desired_state": "new phrase completes the beat",
                    }],
                    "placement": {"status": "required", "write_anchor": {}},
                },
                violation_details=[
                    {
                        "type": "ending_state_not_reached",
                        "severity": "high",
                        "detail": "Scene ending state is not reached.",
                        "target_span": "old phrase",
                    }
                ],
            )
        ],
    )

    updated_texts, updated_plan = await executor.execute(
        plan,
        {0: "scene keeps protected fact and old phrase"},
        context={
            "scene_contracts": {
                0: {
                    "hard_facts": ["protected fact"],
                    "ending_state": "new phrase completes the beat",
                }
            }
        },
    )

    assert updated_plan.orders[0].status == "failed"
    assert updated_texts[0] == "scene keeps protected fact and old phrase"
    assert len(repairer.contexts) == 1
    assert repairer.contexts[0]["repair_brief"]["repair_goals"][0]["goal_id"] == "goal-contract"
    assert repairer.contexts[0]["repair_brief"]["placement"]["status"] == "required"


@pytest.mark.asyncio
async def test_executor_accepts_list_review_packets_in_case_file():
    repairer = _CapturingRepairer()
    executor = ChapterRepairExecutor()
    executor._scene_repairer = repairer

    plan = ChapterRepairPlan(
        case_id="case-1",
        status="needs_repair",
        orders=[
            ChapterRepairOrder(
                order_id="order-1",
                target_scenes=[1],
                owner_scene=1,
                repair_type="local_patch",
                priority="high",
                reason="Fix explanatory prose.",
                instruction="Replace the target span.",
                violation_details=[
                    {
                        "type": "explanatory_punctuation_artifact",
                        "severity": "high",
                        "detail": "Fix explanatory prose.",
                        "target_span": "old phrase",
                    }
                ],
            )
        ],
    )

    updated_texts, updated_plan = await executor.execute(
        plan,
        {0: "scene zero", 1: "scene one old phrase"},
        context={},
        case_file={
            "review_packets": [
                SceneReviewPacket(scene_index=0),
                SceneReviewPacket(
                    scene_index=1,
                    blocking_violations=[
                        {
                            "type": "explanatory_punctuation_artifact",
                            "severity": "high",
                        }
                    ],
                ),
            ],
        },
    )

    assert updated_texts[1] == "scene one new phrase"
    assert updated_plan.orders[0].status == "succeeded"
    assert updated_plan.orders[0].repair_audit["accepted"] is True
    assert repairer.context["original_review_packet"]["scene_index"] == 1


@pytest.mark.asyncio
async def test_executor_rejects_repair_that_removes_existing_contract_obligation():
    executor = ChapterRepairExecutor()
    executor._scene_repairer = _StaticRepairer("凤溪独自站在封印前。")

    plan = ChapterRepairPlan(
        case_id="case-protected",
        status="needs_repair",
        orders=[
            ChapterRepairOrder(
                order_id="order-protected",
                target_scenes=[0],
                owner_scene=0,
                repair_type="scene_rewrite",
                priority="high",
                reason="Improve pacing.",
                instruction="Rewrite the scene.",
            )
        ],
    )

    before = "五位师兄站在凤溪身后，五道灵力同时注入封印。"
    updated_texts, updated_plan = await executor.execute(
        plan,
        {0: before},
        context={
            "scene_contracts": {
                0: {
                    "must_show": ["五位师兄合力破解封印"],
                    "ending_state": "五位师兄合力破解封印",
                }
            }
        },
    )

    order = updated_plan.orders[0]
    assert order.status == "failed"
    assert updated_texts[0] == before
    assert "protected_obligation_removed" in order.repair_audit["failures"]
    assert "五位师兄合力破解封印" not in order.repair_audit["removed_protected_terms"]
    assert "五位师兄" in order.repair_audit["removed_protected_terms"]


def test_contract_connective_is_weak_but_entity_obligation_remains_strong():
    assert ChapterRepairExecutor._is_weak_protected_term(
        "scene_contract.hard_must_show.0",
        "同时",
    ) is True
    assert ChapterRepairExecutor._is_weak_protected_term(
        "scene_contract.hard_must_show.0",
        "五位师兄",
    ) is False


@pytest.mark.asyncio
async def test_executor_does_not_protect_rewrite_state_prose_fragments():
    executor = ChapterRepairExecutor()
    executor._scene_repairer = _StaticRepairer("五位师兄同时结阵，灵力压入封印，石门彻底洞开。")

    plan = ChapterRepairPlan(
        case_id="case-state-prose",
        status="needs_repair",
        orders=[
            ChapterRepairOrder(
                order_id="order-state-prose",
                target_scenes=[0],
                owner_scene=0,
                repair_type="scene_rewrite",
                priority="high",
                reason="Ending state not reached.",
                instruction="Rewrite the scene to reach the ending state.",
            )
        ],
    )

    before = "入口外的密林压着阴影，封印在她身后缓缓闭合。五位师兄站在阵外。"
    updated_texts, updated_plan = await executor.execute(
        plan,
        {0: before},
        context={
            "scene_contracts": {
                0: {
                    "must_show": ["五位师兄合力破解封印"],
                    "opening_state": "入口外的密林压着阴影，封印在她身后缓缓闭合。",
                    "ending_state": "联合五位师兄合力破解封印。",
                }
            }
        },
    )

    order = updated_plan.orders[0]
    assert order.status == "succeeded"
    assert updated_texts[0] != before
    assert "入口外的" not in order.repair_audit["removed_protected_terms"]
    assert "封印在她" not in order.repair_audit["removed_protected_terms"]


@pytest.mark.asyncio
async def test_executor_rejects_artifact_repair_when_target_span_does_not_improve():
    executor = ChapterRepairExecutor()
    executor._scene_repairer = _StaticRepairer("一句——两句——三句")

    plan = ChapterRepairPlan(
        case_id="case-span",
        status="needs_repair",
        orders=[
            ChapterRepairOrder(
                order_id="order-span",
                target_scenes=[0],
                owner_scene=0,
                repair_type="local_patch",
                priority="high",
                reason="Dash density is too high.",
                instruction="Reduce em dashes.",
                violation_details=[
                    {
                        "type": "ai_punctuation_artifact",
                        "severity": "high",
                        "target_span": "——",
                    }
                ],
            )
        ],
    )

    updated_texts, updated_plan = await executor.execute(plan, {0: "一句——两句——三句"})

    order = updated_plan.orders[0]
    assert order.status == "succeeded"
    assert updated_texts[0].count("——") < 2
    assert order.repair_audit["failures"] == []
    assert order.repair_audit["target_span_checks"][0]["before_count"] == 2
    assert order.repair_audit["target_span_checks"][0]["after_count"] < 2


@pytest.mark.asyncio
async def test_executor_deterministically_reduces_dash_density_to_budget():
    repairer = _NoopRepairer()
    executor = ChapterRepairExecutor()
    executor._scene_repairer = repairer

    before = (
        "石斫猛然停住——风从廊下卷过——灯影晃了一下——她听见身后有声响。"
        "她没有回头——只是把银线草攥紧——伤口又裂开——血顺着袖口滴落。"
        "这不是退路——是最后的机会。"
    )
    plan = ChapterRepairPlan(
        case_id="case-dash",
        status="needs_repair",
        orders=[
            ChapterRepairOrder(
                order_id="order-dash",
                target_scenes=[0],
                owner_scene=0,
                repair_type="local_patch",
                repair_domain="anti_ai",
                priority="high",
                reason="破折号使用密度偏高。",
                instruction="将多余破折号改成逗号、句号、冒号或短句拆分。",
                violation_details=[
                    {
                        "type": "ai_punctuation_artifact",
                        "metric": "dash_per_1000",
                        "severity": "high",
                        "target_span": "——",
                        "threshold": 2,
                    }
                ],
                expected_after_repair={"max_dash_count": 2},
            )
        ],
    )

    updated_texts, updated_plan = await executor.execute(plan, {0: before})

    order = updated_plan.orders[0]
    assert order.status == "succeeded"
    assert updated_texts[0].count("——") <= 2
    assert updated_texts[0] != before
    assert "不是退路，而是" in updated_texts[0]
    assert order.repair_audit["dash_density_check"]["target_count"] == 2
    assert order.repair_audit["dash_density_check"]["after_count"] <= 2
    assert order.repair_audit["failures"] == []
    assert repairer.contexts == []


@pytest.mark.asyncio
async def test_executor_reduces_single_and_ascii_dash_artifacts_to_budget():
    repairer = _NoopRepairer()
    executor = ChapterRepairExecutor()
    executor._scene_repairer = repairer

    before = "甲——乙。丙—丁。戊--己。庚-辛。壬—癸。"
    plan = ChapterRepairPlan(
        case_id="case-dash-variants",
        status="needs_repair",
        orders=[
            ChapterRepairOrder(
                order_id="order-dash-variants",
                target_scenes=[0],
                owner_scene=0,
                repair_type="local_patch",
                repair_domain="anti_ai",
                priority="high",
                reason="Dash density exceeds the skill contract.",
                instruction="Reduce dash artifacts.",
                violation_details=[
                    {
                        "type": "dash_per_1000",
                        "metric": "dash_per_1000",
                        "severity": "high",
                        "expected_max": 1,
                    }
                ],
                expected_after_repair={"max_dash_count": 1},
            )
        ],
    )

    updated_texts, updated_plan = await executor.execute(plan, {0: before})

    order = updated_plan.orders[0]
    assert order.status == "succeeded"
    assert count_dash_artifacts(before) == 5
    assert count_dash_artifacts(updated_texts[0]) <= 1
    assert order.repair_audit["dash_density_check"]["before_count"] == 5
    assert order.repair_audit["dash_density_check"]["after_count"] <= 1
    assert repairer.contexts == []


def test_final_gate_text_repair_rebalances_rhythm_and_paragraph_shape():
    before = (
        "这意味着，凤溪抬手看着石门，风声压在门缝里。"
        "凤溪抬手看着石门，脚步停在原地，灯影在墙上晃动。"
        "不是退路而是最后的机会。"
        "\n\n"
        "凤溪抬手看着石门，衣袖擦过墙面，灰尘落下来。"
    )

    result = repair_final_gate_text(
        before,
        [
            {"metric": "structure_word_cluster_count"},
            {"metric": "paragraph_shape_repeat_count"},
            {"metric": "mirrored_paragraph_opening_count"},
        ],
    )

    assert result.changed
    assert "rhythm_paragraph_shape" in result.applied
    assert "这意味着" not in result.text
    assert "不是退路。最后的机会" in result.text
    assert result.text.count("凤溪抬手") < before.count("凤溪抬手")


def test_final_gate_text_repair_localizes_abstract_emotion_without_flattening_paragraphs():
    before = "她感到恐惧。这个局势很危险。\n\n局势的重要意义压在所有人身上。"

    result = repair_final_gate_text(
        before,
        [
            {"metric": "emotion_label_count"},
            {"metric": "abstract_bare_count"},
            {"metric": "standalone_abstract_claims"},
        ],
    )

    assert result.changed
    assert "evidence_localizer" in result.applied
    assert "感到恐惧" not in result.text
    assert "指尖贴住掌心" in result.text
    assert "门缝里的风声" in result.text
    assert "\n\n" in result.text


def test_final_gate_text_repair_locks_pov_and_splits_exposition():
    before = (
        "无人知道，石斫心里害怕。另一边，石斫知道真相。后来才知道危险。"
        "这个局势具有复杂意义，命运已经压下来，故事将在这里改变，"
        "所有关系都变得重要，气氛也越来越压抑，"
        "沉默继续堆叠，关系的重要性被反复强调，局势的特殊意义覆盖了每个人的位置。"
    )

    result = repair_final_gate_text(
        before,
        [
            {"metric": "single_pov_per_scene"},
            {"metric": "head_hopping_count"},
            {"metric": "pure_exposition_block_chars"},
        ],
        scene_contract={"pov": "凤溪"},
    )

    assert result.changed
    assert "pov_exposition_lock" in result.applied
    assert "无人知道" not in result.text
    assert "另一边" not in result.text
    assert "石斫心里" not in result.text
    assert "石斫知道" not in result.text
    assert "石斫的指节收紧" in result.text
    assert "石斫的目光在痕迹上停了一瞬" in result.text
    assert "\n\n" in result.text


def test_final_gate_text_repair_preserves_current_pov_negation_and_pronoun():
    before = (
        "凤溪不知道大师兄身体里发生了什么，"
        "但她知道必须在暗流蔓延前找到他。"
        "凤溪脑中掠过一幅旧画面。"
    )

    result = repair_final_gate_text(
        before,
        [{"metric": "head_hopping_count"}],
        scene_contract={"pov": "凤溪"},
    )

    assert result.text == before
    assert result.changed is False


@pytest.mark.asyncio
async def test_executor_applies_final_gate_text_repair_before_llm():
    repairer = _NoopRepairer()
    executor = ChapterRepairExecutor()
    executor._scene_repairer = repairer
    before = "她感到恐惧。这个局势很危险。"

    plan = ChapterRepairPlan(
        case_id="case-final-gate-local",
        status="needs_repair",
        orders=[
            ChapterRepairOrder(
                order_id="final-gate-local",
                target_scenes=[0],
                owner_scene=0,
                repair_type="local_patch",
                priority="high",
                reason="Final Gate found emotion_label_count and abstract_bare_count.",
                instruction="Reduce abstract emotion labels with concrete evidence.",
                violation_details=[
                    {"metric": "emotion_label_count", "severity": "high"},
                    {"metric": "abstract_bare_count", "severity": "high"},
                ],
            )
        ],
    )

    updated_texts, updated_plan = await executor.execute(plan, {0: before})

    order = updated_plan.orders[0]
    assert order.status == "succeeded"
    assert updated_texts[0] != before
    assert "指尖贴住掌心" in updated_texts[0]
    assert "门缝里的风声" in updated_texts[0]
    assert repairer.contexts == []
    assert order.repair_audit["executor_path"] == "final_gate_text_repair"
    assert "evidence_localizer" in order.repair_audit["primitive_repair"]["applied"]


@pytest.mark.asyncio
async def test_executor_applies_final_gate_text_repair_before_scene_rewrite_llm():
    repairer = _NoopRepairer()
    executor = ChapterRepairExecutor()
    executor._scene_repairer = repairer
    before = (
        "无人知道，石斫心里害怕。另一边，石斫知道真相。后来才知道危险。"
        "这个局势具有复杂意义，所有关系都变得重要，气氛也越来越压抑。"
    )

    plan = ChapterRepairPlan(
        case_id="case-final-gate-rewrite",
        status="needs_repair",
        orders=[
            ChapterRepairOrder(
                order_id="final-gate-rewrite",
                target_scenes=[0],
                owner_scene=0,
                repair_type="scene_rewrite",
                priority="high",
                reason="Final Gate found single_pov_per_scene and pure_exposition_block_chars.",
                instruction="Lock POV and break pure exposition without full regeneration.",
                violation_details=[
                    {"metric": "single_pov_per_scene", "severity": "high"},
                    {"metric": "pure_exposition_block_chars", "severity": "high"},
                ],
            )
        ],
    )

    updated_texts, updated_plan = await executor.execute(
        plan,
        {0: before},
        context={"scene_contracts": {0: {"pov": "凤溪"}}},
    )

    order = updated_plan.orders[0]
    assert order.status == "succeeded"
    assert updated_texts[0] != before
    assert "无人知道" not in updated_texts[0]
    assert "石斫的指节收紧" in updated_texts[0]
    assert repairer.contexts == []
    assert order.repair_audit["executor_path"] == "final_gate_text_repair"


@pytest.mark.asyncio
async def test_executor_preflight_skips_order_when_rule_evidence_is_absent():
    repairer = _CountingAppendRepairer(" should-not-run")
    executor = ChapterRepairExecutor()
    executor._scene_repairer = repairer
    before = "\u4e3b\u89d2\u4e0d\u77e5\u9053\u53d1\u751f\u4e86\u4ec0\u4e48\uff0c\u4f46\u5979\u77e5\u9053\u5fc5\u987b\u5c3d\u5feb\u627e\u5230\u540c\u4f34\u3002"
    plan = ChapterRepairPlan(
        case_id="case-rule-preflight-absence",
        status="needs_repair",
        orders=[
            ChapterRepairOrder(
                order_id="rule-preflight-absence",
                target_scenes=[0],
                owner_scene=0,
                repair_type="scene_rewrite",
                repair_domain="pov",
                priority="high",
                reason="Inner access to non-POV characters exceeds the skill contract.",
                violation_details=[
                    {
                        "validator": "pov_consistency",
                        "metric": "head_hopping_count",
                        "severity": "high",
                    }
                ],
            )
        ],
    )

    updated_texts, updated_plan = await executor.execute(
        plan,
        {0: before},
        context={"scene_contracts": {0: {"pov": "\u4e3b\u89d2"}}},
    )

    order = updated_plan.orders[0]
    assert updated_texts[0] == before
    assert repairer.contexts == []
    assert order.status == "skipped"
    assert order.repair_audit["accepted"] is True
    assert order.repair_audit["disposition"] == "no_confirmed_evidence"
    assert order.repair_audit["executor_path"] == "order_resolution_preflight"
    assert order.repair_audit["resolution_rule"] == "narrative.inner_access"
    assert order.repair_audit["warnings"] == ["no_confirmed_rule_evidence"]
    assert order.repair_audit["evidence_checks"][0]["confirmed_matches"] == 0


@pytest.mark.asyncio
async def test_executor_preflight_runs_repair_when_rule_evidence_remains():
    repairer = _CountingAppendRepairer(" patched")
    executor = ChapterRepairExecutor()
    executor._scene_repairer = repairer
    before = (
        "\u4e3b\u89d2\u63e1\u4f4f\u95e8\u73af\u3002"
        "\u914d\u89d2\u5fc3\u91cc\u4e00\u6c89\u3002"
    )
    plan = ChapterRepairPlan(
        case_id="case-rule-preflight-confirmed",
        status="needs_repair",
        orders=[
            ChapterRepairOrder(
                order_id="rule-preflight-confirmed",
                target_scenes=[0],
                owner_scene=0,
                repair_type="scene_rewrite",
                repair_domain="pov",
                priority="high",
                reason="Inner access to non-POV characters exceeds the skill contract.",
                violation_details=[
                    {
                        "validator": "pov_consistency",
                        "metric": "head_hopping_count",
                        "severity": "high",
                    }
                ],
            )
        ],
    )

    updated_texts, updated_plan = await executor.execute(
        plan,
        {0: before},
        context={"scene_contracts": {0: {"pov": "\u4e3b\u89d2"}}},
    )

    order = updated_plan.orders[0]
    assert updated_texts[0] != before
    assert order.status == "succeeded"
    assert order.repair_audit["accepted"] is True
    assert order.repair_audit["executor_path"] != "order_resolution_preflight"


@pytest.mark.asyncio
async def test_executor_applies_deterministic_fact_patch_before_llm():
    repairer = _StaticRepairer("should not be used")
    executor = ChapterRepairExecutor()
    executor._scene_repairer = repairer

    plan = ChapterRepairPlan(
        case_id="case-fact",
        status="needs_repair",
        orders=[
            ChapterRepairOrder(
                order_id="order-fact",
                target_scenes=[0],
                owner_scene=0,
                repair_type="local_patch",
                priority="high",
                reason="祭坛高度描述与scene_provenance矛盾：正文写'高约半丈'，但scene_provenance指定'约两人高的青石祭坛'。",
                instruction="Fix factual conflict.",
                violation_details=[
                    {
                        "type": "fact_conflict",
                        "severity": "high",
                        "detail": "祭坛高度描述与scene_provenance矛盾：正文写'高约半丈'，但scene_provenance指定'约两人高的青石祭坛'。",
                    }
                ],
            )
        ],
    )

    updated_texts, updated_plan = await executor.execute(
        plan,
        {0: "石台中央立着一座高约半丈的青石祭坛。"},
    )

    assert updated_plan.orders[0].status == "succeeded"
    assert updated_texts[0] == "石台中央立着一座约两人高的青石祭坛。"


@pytest.mark.asyncio
async def test_executor_skips_no_effect_quality_order_after_prior_scene_change():
    class _SequentialRepairer:
        def __init__(self):
            self.calls = 0

        async def execute(self, context: dict) -> dict:
            self.calls += 1
            if self.calls == 1:
                return {"success": True, "repaired_text": context["generated_text"] + " 新增现场阻碍。"}
            return {"success": True, "repaired_text": context["generated_text"]}

    executor = ChapterRepairExecutor()
    executor._scene_repairer = _SequentialRepairer()

    plan = ChapterRepairPlan(
        case_id="case-noop",
        status="needs_repair",
        orders=[
            ChapterRepairOrder(
                order_id="order-change",
                target_scenes=[0],
                owner_scene=0,
                repair_type="local_patch",
                priority="medium",
                reason="冲突密度不足，阻碍没有被现场化。",
                instruction="Add concrete obstacle.",
            ),
            ChapterRepairOrder(
                order_id="order-dup",
                target_scenes=[0],
                owner_scene=0,
                repair_type="local_patch",
                priority="medium",
                reason="悬念驱动不足，读者缺少明确想知道的下一层问题。",
                instruction="Improve curiosity.",
            ),
        ],
    )

    updated_texts, updated_plan = await executor.execute(plan, {0: "凤溪走进禁地。"})

    assert updated_texts[0].endswith("新增现场阻碍。")
    assert updated_plan.orders[0].status == "succeeded"
    assert updated_plan.orders[1].status == "skipped"
    assert updated_plan.orders[1].repair_audit["disposition"] == "no_effect_after_prior_change"


@pytest.mark.asyncio
async def test_executor_does_not_skip_fact_no_effect_after_prior_change_when_old_signature_remains():
    class _SequentialRepairer:
        def __init__(self):
            self.calls = 0

        async def execute(self, context: dict) -> dict:
            self.calls += 1
            if self.calls == 1:
                return {"success": True, "repaired_text": context["generated_text"] + " Prior edit."}
            return {"success": True, "repaired_text": context["generated_text"]}

    old = "clay jar base crack ran from the rim to the center."
    executor = ChapterRepairExecutor()
    executor._scene_repairer = _SequentialRepairer()
    plan = ChapterRepairPlan(
        case_id="case-fact-no-effect-after-prior",
        status="needs_repair",
        orders=[
            ChapterRepairOrder(
                order_id="order-prior-change",
                target_scenes=[0],
                owner_scene=0,
                repair_type="local_patch",
                priority="medium",
                reason="Add a local obstacle.",
                instruction="Add a local obstacle.",
            ),
            ChapterRepairOrder(
                order_id="order-fact-still-lingering",
                target_scenes=[0],
                owner_scene=0,
                repair_type="local_patch",
                repair_domain="fact",
                repair_lane="fact_local_patch",
                priority="high",
                reason=f"Text claim '{old}' conflicts with established fact 'stone trough was used'.",
                instruction="Fix the fact conflict.",
                repair_brief={
                    "fact_repair_goal": {
                        "text_claim": old,
                        "old_error_signatures": [old],
                        "forbidden_claims": [old],
                        "required_entities": ["stone trough base crack"],
                    },
                    "target_metrics": [{"metric": "internal_conflict", "direction": "resolve"}],
                },
                violation_details=[
                    {
                        "type": "internal_conflict",
                        "severity": "high",
                        "target_span": old,
                    }
                ],
            ),
        ],
    )

    updated_texts, updated_plan = await executor.execute(
        plan,
        {0: f"The pill formed. {old} Fengxi picked it up."},
    )

    assert "Prior edit." in updated_texts[0]
    assert old in updated_texts[0]
    assert updated_plan.orders[0].status == "succeeded"
    assert updated_plan.orders[1].status == "failed"
    assert updated_plan.orders[1].repair_audit["accepted"] is False
    assert "target_metric_not_improved" in updated_plan.orders[1].repair_audit["failures"]
    assert updated_plan.orders[1].repair_audit.get("disposition") != "no_effect_after_prior_change"


@pytest.mark.asyncio
async def test_executor_supersedes_earlier_no_effect_when_later_duplicate_improves():
    class _SequentialRepairer:
        def __init__(self):
            self.calls = 0

        async def execute(self, context: dict) -> dict:
            self.calls += 1
            if self.calls == 1:
                return {"success": True, "repaired_text": context["generated_text"]}
            return {"success": True, "repaired_text": context["generated_text"].replace("旧词", "新词", 1)}

    executor = ChapterRepairExecutor()
    executor._scene_repairer = _SequentialRepairer()
    violation = {
        "type": "repetition_artifact",
        "severity": "high",
        "target_span": "旧词",
    }
    plan = ChapterRepairPlan(
        case_id="case-dupe",
        status="needs_repair",
        orders=[
            ChapterRepairOrder(
                order_id="dash-1",
                target_scenes=[0],
                owner_scene=0,
                repair_type="local_patch",
                priority="high",
                reason="重复词过多。",
                instruction="Reduce repeated phrase.",
                violation_details=[violation],
            ),
            ChapterRepairOrder(
                order_id="dash-2",
                target_scenes=[0],
                owner_scene=0,
                repair_type="local_patch",
                priority="high",
                reason="重复词过多。",
                instruction="Reduce repeated phrase.",
                violation_details=[violation],
            ),
        ],
    )

    updated_texts, updated_plan = await executor.execute(plan, {0: "旧词，旧词，保留。"})

    assert updated_texts[0].count("旧词") == 1
    assert updated_plan.orders[0].status == "skipped"
    assert updated_plan.orders[0].repair_audit["disposition"] == "superseded_by_later_scene_change"
    assert updated_plan.orders[1].status == "succeeded"


@pytest.mark.asyncio
async def test_executor_applies_english_fact_conflict_suggested_correction():
    executor = ChapterRepairExecutor()
    executor._scene_repairer = _StaticRepairer("should not be used")
    detail = (
        "Text claim '正文描述：甬道两侧的石壁上嵌着夜明珠' "
        "conflicts with established fact '合同场景事实层：穹顶镶嵌夜明珠'; "
        "suggested correction: '将正文中的石壁夜明珠改为穹顶夜明珠，或调整合同设定以匹配正文。'"
    )
    plan = ChapterRepairPlan(
        case_id="case-en-fact",
        status="needs_repair",
        orders=[
            ChapterRepairOrder(
                order_id="fact-en",
                target_scenes=[0],
                owner_scene=0,
                repair_type="local_patch",
                priority="high",
                reason=detail,
                instruction="Fix fact conflict.",
                violation_details=[{"type": "fact_conflict", "severity": "high", "detail": detail}],
            )
        ],
    )

    updated_texts, updated_plan = await executor.execute(
        plan,
        {0: "甬道两侧的石壁上嵌着夜明珠，光线冷白。"},
    )

    assert updated_plan.orders[0].status == "succeeded"
    assert "穹顶镶嵌夜明珠" in updated_texts[0]
    assert "石壁上嵌着夜明珠" not in updated_texts[0]


@pytest.mark.asyncio
async def test_executor_applies_internal_conflict_suggested_fragment_patch_before_llm():
    repairer = _NoopRepairer()
    executor = ChapterRepairExecutor()
    executor._scene_repairer = repairer
    old = "clay jar base crack ran from the rim to the center."
    expected = "stone trough base crack ran from the rim to the center."
    suggestion = "delete or replace with 'stone trough base crack' to match the established object."
    detail = (
        f"Text claim '{old}' conflicts with established fact 'only a stone trough was used'; "
        f"suggested correction: \"{suggestion}\""
    )
    plan = ChapterRepairPlan(
        case_id="case-internal-suggested-fragment",
        status="needs_repair",
        orders=[
            ChapterRepairOrder(
                order_id="internal-suggested-fragment",
                target_scenes=[0],
                owner_scene=0,
                repair_type="local_patch",
                repair_domain="fact",
                repair_lane="fact_local_patch",
                priority="high",
                reason=detail,
                instruction="Apply the suggested correction.",
                repair_brief={
                    "fact_repair_goal": {
                        "text_claim": old,
                        "old_error_signatures": [old],
                        "forbidden_claims": [old],
                        "suggested_correction": suggestion,
                    },
                    "target_metrics": [{"metric": "internal_conflict", "direction": "resolve"}],
                },
                violation_details=[
                    {
                        "type": "internal_conflict",
                        "severity": "high",
                        "detail": detail,
                        "target_span": old,
                    }
                ],
            )
        ],
    )

    updated_texts, updated_plan = await executor.execute(
        plan,
        {0: f"The liquid settled. {old} Fengxi picked up the pill."},
    )

    order = updated_plan.orders[0]
    assert order.status == "succeeded"
    assert expected in updated_texts[0]
    assert old not in updated_texts[0]
    assert repairer.contexts == []
    assert order.repair_audit["primitive_repair"]["executor_path"] == "deterministic_fact_suggested_correction"


@pytest.mark.asyncio
async def test_workbench_semantic_creative_order_uses_bounded_inline_window():
    before = (
        "无关的前文。" * 500
        + "\n\n她刚才还能一一唤出他们的称呼。"
        + "\n\n你们是谁？\n\n风吹过废墟。"
    )
    after = before.replace(
        "你们是谁？",
        "灵魂重塑时，刚才的记忆已从她意识中脱落。\n\n你们是谁？",
        1,
    )
    repairer = _StaticRepairer(after)
    executor = ChapterRepairExecutor()
    executor._scene_repairer = repairer
    order = ChapterRepairOrder(
        order_id="bounded-semantic-inline",
        owner_scene=0,
        target_scenes=[0],
        repair_type="local_patch",
        source_violation_ids=["issue-one"],
        tool_commands=[{
            "operation": "llm_creative_rewrite",
            "scene_index": 0,
            "target_span": "你们是谁？",
        }],
        repair_brief={
            "workbench_semantic_route": "bounded_creative_work_unit",
            "target_behavior": "保留问句并补足失忆因果。",
        },
        violation_details=[{
            "issue_id": "issue-one",
            "type": "internal_conflict",
            "severity": "critical",
            "target_span": "你们是谁？",
            "detail": "前后认知缺少失忆过渡。",
        }],
    )

    repaired = await executor._repair_local_patch(
        order,
        before,
        0,
        {"scene_contracts": {0: {}}, "force_change": True},
        {},
    )

    assert repaired == after
    context = repairer.contexts[0]
    assert context["repair_strategy"] == "inline"
    assert context["bounded_semantic_repair"] is True
    assert context["bounded_target_span"] == "你们是谁？"
    assert "你们是谁？" in context["inline_window_text"]
    assert len(context["inline_window_text"]) < 1200
    assert context["revision_hints"][0]["target_span"] == "你们是谁？"


@pytest.mark.asyncio
async def test_workbench_cross_scene_consolidation_uses_owner_tail_and_readonly_context():
    owner_before = (
        "early owner prose." * 350
        + "\n\nThe battle ended.\n\nShe woke too early.\n\nShe recognized everyone."
    )
    owner_after = owner_before.replace(
        "\n\nShe woke too early.\n\nShe recognized everyone.",
        "\n\nVoices reached her through the dark.",
    )
    readonly_scene = "She woke once, with no memory.\n\n你们是谁？"
    repairer = _StaticRepairer(owner_after)
    executor = ChapterRepairExecutor()
    executor._scene_repairer = repairer
    order = ChapterRepairOrder(
        order_id="bounded-cross-scene",
        owner_scene=0,
        target_scenes=[0],
        repair_type="local_patch",
        source_violation_ids=["issue-cross-scene"],
        tool_commands=[{
            "operation": "llm_creative_rewrite",
            "scene_index": 0,
            "target_span": "你们是谁？",
        }],
        repair_brief={
            "workbench_semantic_route": "bounded_cross_scene_consolidation",
            "target_behavior": "Keep the later amnesia ending and consolidate the earlier duplicate wake-up.",
            "external_protected_span": {
                "span": "你们是谁？",
                "scene_index": 1,
                "expected_count": 1,
            },
        },
        violation_details=[{
            "issue_id": "issue-cross-scene",
            "type": "internal_conflict",
            "severity": "critical",
            "target_span": "你们是谁？",
            "detail": "The owner scene duplicates the later wake-up event.",
        }],
    )

    repaired = await executor._repair_local_patch(
        order,
        owner_before,
        0,
        {"scene_contracts": {0: {}, 1: {}}, "force_change": True},
        {"all_scene_texts": {0: owner_before, 1: readonly_scene}},
    )

    assert repaired == owner_after
    context = repairer.contexts[0]
    assert context["repair_strategy"] == "inline"
    assert context["bounded_cross_scene_consolidation"] is True
    assert len(context["inline_window_text"]) <= 2500
    assert "She woke too early." in context["inline_window_text"]
    assert "early owner prose." not in context["inline_window_text"]
    assert context["external_protected_context_text"] == readonly_scene
    assert context["generated_text"] == owner_before


@pytest.mark.asyncio
async def test_workbench_cross_scene_consolidation_defers_conflict_to_semantic_recheck():
    from app.services.fbi.work_unit_builder import build_revision_blueprint

    owner_before = "The battle ended.\n\nShe woke too early.\n\nShe recognized everyone."
    owner_after = "The battle ended.\n\nVoices reached her through the dark."
    readonly_scene = "She woke once, with no memory.\n\n你们是谁？"
    executor = ChapterRepairExecutor()
    executor._scene_repairer = _StaticRepairer(owner_after)
    order = ChapterRepairOrder(
        order_id="cross-scene-semantic-recheck",
        owner_scene=0,
        target_scenes=[0],
        repair_type="local_patch",
        repair_domain="fact",
        source_violation_ids=["issue-cross-scene"],
        tool_commands=[{
            "operation": "llm_creative_rewrite",
            "scene_index": 0,
            "target_span": "你们是谁？",
            "source_issue_ids": ["issue-cross-scene"],
        }],
        repair_brief={
            "workbench_semantic_route": "bounded_cross_scene_consolidation",
            "target_behavior": "Keep the later ending and remove the earlier duplicate event.",
            "tool_commands": [{
                "operation": "llm_creative_rewrite",
                "scene_index": 0,
                "target_span": "你们是谁？",
                "source_issue_ids": ["issue-cross-scene"],
            }],
            "target_metrics": [{"metric": "internal_conflict", "direction": "resolve"}],
            "repair_goals": [{
                "goal_id": "goal-cross-scene",
                "blocks_commit": True,
                "acceptance_criteria": [{
                    "metric": "internal_conflict",
                    "operator": "resolved",
                }],
            }],
            "external_protected_span": {
                "span": "你们是谁？",
                "scene_index": 1,
                "expected_count": 1,
            },
        },
        violation_details=[{
            "issue_id": "issue-cross-scene",
            "type": "internal_conflict",
            "severity": "critical",
            "target_span": "你们是谁？",
            "detail": "The owner scene duplicates the later wake-up event.",
        }],
    )
    plan = ChapterRepairPlan(
        case_id="cross-scene-semantic-recheck",
        status="needs_repair",
        orders=[order],
    )
    plan.revision_blueprint = build_revision_blueprint(plan.case_id, plan.orders)
    plan.work_units = list(plan.revision_blueprint.work_units)

    updated_texts, updated_plan = await executor.execute(
        plan,
        {0: owner_before, 1: readonly_scene},
        context={
            "scene_contracts": {0: {}, 1: {}},
            "force_change": True,
            "workbench_smart_repair": True,
        },
        case_file={"all_scene_texts": {0: owner_before, 1: readonly_scene}},
    )

    updated_order = updated_plan.orders[0]
    assert updated_texts[0] == owner_after
    assert updated_texts[1] == readonly_scene
    assert updated_order.status == "succeeded"
    assert updated_order.repair_audit["target_self_check"]["deferred_to_recheck"] is True
    assert updated_order.repair_audit["target_self_check"]["metric"] == "internal_conflict"


def test_executor_does_not_supersede_fact_failure_when_old_signature_remains():
    """方案6 B1：_supersede_no_effect_failures_after_later_repairs 已删除。

    failed 就是 failed，不再被假装成功。此测试验证方法已不存在，
    且 failed 订单状态不会被后续修复覆盖。
    """
    executor = ChapterRepairExecutor()
    # 方法已删除，不应存在
    assert not hasattr(executor, "_supersede_no_effect_failures_after_later_repairs")


@pytest.mark.asyncio
async def test_executor_applies_chinese_fact_conflict_height_patch():
    executor = ChapterRepairExecutor()
    executor._scene_repairer = _StaticRepairer("should not be used")
    detail = "scene_provenance 规定祭坛高约丈余（约3.3米），但正文中描述祭坛高三尺（约1米），高度不符。"
    plan = ChapterRepairPlan(
        case_id="case-height",
        status="needs_repair",
        orders=[
            ChapterRepairOrder(
                order_id="height",
                target_scenes=[0],
                owner_scene=0,
                repair_type="local_patch",
                priority="high",
                reason=detail,
                instruction="Fix height conflict.",
                violation_details=[{"type": "fact_conflict", "severity": "high", "detail": detail}],
            )
        ],
    )

    updated_texts, updated_plan = await executor.execute(plan, {0: "祭坛高三尺。凤溪停下脚步。"})

    assert updated_plan.orders[0].status == "succeeded"
    assert "祭坛高约丈余" in updated_texts[0]
    assert "祭坛高三尺" not in updated_texts[0]


@pytest.mark.asyncio
async def test_executor_applies_antidote_quantity_fact_patch_before_llm():
    executor = ChapterRepairExecutor()
    executor._scene_repairer = _StaticRepairer("should not be used")
    detail = "当前事实层规定凤溪仅有“一粒解毒丹”，但正文写成“半瓶解毒丹”，数量不一致。"
    plan = ChapterRepairPlan(
        case_id="case-antidote-quantity",
        status="needs_repair",
        orders=[
            ChapterRepairOrder(
                order_id="antidote",
                target_scenes=[0],
                owner_scene=0,
                repair_type="local_patch",
                priority="high",
                reason=detail,
                instruction="Fix quantity conflict.",
                violation_details=[{"type": "fact_conflict", "severity": "high", "detail": detail}],
            )
        ],
    )

    updated_texts, updated_plan = await executor.execute(
        plan,
        {0: "凤溪倒出半瓶解毒丹，递到五师兄唇边。"},
    )

    assert updated_plan.orders[0].status == "succeeded"
    assert "一粒解毒丹" in updated_texts[0]
    assert "半瓶解毒丹" not in updated_texts[0]


@pytest.mark.asyncio
async def test_executor_batches_local_quality_orders_for_same_scene():
    repairer = _CountingAppendRepairer(" Pressure rises. A new question remains.")
    executor = ChapterRepairExecutor()
    executor._scene_repairer = repairer

    plan = ChapterRepairPlan(
        case_id="case-batch-quality",
        status="needs_repair",
        orders=[
            ChapterRepairOrder(
                order_id="conflict",
                target_scenes=[0],
                owner_scene=0,
                repair_type="local_patch",
                priority="medium",
                reason="Conflict density is low.",
                instruction="Add an immediate obstacle.",
                violation_details=[{
                    "type": "low_conflict_density",
                    "severity": "medium",
                    "detail": "Conflict density is low.",
                }],
            ),
            ChapterRepairOrder(
                order_id="curiosity",
                target_scenes=[0],
                owner_scene=0,
                repair_type="local_patch",
                priority="medium",
                reason="Curiosity engine is weak.",
                instruction="Leave a concrete reader question.",
                violation_details=[{
                    "type": "weak_curiosity_engine",
                    "severity": "medium",
                    "detail": "Curiosity engine is weak.",
                }],
            ),
        ],
    )

    updated_texts, updated_plan = await executor.execute(plan, {0: "Fengxi enters the valley."})

    assert updated_texts[0].endswith("A new question remains.")
    assert len(repairer.contexts) == 1
    assert len(repairer.contexts[0]["violations"]) == 2
    assert [order.status for order in updated_plan.orders] == ["succeeded", "succeeded"]
    assert updated_plan.orders[0].repair_audit["executor_path"] == "batch_local_quality"
    assert updated_plan.orders[0].repair_audit["batch_size"] == 2
    assert updated_plan.orders[1].repair_audit["batch_order_ids"] == ["conflict", "curiosity"]


@pytest.mark.asyncio
async def test_executor_adds_bridge_for_unprovenanced_clue():
    executor = ChapterRepairExecutor()
    executor._scene_repairer = _StaticRepairer("should not be used")
    detail = "正文中出现‘送归者，吾之真身’字样以及玉佩与凤溪靴筒内的玉佩相同，但未交代这些线索的放置者或来源，属于无来源线索。"
    target = "“送归者，吾之真身。”……玉佩的形状和她靴筒里那枚一模一样。"
    plan = ChapterRepairPlan(
        case_id="case-clue",
        status="needs_repair",
        orders=[
            ChapterRepairOrder(
                order_id="clue",
                target_scenes=[0],
                owner_scene=0,
                repair_type="local_patch",
                priority="high",
                reason=detail,
                instruction="Add clue provenance.",
                violation_details=[{
                    "type": "clue_provenance_error",
                    "severity": "high",
                    "detail": detail,
                    "target_span": target,
                }],
            )
        ],
    )

    text = "石壁上刻着“送归者，吾之真身。”玉佩的形状和她靴筒里那枚一模一样。"
    updated_texts, updated_plan = await executor.execute(plan, {0: text})

    assert updated_plan.orders[0].status == "succeeded"
    assert "她先前发现后将它收起" in updated_texts[0]
    assert "至今不知道是谁留下的" in updated_texts[0]
    assert "不是凭空出现" not in updated_texts[0]
    assert "先到者刻意留下" not in updated_texts[0]
    assert "祭坛附近" not in updated_texts[0]


@pytest.mark.asyncio
async def test_executor_normalizes_dash_artifact_deterministically():
    executor = ChapterRepairExecutor()
    executor._scene_repairer = _StaticRepairer("should not be used")
    plan = ChapterRepairPlan(
        case_id="case-dash",
        status="needs_repair",
        orders=[
            ChapterRepairOrder(
                order_id="dash",
                target_scenes=[0],
                owner_scene=0,
                repair_type="local_patch",
                priority="high",
                reason="破折号使用密度偏高。",
                instruction="Reduce dash density.",
                violation_details=[{
                    "type": "ai_punctuation_artifact",
                    "severity": "high",
                    "target_span": "——",
                }],
            )
        ],
    )

    updated_texts, updated_plan = await executor.execute(plan, {0: "一句——两句——三句——四句"})

    assert updated_plan.orders[0].status == "succeeded"
    assert updated_texts[0].count("——") < 3


@pytest.mark.asyncio
async def test_executor_applies_review_minister_patch_plan_before_llm():
    executor = ChapterRepairExecutor()
    executor._scene_repairer = _StaticRepairer("should not be used")
    plan = ChapterRepairPlan(
        case_id="case-structured-patch",
        status="needs_repair",
        orders=[
            ChapterRepairOrder(
                order_id="fact-patch",
                target_scenes=[0],
                owner_scene=0,
                repair_type="local_patch",
                repair_domain="fact",
                repair_lane="fact_local_patch",
                repair_strength="S2",
                repair_brief={
                    "patch_plan": [
                        {
                            "operation": "replace_phrase",
                            "from": "right arm",
                            "to": "left arm",
                        }
                    ],
                    "target_metrics": [
                        {
                            "metric": "fact_conflict",
                            "direction": "resolved",
                        }
                    ],
                },
                violation_details=[
                    {
                        "type": "fact_conflict",
                        "target_span": "right arm",
                        "detail": "Use the established left arm fact.",
                    }
                ],
            )
        ],
    )

    updated_texts, updated_plan = await executor.execute(
        plan,
        {0: "The wound was on his right arm."},
    )

    assert updated_plan.orders[0].status == "succeeded"
    assert updated_texts[0] == "The wound was on his left arm."
    assert updated_plan.orders[0].repair_audit["primitive_repair"]["executor_path"] == "structured_patch_plan"


@pytest.mark.asyncio
async def test_executor_runs_structured_dash_normalization_on_full_scene():
    repairer = _NoopRepairer()
    executor = ChapterRepairExecutor()
    executor._scene_repairer = repairer
    dash_text = (
        "他抬手——门开了。"
        "她转身——风停了。"
        "这不是退路——是最后的机会。"
        "灯影一晃——有人来了。"
        "石门背后——传来水声。"
        "凤溪停住——下一刻，脚步声贴近门外。"
    )
    before = "山风掠过石壁。" * 450 + dash_text
    plan = ChapterRepairPlan(
        case_id="case-structured-dash",
        status="needs_repair",
        orders=[
            ChapterRepairOrder(
                order_id="dash-patch",
                target_scenes=[0],
                owner_scene=0,
                repair_type="local_patch",
                repair_domain="anti_ai",
                repair_lane="punctuation_cleanup",
                repair_strength="S1",
                repair_brief={
                    "patch_plan": [
                        {
                            "operation": "normalize_punctuation",
                            # 真实蓝图可能只给一个定位窗；密度工具仍须覆盖全场景。
                            "target_span": "他抬手——门开了。",
                        }
                    ],
                    "target_metrics": [
                        {
                            "metric": "dash_per_1000",
                            "direction": "decrease",
                            "expected_max": 0.5,
                        }
                    ],
                },
                violation_details=[
                    {
                        "type": "dash_per_1000",
                        "metric": "dash_per_1000",
                        "expected_max": 0.5,
                        "target_span": "他抬手——门开了。",
                    }
                ],
            )
        ],
    )

    updated_texts, updated_plan = await executor.execute(plan, {0: before})

    assert updated_plan.orders[0].status == "succeeded"
    assert count_dash_artifacts(before) == 6
    assert count_dash_artifacts(updated_texts[0]) <= 1
    assert "这不是退路，而是最后的机会" in updated_texts[0]
    assert repairer.contexts == []
    command = updated_plan.revision_blueprint.work_units[0].tool_batch.commands[0]
    assert command.operation == "normalize_punctuation"


@pytest.mark.asyncio
async def test_executor_round_finalization_reduces_dash_reintroduced_late():
    executor = ChapterRepairExecutor()
    executor._scene_repairer = _StaticRepairer(
        "Alpha——beta——gamma——delta——epsilon."
    )
    plan = ChapterRepairPlan(
        case_id="case-round-finalization",
        status="needs_repair",
        orders=[
            ChapterRepairOrder(
                order_id="quality",
                target_scenes=[0],
                owner_scene=0,
                repair_type="local_patch",
                repair_domain="rhythm",
                repair_lane="pacing_repair",
                repair_strength="S3",
                repair_brief={
                    "target_metrics": [
                        {
                            "metric": "low_conflict_density",
                            "direction": "increase",
                            "expected_max": 2.5,
                        }
                    ]
                },
                violation_details=[
                    {
                        "type": "low_conflict_density",
                        "detail": "Add pressure without leaving surface artifacts.",
                    }
                ],
            )
        ],
    )

    updated_texts, updated_plan = await executor.execute(plan, {0: "Alpha."})

    assert count_dash_artifacts(updated_texts[0]) <= 1
    finalization = updated_plan.repair_execution_summary["round_finalization"]
    assert finalization["cleanup_count"] == 1
    assert finalization["cleanups"][0]["cleanup"] == "dash_density"


def test_round_finalization_uses_dash_density_alias_threshold_on_long_scene():
    executor = ChapterRepairExecutor()
    plan = ChapterRepairPlan(
        case_id="case-round-finalization-threshold",
        status="needs_repair",
        orders=[
            ChapterRepairOrder(
                order_id="quality",
                target_scenes=[0],
                owner_scene=0,
                repair_type="local_patch",
                repair_brief={
                    "target_metrics": [
                        {
                            "metric": "dash_per_1000",
                            "expected_max": 0.5,
                        }
                    ]
                },
            )
        ],
    )
    updated_texts = {
        0: "山风掠过石壁。" * 450 + "甲——乙——丙——丁——戊——己——庚。",
    }

    result = executor._finalize_repair_round(
        plan,
        {0: "原始正文。"},
        updated_texts,
    )

    assert count_dash_artifacts(updated_texts[0]) <= 1
    assert result["dash_threshold"] == 0.5
    assert result["cleanup_count"] == 1
