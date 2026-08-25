"""对标拆解中心数据模型。

对标拆解是"项目引用视图"，不是全局经验库。
用户必须明确选择当前项目使用哪些对标书。
"""
from __future__ import annotations
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field


class BenchmarkBook(BaseModel):
    """对标书"""
    id: str = Field(default="", description="对标书ID")
    title: str = Field(default="", description="书名")
    author: str = Field(default="", description="作者")
    platform: str = Field(default="", description="来源平台")
    genre: str = Field(default="", description="题材")
    total_chapters: int = Field(default=0, description="总章节数")
    imported_at: str = Field(default="", description="导入时间")
    project_bindings: list[str] = Field(default_factory=list, description="绑定的项目ID列表")


class BenchmarkChapter(BaseModel):
    """对标章节"""
    id: str = Field(default="", description="章节ID")
    book_id: str = Field(default="", description="对标书ID")
    chapter_number: int = Field(default=0, description="章节编号")
    chapter_title: str = Field(default="", description="章节标题")
    summary: str = Field(default="", description="章节摘要")
    plot_events: list[str] = Field(default_factory=list, description="情节点列表")
    character_mentions: list[str] = Field(default_factory=list, description="角色提及列表")
    hook_type: str = Field(default="", description="钩子类型")
    emotion_arc: str = Field(default="", description="情绪弧线")


class BenchmarkStyleProfile(BaseModel):
    """对标文风画像"""
    id: str = Field(default="", description="画像ID")
    book_id: str = Field(default="", description="对标书ID")
    narrative_voice: str = Field(default="", description="叙事声音")
    sentence_rhythm: str = Field(default="", description="句式节奏")
    dialogue_style: str = Field(default="", description="对话风格")
    description_density: str = Field(default="", description="描写密度")
    key_patterns: list[str] = Field(default_factory=list, description="关键模式")


class BenchmarkStrategyCard(BaseModel):
    """对标策略卡"""
    id: str = Field(default="", description="策略卡ID")
    book_id: str = Field(default="", description="对标书ID")
    category: Literal[
        "opening_design", "chapter_hook", "suspense",
        "reversal", "emotion_arc", "dialogue",
        "relationship", "pacing", "worldbuilding",
    ] = Field(default="opening_design", description="分类")
    title: str = Field(default="", description="标题")
    strategy: str = Field(default="", description="策略描述")
    evidence: str = Field(default="", description="证据")
    applies_to: list[str] = Field(default_factory=list, description="适用范围")


class BenchmarkDeconstructionRequest(BaseModel):
    """对标拆解请求"""
    book_id: str = Field(default="", description="对标书ID")
    focus: Literal["golden_three", "full", "style_only"] = Field(default="golden_three", description="拆解重点")
    max_chapters: int = Field(default=30, description="最大拆解章节数")


class BenchmarkDeconstructionResult(BaseModel):
    """对标拆解结果"""
    book_id: str = Field(default="", description="对标书ID")
    chapters: list[BenchmarkChapter] = Field(default_factory=list, description="章节列表")
    style_profile: BenchmarkStyleProfile | None = Field(default=None, description="文风画像")
    strategy_cards: list[BenchmarkStrategyCard] = Field(default_factory=list, description="策略卡列表")
    status: Literal["pending", "in_progress", "completed", "failed"] = Field(default="pending", description="状态")
    progress: float = Field(default=0.0, description="进度")
    error: str = Field(default="", description="错误信息")
