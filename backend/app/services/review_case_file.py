from __future__ import annotations

import hashlib
import re
from typing import Any

from app.models.chapter_review import SceneReviewPacket
from app.models.review_case_file import (
    ReviewCaseFile,
    ReviewCaseIssue,
    compute_review_case_id,
)
from app.services.metric_registry import determine_blocks_commit, is_hard_blocking
from app.services.review_issue_semantics import normalize_violation_semantics


_ANTI_AI_TYPES = {
    "ai_punctuation_artifact",
    "explanatory_punctuation_artifact",
    "dash_density",
    "dash_per_1000",
    "tier1_hit_count",
    "sentence_shell_count",
    "structure_word_cluster_count",
    "paragraph_shape_repeat_count",
    "mirrored_paragraph_opening_count",
    "uniform_sentence_streak_max",
}

_RHYTHM_TYPES = {
    "abrupt_shift_count",
    "uniform_sentence_streak_max",
    "dense_paragraph_streak_max",
    "breathing_paragraph_ratio",
    "flat_pressure_ramp",
    "low_conflict_density",
    "weak_curiosity_engine",
    "missing_micro_payoff",
    "weak_opening_hook",
    "low_event_density",
    "low_reversal_density",
    "weak_chapter_end_hook",
    "pacing",
    "rhythm",
}

_POV_TYPES = {
    "pov",
    "pov_consistency",
    "single_pov_per_scene",
    "head_hopping",
    "head_hopping_count",
    "voice_fingerprint",
    "character_voice",
}

_POV_BOUNDARY_TYPES = {
    "pov",
    "pov_consistency",
    "single_pov_per_scene",
    "head_hopping",
    "head_hopping_count",
}

_STRUCTURE_TYPES = {
    "scene_structure",
    "goal_present",
    "conflict_present",
    "value_change",
    "missing_must_show",
    "ending_state_not_reached",
}

_REVIEW_BUCKETS = ("scene_issues", "chapter_issues", "skill_failures", "quality_advisories")

_BUCKET_PRIORITY = {
    "skill_failures": 0,
    "scene_issues": 1,
    "chapter_issues": 2,
    "quality_advisories": 3,
}

_ANTI_AI_PUNCTUATION_TYPES = {
    "ai_punctuation_artifact",
    "explanatory_punctuation_artifact",
    "dash_density",
    "dash_per_1000",
}

_ANTI_AI_PHRASE_TYPES = {
    "tier1_hit_count",
    "sentence_shell_count",
    "structure_word_cluster_count",
}

_ANTI_AI_DISCOURSE_TYPES = {
    "paragraph_shape_repeat_count",
    "mirrored_paragraph_opening_count",
    "uniform_sentence_streak_max",
}

_FACT_TYPES = {
    "fact_conflict",
    "identity_conflict",
    "setting_conflict",
    "spatial_conflict",
    "ownership_conflict",
    "knowledge_boundary_violation",
    "timeline_conflict",
    "temporal_conflict",
    "clue_provenance_error",
    "unprovenanced_clue",
}


