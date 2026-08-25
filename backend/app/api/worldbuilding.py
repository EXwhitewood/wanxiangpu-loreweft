import logging
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.db_models import get_db, Project, WorldviewObservation
from app.services.cross_system_event_bus import CrossSystemEventBus
from app.services.memory_core import CoreMemoryService
from app.services.memory_shell import ShellMemoryService
from sqlalchemy import and_, case, func, or_, select

logger = logging.getLogger(__name__)
router = APIRouter()

ObservationReviewState = Literal["all", "pending", "auto", "manual", "confirmed"]


def _observation_review_condition(review_state: ObservationReviewState | None):
    if not review_state or review_state == "all":
        return None
    if review_state == "pending":
        return and_(
            WorldviewObservation.status == "active",
            WorldviewObservation.confirmed_by.is_(None),
        )
    if review_state == "auto":
        return and_(
            WorldviewObservation.status == "promoted",
            WorldviewObservation.auto_promoted.is_(True),
        )
    if review_state == "manual":
        return and_(
            WorldviewObservation.status == "promoted",
            or_(
                WorldviewObservation.auto_promoted.is_(False),
                WorldviewObservation.auto_promoted.is_(None),
            ),
        )
    return WorldviewObservation.status == "promoted"


async def _get_project_observation(
    db: AsyncSession,
    project_id: uuid.UUID,
    observation_id: str,
) -> WorldviewObservation:
    try:
        oid = uuid.UUID(str(observation_id))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="观察记录 ID 无效") from exc
    result = await db.execute(
        select(WorldviewObservation).where(
            WorldviewObservation.id == oid,
            WorldviewObservation.project_id == project_id,
        )
    )
    observation = result.scalar_one_or_none()
    if not observation:
        raise HTTPException(status_code=404, detail="观察记录不存在")
    return observation


