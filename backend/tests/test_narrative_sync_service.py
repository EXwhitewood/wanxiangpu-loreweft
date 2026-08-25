import pytest

from app.db.db_models import Project
from app.services.foreshadowing_service import ForeshadowingService
from app.services.narrative_sync_service import NarrativeSyncService


@pytest.mark.asyncio
async def test_sync_chapter_write_collects_foreshadowing(project_in_db, db):
    project = await db.get(Project, project_in_db)
    project.outline_data = {
        "chapter_spine": [
            {
                "chapter_number": 1,
                "title": "开端",
                "foreshadowing_actions": [
                    {"name": "身份秘密", "action": "plant", "mode": "subtle"},
                ],
            }
        ]
    }
    project.core_data = {}
    await db.commit()

    service = NarrativeSyncService()
    result = await service.sync_chapter_write(
        project_in_db,
        1,
        db,
        title="开端",
        content="第一章正文",
        summary={"summary_text": "第一章摘要", "foreshadowing_actions": ["身份秘密"]},
        source_system="test",
    )

    assert result["updated"] is True
    assert result["foreshadowing_count"] >= 1

    await db.refresh(project)
    spine = project.outline_data["chapter_spine"][0]
    assert spine["status"] == "written"
    assert spine["summary_text"] == "第一章摘要"
    assert spine["word_count"] == len("第一章正文")

    line = await ForeshadowingService().get_foreshadowing_by_name(project_in_db, "身份秘密", db)
    assert line is not None
    assert line["name"] == "身份秘密"
    assert line["status"] in {"active", "resolved"}
    assert project.core_data.get("change_notifications")

    first_outline_version = project.outline_version
    await service.sync_chapter_write(
        project_in_db,
        1,
        db,
        title="开端",
        content="第一章正文",
        summary={"summary_text": "第一章摘要", "foreshadowing_actions": ["身份秘密"]},
        source_system="test",
    )
    await db.refresh(project)
    assert project.outline_version == first_outline_version


@pytest.mark.asyncio
async def test_sync_chapter_write_strips_recursive_foreshadowing_payload(project_in_db, db):
    nested_action = {
        "name": "recursive thread",
        "action": "plant",
        "description": "seed clue",
    }
    nested_action["legacy_payload"] = {
        "actions": [
            {
                "name": "recursive thread",
                "action": "plant",
                "legacy_payload": {
                    "actions": [
                        {
                            "name": "recursive thread",
                            "action": "plant",
                            "legacy_payload": {"actions": []},
                        }
                    ]
                },
            }
        ]
    }

    project = await db.get(Project, project_in_db)
    project.outline_data = {
        "chapter_spine": [
            {
                "chapter_number": 1,
                "title": "Start",
                "foreshadowing_actions": [nested_action],
            }
        ],
        "chapters": [
            {
                "chapter_number": 1,
                "title": "Start",
                "foreshadowing_actions": [nested_action],
            }
        ],
    }
    project.core_data = {}
    await db.commit()

    await NarrativeSyncService().sync_chapter_write(
        project_in_db,
        1,
        db,
        title="Start",
        content="chapter body",
        summary={},
        source_system="test",
    )

    await db.refresh(project)
    for key in ("chapter_spine", "chapters"):
        item = project.outline_data[key][0]
        assert "foreshadowing_actions" not in item
        assert "legacy_payload" not in item


@pytest.mark.asyncio
async def test_sync_chapter_delete_removes_related_foreshadowing(project_in_db, db):
    project = await db.get(Project, project_in_db)
    project.outline_data = {
        "chapter_spine": [
            {
                "chapter_number": 1,
                "title": "开端",
                "foreshadowing_actions": [
                    {"name": "身份秘密", "action": "plant", "mode": "subtle"},
                ],
            }
        ]
    }
    project.core_data = {
        "chapter_summaries": {
            "1": {
                "summary_text": "第一章摘要",
                "foreshadowing_actions": ["身份秘密"],
            }
        }
    }
    await db.commit()

    service = NarrativeSyncService()
    await service.sync_chapter_write(
        project_in_db,
        1,
        db,
        title="开端",
        content="第一章正文",
        summary={"summary_text": "第一章摘要", "foreshadowing_actions": ["身份秘密"]},
        source_system="test",
    )
    delete_result = await service.sync_chapter_delete(
        project_in_db,
        1,
        db,
        source_system="test",
    )

    assert delete_result["outline_preserved"] is True
    assert delete_result["orphaned_foreshadowing_count"] >= 1

    await db.refresh(project)
    spine = project.outline_data.get("chapter_spine", [])
    assert len(spine) == 1
    assert spine[0]["chapter_number"] == 1
    assert "1" not in (project.core_data.get("chapter_summaries") or {})

    line = await ForeshadowingService().get_foreshadowing_by_name(project_in_db, "身份秘密", db)
    assert line is not None
    assert line["status"] == "revised"
    assert line["clues_placed"] == 0