class ReviewCaseFileBuilder:
    """Build the review-only dossier consumed by FBI intake."""

    def build(
        self,
        *,
        project_id: str,
        chapter_number: int,
        draft_text: str = "",
        scene_packets: list[SceneReviewPacket] | None = None,
        skill_validation: dict | None = None,
        final_gate_result: dict | None = None,
        protection_obligations: list[dict] | None = None,
        created_from: str = "initial_review",
        review_trace: dict | None = None,
    ) -> ReviewCaseFile:
        draft_hash = _hash_text(draft_text)
        case_file = ReviewCaseFile(
            project_id=project_id,
            chapter_number=chapter_number,
            draft_hash=draft_hash,
            created_from=created_from,
            protection_obligations=protection_obligations or [],
            review_trace=review_trace or {},
        )
        case_file.case_id = compute_review_case_id(
            project_id=project_id,
            chapter_number=chapter_number,
            draft_hash=draft_hash,
            created_from=created_from,
        )

        for packet in scene_packets or []:
            if packet is None or packet.unavailable:
                continue
            case_file.scene_issues.extend(
                self._issues_from_violations(
                    packet.blocking_violations,
                    source="scene_review",
                    scene_index=packet.scene_index,
                    # P1-14：统一 blocks_commit 语义，传 None 触发 determine_blocks_commit(metric, severity)
                    default_blocks_commit=None,
                )
            )
            case_file.quality_advisories.extend(
                self._issues_from_violations(
                    packet.advisory_violations,
                    source="scene_review",
                    scene_index=packet.scene_index,
                    # advisory_violations 保持"不阻断"语义，不走 determine_blocks_commit
                    default_blocks_commit=False,
                )
            )

        if skill_validation:
            case_file.skill_failures.extend(
                self.issues_from_skill_validation(
                    skill_validation,
                    created_from="skill_validation",
                )
            )

        if final_gate_result and not final_gate_result.get("allowed", True):
            case_file.skill_failures.extend(
                self.issues_from_final_gate(final_gate_result)
            )

        self._dedupe(case_file)
        return case_file

    def issues_from_skill_validation(
        self,
        validation: dict,
        *,
        created_from: str = "skill_validation",
    ) -> list[ReviewCaseIssue]:
        issues: list[ReviewCaseIssue] = []
        for failure in validation.get("failures") or []:
            if not isinstance(failure, dict):
                continue
            validator = str(failure.get("validator") or failure.get("source_validator") or "")
            skill_id = str(failure.get("skill_id") or failure.get("skill") or "")
            scene_index = failure.get("scene_index")
            metric = str(failure.get("metric") or failure.get("type") or validator or "skill_failure")
            # Explicit Skill contracts still obey the product enforcement
            # grade: dash/correctness contracts are hard, while subjective
            # findings remain best-effort advisory repair work.
            default_blocks_commit = (
                not bool(failure.get("advisory_only"))
                and is_hard_blocking(metric)
            )
            issue = self._issue_from_mapping(
                failure,
                source=created_from,
                scene_index=scene_index if isinstance(scene_index, int) else None,
                default_blocks_commit=default_blocks_commit,
            )
            issue.source = "skill_validator" if created_from == "skill_validation" else created_from
            issue.sources = [issue.source]
            issue.source_validator = validator
            issue.skill_id = skill_id
            metric = str(failure.get("metric") or failure.get("type") or issue.type or validator or "skill_failure")
            issue.metric = metric
            issue.type = metric
            issue.detail = issue.detail or _failure_detail(failure)
            issue.repair_domain = _repair_domain_for(metric, validator=validator, skill_id=skill_id)
            issue.repairability = _repairability_for(metric, issue.repair_domain)
            if _localizable_high_advisory(failure):
                issue.repair_domain = "anti_ai"
                issue.repairability = "local_patch"
            issue.dedupe_key = _dedupe_key(issue)
            issue.issue_id = _issue_id(issue)
            issues.append(issue)
        return issues

    def issues_from_final_gate(self, final_gate_result: dict) -> list[ReviewCaseIssue]:
        trace = final_gate_result.get("trace") or {}
        validation = (
            trace.get("repair", {}).get("validation")
            or trace.get("initial_validation")
            or {}
        )
        issues = self.issues_from_skill_validation(
            validation,
            created_from="final_acceptance",
        )
        # 通用修复（循环 #13）：纳入 unified_violations
        # 根因：FBIFinalAcceptanceService.evaluate() 通过 validator_collection 检测的违规
        #   （如 ending_state_not_reached、scene_validator 的结构问题）存储在
        #   final_gate_result["unified_violations"] 中，但 issues_from_final_gate()
        #   只从 trace.repair.validation.failures / trace.initial_validation.failures
        #   提取 issues。skill_gate.trace 只包含 skill 验证结果，不含
        #   validator_collection 的额外检测，导致 unified_violations 中的 blocking
        #   违规未进入 case_file_delta，终验 delta 修复循环无法修复它们。
        #   只有在后续轮次的 recheck 中偶然检测到（如果 recheck 恰好检查了该场景），
        #   但 max_rounds 可能已耗尽，形成阻断死循环。
        # 修复：将 unified_violations 也作为 failures 传入 issues_from_skill_validation，
        #   _dedupe() 会自动去重（基于 stable_key），不会产生重复 issue。
        # 通用性：所有题材的终验检测都通过 validator_collection 统一收集，
        #   unified_violations 中的 blocking 违规都应进入 case_file_delta 被修复。
        unified_violations = final_gate_result.get("unified_violations") or []
        if unified_violations:
            issues.extend(
                self.issues_from_skill_validation(
                    {"failures": unified_violations},
                    created_from="final_acceptance",
                )
            )
        if not issues:
            issues.append(
                ReviewCaseIssue(
                    source="final_acceptance",
                    scope="chapter",
                    type="final_gate_blocked",
                    metric="final_gate_blocked",
                    severity="high",
                    blocks_commit=True,
                    repairability="manual_review",
                    repair_domain="manual",
                    detail=str(final_gate_result.get("reason") or "final gate blocked"),
                )
            )
        return issues

    def _issues_from_violations(
        self,
        violations: list[dict],
        *,
        source: str,
        scene_index: int | None,
        default_blocks_commit: bool | None,
    ) -> list[ReviewCaseIssue]:
        issues: list[ReviewCaseIssue] = []
        for violation in violations or []:
            if not isinstance(violation, dict):
                continue
            issue = self._issue_from_mapping(
                normalize_violation_semantics(violation),
                source=source,
                scene_index=scene_index,
                default_blocks_commit=default_blocks_commit,
            )
            issues.append(issue)
        return issues

    @staticmethod
    def _issue_from_mapping(
        item: dict,
        *,
        source: str,
        scene_index: int | None,
        default_blocks_commit: bool | None,
    ) -> ReviewCaseIssue:
        item = normalize_violation_semantics(item)
        issue_type = str(
            item.get("violation_type")
            or item.get("type")
            or item.get("metric")
            or item.get("validator")
            or "unknown"
        )
        validator = str(item.get("validator") or item.get("source_validator") or "")
        skill_id = str(item.get("skill_id") or item.get("skill") or "")
        detail = str(item.get("detail") or item.get("reason") or _failure_detail(item))
        target_span = item.get("target_span") or item.get("span") or ""
        if isinstance(target_span, dict):
            target_span = f"{target_span.get('start', '')}:{target_span.get('end', '')}"
        severity = _severity_for(str(item.get("severity") or ""), default_blocks_commit)
        metric_value = str(item.get("metric") or issue_type)
        # 方案 7 Part C：统一 blocks_commit 语义。
        # 优先级：item 显式提供 > default_blocks_commit 回退 > determine_blocks_commit(metric, severity)
        if "blocks_commit" in item and item.get("blocks_commit") is not None:
            blocks_commit = bool(item.get("blocks_commit"))
        elif default_blocks_commit is not None:
            blocks_commit = default_blocks_commit
        else:
            blocks_commit = determine_blocks_commit(metric_value, severity)
        repair_domain = _repair_domain_for(issue_type, validator=validator, skill_id=skill_id)
        repairability = _repairability_for(issue_type, repair_domain)
        normalized_repairability = str(item.get("repairability") or "")
        normalized_classification = str(item.get("issue_classification") or item.get("classification") or "")
        localizable_high_advisory = _localizable_high_advisory(item)
        if localizable_high_advisory:
            repairability = "local_patch"
            repair_domain = "anti_ai"
        elif normalized_repairability == "human_review_required" or normalized_classification == "manual_only":
            repairability = "manual_review"
            repair_domain = "manual" if repair_domain == "surface" else repair_domain
        elif normalized_repairability in {"deterministic", "local_patch", "scene_rewrite", "manual_review"}:
            repairability = normalized_repairability
        issue = ReviewCaseIssue(
            source=source,
            source_validator=validator,
            skill_id=skill_id,
            scene_index=scene_index,
            scope=_scope_for(item, scene_index),
            type=issue_type,
            metric=metric_value,
            severity=severity,
            blocks_commit=blocks_commit,
            repairability=repairability,
            repair_domain=repair_domain,
            detail=detail,
            target_span=str(target_span or ""),
            expected_behavior=str(item.get("expected_behavior") or item.get("repair_goal") or ""),
            evidence=_issue_evidence_from_mapping(item),
        )
        issue.sources = [source]
        issue.dedupe_key = _dedupe_key(issue)
        issue.issue_id = str(item.get("issue_id") or item.get("violation_id") or "") or _issue_id(issue)
        return issue

    @staticmethod
    def _dedupe(case_file: ReviewCaseFile) -> None:
        for attr in _REVIEW_BUCKETS:
            values: list[ReviewCaseIssue] = getattr(case_file, attr)
            merged: dict[str, ReviewCaseIssue] = {}
            for issue in values:
                key = issue.stable_key()
                if key not in merged:
                    merged[key] = issue
                    continue
                _merge_issue(merged[key], issue)
            setattr(case_file, attr, list(merged.values()))
        _dedupe_repair_intents(case_file)


