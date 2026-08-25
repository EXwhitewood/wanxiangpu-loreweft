"""统一 metric 命名注册表（方案 7）。

三套 validator（QualityGate / AgentSkillCommitGate / ValidatorProtocolAdapter）
此前使用不同的 metric 命名风格：
- QualityGate: 单一名（如 "consistency_check_unavailable"）
- AgentSkillCommitGate: 复合名（如 "validator:metric"）
- AgentSkillValidator: 又一种名（如 "validator_implemented"）

本注册表统一所有 metric 命名，消除下游消费方的命名不一致问题。

命名规范：{domain}_{category}_{detail}
- domain: 检查域（consistency/critic/fcip/ai_flavor/pov/tense/scene_evidence/specificity/skill）
- category: 检查类别（check/parse/unavailable/violation/implemented）
- detail: 具体细节（可选）
"""

from __future__ import annotations


class MetricRegistry:
    """统一 metric 命名常量。"""

    # === QualityGate degraded metrics ===
    CONSISTENCY_CHECK_UNAVAILABLE = "consistency_check_unavailable"
    CRITIC_PARSE_ERROR = "critic_parse_error"
    FCIP_CHECK_UNAVAILABLE = "fcip_check_unavailable"

    # === AgentSkillValidator metrics ===
    SKILL_VALIDATOR_IMPLEMENTED = "skill_validator_implemented"
    SKILL_VALIDATOR_NOT_IMPLEMENTED = "skill_validator_not_implemented"

    # === AgentSkillCommitGate 复合命名拆分 ===
    # 旧格式 "validator:metric" 改为分别记录 validator 和 metric
    # blocks_commit 判定统一用 metric 字段，不再用复合字符串

    @staticmethod
    def format_failure_summary(validator: str, metric: str) -> str:
        """格式化失败摘要，用于 trace 和日志。"""
        return f"{validator}:{metric}"

    @staticmethod
    def is_degraded_metric(metric: str) -> bool:
        """判断是否为 degraded 类 metric（检查器不可用，非内容问题）。"""
        return metric in {
            MetricRegistry.CONSISTENCY_CHECK_UNAVAILABLE,
            MetricRegistry.CRITIC_PARSE_ERROR,
            MetricRegistry.FCIP_CHECK_UNAVAILABLE,
            MetricRegistry.SKILL_VALIDATOR_NOT_IMPLEMENTED,
        }


# ---------------------------------------------------------------------------
# 方案 7 Part B1：完整 METRIC_REGISTRY
# ---------------------------------------------------------------------------
# 从 ValidatorProtocolAdapter 的所有 _XXX_METRICS 常量提取 metric 名，
# 每个 metric 补充 category（检查域）和 severity（阻断级别）字段。
#
# severity 取值：
#   "blocking" —— 计数类/契约硬约束，默认阻断 commit
#   "advisory" —— 评分/比例/诊断类，默认不阻断 commit
#
# category 取值：
#   "skill"        —— AgentSkillValidator 确定性检查
#   "quality"      —— QualityGate LLM/体验层检查
#   "consistency"  —— 一致性冲突
#   "health"       —— ChapterCommitHealthChecker 终审结构检查
#   "system"       —— 检查器不可用等降级 metric
# ---------------------------------------------------------------------------

