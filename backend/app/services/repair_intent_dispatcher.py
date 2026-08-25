"""LLM dispatcher that turns fuzzy review findings into repair intents.

This layer does not write prose. It only compiles a natural-language finding
into a structured intent that the existing repair order compiler can execute.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.models.review_finding_v2 import ReviewFindingV2
from app.services.llm_gateway import get_llm_gateway
from app.services.llm_task_profiles import LLMTaskType

logger = logging.getLogger(__name__)


ALLOWED_LANES = {
    "fbi_fact",
    "fbi_prose",
    "fbi_pacing",
    "fbi_style_guard",
    "fbi_scene_restructure",
    "fbi_ending",
    "manual_only",
}

ALLOWED_OPERATIONS = {
    "replace_span",
    "insert_after",
    "insert_bridge_action",
    "insert_bridge_hint",
    "rewrite_window",
    "append_tail",
    "rewrite_scene",
}

OPERATION_ALIASES = {
    "insert_bridge": "insert_bridge_action",
    "insert_bridge_action_after": "insert_bridge_action",
    "insert_bridge_hint_after": "insert_bridge_hint",
    "local_rewrite": "rewrite_window",
    "scoped_rewrite": "rewrite_window",
    "rewrite_region": "rewrite_window",
}

GAP_TYPES = {
    "unknown",
    "unclassified",
    "unclassified_review_issue",
    "semantic_quality_error",
    "causal_chain_error",
    "clue_missing_source",
    "unprovenanced_clue",
    "clue_provenance_error",
    "missing_must_show",
    "fact_conflict",
}

GAP_MARKERS = (
    "未交代",
    "未提及",
    "没有交代",
    "没有说明",
    "缺少",
    "缺失",
    "补充",
    "补足",
    "植入",
    "伏笔",
    "线索",
    "暗示",
    "桥段",
    "前文",
    "动作序列",
    "拿在手里",
    "拿着",
    "书册",
)


class RepairIntentDispatcher:
    """Classify fuzzy findings into executable repair intents using an LLM."""

    def __init__(self, *, agent_name: str = "fbi_fact_repair") -> None:
        self.agent_name = agent_name

    async def dispatch_batch(
        self,
        findings: list[ReviewFindingV2],
        *,
        candidate_text: str,
        context: dict | None = None,
    ) -> tuple[list[ReviewFindingV2], list[dict]]:
        dispatched: list[ReviewFindingV2] = []
        reports: list[dict] = []

        for finding in findings:
            if not self.should_dispatch(finding, candidate_text):
                dispatched.append(finding)
                continue

            next_finding, report = await self.dispatch_one(
                finding,
                candidate_text=candidate_text,
                context=context or {},
            )
            dispatched.append(next_finding)
            reports.append(report)

        return dispatched, reports

    def should_dispatch(self, finding: ReviewFindingV2, candidate_text: str) -> bool:
        if finding.repair_intent:
            return False
        if not finding.blocks_commit:
            return False
        if finding.repair_scope != "prose_text":
            return False
        if finding.repair_lane in {"none", "manual_only"}:
            return True

        target = self._target_text(finding)
        combined = self._combined_text(finding)
        if finding.type in GAP_TYPES and (not target or target not in candidate_text):
            return True
        if finding.type in GAP_TYPES and any(marker in combined for marker in GAP_MARKERS):
            return True
        return False

    async def dispatch_one(
        self,
        finding: ReviewFindingV2,
        *,
        candidate_text: str,
        context: dict,
    ) -> tuple[ReviewFindingV2, dict]:
        report = {
            "finding_id": finding.id,
            "type": finding.type,
            "status": "pending",
            "source": "llm_repair_dispatcher",
        }

        llm_result = await self._call_llm(finding, candidate_text, context)
        intent = None
        if llm_result.get("ok"):
            intent = self._normalize_intent(llm_result.get("payload"), finding, candidate_text)
            if intent:
                report.update({
                    "status": "dispatched",
                    "operation": intent.get("operation"),
                    "repair_lane": intent.get("repair_lane"),
                    "semantic_type": intent.get("semantic_type"),
                    "confidence": intent.get("confidence"),
                    "llm_model": llm_result.get("model", ""),
                })

        if intent is None:
            fallback = self._fallback_intent(finding, candidate_text)
            if fallback:
                intent = fallback
                report.update({
                    "status": "fallback_dispatched",
                    "operation": intent.get("operation"),
                    "repair_lane": intent.get("repair_lane"),
                    "semantic_type": intent.get("semantic_type"),
                    "confidence": intent.get("confidence"),
                    "error": llm_result.get("error") or "llm_intent_validation_failed",
                })
            else:
                report.update({
                    "status": "unroutable",
                    "error": llm_result.get("error") or "no_valid_dispatch_intent",
                })
                return finding, report

        updated = finding.model_copy(update={
            "repair_lane": intent["repair_lane"],
            "repair_scope": "prose_text",
            "repair_intent": intent,
            "retryable": True,
            "max_attempts": max(finding.max_attempts, 3),
        })
        return updated, report

    async def _call_llm(self, finding: ReviewFindingV2, candidate_text: str, context: dict) -> dict:
        gateway = get_llm_gateway()
        if getattr(gateway, "agent_name", self.agent_name) != self.agent_name:
            gateway = gateway.__class__(agent_name=self.agent_name)

        result = await gateway.generate_json(
            system=self._system_prompt(),
            prompt=self._user_prompt(finding, candidate_text, context),
            temperature=0.1,
            task_type=LLMTaskType.JSON_DETECTION,
            response_format={"type": "json_object"},
        )
        if not result.ok:
            return {
                "ok": False,
                "error": result.error_message or result.error_type or "llm_failed",
                "model": result.model,
            }
        return {
            "ok": True,
            "payload": result.parsed_json,
            "model": result.model,
        }

    @staticmethod
    def _system_prompt() -> str:
        schema = {
            "semantic_type": "object_state_continuity_gap | action_continuity_gap | foreshadowing_completion_gap | causal_bridge_gap | knowledge_source_gap | direct_fact_conflict | prose_style_issue | pacing_issue | needs_manual",
            "repair_class": "action_continuity | foreshadowing_completion | causal_alignment | fact_alignment | prose_de_ai | pacing_enhancement | manual",
            "repair_lane": "fbi_fact | fbi_prose | fbi_pacing | fbi_scene_restructure | fbi_ending | manual_only",
            "operation": "replace_span | insert_bridge_action | insert_bridge_hint | rewrite_window | append_tail | rewrite_scene",
            "anchor_text": "verbatim text from current_text used as insertion/window anchor",
            "target_text": "required only for replace_span and must exist verbatim",
            "window": {"before_paragraphs": 2, "after_paragraphs": 1},
            "repair_goal": "what the downstream repairer must achieve",
            "must_preserve": ["facts/timeline/style that must not change"],
            "must_avoid": ["new facts/events that must not be introduced"],
            "recheck": ["object_state_continuity", "fact_consistency"],
            "confidence": 0.0,
            "rationale": "short routing reason",
        }
        return (
            "You are a dispatch-only repair triage agent.\n"
            "Your job is to translate a review finding into an executable repair intent.\n"
            "Do not write prose. Do not provide replacement paragraphs. Do not fix the text.\n"
            "Choose the safest downstream operation and provide anchors/constraints.\n"
            "Rules:\n"
            "1. If operation is replace_span, target_text must exist verbatim in current_text.\n"
            "2. If the problem is missing content, prefer insert_bridge_action, insert_bridge_hint, or rewrite_window.\n"
            "3. Use rewrite_scene only for scene-level structure failures.\n"
            "4. anchor_text must be copied verbatim from current_text whenever possible.\n"
            "5. Return valid JSON only in this schema:\n"
            f"{json.dumps(schema, ensure_ascii=False, indent=2)}"
        )

    def _user_prompt(self, finding: ReviewFindingV2, candidate_text: str, context: dict) -> str:
        raw_evidence = []
        for evidence in finding.evidence_spans:
            if isinstance(evidence, dict):
                raw_evidence.append(evidence)

        scene_contract = context.get("scene_contract") if isinstance(context, dict) else {}
        context_notes: dict[str, Any] = {}
        if isinstance(scene_contract, dict):
            for key in ("goal", "conflict", "ending_state", "pov_lock", "temporal_anchor"):
                if scene_contract.get(key):
                    context_notes[key] = scene_contract.get(key)

        text = candidate_text
        if len(text) > 7000:
            text = text[:3500] + "\n\n...[middle omitted for dispatch]...\n\n" + text[-3000:]

        payload = {
            "finding": finding.model_dump(),
            "raw_evidence": raw_evidence,
            "context_notes": context_notes,
            "current_text": text,
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def _normalize_intent(
        self,
        payload: Any,
        finding: ReviewFindingV2,
        candidate_text: str,
    ) -> dict | None:
        if isinstance(payload, dict) and isinstance(payload.get("intent"), dict):
            payload = payload["intent"]
        if not isinstance(payload, dict):
            return None

        semantic_type = str(payload.get("semantic_type") or "semantic_gap").strip()
        repair_class = str(payload.get("repair_class") or "fact_alignment").strip()
        repair_lane = str(payload.get("repair_lane") or self._lane_for_semantic(semantic_type)).strip()
        operation = str(payload.get("operation") or "").strip()
        operation = OPERATION_ALIASES.get(operation, operation)

        if repair_lane not in ALLOWED_LANES:
            repair_lane = self._lane_for_semantic(semantic_type)
        if operation not in ALLOWED_OPERATIONS:
            operation = self._operation_for_semantic(semantic_type, finding)

        anchor_text = self._clean_text(payload.get("anchor_text"))
        target_text = self._clean_text(payload.get("target_text"))
        repair_goal = self._clean_text(payload.get("repair_goal") or finding.description)
        window = self._normalize_window(payload.get("window"))

        if operation == "replace_span":
            if not target_text:
                target_text = self._target_text(finding)
            if target_text not in candidate_text:
                if anchor_text and anchor_text in candidate_text:
                    operation = "rewrite_window"
                else:
                    return None

        if not anchor_text or anchor_text not in candidate_text:
            anchor_text = self._infer_anchor(finding, candidate_text, preferred=anchor_text or target_text)

        if operation in {"insert_after", "insert_bridge_action", "insert_bridge_hint", "rewrite_window"}:
            if not anchor_text or anchor_text not in candidate_text:
                return None

        window_text = ""
        window_start = None
        window_end = None
        if operation == "rewrite_window":
            window_text, window_start, window_end = self._window_text(candidate_text, anchor_text, window)
            if not window_text:
                return None

        confidence = self._float(payload.get("confidence"), default=0.65)
        if confidence < 0.45:
            return None
        intent = {
            "semantic_type": semantic_type,
            "repair_class": repair_class,
            "repair_lane": repair_lane,
            "operation": operation,
            "anchor_text": anchor_text,
            "target_text": target_text,
            "window": window,
            "window_text": window_text,
            "window_start": window_start,
            "window_end": window_end,
            "insert_position": "after_anchor" if operation.startswith("insert") else "",
            "repair_goal": repair_goal,
            "must_preserve": self._string_list(payload.get("must_preserve")),
            "must_avoid": self._string_list(payload.get("must_avoid")),
            "recheck": self._string_list(payload.get("recheck")) or self._default_recheck(semantic_type),
            "confidence": confidence,
            "rationale": self._clean_text(payload.get("rationale")),
            "source": "llm_repair_dispatcher",
        }
        return intent

    def _fallback_intent(self, finding: ReviewFindingV2, candidate_text: str) -> dict | None:
        combined = self._combined_text(finding)
        anchor = self._infer_anchor(finding, candidate_text)
        if not anchor:
            return None

        semantic_type = "causal_bridge_gap"
        operation = "rewrite_window"
        repair_class = "causal_alignment"
        if any(marker in combined for marker in ("书册", "拿着", "拿在手里", "动作序列", "未交代")):
            semantic_type = "object_state_continuity_gap"
            repair_class = "action_continuity"
            operation = "rewrite_window"
        elif any(marker in combined for marker in ("伏笔", "线索", "前世", "穿越", "暗示")):
            semantic_type = "foreshadowing_completion_gap"
            repair_class = "foreshadowing_completion"
            operation = "insert_bridge_hint"

        window = {"before_paragraphs": 2, "after_paragraphs": 1}
        window_text, window_start, window_end = self._window_text(candidate_text, anchor, window)
        return {
            "semantic_type": semantic_type,
            "repair_class": repair_class,
            "repair_lane": "fbi_fact",
            "operation": operation,
            "anchor_text": anchor,
            "target_text": "",
            "window": window,
            "window_text": window_text,
            "window_start": window_start,
            "window_end": window_end,
            "insert_position": "after_anchor" if operation.startswith("insert") else "",
            "repair_goal": self._clean_text(self._expected_text(finding) or finding.description),
            "must_preserve": [],
            "must_avoid": ["Do not introduce unauthorized new facts or events."],
            "recheck": self._default_recheck(semantic_type),
            "confidence": 0.55,
            "rationale": "Conservative fallback after LLM dispatch failed.",
            "source": "rule_fallback_after_llm_dispatch",
        }

    @staticmethod
    def _lane_for_semantic(semantic_type: str) -> str:
        if "prose" in semantic_type:
            return "fbi_prose"
        if "pacing" in semantic_type:
            return "fbi_pacing"
        if "ending" in semantic_type:
            return "fbi_ending"
        if "manual" in semantic_type:
            return "manual_only"
        return "fbi_fact"

    @staticmethod
    def _operation_for_semantic(semantic_type: str, finding: ReviewFindingV2) -> str:
        text = f"{semantic_type} {finding.description}"
        if any(token in text for token in ("foreshadow", "伏笔", "线索", "暗示")):
            return "insert_bridge_hint"
        if any(token in text for token in ("object_state", "action", "动作", "状态")):
            return "rewrite_window"
        if any(token in text for token in ("ending", "结尾")):
            return "append_tail"
        return "rewrite_window"

    @staticmethod
    def _normalize_window(value: Any) -> dict:
        if not isinstance(value, dict):
            return {"before_paragraphs": 2, "after_paragraphs": 1}
        before = RepairIntentDispatcher._int(value.get("before_paragraphs"), 2)
        after = RepairIntentDispatcher._int(value.get("after_paragraphs"), 1)
        return {
            "before_paragraphs": max(0, min(before, 4)),
            "after_paragraphs": max(0, min(after, 3)),
        }

    @staticmethod
    def _default_recheck(semantic_type: str) -> list[str]:
        if "foreshadow" in semantic_type:
            return ["foreshadowing_presence", "fact_consistency"]
        if "object_state" in semantic_type or "action" in semantic_type:
            return ["object_state_continuity", "action_sequence", "fact_consistency"]
        if "knowledge" in semantic_type:
            return ["knowledge_boundary", "source_provenance"]
        return ["causal_continuity", "fact_consistency"]

    def _infer_anchor(
        self,
        finding: ReviewFindingV2,
        candidate_text: str,
        *,
        preferred: str = "",
    ) -> str:
        candidates = []
        if preferred:
            candidates.append(preferred)
        target = self._target_text(finding)
        if target:
            candidates.append(target)
        candidates.extend(self._quoted_fragments(self._combined_text(finding)))

        for candidate in candidates:
            candidate = self._clean_text(candidate)
            if candidate and candidate in candidate_text:
                return candidate

        for candidate in candidates:
            matched = self._sentence_containing(candidate_text, candidate)
            if matched:
                return matched

        paragraphs = [p.strip() for p in re.split(r"\n{2,}", candidate_text) if p.strip()]
        if not paragraphs:
            return candidate_text.strip()[:160]

        combined = self._combined_text(finding)
        if any(marker in combined for marker in ("全文", "前半段", "伏笔", "线索", "暗示")):
            index = 1 if len(paragraphs) > 2 and paragraphs[0].startswith("#") else 0
            return paragraphs[min(index, len(paragraphs) - 1)][:240]
        return paragraphs[-1][:240]

    @staticmethod
    def _window_text(text: str, anchor: str, window: dict) -> tuple[str, int | None, int | None]:
        if not text or not anchor or anchor not in text:
            return "", None, None

        paragraphs = []
        cursor = 0
        for match in re.finditer(r".+?(?:\n{2,}|$)", text, flags=re.S):
            para = match.group(0)
            if para.strip():
                paragraphs.append((match.start(), match.end(), para.rstrip()))
            cursor = match.end()
        if not paragraphs and cursor < len(text):
            paragraphs.append((0, len(text), text))

        anchor_pos = text.find(anchor)
        para_index = 0
        for idx, (start, end, _para) in enumerate(paragraphs):
            if start <= anchor_pos < end:
                para_index = idx
                break

        before = RepairIntentDispatcher._int(window.get("before_paragraphs"), 2)
        after = RepairIntentDispatcher._int(window.get("after_paragraphs"), 1)
        start_idx = max(0, para_index - before)
        end_idx = min(len(paragraphs) - 1, para_index + after)
        start = paragraphs[start_idx][0]
        end = paragraphs[end_idx][1]
        return text[start:end].strip(), start, end

    @staticmethod
    def _sentence_containing(text: str, fragment: str) -> str:
        fragment = RepairIntentDispatcher._clean_text(fragment)
        if not fragment:
            return ""
        tokens = [token for token in re.split(r"[，,。！？；;：:\s]+", fragment) if len(token) >= 2]
        if not tokens:
            return ""
        sentences = [s.strip() for s in re.split(r"(?<=[。！？!?])\s*", text) if s.strip()]
        for sentence in sentences:
            if any(token in sentence for token in tokens[:4]):
                return sentence[:240]
        return ""

    @staticmethod
    def _quoted_fragments(text: str) -> list[str]:
        fragments = re.findall(r"[「“\"'](.{2,120}?)[」”\"']", text, flags=re.S)
        return [RepairIntentDispatcher._clean_text(item) for item in fragments if item]

    @staticmethod
    def _target_text(finding: ReviewFindingV2) -> str:
        for evidence in finding.evidence_spans:
            if not isinstance(evidence, dict):
                continue
            raw = evidence.get("raw") if isinstance(evidence.get("raw"), dict) else {}
            for key in ("span", "text", "target_span", "evidence"):
                value = evidence.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            for key in ("target_span", "forbidden_item"):
                value = raw.get(key) if raw else None
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return ""

    @staticmethod
    def _expected_text(finding: ReviewFindingV2) -> str:
        for evidence in finding.evidence_spans:
            if not isinstance(evidence, dict):
                continue
            raw = evidence.get("raw") if isinstance(evidence.get("raw"), dict) else {}
            for key in ("expected_behavior", "expected_text", "replacement"):
                value = raw.get(key) if raw else evidence.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return ""

    def _combined_text(self, finding: ReviewFindingV2) -> str:
        parts = [finding.type, finding.description]
        expected = self._expected_text(finding)
        target = self._target_text(finding)
        if expected:
            parts.append(expected)
        if target:
            parts.append(target)
        for evidence in finding.evidence_spans:
            if isinstance(evidence, dict):
                parts.append(json.dumps(evidence, ensure_ascii=False))
        return "\n".join(part for part in parts if part)

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()][:12]
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        return []

    @staticmethod
    def _clean_text(value: Any) -> str:
        if not isinstance(value, str):
            return ""
        return re.sub(r"\s+", " ", value).strip()

    @staticmethod
    def _float(value: Any, *, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _int(value: Any, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default


_dispatcher: RepairIntentDispatcher | None = None


def get_repair_intent_dispatcher() -> RepairIntentDispatcher:
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = RepairIntentDispatcher()
    return _dispatcher
