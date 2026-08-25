"""FBI 蓝图统一路由表。

单一事实来源：metric -> family -> operation -> builder -> postcondition。

本模块只声明路由元数据，不实现 builder 逻辑。
builder 仍由 ReviewMinister 提供，路由表通过 builder 名字引用，
这样可以避免循环导入，同时让 _issue_family() 和 _tool_blueprint_command()
共用同一张表。

设计原则（见 docs/04-主编系统/FBI蓝图路由统一修改指南.md）：
- Review Minister 负责归类、选择策略、生成 tool_blueprint、声明 postconditions
- ToolExecutor 只执行明确工具命令
- 蓝图失败必须可观测，不能静默返回 None
- postcondition 验证补丁效果，不验证诊断语是否被照抄
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class MetricBlueprintRoute:
    """单条 metric 的蓝图路由声明。"""

    metric: str
    family: str
    repair_domain: str
    repair_lane: str
    operation: str
    builder: str
    requires_anchor: bool = True
    anchor_strategy: str = "localized_target_span"
    fallback_anchor_strategy: str = "semantic_anchor"
    postcondition_strategy: str = "replacement_text_present"
    # 方案5 B1：标记此 metric 是否必须走 LLM 创作路径。
    # True = 需要 LLM 创作（如冲突密度、悬念设计、具象化扩写）。
    # False = 纯模式匹配/精确替换，可由确定性工具直接修复。
    requires_llm: bool = True

    def matches(self, metric: str, validator: str = "") -> bool:
        return self.metric == metric


# ---------------------------------------------------------------------------
# 第一批核心 metric 路由
# ---------------------------------------------------------------------------

_ROUTES: list[MetricBlueprintRoute] = [
    # --- 6.1 结构合同类 ---
    # family=structure, operation=insert_after_anchor, builder=build_contract_completion_insert
    # postcondition 验证动作关键词，不要求诊断句逐字出现
    MetricBlueprintRoute(
        metric="ending_state_not_reached",
        family="structure",
        repair_domain="structure",
        repair_lane="contract_completion",
        operation="insert_after_anchor",
        builder="build_contract_completion_insert",
        requires_anchor=True,
        anchor_strategy="last_localized_or_last_action_sentence",
        fallback_anchor_strategy="last_action_sentence",
        postcondition_strategy="contract_action_keywords",
        # 正文未展现结局状态，需要 LLM 创作展现结局状态的正文段落
        requires_llm=True,
    ),
    MetricBlueprintRoute(
        metric="missing_must_show",
        family="structure",
        repair_domain="structure",
        repair_lane="contract_completion",
        operation="insert_after_anchor",
        builder="build_contract_completion_insert",
        requires_anchor=True,
        anchor_strategy="last_localized_or_last_action_sentence",
        fallback_anchor_strategy="last_action_sentence",
        postcondition_strategy="contract_action_keywords",
        # missing_must_show 的本质是"正文缺少必须展现的情节"，需要 LLM 创作型补全
        # （写一段真正展现必须情节的小说正文），不是简单的模板句插入。
        # 确定性 builder _contract_completion_sentence 只能把 expected_behavior 诊断语
        # 当正文写入，导致 recheck 仍判定缺失，2 轮修复耗尽后进入 waiting_review。
        # 改为 requires_llm=True 后，走 llm_creative_rewrite 路径，由 LLM 根据 violation
        # hint（detail/expected_behavior）创作真正的情节段落。
        requires_llm=True,
    ),
    MetricBlueprintRoute(
        metric="goal_present",
        family="structure",
        repair_domain="structure",
        repair_lane="contract_completion",
        operation="insert_after_anchor",
        builder="build_contract_completion_insert",
        requires_anchor=True,
        anchor_strategy="last_localized_or_last_action_sentence",
        fallback_anchor_strategy="last_action_sentence",
        postcondition_strategy="contract_action_keywords",
        # 场景目标未体现，需要 LLM 创作体现目标的正文段落
        requires_llm=True,
    ),
    MetricBlueprintRoute(
        metric="conflict_present",
        family="structure",
        repair_domain="structure",
        repair_lane="contract_completion",
        operation="insert_after_anchor",
        builder="build_contract_completion_insert",
        requires_anchor=True,
        anchor_strategy="last_localized_or_last_action_sentence",
        fallback_anchor_strategy="last_action_sentence",
        postcondition_strategy="contract_action_keywords",
        # 冲突未体现，需要 LLM 创作体现冲突的正文段落
        requires_llm=True,
    ),
    MetricBlueprintRoute(
        metric="value_change",
        family="structure",
        repair_domain="structure",
        repair_lane="contract_completion",
        operation="insert_after_anchor",
        builder="build_contract_completion_insert",
        requires_anchor=True,
        anchor_strategy="last_localized_or_last_action_sentence",
        fallback_anchor_strategy="last_action_sentence",
        postcondition_strategy="contract_action_keywords",
        # 价值变化未体现，需要 LLM 创作体现价值变化的正文段落
        requires_llm=True,
    ),
    MetricBlueprintRoute(
        metric="scene_goal_missing",
        family="structure",
        repair_domain="structure",
        repair_lane="contract_completion",
        operation="insert_after_anchor",
        builder="build_contract_completion_insert",
        requires_anchor=True,
        anchor_strategy="last_localized_or_last_action_sentence",
        fallback_anchor_strategy="last_action_sentence",
        postcondition_strategy="contract_action_keywords",
        # 场景目标缺失，需要 LLM 创作体现场景目标的正文段落
        requires_llm=True,
    ),
    # --- 6.2 抽象解释具体化类 ---
    # family=show_evidence，不再被 anti_ai_local 提前截走
    MetricBlueprintRoute(
        metric="abstraction_over_budget",
        family="show_evidence",
        repair_domain="prose",
        repair_lane="ground_abstract_claim",
        operation="insert_after_anchor",
        builder="build_show_evidence_patch",
        requires_anchor=True,
        anchor_strategy="localized_abstract_sentence_or_nearby_action",
        fallback_anchor_strategy="nearest_action_sentence",
        postcondition_strategy="inserted_observable_evidence",
    ),
    MetricBlueprintRoute(
        metric="standalone_abstract_claims",
        family="show_evidence",
        repair_domain="prose",
        repair_lane="ground_abstract_claim",
        operation="insert_after_anchor",
        builder="build_show_evidence_patch",
        requires_anchor=True,
        anchor_strategy="localized_abstract_sentence_or_nearby_action",
        fallback_anchor_strategy="nearest_action_sentence",
        postcondition_strategy="inserted_observable_evidence",
    ),
    MetricBlueprintRoute(
        metric="abstract_bare_count",
        family="show_evidence",
        repair_domain="prose",
        repair_lane="ground_abstract_claim",
        operation="insert_after_anchor",
        builder="build_show_evidence_patch",
        requires_anchor=True,
        anchor_strategy="localized_abstract_sentence_or_nearby_action",
        fallback_anchor_strategy="nearest_action_sentence",
        postcondition_strategy="inserted_observable_evidence",
    ),
    # --- 6.3 商业压力与冲突密度类 ---
    # family=rhythm，但 builder 不再被 family != "rhythm" 拦截
    MetricBlueprintRoute(
        metric="low_conflict_density",
        family="rhythm",
        repair_domain="rhythm",
        repair_lane="commercial_pressure",
        operation="insert_after_anchor",
        builder="build_commercial_pressure_insert",
        requires_anchor=True,
        anchor_strategy="pressure_or_action_sentence",
        fallback_anchor_strategy="first_or_middle_action_sentence",
        postcondition_strategy="pressure_keywords",
    ),
    MetricBlueprintRoute(
        metric="flat_pressure_ramp",
        family="rhythm",
        repair_domain="rhythm",
        repair_lane="commercial_pressure",
        operation="insert_after_anchor",
        builder="build_commercial_pressure_insert",
        requires_anchor=True,
        anchor_strategy="pressure_or_action_sentence",
        fallback_anchor_strategy="first_or_middle_action_sentence",
        postcondition_strategy="pressure_keywords",
    ),
    MetricBlueprintRoute(
        metric="weak_curiosity_engine",
        family="rhythm",
        repair_domain="rhythm",
        repair_lane="commercial_hook",
        operation="insert_after_anchor",
        builder="build_commercial_hook_insert",
        requires_anchor=True,
        anchor_strategy="question_or_unknown_sentence",
        fallback_anchor_strategy="first_action_sentence",
        postcondition_strategy="question_keywords",
    ),
    MetricBlueprintRoute(
        metric="weak_opening_hook",
        family="rhythm",
        repair_domain="rhythm",
        repair_lane="commercial_hook",
        operation="insert_after_anchor",
        builder="build_commercial_hook_insert",
        requires_anchor=True,
        anchor_strategy="question_or_unknown_sentence",
        fallback_anchor_strategy="first_action_sentence",
        postcondition_strategy="question_keywords",
    ),
    MetricBlueprintRoute(
        metric="weak_chapter_end_hook",
        family="rhythm",
        repair_domain="rhythm",
        repair_lane="commercial_hook",
        operation="insert_after_anchor",
        builder="build_commercial_hook_insert",
        requires_anchor=True,
        anchor_strategy="question_or_unknown_sentence",
        fallback_anchor_strategy="last_action_sentence",
        postcondition_strategy="question_keywords",
    ),
    MetricBlueprintRoute(
        metric="missing_micro_payoff",
        family="rhythm",
        repair_domain="rhythm",
        repair_lane="commercial_payoff",
        operation="insert_after_anchor",
        builder="build_commercial_payoff_insert",
        requires_anchor=True,
        anchor_strategy="partial_success_or_state_change_sentence",
        fallback_anchor_strategy="last_action_sentence",
        postcondition_strategy="payoff_keywords",
    ),
    # --- 6.4 事实一致性类（底层 blocking 类型纳入统一路由）---
    MetricBlueprintRoute(
        metric="fact_conflict",
        family="fact",
        repair_domain="fact",
        repair_lane="fact_resolution",
        operation="replace_exact",
        builder="build_fact_conflict_patch",
        requires_anchor=True,
        anchor_strategy="localized_target_span",
        fallback_anchor_strategy="nearest_action_sentence",
        postcondition_strategy="fact_resolved",
        requires_llm=True,
    ),
    # --- 6.4.1 空间/身份/同时在场冲突类（统一走 fact family）---
    # 审查层会产出更细的 metric，只要语义上属于事实/空间/身份冲突，
    # 都要进同一个事实修复通道。不能只认 fact_conflict。
    MetricBlueprintRoute(
        metric="spatial_conflict",
        family="fact",
        repair_domain="fact",
        repair_lane="fact_resolution",
        operation="replace_exact",
        builder="build_spatial_conflict_patch",
        requires_anchor=True,
        anchor_strategy="evidence_span_first",
        fallback_anchor_strategy="localized_context_window",
        postcondition_strategy="conflict_claim_removed_or_disambiguated",
        # 空间矛盾通常需要 LLM 创作型改写（如解释裂缝如何出现、移除矛盾入口），
        # 确定性 builder _generate_spatial_replacement 只能处理简单模板替换，
        # 无法处理合同级空间矛盾（如"唯一出入口已封"vs"从裂缝进入"）。
        requires_llm=True,
    ),
    MetricBlueprintRoute(
        metric="spatial_consistency_error",
        family="fact",
        repair_domain="fact",
        repair_lane="fact_resolution",
        operation="replace_exact",
        builder="build_spatial_conflict_patch",
        requires_anchor=True,
        anchor_strategy="evidence_span_first",
        fallback_anchor_strategy="localized_context_window",
        postcondition_strategy="conflict_claim_removed_or_disambiguated",
        requires_llm=True,
    ),
    MetricBlueprintRoute(
        metric="location_conflict",
        family="fact",
        repair_domain="fact",
        repair_lane="fact_resolution",
        operation="replace_exact",
        builder="build_spatial_conflict_patch",
        requires_anchor=True,
        anchor_strategy="evidence_span_first",
        fallback_anchor_strategy="localized_context_window",
        postcondition_strategy="conflict_claim_removed_or_disambiguated",
        requires_llm=True,
    ),
    MetricBlueprintRoute(
        metric="simultaneous_presence_conflict",
        family="fact",
        repair_domain="fact",
        repair_lane="fact_resolution",
        operation="replace_exact",
        builder="build_spatial_conflict_patch",
        requires_anchor=True,
        anchor_strategy="evidence_span_first",
        fallback_anchor_strategy="localized_context_window",
        postcondition_strategy="conflict_claim_removed_or_disambiguated",
        requires_llm=True,
    ),
    MetricBlueprintRoute(
        metric="identity_location_conflict",
        family="fact",
        repair_domain="fact",
        repair_lane="fact_resolution",
        operation="replace_exact",
        builder="build_spatial_conflict_patch",
        requires_anchor=True,
        anchor_strategy="evidence_span_first",
        fallback_anchor_strategy="localized_context_window",
        postcondition_strategy="conflict_claim_removed_or_disambiguated",
        requires_llm=True,
    ),
    MetricBlueprintRoute(
        metric="clue_provenance_error",
        family="fact",
        repair_domain="fact",
        repair_lane="clue_provenance_fix",
        operation="replace_exact",
        builder="build_clue_provenance_patch",
        requires_anchor=True,
        anchor_strategy="localized_target_span",
        fallback_anchor_strategy="nearest_action_sentence",
        postcondition_strategy="fact_resolved",
        requires_llm=True,
    ),
    MetricBlueprintRoute(
        metric="causal_chain_error",
        family="fact",
        repair_domain="fact",
        repair_lane="causal_chain_fix",
        operation="insert_after_anchor",
        builder="build_causal_chain_patch",
        requires_anchor=True,
        anchor_strategy="localized_target_span",
        fallback_anchor_strategy="last_action_sentence",
        postcondition_strategy="causal_link_keywords",
        requires_llm=True,
    ),
    MetricBlueprintRoute(
        metric="not_but_pattern",
        family="anti_ai_local",
        repair_domain="prose",
        repair_lane="not_but_rewrite",
        operation="replace_exact",
        builder="build_not_but_pattern_patch",
        requires_anchor=True,
        anchor_strategy="localized_target_span",
        fallback_anchor_strategy="nearest_action_sentence",
        postcondition_strategy="pattern_removed",
        requires_llm=False,
    ),
    # --- 通用 anti_ai / show_evidence 路由 ---
    # 这些 metric 之前走旧 fallback 生成意图型 operation，现统一注册为 replace_exact，
    # 由通用 builder 从 violation 结构化字段提取 old_text/new_text。
    MetricBlueprintRoute(
        metric="tier1_hit_count",
        family="anti_ai_local",
        repair_domain="anti_ai",
        repair_lane="prose_local_patch",
        operation="replace_exact",
        builder="build_anti_ai_term_replace",
        requires_anchor=True,
        anchor_strategy="localized_target_span",
        fallback_anchor_strategy="first_evidence_span",
        postcondition_strategy="ai_flavor_term_removed",
        requires_llm=False,
    ),
    MetricBlueprintRoute(
        metric="vague_attribution_count",
        family="anti_ai_local",
        repair_domain="anti_ai",
        repair_lane="prose_local_patch",
        operation="replace_exact",
        builder="build_anti_ai_term_replace",
        requires_anchor=True,
        anchor_strategy="localized_target_span",
        fallback_anchor_strategy="first_evidence_span",
        postcondition_strategy="vague_attribution_removed",
        requires_llm=False,
    ),
    MetricBlueprintRoute(
        metric="emotion_label_count",
        family="show_evidence",
        repair_domain="specificity",
        repair_lane="ground_abstract_claim",
        operation="replace_exact",
        builder="build_emotion_to_evidence_replace",
        requires_anchor=True,
        anchor_strategy="localized_target_span",
        fallback_anchor_strategy="first_evidence_span",
        postcondition_strategy="emotion_label_replaced",
    ),
    MetricBlueprintRoute(
        metric="head_hopping_count",
        family="pov",
        repair_domain="pov",
        repair_lane="voice_repair",
        operation="replace_exact",
        builder="build_pov_externalization_replace",
        requires_anchor=True,
        anchor_strategy="localized_target_span",
        fallback_anchor_strategy="first_evidence_span",
        postcondition_strategy="pattern_removed",
        requires_llm=True,
    ),
    MetricBlueprintRoute(
        metric="uniform_sentence_streak_max",
        family="anti_ai_discourse",
        repair_domain="anti_ai",
        repair_lane="paragraph_reconstruction",
        operation="replace_exact",
        builder="build_uniform_streak_replace",
        requires_anchor=True,
        anchor_strategy="localized_target_span",
        fallback_anchor_strategy="first_evidence_span",
        postcondition_strategy="sentence_length_varied",
    ),
    # --- 6.5 风格类 metric 路由（方案 26 Part D）---
    # family=style_consistency：禁用词 / avoid 模式违反 → 精确替换
    MetricBlueprintRoute(
        metric="forbidden_word",
        family="style_consistency",
        repair_domain="prose",
        repair_lane="style_local_patch",
        operation="replace_exact",
        builder="build_forbidden_word_replace",
        requires_anchor=True,
        anchor_strategy="localized_target_span",
        fallback_anchor_strategy="first_evidence_span",
        postcondition_strategy="pattern_removed",
        requires_llm=False,
    ),
    MetricBlueprintRoute(
        metric="avoid_pattern_violation",
        family="style_consistency",
        repair_domain="prose",
        repair_lane="style_local_patch",
        operation="replace_exact",
        builder="build_forbidden_word_replace",
        requires_anchor=True,
        anchor_strategy="localized_target_span",
        fallback_anchor_strategy="first_evidence_span",
        postcondition_strategy="pattern_removed",
        requires_llm=False,
    ),
    # family=structure_word_cluster：结构词堆积 → normalize_structure_words
    MetricBlueprintRoute(
        metric="structure_word_cluster_count",
        family="structure_word_cluster",
        repair_domain="prose",
        repair_lane="structure_word_cleanup",
        operation="normalize_structure_words",
        builder="build_structure_word_normalize",
        requires_anchor=True,
        anchor_strategy="localized_target_span",
        fallback_anchor_strategy="first_evidence_span",
        postcondition_strategy="pattern_removed",
        requires_llm=False,
    ),
    # family=sentence_rhythm：句式节奏重复 → vary_sentence_shape
    MetricBlueprintRoute(
        metric="uniform_sentence_streak",
        family="sentence_rhythm",
        repair_domain="prose",
        repair_lane="sentence_shape_variation",
        operation="vary_sentence_shape",
        builder="build_sentence_rhythm_vary",
        requires_anchor=True,
        anchor_strategy="localized_target_span",
        fallback_anchor_strategy="first_evidence_span",
        postcondition_strategy="sentence_length_varied",
        requires_llm=False,
    ),
    # family=paragraph_shape：段落形态重复 → split_paragraph
    MetricBlueprintRoute(
        metric="paragraph_shape_repeat",
        family="paragraph_shape",
        repair_domain="prose",
        repair_lane="paragraph_shape_fix",
        operation="split_paragraph",
        builder="build_paragraph_shape_split",
        requires_anchor=True,
        anchor_strategy="localized_target_span",
        fallback_anchor_strategy="first_evidence_span",
        postcondition_strategy="sentence_length_varied",
        requires_llm=False,
    ),
    # --- P2-18：Part D 三个 LLM 风格工具路由（方案 26）---
    # 文档《方案26 Part D》列出 3 个 LLM 风格工具此前未注册到统一路由表，
    # 导致审查蓝图官遇到 voice_phrase_drift / missing_specific_detail_anchor
    # 时无路可走，只能走旧 fallback。
    # 注：abstract_telling_in_style 是幽灵 metric（无 detector 产出、builder 不存在），
    # 方案 30 §4.9 已清理其路由声明。
    # requires_llm=True 表示 WorkUnitBuilder 必须走 LLM 路径生成蓝图。
    # family=voice_drift：角色声线偏移 → tighten_voice_phrase（LLM 改写声线窗口）
    MetricBlueprintRoute(
        metric="voice_phrase_drift",
        family="voice_drift",
        repair_domain="prose",
        repair_lane="voice_phrase_correction",
        operation="tighten_voice_phrase",
        builder="build_voice_phrase_tighten",
        requires_anchor=True,
        anchor_strategy="localized_target_span",
        fallback_anchor_strategy="first_evidence_span",
        postcondition_strategy="voice_phrase_aligned",
        requires_llm=True,
    ),
    # family=style_consistency：缺少具体细节锚点 → add_specific_detail_anchor（LLM 补写）
    MetricBlueprintRoute(
        metric="missing_specific_detail_anchor",
        family="style_consistency",
        repair_domain="prose",
        repair_lane="detail_anchor_injection",
        operation="add_specific_detail_anchor",
        builder="build_specific_detail_anchor_insert",
        requires_anchor=True,
        anchor_strategy="localized_target_span",
        fallback_anchor_strategy="nearest_action_sentence",
        postcondition_strategy="inserted_observable_evidence",
        requires_llm=True,
    ),
    # --- P2-9：ai_punctuation_artifact 路由注册 ---
    # family=anti_ai_punctuation：AI 标点痕迹（破折号密度异常等）→ normalize_punctuation
    # 确定性 operation，由 ToolExecutor 直接执行标点归一化，不需要 LLM 创作。
    # requires_anchor=False：标点清理作用于整段/窗口，无需锚定到具体句。
    MetricBlueprintRoute(
        metric="ai_punctuation_artifact",
        family="anti_ai_punctuation",
        repair_domain="prose",
        repair_lane="punctuation_cleanup",
        operation="normalize_punctuation",
        builder="build_punctuation_normalize",
        requires_anchor=False,
        anchor_strategy="localized_target_span",
        fallback_anchor_strategy="first_evidence_span",
        postcondition_strategy="pattern_removed",
        requires_llm=False,
    ),
    # --- 循环#9：ai_simile_overuse 路由注册 ---
    # family=anti_ai_local：明喻密度过高（"像"字 + 完整明喻结构）属于本地 AI 味密度问题。
    # 设计意图（通用修复 S-1）：密度类问题需要 LLM 系统性降低全文密度，
    # advisory 携带 evidence_samples（所有命中位置），让 lane 指令指示 LLM 一次性处理，
    # 而非只改 target_span 一处。因此 requires_llm=True，operation=llm_creative_rewrite，
    # 由 SceneRepairer（LLM 创作路径）执行，不经过 ToolExecutor。
    # requires_anchor=False：密度类作用于多个位置，无需锚定到单句（同 ai_punctuation_artifact）。
    # 此前该 metric 未注册路由，导致 _tool_blueprint_command 返回 None，
    # blueprint 被标记为 blueprint_incomplete，修复从未执行（循环#9 bc5f286c 根因）。
    MetricBlueprintRoute(
        metric="ai_simile_overuse",
        family="anti_ai_local",
        repair_domain="anti_ai",
        repair_lane="prose_local_patch",
        operation="llm_creative_rewrite",
        builder="build_llm_creative_rewrite",
        requires_anchor=False,
        anchor_strategy="localized_target_span",
        fallback_anchor_strategy="first_evidence_span",
        postcondition_strategy="simile_density_reduced",
        requires_llm=True,
    ),
    # --- 元叙事引用清理（章节号标记） ---
    # chapter_reference：正文中出现"第N章"等元叙事标记，确定性可修复（_patch_chapter_references）。
    # requires_llm=False 让 _deterministic_local_patch 守卫允许确定性修复路径执行。
    MetricBlueprintRoute(
        metric="chapter_reference",
        family="meta_reference",
        repair_domain="prose",
        repair_lane="meta_reference_cleanup",
        operation="remove_chapter_reference",
        builder="build_chapter_reference_remove",
        requires_anchor=False,
        anchor_strategy="localized_target_span",
        fallback_anchor_strategy="first_evidence_span",
        postcondition_strategy="pattern_removed",
        requires_llm=False,
    ),
    MetricBlueprintRoute(
        metric="meta_chapter_reference",
        family="meta_reference",
        repair_domain="prose",
        repair_lane="meta_reference_cleanup",
        operation="remove_chapter_reference",
        builder="build_chapter_reference_remove",
        requires_anchor=False,
        anchor_strategy="localized_target_span",
        fallback_anchor_strategy="first_evidence_span",
        postcondition_strategy="pattern_removed",
        requires_llm=False,
    ),
    # --- 循环#10：补齐 ch24 blocking metric 的蓝图路由 ---
    # 这两个 metric 之前未注册路由，虽然默认走 LLM 路径（requires_llm=True），
    # 但缺少路由声明导致可观测性不足（无法在路由表中追踪修复状态）。
    # 注册后与同族 metric 保持一致的路由模式。
    # body_language_cliche_count：身体语言陈词滥调（皱眉/咬唇/握拳等模板化动作），
    # 属于 show_evidence family，需要 LLM 改写为具体可观察的动作。
    # 同 abstraction_over_budget / standalone_abstract_claims 模式。
    MetricBlueprintRoute(
        metric="body_language_cliche_count",
        family="show_evidence",
        repair_domain="prose",
        repair_lane="ground_abstract_claim",
        operation="insert_after_anchor",
        builder="build_show_evidence_patch",
        requires_anchor=True,
        anchor_strategy="localized_abstract_sentence_or_nearby_action",
        fallback_anchor_strategy="nearest_action_sentence",
        postcondition_strategy="inserted_observable_evidence",
        requires_llm=True,
    ),
    # pure_exposition_block_chars：纯陈述大段（无动作/对话/感官的说明性段落），
    # 属于 structure family（场景结构缺失：缺少场景化节拍）。
    # 需要 LLM 将纯陈述拆分/改写为含动作和对话的场景化文本。
    MetricBlueprintRoute(
        metric="pure_exposition_block_chars",
        family="structure",
        repair_domain="structure",
        repair_lane="contract_completion",
        operation="insert_after_anchor",
        builder="build_pure_exposition_split",
        requires_anchor=True,
        anchor_strategy="last_localized_or_last_action_sentence",
        fallback_anchor_strategy="last_action_sentence",
        postcondition_strategy="contract_action_keywords",
        requires_llm=True,
    ),
    # 循环#10：temporal_anchor_count 蓝图路由
    # 根因：temporal_anchor_count 未在 _ROUTES 中注册，Review Minister 无法通过路由表
    #   生成 tool_blueprint，蓝图被标记为 blueprint_incomplete 或走旧 fallback 路径，
    #   导致修复质量低。虽然 L9 后检能工作（_TENSE_CONSISTENCY_METRICS 包含此指标），
    #   但蓝图生成阶段产出低质量补丁，LLM 收不到明确的修复指令。
    # 修复：注册到 tense family，与同族时态指标（tense_drift_count 等）保持一致。
    #   repair_lane=tense_repair（有专门的时间锚点补充指令），operation=insert_after_anchor
    #   （在合适位置插入时间锚点），builder=build_contract_completion_insert（复用现有 builder）。
    MetricBlueprintRoute(
        metric="temporal_anchor_count",
        family="tense",
        repair_domain="surface",
        repair_lane="tense_repair",
        operation="insert_after_anchor",
        builder="build_time_anchor_insert",
        requires_anchor=True,
        anchor_strategy="first_evidence_span",
        fallback_anchor_strategy="last_action_sentence",
        postcondition_strategy="contract_action_keywords",
        requires_llm=True,
    ),
]


_ROUTE_INDEX: dict[str, MetricBlueprintRoute] = {route.metric: route for route in _ROUTES}


def get_blueprint_route(metric: str, validator: str = "") -> MetricBlueprintRoute | None:
    """查统一路由表。返回 None 表示该 metric 没有统一路由，应走旧 fallback。"""
    if not metric:
        return None
    return _ROUTE_INDEX.get(metric.strip().lower())


def registered_metrics() -> list[str]:
    """返回所有已注册路由的 metric 名，供测试使用。"""
    return [route.metric for route in _ROUTES]


def route_family_for_metric(metric: str, validator: str = "") -> str | None:
    """仅返回 family，供 _issue_family() 优先查路由表使用。"""
    route = get_blueprint_route(metric, validator)
    return route.family if route is not None else None


# ---------------------------------------------------------------------------
# Phase U-C: 统一 operation 协议——单一事实来源
# route builder / validator / work unit / tool executor 必须共用本白名单。
# ---------------------------------------------------------------------------

# 可被 ToolExecutor 直接执行的确定性 operation。
# normalize_punctuation 虽然不携带单个 old/new 文本，但 ToolExecutor 会先在
# frozen base 上把它展开为逐个字符区间的 RepairPatch，因此仍属于有界确定性操作。
# 其余意图型 operation 必须先被 WorkUnitBuilder 编译为下列之一。
DETERMINISTIC_PATCH_OPS: frozenset[str] = frozenset({
    "replace_exact",
    "delete_exact",
    "insert_before_anchor",
    "insert_after_anchor",
    "normalize_punctuation",
})

# route builder 产出的 exact operation 别名 → executor 接收的标准 operation。
# WorkUnitBuilder 通过此映射把 route builder 输出归一化为 executor 白名单。
EXACT_OPERATION_ALIASES: dict[str, str] = {
    "replace_exact": "replace_exact",
    "delete_exact": "delete_exact",
    "insert_after_exact": "insert_after_anchor",
    "insert_before_exact": "insert_before_anchor",
}

# 意图型 operation：只能作为蓝图意图，不得进入 ToolExecutor。
# WorkUnitBuilder 必须把它们编译为 DETERMINISTIC_PATCH_OPS 之一，否则蓝图标
# blueprint_incomplete / needs_blueprint_completion。
INTENT_OPERATIONS: frozenset[str] = frozenset({
    "replace_span",
    "replace_literal",
    "rewrite_window",
    "delete_span",
    "insert_anchor",
    "insert_transition_anchor",
    "insert_time_anchor",
    "insert_pressure_cost",
    "insert_conflict_beat",
    "insert_hook_beat",
    "insert_micro_payoff",
    "insert_functional_breathing_paragraph",
    "smooth_abrupt_shift",
    "rewrite_voice_window",
    "split_paragraph",
    "merge_paragraphs",
    "normalize_structure_words",
    "replace_tier1_ai_flavor_terms",
    "cleanup_ai_flavor_window",
    "trim_discourse_window",
    "vary_sentence_shape",
    "vary_sentence_length_window",
    "ensure_required_spans",
    "suppress_false_positive",
    "mark_needs_human",
    "mark_unrepairable_by_tool",
    # P2-18：方案 26 Part D 三个 LLM 风格工具 operation
    "tighten_voice_phrase",
    "replace_abstract_telling",
    "add_specific_detail_anchor",
})

# 附录4问题16修复：LLM 创作型 operation 白名单。
# 这些 operation 由 SceneRepairer（LLM 创作路径）处理，不经过 ToolExecutor，
# 也不需要编译为确定性 patch。BlueprintProtocolValidator 识别此白名单后
# 使用专用失败码 llm_creative_operation_route_to_scene_repairer，
# chapter_repair_executor._apply_blueprint_protocol_gate 中的 has_llm_creative_fallback
# 逻辑据此保持 order pending，由 _execute_orders_for_scene 走 LLM 创作兜底。
LLM_CREATIVE_OPS: frozenset[str] = frozenset({
    "llm_creative_rewrite",
})


def is_executable_operation(operation: str) -> bool:
    """判断 operation 是否可直接进入 ToolExecutor。"""
    return operation in DETERMINISTIC_PATCH_OPS


# ---------------------------------------------------------------------------
# 方案4 A2：ISSUE_CONTEXT_TEMPLATE——审查官硬编码骨架
# ---------------------------------------------------------------------------
# 每个 issue 类型定义"必读什么 / 必输出什么 / 约束什么"，用于引导 LLM 注意力。
# 这是按 issue_type 分类的元数据配置（类似路由表本身），不是针对特定小说
# 内容的特化修复：所有小说 / 章节 / 角色名走同一套骨架。
# 未覆盖的 issue_type 走默认模板，调用方无需特判。


@dataclass(frozen=True)
class IssueContextTemplate:
    """单个 issue 类型的上下文骨架。

    设计原则：与 MetricBlueprintRoute 同样为 frozen dataclass，
    字段在构造时一次性确定，运行期不可变。to_dict() 返回 list 副本，
    避免调用方误改模块级常量。
    """

    issue_type: str
    must_read: list[str]
    must_output: list[str]
    constraints: list[str]

    def to_dict(self) -> dict:
        """转换为 dict 供调用方使用（list 字段返回副本）。"""
        return {
            "issue_type": self.issue_type,
            "must_read": list(self.must_read),
            "must_output": list(self.must_output),
            "constraints": list(self.constraints),
        }


# 默认模板：未在 ISSUE_CONTEXT_TABLE 显式注册的 issue_type 走此模板。
# 与 A1 路由表 fallback 同义——宁可给一个保守骨架，也不让 LLM 裸跑。
_DEFAULT_ISSUE_CONTEXT_TEMPLATE: IssueContextTemplate = IssueContextTemplate(
    issue_type="_default",
    must_read=["候选正文", "场景合同"],
    must_output=["old_text", "new_text", "anchor_text"],
    constraints=["patch长度≥20字", "保持POV一致"],
)


# issue_type → 上下文骨架的单一事实来源。
# 新增 issue_type 时只需在此表追加条目，无需修改 get_issue_context_template()。
# 列表覆盖方案4文档要求的全部 issue 类型；与 A1 路由表相比，此处额外包含
# ai_punctuation_artifact / sentence_length_variance 等尚未注册路由的 issue，
# 因为 A2 是审查官引导骨架，覆盖面应宽于确定性路由表。
ISSUE_CONTEXT_TABLE: dict[str, IssueContextTemplate] = {
    "fact_conflict": IssueContextTemplate(
        issue_type="fact_conflict",
        must_read=["人物完整卡", "铁则规则", "前文相关段落"],
        must_output=["old_text", "new_text", "anchor_text", "before_context", "after_context"],
        constraints=["不得改变其他事实", "保持POV一致", "patch长度≥20字"],
    ),
    "low_conflict_density": IssueContextTemplate(
        issue_type="low_conflict_density",
        must_read=["场景合同", "本章大纲", "POV角色卡"],
        must_output=["old_text", "new_text", "anchor_text"],
        constraints=["新增冲突必须服务于场景目标", "不得引入新角色", "patch长度50-300字"],
    ),
    "clue_provenance_error": IssueContextTemplate(
        issue_type="clue_provenance_error",
        must_read=["场景合同的clues字段", "人物卡"],
        must_output=["old_text", "new_text", "anchor_text", "clue_source"],
        constraints=["线索必须有来源", "不得凭空出现线索", "patch长度≥20字"],
    ),
    "causal_chain_error": IssueContextTemplate(
        issue_type="causal_chain_error",
        must_read=["前文相关段落", "场景合同goal/conflict"],
        must_output=["old_text", "new_text", "anchor_text", "causal_link"],
        constraints=["因果链必须连贯", "不得跳跃", "patch长度50-500字"],
    ),
    "ai_punctuation_artifact": IssueContextTemplate(
        issue_type="ai_punctuation_artifact",
        must_read=["候选正文"],
        must_output=["old_text", "new_text"],
        constraints=["仅替换标点", "不改语义", "patch长度1-50字"],
    ),
    "uniform_sentence_streak_max": IssueContextTemplate(
        issue_type="uniform_sentence_streak_max",
        must_read=["候选正文", "POV角色卡"],
        must_output=["old_text", "new_text", "anchor_text"],
        constraints=["句式需多样化", "不得重复同句式", "patch长度20-500字"],
    ),
    "abstraction_over_budget": IssueContextTemplate(
        issue_type="abstraction_over_budget",
        must_read=["场景合同", "风格约束"],
        must_output=["old_text", "new_text", "anchor_text"],
        constraints=["用具体细节替代抽象", "patch长度50-300字"],
    ),
    "weak_curiosity_engine": IssueContextTemplate(
        issue_type="weak_curiosity_engine",
        must_read=["场景合同", "本章大纲", "伏笔状态"],
        must_output=["old_text", "new_text", "anchor_text"],
        constraints=["增强好奇心", "不得直接揭示", "patch长度50-300字"],
    ),
    "vague_attribution_count": IssueContextTemplate(
        issue_type="vague_attribution_count",
        must_read=["候选正文"],
        must_output=["old_text", "new_text"],
        constraints=["明确归因主体", "patch长度10-100字"],
    ),
    "not_but_pattern": IssueContextTemplate(
        issue_type="not_but_pattern",
        must_read=["候选正文"],
        must_output=["old_text", "new_text"],
        constraints=["改为肯定句式", "patch长度10-200字"],
    ),
    "tier1_hit_count": IssueContextTemplate(
        issue_type="tier1_hit_count",
        must_read=["候选正文"],
        must_output=["old_text", "new_text"],
        constraints=["替换AI常用词", "patch长度1-50字"],
    ),
    "sentence_length_variance": IssueContextTemplate(
        issue_type="sentence_length_variance",
        must_read=["候选正文"],
        must_output=["old_text", "new_text", "anchor_text"],
        constraints=["拆分长句或合并短句", "patch长度20-500字"],
    ),
}


def get_issue_context_template(issue_type: str) -> dict:
    """查询 issue_type 的上下文骨架。

    未注册的 issue_type 返回默认模板：
        must_read    = ["候选正文", "场景合同"]
        must_output  = ["old_text", "new_text", "anchor_text"]
        constraints  = ["patch长度≥20字", "保持POV一致"]

    返回 dict 结构：
        {
            "issue_type": str,
            "must_read": list[str],
            "must_output": list[str],
            "constraints": list[str],
        }
    """
    if not issue_type:
        return _DEFAULT_ISSUE_CONTEXT_TEMPLATE.to_dict()
    template = ISSUE_CONTEXT_TABLE.get(issue_type.strip().lower())
    if template is None:
        return _DEFAULT_ISSUE_CONTEXT_TEMPLATE.to_dict()
    return template.to_dict()


# ---------------------------------------------------------------------------
# 方案4 A3：PATCH_VALIDATION_RULES——patch 合规性验证规则
# ---------------------------------------------------------------------------
# 模块级硬编码规则，所有 patch 必须通过此规则集验证才能进入 ToolExecutor。
# 复杂的语义检查（如 iron_rule 违反、fact_preservation 破坏）留给调用方
# 结合上下文做语义判断，本模块只做格式 / 长度 / meta 术语等结构性检查。


@dataclass(frozen=True)
class PatchValidationRules:
    """patch 验证规则集。

    与 MetricBlueprintRoute 同样为 frozen dataclass，规则在模块级一次性
    确定并通过 PATCH_VALIDATION_RULES 单例暴露。调用方通过
    get_patch_validation_rules() 拿 dict 副本，避免误改常量。
    """

    min_length: int = 20
    max_length: int = 2000
    must_have_anchor: bool = True
    pov_check: bool = True
    meta_term_check: bool = True
    iron_rule_check: bool = True
    fact_preservation: bool = True

    def to_dict(self) -> dict:
        """转换为 dict 供调用方使用。"""
        return {
            "min_length": self.min_length,
            "max_length": self.max_length,
            "must_have_anchor": self.must_have_anchor,
            "pov_check": self.pov_check,
            "meta_term_check": self.meta_term_check,
            "iron_rule_check": self.iron_rule_check,
            "fact_preservation": self.fact_preservation,
        }


# 单一事实来源：patch 验证规则。
# 修改规则只需改此常量，validate_patch() 自动跟随。
PATCH_VALIDATION_RULES: PatchValidationRules = PatchValidationRules()


# meta 术语黑名单：patch 的 new_text 不得出现这些词，否则视为 LLM 在"写说明"
# 而非"写小说正文"。与 min_length/max_length 一样属于结构性检查，不依赖
# 具体小说内容。
_META_TERMS: tuple[str, ...] = ("章节", "段落", "场景", "本文", "上文", "下文")


def find_patch_meta_terms(text: str) -> list[str]:
    """Return control-plane terms that must not be newly written as prose."""
    value = str(text or "")
    return [term for term in _META_TERMS if term in value]


def get_patch_validation_rules() -> dict:
    """返回 patch 验证规则集的 dict 副本。"""
    return PATCH_VALIDATION_RULES.to_dict()


def validate_patch(
    patch: dict,
    pov_character: str = "",
    iron_rules: list = None,
    established_facts: list = None,
) -> tuple[bool, list[str]]:
    """验证 patch 是否符合 PATCH_VALIDATION_RULES。

    patch 格式: {"old_text": str, "new_text": str, "anchor_text": str, ...}

    检查项：
    - new_text 长度在 [min_length, max_length] 范围内
    - anchor_text 非空（如果 must_have_anchor）
    - new_text 不含 meta 术语（如果 meta_term_check）：
      章节 / 段落 / 场景 / 本文 / 上文 / 下文
    - pov_check：pov_character 非空时，检查 new_text 中是否出现 pov_character
      （基本检查；严格的 POV 一致性由调用方结合上下文判断）
    - iron_rule_check / fact_preservation：仅做参数类型基本校验，
      复杂的语义判断留给调用方

    返回 (是否通过, 错误列表)。错误列表为空表示通过。
    """
    errors: list[str] = []

    if not isinstance(patch, dict):
        return False, ["patch 必须是 dict 类型"]

    new_text = str(patch.get("new_text") or "").strip()
    anchor_text = str(patch.get("anchor_text") or "").strip()
    rules = PATCH_VALIDATION_RULES

    # 1. 长度检查
    new_text_len = len(new_text)
    if new_text_len < rules.min_length:
        errors.append(
            f"new_text 长度 {new_text_len} 小于最小长度 {rules.min_length}"
        )
    if new_text_len > rules.max_length:
        errors.append(
            f"new_text 长度 {new_text_len} 超过最大长度 {rules.max_length}"
        )

    # 2. anchor 非空检查
    if rules.must_have_anchor and not anchor_text:
        errors.append("anchor_text 为空，但 must_have_anchor=True")

    # 3. meta 术语检查：new_text 不得出现说明性词汇
    if rules.meta_term_check:
        hit_terms = find_patch_meta_terms(new_text)
        if hit_terms:
            errors.append(
                f"new_text 含 meta 术语 {hit_terms}，不得在正文中出现说明性词汇"
            )

    # 4. POV 基本检查：pov_character 提供时，new_text 应包含该角色名
    #    （宽松检查：POV 角色在场景内活动，正文通常会提到。
    #     严格的 POV 一致性由调用方结合上下文判断。）
    if rules.pov_check and pov_character:
        if pov_character not in new_text:
            errors.append(
                f"pov_check：pov_character '{pov_character}' 未在 new_text 中出现"
            )

    # 5. iron_rule 基本检查：仅校验 iron_rules 参数类型
    #    （实际铁则违反由调用方结合 iron_rules 内容做语义判断。）
    if rules.iron_rule_check and iron_rules is not None:
        if not isinstance(iron_rules, list):
            errors.append("iron_rules 必须是 list 类型")

    # 6. fact_preservation 基本检查：仅校验 established_facts 参数类型
    #    （实际事实破坏由调用方结合 established_facts 做语义判断。）
    if rules.fact_preservation and established_facts is not None:
        if not isinstance(established_facts, list):
            errors.append("established_facts 必须是 list 类型")

    return len(errors) == 0, errors


__all__ = [
    "MetricBlueprintRoute",
    "get_blueprint_route",
    "registered_metrics",
    "route_family_for_metric",
    "DETERMINISTIC_PATCH_OPS",
    "EXACT_OPERATION_ALIASES",
    "INTENT_OPERATIONS",
    "is_executable_operation",
    # 方案4 A2：issue 上下文骨架
    "IssueContextTemplate",
    "ISSUE_CONTEXT_TABLE",
    "get_issue_context_template",
    # 方案4 A3：patch 验证规则
    "PatchValidationRules",
    "PATCH_VALIDATION_RULES",
    "get_patch_validation_rules",
    "validate_patch",
]
