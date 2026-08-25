from __future__ import annotations

import re
from statistics import mean, pstdev

from app.models.literary_quality import (
    LiteraryQualityContract,
    LiteraryQualityReport,
    LiteraryQualityScores,
)
from app.models.quality_advisory import make_advisory
from app.models.writing_mode import WritingModeProfile
from app.utils.dash_artifacts import count_dash_artifacts


class LiteraryQualityChecker:
    """Mode-aware prose-quality diagnostics."""

    source_checker = "literary_quality"

    def check(
        self,
        text: str,
        contract: dict | LiteraryQualityContract | None = None,
        writing_mode_profile: dict | WritingModeProfile | None = None,
        style_context: dict | None = None,
    ) -> dict:
        profile = _profile(writing_mode_profile)
        lit_contract = _contract(contract)
        metrics = self._metrics(text)
        scores = self._scores(metrics, lit_contract, profile, style_context or {})
        advisories = self._advisories(text, lit_contract, profile, metrics, scores)
        return LiteraryQualityReport(
            writing_mode_id=profile.id,
            scores=scores,
            advisories=advisories,
            metrics=metrics,
            summary=self._summary(scores, advisories),
        ).model_dump()

    @staticmethod
    def _metrics(text: str) -> dict:
        text = text or ""
        sentences = [s.strip() for s in re.split(r"[。！？!?]+", text) if s.strip()]
        paragraphs = [p.strip() for p in re.split(r"\n+", text) if p.strip()]
        sentence_lengths = [len(s) for s in sentences]
        paragraph_lengths = [len(p) for p in paragraphs]
        abstract_hits = _count_any(text, _ABSTRACT_WORDS)
        generic_hits = _count_any(text, _GENERIC_LABELS)
        concrete_hits = _count_any(text, _CONCRETE_DETAIL_WORDS)
        sensory_hits = _count_any(text, _SENSORY_WORDS)
        body_hits = _count_any(text, _BODY_WORDS)
        emotion_hits = _count_any(text, _EMOTION_LABELS)
        emdash_hits = count_dash_artifacts(text)
        return {
            "length": len(text),
            "sentence_count": len(sentences),
            "paragraph_count": len(paragraphs),
            "sentence_length_cv": _cv(sentence_lengths),
            "paragraph_length_cv": _cv(paragraph_lengths),
            "abstract_hits": abstract_hits,
            "generic_label_hits": generic_hits,
            "concrete_hits": concrete_hits,
            "sensory_hits": sensory_hits,
            "body_hits": body_hits,
            "emotion_label_hits": emotion_hits,
            "emdash_hits": emdash_hits,
            "dialogue_count": text.count("“") // 2 + text.count('"') // 2,
            "unique_sentence_starters": _unique_sentence_starters(sentences),
        }

    @staticmethod
    def _scores(
        metrics: dict,
        contract: LiteraryQualityContract,
        profile: WritingModeProfile,
        style_context: dict,
    ) -> LiteraryQualityScores:
        length_unit = max(metrics["length"] / 1000, 1)
        concrete_density = (metrics["concrete_hits"] + metrics["sensory_hits"] + metrics["body_hits"]) / length_unit
        abstract_density = (metrics["abstract_hits"] + metrics["generic_label_hits"]) / length_unit
        emotion_evidence = _clamp(4.5 + min(4.0, (metrics["body_hits"] + metrics["sensory_hits"]) * 0.2) - min(2.5, metrics["emotion_label_hits"] * 0.18))
        specificity = _clamp(4.0 + min(4.5, concrete_density * 0.35) - min(2.0, metrics["generic_label_hits"] * 0.1))
        exposition_balance = _clamp(7.0 - min(4.0, abstract_density * 0.35))
        rhythm = _clamp(4.8 + min(2.2, metrics["sentence_length_cv"] * 5) + min(1.5, metrics["paragraph_length_cv"] * 3))
        prose_identity = _clamp(
            4.6
            + min(2.0, metrics["unique_sentence_starters"] * 0.25)
            + min(1.2, metrics["sensory_hits"] * 0.08)
            - min(1.5, metrics["emdash_hits"] * 0.08)
        )
        dialogue_pressure = _clamp(4.5 + min(3.2, metrics["dialogue_count"] * 0.35))
        cultural_requirement = str(contract.cultural_texture_policy.get("requirement", "medium"))
        cultural_texture = _clamp(4.8 + min(3.0, concrete_density * 0.25) - (0.8 if cultural_requirement == "high" and concrete_density < 5 else 0))
        style_alignment = _style_alignment(metrics, style_context)

        weighted = _weighted_average({
            "specificity": specificity,
            "rhythm_control": rhythm,
            "prose_identity": prose_identity,
            "style_alignment": style_alignment,
            "cultural_texture": cultural_texture,
            "emotional_engagement": emotion_evidence,
        }, profile)

        return LiteraryQualityScores(
            specificity=round(specificity, 2),
            rhythm_control=round(rhythm, 2),
            prose_identity=round(prose_identity, 2),
            style_alignment=round(style_alignment, 2),
            cultural_texture=round(cultural_texture, 2),
            emotional_evidence=round(emotion_evidence, 2),
            exposition_balance=round(exposition_balance, 2),
            dialogue_pressure=round(dialogue_pressure, 2),
            mode_fit=round(weighted, 2),
        )

    def _advisories(
        self,
        text: str,
        contract: LiteraryQualityContract,
        profile: WritingModeProfile,
        metrics: dict,
        scores: LiteraryQualityScores,
    ) -> list[dict]:
        advisories: list[dict] = []
        budget = contract.specificity_budget or {}
        min_details = int(budget.get("minimum_specific_details", 0) or 0)
        detail_count = metrics["concrete_hits"] + metrics["sensory_hits"] + metrics["body_hits"]
        if min_details and detail_count < min_details:
            advisories.append(self._adv(
                "specificity_budget_unmet",
                "medium",
                f"具体细节数量不足：检测到 {detail_count} 个，合同要求至少 {min_details} 个。",
                expected="补充能承担信息、压力或情绪证据的具体细节。",
                confidence=0.68,
            ))
        abstract_limit = int(budget.get("abstract_explanation_limit", 0) or 0)
        if abstract_limit and metrics["abstract_hits"] > abstract_limit:
            advisories.append(self._adv(
                "abstraction_over_budget",
                "medium",
                f"抽象解释超过预算：{metrics['abstract_hits']} 处，高于 {abstract_limit}。",
                expected="把抽象解释转为动作、对话、物件、感官或后果。",
                confidence=0.72,
            ))
        if metrics["emotion_label_hits"] >= 5 and scores.emotional_evidence < 5.5:
            advisories.append(self._adv(
                "emotional_claim_without_scene_evidence",
                "medium",
                "情绪命名多于现场证据，读者可能感觉被告知而不是被带入。",
                expected="用身体反应、动作选择、物件处理和对白潜台词承载情绪。",
                confidence=0.67,
            ))
        if scores.prose_identity < 5.2 and profile.weight("prose_identity", 1) >= 0.8:
            advisories.append(self._adv(
                "prose_identity_weak",
                "medium",
                "语言辨识度偏弱，句式和细节较通用。",
                expected="让措辞、节奏和细节更贴合当前叙述人格与场景压力。",
                confidence=0.6,
            ))
        if profile.id == "commercial_web" and metrics["abstract_hits"] >= 7 and scores.mode_fit < 6:
            advisories.append(self._adv(
                "over_literary_for_mode",
                "medium",
                "当前表达偏抽象或内省，可能削弱商业网文模式的清晰压力。",
                expected="保持风格质感，但先保证目标、阻碍、代价和钩子清楚。",
                confidence=0.58,
            ))
        if profile.id == "literary" and scores.prose_identity < 5.8 and scores.specificity < 6:
            advisories.append(self._adv(
                "too_plain_for_literary_mode",
                "medium",
                "文本对文学向模式而言偏平，细节和叙述辨识度不足。",
                expected="增加承担心理、关系或主题压力的具体细节，而不是堆叠形容词。",
                confidence=0.6,
            ))
        return advisories[:8]

    def _adv(
        self,
        atype: str,
        severity: str,
        detail: str,
        *,
        target_span: str | None = None,
        expected: str = "",
        confidence: float = 0.5,
    ) -> dict:
        source = "mode_fit" if atype in {"over_literary_for_mode", "too_plain_for_literary_mode"} else self.source_checker
        advisory = dict(make_advisory(
            atype,
            severity,
            detail,
            target_span=target_span,
            expected_behavior=expected,
            detector=source,
            confidence=confidence,
        ))
        advisory["source_checker"] = source
        return advisory

    @staticmethod
    def _summary(scores: LiteraryQualityScores, advisories: list[dict]) -> str:
        if not advisories:
            return "文学质量风险可控。"
        weakest = sorted(scores.model_dump().items(), key=lambda item: item[1])[:3]
        return "文学质量需关注：" + "、".join(f"{k}={v:.1f}" for k, v in weakest)


