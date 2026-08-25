"""统一审查发现 API"""
from __future__ import annotations
from fastapi import APIRouter
from pydantic import BaseModel, Field
from app.services.review_finding_normalizer import get_review_finding_normalizer

router = APIRouter()


class NormalizeViolationsRequest(BaseModel):
    violations: list[dict] = Field(default_factory=list, description="Violation 列表")


class NormalizeDeslopRequest(BaseModel):
    reports: list[dict] = Field(default_factory=list, description="Deslop Gate 报告列表")


class NormalizeAllRequest(BaseModel):
    violations: list[dict] = Field(default_factory=list, description="Violation 列表")
    deslop_reports: list[dict] | None = Field(default=None, description="Deslop Gate 报告列表")


@router.post("/normalize")
async def normalize_violations(request: NormalizeViolationsRequest):
    """归一化 Violation 列表为 ReviewFinding"""
    normalizer = get_review_finding_normalizer()
    return [normalizer.normalize_violation(v) for v in request.violations]


@router.post("/normalize-deslop")
async def normalize_deslop_reports(request: NormalizeDeslopRequest):
    """归一化 Deslop Gate 报告为 ReviewFinding"""
    normalizer = get_review_finding_normalizer()
    return [normalizer.normalize_deslop_report(r) for r in request.reports]


@router.post("/normalize-all")
async def normalize_all(request: NormalizeAllRequest):
    """归一化所有来源的审查发现"""
    normalizer = get_review_finding_normalizer()
    return normalizer.normalize_all(request.violations, request.deslop_reports)


@router.post("/filter-actionable")
async def filter_actionable(findings: list[dict]):
    """过滤出可操作的发现"""
    normalizer = get_review_finding_normalizer()
    return normalizer.filter_actionable(findings)


@router.post("/group-by-scope")
async def group_by_scope(findings: list[dict]):
    """按 scope 分组"""
    normalizer = get_review_finding_normalizer()
    return normalizer.group_by_scope(findings)


@router.post("/group-by-repair-strategy")
async def group_by_repair_strategy(findings: list[dict]):
    """按修复策略分组"""
    normalizer = get_review_finding_normalizer()
    return normalizer.group_by_repair_strategy(findings)
