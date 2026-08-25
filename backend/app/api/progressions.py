import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.db_models import Project, get_db
from app.services.progression_service import ProgressionService
from app.services.generation_feature_policy import resolve_generation_feature_policy

router = APIRouter()


class DetectMentionsRequest(BaseModel):
    chapter_number: int = 0
    scene_index: int = 0
    text: str = Field(min_length=1, max_length=100_000)
    write_candidates: bool = False


@router.get("/progressions")
async def list_progressions(
    project_id: uuid.UUID,
    limit: int = Query(default=100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    return await ProgressionService().list_progressions(db, str(project_id), limit=limit)


@router.post("/progressions/detect-mentions")
async def detect_mentions(
    project_id: uuid.UUID,
    data: DetectMentionsRequest,
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    policy = resolve_generation_feature_policy(project)
    write_allowed = (
        policy.mention_detector_mode == "candidate"
        or policy.progression_mode in ("candidate", "active")
    )
    if data.write_candidates and not write_allowed:
        raise HTTPException(status_code=409, detail="当前项目未启用候选状态写入")
    return await ProgressionService().detect_candidates(
        db,
        project=project,
        project_id=str(project_id),
        chapter_number=data.chapter_number,
        scene_index=data.scene_index,
        text=data.text,
        write=data.write_candidates,
        status="active" if policy.progression_mode == "active" else "candidate",
    )
