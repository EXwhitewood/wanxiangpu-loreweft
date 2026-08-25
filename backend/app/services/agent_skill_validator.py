"""Post-generation validation for executable agent skills."""
from __future__ import annotations

import hashlib
import logging
import re
from typing import Any

from app.models.agent_skill import CompiledSkillPacket
from app.services.metric_registry import MetricRegistry, is_advisory
from app.utils.dash_artifacts import count_dash_artifacts
from app.utils.pov_evidence import analyze_inner_access

logger = logging.getLogger(__name__)

# 附录4问题34修复：集中化 advisory_only 过滤，避免硬编码 {"high_advisories", "medium_advisories"} 分散在多处
ADVISORY_ONLY_METRICS: frozenset[str] = frozenset({"high_advisories", "medium_advisories"})

STYLE_ADVISORY_VALIDATORS: frozenset[str] = frozenset({
    "ai_flavor",
    "ai_discourse",
    "literary_quality",
    "rhythm_metrics",
    "voice_fingerprint",
    "style_polish",
    "style_guard",
    "fbi_style_guard",
})

# ============================================================================
# 通用修复（循环 #1）：统一抽象词表 ABSTRACT_DETECTION_WORDS
# ----------------------------------------------------------------------------
# 根因：质检器 _DETAIL_ABSTRACT_WORDS / localizer _ABSTRACT_MARKERS /
#   review_minister 6 词表 / final_gate ABSTRACT_WORDS / 生成层 prompt 各自
#   维护私有词表，导致质检器判违规的句子 localizer 找不到 → fallback 整段 →
#   LLM 改错位置 → 指标不改善。
# 修复：定义单一权威词表，合并所有抽象词（论断性 + 描写性），去重。
#   下游所有组件（localizer / review_minister / final_gate / 生成层 prompt）
#   统一引用此常量，禁止各自维护私有词表。
# 通用性：适用于所有题材——论断性抽象词（意义/本质/命运）和描写性抽象词
#   （破旧/美丽/紧张）在所有题材中都需要"配具体证据"，规则一致。
# ============================================================================
ABSTRACT_DETECTION_WORDS: tuple[str, ...] = (
    # 论断性抽象词（来自 _SHOW_ABSTRACT_WORDS + _ABSTRACT_MARKERS + final_gate ABSTRACT_WORDS）
    "意义", "本质", "命运", "灵魂", "复杂", "关系", "气氛", "氛围", "局势",
    "压抑", "尴尬", "重要", "特殊", "象征", "代表", "体现", "说明", "意味着",
    "预示", "仿佛", "似乎", "意识到", "明白", "知道", "内心", "某种",
    "无法形容", "难以言说",
    # 描写性抽象词（来自 _DETAIL_ABSTRACT_WORDS）
    "破旧", "美丽", "安静", "紧张", "恐惧", "思念", "温暖", "寒冷",
    "孤独", "悲伤", "神秘", "危险", "华丽", "朴素", "熟悉", "陌生",
    "感觉", "情绪",
)


def is_advisory_only_metric(metric: str) -> bool:
    """判断 finding 是否为 advisory（不阻断 commit）。

    基于 metric_registry 的 enforcement 分级体系：
    - hard_blocking（破折号铁律等）→ False，参与 passed 判定，阻断 commit
    - advisory（明喻、句式、冲突密度等）→ True，不阻断 commit，最多修两轮后放行
    - observe_only → True，不阻断

    enforcement 分级由 ENFORCEMENT_OVERRIDES 覆盖表统一管理，
    质量记忆累计次数不能改变 enforcement（memory_can_promote 永远为 False）。
    """
    return is_advisory(metric)


def filter_blocking_findings(findings: list[dict]) -> list[dict]:
    """过滤掉 advisory_only 的 finding，只保留会阻断的 finding。"""
    from app.services.metric_registry import is_hard_blocking

    blocking: list[dict] = []
    for finding in findings:
        validator = str(finding.get("validator") or "").strip().lower()
        skill_id = str(finding.get("skill_id") or "").strip().lower()
        metric = str(
            finding.get("metric")
            or finding.get("type")
            or finding.get("violation_type")
            or ""
        ).strip().lower()
        style_advisory = (
            validator in STYLE_ADVISORY_VALIDATORS
            or skill_id in {"style_polish", "style_guard"}
        ) and not is_hard_blocking(metric)
        if style_advisory:
            finding["advisory_only"] = True
            finding["blocks_commit"] = False
            finding["enforcement"] = "advisory"
            if finding.get("severity") in {"critical", "high", "medium", "blocking"}:
                finding["severity"] = "advisory"
        if finding.get("advisory_only", False):
            continue
        blocking.append(finding)
    return blocking