def combine_chapter_review_validations(chapter_review_outputs: dict[str, dict] | None) -> dict | None:
    if not chapter_review_outputs:
        return None
    failures: list[dict] = []
    requested: list[str] = []
    validators: dict[str, Any] = {}
    passed = True
    for node_type, output in sorted(chapter_review_outputs.items()):
        if not isinstance(output, dict):
            continue
        validation = output.get("validation") or {}
        if not isinstance(validation, dict):
            continue
        node_failures = [
            dict(item)
            for item in validation.get("failures") or []
            if isinstance(item, dict)
        ]
        for failure in node_failures:
            failure.setdefault("validator", failure.get("source_validator") or node_type)
            failure.setdefault("source_validator", failure.get("validator") or node_type)
            failure.setdefault("chapter_review_node", node_type)
            failure.setdefault("review_source", node_type)
        failures.extend(node_failures)
        requested.extend(str(item) for item in validation.get("requested") or [] if item)
        validators[node_type] = {
            "passed": bool(validation.get("passed", not node_failures)),
            "failures": len(node_failures),
            "unavailable": bool(output.get("unavailable")),
            "source_validation": output.get("source_validation", ""),
        }
        if node_failures or validation.get("passed") is False:
            passed = False
    return {
        "passed": passed and not failures,
        "failures": failures,
        "requested": sorted(set(requested)),
        "validators": validators,
        "source": "chapter_review_collection",
    }


