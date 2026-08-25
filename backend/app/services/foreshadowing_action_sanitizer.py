from __future__ import annotations

from typing import Any


_DROP_KEYS = {
    "legacy_payload",
    "actions",
    "source_actions",
    "raw_actions",
}

_TOP_LEVEL_KEYS = {
    "name",
    "thread_id",
    "foreshadowing_id",
    "core_entity_id",
    "clue_id",
    "action",
    "operation",
    "op",
    "mode",
    "detail",
    "description",
    "summary",
    "evidence_text",
    "scene_index",
    "pov_character",
    "salience",
    "aliases",
    "priority",
    "status",
    "truth_type",
    "impact_level",
    "spoiler_scope",
    "secret",
    "timeline",
    "narrative_structure",
}

_MAX_TEXT_LEN = 1000


def sanitize_foreshadowing_action(action: dict[str, Any]) -> dict[str, Any]:
    """Return a compact outline-safe foreshadowing action.

    Legacy migrations may carry nested legacy_payload/actions structures. Keeping
    them in chapter outlines can recursively multiply the stored prompt context.
    """
    if not isinstance(action, dict):
        return {}

    cleaned: dict[str, Any] = {}
    for key in _TOP_LEVEL_KEYS:
        if key not in action or key in _DROP_KEYS:
            continue
        value = action.get(key)
        if value is None or value == "" or value == [] or value == {}:
            continue
        cleaned[key] = _sanitize_value(key, value)

    name = str(cleaned.get("name") or cleaned.get("thread_id") or "").strip()
    if not name:
        return {}
    cleaned["name"] = _clip_text(name)
    cleaned.pop("thread_id", None)

    if "action" not in cleaned:
        op = cleaned.get("operation") or cleaned.get("op")
        if op:
            cleaned["action"] = op
    cleaned.pop("operation", None)
    cleaned.pop("op", None)

    if "description" not in cleaned:
        secret = cleaned.get("secret")
        if isinstance(secret, dict) and secret.get("canonical_statement"):
            cleaned["description"] = _clip_text(str(secret["canonical_statement"]))
        elif action.get("description") or action.get("summary"):
            cleaned["description"] = _clip_text(str(action.get("description") or action.get("summary")))
        else:
            cleaned["description"] = cleaned["name"]

    return cleaned


def sanitize_foreshadowing_actions(actions: Any) -> list[dict[str, Any]]:
    if not isinstance(actions, list):
        return []

    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for action in actions:
        cleaned = sanitize_foreshadowing_action(action)
        if not cleaned:
            continue
        key = (cleaned.get("name", ""), cleaned.get("action", ""))
        if key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return result


def _sanitize_value(key: str, value: Any) -> Any:
    if isinstance(value, str):
        return _clip_text(value)
    if isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, list):
        if key in {"timeline", "secret", "narrative_structure", "spoiler_scope"}:
            return value[:20]
        return [_sanitize_value("", item) for item in value[:20]]
    if isinstance(value, dict):
        cleaned = {}
        for sub_key, sub_value in value.items():
            if sub_key in _DROP_KEYS:
                continue
            cleaned[sub_key] = _sanitize_value(sub_key, sub_value)
        return cleaned
    return _clip_text(str(value))


def _clip_text(text: str) -> str:
    text = text.strip()
    if len(text) <= _MAX_TEXT_LEN:
        return text
    return text[:_MAX_TEXT_LEN].rstrip() + "..."