class AgentSkillValidator:
    """Run skill-owned validators against generated output."""

    async def validate_output(
        self,
        packet: CompiledSkillPacket,
        *,
        generated_text: str,
        context: dict | None = None,
    ) -> dict:
        context = context or {}
        results: dict[str, dict] = {}
        failures: list[dict] = []

        for validator_id in packet.validators:
            if validator_id == "ai_flavor":
                result = self._validate_ai_flavor(packet, generated_text, context)
            elif validator_id == "ai_discourse":
                result = self._validate_ai_discourse(packet, generated_text, context)
            elif validator_id == "voice_fingerprint":
                result = self._validate_voice_fingerprint(packet, generated_text, context)
            elif validator_id == "rhythm_metrics":
                result = self._validate_rhythm_metrics(packet, generated_text, context)
            elif validator_id == "literary_quality":
                result = self._validate_literary_quality(packet, generated_text, context)
            elif validator_id == "scene_structure":
                result = self._validate_scene_structure(packet, generated_text, context)
            elif validator_id == "narrative_experience":
                result = self._validate_narrative_experience(packet, generated_text, context)
            elif validator_id == "pov_consistency":
                result = self._validate_pov_consistency(packet, generated_text, context)
            elif validator_id == "tense_consistency":
                result = self._validate_tense_consistency(packet, generated_text, context)
            elif validator_id == "scene_evidence":
                result = self._validate_scene_evidence(packet, generated_text, context)
            elif validator_id == "specificity_budget":
                result = self._validate_specificity_budget(packet, generated_text, context)
            else:
                finding = {
                    "skill_id": validator_id,
                    "validator": validator_id,
                    "metric": MetricRegistry.SKILL_VALIDATOR_NOT_IMPLEMENTED,
                    "actual": False,
                    "expected": True,
                    "severity": "medium",
                    "reason": "Validator is declared by a skill but has no executable implementation.",
                    "action": "implement_validator",
                }
                result = {
                    "validator": validator_id,
                    "passed": False,
                    "status": "degraded",
                    "findings": [finding],
                    "repair_hooks": packet.repair_hooks.get(validator_id, []),
                }
            results[validator_id] = result
            # 方案22修复：过滤 advisory_only 的 finding，不计入 failures。
            # 聚合统计指标（high_advisories/medium_advisories）标记为 advisory_only，
            # 不阻断最终验收，与底层 advisory 违规的 advisory_only_never_block 策略一致。
            if not result.get("passed", True):
                failures.extend(filter_blocking_findings(result.get("findings", [])))

        # 方案 7 Part D：为所有 finding 补齐 text_hash，使 skill 路径与 quality/health 路径同构，
        # 便于 FBI 去重与 stale finding 检测。
        text_hash = ""
        if isinstance(context, dict):
            text_hash = str(context.get("text_hash") or "")
        if not text_hash:
            packet_hash = getattr(packet, "text_hash", "")
            if packet_hash:
                text_hash = str(packet_hash)
        if not text_hash and generated_text:
            text_hash = hashlib.md5(generated_text.encode("utf-8")).hexdigest()
        if text_hash:
            for result in results.values():
                if not isinstance(result, dict):
                    continue
                for finding in result.get("findings") or []:
                    if isinstance(finding, dict) and not finding.get("text_hash"):
                        finding["text_hash"] = text_hash
            for finding in failures:
                if isinstance(finding, dict) and not finding.get("text_hash"):
                    finding["text_hash"] = text_hash

        return {
            "passed": not failures,
            "validators": results,
            "failures": failures,
            "requested": list(packet.validators),
            "text_hash": text_hash,
        }

    def _validate_ai_flavor(
        self,
        packet: CompiledSkillPacket,
        generated_text: str,
        context: dict,
    ) -> dict:
        contracts = packet.validation_contracts.get("ai_flavor", {})
        merged_constraints = self._merge_constraints(contracts)
        try:
            from app.services.quality_checkers.ai_flavor_checker import AIFlavorChecker

            report = AIFlavorChecker().check(
                generated_text,
                context.get("scene_contract"),
                context.get("chapter_state"),
            )
        except Exception as exc:
            logger.warning("ai_flavor skill validation failed to run: %s", exc, exc_info=True)
            return {
                "validator": "ai_flavor",
                "passed": False,
                "status": "unavailable",
                "constraints": merged_constraints,
                "findings": [{
                    "skill_id": "ai_flavor",
                    "validator": "ai_flavor",
                    "metric": "validator_available",
                    "actual": False,
                    "expected": True,
                    "severity": "high",
                    "reason": str(exc),
                    "action": self._repair_action(merged_constraints),
                }],
                "repair_hooks": packet.repair_hooks.get("ai_flavor", []),
            }

        metrics = dict(report.get("metrics") or {})
        findings = self._ai_flavor_constraint_findings(
            contracts=contracts,
            constraints=merged_constraints,
            metrics=metrics,
            advisories=list(report.get("advisories") or []),
            text=generated_text,
        )
        # 方案22修复：聚合统计指标（high_advisories/medium_advisories）标记为 advisory_only，
        # 不阻断 passed。与 literary_quality validator 保持一致。
        blocking_findings = filter_blocking_findings(findings)
        return {
            "validator": "ai_flavor",
            "passed": not blocking_findings,
            "status": "ok",
            "skill_ids": list(contracts.keys()),
            "constraints": merged_constraints,
            "metrics": metrics,
            "summary": report.get("summary", ""),
            "findings": findings,
            "blocking_findings": blocking_findings,
            "repair_hooks": packet.repair_hooks.get("ai_flavor", []),
        }

    def _validate_ai_discourse(
        self,
        packet: CompiledSkillPacket,
        generated_text: str,
        context: dict,
    ) -> dict:
        contracts = packet.validation_contracts.get("ai_discourse", {})
        constraints = self._merge_constraints(contracts)
        try:
            from app.services.quality_checkers.ai_discourse_checker import AIDiscourseChecker

            report = AIDiscourseChecker().check(
                generated_text,
                resource_packs=self._resource_packs_for_contracts(packet, contracts),
            )
        except Exception as exc:
            logger.warning("ai_discourse skill validation failed to run: %s", exc, exc_info=True)
            return self._unavailable_validator_result(
                "ai_discourse",
                contracts,
                constraints,
                packet,
                exc,
            )

        metrics = dict(report.get("metrics") or {})
        findings = self._max_metric_findings(
            validator_id="ai_discourse",
            contracts=contracts,
            constraints=constraints,
            metrics=metrics,
            checks=(
                ("sentence_shell_count_max", "sentence_shell_count", "paragraph_reconstruction"),
                ("paragraph_shape_repeat_count_max", "paragraph_shape_repeat_count", "paragraph_reconstruction"),
                ("semantic_restatement_count_max", "semantic_restatement_count", "paragraph_reconstruction"),
                ("action_then_explanation_count_max", "action_then_explanation_count", "paragraph_reconstruction"),
                ("mirrored_paragraph_opening_count_max", "mirrored_paragraph_opening_count", "paragraph_reconstruction"),
                ("double_conclusion_count_max", "double_conclusion_count", "paragraph_reconstruction"),
                ("moral_overclarity_count_max", "moral_overclarity_count", "paragraph_reconstruction"),
                ("fake_interaction_count_max", "fake_interaction_count", "paragraph_reconstruction"),
            ),
        )
        # 方案22修复：过滤 advisory_only 的 finding，不计入 passed。
        # 项目硬约束：AI flavor checkers must not block content generation;
        # enforcement_policy is set to advisory_only_never_block
        blocking_findings = filter_blocking_findings(findings)
        return {
            "validator": "ai_discourse",
            "passed": not blocking_findings,
            "status": report.get("status", "ok"),
            "skill_ids": list(contracts.keys()),
            "constraints": constraints,
            "metrics": metrics,
            "summary": report.get("summary", ""),
            "advisories": list(report.get("advisories") or []),
            "findings": findings,
            "blocking_findings": blocking_findings,
            "repair_hooks": packet.repair_hooks.get("ai_discourse", []),
        }

    def _validate_voice_fingerprint(
        self,
        packet: CompiledSkillPacket,
        generated_text: str,
        context: dict,
    ) -> dict:
        contracts = packet.validation_contracts.get("voice_fingerprint", {})
        constraints = self._merge_constraints(contracts)
        try:
            from app.services.quality_checkers.voice_fingerprint_checker import VoiceFingerprintChecker

            report = VoiceFingerprintChecker().check(
                generated_text,
                context=context,
                resource_packs=self._resource_packs_for_contracts(packet, contracts),
            )
        except Exception as exc:
            logger.warning("voice_fingerprint skill validation failed to run: %s", exc, exc_info=True)
            return self._unavailable_validator_result(
                "voice_fingerprint",
                contracts,
                constraints,
                packet,
                exc,
            )

        metrics = dict(report.get("metrics") or {})
        findings: list[dict] = []
        profile_available = bool(report.get("profile_available"))
        if constraints.get("profile_required") is True and not profile_available:
            findings.append(self._validator_finding(
                validator_id="voice_fingerprint",
                contracts=contracts,
                metric="profile_available",
                actual=False,
                expected=True,
                action="voice_reconstruction",
                constraints=constraints,
                reason="Voice fingerprint is required by the skill contract but no usable profile was provided.",
            ))
        minimum_score = constraints.get("minimum_score")
        if minimum_score is not None and profile_available:
            actual_score = float(metrics.get("voice_fingerprint_score") or 0)
            if actual_score < float(minimum_score):
                finding = self._validator_finding(
                    validator_id="voice_fingerprint",
                    contracts=contracts,
                    metric="voice_fingerprint_score",
                    actual=actual_score,
                    expected=float(minimum_score),
                    action="voice_reconstruction",
                    constraints=constraints,
                    reason="Voice fingerprint score is below the skill contract.",
                )
                finding["expected_min"] = finding.pop("expected_max")
                findings.append(finding)
        findings.extend(self._max_metric_findings(
            validator_id="voice_fingerprint",
            contracts=contracts,
            constraints=constraints,
            metrics=metrics,
            checks=(
                ("generic_voice_density_max", "generic_voice_density", "voice_reconstruction"),
                ("avoided_word_hits_max", "avoided_word_hits", "voice_reconstruction"),
            ),
        ))
        return {
            "validator": "voice_fingerprint",
            "passed": not findings,
            "status": report.get("status", "ok"),
            "skill_ids": list(contracts.keys()),
            "constraints": constraints,
            "metrics": metrics,
            "summary": report.get("summary", ""),
            "advisories": list(report.get("advisories") or []),
            "findings": findings,
            "repair_hooks": packet.repair_hooks.get("voice_fingerprint", []),
        }

    def _validate_rhythm_metrics(
        self,
        packet: CompiledSkillPacket,
        generated_text: str,
        context: dict,
    ) -> dict:
        contracts = packet.validation_contracts.get("rhythm_metrics", {})
        constraints = self._merge_constraints(contracts)
        metrics = self._rhythm_metrics(generated_text, context)
        findings = self._rhythm_metrics_findings(
            contracts=contracts,
            constraints=constraints,
            metrics=metrics,
        )
        # 方案22修复：过滤 advisory_only 的 finding，不计入 passed。
        # 项目硬约束：AI flavor checkers must not block content generation;
        # enforcement_policy is set to advisory_only_never_block
        blocking_findings = filter_blocking_findings(findings)
        return {
            "validator": "rhythm_metrics",
            "passed": not blocking_findings,
            "status": "ok",
            "skill_ids": list(contracts.keys()),
            "constraints": constraints,
            "metrics": metrics,
            "summary": "Rhythm metrics contract passed." if not blocking_findings else "Rhythm metrics contract failed.",
            "findings": findings,
            "blocking_findings": blocking_findings,
            "repair_hooks": packet.repair_hooks.get("rhythm_metrics", []),
        }

    def _validate_literary_quality(
        self,
        packet: CompiledSkillPacket,
        generated_text: str,
        context: dict,
    ) -> dict:
        contracts = packet.validation_contracts.get("literary_quality", {})
        merged_constraints = self._merge_constraints(contracts)
        scene_contract = context.get("scene_contract") or {}
        literary_contract = (
            context.get("literary_quality_contract")
            or scene_contract.get("literary_quality_contract")
            or {}
        )
        writing_mode_profile = (
            context.get("writing_mode_profile")
            or scene_contract.get("writing_mode_profile")
            or {}
        )
        try:
            from app.services.quality_checkers.literary_quality_checker import (
                LiteraryQualityChecker,
            )

            report = LiteraryQualityChecker().check(
                generated_text,
                literary_contract,
                writing_mode_profile,
                context.get("style_context") or {},
            )
        except Exception as exc:
            logger.warning(
                "literary_quality skill validation failed to run: %s",
                exc,
                exc_info=True,
            )
            return {
                "validator": "literary_quality",
                "passed": False,
                "status": "unavailable",
                "constraints": merged_constraints,
                "findings": [{
                    "skill_id": next(iter(contracts.keys()), "literary_quality"),
                    "validator": "literary_quality",
                    "metric": "validator_available",
                    "actual": False,
                    "expected": True,
                    "severity": "high",
                    "reason": str(exc),
                    "action": self._repair_action(merged_constraints),
                }],
                "repair_hooks": packet.repair_hooks.get("literary_quality", []),
            }

        advisories = list(report.get("advisories") or [])
        scores = dict(report.get("scores") or {})
        findings = self._literary_quality_constraint_findings(
            contracts=contracts,
            constraints=merged_constraints,
            advisories=advisories,
            scores=scores,
        )
        # 方案22修复：聚合统计指标（high_advisories/medium_advisories）标记为 advisory_only，
        # 不阻断 passed。这些指标是 advisory 违规的计数，底层 advisory 违规已由
        # advisory_only_never_block 策略标记为不阻断，聚合计数也不应阻断最终验收。
        # findings 仍然保留 advisory_only 的记录供观测/追溯，只是不影响 passed 判定。
        blocking_findings = filter_blocking_findings(findings)
        return {
            "validator": "literary_quality",
            "passed": not blocking_findings,
            "status": "ok",
            "skill_ids": list(contracts.keys()),
            "constraints": merged_constraints,
            "metrics": dict(report.get("metrics") or {}),
            "scores": scores,
            "summary": report.get("summary", ""),
            "advisories": advisories,
            "findings": findings,
            "blocking_findings": blocking_findings,
            "repair_hooks": packet.repair_hooks.get("literary_quality", []),
        }

    def _validate_scene_structure(
        self,
        packet: CompiledSkillPacket,
        generated_text: str,
        context: dict,
    ) -> dict:
        contracts = packet.validation_contracts.get("scene_structure", {})
        constraints = self._merge_constraints(contracts)
        metrics = self._scene_structure_metrics(
            generated_text,
            context.get("scene_contract") or {},
        )
        findings = self._scene_structure_findings(
            contracts=contracts,
            constraints=constraints,
            metrics=metrics,
            text=generated_text,
        )
        return {
            "validator": "scene_structure",
            "passed": not findings,
            "status": "ok",
            "skill_ids": list(contracts.keys()),
            "constraints": constraints,
            "metrics": metrics,
            "summary": "Scene structure contract passed." if not findings else "Scene structure contract failed.",
            "findings": findings,
            "repair_hooks": packet.repair_hooks.get("scene_structure", []),
        }

    def _validate_narrative_experience(
        self,
        packet: CompiledSkillPacket,
        generated_text: str,
        context: dict,
    ) -> dict:
        contracts = packet.validation_contracts.get("narrative_experience", {})
        constraints = self._merge_constraints(contracts)
        scene_contract = context.get("scene_contract") or {}
        narrative_contract = (
            context.get("narrative_experience_contract")
            or scene_contract.get("narrative_experience_contract")
            or scene_contract.get("experience_contract")
            or {}
        )
        writing_mode_profile = (
            context.get("writing_mode_profile")
            or scene_contract.get("writing_mode_profile")
            or {}
        )
        try:
            from app.services.quality_checkers.narrative_experience_checker import (
                NarrativeExperienceChecker,
            )

            report = NarrativeExperienceChecker().check(
                generated_text,
                narrative_contract,
                writing_mode_profile,
                scene_contract,
            )
        except Exception as exc:
            logger.warning(
                "narrative_experience skill validation failed to run: %s",
                exc,
                exc_info=True,
            )
            return {
                "validator": "narrative_experience",
                "passed": False,
                "status": "unavailable",
                "constraints": constraints,
                "findings": [{
                    "skill_id": next(iter(contracts.keys()), "narrative_experience"),
                    "validator": "narrative_experience",
                    "metric": "validator_available",
                    "actual": False,
                    "expected": True,
                    "severity": "high",
                    "reason": str(exc),
                    "action": self._repair_action(constraints),
                }],
                "repair_hooks": packet.repair_hooks.get("narrative_experience", []),
            }

        scores = dict(report.get("scores") or {})
        metrics = dict(report.get("metrics") or {})
        advisories = list(report.get("advisories") or [])
        findings = self._narrative_experience_findings(
            contracts=contracts,
            constraints=constraints,
            scores=scores,
            metrics=metrics,
            advisories=advisories,
        )
        # 方案22修复：聚合统计指标标记为 advisory_only，不阻断 passed
        blocking_findings = filter_blocking_findings(findings)
        return {
            "validator": "narrative_experience",
            "passed": not blocking_findings,
            "status": "ok",
            "skill_ids": list(contracts.keys()),
            "constraints": constraints,
            "metrics": metrics,
            "scores": scores,
            "mode_fit": report.get("mode_fit", {}),
            "summary": report.get("summary", ""),
            "advisories": advisories,
            "findings": findings,
            "blocking_findings": blocking_findings,
            "repair_hooks": packet.repair_hooks.get("narrative_experience", []),
        }

    def _validate_pov_consistency(
        self,
        packet: CompiledSkillPacket,
        generated_text: str,
        context: dict,
    ) -> dict:
        contracts = packet.validation_contracts.get("pov_consistency", {})
        constraints = self._merge_constraints(contracts)
        scene_contract = context.get("scene_contract") or {}
        metrics = self._pov_consistency_metrics(
            generated_text,
            scene_contract,
            context.get("pov_character_card") or {},
            context.get("narrative_config") or {},
            known_character_names=self._known_character_names(context),
        )
        findings = self._pov_consistency_findings(
            contracts=contracts,
            constraints=constraints,
            metrics=metrics,
        )
        return {
            "validator": "pov_consistency",
            "passed": not findings,
            "status": "ok",
            "skill_ids": list(contracts.keys()),
            "constraints": constraints,
            "metrics": metrics,
            "summary": "POV contract passed." if not findings else "POV contract failed.",
            "findings": findings,
            "repair_hooks": packet.repair_hooks.get("pov_consistency", []),
        }

    def _validate_tense_consistency(
        self,
        packet: CompiledSkillPacket,
        generated_text: str,
        context: dict,
    ) -> dict:
        contracts = packet.validation_contracts.get("tense_consistency", {})
        constraints = self._merge_constraints(contracts)
        metrics = self._temporal_consistency_metrics(
            generated_text,
            context.get("scene_contract") or {},
            context.get("narrative_config") or {},
        )
        findings = self._tense_consistency_findings(
            contracts=contracts,
            constraints=constraints,
            metrics=metrics,
        )
        return {
            "validator": "tense_consistency",
            "passed": not findings,
            "status": "ok",
            "skill_ids": list(contracts.keys()),
            "constraints": constraints,
            "metrics": metrics,
            "summary": "Temporal contract passed." if not findings else "Temporal contract failed.",
            "findings": findings,
            "repair_hooks": packet.repair_hooks.get("tense_consistency", []),
        }

    def _validate_scene_evidence(
        self,
        packet: CompiledSkillPacket,
        generated_text: str,
        context: dict,
    ) -> dict:
        contracts = packet.validation_contracts.get("scene_evidence", {})
        constraints = self._merge_constraints(contracts)
        metrics = self._scene_evidence_metrics(
            generated_text,
            context.get("scene_contract") or {},
            context.get("pov_character_card") or {},
        )
        findings = self._scene_evidence_findings(
            contracts=contracts,
            constraints=constraints,
            metrics=metrics,
        )
        return {
            "validator": "scene_evidence",
            "passed": not findings,
            "status": "ok",
            "skill_ids": list(contracts.keys()),
            "constraints": constraints,
            "metrics": metrics,
            "summary": "Scene evidence contract passed." if not findings else "Scene evidence contract failed.",
            "findings": findings,
            "repair_hooks": packet.repair_hooks.get("scene_evidence", []),
        }

    def _validate_specificity_budget(
        self,
        packet: CompiledSkillPacket,
        generated_text: str,
        context: dict,
    ) -> dict:
        contracts = packet.validation_contracts.get("specificity_budget", {})
        constraints = self._merge_constraints(contracts)
        metrics = self._specificity_budget_metrics(generated_text, context)
        findings = self._specificity_budget_findings(
            contracts=contracts,
            constraints=constraints,
            metrics=metrics,
        )
        return {
            "validator": "specificity_budget",
            "passed": not findings,
            "status": "ok",
            "skill_ids": list(contracts.keys()),
            "constraints": constraints,
            "metrics": metrics,
            "summary": "Specific-detail anchor contract passed." if not findings else "Specific-detail anchor contract failed.",
            "findings": findings,
            "repair_hooks": packet.repair_hooks.get("specificity_budget", []),
        }

    def _literary_quality_constraint_findings(
        self,
        *,
        contracts: dict[str, dict],
        constraints: dict,
        advisories: list[dict],
        scores: dict,
    ) -> list[dict]:
        findings: list[dict] = []
        high_count = sum(1 for item in advisories if item.get("severity") == "high")
        high_max = int(constraints.get("max_high_advisories", 0) or 0)
        if high_count > high_max:
            findings.append(self._literary_finding(
                contracts,
                "high_advisories",
                high_count,
                high_max,
                "High-severity literary-quality advisories exceed the skill contract.",
                constraints,
            ))

        if "max_medium_advisories" in constraints:
            medium_count = sum(
                1 for item in advisories if item.get("severity") == "medium"
            )
            medium_max = int(constraints.get("max_medium_advisories") or 0)
            if medium_count > medium_max:
                findings.append(self._literary_finding(
                    contracts,
                    "medium_advisories",
                    medium_count,
                    medium_max,
                    "Medium-severity literary-quality advisories exceed the skill contract.",
                    constraints,
                ))

        minimum_score = constraints.get("minimum_mode_fit_score")
        mode_fit = scores.get("mode_fit")
        if minimum_score is not None and mode_fit is not None:
            if float(mode_fit) < float(minimum_score):
                finding = self._literary_finding(
                    contracts,
                    "mode_fit",
                    float(mode_fit),
                    float(minimum_score),
                    "Literary mode-fit score is below the skill contract.",
                    constraints,
                )
                finding["expected_min"] = finding.pop("expected_max")
                findings.append(finding)
        return findings

    def _scene_structure_findings(
        self,
        *,
        contracts: dict[str, dict],
        constraints: dict,
        metrics: dict,
        text: str = "",
    ) -> list[dict]:
        findings: list[dict] = []
        checks = (
            ("goal_present", "goal_present", True, "inject_goal", "Scene goal is missing or not observable."),
            ("conflict_present", "conflict_present", True, "escalate_conflict", "Scene conflict is missing or too implicit."),
            ("value_change", "value_change", True, "restructure_value_change", "Scene lacks a visible value/state change."),
            ("hook_present", "hook_present", True, "add_hook", "Scene exit lacks a concrete hook."),
        )
        for contract_key, metric_key, expected, action, reason in checks:
            if constraints.get(contract_key) == "required" and metrics.get(metric_key) is not expected:
                # 通用修复 G-2：为 goal_present 补全 target_span/expected_behavior/evidence
                # 根因：原 _scene_finding 不携带这些字段，LLM 不知道改哪里、改成什么样。
                kw: dict[str, Any] = {}
                if metric_key == "goal_present":
                    kw["target_span"] = (text or "")[:30] or None
                    kw["expected_behavior"] = (
                        "正文中必须明确体现场景目标（角色的意图、渴望或行动方向），"
                        "通过角色台词、内心独白或行动呈现，不得只写铺垫而无目标驱动。"
                    )
                    goal_terms_matched = metrics.get("goal_terms_matched") or []
                    if goal_terms_matched:
                        kw["evidence_samples"] = [f"合同目标词: {term}" for term in goal_terms_matched[:8]]
                findings.append(self._scene_finding(
                    contracts,
                    metric_key,
                    metrics.get(metric_key),
                    expected,
                    reason,
                    action,
                    constraints,
                    **kw,
                ))

        conflict_min = constraints.get("conflict_count_min")
        if conflict_min is not None and int(metrics.get("conflict_count") or 0) < int(conflict_min):
            findings.append(self._scene_finding(
                contracts,
                "conflict_count",
                int(metrics.get("conflict_count") or 0),
                int(conflict_min),
                "Conflict count is below the scene-crafting contract.",
                "escalate_conflict",
                constraints,
            ))

        passive_max = constraints.get("passive_percentage_max")
        if passive_max is not None and float(metrics.get("passive_percentage") or 0) > float(passive_max):
            findings.append(self._scene_finding(
                contracts,
                "passive_percentage",
                round(float(metrics.get("passive_percentage") or 0), 3),
                float(passive_max),
                "Passive or static exposition exceeds the scene-crafting contract.",
                "exposition_to_action",
                constraints,
            ))

        # Phase 0 修正：pure_exposition_block_chars 的单位和命名一致性。
        # 根因：约束 key 名含 "words" 但实际比较的是字符数（_max_exposition_block_chars 返回 len(paragraph)）。
        #   导致阈值语义模糊，合约配置者可能误以为是词数。
        # 修复：统一使用字符数语义，约束 key 同时兼容 "max_chars" 和旧 "max_words"（向后兼容）。
        # 通用性：所有题材的纯说明块都以字符数为度量单位。
        exposition_max = (
            constraints.get("pure_exposition_block_max_chars")
            or constraints.get("pure_exposition_block_max_words")  # 向后兼容
        )
        if exposition_max is not None and int(metrics.get("max_exposition_block_chars") or 0) > int(exposition_max):
            findings.append(self._scene_finding(
                contracts,
                "pure_exposition_block_chars",
                int(metrics.get("max_exposition_block_chars") or 0),
                int(exposition_max),
                "Pure exposition block exceeds the scene-crafting contract.",
                "exposition_to_action",
                constraints,
            ))
        return findings

    def _narrative_experience_findings(
        self,
        *,
        contracts: dict[str, dict],
        constraints: dict,
        scores: dict,
        metrics: dict,
        advisories: list[dict],
    ) -> list[dict]:
        findings: list[dict] = []
        pressure_min = constraints.get("pressure_score_min")
        pressure = scores.get("dramatic_pressure")
        if pressure_min is not None and pressure is not None and float(pressure) < float(pressure_min):
            findings.append(self._experience_finding(
                contracts,
                "dramatic_pressure",
                float(pressure),
                float(pressure_min),
                "Dramatic pressure is below the skill contract.",
                constraints,
            ))

        reader_required = constraints.get("reader_momentum_at_exit") == "required"
        if reader_required:
            reading_drive = scores.get("reading_drive")
            ending_summary = bool(metrics.get("ending_summary"))
            if reading_drive is not None and (float(reading_drive) < 5.0 or ending_summary):
                findings.append(self._experience_finding(
                    contracts,
                    "reader_momentum_at_exit",
                    {
                        "reading_drive": reading_drive,
                        "ending_summary": ending_summary,
                    },
                    True,
                    "Scene exit does not create enough reader momentum.",
                    constraints,
                ))

        high_max = constraints.get("max_high_advisories")
        if high_max is not None:
            high_count = sum(1 for item in advisories if item.get("severity") == "high")
            if high_count > int(high_max):
                findings.append(self._experience_finding(
                    contracts,
                    "high_advisories",
                    high_count,
                    int(high_max),
                    "High-severity narrative experience advisories exceed the skill contract.",
                    constraints,
                ))
        return findings

    def _rhythm_metrics_findings(
        self,
        *,
        contracts: dict[str, dict],
        constraints: dict,
        metrics: dict,
    ) -> list[dict]:
        findings: list[dict] = []

        variance_min = constraints.get("sentence_length_variance_min")
        if variance_min is not None and float(metrics.get("sentence_length_variance") or 0) < float(variance_min):
            findings.append(self._rhythm_finding(
                contracts,
                "sentence_length_variance",
                round(float(metrics.get("sentence_length_variance") or 0), 3),
                float(variance_min),
                "Sentence-length variance is below the rhythm-control contract.",
                "vary_sentence_length",
                constraints,
                expected_key="expected_min",
            ))

        uniform_max = constraints.get("uniform_sentence_streak_max")
        if uniform_max is not None and int(metrics.get("uniform_sentence_streak_max") or 0) > int(uniform_max):
            findings.append(self._rhythm_finding(
                contracts,
                "uniform_sentence_streak_max",
                int(metrics.get("uniform_sentence_streak_max") or 0),
                int(uniform_max),
                "Too many adjacent sentences stay in the same length band.",
                "vary_sentence_length",
                constraints,
            ))

        breathing_min = constraints.get("breathing_paragraph_ratio_min")
        if (
            breathing_min is not None
            and int(metrics.get("paragraph_count") or 0) >= 4
            and float(metrics.get("breathing_paragraph_ratio") or 0) < float(breathing_min)
        ):
            findings.append(self._rhythm_finding(
                contracts,
                "breathing_paragraph_ratio",
                round(float(metrics.get("breathing_paragraph_ratio") or 0), 3),
                float(breathing_min),
                "Breathing paragraphs are below the rhythm-control contract.",
                "inject_breathing_paragraph",
                constraints,
                expected_key="expected_min",
            ))

        dense_max = constraints.get("dense_paragraph_streak_max")
        if dense_max is not None and int(metrics.get("dense_paragraph_streak_max") or 0) > int(dense_max):
            findings.append(self._rhythm_finding(
                contracts,
                "dense_paragraph_streak_max",
                int(metrics.get("dense_paragraph_streak_max") or 0),
                int(dense_max),
                "Dense paragraphs run too long without a reader-breathing beat.",
                "break_dense_streak",
                constraints,
            ))

        if (
            constraints.get("transition_paragraph_present") is True
            and metrics.get("transition_required") is True
            and metrics.get("transition_paragraph_present") is not True
        ):
            findings.append(self._rhythm_finding(
                contracts,
                "transition_paragraph_present",
                False,
                True,
                "A rhythm shift was requested but no transition paragraph is visible.",
                "inject_transition",
                constraints,
                expected_key="expected",
            ))

        abrupt_max = constraints.get("abrupt_shift_count")
        if abrupt_max is not None and int(metrics.get("abrupt_shift_count") or 0) > int(abrupt_max):
            findings.append(self._rhythm_finding(
                contracts,
                "abrupt_shift_count",
                int(metrics.get("abrupt_shift_count") or 0),
                int(abrupt_max),
                "Abrupt rhythm shifts exceed the skill contract.",
                "fix_abrupt_shift",
                constraints,
            ))

        return findings

    def _pov_consistency_findings(
        self,
        *,
        contracts: dict[str, dict],
        constraints: dict,
        metrics: dict,
    ) -> list[dict]:
        findings: list[dict] = []
        head_hopping_max = constraints.get("head_hopping_count")
        head_hopping_failed = (
            head_hopping_max is not None
            and int(metrics.get("head_hopping_count") or 0) > int(head_hopping_max)
        )
        if (
            constraints.get("single_pov_per_scene") == "required"
            and not metrics.get("single_pov_per_scene")
            and not head_hopping_failed
        ):
            findings.append(self._pov_finding(
                contracts,
                "single_pov_per_scene",
                metrics.get("single_pov_per_scene"),
                True,
                "Scene output appears to leave the declared single-POV boundary.",
                constraints,
                evidence=metrics.get("pov_shift_evidence") or [],
            ))

        if head_hopping_failed:
            findings.append(self._pov_finding(
                contracts,
                "head_hopping_count",
                int(metrics.get("head_hopping_count") or 0),
                int(head_hopping_max),
                "Inner access to non-POV characters exceeds the skill contract.",
                constraints,
                evidence=metrics.get("head_hopping_evidence") or [],
            ))

        breach_max = constraints.get("knowledge_boundary_breach")
        if breach_max is not None and int(metrics.get("knowledge_boundary_breach") or 0) > int(breach_max):
            findings.append(self._pov_finding(
                contracts,
                "knowledge_boundary_breach",
                int(metrics.get("knowledge_boundary_breach") or 0),
                int(breach_max),
                "Output reveals information outside the POV character knowledge boundary.",
                constraints,
                evidence=metrics.get("knowledge_boundary_evidence") or [],
            ))

        commentary_max = constraints.get("narrator_commentary_max")
        if commentary_max is not None and int(metrics.get("narrator_commentary_count") or 0) > int(commentary_max):
            findings.append(self._pov_finding(
                contracts,
                "narrator_commentary_count",
                int(metrics.get("narrator_commentary_count") or 0),
                int(commentary_max),
                "Narrator commentary exceeds the limited-POV contract.",
                constraints,
                evidence=metrics.get("narrator_commentary_evidence") or [],
            ))
        return findings

    def _tense_consistency_findings(
        self,
        *,
        contracts: dict[str, dict],
        constraints: dict,
        metrics: dict,
    ) -> list[dict]:
        findings: list[dict] = []
        drift_max = constraints.get("tense_drift_count")
        if drift_max is not None and int(metrics.get("tense_drift_count") or 0) > int(drift_max):
            # 通用修复（循环 #1）：传递 evidence_samples 给 finding
            evidence_samples = list(metrics.get("flashback_entry_samples") or []) + list(metrics.get("drift_samples") or [])
            findings.append(self._tense_finding(
                contracts,
                "tense_drift_count",
                int(metrics.get("tense_drift_count") or 0),
                int(drift_max),
                "Temporal/aspect drift exceeds the skill contract.",
                constraints,
                evidence_samples=evidence_samples,
            ))

        jump_max = constraints.get("time_jump_without_signal_count")
        if jump_max is not None and int(metrics.get("time_jump_without_signal_count") or 0) > int(jump_max):
            findings.append(self._tense_finding(
                contracts,
                "time_jump_without_signal_count",
                int(metrics.get("time_jump_without_signal_count") or 0),
                int(jump_max),
                "Time jump lacks a readable transition signal.",
                constraints,
            ))

        if constraints.get("flashback_tense_correct") == "required" and not metrics.get("flashback_tense_correct"):
            evidence_samples = list(metrics.get("flashback_entry_samples") or [])
            findings.append(self._tense_finding(
                contracts,
                "flashback_tense_correct",
                metrics.get("flashback_tense_correct"),
                True,
                "Flashback/memory segment lacks a clear return to the main-scene anchor.",
                constraints,
                evidence_samples=evidence_samples,
            ))

        if constraints.get("temporal_anchor_required") is True and int(metrics.get("temporal_anchor_count") or 0) == 0:
            findings.append(self._tense_finding(
                contracts,
                "temporal_anchor_count",
                0,
                ">= 1",
                "Scene lacks any observable temporal anchor.",
                constraints,
            ))
        return findings

    def _scene_evidence_findings(
        self,
        *,
        contracts: dict[str, dict],
        constraints: dict,
        metrics: dict,
    ) -> list[dict]:
        findings: list[dict] = []

        if constraints.get("emotion_label_replacement_required") is True:
            label_count = int(metrics.get("emotion_label_count") or 0)
            if label_count > int(constraints.get("emotion_label_count_max", 0) or 0):
                # 通用修复（循环 #6 EL-1）：补全 evidence_samples/target_span/expected_behavior
                # 根因：原 finding 不携带这些字段，LLM 不知道改哪里、改成什么样。
                # 通用性：所有题材的情绪标签都需要定位后改写为可观察行为。
                label_samples = list(metrics.get("emotion_label_samples") or [])
                # Phase 0 修正：分离情绪词命中与"关键情绪无证据"。
                # 根因：原 emotion_label_count 只检测"情绪标签数量超限"，不区分：
                #   - 情绪词命中（"他愤怒"——词法命中，可能邻接句已有行为证据）
                #   - 关键情绪无证据（"他愤怒"且邻接句无可观察行为——这才是真正需要修复的）
                # 修复：从 label_samples 中筛出"邻接句无证据"的子集，单独标记为 key_emotion_requires_observable_evidence。
                #   这些才是真正需要修复的违规，而非单纯的词法命中。
                key_emotion_samples = list(metrics.get("key_emotion_no_evidence_samples") or [])
                findings.append(self._scene_evidence_finding(
                    contracts,
                    "emotion_label_count",
                    label_count,
                    int(constraints.get("emotion_label_count_max", 0) or 0),
                    "Emotion labels remain where observable evidence is required.",
                    "emotion_to_scene_evidence",
                    constraints,
                    target_span=(key_emotion_samples[0][:80] if key_emotion_samples
                                 else (label_samples[0][:80] if label_samples else None)),
                    expected_behavior=(
                        "将直接情绪命名（如「愤怒」「悲伤」「恐惧」「惊讶」「厌恶」等情绪标签）"
                        "改写为可观察的外部行为描写——用具体的身体反应、动作细节或环境互动"
                        "替代直接情绪命名，让读者自己感受到情绪。"
                        "若违规含 evidence_samples，必须系统性处理所有列出的命中位置，禁止只改 target_span 一处。"
                    ),
                    evidence_samples=(key_emotion_samples if key_emotion_samples else label_samples),
                ))

        thought_max = constraints.get("thought_verb_count_max_per_500")
        if thought_max is not None and float(metrics.get("thought_verb_count_per_500") or 0) > float(thought_max):
            findings.append(self._scene_evidence_finding(
                contracts,
                "thought_verb_count_per_500",
                metrics.get("thought_verb_count_per_500"),
                float(thought_max),
                "Thought verbs exceed the show-don't-tell contract.",
                "emotion_to_scene_evidence",
                constraints,
            ))

        abstract_max = constraints.get("max_standalone_abstract_claims")
        if abstract_max is not None and int(metrics.get("standalone_abstract_claims") or 0) > int(abstract_max):
            # 通用修复 SA-2：为 standalone_abstract_claims 携带 evidence_samples/target_span/expected_behavior
            # 根因 SA3：原 finding 不携带命中的抽象句清单，LLM 不知道哪些句子需要补证据。
            sa_samples = metrics.get("standalone_abstract_samples") or []
            findings.append(self._scene_evidence_finding(
                contracts,
                "standalone_abstract_claims",
                int(metrics.get("standalone_abstract_claims") or 0),
                int(abstract_max),
                "Abstract claims are not backed by nearby concrete or sensory evidence.",
                "abstract_to_sensory",
                constraints,
                target_span=(sa_samples[0] if sa_samples else None),
                expected_behavior=(
                    "为每个孤立抽象论断补充就近的具体证据（感官描写、动作描写、对话），"
                    "不得删除抽象句本身。必须系统性处理 evidence_samples 中列出的所有命中位置，"
                    "禁止只改 target_span 一处。"
                ),
                evidence_samples=sa_samples[:15] if sa_samples else None,
            ))

        trait_max = constraints.get("trait_statement_count_max")
        if trait_max is not None and int(metrics.get("trait_statement_count") or 0) > int(trait_max):
            findings.append(self._scene_evidence_finding(
                contracts,
                "trait_statement_count",
                int(metrics.get("trait_statement_count") or 0),
                int(trait_max),
                "Character traits are stated directly instead of shown through choices.",
                "trait_to_choice",
                constraints,
            ))

        body_max = constraints.get("body_language_cliche_count_max")
        if body_max is not None and int(metrics.get("body_language_cliche_count") or 0) > int(body_max):
            # 通用修复（循环 #5 BL-3）：补全 evidence_samples/target_span/expected_behavior
            # 根因：原 finding 不携带这些字段，LLM 不知道改哪里、改成什么样。
            # 通用性：所有题材的身体语言陈词滥调都需要定位后改写为具体可观察动作。
            body_samples = list(metrics.get("body_language_cliche_samples") or [])
            findings.append(self._scene_evidence_finding(
                contracts,
                "body_language_cliche_count",
                int(metrics.get("body_language_cliche_count") or 0),
                int(body_max),
                "Generic body-language cliches exceed the skill contract.",
                "emotion_to_scene_evidence",
                constraints,
                target_span=body_samples[0][:80] if body_samples else None,
                expected_behavior=(
                    "将身体语言陈词滥调（如握紧拳头/咬唇/皱眉/叹气/心跳加速/呼吸急促/"
                    "眼中闪过/嘴角上扬/苦笑/冷笑/身体一僵/浑身发抖/瞳孔一缩/喉结滚动/"
                    "指节泛白/脸色苍白等模板化短语）改写为具体的、可观察的外部行为描写——"
                    "用特定的动作细节（如「指节掐进掌心，指甲留下一道白痕」代替「握紧拳头」）"
                    "或环境互动替代模板化身体反应。必须系统性处理 evidence_samples 中列出的所有命中位置，"
                    "禁止只改 target_span 一处。"
                ),
                evidence_samples=body_samples,
            ))

        if constraints.get("dialogue_tag_emotion_label_forbidden") is True and int(metrics.get("dialogue_emotion_tag_count") or 0) > 0:
            findings.append(self._scene_evidence_finding(
                contracts,
                "dialogue_emotion_tag_count",
                int(metrics.get("dialogue_emotion_tag_count") or 0),
                0,
                "Dialogue relies on emotional adverb tags instead of subtext.",
                "dialogue_to_subtext",
                constraints,
            ))

        senses_min = constraints.get("min_senses_per_scene")
        if senses_min is not None and int(metrics.get("sense_category_count") or 0) < int(senses_min):
            findings.append(self._scene_evidence_finding(
                contracts,
                "sense_category_count",
                int(metrics.get("sense_category_count") or 0),
                int(senses_min),
                "Scene does not use enough sensory channels to ground emotional claims.",
                "abstract_to_sensory",
                constraints,
            ))

        evidence_min = constraints.get("evidence_per_emotion_claim_min")
        if evidence_min is not None and int(metrics.get("emotion_label_count") or 0) > 0:
            ratio = float(metrics.get("evidence_per_emotion_claim") or 0)
            if ratio < float(evidence_min):
                findings.append(self._scene_evidence_finding(
                    contracts,
                    "evidence_per_emotion_claim",
                    round(ratio, 2),
                    float(evidence_min),
                    "Emotion claims do not have enough observable evidence.",
                    "emotion_to_scene_evidence",
                    constraints,
                ))
        return findings

    def _specificity_budget_findings(
        self,
        *,
        contracts: dict[str, dict],
        constraints: dict,
        metrics: dict,
    ) -> list[dict]:
        findings: list[dict] = []

        anchor_min = constraints.get("anchor_per_element_min")
        if anchor_min is not None and metrics.get("scene_element_count", 0) > 0:
            missing = metrics.get("missing_anchor_elements") or []
            if missing:
                findings.append(self._specificity_finding(
                    contracts,
                    "anchor_coverage",
                    metrics.get("element_anchor_coverage"),
                    1.0,
                    "One or more declared scene elements lack a concrete sensory anchor.",
                    "add_missing_anchor",
                    constraints,
                    extra={"missing_anchor_elements": missing[:8]},
                    expected_key="expected_min",
                ))

        if constraints.get("core_anchor_required") is True and metrics.get("core_anchor_present") is not True:
            findings.append(self._specificity_finding(
                contracts,
                "core_anchor_present",
                False,
                True,
                "Scene lacks a dominant concrete anchor.",
                "select_anchor_object",
                constraints,
                expected_key="expected",
            ))

        sensory_min = constraints.get("sensory_modes_min")
        if sensory_min is not None and int(metrics.get("sensory_mode_count") or 0) < int(sensory_min):
            findings.append(self._specificity_finding(
                contracts,
                "sensory_mode_count",
                int(metrics.get("sensory_mode_count") or 0),
                int(sensory_min),
                "Scene uses too few sensory modes for the detail-anchor contract.",
                "diversify_sensory_modes",
                constraints,
                expected_key="expected_min",
            ))

        if constraints.get("abstract_evidence_pairing_required") is True and int(metrics.get("abstract_bare_count") or 0) > 0:
            findings.append(self._specificity_finding(
                contracts,
                "abstract_bare_count",
                int(metrics.get("abstract_bare_count") or 0),
                0,
                "Abstract descriptions appear without nearby concrete or sensory evidence.",
                "pair_abstract_with_evidence",
                constraints,
                evidence_samples=list(metrics.get("abstract_bare_samples") or []),
            ))

        if constraints.get("anchor_must_carry_information") is True and int(metrics.get("decoration_only_detail_count") or 0) > 0:
            findings.append(self._specificity_finding(
                contracts,
                "decoration_only_detail_count",
                int(metrics.get("decoration_only_detail_count") or 0),
                0,
                "Some detail clusters look decorative instead of revealing character, plot, mood, or foreshadowing.",
                "reduce_detail_overload",
                constraints,
            ))

        return findings

    def _scene_finding(
        self,
        contracts: dict[str, dict],
        metric: str,
        actual: Any,
        expected: Any,
        reason: str,
        action: str,
        constraints: dict,
        *,
        target_span: str | None = None,
        expected_behavior: str = "",
        evidence_samples: list[str] | None = None,
    ) -> dict:
        finding = {
            "skill_id": next(iter(contracts.keys()), "scene_structure"),
            "validator": "scene_structure",
            "metric": metric,
            "actual": actual,
            "expected": expected,
            "severity": "high",
            "reason": reason,
            "action": action,
            "retry_policy": constraints.get("retry_policy", {}),
        }
        # 通用修复 G-2：补全 target_span/expected_behavior/evidence_samples
        # 根因：原 _scene_finding 不携带这些字段，LLM 收到空 hint，不知道改哪里、改成什么样。
        if target_span:
            finding["target_span"] = target_span
        if expected_behavior:
            finding["expected_behavior"] = expected_behavior
        if evidence_samples:
            finding["evidence_samples"] = list(evidence_samples)
        return finding

    def _experience_finding(
        self,
        contracts: dict[str, dict],
        metric: str,
        actual: Any,
        expected: Any,
        reason: str,
        constraints: dict,
    ) -> dict:
        # 方案22修复：聚合统计指标（high_advisories/medium_advisories）标记为 advisory_only，不阻断
        advisory_only = is_advisory_only_metric(metric)
        return {
            "skill_id": next(iter(contracts.keys()), "narrative_experience"),
            "validator": "narrative_experience",
            "metric": metric,
            "actual": actual,
            "expected_min": expected,
            "severity": "advisory" if advisory_only else "high",
            "reason": reason,
            "action": self._repair_action(constraints),
            "retry_policy": constraints.get("retry_policy", {}),
            "advisory_only": advisory_only,
        }

    def _pov_finding(
        self,
        contracts: dict[str, dict],
        metric: str,
        actual: Any,
        expected: Any,
        reason: str,
        constraints: dict,
        *,
        evidence: list[dict] | None = None,
    ) -> dict:
        finding = {
            "skill_id": next(iter(contracts.keys()), "pov_consistency"),
            "validator": "pov_consistency",
            "metric": metric,
            "actual": actual,
            "expected": expected,
            "severity": "high",
            "reason": reason,
            "action": self._repair_action(constraints) if metric != "knowledge_boundary_breach" else "fix_pov_leak",
            "retry_policy": constraints.get("retry_policy", {}),
        }
        if evidence:
            finding["target_span"] = str(evidence[0].get("target_span") or "")
            finding["evidence"] = {
                "matches": evidence[:12],
                "evidence_count": len(evidence),
            }
            # 通用修复 H-1：补全 expected_behavior + evidence_samples
            # 根因 H1：原 _pov_finding 携带 target_span 但不携带 expected_behavior，LLM 不知道改成什么样。
            # 根因 H2：原 _pov_finding 只取 evidence[0] 的 target_span，LLM 只修单点，剩余越界点未修复。
            # 修复：expected_behavior 指示如何修复；evidence_samples 携带所有越界点的 target_span。
            if metric == "head_hopping_count":
                finding["expected_behavior"] = (
                    "移除所有非 POV 角色的内心直写，改为可观察的外部行为描写"
                    "（表情、动作、语气）。必须系统性处理 evidence_samples 中列出的所有越界位置，"
                    "禁止只改 target_span 一处。"
                )
                all_spans = [
                    str(item.get("target_span") or "")
                    for item in evidence
                    if item.get("target_span")
                ]
                if all_spans:
                    finding["evidence_samples"] = all_spans[:20]
            elif metric == "single_pov_per_scene":
                finding["expected_behavior"] = (
                    "确保整个场景只有一个 POV 角色的视角，移除所有视角切换标记。"
                )
            elif metric == "knowledge_boundary_breach":
                finding["expected_behavior"] = (
                    "移除 POV 角色不可能知道的信息，改为通过观察、推理或他人告知获得。"
                )
            elif metric == "narrator_commentary_count":
                finding["expected_behavior"] = (
                    "移除叙述者评论，改为通过角色行动和对话呈现。"
                )
        return finding

    def _tense_finding(
        self,
        contracts: dict[str, dict],
        metric: str,
        actual: Any,
        expected: Any,
        reason: str,
        constraints: dict,
        *,
        evidence_samples: list[str] | None = None,
    ) -> dict:
        finding = {
            "skill_id": next(iter(contracts.keys()), "tense_consistency"),
            "validator": "tense_consistency",
            "metric": metric,
            "actual": actual,
            "expected": expected,
            "severity": "medium",
            "reason": reason,
            "action": self._repair_action(constraints),
            "retry_policy": constraints.get("retry_policy", {}),
        }
        # 通用修复（循环 #1）：携带 target_span / expected_behavior / evidence。
        # 根因：原 finding 不携带位置和期望行为，LLM 不知道改哪里、改成什么样。
        # 修复：从 evidence_samples 取第一个作为 target_span，并设置通用 expected_behavior。
        # 通用性：适用于所有题材——闪回闭合和时序一致性规则在所有题材中一致。
        if evidence_samples:
            finding["target_span"] = str(evidence_samples[0])[:60]
            finding["evidence"] = {
                "matches": [{"span": str(s)[:60]} for s in evidence_samples[:5]],
                "evidence_count": len(evidence_samples),
            }
        if metric == "tense_drift_count":
            finding["expected_behavior"] = (
                "为未闭合的叙事闪回补充返回信号（如「眼前」「此刻」「现在」），"
                "或将多句闪回压缩为点缀式一句话回忆；不得删除闪回内容本身。"
            )
        elif metric == "time_jump_without_signal_count":
            finding["expected_behavior"] = (
                "为缺少过渡信号的时间跳跃补充过渡词（如「片刻后」「次日」「不久后」），"
                "使时间线变化对读者可读。"
            )
        elif metric == "flashback_tense_correct":
            finding["expected_behavior"] = (
                "为闪回段落补充明确的返回信号，回到主场景时间锚点。"
            )
        # 通用修复（循环 #6 TA-1）：为 temporal_anchor_count 补 expected_behavior
        # 根因：原 finding 不携带 expected_behavior，LLM 不知道改成什么样。
        # 通用性：所有题材的场景都需要可观察的时间锚点。
        elif metric == "temporal_anchor_count":
            finding["expected_behavior"] = (
                "在正文中补充可观察的时间锚点——通过环境细节（如晨光/暮色/月色/日头偏西）、"
                "角色台词（如「天快亮了」「该用晚饭了」）、或动作细节（如「掌灯时分」「烛火燃尽」）"
                "暗示当前时间，使读者能感知场景发生的时间段。"
            )
        return finding

    def _scene_evidence_finding(
        self,
        contracts: dict[str, dict],
        metric: str,
        actual: Any,
        expected: Any,
        reason: str,
        action: str,
        constraints: dict,
        *,
        target_span: str | None = None,
        expected_behavior: str = "",
        evidence_samples: list[str] | None = None,
    ) -> dict:
        finding = {
            "skill_id": next(iter(contracts.keys()), "scene_evidence"),
            "validator": "scene_evidence",
            "metric": metric,
            "actual": actual,
            "expected": expected,
            "severity": "high",
            "reason": reason,
            "action": action,
            "retry_policy": constraints.get("retry_policy", {}),
        }
        # 通用修复 SA-2：补全 target_span/expected_behavior/evidence_samples
        # 根因：原 _scene_evidence_finding 不携带这些字段，LLM 不知道改哪里、改成什么样。
        if target_span:
            finding["target_span"] = target_span
        if expected_behavior:
            finding["expected_behavior"] = expected_behavior
        if evidence_samples:
            finding["evidence_samples"] = list(evidence_samples)
        return finding

    def _specificity_finding(
        self,
        contracts: dict[str, dict],
        metric: str,
        actual: Any,
        expected: Any,
        reason: str,
        action: str,
        constraints: dict,
        *,
        expected_key: str = "expected_max",
        extra: dict | None = None,
        evidence_samples: list[str] | None = None,
    ) -> dict:
        finding = {
            "skill_id": next(iter(contracts.keys()), "specific_detail_anchor"),
            "validator": "specificity_budget",
            "metric": metric,
            "actual": actual,
            expected_key: expected,
            "severity": "high",
            "reason": reason,
            "action": action,
            "retry_policy": constraints.get("retry_policy", {}),
        }
        if extra:
            finding.update(extra)
        # 通用修复（循环 #1）：携带 target_span / expected_behavior / evidence。
        # 根因：原 finding 无 target_span/expected_behavior，LLM 不知道改哪里、改成什么样。
        # 修复：从 evidence_samples 取第一个作为 target_span，并设置通用 expected_behavior。
        # 通用性：具象化要求在所有题材中一致——抽象句必须搭配感官/动作/对话证据。
        if evidence_samples:
            finding["target_span"] = str(evidence_samples[0])[:60]
            finding["evidence"] = {
                "matches": [{"span": str(s)[:60]} for s in evidence_samples[:5]],
                "evidence_count": len(evidence_samples),
            }
        if metric == "abstract_bare_count":
            finding["expected_behavior"] = (
                "用感官描写（视觉/听觉/触觉/嗅觉/味觉）、动作描写或对话证据 ground 抽象陈述，"
                "不得删除抽象句本身；让抽象词与具体证据共存于同一句或相邻句。"
            )
        return finding

    def _rhythm_finding(
        self,
        contracts: dict[str, dict],
        metric: str,
        actual: Any,
        expected: Any,
        reason: str,
        action: str,
        constraints: dict,
        *,
        expected_key: str = "expected_max",
    ) -> dict:
        # 项目硬约束：AI flavor checkers must not block content generation;
        # enforcement_policy is set to advisory_only_never_block
        advisory_only = is_advisory_only_metric(metric)
        return {
            "skill_id": next(iter(contracts.keys()), "rhythm_control"),
            "validator": "rhythm_metrics",
            "metric": metric,
            "actual": actual,
            expected_key: expected,
            "severity": "advisory" if advisory_only else "high",
            "reason": reason,
            "action": action,
            "retry_policy": constraints.get("retry_policy", {}),
            "advisory_only": advisory_only,
        }

    def _scene_structure_metrics(self, text: str, scene_contract: dict) -> dict:
        text = text or ""
        paragraphs = [p.strip() for p in re.split(r"\n+", text) if p.strip()]
        sentences = [s.strip() for s in re.split(r"[。！？!?；;]+", text) if s.strip()]
        last_text = "\n".join(paragraphs[-2:]) if paragraphs else ""
        goal_terms = self._contract_terms(scene_contract, (
            "goal",
            "objective",
            "scene_goal",
            "protagonist_desire",
            "must_show",
            "hard_must_show",
        ))
        outcome_terms = self._contract_terms(scene_contract, (
            "ending_state",
            "required_outcomes",
            "outcome",
            "value_change",
        ))
        goal_present = bool(self._contains_any(text, goal_terms)) or self._has_intent_signal(text)
        conflict_count = self._count_any(text, _SCENE_CONFLICT_WORDS)
        conflict_present = conflict_count > 0
        hook_present = self._has_hook_signal(last_text)
        value_change = bool(self._contains_any(text, outcome_terms)) or self._has_value_change_signal(text)
        max_exposition = self._max_exposition_block_chars(paragraphs)
        passive_hits = self._count_any(text, _SCENE_PASSIVE_WORDS)
        passive_percentage = passive_hits / max(len(sentences), 1)
        return {
            "length": len(text),
            "paragraph_count": len(paragraphs),
            "sentence_count": len(sentences),
            "goal_present": goal_present,
            "goal_terms_matched": [term for term in goal_terms if term and term in text][:8],
            "conflict_present": conflict_present,
            "conflict_count": conflict_count,
            "value_change": value_change,
            "outcome_terms_matched": [term for term in outcome_terms if term and term in text][:8],
            "hook_present": hook_present,
            "max_exposition_block_chars": max_exposition,
            "passive_percentage": round(passive_percentage, 3),
        }

    def _rhythm_metrics(self, text: str, context: dict) -> dict:
        text = text or ""
        paragraphs = [p.strip() for p in re.split(r"\n+", text) if p.strip()]
        sentences = self._split_readable_sentences(text)
        lengths = [self._sentence_visual_length(sentence) for sentence in sentences]
        mean = sum(lengths) / max(len(lengths), 1)
        variance = sum((length - mean) ** 2 for length in lengths) / max(len(lengths), 1)
        sentence_bands = [self._sentence_length_band(length) for length in lengths]

        paragraph_profiles = [self._paragraph_rhythm_profile(paragraph) for paragraph in paragraphs]
        breathing_count = sum(1 for item in paragraph_profiles if item["kind"] == "breathing")
        dense_flags = [item["kind"] == "dense" for item in paragraph_profiles]
        transition_required = self._rhythm_transition_required(context)
        transition_present = (not transition_required) or any(
            item["has_transition_signal"] for item in paragraph_profiles
        )

        return {
            "sentence_count": len(sentences),
            "paragraph_count": len(paragraphs),
            "sentence_length_mean": round(mean, 3),
            "sentence_length_variance": round(variance, 3),
            "uniform_sentence_streak_max": self._max_equal_streak(sentence_bands),
            "breathing_paragraph_count": breathing_count,
            "breathing_paragraph_ratio": round(breathing_count / max(len(paragraphs), 1), 3),
            "dense_paragraph_streak_max": self._max_true_streak(dense_flags),
            "transition_required": transition_required,
            "transition_paragraph_present": transition_present,
            "abrupt_shift_count": self._abrupt_rhythm_shift_count(
                paragraph_profiles,
                transition_required=transition_required,
            ),
        }

    def _pov_consistency_metrics(
        self,
        text: str,
        scene_contract: dict,
        pov_character_card: dict,
        narrative_config: dict,
        *,
        known_character_names: tuple[str, ...] = (),
    ) -> dict:
        text = text or ""
        pov_name = str(
            scene_contract.get("pov")
            or scene_contract.get("pov_character")
            or pov_character_card.get("name")
            or pov_character_card.get("character_name")
            or ""
        ).strip()
        pov_type = str(
            narrative_config.get("pov_type")
            or scene_contract.get("pov_type")
            or "third_person_limited"
        )
        forbidden_terms = self._contract_terms(scene_contract, (
            "forbidden_knowledge",
            "do_not_reveal_yet",
            "hidden_facts",
            "protected_reveals",
            "unknown_to_pov",
        ))
        forbidden_terms.extend(self._contract_terms(pov_character_card, (
            "forbidden_knowledge",
            "unknown_facts",
            "unknown_to_character",
        )))
        forbidden_terms = list(dict.fromkeys(term for term in forbidden_terms if len(term) >= 2))

        inner_access = analyze_inner_access(
            text,
            pov_name=pov_name,
            known_character_names=known_character_names,
        )
        named_inner_evidence = inner_access["confirmed"]
        ambiguous_inner_evidence = inner_access["ambiguous"]
        forbidden_markers = tuple(
            str(item)
            for item in scene_contract.get("pov_forbidden_inner_markers", [])
            if str(item).strip()
        )
        forbidden_marker_evidence = self._literal_evidence(
            text,
            forbidden_markers,
            "forbidden_inner_marker",
        )
        omniscient_evidence = self._regex_evidence(
            text,
            _POV_OMNISCIENT_PATTERNS,
            "omniscient_marker",
        )
        commentary_evidence = self._regex_evidence(
            text,
            _NARRATOR_COMMENTARY_PATTERNS,
            "narrator_commentary",
        )
        knowledge_evidence = self._literal_evidence(
            text,
            tuple(forbidden_terms),
            "knowledge_boundary",
        )
        head_hopping_evidence = [
            *named_inner_evidence,
            *forbidden_marker_evidence,
            *omniscient_evidence,
        ]
        pov_shift_evidence = self._regex_evidence(
            text,
            (r"\[\[POV:[^\]]+\]\]",),
            "explicit_pov_shift",
        )
        head_hopping_count = len(head_hopping_evidence)
        pov_shift_count = len(pov_shift_evidence)
        single_pov = (
            pov_type in {"omniscient", "third_person_omniscient"}
            or (head_hopping_count == 0 and pov_shift_count <= 1)
        )
        return {
            "pov_character": pov_name,
            "pov_type": pov_type,
            "known_character_name_count": len(known_character_names),
            "single_pov_per_scene": single_pov,
            "head_hopping_count": head_hopping_count,
            "named_inner_access_hits": len(named_inner_evidence),
            "ambiguous_inner_access_hits": len(ambiguous_inner_evidence),
            "omniscient_breach_count": len(omniscient_evidence),
            "pov_shift_count": pov_shift_count,
            "knowledge_boundary_breach": len(knowledge_evidence),
            "forbidden_terms_matched": [term for term in forbidden_terms if term in text][:8],
            "narrator_commentary_count": len(commentary_evidence),
            "head_hopping_evidence": head_hopping_evidence[:12],
            "ambiguous_inner_access_evidence": ambiguous_inner_evidence[:12],
            "pov_shift_evidence": pov_shift_evidence[:12],
            "knowledge_boundary_evidence": knowledge_evidence[:12],
            "narrator_commentary_evidence": commentary_evidence[:12],
        }

    def _temporal_consistency_metrics(
        self,
        text: str,
        scene_contract: dict,
        narrative_config: dict,
    ) -> dict:
        text = text or ""
        flashback_entries = self._count_regex(text, _FLASHBACK_ENTRY_PATTERNS)
        flashback_returns = self._count_regex(text, _FLASHBACK_RETURN_PATTERNS)
        explicit_time_jumps = self._count_regex(text, _TIME_JUMP_PATTERNS)
        textual_transition_signals = self._count_regex(text, _TEMPORAL_TRANSITION_PATTERNS)
        editor_enrichment = scene_contract.get("editor_enrichment")
        if not isinstance(editor_enrichment, dict):
            editor_enrichment = {}
        contract_temporal_anchor = str(
            scene_contract.get("temporal_anchor")
            or editor_enrichment.get("temporal_anchor")
            or ""
        ).strip()
        contract_continuity_anchor = self._is_continuity_temporal_anchor(
            contract_temporal_anchor
        )
        # A scene contract may anchor the scene relative to the previous event
        # instead of repeating an absolute clock phrase in the prose.  Treat
        # that discourse relation as an auditable temporal anchor while still
        # preserving the raw textual count in trace output.
        transition_signals = textual_transition_signals + int(
            contract_continuity_anchor and textual_transition_signals == 0
        )
        drift_hits = self._count_regex(text, _TEMPORAL_DRIFT_PATTERNS)
        unreturned_flashbacks = max(flashback_entries - flashback_returns, 0)
        time_jump_without_signal = max(explicit_time_jumps - transition_signals, 0)
        # 通用修复（循环 #1）：采集 evidence samples，供 finding 携带 target_span。
        # 根因：原 _tense_finding 不携带 target_span/evidence，LLM 不知道改哪里。
        # 修复：采集命中闪回进入词和 drift 模式的句子片段，作为 evidence 传递给修复层。
        flashback_entry_samples = self._collect_pattern_hit_sentences(text, _FLASHBACK_ENTRY_PATTERNS)
        drift_samples = self._collect_pattern_hit_sentences(text, _TEMPORAL_DRIFT_PATTERNS)
        return {
            "base_temporal_anchor": (
                narrative_config.get("base_temporal_anchor")
                or narrative_config.get("base_tense")
                or scene_contract.get("base_temporal_anchor")
                or "main_scene_now"
            ),
            "temporal_anchor_count": transition_signals,
            "textual_temporal_anchor_count": textual_transition_signals,
            "contract_temporal_anchor": contract_temporal_anchor,
            "contract_continuity_anchor": contract_continuity_anchor,
            "temporal_anchor_source": (
                "text"
                if textual_transition_signals
                else "scene_contract_continuity"
                if contract_continuity_anchor
                else "missing"
            ),
            "flashback_entry_count": flashback_entries,
            "flashback_return_count": flashback_returns,
            "flashback_tense_correct": unreturned_flashbacks == 0,
            "time_jump_without_signal_count": time_jump_without_signal,
            "tense_drift_count": drift_hits + unreturned_flashbacks,
            "flashback_entry_samples": flashback_entry_samples[:5],
            "drift_samples": drift_samples[:5],
        }

    @staticmethod
    def _known_character_names(context: dict) -> tuple[str, ...]:
        """Collect character identities already present in the validation context."""
        names: set[str] = set()
        raw_character_names = context.get("character_names") or []
        if isinstance(raw_character_names, str):
            raw_character_names = [raw_character_names]
        for name in raw_character_names:
            value = str(name or "").strip()
            if value:
                names.add(value)
        for card in context.get("character_cards") or []:
            if not isinstance(card, dict):
                continue
            aliases = card.get("aliases") or []
            if isinstance(aliases, str):
                aliases = [aliases]
            for value in [card.get("name"), *aliases]:
                normalized = str(value or "").strip()
                if normalized:
                    names.add(normalized)
        chapter_state = context.get("chapter_state") or {}
        character_states = chapter_state.get("character_states") or {}
        if isinstance(character_states, dict):
            names.update(
                str(name).strip()
                for name in character_states
                if str(name).strip()
            )
        scene_contract = context.get("scene_contract") or {}
        for key in ("pov", "pov_character"):
            value = str(scene_contract.get(key) or "").strip()
            if value:
                names.add(value)
        return tuple(sorted(names, key=lambda value: (-len(value), value)))

    @staticmethod
    def _is_continuity_temporal_anchor(anchor: str) -> bool:
        normalized = re.sub(r"\s+", "", str(anchor or "").lower())
        if not normalized:
            return False
        return any(marker in normalized for marker in (
            "接续上一场景",
            "承接上一场景",
            "紧接上一场景",
            "延续上一场景",
            "接续前一场景",
            "承接前一场景",
            "上一场景结尾",
            "前一场景结尾",
            "同一时间",
            "同一时刻",
            "与此同时",
            "continuousfrompreviousscene",
            "continuationofpreviousscene",
            "same-timecontinuation",
        ))

    def _scene_evidence_metrics(
        self,
        text: str,
        scene_contract: dict,
        pov_character_card: dict,
    ) -> dict:
        text = text or ""
        text_units = max(len(text) / 500, 1)
        sentences = [s.strip() for s in re.split(r"[。！？!?；;\n]+", text) if s.strip()]
        emotion_labels = self._count_any(text, _SHOW_EMOTION_LABELS)
        # 通用修复（循环 #6 EL-1）：收集情绪标签的命中句子样本
        # 根因：原 metrics 只存 count，finding 无 evidence_samples 可携带，LLM 不知道改哪里。
        # 通用性：适用于所有题材——情绪标签在任何题材中都需要定位后改写。
        emotion_label_samples = [
            s for s in sentences
            if s and any(phrase in s for phrase in _SHOW_EMOTION_LABELS)
        ][:10]
        # Phase 0 修正：分离情绪词命中与"关键情绪无证据"。
        # 根因：原 metrics 只统计 emotion_label_samples（所有命中情绪词的句子），
        #   不区分"邻接句已有行为证据"和"邻接句无证据"两种情况。
        # 修复：筛出"命中情绪词且邻接句无具体证据"的子集，作为真正需要修复的违规。
        # 通用性：所有题材中，"他愤怒——拳头攥得发白"这种邻接有证据的不需要修复，
        #   而"他愤怒地离开了"这种邻接无证据的才需要改写。
        key_emotion_no_evidence_samples: list[str] = []
        for idx, sentence in enumerate(sentences):
            if not sentence:
                continue
            if not any(phrase in sentence for phrase in _SHOW_EMOTION_LABELS):
                continue
            # 检查邻接句是否含具体证据
            if not self._sentence_or_neighbors_have_evidence(sentences, idx):
                key_emotion_no_evidence_samples.append(sentence)
        key_emotion_no_evidence_samples = key_emotion_no_evidence_samples[:10]
        thought_verbs = self._count_any(text, _SHOW_THOUGHT_VERBS)
        abstract_claims = self._count_any(text, _SHOW_ABSTRACT_WORDS)
        concrete_hits = self._count_any(text, _SHOW_CONCRETE_WORDS)
        action_hits = self._count_any(text, _SHOW_ACTION_WORDS)
        body_cliches = self._count_any(text, _SHOW_BODY_CLICHES)
        # 通用修复（循环 #5 BL-2）：收集身体语言陈词滥调的命中句子样本
        # 根因：原 metrics 只存 count，finding 无 evidence_samples 可携带，LLM 不知道改哪里。
        # 通用性：适用于所有题材——身体语言陈词滥调在任何题材中都需要定位后改写。
        body_language_cliche_samples = [
            s for s in sentences
            if s and any(phrase in s for phrase in _SHOW_BODY_CLICHES)
        ][:10]
        dialogue_tags = self._count_regex(text, _SHOW_DIALOGUE_EMOTION_TAG_PATTERNS)
        trait_statements = self._count_regex(text, _SHOW_TRAIT_STATEMENT_PATTERNS)
        sense_hits = {
            "visual": self._count_any(text, _SHOW_VISUAL_WORDS),
            "auditory": self._count_any(text, _SHOW_AUDITORY_WORDS),
            "tactile": self._count_any(text, _SHOW_TACTILE_WORDS),
            "smell": self._count_any(text, _SHOW_SMELL_WORDS),
            "taste": self._count_any(text, _SHOW_TASTE_WORDS),
        }
        sense_categories = [key for key, value in sense_hits.items() if value > 0]
        standalone_abstract = self._standalone_abstract_claim_count(sentences)
        # 通用修复 SA-2：收集孤立抽象论断的句子样本，供 finding 携带 evidence_samples
        standalone_abstract_samples = [
            s for s in sentences
            if s and any(word in s for word in _SHOW_ABSTRACT_WORDS)
            and not (any(word in s for word in _SHOW_CONCRETE_WORDS + _SHOW_ACTION_WORDS)
                     or any(word in s for word in (
                         _SHOW_VISUAL_WORDS + _SHOW_AUDITORY_WORDS
                         + _SHOW_TACTILE_WORDS + _SHOW_SMELL_WORDS + _SHOW_TASTE_WORDS
                     ))
                     or "“" in s or '"' in s)
        ][:10]
        evidence_count = concrete_hits + action_hits + sum(sense_hits.values()) + text.count("“") + text.count('"')
        emotion_claim_count = emotion_labels + trait_statements + standalone_abstract
        evidence_ratio = evidence_count / max(emotion_claim_count, 1)
        return {
            "emotion_label_count": emotion_labels,
            "emotion_label_samples": emotion_label_samples,
            "emotion_label_count_per_500": round(emotion_labels / text_units, 2),
            "key_emotion_no_evidence_samples": key_emotion_no_evidence_samples,
            "thought_verb_count": thought_verbs,
            "thought_verb_count_per_500": round(thought_verbs / text_units, 2),
            "abstract_claim_count": abstract_claims,
            "standalone_abstract_claims": standalone_abstract,
            "standalone_abstract_samples": standalone_abstract_samples,
            "trait_statement_count": trait_statements,
            "body_language_cliche_count": body_cliches,
            "body_language_cliche_samples": body_language_cliche_samples,
            "dialogue_emotion_tag_count": dialogue_tags,
            "concrete_evidence_count": evidence_count,
            "evidence_per_emotion_claim": round(evidence_ratio, 2),
            "sense_category_count": len(sense_categories),
            "senses_used": sense_categories,
            "scene_role": scene_contract.get("scene_role") or scene_contract.get("role"),
            "character_signature_sources": list((pov_character_card or {}).keys())[:8],
        }

    def _specificity_budget_metrics(self, text: str, context: dict) -> dict:
        text = text or ""
        scene_contract = context.get("scene_contract") or {}
        scene_elements = self._extract_scene_elements(
            context.get("scene_elements")
            or scene_contract.get("scene_elements")
            or scene_contract.get("key_elements")
            or []
        )
        sentences = self._split_readable_sentences(text)
        sense_hits = self._specificity_sense_hits(text)
        senses_used = [key for key, value in sense_hits.items() if value > 0]
        concrete_hits = self._count_any(text, _DETAIL_CONCRETE_WORDS)
        action_hits = self._count_any(text, _DETAIL_ACTION_WORDS)
        information_hits = self._count_any(text, _DETAIL_INFORMATION_WORDS)
        detail_hit_count = concrete_hits + action_hits + sum(sense_hits.values())
        anchored_elements = []
        missing_elements = []

        for element in scene_elements:
            anchored = self._element_has_specific_anchor(text, element, scene_elements)
            if anchored:
                anchored_elements.append(element)
            else:
                missing_elements.append(element)

        if scene_elements:
            coverage = len(anchored_elements) / max(len(scene_elements), 1)
        else:
            coverage = 1.0 if detail_hit_count > 0 else 0.0

        abstract_sentences = [
            sentence
            for sentence in sentences
            if any(word in sentence for word in _DETAIL_ABSTRACT_WORDS)
        ]
        # Phase 0 修正：抽象证据的前后句判定。
        # 根因：原逻辑只检查同句是否有具体证据，不检查邻接句。
        #   但"他变得更强了。"这句是抽象的，如果前后句有"昨夜他单手扼住了一头荒兽的咽喉"，
        #   那么这个抽象声明是有证据支持的，不应计为 abstract_bare。
        # 修复：检查当前句及前后各 1 句是否含具体证据。
        # 通用性：所有题材的抽象声明都应该看邻接上下文是否有证据支持。
        # P1-8 修正：必须使用原始句子列表的索引，不能用过滤后的 abstract_sentences 索引。
        abstract_bare = []
        for orig_idx, sentence in enumerate(sentences):
            if not any(word in sentence for word in _DETAIL_ABSTRACT_WORDS):
                continue
            if self._sentence_or_neighbors_have_evidence(sentences, orig_idx):
                continue
            abstract_bare.append(sentence)
        decoration_only = self._decoration_only_detail_count(sentences)
        core_anchor_present = bool(
            scene_elements and anchored_elements
        ) or (not scene_elements and detail_hit_count >= 2)
        anchor_loop_closed = self._anchor_loop_closed(text, scene_elements)

        return {
            "scene_element_count": len(scene_elements),
            "scene_elements": scene_elements[:12],
            "anchored_element_count": len(anchored_elements),
            "anchored_elements": anchored_elements[:12],
            "missing_anchor_elements": missing_elements[:12],
            "element_anchor_coverage": round(coverage, 3),
            "core_anchor_present": core_anchor_present,
            "anchor_loop_closed": anchor_loop_closed,
            "sensory_mode_count": len(senses_used),
            "senses_used": senses_used,
            "sense_hits": sense_hits,
            "concrete_detail_hits": concrete_hits,
            "action_detail_hits": action_hits,
            "information_bearing_hits": information_hits,
            "detail_hit_count": detail_hit_count,
            "abstract_sentence_count": len(abstract_sentences),
            "abstract_bare_count": len(abstract_bare),
            "abstract_bare_samples": abstract_bare[:3],
            "decoration_only_detail_count": decoration_only,
        }

    @staticmethod
    def _standalone_abstract_claim_count(sentences: list[str]) -> int:
        count = 0
        for sentence in sentences:
            if not sentence:
                continue
            has_abstract = any(word in sentence for word in _SHOW_ABSTRACT_WORDS)
            has_concrete = any(word in sentence for word in _SHOW_CONCRETE_WORDS + _SHOW_ACTION_WORDS)
            has_sensory = any(
                word in sentence
                for word in (
                    _SHOW_VISUAL_WORDS
                    + _SHOW_AUDITORY_WORDS
                    + _SHOW_TACTILE_WORDS
                    + _SHOW_SMELL_WORDS
                    + _SHOW_TASTE_WORDS
                )
            )
            if has_abstract and not (has_concrete or has_sensory or "“" in sentence or '"' in sentence):
                count += 1
        return count

    @staticmethod
    def _extract_scene_elements(raw: Any) -> list[str]:
        elements: list[str] = []

        def add(value: Any) -> None:
            if value is None:
                return
            if isinstance(value, str):
                for part in re.split(r"[,，、;；\n]+", value):
                    item = part.strip()
                    if item and len(item) <= 40:
                        elements.append(item)
                return
            if isinstance(value, dict):
                for key in ("name", "label", "object", "element", "description", "title"):
                    if value.get(key):
                        add(value.get(key))
                        return
                for item in value.values():
                    add(item)
                return
            if isinstance(value, (list, tuple, set)):
                for item in value:
                    add(item)

        add(raw)
        cleaned = []
        for element in elements:
            element = re.sub(r"\s+", "", str(element))
            if element and element not in cleaned:
                cleaned.append(element)
        return cleaned[:24]

    @staticmethod
    def _specificity_sense_hits(text: str) -> dict[str, int]:
        return {
            "visual": AgentSkillValidator._count_any(text, _DETAIL_VISUAL_WORDS),
            "auditory": AgentSkillValidator._count_any(text, _DETAIL_AUDITORY_WORDS),
            "tactile": AgentSkillValidator._count_any(text, _DETAIL_TACTILE_WORDS),
            "smell": AgentSkillValidator._count_any(text, _DETAIL_SMELL_WORDS),
            "taste": AgentSkillValidator._count_any(text, _DETAIL_TASTE_WORDS),
        }

    @staticmethod
    def _element_has_specific_anchor(
        text: str,
        element: str,
        scene_elements: list[str] | None = None,
    ) -> bool:
        if not element:
            return False
        sentences = AgentSkillValidator._split_readable_sentences(text)
        matching_indexes = [
            index for index, sentence in enumerate(sentences)
            if element in sentence
        ]
        if not matching_indexes:
            return False
        other_elements = [
            item for item in (scene_elements or [])
            if item and item != element
        ]
        for index in matching_indexes:
            candidates = [sentences[index]]
            if index + 1 < len(sentences):
                candidates.append(sentences[index + 1])
            window = "。".join(candidates)
            for item in [element, *other_elements]:
                window = window.replace(item, "")
            if AgentSkillValidator._has_specific_evidence(window):
                return True
        return False

    @staticmethod
    def _has_specific_evidence(text: str) -> bool:
        if not text:
            return False
        concrete = AgentSkillValidator._count_any(text, _DETAIL_CONCRETE_WORDS)
        action = AgentSkillValidator._count_any(text, _DETAIL_ACTION_WORDS)
        sensory = sum(AgentSkillValidator._specificity_sense_hits(text).values())
        # 通用修复（循环 #1）：收紧数字/量词证据判定。
        # 根因：原正则把"一/层/道/种/圈/条/块/片/枚/滴/缕"等抽象量化词当具体证据，
        #   导致"一种感觉""一层关系""一道目光"这种伪量化表达蒙混过关。
        # 修复：只保留真正的具体度量词（二/三/半/寸/尺）和物理痕迹词（裂/纹/痕），
        #   以及数字 \d+。排除"一/层/道/种/圈/条/块/片/枚/滴/缕"等抽象量化词。
        # 通用性：在所有题材中，"一种感觉""一层关系"都是抽象表达，不应计为具体证据。
        numbers_or_shapes = len(re.findall(r"\d+|二|三|半|寸|尺|裂|纹|痕", text))
        return (concrete + action + sensory + numbers_or_shapes) > 0

    @staticmethod
    def _sentence_or_neighbors_have_evidence(
        sentences: list[str], idx: int, window: int = 1
    ) -> bool:
        """检查当前句及前后各 window 句是否含具体证据。

        Phase 0 修正：抽象证据的前后句判定。
        原逻辑只检查同句，导致"他变得更强了——昨夜他单手扼住了一头荒兽的咽喉"
        这种邻接句有证据的情况被误判为 abstract_bare。
        """
        if not sentences:
            return False
        start = max(0, idx - window)
        end = min(len(sentences), idx + window + 1)
        for i in range(start, end):
            sentence = sentences[i]
            if i == idx and AgentSkillValidator._has_specific_evidence(sentence):
                return True
            # 邻句只有“桌子/房间”等具体名词不能证明当前抽象断言；
            # 必须有动作、感官或可量化物理痕迹，才能作为跨句证据。
            if i != idx and AgentSkillValidator._has_neighbor_specific_evidence(sentence):
                return True
        return False

    @staticmethod
    def _has_neighbor_specific_evidence(text: str) -> bool:
        if not text:
            return False
        action = AgentSkillValidator._count_any(text, _DETAIL_ACTION_WORDS)
        sensory = sum(AgentSkillValidator._specificity_sense_hits(text).values())
        physical_trace = len(re.findall(r"\d+|二|三|半|寸|尺|裂|纹|痕", text))
        return (action + sensory + physical_trace) > 0

    @staticmethod
    def _decoration_only_detail_count(sentences: list[str]) -> int:
        count = 0
        for sentence in sentences:
            has_decorative = any(word in sentence for word in _DETAIL_DECORATIVE_WORDS)
            has_information = any(word in sentence for word in _DETAIL_INFORMATION_WORDS)
            has_action = any(word in sentence for word in _DETAIL_ACTION_WORDS)
            has_change = bool(re.search(r"(裂|旧|磨|补|掉|缺|锈|湿|冷|烫|血|灰|尘|停|抖|颤|压|藏|露)", sentence))
            if has_decorative and not (has_information or has_action or has_change):
                count += 1
        return count

    @staticmethod
    def _anchor_loop_closed(text: str, elements: list[str]) -> bool:
        paragraphs = [p.strip() for p in re.split(r"\n+", text or "") if p.strip()]
        if not paragraphs:
            return False
        head = "\n".join(paragraphs[:2])
        tail = "\n".join(paragraphs[-2:])
        for element in elements[:8]:
            if element and element in head and element in tail:
                return True
        return False

    @staticmethod
    def _split_readable_sentences(text: str) -> list[str]:
        return [
            sentence.strip()
            for sentence in re.split(r"[。！？!?；;…\n]+", text or "")
            if sentence.strip()
        ]

    @staticmethod
    def _sentence_visual_length(sentence: str) -> int:
        compact = re.sub(r"[\s，、,.：:；;“”\"'‘’（）()\[\]《》<>]+", "", sentence or "")
        return len(compact)

    @staticmethod
    def _sentence_length_band(length: int) -> str:
        if length <= 5:
            return "micro"
        if length <= 15:
            return "short"
        if length <= 30:
            return "medium"
        if length <= 50:
            return "long"
        return "extra_long"

    @staticmethod
    def _max_equal_streak(values: list[str]) -> int:
        max_streak = 0
        current = 0
        previous = None
        for value in values:
            current = current + 1 if value == previous else 1
            previous = value
            max_streak = max(max_streak, current)
        return max_streak

    @staticmethod
    def _max_true_streak(values: list[bool]) -> int:
        max_streak = 0
        current = 0
        for value in values:
            current = current + 1 if value else 0
            max_streak = max(max_streak, current)
        return max_streak

    @staticmethod
    def _paragraph_rhythm_profile(paragraph: str) -> dict:
        text = paragraph or ""
        action_hits = len(re.findall(r"[推抓按砸撞拔冲退躲闪落断裂杀斩劈踢扑逃追响喊吼]", text))
        dialogue_hits = text.count("“") + text.count('"')
        conflict_hits = len(re.findall(r"(不能|必须|危险|威胁|代价|血|刀|死|破|逃|追|拦|阻)", text))
        transition_hits = len(re.findall(r"(片刻后|过了一会儿|与此同时|直到|随后|刚才|这时|那一刻|风停|声音落下|他停下|她停下)", text))
        sensory_hits = len(re.findall(r"(风|光|影|雨|雪|冷|热|疼|痛|湿|尘|香|腥|血味|脚步|声音|茶|灯|门|窗)", text))
        sentence_count = max(len(AgentSkillValidator._split_readable_sentences(text)), 1)
        density = action_hits + dialogue_hits + conflict_hits
        kind = "neutral"
        if density >= 3 or (len(text) <= 80 and density >= 1):
            kind = "dense"
        elif len(text) >= 35 and density <= 1 and (sensory_hits > 0 or sentence_count >= 2):
            kind = "breathing"
        return {
            "kind": kind,
            "density": density,
            "has_transition_signal": transition_hits > 0,
            "length": len(text),
        }

    @staticmethod
    def _rhythm_transition_required(context: dict) -> bool:
        scene_contract = context.get("scene_contract") or {}
        if bool(scene_contract.get("rhythm_shift_required")):
            return True
        previous = (
            context.get("previous_scene_rhythm")
            or scene_contract.get("previous_scene_rhythm")
            or ""
        )
        current = (
            scene_contract.get("rhythm")
            or scene_contract.get("pacing")
            or scene_contract.get("scene_rhythm")
            or ""
        )
        return bool(previous and current and str(previous) != str(current))

    @staticmethod
    def _abrupt_rhythm_shift_count(
        paragraph_profiles: list[dict],
        *,
        transition_required: bool = False,
    ) -> int:
        if not transition_required:
            return 0
        abrupt = 0
        previous: dict | None = None
        for profile in paragraph_profiles:
            kind = profile.get("kind")
            previous_kind = previous.get("kind") if previous else None
            density_gap = abs(int(profile.get("density") or 0) - int((previous or {}).get("density") or 0))
            bridge_visible = bool(profile.get("has_transition_signal") or (previous or {}).get("has_transition_signal"))
            substantial_shift = (
                {previous_kind, kind} == {"dense", "breathing"}
                and density_gap >= 2
                and int(profile.get("length") or 0) >= 35
                and int((previous or {}).get("length") or 0) >= 35
            )
            if substantial_shift and not bridge_visible:
                abrupt += 1
            previous = profile
        return abrupt

    @staticmethod
    def _contract_terms(scene_contract: dict, keys: tuple[str, ...]) -> list[str]:
        terms: list[str] = []
        stack = [scene_contract]
        while stack:
            value = stack.pop()
            if isinstance(value, dict):
                for key, item in value.items():
                    if key in keys:
                        if isinstance(item, str) and item.strip():
                            terms.append(item.strip())
                        elif isinstance(item, list):
                            terms.extend(str(part).strip() for part in item if str(part).strip())
                    if isinstance(item, (dict, list)):
                        stack.append(item)
            elif isinstance(value, list):
                stack.extend(item for item in value if isinstance(item, (dict, list)))
        return list(dict.fromkeys(terms))

    @staticmethod
    def _contains_any(text: str, terms: list[str]) -> bool:
        return any(term and term in text for term in terms)

    @staticmethod
    def _count_any(text: str, words: tuple[str, ...]) -> int:
        return sum(text.count(word) for word in words)

    @staticmethod
    def _count_regex(text: str, patterns: tuple[str, ...]) -> int:
        return sum(len(re.findall(pattern, text or "")) for pattern in patterns)

    @staticmethod
    def _collect_pattern_hit_sentences(text: str, patterns: tuple[str, ...]) -> list[str]:
        """通用修复（循环 #1）：采集命中正则的句子片段，供 finding 携带 evidence。

        通用性：适用于所有题材——按句号/问号/感叹号/分号切句，返回命中 pattern
        的完整句子（截断到前 60 字），供修复层定位违规位置。
        """
        if not text or not patterns:
            return []
        sentences = re.split(r"[。！？!?；;\n]+", text)
        hits: list[str] = []
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
            if any(re.search(pattern, sentence) for pattern in patterns):
                snippet = sentence[:60]
                if snippet and snippet not in hits:
                    hits.append(snippet)
        return hits

    @staticmethod
    def _named_inner_access_hits(text: str, pov_name: str) -> int:
        return len(analyze_inner_access(text, pov_name=pov_name)["confirmed"])

    @staticmethod
    def _literal_evidence(text: str, terms: tuple[str, ...], evidence_type: str) -> list[dict]:
        evidence: list[dict] = []
        source = text or ""
        for term in terms:
            if not term:
                continue
            start = 0
            while True:
                index = source.find(term, start)
                if index < 0:
                    break
                end = index + len(term)
                evidence.append({
                    "type": evidence_type,
                    "target_span": term,
                    "start": index,
                    "end": end,
                    "snippet": source[max(0, index - 24):min(len(source), end + 24)],
                })
                start = end
        return evidence

    @staticmethod
    def _regex_evidence(text: str, patterns: tuple[str, ...], evidence_type: str) -> list[dict]:
        evidence: list[dict] = []
        source = text or ""
        for pattern in patterns:
            for match in re.finditer(pattern, source):
                evidence.append({
                    "type": evidence_type,
                    "target_span": match.group(0),
                    "start": match.start(),
                    "end": match.end(),
                    "snippet": source[
                        max(0, match.start() - 24):min(len(source), match.end() + 24)
                    ],
                })
        return evidence

    @staticmethod
    def _has_intent_signal(text: str) -> bool:
        return bool(re.search(r"(想要|必须|要去|试图|决定|打算|不能|只剩|为了|得先)", text or ""))

    @staticmethod
    def _has_value_change_signal(text: str) -> bool:
        return bool(re.search(r"(终于|变成|不再|已经|失去|得到|发现|明白|暴露|逃出|坠入|醒来|破裂|碎裂)", text or ""))

    @staticmethod
    def _has_hook_signal(text: str) -> bool:
        return bool(re.search(r"(？|\\?|忽然|突然|传来|停在|靠近|看见|发现|只剩|不能|没有|是谁|什么|下一刻)", text or ""))

    @staticmethod
    def _max_exposition_block_chars(paragraphs: list[str]) -> int:
        max_chars = 0
        for paragraph in paragraphs:
            if len(paragraph) < 80:
                continue
            action_marks = len(re.findall(r"(说|问|喊|走|退|抓|按|看|听|抬|落|砸|撞|握|拔|伸)", paragraph))
            dialogue_marks = paragraph.count("“") + paragraph.count('"')
            if action_marks <= 1 and dialogue_marks == 0:
                max_chars = max(max_chars, len(paragraph))
        return max_chars

    def _literary_finding(
        self,
        contracts: dict[str, dict],
        metric: str,
        actual: Any,
        expected: Any,
        reason: str,
        constraints: dict,
    ) -> dict:
        # 方案22修复：聚合统计指标（high_advisories/medium_advisories）是 advisory 违规的计数，
        # 底层 advisory 违规已被 advisory_only_never_block 策略标记为不阻断。
        # 聚合计数也不应该阻断最终验收，否则会导致"底层不阻断但聚合计数阻断"的矛盾。
        # 聚合指标仍然记录在 findings 中供观测，但不影响 passed（advisory_only=True）。
        advisory_only = is_advisory_only_metric(metric)
        return {
            "skill_id": next(iter(contracts.keys()), "literary_quality"),
            "validator": "literary_quality",
            # 方案 7 Part E：统一 metric 命名为 literary_quality_advisory，
            # 避免与 QualityGate 的 LLM literary_quality 判断同名不同实现；
            # 原始 metric（high_advisories / medium_advisories / mode_fit）保留在 original_metric。
            "metric": "literary_quality_advisory",
            "original_metric": metric,
            "actual": actual,
            "expected_max": expected,
            "severity": "advisory" if advisory_only else "high",
            "reason": reason,
            "action": self._repair_action(constraints),
            "retry_policy": constraints.get("retry_policy", {}),
            # 标注来源：确定性 metrics（scores），LLM 判断由 QualityGate 负责
            "source_implementation": "deterministic_metrics",
            "delegated_to": "quality_gate_llm",
            "advisory_only": advisory_only,
        }

    @staticmethod
    def _resource_packs_for_contracts(
        packet: CompiledSkillPacket,
        contracts: dict[str, dict],
    ) -> dict[str, dict]:
        packs: dict[str, dict] = {}
        for skill_id in contracts:
            for pack_name, content in packet.resource_packs.get(skill_id, {}).items():
                if isinstance(content, dict):
                    packs[f"{skill_id}:{pack_name}"] = content
        return packs

    def _max_metric_findings(
        self,
        *,
        validator_id: str,
        contracts: dict[str, dict],
        constraints: dict,
        metrics: dict,
        checks: tuple[tuple[str, str, str], ...],
    ) -> list[dict]:
        findings: list[dict] = []
        for contract_key, metric_key, action in checks:
            if contract_key not in constraints:
                continue
            actual = float(metrics.get(metric_key) or 0)
            expected = float(constraints.get(contract_key) or 0)
            if actual <= expected:
                continue
            if actual.is_integer():
                actual = int(actual)
            if expected.is_integer():
                expected = int(expected)
            findings.append(self._validator_finding(
                validator_id=validator_id,
                contracts=contracts,
                metric=metric_key,
                actual=actual,
                expected=expected,
                action=action,
                constraints=constraints,
                reason=f"{metric_key} exceeds the {validator_id} skill contract.",
            ))
        return findings

    @staticmethod
    def _validator_finding(
        *,
        validator_id: str,
        contracts: dict[str, dict],
        metric: str,
        actual: Any,
        expected: Any,
        action: str,
        constraints: dict,
        reason: str,
    ) -> dict:
        # 项目硬约束：AI flavor checkers must not block content generation;
        # enforcement_policy is set to advisory_only_never_block
        advisory_only = is_advisory_only_metric(metric)
        return {
            "skill_id": next(iter(contracts.keys()), validator_id),
            "validator": validator_id,
            "metric": metric,
            "actual": actual,
            "expected_max": expected,
            "severity": "advisory" if advisory_only else "high",
            "reason": reason,
            "action": action,
            "retry_policy": constraints.get("retry_policy", {}),
            "advisory_only": advisory_only,
        }

    def _unavailable_validator_result(
        self,
        validator_id: str,
        contracts: dict[str, dict],
        constraints: dict,
        packet: CompiledSkillPacket,
        exc: Exception,
    ) -> dict:
        return {
            "validator": validator_id,
            "passed": False,
            "status": "unavailable",
            "constraints": constraints,
            "findings": [self._validator_finding(
                validator_id=validator_id,
                contracts=contracts,
                metric="validator_available",
                actual=False,
                expected=True,
                action=self._repair_action(constraints),
                constraints=constraints,
                reason=str(exc),
            )],
            "repair_hooks": packet.repair_hooks.get(validator_id, []),
        }

    @staticmethod
    def _merge_constraints(contracts: dict[str, dict]) -> dict:
        merged: dict[str, Any] = {}
        for constraint in contracts.values():
            if not isinstance(constraint, dict):
                continue
            for key, value in constraint.items():
                if key == "retry_policy" and isinstance(value, dict):
                    merged.setdefault("retry_policy", {}).update(value)
                elif key.startswith("max_") or key.endswith("_max"):
                    existing = merged.get(key)
                    merged[key] = value if existing is None else min(existing, value)
                else:
                    merged[key] = value
        return merged

    def _ai_flavor_constraint_findings(
        self,
        *,
        contracts: dict[str, dict],
        constraints: dict,
        metrics: dict,
        advisories: list[dict],
        text: str,
    ) -> list[dict]:
        findings: list[dict] = []
        text_units = max(len(text) / 1000, 1)
        dash_hits = int(
            metrics.get("dash_artifact_hits")
            or metrics.get("emdash_pair_hits")
            or count_dash_artifacts(text)
            or 0
        )
        dash_density = dash_hits / text_units
        dash_max = constraints.get("dash_per_1000_max")
        if dash_max is not None and dash_density > float(dash_max):
            findings.append(self._finding(
                contracts,
                "dash_per_1000",
                round(dash_density, 2),
                float(dash_max),
                "Dash density exceeds the skill contract.",
                constraints,
            ))

        max_checks = (
            ("tier1_hit_count", "tier1_hit_count", "tier1_replace", "Tier 1 AI-flavor vocabulary exceeds the skill contract."),
            ("tier2_cluster_count", "tier2_cluster_count", "tier2_decluster", "Tier 2 visual/abstract vocabulary clusters exceed the skill contract."),
            ("three_part_escalation_count", "three_part_escalation_hits", "break_three_part", "Three-part escalation patterns exceed the skill contract."),
            ("negative_parallel_count", "negative_parallel_hits", "break_negative_parallel", "Negative parallel sentence patterns exceed the skill contract."),
            ("false_range_count", "false_range_hits", "break_false_range", "False range structures exceed the skill contract."),
            ("vague_attribution_count", "vague_attribution_hits", "replace_vague_attribution", "Vague attribution exceeds the skill contract."),
            ("filler_phrase_count", "filler_phrase_hits", "remove_filler", "Filler phrases exceed the skill contract."),
            ("promotional_language_count", "promotional_language_hits", "remove_promotional", "Promotional language exceeds the skill contract."),
            ("inflated_significance_count", "inflated_significance_hits", "tone_down_inflated", "Inflated significance language exceeds the skill contract."),
            ("structure_word_cluster_count", "structure_word_cluster_count", "break_three_part", "Structure-word clusters exceed the skill contract."),
            ("chapter_end_moral_count", "summary_ending_hits", "remove_chapter_end_moral", "Chapter-ending moral or prophecy phrases exceed the skill contract."),
            ("overexplain_count", "overexplain_hits", "remove_overexplain", "Overexplanation phrases exceed the skill contract."),
            ("visual_verb_cluster_max", "visual_verb_cluster_count", "tier2_decluster", "Visual-verb clusters exceed the skill contract."),
            ("abstract_noun_cluster_max", "abstract_noun_cluster_count", "tier2_decluster", "Abstract-noun clusters exceed the skill contract."),
            ("consecutive_dash_max", "max_consecutive_emdash_pairs", "reduce_dash", "Consecutive dash usage exceeds the skill contract."),
        )
        for contract_key, metric_key, action, reason in max_checks:
            if contract_key not in constraints:
                continue
            actual = int(metrics.get(metric_key) or 0)
            expected = int(constraints.get(contract_key) or 0)
            if actual > expected:
                findings.append(self._ai_pattern_finding(
                    contracts,
                    contract_key,
                    actual,
                    expected,
                    reason,
                    action,
                    constraints,
                ))

        density_max = constraints.get("tier3_density_max")
        if density_max is not None and float(metrics.get("tier3_density") or 0) > float(density_max):
            findings.append(self._ai_pattern_finding(
                contracts,
                "tier3_density",
                round(float(metrics.get("tier3_density") or 0), 4),
                float(density_max),
                "Tier 3 low-information vocabulary density exceeds the skill contract.",
                "tier3_thin",
                constraints,
            ))

        variance_min = constraints.get("sentence_length_variance_min")
        if variance_min is not None and float(metrics.get("sentence_length_variance") or 0) < float(variance_min):
            findings.append(self._ai_pattern_finding(
                contracts,
                "sentence_length_variance",
                round(float(metrics.get("sentence_length_variance") or 0), 3),
                float(variance_min),
                "Sentence rhythm is too uniform for the anti-AI-prose contract.",
                "vary_sentence_length",
                constraints,
            ))

        cv_min = constraints.get("sentence_length_cv_min")
        if cv_min is not None and float(metrics.get("sentence_length_cv") or 0) < float(cv_min):
            findings.append(self._ai_pattern_finding(
                contracts,
                "sentence_length_cv",
                round(float(metrics.get("sentence_length_cv") or 0), 3),
                float(cv_min),
                "Sentence-length burstiness is below the anti-AI-prose contract.",
                "vary_sentence_length",
                constraints,
            ))

        high_count = sum(1 for item in advisories if item.get("severity") == "high")
        high_max = constraints.get("max_high_advisories")
        if high_max is not None and high_count > int(high_max):
            findings.append(self._finding(
                contracts,
                "high_advisories",
                high_count,
                int(high_max),
                "High-severity AI-flavor advisories exceed the skill contract.",
                constraints,
            ))

        medium_count = sum(1 for item in advisories if item.get("severity") == "medium")
        medium_max = constraints.get("max_medium_advisories")
        if medium_max is not None and medium_count > int(medium_max):
            findings.append(self._finding(
                contracts,
                "medium_advisories",
                medium_count,
                int(medium_max),
                "Medium-severity AI-flavor advisories exceed the skill contract.",
                constraints,
            ))

        return findings

    def _ai_pattern_finding(
        self,
        contracts: dict[str, dict],
        metric: str,
        actual: Any,
        expected: Any,
        reason: str,
        action: str,
        constraints: dict,
    ) -> dict:
        # 项目硬约束：AI flavor checkers must not block content generation;
        # enforcement_policy is set to advisory_only_never_block
        advisory_only = is_advisory_only_metric(metric)
        return {
            "skill_id": next(iter(contracts.keys()), "anti_ai_prose"),
            "validator": "ai_flavor",
            "metric": metric,
            "actual": actual,
            "expected_max": expected,
            "severity": "advisory" if advisory_only else "high",
            "reason": reason,
            "action": action,
            "retry_policy": constraints.get("retry_policy", {}),
            "advisory_only": advisory_only,
        }

    def _finding(
        self,
        contracts: dict[str, dict],
        metric: str,
        actual: Any,
        expected: Any,
        reason: str,
        constraints: dict,
    ) -> dict:
        # 方案22修复：聚合统计指标（high_advisories/medium_advisories）标记为 advisory_only，不阻断
        advisory_only = is_advisory_only_metric(metric)
        return {
            "skill_id": next(iter(contracts.keys()), "unknown"),
            "validator": "ai_flavor",
            "metric": metric,
            "actual": actual,
            "expected_max": expected,
            "severity": "advisory" if advisory_only else "high",
            "reason": reason,
            "action": self._repair_action(constraints),
            "retry_policy": constraints.get("retry_policy", {}),
            "advisory_only": advisory_only,
        }

    @staticmethod
    def _repair_action(constraints: dict) -> str:
        retry_policy = constraints.get("retry_policy") if isinstance(constraints, dict) else {}
        if isinstance(retry_policy, dict):
            return str(retry_policy.get("action") or "repair")
        return "repair"


