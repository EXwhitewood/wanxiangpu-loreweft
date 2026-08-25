import hashlib
from typing import Literal, TypedDict


class Violation(TypedDict):
    violation_id: str
    type: str
    severity: Literal["critical", "high", "medium", "low"]
    detail: str
    suggested_strategy: Literal[
        "patch_text",
        "rewrite_scene",
        "repair_contract",
        "validator_retry",
        "manual_review",
        "patch_text_by_proposition",
        "rewrite_scene_with_fact_contract",
        "patch_literary_quality",
        "rewrite_scene_with_experience_contract",
        "repair_experience_contract",
        "style_experience_negotiation",
        "contract_budget_adjust",
    ]
    target_span: str | None
    expected_behavior: str
    source: Literal[
        "consistency",
        "critic",
        "fcip",
        "deterministic",
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
    ]
    blocks_commit: bool
    evidence: dict
    scope: str  # "prose_text" | "scene_contract" | "outline_plan" | "style_profile" | "advisory" | "validator_system"
    repairable_by_text: bool
    repairable_by_contract: bool
    # V2 诊断增强
    is_system_issue: bool  # True for validator/system failures, not text issues
    repair_scope: str  # "system" for system issues, otherwise same as scope
    # 审校版本一致性协议
    text_hash: str  # 审校时的正文 hash，空字符串表示未知
    review_version: int  # 审校时的 review 版本号
    is_stale: bool  # text_hash 与当前正文不匹配时为 True


TEXT_LOCAL_TYPES: set[str] = {
    "forbidden_triggered",
    "fact_conflict",
    "clue_provenance_error",
    "spatial_consistency_error",
    "missing_must_show",
    "event_repeated",
    "scene_too_short",
    "scene_too_long",
    "ghost_character",
    "insufficient_sensory_in_opening",
    "unprovenanced_clue",
    "clue_missing_source",
    # 信息密度检测
    "info_dump_high_density",
    "info_dump_moderate_density",
    "setting_paragraph_too_long",
    "info_reveal_burst",
    # 表达多样性检测
    "repeated_body_language",
    "emotion_expression_monotone",
    # 命题审计违规类型
    "truth_layer_conflict",
    "certainty_escalation",
    "ownership_conflict",
    "fact_boundary_conflict",
    "specificity_budget_unmet",
    "abstraction_over_budget",
    "prose_identity_weak",
    "emotional_claim_without_scene_evidence",
}

SCENE_STRUCTURAL_TYPES: set[str] = {
    "timeline_conflict",
    "setting_conflict",
    "pov_conflict",
    "identity_conflict",
    "canon_timeline_confusion",
    "temporal_layer_confusion",
    "causal_chain_error",
    "ending_state_not_reached",
    "forbidden_recap_violation",
    "omniscient_in_opening",
    "semantic_quality_error",
    "empty_text",
    # 命题审计结构级违规
    "responsibility_polarity_conflict",
    "spatial_conflict",
    "temporal_conflict",
    "forbidden_assertion_triggered",
    "required_ambiguity_broken",
    "clue_provenance_error_proposition",
    "low_reading_drive",
    "low_scene_pressure",
    "passive_protagonist",
    "weak_hook_out",
    "mode_mismatch",
    "style_experience_conflict",
    "weak_opening_hook",
    "low_event_density",
    "low_conflict_density",
    "flat_pressure_ramp",
    "weak_curiosity_engine",
    "low_reversal_density",
    "missing_micro_payoff",
    "weak_chapter_end_hook",
    "low_reader_retention",
    "plausibility_break",
    "memory_plausibility_break",
    "knowledge_boundary_violation",
}

VALIDATOR_FAILURE_TYPES: set[str] = {
    "critic_parse_error",
    "consistency_check_unavailable",
    "fcip_check_unavailable",
    "scene_contract_compiler_unavailable",
    "proposition_layer_unavailable",
    "proposition_extractor_unavailable",
}

