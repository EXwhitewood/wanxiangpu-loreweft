"""Deterministic repair engine.

This engine handles repairs that should not depend on an LLM:
- scene length hard-limit compression
- advisory acknowledgement
- scene contract budget acknowledgement
- punctuation noise reduction
- obvious short-pattern repetition cleanup
"""
from __future__ import annotations

import logging
import hashlib
import re

from app.models.repair_order import RepairOrder
from app.utils.dash_artifacts import (
    DEFAULT_DASH_ARTIFACT_MAX_PER_1000,
    DASH_ARTIFACT_RE,
    dash_artifact_allowed_count,
    iter_dash_artifacts,
)

logger = logging.getLogger(__name__)


class TimeAnchorRepairer:
    """Deterministically repair obvious prose-time words against a scene anchor."""

    _ANCHOR_RE = re.compile(r"([子丑寅卯辰巳午未申酉戌亥]时(?:[一二三四五六七八九十半零〇\d]+刻)?)")
    _RELATIVE_TIME_RE = re.compile(r"([零〇一二两三四五六七八九十百\d]+(?:个)?[日天]后)")
    _PERIOD_MARKERS: dict[str, tuple[str, ...]] = {
        "predawn": ("寅时", "拂晓前", "天未亮", "天色未明"),
        "morning": ("卯时", "辰时", "清晨", "晨间", "晨光", "天刚亮", "黎明", "拂晓"),
        "late_morning": ("巳时", "上午", "日头渐高"),
        "noon": ("午时", "正午", "中午", "日上中天", "烈日当头", "日头正烈", "白晃晃"),
        "afternoon": ("未时", "申时", "午后", "下午"),
        "dusk": ("酉时", "黄昏", "傍晚", "暮色", "夕阳", "晚霞", "日暮"),
        "night": ("戌时", "亥时", "子时", "丑时", "夜里", "夜色", "深夜", "月光", "星光", "三更"),
    }
    _INCOMPATIBLE: dict[str, tuple[str, ...]] = {
        "predawn": ("noon", "afternoon", "dusk", "night"),
        "morning": ("noon", "afternoon", "dusk", "night"),
        "late_morning": ("dusk", "night"),
        "noon": ("predawn", "morning", "dusk", "night"),
        "afternoon": ("predawn", "morning", "noon", "night"),
        "dusk": ("predawn", "morning", "noon", "night"),
        "night": ("predawn", "morning", "late_morning", "noon", "afternoon", "dusk"),
    }

    @classmethod
    def repair(cls, order: RepairOrder, text: str, context: dict | None = None) -> dict:
        context = context or {}
        relative_result = cls._repair_relative_time_phrase(order, text)
        if relative_result is not None:
            return relative_result

        anchor = cls.extract_anchor(order, context)
        period = cls.classify_anchor(anchor)
        if not anchor or not period:
            order.mark_failed("missing or unsupported temporal anchor")
            return {"text": text, "patches": [], "changed": False}

        candidates = cls._candidate_conflict_spans(order, text, period)
        for before in candidates:
            after = cls._rewrite_span(before, period, anchor)
            if not after or after == before:
                continue
            start = text.find(before)
            if start < 0:
                continue
            end = start + len(before)
            if cls._touches_protected_span(start, end, order):
                continue
            new_text = text[:start] + after + text[end:]
            patch = {
                "operation": "replace_span",
                "repairer": "TimeAnchorRepairer",
                "target_text": before,
                "replacement": after,
                "before": before,
                "after": after,
                "start": start,
                "end": end,
                "affected_issue_ids": list(order.issue_ids),
                "requires_recheck": True,
                "reason": f"align prose time words with temporal_anchor={anchor}",
            }
            order.mark_succeeded([patch])
            return {
                "status": "patched",
                "text": new_text,
                "patches": [patch],
                "changed": True,
                "requires_recheck": True,
            }

        order.mark_failed("no applicable temporal conflict span found")
        return {"text": text, "patches": [], "changed": False}

    @classmethod
    def detect_conflicts(cls, text: str, context: dict | None = None) -> list[dict]:
        anchor = cls.extract_anchor(None, context or {})
        period = cls.classify_anchor(anchor)
        if not anchor or not period:
            return []

        conflicts = cls._find_conflict_spans(text, period, limit=3)
        violations = []
        for conflict in conflicts:
            detail = (
                f"时间锚点指定为 {anchor}，但正文写了“{conflict[:80]}”，"
                "暗示时间与设定不一致。"
            )
            violations.append({
                "violation_id": "tl_" + hashlib.md5(f"{anchor}:{conflict}".encode()).hexdigest()[:10],
                "source": "deterministic",
                "type": "timeline_conflict",
                "severity": "high",
                "detail": detail,
                "target_span": conflict,
                "expected_behavior": f"时间应保持为 {anchor}，删除或替换冲突的时段描写。",
                "suggested_strategy": "patch_text",
                "blocks_commit": True,
                "scope": "prose_text",
            })
        return violations

    @classmethod
    def _repair_relative_time_phrase(cls, order: RepairOrder, text: str) -> dict | None:
        target = order.target_region if isinstance(order.target_region, dict) else {}
        raw = target.get("raw") if isinstance(target.get("raw"), dict) else {}

        expected_fragments = cls._collect_relative_time_fragments(
            target,
            raw,
            keys=("expected_behavior", "expected_text", "replacement", "repair_goal", "after"),
        )
        if order.instruction:
            expected_fragments.append(str(order.instruction))
        replacement = cls._extract_expected_relative_time(expected_fragments)
        if not replacement:
            return None

        evidence_fragments = cls._collect_relative_time_fragments(
            target,
            raw,
            keys=("text", "target_span", "detail", "before", "anchor_text", "target_text", "window_text"),
        )
        wrong_phrases = cls._extract_conflicting_relative_times(evidence_fragments + expected_fragments, replacement)
        if not wrong_phrases:
            return None

        matches: list[tuple[int, int, str]] = []
        seen_positions: set[tuple[int, int]] = set()
        for match in cls._RELATIVE_TIME_RE.finditer(text):
            phrase = match.group(1)
            if phrase == replacement or phrase not in wrong_phrases:
                continue
            start, end = match.span(1)
            if (start, end) in seen_positions:
                continue
            seen_positions.add((start, end))
            if cls._touches_protected_span(start, end, order):
                continue
            matches.append((start, end, phrase))

        if not matches:
            return None

        new_text = text
        patches: list[dict] = []
        for start, end, before in sorted(matches, key=lambda item: item[0], reverse=True):
            new_text = new_text[:start] + replacement + new_text[end:]
            patches.append({
                "operation": "replace_span",
                "repairer": "TimeAnchorRepairer",
                "target_text": before,
                "replacement": replacement,
                "before": before,
                "after": replacement,
                "start": start,
                "end": end,
                "affected_issue_ids": list(order.issue_ids),
                "requires_recheck": True,
                "reason": "align relative prose time with expected temporal fact",
            })

        patches.sort(key=lambda patch: int(patch.get("start", 0)))
        order.mark_succeeded(patches)
        return {
            "status": "patched",
            "text": new_text,
            "patches": patches,
            "changed": True,
            "requires_recheck": True,
        }

    @staticmethod
    def _collect_relative_time_fragments(target: dict, raw: dict, *, keys: tuple[str, ...]) -> list[str]:
        fragments: list[str] = []
        for source in (target, raw):
            if not isinstance(source, dict):
                continue
            for key in keys:
                value = source.get(key)
                if value not in (None, "", [], {}):
                    fragments.append(str(value))
        intent = target.get("repair_intent") if isinstance(target, dict) else None
        if isinstance(intent, dict):
            for key in keys:
                value = intent.get(key)
                if value not in (None, "", [], {}):
                    fragments.append(str(value))
        return fragments

    @classmethod
    def _extract_expected_relative_time(cls, fragments: list[str]) -> str:
        for fragment in fragments:
            phrases = cls._relative_time_phrases(fragment)
            if phrases:
                # Phrases such as "将两日后改为三日后" should pick the final target.
                return phrases[-1]
        return ""

    @classmethod
    def _extract_conflicting_relative_times(cls, fragments: list[str], replacement: str) -> list[str]:
        wrongs: list[str] = []
        for fragment in fragments:
            for phrase in cls._relative_time_phrases(fragment):
                if phrase == replacement or phrase in wrongs:
                    continue
                wrongs.append(phrase)
        return wrongs

    @classmethod
    def _relative_time_phrases(cls, text: str) -> list[str]:
        return [match.group(1) for match in cls._RELATIVE_TIME_RE.finditer(str(text or ""))]

    @classmethod
    def extract_anchor(cls, order: RepairOrder | None, context: dict) -> str:
        for value in cls._iter_context_anchor_values(context):
            if value:
                return value

        fragments: list[str] = []
        if order is not None:
            target = order.target_region if isinstance(order.target_region, dict) else {}
            for key in ("expected_behavior", "detail", "text", "target_span"):
                value = target.get(key)
                if value:
                    fragments.append(str(value))
            fragments.append(order.instruction or "")

        combined = "\n".join(fragments)
        preferred = re.search(r"(?:时间锚点|保持|改为|应为|设定为)[^\n。；;]{0,24}?([子丑寅卯辰巳午未申酉戌亥]时(?:[一二三四五六七八九十半零〇\d]+刻)?)", combined)
        if preferred:
            return preferred.group(1)
        match = cls._ANCHOR_RE.search(combined)
        if match:
            return match.group(1)
        for period, markers in cls._PERIOD_MARKERS.items():
            for marker in markers:
                if marker in combined and period not in ("noon", "dusk", "night"):
                    return marker
        return ""

    @staticmethod
    def _iter_context_anchor_values(context: dict):
        scene_contract = context.get("scene_contract") if isinstance(context, dict) else {}
        packages = [scene_contract]
        scene_context_package = context.get("scene_context_package") if isinstance(context, dict) else {}
        if isinstance(scene_context_package, dict):
            packages.append(scene_context_package.get("scene_contract") or {})
        scene_package = context.get("scene_package") if isinstance(context, dict) else {}
        if isinstance(scene_package, dict):
            packages.append(scene_package.get("scene_contract") or {})

        for contract in packages:
            if not isinstance(contract, dict):
                continue
            for key in ("temporal_anchor", "time_anchor", "narrative_time"):
                value = contract.get(key)
                if isinstance(value, str) and value.strip():
                    yield value.strip()
            enrichment = contract.get("editor_enrichment")
            if isinstance(enrichment, dict):
                value = enrichment.get("temporal_anchor") or enrichment.get("narrative_time")
                if isinstance(value, str) and value.strip():
                    yield value.strip()
            source_of_truth = contract.get("source_of_truth")
            if isinstance(source_of_truth, dict):
                value = source_of_truth.get("time_anchor") or source_of_truth.get("narrative_time")
                if isinstance(value, str) and value.strip():
                    yield value.strip()

    @classmethod
    def classify_anchor(cls, anchor: str) -> str:
        if not anchor:
            return ""
        for period, markers in cls._PERIOD_MARKERS.items():
            if any(marker in anchor for marker in markers):
                return period
        match = cls._ANCHOR_RE.search(anchor)
        if not match:
            return ""
        branch = match.group(1)[0]
        return {
            "寅": "predawn",
            "卯": "morning",
            "辰": "morning",
            "巳": "late_morning",
            "午": "noon",
            "未": "afternoon",
            "申": "afternoon",
            "酉": "dusk",
            "戌": "night",
            "亥": "night",
            "子": "night",
            "丑": "night",
        }.get(branch, "")

    @classmethod
    def _candidate_conflict_spans(cls, order: RepairOrder, text: str, period: str) -> list[str]:
        spans: list[str] = []
        target = order.target_region if isinstance(order.target_region, dict) else {}
        for key in ("text", "target_span"):
            value = target.get(key)
            if isinstance(value, str) and value and value in text and cls._has_incompatible_marker(value, period):
                spans.append(value)
        spans.extend(cls._find_conflict_spans(text, period, limit=3))
        unique: list[str] = []
        for span in spans:
            if span and span not in unique:
                unique.append(span)
        return unique

    @classmethod
    def _find_conflict_spans(cls, text: str, period: str, limit: int = 3) -> list[str]:
        conflict_markers = cls._conflict_markers(period)
        if not conflict_markers:
            return []
        marker_pattern = "|".join(re.escape(marker) for marker in sorted(conflict_markers, key=len, reverse=True))
        pattern = re.compile(rf"[^。！？!?；;\n]{{0,36}}(?:{marker_pattern})[^。！？!?；;\n]{{0,36}}")
        spans: list[str] = []
        for match in pattern.finditer(text):
            span = match.group(0).strip()
            if span and span not in spans:
                spans.append(span)
            if len(spans) >= limit:
                break
        return spans

    @classmethod
    def _conflict_markers(cls, period: str) -> tuple[str, ...]:
        markers: list[str] = []
        for incompatible_period in cls._INCOMPATIBLE.get(period, ()):
            markers.extend(cls._PERIOD_MARKERS.get(incompatible_period, ()))
        return tuple(markers)

    @classmethod
    def _has_incompatible_marker(cls, span: str, period: str) -> bool:
        return any(marker in span for marker in cls._conflict_markers(period))

    @classmethod
    def _rewrite_span(cls, span: str, period: str, anchor: str) -> str:
        replacement_sets = {
            "predawn": (
                ("正午的光，白晃晃的", "天色未明，薄光冷冷的"),
                ("正午的光", "天色未明时的薄光"),
                ("正午", "天色未明时"),
                ("中午", "天色未明时"),
                ("夕阳", "将明未明的天光"),
                ("黄昏", "拂晓前"),
                ("深夜", "寅时前后"),
                ("月光", "淡薄天光"),
                ("白晃晃", "薄冷"),
            ),
            "morning": (
                ("是正午的光，白晃晃的", "晨间的光还薄，冷白的"),
                ("正午的光，白晃晃的", "晨间的光还薄，冷白的"),
                ("正午的光", "晨间偏冷的天光"),
                ("正午", "晨间"),
                ("中午", "晨间"),
                ("午时", anchor if "时" in anchor else "晨间"),
                ("日上中天", "天光初盛"),
                ("烈日当头", "晨光初盛"),
                ("日头正烈", "日头刚升高些"),
                ("夕阳", "晨光"),
                ("黄昏", "晨间"),
                ("傍晚", "晨间"),
                ("暮色", "晨色"),
                ("晚霞", "薄云晨色"),
                ("深夜", "晨间"),
                ("夜色", "晨色"),
                ("月光", "晨光"),
                ("星光", "晨光"),
                ("白晃晃", "薄白"),
            ),
            "late_morning": (
                ("黄昏", "巳时前后"),
                ("傍晚", "巳时前后"),
                ("夕阳", "渐高的日光"),
                ("深夜", "巳时前后"),
                ("夜色", "日光"),
                ("月光", "日光"),
            ),
            "noon": (
                ("清晨", "正午"),
                ("晨间", "正午"),
                ("晨光", "午间日光"),
                ("黄昏", "正午"),
                ("夕阳", "午间日光"),
                ("深夜", "正午"),
                ("夜色", "午间明光"),
                ("月光", "日光"),
            ),
            "afternoon": (
                ("清晨", "午后"),
                ("晨间", "午后"),
                ("正午", "午后"),
                ("中午", "午后"),
                ("深夜", "午后"),
                ("夜色", "午后日色"),
            ),
            "dusk": (
                ("清晨", "傍晚"),
                ("晨间", "傍晚"),
                ("晨光", "暮色"),
                ("正午", "傍晚"),
                ("中午", "傍晚"),
                ("深夜", "傍晚"),
                ("夜色", "暮色"),
                ("月光", "暮色"),
            ),
            "night": (
                ("清晨", "夜里"),
                ("晨间", "夜里"),
                ("晨光", "夜色"),
                ("正午", "夜里"),
                ("中午", "夜里"),
                ("夕阳", "夜色"),
                ("黄昏", "夜里"),
                ("傍晚", "夜里"),
                ("日光", "夜色"),
            ),
        }
        rewritten = span
        for before, after in replacement_sets.get(period, ()):
            rewritten = rewritten.replace(before, after)
        return rewritten

    @staticmethod
    def _touches_protected_span(start: int, end: int, order: RepairOrder) -> bool:
        for protected in order.protected_spans:
            if not isinstance(protected, dict):
                continue
            owner_ids = set(protected.get("owner_issue_ids") or protected.get("issue_ids") or [])
            if owner_ids and owner_ids & set(order.issue_ids):
                continue
            p_start = protected.get("start")
            p_end = protected.get("end")
            if isinstance(p_start, int) and isinstance(p_end, int) and start < p_end and p_start < end:
                return True
        return False


