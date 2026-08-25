import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


CharacterLifecycleStatus = Literal["alive", "deceased", "missing", "transformed", "unknown"]
CharacterNarrativeActivity = Literal["core_active", "scene_active", "dormant", "archived"]
CharacterRoleImportance = Literal["protagonist", "main", "supporting", "episodic", "background"]


class CharacterBase(BaseModel):
    name: str
    aliases: list[str] = Field(default_factory=list)
    description: str = ""
    appearance: str = ""
    personality: str = ""
    desire: str = ""
    deep_need: str = ""
    arc: str = ""
    relationships: dict[str, str] = Field(default_factory=dict)
    voice_profile: dict | None = None
    # `role` and `status` are retained for old clients and stored cards.  They
    # are compatibility fields, not the canonical routing fields.
    role: str = ""
    faction: str = ""
    status: str = "unknown"
    lifecycle_status: CharacterLifecycleStatus = "unknown"
    narrative_activity: CharacterNarrativeActivity = "dormant"
    role_importance: CharacterRoleImportance = "supporting"
    first_seen_chapter: int | None = Field(default=None, ge=1)
    last_seen_chapter: int | None = Field(default=None, ge=1)
    last_mentioned_chapter: int | None = Field(default=None, ge=1)
    next_planned_chapter: int | None = Field(default=None, ge=1)
    active_arc_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _normalize_legacy_lifecycle_fields(cls, value):
        if not isinstance(value, dict):
            return value
        data = dict(value)
        legacy_status = str(data.get("status") or "").strip().lower()
        lifecycle = str(data.get("lifecycle_status") or "").strip().lower()
        legacy_map = {
            "alive": "alive",
            "active": "alive",
            "deceased": "deceased",
            "dead": "deceased",
            "missing": "missing",
            "transformed": "transformed",
            "unknown": "unknown",
        }
        if lifecycle not in {"alive", "deceased", "missing", "transformed", "unknown"}:
            lifecycle = legacy_map.get(legacy_status, "unknown")
        data["lifecycle_status"] = lifecycle
        data["status"] = legacy_map.get(legacy_status, lifecycle)

        importance = str(data.get("role_importance") or "").strip().lower()
        legacy_role = str(data.get("role") or "").strip().lower()
        if importance not in {"protagonist", "main", "supporting", "episodic", "background"}:
            if legacy_role in {"protagonist", "主角", "男主", "女主"}:
                importance = "protagonist"
            elif legacy_role in {"main", "主要角色", "重要角色"}:
                importance = "main"
            elif legacy_role in {"background", "背景角色"}:
                importance = "background"
            elif legacy_role in {"episodic", "单章角色", "路人"}:
                importance = "episodic"
            else:
                importance = "supporting"
        data["role_importance"] = importance

        activity = str(data.get("narrative_activity") or "").strip().lower()
        if activity not in {"core_active", "scene_active", "dormant", "archived"}:
            activity = "core_active" if importance in {"protagonist", "main"} else "dormant"
        data["narrative_activity"] = activity
        return data


class CharacterCreate(CharacterBase):
    pass


class CharacterResponse(CharacterBase):
    id: uuid.UUID
    project_id: uuid.UUID
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