STRATEGY_MAP: dict[str, str] = {
    "forbidden_triggered": "patch_text",
    "forbidden_recap_violation": "rewrite_scene",
    "canon_timeline_confusion": "rewrite_scene",
    "temporal_layer_confusion": "rewrite_scene",
    "fact_conflict": "patch_text",
    "internal_conflict": "patch_text",
    "clue_provenance_error": "patch_text",
    "clue_missing_source": "patch_text",
    "fcip_text_violation": "patch_text",
    "causal_chain_error": "rewrite_scene",
    "spatial_consistency_error": "patch_text",
    "missing_must_show": "patch_text",
    "ending_state_not_reached": "rewrite_scene",
    "event_repeated": "patch_text",
    "empty_text": "rewrite_scene",
    "missing_contract": "repair_contract",
    "scene_contract_compile_blocked": "repair_contract",
    "scene_contract_compiler_unavailable": "validator_retry",
    "critic_parse_error": "validator_retry",
    "ghost_character": "patch_text",
    "omniscient_in_opening": "rewrite_scene",
    "insufficient_sensory_in_opening": "patch_text",
    "unprovenanced_clue": "patch_text",
    "semantic_quality_error": "rewrite_scene",
    "consistency_check_unavailable": "validator_retry",
    "fcip_check_unavailable": "validator_retry",
    "proposition_layer_unavailable": "validator_retry",
    "proposition_extractor_unavailable": "validator_retry",
    "quality_checker_unavailable": "validator_retry",
    "scene_too_short": "patch_text",
    "scene_too_long": "patch_text",
    "timeline_conflict": "rewrite_scene",
    "setting_conflict": "rewrite_scene",
    "pov_conflict": "rewrite_scene",
    "identity_conflict": "rewrite_scene",
    # 信息密度检测
    "info_dump_high_density": "patch_text",
    "info_dump_moderate_density": "patch_text",
    "setting_paragraph_too_long": "patch_text",
    "must_show_overload": "contract_budget_adjust",
    "info_reveal_burst": "patch_text",
    # 表达多样性检测
    "repeated_body_language": "patch_text",
    "emotion_expression_monotone": "patch_text",
    "missing_genre_extension": "manual_review",
    # 风格-合同冲突
    "possible_style_contract_conflict": "manual_review",
    "style_contract_conflict": "repair_contract",
    # 命题审计违规策略
    "truth_layer_conflict": "patch_text",
    "certainty_escalation": "patch_text",
    "responsibility_polarity_conflict": "rewrite_scene",
    "spatial_conflict": "rewrite_scene",
    "temporal_conflict": "rewrite_scene",
    "ownership_conflict": "patch_text",
    "clue_provenance_error_proposition": "rewrite_scene",
    "forbidden_assertion_triggered": "rewrite_scene",
    "required_ambiguity_broken": "rewrite_scene",
    # 叙事体验 / 文学质量层策略
    "low_reading_drive": "rewrite_scene_with_experience_contract",
    "low_scene_pressure": "rewrite_scene_with_experience_contract",
    "passive_protagonist": "rewrite_scene_with_experience_contract",
    "conflict_only_explained": "rewrite_scene_with_experience_contract",
    "exposition_driven_reveal": "patch_literary_quality",
    "missing_dialogue_pressure": "patch_literary_quality",
    "weak_hook_out": "patch_literary_quality",
    "mode_mismatch": "style_experience_negotiation",
    "over_literary_for_mode": "style_experience_negotiation",
    "too_plain_for_literary_mode": "style_experience_negotiation",
    "specificity_budget_unmet": "patch_literary_quality",
    "abstraction_over_budget": "patch_literary_quality",
    "emotional_claim_without_scene_evidence": "patch_literary_quality",
    "prose_identity_weak": "patch_literary_quality",
    "style_experience_conflict": "style_experience_negotiation",
    # 商业节奏 / 爽点 / 留读驱动层策略
    "weak_opening_hook": "rewrite_scene_with_experience_contract",
    "low_event_density": "rewrite_scene_with_experience_contract",
    "low_conflict_density": "rewrite_scene_with_experience_contract",
    "flat_pressure_ramp": "patch_literary_quality",
    "weak_curiosity_engine": "repair_experience_contract",
    "low_reversal_density": "repair_experience_contract",
    "missing_micro_payoff": "patch_literary_quality",
    "weak_chapter_end_hook": "patch_literary_quality",
    "low_reader_retention": "rewrite_scene_with_experience_contract",
    # 场景可信度协议策略
    "fact_boundary_conflict": "patch_text",
    "knowledge_boundary_violation": "rewrite_scene",
    "plausibility_break": "rewrite_scene",
    "memory_plausibility_break": "rewrite_scene",
    "narration_explanation_artifact": "patch_text",
    "explanatory_punctuation_artifact": "patch_text",
    "scene_credibility_unavailable": "validator_retry",
}

