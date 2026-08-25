from __future__ import annotations

import uuid

from sqlalchemy import select

from app.db.db_models import Chapter, ChapterContentRevision, Project
from app.services.chapter_write_guard import (
    CREATE_ONLY,
    REPLACE_WITH_BACKUP,
    backup_chapter_before_ai_replace,
    chapter_content_hash,
    validate_ai_chapter_write,
)


def test_create_only_rejects_existing_user_prose():
    chapter = Chapter(content="用户已经写好的正文")

    allowed, reason = validate_ai_chapter_write(
        chapter,
        write_policy=CREATE_ONLY,
        expected_content_hash="",
    )

    assert allowed is False
    assert "不得覆盖" in reason


def test_explicit_replace_requires_unchanged_source_revision():
    chapter = Chapter(content="当前正文")
    expected = chapter_content_hash("旧正文")

    allowed, reason = validate_ai_chapter_write(
        chapter,
        write_policy=REPLACE_WITH_BACKUP,
        expected_content_hash=expected,
    )

    assert allowed is False
    assert "发生变化" in reason


async def test_explicit_replace_backup_is_immutable_and_idempotent(db):
    project_id = uuid.uuid4()
    project = Project(id=project_id, name="manual")
    chapter = Chapter(
        project_id=project_id,
        chapter_number=3,
        title="第三章",
        content="用户原文",
        status="draft",
    )
    db.add_all([project, chapter])
    await db.commit()

    first = await backup_chapter_before_ai_replace(
        db,
        chapter=chapter,
        execution_id="explicit-rewrite-1",
    )
    second = await backup_chapter_before_ai_replace(
        db,
        chapter=chapter,
        execution_id="explicit-rewrite-1",
    )
    await db.commit()

    assert str(first.id) == str(second.id)
    rows = (await db.execute(select(ChapterContentRevision))).scalars().all()
    assert len(rows) == 1
    assert rows[0].content == "用户原文"
    assert rows[0].content_hash == chapter_content_hash("用户原文")
