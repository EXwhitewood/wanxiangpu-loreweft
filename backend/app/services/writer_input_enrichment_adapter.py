"""Writer input enrichment adapter.

This layer does not invent new narrative systems. It only reshapes the
existing scene contract, provenance, quality guidance and pacing signals into
compact, writer-facing hints with traceable sources.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel, Field


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return [item for item in value if item not in (None, "")]
    if isinstance(value, tuple):
        return [item for item in value if item not in (None, "")]
    if isinstance(value, set):
        return [item for item in value if item not in (None, "")]
    return [value]


def _dedupe_texts(values: Iterable[Any], limit: int = 8) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
        if len(result) >= limit:
            break
    return result


def _truncate_list(values: Iterable[Any], limit: int = 6, item_limit: int = 180) -> list[str]:
    items: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        items.append(text[:item_limit])
        if len(items) >= limit:
            break
    return items


def _conflicts_with_avoidance(text: str, avoid_items: Iterable[Any]) -> bool:
    """Detect hook hints that ask the writer to confirm a forbidden reveal."""
    hint = str(text or "").strip()
    if not hint:
        return False

    confirm_terms = (
        "确认", "确定", "证实", "揭开", "揭示", "暴露", "得知", "知道了",
        "就是", "正是", "真正的", "身份", "动机", "全局真相",
    )
    if not any(term in hint for term in confirm_terms):
        return False

    reveal_terms = ("揭示", "暴露", "露面", "身份", "动机", "完全掌握", "全局", "真相")
    ban_terms = ("不得", "禁止", "不允许", "不可", "不能")
    for item in avoid_items:
        rule = str(item or "").strip()
        if not any(term in rule for term in ban_terms):
            continue
        if any(term in rule for term in reveal_terms):
            return True
        protected_terms = _protected_terms_from_rule(rule)
        if protected_terms and any(term in hint for term in protected_terms):
            return True
    return False


def _protected_terms_from_rule(rule: str) -> list[str]:
    cleaned = re.sub(r"[，。；、,.!?！？（）()]", " ", str(rule or ""))
    cleaned = re.sub(r"(不得|禁止|不允许|不可|不能|提前|完全|具体|全部|揭示|暴露|确认|掌握|写出|写明|透露)", " ", cleaned)
    terms: list[str] = []
    for token in cleaned.split():
        token = token.strip()
        if len(token) >= 3 and token not in {"身份", "动机", "全局", "真相", "存在"}:
            terms.append(token[:24])
    return terms[:5]


def _safe_hook_out(raw_hook: str, avoid_items: Iterable[Any]) -> str:
    hook = str(raw_hook or "").strip()
    if not hook:
        return ""
    if not _conflicts_with_avoidance(hook, avoid_items):
        return hook
    return "以可见但不确证的线索收束；不得确认幕后者身份、动机或全局真相。"


class WriterInputEnrichment(BaseModel):
    active_capabilities: list[str] = Field(default_factory=list)
    activation_reasons: dict[str, str] = Field(default_factory=dict)
    source_trace: dict[str, list[str]] = Field(default_factory=dict)
    packet_patch: dict[str, Any] = Field(default_factory=dict)
    scene_summary: dict[str, Any] = Field(default_factory=dict)


class WriterInputEnrichmentAdapter:
    """Derive compact writer-facing guidance from existing scene data."""

    def build(
        self,
        scene_contract: dict | None,
        *,
        pov_character: str | None = None,
        previous_scene_summary: str = "",
    ) -> WriterInputEnrichment:
        contract = _as_dict(scene_contract)
        source_of_truth = _as_dict(contract.get("source_of_truth"))
        editor_enrichment = _as_dict(contract.get("editor_enrichment"))
        scene_provenance = _as_dict(contract.get("scene_provenance"))
        quality_extensions = _as_dict(contract.get("quality_extensions"))
        commercial_pacing_contract = _as_dict(contract.get("commercial_pacing_contract"))
        experience_contract = _as_dict(contract.get("experience_contract"))
        literary_quality_contract = _as_dict(contract.get("literary_quality_contract"))
        scene_credibility_contract = _as_dict(contract.get("scene_credibility_contract"))
        evidence_provenance_contract = _as_dict(contract.get("evidence_provenance_contract"))
        reader_corpus_guidance = _as_dict(contract.get("reader_corpus_guidance"))
        chapter_rhythm_context = _as_dict(contract.get("chapter_rhythm_context"))
        genre_enrichment = _as_dict(contract.get("genre_enrichment"))
        scene_function = str(contract.get("scene_function") or contract.get("chapter_function") or "").strip()
        target_emotion = str(contract.get("target_emotion") or "").strip()
        opening_state = str(
            editor_enrichment.get("opening_state")
            or contract.get("opening_state")
            or scene_provenance.get("timeline_anchor", {}).get("opening_state")
            or ""
        ).strip()
        ending_state = str(
            editor_enrichment.get("ending_state")
            or contract.get("ending_state")
            or scene_provenance.get("timeline_anchor", {}).get("ending_state")
            or ""
        ).strip()
        previous_tail = str(previous_scene_summary or "").strip()
        previous_tail = previous_tail[-280:] if previous_tail else ""

        active_capabilities: list[str] = []
        activation_reasons: dict[str, str] = {}
        source_trace: dict[str, list[str]] = {}
        packet_patch: dict[str, Any] = {}

        def activate(
            capability: str,
            reason: str,
            sources: Iterable[str],
            patch: dict[str, Any] | None = None,
        ) -> None:
            if capability not in active_capabilities:
                active_capabilities.append(capability)
            if reason:
                activation_reasons[capability] = reason
            source_trace[capability] = _truncate_list(sources, limit=8, item_limit=120)
            if patch:
                packet_patch[capability] = patch

        if previous_tail or opening_state or previous_scene_summary:
            activate(
                "continuity_anchor",
                "当前场景需要承接上一场结尾或既有开场状态",
                ["previous_scene_ending", "scene_provenance.timeline_anchor", "editor_enrichment.opening_state"],
                {
                    "previous_tail": previous_tail,
                    "opening_state": opening_state,
                    "required_opening_mode": "direct_continuation" if previous_tail or opening_state else "scene_opening",
                    "forbidden_opening_modes": [
                        "time_jump",
                        "generic_weather_start",
                        "summary_start",
                    ],
                },
            )

        if target_emotion or experience_contract or literary_quality_contract:
            emotional_arc = {
                "target_emotion": target_emotion,
                "start": str(
                    editor_enrichment.get("opening_emotion")
                    or scene_provenance.get("current_facts", {}).get("emotion_state")
                    or ""
                ).strip(),
                "turn": str(
                    experience_contract.get("emotion_turn")
                    or literary_quality_contract.get("emotion_arc")
                    or target_emotion
                ).strip(),
                "end": str(
                    editor_enrichment.get("ending_emotion")
                    or experience_contract.get("ending_emotion")
                    or target_emotion
                    or ending_state
                ).strip(),
            }
            activate(
                "emotion_atmosphere",
                "场景需要让环境与情绪共同承担压力",
                ["scene_contract.target_emotion", "experience_contract", "literary_quality_contract"],
                emotional_arc,
            )

        voice_guidance = quality_extensions.get("character_voice_guidance")
        if isinstance(voice_guidance, dict) and voice_guidance:
            activate(
                "dialogue_voice",
                "质量层已经给出角色声纹约束",
                ["quality_extensions.character_voice_guidance", "character_voice_checker"],
                {
                    "characters": {
                        str(name): _truncate_list(guidance, limit=3, item_limit=160)
                        for name, guidance in list(voice_guidance.items())[:5]
                        if isinstance(guidance, list)
                    }
                },
            )

        anti_ai_guidance = _truncate_list(quality_extensions.get("anti_ai_guidance", []), limit=6, item_limit=160)
        if anti_ai_guidance:
            activate(
                "anti_ai_prose",
                "已有去 AI 味指导和质量记忆可前置",
                ["quality_extensions.anti_ai_guidance", "ai_flavor_checker", "deslop_gate_engine"],
                {
                    "guidance": anti_ai_guidance,
                    "must_avoid": _truncate_list(
                        quality_extensions.get("reader_corpus_avoid", []) or [],
                        limit=4,
                        item_limit=120,
                    ),
                },
            )

        pacing_guidance = _truncate_list(quality_extensions.get("commercial_pacing_guidance", []), limit=6, item_limit=160)
        if pacing_guidance or chapter_rhythm_context or commercial_pacing_contract:
            activate(
                "rhythm_director",
                "场景节奏与商业压力已经有现成信号",
                ["chapter_rhythm_context", "commercial_pacing_contract", "experience_contract"],
                {
                    "scene_function": scene_function,
                    "pressure_hint": str(
                        commercial_pacing_contract.get("pressure_ramp")
                        or chapter_rhythm_context.get("pressure_hint")
                        or ""
                    ).strip(),
                    "sentence_policy": str(
                        chapter_rhythm_context.get("sentence_policy")
                        or commercial_pacing_contract.get("sentence_policy")
                        or ""
                    ).strip(),
                    "guidance": pacing_guidance,
                },
            )

        current_facts = _as_dict(scene_provenance.get("current_facts"))
        character_states = _as_dict(current_facts.get("character_states"))
        completed_events = _as_list(current_facts.get("completed_events"))
        if character_states or completed_events or scene_provenance.get("timeline_anchor"):
            activate(
                "action_continuity",
                "当前事实层包含角色状态、事件或时间线约束",
                ["scene_provenance.current_facts", "scene_truth_snapshot", "quality_memory"],
                {
                    "character_states": {
                        str(name): state
                        for name, state in list(character_states.items())[:5]
                        if state
                    },
                    "completed_events": _truncate_list(completed_events, limit=5, item_limit=120),
                    "repeat_avoid": _truncate_list(
                        contract.get("recent_repeated_body_language") or contract.get("repeated_body_language") or [],
                        limit=5,
                        item_limit=120,
                    ),
                },
            )

        reveal_control = _as_dict(quality_extensions.get("reveal_control"))
        avoidance_rules = [
            *_as_list(contract.get("forbidden")),
            *_as_list(source_of_truth.get("forbidden_outline")),
            *_as_list(editor_enrichment.get("additional_forbidden")),
            *_as_list(reveal_control.get("forbidden_future_concepts")),
        ]
        foreshadowing_ops = _as_list(source_of_truth.get("foreshadowing_ops"))
        clues = _as_list(scene_provenance.get("clues"))
        if reveal_control or foreshadowing_ops or clues:
            activate(
                "foreshadowing_control",
                "本场存在伏笔/线索/信息释放约束",
                ["scene_contract.source_of_truth.foreshadowing_ops", "quality_extensions.reveal_control", "scene_provenance.clues"],
                {
                    "must_not_reveal_yet": _truncate_list(
                        reveal_control.get("forbidden_future_concepts", []) or source_of_truth.get("forbidden_outline", []) or [],
                        limit=6,
                        item_limit=120,
                    ),
                    "preferred_carriers": _truncate_list(
                        reveal_control.get("preferred_carriers", []) or [],
                        limit=4,
                        item_limit=120,
                    ),
                    "clues": _truncate_list(clues, limit=5, item_limit=120),
                },
            )

        if evidence_provenance_contract:
            activate(
                "evidence_provenance_control",
                "剧情证据/线索/物件首次出现时必须具备来源、获得时机和可信连接",
                ["evidence_provenance_contract", "scene_validator.clue_provenance", "quality_gate"],
                {
                    "rule": evidence_provenance_contract.get("rule", "no_unprovenanced_evidence"),
                    "required_fields": _truncate_list(
                        evidence_provenance_contract.get("required_fields", []),
                        limit=6,
                        item_limit=80,
                    ),
                    "carriers": _truncate_list(
                        evidence_provenance_contract.get("carriers", []),
                        limit=10,
                        item_limit=40,
                    ),
                    "writer_instruction": str(evidence_provenance_contract.get("writer_instruction") or "").strip(),
                },
            )

        if scene_function or genre_enrichment or quality_extensions.get("scene_credibility_guidance"):
            activate(
                "exposition_control",
                "场景需要控制解释密度和概念投放",
                ["scene_function", "quality_extensions.scene_credibility_guidance", "genre_enrichment"],
                {
                    "new_concept_budget": reveal_control.get("new_concept_budget")
                    if isinstance(reveal_control, dict)
                    else None,
                    "definition_sentence_cap": contract.get("information_budget", {}).get("max_definition_sentences_per_1000_chars", 0)
                    if isinstance(contract.get("information_budget"), dict)
                    else None,
                    "scene_function": scene_function,
                },
            )

        if self._looks_like_climax(scene_function, target_emotion, quality_extensions, commercial_pacing_contract):
            raw_hook_out = str(
                commercial_pacing_contract.get("hook_out")
                or experience_contract.get("hook_out")
                or ending_state
                or ""
            ).strip()
            activate(
                "climax_pacing",
                "场景是高潮、冲突或高压段落",
                ["scene_function", "target_emotion", "commercial_pacing_guidance"],
                {
                    "pressure_pattern": _truncate_list(
                        commercial_pacing_contract.get("pressure_pattern", []) or [],
                        limit=5,
                        item_limit=120,
                    ),
                    "hook_out": _safe_hook_out(raw_hook_out, avoidance_rules),
                },
            )

        scene_summary = {
            "active_capabilities": active_capabilities,
            "activation_reasons": activation_reasons,
            "source_trace": source_trace,
        }

        return WriterInputEnrichment(
            active_capabilities=active_capabilities,
            activation_reasons=activation_reasons,
            source_trace=source_trace,
            packet_patch=packet_patch,
            scene_summary=scene_summary,
        )

    def build_chapter_summary(self, scene_contracts: list[dict] | None) -> dict[str, Any]:
        contracts = [item for item in (scene_contracts or []) if isinstance(item, dict)]
        scenes = []
        active_capabilities: list[str] = []
        source_trace: dict[str, list[str]] = {}

        for index, contract in enumerate(contracts):
            enrichment = self.build(contract, previous_scene_summary=str(contract.get("previous_scene_ending") or ""))
            scene_id = str(contract.get("scene_id") or f"scene_{index + 1}")
            scenes.append({
                "scene_id": scene_id,
                "scene_index": contract.get("scene_index", index),
                "active_capabilities": enrichment.active_capabilities,
                "activation_reasons": enrichment.activation_reasons,
            })
            for capability in enrichment.active_capabilities:
                if capability not in active_capabilities:
                    active_capabilities.append(capability)
                if capability in enrichment.source_trace:
                    source_trace.setdefault(capability, enrichment.source_trace[capability])

        return {
            "summary_type": "writer_input_enrichment",
            "active_capabilities": active_capabilities,
            "scenes": scenes,
            "source_trace": source_trace,
        }

    @staticmethod
    def _looks_like_climax(
        scene_function: str,
        target_emotion: str,
        quality_extensions: dict,
        commercial_pacing_contract: dict,
    ) -> bool:
        text = " ".join(
            str(part or "")
            for part in (
                scene_function,
                target_emotion,
                quality_extensions.get("commercial_pacing_guidance"),
                commercial_pacing_contract.get("scene_function"),
            )
        ).lower()
        markers = ("climax", "confront", "冲突", "高潮", "决战", "爆发", "审判")
        return any(marker in text for marker in markers)


_adapter: WriterInputEnrichmentAdapter | None = None


def get_writer_input_enrichment_adapter() -> WriterInputEnrichmentAdapter:
    global _adapter
    if _adapter is None:
        _adapter = WriterInputEnrichmentAdapter()
    return _adapter
