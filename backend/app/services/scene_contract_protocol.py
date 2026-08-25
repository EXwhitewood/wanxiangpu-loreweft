"""Domain-neutral layering for scene contracts.

Scene contracts are task documents, not a single flat authority source. This
module keeps the authority tiers explicit so Writer, QualityGate/FBI, and repair
audit can make the same routing decision without relying on story-specific text.
"""
from __future__ import annotations

import hashlib
import re
from difflib import SequenceMatcher
from typing import Any


CONTRACT_LAYER_KEYS = (
    "hard_facts",
    "initial_state",
    "required_outcomes",
    "required_transitions",
    "performance_requirements",
    "soft_guidance",
)


def build_scene_contract_protocol(scene_contract: dict | None) -> dict[str, Any]:
    contract = scene_contract if isinstance(scene_contract, dict) else {}
    existing = contract.get("contract_protocol")
    if isinstance(existing, dict) and existing:
        return _normalize_protocol(existing, scene_index=_scene_index(contract))

    source = _as_dict(contract.get("source_of_truth"))
    enrichment = _as_dict(contract.get("editor_enrichment"))
    provenance = _as_dict(contract.get("scene_provenance"))
    timeline_anchor = _as_dict(provenance.get("timeline_anchor"))
    current_facts = _as_dict(provenance.get("current_facts"))
    long_term = _as_dict(contract.get("long_term_constraints"))

    hard_facts: list[Any] = []
    hard_facts.extend(_as_list(current_facts.get("established_facts")))
    hard_facts.extend(_as_list(current_facts.get("active_constraints")))
    hard_facts.extend(_as_list(long_term.get("required_prior_facts")))
    hard_facts.extend(_as_list(long_term.get("character_state_constraints")))
    hard_facts.extend(_as_list(source.get("hard_facts")))
    hard_facts.extend(_as_list(contract.get("hard_facts")))

    initial_state = _dedupe([
        *(_as_list(timeline_anchor.get("opening_state"))),
        *(_as_list(enrichment.get("opening_state"))),
        *(_as_list(contract.get("opening_state"))),
    ])

    required_outcomes = _dedupe([
        *(_as_list(source.get("outline_outcome"))),
        *(_as_list(source.get("required_outcomes"))),
        *(_as_list(enrichment.get("ending_state"))),
        *(_as_list(contract.get("ending_state"))),
        *(_as_list(timeline_anchor.get("ending_state"))),
    ])

    required_transitions = _dedupe([
        *(_as_list(source.get("required_transitions"))),
        *(_as_list(contract.get("required_transitions"))),
        *(_as_list(source.get("must_show_outline"))),
        *(_as_list(contract.get("hard_must_show"))),
    ])

    performance_requirements = _dedupe([
        *(_as_list(contract.get("must_show"))),
        *(_as_list(enrichment.get("additional_must_show"))),
        *(_as_list(contract.get("performance_requirements"))),
    ])

    soft_guidance = _dedupe([
        *(_as_list(contract.get("soft_guidance"))),
        *(_as_list(contract.get("deferred_items"))),
        *(_as_list(contract.get("quality_memory_guidance"))),
        *(_as_list(contract.get("commercial_pacing_guidance"))),
    ])

    return _normalize_protocol({
        "schema_version": 1,
        "authority_order": list(CONTRACT_LAYER_KEYS),
        "hard_facts": hard_facts,
        "initial_state": initial_state,
        "required_outcomes": required_outcomes,
        "required_transitions": required_transitions,
        "performance_requirements": performance_requirements,
        "soft_guidance": soft_guidance,
    }, scene_index=_scene_index(contract))


def render_scene_contract_protocol_rules(protocol: dict | None = None) -> list[str]:
    protocol = _normalize_protocol(protocol or {})
    return [
        "场景合同协议：合同按权威层级执行，不得把所有条目当成同一种硬事实。",
        "hard_facts 是最高事实层，正文不得改写、反转或替换。",
        "initial_state 是开场承接层，正文可以换表达，但不得制造与其相反的起点。",
        "required_outcomes 与 required_transitions 是必达层，正文必须达成结果和关键转变，但允许用不同动作、对白和结构实现。",
        "performance_requirements 是表现层，优先满足但可用等效表达；soft_guidance 只作为质量建议，不得当成事实冲突。",
        "如果合同层级互相冲突，停止编造正文，应暴露为合同/规划问题，而不是用旧稿片段硬修。",
    ]


def hard_obligation_sources(scene_contract: dict | None) -> dict[str, list[str]]:
    protocol = build_scene_contract_protocol(scene_contract)
    return {
        "hard_facts": _string_list(protocol.get("hard_facts")),
        "required_outcomes": _string_list(protocol.get("required_outcomes")),
        "required_transitions": _string_list(protocol.get("required_transitions")),
    }


