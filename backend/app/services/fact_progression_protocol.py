"""Shared baseline/progression protocol for generation quality issues.

This module is intentionally deterministic and database-free. It does not
decide facts by itself; it classifies checker output so existing layers can
route issues consistently.
"""

from __future__ import annotations

import copy
import re
from typing import Any


REPAIRABLE_CLASSIFICATIONS = {
    "contradiction",
}

NON_BLOCKING_CLASSIFICATIONS = {
    "progression",
    "time_jump",
    "referenced_time",
    "character_claim",
    "advisory_quality",
}

GOVERNANCE_CLASSIFICATIONS = {
    "retcon",
    "alias_needed",
    "contract_conflict",
}

TEMPORAL_TYPES = {
    "timeline_conflict",
    "temporal_conflict",
    "internal_conflict",
    "canon_timeline_confusion",
    "temporal_layer_confusion",
}

ADVISORY_TYPES = {
    "scene_too_long",
    "scene_too_short",
    "must_show_overload",
    "info_dump_moderate_density",
    "setting_paragraph_too_long",
    "weak_opening_hook",
    "low_event_density",
    "low_conflict_density",
    "flat_pressure_ramp",
    "weak_curiosity_engine",
    "low_reversal_density",
    "missing_micro_payoff",
    "weak_chapter_end_hook",
    "low_reader_retention",
    "specificity_budget_unmet",
    "abstraction_over_budget",
    "emotional_claim_without_scene_evidence",
    "prose_identity_weak",
}

CONTRACT_TYPES = {
    "missing_contract",
    "scene_contract_compile_blocked",
    "style_contract_conflict",
}

ALIAS_TYPES = {
    "naming_conflict",
    "identity_conflict",
}

RETCON_TYPES = {
    "event_repeated",
    "forbidden_recap_violation",
}

REFERENCE_TIME_PATTERNS = [
    r"三日前",
    r"两日前",
    r"数日前",
    r"日前",
    r"昨日",
    r"昨夜",
    r"前日",
    r"当年",
    r"那年",
    r"那日",
    r"那天",
    r"那晚",
    r"此前",
    r"从前",
    r"过去",
    r"曾经",
    r"后来",
    r"明日",
    r"翌日",
    r"次日",
    r"\bago\b",
    r"\byesterday\b",
    r"\blast\s+\w+",
]

CLAIM_PATTERNS = [
    r"说",
    r"问",
    r"道",
    r"声称",
    r"指称",
    r"宣称",
    r"提到",
    r"记得",
    r"回忆",
    r"听说",
    r"传闻",
    r"claim",
    r"said",
    r"asked",
    r"remembered",
]

BOUNDARY_PATTERNS = [
    r"翌日",
    r"次日",
    r"第二天",
    r"一夜",
    r"转日",
    r"天亮",
    r"清晨",
    r"晨",
    r"夜色",
    r"暮色",
    r"数日后",
    r"片刻后",
    r"半个时辰后",
    r"after",
    r"next\s+day",
]

PROGRESSION_PATTERNS = [
    r"交给",
    r"递给",
    r"拿出",
    r"取出",
    r"放入",
    r"收起",
    r"发现",
    r"得知",
    r"来到",
    r"离开",
    r"进入",
    r"赶到",
    r"返回",
    r"恢复",
    r"苏醒",
    r"醒来",
    r"受伤",
    r"治好",
    r"愈合",
    r"恶化",
    r"失去",
    r"遗忘",
    r"失忆",
    r"获得",
    r"转移",
    r"消失",
    r"断裂",
    r"破碎",
    r"毁坏",
    r"熄灭",
    r"燃起",
    r"耗尽",
    r"死亡",
    r"变成",
    r"变为",
    r"转为",
    r"revealed",
    r"found",
    r"learned",
    r"arrived",
    r"left",
    r"gave",
    r"received",
]


def _join_text(*values: Any) -> str:
    parts: list[str] = []
    for value in values:
        if value is None:
            continue
        if isinstance(value, str):
            parts.append(value)
        else:
            parts.append(str(value))
    return "\n".join(parts)


def _has_any(text: str, patterns: list[str]) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def _scene_contract_text(context: dict | None) -> str:
    if not isinstance(context, dict):
        return ""
    contract = context.get("scene_contract") or {}
    package = context.get("scene_package") or {}
    pieces = [contract, package.get("scene_contract"), context.get("scene_beat")]
    return _join_text(*pieces)


