"""Unified chapter output effects for generation workflows.

This module keeps post-generation artifacts in one place so chapter-writer and
scene-writer paths produce the same durable effects: chapter facts, character
states, propositions, and quality observations.
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Mapping


def _as_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        try:
            dumped = value.model_dump()
            return dumped if isinstance(dumped, dict) else {}
        except Exception:
            return {}
    return {}


def _as_list(value: Any) -> list:
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, (tuple, set)):
        return list(value)
    return [value]


def _dedupe_extend(target: list, values: list) -> list:
    for value in values:
        if value not in target:
            target.append(value)
    return target


def _text_sentences(text: str) -> list[str]:
    sentences: list[str] = []
    for raw in re.split(r"(?<=[。！？!?])\s*|\n+", text or ""):
        sentence = re.sub(r"\s+", " ", raw or "").strip()
        if 8 <= len(sentence) <= 180 and not sentence.endswith(("？", "?")):
            sentences.append(sentence)
    return sentences


def _known_character_names(character_cards: list[dict] | None, scene_contract: dict | None = None) -> list[str]:
    names: list[str] = []
    for card in character_cards or []:
        if isinstance(card, dict):
            name = str(card.get("name") or "").strip()
            if name and name not in names:
                names.append(name)
    contract = scene_contract if isinstance(scene_contract, dict) else {}
    for key in ("pov_character", "pov", "main_character"):
        name = str(contract.get(key) or "").strip()
        if name and name not in names:
            names.append(name)
    enrichment = contract.get("editor_enrichment") if isinstance(contract.get("editor_enrichment"), dict) else {}
    for key in ("pov_lock", "pov_character"):
        name = str(enrichment.get(key) or "").strip()
        if name and name not in names:
            names.append(name)
    return names


def enrich_character_states(
    *,
    generated_text: str,
    character_cards: list[dict] | None,
    scene_contract: dict | None,
    scene_facts: dict,
) -> dict:
    """Ensure scene facts carry minimally useful character states.

    LLM fact extraction may return no character state even when characters are
    present. The fallback is conservative: it only records text evidence and
    contract anchors, not invented emotions or outcomes.
    """
    states = dict(scene_facts.get("character_states") or {})
    if not generated_text:
        return states

    sentences = _text_sentences(generated_text)
    contract = scene_contract if isinstance(scene_contract, dict) else {}
    location = (
        contract.get("location_anchor")
        or (contract.get("source_of_truth") or {}).get("location_anchor")
        or (contract.get("editor_enrichment") or {}).get("spatial_anchor")
        or ""
    )
    temporal_anchor = (
        contract.get("temporal_anchor")
        or (contract.get("source_of_truth") or {}).get("time_anchor")
        or (contract.get("editor_enrichment") or {}).get("temporal_anchor")
        or ""
    )

    for name in _known_character_names(character_cards, contract):
        if name not in generated_text:
            continue
        existing = states.get(name)
        if isinstance(existing, dict) and existing:
            existing.setdefault("evidence_span", next((s for s in sentences if name in s), ""))
            existing.setdefault("location", location)
            existing.setdefault("narrative_time", temporal_anchor)
            states[name] = existing
            continue
        if isinstance(existing, str) and existing.strip() and existing != "appears in this scene":
            continue
        evidence = next((s for s in sentences if name in s), "")
        states[name] = {
            "presence": "appears_in_scene",
            "location": location,
            "narrative_time": temporal_anchor,
            "knowledge_state": "explicitly mentioned in current scene",
            "evidence_span": evidence,
        }
    return states


def merge_scene_facts_into_chapter_state(
    chapter_state: dict,
    scene_facts: dict,
    *,
    generated_text: str = "",
    character_cards: list[dict] | None = None,
    scene_contract: dict | None = None,
) -> dict:
    state = chapter_state if isinstance(chapter_state, dict) else {}
    state.setdefault("established_facts", [])
    state.setdefault("character_states", {})
    state.setdefault("completed_events", [])
    state.setdefault("active_constraints", [])

    _dedupe_extend(state["established_facts"], _as_list(scene_facts.get("established_facts")))
    _dedupe_extend(state["completed_events"], _as_list(scene_facts.get("completed_events")))
    _dedupe_extend(state["active_constraints"], _as_list(scene_facts.get("active_constraints")))

    enriched = enrich_character_states(
        generated_text=generated_text,
        character_cards=character_cards,
        scene_contract=scene_contract,
        scene_facts=scene_facts,
    )
    if isinstance(enriched, dict):
        state["character_states"].update(enriched)

    scene_ending = scene_facts.get("scene_ending")
    if scene_ending:
        endings = state.setdefault("scene_endings", [])
        if scene_ending not in endings:
            endings.append(scene_ending)
    return state


def _reports_from_quality_gate(quality_gate_report: dict | None) -> dict:
    report = quality_gate_report if isinstance(quality_gate_report, dict) else {}
    reports = report.get("reports", {})
    return reports if isinstance(reports, dict) else {}


def proposition_effects_from_quality_report(quality_gate_report: dict | None) -> dict:
    reports = _reports_from_quality_gate(quality_gate_report)
    hard = reports.get("hard_correctness", {}) if isinstance(reports.get("hard_correctness"), dict) else {}
    narrative = reports.get("narrative_contract", {}) if isinstance(reports.get("narrative_contract"), dict) else {}
    extraction = hard.get("proposition_extraction") if isinstance(hard.get("proposition_extraction"), dict) else {}
    audit = hard.get("proposition_audit") if isinstance(hard.get("proposition_audit"), dict) else {}
    fact_contract = narrative.get("fact_contract") if isinstance(narrative.get("fact_contract"), dict) else {}

    effects: dict = {}
    propositions = extraction.get("propositions") if isinstance(extraction, dict) else []
    if isinstance(propositions, list) and propositions:
        effects["propositions"] = propositions
    if fact_contract:
        effects["fact_contract"] = fact_contract
        effects["compiler_warnings"] = narrative.get("compiler_warnings", []) or []
    if audit:
        effects["proposition_audit_report"] = audit
    return effects


def quality_effects_from_quality_report(quality_gate_report: dict | None, scene_contract: dict | None) -> dict:
    reports = _reports_from_quality_gate(quality_gate_report)
    contract = scene_contract if isinstance(scene_contract, dict) else {}
    effects: dict = {}

    quality_reports = {}
    for key in ("narrative_experience", "literary_quality", "commercial_pacing"):
        report = reports.get(key)
        if isinstance(report, dict) and report:
            quality_reports[key] = report
    if quality_reports:
        effects["experience_quality_reports"] = quality_reports

    narrative = quality_reports.get("narrative_experience", {})
    literary = quality_reports.get("literary_quality", {})
    commercial = quality_reports.get("commercial_pacing", {})
    exp_contract = contract.get("experience_contract") or narrative.get("contract") or {}
    lit_contract = contract.get("literary_quality_contract") or literary.get("contract") or {}
    pacing_contract = contract.get("commercial_pacing_contract") or commercial.get("contract") or {}
    if isinstance(exp_contract, dict) and exp_contract:
        effects["experience_contract"] = exp_contract
    if isinstance(lit_contract, dict) and lit_contract:
        effects["literary_quality_contract"] = lit_contract
    if isinstance(pacing_contract, dict) and pacing_contract:
        effects["commercial_pacing_contract"] = pacing_contract
    mode_fit = reports.get("mode_fit") if isinstance(reports.get("mode_fit"), dict) else {}
    if mode_fit:
        effects["mode_fit"] = mode_fit
    style_conflict = reports.get("style_experience_conflict") if isinstance(reports.get("style_experience_conflict"), dict) else {}
    if style_conflict:
        effects["style_conflict_report"] = style_conflict
    writing_mode_id = (
        exp_contract.get("writing_mode_id") if isinstance(exp_contract, dict) else ""
    ) or (
        lit_contract.get("writing_mode_id") if isinstance(lit_contract, dict) else ""
    ) or (
        mode_fit.get("writing_mode_id") if isinstance(mode_fit, dict) else ""
    )
    if writing_mode_id:
        effects["writing_mode_id"] = writing_mode_id
    return effects


def _fallback_propositions(
    *,
    project_id: str,
    chapter_number: int,
    scene_index: int,
    generated_text: str,
    scene_facts: dict,
    scene_contract: dict | None,
) -> list[dict]:
    sources = []
    sources.extend(_as_list(scene_facts.get("established_facts")))
    sources.extend(_as_list(scene_facts.get("completed_events")))
    if not sources:
        sources = _text_sentences(generated_text)[:3]
    propositions = []
    contract = scene_contract if isinstance(scene_contract, dict) else {}
    pov = str(contract.get("pov_character") or contract.get("pov") or "").strip()
    for source in sources[:6]:
        text = str(source or "").strip()
        if not text:
            continue
        subject = pov or _first_subject_guess(text)
        propositions.append({
            "proposition_id": str(uuid.uuid4()),
            "project_id": str(project_id),
            "chapter_number": int(chapter_number),
            "scene_index": int(scene_index),
            "generation_revision": 1,
            "subject": {"name": subject, "entity_type": "character" if subject != "chapter_event" else "event"},
            "predicate": {"name": "establishes", "category": "event"},
            "object": {"name": text[:80], "entity_type": "event"},
            "truth_layer": "current",
            "certainty": "confirmed",
            "polarity": "affirmed",
            "responsibility": "unknown",
            "time_scope": str(contract.get("temporal_anchor") or ""),
            "location_scope": str(contract.get("location_anchor") or ""),
            "source_text": text,
            "source_agent": "deterministic_chapter_output_effects",
            "confidence": 0.58,
        })
    return propositions


def _first_subject_guess(text: str) -> str:
    match = re.match(r"([\u4e00-\u9fffA-Za-z0-9_·]{2,12})", text or "")
    return match.group(1) if match else "chapter_event"


def build_scene_output_effects(
    *,
    project_id: str,
    chapter_number: int,
    scene_index: int,
    generated_text: str,
    scene_contract: dict | None,
    scene_facts: dict | None = None,
    base_effects: dict | None = None,
    quality_gate_report: dict | None = None,
    review_packet: Any | None = None,
) -> dict:
    """Build durable effects for one scene from every available artifact."""
    effects = dict(base_effects or {})
    scene_facts = scene_facts if isinstance(scene_facts, dict) else {}
    effects.setdefault("chapter_facts", scene_facts)

    if quality_gate_report:
        proposition_effects = proposition_effects_from_quality_report(quality_gate_report)
        effects.update(proposition_effects)
        if proposition_effects.get("propositions"):
            effects["proposition_extraction_status"] = "complete"
        effects.update(quality_effects_from_quality_report(quality_gate_report, scene_contract))

    if review_packet is not None:
        packet = _as_dict(review_packet)
        if not quality_gate_report and isinstance(packet.get("quality_gate_report"), dict):
            effects.update(proposition_effects_from_quality_report(packet.get("quality_gate_report")))
            effects.update(quality_effects_from_quality_report(packet.get("quality_gate_report"), scene_contract))
        advisories = packet.get("advisory_violations") if isinstance(packet.get("advisory_violations"), list) else []
        if advisories:
            effects["quality_observations"] = advisories
            reports = effects.setdefault("experience_quality_reports", {})
            reports.setdefault("workflow_advisory", {"advisories": advisories, "scores": {}})
        if packet.get("state_delta") and not effects.get("state_delta"):
            effects["state_delta"] = packet.get("state_delta")
        if packet.get("worldview_claims") and not effects.get("worldview_claims"):
            effects["worldview_claims"] = packet.get("worldview_claims")
        if packet.get("foreshadowing_claims") and not effects.get("foreshadowing_claims"):
            effects["foreshadowing_claims"] = packet.get("foreshadowing_claims")

    if not effects.get("propositions"):
        fallback = _fallback_propositions(
            project_id=project_id,
            chapter_number=chapter_number,
            scene_index=scene_index,
            generated_text=generated_text,
            scene_facts=scene_facts,
            scene_contract=scene_contract,
        )
        if fallback:
            effects["propositions"] = fallback
            effects.setdefault("proposition_audit_report", {
                "passed": False,
                "commit_blocked": False,
                "violations": [],
                "proposition_ids": [item["proposition_id"] for item in fallback],
                "warnings": ["deterministic_fallback_only", "proposition_audit_not_completed"],
            })
            effects.setdefault("proposition_extraction_status", "degraded")

    if not effects.get("fact_contract") and scene_facts:
        effects["fact_contract"] = {
            "schema_version": 1,
            "current_facts": _as_list(scene_facts.get("established_facts"))[:20],
            "reference_facts": [],
            "uncertain_facts": [],
            "required_truth_layers": ["current"],
            "forbidden_assertions": [],
            "required_ambiguities": [],
            "responsibility_constraints": [],
            "spatial_constraints": [],
            "temporal_constraints": [],
            "clue_constraints": [],
        }

    # 通用修复：当 effects 缺少 state_patch 但有 state_delta（含 raw_patch）时，
    # 从 state_delta 重建 state_patch。
    # 根因：scene_generation_pipeline._run_scene_postprocess_fanout 在 commit_blocked=True
    #   时被跳过，导致 state_patch 为空；但 review worker 独立调用了
    #   ScenePostProcessor 并将 state_delta 存入 review_packet。
    #   build_scene_output_effects 从 review_packet 补充了 state_delta（上方），
    #   但没有补充 state_patch。persist_scene_effects 只读 effects.state_patch
    #   （不读 state_delta），导致 StateManager.apply_patch 从未被调用，
    #   StoryState 一直是空壳，章节快照 story_state 也随之空壳。
    # 修复：从 state_delta.raw_patch 通过 StateDeltaReducer.reduce 重建 state_patch。
    # 通用性：state_patch 是 StoryState 的唯一更新来源，缺失它会导致章节快照
    #   story_state 为空壳，影响后续章节基线重建。适用于所有题材。
    if not effects.get("state_patch"):
        state_delta = effects.get("state_delta")
        if isinstance(state_delta, dict) and state_delta.get("raw_patch"):
            try:
                from app.services.state_delta_reducer import StateDeltaReducer

                reducer = StateDeltaReducer()
                raw_patch = state_delta.get("raw_patch") or {}
                delta = reducer.from_legacy_patch(
                    raw_patch,
                    project_id=str(project_id),
                    chapter_number=int(chapter_number),
                    scene_index=int(scene_index),
                )
                reduced_patch = reducer.reduce({}, delta)
                if reduced_patch:
                    effects["state_patch"] = reduced_patch
            except Exception:
                pass

    return effects

