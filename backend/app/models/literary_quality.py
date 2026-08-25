from __future__ import annotations

from pydantic import BaseModel, Field


class LiteraryQualityContract(BaseModel):
    schema_version: int = 1
    project_id: str = ""
    chapter_number: int = 0
    scene_index: int = 0
    writing_mode_id: str = "general"

    prose_goal: str = ""
    specificity_budget: dict = Field(default_factory=dict)
    abstraction_ceiling: str = "medium"
    exposition_policy: dict = Field(default_factory=dict)
    dialogue_policy: dict = Field(default_factory=dict)
    character_voice_policy: dict = Field(default_factory=dict)
    sensory_policy: dict = Field(default_factory=dict)
    cultural_texture_policy: dict = Field(default_factory=dict)
    motif_policy: dict = Field(default_factory=dict)
    humor_policy: dict = Field(default_factory=dict)
    intertextuality_policy: dict = Field(default_factory=dict)
    rhythm_policy: dict = Field(default_factory=dict)
    style_alignment_policy: dict = Field(default_factory=dict)

    avoid_patterns: list[str] = Field(default_factory=list)
    required_textual_moves: list[str] = Field(default_factory=list)
    forbidden_textual_moves: list[str] = Field(default_factory=list)
    revision_priorities: list[str] = Field(default_factory=list)


class LiteraryQualityScores(BaseModel):
    specificity: float = Field(default=0.0, ge=0.0, le=10.0)
    rhythm_control: float = Field(default=0.0, ge=0.0, le=10.0)
    prose_identity: float = Field(default=0.0, ge=0.0, le=10.0)
    style_alignment: float = Field(default=0.0, ge=0.0, le=10.0)
    cultural_texture: float = Field(default=0.0, ge=0.0, le=10.0)
    emotional_evidence: float = Field(default=0.0, ge=0.0, le=10.0)
    exposition_balance: float = Field(default=0.0, ge=0.0, le=10.0)
    dialogue_pressure: float = Field(default=0.0, ge=0.0, le=10.0)
    mode_fit: float = Field(default=0.0, ge=0.0, le=10.0)


class LiteraryQualityReport(BaseModel):
    schema_version: int = 1
    status: str = "ok"
    writing_mode_id: str = "general"
    scores: LiteraryQualityScores = Field(default_factory=LiteraryQualityScores)
    advisories: list[dict] = Field(default_factory=list)
    metrics: dict = Field(default_factory=dict)
    summary: str = ""
    degraded: bool = False
    error: str = ""
