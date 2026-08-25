"""质量发现中心。

统一所有审校问题语义。所有 checker、Deslop Gate、FBI 复检、人工反馈
都通过 Finding Normalizer → 去重合并 → 判定 severity → 判定 repair_scope
→ 判定 repair_lane → 分组排序 → 输出 ReviewFindingV2。

每个 finding 必须回答：
1. 这是不是问题？
2. 是否阻断提交？
3. 用户需不需要看到？
4. 是正文问题、合同问题、风格问题、ledger 问题，还是提示？
5. 谁能修？
6. 如何验收？
7. 修几次失败后进入人工？
"""
from __future__ import annotations

import hashlib
import logging

from app.models.review_finding_v2 import ReviewFindingV2
from app.services.review_issue_semantics import normalize_violation_semantics

logger = logging.getLogger(__name__)

# Violation source → repair_scope 映射
_SOURCE_TO_SCOPE: dict[str, str] = {
    "consistency": "prose_text",
    "critic": "prose_text",
    "fcip": "prose_text",
    "deterministic": "prose_text",
    "hard_correctness": "prose_text",
    "narrative_contract": "scene_contract",
    "style_quality": "style_policy",
    "reader_experience": "prose_text",
    "narrative_experience": "prose_text",
    "literary_quality": "prose_text",
    "mode_fit": "style_policy",
    "style_experience_conflict": "style_policy",
    "commercial_pacing": "prose_text",
    "scene_credibility": "prose_text",
    "deslop_gate": "prose_text",
}

# Violation type → repair_lane 映射
_TYPE_TO_LANE: dict[str, str] = {
    # 事实类
    "fact_conflict": "fbi_fact",
    "internal_conflict": "fbi_fact",
    "naming_conflict": "fbi_fact",
    "timeline_conflict": "deterministic",
    "setting_conflict": "fbi_fact",
    "pov_conflict": "fbi_scene_restructure",
    "identity_conflict": "fbi_fact",
    "causal_chain_error": "fbi_fact",
    "clue_provenance_error": "fbi_fact",
    "clue_missing_source": "fbi_fact",
    "unprovenanced_clue": "fbi_fact",
    "fact_boundary_conflict": "fbi_fact",
    "knowledge_boundary_violation": "fbi_fact",
    "truth_layer_conflict": "fbi_fact",
    "certainty_escalation": "fbi_fact",
    "responsibility_polarity_conflict": "fbi_fact",
    "spatial_conflict": "fbi_fact",
    "spatial_consistency_error": "fbi_fact",
    "temporal_conflict": "deterministic",
    "ownership_conflict": "fbi_fact",
    "forbidden_assertion_triggered": "fbi_fact",
    "forbidden_content": "fbi_scene_restructure",
    "forbidden_event": "fbi_scene_restructure",
    # 文体类
    "ai_punctuation_artifact": "deterministic",
    "enforced_ai_flavor": "fbi_prose",
    "ai_style_artifact": "fbi_prose",
    "explanation_monotone": "fbi_prose",
    "repeated_body_language": "fbi_prose",
    "emotion_expression_monotone": "fbi_prose",
    "info_dump_high_density": "fbi_prose",
    "info_dump_moderate_density": "fbi_prose",
    "info_reveal_burst": "fbi_prose",
    "setting_paragraph_too_long": "fbi_prose",
    "narration_explanation_artifact": "fbi_prose",
    "explanatory_punctuation_artifact": "deterministic",
    "repetition_artifact": "deterministic",
    "repeated_phrase": "deterministic",
    "event_repeated": "fbi_fact",
    "specificity_budget_unmet": "fbi_prose",
    "abstraction_over_budget": "fbi_prose",
    "emotional_claim_without_scene_evidence": "fbi_prose",
    "prose_identity_weak": "fbi_prose",
    # 节奏类
    "low_reading_drive": "fbi_pacing",
    "low_scene_pressure": "fbi_pacing",
    "passive_protagonist": "fbi_pacing",
    "weak_hook_out": "fbi_pacing",
    "weak_opening_hook": "fbi_pacing",
    "low_event_density": "fbi_pacing",
    "low_conflict_density": "fbi_pacing",
    "flat_pressure_ramp": "fbi_pacing",
    "weak_curiosity_engine": "fbi_pacing",
    "low_reversal_density": "fbi_pacing",
    "missing_micro_payoff": "fbi_pacing",
    "weak_chapter_end_hook": "fbi_pacing",
    "low_reader_retention": "fbi_pacing",
    # 可信度/结构类
    "plausibility_break": "fbi_scene_restructure",
    "memory_plausibility_break": "fbi_scene_restructure",
    "forbidden_recap_violation": "fbi_scene_restructure",
    "missing_must_show": "fbi_scene_restructure",
    # 结尾类
    "ending_state_not_reached": "fbi_ending",
    # 合同类
    "forbidden_triggered": "deterministic",
    "scene_contract_compile_blocked": "manual_only",
    "must_show_overload": "deterministic",
    # 确定性修复
    "scene_too_short": "deterministic",
    "scene_too_long": "deterministic",
    # 审校器/验证器故障不是正文问题，不能送进 FBI 文本修复。
    "critic_parse_error": "none",
    "consistency_check_unavailable": "none",
    "fcip_check_unavailable": "none",
    "scene_contract_compiler_unavailable": "none",
    "proposition_layer_unavailable": "none",
    "proposition_extractor_unavailable": "none",
}