def case_file_issues_to_violations(case_file: ReviewCaseFile | dict | None) -> list[dict]:
    if not case_file:
        return []
    if isinstance(case_file, dict):
        case_file = ReviewCaseFile.model_validate(case_file)
    violations: list[dict] = []
    for issue in case_file.all_issues():
        if not issue.blocks_commit:
            continue
        violations.append({
            "issue_id": issue.issue_id,
            "violation_id": issue.issue_id,
            "type": issue.type,
            "violation_type": issue.type,
            "metric": issue.metric,
            "severity": issue.severity,
            "scope": issue.scope,
            "blocks_commit": issue.blocks_commit,
            "detail": issue.detail,
            "target_span": issue.target_span,
            "expected_behavior": issue.expected_behavior,
            "evidence": dict(issue.evidence or {}),
            "evidence_spans": list((issue.evidence or {}).get("evidence_spans") or []),
            "actual": (issue.evidence or {}).get("actual"),
            "expected": (issue.evidence or {}).get("expected"),
            "expected_min": (issue.evidence or {}).get("expected_min"),
            "expected_max": (issue.evidence or {}).get("expected_max"),
            "action": (issue.evidence or {}).get("action"),
            "failure_signature": (issue.evidence or {}).get("failure_signature"),
            "repair_granularity": (issue.evidence or {}).get("repair_granularity"),
            "localization_status": (issue.evidence or {}).get("localization_status"),
            "location_confidence": (issue.evidence or {}).get("location_confidence"),
            "source": issue.source,
            "validator": issue.source_validator,
            "skill_id": issue.skill_id,
            "source_scene": issue.scene_index,
            "source_scenes": [issue.scene_index] if issue.scene_index is not None else [],
            "repair_domain": issue.repair_domain,
            "repairability": _legacy_repairability(issue.repairability),
            "repairable_by_text": issue.repairability != "manual_review",
            "issue_classification": "manual_only" if issue.repairability == "manual_review" else "text_repairable",
            "classification": "manual_only" if issue.repairability == "manual_review" else "text_repairable",
            "suggested_strategy": (
                str((issue.evidence or {}).get("suggested_strategy") or "").lower()
                or _suggested_strategy(issue)
            ),
            "review_case_issue": issue.model_dump(),
        })
    return violations


