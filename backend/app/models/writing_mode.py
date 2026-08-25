from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


WritingModeId = Literal[
    "general",
    "commercial_web",
    "literary",
    "mystery",
    "emotional",
    "light_comedy",
    "experimental",
]


class WritingModeProfile(BaseModel):
    """Project-independent writing-mode profile.

    A profile describes how to weigh narrative experience and prose quality for
    a mode. It must never contain project characters, props, locations, or plot
    facts; those belong in the project context and scene contract.
    """

    schema_version: int = 1
    id: str = "general"
    label: str = "通用"
    description: str = ""
    target_reader: str = "中文小说读者"
    metric_weights: dict[str, float] = Field(default_factory=dict)
    experience_defaults: dict = Field(default_factory=dict)
    literary_quality_defaults: dict = Field(default_factory=dict)
    style_interaction: dict = Field(default_factory=dict)
    revision_policy: dict = Field(default_factory=dict)
    prompt_guidance: list[str] = Field(default_factory=list)

    def weight(self, metric: str, default: float = 1.0) -> float:
        try:
            return float(self.metric_weights.get(metric, default))
        except (TypeError, ValueError):
            return default


class ModeFitReport(BaseModel):
    schema_version: int = 1
    writing_mode_id: str = "general"
    fit_score: float = Field(default=0.0, ge=0.0, le=10.0)
    weighted_score: float = Field(default=0.0, ge=0.0, le=10.0)
    mismatches: list[dict] = Field(default_factory=list)
    metric_scores: dict[str, float] = Field(default_factory=dict)
