"""Deterministic, lifecycle-aware proposition selection for scene context."""
from __future__ import annotations

from collections import Counter
from typing import Any, Iterable

from app.services.core_entity_resolver import normalize_entity_key


_CURRENT_CATEGORIES = {"state", "location", "ownership", "relationship", "knowledge"}
_INACTIVE_LIFECYCLES = {"superseded", "retracted"}


def infer_proposition_lifecycle(item: dict) -> str:
    explicit = str(item.get("lifecycle_status") or "").strip()
    if explicit in {"current", "superseded", "historical_event", "unresolved", "planned", "retracted"}:
        return explicit
    category = str(item.get("predicate_category") or "")
    truth_layer = str(item.get("truth_layer") or "")
    if category == "clue":
        return "unresolved"
    if truth_layer in {"plan", "future_hint"} or category == "intention":
        return "planned"
    if category in {"action", "event"}:
        return "historical_event"
    return "current"


def proposition_fingerprint(item: dict) -> tuple[str, ...]:
    return (
        normalize_entity_key(item.get("subject_id") or item.get("subject_name")),
        normalize_entity_key(item.get("predicate_name")),
        str(item.get("predicate_category") or ""),
        normalize_entity_key(item.get("object_id") or item.get("object_name")),
        str(item.get("truth_layer") or ""),
        str(item.get("polarity") or "affirmed"),
    )


