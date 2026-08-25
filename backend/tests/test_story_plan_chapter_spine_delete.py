import uuid

import pytest

from app.db.db_models import Project
from app.services.story_plan_service import StoryPlanService


@pytest.mark.asyncio
async def test_delete_first_chapter_spine_item_renumbers_following_outline(db):
    project_id = uuid.uuid4()
    db.add(
        Project(
            id=project_id,
            name="delete-first-spine-test",
            outline_data={
                "chapter_spine": [
                    {"chapter_number": 1, "chapter_id": "ch_001", "title": "one"},
                    {"chapter_number": 2, "chapter_id": "ch_002", "title": "two"},
                ],
                "story_plan": {
                    "chapter_spine": [
                        {"chapter_number": 1, "chapter_id": "ch_001", "title": "one"},
                        {"chapter_number": 2, "chapter_id": "ch_002", "title": "two"},
                    ],
                    "meta": {"version": 1},
                },
            },
        )
    )
    await db.commit()

    result = await StoryPlanService().delete_chapter_spine_item(project_id, 1, db)
    await db.commit()

    project = await db.get(Project, project_id)
    spine = project.outline_data["chapter_spine"]

    assert result["deleted"] is True
    assert result["shifted_chapters"] == 1
    assert [item["chapter_number"] for item in spine] == [1]
    assert spine[0]["chapter_id"] == "ch_002"
    assert spine[0]["title"] == "two"


@pytest.mark.asyncio
async def test_delete_last_chapter_spine_item_keeps_contiguous_prefix(db):
    project_id = uuid.uuid4()
    db.add(
        Project(
            id=project_id,
            name="delete-last-spine-test",
            outline_data={
                "chapter_spine": [
                    {"chapter_number": 1, "chapter_id": "ch_001", "title": "one"},
                    {"chapter_number": 2, "chapter_id": "ch_002", "title": "two"},
                ],
                "meta": {"version": 1},
            },
        )
    )
    await db.commit()

    result = await StoryPlanService().delete_chapter_spine_item(project_id, 2, db)
    await db.commit()

    project = await db.get(Project, project_id)

    assert result["deleted"] is True
    assert [item["chapter_number"] for item in project.outline_data["chapter_spine"]] == [1]
    assert project.outline_data["meta"]["version"] == 2
