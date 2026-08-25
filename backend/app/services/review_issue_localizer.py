from __future__ import annotations

import re
from typing import Any
from app.services.skill_metric_repair_registry import (
    all_skill_metric_specs,
    get_metric_spec,
)


GLOBAL_PROSE_ISSUE_TYPES = {
    "abstraction_over_budget",
    "specificity_budget_unmet",
    "emotional_claim_without_scene_evidence",
    "prose_identity_weak",
    "low_event_density",
    "low_conflict_density",
    "flat_pressure_ramp",
    "weak_curiosity_engine",
    "missing_micro_payoff",
    "weak_opening_hook",
    "weak_chapter_end_hook",
    "low_reader_retention",
    "low_reading_drive",
    "low_scene_pressure",
    "exposition_driven_reveal",
    "missing_dialogue_pressure",
}

FINAL_ACCEPTANCE_ISSUE_TYPES = {
    "tier1_hit_count",
    "dash_per_1000",
    "dash_density",
    "ai_punctuation_artifact",
    "structure_word_cluster_count",
    "paragraph_shape_repeat_count",
    "mirrored_paragraph_opening_count",
    "uniform_sentence_streak_max",
    "abrupt_shift_count",
    "abstract_bare_count",
    "high_advisory_count",
    "high_advisories",
}
FINAL_ACCEPTANCE_ISSUE_TYPES.update(
    spec.issue_type
    for spec in all_skill_metric_specs().values()
    if spec.family not in {"system"} and spec.status != "manual_with_reason"
)

FACT_ISSUE_TYPES = {
    "fact_conflict",
    "internal_conflict",
    "identity_conflict",
    "setting_conflict",
    "spatial_conflict",
    "ownership_conflict",
    "knowledge_boundary_violation",
    "timeline_conflict",
    "temporal_conflict",
}

MISSING_CONTENT_ISSUE_TYPES = {
    "missing_must_show",
}

# 通用修复（循环 #1）：_ABSTRACT_MARKERS 引用统一词表 ABSTRACT_DETECTION_WORDS
# 根因：原 _ABSTRACT_MARKERS（23 词，偏名词/动词）与质检器 _DETAIL_ABSTRACT_WORDS
#   （24 词，偏形容词）几乎不重叠，导致质检器判违规的句子 localizer 找不到 →
#   fallback 整段 → LLM 改错位置 → 指标不改善。
# 修复：引用统一词表，确保 localizer 能定位到质检器判违规的所有句子。
# 通用性：适用于所有题材——统一词表覆盖论断性 + 描写性抽象词。
from app.services.agent_skill_validator import ABSTRACT_DETECTION_WORDS as _ABSTRACT_MARKERS
_CONFLICT_MARKERS = (
    "阻", "拒绝", "质问", "威胁", "不能", "不许", "代价", "危险", "血",
    "痛", "逼近", "暴露", "来不及", "否则", "只剩", "最后",
)
# 方案 30：_CURIOSITY_MARKERS 降级为预筛辅助——B 类悬念 metric 的判定改用 LLM。
# 锚点定位时 LLM 判定产出的 evidence 优先，此列表仅用于预筛候选段落。
_CURIOSITY_MARKERS = (
    "为什么", "谁", "怎么", "哪里", "真相", "秘密", "线索", "痕迹", "奇怪",
    "不对", "异常", "发现", "疑", "藏",
)
_PAYOFF_MARKERS = (
    "发现", "看见", "确认", "明白", "拿到", "找到", "证明", "破", "救",
    "打开", "变了", "松开", "停住",
)


def enrich_review_issue_list_locations(
    issues: list[dict],
    text: str,
) -> list[dict]:
    return [
        enrich_review_issue_locations(issue, text)
        if isinstance(issue, dict)
        else issue
        for issue in issues
    ]


