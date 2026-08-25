import copy
import json
import re
import uuid
from typing import Any

from app.models.story_state import StoryState, SubjectiveView
from app.services.sqlite_state_manager import SqliteStateManager


_STATE_PREFIX = "loreweft:state"
_CHECKPOINT_PREFIX = "loreweft:checkpoint"
_TIMELINE_PREFIX = "loreweft:timeline"
_TIMELINES_PREFIX = "loreweft:timelines"

_ENTITY_FIELD_ALIASES = {
    "location": "location",
    "current_location": "location",
    "place": "location",
    "位置": "location",
    "地点": "location",
    "所在地": "location",
    "emotional_state": "emotional_state",
    "emotion": "emotional_state",
    "mood": "emotional_state",
    "情绪": "emotional_state",
    "心理状态": "emotional_state",
    "physical_state": "physical_state",
    "physical": "physical_state",
    "body_state": "physical_state",
    "身体状态": "physical_state",
    "生理状态": "physical_state",
    "inventory": "inventory",
    "items": "inventory",
    "持有物品": "inventory",
    "物品": "inventory",
    "alive": "alive",
    "存活": "alive",
    "extra": "extra",
}
_OBJECTIVE_STATE_WRAPPERS = ("entities", "characters", "人物", "角色")
_LOCATION_STATE_FIELDS = {"atmosphere", "light_source", "objects_present", "condition"}


def _stringify_state_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple, set)):
        return "、".join(str(item) for item in value)
    if isinstance(value, dict):
        for key in ("name", "value", "description", "text"):
            if value.get(key):
                return str(value[key])
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _normalize_inventory(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, (tuple, set)):
        return [str(item) for item in value]
    return [str(value)]


def _normalize_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no", "否", "死亡"}
    return bool(value)


def _normalize_index(value: Any, prefixes: tuple[str, ...]) -> Any:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if not isinstance(value, str):
        return value
    prefix_pattern = "|".join(re.escape(prefix) for prefix in prefixes)
    match = re.fullmatch(
        rf"\s*(?:(?:{prefix_pattern})[\s_-]*)?(\d+)\s*",
        value,
        re.IGNORECASE,
    )
    return int(match.group(1)) if match else value


def _normalize_patch_index(value: Any, prefixes: tuple[str, ...]) -> int | None:
    normalized = _normalize_index(value, prefixes)
    if isinstance(normalized, int) and not isinstance(normalized, bool):
        return normalized
    return None


