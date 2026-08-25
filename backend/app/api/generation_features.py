import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.db.db_models import Project, get_db
from app.models.generation_features import GenerationFeaturePolicy
from app.services.generation_feature_policy import resolve_generation_feature_policy
from app.services.writing_mode_profile_service import WritingModeProfileService

router = APIRouter()
logger = logging.getLogger(__name__)


class GenerationFeaturesUpdate(BaseModel):
    generation_features: dict


@router.get("/generation-features")
async def get_generation_features(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    policy = resolve_generation_feature_policy(project)
    raw = (project.core_data or {}).get("generation_features", {}) if isinstance(project.core_data, dict) else {}
    return {"effective": policy.model_dump(), "project": raw}


@router.get("/writing-modes")
async def list_writing_modes(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    profiles = WritingModeProfileService().list_profiles()
    effective = resolve_generation_feature_policy(project)
    return {
        "profiles": [profile.model_dump() for profile in profiles],
        "active_profile_id": effective.writing_mode_profile_id,
    }


@router.put("/generation-features")
async def update_generation_features(
    project_id: uuid.UUID,
    data: GenerationFeaturesUpdate,
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    try:
        policy = GenerationFeaturePolicy(**(data.generation_features or {}))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"增强设置无效：{exc}") from exc

    core_data = dict(project.core_data or {})
    core_data["generation_features"] = policy.model_dump(exclude={"enabled_by", "notes"})
    project.core_data = core_data
    try:
        flag_modified(project, "core_data")
    except Exception:
        pass
    await db.commit()
    await db.refresh(project)
    return {"effective": resolve_generation_feature_policy(project).model_dump(), "project": core_data["generation_features"]}


@router.patch("/generation-features")
async def patch_generation_features(
    project_id: uuid.UUID,
    data: GenerationFeaturesUpdate,
    db: AsyncSession = Depends(get_db),
):
    return await update_generation_features(project_id, data, db)
