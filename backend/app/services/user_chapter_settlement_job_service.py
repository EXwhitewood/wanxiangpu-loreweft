"""Durable asynchronous orchestration for the auxiliary user-save route."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.db_models import (
    Chapter,
    ChapterBaseline,
    ChapterEffectOutbox,
    ChapterSettlementJob,
    Project,
    async_session,
)
from app.models.writing_assistance import ChapterAdvisoryCheckRequest
from app.services.chapter_advisory_service import ChapterAdvisoryService
from app.services.chapter_commit_service import (
    apply_pending_chapter_effects,
    enqueue_worldview_projection,
)
from app.services.chapter_diagnostic_service import get_chapter_diagnostic_service
from app.services.chapter_summary_service import ChapterSummaryService
from app.services.narrative_sync_service import NarrativeSyncService
from app.services.user_chapter_settlement_service import (
    ensure_user_save_baseline,
    get_user_chapter_settlement_service,
    load_user_save_reference,
)


logger = logging.getLogger(__name__)
_MAX_ATTEMPTS = 3
_running_tasks: set[asyncio.Task] = set()
_chapter_locks: dict[str, asyncio.Lock] = {}


def chapter_revision_hash(content: str, title: str = "") -> str:
    material = f"{title or ''}\0{content or ''}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def serialize_settlement_job(job: ChapterSettlementJob) -> dict[str, Any]:
    return {
        "job_id": str(job.id),
        "chapter_number": int(job.chapter_number),
        "chapter_revision_hash": job.chapter_revision_hash,
        "status": job.status,
        "phase": job.phase,
        "stages": job.stage_results or {},
        "attempts": int(job.attempts or 0),
        "error": job.error_message or "",
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "completed_at": job.completed_at,
    }


async def enqueue_user_chapter_settlement(
    db: AsyncSession,
    *,
    project: Project,
    chapter: Chapter,
    reference_snapshot: dict[str, Any],
) -> ChapterSettlementJob:
    revision_hash = chapter_revision_hash(chapter.content or "", chapter.title or "")
    await ensure_user_save_baseline(
        db,
        project=project,
        chapter_number=int(chapter.chapter_number),
        reference_snapshot=reference_snapshot,
    )
    existing = (
        await db.execute(
            select(ChapterSettlementJob).where(
                ChapterSettlementJob.project_id == project.id,
                ChapterSettlementJob.chapter_number == chapter.chapter_number,
                ChapterSettlementJob.chapter_revision_hash == revision_hash,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.status in {"retryable_failed", "degraded"} and int(existing.attempts or 0) < _MAX_ATTEMPTS:
            existing.status = "pending"
            existing.phase = "queued"
            existing.error_message = ""
            existing.next_retry_at = None
        return existing

    await db.execute(
        update(ChapterSettlementJob)
        .where(
            ChapterSettlementJob.project_id == project.id,
            ChapterSettlementJob.chapter_number == chapter.chapter_number,
            ChapterSettlementJob.status.in_(("pending", "running", "retryable_failed")),
        )
        .values(status="superseded", phase="superseded", error_message="正文已产生更新版本")
    )
    job = ChapterSettlementJob(
        project_id=project.id,
        chapter_number=chapter.chapter_number,
        chapter_revision_hash=revision_hash,
        status="pending",
        phase="queued",
        stage_results={"text": {"status": "completed"}},
    )
    db.add(job)
    await db.flush()
    return job


async def latest_user_chapter_settlement(
    db: AsyncSession,
    *,
    project_id: str | uuid.UUID,
    chapter_number: int,
) -> ChapterSettlementJob | None:
    return (
        await db.execute(
            select(ChapterSettlementJob)
            .where(
                ChapterSettlementJob.project_id == project_id,
                ChapterSettlementJob.chapter_number == chapter_number,
            )
            .order_by(ChapterSettlementJob.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


def _set_stage(job: ChapterSettlementJob, name: str, status: str, **details: Any) -> None:
    stages = dict(job.stage_results or {})
    stages[name] = {"status": status, **details}
    job.stage_results = stages
    job.phase = name
    job.updated_at = datetime.now(timezone.utc)


async def _assert_current_revision(
    db: AsyncSession,
    job: ChapterSettlementJob,
) -> tuple[Project, Chapter, dict[str, Any]]:
    project = await db.get(Project, job.project_id)
    chapter = (
        await db.execute(
            select(Chapter).where(
                Chapter.project_id == job.project_id,
                Chapter.chapter_number == job.chapter_number,
            )
        )
    ).scalar_one_or_none()
    if project is None or chapter is None:
        job.status = "superseded"
        job.phase = "superseded"
        job.error_message = "项目或章节已删除"
        await db.commit()
        raise RuntimeError("settlement target no longer exists")
    if chapter_revision_hash(chapter.content or "", chapter.title or "") != job.chapter_revision_hash:
        job.status = "superseded"
        job.phase = "superseded"
        job.error_message = "正文已产生更新版本"
        await db.commit()
        raise RuntimeError("settlement revision has been superseded")
    baseline = (
        await db.execute(
            select(ChapterBaseline).where(
                ChapterBaseline.project_id == job.project_id,
                ChapterBaseline.chapter_number == job.chapter_number,
            )
        )
    ).scalar_one_or_none()
    if baseline is None:
        raise RuntimeError("user-save baseline is missing")
    return project, chapter, load_user_save_reference(baseline)


async def process_user_chapter_settlement(job_id: str | uuid.UUID) -> None:
    async with async_session() as probe:
        job = await probe.get(ChapterSettlementJob, job_id)
        if job is None:
            return
        lock_key = f"{job.project_id}:{job.chapter_number}"
    lock = _chapter_locks.setdefault(lock_key, asyncio.Lock())
    async with lock:
        async with async_session() as db:
            job = await db.get(ChapterSettlementJob, job_id)
            if job is None or job.status in {"completed", "superseded"}:
                return
            job.status = "running"
            job.attempts = int(job.attempts or 0) + 1
            job.error_message = ""
            job.next_retry_at = None
            await db.commit()
            advisory_response = None
            try:
                project, chapter, reference = await _assert_current_revision(db, job)
                summary = await ChapterSummaryService().generate_summary(
                    str(project.id),
                    int(chapter.chapter_number),
                    chapter.content or "",
                    chapter_title=chapter.title or "",
                    db=db,
                )
                _set_stage(job, "summary", "completed")
                await db.commit()

                from app.api.writing_assistance import load_writing_assistance_settings

                settings = load_writing_assistance_settings(project)
                if settings.consistency_reminders.enabled and settings.consistency_reminders.check_on_save:
                    advisory_response = await ChapterAdvisoryService().check(
                        project=project,
                        project_id=str(project.id),
                        chapter_number=int(chapter.chapter_number),
                        request=ChapterAdvisoryCheckRequest(
                            content=chapter.content or "",
                            trigger="save",
                            dimensions=settings.consistency_reminders.dimensions,
                            reference_snapshot=reference,
                        ),
                        settings=settings,
                        db=db,
                    )
                _set_stage(job, "diagnostic_check", "completed")
                await db.commit()

                project, chapter, reference = await _assert_current_revision(db, job)
                await NarrativeSyncService().sync_chapter_write(
                    project.id,
                    int(chapter.chapter_number),
                    db,
                    title=chapter.title or "",
                    content=chapter.content or "",
                    summary=summary if isinstance(summary, dict) else {},
                    source_system="user_chapter_settlement",
                )
                _set_stage(job, "narrative_sync", "completed")
                await db.commit()

                project, chapter, reference = await _assert_current_revision(db, job)
                await enqueue_worldview_projection(
                    db,
                    project_id=project.id,
                    chapter_number=int(chapter.chapter_number),
                    reuse_existing_content=True,
                )
                await db.commit()
                await apply_pending_chapter_effects(
                    db,
                    project_id=project.id,
                    chapter_number=int(chapter.chapter_number),
                    project=project,
                )
                worldview = (
                    await db.execute(
                        select(ChapterEffectOutbox)
                        .where(
                            ChapterEffectOutbox.project_id == project.id,
                            ChapterEffectOutbox.chapter_number == chapter.chapter_number,
                            ChapterEffectOutbox.effect_type == "worldview_projection",
                        )
                        .order_by(ChapterEffectOutbox.created_at.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if worldview is None or not worldview.applied:
                    raise RuntimeError(
                        (worldview.error_message if worldview else "worldview projection was not queued")
                        or "worldview projection did not complete"
                    )
                _set_stage(job, "worldview", "completed")
                await db.commit()

                project, chapter, reference = await _assert_current_revision(db, job)
                settlement = await get_user_chapter_settlement_service().settle(
                    db,
                    project=project,
                    chapter=chapter,
                    reference_snapshot=reference,
                    summary=summary if isinstance(summary, dict) else {},
                )
                _set_stage(job, "state", "completed", active_chapter=settlement.get("active_chapter"))
                await db.commit()

                diagnostic_count = 0
                if advisory_response is not None:
                    records = await get_chapter_diagnostic_service().persist_response(
                        db,
                        project_id=project.id,
                        chapter_number=int(chapter.chapter_number),
                        response=advisory_response,
                        reference_snapshot=reference,
                    )
                    diagnostic_count = len(records)
                _set_stage(job, "diagnostics", "completed", count=diagnostic_count)
                job.status = "completed"
                job.phase = "completed"
                job.completed_at = datetime.now(timezone.utc)
                await db.commit()
            except Exception as exc:
                await db.rollback()
                job = await db.get(ChapterSettlementJob, job_id)
                if job is None or job.status == "superseded":
                    return
                job.error_message = f"{type(exc).__name__}: {exc}"[:500]
                if int(job.attempts or 0) < _MAX_ATTEMPTS:
                    job.status = "retryable_failed"
                    job.next_retry_at = datetime.now(timezone.utc) + timedelta(seconds=30)
                else:
                    job.status = "degraded"
                    job.next_retry_at = None
                _set_stage(job, job.phase or "unknown", "failed", error=job.error_message)
                await db.commit()
                logger.warning("User chapter settlement %s failed: %s", job_id, exc, exc_info=True)


def schedule_user_chapter_settlement(job_id: str | uuid.UUID) -> None:
    task = asyncio.create_task(process_user_chapter_settlement(job_id))
    _running_tasks.add(task)
    task.add_done_callback(_running_tasks.discard)


async def recover_user_chapter_settlements(db: AsyncSession) -> dict[str, int]:
    jobs = (
        await db.execute(
            select(ChapterSettlementJob).where(
                ChapterSettlementJob.status.in_(("pending", "running", "retryable_failed")),
                ChapterSettlementJob.attempts < _MAX_ATTEMPTS,
            )
        )
    ).scalars().all()
    scheduled = 0
    now = datetime.now(timezone.utc)
    for job in jobs:
        retry_at = job.next_retry_at
        if retry_at is not None and retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        if retry_at is not None and retry_at > now:
            continue
        if job.status == "running":
            job.status = "pending"
            job.phase = "queued"
        schedule_user_chapter_settlement(job.id)
        scheduled += 1
    await db.commit()
    return {"scheduled": scheduled, "recoverable": len(jobs)}


async def shutdown_user_chapter_settlement_tasks() -> None:
    tasks = list(_running_tasks)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
