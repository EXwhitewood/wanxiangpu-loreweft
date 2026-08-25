import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.db_models import Project, get_db
from app.services.project_health_service import ProjectHealthService

router = APIRouter()


@router.get("/health")
async def get_project_health(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    return await ProjectHealthService().build(db, project)
