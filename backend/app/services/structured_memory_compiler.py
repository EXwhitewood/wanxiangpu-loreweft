"""Compile long-term memory into writer and contract constraints.

This service sits between persisted chapter effects and the next generation
turn.  It keeps the data domain-neutral: facts, character state, propositions,
quality policies, and information-budget decisions are represented as generic
constraints rather than genre-specific prose advice.
"""
from __future__ import annotations

from typing import Any, Mapping

from app.services.core_entity_resolver import normalize_entity_key


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list:
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _clip(value: Any, limit: int = 180) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit]


class StructuredMemoryCompiler:
    """Build compact constraints from chapter snapshots and propositions."""

    def compile_memory(
        self,
        *,
        latest_state: dict | None = None,
        active_propositions: list[dict] | None = None,
        active_foreshadowing: list[dict] | None = None,
        quality_memory: dict | None = None,
        source_refs: list[str] | None = None,
        chapter_number: int | None = None,
        max_propositions: int = 24,
        max_foreshadowing: int = 16,
        max_facts: int = 18,
        max_characters: int = 8,
        relevant_entity_names: list[str] | None = None,
        proposition_selection_trace: dict | None = None,
    ) -> dict:
        latest_state = _as_dict(latest_state)
        propositions = [_as_dict(item) for item in _as_list(active_propositions)]
        foreshadowing = [_as_dict(item) for item in _as_list(active_foreshadowing)]
        quality_memory = _as_dict(quality_memory)
        relevant_entity_names = [str(name) for name in (relevant_entity_names or []) if str(name).strip()]
        state_projection = self.compile_state_projection(
            latest_state,
            relevant_entity_names=relevant_entity_names,
            max_facts=max_facts,
            max_characters=max_characters,
        )
        character_states = _as_dict(state_projection.get("character_states"))

        proposition_summary = self._summarize_propositions(
            propositions,
            max_items=max_propositions,
        )
        character_constraints = self._compile_character_constraints(
            character_states,
            max_items=max_characters,
        )
        foreshadowing_summary = self._summarize_foreshadowing(
            foreshadowing,
            chapter_number=chapter_number,
            max_items=max_foreshadowing,
        )
        prior_facts = self._compile_prior_facts(state_projection, max_items=max_facts)
        quality_policy = self.compile_quality_policy(quality_memory)

        return {
            "summary_type": "structured_story_memory",
            "state_projection": state_projection,
            "active_character_states": character_states,
            "active_proposition_summary": proposition_summary,
            "proposition_selection_trace": proposition_selection_trace or {},
            "active_foreshadowing": foreshadowing_summary["items"],
            "active_foreshadowing_summary": foreshadowing_summary,
            "required_prior_facts": prior_facts,
            "character_state_constraints": character_constraints,
            "proposition_constraints": proposition_summary["constraints"],
            "foreshadowing_constraints": foreshadowing_summary["constraints"],
            # Retain the source memory for service/API compatibility.  The
            # context envelope removes this copy after deriving quality_policy
            # because quality_memory is already emitted as its own block.
            "quality_memory": quality_memory,
            "quality_policy": quality_policy,
            "source_refs": source_refs or [],
        }

    def compile_state_projection(
        self,
        state: dict | None,
        *,
        relevant_entity_names: list[str] | None = None,
        max_facts: int = 12,
        max_characters: int = 8,
    ) -> dict:
        """Bound a canonical snapshot without mutating or replacing it."""
        state = _as_dict(state)
        relevant_keys = {normalize_entity_key(name) for name in (relevant_entity_names or [])}
        relevant_keys.discard("")
        all_character_states = _as_dict(state.get("character_states"))
        selected_characters: dict[str, Any] = {}
        ordered_names = sorted(
            all_character_states,
            key=lambda name: (normalize_entity_key(name) not in relevant_keys, normalize_entity_key(name)),
        )
        for name in ordered_names[:max_characters]:
            selected_characters[str(name)] = all_character_states[name]

        per_type_limit = max(1, max_facts // 3)
        selected_lists = {}
        omitted = {}
        for key in ("established_facts", "completed_events", "active_constraints"):
            values = _as_list(state.get(key))
            chosen = self._select_relevant_state_items(values, relevant_keys, per_type_limit)
            selected_lists[key] = chosen
            omitted[key] = max(0, len(values) - len(chosen))
        omitted["character_states"] = max(0, len(all_character_states) - len(selected_characters))
        return {
            "schema_version": 1,
            "character_states": selected_characters,
            **selected_lists,
            "selection_metadata": {
                "source_counts": {
                    "character_states": len(all_character_states),
                    **{key: len(_as_list(state.get(key))) for key in selected_lists},
                },
                "selected_counts": {
                    "character_states": len(selected_characters),
                    **{key: len(value) for key, value in selected_lists.items()},
                },
                "omitted_counts": omitted,
                "relevant_entities": sorted(relevant_entity_names or []),
            },
        }

    def compile_current_story_state(self, story_state: dict | None) -> dict:
        """Keep exact current control fields while bounding nested collections."""
        state = _as_dict(story_state)
        scalar_keys = (
            "timeline_id", "narrative_time", "active_chapter", "active_scene",
            "pov_character", "current_location",
        )
        result = {key: state.get(key) for key in scalar_keys if state.get(key) not in (None, "")}
        result["objective_state"] = _as_dict(state.get("objective_state"))
        views = _as_dict(state.get("subjective_views"))
        result["subjective_views"] = dict(list(views.items())[:8])
        result["active_constraints"] = _as_list(state.get("active_constraints"))[-12:]
        result["completed_events"] = _as_list(state.get("completed_events"))[-8:]
        result["projection_metadata"] = {
            "canonical_store": "sqlite_story_state",
            "subjective_views_omitted": max(0, len(views) - len(result["subjective_views"])),
            "active_constraints_omitted": max(0, len(_as_list(state.get("active_constraints"))) - 12),
            "completed_events_omitted": max(0, len(_as_list(state.get("completed_events"))) - 8),
        }
        return result

    @staticmethod
    def _select_relevant_state_items(items: list, relevant_keys: set[str], limit: int) -> list:
        relevant = []
        for item in items:
            normalized = normalize_entity_key(str(item))
            if relevant_keys and any(key in normalized for key in relevant_keys):
                relevant.append(item)
        selected = list(relevant[-limit:])
        for item in reversed(items):
            if len(selected) >= limit:
                break
            if item not in selected:
                selected.append(item)
        selected.reverse()
        return selected

    def enrich_scene_contract(
        self,
        scene_contract: dict,
        structured_memory: dict,
        *,
        proposition_selection: dict | None = None,
        max_hard_must_show: int = 5,
        max_soft_guidance: int = 3,
    ) -> dict:
        """Attach memory constraints and information-budget partitions.

        The input contract is copied.  Existing hard contract fields are not
        overwritten except for `must_show`, which is reduced only when over
        budget and preserved in `information_budget.original_must_show`.
        """
        contract = dict(scene_contract or {})
        memory = _as_dict(structured_memory)

        proposition_constraints = _as_list(memory.get("proposition_constraints"))[:24]
        selection_trace = _as_dict(memory.get("proposition_selection_trace"))
        if proposition_selection:
            selected = _as_list(proposition_selection.get("selected"))
            proposition_constraints = self._summarize_propositions(selected, max_items=24)["constraints"]
            selection_trace = _as_dict(proposition_selection.get("trace"))
        long_term_constraints = {
            "required_prior_facts": _as_list(memory.get("required_prior_facts"))[:18],
            "character_state_constraints": _as_list(memory.get("character_state_constraints"))[:8],
            "proposition_constraints": proposition_constraints,
            "proposition_selection_trace": self.compact_selection_trace(selection_trace),
            "foreshadowing_constraints": self._select_scene_foreshadowing_constraints(
                _as_list(memory.get("foreshadowing_constraints")),
                contract,
                max_items=6,
            ),
            "quality_policy": _as_dict(memory.get("quality_policy")),
            "source_refs": _as_list(memory.get("source_refs")),
        }
        contract["long_term_constraints"] = long_term_constraints

        budgeted = self.compile_information_budget(
            contract,
            max_hard_must_show=max_hard_must_show,
            max_soft_guidance=max_soft_guidance,
        )
        contract.update(budgeted)

        quality_patch = self._quality_extensions_patch(long_term_constraints)
        if quality_patch:
            existing = _as_dict(contract.get("quality_extensions"))
            contract["quality_extensions"] = _merge_quality_extensions(existing, quality_patch)

        return contract

    @staticmethod
    def compact_selection_trace(trace: dict | None) -> dict:
        trace = _as_dict(trace)
        return {
            key: trace.get(key)
            for key in (
                "candidate_count", "eligible_count", "selected_count", "dropped_count",
                "excluded_lifecycle_count", "excluded_validity_count", "deduplicated_count",
                "selection_limit", "truncated", "hard_fact_dropped",
                "selected_by_chapter", "selected_by_bucket",
            )
            if key in trace
        } | {
            "selected_proposition_ids": [
                item.get("proposition_id")
                for item in _as_list(trace.get("selected"))
                if isinstance(item, Mapping) and item.get("proposition_id")
            ]
        }

    def compile_information_budget(
        self,
        scene_contract: dict,
        *,
        max_hard_must_show: int = 5,
        max_soft_guidance: int = 3,
    ) -> dict:
        from app.services.information_budget_compiler import get_information_budget_compiler

        return get_information_budget_compiler().compile_scene_contract_budget(
            scene_contract,
            max_hard_must_show=max_hard_must_show,
            max_soft_guidance=max_soft_guidance,
        )

    def compile_quality_policy(self, quality_memory: dict | None) -> dict:
        quality_memory = _as_dict(quality_memory)
        actions = []
        for pattern in _as_list(quality_memory.get("recent_quality_patterns")):
            if not isinstance(pattern, Mapping):
                continue
            ptype = str(pattern.get("type") or "")
            if not ptype:
                continue
            try:
                count = int(pattern.get("count", 0) or 0)
            except (TypeError, ValueError):
                count = 0
            # enforcement 分级：质量记忆累计次数不能改变 enforcement（memory_can_promote 永远为 False）
            # count >= 5 不再晋升为 quality_gate_candidate（不再阻断 commit）
            # 高累计次数只提高优先级和写作提醒强度，enforcement 保持 advisory
            if count >= 5:
                level = "writer_constraint"  # 高优先级写作约束，但不阻断
            elif count >= 3:
                level = "writer_constraint"
            elif count >= 2:
                level = "planning_warning"
            else:
                level = "advisory_memory"
            if count < 2:
                continue
            actions.append({
                "type": ptype,
                "count": count,
                "level": level,
                "instruction": _clip(pattern.get("suggestion") or _default_policy_instruction(ptype), 220),
                "last_seen_chapter": int(pattern.get("last_seen_chapter", 0) or 0),
                "last_seen_scene": int(pattern.get("last_seen_scene", 0) or 0),
            })
        return {
            "schema_version": 1,
            "policy_actions": actions[:12],
            "writer_constraints": [a for a in actions if a["level"] in {"writer_constraint", "quality_gate_candidate"}][:8],
            "planning_warnings": [a for a in actions if a["level"] == "planning_warning"][:8],
            "quality_gate_candidates": [a for a in actions if a["level"] == "quality_gate_candidate"][:8],
        }

    def _compile_prior_facts(self, state: dict, *, max_items: int) -> list[dict]:
        facts = []
        for key in ("established_facts", "completed_events", "active_constraints"):
            for item in _as_list(state.get(key)):
                text = _clip(item, 220)
                if text:
                    facts.append({
                        "type": key,
                        "text": text,
                        "constraint": "preserve_consistency",
                    })
                if len(facts) >= max_items:
                    return facts
        return facts

    def _compile_character_constraints(self, states: dict, *, max_items: int) -> list[dict]:
        constraints = []
        for name, state in list(states.items())[:max_items]:
            if isinstance(state, Mapping):
                snapshot = {
                    "location": _clip(state.get("location"), 120),
                    "physical_state": _clip(state.get("physical_state") or state.get("body_state"), 120),
                    "emotional_state": _clip(state.get("emotional_state"), 120),
                    "knowledge_state": _clip(state.get("knowledge_state"), 160),
                    "current_intent": _clip(state.get("current_intent") or state.get("intent"), 160),
                    "evidence_span": _clip(state.get("evidence_span"), 180),
                }
                snapshot = {k: v for k, v in snapshot.items() if v}
            else:
                snapshot = {"summary": _clip(state, 220)}
            if snapshot:
                constraints.append({
                    "character_name": str(name),
                    "state": snapshot,
                    "constraint": "do_not_contradict_without_explicit_transition",
                })
        return constraints

    def _summarize_propositions(self, propositions: list[dict], *, max_items: int) -> dict:
        grouped: dict[str, list[dict]] = {
            "confirmed_current": [],
            "uncertain_or_inferred": [],
            "relationship_or_knowledge": [],
            "future_or_plan": [],
        }
        constraints = []
        for prop in propositions[:max_items]:
            subject = _clip(prop.get("subject_name"), 80)
            predicate = _clip(prop.get("predicate_name"), 80)
            obj = _clip(prop.get("object_name"), 80)
            source = _clip(prop.get("source_text"), 180)
            category = str(prop.get("predicate_category") or "")
            truth_layer = str(prop.get("truth_layer") or "")
            certainty = str(prop.get("certainty") or "")
            summary = {
                "proposition_id": prop.get("proposition_id") or prop.get("id"),
                "chapter_number": prop.get("chapter_number"),
                "scene_index": prop.get("scene_index"),
                "claim": " ".join(part for part in [subject, predicate, obj] if part),
                "truth_layer": truth_layer,
                "certainty": certainty,
                "predicate_category": category,
                "source_text": source,
            }
            if truth_layer == "current" and certainty in {"confirmed", "reported"}:
                grouped["confirmed_current"].append(summary)
                constraints.append({
                    **self._constraint_claim(summary),
                    "constraint": "preserve_established_claim",
                    "allowed_change": "only_with_explicit_on_page_reversal_or_new_evidence",
                })
            elif category in {"relationship", "knowledge", "intention"}:
                grouped["relationship_or_knowledge"].append(summary)
                constraints.append({
                    **self._constraint_claim(summary),
                    "constraint": "respect_character_knowledge_and_relationship",
                    "allowed_change": "show_transition_before_state_change",
                })
            elif truth_layer in {"plan", "future_hint", "inference", "rumor"}:
                grouped["future_or_plan"].append(summary)
                constraints.append({
                    **self._constraint_claim(summary),
                    "constraint": "do_not_upgrade_to_confirmed_fact_without_evidence",
                    "allowed_change": "keep_as_suspicion_or_plan_until_revealed",
                })
            else:
                grouped["uncertain_or_inferred"].append(summary)
        return {
            "schema_version": 1,
            "groups": {k: v[:8] for k, v in grouped.items()},
            "constraints": constraints[:max_items],
        }

    @staticmethod
    def _constraint_claim(summary: dict) -> dict:
        return {
            key: value for key, value in summary.items()
            if key != "source_text"
        } | {
            "source_ref": "chapter_{chapter}_scene_{scene}_proposition_{prop}".format(
                chapter=summary.get("chapter_number"),
                scene=summary.get("scene_index"),
                prop=summary.get("proposition_id"),
            )
        }

    def _select_scene_foreshadowing_constraints(
        self,
        constraints: list[dict],
        scene_contract: dict,
        *,
        max_items: int,
    ) -> list[dict]:
        import json

        context_key = normalize_entity_key(json.dumps(scene_contract or {}, ensure_ascii=False, default=str))
        priority_score = {"critical": 30, "high": 20, "moderate": 10, "low": 0}
        action_score = {"overdue_reveal": 100, "reveal": 90, "plant": 70, "maintain": 60, "protect_secret": 25}
        ranked = []
        for item in constraints:
            if not isinstance(item, dict):
                continue
            score = action_score.get(str(item.get("action") or ""), 0)
            score += priority_score.get(str(item.get("priority") or ""), 0)
            names = [item.get("name"), *_as_list(item.get("related_characters"))]
            if any(normalize_entity_key(name) in context_key for name in names if normalize_entity_key(name)):
                score += 50
            ranked.append((
                -score,
                str(item.get("foreshadowing_id") or ""),
                item,
            ))
        ranked.sort(key=lambda row: (row[0], row[1]))
        urgent = [row[2] for row in ranked if row[2].get("action") in {"reveal", "overdue_reveal"}]
        selected = list(urgent)
        for _, _, item in ranked:
            if len(selected) >= max(max_items, len(urgent)):
                break
            if item not in selected:
                selected.append(item)
        return selected

    def _summarize_foreshadowing(
        self,
        foreshadowing: list[dict],
        *,
        chapter_number: int | None,
        max_items: int,
    ) -> dict:
        grouped: dict[str, list[dict]] = {
            "plant_now": [],
            "maintain_now": [],
            "reveal_now": [],
            "protect_secret": [],
            "overdue": [],
        }
        items: list[dict] = []
        constraints: list[dict] = []
        action_score = {"overdue_reveal": 100, "reveal": 90, "plant": 70, "maintain": 60, "protect_secret": 20}
        priority_score = {"critical": 30, "high": 20, "moderate": 10, "low": 0}
        ranked_lines: list[tuple[int, str, dict, str]] = []
        for line in foreshadowing:
            action = self._foreshadowing_action(line, chapter_number)
            if not action:
                continue
            score = action_score.get(action, 0) + priority_score.get(str(line.get("priority") or ""), 0)
            ranked_lines.append((-score, str(line.get("id") or line.get("name") or ""), line, action))
        ranked_lines.sort(key=lambda row: (row[0], row[1]))

        for _, _, line, action in ranked_lines[:max_items]:
            reveal_allowed = action in {"reveal", "overdue_reveal"}
            base = {
                "foreshadowing_id": line.get("id") or line.get("foreshadowing_line_id"),
                "name": _clip(line.get("name"), 100),
                "status": _clip(line.get("status"), 40),
                "priority": _clip(line.get("priority"), 40),
                "action": action,
                "bury_window_start": line.get("bury_window_start"),
                "bury_window_end": line.get("bury_window_end"),
                "maintenance_window_start": line.get("maintenance_window_start"),
                "maintenance_window_end": line.get("maintenance_window_end"),
                "reveal_window_start": line.get("reveal_window_start"),
                "reveal_window_end": line.get("reveal_window_end"),
                "latest_safe_reveal_chapter": line.get("latest_safe_reveal_chapter"),
                "reader_intended_state": _clip(line.get("reader_intended_state"), 80),
                "reader_allowed_interpretations": _as_list(line.get("reader_allowed_interpretations"))[:5],
                "reader_forbidden_interpretations": _as_list(line.get("reader_forbidden_interpretations"))[:5],
                "related_characters": _as_list(line.get("related_characters"))[:8],
                "source_text": _clip(line.get("name") or line.get("secret_truth_type"), 120),
            }
            if reveal_allowed:
                base["secret_canonical_statement"] = _clip(line.get("secret_canonical_statement"), 220)
            items.append(base)

            if action == "plant":
                constraint = {
                    **base,
                    "constraint": "plant_clue_without_confirming_secret",
                    "allowed_change": "add subtle evidence; keep truth as suspicion or misdirection",
                }
                grouped["plant_now"].append(base)
            elif action == "maintain":
                constraint = {
                    **base,
                    "constraint": "maintain_reader_state_without_resolution",
                    "allowed_change": "reinforce or complicate clue; do not resolve",
                }
                grouped["maintain_now"].append(base)
            elif action == "reveal":
                constraint = {
                    **base,
                    "constraint": "resolve_or_advance_reveal_window",
                    "allowed_change": "confirm truth only with on-page evidence and aftermath",
                }
                grouped["reveal_now"].append(base)
            elif action == "overdue_reveal":
                constraint = {
                    **base,
                    "constraint": "overdue_reveal_requires_resolution_or_explicit_deferral",
                    "allowed_change": "resolve, advance, or explicitly defer with reason",
                }
                grouped["overdue"].append(base)
            else:
                constraint = {
                    **base,
                    "constraint": "forbid_premature_reveal",
                    "allowed_change": "may hint at surface clue; do not confirm hidden truth",
                }
                grouped["protect_secret"].append(base)
            constraints.append(constraint)

        return {
            "schema_version": 1,
            "items": items[:max_items],
            "groups": {k: v[:8] for k, v in grouped.items()},
            "constraints": constraints[:max_items],
        }

    def _foreshadowing_action(self, line: dict, chapter_number: int | None) -> str:
        status = str(line.get("status") or "")
        if status in {"resolved", "aborted"}:
            return ""
        chapter = int(chapter_number or 0)
        bury_start = _int_or_none(line.get("bury_window_start"))
        bury_end = _int_or_none(line.get("bury_window_end")) or bury_start
        maintenance_start = _int_or_none(line.get("maintenance_window_start"))
        maintenance_end = _int_or_none(line.get("maintenance_window_end")) or maintenance_start
        reveal_start = _int_or_none(line.get("reveal_window_start"))
        reveal_end = _int_or_none(line.get("reveal_window_end")) or reveal_start
        latest_safe_reveal = _int_or_none(line.get("latest_safe_reveal_chapter"))

        overdue_boundary = latest_safe_reveal or reveal_end
        if chapter and overdue_boundary and chapter > overdue_boundary and status != "resolved":
            return "overdue_reveal"
        if chapter and reveal_start and reveal_start <= chapter <= (reveal_end or reveal_start):
            return "reveal"
        if chapter and maintenance_start and maintenance_start <= chapter <= (maintenance_end or maintenance_start):
            return "maintain"
        if chapter and bury_start and bury_start <= chapter <= (bury_end or bury_start):
            return "plant"
        if reveal_start and (not chapter or chapter < reveal_start):
            return "protect_secret"
        if status in {"planned", "active", "dormant", "revealing"}:
            return "protect_secret"
        return ""

    def _quality_extensions_patch(self, constraints: dict) -> dict:
        quality_policy = _as_dict(constraints.get("quality_policy"))
        writer_constraints = _as_list(quality_policy.get("writer_constraints"))
        planning_warnings = _as_list(quality_policy.get("planning_warnings"))
        patch: dict[str, Any] = {"schema_version": 1}
        if writer_constraints:
            patch["quality_memory_guidance"] = [
                item.get("instruction")
                for item in writer_constraints
                if isinstance(item, Mapping) and item.get("instruction")
            ][:8]
        if planning_warnings:
            patch["planning_quality_warnings"] = planning_warnings[:8]
        if quality_policy.get("quality_gate_candidates"):
            patch["quality_gate_candidates"] = _as_list(quality_policy.get("quality_gate_candidates"))[:8]
        if constraints.get("proposition_constraints"):
            patch["proposition_guardrails"] = [
                {
                    "proposition_id": item.get("proposition_id"),
                    "constraint": item.get("constraint", ""),
                }
                for item in _as_list(constraints.get("proposition_constraints"))[:12]
                if isinstance(item, Mapping)
            ]
        if constraints.get("character_state_constraints"):
            patch["character_state_guardrails"] = _as_list(constraints.get("character_state_constraints"))[:8]
        if constraints.get("foreshadowing_constraints"):
            patch["foreshadowing_guardrails"] = [
                {
                    "name": item.get("name", ""),
                    "action": item.get("action", ""),
                    "constraint": item.get("constraint", ""),
                    "reader_intended_state": item.get("reader_intended_state", ""),
                }
                for item in _as_list(constraints.get("foreshadowing_constraints"))[:12]
                if isinstance(item, Mapping)
            ]
        return {k: v for k, v in patch.items() if k == "schema_version" or v}


def _merge_quality_extensions(existing: dict, patch: dict) -> dict:
    merged = dict(existing or {})
    merged.setdefault("schema_version", 1)
    for key, value in (patch or {}).items():
        if key == "schema_version":
            continue
        if isinstance(value, list):
            base = merged.get(key)
            if not isinstance(base, list):
                base = []
            seen = {str(item) for item in base}
            for item in value:
                marker = str(item)
                if marker not in seen:
                    base.append(item)
                    seen.add(marker)
            merged[key] = base
        elif isinstance(value, dict):
            base = merged.get(key)
            if not isinstance(base, dict):
                base = {}
            base.update(value)
            merged[key] = base
        else:
            merged.setdefault(key, value)
    return merged


def _default_policy_instruction(pattern_type: str) -> str:
    return f"Watch recurring quality pattern: {pattern_type}."


def _int_or_none(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


_compiler: StructuredMemoryCompiler | None = None


def get_structured_memory_compiler() -> StructuredMemoryCompiler:
    global _compiler
    if _compiler is None:
        _compiler = StructuredMemoryCompiler()
    return _compiler
