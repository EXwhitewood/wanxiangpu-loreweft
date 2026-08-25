"""Registry that aligns skill validator metrics with FBI repair blueprints.

The registry is deliberately declarative. It does not discover issues and does
not repair prose. It only gives ReviewCaseFile/FBI layers a shared vocabulary
for routing active skill validator metrics into localization, blueprint, tool,
or explicit non-text fallback states.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SkillMetricRepairSpec:
    validator: str
    metric: str
    issue_type: str
    family: str
    status: str
    operation: str = ""
    localization_strategy: str = "sentence_cluster"
    markers: tuple[str, ...] = field(default_factory=tuple)
    requires_target_span: bool = True
    requires_replacement: bool = False
    fallback_route: str = "needs_human_review"
    repair_family: str = ""
    metric_direction: str = "decrease"

    def as_dict(self) -> dict[str, Any]:
        return {
            "validator": self.validator,
            "metric": self.metric,
            "issue_type": self.issue_type,
            "family": self.family,
            "status": self.status,
            "operation": self.operation,
            "localization_strategy": self.localization_strategy,
            "markers": list(self.markers),
            "requires_target_span": self.requires_target_span,
            "requires_replacement": self.requires_replacement,
            "fallback_route": self.fallback_route,
            "repair_family": self.repair_family,
            "metric_direction": self.metric_direction,
        }


_AI_MARKERS = (
    "仿佛", "似乎", "某种", "意义", "本质", "命运", "灵魂", "复杂", "内心",
    "这说明", "这意味着", "显然", "事实上", "换句话说", "真正", "微光",
    "勾勒", "渲染", "交织", "流淌", "弥漫", "烙印", "羁绊", "宿命",
    "执念", "救赎", "价值", "本质", "标志着", "见证了", "里程碑",
    "不难发现", "值得注意", "综上", "从某种意义上",
)

_STRUCTURE_MARKERS = (
    "首先", "其次", "再次", "最后", "因此", "然而", "于是", "然后",
    "与此同时", "事实上", "显然", "换句话说", "这说明", "这意味着",
)

_FALSE_RANGE_MARKERS = (
    "不是", "而是", "不只是", "更是", "不仅", "而且", "无论", "还是", "都",
)

_FILLER_MARKERS = (
    "值得注意的是", "不难发现", "换句话说", "某种意义上", "事实上",
    "显然", "总而言之", "综上所述",
)

_PROMO_MARKERS = (
    "令人叹为观止", "美轮美奂", "无与伦比", "博大精深", "极致",
    "震撼", "华丽", "宏大",
)

_OVEREXPLAIN_MARKERS = (
    "这说明", "这意味着", "也就是说", "换句话说", "她终于明白",
    "他终于明白", "真正重要", "原因很简单", "答案很简单",
)

_DISCOURSE_MARKERS = (
    "不是", "而是", "真正", "重要的不是", "答案很简单", "原因很简单",
    "这说明", "这意味着", "也就是说", "换句话说", "终于明白",
)

_RHYTHM_MARKERS = (
    "忽然", "突然", "过了一会儿", "片刻", "这时", "然后", "接着",
)

_VOICE_MARKERS = (
    "某种", "仿佛", "似乎", "显然", "事实上", "真正", "内心", "复杂",
    "命运", "灵魂", "意义", "本质",
)


_SHOW_EVIDENCE_MARKERS = (
    "害怕", "恐惧", "担心", "紧张", "愤怒", "生气", "悲伤", "难过", "震惊", "开心",
    "勇敢", "善良", "冷静", "坚强", "复杂的情绪", "心里", "内心", "情绪",
)

_POV_MARKERS = (
    "心里", "心中", "脑中", "暗想", "想起", "觉得", "知道", "明白", "意识到", "认定",
    "担心", "害怕", "希望", "后悔",
)

_EXPOSITION_MARKERS = (
    "这说明", "这意味着", "因此", "所以", "事实上", "显然", "原因", "本质", "意义",
    "价值", "命运", "过去", "背景", "设定", "由此可见",
)

_TIME_ANCHOR_MARKERS = (
    "清晨", "早晨", "上午", "正午", "午后", "下午", "黄昏", "傍晚", "夜里", "深夜",
    "月光", "天色", "日头", "片刻", "过了一会儿", "这时",
)


def _spec(
    validator: str,
    metric: str,
    family: str,
    status: str,
    operation: str = "",
    *,
    markers: tuple[str, ...] = (),
    localization_strategy: str = "sentence_cluster",
    requires_target_span: bool = True,
    requires_replacement: bool = False,
    fallback_route: str = "needs_human_review",
    repair_family: str = "",
    metric_direction: str = "decrease",
) -> SkillMetricRepairSpec:
    return SkillMetricRepairSpec(
        validator=validator,
        metric=metric,
        issue_type=metric,
        family=family,
        status=status,
        operation=operation,
        localization_strategy=localization_strategy,
        markers=markers,
        requires_target_span=requires_target_span,
        requires_replacement=requires_replacement,
        fallback_route=fallback_route,
        repair_family=repair_family or family,
        metric_direction=metric_direction,
    )


_SPECS: dict[str, SkillMetricRepairSpec] = {
    # ai_flavor: implemented deterministic local tools.
    "dash_per_1000": _spec("ai_flavor", "dash_per_1000", "anti_ai_punctuation", "implemented_tool", "normalize_punctuation", markers=("--", "——", "—"), localization_strategy="dash_window", requires_target_span=False, repair_family="anti_ai_punctuation"),
    "dash_density": _spec("ai_flavor", "dash_density", "anti_ai_punctuation", "implemented_tool", "normalize_punctuation", markers=("--", "——", "—"), localization_strategy="dash_window", requires_target_span=False, repair_family="anti_ai_punctuation"),
    "ai_punctuation_artifact": _spec("ai_flavor", "ai_punctuation_artifact", "anti_ai_punctuation", "implemented_tool", "normalize_punctuation", markers=("--", "——", "—"), localization_strategy="dash_window", requires_target_span=False, repair_family="anti_ai_punctuation"),
    "explanatory_punctuation_artifact": _spec("ai_flavor", "explanatory_punctuation_artifact", "anti_ai_punctuation", "implemented_tool", "normalize_punctuation", markers=("--", "——", "—"), localization_strategy="dash_window", requires_target_span=False, repair_family="anti_ai_punctuation"),
    "max_consecutive_emdash_pairs": _spec("ai_flavor", "max_consecutive_emdash_pairs", "anti_ai_punctuation", "implemented_tool", "normalize_punctuation", markers=("--", "——", "—"), localization_strategy="dash_window", requires_target_span=False, repair_family="anti_ai_punctuation"),
    "consecutive_dash_max": _spec("ai_flavor", "consecutive_dash_max", "anti_ai_punctuation", "implemented_tool", "normalize_punctuation", markers=("--", "——", "—"), localization_strategy="dash_window", requires_target_span=False, repair_family="anti_ai_punctuation"),
    "tier1_hit_count": _spec("ai_flavor", "tier1_hit_count", "anti_ai_local", "implemented_tool", "replace_tier1_ai_flavor_terms", markers=_AI_MARKERS, repair_family="anti_ai_tier1"),
    "structure_word_cluster_count": _spec("ai_flavor", "structure_word_cluster_count", "anti_ai_local", "implemented_tool", "normalize_structure_words", markers=_STRUCTURE_MARKERS, repair_family="anti_ai_structure"),
    "tier2_cluster_count": _spec("ai_flavor", "tier2_cluster_count", "anti_ai_local", "implemented_tool", "cleanup_ai_flavor_window", markers=_AI_MARKERS, repair_family="anti_ai_tier2"),
    "tier3_density": _spec("ai_flavor", "tier3_density", "anti_ai_local", "implemented_tool", "cleanup_ai_flavor_window", markers=_AI_MARKERS, repair_family="anti_ai_tier3"),
    "three_part_escalation_hits": _spec("ai_flavor", "three_part_escalation_hits", "anti_ai_local", "implemented_tool", "cleanup_ai_flavor_window", markers=_STRUCTURE_MARKERS, repair_family="anti_ai_structure"),
    "three_part_escalation_count": _spec("ai_flavor", "three_part_escalation_count", "anti_ai_local", "implemented_tool", "cleanup_ai_flavor_window", markers=_STRUCTURE_MARKERS, repair_family="anti_ai_structure"),
    "negative_parallel_hits": _spec("ai_flavor", "negative_parallel_hits", "anti_ai_local", "implemented_tool", "cleanup_ai_flavor_window", markers=_FALSE_RANGE_MARKERS, repair_family="anti_ai_parallel"),
    "negative_parallel_count": _spec("ai_flavor", "negative_parallel_count", "anti_ai_local", "implemented_tool", "cleanup_ai_flavor_window", markers=_FALSE_RANGE_MARKERS, repair_family="anti_ai_parallel"),
    "false_range_hits": _spec("ai_flavor", "false_range_hits", "anti_ai_local", "implemented_tool", "cleanup_ai_flavor_window", markers=_FALSE_RANGE_MARKERS, repair_family="anti_ai_false_range"),
    "false_range_count": _spec("ai_flavor", "false_range_count", "anti_ai_local", "implemented_tool", "cleanup_ai_flavor_window", markers=_FALSE_RANGE_MARKERS, repair_family="anti_ai_false_range"),
    "vague_attribution_hits": _spec("ai_flavor", "vague_attribution_hits", "anti_ai_local", "implemented_tool", "cleanup_ai_flavor_window", markers=("有人说", "专家认为", "研究表明", "据说", "传闻"), repair_family="anti_ai_attribution"),
    "vague_attribution_count": _spec("ai_flavor", "vague_attribution_count", "anti_ai_local", "implemented_tool", "cleanup_ai_flavor_window", markers=("有人说", "专家认为", "研究表明", "据说", "传闻"), repair_family="anti_ai_attribution"),
    "filler_phrase_hits": _spec("ai_flavor", "filler_phrase_hits", "anti_ai_local", "implemented_tool", "cleanup_ai_flavor_window", markers=_FILLER_MARKERS, repair_family="anti_ai_filler"),
    "filler_phrase_count": _spec("ai_flavor", "filler_phrase_count", "anti_ai_local", "implemented_tool", "cleanup_ai_flavor_window", markers=_FILLER_MARKERS, repair_family="anti_ai_filler"),
    "promotional_language_hits": _spec("ai_flavor", "promotional_language_hits", "anti_ai_local", "implemented_tool", "cleanup_ai_flavor_window", markers=_PROMO_MARKERS, repair_family="anti_ai_promo"),
    "promotional_language_count": _spec("ai_flavor", "promotional_language_count", "anti_ai_local", "implemented_tool", "cleanup_ai_flavor_window", markers=_PROMO_MARKERS, repair_family="anti_ai_promo"),
    "inflated_significance_hits": _spec("ai_flavor", "inflated_significance_hits", "anti_ai_local", "implemented_tool", "cleanup_ai_flavor_window", markers=("意义", "标志着", "见证了", "里程碑", "至关重要", "深远"), repair_family="anti_ai_inflated"),
    "inflated_significance_count": _spec("ai_flavor", "inflated_significance_count", "anti_ai_local", "implemented_tool", "cleanup_ai_flavor_window", markers=("意义", "标志着", "见证了", "里程碑", "至关重要", "深远"), repair_family="anti_ai_inflated"),
    "summary_ending_hits": _spec("ai_flavor", "summary_ending_hits", "anti_ai_local", "implemented_tool", "cleanup_ai_flavor_window", markers=("刚刚开始", "未来的路", "命运", "真正", "终于明白"), localization_strategy="ending_window", repair_family="anti_ai_ending"),
    "chapter_end_moral_count": _spec("ai_flavor", "chapter_end_moral_count", "anti_ai_local", "implemented_tool", "cleanup_ai_flavor_window", markers=("刚刚开始", "未来的路", "命运", "真正", "终于明白"), localization_strategy="ending_window", repair_family="anti_ai_ending"),
    "overexplain_hits": _spec("ai_flavor", "overexplain_hits", "anti_ai_local", "implemented_tool", "cleanup_ai_flavor_window", markers=_OVEREXPLAIN_MARKERS, repair_family="anti_ai_overexplain"),
    "overexplain_count": _spec("ai_flavor", "overexplain_count", "anti_ai_local", "implemented_tool", "cleanup_ai_flavor_window", markers=_OVEREXPLAIN_MARKERS, repair_family="anti_ai_overexplain"),
    "visual_verb_cluster_count": _spec("ai_flavor", "visual_verb_cluster_count", "anti_ai_local", "implemented_tool", "cleanup_ai_flavor_window", markers=("勾勒", "渲染", "交织", "流淌", "弥漫", "烙印"), repair_family="anti_ai_visual"),
    "abstract_noun_cluster_count": _spec("ai_flavor", "abstract_noun_cluster_count", "anti_ai_local", "implemented_tool", "cleanup_ai_flavor_window", markers=("命运", "宿命", "羁绊", "执念", "救赎", "灵魂", "意义", "价值", "本质"), repair_family="anti_ai_abstract"),
    "sentence_length_cv": _spec("ai_flavor", "sentence_length_cv", "anti_ai_discourse", "implemented_tool", "vary_sentence_length_window", markers=(), localization_strategy="sentence_streak", repair_family="sentence_rhythm"),
    "sentence_length_variance": _spec("rhythm_metrics", "sentence_length_variance", "rhythm", "implemented_tool", "vary_sentence_length_window", markers=(), localization_strategy="sentence_streak", repair_family="sentence_rhythm", metric_direction="increase"),
    "high_advisories": _spec("ai_flavor", "high_advisories", "anti_ai_local", "implemented_candidate_tool", "cleanup_ai_flavor_window", markers=_AI_MARKERS, fallback_route="needs_human_review", repair_family="anti_ai_advisory_bundle"),
    "medium_advisories": _spec("ai_flavor", "medium_advisories", "anti_ai_local", "implemented_candidate_tool", "cleanup_ai_flavor_window", markers=_AI_MARKERS, fallback_route="needs_human_review", repair_family="anti_ai_advisory_bundle"),
    "abstraction_over_budget": _spec("literary_quality", "abstraction_over_budget", "anti_ai_local", "implemented_tool", "cleanup_ai_flavor_window", markers=_AI_MARKERS + _EXPOSITION_MARKERS, repair_family="anti_ai_abstraction"),

    # show_dont_tell / scene evidence.
    "emotion_label_count": _spec("scene_evidence", "emotion_label_count", "show_evidence", "implemented_tool", "cleanup_ai_flavor_window", markers=_SHOW_EVIDENCE_MARKERS, repair_family="show_evidence"),
    "emotion_label_hits": _spec("literary_quality", "emotion_label_hits", "show_evidence", "implemented_tool", "cleanup_ai_flavor_window", markers=_SHOW_EVIDENCE_MARKERS, repair_family="show_evidence"),
    "trait_statement_count": _spec("scene_evidence", "trait_statement_count", "show_evidence", "implemented_tool", "cleanup_ai_flavor_window", markers=_SHOW_EVIDENCE_MARKERS, repair_family="show_evidence"),
    "body_language_cliche_count": _spec("scene_evidence", "body_language_cliche_count", "show_evidence", "implemented_tool", "cleanup_ai_flavor_window", markers=_SHOW_EVIDENCE_MARKERS, repair_family="show_evidence"),
    "dialogue_emotion_tag_count": _spec("scene_evidence", "dialogue_emotion_tag_count", "show_evidence", "implemented_tool", "cleanup_ai_flavor_window", markers=_SHOW_EVIDENCE_MARKERS, repair_family="show_evidence"),
    "standalone_abstract_claims": _spec("scene_evidence", "standalone_abstract_claims", "show_evidence", "implemented_tool", "cleanup_ai_flavor_window", markers=_SHOW_EVIDENCE_MARKERS + _EXPOSITION_MARKERS, repair_family="show_evidence"),
    "abstract_bare_count": _spec("scene_evidence", "abstract_bare_count", "show_evidence", "implemented_tool", "cleanup_ai_flavor_window", markers=_SHOW_EVIDENCE_MARKERS + _EXPOSITION_MARKERS, repair_family="show_evidence"),

    # ai_discourse.
    "sentence_shell_count": _spec("ai_discourse", "sentence_shell_count", "anti_ai_discourse", "implemented_tool", "trim_discourse_window", markers=_DISCOURSE_MARKERS, repair_family="ai_sentence_shell"),
    "paragraph_shape_repeat_count": _spec("ai_discourse", "paragraph_shape_repeat_count", "anti_ai_discourse", "implemented_tool", "vary_sentence_shape", markers=(), localization_strategy="paragraph_cluster", repair_family="paragraph_rhythm"),
    "semantic_restatement_count": _spec("ai_discourse", "semantic_restatement_count", "anti_ai_discourse", "implemented_tool", "trim_discourse_window", markers=_DISCOURSE_MARKERS, repair_family="ai_semantic_restatement"),
    "action_then_explanation_count": _spec("ai_discourse", "action_then_explanation_count", "anti_ai_discourse", "implemented_tool", "trim_discourse_window", markers=_OVEREXPLAIN_MARKERS, repair_family="ai_action_explanation"),
    "mirrored_paragraph_opening_count": _spec("ai_discourse", "mirrored_paragraph_opening_count", "anti_ai_discourse", "implemented_tool", "vary_sentence_shape", markers=(), localization_strategy="paragraph_opening_cluster", repair_family="paragraph_rhythm"),
    "double_conclusion_count": _spec("ai_discourse", "double_conclusion_count", "anti_ai_discourse", "implemented_tool", "trim_discourse_window", markers=_OVEREXPLAIN_MARKERS, repair_family="ai_double_conclusion"),
    "moral_overclarity_count": _spec("ai_discourse", "moral_overclarity_count", "anti_ai_discourse", "implemented_tool", "trim_discourse_window", markers=("真正", "终于明白", "学会", "放下", "救赎", "成长"), repair_family="ai_moral_overclarity"),
    "fake_interaction_count": _spec("ai_discourse", "fake_interaction_count", "anti_ai_discourse", "implemented_tool", "trim_discourse_window", markers=("你是否也", "不妨想一想", "让我们", "我们不妨", "值得我们思考"), repair_family="ai_fake_interaction"),
    "uniform_sentence_streak_max": _spec("rhythm_metrics", "uniform_sentence_streak_max", "anti_ai_discourse", "implemented_tool", "vary_sentence_length_window", markers=(), localization_strategy="sentence_streak", repair_family="sentence_rhythm"),

    # rhythm.
    "breathing_paragraph_ratio": _spec("rhythm_metrics", "breathing_paragraph_ratio", "rhythm", "implemented_tool", "insert_functional_breathing_paragraph", markers=_RHYTHM_MARKERS, localization_strategy="paragraph_cluster", requires_target_span=False, repair_family="reader_breathing", metric_direction="increase"),
    "dense_paragraph_streak_max": _spec("rhythm_metrics", "dense_paragraph_streak_max", "rhythm", "implemented_tool", "split_paragraph", markers=(), localization_strategy="paragraph_cluster", repair_family="paragraph_rhythm"),
    "transition_paragraph_present": _spec("rhythm_metrics", "transition_paragraph_present", "rhythm", "implemented_tool", "insert_transition_anchor", markers=_RHYTHM_MARKERS, requires_target_span=False, repair_family="rhythm_transition", metric_direction="resolve"),
    "abrupt_shift_count": _spec("rhythm_metrics", "abrupt_shift_count", "rhythm", "implemented_tool", "smooth_abrupt_shift", markers=_RHYTHM_MARKERS, localization_strategy="transition_window", repair_family="rhythm_transition"),
    "chapter_hook_present": _spec("narrative_experience", "chapter_hook_present", "rhythm", "implemented_tool", "insert_hook_beat", markers=("为什么", "哪里", "怎么", "异样", "问"), localization_strategy="opening_window", requires_target_span=False, repair_family="commercial_hook", metric_direction="resolve"),
    "reader_momentum_at_exit": _spec("narrative_experience", "reader_momentum_at_exit", "rhythm", "implemented_tool", "insert_hook_beat", markers=("为什么", "哪里", "怎么", "异样", "问"), localization_strategy="ending_window", requires_target_span=False, repair_family="commercial_hook", metric_direction="resolve"),
    "tension_flatline": _spec("narrative_experience", "tension_flatline", "rhythm", "implemented_tool", "insert_pressure_cost", markers=("不能", "危险", "血", "疼", "阻", "逼近"), localization_strategy="paragraph_cluster", requires_target_span=False, repair_family="commercial_pressure", metric_direction="resolve"),

    "low_conflict_density": _spec("commercial_pacing", "low_conflict_density", "rhythm", "implemented_tool", "insert_pressure_cost", markers=("不能", "危险", "血", "痛", "追", "逼近"), localization_strategy="paragraph_cluster", requires_target_span=False, repair_family="commercial_pressure", metric_direction="resolve"),
    "flat_pressure_ramp": _spec("commercial_pacing", "flat_pressure_ramp", "rhythm", "implemented_tool", "insert_pressure_cost", markers=("不能", "危险", "血", "痛", "追", "逼近"), localization_strategy="paragraph_cluster", requires_target_span=False, repair_family="commercial_pressure", metric_direction="resolve"),
    "weak_curiosity_engine": _spec("commercial_pacing", "weak_curiosity_engine", "rhythm", "implemented_tool", "insert_hook_beat", markers=("为什么", "哪里", "怎么", "异样", "问"), localization_strategy="paragraph_cluster", requires_target_span=False, repair_family="commercial_hook", metric_direction="resolve"),
    "weak_opening_hook": _spec("commercial_pacing", "weak_opening_hook", "rhythm", "implemented_tool", "insert_hook_beat", markers=("为什么", "哪里", "怎么", "异样", "问"), localization_strategy="opening_window", requires_target_span=False, repair_family="commercial_hook", metric_direction="resolve"),
    "weak_chapter_end_hook": _spec("commercial_pacing", "weak_chapter_end_hook", "rhythm", "implemented_tool", "insert_hook_beat", markers=("为什么", "哪里", "怎么", "异样", "问"), localization_strategy="ending_window", requires_target_span=False, repair_family="commercial_hook", metric_direction="resolve"),
    "temporal_anchor_count": _spec("rhythm_metrics", "temporal_anchor_count", "rhythm", "implemented_tool", "insert_time_anchor", markers=_TIME_ANCHOR_MARKERS, requires_target_span=False, repair_family="time_anchor", metric_direction="increase"),
    "time_anchor_count": _spec("rhythm_metrics", "time_anchor_count", "rhythm", "implemented_tool", "insert_time_anchor", markers=_TIME_ANCHOR_MARKERS, requires_target_span=False, repair_family="time_anchor", metric_direction="increase"),
    "max_exposition_block_chars": _spec("scene_structure", "max_exposition_block_chars", "structure", "implemented_tool", "split_paragraph", markers=_EXPOSITION_MARKERS, localization_strategy="paragraph_cluster", repair_family="exposition_break"),
    "pure_exposition_block_chars": _spec("scene_structure", "pure_exposition_block_chars", "structure", "implemented_tool", "split_paragraph", markers=_EXPOSITION_MARKERS, localization_strategy="paragraph_cluster", repair_family="exposition_break"),
    "pure_exposition_block": _spec("scene_structure", "pure_exposition_block", "structure", "implemented_tool", "split_paragraph", markers=_EXPOSITION_MARKERS, localization_strategy="paragraph_cluster", repair_family="exposition_break"),
    "ending_state_not_reached": _spec("scene_contract", "ending_state_not_reached", "structure", "implemented_tool", "insert_micro_payoff", markers=(), localization_strategy="ending_window", requires_target_span=False, repair_family="contract_completion", metric_direction="resolve"),
    "missing_must_show": _spec("scene_contract", "missing_must_show", "structure", "implemented_tool", "insert_micro_payoff", markers=(), localization_strategy="ending_window", requires_target_span=False, repair_family="contract_completion", metric_direction="resolve"),

    # voice.
    "voice_fingerprint": _spec("voice_fingerprint", "voice_fingerprint", "voice", "implemented_candidate_tool", "rewrite_voice_window", markers=_VOICE_MARKERS, localization_strategy="voice_window", repair_family="voice_reconstruction", metric_direction="increase"),
    "voice_fingerprint_score": _spec("voice_fingerprint", "voice_fingerprint_score", "voice", "implemented_candidate_tool", "rewrite_voice_window", markers=_VOICE_MARKERS, localization_strategy="voice_window", repair_family="voice_reconstruction", metric_direction="increase"),
    "generic_voice_density": _spec("voice_fingerprint", "generic_voice_density", "voice", "implemented_tool", "rewrite_voice_window", markers=_VOICE_MARKERS, localization_strategy="voice_window", repair_family="voice_reconstruction"),
    "avoided_word_hits": _spec("voice_fingerprint", "avoided_word_hits", "voice", "implemented_tool", "rewrite_voice_window", markers=_VOICE_MARKERS, localization_strategy="voice_window", repair_family="voice_reconstruction"),
    "head_hopping_count": _spec("pov_consistency", "head_hopping_count", "pov", "implemented_tool", "rewrite_voice_window", markers=_POV_MARKERS, localization_strategy="pov_window", repair_family="pov_boundary"),
    "single_pov_per_scene": _spec("pov_consistency", "single_pov_per_scene", "pov", "implemented_tool", "rewrite_voice_window", markers=_POV_MARKERS, localization_strategy="pov_window", repair_family="pov_boundary", metric_direction="resolve"),
    "profile_available": _spec("voice_fingerprint", "profile_available", "system", "degraded_system_issue", "", requires_target_span=False, fallback_route="degraded_system_issue", metric_direction="resolve"),
    "validator_available": _spec("validator", "validator_available", "system", "degraded_system_issue", "", requires_target_span=False, fallback_route="degraded_system_issue", metric_direction="resolve"),
    "missing_required_context": _spec("validator", "missing_required_context", "system", "degraded_system_issue", "", requires_target_span=False, fallback_route="degraded_system_issue", metric_direction="resolve"),
    "resource_pack_unavailable": _spec("validator", "resource_pack_unavailable", "system", "degraded_system_issue", "", requires_target_span=False, fallback_route="degraded_system_issue", metric_direction="resolve"),
}


_DETAIL_ALIASES: tuple[tuple[str, str], ...] = (
    ("tier 1 ai-flavor vocabulary", "tier1_hit_count"),
    ("tier 2 visual/abstract vocabulary clusters", "tier2_cluster_count"),
    ("tier2_cluster_count", "tier2_cluster_count"),
    ("tier 3 low-information vocabulary density", "tier3_density"),
    ("three-part escalation", "three_part_escalation_count"),
    ("negative parallel", "negative_parallel_count"),
    ("false range", "false_range_count"),
    ("vague attribution", "vague_attribution_count"),
    ("filler phrases", "filler_phrase_count"),
    ("promotional language", "promotional_language_count"),
    ("inflated significance", "inflated_significance_count"),
    ("structure-word clusters", "structure_word_cluster_count"),
    ("chapter-ending moral", "chapter_end_moral_count"),
    ("overexplanation", "overexplain_count"),
    ("visual-verb clusters", "visual_verb_cluster_count"),
    ("abstract-noun clusters", "abstract_noun_cluster_count"),
    ("consecutive dash", "max_consecutive_emdash_pairs"),
    ("dash density", "dash_per_1000"),
    ("same length band", "uniform_sentence_streak_max"),
    ("sentence rhythm is too uniform", "sentence_length_variance"),
    ("sentence-length burstiness", "sentence_length_cv"),
    ("sentence_shell_count", "sentence_shell_count"),
    ("paragraph shape", "paragraph_shape_repeat_count"),
    ("semantic_restatement_count", "semantic_restatement_count"),
    ("semantic restatement", "semantic_restatement_count"),
    ("action_then_explanation_count", "action_then_explanation_count"),
    ("action then explanation", "action_then_explanation_count"),
    ("mirrored_paragraph_opening_count", "mirrored_paragraph_opening_count"),
    ("double_conclusion_count", "double_conclusion_count"),
    ("moral_overclarity_count", "moral_overclarity_count"),
    ("fake_interaction_count", "fake_interaction_count"),
    ("breathing_paragraph_ratio", "breathing_paragraph_ratio"),
    ("dense_paragraph_streak_max", "dense_paragraph_streak_max"),
    ("transition_paragraph_present", "transition_paragraph_present"),
    ("abrupt_shift_count", "abrupt_shift_count"),
    ("voice_fingerprint_score", "voice_fingerprint_score"),
    ("generic_voice_density", "generic_voice_density"),
    ("avoided_word_hits", "avoided_word_hits"),
    ("emotion labels remain", "emotion_label_count"),
    ("emotion label", "emotion_label_count"),
    ("observable evidence", "emotion_label_count"),
    ("trait statement", "trait_statement_count"),
    ("body language cliche", "body_language_cliche_count"),
    ("standalone abstract", "standalone_abstract_claims"),
    ("abstract descriptions", "standalone_abstract_claims"),
    ("abstract bare", "abstract_bare_count"),
    ("inner access to non-pov", "head_hopping_count"),
    ("non-pov inner access", "head_hopping_count"),
    ("head hopping", "head_hopping_count"),
    ("single pov", "single_pov_per_scene"),
    ("observable temporal anchor", "temporal_anchor_count"),
    ("temporal anchor", "temporal_anchor_count"),
    ("pure exposition block", "pure_exposition_block_chars"),
    ("exposition block", "pure_exposition_block_chars"),
    ("validator_available", "validator_available"),
    ("profile_available", "profile_available"),
)


def all_skill_metric_specs() -> dict[str, SkillMetricRepairSpec]:
    return dict(_SPECS)


def get_metric_spec(metric: str | None, validator: str | None = None) -> SkillMetricRepairSpec | None:
    key = str(metric or "").strip().lower()
    if not key:
        return None
    if key in _SPECS:
        return _SPECS[key]
    normalized = key.replace("-", "_").replace(" ", "_")
    if normalized in _SPECS:
        return _SPECS[normalized]
    return None


def infer_metric_from_detail(detail: str) -> str:
    lowered = str(detail or "").lower()
    for token, metric in _DETAIL_ALIASES:
        if token in lowered:
            return metric
    return ""


def metric_family(metric: str | None, default: str = "") -> str:
    spec = get_metric_spec(metric)
    return spec.family if spec is not None else default


def implemented_tool_operations() -> set[str]:
    return {
        spec.operation
        for spec in _SPECS.values()
        if spec.operation and spec.status in {"implemented_tool", "implemented_candidate_tool"}
    }