@router.get(
    "/{project_id}/rules",
    summary="获取世界观规则",
    description="获取指定项目的所有世界观规则，按分类组织。",
)
async def list_world_rules(project_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    service = CoreMemoryService()
    rules = await service.list_world_rules_with_db(db, str(project_id))
    return rules


@router.post(
    "/{project_id}/rules",
    summary="创建世界观规则",
    description="创建一条新的世界观规则。",
)
async def create_world_rule(project_id: uuid.UUID, data: dict, db: AsyncSession = Depends(get_db)):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    service = CoreMemoryService()
    rule = await service.create_world_rule_with_db(db, str(project_id), data)

    cd = project.core_data or {}
    if cd.get("weave_coordinator", {}).get("enabled", False):
        event_bus = CrossSystemEventBus()
        rule_id = rule.get("id", "") if isinstance(rule, dict) else getattr(rule, "id", "")
        await event_bus.publish_event(
            db=db,
            project_id=str(project_id),
            event_type="WORLD_RULE_CREATED.v1",
            source_system="worldbuilder",
            priority="critical" if data.get("priority") == "critical" else "normal",
            payload={
                "entity_type": "world_rule",
                "entity_id": rule_id,
                "entity_name": data.get("name", ""),
                "change_type": "created",
                "summary": f"新增铁则：{data.get('name', '')}",
                "meta": data,
            },
        )

    await db.commit()
    return rule


@router.put(
    "/{project_id}/rules/{rule_id}",
    summary="更新世界观规则",
    description="更新指定的世界观规则。",
)
async def update_world_rule(
    project_id: uuid.UUID,
    rule_id: str,
    data: dict,
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    service = CoreMemoryService()
    rule = await service.update_world_rule_with_db(db, str(project_id), rule_id, data)
    if not rule:
        raise HTTPException(status_code=404, detail="规则不存在")

    cd = project.core_data or {}
    if cd.get("weave_coordinator", {}).get("enabled", False):
        event_bus = CrossSystemEventBus()
        await event_bus.publish_event(
            db=db,
            project_id=str(project_id),
            event_type="WORLD_RULE_MODIFIED.v1",
            source_system="worldbuilder",
            priority="critical" if data.get("priority") == "critical" else "normal",
            payload={
                "entity_type": "world_rule",
                "entity_id": rule_id,
                "entity_name": data.get("name", ""),
                "change_type": "modified",
                "summary": f"修改铁则：{data.get('name', '') or rule_id}",
                "meta": data,
            },
        )

    await db.commit()
    return rule


@router.delete(
    "/{project_id}/rules/{rule_id}",
    summary="删除世界观规则",
    description="删除指定的世界观规则。",
)
async def delete_world_rule(
    project_id: uuid.UUID,
    rule_id: str,
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    service = CoreMemoryService()
    success = await service.delete_world_rule_with_db(db, str(project_id), rule_id)
    if not success:
        raise HTTPException(status_code=404, detail="规则不存在")

    cd = project.core_data or {}
    if cd.get("weave_coordinator", {}).get("enabled", False):
        event_bus = CrossSystemEventBus()
        await event_bus.publish_event(
            db=db,
            project_id=str(project_id),
            event_type="WORLD_RULE_MODIFIED.v1",
            source_system="worldbuilder",
            priority="normal",
            payload={
                "entity_type": "world_rule",
                "entity_id": rule_id,
                "entity_name": "",
                "change_type": "deleted",
                "summary": f"删除铁则：{rule_id}",
                "meta": {"rule_id": rule_id},
            },
        )

    await db.commit()
    return {"message": "已删除"}


@router.get(
    "/{project_id}/characters",
    summary="获取人物列表",
    description="获取指定项目的所有人物卡片。",
)
async def list_characters(project_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    service = CoreMemoryService()
    characters = await service.list_characters(str(project_id))
    return characters


@router.post(
    "/{project_id}/characters",
    summary="创建人物",
    description="创建一个新的人物卡片。",
)
async def create_character(project_id: uuid.UUID, data: dict, db: AsyncSession = Depends(get_db)):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    from app.models.character import CharacterCreate
    service = CoreMemoryService()
    char_data = CharacterCreate(**data)
    character = await service.create_character_with_db(db, str(project_id), char_data)

    cd = project.core_data or {}
    if cd.get("weave_coordinator", {}).get("enabled", False):
        event_bus = CrossSystemEventBus()
        char_id = getattr(character, "id", "") or character.model_dump().get("id", "")
        await event_bus.publish_event(
            db=db,
            project_id=str(project_id),
            event_type="CHARACTER_MODIFIED.v1",
            source_system="worldbuilder",
            priority="normal",
            payload={
                "entity_type": "character",
                "entity_id": str(char_id),
                "entity_name": data.get("name", ""),
                "change_type": "created",
                "summary": f"新增人物：{data.get('name', '')}",
                "meta": data,
            },
        )

    await db.commit()
    return character.model_dump()


@router.put(
    "/{project_id}/characters/{character_id}",
    summary="更新人物",
    description="更新指定的人物卡片。",
)
async def update_character(
    project_id: uuid.UUID,
    character_id: str,
    data: dict,
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    service = CoreMemoryService()
    character = await service.update_character_with_db(db, str(project_id), character_id, data)
    if not character:
        raise HTTPException(status_code=404, detail="人物不存在")

    cd = project.core_data or {}
    if cd.get("weave_coordinator", {}).get("enabled", False):
        event_bus = CrossSystemEventBus()
        await event_bus.publish_event(
            db=db,
            project_id=str(project_id),
            event_type="CHARACTER_MODIFIED.v1",
            source_system="worldbuilder",
            priority="normal",
            payload={
                "entity_type": "character",
                "entity_id": character_id,
                "entity_name": data.get("name", ""),
                "change_type": "modified",
                "summary": f"修改人物：{data.get('name', '') or character_id}",
                "meta": data,
            },
        )

    await db.commit()
    return character.model_dump()


@router.delete(
    "/{project_id}/characters/{character_id}",
    summary="删除人物",
    description="删除指定的人物卡片。",
)
async def delete_character(
    project_id: uuid.UUID,
    character_id: str,
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    service = CoreMemoryService()
    core_data = dict(project.core_data or {})
    char_name = ""
    for c in core_data.get("characters", []):
        if c.get("id") == character_id:
            char_name = c.get("name", "")
            break

    success = await service.delete_character_with_db(db, str(project_id), character_id)
    if not success:
        raise HTTPException(status_code=404, detail="人物不存在")

    if core_data.get("weave_coordinator", {}).get("enabled", False):
        event_bus = CrossSystemEventBus()
        await event_bus.publish_event(
            db=db,
            project_id=str(project_id),
            event_type="CHARACTER_MODIFIED.v1",
            source_system="worldbuilder",
            priority="normal",
            payload={
                "entity_type": "character",
                "entity_id": character_id,
                "entity_name": char_name,
                "change_type": "deleted",
                "summary": f"删除人物：{char_name or character_id}",
                "meta": {"character_id": character_id},
            },
        )

    await db.commit()
    return {"message": "已删除"}


@router.get(
    "/{project_id}/locations",
    summary="获取地点列表",
    description="获取指定项目的所有地点卡片。",
)
async def list_locations(project_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    service = CoreMemoryService()
    return await service.list_locations_with_db(db, str(project_id))


async def _publish_location_change(
    *,
    db: AsyncSession,
    project: Project,
    project_id: uuid.UUID,
    location_id: str,
    location_name: str,
    change_type: str,
    data: dict,
) -> None:
    if not (project.core_data or {}).get("weave_coordinator", {}).get("enabled", False):
        return
    await CrossSystemEventBus().publish_event(
        db=db,
        project_id=str(project_id),
        event_type="LOCATION_MODIFIED.v1",
        source_system="worldbuilder",
        priority="normal",
        payload={
            "entity_type": "location",
            "entity_id": location_id,
            "entity_name": location_name,
            "change_type": change_type,
            "summary": f"{change_type}地点：{location_name or location_id}",
            "meta": data,
        },
    )


@router.post(
    "/{project_id}/locations",
    summary="创建地点",
    description="创建一个新的地点卡片。",
)
async def create_location(project_id: uuid.UUID, data: dict, db: AsyncSession = Depends(get_db)):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    name = str(data.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="地点名称不能为空")

    location = await CoreMemoryService().add_location_with_db(db, str(project_id), data)
    await _publish_location_change(
        db=db,
        project=project,
        project_id=project_id,
        location_id=str(location.get("id") or ""),
        location_name=name,
        change_type="created",
        data=data,
    )
    await db.commit()
    return location


@router.put(
    "/{project_id}/locations/{location_id}",
    summary="更新地点",
    description="更新指定的地点卡片。",
)
async def update_location(
    project_id: uuid.UUID,
    location_id: str,
    data: dict,
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    location = await CoreMemoryService().update_location_with_db(
        db, str(project_id), location_id, data,
    )
    if not location:
        raise HTTPException(status_code=404, detail="地点不存在")

    await _publish_location_change(
        db=db,
        project=project,
        project_id=project_id,
        location_id=location_id,
        location_name=str(location.get("name") or data.get("name") or ""),
        change_type="modified",
        data=data,
    )
    await db.commit()
    return location


@router.delete(
    "/{project_id}/locations/{location_id}",
    summary="删除地点",
    description="删除指定的地点卡片。",
)
async def delete_location(
    project_id: uuid.UUID,
    location_id: str,
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    existing = next(
        (
            item for item in (project.core_data or {}).get("locations", [])
            if str(item.get("id") or "") == location_id
        ),
        None,
    )
    deleted = await CoreMemoryService().delete_location_with_db(
        db, str(project_id), location_id,
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="地点不存在")

    await _publish_location_change(
        db=db,
        project=project,
        project_id=project_id,
        location_id=location_id,
        location_name=str((existing or {}).get("name") or ""),
        change_type="deleted",
        data={"location_id": location_id},
    )
    await db.commit()
    return {"message": "已删除"}


@router.get(
    "/{project_id}/overview",
    summary="获取世界观概览",
    description="获取指定项目的世界观概览，包括规则分类统计、人物数量、地点数量。",
)
async def get_worldbuilding_overview(project_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    service = CoreMemoryService()
    rules = await service.list_world_rules(str(project_id))
    characters = await service.list_characters(str(project_id))
    locations = await service.list_locations(str(project_id))
    foreshadowing = await service.list_foreshadowing(str(project_id))
    promotions = await service.list_promotion_proposals(str(project_id))

    shell_service = ShellMemoryService()
    all_seeds = await shell_service.get_seeds_by_tier(str(project_id))
    seed_stats = {"T1": 0, "T2": 0, "T3": 0}
    for s in all_seeds:
        t = s.get("tier", "T3")
        seed_stats[t] = seed_stats.get(t, 0) + 1

    categories: dict[str, int] = {}
    for rule in rules:
        cat = rule.get("category", "general")
        categories[cat] = categories.get(cat, 0) + 1

    foreshadowing_status: dict[str, int] = {}
    for item in foreshadowing:
        st = item.get("status", "planned")
        foreshadowing_status[st] = foreshadowing_status.get(st, 0) + 1

    promotion_status: dict[str, int] = {"pending": 0, "approved": 0, "rejected": 0, "conflict": 0}
    for p in promotions:
        st = p.get("status", "pending")
        promotion_status[st] = promotion_status.get(st, 0) + 1

    obs_result = await db.execute(
        select(WorldviewObservation).where(WorldviewObservation.project_id == project_id)
    )
    all_observations = obs_result.scalars().all()
    obs_by_type: dict[str, int] = {}
    obs_by_status: dict[str, int] = {}
    pending_review = 0
    for obs in all_observations:
        obs_by_type[obs.entity_type] = obs_by_type.get(obs.entity_type, 0) + 1
        obs_by_status[obs.status] = obs_by_status.get(obs.status, 0) + 1
        if obs.status == "active" and not obs.confirmed_by:
            pending_review += 1

    return {
        "total_rules": len(rules),
        "total_characters": len(characters),
        "total_locations": len(locations),
        "total_foreshadowing": len(foreshadowing),
        "total_promotions": len(promotions),
        "promotion_status": promotion_status,
        "foreshadowing_status": foreshadowing_status,
        "rule_categories": categories,
        "seed_stats": seed_stats,
        "rules": rules,
        "characters": [c.model_dump() if hasattr(c, "model_dump") else c for c in characters],
        "locations": locations,
        "foreshadowing": foreshadowing,
        "observations": {
            "total": len(all_observations),
            "by_type": obs_by_type,
            "by_status": obs_by_status,
            "pending_review": pending_review,
        },
    }


@router.get(
    "/{project_id}/foreshadowing",
    summary="获取伏笔列表",
    description="获取指定项目的所有伏笔。",
)
async def list_foreshadowing(project_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    service = CoreMemoryService()
    return await service.list_foreshadowing(str(project_id))


@router.post(
    "/{project_id}/foreshadowing",
    summary="创建伏笔",
    description="创建一条新的伏笔。",
)
async def create_foreshadowing(project_id: uuid.UUID, data: dict, db: AsyncSession = Depends(get_db)):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    service = CoreMemoryService()
    item = await service.create_foreshadowing(str(project_id), data)
    return item


@router.put(
    "/{project_id}/foreshadowing/{item_id}",
    summary="更新伏笔",
    description="更新指定的伏笔。",
)
async def update_foreshadowing(
    project_id: uuid.UUID,
    item_id: str,
    data: dict,
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    service = CoreMemoryService()
    item = await service.update_foreshadowing(str(project_id), item_id, data)
    if not item:
        raise HTTPException(status_code=404, detail="伏笔不存在")
    return item


@router.delete(
    "/{project_id}/foreshadowing/{item_id}",
    summary="删除伏笔",
    description="删除指定的伏笔。",
)
async def delete_foreshadowing(
    project_id: uuid.UUID,
    item_id: str,
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    service = CoreMemoryService()
    success = await service.delete_foreshadowing(str(project_id), item_id)
    if not success:
        raise HTTPException(status_code=404, detail="伏笔不存在")
    return {"message": "已删除"}


@router.get(
    "/{project_id}/foreshadowing/scene",
    summary="查询场景伏笔",
    description="获取指定章节/场景需要埋设或揭示的伏笔。",
)
async def get_foreshadowing_for_scene(
    project_id: uuid.UUID,
    chapter: int,
    scene: int,
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    service = CoreMemoryService()
    return await service.get_active_foreshadowing_for_scene(str(project_id), chapter, scene)


@router.get(
    "/{project_id}/promotions",
    summary="获取晋升提案列表",
    description="获取指定项目的所有晋升提案。",
)
async def list_promotion_proposals(project_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    service = CoreMemoryService()
    return await service.list_promotion_proposals(str(project_id))


@router.post(
    "/{project_id}/promotions",
    summary="创建晋升提案",
    description="创建一个新的晋升提案，将 Shell 层细节种子晋升为 Core 层正式事实。",
)
async def create_promotion_proposal(project_id: uuid.UUID, data: dict, db: AsyncSession = Depends(get_db)):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    service = CoreMemoryService()
    proposal = await service.create_promotion_proposal(str(project_id), data)
    return proposal


@router.post(
    "/{project_id}/promotions/{proposal_id}/approve",
    summary="批准晋升",
    description="批准晋升提案，将种子数据写入 Core 层。",
)
async def approve_promotion(
    project_id: uuid.UUID,
    proposal_id: str,
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    service = CoreMemoryService()
    try:
        proposal = await service.approve_promotion(str(project_id), proposal_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not proposal:
        raise HTTPException(status_code=404, detail="晋升提案不存在")
    return proposal


@router.post(
    "/{project_id}/promotions/{proposal_id}/reject",
    summary="拒绝晋升",
    description="拒绝晋升提案。",
)
async def reject_promotion(
    project_id: uuid.UUID,
    proposal_id: str,
    data: dict = None,
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    service = CoreMemoryService()
    reason = (data or {}).get("reason", "")
    proposal = await service.reject_promotion(str(project_id), proposal_id, reason)
    if not proposal:
        raise HTTPException(status_code=404, detail="晋升提案不存在")
    return proposal


@router.delete(
    "/{project_id}/promotions/{proposal_id}",
    summary="删除晋升提案",
    description="删除指定的晋升提案。",
)
async def delete_promotion_proposal(
    project_id: uuid.UUID,
    proposal_id: str,
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    service = CoreMemoryService()
    success = await service.delete_promotion_proposal(str(project_id), proposal_id)
    if not success:
        raise HTTPException(status_code=404, detail="晋升提案不存在")
    return {"message": "已删除"}


@router.get(
    "/{project_id}/promotions/candidates",
    summary="获取晋升候选",
    description="扫描 Shell 层细节种子，返回引用次数>=3的晋升候选列表。",
)
async def get_promotion_candidates(project_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    service = ShellMemoryService()
    return await service.check_promotion_candidates(str(project_id))


class WorldbuildingChatMessage(BaseModel):
    role: str
    content: str


class WorldbuildingChatRequest(BaseModel):
    messages: list[WorldbuildingChatMessage]
    active_tab: str = "rules"


@router.post(
    "/{project_id}/chat",
    summary="世界观构建对话",
    description="与世界观构建智能体对话，生成世界观设定。",
)
async def worldbuilding_chat(
    project_id: uuid.UUID,
    data: WorldbuildingChatRequest,
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    from app.agents.worldbuilder import WorldbuilderAgent

    project_context = {
        "name": project.name,
        "genre": project.genre,
    }

    agent = WorldbuilderAgent()
    result = await agent.execute({
        "messages": [m.model_dump() for m in data.messages],
        "project_context": project_context,
        "active_tab": data.active_tab,
        "project_id": str(project_id),
        "db": db,
    })

    return {
        "response": result["response"],
        "suggestions": result["suggestions"],
        "mentions": result.get("mentions", []),
        "navigates": result.get("navigates", []),
    }


@router.get(
    "/sub-agents",
    summary="获取世界观构建师子Agent列表",
    description="获取世界观构建师内部7个专业子Agent的配置信息。",
)
async def list_worldbuilder_sub_agents():
    from app.agents.worldbuilder import WorldbuilderAgent

    agent = WorldbuilderAgent()
    return {"sub_agents": agent.get_sub_agent_info()}


@router.get(
    "/sub-agents/{tab}/skills",
    summary="获取子Agent默认技能（全局）",
    description="获取指定子Agent的默认技能列表（无需项目上下文）。",
)
async def get_sub_agent_default_skills(tab: str):
    from app.agents.worldbuilder import SUB_AGENT_CONFIGS

    if tab not in SUB_AGENT_CONFIGS:
        raise HTTPException(status_code=400, detail=f"无效的tab：{tab}")

    config = SUB_AGENT_CONFIGS[tab]
    default_skills = [
        {"name": s, "display_name": s, "description": "默认技能", "is_default": True}
        for s in config.default_skills
    ]
    return {
        "tab": tab,
        "default_skills": default_skills,
        "custom_skills": [],
    }


@router.get(
    "/{project_id}/sub-agents/{tab}/skills",
    summary="获取子Agent技能列表",
    description="获取指定子Agent的默认技能和自定义技能。",
)
async def get_sub_agent_skills(
    project_id: uuid.UUID,
    tab: str,
    db: AsyncSession = Depends(get_db),
):
    from app.agents.worldbuilder import WorldbuilderAgent, SUB_AGENT_CONFIGS

    if tab not in SUB_AGENT_CONFIGS:
        raise HTTPException(status_code=400, detail=f"无效的tab：{tab}")

    config = SUB_AGENT_CONFIGS[tab]
    default_skills = [
        {"name": s, "display_name": s, "description": "默认技能", "is_default": True}
        for s in config.default_skills
    ]
    custom_skills_raw = await WorldbuilderAgent.get_sub_agent_custom_skills(
        tab, str(project_id), db
    )
    custom_skills = [
        {**s, "is_default": False} for s in custom_skills_raw
    ]
    return {
        "tab": tab,
        "default_skills": default_skills,
        "custom_skills": custom_skills,
    }


class SubAgentSkillRequest(BaseModel):
    name: str
    display_name: str = ""
    description: str = ""


@router.post(
    "/{project_id}/sub-agents/{tab}/skills",
    summary="添加子Agent自定义技能",
    description="为指定子Agent添加自定义技能。默认技能不可覆盖。",
)
async def add_sub_agent_skill(
    project_id: uuid.UUID,
    tab: str,
    data: SubAgentSkillRequest,
    db: AsyncSession = Depends(get_db),
):
    from app.agents.worldbuilder import WorldbuilderAgent, SUB_AGENT_CONFIGS

    if tab not in SUB_AGENT_CONFIGS:
        raise HTTPException(status_code=400, detail=f"无效的tab：{tab}")

    result = await WorldbuilderAgent.add_sub_agent_custom_skill(
        tab,
        str(project_id),
        db,
        {
            "name": data.name,
            "display_name": data.display_name or data.name,
            "description": data.description,
        },
    )
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.delete(
    "/{project_id}/sub-agents/{tab}/skills/{skill_name}",
    summary="删除子Agent自定义技能",
    description="删除指定子Agent的自定义技能。默认技能不可删除。",
)
async def delete_sub_agent_skill(
    project_id: uuid.UUID,
    tab: str,
    skill_name: str,
    db: AsyncSession = Depends(get_db),
):
    from app.agents.worldbuilder import WorldbuilderAgent, SUB_AGENT_CONFIGS

    if tab not in SUB_AGENT_CONFIGS:
        raise HTTPException(status_code=400, detail=f"无效的tab：{tab}")

    result = await WorldbuilderAgent.delete_sub_agent_custom_skill(
        tab, str(project_id), db, skill_name
    )
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


class AuthorRulingRequest(BaseModel):
    conflict_id: str
    action: str
    replacement: str | None = None


@router.post(
    "/{project_id}/ruling",
    summary="作者裁决",
    description="对低置信度一致性冲突进行作者裁决。",
)
async def author_ruling(
    project_id: str,
    data: AuthorRulingRequest,
):
    if data.action == "accept":
        return {"status": "accepted", "conflict_id": data.conflict_id}
    elif data.action == "reject":
        return {"status": "rejected", "conflict_id": data.conflict_id}
    elif data.action == "override":
        return {
            "status": "overridden",
            "conflict_id": data.conflict_id,
            "replacement": data.replacement,
        }
    else:
        raise HTTPException(status_code=400, detail="无效的裁决操作")


@router.get("/{project_id}/seeds")
async def list_detail_seeds(project_id: str, tier: str | None = None):
    from app.services.memory_shell import ShellMemoryService
    service = ShellMemoryService()
    seeds = await service.get_seeds_by_tier(project_id, tier)
    return seeds


@router.put("/{project_id}/seeds/{seed_id}/tier")
async def update_seed_tier(project_id: str, seed_id: str, data: dict):
    from app.services.memory_shell import ShellMemoryService
    service = ShellMemoryService()
    result = await service.update_seed_tier(project_id, seed_id, data.get("tier", "T3"))
    if not result:
        raise HTTPException(status_code=404, detail="细节种子不存在")
    return result


@router.post("/{project_id}/compress")
async def compress_volume(project_id: str, data: dict):
    from app.services.memory_shell import ShellMemoryService
    service = ShellMemoryService()
    result = await service.compress_volume(project_id, data.get("volume_end_chapter", 10))
    return result


@router.post(
    "/{project_id}/audit",
    summary="触发世界观审查",
    description="执行世界观一致性审查，检查未录入人物、过期伏笔等问题。",
)
async def trigger_worldview_audit(project_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    from app.services.worldview_audit import WorldviewAuditService

    audit_service = WorldviewAuditService()
    report = await audit_service.audit(str(project_id), db)
    return report


@router.get(
    "/{project_id}/audit",
    summary="获取最近审查报告",
    description="获取最近一次世界观审查报告。",
)
async def get_worldview_audit(project_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    from app.services.worldview_audit import WorldviewAuditService

    audit_service = WorldviewAuditService()
    report = await audit_service.get_audit_report(str(project_id), db)
    if not report:
        return {"message": "尚未执行过世界观审查", "issues": []}
    return report


@router.get(
    "/{project_id}/digest",
    summary="获取世界观摘要",
    description="获取世界观数据摘要，供其他Agent注入上下文使用。",
)
async def get_worldview_digest(project_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    from app.services.worldview_digest import WorldviewDigestService

    digest_service = WorldviewDigestService()
    digest = await digest_service.get_digest(str(project_id), db)
    return {"digest": digest}


@router.get(
    "/{project_id}/chapter-context/{chapter_number}",
    summary="获取章节世界观上下文",
    description="获取指定章节的世界观上下文，包括出场人物、场景地点、应埋/收伏笔。",
)
async def get_chapter_context(
    project_id: uuid.UUID,
    chapter_number: int,
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    from app.services.worldview_digest import WorldviewDigestService

    digest_service = WorldviewDigestService()
    context = await digest_service.get_chapter_context(
        str(project_id), chapter_number, db
    )
    return {"context": context}


@router.get(
    "/{project_id}/observations",
    summary="获取正文发现列表",
    description="获取指定项目的所有世界观观察记录，即正文自动发现的世界观元素。",
)
async def list_observations(
    project_id: uuid.UUID,
    entity_type: str | None = None,
    status: str | None = None,
    review_state: ObservationReviewState | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int | None = Query(default=None, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    # This endpoint only needs existence, not the project's multi-megabyte JSON
    # documents. Keep the observation list on a narrow read path.
    project_exists = await db.scalar(
        select(Project.id).where(Project.id == project_id)
    )
    if project_exists is None:
        raise HTTPException(status_code=404, detail="项目不存在")

    base_filters = [WorldviewObservation.project_id == project_id]
    if entity_type:
        base_filters.append(WorldviewObservation.entity_type == entity_type)
    if status:
        base_filters.append(WorldviewObservation.status == status)

    review_condition = _observation_review_condition(review_state)
    item_filters = list(base_filters)
    if review_condition is not None:
        item_filters.append(review_condition)

    total = int(
        await db.scalar(
            select(func.count())
            .select_from(WorldviewObservation)
            .where(*item_filters)
        )
        or 0
    )

    type_rows = (
        await db.execute(
            select(WorldviewObservation.entity_type, func.count())
            .where(*item_filters)
            .group_by(WorldviewObservation.entity_type)
        )
    ).all()
    by_type = {str(entity_type_value): int(count) for entity_type_value, count in type_rows}

    status_rows = (
        await db.execute(
            select(WorldviewObservation.status, func.count())
            .where(*item_filters)
            .group_by(WorldviewObservation.status)
        )
    ).all()
    by_status = {str(status_value): int(count) for status_value, count in status_rows}

    # Review-tab counts deliberately ignore the selected review_state so the
    # client can switch tabs without first downloading every observation. They
    # still honor explicit entity_type/status filters.
    review_counts_row = (
        await db.execute(
            select(
                func.count().label("all_count"),
                func.sum(case((and_(
                    WorldviewObservation.status == "active",
                    WorldviewObservation.confirmed_by.is_(None),
                ), 1), else_=0)).label("pending_count"),
                func.sum(case((and_(
                    WorldviewObservation.status == "promoted",
                    WorldviewObservation.auto_promoted.is_(True),
                ), 1), else_=0)).label("auto_count"),
                func.sum(case((and_(
                    WorldviewObservation.status == "promoted",
                    or_(
                        WorldviewObservation.auto_promoted.is_(False),
                        WorldviewObservation.auto_promoted.is_(None),
                    ),
                ), 1), else_=0)).label("manual_count"),
            )
            .select_from(WorldviewObservation)
            .where(*base_filters)
        )
    ).one()
    by_review_state = {
        "all": int(review_counts_row.all_count or 0),
        "pending": int(review_counts_row.pending_count or 0),
        "auto": int(review_counts_row.auto_count or 0),
        "manual": int(review_counts_row.manual_count or 0),
    }
    by_review_state["confirmed"] = by_review_state["auto"] + by_review_state["manual"]

    query = (
        select(WorldviewObservation)
        .where(*item_filters)
        .order_by(
            WorldviewObservation.chapter_number,
            WorldviewObservation.created_at,
            WorldviewObservation.id,
        )
    )
    if page_size is not None:
        query = query.offset((page - 1) * page_size).limit(page_size)

    result = await db.execute(query)
    observations = result.scalars().all()

    items = []
    for obs in observations:
        items.append({
            "id": str(obs.id),
            "entity_type": obs.entity_type,
            "entity_name": obs.entity_name,
            "entity_name_normalized": obs.entity_name_normalized,
            "operation": obs.operation,
            "payload": obs.payload or {},
            "confidence": obs.confidence,
            "evidence_text": obs.evidence_text,
            "chapter_number": obs.chapter_number,
            "scene_index": obs.scene_index,
            "status": obs.status,
            "auto_promoted": obs.auto_promoted,
            "core_entity_id": obs.core_entity_id,
            "confirmed_by": obs.confirmed_by,
            "confirmed_at": obs.confirmed_at.isoformat() if obs.confirmed_at else None,
            "orphan_warning": obs.orphan_warning,
            "generation_revision": obs.generation_revision,
            "extraction_version": obs.extraction_version,
            "extraction_source": obs.extraction_source,
            "created_at": obs.created_at.isoformat() if obs.created_at else None,
        })

    total_pages = (
        max(1, (total + page_size - 1) // page_size)
        if page_size is not None
        else 1
    )
    return {
        "items": items,
        "total": total,
        "by_type": by_type,
        "by_status": by_status,
        "by_review_state": by_review_state,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "has_more": page_size is not None and page * page_size < total,
    }


@router.post(
    "/{project_id}/observations/{observation_id}/promote",
    summary="确认观察记录",
    description="人工确认一条世界观观察记录，将其晋升为正式Core卡片。",
)
async def promote_observation(
    project_id: uuid.UUID,
    observation_id: str,
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    observation = await _get_project_observation(db, project_id, observation_id)
    if observation.status != "active":
        raise HTTPException(
            status_code=409,
            detail=f"观察记录状态为 {observation.status}，无法晋升",
        )

    from app.services.worldview_projection_service import WorldviewProjectionService
    wps = WorldviewProjectionService()
    result = await wps.promote_observation(db, observation_id, confirmed_by="user")
    if result.get("error"):
        raise HTTPException(status_code=409, detail=str(result["error"]))
    await db.commit()
    return result


@router.post(
    "/{project_id}/observations/{observation_id}/reject",
    summary="拒绝观察记录",
    description="人工拒绝一条世界观观察记录。",
)
async def reject_observation(
    project_id: uuid.UUID,
    observation_id: str,
    data: dict = None,
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    await _get_project_observation(db, project_id, observation_id)

    from app.services.worldview_projection_service import WorldviewProjectionService
    wps = WorldviewProjectionService()
    reason = (data or {}).get("reason", "")
    result = await wps.reject_observation(db, observation_id, rejected_by="user", reason=reason)
    if result.get("error"):
        raise HTTPException(status_code=409, detail=str(result["error"]))
    await db.commit()
    return result


@router.post(
    "/{project_id}/observations/retry-sync",
    summary="重试失败的同步",
    description="重试世界观投影同步失败的章节。",
)
async def retry_sync(project_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    from app.services.worldview_projection_service import WorldviewProjectionService
    wps = WorldviewProjectionService()
    result = await wps.retry_all_pending(db, project_id)
    await db.commit()
    return result
