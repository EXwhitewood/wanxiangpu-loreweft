from __future__ import annotations

import re

from app.models.commercial_pacing import (
    CommercialPacingContract,
    CommercialPacingReport,
    CommercialPacingScores,
)
from app.models.quality_advisory import make_advisory


class CommercialPacingChecker:
    """Deterministic commercial pacing and reader-retention diagnostics."""

    source_checker = "commercial_pacing"

    def check(
        self,
        text: str,
        contract: dict | CommercialPacingContract | None = None,
        scene_contract: dict | None = None,
    ) -> dict:
        pacing_contract = _contract(contract)
        metrics = self._metrics(text)
        scores = self._scores(text, pacing_contract, metrics)
        advisories = self._advisories(pacing_contract, metrics, scores)
        return CommercialPacingReport(
            pacing_mode_id=pacing_contract.pacing_mode_id,
            scores=scores,
            metrics=metrics,
            benchmark_targets=pacing_contract.target_metrics,
            advisories=advisories,
            summary=self._summary(scores, advisories),
        ).model_dump()

    @staticmethod
    def _metrics(text: str) -> dict:
        text = text or ""
        paragraphs = [p.strip() for p in re.split(r"\n+", text) if p.strip()]
        sentences = [s.strip() for s in re.split(r"[。！？!?；;]+", text) if s.strip()]
        first_window = text[:500]
        last_window = text[-500:] if text else ""
        length_k = max(len(text) / 1000, 1.0)
        action_hits = _count_any(text, _ACTION_WORDS)
        conflict_hits = _count_any(text, _CONFLICT_WORDS)
        pressure_hits = _count_any(text, _PRESSURE_WORDS)
        curiosity_hits = _count_any(text, _CURIOSITY_WORDS) + text.count("？") + text.count("?")
        reversal_hits = _count_any(text, _REVERSAL_WORDS)
        payoff_hits = _count_any(text, _PAYOFF_WORDS)
        agency_hits = _count_any(text, _AGENCY_WORDS)
        return {
            "length": len(text),
            "paragraph_count": len(paragraphs),
            "sentence_count": len(sentences),
            "dialogue_count": text.count("“") // 2 + text.count('"') // 2,
            "action_hits": action_hits,
            "conflict_hits": conflict_hits,
            "pressure_hits": pressure_hits,
            "curiosity_hits": curiosity_hits,
            "reversal_hits": reversal_hits,
            "payoff_hits": payoff_hits,
            "agency_hits": agency_hits,
            "action_density": round(action_hits / length_k, 2),
            "conflict_density": round(conflict_hits / length_k, 2),
            "pressure_density": round(pressure_hits / length_k, 2),
            "curiosity_density": round(curiosity_hits / length_k, 2),
            "reversal_density": round(reversal_hits / length_k, 2),
            "opening_has_hook": _has_hook(first_window),
            "ending_has_hook": _has_ending_hook(last_window),
            "ending_looks_summary": _looks_like_summary(last_window),
        }

    @staticmethod
    def _scores(
        text: str,
        contract: CommercialPacingContract,
        metrics: dict,
    ) -> CommercialPacingScores:
        opening_hook = _clamp(4.0 + (3.0 if metrics["opening_has_hook"] else 0.0) + min(2.0, metrics["curiosity_density"] * 0.3))
        event_density = _clamp(3.5 + min(4.0, metrics["action_density"] * 0.65) + min(1.5, metrics["payoff_hits"] * 0.35))
        conflict_density = _clamp(3.8 + min(4.0, metrics["conflict_density"] * 0.75) + (0.8 if contract.conflict_driver else 0))
        pressure_ramp = _clamp(4.0 + min(3.5, metrics["pressure_density"] * 0.6) + (0.8 if contract.pressure_ramp else 0))
        curiosity_engine = _clamp(4.0 + min(3.2, metrics["curiosity_density"] * 0.7) + (1.0 if contract.reader_question else 0))
        reversal_density = _clamp(3.5 + min(4.0, metrics["reversal_density"] * 1.2) + (0.8 if contract.reversal_plan else 0))
        payoff_delivery = _clamp(3.8 + min(3.5, metrics["payoff_hits"] * 0.8) + (0.7 if contract.micro_payoffs else 0))
        chapter_end_hook = _clamp(3.8 + (3.2 if metrics["ending_has_hook"] else 0) - (1.2 if metrics["ending_looks_summary"] else 0))
        protagonist_drive = _clamp(3.8 + min(3.8, metrics["agency_hits"] * 0.55) + min(1.5, metrics["action_density"] * 0.25))
        reader_retention = _clamp(
            opening_hook * 0.14
            + event_density * 0.12
            + conflict_density * 0.13
            + pressure_ramp * 0.11
            + curiosity_engine * 0.14
            + reversal_density * 0.1
            + payoff_delivery * 0.09
            + chapter_end_hook * 0.1
            + protagonist_drive * 0.07
        )
        return CommercialPacingScores(
            opening_hook=round(opening_hook, 2),
            event_density=round(event_density, 2),
            conflict_density=round(conflict_density, 2),
            pressure_ramp=round(pressure_ramp, 2),
            curiosity_engine=round(curiosity_engine, 2),
            reversal_density=round(reversal_density, 2),
            payoff_delivery=round(payoff_delivery, 2),
            chapter_end_hook=round(chapter_end_hook, 2),
            protagonist_drive=round(protagonist_drive, 2),
            reader_retention=round(reader_retention, 2),
        )

    def _advisories(
        self,
        contract: CommercialPacingContract,
        metrics: dict,
        scores: CommercialPacingScores,
    ) -> list[dict]:
        target = contract.target_metrics or {}
        advisories: list[dict] = []

        def below(metric: str, margin: float = 0.8) -> bool:
            return getattr(scores, metric) + margin < float(target.get(metric, 6.5) or 6.5)

        if below("opening_hook") or not metrics["opening_has_hook"]:
            advisories.append(self._adv(
                "weak_opening_hook",
                "high" if scores.opening_hook < 4.8 else "medium",
                "开场钩子偏弱，读者进入文本后缺少立即追问的异常、目标或危险。",
                expected="在前300-500字内给出异常、目标、阻碍、危险或具体未解问题。",
                confidence=0.72,
            ))
        if below("event_density"):
            advisories.append(self._adv(
                "low_event_density",
                "medium",
                "事件密度不足，文本更像氛围或解释，缺少可见推进。",
                expected="每个段落尽量承担动作、发现、选择、阻碍或代价之一。",
                confidence=0.68,
            ))
        if below("conflict_density"):
            advisories.append(self._adv(
                "low_conflict_density",
                "medium",
                "冲突密度不足，阻碍没有被现场化。",
                expected="让阻碍通过人物互动、时间限制、资源代价或现场变化出现。",
                confidence=0.67,
            ))
        if below("pressure_ramp"):
            advisories.append(self._adv(
                "flat_pressure_ramp",
                "medium",
                "压力曲线偏平，场景没有持续收紧。",
                expected="从目标、阻碍、发现到代价逐步升级，不要停留在同一情绪平面。",
                confidence=0.63,
            ))
        if below("curiosity_engine"):
            advisories.append(self._adv(
                "weak_curiosity_engine",
                "medium",
                "悬念驱动不足，读者缺少明确想知道的下一层问题。",
                expected="保留关键牌面，给出碎片线索，让读者追问原因、身份、代价或后果。",
                confidence=0.66,
            ))
        if below("reversal_density"):
            advisories.append(self._adv(
                "low_reversal_density",
                "low" if scores.reversal_density >= 5 else "medium",
                "认知变化或局势反转不足，章节容易显得线性。",
                expected="至少安排一次读者或角色对局势理解的变化。",
                confidence=0.6,
            ))
        if below("payoff_delivery"):
            advisories.append(self._adv(
                "missing_micro_payoff",
                "medium",
                "局部兑现不足，只有铺垫会削弱爽点。",
                expected="兑现一个小发现、小胜利、小代价或关系变化，同时保留更大的问题。",
                confidence=0.62,
            ))
        if below("chapter_end_hook") or metrics["ending_looks_summary"]:
            advisories.append(self._adv(
                "weak_chapter_end_hook",
                "high" if scores.chapter_end_hook < 4.8 else "medium",
                "结尾断章张力不足，偏总结或收束。",
                expected="结尾停在新发现、危险逼近、身份揭露、代价落下或不得不选择的瞬间。",
                confidence=0.73,
            ))
        if scores.reader_retention < float(target.get("reader_retention", 6.8) or 6.8) - 0.8:
            advisories.append(self._adv(
                "low_reader_retention",
                "high" if scores.reader_retention < 4.8 else "medium",
                "综合留读分偏低，章节可能稳定但不够抓人。",
                expected="优先强化开场钩子、现场冲突、认知变化和结尾钩子。",
                confidence=0.7,
            ))
        return advisories[:8]

    def _adv(
        self,
        atype: str,
        severity: str,
        detail: str,
        *,
        expected: str,
        confidence: float,
    ) -> dict:
        advisory = dict(make_advisory(
            atype,
            severity,
            detail,
            expected_behavior=expected,
            detector=self.source_checker,
            confidence=confidence,
        ))
        advisory["source_checker"] = self.source_checker
        return advisory

    @staticmethod
    def _summary(scores: CommercialPacingScores, advisories: list[dict]) -> str:
        if not advisories:
            return "商业节奏风险可控，读者留读驱动达到当前目标。"
        weakest = sorted(scores.model_dump().items(), key=lambda item: item[1])[:3]
        return "商业节奏需关注：" + "、".join(f"{key}={value:.1f}" for key, value in weakest)