METRIC_REGISTRY: dict[str, dict[str, str]] = {
    # === skill 域：AI 话语形状（ai_discourse）计数类，advisory ===
    # 项目硬约束：AI flavor checkers must not block content generation;
    # enforcement_policy is set to advisory_only_never_block
    "sentence_shell_count": {"category": "skill", "severity": "advisory"},
    "paragraph_shape_repeat_count": {"category": "skill", "severity": "advisory"},
    "mirrored_paragraph_opening_count": {"category": "skill", "severity": "advisory"},
    "semantic_restatement_count": {"category": "skill", "severity": "advisory"},
    "action_then_explanation_count": {"category": "skill", "severity": "advisory"},
    "double_conclusion_count": {"category": "skill", "severity": "advisory"},
    "moral_overclarity_count": {"category": "skill", "severity": "advisory"},
    "fake_interaction_count": {"category": "skill", "severity": "advisory"},

    # === skill 域：节奏控制（rhythm_metrics） ===
    "uniform_sentence_streak_max": {"category": "skill", "severity": "advisory"},
    "dense_paragraph_streak_max": {"category": "skill", "severity": "advisory"},
    "breathing_paragraph_ratio": {"category": "skill", "severity": "advisory"},
    "abrupt_shift_count": {"category": "skill", "severity": "blocking"},
    "sentence_length_variance": {"category": "skill", "severity": "advisory"},
    "sentence_length_cv": {"category": "skill", "severity": "advisory"},

    # === skill 域：AI 味（ai_flavor）命中/密度类，advisory ===
    # 项目硬约束：AI flavor checkers must not block content generation;
    # enforcement_policy is set to advisory_only_never_block
    "ai_punctuation_artifact": {"category": "skill", "severity": "advisory"},
    "dash_per_1000": {"category": "skill", "severity": "advisory"},
    "dash_artifact_hits": {"category": "skill", "severity": "advisory"},
    "emdash_pair_hits": {"category": "skill", "severity": "advisory"},
    "emdash_hits": {"category": "skill", "severity": "advisory"},
    "tier1_hit_count": {"category": "skill", "severity": "advisory"},
    "tier3_hit_count": {"category": "skill", "severity": "advisory"},
    "tier3_density": {"category": "skill", "severity": "advisory"},
    "false_range_hits": {"category": "skill", "severity": "advisory"},
    "false_range_count": {"category": "skill", "severity": "advisory"},
    "overexplain_hits": {"category": "skill", "severity": "advisory"},
    "template_phrase_hits": {"category": "skill", "severity": "advisory"},
    "summary_ending_hits": {"category": "skill", "severity": "advisory"},
    "technical_register_hits": {"category": "skill", "severity": "advisory"},
    "emotion_label_hits": {"category": "skill", "severity": "advisory"},
    "dialogue_tag_hits": {"category": "skill", "severity": "advisory"},
    "explanatory_narration_hits": {"category": "skill", "severity": "advisory"},
    "formulaic_transition_hits": {"category": "skill", "severity": "advisory"},
    "inflated_significance_hits": {"category": "skill", "severity": "advisory"},
    "promotional_language_hits": {"category": "skill", "severity": "advisory"},
    "filler_phrase_hits": {"category": "skill", "severity": "advisory"},
    "negative_parallel_hits": {"category": "skill", "severity": "advisory"},
    "not_but_pattern": {"category": "skill", "severity": "advisory"},
    "three_part_escalation_hits": {"category": "skill", "severity": "advisory"},
    "vague_attribution_hits": {"category": "skill", "severity": "advisory"},
    "vague_attribution_count": {"category": "skill", "severity": "advisory"},
    "structure_word_hits": {"category": "skill", "severity": "advisory"},
    "structure_word_cluster_count": {"category": "skill", "severity": "advisory"},

    # === skill 域：声纹（voice_fingerprint） ===
    "profile_available": {"category": "skill", "severity": "blocking"},
    "voice_fingerprint_score": {"category": "skill", "severity": "advisory"},
    "generic_voice_hits": {"category": "skill", "severity": "blocking"},
    "generic_voice_density": {"category": "skill", "severity": "blocking"},
    "avoided_word_hits": {"category": "skill", "severity": "blocking"},
    "preferred_marker_hits": {"category": "skill", "severity": "blocking"},
    "observation_domain_hits": {"category": "skill", "severity": "blocking"},
    "average_sentence_length": {"category": "skill", "severity": "advisory"},
    "sentence_length_deviation": {"category": "skill", "severity": "advisory"},
    "dialogue_count": {"category": "skill", "severity": "advisory"},
    "speaker_count": {"category": "skill", "severity": "advisory"},

    # === skill 域：文学质量确定性 metrics（scores 类，advisory） ===
    "literary_quality_advisory": {"category": "skill", "severity": "advisory"},
    "specificity": {"category": "skill", "severity": "advisory"},
    "rhythm_control": {"category": "skill", "severity": "advisory"},
    "prose_identity": {"category": "skill", "severity": "advisory"},
    "style_alignment": {"category": "skill", "severity": "advisory"},
    "cultural_texture": {"category": "skill", "severity": "advisory"},
    "emotional_evidence": {"category": "skill", "severity": "advisory"},
    "exposition_balance": {"category": "skill", "severity": "advisory"},
    "dialogue_pressure": {"category": "skill", "severity": "advisory"},
    "mode_fit": {"category": "skill", "severity": "advisory"},
    "abstract_hits": {"category": "skill", "severity": "blocking"},
    "generic_label_hits": {"category": "skill", "severity": "blocking"},
    "body_hits": {"category": "skill", "severity": "blocking"},
    "concrete_hits": {"category": "skill", "severity": "advisory"},
    "sensory_hits": {"category": "skill", "severity": "advisory"},
    "unique_sentence_starters": {"category": "skill", "severity": "advisory"},

    # === skill 域：场景结构（scene_structure）契约约束 ===
    "goal_present": {"category": "skill", "severity": "blocking"},
    "conflict_present": {"category": "skill", "severity": "blocking"},
    "conflict_count": {"category": "skill", "severity": "blocking"},
    "value_change": {"category": "skill", "severity": "blocking"},
    "hook_present": {"category": "skill", "severity": "blocking"},
    "max_exposition_block_chars": {"category": "skill", "severity": "blocking"},
    "passive_percentage": {"category": "skill", "severity": "blocking"},
    "pure_exposition_block_chars": {"category": "skill", "severity": "blocking"},

    # === skill 域：POV 一致性 ===
    "single_pov_per_scene": {"category": "skill", "severity": "blocking"},
    "head_hopping_count": {"category": "skill", "severity": "blocking"},
    "named_inner_access_hits": {"category": "skill", "severity": "blocking"},
    "ambiguous_inner_access_hits": {"category": "skill", "severity": "blocking"},
    "omniscient_breach_count": {"category": "skill", "severity": "blocking"},
    "pov_shift_count": {"category": "skill", "severity": "blocking"},
    "knowledge_boundary_breach": {"category": "skill", "severity": "blocking"},
    "narrator_commentary_count": {"category": "skill", "severity": "blocking"},

    # === skill 域：时态一致性 ===
    "temporal_anchor_count": {"category": "skill", "severity": "blocking"},
    "flashback_entry_count": {"category": "skill", "severity": "advisory"},
    "flashback_return_count": {"category": "skill", "severity": "advisory"},
    "flashback_tense_correct": {"category": "skill", "severity": "blocking"},
    "time_jump_without_signal_count": {"category": "skill", "severity": "blocking"},
    "tense_drift_count": {"category": "skill", "severity": "blocking"},

    # === skill 域：场景证据（scene_evidence） ===
    "emotion_label_count": {"category": "skill", "severity": "blocking"},
    "emotion_label_count_per_500": {"category": "skill", "severity": "blocking"},
    "thought_verb_count": {"category": "skill", "severity": "blocking"},
    "thought_verb_count_per_500": {"category": "skill", "severity": "blocking"},
    "abstract_claim_count": {"category": "skill", "severity": "blocking"},
    "standalone_abstract_claims": {"category": "skill", "severity": "blocking"},
    "abstraction_over_budget": {"category": "skill", "severity": "blocking"},
    "trait_statement_count": {"category": "skill", "severity": "blocking"},
    "body_language_cliche_count": {"category": "skill", "severity": "blocking"},
    "dialogue_emotion_tag_count": {"category": "skill", "severity": "blocking"},
    "concrete_evidence_count": {"category": "skill", "severity": "advisory"},
    "evidence_per_emotion_claim": {"category": "skill", "severity": "advisory"},
    "sense_category_count": {"category": "skill", "severity": "advisory"},

    # === skill 域：细节锚定预算（specificity_budget） ===
    "specific_detail_count": {"category": "skill", "severity": "advisory"},
    "detail_hit_count": {"category": "skill", "severity": "advisory"},
    "concrete_hit_count": {"category": "skill", "severity": "advisory"},
    "action_hit_count": {"category": "skill", "severity": "advisory"},
    "sensory_hit_count": {"category": "skill", "severity": "advisory"},
    "scene_element_anchor_count": {"category": "skill", "severity": "advisory"},
    "missing_scene_element_count": {"category": "skill", "severity": "blocking"},
    "information_anchor_count": {"category": "skill", "severity": "advisory"},
    "generic_detail_count": {"category": "skill", "severity": "blocking"},
    "anchor_coverage": {"category": "skill", "severity": "blocking"},
    "core_anchor_present": {"category": "skill", "severity": "blocking"},
    "sensory_mode_count": {"category": "skill", "severity": "blocking"},
    "abstract_bare_count": {"category": "skill", "severity": "blocking"},
    "decoration_only_detail_count": {"category": "skill", "severity": "blocking"},

    # === skill 域：advisory 汇总计数（literary_quality / ai_flavor 共用） ===
    "high_advisories": {"category": "skill", "severity": "advisory"},  # 附录4问题11修复
    "medium_advisories": {"category": "skill", "severity": "advisory"},

    # === quality 域：叙事体验（narrative_experience）scores，advisory ===
    "retention_pressure": {"category": "quality", "severity": "advisory"},
    "curiosity_drive": {"category": "quality", "severity": "advisory"},
    "emotional_engagement": {"category": "quality", "severity": "advisory"},
    "clarity": {"category": "quality", "severity": "advisory"},
    "pacing_fit": {"category": "quality", "severity": "advisory"},
    "event_density": {"category": "quality", "severity": "advisory"},
    "reversal_density": {"category": "quality", "severity": "advisory"},
    "question_count": {"category": "quality", "severity": "advisory"},
    "payoff_count": {"category": "quality", "severity": "advisory"},
    "dramatic_pressure": {"category": "quality", "severity": "advisory"},
    "reading_drive": {"category": "quality", "severity": "advisory"},
    "reader_momentum_at_exit": {"category": "quality", "severity": "advisory"},

    # === quality 域：商业节奏 / 留读驱动（enforce 时由 QualityGate 升级为阻断） ===
    "low_conflict_density": {"category": "quality", "severity": "blocking"},
    "weak_curiosity_engine": {"category": "quality", "severity": "blocking"},
    "low_event_density": {"category": "quality", "severity": "blocking"},
    "low_reversal_density": {"category": "quality", "severity": "blocking"},
    "missing_micro_payoff": {"category": "quality", "severity": "blocking"},
    "weak_opening_hook": {"category": "quality", "severity": "blocking"},
    "weak_chapter_end_hook": {"category": "quality", "severity": "blocking"},
    "flat_pressure_ramp": {"category": "quality", "severity": "blocking"},
    "low_reader_retention": {"category": "quality", "severity": "blocking"},

    # === quality 域：方案 30 B 类语义判定指标（LLM 判定，enforce 时阻断） ===
    # 这两个 metric 是方案 30 新增的 B 类指标，由 LlmSemanticChecker 产出。
    # 其余 6 个 B 类 metric（low_conflict_density / weak_curiosity_engine /
    # weak_opening_hook / weak_chapter_end_hook / abstraction_over_budget /
    # standalone_abstract_claims）已在上方 skill/quality 域注册，此处不重复。
    "missing_specific_detail_anchor": {"category": "quality", "severity": "blocking"},
    "voice_phrase_drift": {"category": "quality", "severity": "advisory"},

    # === consistency 域：一致性冲突 ===
    "fact_conflict": {"category": "consistency", "severity": "blocking"},
    "naming_conflict": {"category": "consistency", "severity": "blocking"},
    "timeline_conflict": {"category": "consistency", "severity": "blocking"},
    "setting_conflict": {"category": "consistency", "severity": "blocking"},
    "pov_conflict": {"category": "consistency", "severity": "blocking"},
    "identity_conflict": {"category": "consistency", "severity": "blocking"},
    "internal_conflict": {"category": "consistency", "severity": "blocking"},
    "spatial_conflict": {"category": "consistency", "severity": "blocking"},
    "temporal_conflict": {"category": "consistency", "severity": "blocking"},
    "ownership_conflict": {"category": "consistency", "severity": "blocking"},
    "knowledge_boundary_violation": {"category": "consistency", "severity": "blocking"},

    # === health 域：ChapterCommitHealthChecker 终审结构检查 ===
    "empty_final_text": {"category": "health", "severity": "blocking"},
    "empty_scene_text": {"category": "health", "severity": "blocking"},
    "residual_scene_marker": {"category": "health", "severity": "blocking"},
    "internal_scene_separator": {"category": "health", "severity": "blocking"},
    "empty_chapter_state": {"category": "health", "severity": "blocking"},
    "style_requires_human_review": {"category": "health", "severity": "advisory"},

    # === system 域：检查器降级 metric（向后兼容现有常量） ===
    "consistency_check_unavailable": {"category": "system", "severity": "blocking"},
    "critic_parse_error": {"category": "system", "severity": "blocking"},
    "fcip_check_unavailable": {"category": "system", "severity": "blocking"},
    "skill_validator_implemented": {"category": "system", "severity": "advisory"},
    "skill_validator_not_implemented": {"category": "system", "severity": "blocking"},
    "validator_available": {"category": "system", "severity": "blocking"},
    "quality_checker_unavailable": {"category": "system", "severity": "advisory"},
    "final_gate_blocked": {"category": "system", "severity": "blocking"},

    # === 通用长度类 ===
    "scene_too_short": {"category": "skill", "severity": "blocking"},
    "scene_too_long": {"category": "skill", "severity": "advisory"},
}


