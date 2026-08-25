"""市场情报数据模型。

情报页从展示型页面升级为"市场输入 -> 选题决策 -> 对标候选 -> 主编可用合同"的闭环。
"""
from __future__ import annotations
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field


class MarketSnapshotItem(BaseModel):
    """市场快照条目"""
    rank: int = Field(default=0, description="排名")
    title: str = Field(default="", description="书名")
    author: str = Field(default="", description="作者")
    platform: str = Field(default="", description="平台")
    genre: str = Field(default="", description="题材")
    score: float = Field(default=0.0, description="评分")
    read_count: str = Field(default="", description="阅读量")


class MarketSnapshot(BaseModel):
    """市场快照"""
    id: str = Field(default="", description="快照ID")
    platform: str = Field(default="", description="平台")
    rank_type: str = Field(default="bestseller", description="榜单类型")
    captured_at: str = Field(default="", description="采集时间")
    data_quality: Literal["high", "medium", "low"] = Field(default="medium", description="数据质量")
    items: list[MarketSnapshotItem] = Field(default_factory=list, description="条目列表")


class MarketTrend(BaseModel):
    """市场趋势"""
    id: str = Field(default="", description="趋势ID")
    platform: str = Field(default="", description="平台")
    genre: str = Field(default="", description="题材")
    repeated_patterns: list[str] = Field(default_factory=list, description="重复模式")
    title_patterns: list[str] = Field(default_factory=list, description="标题模式")
    opening_selling_points: list[str] = Field(default_factory=list, description="开篇卖点")
    reader_signal: str = Field(default="", description="读者信号")
    confidence: float = Field(default=0.0, description="置信度")


class TopicDecision(BaseModel):
    """选题决策"""
    id: str = Field(default="", description="决策ID")
    project_id: str = Field(default="", description="项目ID")
    target_platform: str = Field(default="", description="目标平台")
    genre: str = Field(default="", description="题材")
    core_emotion: str = Field(default="", description="核心情感")
    candidate_hooks: list[str] = Field(default_factory=list, description="候选钩子")
    benchmark_candidates: list[str] = Field(default_factory=list, description="对标候选ID")
    risk_notes: list[str] = Field(default_factory=list, description="风险提示")
    status: Literal["draft", "confirmed", "active", "archived"] = Field(default="draft", description="状态")
    created_at: str = Field(default="", description="创建时间")


class TopicDecisionContract(BaseModel):
    """选题决策合同（主编可读取的短字段）"""
    target_platform: str = Field(default="", description="目标平台")
    reader_expectation: str = Field(default="", description="读者期待")
    core_emotion: str = Field(default="", description="核心情感")
    opening_pressure: str = Field(default="", description="开篇压力")
    benchmark_strategy_ids: list[str] = Field(default_factory=list, description="对标策略ID")
    avoid_competition_risks: list[str] = Field(default_factory=list, description="避免竞争风险")
