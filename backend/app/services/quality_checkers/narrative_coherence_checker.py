"""Rule-based checks for local narrative coherence.

The checker is intentionally domain-neutral: it does not know any project
characters, props, sects, or plot arcs. It looks for reusable narrative failure
patterns that commonly slip through broad style metrics:

- mutually incompatible spatial domains compressed into one sentence;
- responsibility polarity flips, where the same event class is first narrated as
  an actor's own deed and later as framing/false accusation.
"""

from __future__ import annotations

import re

from app.models.violation import make_violation


class NarrativeCoherenceChecker:
    """Detect obvious local contradictions in generated prose."""

    def check(
        self,
        text: str,
        scene_contract: dict | None = None,
        chapter_state: dict | None = None,
    ) -> list[dict]:
        if not text:
            return []

        violations: list[dict] = []
        violations.extend(self._check_spatial_anchor_blends(text))
        violations.extend(self._check_causality_polarity_flip(text))
        return violations

    def _check_spatial_anchor_blends(self, text: str) -> list[dict]:
        violations: list[dict] = []
        for sentence in _split_sentences(text):
            domains = _matched_spatial_domains(sentence)
            if len(domains) >= 2:
                domain_names = "、".join(_SPATIAL_DOMAIN_LABELS[d] for d in domains[:3])
                violations.append(make_violation(
                    "spatial_consistency_error",
                    "high",
                    f"同一句同时使用多个互斥空间域锚点（{domain_names}），造成空间物理关系不自洽。",
                    source="deterministic",
                    target_span=_clip(sentence),
                    expected_behavior="不同时间层、地点层或记忆层的空间锚点应分句或用明确转场隔开，避免压缩成同一物理场景。",
                ))
                break
        return violations

    def _check_causality_polarity_flip(self, text: str) -> list[dict]:
        violations: list[dict] = []
        claims = _extract_responsibility_claims(text)
        active_by_event = {claim["event_type"]: claim for claim in claims if claim["polarity"] == "active_deed"}
        framed_by_event = {claim["event_type"]: claim for claim in claims if claim["polarity"] == "framed_or_accused"}
        for event_type in sorted(set(active_by_event) & set(framed_by_event)):
            active = active_by_event[event_type]
            framed = framed_by_event[event_type]
            if active["sentence"] == framed["sentence"]:
                continue
            span = f"{_clip(active['sentence'])} / {_clip(framed['sentence'])}"
            violations.append(make_violation(
                "fact_conflict",
                "high",
                f"同一{_EVENT_TYPE_LABELS[event_type]}事件出现责任极性反转：先按主动行为叙述，后又按栽赃/误指叙述。",
                source="deterministic",
                target_span=span,
                expected_behavior="若事件真相未确定，应使用“被指称/被认定/表面上”等不定表述；若已确认主动行为，后文不能再改写为栽赃或陷害。",
            ))
            break
        return violations


_SPATIAL_DOMAINS: dict[str, tuple[str, ...]] = {
    "workplace": ("办公桌", "工位", "会议桌", "电脑桌", "格子间", "茶水间"),
    "sleeping": ("枕边", "枕头", "床板", "床铺", "床榻", "被褥", "床沿"),
    "vehicle": ("车厢", "后座", "驾驶座", "车窗", "马车", "船舱", "机舱"),
    "street": ("街边", "巷口", "路灯", "人行道", "马路", "城门口"),
    "water": ("水下", "河底", "湖面", "海面", "船舷", "码头"),
}
_SPATIAL_DOMAIN_LABELS: dict[str, str] = {
    "workplace": "办公/工作",
    "sleeping": "床榻/睡眠",
    "vehicle": "交通工具",
    "street": "街道/户外",
    "water": "水域",
}
_TRANSITION_MARKERS = (
    "不对", "不是", "醒来", "睁开眼", "梦里", "梦中", "记忆里", "记得",
    "眼前", "现实", "回到", "仿佛", "像是",
)

_EVENT_TYPE_LABELS: dict[str, str] = {
    "theft": "失窃",
    "murder": "伤害/杀害",
    "betrayal": "背叛",
    "leak": "泄密",
}
_ACTIVE_EVENT_PATTERNS: dict[str, tuple[str, ...]] = {
    "theft": ("偷了", "偷走", "盗走", "窃取", "私拿"),
    "murder": ("杀了", "害死", "毒死", "刺杀", "重伤"),
    "betrayal": ("背叛", "出卖", "倒戈"),
    "leak": ("泄露", "走漏", "告密", "通风报信"),
}
_FRAMING_PATTERNS: dict[str, tuple[str, ...]] = {
    "theft": ("栽赃", "陷害", "嫁祸", "被指偷", "诬陷", "人赃俱获"),
    "murder": ("栽赃", "陷害", "嫁祸", "被指杀", "被诬害"),
    "betrayal": ("栽赃", "陷害", "嫁祸", "被指背叛", "被诬出卖"),
    "leak": ("栽赃", "陷害", "嫁祸", "被指泄露", "被诬告密"),
}


def _split_sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[。！？!?])\s*", text) if s.strip()]


def _matched_spatial_domains(sentence: str) -> list[str]:
    if any(marker in sentence for marker in _TRANSITION_MARKERS):
        return []
    return [
        domain
        for domain, anchors in _SPATIAL_DOMAINS.items()
        if any(anchor in sentence for anchor in anchors)
    ]


def _extract_responsibility_claims(text: str) -> list[dict]:
    claims: list[dict] = []
    for sentence in _split_sentences(text):
        for event_type, patterns in _ACTIVE_EVENT_PATTERNS.items():
            if any(pattern in sentence for pattern in patterns):
                claims.append({
                    "event_type": event_type,
                    "polarity": "active_deed",
                    "sentence": sentence,
                })
        for event_type, patterns in _FRAMING_PATTERNS.items():
            if any(pattern in sentence for pattern in patterns):
                claims.append({
                    "event_type": event_type,
                    "polarity": "framed_or_accused",
                    "sentence": sentence,
                })
    return claims


def _clip(text: str, limit: int = 80) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"
