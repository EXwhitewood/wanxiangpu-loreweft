"""市场情报 API"""
from __future__ import annotations
from fastapi import APIRouter, HTTPException
from app.models.market_intelligence import (
    MarketSnapshot,
    MarketTrend,
    TopicDecision,
)
from app.services.market_intelligence import get_market_intelligence_service

router = APIRouter()


# ---- 快照 ----
@router.get("/snapshots")
async def list_snapshots(platform: str = "", limit: int = 50):
    service = get_market_intelligence_service()
    return service.list_snapshots(platform=platform, limit=limit)


@router.post("/snapshots")
async def import_snapshot(snapshot: MarketSnapshot):
    service = get_market_intelligence_service()
    return service.import_snapshot(snapshot)


@router.get("/snapshots/{snapshot_id}")
async def get_snapshot(snapshot_id: str):
    service = get_market_intelligence_service()
    snapshot = service.get_snapshot(snapshot_id)
    if not snapshot:
        raise HTTPException(status_code=404, detail="快照不存在")
    return snapshot


@router.delete("/snapshots/{snapshot_id}")
async def delete_snapshot(snapshot_id: str):
    service = get_market_intelligence_service()
    if not service.delete_snapshot(snapshot_id):
        raise HTTPException(status_code=404, detail="快照不存在")
    return {"deleted": True}


# ---- 趋势 ----
@router.post("/trends/analyze")
async def analyze_trends(platform: str = ""):
    service = get_market_intelligence_service()
    return service.analyze_trends(platform=platform)


@router.get("/trends")
async def list_trends(platform: str = ""):
    service = get_market_intelligence_service()
    return service.list_trends(platform=platform)


# ---- 选题决策 ----
@router.post("/topic-decisions")
async def create_topic_decision(decision: TopicDecision):
    service = get_market_intelligence_service()
    return service.create_topic_decision(decision)


@router.get("/topic-decisions")
async def list_topic_decisions(project_id: str = ""):
    service = get_market_intelligence_service()
    return service.list_topic_decisions(project_id=project_id)


@router.put("/topic-decisions/{decision_id}/confirm")
async def confirm_topic_decision(decision_id: str):
    service = get_market_intelligence_service()
    decision = service.confirm_topic_decision(decision_id)
    if not decision:
        raise HTTPException(status_code=404, detail="选题决策不存在")
    return decision


@router.get("/projects/{project_id}/topic-contract")
async def get_topic_contract(project_id: str):
    service = get_market_intelligence_service()
    contract = service.get_topic_contract(project_id)
    if not contract:
        return {"target_platform": "", "reader_expectation": "", "core_emotion": "", "opening_pressure": "", "benchmark_strategy_ids": [], "avoid_competition_risks": []}
    return contract.model_dump()
