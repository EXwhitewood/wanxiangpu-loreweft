from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any


@dataclass(frozen=True)
class ValidatorMeasurement:
    validator: str
    metric: str
    value: Any
    status: str = "ok"
    confidence: float = 1.0
    enforcement: str = "commit_blocking"
    source: str = ""


class ValidatorProtocolAdapter:
    """Small common adapter for validator measure/evaluate/compare calls.

    This does not replace the existing validators. It gives FBI, recheck, and
    final acceptance a shared measurement surface while the older checkers are
    migrated incrementally.
    """

    _AI_DISCOURSE_METRICS = {
        "sentence_shell_count",
        "paragraph_shape_repeat_count",
        "mirrored_paragraph_opening_count",
        "semantic_restatement_count",
        "action_then_explanation_count",
        "double_conclusion_count",
        "moral_overclarity_count",
        "fake_interaction_count",
    }

    _RHYTHM_METRICS = {
        "uniform_sentence_streak_max",
        "dense_paragraph_streak_max",
        "breathing_paragraph_ratio",
        "abrupt_shift_count",
    }

    _AI_FLAVOR_METRICS = {
        "dash_per_1000",
        "dash_artifact_hits",
        "emdash_pair_hits",
        "tier1_hit_count",
        "tier3_hit_count",
        "false_range_hits",
        "false_range_count",
        "overexplain_hits",
        "overexplain_count",
        "template_phrase_hits",
        "summary_ending_hits",
        "technical_register_hits",
        "emotion_label_hits",
        "dialogue_tag_hits",
        "explanatory_narration_hits",
        "formulaic_transition_hits",
        "inflated_significance_hits",
        "inflated_significance_count",
        "promotional_language_hits",
        "promotional_language_count",
        "filler_phrase_hits",
        "filler_phrase_count",
        "negative_parallel_hits",
        "negative_parallel_count",
        "three_part_escalation_hits",
        "three_part_escalation_count",
        "vague_attribution_hits",
        "vague_attribution_count",
        "structure_word_hits",
        "structure_word_cluster_count",
        "tier3_density",
        # 通用修复 S-3：注册 simile 指标，让弱后检走真正的重算路径
        # 根因：simile_hits / simile_word_count 不在集合中，导致 compare 返回
        #   unsupported_metric，弱后检只看 before != after，L9 通过但 L10 失败。
        # 通用性：所有 ai_flavor 指标都应注册，统一弱后检路径。
        "simile_hits",
        "simile_word_count",
        "synesthesia_hits",
        "false_agency_hits",
    }

    # 通用修复（循环 #18 D18-4）：contract metric 与 raw metric 名称不匹配。
    # 根因：agent_skill_validator 的 _ai_flavor_constraint_findings 用 contract_key
    #   （如 three_part_escalation_count）作为 finding 的 metric 名，但 AIFlavorChecker
    #   产出的 raw metric 用 metric_key（如 three_part_escalation_hits）。
    #   target_self_check 用 contract metric 名查找 raw metrics → 返回 None →
    #   走 unsupported_metric 兜底（文本变了就 passed=True）→ L9 通过但 L10 失败。
    # 同理 pure_exposition_block_chars 的 raw metric 是 max_exposition_block_chars。
    # 修复：添加别名映射，measure 时如果直接查找失败，尝试别名。
    _METRIC_ALIASES: dict[str, str] = {
        "three_part_escalation_count": "three_part_escalation_hits",
        "negative_parallel_count": "negative_parallel_hits",
        "false_range_count": "false_range_hits",
        "vague_attribution_count": "vague_attribution_hits",
        "filler_phrase_count": "filler_phrase_hits",
        "promotional_language_count": "promotional_language_hits",
        "inflated_significance_count": "inflated_significance_hits",
        "overexplain_count": "overexplain_hits",
        "pure_exposition_block_chars": "max_exposition_block_chars",
    }

    _VOICE_FINGERPRINT_METRICS = {
        "profile_available",
        "voice_fingerprint_score",
        "generic_voice_hits",
        "generic_voice_density",
        "avoided_word_hits",
        "preferred_marker_hits",
        "observation_domain_hits",
        "average_sentence_length",
        "sentence_length_deviation",
        "dialogue_count",
        "speaker_count",
    }

    _LITERARY_QUALITY_METRICS = {
        "specificity",
        "rhythm_control",
        "prose_identity",
        "style_alignment",
        "cultural_texture",
        "emotional_evidence",
        "exposition_balance",
        "dialogue_pressure",
        "mode_fit",
        "length",
        "sentence_count",
        "paragraph_count",
        "sentence_length_cv",
        "paragraph_length_cv",
        "abstract_hits",
        "generic_label_hits",
        "concrete_hits",
        "sensory_hits",
        "body_hits",
        "emotion_label_hits",
        "emdash_hits",
        "unique_sentence_starters",
    }

    _SCENE_STRUCTURE_METRICS = {
        "length",
        "paragraph_count",
        "sentence_count",
        "goal_present",
        "conflict_present",
        "conflict_count",
        "value_change",
        "hook_present",
        "max_exposition_block_chars",
        # 循环#10：pure_exposition_block_chars 与 max_exposition_block_chars 是不同指标
        #   （前者检测纯陈述段落长度，后者检测最大陈述块），但同属 scene_structure 域。
        #   根因：pure_exposition_block_chars 未注册到任何验证器集合，
        #   validator_for_metric 返回空 → _validator_metric_self_check 跳过 →
        #   L9 走 unsupported_metric 兜底（supported=False, passed=文本变了就True）→
        #   L9 给出假乐观信号 → L10 强后检重新检测 → 指标仍超标 → blocking。
        #   注册后 L9 能正确评估指标是否改善，避免 L9/L10 判定不一致。
        "pure_exposition_block_chars",
        "passive_percentage",
    }

    _NARRATIVE_EXPERIENCE_METRICS = {
        "mode_fit",
        "retention_pressure",
        "curiosity_drive",
        "emotional_engagement",
        "clarity",
        "pacing_fit",
        "event_density",
        "reversal_density",
        "question_count",
        "payoff_count",
    }

    _POV_CONSISTENCY_METRICS = {
        "single_pov_per_scene",
        "head_hopping_count",
        "named_inner_access_hits",
        "ambiguous_inner_access_hits",
        "omniscient_breach_count",
        "pov_shift_count",
        "knowledge_boundary_breach",
        "narrator_commentary_count",
    }

    _TENSE_CONSISTENCY_METRICS = {
        "temporal_anchor_count",
        "flashback_entry_count",
        "flashback_return_count",
        "flashback_tense_correct",
        "time_jump_without_signal_count",
        "tense_drift_count",
    }

    _SCENE_EVIDENCE_METRICS = {
        "emotion_label_count",
        "emotion_label_count_per_500",
        "thought_verb_count",
        "thought_verb_count_per_500",
        "abstract_claim_count",
        "standalone_abstract_claims",
        "trait_statement_count",
        "body_language_cliche_count",
        "dialogue_emotion_tag_count",
        "concrete_evidence_count",
        "evidence_per_emotion_claim",
        "sense_category_count",
    }

    _SPECIFICITY_BUDGET_METRICS = {
        "specific_detail_count",
        "detail_hit_count",
        "concrete_hit_count",
        "action_hit_count",
        "sensory_hit_count",
        "scene_element_anchor_count",
        "missing_scene_element_count",
        "sense_category_count",
        "information_anchor_count",
        "generic_detail_count",
        # 通用修复：abstract_bare_count 由 _specificity_budget_metrics 计算，
        # 但未注册到任何 validator 集合，导致 validator_for_metric 返回空，
        # _validator_metric_self_check 跳过它，返回 unsupported_metric 弱后检。
        # L10 final skill validation 直接调用 agent_skill_validator 能正确检测，
        # 造成 L9 弱后检通过但 L10 强后检失败的不一致。
        "abstract_bare_count",
        "abstract_sentence_count",
    }

    # 方案 30：B 类语义判定指标。这些 metric 需要 LLM async 评估，
    # 同步 measure 无法调用。measure 会返回 unsupported，
    # FBI 后置校验对这类 metric 跳过（_audit_repair_result 仅在 supported=True 且
    # passed=False 时才判失败），真正的后置校验由 recheck 阶段的
    # QualityGate.evaluate（含 LlmSemanticChecker）完成。
    # 循环#10：standalone_abstract_claims 移至 _SCENE_EVIDENCE_METRICS 专属，
    # 不再双重注册。确定性检测器（_standalone_abstract_claim_count）更可靠，
    # LLM judge 覆盖会导致本地自检假通过（before=0 硬编码 + 默认 passed=True）。
    _LLM_SEMANTIC_METRICS = {
        # Contract-completion repairs are creative prose operations.  Their
        # local audit can prove that a bounded candidate changed without
        # deleting protected facts, but only the mandatory semantic recheck can
        # decide whether the obligation is genuinely satisfied.
        "missing_must_show",
        "ending_state_not_reached",
        "low_conflict_density",
        "weak_curiosity_engine",
        "weak_opening_hook",
        "weak_chapter_end_hook",
        "abstraction_over_budget",
        "missing_specific_detail_anchor",
        "voice_phrase_drift",
    }

    # These findings are judged by the canonical consistency recheck rather
    # than LlmSemanticChecker, but a bounded creative repair can still have no
    # safe deterministic self-check (for example, adding a missing causal
    # bridge while deliberately preserving both surrounding facts).  Such a
    # candidate must reach the mandatory recheck instead of being rejected as
    # ``unsupported_target_acceptance`` at the local mini-plan boundary.
    _CONSISTENCY_RECHECK_METRICS = {
        "fact_conflict",
        "internal_conflict",
        "spatial_conflict",
        "timeline_conflict",
        "identity_conflict",
        "naming_conflict",
        "setting_conflict",
        "causal_chain_error",
    }

    def __init__(self) -> None:
        self._measure_cache: dict[tuple[str, str, str], ValidatorMeasurement] = {}
        self.cache_hits = 0
        self.cache_misses = 0

    def measure(
        self,
        validator: str,
        metric: str,
        text: str,
        context: dict[str, Any] | None = None,
    ) -> ValidatorMeasurement:
        metric = str(metric or "").lower()
        requested_validator = str(validator or "").lower()
        validator = requested_validator or self.validator_for_metric(metric)
        context = context or {}
        snapshot = self.snapshot_from_context(context)
        cache_key = (
            validator or self.validator_for_metric(metric),
            metric,
            hashlib.md5((text or "").encode("utf-8")).hexdigest(),
            self.snapshot_hash(snapshot),
        )
        if cache_key in self._measure_cache:
            self.cache_hits += 1
            return self._measure_cache[cache_key]
        self.cache_misses += 1
        if requested_validator == "ai_discourse" or (not requested_validator and metric in self._AI_DISCOURSE_METRICS):
            from app.services.quality_checkers.ai_discourse_checker import AIDiscourseChecker

            report = AIDiscourseChecker().check(text or "")
            value = (report.get("metrics") or {}).get(metric)
            measurement = ValidatorMeasurement(
                validator="ai_discourse",
                metric=metric,
                value=value,
                status=str(report.get("status") or "ok"),
                confidence=0.86,
                source="ai_discourse_checker",
            )
            self._measure_cache[cache_key] = measurement
            return measurement
        if requested_validator == "ai_flavor" or (not requested_validator and metric in self._AI_FLAVOR_METRICS):
            from app.services.quality_checkers.ai_flavor_checker import AIFlavorChecker

            report = AIFlavorChecker().check(
                text or "",
                scene_contract=context.get("scene_contract") if isinstance(context, dict) else None,
                chapter_state=context.get("chapter_state") if isinstance(context, dict) else None,
            )
            metrics = report.get("metrics") or {}
            if metric == "dash_per_1000":
                dash_count = metrics.get("dash_artifact_hits")
                if dash_count is None:
                    dash_count = metrics.get("emdash_pair_hits")
                try:
                    value = round(float(dash_count or 0) / max(len(text or "") / 1000, 1), 4)
                except Exception:
                    value = 0.0
            else:
                value = metrics.get(metric)
                # 通用修复（循环 #18 D18-4）：contract metric 名与 raw metric 名不匹配时，
                # 尝试别名映射（如 three_part_escalation_count → three_part_escalation_hits）
                if value is None and metric in self._METRIC_ALIASES:
                    value = metrics.get(self._METRIC_ALIASES[metric])
            measurement = ValidatorMeasurement(
                validator="ai_flavor",
                metric=metric,
                value=value,
                status=str(report.get("status") or "ok"),
                confidence=0.86,
                source="ai_flavor_checker",
            )
            self._measure_cache[cache_key] = measurement
            return measurement
        if requested_validator == "rhythm_metrics" or (not requested_validator and metric in self._RHYTHM_METRICS):
            from app.services.agent_skill_validator import AgentSkillValidator

            value = AgentSkillValidator()._rhythm_metrics(text or "", context).get(metric)
            measurement = ValidatorMeasurement(
                validator="rhythm_metrics",
                metric=metric,
                value=value,
                status="ok",
                confidence=0.82,
                source="agent_skill_validator.rhythm_metrics",
            )
            self._measure_cache[cache_key] = measurement
            return measurement
        if requested_validator == "voice_fingerprint" or (not requested_validator and metric in self._VOICE_FINGERPRINT_METRICS):
            from app.services.quality_checkers.voice_fingerprint_checker import VoiceFingerprintChecker

            report = VoiceFingerprintChecker().check(text or "", context=context)
            metrics = dict(report.get("metrics") or {})
            value = bool(report.get("profile_available")) if metric == "profile_available" else metrics.get(metric)
            measurement = ValidatorMeasurement(
                validator="voice_fingerprint",
                metric=metric,
                value=value,
                status=str(report.get("status") or "ok"),
                confidence=0.82,
                source="voice_fingerprint_checker",
            )
            self._measure_cache[cache_key] = measurement
            return measurement
        if requested_validator == "literary_quality" or (not requested_validator and metric in self._LITERARY_QUALITY_METRICS):
            from app.services.quality_checkers.literary_quality_checker import LiteraryQualityChecker

            scene_contract = context.get("scene_contract") if isinstance(context, dict) else {}
            report = LiteraryQualityChecker().check(
                text or "",
                context.get("literary_quality_contract")
                or (scene_contract or {}).get("literary_quality_contract")
                or {},
                context.get("writing_mode_profile")
                or (scene_contract or {}).get("writing_mode_profile")
                or {},
                context.get("style_context") or {},
            )
            scores = dict(report.get("scores") or {})
            metrics = dict(report.get("metrics") or {})
            value = scores.get(metric) if metric in scores else metrics.get(metric)
            measurement = ValidatorMeasurement(
                validator="literary_quality",
                metric=metric,
                value=value,
                status=str(report.get("status") or "ok"),
                confidence=0.78,
                source="literary_quality_checker",
            )
            self._measure_cache[cache_key] = measurement
            return measurement
        if requested_validator == "narrative_experience" or (not requested_validator and metric in self._NARRATIVE_EXPERIENCE_METRICS):
            from app.services.quality_checkers.narrative_experience_checker import NarrativeExperienceChecker

            scene_contract = context.get("scene_contract") if isinstance(context, dict) else {}
            report = NarrativeExperienceChecker().check(
                text or "",
                context.get("narrative_experience_contract")
                or (scene_contract or {}).get("narrative_experience_contract")
                or (scene_contract or {}).get("experience_contract")
                or {},
                context.get("writing_mode_profile")
                or (scene_contract or {}).get("writing_mode_profile")
                or {},
                scene_contract or {},
            )
            scores = dict(report.get("scores") or {})
            metrics = dict(report.get("metrics") or {})
            value = scores.get(metric) if metric in scores else metrics.get(metric)
            measurement = ValidatorMeasurement(
                validator="narrative_experience",
                metric=metric,
                value=value,
                status=str(report.get("status") or "ok"),
                confidence=0.76,
                source="narrative_experience_checker",
            )
            self._measure_cache[cache_key] = measurement
            return measurement
        if requested_validator in {
            "scene_structure",
            "pov_consistency",
            "tense_consistency",
            "scene_evidence",
            "specificity_budget",
        } or (not requested_validator and metric in (
            self._SCENE_STRUCTURE_METRICS
            | self._POV_CONSISTENCY_METRICS
            | self._TENSE_CONSISTENCY_METRICS
            | self._SCENE_EVIDENCE_METRICS
            | self._SPECIFICITY_BUDGET_METRICS
        )):
            resolved_validator = requested_validator or self.validator_for_metric(metric)
            value = self._measure_agent_skill_validator_metric(resolved_validator, metric, text or "", context)
            measurement = ValidatorMeasurement(
                validator=resolved_validator,
                metric=metric,
                value=value,
                status="ok",
                confidence=0.74,
                source=f"agent_skill_validator.{resolved_validator}",
            )
            self._measure_cache[cache_key] = measurement
            return measurement
        # 方案 30：B 类语义判定 metric 需要 async LLM 评估，同步 measure 无法调用。
        # 返回 unsupported，让 compare 返回 supported=False，
        # FBI 后置校验对此类 metric 跳过（不误判修复失败），
        # 真正的后置校验由 recheck 阶段 QualityGate.evaluate 完成。
        if requested_validator == "llm_semantic" or (not requested_validator and metric in self._LLM_SEMANTIC_METRICS):
            measurement = ValidatorMeasurement(
                validator="llm_semantic",
                metric=metric,
                value=None,
                status="unsupported",
                confidence=0.0,
                enforcement="advisory_trace",
                source="llm_semantic_checker",
            )
            self._measure_cache[cache_key] = measurement
            return measurement
        measurement = ValidatorMeasurement(
            validator=validator or "unknown",
            metric=metric,
            value=None,
            status="unsupported",
            confidence=0.0,
            enforcement="advisory_trace",
            source="validator_protocol_adapter",
        )
        self._measure_cache[cache_key] = measurement
        return measurement

    def cache_stats(self) -> dict[str, int]:
        return {
            "entries": len(self._measure_cache),
            "hits": self.cache_hits,
            "misses": self.cache_misses,
        }

    def build_snapshot(
        self,
        *,
        criteria: list[dict[str, Any]] | None = None,
        validators: list[str] | None = None,
        contracts: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a portable validator contract snapshot for one repair round.

        The snapshot is intentionally small and deterministic. It does not own
        validator logic; it records which validator contract family and metric
        targets a caller expects every stage to use.
        """
        normalized_criteria = [self._normalize_criterion(item) for item in (criteria or [])]
        inferred_validators = {
            self.validator_for_metric(str(item.get("metric") or ""))
            for item in normalized_criteria
            if self.validator_for_metric(str(item.get("metric") or ""))
        }
        if validators:
            inferred_validators.update(str(item).lower() for item in validators if str(item or "").strip())
        payload = {
            "protocol": "loreweft_validator_protocol",
            "version": 1,
            "validators": sorted(inferred_validators),
            "criteria": normalized_criteria,
            "contracts": contracts or {},
        }
        payload["contract_hash"] = self.snapshot_hash(payload, include_hash=False)
        return payload

    @classmethod
    def snapshot_from_context(cls, context: dict[str, Any] | None) -> dict[str, Any]:
        context = context or {}
        snapshot = context.get("validator_snapshot")
        if isinstance(snapshot, dict):
            return snapshot
        return {
            "protocol": "loreweft_validator_protocol",
            "version": 1,
            "validators": [],
            "criteria": [],
            "contracts": {},
            "contract_hash": "default",
        }

    @classmethod
    def snapshot_hash(cls, snapshot: dict[str, Any] | None, *, include_hash: bool = True) -> str:
        snapshot = dict(snapshot or {})
        if not include_hash:
            snapshot.pop("contract_hash", None)
        raw = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def compare(
        self,
        metric: str,
        before_text: str,
        after_text: str,
        *,
        validator: str = "",
        expected: Any = None,
        direction: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        metric = str(metric or "").lower()
        direction = direction or self.default_direction(metric)
        before = self.measure(validator, metric, before_text, context)
        after = self.measure(validator, metric, after_text, context)
        snapshot = self.snapshot_from_context(context)
        if before.status == "unsupported" or after.status == "unsupported":
            return {
                "supported": False,
                "passed": False,
                "metric": metric,
                "reason": "unsupported_metric",
                "source": "validator_protocol_adapter",
                "validator_snapshot_hash": self.snapshot_hash(snapshot),
            }
        passed = self.evaluate_progress(
            metric,
            before.value,
            after.value,
            expected,
            direction=direction,
        )
        return {
            "supported": True,
            "passed": passed,
            "validator": after.validator,
            "metric": metric,
            "before": before.value,
            "after": after.value,
            "expected": expected,
            "direction": direction,
            "confidence": min(before.confidence, after.confidence),
            "enforcement": after.enforcement,
            "source": after.source,
            "validator_snapshot_hash": self.snapshot_hash(snapshot),
        }

    def evaluate(
        self,
        validator: str,
        metric: str,
        text: str,
        *,
        expected: Any = None,
        operator: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        measurement = self.measure(validator, metric, text, context)
        snapshot = self.snapshot_from_context(context)
        if measurement.status == "unsupported":
            return {
                "supported": False,
                "passed": False,
                "validator": measurement.validator,
                "metric": measurement.metric,
                "reason": "unsupported_metric",
                "source": measurement.source,
                "validator_snapshot_hash": self.snapshot_hash(snapshot),
            }
        operator = operator or ("gte" if self.default_direction(metric) == "increase" else "lte")
        passed = self._evaluate_value(measurement.value, expected, operator)
        return {
            "supported": True,
            "passed": passed,
            "validator": measurement.validator,
            "metric": measurement.metric,
            "actual": measurement.value,
            "expected": expected,
            "operator": operator,
            "confidence": measurement.confidence,
            "enforcement": measurement.enforcement,
            "source": measurement.source,
            "validator_snapshot_hash": self.snapshot_hash(snapshot),
        }

    def localize(
        self,
        issue: dict[str, Any],
        text: str,
        *,
        scene_texts: dict[int, str] | None = None,
    ) -> dict[str, Any]:
        try:
            from app.services.review_issue_localizer import enrich_review_issue_locations

            return enrich_review_issue_locations(dict(issue or {}), text or "")
        except Exception as exc:
            enriched = dict(issue or {})
            enriched.setdefault("localization_status", "unavailable")
            enriched.setdefault("localization_error", str(exc))
            return enriched

    @classmethod
    def validator_for_metric(cls, metric: str) -> str:
        metric = str(metric or "").lower()
        if metric in cls._AI_DISCOURSE_METRICS:
            return "ai_discourse"
        if metric in cls._AI_FLAVOR_METRICS:
            return "ai_flavor"
        if metric in cls._RHYTHM_METRICS:
            return "rhythm_metrics"
        if metric in cls._VOICE_FINGERPRINT_METRICS:
            return "voice_fingerprint"
        if metric in cls._LITERARY_QUALITY_METRICS:
            return "literary_quality"
        if metric in cls._NARRATIVE_EXPERIENCE_METRICS:
            return "narrative_experience"
        if metric in cls._POV_CONSISTENCY_METRICS:
            return "pov_consistency"
        if metric in cls._TENSE_CONSISTENCY_METRICS:
            return "tense_consistency"
        if metric in cls._SCENE_EVIDENCE_METRICS:
            return "scene_evidence"
        if metric in cls._SPECIFICITY_BUDGET_METRICS:
            return "specificity_budget"
        if metric in cls._SCENE_STRUCTURE_METRICS:
            return "scene_structure"
        # 方案 30：B 类语义判定指标路由到 llm_semantic validator。
        if metric in cls._LLM_SEMANTIC_METRICS:
            return "llm_semantic"
        return ""

    @classmethod
    def requires_semantic_recheck(cls, metric: str) -> bool:
        """Return whether acceptance must be deferred to the async recheck."""

        metric = str(metric or "").lower()
        return metric in cls._LLM_SEMANTIC_METRICS or metric in cls._CONSISTENCY_RECHECK_METRICS

    @staticmethod
    def default_direction(metric: str) -> str:
        metric = str(metric or "").lower()
        if metric in {
            "breathing_paragraph_ratio",
            "voice_fingerprint_score",
            "specificity",
            "rhythm_control",
            "prose_identity",
            "style_alignment",
            "cultural_texture",
            "emotional_evidence",
            "exposition_balance",
            "dialogue_pressure",
            "mode_fit",
            "retention_pressure",
            "curiosity_drive",
            "emotional_engagement",
            "clarity",
            "pacing_fit",
            "concrete_evidence_count",
            "evidence_per_emotion_claim",
            "sense_category_count",
            "specific_detail_count",
            "detail_hit_count",
            "scene_element_anchor_count",
            "information_anchor_count",
            # temporal_anchor_count 语义是"场景需要至少 1 个时间锚点"（expected=">= 1"），
            # 方向应为 increase。之前默认 decrease 导致 expected=">= 1" 矛盾：
            # evaluate_progress 走 `after < before` 分支，即使 LLM 添加了时间锚点（0→1）
            # 也会判定 1 < 0 = False，修复永远无法通过。
            "temporal_anchor_count",
            # 通用修复（循环 #18 D18-2）：presence 型布尔 metric 语义是"应该存在"，
            # 方向应为 increase。之前默认 decrease 导致严重 bug：
            #   goal_present before=true, after=false（修复把 goal 从有变无）
            #   evaluate_progress 走 decrease 分支：after(0) < before(1) → True → passed=True
            #   误判通过 → 场景 0 的 goal 被破坏但订单标记 succeeded
            #   → 终验 skill_gate 重新检测整个章节，发现 goal_present 违规
            #   → 但终验 case_delta 可能不包含场景 0 的 goal_present 违规
            #   → 死循环或终验失败
            # 同类 presence 型 metric：goal_present, conflict_present, hook_present, value_change
            "goal_present",
            "conflict_present",
            "hook_present",
            "value_change",
            # Boolean correctness/presence metrics improve from false to true.
            # Treating them as density metrics inverted successful repairs
            # (false -> true) into failures and consumed the only repair round.
            "flashback_tense_correct",
            "single_pov_per_scene",
            "transition_paragraph_present",
            "chapter_hook_present",
        }:
            return "increase"
        return "decrease"

    @staticmethod
    def evaluate_progress(
        metric: str,
        before_value: Any,
        after_value: Any,
        expected: Any,
        *,
        direction: str,
    ) -> bool:
        try:
            before = float(before_value or 0)
            after = float(after_value or 0)
        except Exception:
            return False
        expected_number: float | None = None
        try:
            if expected not in (None, ""):
                # 通用修复（循环 #18 D18-2）：布尔型 expected（如 goal_present expected=true）
                #   str(True)="True" 不含数字，正则匹配失败 → expected_number=None
                #   → 退化为纯 before/after 比较，无法判断 after 是否达到 expected 目标。
                #   修复：布尔值直接转换为 1.0/0.0。
                if isinstance(expected, bool):
                    expected_number = 1.0 if expected else 0.0
                else:
                    # 循环#10：expected 可能是 ">= 1"、"<= 150" 等带运算符的字符串，
                    #   float(">= 1") 会抛异常导致 expected_number=None，
                    #   退化为纯 before/after 比较——对阈值类指标（如 pure_exposition_block_chars
                    #   154→152 仍超 150 阈值）会产生 L9 通过但 L10 失败的不一致。
                    #   修复：用正则提取数字部分。
                    import re as _re
                    num_match = _re.search(r"-?\d+\.?\d*", str(expected))
                    expected_number = float(num_match.group()) if num_match else None
        except Exception:
            expected_number = None
        if direction == "increase":
            if expected_number is not None and after >= expected_number:
                return True
            return after > before
        if expected_number is not None and after <= expected_number:
            return True
        return after < before or (metric == "abrupt_shift_count" and after == 0)

    @staticmethod
    def _evaluate_value(value: Any, expected: Any, operator: str) -> bool:
        try:
            actual_number = float(value or 0)
            expected_number = float(expected or 0)
        except Exception:
            if operator in {"eq", "equals"}:
                return value == expected
            if operator in {"neq", "not_equals"}:
                return value != expected
            return False
        if operator in {"lte", "<="}:
            return actual_number <= expected_number
        if operator in {"lt", "<"}:
            return actual_number < expected_number
        if operator in {"gte", ">="}:
            return actual_number >= expected_number
        if operator in {"gt", ">"}:
            return actual_number > expected_number
        if operator in {"eq", "equals"}:
            return actual_number == expected_number
        if operator in {"neq", "not_equals"}:
            return actual_number != expected_number
        return False

    @staticmethod
    def _normalize_criterion(item: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(item, dict):
            return {}
        metric = str(item.get("metric") or "").lower()
        normalized = {
            "validator": str(item.get("validator") or ValidatorProtocolAdapter.validator_for_metric(metric) or "").lower(),
            "metric": metric,
            "operator": str(item.get("operator") or item.get("op") or "").lower(),
            "expected": item.get("expected"),
            "source": str(item.get("source") or ""),
        }
        for key in ("scene_index", "max_dash_per_1000", "max_dash_count", "required_spans", "forbidden_spans"):
            if key in item:
                normalized[key] = item.get(key)
        return normalized

    @staticmethod
    def _measure_agent_skill_validator_metric(
        validator: str,
        metric: str,
        text: str,
        context: dict[str, Any],
    ) -> Any:
        from app.services.agent_skill_validator import AgentSkillValidator

        validator_impl = AgentSkillValidator()
        scene_contract = context.get("scene_contract") or {}
        if validator == "scene_structure":
            raw_metrics = validator_impl._scene_structure_metrics(text, scene_contract)
            value = raw_metrics.get(metric)
            # 通用修复（循环 #18 D18-4）：contract metric 名与 raw metric 名不匹配时，
            # 尝试别名映射（如 pure_exposition_block_chars → max_exposition_block_chars）
            if value is None and metric in ValidatorProtocolAdapter._METRIC_ALIASES:
                value = raw_metrics.get(ValidatorProtocolAdapter._METRIC_ALIASES[metric])
            return value
        if validator == "pov_consistency":
            return validator_impl._pov_consistency_metrics(
                text,
                scene_contract,
                context.get("pov_character_card") or {},
                context.get("narrative_config") or {},
            ).get(metric)
        if validator == "tense_consistency":
            return validator_impl._temporal_consistency_metrics(
                text,
                scene_contract,
                context.get("narrative_config") or {},
            ).get(metric)
        if validator == "scene_evidence":
            return validator_impl._scene_evidence_metrics(
                text,
                scene_contract,
                context.get("pov_character_card") or {},
            ).get(metric)
        if validator == "specificity_budget":
            return validator_impl._specificity_budget_metrics(text, context).get(metric)
        return None
