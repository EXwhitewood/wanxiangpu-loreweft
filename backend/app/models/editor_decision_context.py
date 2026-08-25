"""主编决策上下文数据模型。

核心原则：这些模块不能直接抢主编的方向盘，只能给主编提供结构化证据、约束和复检结果。
上下文必须有 token 预算（1500-2500中文字），不然会撑爆生成链。
"""
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field


class StylePolicy(BaseModel):
    """风格策略"""
    allowed_styles: list[str] = Field(default_factory=list, description="允许的写法")
    forbidden_patterns: list[str] = Field(default_factory=list, description="禁止的写法")
    frozen_style_name: str = Field(default="", description="冻结的风格名称")


class TopicContractSummary(BaseModel):
    """选题合同摘要"""
    target_platform: str = Field(default="", description="目标平台")
    core_emotion: str = Field(default="", description="核心情感")
    reader_expectation: str = Field(default="", description="读者期待")
    opening_pressure: str = Field(default="", description="开篇压力")


class MethodologyGuidanceSummary(BaseModel):
    """方法论指导摘要"""
    active_cards: list[str] = Field(default_factory=list, description="激活的策略卡标题")
    key_rules: list[str] = Field(default_factory=list, description="关键规则")


class BenchmarkGuidanceSummary(BaseModel):
    """对标指导摘要"""
    active_benchmarks: list[str] = Field(default_factory=list, description="激活的对标书")
    strategy_highlights: list[str] = Field(default_factory=list, description="策略要点")


class StoryStateSummary(BaseModel):
    """故事状态摘要"""
    active_characters: list[str] = Field(default_factory=list, description="活跃角色")
    open_foreshadowing: list[str] = Field(default_factory=list, description="未回收伏笔")
    open_questions: list[str] = Field(default_factory=list, description="未解问题")
    recent_relationships: list[str] = Field(default_factory=list, description="最近关系变化")


class ActiveConstraint(BaseModel):
    """活跃约束"""
    constraint_type: Literal["fact", "style", "pacing", "contract", "methodology"] = Field(default="fact", description="约束类型")
    description: str = Field(default="", description="约束描述")
    source: str = Field(default="", description="来源模块")


class RiskWarning(BaseModel):
    """风险提示"""
    risk_type: str = Field(default="", description="风险类型")
    description: str = Field(default="", description="风险描述")
    source: str = Field(default="", description="来源模块")


class EditorDecisionContext(BaseModel):
    """主编决策上下文

    这是所有外部模块整理后的统一入口。
    主编 Agent 不应该直接看到一大堆原始数据，而应该看到这份结构化上下文。
    """
    project_id: str = Field(default="", description="项目ID")
    chapter_number: int = Field(default=0, description="章节编号")
    writing_mode: str = Field(default="", description="写作模式")
    style_policy: StylePolicy = Field(default_factory=StylePolicy, description="风格策略")
    topic_contract: TopicContractSummary = Field(default_factory=TopicContractSummary, description="选题合同")
    methodology_guidance: MethodologyGuidanceSummary = Field(default_factory=MethodologyGuidanceSummary, description="方法论指导")
    benchmark_guidance: BenchmarkGuidanceSummary = Field(default_factory=BenchmarkGuidanceSummary, description="对标指导")
    story_state_summary: StoryStateSummary = Field(default_factory=StoryStateSummary, description="故事状态摘要")
    active_constraints: list[ActiveConstraint] = Field(default_factory=list, description="活跃约束")
    risk_warnings: list[RiskWarning] = Field(default_factory=list, description="风险提示")
    token_budget_used: int = Field(default=0, description="已使用的token预算")
    enabled_modules: list[str] = Field(default_factory=list, description="已启用的模块列表")


class EditorDecisionContextRequest(BaseModel):
    """主编决策上下文构建请求"""
    project_id: str = Field(default="", description="项目ID")
    chapter_number: int = Field(default=1, description="章节编号")
    max_token_budget: int = Field(default=2000, description="最大token预算（中文字数）")
    enabled_modules: list[str] | None = Field(default=None, description="要启用的模块列表，None=全部")


class ChapterDecisionBrief(BaseModel):
    """本章决策依据（主编 Agent 看到的格式）

    主编 Agent 不应该直接看到一大堆原始数据，而应该看到类似：
    【本章决策依据】
    1. 章级目标：
    2. 当前人物状态：
    3. 必须延续的事实：
    4. 本章推荐节奏：
    5. 信息释放上限：
    6. 禁止事项：
    7. 风格保护要求：
    8. 可选商业爽点：
    """
    chapter_goal: str = Field(default="", description="章级目标")
    character_states: str = Field(default="", description="当前人物状态")
    facts_to_continue: list[str] = Field(default_factory=list, description="必须延续的事实")
    recommended_pacing: str = Field(default="", description="本章推荐节奏")
    info_release_limit: str = Field(default="", description="信息释放上限")
    forbidden_items: list[str] = Field(default_factory=list, description="禁止事项")
    style_protection: str = Field(default="", description="风格保护要求")
    commercial_hooks: list[str] = Field(default_factory=list, description="可选商业爽点")
    risk_notes: list[str] = Field(default_factory=list, description="风险提示")
