from __future__ import annotations

import hashlib
from typing import Any

from app.models.review_case_file import ReviewCaseFile
from app.models.review_case_file import ReviewCaseIssue
from app.services.review_case_file import final_gate_case_delta
from app.services.review_issue_localizer import enrich_review_issue_locations
from app.services.skill_metric_repair_registry import get_metric_spec


def build_final_acceptance_case_delta(
    *,
    project_id: str,
    chapter_number: int,
    final_text: str,
    final_gate_result: dict[str, Any],
    acceptance_round: int = 0,
    parent_case_id: str = "",
    scene_texts: dict[int, str] | None = None,
) -> dict[str, Any]:
    """Build the FBI-owned delta emitted by Final Acceptance.

    Final Acceptance remains a gate. This adapter only turns gate evidence into
    a repair-ready FBI case delta; it never repairs prose.
    """
    raw_delta = final_gate_case_delta(
        project_id=project_id,
        chapter_number=chapter_number,
        final_text=final_text,
        final_gate_result=final_gate_result,
    )
    return enrich_final_acceptance_case_delta(
        raw_delta,
        final_text=final_text,
        final_gate_result=final_gate_result,
        acceptance_round=acceptance_round,
        parent_case_id=parent_case_id,
        scene_texts=scene_texts,
    )


def enrich_final_acceptance_case_delta(
    case_delta: dict[str, Any],
    *,
    final_text: str,
    final_gate_result: dict[str, Any] | None = None,
    acceptance_round: int = 0,
    parent_case_id: str = "",
    scene_texts: dict[int, str] | None = None,
) -> dict[str, Any]:
    case_file = ReviewCaseFile.model_validate(case_delta or {})
    trace = dict(case_file.review_trace or {})
    final_trace = {
        "department": "fbi",
        "unit": "final_acceptance",
        "acceptance_round": acceptance_round,
        "allowed": bool((final_gate_result or {}).get("allowed", False)),
        "reason": str((final_gate_result or {}).get("reason") or ""),
    }

    localized = 0
    unlocalized = 0
    signatures: list[str] = []
    for issue in case_file.all_issues():
        issue.source = issue.source or "final_acceptance"
        issue.sources = sorted(set([*issue.sources, issue.source, "final_acceptance"]))
        evidence = dict(issue.evidence or {})
        evidence.setdefault("origin_step", "fbi_final_acceptance")
        evidence.setdefault("acceptance_round", acceptance_round)
        if parent_case_id:
            evidence.setdefault("parent_case_id", parent_case_id)
        for key in ("actual", "expected", "expected_min", "expected_max", "action"):
            if key not in evidence:
                value = _lookup_failure_value(final_gate_result or {}, issue, key)
                if value is not None:
                    evidence[key] = value
        signature = _failure_signature(issue, evidence)
        evidence["failure_signature"] = signature
        signatures.append(signature)

        localized_issue = enrich_review_issue_locations(
            {
                "type": issue.type,
                "violation_type": issue.type,
                "metric": issue.metric,
                "validator": issue.source_validator,
                "source_validator": issue.source_validator,
                "skill_id": issue.skill_id,
                "target_span": issue.target_span,
                "evidence_spans": evidence.get("evidence_spans") or [],
                "detail": issue.detail,
                "blocks_commit": issue.blocks_commit,
            },
            final_text,
        )
        if localized_issue.get("target_span") and not issue.target_span:
            issue.target_span = str(localized_issue.get("target_span") or "")
        if issue.scene_index is None and issue.target_span:
            owner_scene = _infer_owner_scene(issue.target_span, scene_texts or {})
            if owner_scene is not None:
                issue.scene_index = owner_scene
                issue.scope = "scene"
        for key in (
            "evidence_spans",
            "repair_granularity",
            "localization_status",
            "location_confidence",
            "recommended_route",
            "repair_scope",
        ):
            if key in localized_issue and localized_issue.get(key) is not None:
                evidence[key] = localized_issue.get(key)
        if evidence.get("localization_status") == "localized":
            localized += 1
        else:
            evidence.setdefault("localization_status", "pending")
            unlocalized += 1
        issue.evidence = evidence
        _apply_reliability_policy(issue)

    # 通用修复（循环 #8 DD-1）：同一 target_span 的多维度 blocking 违规去重
    # 根因：多个验证维度（pure_exposition_block_chars / emotion_label_count /
    #   standalone_abstract_claims）对同一 target_span 报告 blocking，但部分维度
    #   并不适用（例如 emotion_label_count 报告的句子没有情绪标签词）。
    # 修复：按 (target_span, repair_domain) 分组，同一组的多个 blocking issue
    #   只保留第一个，其他降级为 advisory（blocks_commit=False）。
    # 通用性：适用于所有题材——同一文本片段的多个维度违规本质上是同一问题，
    #   修复一个维度通常改善其他维度，保留一个 blocking 确保真实问题不被忽略。
    _dedupe_blocking_by_target_span(case_file)

    final_trace.update({
        "blocking_failure_count": sum(1 for item in case_file.all_issues() if item.blocks_commit),
        "localized_issue_count": localized,
        "unlocalized_issue_count": unlocalized,
        "failure_signature": _signature_summary(signatures),
    })
    trace["fbi_final_acceptance"] = final_trace
    case_file.review_trace = trace
    return case_file.model_dump()


