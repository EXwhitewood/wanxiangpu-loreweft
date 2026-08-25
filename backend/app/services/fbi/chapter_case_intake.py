"""整章案件接入与修复计划生成。

FBIChapterCaseIntakeService:
  接收 ChapterReviewCase，校验场景包可用性，合并去重违规，
  识别跨场景冲突，返回已验证的案件（含 computed case_id）。

FBIChapterRepairPlanner:
  接收已验证的 ChapterReviewCase，分析全章违规，按归因规则
  生成 ChapterRepairOrder 列表，确定修复计划状态，输出 ChapterRepairPlan。
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
import uuid
from typing import Any, Literal

from app.models.chapter_review import (
    ChapterRepairOrder,
    ChapterRepairPlan,
    ChapterReviewCase,
    RepairWorkUnit,
    RevisionBlueprint,
    SceneReviewPacket,
    ToolCommandBatch,
)
from app.services.fbi.review_minister import FBIReviewMinister
from app.services.fbi.edit_window_planner import EditWindowPlanner
from app.services.review_issue_semantics import normalize_violation_semantics
from app.services.review_case_file import case_file_issues_to_violations
from app.services.skill_metric_repair_registry import get_metric_spec
from app.services.metric_registry import canonical_metric_name, get_enforcement, get_metric_info

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

MAX_CHAPTER_REPAIR_ROUNDS = 2
MAX_SCENE_REPAIR_ROUNDS = 2

_LOCAL_TEXT_REPAIR_TYPES = {
    "ai_punctuation_artifact",
    "abstraction_over_budget",
    "low_conflict_density",
    "flat_pressure_ramp",
    "weak_curiosity_engine",
    "missing_micro_payoff",
    "fact_conflict",
    "internal_conflict",
    "clue_provenance_error",
    "clue_provenance_error_proposition",
    "unprovenanced_clue",
    "chapter_reference",
    "meta_chapter_reference",
    "meta_narrative_reference",
    "timeline_conflict",
    "temporal_conflict",
    "dash_density",
    "dash_per_1000",
    "tier1_hit_count",
    "sentence_shell_count",
    "structure_word_cluster_count",
    "paragraph_shape_repeat_count",
    "uniform_sentence_streak_max",
    "pov_consistency",
    "single_pov_per_scene",
    "head_hopping",
    "head_hopping_count",
    "voice_fingerprint",
}

_POV_BOUNDARY_TYPES = {
    "pov",
    "pov_consistency",
    "single_pov_per_scene",
    "head_hopping",
    "head_hopping_count",
}

_SCENE_OUTCOME_REPAIR_TYPES = {
    "missing_must_show",
    "ending_state_not_reached",
    # 通用修复 G-2：goal_present 走 contract_completion lane
    # 根因：goal_present 原本 fall through 到 fbi_prose（AI痕迹修复 lane），
    #   但 goal_present 是场景结构问题（场景目标缺失），应走 contract_completion
    #   （补齐合同义务）lane，让 LLM 注入场景目标。
    "goal_present",
    "scene_goal_missing",
    # 循环#10：pure_exposition_block_chars 走 contract_completion lane
    # 根因：pure_exposition_block_chars 不在任何 _REPAIR_TYPES 集合中，fall through 到
    #   默认 fbi_prose（AI痕迹修复 lane），但纯陈述大段是场景结构问题（缺少场景化节拍），
    #   应走 contract_completion（补齐合同义务）lane——蓝图路由表也指定了此 lane。
    # 通用性：所有题材的纯陈述大段都需要拆分为含动作和对话的场景化文本。
    "pure_exposition_block_chars",
}

_PUNCTUATION_REPAIR_TYPES = {
    "ai_punctuation_artifact",
    "explanatory_punctuation_artifact",
    "dash_density",
    "dash_per_1000",
}

_PROSE_LOCAL_REPAIR_TYPES = {
    "tier1_hit_count",
    "sentence_shell_count",
    "abstraction_over_budget",
    "structure_word_cluster_count",
    # 通用修复 SA-2：standalone_abstract_claims 走 prose_local_patch lane
    # 根因：standalone_abstract_claims 原本 fall through 到 fbi_prose（AI痕迹修复 lane），
    #   但它是"show don't tell"质量问题（抽象论断缺证据），应走 prose_local_patch
    #   （已有"为抽象陈述补充感官描写、动作描写或对话证据"的 lane 指令）。
    "standalone_abstract_claims",
    "abstract_claim_count",
    "emotional_claim_without_scene_evidence",
    # 通用修复（循环 #5 BL-4）：body_language_cliche_count 走 prose_local_patch lane
    # 根因：body_language_cliche_count 原本不在任何 _REPAIR_TYPES 集合中，fall through 到
    #   fbi_prose（AI痕迹密度修复 lane），但身体语言陈词滥调是"show don't tell"质量问题
    #   （模板化身体反应需改写为具体可观察动作），应走 prose_local_patch（词句级局部改写 lane）。
    # 通用性：所有题材的身体语言陈词滥调都需要词句级改写。
    "body_language_cliche_count",
    # 通用修复（循环 #6 EL-2）：emotion_label_count 走 prose_local_patch lane
    # 根因：emotion_label_count 不在任何 _REPAIR_TYPES 集合中，fall through 到 fbi_prose，
    #   但情绪标签是"show don't tell"质量问题（直接情绪命名需改写为可观察行为），
    #   应走 prose_local_patch（词句级局部改写 lane）。
    # 通用性：所有题材的情绪标签都需要词句级改写。
    "emotion_label_count",
    # 循环#10：abstract_bare_count 走 prose_local_patch lane
    # 根因：abstract_bare_count 不在任何 _REPAIR_TYPES 集合中，fall through 到默认
    #   fbi_prose lane。虽然蓝图路由表指定了 ground_abstract_claim lane，但
    #   _merge_lane_info 的覆盖逻辑不稳定。prose_local_patch lane 已有
    #   "为抽象陈述补充感官描写、动作描写或对话证据"的专科指令，
    #   与 abstract_bare_count 的修复需求（pair_abstract_with_evidence）完全匹配。
    # 通用性：所有题材的抽象描述都需要补充具体证据。
    "abstract_bare_count",
}

# 通用修复（循环 #6 TA-2）：新增 _TENSE_REPAIR_TYPES 集合
# 根因：temporal_anchor_count / tense_drift_count 等时态类违规不在任何 _REPAIR_TYPES 集合中，
#   fall through 到 fbi_prose（AI痕迹密度修复 lane），但时态/时间锚点是时序一致性问题，
#   应走 tense_repair lane（循环 #1 已创建，有专门的闪回闭合和时间锚点修复指令）。
# 通用性：所有题材的时态/时间锚点问题都需要 tense_repair lane 的专科指令。
# 循环#10：timeline_conflict / temporal_conflict / temporal_layer_confusion 同属时序问题，
#   根因：这些 violation type 不在任何 _REPAIR_TYPES 集合中，fall through 到默认 fbi_prose
#   lane（AI痕迹修复指令），LLM 收到的指令与闪回/时间线问题完全脱节，修复不移除闪回标记
#   短语 → recheck 确定性正则复检必然再次触发。加入 _TENSE_REPAIR_TYPES 后走 tense_repair
#   lane（有闪回闭合指令），LLM 能正确改写闪回标记。
# 通用性：所有题材的时间层级混乱都需要 tense_repair lane 的闪回闭合指令。
_TENSE_REPAIR_TYPES = {
    "temporal_anchor_count",
    "tense_drift_count",
    "time_jump_without_signal_count",
    "flashback_tense_correct",
    "unreturned_flashback_count",
    "timeline_conflict",
    "temporal_conflict",
    "temporal_layer_confusion",
}

_PARAGRAPH_RECONSTRUCTION_TYPES = {
    "paragraph_shape_repeat_count",
    "mirrored_paragraph_opening_count",
    "uniform_sentence_streak_max",
}

# 通用修复：lane → 默认 candidate_attempt_budget 映射。
# 当 _merge_lane_info 从 review_minister 的 repair_intent 覆盖 repair_lane 时，
# FBIRepairIntent 不携带 candidate_attempt_budget 字段，导致 _select_repair_lane
# 的默认 attempts=1（未匹配类型集合时）未被覆盖，LLM 修复只有 1 次尝试机会。
# 此映射确保 lane 被覆盖时 budget 也同步设置为新 lane 对应的默认值。
# 与 _select_repair_lane 中各 lane 的 attempts 值保持一致。
_LANE_DEFAULT_BUDGET: dict[str, int] = {
    "deterministic_surface_cleanup": 2,
    "prose_local_patch": 2,
    "paragraph_reconstruction": 2,
    "pacing_repair": 2,
    "voice_reconstruction": 2,
    "voice_repair": 2,
    # 通用修复（循环 #6 TA-2）：tense_repair 的 budget
    "tense_repair": 2,
    "contract_completion": 2,
    "fact_local_patch": 2,
    "fact_bridge_patch": 3,
    "scene_restructure": 1,
    # 通用修复 S-6：fbi_prose 的 budget 从 1 提升到 3
    # 根因：fbi_prose 处理密度类问题（明喻/破折号/情绪标签等），检测是全文密度驱动。
    #   LLM 需要系统性改动多处才能降低密度，1 次尝试通常不够。
    #   现在配合 S-1（evidence_samples 携带所有位置）和 S-2（fbi_prose lane 专用指令），
    #   LLM 一次性处理所有位置，但仍需足够重试预算应对自检失败的情况。
    # 通用性：所有密度类问题的修复轮次应与需改动位置数匹配。
    "fbi_prose": 3,
    "manual_review": 0,
}

_PACING_REPAIR_TYPES = {
    "dense_paragraph_streak_max",
    "breathing_paragraph_ratio",
    "flat_pressure_ramp",
    "low_conflict_density",
    "weak_curiosity_engine",
    "missing_micro_payoff",
    "weak_opening_hook",
    "weak_chapter_end_hook",
    "low_event_density",
    "low_reversal_density",
}

_HARD_CORRECTNESS_REPAIR_TYPES = {
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
    "clue_missing_source",
    "chapter_reference",
    "meta_chapter_reference",
    "meta_narrative_reference",
}


def _violation_type_values(violation: dict) -> set[str]:
    return {
        str(violation.get(key) or "").strip().lower()
        for key in ("type", "violation_type", "semantic_type", "original_type")
        if violation.get(key)
    }


_STRUCTURAL_CONFLICT_TYPES = frozenset({
    "setting_conflict",
    "spatial_conflict",
    "identity_conflict",
    "pov_conflict",
})


def _has_deterministic_text_correction(violation: dict) -> bool:
    # 结构性冲突（整场景空间错位、身份错位、POV 错位）无法通过 local_patch 修正，
    # 必须走 scene_rewrite 整体重写。即使 detail 中出现 "suggested correction" 或
    # "text claim...conflicts with established fact" 措辞，也不应判定为可确定性修正。
    if _violation_type_values(violation) & _STRUCTURAL_CONFLICT_TYPES:
        return False
    text = " ".join(
        str(violation.get(key) or "")
        for key in ("detail", "reason", "expected_behavior", "target_span")
    ).lower()
    return (
        "suggested correction" in text
        or "建议修正" in text
        or "改为" in text
        or ("text claim" in text and "conflicts with established fact" in text)
    )


def _is_fact_conflict(violation: dict) -> bool:
    if _violation_type_values(violation) & {
        "fact_conflict",
        "internal_conflict",
        "identity_conflict",
        "setting_conflict",
        "spatial_conflict",
        "ownership_conflict",
        "knowledge_boundary_violation",
        # 通用修复 C-3：clue_provenance_error 本质是事实缺失（线索来源缺失），
        # 与 fact_conflict 同类，应走 fact_local_patch lane（事实类修复链路）。
        # 根因：_select_repair_lane 没有 clue_provenance_fix lane 分支，
        #   fall through 到 fbi_prose（默认），但 fbi_prose 是 AI 痕迹修复 lane，
        #   不适合处理事实类问题。
        "clue_provenance_error",
        "clue_provenance_error_proposition",
        "clue_missing_source",
        "unprovenanced_clue",
    }:
        return True
    detail = " ".join(
        str(violation.get(key) or "")
        for key in ("detail", "reason", "expected_behavior")
    ).lower()
    return (
        "conflicts with established fact" in detail
        or "事实冲突" in detail
        or "与已知事实" in detail
        or "既定事实" in detail
    )


def _metric_direction(metric: str) -> str:
    lowered = (metric or "").lower()
    if lowered in {
        "fact_conflict",
        "internal_conflict",
        "identity_conflict",
        "setting_conflict",
        "spatial_conflict",
        "ownership_conflict",
        "timeline_conflict",
        "temporal_conflict",
        "knowledge_boundary_violation",
    }:
        return "resolve"
    if any(token in lowered for token in ("count", "density", "streak", "repeat", "ratio")):
        return "decrease"
    if any(token in lowered for token in ("hook", "conflict", "curiosity", "event", "reversal")):
        return "increase"
    return "resolve"


def _repair_failure_signature(violation: dict, owner_scene: int | None) -> str:
    metric = str(
        violation.get("metric")
        or violation.get("type")
        or violation.get("violation_type")
        or "unknown"
    ).lower()
    repair_domain = str(violation.get("repair_domain") or "").lower()
    validator = str(violation.get("validator") or violation.get("source_validator") or "").lower()
    scene = owner_scene
    if scene is None:
        scene = violation.get("source_scene", "chapter")
    return f"{scene}:{repair_domain}:{validator}:{metric}"


def _allowed_operations_for_lane(repair_lane: str) -> list[str]:
    return {
        "deterministic_surface_cleanup": ["replace_punctuation", "remove_redundant_dash"],
        "prose_local_patch": ["replace_phrase", "normalize_structure_words", "rewrite_sentence", "replace_tier1_ai_flavor_terms", "cleanup_ai_flavor_window"],
        "paragraph_reconstruction": ["split_paragraph", "vary_sentence_shape", "merge_short_paragraph", "rewrite_adjacent_sentences", "trim_discourse_window", "vary_sentence_length_window"],
        "pacing_repair": ["insert_hook_beat", "insert_micro_payoff", "split_paragraph", "insert_bridge", "insert_transition_anchor", "insert_functional_breathing_paragraph", "smooth_abrupt_shift", "vary_sentence_length", "sharpen_hook"],
        "voice_repair": ["remove_non_pov_inner_state", "convert_to_observable_action", "rewrite_sentence"],
        "voice_reconstruction": ["rewrite_paragraph", "restore_voice_fingerprint", "rewrite_voice_window"],
        "contract_completion": ["append_ending_beat", "rewrite_last_paragraph", "insert_missing_beat"],
        "fact_local_patch": ["replace_conflicting_phrase", "replace_literal", "insert_time_anchor", "insert_causal_bridge"],
        "fact_bridge_patch": [
            "replace_conflicting_phrase",
            "insert_transition_anchor",
            "insert_causal_bridge",
            "insert_missing_fact_components",
            "rewrite_process_beat",
        ],
        "scene_restructure": ["rewrite_paragraph", "reorder_micro_beats", "limited_scene_rewrite"],
        # 方案 26 Part D：风格类 repair_lane
        "style_local_patch": ["replace_phrase", "replace_exact", "rewrite_sentence"],
        "structure_word_cleanup": ["normalize_structure_words", "replace_phrase"],
        "sentence_shape_variation": ["vary_sentence_shape", "vary_sentence_length_window"],
        "paragraph_shape_fix": ["split_paragraph", "merge_paragraphs", "vary_sentence_shape"],
        # P2-18：方案 26 Part D 三个 LLM 风格工具 repair_lane
        "voice_phrase_correction": ["tighten_voice_phrase", "rewrite_voice_window"],
        "abstract_telling_grounding": ["replace_abstract_telling", "replace_phrase"],
        "detail_anchor_injection": ["add_specific_detail_anchor", "insert_after_anchor"],
        "manual_review": [],
    }.get(repair_lane, ["replace_phrase", "rewrite_sentence"])


def _dedupe_strings(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = str(value or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _fallback_lane_for(repair_lane: str) -> str:
    return {
        "deterministic_surface_cleanup": "prose_local_patch",
        "prose_local_patch": "paragraph_reconstruction",
        "paragraph_reconstruction": "pacing_repair",
        "pacing_repair": "paragraph_reconstruction",
        "voice_repair": "voice_reconstruction",
        "contract_completion": "scene_restructure",
        "fact_local_patch": "fact_bridge_patch",
        "fact_bridge_patch": "scene_restructure",
    }.get(repair_lane, "")


_FACT_QUOTE_RE = re.compile(r"['\"“”‘’]([^'\"“”‘’]{2,160})['\"“”‘’]")
_FACT_TEXT_CLAIM_RE = re.compile(
    r"(?:Text claim|text claim)\s*['\"“”‘’]([^'\"“”‘’]{2,220})['\"“”‘’]"
)
_FACT_AUTHORITY_RE = re.compile(
    r"(?:established fact|authority fact|事实层|已确立事实|既定事实|合同|scene_provenance)"
    r"[^'\"“”‘’：:]{0,40}[：:]?\s*['\"“”‘’]?([^'\"“”‘’。；;\n]{4,220})['\"“”‘’]?"
)
_FACT_AUTHORITY_QUOTED_RE = re.compile(
    r"(?:established fact|authority fact|事实层|已确立事实|既定事实|合同|scene_provenance)"
    r"[^'\"“”‘’。；;\n]{0,80}?"
    r"(?:明确为|锚点明确为|时间锚点明确为|规定为|要求为|应为|为|是)?"
    r"\s*['\"“”‘’]([^'\"“”‘’]{2,160})['\"“”‘’]",
    re.IGNORECASE,
)
_FACT_EXPECTED_QUOTED_RE = re.compile(
    r"(?:应改为|应调整为|应写成|改为|调整为|写成|明确为|规定为|要求为|应为|为|如)"
    r"\s*['\"“”‘’]([^'\"“”‘’]{2,80})['\"“”‘’]",
)
_FACT_EXPECTED_NOUN_RE = re.compile(
    r"(?:一致的|一致为|应为|应改为|应调整为|应写成|改为|调整为|写成)"
    r"\s*([^，。；;、\s'\"“”‘’（）()]{2,24})"
)
_FACT_SUGGESTED_RE = re.compile(
    r"(?:suggested correction|建议修正|建议修改|应改为|应调整为)\s*[：:]?\s*['\"“”‘’]?([^'\"“”‘’。；;\n]{4,220})['\"“”‘’]?",
    re.IGNORECASE,
)
_FACT_OLD_CLAIM_RE = re.compile(
    r"([^。；;\n]{0,24}(?:仅|只|单独|一味|一株|一个|一种)[^。；;\n]{2,80})"
)
_FACT_ENTITY_SPLIT_RE = re.compile(r"[、,，/／和与及以及\s]+")
_FACT_ENTITY_STOPWORDS = {
    "正文",
    "事实层",
    "合同",
    "场景",
    "要求",
    "需要",
    "应",
    "应该",
    "补充",
    "调整",
    "修改",
    "说明",
    "过程",
    "存在",
    "缺失",
    "三味主药",
    "主药",
    "灵药",
    "丹药",
    "破境丹",
    "类似表述",
    "一致",
    "时间",
    "时间锚点",
    "锚点",
    "明确为",
    "点明确为",
}
# 通用元描述过滤：authority_fact 中常见的事实陈述性短语（非正文实体）。
# 这些短语是"关于事实的描述"，不是正文中应该出现的实体名称。
# 例如 "凤溪在已知事实中持有..." 是元描述，正文应体现"半张暗青色云纹面具"而非这整句。
# 例如 "时间锚点为S2结束后约一刻钟" 是事实层元描述，正文应体现时间点而非这整句。
# 例如 "五位师兄灵根特殊性尚未明确说明" 是事实层状态描述，不是正文实体。
_FACT_ENTITY_META_RE = re.compile(
    r"已知事实|场景开始前|已出现|并被|作为|持有|已建立|前文|"
    r"未明确提及|未明确提|未提及|未包含|未说明|未出现|没有说明|"
    # 事实层陈述性元描述：authority_fact 中描述事实层状态的子句
    r"时间锚点|地理位置|目标是|目标为|冲突是|冲突为|"
    r"尚未明确|尚未|明确说明|特殊性|统一场景|结果方向|"
    r"明确指出|明确规定|明确要求|事实层明确|"
    # 引号嵌套提取出的元描述前缀（如 '当前事实层中'森林中有其他妖兽'' 提取出 "当前事实层中"）
    r"当前事实层|事实层中|"
    # 通用修复 Fix 7n v3：FBI 场景状态层元描述前缀（与 _FACT_AUTHORITY_META_RE 保持一致）。
    # '当前状态中描述...' / '当前状态中...' 是 FBI 对场景状态层的元描述，不是字面事实。
    r"当前状态中|当前状态下|状态中描述|状态描述|"
    # 通用修复 Fix 7b：状态描述元描述（ending_state 等合同层描述）。
    # '价值状态由「被控」转向「清醒但弱」' 是状态描述，不是正文实体。
    # LLM 修复后正文应展示状态（如"大师兄缓缓睁眼，虚弱但清醒"），
    # 而非字面包含"价值状态由「被控」转向「清醒但弱」"这句话。
    r"价值状态|状态由|由.*转向|"
    # 通用修复 Fix 7b：动词性句子片段（非名词性实体）。
    # '五师兄在一起' 是动词短语，LLM 修复后正文不会原样包含。
    r"在一起|"
    # 通用修复 Fix 7b：地点状语句子片段（"在...处/中/里/前/后/旁/内"结构）。
    # '凤溪在森林古树根凹陷处' 是地点状语，LLM 修复后正文会用不同表述
    # （如"凤溪蹲伏在古树根部的凹陷处"），字面匹配必然失败。
    r"在[^，。；\s]{2,20}[处中里前后旁内]|"
    # 通用修复 Fix 7c：时间状语描述（非名词性实体）。
    # '祭坛事件后不久' 是时间状语，LLM 修复后正文会用不同表述
    # （如"祭坛一别后"、"自祭坛事件之后"），字面匹配必然失败。
    r"后不久|前不久|"
    # 通用修复 Fix 7g：中文句子结构标志（动词性结构/连词/被动语态）。
    # authority_fact 常是完整事实陈述句子（如"大师兄被魔种控制，失去理智"），
    # 从中切分出的 item 是句子成分（主谓宾），不是名词性实体。
    # LLM 修复后正文不会原样包含这些句子片段（如"大师兄被魔种控制"），
    # 字面匹配必然失败。
    # '大师兄被魔种控制' 包含"被"（被动语态）
    # '失去理智' 包含"失去"（动词）
    # '纱布仍在' 包含"仍在"（动词+副词）
    # '且血渍未干透' 包含"且"（连词）
    # '魔气本能地对声音源方向产生攻击倾向' 包含"产生"（动词）
    # 这些词通常不出现在名词性实体中，过滤是安全的。
    r"被|失去|仍在|产生|且|"
    # 通用修复 Fix 7k：角色动作动词（描述角色行为，非名词性实体）。
    # authority_fact 中常包含角色行为描述（如"五师兄躲避妖兽"），
    # 从中切分出的 item 是动宾短语，LLM 修复后正文会用不同表述
    # （如"五师兄藏身于密林深处"），字面匹配必然失败。
    # 这些动词描述动作，通常不出现在名词性实体中。
    r"躲避|藏身|同行|护送|追踪|寻找|发现|遇到|对峙|交锋|交手"
)
# 通用修复 Fix 7j：old_error_signature 的元描述过滤。
# detail 引号中常包含描述冲突的元描述文本（如"的描述暗示..."、"的核心事实矛盾。"），
# 这些不是正文中需要删除的错误句子，而是 FBI 对冲突的分析描述。
# 含这些关键词的 signature 几乎不可能出现在正文中，把它们作为 signature 会导致
# 后检检查"是否删除了这些分析描述"，但 LLM 不会在正文中写这些描述，lingering 永远不减少。
_FACT_OLD_SIG_META_RE = re.compile(
    r"描述暗示|的核心事实|事实矛盾|与已确立事实|与核心事实|"
    r"细节不符|状态不符|状态矛盾|"
    r"轻微不符|存在不符|存在轻微"
)
# 通用修复 Fix 7n：authority_fact 元描述关键词（用于判断 authority_fact 是否为元描述）。
# 当 authority_fact 含这些关键词且 required_entities 为空（被 META_RE 全过滤）时，
# 说明 authority_fact 是"关于事实的描述"（如"场景结束状态应为价值状态由「被控」转向「清醒但弱」"），
# 不是字面事实。text_claim 与元描述"冲突"，不是字面错误，LLM 不应该删除 text_claim，
# 应该微调表述。清空 old_signatures，后检只检查文本是否改变，
# 语义一致性由下一轮 FBI 审查保障。
# 注意：不含动词关键词（如"被|失去|仍在"），避免把含动词的字面事实误判为元描述。
_FACT_AUTHORITY_META_RE = re.compile(
    r"价值状态|状态由|由.*转向|"
    r"时间锚点|地理位置|目标是|目标为|冲突是|冲突为|"
    r"尚未明确|尚未|明确说明|特殊性|统一场景|结果方向|"
    r"明确指出|明确规定|明确要求|事实层明确|"
    r"当前事实层|事实层中|场景事实层|"
    r"已知事实|场景开始前|已建立|前文|"
    r"未明确提及|未明确提|未提及|未包含|未说明|未出现|没有说明|"
    # 通用修复 Fix 7n v3：FBI 元描述前缀扩展。
    # '当前状态中描述...' / '当前状态中...' 是 FBI 对场景状态层的元描述，
    # 不是字面事实。text_claim 与元描述"冲突"是 FBI 的语义判断，
    # LLM 修复后正文用不同表述，required_entities 字面匹配必然失败。
    r"当前状态中|当前状态下|状态中描述|状态描述"
)


def _compact_fact_text(value: object) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip()


def _fact_goal_text_blob(violation: dict) -> str:
    evidence = violation.get("evidence") if isinstance(violation.get("evidence"), dict) else {}
    parts: list[str] = []
    for key in (
        "detail",
        "reason",
        "expected_behavior",
        "repair_goal",
        "target_span",
        "evidence_span",
        "text_claim",
        "authority_fact",
    ):
        if violation.get(key):
            parts.append(str(violation.get(key)))
    for key in (
        "detail", "reason", "expected_behavior", "repair_goal", "target_span",
        "authority_fact", "authority_status",
    ):
        if evidence.get(key):
            parts.append(str(evidence.get(key)))
    return "\n".join(parts)


def _first_fact_match(pattern: re.Pattern[str], text: str) -> str:
    match = pattern.search(text or "")
    return match.group(1).strip() if match else ""


def _is_weak_fact_authority(value: str) -> bool:
    item = str(value or "").strip(" ：:，,。；;'\"“”‘’")
    if not item:
        return True
    compact = _compact_fact_text(item)
    if compact in {"明确为", "点明确为", "合同", "事实层", "当前事实层", "场景事实层"}:
        return True
    if compact.endswith("明确为") or compact.endswith("规定为") or compact.endswith("要求为"):
        return True
    return len(compact) < 2


def _extract_expected_fact_entities(value: str) -> list[str]:
    text = str(value or "")
    entities: list[str] = []

    def add(raw: object) -> None:
        item = str(raw or "").strip(" ：:，,。；;'\"“”‘’（）()[]【】")
        if not item:
            return
        if item.startswith("时值") and len(item) > 2:
            item = item[2:]
        if item in _FACT_ENTITY_STOPWORDS:
            return
        if any(stop in item and len(item) <= len(stop) + 2 for stop in _FACT_ENTITY_STOPWORDS):
            return
        # 通用修复 Fix 7h：expected_behavior 中的 suggested correction 常是完整句子
        # （如"魔气本能地对声音源方向产生攻击倾向"含动词"产生"），不是名词性实体。
        # 应用与 authority_fact 切分相同的 META_RE 过滤，保持一致性。
        if _FACT_ENTITY_META_RE.search(item):
            return
        if 2 <= len(item) <= 24 and item not in entities:
            entities.append(item)

    for match in _FACT_EXPECTED_QUOTED_RE.findall(text):
        add(match)
    for match in _FACT_EXPECTED_NOUN_RE.findall(text):
        add(match)
    return entities[:6]


def _extract_fact_authority_fact(
    explicit_authority: object,
    blob: str,
    suggested: str,
) -> str:
    explicit = str(explicit_authority or "").strip()
    if explicit and not _is_weak_fact_authority(explicit):
        return _trim_fact_authority_clause(explicit)

    quoted = _first_fact_match(_FACT_AUTHORITY_QUOTED_RE, blob)
    if quoted:
        return _trim_fact_authority_clause(quoted)

    fallback = _trim_fact_authority_clause(_first_fact_match(_FACT_AUTHORITY_RE, blob))
    if fallback and not _is_weak_fact_authority(fallback):
        return fallback

    expected_entities = _extract_expected_fact_entities(suggested)
    if expected_entities:
        return expected_entities[0]
    return ""


def _extract_fact_required_entities(authority_fact: str, expected_behavior: str) -> list[str]:
    source = authority_fact or ""
    if "：" in source:
        source = source.split("：", 1)[1]
    elif ":" in source:
        source = source.split(":", 1)[1]
    source = re.split(r"[,，]\s*(?:但|但是|but|however)|[。；;\n]", source, 1, flags=re.IGNORECASE)[0]

    entities: list[str] = []
    for raw in _FACT_ENTITY_SPLIT_RE.split(source):
        item = raw.strip(" ：:，,。；;'\"“”‘’（）()[]【】")
        if not item:
            continue
        if item in _FACT_ENTITY_STOPWORDS:
            continue
        if any(stop in item and len(item) <= len(stop) + 2 for stop in _FACT_ENTITY_STOPWORDS):
            continue
        # 通用过滤：跳过事实陈述性元描述子句（如"凤溪在已知事实中持有"），
        # 这些是关于事实的描述语，不是正文中应该出现的实体名称。
        # LLM 创意改写后正文不会原样包含这些短语，字面匹配必然失败。
        if _FACT_ENTITY_META_RE.search(item):
            continue
        if not re.search(r"[\u4e00-\u9fffA-Za-z0-9]", item):
            continue
        if 2 <= len(item) <= 24 and item not in entities:
            entities.append(item)

    quoted = [
        item.strip()
        for item in _FACT_QUOTE_RE.findall(authority_fact or "")
        if 2 <= len(item.strip()) <= 40
    ]
    for item in quoted:
        # 通用修复 Fix 7h：authority_fact 引号内容也可能是完整句子
        # （如状态描述、修复建议），应用 META_RE 过滤保持一致性。
        if _FACT_ENTITY_META_RE.search(item):
            continue
        if item not in entities and item not in _FACT_ENTITY_STOPWORDS:
            entities.append(item)
    # 通用修复 Fix 7m：不从 suggested_correction/expected_behavior 提取 required_entities。
    # suggested_correction 是 FBI 对修复方向的描述（如"在修为层面体现为'修为大跌'"），
    # 其中的引号内容（如"修为大跌"）是描述性词语，不是 LLM 必须字面写入正文的实体。
    # LLM 修复后正文会用不同表述（如"修为跌落到筑基初期"），字面匹配必然失败，
    # 导致 blocking order 永远无法修复。
    # 贯彻 Fix 7i 已声明的设计原则：required_entities 只从 authority_fact 提取，
    # 不从 suggested/expected_behavior 提取。如果 authority_fact 没有可提取的实体
    # （被 META_RE 全过滤，说明是元描述），后检将只检查 old_error_signatures，
    # 语义一致性由下一轮 FBI 审查保障。
    return entities[:8]


def _trim_fact_authority_clause(value: str) -> str:
    return re.split(
        r"[,，]\s*(?:但|但是|but|however)|[。；;\n]",
        str(value or ""),
        1,
        flags=re.IGNORECASE,
    )[0].strip()


def _extract_fact_old_signatures(text_claim: str, target_span: str, detail: str) -> list[str]:
    signatures: list[str] = []

    def add(value: object) -> None:
        item = str(value or "").strip()
        if not item:
            return
        lowered = item.lower()
        if "text claim" in lowered and "conflicts with established fact" in lowered:
            return
        if "但正文" in item and any(token in item for token in ("事实", "主药", "合同", "scene_provenance")):
            return
        # 通用修复 Fix 7e：text_claim 的元描述前缀（如"正文描述凤溪使用玉佩..."）
        # 是关于正文的描述语，不是正文中的错误句子。把它们当成 old_error_signature
        # 会导致后检检查"是否删除了这个描述语"，但 LLM 修复后正文可能仍然有类似描述
        # （只是修改了具体内容），lingering 永远不减少，后检必然失败。
        # 这些元描述通常从 _FACT_TEXT_CLAIM_RE 提取（"Text claim '正文描述...'"），
        # 以"正文描述"或"正文"开头。
        if item.startswith("正文描述") or item.startswith("正文"):
            return
        # 通用过滤：old_error_signature 应为短错误特征（如"金丹初期的修为"），
        # 不是整段原文。超过 80 字符的 signature 通常是整段 target_span，
        # 对于 missing_context_bridge 类型（修复=添加内容），原文不会改变，
        # 把它作为 signature 会导致 lingering 永远不减少，修复必然失败。
        if len(item) > 80:
            return
        # 通用修复 Fix 7j：过滤会导致后检误判的 old_error_signature 片段。
        # detail 引号中常包含描述冲突的元描述文本（如"的描述暗示..."、"的核心事实矛盾。"），
        # 这些不是正文中需要删除的错误句子。把它们作为 signature 会导致：
        # 1. 后检在候选文本中匹配到相似表述（false positive）
        # 2. 或后检要求删除这些描述语（但 LLM 不会在正文中写这些描述语，lingering 永远不减少）
        # 过滤规则：
        # a. 去除首尾标点后 <=3 字符（如 '，且' → '且'，太短会在几乎所有中文文本中匹配）
        stripped_punct = re.sub(
            r"^[，。；：、！？,.;:!?'\"“”‘’（）()[]【】…\s]+|[，。；：、！？,.;:!?'\"“”‘’（）()[]【】…\s]+$",
            "",
            item,
        )
        if len(stripped_punct) <= 3:
            return
        # b. 以标点开头（如 '，且'、'。然而'，是从 detail 引号切分出的句子片段）
        if item[0] in "，。；：、！？,.;:!?'\"“”‘’（）()[]【】…":
            return
        # c. 含元描述分析关键词（描述冲突本身，而非正文错误文本）
        if _FACT_OLD_SIG_META_RE.search(item):
            return
        if item and item not in signatures:
            signatures.append(item)

    add(text_claim)
    add(target_span)
    contrast_head = re.split(
        r"(?:\bconflicts with established fact\b|但|但是|however|suggested correction|建议修正|建议修改)",
        str(detail or ""),
        1,
        flags=re.IGNORECASE,
    )[0]
    for item in _FACT_QUOTE_RE.findall(contrast_head):
        add(item)
    for item in _FACT_QUOTE_RE.findall(str(text_claim or "")):
        add(item)
    for item in _FACT_QUOTE_RE.findall(str(target_span or "")):
        add(item)
    for part in re.split(r"\.{3,}|……|…", str(target_span or "")):
        add(part)
    for match in _FACT_OLD_CLAIM_RE.findall(detail or ""):
        add(match)
    for match in _FACT_OLD_CLAIM_RE.findall(text_claim or ""):
        add(match)
    return signatures[:10]


def _compile_fact_repair_goal(violation: dict) -> dict:
    if not _is_fact_conflict(violation):
        return {}
    blob = _fact_goal_text_blob(violation)
    text_claim = (
        str(violation.get("text_claim") or "").strip()
        or _first_fact_match(_FACT_TEXT_CLAIM_RE, blob)
        or str(violation.get("target_span") or violation.get("evidence_span") or "").strip()
    )
    suggested = (
        str(violation.get("suggested_correction") or violation.get("expected_behavior") or "").strip()
        or _first_fact_match(_FACT_SUGGESTED_RE, blob)
    )
    authority_fact = _extract_fact_authority_fact(
        violation.get("authority_fact") or (
            violation.get("evidence", {}).get("authority_fact")
            if isinstance(violation.get("evidence"), dict)
            else ""
        ),
        blob,
        suggested,
    )
    required_entities = _extract_fact_required_entities(authority_fact, suggested)
    # 通用 fallback Fix 7i：当 required_entities 为空时（authority_fact 被元描述过滤后），
    # 只从 authority_fact 中用引号提取候选实体。
    # 不从 suggested/expected_behavior 提取——这些是"修复建议"（如"右臂的纱布松垮地挂在臂上..."），
    # 是完整的建议句子，不是"必须出现的实体"。把它们当成 required_entities 会导致后检要求
    # "建议句子必须原样出现"，LLM 正确改写后反而后检失败。
    # 如果 authority_fact 也没有可提取的实体，返回空列表——后检将只检查 old_error_signatures，
    # 对于"添加内容"型修复（old_signatures 已清空），后检会通过，信任 LLM 完成修复。
    if not required_entities:
        for match in _FACT_QUOTE_RE.findall(authority_fact or ""):
            item = match.strip()
            if not item or _FACT_ENTITY_META_RE.search(item):
                continue
            if item in _FACT_ENTITY_STOPWORDS:
                continue
            if 2 <= len(item) <= 40 and item not in required_entities:
                required_entities.append(item)
        required_entities = required_entities[:8]
    old_signatures = _extract_fact_old_signatures(
        text_claim,
        str(violation.get("target_span") or violation.get("evidence_span") or ""),
        blob,
    )
    conflict_type = "direct_fact_conflict"
    compact_blob = _compact_fact_text(blob)
    # 通用修复 Fix 7f：添加"未明确"模式，匹配"未明确说明"、"未明确提及"等。
    # FBI 生成的 violation 中常包含"未明确说明时间是否..."这类描述，
    # 这是"添加内容"型修复（missing_context_bridge），不是"替换错误事实"型。
    if re.search(r"未提及|未包含|未说明|未明确|缺少说明|没有说明|无法感知|感知不到|说明为何", compact_blob):
        conflict_type = "missing_context_bridge"
    elif re.search(
        r"包含|补充解释|因果|桥|链条|解释为何|说明为何|"
        r"铺垫|过渡|承接|补(?:足|上|入|写).{0,8}(?:原因|缘由|因果|过渡|承接)",
        _compact_fact_text(suggested),
    ):
        conflict_type = "missing_context_bridge"
    elif required_entities and re.search(r"仅|只|单独|一味|一株|一种|未出现|缺失|没有", compact_blob):
        conflict_type = "missing_required_components"
    elif re.search(r"名称不一致|误写|写成|改为|替换", compact_blob):
        conflict_type = "wrong_fact_value"

    # 通用修复 Fix 7n：当 authority_fact 是元描述（含元描述关键词）时，
    # authority_fact 是"关于事实的描述"（如"当前事实层：凤溪与五师兄在森林，五师兄昏迷"），
    # 不是字面事实。text_claim 与元描述"冲突"是 FBI 的语义判断，text_claim 是概括性描述，
    # 不是字面错误。required_entities 是从元描述提取的句子片段，LLM 修复后正文用不同表述，
    # 字面匹配必然失败。
    # 此时清空 required_entities 和 old_signatures，后检只检查文本是否改变，
    # 语义一致性由下一轮 FBI 审查保障。
    # 这是通用的——基于 authority_fact 是否含元描述关键词判断，不针对任何 metric。
    # 注意：此修复在 Fix 7l（direct_fact_conflict 清空 required_entities）和
    # missing_context_bridge/missing_required_components 清空 old_signatures 之前执行，
    # 确保元描述型 authority_fact 的 required_entities 和 old_signatures 都被清空。
    if authority_fact and _FACT_AUTHORITY_META_RE.search(authority_fact):
        required_entities = []
        old_signatures = []

    # 通用修复：对于"添加内容"型修复（missing_context_bridge 和
    # missing_required_components），target_span/text_claim 是"缺少内容的上下文"，
    # 不是"错误特征"。LLM 不应该删除这些内容，应该保留并添加缺失内容。
    # 把它们作为 old_error_signatures 会导致后检检查"是否删除了这些内容"，
    # 但 LLM 正确地保留了这些内容，lingering_old_error_signatures 永远不减少，后检必然失败。
    # 注意：Fix 7n 可能已经清空 old_signatures，这里是针对非元描述型 authority_fact 的补充。
    if conflict_type in ("missing_context_bridge", "missing_required_components"):
        old_signatures = []

    # 通用修复 Fix 7l：对于所有"语义改写型" fact_conflict（direct/missing_context_bridge/
    # missing_required_components），清空 required_entities。
    # 这些类型的 authority_fact 是完整事实陈述句，从中切分出的 required_entities
    # 是句子片段（动宾短语如"凤溪通过传讯符逐一联系"），LLM 正确改写后正文用不同表述
    # （如"凤溪拿出传讯符，依次联系二师兄"），字面匹配必然失败，导致 blocking order
    # 永远无法修复。
    # 后检改为只检查 old_error_signatures（矛盾文本是否已删除/改写）+ 文本是否变化，
    # 真正的语义一致性由下一轮 FBI 审查（LLM 语义法官）保障。
    # 注意：wrong_fact_value 不清空（需要正确值字面出现在正文中，如人名/地名修正）。
    # 注意：Fix 7n 可能已经清空 required_entities，这里是对非元描述型 authority_fact 的补充。
    if conflict_type == "missing_context_bridge":
        required_entities = []

    return {
        "conflict_type": conflict_type,
        "authority_fact": authority_fact,
        "authority_status": (
            violation.get("authority_status")
            or (
                violation.get("evidence", {}).get("authority_status")
                if isinstance(violation.get("evidence"), dict)
                else ""
            )
            or "unknown"
        ),
        "text_claim": text_claim,
        "required_entities": required_entities,
        "forbidden_claims": old_signatures[:6],
        "old_error_signatures": old_signatures,
        "required_relation": suggested or authority_fact,
        "suggested_correction": suggested,
        "source_violation_type": violation.get("type") or violation.get("violation_type") or "fact_conflict",
    }


def _is_text_contract_completion_gap(violation: dict) -> bool:
    if not (_violation_type_values(violation) & _SCENE_OUTCOME_REPAIR_TYPES):
        return False
    repair_scope = str(violation.get("repair_scope") or violation.get("scope") or "")
    if repair_scope in {"scene_contract", "chapter_contract", "planning", "system"}:
        return False
    if violation.get("repairable_by_contract") is True and violation.get("repairable_by_text") is not True:
        return False
    return str(violation.get("obligation_scope") or "") != "chapter"


# 违规严重度 → 优先级映射
_SEVERITY_TO_PRIORITY: dict[str, Literal["critical", "high", "medium", "low"]] = {
    "blocking": "critical",
    "critical": "critical",
    "major": "high",
    "high": "high",
    "medium": "medium",
    "minor": "low",
    "low": "low",
    "info": "low",
}

_TRUE_CONTRACT_REPAIR_TYPES: set[str] = {
    "missing_contract",
    "scene_contract_compile_blocked",
    "scene_contract_compiler_unavailable",
    "contract_schema_conflict",
    "contract_field_conflict",
    "outline_contract_conflict",
}


# ---------------------------------------------------------------------------
# FBIChapterCaseIntakeService
# ---------------------------------------------------------------------------


class FBIChapterCaseIntakeService:
    """整章案件接入服务。

    职责：
    1. 接收 ChapterReviewCase
    2. 校验所有 scene_packets 可用性（unavailable 标记）
    3. 合并所有场景的违规
    4. 按 type + target_span 去重
    5. 识别跨场景冲突（相邻场景状态不连续等）
    6. 返回已验证的案件（含 computed case_id）
    """

    async def intake(self, case: ChapterReviewCase) -> ChapterReviewCase:
        """校验并处理整章审校案件。

        Args:
            case: 原始 ChapterReviewCase。

        Returns:
            已验证的 ChapterReviewCase（case_id 已计算，违规已合并去重）。

        Raises:
            ValueError: 所有场景包均不可用或案件数据无效。
        """
        _logger.info(
            "ChapterCaseIntake: project=%s chapter=%d packets=%d",
            case.project_id, case.chapter_number, len(case.scene_packets),
        )

        # --- 空案件 ---
        if not case.scene_packets:
            _logger.warning(
                "ChapterCaseIntake: empty scene_packets project=%s chapter=%d",
                case.project_id, case.chapter_number,
            )
            case.case_id = case.compute_case_id()
            return case

        # --- 校验可用性 ---
        available_packets = [p for p in case.scene_packets if not p.unavailable]
        unavailable_count = len(case.scene_packets) - len(available_packets)

        if unavailable_count > 0:
            _logger.warning(
                "ChapterCaseIntake: %d/%d packets unavailable project=%s chapter=%d",
                unavailable_count, len(case.scene_packets),
                case.project_id, case.chapter_number,
            )

        if not available_packets:
            raise ValueError(
                f"所有场景包均不可用: project={case.project_id} "
                f"chapter={case.chapter_number} unavailable={unavailable_count}"
            )

        for packet in available_packets:
            self._ensure_packet_violation_metadata(packet)

        self._merge_review_case_file_issues(case, available_packets)
        for packet in available_packets:
            self._apply_enforcement_grades(packet)

        # --- 合并违规 ---
        merged_blocking = self._merge_violations(
            [v for p in available_packets for v in p.blocking_violations if isinstance(v, dict)],
        )
        merged_advisory = self._merge_violations(
            [v for p in available_packets for v in p.advisory_violations if isinstance(v, dict)],
        )
        promoted_blocking, merged_advisory = self._promote_hard_correctness_advisories(
            merged_advisory,
            existing_blocking=merged_blocking,
        )
        merged_blocking.extend(promoted_blocking)

        # --- 识别跨场景冲突 ---
        cross_scene_conflicts = self._detect_cross_scene_conflicts(available_packets)
        merged_blocking.extend(cross_scene_conflicts)

        # --- 回写去重后的违规到各场景包 ---
        self._redistribute_violations(available_packets, merged_blocking, merged_advisory)

        # --- 计算 case_id ---
        case.case_id = case.compute_case_id()

        _logger.info(
            "ChapterCaseIntake: validated case_id=%s blocking=%d advisory=%d cross_scene=%d",
            case.case_id, len(merged_blocking), len(merged_advisory), len(cross_scene_conflicts),
        )

        return case

    # ------------------------------------------------------------------
    # 违规合并与去重
    # ------------------------------------------------------------------

    @staticmethod
    def _ensure_packet_violation_metadata(packet: SceneReviewPacket) -> None:
        """Ensure packet violations can be redistributed after deduplication."""
        for bucket in (packet.blocking_violations, packet.advisory_violations):
            for violation in bucket:
                if not isinstance(violation, dict):
                    continue
                normalized = normalize_violation_semantics(violation)
                violation.clear()
                violation.update(normalized)

                source_scene = violation.get("source_scene")
                if not isinstance(source_scene, int):
                    source_scene = packet.scene_index
                    violation["source_scene"] = source_scene
                source_scenes = violation.get("source_scenes")
                if not isinstance(source_scenes, list):
                    source_scenes = [source_scene]
                elif source_scene not in source_scenes:
                    source_scenes = [source_scene, *source_scenes]
                violation["source_scenes"] = [
                    scene for scene in source_scenes if isinstance(scene, int)
                ] or [packet.scene_index]
                violation.setdefault("repairability", packet.repairability)

    @staticmethod
    def _merge_review_case_file_issues(
        case: ChapterReviewCase,
        packets: list[SceneReviewPacket],
    ) -> None:
        """Merge standard Review Case File issues into legacy scene packets."""
        violations = case_file_issues_to_violations(case.review_case_file)
        if case.case_file_deltas:
            for delta in case.case_file_deltas:
                # manual_review 也必须进入 case/plan 证据链，只是不得交给
                # 自动文本修复器。在 intake 丢弃它会把真实阻断伪装成 clean。
                violations.extend(case_file_issues_to_violations(delta))
        if not violations or not packets:
            return

        packets_by_scene = {packet.scene_index: packet for packet in packets}
        fallback_packet = packets[0]
        existing_keys = {
            FBIChapterCaseIntakeService._violation_merge_key(item)
            for packet in packets
            for item in [*packet.blocking_violations, *packet.advisory_violations]
            if isinstance(item, dict)
        }
        for violation in violations:
            normalized = normalize_violation_semantics(violation)
            source_scene = normalized.get("source_scene")
            source_scenes = [
                scene
                for scene in (normalized.get("source_scenes") or [])
                if isinstance(scene, int) and scene in packets_by_scene
            ]
            review_case_issue = normalized.get("review_case_issue") or {}
            is_chapter_level = (
                not isinstance(source_scene, int)
                and not source_scenes
                and (
                    str(normalized.get("source") or "") == "final_acceptance"
                    or str(review_case_issue.get("scope") or "") == "chapter"
                    or str(normalized.get("scope") or "") == "chapter"
                )
            )
            evidence = normalized.get("evidence") if isinstance(normalized.get("evidence"), dict) else {}
            metric = str(normalized.get("metric") or normalized.get("type") or "").lower()
            deterministic_chapter_fix = metric in {
                "dash_density",
                "dash_per_1000",
                "ai_punctuation_artifact",
                "explanatory_punctuation_artifact",
            }
            localizable_chapter_fix = metric in {
                "tier1_hit_count",
                "sentence_shell_count",
                "structure_word_cluster_count",
                "paragraph_shape_repeat_count",
                "mirrored_paragraph_opening_count",
                "uniform_sentence_streak_max",
                "abstract_bare_count",
                "high_advisory_count",
                "high_advisories",
            }
            advisory_bundle_fix = metric in {"high_advisories", "medium_advisories"}
            metric_spec = get_metric_spec(metric)
            if metric_spec is not None and metric_spec.status in {"implemented_tool", "implemented_candidate_tool"}:
                localizable_chapter_fix = True
            if metric_spec is not None and metric_spec.status in {"manual_with_reason", "degraded_system_issue"}:
                localizable_chapter_fix = False
            localization_status = str(
                evidence.get("localization_status") or normalized.get("localization_status") or ""
            )
            has_scene_candidate_text = any(
                bool(str(getattr(packet, "candidate_text", "") or "").strip())
                for packet in packets_by_scene.values()
            )
            has_explicit_local_evidence = bool(
                str(normalized.get("target_span") or "").strip()
                or normalized.get("evidence_spans")
                or localization_status == "localized"
                or str(normalized.get("_candidate_text") or "").strip()
            )
            if advisory_bundle_fix and not has_explicit_local_evidence:
                # advisory_bundle_fix（high_advisories / medium_advisories）是聚合统计指标，
                # 没有具体文本位置时不能伪装成可定位的自动修复，也不能从案件
                # 证据链中消失。保留为 chapter-level manual_review；若上游同时
                # 提供了具体 AI 痕迹，那些具体 finding 仍可各自生成 local_patch。
                localizable_chapter_fix = False

            has_local_evidence = bool(
                has_explicit_local_evidence
                or has_scene_candidate_text
            )
            chapter_unlocalized = bool(
                is_chapter_level
                and not deterministic_chapter_fix
                and not (localizable_chapter_fix and has_local_evidence)
                and not str(normalized.get("target_span") or "").strip()
                and localization_status != "localized"
            )
            target_packets = (
                [fallback_packet]
                if chapter_unlocalized or (
                    is_chapter_level
                    and localizable_chapter_fix
                    and has_local_evidence
                    and not str(normalized.get("target_span") or "").strip()
                )
                else list(packets_by_scene.values())
                if is_chapter_level
                else [
                    packets_by_scene.get(source_scene)
                    if isinstance(source_scene, int)
                    else None
                ]
            )
            if source_scenes and not is_chapter_level:
                target_packets = [packets_by_scene[scene] for scene in source_scenes]
            if not any(target_packets):
                target_packets = [fallback_packet]

            for packet in target_packets:
                if packet is None:
                    continue
                packet_violation = dict(normalized)
                if chapter_unlocalized:
                    packet_violation["source_scene"] = packet.scene_index
                    packet_violation["source_scenes"] = [packet.scene_index]
                    packet_violation["repairable_by_text"] = False
                    packet_violation["repairability"] = "human_review_required"
                    packet_violation["suggested_strategy"] = "manual_review"
                    packet_violation.setdefault("repair_scope", "chapter")
                    packet_violation.setdefault("distribution_policy", "chapter_unlocalized_single_case")
                else:
                    packet_violation["source_scene"] = packet.scene_index
                    packet_violation["source_scenes"] = [packet.scene_index]
                    if is_chapter_level and localizable_chapter_fix and has_local_evidence:
                        packet_violation["repairable_by_text"] = True
                        packet_violation["repairability"] = "auto_fixable"
                        packet_violation["suggested_strategy"] = "patch_text"
                        packet_violation.setdefault("repair_scope", "prose_text")
                        packet_violation.setdefault("distribution_policy", "chapter_localizable_single_case")
                key = FBIChapterCaseIntakeService._violation_merge_key(packet_violation)
                if key in existing_keys:
                    continue
                existing_keys.add(key)
                packet.blocking_violations.append(packet_violation)

    @staticmethod
    def _violation_merge_key(violation: dict) -> str:
        v_type = violation.get("violation_type") or violation.get("type") or ""
        target_span = violation.get("target_span") or violation.get("span") or ""
        if isinstance(target_span, dict):
            target_span = f"{target_span.get('start', '')}:{target_span.get('end', '')}"
        scene = violation.get("source_scene")
        if not isinstance(scene, int):
            scenes = violation.get("source_scenes") or []
            scene = scenes[0] if scenes and isinstance(scenes[0], int) else ""
        repair_domain = violation.get("repair_domain") or ""
        return f"{scene}:{repair_domain}:{v_type}:{target_span}"

    @staticmethod
    def _merge_violations(violations: list[dict]) -> list[dict]:
        """按 type + target_span 去重违规。

        去重键: f"{violation_type}:{target_span}" 。
        保留首次出现的违规，合并 source_scene 信息。
        """
        if not violations:
            return []

        seen: dict[str, dict] = {}
        for v in violations:
            v_type = v.get("violation_type") or v.get("type") or ""
            target_span = v.get("target_span") or v.get("span") or ""
            # target_span 可能是 dict，转为可哈希字符串
            if isinstance(target_span, dict):
                target_span = f"{target_span.get('start', '')}:{target_span.get('end', '')}"
            scene_key = v.get("source_scene")
            if not isinstance(scene_key, int):
                source_scenes = v.get("source_scenes") or []
                scene_key = source_scenes[0] if source_scenes and isinstance(source_scenes[0], int) else ""
            # Intake only removes duplicate reports of the same finding.  It
            # must not decide that different metrics/types describe one repair
            # obligation; RepairGoalGrouper performs that semantic operation
            # later from authority/ownership/desired-state fields.
            finding_id = str(
                v.get("issue_id")
                or v.get("violation_id")
                or v.get("id")
                or ""
            )
            if finding_id:
                dedup_key = f"{scene_key}:finding:{finding_id}"
            else:
                validator = str(
                    v.get("validator")
                    or v.get("source_validator")
                    or v.get("source")
                    or ""
                )
                metric = str(v.get("metric") or "")
                detail = str(v.get("detail") or v.get("reason") or "")
                dedup_key = (
                    f"{scene_key}:finding:{validator}:{v_type}:{metric}:"
                    f"{target_span}:{detail}"
                )

            if dedup_key in seen:
                # 合并 source_scene 信息
                existing = seen[dedup_key]
                existing_scenes = existing.get("source_scenes") or [existing.get("source_scene")]
                new_scene = v.get("source_scene")
                if new_scene and new_scene not in existing_scenes:
                    existing_scenes.append(new_scene)
                    existing["source_scenes"] = existing_scenes
                merged_types = set(existing.get("merged_violation_types") or [])
                merged_types.update(t for t in (existing.get("type"), existing.get("violation_type"), v.get("type"), v.get("violation_type")) if t)
                if merged_types:
                    existing["merged_violation_types"] = sorted(merged_types)
                merged_metrics = set(existing.get("merged_metrics") or [])
                merged_metrics.update(
                    str(item)
                    for item in (existing.get("metric"), v.get("metric"))
                    if item
                )
                if merged_metrics:
                    existing["merged_metrics"] = sorted(merged_metrics)
                details = existing.get("related_details") or []
                new_detail = v.get("detail") or v.get("reason")
                if new_detail and new_detail not in details and new_detail != existing.get("detail"):
                    details.append(new_detail)
                    existing["related_details"] = details
            else:
                entry = dict(v)
                # 标准化 source_scene → source_scenes
                if "source_scene" in entry and "source_scenes" not in entry:
                    entry["source_scenes"] = [entry["source_scene"]]
                seen[dedup_key] = entry

        return list(seen.values())

    @classmethod
    def _apply_enforcement_grades(cls, packet: SceneReviewPacket) -> None:
        """Rebucket legacy violations using the canonical product grade."""
        blocking: list[dict] = []
        advisory: list[dict] = []
        original_advisory_ids = {id(item) for item in packet.advisory_violations or []}
        for violation in [*(packet.blocking_violations or []), *(packet.advisory_violations or [])]:
            if not isinstance(violation, dict):
                continue
            was_advisory = id(violation) in original_advisory_ids
            item = dict(violation)
            metric = canonical_metric_name(
                item.get("metric")
                or item.get("violation_type")
                or item.get("type")
                or ""
            )
            classification = str(
                item.get("issue_classification") or item.get("classification") or ""
            )
            repair_scope = str(item.get("repair_scope") or item.get("scope") or "")
            system_owned = bool(
                classification == "validator_system_error"
                or repair_scope in {"system", "validator_system"}
                or metric.endswith("_unavailable")
            )
            hard = False if system_owned else (
                get_enforcement(metric).get("enforcement") == "hard_blocking"
                or cls._should_promote_to_blocking(item)
                or classification in {"planning_conflict", "contract_repair_required"}
                or (
                    item.get("blocks_commit") is True
                    and (
                        item.get("repairable_by_contract") is True
                        or repair_scope
                        in {"scene_contract", "chapter_contract", "outline_plan"}
                    )
                )
                or (
                    item.get("blocks_commit") is True
                    and str(item.get("enforcement") or "") != "advisory"
                    and get_metric_info(metric).get("category") == "unknown"
                    and (
                        str(item.get("repairability") or "") == "human_review_required"
                        or str(item.get("suggested_strategy") or "") == "manual_review"
                    )
                )
            )
            if hard:
                item["blocks_commit"] = True
                item["enforcement"] = "hard_blocking"
                if was_advisory:
                    item.setdefault("promotion_reason", "hard_correctness_advisory")
                    item.setdefault("original_scope", "advisory")
                if str(item.get("severity") or "").lower() not in {"critical", "high", "blocking"}:
                    item["severity"] = "high"
                blocking.append(item)
            else:
                item["blocks_commit"] = False
                item["enforcement"] = "advisory"
                advisory.append(item)
        packet.blocking_violations = cls._merge_violations(blocking)
        packet.advisory_violations = cls._merge_violations(advisory)

    @classmethod
    def _promote_hard_correctness_advisories(
        cls,
        merged_advisory: list[dict],
        *,
        existing_blocking: list[dict],
    ) -> tuple[list[dict], list[dict]]:
        """Promote fact/state/timeline findings into executable orders."""
        if not merged_advisory:
            return [], []

        blocking_keys = {
            cls._promotion_identity(item)
            for item in existing_blocking
        }
        promoted: list[dict] = []
        remaining: list[dict] = []
        for violation in merged_advisory:
            if not cls._should_promote_to_blocking(violation):
                remaining.append(violation)
                continue

            promoted_violation = dict(violation)
            promoted_violation["blocks_commit"] = True
            promoted_violation.setdefault("promotion_reason", "hard_correctness_advisory")
            promoted_violation.setdefault("original_scope", "advisory")
            severity = str(promoted_violation.get("severity") or "").lower()
            if severity not in {"blocking", "critical", "high"}:
                promoted_violation["severity"] = "high"

            key = cls._promotion_identity(promoted_violation)
            if key in blocking_keys:
                continue
            blocking_keys.add(key)
            promoted.append(promoted_violation)

        return promoted, remaining

    @staticmethod
    def _should_promote_to_blocking(violation: dict) -> bool:
        type_values = _violation_type_values(violation)
        if type_values & _HARD_CORRECTNESS_REPAIR_TYPES:
            return True
        return _is_fact_conflict(violation)

    @staticmethod
    def _promotion_identity(violation: dict) -> tuple:
        type_values = sorted(_violation_type_values(violation))
        v_type = type_values[0] if type_values else str(violation.get("type") or "")
        target_span = violation.get("target_span") or violation.get("span") or ""
        if isinstance(target_span, dict):
            target_span = f"{target_span.get('start', '')}:{target_span.get('end', '')}"
        source_scene = violation.get("source_scene")
        if not isinstance(source_scene, int):
            source_scenes = violation.get("source_scenes") or []
            source_scene = (
                source_scenes[0]
                if source_scenes and isinstance(source_scenes[0], int)
                else None
            )
        detail = violation.get("detail") or violation.get("reason") or ""
        return (source_scene, v_type, str(target_span), str(detail))

    # ------------------------------------------------------------------
    # 跨场景冲突检测
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_cross_scene_conflicts(packets: list[SceneReviewPacket]) -> list[dict]:
        """检测跨场景冲突。

        主要检测：
        - scene_i ending_state_claims != scene_{i+1} opening_state_claims
        - 后场景使用了前场景未建立的事实
        """
        conflicts: list[dict] = []
        if len(packets) < 2:
            return conflicts

        # 按 scene_index 排序
        sorted_packets = sorted(packets, key=lambda p: p.scene_index)

        for i in range(len(sorted_packets) - 1):
            curr = sorted_packets[i]
            nxt = sorted_packets[i + 1]

            # 1. 结束状态 vs 下一场景开始状态
            ending = curr.ending_state_claims
            opening = nxt.opening_state_claims

            if ending and opening:
                state_conflicts = _compare_state_claims(
                    ending, opening, curr.scene_index, nxt.scene_index,
                )
                conflicts.extend(state_conflicts)

            # 2. 后场景世界观声明 vs 前场景未建立
            if nxt.worldview_claims and curr.worldview_claims is not None:
                fact_conflicts = _detect_unestablished_facts(
                    curr.worldview_claims,
                    nxt.worldview_claims,
                    curr.scene_index,
                    nxt.scene_index,
                )
                conflicts.extend(fact_conflicts)

        return conflicts

    # ------------------------------------------------------------------
    # 违规回写
    # ------------------------------------------------------------------

    @staticmethod
    def _redistribute_violations(
        packets: list[SceneReviewPacket],
        merged_blocking: list[dict],
        merged_advisory: list[dict],
    ) -> None:
        """将去重后的违规回写到对应的场景包。

        按 source_scene / source_scenes 分配违规到对应场景包。
        """
        packet_map: dict[int, SceneReviewPacket] = {p.scene_index: p for p in packets}

        # 清空原有违规
        for p in packets:
            p.blocking_violations = []
            p.advisory_violations = []

        # 分配 blocking
        for v in merged_blocking:
            scenes = v.get("source_scenes") or [v.get("source_scene")]
            for scene_idx in scenes:
                pkt = packet_map.get(scene_idx)
                if pkt is not None:
                    pkt.blocking_violations.append(v)

        # 分配 advisory
        for v in merged_advisory:
            scenes = v.get("source_scenes") or [v.get("source_scene")]
            for scene_idx in scenes:
                pkt = packet_map.get(scene_idx)
                if pkt is not None:
                    pkt.advisory_violations.append(v)


# ---------------------------------------------------------------------------
# 辅助函数（模块级，便于测试）
# ---------------------------------------------------------------------------


def _compare_state_claims(
    ending: dict,
    opening: dict,
    scene_i: int,
    scene_j: int,
) -> list[dict]:
    """比较相邻场景的状态声明，返回冲突列表。

    检查 ending 中的键值是否与 opening 一致。
    不一致则生成一条跨场景冲突违规。
    """
    conflicts: list[dict] = []
    for key, end_val in ending.items():
        open_val = opening.get(key)
        if open_val is None:
            # Parallel scene review does not replay scene_i's ending state into
            # scene_j's opening context. Absence is therefore not a conflict;
            # only explicit contradictory claims are actionable.
            continue
        elif end_val != open_val:
            # 值不一致
            conflicts.append({
                "violation_type": "cross_scene_state_mismatch",
                "type": "cross_scene_state_mismatch",
                "severity": "blocking",
                "source_scene": scene_i,
                "affected_scene": scene_j,
                "source_scenes": [scene_i, scene_j],
                "detail": (
                    f"场景 {scene_i} 结束状态 '{key}'={end_val!r}，"
                    f"但场景 {scene_j} 开始状态 '{key}'={open_val!r}"
                ),
                "target_span": f"state:{key}",
                "repairability": "cross_scene_fixable",
            })
    return conflicts


def _detect_unestablished_facts(
    earlier_claims: list[dict],
    later_claims: list[dict],
    earlier_scene: int,
    later_scene: int,
) -> list[dict]:
    """检测后场景使用了前场景未建立的事实。

    对比 later_claims 中引用的 fact_id 是否在 earlier_claims 中存在。
    """
    conflicts: list[dict] = []

    # 收集前场景已建立的事实标识
    established_ids: set[str] = set()
    for claim in earlier_claims:
        fact_id = claim.get("fact_id") or claim.get("id") or ""
        if fact_id:
            established_ids.add(fact_id)

    # 检查后场景引用的事实
    for claim in later_claims:
        ref_id = claim.get("references_fact_id") or claim.get("depends_on") or ""
        if not ref_id:
            continue
        if ref_id not in established_ids:
            conflicts.append({
                "violation_type": "unestablished_fact_reference",
                "type": "unestablished_fact_reference",
                "severity": "high",
                "source_scene": later_scene,
                "affected_scene": earlier_scene,
                "source_scenes": [later_scene],
                "detail": (
                    f"场景 {later_scene} 引用了事实 '{ref_id}'，"
                    f"但场景 {earlier_scene} 未建立该事实"
                ),
                "target_span": f"fact:{ref_id}",
                "repairability": "cross_scene_fixable",
            })

    return conflicts


# ---------------------------------------------------------------------------
# FBIChapterRepairPlanner
# ---------------------------------------------------------------------------


class FBIChapterRepairPlanner:
    """整章修复计划生成器。

    职责：
    1. 接收已验证的 ChapterReviewCase
    2. 分析全章违规
    3. 按归因规则确定 owner_scene 和 repair_type
    4. 生成 ChapterRepairOrder 列表
    5. 确定计划状态（clean / needs_repair / needs_contract_repair / needs_human_review）
    6. 返回 ChapterRepairPlan
    """

    async def plan(
        self,
        case: ChapterReviewCase,
        *,
        run_blueprint_agent: bool = True,
    ) -> ChapterRepairPlan:
        """根据已验证案件生成修复计划。

        Args:
            case: 已通过 FBIChapterCaseIntakeService.intake() 验证的案件。
            run_blueprint_agent: 是否执行在线蓝图 Agent。仅当调用方会从
                已编译订单立即构建确定性蓝图时才可关闭；默认保持正式路径。

        Returns:
            ChapterRepairPlan 实例。
        """
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        _logger.info(
            "ChapterRepairPlanner: case_id=%s packets=%d round=%d",
            case.case_id, len(case.scene_packets), case.review_round,
        )

        # --- 收集所有违规 ---
        all_blocking: list[dict] = []
        all_advisory: list[dict] = []
        for pkt in case.scene_packets:
            if pkt.unavailable:
                continue
            all_blocking.extend(pkt.blocking_violations)
            all_advisory.extend(pkt.advisory_violations)

        total_violations = len(all_blocking) + len(all_advisory)
        blocking_count = len(all_blocking)

        # Advisory findings remain observable quality signals, but they must not
        # enter automatic convergence. Repairing them repeatedly expands token
        # spend and can destabilize prose that already passed the commit gate.
        actionable_violations = list(all_blocking)
        actionable_violations, diagnosis_set = FBIReviewMinister().annotate_violations(
            case,
            actionable_violations,
        )

        # --- 无可执行违规 → clean ---
        if not actionable_violations:
            global_notes = ["No executable repair findings found."]
            if all_advisory:
                global_notes.append(
                    f"{len(all_advisory)} advisory finding(s) recorded for quality follow-up; "
                    "advisories are excluded from automatic repair convergence."
                )
            _logger.info("ChapterRepairPlanner: clean case_id=%s", case.case_id)
            return ChapterRepairPlan(
                case_id=case.case_id,
                status="clean",
                orders=[],
                global_notes=global_notes,
                total_violations=total_violations,
                blocking_violations=0,
                cross_scene_conflicts=0,
                repair_strategy_summary={
                    "fbi_department_phase": "repair_planning",
                    "review_minister": diagnosis_set.trace,
                },
                review_round=case.review_round,
                created_at=now,
            )

        # --- 生成修复命令 ---
        orders = self._generate_orders_from_diagnoses(
            case,
            diagnosis_set,
            actionable_violations,
        )

        # --- 统计跨场景冲突 ---
        cross_scene_count = sum(
            1 for o in orders if o.repair_type == "cross_scene_alignment"
        )

        # --- 确定计划状态 ---
        plan_status = self._determine_plan_status(case, orders)

        # --- 全局备注 ---
        global_notes = self._build_global_notes(case, orders, total_violations, blocking_count)
        if all_advisory:
            global_notes.append(
                f"Recorded {len(all_advisory)} advisory finding(s) for follow-up; "
                "only hard blocking findings were scheduled for automatic repair."
            )

        plan = ChapterRepairPlan(
            case_id=case.case_id,
            status=plan_status,
            orders=orders,
            global_notes=global_notes,
            total_violations=total_violations,
            blocking_violations=blocking_count,
            cross_scene_conflicts=cross_scene_count,
            execution_layers=self._execution_layers_for_orders(orders),
            execution_scope_trace=self._execution_scope_trace(orders),
            repair_strategy_summary=self._repair_strategy_summary(orders),
            review_round=case.review_round,
            created_at=now,
        )
        issue_to_order_ids: dict[str, list[str]] = {}
        for order in plan.orders:
            issue_id_aliases = list(order.source_violation_ids or [])
            for detail in order.violation_details or []:
                if not isinstance(detail, dict):
                    continue
                issue_id_aliases.extend([
                    detail.get("issue_id"),
                    detail.get("violation_id"),
                    detail.get("id"),
                ])
            for issue_id in dict.fromkeys(
                str(item) for item in issue_id_aliases if item
            ):
                issue_to_order_ids.setdefault(issue_id, []).append(order.order_id)
        scene_texts = {
            packet.scene_index: packet.candidate_text or ""
            for packet in case.scene_packets or []
            if isinstance(packet.scene_index, int)
        }

        # === T4: 调用审查蓝图官 Agent（一次会话+三轮 tool calling） ===
        # agent 内部完成 S1 归一化 → S2 三轮 tool calling → 统一蓝图 → 协议校验
        # 这里传入预注释结果避免重复 annotate_violations
        if run_blueprint_agent:
            from app.agents.fbi.review_blueprint_agent import FBIReviewBlueprintAgent
            agent = FBIReviewBlueprintAgent()
            agent_result = await agent.execute({
                "case": case,
                "all_blocking": actionable_violations,
                "annotated": actionable_violations,  # 预注释结果（已含 issue_id）
                "diagnosis_set": diagnosis_set,  # 预生成诊断集
                "orders": plan.orders,
                "scene_texts": scene_texts,
            })
        else:
            agent_result = {
                "status": "skipped",
                "revision_blueprint": RevisionBlueprint(
                    blueprint_id=f"orders_only_{case.case_id}",
                    case_id=case.case_id,
                    work_units=[],
                    planning_trace=[{
                        "status": "blueprint_agent_skipped_for_precompiled_orders",
                    }],
                    status="degraded",
                ).model_dump(),
                "trace": {
                    "mode": "precompiled_orders",
                    "session_status": "skipped",
                    "failure_reason": "caller_will_compile_deterministic_blueprint",
                },
            }

        # 从 agent 结果重建 RevisionBlueprint
        blueprint_data = agent_result.get("revision_blueprint") or {}
        try:
            blueprint = RevisionBlueprint.model_validate(blueprint_data)
        except Exception as exc:
            _logger.warning(
                "ChapterRepairPlanner: agent blueprint 重建失败，降级空蓝图 case_id=%s: %s",
                case.case_id, exc,
            )
            blueprint = RevisionBlueprint(
                blueprint_id=f"fallback_{case.case_id}",
                case_id=case.case_id,
                work_units=[],
                planning_trace=[{"status": "agent_blueprint_rebuild_failed", "error": str(exc)}],
                status="degraded",
            )

        # 映射 issue_id → source_order_ids（agent 产出的 work_unit 只含 issue_id）
        self._attach_source_order_ids_to_units(blueprint.work_units, issue_to_order_ids)
        self._attach_goal_ids_to_units(blueprint.work_units, diagnosis_set)
        blueprint.work_units, goal_unit_trace = self._coalesce_goal_work_units(
            blueprint.work_units,
        )
        blueprint.work_units, goal_expansion_trace = self._expand_global_goal_work_units(
            blueprint.work_units,
            diagnosis_set,
            scene_indices=sorted(scene_texts),
            orders=plan.orders,
        )

        # === EditWindowPlanner：窗口分组、compound 合并、family 拆分 ===
        planned_units, edit_window_trace = EditWindowPlanner().plan_work_units(
            blueprint.work_units,
            scene_texts,
            case_id=case.case_id,
        )
        blueprint = blueprint.model_copy(update={
            "work_units": planned_units,
            "planning_trace": [
                *(blueprint.planning_trace or []),
                {
                    "source_blueprint": "fbi_review_blueprint_agent_unified",
                    "edit_window_planner": edit_window_trace,
                    "goal_work_unit_coalescing": goal_unit_trace,
                    "goal_work_unit_expansion": goal_expansion_trace,
                },
            ],
            "completion_summary": {
                **(blueprint.completion_summary or {}),
                "edit_window_planner": edit_window_trace,
            },
        })

        agent_trace = agent_result.get("trace") or {}

        manual_order_ids = {
            order.order_id
            for order in plan.orders
            if order.repair_type == "manual_review"
        }
        if manual_order_ids:
            blueprint.work_units = [
                unit for unit in blueprint.work_units
                if not (set(unit.source_order_ids or []) & manual_order_ids)
            ]
            for order in plan.orders:
                if order.order_id in manual_order_ids:
                    order.tool_commands = []
        canonical_orders, canonical_trace = self._canonicalize_orders_for_work_units(
            plan.orders,
            blueprint.work_units,
        )
        plan.orders = canonical_orders
        self._sync_work_units_to_orders(plan.orders, blueprint.work_units)
        plan.revision_blueprint = blueprint
        plan.work_units = blueprint.work_units
        plan.repair_goals = [goal.model_dump() for goal in diagnosis_set.repair_goals]
        plan.execution_layers = self._execution_layers_for_orders(plan.orders)
        plan.execution_scope_trace = self._execution_scope_trace(plan.orders)
        plan.repair_strategy_summary = self._repair_strategy_summary(plan.orders)
        plan.repair_strategy_summary["fbi_department_phase"] = "repair_planning"
        plan.repair_strategy_summary["review_minister"] = diagnosis_set.trace
        plan.repair_strategy_summary["revision_blueprint"] = {
            "status": blueprint.status,
            "work_units": len(blueprint.work_units),
            "tool_commands": sum(
                len(unit.tool_batch.commands)
                for unit in blueprint.work_units
            ),
            "agent_status": agent_result.get("status", ""),
            "agent_mode": agent_trace.get("mode", ""),
            "agent_session_status": agent_trace.get("session_status", ""),
            "agent_failure_reason": agent_trace.get("failure_reason", ""),
            "edit_window_planner": edit_window_trace,
            "goal_work_unit_coalescing": goal_unit_trace,
            "goal_work_unit_expansion": goal_expansion_trace,
            "canonical_order_projection": canonical_trace,
            "executable_order_count": len({
                order_id
                for unit in blueprint.work_units
                for order_id in (unit.source_order_ids or [])
            }),
            "total_order_count": len(plan.orders),
            "executable_blueprint_rate": (
                round(
                    len({
                        order_id
                        for unit in blueprint.work_units
                        for order_id in (unit.source_order_ids or [])
                    }) / len(plan.orders),
                    4,
                )
                if plan.orders
                else 1.0
            ),
            "blueprint_completion_required_count": max(
                0,
                len(plan.orders)
                - len({
                    order_id
                    for unit in blueprint.work_units
                    for order_id in (unit.source_order_ids or [])
                }),
            ),
            "completion_summary": blueprint.completion_summary,
            "validator_snapshot_hash": (blueprint.validator_snapshot or {}).get("contract_hash"),
            "fbi_review_blueprint_agent": agent_trace,
        }

        _logger.info(
            "ChapterRepairPlanner: case_id=%s status=%s orders=%d "
            "blocking=%d cross_scene=%d round=%d",
            case.case_id, plan_status, len(orders),
            blocking_count, cross_scene_count, case.review_round,
        )

        return plan

    # ------------------------------------------------------------------
    # 修复命令生成
    # ------------------------------------------------------------------

    @staticmethod
    def _attach_source_order_ids_to_units(
        units,
        issue_to_order_ids: dict[str, list[str]],
    ) -> None:
        """Map LLM issue ids back to concrete repair orders before execution."""
        for unit in units:
            issue_ids: list[str] = []
            issue_ids.extend(unit.source_violation_ids or [])
            issue_ids.extend(unit.compound_issue_ids or [])
            for command in unit.tool_batch.commands:
                issue_ids.extend(command.source_issue_ids or [])
            issue_ids = list(dict.fromkeys(str(item) for item in issue_ids if item))
            if not unit.source_violation_ids:
                unit.source_violation_ids = issue_ids

            source_order_ids = list(unit.source_order_ids or [])
            for issue_id in issue_ids:
                source_order_ids.extend(issue_to_order_ids.get(issue_id, []))
            source_order_ids = list(dict.fromkeys(str(item) for item in source_order_ids if item))
            unit.source_order_ids = source_order_ids
            unit.tool_batch.source_order_ids = source_order_ids

    @staticmethod
    def _attach_goal_ids_to_units(units, diagnosis_set) -> None:
        """Project semantic goal identity onto executable work units."""
        issue_to_goal_ids: dict[str, list[str]] = {}
        for diagnosis in diagnosis_set.diagnoses:
            for issue_id in diagnosis.issue_ids:
                issue_to_goal_ids.setdefault(issue_id, []).extend(
                    diagnosis.repair_goal_ids or []
                )
        for unit in units:
            goal_ids = list(unit.source_goal_ids or [])
            for issue_id in unit.source_violation_ids or []:
                goal_ids.extend(issue_to_goal_ids.get(issue_id, []))
            unit.source_goal_ids = list(dict.fromkeys(
                str(goal_id) for goal_id in goal_ids if goal_id
            ))

    @staticmethod
    def _coalesce_goal_work_units(
        units: list[RepairWorkUnit],
    ) -> tuple[list[RepairWorkUnit], dict[str, Any]]:
        """Guarantee one pre-expansion physical unit per connected Goal set.

        LLM planning still reports finding ids for compatibility.  This gate
        prevents it from fanning one semantic RepairGoal back out into several
        independent orders.  Chapter-global physical expansion happens only
        after this invariant has been established.
        """

        if not units:
            return [], {"input_work_units": 0, "output_work_units": 0, "merged": []}

        remaining = set(range(len(units)))
        components: list[list[int]] = []
        while remaining:
            seed = min(remaining)
            component = {seed}
            goal_ids = set(units[seed].source_goal_ids or [])
            changed = True
            while changed:
                changed = False
                for index in sorted(remaining - component):
                    candidate_goals = set(units[index].source_goal_ids or [])
                    if goal_ids and candidate_goals and goal_ids & candidate_goals:
                        component.add(index)
                        goal_ids.update(candidate_goals)
                        changed = True
            remaining.difference_update(component)
            components.append(sorted(component))

        def _dedupe_values(values):
            seen: set[str] = set()
            result = []
            for value in values:
                marker = repr(value)
                if marker in seen:
                    continue
                seen.add(marker)
                result.append(value)
            return result

        def _merge_boundaries(values: list[dict]) -> dict:
            merged: dict[str, Any] = {}
            for value in values:
                if not isinstance(value, dict):
                    continue
                for key, item in value.items():
                    if isinstance(item, list):
                        merged[key] = _dedupe_values([
                            *(merged.get(key) or []),
                            *item,
                        ])
                    elif isinstance(item, bool):
                        merged[key] = bool(merged.get(key, False) or item)
                    elif key == "max_delta_chars" and isinstance(item, int) and item > 0:
                        current = merged.get(key)
                        merged[key] = min(current, item) if isinstance(current, int) else item
                    elif key not in merged and item not in (None, ""):
                        merged[key] = item
            return merged

        output: list[RepairWorkUnit] = []
        merged_trace: list[dict[str, Any]] = []
        for component in components:
            members = [units[index] for index in component]
            if len(members) == 1:
                output.append(members[0])
                continue
            members.sort(key=lambda unit: unit.work_unit_id)
            base = members[0].model_copy(deep=True)
            all_commands = [
                command
                for member in members
                for command in (member.tool_batch.commands or [])
            ]
            creative_commands = [
                command
                for command in all_commands
                if command.operation == "llm_creative_rewrite"
            ]
            if creative_commands:
                command = creative_commands[0].model_copy(deep=True)
                command.target_span = ""
                command.old_text = ""
                command.new_text = ""
                command.replacement = ""
                command.anchor_text = ""
                commands = [command]
            else:
                commands = _dedupe_values(all_commands)

            source_goal_ids = list(dict.fromkeys(
                goal_id
                for member in members
                for goal_id in (member.source_goal_ids or [])
                if goal_id
            ))
            source_issue_ids = list(dict.fromkeys(
                issue_id
                for member in members
                for issue_id in (member.source_violation_ids or [])
                if issue_id
            ))
            source_order_ids = list(dict.fromkeys(
                order_id
                for member in members
                for order_id in (member.source_order_ids or [])
                if order_id
            ))
            target_scenes = sorted({
                scene
                for member in members
                for scene in (member.target_scenes or [])
                if isinstance(scene, int)
            })
            owners = {
                member.owner_scene
                for member in members
                if isinstance(member.owner_scene, int)
            }
            anchors = _dedupe_values([
                member.write_anchor
                for member in members
                if member.write_anchor
            ])
            anchor = anchors[0] if len(anchors) == 1 else {}
            work_unit_id = "goal_unit_" + hashlib.sha256(
                ":".join(sorted(source_goal_ids)).encode("utf-8")
            ).hexdigest()[:16]
            for command_index, command in enumerate(commands):
                command.command_id = f"{work_unit_id}:cmd:{command_index}"
                command.source_issue_ids = list(source_issue_ids)
            base.work_unit_id = work_unit_id
            base.local_id = work_unit_id
            base.owner_scene = next(iter(owners)) if len(owners) == 1 else None
            base.target_scenes = target_scenes
            base.source_order_ids = source_order_ids
            base.source_violation_ids = source_issue_ids
            base.source_goal_ids = source_goal_ids
            base.read_context_spans = _dedupe_values([
                span for member in members for span in member.read_context_spans
            ])
            base.diagnostic_evidence_spans = _dedupe_values([
                span for member in members for span in member.diagnostic_evidence_spans
            ])
            base.write_anchor = dict(anchor)
            base.placement_status = "resolved" if anchor else "required"
            base.edit_window = {
                "anchor": str(anchor.get("text") or "") if anchor else "",
                "source": "repair_goal_coalescing",
            }
            base.edit_window_id = ""
            base.compound_issue_ids = list(source_issue_ids)
            base.compound_issue_families = list(dict.fromkeys(
                family
                for member in members
                for family in (member.compound_issue_families or [])
                if family
            ))
            base.merge_policy = "goal_atomic"
            base.protection_boundary = _merge_boundaries([
                member.protection_boundary for member in members
            ])
            base.max_delta_chars = min(
                (
                    member.max_delta_chars
                    for member in members
                    if isinstance(member.max_delta_chars, int)
                    and member.max_delta_chars > 0
                ),
                default=None,
            )
            base.issue_summary = "; ".join(dict.fromkeys(
                member.issue_summary for member in members if member.issue_summary
            ))[:1000]
            base.tool_batch = ToolCommandBatch(
                batch_id=f"{work_unit_id}:batch",
                source_blueprint_id="repair_goal_coalescing",
                source_work_unit_id=work_unit_id,
                commands=commands,
                target_scenes=target_scenes,
                source_order_ids=source_order_ids,
                acceptance_criteria=_dedupe_values([
                    criterion
                    for member in members
                    for criterion in (member.tool_batch.acceptance_criteria or [])
                ]),
            )
            member_ids = {member.work_unit_id for member in members}
            base.dependencies = list(dict.fromkeys(
                dependency
                for member in members
                for dependency in (member.dependencies or [])
                if dependency not in member_ids
            ))
            output.append(base)
            merged_trace.append({
                "source_work_unit_ids": sorted(member_ids),
                "work_unit_id": work_unit_id,
                "source_goal_ids": source_goal_ids,
            })

        return output, {
            "input_work_units": len(units),
            "output_work_units": len(output),
            "merged": merged_trace,
            "invariant_satisfied": all(
                sum(goal_id in (unit.source_goal_ids or []) for unit in output) == 1
                for goal_id in {
                    goal_id
                    for unit in output
                    for goal_id in (unit.source_goal_ids or [])
                }
            ),
        }

    @staticmethod
    def _expand_global_goal_work_units(
        units,
        diagnosis_set,
        *,
        scene_indices: list[int],
        orders: list[ChapterRepairOrder],
    ):
        """Split one semantic global goal into one physical unit per scene.

        This preserves goal deduplication while acknowledging that a physical
        patch cannot safely mutate multiple frozen scene texts as one unit.
        """
        goals_by_id = {goal.goal_id: goal for goal in diagnosis_set.repair_goals}
        orders_by_goal: dict[str, list[str]] = {}
        for order in orders:
            for goal_id in order.source_goal_ids or []:
                orders_by_goal.setdefault(goal_id, []).append(order.order_id)

        def _finding_id(finding: dict[str, Any]) -> str:
            return str(
                finding.get("issue_id")
                or finding.get("violation_id")
                or finding.get("id")
                or ""
            )

        def _finding_scene(finding: dict[str, Any]) -> int | None:
            for key in (
                "obligation_owner_scene",
                "owner_scene",
                "source_scene",
                "scene_index",
            ):
                value = finding.get(key)
                if isinstance(value, int) and not isinstance(value, bool):
                    return value
            scenes = finding.get("source_scenes") or []
            return next(
                (
                    value
                    for value in scenes
                    if isinstance(value, int) and not isinstance(value, bool)
                ),
                None,
            )

        def _project(
            unit: RepairWorkUnit,
            goal_ids: list[str],
            *,
            scene_index: int | None,
            suffix: str,
        ) -> RepairWorkUnit:
            goals = [goals_by_id[goal_id] for goal_id in goal_ids if goal_id in goals_by_id]
            all_goal_issue_ids = list(dict.fromkeys(
                issue_id
                for goal in goals
                for issue_id in goal.source_issue_ids
                if issue_id
            ))
            scoped_issue_ids = list(dict.fromkeys(
                _finding_id(finding)
                for goal in goals
                for finding in (goal.source_findings or [])
                if isinstance(finding, dict)
                and (scene_index is None or _finding_scene(finding) == scene_index)
                and _finding_id(finding)
            ))
            if not scoped_issue_ids:
                scoped_issue_ids = list(all_goal_issue_ids)

            child = unit.model_copy(deep=True)
            child.work_unit_id = f"{unit.work_unit_id}:{suffix}"
            child.local_id = f"{unit.local_id}:{suffix}"
            child.source_goal_ids = list(goal_ids)
            child.source_violation_ids = list(scoped_issue_ids)
            child.compound_issue_ids = list(scoped_issue_ids)
            child.source_order_ids = list(dict.fromkeys(
                order_id
                for goal_id in goal_ids
                for order_id in orders_by_goal.get(goal_id, [])
            ))
            if scene_index is not None:
                child.owner_scene = scene_index
                child.target_scenes = [scene_index]
            child.edit_window = {
                "anchor": "",
                "source": "global_goal_physical_projection",
                "global_goal_scene": scene_index,
            }
            child.edit_window_id = ""
            child.write_anchor = {}
            child.placement_status = "required"
            child.read_context_spans = [
                span
                for span in (child.read_context_spans or [])
                if not isinstance(span, dict)
                or scene_index is None
                or span.get("scene_index") == scene_index
            ]
            child.diagnostic_evidence_spans = [
                span
                for span in (child.diagnostic_evidence_spans or [])
                if not isinstance(span, dict)
                or scene_index is None
                or span.get("scene_index") == scene_index
            ]

            projected_commands = []
            goal_issue_set = set(all_goal_issue_ids)
            for command in child.tool_batch.commands:
                command_issues = set(command.source_issue_ids or [])
                if command_issues and goal_issue_set and not command_issues & goal_issue_set:
                    continue
                projected_commands.append(command)
            child.tool_batch.commands = projected_commands
            child.tool_batch.batch_id = f"{child.work_unit_id}:batch"
            child.tool_batch.source_work_unit_id = child.work_unit_id
            child.tool_batch.source_order_ids = list(child.source_order_ids)
            child.tool_batch.target_scenes = list(child.target_scenes)
            child.tool_batch.acceptance_criteria = [
                criterion.model_dump()
                for goal in goals
                for criterion in goal.acceptance_criteria
            ]
            for command_index, command in enumerate(child.tool_batch.commands):
                command.scene_index = scene_index
                command.command_id = f"{child.work_unit_id}:cmd:{command_index}"
                command.source_issue_ids = list(scoped_issue_ids)
            return child

        expanded: list[RepairWorkUnit] = []
        split_units: list[dict[str, Any]] = []
        for unit in units:
            global_goal_ids = [
                goal_id
                for goal_id in unit.source_goal_ids or []
                if goal_id in goals_by_id
                and goals_by_id[goal_id].mutation_kind == "global_transform"
                and goals_by_id[goal_id].owner_scope.get("scope") == "chapter"
            ]
            if not global_goal_ids:
                expanded.append(unit)
                continue

            local_goal_ids = [
                goal_id
                for goal_id in unit.source_goal_ids or []
                if goal_id not in set(global_goal_ids)
            ]
            if local_goal_ids:
                expanded.append(_project(
                    unit,
                    local_goal_ids,
                    scene_index=unit.owner_scene,
                    suffix="local",
                ))

            child_ids: list[str] = []
            physical_scenes: set[int] = set()
            for goal_id in global_goal_ids:
                goal = goals_by_id[goal_id]
                goal_scenes = sorted({
                    value
                    for value in (goal.owner_scope.get("scene_indices") or scene_indices)
                    if isinstance(value, int)
                })
                for scene_index in goal_scenes:
                    suffix_hash = hashlib.sha256(
                        f"{unit.work_unit_id}:{goal_id}:scene:{scene_index}".encode("utf-8")
                    ).hexdigest()[:12]
                    child = _project(
                        unit,
                        [goal_id],
                        scene_index=scene_index,
                        suffix=f"scene:{suffix_hash}",
                    )
                    expanded.append(child)
                    child_ids.append(child.work_unit_id)
                    physical_scenes.add(scene_index)
            split_units.append({
                "source_work_unit_id": unit.work_unit_id,
                "source_goal_ids": list(global_goal_ids),
                "scene_indices": sorted(physical_scenes),
                "successor_work_unit_ids": child_ids,
            })
        return expanded, {
            "input_work_units": len(units),
            "output_work_units": len(expanded),
            "split_global_work_units": len(split_units),
            "splits": split_units,
        }

    def _generate_orders_from_diagnoses(
        self,
        case: ChapterReviewCase,
        diagnosis_set,
        annotated_findings: list[dict],
    ) -> list[ChapterRepairOrder]:
        """Create planning projections per semantic diagnosis, never per finding."""
        findings_by_id = {
            str(
                finding.get("issue_id")
                or finding.get("violation_id")
                or finding.get("id")
                or ""
            ): finding
            for finding in annotated_findings
            if isinstance(finding, dict)
        }
        goals_by_id = {goal.goal_id: goal for goal in diagnosis_set.repair_goals}
        orders: list[ChapterRepairOrder] = []
        for order_index, diagnosis in enumerate(diagnosis_set.diagnoses):
            members = [
                findings_by_id[issue_id]
                for issue_id in diagnosis.issue_ids
                if issue_id in findings_by_id
            ]
            members = sorted(
                members,
                key=lambda item: str(
                    item.get("issue_id")
                    or item.get("violation_id")
                    or item.get("id")
                    or ""
                ),
            )
            representative = dict(members[0] if members else {})
            representative["issue_id"] = (
                diagnosis.issue_ids[0]
                if diagnosis.issue_ids
                else diagnosis.diagnosis_id
            )
            representative["fbi_diagnosis"] = diagnosis.model_dump()
            representative["repair_intent"] = diagnosis.repair_intent.model_dump()
            representative["revision_blueprint"] = diagnosis.revision_blueprint.model_dump()
            representative["tool_blueprint"] = dict(
                diagnosis.revision_blueprint.tool_blueprint or {}
            )
            representative["diagnosis_id"] = diagnosis.diagnosis_id
            representative["blocks_commit"] = diagnosis.blocks_commit
            if diagnosis.scene_index is not None:
                representative["source_scene"] = diagnosis.scene_index

            order = self._violation_to_order(
                representative,
                case,
                order_index,
                is_blocking=diagnosis.blocks_commit,
            )
            if order is None:
                continue
            order.source_violation_ids = list(diagnosis.issue_ids)
            order.source_goal_ids = list(diagnosis.repair_goal_ids)
            chapter_goal_scenes = sorted({
                scene_index
                for goal_id in diagnosis.repair_goal_ids
                if goal_id in goals_by_id
                and goals_by_id[goal_id].owner_scope.get("scope") == "chapter"
                for scene_index in (
                    goals_by_id[goal_id].owner_scope.get("scene_indices") or []
                )
                if isinstance(scene_index, int)
            })
            if chapter_goal_scenes:
                order.owner_scene = None
                order.target_scenes = chapter_goal_scenes
                order.read_scope = [f"scene:{scene}" for scene in chapter_goal_scenes]
                order.write_scope = [f"scene:{scene}" for scene in chapter_goal_scenes]
            order.violation_details = [dict(item) for item in members]
            order.reason = diagnosis.problem_statement
            order.repair_brief = {
                **(order.repair_brief or {}),
                "repair_goals": [
                    goals_by_id[goal_id].model_dump()
                    for goal_id in diagnosis.repair_goal_ids
                    if goal_id in goals_by_id
                ],
                "source_finding_ids": list(diagnosis.issue_ids),
                "canonical_scope": "diagnosis_projection",
            }
            order.expected_after_repair = {
                **(order.expected_after_repair or {}),
                "covered_goal_ids": list(diagnosis.repair_goal_ids),
                "acceptance_criteria": [
                    item.model_dump() for item in diagnosis.acceptance_criteria
                ],
            }
            orders.append(order)

        priority_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        orders.sort(key=lambda order: (
            priority_rank.get(order.priority, 99),
            order.order_id,
        ))
        return orders

    @staticmethod
    def _canonicalize_orders_for_work_units(
        orders: list[ChapterRepairOrder],
        units,
    ) -> tuple[list[ChapterRepairOrder], dict[str, Any]]:
        """Make each atomic work unit own exactly one executable order.

        Preliminary diagnosis orders remain useful planning evidence, but the
        returned plan exposes only canonical executable orders plus genuinely
        uncovered/manual projections.  Finding count can therefore never
        multiply executor or LLM calls.
        """
        by_id = {order.order_id: order for order in orders}
        reference_counts: dict[str, int] = {}
        for unit in units:
            for order_id in set(unit.source_order_ids or []):
                reference_counts[order_id] = reference_counts.get(order_id, 0) + 1

        covered_projection_ids: set[str] = set()
        unit_to_order_id: dict[str, str] = {}
        old_to_canonical: dict[str, list[str]] = {}
        canonical: list[ChapterRepairOrder] = []

        def _dedupe_dicts(items: list[dict]) -> list[dict]:
            seen: set[str] = set()
            result: list[dict] = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                key = str(
                    item.get("issue_id")
                    or item.get("violation_id")
                    or item.get("id")
                    or repr(sorted(item.items(), key=lambda pair: str(pair[0])))
                )
                if key in seen:
                    continue
                seen.add(key)
                result.append(dict(item))
            return result

        def _dedupe_values(items: list[Any]) -> list[Any]:
            seen: set[str] = set()
            result: list[Any] = []
            for item in items:
                key = repr(item)
                if key in seen:
                    continue
                seen.add(key)
                result.append(item)
            return result

        for unit in units:
            source_ids = list(dict.fromkeys(unit.source_order_ids or []))
            source_orders = [by_id[order_id] for order_id in source_ids if order_id in by_id]
            covered_projection_ids.update(order.order_id for order in source_orders)
            if not source_orders:
                # A protocol-valid work unit must still have a control-plane
                # order so postchecks and workflow status cannot be skipped.
                source_orders = [ChapterRepairOrder(
                    order_id=f"projection_{unit.work_unit_id}",
                    owner_scene=unit.owner_scene,
                    target_scenes=list(unit.target_scenes or []),
                    repair_type="local_patch",
                    reason=unit.issue_summary,
                )]

            preserve_id = (
                len(source_orders) == 1
                and reference_counts.get(source_orders[0].order_id, 0) == 1
            )
            canonical_id = (
                source_orders[0].order_id
                if preserve_id
                else f"order_{unit.work_unit_id}"
            )
            template = source_orders[0].model_copy(deep=True)
            violation_details = _dedupe_dicts([
                detail
                for source_order in source_orders
                for detail in (source_order.violation_details or [])
            ])
            source_violation_ids = list(dict.fromkeys(
                list(unit.source_violation_ids or [])
                or [
                    issue_id
                    for source_order in source_orders
                    for issue_id in (source_order.source_violation_ids or [])
                ]
            ))
            scoped_violation_details = [
                detail
                for detail in violation_details
                if str(
                    detail.get("issue_id")
                    or detail.get("violation_id")
                    or detail.get("id")
                    or ""
                ) in source_violation_ids
            ]
            if scoped_violation_details:
                violation_details = scoped_violation_details
            source_goal_ids = list(dict.fromkeys(
                list(unit.source_goal_ids or [])
                or [
                    goal_id
                    for source_order in source_orders
                    for goal_id in (source_order.source_goal_ids or [])
                ]
            ))
            repair_goals = _dedupe_dicts([
                goal
                for source_order in source_orders
                for goal in ((source_order.repair_brief or {}).get("repair_goals") or [])
                if isinstance(goal, dict)
                if not source_goal_ids or goal.get("goal_id") in source_goal_ids
            ])
            scoped_repair_goals: list[dict] = []
            for goal in repair_goals:
                scoped_goal = dict(goal)
                goal_issue_ids = [
                    str(issue_id)
                    for issue_id in (goal.get("source_issue_ids") or [])
                    if issue_id
                ]
                scoped_issue_ids = [
                    issue_id
                    for issue_id in goal_issue_ids
                    if issue_id in source_violation_ids
                ]
                if scoped_issue_ids:
                    scoped_goal["source_issue_ids"] = scoped_issue_ids
                    source_findings = [
                        finding
                        for finding in (goal.get("source_findings") or [])
                        if isinstance(finding, dict)
                        and str(
                            finding.get("issue_id")
                            or finding.get("violation_id")
                            or finding.get("id")
                            or ""
                        ) in scoped_issue_ids
                    ]
                    if source_findings:
                        scoped_goal["source_findings"] = source_findings
                scoped_repair_goals.append(scoped_goal)
            target_metrics = _dedupe_dicts([
                metric
                for source_order in source_orders
                for metric in ((source_order.repair_brief or {}).get("target_metrics") or [])
            ])
            merged_preserve: dict[str, Any] = {}
            merged_edit_scope: dict[str, Any] = {}
            for source_order in source_orders:
                brief = source_order.repair_brief or {}
                preserve = brief.get("preserve") if isinstance(brief, dict) else {}
                if isinstance(preserve, dict):
                    for key, value in preserve.items():
                        if isinstance(value, list):
                            merged_preserve[key] = _dedupe_values([
                                *(merged_preserve.get(key) or []),
                                *value,
                            ])
                        elif isinstance(value, bool):
                            merged_preserve[key] = bool(
                                merged_preserve.get(key, False) or value
                            )
                        elif value and not merged_preserve.get(key):
                            merged_preserve[key] = value
                edit_scope = brief.get("edit_scope") if isinstance(brief, dict) else {}
                if isinstance(edit_scope, dict):
                    for key, value in edit_scope.items():
                        if isinstance(value, list):
                            merged_edit_scope[key] = _dedupe_values([
                                *(merged_edit_scope.get(key) or []),
                                *value,
                            ])
                        elif value and not merged_edit_scope.get(key):
                            merged_edit_scope[key] = value
            template.order_id = canonical_id
            template.owner_scene = unit.owner_scene
            template.target_scenes = list(unit.target_scenes or [])
            template.source_violation_ids = source_violation_ids
            template.source_goal_ids = source_goal_ids
            template.violation_details = violation_details
            template.work_unit_id = unit.work_unit_id
            template.tool_commands = [
                command.model_dump() for command in unit.tool_batch.commands
            ]
            template.status = "pending"
            template.result_text_hash = ""
            template.repair_audit = {}
            template.candidate_attempt_budget = max(
                (order.candidate_attempt_budget or 1) for order in source_orders
            )
            if any(
                command.operation == "llm_creative_rewrite"
                for command in unit.tool_batch.commands
            ):
                # One creative unit produces one candidate.  Further attempts
                # belong to the explicit delta/repair-cycle budget, not a hidden
                # nested SceneRepairer loop.
                template.candidate_attempt_budget = 1
            executable_blueprint = dict(
                (template.repair_brief or {}).get("revision_blueprint") or {}
            )
            if unit.tool_batch.commands:
                executable_blueprint = {
                    **executable_blueprint,
                    "status": "ready",
                    "route": (
                        "creative_work_unit"
                        if any(
                            command.operation == "llm_creative_rewrite"
                            for command in unit.tool_batch.commands
                        )
                        else "tool_work_unit"
                    ),
                    "tool_blueprint": unit.tool_batch.commands[0].model_dump(),
                    "blueprint_source": unit.blueprint_source,
                    "canonical_work_unit_id": unit.work_unit_id,
                }
            template.reason = unit.issue_summary or "; ".join(
                order.reason for order in source_orders if order.reason
            )
            template.repair_brief = {
                **(template.repair_brief or {}),
                "repair_goals": scoped_repair_goals,
                "target_metrics": target_metrics,
                "preserve": merged_preserve,
                "edit_scope": merged_edit_scope,
                "source_finding_ids": source_violation_ids,
                "source_projection_order_ids": [order.order_id for order in source_orders],
                "canonical_work_unit_id": unit.work_unit_id,
                "placement": {
                    "status": unit.placement_status,
                    "read_context_spans": list(unit.read_context_spans or []),
                    "diagnostic_evidence_spans": list(
                        unit.diagnostic_evidence_spans or []
                    ),
                    "write_anchor": dict(unit.write_anchor or {}),
                },
                "acceptance_criteria": list(
                    unit.tool_batch.acceptance_criteria or []
                ),
                "revision_blueprint": executable_blueprint,
                "canonical_scope": "atomic_work_unit",
            }
            template.expected_after_repair = {
                **(template.expected_after_repair or {}),
                "covered_goal_ids": source_goal_ids,
                "acceptance_criteria": list(
                    unit.tool_batch.acceptance_criteria or []
                ),
            }
            canonical.append(template)
            unit.source_order_ids = [canonical_id]
            unit.tool_batch.source_order_ids = [canonical_id]
            unit_to_order_id[unit.work_unit_id] = canonical_id
            for source_order in source_orders:
                old_to_canonical.setdefault(source_order.order_id, []).append(canonical_id)

        for unit, order in zip(units, canonical):
            resolved_dependencies: list[str] = []
            for dependency in unit.dependencies or []:
                if dependency in unit_to_order_id:
                    candidates = [unit_to_order_id[dependency]]
                elif dependency in old_to_canonical:
                    candidates = old_to_canonical[dependency]
                else:
                    candidates = [dependency]
                resolved_dependencies.extend(
                    candidate
                    for candidate in candidates
                    if candidate and candidate != order.order_id
                )
            order.dependencies = list(dict.fromkeys(resolved_dependencies))

        uncovered = [
            order.model_copy(deep=True)
            for order in orders
            if order.order_id not in covered_projection_ids
        ]
        final_orders = [*canonical, *uncovered]
        return final_orders, {
            "input_projection_orders": len(orders),
            "executable_work_unit_count": len(units),
            "executable_order_count": len(canonical),
            "uncovered_order_count": len(uncovered),
            "finding_count": sum(len(order.violation_details or []) for order in final_orders),
            "avoided_executable_orders": max(0, len(orders) - len(canonical)),
            "invariant_satisfied": len(canonical) == len(units),
            "work_unit_to_order": unit_to_order_id,
        }

    @staticmethod
    def _sync_work_units_to_orders(orders, units) -> None:
        """Expose executable blueprint commands on their source orders.

        Work units are the execution source of truth, while orders are the
        control-plane/audit view consumed by workflow status and acceptance
        checks.  Keeping only the forward order-id mapping made a valid plan
        appear commandless outside the executor.
        """
        by_id = {order.order_id: order for order in orders}
        for order in orders:
            if order.repair_type != "manual_review":
                order.tool_commands = []
                order.work_unit_id = ""
        for unit in units:
            commands = [command.model_dump() for command in unit.tool_batch.commands]
            for order_id in unit.source_order_ids or []:
                order = by_id.get(order_id)
                if order is None or order.repair_type == "manual_review":
                    continue
                order.tool_commands.extend(commands)
                if not order.work_unit_id:
                    order.work_unit_id = unit.work_unit_id

    def _generate_orders(
        self,
        case: ChapterReviewCase,
        blocking: list[dict],
    ) -> list[ChapterRepairOrder]:
        """根据违规列表生成修复命令。

        归因规则：
        1. scene_i ending 与 scene_{i+1} opening 冲突 → owner_scene = i+1
        2. 后场景使用前场景未建立的事实 → owner_scene = 使用场景
        3. 前场景未展示大纲要求的事实 → owner_scene = 应展示场景
        4. 多场景依赖错误合同 → repair_type = contract_patch
        """
        orders: list[ChapterRepairOrder] = []
        order_index = 0

        # 处理 blocking 违规
        for v in blocking:
            order = self._violation_to_order(v, case, order_index, is_blocking=True)
            if order is not None:
                orders.append(order)
                order_index += 1

        # 按优先级排序：critical > high > medium > low
        priority_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        orders.sort(key=lambda o: priority_rank.get(o.priority, 99))

        return orders

    def _violation_to_order(
        self,
        violation: dict,
        case: ChapterReviewCase,
        order_index: int,
        *,
        is_blocking: bool,
    ) -> ChapterRepairOrder | None:
        """将单条违规转换为修复命令。"""
        v_type = violation.get("violation_type") or violation.get("type") or "unknown"
        severity = violation.get("severity", "high" if is_blocking else "medium")
        priority = _SEVERITY_TO_PRIORITY.get(severity, "medium")
        if not is_blocking and priority in ("critical", "high"):
            priority = "medium"

        # 确定 owner_scene
        owner_scene = self._determine_owner_scene(violation, case)

        # 确定 target_scenes。正文局部问题只修改归属场景，不因证据来源跨场景而扩大写入范围。
        source_scenes = violation.get("source_scenes") or [violation.get("source_scene")]
        target_scenes = sorted(set(s for s in source_scenes if isinstance(s, int)))
        type_values = _violation_type_values(violation)
        cross_scene_temporal = bool(
            type_values & {"timeline_conflict", "temporal_conflict"}
            and len(target_scenes) > 1
        )
        local_owner_required = bool(
            not cross_scene_temporal
            and (
                type_values & (_LOCAL_TEXT_REPAIR_TYPES | _SCENE_OUTCOME_REPAIR_TYPES)
                or _is_fact_conflict(violation)
                or violation.get("obligation_ownership_explicit") is True
            )
        )
        if local_owner_required and owner_scene is not None:
            target_scenes = [owner_scene]
        elif not target_scenes and owner_scene is not None:
            target_scenes = [owner_scene]

        # 确定 repair_type
        repair_type = self._determine_repair_type(violation, target_scenes, case)

        # 生成确定性 order_id
        order_id = _generate_order_id(case.case_id, order_index, v_type)

        # 构建修复指令
        instruction = self._build_instruction(violation, repair_type, owner_scene)

        # 构建预期修复后状态
        expected_after_repair = self._build_expected_after_repair(violation)

        # 违规 ID
        # Review Minister and the blueprint agent use ``issue_id`` as the
        # canonical identity.  Keep the order side on the same identity; using
        # ``violation_id`` first made otherwise valid work units impossible to
        # map back to their orders when both aliases were present.
        violation_id = (
            violation.get("issue_id")
            or violation.get("violation_id")
            or violation.get("id")
            or ""
        )
        source_violation_ids = [violation_id] if violation_id else []
        repair_domain = self._determine_repair_domain(violation, repair_type)
        read_scope = self._read_scope_for_order(repair_type, target_scenes, owner_scene)
        write_scope = self._write_scope_for_order(repair_type, target_scenes, owner_scene)
        lane_info = self._select_repair_lane(
            violation,
            repair_type=repair_type,
            repair_domain=repair_domain,
        )
        repair_intent = violation.get("repair_intent") if isinstance(violation.get("repair_intent"), dict) else {}
        if repair_intent:
            lane_info = self._merge_lane_info(lane_info, repair_intent)
        repair_brief = self._build_repair_brief(
            violation=violation,
            order_id=order_id,
            repair_type=repair_type,
            repair_domain=repair_domain,
            repair_lane=lane_info["repair_lane"],
            repair_strength=lane_info["repair_strength"],
            target_scenes=target_scenes,
            owner_scene=owner_scene,
            read_scope=read_scope,
            write_scope=write_scope,
            instruction=instruction,
            expected_after_repair=expected_after_repair,
            allowed_max_strength=lane_info["allowed_max_strength"],
            candidate_attempt_budget=lane_info["candidate_attempt_budget"],
        )
        repair_brief = self._merge_review_minister_brief(repair_brief, violation)

        return ChapterRepairOrder(
            order_id=order_id,
            target_scenes=target_scenes,
            owner_scene=owner_scene,
            repair_type=repair_type,
            repair_domain=repair_domain,
            repair_lane=lane_info["repair_lane"],
            repair_strength=lane_info["repair_strength"],
            allowed_max_strength=lane_info["allowed_max_strength"],
            candidate_attempt_budget=lane_info["candidate_attempt_budget"],
            repair_brief=repair_brief,
            read_scope=read_scope,
            write_scope=write_scope,
            priority=priority,
            reason=violation.get("detail") or violation.get("reason") or v_type,
            instruction=instruction,
            source_violation_ids=source_violation_ids,
            violation_details=[dict(violation)],
            expected_after_repair=expected_after_repair,
            status="pending",
        )

    # ------------------------------------------------------------------
    # 归因规则
    # ------------------------------------------------------------------

    @staticmethod
    def _determine_owner_scene(
        violation: dict,
        case: ChapterReviewCase,
    ) -> int | None:
        """确定违规的归属场景。

        归因规则：
        1. cross_scene_state_mismatch / cross_scene_state_gap → owner = 后一场景 (i+1)
        2. unestablished_fact_reference → owner = 使用场景
        3. 前场景未展示大纲要求事实 → owner = 应展示场景
        4. 其他 → owner = source_scene
        """
        v_type = violation.get("violation_type") or violation.get("type") or ""

        # A boundary conflict can be discovered in the later scene while the
        # repair belongs to the earlier scene that overshot its contract.  The
        # reviewer supplies ``affected_scene`` after comparing the later scene
        # contract with the prior text; keep that explicit ownership intact.
        if v_type in ("timeline_conflict", "temporal_conflict"):
            affected = violation.get("affected_scene")
            if isinstance(affected, int):
                return affected

        # 显式义务归属优先于发现问题的场景；章节级义务不能擅自归给单个场景。
        if violation.get("obligation_ownership_explicit") is True:
            if str(violation.get("obligation_scope") or "") == "chapter":
                return None
            obligation_owner = violation.get("obligation_owner_scene")
            if isinstance(obligation_owner, int):
                return obligation_owner

        # 跨场景状态冲突 → owner = 后一场景
        if v_type in ("cross_scene_state_mismatch", "cross_scene_state_gap"):
            affected = violation.get("affected_scene")
            if isinstance(affected, int):
                return affected
            # 回退：取 source_scenes 中较大的 index
            source_scenes = violation.get("source_scenes") or []
            if len(source_scenes) >= 2:
                return max(source_scenes)
            return source_scenes[-1] if source_scenes else None

        # 未建立事实引用 → owner = 使用场景
        if v_type == "unestablished_fact_reference":
            source_scene = violation.get("source_scene")
            if isinstance(source_scene, int):
                return source_scene

        # 大纲事实缺失 → owner = 应展示场景
        if v_type in ("missing_outline_fact", "outline_fact_not_shown"):
            source_scene = violation.get("source_scene")
            if isinstance(source_scene, int):
                return source_scene

        # 默认：取 source_scene
        source_scene = violation.get("source_scene")
        if isinstance(source_scene, int):
            return source_scene

        # 回退：取 source_scenes 第一个
        source_scenes = violation.get("source_scenes") or []
        return source_scenes[0] if source_scenes else None

    @staticmethod
    def _determine_repair_type(
        violation: dict,
        target_scenes: list[int],
        case: ChapterReviewCase,
    ) -> Literal[
        "local_patch", "scene_rewrite", "cross_scene_alignment",
        "contract_patch", "contract_completion_patch", "manual_review",
    ]:
        """确定修复类型。

        规则：
        1. 跨场景冲突 → cross_scene_alignment
        2. 合同/大纲本体问题 → contract_patch
        3. validator/system 问题 → manual_review
        4. repairability = human_review_required → manual_review
        5. 正文可修问题 → local_patch / scene_rewrite / cross_scene_alignment
        6. 其余 → scene_rewrite
        """
        v_type = violation.get("violation_type") or violation.get("type") or ""
        repairability = violation.get("repairability", "")
        repair_scope = str(violation.get("repair_scope") or violation.get("scope") or "")
        repairable_by_text = violation.get("repairable_by_text")
        repairable_by_contract = violation.get("repairable_by_contract")
        suggested_strategy = str(violation.get("suggested_strategy") or "")

        # ``high_advisories`` / ``medium_advisories`` are normally aggregate
        # signals and therefore remain a manual-review boundary.  Final
        # acceptance can, however, promote one concrete advisory into an exact
        # localized finding while retaining the aggregate finding's original
        # ``human_review_required`` / ``manual_only`` metadata.  Treat that
        # concrete finding as prose repair only when its span is both declared
        # localized and present in the frozen scene text.  This preserves the
        # safety boundary for unlocalized advisory bundles while allowing an
        # executable candidate route for evidence we can actually patch.
        metric = str(
            violation.get("metric")
            or violation.get("violation_type")
            or violation.get("type")
            or ""
        ).lower()
        evidence = violation.get("evidence") if isinstance(violation.get("evidence"), dict) else {}
        # The nested evidence record is the upstream provenance.  A later
        # locator may add top-level ``localization_status=localized`` to an
        # aggregate issue by selecting a low-confidence whole-scene window;
        # that must not silently turn a manual aggregate into an auto repair.
        evidence_localization_status = str(
            evidence.get("localization_status") or ""
        ).lower()
        declared_localization_status = str(
            violation.get("localization_status") or ""
        ).lower()
        localized_spans: list[str] = []
        explicit_evidence_spans: list[str] = []
        for value in (
            violation.get("target_span"),
            violation.get("evidence_span"),
            violation.get("span"),
        ):
            text = str(value or "").strip()
            if text:
                localized_spans.append(text)
        raw_spans = violation.get("evidence_spans") or evidence.get("evidence_spans") or []
        if isinstance(raw_spans, list):
            for item in raw_spans:
                if isinstance(item, dict):
                    text = str(item.get("text") or item.get("span") or "").strip()
                    role = str(item.get("role") or "").strip().lower()
                else:
                    text = str(item or "").strip()
                    role = ""
                if text:
                    localized_spans.append(text)
                    # ReviewIssueLocalizer marks synthesized fallback windows as
                    # ``generated_window``.  A bounded evidence sentence is
                    # upstream, actionable evidence even when the aggregate's
                    # nested evidence payload predates localization metadata.
                    if role != "generated_window":
                        explicit_evidence_spans.append(text)
        candidate_texts = [
            str(packet.candidate_text or "")
            for packet in case.scene_packets
            if str(packet.candidate_text or "")
        ]
        exact_bounded_evidence = any(
            0 < len(span) <= 500
            and any(span in candidate_text for candidate_text in candidate_texts)
            for span in explicit_evidence_spans
        )
        localized_advisory_is_patchable = bool(
            metric in {"high_advisories", "medium_advisories"}
            and (
                evidence_localization_status == "localized"
                or (
                    declared_localization_status == "localized"
                    and exact_bounded_evidence
                )
            )
            and localized_spans
            and any(
                span in candidate_text
                for span in localized_spans
                for candidate_text in candidate_texts
            )
        )

        if repair_scope == "system" or v_type.endswith("_unavailable"):
            return "manual_review"

        # Explicit human-review routing is a safety boundary and must win
        # before local text-repair type heuristics, except for a localized
        # advisory finding whose exact source span is verified above.
        if (
            repairability == "human_review_required"
            or suggested_strategy == "manual_review"
        ) and not localized_advisory_is_patchable:
            return "manual_review"

        if localized_advisory_is_patchable:
            return "local_patch"

        if (
            violation.get("obligation_ownership_explicit") is True
            and str(violation.get("obligation_scope") or "") == "chapter"
        ):
            return "manual_review"

        if (
            repair_scope in {"scene_contract", "chapter_contract"}
            or repairable_by_contract is True
            or _is_contract_related(violation, case)
        ) and repairable_by_text is not True:
            return "contract_patch"

        type_values = _violation_type_values(violation)

        if type_values & _SCENE_OUTCOME_REPAIR_TYPES:
            # 通用修复 S-3：当 target_span / evidence_span 为"整篇正文"等全文级范围时，
            # contract_completion_patch（插入锚点后）无法修复——整篇正文都缺少必须展现
            # 的内容。需要 scene_rewrite 重建整个场景。
            # 典型场景：L9 fact_conflict 修复破坏了 L2 已修复的 must_show 锚点，
            # L10 重新检测到 missing_must_show 但 target_span="整篇正文"，
            # 此时插入式补全无效，必须 scene_rewrite。
            full_text_span = str(
                violation.get("target_span")
                or violation.get("evidence_span")
                or ""
            ).strip()
            if full_text_span in {"整篇正文", "整篇", "全文", "全篇正文"}:
                return "scene_rewrite"
            if len(target_scenes) <= 1 and _is_text_contract_completion_gap(violation):
                return "contract_completion_patch"
            return "scene_rewrite"

        # A timeline finding carrying both sides of a scene boundary must see
        # both texts.  It still writes only the explicit owner scene.
        if (
            type_values & {"timeline_conflict", "temporal_conflict"}
            and len(target_scenes) > 1
        ):
            return "cross_scene_alignment"

        # 时间标记、文风和可定位事实问题优先局部修补，不能被上游 rewrite_scene 建议放大。
        if type_values & {"timeline_conflict", "temporal_conflict"}:
            return "local_patch"
        if _is_fact_conflict(violation):
            # 失败记忆：fact_conflict 已经 hard_failed 过，local_patch 不足以修复，
            # 升级到 scene_rewrite 让 LLM 重写整个场景以确保合同事实被正确实现。
            if violation.get("repair_failure") is True and violation.get("repair_failure_disposition") == "hard_failed":
                return "scene_rewrite"
            if suggested_strategy != "rewrite_scene" or _has_deterministic_text_correction(violation):
                return "local_patch"
        if type_values & _LOCAL_TEXT_REPAIR_TYPES and suggested_strategy != "rewrite_scene":
            return "local_patch"

        # 只有真正的跨场景状态问题或多场景写入才走跨场景对齐。
        if v_type.startswith("cross_scene_") or len(target_scenes) > 1:
            return "cross_scene_alignment"

        # 单场景正文修复
        if (
            repairability in {"auto_fixable", "contract_repair_required", "cross_scene_fixable"}
            or repairable_by_text is True
            or repair_scope == "prose_text"
        ) and len(target_scenes) <= 1:
            if suggested_strategy == "rewrite_scene":
                return "scene_rewrite"
            return "local_patch"

        # 默认
        return "scene_rewrite"

    @staticmethod
    def _determine_repair_domain(violation: dict, repair_type: str) -> str:
        explicit = str(violation.get("repair_domain") or "").strip()
        if explicit:
            return explicit
        type_values = _violation_type_values(violation)
        if type_values & {
            *_PUNCTUATION_REPAIR_TYPES,
            *_PROSE_LOCAL_REPAIR_TYPES,
            *_PARAGRAPH_RECONSTRUCTION_TYPES,
        }:
            return "anti_ai"
        if type_values & {
            "pov_consistency",
            "single_pov_per_scene",
            "head_hopping",
            "head_hopping_count",
            "voice_fingerprint",
        }:
            return "pov"
        if type_values & _PACING_REPAIR_TYPES:
            return "rhythm"
        if repair_type == "contract_patch":
            return "contract"
        if repair_type == "cross_scene_alignment":
            return "chronology"
        if _is_fact_conflict(violation):
            return "fact"
        if repair_type == "scene_rewrite":
            return "structure"
        return "surface"

    @staticmethod
    def _read_scope_for_order(
        repair_type: str,
        target_scenes: list[int],
        owner_scene: int | None,
    ) -> list[str]:
        scopes = ["review_case_file"]
        if repair_type == "cross_scene_alignment":
            scopes.append("chapter_text")
        scenes = sorted(set(target_scenes or ([] if owner_scene is None else [owner_scene])))
        scopes.extend(f"scene:{scene}" for scene in scenes)
        return scopes

    @staticmethod
    def _write_scope_for_order(
        repair_type: str,
        target_scenes: list[int],
        owner_scene: int | None,
    ) -> list[str]:
        if repair_type in {"contract_patch", "manual_review"}:
            return []
        if repair_type == "cross_scene_alignment" and owner_scene is not None:
            return [f"scene:{owner_scene}"]
        scenes = sorted(set(target_scenes or ([] if owner_scene is None else [owner_scene])))
        return [f"scene:{scene}" for scene in scenes]

    @staticmethod
    def _execution_layers_for_orders(orders: list[ChapterRepairOrder]) -> list[list[str]]:
        layers: list[list[str]] = []
        occupied_by_layer: list[set[str]] = []
        for order in orders:
            write_scope = set(order.write_scope or [f"scene:{scene}" for scene in order.target_scenes])
            placed = False
            for index, occupied in enumerate(occupied_by_layer):
                if write_scope and write_scope & occupied:
                    continue
                layers[index].append(order.order_id)
                occupied.update(write_scope)
                placed = True
                break
            if not placed:
                layers.append([order.order_id])
                occupied_by_layer.append(set(write_scope))
        return layers

    @staticmethod
    def _execution_scope_trace(orders: list[ChapterRepairOrder]) -> list[dict]:
        return [
            {
                "order_id": order.order_id,
                "repair_type": order.repair_type,
                "repair_domain": order.repair_domain,
                "repair_lane": order.repair_lane,
                "repair_strength": order.repair_strength,
                "allowed_max_strength": order.allowed_max_strength,
                "read_scope": list(order.read_scope),
                "write_scope": list(order.write_scope),
                "dependencies": list(order.dependencies),
            }
            for order in orders
        ]

    @staticmethod
    def _repair_strategy_summary(orders: list[ChapterRepairOrder]) -> dict:
        def counts(values: list[str]) -> dict[str, int]:
            result: dict[str, int] = {}
            for value in values:
                key = value or "unknown"
                result[key] = result.get(key, 0) + 1
            return result

        lanes = [order.repair_lane for order in orders]
        strengths = [order.repair_strength for order in orders]
        domains = [order.repair_domain for order in orders]
        return {
            "domain_counts": counts(domains),
            "lane_counts": counts(lanes),
            "strength_counts": counts(strengths),
            "deterministic_first": sum(
                1 for order in orders
                if order.repair_lane == "deterministic_surface_cleanup"
            ),
            "llm_required": sum(
                1 for order in orders
                if order.repair_lane not in {"deterministic_surface_cleanup", "manual_review"}
            ),
        }

    # ------------------------------------------------------------------
    # 修复指令与预期状态
    # ------------------------------------------------------------------

    @staticmethod
    def _build_instruction(
        violation: dict,
        repair_type: str,
        owner_scene: int | None,
    ) -> str:
        """构建修复指令文本。"""
        v_type = violation.get("violation_type") or violation.get("type") or ""
        detail = violation.get("detail") or violation.get("reason") or ""
        repair_goal = violation.get("repair_goal") or ""

        parts: list[str] = []

        if repair_type == "cross_scene_alignment":
            parts.append(f"[跨场景对齐] 场景 {owner_scene}")
        elif repair_type == "contract_patch":
            parts.append("[合同修正]")
        elif repair_type == "contract_completion_patch":
            parts.append(f"[合同义务正文补全] 场景 {owner_scene}")
        elif repair_type == "manual_review":
            parts.append("[需人工审核]")
        elif repair_type == "scene_rewrite":
            parts.append(f"[场景重写] 场景 {owner_scene}")
        else:
            parts.append(f"[局部修补] 场景 {owner_scene}")

        if detail:
            parts.append(detail)
        if repair_goal:
            parts.append(f"修复目标: {repair_goal}")

        return " | ".join(parts)

    @staticmethod
    def _select_repair_lane(
        violation: dict,
        *,
        repair_type: str,
        repair_domain: str,
    ) -> dict:
        type_values = _violation_type_values(violation)
        lane = "fbi_prose"
        strength = "S2"
        max_strength = "S3"
        # 通用修复 S-6：fbi_prose 默认 attempts 从 1 提升到 3
        # 根因：密度类问题（明喻/破折号/情绪标签等）需要 LLM 系统性改动多处，
        #   1 次尝试通常不够。配合 S-1/S-2 让 LLM 一次性处理所有位置，
        #   但仍需足够重试预算应对自检失败。
        attempts = 3

        # 失败记忆：如果违规已经 hard_failed 过，升级修复策略
        # 根因：intake 层此前无失败记忆，每次 recheck 都用同样 lane/strength 重试，
        #   导致 fact_conflict 等内容问题在 local_patch 反复失败却永不升级到 rewrite。
        _previously_hard_failed = (
            violation.get("repair_failure") is True
            and violation.get("repair_failure_disposition") == "hard_failed"
        )

        if type_values & _PUNCTUATION_REPAIR_TYPES:
            lane, strength, max_strength, attempts = "deterministic_surface_cleanup", "S1", "S2", 2
        elif type_values & _PROSE_LOCAL_REPAIR_TYPES:
            lane, strength, max_strength, attempts = "prose_local_patch", "S2", "S3", 2
            # 失败记忆：prose 类违规 hard_failed 后升级到场景重构
            # 根因：此前升级到 paragraph_reconstruction（段落结构操作），但 prose 类问题
            #   （如 abstract_bare_count 抽象描述缺证据）是内容质量问题，不是段落结构问题。
            #   paragraph_reconstruction 的 split_paragraph/vary_sentence_shape 操作
            #   无法有效补充具体证据或替换抽象描述。升级到 scene_restructure
            #   （有 rewrite_paragraph/limited_scene_rewrite 操作）让 LLM 重写相关段落。
            # 通用性：所有题材的 prose 类问题在 local_patch 失败后都需要场景级重写。
            if _previously_hard_failed:
                lane, strength, max_strength, attempts = "scene_restructure", "S4", "S5", 2
        elif type_values & _TENSE_REPAIR_TYPES:
            # 通用修复（循环 #6 TA-2）：时态/时间锚点类违规走 tense_repair lane
            lane, strength, max_strength, attempts = "tense_repair", "S2", "S3", 2
        elif type_values & _PARAGRAPH_RECONSTRUCTION_TYPES:
            lane, strength, max_strength, attempts = "paragraph_reconstruction", "S3", "S4", 2
        elif type_values & _PACING_REPAIR_TYPES:
            lane, strength, max_strength, attempts = "pacing_repair", "S3", "S4", 2
        elif type_values & {"voice_fingerprint"}:
            lane, strength, max_strength, attempts = "voice_reconstruction", "S4", "S4", 2
        elif type_values & _POV_BOUNDARY_TYPES:
            lane, strength, max_strength, attempts = "voice_repair", "S3", "S4", 2
        elif type_values & _SCENE_OUTCOME_REPAIR_TYPES:
            lane, strength, max_strength, attempts = "contract_completion", "S2", "S3", 2
            # 失败记忆：contract_completion hard_failed 后升级到场景重构
            # 根因：contract_completion lane（insert_missing_beat/append_ending_beat）失败后，
            #   此前升级到 paragraph_reconstruction（split_paragraph/vary_sentence_shape），
            #   但 missing_must_show / ending_state_not_reached 等问题是"缺少故事节拍"，
            #   paragraph_reconstruction 的段落拆分/句式变化操作无法补入缺失内容。
            #   升级到 scene_restructure（有 rewrite_paragraph/limited_scene_rewrite 操作）
            #   让 LLM 重写相关场景段落以补入缺失的合同节拍。
            # 通用性：所有 contract_completion 失败的场景结构问题都需要场景级重写而非段落拆分。
            if _previously_hard_failed:
                lane, strength, max_strength, attempts = "scene_restructure", "S4", "S5", 2
        elif _is_fact_conflict(violation):
            fact_goal = _compile_fact_repair_goal(violation)
            required_entities = fact_goal.get("required_entities") or []
            conflict_type = str(fact_goal.get("conflict_type") or "")
            if conflict_type in {"missing_required_components", "missing_context_bridge"}:
                lane, strength, max_strength, attempts = "fact_bridge_patch", "S3", "S4", 3
            else:
                lane, strength, max_strength, attempts = "fact_local_patch", "S2", "S3", 2
            # 失败记忆：fact_conflict hard_failed 后升级
            # fact_local_patch → fact_bridge_patch S3（更宽的上下文修补）
            # fact_bridge_patch → scene_restructure S4（整场景重构）
            if _previously_hard_failed:
                if lane == "fact_local_patch":
                    lane, strength, max_strength, attempts = "fact_bridge_patch", "S3", "S4", 3
                elif lane == "fact_bridge_patch":
                    lane, strength, max_strength, attempts = "scene_restructure", "S4", "S5", 2
        elif repair_type == "scene_rewrite" or repair_domain == "structure":
            lane, strength, max_strength, attempts = "scene_restructure", "S4", "S5", 1
        elif repair_type == "manual_review":
            lane, strength, max_strength, attempts = "manual_review", "S0", "S0", 0

        return {
            "repair_lane": lane,
            "repair_strength": strength,
            "allowed_max_strength": max_strength,
            "candidate_attempt_budget": attempts,
        }

    @staticmethod
    def _merge_lane_info(default_lane: dict, repair_intent: dict) -> dict:
        merged = dict(default_lane)
        for source_key, target_key in [
            ("repair_lane", "repair_lane"),
            ("repair_strength", "repair_strength"),
            ("allowed_max_strength", "allowed_max_strength"),
        ]:
            value = repair_intent.get(source_key)
            if value:
                merged[target_key] = str(value)
        budget = repair_intent.get("candidate_attempt_budget") or repair_intent.get("max_candidate_attempts")
        if isinstance(budget, int) and budget >= 0:
            merged["candidate_attempt_budget"] = budget
        elif repair_intent.get("repair_lane") == "manual_review":
            merged["candidate_attempt_budget"] = 0
        elif "repair_lane" in repair_intent and "candidate_attempt_budget" not in repair_intent:
            # 通用修复：review_minister 的 repair_intent 覆盖了 repair_lane 但未携带
            # candidate_attempt_budget（FBIRepairIntent 模型没有此字段）。
            # 此时 _select_repair_lane 的默认 attempts=1（未匹配类型集合时）会错误地保留，
            # 导致 prose_local_patch/pacing_repair 等 lane 的 LLM 修复只有 1 次尝试。
            # 根据 lane 同步设置默认 budget，与 _select_repair_lane 保持一致。
            lane_budget = _LANE_DEFAULT_BUDGET.get(str(repair_intent["repair_lane"]))
            if lane_budget is not None:
                merged["candidate_attempt_budget"] = lane_budget
        return merged

    @staticmethod
    def _merge_review_minister_brief(base: dict, violation: dict) -> dict:
        diagnosis = violation.get("fbi_diagnosis") if isinstance(violation.get("fbi_diagnosis"), dict) else {}
        repair_intent = violation.get("repair_intent") if isinstance(violation.get("repair_intent"), dict) else {}
        if not diagnosis and not repair_intent:
            return base

        merged = dict(base)
        if diagnosis.get("diagnosis_id"):
            merged["diagnosis_id"] = diagnosis["diagnosis_id"]
        revision_blueprint = diagnosis.get("revision_blueprint") if isinstance(diagnosis.get("revision_blueprint"), dict) else {}
        if revision_blueprint:
            merged["revision_blueprint"] = dict(revision_blueprint)
            tool_blueprint = revision_blueprint.get("tool_blueprint")
            if isinstance(tool_blueprint, dict):
                merged["tool_blueprint"] = dict(tool_blueprint)
        if repair_intent:
            merged["repair_intent"] = dict(repair_intent)
            if repair_intent.get("target_behavior"):
                merged["target_behavior"] = str(repair_intent["target_behavior"])
            patch_plan = repair_intent.get("patch_plan")
            if isinstance(patch_plan, list):
                merged["patch_plan"] = [dict(item) for item in patch_plan if isinstance(item, dict)]
            metrics = repair_intent.get("target_metrics")
            if isinstance(metrics, list) and metrics:
                merged["target_metrics"] = [dict(item) for item in metrics if isinstance(item, dict)]
            operation = repair_intent.get("preferred_operation")
            if operation:
                edit_scope = dict(merged.get("edit_scope") or {})
                allowed = list(edit_scope.get("allowed_operations") or [])
                if operation not in allowed:
                    allowed.insert(0, operation)
                edit_scope["allowed_operations"] = allowed
                merged["edit_scope"] = edit_scope

        minister = {
            "issue_family": diagnosis.get("issue_family", ""),
            "problem_statement": diagnosis.get("problem_statement", ""),
            "authorities": diagnosis.get("authorities") or [],
            "conflict_decisions": diagnosis.get("conflict_decisions") or [],
            "acceptance_criteria": diagnosis.get("acceptance_criteria") or [],
            "source_trace": diagnosis.get("source_trace") or [],
        }
        merged["review_minister"] = minister

        boundary = diagnosis.get("protection_boundary") if isinstance(diagnosis.get("protection_boundary"), dict) else {}
        if boundary:
            preserve = dict(merged.get("preserve") or {})
            hard_facts = list(preserve.get("hard_facts") or [])
            hard_facts.extend(str(item) for item in boundary.get("preserve_hard_facts") or [] if item)
            protected_spans = list(preserve.get("protected_spans") or [])
            protected_spans.extend(str(item) for item in boundary.get("preserve_protected_spans") or [] if item)
            preserve["hard_facts"] = _dedupe_strings(hard_facts)
            preserve["protected_spans"] = _dedupe_strings(protected_spans)
            if boundary.get("preserve_pov") and not preserve.get("pov"):
                preserve["pov"] = str(boundary["preserve_pov"])
            preserve["scene_markers"] = bool(boundary.get("preserve_scene_markers", preserve.get("scene_markers", True)))
            merged["preserve"] = preserve

            edit_scope = dict(merged.get("edit_scope") or {})
            forbidden = list(edit_scope.get("forbidden_operations") or [])
            forbidden.extend(str(item) for item in boundary.get("forbidden_operations") or [] if item)
            edit_scope["forbidden_operations"] = _dedupe_strings(forbidden)
            merged["edit_scope"] = edit_scope

        criteria = diagnosis.get("acceptance_criteria")
        if isinstance(criteria, list) and criteria:
            self_check = dict(merged.get("self_check") or {})
            self_check["acceptance_criteria"] = [dict(item) for item in criteria if isinstance(item, dict)]
            merged["self_check"] = self_check
        return merged

    @staticmethod
    def _build_repair_brief(
        *,
        violation: dict,
        order_id: str,
        repair_type: str,
        repair_domain: str,
        repair_lane: str,
        repair_strength: str,
        target_scenes: list[int],
        owner_scene: int | None,
        read_scope: list[str],
        write_scope: list[str],
        instruction: str,
        expected_after_repair: dict,
        allowed_max_strength: str,
        candidate_attempt_budget: int,
    ) -> dict:
        metric = str(
            violation.get("metric")
            or violation.get("type")
            or violation.get("violation_type")
            or "unknown"
        )
        # 通用修复 S-5：从 violation evidence 读取 validator_metrics，
        # 优先使用 advisory 携带的可测量指标名作为 target_metric.metric。
        # 根因：enforced_ai_simile_overuse 不是 validator_protocol 注册的 metric 名，
        #   弱后检找不到 metric 返回 unsupported_metric，只看 before != after。
        #   但 advisory 携带了 validator_metrics=["simile_hits","simile_word_count"]，
        #   这些是注册过的 metric，弱后检能正确路由到重算路径。
        # 通用性：任何携带 validator_metrics 的 violation 都走此路径，不限于 ai_simile_overuse。
        evidence = violation.get("evidence")
        all_validator_metrics: list[str] = []
        if isinstance(evidence, dict):
            vm = evidence.get("validator_metrics")
            if isinstance(vm, list) and vm:
                all_validator_metrics = [str(m) for m in vm if m]
                if all_validator_metrics:
                    metric = all_validator_metrics[0]
        target_metric = {
            "validator": violation.get("validator") or violation.get("source_validator") or "",
            "metric": metric,
            "actual": violation.get("actual", violation.get("value", "")),
            "expected": violation.get("expected", violation.get("limit", "")),
            "expected_max": violation.get("expected_max", violation.get("max", "")),
            "direction": _metric_direction(metric),
        }
        # 通用修复 S-5：将所有 validator_metrics 都加入 target_metrics，
        # 弱后检 _validator_metric_self_check 会遍历 metric_names，
        # 任一指标通过即判 passed。
        target_metrics_list = [target_metric]
        for vm in all_validator_metrics[1:]:
            target_metrics_list.append({
                "validator": target_metric["validator"],
                "metric": vm,
                "actual": "",
                "expected": "",
                "expected_max": "",
                "direction": _metric_direction(vm),
            })
        target_behavior = (
            violation.get("expected_behavior")
            or violation.get("repair_goal")
            or expected_after_repair.get("target_behavior")
            or expected_after_repair.get("violation_resolved")
            or ""
        )
        preserve_terms: list = []
        for key in ("preserve", "must_preserve", "protected_terms"):
            value = violation.get(key)
            if isinstance(value, list):
                preserve_terms.extend(value)
            elif value:
                preserve_terms.append(value)
        expected_preserve = expected_after_repair.get("preserve")
        if isinstance(expected_preserve, list):
            preserve_terms.extend(expected_preserve)
        elif expected_preserve:
            preserve_terms.append(expected_preserve)
        allowed_operations = _allowed_operations_for_lane(repair_lane)
        brief = {
            "order_id": order_id,
            "repair_domain": repair_domain,
            "repair_type": repair_type,
            "repair_lane": repair_lane,
            "repair_strength": repair_strength,
            "allowed_max_strength": allowed_max_strength,
            "target_metrics": target_metrics_list,
            "target_behavior": str(target_behavior or ""),
            "failure_signature": _repair_failure_signature(violation, owner_scene),
            "edit_scope": {
                "read_scope": list(read_scope),
                "write_scope": list(write_scope),
                "target_scenes": list(target_scenes),
                "allowed_operations": allowed_operations,
                "forbidden_operations": [
                    "change_fact",
                    "change_pov",
                    "remove_scene_marker",
                    "remove_protected_obligation",
                ],
            },
            "preserve": {
                "scene_markers": True,
                "hard_facts": [],
                "protected_spans": [item for item in preserve_terms if item],
                "ending_state": str(violation.get("ending_state") or ""),
                "pov": str(violation.get("pov") or violation.get("pov_character") or ""),
            },
            "repair_strategy": {
                "preferred_lane": repair_lane,
                "fallback_lane": _fallback_lane_for(repair_lane),
                "max_candidate_attempts": candidate_attempt_budget,
                "allow_scene_rewrite": repair_type == "scene_rewrite",
            },
            "self_check": {
                "required": True,
                "target_metric_must_improve": True,
                "protection_must_pass": True,
                "no_new_hard_failure": True,
            },
            "instruction": instruction,
        }
        fact_goal = _compile_fact_repair_goal(violation)
        if fact_goal:
            brief["fact_repair_goal"] = fact_goal
            brief["target_behavior"] = str(
                fact_goal.get("suggested_correction")
                or fact_goal.get("required_relation")
                or target_behavior
                or ""
            )
        return brief

    @staticmethod
    def _build_expected_after_repair(violation: dict) -> dict:
        """构建修复后的预期状态。"""
        expected = violation.get("expected_after_repair") or violation.get("expected_state") or {}
        if isinstance(expected, dict):
            return expected

        # 从违规信息推断预期状态
        v_type = violation.get("violation_type") or violation.get("type") or ""
        result: dict = {"violation_resolved": v_type}

        # 状态冲突：预期后场景开始状态与前场景结束状态一致
        if v_type in ("cross_scene_state_mismatch", "cross_scene_state_gap"):
            target_span = violation.get("target_span", "")
            if target_span.startswith("state:"):
                state_key = target_span[len("state:"):]
                result["state_aligned"] = state_key

        return result

    # ------------------------------------------------------------------
    # 计划状态判定
    # ------------------------------------------------------------------

    @staticmethod
    def _determine_plan_status(
        case: ChapterReviewCase,
        orders: list[ChapterRepairOrder],
    ) -> Literal[
        "clean", "needs_repair", "needs_contract_repair",
        "needs_human_review", "failed",
    ]:
        """确定修复计划的整体状态。

        规则：
        - "clean": 无违规
        - "needs_repair": 仅场景级修复
        - "needs_contract_repair": 涉及合同级修复
        - "needs_human_review": 存在不可自动解决
        """
        if not orders:
            return "clean"

        hard_orders = []
        for order in orders:
            details = [item for item in order.violation_details or [] if isinstance(item, dict)]
            if not details:
                hard_orders.append(order)
                continue
            if any(
                str(item.get("enforcement") or "") == "hard_blocking"
                or get_enforcement(
                    canonical_metric_name(
                        item.get("metric")
                        or item.get("violation_type")
                        or item.get("type")
                        or ""
                    )
                ).get("enforcement") == "hard_blocking"
                for item in details
            ):
                hard_orders.append(order)
        hard_repair_types = {order.repair_type for order in hard_orders}

        # Only a hard manual-review order may stop the workflow.  Advisory
        # manual orders simply consume the current best-effort attempt.
        if "manual_review" in hard_repair_types:
            return "needs_human_review"

        if "contract_patch" in hard_repair_types:
            return "needs_contract_repair"

        # 其他 → needs_repair
        return "needs_repair"

    # ------------------------------------------------------------------
    # 全局备注
    # ------------------------------------------------------------------

    @staticmethod
    def _build_global_notes(
        case: ChapterReviewCase,
        orders: list[ChapterRepairOrder],
        total_violations: int,
        blocking_count: int,
    ) -> list[str]:
        """构建全局备注列表。"""
        notes: list[str] = []

        notes.append(
            f"全章违规: {total_violations} (阻塞 {blocking_count})"
        )

        # 按修复类型统计
        type_counts: dict[str, int] = {}
        for o in orders:
            type_counts[o.repair_type] = type_counts.get(o.repair_type, 0) + 1
        if type_counts:
            type_summary = ", ".join(f"{t}={c}" for t, c in sorted(type_counts.items()))
            notes.append(f"修复类型分布: {type_summary}")

        # 按优先级统计
        prio_counts: dict[str, int] = {}
        for o in orders:
            prio_counts[o.priority] = prio_counts.get(o.priority, 0) + 1
        if prio_counts:
            prio_summary = ", ".join(f"{p}={c}" for p, c in sorted(prio_counts.items()))
            notes.append(f"优先级分布: {prio_summary}")

        # 轮次提醒
        if case.review_round >= MAX_CHAPTER_REPAIR_ROUNDS:
            notes.append(
                f"已达到最大章节修复轮次 ({MAX_CHAPTER_REPAIR_ROUNDS})，建议人工审核"
            )

        # 跨场景冲突提醒
        cross_scene_orders = [o for o in orders if o.repair_type == "cross_scene_alignment"]
        if cross_scene_orders:
            affected = set()
            for o in cross_scene_orders:
                affected.update(o.target_scenes)
            notes.append(
                f"跨场景冲突影响场景: {sorted(affected)}"
            )

        return notes


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _generate_order_id(case_id: str, index: int, v_type: str) -> str:
    """生成确定性 order_id。

    使用 case_id + index + v_type 的哈希确保确定性。
    """
    raw = f"{case_id}:{index}:{v_type}"
    short_hash = hashlib.md5(raw.encode("utf-8")).hexdigest()[:8]
    return f"cho_{short_hash}_{index:03d}"


def _is_contract_related(violation: dict, case: ChapterReviewCase) -> bool:
    """判断违规是否与场景合同相关。

    Only true contract/schema/outline problems should reach contract_patch.
    Prose-level contract violations such as forbidden_triggered or
    required_ambiguity_broken are repaired in the scene text.
    """
    repair_scope = str(violation.get("repair_scope") or violation.get("scope") or "")
    if repair_scope in {"scene_contract", "chapter_contract"}:
        return True
    if violation.get("repairable_by_contract") is True and violation.get("repairable_by_text") is not True:
        return True
    if violation.get("repairable_by_text") is True:
        return False

    # 检查违规是否引用了合同字段
    contract_fields = {"word_budget", "pacing_target", "scene_role", "emotional_arc"}
    target_span = violation.get("target_span") or ""
    for field in contract_fields:
        if field in target_span:
            return True

    # 检查是否涉及大纲合同
    v_type = violation.get("violation_type") or violation.get("type") or ""
    if v_type in _TRUE_CONTRACT_REPAIR_TYPES:
        return True
    if "contract" in v_type.lower() or "outline" in v_type.lower():
        return True

    return False