def _profile(value) -> WritingModeProfile:
    if isinstance(value, WritingModeProfile):
        return value
    if isinstance(value, dict):
        return WritingModeProfile(**value)
    return WritingModeProfile()


def _contract(value) -> LiteraryQualityContract:
    if isinstance(value, LiteraryQualityContract):
        return value
    if isinstance(value, dict):
        normalized = dict(value)
        for key in (
            "specificity_budget",
            "exposition_policy",
            "dialogue_policy",
            "character_voice_policy",
            "sensory_policy",
            "cultural_texture_policy",
            "motif_policy",
            "humor_policy",
            "intertextuality_policy",
            "rhythm_policy",
            "style_alignment_policy",
        ):
            normalized[key] = _as_dict(normalized.get(key))
        for key in (
            "avoid_patterns",
            "required_textual_moves",
            "forbidden_textual_moves",
            "revision_priorities",
        ):
            normalized[key] = _as_list(normalized.get(key))
        return LiteraryQualityContract(**normalized)
    return LiteraryQualityContract()


def _as_dict(value) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        return {"items": [str(item) for item in value if item is not None]}
    if value is None or value == "":
        return {}
    return {"summary": str(value)}


def _as_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if item is not None]
    if isinstance(value, dict):
        return [str(item) for item in value.values() if item is not None]
    if value is None or value == "":
        return []
    return [str(value)]


