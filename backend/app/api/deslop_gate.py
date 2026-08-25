"""去AI味 Gate 化修复 API"""
from __future__ import annotations
from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.models.deslop_gate import DeslopRepairPlan, DeslopResult
from app.services.deslop_gate_engine import get_deslop_gate_engine

router = APIRouter()


class DeslopDetectRequestModel(BaseModel):
    text: str = Field(default="", description="待检测文本")
    gates: list[str] | None = Field(default=None, description="要检测的Gate列表")
    whitelist: list[str] = Field(default_factory=list, description="白名单词汇")


class DeslopRepairRequestModel(BaseModel):
    text: str = Field(default="", description="待修复文本")
    gates: list[str] | None = Field(default=None, description="要应用的Gate列表")
    max_delete_ratio: float = Field(default=0.3, description="最大删除比例")
    preserve_plot: bool = Field(default=True, description="保护剧情功能句")
    whitelist: list[str] = Field(default_factory=list, description="白名单词汇")


@router.post("/detect")
async def detect_deslop(request: DeslopDetectRequestModel):
    """检测AI味问题"""
    engine = get_deslop_gate_engine()
    engine.set_whitelist(request.whitelist)
    reports = engine.detect(request.text, request.gates)
    return {"reports": [r.model_dump() for r in reports], "total_issues": sum(len(r.evidence_spans) for r in reports)}


@router.post("/repair")
async def repair_deslop(request: DeslopRepairRequestModel):
    """检测并修复AI味问题"""
    engine = get_deslop_gate_engine()
    engine.set_whitelist(request.whitelist)
    reports = engine.detect(request.text, request.gates)
    plan = engine.create_repair_plan(reports, request.max_delete_ratio, request.preserve_plot)
    result = engine.apply_repair(request.text, plan, reports)
    return result.model_dump()


@router.get("/gates")
async def list_deslop_gates():
    """列出所有Gate及其描述"""
    from app.models.deslop_gate import GATE_DESCRIPTIONS, GATE_CHECKER_MAP, GATE_REPAIR_MAP
    return {
        "gates": [
            {
                "gate": gate,
                "description": desc,
                "checker": GATE_CHECKER_MAP.get(gate, ""),
                "repair_strategy": GATE_REPAIR_MAP.get(gate, ""),
            }
            for gate, desc in GATE_DESCRIPTIONS.items()
        ]
    }