# Violation type → repair_scope 强制覆盖。
_TYPE_TO_SCOPE: dict[str, str] = {
    "forbidden_triggered": "prose_text",
    "forbidden_assertion_triggered": "prose_text",
    "forbidden_recap_violation": "prose_text",
    "forbidden_content": "prose_text",
    "forbidden_event": "prose_text",
    "event_repeated": "prose_text",
    "spatial_consistency_error": "prose_text",
    "missing_must_show": "prose_text",
    "ending_state_not_reached": "prose_text",
    "must_show_overload": "scene_contract",
    "missing_contract": "scene_contract",
    "scene_contract_compile_blocked": "scene_contract",
    "style_contract_conflict": "style_policy",
    "possible_style_contract_conflict": "style_policy",
    "style_experience_conflict": "style_policy",
    "mode_mismatch": "style_policy",
    "critic_parse_error": "validator_system",
    "consistency_check_unavailable": "validator_system",
    "fcip_check_unavailable": "validator_system",
    "scene_contract_compiler_unavailable": "validator_system",
    "proposition_layer_unavailable": "validator_system",
    "proposition_extractor_unavailable": "validator_system",
}

# Severity 映射
_SEVERITY_MAP: dict[str, str] = {
    "critical": "S1",
    "high": "S2",
    "medium": "S3",
    "low": "S4",
}


