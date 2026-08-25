from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from app.db.db_models import Chapter, Project
from app.utils.word_count import count_words


def calculate_project_chapter_aggregates(
    rows: Iterable[tuple[int, str | None]],
) -> tuple[int, int]:
    """Return ``(total_words, next_chapter_to_write)`` from chapter rows.

    ``projects.current_chapter`` has one durable meaning throughout the
    application: the number immediately after the highest chapter containing
    substantive prose. Empty planning placeholders do not advance progress.
    """
    total_words = 0
    latest_substantive = 0
    for chapter_number, content in rows:
        chapter_words = count_words(content or "")
        total_words += chapter_words
        if chapter_words > 0:
            latest_substantive = max(latest_substantive, int(chapter_number))
    return total_words, latest_substantive + 1


async def refresh_project_chapter_aggregates(
    db: AsyncSession,
    *,
    project_id: str | uuid.UUID,
    project: Project | None = None,
) -> dict[str, int | bool]:
    """Recompute durable project counters from canonical chapter rows.

    Call this in the same transaction that creates, changes, or deletes prose.
    Read endpoints must not invent or silently persist a different meaning.
    """
    if project is None:
        project = (
            await db.execute(
                select(Project)
                .options(load_only(Project.id, Project.total_words, Project.current_chapter))
                .where(Project.id == project_id)
            )
        ).scalar_one_or_none()
    if project is None:
        return {"changed": False, "total_words": 0, "current_chapter": 1}

    rows = (
        await db.execute(
            select(Chapter.chapter_number, Chapter.content).where(Chapter.project_id == project_id)
        )
    ).all()
    total_words, next_chapter = calculate_project_chapter_aggregates(rows)

    changed = (
        int(project.total_words or 0) != total_words
        or int(project.current_chapter or 1) != next_chapter
    )
    project.total_words = total_words
    project.current_chapter = next_chapter
    await db.flush()
    return {
        "changed": changed,
        "total_words": total_words,
        "current_chapter": next_chapter,
    }


async def refresh_project_totals(
    db: AsyncSession,
    projects: Iterable[Project],
) -> bool:
    """Bulk repair durable counters at startup or from an explicit audit."""
    project_list = list(projects)
    if not project_list:
        return False
    project_ids = [project.id for project in project_list]
    rows = (
        await db.execute(
            select(Chapter.project_id, Chapter.chapter_number, Chapter.content).where(
                Chapter.project_id.in_(project_ids)
            )
        )
    ).all()
    totals = {project_id: 0 for project_id in project_ids}
    latest = {project_id: 0 for project_id in project_ids}
    for project_id, chapter_number, content in rows:
        chapter_words = count_words(content or "")
        totals[project_id] = totals.get(project_id, 0) + chapter_words
        if chapter_words > 0:
            latest[project_id] = max(latest.get(project_id, 0), int(chapter_number))

    changed = False
    for project in project_list:
        total_words = totals.get(project.id, 0)
        if int(project.total_words or 0) != total_words:
            project.total_words = total_words
            changed = True
        next_chapter = latest.get(project.id, 0) + 1
        if int(project.current_chapter or 1) != next_chapter:
            project.current_chapter = next_chapter
            changed = True
    if changed:
        await db.flush()
    return changed
