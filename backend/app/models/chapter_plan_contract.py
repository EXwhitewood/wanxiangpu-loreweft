"""章级合同数据模型。

ChapterPlanContract 是比 SceneContract 更高层的规划合同。
主编规划器从 Project Brief + TopicDecision + SQLite 故事状态摘要 +
BenchmarkStrategyCards + WritingModeProfile 编译出章级合同，
再由 SceneContractCompiler 编译为场景合同。
"""
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field


class ChapterPlanContract(BaseModel):
    """章级规划合同"""
    chapter_number: int = Field(default=0, description="章节编号")
    chapter_function: Literal[
        "opening_hook", "setup", "escalation", "reversal",
        "payoff", "transition", "climax", "resolution", "breather",
    ] = Field(default="setup", description="章节功能")
    target_emotion: str = Field(default="", description="目标情绪")
    reader_question_to_open: str = Field(default="", description="要打开的读者问题")
    reader_question_to_close: str = Field(default="", description="要关闭的读者问题")
    main_conflict: str = Field(default="", description="主要冲突")
    must_progress: list[str] = Field(default_factory=list, description="必须推进的情节线")
    must_not_resolve: list[str] = Field(default_factory=list, description="不能在本章解决的悬念")
    payoff_or_setup: Literal["payoff", "setup", "both", "neither"] = Field(default="setup", description="是兑现还是铺垫")
    character_delta: list[str] = Field(default_factory=list, description="角色变化列表")
    relationship_delta: list[str] = Field(default_factory=list, description="关系变化列表")
    foreshadowing_ops: list[str] = Field(default_factory=list, description="伏笔操作列表")
    scene_budget: int = Field(default=3, description="场景预算（场景数）")
    word_budget: int = Field(default=3000, description="字数预算")


class ChapterPlanSequence(BaseModel):
    """章级规划序列"""
    project_id: str = Field(default="", description="项目ID")
    chapters: list[ChapterPlanContract] = Field(default_factory=list, description="章级合同列表")
    total_chapters: int = Field(default=0, description="总章节数")
    created_at: str = Field(default="", description="创建时间")
    source: str = Field(default="", description="来源（如 editor_planner）")


class ChapterPlanCompileInput(BaseModel):
    """章级合同编译输入"""
    project_id: str = Field(default="", description="项目ID")
    project_brief: str = Field(default="", description="项目简介")
    outline_data: dict = Field(default_factory=dict, description="大纲数据")
    topic_decision: dict = Field(default_factory=dict, description="选题决策")
    story_state: dict = Field(default_factory=dict, description="故事状态")
    benchmark_strategies: list[dict] = Field(default_factory=list, description="对标策略卡")
    writing_mode: dict = Field(default_factory=dict, description="写作模式配置")
    start_chapter: int = Field(default=1, description="起始章节")
    end_chapter: int = Field(default=10, description="结束章节")