class DeterministicRepairEngine:
    """Repair small, mechanical issues without asking an LLM."""

    async def execute(self, order: RepairOrder, text: str, context: dict | None = None) -> dict:
        order.mark_running()
        context = context or {}

        try:
            if order.operation == "acknowledge":
                return self._acknowledge(order)
            if order.operation == "compress_region":
                return self._compress_region(order, text)
            if order.operation == "clean_punctuation":
                return self._clean_punctuation(order, text)
            if order.operation == "remove_repetition":
                return self._remove_repetition_order(order, text)
            if order.operation == "repair_time_anchor":
                return TimeAnchorRepairer.repair(order, text, context)
            if order.operation == "remove_forbidden_event":
                return self._remove_forbidden_event(order, text)
            if order.operation == "complete_ending_state":
                return self._complete_contract_text(order, text, context, "EndingCompletionRepairer")
            if order.operation == "complete_contract_item":
                return self._complete_contract_text(order, text, context, "ContractCompletionRepairer")
            if order.operation == "repair_fact_state":
                return self._repair_fact_state(order, text)
            if order.operation == "cleanup_ai_style":
                return self._cleanup_ai_style(order, text)
            if order.operation == "adjust_contract":
                return self._adjust_contract(order)
            if order.operation == "delete_span":
                return self._delete_span(order, text)

            order.mark_skipped(f"unsupported deterministic operation: {order.operation}")
            return {"text": text, "patches": [], "changed": False}
        except Exception as exc:
            order.mark_failed(str(exc))
            return {"text": text, "patches": [], "changed": False}

    def _acknowledge(self, order: RepairOrder) -> dict:
        order.mark_succeeded([])
        return {"text": "", "patches": [], "changed": False, "acknowledged": True}

    def _compress_region(self, order: RepairOrder, text: str) -> dict:
        original = text
        patches: list[dict] = []
        max_chars = self._extract_max_chars(order)

        text = self._apply_punctuation_cleanup(text, patches)
        text = self._remove_short_repetition(text, patches)

        if max_chars and len(text) > max_chars:
            compressed, removed = self._compress_to_limit(text, max_chars)
            if compressed != text:
                text = compressed
                patches.append({
                    "operation": "compress_to_limit",
                    "description": f"compress text to <= {max_chars} chars",
                    "removed_paragraphs": removed,
                })

        changed = text != original
        if changed:
            self._stamp_patches(patches, order, "LengthCompressionRepairer")
            order.mark_succeeded(patches)
        else:
            order.mark_skipped("no deterministic change available")

        return {"text": text, "patches": patches, "changed": changed}

    def _clean_punctuation(self, order: RepairOrder, text: str) -> dict:
        original = text
        patches: list[dict] = []
        text = self._apply_punctuation_cleanup(text, patches)
        changed = text != original
        if changed:
            self._stamp_patches(patches, order, "PunctuationRepairer")
            order.mark_succeeded(patches)
        else:
            order.mark_failed("no punctuation repair available")
        return {"text": text, "patches": patches, "changed": changed}

    def _remove_repetition_order(self, order: RepairOrder, text: str) -> dict:
        original = text
        patches: list[dict] = []
        text = self._remove_short_repetition(text, patches)
        changed = text != original
        if changed:
            self._stamp_patches(patches, order, "RepetitionRepairer")
            order.mark_succeeded(patches)
        else:
            order.mark_failed("no repetition repair available")
        return {"text": text, "patches": patches, "changed": changed}

    @classmethod
    def _apply_punctuation_cleanup(cls, text: str, patches: list[dict]) -> str:
        text = cls._normalize_repeated_punctuation(text, patches)
        return cls._soften_explanatory_dashes(text, patches)

    @staticmethod
    def _stamp_patches(patches: list[dict], order: RepairOrder, repairer: str) -> None:
        for patch in patches:
            patch.setdefault("repairer", repairer)
            patch.setdefault("affected_issue_ids", list(order.issue_ids))
            patch.setdefault("requires_recheck", True)

    def _remove_forbidden_event(self, order: RepairOrder, text: str) -> dict:
        target = self._target_dict(order)
        target_text = self._first_non_empty(
            target.get("text"),
            target.get("target_span"),
            target.get("forbidden_item"),
        )
        if not target_text:
            order.mark_failed("missing forbidden target")
            return {"text": text, "patches": [], "changed": False}

        start = text.find(target_text)
        if start < 0:
            order.mark_failed("forbidden target text not found")
            return {"text": text, "patches": [], "changed": False}

        span_start, span_end = self._sentence_bounds(text, start, start + len(target_text))
        before = text[span_start:span_end]
        if not before.strip():
            before = target_text
            span_start = start
            span_end = start + len(target_text)
        if self._touches_protected_span(span_start, span_end, order):
            order.mark_failed("forbidden target overlaps protected span")
            return {"text": text, "patches": [], "changed": False}

        replacement = self._join_after_delete(text, span_start, span_end)
        patch = {
            "operation": "delete_span",
            "repairer": "ForbiddenEventRepairer",
            "target_text": before,
            "replacement": "",
            "before": before,
            "after": "",
            "start": span_start,
            "end": span_end,
            "affected_issue_ids": list(order.issue_ids),
            "requires_recheck": True,
            "reason": "remove forbidden event/content from prose text",
        }
        order.mark_succeeded([patch])
        return {"status": "patched", "text": replacement, "patches": [patch], "changed": True}

    def _complete_contract_text(
        self,
        order: RepairOrder,
        text: str,
        context: dict,
        repairer: str,
    ) -> dict:
        required = self._required_completion_text(order, context)
        if not required:
            order.mark_failed("missing required completion text")
            return {"text": text, "patches": [], "changed": False}
        if self._requirement_present(text, required):
            order.mark_skipped("required completion text already present")
            return {"text": text, "patches": [], "changed": False}

        append_text = self._completion_sentence(required)
        if not append_text or append_text in text:
            order.mark_failed("completion text is empty or unchanged")
            return {"text": text, "patches": [], "changed": False}

        insert_at = len(text.rstrip())
        separator = "\n\n" if text.strip() else ""
        new_text = text.rstrip() + separator + append_text
        patch = {
            "operation": "append_tail",
            "repairer": repairer,
            "append_text": append_text,
            "replacement": append_text,
            "before": "",
            "after": append_text,
            "start": insert_at,
            "end": insert_at,
            "affected_issue_ids": list(order.issue_ids),
            "requires_recheck": True,
            "reason": "complete missing contract/ending requirement",
        }
        order.mark_succeeded([patch])
        return {"status": "patched", "text": new_text, "patches": [patch], "changed": True}

    def _repair_fact_state(self, order: RepairOrder, text: str) -> dict:
        target = self._target_dict(order)
        before = self._first_non_empty(target.get("text"), target.get("target_span"))
        expected = self._first_non_empty(
            target.get("replacement"),
            target.get("expected_text"),
            target.get("expected_behavior"),
            target.get("detail"),
        )
        if not before or not expected:
            order.mark_failed("missing fact target or expected replacement")
            return {"text": text, "patches": [], "changed": False}

        after = self._expected_to_replacement(str(expected), str(before))
        if not after or after == before:
            order.mark_failed("fact replacement is empty or unchanged")
            return {"text": text, "patches": [], "changed": False}

        start = text.find(before)
        if start < 0:
            order.mark_failed("fact target text not found")
            return {"text": text, "patches": [], "changed": False}
        end = start + len(before)
        if self._touches_protected_span(start, end, order):
            order.mark_failed("fact target overlaps protected span")
            return {"text": text, "patches": [], "changed": False}

        new_text = text[:start] + after + text[end:]
        patch = {
            "operation": "replace_span",
            "repairer": "FactStateRepairer",
            "target_text": before,
            "replacement": after,
            "before": before,
            "after": after,
            "start": start,
            "end": end,
            "affected_issue_ids": list(order.issue_ids),
            "requires_recheck": True,
            "reason": "replace conflicting fact/state span with expected state",
        }
        order.mark_succeeded([patch])
        return {"status": "patched", "text": new_text, "patches": [patch], "changed": True}

    def _cleanup_ai_style(self, order: RepairOrder, text: str) -> dict:
        original = text
        patches: list[dict] = []
        text = self._apply_punctuation_cleanup(text, patches)
        text = self._strip_formulaic_explanation(text, patches)

        changed = text != original
        if changed:
            self._stamp_patches(patches, order, "StyleCleanupRepairer")
            order.mark_succeeded(patches)
        else:
            order.mark_failed("no deterministic style cleanup available")
        return {"text": text, "patches": patches, "changed": changed}

    @staticmethod
    def _target_dict(order: RepairOrder) -> dict:
        if isinstance(order.target_region, dict):
            target = dict(order.target_region)
            raw = target.get("raw")
            if isinstance(raw, dict):
                for key, value in raw.items():
                    target.setdefault(key, value)
            return target
        return {}

    @staticmethod
    def _first_non_empty(*values) -> str:
        for value in values:
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, (int, float)):
                return str(value)
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, str) and item.strip():
                        return item.strip()
        return ""

    @staticmethod
    def _sentence_bounds(text: str, start: int, end: int) -> tuple[int, int]:
        separators = "。！？!?；;\n"
        left = max(text.rfind(ch, 0, start) for ch in separators)
        span_start = left + 1 if left >= 0 else 0
        while span_start < len(text) and text[span_start] in " \t\r\n":
            span_start += 1

        right_positions = [text.find(ch, end) for ch in separators]
        right_positions = [pos for pos in right_positions if pos >= 0]
        span_end = min(right_positions) + 1 if right_positions else len(text)
        while span_end < len(text) and text[span_end] in " \t":
            span_end += 1
        return span_start, span_end

    @staticmethod
    def _join_after_delete(text: str, start: int, end: int) -> str:
        new_text = text[:start] + text[end:]
        new_text = re.sub(r"\n{3,}", "\n\n", new_text)
        return re.sub(r"[ \t]{2,}", " ", new_text)

    @classmethod
    def _required_completion_text(cls, order: RepairOrder, context: dict) -> str:
        target = cls._target_dict(order)
        required = cls._first_non_empty(
            target.get("expected_behavior"),
            target.get("expected_state"),
            target.get("ending_state"),
            target.get("text"),
            target.get("detail"),
        )
        if required:
            return cls._core_requirement_phrase(required)

        scene_contract = context.get("scene_contract") if isinstance(context, dict) else {}
        if isinstance(scene_contract, dict):
            required = cls._first_non_empty(
                scene_contract.get("ending_state"),
                scene_contract.get("outcome_text"),
                scene_contract.get("outcome"),
            )
            if required:
                return cls._core_requirement_phrase(required)
            source_of_truth = scene_contract.get("source_of_truth")
            if isinstance(source_of_truth, dict):
                required = cls._first_non_empty(
                    source_of_truth.get("ending_state"),
                    source_of_truth.get("outcome_text"),
                    source_of_truth.get("must_show"),
                )
                if required:
                    return cls._core_requirement_phrase(required)
        return ""

    @staticmethod
    def _core_requirement_phrase(text: str) -> str:
        cleaned = str(text).strip()
        quoted = re.findall(r"[“\"'「『](.*?)[”\"'」』]", cleaned)
        for item in reversed(quoted):
            if item.strip():
                return item.strip()
        cleaned = re.sub(r"^(应当|应该|需要|必须|请|修订目标[:：]?)", "", cleaned).strip()
        cleaned = re.sub(r"^(结尾|正文|场景|状态)[^：:]{0,12}[:：]", "", cleaned).strip()
        return cleaned[:180].strip()

    @classmethod
    def _requirement_present(cls, text: str, required: str) -> bool:
        phrase = cls._core_requirement_phrase(required)
        if not phrase:
            return False
        if phrase in text:
            return True
        tokens = [token for token in re.split(r"[，,。；;、\s]+", phrase) if len(token) >= 2]
        return bool(tokens) and all(token in text for token in tokens[:3])

    @staticmethod
    def _completion_sentence(required: str) -> str:
        sentence = str(required).strip()
        if not sentence:
            return ""
        sentence = re.sub(r"^(应当|应该|需要|必须|请)", "", sentence).strip()
        sentence = sentence.rstrip("。！？!?；;")
        if not sentence:
            return ""
        return sentence + "。"

    @classmethod
    def _expected_to_replacement(cls, expected: str, before: str) -> str:
        expected = str(expected).strip()
        for pattern in (
            r"(?:改为|替换为|应为|保持为|正确为|应保持为)[:：]?\s*[“\"'「『]([^”\"'」』]+)[”\"'」』]",
            r"(?:改为|替换为|应为|保持为|正确为|应保持为)[:：]?\s*([^。；;\n]+)",
        ):
            match = re.search(pattern, expected)
            if match:
                candidate = match.group(1).strip()
                if candidate and candidate != before and not cls._looks_like_repair_instruction(candidate):
                    return candidate

        quoted = re.findall(r"[“\"'「『](.*?)[”\"'」』]", expected)
        for candidate in reversed(quoted):
            candidate = candidate.strip()
            if candidate and candidate != before and not cls._looks_like_repair_instruction(candidate):
                return candidate

        candidate = cls._core_requirement_phrase(expected)
        if len(candidate) <= 180 and not cls._looks_like_repair_instruction(candidate):
            return candidate
        return ""

    @staticmethod
    def _looks_like_repair_instruction(text: str) -> bool:
        return str(text or "").strip().startswith((
            "删除", "删去", "去掉", "移除", "避免", "保留", "确认", "说明", "建议", "需要", "需", "应当", "应该",
        ))

    @staticmethod
    def _strip_formulaic_explanation(text: str, patches: list[dict]) -> str:
        patterns: tuple[tuple[str, str], ...] = (
            (r"(?:这意味着|也就是说|换言之|简单来说)[，,]?", ""),
            (r"(?:她|他|他们|她们|众人)?(?:意识到|明白了|确认了)[，,]", ""),
            (r"(?:这)?并?不是[^。！？!?；;\n]{1,24}，而是", "更像是"),
            (r"不是[^。！？!?；;\n]{1,24}，更不是", "不是"),
        )

        for pattern, replacement in patterns:
            regex = re.compile(pattern)
            while True:
                match = regex.search(text)
                if not match:
                    break
                before = match.group(0)
                after = match.expand(replacement)
                if before == after:
                    break
                start, end = match.span()
                text = text[:start] + after + text[end:]
                patches.append({
                    "operation": "replace_span",
                    "target_text": before,
                    "replacement": after,
                    "before": before,
                    "after": after,
                    "start": start,
                    "end": end,
                    "reason": "remove formulaic explanatory narration",
                })
        return text

    @staticmethod
    def _touches_protected_span(start: int, end: int, order: RepairOrder) -> bool:
        for protected in order.protected_spans:
            if not isinstance(protected, dict):
                continue
            owner_ids = set(protected.get("owner_issue_ids") or protected.get("issue_ids") or [])
            if owner_ids and owner_ids & set(order.issue_ids):
                continue
            p_start = protected.get("start")
            p_end = protected.get("end")
            if isinstance(p_start, int) and isinstance(p_end, int) and start < p_end and p_start < end:
                return True
        return False

    @staticmethod
    def _normalize_repeated_punctuation(text: str, patches: list[dict]) -> str:
        new_text = re.sub(r"—{3,}", "——", text)
        if new_text != text:
            patches.append({"operation": "compress_dashes", "description": "merge repeated dashes"})
            text = new_text

        new_text = re.sub(r"(?:……){2,}", "……", text)
        if new_text != text:
            patches.append({"operation": "compress_ellipsis", "description": "merge repeated ellipses"})
        return new_text

    @classmethod
    def _soften_explanatory_dashes(cls, text: str, patches: list[dict]) -> str:
        """Replace dashes that introduce direct explanation with lighter punctuation."""
        pattern = re.compile(
            rf"(?:{DASH_ARTIFACT_RE.pattern})\s*(?=(?:不是|并非|而是|说明|意味着|也就是说|换言之|因为|所以|只不过|更像|像是|确认|明白|意识到|是))"
        )
        new_text = pattern.sub("，", text)
        if new_text != text:
            patches.append({
                "operation": "soften_explanatory_dash",
                "description": "replace explanatory dash with comma",
            })

        return cls._reduce_emdash_density(new_text, patches)

    @classmethod
    def _reduce_emdash_density(cls, text: str, patches: list[dict]) -> str:
        """Reduce excessive dash artifacts without rewriting prose."""
        matches = list(iter_dash_artifacts(text))
        if not matches:
            return text

        max_allowed = dash_artifact_allowed_count(text, DEFAULT_DASH_ARTIFACT_MAX_PER_1000)
        if len(matches) <= max_allowed:
            return text

        pieces: list[str] = []
        cursor = 0
        for index, match in enumerate(matches):
            start, end = match.span()
            pieces.append(text[cursor:start])
            if index < max_allowed:
                pieces.append(match.group(0))
            else:
                before = text[max(0, start - 18):start].rstrip()
                after = text[end:end + 18].lstrip()
                pieces.append(cls._emdash_replacement(before, after))
            cursor = end
        pieces.append(text[cursor:])
        new_text = "".join(pieces)
        if new_text != text:
            patches.append({
                "operation": "reduce_emdash_density",
                "description": "replace excessive explanatory em-dashes with sentence punctuation",
                "original_pairs": len(matches),
                "kept_pairs": max_allowed,
                "replaced_pairs": len(matches) - max_allowed,
            })
        return new_text

    @staticmethod
    def _emdash_replacement(before: str, after: str) -> str:
        if after.startswith(("“", '"', "'", "《")):
            return "："
        reveal_verbs = (
            "回放", "写着", "写道", "想起", "发现", "看到", "看见", "听见",
            "触到", "摸到", "取出", "抽出", "抽出来", "展开", "念出",
            "确认", "明白", "意识到",
        )
        if any(before.endswith(token) for token in reveal_verbs):
            return "："
        return "，"

    @staticmethod
    def _remove_short_repetition(text: str, patches: list[dict]) -> str:
        for match in list(re.finditer(r"(.{4,8})\1{2,}", text)):
            repeated = match.group(1)
            replacement = repeated * 2
            text = text[:match.start()] + replacement + text[match.end():]
            patches.append({"operation": "remove_repetition", "description": f"dedupe: {repeated}"})
        return text

    @staticmethod
    def _extract_max_chars(order: RepairOrder) -> int:
        source = f"{order.instruction} {order.result}"
        patterns = (
            r"硬上限\s*(\d+)",
            r"应\s*[≤<=]\s*(\d+)",
            r"建议\s*[≤<=]\s*(\d+)",
            r"上限\s*(\d+)",
            r"hard[_ ]?max[_ ]?chars[^\d]*(\d+)",
        )
        for pattern in patterns:
            match = re.search(pattern, source, flags=re.IGNORECASE)
            if match:
                try:
                    return int(match.group(1))
                except ValueError:
                    return 0
        return 0

    @classmethod
    def _compress_to_limit(cls, text: str, max_chars: int) -> tuple[str, list[str]]:
        if max_chars <= 0 or len(text) <= max_chars:
            return text, []

        paragraphs = text.split("\n\n")
        if len(paragraphs) <= 3:
            return cls._trim_sentences(text, max_chars), []

        protected_indexes = {0, len(paragraphs) - 1}
        if paragraphs[0].lstrip().startswith("#") and len(paragraphs) > 1:
            protected_indexes.add(1)

        candidates: list[tuple[int, int, str]] = []
        for idx, para in enumerate(paragraphs):
            if idx in protected_indexes:
                continue
            stripped = para.strip()
            if not stripped:
                candidates.append((idx, -10, stripped))
            else:
                candidates.append((idx, cls._paragraph_score(stripped), stripped))

        removed: list[str] = []
        active = [True] * len(paragraphs)
        for idx, _score, para in sorted(candidates, key=lambda item: (item[1], len(item[2]))):
            current = "\n\n".join(p for keep, p in zip(active, paragraphs) if keep).strip()
            if len(current) <= max_chars:
                break
            active[idx] = False
            removed.append(para[:80])

        compressed = "\n\n".join(p for keep, p in zip(active, paragraphs) if keep).strip()
        if len(compressed) > max_chars:
            compressed = cls._trim_sentences(compressed, max_chars)
        return compressed, removed

    @staticmethod
    def _paragraph_score(paragraph: str) -> int:
        score = 0
        if "“" in paragraph or "”" in paragraph:
            score += 8
        if any(token in paragraph for token in ("拿起", "放下", "起身", "推", "开", "看", "听", "问", "答", "说", "藏", "发现")):
            score += 5
        if any(token in paragraph for token in ("密信", "符文", "残香", "玉簪", "血", "门", "箱", "禁地", "长老", "证据")):
            score += 6
        if any(token in paragraph for token in ("因此", "所以", "因为", "只知道", "这意味着", "她明白", "她意识到", "不是", "而是")):
            score -= 4
        if len(paragraph) > 180:
            score -= 2
        if len(paragraph) < 30:
            score -= 1
        return score

    @staticmethod
    def _trim_sentences(text: str, max_chars: int) -> str:
        if len(text) <= max_chars:
            return text
        sentences = re.split(r"(?<=[。！？?!])", text)
        sentences = [s for s in sentences if s]
        if len(sentences) <= 4:
            return text[:max_chars].rstrip()

        keep = [True] * len(sentences)
        protected = {0, 1, len(sentences) - 1}
        candidates = [i for i in range(len(sentences)) if i not in protected]
        midpoint = len(sentences) / 2
        for idx in sorted(candidates, key=lambda i: abs(i - midpoint)):
            current = "".join(s for k, s in zip(keep, sentences) if k).strip()
            if len(current) <= max_chars:
                break
            keep[idx] = False
        return "".join(s for k, s in zip(keep, sentences) if k).strip()

    def _adjust_contract(self, order: RepairOrder) -> dict:
        order.mark_succeeded([{
            "operation": "adjust_contract",
            "description": order.instruction,
        }])
        return {"text": "", "patches": [], "changed": False, "contract_adjusted": True}

    def _delete_span(self, order: RepairOrder, text: str) -> dict:
        target = order.target_region
        if not target or not isinstance(target, dict):
            order.mark_failed("missing target region")
            return {"text": text, "patches": [], "changed": False}

        target_text = target.get("text", "")
        if target_text and target_text in text:
            start = text.index(target_text)
            end = start + len(target_text)
            replacement = ""
            new_text = text[:start] + replacement + text[end:]
            patch = {
                "operation": "delete_span",
                "repairer": "ForbiddenPhraseRepairer",
                "deleted": target_text,
                "target_text": target_text,
                "replacement": replacement,
                "before": target_text,
                "after": replacement,
                "start": start,
                "end": end,
                "affected_issue_ids": list(order.issue_ids),
                "requires_recheck": True,
            }
            order.mark_succeeded([patch])
            return {"text": new_text, "patches": [patch], "changed": True}

        order.mark_failed("target text not found")
        return {"text": text, "patches": [], "changed": False}

    def can_handle(self, order: RepairOrder) -> bool:
        return order.lane == "deterministic" or order.operation in (
            "acknowledge",
            "adjust_contract",
            "compress_region",
            "clean_punctuation",
            "remove_repetition",
            "repair_time_anchor",
            "remove_forbidden_event",
            "complete_ending_state",
            "complete_contract_item",
            "repair_fact_state",
            "cleanup_ai_style",
            "delete_span",
        )


_engine: DeterministicRepairEngine | None = None


def get_deterministic_repair_engine() -> DeterministicRepairEngine:
    global _engine
    if _engine is None:
        _engine = DeterministicRepairEngine()
    return _engine