def final_gate_case_delta(
    *,
    project_id: str,
    chapter_number: int,
    final_text: str,
    final_gate_result: dict,
) -> dict:
    case_file = ReviewCaseFileBuilder().build(
        project_id=project_id,
        chapter_number=chapter_number,
        draft_text=final_text,
        final_gate_result=final_gate_result,
        created_from="final_acceptance",
    )
    return case_file.model_dump()


def _hash_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def _issue_id(issue: ReviewCaseIssue) -> str:
    return "iss_" + hashlib.md5(issue.stable_key().encode("utf-8")).hexdigest()[:12]


def _issue_evidence_from_mapping(item: dict) -> dict:
    evidence = dict(item.get("evidence") or {}) if isinstance(item.get("evidence"), dict) else {}
    for key in (
        "actual",
        "expected",
        "expected_min",
        "expected_max",
        "action",
        "retry_policy",
        "repair_granularity",
        "localization_status",
        "location_confidence",
        "failure_signature",
        "acceptance_round",
        "origin_step",
        "parent_case_id",
    ):
        if key in item and item.get(key) is not None:
            evidence[key] = item.get(key)
    evidence_spans = item.get("evidence_spans")
    if isinstance(evidence_spans, list):
        evidence["evidence_spans"] = [
            dict(entry) for entry in evidence_spans if isinstance(entry, dict)
        ][:5]
    return evidence


def _dedupe_key(issue: ReviewCaseIssue) -> str:
    scene = "" if issue.scene_index is None else str(issue.scene_index)
    return f"{scene}:{issue.repair_domain}:{issue.type}:{issue.metric}:{issue.target_span}"


def _severity_for(raw: str, default_blocks_commit: bool | None) -> str:
    raw = raw.lower()
    if raw in {"blocking", "critical"}:
        return "critical"
    if raw in {"major", "high"}:
        return "high"
    if raw in {"minor", "medium"}:
        return "medium"
    if raw in {"low", "info"}:
        return "low"
    return "high" if default_blocks_commit else "medium"


def _max_severity(left: str, right: str) -> str:
    rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    return left if rank.get(left, 9) <= rank.get(right, 9) else right