def _normalize_location_patch(value: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(value)
    for key in ("atmosphere", "light_source", "condition"):
        if key in normalized:
            normalized[key] = _stringify_state_value(normalized[key])
    if "objects_present" in normalized:
        normalized["objects_present"] = _normalize_inventory(normalized["objects_present"])
    return normalized


def _normalize_entity_patch(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"extra": {"summary": _stringify_state_value(value)}}

    normalized: dict[str, Any] = {}
    extra = dict(value.get("extra", {})) if isinstance(value.get("extra"), dict) else {}
    for key, item in value.items():
        canonical_key = _ENTITY_FIELD_ALIASES.get(key)
        if canonical_key == "extra":
            continue
        if canonical_key in {"location", "emotional_state", "physical_state"}:
            normalized[canonical_key] = _stringify_state_value(item)
        elif canonical_key == "inventory":
            normalized[canonical_key] = _normalize_inventory(item)
        elif canonical_key == "alive":
            normalized[canonical_key] = _normalize_bool(item)
        else:
            extra[key] = item
    if extra:
        normalized["extra"] = extra
    return normalized


def _normalize_subjective_view(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"believed_state": {"summary": value}}

    believed_state = value.get("believed_state", {})
    last_known = value.get("last_known", {})
    if not isinstance(believed_state, dict):
        believed_state = {"summary": believed_state}
    if not isinstance(last_known, dict):
        last_known = {"summary": last_known}
    for key, item in value.items():
        if key not in {"believed_state", "last_known"}:
            believed_state[key] = item
    return {"believed_state": believed_state, "last_known": last_known}


def normalize_state_patch(patch: dict[str, Any]) -> dict[str, Any]:
    """Coerce model-generated state patches into the stable StoryState schema."""
    normalized = copy.deepcopy(patch)
    objective_state = normalized.get("objective_state")
    if isinstance(objective_state, dict):
        for wrapper in _OBJECTIVE_STATE_WRAPPERS:
            wrapped = objective_state.get(wrapper)
            if isinstance(wrapped, dict):
                objective_state = {
                    **{key: value for key, value in objective_state.items() if key != wrapper},
                    **wrapped,
                }

        normalized_objective_state: dict[str, Any] = {}
        for entity_id, value in objective_state.items():
            if isinstance(value, dict) and value and set(value).issubset(_LOCATION_STATE_FIELDS):
                normalized_objective_state[entity_id] = _normalize_location_patch(value)
            else:
                normalized_objective_state[entity_id] = _normalize_entity_patch(value)
        normalized["objective_state"] = normalized_objective_state

    subjective_views = normalized.get("subjective_views")
    if isinstance(subjective_views, dict):
        normalized["subjective_views"] = {
            character_id: _normalize_subjective_view(value)
            for character_id, value in subjective_views.items()
        }

    if isinstance(normalized.get("narrative_time"), dict):
        normalized["narrative_time"] = _stringify_state_value(normalized["narrative_time"])
    if "active_chapter" in normalized:
        active_chapter = _normalize_patch_index(
            normalized["active_chapter"], ("chapter", "ch", "c"),
        )
        if active_chapter is None:
            normalized.pop("active_chapter", None)
        else:
            normalized["active_chapter"] = active_chapter
    if "active_scene" in normalized:
        active_scene = _normalize_patch_index(
            normalized["active_scene"], ("scene", "s"),
        )
        if active_scene is None:
            normalized.pop("active_scene", None)
        else:
            normalized["active_scene"] = active_scene
    if isinstance(normalized.get("completed_events"), str):
        normalized["completed_events"] = [normalized["completed_events"]]
    elif isinstance(normalized.get("completed_events"), list):
        events = []
        for event in normalized["completed_events"]:
            text = _stringify_state_value(event)
            if text:
                events.append(text)
        normalized["completed_events"] = events
    if isinstance(normalized.get("active_constraints"), dict):
        normalized["active_constraints"] = [normalized["active_constraints"]]
    elif isinstance(normalized.get("active_constraints"), str):
        normalized["active_constraints"] = [{"description": normalized["active_constraints"]}]
    elif isinstance(normalized.get("active_constraints"), list):
        constraints = []
        for constraint in normalized["active_constraints"]:
            if isinstance(constraint, dict):
                constraints.append(constraint)
            else:
                text = _stringify_state_value(constraint)
                if text:
                    constraints.append({"description": text})
        normalized["active_constraints"] = constraints
    return normalized


class StateManager:
    _sqlite_manager = SqliteStateManager()
    _checkpoint_cache: dict[str, dict] = {}
    _timeline_cache: dict[str, dict] = {}
    _timelines_cache: dict[str, list[dict]] = {}

    def _key(self, project_id: str) -> str:
        return f"{_STATE_PREFIX}:{project_id}"

    def _checkpoint_key(self, project_id: str, checkpoint_id: str) -> str:
        return f"{_CHECKPOINT_PREFIX}:{project_id}:{checkpoint_id}"

    async def get_state(self, project_id: str) -> StoryState:
        data = await self._sqlite_manager.get_state(project_id)
        if not data:
            return StoryState()
        return StoryState(**data)

    async def set_state(self, project_id: str, state: StoryState) -> None:
        await self._sqlite_manager.set_state(project_id, state.model_dump())

    async def get_snapshot(self, project_id: str) -> StoryState:
        state = await self.get_state(project_id)
        return copy.deepcopy(state)

    async def apply_patch(self, project_id: str, patch: dict) -> StoryState:
        state = await self.get_state(project_id)
        state_dict = state.model_dump()
        patch = normalize_state_patch(patch)

        new_events = patch.pop("completed_events", None)
        if new_events and isinstance(new_events, list):
            existing_events = set(state_dict.get("completed_events", []))
            for evt in new_events:
                if evt not in existing_events:
                    state_dict.setdefault("completed_events", []).append(evt)

        new_constraints = patch.pop("active_constraints", None)
        if new_constraints and isinstance(new_constraints, list):
            existing_constraints = state_dict.get("active_constraints", [])
            existing_keys = {c.get("description") for c in existing_constraints if isinstance(c, dict)}
            for c in new_constraints:
                if isinstance(c, dict):
                    desc = c.get("description", "")
                    if desc and desc not in existing_keys:
                        existing_constraints.append(c)
                        existing_keys.add(desc)
            state_dict["active_constraints"] = existing_constraints

        self._deep_merge(state_dict, patch)
        state = StoryState(**state_dict)
        await self.set_state(project_id, state)
        return state

    async def update_subjective_view(
        self, project_id: str, character_id: str, view: SubjectiveView
    ) -> None:
        state = await self.get_state(project_id)
        state.subjective_views[character_id] = view
        await self.set_state(project_id, state)

    async def create_checkpoint(self, project_id: str) -> str:
        state = await self.get_state(project_id)
        checkpoint_id = str(uuid.uuid4())
        self._checkpoint_cache[self._checkpoint_key(project_id, checkpoint_id)] = state.model_dump()
        return checkpoint_id

    async def rollback_to_checkpoint(self, project_id: str, checkpoint_id: str) -> None:
        raw = self._checkpoint_cache.get(self._checkpoint_key(project_id, checkpoint_id))
        if raw is None:
            raise ValueError(f"Checkpoint {checkpoint_id} not found")
        await self.set_state(project_id, StoryState(**raw))

    async def create_timeline(
        self,
        project_id: str,
        name: str,
        parent_timeline_id: str | None = None,
        branch_chapter: int | None = None,
    ) -> dict:
        timeline_id = f"timeline_{uuid.uuid4().hex[:8]}"

        if parent_timeline_id and branch_chapter:
            parent_state = await self._load_state_from_timeline(project_id, parent_timeline_id)
            if parent_state:
                state = parent_state.model_dump()
            else:
                state = (await self.get_state(project_id)).model_dump()
        else:
            state = (await self.get_state(project_id)).model_dump()

        state["timeline_id"] = timeline_id
        state["timeline_name"] = name
        state["parent_timeline"] = parent_timeline_id
        state["branch_chapter"] = branch_chapter

        key = f"{_TIMELINE_PREFIX}:{project_id}:{timeline_id}"
        self._timeline_cache[key] = state
        timelines = self._timelines_cache.get(project_id, [])
        timelines = [*timelines, {
            "id": timeline_id,
            "name": name,
            "parent": parent_timeline_id,
            "branch_chapter": branch_chapter,
        }]
        self._timelines_cache[project_id] = timelines

        return {"timeline_id": timeline_id, "name": name}

    async def list_timelines(self, project_id: str) -> list[dict]:
        if project_id in self._timelines_cache:
            return self._timelines_cache[project_id]
        return [{"id": "timeline_main", "name": "主线", "parent": None, "branch_chapter": None}]

    async def switch_timeline(self, project_id: str, timeline_id: str) -> StoryState | None:
        state = await self._load_state_from_timeline(project_id, timeline_id)
        if state:
            state.timeline_id = timeline_id
            await self.set_state(project_id, state)
            return state
        return None

    async def _load_state_from_timeline(self, project_id: str, timeline_id: str) -> StoryState | None:
        key = f"{_TIMELINE_PREFIX}:{project_id}:{timeline_id}"
        raw = self._timeline_cache.get(key)
        if raw:
            return StoryState(**raw)
        return None

    def _deep_merge(self, base: dict, override: dict) -> dict:
        for key, value in override.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._deep_merge(base[key], value)
            else:
                base[key] = value
        return base