def enrich_review_issue_locations(issue: dict[str, Any], text: str) -> dict[str, Any]:
    """Attach executable location evidence to global prose findings.

    Checkers sometimes emit scene-level quality findings without a concrete
    target span. FBI can only repair them reliably after review has identified a
    bounded window. This layer is deterministic and evidence-only: it does not
    discover new issues or decide whether the issue should block commit.
    """
    item = dict(issue or {})
    prose = str(text or "")
    issue_type = str(item.get("type") or item.get("violation_type") or "").strip().lower()
    if not prose or issue_type not in GLOBAL_PROSE_ISSUE_TYPES | FINAL_ACCEPTANCE_ISSUE_TYPES | FACT_ISSUE_TYPES:
        return item
    if issue_type in MISSING_CONTENT_ISSUE_TYPES:
        return _enrich_missing_content_location(item, prose)
    if str(item.get("target_span") or "").strip() and item.get("evidence_spans"):
        return item

    evidence, granularity, confidence = _locate_issue_windows(issue_type, prose, item)
    if not evidence:
        item.setdefault("needs_localization", True)
        item.setdefault("repair_granularity", "scene_window")
        item.setdefault("localization_status", "missing_evidence")
        return item

    target_span = str(item.get("target_span") or "").strip()
    if not target_span or target_span not in prose:
        item["target_span"] = evidence[0]["span"]
    existing = item.get("evidence_spans")
    if isinstance(existing, list):
        spans = [entry for entry in existing if isinstance(entry, dict)]
    else:
        spans = []
    known = {str(entry.get("span") or entry.get("text") or "") for entry in spans}
    for entry in evidence:
        if entry["span"] not in known:
            spans.append(entry)
            known.add(entry["span"])
    item["evidence_spans"] = spans[:5]
    item["repair_granularity"] = granularity
    item["location_confidence"] = confidence
    item["localization_status"] = "localized"
    item.setdefault("suggested_strategy", "patch_text")
    item.setdefault("recommended_route", "repair_text")
    item.setdefault("repair_scope", "prose_text")
    return item


def _enrich_missing_content_location(item: dict[str, Any], text: str) -> dict[str, Any]:
    """Locate an insertion anchor for a missing narrative obligation.

    Missing content has no erroneous source span to replace.  The target must
    therefore be an exact, semantically related sentence already in the draft,
    never a longest-paragraph fallback selected only because it exists.
    """
    paragraphs = _paragraphs(text)
    evidence_blob = "\n".join(
        str(item.get(key) or "")
        for key in ("evidence_span", "detail", "expected_behavior", "repair_goal")
    )

    # Reviewer quotes are the strongest insertion anchors: a quoted fragment
    # in the finding resolves to the exact existing dialogue paragraph.
    for phrase in _quoted_phrases(evidence_blob):
        if phrase and phrase in text:
            anchor = _paragraph_anchor_for_phrase(text, phrase, paragraphs)
            return _apply_missing_content_anchor(item, anchor, text, confidence=0.94)

    # Prefer the reviewer's evidence span over target_span.  A target produced
    # by the old generic fallback is explicitly rejected when its confidence is
    # below the executable threshold or its role is a generated scene window.
    raw_evidence = str(item.get("evidence_span") or "").strip()
    evidence_anchor = _existing_anchor_fragment(raw_evidence, text)
    if evidence_anchor:
        anchor = _paragraph_anchor_for_phrase(text, evidence_anchor, paragraphs)
        return _apply_missing_content_anchor(item, anchor, text, confidence=0.88)

    existing_target = str(item.get("target_span") or "").strip()
    location_confidence = item.get("location_confidence")
    evidence_spans = item.get("evidence_spans") or []
    generated_roles = {
        str(entry.get("role") or "").lower()
        for entry in evidence_spans
        if isinstance(entry, dict)
    }
    fallback_target = bool(
        isinstance(location_confidence, (int, float))
        and float(location_confidence) < 0.6
    ) or bool(generated_roles & {"scene_window", "generated_window"})
    target_anchor = "" if fallback_target else _existing_anchor_fragment(existing_target, text)
    if target_anchor:
        anchor = _paragraph_anchor_for_phrase(text, target_anchor, paragraphs)
        return _apply_missing_content_anchor(item, anchor, text, confidence=0.82)

    # Keep a bounded read window for human/LLM context, but do not promote it to
    # target_span.  Workbench repair must fail closed until an exact anchor is
    # available.
    start = max(0, len(text) - 650)
    context_windows = _window_evidence(text, start, len(text), "generated_window")
    item["target_span"] = ""
    item["evidence_spans"] = context_windows
    item["repair_granularity"] = "scene_window"
    item["location_confidence"] = 0.45 if context_windows else 0.0
    item["localization_status"] = "missing_anchor"
    item["needs_localization"] = True
    item.setdefault("suggested_strategy", "patch_text")
    item.setdefault("recommended_route", "repair_text")
    item.setdefault("repair_scope", "prose_text")
    return item