_SCENE_CONFLICT_WORDS = (
    "挡",
    "阻",
    "逼",
    "冲",
    "撞",
    "断",
    "裂",
    "敌",
    "伤",
    "血",
    "危险",
    "威胁",
    "代价",
    "不能",
    "必须",
    "追",
    "逃",
    "压",
    "震",
)

_SCENE_PASSIVE_WORDS = (
    "想到",
    "觉得",
    "意识到",
    "回忆",
    "据说",
    "传闻",
    "事实上",
    "意味着",
    "说明",
    "代表",
    "象征",
    "仿佛",
)


_POV_OMNISCIENT_PATTERNS = (
    r"无人知道",
    r"谁也不知道",
    r"(?m)(?:^|[。！？!?；;\n])\s*与此同时[，,].{0,12}在",
    r"(?m)(?:^|[。！？!?；;\n])\s*另一边[，,]",
)

_NARRATOR_COMMENTARY_PATTERNS = (
    r"读者(?:应该|可以|会)",
    r"这(?:意味着|说明|象征|预示)",
    r"命运(?:已经|正在|从不|不会)",
    r"故事(?:由此|将在|就此)",
)

# 通用修复（循环 #1）：收紧闪回进入词表，区分"叙事级闪回"与"点缀式回忆"。
# 根因：原词表含"记得/想起/回忆"等中文叙事超高频词，这些词在对白和心理活动中
#   极常见（如"我记得他说过"、"她想起一件事"），并非叙事闪回。原逻辑
#   unreturned_flashbacks = max(进入-返回, 0) 几乎必然 >0，导致正常叙述被误判。
# 修复：只对"叙事级闪回"信号计数——必须带明显时间跳跃标记：
#   - "当年/多年前/X年前"：明确表示过去时段，叙事闪回信号
#   - "回忆起/回忆到"：带趋向补语，表示进入回忆叙事
#   - "想起那/想起曾/想起当年/想起多年前"：接过去时间标记，叙事闪回
#   - "记得那/记得曾/记得当年/记得多年前"：接过去时间标记，叙事闪回
#   - "那一年/那一年"/"那天的"/"那夜"/"那时节"：明确过去时间点
# 排除单独使用的"记得/想起/回忆/那时"，这些是点缀式回忆，不需返回信号。
# 通用性：在所有题材中，"我记得他说过"都是点缀式回忆而非叙事闪回。
_FLASHBACK_ENTRY_PATTERNS = (
    r"当年",
    r"多年前",
    r"[一二三四五六七八九十\d]+年前",
    r"回忆起",
    r"回忆到",
    r"想起那",
    r"想起曾",
    r"想起当年",
    r"想起多年前",
    r"记得那",
    r"记得曾",
    r"记得当年",
    r"记得多年前",
    r"那一年",
    r"那天",
    r"那夜",
    r"那时节",
)

