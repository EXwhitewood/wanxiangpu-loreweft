"""Validation for domain-structured context compaction."""
from __future__ import annotations

import hashlib
import json
from typing import Any


PROTECTED_PRIORITIES = {"P0", "P1"}


def stable_hash(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


class CompactionValidator:
    """Check that compaction did not mutate pinned facts."""

    def validate(self, before: dict, after: dict, compaction_id: str = "") -> dict:
        checks: dict[str, Any] = {}
        warnings: list[str] = []

        before_blocks = {
            str(block.get("block_id")): block
            for block in before.get("blocks", [])
            if isinstance(block, dict)
        }
        after_blocks = {
            str(block.get("block_id")): block
            for block in after.get("blocks", [])
            if isinstance(block, dict)
        }

        protected_ok = True
        for block_id, block in before_blocks.items():
            if block.get("priority") not in PROTECTED_PRIORITIES:
                continue
            after_block = after_blocks.get(block_id)
            if not after_block:
                protected_ok = False
                warnings.append(f"protected block missing: {block_id}")
                continue
            if stable_hash(block.get("content")) != stable_hash(after_block.get("content")):
                protected_ok = False
                warnings.append(f"protected block changed: {block_id}")
        checks["protected_hash_unchanged"] = protected_ok

        scene_ids_before = self._scene_ids(before)
        scene_ids_after = self._scene_ids(after)
        checks["all_scene_ids_present"] = scene_ids_before.issubset(scene_ids_after)
        if not checks["all_scene_ids_present"]:
            warnings.append("scene_id lost after compaction")

        forbidden_before = self._forbidden_rules(before)
        forbidden_after = self._forbidden_rules(after)
        checks["forbidden_reveals_preserved"] = forbidden_before.issubset(forbidden_after)
        if not checks["forbidden_reveals_preserved"]:
            warnings.append("forbidden/do_not_reveal rule lost after compaction")

        source_facts = self._critical_fact_count(after)
        source_refs = self._critical_fact_with_ref_count(after)
        checks["source_coverage_ratio"] = 1.0 if source_facts == 0 else round(source_refs / source_facts, 4)
        checks["new_fact_count"] = 0

        passed = (
            bool(checks["protected_hash_unchanged"])
            and bool(checks["all_scene_ids_present"])
            and bool(checks["forbidden_reveals_preserved"])
            and checks["source_coverage_ratio"] >= 0.8
        )
        return {
            "validation_id": f"ctxv_{compaction_id or stable_hash(after)[:8]}",
            "compaction_id": compaction_id,
            "passed": passed,
            "checks": checks,
            "warnings": warnings,
        }

    def _scene_ids(self, envelope: dict) -> set[str]:
        ids: set[str] = set()
        for block in envelope.get("blocks") or []:
            if not isinstance(block, dict):
                continue
            content = block.get("content")
            candidates = []
            if isinstance(content, dict):
                candidates.extend(content.get("scenes") or [])
                candidates.extend(content.get("scene_map") or [])
            elif isinstance(content, list):
                candidates.extend(content)
            for item in candidates:
                if isinstance(item, dict) and item.get("scene_id"):
                    ids.add(str(item["scene_id"]))
        return ids

    def _forbidden_rules(self, envelope: dict) -> set[str]:
        rules: set[str] = set()

        def walk(value: Any, key_hint: str = "") -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    walk(child, str(key))
            elif isinstance(value, list):
                for child in value:
                    walk(child, key_hint)
            elif value and key_hint in {
                "forbidden", "forbidden_outline", "do_not_reveal_yet",
                "must_not_resolve", "must_preserve_for_later",
            }:
                rules.add(str(value).strip())

        for block in envelope.get("blocks") or []:
            if isinstance(block, dict):
                walk(block.get("content"))
        return {rule for rule in rules if rule}

    def _critical_fact_count(self, envelope: dict) -> int:
        total = 0
        for block in envelope.get("blocks") or []:
            if not isinstance(block, dict):
                continue
            content = block.get("content")
            if isinstance(content, dict):
                for key in ("plot_beats", "character_states", "object_states", "unresolved_foreshadowing"):
                    values = content.get(key)
                    if isinstance(values, list):
                        total += len(values)
        return total

    def _critical_fact_with_ref_count(self, envelope: dict) -> int:
        total = 0
        for block in envelope.get("blocks") or []:
            if not isinstance(block, dict):
                continue
            content = block.get("content")
            if isinstance(content, dict):
                for key in ("plot_beats", "character_states", "object_states", "unresolved_foreshadowing"):
                    values = content.get(key)
                    if isinstance(values, list):
                        total += sum(1 for item in values if isinstance(item, dict) and item.get("source_ref"))
        return total
