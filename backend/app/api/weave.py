import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.db_models import get_db, Project, CrossSystemProposal, CoordinatorRun, CrossSystemEventDelivery
from app.services.cross_system_event_bus import CrossSystemEventBus
from app.services.cross_system_proposal_service import CrossSystemProposalService
from app.services.coordination_read_model_service import CoordinationReadModelService
from app.agents.weave_coordinator import WeaveCoordinatorAgent

router = APIRouter()


class ReviewProposalRequest(BaseModel):
    action: str
    review_notes: str = ""
    modified_data: dict | None = None


class AckDeliveryRequest(BaseModel):
    consumer_id: str


class ConsistencyCheckRequest(BaseModel):
    scope: str = "full"
    target_chapter: int | None = None


@router.get(
    "/status",
    summary="获取协调状态摘要",
    description="获取指定项目的跨系统协调状态，包括待投递数、活跃冲突数、待审批提案数和一致性评分。",
)
async def get_status(
    project_id: uuid.UUID,
    target_system: str = Query(default="editor"),
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    service = CoordinationReadModelService()
    return await service.get_status_summary(db, str(project_id), target_system)


@router.get(
    "/proposals",
    summary="获取提案列表",
    description="获取指定项目的跨系统提案列表，支持按状态、目标域、提案类型筛选。",
)
async def list_proposals(
    project_id: uuid.UUID,
    status: str | None = None,
    target_domain: str | None = None,
    proposal_type: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    service = CoordinationReadModelService()
    filters = {
        "status": status,
        "target_domain": target_domain,
        "proposal_type": proposal_type,
        "limit": limit,
        "offset": offset,
    }
    return await service.get_proposal_list(db, str(project_id), filters)


@router.get(
    "/proposals/{proposal_id}",
    summary="获取提案详情",
    description="获取指定提案的完整详情。",
)
async def get_proposal(
    project_id: uuid.UUID,
    proposal_id: str,
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    proposal = await db.get(CrossSystemProposal, proposal_id)
    if not proposal or str(proposal.project_id) != str(project_id):
        raise HTTPException(status_code=404, detail="提案不存在")
    return proposal


@router.put(
    "/proposals/{proposal_id}/review",
    summary="审查提案",
    description="审批或拒绝待审核提案。action 可选 approve 或 reject。",
)
async def review_proposal(
    project_id: uuid.UUID,
    proposal_id: str,
    data: ReviewProposalRequest,
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    if data.action not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="action 必须为 approve 或 reject")

    service = CrossSystemProposalService()
    proposal = await service.review_proposal(
        db,
        proposal_id,
        data.action,
        reviewed_by="user",
        review_notes=data.review_notes,
        modified_data=data.modified_data,
    )
    if not proposal:
        raise HTTPException(status_code=404, detail="提案不存在或状态不可审查")
    if str(proposal.project_id) != str(project_id):
        raise HTTPException(status_code=403, detail="提案不属于当前项目")
    await db.commit()
    return proposal


@router.post(
    "/proposals/{proposal_id}/execute",
    summary="执行已审批提案",
    description="执行状态为 approved 的提案，将其变更应用到目标域。",
)
async def execute_proposal(
    project_id: uuid.UUID,
    proposal_id: str,
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    proposal_check = await db.get(CrossSystemProposal, proposal_id)
    if not proposal_check or str(proposal_check.project_id) != str(project_id):
        raise HTTPException(status_code=404, detail="提案不存在或不属于当前项目")

    service = CrossSystemProposalService()
    result = await service.execute_proposal(db, proposal_id)
    if result.get("status") == "not_found":
        raise HTTPException(status_code=404, detail="提案不存在")
    if result.get("status") == "invalid_state":
        raise HTTPException(status_code=400, detail=f"提案状态不可执行: {result.get('current')}")
    if result.get("status") == "failed":
        raise HTTPException(status_code=500, detail=result.get("reason", "执行失败"))
    await db.commit()
    return result


@router.get(
    "/events",
    summary="获取事件列表",
    description="获取指定项目的跨系统事件投递记录。",
)
async def list_events(
    project_id: uuid.UUID,
    target_system: str = Query(default="editor"),
    status: str | None = None,
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    event_bus = CrossSystemEventBus()
    deliveries = await event_bus.get_deliveries(
        db,
        str(project_id),
        target_system,
        status_filter=status,
        limit=limit,
    )
    return {"items": deliveries, "limit": limit, "offset": offset}


@router.get(
    "/deliveries",
    summary="获取投递记录",
    description="获取指定项目的跨系统事件投递记录。",
)
async def list_deliveries(
    project_id: uuid.UUID,
    target_system: str = Query(default="editor"),
    status: str | None = None,
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    event_bus = CrossSystemEventBus()
    deliveries = await event_bus.get_deliveries(
        db,
        str(project_id),
        target_system,
        status_filter=status,
        limit=limit,
    )
    return {"items": deliveries, "limit": limit, "offset": offset}


@router.put(
    "/deliveries/{delivery_id}/ack",
    summary="确认投递",
    description="确认指定投递记录已处理。",
)
async def ack_delivery(
    project_id: uuid.UUID,
    delivery_id: str,
    data: AckDeliveryRequest,
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    delivery = await db.get(CrossSystemEventDelivery, delivery_id)
    if not delivery:
        raise HTTPException(status_code=404, detail="投递记录不存在")
    if str(delivery.project_id) != str(project_id):
        raise HTTPException(status_code=403, detail="投递记录不属于当前项目")
    if delivery.claimed_by and delivery.claimed_by != data.consumer_id:
        raise HTTPException(status_code=403, detail="无权确认此投递")

    event_bus = CrossSystemEventBus()
    await event_bus.mark_acknowledged(
        db,
        [delivery_id],
        resolved_by="user",
        consumer_id=data.consumer_id,
    )
    await db.commit()
    return {"status": "acknowledged", "delivery_id": delivery_id}


@router.post(
    "/check",
    summary="手动触发一致性检查",
    description="手动触发织梦协调器进行一致性冲突分析。",
)
async def trigger_consistency_check(
    project_id: uuid.UUID,
    data: ConsistencyCheckRequest,
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    agent = WeaveCoordinatorAgent()
    result = await agent.analyze_conflicts(
        db,
        str(project_id),
        scope=data.scope,
        target_chapter=data.target_chapter,
    )
    return result


@router.get(
    "/impact",
    summary="影响分析",
    description="分析指定实体变更对其他系统的影响范围。",
)
async def analyze_impact(
    project_id: uuid.UUID,
    entity_type: str = Query(...),
    entity_id_or_name: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    agent = WeaveCoordinatorAgent()
    result = await agent.analyze_impact(
        db,
        str(project_id),
        entity_type=entity_type,
        entity_id_or_name=entity_id_or_name,
    )
    return result


@router.get(
    "/runs",
    summary="协调运行历史",
    description="获取织梦协调器的运行历史记录。",
)
async def list_coordinator_runs(
    project_id: uuid.UUID,
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    stmt = (
        select(CoordinatorRun)
        .where(CoordinatorRun.project_id == str(project_id))
        .order_by(CoordinatorRun.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await db.execute(stmt)
    runs = list(result.scalars().all())
    return {"items": runs, "limit": limit, "offset": offset}