def _severity_rank(value: str) -> int:
    return {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(value, 9)


def _merge_issue(existing: ReviewCaseIssue, incoming: ReviewCaseIssue) -> ReviewCaseIssue:
    existing.sources = sorted(set([*existing.sources, existing.source, incoming.source, *incoming.sources]))
    existing.blocks_commit = existing.blocks_commit or incoming.blocks_commit
    existing.severity = _max_severity(existing.severity, incoming.severity)
    if not existing.source_validator and incoming.source_validator:
        existing.source_validator = incoming.source_validator
    if not existing.skill_id and incoming.skill_id:
        existing.skill_id = incoming.skill_id
    if not existing.metric and incoming.metric:
        existing.metric = incoming.metric
    if incoming.detail and incoming.detail not in existing.detail:
        existing.detail = f"{existing.detail}; {incoming.detail}" if existing.detail else incoming.detail
    if incoming.expected_behavior and incoming.expected_behavior not in existing.expected_behavior:
        existing.expected_behavior = (
            f"{existing.expected_behavior}; {incoming.expected_behavior}"
            if existing.expected_behavior
            else incoming.expected_behavior
        )
    if not existing.target_span and incoming.target_span:
        existing.target_span = incoming.target_span
    if incoming.evidence:
        merged_evidence = {**incoming.evidence, **existing.evidence}
        incoming_spans = incoming.evidence.get("evidence_spans") or []
        existing_spans = existing.evidence.get("evidence_spans") or []
        if incoming_spans or existing_spans:
            spans: list[dict] = []
            seen: set[str] = set()
            for span in [*existing_spans, *incoming_spans]:
                if not isinstance(span, dict):
                    continue
                key = str(span.get("span") or span.get("text") or span)
                if key in seen:
                    continue
                seen.add(key)
                spans.append(span)
            merged_evidence["evidence_spans"] = spans[:5]
        existing.evidence = merged_evidence
    return existing


def _dedupe_repair_intents(case_file: ReviewCaseFile) -> None:
    chosen: dict[str, tuple[str, ReviewCaseIssue]] = {}
    for attr in _REVIEW_BUCKETS:
        for issue in getattr(case_file, attr):
            key = _repair_intent_key(issue)
            issue.dedupe_key = key
            issue.issue_id = _issue_id(issue)
            if key not in chosen:
                chosen[key] = (attr, issue)
                continue
            existing_attr, existing = chosen[key]
            if _issue_preferred(issue, attr, existing, existing_attr):
                _merge_issue(issue, existing)
                issue.issue_id = _issue_id(issue)
                chosen[key] = (attr, issue)
            else:
                _merge_issue(existing, issue)
                existing.issue_id = _issue_id(existing)

    for attr in _REVIEW_BUCKETS:
        setattr(case_file, attr, [])
    for attr, issue in chosen.values():
        getattr(case_file, attr).append(issue)


def _issue_preferred(
    candidate: ReviewCaseIssue,
    candidate_bucket: str,
    existing: ReviewCaseIssue,
    existing_bucket: str,
) -> bool:
    if candidate.blocks_commit != existing.blocks_commit:
        return candidate.blocks_commit
    if _severity_rank(candidate.severity) != _severity_rank(existing.severity):
        return _severity_rank(candidate.severity) < _severity_rank(existing.severity)
    return _BUCKET_PRIORITY.get(candidate_bucket, 99) < _BUCKET_PRIORITY.get(existing_bucket, 99)


def _repair_intent_key(issue: ReviewCaseIssue) -> str:
    location = "chapter" if issue.scene_index is None else f"scene:{issue.scene_index}"
    issue_type = str(issue.type or "").lower()
    metric = str(issue.metric or "").lower()
    values = {
        issue_type,
        metric,
        str(issue.source_validator or "").lower(),
        str(issue.skill_id or "").lower(),
    }
    if issue.repair_domain == "anti_ai":
        if values & _ANTI_AI_PUNCTUATION_TYPES:
            return f"{location}:anti_ai:punctuation_density"
        if values & _ANTI_AI_PHRASE_TYPES:
            return f"{location}:anti_ai:phrase_density"
        if values & _ANTI_AI_DISCOURSE_TYPES:
            return f"{location}:anti_ai:discourse_shape"
    pov_boundary_values = {metric} if metric else {issue_type}
    if issue.repair_domain == "pov" and pov_boundary_values & _POV_BOUNDARY_TYPES:
        return f"{location}:pov:boundary"
    if issue.repair_domain == "fact" and issue_type == "fact_conflict":
        fact_key = _fact_conflict_intent_key(issue, location)
        if fact_key:
            return fact_key
    return issue.stable_key()


_FACT_AUTHORITY_INTENT_RE = re.compile(
    r"(?:established fact|authority fact|事实层|已确立事实|既定事实|合同|scene_provenance)"
    r"[^'\"“”‘’：:]{0,40}[：:]?\s*['\"“”‘’]?([^'\"“”‘’。；;\n]{4,220})['\"“”‘’]?",
    re.IGNORECASE,
)


def _fact_conflict_intent_key(issue: ReviewCaseIssue, location: str) -> str:
    evidence = issue.evidence if isinstance(issue.evidence, dict) else {}
    parts = [
        issue.detail,
        issue.expected_behavior,
        issue.target_span,
        str(evidence.get("authority_fact") or ""),
        str(evidence.get("established_fact") or ""),
        str(evidence.get("expected_behavior") or ""),
    ]
    blob = "\n".join(str(item or "") for item in parts if item)
    authority = ""
    match = _FACT_AUTHORITY_INTENT_RE.search(blob)
    if match:
        authority = match.group(1).strip()
    if not authority and issue.expected_behavior:
        authority = issue.expected_behavior.strip()
    if not authority:
        return ""
    normalized = re.sub(r"\s+", "", authority.lower())
    normalized = re.sub(r"[，,。；;：:'\"“”‘’（）()\[\]【】]", "", normalized)
    if not normalized:
        return ""
    return f"{location}:fact:fact_conflict:{hashlib.md5(normalized.encode('utf-8')).hexdigest()[:12]}"


def _scope_for(item: dict, scene_index: int | None) -> str:
    raw = str(item.get("scope") or item.get("repair_scope") or "")
    if raw in {"chapter", "span", "marker", "contract"}:
        return raw
    if raw in {"scene_contract", "chapter_contract"}:
        return "contract"
    return "scene" if scene_index is not None else "chapter"


def _repair_domain_for(issue_type: str, *, validator: str = "", skill_id: str = "") -> str:
    values = {issue_type.lower(), validator.lower(), skill_id.lower()}
    if values & _FACT_TYPES:
        return "fact"
    if values & _ANTI_AI_TYPES or "ai_flavor" in values or "anti_ai" in " ".join(values):
        return "anti_ai"
    if values & _RHYTHM_TYPES:
        return "rhythm"
    if values & _POV_TYPES:
        return "pov"
    if values & _STRUCTURE_TYPES:
        return "structure"
    if "contract" in " ".join(values):
        return "contract"
    if "specific" in " ".join(values) or "detail" in " ".join(values):
        return "specificity"
    return "surface"


def _repairability_for(issue_type: str, repair_domain: str) -> str:
    lowered = issue_type.lower()
    if lowered in {"high_advisories", "medium_advisories"}:
        return "manual_review"
    if lowered in {"dash_per_1000", "dash_density", "tier1_hit_count", "ai_punctuation_artifact"}:
        return "deterministic"
    if repair_domain in {"fact", "contract"}:
        return "local_patch"
    if repair_domain in {"structure", "pov", "rhythm"}:
        return "scene_rewrite"
    if repair_domain == "manual":
        return "manual_review"
    return "local_patch"


def _localizable_high_advisory(item: dict[str, Any]) -> bool:
    metric = str(item.get("metric") or item.get("type") or item.get("violation_type") or "").lower()
    if metric not in {"high_advisories", "medium_advisories"}:
        return False
    if str(item.get("target_span") or item.get("span") or "").strip():
        return True
    evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
    spans = item.get("evidence_spans") or evidence.get("evidence_spans") or []
    if isinstance(spans, list):
        for span in spans:
            if isinstance(span, dict) and str(span.get("span") or span.get("text") or "").strip():
                return True
            if isinstance(span, str) and span.strip():
                return True
    return False


def _legacy_repairability(repairability: str) -> str:
    if repairability == "manual_review":
        return "human_review_required"
    return "auto_fixable"


def _suggested_strategy(issue: ReviewCaseIssue) -> str:
    if issue.repairability == "deterministic":
        return "patch_text"
    if issue.repairability == "scene_rewrite":
        return "rewrite_scene"
    if issue.repairability == "manual_review":
        return "manual_review"
    return "patch_text"


def _failure_detail(failure: dict[str, Any]) -> str:
    validator = failure.get("validator") or failure.get("source_validator") or "validator"
    metric = failure.get("metric") or failure.get("type") or "metric"
    actual = failure.get("actual", failure.get("value", ""))
    expected = failure.get("expected", failure.get("limit", ""))
    if actual != "" or expected != "":
        return f"{validator}:{metric} actual={actual} expected={expected}"
    return str(failure.get("detail") or failure.get("reason") or f"{validator}:{metric}")