_FLASHBACK_RETURN_PATTERNS = (
    r"回到眼前",
    r"拉回(?:眼前|现实)",
    r"醒过神",
    r"眼前",
    r"此刻",
    r"现在",
    r"声音把.{0,12}拉回",
)

_TIME_JUMP_PATTERNS = (
    r"[一二三四五六七八九十\d]+(?:天|日|月|年|个时辰|刻钟|分钟|小时)后",
    r"片刻后",
    r"不久后",
    r"转眼",
    r"与此同时",
    r"下一刻",
    r"随后",
    r"后来",
)

_TEMPORAL_TRANSITION_PATTERNS = (
    # 相对时间过渡
    r"[一二三四五六七八九十\d]+(?:天|日|月|年|个时辰|刻钟|分钟|小时)后",
    # 局部时长同样能建立场景内时间关系，避免连续场景
    # 为满足词表而重复声明晨昏。
    r"[一二三四五六七八九十两半\d]+(?:息|刻钟?|盏茶|炷香|呼吸|顷刻)(?:的?工夫|的?功夫|后|前|之内)?",
    r"片刻后",
    r"不久后",
    r"转眼",
    r"与此同时",
    r"下一刻",
    r"随后",
    r"后来",
    r"当时",
    r"此刻",
    r"眼前",
    r"刚才",
    r"那一刻",
    # 指示性时间锚点（与 review_minister token_map 对齐，修复配置不一致）
    r"此时",
    r"这时",
    r"那时",
    r"不多时",
    r"不多久",
    r"顷刻",
    r"半晌",
    # 绝对时段锚点（与 review_minister token_map 对齐）
    r"清晨",
    r"早晨",
    r"上午",
    r"正午",
    r"午后",
    r"下午",
    r"黄昏",
    r"夜里",
    r"天色",
    r"日头",
    # 扩展绝对时间锚点（token_map 未覆盖但常见）
    r"黎明",
    r"拂晓",
    r"日出",
    r"日落",
    r"傍晚",
    r"深夜",
    r"午夜",
    r"子夜",
    r"半夜",
    r"入夜",
    r"次日",
    r"翌日",
    # 古代时辰（题材适配）
    r"辰时",
    r"巳时",
    r"午时",
    r"未时",
    r"申时",
    r"酉时",
    r"戌时",
    r"亥时",
    r"子时",
    r"丑时",
    r"寅时",
    r"卯时",
)