def _count_any(text: str, words: tuple[str, ...]) -> int:
    return sum((text or "").count(word) for word in words)


def _cv(values: list[int]) -> float:
    if len(values) < 2:
        return 0.0
    avg = mean(values)
    return round(pstdev(values) / avg, 3) if avg else 0.0


def _clamp(value: float) -> float:
    return max(0.0, min(10.0, float(value)))


def _unique_sentence_starters(sentences: list[str]) -> int:
    return len({s[:2] for s in sentences if len(s) >= 2})


def _weighted_average(scores: dict[str, float], profile: WritingModeProfile) -> float:
    total = 0.0
    weighted = 0.0
    key_map = {"emotional_engagement": "emotional_engagement"}
    for key, score in scores.items():
        weight_key = key_map.get(key, key)
        weight = profile.weight(weight_key, 1.0)
        total += weight
        weighted += score * weight
    return weighted / total if total else 0.0


def _style_alignment(metrics: dict, style_context: dict) -> float:
    embedding = style_context.get("style_embedding") if isinstance(style_context, dict) else {}
    if not isinstance(embedding, dict) or not embedding:
        return 6.0
    score = 6.0
    dialogue_ratio = _as_float(embedding.get("dialogue_ratio"), None)
    if dialogue_ratio is not None:
        observed = min(1.0, metrics["dialogue_count"] / max(metrics["paragraph_count"], 1))
        score -= min(2.0, abs(dialogue_ratio - observed) * 3)
    complexity = _as_float(embedding.get("sentence_complexity"), None)
    if complexity is not None:
        observed_complexity = min(1.0, metrics["sentence_length_cv"])
        score -= min(1.5, abs(complexity - observed_complexity) * 2)
    return _clamp(score)


def _as_float(value, default):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


_ABSTRACT_WORDS = (
    "意义", "本质", "命运", "灵魂", "复杂", "情绪", "感觉", "内心", "某种",
    "仿佛", "似乎", "象征", "代表", "体现", "说明", "意味着", "意识到",
)
_GENERIC_LABELS = (
    "美貌", "天赋", "强大", "震惊", "愤怒", "恐惧", "温柔", "冰冷", "复杂",
    "熟悉", "陌生", "重要", "特殊",
)
_CONCRETE_DETAIL_WORDS = (
    "门", "窗", "桌", "椅", "杯", "纸", "灯", "墙", "地面", "衣角", "袖口",
    "指节", "掌心", "鞋底", "灰尘", "雨水", "火光", "影子", "铃声",
)
_SENSORY_WORDS = (
    "粗糙", "刺眼", "发烫", "发凉", "潮湿", "干涩", "疼", "痒", "腥", "苦",
    "冷", "热", "暗", "亮", "响", "静",
)
_BODY_WORDS = (
    "手", "指", "掌心", "肩", "背", "眼", "喉", "呼吸", "膝", "脚", "额头",
    "指节", "牙", "唇",
)
_EMOTION_LABELS = (
    "愤怒", "恐惧", "害怕", "悲伤", "痛苦", "震惊", "绝望", "开心", "复杂",
    "不安", "紧张", "焦虑",
)
