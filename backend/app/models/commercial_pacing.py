from __future__ import annotations

from pydantic import BaseModel, Field


class CommercialPacingContract(BaseModel):
    """Scene-level commercial pacing target.

    This contract is about reader-retention mechanics only. It must not replace
    source_of_truth, fact contracts, or literary style contracts.
    """

    schema_version: int = 1
    project_id: str = ""
    chapter_number: int = 0
    scene_index: int = 0
    pacing_mode_id: str = "general"
    scene_position: str = "middle"

    reader_hook: str = ""
    reader_question: str = ""
    promise_to_payoff: str = ""
    conflict_driver: str = ""
    pressure_ramp: str = ""
    reversal_plan: list[str] = Field(default_factory=list)
    micro_payoffs: list[str] = Field(default_factory=list)
    withheld_cards: list[str] = Field(default_factory=list)
    chapter_end_hook: str = ""

    target_metrics: dict = Field(default_factory=dict)
    guardrails: list[str] = Field(default_factory=list)

    # 方案 30：新增语义判定辅助字段，供 LlmSemanticChecker/JudgeAgent 参考预期设计
    conflict_beat: list[str] = Field(default_factory=list, description="本场景的压力升级点列表")
    hook_design: dict[str, str] = Field(default_factory=dict, description="钩子设计：{'opening': '...', 'ending': '...'}")
    abstract_explanation_allowlist: list[str] = Field(default_factory=list, description="允许的抽象表达（如世界观设定的必要抽象）")


class CommercialPacingScores(BaseModel):
    opening_hook: float = Field(default=0.0, ge=0.0, le=10.0)
    event_density: float = Field(default=0.0, ge=0.0, le=10.0)
    conflict_density: float = Field(default=0.0, ge=0.0, le=10.0)
    pressure_ramp: float = Field(default=0.0, ge=0.0, le=10.0)
    curiosity_engine: float = Field(default=0.0, ge=0.0, le=10.0)
    reversal_density: float = Field(default=0.0, ge=0.0, le=10.0)
    payoff_delivery: float = Field(default=0.0, ge=0.0, le=10.0)
    chapter_end_hook: float = Field(default=0.0, ge=0.0, le=10.0)
    protagonist_drive: float = Field(default=0.0, ge=0.0, le=10.0)
    reader_retention: float = Field(default=0.0, ge=0.0, le=10.0)


class CommercialPacingReport(BaseModel):
    schema_version: int = 1
    status: str = "ok"
    pacing_mode_id: str = "general"
    scores: CommercialPacingScores = Field(default_factory=CommercialPacingScores)
    metrics: dict = Field(default_factory=dict)
    benchmark_targets: dict = Field(default_factory=dict)
    advisories: list[dict] = Field(default_factory=list)
    summary: str = ""
    degraded: bool = False
    error: str = ""