def contract_obligations(scene_contract: dict | None) -> list[dict[str, Any]]:
    """Return stable, domain-neutral obligation descriptors for a scene."""
    return list(build_scene_contract_protocol(scene_contract).get("obligations") or [])


def bind_contract_violation(
    scene_contract: dict | None,
    violation: dict | None,
    *,
    scene_index: int | None = None,
) -> dict[str, Any]:
    """Attach the best matching obligation identity to a contract violation."""
    item = dict(violation or {})
    vtype = str(item.get("type") or item.get("violation_type") or "")
    candidate_layers = _violation_candidate_layers(vtype)
    if not candidate_layers:
        return item

    obligations = [
        obligation
        for obligation in contract_obligations(scene_contract)
        if obligation.get("layer") in candidate_layers
    ]
    explicit_id = str(item.get("obligation_id") or "").strip()
    matched = next(
        (obligation for obligation in obligations if obligation["obligation_id"] == explicit_id),
        None,
    )
    if matched is None:
        matched = _best_obligation_match(item, obligations)

    if matched is None:
        item.setdefault("obligation_binding_status", "unresolved")
        return item

    item.update({
        "obligation_id": matched["obligation_id"],
        "obligation_scope": matched["scope"],
        "obligation_owner_scene": matched.get("owner_scene"),
        "obligation_layer": matched["layer"],
        "obligation_text": matched["text"],
        "obligation_binding_status": "bound",
        "obligation_ownership_explicit": matched["ownership_explicit"],
    })
    if scene_index is not None:
        item["obligation_owner_matches_scene"] = (
            matched["scope"] == "scene"
            and matched.get("owner_scene") == scene_index
        )
    return item


def stabilize_recheck_contract_violations(
    *,
    initial_violations: list[dict],
    recheck_blocking: list[dict],
    scene_contract: dict | None,
    scene_index: int,
) -> tuple[list[dict], list[dict]]:
    """Keep recheck blockers stable and demote unowned newly discovered gaps."""
    initial_signatures = {
        _violation_signature(
            bind_contract_violation(
                scene_contract,
                violation,
                scene_index=scene_index,
            )
        )
        for violation in initial_violations
        if isinstance(violation, dict)
    }
    kept: list[dict] = []
    demoted: list[dict] = []

    for raw in recheck_blocking:
        violation = bind_contract_violation(
            scene_contract,
            raw,
            scene_index=scene_index,
        )
        if not _violation_candidate_layers(
            str(violation.get("type") or violation.get("violation_type") or "")
        ):
            kept.append(violation)
            continue

        signature = _violation_signature(violation)
        binding_status = violation.get("obligation_binding_status")
        scope = violation.get("obligation_scope")
        owner = violation.get("obligation_owner_scene")
        ownership_explicit = bool(violation.get("obligation_ownership_explicit"))

        reason = ""
        if scope == "chapter":
            reason = "chapter_scoped_obligation_deferred_to_chapter_validation"
        elif scope == "scene" and owner is not None and owner != scene_index:
            reason = "obligation_owned_by_other_scene"
        elif (
            signature not in initial_signatures
            and (binding_status != "bound" or not ownership_explicit)
        ):
            reason = "new_recheck_contract_gap_without_explicit_ownership"

        if not reason:
            kept.append(violation)
            continue

        advisory = dict(violation)
        advisory["severity"] = "medium"
        advisory["blocks_commit"] = False
        advisory["recheck_disposition"] = "demoted"
        advisory["recheck_demotion_reason"] = reason
        demoted.append(advisory)

    return kept, demoted


def _normalize_protocol(
    value: dict[str, Any],
    *,
    scene_index: int | None = None,
) -> dict[str, Any]:
    result = {"schema_version": int(value.get("schema_version") or 1)}
    result["authority_order"] = list(CONTRACT_LAYER_KEYS)
    for key in CONTRACT_LAYER_KEYS:
        result[key] = _dedupe(_as_list(value.get(key)))
    result["obligations"] = _normalize_obligations(
        value.get("obligations"),
        result,
        scene_index=scene_index,
    )
    return result


def _normalize_obligations(
    existing: Any,
    protocol: dict[str, Any],
    *,
    scene_index: int | None,
) -> list[dict[str, Any]]:
    obligations: list[dict[str, Any]] = []
    if isinstance(existing, list):
        for item in existing:
            normalized = _normalize_obligation_item(
                item,
                layer=str(item.get("layer") or "performance_requirements")
                if isinstance(item, dict)
                else "performance_requirements",
                scene_index=scene_index,
            )
            if normalized:
                obligations.append(normalized)

    existing_ids = {item["obligation_id"] for item in obligations}
    for layer in CONTRACT_LAYER_KEYS:
        if layer == "soft_guidance":
            continue
        for item in protocol.get(layer) or []:
            normalized = _normalize_obligation_item(
                item,
                layer=layer,
                scene_index=scene_index,
            )
            if normalized and normalized["obligation_id"] not in existing_ids:
                obligations.append(normalized)
                existing_ids.add(normalized["obligation_id"])
    return obligations


