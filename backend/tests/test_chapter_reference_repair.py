import pytest

from app.models.chapter_review import ChapterRepairOrder, ChapterRepairPlan
from app.services.fbi.chapter_repair_executor import ChapterRepairExecutor


class _NoopRepairer:
    async def execute(self, context: dict) -> dict:
        return {"success": True, "repaired_text": context["generated_text"]}


@pytest.mark.asyncio
async def test_executor_removes_chapter_number_reference_deterministically():
    executor = ChapterRepairExecutor()
    executor._scene_repairer = _NoopRepairer()

    plan = ChapterRepairPlan(
        case_id="case-chapter-ref",
        status="needs_repair",
        orders=[
            ChapterRepairOrder(
                order_id="chapter-ref",
                target_scenes=[0],
                owner_scene=0,
                repair_type="local_patch",
                priority="critical",
                reason="正文中不应出现章节号等元叙事标记: 第11章",
                instruction="Remove meta chapter reference.",
                violation_details=[{
                    "type": "chapter_reference",
                    "severity": "critical",
                    "detail": "正文中不应出现章节号等元叙事标记: 第11章",
                    "target_span": "第11章",
                }],
            )
        ],
    )

    text = "这个气味她很熟悉——第11章藏经阁的木箱碎开时，那股香味就是这个。"
    updated_texts, updated_plan = await executor.execute(plan, {0: text})

    assert updated_plan.orders[0].status == "succeeded"
    assert "第11章" not in updated_texts[0]
    assert "先前在藏经阁" in updated_texts[0]


@pytest.mark.asyncio
async def test_executor_removes_relative_chapter_reference_deterministically():
    executor = ChapterRepairExecutor()
    executor._scene_repairer = _NoopRepairer()

    plan = ChapterRepairPlan(
        case_id="case-relative-ref",
        status="needs_repair",
        orders=[
            ChapterRepairOrder(
                order_id="relative-ref",
                target_scenes=[0],
                owner_scene=0,
                repair_type="local_patch",
                priority="high",
                reason="正文中不应出现章节引用: 上一章",
                instruction="Remove meta chapter reference.",
                violation_details=[{
                    "type": "meta_narrative_reference",
                    "severity": "high",
                    "detail": "正文中不应出现章节引用: 上一章",
                    "target_span": "上一章",
                }],
            )
        ],
    )

    text = "上一章中留下的铜铃声，此刻又从石壁深处响起。"
    updated_texts, updated_plan = await executor.execute(plan, {0: text})

    assert updated_plan.orders[0].status == "succeeded"
    assert "上一章" not in updated_texts[0]
    assert updated_texts[0].startswith("先前留下的铜铃声")