def _apply_reliability_policy(issue: ReviewCaseIssue) -> None:
    evidence = dict(issue.evidence or {})
    localization = str(evidence.get("localization_status") or "")
    confidence_value = evidence.get("location_confidence", evidence.get("confidence"))
    confidence = str(confidence_value or "")
    low_numeric_confidence = isinstance(confidence_value, (int, float)) and float(confidence_value) < 0.6
    metric = str(issue.metric or issue.type or "").lower()
    repair_domain = str(issue.repair_domain or "").lower()
    metric_spec = get_metric_spec(metric)
    # 通用修复（循环 #13）：requires_target_span=False 的 metric 跳过定位检查
    # 根因：_apply_reliability_policy 无条件要求 target_span 非空 + confidence≥0.6，
    #   导致 requires_target_span=False 的"缺少内容"型 metric（如 temporal_anchor_count=0，
    #   场景缺少时间锚点）被误降级为 manual_review + blocks_commit=False，
    #   无法进入自动修复候选。但终验技能验证仍检测到该问题，形成阻断死循环。
    #   此类 metric 的修复工具（如 insert_time_anchor）不需要精确定位即可工作，
    #   跳过定位检查后 has_executable_metric_tool=True → 保持 blocking + 可自动修复。
    # 通用性：任何 requires_target_span=False 的 metric 都会受益，不针对特定类型。
    requires_no_target_span = (
        metric_spec is not None
        and not metric_spec.requires_target_span
    )
    metric_location_sufficient = _metric_location_is_sufficient(metric, evidence)
    exact_multi_span_location = (
        metric in {
            "paragraph_shape_repeat_count",
            "semantic_restatement_count",
            "uniform_sentence_streak_max",
            "dense_paragraph_streak_max",
        }
        and metric_location_sufficient
    )
    has_executable_metric_tool = (
        metric_spec is not None
        and metric_spec.status in {"implemented_tool", "implemented_candidate_tool"}
        and bool(metric_spec.operation)
        and (requires_no_target_span or (
            bool(str(issue.target_span or "").strip())
            and metric_location_sufficient
            # 通用修复 S-4：fallback 定位（confidence < 0.6）不应视为"有可执行修复工具"。
            # 根因：localizer 对"缺少内容"型违规（如 temporal_anchor_count=0，场景缺少
            #   时间锚点）无法定位，fall through 到 _paragraph_fallback 返回最长段落作为
            #   target_span（confidence=0.48）。这个 fallback span 对修复无意义（违规语义
            #   是"缺少"而非"有错"），但 has_executable_metric_tool 判定为 True 导致
            #   issue 保持 blocking，soft_skill_failure_policy 不放行，工作流死循环。
            # 通用性：任何 metric 如果定位 confidence 低于 0.6，fallback span 对修复工具
            #   无实际定位价值，不应阻断 commit；走 weak_location 分支降级为 advisory。
            and (not low_numeric_confidence or exact_multi_span_location)
        ))
    )
    deterministic_metrics = {
        "dash_density",
        "dash_per_1000",
        "ai_punctuation_artifact",
        "explanatory_punctuation_artifact",
    }
    broad_style_metrics = {
        "paragraph_shape_repeat_count",
        "mirrored_paragraph_opening_count",
        "standalone_abstract_claims",
        "abrupt_shift_count",
        "breathing_paragraph_ratio",
        "dense_paragraph_streak_max",
        "uniform_sentence_streak_max",
    }
    weak_location = (
        not requires_no_target_span
        and (
            localization != "localized"
            or confidence in {"low", "ambiguous", "pending"}
            or (low_numeric_confidence and not exact_multi_span_location)
            or not str(issue.target_span or "").strip()
        )
    )
    if metric in deterministic_metrics:
        evidence.setdefault("enforcement", "commit_blocking")
        issue.evidence = evidence
        return
    if has_executable_metric_tool:
        evidence.setdefault("enforcement", "commit_blocking")
        issue.evidence = evidence
        return
    if weak_location and (metric in broad_style_metrics or repair_domain in {"anti_ai", "rhythm", "surface"}):
        issue.blocks_commit = False
        issue.severity = "medium" if issue.severity in {"critical", "high"} else issue.severity
        issue.repairability = "manual_review"
        evidence["enforcement"] = "advisory_trace"
        evidence["reliability_policy"] = "unlocalized_or_low_confidence_not_auto_fixable"
    else:
        evidence.setdefault("enforcement", "commit_blocking")
    issue.evidence = evidence


