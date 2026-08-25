from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.auth import verify_project_ownership
from app.db.db_models import Chapter, Project, get_db
from app.models.writing_assistance import (
    ChapterAdvisoryCheckRequest,
    ChapterAdvisoryCheckResponse,
    ChapterDiagnosticDecisionRequest,
    WritingAssistanceSettings,
)
from app.services.chapter_advisory_service import ChapterAdvisoryService
from app.services.chapter_diagnostic_service import get_chapter_diagnostic_service
from app.services.narrative_sync_service import NarrativeSyncService

logger = logging.getLogger(__name__)
router = APIRouter()


def load_writing_assistance_settings(project: Project) -> WritingAssistanceSettings:
    core_data = project.core_data if isinstance(project.core_data, dict) else {}
    raw = core_data.get("writing_assistance")
    if not isinstance(raw, dict):
        return WritingAssistanceSettings()
    try:
        return WritingAssistanceSettings.model_validate(raw)
    except ValidationError as exc:
        logger.warning(
            "[writing-assistance] invalid stored settings for project=%s: %s",
            project.id,
            exc,
        )
        return WritingAssistanceSettings()


@router.get(
    "/settings",
    response_model=WritingAssistanceSettings,
    summary="获取写作辅助设置",
)
async def get_writing_assistance_settings(
    project: Project = Depends(verify_project_ownership),
) -> WritingAssistanceSettings:
    return load_writing_assistance_settings(project)


@router.put(
    "/settings",
    response_model=WritingAssistanceSettings,
    summary="更新写作辅助设置",
)
async def update_writing_assistance_settings(
    data: WritingAssistanceSettings,
    project: Project = Depends(verify_project_ownership),
    db: AsyncSession = Depends(get_db),
) -> WritingAssistanceSettings:
    core_data = dict(project.core_data or {})
    core_data["writing_assistance"] = data.model_dump(mode="json")
    project.core_data = core_data
    flag_modified(project, "core_data")
    await db.commit()
    await db.refresh(project)
    return load_writing_assistance_settings(project)


@router.post(
    "/chapters/{chapter_number}/check",
    response_model=ChapterAdvisoryCheckResponse,
    summary="检查章节一致性提醒",
    description=(
        "只读、非阻断地检查当前章节草稿。该端点不会修改正文、推进伏笔状态、"
        "调用 FBI 修订或影响章节提交。"
    ),
)
async def check_chapter_advisories(
    project_id: uuid.UUID,
    chapter_number: int,
    data: ChapterAdvisoryCheckRequest,
    project: Project = Depends(verify_project_ownership),
    db: AsyncSession = Depends(get_db),
) -> ChapterAdvisoryCheckResponse:
    service = ChapterAdvisoryService()
    response = await service.check(
        project=project,
        project_id=str(project_id),
        chapter_number=chapter_number,
        request=data,
        settings=load_writing_assistance_settings(project),
        db=db,
    )
    diagnostic_service = get_chapter_diagnostic_service()
    records = await diagnostic_service.persist_response(
        db,
        project_id=project_id,
        chapter_number=chapter_number,
        response=response,
        reference_snapshot=data.reference_snapshot,
    )
    await db.commit()
    response.persisted = True
    response.diagnostic_count = len(records)
    for finding, record in zip(response.findings, records):
        finding.diagnostic_id = str(record.id)
        finding.status = record.status
        finding.available_actions = record.available_actions or []
        finding.affected_domains = record.affected_domains or []
    return response


@router.get(
    "/chapters/{chapter_number}/diagnostics",
    summary="获取章节保存诊断表",
)
async def list_chapter_diagnostics(
    project_id: uuid.UUID,
    chapter_number: int,
    include_resolved: bool = False,
    project: Project = Depends(verify_project_ownership),
    db: AsyncSession = Depends(get_db),
):
    records = await get_chapter_diagnostic_service().list_records(
        db,
        project_id=project_id,
        chapter_number=chapter_number,
        include_resolved=include_resolved,
    )
    return {
        "chapter_number": chapter_number,
        "items": [get_chapter_diagnostic_service().serialize(item) for item in records],
    }