def _collect_terms(value: Any, output: set[str]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key not in {"source_text", "content", "generated_text", "previous_text"}:
                _collect_terms(child, output)
    elif isinstance(value, (list, tuple, set)):
        for child in value:
            _collect_terms(child, output)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return
        normalized = normalize_entity_key(text)
        if 1 < len(normalized) <= 40:
            output.add(normalized)


class PropositionSelector:
    DEFAULT_QUOTAS = {
        "current_hard": 10,
        "unresolved": 5,
        "continuity": 5,
        "historical": 4,
    }

    def select(
        self,
        candidates: Iterable[dict] | None,
        *,
        chapter_number: int,
        scene_context: dict | None = None,
        max_items: int = 24,
        quotas: dict[str, int] | None = None,
    ) -> dict:
        candidates = [dict(item) for item in (candidates or []) if isinstance(item, dict)]
        terms: set[str] = set()
        _collect_terms(scene_context or {}, terms)
        eligible: list[dict] = []
        excluded_lifecycle = 0
        excluded_validity = 0
        deduplicated = 0

        by_fingerprint: dict[tuple[str, ...], dict] = {}
        for item in candidates:
            lifecycle = infer_proposition_lifecycle(item)
            item["lifecycle_status"] = lifecycle
            if str(item.get("status") or "active") != "active" or lifecycle in _INACTIVE_LIFECYCLES:
                excluded_lifecycle += 1
                continue
            valid_from = _int(item.get("valid_from_chapter"))
            valid_to = _int(item.get("valid_to_chapter"))
            if (valid_from and chapter_number < valid_from) or (valid_to and chapter_number > valid_to):
                excluded_validity += 1
                continue
            key = proposition_fingerprint(item)
            previous = by_fingerprint.get(key)
            if previous is None or self._newer_key(item) > self._newer_key(previous):
                if previous is not None:
                    deduplicated += 1
                by_fingerprint[key] = item
            else:
                deduplicated += 1

        for item in by_fingerprint.values():
            score, reasons, bucket = self._score(item, chapter_number, terms)
            ranked = dict(item)
            ranked["_selection_score"] = round(score, 3)
            ranked["_selection_reasons"] = reasons
            ranked["_selection_bucket"] = bucket
            eligible.append(ranked)

        eligible.sort(key=self._rank_key)
        effective_quotas = dict(self.DEFAULT_QUOTAS)
        effective_quotas.update(quotas or {})
        selected: list[dict] = []
        selected_ids: set[str] = set()
        for bucket in ("current_hard", "unresolved", "continuity", "historical"):
            bucket_items = [item for item in eligible if item["_selection_bucket"] == bucket]
            for item in bucket_items[:max(0, int(effective_quotas.get(bucket, 0)))]:
                marker = self._marker(item)
                if marker not in selected_ids and len(selected) < max_items:
                    selected.append(item)
                    selected_ids.add(marker)
        for item in eligible:
            marker = self._marker(item)
            # Only use unallocated capacity when the configured quotas do not
            # already consume the whole budget.  Explicit scene quotas are a
            # real boundary, not a prelude to filling with the same bucket.
            if sum(max(0, int(value)) for value in effective_quotas.values()) >= max_items:
                break
            if marker not in selected_ids and len(selected) < max_items:
                selected.append(item)
                selected_ids.add(marker)
        selected.sort(key=self._rank_key)

        selected_by_chapter = Counter(str(item.get("chapter_number") or "unknown") for item in selected)
        selected_by_bucket = Counter(item.get("_selection_bucket", "continuity") for item in selected)
        eligible_hard = sum(1 for item in eligible if item["_selection_bucket"] == "current_hard")
        selected_hard = sum(1 for item in selected if item["_selection_bucket"] == "current_hard")
        trace = {
            "candidate_count": len(candidates),
            "eligible_count": len(eligible),
            "selected_count": len(selected),
            "dropped_count": max(0, len(eligible) - len(selected)),
            "excluded_lifecycle_count": excluded_lifecycle,
            "excluded_validity_count": excluded_validity,
            "deduplicated_count": deduplicated,
            "selection_limit": max_items,
            "truncated": len(eligible) > len(selected),
            "hard_fact_dropped": max(0, eligible_hard - selected_hard),
            "selected_by_chapter": dict(selected_by_chapter),
            "selected_by_bucket": dict(selected_by_bucket),
            "selected": [
                {
                    "proposition_id": item.get("proposition_id") or item.get("id"),
                    "chapter_number": item.get("chapter_number"),
                    "score": item.get("_selection_score"),
                    "reasons": item.get("_selection_reasons", []),
                    "bucket": item.get("_selection_bucket"),
                }
                for item in selected
            ],
        }
        return {"selected": selected, "trace": trace}

    def _score(self, item: dict, chapter_number: int, terms: set[str]) -> tuple[float, list[str], str]:
        lifecycle = infer_proposition_lifecycle(item)
        category = str(item.get("predicate_category") or "")
        truth = str(item.get("truth_layer") or "")
        certainty = str(item.get("certainty") or "")
        reasons: list[str] = []
        score = float(item.get("importance") or 0.5) * 10.0
        if lifecycle == "current" and category in _CURRENT_CATEGORIES and truth == "current" and certainty == "confirmed":
            bucket = "current_hard"
            score += 80
            reasons.append("confirmed_current_fact")
        elif lifecycle == "unresolved" or category == "clue":
            bucket = "unresolved"
            score += 55
            reasons.append("unresolved_clue")
        elif lifecycle == "historical_event":
            bucket = "historical"
            score += 15
            reasons.append("historical_event")
        else:
            bucket = "continuity"
            score += 35 if lifecycle == "current" else 25
            reasons.append(lifecycle)

        entity_keys = {
            normalize_entity_key(item.get("subject_name")),
            normalize_entity_key(item.get("subject_id")),
            normalize_entity_key(item.get("object_name")),
            normalize_entity_key(item.get("object_id")),
            normalize_entity_key(item.get("location_scope")),
        }
        entity_keys.discard("")
        exact_matches = sorted(entity_keys & terms)
        if exact_matches:
            score += 45 + min(10, len(exact_matches) * 2)
            reasons.append("scene_entity_match")
        else:
            haystack = normalize_entity_key(" ".join(str(item.get(k) or "") for k in (
                "subject_name", "predicate_name", "object_name", "location_scope", "time_scope",
            )))
            if any(term in haystack for term in terms if len(term) >= 2):
                score += 18
                reasons.append("scene_term_match")

        source_chapter = _int(item.get("chapter_number")) or 0
        distance = max(0, chapter_number - source_chapter)
        recency = max(0.0, 18.0 - min(18, distance * 2.0))
        score += recency
        if distance <= 1:
            reasons.append("adjacent_chapter")
        return score, reasons, bucket

    @staticmethod
    def _newer_key(item: dict) -> tuple[int, int, int, str]:
        return (
            _int(item.get("chapter_number")) or 0,
            _int(item.get("scene_index")) or 0,
            _int(item.get("generation_revision")) or 0,
            str(item.get("created_at") or ""),
        )

    @staticmethod
    def _rank_key(item: dict) -> tuple[float, int, int, str]:
        return (
            -float(item.get("_selection_score") or 0.0),
            -(_int(item.get("chapter_number")) or 0),
            -(_int(item.get("scene_index")) or 0),
            str(item.get("proposition_id") or item.get("id") or ""),
        )

    @staticmethod
    def _marker(item: dict) -> str:
        return str(item.get("proposition_id") or item.get("id") or proposition_fingerprint(item))


def _int(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def get_proposition_selector() -> PropositionSelector:
    return PropositionSelector()