def _dedupe_blocking_by_target_span(case_file: ReviewCaseFile) -> None:
    """同一 target_span + repair_domain 的多个 blocking issue 只保留第一个。

    场景：pure_exposition_block_chars / emotion_label_count /
    standalone_abstract_claims 可能对同一 target_span 报告 blocking，但部分
    维度并不适用（例如 emotion_label_count 报告的句子没有情绪标签词）。
    保留第一个 blocking，其他降级为 advisory，避免重复修复和误报叠加。

    通用性：适用于所有题材——同一文本片段的多个维度违规本质上是同一问题，
    修复一个维度通常改善其他维度。不同 repair_domain 的问题不会互相吞并。
    """
    groups: dict[tuple[str, str], list] = {}
    for issue in case_file.all_issues():
        if not issue.blocks_commit:
            continue
        span = str(issue.target_span or "").strip()
        if not span:
            continue
        domain = str(getattr(issue, "repair_domain", "") or "").lower()
        key = (span, domain)
        groups.setdefault(key, []).append(issue)

    for key, issues in groups.items():
        if len(issues) <= 1:
            continue
        primary = issues[0]
        for issue in issues[1:]:
            issue.blocks_commit = False
            if issue.severity in {"critical", "high"}:
                issue.severity = "medium"
            evidence = dict(issue.evidence or {})
            evidence["dedupe_reason"] = (
                "same_target_span_as_another_blocking_issue: "
                "downgraded to advisory to avoid duplicate repair"
            )
            evidence["dedupe_primary_metric"] = str(primary.metric or primary.type or "")
            evidence["reliability_policy"] = "deduped_same_target_span"
            issue.evidence = evidence


def _metric_location_is_sufficient(metric: str, evidence: dict[str, Any]) -> bool:
    spans = [
        item for item in evidence.get("evidence_spans") or []
        if isinstance(item, dict) and str(item.get("span") or "").strip()
    ]
    if metric in {"paragraph_shape_repeat_count", "semantic_restatement_count"}:
        return len(spans) >= 2
    if metric in {"uniform_sentence_streak_max", "dense_paragraph_streak_max"}:
        return len(spans) >= 1
    return True


