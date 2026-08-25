"""方法论适配层 API"""
from __future__ import annotations
from fastapi import APIRouter, HTTPException
from app.models.methodology import (
    MethodologyCard,
    MethodologyCardQuery,
    MethodologyRule,
    MethodologySource,
)
from app.services.methodology_adapter import get_methodology_adapter

router = APIRouter()


@router.get("/sources")
async def list_methodology_sources():
    adapter = get_methodology_adapter()
    return adapter.list_sources()


@router.post("/sources")
async def register_methodology_source(source: MethodologySource):
    adapter = get_methodology_adapter()
    return adapter.register_source(source)


@router.get("/sources/{source_id}")
async def get_methodology_source(source_id: str):
    adapter = get_methodology_adapter()
    src = adapter.get_source(source_id)
    if not src:
        raise HTTPException(status_code=404, detail="来源不存在")
    return src


@router.put("/sources/{source_id}/toggle")
async def toggle_methodology_source(source_id: str, enabled: bool = True):
    adapter = get_methodology_adapter()
    src = adapter.toggle_source(source_id, enabled)
    if not src:
        raise HTTPException(status_code=404, detail="来源不存在")
    return src


@router.delete("/sources/{source_id}")
async def delete_methodology_source(source_id: str):
    adapter = get_methodology_adapter()
    if not adapter.delete_source(source_id):
        raise HTTPException(status_code=404, detail="来源不存在")
    return {"deleted": True}


@router.post("/cards")
async def add_methodology_card(card: MethodologyCard):
    adapter = get_methodology_adapter()
    return adapter.add_card(card)


@router.post("/cards/query")
async def query_methodology_cards(query: MethodologyCardQuery):
    adapter = get_methodology_adapter()
    return adapter.query_cards(query)


@router.post("/rules")
async def add_methodology_rule(rule: MethodologyRule):
    adapter = get_methodology_adapter()
    return adapter.add_rule(rule)


@router.get("/cards/{card_id}/rules")
async def get_card_rules(card_id: str):
    adapter = get_methodology_adapter()
    return adapter.get_rules_for_card(card_id)


@router.post("/guidance")
async def build_methodology_guidance(
    categories: list[str] | None = None,
    max_token_budget: int = 2000,
):
    adapter = get_methodology_adapter()
    return adapter.build_guidance(categories=categories, max_token_budget=max_token_budget)