_TEMPORAL_DRIFT_PATTERNS = (
    r"现在(?:正|正在).{0,12}(?:站|走|看|听|推|握|跑)",
    r"刚才.{0,12}(?:将会|会在|正在)",
)

_SHOW_EMOTION_LABELS = (
    "愤怒",
    "生气",
    "悲伤",
    "难过",
    "痛苦",
    "恐惧",
    "害怕",
    "震惊",
    "绝望",
    "开心",
    "高兴",
    "复杂",
    "不安",
    "紧张",
    "焦虑",
    "羞愧",
    "后悔",
    "委屈",
    "孤独",
)

_SHOW_THOUGHT_VERBS = (
    "想到",
    "想起",
    "觉得",
    "认为",
    "知道",
    "明白",
    "意识到",
    "记得",
    "相信",
    "怀疑",
    "希望",
    "想要",
    "感觉到",
    "感到",
)

_SHOW_ABSTRACT_WORDS = (
    "意义",
    "命运",
    "本质",
    "灵魂",
    "复杂",
    "关系",
    "气氛",
    "局势",
    "压抑",
    "尴尬",
    "重要",
    "特殊",
    "象征",
    "代表",
    "体现",
    "说明",
    "意味着",
    "预示",
)

_SHOW_CONCRETE_WORDS = (
    # 家具/建筑
    "门", "窗", "桌", "椅", "床", "柜", "墙", "地面", "台阶", "柱子",
    "屋顶", "门槛", "帘子", "屏风", "梁", "砖",
    # 器物/工具
    "杯", "碗", "碟", "壶", "瓶", "纸", "灯", "烛", "镜", "梳",
    "针", "线", "绳", "锁", "钥匙", "刀", "剑", "鞘", "弓", "箭",
    "笔", "墨", "砚", "卷轴", "书", "册", "牌",
    # 衣物/身体部位
    "衣角", "袖口", "领口", "裙摆", "腰带", "鞋底", "靴", "帽", "巾",
    "指节", "掌心", "指尖", "手腕", "肩膀", "膝盖", "脚踝", "脖颈", "脊背",
    "眼角", "嘴唇", "齿", "舌",
    # 自然/材质
    "灰尘", "雨水", "露水", "雾", "霜", "雪", "冰",
    "火光", "烟", "灰", "炭", "烛火",
    "影子", "光", "影",
    "木", "竹", "铁", "铜", "布", "丝", "麻", "棉", "皮",
    "石", "土", "泥", "沙",
    # 感官物
    "血", "泪", "汗", "水",
    "花", "叶", "枝", "根", "草", "苔",
    # 食物/药物
    "茶", "酒", "药", "米", "面", "饼", "肉",
    # 交通/场所
    "车", "船", "马", "桥", "路", "巷", "院",
)