def _apply_missing_content_anchor(
    item: dict[str, Any],
    anchor: str,
    text: str,
    *,
    confidence: float,
) -> dict[str, Any]:
    anchor = str(anchor or "").strip()
    if not anchor or anchor not in text:
        return _enrich_missing_content_location(
            {**item, "target_span": "", "evidence_span": "", "evidence_spans": []},
            text,
        )
    start = text.find(anchor)
    item["target_span"] = anchor
    item["evidence_spans"] = [{
        "span": anchor,
        "text": anchor,
        "start": start,
        "end": start + len(anchor),
        "role": "insertion_anchor",
        "score": 3,
    }]
    item["repair_granularity"] = "insertion_anchor"
    item["location_confidence"] = confidence
    item["localization_status"] = "localized"
    item["needs_localization"] = False
    item.setdefault("suggested_strategy", "patch_text")
    item.setdefault("recommended_route", "repair_text")
    item.setdefault("repair_scope", "prose_text")
    return item


def _paragraph_anchor_for_phrase(text: str, phrase: str, paragraphs: list[dict]) -> str:
    index = text.find(phrase)
    if index < 0:
        return ""
    for paragraph in paragraphs:
        start = int(paragraph.get("start") or 0)
        end = int(paragraph.get("end") or start)
        span = str(paragraph.get("span") or "").strip()
        if start <= index < end and span and span in text and phrase in span:
            return span
    return phrase


