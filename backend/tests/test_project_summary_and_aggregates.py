import uuid

import pytest
from sqlalchemy import text

from app.api.projects import list_projects
from app.db.db_models import Chapter, Project
from app.services.project_chapter_aggregate_service import (
    calculate_project_chapter_aggregates,
    refresh_project_chapter_aggregates,
)


def test_project_progress_has_one_next_chapter_semantic():
    total, current = calculate_project_chapter_aggregates([
        (1, "<p>第一章正文</p>"),
        (2, "<p></p>"),
        (4, "four words in English"),
        (7, ""),
    ])

    assert total == 9
    assert current == 5


@pytest.mark.asyncio
async def test_refresh_project_progress_repairs_words_and_next_chapter(db):
    project_id = uuid.uuid4()
    project = Project(
        id=project_id,
        name="aggregate",
        total_words=999,
        current_chapter=99,
    )
    db.add_all([
        project,
        Chapter(project_id=project_id, chapter_number=1, content="甲乙丙", status="committed"),
        Chapter(project_id=project_id, chapter_number=2, content="", status="draft"),
        Chapter(project_id=project_id, chapter_number=3, content="delta", status="draft"),
    ])
    await db.flush()

    result = await refresh_project_chapter_aggregates(
        db,
        project_id=project_id,
        project=project,
    )

    assert result == {"changed": True, "total_words": 4, "current_chapter": 4}
    assert project.total_words == 4
    assert project.current_chapter == 4


@pytest.mark.asyncio
async def test_project_list_uses_json_free_covering_index_and_dto(db):
    project = Project(
        id=uuid.uuid4(),
        name="large-outline",
        description="summary",
        genre="mystery",
        total_words=321,
        current_chapter=8,
        core_data={"large": "x" * 100_000},
        outline_data={"large": "y" * 100_000},
        outline_index={"large": "z" * 100_000},
    )
    db.add(project)
    await db.commit()

    result = await list_projects(db=db, user=None)

    assert len(result) == 1
    assert result[0]["id"] == project.id
    assert set(result[0]) == {
        "id",
        "name",
        "description",
        "genre",
        "word_count_target",
        "current_chapter",
        "total_words",
        "created_at",
        "updated_at",
    }

    plan = (
        await db.execute(text(
            "EXPLAIN QUERY PLAN SELECT id, name, description, genre, "
            "word_count_target, current_chapter, total_words, created_at, updated_at "
            "FROM projects ORDER BY created_at DESC, id DESC"
        ))
    ).all()
    details = " ".join(str(row[-1]) for row in plan)
    assert "COVERING INDEX ix_projects_summary_created" in details