_SHOW_ACTION_WORDS = (
    "推",
    "拉",
    "放下",
    "抬起",
    "按住",
    "攥住",
    "松开",
    "退后",
    "停下",
    "砸",
    "摔",
    "捡起",
    "递给",
    "转身",
    "避开",
    "关上",
    "打开",
)

# 通用修复（循环 #5 BL-1）：扩展身体语言陈词滥调词表
# 根因：原词表仅 13 个词，覆盖不足，AI 生成的身体语言陈词滥调远不止这些。
#   扩展为通用词表，覆盖所有题材中 AI 高频使用的模板化身体反应。
# 通用性：这些短语在玄幻/都市/言情/悬疑/科幻/历史等所有题材中均为 AI 模板化表达。
_SHOW_BODY_CLICHES = (
    # 拳/手部
    "握紧拳头", "攥紧拳头", "攥紧双拳", "握紧双拳",
    # 唇/齿
    "咬住嘴唇", "咬唇", "咬紧牙关", "咬牙",
    # 眉
    "皱眉", "蹙眉", "眉头紧锁", "眉头一皱", "眉头舒展",
    # 叹气
    "叹气", "叹了口气", "长叹一声", "长叹",
    # 心跳/呼吸
    "心跳加速", "心跳骤然加速", "心跳漏了一拍",
    "呼吸急促", "呼吸一滞", "呼吸一紧",
    # 眼神
    "眼中闪过", "眼中掠过", "眼底闪过", "眼神一凝", "目光一沉",
    # 嘴角
    "嘴角上扬", "嘴角微扬", "嘴角抽搐", "嘴角勾起",
    # 笑
    "苦笑", "冷笑", "轻笑", "淡笑", "讥笑",
    # 身体僵/抖
    "身体一僵", "身子一僵", "动作一顿", "浑身发抖", "身体颤抖",
    # 瞳孔
    "瞳孔一缩", "瞳孔骤缩", "瞳孔微缩",
    # 喉结/指节/脸色
    "喉结滚动", "指尖发白", "指节泛白",
    "脸色一变", "脸色苍白", "脸色铁青",
    # 心头
    "心头一紧", "心中一凛", "心头一凛",
)