SEVERITY_BLOCKS_COMMIT: dict[str, bool] = {
    "critical": True,
    "high": True,
    "medium": False,
    "low": False,
}

NON_BLOCKING_TYPES: set[str] = {
    "ghost_character",
    "insufficient_sensory_in_opening",
    "unprovenanced_clue",
    # 信息密度和表达多样性的 medium/low 级别不阻断提交
    "info_dump_moderate_density",
    "setting_paragraph_too_long",
    "emotion_expression_monotone",
    "must_show_overload",
    "quality_checker_unavailable",
    "missing_genre_extension",
    # 风格-合同冲突：possible 级别不阻断，需复核后升级
    "possible_style_contract_conflict",
    # 叙事体验 / 文学质量默认不阻断提交，交给 coordinator 生成受控建议
    "low_reading_drive",
    "low_scene_pressure",
    "passive_protagonist",
    "conflict_only_explained",
    "exposition_driven_reveal",
    "missing_dialogue_pressure",
    "weak_hook_out",
    "mode_mismatch",
    "over_literary_for_mode",
    "too_plain_for_literary_mode",
    "specificity_budget_unmet",
    "abstraction_over_budget",
    "emotional_claim_without_scene_evidence",
    "prose_identity_weak",
    "style_experience_conflict",
    # 商业节奏层默认不阻塞提交，只有 enforce 时由 QualityGate 显式升级。
    "weak_opening_hook",
    "low_event_density",
    "low_conflict_density",
    "flat_pressure_ramp",
    "weak_curiosity_engine",
    "low_reversal_density",
    "missing_micro_payoff",
    "weak_chapter_end_hook",
    "low_reader_retention",
    # 叙事表达可信度默认不硬阻塞，assist/enforce 可由 QualityGate 显式升级。
    "narration_explanation_artifact",
    "explanatory_punctuation_artifact",
}

PENDING_VALIDATION_TYPES: set[str] = VALIDATOR_FAILURE_TYPES


def make_violation(
    vtype: str,
    severity: str,
    detail: str,
    source: str = "deterministic",
    target_span: str | None = None,
    expected_behavior: str = "",
    suggested_strategy: str | None = None,
    blocks_commit: bool | None = None,
    evidence: dict | None = None,
    scope: str = "prose_text",
    repairable_by_text: bool = True,
    repairable_by_contract: bool = False,
    text_hash: str = "",
    review_version: int = 0,
) -> Violation:
    vid = hashlib.md5(
        f"{vtype}:{target_span or detail[:50]}:{source}".encode()
    ).hexdigest()[:12]

    if suggested_strategy is None:
        suggested_strategy = STRATEGY_MAP.get(vtype, "manual_review")

    if blocks_commit is None:
        if vtype in PENDING_VALIDATION_TYPES:
            blocks_commit = True
        elif vtype in NON_BLOCKING_TYPES:
            blocks_commit = False
        else:
            blocks_commit = SEVERITY_BLOCKS_COMMIT.get(severity, False)

    if vtype in PENDING_VALIDATION_TYPES:
        scope = "validator_system"
        repairable_by_text = False
        repairable_by_contract = False

    # V2 诊断增强：系统问题统一打标
    is_system_issue = vtype in PENDING_VALIDATION_TYPES
    repair_scope = "system" if is_system_issue else scope

    return Violation(
        violation_id=vid,
        type=vtype,
        severity=severity,
        detail=detail,
        suggested_strategy=suggested_strategy,
        target_span=target_span,
        expected_behavior=expected_behavior,
        source=source,
        blocks_commit=blocks_commit,
        evidence=evidence or {},
        scope=scope,
        repairable_by_text=repairable_by_text,
        repairable_by_contract=repairable_by_contract,
        is_system_issue=is_system_issue,
        repair_scope=repair_scope,
        text_hash=text_hash,
        review_version=review_version,
        is_stale=False,
    )


