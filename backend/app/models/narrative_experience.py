from __future__ import annotations

from pydantic import BaseModel, Field


class NarrativeExperienceContract(BaseModel):
    schema_version: int = 1
    project_id: str = ""
    chapter_number: int = 0
    scene_index: int = 0
    writing_mode_id: str = "general"

    scene_role: str = ""
    reader_promise: str = ""
    target_reader_state_start: str = ""
    target_reader_state_end: str = ""

    primary_emotion: str = ""
    secondary_emotion: str = ""
    emotional_shift: str = ""

    protagonist_desire: str = ""
    obstacle: str = ""
    pressure_source: str = ""
    stakes: str = ""
    decision_point: str = ""
    agency_requirement: str = ""

    curiosity_question: str = ""
    information_delta: list[str] = Field(default_factory=list)
    withheld_information: list[str] = Field(default_factory=list)
    reveal_policy: dict = Field(default_factory=dict)
    misdirection_policy: dict = Field(default_factory=dict)

    scene_embodiment: dict = Field(default_factory=dict)
    sensory_anchor: list[str] = Field(default_factory=list)
    social_interaction_requirement: str = ""
    dialogue_pressure: str = ""

    pacing_shape: str = ""
    hook_out: str = ""
    prohibited_experience: list[str] = Field(default_factory=list)
    success_metrics: dict = Field(default_factory=dict)


class NarrativeExperienceScores(BaseModel):
    reading_drive: float = Field(default=0.0, ge=0.0, le=10.0)
    clarity: float = Field(default=0.0, ge=0.0, le=10.0)
    dramatic_pressure: float = Field(default=0.0, ge=0.0, le=10.0)
    protagonist_agency: float = Field(default=0.0, ge=0.0, le=10.0)
    conflict_visibility: float = Field(default=0.0, ge=0.0, le=10.0)
    information_design: float = Field(default=0.0, ge=0.0, le=10.0)
    curiosity_gap: float = Field(default=0.0, ge=0.0, le=10.0)
    emotional_engagement: float = Field(default=0.0, ge=0.0, le=10.0)
    scene_embodiment: float = Field(default=0.0, ge=0.0, le=10.0)
    dialogue_vitality: float = Field(default=0.0, ge=0.0, le=10.0)


class NarrativeExperienceReport(BaseModel):
    schema_version: int = 1
    status: str = "ok"
    writing_mode_id: str = "general"
    scores: NarrativeExperienceScores = Field(default_factory=NarrativeExperienceScores)
    advisories: list[dict] = Field(default_factory=list)
    metrics: dict = Field(default_factory=dict)
    summary: str = ""
    degraded: bool = False
    error: str = ""
