"""Structured state-delta models for chapter generation commits."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class EntityDelta(BaseModel):
    entity_name: str = Field(default="")
    delta_type: Literal["add", "update", "remove"] = Field(default="update")
    entity_type: str = Field(default="character")
    attribute_changes: dict[str, Any] = Field(default_factory=dict)
    evidence_span: str = Field(default="")


class ForeshadowingDelta(BaseModel):
    clue_id: str = Field(default="")
    operation: Literal["plant", "advance", "pay_off", "abandon"] = Field(default="advance")
    evidence_span: str = Field(default="")
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChapterStateDelta(BaseModel):
    project_id: str = Field(default="")
    chapter_number: int = Field(default=0)
    scene_index: int = Field(default=0)
    entity_deltas: list[EntityDelta] = Field(default_factory=list)
    foreshadowing_ops: list[ForeshadowingDelta] = Field(default_factory=list)
    completed_events: list[str] = Field(default_factory=list)
    active_constraints_add: list[dict[str, Any]] = Field(default_factory=list)
    active_constraints_remove: list[str] = Field(default_factory=list)
    narrative_time: str | None = None
    raw_patch: dict[str, Any] = Field(default_factory=dict)