_METRIC_ALIASES: dict[str, str] = {
    "dash_density": "dash_per_1000",
    "max_consecutive_emdash_pairs": "consecutive_dash_max",
}


def canonical_metric_name(metric: str) -> str:
    """Return the canonical registry key used for enforcement decisions.

    QualityGate historically wraps repair-triggering advisories as
    ``enforced_<metric>``.  Enforcement must still be decided from the
    underlying metric; otherwise ``enforced_ai_punctuation_artifact`` becomes
    an unknown advisory while ``ai_punctuation_artifact`` is hard-blocking.
    """
    key = str(metric or "").strip().lower()
    while key.startswith("enforced_"):
        key = key[len("enforced_"):]
    return _METRIC_ALIASES.get(key, key)


def get_metric_info(metric: str) -> dict[str, str]:
    """返回 metric 的 category / severity 元信息。

    未知 metric 返回 ``{"category": "unknown", "severity": "advisory"}``，
    保证下游在不识别 metric 时默认走非阻断路径，避免误伤。
    """
    key = canonical_metric_name(metric)
    if not key:
        return {"category": "unknown", "severity": "advisory"}
    info = METRIC_REGISTRY.get(key)
    if not info:
        return {"category": "unknown", "severity": "advisory"}
    return {
        "category": str(info.get("category") or "unknown"),
        "severity": str(info.get("severity") or "advisory"),
    }


