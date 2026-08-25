"""方法论适配层数据模型。

将外部写作方法论（如 oh-story-claudecode 的 references）转换为
万象谱可控的结构化策略卡，而不是直接复制文本进 prompt。
"""
from __future__ import annotations
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field


class MethodologySource(BaseModel):
    """方法论来源"""
    id: str = Field(default="", description="来源ID")
    name: str = Field(default="", description="来源名称")
    source_url: str = Field(default="", description="来源URL")
    license: str = Field(default="MIT", description="许可证")
    version: str = Field(default="", description="版本号")
    imported_at: str = Field(default="", description="导入时间")
    enabled: bool = Field(default=True, description="是否启用")


class MethodologyCard(BaseModel):
    """方法论策略卡"""
    id: str = Field(default="", description="策略卡ID")
    source_id: str = Field(default="", description="来源ID")
    category: Literal[
        "opening_design", "chapter_hook", "paragraph_hook",
        "suspense", "reversal", "emotion_arc", "dialogue",
        "relationship", "anti_ai", "quality_rubric", "market_scan",
    ] = Field(default="opening_design", description="分类")
    title: str = Field(default="", description="标题")
    abstract_rule: str = Field(default="", description="抽象规则")
    applies_to: list[str] = Field(default_factory=list, description="适用范围")
    conflicts_with: list[str] = Field(default_factory=list, description="冲突范围")
    token_cost_estimate: int = Field(default=0, description="预估token消耗")
    safety_level: Literal["safe", "caution", "experimental"] = Field(default="safe", description="安全级别")
    source_file: str = Field(default="", description="原始文件路径")


class MethodologyRule(BaseModel):
    """方法论规则"""
    id: str = Field(default="", description="规则ID")
    card_id: str = Field(default="", description="策略卡ID")
    rule_type: Literal["guidance", "avoid", "example_policy"] = Field(default="guidance", description="规则类型")
    trigger_condition: str = Field(default="", description="触发条件")
    guidance: str = Field(default="", description="指导内容")
    avoid: str = Field(default="", description="应避免的内容")
    examples_policy: Literal["no_examples", "abstract_only", "with_examples"] = Field(default="no_examples", description="示例策略")


class MethodologySourceResponse(BaseModel):
    """方法论来源响应"""
    source: MethodologySource
    card_count: int = 0
    rule_count: int = 0


class MethodologyCardQuery(BaseModel):
    """策略卡查询"""
    category: str | None = None
    source_id: str | None = None
    enabled_only: bool = True
    limit: int = 50
    offset: int = 0


class MethodologyGuidanceResult(BaseModel):
    """方法论指导结果（供生成时使用）"""
    cards: list[MethodologyCard] = Field(default_factory=list)
    rules: list[MethodologyRule] = Field(default_factory=list)
    total_token_estimate: int = 0
