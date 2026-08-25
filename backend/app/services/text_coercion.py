import json
import re
from typing import Any


_COMPLETE_SENTENCE_END_RE = re.compile(r"[。！？!?…」』”）)]$")


def ensure_complete_sentence_ending(text: str) -> str:
    """Make stored generated prose end on a complete sentence boundary."""
    text = (text or "").rstrip()
    if text.endswith("."):
        return text[:-1].rstrip() + "。"
    if not text or _COMPLETE_SENTENCE_END_RE.search(text):
        return text
    return text + "。"


def to_text(value: Any) -> str:
    """Convert flexible plan fields into readable text without losing structure."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list, tuple, set)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


def to_search_text(value: Any) -> str:
    return to_text(value).lower()


def to_entity_name(value: Any) -> str:
    """Extract a stable lookup name from either a legacy string or a structured ref."""
    if isinstance(value, dict):
        for key in ("name", "id", "entity_id", "location", "title", "value", "kind", "category", "type", "label"):
            candidate = value.get(key)
            if candidate not in (None, ""):
                return to_text(candidate)
    if isinstance(value, (list, tuple, set)):
        for item in value:
            candidate = to_entity_name(item)
            if candidate:
                return candidate
        return ""
    return to_text(value)
