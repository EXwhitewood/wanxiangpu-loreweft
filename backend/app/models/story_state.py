from typing import Any

from pydantic import BaseModel


class EntityState(BaseModel):
    location: str | None = None
    emotional_state: str | None = None
    physical_state: str | None = None
    inventory: list[str] = []
    alive: bool = True
    extra: dict[str, Any] = {}


class LocationState(BaseModel):
    atmosphere: str | None = None
    light_source: str | None = None
    objects_present: list[str] = []
    condition: str | None = None


class SubjectiveView(BaseModel):
    believed_state: dict[str, Any] = {}
    last_known: dict[str, Any] = {}


class StoryState(BaseModel):
    timeline_id: str = "timeline_main"
    narrative_time: str | None = None
    active_chapter: int = 1
    active_scene: int = 1
    pov_character: str | None = None
    objective_state: dict[str, EntityState | LocationState] = {}
    subjective_views: dict[str, SubjectiveView] = {}
    completed_events: list[str] = []
    active_constraints: list[dict[str, Any]] = []
