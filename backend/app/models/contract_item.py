"""统一合同条目模型。

所有从大纲、主编、Ledger、方法论、对标、情报、质量层来的信息，
都先变成 ContractItem，统一语义后再分配给场景。

关键规则：
- Writer 只能看到 visibility == writer_visible 的 item
- 每个场景 current_scene_must 不得超过预算
- future_hidden 不能进入 Writer
- quality_check_only 只能给质量门
- advisory 不能阻断生成
"""
from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Literal


class ContractItem(BaseModel):
    """统一合同条目"""
    id: str = ""
    text: str
    source: Literal[
        "outline",
        "editor",
        "chapter_plan",
        "scene_plan",
        "sqlite_story_memory",
        "methodology",
        "benchmark",
        "market_intelligence",
        "style_profile",
        "quality_gate",
        "user",
    ] = "editor"
    source_ref: str | None = None
    layer: Literal[
        "fact",
        "plot",
        "character",
        "emotion",
        "foreshadowing",
        "style",
        "pacing",
        "commercial",
        "literary",
        "safety",
    ] = "plot"
    obligation_type: Literal[
        "hard_fact",
        "current_scene_must",
        "current_scene_soft_hint",
        "chapter_goal",
        "carry_forward",
        "future_hidden",
        "forbidden",
        "advisory",
        "quality_check_only",
    ] = "advisory"
    priority: Literal["critical", "high", "medium", "low"] = "medium"
    certainty: Literal["confirmed", "likely", "inferred", "unknown"] = "likely"
    visibility: Literal[
        "writer_visible",
        "editor_only",
        "quality_only",
        "repair_only",
        "hidden",
    ] = "writer_visible"
    scene_scope: list[str] = Field(default_factory=list)
    due_scene_id: str | None = None
    word_cost: int = 0
    explanation_risk: float = 0.0
    conflict_group: str | None = None
    status: Literal[
        "pending",
        "allocated",
        "consumed",
        "deferred",
        "cancelled",
        "superseded",
    ] = "pending"
    evidence: list[str] = Field(default_factory=list)

    def is_writer_visible(self) -> bool:
        """Writer 是否可见"""
        return self.visibility == "writer_visible"

    def is_blocking(self) -> bool:
        """是否阻断生成"""
        return self.obligation_type in ("hard_fact", "current_scene_must", "forbidden") and self.priority in ("critical", "high")

    def is_allocatable(self) -> bool:
        """是否可分配到场景"""
        return self.status == "pending" and self.obligation_type not in ("advisory", "quality_check_only")


class InformationBudget(BaseModel):
    """信息预算"""
    target_chars: int = 3000
    hard_max_chars: int = 5000
    max_current_scene_must: int = 3
    max_soft_hints: int = 2
    max_new_named_entities: int = 2
    max_new_terms: int = 2
    max_definition_sentences_per_1000_chars: float = 1.0
    max_explanation_markers_per_1000_chars: float = 2.0
    max_foreshadowing_ops: int = 1
    max_reader_questions_opened: int = 1


class CompiledSceneContract(BaseModel):
    """编译后的场景合同——Writer 可消费的最终场景合同"""
    scene_id: str = ""
    chapter_id: str = ""
    pov_character: str | None = None
    scene_function: str = ""
    target_emotion: str = ""
    target_chars: int = 3000
    hard_facts: list[ContractItem] = Field(default_factory=list)
    current_scene_must: list[ContractItem] = Field(default_factory=list)
    soft_hints: list[ContractItem] = Field(default_factory=list)
    forbidden: list[ContractItem] = Field(default_factory=list)
    ending_state: str = ""
    style_policy: dict = Field(default_factory=dict)
    # P1-18 修复：新增 style_directive 字段，承载风格指令（声纹/修辞/禁忌等）
    style_directive: dict = Field(default_factory=dict)
    pacing_policy: dict = Field(default_factory=dict)
    information_budget: InformationBudget = Field(default_factory=InformationBudget)
    # 以下不进入 Writer
    hidden_carry_forward: list[ContractItem] = Field(default_factory=list)
    quality_check_only: list[ContractItem] = Field(default_factory=list)

    def writer_visible_items(self) -> list[ContractItem]:
        """获取 Writer 可见的所有 item"""
        items = []
        items.extend(self.hard_facts)
        items.extend(self.current_scene_must)
        items.extend(self.soft_hints)
        items.extend(self.forbidden)
        return [i for i in items if i.is_writer_visible()]

    def budget_check(self) -> dict:
        """检查是否超预算"""
        must_count = len(self.current_scene_must)
        soft_count = len(self.soft_hints)
        budget = self.information_budget
        return {
            "must_over_budget": must_count > budget.max_current_scene_must,
            "soft_over_budget": soft_count > budget.max_soft_hints,
            "must_count": must_count,
            "soft_count": soft_count,
            "max_must": budget.max_current_scene_must,
            "max_soft": budget.max_soft_hints,
        }


class WriterInputPacket(BaseModel):
    """Writer 输入包——Writer 唯一输入"""
    system_rules: list[str] = Field(default_factory=list)
    scene_task: str = ""
    visible_facts: list[str] = Field(default_factory=list)
    must_include: list[str] = Field(default_factory=list)
    soft_suggestions: list[str] = Field(default_factory=list)
    must_avoid: list[str] = Field(default_factory=list)
    ending_state: str = ""
    style_instruction: str = ""
    pacing_instruction: str = ""
    output_constraints: dict = Field(default_factory=dict)
    active_capabilities: list[str] = Field(default_factory=list)
    activation_reasons: dict[str, str] = Field(default_factory=dict)
    source_trace: dict[str, list[str]] = Field(default_factory=dict)
    writer_input_enrichment: dict = Field(default_factory=dict)