def _contract(value) -> CommercialPacingContract:
    if isinstance(value, CommercialPacingContract):
        return value
    if isinstance(value, dict):
        normalized = dict(value)
        for key in ("reversal_plan", "micro_payoffs", "withheld_cards", "guardrails"):
            normalized[key] = _as_list(normalized.get(key))
        if not isinstance(normalized.get("target_metrics"), dict):
            normalized["target_metrics"] = {}
        return CommercialPacingContract(**normalized)
    return CommercialPacingContract()


def _as_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if item is not None and str(item).strip()]
    if isinstance(value, dict):
        return [str(item) for item in value.values() if item is not None and str(item).strip()]
    if value is None or value == "":
        return []
    return [str(value)]


def _count_any(text: str, words: tuple[str, ...]) -> int:
    return sum((text or "").count(word) for word in words)


def _clamp(value: float) -> float:
    return max(0.0, min(10.0, float(value)))


def _has_hook(text: str) -> bool:
    return bool(text) and (
        _count_any(text, _CURIOSITY_WORDS + _CONFLICT_WORDS + _PRESSURE_WORDS) >= 2
        or "？" in text
        or "?" in text
    )


def _has_ending_hook(text: str) -> bool:
    return bool(text) and (
        _count_any(text, _CURIOSITY_WORDS + _PRESSURE_WORDS + _REVERSAL_WORDS) >= 1
        or text.rstrip().endswith(("？", "?", "！", "!"))
    )


