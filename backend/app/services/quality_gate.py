import asyncio
import difflib
import hashlib
import logging
import re
import time

from app.models.violation import make_violation, SCENE_STRUCTURAL_TYPES
from app.services.fact_progression_protocol import (
    apply_protocol_to_violations,
    summarize_protocol,
)
from app.services.metric_registry import MetricRegistry, is_hard_blocking

_logger = logging.getLogger(__name__)

# P3-5 修复：ReaderExperience 实验性检查的执行级超时常量（非 LLM 直调，属 agent 执行级）。
# 20s 硬上限避免 ReaderExperienceAgent 长时间阻塞主链路。
_READER_EXPERIENCE_TIMEOUT_SECONDS = 20

# 校验版一致性：带 text_hash 和 review_version 的 make_violation 封装
def _make_violation_with_hash(
    text_hash: str,
    review_version: int,
    *args,
    **kwargs,
):
    """Wrap make_violation and inject text hash/review version."""
    return make_violation(*args, text_hash=text_hash, review_version=review_version, **kwargs)

CONSISTENCY_STRATEGY_MAP: dict[str, str] = {
    "fact_conflict": "patch_text",
    "naming_conflict": "patch_text",
    "timeline_conflict": "rewrite_scene",
    "setting_conflict": "rewrite_scene",
    "pov_conflict": "rewrite_scene",
    "identity_conflict": "rewrite_scene",
    "internal_conflict": "rewrite_scene",
}

# ---------------------------------------------------------------------------
# 违规分层映射：violation.type -> 质量层名称
# ---------------------------------------------------------------------------
_HARD_CORRECTNESS_TYPES: set[str] = {
    # 命题审计违规
    "truth_layer_conflict",
    "certainty_escalation",
    "responsibility_polarity_conflict",
    "spatial_conflict",
    "temporal_conflict",
    "ownership_conflict",
    # 一致性违规
    "fact_conflict",
    "naming_conflict",
    "timeline_conflict",
    "setting_conflict",
    "pov_conflict",
    "identity_conflict",
    "internal_conflict",
    "consistency_check_unavailable",
    "proposition_layer_unavailable",
    "proposition_extractor_unavailable",
}

_NARRATIVE_CONTRACT_TYPES: set[str] = {
    # must_show / forbidden / ending_state
    "forbidden_triggered",
    "missing_must_show",
    "ending_state_not_reached",
    "forbidden_recap_violation",
    "forbidden_assertion_triggered",
    "required_ambiguity_broken",
    "premature_foreshadowing_reveal",
    # clue_provenance
    "clue_provenance_error",
    "clue_provenance_error_proposition",
    "unprovenanced_clue",
    "clue_missing_source",
    # fcip
    "fcip_registration_error",
    "fcip_text_violation",
    "fcip_check_unavailable",
    "scene_contract_compile_blocked",
    "scene_contract_compiler_unavailable",
}

_STYLE_QUALITY_TYPES: set[str] = {
    # AI flavor
    "enforced_ai_flavor",
    # concept budget
    "enforced_concept_budget",
    "must_show_overload",
    # character voice
    "enforced_character_voice",
    # info density
    "info_dump_high_density",
    "info_dump_moderate_density",
    "setting_paragraph_too_long",
    "info_reveal_burst",
    # expression variety
    "repeated_body_language",
    "emotion_expression_monotone",
    # style-contract conflict
    "possible_style_contract_conflict",
    "style_contract_conflict",
    "missing_genre_extension",
    # quality checker unavailable
    "quality_checker_unavailable",
}

_READER_EXPERIENCE_TYPES: set[str] = {
    "reader_experience_advisory",
}

_NARRATIVE_EXPERIENCE_TYPES: set[str] = {
    "low_reading_drive",
    "low_scene_pressure",
    "passive_protagonist",
    "conflict_only_explained",
    "exposition_driven_reveal",
    "missing_dialogue_pressure",
    "weak_hook_out",
}

_LITERARY_QUALITY_TYPES: set[str] = {
    "specificity_budget_unmet",
    "abstraction_over_budget",
    "emotional_claim_without_scene_evidence",
    "prose_identity_weak",
}

_MODE_FIT_TYPES: set[str] = {
    "mode_mismatch",
    "over_literary_for_mode",
    "too_plain_for_literary_mode",
}

_STYLE_EXPERIENCE_CONFLICT_TYPES: set[str] = {
    "style_experience_conflict",
}

_COMMERCIAL_PACING_TYPES: set[str] = {
    "weak_opening_hook",
    "low_event_density",
    "low_conflict_density",
    "flat_pressure_ramp",
    "weak_curiosity_engine",
    "low_reversal_density",
    "missing_micro_payoff",
    "weak_chapter_end_hook",
    "low_reader_retention",
}

_SCENE_CREDIBILITY_TYPES: set[str] = {
    "fact_boundary_conflict",
    "knowledge_boundary_violation",
    "plausibility_break",
    "memory_plausibility_break",
    "narration_explanation_artifact",
    "explanatory_punctuation_artifact",
    "scene_credibility_unavailable",
}


def _consistency_repair_strategy(conflict: dict) -> str:
    """Promote broad consistency conflicts to scene rewrites."""
    category = conflict.get("category", "fact_conflict")
    if conflict.get("scope") == "scene":
        return "rewrite_scene"
    return CONSISTENCY_STRATEGY_MAP.get(
        category,
        "rewrite_scene" if category in SCENE_STRUCTURAL_TYPES else "patch_text",
    )