class QualityFindingHub:
    """质量发现中心"""

    @staticmethod
    def _normalize_scope(scope: str | None) -> str | None:
        if not scope:
            return None
        mapping = {
            "prose_text": "prose_text",
            "scene_contract": "scene_contract",
            "chapter_contract": "chapter_contract",
            "outline_plan": "chapter_contract",
            "ledger": "ledger",
            "style_profile": "style_policy",
            "style_policy": "style_policy",
            "validator_system": "validator_system",
            "advisory": "advisory",
        }
        return mapping.get(scope)

    def normalize_violation(self, violation: dict) -> ReviewFindingV2:
        """将旧 Violation 转换为 ReviewFindingV2"""
        violation = normalize_violation_semantics(violation)
        source = violation.get("source", "unknown")
        v_type = violation.get("type", "unknown")
        severity = violation.get("severity", "low")

        s_level = _SEVERITY_MAP.get(severity, "S4")
        repair_scope = (
            self._normalize_scope(violation.get("scope"))
            or _TYPE_TO_SCOPE.get(v_type)
            or _SOURCE_TO_SCOPE.get(source, "prose_text")
        )
        repair_lane = _TYPE_TO_LANE.get(v_type, "none")

        suggested_strategy = violation.get("suggested_strategy", "")
        if suggested_strategy == "contract_budget_adjust":
            repair_scope = "scene_contract"
            repair_lane = "deterministic"

        if v_type in {
            "critic_parse_error",
            "consistency_check_unavailable",
            "fcip_check_unavailable",
            "scene_contract_compiler_unavailable",
            "proposition_layer_unavailable",
            "proposition_extractor_unavailable",
        }:
            repair_scope = "validator_system"
            repair_lane = "none"

        if violation.get("repairable_by_text") is False and violation.get("repairable_by_contract"):
            repair_scope = "scene_contract"
            repair_lane = "deterministic"

        # 合同问题不能送去正文改写
        if repair_scope in ("scene_contract", "chapter_contract"):
            if repair_lane.startswith("fbi_"):
                repair_lane = "manual_only"

        # advisory 不进入修复管线
        if repair_scope in ("advisory", "validator_system"):
            repair_lane = "none"

        # S1 必须阻断
        blocks_commit = s_level in ("S1", "S2") and repair_scope != "advisory"

        # S4 默认不阻断
        if s_level == "S4":
            blocks_commit = False

        return ReviewFindingV2(
            id=violation.get("violation_id", hashlib.md5(f"{source}:{v_type}".encode()).hexdigest()[:12]),
            source=source,
            type=v_type,
            title=f"{source}: {v_type}",
            description=violation.get("detail", ""),
            severity=s_level,
            blocks_commit=blocks_commit,
            user_visible=repair_scope != "advisory",
            repair_scope=repair_scope,
            repair_lane=repair_lane,
            evidence_spans=[{
                "span": violation.get("target_span", "") or violation.get("forbidden_item", ""),
                "text": violation.get("target_span", "") or violation.get("forbidden_item", ""),
                "current_length": violation.get("current_length"),
                "hard_max_chars": violation.get("hard_max_chars"),
                "raw": violation,
            }],
            validator=source,
            retryable=repair_lane not in ("none", "manual_only"),
            max_attempts=3 if repair_lane.startswith("fbi_") else 1,
        )

    def normalize_deslop_finding(self, finding: dict) -> ReviewFindingV2:
        """将 Deslop Gate ReviewFinding 转换为 ReviewFindingV2"""
        gate = finding.get("source_checker", "deslop_gate_A")[-1]  # 取最后一个字母 A-H
        severity = finding.get("severity", "S3")

        # Gate → repair_lane 映射
        gate_lane_map = {
            "A": "fbi_prose",
            "B": "fbi_prose",
            "C": "fbi_prose",
            "D": "fbi_pacing",
            "E": "fbi_style_guard",
            "F": "fbi_pacing",
            "G": "fbi_prose",
            "H": "fbi_prose",
        }

        return ReviewFindingV2(
            id=finding.get("id", ""),
            source="deslop_gate",
            type=f"deslop_gate_{gate}",
            title=f"Deslop Gate {gate}",
            description=finding.get("issue", ""),
            severity=severity,
            blocks_commit=severity in ("S1", "S2"),
            user_visible=True,
            repair_scope="prose_text",
            repair_lane=gate_lane_map.get(gate, "fbi_prose"),
            evidence_spans=[{"evidence": finding.get("evidence", "")}],
            validator="deslop_gate",
            retryable=True,
            max_attempts=3,
        )

    def normalize_all(self, violations: list[dict], deslop_findings: list[dict] | None = None) -> list[ReviewFindingV2]:
        """归一化所有来源的审查发现"""
        findings = []

        for v in violations:
            findings.append(self.normalize_violation(v))

        if deslop_findings:
            for f in deslop_findings:
                findings.append(self.normalize_deslop_finding(f))

        # 去重：同 type + 同 scope 只保留最高 severity
        findings = self._deduplicate(findings)

        # 排序：severity 高的优先，advisory 最后
        severity_order = {"S1": 0, "S2": 1, "S3": 2, "S4": 3}
        scope_order = {
            "prose_text": 0,
            "scene_contract": 1,
            "chapter_contract": 2,
            "style_policy": 3,
            "ledger": 4,
            "validator_system": 5,
            "advisory": 6,
        }

        findings.sort(key=lambda f: (
            severity_order.get(f.severity, 99),
            scope_order.get(f.repair_scope, 99),
        ))

        return findings

    def filter_actionable(self, findings: list[ReviewFindingV2]) -> list[ReviewFindingV2]:
        """过滤出可操作的发现"""
        return [f for f in findings if f.is_actionable()]

    def filter_by_scope(self, findings: list[ReviewFindingV2], scope: str) -> list[ReviewFindingV2]:
        """按 repair_scope 过滤"""
        return [f for f in findings if f.repair_scope == scope]

    def filter_by_lane(self, findings: list[ReviewFindingV2], lane: str) -> list[ReviewFindingV2]:
        """按 repair_lane 过滤"""
        return [f for f in findings if f.repair_lane == lane]

    def group_by_repair_lane(self, findings: list[ReviewFindingV2]) -> dict[str, list[ReviewFindingV2]]:
        """按修复通道分组"""
        groups: dict[str, list[ReviewFindingV2]] = {}
        for f in findings:
            lane = f.repair_lane
            if lane not in groups:
                groups[lane] = []
            groups[lane].append(f)
        return groups

    def _deduplicate(self, findings: list[ReviewFindingV2]) -> list[ReviewFindingV2]:
        """去重：同 type + 同 scope 只保留最高 severity"""
        seen: dict[str, ReviewFindingV2] = {}
        severity_rank = {"S1": 0, "S2": 1, "S3": 2, "S4": 3}

        for f in findings:
            key = self._dedupe_key(f)
            existing = seen.get(key)
            if existing is None or severity_rank.get(f.severity, 99) < severity_rank.get(existing.severity, 99):
                seen[key] = f

        return list(seen.values())

    @staticmethod
    def _dedupe_key(finding: ReviewFindingV2) -> str:
        gap_types = {
            "causal_chain_error",
            "clue_missing_source",
            "unprovenanced_clue",
            "clue_provenance_error",
            "unknown",
            "unclassified_review_issue",
            "semantic_quality_error",
        }
        if finding.type not in gap_types:
            return f"{finding.type}:{finding.repair_scope}"

        target = ""
        for evidence in finding.evidence_spans:
            if not isinstance(evidence, dict):
                continue
            raw = evidence.get("raw") if isinstance(evidence.get("raw"), dict) else {}
            target = (
                evidence.get("span")
                or evidence.get("text")
                or raw.get("target_span")
                or ""
            )
            if target:
                break
        seed = str(target or finding.description or finding.id)
        digest = hashlib.md5(seed.encode()).hexdigest()[:10]
        return f"{finding.type}:{finding.repair_scope}:{digest}"


# 全局单例
_hub: QualityFindingHub | None = None


def get_quality_finding_hub() -> QualityFindingHub:
    global _hub
    if _hub is None:
        _hub = QualityFindingHub()
    return _hub
