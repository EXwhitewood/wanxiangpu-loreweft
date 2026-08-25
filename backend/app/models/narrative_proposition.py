"""叙事命题层模型定义。

通用叙事命题层：正文和系统事实之间的中间表示。
只回答"正文声称了什么"，不评价文笔。
所有枚举和规则必须题材无关、作品无关、项目无关。
"""

from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# 枚举定义
# ---------------------------------------------------------------------------

TruthLayer = Literal[
    "current",         # 当前事实
    "reference",       # 参考设定、原书、历史资料、外部剧本
    "memory",          # 角色记忆
    "dream",           # 梦境、幻觉、预知画面
    "rumor",           # 传闻
    "inference",       # 角色推测
    "plan",            # 计划、意图
    "future_hint",     # 未来暗示、预言、未发生片段
    "counterfactual",  # 假设、如果、脑补
]

Certainty = Literal[
    "confirmed",    # 已确认
    "reported",     # 被记载、被说法声称
    "accused",      # 被指控
    "suspected",    # 被怀疑
    "inferred",     # 推断
    "unknown",      # 未确认
    "negated",      # 被否定
    "misleading",   # 可能误导
]

Responsibility = Literal[
    "active_actor",  # 主动行为
    "victim",        # 受害者
    "framed",        # 被栽赃
    "accused",       # 被指认
    "coerced",       # 被迫
    "witness",       # 目击或知情
    "unknown",       # 不明
]

Polarity = Literal[
    "affirmed",   # 肯定
    "negated",    # 否定
    "ambiguous",  # 模糊
]

PredicateCategory = Literal[
    "action",       # 行为
    "state",        # 状态
    "location",     # 位置
    "ownership",    # 归属
    "relationship", # 关系
    "knowledge",    # 认知
    "intention",    # 意图
    "event",        # 事件
    "clue",         # 线索
]

EntityType = Literal[
    "character",      # 角色
    "item",           # 物品
    "location",       # 地点
    "organization",   # 组织
    "concept",        # 概念
    "event",          # 事件
    "unknown",        # 未知
]

PropositionLifecycle = Literal[
    "current",
    "superseded",
    "historical_event",
    "unresolved",
    "planned",
    "retracted",
]


# ---------------------------------------------------------------------------
# 核心模型
# ---------------------------------------------------------------------------

class NarrativeEntity(BaseModel):
    """叙事实体：角色、物品、地点、组织等。"""
    name: str
    entity_type: EntityType = "unknown"
    entity_id: str | None = None
    aliases: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str) -> str:
        value = (value or "").strip()
        if not value:
            raise ValueError("entity name must not be blank")
        return value


class NarrativePredicate(BaseModel):
    """叙事谓词：行为、状态、位置等。"""
    name: str
    category: PredicateCategory

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str) -> str:
        value = (value or "").strip()
        if not value:
            raise ValueError("predicate name must not be blank")
        return value


class NarrativeProposition(BaseModel):
    """叙事命题：正文声称的结构化表示。

    每条命题对应正文中的一个声称，包含来源文本、真值层、确定性、
    责任归属等维度，用于后续审计和一致性检查。
    """
    proposition_id: str
    project_id: str
    chapter_number: int
    scene_index: int
    generation_revision: int = 1

    subject: NarrativeEntity
    predicate: NarrativePredicate
    object: NarrativeEntity | None = None

    truth_layer: TruthLayer
    certainty: Certainty
    polarity: Polarity = "affirmed"
    responsibility: Responsibility = "unknown"
    time_scope: str = ""
    location_scope: str = ""

    source_text: str
    source_agent: str = "narrative_proposition_extractor"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    lifecycle_status: PropositionLifecycle = "current"
    valid_from_chapter: int | None = Field(default=None, ge=1)
    valid_to_chapter: int | None = Field(default=None, ge=1)
    supersedes_id: str | None = None
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    last_confirmed_chapter: int | None = Field(default=None, ge=1)
    source_chunk_index: int | None = Field(default=None, ge=0)

    @field_validator("source_text")
    @classmethod
    def _source_text_not_blank(cls, value: str) -> str:
        value = (value or "").strip()
        if not value:
            raise ValueError("source_text must not be blank")
        return value