def _normalize_quality_pattern_type(value) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    for suffix in ("_trend", "_pattern"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
    return text


def _normalize_match_text(value) -> str:
    return "".join(str(value or "").lower().split())


_ABSENCE_ONLY_AUTHORITY_MARKERS = (
    "未提及",
    "没有提及",
    "尚未提及",
    "未说明",
    "未记录",
    "未建立",
    "上下文未提供",
    "not mentioned",
    "no mention",
    "not established",
    "not recorded",
    "context does not provide",
)


def _is_absence_only_authority_fact(value: str) -> bool:
    """Reject closed-world claims disguised as authoritative facts.

    A compressed context is intentionally incomplete.  The absence of an item,
    alias, or state from that window is not evidence that it does not exist.
    Explicit negative facts (for example, "the item was destroyed") remain
    valid; only statements whose authority is *lack of mention* are rejected.
    """
    normalized = _normalize_match_text(value)
    return any(
        _normalize_match_text(marker) in normalized
        for marker in _ABSENCE_ONLY_AUTHORITY_MARKERS
    )


_DUPLICATE_INTERNAL_CONFLICT_MARKERS = (
    "重复",
    "两段",
    "两次",
    "再次叙述",
    "几乎完全相同",
    "同一事件被叙述",
    "duplicate",
    "duplicated",
    "repeated passage",
    "repeated twice",
    "two passages",
)
_CONSISTENCY_QUOTED_SPAN_RE = re.compile(
    r"[“‘\"']([^”’\"'\n]{2,1200})[”’\"']"
)


def _consistency_conflict_evidence_spans(
    conflict: dict,
    *,
    include_summary_quotes: bool = True,
) -> list[str]:
    """Collect verbatim evidence candidates declared by the consistency LLM."""
    raw_evidence = conflict.get("evidence_spans") or conflict.get("evidence") or []
    if isinstance(raw_evidence, (str, dict)):
        raw_evidence = [raw_evidence]
    spans: list[str] = []
    for raw in raw_evidence if isinstance(raw_evidence, list) else []:
        if isinstance(raw, dict):
            value = raw.get("span") or raw.get("text") or raw.get("quote") or ""
        else:
            value = raw
        span = str(value or "").strip()
        if span and span not in spans:
            spans.append(span)
    if include_summary_quotes and len(spans) < 2:
        for value in (conflict.get("fact"), conflict.get("text_claim")):
            for match in _CONSISTENCY_QUOTED_SPAN_RE.findall(str(value or "")):
                span = str(match or "").strip()
                if span and span not in spans:
                    spans.append(span)
    return spans


def _normalized_consistency_evidence(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", str(value or "")).lower()


_AUDITABLE_CONSISTENCY_CATEGORIES = {
    "fact_conflict",
    "timeline_conflict",
    "identity_conflict",
    "pov_conflict",
    "naming_conflict",
    "internal_conflict",
    "setting_conflict",
}


def _consistency_conflict_grounding_audit(conflict: dict, text: str) -> dict:
    """Require hard consistency findings to cite current-prose evidence."""
    category = str(conflict.get("category") or "fact_conflict")
    if category not in _AUDITABLE_CONSISTENCY_CATEGORIES:
        return {"required": False, "passed": True, "reason": "category_not_audited"}

    declared = _consistency_conflict_evidence_spans(
        conflict,
        include_summary_quotes=False,
    )
    grounded = [span for span in declared if span and span in text]
    authority_source = str(conflict.get("authority_source") or "").strip().lower()
    current_text_authority = bool(
        category == "internal_conflict"
        or authority_source in {"current_text", "generated_text", "same_scene", "chapter_text"}
    )
    required_count = 2 if current_text_authority else 1
    transition_evidence = str(conflict.get("transition_evidence") or "").strip()
    if transition_evidence and transition_evidence in text:
        return {
            "required": True,
            "passed": False,
            "reason": "explicit_transition_evidence_explains_state_change",
            "authority_source": authority_source,
            "transition_evidence": transition_evidence,
            "declared_evidence_count": len(declared),
            "grounded_evidence_count": len(grounded),
        }
    passed = len(grounded) >= required_count
    return {
        "required": True,
        "passed": passed,
        "reason": (
            "declared_evidence_grounded"
            if passed
            else "hard_conflict_lacks_declared_verbatim_evidence"
        ),
        "authority_source": authority_source,
        "required_evidence_count": required_count,
        "declared_evidence_count": len(declared),
        "grounded_evidence_count": len(grounded),
        "evidence_spans": grounded[:4],
    }


def _duplicate_internal_conflict_evidence_audit(
    conflict: dict,
    text: str,
) -> dict:
    """Require two grounded, genuinely similar passages for duplicate claims.

    An LLM summary saying that an event happened twice is not hard-gate
    evidence.  Duplicate-prose findings must identify either the same exact
    span at two offsets or two sufficiently long verbatim passages with strong
    lexical overlap.  Sequential phases that merely reuse motifs such as cold,
    blurred vision, or light therefore cannot become a blocking contradiction.
    """
    summary = " ".join(
        str(conflict.get(key) or "")
        for key in ("fact", "text_claim")
    ).lower()
    duplicate_claim = any(
        marker.lower() in summary
        for marker in _DUPLICATE_INTERNAL_CONFLICT_MARKERS
    )
    if not duplicate_claim:
        return {"required": False, "passed": True, "reason": "not_duplicate_claim"}

    spans = _consistency_conflict_evidence_spans(conflict)
    grounded = [span for span in spans if span and span in text]
    for span in grounded:
        if len(_normalized_consistency_evidence(span)) >= 6 and text.count(span) >= 2:
            return {
                "required": True,
                "passed": True,
                "reason": "same_verbatim_span_has_distinct_occurrences",
                "evidence_spans": [span, span],
            }

    best_pair: tuple[str, str] | None = None
    best_ratio = 0.0
    for index, left in enumerate(grounded):
        left_normalized = _normalized_consistency_evidence(left)
        if len(left_normalized) < 20:
            continue
        for right in grounded[index + 1:]:
            right_normalized = _normalized_consistency_evidence(right)
            if len(right_normalized) < 20:
                continue
            ratio = difflib.SequenceMatcher(
                None,
                left_normalized,
                right_normalized,
                autojunk=False,
            ).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_pair = (left, right)
    passed = bool(best_pair and best_ratio >= 0.72)
    return {
        "required": True,
        "passed": passed,
        "reason": (
            "two_grounded_similar_passages"
            if passed
            else "duplicate_claim_lacks_two_similar_verbatim_passages"
        ),
        "declared_evidence_count": len(spans),
        "grounded_evidence_count": len(grounded),
        "best_similarity": round(best_ratio, 4),
        "evidence_spans": list(best_pair or grounded[:2]),
    }


class QualityGate:
    async def evaluate(self, context: dict, level: str = "full") -> dict:
        self._protocol_context = context
        generated_text = context.get("generated_text", "")
        scene_contract = context.get("scene_contract", {})
        chapter_state = context.get("chapter_state", {})
        character_cards = context.get("character_cards", [])
        character_names = context.get("character_names", [])
        project = context.get("project")
        project_id = context.get("project_id", "")
        chapter_number = context.get("chapter_number", 0)
        scene_index = context.get("scene_index", 0)
        # Parallel DAG nodes must not share one AsyncSession. Workers receive a
        # session factory and open their own short-lived session at the DB
        # boundary (here: the FCIP read) rather than a shared live session.
        db_session_factory = context.get("db_session_factory")
        core_facts = context.get("core_facts", {})
        current_state = context.get("current_state", {})
        policy = context.get("generation_feature_policy")
        if policy is None:
            try:
                from app.services.generation_feature_policy import resolve_generation_feature_policy
                policy = resolve_generation_feature_policy(project)
            except Exception:
                policy = None

        # 审校版本一致性协议：计算当前正文 hash 和 review 版本
        _text_hash = hashlib.md5(generated_text.encode()).hexdigest()[:12] if generated_text else ""
        _review_version = int(context.get("review_version", 0) or 0)

        if not generated_text:
            return {
                "passed": False,
                "commit_blocked": True,
                "violations": [_make_violation_with_hash(_text_hash, _review_version,
                    "empty_text", "critical", "生成文本为空",
                    source="deterministic", expected_behavior="Writer should produce non-empty prose",
                )],
                "reports": {
                    "deterministic": {}, "consistency": {}, "semantic": {}, "fcip": {},
                    "hard_correctness": {}, "narrative_contract": {},
                    "style_quality": {}, "reader_experience": {},
                    "narrative_experience": {}, "literary_quality": {},
                    "mode_fit": {}, "style_experience_conflict": {},
                    "commercial_pacing": {}, "scene_credibility": {},
                    "deslop_gate": {}, "llm_semantic": {},
                },
                "level_executed": level,
            }

        if not scene_contract:
            return {
                "passed": False,
                "commit_blocked": True,
                "violations": [_make_violation_with_hash(_text_hash, _review_version,
                    "missing_contract", "critical", "Scene contract is missing; cannot validate",
                    source="deterministic", expected_behavior="编译器应产出有效合同",
                )],
                "reports": {
                    "deterministic": {}, "consistency": {}, "semantic": {}, "fcip": {},
                    "hard_correctness": {}, "narrative_contract": {},
                    "style_quality": {}, "reader_experience": {},
                    "narrative_experience": {}, "literary_quality": {},
                    "mode_fit": {}, "style_experience_conflict": {},
                    "commercial_pacing": {}, "scene_credibility": {},
                    "deslop_gate": {}, "llm_semantic": {},
                },
                "level_executed": level,
            }

        all_violations = []
        reports = {
            "deterministic": {}, "consistency": {}, "semantic": {}, "fcip": {},
            "quality_checkers": {}, "experimental": {},
            "hard_correctness": {}, "narrative_contract": {},
            "style_quality": {}, "reader_experience": {},
            "narrative_experience": {}, "literary_quality": {},
            "mode_fit": {}, "style_experience_conflict": {},
            "commercial_pacing": {}, "scene_credibility": {},
            "deslop_gate": {}, "prose_lint": {},
            "foreshadowing_guardrails": {}, "llm_semantic": {},
        }

        # 0. Cheap deterministic prose lint runs before expensive LLM checks.
        try:
            from app.services.prose_lint_service import ProseLintService

            lint_findings = ProseLintService().lint(generated_text)
            lint_violations = [
                _make_violation_with_hash(
                    _text_hash,
                    _review_version,
                    finding.type,
                    finding.severity,
                    finding.detail,
                    source="prose_lint",
                    expected_behavior="修正文中可精确定位的低级文法或标点问题",
                    **finding.to_violation_kwargs(),
                )
                for finding in lint_findings
            ]
            all_violations.extend(lint_violations)
            reports["prose_lint"] = {
                "findings_count": len(lint_findings),
                "violations": lint_violations,
            }
            if any(v.get("blocks_commit") for v in lint_violations):
                return self._build_report(all_violations, reports, "prose_lint_short_circuited")
        except Exception as exc:
            _logger.warning("ProseLint check failed: %s", exc)

        # ------------------------------------------------------------------
        # 1. SceneContractCompiler（如果启用）
        # ------------------------------------------------------------------
        fact_contract = None
        narrative_contract_mode = self._mode(policy, "narrative_contract_mode")
        if narrative_contract_mode != "off":
            try:
                from app.services.scene_contract_compiler import SceneContractCompiler

                compiler_context = {
                    "raw_scene_contract": scene_contract,
                    "scene_beat": context.get("scene_beat", {}),
                    "story_state": context.get("current_state", {}),
                    "chapter_state": chapter_state,
                    "foreshadowing_ops": context.get("foreshadowing_ops", []),
                    "genre_profile": context.get("genre_profile"),
                    "worldview_context": context.get("worldview_context", {}),
                    "scene_provenance": context.get("scene_provenance", {}),
                }
                compiled = await SceneContractCompiler().compile(compiler_context)
                fact_contract = compiled.fact_contract
                reports["narrative_contract"]["compiler_warnings"] = compiled.compiler_warnings
                reports["narrative_contract"]["blocked_contract"] = compiled.blocked_contract
                reports["narrative_contract"]["fact_contract"] = fact_contract.model_dump()
                reports["narrative_contract"]["compiled_scene_contract"] = compiled.scene_contract
                # 将 fact_contract 写回 context，供后续抽取器和审计器使用
                context["fact_contract"] = fact_contract
                context["compiled_scene_contract"] = compiled.scene_contract
                if compiled.blocked_contract:
                    all_violations.append(_make_violation_with_hash(_text_hash, _review_version,
                        "scene_contract_compile_blocked",
                        "critical",
                        "Scene contract compiler blocked this scene contract.",
                        source="narrative_contract",
                        expected_behavior="Repair the scene contract before writing or committing the scene.",
                        suggested_strategy="repair_contract",
                        blocks_commit=narrative_contract_mode == "enforce",
                        evidence={
                            "compiler_warnings": compiled.compiler_warnings,
                            "fact_contract": fact_contract.model_dump(),
                        },
                    ))
            except Exception as exc:
                _logger.warning("SceneContractCompiler failed: %s", exc)
                if narrative_contract_mode == "enforce":
                    all_violations.append(_make_violation_with_hash(_text_hash, _review_version,
                        "scene_contract_compiler_unavailable",
                        "high",
                        f"Scene contract compiler unavailable: {exc}",
                        source="narrative_contract",
                        expected_behavior="Retry after the compiler is available.",
                        suggested_strategy="validator_retry",
                        blocks_commit=True,
                        evidence={"error": str(exc)},
                    ))
                reports["narrative_contract"]["compiler_warnings"] = [f"compiler unavailable: {exc}"]

        # ------------------------------------------------------------------
        # 2. 场景可信度协议（SCP）编译与审计
        # ------------------------------------------------------------------
        scene_credibility_mode = self._mode(policy, "scene_credibility_mode")
        if scene_credibility_mode != "off":
            try:
                from app.services.scene_credibility_compiler import SceneCredibilityCompiler
                from app.services.quality_checkers.scene_credibility_checker import SceneCredibilityChecker

                credibility_contract = context.get("scene_credibility_contract")
                if not credibility_contract and isinstance(scene_contract, dict):
                    credibility_contract = scene_contract.get("scene_credibility_contract")
                if not credibility_contract:
                    compiled_credibility = SceneCredibilityCompiler().compile({
                        "project_id": project_id,
                        "chapter_number": chapter_number,
                        "scene_index": scene_index,
                        "scene_contract": scene_contract,
                        "fact_contract": fact_contract.model_dump() if hasattr(fact_contract, "model_dump") else context.get("fact_contract", {}),
                        "scene_provenance": context.get("scene_provenance", {}),
                        "chapter_state": chapter_state,
                        "story_state": current_state,
                        "character_cards": character_cards,
                        "quality_memory": context.get("quality_memory", {}),
                    })
                    credibility_contract = compiled_credibility.contract.model_dump()
                    context["scene_credibility_contract"] = credibility_contract
                report = SceneCredibilityChecker().check(
                    generated_text,
                    credibility_contract,
                    mode=scene_credibility_mode,
                )
                report["mode"] = scene_credibility_mode
                reports["scene_credibility"] = report

                scp_violations = report.get("violations", [])
                if scene_credibility_mode == "shadow":
                    pass
                elif scene_credibility_mode == "report":
                    for v in scp_violations:
                        v["blocks_commit"] = False
                    all_violations.extend(scp_violations)
                else:
                    all_violations.extend(scp_violations)
            except Exception as exc:
                _logger.warning("SceneCredibility layer failed: %s", exc)
                reports["scene_credibility"] = {
                    "status": "unavailable",
                    "error": str(exc),
                    "violations": [],
                    "advisories": [],
                }
                if scene_credibility_mode == "enforce":
                    all_violations.append(_make_violation_with_hash(_text_hash, _review_version,
                        "scene_credibility_unavailable",
                        "high",
                        f"Scene credibility compiler unavailable: {exc}",
                        source="scene_credibility",
                        expected_behavior="Retry after scene credibility compiler recovers.",
                        suggested_strategy="validator_retry",
                        blocks_commit=True,
                        evidence={"error": str(exc)},
                    ))

        # ------------------------------------------------------------------
        # 3. NarrativeProposition 抽取与审计
        # ------------------------------------------------------------------
        proposition_extraction_mode = self._mode(policy, "proposition_extraction_mode")
        proposition_audit_mode = self._mode(policy, "proposition_audit_mode")
        hard_correctness_mode = self._mode(policy, "hard_correctness_mode")
        if proposition_extraction_mode != "off" and hard_correctness_mode != "off":
            try:
                propositions, audit_result, _fc, prop_violations = await self._run_proposition_layer(
                    context, policy, _text_hash, _review_version,
                )
                reports["hard_correctness"]["extraction"] = {
                    "proposition_count": len(propositions.propositions) if propositions else 0,
                    "extractor_warnings": propositions.extractor_warnings if propositions else [],
                }
                reports["hard_correctness"]["audit"] = {
                    "passed": audit_result.passed if audit_result else None,
                    "commit_blocked": audit_result.commit_blocked if audit_result else None,
                    "violation_count": len(audit_result.violations) if audit_result else 0,
                    "warnings": audit_result.warnings if audit_result else [],
                }
                reports["hard_correctness"]["proposition_extraction"] = (
                    propositions.model_dump() if propositions else {}
                )
                reports["hard_correctness"]["proposition_audit"] = (
                    audit_result.model_dump() if audit_result else {}
                )

                audit_action = self._effective_mode(
                    hard_correctness_mode,
                    proposition_audit_mode,
                )
                if audit_action in {"off", "shadow"}:
                    pass
                elif audit_action == "report":
                    for v in prop_violations:
                        v["blocks_commit"] = False
                    all_violations.extend(prop_violations)
                else:
                    all_violations.extend(prop_violations)
            except Exception as exc:
                _logger.warning("Proposition layer failed: %s", exc)
                reports["hard_correctness"]["error"] = str(exc)
                if hard_correctness_mode == "enforce":
                    all_violations.append(_make_violation_with_hash(_text_hash, _review_version,
                        "proposition_layer_unavailable",
                        "high",
                        f"Proposition layer failed: {exc}",
                        source="hard_correctness",
                        expected_behavior="Retry proposition extraction and audit before commit.",
                        suggested_strategy="validator_retry",
                        blocks_commit=True,
                        evidence={"error": str(exc)},
                    ))

        # ------------------------------------------------------------------
        # 3. 长度预算检查
        # ------------------------------------------------------------------
        length_violations = self._check_length_budget(
            generated_text, context.get("word_budget"), _text_hash, _review_version,
        )
        all_violations.extend(length_violations)

        # ------------------------------------------------------------------
        # 4. 确定性检查
        # ------------------------------------------------------------------
        deterministic_violations = await self._run_deterministic(
            generated_text, scene_contract, chapter_state, character_names,
        )
        all_violations.extend(deterministic_violations)
        reports["deterministic"]["violations"] = deterministic_violations

        # ------------------------------------------------------------------
        # 5. 信息密度与表达多样性检查
        # ------------------------------------------------------------------
        checker_violations = self._run_quality_checkers(
            generated_text, scene_contract, chapter_state, _text_hash, _review_version,
        )
        all_violations.extend(checker_violations)
        reports["quality_checkers"]["violations"] = checker_violations

        # ------------------------------------------------------------------
        # 6. 实验性质量检查器
        # ------------------------------------------------------------------
        experimental = self._run_experimental_quality_checkers(
            generated_text, scene_contract, chapter_state, character_cards, policy,
            _text_hash, _review_version,
        )
        all_violations.extend(experimental.pop("_enforced_violations", []))
        if experimental:
            reports["experimental"] = experimental

        protocol_checked_violations = apply_protocol_to_violations(
            all_violations,
            getattr(self, "_protocol_context", {}) or {},
        )
        has_blocking = any(v.get("blocks_commit") for v in protocol_checked_violations)

        if level == "fast":
            return self._build_report(all_violations, reports, "fast")

        if has_blocking:
            rewrite_suggested = any(
                v.get("suggested_strategy") == "rewrite_scene"
                for v in protocol_checked_violations
                if v.get("blocks_commit")
            )
            if rewrite_suggested:
                return self._build_report(all_violations, reports, "fast_short_circuited")

        # ------------------------------------------------------------------
        # 7-11. Independent expensive checks fan out in parallel.
        # ------------------------------------------------------------------
        async def _safe_consistency():
            try:
                return await self._run_consistency(
                    generated_text, scene_contract, core_facts, current_state,
                    chapter_state, character_cards, _text_hash, _review_version,
                    previous_scene_ending=str(context.get("previous_scene_ending") or ""),
                    previous_scenes_summary=str(context.get("previous_scenes_summary") or ""),
                )
            except Exception as exc:
                return {
                    "violations": [_make_violation_with_hash(_text_hash, _review_version,
                        MetricRegistry.CONSISTENCY_CHECK_UNAVAILABLE, "medium",
                        f"一致性审校暂时不可用：{exc}",
                        source="consistency",
                        expected_behavior="稍后重新执行一致性审校",
                    )],
                    "raw_report": {},
                    "error": str(exc),
                }

        async def _safe_semantic():
            try:
                return await self._run_semantic(
                    generated_text, scene_contract, chapter_state, character_cards,
                )
            except Exception as exc:
                return [_make_violation_with_hash(_text_hash, _review_version,
                    MetricRegistry.CRITIC_PARSE_ERROR, "medium",
                    f"语义 Critic 暂时不可用：{exc}",
                    source="critic",
                    expected_behavior="稍后重新执行语义审校",
                )]

        async def _safe_fcip():
            try:
                result = await self._run_fcip(
                    generated_text, scene_contract, project, project_id,
                    chapter_number, scene_index, db_session_factory, _text_hash, _review_version,
                )
                if isinstance(result, list):
                    return {"violations": result, "detection": {}}
                return result
            except Exception as exc:
                return {
                    "violations": [_make_violation_with_hash(_text_hash, _review_version,
                        MetricRegistry.FCIP_CHECK_UNAVAILABLE, "medium",
                        f"伏笔审校暂时不可用：{exc}",
                        source="fcip",
                        expected_behavior="稍后重新执行伏笔审校",
                    )],
                    "detection": {},
                    "error": str(exc),
                }

        async def _safe_reader():
            return await self._run_reader_experience(
                generated_text, scene_contract, project, chapter_number, scene_index, policy,
            )

        async def _safe_experience():
            return await self._run_experience_quality(
                generated_text,
                scene_contract,
                context,
                project,
                policy,
            )

        (
            consistency_report,
            semantic_violations,
            fcip_report,
            reader_report,
            experience_reports,
        ) = await asyncio.gather(
            _safe_consistency(),
            _safe_semantic(),
            _safe_fcip(),
            _safe_reader(),
            _safe_experience(),
        )

        consistency_violations = consistency_report["violations"]
        all_violations.extend(consistency_violations)
        reports["consistency"] = consistency_report

        all_violations.extend(semantic_violations)
        reports["semantic"]["violations"] = semantic_violations

        fcip_violations = fcip_report["violations"]
        all_violations.extend(fcip_violations)
        reports["fcip"] = fcip_report

        if reader_report:
            reports.setdefault("experimental", {})["reader_experience"] = reader_report
            advisories = reader_report.get("advisories", [])
            if advisories and self._mode(policy, "reader_experience_mode") == "report":
                reports["experimental"].setdefault("advisories", []).extend(advisories)

        if experience_reports:
            for key in (
                "narrative_experience",
                "literary_quality",
                "mode_fit",
                "style_experience_conflict",
            ):
                if key in experience_reports:
                    reports[key] = experience_reports[key]
                    reports.setdefault("experimental", {})[key] = experience_reports[key]
            for key in ("narrative_experience", "literary_quality"):
                advisories = experience_reports.get(key, {}).get("advisories", [])
                if advisories and self._mode(policy, f"{key}_mode") in {"report", "assist", "enforce"}:
                    reports.setdefault("experimental", {}).setdefault("advisories", []).extend(advisories)

        commercial_report = self._run_commercial_pacing(
            generated_text,
            scene_contract,
            policy,
        )
        if commercial_report:
            reports["commercial_pacing"] = commercial_report
            reports.setdefault("experimental", {})["commercial_pacing"] = commercial_report
            mode = self._mode(policy, "commercial_pacing_mode")
            advisories = commercial_report.get("advisories", [])
            if advisories and mode in {"report", "assist", "enforce"}:
                reports.setdefault("experimental", {}).setdefault("advisories", []).extend(advisories)
            if advisories and mode == "enforce":
                for advisory in advisories:
                    if not isinstance(advisory, dict) or advisory.get("severity") != "high":
                        continue
                    # enforcement 分级：商业节奏指标（low_conflict_density 等）均为 advisory，
                    # 质量记忆累计不能改变 enforcement，不阻断 commit。
                    advisory_type = advisory.get("type", "low_reader_retention")
                    blocks = is_hard_blocking(advisory_type)
                    all_violations.append(_make_violation_with_hash(_text_hash, _review_version,
                        advisory_type,
                        "high",
                        advisory.get("detail", "Commercial pacing did not meet enforce threshold"),
                        source="commercial_pacing",
                        target_span=advisory.get("target_span"),
                        expected_behavior=advisory.get("expected_behavior", ""),
                        blocks_commit=blocks,
                        evidence={
                            "advisory": advisory,
                            "enforcement": "hard_blocking" if blocks else "advisory",
                        },
                    ))

        # 方案 30：B 类语义指标的 LLM 判定（两级过滤，仅当预筛发现潜在问题时才触发 LLM）
        llm_semantic_report = await self._run_llm_semantic(
            generated_text,
            scene_contract,
            policy,
            reports,
        )
        if llm_semantic_report:
            reports["llm_semantic"] = llm_semantic_report
            reports.setdefault("experimental", {})["llm_semantic"] = llm_semantic_report
            ls_mode = self._mode(policy, "llm_semantic_mode")
            ls_advisories = llm_semantic_report.get("advisories", [])
            if ls_advisories and ls_mode in {"report", "assist", "enforce"}:
                reports.setdefault("experimental", {}).setdefault("advisories", []).extend(ls_advisories)
            if ls_advisories and ls_mode == "enforce":
                for advisory in ls_advisories:
                    if not isinstance(advisory, dict) or advisory.get("severity") not in ("high", "medium"):
                        continue
                    advisory_type = advisory.get("type", "llm_semantic_violation")
                    blocks = is_hard_blocking(advisory_type)
                    all_violations.append(_make_violation_with_hash(_text_hash, _review_version,
                        advisory_type,
                        advisory.get("severity", "medium"),
                        advisory.get("detail", "LLM semantic checker detected issue"),
                        source="llm_semantic",
                        target_span=advisory.get("target_span"),
                        expected_behavior=advisory.get("expected_behavior", ""),
                        blocks_commit=blocks,
                        evidence={
                            "advisory": advisory,
                            "enforcement": "hard_blocking" if blocks else "advisory",
                        },
                    ))

        policy_enforced, policy_metrics = self._quality_policy_enforced_violations(
            scene_contract,
            reports,
            _text_hash,
            _review_version,
            return_metrics=True,
        )
        if policy_enforced:
            all_violations.extend(policy_enforced)
        reports["quality_policy_enforcement"] = {
            **policy_metrics,
            "enforced_count": len(policy_enforced),
            "violations": policy_enforced,
        }

        foreshadowing_guardrail_violations = self._foreshadowing_guardrail_violations(
            scene_contract,
            generated_text,
            _text_hash,
            _review_version,
        )
        if foreshadowing_guardrail_violations:
            all_violations.extend(foreshadowing_guardrail_violations)
        reports["foreshadowing_guardrails"] = {
            "violations": foreshadowing_guardrail_violations,
            "blocking_count": len(foreshadowing_guardrail_violations),
        }

        # ------------------------------------------------------------------
        # 12. 分层归类
        # ------------------------------------------------------------------
        layer_violations = self._categorize_violations_by_layer(all_violations)
        reports["hard_correctness"]["violations"] = layer_violations["hard_correctness"]
        reports["narrative_contract"]["violations"] = layer_violations["narrative_contract"]
        reports["style_quality"]["violations"] = layer_violations["style_quality"]
        reports["reader_experience"]["violations"] = layer_violations["reader_experience"]
        reports["narrative_experience"]["violations"] = layer_violations["narrative_experience"]
        reports["literary_quality"]["violations"] = layer_violations["literary_quality"]
        reports["mode_fit"]["violations"] = layer_violations["mode_fit"]
        reports["style_experience_conflict"]["violations"] = layer_violations["style_experience_conflict"]
        reports["commercial_pacing"]["violations"] = layer_violations["commercial_pacing"]
        reports["scene_credibility"]["violations"] = layer_violations["scene_credibility"]
        # 方案 30：B 类语义判定层单独归并，保留 LLM evidence 供 FBI 修复直接使用。
        reports["llm_semantic"]["violations"] = layer_violations["llm_semantic"]

        # ------------------------------------------------------------------
        # 13. Deslop Gate 检查（如果启用）
        # ------------------------------------------------------------------
        deslop_mode = self._mode(policy, "deslop_gate_mode")
        if deslop_mode != "off" and generated_text:
            try:
                from app.services.deslop_gate_engine import get_deslop_gate_engine
                from app.services.review_finding_normalizer import get_review_finding_normalizer

                engine = get_deslop_gate_engine()
                deslop_reports = engine.detect(generated_text)
                if deslop_reports:
                    normalizer = get_review_finding_normalizer()
                    deslop_findings = [
                        normalizer.normalize_deslop_report(r.model_dump())
                        for r in deslop_reports
                        if r.evidence_spans
                    ]
                    for finding in deslop_findings:
                        all_violations.append(_make_violation_with_hash(_text_hash, _review_version,
                            f"deslop_gate_{finding['source_checker'][-1]}",
                            {"S1": "critical", "S2": "high", "S3": "medium", "S4": "low"}.get(finding["severity"], "low"),
                            finding["issue"],
                            source="deslop_gate",
                            expected_behavior=finding["fix_direction"],
                            suggested_strategy=finding["suggested_strategy"],
                            evidence={"evidence_spans": finding["evidence"]},
                        ))
                    reports["deslop_gate"] = {"findings_count": len(deslop_findings)}
                    reports["deslop_gate"]["review_findings"] = deslop_findings
            except Exception as exc:
                _logger.warning("Deslop Gate check failed: %s", exc)

        return self._build_report(all_violations, reports, "full")

    # ------------------------------------------------------------------
    # 辅助方法：命题层抽取与审计
    # ------------------------------------------------------------------
    async def evaluate_validator_retry(
        self,
        context: dict,
        violation_types: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> dict:
        """Re-run only the failed validator component when isolation is safe.

        A proposition extractor outage does not invalidate deterministic prose,
        style, reader, or chapter review results that already completed for the
        same immutable candidate. Unknown or mixed failures retain the existing
        conservative full-gate behavior.
        """
        requested = {
            str(item or "").strip()
            for item in (violation_types or [])
            if str(item or "").strip()
        }
        supported = {
            "proposition_extractor_unavailable",
            "proposition_layer_unavailable",
        }
        if not requested or not requested.issubset(supported):
            return await self.evaluate(context, level="full")

        generated_text = str(context.get("generated_text") or "")
        scene_contract = context.get("scene_contract") or {}
        if not generated_text or not scene_contract:
            return await self.evaluate(context, level="full")

        self._protocol_context = context
        policy = context.get("generation_feature_policy")
        if policy is None:
            try:
                from app.services.generation_feature_policy import resolve_generation_feature_policy
                policy = resolve_generation_feature_policy(context.get("project"))
            except Exception:
                policy = None

        if (
            self._mode(policy, "proposition_extraction_mode") == "off"
            or self._mode(policy, "hard_correctness_mode") == "off"
        ):
            return await self.evaluate(context, level="full")

        text_hash = hashlib.md5(generated_text.encode()).hexdigest()[:12]
        review_version = int(context.get("review_version", 0) or 0)
        reports = {
            "hard_correctness": {},
            "narrative_contract": {},
        }
        violations: list[dict] = []

        if context.get("fact_contract") is None:
            try:
                from app.services.scene_contract_compiler import SceneContractCompiler

                compiled = await SceneContractCompiler().compile({
                    "raw_scene_contract": scene_contract,
                    "scene_beat": context.get("scene_beat", {}),
                    "story_state": context.get("current_state", {}),
                    "chapter_state": context.get("chapter_state", {}),
                    "foreshadowing_ops": context.get("foreshadowing_ops", []),
                    "genre_profile": context.get("genre_profile"),
                    "worldview_context": context.get("worldview_context", {}),
                    "scene_provenance": context.get("scene_provenance", {}),
                })
                context["fact_contract"] = compiled.fact_contract
                context["compiled_scene_contract"] = compiled.scene_contract
                reports["narrative_contract"] = {
                    "compiler_warnings": compiled.compiler_warnings,
                    "blocked_contract": compiled.blocked_contract,
                    "fact_contract": compiled.fact_contract.model_dump(),
                }
            except Exception as exc:
                violations.append(_make_violation_with_hash(
                    text_hash,
                    review_version,
                    "scene_contract_compiler_unavailable",
                    "high",
                    f"Scene contract compiler unavailable during targeted validator retry: {exc}",
                    source="narrative_contract",
                    expected_behavior="Retry after the scene contract compiler is available.",
                    suggested_strategy="validator_retry",
                    blocks_commit=True,
                    evidence={"error": str(exc), "retry_scope": "proposition"},
                ))
                result = self._build_report(
                    violations,
                    reports,
                    "validator_retry:proposition",
                )
                result["validator_retry_scope"] = "proposition"
                return result

        try:
            extraction, audit_result, _fact_contract, prop_violations = (
                await self._run_proposition_layer(
                    context,
                    policy,
                    text_hash,
                    review_version,
                )
            )
            reports["hard_correctness"] = {
                "extraction": {
                    "proposition_count": len(extraction.propositions) if extraction else 0,
                    "extractor_warnings": extraction.extractor_warnings if extraction else [],
                },
                "audit": {
                    "passed": audit_result.passed if audit_result else None,
                    "commit_blocked": audit_result.commit_blocked if audit_result else None,
                    "violation_count": len(audit_result.violations) if audit_result else 0,
                    "warnings": audit_result.warnings if audit_result else [],
                },
                "proposition_extraction": extraction.model_dump() if extraction else {},
                "proposition_audit": audit_result.model_dump() if audit_result else {},
            }
            audit_action = self._effective_mode(
                self._mode(policy, "hard_correctness_mode"),
                self._mode(policy, "proposition_audit_mode"),
            )
            if audit_action == "report":
                for violation in prop_violations:
                    violation["blocks_commit"] = False
                violations.extend(prop_violations)
            elif audit_action not in {"off", "shadow"}:
                violations.extend(prop_violations)
        except Exception as exc:
            violations.append(_make_violation_with_hash(
                text_hash,
                review_version,
                "proposition_layer_unavailable",
                "high",
                f"Proposition layer failed during targeted validator retry: {exc}",
                source="hard_correctness",
                expected_behavior="Retry proposition extraction and audit before commit.",
                suggested_strategy="validator_retry",
                blocks_commit=True,
                evidence={"error": str(exc), "retry_scope": "proposition"},
            ))

        result = self._build_report(
            violations,
            reports,
            "validator_retry:proposition",
        )
        result["validator_retry_scope"] = "proposition"
        return result

    async def _run_proposition_layer(
        self, context: dict, policy, text_hash: str, review_version: int,
    ) -> tuple:
        """Run proposition extraction and audit."""

        from app.services.narrative_proposition_extractor import NarrativePropositionExtractor
        from app.services.narrative_proposition_auditor import NarrativePropositionAuditor
        from app.models.narrative_proposition import AuditReport

        proposition_audit_mode = self._mode(policy, "proposition_audit_mode")
        hard_correctness_mode = self._mode(policy, "hard_correctness_mode")
        audit_action = self._effective_mode(hard_correctness_mode, proposition_audit_mode)

        # 抽取
        extractor = NarrativePropositionExtractor()
        context["strict_source_text"] = True
        extraction_result = await extractor.extract(context)
        extractor_failed = (
            (bool(extraction_result.extractor_warnings) and not extraction_result.propositions)
            or not extraction_result.complete
            or bool(extraction_result.failed_chunks)
        )
        if extractor_failed:
            blocks_commit = audit_action == "enforce" or hard_correctness_mode == "enforce"
            audit_result = AuditReport(
                passed=False,
                commit_blocked=blocks_commit,
                violations=[],
                proposition_ids=[],
                warnings=extraction_result.extractor_warnings,
            )
            violations = [
                _make_violation_with_hash(text_hash, review_version,
                    "proposition_extractor_unavailable",
                    "high",
                    "Proposition extractor did not return a complete, auditable proposition set.",
                    source="hard_correctness",
                    expected_behavior="Retry proposition extraction before commit.",
                    suggested_strategy="validator_retry",
                    blocks_commit=blocks_commit,
                    evidence={
                        "extractor_warnings": extraction_result.extractor_warnings,
                        "input_chars": extraction_result.input_chars,
                        "covered_chars": extraction_result.covered_chars,
                        "chunk_count": extraction_result.chunk_count,
                        "failed_chunks": extraction_result.failed_chunks,
                    },
                )
            ]
            return extraction_result, audit_result, context.get("fact_contract"), violations

        if audit_action == "off":
            audit_result = AuditReport(
                passed=True,
                commit_blocked=False,
                violations=[],
                proposition_ids=[p.proposition_id for p in extraction_result.propositions],
                warnings=["proposition_audit_off"],
            )
            return extraction_result, audit_result, context.get("fact_contract"), []

        # 审计
        audit_context = {
            "propositions": extraction_result.propositions,
            "fact_contract": context.get("fact_contract"),
            "scene_provenance": context.get("scene_provenance", {}),
            "previous_propositions": context.get("previous_propositions", []),
        }
        auditor = NarrativePropositionAuditor()
        audit_result = await auditor.audit(audit_context)

        # 将审计违规转换为 make_violation 格式
        violations = []
        for av in audit_result.violations:
            # 根据模式决定 blocks_commit
            if audit_action == "enforce":
                blocks_commit = av.blocks_commit
            elif audit_action == "report":
                blocks_commit = False
            else:
                # shadow 模式不应进入这里，仍作安全处理
                blocks_commit = False

            violations.append(_make_violation_with_hash(text_hash, review_version,
                vtype=av.type,
                severity=av.severity,
                detail=av.expected_behavior or av.type,
                source="hard_correctness",
                target_span=av.target_span or None,
                expected_behavior=av.expected_behavior,
                suggested_strategy=av.suggested_strategy,
                blocks_commit=blocks_commit,
                evidence=av.evidence,
            ))

        fact_contract = context.get("fact_contract")
        return extraction_result, audit_result, fact_contract, violations

    # ------------------------------------------------------------------
    # 辅助方法：违规分层归类
    # ------------------------------------------------------------------
    @staticmethod
    def _categorize_violations_by_layer(violations: list) -> dict:
        """Categorize violations into quality layers."""




        layers = {
            "hard_correctness": [],
            "narrative_contract": [],
            "style_quality": [],
            "reader_experience": [],
            "narrative_experience": [],
            "literary_quality": [],
            "mode_fit": [],
            "style_experience_conflict": [],
            "commercial_pacing": [],
            "scene_credibility": [],
            "deslop_gate": [],
            "llm_semantic": [],
        }

        for v in violations:
            vtype = v.get("type", "")
            source = v.get("source", "")

            if vtype in _HARD_CORRECTNESS_TYPES:
                layers["hard_correctness"].append(v)
            elif vtype in _NARRATIVE_CONTRACT_TYPES:
                layers["narrative_contract"].append(v)
            elif vtype in _STYLE_QUALITY_TYPES:
                layers["style_quality"].append(v)
            elif vtype in _READER_EXPERIENCE_TYPES:
                layers["reader_experience"].append(v)
            elif vtype in _NARRATIVE_EXPERIENCE_TYPES:
                layers["narrative_experience"].append(v)
            elif vtype in _LITERARY_QUALITY_TYPES:
                layers["literary_quality"].append(v)
            elif vtype in _MODE_FIT_TYPES:
                layers["mode_fit"].append(v)
            elif vtype in _STYLE_EXPERIENCE_CONFLICT_TYPES:
                layers["style_experience_conflict"].append(v)
            elif vtype in _COMMERCIAL_PACING_TYPES:
                layers["commercial_pacing"].append(v)
            elif vtype in _SCENE_CREDIBILITY_TYPES:
                layers["scene_credibility"].append(v)
            elif source == "hard_correctness":
                layers["hard_correctness"].append(v)
            elif source == "narrative_contract":
                layers["narrative_contract"].append(v)
            elif source == "style_quality":
                layers["style_quality"].append(v)
            elif source == "reader_experience":
                layers["reader_experience"].append(v)
            elif source == "narrative_experience":
                layers["narrative_experience"].append(v)
            elif source == "literary_quality":
                layers["literary_quality"].append(v)
            elif source == "mode_fit":
                layers["mode_fit"].append(v)
            elif source == "style_experience_conflict":
                layers["style_experience_conflict"].append(v)
            elif source == "commercial_pacing":
                layers["commercial_pacing"].append(v)
            elif source == "llm_semantic":
                # 方案 30：B 类语义判定产出的 violation 单独归层，
                # 避免被兜底到 hard_correctness 导致修复策略误判。
                layers["llm_semantic"].append(v)
            elif source == "scene_credibility":
                layers["scene_credibility"].append(v)
            elif source == "deslop_gate":
                layers["deslop_gate"].append(v)
            elif source == "prose_lint":
                layers["style_quality"].append(v)
            elif source == "consistency":
                layers["hard_correctness"].append(v)
            elif source == "fcip":
                layers["narrative_contract"].append(v)
            else:
                layers["hard_correctness"].append(v)

        return layers

    # ------------------------------------------------------------------
    # 报告构建
    # ------------------------------------------------------------------
    def _build_report(self, violations: list, reports: dict, level_executed: str) -> dict:
        protocol_context = getattr(self, "_protocol_context", {}) or {}
        violations = apply_protocol_to_violations(violations, protocol_context)
        layer_violations = self._categorize_violations_by_layer(violations)
        for layer_name, layer_items in layer_violations.items():
            reports.setdefault(layer_name, {})["violations"] = layer_items
        reports["fact_progression_protocol"] = summarize_protocol(violations)
        has_blocking = any(v.get("blocks_commit") for v in violations)

        # 构建层级摘要
        layer_summary = {}
        for layer_name in (
            "hard_correctness",
            "narrative_contract",
            "style_quality",
            "reader_experience",
            "narrative_experience",
            "literary_quality",
            "mode_fit",
            "style_experience_conflict",
            "commercial_pacing",
            "scene_credibility",
            "deslop_gate",
            "llm_semantic",
            "prose_lint",
            "foreshadowing_guardrails",
        ):
            layer_violations = reports.get(layer_name, {}).get("violations", [])
            blocking_count = sum(1 for v in layer_violations if v.get("blocks_commit"))
            layer_summary[layer_name] = {
                "total": len(layer_violations),
                "blocking": blocking_count,
            }

        return {
            "passed": not has_blocking,
            "commit_blocked": has_blocking,
            "degraded": bool(violations) and not has_blocking,
            "violations": violations,
            "reports": reports,
            "protocol_summary": reports["fact_progression_protocol"],
            "layer_summary": layer_summary,
            "level_executed": level_executed,
        }

    async def _run_deterministic(
        self, text: str, contract: dict, chapter_state: dict, character_names: list,
    ) -> list:
        from app.agents.scene_critic import SceneCriticAgent

        critic = SceneCriticAgent()
        violations = critic._deterministic_check(
            text, contract, chapter_state,
            character_names=character_names,
            is_book_first_scene=(contract.get("chapter_number") == 1 and contract.get("scene_index") == 0),
        )
        return violations

    def _run_experimental_quality_checkers(
        self,
        text: str,
        contract: dict,
        chapter_state: dict,
        character_cards: list,
        policy,
        text_hash: str,
        review_version: int,
    ) -> dict:
        experimental: dict = {}
        advisories: list[dict] = []

        ai_flavor_mode = self._mode(policy, "ai_flavor_mode")
        if ai_flavor_mode != "off":
            try:
                from app.services.quality_checkers.ai_flavor_checker import AIFlavorChecker

                report = AIFlavorChecker().check(text, contract, chapter_state)
                report["mode"] = ai_flavor_mode
                if ai_flavor_mode == "assist":
                    report["assistance_requested"] = True
                experimental["ai_flavor"] = report
                self._collect_advisories(advisories, report, ai_flavor_mode)
            except Exception as exc:
                _logger.warning("AIFlavorChecker experimental check failed: %s", exc)
                experimental["ai_flavor"] = {"status": "unavailable", "error": str(exc)}

        concept_budget_mode = self._mode(policy, "concept_budget_mode")
        if concept_budget_mode != "off":
            try:
                from app.services.quality_checkers.concept_budget_checker import ConceptBudgetChecker

                report = ConceptBudgetChecker().check(text, contract, chapter_state)
                report["mode"] = concept_budget_mode
                experimental["concept_budget"] = report
                self._collect_advisories(advisories, report, concept_budget_mode)
            except Exception as exc:
                _logger.warning("ConceptBudgetChecker experimental check failed: %s", exc)
                experimental["concept_budget"] = {"status": "unavailable", "error": str(exc)}

        character_voice_mode = self._mode(policy, "character_voice_mode")
        if character_voice_mode != "off":
            try:
                from app.services.quality_checkers.character_voice_checker import CharacterVoiceChecker

                report = CharacterVoiceChecker().check(text, contract, chapter_state, character_cards)
                report["mode"] = character_voice_mode
                if character_voice_mode == "assist":
                    report["assistance_requested"] = True
                experimental["character_voice"] = report
                self._collect_advisories(advisories, report, character_voice_mode)
            except Exception as exc:
                _logger.warning("CharacterVoiceChecker experimental check failed: %s", exc)
                experimental["character_voice"] = {"status": "unavailable", "error": str(exc)}

        if advisories:
            experimental["advisories"] = advisories
        enforced = self._enforced_advisory_violations(
            experimental, policy, text_hash, review_version,
        )
        if enforced:
            experimental["_enforced_violations"] = enforced
        return experimental

    async def _run_reader_experience(
        self,
        text: str,
        contract: dict,
        project,
        chapter_number: int,
        scene_index: int,
        policy,
    ) -> dict:
        if self._mode(policy, "reader_experience_mode") == "off":
            return {}
        try:
            from app.agents.reader_experience import ReaderExperienceAgent
            from app.services.reader_context_builder import ReaderContextBuilder

            reader_context = ReaderContextBuilder().build(
                generated_text=text,
                project=project,
                chapter_number=chapter_number,
                scene_index=scene_index,
            )
            report = await asyncio.wait_for(
                ReaderExperienceAgent().execute(reader_context),
                timeout=_READER_EXPERIENCE_TIMEOUT_SECONDS,
            )
            report["mode"] = self._mode(policy, "reader_experience_mode")
            return report
        except asyncio.TimeoutError:
            _logger.warning("ReaderExperience experimental check timed out")
            return {"status": "unavailable", "error": "reader_experience_timeout", "advisories": []}
        except Exception as exc:
            _logger.warning("ReaderExperience experimental check failed: %s", exc)
            return {"status": "unavailable", "error": str(exc), "advisories": []}

    async def _run_experience_quality(
        self,
        text: str,
        contract: dict,
        context: dict,
        project,
        policy,
    ) -> dict:
        modes = {
            "writing_mode_profile": self._mode(policy, "writing_mode_profile_mode"),
            "narrative_experience": self._mode(policy, "narrative_experience_mode"),
            "literary_quality": self._mode(policy, "literary_quality_mode"),
            "mode_fit": self._mode(policy, "mode_fit_mode"),
            "style_experience_conflict": self._mode(policy, "style_experience_conflict_mode"),
        }
        if all(mode == "off" for mode in modes.values()):
            return {}

        result: dict = {}
        try:
            from app.models.writing_mode import WritingModeProfile
            from app.services.experience_contract_compiler import ExperienceContractCompiler
            from app.services.experience_context_builder import ExperienceContextBuilder
            from app.services.quality_checkers.narrative_experience_checker import NarrativeExperienceChecker
            from app.agents.literary_quality_auditor import LiteraryQualityAuditorAgent
            from app.services.quality_memory_service import QualityMemoryService
            from app.services.writing_mode_profile_service import WritingModeProfileService

            profile = context.get("writing_mode_profile")
            if isinstance(profile, dict):
                writing_profile = WritingModeProfile(**profile)
            else:
                writing_profile = WritingModeProfileService().get_project_profile(project, policy)

            experience_contract = contract.get("experience_contract") if isinstance(contract, dict) else {}
            literary_contract = contract.get("literary_quality_contract") if isinstance(contract, dict) else {}
            style_conflict_report = context.get("style_conflict_report") or {}
            compiler_warnings = []

            if not experience_contract or not literary_contract:
                quality_memory = QualityMemoryService().get_project_quality_memory(project)
                builder = ExperienceContextBuilder()
                planning_context = builder.build_for_scene(
                    project_id=str(context.get("project_id", "")),
                    project=project,
                    chapter_number=int(context.get("chapter_number", 0) or 0),
                    scene_index=int(context.get("scene_index", 0) or 0),
                    scene_beat=context.get("scene_beat", {}),
                    story_state=context.get("current_state", {}),
                    scene_contract=contract,
                    prepared={
                        "character_cards": context.get("character_cards", []),
                        "style_profile": context.get("style_profile"),
                        "style_prompt": context.get("style_prompt", ""),
                        "style_embedding": context.get("style_embedding", {}),
                        "persona_card": context.get("persona_card", {}),
                    },
                    writing_mode_profile=writing_profile,
                    quality_memory=quality_memory,
                )
                compiled = await ExperienceContractCompiler().compile(planning_context)
                experience_contract = compiled.experience_contract.model_dump()
                literary_contract = compiled.literary_quality_contract.model_dump()
                style_conflict_report = compiled.style_conflict_report
                compiler_warnings = compiled.compiler_warnings

            if modes["narrative_experience"] != "off" or modes["mode_fit"] != "off":
                narrative_report = NarrativeExperienceChecker().check(
                    text,
                    experience_contract,
                    writing_profile,
                    scene_contract=contract,
                )
                narrative_report["mode"] = modes["narrative_experience"]
                narrative_report["contract"] = experience_contract
                narrative_report["compiler_warnings"] = compiler_warnings
                result["narrative_experience"] = narrative_report
                if isinstance(narrative_report.get("mode_fit"), dict):
                    mode_fit_report = dict(narrative_report["mode_fit"])
                    mode_fit_report["mode"] = modes["mode_fit"]
                    result["mode_fit"] = mode_fit_report

            if modes["literary_quality"] != "off":
                literary_report = await LiteraryQualityAuditorAgent().execute({
                    "generated_text": text,
                    "literary_quality_contract": literary_contract,
                    "writing_mode_profile": writing_profile.model_dump(),
                    "style_context": context.get("style_context", {}),
                })
                literary_report["mode"] = modes["literary_quality"]
                literary_report["contract"] = literary_contract
                result["literary_quality"] = literary_report

            if modes["style_experience_conflict"] != "off":
                style_report = dict(style_conflict_report or {})
                style_report.setdefault("schema_version", 1)
                style_report.setdefault("status", "ok")
                style_report["mode"] = modes["style_experience_conflict"]
                result["style_experience_conflict"] = style_report

        except Exception as exc:
            _logger.warning("Experience/literary quality layer failed: %s", exc)
            unavailable = {"status": "unavailable", "error": str(exc), "advisories": []}
            if modes["narrative_experience"] != "off":
                result["narrative_experience"] = unavailable
            if modes["literary_quality"] != "off":
                result["literary_quality"] = unavailable

        return result

    @staticmethod
    def _mode(policy, field: str) -> str:
        defaults = {
            "proposition_extraction_mode": "off",
            "proposition_audit_mode": "off",
            "hard_correctness_mode": "off",
            "narrative_contract_mode": "off",
            "style_quality_mode": "off",
            "writing_mode_profile_mode": "off",
            "narrative_experience_mode": "off",
            "literary_quality_mode": "off",
            "mode_fit_mode": "off",
            "style_experience_conflict_mode": "off",
            "commercial_pacing_mode": "off",
            "scene_credibility_mode": "off",
            "deslop_gate_mode": "off",
            # 方案 30 阶段 5：从 off 切换到 report（shadow）模式，
            # 收集 LLM 判定数据用于准确率验证。准确率 ≥90% 后切换到 enforce。
            "llm_semantic_mode": "report",
        }
        if policy is None:
            # 方案 30：llm_semantic_mode 默认 report，其余默认 off
            return defaults.get(field, "off")
        if isinstance(policy, dict):
            if field == "proposition_audit_mode" and field not in policy:
                return str(policy.get("proposition_extraction_mode", defaults[field]))
            if field == "hard_correctness_mode" and field not in policy:
                if policy.get("proposition_extraction_mode", "off") != "off":
                    return "enforce"
                return "off"
            return str(policy.get(field, defaults.get(field, "off")))
        return str(getattr(policy, field, defaults.get(field, "off")))

    def _run_commercial_pacing(self, text: str, contract: dict, policy) -> dict:
        if self._mode(policy, "commercial_pacing_mode") == "off":
            return {}
        try:
            from app.services.quality_checkers.commercial_pacing_checker import CommercialPacingChecker

            pacing_contract = {}
            if isinstance(contract, dict):
                pacing_contract = contract.get("commercial_pacing_contract") or {}
            report = CommercialPacingChecker().check(
                text,
                pacing_contract,
                scene_contract=contract,
            )
            report["mode"] = self._mode(policy, "commercial_pacing_mode")
            return report
        except Exception as exc:
            _logger.warning("CommercialPacingChecker failed: %s", exc)
            return {"status": "unavailable", "error": str(exc), "advisories": []}

    async def _run_llm_semantic(self, text: str, contract: dict, policy, reports: dict) -> dict:
        """方案 30：B 类语义指标的 LLM 判定。

        两级过滤：
        1. 预筛：从 commercial_pacing / literary_quality 报告中提取 A 类计数
        2. 精判：仅当预筛发现潜在问题时才触发 LLM

        约 60-70% 的场景不会触发 LLM（A 类预筛通过），降低成本。
        """
        if self._mode(policy, "llm_semantic_mode") == "off":
            return {}
        if not text:
            return {}

        # 预筛：从已跑的 A 类报告中提取计数
        prefilter_result = self._build_prefilter_input(reports)

        try:
            from app.services.quality_checkers.llm_semantic_checker import LlmSemanticChecker

            checker = LlmSemanticChecker()
            result = await asyncio.wait_for(
                checker.check(
                    text,
                    contract if isinstance(contract, dict) else {},
                    scene_contract=contract if isinstance(contract, dict) else None,
                    prefilter_result=prefilter_result,
                ),
                timeout=120,
            )
        except asyncio.TimeoutError:
            _logger.warning("LlmSemanticChecker timed out (120s)")
            return {"status": "unavailable", "error": "llm_timeout", "advisories": [], "degraded": True}
        except Exception as exc:
            _logger.warning("LlmSemanticChecker failed: %s", exc)
            return {"status": "unavailable", "error": str(exc), "advisories": [], "degraded": True}

        result["mode"] = self._mode(policy, "llm_semantic_mode")
        return result

    @staticmethod
    def _build_prefilter_input(reports: dict) -> dict:
        """从已跑的 A 类 checker 报告中提取预筛数据。"""
        prefilter: dict = {}

        # 从 commercial_pacing 报告提取冲突/好奇词计数
        cp_report = reports.get("commercial_pacing", {}) or {}
        if isinstance(cp_report, dict):
            prefilter["commercial_pacing_metrics"] = cp_report.get("metrics", {})

        # 从 literary_quality 报告提取抽象词计数
        lq_report = reports.get("literary_quality", {}) or {}
        if isinstance(lq_report, dict):
            prefilter["literary_quality_metrics"] = lq_report.get("metrics", {})

        # 从 scene_evidence 报告提取思想动词计数（如果 available）
        se_report = reports.get("scene_evidence", {}) or {}
        if isinstance(se_report, dict):
            prefilter["scene_evidence_metrics"] = se_report.get("metrics", {})

        return prefilter


    @staticmethod
    def _effective_mode(parent_mode: str, child_mode: str) -> str:
        if parent_mode == "off" or child_mode == "off":
            return "off"
        if parent_mode == "shadow" or child_mode == "shadow":
            return "shadow"
        if parent_mode == "report" or child_mode == "report":
            return "report"
        return "enforce"

    @staticmethod
    def _collect_advisories(target: list[dict], report: dict, mode: str) -> None:
        if mode in {"report", "assist", "enforce"}:
            target.extend(report.get("advisories", []))

    def _enforced_advisory_violations(
        self,
        experimental: dict,
        policy,
        text_hash: str,
        review_version: int,
    ) -> list[dict]:
        enforced = []
        for report_name, policy_field in (
            ("ai_flavor", "ai_flavor_mode"),
            ("concept_budget", "concept_budget_mode"),
            ("llm_semantic", "llm_semantic_mode"),  # 附录4问题10修复
        ):
            mode = self._mode(policy, policy_field)
            # 反例驱动策略：AI flavor 类 advisory 只在 enforce 模式才生成 violation（触发 FBI LLM 修复）。
            # report/assist 模式不生成 violation——AI 味问题不进 FBI 修复链路，
            # 而是通过 writer 输出后的确定性后处理（DeslopGateEngine）+ 下一章反例注入解决。
            # concept_budget/llm_semantic 保持原有行为（report/assist 也触发 FBI，只是不阻断）。
            if report_name == "ai_flavor":
                if mode != "enforce":
                    continue
            else:
                if mode not in {"enforce", "report", "assist"}:
                    continue
            # 附录4问题8修复：根据报告来源确定正确的 source 和层级
            source_field = "style_quality" if report_name == "ai_flavor" else "narrative_contract"
            if report_name == "llm_semantic":
                source_field = "llm_semantic"
            report = experimental.get(report_name, {})
            for advisory in report.get("advisories", []) if isinstance(report, dict) else []:
                if not isinstance(advisory, dict) or advisory.get("severity") != "high":
                    continue
                advisory_type = advisory.get("type", report_name)
                blocks_commit = mode == "enforce" and is_hard_blocking(advisory_type)
                enforced.append(_make_violation_with_hash(text_hash, review_version,
                    f"enforced_{advisory_type}",
                    "high",
                    advisory.get("detail", "Enhanced quality policy did not pass"),
                    source=source_field,
                    target_span=advisory.get("target_span"),
                    expected_behavior=advisory.get("expected_behavior", ""),
                    suggested_strategy="patch_text",
                    blocks_commit=blocks_commit,
                    # 通用修复 S-1 + S-5：advisory → enforced_violation 透传
                    # evidence_samples（命中位置清单）和 validator_metrics（可测量指标名），
                    # 让下游 FBI 修复链路和 SceneRepairer 能读到全部命中位置，
                    # 弱后检能正确路由到重算路径。
                    evidence={
                        key: value
                        for key, value in {
                            "evidence_samples": advisory.get("evidence_samples"),
                            "validator_metrics": advisory.get("validator_metrics"),
                            "enforcement": "hard_blocking" if blocks_commit else "advisory",
                        }.items()
                        if value
                    } or None,
                ))
        return enforced

    def _quality_policy_enforced_violations(
        self,
        scene_contract: dict,
        reports: dict,
        text_hash: str,
        review_version: int,
        *,
        return_metrics: bool = False,
    ):
        empty_metrics = {
            "enabled": False,
            "mode": "off",
            "candidates_total": 0,
            "candidates_considered": 0,
            "skipped": [],
        }
        quality_extensions = scene_contract.get("quality_extensions") if isinstance(scene_contract, dict) else {}
        if not isinstance(quality_extensions, dict):
            return ([], empty_metrics) if return_metrics else []
        candidates = quality_extensions.get("quality_gate_candidates")
        if not isinstance(candidates, list) or not candidates:
            return ([], empty_metrics) if return_metrics else []

        settings = self._quality_policy_enforcement_settings(quality_extensions)
        metrics = {
            "enabled": settings["enabled"],
            "mode": settings["mode"],
            "min_candidate_count": settings["min_candidate_count"],
            "allow_types": sorted(settings["allow_types"]),
            "deny_types": sorted(settings["deny_types"]),
            "candidates_total": len(candidates),
            "candidates_considered": 0,
            "skipped": [],
        }
        if not settings["enabled"] or settings["mode"] in {"off", "shadow"}:
            metrics["skipped"].append({"reason": f"mode_{settings['mode']}"})
            return ([], metrics) if return_metrics else []

        candidate_map: dict[str, dict] = {}
        for item in candidates:
            if not isinstance(item, dict):
                continue
            normalized = _normalize_quality_pattern_type(item.get("type"))
            if normalized:
                try:
                    count = int(item.get("count", 0) or 0)
                except (TypeError, ValueError):
                    count = 0
                if settings["allow_types"] and normalized not in settings["allow_types"]:
                    metrics["skipped"].append({"type": normalized, "reason": "not_in_allow_types"})
                    continue
                if normalized in settings["deny_types"]:
                    metrics["skipped"].append({"type": normalized, "reason": "in_deny_types"})
                    continue
                if count < settings["min_candidate_count"]:
                    metrics["skipped"].append({"type": normalized, "reason": "below_min_candidate_count", "count": count})
                    continue
                candidate_map.setdefault(normalized, item)
        if not candidate_map:
            return ([], metrics) if return_metrics else []
        metrics["candidates_considered"] = len(candidate_map)

        enforced: list[dict] = []
        seen: set[tuple[str, str]] = set()
        for advisory in self._iter_quality_advisories(reports):
            if not isinstance(advisory, dict):
                continue
            advisory_type = _normalize_quality_pattern_type(advisory.get("type"))
            if not advisory_type or advisory_type not in candidate_map:
                continue
            candidate = candidate_map[advisory_type]
            key = (advisory_type, str(advisory.get("detail") or ""))
            if key in seen:
                continue
            seen.add(key)
            severity = "high" if advisory.get("severity") in {"high", "critical"} else "medium"
            # enforcement 分级：只有 hard_blocking 指标才阻断 commit
            # 质量记忆累计次数不能改变 enforcement（memory_can_promote 永远为 False）
            blocks = is_hard_blocking(advisory_type)
            enforced.append(_make_violation_with_hash(
                text_hash,
                review_version,
                advisory_type,
                severity,
                advisory.get("detail") or candidate.get("instruction") or f"Quality policy candidate failed: {advisory_type}",
                source=str(advisory.get("detector") or advisory.get("source_checker") or "quality_policy"),
                target_span=advisory.get("target_span"),
                expected_behavior=advisory.get("expected_behavior") or candidate.get("instruction") or "",
                suggested_strategy="patch_text",
                blocks_commit=blocks,
                evidence={
                    "quality_policy_candidate": candidate,
                    "advisory": advisory,
                    "enforcement_reason": "quality_memory_candidate_matched_current_advisory",
                    "enforcement": "hard_blocking" if blocks else "advisory",
                },
            ))
        enforced = enforced[:8]
        return (enforced, metrics) if return_metrics else enforced

    @staticmethod
    def _quality_policy_enforcement_settings(quality_extensions: dict) -> dict:
        raw = quality_extensions.get("quality_policy_enforcement")
        settings = raw if isinstance(raw, dict) else {}
        mode = str(settings.get("mode") or "enforce").strip().lower()
        enabled = settings.get("enabled", True)
        if mode in {"off", "disabled"}:
            enabled = False
            mode = "off"
        try:
            min_candidate_count = int(settings.get("min_candidate_count", 0) or 0)
        except (TypeError, ValueError):
            min_candidate_count = 0

        def _type_set(*keys: str) -> set[str]:
            values: set[str] = set()
            for key in keys:
                raw_values = settings.get(key)
                if isinstance(raw_values, str):
                    raw_values = [raw_values]
                if isinstance(raw_values, list):
                    for value in raw_values:
                        normalized = _normalize_quality_pattern_type(value)
                        if normalized:
                            values.add(normalized)
            return values

        return {
            "enabled": bool(enabled),
            "mode": mode,
            "min_candidate_count": max(0, min_candidate_count),
            "allow_types": _type_set("allow_types", "whitelist", "enabled_types"),
            "deny_types": _type_set("deny_types", "blacklist", "disabled_types"),
        }

    def _foreshadowing_guardrail_violations(
        self,
        scene_contract: dict,
        generated_text: str,
        text_hash: str,
        review_version: int,
    ) -> list[dict]:
        if not isinstance(scene_contract, dict) or not generated_text:
            return []

        constraints = self._foreshadowing_guardrail_sources(scene_contract)
        if not constraints:
            return []

        normalized_text = _normalize_match_text(generated_text)
        violations: list[dict] = []
        seen: set[tuple[str, str]] = set()

        for constraint in constraints:
            if not isinstance(constraint, dict):
                continue
            action = str(constraint.get("action") or "").strip()
            rule = str(constraint.get("constraint") or "").strip()
            if action in {"reveal", "overdue_reveal"} or rule in {
                "resolve_or_advance_reveal_window",
                "overdue_reveal_requires_resolution_or_explicit_deferral",
            }:
                continue
            if rule and rule not in {
                "forbid_premature_reveal",
                "plant_clue_without_confirming_secret",
                "maintain_reader_state_without_resolution",
            }:
                continue

            name = str(constraint.get("name") or constraint.get("source_text") or "foreshadowing").strip()
            blocked_phrases = self._foreshadowing_blocked_phrases(constraint)
            for phrase in blocked_phrases:
                normalized_phrase = _normalize_match_text(phrase)
                if len(normalized_phrase) < 4 or normalized_phrase not in normalized_text:
                    continue
                key = (name, normalized_phrase)
                if key in seen:
                    continue
                seen.add(key)
                violations.append(_make_violation_with_hash(
                    text_hash,
                    review_version,
                    "premature_foreshadowing_reveal",
                    "high",
                    f"Foreshadowing '{name}' revealed protected interpretation before its reveal window.",
                    source="foreshadowing_guardrail",
                    target_span=phrase,
                    expected_behavior="Keep this foreshadowing line as clue, suspicion, or misdirection until its reveal window.",
                    suggested_strategy="patch_text",
                    blocks_commit=True,
                    evidence={
                        "foreshadowing_constraint": constraint,
                        "matched_phrase": phrase,
                        "enforcement_reason": "protected_foreshadowing_phrase_matched_candidate_text",
                    },
                ))
                break

        return violations[:8]

    def _foreshadowing_guardrail_sources(self, scene_contract: dict) -> list[dict]:
        sources: list[dict] = []
        long_term = scene_contract.get("long_term_constraints")
        if isinstance(long_term, dict):
            raw = long_term.get("foreshadowing_constraints")
            if isinstance(raw, list):
                sources.extend(item for item in raw if isinstance(item, dict))

        quality_extensions = scene_contract.get("quality_extensions")
        if isinstance(quality_extensions, dict):
            raw = quality_extensions.get("foreshadowing_guardrails")
            if isinstance(raw, list):
                sources.extend(item for item in raw if isinstance(item, dict))

        deduped: list[dict] = []
        seen: set[str] = set()
        for item in sources:
            marker = "|".join([
                str(item.get("name") or item.get("source_text") or ""),
                str(item.get("action") or ""),
                str(item.get("constraint") or ""),
            ])
            if marker in seen:
                continue
            seen.add(marker)
            deduped.append(item)
        return deduped

    @staticmethod
    def _foreshadowing_blocked_phrases(constraint: dict) -> list[str]:
        phrases: list[str] = []
        for key in (
            "secret_canonical_statement",
            "hidden_truth",
            "canonical_statement",
            "forbidden_assertion",
        ):
            value = constraint.get(key)
            if isinstance(value, str) and value.strip():
                phrases.append(value.strip())
        for key in ("reader_forbidden_interpretations", "forbidden_interpretations"):
            raw = constraint.get(key)
            if isinstance(raw, list):
                phrases.extend(str(item).strip() for item in raw if str(item).strip())
        return phrases

    @staticmethod
    def _iter_quality_advisories(reports: dict) -> list[dict]:
        advisories: list[dict] = []
        experimental = reports.get("experimental") if isinstance(reports, dict) else {}
        if isinstance(experimental, dict):
            raw = experimental.get("advisories")
            if isinstance(raw, list):
                advisories.extend(item for item in raw if isinstance(item, dict))
        for key in (
            "narrative_experience",
            "literary_quality",
            "commercial_pacing",
            "reader_experience",
            "scene_credibility",
            "quality_checkers",
        ):
            report = reports.get(key) if isinstance(reports, dict) else {}
            if isinstance(report, dict) and isinstance(report.get("advisories"), list):
                advisories.extend(item for item in report["advisories"] if isinstance(item, dict))
        return advisories

    def _run_quality_checkers(
        self, text: str, contract: dict, chapter_state: dict,
        text_hash: str, review_version: int,
    ) -> list:
        """Run deterministic local quality checkers."""
        from app.services.quality_checkers.info_density_checker import InfoDensityChecker
        from app.services.quality_checkers.expression_variety_checker import ExpressionVarietyChecker
        from app.services.quality_checkers.style_contract_conflict_detector import StyleContractConflictDetector

        violations = []

        checks = [
            ("InfoDensityChecker", lambda: InfoDensityChecker().check(text, contract, chapter_state)),
            ("ExpressionVarietyChecker", lambda: ExpressionVarietyChecker().check(text, contract, chapter_state)),
            ("StyleContractConflictDetector", lambda: StyleContractConflictDetector().check(contract)),
        ]
        try:
            from app.services.quality_checkers.narrative_coherence_checker import NarrativeCoherenceChecker
            checks.append(("NarrativeCoherenceChecker", lambda: NarrativeCoherenceChecker().check(text, contract, chapter_state)))
        except Exception as exc:
            _logger.warning("NarrativeCoherenceChecker import failed: %s", exc)

        for checker_name, run_check in checks:
            try:
                violations.extend(run_check())
            except Exception as exc:
                _logger.warning("%s failed: %s", checker_name, exc)
                violations.append(_make_violation_with_hash(text_hash, review_version,
                    "quality_checker_unavailable", "medium",
                    f"{checker_name} unavailable: {exc}",
                    source="deterministic",
                    expected_behavior="Retry this quality checker later",
                ))

        return violations

    async def _run_semantic(
        self, text: str, contract: dict, chapter_state: dict, character_cards: list,
    ) -> list:
        from app.agents.scene_critic import SceneCriticAgent

        critic = SceneCriticAgent()
        violations = await critic._llm_check(text, contract, chapter_state, character_cards)
        if any(v.get("type") == "critic_parse_error" for v in violations):
            violations = await critic._llm_check(text, contract, chapter_state, character_cards)
        return violations

    async def _run_consistency(
        self, text: str, scene_contract: dict, core_facts: dict, current_state: dict,
        chapter_state: dict, character_cards: list,
        text_hash: str, review_version: int,
        *,
        previous_scene_ending: str = "",
        previous_scenes_summary: str = "",
    ) -> dict:
        from app.agents.scene_validator import SceneValidator

        checker = SceneValidator()
        result = await checker.execute({
            "generated_text": text,
            "scene_contract": scene_contract,
            "core_facts": core_facts,
            "current_state": current_state,
            "chapter_state": chapter_state,
            "character_cards": character_cards,
            "previous_scene_ending": previous_scene_ending,
            "previous_scenes_summary": previous_scenes_summary,
        })
        raw_report = result.get("consistency", {})
        violations = []
        if raw_report.get("check_warning"):
            violations.append(_make_violation_with_hash(text_hash, review_version,
                "consistency_check_unavailable", "medium",
                f"Consistency response was not parseable: {raw_report['check_warning']}",
                source="consistency",
                expected_behavior="Retry consistency validation later",
            ))
        evidence_rejections: list[dict] = []
        for conflict in raw_report.get("conflicts", []):
            if not isinstance(conflict, dict):
                continue
            severity = "critical" if conflict.get("severity") == "critical" else "high"
            fact = conflict.get("fact", "")
            claim = conflict.get("text_claim", "")
            suggestion = conflict.get("suggestion", "")
            if _is_absence_only_authority_fact(fact):
                _logger.info(
                    "Discarding consistency conflict based only on missing context: %s",
                    str(fact)[:240],
                )
                continue
            conflict_category = conflict.get("category", "fact_conflict")
            grounding_audit = _consistency_conflict_grounding_audit(conflict, text)
            if grounding_audit.get("required") and not grounding_audit.get("passed"):
                evidence_rejections.append({
                    "category": conflict_category,
                    "fact": fact,
                    "text_claim": claim,
                    **grounding_audit,
                })
                _logger.info(
                    "Discarding ungrounded hard consistency conflict: %s",
                    grounding_audit.get("reason"),
                )
                continue
            duplicate_audit = (
                _duplicate_internal_conflict_evidence_audit(conflict, text)
                if conflict_category == "internal_conflict"
                else {"required": False, "passed": True}
            )
            if duplicate_audit.get("required") and not duplicate_audit.get("passed"):
                evidence_rejections.append({
                    "category": conflict_category,
                    "fact": fact,
                    "text_claim": claim,
                    **duplicate_audit,
                    "grounding": grounding_audit,
                })
                _logger.info(
                    "Discarding ungrounded duplicate internal conflict: %s",
                    duplicate_audit.get("reason"),
                )
                continue
            evidence_audit = {
                **duplicate_audit,
                "grounding": grounding_audit,
            }
            detail = f"Text claim {claim!r} conflicts with established fact {fact!r}"
            if suggestion:
                detail += f"; suggested correction: {suggestion!r}"
            strategy = _consistency_repair_strategy(conflict)
            grounded_spans = list(evidence_audit.get("evidence_spans") or [])
            target_span = claim if claim and claim in text else (
                grounded_spans[-1] if grounded_spans else claim or None
            )
            violations.append(_make_violation_with_hash(text_hash, review_version,
                conflict_category,
                severity,
                detail,
                source="consistency",
                target_span=target_span,
                expected_behavior=suggestion or fact,
                suggested_strategy=strategy,
                evidence={"consistency_evidence_audit": evidence_audit},
            ))
        if evidence_rejections:
            raw_report = {
                **raw_report,
                "evidence_rejections": evidence_rejections,
            }
        return {"violations": violations, "raw_report": raw_report}

    async def _run_fcip(
        self, text: str, contract: dict, project, project_id: str,
        chapter_number: int, scene_index: int, db_session_factory,
        text_hash: str = "", review_version: int = 0,
    ) -> dict:
        violations = []
        detection = {}
        try:
            from app.services.scene_generation_pipeline import run_foreshadowing_post_check

            # Open a short-lived session owned by this call. The 5-way gather
            # in evaluate() means multiple scenes/sub-checks can reach here at
            # once; a per-call session keeps them off a shared AsyncSession.
            if project is not None and db_session_factory is not None:
                async with db_session_factory() as fcip_db:
                    detection = await run_foreshadowing_post_check(
                        project=project,
                        project_id=project_id,
                        chapter_number=chapter_number,
                        scene_index=scene_index,
                        generated_text=text,
                        db=fcip_db,
                    )
                if detection.get("violations_found", 0) > 0:
                    for fv in detection.get("violations", []):
                        fcip_category = fv.get("fcip_category", "text_fixable")
                        if fcip_category == "registration_error":
                            violations.append(_make_violation_with_hash(text_hash, review_version,
                                "fcip_registration_error", "critical",
                                f"Foreshadowing registry data is inconsistent: {fv.get('detail') or fv.get('description', '')}",
                                source="fcip",
                                suggested_strategy="manual_review",
                                expected_behavior="Foreshadowing registry should be consistent",
                            ))
                        else:
                            severity = str(fv.get("severity", "Critical")).lower()
                            # 防御性兜底：fcip_detector 输出 description/matched_text/suggestion，
                            # 早期版本不带 detail/target_span/expected_behavior 字段。
                            # 优先读新字段，缺失时回退到旧字段，确保修复锚点不丢失。
                            fv_detail = fv.get("detail") or fv.get("description", "")
                            fv_target_span = fv.get("target_span") or fv.get("matched_text")
                            fv_expected = fv.get("expected_behavior") or fv.get("suggestion", "Prose should cite foreshadowing evidence correctly")
                            violations.append(_make_violation_with_hash(text_hash, review_version,
                                "fcip_text_violation", severity,
                                f"Foreshadowing text violation: {fv_detail}",
                                source="fcip",
                                target_span=fv_target_span,
                                expected_behavior=fv_expected,
                                suggested_strategy="patch_text",
                            ))
        except Exception as exc:
            violations.append(_make_violation_with_hash(text_hash, review_version,
                "fcip_check_unavailable", "medium",
                f"伏笔审校暂时不可用：{exc}",
                source="fcip",
                expected_behavior="Retry foreshadowing validation later",
            ))
            detection = {"error": str(exc)}

        return {"violations": violations, "detection": detection}

    def _check_length_budget(
        self,
        text: str,
        word_budget: dict | None,
        text_hash: str,
        review_version: int,
    ) -> list:
        if not word_budget or not isinstance(word_budget, dict) or not text:
            return []

        actual = len(text)
        soft_min = int(word_budget.get("soft_min_chars", 0) or 0)
        soft_max = int(word_budget.get("soft_max_chars", 0) or 0)
        hard_min = int(word_budget.get("hard_min_chars", 0) or 0)
        hard_max = int(word_budget.get("hard_max_chars", 0) or 0)

        violations = []
        if hard_min and actual < hard_min:
            violations.append(_make_violation_with_hash(text_hash, review_version,
                "scene_too_short", "high",
                f"Scene length {actual} is below hard minimum {hard_min}",
                source="deterministic",
                expected_behavior=f"Scene length should be at least {hard_min}",
            ))
        elif soft_min and actual < soft_min:
            violations.append(_make_violation_with_hash(text_hash, review_version,
                "scene_too_short", "medium",
                f"Scene length {actual} is below soft minimum {soft_min}",
                source="deterministic",
                expected_behavior=f"Scene length should be at least {soft_min}",
            ))

        if hard_max and actual > hard_max:
            violations.append(_make_violation_with_hash(text_hash, review_version,
                "scene_too_long", "high",
                f"Scene length {actual} exceeds hard maximum {hard_max}",
                source="deterministic",
                expected_behavior=f"Scene length should be at most {hard_max}",
            ))
        elif soft_max and actual > soft_max:
            violations.append(_make_violation_with_hash(text_hash, review_version,
                "scene_too_long", "low",
                f"Scene length {actual} exceeds soft maximum {soft_max}",
                source="deterministic",
                expected_behavior=f"Scene length should preferably be at most {soft_max}",
            ))
        return violations

