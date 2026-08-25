"""Deterministic temporal-layer review helpers."""

from __future__ import annotations

import re
from typing import Any


_STRONG_FLASHBACK_PATTERNS = (
    r"那是.{0,16}?前的事",
    r"那是.{0,16}?天前",
    r"回忆起",
    r"想起了",
    r"那时候",
    r"当时她",
    r"当时他",
)

_WEAK_MEMORY_PATTERNS = (
    r"记忆中",
    r"前世记忆",
    r"上辈子",
    r"而(现在|此刻|如今)",
    r"回到(眼前|现实|此刻)",
)

_MEMORY_REQUIRED_TERMS = (
    "前世记忆",
    "前世",
    "上辈子",
    "前生记忆",
    "旧世记忆",
)


def detect_temporal_layer_confusion(text: str, contract: dict[str, Any]) -> dict[str, Any] | None:
    """Return a temporal-layer finding when flashback markers imply a real conflict.

    Some scenes explicitly require memory-assisted action, such as using former-life
    memory to identify herbs. In those scenes, weak memory mentions are contractual
    material rather than flashback drift. Strong historical-entry markers still
    count because they can indicate an actual scene-time break.
    """

    if not text or not contract.get("opening_state"):
        return None

    strong_matches = _pattern_matches(_STRONG_FLASHBACK_PATTERNS, text)
    weak_matches = _pattern_matches(_WEAK_MEMORY_PATTERNS, text)
    memory_required = any(term in _contract_text(contract) for term in _MEMORY_REQUIRED_TERMS)
    blocking_count = len(strong_matches) + (0 if memory_required else len(weak_matches))

    if blocking_count < 2:
        return None

    matches = strong_matches + ([] if memory_required else weak_matches)
    return {
        "count": len(strong_matches) + len(weak_matches),
        "blocking_count": blocking_count,
        "memory_required": memory_required,
        "target_span": "；".join(match[:40] for match in matches[:5]),
    }


def _pattern_matches(patterns: tuple[str, ...], text: str) -> list[str]:
    matches: list[str] = []
    for pattern in patterns:
        matches.extend(match.group(0) for match in re.finditer(pattern, text))
    return matches


def _contract_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(_contract_text(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return " ".join(_contract_text(item) for item in value)
    return str(value)