# ---------------------------------------------------------------------------
# FactContract 模型
# ---------------------------------------------------------------------------

class FactConstraint(BaseModel):
    """事实约束：用于 forbidden_assertions / required_ambiguities 等。"""
    event_type: str = ""
    subject_role: str = ""
    responsibility: Responsibility | None = None
    reason: str = ""
    allowed_certainty: list[Certainty] = Field(default_factory=list)
    forbidden_certainty: list[Certainty] = Field(default_factory=list)


class SpatialConstraint(BaseModel):
    """空间约束：同一主体同一时间不得处于互斥空间。"""
    subject_name: str
    allowed_locations: list[str] = Field(default_factory=list)
    forbidden_locations: list[str] = Field(default_factory=list)
    reason: str = ""


class TemporalConstraint(BaseModel):
    """时间约束：事件顺序或已完成事件不得重演。"""
    event_description: str
    must_before: list[str] = Field(default_factory=list)
    must_after: list[str] = Field(default_factory=list)
    no_replay: bool = False
    reason: str = ""


class ResponsibilityConstraint(BaseModel):
    """责任归属约束：事件责任极性约束。"""
    event_type: str
    subject_role: str = ""
    allowed_responsibility: list[Responsibility] = Field(default_factory=list)
    forbidden_responsibility: list[Responsibility] = Field(default_factory=list)
    reason: str = ""


class ClueConstraint(BaseModel):
    """线索来源约束：线索必须有来源、放置者、发现条件。"""
    clue_description: str
    required_source_actor: bool = True
    required_placement_time: bool = False
    required_discovery_condition: bool = False
    source_actor: str = ""
    placement_time: str = ""
    discovery_condition: str = ""
    reason: str = ""


class FactContract(BaseModel):
    """场景事实合同：描述事实边界，作为 scene_contract 的子结构。

    fact_contract 不替代 scene_contract，而是补充事实层面的约束。
    """
    schema_version: int = 1
    current_facts: list[str] = Field(default_factory=list)
    reference_facts: list[str] = Field(default_factory=list)
    uncertain_facts: list[str] = Field(default_factory=list)
    required_truth_layers: list[TruthLayer] = Field(default_factory=list)
    forbidden_assertions: list[FactConstraint] = Field(default_factory=list)
    required_ambiguities: list[FactConstraint] = Field(default_factory=list)
    responsibility_constraints: list[ResponsibilityConstraint] = Field(default_factory=list)
    spatial_constraints: list[SpatialConstraint] = Field(default_factory=list)
    temporal_constraints: list[TemporalConstraint] = Field(default_factory=list)
    clue_constraints: list[ClueConstraint] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 抽取/审计结果模型
# ---------------------------------------------------------------------------

class ExtractionResult(BaseModel):
    """命题抽取结果。"""
    propositions: list[NarrativeProposition] = Field(default_factory=list)
    entity_mentions: list[NarrativeEntity] = Field(default_factory=list)
    ambiguous_claims: list[dict] = Field(default_factory=list)
    extractor_warnings: list[str] = Field(default_factory=list)
    input_chars: int = 0
    covered_chars: int = 0
    chunk_count: int = 0
    failed_chunks: list[int] = Field(default_factory=list)
    complete: bool = True


class AuditViolation(BaseModel):
    """命题审计违规。"""
    type: str
    severity: Literal["critical", "high", "medium", "low"] = "high"
    blocks_commit: bool = True
    target_span: str = ""
    expected_behavior: str = ""
    evidence: dict = Field(default_factory=dict)
    suggested_strategy: str = "patch_text"


class AuditReport(BaseModel):
    """命题审计报告。"""
    passed: bool = False
    commit_blocked: bool = False
    violations: list[AuditViolation] = Field(default_factory=list)
    proposition_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# SceneContractCompiler 输出模型
# ---------------------------------------------------------------------------

class CompiledContract(BaseModel):
    """编译后的场景合同，包含 scene_contract 和 fact_contract。"""
    scene_contract: dict = Field(default_factory=dict)
    fact_contract: FactContract = Field(default_factory=FactContract)
    compiler_warnings: list[str] = Field(default_factory=list)
    blocked_contract: bool = False
