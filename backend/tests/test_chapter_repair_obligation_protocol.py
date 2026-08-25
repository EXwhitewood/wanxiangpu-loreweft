import pytest

from app.models.chapter_review import ChapterRepairOrder, ChapterRepairPlan
from app.services.fbi.chapter_repair_executor import ChapterRepairExecutor


class _StaticRepairer:
    def __init__(self, repaired_text: str):
        self.repaired_text = repaired_text

    async def execute(self, context: dict) -> dict:
        return {"success": True, "repaired_text": self.repaired_text}


class _SequenceRepairer:
    def __init__(self, repaired_texts: list[str]):
        self.repaired_texts = list(repaired_texts)
        self.contexts: list[dict] = []

    async def execute(self, context: dict) -> dict:
        self.contexts.append(context)
        if not self.repaired_texts:
            return {"success": False, "error": "no candidate"}
        return {"success": True, "repaired_text": self.repaired_texts.pop(0)}


@pytest.mark.asyncio
async def test_executor_does_not_protect_performance_requirements_as_hard_obligations():
    executor = ChapterRepairExecutor()
    executor._scene_repairer = _StaticRepairer("fresh rewrite that solves the local problem")

    plan = ChapterRepairPlan(
        case_id="case-performance-layer",
        status="needs_repair",
        orders=[
            ChapterRepairOrder(
                order_id="order-performance-layer",
                target_scenes=[0],
                owner_scene=0,
                repair_type="scene_rewrite",
                priority="high",
                reason="Rewrite scene.",
                instruction="Rewrite scene.",
            )
        ],
    )

    before = "old staging fragment appears in the draft."
    updated_texts, updated_plan = await executor.execute(
        plan,
        {0: before},
        context={
            "scene_contracts": {
                0: {
                    "must_show": ["old staging fragment"],
                    "performance_requirements": ["old staging fragment"],
                }
            }
        },
    )

    order = updated_plan.orders[0]
    assert order.status == "succeeded"
    assert updated_texts[0] != before
    assert order.repair_audit["removed_protected_terms"] == []


@pytest.mark.asyncio
async def test_executor_protects_hard_fact_obligation_groups():
    executor = ChapterRepairExecutor()
    executor._scene_repairer = _StaticRepairer("fresh rewrite that omits the known fact")

    plan = ChapterRepairPlan(
        case_id="case-hard-fact-layer",
        status="needs_repair",
        orders=[
            ChapterRepairOrder(
                order_id="order-hard-fact-layer",
                target_scenes=[0],
                owner_scene=0,
                repair_type="scene_rewrite",
                priority="high",
                reason="Rewrite scene.",
                instruction="Rewrite scene.",
            )
        ],
    )

    before = "artifact-x remains sealed while the protagonist waits."
    updated_texts, updated_plan = await executor.execute(
        plan,
        {0: before},
        context={
            "scene_contracts": {
                0: {
                    "hard_facts": ["artifact-x remains sealed"],
                    "must_show": ["the protagonist waits"],
                }
            }
        },
    )

    order = updated_plan.orders[0]
    assert order.status == "failed"
    assert updated_texts[0] == before
    assert "protected_obligation_removed" in order.repair_audit["failures"]
    assert "artifact-x remains sealed" in order.repair_audit["removed_protected_terms"]


@pytest.mark.asyncio
async def test_executor_warns_but_accepts_removed_short_prose_fragments_in_scene_rewrite():
    executor = ChapterRepairExecutor()
    executor._scene_repairer = _StaticRepairer("The rewritten scene reaches the required outcome with new staging.")

    plan = ChapterRepairPlan(
        case_id="case-weak-prose-fragments",
        status="needs_repair",
        orders=[
            ChapterRepairOrder(
                order_id="order-weak-prose-fragments",
                target_scenes=[0],
                owner_scene=0,
                repair_type="scene_rewrite",
                priority="high",
                reason="Rewrite scene to reach the ending state.",
                instruction="Rewrite scene.",
            )
        ],
    )

    before = "但守护妖退进林里，草叶里的沙沙声停了，山壁透出薄薄的光。"
    updated_texts, updated_plan = await executor.execute(
        plan,
        {0: before},
        context={
            "scene_contracts": {
                0: {
                    "ending_state": "但守护妖退去，的沙沙声停下，薄薄的光露出。",
                }
            }
        },
    )

    order = updated_plan.orders[0]
    assert order.status == "succeeded"
    assert updated_texts[0] != before
    assert "protected_obligation_removed" not in order.repair_audit["failures"]
    assert order.repair_audit["removed_protected_terms"] == []
    assert set(order.repair_audit["weak_removed_protected_terms"]) >= {
        "但守护妖",
        "的沙沙声",
        "薄薄的光",
    }


