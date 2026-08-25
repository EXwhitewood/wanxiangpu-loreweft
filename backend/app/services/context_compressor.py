"""Domain-structured context compaction for the editor system."""
from __future__ import annotations

import copy
import datetime as _dt
import hashlib
import json
from typing import Any

from app.services.compaction_validator import CompactionValidator, stable_hash
from app.services.context_budget_service import get_context_budget_service


class ContextCompressor:
    """Compress low-priority context blocks while preserving story invariants."""

    def __init__(self):
        self.budget = get_context_budget_service()
        self.validator = CompactionValidator()

    def compact(
        self,
        envelope: dict,
        *,
        manual_focus: str | None = None,
        dry_run: bool = False,
    ) -> dict:
        before = self.budget.attach_token_estimates(copy.deepcopy(envelope))
        profile = self.budget.resolve_profile(before.get("model_context_profile") or before.get("budget"))
        before_tokens = int(before.get("token_report", {}).get("estimated_input_tokens", 0))

        after = copy.deepcopy(before)
        after.setdefault("compaction_policy", {})
        if manual_focus:
            after["compaction_policy"]["manual_focus"] = manual_focus

        actions: list[dict] = []
        if before_tokens > profile.soft_input_limit_tokens and profile.auto_compaction_enabled:
            for priorities in ({"P6", "P5"}, {"P4"}, {"P3"}, {"P2"}):
                self._compact_priorities(after, priorities, actions, manual_focus=manual_focus)
                self.budget.attach_token_estimates(after)
                current = int(after.get("token_report", {}).get("estimated_input_tokens", 0))
                if current <= profile.soft_input_limit_tokens:
                    break

        if dry_run:
            after = before

        after = self.budget.attach_token_estimates(after)
        after_tokens = int(after.get("token_report", {}).get("estimated_input_tokens", 0))
        compaction_id = self._compaction_id(before, after)
        validation = self.validator.validate(before, after, compaction_id=compaction_id)

        report = {
            "compaction_id": compaction_id,
            "created_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "before_tokens": before_tokens,
            "after_tokens": after_tokens,
            "saved_tokens": max(before_tokens - after_tokens, 0),
            "manual_focus": manual_focus or "",
            "dry_run": dry_run,
            "blocks_compacted": actions,
            "protected_blocks_unchanged": [
                block.get("block_id")
                for block in after.get("blocks", [])
                if isinstance(block, dict) and block.get("priority") in {"P0", "P1"}
            ],
            "validation": validation,
        }
        after["token_report"]["compaction_applied"] = bool(actions) and not dry_run
        after["token_report"]["compaction_report"] = report
        after.setdefault("compaction_reports", []).append(report)
        return {
            "envelope": after,
            "report": report,
            "validation": validation,
        }

    def _compact_priorities(
        self,
        envelope: dict,
        priorities: set[str],
        actions: list[dict],
        *,
        manual_focus: str | None,
    ) -> None:
        for block in envelope.get("blocks") or []:
            if not isinstance(block, dict):
                continue
            if block.get("priority") not in priorities:
                continue
            if not block.get("compressible", False):
                continue
            if block.get("compacted"):
                continue

            before_tokens = int(block.get("estimated_tokens") or self.budget.estimate_tokens(block.get("content")))
            original_hash = stable_hash(block.get("content"))
            block["content"] = self._summarize_block(block, manual_focus=manual_focus)
            block["compacted"] = True
            block["original_content_hash"] = original_hash
            after_tokens = self.budget.estimate_tokens(block.get("content"))
            actions.append({
                "block_id": block.get("block_id"),
                "from_tokens": before_tokens,
                "to_tokens": after_tokens,
                "method": self._method_for(block),
            })

    def _method_for(self, block: dict) -> str:
        btype = str(block.get("block_type") or block.get("type") or "")
        if "chapter" in btype or "text" in btype:
            return "rolling_summary_plus_key_spans"
        if "repair" in btype or "finding" in btype:
            return "active_risks_plus_counts"
        if "future" in btype or "corridor" in btype:
            return "future_corridor_structured"
        return "structured_summary"

    def _summarize_block(self, block: dict, *, manual_focus: str | None) -> Any:
        content = block.get("content")
        block_type = str(block.get("block_type") or block.get("type") or "")
        source_refs = block.get("source_refs") or []

        if isinstance(content, str):
            return self._summarize_text(content, block_type, source_refs, manual_focus)
        if isinstance(content, list):
            return self._summarize_list(content, block_type, source_refs, manual_focus)
        if isinstance(content, dict):
            return self._summarize_dict(content, block_type, source_refs, manual_focus)
        return {
            "summary_type": "scalar_context_summary",
            "value": str(content)[:500],
            "source_refs": source_refs,
        }

    def _summarize_text(
        self,
        text: str,
        block_type: str,
        source_refs: list,
        manual_focus: str | None,
    ) -> dict:
        text = text or ""
        head = text[:1200].strip()
        tail = text[-1800:].strip() if len(text) > 1800 else ""
        return {
            "summary_type": "rolling_text_summary",
            "block_type": block_type,
            "source_refs": source_refs,
            "source_ref": source_refs[0] if source_refs else block_type,
            "manual_focus": manual_focus or "",
            "head_excerpt": head,
            "tail_excerpt": tail,
            "char_count_before": len(text),
            "note": "保留首尾关键片段；完整原文仍在章节/档案存储中按 source_ref 回溯。",
        }

    def _summarize_list(
        self,
        values: list,
        block_type: str,
        source_refs: list,
        manual_focus: str | None,
    ) -> dict:
        active = []
        resolved_count = 0
        for item in values:
            if isinstance(item, dict):
                status = str(item.get("status") or item.get("review_status") or "").lower()
                if status in {"resolved", "ignored", "closed"}:
                    resolved_count += 1
                    continue
                active.append(self._compact_dict_item(item))
            elif len(active) < 12:
                active.append(str(item)[:300])
        return {
            "summary_type": "list_context_summary",
            "block_type": block_type,
            "source_refs": source_refs,
            "manual_focus": manual_focus or "",
            "active_items": active[:24],
            "resolved_or_low_value_count": resolved_count + max(len(values) - len(active) - resolved_count, 0),
        }

    def _summarize_dict(
        self,
        content: dict,
        block_type: str,
        source_refs: list,
        manual_focus: str | None,
    ) -> dict:
        preserved_keys = {
            "summary_type", "chapter_range", "plot_beats", "character_states",
            "object_states", "unresolved_foreshadowing", "forbidden_reveals",
            "do_not_reveal_yet", "must_preserve_for_later", "active_risks",
            "future_corridor", "constraints",
        }
        compacted = {
            key: value
            for key, value in content.items()
            if key in preserved_keys
        }
        if not compacted:
            compacted = {
                key: self._compact_value(value)
                for key, value in list(content.items())[:18]
            }
        compacted.setdefault("summary_type", f"{block_type or 'dict'}_compact")
        compacted.setdefault("source_refs", source_refs)
        compacted["manual_focus"] = manual_focus or ""
        compacted["original_keys"] = list(content.keys())[:40]
        return compacted

    def _compact_dict_item(self, item: dict) -> dict:
        keep = (
            "id", "issue_id", "finding_id", "type", "severity", "detail",
            "expected_behavior", "repair_goal", "repair_lane", "operation",
            "scene_id", "source_ref", "status",
        )
        return {key: self._compact_value(item.get(key)) for key in keep if item.get(key) not in (None, "", [], {})}

    def _compact_value(self, value: Any) -> Any:
        if isinstance(value, str):
            return value if len(value) <= 500 else value[:500] + "..."
        if isinstance(value, list):
            return [self._compact_value(v) for v in value[:12]]
        if isinstance(value, dict):
            return {k: self._compact_value(v) for k, v in list(value.items())[:16]}
        return value

    def _compaction_id(self, before: dict, after: dict) -> str:
        raw = json.dumps({
            "before": before.get("envelope_id"),
            "after_hash": stable_hash(after),
            "tokens": after.get("token_report", {}),
        }, ensure_ascii=False, sort_keys=True, default=str)
        return "ctxc_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


_compressor: ContextCompressor | None = None


def get_context_compressor() -> ContextCompressor:
    global _compressor
    if _compressor is None:
        _compressor = ContextCompressor()
    return _compressor
