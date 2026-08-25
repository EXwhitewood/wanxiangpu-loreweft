import copy
import uuid

import pytest

from app.agents.outline_architect import OutlineArchitectAgent
from app.db.db_models import Chapter, Project
from app.services.story_plan_service import StoryPlanService


def _spine(*numbers: int) -> list[dict]:
    return [
        {
            "chapter_id": f"ch_{number:03d}",
            "chapter_number": number,
            "title": f"chapter-{number}",
            "conflict_text": f"conflict-{number}",
        }
        for number in numbers
    ]


@pytest.mark.asyncio
async def test_manual_append_adds_exactly_one_contiguous_outline_chapter(db):
    project_id = uuid.uuid4()
    db.add(Project(
        id=project_id,
        name="manual-outline-append",
        outline_data={"chapter_spine": _spine(1), "meta": {"version": 1}},
        draft_outline={},
    ))
    await db.commit()

    result = await StoryPlanService().append_chapter_spine_item(
        project_id,
        {"title": "manual chapter", "pov_character": "Lin"},
        db,
    )
    await db.commit()

    project = await db.get(Project, project_id)
    assert result["chapter_number"] == 2
    assert result["chapter"]["manual_created"] is True
    assert result["chapter"]["pov_character"] == "Lin"
    assert [item["chapter_number"] for item in project.outline_data["chapter_spine"]] == [1, 2]
    assert [item["chapter_number"] for item in project.outline_data["chapters"]] == [1, 2]


@pytest.mark.asyncio
async def test_manual_append_refuses_sparse_outline_and_pending_draft(db):
    sparse_id = uuid.uuid4()
    sparse_outline = {"chapter_spine": _spine(1, 3)}
    db.add(Project(
        id=sparse_id,
        name="sparse-outline",
        outline_data=copy.deepcopy(sparse_outline),
        draft_outline={},
    ))

    draft_id = uuid.uuid4()
    db.add(Project(
        id=draft_id,
        name="pending-draft",
        outline_data={"chapter_spine": _spine(1)},
        draft_outline={"chapter_spine": _spine(1, 2)},
    ))
    await db.commit()

    sparse_result = await StoryPlanService().append_chapter_spine_item(sparse_id, {}, db)
    draft_result = await StoryPlanService().append_chapter_spine_item(draft_id, {}, db)

    assert sparse_result["code"] == "chapter_continuity_error"
    assert sparse_result["status_code"] == 409
    assert draft_result["code"] == "outline_draft_pending"
    assert draft_result["status_code"] == 409
    sparse_project = await db.get(Project, sparse_id)
    assert sparse_project.outline_data == sparse_outline


@pytest.mark.asyncio
async def test_outline_chapter_with_written_chapter_cannot_be_deleted(db):
    project_id = uuid.uuid4()
    db.add(Project(
        id=project_id,
        name="written-chapter-guard",
        outline_data={"chapter_spine": _spine(1)},
        draft_outline={},
    ))
    db.add(Chapter(
        project_id=project_id,
        chapter_number=1,
        title="written chapter",
        content="",
        status="draft",
    ))
    await db.commit()

    result = await StoryPlanService().delete_chapter_spine_item(project_id, 1, db)

    assert result["code"] == "chapter_outline_has_written_chapter"
    assert result["status_code"] == 409
    project = await db.get(Project, project_id)
    assert [item["chapter_number"] for item in project.outline_data["chapter_spine"]] == [1]


@pytest.mark.asyncio
async def test_insert_middle_chapter_renumbers_references_and_preserves_stable_ids(db):
    project_id = uuid.uuid4()
    outline = {
        "chapter_spine": [
            {**item, "depends_on": [f"ch_{item['chapter_number'] - 1:03d}"] if item["chapter_number"] > 1 else []}
            for item in _spine(1, 2, 3)
        ],
        "thread_plan": {
            "threads": [{
                "thread_id": "main",
                "plant_chapters": [1, 2],
                "payoff_chapters": [3],
            }],
        },
        "macro_plan": {"target_chapters": 3, "volumes": [{"chapter_range": [1, 3]}]},
        "scene_briefs": {
            f"ch_{number:03d}": {"chapter_id": f"ch_{number:03d}", "scenes": []}
            for number in range(1, 4)
        },
        "meta": {"version": 1, "generation_target_chapters": 3},
    }
    db.add(Project(
        id=project_id,
        name="insert-middle",
        outline_data=copy.deepcopy(outline),
        draft_outline={},
    ))
    await db.commit()

    result = await StoryPlanService().insert_chapter_spine_item(
        project_id,
        2,
        {"title": "inserted", "hook": "new hook"},
        db,
    )
    await db.commit()

    project = await db.get(Project, project_id)
    plan = project.outline_data
    spine = plan["chapter_spine"]
    assert result["created"] is True
    assert result["actual_chapter_count"] == 4
    assert [item["chapter_number"] for item in spine] == [1, 2, 3, 4]
    assert [item["chapter_id"] for item in spine if item["chapter_number"] in {1, 3, 4}] == [
        "ch_001", "ch_002", "ch_003",
    ]
    assert spine[1]["title"] == "inserted"
    assert plan["thread_plan"]["threads"][0]["plant_chapters"] == [1, 3]
    assert plan["thread_plan"]["threads"][0]["payoff_chapters"] == [4]
    assert plan["macro_plan"]["volumes"][0]["chapter_range"] == [1, 4]
    assert plan["macro_plan"]["target_chapters"] == 4
    assert plan["meta"]["generation_target_chapters"] == 4
    assert set(plan["scene_briefs"]) >= {"ch_001", "ch_002", "ch_003"}


