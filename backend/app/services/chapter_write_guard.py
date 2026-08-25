"""Non-destructive boundary between AI generation and user-authored prose."""
from __future__ import annotations

import hashlib

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.db_models import Chapter, ChapterContentRevision
from app.utils.word_count import count_words


CREATE_ONLY = "create_only"
REPLACE_WITH_BACKUP = "replace_with_backup"


def chapter_content_hash(content: str) -> str:
    return hashlib.sha256((content or "").encode("utf-8")).hexdigest()


def has_substantive_chapter_content(chapter: Chapter | None) -> bool:
    return bool(chapter is not None and count_words(chapter.content or "") > 0)


def validate_ai_chapter_write(
    chapter: Chapter | None,
    *,
    write_policy: str,
    expected_content_hash: str,
) -> tuple[bool, str]:
    if not has_substantive_chapter_content(chapter):
        return True, ""
    if write_policy != REPLACE_WITH_BACKUP:
        return False, "章节已有正文；普通 AI 生成不得覆盖现有内容"
    actual_hash = chapter_content_hash(chapter.content or "")
    if not expected_content_hash or actual_hash != expected_content_hash:
        return False, "章节正文已在生成期间发生变化；已取消替换以保护最新内容"
    return True, ""


async def backup_chapter_before_ai_replace(
    db: AsyncSession,
    *,
    chapter: Chapter,
    execution_id: str,
) -> ChapterContentRevision:
    existing = (
        await db.execute(
            select(ChapterContentRevision).where(
                ChapterContentRevision.project_id == chapter.project_id,
                ChapterContentRevision.chapter_number == chapter.chapter_number,
                ChapterContentRevision.execution_id == execution_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    revision = ChapterContentRevision(
        project_id=chapter.project_id,
        chapter_number=chapter.chapter_number,
        source_chapter_id=chapter.id,
        title=chapter.title or "",
        content=chapter.content or "",
        chapter_status=chapter.status or "draft",
        content_hash=chapter_content_hash(chapter.content or ""),
        reason="ai_explicit_replace",
        execution_id=execution_id,
    )
    db.add(revision)
    await db.flush()
    return revision