def build_commit_health_case_delta(
    *,
    project_id: str,
    chapter_number: int,
    final_text: str,
    health_result: dict[str, Any],
    acceptance_round: int = 0,
    parent_case_id: str = "",
    scene_texts: dict[int, str] | None = None,
) -> dict[str, Any]:
    draft_hash = hashlib.sha256((final_text or "").encode("utf-8")).hexdigest()[:16]
    issues: list[ReviewCaseIssue] = []
    for raw_issue in health_result.get("hard_issues") or []:
        if not isinstance(raw_issue, dict):
            continue
        scene_index = raw_issue.get("scene_index")
        if not isinstance(scene_index, int):
            scene_index = None
        issue_type = str(raw_issue.get("code") or "commit_health_failed")
        target_span, evidence_spans = _commit_health_location(
            issue_type,
            scene_index=scene_index,
            scene_texts=scene_texts or {},
            final_text=final_text,
        )
        evidence = {
            "actual": raw_issue.get("code"),
            "expected": "commit_health_clean",
            "origin_step": "fbi_final_acceptance",
            "acceptance_round": acceptance_round,
            "failure_signature": _commit_health_signature(raw_issue, target_span),
            "localization_status": "localized" if target_span else "pending",
            "repair_granularity": "scene_window" if scene_index is not None else "chapter_window",
            "evidence_spans": evidence_spans,
        }
        if parent_case_id:
            evidence["parent_case_id"] = parent_case_id
        issues.append(ReviewCaseIssue(
            source="final_acceptance",
            source_validator="commit_health",
            skill_id="",
            scene_index=scene_index,
            scope="scene" if scene_index is not None else "chapter",
            type=issue_type,
            metric=issue_type,
            severity="critical",
            blocks_commit=True,
            repairability=_commit_health_repairability(issue_type),
            repair_domain=_commit_health_repair_domain(issue_type),
            detail=str(raw_issue.get("detail") or issue_type),
            target_span=target_span,
            expected_behavior=_commit_health_expected_behavior(issue_type),
            evidence=evidence,
            dedupe_key=(
                f"{'scene:' + str(scene_index) if scene_index is not None else 'chapter'}:"
                f"{_commit_health_repair_domain(issue_type)}:{issue_type}:{target_span}"
            ),
            sources=["final_acceptance", "commit_health"],
        ))
    case_file = ReviewCaseFile(
        project_id=project_id,
        chapter_number=chapter_number,
        draft_hash=draft_hash,
        created_from="final_acceptance",
        chapter_issues=[issue for issue in issues if issue.scene_index is None],
        scene_issues=[issue for issue in issues if issue.scene_index is not None],
        review_trace={
            "fbi_final_acceptance": {
                "department": "fbi",
                "unit": "final_acceptance",
                "acceptance_round": acceptance_round,
                "allowed": False,
                "reason": "commit health failed",
                "blocking_failure_count": len(issues),
                "localized_issue_count": sum(1 for issue in issues if issue.target_span),
                "unlocalized_issue_count": sum(1 for issue in issues if not issue.target_span),
                "failure_signature": _signature_summary([
                    str(issue.evidence.get("failure_signature") or "")
                    for issue in issues
                ]),
            }
        },
    )
    case_file.case_id = "rcf_" + hashlib.md5(
        f"{project_id}:{chapter_number}:{draft_hash}:final_acceptance:commit_health".encode("utf-8")
    ).hexdigest()[:16]
    for issue in case_file.all_issues():
        if not issue.issue_id:
            issue.issue_id = "iss_" + hashlib.md5(issue.stable_key().encode("utf-8")).hexdigest()[:12]
    return case_file.model_dump()


def _lookup_failure_value(
    final_gate_result: dict[str, Any],
    issue,
    key: str,
) -> Any:
    trace = final_gate_result.get("trace") or {}
    validation = trace.get("repair", {}).get("validation") or trace.get("initial_validation") or {}
    for failure in validation.get("failures") or []:
        if not isinstance(failure, dict):
            continue
        metric = str(failure.get("metric") or failure.get("type") or "")
        validator = str(failure.get("validator") or failure.get("source_validator") or "")
        if metric == issue.metric and (not issue.source_validator or validator == issue.source_validator):
            return failure.get(key)
    return None