@pytest.mark.asyncio
async def test_executor_falls_back_to_contract_completion_patch_when_rewrite_removes_protection():
    repairer = _SequenceRepairer([
        "凤溪独自站在封印前。",
        "五位师兄站在凤溪身后，五道灵力同时注入封印，封印随之松动。",
    ])
    executor = ChapterRepairExecutor()
    executor._scene_repairer = repairer

    plan = ChapterRepairPlan(
        case_id="case-protection-retry",
        status="needs_repair",
        orders=[
            ChapterRepairOrder(
                order_id="order-protection-retry",
                target_scenes=[0],
                owner_scene=0,
                repair_type="scene_rewrite",
                priority="high",
                reason="Rewrite scene.",
                instruction="Rewrite scene.",
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
                    "ending_state": "五位师兄合力破解封印",
                }
            }
        },
    )

    order = updated_plan.orders[0]
    assert order.status == "succeeded"
    assert updated_texts[0] == "五位师兄站在凤溪身后，五道灵力同时注入封印，封印随之松动。"
    assert len(repairer.contexts) == 2
    assert repairer.contexts[1]["repair_strategy"] == "patch"
    assert repairer.contexts[1]["revision_hints"][0]["strategy"] == "contract_completion_patch"
    assert repairer.contexts[1]["protection_retry"]["reason"] == "protected_obligation_removed"
    assert "五位师兄" in repairer.contexts[1]["repair_plan"]["preserve"]
    assert order.repair_audit["retry"]["reason"] == "protected_obligation_removed"


@pytest.mark.asyncio
async def test_executor_retries_contract_completion_patch_when_it_removes_protection():
    repairer = _SequenceRepairer([
        "Fengxi steadies her breath and ends the scene without the protected beat.",
        "Fengxi steadies her breath, preserves breakthrough core, and completes the missing ending.",
    ])
    executor = ChapterRepairExecutor()
    executor._scene_repairer = repairer

    plan = ChapterRepairPlan(
        case_id="case-contract-protection-retry",
        status="needs_repair",
        orders=[
            ChapterRepairOrder(
                order_id="order-contract-protection-retry",
                target_scenes=[0],
                owner_scene=0,
                repair_type="contract_completion_patch",
                priority="high",
                reason="Scene ending state is incomplete.",
                instruction="Complete the ending state.",
                expected_after_repair={"preserve": ["breakthrough core"]},
            )
        ],
    )

    before = "Fengxi keeps breakthrough core visible but has not completed the ending."
    updated_texts, updated_plan = await executor.execute(
        plan,
        {0: before},
        context={
            "scene_contracts": {
                0: {
                    "ending_state": "breakthrough core completes the ending",
                }
            }
        },
    )

    order = updated_plan.orders[0]
    assert order.status == "succeeded"
    assert updated_texts[0] == "Fengxi steadies her breath, preserves breakthrough core, and completes the missing ending."
    assert len(repairer.contexts) == 2
    assert repairer.contexts[1]["repair_strategy"] == "patch"
    assert repairer.contexts[1]["protection_retry"]["reason"] == "protected_obligation_removed"
    assert "breakthrough core" in repairer.contexts[1]["repair_plan"]["preserve"]
    assert order.repair_audit["retry"]["reason"] == "protected_obligation_removed"


@pytest.mark.asyncio
async def test_executor_retries_local_patch_when_it_removes_protection():
    repairer = _SequenceRepairer([
        "Fengxi replaces the problem but drops the rank marker.",
        "Fengxi keeps Golden Core early visible and answers with sharper action.",
    ])
    executor = ChapterRepairExecutor()
    executor._scene_repairer = repairer

    plan = ChapterRepairPlan(
        case_id="case-local-protection-retry",
        status="needs_repair",
        orders=[
            ChapterRepairOrder(
                order_id="order-local-protection-retry",
                target_scenes=[0],
                owner_scene=0,
                repair_type="local_patch",
                priority="high",
                reason="Replace flat phrasing without changing facts.",
                instruction="Replace flat phrase.",
                expected_after_repair={"preserve": ["Golden Core early"]},
                violation_details=[
                    {
                        "type": "emotional_claim_without_scene_evidence",
                        "severity": "high",
                        "target_span": "flat phrase",
                        "blocks_commit": True,
                    }
                ],
            )
        ],
    )

    before = "Fengxi keeps Golden Core early visible and uses a flat phrase."
    updated_texts, updated_plan = await executor.execute(plan, {0: before})

    order = updated_plan.orders[0]
    assert order.status == "succeeded"
    assert updated_texts[0] == (
        "Fengxi keeps Golden Core early visible and answers with sharper action."
    )
    assert len(repairer.contexts) == 2
    assert repairer.contexts[1]["repair_strategy"] == "patch"
    assert repairer.contexts[1]["protection_retry"]["reason"] == "protected_obligation_removed"
    assert "Golden Core early" in repairer.contexts[1]["repair_plan"]["preserve"]
    assert repairer.contexts[1]["revision_hints"][0]["strategy"] == "local_patch_protection_retry"
    assert order.repair_audit["retry"]["reason"] == "protected_obligation_removed"