@pytest.mark.asyncio
async def test_delete_middle_chapter_removes_brief_and_renumbers_references(db):
    project_id = uuid.uuid4()
    outline = {
        "chapter_spine": _spine(1, 2, 3),
        "thread_plan": {
            "threads": [{
                "thread_id": "main",
                "plant_chapters": [1, 2],
                "payoff_chapters": [3],
            }],
        },
        "macro_plan": {"target_chapters": 3, "volumes": [{"chapter_range": [1, 3]}]},
        "scene_briefs": {
            f"ch_{number:03d}": {"chapter_id": f"ch_{number:03d}", "scenes": []}
            for number in range(1, 4)
        },
        "meta": {"version": 1, "generation_target_chapters": 3},
    }
    db.add(Project(
        id=project_id,
        name="delete-middle",
        outline_data=copy.deepcopy(outline),
        draft_outline={},
    ))
    await db.commit()

    result = await StoryPlanService().delete_chapter_spine_item(project_id, 2, db)
    await db.commit()

    project = await db.get(Project, project_id)
    plan = project.outline_data
    assert result["deleted"] is True
    assert result["actual_chapter_count"] == 2
    assert [(item["chapter_number"], item["chapter_id"]) for item in plan["chapter_spine"]] == [
        (1, "ch_001"),
        (2, "ch_003"),
    ]
    assert "ch_002" not in plan["scene_briefs"]
    assert plan["thread_plan"]["threads"][0]["plant_chapters"] == [1]
    assert plan["thread_plan"]["threads"][0]["payoff_chapters"] == [2]
    assert plan["macro_plan"]["volumes"][0]["chapter_range"] == [1, 2]
    assert plan["meta"]["generation_target_chapters"] == 2


@pytest.mark.asyncio
async def test_middle_insert_refuses_to_renumber_existing_written_chapters(db):
    project_id = uuid.uuid4()
    official = {"chapter_spine": _spine(1, 2, 3), "meta": {"version": 1}}
    db.add(Project(
        id=project_id,
        name="written-resequence-guard",
        outline_data=copy.deepcopy(official),
        draft_outline={},
    ))
    db.add(Chapter(
        project_id=project_id,
        chapter_number=3,
        title="written third",
        content="body",
        status="draft",
    ))
    await db.commit()

    result = await StoryPlanService().insert_chapter_spine_item(
        project_id, 2, {"title": "unsafe insert"}, db
    )

    assert result["code"] == "chapter_resequence_has_written_chapters"
    assert result["first_affected_written_chapter"] == 3
    project = await db.get(Project, project_id)
    assert project.outline_data == official


@pytest.mark.asyncio
async def test_outline_architect_dialogue_tools_cover_single_chapter_crud(db):
    project_id = uuid.uuid4()
    db.add(Project(
        id=project_id,
        name="dialogue-crud",
        outline_data={"chapter_spine": _spine(1, 2), "meta": {"version": 1}},
        draft_outline={},
    ))
    await db.commit()
    agent = OutlineArchitectAgent()

    created = await agent._execute_tool(
        "create_chapter_outline",
        {
            "chapter_number": 2,
            "chapter_data": {
                "title": "inserted by dialogue",
                "conflict_text": "new conflict",
                "hook": "new hook",
            },
        },
        project_id,
        db,
    )
    assert created["status"] == "created"
    assert created["actual_chapter_count"] == 3

    read = await agent._execute_tool(
        "get_chapter_outline", {"chapter_number": 2}, project_id, db
    )
    assert read["chapter"]["title"] == "inserted by dialogue"

    updated = await agent._execute_tool(
        "apply_chapter_change",
        {"chapter_number": 2, "updates": {"title": "updated by dialogue"}},
        project_id,
        db,
    )
    assert updated["status"] == "applied"

    deleted = await agent._execute_tool(
        "delete_chapter_outline", {"chapter_number": 2}, project_id, db
    )
    assert deleted["status"] == "deleted"
    assert deleted["actual_chapter_count"] == 2

    project = await db.get(Project, project_id)
    assert [item["title"] for item in project.outline_data["chapter_spine"]] == [
        "chapter-1",
        "chapter-2",
    ]