def _failure_signature(issue, evidence: dict[str, Any]) -> str:
    raw = "|".join(
        str(part or "")
        for part in (
            issue.source_validator,
            issue.skill_id,
            issue.type,
            issue.metric,
            evidence.get("actual"),
            evidence.get("expected"),
            evidence.get("expected_min"),
            evidence.get("expected_max"),
            issue.target_span,
        )
    )
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]


def _signature_summary(signatures: list[str]) -> str:
    raw = "|".join(sorted(set(signatures)))
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:12] if raw else ""


def _commit_health_repairability(issue_type: str) -> str:
    if issue_type in {"residual_scene_marker"}:
        return "deterministic"
    if issue_type in {"internal_scene_separator"}:
        return "scene_rewrite"
    return "manual_review"


def _commit_health_repair_domain(issue_type: str) -> str:
    if issue_type in {"residual_scene_marker", "internal_scene_separator", "empty_scene_text"}:
        return "structure"
    if issue_type == "style_requires_human_review":
        return "surface"
    return "manual"


def _commit_health_expected_behavior(issue_type: str) -> str:
    return {
        "residual_scene_marker": "Remove residual scene/chapter markers from prose.",
        "internal_scene_separator": "Keep each aligned scene as one scene without full-chapter separators.",
        "empty_scene_text": "Scene text must be non-empty before commit.",
        "empty_chapter_state": "Chapter state must contain facts, events, character states, or constraints for substantial final text.",
        "style_requires_human_review": "Resolve or explicitly accept style polish human-review requirement.",
    }.get(issue_type, "Commit health issue must be resolved before chapter write.")


def _commit_health_location(
    issue_type: str,
    *,
    scene_index: int | None,
    scene_texts: dict[int, str],
    final_text: str,
) -> tuple[str, list[dict]]:
    text = scene_texts.get(scene_index, "") if scene_index is not None else final_text
    if not text:
        return "", []
    if issue_type == "residual_scene_marker":
        import re

        match = re.search(r"(?im)^\s*(?:\[\[SCENE:[^\]]+\]\]|<<<SCENE_ID:[^>]+>>>|Scene\s*\d+\s*[:：]?)\s*$", text)
        if match:
            span = match.group(0).strip()
            return span, [{"span": span, "start": match.start(), "end": match.end(), "role": "commit_health_marker"}]
    if issue_type == "internal_scene_separator":
        import re

        match = re.search(r"(?m)^\s*(?:-{3,}|\*{3,}|#{2,}\s+\S.*)\s*$", text)
        if match:
            start = max(0, match.start() - 180)
            end = min(len(text), match.end() + 180)
            span = text[start:end].strip()
            return span, [{"span": span, "start": start, "end": end, "role": "commit_health_separator"}]
    clipped = text.strip()[:520]
    if clipped:
        return clipped, [{"span": clipped, "start": 0, "end": min(len(text), 520), "role": "commit_health_window"}]
    return "", []


def _commit_health_signature(raw_issue: dict[str, Any], target_span: str) -> str:
    raw = "|".join(
        str(part or "")
        for part in (
            raw_issue.get("code"),
            raw_issue.get("scene_index"),
            raw_issue.get("detail"),
            target_span,
        )
    )
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]


def _infer_owner_scene(target_span: str, scene_texts: dict[int, str]) -> int | None:
    span = _compact(target_span)
    if not span:
        return None
    best_scene: int | None = None
    best_score = 0
    for scene_index, text in scene_texts.items():
        compact_text = _compact(text)
        if not compact_text:
            continue
        if span in compact_text:
            return scene_index
        overlap = _longest_common_substring_len(span[:260], compact_text)
        if overlap > best_score:
            best_score = overlap
            best_scene = scene_index
    return best_scene if best_score >= min(32, max(12, len(span) // 4)) else None


def _compact(text: str) -> str:
    return "".join(str(text or "").split())


def _longest_common_substring_len(left: str, right: str) -> int:
    if not left or not right:
        return 0
    previous = [0] * (len(right) + 1)
    best = 0
    for left_char in left:
        current = [0]
        for index, right_char in enumerate(right, start=1):
            value = previous[index - 1] + 1 if left_char == right_char else 0
            current.append(value)
            if value > best:
                best = value
        previous = current
    return best