# ---------------------------------------------------------------------------
# Phase 7: 统一审查 Findings Schema
# ---------------------------------------------------------------------------

class ReviewFinding(TypedDict):
    """统一审查发现

    与现有 Violation 兼容，但增加了 S1-S4 severity、
    category 分类、scope/repairable 字段等。
    FBI 只吃统一后的 RepairIssue。
    """
    id: str
    severity: Literal["S1", "S2", "S3", "S4"]  # S1=阻断 S2=高 S3=中 S4=低
    category: Literal[
        "structure", "character", "prose", "consistency",
        "platform", "factual", "format", "pacing", "style", "advisory",
    ]
    source_checker: str
    source_layer: str
    location: str
    evidence: str
    issue: str
    fix_direction: str
    scope: str  # "prose_text" | "scene_contract" | "outline_plan" | "style_profile" | "advisory"
    repairable_by_text: bool
    repairable_by_contract: bool
    suggested_strategy: str
    confidence: float  # 0.0 - 1.0


# Violation severity 到 ReviewFinding severity 的映射
SEVERITY_TO_S_LEVEL: dict[str, str] = {
    "critical": "S1",
    "high": "S2",
    "medium": "S3",
    "low": "S4",
}

# Violation source 到 ReviewFinding category 的映射
SOURCE_TO_CATEGORY: dict[str, str] = {
    "consistency": "consistency",
    "critic": "structure",
    "fcip": "factual",
    "deterministic": "factual",
    "hard_correctness": "factual",
    "narrative_contract": "structure",
    "style_quality": "style",
    "reader_experience": "prose",
    "narrative_experience": "prose",
    "literary_quality": "prose",
    "mode_fit": "style",
    "style_experience_conflict": "style",
    "commercial_pacing": "pacing",
    "scene_credibility": "factual",
    "deslop_gate": "prose",
}


def violation_to_review_finding(v: Violation) -> ReviewFinding:
    """将现有 Violation 转换为统一 ReviewFinding"""
    return ReviewFinding(
        id=v["violation_id"],
        severity=SEVERITY_TO_S_LEVEL.get(v["severity"], "S4"),
        category=SOURCE_TO_CATEGORY.get(v["source"], "advisory"),
        source_checker=v["source"],
        source_layer=v["source"],
        location=v.get("target_span", ""),
        evidence=str(v.get("evidence", {})),
        issue=v["detail"],
        fix_direction=v.get("expected_behavior", ""),
        scope=v.get("scope", "prose_text"),
        repairable_by_text=v.get("repairable_by_text", True),
        repairable_by_contract=v.get("repairable_by_contract", False),
        suggested_strategy=v.get("suggested_strategy", "manual_review"),
        confidence=0.8 if v["severity"] in ("critical", "high") else 0.6,
    )


# S 级别是否阻断提交
S_LEVEL_BLOCKS_COMMIT: dict[str, bool] = {
    "S1": True,
    "S2": True,
    "S3": False,
    "S4": False,
}

# advisory 不进入修复管线
ADVISORY_CATEGORIES: set[str] = {"advisory"}

# scene_contract 问题不走文本修复
CONTRACT_SCOPES: set[str] = {"scene_contract", "outline_plan", "style_profile"}
