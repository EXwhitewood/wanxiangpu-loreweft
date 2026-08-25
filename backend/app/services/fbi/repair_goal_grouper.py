"""Semantic repair-goal normalization and placement protocol.

Findings are evidence.  They are not execution units.  This module collapses
duplicate reports into stable repair goals before any physical edit-window
grouping takes place, and keeps diagnostic evidence separate from write
anchors so a fallback review window can never silently become an insertion
location.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Iterable

from app.models.fbi_diagnosis import FBIPlacementDecision, FBIRepairGoal


_OPERATION_TO_MUTATION = {
    "replace_exact": "replace",
    "replace_span": "replace",
    "replace_literal": "replace",
    "delete_exact": "delete",
    "delete_span": "delete",
    "insert_before_anchor": "insert",
    "insert_after_anchor": "insert",
    "insert_anchor": "insert",
    "rewrite_window": "rewrite_window",
    "llm_creative_rewrite": "rewrite_window",
}

_QUOTED_OBLIGATION_RE = re.compile(
    r"[\"'“‘《]([^\"'”’》]{4,240})[\"'”’》]"
)


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple, set)):
        return " | ".join(_as_text(item) for item in value if _as_text(item))
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return str(value).strip()


def _identity_text(value: Any) -> str:
    text = _as_text(value).lower()
    return re.sub(r"[\s\u3000,，。；;：:、.!！?？'\"“”‘’（）()\[\]{}]+", "", text)


def _first_text(*values: Any) -> str:
    for value in values:
        text = _as_text(value)
        if text:
            return text
    return ""


def _quoted_obligation(finding: dict[str, Any]) -> str:
    candidates: list[str] = []
    for value in (
        finding.get("expected_behavior"),
        finding.get("required_behavior"),
        finding.get("obligation_text"),
        finding.get("detail"),
        finding.get("reason"),
    ):
        for match in _QUOTED_OBLIGATION_RE.findall(_as_text(value)):
            text = match.strip()
            if text:
                candidates.append(text)
    return max(candidates, key=len, default="")


def _issue_id(finding: dict[str, Any]) -> str:
    return str(
        finding.get("issue_id")
        or finding.get("violation_id")
        or finding.get("id")
        or ""
    )


def _scene_index(finding: dict[str, Any]) -> int | None:
    for key in (
        "obligation_owner_scene",
        "owner_scene",
        "source_scene",
        "scene_index",
        "affected_scene",
    ):
        value = finding.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    scenes = finding.get("target_scenes") or finding.get("source_scenes") or []
    if isinstance(scenes, list):
        for value in scenes:
            if isinstance(value, int) and not isinstance(value, bool):
                return value
    return None


def _owner_scope(finding: dict[str, Any]) -> dict[str, Any]:
    explicit_scope = str(
        finding.get("obligation_scope")
        or finding.get("owner_scope")
        or finding.get("repair_scope")
        or finding.get("scope")
        or ""
    ).strip().lower()
    scene_index = _scene_index(finding)
    if explicit_scope in {"chapter", "chapter_text", "all_scenes", "global"}:
        return {"scope": "chapter"}
    if scene_index is not None:
        return {"scope": "scene", "scene_index": scene_index}
    return {"scope": explicit_scope or "chapter"}


def _authority_ref(finding: dict[str, Any], desired: str, prohibited: str) -> str:
    evidence = finding.get("evidence") if isinstance(finding.get("evidence"), dict) else {}
    authority = finding.get("authority") if isinstance(finding.get("authority"), dict) else {}
    explicit = _first_text(
        finding.get("authority_ref"),
        finding.get("obligation_id"),
        finding.get("requirement_id"),
        finding.get("contract_ref"),
        finding.get("contract_id"),
        finding.get("skill_contract_ref"),
        evidence.get("authority_ref"),
        evidence.get("obligation_id"),
        evidence.get("contract_ref"),
        authority.get("ref"),
        authority.get("id"),
    )
    if explicit:
        return explicit
    statement = _first_text(
        finding.get("authority_statement"),
        finding.get("contract_obligation"),
        finding.get("must_show"),
        finding.get("required_state"),
        authority.get("statement"),
        desired,
        prohibited,
    )
    if statement:
        return f"derived:{_stable_hash(_identity_text(statement))}"
    source = _first_text(
        finding.get("validator"),
        finding.get("source"),
        finding.get("metric"),
        finding.get("type"),
        finding.get("violation_type"),
        finding.get("detail"),
    )
    return f"finding:{_stable_hash(_identity_text(source))}"


def _explicit_obligation_ref(finding: dict[str, Any]) -> str:
    """Return an upstream identity only when it names an actual obligation."""

    evidence = finding.get("evidence") if isinstance(finding.get("evidence"), dict) else {}
    authority = finding.get("authority") if isinstance(finding.get("authority"), dict) else {}
    return _first_text(
        finding.get("obligation_id"),
        finding.get("requirement_id"),
        finding.get("contract_item_id"),
        finding.get("must_show_id"),
        finding.get("contract_ref"),
        finding.get("skill_contract_ref"),
        evidence.get("obligation_id"),
        evidence.get("contract_ref"),
        authority.get("id"),
        finding.get("authority_ref"),
    )


def _finding_semantic_text(finding: dict[str, Any]) -> str:
    return "\n".join(
        _as_text(value)
        for value in (
            finding.get("obligation_text"),
            finding.get("must_show"),
            finding.get("required_state"),
            finding.get("contract_obligation"),
            finding.get("expected_behavior"),
            finding.get("required_behavior"),
            finding.get("detail"),
            finding.get("reason"),
        )
        if _as_text(value)
    )


def _character_ngrams(value: Any, size: int = 2) -> set[str]:
    text = _identity_text(value)
    if not text:
        return set()
    if len(text) <= size:
        return {text}
    return {text[index:index + size] for index in range(len(text) - size + 1)}


def _semantic_overlap(left: Any, right: Any) -> float:
    """Conservative language-independent overlap used only for contract lookup."""

    left_text = _identity_text(left)
    right_text = _identity_text(right)
    if not left_text or not right_text:
        return 0.0
    if left_text in right_text or right_text in left_text:
        return min(len(left_text), len(right_text)) / max(len(left_text), len(right_text))
    left_grams = _character_ngrams(left_text)
    right_grams = _character_ngrams(right_text)
    if not left_grams or not right_grams:
        return 0.0
    return len(left_grams & right_grams) / min(len(left_grams), len(right_grams))


def _contract_leaf_items(value: Any, path: str = "") -> Iterable[tuple[str, str]]:
    """Yield stable scalar contract obligations without knowing story content."""

    if isinstance(value, dict):
        for key in sorted(value, key=str):
            child_path = f"{path}.{key}" if path else str(key)
            yield from _contract_leaf_items(value[key], child_path)
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            yield from _contract_leaf_items(item, f"{path}[{index}]")
        return
    text = _as_text(value)
    if len(_identity_text(text)) >= 4:
        yield path, text


def _matching_contract_ref(
    finding: dict[str, Any],
    authority_catalog: list[dict[str, Any]],
) -> str:
    finding_scene = _scene_index(finding)
    finding_text = _finding_semantic_text(finding)
    quoted = _quoted_obligation(finding)
    best_ref = ""
    best_score = 0.0
    for item in authority_catalog:
        item_scene = item.get("scene_index")
        if (
            isinstance(finding_scene, int)
            and isinstance(item_scene, int)
            and finding_scene != item_scene
        ):
            continue
        statement = str(item.get("statement") or "")
        score = max(
            _semantic_overlap(quoted, statement) if quoted else 0.0,
            _semantic_overlap(finding_text, statement),
        )
        if score > best_score:
            best_score = score
            best_ref = str(item.get("authority_ref") or "")
    # Contract matching is allowed to merge findings only when most of the
    # contract statement is visibly present in the finding.  Ambiguous matches
    # stay separate instead of collapsing unrelated obligations.
    return best_ref if best_score >= 0.72 else ""


def _desired_state(finding: dict[str, Any]) -> str:
    repair_goal = finding.get("repair_goal") if isinstance(finding.get("repair_goal"), dict) else {}
    repair_intent = finding.get("repair_intent") if isinstance(finding.get("repair_intent"), dict) else {}
    expected = finding.get("expected_after_repair")
    if isinstance(expected, dict):
        expected_value = _first_text(
            expected.get("desired_state"),
            expected.get("target_behavior"),
            expected.get("expected_behavior"),
            expected.get("required_state"),
        )
    else:
        expected_value = _as_text(expected)
    return _first_text(
        repair_goal.get("desired_state"),
        finding.get("desired_state"),
        finding.get("required_state"),
        finding.get("must_show"),
        finding.get("contract_obligation"),
        finding.get("expected_behavior"),
        repair_intent.get("target_behavior"),
        expected_value,
    )


def _prohibited_state(finding: dict[str, Any]) -> str:
    repair_goal = finding.get("repair_goal") if isinstance(finding.get("repair_goal"), dict) else {}
    return _first_text(
        repair_goal.get("prohibited_state"),
        finding.get("prohibited_state"),
        finding.get("forbidden_claims"),
        finding.get("forbidden"),
        finding.get("old_claims_to_remove_or_rewrite"),
        finding.get("old_text_claim"),
        finding.get("current_claim"),
    )


def _mutation_kind(
    finding: dict[str, Any],
    desired: str,
    prohibited: str,
    owner_scope: dict[str, Any],
) -> str:
    repair_goal = finding.get("repair_goal") if isinstance(finding.get("repair_goal"), dict) else {}
    repair_intent = finding.get("repair_intent") if isinstance(finding.get("repair_intent"), dict) else {}
    tool_blueprint = finding.get("tool_blueprint") if isinstance(finding.get("tool_blueprint"), dict) else {}
    explicit = str(
        repair_goal.get("mutation_kind")
        or finding.get("mutation_kind")
        or repair_intent.get("mutation_kind")
        or ""
    ).strip().lower()
    if explicit in {"replace", "delete", "insert", "rewrite_window", "global_transform"}:
        return explicit
    operation = str(
        tool_blueprint.get("operation")
        or repair_intent.get("preferred_operation")
        or finding.get("suggested_operation")
        or ""
    ).strip().lower()
    if operation in _OPERATION_TO_MUTATION:
        return _OPERATION_TO_MUTATION[operation]
    upstream_target = bool(
        finding.get("_location_supplied_upstream") is True
        and str(finding.get("target_span") or "").strip()
    )
    explicit_desired = bool(
        repair_goal.get("desired_state")
        or finding.get("desired_state")
        or finding.get("required_state")
        or finding.get("must_show")
        or finding.get("contract_obligation")
        or finding.get("expected_behavior")
    )
    if explicit_desired and desired and not prohibited and not upstream_target:
        return "insert"
    if owner_scope.get("scope") == "chapter" and not upstream_target:
        return "global_transform"
    if desired and prohibited:
        return "replace"
    if prohibited and not desired:
        return "delete"
    if (
        explicit_desired
        and desired
        and not str(finding.get("target_span") or "").strip()
    ):
        return "insert"
    return "rewrite_window"


def _finding_without_runtime_text(finding: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in finding.items()
        if key not in {"_candidate_text", "fbi_diagnosis", "revision_blueprint", "tool_blueprint"}
    }


def _validate_write_anchor(
    anchor: Any,
    scene_texts: dict[int, str],
    default_scene_index: int | None,
) -> dict[str, Any]:
    """Validate an upstream anchor against the frozen scene text."""

    if not isinstance(anchor, dict):
        return {}
    if not (
        anchor.get("validated") is True
        or str(anchor.get("status") or "").lower() in {"validated", "resolved"}
    ):
        return {}
    scene_index = anchor.get("scene_index", default_scene_index)
    if not isinstance(scene_index, int):
        return {}
    text = str(anchor.get("text") or "").strip()
    scene_text = scene_texts.get(scene_index, "")
    if not text or not scene_text or scene_text.count(text) != 1:
        return {}
    start = scene_text.find(text)
    declared_start = anchor.get("start")
    declared_end = anchor.get("end")
    if isinstance(declared_start, int) and declared_start != start:
        return {}
    if isinstance(declared_end, int) and declared_end != start + len(text):
        return {}
    return {
        **anchor,
        "scene_index": scene_index,
        "text": text,
        "start": start,
        "end": start + len(text),
        "validated": True,
    }


class RepairGoalGrouper:
    """Deduplicate findings by contract obligation, not by evidence window."""

    @staticmethod
    def authority_catalog(
        *,
        scene_contracts: dict[int, dict[str, Any]] | None = None,
        chapter_contract: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Build stable authority references from the contracts under review."""

        catalog: list[dict[str, Any]] = []
        for scene_index, contract in sorted((scene_contracts or {}).items()):
            for path, statement in _contract_leaf_items(contract):
                catalog.append({
                    "authority_ref": f"scene_contract:{scene_index}:{path}",
                    "scene_index": scene_index,
                    "statement": statement,
                })
        for path, statement in _contract_leaf_items(chapter_contract or {}):
            catalog.append({
                "authority_ref": f"chapter_contract:{path}",
                "scene_index": None,
                "statement": statement,
            })
        return catalog

    def group_findings(
        self,
        findings: list[dict[str, Any]],
        *,
        authority_catalog: list[dict[str, Any]] | None = None,
    ) -> tuple[list[FBIRepairGoal], dict[str, Any]]:
        authority_catalog = authority_catalog or []
        buckets: dict[str, dict[str, Any]] = {}
        for finding in findings:
            if not isinstance(finding, dict):
                continue
            desired = _desired_state(finding)
            prohibited = _prohibited_state(finding)
            if not desired and not prohibited:
                quoted_obligation = _quoted_obligation(finding)
                finding_type = str(
                    finding.get("type") or finding.get("violation_type") or ""
                ).strip().lower()
                metric = str(finding.get("metric") or "").strip().lower()
                desired = _first_text(
                    quoted_obligation,
                    finding.get("detail"),
                    finding.get("reason"),
                    metric,
                    finding_type,
                )
            owner_scope = _owner_scope(finding)
            mutation_kind = _mutation_kind(finding, desired, prohibited, owner_scope)
            explicit_ref = _explicit_obligation_ref(finding)
            catalog_ref = _matching_contract_ref(finding, authority_catalog)
            authority_ref = explicit_ref or catalog_ref or _authority_ref(
                finding, desired, prohibited
            )
            authoritative_identity = bool(explicit_ref or catalog_ref)
            identity = {
                "authority_ref": _identity_text(authority_ref),
                "owner_scope": owner_scope,
                "mutation_kind": mutation_kind,
                # A stable obligation/contract reference is the semantic
                # identity.  Validator paraphrases must not split it.  Without
                # such a reference, retain exact normalized states and prefer a
                # safe under-merge over collapsing unrelated categories.
                "desired_state": "" if authoritative_identity else _identity_text(desired),
                "prohibited_state": "" if authoritative_identity else _identity_text(prohibited),
            }
            key = _stable_hash(identity)
            bucket = buckets.setdefault(key, {
                "identity": identity,
                "authority_ref": authority_ref,
                "owner_scope": owner_scope,
                "mutation_kind": mutation_kind,
                "desired_state": desired,
                "prohibited_state": prohibited,
                "source_issue_ids": [],
                "source_findings": [],
                "owner_scene_indices": [],
                "blocks_commit": False,
            })
            issue_id = _issue_id(finding)
            if issue_id:
                bucket["source_issue_ids"].append(issue_id)
            bucket["source_findings"].append(_finding_without_runtime_text(finding))
            finding_scene = _scene_index(finding)
            if finding_scene is not None:
                bucket["owner_scene_indices"].append(finding_scene)
            bucket["blocks_commit"] = bool(
                bucket["blocks_commit"] or finding.get("blocks_commit", True)
            )

        goals: list[FBIRepairGoal] = []
        for key in sorted(
            buckets,
            key=lambda item: (
                0 if buckets[item]["owner_scope"].get("scope") == "scene" else 1,
                buckets[item]["owner_scope"].get("scene_index", -1),
                item,
            ),
        ):
            bucket = buckets[key]
            source_findings = sorted(
                bucket["source_findings"],
                key=lambda item: (
                    _identity_text(
                        item.get("metric")
                        or item.get("type")
                        or item.get("violation_type")
                        or ""
                    ),
                    _identity_text(item.get("detail") or item.get("reason") or ""),
                    _issue_id(item),
                ),
            )
            final_owner_scope = dict(bucket["owner_scope"])
            if final_owner_scope.get("scope") == "chapter":
                scene_indices = sorted(set(bucket["owner_scene_indices"]))
                if scene_indices:
                    final_owner_scope["scene_indices"] = scene_indices
            goals.append(FBIRepairGoal(
                goal_id=f"goal_{key}",
                authority_ref=bucket["authority_ref"],
                owner_scope=final_owner_scope,
                mutation_kind=bucket["mutation_kind"],
                desired_state=bucket["desired_state"],
                prohibited_state=bucket["prohibited_state"],
                source_issue_ids=sorted(set(bucket["source_issue_ids"])),
                source_findings=source_findings,
                blocks_commit=bool(bucket["blocks_commit"]),
            ))
        return goals, {
            "component": "repair_goal_grouper",
            "input_findings": len([item for item in findings if isinstance(item, dict)]),
            "repair_goals": len(goals),
            "duplicate_findings_avoided": max(0, len(findings) - len(goals)),
            "goal_ids": [goal.goal_id for goal in goals],
        }

    def placement_for_goal(
        self,
        goal: FBIRepairGoal,
        scene_texts: dict[int, str],
    ) -> FBIPlacementDecision:
        scene_index = goal.owner_scope.get("scene_index")
        scene_text = scene_texts.get(scene_index, "") if isinstance(scene_index, int) else ""
        read_context_spans: list[dict[str, Any]] = []
        if isinstance(scene_index, int):
            read_context_spans.append({
                "scope": "scene",
                "scene_index": scene_index,
                "start": 0,
                "end": len(scene_text),
                "complete": True,
            })
        else:
            for index in sorted(scene_texts):
                read_context_spans.append({
                    "scope": "scene",
                    "scene_index": index,
                    "start": 0,
                    "end": len(scene_texts[index]),
                    "complete": True,
                })

        evidence_spans: list[dict[str, Any]] = []
        validated_anchors: dict[tuple[Any, ...], dict[str, Any]] = {}
        for finding in goal.source_findings:
            validated_anchor = _validate_write_anchor(
                finding.get("write_anchor"), scene_texts, scene_index
            )
            if validated_anchor:
                anchor_key = (
                    validated_anchor.get("scene_index"),
                    validated_anchor.get("start"),
                    validated_anchor.get("end"),
                    validated_anchor.get("text"),
                )
                validated_anchors[anchor_key] = validated_anchor
            for key in ("target_span", "evidence_span"):
                text = str(finding.get(key) or "").strip()
                if text:
                    evidence_spans.append({
                        "scene_index": _scene_index(finding),
                        "text": text,
                        "role": "diagnostic_evidence",
                        "source_issue_id": _issue_id(finding),
                    })
            raw_spans = finding.get("evidence_spans") or []
            if isinstance(raw_spans, list):
                for span in raw_spans:
                    if isinstance(span, dict):
                        text = str(span.get("text") or span.get("span") or "").strip()
                    else:
                        text = str(span or "").strip()
                    if text:
                        evidence_spans.append({
                            "scene_index": _scene_index(finding),
                            "text": text,
                            "role": "diagnostic_evidence",
                            "source_issue_id": _issue_id(finding),
                        })

        status = "required"
        confidence = ""
        source = "chapter_level_llm_placement_required"
        reason = "diagnostic_evidence_is_not_a_write_anchor"
        write_anchor: dict[str, Any] = {}
        if len(validated_anchors) == 1:
            write_anchor = next(iter(validated_anchors.values()))
            status = "resolved"
            confidence = str(write_anchor.get("confidence") or "high")
            source = "upstream_validated_write_anchor"
            reason = ""
        elif len(validated_anchors) > 1:
            reason = "conflicting_validated_write_anchors_require_placement"
        elif goal.mutation_kind in {"replace", "delete"}:
            mutation_targets = [
                str(item.get("target_span") or "").strip()
                for item in goal.source_findings
                if item.get("_location_supplied_upstream") is True
                and str(item.get("target_span") or "").strip()
            ]
            unique_targets = sorted(set(mutation_targets))
            if len(unique_targets) == 1 and scene_text and scene_text.count(unique_targets[0]) == 1:
                text = unique_targets[0]
                start = scene_text.find(text)
                write_anchor = {
                    "scene_index": scene_index,
                    "text": text,
                    "start": start,
                    "end": start + len(text),
                    "validated": True,
                    "kind": "explicit_mutation_target",
                }
                status = "resolved"
                confidence = "high"
                source = "upstream_explicit_mutation_target"
                reason = ""

        placement_id = f"place_{_stable_hash([goal.goal_id, status, write_anchor])}"
        return FBIPlacementDecision(
            placement_id=placement_id,
            status=status,
            scene_index=scene_index if isinstance(scene_index, int) else None,
            read_context_spans=read_context_spans,
            diagnostic_evidence_spans=evidence_spans,
            write_anchor=write_anchor,
            confidence=confidence,
            source=source,
            reason=reason,
        )