# ---------------------------------------------------------------------------
# Enforcement 分级体系（破折号铁律 + advisory 仅记录）
# ---------------------------------------------------------------------------
# enforcement 取值：
#   "hard_blocking" —— 铁律硬约束，修复耗尽后 waiting_review
#   "advisory"      —— 主观指标，仅记录，不进入自动修订收敛
#   "observe_only"  —— 低置信度诊断，不自动修复不阻断
#
# 字段说明：
#   enforcement        —— 执法级别
#   max_repair_cycles  —— 自动修复轮数；advisory 固定为 0
#   after_exhaustion   —— 修复耗尽后行为：waiting_review / allow_commit / drop
#   memory_can_promote —— 质量记忆累计能否改变 enforcement（永远 False）
# ---------------------------------------------------------------------------

ENFORCEMENT_OVERRIDES: dict[str, dict[str, object]] = {
    # === 破折号铁律：hard_blocking（Skill 合同 dash_per_1000_max=0.5, consecutive_dash_max=2） ===
    "dash_per_1000": {
        "enforcement": "hard_blocking",
        "max_repair_cycles": None,
        "after_exhaustion": "waiting_review",
        "memory_can_promote": False,
    },
    "ai_punctuation_artifact": {
        "enforcement": "hard_blocking",
        "max_repair_cycles": None,
        "after_exhaustion": "waiting_review",
        "memory_can_promote": False,
    },
    "dash_artifact_hits": {
        "enforcement": "hard_blocking",
        "max_repair_cycles": None,
        "after_exhaustion": "waiting_review",
        "memory_can_promote": False,
    },
    "emdash_pair_hits": {
        "enforcement": "hard_blocking",
        "max_repair_cycles": None,
        "after_exhaustion": "waiting_review",
        "memory_can_promote": False,
    },
    "emdash_hits": {
        "enforcement": "hard_blocking",
        "max_repair_cycles": None,
        "after_exhaustion": "waiting_review",
        "memory_can_promote": False,
    },
    "consecutive_dash_max": {
        "enforcement": "hard_blocking",
        "max_repair_cycles": None,
        "after_exhaustion": "waiting_review",
        "memory_can_promote": False,
    },

    # === 商业节奏/留读驱动类：从 blocking 降级为 advisory ===
    # 这些是主观体验指标，仅记录并直接放行，不进入自动修订或 waiting_review
    "low_conflict_density": {
        "enforcement": "advisory",
        "max_repair_cycles": 2,
        "after_exhaustion": "allow_commit",
        "memory_can_promote": False,
    },
    "weak_curiosity_engine": {
        "enforcement": "advisory",
        "max_repair_cycles": 2,
        "after_exhaustion": "allow_commit",
        "memory_can_promote": False,
    },
    "low_event_density": {
        "enforcement": "advisory",
        "max_repair_cycles": 2,
        "after_exhaustion": "allow_commit",
        "memory_can_promote": False,
    },
    "low_reversal_density": {
        "enforcement": "advisory",
        "max_repair_cycles": 2,
        "after_exhaustion": "allow_commit",
        "memory_can_promote": False,
    },
    "missing_micro_payoff": {
        "enforcement": "advisory",
        "max_repair_cycles": 2,
        "after_exhaustion": "allow_commit",
        "memory_can_promote": False,
    },
    "weak_opening_hook": {
        "enforcement": "advisory",
        "max_repair_cycles": 2,
        "after_exhaustion": "allow_commit",
        "memory_can_promote": False,
    },
    "weak_chapter_end_hook": {
        "enforcement": "advisory",
        "max_repair_cycles": 2,
        "after_exhaustion": "allow_commit",
        "memory_can_promote": False,
    },
    "flat_pressure_ramp": {
        "enforcement": "advisory",
        "max_repair_cycles": 2,
        "after_exhaustion": "allow_commit",
        "memory_can_promote": False,
    },
    "low_reader_retention": {
        "enforcement": "advisory",
        "max_repair_cycles": 2,
        "after_exhaustion": "allow_commit",
        "memory_can_promote": False,
    },
    "missing_specific_detail_anchor": {
        "enforcement": "advisory",
        "max_repair_cycles": 2,
        "after_exhaustion": "allow_commit",
        "memory_can_promote": False,
    },
    "voice_phrase_drift": {
        "enforcement": "advisory",
        "max_repair_cycles": 2,
        "after_exhaustion": "allow_commit",
        "memory_can_promote": False,
    },

    # === AI 味主观指标：advisory（明喻、句式、节奏等） ===
    "ai_simile_overuse": {
        "enforcement": "advisory",
        "max_repair_cycles": 2,
        "after_exhaustion": "allow_commit",
        "memory_can_promote": False,
    },
    "uniform_sentence_streak_max": {
        "enforcement": "advisory",
        "max_repair_cycles": 2,
        "after_exhaustion": "allow_commit",
        "memory_can_promote": False,
    },
    "dense_paragraph_streak_max": {
        "enforcement": "advisory",
        "max_repair_cycles": 2,
        "after_exhaustion": "allow_commit",
        "memory_can_promote": False,
    },
    "breathing_paragraph_ratio": {
        "enforcement": "advisory",
        "max_repair_cycles": 2,
        "after_exhaustion": "allow_commit",
        "memory_can_promote": False,
    },
}