def _looks_like_summary(text: str) -> bool:
    return any(word in (text or "") for word in ("终于明白", "这意味着", "从此", "注定", "命运", "一切都", "接下来"))


_ACTION_WORDS = (
    "走", "冲", "退", "按", "抓", "推", "拉", "砸", "挡", "躲", "看", "问", "答", "笑",
    "站起", "转身", "抬手", "伸手", "打开", "合上", "握住", "逼近", "停下",
)
_CONFLICT_WORDS = (
    "拦", "挡", "拒绝", "怀疑", "质问", "威胁", "争", "怒", "冷笑", "不许", "不能",
    "背叛", "陷害", "追", "逃", "敌", "危险",
)
_PRESSURE_WORDS = (
    "必须", "来不及", "代价", "暴露", "失去", "死", "血", "痛", "逼", "压", "锁", "困",
    "只剩", "最后", "立刻", "否则",
)
_CURIOSITY_WORDS = (
    "为什么", "谁", "怎么", "哪里", "真相", "秘密", "线索", "痕迹", "奇怪", "不对", "异常",
    "疑", "谜", "藏", "发现",
)
_REVERSAL_WORDS = (
    "却", "忽然", "突然", "原来", "不是", "竟", "反而", "偏偏", "没想到", "真正", "直到",
)
_PAYOFF_WORDS = (
    "发现", "看见", "确认", "明白", "拿到", "找到", "证明", "救", "赢", "破", "打开",
)
_AGENCY_WORDS = (
    "决定", "选择", "不能等", "主动", "试探", "反击", "拒绝", "追上", "推开", "抓住", "开口",
)