def _normalize_obligation_item(
    item: Any,
    *,
    layer: str,
    scene_index: int | None,
) -> dict[str, Any] | None:
    data = item if isinstance(item, dict) else {}
    text = _obligation_text(item)
    if not text:
        return None
    explicit_scope = str(data.get("scope") or data.get("obligation_scope") or "").strip()
    explicit_owner = data.get("owner_scene", data.get("obligation_owner_scene"))
    owner_scene = explicit_owner if isinstance(explicit_owner, int) else scene_index
    scope = explicit_scope if explicit_scope in {"scene", "chapter"} else "scene"
    if scope == "chapter":
        owner_scene = None
    ownership_explicit = bool(
        explicit_scope
        or isinstance(explicit_owner, int)
        or data.get("obligation_id")
    )
    obligation_id = str(data.get("obligation_id") or data.get("id") or "").strip()
    if not obligation_id:
        seed = f"{layer}:{scope}:{owner_scene}:{_normalize_text(text)}"
        obligation_id = f"obl_{hashlib.sha1(seed.encode('utf-8')).hexdigest()[:12]}"
    return {
        "obligation_id": obligation_id,
        "scope": scope,
        "owner_scene": owner_scene,
        "layer": layer,
        "text": text,
        "evidence_mode": str(data.get("evidence_mode") or "semantic"),
        "blocking": layer in {
            "hard_facts",
            "required_outcomes",
            "required_transitions",
        },
        "ownership_explicit": ownership_explicit,
    }


def _best_obligation_match(
    violation: dict[str, Any],
    obligations: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not obligations:
        return None
    haystack = _normalize_text(" ".join(
        str(violation.get(key) or "")
        for key in ("target_span", "expected_behavior", "detail", "reason")
    ))
    if not haystack:
        return None

    scored: list[tuple[float, dict[str, Any]]] = []
    for obligation in obligations:
        needle = _normalize_text(obligation.get("text"))
        if not needle:
            continue
        if needle in haystack or haystack in needle:
            score = 1.0
        else:
            score = SequenceMatcher(None, needle, haystack).ratio()
        scored.append((score, obligation))
    if not scored:
        return None
    scored.sort(key=lambda pair: pair[0], reverse=True)
    best_score, best = scored[0]
    next_score = scored[1][0] if len(scored) > 1 else 0.0
    if best_score < 0.28 or best_score - next_score < 0.04:
        return None
    return best


def _violation_candidate_layers(vtype: str) -> set[str]:
    if vtype in {"missing_must_show", "missing_outline_fact", "outline_fact_not_shown"}:
        return {"required_outcomes", "required_transitions", "performance_requirements"}
    if vtype == "ending_state_not_reached":
        return {"required_outcomes", "required_transitions"}
    return set()


def _violation_signature(violation: dict[str, Any]) -> str:
    obligation_id = str(violation.get("obligation_id") or "")
    vtype = str(violation.get("type") or violation.get("violation_type") or "")
    if obligation_id:
        return f"{vtype}:{obligation_id}"
    target = _normalize_text(
        violation.get("target_span")
        or violation.get("detail")
        or violation.get("reason")
        or ""
    )
    return f"{vtype}:{hashlib.sha1(target.encode('utf-8')).hexdigest()[:12]}"


def _obligation_text(item: Any) -> str:
    if isinstance(item, dict):
        for key in ("text", "requirement", "description", "claim", "name"):
            value = str(item.get(key) or "").strip()
            if value:
                return value
        return ""
    return str(item or "").strip()


def _normalize_text(value: Any) -> str:
    return re.sub(r"[\W_]+", "", str(value or "").lower())


def _scene_index(contract: dict[str, Any]) -> int | None:
    value = contract.get("scene_index")
    return value if isinstance(value, int) else None


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list:
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return [item for item in value if item not in (None, "")]
    if isinstance(value, tuple | set):
        return [item for item in value if item not in (None, "")]
    return [value]


def _string_list(value: Any) -> list[str]:
    return [str(item).strip() for item in _as_list(value) if str(item).strip()]


def _dedupe(values: list[Any]) -> list[Any]:
    seen: set[str] = set()
    result: list[Any] = []
    for value in values:
        key = str(value).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result