# Required story outcomes, truth/protection boundaries and validator
# availability are hard requirements even when their metric did not exist in
# the legacy METRIC_REGISTRY.  Keeping this list next to the enforcement table
# prevents unknown-metric fallback from silently downgrading them.
_HARD_BLOCKING_METRICS: frozenset[str] = frozenset({
    # Explicit scene/chapter contract prohibitions remain deterministic hard
    # requirements. Style-profile forbidden words use a separate advisory key.
    "forbidden_triggered",
    "missing_must_show",
    "ending_state_not_reached",
    "forbidden_recap_violation",
    "forbidden_assertion_triggered",
    "required_ambiguity_broken",
    "premature_foreshadowing_reveal",
    "truth_layer_conflict",
    "certainty_escalation",
    "responsibility_polarity_conflict",
    "clue_provenance_error",
    "clue_provenance_error_proposition",
    "unprovenanced_clue",
    "clue_missing_source",
    "fcip_registration_error",
    "fcip_text_violation",
    "scene_contract_compile_blocked",
    "scene_contract_compiler_unavailable",
    "proposition_layer_unavailable",
    "proposition_extractor_unavailable",
    "empty_text",
})

# These metrics are measurable, but the thresholds describe style or reader
# experience rather than factual correctness. They are retained as observations
# and never enter automatic repair or become blockers by accumulation.
_ADVISORY_METRICS: frozenset[str] = frozenset({
    "abrupt_shift_count",
    "generic_voice_hits",
    "generic_voice_density",
    "avoided_word_hits",
    "preferred_marker_hits",
    "observation_domain_hits",
    "abstract_hits",
    "generic_label_hits",
    "body_hits",
    "emotion_label_count",
    "emotion_label_count_per_500",
    "thought_verb_count",
    "thought_verb_count_per_500",
    "abstract_claim_count",
    "standalone_abstract_claims",
    "abstraction_over_budget",
    "trait_statement_count",
    "body_language_cliche_count",
    "dialogue_emotion_tag_count",
    "generic_detail_count",
    "anchor_coverage",
    "sensory_mode_count",
    "abstract_bare_count",
    "decoration_only_detail_count",
    "style_requires_human_review",
    "forbidden_word",
    "avoid_pattern_violation",
    "style_directive_deviation",
    "voice_phrase_drift",
})

