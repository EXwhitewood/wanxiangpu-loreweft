"""章节结构抽取数据模型。

Chapter Extractor 不负责创作，只负责从已有正文或对标正文中抽取结构资产。
"""
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field


class ChapterExtractionInput(BaseModel):
    """章节抽取输入"""
    chapter_text: str = Field(default="", description="章节正文")
    chapter_number: int = Field(default=0, description="章节编号")
    known_entities: list[str] = Field(default_factory=list, description="已知实体列表")
    known_terms: list[str] = Field(default_factory=list, description="已知术语列表")
    extract_mode: Literal["own_project", "benchmark", "corpus"] = Field(default="own_project", description="抽取模式")


class PlotEvent(BaseModel):
    """情节点"""
    description: str = Field(default="", description="事件描述")
    event_type: Literal["action", "revelation", "decision", "conflict", "resolution", "twist"] = Field(default="action", description="事件类型")
    characters_involved: list[str] = Field(default_factory=list, description="涉及角色")
    impact_level: Literal["major", "moderate", "minor"] = Field(default="moderate", description="影响级别")


class CharacterMention(BaseModel):
    """角色提及"""
    name: str = Field(default="", description="角色名")
    action: str = Field(default="", description="行为")
    state_change: str = Field(default="", description="状态变化")
    emotion: str = Field(default="", description="情绪")


class ForeshadowingOp(BaseModel):
    """伏笔操作"""
    operation: Literal["plant", "advance", "payoff", "abandon"] = Field(default="plant", description="操作类型")
    clue_id: str = Field(default="", description="线索ID")
    description: str = Field(default="", description="描述")


class TimelineEvent(BaseModel):
    """时间线事件"""
    time_marker: str = Field(default="", description="时间标记")
    event: str = Field(default="", description="事件")
    location: str = Field(default="", description="地点")


class HookPoint(BaseModel):
    """钩子点"""
    position: Literal["opening", "mid", "closing"] = Field(default="closing", description="位置")
    hook_type: Literal["question", "mystery", "conflict", "reversal", "promise"] = Field(default="question", description="钩子类型")
    content: str = Field(default="", description="钩子内容")


class ChapterExtractionResult(BaseModel):
    """章节抽取结果"""
    chapter_number: int = Field(default=0, description="章节编号")
    chapter_summary: str = Field(default="", description="章节摘要")
    plot_events: list[PlotEvent] = Field(default_factory=list, description="情节点列表")
    character_mentions: list[CharacterMention] = Field(default_factory=list, description="角色提及列表")
    state_changes: list[str] = Field(default_factory=list, description="状态变化列表")
    foreshadowing_ops: list[ForeshadowingOp] = Field(default_factory=list, description="伏笔操作列表")
    timeline_events: list[TimelineEvent] = Field(default_factory=list, description="时间线事件列表")
    setting_mentions: list[str] = Field(default_factory=list, description="设定提及列表")
    emotion_curve: list[str] = Field(default_factory=list, description="情绪曲线")
    hook_points: list[HookPoint] = Field(default_factory=list, description="钩子点列表")
    information_release_points: list[str] = Field(default_factory=list, description="信息释放点列表")
    extraction_quality: Literal["high", "medium", "low"] = Field(default="medium", description="抽取质量")
    extraction_notes: str = Field(default="", description="抽取备注")