_SHOW_VISUAL_WORDS = (
    "看见",
    "望见",
    "颜色",
    "影子",
    "光",
    "暗",
    "亮",
    "红",
    "白",
    "黑",
    "灰",
)

_SHOW_AUDITORY_WORDS = (
    "听见",
    "响",
    "声音",
    "脚步",
    "敲",
    "喊",
    "低声",
    "铃",
    "风声",
)

_SHOW_TACTILE_WORDS = (
    "冷",
    "热",
    "疼",
    "痛",
    "麻",
    "湿",
    "干",
    "粗糙",
    "刺",
    "沉",
)

_SHOW_SMELL_WORDS = (
    "气味",
    "味道",
    "腥",
    "焦",
    "香",
    "霉",
    "烟味",
    "血腥",
)

_SHOW_TASTE_WORDS = (
    "甜",
    "苦",
    "酸",
    "咸",
    "涩",
    "舌尖",
    "喉咙",
)

_SHOW_DIALOGUE_EMOTION_TAG_PATTERNS = (
    r"(?:生气|愤怒|悲伤|难过|紧张|焦急|害怕|温柔|冷冷|讽刺|委屈)地(?:说|问|回答|喊|低声说)",
)

_SHOW_TRAIT_STATEMENT_PATTERNS = (
    r"(?:他|她|[一-龥]{1,4})(?:是|算是|向来是|一直是)(?:一个|个)?(?:善良|勇敢|聪明|冷漠|温柔|残忍|懦弱|坚强|自私|可靠|内向|外向|固执|敏感)(?:的)?(?:人|孩子|姑娘|少年)?",
)