for _metric in _HARD_BLOCKING_METRICS:
    ENFORCEMENT_OVERRIDES.setdefault(_metric, {
        "enforcement": "hard_blocking",
        "max_repair_cycles": None,
        "after_exhaustion": "waiting_review",
        "memory_can_promote": False,
    })

for _metric in _ADVISORY_METRICS:
    ENFORCEMENT_OVERRIDES[_metric] = {
        "enforcement": "advisory",
        "max_repair_cycles": 0,
        "after_exhaustion": "allow_commit",
        "memory_can_promote": False,
    }

for _enforcement_info in ENFORCEMENT_OVERRIDES.values():
    if _enforcement_info.get("enforcement") == "advisory":
        _enforcement_info["max_repair_cycles"] = 0


def get_enforcement(metric: str) -> dict[str, object]:
    """返回 metric 的 enforcement 信息。

    优先查 ENFORCEMENT_OVERRIDES，没有则按 METRIC_REGISTRY 的 severity 推导：
    - severity == "blocking" -> hard_blocking
    - severity == "advisory" -> advisory
    - 未知 -> advisory（默认非阻断）

    返回 dict 包含：
    - enforcement: "hard_blocking" / "advisory" / "observe_only"
    - max_repair_cycles: int 或 None
    - after_exhaustion: "waiting_review" / "allow_commit" / "drop"
    - memory_can_promote: bool（永远 False）
    """
    key = canonical_metric_name(metric)
    if key in ENFORCEMENT_OVERRIDES:
        return dict(ENFORCEMENT_OVERRIDES[key])
    info = get_metric_info(key)
    sev = str(info.get("severity") or "advisory").lower()
    if sev == "blocking":
        return {
            "enforcement": "hard_blocking",
            "max_repair_cycles": None,
            "after_exhaustion": "waiting_review",
            "memory_can_promote": False,
        }
    return {
        "enforcement": "advisory",
        "max_repair_cycles": 0,
        "after_exhaustion": "allow_commit",
        "memory_can_promote": False,
    }


def is_hard_blocking(metric: str) -> bool:
    """判断 metric 是否为 hard_blocking enforcement。"""
    return str(get_enforcement(metric).get("enforcement") or "").lower() == "hard_blocking"


def is_advisory(metric: str) -> bool:
    """判断 metric 是否为 advisory enforcement。"""
    return str(get_enforcement(metric).get("enforcement") or "").lower() == "advisory"


def determine_blocks_commit(metric: str, severity: str) -> bool:
    """按 enforcement 决定是否阻断 commit。

    判定规则（基于 enforcement，不再用 severity 直接决定）：
    - enforcement == "hard_blocking" -> True
    - enforcement == "advisory" / "observe_only" -> False
    - 未知 metric -> False（advisory 默认）

    severity 参数保留用于向后兼容，但不再决定 blocking。
    这样同一 metric 在初审、recheck、final_acceptance 三处得到的阻断判定
    完全一致，消除"同名不同阻断"的根因 B5。
    """
    enforcement = get_enforcement(metric)
    return str(enforcement.get("enforcement") or "").lower() == "hard_blocking"
