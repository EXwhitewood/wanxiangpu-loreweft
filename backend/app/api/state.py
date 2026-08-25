import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.db_models import get_db, Project
from app.models.story_state import StoryState, EntityState, LocationState, SubjectiveView
from app.services.state_manager import StateManager

router = APIRouter()


@router.get(
    "",
    summary="获取故事状态",
    description="获取指定项目的完整 StoryState，包括时间线ID、叙事时间、当前章节/场景、POV角色、客观状态层和主观认知层。",
)
async def get_state(project_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    state_manager = StateManager()
    state = await state_manager.get_state(str(project_id))
    return state.model_dump()


@router.put(
    "",
    summary="更新故事状态",
    description="完整替换指定项目的 StoryState。用于批量状态更新或回滚操作。",
)
async def update_state(
    project_id: uuid.UUID, state_data: dict, db: AsyncSession = Depends(get_db)
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    state_manager = StateManager()
    state = StoryState(**state_data)
    await state_manager.set_state(str(project_id), state)
    return state.model_dump()


@router.get(
    "/entity/{entity_id}",
    summary="获取实体状态",
    description="获取指定实体（人物或地点）的当前状态，包括位置、情绪、物理状态、持有物品等。实体ID以 'char_' 开头表示人物，'loc_' 开头表示地点。",
)
async def get_entity_state(
    project_id: uuid.UUID, entity_id: str, db: AsyncSession = Depends(get_db)
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    state_manager = StateManager()
    state = await state_manager.get_state(str(project_id))
    entity = state.objective_state.get(entity_id)
    if not entity:
        raise HTTPException(status_code=404, detail="实体状态不存在")
    return entity.model_dump()


@router.put(
    "/entity/{entity_id}",
    summary="更新实体状态",
    description="更新指定实体的状态。可更新字段：location（位置）、emotional_state（情绪）、physical_state（物理状态）、inventory（持有物品）、alive（存活状态）等。",
)
async def update_entity_state(
    project_id: uuid.UUID, entity_id: str, entity_data: dict, db: AsyncSession = Depends(get_db)
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    state_manager = StateManager()
    state = await state_manager.get_state(str(project_id))

    patch = {"objective_state": {entity_id: entity_data}}
    state = await state_manager.apply_patch(str(project_id), patch)
    return state.model_dump()


@router.post(
    "/timelines",
    summary="创建时间线",
    description="创建新的故事时间线分支。可指定父时间线和分支章节号，系统会复制父时间线在该章节的状态作为新时间线的起点。",
)
async def create_timeline(project_id: uuid.UUID, data: dict, db: AsyncSession = Depends(get_db)):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    manager = StateManager()
    result = await manager.create_timeline(
        str(project_id),
        data.get("name", "新时间线"),
        data.get("parent_timeline_id"),
        data.get("branch_chapter"),
    )
    return result


@router.get(
    "/timelines",
    summary="获取时间线列表",
    description="获取项目的所有时间线分支列表。每个时间线包含ID、名称、父时间线和分支章节信息。",
)
async def list_timelines(project_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    manager = StateManager()
    return await manager.list_timelines(str(project_id))


@router.post(
    "/timelines/{timeline_id}/switch",
    summary="切换时间线",
    description="切换到指定的时间线分支。切换后，项目的当前故事状态将变为该时间线的状态。",
)
async def switch_timeline(project_id: uuid.UUID, timeline_id: str, db: AsyncSession = Depends(get_db)):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    manager = StateManager()
    result = await manager.switch_timeline(str(project_id), timeline_id)
    if not result:
        raise HTTPException(status_code=404, detail="时间线不存在")
    return result.model_dump()
