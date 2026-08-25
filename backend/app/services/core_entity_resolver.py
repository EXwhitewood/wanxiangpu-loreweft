"""Deterministic ID/name/alias resolution for Core entities.

Core cards are JSON documents rather than relational rows.  Every generation
path must therefore share the same matching rules; otherwise a scene beat that
contains a display name silently misses a card whose API expects an UUID.
"""
from __future__ import annotations

import re
import unicodedata
import json
from dataclasses import dataclass
from typing import Any, Iterable


_IGNORED_NAME_CHARS = re.compile(r"[\s\-—–_·•,，。.!！?？:：;；'\"“”‘’()（）\[\]【】]+")


def normalize_entity_key(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    return _IGNORED_NAME_CHARS.sub("", text)


@dataclass(frozen=True)
class EntityResolution:
    card: dict
    entity_type: str
    matched_by: str
    query: str
    canonical_name: str

    def to_dict(self) -> dict:
        return {
            "card": self.card,
            "entity_type": self.entity_type,
            "matched_by": self.matched_by,
            "query": self.query,
            "canonical_name": self.canonical_name,
        }


class CoreEntityResolver:
    """Resolve a Core card without fuzzy guessing.

    Matching precedence is exact ID, normalized canonical name, then normalized
    alias.  Ambiguous matches are deterministic but marked as ambiguous so
    callers can surface the condition instead of silently inventing identity.
    """

    @staticmethod
    def resolve_cards(
        cards: Iterable[dict] | None,
        query: Any,
        *,
        entity_type: str,
    ) -> EntityResolution | None:
        raw_query = str(query or "").strip()
        if not raw_query:
            return None
        candidates = [dict(card) for card in (cards or []) if isinstance(card, dict)]

        id_matches = [card for card in candidates if str(card.get("id") or "") == raw_query]
        if id_matches:
            card = sorted(id_matches, key=CoreEntityResolver._stable_key)[0]
            return CoreEntityResolver._result(card, entity_type, "id", raw_query)

        key = normalize_entity_key(raw_query)
        if not key:
            return None
        name_matches = [card for card in candidates if normalize_entity_key(card.get("name")) == key]
        if name_matches:
            card = sorted(name_matches, key=CoreEntityResolver._stable_key)[0]
            matched_by = "name" if len(name_matches) == 1 else "ambiguous_name"
            return CoreEntityResolver._result(card, entity_type, matched_by, raw_query)

        alias_matches = []
        for card in candidates:
            aliases = card.get("aliases") or []
            if not isinstance(aliases, list):
                aliases = [aliases]
            if any(normalize_entity_key(alias) == key for alias in aliases):
                alias_matches.append(card)
        if alias_matches:
            card = sorted(alias_matches, key=CoreEntityResolver._stable_key)[0]
            matched_by = "alias" if len(alias_matches) == 1 else "ambiguous_alias"
            return CoreEntityResolver._result(card, entity_type, matched_by, raw_query)
        return None

    @staticmethod
    def resolve_core_data(
        core_data: dict | None,
        query: Any,
        *,
        expected_type: str | None = None,
    ) -> EntityResolution | None:
        core = core_data if isinstance(core_data, dict) else {}
        groups = {
            "character": core.get("characters") or [],
            "location": core.get("locations") or [],
            "item": core.get("items") or [],
        }
        order = [expected_type] if expected_type in groups else ["character", "location", "item"]
        for entity_type in order:
            result = CoreEntityResolver.resolve_cards(groups[entity_type], query, entity_type=entity_type)
            if result is not None:
                return result
        return None

    @staticmethod
    def _stable_key(card: dict) -> tuple[str, str]:
        return (normalize_entity_key(card.get("name")), str(card.get("id") or ""))

    @staticmethod
    def _result(card: dict, entity_type: str, matched_by: str, query: str) -> EntityResolution:
        return EntityResolution(
            card=card,
            entity_type=entity_type,
            matched_by=matched_by,
            query=query,
            canonical_name=str(card.get("name") or ""),
        )


def select_relevant_character_cards(
    cards: Iterable[dict] | None,
    context: dict | None,
    *,
    limit: int = 8,
) -> list[dict]:
    """Select named scene participants; never fall back to the entire cast."""
    candidates = [dict(card) for card in (cards or []) if isinstance(card, dict)]
    context_text = json.dumps(context or {}, ensure_ascii=False, default=str)
    context_key = normalize_entity_key(context_text)
    ranked = []
    for card in candidates:
        names = [card.get("name"), *(card.get("aliases") or [])]
        matched = any(
            key and key in context_key
            for key in (normalize_entity_key(name) for name in names)
        )
        importance = str(card.get("role_importance") or "")
        activity = str(card.get("narrative_activity") or "")
        score = 0
        reasons = []
        if matched:
            score += 100
            reasons.append("scene_name_or_alias")
        if importance == "protagonist":
            score += 20
            reasons.append("protagonist")
        elif importance == "main":
            score += 10
        if activity == "core_active":
            score += 8
        elif activity == "scene_active":
            score += 15
        if matched:
            ranked.append((score, normalize_entity_key(card.get("name")), str(card.get("id") or ""), card))
    if not ranked:
        for card in candidates:
            if card.get("role_importance") == "protagonist" or card.get("narrative_activity") == "core_active":
                score = 20 if card.get("role_importance") == "protagonist" else 8
                ranked.append((score, normalize_entity_key(card.get("name")), str(card.get("id") or ""), card))
    ranked.sort(key=lambda item: (-item[0], item[1], item[2]))
    return [item[3] for item in ranked[:limit]]
