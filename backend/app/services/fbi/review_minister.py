from __future__ import annotations

import hashlib
import re
from typing import Any

from app.models.chapter_review import ChapterReviewCase
from app.models.fbi_diagnosis import (
    FBIAcceptanceCriterion,
    FBIAuthority,
    FBIConflictDecision,
    FBIDependencyPolicy,
    FBIProtectionBoundary,
    FBIRepairIntent,
    FBIRevisionBlueprint,
    FBIReviewDiagnosis,
    FBIReviewDiagnosisSet,
)
from app.models.review_case_file import ReviewCaseFile
from app.services.fbi.blueprint_route_registry import (
    DETERMINISTIC_PATCH_OPS,
    INTENT_OPERATIONS,
    get_blueprint_route,
    route_family_for_metric,
)
from app.services.fbi.repair_goal_grouper import RepairGoalGrouper
from app.services.fbi.normalization_rule_engine import _query_active_rule_family
from app.services.review_issue_localizer import enrich_review_issue_locations
from app.services.review_issue_semantics import normalize_violation_semantics
from app.services.skill_metric_repair_registry import (
    get_metric_spec,
    implemented_tool_operations,
)
from app.utils.dash_artifacts import DEFAULT_DASH_ARTIFACT_MAX_PER_1000


_ANTI_AI_PUNCTUATION = {
    "ai_punctuation_artifact",
    "explanatory_punctuation_artifact",
    "dash_density",
    "dash_per_1000",
}
_ANTI_AI_LOCAL = {
    "tier1_hit_count",
    "sentence_shell_count",
    "abstraction_over_budget",
    "structure_word_cluster_count",
}
_ANTI_AI_DISCOURSE = {"paragraph_shape_repeat_count", "uniform_sentence_streak_max"}
_FACT_TYPES = {
    "fact_conflict",
    "internal_conflict",
    "identity_conflict",
    "setting_conflict",
    "spatial_conflict",
    "ownership_conflict",
    "knowledge_boundary_violation",
    "timeline_conflict",
    "temporal_conflict",
    "clue_provenance_error",
    "clue_provenance_error_proposition",
    "unprovenanced_clue",
}
_RHYTHM_TYPES = {
    "flat_pressure_ramp",
    "low_conflict_density",
    "weak_curiosity_engine",
    "weak_opening_hook",
    "low_event_density",
    "low_reversal_density",
    "weak_chapter_end_hook",
    "missing_micro_payoff",
    "pacing",
    "rhythm",
    "dense_paragraph",
    "reader_breathing",
    "uniform_sentence_streak",
    "long_paragraph",
}
_POV_TYPES = {
    "pov",
    "pov_consistency",
    "single_pov_per_scene",
    "head_hopping",
    "head_hopping_count",
    "voice_fingerprint",
}
_STRUCTURE_TYPES = {
    "missing_must_show",
    "ending_state_not_reached",
    "scene_structure",
    "goal_present",
    "conflict_present",
    "value_change",
    "scene_goal_missing",
}
_TRANSFORM_OPERATIONS_WITHOUT_REPLACEMENT = {
    "normalize_structure_words",
    "replace_tier1_ai_flavor_terms",
    "cleanup_ai_flavor_window",
    "trim_discourse_window",
    "vary_sentence_shape",
    "vary_sentence_length_window",
    "insert_functional_breathing_paragraph",
    "smooth_abrupt_shift",
    "rewrite_voice_window",
    "split_paragraph",
    "normalize_punctuation",
    # 问题29修复：llm_creative_rewrite 是 LLM 创作标记，不需要 replacement，
    # 由 _repair_local_patch 路径调用 SceneRepairer 执行 LLM 创作。
    "llm_creative_rewrite",
}
_TRANSFORM_OPERATIONS_WITHOUT_REPLACEMENT.update(implemented_tool_operations())