def _classification_for_violation(violation: dict, context: dict | None = None) -> tuple[str, str, str]:
    vtype = str(violation.get("type") or "")
    scope = str(violation.get("repair_scope") or violation.get("scope") or "")
    strategy = str(violation.get("suggested_strategy") or "")
    source = str(violation.get("source") or "")
    text = _join_text(
        violation.get("detail"),
        violation.get("target_span"),
        violation.get("expected_behavior"),
        violation.get("evidence"),
    )
    contract_text = _scene_contract_text(context)

    if vtype in ADVISORY_TYPES and not violation.get("blocks_commit"):
        return "advisory_quality", "advisory", "non_blocking_polish"

    if scope == "scene_contract" or strategy in {"repair_contract", "contract_budget_adjust"} or vtype in CONTRACT_TYPES:
        if vtype == "must_show_overload":
            return "advisory_quality", "advisory", "non_blocking_polish"
        return "contract_conflict", "scene_contract", "return_to_planning"

    if vtype in ALIAS_TYPES:
        return "alias_needed", "entity_alias", "create_alias_proposal"

    if vtype in RETCON_TYPES:
        return "retcon", "baseline_world_state", "wait_human_review"

    if vtype in TEMPORAL_TYPES:
        if _has_any(text, CLAIM_PATTERNS) and _has_any(text, REFERENCE_TIME_PATTERNS):
            return "character_claim", "draft_text", "preserve_claim_check_truth_status"
        if _has_any(text, REFERENCE_TIME_PATTERNS):
            return "referenced_time", "draft_text", "downgrade_or_check_internal_consistency"
        if _has_any(text, BOUNDARY_PATTERNS) or _has_any(contract_text, BOUNDARY_PATTERNS):
            return "time_jump", "scene_contract", "validate_boundary_then_accept"
        return "contradiction", "baseline_world_state", "repair_text"

    if vtype in {"ownership_conflict", "spatial_conflict", "fact_conflict", "setting_conflict"}:
        prior_chronology_text = _join_text(
            context.get("previous_scenes_summary") if isinstance(context, dict) else "",
            context.get("previous_scene_ending") if isinstance(context, dict) else "",
        )
        chronology_text = prior_chronology_text or _join_text(
            context.get("generated_text") if isinstance(context, dict) else "",
        )
        if (
            _has_any(text, PROGRESSION_PATTERNS)
            and (
                not chronology_text.strip()
                or _has_any(chronology_text, PROGRESSION_PATTERNS)
            )
        ):
            return "progression", "draft_text", "accept_as_delta"

    if source in {"reader_experience", "narrative_experience", "literary_quality", "commercial_pacing"}:
        if not violation.get("blocks_commit"):
            return "advisory_quality", "advisory", "non_blocking_polish"

    return "contradiction", "baseline_world_state", "repair_text"


def enrich_violation(violation: dict, context: dict | None = None) -> dict:
    """Return a copy of a violation with protocol routing fields."""
    item = copy.deepcopy(violation)
    classification, authority_source, route = _classification_for_violation(item, context)
    item["classification"] = classification
    item["authority_source"] = authority_source
    item["recommended_route"] = route
    item.setdefault("evidence_span", item.get("target_span") or "")
    item["protocol_version"] = "baseline_progression_v1"

    if classification in NON_BLOCKING_CLASSIFICATIONS:
        item["blocks_commit"] = False
        item["suggested_strategy"] = "manual_review" if classification != "advisory_quality" else "patch_text"
        item["repair_scope"] = "advisory"
        item["scope"] = "advisory"
    elif classification == "contract_conflict":
        item["repair_scope"] = "scene_contract"
        item["scope"] = "scene_contract"
        item["repairable_by_text"] = False
        item["repairable_by_contract"] = True
        item["suggested_strategy"] = "repair_contract"
    elif classification in {"alias_needed", "retcon"}:
        item["repair_scope"] = classification
        item["scope"] = "advisory"
        item["repairable_by_text"] = False
        item["repairable_by_contract"] = False
        item["suggested_strategy"] = "manual_review"
        item["blocks_commit"] = bool(item.get("blocks_commit", True))
    return item


def apply_protocol_to_violations(violations: list, context: dict | None = None) -> list[dict]:
    return [
        enrich_violation(v, context)
        for v in violations
        if isinstance(v, dict)
    ]


def summarize_protocol(violations: list[dict]) -> dict:
    by_classification: dict[str, int] = {}
    by_route: dict[str, int] = {}
    blocking_by_classification: dict[str, int] = {}
    for item in violations:
        classification = str(item.get("classification") or "unclassified")
        route = str(item.get("recommended_route") or "unknown")
        by_classification[classification] = by_classification.get(classification, 0) + 1
        by_route[route] = by_route.get(route, 0) + 1
        if item.get("blocks_commit"):
            blocking_by_classification[classification] = blocking_by_classification.get(classification, 0) + 1
    return {
        "protocol_version": "baseline_progression_v1",
        "by_classification": by_classification,
        "by_route": by_route,
        "blocking_by_classification": blocking_by_classification,
        "blocking_count": sum(1 for item in violations if item.get("blocks_commit")),
    }


def review_status_for_violations(violations: list[dict], default: str = "waiting_review") -> str:
    blocking = [item for item in violations if isinstance(item, dict) and item.get("blocks_commit")]
    if not blocking:
        return default
    classes = {str(item.get("classification") or "") for item in blocking}
    if classes <= {"contract_conflict"}:
        return "waiting_contract_repair"
    if classes <= {"alias_needed"}:
        return "waiting_alias_decision"
    if "retcon" in classes:
        return "waiting_retcon_decision"
    return default


def is_fbi_repairable_violation(violation: dict) -> bool:
    if not isinstance(violation, dict):
        return False
    classification = str(violation.get("classification") or "")
    if classification in NON_BLOCKING_CLASSIFICATIONS:
        return False
    if classification in GOVERNANCE_CLASSIFICATIONS:
        return False
    scope = str(violation.get("repair_scope") or violation.get("scope") or "prose_text")
    # prose_text 层：需要 repairable_by_text
    if scope == "prose_text":
        return bool(violation.get("repairable_by_text", True))
    # scene_contract / outline_plan 层：需要 repairable_by_contract
    if scope in {"scene_contract", "outline_plan"}:
        return bool(violation.get("repairable_by_contract", False))
    return False
