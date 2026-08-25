"""AI Quality Coordinator Agent — 主编系统下属的质量副主编.

职责：
- 汇总 AIFlavorChecker / ConceptBudgetChecker / CharacterVoiceChecker / ReaderExperience 的 advisory
- 将检测指标翻译为主编可读的质量摘要（risk_summary）
- 将 advisory 转换为 quality_guidance_patch（生成前注入场景合同）
- 将局部问题转换为 revision_hints（生成后修订建议）
- 维护项目级质量记忆（recent_quality_patterns）

不做什么：
- 不直接生成正文
- 不绕过 EditorInChiefAgent 决定剧情方向
- 不绕过 SceneContract 修改项目状态
- 不把 advisory 自动升级为 violation
- 不直接污染 truth_snapshot / 核心事实 / StoryState

各模块开关独立生效：
- 每条 advisory 带有 source_checker 标记
- guidance/revision 按来源检测器的模式独立过滤
- report 模式的检测器只进摘要，不生成 guidance/revision
- assist 模式的检测器可生成 guidance + 高风险 revision_hint
- enforce 模式的检测器可生成受控 revision_hint
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Literal

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class CoordinatorInput:
    """Coordinator 的输入契约. """
    scene_contract: dict = field(default_factory=dict)
    draft_text: str = ""
    quality_reports: dict = field(default_factory=dict)  # ai_flavor / concept_budget / character_voice / reader_experience
    project_quality_memory: dict = field(default_factory=dict)  # recent_quality_patterns
    source_modes: dict = field(default_factory=dict)  # {"ai_flavor": "report", "concept_budget": "enforce", ...}
    current_chapter: int = 0
    current_scene: int = 0


@dataclass
class CoordinatorOutput:
    """Coordinator 的输出契约. """
    risk_summary: str = ""
    quality_guidance_patch: dict = field(default_factory=dict)  # anti_ai_guidance / character_voice_guidance
    revision_hints: list = field(default_factory=list)
    recent_quality_patterns: list = field(default_factory=list)
    coordinator_mode: str = "off"


# ---------------------------------------------------------------------------
# Advisory 分类汇总
# ---------------------------------------------------------------------------

_ADVISORY_TYPE_LABELS: dict[str, str] = {
    # AI flavor
    "template_phrase": "模板化表达",
    "format_artifact": "格式化痕迹",
    "dialogue_tag": "对白标签单一",
    "emotion_label": "情绪直接标注",
    "technical_register": "技术性叙述",
    "explanatory_narration": "解释性叙述",
    "formulaic_transition": "公式化转折",
    "rhythm_uniformity": "节奏均匀",
    "punctuation_artifact": "标点停顿痕迹",
    "ai_simile_overuse": "明喻句式过度",
    "ai_synesthesia_formula": "通感公式句式",
    "ai_false_agency": "虚假代理句式",
    # Concept budget
    "concept_budget_exceeded": "概念投放超预算",
    "unplanned_new_concept": "计划外新概念",
    "forbidden_future_concept": "提前揭示未来概念",
    # Character voice
    "avoided_words": "角色使用了回避词",
    "voice_merge": "角色声纹趋同",
    "dialogue_info_dump": "对白信息倾倒",
    # Reader experience
    "reader_confusion": "读者困惑",
    "reader_boredom": "读者无聊",
    "reader_inconsistency": "读者感知矛盾",
    "reader_cognitive_load": "读者认知负担偏高",
    "reader_experience_advice": "读者体验建议",
    # Narrative experience
    "low_reading_drive": "继续阅读欲偏弱",
    "low_scene_pressure": "场景压力偏弱",
    "passive_protagonist": "主角主动性不足",
    "conflict_only_explained": "冲突只被说明",
    "exposition_driven_reveal": "信息释放依赖说明",
    "missing_dialogue_pressure": "互动压力不足",
    "weak_hook_out": "出场钩子偏弱",
    "mode_mismatch": "写作模式不匹配",
    # Literary quality
    "specificity_budget_unmet": "具体性预算不足",
    "abstraction_over_budget": "抽象解释超预算",
    "emotional_claim_without_scene_evidence": "情绪缺少场景证据",
    "prose_identity_weak": "语言辨识度偏弱",
    "over_literary_for_mode": "相对模式过度文学化",
    "too_plain_for_literary_mode": "相对文学模式偏平",
    "style_experience_conflict": "风格体验冲突",
    # Scene credibility
    "fact_boundary_conflict": "事实边界冲突",
    "knowledge_boundary_violation": "人物知识越界",
    "plausibility_break": "行为/常识不可信",
    "memory_plausibility_break": "记忆可信度不足",
    "narration_explanation_artifact": "叙事解释腔",
    "explanatory_punctuation_artifact": "解释性标点痕迹",
}

_GUIDANCE_MAP: dict[str, list[str]] = {
    "template_phrase": ["避免总结式结尾和套话", "用具体动作替代抽象概括"],
    "format_artifact": ["删除编号式列举", "用叙事节奏替代结构化呈现"],
    "dialogue_tag": ["减少'说道'类标签", "用动作和语境暗示说话人"],
    "emotion_label": ["减少直接解释人物情绪", "优先通过动作和观察传递判断"],
    "technical_register": ["避免学术性解释", "用角色视角内可感知的方式传递信息"],
    "explanatory_narration": ["删除因果解释", "改为角色检查、观察、停顿等可观察行为"],
    "formulaic_transition": ["减少模板化转折", "用场景内在逻辑推动过渡"],
    "rhythm_uniformity": ["变化句子长度", "交替使用短句加速和长句铺陈"],
    "punctuation_artifact": ["减少破折号制造的停顿", "用动作、句读和段落节奏承接转折"],
    "ai_simile_overuse": ["减少'像...一样'/'仿佛'/'如同'等明喻包装", "直接描写动作、感官或物件状态"],
    "ai_synesthesia_formula": ["减少'X中带着Y'的通感公式", "用具体感官细节替代混合感官"],
    "ai_false_agency": ["用具体角色作为动作执行者", "避免'决定出现了'/'变化发生了'等无主语句式"],
    "concept_budget_exceeded": ["本场景不引入新概念", "只深化已有概念"],
    "unplanned_new_concept": ["不引入计划外设定", "只使用已建立的世界观元素"],
    "forbidden_future_concept": ["不得提前揭示后续设定", "只给碎片线索"],
    "voice_merge": ["区分角色对白风格", "用句长、语气助词、用词偏好区分角色"],
    "dialogue_info_dump": ["拆分对白中的信息", "用动作和观察分散信息释放"],
    "low_reading_drive": ["尽早建立可见压力", "结尾停在行动、发现、代价或疑问上"],
    "low_scene_pressure": ["让阻碍通过人物互动、时间限制、资源代价或现场变化呈现"],
    "passive_protagonist": ["让核心角色做出可观察选择", "用行动后果体现主动性"],
    "conflict_only_explained": ["把冲突改成现场交锋、阻断、误差或代价"],
    "exposition_driven_reveal": ["将信息拆给动作、对话、物件和感官观察"],
    "missing_dialogue_pressure": ["用少量高压对白、停顿或打断承载互动压力"],
    "weak_hook_out": ["结尾停在具体变化或未解问题上，不做抽象总结"],
    "mode_mismatch": ["优先修复当前写作模式的高权重指标"],
    "specificity_budget_unmet": ["补充承担信息或压力的具体细节"],
    "abstraction_over_budget": ["减少抽象解释，改用场景证据"],
    "emotional_claim_without_scene_evidence": ["用身体反应、物件处理和对白潜台词承载情绪"],
    "prose_identity_weak": ["让措辞、句式和细节更贴合叙述人格"],
    "over_literary_for_mode": ["保留质感，但先保证目标、阻碍、代价和钩子清楚"],
    "too_plain_for_literary_mode": ["增加承担心理、关系或主题压力的具体细节"],
    "style_experience_conflict": ["按风格硬规则优先，协商体验目标的承载方式"],
    "fact_boundary_conflict": ["生成前锁定身份、地点、对象归属、权限和时间状态", "正文不得写出与事实边界互斥的断言"],
    "knowledge_boundary_violation": ["明确角色已知、可推断和不可知道的信息", "让判断来自观察、对话、线索或身体反应"],
    "plausibility_break": ["重要结论必须有观察、对话、线索或动作代价支撑"],
    "memory_plausibility_break": ["记忆采用触发式、片段式、不完整呈现，避免无触发完整复盘"],
    "narration_explanation_artifact": ["减少解释腔，用动作、停顿、视线、感官和选择承载判断"],
    "explanatory_punctuation_artifact": ["减少解释性破折号，改用句读、动作承接或对白压力"],
}

# advisory type → 所属检测器
_ADVISORY_TYPE_SOURCE: dict[str, str] = {
    # AI flavor
    "template_phrase": "ai_flavor",
    "format_artifact": "ai_flavor",
    "dialogue_tag": "ai_flavor",
    "emotion_label": "ai_flavor",
    "technical_register": "ai_flavor",
    "explanatory_narration": "ai_flavor",
    "formulaic_transition": "ai_flavor",
    "rhythm_uniformity": "ai_flavor",
    "punctuation_artifact": "ai_flavor",
    "ai_simile_overuse": "ai_flavor",
    "ai_synesthesia_formula": "ai_flavor",
    "ai_false_agency": "ai_flavor",
    # Concept budget
    "concept_budget_exceeded": "concept_budget",
    "unplanned_new_concept": "concept_budget",
    "forbidden_future_concept": "concept_budget",
    # Character voice
    "avoided_words": "character_voice",
    "voice_merge": "character_voice",
    "dialogue_info_dump": "character_voice",
    # Reader experience
    "reader_confusion": "reader_experience",
    "reader_boredom": "reader_experience",
    "reader_inconsistency": "reader_experience",
    "reader_cognitive_load": "reader_experience",
    "reader_experience_advice": "reader_experience",
    # Narrative experience
    "low_reading_drive": "narrative_experience",
    "low_scene_pressure": "narrative_experience",
    "passive_protagonist": "narrative_experience",
    "conflict_only_explained": "narrative_experience",
    "exposition_driven_reveal": "narrative_experience",
    "missing_dialogue_pressure": "narrative_experience",
    "weak_hook_out": "narrative_experience",
    "mode_mismatch": "mode_fit",
    # Literary quality
    "specificity_budget_unmet": "literary_quality",
    "abstraction_over_budget": "literary_quality",
    "emotional_claim_without_scene_evidence": "literary_quality",
    "prose_identity_weak": "literary_quality",
    "over_literary_for_mode": "mode_fit",
    "too_plain_for_literary_mode": "mode_fit",
    "style_experience_conflict": "style_experience_conflict",
    # Scene credibility
    "fact_boundary_conflict": "scene_credibility",
    "knowledge_boundary_violation": "scene_credibility",
    "plausibility_break": "scene_credibility",
    "memory_plausibility_break": "scene_credibility",
    "narration_explanation_artifact": "scene_credibility",
    "explanatory_punctuation_artifact": "scene_credibility",
}

_ADVISORY_TYPE_ALIASES: dict[str, str] = {
    "ai_template_phrase": "template_phrase",
    "ai_format_artifact": "format_artifact",
    "ai_dialogue_tag_overuse": "dialogue_tag",
    "ai_emotion_label": "emotion_label",
    "ai_technical_register": "technical_register",
    "ai_explanatory_narration": "explanatory_narration",
    "ai_formulaic_transition": "formulaic_transition",
    "ai_rhythm_uniformity": "rhythm_uniformity",
    "ai_punctuation_artifact": "punctuation_artifact",
    "character_voice_avoided_word": "avoided_words",
    "ai_character_voice_merge": "voice_merge",
}


# ---------------------------------------------------------------------------
# 质量记忆
# ---------------------------------------------------------------------------

_PATTERN_COUNTERS: dict[str, str] = {
    "template_phrase": "summary_ending_overuse",
    "explanatory_narration": "explanatory_narration_overuse",
    "voice_merge": "voice_merge_trend",
    "rhythm_uniformity": "rhythm_uniformity_trend",
    "ai_simile_overuse": "ai_simile_overuse_trend",
    "ai_synesthesia_formula": "ai_synesthesia_formula_trend",
    "ai_false_agency": "ai_false_agency_trend",
    "concept_budget_exceeded": "concept_overload_trend",
    "dialogue_info_dump": "dialogue_dump_trend",
    "low_reading_drive": "low_reading_drive_trend",
    "low_scene_pressure": "low_scene_pressure_trend",
    "passive_protagonist": "passive_protagonist_trend",
    "exposition_driven_reveal": "exposition_driven_reveal_trend",
    "weak_hook_out": "weak_hook_out_trend",
    "specificity_budget_unmet": "specificity_budget_unmet_trend",
    "abstraction_over_budget": "abstraction_over_budget_trend",
    "prose_identity_weak": "prose_identity_weak_trend",
    "fact_boundary_conflict": "fact_boundary_conflict_trend",
    "knowledge_boundary_violation": "knowledge_boundary_violation_trend",
    "plausibility_break": "plausibility_break_trend",
    "memory_plausibility_break": "memory_plausibility_break_trend",
    "narration_explanation_artifact": "narration_explanation_trend",
    "explanatory_punctuation_artifact": "explanatory_punctuation_trend",
}

_PATTERN_SUGGESTIONS: dict[str, str] = {
    "summary_ending_overuse": "后续场景结尾优先停在动作或悬念上",
    "explanatory_narration_overuse": "减少直接心理解释，增加可观察行为",
    "voice_merge_trend": "加强角色对白差异化",
    "rhythm_uniformity_trend": "增加句式和段落节奏变化",
    "ai_simile_overuse_trend": "后续场景减少'像...一样'/'仿佛'等明喻包装，直接描写动作或感官",
    "ai_synesthesia_formula_trend": "后续场景减少'X中带着Y'的通感公式，用具体感官细节替代",
    "ai_false_agency_trend": "后续场景用具体角色作为动作执行者，避免无主语句式",
    "concept_overload_trend": "控制新概念投放节奏",
    "dialogue_dump_trend": "拆分对白信息，用行为分散释放",
    "low_reading_drive_trend": "后续场景优先建立压力、选择和出场钩子",
    "low_scene_pressure_trend": "让阻碍通过互动、时间、代价或现场变化呈现",
    "passive_protagonist_trend": "增加核心角色可观察选择",
    "exposition_driven_reveal_trend": "把说明拆给动作、对话、物件和后果",
    "weak_hook_out_trend": "结尾停在具体行动、发现、代价或疑问上",
    "specificity_budget_unmet_trend": "补充承担信息或情绪证据的具体细节",
    "abstraction_over_budget_trend": "降低抽象解释，改为场景化呈现",
    "prose_identity_weak_trend": "增强句式、措辞和叙述人格辨识度",
    "fact_boundary_conflict_trend": "后续场景强化事实边界合同",
    "knowledge_boundary_violation_trend": "后续场景强化人物知识边界",
    "plausibility_break_trend": "后续场景要求判断和行动有证据链",
    "memory_plausibility_break_trend": "后续场景使用片段式触发记忆",
    "narration_explanation_trend": "后续场景减少解释腔",
    "explanatory_punctuation_trend": "后续场景降低解释性破折号",
}

_ADVISORY_TYPE_LABELS.update({
    "weak_opening_hook": "商业开场钩子偏弱",
    "low_event_density": "商业事件密度偏低",
    "low_conflict_density": "商业冲突密度偏低",
    "flat_pressure_ramp": "商业压力曲线偏平",
    "weak_curiosity_engine": "悬念驱动偏弱",
    "low_reversal_density": "反转/变局密度偏低",
    "missing_micro_payoff": "阶段性兑现不足",
    "weak_chapter_end_hook": "章末追读钩子偏弱",
    "low_reader_retention": "留读驱动力不足",
})

_GUIDANCE_MAP.update({
    "weak_opening_hook": ["开场尽快给出可感知异常、目标、危机或利益变化", "避免以背景说明或抽象情绪作为唯一开场动力"],
    "low_event_density": ["提高场景内可见事件密度，让发现、阻碍、交换、代价至少持续推进一项"],
    "low_conflict_density": ["把冲突落到人物互动、资源限制、时间压力或现场变化上"],
    "flat_pressure_ramp": ["让压力逐段升级，从不便、风险、代价推进到必须选择"],
    "weak_curiosity_engine": ["保留一个清晰未解问题，并让读者知道答案会改变局面"],
    "low_reversal_density": ["加入一次信息变向、关系变向、局势变向或代价变向"],
    "missing_micro_payoff": ["兑现本场建立过的一个小问题，同时引出更大的未解问题"],
    "weak_chapter_end_hook": ["章末停在新发现、新危险、新选择或代价落下之前"],
    "low_reader_retention": ["确保目标、阻碍、代价、悬念、阶段兑现和出场钩子同时可见"],
})

_ADVISORY_TYPE_SOURCE.update({
    "weak_opening_hook": "commercial_pacing",
    "low_event_density": "commercial_pacing",
    "low_conflict_density": "commercial_pacing",
    "flat_pressure_ramp": "commercial_pacing",
    "weak_curiosity_engine": "commercial_pacing",
    "low_reversal_density": "commercial_pacing",
    "missing_micro_payoff": "commercial_pacing",
    "weak_chapter_end_hook": "commercial_pacing",
    "low_reader_retention": "commercial_pacing",
})

_PATTERN_COUNTERS.update({
    "weak_opening_hook": "weak_opening_hook_trend",
    "low_event_density": "low_event_density_trend",
    "low_conflict_density": "low_conflict_density_trend",
    "flat_pressure_ramp": "flat_pressure_ramp_trend",
    "weak_curiosity_engine": "weak_curiosity_engine_trend",
    "low_reversal_density": "low_reversal_density_trend",
    "missing_micro_payoff": "missing_micro_payoff_trend",
    "weak_chapter_end_hook": "weak_chapter_end_hook_trend",
    "low_reader_retention": "low_reader_retention_trend",
})

_PATTERN_SUGGESTIONS.update({
    "weak_opening_hook_trend": "后续场景优先用异常、目标、危机或利益变化开场。",
    "low_event_density_trend": "后续场景减少静态铺陈，增加发现、阻碍、交换或代价。",
    "low_conflict_density_trend": "后续场景把冲突落到人物互动、资源、时间或现场变化。",
    "flat_pressure_ramp_trend": "后续场景让压力逐段升级，最终逼出选择。",
    "weak_curiosity_engine_trend": "后续场景保留能改变局面的清晰未解问题。",
    "low_reversal_density_trend": "后续场景安排信息、关系、局势或代价变向。",
    "missing_micro_payoff_trend": "后续场景兑现一个小问题，再引出更大的未解问题。",
    "weak_chapter_end_hook_trend": "后续章末停在新发现、新危险、新选择或代价落下前。",
    "low_reader_retention_trend": "后续场景同时检查目标、阻碍、代价、悬念、兑现和钩子。",
})

for alias, canonical in _ADVISORY_TYPE_ALIASES.items():
    if canonical in _ADVISORY_TYPE_LABELS:
        _ADVISORY_TYPE_LABELS[alias] = _ADVISORY_TYPE_LABELS[canonical]
    if canonical in _GUIDANCE_MAP:
        _GUIDANCE_MAP[alias] = _GUIDANCE_MAP[canonical]
    if canonical in _ADVISORY_TYPE_SOURCE:
        _ADVISORY_TYPE_SOURCE[alias] = _ADVISORY_TYPE_SOURCE[canonical]
    if canonical in _PATTERN_COUNTERS:
        _PATTERN_COUNTERS[alias] = _PATTERN_COUNTERS[canonical]


def _update_quality_memory(
    existing_memory: dict,
    advisories: list[dict],
    source_modes: dict | None = None,
    current_chapter: int = 0,
    current_scene: int = 0,
    max_patterns: int = 10,
    window_size: int = 10,
    min_confidence: float = 0.5,
) -> list[dict]:
    """根据本次 advisory 更新项目级质量记忆，返回 recent_quality_patterns 列表.

    过滤规则：
    - 仅累计 medium/high 严重级别的 advisory
    - 仅累计置信度 >= min_confidence 的 advisory
    - 仅累计来源检测器模式为 assist/enforce 的 advisory
    - 记录 last_seen_chapter / last_seen_scene
    - 按最近 window_size 个场景计算，连续未出现时衰减计数
    """
    # 加载已有记忆
    patterns: dict[str, dict] = {}
    for p in existing_memory.get("recent_quality_patterns", []):
        if isinstance(p, dict) and p.get("type"):
            patterns[p["type"]] = {
                "count": p.get("count", 0),
                "suggestion": p.get("suggestion", ""),
                "last_seen_chapter": p.get("last_seen_chapter", 0),
                "last_seen_scene": p.get("last_seen_scene", 0),
            }

    # 累加本次 advisory
    for adv in advisories:
        if not isinstance(adv, dict):
            continue
        severity = adv.get("severity", "low")
        if severity not in ("medium", "high"):
            continue
        confidence = adv.get("confidence", 0.5)
        if confidence < min_confidence:
            continue

        # 按来源检测器模式过滤：只有 assist/enforce 的检测器才写入记忆
        # 无 source_modes 时按 off 处理，不累计记忆
        if source_modes is None:
            continue
        src_mode = _effective_mode_for_advisory(adv, source_modes)
        if src_mode not in ("assist", "enforce"):
            continue

        adv_type = adv.get("type", "")
        pattern_key = _PATTERN_COUNTERS.get(adv_type)
        if pattern_key:
            if pattern_key not in patterns:
                patterns[pattern_key] = {
                    "count": 0,
                    "suggestion": _PATTERN_SUGGESTIONS.get(pattern_key, ""),
                    "last_seen_chapter": 0,
                    "last_seen_scene": 0,
                }
            patterns[pattern_key]["count"] += 1
            patterns[pattern_key]["last_seen_chapter"] = current_chapter
            patterns[pattern_key]["last_seen_scene"] = current_scene

    # 衰减：连续 window_size 个场景未出现的模式，计数减半
    for ptype, pdata in patterns.items():
        last_ch = pdata.get("last_seen_chapter", 0)
        last_sc = pdata.get("last_seen_scene", 0)
        last_pos = last_ch * 10 + last_sc
        current_pos = current_chapter * 10 + current_scene
        distance = current_pos - last_pos
        if distance > window_size and pdata["count"] > 0:
            pdata["count"] = max(1, pdata["count"] // 2)

    # 转换为列表，按 count 降序
    result = []
    for ptype, pdata in sorted(patterns.items(), key=lambda x: -x[1].get("count", 0)):
        count = pdata.get("count", 0)
        if count <= 0:
            continue
        result.append({
            "type": ptype,
            "count": count,
            "suggestion": pdata.get("suggestion", ""),
            "last_seen_chapter": pdata.get("last_seen_chapter", 0),
            "last_seen_scene": pdata.get("last_seen_scene", 0),
        })

    return result[:max_patterns]


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _effective_mode_for_advisory(adv: dict, source_modes: dict) -> str:
    """获取某条 advisory 来源检测器的有效模式. """
    source = adv.get("source_checker", "")
    if source and source in source_modes:
        return source_modes[source]
    # 回退：从 advisory type 推断来源
    adv_type = adv.get("type", "")
    source_from_type = _ADVISORY_TYPE_SOURCE.get(adv_type, "")
    if source_from_type and source_from_type in source_modes:
        return source_modes[source_from_type]
    return "off"


def _any_mode_at_least(source_modes: dict, threshold: str) -> bool:
    """检查是否有任何检测器模式达到或超过指定级别. """
    level_order = {"off": 0, "shadow": 1, "report": 2, "assist": 3, "enforce": 4}
    threshold_level = level_order.get(threshold, 0)
    return any(level_order.get(m, 0) >= threshold_level for m in source_modes.values())


# ---------------------------------------------------------------------------
# AIQualityCoordinatorAgent
# ---------------------------------------------------------------------------

class AIQualityCoordinatorAgent:
    """主编系统下属的质量副主编.

    各模块开关独立生效：
    - off: 不运行
    - shadow: 只生成内部质量摘要
    - report: 生成用户可见报告，不影响生成
    - assist: 生成质量约束补丁和修订建议（需主编确认）
    - enforce: 高置信问题进入受控修订流程
    """

    def coordinate(self, coordinator_input: CoordinatorInput) -> CoordinatorOutput:
        """主入口：输入 advisory + 质量记忆，输出 risk_summary + guidance_patch + revision_hints. """
        source_modes = coordinator_input.source_modes

        # 如果所有检测器都是 off，直接返回
        if not source_modes or all(m == "off" for m in source_modes.values()):
            return CoordinatorOutput(coordinator_mode="off")

        # 收集所有 advisory，标记来源
        advisories = self._collect_advisories(coordinator_input.quality_reports)

        # 计算全局协调模式（用于输出标记）
        coord_mode = "off"
        if _any_mode_at_least(source_modes, "enforce"):
            coord_mode = "enforce"
        elif _any_mode_at_least(source_modes, "assist"):
            coord_mode = "assist"
        elif _any_mode_at_least(source_modes, "report"):
            coord_mode = "report"
        elif _any_mode_at_least(source_modes, "shadow"):
            coord_mode = "shadow"

        # shadow 模式：只生成内部摘要
        if coord_mode == "shadow":
            risk_summary = self._build_risk_summary(advisories)
            return CoordinatorOutput(
                risk_summary=risk_summary,
                coordinator_mode="shadow",
            )

        # report/assist/enforce 模式
        risk_summary = self._build_risk_summary(advisories)
        # guidance/revision 按各 advisory 来源模式独立过滤
        quality_guidance_patch = self._build_guidance_patch(advisories, source_modes)
        revision_hints = self._build_revision_hints(advisories, source_modes, coordinator_input.draft_text)
        updated_patterns = _update_quality_memory(
            coordinator_input.project_quality_memory, advisories,
            source_modes=source_modes,
            current_chapter=coordinator_input.current_chapter,
            current_scene=coordinator_input.current_scene,
        )

        return CoordinatorOutput(
            risk_summary=risk_summary,
            quality_guidance_patch=quality_guidance_patch,
            revision_hints=revision_hints,
            recent_quality_patterns=updated_patterns,
            coordinator_mode=coord_mode,
        )

    def build_pre_generation_patch(
        self,
        project_id: str,
        scene_contract: dict,
        quality_memory: dict,
        feature_policy=None,
    ) -> dict:
        """生成前：根据质量记忆构建 quality_extensions 注入场景合同.

        返回值可直接写入 scene_contract["quality_extensions"]。
        只有 assist/enforce 模式可以影响后续生成；report 只负责可见报告。
        feature_policy 接受 GenerationFeaturePolicy 对象或 dict，按各检测器独立判断。
        """
        # 解析各检测器模式
        modes = self._resolve_modes(feature_policy)

        has_active = any(m in ("assist", "enforce") for m in modes.values())
        if not has_active:
            return {}

        patterns = quality_memory.get("recent_quality_patterns", [])
        excerpts = quality_memory.get("recent_ai_flavor_excerpts", [])
        if not patterns and not excerpts:
            return {}

        anti_ai_guidance = []
        reveal_control = {}
        character_voice_guidance = {}
        experience_guidance = []
        literary_quality_guidance = []
        commercial_pacing_guidance = []
        scene_credibility_guidance = []
        style_experience_resolution = []

        # ===== 反例驱动：注入具体 AI 味句子 =====
        if modes.get("ai_flavor") in ("assist", "enforce") and excerpts:
            # 取最近 5 条反例（按章节倒序，最新的在前）
            recent_excerpts = sorted(
                [e for e in excerpts if isinstance(e, dict) and e.get("text")],
                key=lambda e: (-int(e.get("chapter", 0) or 0), -int(e.get("scene", 0) or 0)),
            )[:5]
            if recent_excerpts:
                offender_texts = [e["text"] for e in recent_excerpts]
                # 反例清单（每条 ≤80 字，避免 prompt 膨胀）
                offenders_str = "；".join([f"'{t[:80]}'" for t in offender_texts[:3]])
                anti_ai_guidance.append(
                    f"你最近章节写出了这些 AI 味句子（本章不要重复类似句式）：{offenders_str}"
                )
                # 量化约束
                simile_count = sum(1 for e in recent_excerpts if "simile" in str(e.get("type", "")).lower())
                if simile_count > 0:
                    anti_ai_guidance.append(
                        f"上一章检测到 {simile_count} 处明喻类问题，本章'像'字比喻总量控制在 8 个以内，优先用具象描述"
                    )

        # 从质量记忆中提取约束，按 advisory 来源分别判断
        for pattern in patterns:
            if not isinstance(pattern, dict):
                continue
            ptype = pattern.get("type", "")
            count = pattern.get("count", 0)

            if count < 2:
                continue

            if ptype == "summary_ending_overuse":
                if modes.get("ai_flavor") in ("assist", "enforce"):
                    anti_ai_guidance.append("结尾停在角色行动或悬念上，不要总结")
            elif ptype == "explanatory_narration_overuse":
                if modes.get("ai_flavor") in ("assist", "enforce"):
                    anti_ai_guidance.append("减少直接心理解释，用动作和观察传递判断")
            elif ptype == "voice_merge_trend":
                if modes.get("character_voice") in ("assist", "enforce"):
                    anti_ai_guidance.append("区分角色对白风格，用句长和语气助词区分")
            elif ptype == "rhythm_uniformity_trend":
                if modes.get("ai_flavor") in ("assist", "enforce"):
                    anti_ai_guidance.append("变化句子长度，交替使用短句和长句")
            elif ptype == "ai_simile_overuse_trend":
                if modes.get("ai_flavor") in ("assist", "enforce"):
                    anti_ai_guidance.append("减少'像...一样'/'仿佛'/'如同'等明喻包装，直接描写动作或感官")
            elif ptype == "ai_synesthesia_formula_trend":
                if modes.get("ai_flavor") in ("assist", "enforce"):
                    anti_ai_guidance.append("减少'X中带着Y'的通感公式，用具体感官细节替代")
            elif ptype == "ai_false_agency_trend":
                if modes.get("ai_flavor") in ("assist", "enforce"):
                    anti_ai_guidance.append("用具体角色作为动作执行者，避免无主语句式")
            elif ptype == "concept_overload_trend":
                if modes.get("concept_budget") in ("assist", "enforce"):
                    reveal_control["new_concept_budget"] = 1
                    reveal_control["preferred_carriers"] = ["action", "dialogue", "observation"]
            elif ptype == "dialogue_dump_trend":
                if modes.get("character_voice") in ("assist", "enforce"):
                    anti_ai_guidance.append("拆分对白中的信息，用动作和观察分散释放")
            elif ptype == "low_reading_drive_trend":
                if modes.get("narrative_experience") in ("assist", "enforce"):
                    experience_guidance.append("本场尽早建立可见压力，并在结尾留下行动、发现、代价或疑问")
            elif ptype == "low_scene_pressure_trend":
                if modes.get("narrative_experience") in ("assist", "enforce"):
                    experience_guidance.append("让阻碍以互动、时间限制、资源代价或现场变化出现")
            elif ptype == "passive_protagonist_trend":
                if modes.get("narrative_experience") in ("assist", "enforce"):
                    experience_guidance.append("核心角色必须做出可观察选择，避免只被事件推着走")
            elif ptype == "exposition_driven_reveal_trend":
                if modes.get("narrative_experience") in ("assist", "enforce"):
                    experience_guidance.append("信息释放依附动作、对话、物件或感官观察")
            elif ptype == "weak_hook_out_trend":
                if modes.get("narrative_experience") in ("assist", "enforce"):
                    experience_guidance.append("结尾停在具体变化或未解问题上，不要抽象总结")
            elif ptype == "specificity_budget_unmet_trend":
                if modes.get("literary_quality") in ("assist", "enforce"):
                    literary_quality_guidance.append("补充承担信息、压力或情绪证据的具体细节")
            elif ptype == "abstraction_over_budget_trend":
                if modes.get("literary_quality") in ("assist", "enforce"):
                    literary_quality_guidance.append("减少抽象解释，改用场景证据推进")
            elif ptype == "prose_identity_weak_trend":
                if modes.get("literary_quality") in ("assist", "enforce"):
                    literary_quality_guidance.append("让措辞、句式和细节更贴合叙述人格")
            elif ptype in {
                "fact_boundary_conflict_trend",
                "knowledge_boundary_violation_trend",
                "plausibility_break_trend",
                "memory_plausibility_break_trend",
                "narration_explanation_trend",
                "explanatory_punctuation_trend",
            }:
                if modes.get("scene_credibility") in ("assist", "enforce"):
                    suggestion = _PATTERN_SUGGESTIONS.get(ptype)
                    if suggestion:
                        scene_credibility_guidance.append(suggestion)

        # 去重
        if modes.get("commercial_pacing") in ("assist", "enforce"):
            commercial_trend_types = {
                "weak_opening_hook_trend",
                "low_event_density_trend",
                "low_conflict_density_trend",
                "flat_pressure_ramp_trend",
                "weak_curiosity_engine_trend",
                "low_reversal_density_trend",
                "missing_micro_payoff_trend",
                "weak_chapter_end_hook_trend",
                "low_reader_retention_trend",
            }
            for pattern in patterns:
                if not isinstance(pattern, dict):
                    continue
                ptype = pattern.get("type", "")
                try:
                    count = int(pattern.get("count", 0) or 0)
                except (TypeError, ValueError):
                    count = 0
                if count >= 2 and ptype in commercial_trend_types:
                    suggestion = _PATTERN_SUGGESTIONS.get(ptype)
                    if suggestion:
                        commercial_pacing_guidance.append(suggestion)

        anti_ai_guidance = list(dict.fromkeys(anti_ai_guidance))
        anti_ai_guidance = anti_ai_guidance[:5]
        experience_guidance = list(dict.fromkeys(experience_guidance))[:5]
        literary_quality_guidance = list(dict.fromkeys(literary_quality_guidance))[:5]
        commercial_pacing_guidance = list(dict.fromkeys(commercial_pacing_guidance))[:7]
        scene_credibility_guidance = list(dict.fromkeys(scene_credibility_guidance))[:8]
        style_experience_resolution = list(dict.fromkeys(style_experience_resolution))[:5]

        if (
            not anti_ai_guidance
            and not reveal_control
            and not experience_guidance
            and not literary_quality_guidance
            and not commercial_pacing_guidance
            and not scene_credibility_guidance
            and not style_experience_resolution
        ):
            return {}

        result = {}
        if anti_ai_guidance:
            result["anti_ai_guidance"] = anti_ai_guidance
        if reveal_control:
            result["reveal_control"] = reveal_control
        if experience_guidance:
            result["experience_guidance"] = experience_guidance
        if literary_quality_guidance:
            result["literary_quality_guidance"] = literary_quality_guidance
        if commercial_pacing_guidance:
            result["commercial_pacing_guidance"] = commercial_pacing_guidance
        if scene_credibility_guidance:
            result["scene_credibility_guidance"] = scene_credibility_guidance
        if style_experience_resolution:
            result["style_experience_resolution"] = style_experience_resolution
        return result

    @staticmethod
    def _resolve_modes(feature_policy) -> dict[str, str]:
        """从 GenerationFeaturePolicy 或 dict 中解析各检测器模式. """
        modes = {
            "ai_flavor": "off",
            "concept_budget": "off",
            "character_voice": "off",
            "reader_experience": "off",
            "writing_mode_profile": "off",
            "narrative_experience": "off",
            "literary_quality": "off",
            "commercial_pacing": "off",
            "scene_credibility": "off",
            "mode_fit": "off",
            "style_experience_conflict": "off",
            "llm_semantic": "off",  # 附录4问题7修复
        }
        if feature_policy is None:
            return modes

        field_map = {
            "ai_flavor": "ai_flavor_mode",
            "concept_budget": "concept_budget_mode",
            "character_voice": "character_voice_mode",
            "reader_experience": "reader_experience_mode",
            "writing_mode_profile": "writing_mode_profile_mode",
            "narrative_experience": "narrative_experience_mode",
            "literary_quality": "literary_quality_mode",
            "commercial_pacing": "commercial_pacing_mode",
            "scene_credibility": "scene_credibility_mode",
            "mode_fit": "mode_fit_mode",
            "style_experience_conflict": "style_experience_conflict_mode",
        }
        for key, field_name in field_map.items():
            if hasattr(feature_policy, field_name):
                modes[key] = getattr(feature_policy, field_name, "off")
            elif isinstance(feature_policy, dict):
                modes[key] = feature_policy.get(field_name, "off")
        return modes

    # -----------------------------------------------------------------------
    # 内部方法
    # -----------------------------------------------------------------------

    def _collect_advisories(self, quality_reports: dict) -> list[dict]:
        """从各检测器报告中收集所有 advisory，标记来源检测器. """
        advisories: list[dict] = []
        for report_name in (
            "ai_flavor",
            "concept_budget",
            "character_voice",
            "reader_experience",
            "narrative_experience",
            "literary_quality",
            "commercial_pacing",
            "scene_credibility",
            "mode_fit",
            "style_experience_conflict",
            "llm_semantic",  # 附录4问题7修复
        ):
            report = quality_reports.get(report_name)
            if not isinstance(report, dict):
                continue
            report_advisories = report.get("advisories", [])
            if report_name == "style_experience_conflict" and not report_advisories:
                report_advisories = self._style_conflict_advisories(report)
            if isinstance(report_advisories, list):
                for adv in report_advisories:
                    if isinstance(adv, dict):
                        # 标记来源检测器
                        adv_with_source = dict(adv)
                        adv_with_source.setdefault("source_checker", report_name)
                        advisories.append(adv_with_source)
        return advisories

    @staticmethod
    def _style_conflict_advisories(report: dict) -> list[dict]:
        conflicts = report.get("conflicts", [])
        if not isinstance(conflicts, list):
            return []
        advisories = []
        for conflict in conflicts[:5]:
            if not isinstance(conflict, dict):
                continue
            advisories.append({
                "type": "style_experience_conflict",
                "severity": conflict.get("severity", "low"),
                "detail": conflict.get("resolution") or conflict.get("experience_need") or "风格与体验目标存在冲突。",
                "expected_behavior": conflict.get("writer_guidance", ""),
                "confidence": 0.62,
                "detector": "style_experience_conflict_resolver",
            })
        return advisories

    def _build_risk_summary(self, advisories: list[dict]) -> str:
        """将 advisory 汇总为主编可读的风险摘要. """
        if not advisories:
            return "本次生成未检测到显著质量风险。"

        # 按类型分组计数
        type_counts: dict[str, int] = {}
        high_types: list[str] = []
        for adv in advisories:
            adv_type = adv.get("type", "unknown")
            type_counts[adv_type] = type_counts.get(adv_type, 0) + 1
            if adv.get("severity") == "high" and adv_type not in high_types:
                high_types.append(adv_type)

        # 构建摘要
        parts = []
        if high_types:
            high_labels = [_ADVISORY_TYPE_LABELS.get(t, t) for t in high_types]
            parts.append(f"高风险：{'、'.join(high_labels)}")

        # 按频次排序
        sorted_types = sorted(type_counts.items(), key=lambda x: -x[1])
        other_labels = []
        for adv_type, count in sorted_types:
            if adv_type in high_types:
                continue
            label = _ADVISORY_TYPE_LABELS.get(adv_type, adv_type)
            other_labels.append(f"{label}({count})")

        if other_labels:
            parts.append(f"其他问题：{'、'.join(other_labels[:5])}")

        if high_types:
            parts.append("建议关注高置信问题，考虑局部修订。")
        else:
            parts.append("整体风险可控，可选择性优化。")

        return "；".join(parts)

    def _build_guidance_patch(self, advisories: list[dict], source_modes: dict) -> dict:
        """将 advisory 转换为 quality_guidance_patch，按来源模式过滤.

        只有来源检测器模式为 assist/enforce 的 advisory 才生成 guidance。
        report 模式的检测器只进摘要，不生成 guidance。
        """
        anti_ai_guidance: list[str] = []
        experience_guidance: list[str] = []
        literary_quality_guidance: list[str] = []
        commercial_pacing_guidance: list[str] = []
        scene_credibility_guidance: list[str] = []
        style_experience_resolution: list[str] = []
        character_voice_guidance: dict[str, list[str]] = {}

        for adv in advisories:
            if not isinstance(adv, dict):
                continue

            # 按来源模式过滤：只有 assist/enforce 才生成 guidance
            src_mode = _effective_mode_for_advisory(adv, source_modes)
            if src_mode not in ("assist", "enforce"):
                continue

            adv_type = adv.get("type", "")
            severity = adv.get("severity", "low")

            # 低严重度在非 enforce 模式下跳过
            if severity == "low" and src_mode != "enforce":
                continue

            source = _ADVISORY_TYPE_SOURCE.get(adv_type, adv.get("source_checker", ""))

            # 不同来源写入不同 quality_extensions 槽位
            if adv_type in _GUIDANCE_MAP:
                target_list = anti_ai_guidance
                if source == "narrative_experience":
                    target_list = experience_guidance
                elif source == "literary_quality":
                    target_list = literary_quality_guidance
                elif source == "commercial_pacing":
                    target_list = commercial_pacing_guidance
                elif source == "scene_credibility":
                    target_list = scene_credibility_guidance
                elif source in {"mode_fit", "style_experience_conflict"}:
                    target_list = style_experience_resolution
                for guidance in _GUIDANCE_MAP[adv_type]:
                    if guidance not in target_list:
                        target_list.append(guidance)

            # 角色声纹类 → character_voice_guidance
            if adv_type in (
                "voice_merge",
                "avoided_words",
                "dialogue_info_dump",
                "ai_character_voice_merge",
                "character_voice_avoided_word",
            ):
                target_span = adv.get("target_span", "")
                detail = adv.get("detail", "")
                char_name = self._extract_character_name(target_span, detail)
                if char_name:
                    if char_name not in character_voice_guidance:
                        character_voice_guidance[char_name] = []
                    for g in _GUIDANCE_MAP.get(adv_type, []):
                        if g not in character_voice_guidance[char_name]:
                            character_voice_guidance[char_name].append(g)

        # 限制数量
        anti_ai_guidance = anti_ai_guidance[:5]
        experience_guidance = experience_guidance[:5]
        literary_quality_guidance = literary_quality_guidance[:5]
        commercial_pacing_guidance = commercial_pacing_guidance[:7]
        scene_credibility_guidance = scene_credibility_guidance[:8]
        style_experience_resolution = style_experience_resolution[:5]

        result = {}
        if anti_ai_guidance:
            result["anti_ai_guidance"] = anti_ai_guidance
        if experience_guidance:
            result["experience_guidance"] = experience_guidance
        if literary_quality_guidance:
            result["literary_quality_guidance"] = literary_quality_guidance
        if commercial_pacing_guidance:
            result["commercial_pacing_guidance"] = commercial_pacing_guidance
        if scene_credibility_guidance:
            result["scene_credibility_guidance"] = scene_credibility_guidance
        if style_experience_resolution:
            result["style_experience_resolution"] = style_experience_resolution
        if character_voice_guidance:
            result["character_voice_guidance"] = character_voice_guidance
        return result

    def _build_revision_hints(self, advisories: list[dict], source_modes: dict, draft_text: str) -> list[dict]:
        """将高置信 advisory 转换为 revision_hints，按来源模式过滤.

        - report 模式的检测器不生成 revision_hint
        - assist 模式的检测器只对 high severity 生成 revision_hint
        - enforce 模式的检测器对 medium 及以上生成 revision_hint
        """
        hints: list[dict] = []
        seen_types: set[str] = set()

        for adv in advisories:
            if not isinstance(adv, dict):
                continue

            # 按来源模式过滤
            src_mode = _effective_mode_for_advisory(adv, source_modes)
            if src_mode not in ("assist", "enforce"):
                continue  # report/off 不生成 revision_hint

            severity = adv.get("severity", "low")
            adv_type = adv.get("type", "")

            # assist 只处理 high，enforce 处理 medium 及以上
            if severity == "low":
                continue

            # 同类型只生成一条 hint
            if adv_type in seen_types:
                continue
            seen_types.add(adv_type)

            target_span = adv.get("target_span") or self._infer_target_span(adv_type, draft_text)
            problem = _ADVISORY_TYPE_LABELS.get(adv_type, adv_type)
            strategy_list = _GUIDANCE_MAP.get(adv_type, ["请手动审查此问题"])
            strategy = "；".join(strategy_list)
            try:
                confidence = float(adv.get("confidence", 0.5) or 0.5)
            except (TypeError, ValueError):
                confidence = 0.5
            auto_revise_allowed = (
                src_mode in ("assist", "enforce")
                and severity in ("medium", "high")
                and confidence >= 0.6
            )

            hint = {
                "target_span": target_span,
                "problem": problem,
                "strategy": strategy,
                "preserve": ["剧情事实", "伏笔信息", "人物行为结果", "体验合同", "文笔风格硬规则"],
                "auto_revise_allowed": auto_revise_allowed,
                "severity_level": severity,
                "source_mode": src_mode,
                "needs_user_confirm": not auto_revise_allowed,
            }
            hints.append(hint)

        return hints[:5]

    @staticmethod
    def _extract_character_name(target_span: str, detail: str) -> str:
        """从 advisory 的 target_span 或 detail 中尝试提取角色名. """
        import re
        for text in (target_span, detail):
            if not text:
                continue
            m = re.search(r"角色[「「](.+?)[」」]|(.+?)的对白|(.+?)的声纹", text)
            if m:
                return (m.group(1) or m.group(2) or m.group(3)).strip()
        return ""

    @staticmethod
    def _infer_target_span(adv_type: str, draft_text: str) -> str:
        """当 advisory 没有提供 target_span 时，推断大致位置. """
        if not draft_text:
            return "全文"
        if adv_type in ("summary_ending_overuse", "template_phrase"):
            return "结尾段落"
        if adv_type in ("explanatory_narration", "technical_register"):
            return "叙述段落"
        if adv_type in ("voice_merge", "dialogue_info_dump", "dialogue_tag"):
            return "对白段落"
        if adv_type in ("low_reading_drive", "weak_hook_out"):
            return "开头或结尾段落"
        if adv_type in ("low_scene_pressure", "passive_protagonist"):
            return "冲突推进段落"
        if adv_type in ("specificity_budget_unmet", "abstraction_over_budget", "prose_identity_weak"):
            return "叙述段落"
        return "相关段落"
