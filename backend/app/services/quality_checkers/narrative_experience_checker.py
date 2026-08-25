from __future__ import annotations

import re

from app.models.narrative_experience import (
    NarrativeExperienceContract,
    NarrativeExperienceReport,
    NarrativeExperienceScores,
)
from app.models.quality_advisory import make_advisory
from app.models.writing_mode import ModeFitReport, WritingModeProfile


class NarrativeExperienceChecker:
    """Mode-aware narrative experience diagnostics."""

    source_checker = "narrative_experience"

    def check(
        self,
        text: str,
        contract: dict | NarrativeExperienceContract | None = None,
        writing_mode_profile: dict | WritingModeProfile | None = None,
        scene_contract: dict | None = None,
    ) -> dict:
        profile = _profile(writing_mode_profile)
        exp_contract = _contract(contract)
        metrics = self._metrics(text)
        scores = self._scores(text, exp_contract, metrics)
        advisories = self._advisories(text, exp_contract, profile, metrics, scores)
        report = NarrativeExperienceReport(
            writing_mode_id=profile.id,
            scores=scores,
            advisories=advisories,
            metrics=metrics,
            summary=self._summary(scores, advisories),
        ).model_dump()
        report["mode_fit"] = self.mode_fit(scores, profile).model_dump()
        return report

    def mode_fit(
        self,
        scores: NarrativeExperienceScores,
        profile: WritingModeProfile,
    ) -> ModeFitReport:
        metric_scores = scores.model_dump()
        total_weight = 0.0
        weighted = 0.0
        mismatches = []
        for metric, score in metric_scores.items():
            weight = profile.weight(metric, 0.0)
            if weight <= 0:
                continue
            total_weight += weight
            weighted += score * weight
            if weight >= 1.2 and score < 5.5:
                mismatches.append({
                    "metric": metric,
                    "score": round(score, 2),
                    "weight": round(weight, 2),
                    "detail": "高权重体验指标低于模式要求。",
                })
        fit = (
            weighted / total_weight
            if total_weight
            else sum(metric_scores.values()) / max(len(metric_scores), 1)
        )
        return ModeFitReport(
            writing_mode_id=profile.id,
            fit_score=round(fit, 2),
            weighted_score=round(fit, 2),
            mismatches=mismatches[:6],
            metric_scores={k: round(v, 2) for k, v in metric_scores.items()},
        )

    @staticmethod
    def _metrics(text: str) -> dict:
        paragraphs = [p.strip() for p in re.split(r"\n+", text or "") if p.strip()]
        sentences = [s.strip() for s in re.split(r"[。！？!?]+", text or "") if s.strip()]
        dialogue_count = (text or "").count("“") // 2 + (text or "").count('"') // 2
        action_hits = _count_any(text, _ACTION_WORDS)
        pressure_hits = _count_any(text, _PRESSURE_WORDS)
        question_hits = (text or "").count("？") + (text or "").count("?")
        expository_hits = _count_any(text, _EXPOSITION_WORDS)
        sensory_hits = _count_any(text, _SENSORY_WORDS)
        concrete_hits = _count_any(text, _CONCRETE_WORDS)
        return {
            "length": len(text or ""),
            "paragraph_count": len(paragraphs),
            "sentence_count": len(sentences),
            "dialogue_count": dialogue_count,
            "action_hits": action_hits,
            "pressure_hits": pressure_hits,
            "question_hits": question_hits,
            "expository_hits": expository_hits,
            "sensory_hits": sensory_hits,
            "concrete_hits": concrete_hits,
            "ending_summary": _looks_like_summary(paragraphs[-1] if paragraphs else ""),
        }

    @staticmethod
    def _scores(text: str, contract: NarrativeExperienceContract, metrics: dict) -> NarrativeExperienceScores:
        length = max(len(text or ""), 1)
        action_density = metrics["action_hits"] / max(length / 1000, 1)
        pressure_density = metrics["pressure_hits"] / max(length / 1000, 1)
        exposition_density = metrics["expository_hits"] / max(length / 1000, 1)
        concrete_density = (metrics["sensory_hits"] + metrics["concrete_hits"]) / max(length / 1000, 1)

        dramatic_pressure = _clamp(4.5 + min(3.0, pressure_density * 0.6) + (1.0 if contract.obstacle else 0))
        protagonist_agency = _clamp(4.0 + min(3.5, action_density * 0.5) + (1.0 if contract.decision_point else 0))
        scene_embodiment = _clamp(4.0 + min(4.0, concrete_density * 0.45))
        clarity = _clamp(7.2 - min(3.0, exposition_density * 0.35) + (0.5 if metrics["paragraph_count"] >= 2 else 0))
        information_design = _clamp(5.0 + min(2.0, metrics["dialogue_count"] * 0.2) - min(2.2, exposition_density * 0.25))
        curiosity_gap = _clamp(4.8 + min(2.0, metrics["question_hits"] * 0.8) + (1.2 if contract.curiosity_question else 0))
        dialogue_vitality = _clamp(4.5 + min(3.0, metrics["dialogue_count"] * 0.35))
        emotional_engagement = _clamp(4.5 + min(3.0, (metrics["sensory_hits"] + metrics["action_hits"]) * 0.08))
        conflict_visibility = _clamp((dramatic_pressure + pressure_density + (1 if contract.obstacle else 0)) / 1.4)
        reading_drive = _clamp(
            (dramatic_pressure * 0.25)
            + (protagonist_agency * 0.2)
            + (curiosity_gap * 0.2)
            + (scene_embodiment * 0.15)
            + (clarity * 0.2)
            - (1.0 if metrics["ending_summary"] else 0.0)
        )
        return NarrativeExperienceScores(
            reading_drive=round(reading_drive, 2),
            clarity=round(clarity, 2),
            dramatic_pressure=round(dramatic_pressure, 2),
            protagonist_agency=round(protagonist_agency, 2),
            conflict_visibility=round(conflict_visibility, 2),
            information_design=round(information_design, 2),
            curiosity_gap=round(curiosity_gap, 2),
            emotional_engagement=round(emotional_engagement, 2),
            scene_embodiment=round(scene_embodiment, 2),
            dialogue_vitality=round(dialogue_vitality, 2),
        )

    def _advisories(
        self,
        text: str,
        contract: NarrativeExperienceContract,
        profile: WritingModeProfile,
        metrics: dict,
        scores: NarrativeExperienceScores,
    ) -> list[dict]:
        advisories: list[dict] = []
        if scores.reading_drive < 5.5 and profile.weight("reading_drive", 1) >= 1.0:
            advisories.append(self._adv(
                "low_reading_drive",
                "high" if scores.reading_drive < 4.5 else "medium",
                "场景继续阅读驱动力偏弱，压力、选择或结尾钩子不足。",
                expected="强化可见压力、角色选择和出场钩子。",
                confidence=0.68,
            ))
        if scores.dramatic_pressure < 5.2 and (contract.obstacle or profile.weight("dramatic_pressure", 1) >= 1.2):
            advisories.append(self._adv(
                "low_scene_pressure",
                "medium",
                "场景压力不够可见，冲突更像被说明而不是正在发生。",
                expected="让阻碍通过人物互动、时间限制、资源代价或现场变化呈现。",
                confidence=0.66,
            ))
        if scores.protagonist_agency < 5.0 and contract.agency_requirement:
            advisories.append(self._adv(
                "passive_protagonist",
                "medium",
                "核心角色主动性不足，缺少可观察选择或行动。",
                expected=contract.agency_requirement,
                confidence=0.64,
            ))
        if metrics["expository_hits"] >= 5 and scores.information_design < 5.5:
            advisories.append(self._adv(
                "exposition_driven_reveal",
                "medium",
                "信息主要依赖解释性叙述释放，现场承载不足。",
                expected="将信息拆给动作、对话、物件、观察和后果。",
                confidence=0.7,
            ))
        if contract.dialogue_pressure == "high" and metrics["dialogue_count"] < 2:
            advisories.append(self._adv(
                "missing_dialogue_pressure",
                "medium",
                "合同要求互动压力，但正文对白或互动承压不足。",
                expected="用少量高压对白、沉默、打断或旁人反应承载关系压力。",
                confidence=0.62,
            ))
        if metrics["ending_summary"] and profile.weight("reading_drive", 1) >= 1.0:
            advisories.append(self._adv(
                "weak_hook_out",
                "medium",
                "结尾偏总结式，出场钩子不够具体。",
                target_span="结尾段落",
                expected="结尾停在行动、发现、代价、关系变化或明确疑问上。",
                confidence=0.72,
            ))

        mode_fit = self.mode_fit(scores, profile)
        if mode_fit.fit_score < 5.5:
            advisories.append(self._adv(
                "mode_mismatch",
                "high" if mode_fit.fit_score < 4.8 else "medium",
                f"文本体验与当前写作模式「{profile.label}」匹配度偏低。",
                expected="优先修复该模式高权重指标，而不是套用单一文学评分标准。",
                confidence=0.65,
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
        source = "mode_fit" if atype == "mode_mismatch" else self.source_checker
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
    def _summary(scores: NarrativeExperienceScores, advisories: list[dict]) -> str:
        if not advisories:
            return "叙事体验风险可控。"
        weakest = sorted(scores.model_dump().items(), key=lambda item: item[1])[:3]
        return "叙事体验需关注：" + "、".join(f"{k}={v:.1f}" for k, v in weakest)


def _profile(value) -> WritingModeProfile:
    if isinstance(value, WritingModeProfile):
        return value
    if isinstance(value, dict):
        return WritingModeProfile(**value)
    return WritingModeProfile()


def _contract(value) -> NarrativeExperienceContract:
    if isinstance(value, NarrativeExperienceContract):
        return value
    if isinstance(value, dict):
        normalized = dict(value)
        for key in (
            "reveal_policy",
            "misdirection_policy",
            "scene_embodiment",
            "success_metrics",
        ):
            normalized[key] = _as_dict(normalized.get(key))
        for key in (
            "information_delta",
            "withheld_information",
            "sensory_anchor",
            "prohibited_experience",
        ):
            normalized[key] = _as_list(normalized.get(key))
        return NarrativeExperienceContract(**normalized)
    return NarrativeExperienceContract()


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


def _clamp(value: float) -> float:
    return max(0.0, min(10.0, float(value)))


def _looks_like_summary(paragraph: str) -> bool:
    if not paragraph:
        return False
    return any(word in paragraph for word in ("这一刻", "从此", "注定", "终于明白", "这意味着", "命运"))


_ACTION_WORDS = (
    "走", "站", "抬", "按", "推", "拉", "拿", "放", "看", "听", "问", "答",
    "转身", "停下", "伸手", "退后", "靠近", "打开", "合上", "握住", "松开",
)
_PRESSURE_WORDS = (
    "逼", "拦", "挡", "催", "危险", "代价", "不能", "必须", "来不及", "质问",
    "沉默", "拒绝", "怀疑", "暴露", "失去", "疼", "冷", "响",
)
_EXPOSITION_WORDS = (
    "说明", "解释", "意味着", "显然", "事实上", "其实", "换言之", "因为", "所以",
    "原来", "意识到", "明白", "知道了",
)
_SENSORY_WORDS = (
    "冷", "热", "疼", "痒", "潮", "湿", "亮", "暗", "响", "静", "气味", "血腥",
    "粗糙", "柔软", "刺眼", "发烫", "发凉",
)
_CONCRETE_WORDS = (
    "门", "窗", "桌", "椅", "纸", "杯", "衣", "袖", "手指", "掌心", "眼", "肩",
    "墙", "地面", "灯", "影", "尘", "雨", "风", "火", "水",
)