def _existing_anchor_fragment(value: str, text: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if raw in text:
        return raw
    for fragment in re.split(r"\.{3,}|…+", raw):
        fragment = fragment.strip(" \t\r\n，。；;：:")
        if len(fragment) >= 4 and fragment in text:
            return fragment
    return ""


def _locate_issue_windows(issue_type: str, text: str, issue: dict[str, Any] | None = None) -> tuple[list[dict], str, float]:
    issue = issue or {}
    paragraphs = _paragraphs(text)
    sentences = _sentences(text)
    spec = get_metric_spec(issue.get("metric") or issue_type)
    if issue_type in FACT_ISSUE_TYPES:
        evidence = _fact_issue_windows(issue, text, paragraphs)
        if evidence:
            return evidence[:4], "fact_evidence_window", 0.86
        return _paragraph_fallback(paragraphs, "fact_context_window")

    if issue_type in {"dash_per_1000", "dash_density", "ai_punctuation_artifact"}:
        hits = _sentence_hits(sentences, ("--", "——", "—"))
        if hits:
            return hits[:5], "sentence_cluster", 0.86
        return _paragraph_fallback(paragraphs, "dash_window")

    if issue_type in {"tier1_hit_count", "high_advisory_count", "high_advisories"}:
        hits = _sentence_hits(sentences, _ABSTRACT_MARKERS)
        if hits:
            return hits[:5], "sentence_cluster", 0.68
        return _paragraph_fallback(paragraphs, "ai_flavor_window")

    if issue_type == "structure_word_cluster_count":
        hits = _sentence_hits(sentences, (
            "这说明", "这意味着", "因此", "然而", "于是", "然后",
            "与此同时", "事实上", "显然", "换句话说", "某种意义上",
        ))
        if hits:
            return hits[:5], "sentence_cluster", 0.76
        return _paragraph_fallback(paragraphs, "structure_word_window")

    if spec is not None and spec.markers:
        hits = _sentence_hits(sentences, tuple(spec.markers))
        if hits:
            return hits[:5], spec.localization_strategy or "sentence_cluster", 0.76
    if spec is not None and spec.localization_strategy == "ending_window":
        start = max(0, len(text) - 650)
        return _window_evidence(text, start, len(text), "ending_window"), "ending_window", 0.62
    if spec is not None and spec.localization_strategy == "opening_window":
        return _window_evidence(text, 0, min(len(text), 500), "opening_window"), "opening_window", 0.62
    if spec is not None and spec.localization_strategy == "voice_window":
        return _paragraph_fallback(paragraphs, "voice_window")

    if spec is not None and spec.localization_strategy == "sentence_streak":
        evidence = _uniform_sentence_streak_windows(sentences)
        if evidence:
            return evidence[:4], "sentence_streak", 0.75
        return _paragraph_fallback(paragraphs, "sentence_rhythm_window")

    if spec is not None and spec.localization_strategy == "paragraph_cluster":
        return _paragraph_fallback(paragraphs, "paragraph_cluster")

    if spec is not None and spec.localization_strategy == "transition_window":
        evidence = _abrupt_shift_windows(sentences)
        if evidence:
            return evidence[:5], "transition_window", 0.7
        return _paragraph_fallback(paragraphs, "transition_window")

    if issue_type == "paragraph_shape_repeat_count":
        evidence = _paragraph_shape_repeat_windows(paragraphs)
        if evidence:
            return evidence[:5], "paragraph_cluster", 0.74
        return _paragraph_fallback(paragraphs, "paragraph_shape_window")

    if issue_type == "mirrored_paragraph_opening_count":
        evidence = _mirrored_paragraph_opening_windows(paragraphs)
        if evidence:
            return evidence[:5], "paragraph_opening_cluster", 0.76
        return _paragraph_fallback(paragraphs, "paragraph_opening_window")

    if issue_type == "uniform_sentence_streak_max":
        evidence = _uniform_sentence_streak_windows(sentences)
        if evidence:
            return evidence[:4], "sentence_streak", 0.75
        return _paragraph_fallback(paragraphs, "sentence_rhythm_window")

    if issue_type == "abrupt_shift_count":
        evidence = _abrupt_shift_windows(sentences)
        if evidence:
            return evidence[:5], "transition_window", 0.7
        return _paragraph_fallback(paragraphs, "rhythm_shift_window")

    if issue_type == "abstract_bare_count":
        hits = _sentence_hits(sentences, _ABSTRACT_MARKERS)
        if hits:
            return hits[:5], "sentence_cluster", 0.78
        return _paragraph_fallback(paragraphs, "specificity_window")

    if issue_type == "abstraction_over_budget":
        hits = _sentence_hits(sentences, _ABSTRACT_MARKERS)
        if hits:
            return hits[:4], "sentence_cluster", 0.78
        return _paragraph_fallback(paragraphs, "abstraction_candidate")

    if issue_type == "weak_opening_hook":
        return _window_evidence(text, 0, min(len(text), 500), "opening_window"), "opening_window", 0.74

    if issue_type in {"weak_chapter_end_hook", "missing_micro_payoff"}:
        start = max(0, len(text) - 650)
        return _window_evidence(text, start, len(text), "ending_window"), "ending_window", 0.72

    if issue_type in {"low_conflict_density", "low_scene_pressure", "flat_pressure_ramp"}:
        ranked = _low_signal_paragraphs(paragraphs, _CONFLICT_MARKERS)
        if ranked:
            return ranked[:3], "paragraph_cluster", 0.66
        return _paragraph_fallback(paragraphs, "conflict_window")

    if issue_type in {"weak_curiosity_engine", "low_reader_retention", "low_reading_drive"}:
        tail_start = max(0, len(text) - 750)
        evidence = []
        evidence.extend(_window_evidence(text, 0, min(len(text), 500), "opening_window"))
        evidence.extend(_window_evidence(text, tail_start, len(text), "ending_window"))
        if not any(_contains_any(item["span"], _CURIOSITY_MARKERS) for item in evidence):
            evidence.extend(_low_signal_paragraphs(paragraphs, _CURIOSITY_MARKERS)[:1])
        return evidence[:3], "scene_arc_window", 0.62

    if issue_type in {"low_event_density", "specificity_budget_unmet", "prose_identity_weak"}:
        return _paragraph_fallback(paragraphs, "event_detail_window")

    if issue_type in {"emotional_claim_without_scene_evidence", "exposition_driven_reveal"}:
        hits = _sentence_hits(sentences, (*_ABSTRACT_MARKERS, "觉得", "认为", "解释", "回忆"))
        if hits:
            return hits[:3], "sentence_cluster", 0.7
        return _paragraph_fallback(paragraphs, "exposition_window")

    if issue_type == "missing_dialogue_pressure":
        return _paragraph_fallback(paragraphs, "interaction_window")

    return _paragraph_fallback(paragraphs, "scene_window")


def _fact_issue_windows(issue: dict[str, Any], text: str, paragraphs: list[dict]) -> list[dict]:
    values: list[str] = []
    evidence = issue.get("evidence") if isinstance(issue.get("evidence"), dict) else {}
    for key in (
        "target_span",
        "evidence_span",
        "current_claim",
        "text_claim",
        "actual",
        "wrong",
    ):
        raw = issue.get(key)
        if raw:
            values.append(str(raw))
        raw = evidence.get(key)
        if raw:
            values.append(str(raw))
    detail_blob = "\n".join(
        str(issue.get(key) or "")
        for key in ("detail", "reason", "expected_behavior", "repair_goal", "target_span")
    )
    values.extend(_quoted_phrases(detail_blob))
    values.extend(_fact_keywords(detail_blob))

    matches: list[dict] = []
    seen: set[str] = set()
    for raw in values:
        phrase = _clean_fact_phrase(raw)
        if len(phrase) < 2 or phrase in seen:
            continue
        seen.add(phrase)
        index = text.find(phrase)
        if index < 0:
            continue
        matches.append({
            "span": phrase,
            "text": phrase,
            "start": index,
            "end": index + len(phrase),
            "role": "conflicting_claim",
            "score": 3,
        })
    if matches:
        return matches

    compact_detail = re.sub(r"\s+", "", detail_blob)
    bridge_markers = ("森林", "林子", "树林", "山谷", "洞", "回廊", "药径")
    if any(marker in compact_detail for marker in bridge_markers):
        for paragraph in paragraphs:
            span = str(paragraph.get("span") or "")
            if any(marker in span for marker in bridge_markers):
                return [{**paragraph, "role": "fact_bridge_anchor", "score": 1}]
    time_markers = ("天已经亮", "天亮", "黎明", "夜半", "深夜", "晨光")
    if any(marker in compact_detail for marker in time_markers):
        for paragraph in paragraphs:
            span = str(paragraph.get("span") or "")
            if any(marker in span for marker in time_markers):
                return [{**paragraph, "role": "time_anchor_context", "score": 1}]
    return []


def _quoted_phrases(text: str) -> list[str]:
    phrases: list[str] = []
    for pattern in (
        r"'([^']{2,160})'",
        r'"([^"]{2,160})"',
        r"“([^”]{2,160})”",
        r"‘([^’]{2,160})’",
    ):
        for match in re.finditer(pattern, text or ""):
            phrase = match.group(1).strip()
            if phrase and phrase not in phrases:
                phrases.append(phrase)
    return phrases[:12]


def _fact_keywords(text: str) -> list[str]:
    values: list[str] = []
    for marker in ("天已经亮了", "外面的天已经亮了", "原始森林", "后山回廊", "森林", "林子"):
        if marker in str(text or "") and marker not in values:
            values.append(marker)
    return values


def _clean_fact_phrase(value: str) -> str:
    phrase = re.sub(r"\s+", " ", str(value or "")).strip(" '\"“”‘’`，。；;：:")
    if len(phrase) > 160:
        phrase = phrase[:160]
    return phrase


def _paragraphs(text: str) -> list[dict]:
    items: list[dict] = []
    for match in re.finditer(r"\S(?:.*?)(?=\n\s*\n|\Z)", text or "", flags=re.S):
        span = match.group(0).strip()
        if span:
            items.append({"span": _clip(span, 420), "start": match.start(), "end": match.end()})
    if not items and text.strip():
        items.append({"span": _clip(text.strip(), 420), "start": 0, "end": len(text)})
    return items


def _sentences(text: str) -> list[dict]:
    items: list[dict] = []
    cn_pattern = re.compile(r"[^。！？!?；;\n]{8,220}[。！？!?；;]?", flags=re.S)
    for match in cn_pattern.finditer(text or ""):
        span = re.sub(r"\s+", " ", match.group(0)).strip()
        if span:
            items.append({"span": _clip(span, 260), "start": match.start(), "end": match.end()})
    if items:
        return items
    pattern = re.compile(r"[^。！？!?；;\n]{8,220}[。！？!?；;]?", flags=re.S)
    for match in pattern.finditer(text or ""):
        span = re.sub(r"\s+", " ", match.group(0)).strip()
        if span:
            items.append({"span": _clip(span, 260), "start": match.start(), "end": match.end()})
    return items


def _sentence_hits(sentences: list[dict], markers: tuple[str, ...]) -> list[dict]:
    hits: list[dict] = []
    for item in sentences:
        score = sum(str(item["span"]).count(marker) for marker in markers)
        if score:
            hits.append({**item, "role": "evidence_sentence", "score": score})
    return sorted(hits, key=lambda item: (-int(item.get("score") or 0), item["start"]))


def _low_signal_paragraphs(paragraphs: list[dict], markers: tuple[str, ...]) -> list[dict]:
    ranked: list[dict] = []
    for item in paragraphs:
        span = str(item.get("span") or "")
        if len(span) < 60:
            continue
        marker_hits = sum(span.count(marker) for marker in markers)
        dialogue_hits = span.count("“") + span.count('"')
        score = marker_hits + dialogue_hits
        ranked.append({**item, "role": "repair_window", "score": score})
    return sorted(ranked, key=lambda item: (int(item.get("score") or 0), -len(str(item.get("span") or ""))))


def _paragraph_fallback(paragraphs: list[dict], role: str) -> tuple[list[dict], str, float]:
    if not paragraphs:
        return [], "scene_window", 0.0
    ranked = sorted(paragraphs, key=lambda item: len(str(item.get("span") or "")), reverse=True)
    evidence = [{**item, "role": role, "score": 0} for item in ranked[:3]]
    return evidence, "paragraph_cluster", 0.48


def _paragraph_shape_repeat_windows(paragraphs: list[dict]) -> list[dict]:
    evidence: list[dict] = []
    previous: dict | None = None
    for item in paragraphs:
        if previous is None:
            previous = item
            continue
        if _paragraph_shape(previous["span"]) == _paragraph_shape(item["span"]):
            start = int(previous.get("start") or 0)
            end = int(item.get("end") or start)
            span = f"{previous['span']}\n\n{item['span']}"
            evidence.append({
                "span": _clip(span, 620),
                "start": start,
                "end": end,
                "role": "paragraph_shape_repeat_window",
                "score": 1,
            })
        previous = item
    return evidence


def _mirrored_paragraph_opening_windows(paragraphs: list[dict]) -> list[dict]:
    groups: dict[str, list[dict]] = {}
    for item in paragraphs:
        opening = _opening_key(str(item.get("span") or ""))
        if not opening:
            continue
        groups.setdefault(opening, []).append(item)
    evidence: list[dict] = []
    for opening, items in groups.items():
        if len(items) < 2:
            continue
        first = items[0]
        second = items[1]
        evidence.append({
            "span": _clip(f"{first['span']}\n\n{second['span']}", 620),
            "start": int(first.get("start") or 0),
            "end": int(second.get("end") or first.get("end") or 0),
            "role": "mirrored_paragraph_opening",
            "opening": opening,
            "score": len(items),
        })
    return sorted(evidence, key=lambda item: (-int(item.get("score") or 0), int(item.get("start") or 0)))


def _uniform_sentence_streak_windows(sentences: list[dict]) -> list[dict]:
    evidence: list[dict] = []
    streak: list[dict] = []
    current_band: str | None = None
    for sentence in sentences:
        band = _length_band(str(sentence.get("span") or ""))
        if band == current_band:
            streak.append(sentence)
        else:
            if len(streak) >= 3:
                evidence.append(_sentence_window(streak, "uniform_sentence_streak"))
            streak = [sentence]
            current_band = band
    if len(streak) >= 3:
        evidence.append(_sentence_window(streak, "uniform_sentence_streak"))
    return evidence


def _abrupt_shift_windows(sentences: list[dict]) -> list[dict]:
    evidence: list[dict] = []
    previous: dict | None = None
    for sentence in sentences:
        if previous is None:
            previous = sentence
            continue
        if abs(_band_index(previous["span"]) - _band_index(sentence["span"])) >= 2:
            evidence.append(_sentence_window([previous, sentence], "abrupt_rhythm_shift"))
        previous = sentence
    return evidence


def _sentence_window(sentences: list[dict], role: str) -> dict:
    start = int(sentences[0].get("start") or 0)
    end = int(sentences[-1].get("end") or start)
    return {
        "span": _clip("".join(str(item.get("span") or "") for item in sentences), 520),
        "start": start,
        "end": end,
        "role": role,
        "score": len(sentences),
    }


def _paragraph_shape(text: str) -> str:
    stripped = re.sub(r"\s+", "", str(text or ""))
    dialogue = "d" if ("\"" in stripped or "“" in stripped or "”" in stripped) else "n"
    return f"{_length_band(stripped)}:{dialogue}:{stripped.count('，') + stripped.count(',')}"


def _opening_key(text: str) -> str:
    stripped = re.sub(r"^[\s\"'“”‘’]+", "", str(text or ""))
    stripped = re.sub(r"\s+", "", stripped)
    if len(stripped) < 2:
        return ""
    return stripped[: min(4, len(stripped))]


def _length_band(text: str) -> str:
    length = len(re.sub(r"\s+", "", str(text or "")))
    if length < 18:
        return "short"
    if length < 45:
        return "medium"
    if length < 90:
        return "long"
    return "dense"


def _band_index(text: str) -> int:
    return {"short": 0, "medium": 1, "long": 2, "dense": 3}.get(_length_band(text), 1)


def _window_evidence(text: str, start: int, end: int, role: str) -> list[dict]:
    span = str(text or "")[start:end].strip()
    if not span:
        return []
    return [{
        "span": _clip(span, 520),
        "start": start,
        "end": min(end, len(text)),
        "role": role,
        "score": 0,
    }]


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in str(text or "") for marker in markers)


def _clip(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"
