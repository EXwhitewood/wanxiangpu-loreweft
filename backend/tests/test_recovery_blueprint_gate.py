from __future__ import annotations

import uuid

from app.db.db_models import Project
from app.services.story_plan_service import StoryPlanService


def _complete_scene() -> dict:
    return {
        "scene_id": "ch_003_s1",
        "goal": "陈长生查清药力来源",
        "conflict": "旧丹方与身体反应互相矛盾",
        "outcome": "确认需要寻找外部医者",
        "info_release": "旧庙医书已经失效",
        "hook": "线索指向师父",
    }


def test_recovery_flags_and_placeholder_text_both_block_generation():
    service = StoryPlanService()
    chapter = {
        "chapter_number": 3,
        "status": "needs_review",
        "recovery_generated": True,
        "core_conflict": "本章的具体阻力尚待作者补充",
    }

    diagnostics = service.validate_chapter_blueprint(chapter, [_complete_scene()])
    codes = {item["code"] for item in diagnostics if item["blocks_generation"]}

    assert "recovery_confirmation_required" in codes
    assert "recovery_placeholder_present" in codes


async def test_explicit_confirmation_clears_recovery_flags_only_after_detail_is_complete(db):
    project_id = uuid.uuid4()
    chapter = {
        "chapter_id": "ch_001",
        "chapter_number": 1,
        "title": "第一章",
        "status": "needs_review",
        "recovery_generated": True,
        "core_conflict": "主角必须在追兵抵达前取得证据",
        "value_shift": {"from": "被动", "to": "主动"},
        "hook": "证据指向最信任的人",
        "pov_character": "陈长生",
    }
    project = Project(
        id=project_id,
        name="outline",
        outline_data={
            "chapter_spine": [chapter],
            "scene_briefs": {"ch_001": {"chapter_id": "ch_001", "scenes": [_complete_scene()]}},
            "meta": {"version": 1, "frozen_layers": []},
        },
    )
    db.add(project)
    await db.commit()

    result = await StoryPlanService().save_chapter_blueprint(
        project_id=project_id,
        chapter_number=1,
        chapter_updates=chapter,
        scenes=[_complete_scene()],
        expected_revision=0,
        db=db,
        confirm_recovery=True,
    )

    assert "error" not in result
    assert result["chapter"]["status"] == "planned"
    assert "recovery_generated" not in result["chapter"]
    assert not [item for item in result["diagnostics"] if item["blocks_generation"]]