_validator: AgentSkillValidator | None = None


def get_skill_validator() -> AgentSkillValidator:
    global _validator
    if _validator is None:
        _validator = AgentSkillValidator()
    return _validator


# 通用修复（循环 #5 AB-1）：统一 _DETAIL_CONCRETE_WORDS 与 _SHOW_CONCRETE_WORDS
# 根因：_DETAIL_CONCRETE_WORDS（用于 abstract_bare_count 的 _has_specific_evidence 判定）
#   原本只有 32 个词，而 _SHOW_CONCRETE_WORDS（循环 #3 扩展到约 80 个词）是另一套词表。
#   两套词表不同步导致：含"烛火/指节/泪/酒/草/枝"等词的句子被 _DETAIL_CONCRETE_WORDS
#   判为"无具体证据"（abstract_bare），但 _SHOW_CONCRETE_WORDS 认为这些是具体词——
#   abstract_bare_count 因此误判复发。
# 修复：_DETAIL_CONCRETE_WORDS = _SHOW_CONCRETE_WORDS + 额外单字（地/衣/袖/鞋/信/尘/火/锈），
#   以提供更广的单字匹配覆盖（如"地上""衣服""袖子""鞋子""尘土""火焰"等组合词）。
# 通用性：词表统一消除检测漂移，适用于所有题材——具体证据的判定标准在所有题材中一致。
_DETAIL_CONCRETE_WORDS = _SHOW_CONCRETE_WORDS + (
    "地", "衣", "袖", "鞋", "信", "尘", "火", "锈",
)

# 通用修复（循环 #6 AB-2）：扩展 _DETAIL_ACTION_WORDS
# 根因：原词表仅 23 个单字动作词，"翻身/坐起/吐/拆/翻/走/跑/跳/站/跪/坐/躺/弯/
#   低头/抬头/点头/摇头/挥手/伸手/拿起/翻开/合上/穿上/脱下/撕/塞/掏/拾"等常见身体
#   动作词不在词表中，导致含动作的句子（如"她才翻身坐起，把药丸吐在掌心"）被误判为
#   abstract_bare（无具体证据）。
# 修复：统一 _SHOW_ACTION_WORDS + 原有单字词 + 常见身体动作词。
# 通用性：这些动作词在所有题材中都是具体可观察的动作，应计为具体证据。
_DETAIL_ACTION_WORDS = (
    # 来自 _SHOW_ACTION_WORDS（双字动作词）
    "推", "拉", "放下", "抬起", "按住", "攥住", "松开", "退后", "停下",
    "砸", "摔", "捡起", "递给", "转身", "避开", "关上", "打开",
    # 原有单字动作词
    "抓", "按", "握", "抬", "放", "撞", "退", "停", "躲", "擦",
    "捏", "扣", "拽", "扔", "递", "藏", "露", "落", "响",
    # 循环 #6 新增：常见身体动作词（单字）
    "走", "跑", "跳", "站", "跪", "坐", "躺", "弯", "拆", "翻",
    "吐", "咽", "嚼", "喝", "吃", "穿", "脱", "撕", "塞", "掏",
    "拾", "摸", "探", "伸", "缩", "挥", "举", "撑", "靠", "抵",
    # 循环 #6 新增：常见身体动作词（双字）
    "翻身", "坐起", "起身", "站起", "跪下", "躺下", "弯腰", "低头",
    "抬头", "点头", "摇头", "挥手", "伸手", "缩手", "拿起", "拿起",
    "翻开", "合上", "穿上", "脱下", "撕开", "塞进", "掏出", "拾起",
    "握住", "松手", "抓紧", "捏住", "扣住", "拽住", "按倒", "推倒",
    "跨过", "迈过", "踩", "踏", "踢", "踹", "敲", "拍", "抚",
)

_DETAIL_INFORMATION_WORDS = (
    "因为",
    "所以",
    "却",
    "但",
    "仍",
    "又",
    "终于",
    "已经",
    "只剩",
    "原来",
    "发现",
    "记得",
    "留下",
    "证明",
    "暗示",
    "像",
    "仿佛",
)

# 通用修复（循环 #1）：_DETAIL_ABSTRACT_WORDS 引用统一词表 ABSTRACT_DETECTION_WORDS
# 保持 abstract_bare_count 判定范围不变，但词表来源统一，避免词表漂移。
_DETAIL_ABSTRACT_WORDS = ABSTRACT_DETECTION_WORDS

_DETAIL_DECORATIVE_WORDS = (
    "漂亮",
    "美丽",
    "华丽",
    "优雅",
    "精致",
    "迷人",
    "壮观",
    "如画",
    "令人惊叹",
    "美不胜收",
)

_DETAIL_VISUAL_WORDS = (
    "看",
    "望",
    "瞥",
    "盯",
    "光",
    "影",
    "亮",
    "暗",
    "红",
    "白",
    "黑",
    "灰",
    "蓝",
    "黄",
    "纹",
    "痕",
    "裂",
    "斑",
)

_DETAIL_AUDITORY_WORDS = (
    "听",
    "响",
    "声",
    "脚步",
    "咔",
    "吱",
    "嗒",
    "哗",
    "砰",
    "低声",
    "风声",
    "钟",
)

_DETAIL_TACTILE_WORDS = (
    "冷",
    "热",
    "烫",
    "凉",
    "湿",
    "干",
    "粗糙",
    "光滑",
    "疼",
    "痛",
    "麻",
    "硬",
    "软",
    "黏",
    "汗",
)

_DETAIL_SMELL_WORDS = (
    "味",
    "气味",
    "霉",
    "腥",
    "焦",
    "烟",
    "香",
    "潮",
    "尘味",
    "血腥",
)

_DETAIL_TASTE_WORDS = (
    "甜",
    "苦",
    "酸",
    "咸",
    "涩",
    "舌",
    "喉",
    "口腔",
    "铁锈味",
)
