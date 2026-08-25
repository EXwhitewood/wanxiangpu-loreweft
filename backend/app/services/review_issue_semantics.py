"""Semantic normalization for review issues.

Review/checker output is sometimes free-form prose instead of a stable issue
type. This module turns those loose diagnostics into a routable rule id plus
extracts target spans and expected behavior when they are embedded in text.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any
from app.services.skill_metric_repair_registry import (
    all_skill_metric_specs,
    get_metric_spec,
    infer_metric_from_detail,
)

try:
    from app.models.violation import STRATEGY_MAP
except Exception:  # pragma: no cover - defensive import guard
    STRATEGY_MAP = {}


UNKNOWN_TYPES = {
    "",
    "unknown",
    "violation",
    "review_issue",
    "unclassified",
    "unclassified_review_issue",
    "semantic_quality_error",
}

TEMPORAL_DIRECT_REPAIR_RE = re.compile(
    r"([零〇一二两三四五六七八九十百\d]+(?:个)?[日天]后|[子丑寅卯辰巳午未申酉戌亥]时(?:[一二三四五六七八九十半零〇\d]+刻)?|"
    r"清晨|晨间|晨光|黎明|拂晓|上午|正午|中午|午后|下午|黄昏|傍晚|暮色|夜里|夜色|深夜|午夜|月光|星光|三更|日头|天色)"
)

DIRECT_TEXT_SCOPES = {
    "fact_conflict",
    "identity_conflict",
    "setting_conflict",
    "spatial_conflict",
    "spatial_consistency_error",
    "ownership_conflict",
    "knowledge_boundary_violation",
    "pov_conflict",
    "timeline_conflict",
    "temporal_conflict",
    "forbidden_assertion_triggered",
    "forbidden_recap_violation",
    "forbidden_triggered",
    "required_ambiguity_broken",
    "missing_must_show",
    "ending_state_not_reached",
    "scene_too_long",
    "scene_too_short",
    "event_repeated",
    "repetition_artifact",
    "narration_explanation_artifact",
    "explanatory_punctuation_artifact",
    "chapter_reference",
    "meta_chapter_reference",
    "meta_narrative_reference",
}

SYSTEM_TYPES = {
    "critic_parse_error",
    "consistency_check_unavailable",
    "fcip_check_unavailable",
    "scene_contract_compiler_unavailable",
    "proposition_layer_unavailable",
    "proposition_extractor_unavailable",
}

TEXT_REPAIRABLE_TYPES = {
    *DIRECT_TEXT_SCOPES,
    "ai_punctuation_artifact",
    "dash_per_1000",
    "dash_density",
    "tier1_hit_count",
    "sentence_shell_count",
    "structure_word_cluster_count",
    "paragraph_shape_repeat_count",
    "mirrored_paragraph_opening_count",
    "uniform_sentence_streak_max",
    "clue_provenance_error",
    "clue_provenance_error_proposition",
    "unprovenanced_clue",
    "abstraction_over_budget",
    "low_conflict_density",
    "weak_curiosity_engine",
    "missing_micro_payoff",
    "flat_pressure_ramp",
    "chapter_reference",
    "meta_chapter_reference",
    "meta_narrative_reference",
}
TEXT_REPAIRABLE_TYPES.update(
    spec.issue_type
    for spec in all_skill_metric_specs().values()
    if spec.family not in {"system"} and spec.status != "manual_with_reason"
)

PLANNING_CONFLICT_TYPES = {
    "scene_contract_compile_blocked",
    "contract_internal_conflict",
    "planning_conflict",
    "outline_conflict",
}


TYPE_ALIASES = {
    # Scene critic / legacy canon names.
    "canon_timeline_confusion": "timeline_conflict",
    "timeline_confusion": "timeline_conflict",
    "temporal_layer_confusion": "temporal_conflict",
    "canon_temporal_confusion": "temporal_conflict",
    "canon_time_confusion": "timeline_conflict",
    "canon_chronology_confusion": "timeline_conflict",
    "time_anchor_conflict": "timeline_conflict",
    "time_anchor_mismatch": "timeline_conflict",
    "temporal_anchor_conflict": "timeline_conflict",
    "temporal_anchor_mismatch": "timeline_conflict",
    "chronology_conflict": "timeline_conflict",
    "chronology_mismatch": "timeline_conflict",
    "canon_spatial_confusion": "spatial_conflict",
    "spatial_layer_confusion": "spatial_conflict",
    "canon_identity_confusion": "identity_conflict",
    "identity_layer_confusion": "identity_conflict",
    "canon_fact_confusion": "fact_conflict",
    "canon_state_confusion": "fact_conflict",
    "fact_layer_confusion": "fact_conflict",
    "canon_causal_confusion": "causal_chain_error",
    "causal_layer_confusion": "causal_chain_error",
    "canon_knowledge_confusion": "knowledge_boundary_violation",
    "knowledge_layer_confusion": "knowledge_boundary_violation",
}


def normalize_violation_semantics(violation: dict[str, Any]) -> dict[str, Any]:
    """Return a copy enriched with stable type, evidence span, and strategy."""
    item = dict(violation or {})
    raw_type = str(item.get("type") or "").strip()
    detail = _combined_text(item)
    canonical_type = _canonical_issue_type(raw_type, detail)
    if canonical_type != raw_type:
        item["type"] = canonical_type
        item["original_type"] = raw_type or "unknown"

    target_span = _first_text(
        item.get("target_span"),
        item.get("span"),
        item.get("forbidden_item"),
        _extract_target_span(detail),
    )
    expected = _first_text(
        item.get("expected_behavior"),
        item.get("expected_text"),
        item.get("replacement"),
        _extract_expected_behavior(detail),
    )

    original_type = str(item.get("type") or "").strip()
    inferred_type = _infer_type(item, detail, target_span, expected)
    if canonical_type != raw_type:
        item["type"] = canonical_type
        item["semantic_type"] = canonical_type
        item["classification_reason"] = _classification_reason(canonical_type, detail)
    elif inferred_type and (not original_type or _is_unclassified(original_type) or original_type != inferred_type and _is_generic_type(original_type)):
        item["original_type"] = original_type or "unknown"
        item["type"] = inferred_type
        item["semantic_type"] = inferred_type
        item["classification_reason"] = _classification_reason(inferred_type, detail)
    else:
        item["type"] = original_type or inferred_type or "unknown"

    if target_span and not item.get("target_span"):
        item["target_span"] = target_span
    if expected and not item.get("expected_behavior"):
        item["expected_behavior"] = expected

    _enrich_length_fields(item, detail)
    _enrich_scope_and_strategy(item)
    _enrich_issue_classification(item, detail)
    item.setdefault("violation_id", _stable_violation_id(item))
    return item


def _canonical_issue_type(vtype: str, detail: str = "") -> str:
    normalized = re.sub(r"[\s\-]+", "_", str(vtype or "").strip().lower())
    if not normalized:
        return ""
    if normalized in {"pov_conflict", "point_of_view_conflict", "viewpoint_conflict"}:
        return "pov_conflict"
    alias = TYPE_ALIASES.get(normalized)
    if alias:
        return alias

    tokens = set(filter(None, normalized.split("_")))
    confusionish = bool(tokens & {"confusion", "mismatch", "conflict", "error", "violation", "inconsistency"})
    if confusionish:
        if tokens & {"pov", "viewpoint"}:
            return "pov_conflict"
        if tokens & {"timeline", "time", "temporal", "chronology"}:
            return "timeline_conflict"
        if tokens & {"spatial", "space", "location", "position"}:
            return "spatial_conflict"
        if tokens & {"identity", "name", "role"}:
            return "identity_conflict"
        if tokens & {"causal", "cause", "logic"}:
            return "causal_chain_error"
        if tokens & {"knowledge", "cognitive"}:
            return "knowledge_boundary_violation"
        if "canon" in tokens and tokens & {"fact", "state"}:
            return "fact_conflict"

    if normalized.startswith("canon_") and normalized.endswith("_confusion"):
        return "fact_conflict"

    return str(vtype or "").strip()


def issue_fingerprint(issue: dict[str, Any]) -> str:
    """Stable key for deduplicating equivalent review items."""
    normalized = normalize_violation_semantics(issue)
    vtype = str(normalized.get("type") or "unknown")
    scope = str(normalized.get("scope") or normalized.get("repair_scope") or "prose_text")
    target = str(normalized.get("target_span") or "")
    hard_max = str(normalized.get("hard_max_chars") or "")
    if vtype in {"scene_too_long", "scene_too_short"}:
        return f"{vtype}:{scope}:{hard_max}"
    if target:
        return f"{vtype}:{scope}:{hashlib.md5(target.encode()).hexdigest()[:10]}"
    detail = str(normalized.get("detail") or "")[:120]
    return f"{vtype}:{scope}:{hashlib.md5(detail.encode()).hexdigest()[:10]}"


def _combined_text(item: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("type", "detail", "expected_behavior", "target_span", "evidence", "source", "suggested_strategy"):
        value = item.get(key)
        if value not in (None, ""):
            parts.append(str(value))
    return "\n".join(parts)


def _first_text(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _is_unclassified(vtype: str) -> bool:
    return vtype.strip().lower() in UNKNOWN_TYPES or "未分类" in vtype


def _is_generic_type(vtype: str) -> bool:
    return vtype in {
        "semantic_quality_error",
        "hard_correctness_issue",
        "review_issue",
        "unknown",
    }


def _extract_target_span(text: str) -> str:
    patterns = (
        r"文本描述[「“\"](.+?)[」”\"]",
        r"冲突片段[：:]\s*[「“\"](.+?)[」”\"]",
        r"正文(?:写了|描述|出现)[「“\"](.+?)[」”\"]",
        r"target_span[：:=]\s*[「“\"]?([^」”\"\n]+)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.S)
        if match:
            return _clean_span(match.group(1))
    return ""


def _extract_expected_behavior(text: str) -> str:
    patterns = (
        r"建议(?:修改|改写|调整)?为[「“\"](.+?)[」”\"]",
        r"应(?:修改|改写|调整|改为|保持|体现)?为[「“\"](.+?)[」”\"]",
        r"正确(?:写法|行为|状态)?[：:]\s*[「“\"](.+?)[」”\"]",
        r"与已知事实[「“\"](.+?)[」”\"]矛盾",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.S)
        if match:
            return _clean_span(match.group(1))
    return ""


def _clean_span(value: str) -> str:
    value = re.sub(r"\s+", " ", str(value or "")).strip()
    return value[:500]


def _infer_type(item: dict[str, Any], detail: str, target_span: str, expected: str) -> str:
    current = str(item.get("type") or "").strip()
    if current and not _is_unclassified(current) and not _is_generic_type(current):
        return current

    metric = str(item.get("metric") or "").strip()
    spec = get_metric_spec(metric, str(item.get("validator") or item.get("source_validator") or ""))
    if spec is not None:
        return spec.issue_type

    lowered = detail.lower()
    inferred_metric = infer_metric_from_detail(lowered)
    if inferred_metric:
        spec = get_metric_spec(inferred_metric)
        return spec.issue_type if spec is not None else inferred_metric

    if _looks_like_length_too_long(detail):
        return "scene_too_long"
    if _looks_like_length_too_short(detail):
        return "scene_too_short"
    if re.search(r"(时间锚点|辰时|卯时|午时|正午|黄昏|深夜|时间线|时辰)", detail):
        return "timeline_conflict"
    if re.search(r"(视角|POV|不该知道|离场角色|私密对话|认知边界)", detail, flags=re.I):
        return "pov_conflict"
    if re.search(r"(禁止|不得|不允许|forbidden)", detail, flags=re.I):
        if re.search(r"(回顾|复述|重提|recap)", detail, flags=re.I):
            return "forbidden_recap_violation"
        return "forbidden_assertion_triggered"
    if re.search(r"(结尾状态|结束状态|未达成|ending_state)", detail, flags=re.I):
        return "ending_state_not_reached"
    if re.search(r"(must_show|必须展示|必要信息|缺失|未出现|没有出现)", detail, flags=re.I):
        return "missing_must_show"
    if _looks_like_known_fact_conflict(detail):
        return _known_fact_conflict_type(detail)
    if re.search(r"(重复|反复|已.*又|再次).*?(动作|事件|放下|取出|摆在|滑入)?", detail):
        return "event_repeated"
    if re.search(r"(与已知事实|事实矛盾|自相矛盾|已知状态|状态冲突)", detail):
        if re.search(r"(身份|称谓|内门|外门|名字|姓名)", detail):
            return "identity_conflict"
        if re.search(r"(屋内|屋外|门外|院外|位置|地点|空间)", detail):
            return "spatial_conflict"
        return "fact_conflict"
    if re.search(r"(身份|称谓|名字|姓名).*?(矛盾|不一致|冲突)", detail):
        return "identity_conflict"
    if re.search(r"(解释腔|AI味|这意味着|也就是说|不是.+而是|总结式|破折号)", detail):
        return "narration_explanation_artifact"
    if re.search(r"(破折号|——|标点)", detail):
        return "explanatory_punctuation_artifact"
    if target_span and expected:
        return "fact_conflict"
    return current or "unknown"


def _looks_like_known_fact_conflict(text: str) -> bool:
    return bool(re.search(
        "(\u4e0e\u5df2\u77e5\u4e8b\u5b9e|\u4e8b\u5b9e\u77db\u76fe|\u81ea\u76f8\u77db\u76fe|\u5df2\u77e5\u72b6\u6001|\u72b6\u6001\u51b2\u7a81)",
        text,
    ))


def _known_fact_conflict_type(detail: str) -> str:
    if re.search("(\u8eab\u4efd|\u79f0\u8c13|\u5185\u95e8|\u5916\u95e8|\u540d\u5b57|\u59d3\u540d)", detail):
        return "identity_conflict"
    if re.search("(\u5c4b\u5185|\u5c4b\u5916|\u95e8\u5916|\u9662\u5916|\u4f4d\u7f6e|\u5730\u70b9|\u7a7a\u95f4)", detail):
        return "spatial_conflict"
    return "fact_conflict"


def _looks_like_length_too_long(text: str) -> bool:
    return bool(re.search(r"(文本长度|场景字数|字数|长度)\s*\d+.*?(超过|超出|大于).{0,12}(硬性上限|硬上限|上限)\s*\d+", text))


def _looks_like_length_too_short(text: str) -> bool:
    return bool(re.search(r"(文本长度|场景字数|字数|长度)\s*\d+.*?(低于|少于|小于).{0,12}(硬性下限|硬下限|下限)\s*\d+", text))


def _enrich_length_fields(item: dict[str, Any], detail: str) -> None:
    if item.get("current_length") and (item.get("hard_max_chars") or item.get("hard_min_chars")):
        return
    match = re.search(r"(?:文本长度|场景字数|字数|长度)\s*(\d+).*?(?:硬性上限|硬上限|上限)\s*(\d+)", detail)
    if match:
        item.setdefault("current_length", int(match.group(1)))
        item.setdefault("hard_max_chars", int(match.group(2)))
        return
    match = re.search(r"(?:文本长度|场景字数|字数|长度)\s*(\d+).*?(?:硬性下限|硬下限|下限)\s*(\d+)", detail)
    if match:
        item.setdefault("current_length", int(match.group(1)))
        item.setdefault("hard_min_chars", int(match.group(2)))


def _enrich_scope_and_strategy(item: dict[str, Any]) -> None:
    vtype = str(item.get("type") or "")
    explicit_repair_scope = str(item.get("repair_scope") or item.get("scope") or "")
    spec = get_metric_spec(item.get("metric") or vtype)
    if vtype in SYSTEM_TYPES or (spec is not None and spec.family == "system"):
        item["scope"] = "validator_system"
        item["repair_scope"] = "system"
        item["repairable_by_text"] = False
        item["repairable_by_contract"] = False
        item.setdefault("suggested_strategy", "validator_retry")
        item.setdefault("repairability", "degraded_system_issue")
        return
    if _is_unlocalized_advisory_bundle(item):
        item.setdefault("scope", "chapter")
        item.setdefault("repair_scope", "chapter")
        item["repairable_by_text"] = False
        item["repairable_by_contract"] = False
        item.setdefault("suggested_strategy", "manual_review")
        item.setdefault("repairability", "human_review_required")
        return
    if spec is not None and spec.status == "manual_with_reason":
        item.setdefault("scope", "chapter")
        item.setdefault("repair_scope", "chapter")
        item["repairable_by_text"] = False
        item["repairable_by_contract"] = False
        item.setdefault("suggested_strategy", "manual_review")
        item.setdefault("repairability", "human_review_required")
        return

    if vtype in DIRECT_TEXT_SCOPES:
        item.setdefault("scope", "prose_text")
        item.setdefault("repair_scope", "prose_text")
        if explicit_repair_scope in {"scene_contract", "chapter_contract", "planning", "system"}:
            item["repairable_by_text"] = False
            item["repairable_by_contract"] = explicit_repair_scope == "scene_contract"
        else:
            item["repairable_by_text"] = True
            item["repairable_by_contract"] = False

    if not item.get("suggested_strategy") or item.get("suggested_strategy") == "manual_review":
        strategy = STRATEGY_MAP.get(vtype)
        if strategy:
            item["suggested_strategy"] = strategy
        elif vtype != "unknown":
            item["suggested_strategy"] = "patch_text"

    if not item.get("source") or item.get("source") == "unknown":
        item["source"] = _source_for_type(vtype)

    if _looks_like_direct_temporal_repair(item):
        item["suggested_strategy"] = "patch_text"


def _enrich_issue_classification(item: dict[str, Any], detail: str) -> None:
    vtype = str(item.get("type") or "")
    repair_scope = str(item.get("repair_scope") or item.get("scope") or "")
    repairability = str(item.get("repairability") or "")
    suggested = str(item.get("suggested_strategy") or "")

    if vtype in SYSTEM_TYPES or repair_scope == "system":
        classification = "validator_system_error"
    elif vtype in PLANNING_CONFLICT_TYPES or repair_scope in {"chapter_contract", "planning"}:
        classification = "planning_conflict"
    elif repair_scope == "scene_contract" or item.get("repairable_by_contract") is True:
        classification = "contract_repair_required"
    elif repairability == "human_review_required" or suggested == "manual_review":
        classification = "manual_only"
    elif item.get("repairable_by_text") is True or repair_scope == "prose_text" or vtype in TEXT_REPAIRABLE_TYPES:
        classification = "text_repairable"
    elif _looks_like_contract_authority_problem(detail):
        classification = "contract_repair_required"
    else:
        classification = "text_repairable" if vtype != "unknown" else "manual_only"

    item.setdefault("issue_classification", classification)
    item.setdefault("classification", classification)


def _looks_like_contract_authority_problem(detail: str) -> bool:
    text = str(detail or "")
    return bool(re.search(r"(合同内部冲突|合同本身|大纲互相矛盾|无法同时满足|需修改合同|需调整合同)", text))


def _source_for_type(vtype: str) -> str:
    if vtype in {"scene_too_long", "scene_too_short"}:
        return "deterministic"
    if vtype in {"narration_explanation_artifact", "explanatory_punctuation_artifact", "repetition_artifact"}:
        return "scene_credibility"
    if vtype.startswith("forbidden"):
        return "narrative_contract"
    if vtype in {"missing_must_show", "ending_state_not_reached"}:
        return "critic"
    return "consistency"


def _looks_like_direct_temporal_repair(item: dict[str, Any]) -> bool:
    vtype = str(item.get("type") or "")
    if vtype not in {"timeline_conflict", "temporal_conflict"}:
        return False
    target_span = str(item.get("target_span") or "")
    expected = str(item.get("expected_behavior") or item.get("expected_text") or item.get("replacement") or "")
    if not target_span or not expected:
        return False
    target_phrases = _extract_temporal_phrases(target_span)
    expected_phrases = _extract_temporal_phrases(expected)
    if not target_phrases or not expected_phrases:
        return False
    return target_phrases != expected_phrases


def _extract_temporal_phrases(text: str) -> list[str]:
    phrases: list[str] = []
    for match in TEMPORAL_DIRECT_REPAIR_RE.finditer(str(text or "")):
        phrase = match.group(1)
        if phrase not in phrases:
            phrases.append(phrase)
    return phrases


def _classification_reason(vtype: str, detail: str) -> str:
    if vtype == "scene_too_long":
        return "length_over_hard_or_soft_limit"
    if "已知事实" in detail:
        return "known_fact_conflict_phrase"
    if "文本描述" in detail:
        return "quoted_text_description_extracted"
    return "semantic_keyword_rule"


def _stable_violation_id(item: dict[str, Any]) -> str:
    seed = f"{item.get('source')}:{item.get('type')}:{item.get('target_span') or item.get('detail', '')[:120]}"
    return hashlib.md5(seed.encode()).hexdigest()[:12]


def _is_unlocalized_advisory_bundle(item: dict[str, Any]) -> bool:
    metric = str(item.get("metric") or item.get("type") or item.get("violation_type") or "").lower()
    if metric not in {"high_advisories", "medium_advisories"}:
        return False
    if str(item.get("target_span") or item.get("span") or "").strip():
        return False
    evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
    spans = item.get("evidence_spans") or evidence.get("evidence_spans") or []
    if isinstance(spans, list):
        for span in spans:
            if isinstance(span, dict) and str(span.get("span") or span.get("text") or "").strip():
                return False
            if isinstance(span, str) and span.strip():
                return False
    return True