class FBIReviewMinister:
    """Normalize FBI review findings into repair-ready diagnoses.

    This service is deliberately deterministic. It does not judge prose by
    itself and does not modify text; it translates collected findings into the
    repair language that FBI planners and tools can consume.
    """

    def diagnose_case(self, case: ChapterReviewCase) -> FBIReviewDiagnosisSet:
        violations = self._collect_case_violations(case)
        return self.diagnose_violations(case, violations)

    def diagnose_violations(
        self,
        case: ChapterReviewCase,
        violations: list[dict[str, Any]],
    ) -> FBIReviewDiagnosisSet:
        """Normalize findings into semantic goals before physical placement.

        A diagnostic/fallback span is evidence, not permission to write there.
        Consequently this phase no longer calls ``EditWindowGrouper``.  It
        first deduplicates contract obligations into RepairGoals, records a
        placement decision for every goal, and leaves physical window merging
        to ``EditWindowPlanner`` after a write anchor has been validated.
        """
        normalized_violations: list[dict[str, Any]] = []
        for raw in violations:
            violation = normalize_violation_semantics(self._localize_violation(case, dict(raw or {})))
            normalized_violations.append(violation)
        self._scope_duplicate_issue_ids(normalized_violations)

        scene_texts = {
            packet.scene_index: packet.candidate_text or ""
            for packet in case.scene_packets or []
            if isinstance(packet.scene_index, int)
        }
        goal_grouper = RepairGoalGrouper()
        authority_catalog = goal_grouper.authority_catalog(
            scene_contracts={
                packet.scene_index: dict(packet.scene_contract or {})
                for packet in case.scene_packets or []
                if isinstance(packet.scene_index, int)
            },
            chapter_contract=dict(case.chapter_outline_contract or {}),
        )
        repair_goals, goal_trace = goal_grouper.group_findings(
            normalized_violations,
            authority_catalog=authority_catalog,
        )
        findings_by_id = {
            self._issue_id(item): item
            for item in normalized_violations
            if self._issue_id(item)
        }

        diagnoses: list[FBIReviewDiagnosis] = []
        issue_status: dict[str, str] = {}
        placement_required = 0
        placement_resolved = 0
        for goal in repair_goals:
            members = [dict(item) for item in goal.source_findings]
            representative = dict(members[0] if members else {})
            primary_issue_id = goal.source_issue_ids[0] if goal.source_issue_ids else goal.goal_id
            representative["issue_id"] = primary_issue_id
            representative["repair_goal"] = goal.model_dump()
            representative["desired_state"] = goal.desired_state
            representative["prohibited_state"] = goal.prohibited_state
            representative["mutation_kind"] = goal.mutation_kind
            representative["blocks_commit"] = goal.blocks_commit
            if goal.desired_state:
                representative["expected_behavior"] = goal.desired_state
            goal_scene_index = goal.owner_scope.get("scene_index")
            if isinstance(goal_scene_index, int):
                representative["source_scene"] = goal_scene_index
                representative["scene_index"] = goal_scene_index
                representative["_candidate_text"] = scene_texts.get(goal_scene_index, "")

            member_diagnoses = [
                self._diagnose_violation(
                    case,
                    {
                        **member,
                        "repair_goal": goal.model_dump(),
                        "desired_state": goal.desired_state,
                        "prohibited_state": goal.prohibited_state,
                        "mutation_kind": goal.mutation_kind,
                        "blocks_commit": goal.blocks_commit,
                    },
                    self._issue_id(member) or primary_issue_id,
                )
                for member in members
            ]
            fallback_diagnosis = (
                member_diagnoses[0]
                if member_diagnoses
                else self._diagnose_violation(case, representative, primary_issue_id)
            )
            diagnosis = self._merge_goal_diagnoses(
                member_diagnoses,
                fallback=fallback_diagnosis,
            )
            diagnosis.diagnosis_id = "diag_" + hashlib.sha256(
                f"{case.case_id}:{goal.goal_id}".encode("utf-8")
            ).hexdigest()[:16]
            diagnosis.issue_ids = list(goal.source_issue_ids or [primary_issue_id])
            diagnosis.repair_goal_ids = [goal.goal_id]
            diagnosis.repair_intent.intent_id = f"intent_{diagnosis.diagnosis_id}"
            diagnosis.repair_intent.issue_ids = list(diagnosis.issue_ids)
            diagnosis.blocks_commit = goal.blocks_commit
            diagnosis.placement = goal_grouper.placement_for_goal(goal, scene_texts)
            diagnosis.source_trace = [
                *diagnosis.source_trace,
                f"repair_goal:{goal.goal_id}",
                *[f"source_finding:{issue_id}" for issue_id in diagnosis.issue_ids],
            ]
            placement_payload = diagnosis.placement.model_dump()
            diagnosis.revision_blueprint.edit_window = {
                **(diagnosis.revision_blueprint.edit_window or {}),
                "placement": placement_payload,
                "read_context_spans": placement_payload["read_context_spans"],
                "diagnostic_evidence_spans": placement_payload["diagnostic_evidence_spans"],
                "write_anchor": placement_payload["write_anchor"],
            }
            tool_blueprint = diagnosis.revision_blueprint.tool_blueprint
            if isinstance(tool_blueprint, dict):
                tool_blueprint["source_goal_ids"] = [goal.goal_id]
                if (
                    diagnosis.placement.status != "resolved"
                    and goal.mutation_kind in {"insert", "rewrite_window", "global_transform"}
                    and tool_blueprint.get("operation") == "llm_creative_rewrite"
                ):
                    # Evidence windows are deliberately not promoted to write
                    # anchors.  SceneRepairer receives the full chapter and must
                    # select the natural insertion/rewrite location itself.
                    tool_blueprint["target_span"] = ""
                    tool_blueprint["anchor_text"] = ""
                    tool_blueprint["placement_required"] = True
            goal.acceptance_criteria = list(diagnosis.acceptance_criteria)
            diagnoses.append(diagnosis)
            for issue_id in diagnosis.issue_ids:
                issue_status[issue_id] = "diagnosed"
            if diagnosis.placement.status == "resolved":
                placement_resolved += 1
            else:
                placement_required += 1

        diagnosis_set = FBIReviewDiagnosisSet(
            case_id=case.case_id,
            review_round=case.review_round,
            diagnoses=diagnoses,
            repair_goals=repair_goals,
            issue_status=issue_status,
            trace={
                "component": "fbi_review_minister",
                "input_violations": len(violations),
                "diagnoses": len(diagnoses),
                "families": self._family_counts(diagnoses),
                "blueprint_status": self._blueprint_status_counts(diagnoses),
                "repair_goal_grouper": goal_trace,
                "placement": {
                    "resolved": placement_resolved,
                    "required": placement_required,
                },
                "edit_window_grouper": {
                    "mode": "deferred_until_validated_write_anchor",
                    "window_cases": 0,
                    "compound_windows": 0,
                    "covered_issue_ids": sorted(issue_status),
                },
            },
        )
        self._attach_to_case_file(case, diagnosis_set)
        return diagnosis_set

    def annotate_violations(
        self,
        case: ChapterReviewCase,
        violations: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], FBIReviewDiagnosisSet]:
        diagnosis_set = self.diagnose_violations(case, violations)
        by_issue = diagnosis_set.by_issue_id()
        goals_by_id = {goal.goal_id: goal for goal in diagnosis_set.repair_goals}
        annotated: list[dict[str, Any]] = [
            normalize_violation_semantics(self._localize_violation(case, dict(raw or {})))
            for raw in violations
        ]
        self._scope_duplicate_issue_ids(annotated)
        projected: list[dict[str, Any]] = []
        for violation in annotated:
            issue_id = self._issue_id(violation)
            violation.setdefault("issue_id", issue_id)
            diagnosis = by_issue.get(issue_id)
            if diagnosis is not None:
                violation["fbi_diagnosis"] = diagnosis.model_dump()
                violation["repair_intent"] = diagnosis.repair_intent.model_dump()
                violation["revision_blueprint"] = diagnosis.revision_blueprint.model_dump()
                violation["tool_blueprint"] = dict(diagnosis.revision_blueprint.tool_blueprint or {})
                violation["diagnosis_id"] = diagnosis.diagnosis_id
                violation["repair_goal_ids"] = list(diagnosis.repair_goal_ids)
                violation["placement"] = diagnosis.placement.model_dump()
                if diagnosis.repair_goal_ids:
                    goal = goals_by_id.get(diagnosis.repair_goal_ids[0])
                    if goal is not None:
                        violation["repair_goal"] = goal.model_dump()
                violation.setdefault("expected_after_repair", {})
                if isinstance(violation["expected_after_repair"], dict):
                    violation["expected_after_repair"].update({
                        "target_behavior": diagnosis.repair_intent.target_behavior,
                        "acceptance_criteria": [
                            item.model_dump() for item in diagnosis.acceptance_criteria
                        ],
                    })
            projected.append(violation)
        return projected, diagnosis_set

    @classmethod
    def _scope_duplicate_issue_ids(cls, violations: list[dict[str, Any]]) -> None:
        """Disambiguate reused finding ids across distinct ownership scopes."""
        scopes_by_id: dict[str, set[str]] = {}
        for violation in violations:
            issue_id = cls._issue_id(violation)
            if not issue_id:
                continue
            scene_index = cls._scene_index(violation)
            scope = str(violation.get("obligation_scope") or violation.get("scope") or "scene")
            scope_key = f"{scope}:{scene_index if scene_index is not None else 'chapter'}"
            scopes_by_id.setdefault(issue_id, set()).add(scope_key)
        duplicated = {
            issue_id for issue_id, scopes in scopes_by_id.items() if len(scopes) > 1
        }
        if not duplicated:
            return
        for violation in violations:
            issue_id = cls._issue_id(violation)
            if issue_id not in duplicated:
                continue
            scene_index = cls._scene_index(violation)
            scope = str(violation.get("obligation_scope") or violation.get("scope") or "scene")
            scope_key = f"{scope}:{scene_index if scene_index is not None else 'chapter'}"
            suffix = hashlib.sha256(scope_key.encode("utf-8")).hexdigest()[:8]
            violation["source_issue_id"] = issue_id
            violation["issue_id"] = f"{issue_id}:scope:{suffix}"

    @staticmethod
    def _localize_violation(case: ChapterReviewCase, violation: dict[str, Any]) -> dict[str, Any]:
        # 保留“上游是否真正给出定位证据”。localizer 会为无证据问题
        # 生成低置信度上下文窗口，该窗口只能用于审阅，不能被误当作
        # 事实冲突的可写回落点。用私有字段区分两者，不改变对外协议。
        if "_location_supplied_upstream" not in violation:
            evidence = violation.get("evidence") if isinstance(violation.get("evidence"), dict) else {}
            violation["_location_supplied_upstream"] = bool(
                str(violation.get("target_span") or "").strip()
                or violation.get("evidence_spans")
                or evidence.get("evidence_spans")
            )
        scene_index = FBIReviewMinister._scene_index(violation)
        scene_text = ""
        for packet in case.scene_packets or []:
            if packet.scene_index == scene_index:
                scene_text = packet.candidate_text or ""
                break
        if not scene_text and case.scene_packets:
            for packet in case.scene_packets:
                if packet.candidate_text:
                    scene_text = packet.candidate_text
                    break
        if not scene_text:
            return violation
        violation["_candidate_text"] = scene_text
        try:
            localized = enrich_review_issue_locations(violation, scene_text)
        except Exception:
            return violation
        if isinstance(localized, dict):
            localized["_candidate_text"] = scene_text
            return localized
        return violation

    def _diagnose_violation(
        self,
        case: ChapterReviewCase,
        violation: dict[str, Any],
        issue_id: str,
    ) -> FBIReviewDiagnosis:
        values = self._semantic_values(violation)
        family = self._issue_family(values, violation)
        scene_index = self._scene_index(violation)
        diagnosis_id = self._diagnosis_id(case, issue_id, family, scene_index)
        repair_intent = self._repair_intent(diagnosis_id, issue_id, family, violation)
        authority = self._authority(violation, family)
        boundary = self._protection_boundary(violation, authority)
        criteria = self._acceptance_criteria(family, violation, repair_intent)
        revision_blueprint = self._revision_blueprint(
            diagnosis_id=diagnosis_id,
            issue_id=issue_id,
            family=family,
            violation=violation,
            repair_intent=repair_intent,
            boundary=boundary,
            criteria=criteria,
        )
        if (
            revision_blueprint.status == "ready"
            and revision_blueprint.tool_blueprint
            and not repair_intent.patch_plan
        ):
            repair_intent.patch_plan = [dict(revision_blueprint.tool_blueprint)]
        decision = self._conflict_decision(violation, authority)
        return FBIReviewDiagnosis(
            diagnosis_id=diagnosis_id,
            issue_ids=[issue_id],
            scene_index=scene_index,
            scope=str(violation.get("scope") or ("scene" if scene_index is not None else "chapter")),
            issue_family=family,
            problem_statement=str(
                violation.get("detail")
                or violation.get("reason")
                or violation.get("type")
                or violation.get("violation_type")
                or "review finding"
            ),
            authorities=[authority] if authority.statement else [],
            conflict_decisions=[decision] if decision is not None else [],
            repair_intent=repair_intent,
            revision_blueprint=revision_blueprint,
            protection_boundary=boundary,
            acceptance_criteria=criteria,
            dependency_policy=self._dependency_policy(family),
            blocks_commit=bool(violation.get("blocks_commit", True)),
            source_trace=self._source_trace(violation),
        )

    @classmethod
    def _merge_goal_diagnoses(
        cls,
        diagnoses: list[FBIReviewDiagnosis],
        *,
        fallback: FBIReviewDiagnosis,
    ) -> FBIReviewDiagnosis:
        """Merge all validator evidence for one semantic repair goal."""

        if not diagnoses:
            return fallback.model_copy(deep=True)

        def _dedupe_models(items):
            seen: set[str] = set()
            merged = []
            for item in items:
                payload = item.model_dump() if hasattr(item, "model_dump") else item
                key = repr(sorted(payload.items(), key=lambda pair: str(pair[0])))
                if key in seen:
                    continue
                seen.add(key)
                merged.append(item)
            return merged

        def _dedupe_dicts(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
            seen: set[str] = set()
            merged: list[dict[str, Any]] = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                key = repr(sorted(item.items(), key=lambda pair: str(pair[0])))
                if key in seen:
                    continue
                seen.add(key)
                merged.append(dict(item))
            return merged

        # Any creative route makes the semantic goal creative.  This prevents a
        # deterministic representative from silently downgrading a richer
        # validator diagnosis selected later in lexical order.
        selected = next(
            (
                diagnosis
                for diagnosis in diagnoses
                if (
                    diagnosis.revision_blueprint.tool_blueprint or {}
                ).get("operation") == "llm_creative_rewrite"
            ),
            next(
                (
                    diagnosis
                    for diagnosis in diagnoses
                    if diagnosis.revision_blueprint.status == "ready"
                ),
                diagnoses[0],
            ),
        )
        merged = selected.model_copy(deep=True)
        merged.issue_ids = list(dict.fromkeys(
            issue_id
            for diagnosis in diagnoses
            for issue_id in diagnosis.issue_ids
            if issue_id
        ))
        merged.problem_statement = "; ".join(dict.fromkeys(
            diagnosis.problem_statement
            for diagnosis in diagnoses
            if diagnosis.problem_statement
        ))[:1000]
        merged.authorities = _dedupe_models([
            item for diagnosis in diagnoses for item in diagnosis.authorities
        ])
        merged.conflict_decisions = _dedupe_models([
            item for diagnosis in diagnoses for item in diagnosis.conflict_decisions
        ])
        merged.acceptance_criteria = _dedupe_models([
            item for diagnosis in diagnoses for item in diagnosis.acceptance_criteria
        ])
        merged.protection_boundary = cls._merge_protection_boundaries([
            diagnosis.protection_boundary for diagnosis in diagnoses
        ])
        merged.blocks_commit = any(diagnosis.blocks_commit for diagnosis in diagnoses)
        merged.source_trace = list(dict.fromkeys(
            trace
            for diagnosis in diagnoses
            for trace in diagnosis.source_trace
            if trace
        ))

        intent = merged.repair_intent.model_copy(deep=True)
        intent.issue_ids = list(merged.issue_ids)
        intent.target_metrics = _dedupe_dicts([
            metric
            for diagnosis in diagnoses
            for metric in diagnosis.repair_intent.target_metrics
        ])
        intent.patch_plan = _dedupe_dicts([
            patch
            for diagnosis in diagnoses
            for patch in diagnosis.repair_intent.patch_plan
        ])
        intent.source_trace = list(dict.fromkeys(
            trace
            for diagnosis in diagnoses
            for trace in diagnosis.repair_intent.source_trace
            if trace
        ))
        merged.repair_intent = intent

        merged.dependency_policy = FBIDependencyPolicy(
            depends_on=list(dict.fromkeys(
                item
                for diagnosis in diagnoses
                for item in diagnosis.dependency_policy.depends_on
            )),
            blocks=list(dict.fromkeys(
                item
                for diagnosis in diagnoses
                for item in diagnosis.dependency_policy.blocks
            )),
            must_run_before_domains=list(dict.fromkeys(
                item
                for diagnosis in diagnoses
                for item in diagnosis.dependency_policy.must_run_before_domains
            )),
            can_batch_with=list(dict.fromkeys(
                item
                for diagnosis in diagnoses
                for item in diagnosis.dependency_policy.can_batch_with
            )),
        )
        return merged

    @staticmethod
    def _merge_protection_boundaries(
        boundaries: list[FBIProtectionBoundary],
    ) -> FBIProtectionBoundary:
        """合并多个 protection_boundary，取最严格约束。"""
        if not boundaries:
            return FBIProtectionBoundary()
        preserve_hard_facts: list[str] = []
        preserve_protected_spans: list[str] = []
        for b in boundaries:
            preserve_hard_facts.extend(b.preserve_hard_facts or [])
            preserve_protected_spans.extend(b.preserve_protected_spans or [])
        # preserve_pov 是 string，取第一个非空值
        merged_pov = ""
        for b in boundaries:
            if b.preserve_pov:
                merged_pov = b.preserve_pov
                break
        # forbidden_operations 去重
        seen_ops: set[str] = set()
        merged_ops: list[str] = []
        for b in boundaries:
            for op in (b.forbidden_operations or []):
                if op not in seen_ops:
                    seen_ops.add(op)
                    merged_ops.append(op)
        return FBIProtectionBoundary(
            preserve_scene_markers=any(b.preserve_scene_markers for b in boundaries),
            preserve_hard_facts=list(dict.fromkeys(preserve_hard_facts)),
            preserve_protected_spans=list(dict.fromkeys(preserve_protected_spans)),
            preserve_pov=merged_pov,
            forbidden_operations=merged_ops,
        )

    def _repair_intent(
        self,
        diagnosis_id: str,
        issue_id: str,
        family: str,
        violation: dict[str, Any],
    ) -> FBIRepairIntent:
        lane, strength, max_strength, operation, fallbacks = self._lane_for_family(family, violation)
        target_behavior = str(
            violation.get("expected_behavior")
            or violation.get("repair_goal")
            or violation.get("expected")
            or violation.get("detail")
            or ""
        )
        target_metrics = [self._target_metric(violation, family)]
        patch_plan = self._patch_plan(violation, family)
        if patch_plan and not target_behavior:
            target_behavior = "Apply the explicit patch plan without changing unrelated content."
        return FBIRepairIntent(
            intent_id=f"intent_{diagnosis_id}",
            issue_ids=[issue_id],
            repair_domain=self._repair_domain_for_family(family, violation),
            repair_lane=lane,
            repair_strength=strength,
            allowed_max_strength=max_strength,
            preferred_operation=operation,
            fallback_operations=fallbacks,
            target_behavior=target_behavior,
            patch_plan=patch_plan,
            target_metrics=target_metrics,
            source_trace=self._source_trace(violation),
        )

    def _revision_blueprint(
        self,
        *,
        diagnosis_id: str,
        issue_id: str,
        family: str,
        violation: dict[str, Any],
        repair_intent: FBIRepairIntent,
        boundary: FBIProtectionBoundary,
        criteria: list[FBIAcceptanceCriterion],
    ) -> FBIRevisionBlueprint:
        metric = self._metric_name(violation)
        scene_index = self._scene_index(violation)
        anchor = self._blueprint_anchor(violation, metric)
        # 清空上一次的 route 失败原因，避免跨 violation 串味
        self._last_route_failure_reason = ""
        command = self._tool_blueprint_command(
            violation=violation,
            family=family,
            metric=metric,
            anchor=anchor,
            issue_id=issue_id,
        )
        route_failure_reason = self._last_route_failure_reason
        self._last_route_failure_reason = ""
        missing: list[str] = []
        # 问题29修复：llm_creative_rewrite 是 LLM 创作标记，不需要 anchor 和 replacement，
        # 由 _repair_local_patch 路径调用 SceneRepairer 执行 LLM 创作。
        is_llm_creative = bool(command and command.get("operation") == "llm_creative_rewrite")
        if not anchor and not is_llm_creative and family in {"rhythm", "anti_ai_local", "anti_ai_discourse", "fact", "structure", "show_evidence", "pov", "voice"}:
            missing.append("anchor")
        if command and not str(command.get("replacement") or "").strip() and command.get("operation") not in _TRANSFORM_OPERATIONS_WITHOUT_REPLACEMENT:
            missing.append("replacement")
        if not command:
            missing.append("tool_blueprint")

        status = "ready" if command and not missing else "blueprint_incomplete"
        edit_window = {
            "anchor_strategy": self._anchor_strategy(metric, family),
            "primary_anchor": anchor,
            "fallback_anchors": self._fallback_anchor_descriptors(violation),
        }
        proposed_change = {
            "mode": command.get("operation", "") if command else "",
            "text": command.get("replacement", "") if command else "",
        }
        protection_policy = {
            "preserve_facts": True,
            "preserve_pov": True,
            "preserve_scene_markers": boundary.preserve_scene_markers,
            "preserve_protected_spans": list(boundary.preserve_protected_spans),
            "forbid_new_major_event": family == "rhythm",
            "forbid_goal_resolution": metric in {
                "low_conflict_density",
                "flat_pressure_ramp",
                "weak_curiosity_engine",
                "weak_opening_hook",
            },
        }
        acceptance = {
            "criteria": [item.model_dump() for item in criteria],
            "required_effect": self._required_effect(metric, family),
            "larger_problem_preserved": True,
        }
        # 路由命中但 builder 失败时，rejection_reason 细化为具体断点（指南 §12）
        if status == "ready":
            rejection_reason = ""
        elif route_failure_reason:
            rejection_reason = f"route_builder_failed:{route_failure_reason}"
        else:
            rejection_reason = "missing_executable_blueprint_parts"
        return FBIRevisionBlueprint(
            blueprint_id=f"blueprint_{diagnosis_id}",
            status=status,
            repair_family=self._repair_domain_for_family(family, violation),
            repair_intent=repair_intent.target_behavior,
            strategy=str(command.get("operation") or repair_intent.preferred_operation) if command else repair_intent.preferred_operation,
            target_scene=scene_index,
            edit_window=edit_window,
            proposed_change=proposed_change,
            protection_policy=protection_policy,
            acceptance=acceptance,
            tool_blueprint=command or {},
            missing=sorted(set(missing)),
            rejection_reason=rejection_reason,
            route="tool_work_unit" if status == "ready" else "needs_human_review",
        )

    def _tool_blueprint_command(
        self,
        *,
        violation: dict[str, Any],
        family: str,
        metric: str,
        anchor: str,
        issue_id: str,
    ) -> dict[str, Any] | None:
        target_span = _localized_target_span(violation)
        suggested = _suggested_repair_text(violation, "")
        source = "fbi_review_minister_revision_blueprint"
        if metric in {"high_advisories", "medium_advisories"}:
            classification = str(violation.get("issue_classification") or violation.get("classification") or "")
            if classification == "manual_only" and not _first_evidence_span(violation, allow_generated=False):
                return None
            if not _has_explicit_local_evidence(violation):
                return None
            advisory_span = _first_evidence_span(violation, allow_generated=False) or target_span
            replacement = _strip_ai_flavor_terms(advisory_span)
            if advisory_span and replacement and replacement != advisory_span:
                return {
                    "operation": "replace_exact",
                    "target_span": advisory_span,
                    "anchor_text": advisory_span,
                    "old_text": advisory_span,
                    "new_text": replacement,
                    "replacement": replacement,
                    "scope": "target_scene",
                    "repair_family": "anti_ai_local",
                    "source": source,
                    "source_issue_ids": [issue_id],
                    "rationale": "Compile localized AI-flavor advisory into bounded cleanup.",
                    **_localized_locator_fields(violation, advisory_span),
                }
        # 优先走统一路由表（指南 §10）：route -> anchor -> builder -> tool_blueprint
        # 旧 if 分支保留为兼容 fallback，不能优先于 route
        route = get_blueprint_route(metric, str(violation.get("validator") or ""))
        if route is not None:
            if route.requires_llm and not str(violation.get("_candidate_text") or "").strip():
                self._last_route_failure_reason = "requires_llm_creative"
                return None
            # requires_llm 决定候选文本的生成方式，不允许跳过蓝图编译。
            # 即使后续由 LLM 创作，这里也必须先产出可验证的 anchor、
            # scope、operation 和 postconditions；否则未定位问题会被伪装成
            # ready 的整场创作任务，破坏 fail-closed 和可观测失败原因。
            route_blueprint, failure_reason = _build_tool_blueprint_from_route(
                violation=violation,
                route=route,
                issue_id=issue_id,
                source=source,
            )
            if route_blueprint is not None:
                return route_blueprint
            # 路由命中但 builder 失败，记录失败原因供 _revision_blueprint 读取（指南 §12）。
            # 不再走旧 fallback，保持失败可观测。仅当 route 未命中时才走旧逻辑。
            self._last_route_failure_reason = failure_reason or "builder_returned_none"
            return None
        if family == "anti_ai_local" and metric == "tier1_hit_count" and (target_span or anchor):
            return {
                "operation": "replace_tier1_ai_flavor_terms",
                "target_span": target_span or anchor,
                "max_replacements": 4,
                "scope": "target_scene",
                "repair_family": "anti_ai_tier1",
                "source": source,
                "source_issue_ids": [issue_id],
                "rationale": "Compile Tier 1 AI-flavor vocabulary finding into bounded term cleanup.",
            }
        if family == "anti_ai_local" and metric == "structure_word_cluster_count" and target_span:
            return {
                "operation": "normalize_structure_words",
                "target_span": target_span,
                "max_replacements": 3,
                "scope": "target_scene",
                "repair_family": "anti_ai_structure",
                "source": source,
                "source_issue_ids": [issue_id],
                "rationale": "Compile localized structure-word cluster into bounded wording cleanup.",
            }
        if family == "anti_ai_discourse" and metric in {
            "paragraph_shape_repeat_count",
            "mirrored_paragraph_opening_count",
        } and target_span:
            return {
                "operation": "vary_sentence_shape",
                "target_span": target_span,
                "scope": "target_scene",
                "repair_family": "paragraph_rhythm",
                "source": source,
                "source_issue_ids": [issue_id],
                "rationale": "Compile paragraph-shape repetition into bounded sentence-shape variation.",
            }
        if family == "anti_ai_discourse" and metric == "uniform_sentence_streak_max" and (target_span or anchor):
            return {
                "operation": "vary_sentence_length_window",
                "target_span": target_span or anchor,
                "scope": "target_scene",
                "repair_family": "sentence_rhythm",
                "source": source,
                "source_issue_ids": [issue_id],
                "rationale": "Compile uniform sentence-length streak into bounded sentence-length variation.",
            }
        if family in {"rhythm", "anti_ai_discourse"} and metric in {
            "dense_paragraph",
            "long_paragraph",
            "reader_breathing",
            "uniform_sentence_streak",
        } and target_span:
            return {
                "operation": "split_paragraph",
                "target_span": target_span,
                "scope": "target_scene",
                "repair_family": "paragraph_rhythm",
                "source": source,
                "source_issue_ids": [issue_id],
                "rationale": "Compile paragraph-breathing issue into paragraph split.",
            }
        # 方案5 B3：show_evidence 类（abstraction_over_budget 等）需要 LLM 创作具象化文本，
        # 不再走确定性模板句。这些 metric 已在路由表注册 requires_llm=True，
        # 此分支仅作为未注册时的安全兜底。
        # 问题29修复：返回 llm_creative_rewrite 而非 None，让工单能通过 LLM 创作路径执行。
        if family == "show_evidence" and metric in {
            "standalone_abstract_claims",
            "abstract_bare_count",
            "abstraction_over_budget",
        }:
            return {
                "operation": "llm_creative_rewrite",
                "target_span": target_span or anchor or "",
                "scope": "target_scene",
                "repair_family": family,
                "source": source,
                "source_issue_ids": [issue_id],
                "rationale": (
                    f"LLM creative rewrite required for {metric} "
                    f"(show_evidence fallback). "
                    f"Hint: {violation.get('detail', '')[:200]}"
                ),
                "violation_hint": {
                    "metric": metric,
                    "family": family,
                    "detail": violation.get("detail", ""),
                    "expected_behavior": violation.get("expected_behavior", ""),
                    "suggested_strategy": violation.get("suggested_strategy", ""),
                },
            }
        if family == "structure" and metric in {
            "ending_state_not_reached",
            "missing_must_show",
            "goal_present",
            "conflict_present",
            "value_change",
            "scene_goal_missing",
        }:
            return self._contract_completion_tool_blueprint(
                violation=violation,
                metric=metric,
                anchor=target_span or anchor,
                source=source,
                issue_id=issue_id,
            )
        spec = get_metric_spec(metric, str(violation.get("validator") or violation.get("source_validator") or ""))
        if spec is not None and spec.status in {"implemented_tool", "implemented_candidate_tool"} and spec.operation:
            command_target = target_span or anchor
            if spec.requires_target_span and not command_target:
                return None
            if spec.operation in INTENT_OPERATIONS:
                # Skill Metric Repair Registry declares these operations as
                # implemented tools.  Keep the intent operation in the
                # blueprint and let WorkUnitBuilder compile it into an exact
                # patch.  Converting every intent to ``llm_creative_rewrite``
                # made the registry lie about tool coverage and left orders
                # without a command whenever the LLM session was skipped.
                compiled = _build_registered_intent_blueprint(
                    violation=violation,
                    metric=metric,
                    family=family,
                    operation=spec.operation,
                    target=command_target,
                    issue_id=issue_id,
                    source=source,
                )
                if compiled is not None:
                    return compiled
                self._last_route_failure_reason = (
                    f"registered_intent_not_compilable:{spec.operation}"
                )
                return None
            command: dict[str, Any] = {
                "operation": spec.operation,
                "target_span": command_target,
                "max_replacements": 4,
                "scope": "target_scene",
                "repair_family": spec.repair_family,
                "source": source,
                "source_issue_ids": [issue_id],
                "rationale": f"Compile {metric} into {spec.operation} via Skill Metric Repair Registry.",
                "evidence": {"registry_status": spec.status, "validator": spec.validator},
            }
            return command
        if family == "fact" and target_span:
            return self._fact_tool_blueprint(violation, target_span, suggested, source, issue_id)
        # 方案5 B3：rhythm 类商业节奏问题（冲突密度、悬念、微回报等）需要 LLM 创作，
        # 不再走确定性模板句。
        # 问题29补全修复：原设计返回 None 导致 LLM 蓝图阶段生成的修复意图被丢弃，
        # 工单无法执行（no_progress 死循环）。改为返回 operation="llm_creative_rewrite"
        # 的特殊 tool_blueprint，携带 violation 信息作为 LLM 创作 hint。
        if family == "rhythm":
            return {
                "operation": "llm_creative_rewrite",
                "target_span": target_span or anchor or "",
                "scope": "target_scene",
                "repair_family": family,
                "source": source,
                "source_issue_ids": [issue_id],
                "rationale": (
                    f"LLM creative rewrite required for {metric} "
                    f"(rhythm family). "
                    f"Hint: {violation.get('detail', '')[:200]}"
                ),
                "violation_hint": {
                    "metric": metric,
                    "family": family,
                    "detail": violation.get("detail", ""),
                    "expected_behavior": violation.get("expected_behavior", ""),
                    "suggested_strategy": violation.get("suggested_strategy", ""),
                },
            }
        return None

    @staticmethod
    def _fact_tool_blueprint(
        violation: dict[str, Any],
        target_span: str,
        suggested: str,
        source: str,
        issue_id: str,
    ) -> dict[str, Any] | None:
        detail = str(violation.get("detail") or violation.get("reason") or "")
        claim_match = re.search(r"Text claim ['\"](.+?)['\"]", detail, re.IGNORECASE)
        correction_match = re.search(
            r"suggested correction\s*:\s*['\"](.+?)['\"]",
            detail,
            re.IGNORECASE,
        )
        candidate_text = str(violation.get("_candidate_text") or "")
        if claim_match:
            claim = claim_match.group(1).strip()
            if claim and (not candidate_text or claim in candidate_text):
                # Localizers may widen a diagnostic target to its containing
                # sentence.  The explicit diagnostic claim is the authority
                # for the smallest safe replacement span.
                target_span = claim
        if correction_match:
            correction = correction_match.group(1).strip()
            if correction:
                suggested = correction
        if _looks_like_bridge_gap(violation):
            replacement = (
                suggested
                if suggested and not _looks_like_instruction(suggested) and not _looks_like_quality_contract_statement(suggested)
                else (
                "\u79bb\u5f00\u539f\u5148\u7684\u4f4d\u7f6e\u540e\uff0c"
                "\u5979\u5148\u628a\u773c\u524d\u7684\u7a7a\u7f3a\u8865\u4e0a\uff0c"
                "\u518d\u7ee7\u7eed\u5904\u7406\u540e\u7eed\u52a8\u4f5c\u3002"
                )
            )
            return {
                "operation": "insert_transition_anchor",
                "target_span": target_span,
                "replacement": "\n\n" + replacement,
                "scope": "target_scene",
                "repair_family": "fact_transition",
                "source": source,
                "source_issue_ids": [issue_id],
                "rationale": "Compile missing fact/context bridge into a local transition anchor.",
                "postconditions": {"required_spans": [replacement[:40]], "forbidden_spans": []},
            }
        if (
            suggested
            and suggested != target_span
            and not _looks_like_instruction(suggested)
            and not _looks_like_quality_contract_statement(suggested)
        ):
            replacement = _replacement_for_target_span(target_span, suggested)
            return {
                "operation": "replace_literal",
                "from": target_span,
                "to": replacement,
                "target_span": target_span,
                "replacement": replacement,
                "scope": "target_scene",
                "repair_family": "fact_local",
                "source": source,
                "source_issue_ids": [issue_id],
                "rationale": "Compile localized fact conflict into literal replacement.",
                "postconditions": {
                    "forbidden_spans": [target_span],
                    "required_spans": [replacement],
                },
            }
        return None

    @staticmethod
    def _contract_completion_tool_blueprint(
        *,
        violation: dict[str, Any],
        metric: str,
        anchor: str,
        source: str,
        issue_id: str,
    ) -> dict[str, Any] | None:
        required = _contract_required_state(violation)
        if not required or not anchor:
            return None
        insert_text = _contract_completion_sentence(required)
        if not insert_text:
            return None
        required_spans = _contract_completion_required_spans(required, insert_text)
        locator = _localized_locator_fields(violation, anchor)
        return {
            "operation": "insert_micro_payoff",
            "target_span": anchor,
            "after_span": anchor,
            "replacement": "\n\n" + insert_text,
            "new_text": insert_text,
            "scope": "target_scene",
            "repair_family": "contract_completion",
            "source": source,
            "source_issue_ids": [issue_id],
            "rationale": f"Compile {metric} into a bounded contract-completion beat.",
            "postconditions": {
                "required_spans": required_spans,
                "forbidden_spans": [],
            },
            **locator,
        }

    @staticmethod
    def _metric_name(violation: dict[str, Any]) -> str:
        return str(
            violation.get("metric")
            or violation.get("type")
            or violation.get("violation_type")
            or ""
        ).lower()

    def _blueprint_anchor(self, violation: dict[str, Any], metric: str) -> str:
        localized = _localized_target_span(violation)
        if localized:
            return localized
        evidence = violation.get("evidence") if isinstance(violation.get("evidence"), dict) else {}
        spans = violation.get("evidence_spans") or evidence.get("evidence_spans") or []
        if isinstance(spans, list):
            for item in spans:
                if isinstance(item, dict):
                    text = str(item.get("text") or item.get("span") or "").strip()
                else:
                    text = str(item or "").strip()
                if text and not _looks_like_diagnostic_sentence(text):
                    return text
        scene_text = str(violation.get("_candidate_text") or "")
        return self._semantic_anchor(scene_text, metric)

    @staticmethod
    def _semantic_anchor(scene_text: str, metric: str) -> str:
        sentences = [
            item.strip()
            for item in re.split(r"(?<=[\u3002\uff01\uff1f!?])\s*|\n+", scene_text or "")
            if item and item.strip()
        ]
        if not sentences:
            return ""
        token_map = {
            "low_conflict_density": ["\u6d1e\u5916", "\u8ffd", "\u547c\u5438", "\u8109\u640f", "\u4e0d\u591f", "\u591a\u4e45", "\u75bc"],
            "flat_pressure_ramp": ["\u6d1e\u5916", "\u8ffd", "\u547c\u5438", "\u8109\u640f", "\u4e0d\u591f", "\u591a\u4e45", "\u75bc"],
            "low_event_density": ["\u722c", "\u62d6", "\u6478", "\u585e", "\u627e", "\u653e", "\u70bc"],
            "low_reversal_density": ["\u722c", "\u62d6", "\u6478", "\u585e", "\u627e", "\u653e", "\u70bc"],
            "weak_curiosity_engine": ["\u4e3a\u4ec0\u4e48", "\u54ea\u91cc", "\u600e\u4e48", "\u5f02\u6837", "\u95ee"],
            "weak_opening_hook": ["\u4e3a\u4ec0\u4e48", "\u54ea\u91cc", "\u600e\u4e48", "\u5f02\u6837", "\u95ee"],
            "weak_chapter_end_hook": ["\u4e3a\u4ec0\u4e48", "\u54ea\u91cc", "\u600e\u4e48", "\u5f02\u6837", "\u95ee"],
            "missing_micro_payoff": ["\u7ec8\u4e8e", "\u6210\u4e86", "\u7761\u5f00", "\u91d1\u4e39", "\u4e39", "\u836f", "\u89e3\u6bd2"],
            "emotion_label_count": ["害怕", "恐惧", "担心", "紧张", "愤怒", "悲伤", "难过", "震惊", "开心", "情绪"],
            "emotion_label_hits": ["害怕", "恐惧", "担心", "紧张", "愤怒", "悲伤", "难过", "震惊", "开心", "情绪"],
            "trait_statement_count": ["勇敢", "善良", "冷静", "坚强", "脆弱"],
            "body_language_cliche_count": ["攥紧拳头", "咬紧牙关", "瞪大眼睛", "眼神复杂"],
            "standalone_abstract_claims": ["命运", "意义", "本质", "价值", "复杂", "情绪"],
            "abstract_bare_count": ["命运", "意义", "本质", "价值", "复杂", "情绪"],
            "head_hopping_count": ["心里", "心中", "脑中", "知道", "明白", "意识到", "觉得", "认定"],
            "single_pov_per_scene": ["心里", "心中", "脑中", "知道", "明白", "意识到", "觉得", "认定"],
            "temporal_anchor_count": ["清晨", "早晨", "上午", "正午", "午后", "下午", "黄昏", "夜里", "天色", "日头"],
            "time_anchor_count": ["清晨", "早晨", "上午", "正午", "午后", "下午", "黄昏", "夜里", "天色", "日头"],
            "pure_exposition_block_chars": ["这说明", "这意味着", "因此", "所以", "事实上", "显然", "原因", "背景"],
            "max_exposition_block_chars": ["这说明", "这意味着", "因此", "所以", "事实上", "显然", "原因", "背景"],
        }
        tokens = token_map.get(metric, [])
        for sentence in sentences:
            if any(token in sentence for token in tokens):
                return sentence[:120]
        if metric in {"missing_micro_payoff", "weak_chapter_end_hook"}:
            return sentences[-1][:120]
        if metric in {"ending_state_not_reached", "missing_must_show"}:
            return sentences[-1][:120]
        if metric in {"weak_opening_hook", "weak_curiosity_engine"}:
            return sentences[0][:120]
        return sentences[min(len(sentences) - 1, max(0, len(sentences) // 2))][:120]

    @staticmethod
    def _anchor_strategy(metric: str, family: str) -> str:
        if family == "fact":
            return "localized_fact_span"
        if metric in {"low_conflict_density", "flat_pressure_ramp"}:
            return "after_existing_pressure_or_action_beat"
        if metric in {"weak_curiosity_engine", "weak_opening_hook", "weak_chapter_end_hook"}:
            return "after_question_or_unknown_beat"
        if metric == "missing_micro_payoff":
            return "after_partial_success_or_state_change"
        if metric in {"temporal_anchor_count", "time_anchor_count"}:
            return "after_existing_action_beat"
        if metric in {"pure_exposition_block_chars", "max_exposition_block_chars", "pure_exposition_block"}:
            return "inside_exposition_paragraph"
        if metric in {"head_hopping_count", "single_pov_per_scene"}:
            return "localized_non_pov_inner_access_span"
        return "localized_target_span"

    @staticmethod
    def _fallback_anchor_descriptors(violation: dict[str, Any]) -> list[dict[str, Any]]:
        fallback: list[dict[str, Any]] = []
        if violation.get("_candidate_text"):
            fallback.append({"type": "semantic_anchor", "role": "scene_text_scan"})
        if violation.get("source_scene") is not None:
            fallback.append({"type": "scene_index", "value": violation.get("source_scene")})
        return fallback

    @staticmethod
    def _required_effect(metric: str, family: str) -> str:
        if metric in {"low_conflict_density", "flat_pressure_ramp"}:
            return "pressure_cost_visible"
        if metric in {"weak_curiosity_engine", "weak_opening_hook", "weak_chapter_end_hook"}:
            return "reader_question_visible"
        if metric == "missing_micro_payoff":
            return "local_payoff_visible"
        if metric in {"temporal_anchor_count", "time_anchor_count"}:
            return "observable_time_anchor_visible"
        if family == "show_evidence":
            return "abstract_claim_grounded_in_observable_evidence"
        if family == "pov":
            return "non_pov_inner_access_removed_or_externalized"
        if metric in {"ending_state_not_reached", "missing_must_show"}:
            return "required_contract_beat_visible"
        if family == "structure":
            return "exposition_block_broken_into_scene_beats"
        if family.startswith("anti_ai"):
            return "ai_flavor_metric_improved"
        if family == "fact":
            return "fact_conflict_resolved"
        return "finding_improved"

    @staticmethod
    def _lane_for_family(
        family: str,
        violation: dict[str, Any],
    ) -> tuple[str, str, str, str, list[str]]:
        if family == "anti_ai_punctuation":
            return "deterministic_surface_cleanup", "S1", "S2", "surface_cleanup", ["rewrite_sentence"]
        if family == "anti_ai_discourse":
            return "paragraph_reconstruction", "S3", "S4", "rebuild_paragraph_shape", ["prose_local_patch"]
        if family == "anti_ai_local":
            return "prose_local_patch", "S2", "S3", "replace_formulaic_phrase", ["rewrite_sentence"]
        if family == "fact":
            if _looks_like_bridge_gap(violation):
                return "fact_bridge_patch", "S3", "S4", "insert_context_bridge", ["rewrite_sentence"]
            return "fact_local_patch", "S2", "S3", "replace_conflicting_phrase", ["rewrite_sentence"]
        if family == "rhythm":
            return "pacing_repair", "S3", "S4", "repace_scene_beat", ["paragraph_reconstruction"]
        if family == "voice":
            return "voice_reconstruction", "S4", "S4", "restore_voice_fingerprint", ["voice_repair"]
        if family == "pov":
            return "voice_repair", "S3", "S4", "restore_pov_boundary", ["rewrite_sentence"]
        if family == "show_evidence":
            return "prose_local_patch", "S2", "S3", "ground_abstract_claim", ["rewrite_sentence"]
        if family == "structure":
            return "contract_completion", "S2", "S3", "insert_missing_beat", ["scene_restructure"]
        if family == "system":
            return "manual_review", "S0", "S0", "manual_review", []
        return "fbi_prose", "S2", "S3", "rewrite_sentence", ["prose_local_patch"]

    @staticmethod
    def _target_metric(violation: dict[str, Any], family: str) -> dict[str, Any]:
        metric = str(
            violation.get("metric")
            or violation.get("type")
            or violation.get("violation_type")
            or family
        )
        direction = "decrease" if family.startswith("anti_ai") or family in {"pov", "voice", "show_evidence"} else "resolve"
        return {
            "validator": violation.get("validator") or violation.get("source_validator") or "",
            "metric": metric,
            "actual": violation.get("actual", violation.get("value", "")),
            "expected": violation.get("expected", violation.get("limit", "")),
            "expected_max": violation.get("expected_max", violation.get("max", "")),
            "direction": direction,
        }

    def _patch_plan(self, violation: dict[str, Any], family: str) -> list[dict[str, Any]]:
        explicit = violation.get("patch_plan")
        if isinstance(explicit, list):
            return [dict(item) for item in explicit if isinstance(item, dict)]
        repair_brief = violation.get("repair_brief") if isinstance(violation.get("repair_brief"), dict) else {}
        explicit = repair_brief.get("patch_plan")
        if isinstance(explicit, list):
            return [dict(item) for item in explicit if isinstance(item, dict)]
        repair_intent = violation.get("repair_intent") if isinstance(violation.get("repair_intent"), dict) else {}
        explicit = repair_intent.get("patch_plan")
        if isinstance(explicit, list):
            return [dict(item) for item in explicit if isinstance(item, dict)]

        pairs: list[tuple[str, str]] = []
        evidence = violation.get("evidence") if isinstance(violation.get("evidence"), dict) else {}
        if family == "fact":
            detail = str(violation.get("detail") or violation.get("reason") or "")
            claim_match = re.search(r"Text claim ['\"](.+?)['\"]", detail, re.IGNORECASE)
            correction_match = re.search(
                r"suggested correction\s*:\s*['\"](.+?)['\"]",
                detail,
                re.IGNORECASE,
            )
            if claim_match and correction_match:
                pairs.append((claim_match.group(1).strip(), correction_match.group(1).strip()))
        for old_key, new_key in [
            ("current_claim", "authority_claim"),
            ("text_claim", "authority_fact"),
            ("actual", "expected"),
            ("wrong", "correct"),
            ("from", "to"),
            ("target_span", "replacement"),
            ("target_span", "expected_text"),
            ("target_span", "suggested_correction"),
        ]:
            old = str(evidence.get(old_key) or violation.get(old_key) or "").strip()
            new = str(evidence.get(new_key) or violation.get(new_key) or "").strip()
            if (old_key, new_key) == ("actual", "expected") and (
                family not in {"fact", "structure"} or _looks_like_metric_value(old, new)
            ):
                continue
            if old and new and old != new:
                pairs.append((old, new))

        target_span = str(violation.get("target_span") or "").strip()
        repair_goal = violation.get("repair_goal") if isinstance(violation.get("repair_goal"), dict) else {}
        for key in ("replacement", "expected_text", "suggested_correction", "authority_fact"):
            new = str(evidence.get(key) or repair_goal.get(key) or "").strip()
            if target_span and new and target_span != new:
                pairs.append((target_span, new))

        blob = "\n".join(
            str(item or "")
            for item in [
                violation.get("detail"),
                violation.get("reason"),
                violation.get("expected_behavior"),
                violation.get("repair_goal"),
                violation.get("target_span"),
            ]
        )
        if not pairs:
            bounded = self._bounded_non_rewrite_plan(violation, family, blob)
            if bounded:
                return bounded

        pairs.extend(self._extract_replace_pairs(blob))
        if not pairs and family == "fact":
            target_span = str(violation.get("target_span") or "").strip()
            authority = str(evidence.get("authority_fact") or evidence.get("established_fact") or "").strip()
            if target_span and authority and target_span not in authority:
                pairs.append((target_span, authority))

        plan: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for old, new in pairs:
            old = self._clean_patch_phrase(old)
            new = self._clean_patch_phrase(new)
            if family not in {"fact", "structure"} and _looks_like_quality_contract_statement(new):
                continue
            if _looks_like_instruction(new):
                continue
            if not old or not new or old == new or (old, new) in seen:
                continue
            seen.add((old, new))
            plan.append({
                "operation": "replace_phrase",
                "from": old,
                "to": new,
                "scope": "target_scene",
                "source": "fbi_review_minister",
            })
        if plan:
            return plan[:6]
        completed = self._work_order_completion_plan(violation, family, blob)
        if completed:
            return completed
        return []

    @staticmethod
    def _work_order_completion_plan(
        violation: dict[str, Any],
        family: str,
        blob: str,
    ) -> list[dict[str, Any]]:
        metric = str(
            violation.get("metric")
            or violation.get("type")
            or violation.get("violation_type")
            or ""
        ).lower()
        target_span = _localized_target_span(violation)
        suggested = _suggested_repair_text(violation, blob)

        if family == "anti_ai_local" and metric == "structure_word_cluster_count" and target_span:
            return [{
                "operation": "normalize_structure_words",
                "target_span": target_span,
                "max_replacements": 3,
                "scope": "target_scene",
                "repair_family": "anti_ai_structure",
                "source": "fbi_review_minister_work_order_completion",
                "rationale": "Localized structure-word cluster can be repaired by bounded wording cleanup.",
            }]

        if family == "anti_ai_discourse" and metric in {
            "paragraph_shape_repeat_count",
            "uniform_sentence_streak_max",
            "mirrored_paragraph_opening_count",
        } and target_span:
            return [{
                "operation": "vary_sentence_shape",
                "target_span": target_span,
                "scope": "target_scene",
                "repair_family": "paragraph_rhythm",
                "source": "fbi_review_minister_work_order_completion",
                "rationale": "Localized paragraph/sentence shape repetition can be repaired in-place.",
            }]

        if family == "rhythm" and metric == "weak_curiosity_engine" and target_span:
            return [{
                "operation": "insert_hook_beat",
                "target_span": target_span,
                "replacement": suggested or "更麻烦的是，真正的答案还没有露面。",
                "scope": "target_scene",
                "repair_family": "narrative_hook",
                "source": "fbi_review_minister_work_order_completion",
                "rationale": "Review requires one bounded reader-question beat.",
            }]

        if family == "rhythm" and metric == "missing_micro_payoff" and target_span:
            return [{
                "operation": "insert_micro_payoff",
                "target_span": target_span,
                "replacement": suggested or "至少眼前这一处变化，证明她的判断没有落空。",
                "scope": "target_scene",
                "repair_family": "narrative_payoff",
                "source": "fbi_review_minister_work_order_completion",
                "rationale": "Review requires one bounded local payoff beat.",
            }]

        if family == "fact" and target_span:
            if _looks_like_bridge_gap(violation):
                return [{
                    "operation": "insert_transition_anchor",
                    "target_span": target_span,
                    "replacement": suggested or "离开原先的位置后，她沿着隐蔽路径绕入此处，才继续处理眼前的事。",
                    "scope": "target_scene",
                    "repair_family": "fact_transition",
                    "source": "fbi_review_minister_work_order_completion",
                    "rationale": "Fact review identified a missing context bridge before this localized evidence.",
                }]
            if suggested and suggested != target_span:
                replacement = _replacement_for_target_span(target_span, suggested)
                return [{
                    "operation": "replace_literal",
                    "target_span": target_span,
                    "replacement": replacement,
                    "scope": "target_scene",
                    "repair_family": "fact_time" if _looks_like_time_issue(violation) else "fact_local",
                    "source": "fbi_review_minister_work_order_completion",
                    "rationale": "Fact review supplied a localized conflicting span and authority wording.",
                }]

        return []

    @staticmethod
    def _bounded_non_rewrite_plan(
        violation: dict[str, Any],
        family: str,
        blob: str,
    ) -> list[dict[str, Any]]:
        target_span = str(violation.get("target_span") or "").strip()
        if not target_span:
            evidence = violation.get("evidence") if isinstance(violation.get("evidence"), dict) else {}
            spans = evidence.get("evidence_spans") if isinstance(evidence, dict) else None
            if isinstance(spans, list):
                for item in spans:
                    if isinstance(item, dict):
                        target_span = str(item.get("text") or item.get("span") or "").strip()
                    else:
                        target_span = str(item or "").strip()
                    if target_span:
                        break
        if not target_span:
            return []

        metric = str(
            violation.get("metric")
            or violation.get("type")
            or violation.get("violation_type")
            or ""
        ).lower()
        joined = f"{family} {metric} {blob}".lower()
        paragraph_split_tokens = (
            "dense_paragraph",
            "long_paragraph",
            "reader_breathing",
            "breathing beat",
            "paragraph",
            "uniform_sentence_streak",
            "paragraph_shape",
        )
        if family in {"rhythm", "anti_ai_discourse"} and any(token in joined for token in paragraph_split_tokens):
            return [{
                "operation": "split_paragraph",
                "target_span": target_span,
                "scope": "target_scene",
                "source": "fbi_review_minister_bounded_command",
                "rationale": "Bounded paragraph/rhythm repair with localized target span.",
            }]
        return []

    @staticmethod
    def _extract_replace_pairs(text: str) -> list[tuple[str, str]]:
        if not text:
            return []
        quote = r"['\"\u201c\u201d\u2018\u2019]"
        patterns = [
            rf"(?:replace|change|correct)\s+{quote}?([^'\"\u201c\u201d\u2018\u2019,;.\n]{{1,80}}){quote}?\s+(?:with|to|as)\s+{quote}?([^'\"\u201c\u201d\u2018\u2019,;.\n]{{1,120}}){quote}?",
            rf"{quote}([^'\"\u201c\u201d\u2018\u2019]{{1,80}}){quote}\s*(?:->|=>|to)\s*{quote}([^'\"\u201c\u201d\u2018\u2019]{{1,120}}){quote}",
            rf"(?:\u5c06|\u628a)\s*{quote}?([^'\"\u201c\u201d\u2018\u2019\uff0c\u3002\uff1b\n]{{1,80}}){quote}?\s*(?:\u6539\u4e3a|\u66ff\u6362\u4e3a|\u6539\u6210)\s*{quote}?([^'\"\u201c\u201d\u2018\u2019\uff0c\u3002\uff1b\n]{{1,120}}){quote}?",
            rf"{quote}([^'\"\u201c\u201d\u2018\u2019]{{1,80}}){quote}\s*(?:\u5e94\u4e3a|\u5e94\u8be5\u662f|\u6539\u4e3a)\s*{quote}([^'\"\u201c\u201d\u2018\u2019]{{1,120}}){quote}",
        ]
        pairs: list[tuple[str, str]] = []
        for pattern in patterns:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                pairs.append((match.group(1), match.group(2)))
        return pairs

    @staticmethod
    def _clean_patch_phrase(value: str) -> str:
        value = re.sub(r"\s+", " ", str(value or "")).strip()
        return value.strip(" '\"\u201c\u201d\u2018\u2019`")

    @staticmethod
    def _authority(violation: dict[str, Any], family: str) -> FBIAuthority:
        evidence = violation.get("evidence") if isinstance(violation.get("evidence"), dict) else {}
        if family == "fact":
            candidates = [
                evidence.get("authority_fact"),
                evidence.get("established_fact"),
                violation.get("authority_fact"),
                violation.get("expected_text"),
                violation.get("suggested_correction"),
            ]
            authority_type = "hard_fact"
        elif family == "structure":
            candidates = [
                evidence.get("contract_state"),
                evidence.get("required_state"),
                violation.get("expected_text"),
                violation.get("required_state"),
            ]
            authority_type = "structure_contract"
        else:
            candidates = []
            authority_type = "quality_contract"
        statement = next((str(item).strip() for item in candidates if str(item or "").strip()), "")
        return FBIAuthority(
            authority_type=authority_type,
            source=str(violation.get("source") or violation.get("validator") or "review_case"),
            statement=statement,
            confidence="high" if family in {"fact", "structure"} else "medium",
            evidence=evidence,
        )

    @staticmethod
    def _conflict_decision(
        violation: dict[str, Any],
        authority: FBIAuthority,
    ) -> FBIConflictDecision | None:
        if not authority.statement:
            return None
        if authority.authority_type not in {"hard_fact", "structure_contract"}:
            return None
        if _looks_like_instruction(authority.statement):
            return None
        if _looks_like_quality_contract_statement(authority.statement):
            return None
        current = str(
            violation.get("target_span")
            or violation.get("current_claim")
            or violation.get("actual")
            or ""
        ).strip()
        if not current:
            return None
        return FBIConflictDecision(
            conflict_id="conflict_" + hashlib.md5(f"{current}:{authority.statement}".encode("utf-8")).hexdigest()[:10],
            current_claim=current,
            authority_claim=authority.statement,
            decision="replace_current_with_authority",
            rationale="Review evidence designates the authority claim as canonical.",
        )

    @staticmethod
    def _protection_boundary(
        violation: dict[str, Any],
        authority: FBIAuthority,
    ) -> FBIProtectionBoundary:
        protected: list[str] = []
        for key in ("protected_context", "protected_terms", "must_preserve", "preserve"):
            value = violation.get(key)
            if isinstance(value, list):
                protected.extend(str(item) for item in value if item)
            elif value:
                protected.append(str(value))
        hard_facts = [authority.statement] if authority.statement and authority.authority_type == "hard_fact" else []
        return FBIProtectionBoundary(
            preserve_scene_markers=True,
            preserve_hard_facts=hard_facts,
            preserve_protected_spans=protected,
            preserve_pov=str(violation.get("pov") or violation.get("pov_character") or ""),
        )

    @staticmethod
    def _acceptance_criteria(
        family: str,
        violation: dict[str, Any],
        intent: FBIRepairIntent,
    ) -> list[FBIAcceptanceCriterion]:
        metric = str(
            violation.get("metric")
            or violation.get("type")
            or violation.get("violation_type")
            or family
        )
        criteria = [
            FBIAcceptanceCriterion(
                criterion_id=f"criterion_{metric}",
                metric=metric,
                operator="resolved" if intent.repair_domain in {"fact", "structure"} else "improved",
                expected=violation.get("expected", violation.get("expected_max", "")),
                source=str(violation.get("validator") or violation.get("source") or "review_case"),
            )
        ]
        if family == "anti_ai_punctuation":
            criteria.append(
                FBIAcceptanceCriterion(
                    criterion_id="criterion_dash_density",
                    metric="dash_density",
                    operator="<=",
                    expected=violation.get("expected_max", violation.get("max", DEFAULT_DASH_ARTIFACT_MAX_PER_1000)),
                    source="anti_ai_prose",
                )
            )
        return criteria

    @staticmethod
    def _dependency_policy(family: str) -> FBIDependencyPolicy:
        # 9.2.3: 放宽 anti_ai 与 rhythm/structure 的同窗限制，允许 compound replacement
        # 注意：使用具体 family 名（anti_ai_punctuation/anti_ai_local/anti_ai_discourse），
        # 与 edit_window_planner._family_policy_map 保持一致，避免双向检查时找不到匹配。
        anti_ai_families = ["anti_ai_punctuation", "anti_ai_local", "anti_ai_discourse"]
        if family == "fact":
            return FBIDependencyPolicy(
                must_run_before_domains=["anti_ai", "rhythm"],
                can_batch_with=["fact", "structure"],  # 允许 fact 与 structure 同窗
            )
        if family in anti_ai_families:
            return FBIDependencyPolicy(
                can_batch_with=anti_ai_families + ["rhythm", "structure"],  # 放宽：允许与 rhythm/structure 同窗
            )
        if family == "structure":
            return FBIDependencyPolicy(
                must_run_before_domains=["rhythm"],  # 仍要求在 rhythm 之前，但不再要求在 anti_ai 之前
                can_batch_with=["structure", "fact"] + anti_ai_families,  # 允许与 anti_ai/fact 同窗
            )
        return FBIDependencyPolicy()

    @staticmethod
    def _issue_family(values: set[str], violation: dict[str, Any]) -> str:
        # T2.2: 优先查 active 自迭代规则（讨论稿 §5.1.2）
        # 规则的 then 只到归一化层（family），不碰路由判断
        # 缓存为 None 时返回 None，走原归一化逻辑（安全 fallback）
        rule_family = _query_active_rule_family(violation)
        if rule_family is not None:
            return rule_family

        scope = str(violation.get("scope") or violation.get("repair_scope") or "")
        if scope == "system" or any(value.endswith("_unavailable") for value in values):
            return "system"
        # 优先查统一路由表，避免 abstraction_over_budget 被 anti_ai_local 提前截走
        metric_key = str(
            violation.get("metric")
            or violation.get("type")
            or violation.get("violation_type")
            or ""
        ).lower()
        route_family = route_family_for_metric(metric_key, str(violation.get("validator") or ""))
        if route_family is not None:
            return route_family
        spec = get_metric_spec(violation.get("metric") or violation.get("type") or violation.get("violation_type"))
        if spec is not None:
            return spec.family
        if values & _FACT_TYPES:
            return "fact"
        if values & _ANTI_AI_PUNCTUATION:
            return "anti_ai_punctuation"
        if values & _ANTI_AI_DISCOURSE:
            return "anti_ai_discourse"
        if values & _ANTI_AI_LOCAL:
            return "anti_ai_local"
        if "voice_fingerprint" in values:
            return "voice"
        if values & _POV_TYPES:
            return "pov"
        if values & _RHYTHM_TYPES:
            return "rhythm"
        if values & _STRUCTURE_TYPES:
            return "structure"
        return str(violation.get("repair_domain") or "general")

    @staticmethod
    def _repair_domain_for_family(family: str, violation: dict[str, Any]) -> str:
        if family.startswith("anti_ai"):
            return "anti_ai"
        if family == "voice":
            return "pov"
        if family in {"fact", "rhythm", "pov", "structure"}:
            return family
        if family == "show_evidence":
            return "surface"
        return str(violation.get("repair_domain") or "surface")

    @staticmethod
    def _semantic_values(violation: dict[str, Any]) -> set[str]:
        values = {
            str(violation.get("type") or "").lower(),
            str(violation.get("violation_type") or "").lower(),
            str(violation.get("metric") or "").lower(),
            str(violation.get("validator") or "").lower(),
            str(violation.get("source_validator") or "").lower(),
            str(violation.get("repair_domain") or "").lower(),
        }
        values.update(str(item).lower() for item in violation.get("merged_violation_types") or [] if item)
        values.update(str(item).lower() for item in violation.get("merged_metrics") or [] if item)
        return {value for value in values if value}

    @staticmethod
    def _scene_index(violation: dict[str, Any]) -> int | None:
        for key in (
            "obligation_owner_scene",
            "owner_scene",
            "source_scene",
            "scene_index",
            "affected_scene",
        ):
            value = violation.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                return value
        scenes = violation.get("source_scenes") or []
        for scene in scenes:
            if isinstance(scene, int):
                return scene
        issue = violation.get("review_case_issue") if isinstance(violation.get("review_case_issue"), dict) else {}
        value = issue.get("scene_index")
        return value if isinstance(value, int) else None

    @staticmethod
    def _issue_id(violation: dict[str, Any]) -> str:
        existing = violation.get("issue_id") or violation.get("violation_id") or violation.get("id")
        if existing:
            return str(existing)
        raw = "|".join(
            str(item or "")
            for item in [
                violation.get("source_scene"),
                violation.get("type") or violation.get("violation_type"),
                violation.get("metric"),
                violation.get("target_span"),
                violation.get("detail") or violation.get("reason"),
            ]
        )
        return "iss_" + hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]

    @staticmethod
    def _diagnosis_id(
        case: ChapterReviewCase,
        issue_id: str,
        family: str,
        scene_index: int | None,
    ) -> str:
        raw = f"{case.case_id}:{case.review_round}:{scene_index}:{family}:{issue_id}"
        return "diag_" + hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]

    @staticmethod
    def _source_trace(violation: dict[str, Any]) -> list[str]:
        trace = []
        for key in ("source", "validator", "source_validator", "skill_id", "chapter_review_node"):
            value = violation.get(key)
            if value:
                trace.append(f"{key}:{value}")
        return trace

    @staticmethod
    def _family_counts(diagnoses: list[FBIReviewDiagnosis]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for diagnosis in diagnoses:
            counts[diagnosis.issue_family] = counts.get(diagnosis.issue_family, 0) + 1
        return counts

    @staticmethod
    def _blueprint_status_counts(diagnoses: list[FBIReviewDiagnosis]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for diagnosis in diagnoses:
            status = diagnosis.revision_blueprint.status or "unknown"
            counts[status] = counts.get(status, 0) + 1
        return counts

    @staticmethod
    def _collect_case_violations(case: ChapterReviewCase) -> list[dict[str, Any]]:
        violations: list[dict[str, Any]] = []
        for packet in case.scene_packets:
            if packet.unavailable:
                continue
            violations.extend(dict(item) for item in packet.blocking_violations if isinstance(item, dict))
        return violations

    @staticmethod
    def _attach_to_case_file(
        case: ChapterReviewCase,
        diagnosis_set: FBIReviewDiagnosisSet,
    ) -> None:
        if not case.review_case_file:
            case.review_case_file = {}
        try:
            case_file = ReviewCaseFile.model_validate(case.review_case_file)
        except Exception:
            case.review_case_file.setdefault("diagnoses", [])
            case.review_case_file.setdefault("repair_intents", [])
            case.review_case_file.setdefault("revision_blueprints", [])
            case.review_case_file.setdefault("issue_status", {})
            case.review_case_file["diagnoses"] = [item.model_dump() for item in diagnosis_set.diagnoses]
            case.review_case_file["repair_intents"] = [
                item.repair_intent.model_dump() for item in diagnosis_set.diagnoses
            ]
            case.review_case_file["revision_blueprints"] = [
                item.revision_blueprint.model_dump() for item in diagnosis_set.diagnoses
            ]
            case.review_case_file["issue_status"].update(diagnosis_set.issue_status)
            case.review_case_file.setdefault("review_trace", {})["fbi_review_minister"] = diagnosis_set.trace
            return

        issue_status = dict(case_file.issue_status or {})
        issue_status.update(diagnosis_set.issue_status)
        for issue in case_file.all_issues():
            status = issue_status.get(issue.issue_id)
            if status:
                issue.status = status
            for diagnosis in diagnosis_set.diagnoses:
                if issue.issue_id in diagnosis.issue_ids:
                    issue.diagnosis_id = diagnosis.diagnosis_id
                    issue.repair_intent_id = diagnosis.repair_intent.intent_id
                    break
        case_file.issue_status = issue_status
        case_file.diagnoses = [item.model_dump() for item in diagnosis_set.diagnoses]
        case_file.repair_intents = [
            item.repair_intent.model_dump() for item in diagnosis_set.diagnoses
        ]
        case_file.revision_blueprints = [
            item.revision_blueprint.model_dump() for item in diagnosis_set.diagnoses
        ]
        case_file.review_trace = dict(case_file.review_trace or {})
        case_file.review_trace["fbi_review_minister"] = diagnosis_set.trace
        case.review_case_file = case_file.model_dump()


def _looks_like_bridge_gap(violation: dict[str, Any]) -> bool:
    values = " ".join(
        str(item or "").lower()
        for item in [
            violation.get("type"),
            violation.get("violation_type"),
            violation.get("metric"),
            violation.get("detail"),
            violation.get("reason"),
            violation.get("expected_behavior"),
            violation.get("repair_goal"),
            violation.get("suggested_correction"),
        ]
    )
    return any(token in values for token in [
        "missing_required_components",
        "missing_context_bridge",
        "unestablished",
        "not shown",
        "missing",
        "requires",
        "or explain",
        "\u8865\u5145",
        "\u8bf4\u660e",
        "\u65e0\u6cd5",
        "\u7f3a\u5c11",
        "\u672a\u63d0\u53ca",
    ])


def _contract_required_state(violation: dict[str, Any]) -> str:
    evidence = violation.get("evidence") if isinstance(violation.get("evidence"), dict) else {}
    candidates = [
        violation.get("required_state"),
        violation.get("expected_text"),
        violation.get("expected_behavior"),
        violation.get("repair_goal"),
        evidence.get("required_state"),
        evidence.get("expected_text"),
        evidence.get("expected_behavior"),
        evidence.get("contract_state"),
        violation.get("detail"),
        violation.get("reason"),
    ]
    for value in candidates:
        required = _extract_contract_state_phrase(str(value or ""))
        if required:
            return required
    return ""


def _extract_contract_state_phrase(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return ""
    quote_match = re.search(r"[\"'“”‘’《》](.{2,80}?)[\"'“”‘’《》]", text)
    if quote_match:
        return _clean_contract_state(quote_match.group(1))
    for marker in (
        "场景结束状态为",
        "场景结束状态要求",
        "结束状态为",
        "结束状态要求",
        "结尾状态为",
        "结尾状态要求",
        "ending state",
        "required state",
        "must show",
    ):
        index = text.lower().find(marker.lower())
        if index < 0:
            continue
        tail = text[index + len(marker):].strip(" ：:，,。.;；")
        if tail:
            return _clean_contract_state(tail)
    require_match = re.search(r"(?:要求|需要|应当|必须)(.{4,80}?)(?:。|；|;|，但|但正文|$)", text)
    if require_match:
        return _clean_contract_state(require_match.group(1))
    if len(text) <= 60 and not _looks_like_quality_contract_statement(text):
        return _clean_contract_state(text)
    return ""


def _clean_contract_state(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip(" ：:，,。.;；\"'“”‘’《》")
    for marker in ("但正文", "但候选", "but the draft", "however"):
        index = text.lower().find(marker.lower())
        if index > 0:
            text = text[:index].strip(" ：:，,。.;；")
    return text[:80]


def _contract_completion_sentence(required: str) -> str:
    """Do not synthesize narrative prose from a diagnostic contract string.

    Registered semantic routes now produce ``llm_creative_rewrite`` work units.
    Returning an empty value keeps legacy/unregistered callers fail-closed
    instead of inserting a genre-specific or instruction-shaped template.
    """
    return ""


def _contract_completion_required_spans(required: str, insert_text: str) -> list[str]:
    """Return action-keyword spans that verify contract completion.

    Spans must appear in the patched text, so they are drawn from insert_text
    (the actual patch prose) rather than the diagnostic sentence. This avoids
    false negatives where the patch writes natural action prose but the
    postcondition expected the diagnostic sentence verbatim.
    """
    if not insert_text:
        return []
    spans: list[str] = []
    if re.search(r"[\u4e00-\u9fff]", insert_text):
        for marker in ("正式开始炼丹", "五师兄", "前世记忆"):
            if marker in insert_text:
                spans.append(marker)
        if not spans:
            tail = insert_text.rstrip("。.；;").strip()
            if tail:
                spans.append(tail[-20:])
    else:
        required_clean = _clean_contract_state(required)
        if required_clean and required_clean in insert_text:
            spans.append(required_clean)
        if not spans:
            tail = insert_text.rstrip(".;").strip()
            if tail:
                spans.append(tail[-40:])
    return spans


def _localized_locator_fields(violation: dict[str, Any], anchor: str) -> dict[str, Any]:
    """Return frozen-base coordinates for an anchor when they are unambiguous."""

    text = str(anchor or "").strip()
    if not text:
        return {}

    evidence = violation.get("evidence") if isinstance(violation.get("evidence"), dict) else {}
    spans = violation.get("evidence_spans") or evidence.get("evidence_spans") or []
    if isinstance(spans, list):
        for item in spans:
            if not isinstance(item, dict):
                continue
            item_text = str(item.get("text") or item.get("span") or "").strip()
            start = item.get("start")
            end = item.get("end")
            if item_text == text and isinstance(start, int) and isinstance(end, int):
                return {"span_start": start, "span_end": end}

    scene_text = str(violation.get("_candidate_text") or "")
    if not scene_text:
        return {}
    matches = list(re.finditer(re.escape(text), scene_text))
    if len(matches) != 1:
        return {}
    match = matches[0]
    return {"span_start": match.start(), "span_end": match.end()}


# ---------------------------------------------------------------------------
# 统一 anchor resolver（Phase 4）
# 优先级见指南 §7：evidence_spans > target_span > semantic_anchor > fallback
# ---------------------------------------------------------------------------

def _resolve_route_anchor(violation: dict[str, Any], route: Any) -> str:
    """按路由表声明的 anchor_strategy 解析首选 anchor。

    9.4.1+: 新增 evidence_span_first 策略——优先使用 evidence_span，
    因为 evidence_span 是真正有问题的句子，target_span 可能被错误定位。
    """
    strategy = getattr(route, "anchor_strategy", "") or ""

    # evidence_span_first：优先用 evidence_span，target_span 只作辅助
    if strategy == "evidence_span_first":
        evidence_span = _first_evidence_span(violation, allow_generated=False)
        if evidence_span and not _looks_like_diagnostic_sentence(evidence_span):
            return evidence_span
        # evidence_span 缺失或像诊断句，降级到 target_span
        localized = _localized_target_span(violation)
        if localized:
            return localized
        # 最后降级到任意 evidence_span（含 generated）
        evidence_span = _first_evidence_span(violation, allow_generated=True)
        if evidence_span and not _looks_like_diagnostic_sentence(evidence_span):
            return evidence_span
        return ""

    # 默认策略：localized_target_span 优先
    localized = _localized_target_span(violation)
    if localized:
        return localized
    evidence = violation.get("evidence") if isinstance(violation.get("evidence"), dict) else {}
    spans = violation.get("evidence_spans") or evidence.get("evidence_spans") or []
    if isinstance(spans, list):
        for item in spans:
            if isinstance(item, dict):
                text = str(item.get("text") or item.get("span") or "").strip()
            else:
                text = str(item or "").strip()
            if text and not _looks_like_diagnostic_sentence(text):
                return text
    return ""


def _resolve_route_fallback_anchor(violation: dict[str, Any], route: Any) -> str:
    """按路由表声明的 fallback_anchor_strategy 从 scene_text 生成可用 anchor。

    关键：不能因为 target_span 缺失直接返回 None（指南 §6.3 要求）。
    """
    scene_text = str(violation.get("_candidate_text") or "")
    if not scene_text:
        return ""
    sentences = [
        item.strip()
        for item in re.split(r"(?<=[\u3002\uff01\uff1f!?])\s*|\n+", scene_text)
        if item and item.strip()
    ]
    if not sentences:
        return ""
    strategy = getattr(route, "fallback_anchor_strategy", "") or ""
    metric = getattr(route, "metric", "")

    # 合同完成类：取最后一个动作句
    if strategy == "last_action_sentence" or metric in {
        "ending_state_not_reached", "missing_must_show", "goal_present",
        "conflict_present", "value_change", "scene_goal_missing",
        "missing_micro_payoff", "weak_chapter_end_hook",
    }:
        return sentences[-1][:120]

    # 开头钩子类：取第一个动作句
    if strategy == "first_action_sentence" or metric in {
        "weak_opening_hook", "weak_curiosity_engine",
    }:
        return sentences[0][:120]

    # 商业压力类：找含压力词的句子，否则取中间动作句
    if strategy == "first_or_middle_action_sentence":
        pressure_tokens = ["\u8ffd", "\u5371\u9669", "\u75db", "\u8840", "\u538b", "\u6765\u4e0d\u53ca", "\u5426\u5219"]
        for sentence in sentences:
            if any(token in sentence for token in pressure_tokens):
                return sentence[:120]
        return sentences[min(len(sentences) - 1, max(0, len(sentences) // 2))][:120]

    # 抽象具体化类：找含抽象词的句子，否则取最近的动作句
    if strategy == "nearest_action_sentence":
        abstract_tokens = ["\u547d\u8fd0", "\u610f\u4e49", "\u672c\u8d28", "\u4ef7\u503c", "\u590d\u6742", "\u60c5\u7eea"]
        for sentence in sentences:
            if any(token in sentence for token in abstract_tokens):
                return sentence[:120]
        return sentences[min(len(sentences) - 1, max(0, len(sentences) // 2))][:120]

    # 9.4.1+: localized_context_window——用 evidence_span 关键词在正文中定位上下文窗口
    if strategy == "localized_context_window":
        # 尝试用 evidence_span 的关键词在正文中找到唯一候选句
        evidence_span = _first_evidence_span(violation, allow_generated=False)
        if evidence_span:
            # 提取关键词（>2 字的连续中文片段）
            keywords = re.findall(r"[\u4e00-\u9fff]{2,8}", evidence_span)
            # 用最长的 2-3 个关键词在正文中找句
            keywords_sorted = sorted(set(keywords), key=len, reverse=True)[:3]
            for sentence in sentences:
                if any(kw in sentence for kw in keywords_sorted) and not _looks_like_diagnostic_sentence(sentence):
                    return sentence[:200]
        # 降级：找含冲突/位置词的句子
        conflict_tokens = ["\u6c14\u606f", "\u4f4d\u7f6e", "\u5728\u573a", "\u540c\u65f6", "\u8fdc\u5904", "\u8fd1\u5904", "\u6d1e\u5185", "\u5916\u9762"]
        for sentence in sentences:
            if any(token in sentence for token in conflict_tokens):
                return sentence[:200]
        return sentences[min(len(sentences) - 1, max(0, len(sentences) // 2))][:120]

    # 默认：取中间动作句
    return sentences[min(len(sentences) - 1, max(0, len(sentences) // 2))][:120]


# ---------------------------------------------------------------------------
# 统一 postcondition strategy（Phase 5）
# 验证补丁效果，不验证诊断语是否被照抄（指南 §11）
# ---------------------------------------------------------------------------

_PRESSURE_KEYWORDS = ["\u8ffd", "\u5371\u9669", "\u75db", "\u8840", "\u538b", "\u6765\u4e0d\u53ca", "\u5426\u5219", "\u53ea\u5269"]
# 方案 30：_QUESTION_KEYWORDS 降级为兜底——B 类 hook metric 的后置校验改用 LLM 判定。
# 此列表仅在 _postcondition_required_spans 的 question_keywords 策略中用作预筛辅助。
_QUESTION_KEYWORDS = ["\u4e3a\u4ec0\u4e48", "\u54ea\u91cc", "\u600e\u4e48", "\u5f02\u6837", "\u8bf7", "\u4e48", "\u5417"]
_PAYOFF_KEYWORDS = ["\u7ec8\u4e8e", "\u6210\u4e86", "\u7761\u5f00", "\u91d1\u4e39", "\u4e39", "\u836f", "\u89e3\u6bd2", "\u6ca1\u6709\u843d\u7a7a"]


# ---------------------------------------------------------------------------
# 上下文感知插入文本生成（9.1.5 改进）
# ---------------------------------------------------------------------------


def _postcondition_required_spans(strategy: str, insert_text: str, metric: str = "") -> list[str]:
    """按 postcondition_strategy 生成 required_spans。

    所有 span 必须是 insert_text 的子串（executor 校验 span in after_text）。
    """
    if not insert_text:
        return []
    if strategy == "contract_action_keywords":
        spans: list[str] = []
        for marker in ("\u6b63\u5f0f\u5f00\u59cb\u70bc\u4e39", "\u4e94\u5e08\u5144", "\u524d\u4e16\u8bb0\u5fc6"):
            if marker in insert_text:
                spans.append(marker)
        if not spans:
            tail = insert_text.rstrip("\u3002.\uff1b;\uff1b").strip()
            if tail:
                spans.append(tail[-20:])
        return spans
    if strategy == "inserted_observable_evidence":
        # 从实际插入文本里提取感官/动作片段
        for marker in ("\u6307\u5c16", "\u8840\u8165\u5473", "\u547c\u5438", "\u6307\u8282", "\u89c6\u7ebf", "\u6307\u8179"):
            if marker in insert_text:
                return [marker]
        tail = insert_text.rstrip("\u3002.\uff1b;\uff1b").strip()
        return [tail[-20:]] if tail else []
    if strategy == "pressure_keywords":
        for marker in _PRESSURE_KEYWORDS:
            if marker in insert_text:
                return [marker]
        tail = insert_text.rstrip("\u3002.\uff1b;\uff1b").strip()
        return [tail[-20:]] if tail else []
    if strategy == "question_keywords":
        for marker in _QUESTION_KEYWORDS:
            if marker in insert_text:
                return [marker]
        tail = insert_text.rstrip("\u3002.\uff1b;\uff1b").strip()
        return [tail[-20:]] if tail else []
    if strategy == "payoff_keywords":
        for marker in _PAYOFF_KEYWORDS:
            if marker in insert_text:
                return [marker]
        tail = insert_text.rstrip("\u3002.\uff1b;\uff1b").strip()
        return [tail[-20:]] if tail else []
    if strategy == "fact_resolved":
        # 事实修复：验证修正后的文本片段出现
        tail = insert_text.rstrip("\u3002.\uff1b;\uff1b").strip()
        return [tail[-40:]] if tail else []
    if strategy == "causal_link_keywords":
        for marker in ("前因", "后果", "并非偶然", "追上"):
            if marker in insert_text:
                return [marker]
        tail = insert_text.rstrip("\u3002.\uff1b;\uff1b").strip()
        return [tail[-20:]] if tail else []
    if strategy == "pattern_removed":
        # not_but 模式移除：验证新文本不包含"不是…而是…"
        if "不是" in insert_text and "而是" in insert_text:
            return []  # 仍包含模式，返回空让验收失败
        tail = insert_text.rstrip("\u3002.\uff1b;\uff1b").strip()
        return [tail[-20:]] if tail else []
    # 默认：replacement_text_present
    tail = insert_text.rstrip("\u3002.\uff1b;\uff1b").strip()
    return [tail[-40:]] if tail else []


# ---------------------------------------------------------------------------
# 统一 blueprint builders（Phase 3）
# 每个 builder 返回 tool_blueprint dict，供 _tool_blueprint_command 调用
# ---------------------------------------------------------------------------

def _build_registered_intent_blueprint(
    *,
    violation: dict[str, Any],
    metric: str,
    family: str,
    operation: str,
    target: str,
    issue_id: str,
    source: str,
) -> dict[str, Any] | None:
    """Compile registry-declared tool intents without widening edit scope.

    Window transforms retain their intent operation here; WorkUnitBuilder runs
    the same deterministic ToolExecutor primitive against the localized text
    and emits an exact patch.  Structural rhythm repairs are expressed as
    exact edits immediately because they only add a boundary/transition word
    and do not need invented plot content.
    """
    target = str(target or "").strip()
    if not target:
        return None

    base = {
        "target_span": target,
        "anchor_text": target,
        "old_text": target,
        "scope": "target_scene",
        "repair_family": family,
        "source": source,
        "source_issue_ids": [issue_id],
        "metric": metric,
        "rationale": f"Compile registry metric {metric} via {operation}.",
        "guards": {
            "preserve_facts": True,
            "preserve_pov": True,
            "forbid_unrelated_edits": True,
        },
        **_localized_locator_fields(violation, target),
    }

    transform_operations = {
        "normalize_punctuation",
        "normalize_structure_words",
        "replace_tier1_ai_flavor_terms",
        "cleanup_ai_flavor_window",
        "trim_discourse_window",
        "vary_sentence_shape",
        "vary_sentence_length_window",
        "rewrite_voice_window",
        "split_paragraph",
    }
    if operation in transform_operations:
        return {"operation": operation, **base}

    if operation == "insert_functional_breathing_paragraph":
        replacement = "\n\n" + target
        return {
            "operation": "replace_exact",
            **base,
            "new_text": replacement,
            "replacement": replacement,
            "postconditions": {"required_spans": [replacement], "forbidden_spans": []},
        }

    if operation in {"insert_transition_anchor", "smooth_abrupt_shift"}:
        if re.match(r"^(?:随后|继而|与此同时|就在这时|片刻后)[，,]", target):
            return None
        replacement = "随后，" + target
        return {
            "operation": "replace_exact",
            **base,
            "new_text": replacement,
            "replacement": replacement,
            "postconditions": {"required_spans": ["随后，"], "forbidden_spans": []},
        }

    return None

def _build_contract_completion_insert(
    *,
    violation: dict[str, Any],
    route: Any,
    anchor: str,
    issue_id: str,
    source: str,
) -> dict[str, Any] | None:
    """结构合同类 builder。复用现有 _contract_completion_sentence 逻辑。"""
    required = _contract_required_state(violation)
    if not required or not anchor:
        return None
    insert_text = _contract_completion_sentence(required)
    if not insert_text:
        return None
    required_spans = _postcondition_required_spans(
        route.postcondition_strategy, insert_text, route.metric
    )
    locator = _localized_locator_fields(violation, anchor)
    return {
        "operation": "insert_after_anchor",
        "target_span": anchor,
        "after_span": anchor,
        "replacement": "\n\n" + insert_text,
        "new_text": insert_text,
        "scope": "target_scene",
        "repair_family": "contract_completion",
        "source": source,
        "source_issue_ids": [issue_id],
        "rationale": f"Compile {route.metric} into a bounded contract-completion beat.",
        "postconditions": {
            "required_spans": required_spans,
            "forbidden_spans": [],
        },
        **locator,
    }


def _build_show_evidence_patch(
    *,
    violation: dict[str, Any],
    route: Any,
    anchor: str,
    issue_id: str,
    source: str,
) -> dict[str, Any] | None:
    """为已定位的抽象句补一个可观察证据节拍。"""
    if not anchor:
        return None
    insert_text = "她的指节收紧，呼吸在这一刻停了半拍。"
    return _make_insert_command(
        violation=violation, route=route, anchor=anchor,
        insert_text=insert_text, issue_id=issue_id, source=source,
    )


def _build_commercial_pressure_insert(
    *,
    violation: dict[str, Any],
    route: Any,
    anchor: str,
    issue_id: str,
    source: str,
) -> dict[str, Any] | None:
    """为已定位的平静节拍插入有代价的现场压力。"""
    if not anchor:
        return None
    insert_text = "可留给他们的时间又少了一截，余地被压得更窄。"
    return _make_insert_command(
        violation=violation, route=route, anchor=anchor,
        insert_text=insert_text, issue_id=issue_id, source=source,
    )


def _build_commercial_hook_insert(
    *,
    violation: dict[str, Any],
    route: Any,
    anchor: str,
    issue_id: str,
    source: str,
) -> dict[str, Any] | None:
    """在已定位动作后保留一个未解问题。"""
    if not anchor:
        return None
    insert_text = "可那道异响究竟从哪里来，仍没有答案。"
    return _make_insert_command(
        violation=violation, route=route, anchor=anchor,
        insert_text=insert_text, issue_id=issue_id, source=source,
    )


def _build_commercial_payoff_insert(
    *,
    violation: dict[str, Any],
    route: Any,
    anchor: str,
    issue_id: str,
    source: str,
) -> dict[str, Any] | None:
    """为已完成的局部尝试补上可见回报。"""
    if not anchor:
        return None
    insert_text = "至少眼前这一处变化，证明她的判断没有落空。"
    return _make_insert_command(
        violation=violation, route=route, anchor=anchor,
        insert_text=insert_text, issue_id=issue_id, source=source,
    )


def _build_pov_externalization_replace(
    *, violation: dict[str, Any], route: Any, anchor: str,
    issue_id: str, source: str,
) -> dict[str, Any] | None:
    old_text = _localized_target_span(violation) or anchor
    if not old_text:
        return None
    replacement = re.sub(r"心里一沉|心中一沉", "的动作顿了一下", old_text)
    if replacement == old_text:
        replacement = "他的动作顿了一下"
    return _make_replace_command(
        violation=violation, route=route, old_text=old_text,
        new_text=replacement, issue_id=issue_id, source=source,
    )


def _build_time_anchor_insert(
    *, violation: dict[str, Any], route: Any, anchor: str,
    issue_id: str, source: str,
) -> dict[str, Any] | None:
    if not anchor:
        return None
    return _make_insert_command(
        violation=violation, route=route, anchor=anchor,
        insert_text="洞外的天色又暗了一层。",
        issue_id=issue_id, source=source,
    )


def _build_pure_exposition_split(
    *, violation: dict[str, Any], route: Any, anchor: str,
    issue_id: str, source: str,
) -> dict[str, Any] | None:
    old_text = _localized_target_span(violation) or anchor
    if not old_text:
        return None
    return _make_replace_command(
        violation=violation, route=route, old_text=old_text,
        new_text="\n\n" + old_text, issue_id=issue_id, source=source,
    )


# ---------------------------------------------------------------------------
# 事实一致性类 builder（从零编写，不复用 _patch_clue_provenance 等返回 str 的函数）
# ---------------------------------------------------------------------------


def _extract_fact_correction(violation: dict[str, Any]) -> str:
    """从 violation.detail/evidence 提取正确事实（authority claim）。"""
    detail = str(violation.get("detail") or "")
    # 尝试匹配 "Text claim '...' conflicts with established fact '...'" 模式
    match = re.search(r"established fact ['\"](.+?)['\"]", detail)
    if match:
        return match.group(1).strip()
    # 尝试匹配 "将正文中的 X 改为 Y" 模式
    match = re.search(r"改为(.+?)(?:，|。|,|;|；|$)", detail)
    if match:
        return match.group(1).strip(" '\"""''")
    # 尝试 evidence 字段
    evidence = violation.get("evidence")
    if isinstance(evidence, dict):
        correction = str(evidence.get("correction") or evidence.get("authority_claim") or "").strip()
        if correction:
            return correction
    return ""


def _rewrite_not_but(text: str) -> str:
    """重写'不是…而是…'句式，保留后半并改为自然陈述。"""
    if not text:
        return ""
    # 匹配 "不是X而是Y" 或 "并非X而是Y"
    match = re.search(r"(?:不是|并非)(.+?)而是(.+)", text)
    if match:
        first = match.group(1).strip()
        second = match.group(2).strip()
        # 去掉末尾标点后重新组合
        terminal = ""
        if second and second[-1] in "。！？":
            terminal = second[-1]
            second = second[:-1]
        if first and second:
            return f"{second}{terminal or '。'}"
    return ""


def _make_replace_command(
    *,
    violation: dict[str, Any],
    route: Any,
    old_text: str,
    new_text: str,
    issue_id: str,
    source: str,
    postconditions: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构造 replace_exact 命令字典。

    9.4.1+: 支持通过 postconditions 参数覆盖默认的 postcondition 生成，
    让 builder 能传递自定义的 conflict_resolved / conflict_type 等字段。
    """
    locator = _localized_locator_fields(violation, old_text)
    if postconditions is not None:
        final_postconditions = postconditions
    else:
        final_postconditions = {
            "required_spans": _postcondition_required_spans(
                route.postcondition_strategy, new_text, route.metric
            ),
            "forbidden_spans": [old_text] if route.postcondition_strategy == "pattern_removed" else [],
        }
    return {
        "operation": "replace_exact",
        "target_span": old_text,
        "anchor_text": old_text,
        "old_text": old_text,
        "new_text": new_text,
        "replacement": new_text,
        "scope": "target_scene",
        "repair_family": route.family,
        "source": source,
        "source_issue_ids": [issue_id],
        "metric": route.metric,
        "rationale": f"Resolve {route.metric} via route builder {route.builder}.",
        "postconditions": final_postconditions,
        **locator,
    }


def _make_insert_command(
    *,
    violation: dict[str, Any],
    route: Any,
    anchor: str,
    insert_text: str,
    issue_id: str,
    source: str,
) -> dict[str, Any]:
    """构造 insert_after_anchor 命令字典。"""
    locator = _localized_locator_fields(violation, anchor)
    return {
        "operation": "insert_after_anchor",
        "target_span": anchor,
        "after_span": anchor,
        "anchor_text": anchor,
        "replacement": "\n\n" + insert_text,
        "new_text": insert_text,
        "scope": "target_scene",
        "repair_family": route.family,
        "source": source,
        "source_issue_ids": [issue_id],
        "metric": route.metric,
        "rationale": f"Resolve {route.metric} via route builder {route.builder}.",
        "postconditions": {
            "required_spans": _postcondition_required_spans(
                route.postcondition_strategy, insert_text, route.metric
            ),
            "forbidden_spans": [],
        },
        **locator,
    }


def _build_fact_conflict_patch(
    *,
    violation: dict[str, Any],
    route: Any,
    anchor: str,
    issue_id: str,
    source: str,
) -> dict[str, Any] | None:
    """fact_conflict builder：基于 target_span 构造 replace_exact 命令。"""
    target_span = _localized_target_span(violation)
    detail = str(violation.get("detail") or "")
    claim_match = re.search(r"Text claim ['\"](.+?)['\"]", detail, re.IGNORECASE)
    candidate_text = str(violation.get("_candidate_text") or "")
    if claim_match:
        claimed_text = claim_match.group(1).strip()
        if claimed_text and (not candidate_text or claimed_text in candidate_text):
            target_span = claimed_text
    if not target_span:
        return None
    correction = _extract_fact_correction(violation)
    if not correction:
        return None
    return _make_replace_command(
        violation=violation, route=route, old_text=target_span,
        new_text=correction, issue_id=issue_id, source=source,
    )


def _build_clue_provenance_patch(
    *,
    violation: dict[str, Any],
    route: Any,
    anchor: str,
    issue_id: str,
    source: str,
) -> dict[str, Any] | None:
    """clue_provenance_error builder：返回 None，让 LLM 修复链路创作溯源从句。

    通用修复 C-2：移除硬编码从句"她先前发现后将它收起..."——这是特化修复
    （只适用于女性角色 + 特定场景）。线索来源的补写需要语义理解，应由 LLM
    修复链路（scene_repairer / FBI fact_local_patch）根据上下文创作合适的溯源表述。
    与 _build_show_evidence_patch 一致，确定性修复层不做具象化创作。
    """
    return None


def _build_causal_chain_patch(
    *,
    violation: dict[str, Any],
    route: Any,
    anchor: str,
    issue_id: str,
    source: str,
) -> dict[str, Any] | None:
    """causal_chain_error builder：在 anchor 后插入因果连接句。"""
    target_span = _localized_target_span(violation)
    if not target_span:
        return None
    causal_clause = "这一步并非偶然，而是前因终于追上了后果。"
    return _make_insert_command(
        violation=violation, route=route, anchor=target_span,
        insert_text=causal_clause, issue_id=issue_id, source=source,
    )


def _build_not_but_pattern_patch(
    *,
    violation: dict[str, Any],
    route: Any,
    anchor: str,
    issue_id: str,
    source: str,
) -> dict[str, Any] | None:
    """not_but_pattern builder：替换'不是…而是…'句式。"""
    target_span = _localized_target_span(violation)
    if not target_span:
        return None
    rewritten = _rewrite_not_but(target_span)
    if not rewritten or rewritten == target_span:
        return None
    return _make_replace_command(
        violation=violation, route=route, old_text=target_span,
        new_text=rewritten, issue_id=issue_id, source=source,
    )


# ---------------------------------------------------------------------------
# 通用 anti_ai / show_evidence replace builders
# 从 violation 结构化字段提取 old_text/new_text，不依赖 metric 名硬编码。
# ---------------------------------------------------------------------------


def _build_anti_ai_term_replace(
    *,
    violation: dict[str, Any],
    route: Any,
    anchor: str,
    issue_id: str,
    source: str,
) -> dict[str, Any] | None:
    """通用 anti_ai term replace builder。

    适用于 tier1_hit_count / vague_attribution_count 等 anti_ai_local metric。
    从 violation 的 target_span / evidence_spans 提取 old_text，
    从 suggested_repair_text 或 evidence.action 提取 new_text。
    若无法生成 new_text，返回 None 让上层标记 blueprint_incomplete。
    """
    old_text = _localized_target_span(violation) or anchor
    if not old_text:
        return None
    # 优先用 suggested_repair_text
    new_text = _suggested_repair_text(violation, "")
    if not new_text:
        # 通用 fallback：从 evidence.action 推导
        evidence = violation.get("evidence") if isinstance(violation.get("evidence"), dict) else {}
        action = str(evidence.get("action") or "").lower()
        if action == "tier1_replace":
            # 通用策略：删除 old_text 中的 Tier1 词汇（不硬编码具体词）
            new_text = _strip_ai_flavor_terms(old_text)
        elif action == "replace_vague_attribution":
            # 通用策略：把模糊归因改为具体主语
            new_text = _concrete_attribution_rewrite(old_text)
        else:
            # 通用 fallback：evidence.action 缺失时，按 metric family 选择默认策略。
            # 这是通用逻辑，不针对特定内容：anti_ai metric 默认尝试删除 AI 词汇。
            new_text = _strip_ai_flavor_terms(old_text)
    if not new_text or new_text == old_text:
        return None
    return _make_replace_command(
        violation=violation, route=route, old_text=old_text,
        new_text=new_text, issue_id=issue_id, source=source,
    )


def _build_emotion_to_evidence_replace(
    *,
    violation: dict[str, Any],
    route: Any,
    anchor: str,
    issue_id: str,
    source: str,
) -> dict[str, Any] | None:
    """通用 emotion_label → observable evidence replace builder。

    适用于 emotion_label_count 等 show_evidence metric。
    把"瞳孔里有愤怒也有恐惧"这类情绪标签句改写为可观察证据。
    """
    old_text = _localized_target_span(violation) or anchor
    if not old_text:
        return None
    # 优先用 suggested_repair_text
    new_text = _suggested_repair_text(violation, "")
    if not new_text:
        # 通用策略：把情绪标签替换为可观察的身体反应
        new_text = _emotion_to_observable_evidence(old_text)
    if not new_text or new_text == old_text:
        return None
    return _make_replace_command(
        violation=violation, route=route, old_text=old_text,
        new_text=new_text, issue_id=issue_id, source=source,
    )


def _build_uniform_streak_replace(
    *,
    violation: dict[str, Any],
    route: Any,
    anchor: str,
    issue_id: str,
    source: str,
) -> dict[str, Any] | None:
    """通用 uniform_sentence_streak replace builder。

    适用于 uniform_sentence_streak_max 等 anti_ai_discourse metric。
    改写句长均匀的段落，使句长有变化。
    """
    old_text = _localized_target_span(violation) or anchor
    if not old_text:
        return None
    # 优先用 suggested_repair_text
    new_text = _suggested_repair_text(violation, "")
    if not new_text:
        # 通用策略：合并短句或拆分长句，使句长有变化
        new_text = _vary_sentence_length(old_text)
    if not new_text or new_text == old_text:
        return None
    return _make_replace_command(
        violation=violation, route=route, old_text=old_text,
        new_text=new_text, issue_id=issue_id, source=source,
    )


# ----------------------------------------------------------------------
# 方案 26 Part D：风格类 metric builder
# ----------------------------------------------------------------------

def _build_forbidden_word_replace(
    *,
    violation: dict[str, Any],
    route: Any,
    anchor: str,
    issue_id: str,
    source: str,
) -> dict[str, Any] | None:
    """风格禁用词 / avoid 模式违反 → replace_exact builder。

    适用于 forbidden_word / avoid_pattern_violation 等 style_consistency metric。
    从 violation.target_span 提取 old_text，从 suggestion 字段提取 new_text。
    若无 suggestion，直接删除禁用词（替换为空）。
    """
    old_text = _localized_target_span(violation) or anchor
    if not old_text:
        return None
    # 优先用 suggestion 字段（StyleReviewAdapter 在 extra 中设置）
    suggestion = ""
    for source_dict in (violation, violation.get("evidence") if isinstance(violation.get("evidence"), dict) else {}):
        if isinstance(source_dict, dict):
            suggestion = str(source_dict.get("suggestion") or "").strip()
            if suggestion:
                break
    # 也检查 _suggested_repair_text（标准路径）
    if not suggestion:
        suggestion = _suggested_repair_text(violation, "")
    if suggestion:
        new_text = suggestion
    else:
        # 无 suggestion 时，删除禁用词本身（old_text 即禁用词/模式）
        new_text = ""
    if new_text == old_text:
        return None
    return _make_replace_command(
        violation=violation, route=route, old_text=old_text,
        new_text=new_text, issue_id=issue_id, source=source,
    )


def _build_structure_word_normalize(
    *,
    violation: dict[str, Any],
    route: Any,
    anchor: str,
    issue_id: str,
    source: str,
) -> dict[str, Any] | None:
    """结构词堆积 → normalize_structure_words intent builder。

    适用于 structure_word_cluster_count metric。
    产出 intent operation，由 WorkUnitBuilder 编译为确定性操作。
    """
    target_span = _localized_target_span(violation) or anchor
    if not target_span:
        return None
    locator = _localized_locator_fields(violation, target_span)
    return {
        "operation": "normalize_structure_words",
        "target_span": target_span,
        "anchor_text": target_span,
        "max_replacements": 3,
        "scope": "target_scene",
        "repair_family": route.family,
        "source": source,
        "source_issue_ids": [issue_id],
        "metric": route.metric,
        "rationale": f"Resolve {route.metric} via route builder {route.builder}.",
        **locator,
    }


def _build_sentence_rhythm_vary(
    *,
    violation: dict[str, Any],
    route: Any,
    anchor: str,
    issue_id: str,
    source: str,
) -> dict[str, Any] | None:
    """句式节奏重复 → vary_sentence_shape intent builder。

    适用于 uniform_sentence_streak metric（family=sentence_rhythm）。
    """
    target_span = _localized_target_span(violation) or anchor
    if not target_span:
        return None
    locator = _localized_locator_fields(violation, target_span)
    return {
        "operation": "vary_sentence_shape",
        "target_span": target_span,
        "anchor_text": target_span,
        "scope": "target_scene",
        "repair_family": route.family,
        "source": source,
        "source_issue_ids": [issue_id],
        "metric": route.metric,
        "rationale": f"Resolve {route.metric} via route builder {route.builder}.",
        **locator,
    }


def _build_paragraph_shape_split(
    *,
    violation: dict[str, Any],
    route: Any,
    anchor: str,
    issue_id: str,
    source: str,
) -> dict[str, Any] | None:
    """段落形态重复 → split_paragraph intent builder。

    适用于 paragraph_shape_repeat metric。
    """
    target_span = _localized_target_span(violation) or anchor
    if not target_span:
        return None
    locator = _localized_locator_fields(violation, target_span)
    return {
        "operation": "split_paragraph",
        "target_span": target_span,
        "anchor_text": target_span,
        "scope": "target_scene",
        "repair_family": route.family,
        "source": source,
        "source_issue_ids": [issue_id],
        "metric": route.metric,
        "rationale": f"Resolve {route.metric} via route builder {route.builder}.",
        **locator,
    }


def _strip_ai_flavor_terms(text: str) -> str:
    """通用：删除文本中的 Tier1 AI 词汇。

    复用 tool_executor 中的 Tier1 词汇表，保持单一事实来源。
    不硬编码特定词汇。
    """
    if not text:
        return ""
    # 与 tool_executor._replace_tier1_ai_flavor_terms 保持一致的词汇表
    replacements = {
        "仿佛": "像",
        "似乎": "",
        "某种意义上": "",
        "某种": "",
        "这说明": "",
        "这意味着": "",
        "显然": "",
        "事实上": "",
        "换句话说": "",
        "真正": "",
        "复杂的情绪": "情绪",
        "复杂": "",
        "内心": "",
        "灵魂": "",
        "命运": "",
        "象征": "像",
        "代表": "是",
        "体现": "露出",
        "无法形容": "",
        "难以言说": "",
        "薄薄的": "一层",
        "微光": "热意",
        "一瞬": "一下",
        "不由得": "",
        "忍不住": "",
    }
    result = text
    applied = False
    for old, new in replacements.items():
        if old in result:
            result = result.replace(old, new, 1)
            applied = True
    # 清理多余标点和空格
    result = re.sub(r"[，,]\s*[，,]", "，", result)
    result = re.sub(r"\s+", "", result)
    result = re.sub(r"^[，,、]+|[，,、]+$", "", result)
    return result.strip() if applied and result.strip() else ""


def _concrete_attribution_rewrite(text: str) -> str:
    """通用：把模糊归因句改写为具体主语句。

    例如"听到背后有人说话" → "听到背后传来脚步声和低语"。
    策略：把"有人"替换为更具体的描述，但不硬编码特定角色名。
    """
    if not text:
        return ""
    # 通用替换：模糊归因词 → 具体描述
    replacements = {
        "有人说话": "传来低语声",
        "有人": "传来声响",
        "什么东西": "某个物体",
        "似乎": "",
        "好像": "",
        "仿佛": "",
    }
    result = text
    for old, new in replacements.items():
        if old in result:
            result = result.replace(old, new)
    # 清理多余标点
    result = re.sub(r"\s+", "", result)
    result = re.sub(r"[，,]{2,}", "，", result)
    return result.strip() if result.strip() else ""


def _emotion_to_observable_evidence(text: str) -> str:
    """通用：把情绪标签替换为可观察的身体反应。

    例如"瞳孔里有愤怒也有恐惧" → "瞳孔紧缩，身体僵住"。
    策略：删除情绪名词，替换为可观察的生理反应，同时清理程度副词。
    """
    if not text:
        return ""
    # 通用情绪词 → 可观察反应映射
    emotion_to_observable = {
        "愤怒": "龇出牙齿",
        "恐惧": "身体后缩",
        "害怕": "身体后缩",
        "担心": "眉头紧锁",
        "紧张": "肌肉绷紧",
        "悲伤": "垂下眼帘",
        "难过": "垂下眼帘",
        "震惊": "瞳孔紧缩",
        "开心": "嘴角上扬",
        "情绪": "神情变化",
    }
    result = text
    for emotion, observable in emotion_to_observable.items():
        if emotion in result:
            result = result.replace(emotion, observable)
    # 通用清理：程度副词在替换后不再适用（"很身体后缩" → "身体后缩"）
    degree_adverbs = ["很", "非常", "十分", "极其", "格外", "尤其", "相当"]
    for adv in degree_adverbs:
        result = result.replace(adv, "")
    # 如果还有"也有"连接两个反应，改为更自然的并列
    result = re.sub(r"也有", "，", result)
    # 清理
    result = re.sub(r"\s+", "", result)
    return result.strip() if result.strip() else ""


def _vary_sentence_length(text: str) -> str:
    """通用：改变句长均匀的段落，使句长有变化。

    策略：合并相邻短句或拆分长句，使句长分布不均匀。
    """
    if not text:
        return ""
    # 按句号分句
    sentences = re.split(r"(?<=[。！？])", text)
    sentences = [s for s in sentences if s.strip()]
    if len(sentences) < 2:
        return ""
    # 通用策略：合并前两个短句为一个长句，使句长分布不均匀
    first = sentences[0].rstrip("。！？")
    second = sentences[1].rstrip("。！？")
    merged = first + "，" + second + "。"
    return merged + "".join(sentences[2:])


def _detect_spatial_conflict_type(violation: dict[str, Any], old_text: str) -> str:
    """检测空间冲突的子类型，决定 replacement 策略。

    返回值：
    - "deny_misidentification": 否定误认（"是某人的气息" -> "不是某人，而是陌生/相似"）
    - "transfer_source": 转移来源（"某人在远处" -> "远处另有魔气/追兵"）
    - "eliminate_simultaneous_presence": 消除同时在场（删除/改写同时出现在两地的判断句）
    - "generic": 通用替换
    """
    detail = str(violation.get("detail") or "").lower()
    evidence = violation.get("evidence") if isinstance(violation.get("evidence"), dict) else {}
    evidence_text = str(evidence.get("text") or evidence.get("summary") or "").lower()
    blob = f"{detail} {evidence_text} {old_text}".lower()

    # 同时在场：detail 或 evidence 提到 "同时" / "两处" / "both" / "simultaneous"
    simultaneous_tokens = ["同时", "两处", "两地", "both", "simultaneous", "同一时间"]
    if any(token in blob for token in simultaneous_tokens):
        return "eliminate_simultaneous_presence"

    # 否定误认：old_text 含 "是...的气息" / "is ...'s presence" / 识别判断
    misidentification_patterns = [
        r"是.{1,20}的气息",
        r"认出.{1,20}",
        r"识别.{1,20}",
        r"is\s+.{1,30}['\"]?s\s+(presence|aura|breath)",
        r"那是.{1,20}的",
    ]
    for pattern in misidentification_patterns:
        if re.search(pattern, old_text, re.IGNORECASE):
            return "deny_misidentification"

    # 转移来源：detail 提到 "远处" / "另一处" / "elsewhere" / "another location"
    transfer_tokens = ["远处", "另一处", "另一地", "elsewhere", "another location", "origin"]
    if any(token in blob for token in transfer_tokens):
        return "transfer_source"

    return "generic"


def _generate_spatial_replacement(old_text: str, conflict_type: str, violation: dict[str, Any]) -> str:
    """根据冲突类型生成 replacement 文本。

    蓝图官产出明确命令，工具只负责替换。
    """
    # 优先使用 violation 自带的 correction / suggested_correction
    evidence = violation.get("evidence") if isinstance(violation.get("evidence"), dict) else {}
    correction = str(
        evidence.get("correction")
        or evidence.get("suggested_correction")
        or violation.get("suggested_correction")
        or violation.get("expected_text")
        or ""
    ).strip()
    if correction and not _looks_like_instruction(correction) and not _looks_like_quality_contract_statement(correction):
        return correction

    if conflict_type == "deny_misidentification":
        # 否定误认：把"是某人的气息"改成"不是某人的气息，而是相似/陌生/被污染的气息"
        # 尝试提取被误认的对象
        match = re.search(r"是(.{1,20}?)的(气息|身影|声音|味道)", old_text)
        if match:
            target = match.group(1)
            attribute = match.group(2)
            return f"那道{attribute}里夹着陌生的邪性，并不是{target}的{attribute}。"
        # 英文模式
        match = re.search(r"is\s+(.{1,30}?)['\"]?s\s+(presence|aura|breath)", old_text, re.IGNORECASE)
        if match:
            return f"那道气息里夹着陌生的邪性，并不是{match.group(1)}的气息。"
        # 通用否定
        return "那气息里夹着陌生的邪性，并不是她先前认出的那个人。"

    if conflict_type == "transfer_source":
        # 转移来源：把"某人在远处"改成"远处另有魔气/追兵/阵法残息"
        match = re.search(r"(远处|另一处|另一地)(.{1,30})", old_text)
        if match:
            location = match.group(1)
            rest = match.group(2)
            return f"{location}另有魔气翻涌，并不是她先前以为的{rest.strip('，。；')}"
        return "远处另有魔气翻涌，并不是她先前以为的那个人。"

    if conflict_type == "eliminate_simultaneous_presence":
        # 消除同时在场：删除或改写会造成同一人物同时出现在两地的判断句
        # 策略：把判断句改为"她无法确认"或"那只是错觉"
        if "气息" in old_text:
            return "她无法确认那道气息的来源，只觉得其中夹着陌生的邪性。"
        if "身影" in old_text or "身影" in old_text:
            return "那道身影模糊一闪，她无法确认是不是她以为的那个人。"
        return "她无法确认眼前所见，那更像是错觉或障眼法。"

    # generic：尝试用 authority_fact
    authority = str(evidence.get("authority_fact") or violation.get("authority_fact") or "").strip()
    if authority and not _looks_like_instruction(authority):
        return authority

    # T11.9: generic 兜底——不再返回空字符串导致整个路由失败。
    # 生成基于 old_text 的消歧占位替换，消除冲突声明并标记需要 LLM 后续完善。
    # 策略：把判断句改成"无法确认"模式，既消除冲突又保留叙事连贯性。
    if "气息" in old_text:
        return "她无法确认那道气息的来源，只觉得其中夹着陌生的异样。"
    if "身影" in old_text:
        return "那道身影模糊一闪，她无法确认是不是她以为的那个人。"
    if "声音" in old_text:
        return "那声音断续难辨，她无法确认来源。"
    if "目光" in old_text or "视线" in old_text:
        return "她无法确认那道目光的来源，只觉得其中夹着陌生的异样。"
    # 通用兜底：把 old_text 改成"无法确认"句式，保留原文片段作为上下文
    snippet = old_text.strip().rstrip("。；，")
    if len(snippet) > 30:
        snippet = snippet[:30]
    return f"她无法确认{snippet}，那更像是错觉或障眼法。"


def _build_spatial_conflict_patch(
    *,
    violation: dict[str, Any],
    route: Any,
    anchor: str,
    issue_id: str,
    source: str,
) -> dict[str, Any] | None:
    """spatial_conflict / location_conflict / simultaneous_presence_conflict builder。

    关键：优先使用 evidence_span 作为 old_text（evidence_span 是真正有问题的句子），
    target_span 只作辅助定位。强制产出 old_text + replacement + postcondition。
    """
    # 优先用 evidence_span（route.anchor_strategy == "evidence_span_first"）
    old_text = _first_evidence_span(violation, allow_generated=False)
    if not old_text or _looks_like_diagnostic_sentence(old_text):
        # 降级到 target_span / anchor
        old_text = _localized_target_span(violation) or anchor
    if not old_text:
        return None

    conflict_type = _detect_spatial_conflict_type(violation, old_text)
    replacement = _generate_spatial_replacement(old_text, conflict_type, violation)
    if not replacement:
        return None

    # postcondition：冲突声明被移除或消歧
    postcondition = {
        "required_spans": [replacement[:40]],
        "forbidden_spans": [old_text],
        "conflict_resolved": True,
        "conflict_type": conflict_type,
    }

    return _make_replace_command(
        violation=violation, route=route, old_text=old_text,
        new_text=replacement, issue_id=issue_id, source=source,
        postconditions=postcondition,
    )


# builder 名 -> 函数 的映射表
_ROUTE_BUILDERS: dict[str, Any] = {
    "build_contract_completion_insert": _build_contract_completion_insert,
    "build_show_evidence_patch": _build_show_evidence_patch,
    "build_commercial_pressure_insert": _build_commercial_pressure_insert,
    "build_commercial_hook_insert": _build_commercial_hook_insert,
    "build_commercial_payoff_insert": _build_commercial_payoff_insert,
    "build_pov_externalization_replace": _build_pov_externalization_replace,
    "build_time_anchor_insert": _build_time_anchor_insert,
    "build_pure_exposition_split": _build_pure_exposition_split,
    "build_fact_conflict_patch": _build_fact_conflict_patch,
    "build_clue_provenance_patch": _build_clue_provenance_patch,
    "build_causal_chain_patch": _build_causal_chain_patch,
    "build_not_but_pattern_patch": _build_not_but_pattern_patch,
    "build_spatial_conflict_patch": _build_spatial_conflict_patch,
    "build_anti_ai_term_replace": _build_anti_ai_term_replace,
    "build_emotion_to_evidence_replace": _build_emotion_to_evidence_replace,
    "build_uniform_streak_replace": _build_uniform_streak_replace,
    # 方案 26 Part D：风格类 metric builder
    "build_forbidden_word_replace": _build_forbidden_word_replace,
    "build_structure_word_normalize": _build_structure_word_normalize,
    "build_sentence_rhythm_vary": _build_sentence_rhythm_vary,
    "build_paragraph_shape_split": _build_paragraph_shape_split,
}


def _build_tool_blueprint_from_route(
    *,
    violation: dict[str, Any],
    route: Any,
    issue_id: str,
    source: str,
) -> tuple[dict[str, Any] | None, str]:
    """按路由表声明的 builder 生成 tool_blueprint。

    统一流程：route -> resolve_anchor -> fallback_anchor -> builder。
    返回 (blueprint, failure_reason)：
    - 成功：(blueprint_dict, "")
    - 失败：(None, "anchor_unresolved" | "builder_not_implemented" | "builder_returned_none")

    失败原因遵循指南 §12，让上层能精确区分断点。
    """
    anchor = _resolve_route_anchor(violation, route)
    if not anchor and route.requires_anchor:
        anchor = _resolve_route_fallback_anchor(violation, route)
    if not anchor and route.requires_anchor:
        return None, "anchor_unresolved"

    # ``requires_llm`` is an execution boundary, not a documentation hint.
    # These routes need new narrative prose whose correctness can only be
    # established by the formal recheck.  Calling their deterministic builder
    # used to turn diagnostic fields such as ``expected_behavior`` into literal
    # novel text (and encouraged genre-specific template sentences).  Emit the
    # existing creative-repair command instead; ChapterRepairExecutor routes it
    # through SceneRepairer and the normal protection/metric gates.
    if route.requires_llm:
        metric = str(getattr(route, "metric", "") or _metric_name_from_violation(violation))
        family = str(getattr(route, "family", "") or "")
        # 事实修复不能凭候选文本里的“相似句子”猜测落点。没有
        # Validator 提供的局部 target/evidence 时必须 fail-closed，否则整景
        # LLM 会把未定位的事实冲突伪装成可执行蓝图。这里按修复族
        # 约束，不依赖任何具体指标、题材或章节内容。
        if family == "fact" and not _has_reliable_fact_evidence(violation):
            return None, "fact_evidence_unresolved"
        return {
            "operation": "llm_creative_rewrite",
            "target_span": anchor,
            "anchor_text": anchor,
            "scope": "target_scene",
            "repair_family": family,
            "source": source,
            "source_issue_ids": [issue_id],
            "rationale": f"Creative narrative repair required for {metric}.",
            "violation_hint": {
                "metric": metric,
                "family": family,
                "detail": violation.get("detail", ""),
                "expected_behavior": violation.get("expected_behavior", ""),
                "suggested_strategy": violation.get("suggested_strategy", ""),
            },
            "guards": {
                "preserve_facts": True,
                "preserve_pov": True,
                "forbid_unrelated_edits": True,
            },
        }, ""
    builder = _ROUTE_BUILDERS.get(route.builder)
    if builder is None:
        return None, "builder_not_implemented"
    blueprint = builder(
        violation=violation,
        route=route,
        anchor=anchor,
        issue_id=issue_id,
        source=source,
    )
    if blueprint is None:
        return None, "builder_returned_none"
    return blueprint, ""


def _metric_name_from_violation(violation: dict[str, Any]) -> str:
    return str(
        violation.get("metric")
        or violation.get("type")
        or violation.get("violation_type")
        or ""
    ).lower()


def _localized_target_span(violation: dict[str, Any]) -> str:
    metric = str(violation.get("metric") or violation.get("type") or violation.get("violation_type") or "").lower()
    if metric in {"high_advisories", "medium_advisories"}:
        evidence_span = _first_evidence_span(violation)
        if evidence_span:
            return evidence_span
    target = str(violation.get("target_span") or "").strip()
    if target and str(violation.get("localization_status") or "") == "localized":
        return target
    evidence = violation.get("evidence") if isinstance(violation.get("evidence"), dict) else {}
    spans = violation.get("evidence_spans") or evidence.get("evidence_spans") or []
    if isinstance(spans, list):
        for item in spans:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or item.get("span") or "").strip()
            if text:
                return text
    if target and not _looks_like_diagnostic_sentence(target):
        return target
    return ""


def _first_evidence_span(violation: dict[str, Any], *, allow_generated: bool = True) -> str:
    evidence = violation.get("evidence") if isinstance(violation.get("evidence"), dict) else {}
    # Preserve upstream evidence priority.  The locator may later add a
    # generated top-level whole-scene window; using ``top_level or nested``
    # hid the original exact evidence and incorrectly forced manual routing.
    span_sources = [
        evidence.get("evidence_spans") or [],
        violation.get("evidence_spans") or [],
    ]
    for spans in span_sources:
        if not isinstance(spans, list):
            continue
        for item in spans:
            if isinstance(item, dict):
                if not allow_generated and str(item.get("role") or "") in {"ai_flavor_window", "semantic_anchor"}:
                    continue
                text = str(item.get("text") or item.get("span") or "").strip()
            else:
                if not allow_generated:
                    continue
                text = str(item or "").strip()
            if text:
                return text
    return ""


def _has_explicit_local_evidence(violation: dict[str, Any]) -> bool:
    return bool(str(violation.get("target_span") or "").strip() or _first_evidence_span(violation))


def _has_reliable_fact_evidence(violation: dict[str, Any]) -> bool:
    """Return whether a fact repair has an evidence-backed writable location.

    A generic paragraph/context fallback is useful for showing a reviewer where
    to look, but it cannot establish which assertion is false.  Fact-writing is
    therefore allowed only for an upstream location or a high-confidence fact
    locator result; otherwise the blueprint remains incomplete.
    """
    if violation.get("_location_supplied_upstream"):
        return bool(_localized_target_span(violation) or _first_evidence_span(violation, allow_generated=False))

    evidence = violation.get("evidence") if isinstance(violation.get("evidence"), dict) else {}
    spans = violation.get("evidence_spans") or evidence.get("evidence_spans") or []
    if not isinstance(spans, list):
        return False
    for item in spans:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        text = str(item.get("text") or item.get("span") or "").strip()
        if text and role not in {"fact_context_window", "paragraph_context", "scene_context"}:
            return True

    # A localized target without a typed evidence role is usable only when the
    # locator explicitly reports high confidence.  Low-confidence paragraph
    # fallbacks must remain review context rather than writable fact evidence.
    try:
        confidence = float(violation.get("location_confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    if confidence >= 0.75:
        return bool(_localized_target_span(violation))
    return False


def _looks_like_diagnostic_sentence(value: str) -> bool:
    text = str(value or "")
    lowered = text.lower()
    return (
        len(text) > 80
        or "text claim" in lowered
        or "conflicts with established fact" in lowered
        or "suggested correction" in lowered
        or "\u6b63\u6587" in text and ("\u51b2\u7a81" in text or "\u5408\u540c" in text)
    )


def _suggested_repair_text(violation: dict[str, Any], blob: str) -> str:
    evidence = violation.get("evidence") if isinstance(violation.get("evidence"), dict) else {}
    repair_goal = violation.get("repair_goal") if isinstance(violation.get("repair_goal"), dict) else {}
    for source in (violation, evidence, repair_goal):
        for key in (
            "replacement",
            "expected_text",
            "suggested_correction",
            "authority_claim",
            "authority_fact",
            "expected_behavior",
        ):
            value = str(source.get(key) or "").strip() if isinstance(source, dict) else ""
            if value and not _looks_like_instruction(value) and not _looks_like_quality_contract_statement(value):
                return _clean_suggested_text(value)

    patterns = [
        r"(?:suggested correction|建议修正|建议修改|应改为|应调整为)\s*[:：]?\s*['\"“‘]?([^'\"”’。\n;；]{2,120})['\"”’]?",
        r"(?:replace|change|correct)\s+['\"“‘]?[^'\"”’]{1,80}['\"”’]?\s+(?:with|to|as)\s+['\"“‘]?([^'\"”’.\n;]{2,120})['\"”’]?",
        r"(?:将|把)\s*['\"“‘]?[^'\"”’，。；\n]{1,80}['\"”’]?\s*(?:改为|替换为|改成)\s*['\"“‘]?([^'\"”’，。；\n]{2,120})['\"”’]?",
    ]
    for pattern in patterns:
        match = re.search(pattern, blob or "", re.IGNORECASE)
        if match:
            return _clean_suggested_text(match.group(1))
    return ""


def _clean_suggested_text(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip(" '\"“”‘’`，。；;：:")
    replacement_match = re.search(
        r"(?:delete\s+or\s+)?(?:replace|change|correct)\s+(?:with|to|as)\s+['\"]([^'\"]{2,80})['\"]",
        text,
        re.IGNORECASE,
    )
    if replacement_match:
        text = replacement_match.group(1).strip()
    if len(text) > 120:
        text = text[:120]
    return text


def _replacement_for_target_span(target_span: str, suggested: str) -> str:
    target = str(target_span or "").strip()
    replacement = str(suggested or "").strip()
    if not target or not replacement:
        return replacement
    if replacement in target:
        return replacement
    if not re.search(r"[A-Za-z]", target + replacement):
        return replacement

    target_words = target.split()
    replacement_words = replacement.split()
    if len(target_words) < 3 or len(replacement_words) < 2:
        return replacement
    target_norm = [re.sub(r"^\W+|\W+$", "", word).lower() for word in target_words]
    replacement_norm = [re.sub(r"^\W+|\W+$", "", word).lower() for word in replacement_words]
    max_overlap = min(len(target_words), len(replacement_words))
    for size in range(max_overlap, 0, -1):
        suffix = replacement_norm[-size:]
        if not all(suffix):
            continue
        for start in range(0, len(target_norm) - size + 1):
            if target_norm[start:start + size] != suffix:
                continue
            if start == 0:
                return replacement
            tail = target_words[start + size:]
            return " ".join([*replacement_words, *tail]).strip()
    return replacement


def _looks_like_instruction(value: str) -> bool:
    text = str(value or "")
    lowered = text.lower()
    return (
        len(text) > 120
        or "add " in lowered
        or "insert " in lowered
        or "modify " in lowered
        or "replace " in lowered
        or "delete " in lowered
        or "adjust " in lowered
        or "\u589e\u52a0" in text
        or "\u4fee\u6539" in text
        or "\u8c03\u6574" in text
        or "\u89e3\u91ca" in text
    )


def _looks_like_quality_contract_statement(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    lowered = text.lower()
    contract_tokens = [
        "conflict density",
        "curiosity engine",
        "micro payoff",
        "reader question",
        "pressure ramp",
        "skill contract",
        "quality contract",
        "required effect",
        "target behavior",
        "repair intent",
        "abstract",
        "exceeds",
        "should",
        "\u51b2\u7a81\u5bc6\u5ea6",
        "\u60ac\u5ff5",
        "\u5fae\u5151\u73b0",
        "\u8bfb\u8005",
        "\u538b\u529b",
        "\u963b\u788d",
        "\u73b0\u573a\u53d8\u5316",
        "\u4eba\u7269\u4e92\u52a8",
        "\u65f6\u95f4\u9650\u5236",
        "\u8d44\u6e90\u4ee3\u4ef7",
        "\u5e94\u8be5",
        "\u5e94\u5f53",
        "\u9700\u8981",
        "\u4fee\u8ba2\u76ee\u6807",
        "\u4fee\u590d\u76ee\u6807",
        "\u5408\u540c",
        "\u6307\u6807",
    ]
    if any(token in lowered or token in text for token in contract_tokens):
        return True
    return len(text) > 80 and any(token in text for token in ["\u589e\u5f3a", "\u51cf\u5c11", "\u4fdd\u7559", "\u7ed9\u51fa", "\u8ba9"])


def _looks_like_time_issue(violation: dict[str, Any]) -> bool:
    values = " ".join(
        str(item or "").lower()
        for item in [
            violation.get("type"),
            violation.get("violation_type"),
            violation.get("metric"),
            violation.get("detail"),
            violation.get("reason"),
            violation.get("target_span"),
        ]
    )
    return any(token in values for token in [
        "time",
        "temporal",
        "timeline",
        "\u65f6\u95f4",
        "\u5929\u4eae",
        "\u9ece\u660e",
        "\u591c\u534a",
    ])


def _looks_like_metric_value(left: str, right: str) -> bool:
    value_re = re.compile(r"^\s*(?:true|false|none|null|[\d.]+|<=?\s*[\d.]+|>=?\s*[\d.]+)\s*$", re.I)
    return bool(value_re.match(str(left or "")) or value_re.match(str(right or "")))