@router.post(
    "/chapters/{chapter_number}/diagnostics/{diagnostic_id}/decision",
    summary="处理章节诊断决定",
)
async def decide_chapter_diagnostic(
    project_id: uuid.UUID,
    chapter_number: int,
    diagnostic_id: uuid.UUID,
    data: ChapterDiagnosticDecisionRequest,
    project: Project = Depends(verify_project_ownership),
    db: AsyncSession = Depends(get_db),
):
    diagnostic_service = get_chapter_diagnostic_service()
    record = await diagnostic_service.get_record(
        db,
        project_id=project_id,
        chapter_number=chapter_number,
        diagnostic_id=diagnostic_id,
    )
    if record is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="诊断记录不存在")

    now = datetime.now(timezone.utc)
    record.decision_by = data.decided_by or "user"
    record.decision_reason = data.reason or ""
    record.decision_payload = {**dict(record.decision_payload or {}), "action": data.action}
    record.decided_at = now

    if data.action == "ignore":
        record.status = "ignored"
    elif data.action == "ignore_and_amend_outline":
        if record.category != "outline_deviation" or record.severity != "high":
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail="只有重要大纲偏离可以修改大纲")
        reference_snapshot = await diagnostic_service.resolve_reference_snapshot(db, record)
        try:
            amendment_id = await diagnostic_service.apply_outline_amendment(
                db,
                project=project,
                chapter_number=chapter_number,
                record=record,
                outline_changes=data.outline_changes,
                reason=data.reason,
                decided_by=data.decided_by or "user",
            )
        except ValueError as exc:
            from fastapi import HTTPException
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        await NarrativeSyncService().sync_outline_save(
            project_id,
            db,
            old_outline=reference_snapshot.get("outline_data") or {},
            new_outline=project.outline_data or {},
            source_system="user_chapter_save",
        )
        record.status = "outline_amendment_applied"
        record.decision_payload = {
            "action": data.action,
            "outline_amendment_id": amendment_id,
            "outline_changes": data.outline_changes,
            "applied_proposal": (record.decision_payload or {}).get("proposed_outline_amendment"),
        }
    else:
        # Do not mark a finding resolved merely because the user clicked the
        # button. Re-run the advisory against the same frozen baseline.
        record.status = "pending_recheck"
        await db.commit()
        chapter = await db.scalar(
            select(Chapter).where(
                Chapter.project_id == project_id,
                Chapter.chapter_number == chapter_number,
            )
        )
        if chapter is None:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="章节不存在")
        settings = load_writing_assistance_settings(project)
        settings.consistency_reminders.enabled = True
        settings.consistency_reminders.check_on_save = False
        if record.category not in settings.consistency_reminders.dimensions:
            settings.consistency_reminders.dimensions.append(record.category)
        reference_snapshot = await diagnostic_service.resolve_reference_snapshot(db, record)
        response = await ChapterAdvisoryService().check(
            project=project,
            project_id=str(project_id),
            chapter_number=chapter_number,
            request=ChapterAdvisoryCheckRequest(
                content=chapter.content or "",
                trigger="manual",
                dimensions=[record.category] if record.category in {
                    "fact", "character", "timeline", "world_rule",
                    "pov_knowledge", "foreshadowing", "outline_deviation",
                    "pacing", "clarity", "character_expression", "dialogue",
                    "hook", "prose_style",
                } else None,
                reference_snapshot=reference_snapshot or None,
            ),
            settings=settings,
            db=db,
        )
        await diagnostic_service.persist_response(
            db,
            project_id=project_id,
            chapter_number=chapter_number,
            response=response,
            reference_snapshot=reference_snapshot or None,
        )
        still_present = any(item.fingerprint == record.fingerprint for item in response.findings)
        record.status = "open" if still_present else "resolved"

    await db.commit()
    await db.refresh(record)
    return diagnostic_service.serialize(record)
