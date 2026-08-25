import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.db_models import GenerationTrace, Project, get_db

router = APIRouter()


def _trace_to_dict(trace: GenerationTrace) -> dict:
    return {
        "id": str(trace.id),
        "project_id": str(trace.project_id),
        "chapter_number": trace.chapter_number,
        "scene_index": trace.scene_index,
        "trace_version": trace.trace_version,
        "mode": trace.mode,
        "status": trace.status,
        "context_manifest": trace.context_manifest or {},
        "prompt_preview": trace.prompt_preview or {},
        "quality_summary": trace.quality_summary or {},
        "recovery_summary": trace.recovery_summary or {},
        "created_at": trace.created_at.isoformat() if trace.created_at else None,
        "expires_at": trace.expires_at.isoformat() if trace.expires_at else None,
    }


@router.get("/generation-traces")
async def list_generation_traces(
    project_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    await db.execute(
        delete(GenerationTrace).where(
            GenerationTrace.project_id == project_id,
            GenerationTrace.expires_at.is_not(None),
            GenerationTrace.expires_at <= datetime.now(timezone.utc),
        )
    )
    await db.commit()
    active_filter = or_(GenerationTrace.expires_at.is_(None), GenerationTrace.expires_at > datetime.now(timezone.utc))
    total_result = await db.execute(
        select(func.count()).select_from(GenerationTrace).where(
            GenerationTrace.project_id == project_id,
            active_filter,
        )
    )
    result = await db.execute(
        select(GenerationTrace)
        .where(GenerationTrace.project_id == project_id, active_filter)
        .order_by(GenerationTrace.created_at.desc())
        .limit(limit)
    )
    items = [_trace_to_dict(item) for item in result.scalars().all()]
    return {"items": items, "total": total_result.scalar() or 0}


@router.get("/generation-traces/{trace_id}")
async def get_generation_trace(
    project_id: uuid.UUID,
    trace_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    trace = await db.get(GenerationTrace, trace_id)
    if not trace or str(trace.project_id) != str(project_id):
        raise HTTPException(status_code=404, detail="生成轨迹不存在")
    return _trace_to_dict(trace)


@router.delete("/generation-traces/{trace_id}")
async def delete_generation_trace(
    project_id: uuid.UUID,
    trace_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    trace = await db.get(GenerationTrace, trace_id)
    if not trace or str(trace.project_id) != str(project_id):
        raise HTTPException(status_code=404, detail="生成轨迹不存在")
    await db.delete(trace)
    await db.commit()
    return {"deleted": True}
