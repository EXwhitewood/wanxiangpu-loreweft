"""Local reducer for model-extracted chapter state deltas.

LLMs may propose state changes, but only this reducer is allowed to normalize
them into a StoryState patch that can be committed by the existing outbox path.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.models.state_delta import ChapterStateDelta
from app.services.state_manager import normalize_state_patch


class StateDeltaReductionError(ValueError):
    pass


class StateDeltaReducer:
    def reduce(self, current_state: dict[str, Any], delta: ChapterStateDelta) -> dict[str, Any]:
        if delta.chapter_number < 0 or delta.scene_index < 0:
            raise StateDeltaReductionError("chapter_number and scene_index must be non-negative")

        patch: dict[str, Any] = deepcopy(delta.raw_patch or {})
        objective_state = dict(patch.get("objective_state") or {})

        current_entities = current_state.get("objective_state") or {}
        for entity_delta in delta.entity_deltas:
            name = entity_delta.entity_name.strip()
            if not name:
                continue
            if entity_delta.delta_type == "remove":
                objective_state[name] = {"extra": {"removed": True, "evidence_span": entity_delta.evidence_span}}
                continue
            if entity_delta.delta_type == "update" and name not in current_entities and name not in objective_state:
                entity_delta.delta_type = "add"
            merged = dict(objective_state.get(name) or {})
            for key, value in entity_delta.attribute_changes.items():
                if key in {"location", "emotional_state", "physical_state", "inventory", "alive", "extra"}:
                    merged[key] = value
                else:
                    extra = dict(merged.get("extra") or {})
                    extra[key] = value
                    merged["extra"] = extra
            if entity_delta.evidence_span:
                extra = dict(merged.get("extra") or {})
                extra.setdefault("evidence_spans", []).append(entity_delta.evidence_span)
                merged["extra"] = extra
            objective_state[name] = merged

        if objective_state:
            patch["objective_state"] = objective_state

        if delta.narrative_time:
            patch["narrative_time"] = delta.narrative_time

        if delta.completed_events:
            existing = list(patch.get("completed_events") or [])
            for event in delta.completed_events:
                if event and event not in existing:
                    existing.append(event)
            patch["completed_events"] = existing

        if delta.active_constraints_add or delta.active_constraints_remove:
            existing_constraints = list(current_state.get("active_constraints") or [])
            remove_keys = {str(x) for x in delta.active_constraints_remove if x}
            filtered = [
                item for item in existing_constraints
                if str(item.get("description") if isinstance(item, dict) else item) not in remove_keys
            ]
            filtered.extend(delta.active_constraints_add)
            patch["active_constraints"] = filtered

        normalized = normalize_state_patch(patch)
        return normalized

    def from_legacy_patch(
        self,
        patch: dict[str, Any],
        *,
        project_id: str = "",
        chapter_number: int = 0,
        scene_index: int = 0,
    ) -> ChapterStateDelta:
        return ChapterStateDelta(
            project_id=project_id,
            chapter_number=chapter_number,
            scene_index=scene_index,
            raw_patch=patch or {},
        )

