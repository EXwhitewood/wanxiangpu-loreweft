import copy
import json
import logging
import uuid
import asyncio
import hashlib
import difflib
import re
import time
from typing import Any

from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.db_models import get_db, Project, WorkflowExecution, WorkflowStep
from app.agents.editor_in_chief import EditorInChiefAgent
from app.models.story_state import StoryState
from app.services.cross_system_event_bus import CrossSystemEventBus
from app.services.generation_candidate_service import (
    build_chapter_candidate_payload,
    classify_candidate_block,
)
from app.services.review_issue_semantics import issue_fingerprint, normalize_violation_semantics
from app.services.review_issue_localizer import enrich_review_issue_locations
from app.services.scene_generation_pipeline import (
    extract_chapter_facts,
    normalize_editor_scene_contracts,
    run_scene_postprocess_fanout,
    run_scene_pipeline,
)
from app.services.text_coercion import ensure_complete_sentence_ending
from app.utils.json_safety import to_json_safe
from app.utils.word_count import count_words

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    """Return a UTC-naive timestamp for legacy naive database columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


router = APIRouter()

_WORKFLOW_TASKS: dict[str, set[asyncio.Task]] = {}
_WORKFLOW_TASK_ROLES: dict[tuple[str, str], asyncio.Task] = {}
_WORKFLOW_HEARTBEAT_INTERVAL_SECONDS = 30
_WORKFLOW_STALE_RUNNING_SECONDS = 20 * 60
_CHAPTER_WRITER_TIMEOUT_SECONDS = 18 * 60


def _spawn_workflow_task(
    execution_id: str,
    coro,
    *,
    role: str = "workflow",
) -> asyncio.Task:
    execution_key = str(execution_id)
    role_key = (execution_key, str(role or "workflow"))
    existing = _WORKFLOW_TASK_ROLES.get(role_key)
    if existing is not None and not existing.done():
        close = getattr(coro, "close", None)
        if callable(close):
            close()
        return existing

    task = asyncio.create_task(coro)
    if not hasattr(task, "add_done_callback"):
        return task
    tasks = _WORKFLOW_TASKS.setdefault(execution_key, set())
    tasks.add(task)
    _WORKFLOW_TASK_ROLES[role_key] = task

    def _cleanup(done_task: asyncio.Task) -> None:
        tracked = _WORKFLOW_TASKS.get(execution_key)
        if tracked is not None:
            tracked.discard(done_task)
            if not tracked:
                _WORKFLOW_TASKS.pop(execution_key, None)
        if _WORKFLOW_TASK_ROLES.get(role_key) is done_task:
            _WORKFLOW_TASK_ROLES.pop(role_key, None)

    task.add_done_callback(_cleanup)
    return task


def _cancel_workflow_tasks(execution_id: str) -> int:
    tasks = list(_WORKFLOW_TASKS.get(str(execution_id)) or [])
    cancelled = 0
    for task in tasks:
        if task.done():
            continue
        task.cancel()
        cancelled += 1
    return cancelled


async def _await_cancelled_workflow_tasks(tasks: list[asyncio.Task]) -> None:
    current = asyncio.current_task()
    awaitable_tasks = [
        task
        for task in tasks
        if task is not current and isinstance(task, asyncio.Future)
    ]
    if awaitable_tasks:
        await asyncio.gather(*awaitable_tasks, return_exceptions=True)


def _workflow_chapter_number(execution: WorkflowExecution) -> int | None:
    input_context = execution.input_context if isinstance(execution.input_context, dict) else {}
    result_context = execution.result_context if isinstance(execution.result_context, dict) else {}
    candidates = [
        input_context.get("chapter_number"),
        result_context.get("chapter_number"),
        (result_context.get("resume_state") or {}).get("chapter_number")
        if isinstance(result_context.get("resume_state"), dict)
        else None,
        (result_context.get("review") or {}).get("chapter_number")
        if isinstance(result_context.get("review"), dict)
        else None,
    ]
    for candidate in candidates:
        try:
            number = int(candidate)
        except (TypeError, ValueError):
            continue
        if number > 0:
            return number
    match = re.search(r"(?:^|[_-])ch(?:apter)?[_-]?(\d+)(?:$|[_-])", execution.trigger_type or "", re.I)
    if match:
        return int(match.group(1))
    return None


async def _touch_workflow_heartbeat(execution_id: str, agent_name: str) -> bool:
    """Update workflow liveness from a separate session.

    Long generation steps can spend minutes inside an LLM call or downstream
    validation. The main DB session must not be shared concurrently, so this
    heartbeat uses a short-lived session and only touches liveness fields.
    """
    from app.db.db_models import async_session

    now = _utcnow()
    async with async_session() as heartbeat_db:
        exec_query = await heartbeat_db.execute(
            select(WorkflowExecution).where(WorkflowExecution.id == execution_id)
        )
        execution = exec_query.scalar_one_or_none()
        if not execution or execution.status != "running":
            return False
        step_query = await heartbeat_db.execute(
            select(WorkflowStep).where(
                WorkflowStep.execution_id == execution_id,
                WorkflowStep.agent_name == agent_name,
            )
        )
        step = step_query.scalar_one_or_none()
        if step and step.status == "running":
            execution.updated_at = now
            output = dict(step.output_snapshot or {})
            output["heartbeat_at"] = now.isoformat()
            step.output_snapshot = to_json_safe(output)
            await heartbeat_db.commit()
            return True
        return False


async def _await_workflow_operation(
    execution_id: str,
    agent_name: str,
    awaitable,
    *,
    timeout_seconds: int | None = None,
):
    """Await a long operation with heartbeat and optional timeout."""
    heartbeat_task: asyncio.Task | None = None

    async def _heartbeat_loop() -> None:
        try:
            while True:
                await asyncio.sleep(_WORKFLOW_HEARTBEAT_INTERVAL_SECONDS)
                keep_going = await _touch_workflow_heartbeat(execution_id, agent_name)
                if not keep_going:
                    return
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Workflow heartbeat failed: execution=%s step=%s", execution_id, agent_name)

    heartbeat_task = asyncio.create_task(_heartbeat_loop())
    try:
        if timeout_seconds and timeout_seconds > 0:
            return await asyncio.wait_for(awaitable, timeout=timeout_seconds)
        return await awaitable
    except asyncio.TimeoutError as exc:
        raise TimeoutError(f"{agent_name} exceeded {timeout_seconds}s timeout") from exc
    finally:
        if heartbeat_task:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass


async def _cleanup_stale_running_workflows(
    db: AsyncSession,
    *,
    project_id: uuid.UUID | str | None = None,
    stale_after_seconds: int = _WORKFLOW_STALE_RUNNING_SECONDS,
) -> int:
    """Fail running workflows whose liveness timestamp stopped advancing."""
    now = _utcnow()
    cutoff = now - timedelta(seconds=max(1, stale_after_seconds))
    query = select(WorkflowExecution).where(WorkflowExecution.status == "running")
    if project_id is not None:
        query = query.where(WorkflowExecution.project_id == str(project_id))
    result = await db.execute(query)
    stale: list[WorkflowExecution] = []
    for execution in result.scalars().all():
        last_seen = execution.updated_at or execution.created_at
        if last_seen is None or last_seen < cutoff:
            stale.append(execution)

    for execution in stale:
        execution_id = str(execution.id)
        cancelled_tasks = _cancel_workflow_tasks(execution_id)
        execution.status = "failed"
        execution.error_message = (
            "Workflow stale running timed out; no heartbeat or progress was recorded"
            f" for {stale_after_seconds}s"
        )
        execution.updated_at = now
        await _finalize_open_workflow_steps(
            execution_id,
            status="failed",
            error=execution.error_message,
            db=db,
        )
        _broadcast_workflow_event(execution_id, {
            "type": "execution_update",
            "status": "failed",
            "reason": "stale_running_timeout",
            "cancelled_tasks": cancelled_tasks,
            "timestamp": now.isoformat(),
        })

    if stale:
        await db.commit()
    return len(stale)


class EditorChatMessage(BaseModel):
    role: str
    content: str


class EditorChatRequest(BaseModel):
    messages: list[EditorChatMessage]
    chapter_number: int = 1


class EditorSettingsRequest(BaseModel):
    token_budget: int | None = None
    budget_mode: str | None = None


class GenerateFromChatRequest(BaseModel):
    chapter_number: int = 1
    pov_character: str | None = None
    custom_instructions: str | None = None
    expected_blueprint_revision: int | None = None


class ContextEnvelopeRequest(BaseModel):
    chapter_number: int = 1
    target_agent: str = "chapter_writer"
    manual_focus: str | None = None
    profile_override: dict | None = None
    dry_run: bool = False


class ResumeWorkflowReviewRequest(BaseModel):
    action: str
    edited_text: str | None = None
    review_version: int | None = None


class ReviewIssueActionRequest(BaseModel):
    review_version: int | None = None
    edited_text: str | None = None
    message: str | None = None
    repair_strategy: str | None = None


_ENDING_REPAIR_STRATEGIES = {
    "append_ending",
    "allow_scene_rewrite",
    "scene_restructure",
}

_DETERMINISTIC_DASH_REPAIR_TYPES = {
    "ai_punctuation_artifact",
    "explanatory_punctuation_artifact",
    "dash_density",
    "dash_per_1000",
}


def _repair_failed_delta_packets(
    repair_failed_deltas: dict[int, list[dict]],
    scene_texts: dict[int, str],
):
    """Turn content-blocking repair failures back into FBI review packets."""
    from app.models.chapter_review import SceneReviewPacket

    packets: list[SceneReviewPacket] = []
    for scene_idx, items in sorted((repair_failed_deltas or {}).items()):
        if not items or not isinstance(scene_idx, int):
            continue
        content_items: list[dict] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            normalized = normalize_violation_semantics(dict(item))
            if _classify_review_violation_for_commit(normalized) == "content_blocking":
                content_items.append(normalized)
        if content_items:
            packets.append(SceneReviewPacket(
                scene_index=scene_idx,
                candidate_text=scene_texts.get(scene_idx, ""),
                blocking_violations=content_items,
                advisory_violations=[],
                repairability="auto_fixable",
            ))
    return packets


def _review_scope(resume_state: dict, review: dict | None = None) -> str:
    scope = str(
        (resume_state or {}).get("review_scope")
        or (review or {}).get("review_scope")
        or "scene"
    ).strip().lower()
    return "chapter" if scope == "chapter" else "scene"


def _skip_locked_scene_validation(
    scene_index: int,
    reviewed_scene_overrides: dict[int, str] | None,
    skip_reviewed_scene_validation: bool,
    reviewed_scene_repair_indexes: set[int] | None,
) -> bool:
    """Skip validation only for locked scenes, never for retry repair targets."""
    repair_indexes = reviewed_scene_repair_indexes or set()
    return bool(
        skip_reviewed_scene_validation
        and scene_index in (reviewed_scene_overrides or {})
        and scene_index not in repair_indexes
    )


def _reuse_locked_scene_validator_result(
    scene_index: int,
    violation: dict,
    *,
    validator_retry_resumed: bool,
    reviewed_scene_overrides: dict[int, str] | None,
    skip_reviewed_scene_validation: bool,
    reviewed_scene_repair_indexes: set[int] | None,
) -> bool:
    """Reuse a successful retry only for the same locked scene text."""
    return bool(
        validator_retry_resumed
        and _classify_review_violation_for_commit(violation) == "validator_error"
        and _skip_locked_scene_validation(
            scene_index,
            reviewed_scene_overrides,
            skip_reviewed_scene_validation,
            reviewed_scene_repair_indexes,
        )
    )


def _review_retry_recovery_budget(
    scene_index: int,
    reviewed_scene_repair_indexes: set[int] | None,
) -> dict | None:
    """Allow edits to an existing review candidate, but never regenerate it."""
    if scene_index not in (reviewed_scene_repair_indexes or set()):
        return None
    return {
        "max_patch": 2,
        "max_rewrite": 0,
    }


def _repair_plan_preserves_review_claims(
    repair_plan,
    scene_index: int,
) -> bool:
    """Allow state/detail claim reuse only for surface-only local patches."""
    def owner(order) -> int | None:
        explicit = getattr(order, "owner_scene", None)
        if isinstance(explicit, int):
            return explicit
        targets = list(getattr(order, "target_scenes", None) or [])
        return targets[0] if targets and isinstance(targets[0], int) else None

    orders = [
        order
        for order in (getattr(repair_plan, "orders", None) or [])
        if owner(order) == scene_index
    ]
    return bool(orders) and all(
        str(getattr(order, "repair_type", "") or "") == "local_patch"
        and str(getattr(order, "repair_domain", "") or "") in {"surface", "style"}
        for order in orders
    )


def _as_in_place_review_order(order):
    """Downgrade a full-scene rewrite to an existing-text patch during review retry."""
    cloned = order.model_copy(deep=True)
    if getattr(cloned, "repair_type", "") != "scene_rewrite":
        return cloned

    cloned.repair_type = "local_patch"
    cloned.repair_lane = "fbi_prose"
    cloned.instruction = (
        f"{cloned.instruction}\n" if cloned.instruction else ""
    ) + (
        "Repair the supplied existing scene in place. Use bounded insertions, "
        "deletions, or replacements only; do not regenerate or rewrite the full scene."
    )
    cloned.tool_commands = []
    cloned.work_unit_id = ""
    repair_brief = dict(cloned.repair_brief or {})
    repair_brief.pop("patch_plan", None)
    repair_brief.pop("tool_commands", None)
    repair_brief["in_place_only"] = True
    cloned.repair_brief = repair_brief
    return cloned


def _chapter_review_scene_map(resume_state: dict) -> list[dict]:
    scene_map = resume_state.get("scene_map")
    if isinstance(scene_map, list) and scene_map:
        normalized = []
        for index, item in enumerate(scene_map):
            if not isinstance(item, dict):
                continue
            scene_index = item.get("scene_index", index)
            if not isinstance(scene_index, int):
                try:
                    scene_index = int(scene_index)
                except Exception:
                    scene_index = index
            normalized.append({
                **item,
                "scene_index": scene_index,
                "scene_id": str(item.get("scene_id") or f"scene_{scene_index + 1}"),
            })
        if normalized:
            return sorted(normalized, key=lambda item: item["scene_index"])

    approved = resume_state.get("approved_scene_texts") or {}
    indexes: set[int] = set()
    for key in approved:
        try:
            indexes.add(int(key))
        except Exception:
            continue
    current_index = resume_state.get("scene_index")
    if isinstance(current_index, int) and current_index >= 0:
        indexes.add(current_index)
    if not indexes:
        indexes.add(0)
    return [
        {"scene_index": index, "scene_id": f"scene_{index + 1}"}
        for index in sorted(indexes)
    ]


def _chapter_review_overrides_from_alignment(
    alignment: dict,
    expected: int,
) -> dict[int, str]:
    spans = alignment.get("spans") or []
    if len(spans) != expected:
        raise HTTPException(
            status_code=409,
            detail=f"Chapter review text cannot be split into {expected} scene(s); please keep scene structure intact",
        )
    overrides: dict[int, str] = {}
    empty_scenes: list[int] = []
    for span in spans:
        if not isinstance(span, dict):
            continue
        scene_index = span.get("scene_index")
        try:
            scene_index = int(scene_index)
        except Exception:
            continue
        text = str(span.get("text") or "").strip()
        if not text:
            empty_scenes.append(scene_index)
            continue
        overrides[scene_index] = text
    if empty_scenes or len(overrides) != expected:
        raise HTTPException(
            status_code=409,
            detail=(
                "Chapter review text produced empty scene span(s): "
                + ", ".join(str(index + 1) for index in empty_scenes)
            ),
        )
    return overrides


def _chapter_review_scene_overrides(
    edited_text: str,
    resume_state: dict,
) -> tuple[dict[int, str], dict]:
    from app.services.scene_span_aligner import SceneSpanAligner

    scene_map = _chapter_review_scene_map(resume_state)
    alignment = SceneSpanAligner().align(edited_text, scene_map)
    expected = len(scene_map)
    if alignment.get("method") == "explicit_markers":
        return _chapter_review_overrides_from_alignment(alignment, expected), alignment

    paragraph_counts = resume_state.get("chapter_scene_paragraph_counts") or []
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", edited_text or "") if p.strip()]
    if (
        isinstance(paragraph_counts, list)
        and len(paragraph_counts) == len(scene_map)
        and paragraphs
    ):
        overrides: dict[int, str] = {}
        cursor = 0
        for index, item in enumerate(scene_map):
            raw_count = paragraph_counts[index]
            try:
                count = max(1, int(raw_count))
            except Exception:
                count = 1
            remaining_scenes = len(scene_map) - index - 1
            end = min(cursor + count, max(len(paragraphs) - remaining_scenes, cursor + 1))
            if index == len(scene_map) - 1:
                end = len(paragraphs)
            text = "\n\n".join(paragraphs[cursor:end]).strip()
            scene_index = int(item["scene_index"])
            if not text:
                break
            overrides[scene_index] = text
            cursor = end
        if len(overrides) == len(scene_map):
            return overrides, {
                "method": "paragraph_count_preserve",
                "passed": True,
                "spans": [
                    {
                        "scene_index": scene_index,
                        "scene_id": f"scene_{scene_index + 1}",
                        "text": text,
                        "method": "paragraph_count_preserve",
                    }
                    for scene_index, text in sorted(overrides.items())
                ],
                "warnings": [],
            }

    return _chapter_review_overrides_from_alignment(alignment, expected), alignment


_RECOVERABLE_REPAIR_AUDIT_FAILURES = {
    "protected_obligation_removed",
    "unchanged_result",
    "target_span_not_improved",
}

_HARD_REPAIR_AUDIT_FAILURES = {
    "empty_result",
    "exception",
    "batch_exception",
    "missing_required_dependencies",
    "cyclic_or_unresolved_dependencies",
    "failed_required_dependencies",
    "missing_target_scene",
}


def _is_transient_review_error(exc: Exception) -> bool:
    """方案6 B3 + 方案15：判断异常是否为可重试的瞬时错误。

    统一委托给 ErrorClassifier.is_retryable，消除双套异常分类逻辑。
    保留 DB 异常类名匹配作为补充（ErrorClassifier 未覆盖 SQLAlchemy 异常）。
    """
    from app.services.fbi.error_classifier import ErrorClassifier
    # 先用 ErrorClassifier 判断（覆盖 httpx/asyncio/ConnectionError 及消息关键词）
    if ErrorClassifier.is_retryable(exc):
        return True
    # 补充：DB 临时错误（SQLAlchemy OperationalError / InterfaceError 等）
    # ErrorClassifier 未覆盖这些，因为它们不是 httpx/asyncio 异常
    if exc.__class__.__name__ in {
        "OperationalError",
        "InterfaceError",
        "ConnectionResetError",
        "ConnectionAbortedError",
    }:
        return True
    return False


def _repair_order_failure_scope(order) -> dict[str, Any]:
    """Return the smallest failed Goal/Issue subset recorded by the executor."""

    audit = getattr(order, "repair_audit", {}) or {}
    tool_failures = [
        item
        for item in (audit.get("tool_failures") or [])
        if isinstance(item, dict)
    ]
    latest = tool_failures[-1] if tool_failures else {}
    failed_goal_ids = {
        str(goal_id)
        for goal_id in (
            latest.get("failed_covered_goal_ids")
            or audit.get("failed_covered_goal_ids")
            or []
        )
        if goal_id
    }
    failed_issue_ids = {
        str(issue_id)
        for issue_id in (
            latest.get("failed_covered_issue_ids")
            or audit.get("failed_covered_issue_ids")
            or []
        )
        if issue_id
    }
    repair_brief = getattr(order, "repair_brief", {}) or {}
    if failed_goal_ids and not failed_issue_ids and isinstance(repair_brief, dict):
        for goal in repair_brief.get("repair_goals") or []:
            if not isinstance(goal, dict) or str(goal.get("goal_id") or "") not in failed_goal_ids:
                continue
            failed_issue_ids.update(
                str(issue_id)
                for issue_id in (goal.get("source_issue_ids") or [])
                if issue_id
            )

    all_details = [
        item
        for item in (getattr(order, "violation_details", None) or [])
        if isinstance(item, dict)
    ]
    scoped_details = [
        item
        for item in all_details
        if str(
            item.get("issue_id")
            or item.get("violation_id")
            or item.get("id")
            or ""
        ) in failed_issue_ids
    ]
    return {
        "failed_goal_ids": sorted(failed_goal_ids),
        "failed_issue_ids": sorted(failed_issue_ids),
        "violation_details": scoped_details or all_details,
        "scoped": bool(scoped_details),
    }


def _repair_order_failure_disposition(order) -> str:
    """Classify an unsuccessful repair order for workflow control.

    Candidate-level audit rejections preserve the pre-repair text. They should
    flow into recheck/delta/human-review handling instead of crashing the DAG.
    """
    status = str(getattr(order, "status", "") or "")
    if status not in {"failed", "skipped"}:
        return "accepted"

    audit = getattr(order, "repair_audit", {}) or {}
    if status == "skipped" and audit.get("accepted") is True:
        return "accepted"

    violation_details = _repair_order_failure_scope(order)["violation_details"]
    # A rejected advisory candidate leaves the frozen baseline untouched.
    # Regardless of audit failure code, it must flow to the second attempt or
    # advisory_exhausted instead of becoming a workflow-blocking order error.
    if violation_details and all(
        _is_advisory_review_violation(item)
        for item in violation_details
    ):
        return "recoverable_delta"

    repair_type = str(getattr(order, "repair_type", "") or "")
    failures = {
        str(item)
        for item in (audit.get("failures") or [])
        if str(item)
    }
    reason = str(audit.get("reason") or "")
    if reason:
        failures.add(reason)

    if repair_type == "manual_review" or "manual_review_required" in failures:
        return "needs_human_review"
    if repair_type == "contract_patch":
        return "hard_failed"

    # A structured patch that reached tool execution/postcheck has concrete
    # failure evidence and can be replanned as a bounded delta.  The outer
    # legacy-disabled audit may say ``needs_blueprint_completion``; use the
    # retained tool trace to avoid turning that recoverable failure into an
    # immediate human-review stop.
    tool_failures = [
        item
        for item in (audit.get("tool_failures") or [])
        if isinstance(item, dict)
    ]
    tool_failure_codes = {
        str(code)
        for item in tool_failures
        for code in [item.get("reason"), *(item.get("failures") or [])]
        if str(code or "")
    }
    if tool_failures and not (tool_failure_codes & _HARD_REPAIR_AUDIT_FAILURES):
        return "recoverable_delta"

    if failures and failures.issubset(_RECOVERABLE_REPAIR_AUDIT_FAILURES):
        return "recoverable_delta"
    if failures & _HARD_REPAIR_AUDIT_FAILURES:
        return "hard_failed"

    if not violation_details:
        return "hard_failed"

    lanes = {
        _classify_review_violation_for_commit(item)
        for item in violation_details
    }
    if lanes and lanes.issubset({"style_advisory"}):
        return "recoverable_delta"
    return "hard_failed"


def _is_blocking_repair_order_failure(order) -> bool:
    """Return whether an unsuccessful repair order must block chapter commit."""
    return _repair_order_failure_disposition(order) in {"hard_failed", "needs_human_review"}


def _is_advisory_repair_order(order) -> bool:
    details = [
        item
        for item in (getattr(order, "violation_details", None) or [])
        if isinstance(item, dict)
    ]
    return bool(details) and all(_is_advisory_review_violation(item) for item in details)


def _final_acceptance_failure_summary(
    final_skill_gate: dict | None,
    *,
    content_blocking_count: int = 0,
) -> dict:
    """Summarize final-acceptance failures for repair-cycle progress checks."""
    gate = final_skill_gate or {}
    trace = gate.get("trace") or {}
    validation = (
        trace.get("repair", {}).get("validation")
        or trace.get("initial_validation")
        or {}
    )
    failures = [
        item for item in validation.get("failures") or []
        if isinstance(item, dict)
    ]
    case_delta = gate.get("case_file_delta") or {}
    case_issues = _review_case_delta_issues(case_delta)
    evidence_items = failures or case_issues
    if not evidence_items and not gate.get("allowed", True):
        evidence_items = [{
            "type": "final_gate_blocked",
            "metric": "final_gate_blocked",
            "reason": gate.get("reason", ""),
            "severity": "high",
        }]

    failure_keys: list[str] = []
    critical_high_contract_count = 0
    localized_deterministic_count = 0
    unlocalized_count = 0
    for item in evidence_items:
        normalized = normalize_violation_semantics(item)
        issue_type = str(
            normalized.get("type")
            or normalized.get("metric")
            or normalized.get("validator")
            or "unknown"
        ).lower()
        metric = str(normalized.get("metric") or issue_type).lower()
        validator = str(normalized.get("validator") or normalized.get("source_validator") or "").lower()
        skill_id = str(normalized.get("skill_id") or normalized.get("skill") or "").lower()
        scene = normalized.get("scene_index", normalized.get("source_scene", "chapter"))
        severity = str(normalized.get("severity") or "").lower()
        repair_domain = str(normalized.get("repair_domain") or "").lower()
        failure_keys.append(f"{scene}:{repair_domain}:{validator}:{skill_id}:{issue_type}:{metric}")
        has_location = any(
            normalized.get(key) not in (None, "", [], {})
            for key in ("target_span", "evidence_span", "scene_index", "source_scene", "location")
        )
        deterministic = (
            issue_type in _DETERMINISTIC_DASH_REPAIR_TYPES
            or metric in _DETERMINISTIC_DASH_REPAIR_TYPES
            or bool((normalized.get("repair_intent") or {}).get("patch_plan"))
        )
        if has_location and deterministic:
            localized_deterministic_count += 1
        elif not has_location:
            unlocalized_count += 1
        if repair_domain == "contract" or "contract" in f"{issue_type} {metric} {validator}":
            if severity in {"critical", "high", "blocking", ""}:
                critical_high_contract_count += 1

    failure_keys.extend(f"content_blocking:{idx}" for idx in range(max(0, int(content_blocking_count))))
    failure_keys = sorted(set(failure_keys))
    blocking_failure_count = len(failure_keys)
    signature_hash = hashlib.md5("|".join(failure_keys).encode("utf-8")).hexdigest()[:12]
    return {
        "blocking_failure_count": blocking_failure_count,
        "skill_failure_count": len(evidence_items),
        "content_blocking_count": max(0, int(content_blocking_count)),
        "critical_high_contract_failure_count": critical_high_contract_count,
        "localized_deterministic_count": localized_deterministic_count,
        "unlocalized_count": unlocalized_count,
        "signature_hash": signature_hash,
        "samples": failure_keys[:8],
    }


def _final_acceptance_repair_budget(summary: dict | None, *, hard_limit: int = 2) -> int:
    """Allocate bounded FBI cycles from failure localization and determinism."""
    summary = summary or {}
    failure_count = int(summary.get("blocking_failure_count") or 0)
    localized = int(summary.get("localized_deterministic_count") or 0)
    unlocalized = int(summary.get("unlocalized_count") or 0)
    if failure_count > 0 and localized == failure_count and unlocalized == 0:
        return max(1, min(int(hard_limit), 2))
    return 1


def _final_acceptance_cycle_progress(before: dict | None, after: dict | None) -> dict:
    """Return whether a final-acceptance repair cycle reduced blocking failures."""
    before = before or {}
    after = after or {}
    blocking_before = int(before.get("blocking_failure_count") or 0)
    blocking_after = int(after.get("blocking_failure_count") or 0)
    contract_before = int(before.get("critical_high_contract_failure_count") or 0)
    contract_after = int(after.get("critical_high_contract_failure_count") or 0)
    skill_before = int(before.get("skill_failure_count") or 0)
    skill_after = int(after.get("skill_failure_count") or 0)
    progressed = (
        blocking_after < blocking_before
        or contract_after < contract_before
        or skill_after < skill_before
    )
    return {
        "has_progress": progressed,
        "blocking_failure_delta": blocking_after - blocking_before,
        "critical_high_contract_delta": contract_after - contract_before,
        "skill_failure_delta": skill_after - skill_before,
        "before_signature": before.get("signature_hash", ""),
        "after_signature": after.get("signature_hash", ""),
    }


def _review_case_delta_issues(case_delta: dict | None) -> list[dict]:
    if not isinstance(case_delta, dict):
        return []
    issues: list[dict] = []
    for key in ("scene_issues", "chapter_issues", "skill_failures", "quality_advisories"):
        values = case_delta.get(key) or []
        if isinstance(values, list):
            issues.extend(item for item in values if isinstance(item, dict))
    if not issues and isinstance(case_delta.get("issues"), list):
        issues.extend(item for item in case_delta["issues"] if isinstance(item, dict))
    return issues


def _quality_policy_enforcement_summary(source, limit: int = 5) -> dict:
    """Summarize QualityGate policy-enforcement provenance for workflow output."""
    if source is None:
        return {"enforced_count": 0, "samples": []}

    if isinstance(source, dict) and "reports" in source:
        packets = [source]
    elif isinstance(source, (list, tuple)):
        packets = list(source)
    else:
        packets = [source]

    enforced_count = 0
    samples: list[dict] = []

    for packet in packets:
        if packet is None:
            continue

        if isinstance(packet, dict) and "reports" in packet:
            gate_report = packet
            scene_index = packet.get("scene_index")
        elif isinstance(packet, dict):
            gate_report = packet.get("quality_gate_report") or {}
            scene_index = packet.get("scene_index")
        else:
            gate_report = getattr(packet, "quality_gate_report", {}) or {}
            scene_index = getattr(packet, "scene_index", None)

        if not isinstance(gate_report, dict):
            continue

        reports = gate_report.get("reports") or {}
        if not isinstance(reports, dict):
            continue

        enforcement = reports.get("quality_policy_enforcement") or {}
        if not isinstance(enforcement, dict):
            continue

        violations = enforcement.get("violations") or []
        if not isinstance(violations, list):
            violations = []
        enforced_count += int(enforcement.get("enforced_count") or len(violations) or 0)

        for violation in violations:
            if len(samples) >= limit or not isinstance(violation, dict):
                continue
            evidence = violation.get("evidence") or {}
            if not isinstance(evidence, dict):
                evidence = {}
            candidate = evidence.get("quality_policy_candidate") or {}
            advisory = evidence.get("advisory") or {}
            if not isinstance(candidate, dict):
                candidate = {}
            if not isinstance(advisory, dict):
                advisory = {}
            samples.append({
                "scene_index": scene_index,
                "type": violation.get("violation_type") or violation.get("type"),
                "severity": violation.get("severity"),
                "source": violation.get("source"),
                "detail": violation.get("detail") or violation.get("reason"),
                "candidate_type": candidate.get("type"),
                "candidate_level": candidate.get("level"),
                "candidate_count": candidate.get("count"),
                "advisory_type": advisory.get("type"),
                "advisory_severity": advisory.get("severity"),
                "enforcement_reason": evidence.get("enforcement_reason"),
                "expected_behavior": violation.get("expected_behavior"),
            })

    return {"enforced_count": enforced_count, "samples": samples}


def _information_budget_summary(scene_contracts: list[dict] | None) -> list[dict]:
    summaries: list[dict] = []
    for idx, contract in enumerate(scene_contracts or []):
        if not isinstance(contract, dict):
            continue
        budget = contract.get("information_budget") or {}
        if not isinstance(budget, dict):
            budget = {}
        summaries.append({
            "scene_index": idx,
            "scene_id": contract.get("scene_id") or f"scene_{idx + 1}",
            "original_must_show_count": len(budget.get("original_must_show") or contract.get("must_show") or []),
            "hard_must_show_count": len(contract.get("hard_must_show") or contract.get("must_show") or []),
            "soft_guidance_count": len(contract.get("soft_guidance") or []),
            "deferred_count": len(contract.get("deferred_items") or []),
            "over_budget": bool(budget.get("over_budget")),
        })
    return summaries

_RECHECK_SYSTEM_TYPES = {
    "proposition_extractor_unavailable",
    "proposition_layer_unavailable",
    "critic_parse_error",
    "consistency_check_unavailable",
    "fcip_check_unavailable",
    "scene_contract_compiler_unavailable",
    "empty_text",
}

_RECHECK_HARD_CONTENT_TYPES = {
    "forbidden_triggered",
    "missing_must_show",
    "ending_state_not_reached",
    "forbidden_recap_violation",
    "forbidden_assertion_triggered",
    "required_ambiguity_broken",
    "fact_conflict",
    "timeline_conflict",
    "temporal_conflict",
    "setting_conflict",
    "spatial_conflict",
    "identity_conflict",
    "pov_conflict",
    "knowledge_boundary_violation",
    "clue_provenance_error",
    "clue_provenance_error_proposition",
    "unprovenanced_clue",
    "clue_missing_source",
    "fcip_registration_error",
    "fcip_text_violation",
    "scene_contract_compile_blocked",
}

_RECHECK_STYLE_ADVISORY_TYPES = {
    # ai_punctuation_artifact 已移除：破折号铁律是 hard_blocking，
    # 通过 blocks_commit=True 走 content_blocking 路径（见 _classify_review_violation_for_commit）
    "narration_explanation_artifact",
    "explanatory_punctuation_artifact",
    "repetition_artifact",
    "must_show_overload",
    "info_dump_high_density",
    "info_dump_moderate_density",
    "setting_paragraph_too_long",
    "info_reveal_burst",
    "repeated_body_language",
    "emotion_expression_monotone",
    "reader_experience_advisory",
    "low_reading_drive",
    "low_scene_pressure",
    "passive_protagonist",
    "conflict_only_explained",
    "exposition_driven_reveal",
    "missing_dialogue_pressure",
    "weak_hook_out",
    "specificity_budget_unmet",
    "abstraction_over_budget",
    "emotional_claim_without_scene_evidence",
    "prose_identity_weak",
    "mode_mismatch",
    "over_literary_for_mode",
    "too_plain_for_literary_mode",
    "weak_opening_hook",
    "low_event_density",
    "low_conflict_density",
    "flat_pressure_ramp",
    "weak_curiosity_engine",
    "low_reversal_density",
    "missing_micro_payoff",
    "weak_chapter_end_hook",
    "low_reader_retention",
}

_RECHECK_SCENE_REWRITE_TYPES = {
    "missing_must_show",
    "ending_state_not_reached",
}

_RECHECK_REVIEW_TIMEOUT_SECONDS = 480
_RECHECK_AUTO_REPAIR_TIMEOUT_SECONDS = 240


def _classify_review_violation_for_commit(violation: dict) -> str:
    """Return commit-level lane for a normalized review violation."""
    normalized = normalize_violation_semantics(violation)
    v_type = (normalized.get("type") or "").lower()
    severity = (normalized.get("severity") or "").lower()
    blocks_commit = bool(normalized.get("blocks_commit"))
    issue_classification = str(
        normalized.get("issue_classification") or normalized.get("classification") or ""
    )

    if v_type in _RECHECK_SYSTEM_TYPES or "parse_error" in v_type or "unavailable" in v_type:
        return "validator_error"
    if issue_classification == "validator_system_error":
        return "validator_error"

    if v_type in _RECHECK_HARD_CONTENT_TYPES:
        return "content_blocking"
    if issue_classification in {"planning_conflict", "contract_repair_required"}:
        return "content_blocking"

    if v_type in _RECHECK_STYLE_ADVISORY_TYPES:
        return "style_advisory"
    # blocks_commit=True 的 violation（不在 style_advisory 列表中）会阻断 commit gate，
    # 无论 severity 高低都必须归为 content_blocking，让 FinalDeltaRepairRuntime 能修复。
    # 例如 temporal_anchor_count (severity=medium, blocks_commit=True) 之前被归为
    # style_advisory 导致修复循环跳过，commit gate 永久阻断。
    if blocks_commit:
        return "content_blocking"
    if issue_classification in {"text_repairable", "manual_only"} and severity not in {"critical", "high", "blocking"}:
        return "style_advisory"

    return "style_advisory"


def _validator_system_violations(violations: list[dict] | None) -> list[dict]:
    """Return validator-owned findings regardless of legacy block flags."""
    normalized = [
        normalize_violation_semantics(dict(violation))
        for violation in (violations or [])
        if isinstance(violation, dict)
    ]
    return [
        violation
        for violation in normalized
        if _classify_review_violation_for_commit(violation) == "validator_error"
    ]


def _validator_system_blockers(violations: list[dict] | None) -> list[dict]:
    """Return blockers only when the whole blocking set is validator-owned.

    Content findings must never enter the validator retry lane.  Non-blocking
    advisories may coexist with a transient validator failure and do not change
    ownership of the blocking condition.
    """
    normalized = [
        normalize_violation_semantics(dict(violation))
        for violation in (violations or [])
        if isinstance(violation, dict)
    ]
    blocking = [violation for violation in normalized if violation.get("blocks_commit")]
    if blocking and all(
        _classify_review_violation_for_commit(violation) == "validator_error"
        for violation in blocking
    ):
        return blocking
    return []


def _validator_only_system_findings(violations: list[dict] | None) -> list[dict]:
    """Return findings only when every supplied finding is validator-owned.

    Parallel recheck packets may report a validator failure without setting the
    legacy ``blocks_commit`` flag.  The caller has already stopped because that
    failure makes verification unreliable, so ownership -- not the stale block
    flag -- decides whether the checkpoint belongs to validator retry.
    """
    normalized = [
        normalize_violation_semantics(dict(violation))
        for violation in (violations or [])
        if isinstance(violation, dict)
    ]
    if normalized and all(
        _classify_review_violation_for_commit(violation) == "validator_error"
        for violation in normalized
    ):
        return normalized
    return []


def _workflow_status_after_dag_abort(abort_status: str) -> str | None:
    """Map a DAG abort without overwriting a status already persisted by its handler."""
    if abort_status in {"waiting_review", "pending_validator_retry"}:
        return None
    if abort_status == "cancelled":
        return "cancelled"
    if abort_status in {"blocked", "needs_human_review"}:
        return "blocked"
    return "failed"


def _is_hard_review_violation(violation: dict) -> bool:
    """Classify hard content without trusting legacy ``blocks_commit``.

    Subjective advisories are temporarily repairable even when an older
    QualityGate wrapper marked them blocking.  Conversely, required outcomes
    such as ``missing_must_show`` remain hard even if a caller omitted the
    flag.  The metric registry is the primary authority, with the existing
    contract lanes retained as a compatibility safety net.
    """
    from app.services.metric_registry import is_hard_blocking

    normalized = normalize_violation_semantics(violation)
    metric = str(
        normalized.get("metric")
        or normalized.get("type")
        or normalized.get("violation_type")
        or ""
    )
    if is_hard_blocking(metric):
        return True
    v_type = str(normalized.get("type") or normalized.get("violation_type") or "").lower()
    if v_type in _RECHECK_HARD_CONTENT_TYPES:
        return True
    if normalized.get("blocks_commit") is True and (
        normalized.get("repairable_by_contract") is True
        or str(normalized.get("repair_scope") or normalized.get("scope") or "")
        in {"scene_contract", "chapter_contract", "outline_plan"}
    ):
        return True
    issue_classification = str(
        normalized.get("issue_classification")
        or normalized.get("classification")
        or ""
    )
    return issue_classification in {"planning_conflict", "contract_repair_required"}


def _allow_novel_delta_repair_cycle(
    *,
    repair_round: int,
    current_keys: set[tuple[str, str, str]],
    attempted_keys: set[tuple[str, str, str]],
    base_limit: int = 1,
    total_limit: int = 2,
) -> bool:
    """Grant one bounded cycle only when recheck reveals a new hard issue."""
    return bool(
        repair_round >= base_limit
        and repair_round < total_limit
        and current_keys - attempted_keys
    )


def _annotate_boundary_conflict_sources(
    violation: dict,
    *,
    scene_index: int,
    scene_contract: dict | None = None,
) -> dict:
    """Attach both sides and the single write owner to a scene-boundary conflict."""
    item = violation
    item.setdefault("scene_index", scene_index)
    item.setdefault("source_scene", scene_index)
    source_scenes = [
        scene
        for scene in (item.get("source_scenes") or [])
        if isinstance(scene, int)
    ]
    if scene_index not in source_scenes:
        source_scenes.append(scene_index)

    type_values = {
        str(item.get(key) or "").strip().lower()
        for key in ("type", "violation_type", "metric")
    }
    detail = str(item.get("detail") or "").lower()
    refers_to_prior_scene = any(marker in detail for marker in (
        "前序场景", "上一场景", "前一场景", "先前场景",
        "previous scene", "prior scene", "earlier scene",
    ))
    if not (
        type_values & {"timeline_conflict", "temporal_conflict"}
        and scene_index > 0
        and refers_to_prior_scene
    ):
        item["source_scenes"] = source_scenes or [scene_index]
        return item

    previous_scene = scene_index - 1
    item["source_scenes"] = sorted(set([
        previous_scene,
        scene_index,
        *source_scenes,
    ]))
    item["boundary_conflict"] = True

    contract = scene_contract or {}
    obligations: list[str] = []
    for key in ("goal", "opening_state", "conflict"):
        value = contract.get(key)
        if isinstance(value, str) and value.strip():
            obligations.append(value)
    for key in ("hard_must_show", "must_show"):
        values = contract.get(key) or []
        if isinstance(values, str):
            values = [values]
        obligations.extend(
            str(value)
            for value in values
            if isinstance(value, str) and value.strip()
        )

    def _han_bigrams(value: str) -> set[str]:
        chars = "".join(re.findall(r"[\u4e00-\u9fff]", value or ""))
        return {
            chars[index:index + 2]
            for index in range(max(0, len(chars) - 1))
        }

    claim = " ".join(
        str(item.get(key) or "")
        for key in ("detail", "text_claim", "target_span")
    )
    later_contract_owns_event = bool(
        obligations
        and len(_han_bigrams(" ".join(obligations)) & _han_bigrams(claim)) >= 3
    )
    item["affected_scene"] = previous_scene if later_contract_owns_event else scene_index
    return item


def _is_advisory_review_violation(violation: dict) -> bool:
    from app.services.metric_registry import get_enforcement, get_metric_info

    if _is_hard_review_violation(violation):
        return False
    normalized = normalize_violation_semantics(violation)
    metric = str(normalized.get("metric") or normalized.get("type") or "")
    if (
        str(normalized.get("enforcement") or "") == "advisory"
        or normalized.get("advisory_only") is True
        or normalized.get("blocks_commit") is False
    ):
        return True
    return (
        get_metric_info(metric).get("category") != "unknown"
        and get_enforcement(metric).get("enforcement") == "advisory"
    )


def _mark_advisories_exhausted(
    packets,
    *,
    repair_attempts: int = 2,
) -> list[dict]:
    """Demote exhausted advisory findings in the authoritative review packets."""
    exhausted: list[dict] = []

    def _mark(item: dict) -> dict:
        marked = dict(item)
        marked["blocks_commit"] = False
        marked["enforcement"] = "advisory"
        marked["advisory_exhausted"] = True
        marked["repair_attempts"] = repair_attempts
        evidence = marked.get("evidence") if isinstance(marked.get("evidence"), dict) else {}
        marked["evidence"] = {
            **evidence,
            "enforcement": "advisory",
            "advisory_exhausted": True,
            "repair_attempts": repair_attempts,
        }
        return marked

    packet_iter = packets.values() if isinstance(packets, dict) else packets or []
    for packet in packet_iter:
        if packet is None:
            continue
        if isinstance(packet, dict):
            blocking = list(packet.get("blocking_violations") or [])
            advisory = list(packet.get("advisory_violations") or [])
        else:
            blocking = list(getattr(packet, "blocking_violations", []) or [])
            advisory = list(getattr(packet, "advisory_violations", []) or [])

        kept_blocking: list[dict] = []
        marked_advisory: list[dict] = []
        for item in blocking:
            if not isinstance(item, dict):
                continue
            if _is_hard_review_violation(item):
                kept_blocking.append(item)
            else:
                marked = _mark(item)
                marked_advisory.append(marked)
                exhausted.append(marked)
        for item in advisory:
            if not isinstance(item, dict):
                continue
            if _is_hard_review_violation(item):
                kept_blocking.append({**item, "blocks_commit": True})
            else:
                marked = _mark(item)
                marked_advisory.append(marked)
                exhausted.append(marked)

        # Deduplicate findings that arrived through both legacy buckets.
        deduped_advisory: list[dict] = []
        seen: set[tuple[str, str, str]] = set()
        for item in marked_advisory:
            key = (
                str(item.get("issue_id") or item.get("violation_id") or item.get("id") or item.get("type") or ""),
                str(item.get("source_scene") if item.get("source_scene") is not None else item.get("scene_index") or ""),
                str(item.get("target_span") or item.get("detail") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            deduped_advisory.append(item)

        if isinstance(packet, dict):
            packet["blocking_violations"] = kept_blocking
            packet["advisory_violations"] = deduped_advisory
        else:
            packet.blocking_violations = kept_blocking
            packet.advisory_violations = deduped_advisory

    return exhausted


def _validation_advisory_findings(validation: dict | None) -> list[dict]:
    """Collect non-hard Skill findings that are retained outside failures."""
    validation = validation or {}
    collected: list[dict] = []
    validators = validation.get("validators") or {}
    if not isinstance(validators, dict):
        return collected
    seen: set[tuple[str, str, str]] = set()
    for result in validators.values():
        if not isinstance(result, dict):
            continue
        for finding in result.get("findings") or []:
            if not isinstance(finding, dict) or _is_hard_review_violation(finding):
                continue
            item = dict(finding)
            item["blocks_commit"] = False
            item["advisory_only"] = True
            item["enforcement"] = "advisory"
            key = (
                str(item.get("metric") or item.get("type") or ""),
                str(item.get("scene_index") or ""),
                str(item.get("target_span") or item.get("reason") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            collected.append(item)
    return collected


def _recheck_repair_type(violation: dict) -> str:
    """Route recheck repair by issue responsibility, never retry count."""
    normalized = normalize_violation_semantics(violation)
    v_type = str(normalized.get("type") or "").lower()
    if v_type in _RECHECK_SCENE_REWRITE_TYPES:
        return "scene_rewrite"
    # 通用修复（循环 #13）：尊重 violation 自身的 suggested_strategy 字段
    # 根因：_RECHECK_SCENE_REWRITE_TYPES 只含 missing_must_show 和 ending_state_not_reached，
    #   timeline_conflict / setting_conflict 等结构性冲突的 suggested_strategy=rewrite_scene
    #   但不在集合中，被错误路由到 local_patch，导致相同策略反复失败。
    #   通用性：任何被系统判定为 rewrite_scene 的违规都应走 scene_rewrite 路径。
    suggested = str(normalized.get("suggested_strategy") or "").lower()
    if suggested == "rewrite_scene":
        return "scene_rewrite"
    return "local_patch"


def _review_packet_violations(packet_obj) -> list[dict]:
    items = [
        *(getattr(packet_obj, "blocking_violations", []) or []),
        *(getattr(packet_obj, "advisory_violations", []) or []),
    ]
    return [
        normalize_violation_semantics(item)
        for item in items
        if isinstance(item, dict)
    ]


def _context_block_preview(block: dict, limit: int = 360) -> str:
    content = block.get("content") if isinstance(block, dict) else None
    if content in (None, "", [], {}):
        return ""
    try:
        if isinstance(content, (dict, list)):
            text = json.dumps(content, ensure_ascii=False, default=str)
        else:
            text = str(content)
    except Exception:
        text = str(content)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] + ("..." if len(text) > limit else "")


def _is_ending_state_issue(issue: dict | None) -> bool:
    if not isinstance(issue, dict):
        return False
    return str(issue.get("type") or "") == "ending_state_not_reached"


def _build_ending_state_repair_instruction(
    issue: dict,
    scene_contract: dict | None,
    repair_strategy: str,
) -> str:
    contract = scene_contract if isinstance(scene_contract, dict) else {}
    ending_state = str(
        contract.get("ending_state")
        or contract.get("outline_outcome")
        or issue.get("expected_behavior")
        or ""
    ).strip()
    if repair_strategy == "append_ending":
        action = (
            "Please prefer ending append: only append actions, sounds, discoveries or hooks at the end of the text or last paragraph to achieve the ending state;"
            "Do not rewrite the entire scene, do not change existing facts and order."
        )
    else:
        action = (
            "Please allow FBI scene restructuring specialist: may rearrange the last few paragraphs or perform full-scene structural repair,"
            "but must preserve existing facts, characters, event order, and text not involved in the issue."
        )
    target = f"Must make text naturally achieve scene ending state: {ending_state}" if ending_state else "Must make text naturally achieve the scene contract required ending state."
    return f"{action}\n{target}"


_workflow_events: dict[str, list] = {}

_EDITOR_WORKFLOW_HEAD_STEPS: tuple[str, ...] = (
    "editor_planning",
    "context_compile",
    "chapter_writer",
    "scene_alignment",
)
_EDITOR_WORKFLOW_TAIL_STEPS: tuple[str, ...] = (
    "fact_extraction",
    "style_polish",
    "final_acceptance",
    "write_chapter",
)


def _broadcast_workflow_event(execution_id: str, event: dict):
    if execution_id not in _workflow_events:
        _workflow_events[execution_id] = []
    _workflow_events[execution_id].append(event)


def _workflow_step_layers_from_dag(steps: list[str], dag_snapshot: dict | None = None) -> list[tuple[str, int]]:
    """Map legacy workflow step names to their DAG layer."""
    if not dag_snapshot:
        return [(step_name, index) for index, step_name in enumerate(steps)]

    nodes = {
        str(node.get("node_id")): node
        for node in dag_snapshot.get("nodes", [])
        if isinstance(node, dict) and node.get("node_id")
    }
    mapped: dict[str, int] = {}
    ordered: list[tuple[str, int]] = []
    for step_name in _EDITOR_WORKFLOW_HEAD_STEPS:
        if step_name in steps and step_name not in mapped:
            mapped[step_name] = -1
            ordered.append((step_name, -1))
    for layer_index, layer_nodes in enumerate(dag_snapshot.get("layers") or []):
        if not isinstance(layer_nodes, list):
            continue
        for node_id in layer_nodes:
            node = nodes.get(str(node_id))
            if not node:
                continue
            step_name = str(node.get("workflow_step") or node.get("node_id") or "").strip()
            if not step_name or step_name in mapped:
                continue
            mapped[step_name] = layer_index
            ordered.append((step_name, layer_index))

    dag_layer_count = len(dag_snapshot.get("layers") or [])
    fallback_layer = dag_layer_count + 1
    for step_name in steps:
        if step_name not in mapped:
            if step_name in _EDITOR_WORKFLOW_TAIL_STEPS:
                ordered.append((step_name, dag_layer_count))
            else:
                ordered.append((step_name, fallback_layer))
                fallback_layer += 1
    return ordered


def _complete_editor_workflow_step_names(dag_step_names: list[str]) -> list[str]:
    """Include serial lifecycle steps that execute outside the review DAG."""
    return list(dict.fromkeys([
        *_EDITOR_WORKFLOW_HEAD_STEPS,
        *dag_step_names,
        *_EDITOR_WORKFLOW_TAIL_STEPS,
    ]))


def _inferred_dynamic_workflow_layer(agent_name: str, execution: WorkflowExecution) -> int:
    if agent_name in _EDITOR_WORKFLOW_HEAD_STEPS or re.match(
        r"^(?:core_generation|scene_preparation|scene_alignment)(?:_\d+)?$",
        agent_name,
    ):
        return -1
    if agent_name in _EDITOR_WORKFLOW_TAIL_STEPS:
        return int(execution.total_layers or 0)
    return int(execution.current_layer or 0)


async def _create_workflow_execution(
    project_id: str,
    trigger_type: str,
    steps: list[str],
    db: AsyncSession,
    dag_snapshot: dict | None = None,
    input_context: dict | None = None,
) -> str:
    await _cleanup_stale_running_workflows(db, project_id=project_id)
    execution_id = str(uuid.uuid4())
    execution_context = dict(input_context or {})
    if dag_snapshot:
        execution_context["dag"] = dag_snapshot
    execution = WorkflowExecution(
        id=execution_id,
        project_id=project_id,
        status="running",
        trigger_type=trigger_type,
        input_context=execution_context,
        current_layer=0,
        total_layers=len((dag_snapshot or {}).get("layers") or steps),
    )
    db.add(execution)

    for step_name, layer in _workflow_step_layers_from_dag(steps, dag_snapshot):
        step = WorkflowStep(
            execution_id=execution_id,
            agent_name=step_name,
            layer=layer,
            status="pending",
        )
        db.add(step)

    await db.commit()
    return execution_id


async def _update_workflow_step(
    execution_id: str,
    agent_name: str,
    status: str,
    output: dict | None = None,
    error: str | None = None,
    db: AsyncSession | None = None,
):
    if not db:
        return
    now = _utcnow()
    exec_query = await db.execute(
        select(WorkflowExecution).where(WorkflowExecution.id == execution_id)
    )
    execution = exec_query.scalar_one_or_none()
    if execution is not None and execution.status == "cancelled" and status != "cancelled":
        return False
    step_query = await db.execute(
        select(WorkflowStep).where(
            WorkflowStep.execution_id == execution_id,
            WorkflowStep.agent_name == agent_name,
        )
    )
    step = step_query.scalar_one_or_none()
    if step is None:
        if execution is not None:
            step = WorkflowStep(
                execution_id=execution_id,
                agent_name=agent_name,
                layer=_inferred_dynamic_workflow_layer(agent_name, execution),
                status="pending",
            )
            db.add(step)
            await db.flush()
    values = {"status": status}
    if status == "running":
        values["started_at"] = (
            step.started_at
            if step and step.status == "running" and step.started_at
            else now
        )
        values["completed_at"] = None
        values["duration_ms"] = None
        values["error_message"] = ""
        if output:
            values["output_snapshot"] = to_json_safe(output)
    elif status in ("completed", "failed", "cancelled", "skipped", "degraded", "blocked_by_outline", "waiting_review", "pending_validator_retry"):
        values["completed_at"] = now
        if output:
            values["output_snapshot"] = to_json_safe(output)
        if error is not None:
            values["error_message"] = error

    if step:
        for k, v in values.items():
            setattr(step, k, v)
        if step.started_at and step.completed_at:
            step.duration_ms = int((step.completed_at - step.started_at).total_seconds() * 1000)
        if status in ("running", "completed", "failed", "cancelled", "skipped", "degraded", "blocked_by_outline", "waiting_review", "pending_validator_retry"):
            if execution:
                execution.current_layer = max(int(execution.current_layer or 0), int(step.layer or 0))
                execution.updated_at = now
        await db.commit()

    _broadcast_workflow_event(execution_id, {
        "type": "step_update",
        "agent_name": agent_name,
        "status": status,
        "timestamp": now.isoformat(),
    })
    return True


async def _finalize_open_workflow_steps(
    execution_id: str,
    *,
    status: str,
    error: str = "",
    db: AsyncSession,
) -> None:
    now = _utcnow()
    open_statuses = {
        "running",
        "pending_validator_retry",
        "waiting_review",
        "waiting_human_content_review",
        "waiting_human_system_review",
        "waiting_contract_repair",
        "waiting_alias_decision",
        "waiting_retcon_decision",
    }
    steps_query = await db.execute(
        select(WorkflowStep).where(
            WorkflowStep.execution_id == execution_id,
            WorkflowStep.status.in_(open_statuses),
        )
    )
    for step in steps_query.scalars().all():
        step.status = status
        if status == "completed" and not error:
            step.error_message = ""
        else:
            step.error_message = error or step.error_message or status
        step.completed_at = now
        if step.started_at:
            step.duration_ms = int((step.completed_at - step.started_at).total_seconds() * 1000)
        else:
            step.duration_ms = 0

    pending_query = await db.execute(
        select(WorkflowStep).where(
            WorkflowStep.execution_id == execution_id,
            WorkflowStep.status == "pending",
        )
    )
    for step in pending_query.scalars().all():
        step.status = "skipped"
        step.error_message = error or f"workflow {status}"
        step.completed_at = now
        step.duration_ms = 0


async def _reset_workflow_steps_from_layer(
    execution_id: str,
    *,
    from_layer: int,
    db: AsyncSession,
) -> None:
    steps_query = await db.execute(
        select(WorkflowStep).where(
            WorkflowStep.execution_id == execution_id,
            WorkflowStep.layer >= from_layer,
        )
    )
    for step in steps_query.scalars().all():
        step.status = "pending"
        step.started_at = None
        step.completed_at = None
        step.duration_ms = 0
        step.error_message = ""
        step.output_snapshot = {}


async def _prepare_workflow_resume(
    execution: WorkflowExecution,
    *,
    db: AsyncSession,
    from_layer: int = 0,
) -> bool:
    now = _utcnow()
    claim = await db.execute(
        update(WorkflowExecution)
        .where(
            WorkflowExecution.id == execution.id,
            WorkflowExecution.status != "cancelled",
        )
        .values(
            status="running",
            error_message="",
            current_layer=from_layer,
            updated_at=now,
        )
    )
    if claim.rowcount == 0:
        await db.refresh(execution)
        return False
    await db.refresh(execution)
    await _reset_workflow_steps_from_layer(
        str(execution.id),
        from_layer=from_layer,
        db=db,
    )
    return True


async def _prepare_validated_review_resume_boundary(
    execution_id: str,
    *,
    resume_state: dict,
    already_validated: bool,
    db: AsyncSession,
) -> tuple[int, bool]:
    """Resume an L4 workbench candidate at the convergence boundary itself.

    Workbench recheck proves that the issue shown to the user is resolved; it
    does not prove that chapter-level Skill validators still pass after the
    edit.  Re-running L4 makes the same commit-blocking validator set execute
    before ordered state commit, so final acceptance cannot introduce a rule
    that was skipped merely because a human-approved candidate resumed at L5.
    Other breakpoint types keep the conservative full rebuild path.
    """
    breakpoint = str(resume_state.get("human_review_step") or "").strip()
    if not already_validated or breakpoint != "review_case_delta_merge":
        return 0, False

    step_query = await db.execute(
        select(WorkflowStep).where(
            WorkflowStep.execution_id == execution_id,
            WorkflowStep.agent_name == breakpoint,
        )
    )
    step = step_query.scalar_one_or_none()
    if step is None:
        return 0, False

    return int(step.layer or 0), True


async def _resume_review_candidate(
    *,
    execution: WorkflowExecution,
    project_id: uuid.UUID,
    persisted: dict,
    review: dict,
    resume_state: dict,
    candidate_text: str,
    review_version: int,
    source: str,
    already_validated: bool,
    human_override: bool = False,
    db: AsyncSession,
) -> dict:
    """Map a reviewed candidate back to scenes and resume the canonical commit path."""
    candidate_text = str(candidate_text or "").strip()
    if not candidate_text:
        raise HTTPException(status_code=409, detail="Review candidate text is empty; cannot resume workflow")

    scene_index = int(resume_state.get("scene_index", -1))
    if scene_index < 0:
        raise HTTPException(status_code=409, detail="Human review breakpoint is missing its scene index")

    from app.services.project_lock import ProjectLockManager

    if ProjectLockManager.get_lock(str(project_id)).locked():
        raise HTTPException(status_code=409, detail="Project is executing another generation task, please retry later")

    review_scope = _review_scope(resume_state, review)
    overrides = {
        int(index): text
        for index, text in (resume_state.get("approved_scene_texts") or {}).items()
        if isinstance(text, str) and text.strip()
    }
    changed_indexes: set[int]
    if review_scope == "chapter":
        chapter_overrides, chapter_alignment = _chapter_review_scene_overrides(
            candidate_text,
            resume_state,
        )
        overrides.update(chapter_overrides)
        changed_indexes = set(chapter_overrides)
        persisted["chapter_review_alignment"] = {
            "method": chapter_alignment.get("method"),
            "span_count": len(chapter_alignment.get("spans") or []),
            "warnings": chapter_alignment.get("warnings", []),
        }
    else:
        overrides[scene_index] = candidate_text
        changed_indexes = {scene_index}

    if not any(isinstance(text, str) and text.strip() for text in overrides.values()):
        raise HTTPException(
            status_code=409,
            detail="Review candidate could not be mapped back to any scene",
        )

    candidate_hash = hashlib.md5(candidate_text.encode()).hexdigest()[:12]
    approved_scene_texts = dict(resume_state.get("approved_scene_texts") or {})
    for index, text in overrides.items():
        if isinstance(text, str) and text.strip():
            approved_scene_texts[str(int(index))] = text

    resume_state["approved_scene_texts"] = approved_scene_texts
    resume_state["candidate_text"] = candidate_text
    resume_state["current_text_hash"] = candidate_hash
    resume_state["review_version"] = review_version
    resume_state["scene_recovery_status"] = (
        "human_override"
        if human_override
        else "validated" if already_validated else "review_resuming"
    )
    review["candidate_text"] = candidate_text
    review["current_text_hash"] = candidate_hash
    review["review_version"] = review_version
    review["review_scope"] = review_scope
    review["passed"] = bool(already_validated) and not human_override
    if human_override:
        review["review_resolution"] = "human_override"
        review["message"] = "Human override accepted. Content recheck was skipped; commit safety checks remain active."
        review["error_code"] = ""
    elif already_validated:
        review["message"] = "Re-verify passed. Workflow resumed automatically."
        review["error_code"] = ""

    resume_from_layer, resume_existing_dag = await _prepare_validated_review_resume_boundary(
        str(execution.id),
        resume_state=resume_state,
        already_validated=already_validated or human_override,
        db=db,
    )

    persisted["resume_state"] = resume_state
    persisted["review"] = review
    persisted["resumed_from_review"] = True
    persisted["review_resume"] = {
        "source": source,
        "status": "running",
        "review_scope": review_scope,
        "scene_indexes": sorted(changed_indexes),
        "text_hash": candidate_hash,
        "already_validated": bool(already_validated),
        "resolution": "human_override" if human_override else "already_validated" if already_validated else "recheck_required",
        "content_recheck_skipped": bool(human_override),
        "canonical_commit_required": True,
        "resume_from_layer": resume_from_layer,
        "resume_existing_dag": resume_existing_dag,
    }
    persisted.pop("candidate_ready", None)
    persisted.pop("candidate", None)

    resume_prepared = await _prepare_workflow_resume(
        execution,
        db=db,
        from_layer=resume_from_layer,
    )
    if not resume_prepared:
        raise HTTPException(status_code=409, detail="Cancelled workflow cannot be resumed")
    execution.result_context = _json_snapshot(persisted)
    await db.commit()

    _broadcast_workflow_event(str(execution.id), {
        "type": "execution_update",
        "status": "running",
        "resumed_from_review": True,
        "timestamp": _utcnow().isoformat(),
    })
    _spawn_workflow_task(str(execution.id), _run_editor_generation(
        execution_id=str(execution.id),
        project_id=str(project_id),
        chapter_number=int(resume_state["chapter_number"]),
        pov_character=resume_state.get("pov_character"),
        custom_instructions=resume_state.get("custom_instructions"),
        reviewed_scene_overrides=overrides,
        review_version=review_version,
        skip_editor_planning=True,
        skip_reviewed_scene_validation=True,
        reviewed_scene_repair_indexes=set() if already_validated or human_override else changed_indexes,
        human_review_override=human_override,
        resume_existing_dag=resume_existing_dag,
    ))
    return {
        "execution_id": str(execution.id),
        "status": "running",
        "auto_resumed": source == "auto_recheck_passed",
        "review": review,
    }


async def _commit_gate_check(
    *,
    execution_id: str,
    review_packets,
    current_repair_plan,
    db,
    final_text: str = "",
    scene_texts: dict[int, str] | None = None,
    chapter_state: dict | None = None,
    style_result: dict | None = None,
    skill_gate_result: dict | None = None,
) -> dict:
    """提交闸：写章前统一硬闸检查。

    只有满足以下条件才能 write_chapter：
    1. 没有 content_blocking 违规
    2. 没有 unresolved contract_required
    3. 没有 failed repair order
    4. 复检已通过，或仅剩允许提交的 advisory
    5. workflow 状态不是 blocked / failed / human_review

    Returns:
        {"allowed": bool, "reason": str}
    """
    reasons = []

    # 1. 检查 review_packets 中是否有 content_blocking
    if isinstance(review_packets, dict):
        packet_iter = review_packets.items()
    elif isinstance(review_packets, list):
        packet_iter = enumerate(review_packets)
    else:
        packet_iter = []

    for scene_idx, packet in packet_iter:
        if packet is None:
            continue
        blocking_violations = (
            packet.get("blocking_violations", [])
            if isinstance(packet, dict)
            else getattr(packet, "blocking_violations", [])
        )
        if blocking_violations:
            # 区分 content_blocking 和 validator_errors
            for v in blocking_violations:
                if not isinstance(v, dict):
                    continue
                v_type = (v.get("violation_type") or v.get("type") or "").lower()
                lane = _classify_review_violation_for_commit(v)
                if lane == "content_blocking":
                    reasons.append(
                        f"scene {scene_idx}: content blocking violation ({v_type})"
                    )
                    break  # one per scene is enough
                if lane == "validator_error":
                    reasons.append(
                        f"scene {scene_idx}: validator error ({v_type})"
                    )
                    break

    # 2. 检查是否有不可恢复的 repair order 失败。质量增强候选被保护审计
    # 回滚后交给复检判断，不在这里重复阻断。
    if current_repair_plan is not None and hasattr(current_repair_plan, "orders"):
        failed_orders = [
            o for o in current_repair_plan.orders
            if _is_blocking_repair_order_failure(o)
        ]
        if failed_orders:
            reasons.append(f"{len(failed_orders)} blocking repair order failure(s)")

        # 3. 检查是否有 contract_required orders
        contract_orders = [
            o for o in current_repair_plan.orders
            if getattr(o, "repair_type", "") == "contract_patch"
            and getattr(o, "status", "") not in ("succeeded", "skipped")
        ]
        if contract_orders:
            reasons.append(f"{len(contract_orders)} unresolved contract_required order(s)")

    from app.services.chapter_commit_health import ChapterCommitHealthChecker

    health = ChapterCommitHealthChecker().evaluate(
        final_text=final_text,
        scene_texts=scene_texts or {},
        chapter_state=chapter_state or {},
        style_result=style_result or {},
    )
    for issue in health.get("hard_issues", []):
        scene_label = (
            f"scene {issue['scene_index']}: "
            if issue.get("scene_index") is not None
            else ""
        )
        reasons.append(f"{scene_label}{issue.get('code')}: {issue.get('detail')}")

    if skill_gate_result and not skill_gate_result.get("allowed", False):
        reasons.append(
            str(skill_gate_result.get("reason") or "final skill validation failed")
        )

    if reasons:
        return {
            "allowed": False,
            "reason": "; ".join(reasons),
            "commit_health": health,
        }

    return {"allowed": True, "reason": ""}


def _style_polish_output(polish_result: dict) -> dict:
    return {
        "applied": False,
        "has_issues": bool(polish_result.get("has_issues")),
        "transition_changes": polish_result.get("transition_changes", []),
        "issues": polish_result.get("issues", []),
        "summary": polish_result.get("summary", {}),
        "style_score": polish_result.get("style_score"),
        "embedding_delta": polish_result.get("embedding_delta", {}),
        "repair_suggestions": polish_result.get("repair_suggestions", []),
        "requires_human_review": polish_result.get("requires_human_review", False),
    }


def _style_polish_validation(polish_result: dict) -> dict:
    failures: list[dict] = []
    for issue in polish_result.get("issues") or []:
        if not isinstance(issue, dict):
            continue
        issue_type = str(issue.get("type") or "style_issue")
        failures.append({
            "skill_id": "style_polish",
            "validator": "style_polish",
            "metric": issue_type,
            "severity": "advisory",
            "advisory_only": True,
            "blocks_commit": False,
            "enforcement": "advisory",
            "reason": issue.get("suggestion") or issue.get("context") or issue_type,
            "action": "style_repair",
            "issue": {key: value for key, value in issue.items() if key != "context"},
            "retry_policy": {"max_retries": 1, "action": "style_repair"},
        })
    for issue in polish_result.get("style_violations") or polish_result.get("violations") or []:
        if not isinstance(issue, dict) or issue.get("type") in {"forbidden_word", "repetition"}:
            continue
        failures.append({
            "skill_id": "style_polish",
            "validator": "style_polish",
            "metric": str(issue.get("type") or "style_violation"),
            "severity": "advisory",
            "advisory_only": True,
            "blocks_commit": False,
            "enforcement": "advisory",
            "reason": issue.get("suggestion") or issue.get("context") or "style violation",
            "action": "style_repair",
            "issue": {key: value for key, value in issue.items() if key != "context"},
            "retry_policy": {"max_retries": 1, "action": "style_repair"},
        })
    return {
        "passed": not failures,
        "validators": {
            "style_polish": {
                "validator": "style_polish",
                "passed": not failures,
                "status": "ok",
                "repair_hooks": ["style_repair"],
                "findings": failures,
            }
        },
        "failures": failures,
    }


async def _attempt_style_polish_auto_repair(
    *,
    text: str,
    polish_result: dict,
    active_style: dict | None = None,
    llm=None,
    max_tokens: int = 16000,
    project_id: str | None = None,
    chapter_number: int | None = None,
) -> dict:
    if not polish_result.get("requires_human_review"):
        return {"attempted": False, "reason": "style_review_not_required"}
    summary = polish_result.get("summary") or {}
    conflict = polish_result.get("contract_style_conflict") or summary.get("contract_style_conflict")
    if conflict:
        # 方案18 步骤4：记录 contract_style_conflict 信号，供主编规划下一章时参考
        # 不再仅返回 reason 字符串，而是把冲突详情持久化到反馈服务
        if project_id and chapter_number is not None:
            try:
                from app.services.editor_feedback_service import EditorFeedbackService
                if isinstance(conflict, dict):
                    conflict_detail = dict(conflict)
                else:
                    conflict_detail = {"summary": str(conflict)}
                # dimension 缺失时用默认值；summary 保留原意
                if "dimension" not in conflict_detail:
                    conflict_detail["dimension"] = "contract_style_conflict"
                EditorFeedbackService.record_style_conflict(
                    project_id, chapter_number, conflict_detail
                )
            except Exception:
                pass
        return {"attempted": False, "reason": "contract_style_conflict", "conflict": conflict}

    validation = _style_polish_validation(polish_result)
    if not validation.get("failures"):
        return {"attempted": False, "reason": "no_style_failures"}

    from app.agents.core_generation import CoreGenerationAgent
    from app.models.agent_skill import CompiledSkillPacket
    from app.services.agent_skill_commit_gate import AgentSkillCommitGate
    from app.services.agent_skill_repair_dispatcher import get_skill_repair_dispatcher
    from app.skills.style_polish import StylePolishSkill

    if llm is None:
        llm = await CoreGenerationAgent().get_llm_client()

    style_context = active_style or {}
    style_packet = CompiledSkillPacket(
        agent_name="core_generation",
        active_skills=["style_polish"],
        validators=["style_polish"],
        repair_hooks={"style_polish": ["style_repair"]},
    )

    async def validate_style_candidate(candidate_text: str) -> dict:
        stripped = AgentSkillCommitGate._strip_final_scene_markers(str(candidate_text or ""))
        candidate_polish = StylePolishSkill().polish(
            stripped,
            style_features=style_context.get("style_features") if style_context else None,
            style_embedding=style_context.get("style_embedding") if style_context else None,
            persona_card=style_context.get("persona_card") if style_context else None,
        )
        return _style_polish_validation(candidate_polish)

    dispatch = await get_skill_repair_dispatcher().dispatch(
        style_packet,
        validation=validation,
        generated_text=text,
        context={
            "style_context": style_context,
            "style_profile": style_context,
            "style_prompt": style_context.get("style_prompt", ""),
            "style_sample_passages": style_context.get("style_sample_passages")
            or style_context.get("sample_passages")
            or [],
            "style_embedding": style_context.get("style_embedding") or {},
            "persona_card": style_context.get("persona_card") or {},
            "style_statistics": style_context.get("style_statistics") or {},
            "evolution_report": style_context.get("evolution_report") or {},
            "output_surface": "final_chapter",
            "final_structure_policy": {"scene_markers": "forbidden"},
        },
        llm=llm,
        max_tokens=max_tokens,
        validate_candidate=validate_style_candidate,
    )
    if not dispatch.get("completed"):
        return {
            "attempted": True,
            "accepted": False,
            "dispatch": {key: value for key, value in dispatch.items() if key != "text"},
            "reason": "dispatch_not_completed",
        }

    candidate = AgentSkillCommitGate._strip_final_scene_markers(str(dispatch.get("text") or ""))
    retry_polish = StylePolishSkill().polish(
        candidate,
        style_features=style_context.get("style_features") if style_context else None,
        style_embedding=style_context.get("style_embedding") if style_context else None,
        persona_card=style_context.get("persona_card") if style_context else None,
    )
    old_score = polish_result.get("style_score")
    new_score = retry_polish.get("style_score")
    accepted = not retry_polish.get("requires_human_review")
    return {
        "attempted": True,
        "accepted": accepted,
        "text": candidate if accepted else text,
        "polish_result": retry_polish,
        "old_style_score": old_score,
        "new_style_score": new_score,
        "dispatch": {key: value for key, value in dispatch.items() if key != "text"},
        "reason": "" if accepted else "style_repair_still_requires_human_review",
    }


async def _complete_workflow_execution(
    execution_id: str,
    status: str,
    result: dict | None = None,
    error: str | None = None,
    db: AsyncSession | None = None,
):
    if not db:
        return
    values = {"status": status, "updated_at": _utcnow()}
    if result:
        values["result_context"] = result
    if error:
        values["error_message"] = error

    exec_query = await db.execute(
        select(WorkflowExecution).where(WorkflowExecution.id == execution_id)
    )
    execution = exec_query.scalar_one_or_none()
    if execution:
        if status != "cancelled":
            transition = await db.execute(
                update(WorkflowExecution)
                .where(
                    WorkflowExecution.id == execution_id,
                    WorkflowExecution.status != "cancelled",
                )
                .values(**values)
            )
            if transition.rowcount == 0:
                await db.refresh(execution)
                return False
        else:
            for key, value in values.items():
                setattr(execution, key, value)
        if status == "completed":
            await _finalize_open_workflow_steps(
                execution_id,
                status="skipped",
                error="workflow completed without this optional step",
                db=db,
            )
        if status in {"failed", "cancelled", "blocked", "needs_human_review"}:
            await _finalize_open_workflow_steps(
                execution_id,
                status=status,
                error=error or status,
                db=db,
            )
        await db.commit()

    _broadcast_workflow_event(execution_id, {
        "type": "execution_update",
        "status": status,
        "timestamp": _utcnow().isoformat(),
    })
    return True


async def _record_chapter_candidate(
    execution_id: str,
    candidate: dict,
    db: AsyncSession | None = None,
) -> None:
    if not db:
        return
    exec_query = await db.execute(
        select(WorkflowExecution).where(WorkflowExecution.id == execution_id)
    )
    execution = exec_query.scalar_one_or_none()
    if execution:
        result_context = dict(execution.result_context or {})
        result_context["candidate_ready"] = True
        result_context["candidate"] = to_json_safe(candidate)
        execution.result_context = result_context
        execution.updated_at = _utcnow()
        await db.commit()

    _broadcast_workflow_event(execution_id, {
        "type": "candidate_ready",
        "candidate": {
            "status": candidate.get("status"),
            "source": candidate.get("source"),
            "chapter_number": candidate.get("chapter_number"),
            "scene_count": candidate.get("scene_count"),
            "word_count": candidate.get("word_count"),
            "alignment_method": candidate.get("alignment_method"),
            "alignment_warnings": candidate.get("alignment_warnings", []),
            "created_at": candidate.get("created_at"),
        },
        "timestamp": _utcnow().isoformat(),
    })


async def _is_workflow_cancelled(execution_id: str, db: AsyncSession) -> bool:
    status_query = await db.execute(
        select(WorkflowExecution.status).where(WorkflowExecution.id == execution_id)
    )
    return status_query.scalar_one_or_none() == "cancelled"


def _json_snapshot(value):
    """Keep persisted review checkpoints JSON-safe across PostgreSQL and SQLite."""
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _build_review_guidance(violations: list[dict], attempts: list[dict], *, window_count: int = 0) -> dict:
    blocking = [
        v for v in violations
        if (
            isinstance(v, dict)
            and v.get("blocks_commit")
            and v.get("user_visible", True)
            and v.get("review_status", "open") == "open"
            and str(v.get("fbi_status") or "") != "resolved"
            and not v.get("is_stale", False)
        )
    ]
    strategies = [v.get("suggested_strategy", "manual_review") for v in blocking]
    if "rewrite_scene" in strategies:
        recommended_action = "rewrite_scene"
        action_label = "Suggest regenerating current scene"
    elif "patch_text" in strategies:
        recommended_action = "patch_text"
        action_label = "Suggest local repair per annotation"
    else:
        recommended_action = "manual_review"
        action_label = "Suggest human confirmation before continuing"

    return {
        "recommended_action": recommended_action,
        "action_label": action_label,
        "blocking_count": len(blocking),
        "blocking_window_count": window_count if window_count else len(blocking),
        "auto_repair_attempt_count": len(attempts),
        "items": [
            {
                "type": v.get("type", "violation"),
                "severity": v.get("severity", "high"),
                "detail": v.get("detail", ""),
                "target_span": v.get("target_span") or "",
                "expected_behavior": v.get("expected_behavior") or "",
                "suggested_strategy": v.get("suggested_strategy", "manual_review"),
                "window_id": v.get("window_id", ""),
            }
            for v in blocking
        ],
    }


def _aggregate_review_violations_by_window(
    violations: list[dict],
    resume_state: dict,
    candidate_text: str,
) -> int:
    """F5: 按窗口聚合 violations，给每个 violation 回填 window_id，返回窗口数量。

    复用 FBI 的 EditWindowGrouper，通用方案，不针对特定 metric。
    如果聚合失败（缺 span 信息等），退化为 issue 数量，不会比现状更差。
    """
    if not violations:
        return 0
    try:
        from app.services.fbi.edit_window_grouper import EditWindowGrouper
        scene_index = resume_state.get("scene_index", 0)
        scene_texts = {scene_index: candidate_text} if candidate_text else None
        case_id = str(resume_state.get("case_id") or resume_state.get("execution_id") or "")
        window_cases = EditWindowGrouper().group_violations(
            violations,
            scene_texts,
            case_id=case_id,
        )
        if not window_cases:
            return len(violations)
        issue_to_window: dict[str, str] = {}
        for wc in window_cases:
            for issue_id in wc.issue_ids:
                issue_to_window[issue_id] = wc.window_id
        for v in violations:
            issue_id = v.get("issue_id") or v.get("violation_id")
            if issue_id and issue_id in issue_to_window:
                v["window_id"] = issue_to_window[issue_id]
        return len(window_cases)
    except Exception:
        return len(violations)


def _prepare_review_violations(violations: list[dict], candidate_text: str = "") -> list[dict]:
    prepared = []
    for index, violation in enumerate(violations):
        if not isinstance(violation, dict):
            continue
        item = normalize_violation_semantics(dict(violation))
        item = enrich_review_issue_locations(item, candidate_text)
        issue_id = item.get("violation_id") or hashlib.md5(
            f"{index}:{item.get('type', '')}:{item.get('detail', '')}".encode("utf-8")
        ).hexdigest()[:12]
        item["issue_id"] = issue_id
        blocks_commit = bool(item.get("blocks_commit"))
        scope = str(item.get("scope") or "")
        if not scope:
            if item.get("type") in (
                "critic_parse_error",
                "consistency_check_unavailable",
                "fcip_check_unavailable",
                "scene_contract_compiler_unavailable",
                "proposition_layer_unavailable",
                "proposition_extractor_unavailable",
            ):
                scope = "validator_system"
            elif item.get("suggested_strategy") == "contract_budget_adjust":
                scope = "scene_contract"
            elif not blocks_commit:
                scope = "advisory"
            else:
                scope = "prose_text"
        elif blocks_commit and scope == "advisory":
            # A finding cannot be both commit-blocking and advisory-only.  The
            # workflow already stopped on it, so expose it as an actionable
            # prose issue instead of rendering an empty workbench.
            scope = "prose_text"
        item["scope"] = scope
        item["blocks_commit"] = blocks_commit
        item.setdefault("user_visible", scope != "advisory")
        if scope == "validator_system":
            # A validator finding disappears only after a successful retry for
            # the same candidate hash.  Merely being system-owned or
            # non-blocking is not resolution evidence.
            item["review_status"] = "pending_validator_retry"
        elif not blocks_commit or scope == "advisory":
            item["review_status"] = "ignored"
        else:
            item.setdefault("review_status", "open")
        item.setdefault("chat", [])
        # Review version consistency protocol: backfill text_hash/review_version/is_stale
        item.setdefault("text_hash", "")
        item.setdefault("review_version", 0)
        item.setdefault("is_stale", False)
        item.setdefault("is_system_issue", item.get("scope") == "validator_system")
        item.setdefault("repair_scope", "system" if item.get("scope") == "validator_system" else item.get("scope", "prose_text"))
        prepared.append(item)
    return _dedupe_review_violations(prepared)


def _dedupe_review_violations(violations: list[dict]) -> list[dict]:
    severity_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3, "S1": 0, "S2": 1, "S3": 2, "S4": 3}
    seen: dict[str, dict] = {}
    order: list[str] = []
    for item in violations:
        key = issue_fingerprint(item)
        existing = seen.get(key)
        if existing is None:
            seen[key] = item
            order.append(key)
            continue
        old_rank = severity_rank.get(str(existing.get("severity", "low")), 99)
        new_rank = severity_rank.get(str(item.get("severity", "low")), 99)
        if new_rank < old_rank:
            merged = {**existing, **item}
            seen[key] = merged
        else:
            details = [str(existing.get("detail") or "")]
            new_detail = str(item.get("detail") or "")
            if new_detail and new_detail not in details[0]:
                existing["detail"] = (details[0] + "\n" + new_detail).strip()
    return [seen[key] for key in order]


def _build_text_diff(original_text: str, revised_text: str) -> dict:
    """Build compact inline revision marks that remain readable for CJK prose."""
    token_pattern = re.compile(r"[\u4e00-\u9fff]|[A-Za-z0-9_]+|\s+|.", re.DOTALL)
    original_tokens = token_pattern.findall(original_text or "")
    revised_tokens = token_pattern.findall(revised_text or "")
    matcher = difflib.SequenceMatcher(a=original_tokens, b=revised_tokens, autojunk=False)
    segments = []

    def append_segment(operation: str, tokens: list[str]):
        text = "".join(tokens)
        if not text:
            return
        if segments and segments[-1]["operation"] == operation:
            segments[-1]["text"] += text
        else:
            segments.append({"operation": operation, "text": text})

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            append_segment("equal", original_tokens[i1:i2])
        elif tag == "delete":
            append_segment("delete", original_tokens[i1:i2])
        elif tag == "insert":
            append_segment("add", revised_tokens[j1:j2])
        elif tag == "replace":
            append_segment("delete", original_tokens[i1:i2])
            append_segment("add", revised_tokens[j1:j2])

    return {
        "segments": segments,
        "added_chars": sum(len(item["text"]) for item in segments if item["operation"] == "add"),
        "deleted_chars": sum(len(item["text"]) for item in segments if item["operation"] == "delete"),
    }


def _validator_attempts(validator_retry: dict) -> list[dict]:
    attempts = []
    for item in validator_retry.get("last_errors", []):
        if isinstance(item, dict):
            attempts.append(item)
        elif item:
            attempts.append({"component": "validator", "error": str(item)})
    return attempts


def _record_validator_failure(
    validator_retry: dict,
    *,
    violations: list[dict] | None = None,
    error: str = "",
) -> int:
    delayed_attempts = int(validator_retry.get("validator_retry_delayed_attempts", 0)) + 1
    validator_retry["validator_retry_delayed_attempts"] = delayed_attempts

    records = _validator_attempts(validator_retry)
    for violation in violations or []:
        if not isinstance(violation, dict):
            continue
        if _classify_review_violation_for_commit(violation) != "validator_error":
            continue
        records.append({
            "component": violation.get("source") or violation.get("type", "validator"),
            "error": violation.get("detail", "")[:300],
            "type": violation.get("type", "validator_error"),
            "attempt": delayed_attempts,
        })
    if error:
        records.append({
            "component": "quality_gate",
            "error": error[:300],
            "type": "quality_gate_error",
            "attempt": delayed_attempts,
        })
    validator_retry["last_errors"] = records[-12:]
    return delayed_attempts


def _validator_retry_from_parallel_review(persisted: dict) -> dict | None:
    """Recover the validator breakpoint written by the older parallel route.

    This deliberately accepts only a scene-level parallel checkpoint whose
    complete blocking set is owned by a validator.  A content review therefore
    cannot be converted into a validator retry by changing endpoint status.
    """
    review = persisted.get("review") if isinstance(persisted, dict) else None
    resume_state = persisted.get("resume_state") if isinstance(persisted, dict) else None
    if not isinstance(review, dict) or not isinstance(resume_state, dict):
        return None
    if not resume_state.get("parallel_review_breakpoint"):
        return None
    if str(resume_state.get("review_scope") or review.get("review_scope") or "scene") != "scene":
        return None

    blockers = _validator_system_blockers(review.get("violations") or [])
    candidate_text = str(
        review.get("candidate_text") or resume_state.get("candidate_text") or ""
    )
    try:
        scene_index = int(review.get("scene_index", resume_state.get("scene_index", -1)))
    except (TypeError, ValueError):
        return None
    if not blockers or scene_index < 0 or not candidate_text:
        return None

    previous_scene_context = _review_previous_scene_context(
        resume_state,
        scene_index,
    )

    return {
        "scene_index": scene_index,
        "candidate_text": candidate_text,
        "pipeline_output": review,
        "error_code": str(review.get("error_code") or blockers[0].get("type") or "validator_unavailable"),
        "error_detail": str(review.get("message") or blockers[0].get("detail") or "Validator temporarily unavailable"),
        "scene_contract": resume_state.get("scene_contract") or {},
        "chapter_state": resume_state.get("chapter_state") or {},
        "character_cards": resume_state.get("character_cards") or [],
        "character_names": resume_state.get("character_names") or [],
        "scene_truth_snapshot": resume_state.get("scene_truth_snapshot"),
        "core_facts": resume_state.get("core_facts") or {},
        "word_budget": resume_state.get("word_budget"),
        **previous_scene_context,
        "validator_retry_delays": [5, 15, 45],
        "validator_retry_immediate_attempts": 0,
        "validator_retry_delayed_attempts": 0,
        "source_violations": blockers,
        "recovered_from_parallel_review": True,
    }


async def _prepare_scene_facts_for_commit(
    *,
    chapter_number: int,
    generated_text: str,
    result: dict,
    character_cards: list[dict],
    scene_contract: dict | None,
) -> tuple[dict, str]:
    """Prepare facts for the chapter outbox without writing durable state."""
    scene_facts = result.get("writer_scene_facts") or {}
    fact_source = "writer_scene_facts"
    if not scene_facts:
        scene_facts, fact_source = await extract_chapter_facts(
            generated_text,
            chapter_number,
            character_cards,
            scene_contract=scene_contract,
        )
    result["writer_scene_facts"] = scene_facts
    result["fact_source"] = fact_source
    return scene_facts, fact_source


async def _load_persisted_pending_scene_effects(
    execution_id: str,
    *,
    db: AsyncSession,
) -> list[dict]:
    """Restore scene effects completed before a resumable downstream failure."""
    steps_query = await db.execute(
        select(WorkflowStep).where(
            WorkflowStep.execution_id == execution_id,
            WorkflowStep.status.in_(("completed", "degraded")),
        )
    )
    restored: dict[int, dict] = {}
    for step in steps_query.scalars().all():
        payload = (step.output_snapshot or {}).get("pending_scene_effect")
        if not isinstance(payload, dict):
            continue
        try:
            scene_index = int(payload.get("scene_index"))
        except (TypeError, ValueError):
            continue
        if isinstance(payload.get("generated_text"), str) and isinstance(payload.get("effects"), dict):
            restored[scene_index] = payload
    return [restored[index] for index in sorted(restored)]


def _build_system_review(
    *,
    scene_index: int,
    candidate_text: str,
    validator_retry: dict,
    resume_state: dict,
    violations: list[dict] | None = None,
) -> dict:
    return {
        "scene_index": scene_index,
        "candidate_text": candidate_text,
        "violations": violations or [],
        "attempts": _validator_attempts(validator_retry),
        "retry_count": int(validator_retry.get("validator_retry_delayed_attempts", 0)),
        "error_code": "validator_retry_exhausted",
        "message": "Validator continuous failure, delayed re-verify budget exhausted, needs human confirmation of system state",
        "review_version": int(resume_state.get("review_version", 0)) + 1,
        "review_type": "system_review",
    }


async def _recover_candidate_after_validator(
    *,
    candidate_text: str,
    validator_retry: dict,
    resume_state: dict,
    project: Project,
    project_id: str,
    db: AsyncSession,
) -> dict:
    """Run content repair again after an unavailable validator comes back."""
    from app.services.scene_recovery_controller import SceneRecoveryController, STREAM_BUDGET
    from app.services.state_manager import StateManager

    story_state = await StateManager().get_state(project_id)
    scene_index = int(validator_retry.get("scene_index", resume_state.get("scene_index", 0)))
    context = {
        "scene_contract": validator_retry.get("scene_contract") or resume_state.get("scene_contract") or {},
        "chapter_state": validator_retry.get("chapter_state", resume_state.get("chapter_state", {})),
        "character_cards": validator_retry.get("character_cards", resume_state.get("character_cards", [])),
        "character_names": validator_retry.get("character_names", resume_state.get("character_names", [])),
        "project": project,
        "project_id": project_id,
        "chapter_number": int(resume_state.get("chapter_number", 0)),
        "scene_index": scene_index,
        "db": db,
        "core_facts": validator_retry.get("core_facts", resume_state.get("core_facts", {})),
        "current_state": story_state.model_dump(),
        "scene_truth_snapshot": validator_retry.get("scene_truth_snapshot", resume_state.get("scene_truth_snapshot")),
        "word_budget": validator_retry.get("word_budget", resume_state.get("word_budget")),
        "previous_scene_ending": validator_retry.get("previous_scene_ending", ""),
        "previous_scenes_summary": validator_retry.get("previous_scenes_summary", ""),
    }
    return await SceneRecoveryController(budget=STREAM_BUDGET).recover_generated_scene(
        context,
        candidate_text,
    )


def _validator_retry_violation_types(validator_retry: dict) -> set[str]:
    types = {
        str(item.get("type") or "").strip()
        for item in (validator_retry.get("source_violations") or [])
        if isinstance(item, dict) and str(item.get("type") or "").strip()
    }
    if not types:
        error_code = str(validator_retry.get("error_code") or "").strip()
        if error_code:
            types.add(error_code)
    return types


async def _evaluate_validator_retry_gate(
    *,
    quality_gate,
    context: dict,
    validator_retry: dict,
) -> dict:
    """Use component isolation when the persisted failure has one clear owner."""
    return await quality_gate.evaluate_validator_retry(
        context,
        violation_types=_validator_retry_violation_types(validator_retry),
    )


async def _prepare_validator_retry_resume_boundary(
    execution_id: str,
    *,
    validator_retry: dict,
    resume_state: dict,
    db: AsyncSession,
) -> tuple[int, bool]:
    """Resume the persisted DAG at its validator breakpoint, not at layer zero."""
    step_names = {
        str(value).strip()
        for value in (
            validator_retry.get("resume_step"),
            resume_state.get("validator_retry_step"),
        )
        if str(value or "").strip()
    }
    query = select(WorkflowStep).where(
        WorkflowStep.execution_id == execution_id,
        WorkflowStep.status == "pending_validator_retry",
    )
    steps_query = await db.execute(query)
    all_pending_steps = list(steps_query.scalars().all())
    steps = list(all_pending_steps)
    if step_names:
        named = [step for step in steps if step.agent_name in step_names]
        if named:
            steps = named
    if not steps:
        return 0, False

    # Component-isolated retry has already validated the candidate owned by the
    # breakpoint. Mark that checkpoint complete and continue at its successor;
    # replaying the same full review layer recreates the expensive failure loop.
    resume_layer = max(int(step.layer or 0) for step in steps)
    continue_from_layer = resume_layer + 1
    now = _utcnow()
    for step in all_pending_steps:
        if int(step.layer or 0) > resume_layer:
            continue
        previous_output = dict(step.output_snapshot or {})
        previous_output["validator_retry_resume"] = {
            "status": "validated_component",
            "validated_layer": resume_layer,
            "resume_from_layer": continue_from_layer,
        }
        step.status = "completed"
        step.completed_at = now
        step.error_message = ""
        step.output_snapshot = to_json_safe(previous_output)
        if step.duration_ms is None:
            step.duration_ms = 0
    return continue_from_layer, True


async def _resume_from_validator_retry(
    *,
    execution: WorkflowExecution,
    execution_id: str,
    project_id: str,
    persisted: dict,
    validator_retry: dict,
    resume_state: dict,
    candidate_text: str,
    result_updates: dict,
    db: AsyncSession,
) -> bool:
    resume_layer, resume_existing_dag = await _prepare_validator_retry_resume_boundary(
        execution_id,
        validator_retry=validator_retry,
        resume_state=resume_state,
        db=db,
    )
    resume_prepared = await _prepare_workflow_resume(
        execution,
        db=db,
        from_layer=resume_layer,
    )
    if not resume_prepared:
        return False
    execution.result_context = {
        **persisted,
        **result_updates,
        "validator_retry_resume": {
            "resume_from_layer": resume_layer,
            "resume_existing_dag": resume_existing_dag,
            "candidate_hash": hashlib.md5(candidate_text.encode()).hexdigest()[:12],
        },
    }
    await db.commit()

    _broadcast_workflow_event(execution_id, {
        "type": "execution_update",
        "status": "running",
        "timestamp": _utcnow().isoformat(),
    })
    overrides = {
        int(index): text
        for index, text in (resume_state.get("approved_scene_texts") or {}).items()
        if isinstance(text, str) and text
    }
    scene_index = int(validator_retry.get("scene_index", resume_state.get("scene_index", 0)))
    overrides[scene_index] = candidate_text
    _spawn_workflow_task(execution_id, _run_editor_generation(
        execution_id=execution_id,
        project_id=project_id,
        chapter_number=int(resume_state["chapter_number"]),
        pov_character=resume_state.get("pov_character"),
        custom_instructions=resume_state.get("custom_instructions"),
        reviewed_scene_overrides=overrides,
        review_version=int(resume_state.get("review_version", 0)),
        skip_editor_planning=True,
        skip_reviewed_scene_validation=True,
        validator_retry_resumed=True,
        resume_existing_dag=resume_existing_dag,
    ))
    return True


def _schedule_delayed_validator_retries(
    *,
    execution_id: str,
    project_id: str,
    delays: list[int],
):
    async def _delayed_retry_loop():
        from app.db.db_models import async_session

        for delay in delays:
            await asyncio.sleep(delay)
            try:
                async with async_session() as db:
                    exec_query = await db.execute(
                        select(WorkflowExecution).where(
                            WorkflowExecution.id == execution_id,
                        )
                    )
                    execution = exec_query.scalar_one_or_none()
                    if not execution or execution.status != "pending_validator_retry":
                        return

                    from app.services.project_lock import ProjectLockManager
                    lock = ProjectLockManager.get_lock(project_id)
                    lock_waited = 0
                    while lock.locked() and lock_waited < 30:
                        await asyncio.sleep(2)
                        lock_waited += 2
                    await db.refresh(execution)
                    if execution.status != "pending_validator_retry":
                        return
                    if lock.locked():
                        persisted = execution.result_context or {}
                        validator_retry = persisted.get("validator_retry") or {}
                        _record_validator_failure(
                            validator_retry,
                            error="project lock held, skipping this round with delayed re-verify",
                        )
                        execution.result_context = {**persisted, "validator_retry": validator_retry}
                        await db.commit()
                        continue

                    await lock.acquire()
                    try:
                        await db.refresh(execution)
                        if execution.status != "pending_validator_retry":
                            return
                        project = await db.get(Project, uuid.UUID(project_id))
                        if not project:
                            return

                        persisted = execution.result_context or {}
                        validator_retry = persisted.get("validator_retry") or {}
                        resume_state = persisted.get("resume_state") or {}

                        from app.services.quality_gate import QualityGate
                        quality_gate = QualityGate()

                        candidate_text = validator_retry.get("candidate_text", "")
                        scene_contract = validator_retry.get("scene_contract") or resume_state.get("scene_contract") or {}

                        try:
                            gate_result = await _evaluate_validator_retry_gate(
                                quality_gate=quality_gate,
                                validator_retry=validator_retry,
                                context={
                                    "generated_text": candidate_text,
                                    "scene_contract": scene_contract,
                                    "chapter_state": validator_retry.get("chapter_state", resume_state.get("chapter_state", {})),
                                    "character_cards": validator_retry.get("character_cards", resume_state.get("character_cards", [])),
                                    "character_names": validator_retry.get("character_names", resume_state.get("character_names", [])),
                                    "project": project,
                                    "project_id": project_id,
                                    "chapter_number": int(resume_state.get("chapter_number", 0)),
                                    "scene_index": int(validator_retry.get("scene_index", 0)),
                                    "db": db,
                                    "core_facts": validator_retry.get("core_facts", resume_state.get("core_facts", {})),
                                    "scene_truth_snapshot": validator_retry.get("scene_truth_snapshot", resume_state.get("scene_truth_snapshot")),
                                    "previous_scene_ending": validator_retry.get("previous_scene_ending", ""),
                                    "previous_scenes_summary": validator_retry.get("previous_scenes_summary", ""),
                                },
                            )
                        except Exception as gate_exc:
                            logger.warning(
                                "Delayed validator retry QualityGate failed",
                                extra={"execution_id": execution_id, "error": str(gate_exc)},
                            )
                            _record_validator_failure(validator_retry, error=str(gate_exc))
                            execution.result_context = {**persisted, "validator_retry": validator_retry}
                            await db.commit()
                            continue

                        validator_violations = _validator_system_violations(
                            gate_result.get("violations", [])
                        )

                        if gate_result.get("passed") and not validator_violations:
                            await _resume_from_validator_retry(
                                execution=execution,
                                execution_id=execution_id,
                                project_id=project_id,
                                persisted=persisted,
                                validator_retry=validator_retry,
                                resume_state=resume_state,
                                candidate_text=candidate_text,
                                result_updates={
                                    "resumed_from_validator_retry": True,
                                    "gate_result": gate_result,
                                },
                                db=db,
                            )
                            return

                        if validator_violations:
                            _record_validator_failure(
                                validator_retry,
                                violations=validator_violations,
                            )
                            execution.result_context = {**persisted, "validator_retry": validator_retry}
                            await db.commit()
                            continue

                        remaining_blocking = [
                            v for v in gate_result.get("violations", [])
                            if v.get("blocks_commit")
                        ]
                        if remaining_blocking and candidate_text:
                            recovery = await _recover_candidate_after_validator(
                                candidate_text=candidate_text,
                                validator_retry=validator_retry,
                                resume_state=resume_state,
                                project=project,
                                project_id=project_id,
                                db=db,
                            )
                            if not recovery.get("commit_blocked") and recovery.get("generated_text"):
                                await _resume_from_validator_retry(
                                    execution=execution,
                                    execution_id=execution_id,
                                    project_id=project_id,
                                    persisted=persisted,
                                    validator_retry=validator_retry,
                                    resume_state=resume_state,
                                    candidate_text=recovery["generated_text"],
                                    result_updates={
                                        "resumed_from_validator_retry": True,
                                        "auto_repaired_after_validator_retry": True,
                                        "recovery": recovery,
                                    },
                                    db=db,
                                )
                                return

                            gen_step = f"core_generation_{int(validator_retry.get('scene_index', 0)) + 1}"
                            check_step = f"consistency_check_{int(validator_retry.get('scene_index', 0)) + 1}"
                            repaired_candidate = recovery.get("generated_text") or candidate_text
                            resume_state["candidate_text"] = repaired_candidate
                            await _pause_for_human_review(
                                execution_id=execution_id,
                                gen_step=gen_step,
                                check_step=check_step,
                                error_detail="Auto-fix budget exhausted, please confirm remaining conflicts before continuing",
                                pipeline_output={
                                    "final_violations": (recovery.get("final_report") or gate_result).get("violations", []),
                                    "attempts": recovery.get("attempts", []),
                                    "gate_result": gate_result,
                                },
                                resume_state=resume_state,
                                db=db,
                                review_type="content_review",
                            )
                            return

                        await _resume_from_validator_retry(
                            execution=execution,
                            execution_id=execution_id,
                            project_id=project_id,
                            persisted=persisted,
                            validator_retry=validator_retry,
                            resume_state=resume_state,
                            candidate_text=candidate_text,
                            result_updates={
                                "resumed_from_validator_retry": True,
                                "gate_result": gate_result,
                            },
                            db=db,
                        )
                        return
                    finally:
                        lock.release()

            except Exception as loop_exc:
                logger.warning(
                    "Delayed validator retry loop iteration failed",
                    extra={"execution_id": execution_id, "error": str(loop_exc)},
                )

        try:
            async with async_session() as db:
                exec_query = await db.execute(
                    select(WorkflowExecution).where(
                        WorkflowExecution.id == execution_id,
                    )
                )
                execution = exec_query.scalar_one_or_none()
                if not execution or execution.status != "pending_validator_retry":
                    return

                persisted = execution.result_context or {}
                validator_retry = persisted.get("validator_retry") or {}
                resume_state = persisted.get("resume_state") or {}
                scene_index = int(validator_retry.get("scene_index", 0))
                expected_retries = len(validator_retry.get("validator_retry_delays", delays))
                validator_retry["validator_retry_delayed_attempts"] = max(
                    int(validator_retry.get("validator_retry_delayed_attempts", 0)),
                    expected_retries,
                )

                execution.status = "waiting_human_system_review"
                execution.error_message = "Validator continuous failure, delayed re-verify budget exhausted, needs human confirmation of system state"

                review = _build_system_review(
                    scene_index=scene_index,
                    candidate_text=validator_retry.get("candidate_text", ""),
                    validator_retry=validator_retry,
                    resume_state=resume_state,
                )
                resume_state["review_version"] = review["review_version"]
                execution.result_context = _json_snapshot({
                    "review": review,
                    "resume_state": resume_state,
                    "validator_retry": validator_retry,
                })
                await db.commit()

                _broadcast_workflow_event(execution_id, {
                    "type": "execution_update",
                    "status": "waiting_human_system_review",
                    "timestamp": _utcnow().isoformat(),
                })
        except Exception as finalize_exc:
            logger.warning(
                "Delayed validator retry finalization failed",
                extra={"execution_id": execution_id, "error": str(finalize_exc)},
            )

    return _spawn_workflow_task(
        execution_id,
        _delayed_retry_loop(),
        role="validator_retry_scheduler",
    )


async def _pause_for_human_review(
    *,
    execution_id: str,
    gen_step: str,
    check_step: str,
    error_detail: str,
    pipeline_output: dict,
    resume_state: dict,
    db: AsyncSession,
    review_type: str = "content_review",
):
    review_version = int(resume_state.get("review_version", 0)) + 1
    resume_state["review_version"] = review_version
    attempts = pipeline_output.get("attempts", [])
    candidate_text = resume_state.get("candidate_text", "")
    violations = _prepare_review_violations(
        pipeline_output.get("final_violations", []),
        candidate_text,
    )
    # F5: 按窗口聚合 violations — 复用 FBI 的 EditWindowGrouper，
    # 让工作台显示窗口数量而非 issue 数量（5 个 issue → 2 个窗口）。
    # 通用方案，不针对特定 metric；聚合失败时退化为 issue 数量。
    window_count = _aggregate_review_violations_by_window(violations, resume_state, candidate_text)
    current_text_hash = hashlib.md5(candidate_text.encode()).hexdigest()[:12] if candidate_text else ""
    review_scope = _review_scope(resume_state)
    review = {
        "scene_index": resume_state["scene_index"],
        "review_scope": review_scope,
        "candidate_text": candidate_text,
        "violations": violations,
        "attempts": attempts,
        "guidance": _build_review_guidance(violations, attempts, window_count=window_count),
        "window_count": window_count,
        "error_code": pipeline_output.get("error_code", ""),
        "message": error_detail,
        "review_version": review_version,
        "current_text_hash": current_text_hash,
    }
    if review_type == "system_review":
        review["review_type"] = "system_review"
    persisted = _json_snapshot({"review": review, "resume_state": resume_state})
    await _update_workflow_step(
        execution_id, gen_step, "waiting_review",
        output=pipeline_output, error=error_detail, db=db,
    )
    await _update_workflow_step(
        execution_id, check_step, "waiting_review",
        output=pipeline_output, error=error_detail, db=db,
    )
    await _complete_workflow_execution(
        execution_id, "waiting_review", result=persisted, error=error_detail, db=db,
    )


@router.post(
    "/{project_id}/chat",
    summary="Editor dialogue",
)
async def editor_chat(
    project_id: uuid.UUID,
    data: EditorChatRequest,
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    agent = EditorInChiefAgent()
    context = {
        "messages": [{"role": m.role, "content": m.content} for m in data.messages],
        "project_id": str(project_id),
        "db": db,
        "chapter_number": data.chapter_number,
        "project_info": {
            "name": project.name,
            "description": project.description,
            "genre": project.genre,
            "word_count_target": project.word_count_target,
        },
    }
    try:
        result = await agent.execute(context)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Editor execution failed: {str(e)}")
    if result.get("error"):
        raise HTTPException(status_code=500, detail=result["error"])
    return result


@router.post(
    "/{project_id}/chat/stream",
    summary="Editor dialogue (streaming)",
)
async def editor_chat_stream(
    project_id: uuid.UUID,
    data: EditorChatRequest,
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    agent = EditorInChiefAgent()
    context = {
        "messages": [{"role": m.role, "content": m.content} for m in data.messages],
        "project_id": str(project_id),
        "db": db,
        "chapter_number": data.chapter_number,
        "project_info": {
            "name": project.name,
            "description": project.description,
            "genre": project.genre,
            "word_count_target": project.word_count_target,
        },
    }

    async def event_generator():
        async for event in agent.execute_stream(context):
            event_type = event.get("type", "unknown")
            event_data = event.get("data", {})
            yield f"data: {json.dumps({'type': event_type, 'data': event_data}, ensure_ascii=False, default=str)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get(
    "/{project_id}/settings",
    summary="Get editor system settings",
)
async def get_editor_settings(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    core_data = project.core_data or {}
    return {
        "token_budget": core_data.get("editor_token_budget", 10000),
        "budget_mode": core_data.get("editor_budget_mode", "standard"),
    }


@router.put(
    "/{project_id}/settings",
    summary="Update editor system settings",
)
async def update_editor_settings(
    project_id: uuid.UUID,
    data: EditorSettingsRequest,
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    core_data = dict(project.core_data or {})
    if data.token_budget is not None:
        core_data["editor_token_budget"] = data.token_budget
    if data.budget_mode is not None:
        core_data["editor_budget_mode"] = data.budget_mode
    project.core_data = core_data
    await db.commit()

    return {
        "token_budget": core_data.get("editor_token_budget", 10000),
        "budget_mode": core_data.get("editor_budget_mode", "standard"),
    }


@router.get(
    "/{project_id}/chapter-summary/{chapter_number}",
    summary="Get chapter summary",
)
async def get_chapter_summary(
    project_id: uuid.UUID,
    chapter_number: int,
    db: AsyncSession = Depends(get_db),
):
    from app.services.chapter_summary_service import ChapterSummaryService

    service = ChapterSummaryService()
    summary = await service.get_summary(str(project_id), chapter_number, db)
    if not summary:
        raise HTTPException(status_code=404, detail="No summary available for this chapter")
    return summary


@router.get(
    "/{project_id}/context-window",
    summary="Get current chapter full context window",
)
async def get_context_window(
    project_id: uuid.UUID,
    chapter_number: int = Query(1),
    db: AsyncSession = Depends(get_db),
):
    from app.services.chapter_summary_service import ChapterSummaryService

    service = ChapterSummaryService()
    context = await service.get_context_window(str(project_id), chapter_number, db)
    return {"context": context}


async def _build_context_envelope_for_api(
    *,
    project_id: uuid.UUID,
    chapter_number: int,
    db: AsyncSession,
    manual_focus: str | None = None,
    profile_override: dict | None = None,
    dry_run: bool = False,
) -> dict:
    from app.services.context_envelope_builder import get_context_envelope_builder
    from app.services.generation_outline_adapter import GenerationOutlineAdapter
    from app.services.state_manager import StateManager

    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    story_state = await StateManager().get_state(str(project_id))
    adapter = GenerationOutlineAdapter()
    compiled = await adapter.compile_chapter(
        project_id,
        chapter_number,
        story_state.model_dump() if story_state else {},
        db,
        getattr(story_state, "pov_character", None),
    )
    if compiled.has_blocking:
        raise HTTPException(
            status_code=400,
            detail="; ".join(d.message for d in compiled.blocking_diagnostics),
        )
    scene_contracts = [sp.to_dict().get("scene_contract", {}) for sp in compiled.scene_packages]
    builder = get_context_envelope_builder()
    result = await builder.build_chapter_writer_envelope(
        project=project,
        project_id=str(project_id),
        chapter_number=chapter_number,
        db=db,
        story_state=story_state.model_dump() if story_state else {},
        compiled_chapter=compiled.to_dict(),
        scene_contracts=scene_contracts,
        profile_override=profile_override,
        manual_focus=manual_focus,
        dry_run=dry_run,
    )
    return result


@router.post(
    "/{project_id}/context/envelope/preview",
    summary="Preview editor context Envelope",
)
async def preview_context_envelope(
    project_id: uuid.UUID,
    data: ContextEnvelopeRequest,
    db: AsyncSession = Depends(get_db),
):
    result = await _build_context_envelope_for_api(
        project_id=project_id,
        chapter_number=data.chapter_number,
        db=db,
        manual_focus=data.manual_focus,
        profile_override=data.profile_override,
        dry_run=True,
    )
    envelope = result.get("envelope", {})
    return {
        "chapter_number": data.chapter_number,
        "target_agent": data.target_agent,
        "token_report": envelope.get("token_report", {}),
        "blocks": [
            {
                "block_id": block.get("block_id"),
                "block_type": block.get("block_type"),
                "priority": block.get("priority"),
                "compressible": block.get("compressible", False),
                "compacted": block.get("compacted", False),
                "estimated_tokens": block.get("estimated_tokens", 0),
                "source_refs": block.get("source_refs", []),
                "content_preview": _context_block_preview(block),
            }
            for block in envelope.get("blocks", [])
            if isinstance(block, dict)
        ],
        "ledger": result.get("ledger", {}),
        "validation": result.get("validation", {}),
        "compaction": result.get("compaction", {}),
    }


@router.post(
    "/{project_id}/context/compact",
    summary="Manually trigger editor context compression",
)
async def compact_context_envelope(
    project_id: uuid.UUID,
    data: ContextEnvelopeRequest,
    db: AsyncSession = Depends(get_db),
):
    result = await _build_context_envelope_for_api(
        project_id=project_id,
        chapter_number=data.chapter_number,
        db=db,
        manual_focus=data.manual_focus,
        profile_override=data.profile_override,
        dry_run=data.dry_run,
    )
    envelope = result.get("envelope", {})
    return {
        "chapter_number": data.chapter_number,
        "target_agent": data.target_agent,
        "token_report": envelope.get("token_report", {}),
        "ledger": result.get("ledger", {}),
        "validation": result.get("validation", {}),
        "compaction": result.get("compaction", {}),
        "blocks": [
            {
                "block_id": block.get("block_id"),
                "block_type": block.get("block_type"),
                "priority": block.get("priority"),
                "compressible": block.get("compressible", False),
                "compacted": block.get("compacted", False),
                "estimated_tokens": block.get("estimated_tokens", 0),
                "source_refs": block.get("source_refs", []),
                "content_preview": _context_block_preview(block),
            }
            for block in envelope.get("blocks", [])
            if isinstance(block, dict)
        ],
    }


@router.get(
    "/{project_id}/context/ledger/latest",
    summary="Get latest context ledger",
)
async def get_latest_context_ledger(
    project_id: uuid.UUID,
    chapter_number: int = Query(1),
    agent: str = Query("chapter_writer"),
):
    from app.services.context_ledger_service import get_context_ledger_service

    item = get_context_ledger_service().latest(str(project_id), chapter_number, agent)
    if not item:
        raise HTTPException(status_code=404, detail="No context ledger available")
    return item


@router.get(
    "/{project_id}/context/ledger/{ledger_id}",
    summary="Get specified context ledger",
)
async def get_context_ledger(
    project_id: uuid.UUID,
    ledger_id: str,
):
    from app.services.context_ledger_service import get_context_ledger_service

    item = get_context_ledger_service().get(str(project_id), ledger_id)
    if not item:
        raise HTTPException(status_code=404, detail="Context ledger not found")
    return item


@router.get(
    "/{project_id}/forward-constraints",
    summary="Get current chapter forward constraints",
)
async def get_forward_constraints(
    project_id: uuid.UUID,
    chapter_number: int = Query(1),
    db: AsyncSession = Depends(get_db),
):
    from app.services.forward_constraint_builder import ForwardConstraintBuilder

    builder = ForwardConstraintBuilder()
    constraints = await builder.build_all_constraints(str(project_id), chapter_number, db)
    return {"constraints": constraints}


@router.post(
    "/{project_id}/chapters/{chapter_number}/regenerate-from-outline",
    summary="Rebuild chapter per new outline",
    description="Roll back chapter baseline, clear all candidate facts, cancel active workflows, regenerate from the first scene.",
)
async def regenerate_chapter_from_outline(
    project_id: uuid.UUID,
    chapter_number: int,
    db: AsyncSession = Depends(get_db),
):
    from app.db.db_models import ChapterBaseline, Chapter, ChapterSnapshot
    from app.services.state_manager import StateManager
    from app.services.generation_outline_adapter import GenerationOutlineAdapter
    from app.services.project_lock import ProjectLockManager
    from app.services.chapter_commit_service import discard_chapter_outbox

    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    from app.services.chapter_write_guard import REPLACE_WITH_BACKUP, chapter_content_hash
    original_chapter = (
        await db.execute(
            select(Chapter).where(
                Chapter.project_id == project_id,
                Chapter.chapter_number == chapter_number,
            )
        )
    ).scalar_one_or_none()
    source_content_hash = chapter_content_hash(original_chapter.content or "") if original_chapter else ""

    lock = ProjectLockManager.get_lock(str(project_id))
    if lock.locked():
        raise HTTPException(
            status_code=409,
            detail="Project is currently generating, please wait for the current task to complete before rebuilding",
        )
    async with lock:
        active_query = await db.execute(
            select(WorkflowExecution).where(
                WorkflowExecution.project_id == project_id,
                WorkflowExecution.status.in_((
                    "running", "waiting_review", "pending_validator_retry",
                    "waiting_human_content_review", "waiting_human_system_review",
                )),
            )
        )
        active_execs = active_query.scalars().all()
        for ex in active_execs:
            ex.status = "cancelled"
            ex.error_message = "Rebuilt chapter per new outline, auto-terminated"
            ex.updated_at = _utcnow()
            await _finalize_open_workflow_steps(
                str(ex.id),
                status="cancelled",
                error=ex.error_message,
                db=db,
            )
            _broadcast_workflow_event(ex.id, {
                "type": "execution_update",
                "status": "cancelled",
                "timestamp": _utcnow().isoformat(),
            })
        await db.flush()

        await asyncio.sleep(0.3)

        stale_q = await db.execute(
            select(ChapterSnapshot).where(
                ChapterSnapshot.project_id == project_id,
                ChapterSnapshot.chapter_number >= chapter_number,
                ChapterSnapshot.stale == False,
            )
        )
        for stale_snap in stale_q.scalars().all():
            stale_snap.stale = True
        await discard_chapter_outbox(db, project_id, chapter_number, from_chapter=True)
        invalid_chapters_q = await db.execute(
            select(Chapter).where(
                Chapter.project_id == project_id,
                Chapter.chapter_number >= chapter_number,
            )
        )
        for invalid_chapter in invalid_chapters_q.scalars().all():
            if invalid_chapter.chapter_number != chapter_number:
                invalid_chapter.status = "draft"
            invalid_chapter.updated_at = _utcnow()
        await db.flush()
        # Standard mode stores relational data and story state in the same SQLite
        # file through separate connections. Release this transaction's write lock
        # before StateManager persists the restored baseline.
        await db.commit()

        baseline_query = await db.execute(
            select(ChapterBaseline).where(
                ChapterBaseline.project_id == project_id,
                ChapterBaseline.chapter_number == chapter_number,
            )
        )
        baseline = baseline_query.scalar_one_or_none()

        state_manager = StateManager()
        restored_chapter_state = {}

        if baseline and baseline.baseline_story_state:
            await db.commit()
            await state_manager.set_state(str(project_id), StoryState(**baseline.baseline_story_state))
            if baseline.baseline_chapter_state:
                restored_chapter_state = baseline.baseline_chapter_state
            await db.delete(baseline)
            await db.flush()
        elif chapter_number == 1:
            await db.commit()
            await state_manager.set_state(str(project_id), StoryState(
                active_chapter=1,
                active_scene=0,
            ))
        else:
            prev_snapshot_query = await db.execute(
                select(ChapterSnapshot).where(
                    ChapterSnapshot.project_id == project_id,
                    ChapterSnapshot.chapter_number == chapter_number - 1,
                    ChapterSnapshot.stale == False,
                )
            )
            prev_snapshot = prev_snapshot_query.scalar_one_or_none()
            if prev_snapshot and prev_snapshot.story_state:
                restored = StoryState(**prev_snapshot.story_state)
                restored.active_chapter = chapter_number
                restored.active_scene = 0
                await db.commit()
                await state_manager.set_state(str(project_id), restored)
                if prev_snapshot.chapter_state:
                    restored_chapter_state = prev_snapshot.chapter_state
            else:
                raise HTTPException(
                    status_code=409,
                    detail=f"Chapter {chapter_number - 1} has no committed snapshot, cannot safely restore chapter {chapter_number} baseline. Please complete preceding chapters or manually reset state.",
                )

        chapter_query = await db.execute(
            select(Chapter).where(
                Chapter.project_id == project_id,
                Chapter.chapter_number == chapter_number,
            )
        )
        chapter = chapter_query.scalar_one_or_none()
        outline_version = project.outline_version or 0
        story_state = await state_manager.get_state(str(project_id))

        if baseline and baseline.baseline_story_state:
            snapshot_src = "existing_baseline"
        elif chapter_number == 1:
            snapshot_src = "initial_state"
        else:
            snapshot_src = f"snapshot_ch{chapter_number - 1}"

        new_baseline = ChapterBaseline(
            project_id=project_id,
            chapter_number=chapter_number,
            outline_version=outline_version,
            baseline_story_state=story_state.model_dump(),
            baseline_chapter_state=restored_chapter_state,
            snapshot_source=snapshot_src,
        )
        db.add(new_baseline)
        await db.flush()

        adapter = GenerationOutlineAdapter()
        compiled = await adapter.compile_chapter(
            str(project_id), chapter_number,
            story_state.model_dump(), db,
            story_state.pov_character,
            chapter_baseline=new_baseline.baseline_story_state,
        )
        scene_count = len(compiled.scene_packages)
        if compiled.has_blocking:
            blocking_msgs = [d.message for d in compiled.blocking_diagnostics]
            raise HTTPException(
                status_code=400,
                detail=f"Outline compile has blocking issues: {'; '.join(blocking_msgs)}",
            )
        if scene_count == 0:
            raise HTTPException(
                status_code=400,
                detail=f"Chapter {chapter_number} has no compiled scene data, please complete the outline first",
            )

        from app.services.editor_generation_dag import EditorGenerationDAGBuilder

        editor_dag = EditorGenerationDAGBuilder().build(scene_count=scene_count, mode="parallel_review")
        all_step_names = _complete_editor_workflow_step_names(editor_dag.step_names())

        execution_id = await _create_workflow_execution(
            str(project_id),
            f"regenerate_ch{chapter_number}_from_outline",
            all_step_names,
            db,
            dag_snapshot=editor_dag.to_dict(),
            input_context={
                "chapter_number": chapter_number,
                "write_policy": REPLACE_WITH_BACKUP,
                "source_content_hash": source_content_hash,
            },
        )

        _spawn_workflow_task(
            execution_id,
            _run_editor_generation_after_project_unlock(
                execution_id=execution_id,
                project_id=str(project_id),
                chapter_number=chapter_number,
                pov_character=None,
                custom_instructions=None,
            )
        )

        return {
            "execution_id": execution_id,
            "status": "running",
            "chapter_number": chapter_number,
            "message": "Rebuilt chapter per new outline, starting regeneration",
        }


@router.post(
    "/{project_id}/generate",
    summary="Editor-coordinated generation",
    description="Editor coordinates agents to generate chapter content based on dialogue. Immediately returns execution_id; generation runs in background.",
)
async def editor_generate(
    project_id: uuid.UUID,
    data: GenerateFromChatRequest,
    db: AsyncSession = Depends(get_db),
):
    from app.db.db_models import Chapter
    from app.services.project_lock import ProjectLockManager

    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    from app.services.chapter_write_guard import (
        CREATE_ONLY,
        chapter_content_hash,
        has_substantive_chapter_content,
    )
    existing_chapter = (
        await db.execute(
            select(Chapter).where(
                Chapter.project_id == project_id,
                Chapter.chapter_number == data.chapter_number,
            )
        )
    ).scalar_one_or_none()
    if has_substantive_chapter_content(existing_chapter):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "chapter_content_exists",
                "message": "该章已有正文。普通生成不会覆盖现有内容；如确需重写，请使用明确的重新生成流程。",
                "chapter_number": data.chapter_number,
            },
        )
    source_content_hash = chapter_content_hash(existing_chapter.content or "") if existing_chapter else ""
    current_blueprint_revision = int(project.outline_version or 0)
    if (
        data.expected_blueprint_revision is not None
        and int(data.expected_blueprint_revision) != current_blueprint_revision
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "message": "本章蓝图已更新，请重新加载后再生成",
                "current_revision": current_blueprint_revision,
            },
        )
    if ProjectLockManager.get_lock(str(project_id)).locked():
        raise HTTPException(status_code=409, detail="Previous generation task is wrapping up, please retry later")

    # 方案12：清理上次崩溃的running状态工作流（标记为interrupted，可恢复）
    # P0-2 修复（循环 #5）：限定同项目 + 心跳超时（5 分钟），
    # 避免新工作流启动时误伤其他项目或其他章节的正常工作流。
    from app.services.editor_generation_dag_runtime import EditorGenerationDAGRuntime
    try:
        recovered = await EditorGenerationDAGRuntime.recover_interrupted(
            project_id=str(project_id),
            stale_threshold_seconds=300,
        )
        if recovered > 0:
            logger.info(f"recovered {recovered} interrupted editor_dag executions before starting new generation")
    except Exception as e:
        logger.warning(f"failed to recover interrupted executions: {e}")

    active_query = await db.execute(
        select(WorkflowExecution.id).where(
            WorkflowExecution.project_id == project_id,
            WorkflowExecution.status.in_((
                "running", "waiting_review", "pending_validator_retry",
                "waiting_human_content_review", "waiting_human_system_review",
            )),
        )
    )
    if active_query.scalars().first():
        raise HTTPException(status_code=409, detail="Project already has a running generation task or pending human review")

    from app.services.generation_outline_adapter import GenerationOutlineAdapter
    from app.services.story_plan_service import StoryPlanService
    from app.services.state_manager import StateManager

    blueprint = await StoryPlanService().get_chapter_blueprint(
        project_id, data.chapter_number, db,
    )
    if "error" in blueprint:
        raise HTTPException(status_code=400, detail=blueprint["error"])
    blueprint_blocking = [
        item for item in blueprint.get("diagnostics", [])
        if item.get("blocks_generation")
    ]
    if blueprint_blocking:
        raise HTTPException(
            status_code=400,
            detail="; ".join(item.get("message", "本章蓝图不完整") for item in blueprint_blocking),
        )
    if data.expected_blueprint_revision is not None and not blueprint.get("persisted", False):
        raise HTTPException(status_code=409, detail="本章场景蓝图尚未保存，请保存后再生成")

    outline_snapshot = copy.deepcopy(project.outline_data or {})

    state_manager = StateManager()
    story_state = await state_manager.get_state(str(project_id))

    gen_baseline_data = None
    try:
        from app.db.db_models import ChapterBaseline as _CB
        bl_q = await db.execute(
            select(_CB).where(
                _CB.project_id == project_id,
                _CB.chapter_number == data.chapter_number,
            )
        )
        bl_obj = bl_q.scalar_one_or_none()
        if bl_obj and bl_obj.baseline_story_state:
            gen_baseline_data = bl_obj.baseline_story_state
    except Exception:
        pass

    adapter = GenerationOutlineAdapter()
    compiled = await adapter.compile_chapter(
        project_id, data.chapter_number,
        story_state.model_dump(), db,
        data.pov_character or story_state.pov_character,
        chapter_baseline=gen_baseline_data,
        outline_override=outline_snapshot,
    )
    scene_count = len(compiled.scene_packages)
    if compiled.has_blocking:
        raise HTTPException(
            status_code=400,
            detail="; ".join(d.message for d in compiled.blocking_diagnostics),
        )
    if scene_count == 0:
        raise HTTPException(
            status_code=400,
            detail=f"Chapter {data.chapter_number} has no compiled scene data, please complete the outline first before generating",
        )
    from app.services.editor_generation_dag import EditorGenerationDAGBuilder

    editor_dag = EditorGenerationDAGBuilder().build(scene_count=scene_count, mode="parallel_review")
    all_step_names = _complete_editor_workflow_step_names(editor_dag.step_names())

    execution_id = await _create_workflow_execution(
        str(project_id),
        f"editor_generate_ch{data.chapter_number}",
        all_step_names,
        db,
        dag_snapshot=editor_dag.to_dict(),
        input_context={
            "chapter_number": data.chapter_number,
            "blueprint_revision": current_blueprint_revision,
            "plan_version": blueprint.get("plan_version", 0),
            "scene_brief_version": blueprint.get("scene_brief_version", 0),
            "outline_snapshot": outline_snapshot,
            "compiled_chapter_snapshot": compiled.to_dict(),
            "write_policy": CREATE_ONLY,
            "source_content_hash": source_content_hash,
        },
    )

    import asyncio
    _spawn_workflow_task(execution_id, _run_editor_generation(
        execution_id=execution_id,
        project_id=str(project_id),
        chapter_number=data.chapter_number,
        pov_character=data.pov_character,
        custom_instructions=data.custom_instructions,
    ))

    return {
        "execution_id": execution_id,
        "status": "running",
        "chapter_number": data.chapter_number,
    }


async def _run_editor_generation_after_project_unlock(**kwargs):
    """Start a rebuilt chapter only after the rebuild transaction releases its lock."""
    from app.services.project_lock import ProjectLockManager

    lock = ProjectLockManager.get_lock(kwargs["project_id"])
    while lock.locked():
        await asyncio.sleep(0.05)
    await _run_editor_generation(**kwargs)


async def _run_editor_generation(
    execution_id: str,
    project_id: str,
    chapter_number: int,
    pov_character: str | None,
    custom_instructions: str | None,
    reviewed_scene_overrides: dict[int, str] | None = None,
    review_version: int = 0,
    skip_editor_planning: bool = False,
    skip_reviewed_scene_validation: bool = False,
    reviewed_scene_repair_indexes: set[int] | None = None,
    validator_retry_resumed: bool = False,
    human_review_override: bool = False,
    resume_existing_dag: bool = False,
):
    from app.db.db_models import async_session, Project, Chapter
    from app.services.state_manager import StateManager
    from app.services.project_lock import ProjectLockManager

    async with async_session() as db:
        # ---- Phase 0: Start diagnostic trace ----
        _trace_id = ""
        try:
            from app.services.editor_trace_collector import get_editor_trace_collector
            from app.models.editor_trace import PreGenerationMetrics, PostGenerationMetrics
            _trace_collector = get_editor_trace_collector()
            _trace = _trace_collector.start_trace(project_id, chapter_number, execution_id)
            _trace_id = _trace.trace_id
        except Exception:
            _trace_id = ""

        lock = ProjectLockManager.get_lock(project_id)
        if lock.locked():
            await _complete_workflow_execution(
                execution_id,
                "failed",
                error="Project is currently generating, please retry later",
                db=db,
            )
            return
        await lock.acquire()
        from app.services.llm_metrics import (
            get_llm_metrics,
            pop_llm_metric_context,
            push_llm_metric_context,
        )
        llm_metric_token = push_llm_metric_context(
            execution_id=execution_id,
            project_id=project_id,
            chapter_number=chapter_number,
            workflow_step="editor_generation",
        )
        try:
            project = await db.get(Project, uuid.UUID(project_id))
            if not project:
                await _complete_workflow_execution(execution_id, "failed", error="Project not found", db=db)
                return

            # Load one style snapshot for the whole chapter workflow.  The
            # chapter Writer, review gates and every FBI repair lane must see
            # the same profile instead of independently loading drifting
            # copies (or silently falling back to persona-only context).
            try:
                from app.services.memory_core import CoreMemoryService

                _active_style_profile = (
                    await CoreMemoryService().get_active_style_profile(project_id)
                    or {}
                )
            except Exception as _style_exc:
                logger.warning(
                    "[editor_generation] active style snapshot load failed: %s",
                    _style_exc,
                )
                _active_style_profile = {}
            _workflow_style_context = _active_style_profile

            def _style_repair_context_snapshot() -> dict:
                profile = (
                    _workflow_style_context
                    if isinstance(_workflow_style_context, dict)
                    else {}
                )
                return {
                    "style_context": profile,
                    "style_profile": profile,
                    "style_prompt": profile.get("style_prompt", ""),
                    "style_embedding": profile.get("style_embedding") or {},
                    "persona_card": profile.get("persona_card") or {},
                }

            execution = await db.get(WorkflowExecution, execution_id)
            execution_input = (
                execution.input_context
                if execution and isinstance(execution.input_context, dict)
                else {}
            )
            outline_snapshot = execution_input.get("outline_snapshot")
            outline_data = copy.deepcopy(
                outline_snapshot
                if isinstance(outline_snapshot, dict)
                else (project.outline_data or {})
            )
            run_blueprint_revision = int(
                execution_input.get("blueprint_revision", project.outline_version or 0) or 0
            )

            state_manager = StateManager()
            story_state = await state_manager.get_state(project_id)

            async def persist_story_state(next_state: StoryState) -> None:
                # Standard mode stores relational rows and StoryState through
                # separate SQLite connections. End the ORM transaction before
                # crossing that boundary so resume and rollback paths cannot
                # retain a competing SQLite lock.
                await db.commit()
                await state_manager.set_state(project_id, next_state)

            from app.db.db_models import ChapterBaseline, ChapterSnapshot
            baseline_query = await db.execute(
                select(ChapterBaseline).where(
                    ChapterBaseline.project_id == uuid.UUID(project_id),
                    ChapterBaseline.chapter_number == chapter_number,
                )
            )
            baseline = baseline_query.scalar_one_or_none()
            outline_version = run_blueprint_revision

            if baseline and baseline.outline_version < outline_version:
                from app.services.chapter_commit_service import discard_chapter_outbox

                stale_q = await db.execute(
                    select(ChapterSnapshot).where(
                        ChapterSnapshot.project_id == uuid.UUID(project_id),
                        ChapterSnapshot.chapter_number >= chapter_number,
                        ChapterSnapshot.stale == False,
                    )
                )
                for stale_snap in stale_q.scalars().all():
                    stale_snap.stale = True
                await discard_chapter_outbox(
                    db,
                    uuid.UUID(project_id),
                    chapter_number,
                    from_chapter=True,
                )
                invalid_chapters_q = await db.execute(
                    select(Chapter).where(
                        Chapter.project_id == uuid.UUID(project_id),
                        Chapter.chapter_number >= chapter_number,
                    )
                )
                for invalid_chapter in invalid_chapters_q.scalars().all():
                    invalid_chapter.status = "draft"
                    if invalid_chapter.chapter_number == chapter_number:
                        invalid_chapter.content = ""
                    invalid_chapter.updated_at = _utcnow()
                await db.flush()
                from app.services.project_chapter_aggregate_service import (
                    refresh_project_chapter_aggregates,
                )
                await refresh_project_chapter_aggregates(
                    db,
                    project_id=uuid.UUID(project_id),
                    project=project,
                )
                # Standard mode persists relational rows and StoryState through
                # separate SQLite connections. Release the relational write lock
                # before restoring the state baseline.
                await db.commit()

                if chapter_number == 1:
                    fresh_state = StoryState(active_chapter=1, active_scene=0)
                    await persist_story_state(fresh_state)
                    story_state = fresh_state
                else:
                    prev_snap_query = await db.execute(
                        select(ChapterSnapshot).where(
                            ChapterSnapshot.project_id == uuid.UUID(project_id),
                            ChapterSnapshot.chapter_number == chapter_number - 1,
                            ChapterSnapshot.stale == False,
                        )
                    )
                    prev_snap = prev_snap_query.scalar_one_or_none()
                    if prev_snap and prev_snap.story_state:
                        restored = StoryState(**prev_snap.story_state)
                        restored.active_chapter = chapter_number
                        restored.active_scene = 0
                        await persist_story_state(restored)
                        story_state = restored
                    else:
                        await _complete_workflow_execution(
                            execution_id, "failed",
                            error=f"Chapter {chapter_number-1} has no committed snapshot, cannot safely rebuild chapter {chapter_number} baseline. Please complete preceding chapters first",
                            db=db,
                        )
                        return
                await db.delete(baseline)
                await db.flush()
                baseline = None

            if baseline:
                baseline_state = baseline.baseline_story_state
                if baseline_state:
                    await persist_story_state(StoryState(**baseline_state))
                    story_state = StoryState(**baseline_state)
            else:
                init_chapter_state = {}
                if chapter_number == 1:
                    fresh_state = StoryState(active_chapter=1, active_scene=0)
                    await persist_story_state(fresh_state)
                    story_state = fresh_state
                else:
                    prev_snap_query = await db.execute(
                        select(ChapterSnapshot).where(
                            ChapterSnapshot.project_id == uuid.UUID(project_id),
                            ChapterSnapshot.chapter_number == chapter_number - 1,
                            ChapterSnapshot.stale == False,
                        )
                    )
                    prev_snap = prev_snap_query.scalar_one_or_none()
                    if prev_snap and prev_snap.story_state:
                        restored = StoryState(**prev_snap.story_state)
                        restored.active_chapter = chapter_number
                        restored.active_scene = 0
                        await persist_story_state(restored)
                        story_state = restored
                        if prev_snap.chapter_state:
                            init_chapter_state = prev_snap.chapter_state
                    else:
                        await _complete_workflow_execution(
                            execution_id, "failed",
                            error=f"Chapter {chapter_number-1} has no committed snapshot, cannot fully initialize chapter {chapter_number}. Please complete preceding chapters first",
                            db=db,
                        )
                        return

                snapshot_src = "initial_state" if chapter_number == 1 else f"snapshot_ch{chapter_number-1}"
                baseline_story_state = story_state.model_dump()
                baseline = ChapterBaseline(
                    project_id=uuid.UUID(project_id),
                    chapter_number=chapter_number,
                    outline_version=outline_version,
                    baseline_story_state=baseline_story_state,
                    baseline_chapter_state=init_chapter_state,
                    snapshot_source=snapshot_src,
                )
                db.add(baseline)
                await db.flush()
                # The baseline is immutable input for the run. Persist it before
                # waiting on LLM calls so standard-mode SQLite does not retain a
                # write lock for the full chapter generation lifetime.
                await db.commit()

            async def stop_if_cancelled() -> bool:
                if not await _is_workflow_cancelled(execution_id, db):
                    return False
                logger.info("Workflow %s terminated by user, stopping generation", execution_id)
                try:
                    if baseline and baseline.baseline_story_state:
                        await persist_story_state(StoryState(**baseline.baseline_story_state))
                except Exception as rollback_exc:
                    logger.warning(
                        "Cancelled workflow checkpoint rollback failed",
                        extra={"execution_id": execution_id, "error": str(rollback_exc)},
                    )
                return True

            await _update_workflow_step(execution_id, "editor_planning", "running", db=db)
            _editor_planning_start = __import__('time').monotonic()
            editor = EditorInChiefAgent()
            if skip_editor_planning:
                class _ManualRevisionAcceptedEditor:
                    async def execute(self, _context: dict) -> dict:
                        return {
                            "scene_contracts": [],
                            "scene_context_packages": [],
                            "manual_revision_accepted": True,
                        }

                editor = _ManualRevisionAcceptedEditor()
            editor_messages = []
            if custom_instructions:
                editor_messages.append({
                    "role": "user",
                    "content": f"User creation requirements for chapter {chapter_number}: {custom_instructions}",
                })

            # ---- Editor decision context injection ----
            # Build EditorDecisionContext and inject brief into context before editor Agent executes
            decision_context_brief = None
            try:
                from app.services.editor_decision_context_builder import get_editor_decision_context_builder
                from app.models.editor_decision_context import EditorDecisionContextRequest

                _builder = get_editor_decision_context_builder()
                _ctx_request = EditorDecisionContextRequest(
                    project_id=project_id,
                    chapter_number=chapter_number,
                )
                _decision_ctx = await _builder.build(_ctx_request)
                decision_context_brief = _builder.build_brief(_decision_ctx)

                # Inject brief as structured message into editor message list
                brief_parts = []
                if decision_context_brief.chapter_goal and decision_context_brief.chapter_goal != "per outline":
                    brief_parts.append(f"[Chapter Goal] {decision_context_brief.chapter_goal}")
                if decision_context_brief.character_states and decision_context_brief.character_states != "no special states":
                    brief_parts.append(f"[Character States] {decision_context_brief.character_states}")
                if decision_context_brief.facts_to_continue:
                    brief_parts.append(f"[Must Continue] {', '.join(decision_context_brief.facts_to_continue)}")
                if decision_context_brief.recommended_pacing:
                    brief_parts.append(f"[Recommended Pacing] {decision_context_brief.recommended_pacing}")
                if decision_context_brief.forbidden_items:
                    brief_parts.append(f"[Forbidden Items] {', '.join(decision_context_brief.forbidden_items)}")
                if decision_context_brief.style_protection:
                    brief_parts.append(f"[Style Protection] {decision_context_brief.style_protection}")
                if decision_context_brief.commercial_hooks:
                    brief_parts.append(f"[Commercial Hooks] {', '.join(decision_context_brief.commercial_hooks)}")
                if decision_context_brief.risk_notes:
                    brief_parts.append(f"[Risk Alerts] {', '.join(decision_context_brief.risk_notes)}")

                if brief_parts:
                    editor_messages.append({
                        "role": "user",
                        "content": "Editor decision basis:\n" + "\n".join(brief_parts),
                    })
                    logger.info(
                        "[editor_planning] Decision context injected, budget_used=%d",
                        _decision_ctx.token_budget_used,
                    )
            except Exception as _ctx_exc:
                logger.warning("[editor_planning] Decision context build failed, continuing without context plan: %s", _ctx_exc)

            # ---- Chapter-level contract compilation ----
            # Compile ChapterPlanContract from decision context, as editor's formal product
            chapter_plan_contract = None
            try:
                if decision_context_brief is not None:
                    from app.services.editor_planning_compiler import get_editor_planning_compiler

                    _compiler = get_editor_planning_compiler()
                    chapter_plan_contract = await _compiler.compile_from_decision_context(
                        project_id=project_id,
                        chapter_number=chapter_number,
                        decision_context=_decision_ctx,
                        outline_data=outline_data,
                    )
                    logger.info(
                        "[editor_planning] Chapter-level contract compiled: ch=%s function=%s emotion=%s",
                        chapter_plan_contract.chapter_number,
                        chapter_plan_contract.chapter_function,
                        chapter_plan_contract.target_emotion,
                    )
            except Exception as _cpc_exc:
                logger.warning("[editor_planning] Chapter-level contract compile failed, continuing without contract plan: %s", _cpc_exc)

            try:
                import time
                editor_deadline = time.monotonic() + 300  # match outer timeout
                editor_result = await asyncio.wait_for(
                    editor.execute({
                        "project_id": project_id,
                        "chapter_number": chapter_number,
                        "story_state": story_state.model_dump(),
                        "outline": outline_data,
                        "outline_snapshot": outline_data,
                        "db": db,
                        "messages": editor_messages,
                        "deadline": editor_deadline,
                        "execution_id": execution_id,
                    }),
                    timeout=300,
                )
                # Inner LLM timeout/failure also returns degraded=True, unified degradation path
                if editor_result.get("degraded"):
                    degraded_reason = editor_result.get("degraded_reason", "editor planning degraded")
                    logger.warning(f"[editor_planning] Editor planning degraded: {degraded_reason}")
                    # Try to recover more contracts from intermediate state
                    if not editor_result.get("scene_contracts"):
                        try:
                            step_query = await db.execute(
                                select(WorkflowStep).where(
                                    WorkflowStep.execution_id == execution_id,
                                    WorkflowStep.agent_name == "editor_planning",
                                )
                            )
                            step = step_query.scalar_one_or_none()
                            if step and step.output_snapshot:
                                editor_result["scene_contracts"] = step.output_snapshot.get("intermediate_contracts", [])
                                editor_result["chapter_rhythm_map"] = step.output_snapshot.get("intermediate_rhythm_map", "")
                        except Exception as e:
                            logger.warning(f"[editor_planning] Degraded recovery of intermediate state failed: {e}")
                    await _update_workflow_step(execution_id, "editor_planning", "completed",
                                                output={
                                                    "scene_count": len(editor_result.get("scene_contracts", [])),
                                                    "degraded": True,
                                                    "degraded_reason": degraded_reason,
                                                }, db=db)
                else:
                    await _update_workflow_step(execution_id, "editor_planning", "completed",
                                                output={"scene_count": len(editor_result.get("scene_contracts", []))}, db=db)
            except asyncio.TimeoutError:
                logger.warning("[editor_planning] Editor planning timeout, trying compiled contract degradation")

                # Try to recover intermediate plan from workflow step
                intermediate_contracts = []
                intermediate_rhythm = ""
                try:
                    step_query = await db.execute(
                        select(WorkflowStep).where(
                            WorkflowStep.execution_id == execution_id,
                            WorkflowStep.agent_name == "editor_planning",
                        )
                    )
                    step = step_query.scalar_one_or_none()
                    if step and step.output_snapshot:
                        intermediate_contracts = step.output_snapshot.get("intermediate_contracts", [])
                        intermediate_rhythm = step.output_snapshot.get("intermediate_rhythm_map", "")
                        if intermediate_contracts:
                            logger.info(
                                f"[editor_planning] Recovered {len(intermediate_contracts)} scene contracts from intermediate state"
                            )
                except Exception as e:
                    logger.warning(f"[editor_planning] Recovery of intermediate state failed: {e}")

                await _update_workflow_step(execution_id, "editor_planning", "completed",
                                            output={"scene_count": len(intermediate_contracts), "degraded": True, "degraded_reason": "outer timeout (300s)"}, db=db)
                editor_result = {
                    "scene_contracts": intermediate_contracts,
                    "chapter_rhythm_map": intermediate_rhythm,
                    "response": "",
                    "degraded": True,
                    "degraded_reason": "outer timeout (300s)",
                }
            except Exception as e:
                await _update_workflow_step(execution_id, "editor_planning", "failed", error=str(e), db=db)
                await _complete_workflow_execution(execution_id, "failed", error=str(e), db=db)
                try:
                    if baseline and baseline.baseline_story_state:
                        await persist_story_state(StoryState(**baseline.baseline_story_state))
                except Exception as _state_exc:
                    logger.warning("persist_story_state failed during error path: %s", _state_exc)
                return

            if editor_result.get("error"):
                await _update_workflow_step(execution_id, "editor_planning", "failed", error=editor_result["error"], db=db)
                # ---- Phase 0: Record editor_planning failure ----
                if _trace_id:
                    try:
                        _trace_collector.record_layer(_trace_id, "editor_planning", "failed",
                            duration_ms=int((__import__('time').monotonic() - _editor_planning_start) * 1000),
                            error=editor_result["error"])
                    except Exception:
                        pass
                await _complete_workflow_execution(execution_id, "failed", error=editor_result["error"], db=db)
                try:
                    if baseline and baseline.baseline_story_state:
                        await persist_story_state(StoryState(**baseline.baseline_story_state))
                except Exception as _state_exc:
                    logger.warning("persist_story_state failed during error path: %s", _state_exc)
                return

            from app.services.generation_outline_adapter import GenerationOutlineAdapter
            adapter = GenerationOutlineAdapter()
            compiled = await adapter.compile_chapter(
                project_id, chapter_number,
                story_state.model_dump(), db,
                pov_character or story_state.pov_character,
                chapter_baseline=baseline.baseline_story_state if baseline and baseline.baseline_story_state else None,
                outline_override=outline_data,
            )
            if compiled.has_blocking:
                import logging as _logging
                _logger = _logging.getLogger(__name__)
                for d in compiled.blocking_diagnostics:
                    _logger.error(f"[compile] BLOCKING: {d.code}: {d.message}")
                await _complete_workflow_execution(execution_id, "failed",
                    error=f"Outline compile failed: {'; '.join(d.message for d in compiled.blocking_diagnostics)}", db=db)
                try:
                    if baseline and baseline.baseline_story_state:
                        await persist_story_state(StoryState(**baseline.baseline_story_state))
                except Exception as _state_exc:
                    logger.warning("persist_story_state failed during error path: %s", _state_exc)
                return {"execution_id": execution_id, "status": "failed",
                        "error": f"Outline compile failed: {'; '.join(d.message for d in compiled.blocking_diagnostics)}"}

            scene_packages = [sp.to_dict() for sp in compiled.scene_packages]

            # ---- Phase 0: Record editor_planning + scene_contract layer ----
            if _trace_id:
                try:
                    _ep_status = "degraded" if editor_result.get("degraded") else "ok"
                    _trace_collector.record_layer(_trace_id, "editor_planning", _ep_status,
                        duration_ms=int((__import__('time').monotonic() - _editor_planning_start) * 1000),
                        detail=f"scene_count={len(scene_packages)}")
                    _trace_collector.record_layer(_trace_id, "scene_contract", "ok",
                        detail=f"scene_packages={len(scene_packages)}")
                    # Record pre_metrics
                    _scene_contracts_raw = editor_result.get("scene_contracts", [])
                    _must_show_counts = []
                    _forbidden_counts = []
                    for sc in _scene_contracts_raw:
                        if isinstance(sc, dict):
                            ms = sc.get("must_show", [])
                            _must_show_counts.append(len(ms) if isinstance(ms, list) else 0)
                            fb = sc.get("forbidden", [])
                            _forbidden_counts.append(len(fb) if isinstance(fb, list) else 0)
                    _trace_collector.record_pre_metrics(_trace_id, PreGenerationMetrics(
                        chapter_plan_contract_item_count=len([c for c in [chapter_plan_contract] if c]),
                        scene_contract_item_counts=[1] * len(scene_packages),
                        per_scene_must_show_counts=_must_show_counts,
                        per_scene_forbidden_counts=_forbidden_counts,
                    ))
                except Exception:
                    pass

            # Preserve degraded flag, not overridden by subsequent completed
            _planning_output = {"scene_count": len(scene_packages)}
            if editor_result.get("degraded"):
                _planning_output["degraded"] = True
                _planning_output["degraded_reason"] = editor_result.get("degraded_reason", "")
            await _update_workflow_step(execution_id, "editor_planning", "completed",
                                        output=_planning_output, db=db)

            all_content = []
            total_seeds = 0
            all_patches = []
            consistency_reports = []
            pending_scene_effects = []
            if resume_existing_dag:
                pending_scene_effects.extend(
                    await _load_persisted_pending_scene_effects(
                        execution_id,
                        db=db,
                    )
                )
            if baseline and baseline.baseline_chapter_state:
                chapter_state = dict(baseline.baseline_chapter_state)
                chapter_state.setdefault("established_facts", [])
                chapter_state.setdefault("character_states", {})
                chapter_state.setdefault("completed_events", [])
                chapter_state.setdefault("active_constraints", [])
            else:
                chapter_state = {"established_facts": [], "character_states": {}, "completed_events": [], "active_constraints": []}
            previous_scenes_summary = ""
            previous_scene_ending = ""

            scene_contracts = normalize_editor_scene_contracts(editor_result.get("scene_contracts"))

            # ---- Chapter-level contract enhances scene contracts ----
            # Attach ChapterPlanContract chapter-level metadata to scene contracts.
            # Note: chapter-level must_progress / must_not_resolve / foreshadowing_ops
            # Cannot scatter into each scene's text constraints, otherwise would recreate information overload
            # They are saved in chapter_plan_context, for V2 allocator, review and trace reading.
            if chapter_plan_contract is not None:
                try:
                    cpc = chapter_plan_contract
                    for sc in scene_contracts:
                        if cpc.target_emotion and "target_emotion" not in sc:
                            sc["target_emotion"] = cpc.target_emotion
                        if cpc.chapter_function and "chapter_function" not in sc:
                            sc["chapter_function"] = cpc.chapter_function
                        if cpc.main_conflict and "main_conflict" not in sc:
                            sc["main_conflict"] = cpc.main_conflict
                        if cpc.reader_question_to_open and "reader_question_to_open" not in sc:
                            sc["reader_question_to_open"] = cpc.reader_question_to_open
                        sc["chapter_plan_context"] = {
                            "must_progress": list(cpc.must_progress or []),
                            "must_not_resolve": list(cpc.must_not_resolve or []),
                            "foreshadowing_ops": list(cpc.foreshadowing_ops or []),
                        }
                    logger.info(
                        "[editor_planning] Chapter-level contract enhanced %d scene contracts",
                        len(scene_contracts),
                    )
                except Exception as _cpc_merge_exc:
                    logger.warning("[editor_planning] Chapter-level contract enhance scene contracts failed: %s", _cpc_merge_exc)

            if len(scene_contracts) < len(scene_packages):
                if not scene_contracts:
                    logger.warning(
                        f"Editor returned no scene contracts, using compiled contracts for all {len(scene_packages)} scenes"
                    )
                else:
                    logger.warning(
                        f"Scene contract count ({len(scene_contracts)}) < scene package count ({len(scene_packages)}), "
                        f"using compiled contracts for missing scenes"
                    )
                while len(scene_contracts) < len(scene_packages):
                    idx = len(scene_contracts)
                    pkg = scene_packages[idx] if idx < len(scene_packages) else {}
                    compiled_contract = pkg.get("scene_contract", {})
                    if compiled_contract:
                        scene_contracts.append(compiled_contract)
                    else:
                        beat = pkg.get("scene_beat", {})
                        scene_contracts.append({
                            "scene_id": f"c{chapter_number}-s{idx+1}",
                            "pov_character": pkg.get("pov_character", ""),
                            "goal": beat.get("goal_text", beat.get("goal", f"Scene {idx+1}")),
                            "conflict": beat.get("conflict_text", beat.get("conflict", "")),
                            "must_show": beat.get("must_show", []),
                            "forbidden": beat.get("forbidden", ["repeat previous scene completed events"]),
                            "ending_state": beat.get("outcome_text", beat.get("outcome", "")),
                        })

            for _si, _scene_contract in enumerate(scene_contracts):
                if not isinstance(_scene_contract, dict) or _si >= len(compiled.scene_packages):
                    continue
                _compiled_contract = compiled.scene_packages[_si].scene_contract
                _source_of_truth = copy.deepcopy(_compiled_contract.get("source_of_truth") or {})
                if _source_of_truth:
                    _scene_contract["source_of_truth"] = _source_of_truth
                    _scene_contract["must_show_outline"] = _source_of_truth.get("must_show_outline", [])
                    _scene_contract["forbidden_outline"] = _source_of_truth.get("forbidden_outline", [])
                for _field in (
                    "scene_id", "goal", "conflict", "ending_state",
                    "pov_character", "must_show", "forbidden",
                ):
                    if _field in _compiled_contract:
                        _scene_contract[_field] = copy.deepcopy(_compiled_contract[_field])

            try:
                from app.services.information_budget_compiler import get_information_budget_compiler

                _budget_compiler = get_information_budget_compiler()
                scene_contracts = [
                    _budget_compiler.apply_to_scene_contract(sc) if isinstance(sc, dict) else sc
                    for sc in scene_contracts
                ]
                _planning_budget_summary = _information_budget_summary(scene_contracts)
                _planning_output = {"scene_count": len(scene_packages), "information_budget_summary": _planning_budget_summary}
                if editor_result.get("degraded"):
                    _planning_output["degraded"] = True
                    _planning_output["degraded_reason"] = editor_result.get("degraded_reason", "")
                await _update_workflow_step(execution_id, "editor_planning", "completed",
                                            output=_planning_output, db=db)
            except Exception as _budget_exc:
                logger.warning("[editor_planning] Information budget apply failed: %s", _budget_exc)

            # ---- Canonical compile pipeline: ContractItem -> SceneAllocation -> InformationBudget -> WriterInputPacket ----
            # Normalize, allocate, budget-control, and compile all contract info into WriterInputPacket
            _v2_compile_ok = False
            try:
                from app.services.contract_item_normalizer import get_contract_item_normalizer
                from app.services.scene_allocation_compiler import get_scene_allocation_compiler
                from app.services.information_budget_compiler import get_information_budget_compiler
                from app.services.writer_input_compiler import get_writer_input_compiler
                from app.models.editor_trace import SceneWriterInputSnapshot

                _normalizer = get_contract_item_normalizer()
                _allocation_compiler = get_scene_allocation_compiler()
                _budget_compiler = get_information_budget_compiler()
                _writer_compiler = get_writer_input_compiler()

                # 1. Normalize all sources into ContractItem
                _all_items = _normalizer.normalize_all(
                    chapter_plan=chapter_plan_contract.model_dump() if chapter_plan_contract else None,
                    scene_contracts=scene_contracts,
                )

                # 2. Allocate to scenes
                _scene_functions = [sc.get("chapter_function", "") for sc in scene_contracts] if scene_contracts else []
                _scene_emotions = [sc.get("target_emotion", "") for sc in scene_contracts] if scene_contracts else []
                _allocation_plan = _allocation_compiler.compile(
                    chapter_id=f"ch_{chapter_number}",
                    items=_all_items,
                    scene_count=len(scene_packages),
                    scene_functions=_scene_functions,
                    scene_emotions=_scene_emotions,
                )

                # 3. Compile into CompiledSceneContract
                _compiled_contracts = _allocation_compiler.compile_to_contracts(_allocation_plan)

                # 4. Information budget compile + WriterInputPacket compile
                for _ci, _compiled_contract in enumerate(_compiled_contracts):
                    # Information budget
                    _compiled_contract = _budget_compiler.compile(_compiled_contract, mode="commercial")

                    # POV
                    _pov = scene_contracts[_ci].get("pov_character") if _ci < len(scene_contracts) else None

                    # Previous scene summary
                    _prev_summary = previous_scenes_summary if _ci == 0 else ""

                    # Compile WriterInputPacket
                    _source_scene_contract = scene_contracts[_ci] if _ci < len(scene_contracts) else {}
                    _packet = _writer_compiler.compile(
                        _compiled_contract,
                        pov_character=_pov,
                        previous_scene_summary=_prev_summary,
                        source_scene_contract=_source_scene_contract,
                    )

                    # Inject into scene_package
                    if _ci < len(scene_packages):
                        scene_packages[_ci]["writer_input_packet"] = _packet.model_dump()

                    # ---- Save WriterInputPacket snapshot to trace ----
                    if _trace_id:
                        try:
                            _budget_check = _compiled_contract.budget_check()
                            _degraded = [i.text[:50] for i in _compiled_contract.hidden_carry_forward if i.status == "deferred"]
                            _snapshot = SceneWriterInputSnapshot(
                                scene_index=_ci,
                                v2_used=True,
                                must_include=_packet.must_include,
                                must_avoid=_packet.must_avoid,
                                active_capabilities=_packet.active_capabilities,
                                activation_reasons=_packet.activation_reasons,
                                source_trace=_packet.source_trace,
                                writer_input_enrichment=_packet.writer_input_enrichment,
                                hard_max_chars=_packet.output_constraints.get("hard_max_chars", 0),
                                target_chars=_packet.output_constraints.get("target_chars", 0),
                                ending_state=_packet.ending_state,
                                soft_hints=_packet.soft_suggestions,
                                visible_facts=_packet.visible_facts,
                                degraded_items=_degraded,
                                budget_check=_budget_check,
                            )
                            _trace_collector._traces[_trace_id].writer_input_snapshots.append(_snapshot)
                        except Exception:
                            pass

                _v2_compile_ok = True
                logger.info(
                    "[V2] Compile pipeline completed: %d items -> %d scenes, WriterInputPacket injected",
                    len(_all_items), len(scene_packages),
                )
            except Exception as _v2_exc:
                logger.exception("WriterInputPacket compile pipeline failed")
                # ---- Record compile failure to trace ----
                if _trace_id:
                    try:
                        _trace_collector._traces[_trace_id].v2_compile_status = "failed"
                        _trace_collector._traces[_trace_id].v2_compile_error = str(_v2_exc)[:200]
                        _trace_collector.record_layer(_trace_id, "v2_compile", "failed", error=str(_v2_exc)[:200])
                        _trace_collector.save_trace(_trace_id)
                    except Exception as _trace_exc:
                        logger.debug("trace collection failed: %s", _trace_exc)
                raise RuntimeError(f"WriterInputPacket compile failed: {_v2_exc}") from _v2_exc

            if _v2_compile_ok and _trace_id:
                try:
                    _trace_collector._traces[_trace_id].v2_compile_status = "ok"
                    _trace_collector.record_layer(_trace_id, "v2_compile", "ok",
                        detail=f"items={len(_all_items)}, scenes={len(scene_packages)}")
                    # Mid-fall, ensure writer_input_snapshots are not lost
                    _trace_collector.save_trace(_trace_id)
                except Exception as _trace_exc:
                    logger.debug("trace collection failed: %s", _trace_exc)

            # ---- Long-context chapter-level Writer ----
            # Default path: Writer sees full scene_map and chapter context at once, generates entire chapter;
            # Then SceneSpanAligner splits chapter into scene spans, reusing existing scene review/repair pipeline.
            # When human review restores scenes, keep old per-scene path to avoid regenerating entire chapter and disturbing reviewed content.
            chapter_writer_scene_texts: dict[int, str] = {}
            chapter_writer_alignment: dict = {}
            chapter_writer_quality_reports: dict[int, dict] = {}
            chapter_context_ledger: dict = {}
            chapter_candidate_payload: dict = {}
            fallback_scene_results: dict[int, dict] = {}
            use_chapter_writer = not reviewed_scene_overrides and not skip_editor_planning
            _context_compile_completed = False
            _chapter_writer_completed = False
            _scene_alignment_started = False
            if use_chapter_writer:
                for _si, _scene_contract in enumerate(scene_contracts):
                    if not isinstance(_scene_contract, dict) or _si >= len(compiled.scene_packages):
                        continue
                    _compiled_pkg = compiled.scene_packages[_si]
                    _compiled_contract = _compiled_pkg.scene_contract
                    # The saved blueprint snapshot is authoritative.  The editor may
                    # enrich delivery and rhythm, but it cannot silently rewrite the
                    # scene goal, conflict or ending state selected by the user.
                    if _compiled_contract.get("source_of_truth"):
                        _source_of_truth = copy.deepcopy(_compiled_contract["source_of_truth"])
                        _scene_contract["source_of_truth"] = _source_of_truth
                        _scene_contract["must_show_outline"] = _source_of_truth.get("must_show_outline", [])
                        _scene_contract["forbidden_outline"] = _source_of_truth.get("forbidden_outline", [])
                    for _field in (
                        "scene_id", "goal", "conflict", "ending_state",
                        "pov_character", "must_show", "forbidden",
                    ):
                        if _field in _compiled_contract:
                            _scene_contract[_field] = copy.deepcopy(_compiled_contract[_field])
                    if _compiled_contract.get("editor_enrichment"):
                        _scene_contract.setdefault("editor_enrichment", _compiled_contract.get("editor_enrichment", {}))
                    _scene_contract.setdefault("word_budget", _compiled_pkg.word_budget)
                    _scene_contract.setdefault("scene_id", _compiled_pkg.scene_id)
                    _scene_contract.setdefault("chapter_number", chapter_number)
                    _scene_contract.setdefault("scene_index", _si)

                try:
                    await _update_workflow_step(execution_id, "context_compile", "running", db=db)
                    from app.services.context_envelope_builder import get_context_envelope_builder

                    _ctx_builder = get_context_envelope_builder()
                    _ctx_result = await _ctx_builder.build_chapter_writer_envelope(
                        project=project,
                        project_id=project_id,
                        chapter_number=chapter_number,
                        db=db,
                        story_state=story_state.model_dump(),
                        compiled_chapter=compiled.to_dict(),
                        scene_contracts=scene_contracts,
                        chapter_plan_contract=chapter_plan_contract,
                        custom_instructions=custom_instructions,
                        style_profile=_active_style_profile,
                    )
                    _envelope = _ctx_result["envelope"]
                    chapter_context_ledger = _ctx_result.get("ledger", {})
                    _token_report = _envelope.get("token_report", {})
                    await _update_workflow_step(
                        execution_id,
                        "context_compile",
                        "completed",
                        output={
                            "estimated_tokens": _token_report.get("estimated_input_tokens", 0),
                            "limit_tokens": _token_report.get("limit_tokens", 0),
                            "compaction_applied": _token_report.get("compaction_applied", False),
                            "ledger_id": chapter_context_ledger.get("ledger_id", ""),
                            "validation": _ctx_result.get("validation", {}),
                        },
                        db=db,
                    )
                    _context_compile_completed = True
                    if _trace_id:
                        try:
                            _trace_collector.record_layer(
                                _trace_id,
                                "context_compile",
                                "ok",
                                detail=f"tokens={_token_report.get('estimated_input_tokens', 0)} ledger={chapter_context_ledger.get('ledger_id', '')}",
                            )
                        except Exception:
                            pass

                    await _update_workflow_step(execution_id, "chapter_writer", "running", db=db)
                    from app.agents.core_generation import CoreGenerationAgent

                    _chapter_writer_step_start = time.perf_counter()
                    _chapter_writer_orchestration_timing = {}
                    _phase_start = time.perf_counter()
                    _writer_packet = _ctx_builder.build_chapter_writer_packet(
                        envelope=_envelope,
                        chapter_number=chapter_number,
                        scene_contracts=scene_contracts,
                        custom_instructions=custom_instructions,
                    )
                    _chapter_writer_orchestration_timing["packet_build_ms"] = int(
                        (time.perf_counter() - _phase_start) * 1000
                    )
                    # Feed one compact active-style asset to the chapter
                    # prompt, Skill runtime and Writer validation.  Do not
                    # depend on the truncated tail of ScenePreparation output.
                    _chapter_style_context = (
                        _writer_packet.get("style_context")
                        or _ctx_result.get("style_context")
                        or {}
                    )
                    _workflow_style_context = _chapter_style_context
                    _style_directives = {
                        str(item.get("scene_id") or index): item.get("style_directive") or {}
                        for index, item in enumerate(_writer_packet.get("scene_map") or [])
                        if isinstance(item, dict) and item.get("style_directive")
                    }
                    _writer_packet["skill_context"] = {
                        "project_id": str(project_id),
                        "db": db,
                        "scene_contract": scene_contracts[0] if scene_contracts else {},
                        "scene_context_package": {
                            "chapter_state": story_state.model_dump(),
                            "style_profile": _chapter_style_context,
                            "style_prompt": _chapter_style_context.get("style_prompt", ""),
                            "style_sample_passages": _chapter_style_context.get("style_sample_passages", []),
                            "style_embedding": _chapter_style_context.get("style_embedding", {}),
                            "persona_card": _chapter_style_context.get("persona_card", {}),
                            "style_statistics": _chapter_style_context.get("style_statistics", {}),
                            "evolution_report": _chapter_style_context.get("evolution_report", {}),
                        },
                        "chapter_state": story_state.model_dump(),
                        "chapter_number": chapter_number,
                        "pov_character": (
                            scene_contracts[0].get("pov_character")
                            if scene_contracts and isinstance(scene_contracts[0], dict)
                            else None
                        ),
                        "style_context": _chapter_style_context,
                        "style_profile": _chapter_style_context,
                        "style_prompt": _chapter_style_context.get("style_prompt", ""),
                        "style_samples": _chapter_style_context.get("style_sample_passages", []),
                        "style_features": _chapter_style_context.get("style_features", {}),
                        "style_embedding": _chapter_style_context.get("style_embedding", {}),
                        "style_directive": _style_directives,
                        "quality_extensions": (scene_contracts[0] if scene_contracts else {}).get("quality_extensions", {}),
                    }
                    _writer_packet["persona_card"] = _chapter_style_context.get("persona_card", {})
                    _chapter_writer_orchestration_timing["persona_load_ms"] = 0
                    _phase_start = time.perf_counter()
                    _writer_result = await _await_workflow_operation(
                        execution_id,
                        "chapter_writer",
                        CoreGenerationAgent().execute({
                            "chapter_writer_packet": _writer_packet,
                        }),
                        timeout_seconds=_CHAPTER_WRITER_TIMEOUT_SECONDS,
                    )
                    _chapter_writer_orchestration_timing["agent_execute_ms"] = int(
                        (time.perf_counter() - _phase_start) * 1000
                    )
                    _chapter_draft = _writer_result.get("generated_text", "")
                    if not _chapter_draft.strip():
                        raise RuntimeError("Chapter-level Writer returned empty text")
                    _chapter_writer_orchestration_timing["step_total_ms"] = int(
                        (time.perf_counter() - _chapter_writer_step_start) * 1000
                    )
                    await _update_workflow_step(
                        execution_id,
                        "chapter_writer",
                        "completed",
                        output={
                            "word_count": count_words(_chapter_draft),
                            "scene_count": len(scene_contracts),
                            "context_ledger_id": _writer_result.get("context_ledger_id", ""),
                            "prompt_debug": _writer_result.get("prompt_debug", {}),
                            "orchestration_timing_ms": _chapter_writer_orchestration_timing,
                        },
                        db=db,
                    )
                    # 方案18 步骤1：记录 writer 执行报告到反馈服务，供主编规划下一章时参考
                    _execution_report = _writer_result.get("execution_report")
                    if _execution_report and project_id and chapter_number:
                        try:
                            from app.services.editor_feedback_service import EditorFeedbackService
                            EditorFeedbackService.record_execution_report(
                                str(project_id), int(chapter_number), _execution_report
                            )
                        except Exception:
                            pass
                    _chapter_writer_completed = True
                    if _trace_id:
                        try:
                            _trace_collector.record_layer(
                                _trace_id,
                                "chapter_writer",
                                "ok",
                                detail=f"chars={len(_chapter_draft)}",
                            )
                        except Exception:
                            pass

                    await _update_workflow_step(execution_id, "scene_alignment", "running", db=db)
                    _scene_alignment_started = True
                    from app.services.scene_span_aligner import SceneSpanAligner

                    _scene_map = _writer_packet.get("scene_map", [])

                    # 方案20：语义切分函数（LLM 辅助识别场景边界）
                    # 注意：align() 是同步方法，所以这里先 await LLM 调用，结果缓存在闭包中供同步函数使用
                    _semantic_split_cache: dict[str, list[str] | None] = {}

                    async def _run_semantic_split(text: str, scene_ids: list[str]) -> list[str] | None:
                        """LLM 辅助场景切分，返回切分后的场景文本列表。"""
                        cache_key = str(hash(text))
                        if cache_key in _semantic_split_cache:
                            return _semantic_split_cache[cache_key]
                        try:
                            from app.agents.core_generation import CoreGenerationAgent
                            _llm = await CoreGenerationAgent().get_llm_client()
                            _prompt = (
                                f"请将以下正文切分为 {len(scene_ids)} 个场景。\n\n"
                                f"要求：\n"
                                f"1. 按场景边界切分（时间跳转、地点转换、视角切换等）\n"
                                f"2. 不要在句子中间切分\n"
                                f"3. 每个场景应该是一个完整的叙事单元\n"
                                f"4. 用 [[SCENE_SPLIT]] 标记切分点，只输出切分点标记\n\n"
                                f"正文：\n{text}"
                            )
                            _result = await _llm.generate(
                                system_prompt="你是一个场景切分助手，负责将章节正文按场景边界切分。",
                                user_prompt=_prompt,
                                temperature=0.3,
                                task_type=LLMTaskType.JSON_DETECTION,
                            )
                            # 按 [[SCENE_SPLIT]] 切分
                            parts = _result.split("[[SCENE_SPLIT]]")
                            scenes = [p.strip() for p in parts if p.strip()]
                            if len(scenes) == len(scene_ids):
                                _semantic_split_cache[cache_key] = scenes
                                return scenes
                            _semantic_split_cache[cache_key] = None
                            return None
                        except Exception as _split_exc:
                            logger.warning("[scene_alignment] semantic split failed: %s", _split_exc)
                            _semantic_split_cache[cache_key] = None
                            return None

                    def _semantic_split_fn(text: str, scene_ids: list[str]) -> list[str] | None:
                        """同步包装器：从缓存读取语义切分结果。"""
                        cache_key = str(hash(text))
                        return _semantic_split_cache.get(cache_key)

                    # 方案20：先尝试语义切分（如果标记缺失），预填充缓存
                    _fallback_text = SceneSpanAligner().strip_markers(_chapter_draft)
                    _scene_ids_list = [str(item.get("scene_id") or f"scene_{i+1}") for i, item in enumerate(_scene_map)]
                    import re as _re_mod
                    _marker_spans_check = list(_re_mod.finditer(
                        r"^\s*(?:\[\[SCENE:[^\]]+\]\]|<<<SCENE_ID:[^>]+>>>)\s*$",
                        _chapter_draft,
                        _re_mod.MULTILINE,
                    )) if _chapter_draft else []
                    _needs_semantic = not _marker_spans_check or len(_marker_spans_check) != len(_scene_ids_list)
                    if _needs_semantic and _fallback_text.strip():
                        await _run_semantic_split(_fallback_text, _scene_ids_list)

                    # 方案20：标记缺失时重试 writer（最多 2 次），然后语义切分，最后才段落均分
                    _max_alignment_attempts = 2
                    chapter_writer_alignment = SceneSpanAligner().align(
                        _chapter_draft,
                        _scene_map,
                        semantic_split_fn=_semantic_split_fn,
                    )
                    _spans = chapter_writer_alignment.get("spans", [])
                    _empty_spans = [
                        item.get("scene_index")
                        for item in _spans
                        if not str(item.get("text") or "").strip()
                    ]

                    # 方案20：对齐失败时重试 writer（最多 2 次）
                    if (len(_spans) != len(scene_packages) or _empty_spans) and chapter_writer_alignment.get("method") != "explicit_markers":
                        for _retry_idx in range(_max_alignment_attempts):
                            logger.warning(
                                "[scene_alignment] alignment failed (spans=%s/%s, empty=%s), retrying writer (attempt %d/%d)",
                                len(_spans), len(scene_packages), _empty_spans, _retry_idx + 1, _max_alignment_attempts,
                            )
                            try:
                                _retry_result = await _await_workflow_operation(
                                    execution_id,
                                    "chapter_writer",
                                    CoreGenerationAgent().execute({
                                        "chapter_writer_packet": {
                                            **_writer_packet,
                                            "marker_emphasis": True,  # 提示 writer 强调场景标记
                                        },
                                    }),
                                    timeout_seconds=_CHAPTER_WRITER_TIMEOUT_SECONDS,
                                )
                                _retry_draft = _retry_result.get("generated_text", "")
                                if _retry_draft.strip():
                                    _chapter_draft = _retry_draft
                                    # 预填充语义切分缓存
                                    _retry_fallback_text = SceneSpanAligner().strip_markers(_chapter_draft)
                                    if _retry_fallback_text.strip():
                                        await _run_semantic_split(_retry_fallback_text, _scene_ids_list)
                                    chapter_writer_alignment = SceneSpanAligner().align(
                                        _chapter_draft,
                                        _scene_map,
                                        semantic_split_fn=_semantic_split_fn,
                                    )
                                    _spans = chapter_writer_alignment.get("spans", [])
                                    _empty_spans = [
                                        item.get("scene_index")
                                        for item in _spans
                                        if not str(item.get("text") or "").strip()
                                    ]
                                    if len(_spans) == len(scene_packages) and not _empty_spans:
                                        logger.info("[scene_alignment] retry succeeded on attempt %d", _retry_idx + 1)
                                        break
                            except Exception as _retry_exc:
                                logger.warning("[scene_alignment] writer retry %d failed: %s", _retry_idx + 1, _retry_exc)

                    if len(_spans) != len(scene_packages) or _empty_spans:
                        raise RuntimeError(
                            f"Chapter prose scene alignment failed: spans={len(_spans)}/{len(scene_packages)}, empty={_empty_spans}"
                        )
                    chapter_writer_scene_texts = {
                        int(item["scene_index"]): str(item.get("text") or "").strip()
                        for item in _spans
                    }
                    # 反例驱动去 AI 味：chapter_writer 模式下补跑 coordinator 闭环（parallel_review 模式跳过 run_scene_pipeline，需在此处收集反例）
                    try:
                        from app.agents.ai_quality_coordinator import (
                            AIQualityCoordinatorAgent,
                            CoordinatorInput,
                            _effective_mode_for_advisory,
                        )
                        from app.models.generation_features import GenerationFeaturePolicy as _cw_GFP

                        _cw_coordinator = AIQualityCoordinatorAgent()
                        _cw_feature_policy = _cw_GFP()
                        _cw_source_modes = _cw_coordinator._resolve_modes(_cw_feature_policy)
                        if any(m != "off" for m in _cw_source_modes.values()):
                            # 对每个场景文本运行确定性后处理 + AIFlavorChecker
                            for _cw_si, _cw_text in chapter_writer_scene_texts.items():
                                if not _cw_text:
                                    continue
                                _cw_reports = {}
                                _cw_ai_flavor_mode = str(_cw_source_modes.get("ai_flavor") or "off")
                                if _cw_ai_flavor_mode in ("report", "assist", "enforce"):
                                    try:
                                        from app.services.quality_checkers.ai_flavor_checker import AIFlavorChecker
                                        _cw_af_report = AIFlavorChecker().check(_cw_text, {}, {})
                                        _cw_af_report["mode"] = _cw_ai_flavor_mode
                                        _cw_reports["ai_flavor"] = _cw_af_report
                                    except Exception as _cw_af_err:
                                        logger.warning("[chapter_writer] AIFlavorChecker scene %d failed: %s", _cw_si, _cw_af_err)

                                if not _cw_reports:
                                    continue
                                _cw_quality_memory = (project.core_data or {}).get("quality_memory", {})
                                if not isinstance(_cw_quality_memory, dict):
                                    _cw_quality_memory = {}
                                _cw_coord_input = CoordinatorInput(
                                    scene_contract={},
                                    draft_text=_cw_text,
                                    quality_reports=_cw_reports,
                                    project_quality_memory=_cw_quality_memory,
                                    source_modes=_cw_source_modes,
                                    current_chapter=chapter_number,
                                    current_scene=_cw_si,
                                )
                                _cw_coord_output = _cw_coordinator.coordinate(_cw_coord_input)
                                _cw_memory_advisories = [
                                    adv for adv in _cw_coordinator._collect_advisories(_cw_reports)
                                    if _effective_mode_for_advisory(adv, _cw_source_modes) in ("report", "assist", "enforce")
                                ]
                                if _cw_memory_advisories or _cw_coord_output.recent_quality_patterns:
                                    chapter_writer_quality_reports[_cw_si] = {
                                        "workflow_advisory": {
                                            "scores": {},
                                            "advisories": _cw_memory_advisories,
                                            "mode": "report",
                                        }
                                    }
                    except Exception as _cw_coord_err:
                        logger.warning("[chapter_writer] coordinator 闭环失败，不影响主流程: %s", _cw_coord_err)
                    from app.services.chapter_commit_health import ChapterCommitHealthChecker

                    _span_health = ChapterCommitHealthChecker().evaluate(
                        scene_texts=chapter_writer_scene_texts,
                    )
                    if not _span_health.get("allowed", True):
                        _span_reason = "; ".join(
                            f"scene {issue.get('scene_index')}: {issue.get('code')}"
                            for issue in _span_health.get("hard_issues", [])
                        )
                        raise RuntimeError(f"Chapter prose scene alignment health failed: {_span_reason}")
                    await _update_workflow_step(
                        execution_id,
                        "scene_alignment",
                        "completed",
                        output={
                            "method": chapter_writer_alignment.get("method"),
                            "span_count": len(_spans),
                            "warnings": chapter_writer_alignment.get("warnings", []),
                            "candidate_ready": True,
                        },
                        db=db,
                    )
                    _chapter_candidate_text = "\n\n".join(
                        chapter_writer_scene_texts.get(_candidate_idx, "")
                        for _candidate_idx in range(len(scene_packages))
                    ).strip()
                    chapter_candidate_payload = build_chapter_candidate_payload(
                        chapter_number=chapter_number,
                        candidate_text=_chapter_candidate_text,
                        scene_count=len(scene_packages),
                        alignment=chapter_writer_alignment,
                        source="chapter_writer",
                        context_ledger_id=chapter_context_ledger.get("ledger_id", ""),
                    )
                    await _record_chapter_candidate(
                        execution_id,
                        chapter_candidate_payload,
                        db=db,
                    )
                    if _trace_id:
                        try:
                            _trace_collector.record_layer(
                                _trace_id,
                                "scene_alignment",
                                "ok" if not chapter_writer_alignment.get("warnings") else "degraded",
                                detail=f"method={chapter_writer_alignment.get('method')} spans={len(_spans)}",
                            )
                        except Exception:
                            pass
                except Exception as _chapter_writer_exc:
                    use_chapter_writer = False
                    logger.warning("[chapter_writer] Chapter-level Writer degraded to per-scene path: %s", _chapter_writer_exc)
                    if not _context_compile_completed:
                        await _update_workflow_step(
                            execution_id,
                            "context_compile",
                            "degraded",
                            output={"fallback_to_scene_writer": True},
                            error=str(_chapter_writer_exc)[:300],
                            db=db,
                        )
                    if not _chapter_writer_completed:
                        await _update_workflow_step(
                            execution_id,
                            "chapter_writer",
                            "degraded",
                            output={"fallback_to_scene_writer": True},
                            error=str(_chapter_writer_exc)[:300],
                            db=db,
                        )
                    await _update_workflow_step(
                        execution_id,
                        "scene_alignment",
                        "degraded" if _scene_alignment_started else "skipped",
                        output={"fallback_to_scene_writer": True},
                        error=str(_chapter_writer_exc)[:300] if _scene_alignment_started else None,
                        db=db,
                    )
                    if _trace_id:
                        try:
                            _trace_collector.record_layer(
                                _trace_id,
                                "chapter_writer",
                                "degraded",
                                error=str(_chapter_writer_exc)[:200],
                            )
                        except Exception:
                            pass
            else:
                await _update_workflow_step(execution_id, "context_compile", "skipped", db=db)
                await _update_workflow_step(execution_id, "chapter_writer", "skipped", db=db)
                await _update_workflow_step(execution_id, "scene_alignment", "skipped", db=db)

            # parallel_review normally consumes ChapterWriter-aligned text and
            # therefore has no scene-generation nodes of its own.  If the
            # chapter-level Writer is unavailable, generate the missing scene
            # texts with the existing scene pipeline before entering review.
            # Keep this sequential so later scenes receive the preceding
            # scene's ending/summary, and defer all official side effects until
            # the normal ordered commit stage.
            if not use_chapter_writer:
                for _fallback_si, _fallback_pkg in enumerate(scene_packages):
                    _fallback_contract = (
                        scene_contracts[_fallback_si]
                        if scene_contracts and _fallback_si < len(scene_contracts)
                        else {}
                    )
                    if isinstance(_fallback_contract, dict):
                        _fallback_contract.setdefault("chapter_number", chapter_number)
                        _fallback_contract.setdefault("scene_index", _fallback_si)
                    _fallback_pkg["chapter_state"] = chapter_state
                    _fallback_pkg["previous_scenes_summary"] = previous_scenes_summary
                    _fallback_pkg["previous_scene_ending"] = previous_scene_ending
                    _fallback_pkg["scene_contract"] = _fallback_contract

                    _fallback_step = f"core_generation_{_fallback_si + 1}"
                    await _update_workflow_step(
                        execution_id,
                        _fallback_step,
                        "running",
                        output={"source": "scene_writer_fallback"},
                        db=db,
                    )
                    _fallback_pre_generated = (reviewed_scene_overrides or {}).get(
                        _fallback_si
                    )
                    _fallback_locked = _skip_locked_scene_validation(
                        _fallback_si,
                        reviewed_scene_overrides,
                        skip_reviewed_scene_validation,
                        reviewed_scene_repair_indexes,
                    )
                    _fallback_result = await run_scene_pipeline(
                        project_id=project_id,
                        chapter_number=chapter_number,
                        scene_package=_fallback_pkg,
                        custom_instructions=custom_instructions,
                        project=project,
                        db=db,
                        pre_generated_text=_fallback_pre_generated,
                        skip_pre_generated_validation=_fallback_locked,
                        defer_pre_generated_postprocess=_fallback_locked,
                        recovery_budget=_review_retry_recovery_budget(
                            _fallback_si,
                            reviewed_scene_repair_indexes,
                        ),
                    )
                    _fallback_text = str(
                        _fallback_result.get("generated_text") or ""
                    ).strip()
                    fallback_scene_results[_fallback_si] = _fallback_result
                    _fallback_snapshot = _fallback_result.get("scene_truth_snapshot")
                    if _fallback_snapshot:
                        _fallback_pkg["scene_truth_snapshot"] = _fallback_snapshot

                    if not _fallback_text:
                        _fallback_error = str(
                            _fallback_result.get("underlying_error")
                            or _fallback_result.get("error_code")
                            or "scene writer returned empty text"
                        )
                        await _update_workflow_step(
                            execution_id,
                            _fallback_step,
                            "failed",
                            output={
                                "source": "scene_writer_fallback",
                                "error_code": _fallback_result.get("error_code", ""),
                            },
                            error=_fallback_error[:300],
                            db=db,
                        )
                        raise RuntimeError(
                            "Per-scene Writer fallback failed for "
                            f"scene {_fallback_si + 1}: {_fallback_error}"
                        )

                    chapter_writer_scene_texts[_fallback_si] = _fallback_text
                    await _update_workflow_step(
                        execution_id,
                        _fallback_step,
                        "completed",
                        output={
                            "source": "scene_writer_fallback",
                            "word_count": count_words(_fallback_text),
                            "error_code": _fallback_result.get("error_code", ""),
                            "commit_blocked": bool(
                                _fallback_result.get("commit_blocked", False)
                            ),
                        },
                        db=db,
                    )
                    previous_scene_ending = _fallback_text[-800:]
                    _fallback_summary = _fallback_text[:800]
                    previous_scenes_summary = (
                        f"Scene {_fallback_si + 1} summary: {_fallback_summary}\n\n"
                        + previous_scenes_summary
                    )[:2000]

            from app.services.editor_generation_dag import EditorGenerationDAGBuilder
            from app.services.editor_generation_dag_runtime import (
                EditorDAGAbort,
                EditorGenerationDAGRuntime,
            )

            scene_exec: dict[int, dict] = {}
            workflow_update_lock = asyncio.Lock()

            async def update_step_locked(*args, **kwargs):
                async with workflow_update_lock:
                    return await _update_workflow_step(*args, **kwargs)

            async def complete_workflow_locked(*args, **kwargs):
                async with workflow_update_lock:
                    return await _complete_workflow_execution(*args, **kwargs)

            async def pause_for_human_review_locked(**kwargs):
                async with workflow_update_lock:
                    return await _pause_for_human_review(**kwargs)

            async def skip_future_steps(scene_idx: int) -> None:
                for future_idx in range(scene_idx + 1, len(scene_packages)):
                    for future_step_name in [
                        "scene_preparation",
                        "core_generation",
                        "detail_harvest",
                        "state_update",
                        "consistency_check",
                    ]:
                        await update_step_locked(
                            execution_id,
                            f"{future_step_name}_{future_idx + 1}",
                            "skipped",
                            db=db,
                        )
                for final_step in [
                    "fact_extraction",
                    "style_polish",
                    "final_acceptance",
                    "final_acceptance_delta_intake",
                    "final_acceptance_delta_blueprint",
                    "final_acceptance_delta_execute",
                    "final_acceptance_delta_recheck",
                    "write_chapter",
                ]:
                    await update_step_locked(execution_id, final_step, "skipped", db=db)

            async def handle_scene_prepare(node, context) -> None:
                nonlocal previous_scene_ending, previous_scenes_summary
                if await stop_if_cancelled():
                    raise EditorDAGAbort({"content": "", "status": "cancelled"})

                # DAGContext 是 handler 的权威节点视图；使用 context.node
                # 确保恢复/重放时读取的是 Runtime 实际调度节点。
                scene_idx = int(context.node.scene_index or 0)
                pkg = scene_packages[scene_idx]
                prep_step = f"scene_preparation_{scene_idx + 1}"

                pkg["chapter_state"] = chapter_state
                pkg["previous_scenes_summary"] = previous_scenes_summary
                pkg["previous_scene_ending"] = previous_scene_ending
                if use_chapter_writer:
                    pkg["_chapter_writer_generated"] = True
                    pkg["chapter_context_ledger_id"] = chapter_context_ledger.get("ledger_id", "")
                    pkg["chapter_scene_alignment"] = chapter_writer_alignment

                scene_contract = None
                if scene_contracts and scene_idx < len(scene_contracts):
                    scene_contract = scene_contracts[scene_idx]
                    scene_contract["chapter_number"] = chapter_number
                    scene_contract["scene_index"] = scene_idx
                if scene_contract and scene_idx < len(compiled.scene_packages):
                    compiled_contract = compiled.scene_packages[scene_idx].scene_contract
                    if compiled_contract.get("source_of_truth"):
                        scene_contract["source_of_truth"] = compiled_contract["source_of_truth"]
                        editor_enrichment = scene_contract.get("editor_enrichment")
                        if not editor_enrichment or not isinstance(editor_enrichment, dict):
                            scene_contract["editor_enrichment"] = compiled_contract.get("editor_enrichment", {})
                        else:
                            for key in ["pov_lock", "temporal_anchor", "opening_state", "ending_state"]:
                                if scene_contract.get(key) and not editor_enrichment.get(key):
                                    editor_enrichment[key] = scene_contract[key]
                            scene_contract["editor_enrichment"] = editor_enrichment
                        source_of_truth = compiled_contract["source_of_truth"]
                        scene_contract["must_show_outline"] = source_of_truth.get("must_show_outline", [])
                        scene_contract["forbidden_outline"] = source_of_truth.get("forbidden_outline", [])
                pkg["scene_contract"] = scene_contract
                scene_exec[scene_idx] = {"pkg": pkg, "scene_contract": scene_contract}

                await update_step_locked(execution_id, prep_step, "running", db=db)
                await update_step_locked(
                    execution_id,
                    prep_step,
                    "completed",
                    output={"runtime": "editor_generation_dag"},
                    db=db,
                )

            async def handle_scene_generate(node, context) -> None:
                scene_idx = int(node.scene_index or 0)
                pkg = scene_exec.setdefault(scene_idx, {}).get("pkg") or scene_packages[scene_idx]
                gen_step = f"core_generation_{scene_idx + 1}"

                await update_step_locked(execution_id, gen_step, "running", db=db)
                pre_generated_scene_text = (reviewed_scene_overrides or {}).get(scene_idx)
                if pre_generated_scene_text is None and use_chapter_writer:
                    pre_generated_scene_text = chapter_writer_scene_texts.get(scene_idx)
                locked_scene = _skip_locked_scene_validation(
                    scene_idx,
                    reviewed_scene_overrides,
                    skip_reviewed_scene_validation,
                    reviewed_scene_repair_indexes,
                )
                result = await run_scene_pipeline(
                    project_id=project_id,
                    chapter_number=chapter_number,
                    scene_package=pkg,
                    custom_instructions=custom_instructions,
                    project=project,
                    db=db,
                    pre_generated_text=pre_generated_scene_text,
                    skip_pre_generated_validation=locked_scene,
                    defer_pre_generated_postprocess=locked_scene,
                    recovery_budget=_review_retry_recovery_budget(
                        scene_idx,
                        reviewed_scene_repair_indexes,
                    ),
                )
                if await stop_if_cancelled():
                    raise EditorDAGAbort({"content": "", "status": "cancelled"})

                generated_text = result.get("generated_text", "")
                error_code = result.get("error_code", "")
                commit_blocked = result.get("commit_blocked", False)
                recovery_mode = result.get("recovery_mode", "none")
                attempts = result.get("attempts", [])
                scene_recovery_status = result.get("scene_recovery_status", "")
                underlying_error = result.get("underlying_error", "")
                circuit_state = result.get("circuit_state")
                pipeline_output = {
                    "recovery_mode": recovery_mode,
                    "attempts": attempts,
                    "scene_recovery_status": scene_recovery_status,
                    "error_code": error_code,
                    "candidate_text": result.get("candidate_text", ""),
                    "needs_human_review": result.get("needs_human_review", False),
                    "runtime": "editor_generation_dag",
                }
                fbi_v2_outcome = result.get("fbi_v2_outcome") or {}
                if fbi_v2_outcome:
                    pipeline_output["fbi_v2_outcome"] = fbi_v2_outcome
                    try:
                        if not _trace_id:
                            raise RuntimeError("editor trace is not initialized")
                        from app.models.editor_trace import SceneRepairOutcome

                        summary = fbi_v2_outcome.get("summary", {}) if isinstance(fbi_v2_outcome, dict) else {}
                        round_results = fbi_v2_outcome.get("round_results", []) if isinstance(fbi_v2_outcome, dict) else []
                        first_round = round_results[0] if round_results else {}
                        last_round = round_results[-1] if round_results else {}
                        status = str(fbi_v2_outcome.get("status") or "")
                        if status == "resolved":
                            v2_status = "v2_resolved"
                        elif status in {"needs_workbench", "failed"}:
                            v2_status = "v2_failed"
                        else:
                            v2_status = "v2_degraded"
                        _trace_collector._traces[_trace_id].repair_outcomes.append(SceneRepairOutcome(
                            scene_index=scene_idx,
                            v2_status=v2_status,
                            total_orders=int(summary.get("total_orders") or 0),
                            succeeded_orders=int(summary.get("succeeded") or 0),
                            failed_orders=int(summary.get("failed") or 0),
                            needs_human=list(fbi_v2_outcome.get("needs_human") or []),
                            legacy_fallback=False,
                            failure_reasons=list(fbi_v2_outcome.get("failure_reasons") or []),
                            text_hash_before=str(first_round.get("text_hash_before") or ""),
                            text_hash_after=str(last_round.get("text_hash_after") or hashlib.md5(generated_text.encode()).hexdigest()[:12]),
                        ))
                        _trace_collector._traces[_trace_id].scene_text_hashes.append(
                            hashlib.md5(generated_text.encode()).hexdigest()[:12]
                        )
                        _trace_collector.record_layer(
                            _trace_id,
                            "fbi_v2_repair",
                            "ok" if status == "resolved" else "failed",
                            detail=f"orders={summary.get('total_orders', 0)} rounds={summary.get('rounds_used', 0)}",
                        )
                        _trace_collector.save_trace(_trace_id)
                    except Exception as trace_exc:
                        logger.warning("Failed to record FBI V2 trace outcome: %s", trace_exc)
                elif _trace_id and generated_text:
                    try:
                        _trace_collector._traces[_trace_id].scene_text_hashes.append(
                            hashlib.md5(generated_text.encode()).hexdigest()[:12]
                        )
                        _trace_collector.save_trace(_trace_id)
                    except Exception:
                        pass

                final_violations = (
                    result.get("consistency_report", {})
                    .get("quality_gate", {})
                    .get("violations", [])
                )
                if final_violations:
                    pipeline_output["final_violations"] = final_violations
                if circuit_state:
                    pipeline_output["circuit_state"] = circuit_state
                if underlying_error:
                    pipeline_output["underlying_error"] = underlying_error

                candidate_block = classify_candidate_block(
                    chapter_writer_mode=use_chapter_writer,
                    generated_text=generated_text,
                    error_code=error_code,
                    commit_blocked=commit_blocked,
                    violations=final_violations,
                )
                if (
                    use_chapter_writer
                    and (commit_blocked or error_code)
                    and not candidate_block.get("block")
                    and generated_text
                ):
                    pipeline_output["quality_report_only"] = True
                    pipeline_output["candidate_block_decision"] = candidate_block
                    if result.get("consistency_report"):
                        result["consistency_report"]["quality_report_only"] = True
                    commit_blocked = False
                    error_code = ""

                scene_exec[scene_idx].update({
                    "result": result,
                    "generated_text": generated_text,
                    "error_code": error_code,
                    "commit_blocked": commit_blocked,
                    "recovery_mode": recovery_mode,
                    "attempts": attempts,
                    "scene_recovery_status": scene_recovery_status,
                    "underlying_error": underlying_error,
                    "pipeline_output": pipeline_output,
                    "final_violations": final_violations,
                })
                # P0-1 修复：把 scene_truth_snapshot 写回 pkg（= scene_packages[scene_idx] 同引用），
                # 供 _scene_truth_snapshot_map() 在 L2/L4 窗口修复 protection checker 读取。
                # 缺失会导致 protection checker fail-closed，所有候选被拒。
                _wr_truth_snapshot = result.get("scene_truth_snapshot")
                if _wr_truth_snapshot:
                    pkg["scene_truth_snapshot"] = _wr_truth_snapshot

            async def handle_scene_quality(node, context) -> None:
                nonlocal total_seeds, previous_scene_ending, previous_scenes_summary
                scene_idx = int(node.scene_index or 0)
                data = scene_exec.get(scene_idx, {})
                pkg = data.get("pkg") or scene_packages[scene_idx]
                scene_contract = data.get("scene_contract")
                result = data.get("result", {})
                generated_text = data.get("generated_text", "")
                error_code = data.get("error_code", "")
                commit_blocked = data.get("commit_blocked", False)
                recovery_mode = data.get("recovery_mode", "none")
                attempts = data.get("attempts", [])
                scene_recovery_status = data.get("scene_recovery_status", "")
                underlying_error = data.get("underlying_error", "")
                pipeline_output = data.get("pipeline_output", {})
                final_violations = data.get("final_violations", [])

                gen_step = f"core_generation_{scene_idx + 1}"
                check_step = f"consistency_check_{scene_idx + 1}"
                harvest_step = f"detail_harvest_{scene_idx + 1}"
                update_step = f"state_update_{scene_idx + 1}"

                _ERROR_MSG_MAP = {
                    "core_generation_circuit_open": "Core generation circuit breaker open, please wait 60s and retry",
                    "core_generation_llm_error": f"Core generation call failed: {underlying_error or 'unknown exception'}",
                    "core_generation_empty_response": "Model returned empty text",
                    "core_generation_context_invalid": f"Writer context type exception: {result.get('invalid_field', '') or underlying_error or 'unknown field'}",
                    "recovery_exhausted": "Scene recovery budget exhausted, Critic violation cannot be auto-fixed",
                    "recovery_non_repairable": "Scene has unrepairable violation (contract damaged or needs human review)",
                    "recovery_timeout": "Scene recovery timeout",
                    "recovery_failed": "Scene recovery failed",
                    "recovery_pending_validation": "Validator temporarily unavailable, text pending validation",
                    "recovery_contract_blocked": "Contract unrepairable, cannot generate",
                    "pending_validation": "Validator temporarily unavailable, text pending validation",
                    "needs_review": "Recovery budget exhausted, needs human review",
                    "pending_validator_retry": "Validator temporarily unavailable, system will auto re-verify later",
                    "waiting_human_content_review": "Recovery budget exhausted, needs human text revision",
                    "waiting_human_system_review": "Validator continuous failure, needs human confirmation of system state",
                    "commit_blocked": "Scene validation not passed, commit blocked",
                }

                if commit_blocked or error_code:
                    error_detail = _ERROR_MSG_MAP.get(error_code, "Scene validation not passed, chapter terminated")
                    if error_code in ("recovery_exhausted", "recovery_non_repairable"):
                        blocking_violations = [v for v in final_violations if v.get("blocks_commit")]
                        if blocking_violations:
                            worst = blocking_violations[0]
                            error_detail += f" (last violation: {worst.get('type', 'unknown')} - {worst.get('detail', '')})"

                    if scene_recovery_status == "pending_validator_retry":
                        approved_scene_texts = {
                            str(index): text
                            for index, text in enumerate(all_content)
                            if text
                        }
                        validator_retry_info = {
                            "scene_index": scene_idx,
                            "candidate_text": generated_text,
                            "pipeline_output": pipeline_output,
                            "error_code": error_code,
                            "error_detail": error_detail,
                            "scene_contract": scene_contract,
                            "chapter_state": chapter_state,
                            "character_cards": pkg.get("character_cards", []),
                            "character_names": pkg.get("character_names", []),
                            "scene_truth_snapshot": pkg.get("scene_truth_snapshot"),
                            "core_facts": pkg.get("core_facts", {}),
                            "word_budget": pkg.get("word_budget"),
                            "scene_provenance": scene_contract.get("scene_provenance", {}) if scene_contract else {},
                            "validator_retry_delays": result.get("validator_retry", {}).get("next_retry_delays", [5, 15, 45]),
                            "validator_retry_immediate_attempts": result.get("validator_retry", {}).get("immediate_attempts", 0),
                            "validator_retry_delayed_attempts": 0,
                        }
                        resume_state = {
                            "project_id": project_id,
                            "chapter_number": chapter_number,
                            "pov_character": pov_character,
                            "custom_instructions": custom_instructions,
                            "scene_index": scene_idx,
                            "candidate_text": generated_text,
                            "approved_scene_texts": approved_scene_texts,
                            "review_version": review_version,
                            "scene_contract": scene_contract,
                            "chapter_state": chapter_state,
                            "character_cards": pkg.get("character_cards", []),
                            "character_names": pkg.get("character_names", []),
                            "scene_truth_snapshot": pkg.get("scene_truth_snapshot"),
                            "core_facts": pkg.get("core_facts", {}),
                            "word_budget": pkg.get("word_budget"),
                        }
                        persisted = _json_snapshot({
                            "validator_retry": validator_retry_info,
                            "resume_state": resume_state,
                        })
                        await update_step_locked(
                            execution_id, gen_step, "pending_validator_retry",
                            output=pipeline_output, error=error_detail, db=db,
                        )
                        await update_step_locked(
                            execution_id, check_step, "pending_validator_retry",
                            output=pipeline_output, error=error_detail, db=db,
                        )
                        for skip_step in [harvest_step, update_step]:
                            await update_step_locked(execution_id, skip_step, "skipped", db=db)
                        await complete_workflow_locked(
                            execution_id, "pending_validator_retry",
                            result=persisted, error=error_detail, db=db,
                        )
                        _schedule_delayed_validator_retries(
                            execution_id=execution_id,
                            project_id=project_id,
                            delays=validator_retry_info["validator_retry_delays"],
                        )
                        raise EditorDAGAbort({"content": "", "status": "pending_validator_retry", "error": error_detail})

                    if generated_text:
                        approved_scene_texts = {
                            str(index): text
                            for index, text in enumerate(all_content)
                            if text
                        }
                        resume_state = {
                            "project_id": project_id,
                            "chapter_number": chapter_number,
                            "pov_character": pov_character,
                            "custom_instructions": custom_instructions,
                            "scene_index": scene_idx,
                            "candidate_text": generated_text,
                            "approved_scene_texts": approved_scene_texts,
                            "review_version": review_version,
                            "scene_contract": scene_contract,
                            "chapter_state": chapter_state,
                            "character_cards": pkg.get("character_cards", []),
                            "character_names": pkg.get("character_names", []),
                            "scene_truth_snapshot": pkg.get("scene_truth_snapshot"),
                            "core_facts": pkg.get("core_facts", {}),
                            "word_budget": pkg.get("word_budget"),
                        }
                        await pause_for_human_review_locked(
                            execution_id=execution_id,
                            gen_step=gen_step,
                            check_step=check_step,
                            error_detail=error_detail,
                            pipeline_output=pipeline_output,
                            resume_state=resume_state,
                            db=db,
                            review_type="content_review",
                        )
                        raise EditorDAGAbort({"content": "", "status": "waiting_review", "error": error_detail})
                    if not generated_text:
                        pipeline_output["word_count"] = 0
                        await update_step_locked(execution_id, gen_step, "failed",
                                                    error=error_detail, output=pipeline_output, db=db)
                    else:
                        await update_step_locked(execution_id, gen_step, "completed",
                                                    output={"word_count": count_words(generated_text)}, db=db)
                    await update_step_locked(execution_id, check_step, "failed",
                                                error=error_detail, output=pipeline_output, db=db)
                    for skip_step in [harvest_step, update_step]:
                        await update_step_locked(execution_id, skip_step, "skipped", db=db)
                    await skip_future_steps(scene_idx)
                    await complete_workflow_locked(execution_id, "failed", error=error_detail, db=db)
                    try:
                        if baseline and baseline.baseline_story_state:
                            await persist_story_state(StoryState(**baseline.baseline_story_state))
                    except Exception:
                        pass
                    all_content.append("")
                    raise EditorDAGAbort({"content": "", "status": "failed", "error": error_detail})

                if not generated_text:
                    await update_step_locked(execution_id, gen_step, "failed", error="Generated content is empty", db=db)
                    for skip_step in [harvest_step, update_step, check_step]:
                        await update_step_locked(execution_id, skip_step, "skipped", db=db)
                    await skip_future_steps(scene_idx)
                    await complete_workflow_locked(execution_id, "failed", error="Generated content is empty, chapter terminated", db=db)
                    try:
                        if baseline and baseline.baseline_story_state:
                            await persist_story_state(StoryState(**baseline.baseline_story_state))
                    except Exception:
                        pass
                    all_content.append("")
                    raise EditorDAGAbort({"content": "", "status": "failed", "error": "Generated content is empty, chapter terminated"})

                await update_step_locked(execution_id, gen_step, "completed",
                                            output={
                                                "word_count": count_words(generated_text),
                                                "source": "chapter_writer_aligned" if use_chapter_writer else "scene_writer",
                                                "context_ledger_id": chapter_context_ledger.get("ledger_id", "") if use_chapter_writer else "",
                                                "runtime": "editor_generation_dag",
                                            }, db=db)

                consistency_reports.append(result["consistency_report"])
                await update_step_locked(execution_id, check_step, "completed",
                                            output={
                                                "pass": result["consistency_report"].get("pass", True),
                                                "recovery_mode": recovery_mode,
                                                "attempts": attempts,
                                                "scene_recovery_status": scene_recovery_status,
                                                "error_code": error_code,
                                                "runtime": "editor_generation_dag",
                                            }, db=db)

            async def handle_scene_post_extract(node, context) -> None:
                nonlocal total_seeds
                scene_idx = int(node.scene_index or 0)
                data = scene_exec.get(scene_idx, {})
                result = data.get("result", {})
                generated_text = data.get("generated_text", "")
                harvest_step = f"detail_harvest_{scene_idx + 1}"

                if not generated_text or data.get("commit_blocked") or data.get("error_code"):
                    return

                detail_seeds_count = result.get("detail_seeds_count", 0)
                total_seeds += detail_seeds_count
                pending_scene_effect = {
                    "scene_index": scene_idx,
                    "generated_text": generated_text,
                    "effects": result.get("pending_effects", {}),
                }
                pending_scene_effects.append(pending_scene_effect)
                await update_step_locked(
                    execution_id,
                    harvest_step,
                    "completed",
                    output={
                        "seeds": detail_seeds_count,
                        "runtime": "editor_generation_dag",
                        "pending_scene_effect": pending_scene_effect,
                    },
                    db=db,
                )
                return {"pending_scene_effect": pending_scene_effect}

            async def handle_scene_accept(node, context) -> None:
                scene_idx = int(node.scene_index or 0)
                data = scene_exec.get(scene_idx, {})
                generated_text = data.get("generated_text", "")
                if generated_text:
                    all_content.append(generated_text)

            async def handle_scene_state_commit(node, context) -> None:
                nonlocal previous_scene_ending, previous_scenes_summary
                scene_idx = int(node.scene_index or 0)
                data = scene_exec.get(scene_idx, {})
                pkg = data.get("pkg") or scene_packages[scene_idx]
                result = data.get("result", {})
                generated_text = data.get("generated_text", "")
                scene_contract = data.get("scene_contract")
                update_step = f"state_update_{scene_idx + 1}"

                if not generated_text:
                    await update_step_locked(execution_id, update_step, "skipped", db=db)
                    return

                if result["state_patch"]:
                    all_patches.append(result["state_patch"])

                scene_facts, _fact_source = await _prepare_scene_facts_for_commit(
                    chapter_number=chapter_number,
                    generated_text=generated_text,
                    result=result,
                    character_cards=pkg.get("character_cards", []),
                    scene_contract=scene_contract,
                )
                result["writer_scene_facts"] = scene_facts
                from app.services.chapter_output_effects import merge_scene_facts_into_chapter_state

                merge_scene_facts_into_chapter_state(
                    chapter_state,
                    scene_facts,
                    generated_text=generated_text,
                    character_cards=pkg.get("character_cards", []),
                    scene_contract=scene_contract,
                )

                scene_ending = scene_facts.get("scene_ending", "")
                if scene_ending:
                    previous_scene_ending = scene_ending

                if generated_text:
                        summary_text = generated_text[:800] if len(generated_text) > 800 else generated_text
                        previous_scenes_summary = f"Scene {scene_idx + 1} summary: {summary_text}\n\n" + previous_scenes_summary
                        if len(previous_scenes_summary) > 2000:
                            previous_scenes_summary = previous_scenes_summary[:2000]

                await update_step_locked(
                    execution_id,
                    update_step,
                    "completed" if _fact_source == "writer_scene_facts" else "degraded",
                    output={
                        "runtime": "editor_generation_dag",
                        "fact_source": _fact_source,
                    },
                    db=db,
                )

            editor_scene_dag = EditorGenerationDAGBuilder().build(
                scene_count=len(scene_packages),
                mode="parallel_review",
            )
            # 方案16 步骤1：接入进度广播回调，将 DAG 层级事件转发到 _workflow_events
            def _editor_dag_progress_cb(event: dict) -> None:
                _broadcast_workflow_event(execution_id, {
                    "type": event.get("type", "dag_progress"),
                    "phase": "editor_dag",
                    "layer_index": event.get("layer_index"),
                    "total_layers": event.get("total_layers"),
                    "layer_nodes": event.get("layer_nodes"),
                    "executed": event.get("executed"),
                    "skipped": event.get("skipped"),
                    "failed_nodes": event.get("failed_nodes"),
                    "dag_execution_id": event.get("execution_id"),
                })
            editor_scene_runtime = EditorGenerationDAGRuntime(
                editor_scene_dag,
                project_id=project_id,
                chapter_number=chapter_number,
                progress_callback=_editor_dag_progress_cb,
                execution_id=execution_id,
            )

            # Populate scene_exec from chapter_writer_scene_texts for parallel_review mode.
            # In safe mode, scene_exec is filled by handle_scene_prepare/handle_scene_generate,
            # but parallel_review skips those nodes — chapter_writer already generated all text.
            for _si in range(len(scene_packages)):
                _pkg = scene_packages[_si]
                _scene_contract = None
                if scene_contracts and _si < len(scene_contracts):
                    _scene_contract = scene_contracts[_si]
                # 已批准场景使用 overrides；retry 目标场景使用本轮
                # ChapterWriter 新生成的候选，避免将旧 candidate 重新复审。
                _gen_text = chapter_writer_scene_texts.get(_si, "")
                if reviewed_scene_overrides and _si in reviewed_scene_overrides:
                    _gen_text = reviewed_scene_overrides[_si]
                scene_exec[_si] = {
                    "pkg": _pkg,
                    "scene_contract": _scene_contract,
                    "generated_text": _gen_text,
                    "result": fallback_scene_results.get(_si, {}),
                }

            # --- parallel_review mode: new handlers ---
            from app.services.chapter_parallel_review import SceneReviewWorker
            from app.services.fbi.chapter_case_intake import FBIChapterCaseIntakeService, FBIChapterRepairPlanner
            from app.services.fbi.chapter_repair_executor import ChapterRepairExecutor
            from app.models.chapter_review import (
                SceneReviewPacket, ChapterReviewCase, ChapterRepairPlan, ChapterRepairOrder,
            )

            # Accumulate SceneReviewPackets from parallel review
            review_packets: list[SceneReviewPacket] = [None] * len(scene_packages)
            initial_review_packets: list[SceneReviewPacket] = [None] * len(scene_packages)
            # Token 优化：保存 L1 review 时的原始文本，L3 recheck 时若文本未变可复用 L1 packet，
            # 跳过完整 QualityGate（省 6-9 次 LLM 调用）。适用于 L2 修复失败/回滚但需 recheck 的场景。
            initial_review_texts: dict[int, str] = {}
            # Track which scenes have been repaired
            repaired_scene_texts: dict[int, str] = {}
            # 方案14 Part C：共享可变状态加锁，保护 read-modify-write 操作
            scene_state_lock = asyncio.Lock()
            # Scenes can require recheck even when a rejected repair preserved the text.
            scenes_requiring_recheck: set[int] = set()
            repair_failed_deltas: dict[int, list[dict]] = {}
            # Track the current repair plan
            current_repair_plan: ChapterRepairPlan | None = None
            current_review_case_file: dict | None = None
            chapter_review_outputs: dict[str, dict] = {}
            chapter_skill_review_task: asyncio.Task | None = None
            # Track repair round
            repair_round = 0
            # 修复（循环 #17 D17-6）：L4 修复轮次从 2 降到 1。
            # 原值 2：L3 已做 MAX_REPAIR_ROUNDS=2 轮内部修复循环，L4 再做 2 轮，
            #   总共 4 轮修复，每轮 ~5min，共 ~20min。但 L3 失败的问题 L4 用相同策略
            #   通常也会失败（如 ai_simile_overuse 密度类问题，LLM 无法系统性降密度）。
            # 新值 1：L4 只做 1 轮兜底修复。如果 L3 的 2 轮 + L4 的 1 轮 = 3 轮都失败，
            #   说明问题超出自动修复能力，应进入 waiting_review 触发人工介入或新循环迭代。
            # 大局观评估（铁律 12 五问）：
            #   1. 能解决目标问题吗？是——减少无效重试，省 ~5min
            #   2. 会引入新问题吗？否——L3 已有 2 轮修复，L4 的 1 轮是兜底，
            #      终验仍有 2 轮（D17-4 条件性跳过 Round 2 后实际 1 轮）
            #   3. 耗时成本？降低——L4 从 ~10min 降到 ~5min
            #   4. 更低成本的替代方案？否——L4 完全跳过会丢失兜底修复机会
            #   5. 对下一轮修复的影响？正面——更快进入 waiting_review
            MAX_CHAPTER_REPAIR_ROUNDS = 1
            # A recheck can reveal a different hard issue only after the first
            # repair changes the text. Give that newly discovered issue one
            # bounded attempt without retrying an unchanged failure forever.
            MAX_CHAPTER_REPAIR_ROUNDS_WITH_NOVEL_DELTA = 2
            MAX_FINAL_ACCEPTANCE_REPAIR_ROUNDS = 2

            def _current_scene_texts() -> dict[int, str]:
                texts: dict[int, str] = {}
                for index in range(len(scene_packages)):
                    texts[index] = repaired_scene_texts.get(
                        index,
                        scene_exec.get(index, {}).get("generated_text", ""),
                    )
                return texts

            def _scene_contract_map() -> dict[int, dict]:
                contracts: dict[int, dict] = {}
                for index in range(len(scene_packages)):
                    contract = scene_exec.get(index, {}).get("scene_contract")
                    if contract is None and scene_contracts and index < len(scene_contracts):
                        contract = scene_contracts[index]
                    contracts[index] = contract or {}
                return contracts

            def _scene_truth_snapshot_map() -> dict[int, dict]:
                snapshots: dict[int, dict] = {}
                for index in range(len(scene_packages)):
                    pkg = scene_exec.get(index, {}).get("pkg") or scene_packages[index]
                    snapshot = pkg.get("scene_truth_snapshot") or {}
                    if snapshot:
                        snapshots[index] = snapshot
                        continue
                    # P0-1 修复：chapter_writer + parallel_review 模式跳过 run_scene_pipeline，
                    # 导致 scene_truth_snapshot 未构建。在此实时构建，避免 protection checker fail-closed。
                    try:
                        from app.services.scene_truth_snapshot import build_scene_truth_snapshot
                        contract = scene_exec.get(index, {}).get("scene_contract")
                        if contract is None and scene_contracts and index < len(scene_contracts):
                            contract = scene_contracts[index]
                        built = build_scene_truth_snapshot(
                            story_state=story_state.model_dump() if hasattr(story_state, "model_dump") else {},
                            chapter_state=pkg.get("chapter_state", {}),
                            scene_contract=contract or {},
                            previous_scene_ending=pkg.get("previous_scene_ending", ""),
                            scene_id=f"ch{chapter_number}s{index}",
                        )
                        if built:
                            pkg["scene_truth_snapshot"] = built
                            snapshots[index] = built
                    except Exception as _sts_exc:
                        logger.warning(
                            "scene_truth_snapshot build failed for scene=%d: %s",
                            index, _sts_exc,
                        )
                return snapshots

            def _chapter_scene_context(scene_texts: dict[int, str]) -> str:
                blocks: list[str] = []
                for index in range(len(scene_packages)):
                    text = scene_texts.get(index, "")
                    if not text:
                        continue
                    blocks.append(f"--- Scene {index + 1} ---\n{text}")
                return "\n\n".join(blocks)

            def _repair_owner(order: ChapterRepairOrder) -> int | None:
                if order.owner_scene is not None:
                    return order.owner_scene
                if order.target_scenes:
                    return order.target_scenes[0]
                return None

            def _repair_affected_scenes(order: ChapterRepairOrder) -> set[int]:
                scenes = {
                    scene
                    for scene in (getattr(order, "target_scenes", []) or [])
                    if isinstance(scene, int)
                }
                owner = _repair_owner(order)
                if owner is not None:
                    scenes.add(owner)
                if (
                    not scenes
                    and getattr(order, "repair_type", "") in {"contract_patch", "manual_review"}
                    and len(scene_packages) > 0
                ):
                    scenes.add(0)
                return {scene for scene in scenes if 0 <= scene < len(scene_packages)}

            def _violation_sample(violations: list[dict], limit: int = 5) -> list[dict]:
                samples: list[dict] = []
                for violation in violations[:limit]:
                    if not isinstance(violation, dict):
                        continue
                    samples.append({
                        "type": violation.get("violation_type") or violation.get("type"),
                        "severity": violation.get("severity"),
                        "detail": violation.get("detail") or violation.get("reason"),
                        "source_scene": violation.get("source_scene"),
                        "source_scenes": violation.get("source_scenes"),
                        "target_span": violation.get("target_span"),
                        "repairability": violation.get("repairability"),
                    })
                return samples

            def _repair_order_sample(orders: list, limit: int = 8) -> list[dict]:
                samples: list[dict] = []
                for order in orders[:limit]:
                    audit = getattr(order, "repair_audit", {}) or {}
                    # 提取蓝图失败原因到顶层（9.1.4 增强）
                    blueprint_failure_stage = audit.get("blueprint_failure_stage", "legacy")
                    route_failure_reason = audit.get("route_failure_reason", "")
                    route_metric = audit.get("route_metric", "")
                    route_family = audit.get("route_family", "")
                    # 如果 audit 中没有，尝试从 repair_brief 提取
                    if not route_failure_reason:
                        brief = getattr(order, "repair_brief", {}) or {}
                        revision_bp = brief.get("revision_blueprint") if isinstance(brief, dict) else None
                        if isinstance(revision_bp, dict):
                            rejection = str(revision_bp.get("rejection_reason") or "")
                            route_failure_reason = rejection
                            route_family = route_family or str(revision_bp.get("repair_family") or "")
                            if "route_builder_failed" in rejection:
                                blueprint_failure_stage = "route_builder"
                            elif rejection:
                                blueprint_failure_stage = "protocol_gate"
                            tool_bp = revision_bp.get("tool_blueprint") or {}
                            if isinstance(tool_bp, dict) and not route_metric:
                                route_metric = str(tool_bp.get("metric") or "")
                    samples.append({
                        "order_id": getattr(order, "order_id", ""),
                        "repair_type": getattr(order, "repair_type", ""),
                        "priority": getattr(order, "priority", ""),
                        "owner_scene": getattr(order, "owner_scene", None),
                        "target_scenes": getattr(order, "target_scenes", []),
                        "reason": getattr(order, "reason", ""),
                        "status": getattr(order, "status", ""),
                        "result_text_hash": getattr(order, "result_text_hash", ""),
                        "repair_audit": audit,
                        "failure_disposition": _repair_order_failure_disposition(order),
                        "blueprint_failure_stage": blueprint_failure_stage,
                        "route_failure_reason": route_failure_reason,
                        "route_metric": route_metric,
                        "route_family": route_family,
                    })
                return samples

            def _repair_type_counts(orders: list) -> dict[str, int]:
                counts: dict[str, int] = {}
                for order in orders:
                    repair_type = str(getattr(order, "repair_type", "") or "unknown")
                    counts[repair_type] = counts.get(repair_type, 0) + 1
                return counts

            def _repair_failure_delta_items(
                order,
                disposition: str,
                current_scene_texts: dict[int, str] | None = None,
            ) -> list[dict]:
                audit = getattr(order, "repair_audit", {}) or {}
                failures = list(audit.get("failures") or [])
                tool_failures = list(audit.get("tool_failures") or [])
                latest_tool_failure = tool_failures[-1] if tool_failures else {}
                failure_evidence = (
                    latest_tool_failure.get("failure_evidence") or {}
                    if isinstance(latest_tool_failure, dict)
                    else {}
                )
                rollback_scope = (
                    latest_tool_failure.get("rollback_scope") or {}
                    if isinstance(latest_tool_failure, dict)
                    else {}
                )
                failed_spans = list(failure_evidence.get("failed_spans") or [])
                metric_evidence: list[dict] = []
                for postcheck in failure_evidence.get("postcheck") or []:
                    if not isinstance(postcheck, dict):
                        continue
                    for key in (
                        "target_self_check",
                        "dash_density_check",
                        "target_span_checks",
                    ):
                        value = postcheck.get(key)
                        if isinstance(value, dict):
                            metric_evidence.append({"source": key, **value})
                        elif isinstance(value, list):
                            metric_evidence.extend(
                                {"source": key, **entry}
                                for entry in value
                                if isinstance(entry, dict)
                            )
                failure_scope = _repair_order_failure_scope(order)
                details = [
                    normalize_violation_semantics(item)
                    for item in failure_scope["violation_details"]
                    if isinstance(item, dict)
                ]
                if not details:
                    details = [{
                        "type": getattr(order, "repair_type", "repair_failed"),
                        "severity": getattr(order, "priority", "high"),
                        "detail": getattr(order, "reason", "") or "Repair candidate was rejected by audit.",
                        "target_span": getattr(order, "instruction", ""),
                        "blocks_commit": True,
                    }]
                # 通用修复：stale target_span 清除
                # 根因：FBI 修复失败后，_repair_failure_delta_items 把原始 violation_details
                #   传递给人工审核。但 violation_details 中的 target_span 指向的是修复前的
                #   旧文本。如果 FBI 修复虽然 audit 失败但实际已修改了 target_span 对应的
                #   文本片段，那么 target_span 在当前正文中已不存在——这就是过期误报。
                #   修复：用当前场景正文校验 target_span，若不存在则标记 is_stale=True
                #   并降级为非 blocking（advisory），避免人工审核被不存在的文本片段阻塞。
                # 通用性：所有题材的 FBI 修复失败场景都适用——target_span 不在当前正文
                #   中意味着该违规引用的文本已被修改，原违规已不适用。
                order_scene = getattr(order, "owner_scene", None)
                if order_scene is None:
                    target_scenes = getattr(order, "target_scenes", None) or []
                    order_scene = target_scenes[0] if target_scenes else None
                current_text = ""
                if order_scene is not None:
                    text_source = (
                        current_scene_texts
                        if current_scene_texts is not None
                        else _current_scene_texts()
                    )
                    current_text = text_source.get(order_scene, "")
                items: list[dict] = []
                for detail in details[:5]:
                    item = {
                        **detail,
                        "repair_failure": True,
                        "repair_order_id": getattr(order, "order_id", ""),
                        "repair_type": getattr(order, "repair_type", ""),
                        "repair_failure_disposition": disposition,
                        "repair_audit_failures": failures,
                        "removed_protected_terms": audit.get("removed_protected_terms", []),
                        "failed_work_unit_id": latest_tool_failure.get("work_unit_id", "")
                        if isinstance(latest_tool_failure, dict) else "",
                        "repair_failure_reason": latest_tool_failure.get("reason", "")
                        if isinstance(latest_tool_failure, dict) else "",
                        "repair_failure_evidence": failure_evidence,
                        "repair_metric_evidence": metric_evidence,
                        "rollback_scope": rollback_scope,
                        "failed_goal_ids": failure_scope["failed_goal_ids"],
                        "failed_issue_ids": failure_scope["failed_issue_ids"],
                        "failure_scope_applied": failure_scope["scoped"],
                        "current_text_hash": hashlib.sha256(
                            current_text.encode("utf-8")
                        ).hexdigest() if current_text else "",
                    }
                    exact_failed_span = next(
                        (
                            span
                            for span in failed_spans
                            if isinstance(span, dict)
                            and str(span.get("before_text") or "").strip()
                            and str(span.get("before_text") or "").strip() in current_text
                        ),
                        None,
                    )
                    if exact_failed_span:
                        item["target_span"] = str(
                            exact_failed_span.get("before_text") or ""
                        ).strip()
                        item["failed_span"] = exact_failed_span
                        item["previous_patch"] = str(
                            exact_failed_span.get("after_text") or ""
                        )
                    target_span = str(item.get("target_span") or "").strip()
                    if (
                        target_span
                        and current_text
                        and target_span not in current_text
                    ):
                        # 循环 #5 修复（L4 stale span 引用问题）：
                        # target_span 引用的是旧文本（L0/L2），但 current_text 是 L4-repaired 文本。
                        # 先尝试用 enrich_review_issue_locations 基于 current_text 重定位 target_span，
                        # 重定位成功则更新 span 并保持 blocking；失败才降级为 stale。
                        # 通用性：复用已有的基于 issue_type 的智能定位逻辑，不针对任何具体 metric。
                        _relocalized_span = ""
                        try:
                            from app.services.review_issue_localizer import enrich_review_issue_locations
                            _relocalized = enrich_review_issue_locations(
                                # 清除 evidence_spans 以绕过早返回条件，强制重定位
                                {**detail, "evidence_spans": None},
                                current_text,
                            )
                            _relocalized_span = str(_relocalized.get("target_span") or "").strip()
                        except Exception:
                            _relocalized_span = ""
                        if (
                            _relocalized_span
                            and _relocalized_span in current_text
                            and _relocalized_span != target_span
                        ):
                            item["target_span"] = _relocalized_span
                            item["relocalized"] = True
                            item["relocalized_from"] = target_span
                        else:
                            item["is_stale"] = True
                            item["blocks_commit"] = False
                            item["stale_reason"] = (
                                "target_span not found in current text — "
                                "the referenced text was likely modified during repair"
                            )
                    items.append(item)
                return items

            def _inject_repair_failure_memory(
                violations: list[dict],
                scene_index: int,
            ) -> None:
                """Attach the latest exact patch failure to fresh recheck findings."""

                failed_by_type: dict[str, list[dict]] = {}
                for failed in repair_failed_deltas.get(scene_index, []):
                    if not isinstance(failed, dict):
                        continue
                    failure_type = str(
                        failed.get("type")
                        or failed.get("violation_type")
                        or failed.get("metric")
                        or ""
                    ).lower()
                    if failure_type:
                        failed_by_type.setdefault(failure_type, []).append(failed)
                for violation in violations:
                    if not isinstance(violation, dict):
                        continue
                    violation_type = str(
                        violation.get("type")
                        or violation.get("violation_type")
                        or violation.get("metric")
                        or ""
                    ).lower()
                    matches = failed_by_type.get(violation_type) or []
                    if not matches:
                        continue
                    latest = matches[-1]
                    violation["repair_failure"] = True
                    # Keep the existing lane-escalation signal: the workflow
                    # can retry this order while the planner still increases
                    # repair strength after a rejected patch.
                    violation["repair_failure_disposition"] = "hard_failed"
                    for key in (
                        "repair_order_id",
                        "failed_work_unit_id",
                        "repair_failure_reason",
                        "repair_failure_evidence",
                        "repair_metric_evidence",
                        "rollback_scope",
                        "failed_span",
                        "previous_patch",
                        "current_text_hash",
                    ):
                        if latest.get(key) not in (None, "", [], {}):
                            violation[key] = latest[key]

            async def handle_parallel_scene_review(node, context) -> None:
                """并行审校：对单个场景执行 QualityGate + 状态抽取 + 细节抽取."""
                scene_idx = int(node.scene_index or 0)
                review_step = f"parallel_review_{scene_idx + 1}"
                # Each parallel node owns its own short-lived session: the DAG
                # runtime fans nodes out via asyncio.gather, and sharing the
                # request-scoped `db` here would hit one AsyncSession
                # concurrently and raise "concurrent operations are not
                # permitted". Workers receive the *factory* (not a live
                # session) so even their internal gathers stay isolated.
                async with async_session() as node_db:
                    await update_step_locked(execution_id, review_step, "running", db=node_db)

                    try:
                        pkg = scene_exec.get(scene_idx, {}).get("pkg") or scene_packages[scene_idx]
                        scene_contract = scene_exec.get(scene_idx, {}).get("scene_contract")
                        result = scene_exec.get(scene_idx, {}).get("result", {})
                        generated_text = scene_exec.get(scene_idx, {}).get("generated_text", "")

                        if not generated_text:
                            packet = SceneReviewPacket(
                                scene_index=scene_idx,
                                unavailable=True,
                                unavailable_reason="no generated text",
                            )
                            review_packets[scene_idx] = packet
                            await update_step_locked(execution_id, review_step, "completed",
                                                      output={"repairability": "clean", "unavailable": True}, db=node_db)
                            return

                        if _skip_locked_scene_validation(
                            scene_idx,
                            reviewed_scene_overrides,
                            skip_reviewed_scene_validation,
                            reviewed_scene_repair_indexes,
                        ):
                            # A resumed candidate reaches this branch only after
                            # the full quality gate (or explicit human acceptance)
                            # has approved the exact text hash.  Re-running the
                            # parallel review here can turn an unrelated,
                            # transient validator outage in another scene into an
                            # endless A/B retry loop.  Reuse the approved result
                            # while keeping the remaining DAG, commit gates and
                            # final chapter write intact.
                            packet = SceneReviewPacket(
                                scene_index=scene_idx,
                                candidate_text=generated_text,
                                blocking_violations=[],
                                advisory_violations=[],
                                repairability="clean",
                            )
                            review_packets[scene_idx] = packet
                            initial_review_packets[scene_idx] = packet.model_copy(deep=True)
                            initial_review_texts[scene_idx] = generated_text
                            await update_step_locked(
                                execution_id,
                                review_step,
                                "completed",
                                output={
                                    "repairability": "clean",
                                    "blocking_violations": 0,
                                    "advisory_violations": 0,
                                    "validation_reused": True,
                                    "runtime": "parallel_review",
                                },
                                db=node_db,
                            )
                            return

                        # Use SceneReviewWorker to produce a SceneReviewPacket
                        worker = SceneReviewWorker(scene_index=scene_idx)
                        context = {
                            "generated_text": generated_text,
                            "scene_contract": scene_contract or {},
                            "current_state": chapter_state,
                            "core_facts": pkg.get("core_facts", {}),
                            "character_names": pkg.get("character_names", []),
                            "character_cards": pkg.get("character_cards", []),
                            "project_id": project_id,
                            "chapter_number": chapter_number,
                            "scene_index": scene_idx,
                            "project": project,
                            "db_session_factory": async_session,
                            "precomputed_review_extraction": result.get("review_extraction") or {},
                        }
                        packet = await asyncio.wait_for(
                            worker.review(context),
                            timeout=_RECHECK_REVIEW_TIMEOUT_SECONDS,
                        )
                        review_packets[scene_idx] = packet
                        initial_review_packets[scene_idx] = packet.model_copy(deep=True)
                        # Token 优化：保存 L1 review 时的原始文本，供 L3 recheck 对比
                        initial_review_texts[scene_idx] = str(context.get("generated_text") or "")

                        await update_step_locked(execution_id, review_step, "completed",
                                                  output={
                                                      "repairability": packet.repairability,
                                                      "blocking_violations": len(packet.blocking_violations),
                                                      "advisory_violations": len(packet.advisory_violations),
                                                      "quality_policy_enforcement": _quality_policy_enforcement_summary(packet),
                                                      "runtime": "parallel_review",
                                                  }, db=node_db)
                    except EditorDAGAbort:
                        # 方案6 B3：DAG 中止异常不吞掉，直接向上抛
                        raise
                    except Exception as e:
                        # 方案6 B3：异常分类——区分可重试异常和不可重试异常。
                        # 可重试异常（LLM 超时、DB 临时错误）：标记 retrying，不假装 completed。
                        # 不可重试异常（contract 错误、编程错误）：标记 failed，抛 EditorDAGAbort 走 waiting_review。
                        logger.warning("parallel_scene_review %d failed: %s", scene_idx, e)
                        error_class_name = type(e).__name__
                        is_retryable = isinstance(e, (asyncio.TimeoutError, TimeoutError)) or _is_transient_review_error(e)
                        packet = SceneReviewPacket(
                            scene_index=scene_idx,
                            unavailable=True,
                            unavailable_reason=str(e),
                        )
                        review_packets[scene_idx] = packet
                        if is_retryable:
                            # 可重试：标记 retrying，让上层（方案15/DAG runtime）决定是否重试
                            await update_step_locked(
                                execution_id, review_step, "retrying",
                                output={
                                    "repairability": "unavailable",
                                    "unavailable": True,
                                    "error": str(e),
                                    "error_class": error_class_name,
                                    "retryable": True,
                                },
                                db=node_db,
                            )
                            error_detail = (
                                f"scene {scene_idx + 1} initial review unavailable: "
                                f"{error_class_name or 'transient_review_error'}"
                            )
                            validator_violation = {
                                "type": "parallel_review_unavailable",
                                "severity": "high",
                                "detail": error_detail,
                                "source": "parallel_review",
                                "blocks_commit": True,
                                "suggested_strategy": "validator_retry",
                                "issue_classification": "validator_system_error",
                                "is_system_issue": True,
                                "repair_scope": "system",
                            }
                            await _pause_parallel_scene_for_validator_retry(
                                scene_idx=scene_idx,
                                gen_step=review_step,
                                check_step=review_step,
                                error_detail=error_detail,
                                pipeline_output={
                                    "runtime": "parallel_review",
                                    "error_code": "parallel_review_unavailable",
                                    "final_violations": [validator_violation],
                                    "candidate_text": generated_text,
                                },
                                violations=[validator_violation],
                                candidate_text=generated_text,
                                db_session=node_db,
                            )
                            raise EditorDAGAbort({
                                "content": "",
                                "status": "pending_validator_retry",
                                "error": error_detail,
                            })
                        else:
                            # 不可重试：标记 failed，并抛 EditorDAGAbort 让 DAG 走 waiting_review
                            await update_step_locked(
                                execution_id, review_step, "failed",
                                output={
                                    "repairability": "unavailable",
                                    "unavailable": True,
                                    "error": str(e),
                                    "error_class": error_class_name,
                                    "retryable": False,
                                },
                                db=node_db,
                            )
                            raise EditorDAGAbort({
                                "content": "",
                                "status": "waiting_review",
                                "reason": "unrecoverable_review_error",
                                "error": f"{error_class_name}: {e}",
                                "scene_index": scene_idx,
                            })

            def _chapter_review_failure_filter(node_type: str, failures: list[dict]) -> list[dict]:
                filtered: list[dict] = []
                for failure in failures:
                    if not isinstance(failure, dict):
                        continue
                    validator = str(failure.get("validator") or "").lower()
                    metric = str(failure.get("metric") or failure.get("type") or "").lower()
                    skill_id = str(failure.get("skill_id") or failure.get("skill") or "").lower()
                    joined = f"{validator} {metric} {skill_id}"
                    is_anti_ai = (
                        "ai_flavor" in joined
                        or "anti_ai" in joined
                        or any(token in joined for token in ("dash", "tier1", "sentence_shell", "paragraph_shape"))
                    )
                    is_rhythm = (
                        "rhythm" in joined
                        or "pacing" in joined
                        or any(token in joined for token in ("pressure_ramp", "event_density", "reversal", "streak"))
                    )
                    if node_type == "chapter_anti_ai_review" and is_anti_ai:
                        filtered.append(failure)
                    elif node_type == "chapter_rhythm_review" and is_rhythm:
                        filtered.append(failure)
                    elif node_type == "chapter_quality_review" and not is_anti_ai and not is_rhythm:
                        filtered.append(failure)
                    elif node_type in ("chapter_skill_review", "chapter_review"):
                        filtered.append(failure)
                return filtered

            async def _run_chapter_skill_review_once() -> dict:
                from app.services.agent_skill_commit_gate import get_agent_skill_commit_gate

                scene_texts = _current_scene_texts()
                chapter_text = "\n\n".join(scene_texts.values())
                final_scene_contract = scene_contracts[0] if scene_contracts else {}
                final_writing_mode = (
                    final_scene_contract.get("writing_mode_profile")
                    if isinstance(final_scene_contract, dict)
                    else {}
                ) or {}
                async with async_session() as skill_db:
                    gate_result = await get_agent_skill_commit_gate().evaluate(
                        project_id=str(project_id),
                        db=skill_db,
                        final_text=chapter_text,
                        scene_contract=final_scene_contract,
                        chapter_state=chapter_state,
                        writing_mode_profile=final_writing_mode,
                        style_context=_workflow_style_context,
                        scene_units=[
                            {
                                "text": text,
                                "scene_contract": (
                                    scene_contracts[index]
                                    if index < len(scene_contracts)
                                    else {}
                                ),
                                "writing_mode_profile": (
                                    scene_contracts[index].get("writing_mode_profile") or {}
                                    if index < len(scene_contracts)
                                    else {}
                                ),
                            }
                            for index, text in sorted(scene_texts.items())
                        ],
                        allow_llm_repair=False,
                    )
                trace = gate_result.get("trace") or {}
                validation = trace.get("initial_validation") or {}
                return {
                    "allowed": bool(gate_result.get("allowed")),
                    "reason": gate_result.get("reason", ""),
                    "validation": validation,
                    "trace": {
                        "active_skills": trace.get("active_skills", []),
                        "validators": trace.get("validators", []),
                        "repair": trace.get("repair", {}),
                    },
                }

            async def _get_chapter_skill_review_result() -> dict:
                nonlocal chapter_skill_review_task
                if chapter_skill_review_task is None:
                    chapter_skill_review_task = asyncio.create_task(_run_chapter_skill_review_once())
                return await chapter_skill_review_task

            async def handle_chapter_level_review(node, context) -> None:
                step = node.workflow_step or node.node_id
                await update_step_locked(execution_id, step, "running", db=db)
                try:
                    raw_result = await _get_chapter_skill_review_result()
                    validation = dict(raw_result.get("validation") or {})
                    all_failures = [
                        item for item in validation.get("failures") or []
                        if isinstance(item, dict)
                    ]
                    failures = _chapter_review_failure_filter(node.node_type, all_failures)
                    filtered_validation = {
                        **validation,
                        "passed": len(failures) == 0,
                        "failures": failures,
                    }
                    chapter_review_outputs[node.node_type] = {
                        "validation": filtered_validation,
                        "source_validation": "chapter_skill_review_shared",
                        "raw_allowed": raw_result.get("allowed"),
                        "raw_reason": raw_result.get("reason", ""),
                        "trace": raw_result.get("trace", {}),
                    }
                    await update_step_locked(
                        execution_id,
                        step,
                        "completed",
                        output={
                            "runtime": "review_collection_parallel",
                            "source_validation": "chapter_skill_review_shared",
                            "failures": len(failures),
                            "failure_samples": _violation_sample(failures),
                            "raw_failures": len(all_failures),
                            "can_mutate_text": False,
                        },
                        db=db,
                    )
                except Exception as exc:
                    logger.warning("%s failed: %s", step, exc, exc_info=True)
                    # 方案15：用 ErrorClassifier 区分可重试/不可重试异常
                    from app.services.fbi.error_classifier import ErrorClassifier
                    is_retryable = ErrorClassifier.is_retryable(exc)
                    error_class = ErrorClassifier.classify(exc)
                    chapter_review_outputs[node.node_type] = {
                        "validation": {
                            "passed": False,
                            "failures": [{
                                "validator": node.node_type,
                                "metric": "review_unavailable",
                                "severity": "high",
                                "detail": str(exc),
                                "blocks_commit": True,
                            }],
                        },
                        "unavailable": True,
                        "error": str(exc),
                        "retryable": is_retryable,
                        "error_class": error_class,
                    }
                    if is_retryable:
                        # 方案15：可重试异常标记 retrying（不假装 completed），让 DAG runtime 决定是否重试
                        await update_step_locked(
                            execution_id,
                            step,
                            "retrying",
                            output={
                                "runtime": "review_collection_parallel",
                                "unavailable": True,
                                "error": str(exc),
                                "retryable": True,
                                "error_class": error_class,
                                "can_mutate_text": False,
                            },
                            db=db,
                        )
                    else:
                        # 不可重试：保留 completed + unavailable（优雅降级，下游 FBI intake 会处理不可用性）
                        await update_step_locked(
                            execution_id,
                            step,
                            "completed",
                            output={
                                "runtime": "review_collection_parallel",
                                "unavailable": True,
                                "error": str(exc),
                                "retryable": False,
                                "error_class": error_class,
                                "can_mutate_text": False,
                            },
                            db=db,
                        )

            async def handle_fbi_chapter_case_intake(node, context) -> None:
                """FBI 总案台受理：合并所有场景审校结果."""
                nonlocal current_repair_plan, current_review_case_file, repair_round
                intake_step = "fbi_case_intake"
                await update_step_locked(execution_id, intake_step, "running", db=db)

                try:
                    ready_packets = [p for p in review_packets if p is not None]
                    raw_blocking_count = sum(
                        len(p.blocking_violations) for p in ready_packets if not p.unavailable
                    )
                    raw_advisory_count = sum(
                        len(p.advisory_violations) for p in ready_packets if not p.unavailable
                    )
                    from app.services.review_case_file import (
                        ReviewCaseFileBuilder,
                        ReviewCaseFile,
                        combine_chapter_review_validations,
                    )

                    review_case = ReviewCaseFileBuilder().build(
                        project_id=project_id,
                        chapter_number=chapter_number,
                        draft_text="\n\n".join(_current_scene_texts().values()),
                        scene_packets=ready_packets,
                        skill_validation=combine_chapter_review_validations(chapter_review_outputs),
                        created_from="initial_review",
                        review_trace={
                            "parallel_review_nodes": [
                                f"parallel_review_{idx + 1}"
                                for idx, _ in enumerate(ready_packets)
                            ],
                            "chapter_review_nodes": sorted(chapter_review_outputs.keys()),
                            "chapter_review_outputs": {
                                key: {
                                    "failures": len(
                                        (value.get("validation") or {}).get("failures") or []
                                    ),
                                    "unavailable": bool(value.get("unavailable")),
                                }
                                for key, value in chapter_review_outputs.items()
                            },
                            "repair_round": repair_round,
                        },
                    )
                    current_review_case_file = review_case.model_dump()
                    # D19-1：把 L0 chapter 级 skill gate 结果转换为 case_file_delta，
                    # 注入 L1 intake，让 L2 修复时能处理 skill 维度违规（如
                    # abstract_bare_count、tense_drift_count 等）。
                    # 根因：L0 已在 chapter 级跑过 AgentSkillCommitGate（_run_chapter_skill_review_once），
                    #   但结果只存到 chapter_review_outputs，未转换为 case_file_delta。
                    #   L1 intake 的 _merge_review_case_file_issues 已有处理 case_file_deltas
                    #   的现成逻辑（chapter_case_intake.py:1120），只需把 delta 放入即可。
                    # 效果：L2 修复 skill 维度违规 → 终验阶段 case_file_delta 为空 →
                    #   跳过 FinalDeltaRepairRuntime（D19-2），节省 ~21min。
                    _l0_skill_deltas: list[dict] = []
                    try:
                        _skill_review = await _get_chapter_skill_review_result()
                        _skill_validation = dict(_skill_review.get("validation") or {})
                        _skill_advisories = _validation_advisory_findings(_skill_validation)
                        if not _skill_review.get("allowed", True) or _skill_advisories:
                            from app.services.fbi.final_acceptance_delta import (
                                build_final_acceptance_case_delta,
                            )
                            _chapter_text = "\n\n".join(_current_scene_texts().values())
                            _combined_failures = [
                                *(
                                    item for item in (_skill_validation.get("failures") or [])
                                    if isinstance(item, dict)
                                ),
                                *_skill_advisories,
                            ]
                            _delta_validation = {
                                **_skill_validation,
                                "passed": False,
                                "failures": _combined_failures,
                            }
                            _final_gate_result = {
                                "allowed": False,
                                "reason": (
                                    _skill_review.get("reason", "")
                                    or "l0_skill_advisory_best_effort_repair"
                                ),
                                "trace": {
                                    "initial_validation": _delta_validation,
                                    "repair": (_skill_review.get("trace") or {}).get("repair", {}),
                                },
                            }
                            _skill_delta = build_final_acceptance_case_delta(
                                project_id=project_id,
                                chapter_number=chapter_number,
                                final_text=_chapter_text,
                                final_gate_result=_final_gate_result,
                                acceptance_round=0,
                                parent_case_id=review_case.case_id if hasattr(review_case, "case_id") else "",
                                scene_texts=_current_scene_texts(),
                            )
                            if _skill_delta and (_skill_delta.get("scene_issues") or _skill_delta.get("chapter_issues") or _skill_delta.get("skill_failures")):
                                _l0_skill_deltas.append(_skill_delta)
                                logger.info(
                                    "D19-1: L0 skill gate delta injected into L1 intake: "
                                    "scene_issues=%d, skill_failures=%d",
                                    len(_skill_delta.get("scene_issues") or []),
                                    len(_skill_delta.get("skill_failures") or []),
                                )
                    except Exception as _skill_delta_exc:
                        logger.warning("D19-1: failed to build L0 skill delta: %s", _skill_delta_exc)
                    case = ChapterReviewCase(
                        project_id=project_id,
                        chapter_number=chapter_number,
                        chapter_initial_state=chapter_state,
                        scene_packets=ready_packets,
                        review_round=repair_round,
                        review_case_file=current_review_case_file,
                        case_file_deltas=_l0_skill_deltas,
                    )

                    intake_svc = FBIChapterCaseIntakeService()
                    validated_case = await intake_svc.intake(case)

                    planner = FBIChapterRepairPlanner()
                    current_repair_plan = await planner.plan(validated_case)
                    if validated_case.review_case_file:
                        current_review_case_file = validated_case.review_case_file
                    review_case_summary = (
                        ReviewCaseFile.model_validate(current_review_case_file).summary()
                        if current_review_case_file
                        else review_case.summary()
                    )
                    review_minister_summary = (
                        (current_repair_plan.repair_strategy_summary or {}).get("review_minister")
                        if current_repair_plan is not None
                        else {}
                    ) or {}
                    validated_blocking = [
                        v
                        for p in validated_case.scene_packets
                        for v in p.blocking_violations
                        if isinstance(v, dict)
                    ]
                    validated_advisory = [
                        v
                        for p in validated_case.scene_packets
                        for v in p.advisory_violations
                        if isinstance(v, dict)
                    ]

                    if raw_blocking_count > 0 and current_repair_plan.is_clean():
                        error_detail = (
                            "FBI intake produced a clean repair plan despite "
                            f"{raw_blocking_count} blocking review violations"
                        )
                        current_repair_plan = ChapterRepairPlan(
                            status="failed",
                            total_violations=raw_blocking_count + raw_advisory_count,
                            blocking_violations=raw_blocking_count,
                            global_notes=[error_detail],
                        )
                        await update_step_locked(execution_id, intake_step, "failed",
                                                  output={
                                                      "status": "failed",
                                                      "error": error_detail,
                                                      "raw_packet_count": len(ready_packets),
                                                      "raw_blocking_violations": raw_blocking_count,
                                                      "raw_advisory_violations": raw_advisory_count,
                                                      "runtime": "parallel_review",
                                                  }, db=db)
                        raise EditorDAGAbort({"content": "", "status": "failed", "error": error_detail})

                    await update_step_locked(execution_id, intake_step, "completed",
                                              output={
                                                  "status": current_repair_plan.status,
                                                  "total_violations": current_repair_plan.total_violations,
                                                  "blocking_violations": current_repair_plan.blocking_violations,
                                                  "cross_scene_conflicts": current_repair_plan.cross_scene_conflicts,
                                                  "orders_count": len(current_repair_plan.orders),
                                                  "repair_type_counts": _repair_type_counts(current_repair_plan.orders),
                                                  "blocking_samples": _violation_sample(validated_blocking),
                                                  "advisory_samples": _violation_sample(validated_advisory),
                                                  "order_samples": _repair_order_sample(current_repair_plan.orders),
                                                  "review_case_file": review_case_summary,
                                                  "fbi_department_phase": "case_desk",
                                                  "review_minister": review_minister_summary,
                                                  "quality_policy_enforcement": _quality_policy_enforcement_summary(ready_packets),
                                                  "raw_packet_count": len(ready_packets),
                                                  "raw_blocking_violations": raw_blocking_count,
                                                  "raw_advisory_violations": raw_advisory_count,
                                                  "runtime": "parallel_review",
                                              }, db=db)
                except EditorDAGAbort:
                    raise
                except Exception as e:
                    logger.exception("fbi_chapter_case_intake failed: %s", e)
                    current_repair_plan = ChapterRepairPlan(status="failed", global_notes=[str(e)])
                    await update_step_locked(execution_id, intake_step, "failed",
                                              output={"status": "failed", "error": str(e)}, db=db)
                    raise EditorDAGAbort({"content": "", "status": "failed", "error": str(e)})

            async def handle_fbi_chapter_repair_plan(node, context) -> None:
                """FBI 修复计划：记录修复计划状态."""
                plan_step = "fbi_repair_plan"
                if current_repair_plan is None:
                    await update_step_locked(execution_id, plan_step, "skipped", db=db)
                    return

                await update_step_locked(execution_id, plan_step, "running", db=db)
                await update_step_locked(execution_id, plan_step, "completed",
                                          output={
                                              "status": current_repair_plan.status,
                                              "orders": len(current_repair_plan.orders),
                                              "repair_type_counts": _repair_type_counts(current_repair_plan.orders),
                                              "order_samples": _repair_order_sample(current_repair_plan.orders),
                                              "fbi_repair_execution": {
                                                  "center": "FBI Repair Execution",
                                                  "execution_layers": current_repair_plan.execution_layers,
                                                  "execution_scope_trace": current_repair_plan.execution_scope_trace,
                                              },
                                              "fbi_department_phase": "repair_planning",
                                              "repair_strategy_summary": current_repair_plan.repair_strategy_summary,
                                              "runtime": "parallel_review",
                                          }, db=db)

            async def _pause_parallel_scene_for_human_review(
                *,
                scene_idx: int,
                gen_step: str,
                check_step: str,
                error_detail: str,
                pipeline_output: dict,
                violations: list[dict] | None = None,
                candidate_text: str | None = None,
                review_scope: str = "scene",
                db_session: AsyncSession,
            ) -> None:
                review_violations = [
                    normalize_violation_semantics(dict(violation))
                    for violation in (
                        violations
                        or pipeline_output.get("final_violations")
                        or pipeline_output.get("blocking_samples")
                        or pipeline_output.get("validator_error_samples")
                        or []
                    )
                    if isinstance(violation, dict)
                ]
                if not review_violations:
                    review_violations = [{
                        "type": "parallel_review_needs_human_review",
                        "severity": "high",
                        "detail": error_detail,
                        "blocks_commit": True,
                        "suggested_strategy": "manual_review",
                        "repair_scope": "prose_text",
                        "user_visible": True,
                    }]
                for violation in review_violations:
                    violation["blocks_commit"] = True
                    violation.setdefault("severity", "high")
                    violation.setdefault("suggested_strategy", "manual_review")
                    violation.setdefault("repair_scope", "prose_text")
                    violation.setdefault("user_visible", True)

                scene_texts = _current_scene_texts()
                normalized_scope = "chapter" if review_scope == "chapter" else "scene"
                scene_map = [
                    {
                        "scene_index": index,
                        "scene_id": str(
                            (
                                scene_exec.get(index, {}).get("scene_contract")
                                or scene_packages[index].get("scene_contract", {})
                                or {}
                            ).get("scene_id")
                            or f"scene_{index + 1}"
                        ),
                    }
                    for index in range(len(scene_packages))
                ]
                selected_text = (
                    candidate_text
                    if isinstance(candidate_text, str)
                    else "\n\n".join(scene_texts.get(index, "") for index in range(len(scene_packages))).strip()
                    if normalized_scope == "chapter"
                    else repaired_scene_texts.get(scene_idx)
                    or scene_exec.get(scene_idx, {}).get("generated_text")
                    or scene_texts.get(scene_idx, "")
                )
                approved_scene_texts = {
                    str(index): text
                    for index, text in scene_texts.items()
                    if index != scene_idx and isinstance(text, str) and text
                }
                chapter_scene_paragraph_counts = [
                    max(1, len([p for p in re.split(r"\n\s*\n", scene_texts.get(index, "")) if p.strip()]))
                    for index in range(len(scene_packages))
                ]
                pkg = scene_exec.get(scene_idx, {}).get("pkg") or scene_packages[scene_idx]
                review_output = {
                    **(pipeline_output or {}),
                    "runtime": "parallel_review",
                    "error_code": pipeline_output.get(
                        "error_code",
                        "parallel_review_needs_human_review",
                    ),
                    "final_violations": review_violations,
                    "candidate_text": selected_text,
                }
                resume_state = {
                    "project_id": project_id,
                    "chapter_number": chapter_number,
                    "pov_character": pov_character,
                    "custom_instructions": custom_instructions,
                    "scene_index": scene_idx,
                    "review_scope": normalized_scope,
                    "candidate_text": selected_text,
                    "approved_scene_texts": approved_scene_texts,
                    "scene_map": scene_map,
                    "chapter_scene_paragraph_counts": chapter_scene_paragraph_counts,
                    "review_version": review_version,
                    "scene_contract": scene_exec.get(scene_idx, {}).get("scene_contract") or {},
                    "scene_contracts": {
                        str(index): (
                            scene_exec.get(index, {}).get("scene_contract")
                            or (
                                scene_packages[index].get("scene_contract", {})
                                if index < len(scene_packages)
                                else {}
                            )
                            or {}
                        )
                        for index in range(len(scene_packages))
                    },
                    "chapter_state": chapter_state,
                    "character_cards": pkg.get("character_cards", []),
                    "character_names": pkg.get("character_names", []),
                    "scene_truth_snapshot": pkg.get("scene_truth_snapshot"),
                    "core_facts": pkg.get("core_facts", {}),
                    "word_budget": pkg.get("word_budget"),
                    "parallel_review_breakpoint": True,
                    "human_review_step": gen_step,
                }
                await pause_for_human_review_locked(
                    execution_id=execution_id,
                    gen_step=gen_step,
                    check_step=check_step,
                    error_detail=error_detail,
                    pipeline_output=review_output,
                    resume_state=resume_state,
                    db=db_session,
                    review_type="content_review",
                )

            async def _pause_parallel_scene_for_validator_retry(
                *,
                scene_idx: int,
                gen_step: str,
                check_step: str,
                error_detail: str,
                pipeline_output: dict,
                violations: list[dict],
                candidate_text: str,
                db_session: AsyncSession,
            ) -> None:
                validator_violations = _validator_only_system_findings(violations)
                if not validator_violations:
                    raise ValueError("parallel validator retry requires validator-only blockers")

                scene_texts = _current_scene_texts()
                pkg = scene_exec.get(scene_idx, {}).get("pkg") or scene_packages[scene_idx]
                scene_contract = scene_exec.get(scene_idx, {}).get("scene_contract") or {}
                approved_scene_texts = {
                    str(index): text
                    for index, text in scene_texts.items()
                    if index != scene_idx and isinstance(text, str) and text
                }
                validator_retry_info = {
                    "scene_index": scene_idx,
                    "candidate_text": candidate_text,
                    "pipeline_output": pipeline_output,
                    "error_code": validator_violations[0].get("type", "validator_unavailable"),
                    "error_detail": error_detail,
                    "scene_contract": scene_contract,
                    "chapter_state": chapter_state,
                    "character_cards": pkg.get("character_cards", []),
                    "character_names": pkg.get("character_names", []),
                    "scene_truth_snapshot": pkg.get("scene_truth_snapshot"),
                    "core_facts": pkg.get("core_facts", {}),
                    "word_budget": pkg.get("word_budget"),
                    "validator_retry_delays": [5, 15, 45],
                    "validator_retry_immediate_attempts": 0,
                    "validator_retry_delayed_attempts": 0,
                    "source_violations": validator_violations,
                    "source": "parallel_review",
                    "resume_step": gen_step,
                }
                resume_state = {
                    "project_id": project_id,
                    "chapter_number": chapter_number,
                    "pov_character": pov_character,
                    "custom_instructions": custom_instructions,
                    "scene_index": scene_idx,
                    "review_scope": "scene",
                    "candidate_text": candidate_text,
                    "approved_scene_texts": approved_scene_texts,
                    "review_version": review_version,
                    "scene_contract": scene_contract,
                    "chapter_state": chapter_state,
                    "character_cards": pkg.get("character_cards", []),
                    "character_names": pkg.get("character_names", []),
                    "scene_truth_snapshot": pkg.get("scene_truth_snapshot"),
                    "core_facts": pkg.get("core_facts", {}),
                    "word_budget": pkg.get("word_budget"),
                    "parallel_review_breakpoint": True,
                    "validator_retry_step": gen_step,
                }
                persisted = _json_snapshot({
                    "validator_retry": validator_retry_info,
                    "resume_state": resume_state,
                })
                await update_step_locked(
                    execution_id,
                    gen_step,
                    "pending_validator_retry",
                    output=pipeline_output,
                    error=error_detail,
                    db=db_session,
                )
                if check_step != gen_step:
                    await update_step_locked(
                        execution_id,
                        check_step,
                        "pending_validator_retry",
                        output=pipeline_output,
                        error=error_detail,
                        db=db_session,
                    )
                await complete_workflow_locked(
                    execution_id,
                    "pending_validator_retry",
                    result=persisted,
                    error=error_detail,
                    db=db_session,
                )
                _schedule_delayed_validator_retries(
                    execution_id=execution_id,
                    project_id=project_id,
                    delays=validator_retry_info["validator_retry_delays"],
                )

            async def handle_parallel_scene_repair(node, context) -> None:
                """并行修复：只修复有修复命令的场景."""
                scene_idx = int(node.scene_index or 0)
                repair_step = f"parallel_repair_{scene_idx + 1}"

                # Node-owned session (see handle_parallel_scene_review): the DAG
                # runtime runs sibling scenes concurrently and a shared request
                # session would be used from multiple coroutines at once.
                async with async_session() as node_db:
                    if current_repair_plan is None or current_repair_plan.is_clean():
                        await update_step_locked(execution_id, repair_step, "skipped", db=node_db)
                        return

                    orders = [
                        order
                        for order in current_repair_plan.orders
                        if _repair_owner(order) == scene_idx
                    ]
                    if not orders:
                        await update_step_locked(execution_id, repair_step, "skipped", db=node_db)
                        return

                    await update_step_locked(execution_id, repair_step, "running", db=node_db)
                    try:
                        scene_texts = _current_scene_texts()
                        scene_text = scene_texts.get(scene_idx, "")

                        executor = ChapterRepairExecutor()
                        # Build an owner-scoped plan for this scene. Cross-scene orders
                        # run once on their owner scene while receiving the full chapter dossier.
                        from app.models.chapter_review import ChapterRepairPlan as _CRP
                        from app.models.chapter_review import RevisionBlueprint as _RB
                        # §3.2 G1: 透传 L6 的 LLM 蓝图 work_units 到 L7，避免 L6→L7 传递断层。
                        # 按 owner_scene 过滤，只把当前 scene 的 work_units 传给执行器。
                        in_place_retry = scene_idx in (reviewed_scene_repair_indexes or set())
                        rewritten_order_ids = {
                            order.order_id
                            for order in orders
                            if in_place_retry and order.repair_type == "scene_rewrite"
                        }
                        rewritten_work_unit_ids = {
                            order.work_unit_id
                            for order in orders
                            if (
                                in_place_retry
                                and order.repair_type == "scene_rewrite"
                                and order.work_unit_id
                            )
                        }
                        scene_work_units = [
                            wu for wu in (current_repair_plan.work_units or [])
                            if wu.owner_scene == scene_idx
                            and getattr(wu, "work_unit_id", "") not in rewritten_work_unit_ids
                            and not (
                                rewritten_order_ids
                                & set(getattr(wu, "source_order_ids", []) or [])
                            )
                        ]
                        scene_blueprint_work_units = [
                            wu for wu in (current_repair_plan.revision_blueprint.work_units or [])
                            if wu.owner_scene == scene_idx
                            and getattr(wu, "work_unit_id", "") not in rewritten_work_unit_ids
                            and not (
                                rewritten_order_ids
                                & set(getattr(wu, "source_order_ids", []) or [])
                            )
                        ]
                        source_blueprint = current_repair_plan.revision_blueprint
                        scene_blueprint = _RB(
                            blueprint_id=source_blueprint.blueprint_id,
                            case_id=source_blueprint.case_id,
                            work_units=scene_blueprint_work_units,
                            planning_trace=list(source_blueprint.planning_trace or []),
                            validator_snapshot=dict(source_blueprint.validator_snapshot or {}),
                            completion_summary=dict(source_blueprint.completion_summary or {}),
                            status=source_blueprint.status,
                        )
                        single_plan = _CRP(
                            case_id=current_repair_plan.case_id,
                            status="needs_repair",
                            orders=[
                                _as_in_place_review_order(order)
                                if in_place_retry
                                else order.model_copy(deep=True)
                                for order in orders
                            ],
                            revision_blueprint=scene_blueprint,
                            work_units=scene_work_units,
                        )
                        updated_texts, updated_plan = await executor.execute(
                            single_plan,
                            scene_texts,
                            context={
                                "project_id": project_id,
                                "chapter_number": chapter_number,
                                "scene_index": scene_idx,
                                "scene_contracts": _scene_contract_map(),
                                "scene_truth_snapshots": _scene_truth_snapshot_map(),
                                "chapter_state": chapter_state,
                                "chapter_scene_context": _chapter_scene_context(scene_texts),
                                "db_session_factory": async_session,
                                "allow_legacy_fbi_executor": False,
                                "cancellation_check": stop_if_cancelled,
                                **_style_repair_context_snapshot(),
                            },
                            case_file={
                                "all_scene_texts": scene_texts,
                                "all_scene_contracts": _scene_contract_map(),
                                "chapter_state": chapter_state,
                                "review_packets": review_packets,
                                "review_case_file": current_review_case_file or {},
                                "repair_plan": current_repair_plan,
                                "scene_truth_snapshots": _scene_truth_snapshot_map(),
                                "allow_legacy_fbi_executor": False,
                                "style_context": _workflow_style_context,
                                "style_profile": _workflow_style_context,
                            },
                        )
                        order_status_by_id = {o.order_id: o.status for o in updated_plan.orders}
                        order_hash_by_id = {o.order_id: o.result_text_hash for o in updated_plan.orders}
                        order_audit_by_id = {o.order_id: getattr(o, "repair_audit", {}) or {} for o in updated_plan.orders}
                        for order in orders:
                            if order.order_id in order_status_by_id:
                                order.status = order_status_by_id[order.order_id]
                                order.result_text_hash = order_hash_by_id.get(order.order_id, "")
                                order.repair_audit = order_audit_by_id.get(order.order_id, {})

                        failed_orders = [
                            o for o in orders
                            if _repair_order_failure_disposition(o) != "accepted"
                        ]
                        order_dispositions = {
                            o.order_id: _repair_order_failure_disposition(o)
                            for o in failed_orders
                        }
                        hard_failed_orders = [
                            o for o in failed_orders
                            if order_dispositions.get(o.order_id) == "hard_failed"
                        ]
                        human_review_failed_orders = [
                            o for o in failed_orders
                            if order_dispositions.get(o.order_id) == "needs_human_review"
                        ]
                        blocking_failed_orders = human_review_failed_orders
                        recoverable_failed_orders = [
                            o for o in failed_orders
                            if order_dispositions.get(o.order_id) == "recoverable_delta"
                        ]
                        retryable_failed_orders = recoverable_failed_orders + hard_failed_orders
                        # 方案14 Part C：保护对共享可变状态的 read-modify-write 操作
                        async with scene_state_lock:
                            for order in retryable_failed_orders:
                                disposition = order_dispositions.get(order.order_id, "recoverable_delta")
                                order.status = "skipped"
                                order.repair_audit = {
                                    **(order.repair_audit or {}),
                                    "disposition": disposition,
                                    "auto_retry_via_delta": True,
                                    "continue_to_recheck": True,
                                }
                                for target_scene in sorted(_repair_affected_scenes(order)):
                                    scenes_requiring_recheck.add(target_scene)
                                    repair_failed_deltas.setdefault(target_scene, []).extend(
                                        _repair_failure_delta_items(
                                            order,
                                            disposition,
                                            updated_texts,
                                        )
                                    )
                                    if target_scene not in repaired_scene_texts:
                                        base_text = scene_texts.get(target_scene, "")
                                        if base_text:
                                            repaired_scene_texts[target_scene] = base_text

                            for order in orders:
                                audit = getattr(order, "repair_audit", {}) or {}
                                if (
                                    getattr(order, "repair_type", "") == "contract_patch"
                                    and _repair_order_failure_disposition(order) == "accepted"
                                    and audit.get("contract_changed")
                                ):
                                    for target_scene in sorted(_repair_affected_scenes(order)):
                                        scenes_requiring_recheck.add(target_scene)
                                        if target_scene not in repaired_scene_texts:
                                            base_text = scene_texts.get(target_scene, "")
                                            if base_text:
                                                repaired_scene_texts[target_scene] = base_text

                            changed_scenes: list[int] = []
                            for updated_idx, repaired_text in updated_texts.items():
                                original_text = scene_texts.get(updated_idx, "")
                                if repaired_text and repaired_text != original_text:
                                    repaired_scene_texts[updated_idx] = repaired_text
                                    scene_exec.setdefault(updated_idx, {})["generated_text"] = repaired_text
                                    changed_scenes.append(updated_idx)
                                    scenes_requiring_recheck.add(updated_idx)

                        repair_output = {
                            "order_id": orders[0].order_id,
                            "repair_type": orders[0].repair_type,
                            "status": (
                                "degraded"
                                if retryable_failed_orders and not blocking_failed_orders
                                else orders[0].status
                            ),
                            "failed_orders": len(blocking_failed_orders),
                            "hard_failed_orders": len(hard_failed_orders),
                            "human_review_failed_orders": len(human_review_failed_orders),
                            "recoverable_failed_orders": len(recoverable_failed_orders),
                            "retryable_failed_orders": len(retryable_failed_orders),
                            "orders": len(orders),
                            "order_samples": _repair_order_sample(orders),
                            "changed_scenes": changed_scenes,
                            "recheck_scenes": sorted(scenes_requiring_recheck),
                            "repair_failed_delta_samples": _violation_sample(
                                [
                                    item
                                    for order in retryable_failed_orders
                                    for item in _repair_failure_delta_items(
                                        order,
                                        order_dispositions.get(order.order_id, "recoverable_delta"),
                                        updated_texts,
                                    )
                                ][:5]
                            ),
                            "context_scene_count": len(scene_texts),
                            "owner_scoped": True,
                            "continue_to_recheck": not blocking_failed_orders,
                            "runtime": "parallel_review",
                        }
                        await update_step_locked(
                            execution_id,
                            repair_step,
                            "waiting_review" if blocking_failed_orders else "completed",
                            output=repair_output,
                            db=node_db,
                        )
                        if blocking_failed_orders:
                            error_detail = (
                                f"scene {scene_idx + 1} repair failed for "
                                f"{len(blocking_failed_orders)} blocking order(s)"
                            )
                            blocking_failure_violations = [
                                item
                                for order in blocking_failed_orders
                                for item in _repair_failure_delta_items(
                                    order,
                                    order_dispositions.get(order.order_id, "needs_human_review"),
                                    updated_texts,
                                )
                                if isinstance(item, dict)
                            ]
                            validator_blockers = _validator_system_blockers(
                                blocking_failure_violations
                            )
                            if validator_blockers:
                                candidate_text = _current_scene_texts().get(scene_idx, "")
                                validator_output = {
                                    **repair_output,
                                    "error_code": validator_blockers[0].get(
                                        "type", "validator_unavailable"
                                    ),
                                    "validator_error_samples": validator_blockers,
                                }
                                await _pause_parallel_scene_for_validator_retry(
                                    scene_idx=scene_idx,
                                    gen_step=repair_step,
                                    check_step=f"parallel_recheck_{scene_idx + 1}",
                                    error_detail=error_detail,
                                    pipeline_output=validator_output,
                                    violations=validator_blockers,
                                    candidate_text=candidate_text,
                                    db_session=node_db,
                                )
                                raise EditorDAGAbort({
                                    "content": "",
                                    "status": "pending_validator_retry",
                                    "error": error_detail,
                                })
                            await _pause_parallel_scene_for_human_review(
                                scene_idx=scene_idx,
                                gen_step=repair_step,
                                check_step=f"parallel_recheck_{scene_idx + 1}",
                                error_detail=error_detail,
                                pipeline_output={
                                    **repair_output,
                                    "error_code": "parallel_repair_needs_human_review",
                                    "hard_failed_orders": len(hard_failed_orders),
                                    "human_review_failed_orders": len(human_review_failed_orders),
                                },
                                violations=blocking_failure_violations,
                                candidate_text=_current_scene_texts().get(scene_idx, ""),
                                db_session=node_db,
                            )
                            raise EditorDAGAbort({"content": "", "status": "waiting_review", "error": error_detail})
                    except Exception as e:
                        if isinstance(e, EditorDAGAbort):
                            raise
                        logger.warning("parallel_scene_repair %d failed: %s", scene_idx, e)
                        from app.services.fbi.error_classifier import ErrorClassifier
                        # 方案15：用 ErrorClassifier 统一判断 contract 错误（替代手动字符串匹配）
                        # contract_patch RuntimeError 等阻断性异常不应被吞成 completed
                        is_contract_error = ErrorClassifier.is_contract_error(e)
                        if is_contract_error:
                            error_detail = str(e)
                            pipeline_output = {
                                "error": error_detail,
                                "repair_blocked": True,
                                "error_code": "parallel_repair_needs_human_review",
                                "runtime": "parallel_review",
                            }
                            await update_step_locked(
                                execution_id,
                                repair_step,
                                "waiting_review",
                                output=pipeline_output,
                                error=error_detail,
                                db=node_db,
                            )
                            await _pause_parallel_scene_for_human_review(
                                scene_idx=scene_idx,
                                gen_step=repair_step,
                                check_step=f"parallel_recheck_{scene_idx + 1}",
                                error_detail=error_detail,
                                pipeline_output=pipeline_output,
                                violations=[{
                                    "type": "repair_execution_exception",
                                    "severity": "high",
                                    "detail": error_detail,
                                    "blocks_commit": True,
                                    "suggested_strategy": "manual_review",
                                    "repair_scope": "prose_text",
                                    "user_visible": True,
                                }],
                                candidate_text=_current_scene_texts().get(scene_idx, ""),
                                db_session=node_db,
                            )
                            raise EditorDAGAbort({"content": "", "status": "waiting_review", "error": error_detail})
                        # 方案15：可重试异常标注 retryable/error_class（不在此自动重试，状态管理交给 DAG runtime）
                        is_retryable = ErrorClassifier.is_retryable(e)
                        error_class = ErrorClassifier.classify(e)
                        logger.error(
                            "parallel_scene_repair %d %s error (class=%s), marking failed: %s",
                            scene_idx, "retryable" if is_retryable else "non-retryable", error_class, e
                        )
                        await update_step_locked(
                            execution_id,
                            repair_step,
                            "failed",
                            output={
                                "error": str(e),
                                "repair_blocked": False,
                                "retryable": is_retryable,  # 方案15：标注可重试性
                                "error_class": error_class,  # 方案15：持久化异常分类
                            },
                            error=str(e),
                            db=node_db,
                        )

            async def handle_parallel_scene_recheck(node, context) -> None:
                """并行复检：只复检被修复的场景.

                复检决策策略：
                - content_blocking: 正文违反硬约束 → 自动再修复（最多 MAX_REPAIR_ROUNDS 轮）
                - validator_unavailable: 校验器自身失败 → 标记但不判正文死刑
                - repair_failed: 修复器未产出有效修改 → 阻断
                - 超过最大轮数仍有 blocking → blocked / needs_human_review
                """
                scene_idx = int(node.scene_index or 0)
                recheck_step = f"parallel_recheck_{scene_idx + 1}"

                # Node-owned session (see handle_parallel_scene_review). Recheck
                # also drives a multi-round repair loop, all of which must stay
                # off the shared request-scoped session.
                async with async_session() as node_db:
                    should_recheck_scene = (
                        scene_idx in repaired_scene_texts
                        or scene_idx in scenes_requiring_recheck
                    )
                    if not should_recheck_scene:
                        await update_step_locked(execution_id, recheck_step, "skipped", db=node_db)
                        return

                    # 修复（循环 #17 D17-6）：MAX_REPAIR_ROUNDS 从 2 降到 1。
                    # 原值 2：L3 recheck 内部做 2 轮修复循环，每轮 ~9min（recheck+auto_repair+review）。
                    #   但 L3 失败的问题 L4 会再修（MAX_CHAPTER_REPAIR_ROUNDS=1），终验也会兜底。
                    # 新值 1：L3 只做 1 轮修复循环。如果失败，留给 L4 和终验处理。
                    # 大局观：L3(1轮) + L4(1轮) + 终验(1轮) = 3 轮总修复机会，足够覆盖大部分场景。
                    #   原 L3(2轮) + L4(2轮) + 终验(2轮) = 6 轮，大部分轮次是无效重试。
                    MAX_REPAIR_ROUNDS = 1
                    repair_attempts = 0
                    candidate_decisions: list[dict] = []
                    recheck_started = time.monotonic()
                    initial_review_ms: int | None = None
                    repair_round_timings: list[dict] = []

                    def _classify_recheck_violations(packet_obj):
                        content_blocking_items = []
                        validator_error_items = []
                        style_advisory_items = []

                        for violation in getattr(packet_obj, "blocking_violations", []) or []:
                            violation = normalize_violation_semantics(violation)
                            v_type = (violation.get("type") or "").lower()
                            detail_text = str(violation.get("detail") or "").strip()
                            has_actionable_evidence = bool(
                                detail_text
                                and detail_text not in {"Foreshadowing text violation:"}
                                or str(violation.get("target_span") or "").strip()
                                or violation.get("evidence")
                            )
                            if not has_actionable_evidence and v_type not in {"empty_text", "missing_contract"}:
                                style_advisory_items.append(violation)
                                continue
                            lane = _classify_review_violation_for_commit(violation)
                            if lane == "validator_error":
                                validator_error_items.append(violation)
                            elif lane == "content_blocking":
                                content_blocking_items.append(violation)
                            elif lane == "style_advisory":
                                style_advisory_items.append(violation)
                            else:
                                severity = (violation.get("severity") or "").lower()
                                blocks_commit = bool(violation.get("blocks_commit"))
                                if blocks_commit and severity in ("critical", "high", "blocking"):
                                    content_blocking_items.append(violation)
                                else:
                                    style_advisory_items.append(violation)

                        style_advisory_items.extend(
                            normalize_violation_semantics(violation)
                            for violation in getattr(packet_obj, "advisory_violations", []) or []
                            if isinstance(violation, dict)
                        )
                        return content_blocking_items, validator_error_items, style_advisory_items

                    await update_step_locked(execution_id, recheck_step, "running", db=node_db)
                    try:
                        # Re-run review on repaired text
                        recheck_text = repaired_scene_texts.get(scene_idx)
                        if recheck_text is None:
                            recheck_text = _current_scene_texts().get(scene_idx, "")
                        if not recheck_text:
                            await update_step_locked(
                                execution_id,
                                recheck_step,
                                "skipped",
                                output={
                                    "runtime": "parallel_review",
                                    "skip_reason": "no_text_for_recheck",
                                },
                                db=node_db,
                            )
                            return
                        worker = SceneReviewWorker(scene_index=scene_idx)
                        context = {
                            "generated_text": recheck_text,
                            "scene_contract": scene_exec.get(scene_idx, {}).get("scene_contract") or {},
                            "current_state": chapter_state,
                            "core_facts": scene_exec.get(scene_idx, {}).get("pkg", {}).get("core_facts", {}),
                            "character_names": scene_exec.get(scene_idx, {}).get("pkg", {}).get("character_names", []),
                            "character_cards": scene_exec.get(scene_idx, {}).get("pkg", {}).get("character_cards", []),
                            "project_id": project_id,
                            "chapter_number": chapter_number,
                            "scene_index": scene_idx,
                            "project": project,
                            "db_session_factory": async_session,
                        }
                        if (
                            initial_review_packets[scene_idx] is not None
                            and _repair_plan_preserves_review_claims(current_repair_plan, scene_idx)
                        ):
                            context["precomputed_review_packet"] = initial_review_packets[scene_idx]
                            context["quality_level"] = "fast"
                        initial_review_started = time.monotonic()
                        # Token 优化：如果 recheck_text 与 L1 review 时的文本完全相同，
                        # 且 L1 packet 可用（非 None、非 unavailable），跳过完整 QualityGate，
                        # 直接复用 L1 packet。适用于 L2 修复失败/回滚但需 recheck 的场景。
                        baseline_packet_for_skip = initial_review_packets[scene_idx]
                        l1_text = initial_review_texts.get(scene_idx)
                        can_reuse_l1 = (
                            baseline_packet_for_skip is not None
                            and not getattr(baseline_packet_for_skip, "unavailable", False)
                            and l1_text is not None
                            and recheck_text.strip() == l1_text.strip()
                        )
                        if can_reuse_l1:
                            packet = baseline_packet_for_skip.model_copy(deep=True)
                            initial_review_ms = 0
                            logger.info(
                                "parallel_scene_recheck %d: reusing L1 packet (text unchanged), "
                                "skipping full QualityGate",
                                scene_idx,
                            )
                        else:
                            packet = await worker.review(context)
                            initial_review_ms = int((time.monotonic() - initial_review_started) * 1000)
                        from app.services.scene_contract_protocol import (
                            stabilize_recheck_contract_violations,
                        )

                        baseline_packet = initial_review_packets[scene_idx]
                        stable_blocking, demoted_contract = (
                            stabilize_recheck_contract_violations(
                                initial_violations=(
                                    baseline_packet.blocking_violations
                                    if baseline_packet is not None
                                    else []
                                ),
                                recheck_blocking=packet.blocking_violations,
                                scene_contract=context["scene_contract"],
                                scene_index=scene_idx,
                            )
                        )
                        packet.blocking_violations = stable_blocking
                        packet.advisory_violations.extend(demoted_contract)
                        review_packets[scene_idx] = packet

                        content_blocking, validator_errors, style_advisory = _classify_recheck_violations(packet)

                        # 失败记忆注入：如果该场景有 L3 修复失败的 delta，把 repair_failure
                        # 标记注入到当前 content_blocking 中，让 _auto_repair_scene 构建的
                        # repair orders 携带失败记忆 → chapter_case_intake._select_repair_lane
                        # 的 _previously_hard_failed 逻辑能触发 lane 升级。
                        # 根因：L4 recheck 从 fresh review packet 提取违规，不带 repair_failure
                        #   字段，导致失败记忆升级机制在 L4 永远不生效，相同策略反复失败。
                        _inject_repair_failure_memory(content_blocking, scene_idx)

                        # ---- 多轮修复闭环 ----
                        while content_blocking and repair_attempts < MAX_REPAIR_ROUNDS:
                            # 自动进入下一轮修复：构建新的 repair order 并执行
                            logger.info(
                                "parallel_scene_recheck %d: repair attempt %d has %d content_blocking, "
                                "attempting auto-repair round %d",
                                scene_idx, repair_attempts, len(content_blocking), repair_attempts + 1,
                            )
                            try:
                                attempt_started = time.monotonic()
                                new_text = await asyncio.wait_for(
                                    _auto_repair_scene(
                                        scene_idx, content_blocking, packet, repair_attempts,
                                    ),
                                    timeout=_RECHECK_AUTO_REPAIR_TIMEOUT_SECONDS,
                                )
                                auto_repair_ms = int((time.monotonic() - attempt_started) * 1000)
                                repair_attempts += 1
                                if new_text:
                                    # Token 优化：如果修复器返回的文本与当前文本完全相同，
                                    # 跳过完整 QualityGate review（结果必然与上一轮相同）。
                                    # 这种情况发生在修复器判定"无需修改"或"无法修改"时，
                                    # 避免浪费 6-9 次 LLM 调用。
                                    current_text = context.get("generated_text") or ""
                                    if new_text.strip() == current_text.strip():
                                        repair_round_timings.append({
                                            "round": repair_attempts,
                                            "auto_repair_ms": auto_repair_ms,
                                            "review_ms": 0,
                                            "accepted": False,
                                            "status": "no_text_change",
                                            "skipped_review": True,
                                        })
                                        logger.info(
                                            "parallel_scene_recheck %d: skipping review for round %d, "
                                            "repair produced no text change",
                                            scene_idx, repair_attempts,
                                        )
                                        # 文本未变，无法通过修复改善，跳出循环
                                        break
                                    candidate_context = dict(context)
                                    candidate_context["generated_text"] = new_text
                                    # 更新 context 用于下一轮复检
                                    candidate_review_started = time.monotonic()
                                    packet2 = await asyncio.wait_for(
                                        worker.review(candidate_context),
                                        timeout=_RECHECK_REVIEW_TIMEOUT_SECONDS,
                                    )
                                    candidate_review_ms = int((time.monotonic() - candidate_review_started) * 1000)
                                    stable_blocking, demoted_contract = (
                                        stabilize_recheck_contract_violations(
                                            initial_violations=(
                                                baseline_packet.blocking_violations
                                                if baseline_packet is not None
                                                else []
                                            ),
                                            recheck_blocking=packet2.blocking_violations,
                                            scene_contract=context["scene_contract"],
                                            scene_index=scene_idx,
                                        )
                                    )
                                    packet2.blocking_violations = stable_blocking
                                    packet2.advisory_violations.extend(demoted_contract)
                                    from app.services.agent_skill_candidate_pipeline import (
                                        ReviewCandidatePipeline,
                                    )

                                    decision = ReviewCandidatePipeline().assess(
                                        before_violations=_review_packet_violations(packet),
                                        after_violations=_review_packet_violations(packet2),
                                        target_violations=content_blocking,
                                        classify_lane=_classify_review_violation_for_commit,
                                    )
                                    decision_record = {
                                        "round": repair_attempts,
                                        **decision,
                                        "timing_ms": int((time.monotonic() - attempt_started) * 1000),
                                        "auto_repair_ms": auto_repair_ms,
                                        "review_ms": candidate_review_ms,
                                    }
                                    candidate_decisions.append(decision_record)
                                    repair_round_timings.append({
                                        "round": repair_attempts,
                                        "auto_repair_ms": auto_repair_ms,
                                        "review_ms": candidate_review_ms,
                                        "accepted": bool(decision.get("accepted")),
                                        "status": decision.get("status"),
                                    })
                                    if decision["accepted"]:
                                        # 方案14 Part C：保护对共享可变状态的写入
                                        async with scene_state_lock:
                                            repaired_scene_texts[scene_idx] = new_text
                                            scene_exec.setdefault(scene_idx, {})["generated_text"] = new_text
                                            context["generated_text"] = new_text
                                            review_packets[scene_idx] = packet2
                                            content_blocking, validator_errors, style_advisory = _classify_recheck_violations(packet2)
                                            packet = packet2
                                    else:
                                        if decision.get("status") == "rejected_no_progress":
                                            decision_record["early_stop"] = "no_target_progress"
                                            logger.warning(
                                                "parallel_scene_recheck %d: stopping repair loop after no target progress in round %d",
                                                scene_idx,
                                                repair_attempts,
                                            )
                                            break
                                        logger.warning(
                                            "parallel_scene_recheck %d: candidate round %d rolled back: %s",
                                            scene_idx,
                                            repair_attempts,
                                            decision,
                                        )
                                else:
                                    repair_round_timings.append({
                                        "round": repair_attempts,
                                        "auto_repair_ms": auto_repair_ms,
                                        "review_ms": 0,
                                        "accepted": False,
                                        "status": "empty_candidate",
                                    })
                            except Exception as repair_exc:
                                logger.warning(
                                    "parallel_scene_recheck %d: auto-repair attempt %d failed: %s",
                                    scene_idx, repair_attempts + 1, repair_exc,
                                )
                                repair_attempts += 1
                                # 修复失败，保持原有 blocking 状态

                        has_content_blocking = len(content_blocking) > 0
                        has_validator_errors = len(validator_errors) > 0
                        recheck_output = {
                            "repairability": packet.repairability,
                            "content_blocking": len(content_blocking),
                            "validator_errors": len(validator_errors),
                            "style_advisory": len(style_advisory),
                            "blocking_samples": _violation_sample(content_blocking[:3]),
                            "validator_error_samples": _violation_sample(validator_errors[:3]),
                            "quality_policy_enforcement": _quality_policy_enforcement_summary(packet),
                            "runtime": "parallel_review",
                            "recheck_blocked": has_content_blocking,
                            "validator_unavailable": has_validator_errors,
                            "recheck_reason": (
                                "repair_candidate_rejected"
                                if scene_idx in repair_failed_deltas
                                else "scene_text_changed"
                            ),
                            "repair_failed_delta_count": len(
                                repair_failed_deltas.get(scene_idx, [])
                            ),
                            "repair_failed_delta_samples": _violation_sample(
                                repair_failed_deltas.get(scene_idx, [])[:3]
                            ),
                            "repair_rounds_attempted": repair_attempts,
                            "repair_disabled": True,
                            "repair_disabled_reason": "recheck_review_only",
                            "candidate_decisions": candidate_decisions,
                            "timing": {
                                "total_ms": int((time.monotonic() - recheck_started) * 1000),
                                "initial_review_ms": initial_review_ms,
                                "initial_review_reused_l1": can_reuse_l1,
                                "repair_rounds": repair_round_timings,
                            },
                        }
                        await update_step_locked(
                            execution_id,
                            recheck_step,
                            "failed" if has_validator_errors else "completed",
                            output=recheck_output,
                            db=node_db,
                        )

                        if has_content_blocking:
                            logger.info(
                                "parallel_scene_recheck %d: recorded %d content blocking issue(s) for FBI delta cycle",
                                scene_idx, len(content_blocking),
                            )

                        if has_validator_errors:
                            # Validator availability is a system concern.  Preserve the
                            # candidate and retry validation; do not send it to the prose
                            # workbench or spend a content-repair round.
                            error_detail = (
                                f"scene {scene_idx + 1} recheck: "
                                f"{len(validator_errors)} validator error(s), "
                                f"cannot reliably verify content quality"
                            )
                            await _pause_parallel_scene_for_validator_retry(
                                scene_idx=scene_idx,
                                gen_step=recheck_step,
                                check_step=recheck_step,
                                error_detail=error_detail,
                                pipeline_output={
                                    **recheck_output,
                                    "error_code": "parallel_recheck_validator_needs_human_review",
                                },
                                violations=validator_errors,
                                candidate_text=recheck_text,
                                db_session=node_db,
                            )
                            raise EditorDAGAbort({
                                "content": "",
                                "status": "pending_validator_retry",
                                "error": error_detail,
                            })

                    except Exception as e:
                        if isinstance(e, EditorDAGAbort):
                            raise
                        logger.warning("parallel_scene_recheck %d failed: %s", scene_idx, e)
                        await update_step_locked(execution_id, recheck_step, "failed",
                                                  output={"error": str(e)}, db=node_db)
                        raise EditorDAGAbort({"content": "", "status": "failed", "error": str(e)})

            async def _auto_repair_scene(
                scene_idx: int,
                blocking_violations: list[dict],
                review_packet,
                current_round: int,
            ) -> str | None:
                """对单个场景执行自动修复，返回修复后的正文或 None。"""
                from app.models.chapter_review import ChapterRepairOrder as _CRO, ChapterRepairPlan as _CRP
                from app.services.fbi.chapter_case_intake import FBIChapterCaseIntakeService, FBIChapterRepairPlanner

                # 根据违规构建修复订单
                orders = []
                for i, v in enumerate(blocking_violations):
                    # 修复策略由问题责任域决定，重试轮次不得扩大修改范围。
                    repair_type = _recheck_repair_type(v)
                    # 通用修复（循环 #13）：复用 FBI intake 的 lane 选择逻辑，确保 L4 recheck
                    # 与 L3 修复使用一致的 lane/strength，且失败记忆（repair_failure: True）
                    # 能触发 _select_repair_lane 的 lane 升级。
                    # 根因：此前 _auto_repair_scene 创建的 orders 不设 repair_lane，
                    #   executor 的 _lane_for_attempt 返回空 lane，LLM 修复指令缺失，
                    #   且 hard_failed 失败记忆无法触发升级，相同策略反复失败。
                    #   通用性：所有违规类型在 L4 recheck 都应走与 L3 一致的 lane 选择。
                    # 修复（循环 #16）：_determine_repair_domain 和 _select_repair_lane 是
                    #   FBIChapterRepairPlanner 的方法（不是 FBIChapterCaseIntakeService），
                    #   原代码用错误类名调用导致 AttributeError，L3 recheck 自动修复完全失败。
                    repair_domain = FBIChapterRepairPlanner._determine_repair_domain(v, repair_type)
                    lane_info = FBIChapterRepairPlanner._select_repair_lane(
                        v, repair_type=repair_type, repair_domain=repair_domain,
                    )

                    orders.append(_CRO(
                        order_id=f"auto_r{current_round + 1}_s{scene_idx}_{i}",
                        target_scenes=[scene_idx],
                        owner_scene=scene_idx,
                        repair_type=repair_type,
                        repair_lane=lane_info["repair_lane"],
                        repair_strength=lane_info["repair_strength"],
                        allowed_max_strength=lane_info["allowed_max_strength"],
                        candidate_attempt_budget=lane_info["candidate_attempt_budget"],
                        priority=v.get("severity", "high"),
                        reason=v.get("detail", v.get("description", "")),
                        instruction=v.get("target_span", v.get("detail", "")),
                        expected_after_repair={
                            "description": v.get("expected_behavior", ""),
                            "preserve": [],
                            "remove": [],
                        },
                        source_violation_ids=[v.get("violation_id", f"auto_v{i}")],
                        violation_details=[v],
                    ))

                single_plan = _CRP(
                    case_id=f"auto_repair_r{current_round + 1}",
                    status="needs_repair",
                    orders=orders,
                )

                scene_texts = _current_scene_texts()
                executor = ChapterRepairExecutor()
                try:
                    updated_texts, updated_plan = await executor.execute(
                        single_plan,
                        scene_texts,
                        context={
                            "project_id": project_id,
                            "chapter_number": chapter_number,
                            "scene_index": scene_idx,
                            "scene_contracts": _scene_contract_map(),
                            "scene_truth_snapshots": _scene_truth_snapshot_map(),
                            "chapter_state": chapter_state,
                            "chapter_scene_context": _chapter_scene_context(scene_texts),
                            "db_session_factory": async_session,
                            "cancellation_check": stop_if_cancelled,
                            "allow_legacy_fbi_executor": False,
                            **_style_repair_context_snapshot(),
                        },
                        case_file={
                            "all_scene_texts": scene_texts,
                            "all_scene_contracts": _scene_contract_map(),
                            "chapter_state": chapter_state,
                            "review_packets": review_packets,
                            "review_case_file": current_review_case_file or {},
                            "repair_plan": current_repair_plan,
                            "scene_truth_snapshots": _scene_truth_snapshot_map(),
                            "previous_repair_output": repaired_scene_texts.get(scene_idx),
                            "recheck_failure_samples": _violation_sample(blocking_violations[:3]),
                            "allow_legacy_fbi_executor": False,
                            "style_context": _workflow_style_context,
                            "style_profile": _workflow_style_context,
                        },
                    )
                    failed_orders = [
                        order for order in updated_plan.orders
                        if _repair_order_failure_disposition(order) != "accepted"
                    ]
                    blocking_failed_orders = [
                        order for order in failed_orders
                        if _is_blocking_repair_order_failure(order)
                    ]
                    if blocking_failed_orders:
                        logger.warning(
                            "parallel_scene_recheck %d: auto-repair round %d rejected by repair audit: %s",
                            scene_idx, current_round + 1, _repair_order_sample(blocking_failed_orders),
                        )
                        return None
                    return updated_texts.get(scene_idx)
                except Exception:
                    return None

            async def handle_review_case_delta_merge(node, context) -> None:
                # Collect recheck deltas and run serialized FBI repair cycles.
                nonlocal current_repair_plan, current_review_case_file, repair_round
                step = node.workflow_step or "review_case_delta_merge"
                await update_step_locked(execution_id, step, "running", db=db)

                if human_review_override:
                    await update_step_locked(
                        execution_id,
                        step,
                        "completed",
                        output={
                            "runtime": "parallel_review",
                            "human_override": True,
                            "content_recheck_skipped": True,
                            "blocking_status": "not_rechecked",
                        },
                        db=db,
                    )
                    return

                def _collect_delta_packets(
                    packets: list[SceneReviewPacket | None],
                ) -> tuple[list[SceneReviewPacket], list[dict]]:
                    collected: list[SceneReviewPacket] = []
                    validator_items: list[dict] = []
                    for packet in packets:
                        if packet is None or packet.unavailable:
                            continue
                        content_blocking: list[dict] = []
                        for violation in [
                            *(packet.blocking_violations or []),
                            *(packet.advisory_violations or []),
                        ]:
                            if not isinstance(violation, dict):
                                continue
                            normalized = normalize_violation_semantics(violation)
                            _annotate_boundary_conflict_sources(
                                normalized,
                                scene_index=packet.scene_index,
                                scene_contract=_scene_contract_map().get(packet.scene_index),
                            )
                            lane = _classify_review_violation_for_commit(normalized)
                            if lane == "validator_error":
                                validator_items.append(normalized)
                            elif _is_hard_review_violation(normalized):
                                normalized["blocks_commit"] = True
                                content_blocking.append(normalized)
                        if content_blocking:
                            delta_packet = packet.model_copy(deep=True)
                            delta_packet.blocking_violations = content_blocking
                            delta_packet.advisory_violations = []
                            collected.append(delta_packet)
                    return collected, validator_items

                def _human_review_scene_index(violations: list[dict], output: dict) -> int:
                    for violation in violations:
                        if not isinstance(violation, dict):
                            continue
                        source_scene = violation.get("source_scene")
                        if isinstance(source_scene, int) and 0 <= source_scene < len(scene_packages):
                            return source_scene
                        source_scenes = violation.get("source_scenes") or []
                        for source in source_scenes:
                            if isinstance(source, int) and 0 <= source < len(scene_packages):
                                return source
                        scene_index = violation.get("scene_index")
                        if isinstance(scene_index, int) and 0 <= scene_index < len(scene_packages):
                            return scene_index
                    for key in ("changed_scenes", "rechecked_scenes"):
                        for scene_index in output.get(key) or []:
                            if isinstance(scene_index, int) and 0 <= scene_index < len(scene_packages):
                                return scene_index
                    return 0

                async def _pause_parallel_review_for_human_review(
                    *,
                    step: str,
                    error_detail: str,
                    output: dict,
                    violations: list[dict] | None = None,
                ) -> None:
                    review_violations = [
                        normalize_violation_semantics(dict(violation))
                        for violation in (
                            violations
                            or output.get("blocking_samples")
                            or output.get("validator_error_samples")
                            or []
                        )
                        if isinstance(violation, dict)
                    ]
                    if not review_violations:
                        review_violations = [{
                            "type": "parallel_review_needs_human_review",
                            "severity": "high",
                            "detail": error_detail,
                            "blocks_commit": True,
                            "suggested_strategy": "manual_review",
                            "repair_scope": "prose_text",
                            "user_visible": True,
                        }]
                    for violation in review_violations:
                        violation["blocks_commit"] = True
                        violation.setdefault("severity", "high")
                        violation.setdefault("suggested_strategy", "manual_review")
                        violation.setdefault("repair_scope", "prose_text")
                        violation.setdefault("user_visible", True)
                    scene_idx = _human_review_scene_index(review_violations, output)
                    scene_texts = _current_scene_texts()
                    scene_map = [
                        {
                            "scene_index": index,
                            "scene_id": str(
                                (
                                    scene_exec.get(index, {}).get("scene_contract")
                                    or scene_packages[index].get("scene_contract", {})
                                    or {}
                                ).get("scene_id")
                                or f"scene_{index + 1}"
                            ),
                        }
                        for index in range(len(scene_packages))
                    ]
                    candidate_text = scene_texts.get(scene_idx, "")
                    approved_scene_texts = {
                        index: text
                        for index, text in scene_texts.items()
                        if index != scene_idx and isinstance(text, str) and text
                    }
                    pkg = scene_exec.get(scene_idx, {}).get("pkg") or scene_packages[scene_idx]
                    pipeline_output = {
                        "runtime": "parallel_review",
                        "error_code": "parallel_review_needs_human_review",
                        "final_violations": review_violations,
                        "attempts": output.get("cycle_history", []),
                        "review_case_delta": output,
                        "candidate_text": candidate_text,
                    }
                    resume_state = {
                        "chapter_number": chapter_number,
                        "pov_character": pov_character,
                        "custom_instructions": custom_instructions,
                        "scene_index": scene_idx,
                        "review_scope": "scene",
                        "candidate_text": candidate_text,
                        "approved_scene_texts": approved_scene_texts,
                        "scene_map": scene_map,
                        "review_version": review_version,
                        "scene_contract": scene_exec.get(scene_idx, {}).get("scene_contract") or {},
                        "scene_contracts": _scene_contract_map(),
                        "chapter_state": chapter_state,
                        "character_cards": pkg.get("character_cards", []),
                        "character_names": pkg.get("character_names", []),
                        "scene_truth_snapshot": pkg.get("scene_truth_snapshot"),
                        "core_facts": pkg.get("core_facts", {}),
                        "word_budget": pkg.get("word_budget"),
                        "parallel_review_breakpoint": True,
                        "human_review_step": step,
                    }
                    await pause_for_human_review_locked(
                        execution_id=execution_id,
                        gen_step=step,
                        check_step=f"parallel_recheck_{scene_idx + 1}",
                        error_detail=error_detail,
                        pipeline_output=pipeline_output,
                        resume_state=resume_state,
                        db=db,
                        review_type="content_review",
                    )

                async def _abort_delta(
                    status: str,
                    error_detail: str,
                    output: dict,
                    violations: list[dict] | None = None,
                ) -> None:
                    if status == "needs_human_review":
                        validator_violations = _validator_only_system_findings(violations)
                        if validator_violations:
                            scene_idx = _human_review_scene_index(
                                validator_violations,
                                output,
                            )
                            candidate_text = _current_scene_texts().get(scene_idx, "")
                            validator_output = {
                                "runtime": "parallel_review",
                                "error_code": validator_violations[0].get(
                                    "type",
                                    "validator_unavailable",
                                ),
                                "final_violations": validator_violations,
                                "review_case_delta": output,
                                "candidate_text": candidate_text,
                            }
                            await _pause_parallel_scene_for_validator_retry(
                                scene_idx=scene_idx,
                                gen_step=step,
                                check_step=f"parallel_recheck_{scene_idx + 1}",
                                error_detail=error_detail,
                                pipeline_output=validator_output,
                                violations=validator_violations,
                                candidate_text=candidate_text,
                                db_session=db,
                            )
                            raise EditorDAGAbort({
                                "content": "",
                                "status": "pending_validator_retry",
                                "error": error_detail,
                            })
                        await _pause_parallel_review_for_human_review(
                            step=step,
                            error_detail=error_detail,
                            output=output,
                            violations=violations,
                        )
                        raise EditorDAGAbort({
                            "content": "",
                            "status": "waiting_review",
                            "error": error_detail,
                        })
                    await update_step_locked(
                        execution_id,
                        step,
                        "failed",
                        output={"runtime": "parallel_review", **output},
                        db=db,
                    )
                    raise EditorDAGAbort({"content": "", "status": status, "error": error_detail})

                def _finding_key(item: dict) -> tuple[str, str, str]:
                    return (
                        str(
                            item.get("issue_id")
                            or item.get("violation_id")
                            or item.get("id")
                            or item.get("metric")
                            or item.get("type")
                            or ""
                        ),
                        str(item.get("target_span") or item.get("detail") or ""),
                        str(
                            item.get("source_scene")
                            if item.get("source_scene") is not None
                            else item.get("scene_index") or ""
                        ),
                    )

                def _merge_packet_findings(
                    target: SceneReviewPacket,
                    incoming: SceneReviewPacket,
                ) -> None:
                    """Merge findings while re-bucketing by canonical enforcement."""
                    merged: dict[tuple[str, str, str], tuple[dict, bool]] = {}
                    for raw in [
                        *(target.blocking_violations or []),
                        *(target.advisory_violations or []),
                        *(incoming.blocking_violations or []),
                        *(incoming.advisory_violations or []),
                    ]:
                        if not isinstance(raw, dict):
                            continue
                        item = normalize_violation_semantics(dict(raw))
                        key = _finding_key(item)
                        is_hard = _is_hard_review_violation(item)
                        previous = merged.get(key)
                        # The canonical hard classification always wins when a
                        # legacy packet placed the same finding in advisory.
                        if previous is None or (is_hard and not previous[1]):
                            merged[key] = (item, is_hard)

                    hard: list[dict] = []
                    advisory: list[dict] = []
                    for item, is_hard in merged.values():
                        if is_hard:
                            item["blocks_commit"] = True
                            hard.append(item)
                        else:
                            item["blocks_commit"] = False
                            item["enforcement"] = "advisory"
                            advisory.append(item)
                    target.blocking_violations = hard
                    target.advisory_violations = advisory

                def _merge_delta_packets(
                    target_packets: list[SceneReviewPacket],
                    incoming_packets: list[SceneReviewPacket],
                ) -> None:
                    by_scene = {packet.scene_index: packet for packet in target_packets}
                    for incoming in incoming_packets:
                        current = by_scene.get(incoming.scene_index)
                        if current is None:
                            current = incoming.model_copy(deep=True)
                            target_packets.append(current)
                            by_scene[current.scene_index] = current
                        else:
                            _merge_packet_findings(current, incoming)

                def _merge_authoritative_packets(
                    incoming_packets: list[SceneReviewPacket],
                ) -> None:
                    for incoming in incoming_packets:
                        scene_idx = incoming.scene_index
                        if not isinstance(scene_idx, int) or not (0 <= scene_idx < len(review_packets)):
                            continue
                        current = review_packets[scene_idx]
                        if current is None:
                            review_packets[scene_idx] = incoming.model_copy(deep=True)
                        else:
                            _merge_packet_findings(current, incoming)

                async def _chapter_skill_recheck_packets(
                    phase: str,
                ) -> list[SceneReviewPacket]:
                    """Run the chapter Skill contract and return repair-ready scene packets."""
                    from app.services.agent_skill_commit_gate import get_agent_skill_commit_gate
                    from app.services.fbi.final_acceptance_delta import (
                        build_final_acceptance_case_delta,
                    )

                    scene_texts = _current_scene_texts()
                    chapter_text = "\n\n".join(scene_texts.values())
                    final_scene_contract = scene_contracts[0] if scene_contracts else {}
                    final_writing_mode = (
                        final_scene_contract.get("writing_mode_profile")
                        if isinstance(final_scene_contract, dict)
                        else {}
                    ) or {}
                    async with async_session() as skill_db:
                        gate_result = await get_agent_skill_commit_gate().evaluate(
                            project_id=str(project_id),
                            db=skill_db,
                            final_text=chapter_text,
                            scene_contract=final_scene_contract,
                            chapter_state=chapter_state,
                            writing_mode_profile=final_writing_mode,
                            style_context=_workflow_style_context,
                            scene_units=[
                                {
                                    "text": text,
                                    "scene_contract": (
                                        scene_contracts[index]
                                        if index < len(scene_contracts)
                                        else {}
                                    ),
                                    "writing_mode_profile": (
                                        scene_contracts[index].get("writing_mode_profile") or {}
                                        if index < len(scene_contracts)
                                        else {}
                                    ),
                                }
                                for index, text in sorted(scene_texts.items())
                            ],
                            allow_llm_repair=False,
                        )
                    trace = gate_result.get("trace") or {}
                    validation = trace.get("initial_validation") or {}
                    failures = [
                        item for item in validation.get("failures") or []
                        if isinstance(item, dict)
                    ]
                    blocking_failures = failures if not gate_result.get("allowed", True) else []
                    actionable = [
                        *blocking_failures,
                        *_validation_advisory_findings(validation),
                    ]
                    logger.info(
                        "L4 skill gate %s: allowed=%s, actionable=%d, samples=%s",
                        phase,
                        gate_result.get("allowed"),
                        len(actionable),
                        [
                            {
                                "validator": item.get("validator"),
                                "metric": item.get("metric"),
                                "blocks_commit": item.get("blocks_commit"),
                            }
                            for item in actionable[:5]
                        ],
                    )
                    if not actionable:
                        return []

                    skill_delta = build_final_acceptance_case_delta(
                        project_id=project_id,
                        chapter_number=chapter_number,
                        final_text=chapter_text,
                        final_gate_result={
                            "allowed": False,
                            "reason": f"l4_skill_gate_{phase}_actionable",
                            "trace": {"initial_validation": {
                                **validation,
                                "passed": False,
                                "failures": actionable,
                            }},
                        },
                        acceptance_round=0,
                        parent_case_id="",
                        scene_texts=scene_texts,
                    )
                    issues = [
                        *(skill_delta.get("skill_failures") or []),
                        *(skill_delta.get("chapter_issues") or []),
                        *(skill_delta.get("scene_issues") or []),
                    ]
                    packets_by_scene: dict[int, SceneReviewPacket] = {}
                    unassigned = 0
                    for issue in issues:
                        if not isinstance(issue, dict):
                            continue
                        scene_idx = issue.get("scene_index")
                        if scene_idx is None:
                            scene_idx = issue.get("source_scene")
                        if not isinstance(scene_idx, int) or not (0 <= scene_idx < len(scene_packages)):
                            unassigned += 1
                            continue
                        normalized = normalize_violation_semantics(issue)
                        normalized["source"] = f"l4_skill_gate_{phase}"
                        if _reuse_locked_scene_validator_result(
                            scene_idx,
                            normalized,
                            validator_retry_resumed=validator_retry_resumed,
                            reviewed_scene_overrides=reviewed_scene_overrides,
                            skip_reviewed_scene_validation=skip_reviewed_scene_validation,
                            reviewed_scene_repair_indexes=reviewed_scene_repair_indexes,
                        ):
                            logger.info(
                                "L4 skill gate %s reused successful validator retry "
                                "for locked scene %d (%s)",
                                phase,
                                scene_idx,
                                normalized.get("type") or normalized.get("metric") or "validator_error",
                            )
                            continue
                        packet = packets_by_scene.setdefault(
                            scene_idx,
                            SceneReviewPacket(
                                scene_index=scene_idx,
                                quality_gate_report={},
                                blocking_violations=[],
                                advisory_violations=[],
                                repairability="auto_fixable",
                            ),
                        )
                        if _is_hard_review_violation(normalized):
                            normalized["blocks_commit"] = True
                            packet.blocking_violations.append(normalized)
                        else:
                            normalized["blocks_commit"] = False
                            normalized["enforcement"] = "advisory"
                            packet.advisory_violations.append(normalized)
                    logger.info(
                        "L4 skill gate %s packets=%s, unassigned=%d",
                        phase,
                        {index: len(packet.blocking_violations) + len(packet.advisory_violations)
                         for index, packet in packets_by_scene.items()},
                        unassigned,
                    )
                    return list(packets_by_scene.values())

                delta_packets, validator_errors = _collect_delta_packets(review_packets)
                cycle_history: list[dict] = []
                latest_order_samples: list[dict] = []
                latest_changed_scenes: list[int] = []
                attempted_hard_finding_keys: set[tuple[str, str, str]] = set()

                # L3 only rechecks scene QualityGate findings.  Run the chapter
                # Skill contract before L4, and merge the findings back into the
                # authoritative packets so hard/advisory exhaustion uses one source.
                try:
                    initial_skill_packets = await _chapter_skill_recheck_packets("before_repair")
                    skill_deltas, skill_validator_errors = _collect_delta_packets(
                        initial_skill_packets
                    )
                    _merge_delta_packets(delta_packets, skill_deltas)
                    validator_errors.extend(skill_validator_errors)
                    _merge_authoritative_packets(initial_skill_packets)
                except Exception as skill_exc:
                    logger.warning("L4 skill gate before-repair recheck failed: %s", skill_exc)

                if validator_errors:
                    error_detail = (
                        f"recheck delta merge: {len(validator_errors)} validator error(s), "
                        "cannot reliably verify content quality"
                    )
                    await _abort_delta(
                        "needs_human_review",
                        error_detail,
                        {
                            "validator_errors": len(validator_errors),
                            "validator_error_samples": _violation_sample(validator_errors[:5]),
                        },
                        violations=validator_errors,
                    )

                if not delta_packets and repair_failed_deltas:
                    # 修复（循环 #17 D17-3）：L4 快速通道——L3 复检已确认无 content_blocking
                    # 的场景，不回填 repair_failed_deltas。
                    # 原逻辑：只要 L2 有 failed orders（recoverable_delta），就回填到 delta_packets
                    # 触发 L4 完整修复循环（~6.7min）。
                    # 问题：L3 复检已经验证了场景文本质量，如果 L3 对该场景返回无 content_blocking，
                    # 说明 L2 的 recoverable 失败已被其他订单修复或问题不存在，L4 修复循环是浪费。
                    # 修复：只回填 L3 复检发现有 content_blocking 的场景的 repair_failed_deltas。
                    # 大局观评估（铁律 12 五问）：
                    #   1. 能解决目标问题吗？是——L3 已验证 clean 的场景跳过 L4 修复，省 ~6.7min
                    #   2. 会引入新问题吗？否——L3 复检是独立的质量验证，其结果可信
                    #   3. 耗时成本？降低——L4 从 6.7min 降到 ~0.1min（快速跳过路径）
                    #   4. 更低成本的替代方案？否——这是最直接的优化
                    #   5. 对下一轮修复的影响？正面——减少不必要的 L4 修复，避免引入新问题
                    scenes_with_l3_blocking: set[int] = set()
                    for packet in review_packets:
                        if packet is None or packet.unavailable:
                            continue
                        for violation in packet.blocking_violations or []:
                            if not isinstance(violation, dict):
                                continue
                            normalized = normalize_violation_semantics(violation)
                            lane = _classify_review_violation_for_commit(normalized)
                            if lane == "content_blocking":
                                scenes_with_l3_blocking.add(packet.scene_index)
                                break
                    filtered_repair_failed_deltas = {
                        scene_idx: items
                        for scene_idx, items in repair_failed_deltas.items()
                        if scene_idx in scenes_with_l3_blocking
                    }
                    if filtered_repair_failed_deltas:
                        delta_packets.extend(_repair_failed_delta_packets(
                            filtered_repair_failed_deltas,
                            _current_scene_texts(),
                        ))
                    else:
                        # 所有 L2 失败的场景在 L3 复检中都已 clean，跳过 L4 修复
                        await update_step_locked(
                            execution_id,
                            step,
                            "completed",
                            output={
                                "runtime": "parallel_review",
                                "delta_blocking": 0,
                                "repair_failed_delta_count": sum(
                                    len(items) for items in repair_failed_deltas.values()
                                ),
                                "repair_failed_delta_skipped_by_l3_clean": True,
                                "repair_cycle_attempted": False,
                            },
                            db=db,
                        )
                        return

                if not delta_packets:
                    await update_step_locked(
                        execution_id,
                        step,
                        "completed",
                        output={
                            "runtime": "parallel_review",
                            "delta_blocking": 0,
                            "repair_failed_delta_count": sum(
                                len(items) for items in repair_failed_deltas.values()
                            ),
                            "repair_cycle_attempted": False,
                        },
                        db=db,
                    )
                    return

                from app.services.review_case_file import ReviewCaseFileBuilder

                try:
                    while delta_packets:
                        hard_delta_count = sum(
                            len(packet.blocking_violations)
                            for packet in delta_packets
                        )
                        advisory_delta_count = sum(
                            len(packet.advisory_violations)
                            for packet in delta_packets
                        )
                        delta_count = hard_delta_count + advisory_delta_count
                        current_hard_findings = [
                            violation
                            for packet in delta_packets
                            for violation in packet.blocking_violations or []
                            if isinstance(violation, dict)
                            and _is_hard_review_violation(violation)
                        ]
                        novel_hard_findings = [
                            violation
                            for violation in current_hard_findings
                            if _finding_key(violation) not in attempted_hard_finding_keys
                        ]
                        novel_delta_extension = _allow_novel_delta_repair_cycle(
                            repair_round=repair_round,
                            current_keys={
                                _finding_key(violation)
                                for violation in current_hard_findings
                            },
                            attempted_keys=attempted_hard_finding_keys,
                            base_limit=MAX_CHAPTER_REPAIR_ROUNDS,
                            total_limit=MAX_CHAPTER_REPAIR_ROUNDS_WITH_NOVEL_DELTA,
                        )
                        if (
                            repair_round >= MAX_CHAPTER_REPAIR_ROUNDS
                            and not novel_delta_extension
                        ):
                            # enforcement 分级：L4 修复预算耗尽后按 enforcement 分流
                            # - 剩余 hard_blocking → waiting_review（人工复核）
                            # - 只剩 advisory → 标记 advisory_exhausted，放行继续 L5/L6 提交
                            all_remaining = [
                                violation
                                for packet in delta_packets
                                for violation in [
                                    *(packet.blocking_violations or []),
                                    *(packet.advisory_violations or []),
                                ]
                                if isinstance(violation, dict)
                            ]
                            hard_blocking_remaining = [
                                v for v in all_remaining
                                if _is_hard_review_violation(v)
                            ]
                            advisory_remaining = [
                                v for v in all_remaining
                                if not _is_hard_review_violation(v)
                            ]
                            total_repair_attempts = 1 + repair_round  # L2 + L4
                            exhausted_advisories = _mark_advisories_exhausted(
                                review_packets,
                                repair_attempts=total_repair_attempts,
                            )
                            if exhausted_advisories:
                                advisory_remaining = exhausted_advisories

                            if not hard_blocking_remaining:
                                # 只剩 advisory：标记 advisory_exhausted，放行继续提交
                                logger.info(
                                    "recheck delta merge: %d advisory issue(s) remain after %d repair cycle(s), "
                                    "marking advisory_exhausted and allowing commit (enforcement=advisory)",
                                    len(advisory_remaining), total_repair_attempts,
                                )
                                # 记录 advisory_exhausted 到 cycle_history 供最终报告使用
                                cycle_history.append({
                                    "repair_round": repair_round,
                                    "repair_attempts_total": total_repair_attempts,
                                    "advisory_exhausted": True,
                                    "advisory_remaining": len(advisory_remaining),
                                    "hard_blocking_remaining": 0,
                                    "advisory_samples": _violation_sample(advisory_remaining[:5]),
                                })
                                # 更新 step 状态为 completed（advisory_exhausted 允许放行）
                                await update_step_locked(
                                    execution_id,
                                    step,
                                    "completed",
                                    output={
                                        "runtime": "parallel_review",
                                        "delta_blocking": 0,  # hard_blocking 已全部解决
                                        "advisory_exhausted": len(advisory_remaining),
                                        "repair_failed_delta_count": sum(
                                            len(items) for items in repair_failed_deltas.values()
                                        ),
                                        "repair_cycle_attempted": True,
                                        "repair_round": repair_round,
                                        "repair_attempts_total": total_repair_attempts,
                                        "max_repair_cycles": 2,
                                        "status": getattr(current_repair_plan, "status", ""),
                                        "orders": len(getattr(current_repair_plan, "orders", []) or []),
                                        "changed_scenes": changed_scenes,
                                        "remaining_blocking": 0,
                                        "cycle_history": cycle_history,
                                        "order_samples": latest_order_samples,
                                    },
                                    db=db,
                                )
                                break  # 退出 while 循环，继续 L5/L6 提交

                            # 仍有 hard_blocking：进入人工复核
                            error_detail = (
                                f"recheck delta merge: {len(hard_blocking_remaining)} hard blocking issue(s) remain "
                                f"after {total_repair_attempts} total repair attempt(s), needs human review "
                                f"(advisory_exhausted: {len(advisory_remaining)})"
                            )
                            await _abort_delta(
                                "needs_human_review",
                                error_detail,
                                {
                                    "delta_blocking": len(hard_blocking_remaining),
                                    "advisory_exhausted": len(advisory_remaining),
                                    "repair_cycle_attempted": bool(cycle_history),
                                    "repair_round": repair_round,
                                    "repair_attempts_total": total_repair_attempts,
                                    "max_repair_cycles": 2,
                                    "cycle_history": cycle_history,
                                    "blocking_samples": _violation_sample(hard_blocking_remaining[:5]),
                                    "advisory_samples": _violation_sample(advisory_remaining[:5]),
                                    "order_samples": latest_order_samples,
                                    "changed_scenes": latest_changed_scenes,
                                },
                                violations=hard_blocking_remaining,
                            )

                        if novel_delta_extension:
                            logger.info(
                                "recheck delta merge: granting one bounded repair cycle for %d "
                                "newly discovered hard finding(s)",
                                len(novel_hard_findings),
                            )

                        attempted_hard_finding_keys.update(
                            _finding_key(violation)
                            for violation in current_hard_findings
                        )

                        repair_round += 1
                        current_cycle = {
                            "repair_round": repair_round,
                            "repair_attempts_total": 1 + repair_round,
                            "delta_blocking": hard_delta_count,
                            "delta_advisory": advisory_delta_count,
                            "source_scenes": [packet.scene_index for packet in delta_packets],
                            "novel_delta_extension": novel_delta_extension,
                            "novel_hard_findings": len(novel_hard_findings),
                        }
                        cycle_repair_packets = delta_packets
                        if novel_delta_extension:
                            novel_keys = {
                                _finding_key(violation)
                                for violation in novel_hard_findings
                            }
                            cycle_repair_packets = []
                            for packet in delta_packets:
                                novel_items = [
                                    violation
                                    for violation in packet.blocking_violations or []
                                    if isinstance(violation, dict)
                                    and _finding_key(violation) in novel_keys
                                ]
                                if not novel_items:
                                    continue
                                novel_packet = packet.model_copy(deep=True)
                                novel_packet.blocking_violations = novel_items
                                novel_packet.advisory_violations = []
                                cycle_repair_packets.append(novel_packet)
                        delta_case_file = ReviewCaseFileBuilder().build(
                            project_id=project_id,
                            chapter_number=chapter_number,
                            draft_text="\n\n".join(_current_scene_texts().values()),
                            scene_packets=cycle_repair_packets,
                            created_from="recheck",
                            review_trace={
                                "created_from": "parallel_recheck",
                                "repair_round": repair_round,
                                "source_scenes": [packet.scene_index for packet in delta_packets],
                                "repair_failed_delta_scenes": sorted(repair_failed_deltas),
                            },
                        )
                        cycle_case = ChapterReviewCase(
                            project_id=project_id,
                            chapter_number=chapter_number,
                            chapter_initial_state=chapter_state,
                            scene_packets=cycle_repair_packets,
                            review_round=repair_round,
                            review_case_file={},
                            case_file_deltas=[delta_case_file.model_dump()],
                        )

                        intake_svc = FBIChapterCaseIntakeService()
                        validated_cycle_case = await intake_svc.intake(cycle_case)
                        planner = FBIChapterRepairPlanner()
                        cycle_plan = await planner.plan(validated_cycle_case)
                        # 方案14 Part C：保护对共享可变状态的写入
                        async with scene_state_lock:
                            current_repair_plan = cycle_plan
                            current_cycle.update({
                                "status": cycle_plan.status,
                                "orders": len(cycle_plan.orders),
                                "repair_type_counts": _repair_type_counts(cycle_plan.orders),
                            })

                        changed_scenes: list[int] = []
                        blocking_failed_orders: list = []
                        recoverable_failed_orders: list = []
                        cycle_recheck_scenes = {packet.scene_index for packet in delta_packets}
                        if not cycle_plan.is_clean():
                            scene_texts = _current_scene_texts()
                            executor = ChapterRepairExecutor()
                            updated_texts, updated_plan = await executor.execute(
                                cycle_plan,
                                scene_texts,
                                context={
                                    "project_id": project_id,
                                    "chapter_number": chapter_number,
                                    "scene_contracts": _scene_contract_map(),
                                    "scene_truth_snapshots": _scene_truth_snapshot_map(),
                                    "chapter_state": chapter_state,
                                    "chapter_scene_context": _chapter_scene_context(scene_texts),
                                    "db_session_factory": async_session,
                                    "allow_legacy_fbi_executor": False,
                                    "cancellation_check": stop_if_cancelled,
                                    **_style_repair_context_snapshot(),
                                },
                                case_file={
                                    "all_scene_texts": scene_texts,
                                    "all_scene_contracts": _scene_contract_map(),
                                    "chapter_state": chapter_state,
                                    "review_packets": review_packets,
                                    "review_case_file": current_review_case_file or {},
                                    "case_file_delta": delta_case_file.model_dump(),
                                    "repair_plan": cycle_plan,
                                    "scene_truth_snapshots": _scene_truth_snapshot_map(),
                                    "allow_legacy_fbi_executor": False,
                                    "style_context": _workflow_style_context,
                                    "style_profile": _workflow_style_context,
                                },
                            )
                            # 方案14 Part C：保护对共享可变状态的 read-modify-write 操作
                            async with scene_state_lock:
                                current_repair_plan = updated_plan
                                failed_orders = [
                                    order for order in updated_plan.orders
                                    if _repair_order_failure_disposition(order) != "accepted"
                                ]
                                blocking_failed_orders = [
                                    order for order in failed_orders
                                    if _is_blocking_repair_order_failure(order)
                                ]
                                recoverable_failed_orders = [
                                    order for order in failed_orders
                                    if _repair_order_failure_disposition(order) == "recoverable_delta"
                                ]
                                for order in recoverable_failed_orders:
                                    disposition = _repair_order_failure_disposition(order)
                                    order.status = "skipped"
                                    order.repair_audit = {
                                        **(order.repair_audit or {}),
                                        "disposition": disposition,
                                        "continue_to_recheck": True,
                                    }
                                    for target_scene in sorted(_repair_affected_scenes(order)):
                                        cycle_recheck_scenes.add(target_scene)
                                        scenes_requiring_recheck.add(target_scene)
                                        repair_failed_deltas.setdefault(target_scene, []).extend(
                                            _repair_failure_delta_items(
                                                order,
                                                disposition,
                                                updated_texts,
                                            )
                                        )
                                        if target_scene not in repaired_scene_texts:
                                            base_text = scene_texts.get(target_scene, "")
                                            if base_text:
                                                repaired_scene_texts[target_scene] = base_text
                                for order in updated_plan.orders:
                                    audit = getattr(order, "repair_audit", {}) or {}
                                    if (
                                        getattr(order, "repair_type", "") == "contract_patch"
                                        and _repair_order_failure_disposition(order) == "accepted"
                                        and audit.get("contract_changed")
                                    ):
                                        for target_scene in sorted(_repair_affected_scenes(order)):
                                            cycle_recheck_scenes.add(target_scene)
                                            scenes_requiring_recheck.add(target_scene)
                                            if target_scene not in repaired_scene_texts:
                                                base_text = scene_texts.get(target_scene, "")
                                                if base_text:
                                                    repaired_scene_texts[target_scene] = base_text
                                for updated_idx, repaired_text in updated_texts.items():
                                    original_text = scene_texts.get(updated_idx, "")
                                    if repaired_text and repaired_text != original_text:
                                        repaired_scene_texts[updated_idx] = repaired_text
                                        scene_exec.setdefault(updated_idx, {})["generated_text"] = repaired_text
                                        changed_scenes.append(updated_idx)
                                        cycle_recheck_scenes.add(updated_idx)

                        latest_changed_scenes = changed_scenes
                        latest_order_samples = _repair_order_sample(getattr(current_repair_plan, "orders", []) or [])
                        current_cycle.update({
                            "changed_scenes": changed_scenes,
                            "blocking_failed_orders": len(blocking_failed_orders),
                            "recoverable_failed_orders": len(recoverable_failed_orders),
                            "order_samples": latest_order_samples,
                        })

                        if blocking_failed_orders:
                            error_detail = (
                                f"recheck delta merge: {len(blocking_failed_orders)} blocking repair order(s) failed"
                            )
                            blocking_failure_violations = [
                                item
                                for order in blocking_failed_orders
                                for item in _repair_failure_delta_items(
                                    order,
                                    _repair_order_failure_disposition(order),
                                    updated_texts,
                                )
                                if isinstance(item, dict)
                            ]
                            # 通用修复：若所有失败订单的违规都已降级为非 blocking
                            # （stale target_span），则不需要人工审核，继续工作流。
                            # 根因：_repair_failure_delta_items 已对 target_span 不在
                            #   当前正文中的违规降级为 is_stale + blocks_commit=False。
                            #   如果所有违规都是 stale，说明 FBI 修复虽 audit 失败但
                            #   实际已修改了问题文本，原违规不再适用，不应阻塞工作流。
                            remaining_blocking = [
                                v for v in blocking_failure_violations
                                if isinstance(v, dict) and v.get("blocks_commit")
                            ]
                            if not remaining_blocking:
                                logger.info(
                                    "recheck delta merge: all %d failed order violation(s) "
                                    "have stale target_span, skipping human review",
                                    len(blocking_failure_violations),
                                )
                            else:
                                # A failed order is not yet proof that the
                                # current text is unrepairable. Recheck the
                                # unchanged owner scene, then let the bounded
                                # convergence loop decide from authoritative
                                # findings. Immediate human review here used to
                                # hide newly discovered, auto-fixable issues.
                                logger.info(
                                    "%s; continuing to authoritative recheck",
                                    error_detail,
                                )
                                for order in blocking_failed_orders:
                                    disposition = _repair_order_failure_disposition(order)
                                    for target_scene in sorted(_repair_affected_scenes(order)):
                                        cycle_recheck_scenes.add(target_scene)
                                        scenes_requiring_recheck.add(target_scene)
                                        repair_failed_deltas.setdefault(target_scene, []).extend(
                                            _repair_failure_delta_items(
                                                order,
                                                disposition,
                                                updated_texts,
                                            )
                                        )

                        unresolved_blocking: list[dict] = []
                        unresolved_advisory: list[dict] = []
                        next_delta_packets: list[SceneReviewPacket] = []
                        post_repair_validator_errors: list[dict] = []
                        scenes_to_recheck = sorted(cycle_recheck_scenes)
                        # 通用修复（循环 #14 时间优化）：L4 recheck 串行→并行
                        # 根因：原 for 循环逐场景调用 worker.review()，每场景 ~120s LLM 调用，
                        #   N 场景 = N×120s。L7-L10 已用 asyncio.gather 并行化（见
                        #   final_delta_repair_runtime.py:438-441），L4 应对齐。
                        # 大局观：场景间复检无依赖关系，可安全并行。结果按 scene_idx
                        #   顺序处理，逻辑与串行完全等价。零质量风险。
                        # 预期收益：N 场景 N×120s → max(120s)，省 ~6min（3 场景）。
                        async def _recheck_one_scene(idx: int):
                            worker = SceneReviewWorker(scene_index=idx)
                            context = {
                                "generated_text": _current_scene_texts().get(idx, ""),
                                "scene_contract": scene_exec.get(idx, {}).get("scene_contract") or {},
                                "current_state": chapter_state,
                                "core_facts": scene_exec.get(idx, {}).get("pkg", {}).get("core_facts", {}),
                                "character_names": scene_exec.get(idx, {}).get("pkg", {}).get("character_names", []),
                                "character_cards": scene_exec.get(idx, {}).get("pkg", {}).get("character_cards", []),
                                "project_id": project_id,
                                "chapter_number": chapter_number,
                                "scene_index": idx,
                                "project": project,
                                "db_session_factory": async_session,
                            }
                            prior_packet = review_packets[idx] if idx < len(review_packets) else None
                            if (
                                prior_packet is not None
                                and _repair_plan_preserves_review_claims(current_repair_plan, idx)
                            ):
                                context["precomputed_review_packet"] = prior_packet
                                context["quality_level"] = "fast"
                            return idx, await worker.review(context)

                        recheck_pairs = await asyncio.gather(
                            *[_recheck_one_scene(idx) for idx in scenes_to_recheck],
                            return_exceptions=True,
                        )
                        for pair in recheck_pairs:
                            if isinstance(pair, Exception):
                                # 单场景复检异常不应中断整体流程，记录后跳过
                                continue
                            scene_idx, packet = pair
                            review_packets[scene_idx] = packet
                            collected_packets, collected_validator_errors = _collect_delta_packets([packet])
                            post_repair_validator_errors.extend(collected_validator_errors)
                            if collected_packets:
                                delta_packet = collected_packets[0]
                                content_blocking = list(delta_packet.blocking_violations or [])
                                content_advisory = list(delta_packet.advisory_violations or [])
                                unresolved_blocking.extend(content_blocking)
                                unresolved_advisory.extend(content_advisory)
                            else:
                                content_blocking = []
                                content_advisory = []
                            if content_blocking:
                                _inject_repair_failure_memory(
                                    content_blocking,
                                    scene_idx,
                                )
                            if content_blocking or content_advisory:
                                next_delta_packets.append(delta_packet)

                        # SceneReviewWorker does not evaluate chapter-level Skill
                        # metrics.  Re-run the exact Skill contract after the L4
                        # edits so executor success cannot bypass the 0.5/1000 and
                        # consecutive-dash hard gates. Advisories stay visible in
                        # authoritative review packets but do not trigger repairs.
                        try:
                            post_skill_packets = await _chapter_skill_recheck_packets(
                                f"after_repair_{repair_round}"
                            )
                            post_skill_deltas, post_skill_validator_errors = _collect_delta_packets(
                                post_skill_packets
                            )
                            _merge_delta_packets(next_delta_packets, post_skill_deltas)
                            post_repair_validator_errors.extend(post_skill_validator_errors)
                            _merge_authoritative_packets(post_skill_packets)
                            unresolved_blocking = [
                                violation
                                for packet in next_delta_packets
                                for violation in packet.blocking_violations or []
                                if isinstance(violation, dict)
                            ]
                            unresolved_advisory = [
                                violation
                                for packet in next_delta_packets
                                for violation in packet.advisory_violations or []
                                if isinstance(violation, dict)
                            ]
                        except Exception as skill_exc:
                            logger.warning(
                                "L4 skill gate post-repair recheck failed: %s",
                                skill_exc,
                            )

                        current_cycle.update({
                            "rechecked_scenes": scenes_to_recheck,
                            "remaining_blocking": len(unresolved_blocking),
                            "remaining_advisory": len(unresolved_advisory),
                            "validator_errors": len(post_repair_validator_errors),
                        })
                        cycle_history.append(current_cycle)

                        if post_repair_validator_errors:
                            error_detail = (
                                f"recheck delta merge: {len(post_repair_validator_errors)} validator error(s), "
                                "cannot reliably verify content quality"
                            )
                            await _abort_delta(
                                "needs_human_review",
                                error_detail,
                                {
                                    "delta_blocking": delta_count,
                                    "repair_failed_delta_count": sum(
                                        len(items) for items in repair_failed_deltas.values()
                                    ),
                                    "repair_cycle_attempted": True,
                                    "repair_round": repair_round,
                                    "changed_scenes": changed_scenes,
                                    "validator_errors": len(post_repair_validator_errors),
                                    "validator_error_samples": _violation_sample(post_repair_validator_errors[:5]),
                                    "order_samples": latest_order_samples,
                                    "cycle_history": cycle_history,
                                },
                                violations=post_repair_validator_errors,
                            )

                        if next_delta_packets:
                            delta_packets = next_delta_packets
                            continue

                        await update_step_locked(
                            execution_id,
                            step,
                            "completed",
                            output={
                                "runtime": "parallel_review",
                                "delta_blocking": delta_count,
                                "repair_failed_delta_count": sum(
                                    len(items) for items in repair_failed_deltas.values()
                                ),
                                "repair_cycle_attempted": True,
                                "repair_round": repair_round,
                                "status": getattr(current_repair_plan, "status", ""),
                                "orders": len(getattr(current_repair_plan, "orders", []) or []),
                                "changed_scenes": changed_scenes,
                                "remaining_blocking": 0,
                                "cycle_history": cycle_history,
                                "order_samples": latest_order_samples,
                            },
                            db=db,
                        )
                        return
                except EditorDAGAbort:
                    raise
                except Exception as exc:
                    logger.warning("review_case_delta_merge failed: %s", exc, exc_info=True)
                    await update_step_locked(
                        execution_id,
                        step,
                        "failed",
                        output={"runtime": "parallel_review", "error": str(exc)},
                        db=db,
                    )
                    raise EditorDAGAbort({"content": "", "status": "failed", "error": str(exc)})

            async def handle_ordered_state_commit(node, context) -> None:
                """有序状态提交：严格按场景顺序提交."""
                nonlocal previous_scene_ending, previous_scenes_summary
                scene_idx = int(node.scene_index or 0)
                commit_step = f"ordered_commit_{scene_idx + 1}"

                data = scene_exec.get(scene_idx, {})
                pkg = data.get("pkg") or scene_packages[scene_idx]
                generated_text = data.get("generated_text", "")
                scene_contract = data.get("scene_contract")
                result = data.get("result", {})

                if not generated_text:
                    await update_step_locked(execution_id, commit_step, "skipped", db=db)
                    return

                await update_step_locked(execution_id, commit_step, "running", db=db)

                # Collect content in order
                all_content.append(generated_text)

                # A validated review resume must not re-run scene post-processing
                # while reconstructing the DAG. Run it exactly once here, against
                # the final text that is about to be committed.
                if result.get("postprocess_deferred"):
                    deferred_effects = dict(result.get("pending_effects") or {})
                    postprocess = await run_scene_postprocess_fanout(
                        project_id=project_id,
                        chapter_number=chapter_number,
                        generated_text=generated_text,
                        story_state=story_state,
                        scene_package=pkg,
                        report=result.get("consistency_report") or {},
                        scene_contract=scene_contract if isinstance(scene_contract, dict) else {},
                    )
                    result["detail_seeds_count"] = len(postprocess.get("seeds") or [])
                    result["state_patch"] = postprocess.get("state_patch") or {}
                    merged_effects = dict(postprocess.get("pending_effects") or {})
                    merged_effects.update(deferred_effects)
                    result["pending_effects"] = merged_effects
                    result["review_extraction"] = postprocess.get("review_extraction") or {}
                    result["postprocess_deferred"] = False

                # Apply state patch
                if result.get("state_patch"):
                    all_patches.append(result["state_patch"])

                scene_facts, _fact_source = await _prepare_scene_facts_for_commit(
                    chapter_number=chapter_number,
                    generated_text=generated_text,
                    result=result,
                    character_cards=pkg.get("character_cards", []),
                    scene_contract=scene_contract,
                )
                result["writer_scene_facts"] = scene_facts
                from app.services.chapter_output_effects import (
                    build_scene_output_effects,
                    merge_scene_facts_into_chapter_state,
                )

                merge_scene_facts_into_chapter_state(
                    chapter_state,
                    scene_facts,
                    generated_text=generated_text,
                    character_cards=pkg.get("character_cards", []),
                    scene_contract=scene_contract,
                )
                scene_ending = scene_facts.get("scene_ending", "")
                if scene_ending:
                    previous_scene_ending = scene_ending
                if generated_text:
                    summary_text = generated_text[:800] if len(generated_text) > 800 else generated_text
                    previous_scenes_summary = f"Scene {scene_idx + 1} summary: {summary_text}\n\n" + previous_scenes_summary
                    if len(previous_scenes_summary) > 2000:
                        previous_scenes_summary = previous_scenes_summary[:2000]

                packet = review_packets[scene_idx] if scene_idx < len(review_packets) else None
                base_effects = dict(result.get("pending_effects", {}) or {})
                chapter_writer_reports = chapter_writer_quality_reports.get(scene_idx)
                if chapter_writer_reports:
                    persisted_reports = dict(base_effects.get("experience_quality_reports") or {})
                    persisted_reports.update(chapter_writer_reports)
                    base_effects["experience_quality_reports"] = persisted_reports
                pending_scene_effect = {
                    "scene_index": scene_idx,
                    "generated_text": generated_text,
                    "effects": build_scene_output_effects(
                        project_id=project_id,
                        chapter_number=chapter_number,
                        scene_index=scene_idx,
                        generated_text=generated_text,
                        scene_contract=scene_contract,
                        scene_facts=scene_facts,
                        base_effects=base_effects,
                        quality_gate_report=getattr(packet, "quality_gate_report", {}) if packet else {},
                        review_packet=packet,
                    ),
                }
                pending_scene_effects.append(pending_scene_effect)

                await update_step_locked(
                    execution_id,
                    commit_step,
                    "completed" if _fact_source == "writer_scene_facts" else "degraded",
                                          output={
                                              "runtime": "parallel_review",
                                              "fact_source": _fact_source,
                                              "facts_count": len(scene_facts.get("established_facts", [])),
                                              "character_states": len(chapter_state.get("character_states", {})),
                                              "pending_scene_effect": pending_scene_effect,
                                          }, db=db)
                return {"pending_scene_effect": pending_scene_effect}

            editor_scene_handlers = {
                        # safe mode handlers (kept for fallback)
                        "scene_prepare": handle_scene_prepare,
                        "scene_generate": handle_scene_generate,
                        "scene_quality_fanout": handle_scene_quality,
                        "scene_post_extract_fanout": handle_scene_post_extract,
                        "scene_accept": handle_scene_accept,
                        "scene_state_commit": handle_scene_state_commit,
                        # parallel_review mode handlers
                        "parallel_scene_review": handle_parallel_scene_review,
                        "chapter_review": handle_chapter_level_review,
                        "fbi_chapter_case_intake": handle_fbi_chapter_case_intake,
                        "parallel_scene_repair": handle_parallel_scene_repair,
                        "parallel_scene_recheck": handle_parallel_scene_recheck,
                        "review_case_delta_merge": handle_review_case_delta_merge,
                        "ordered_state_commit": handle_ordered_state_commit,
                    }
            try:
                if resume_existing_dag:
                    dag_report = await editor_scene_runtime.resume_from(
                        execution_id,
                        editor_scene_handlers,
                    )
                else:
                    dag_report = await editor_scene_runtime.run(editor_scene_handlers)
            except EditorDAGAbort as abort:
                # ---- Workflow correct termination on abort ----
                abort_result = abort.result or {}
                abort_status = abort_result.get("status", "failed")
                abort_error = abort_result.get("error", "editor DAG execution aborted")

                # Review and validator-retry handlers persist their own checkpoint
                # before aborting the DAG.  Do not overwrite that resumable state.
                wf_status = _workflow_status_after_dag_abort(str(abort_status))
                if wf_status is None:
                    return abort_result

                # Complete the workflow execution with proper status
                await _complete_workflow_execution(
                    execution_id, wf_status,
                    error=abort_error, db=db,
                )
                return abort_result
            logger.info(
                "Editor generation DAG runtime completed",
                extra={
                    "execution_id": execution_id,
                    "executed_nodes": len(dag_report.executed_nodes),
                    "max_parallel_width": dag_report.max_parallel_width,
                    "layer_widths": dag_report.layer_widths,
                },
            )
            if dag_report.failed_nodes:
                failure_summary = "; ".join(
                    f"{item.get('node_id')}: {item.get('error_type') or 'error'} "
                    f"{item.get('error') or ''}".strip()
                    for item in dag_report.failed_nodes[:8]
                )
                error_detail = (
                    "editor generation DAG has hard failed nodes; chapter commit aborted: "
                    + failure_summary
                )
                await _complete_workflow_execution(
                    execution_id,
                    "failed",
                    error=error_detail,
                    db=db,
                )
                return {
                    "content": "",
                    "status": "failed",
                    "error": error_detail,
                }
            if _trace_id:
                try:
                    _trace_collector.record_layer(
                        _trace_id,
                        "editor_generation_dag_runtime",
                        "ok",
                        detail=f"nodes={len(dag_report.executed_nodes)} max_parallel={dag_report.max_parallel_width}",
                    )
                except Exception:
                    pass

            if await stop_if_cancelled():
                return {"content": "", "status": "cancelled"}
            await _update_workflow_step(execution_id, "fact_extraction", "completed",
                                        output={"facts_count": len(chapter_state.get("established_facts", []))}, db=db)

            # A redundant parallel review worker may time out while another
            # review path succeeds. Assemble from the canonical scene results;
            # ordered commit callbacks are not guaranteed to run in that case.
            final_scene_texts = _current_scene_texts()
            full_content = "\n\n".join(
                final_scene_texts.get(index, "")
                for index in range(len(scene_packages))
            )

            if await stop_if_cancelled():
                return {"content": "", "status": "cancelled"}
            await _update_workflow_step(execution_id, "style_polish", "running", db=db)
            from app.skills.style_polish import StylePolishSkill
            active_style = _workflow_style_context
            polish_skill = StylePolishSkill()
            polish_result = polish_skill.polish(
                full_content,
                style_features=active_style.get("style_features") if active_style else None,
                style_embedding=active_style.get("style_embedding") if active_style else None,
                persona_card=active_style.get("persona_card") if active_style else None,
            )
            polish_result_data = _style_polish_output(polish_result)
            if polish_result["has_issues"]:
                polish_result_data["applied"] = False
                polish_result_data["emitted_as_review_issue"] = True
                polish_result_data["disabled_reason"] = "style_polish_is_review_source_not_final_modifier"
            if polish_result.get("requires_human_review"):
                polish_result_data["auto_repair"] = {
                    "attempted": False,
                    "accepted": False,
                    "disabled": True,
                    "reason": "review_repair_separation_final_gate_validation_only",
                }
            await _update_workflow_step(
                execution_id,
                "style_polish",
                "completed",
                output=polish_result_data,
                db=db,
            )

            full_content = re.sub(r'^#{1,6}\s+.*$', '', full_content, flags=re.MULTILINE)
            full_content = re.sub(r'^Scene\\d+[::]\\s*.*$', '', full_content, flags=re.MULTILINE)
            full_content = re.sub(r'^Chapter[\\d]+Scene[::]\\s*.*$', '', full_content, flags=re.MULTILINE)
            full_content = re.sub(r'\n{3,}', '\n\n', full_content)
            full_content = full_content.strip()
            full_content = ensure_complete_sentence_ending(full_content)

            await _update_workflow_step(execution_id, "final_acceptance", "running", db=db)
            final_acceptance_started = time.perf_counter()
            final_acceptance_phase_events: list[dict] = []
            final_acceptance_phase_totals: dict[str, int] = {}

            async def _record_final_acceptance_phase(
                phase: str,
                started: float,
                **details,
            ) -> None:
                duration_ms = int((time.perf_counter() - started) * 1000)
                event = {
                    "phase": phase,
                    "duration_ms": duration_ms,
                    **{key: value for key, value in details.items() if value is not None},
                }
                final_acceptance_phase_events.append(event)
                final_acceptance_phase_totals[phase] = (
                    int(final_acceptance_phase_totals.get(phase, 0)) + duration_ms
                )
                await _update_workflow_step(
                    execution_id,
                    "final_acceptance",
                    "running",
                    output={
                        "current_phase": phase,
                        "phase_timing_ms": final_acceptance_phase_totals,
                        "phase_timing_events": final_acceptance_phase_events[-20:],
                    },
                    db=db,
                )

            final_scene_contract = scene_contracts[0] if scene_contracts else {}
            final_writing_mode = (
                final_scene_contract.get("writing_mode_profile")
                if isinstance(final_scene_contract, dict)
                else {}
            ) or {}
            from app.services.fbi.final_acceptance import (
                get_fbi_final_acceptance_service,
            )

            final_acceptance_service = get_fbi_final_acceptance_service()

            async def _evaluate_fbi_final_acceptance(acceptance_round: int) -> dict:
                return await final_acceptance_service.evaluate(
                    project_id=str(project_id),
                    chapter_number=chapter_number,
                    db=db,
                    final_text=full_content,
                    scene_contract=final_scene_contract,
                    chapter_state=chapter_state,
                    writing_mode_profile=final_writing_mode,
                    style_context=active_style or {},
                    scene_units=[
                        {
                            "text": text,
                            "scene_contract": (
                                scene_contracts[index]
                                if index < len(scene_contracts)
                                else {}
                            ),
                            "writing_mode_profile": (
                                scene_contracts[index].get("writing_mode_profile") or {}
                                if index < len(scene_contracts)
                                else {}
                            ),
                        }
                        for index, text in sorted(_current_scene_texts().items())
                    ],
                    scene_texts=_current_scene_texts(),
                    style_result=polish_result_data,
                    acceptance_round=acceptance_round,
                    parent_case_id=(
                        str(current_review_case_file.get("case_id") or "")
                        if isinstance(current_review_case_file, dict)
                        else ""
                    ),
                )

            def _final_delta_auto_repair_candidates(case_delta: dict | None) -> list[dict]:
                if not isinstance(case_delta, dict) or not case_delta:
                    return []
                from app.services.review_case_file import case_file_issues_to_violations

                candidates: list[dict] = []
                for violation in case_file_issues_to_violations(case_delta):
                    if not isinstance(violation, dict):
                        continue
                    normalized = normalize_violation_semantics(violation)
                    if not normalized.get("blocks_commit", True):
                        continue
                    if _classify_review_violation_for_commit(normalized) != "content_blocking":
                        continue
                    repairability = str(normalized.get("repairability") or "").lower()
                    strategy = str(normalized.get("suggested_strategy") or "").lower()
                    if repairability in {"human_review_required", "manual_review"}:
                        continue
                    if strategy in {"manual_review", "human_review", "review_only"}:
                        continue
                    candidates.append(normalized)
                return candidates

            async def _run_final_delta_repair_cycle() -> dict:
                """Run explicit delta-only FBI repair after Final Acceptance.

                Final Acceptance remains read-only. This function consumes only
                the newly emitted case_file_delta, builds synthetic clean scene
                packets as carriers, and lets FBI repair/recheck own text
                mutation in visible final delta workflow steps.
                """
                nonlocal full_content, final_skill_gate, current_repair_plan

                from app.services.fbi.final_delta_repair_runtime import (
                    FinalDeltaRepairRuntime,
                    FinalDeltaRepairRuntimeCallbacks,
                    FinalDeltaRepairRuntimeConfig,
                )

                async def _delta_update_step(
                    step_name: str,
                    status: str,
                    output: dict | None = None,
                    error: str | None = None,
                ) -> None:
                    await _update_workflow_step(
                        execution_id,
                        step_name,
                        status,
                        output=output,
                        error=error,
                        db=db,
                    )

                def _delta_set_final_gate(value: dict) -> None:
                    nonlocal final_skill_gate
                    final_skill_gate = value

                def _delta_update_repair_plan(value) -> None:
                    nonlocal current_repair_plan
                    current_repair_plan = value

                def _delta_build_executor_context(scene_texts: dict[int, str]) -> dict:
                    return {
                        "project_id": project_id,
                        "chapter_number": chapter_number,
                        "scene_contracts": _scene_contract_map(),
                        "scene_truth_snapshots": _scene_truth_snapshot_map(),
                        "chapter_state": chapter_state,
                        "chapter_scene_context": _chapter_scene_context(scene_texts),
                        "db_session_factory": async_session,
                        "final_delta_only": True,
                        "allow_legacy_fbi_executor": False,
                        **_style_repair_context_snapshot(),
                    }

                def _delta_build_executor_case_file(
                    scene_texts: dict[int, str],
                    case_delta: dict,
                    cycle_plan,
                ) -> dict:
                    return {
                        "all_scene_texts": scene_texts,
                        "all_scene_contracts": _scene_contract_map(),
                        "chapter_state": chapter_state,
                        "review_case_file": {},
                        "case_file_delta": case_delta,
                        "repair_plan": cycle_plan,
                        "scene_truth_snapshots": _scene_truth_snapshot_map(),
                        "delta_only": True,
                        "allow_legacy_fbi_executor": False,
                        "style_context": _workflow_style_context,
                        "style_profile": _workflow_style_context,
                    }

                def _delta_apply_updated_texts(
                    scene_texts: dict[int, str],
                    updated_texts: dict[int, str],
                ) -> list[int]:
                    nonlocal full_content
                    changed_scenes: list[int] = []
                    for updated_idx, repaired_text in updated_texts.items():
                        original_text = scene_texts.get(updated_idx, "")
                        if repaired_text and repaired_text != original_text:
                            repaired_scene_texts[updated_idx] = repaired_text
                            scene_exec.setdefault(updated_idx, {})["generated_text"] = repaired_text
                            changed_scenes.append(updated_idx)
                    full_content = "\n\n".join(_current_scene_texts().values())
                    return changed_scenes

                async def _delta_recheck_scene(scene_idx: int) -> dict | None:
                    if scene_idx < 0 or scene_idx >= len(scene_packages):
                        return {
                            "scene_index": scene_idx,
                            "error": "scene_index_out_of_range",
                        }
                    try:
                        worker = SceneReviewWorker(scene_index=scene_idx)
                        pkg = scene_exec.get(scene_idx, {}).get("pkg") or scene_packages[scene_idx]
                        packet = await asyncio.wait_for(
                            worker.review({
                                "generated_text": _current_scene_texts().get(scene_idx, ""),
                                "scene_contract": scene_exec.get(scene_idx, {}).get("scene_contract") or {},
                                "current_state": chapter_state,
                                "core_facts": pkg.get("core_facts", {}),
                                "character_names": pkg.get("character_names", []),
                                "character_cards": pkg.get("character_cards", []),
                                "project_id": project_id,
                                "chapter_number": chapter_number,
                                "scene_index": scene_idx,
                                "project": project,
                                "db_session_factory": async_session,
                            }),
                            timeout=_RECHECK_REVIEW_TIMEOUT_SECONDS,
                        )
                        review_packets[scene_idx] = packet
                        # delta recheck 必须检查 content_blocking：L9 的 scene_rewrite
                        # 修复可能破坏 L2 已修复的字面锚点等硬约束。若不反馈到下一轮，
                        # final_gate 会因 skill_gate 不含字面锚点检查而 allowed=true，
                        # 但 commit gate 检查 review_packets 时仍会阻断。
                        content_blocking: list[dict] = []
                        for violation in packet.blocking_violations or []:
                            if not isinstance(violation, dict):
                                continue
                            normalized = normalize_violation_semantics(violation)
                            if _classify_review_violation_for_commit(normalized) == "content_blocking":
                                content_blocking.append(normalized)
                        if content_blocking:
                            return {
                                "scene_index": scene_idx,
                                "content_blocking": content_blocking,
                            }
                        return None
                    except Exception as recheck_exc:
                        return {
                            "scene_index": scene_idx,
                            "error": str(recheck_exc),
                        }

                runtime = FinalDeltaRepairRuntime(
                    FinalDeltaRepairRuntimeConfig(
                        project_id=project_id,
                        chapter_number=chapter_number,
                        chapter_state=chapter_state,
                        repair_round=repair_round,
                        max_rounds=MAX_FINAL_ACCEPTANCE_REPAIR_ROUNDS,
                    ),
                    FinalDeltaRepairRuntimeCallbacks(
                        update_step=_delta_update_step,
                        get_final_gate=lambda: final_skill_gate,
                        set_final_gate=_delta_set_final_gate,
                        get_scene_texts=_current_scene_texts,
                        build_executor_context=_delta_build_executor_context,
                        build_executor_case_file=_delta_build_executor_case_file,
                        apply_updated_texts=_delta_apply_updated_texts,
                        recheck_scene=_delta_recheck_scene,
                        evaluate_final_acceptance=_evaluate_fbi_final_acceptance,
                        update_repair_plan=_delta_update_repair_plan,
                        review_case_delta_issues=_review_case_delta_issues,
                        auto_repair_candidates=_final_delta_auto_repair_candidates,
                        violation_sample=_violation_sample,
                        repair_type_counts=_repair_type_counts,
                        repair_order_sample=_repair_order_sample,
                        is_blocking_order_failure=_is_blocking_repair_order_failure,
                        final_failure_summary=_final_acceptance_failure_summary,
                        cycle_progress=_final_acceptance_cycle_progress,
                    ),
                )
                await EditorGenerationDAGRuntime(
                    editor_scene_dag,
                    project_id=project_id,
                    chapter_number=chapter_number,
                    max_concurrent=8,
                    execution_id=execution_id,
                ).run(
                    runtime.node_handlers(),
                    node_types={
                        "final_acceptance_delta_intake",
                        "final_acceptance_delta_blueprint",
                        "final_acceptance_delta_execute",
                        "final_acceptance_delta_recheck",
                    },
                )
                return runtime.result or {}

            _phase_start = time.perf_counter()
            if human_review_override:
                final_skill_gate = {
                    "allowed": True,
                    "reason": "human_override_skip_content_recheck",
                    "trace": {
                        "human_override": True,
                        "content_recheck_skipped": True,
                        "source": "manual_accept_without_recheck",
                    },
                    "case_file_delta": {},
                }
            else:
                final_skill_gate = await _evaluate_fbi_final_acceptance(0)
            await _record_final_acceptance_phase(
                "initial_skill_gate",
                _phase_start,
                allowed=bool(final_skill_gate.get("allowed", False)),
                reason=str(final_skill_gate.get("reason") or "")[:160],
            )

            if final_skill_gate.get("allowed", False):
                for delta_step in (
                    "final_acceptance_delta_intake",
                    "final_acceptance_delta_blueprint",
                    "final_acceptance_delta_execute",
                    "final_acceptance_delta_recheck",
                ):
                    await _update_workflow_step(
                        execution_id,
                        delta_step,
                        "skipped",
                        output={
                            "reason": "initial_final_acceptance_passed",
                            "delta_required": False,
                        },
                        error="",
                        db=db,
                    )

            if not final_skill_gate.get("allowed", False):
                _phase_start = time.perf_counter()
                await _record_final_acceptance_phase(
                    "case_delta_build",
                    _phase_start,
                    issue_count=len(_review_case_delta_issues(final_skill_gate.get("case_file_delta") or {})),
                )

                final_repair_cycle: dict = {
                    "attempted": False,
                    "reason": "final_acceptance_delta_pending",
                    "prior_repair_round": repair_round,
                    "final_repair_round": 0,
                    "max_repair_cycles": 1,
                    "cycle_history": [],
                }
                final_repair_cycle["initial_failure_summary"] = _final_acceptance_failure_summary(
                    final_skill_gate,
                    content_blocking_count=0,
                )
                final_repair_budget = 0
                final_repair_cycle["max_repair_cycles"] = final_repair_budget
                final_repair_cycle["external_delta_required"] = True
                # Final Acceptance is an acceptance gate only. It emits a
                # case_file_delta and leaves follow-up repair to explicit FBI
                # delta handling instead of hiding a repair cycle inside this
                # workflow step.
                final_repair_round = 0
                final_content_blocking_pending = False
                previous_content_blocking_count = 0
                try:
                    while (
                        (
                            not final_skill_gate.get("allowed", False)
                            or final_content_blocking_pending
                        )
                        and final_repair_round < final_repair_budget
                    ):
                        final_repair_cycle["attempted"] = True
                        final_repair_round += 1
                        case_review_round = final_repair_round
                        cycle_before_summary = _final_acceptance_failure_summary(
                            final_skill_gate,
                            content_blocking_count=previous_content_blocking_count,
                        )
                        case_delta = final_skill_gate.get("case_file_delta") or {}
                        case_deltas = [case_delta] if case_delta else []
                        ready_packets = [p for p in review_packets if p is not None]
                        cycle_case = ChapterReviewCase(
                            project_id=project_id,
                            chapter_number=chapter_number,
                            chapter_initial_state=chapter_state,
                            scene_packets=ready_packets,
                            review_round=case_review_round,
                            review_case_file=current_review_case_file or {},
                            case_file_deltas=case_deltas,
                        )

                        _phase_start = time.perf_counter()
                        intake_svc = FBIChapterCaseIntakeService()
                        validated_cycle_case = await intake_svc.intake(cycle_case)
                        planner = FBIChapterRepairPlanner()
                        cycle_plan = await planner.plan(validated_cycle_case)
                        current_repair_plan = cycle_plan
                        await _record_final_acceptance_phase(
                            "cycle_intake_plan",
                            _phase_start,
                            final_repair_round=final_repair_round,
                            case_id=cycle_plan.case_id,
                            status=cycle_plan.status,
                            orders=len(cycle_plan.orders),
                        )

                        current_cycle = {
                            "repair_round": case_review_round,
                            "final_repair_round": final_repair_round,
                            "case_id": cycle_plan.case_id,
                            "status": cycle_plan.status,
                            "orders": len(cycle_plan.orders),
                            "repair_type_counts": _repair_type_counts(cycle_plan.orders),
                            "execution_layers": cycle_plan.execution_layers,
                        }

                        changed_scenes: list[int] = []
                        blocking_failed_orders: list = []
                        recoverable_failed_orders: list = []
                        scenes_to_recheck: set[int] = set()
                        if not cycle_plan.is_clean():
                            scene_texts = _current_scene_texts()
                            executor = ChapterRepairExecutor()
                            _phase_start = time.perf_counter()
                            updated_texts, updated_plan = await executor.execute(
                                cycle_plan,
                                scene_texts,
                                context={
                                    "project_id": project_id,
                                    "chapter_number": chapter_number,
                                    "scene_contracts": _scene_contract_map(),
                                    "scene_truth_snapshots": _scene_truth_snapshot_map(),
                                    "chapter_state": chapter_state,
                                    "chapter_scene_context": _chapter_scene_context(scene_texts),
                                    "db_session_factory": async_session,
                                    "allow_legacy_fbi_executor": False,
                                    "cancellation_check": stop_if_cancelled,
                                    **_style_repair_context_snapshot(),
                                },
                                case_file={
                                    "all_scene_texts": scene_texts,
                                    "all_scene_contracts": _scene_contract_map(),
                                    "chapter_state": chapter_state,
                                    "review_packets": review_packets,
                                    "review_case_file": current_review_case_file or {},
                                    "case_file_delta": case_delta,
                                    "repair_plan": cycle_plan,
                                    "scene_truth_snapshots": _scene_truth_snapshot_map(),
                                    "allow_legacy_fbi_executor": False,
                                    "style_context": _workflow_style_context,
                                    "style_profile": _workflow_style_context,
                                },
                            )
                            await _record_final_acceptance_phase(
                                "cycle_executor",
                                _phase_start,
                                final_repair_round=final_repair_round,
                                order_count=len(updated_plan.orders),
                            )
                            current_repair_plan = updated_plan
                            failed_orders = [
                                order for order in updated_plan.orders
                                if _repair_order_failure_disposition(order) != "accepted"
                            ]
                            blocking_failed_orders = [
                                order for order in failed_orders
                                if _is_blocking_repair_order_failure(order)
                            ]
                            recoverable_failed_orders = [
                                order for order in failed_orders
                                if _repair_order_failure_disposition(order) == "recoverable_delta"
                            ]
                            for order in recoverable_failed_orders:
                                disposition = _repair_order_failure_disposition(order)
                                order.status = "skipped"
                                order.repair_audit = {
                                    **(order.repair_audit or {}),
                                    "disposition": disposition,
                                    "continue_to_recheck": True,
                                }
                                for target_scene in sorted(_repair_affected_scenes(order)):
                                    scenes_to_recheck.add(target_scene)
                                    scenes_requiring_recheck.add(target_scene)
                                    repair_failed_deltas.setdefault(target_scene, []).extend(
                                        _repair_failure_delta_items(
                                            order,
                                            disposition,
                                            updated_texts,
                                        )
                                    )
                                    if target_scene not in repaired_scene_texts:
                                        base_text = scene_texts.get(target_scene, "")
                                        if base_text:
                                            repaired_scene_texts[target_scene] = base_text

                            for order in updated_plan.orders:
                                audit = getattr(order, "repair_audit", {}) or {}
                                if (
                                    getattr(order, "repair_type", "") == "contract_patch"
                                    and _repair_order_failure_disposition(order) == "accepted"
                                    and audit.get("contract_changed")
                                ):
                                    for target_scene in sorted(_repair_affected_scenes(order)):
                                        scenes_to_recheck.add(target_scene)
                                        scenes_requiring_recheck.add(target_scene)
                                        if target_scene not in repaired_scene_texts:
                                            base_text = scene_texts.get(target_scene, "")
                                            if base_text:
                                                repaired_scene_texts[target_scene] = base_text

                            for updated_idx, repaired_text in updated_texts.items():
                                original_text = scene_texts.get(updated_idx, "")
                                if repaired_text and repaired_text != original_text:
                                    repaired_scene_texts[updated_idx] = repaired_text
                                    scene_exec.setdefault(updated_idx, {})["generated_text"] = repaired_text
                                    changed_scenes.append(updated_idx)
                                    scenes_to_recheck.add(updated_idx)

                        current_cycle.update({
                            "changed_scenes": changed_scenes,
                            "failed_orders": len(blocking_failed_orders),
                            "recoverable_failed_orders": len(recoverable_failed_orders),
                            "order_samples": _repair_order_sample(
                                getattr(current_repair_plan, "orders", []) or []
                            ),
                        })

                        post_repair_validator_errors: list[dict] = []
                        post_repair_content_blocking: list[dict] = []
                        _phase_start = time.perf_counter()
                        for scene_idx in sorted(scenes_to_recheck):
                            if scene_idx < 0 or scene_idx >= len(scene_packages):
                                continue
                            worker = SceneReviewWorker(scene_index=scene_idx)
                            context = {
                                "generated_text": _current_scene_texts().get(scene_idx, ""),
                                "scene_contract": scene_exec.get(scene_idx, {}).get("scene_contract") or {},
                                "current_state": chapter_state,
                                "core_facts": scene_exec.get(scene_idx, {}).get("pkg", {}).get("core_facts", {}),
                                "character_names": scene_exec.get(scene_idx, {}).get("pkg", {}).get("character_names", []),
                                "character_cards": scene_exec.get(scene_idx, {}).get("pkg", {}).get("character_cards", []),
                                "project_id": project_id,
                                "chapter_number": chapter_number,
                                "scene_index": scene_idx,
                                "project": project,
                                "db_session_factory": async_session,
                            }
                            packet = await worker.review(context)
                            review_packets[scene_idx] = packet
                            for violation in packet.blocking_violations or []:
                                if not isinstance(violation, dict):
                                    continue
                                normalized = normalize_violation_semantics(violation)
                                lane = _classify_review_violation_for_commit(normalized)
                                if lane == "validator_error":
                                    post_repair_validator_errors.append(normalized)
                                elif lane == "content_blocking":
                                    post_repair_content_blocking.append(normalized)
                        await _record_final_acceptance_phase(
                            "cycle_scene_recheck",
                            _phase_start,
                            final_repair_round=final_repair_round,
                            rechecked_scenes=sorted(scenes_to_recheck),
                            content_blocking=len(post_repair_content_blocking),
                            validator_errors=len(post_repair_validator_errors),
                        )

                        full_content = "\n\n".join(_current_scene_texts().values())
                        _phase_start = time.perf_counter()
                        final_skill_gate = await _evaluate_fbi_final_acceptance(final_repair_round)
                        await _record_final_acceptance_phase(
                            "cycle_skill_gate",
                            _phase_start,
                            final_repair_round=final_repair_round,
                            allowed=bool(final_skill_gate.get("allowed", False)),
                            reason=str(final_skill_gate.get("reason") or "")[:160],
                        )
                        if final_skill_gate.get("allowed", False):
                            final_skill_gate.pop("case_file_delta", None)
                        final_content_blocking_pending = bool(post_repair_content_blocking)

                        current_cycle.update({
                            "rechecked_scenes": sorted(scenes_to_recheck),
                            "post_repair_content_blocking": len(post_repair_content_blocking),
                            "post_repair_validator_errors": len(post_repair_validator_errors),
                            "final_allowed": bool(final_skill_gate.get("allowed", False)),
                            "final_reason": final_skill_gate.get("reason", ""),
                        })
                        cycle_after_summary = _final_acceptance_failure_summary(
                            final_skill_gate,
                            content_blocking_count=len(post_repair_content_blocking),
                        )
                        cycle_progress = _final_acceptance_cycle_progress(
                            cycle_before_summary,
                            cycle_after_summary,
                        )
                        current_cycle.update({
                            "failure_summary_before": cycle_before_summary,
                            "failure_summary_after": cycle_after_summary,
                            "failure_progress": cycle_progress,
                        })
                        final_repair_cycle["cycle_history"].append(current_cycle)
                        final_repair_cycle.update({
                            "repair_round": case_review_round,
                            "final_repair_round": final_repair_round,
                            "case_id": current_cycle["case_id"],
                            "status": current_cycle["status"],
                            "orders": current_cycle["orders"],
                            "repair_type_counts": current_cycle["repair_type_counts"],
                            "execution_layers": current_cycle["execution_layers"],
                            "changed_scenes": changed_scenes,
                            "failed_orders": len(blocking_failed_orders),
                            "recoverable_failed_orders": len(recoverable_failed_orders),
                            "rechecked_scenes": sorted(scenes_to_recheck),
                            "order_samples": current_cycle["order_samples"],
                            "failure_progress": cycle_progress,
                        })

                        if blocking_failed_orders or post_repair_validator_errors:
                            final_repair_cycle["reason"] = (
                                "blocking_repair_order_failed"
                                if blocking_failed_orders
                                else "validator_error_after_final_repair"
                            )
                            break
                        if (
                            not final_skill_gate.get("allowed", False)
                            or post_repair_content_blocking
                        ) and not cycle_progress.get("has_progress"):
                            final_repair_cycle["reason"] = "no_progress"
                            final_skill_gate["allowed"] = False
                            final_skill_gate["reason"] = (
                                "final acceptance repair cycle made no measurable progress"
                            )
                            break
                        previous_content_blocking_count = len(post_repair_content_blocking)

                    if final_skill_gate.get("allowed", False) and not final_content_blocking_pending:
                        final_repair_cycle["reason"] = "resolved"
                    elif (
                        final_repair_round >= final_repair_budget
                        and final_repair_cycle.get("reason") not in {
                            "blocking_repair_order_failed",
                            "validator_error_after_final_repair",
                            "no_progress",
                            "final_acceptance_delta_pending",
                        }
                    ):
                        final_repair_cycle["reason"] = "max_repair_cycles_exhausted"
                    if final_content_blocking_pending:
                        final_skill_gate["allowed"] = False
                        final_skill_gate["reason"] = (
                            "final acceptance recheck content blocking remains"
                        )
                except Exception as final_cycle_exc:
                    logger.warning("final_acceptance FBI repair cycle failed: %s", final_cycle_exc, exc_info=True)
                    final_repair_cycle.update({
                        "failed": True,
                        "error": str(final_cycle_exc),
                    })
                final_skill_gate["fbi_repair_cycle"] = final_repair_cycle
            full_content = final_skill_gate.get("text", full_content)
            final_deterministic_cleanup = {
                "applied": False,
                "repair_count": 0,
                "handled_issue_ids": [],
                "executor_path": "deterministic_final_gate",
                "llm_calls": 0,
            }
            if not final_skill_gate.get("allowed", False):
                final_auto_candidates = _final_delta_auto_repair_candidates(
                    final_skill_gate.get("case_file_delta") or {}
                )
                final_deterministic_cleanup = _apply_deterministic_final_gate_repairs(
                    full_content,
                    final_auto_candidates,
                )
                if final_deterministic_cleanup.get("applied"):
                    from app.utils.dash_artifacts import count_dash_artifacts

                    before_cleanup_text = full_content
                    full_content = str(
                        final_deterministic_cleanup.get("repaired_text")
                        or full_content
                    )
                    final_deterministic_cleanup.update({
                        "before_dash_count": count_dash_artifacts(before_cleanup_text),
                        "after_dash_count": count_dash_artifacts(full_content),
                    })
                    _phase_start = time.perf_counter()
                    rechecked_final_gate = await _evaluate_fbi_final_acceptance(1)
                    rechecked_trace = dict(rechecked_final_gate.get("trace") or {})
                    rechecked_trace["deterministic_cleanup"] = {
                        key: value
                        for key, value in final_deterministic_cleanup.items()
                        if key != "repaired_text"
                    }
                    rechecked_final_gate["trace"] = rechecked_trace
                    rechecked_final_gate["deterministic_cleanup"] = rechecked_trace[
                        "deterministic_cleanup"
                    ]
                    rechecked_final_gate["fbi_repair_cycle"] = {
                        "attempted": True,
                        "reason": (
                            "deterministic_final_gate_resolved"
                            if rechecked_final_gate.get("allowed", False)
                            else "deterministic_final_gate_remaining"
                        ),
                        "max_repair_cycles": 0,
                        "llm_calls": 0,
                        "deterministic_cleanup": rechecked_trace[
                            "deterministic_cleanup"
                        ],
                    }
                    final_skill_gate = rechecked_final_gate
                    await _record_final_acceptance_phase(
                        "deterministic_cleanup_recheck",
                        _phase_start,
                        allowed=bool(final_skill_gate.get("allowed", False)),
                        before_dash_count=final_deterministic_cleanup.get("before_dash_count"),
                        after_dash_count=final_deterministic_cleanup.get("after_dash_count"),
                        llm_calls=0,
                    )
            final_acceptance_phase_totals["final_acceptance_total"] = int(
                (time.perf_counter() - final_acceptance_started) * 1000
            )

            await _update_workflow_step(
                execution_id,
                "final_acceptance",
                "completed" if final_skill_gate.get("allowed", False) else "failed",
                output={
                    "allowed": bool(final_skill_gate.get("allowed", False)),
                    "reason": final_skill_gate.get("reason", ""),
                    "final_skill_gate": final_skill_gate.get("trace", {}),
                    "case_file_delta": final_skill_gate.get("case_file_delta", {}),
                    "fbi_repair_cycle": final_skill_gate.get("fbi_repair_cycle", {}),
                    "phase_timing_ms": final_acceptance_phase_totals,
                    "phase_timing_events": final_acceptance_phase_events[-50:],
                },
                db=db,
            )

            # 双闸门一致性保障：final_skill_gate 只检查 skill validation，
            # commit gate 还会检查 review_packets 的 content_blocking violations。
            # 若 allowed=true 但 review_packets 有 content_blocking，FinalDeltaRepairRuntime
            # 会因 allowed=true 跳过修复，导致 commit gate 永久阻断。这里在调用
            # FinalDeltaRepairRuntime 之前检测并注入这些 violations，让修复循环能处理。
            if final_skill_gate.get("allowed", False):
                from app.services.fbi.final_delta_repair_runtime import (
                    FinalDeltaRepairRuntime as _FDRT,
                )
                _packet_blocking_issues: list[dict] = []
                for _pkt_idx, _packet in enumerate(
                    review_packets if isinstance(review_packets, list) else
                    (review_packets.values() if isinstance(review_packets, dict) else [])
                ):
                    if _packet is None:
                        continue
                    _bv = (
                        _packet.get("blocking_violations", [])
                        if isinstance(_packet, dict)
                        else getattr(_packet, "blocking_violations", [])
                    ) or []
                    for _v in _bv:
                        if not isinstance(_v, dict):
                            continue
                        _normalized = normalize_violation_semantics(_v)
                        if _classify_review_violation_for_commit(_normalized) != "content_blocking":
                            continue
                        _scene_idx = _normalized.get("scene_index")
                        if _scene_idx is None:
                            _scene_idx = _pkt_idx
                        _issue = _FDRT._violation_to_case_issue(_normalized, _scene_idx)
                        _packet_blocking_issues.append(_issue.model_dump())
                if _packet_blocking_issues:
                    _case_delta = dict(final_skill_gate.get("case_file_delta") or {})
                    _scene_issues = list(_case_delta.get("scene_issues") or [])
                    _existing_keys = {
                        str(_i.get("issue_id") or _i.get("dedupe_key") or "")
                        for _i in _scene_issues
                        if isinstance(_i, dict)
                    }
                    _injected = 0
                    for _issue_dict in _packet_blocking_issues:
                        _key = str(_issue_dict.get("issue_id") or _issue_dict.get("dedupe_key") or "")
                        if _key and _key in _existing_keys:
                            continue
                        _scene_issues.append(_issue_dict)
                        _existing_keys.add(_key)
                        _injected += 1
                    if _injected > 0:
                        _case_delta["scene_issues"] = _scene_issues
                        final_skill_gate = dict(final_skill_gate)
                        final_skill_gate["allowed"] = False
                        final_skill_gate["reason"] = (
                            f"review_packets content blocking remains: "
                            f"{_injected} issue(s) from scene_validator"
                        )
                        final_skill_gate["case_file_delta"] = _case_delta

            # D19-2：直接跳过 FinalDeltaRepairRuntime（取消终验修复循环）。
            # 根因：终验的标准（AgentSkillCommitGate）已通过 D19-1 提前到 L0 检测、
            #   L2 修复。终验阶段不再做修复循环，只保留 final_skill_gate 的检测结果
            #   作为最终确认。如果 L0-L3 修复不彻底，残留违规直接进入 waiting_review
            #   人工处理，避免终验 Round 2 的时间消耗（~21-50min）。
            # 效果：终验阶段从 ~21min 降到 ~0.1min（只做一次 skill gate 检测）。
            _final_delta_blocking_count = len(_review_case_delta_issues(final_skill_gate.get("case_file_delta") or {}))
            final_delta_repair_output = {
                "attempted": bool(final_deterministic_cleanup.get("applied")),
                "delta_only": True,
                "runtime": "final_delta_repair_runtime",
                "visible_nodes": ["final_acceptance_delta_execute"]
                if final_deterministic_cleanup.get("applied")
                else [],
                "initial_issue_count": _final_delta_blocking_count,
                "auto_repairable_issue_count": len(
                    final_deterministic_cleanup.get("handled_issue_ids") or []
                ),
                "cycles": [
                    {
                        key: value
                        for key, value in final_deterministic_cleanup.items()
                        if key != "repaired_text"
                    }
                ] if final_deterministic_cleanup.get("applied") else [],
                "final_allowed": bool(final_skill_gate.get("allowed", False)),
                "final_reason": final_skill_gate.get("reason", ""),
                "reason": (
                    "deterministic_final_gate_cleanup"
                    if final_deterministic_cleanup.get("applied")
                    else "final_acceptance_standard_moved_to_l0_skip_delta_repair"
                ),
            }
            for delta_step in (
                "final_acceptance_delta_intake",
                "final_acceptance_delta_blueprint",
                "final_acceptance_delta_execute",
                "final_acceptance_delta_recheck",
            ):
                await _update_workflow_step(
                    execution_id,
                    delta_step,
                    "skipped",
                    output={
                        "reason": final_delta_repair_output["reason"],
                        "deterministic_cleanup": bool(
                            final_deterministic_cleanup.get("applied")
                        ),
                        "llm_calls": 0,
                    },
                    error="",
                    db=db,
                )
            logger.info(
                "D19-2: FinalDeltaRepairRuntime skipped (standard moved to L0). "
                "allowed=%s, blocking_delta=%d",
                final_skill_gate.get("allowed", False),
                _final_delta_blocking_count,
            )
            from app.services.fbi.workflow_metrics import FBIWorkflowMetrics

            fbi_workflow_metrics = FBIWorkflowMetrics.combine(
                plan=current_repair_plan,
                final_delta_cycle=final_delta_repair_output,
            )
            if final_delta_repair_output.get("attempted"):
                await _update_workflow_step(
                    execution_id,
                    "final_acceptance",
                    "completed" if final_skill_gate.get("allowed", False) else "failed",
                    output={
                        "allowed": bool(final_skill_gate.get("allowed", False)),
                        "reason": final_skill_gate.get("reason", ""),
                        "final_skill_gate": final_skill_gate.get("trace", {}),
                        "case_file_delta": final_skill_gate.get("case_file_delta", {}),
                        "fbi_repair_cycle": final_skill_gate.get("fbi_repair_cycle", {}),
                        "fbi_delta_repair_cycle": final_skill_gate.get("fbi_delta_repair_cycle", {}),
                        "fbi_workflow_metrics": fbi_workflow_metrics,
                        "phase_timing_ms": final_acceptance_phase_totals,
                        "phase_timing_events": final_acceptance_phase_events[-50:],
                    },
                    db=db,
                )

            outline_data = project.outline_data or {}
            outline_chapters = outline_data.get("chapters", [])
            chapter_title = f"Chapter {chapter_number}"
            for ch in outline_chapters:
                if ch.get("chapter_number") == chapter_number:
                    ch_title = ch.get("title", "").strip()
                    if ch_title:
                        chapter_title = ch_title
                    break

            if await stop_if_cancelled():
                return {"content": "", "status": "cancelled"}

            # ---- 提交闸：写章前统一硬闸检查 ----
            commit_gate_result = await _commit_gate_check(
                execution_id=execution_id,
                review_packets=review_packets,
                current_repair_plan=current_repair_plan,
                db=db,
                final_text=full_content,
                scene_texts=_current_scene_texts(),
                chapter_state=chapter_state,
                style_result=polish_result_data,
                skill_gate_result=final_skill_gate,
            )
            if not commit_gate_result["allowed"]:
                await _update_workflow_step(
                    execution_id,
                    "write_chapter",
                    "skipped",
                    output={
                        "final_skill_gate": final_skill_gate.get("trace", {}),
                        "case_file_delta": final_skill_gate.get("case_file_delta", {}),
                        "fbi_delta_repair_cycle": final_skill_gate.get("fbi_delta_repair_cycle", {}),
                        "fbi_workflow_metrics": fbi_workflow_metrics,
                    },
                    db=db,
                )
                gate_error = commit_gate_result["reason"]
                case_delta = final_skill_gate.get("case_file_delta") or {}
                from app.services.review_case_file import case_file_issues_to_violations

                final_gate_violations = case_file_issues_to_violations(case_delta)
                if not final_gate_violations and (commit_gate_result.get("commit_health") or {}).get("hard_issues"):
                    from app.services.fbi.final_acceptance_delta import build_commit_health_case_delta

                    case_delta = build_commit_health_case_delta(
                        project_id=str(project_id),
                        chapter_number=chapter_number,
                        final_text=full_content,
                        health_result=commit_gate_result.get("commit_health") or {},
                        acceptance_round=MAX_FINAL_ACCEPTANCE_REPAIR_ROUNDS,
                        parent_case_id=(
                            str(current_review_case_file.get("case_id") or "")
                            if isinstance(current_review_case_file, dict)
                            else ""
                        ),
                        scene_texts=_current_scene_texts(),
                    )
                    final_skill_gate["case_file_delta"] = case_delta
                    final_gate_violations = case_file_issues_to_violations(case_delta)
                if not final_gate_violations:
                    final_gate_violations = [{
                        "type": "final_commit_gate_blocked",
                        "severity": "high",
                        "detail": gate_error,
                        "blocks_commit": True,
                        "suggested_strategy": "manual_review",
                        "repair_scope": "prose_text",
                        "user_visible": True,
                    }]
                await _update_workflow_step(
                    execution_id,
                    "write_chapter",
                    "skipped",
                    output={
                        "final_skill_gate": final_skill_gate.get("trace", {}),
                        "case_file_delta": case_delta,
                        "fbi_repair_cycle": final_skill_gate.get("fbi_repair_cycle", {}),
                        "fbi_delta_repair_cycle": final_skill_gate.get("fbi_delta_repair_cycle", {}),
                        "fbi_workflow_metrics": fbi_workflow_metrics,
                        "commit_gate_result": commit_gate_result,
                    },
                    db=db,
                )
                scene_idx = 0
                for violation in final_gate_violations:
                    source_scene = violation.get("source_scene")
                    if isinstance(source_scene, int) and 0 <= source_scene < len(scene_packages):
                        scene_idx = source_scene
                        break
                await _pause_parallel_scene_for_human_review(
                    scene_idx=scene_idx,
                    gen_step="final_acceptance",
                    check_step="write_chapter",
                    error_detail=f"commit gate blocked: {gate_error}",
                    pipeline_output={
                        "runtime": "parallel_review",
                        "error_code": "final_commit_gate_needs_human_review",
                        "final_skill_gate": final_skill_gate.get("trace", {}),
                        "case_file_delta": case_delta,
                        "fbi_repair_cycle": final_skill_gate.get("fbi_repair_cycle", {}),
                        "fbi_delta_repair_cycle": final_skill_gate.get("fbi_delta_repair_cycle", {}),
                        "fbi_workflow_metrics": fbi_workflow_metrics,
                        "commit_gate_result": commit_gate_result,
                    },
                    violations=final_gate_violations,
                    candidate_text=full_content,
                    review_scope="chapter",
                    db_session=db,
                )
                raise EditorDAGAbort({
                    "content": "",
                    "status": "waiting_review",
                    "error": f"commit gate blocked: {gate_error}",
                })

            await _update_workflow_step(execution_id, "write_chapter", "running", db=db)
            existing = await db.execute(
                select(Chapter).where(
                    Chapter.project_id == uuid.UUID(project_id),
                    Chapter.chapter_number == chapter_number,
                )
            )
            chapter = existing.scalar_one_or_none()
            execution_row = await db.get(WorkflowExecution, uuid.UUID(str(execution_id)))
            write_context = execution_row.input_context if execution_row and isinstance(execution_row.input_context, dict) else {}
            from app.services.chapter_write_guard import (
                REPLACE_WITH_BACKUP,
                backup_chapter_before_ai_replace,
                validate_ai_chapter_write,
            )
            write_policy = str(write_context.get("write_policy") or "create_only")
            expected_content_hash = str(write_context.get("source_content_hash") or "")
            write_allowed, write_error = validate_ai_chapter_write(
                chapter,
                write_policy=write_policy,
                expected_content_hash=expected_content_hash,
            )
            if not write_allowed:
                await _update_workflow_step(
                    execution_id,
                    "write_chapter",
                    "failed",
                    error=write_error,
                    db=db,
                )
                await _complete_workflow_execution(
                    execution_id,
                    "failed",
                    error=write_error,
                    result={"chapter_number": chapter_number, "content_preserved": True},
                    db=db,
                )
                return {"content": "", "status": "failed", "error": write_error}
            if chapter and write_policy == REPLACE_WITH_BACKUP:
                await backup_chapter_before_ai_replace(
                    db,
                    chapter=chapter,
                    execution_id=str(execution_id),
                )
            if chapter:
                try:
                    from app.services.chapter_commit_service import discard_worldview_projection_outbox
                    # Two-phase commit: do NOT retract old worldview here.
                    # project_committed_chapter will retract old generation_revisions
                    # after new observations are successfully created.
                    await discard_worldview_projection_outbox(db, uuid.UUID(project_id), chapter_number)
                except Exception as exc:
                    import logging
                    logging.getLogger(__name__).warning("[editor_chat] worldview outbox discard before rewrite: %s", exc)
                    raise
                chapter.content = full_content
                chapter.title = chapter_title
                chapter.status = "committing"
            else:
                chapter = Chapter(
                    project_id=uuid.UUID(project_id),
                    chapter_number=chapter_number,
                    title=chapter_title,
                    content=full_content,
                    status="committing",
                )
                db.add(chapter)

            if await stop_if_cancelled():
                return {"content": "", "status": "cancelled"}
            await db.commit()
            await db.refresh(chapter)

            from app.services.chapter_commit_service import (
                apply_pending_chapter_effects,
                enqueue_chapter_effects,
            )

            await enqueue_chapter_effects(
                db,
                project_id=uuid.UUID(project_id),
                chapter_number=chapter_number,
                pending_scene_effects=pending_scene_effects,
                chapter_state=chapter_state,
                execution_id=execution_id,
            )
            await db.commit()
            await apply_pending_chapter_effects(
                db,
                project_id=uuid.UUID(project_id),
                chapter_number=chapter_number,
                project=project,
            )

            try:
                event_bus = CrossSystemEventBus()
                await event_bus.publish_event(
                    db=db,
                    project_id=project_id,
                    event_type="CHAPTER_GENERATED.v1",
                    source_system="editor",
                    priority="normal",
                    payload={
                        "entity_type": "chapter",
                        "entity_id": str(chapter.id) if chapter.id else "",
                        "entity_name": chapter_title,
                        "change_type": "generated",
                        "relevant_chapter": chapter_number,
                        "summary": f"Chapter {chapter_number} generation completed: {chapter_title}",
                        "meta": {
                            "chapter_number": chapter_number,
                            "word_count": count_words(full_content),
                            "execution_id": execution_id,
                        },
                    },
                )
            except Exception as exc:
                logger.warning("Failed to publish committed chapter event: %s", exc)

            await _update_workflow_step(execution_id, "write_chapter", "completed",
                                        output={
                                            "word_count": count_words(full_content),
                                            "final_skill_gate": final_skill_gate.get("trace", {}),
                                            "fbi_workflow_metrics": fbi_workflow_metrics,
                                        }, db=db)

            try:
                from app.services.chapter_summary_service import ChapterSummaryService
                summary_service = ChapterSummaryService()
                chapter_summary = await summary_service.generate_summary(
                    project_id, chapter_number, full_content,
                    chapter_title=chapter_title, db=db
                )
            except Exception:
                chapter_summary = {}

            try:
                from app.services.editor_memory_service import EditorMemoryService
                memory_service = EditorMemoryService()
                await memory_service.record_session(
                    project_id, {
                        "topics": [f"Chapter {chapter_number} generation"],
                        "unfinished_tasks": [],
                        "key_decisions": [],
                        "user_preferences": {},
                    }, db=db
                )
            except Exception:
                pass

            try:
                await db.refresh(project)
            except Exception:
                pass

            core_data = project.core_data or {}
            history = core_data.get("generation_history", [])
            if not isinstance(history, list):
                history = []
            history.append({
                "type": "chapter",
                "chapter": chapter_number,
                "timestamp": _utcnow().isoformat(),
                "word_count": count_words(full_content),
                "execution_id": execution_id,
                "generation_mode": "chapter_writer" if use_chapter_writer else "scene_writer",
                "context_ledger_id": chapter_context_ledger.get("ledger_id", "") if chapter_context_ledger else "",
                "report_summary": {
                    "pass": all(r.get("pass", True) for r in consistency_reports),
                    "auto_repairs": sum(len(r.get("auto_repairs", [])) for r in consistency_reports),
                    "repair_applied": any(r.get("repair_applied", False) for r in consistency_reports),
                    "scene_critic_violations": sum(len(r.get("scene_critic_violations", [])) for r in consistency_reports),
                    "degraded": any(r.get("degraded", False) for r in consistency_reports),
                },
                "chapter_state": chapter_state,
            })
            core_data["generation_history"] = history
            project.core_data = core_data
            await db.commit()

            try:
                from app.services.narrative_sync_service import NarrativeSyncService

                sync_service = NarrativeSyncService()
                await sync_service.sync_chapter_write(
                    project_id,
                    chapter_number,
                    db,
                    title=chapter_title,
                    content=full_content,
                    summary=chapter_summary,
                    chapter_state=chapter_state,
                    scene_packages=scene_packages,
                    source_system="editor",
                )
            except Exception:
                pass

            await _complete_workflow_execution(execution_id, "completed", result={
                "chapter_number": chapter_number,
                "word_count": count_words(full_content),
                "total_scenes": len(scene_packages),
                "generation_mode": "chapter_writer" if use_chapter_writer else "scene_writer",
                "context_ledger_id": chapter_context_ledger.get("ledger_id", "") if chapter_context_ledger else "",
                "trace_id": _trace_id,
                "candidate_ready": bool(chapter_candidate_payload),
                "candidate": chapter_candidate_payload or None,
                "llm_usage": get_llm_metrics().usage_for_execution(execution_id),
            }, db=db)

            # ---- Phase 0: Record completion trace ----
            if _trace_id:
                try:
                    _total_findings = sum(
                        len(r.get("quality_gate", {}).get("violations", []))
                        for r in consistency_reports if isinstance(r, dict)
                    )
                    _trace_collector.record_post_metrics(_trace_id, PostGenerationMetrics(
                        text_char_count=len(full_content),
                        quality_finding_count=_total_findings,
                    ))
                    _trace_collector.record_layer(_trace_id, "ledger_writeback", "ok")
                    _trace_collector.finish_trace(_trace_id, "completed")
                except Exception:
                    pass

        except asyncio.CancelledError:
            logger.info("Workflow %s asyncio task cancelled by user", execution_id)
            if _trace_id:
                try:
                    _trace_collector.finish_trace(_trace_id, "cancelled", final_error="User manually terminated workflow")
                except Exception:
                    pass
            try:
                if not await _is_workflow_cancelled(execution_id, db):
                    await _complete_workflow_execution(
                        execution_id,
                        "cancelled",
                        error="User manually terminated workflow",
                        db=db,
                    )
            except Exception:
                async with async_session() as fallback_db:
                    if not await _is_workflow_cancelled(execution_id, fallback_db):
                        await _complete_workflow_execution(
                            execution_id,
                            "cancelled",
                            error="User manually terminated workflow",
                            db=fallback_db,
                        )
            raise
        except EditorDAGAbort as abort:
            abort_result = abort.result or {}
            abort_status = abort_result.get("status", "failed")
            abort_error = abort_result.get("error", "editor DAG execution aborted")

            if abort_status == "waiting_review":
                return abort_result
            if abort_status in ("cancelled",):
                wf_status = "cancelled"
            elif abort_status in ("blocked", "needs_human_review"):
                wf_status = "blocked"
            else:
                wf_status = "failed"

            if _trace_id:
                try:
                    _trace_collector.finish_trace(_trace_id, wf_status, final_error=str(abort_error)[:200])
                except Exception:
                    pass
            try:
                if not await _is_workflow_cancelled(execution_id, db):
                    await _complete_workflow_execution(execution_id, wf_status, error=abort_error, db=db)
            except Exception:
                async with async_session() as fallback_db:
                    if not await _is_workflow_cancelled(execution_id, fallback_db):
                        await _complete_workflow_execution(execution_id, wf_status, error=abort_error, db=fallback_db)
            return abort_result
        except Exception as e:
            import traceback
            traceback.print_exc()
            # ---- Phase 0: Record failure trace ----
            if _trace_id:
                try:
                    _trace_collector.finish_trace(_trace_id, "failed", final_error=str(e)[:200])
                except Exception:
                    pass
            try:
                if not await _is_workflow_cancelled(execution_id, db):
                    await _complete_workflow_execution(execution_id, "failed", error=str(e), db=db)
            except Exception:
                async with async_session() as fallback_db:
                    if not await _is_workflow_cancelled(execution_id, fallback_db):
                        await _complete_workflow_execution(execution_id, "failed", error=str(e), db=fallback_db)
        finally:
            pop_llm_metric_context(llm_metric_token)
            if lock.locked():
                lock.release()
            ProjectLockManager.cleanup(project_id)


class ResumeInterruptedRequest(BaseModel):
    """方案12：恢复中断工作流的请求体。"""
    force: bool = False  # 强制恢复，即使有active workflow也恢复


@router.get(
    "/{project_id}/workflow/interrupted",
    summary="List interrupted editor workflows that can be resumed (方案12)",
)
async def list_interrupted_workflows(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """方案12：列出可恢复的中断工作流。"""
    query = await db.execute(
        select(WorkflowExecution).where(
            WorkflowExecution.project_id == str(project_id),
            WorkflowExecution.status == "interrupted",
        ).order_by(WorkflowExecution.updated_at.desc())
    )
    executions = query.scalars().all()
    result = []
    for execution in executions:
        # 统计各状态的步骤数
        steps_query = await db.execute(
            select(WorkflowStep).where(WorkflowStep.execution_id == str(execution.id))
        )
        steps = steps_query.scalars().all()
        step_summary = {
            "total": len(steps),
            "completed": sum(1 for s in steps if s.status == "completed"),
            "failed": sum(1 for s in steps if s.status == "failed"),
            "running": sum(1 for s in steps if s.status == "running"),
            "skipped": sum(1 for s in steps if s.status == "skipped"),
        }
        result.append({
            "execution_id": str(execution.id),
            "status": execution.status,
            "trigger_type": execution.trigger_type,
            "current_layer": execution.current_layer,
            "total_layers": execution.total_layers,
            "created_at": execution.created_at.isoformat() if execution.created_at else None,
            "updated_at": execution.updated_at.isoformat() if execution.updated_at else None,
            "error_message": execution.error_message,
            "step_summary": step_summary,
        })
    return {"interrupted_workflows": result, "count": len(result)}


@router.post(
    "/{project_id}/workflow/{execution_id}/resume-interrupted",
    summary="Resume an interrupted editor workflow from where it stopped (方案12)",
)
async def resume_interrupted_workflow(
    project_id: uuid.UUID,
    execution_id: str,
    data: ResumeInterruptedRequest | None = None,
    db: AsyncSession = Depends(get_db),
):
    """方案12：从interrupted状态恢复工作流执行。

    加载DB中的WorkflowStep状态，跳过已完成节点，重新执行未完成或失败的节点。
    """
    from app.services.project_lock import ProjectLockManager

    if data is None:
        data = ResumeInterruptedRequest()

    exec_query = await db.execute(
        select(WorkflowExecution).where(
            WorkflowExecution.id == execution_id,
            WorkflowExecution.project_id == str(project_id),
        )
    )
    execution = exec_query.scalar_one_or_none()
    if not execution:
        raise HTTPException(status_code=404, detail="Workflow execution record not found")
    retryable_failed_timeout = False
    timeout_layer: int | None = None
    if execution.status == "failed":
        failed_query = await db.execute(
            select(WorkflowStep).where(
                WorkflowStep.execution_id == execution_id,
                WorkflowStep.status == "failed",
            )
        )
        failed_steps = list(failed_query.scalars().all())
        timeout_owned = [
            step
            for step in failed_steps
            if (
                str(step.error_message or "").startswith("[TimeoutError]")
                or any(
                    isinstance(item, dict) and item.get("phase") == "timeout"
                    for item in (step.phase_trace or [])
                )
            )
        ]
        retryable_failed_timeout = bool(
            timeout_owned
            and all(
                "timeout" in str(step.error_message or "").lower()
                for step in failed_steps
            )
        )
        if timeout_owned:
            timeout_layer = min(int(step.layer or 0) for step in timeout_owned)
    if execution.status != "interrupted" and not retryable_failed_timeout:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Workflow status is '{execution.status}'. Only interrupted workflows "
                "or failed workflows owned by a persisted DAG timeout can be resumed."
            ),
        )

    if not data.force:
        active_query = await db.execute(
            select(WorkflowExecution.id).where(
                WorkflowExecution.project_id == str(project_id),
                WorkflowExecution.id != execution_id,
                WorkflowExecution.status.in_((
                    "running", "waiting_review", "pending_validator_retry",
                    "waiting_human_content_review", "waiting_human_system_review",
                )),
            )
        )
        if active_query.scalars().first():
            raise HTTPException(status_code=409, detail="Project already has a running generation task or pending human review")

    if ProjectLockManager.get_lock(str(project_id)).locked():
        raise HTTPException(status_code=409, detail="Project is executing another generation task, please retry later")

    if retryable_failed_timeout and timeout_layer is not None:
        propagated_query = await db.execute(
            select(WorkflowStep).where(
                WorkflowStep.execution_id == execution_id,
                WorkflowStep.layer < timeout_layer,
                WorkflowStep.status.in_(("failed", "pending_validator_retry")),
            )
        )
        now = _utcnow()
        for step in propagated_query.scalars().all():
            previous_output = dict(step.output_snapshot or {})
            previous_output["failed_resume"] = {
                "status": "propagated_failure_cleared",
                "timeout_layer": timeout_layer,
            }
            step.status = "completed"
            step.completed_at = now
            step.error_message = ""
            step.output_snapshot = to_json_safe(previous_output)

    # 标记为running，准备恢复
    execution.status = "running"
    execution.error_message = None
    await db.commit()

    # 后台异步执行恢复
    _spawn_workflow_task(execution_id, _run_editor_generation_resume(
        execution_id=execution_id,
        project_id=str(project_id),
    ))

    return {
        "execution_id": execution_id,
        "status": "running",
        "resume": True,
        "resume_from_timeout": retryable_failed_timeout,
        "timeout_layer": timeout_layer,
        "message": "Workflow resume started in background",
    }


def _interrupted_resume_overrides(persisted: dict) -> tuple[dict[int, str], dict]:
    """Recover validated scene text from a durable review/validator checkpoint."""
    resume_state = persisted.get("resume_state") or {}
    resume_review = persisted.get("review") or {}
    validator_retry = persisted.get("validator_retry") or {}
    overrides = {
        int(index): text
        for index, text in (resume_state.get("approved_scene_texts") or {}).items()
        if isinstance(text, str) and text
    }
    candidate_text = str(
        resume_review.get("candidate_text")
        or validator_retry.get("candidate_text")
        or resume_state.get("candidate_text")
        or ""
    ).strip()
    if candidate_text:
        scene_index = int(
            validator_retry.get(
                "scene_index",
                resume_review.get("scene_index", resume_state.get("scene_index", 0)),
            )
        )
        if _review_scope(resume_state, resume_review) == "chapter":
            chapter_overrides, _ = _chapter_review_scene_overrides(
                candidate_text,
                resume_state,
            )
            overrides.update(chapter_overrides)
        else:
            overrides[scene_index] = candidate_text
    return overrides, resume_state


async def _run_editor_generation_resume(
    execution_id: str,
    project_id: str,
):
    """方案12：后台执行工作流恢复。

    加载历史执行状态，构建DAG和handlers，调用resume_from继续执行未完成节点。

    注意：完整的恢复需要方案13（DAG对齐）配合，此处提供基础恢复框架。
    对于场景级DAG节点，能正确跳过已完成节点并恢复执行；
    对于头部4节点（editor_planning等），由editor_chat.py内联串行执行，不走DAGRuntime，
    其恢复需要方案13将它们迁入DAG调度后才能完整生效。
    """
    from app.db.db_models import async_session as _async_session, WorkflowExecution, WorkflowStep
    from app.services.editor_generation_dag_runtime import EditorGenerationDAGRuntime
    from app.services.project_lock import ProjectLockManager
    from sqlalchemy import select

    lock = ProjectLockManager.get_lock(project_id)
    await lock.acquire()
    try:
        # 加载execution记录，获取chapter_number等上下文
        async with _async_session() as db:
            exec_result = await db.execute(
                select(WorkflowExecution).where(WorkflowExecution.id == execution_id)
            )
            execution = exec_result.scalar_one_or_none()
            if execution is None:
                logger.error(f"resume: execution {execution_id} not found")
                return
            input_context = execution.input_context or {}
            persisted = _json_snapshot(execution.result_context or {})
            reviewed_scene_overrides, resume_state = _interrupted_resume_overrides(
                persisted
            )
            chapter_number = input_context.get("chapter_number")
            if chapter_number is None:
                # 从trigger_type解析（如 "editor_generate_ch3" -> 3）
                trigger = execution.trigger_type or ""
                if "ch" in trigger:
                    try:
                        chapter_number = int(trigger.split("ch")[-1])
                    except (ValueError, IndexError):
                        chapter_number = None
            if chapter_number is None:
                logger.error(f"resume: cannot determine chapter_number for execution {execution_id}")
                await _complete_workflow_execution(execution_id, "failed", error="cannot determine chapter_number for resume", db=db)
                return

        # 完整的恢复需要重建DAG和handlers，这需要复用_run_editor_generation的逻辑。
        # 由于_run_editor_generation的handlers是闭包定义的，无法直接复用，
        # 此处采取"重新执行整个章节生成"的策略，但保留已有的执行记录ID，
        # 让DAGRuntime的resume_from跳过已完成节点。
        #
        # 注意：这要求_run_editor_generation能接受execution_id参数并复用已有记录。
        # 当前_run_editor_generation已经接受execution_id参数，但其内部调用
        # editor_scene_runtime.run()而非resume_from()。
        #
        # 完整的接入需要修改_run_editor_generation，在execution_id已存在且DB中有
        # completed steps时，调用resume_from而非run。这是一个较大的改动，
        # 依赖方案13（DAG对齐）才能完整生效。
        #
        # 此处提供基础框架：调用_run_editor_generation重新执行，
        # 但传入已有的execution_id，让持久化逻辑复用记录。
        # The canonical runner owns the project lock for its full lifetime.
        # Release the short resume-bootstrap lock before entering it; otherwise
        # the runner sees its own bootstrap lock as a competing generation and
        # fails the interrupted execution immediately.
        if lock.locked():
            lock.release()
        ProjectLockManager.cleanup(project_id)

        await _run_editor_generation(
            execution_id=execution_id,
            project_id=project_id,
            chapter_number=chapter_number,
            pov_character=None,
            custom_instructions=None,
            reviewed_scene_overrides=reviewed_scene_overrides or None,
            review_version=int(resume_state.get("review_version", 0)),
            skip_editor_planning=bool(reviewed_scene_overrides),
            skip_reviewed_scene_validation=bool(reviewed_scene_overrides),
            validator_retry_resumed=bool(
                persisted.get("resumed_from_validator_retry")
            ),
            human_review_override=bool(
                persisted.get("human_review_override", {}).get("skip_content_recheck")
            ),
            resume_existing_dag=True,
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        try:
            async with _async_session() as fallback_db:
                await _complete_workflow_execution(execution_id, "failed", error=str(e), db=fallback_db)
        except Exception:
            pass
    finally:
        if lock.locked():
            lock.release()
        ProjectLockManager.cleanup(project_id)


@router.post(
    "/{project_id}/workflow/{execution_id}/resume-review",
    summary="Resume editor workflow from blocking scene recovery after human review",
)
async def resume_workflow_review(
    project_id: uuid.UUID,
    execution_id: str,
    data: ResumeWorkflowReviewRequest,
    db: AsyncSession = Depends(get_db),
):
    exec_query = await db.execute(
        select(WorkflowExecution).where(
            WorkflowExecution.id == execution_id,
            WorkflowExecution.project_id == project_id,
        )
    )
    execution = exec_query.scalar_one_or_none()
    if not execution:
        raise HTTPException(status_code=404, detail="Workflow execution record not found")
    persisted = _json_snapshot(execution.result_context or {})
    persisted_review = persisted.get("review") or {}
    failure_text = str(execution.error_message or "").lower()
    retryable_failed_review = bool(
        execution.status == "failed"
        and persisted_review.get("passed")
        and any(marker in failure_text for marker in ("timeout", "timed out"))
    )
    retryable_interrupted_review = bool(
        execution.status == "interrupted"
        and persisted_review.get("passed")
        and persisted_review.get("candidate_text")
    )
    if (
        execution.status != "waiting_review"
        and not retryable_failed_review
        and not retryable_interrupted_review
    ):
        raise HTTPException(status_code=409, detail="Workflow is not currently in human review status")

    resume_state = persisted.get("resume_state") or {}
    expected_version = int(resume_state.get("review_version", 0))
    if data.review_version is not None and data.review_version != expected_version:
        raise HTTPException(status_code=409, detail="Review page has expired, please refresh and retry")

    scene_index = int(resume_state.get("scene_index", -1))
    if scene_index < 0:
        raise HTTPException(status_code=409, detail="Human review breakpoint damaged, cannot continue")
    if data.action not in ("accept_edited", "retry", "accept_without_recheck"):
        raise HTTPException(status_code=400, detail="Unknown recovery operation")
    from app.services.project_lock import ProjectLockManager
    if ProjectLockManager.get_lock(str(project_id)).locked():
        raise HTTPException(status_code=409, detail="Project is executing another generation task, please retry later")

    overrides = {
        int(index): text
        for index, text in (resume_state.get("approved_scene_texts") or {}).items()
        if isinstance(text, str) and text
    }
    review = persisted.get("review", {})
    review_scope = _review_scope(resume_state, review)
    reviewed_scene_repair_indexes: set[int] = set()
    if data.action == "retry":
        retry_candidate = str(
            review.get("candidate_text")
            or resume_state.get("candidate_text")
            or ""
        ).strip()
        if not retry_candidate:
            raise HTTPException(
                status_code=409,
                detail="Review candidate text is missing; cannot perform in-place repair",
            )

        if review_scope == "chapter":
            overrides, chapter_alignment = _chapter_review_scene_overrides(
                retry_candidate,
                resume_state,
            )
            reviewed_scene_repair_indexes = set(overrides)
            persisted["chapter_review_alignment"] = {
                "method": chapter_alignment.get("method"),
                "span_count": len(chapter_alignment.get("spans") or []),
                "warnings": chapter_alignment.get("warnings", []),
            }
        else:
            overrides[scene_index] = retry_candidate
            reviewed_scene_repair_indexes = {scene_index}

        persisted["review_retry"] = {
            "mode": "in_place_repair",
            "scene_indexes": sorted(reviewed_scene_repair_indexes),
            "source_text_hashes": {
                str(index): hashlib.md5(overrides[index].encode()).hexdigest()[:12]
                for index in sorted(reviewed_scene_repair_indexes)
            },
            "skip_editor_planning": True,
            "skip_chapter_writer": True,
        }
    if data.action in ("accept_edited", "accept_without_recheck"):
        human_override = data.action == "accept_without_recheck"
        edited_text = (data.edited_text or "").strip()
        if not edited_text:
            raise HTTPException(status_code=400, detail="Edited text cannot be empty")

        # Review version consistency: verify hash on commit, auto re-verify if inconsistent
        current_text_hash = review.get("current_text_hash", "")
        # If current_text_hash is missing, derive from candidate_text
        if not current_text_hash:
            candidate_text = review.get("candidate_text", "")
            if candidate_text:
                current_text_hash = hashlib.md5(candidate_text.encode()).hexdigest()[:12]
        edited_hash = hashlib.md5(edited_text.encode()).hexdigest()[:12]
        review_passed = bool(review.get("passed")) and edited_hash == current_text_hash

        if (
            not human_override
            and review_scope == "scene"
            and current_text_hash
            and edited_hash != current_text_hash
        ):
            # Text has changed, auto re-verify
            try:
                from app.services.quality_gate import QualityGate
                from app.services.state_manager import StateManager

                quality_gate = QualityGate()
                state_manager = StateManager()
                story_state = await state_manager.get_state(str(project_id))
                project = await db.get(Project, project_id)
                previous_scene_context = _review_previous_scene_context(
                    resume_state,
                    int(resume_state.get("scene_index", 0)),
                )

                gate_result = await quality_gate.evaluate({
                    "generated_text": edited_text,
                    "scene_contract": resume_state.get("scene_contract", {}),
                    "chapter_state": resume_state.get("chapter_state", {}),
                    "character_cards": resume_state.get("character_cards", []),
                    "character_names": resume_state.get("character_names", []),
                    "project": project,
                    "project_id": str(project_id),
                    "chapter_number": int(resume_state.get("chapter_number", 0)),
                    "scene_index": int(resume_state.get("scene_index", 0)),
                    "db": db,
                    "core_facts": resume_state.get("core_facts", {}),
                    "current_state": story_state.model_dump() if story_state else {},
                    "review_version": int(review.get("review_version", 0)) + 1,
                    **previous_scene_context,
                }, level="full")

                new_violations = gate_result.get("violations", [])
                review_version = int(review.get("review_version", 0)) + 1
                for v in new_violations:
                    if isinstance(v, dict):
                        v["text_hash"] = edited_hash
                        v["review_version"] = review_version
                        v["is_stale"] = False

                # Check if there are still blocking issues
                blocking_count = sum(
                    1 for v in new_violations
                    if isinstance(v, dict) and v.get("blocks_commit") and not v.get("is_stale") and not v.get("is_system_issue")
                )
                review["violations"] = new_violations
                review["guidance"] = _build_review_guidance(new_violations, review.get("attempts", []))
                review["current_text_hash"] = edited_hash
                review["review_version"] = review_version
                review["candidate_text"] = edited_text
                review["passed"] = blocking_count == 0
                resume_state["candidate_text"] = edited_text
                resume_state["current_text_hash"] = edited_hash
                resume_state["review_version"] = review_version
                if blocking_count > 0:
                    # Has blocking issues, do not continue, return re-verify result
                    await _save_content_review(execution, persisted, review, resume_state, db)
                    raise HTTPException(
                        status_code=409,
                        detail=f"Text has changed and re-verify found {blocking_count} blocking issues, please handle first",
                    )
                review_passed = True
            except HTTPException:
                raise
            except Exception as exc:
                logger.warning(
                    "Manual review text re-verify failed; canonical workflow will validate it again: %s",
                    exc,
                )
                review_passed = False

        review["candidate_text"] = edited_text
        review["current_text_hash"] = edited_hash
        resume_state["candidate_text"] = edited_text
        resume_state["current_text_hash"] = edited_hash
        review_version = int(review.get("review_version", expected_version))
        persisted["human_revision_accepted"] = True
        persisted["manual_acceptance"] = {
            "status": "human_override" if human_override else "resuming",
            "review_scope": review_scope,
            "text_hash": edited_hash,
            "canonical_commit_required": True,
        }
        if human_override:
            unresolved_issue_ids = [
                str(issue.get("issue_id") or issue.get("violation_id"))
                for issue in review.get("violations", [])
                if isinstance(issue, dict)
                and (issue.get("issue_id") or issue.get("violation_id"))
                and not issue.get("is_stale")
                and not issue.get("is_system_issue")
                and str(issue.get("review_status") or "open") not in {"ignored", "resolved"}
                and (bool(issue.get("blocks_commit")) or str(issue.get("review_status") or "open") == "open")
            ]
            persisted["human_review_override"] = {
                "skip_content_recheck": True,
                "source": "manual_accept_without_recheck",
                "review_version": review_version,
                "review_scope": review_scope,
                "text_hash": edited_hash,
                "unresolved_issue_ids": unresolved_issue_ids,
            }
        else:
            persisted.pop("human_review_override", None)
        return await _resume_review_candidate(
            execution=execution,
            project_id=project_id,
            persisted=persisted,
            review=review,
            resume_state=resume_state,
            candidate_text=edited_text,
            review_version=review_version,
            source=(
                "manual_accept_without_recheck"
                if human_override
                else "manual_accept_edited"
            ),
            already_validated=review_passed,
            human_override=human_override,
            db=db,
        )

    resume_prepared = await _prepare_workflow_resume(execution, db=db)
    if not resume_prepared:
        raise HTTPException(status_code=409, detail="Cancelled workflow cannot be resumed")
    execution.result_context = _json_snapshot({
        **persisted,
        "resume_state": resume_state,
        "review": review,
        "resumed_from_review": True,
        "review_version": expected_version,
    })
    await db.commit()
    _broadcast_workflow_event(execution_id, {
        "type": "execution_update",
        "status": "running",
        "timestamp": _utcnow().isoformat(),
    })

    _spawn_workflow_task(execution_id, _run_editor_generation(
        execution_id=execution_id,
        project_id=str(project_id),
        chapter_number=int(resume_state["chapter_number"]),
        pov_character=resume_state.get("pov_character"),
        custom_instructions=resume_state.get("custom_instructions"),
        reviewed_scene_overrides=overrides,
        review_version=expected_version,
        skip_editor_planning=True,
        skip_reviewed_scene_validation=True,
        reviewed_scene_repair_indexes=reviewed_scene_repair_indexes,
    ))
    return {"execution_id": execution_id, "status": "running"}


async def _get_content_review_execution(
    project_id: uuid.UUID,
    execution_id: str,
    db: AsyncSession,
) -> tuple[WorkflowExecution, dict, dict, dict]:
    exec_query = await db.execute(
        select(WorkflowExecution).where(
            WorkflowExecution.id == execution_id,
            WorkflowExecution.project_id == project_id,
        )
    )
    execution = exec_query.scalar_one_or_none()
    if not execution:
        raise HTTPException(status_code=404, detail="Workflow execution record not found")
    if execution.status != "waiting_review":
        raise HTTPException(status_code=409, detail="Workflow is not currently in text human review status")
    persisted = dict(execution.result_context or {})
    review = dict(persisted.get("review") or {})
    resume_state = dict(persisted.get("resume_state") or {})
    if review.get("review_type") == "system_review":
        raise HTTPException(status_code=409, detail="System fault review does not support text issue operations")
    candidate_text = str(
        review.get("candidate_text")
        or resume_state.get("candidate_text")
        or ""
    )
    review["violations"] = _prepare_review_violations(review.get("violations", []), candidate_text)
    return execution, persisted, review, resume_state


def _find_review_issue(review: dict, issue_id: str) -> dict:
    for issue in review.get("violations", []):
        if isinstance(issue, dict) and issue.get("issue_id") == issue_id:
            return issue
    raise HTTPException(status_code=404, detail="Review issue not found or page expired")


_WORKBENCH_TARGET_PROTECTION_RE = re.compile(
    r"保留|保住|保持|维持|留下|"
    r"(?:不要|不许|不准|不得|不能|不可|别|勿).{0,12}"
    r"(?:删除|删去|删|移除|替换|改写|改掉|动)|"
    r"do\s+not\s+(?:delete|remove|replace|rewrite|change)|keep|preserve",
    re.IGNORECASE,
)


def _workbench_user_protects_target(issue: dict) -> bool:
    """Return whether the user explicitly protected this issue's target text.

    The validator target remains diagnostic evidence.  It must not silently
    become write ownership when the user's instruction says that exact text is
    the part that should survive the repair.
    """
    instruction = str(issue.get("user_repair_instruction") or "").strip()
    target_span = str(issue.get("target_span") or "").strip()
    if not instruction or not target_span:
        return False
    target_candidates = {
        target_span,
        target_span.strip("\"'“”‘’。！？!?\u2026 "),
    }
    target_mentioned = any(
        len(candidate) >= 2 and candidate in instruction
        for candidate in target_candidates
    )
    return bool(target_mentioned and _WORKBENCH_TARGET_PROTECTION_RE.search(instruction))


def _workbench_issue_repair_scope(
    candidate_text: str,
    issue: dict,
    resume_state: dict,
    review: dict,
) -> dict:
    """Bind one workbench issue to its owning scene and contract."""
    review_scene_index = int(resume_state.get("scene_index", 0) or 0)
    scene_index = issue.get("source_scene")
    if not isinstance(scene_index, int):
        case_issue = issue.get("review_case_issue") or {}
        scene_index = case_issue.get("scene_index") if isinstance(case_issue, dict) else None
    if not isinstance(scene_index, int):
        scene_index = review_scene_index

    source_scenes = [
        value
        for value in (issue.get("source_scenes") or [])
        if isinstance(value, int)
    ]
    affected_scene = issue.get("affected_scene")
    cross_scene_owner = bool(
        issue.get("boundary_conflict") is True
        and isinstance(affected_scene, int)
        and affected_scene != review_scene_index
        and affected_scene in source_scenes
    )
    if cross_scene_owner:
        contracts = _persisted_scene_contract_map(resume_state)
        approved_scene_texts = {
            int(index): str(text)
            for index, text in (resume_state.get("approved_scene_texts") or {}).items()
            if str(index).isdigit() and isinstance(text, str) and text.strip()
        }
        owner_text = approved_scene_texts.get(affected_scene, "").strip()
        owner_contract = contracts.get(affected_scene)
        if not owner_text or not isinstance(owner_contract, dict) or not owner_contract:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Cross-scene review checkpoint lacks the owner scene text or contract; "
                    "restart the workflow so canonical cross-scene repair can run safely"
                ),
            )
        scene_texts = dict(approved_scene_texts)
        scene_texts[review_scene_index] = candidate_text
        return {
            "chapter_scope": False,
            "cross_scene_scope": True,
            "scene_index": affected_scene,
            "review_scene_index": review_scene_index,
            "candidate_text": owner_text,
            "review_candidate_text": candidate_text,
            "scene_contract": owner_contract,
            "scene_contracts": contracts,
            "scene_texts": scene_texts,
            "scene_order": sorted(scene_texts),
            "alignment": "cross_scene_owner",
        }

    issue_type_values = {
        str(issue.get(key) or "").strip().lower()
        for key in ("type", "violation_type", "metric", "metric_name")
        if issue.get(key)
    }
    if (
        _review_scope(resume_state, review) == "chapter"
        and (
            issue_type_values & _DETERMINISTIC_DASH_REPAIR_TYPES
            or "dash_per_1000" in issue_type_values
        )
    ):
        contracts = _persisted_scene_contract_map(resume_state)
        return {
            "chapter_scope": True,
            "whole_chapter_text": True,
            "cross_scene_scope": False,
            "scene_index": scene_index,
            "review_scene_index": review_scene_index,
            "candidate_text": candidate_text,
            "scene_contract": contracts.get(scene_index) or resume_state.get("scene_contract") or {},
            "scene_contracts": contracts,
            "scene_texts": {},
            "scene_order": [],
            "alignment": "whole_chapter_deterministic",
        }

    if _review_scope(resume_state, review) != "chapter":
        return {
            "chapter_scope": False,
            "cross_scene_scope": False,
            "scene_index": scene_index,
            "review_scene_index": review_scene_index,
            "candidate_text": candidate_text,
            "scene_contract": resume_state.get("scene_contract") or {},
            "scene_texts": {scene_index: candidate_text},
            "scene_order": [scene_index],
            "alignment": "scene_scope",
        }

    contracts = _persisted_scene_contract_map(resume_state)
    scene_texts, alignment = _chapter_review_scene_overrides(candidate_text, resume_state)
    target_span = str(
        issue.get("target_span")
        or issue.get("evidence_span")
        or ""
    ).strip()
    protected_target = _workbench_user_protects_target(issue)
    matching_scenes: list[int] = []
    if target_span:
        matching_scenes = [
            index
            for index, text in scene_texts.items()
            if target_span in str(text or "")
        ]
    external_protected_span: dict | None = None
    if target_span and target_span not in str(scene_texts.get(scene_index) or ""):
        if protected_target and matching_scenes:
            # The target is read-only evidence in another scene.  Keep the
            # validator/user-selected owner as the only write scope and let a
            # bounded semantic repair consolidate the duplicate material there.
            external_scene = matching_scenes[0]
            external_protected_span = {
                "span": target_span,
                "scene_index": external_scene,
                "expected_count": candidate_text.count(target_span),
            }
        elif len(matching_scenes) == 1:
            # Final chapter checks may conservatively label a chapter-level
            # conflict as scene 0. The exact evidence span is stronger write
            # ownership evidence than that fallback index.
            scene_index = matching_scenes[0]
    if scene_index not in contracts:
        # A chapter candidate must never be repaired with another scene's
        # contract.  Legacy checkpoints without the complete contract map are
        # revalidated through the canonical workflow instead.
        raise HTTPException(
            status_code=409,
            detail=(
                "Chapter review checkpoint lacks the target scene contract; "
                "run re-verify before smart repair"
            ),
        )
    local_text = str(scene_texts.get(scene_index) or "").strip()
    if not local_text:
        raise HTTPException(
            status_code=409,
            detail="Chapter candidate could not be aligned to the issue's scene; run re-verify",
        )
    scene_order = [
        int(item["scene_index"])
        for item in (resume_state.get("scene_map") or [])
        if isinstance(item, dict) and isinstance(item.get("scene_index"), int)
    ] or sorted(scene_texts)
    scope = {
        "chapter_scope": True,
        "cross_scene_scope": False,
        "scene_index": scene_index,
        "review_scene_index": review_scene_index,
        "candidate_text": local_text,
        "scene_contract": contracts[scene_index],
        "scene_texts": scene_texts,
        "scene_order": scene_order,
        "alignment": alignment.get("method"),
    }
    if protected_target and target_span:
        scope["protected_span_postconditions"] = [{
            "span": target_span,
            "expected_count": candidate_text.count(target_span),
        }]
    if external_protected_span:
        scope["external_protected_span"] = external_protected_span
    return scope


def _merge_workbench_repaired_scene(scope: dict, repaired_scene_text: str) -> str:
    if scope.get("whole_chapter_text"):
        return str(repaired_scene_text or "").strip()
    if not scope.get("chapter_scope"):
        return str(repaired_scene_text or "").strip()
    scene_texts = dict(scope.get("scene_texts") or {})
    scene_texts[int(scope["scene_index"])] = str(repaired_scene_text or "").strip()
    return "\n\n".join(
        str(scene_texts.get(index) or "").strip()
        for index in scope.get("scene_order") or sorted(scene_texts)
        if str(scene_texts.get(index) or "").strip()
    ).strip()


def _workbench_repaired_scene_texts(scope: dict, repaired_scene_text: str) -> dict[int, str]:
    """Return the post-repair scene map used to rebuild chapter boundaries."""
    scene_texts = {
        int(index): str(text or "").strip()
        for index, text in (scope.get("scene_texts") or {}).items()
        if str(index).lstrip("-").isdigit()
    }
    scene_texts[int(scope["scene_index"])] = str(repaired_scene_text or "").strip()
    return scene_texts


def _workbench_scene_paragraph_counts(scope: dict, repaired_scene_text: str) -> list[int]:
    """Recompute persisted scene boundaries after a local chapter edit."""
    if not scope.get("chapter_scope") or scope.get("whole_chapter_text"):
        return []
    scene_texts = _workbench_repaired_scene_texts(scope, repaired_scene_text)
    scene_order = scope.get("scene_order") or sorted(scene_texts)
    counts: list[int] = []
    for raw_index in scene_order:
        index = int(raw_index)
        paragraphs = [
            paragraph
            for paragraph in re.split(r"\n\s*\n", scene_texts.get(index, ""))
            if paragraph.strip()
        ]
        counts.append(len(paragraphs))
    return counts


def _validate_workbench_protected_spans(
    scope: dict,
    before_text: str,
    after_text: str,
) -> list[dict]:
    """Fail closed when a local candidate changes a user-protected span."""
    checks: list[dict] = []
    for raw in scope.get("protected_span_postconditions") or []:
        if not isinstance(raw, dict):
            continue
        span = str(raw.get("span") or "").strip()
        if not span:
            continue
        expected_count = int(raw.get("expected_count", before_text.count(span)) or 0)
        actual_count = after_text.count(span)
        checks.append({
            "span": span,
            "expected_count": expected_count,
            "actual_count": actual_count,
            "passed": actual_count == expected_count,
        })
    return checks


def _try_deterministic_workbench_repair(
    candidate_text: str,
    targeted_issue: dict,
    *,
    scene_index: int = 0,
) -> dict | None:
    type_values = {
        str(targeted_issue.get(key) or "").strip().lower()
        for key in ("type", "violation_type", "semantic_type", "original_type")
        if targeted_issue.get(key)
    }
    metric = str(targeted_issue.get("metric") or targeted_issue.get("metric_name") or "").strip().lower()
    detail_blob = " ".join(
        str(targeted_issue.get(key) or "")
        for key in ("detail", "reason", "expected_behavior", "repair_goal", "target_span")
    ).lower()
    is_missing_must_show = bool(
        type_values & {"missing_must_show", "missing_required_beat"}
        or metric in {"missing_must_show", "missing_required_beat"}
    )
    if is_missing_must_show:
        target_span = str(targeted_issue.get("target_span") or "").strip()
        expected = " ".join(
            str(targeted_issue.get(key) or "")
            for key in ("expected_behavior", "repair_goal", "detail")
        )
        quoted = [
            str(match).strip()
            for match in re.findall(
                r"(?:['\"“‘])([^'\"”’\n]{2,100})(?:['\"”’])",
                expected,
            )
            if str(match).strip()
        ]
        required_candidates = [
            phrase
            for phrase in quoted
            if any(marker in phrase for marker in ("……", "…", "..."))
        ] or quoted
        required_candidates = list(dict.fromkeys(required_candidates))
        if (
            target_span
            and candidate_text.count(target_span) == 1
            and len(required_candidates) == 1
            and required_candidates[0] not in candidate_text
        ):
            required = required_candidates[0]
            replacement = f"“{required}”"
            repaired_text = candidate_text.replace(target_span, replacement, 1)
            return {
                "repaired_text": repaired_text,
                "repairs": [{
                    "original": target_span,
                    "repaired": replacement,
                    "reason": "exact_required_utterance",
                    "violation_type": targeted_issue.get("type", "missing_must_show"),
                }],
                "success": True,
                "repair_engine": "deterministic_required_utterance",
            }

    is_dash_issue = bool(
        type_values & _DETERMINISTIC_DASH_REPAIR_TYPES
        or metric == "dash_per_1000"
        or ("dash" in detail_blob and "punctuation" in detail_blob)
        or ("破折号" in detail_blob and ("密度" in detail_blob or "滥用" in detail_blob))
    )
    if not is_dash_issue:
        return None

    from app.models.chapter_review import ChapterRepairOrder
    from app.services.fbi.chapter_repair_executor import ChapterRepairExecutor

    expected_after_repair = {}
    for source_key, target_key in (
        ("target_dash_count", "target_dash_count"),
        ("max_dash_count", "max_dash_count"),
        ("allowed_dash_count", "allowed_dash_count"),
        ("dash_budget", "dash_budget"),
        ("target_dash_per_1000", "target_dash_per_1000"),
        ("max_dash_per_1000", "max_dash_per_1000"),
        ("threshold", "target_dash_per_1000"),
    ):
        if targeted_issue.get(source_key) is not None:
            expected_after_repair[target_key] = targeted_issue.get(source_key)

    order = ChapterRepairOrder(
        order_id=str(targeted_issue.get("issue_id") or targeted_issue.get("violation_id") or "workbench-dash"),
        target_scenes=[scene_index],
        owner_scene=scene_index,
        repair_type="local_patch",
        repair_domain="anti_ai",
        priority=str(targeted_issue.get("severity") or "high")
        if str(targeted_issue.get("severity") or "high") in {"critical", "high", "medium", "low"}
        else "high",
        reason=str(targeted_issue.get("detail") or targeted_issue.get("reason") or ""),
        instruction=str(
            targeted_issue.get("repair_goal")
            or targeted_issue.get("expected_behavior")
            or "Reduce overused em dashes with natural Chinese punctuation."
        ),
        violation_details=[targeted_issue],
        expected_after_repair=expected_after_repair,
    )
    executor = ChapterRepairExecutor()
    violations = executor._build_violations_from_order(order)
    repaired_text = executor._deterministic_local_patch(order, candidate_text, violations)
    if not repaired_text or repaired_text == candidate_text:
        return None

    dash_check = executor._dash_density_check(order, candidate_text, repaired_text)
    if dash_check and not dash_check.get("passed"):
        return None

    return {
        "repaired_text": repaired_text,
        "repairs": [{
            "original": targeted_issue.get("target_span") or "——",
            "repaired": "deterministic punctuation normalization",
            "reason": "dash_density_budget",
            "violation_type": targeted_issue.get("type", ""),
            "dash_density_check": dash_check,
        }],
        "success": True,
        "repair_engine": "deterministic_dash",
        "dash_density_check": dash_check,
    }


def _apply_deterministic_final_gate_repairs(
    candidate_text: str,
    violations: list[dict],
) -> dict:
    """Apply only zero-LLM final-gate repairs to the whole chapter text."""
    repaired_text = str(candidate_text or "")
    repairs: list[dict] = []
    handled_issue_ids: list[str] = []
    for violation in violations or []:
        if not isinstance(violation, dict):
            continue
        type_values = {
            str(violation.get(key) or "").strip().lower()
            for key in ("type", "violation_type", "metric", "metric_name")
            if violation.get(key)
        }
        if not (
            type_values & _DETERMINISTIC_DASH_REPAIR_TYPES
            or "dash_per_1000" in type_values
        ):
            continue
        result = _try_deterministic_workbench_repair(
            repaired_text,
            violation,
            scene_index=int(violation.get("source_scene") or 0),
        )
        if result is None or not result.get("success"):
            continue
        next_text = str(result.get("repaired_text") or repaired_text)
        if next_text == repaired_text:
            continue
        repaired_text = next_text
        repairs.extend(result.get("repairs") or [])
        handled_issue_ids.append(str(
            violation.get("issue_id")
            or violation.get("violation_id")
            or violation.get("type")
            or "dash_per_1000"
        ))
    return {
        "applied": repaired_text != candidate_text,
        "repaired_text": repaired_text,
        "repair_count": len(repairs),
        "repairs": repairs,
        "handled_issue_ids": handled_issue_ids,
        "executor_path": "deterministic_final_gate",
        "llm_calls": 0,
    }


def _preserve_workbench_fact_repair_hint(plan, issue: dict, executor_cls) -> bool:
    """Keep concrete reviewer hints executable after FBI diagnosis normalization."""
    if issue.get("external_protected_span"):
        # The suggested literal replacement points at read-only evidence in a
        # different scene.  It must be handled by the bounded consolidation
        # route, never projected back into a deterministic replacement.
        return False
    suggested = str(issue.get("suggested_correction") or "").strip()
    if not suggested:
        suggested = executor_cls._extract_suggested_correction_text(str(issue.get("detail") or ""))
    if not suggested:
        return False

    old = (
        str(issue.get("text_claim") or "").strip()
        or str(issue.get("target_span") or issue.get("evidence_span") or "").strip()
    )
    fragments = executor_cls._replacement_fragments_from_suggestion(suggested)
    if not old or not fragments:
        return False

    replacement = fragments[0]
    new = executor_cls._rewrite_old_claim_with_replacement_fragment(old, replacement)
    if not new or new == old:
        new = replacement
    prose_suggestion = new or replacement

    changed = False
    for order in getattr(plan, "orders", []) or []:
        brief = dict(order.repair_brief or {})
        fact_goal = dict(brief.get("fact_repair_goal") or {})
        if not fact_goal:
            continue
        fact_goal.setdefault("text_claim", old)
        if str(fact_goal.get("text_claim") or "").strip() != old:
            continue

        fact_goal["suggested_correction"] = prose_suggestion
        fact_goal["required_relation"] = prose_suggestion
        fact_goal["required_entities"] = [replacement]
        fact_goal["forbidden_claims"] = [old]
        fact_goal["old_error_signatures"] = [old]
        brief["fact_repair_goal"] = fact_goal
        brief["target_behavior"] = prose_suggestion

        def _patch_targets_old(item: dict) -> bool:
            source = str(
                item.get("from")
                or item.get("old")
                or item.get("target_span")
                or item.get("source")
                or ""
            ).strip()
            return source == old

        patch_plan = [
            item for item in (brief.get("patch_plan") or [])
            if not (
                isinstance(item, dict)
                and (
                    _patch_targets_old(item)
                    or str(item.get("source") or "") == "fbi_review_minister"
                )
            )
        ]
        patch_plan.insert(0, {
            "operation": "replace_phrase",
            "from": old,
            "to": new,
            "scope": "target_scene",
            "source": "fbi_workbench_issue_hint",
        })
        brief["patch_plan"] = patch_plan
        # The reviewer hint is newer, localized authority.  A stale
        # ``blueprint_incomplete`` produced before this hint must not shadow
        # the newly executable patch_plan when build_revision_blueprint()
        # recompiles the mini-plan.
        brief.pop("revision_blueprint", None)
        brief.pop("tool_blueprint", None)
        repair_intent = dict(brief.get("repair_intent") or {})
        if repair_intent:
            intent_patch_plan = [
                item for item in (repair_intent.get("patch_plan") or [])
                if not (
                    isinstance(item, dict)
                    and (
                        _patch_targets_old(item)
                        or str(item.get("source") or "") == "fbi_review_minister"
                    )
                )
            ]
            intent_patch_plan.insert(0, {
                "operation": "replace_phrase",
                "from": old,
                "to": new,
                "scope": "target_scene",
                "source": "fbi_workbench_issue_hint",
            })
            repair_intent["patch_plan"] = intent_patch_plan
            repair_intent["target_behavior"] = prose_suggestion
            brief["repair_intent"] = repair_intent
        order.repair_brief = brief
        order.tool_commands = []
        changed = True
    return changed


def _route_workbench_semantic_repair(plan, issue: dict) -> bool:
    """Compile a non-literal review suggestion into one bounded creative unit.

    Reviewer suggestions are sometimes decisions rather than replacement text,
    for example "delete the line, or add a causal bridge".  Treating such a
    suggestion as an exact patch produces a tool-only plan that cannot change
    prose, while running another blueprint LLM only adds latency and can repeat
    the same ambiguity.  The normalized FBI order already contains the issue,
    authority and preservation envelope, so project that order directly onto
    the existing creative-work-unit lane and let SceneRepairer make one local
    candidate.  The normal protection audit and semantic recheck still decide
    whether that candidate is acceptable.
    """
    issue_id = str(
        issue.get("issue_id")
        or issue.get("violation_id")
        or issue.get("id")
        or ""
    ).strip()
    target_span = str(
        issue.get("target_span") or issue.get("evidence_span") or ""
    ).strip()
    expected = str(
        issue.get("expected_behavior")
        or issue.get("suggested_correction")
        or ""
    ).strip()
    detail = str(issue.get("detail") or "").strip()
    user_instruction = str(issue.get("user_repair_instruction") or "").strip()
    preserve_target = _workbench_user_protects_target(issue)
    external_protected_span = (
        dict(issue.get("external_protected_span") or {})
        if isinstance(issue.get("external_protected_span"), dict)
        else {}
    )
    required_spans = [target_span] if preserve_target and not external_protected_span else []
    changed = False

    for order in getattr(plan, "orders", []) or []:
        order_issue_ids = {
            str(item).strip()
            for item in (order.source_violation_ids or [])
            if str(item).strip()
        }
        if issue_id and order_issue_ids and issue_id not in order_issue_ids:
            continue

        owner_scene = order.owner_scene if order.owner_scene is not None else (
            order.target_scenes[0] if order.target_scenes else None
        )
        source_issue_ids = list(order_issue_ids) or ([issue_id] if issue_id else [order.order_id])
        rationale_parts = []
        if user_instruction:
            rationale_parts.append(f"User requirement (highest priority): {user_instruction}")
        rationale_parts.append(detail)
        if expected and expected not in detail:
            rationale_parts.append(f"Expected behavior: {expected}")
        rationale = "\n".join(part for part in rationale_parts if part).strip() or order.reason

        brief = dict(order.repair_brief or {})
        external_route = bool(
            external_protected_span
            and isinstance(external_protected_span.get("scene_index"), int)
            and external_protected_span.get("scene_index") != owner_scene
        )
        # Explicit commands have precedence in build_revision_blueprint().
        # Remove any stale exact projection so an ambiguous alternative cannot
        # be reintroduced as a no-op deterministic patch.
        brief.pop("revision_blueprint", None)
        brief.pop("tool_blueprint", None)
        route_reason = (
            "external_protected_span_in_readonly_scene"
            if external_route
            else "non_literal_review_suggestion"
        )
        brief["tool_commands"] = [{
            "operation": "llm_creative_rewrite",
            "scene_index": owner_scene,
            "target_span": target_span,
            "rationale": rationale,
            "source_issue_ids": source_issue_ids,
            "guards": {
                "preserve_facts": True,
                "preserve_scene_contract": True,
                "forbid_unrelated_edits": True,
            },
            "postconditions": {
                "required_spans": required_spans,
            } if required_spans else {},
            "evidence": {
                "target_span": target_span,
                "expected_behavior": expected,
                "route_reason": route_reason,
            },
        }]
        target_behavior = user_instruction or expected or detail
        if external_route:
            target_behavior = (
                f"{target_behavior}\n"
                f"Only scene {owner_scene} is writable. Consolidate the conflicting or duplicated material "
                f"there while scene {external_protected_span.get('scene_index')} and its protected span "
                "remain read-only and unchanged."
            ).strip()
            # Literal fact projections were derived from the validator's
            # suggested branch before the user chose which branch to preserve.
            # They are invalid for this write owner; the mandatory semantic
            # recheck remains the authoritative acceptance gate.
            brief.pop("fact_repair_goal", None)
            brief["patch_plan"] = []
            repair_intent = dict(brief.get("repair_intent") or {})
            if repair_intent:
                repair_intent["target_behavior"] = target_behavior
                repair_intent["patch_plan"] = []
                brief["repair_intent"] = repair_intent
            repair_goals = []
            for raw_goal in brief.get("repair_goals") or []:
                if not isinstance(raw_goal, dict):
                    continue
                goal = dict(raw_goal)
                goal["desired_state"] = target_behavior
                goal["prohibited_state"] = (
                    "The writable owner scene still duplicates the protected later event, "
                    "or the read-only protected span is changed."
                )
                repair_goals.append(goal)
            if repair_goals:
                brief["repair_goals"] = repair_goals
            edit_scope = dict(brief.get("edit_scope") or {})
            edit_scope["read_scope"] = list(dict.fromkeys([
                *(edit_scope.get("read_scope") or []),
                f"scene:{external_protected_span.get('scene_index')}",
            ]))
            edit_scope["write_scope"] = [f"scene:{owner_scene}"]
            edit_scope["target_scenes"] = [owner_scene]
            edit_scope["allowed_operations"] = [
                "delete_duplicate_passage",
                "condense_duplicate_passage",
                "insert_transition",
            ]
            brief["edit_scope"] = edit_scope
            brief["external_protected_span"] = external_protected_span
            brief["workbench_semantic_route"] = "bounded_cross_scene_consolidation"
            order.read_scope = list(dict.fromkeys([
                *(order.read_scope or []),
                f"scene:{external_protected_span.get('scene_index')}",
            ]))
            order.write_scope = [f"scene:{owner_scene}"]
            order.instruction = target_behavior
        else:
            brief["workbench_semantic_route"] = "bounded_creative_work_unit"
        brief["target_behavior"] = target_behavior
        if required_spans:
            brief["required_preserve_spans"] = required_spans
        order.repair_brief = brief
        order.tool_commands = []
        expected_after_repair = dict(order.expected_after_repair or {})
        preserve = list(expected_after_repair.get("preserve") or [])
        for span in required_spans:
            if span not in preserve:
                preserve.append(span)
        if preserve:
            expected_after_repair["preserve"] = preserve
        order.expected_after_repair = expected_after_repair
        changed = True

    return changed


async def _run_fbi_workbench_issue_repair(
    *,
    project_id: uuid.UUID,
    candidate_text: str,
    targeted_issue: dict,
    resume_state: dict,
    scene_contract: dict,
) -> dict | None:
    """Run the current FBI chapter repair pipeline for one review issue.

    The workbench smart-repair button is a trigger, not an independent repair
    system. This mini-plan keeps diagnosis, repair ordering, deterministic
    patches, protection audit, and Skill-contract injection inside FBI.
    """
    from app.models.chapter_review import ChapterReviewCase, SceneReviewPacket
    from app.services.fbi.chapter_case_intake import FBIChapterCaseIntakeService, FBIChapterRepairPlanner
    from app.services.fbi.chapter_repair_executor import ChapterRepairExecutor

    scene_index = int(resume_state.get("scene_index", 0) or 0)
    chapter_number = int(resume_state.get("chapter_number", 0) or 0)
    style_context = resume_state.get("style_context") or {}
    if not isinstance(style_context, dict) or not style_context:
        try:
            from app.services.memory_core import CoreMemoryService

            style_context = (
                await CoreMemoryService().get_active_style_profile(str(project_id))
                or {}
            )
        except Exception as exc:
            logger.warning("FBI workbench active style load failed: %s", exc)
            style_context = {}
    all_scene_texts = {
        int(index): str(text)
        for index, text in (
            resume_state.get("workbench_scene_texts")
            or resume_state.get("approved_scene_texts")
            or {}
        ).items()
        if str(index).isdigit() and isinstance(text, str) and text.strip()
    }
    review_scene_index = int(resume_state.get("review_scene_index", scene_index) or 0)
    review_candidate = str(resume_state.get("candidate_text") or "").strip()
    if review_candidate:
        all_scene_texts[review_scene_index] = review_candidate
    all_scene_texts[scene_index] = candidate_text
    all_scene_contracts = _persisted_scene_contract_map(resume_state)
    all_scene_contracts[scene_index] = scene_contract or {}

    issue = dict(targeted_issue or {})
    issue.setdefault("blocks_commit", True)
    issue.setdefault("repairable_by_text", True)
    issue.setdefault("repair_scope", issue.get("scope") or "prose_text")
    issue.setdefault("scope", issue.get("repair_scope") or "prose_text")
    issue.setdefault("source_scene", scene_index)
    issue.setdefault("source_scenes", [scene_index])
    issue.setdefault("repairability", "auto_fixable")
    localization_text = all_scene_texts.get(
        int(issue.get("source_scene"))
        if isinstance(issue.get("source_scene"), int)
        else scene_index,
        candidate_text,
    )
    issue = enrich_review_issue_locations(issue, localization_text)
    if str(issue.get("type") or "") == "missing_must_show":
        anchor = str(issue.get("target_span") or "").strip()
        if (
            issue.get("localization_status") != "localized"
            or not anchor
            or anchor not in candidate_text
        ):
            raise ValueError(
                "missing_must_show has no verified insertion anchor; refusing unsafe smart repair"
            )
    suggested = ChapterRepairExecutor._extract_suggested_correction_text(str(issue.get("detail") or ""))
    if suggested:
        issue["suggested_correction"] = suggested

    issue_packet_scene = (
        int(issue.get("source_scene"))
        if isinstance(issue.get("source_scene"), int)
        and int(issue.get("source_scene")) in all_scene_texts
        else scene_index
    )
    packets = [
        SceneReviewPacket(
            scene_index=index,
            candidate_text=text,
            scene_contract=all_scene_contracts.get(index, {}),
            blocking_violations=[issue] if index == issue_packet_scene else [],
            repairability=(
                "cross_scene_fixable"
                if issue.get("boundary_conflict") is True
                else "auto_fixable"
            ),
            review_version=int(resume_state.get("review_version", 0) or 0),
        )
        for index, text in sorted(all_scene_texts.items())
    ]
    case = ChapterReviewCase(
        project_id=str(project_id),
        chapter_number=chapter_number,
        chapter_initial_state=resume_state.get("chapter_state") or {},
        chapter_outline_contract=resume_state.get("chapter_outline_contract") or {},
        scene_packets=packets,
        chapter_draft_hash=hashlib.md5(candidate_text.encode("utf-8")).hexdigest()[:12],
        review_round=int(resume_state.get("review_version", 0) or 0),
    )
    validated_case = await FBIChapterCaseIntakeService().intake(case)
    # A concrete reviewer correction can be compiled directly from the
    # normalized repair order.  Avoid a costly blueprint LLM round-trip that
    # would be discarded immediately by _preserve_workbench_fact_repair_hint.
    plan = await FBIChapterRepairPlanner().plan(
        validated_case,
        run_blueprint_agent=not bool(suggested),
    )
    if plan.is_clean() or not plan.orders:
        return None
    hint_preserved = _preserve_workbench_fact_repair_hint(plan, issue, ChapterRepairExecutor)
    semantic_route_compiled = False
    if suggested and not hint_preserved:
        # A non-literal/alternative suggestion is a bounded semantic edit, not
        # an exact string patch.  Compile it directly into the existing FBI
        # creative work-unit lane instead of spending an extra blueprint call
        # that may emit a non-executable deterministic command.
        semantic_route_compiled = _route_workbench_semantic_repair(plan, issue)
    if not hint_preserved and not semantic_route_compiled and suggested:
        # Defensive fallback for an unusual plan whose orders cannot be mapped
        # to the single workbench issue.
        plan = await FBIChapterRepairPlanner().plan(validated_case)
        hint_preserved = _preserve_workbench_fact_repair_hint(
            plan,
            issue,
            ChapterRepairExecutor,
        )
        if not hint_preserved:
            semantic_route_compiled = _route_workbench_semantic_repair(plan, issue)
    if hint_preserved or semantic_route_compiled:
        from app.services.fbi.work_unit_builder import build_revision_blueprint

        plan.revision_blueprint = build_revision_blueprint(plan.case_id, plan.orders)
        plan.work_units = list(plan.revision_blueprint.work_units)

    executor = ChapterRepairExecutor()
    scene_texts = dict(all_scene_texts)
    previous_scene_ending = str(resume_state.get("previous_scene_ending") or "")
    previous_scenes_summary = str(resume_state.get("previous_scenes_summary") or "")
    updated_texts, updated_plan = await executor.execute(
        plan,
        scene_texts,
        context={
            "project_id": str(project_id),
            "chapter_number": chapter_number,
            "scene_contracts": all_scene_contracts,
            "scene_truth_snapshots": {scene_index: resume_state.get("scene_truth_snapshot")},
            "chapter_state": resume_state.get("chapter_state") or {},
            "chapter_scene_context": "\n\n".join(
                text for _, text in sorted(all_scene_texts.items())
            ),
            "previous_scene_endings": {scene_index - 1: previous_scene_ending}
            if previous_scene_ending else {},
            "previous_scenes_summary": previous_scenes_summary,
            "word_budgets": {scene_index: resume_state.get("word_budget") or {}},
            "force_change": True,
            "workbench_smart_repair": True,
            "style_context": style_context,
            "style_profile": style_context,
            "style_prompt": style_context.get("style_prompt", ""),
            "style_embedding": style_context.get("style_embedding") or {},
            "persona_card": style_context.get("persona_card") or {},
        },
        case_file={
            "all_scene_texts": all_scene_texts,
            "all_scene_contracts": all_scene_contracts,
            "chapter_state": resume_state.get("chapter_state") or {},
            "review_packets": {
                packet.scene_index: packet.model_dump()
                for packet in packets
            },
            "repair_plan": plan,
            "scene_truth_snapshots": {scene_index: resume_state.get("scene_truth_snapshot")},
            "style_context": style_context,
            "style_profile": style_context,
        },
    )
    repaired_text = (updated_texts.get(scene_index) or candidate_text).strip()
    orders = [order.model_dump() for order in updated_plan.orders]
    blocking_failed = [
        order for order in updated_plan.orders
        if order.status == "failed" and not (order.repair_audit or {}).get("accepted")
    ]
    changed = repaired_text != candidate_text
    return {
        "repaired_text": repaired_text,
        "repairs": orders,
        "success": changed and not blocking_failed,
        "repair_engine": "fbi_chapter_mini_plan",
        "fbi_status": updated_plan.status,
        "fbi_case_id": updated_plan.case_id,
        "repair_plan": updated_plan.model_dump(),
        "failed_orders": [order.model_dump() for order in blocking_failed],
    }


def _check_review_version(review: dict, request_version: int | None):
    if request_version is not None and request_version != int(review.get("review_version", 0)):
        raise HTTPException(status_code=409, detail="Review page has expired, please refresh and retry")


async def _save_content_review(
    execution: WorkflowExecution,
    persisted: dict,
    review: dict,
    resume_state: dict,
    db: AsyncSession,
):
    if isinstance(resume_state, dict):
        if review.get("candidate_text") is not None:
            resume_state["candidate_text"] = review.get("candidate_text")
        if review.get("review_version") is not None:
            resume_state["review_version"] = review.get("review_version")
        if review.get("current_text_hash"):
            resume_state["current_text_hash"] = review.get("current_text_hash")
    review["guidance"] = _build_review_guidance(
        review.get("violations", []),
        review.get("attempts", []),
    )
    execution.result_context = _json_snapshot({
        **persisted,
        "review": review,
        "resume_state": resume_state,
    })
    execution.updated_at = _utcnow()
    await db.commit()


@router.post(
    "/{project_id}/workflow/{execution_id}/review/issues/{issue_id}/ignore",
    summary="Dismiss single human review issue",
)
async def ignore_workflow_review_issue(
    project_id: uuid.UUID,
    execution_id: str,
    issue_id: str,
    data: ReviewIssueActionRequest,
    db: AsyncSession = Depends(get_db),
):
    execution, persisted, review, resume_state = await _get_content_review_execution(
        project_id, execution_id, db,
    )
    _check_review_version(review, data.review_version)
    issue = _find_review_issue(review, issue_id)
    issue["review_status"] = "ignored"
    issue["ignored_at"] = _utcnow().isoformat()
    if data.edited_text is not None:
        review["candidate_text"] = data.edited_text
        resume_state["candidate_text"] = data.edited_text
    ignored_ids = list(resume_state.get("ignored_violation_ids") or [])
    if issue_id not in ignored_ids:
        ignored_ids.append(issue_id)
    resume_state["ignored_violation_ids"] = ignored_ids
    await _save_content_review(execution, persisted, review, resume_state, db)
    return {"execution_id": execution_id, "status": execution.status, "review": review}


@router.post(
    "/{project_id}/workflow/{execution_id}/review/issues/{issue_id}/chat",
    summary="Smart repair for single human review issue",
)
async def chat_workflow_review_issue(
    project_id: uuid.UUID,
    execution_id: str,
    issue_id: str,
    data: ReviewIssueActionRequest,
    db: AsyncSession = Depends(get_db),
):
    execution, persisted, review, resume_state = await _get_content_review_execution(
        project_id, execution_id, db,
    )
    _check_review_version(review, data.review_version)
    issue = _find_review_issue(review, issue_id)
    if issue.get("review_status") == "ignored":
        raise HTTPException(status_code=409, detail="This issue has been dismissed, refresh and re-enter review if needed")

    candidate_text = (data.edited_text or review.get("candidate_text") or "").strip()
    if not candidate_text:
        raise HTTPException(status_code=400, detail="Text to repair cannot be empty")

    workbench_style_context = resume_state.get("style_context") or {}
    if not isinstance(workbench_style_context, dict) or not workbench_style_context:
        try:
            from app.services.memory_core import CoreMemoryService

            workbench_style_context = (
                await CoreMemoryService().get_active_style_profile(str(project_id))
                or {}
            )
        except Exception as exc:
            logger.warning("review smart-repair active style load failed: %s", exc)
            workbench_style_context = {}

    # Compile the user decision before resolving write ownership.  In
    # particular, a protected target in a later scene is read-only evidence;
    # it must not pull the write scope away from the actual owner scene.
    instruction = (data.message or "").strip()
    repair_strategy = (data.repair_strategy or "").strip()
    targeted_issue = dict(issue)
    if instruction:
        targeted_issue["user_repair_instruction"] = instruction
        targeted_issue["detail"] = (
            f"{targeted_issue.get('detail', '')}\nUser additional requirement: {instruction}"
        )

    repair_scope = _workbench_issue_repair_scope(
        candidate_text,
        targeted_issue,
        resume_state,
        review,
    )
    repair_candidate_text = str(repair_scope["candidate_text"])
    repair_scene_index = int(repair_scope["scene_index"])
    repair_resume_state = dict(resume_state)
    repair_resume_state["style_context"] = workbench_style_context
    repair_resume_state["scene_index"] = repair_scene_index
    repair_resume_state["review_scene_index"] = int(repair_scope["review_scene_index"])
    repair_resume_state["scene_contract"] = dict(repair_scope["scene_contract"])
    if repair_scope.get("scene_contracts"):
        repair_resume_state["scene_contracts"] = dict(repair_scope["scene_contracts"])
    if repair_scope.get("scene_texts"):
        repair_resume_state["workbench_scene_texts"] = dict(repair_scope["scene_texts"])
    repair_resume_state.update(
        _review_previous_scene_context(resume_state, repair_scene_index)
    )
    if repair_scope["scene_contract"].get("word_budget"):
        repair_resume_state["word_budget"] = repair_scope["scene_contract"]["word_budget"]

    if repair_scope.get("cross_scene_scope"):
        targeted_issue["affected_scene"] = repair_scene_index
        targeted_issue["source_scenes"] = sorted(set(
            source
            for source in (targeted_issue.get("source_scenes") or [])
            if isinstance(source, int)
        ))
    elif repair_scope.get("external_protected_span"):
        external_protected_span = dict(repair_scope["external_protected_span"])
        targeted_issue["source_scene"] = repair_scene_index
        targeted_issue["source_scenes"] = sorted(set([
            repair_scene_index,
            int(external_protected_span["scene_index"]),
        ]))
        targeted_issue["external_protected_span"] = external_protected_span
    else:
        targeted_issue["source_scene"] = repair_scene_index
        targeted_issue["source_scenes"] = [repair_scene_index]
    scene_contract_for_repair = dict(repair_scope["scene_contract"])
    if _is_ending_state_issue(targeted_issue) and repair_strategy in _ENDING_REPAIR_STRATEGIES:
        ending_instruction = _build_ending_state_repair_instruction(
            targeted_issue,
            scene_contract_for_repair,
            repair_strategy,
        )
        instruction = f"{instruction}\n{ending_instruction}".strip() if instruction else ending_instruction
        targeted_issue["suggested_strategy"] = "rewrite_scene"
        targeted_issue["expected_behavior"] = (
            targeted_issue.get("expected_behavior")
            or scene_contract_for_repair.get("ending_state", "")
        )
    targeted_issue = enrich_review_issue_locations(targeted_issue, repair_candidate_text)
    for key in (
        "source_scene",
        "source_scenes",
        "target_span",
        "evidence",
        "evidence_spans",
        "repair_granularity",
        "location_confidence",
        "localization_status",
        "external_protected_span",
    ):
        if key in targeted_issue:
            issue[key] = targeted_issue[key]

    if str(targeted_issue.get("type") or "") == "temporal_anchor_count":
        from app.services.agent_skill_validator import AgentSkillValidator

        temporal_metrics = AgentSkillValidator()._temporal_consistency_metrics(
            repair_candidate_text,
            scene_contract_for_repair,
            {},
        )
        if int(temporal_metrics.get("temporal_anchor_count") or 0) > 0:
            raise HTTPException(
                status_code=409,
                detail="Temporal anchor finding is stale under the current scene contract; run re-verify",
            )

    # --- Check FBI repair mode ---
    fbi_case_id = None
    fbi_status = None
    repair_result = None
    repaired_text = repair_candidate_text
    deterministic_result = _try_deterministic_workbench_repair(
        repair_candidate_text,
        targeted_issue,
        scene_index=repair_scene_index,
    )
    if deterministic_result is not None:
        repair_result = deterministic_result
        repaired_text = str(deterministic_result.get("repaired_text") or repair_candidate_text).strip()

    if repair_result is None:
        try:
            repair_result = await _run_fbi_workbench_issue_repair(
                project_id=project_id,
                candidate_text=repair_candidate_text,
                targeted_issue=targeted_issue,
                resume_state=repair_resume_state,
                scene_contract=scene_contract_for_repair,
            )
            if repair_result is not None:
                repaired_text = str(repair_result.get("repaired_text") or repair_candidate_text).strip()
                fbi_case_id = repair_result.get("fbi_case_id")
                fbi_status = repair_result.get("fbi_status")
        except Exception as fbi_current_exc:
            logger.warning("Current FBI workbench repair failed: %s", fbi_current_exc, exc_info=True)
            raise HTTPException(status_code=502, detail=f"FBI smart repair failed: {fbi_current_exc}")

    if repair_result is None:
        raise HTTPException(status_code=502, detail="FBI smart repair did not produce a repair plan")
    if not repair_result.get("success"):
        failed_count = len(repair_result.get("failed_orders") or [])
        detail = (
            "FBI smart repair produced no effective text change"
            if str(repair_result.get("repaired_text") or repair_candidate_text).strip() == repair_candidate_text
            else f"FBI smart repair left {failed_count} failed order(s)"
        )
        raise HTTPException(status_code=502, detail=detail)

    chat = list(issue.get("chat") or [])
    if instruction:
        chat.append({"role": "user", "content": instruction})
    repaired_scene_text = repaired_text
    repairing_approved_scene = bool(repair_scope.get("cross_scene_scope"))
    if repairing_approved_scene:
        merged_repaired_text = candidate_text
        changed = repaired_scene_text != repair_candidate_text
    else:
        merged_repaired_text = _merge_workbench_repaired_scene(
            repair_scope,
            repaired_scene_text,
        )
        changed = merged_repaired_text != candidate_text
    protected_span_checks = _validate_workbench_protected_spans(
        repair_scope,
        candidate_text,
        merged_repaired_text,
    )
    failed_protected_span_checks = [
        check for check in protected_span_checks if not check.get("passed")
    ]
    if failed_protected_span_checks:
        raise HTTPException(
            status_code=502,
            detail=(
                "Smart repair candidate changed user-protected text; "
                "the candidate was rejected before entering the workbench"
            ),
        )
    issue_type = issue.get("type")
    budget = repair_resume_state.get("word_budget") or {}
    length_resolved = True
    if isinstance(budget, dict):
        if issue_type == "scene_too_long":
            hard_max = int(budget.get("hard_max_chars") or 0)
            length_resolved = not hard_max or len(repaired_scene_text) <= hard_max
        elif issue_type == "scene_too_short":
            hard_min = int(budget.get("hard_min_chars") or 0)
            length_resolved = not hard_min or len(repaired_scene_text) >= hard_min

    repair_candidate_ready = changed and length_resolved
    if repair_candidate_ready:
        summary = "Local repair candidate generated; automatic re-verify will decide whether the issue is resolved."
    elif not changed:
        summary = "This round of smart repair produced no effective changes, issue retained. Please add requirements or edit text directly."
    else:
        summary = "This round of smart repair has not met the length hard constraint, issue retained. Please continue repairing or edit text directly."
    chat.append({"role": "assistant", "content": summary})
    issue["chat"] = chat[-12:]
    issue["review_status"] = "pending_recheck" if repair_candidate_ready else "open"
    issue["repairs"] = repair_result.get("repairs", [])
    issue["repair_engine"] = repair_result.get("repair_engine", "scene_repairer")
    if protected_span_checks:
        issue["protected_span_checks"] = protected_span_checks
    if repair_result.get("fbi_case_id"):
        issue["fbi_case_id"] = repair_result.get("fbi_case_id")
        issue["fbi_status"] = repair_result.get("fbi_status")
    issue["revision_diff"] = _build_text_diff(
        repair_candidate_text if repairing_approved_scene else candidate_text,
        repaired_scene_text if repairing_approved_scene else merged_repaired_text,
    )
    if repairing_approved_scene and changed:
        approved_scene_texts = dict(resume_state.get("approved_scene_texts") or {})
        approved_scene_texts[repair_scene_index] = repaired_scene_text
        resume_state["approved_scene_texts"] = approved_scene_texts
        issue["repaired_scene_index"] = repair_scene_index
    else:
        review["candidate_text"] = merged_repaired_text
        resume_state["candidate_text"] = merged_repaired_text
        paragraph_counts = _workbench_scene_paragraph_counts(
            repair_scope,
            repaired_scene_text,
        )
        if paragraph_counts:
            resume_state["chapter_scene_paragraph_counts"] = paragraph_counts
    await _save_content_review(execution, persisted, review, resume_state, db)
    response = {
        "execution_id": execution_id,
        "status": execution.status,
        "review": review,
        "assistant_message": summary,
    }
    if fbi_case_id:
        response["fbi_case_id"] = fbi_case_id
        response["fbi_status"] = fbi_status
    return response


@router.post(
    "/{project_id}/workflow/{execution_id}/retry-validator",
    summary="Retry validation from validator temporarily unavailable state",
)
async def retry_validator(
    project_id: uuid.UUID,
    execution_id: str,
    db: AsyncSession = Depends(get_db),
):
    exec_query = await db.execute(
        select(WorkflowExecution).where(
            WorkflowExecution.id == execution_id,
            WorkflowExecution.project_id == project_id,
        )
    )
    execution = exec_query.scalar_one_or_none()
    if not execution:
        raise HTTPException(status_code=404, detail="Workflow execution record not found")
    if execution.status not in (
        "pending_validator_retry",
        "waiting_human_system_review",
        "waiting_review",
    ):
        raise HTTPException(status_code=409, detail="Workflow is not currently in validator retry status")

    persisted = execution.result_context or {}
    recovered_from_waiting_review = execution.status == "waiting_review"
    if execution.status == "waiting_review":
        recovered_retry = _validator_retry_from_parallel_review(persisted)
        if not recovered_retry:
            raise HTTPException(
                status_code=409,
                detail="Content review cannot be converted into validator retry",
            )
        execution.status = "pending_validator_retry"
        execution.error_message = recovered_retry["error_detail"]
        persisted = {
            **persisted,
            "validator_retry": recovered_retry,
            "recovered_validator_retry_breakpoint": True,
        }
        execution.result_context = _json_snapshot(persisted)
        await db.commit()

    validator_retry = persisted.get("validator_retry") or {}
    resume_state = persisted.get("resume_state") or {}

    scene_index = int(validator_retry.get("scene_index", resume_state.get("scene_index", -1)))
    candidate_text = validator_retry.get("candidate_text") or resume_state.get("candidate_text", "")
    if scene_index < 0 or not candidate_text:
        raise HTTPException(status_code=409, detail="Validator retry breakpoint damaged, cannot continue")

    from app.services.project_lock import ProjectLockManager
    if ProjectLockManager.get_lock(str(project_id)).locked():
        raise HTTPException(status_code=409, detail="Project is executing another generation task, please retry later")

    from app.services.quality_gate import QualityGate

    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    from app.services.state_manager import StateManager
    state_manager = StateManager()
    story_state = await state_manager.get_state(str(project_id))

    scene_contract = validator_retry.get("scene_contract") or resume_state.get("scene_contract") or {}

    if not scene_contract:
        step_query = await db.execute(
            select(WorkflowStep).where(
                WorkflowStep.execution_id == execution_id,
                WorkflowStep.agent_name == f"core_generation_{scene_index + 1}",
            )
        )
        step = step_query.scalar_one_or_none()
        if step and step.output_snapshot:
            scene_contract = step.output_snapshot.get("scene_contract", {})

    quality_gate = QualityGate()
    try:
        gate_result = await _evaluate_validator_retry_gate(
            quality_gate=quality_gate,
            validator_retry=validator_retry,
            context={
                "generated_text": candidate_text,
                "scene_contract": scene_contract,
                "chapter_state": validator_retry.get("chapter_state", resume_state.get("chapter_state", {})),
                "character_cards": validator_retry.get("character_cards", resume_state.get("character_cards", [])),
                "character_names": validator_retry.get("character_names", resume_state.get("character_names", [])),
                "project": project,
                "project_id": str(project_id),
                "chapter_number": int(resume_state.get("chapter_number", 0)),
                "scene_index": scene_index,
                "db": db,
                "core_facts": validator_retry.get("core_facts", resume_state.get("core_facts", {})),
                "current_state": story_state.model_dump(),
                "scene_truth_snapshot": validator_retry.get("scene_truth_snapshot", resume_state.get("scene_truth_snapshot")),
                "previous_scene_ending": validator_retry.get("previous_scene_ending", ""),
                "previous_scenes_summary": validator_retry.get("previous_scenes_summary", ""),
            },
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Validator retry evaluation failed: {str(e)}")

    violations = gate_result.get("violations", [])
    validator_violations = _validator_system_violations(violations)

    if gate_result.get("passed") and not validator_violations:
        resumed = await _resume_from_validator_retry(
            execution=execution,
            execution_id=execution_id,
            project_id=str(project_id),
            persisted=persisted,
            validator_retry=validator_retry,
            resume_state=resume_state,
            candidate_text=candidate_text,
            result_updates={
                "resumed_from_validator_retry": True,
                "gate_result": gate_result,
            },
            db=db,
        )
        if not resumed:
            raise HTTPException(status_code=409, detail="Cancelled workflow cannot be resumed")
        return {"execution_id": execution_id, "status": "running", "gate_passed": True}

    blocking_violations = [v for v in violations if v.get("blocks_commit")]

    if validator_violations:
        current_delayed = int(validator_retry.get("validator_retry_delayed_attempts", 0))
        max_delayed = 3
        if current_delayed + 1 >= max_delayed:
            execution.status = "waiting_human_system_review"
            execution.error_message = "Validator continuous failure, delayed re-verify budget exhausted, needs human confirmation of system state"
            _record_validator_failure(validator_retry, violations=validator_violations)

            review = _build_system_review(
                scene_index=scene_index,
                candidate_text=candidate_text,
                validator_retry=validator_retry,
                resume_state=resume_state,
                violations=validator_violations,
            )
            resume_state["review_version"] = review["review_version"]
            execution.result_context = _json_snapshot({
                "review": review,
                "resume_state": resume_state,
                "validator_retry": validator_retry,
            })
            await db.commit()

            _broadcast_workflow_event(execution_id, {
                "type": "execution_update",
                "status": "waiting_human_system_review",
                "timestamp": _utcnow().isoformat(),
            })

            return {
                "execution_id": execution_id,
                "status": "waiting_human_system_review",
                "gate_passed": False,
                "message": "Validator continuous failure, delayed re-verify budget exhausted, needs human confirmation of system state",
            }

        execution.result_context = {
            **persisted,
            "last_retry_gate_result": gate_result,
            "last_retry_at": _utcnow().isoformat(),
        }
        _record_validator_failure(validator_retry, violations=validator_violations)
        execution.result_context["validator_retry"] = validator_retry
        await db.commit()
        if recovered_from_waiting_review:
            retry_delays = validator_retry.get(
                "validator_retry_delays",
                [5, 15, 45],
            )
            delayed_attempts = int(
                validator_retry.get("validator_retry_delayed_attempts", 0)
            )
            remaining_delays = retry_delays[delayed_attempts:]
            if remaining_delays:
                _schedule_delayed_validator_retries(
                    execution_id=execution_id,
                    project_id=str(project_id),
                    delays=remaining_delays,
                )
        return {
            "execution_id": execution_id,
            "status": "pending_validator_retry",
            "gate_passed": False,
            "message": "Validator still unavailable, please retry later",
        }

    remaining_blocking = blocking_violations
    if remaining_blocking and candidate_text:
        recovery = await _recover_candidate_after_validator(
            candidate_text=candidate_text,
            validator_retry=validator_retry,
            resume_state=resume_state,
            project=project,
            project_id=str(project_id),
            db=db,
        )
        if not recovery.get("commit_blocked") and recovery.get("generated_text"):
            resumed = await _resume_from_validator_retry(
                execution=execution,
                execution_id=execution_id,
                project_id=str(project_id),
                persisted=persisted,
                validator_retry=validator_retry,
                resume_state=resume_state,
                candidate_text=recovery["generated_text"],
                result_updates={
                    "resumed_from_validator_retry": True,
                    "auto_repaired_after_validator_retry": True,
                    "recovery": recovery,
                },
                db=db,
            )
            if not resumed:
                raise HTTPException(status_code=409, detail="Cancelled workflow cannot be resumed")
            return {
                "execution_id": execution_id,
                "status": "running",
                "gate_passed": True,
                "auto_repaired": True,
            }

        gen_step = f"core_generation_{scene_index + 1}"
        check_step = f"consistency_check_{scene_index + 1}"
        error_detail = "Auto-fix budget exhausted, please confirm remaining conflicts before continuing"
        pipeline_output = validator_retry.get("pipeline_output", {})
        repaired_candidate = recovery.get("generated_text") or candidate_text
        resume_state["candidate_text"] = repaired_candidate

        await _pause_for_human_review(
            execution_id=execution_id,
            gen_step=gen_step,
            check_step=check_step,
            error_detail=error_detail,
            pipeline_output={
                **pipeline_output,
                "final_violations": (recovery.get("final_report") or gate_result).get("violations", []),
                "attempts": recovery.get("attempts", []),
                "gate_result": gate_result,
            },
            resume_state=resume_state,
            db=db,
            review_type="content_review",
        )
        return {
            "execution_id": execution_id,
            "status": "waiting_review",
            "gate_passed": False,
            "message": "Validator retry found blocking violations, transferred to human review",
        }

    resumed = await _resume_from_validator_retry(
        execution=execution,
        execution_id=execution_id,
        project_id=str(project_id),
        persisted=persisted,
        validator_retry=validator_retry,
        resume_state=resume_state,
        candidate_text=candidate_text,
        result_updates={
            "resumed_from_validator_retry": True,
            "validator_retry_degraded_resume": True,
            "last_retry_gate_result": gate_result,
        },
        db=db,
    )
    if not resumed:
        raise HTTPException(status_code=409, detail="Cancelled workflow cannot be resumed")

    return {
        "execution_id": execution_id,
        "status": "running",
        "gate_passed": False,
        "message": "Validator retry has no blocking violations, resumed with degraded validation trace",
    }


@router.post(
    "/{project_id}/workflow/{execution_id}/cancel",
    summary="Terminate running workflow execution",
)
async def cancel_workflow_execution(
    project_id: uuid.UUID,
    execution_id: str,
    db: AsyncSession = Depends(get_db),
):
    await _cleanup_stale_running_workflows(db, project_id=project_id)
    exec_query = await db.execute(
        select(WorkflowExecution).where(
            WorkflowExecution.id == execution_id,
            WorkflowExecution.project_id == project_id,
        )
    )
    execution = exec_query.scalar_one_or_none()
    if not execution:
        raise HTTPException(status_code=404, detail="Workflow execution record not found")

    cancellable = {
        "failed",
        "running",
        "pending_validator_retry",
        "waiting_human_content_review",
        "waiting_human_system_review",
        "waiting_review",
    }
    if execution.status not in cancellable:
        raise HTTPException(
            status_code=400,
            detail=f"Current status '{execution.status}' cannot be terminated, only {cancellable} status supports termination",
        )

    execution.status = "cancelled"
    execution.error_message = "User manually terminated workflow"
    execution.updated_at = _utcnow()
    await _finalize_open_workflow_steps(
        execution_id,
        status="cancelled",
        error="User manually terminated workflow",
        db=db,
    )
    await db.commit()
    tracked_tasks = list(_WORKFLOW_TASKS.get(str(execution_id)) or [])
    cancelled_tasks = _cancel_workflow_tasks(execution_id)
    await _await_cancelled_workflow_tasks(tracked_tasks)

    chapter_number = _workflow_chapter_number(execution)
    cleanup: dict = {}
    if chapter_number is not None:
        from app.db.db_models import Chapter, ChapterSnapshot
        from app.services.chapter_commit_service import purge_chapter_artifacts

        chapter_result = await db.execute(
            select(Chapter).where(
                Chapter.project_id == project_id,
                Chapter.chapter_number == chapter_number,
            )
        )
        chapter = chapter_result.scalar_one_or_none()
        committed_snapshot = (
            await db.execute(
                select(ChapterSnapshot).where(
                    ChapterSnapshot.project_id == project_id,
                    ChapterSnapshot.chapter_number == chapter_number,
                    ChapterSnapshot.stale == False,
                    ChapterSnapshot.execution_id != execution_id,
                )
            )
        ).scalar_one_or_none()
        full_chapter_cleanup = bool(
            chapter is None
            or (
                chapter.status != "committed"
                and committed_snapshot is None
            )
        )
        cleanup = await purge_chapter_artifacts(
            db,
            project_id=project_id,
            chapter_number=chapter_number,
            chapter_id=chapter.id if chapter is not None else None,
            execution_id=execution_id,
            created_at_from=execution.created_at,
            full_chapter=full_chapter_cleanup,
        )
        if chapter is not None and full_chapter_cleanup:
            chapter.content = ""
            chapter.status = "draft"
            await db.flush()
            from app.services.project_chapter_aggregate_service import (
                refresh_project_chapter_aggregates,
            )
            await refresh_project_chapter_aggregates(
                db,
                project_id=project_id,
            )

    steps_result = await db.execute(
        select(WorkflowStep).where(WorkflowStep.execution_id == execution_id)
    )
    for step in steps_result.scalars().all():
        step.input_snapshot = {}
        step.output_snapshot = {}
        step.phase_trace = []
        step.inputs_from = {}
        step.output_to = []
    execution.input_context = (
        {"chapter_number": chapter_number} if chapter_number is not None else {}
    )
    execution.result_context = {
        "cancelled": True,
        "chapter_number": chapter_number,
        "cleanup": cleanup,
    }
    await db.commit()

    _workflow_events[str(execution_id)] = []
    _broadcast_workflow_event(execution_id, {
        "type": "execution_update",
        "status": "cancelled",
        "timestamp": _utcnow().isoformat(),
        "cancelled_tasks": cancelled_tasks,
    })

    return {
        "execution_id": execution_id,
        "status": "cancelled",
        "message": "Workflow terminated",
        "cancelled_tasks": cancelled_tasks,
        "cleanup": cleanup,
    }


@router.get(
    "/{project_id}/workflow/{execution_id}",
    summary="Get workflow execution details",
)
async def get_workflow_execution(
    project_id: uuid.UUID,
    execution_id: str,
    db: AsyncSession = Depends(get_db),
):
    await _cleanup_stale_running_workflows(db, project_id=project_id)
    exec_query = await db.execute(
        select(WorkflowExecution).where(
            WorkflowExecution.id == execution_id,
            WorkflowExecution.project_id == project_id,
        )
    )
    execution = exec_query.scalar_one_or_none()
    if not execution:
        raise HTTPException(status_code=404, detail="Workflow execution record not found")

    steps_query = await db.execute(
        select(WorkflowStep).where(WorkflowStep.execution_id == execution_id).order_by(WorkflowStep.layer)
    )
    steps = steps_query.scalars().all()
    result_context = dict(execution.result_context or {})
    review = result_context.get("review")
    display_error_message = execution.error_message
    if isinstance(review, dict) and review.get("review_type") != "system_review":
        review = dict(review)
        candidate_text = str(
            review.get("candidate_text")
            or (result_context.get("resume_state") or {}).get("candidate_text")
            or ""
        )
        review["violations"] = _prepare_review_violations(review.get("violations", []), candidate_text)
        review["guidance"] = _build_review_guidance(
            review["violations"],
            review.get("attempts", []),
        )
        current_content_blockers = [
            violation
            for violation in review["violations"]
            if (
                isinstance(violation, dict)
                and violation.get("blocks_commit")
                and not violation.get("is_stale")
                and str(violation.get("review_status") or "open")
                not in {"ignored", "resolved"}
                and _classify_review_violation_for_commit(violation) != "validator_error"
            )
        ]
        if execution.status == "waiting_review" and current_content_blockers:
            display_error_message = (
                f"Current text review has {len(current_content_blockers)} content blocking issue(s)."
            )
            review["message"] = display_error_message
            review["error_code"] = "content_review_required"
            review["window_count"] = _aggregate_review_violations_by_window(
                current_content_blockers,
                result_context.get("resume_state") or {},
                candidate_text,
            )
        elif execution.status == "waiting_review":
            review["window_count"] = 0
        result_context["review"] = review

    return {
        "id": str(execution.id),
        "project_id": str(execution.project_id),
        "status": execution.status,
        "trigger_type": execution.trigger_type,
        "current_layer": execution.current_layer,
        "total_layers": execution.total_layers,
        "error_message": display_error_message,
        "result_context": result_context,
        "created_at": execution.created_at.isoformat() if execution.created_at else None,
        "updated_at": execution.updated_at.isoformat() if execution.updated_at else None,
        "steps": [
            {
                "id": str(s.id),
                "agent_name": s.agent_name,
                "layer": s.layer,
                "status": s.status,
                "started_at": s.started_at.isoformat() if s.started_at else None,
                "completed_at": s.completed_at.isoformat() if s.completed_at else None,
                "duration_ms": s.duration_ms,
                "output_snapshot": s.output_snapshot,
                "error_message": (
                    display_error_message
                    if s.status == "waiting_review" and display_error_message
                    else s.error_message
                ),
            }
            for s in steps
        ],
    }


@router.get(
    "/{project_id}/workflows",
    summary="Get project workflow execution list",
)
async def list_workflow_executions(
    project_id: uuid.UUID,
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    await _cleanup_stale_running_workflows(db, project_id=project_id)
    query = (
        select(WorkflowExecution)
        .where(WorkflowExecution.project_id == project_id)
        .order_by(WorkflowExecution.created_at.desc())
        .limit(limit)
    )
    result = await db.execute(query)
    executions = result.scalars().all()

    return [
        {
            "id": str(e.id),
            "status": e.status,
            "trigger_type": e.trigger_type,
            "current_layer": e.current_layer,
            "total_layers": e.total_layers,
            "created_at": e.created_at.isoformat() if e.created_at else None,
            "updated_at": e.updated_at.isoformat() if e.updated_at else None,
        }
        for e in executions
    ]


@router.get(
    "/{project_id}/workflow/{execution_id}/stream",
    summary="Workflow execution real-time event stream",
)
async def workflow_event_stream(
    project_id: uuid.UUID,
    execution_id: str,
):
    async def event_generator():
        import asyncio
        last_idx = 0
        idle_count = 0
        while True:
            events = _workflow_events.get(execution_id, [])
            new_events = events[last_idx:]
            for event in new_events:
                yield f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
                last_idx += 1

            exec_query_check = await _check_execution_status(execution_id)
            if exec_query_check in ("completed", "failed", "cancelled"):
                remaining = _workflow_events.get(execution_id, [])[last_idx:]
                for event in remaining:
                    yield f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
                yield "data: [DONE]\n\n"
                break

            if not new_events:
                idle_count += 1
            else:
                idle_count = 0

            await asyncio.sleep(0.5 if idle_count < 10 else 2)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _check_execution_status(execution_id: str) -> str | None:
    from app.db.db_models import async_session
    async with async_session() as session:
        await _cleanup_stale_running_workflows(session)
        result = await session.execute(
            select(WorkflowExecution.status).where(WorkflowExecution.id == execution_id)
        )
        status = result.scalar_one_or_none()
        return status


def _persisted_scene_contract_map(resume_state: dict) -> dict[int, dict]:
    raw = (resume_state or {}).get("scene_contracts") or {}
    items = raw.items() if isinstance(raw, dict) else enumerate(raw) if isinstance(raw, list) else []
    contracts: dict[int, dict] = {}
    for index, contract in items:
        try:
            scene_index = int(index)
        except (TypeError, ValueError):
            continue
        if isinstance(contract, dict) and contract:
            contracts[scene_index] = dict(contract)
    return contracts


def _review_previous_scene_context(
    resume_state: dict,
    scene_index: int,
) -> dict[str, str]:
    """Build bounded same-chapter chronology for a scene workbench check.

    ``current_state`` is the chapter-opening baseline. Approved earlier scenes
    are later evidence, so a scene-scoped recheck must see their explicit state
    transitions before deciding that the current text contradicts the prior
    chapter.
    """
    approved: dict[int, str] = {}
    for raw_index, text in ((resume_state or {}).get("approved_scene_texts") or {}).items():
        try:
            index = int(raw_index)
        except (TypeError, ValueError):
            continue
        normalized = str(text or "").strip()
        if index < scene_index and normalized:
            approved[index] = normalized
    if not approved:
        return {"previous_scene_ending": "", "previous_scenes_summary": ""}

    ordered = [approved[index] for index in sorted(approved)]
    immediate = approved.get(scene_index - 1) or ordered[-1]
    return {
        "previous_scene_ending": immediate[-1800:],
        "previous_scenes_summary": "\n\n".join(text[-600:] for text in ordered[-3:]),
    }


async def _workbench_skill_recheck(
    *,
    project_id: uuid.UUID,
    candidate_text: str,
    review: dict,
    resume_state: dict,
    db: AsyncSession,
) -> dict:
    """Run the final Skill validator set before a chapter workbench can exit.

    Older checkpoints did not persist all scene contracts.  Those checkpoints
    deliberately fall back to the canonical L4 convergence rerun instead of
    validating scenes against the wrong contract.
    """
    if _review_scope(resume_state, review) != "chapter":
        return {"executed": False, "allowed": True, "reason": "scene_scope"}

    contracts = _persisted_scene_contract_map(resume_state)
    scene_map = [
        item for item in (resume_state.get("scene_map") or [])
        if isinstance(item, dict) and isinstance(item.get("scene_index"), int)
    ]
    expected_indexes = sorted(item["scene_index"] for item in scene_map)
    if not expected_indexes or any(index not in contracts for index in expected_indexes):
        return {
            "executed": False,
            "allowed": True,
            "reason": "missing_complete_scene_contracts",
        }

    scene_texts, alignment = _chapter_review_scene_overrides(candidate_text, resume_state)
    if any(not str(scene_texts.get(index) or "").strip() for index in expected_indexes):
        return {
            "executed": False,
            "allowed": True,
            "reason": "incomplete_chapter_alignment",
            "alignment": alignment.get("method"),
        }

    from app.services.agent_skill_commit_gate import get_agent_skill_commit_gate

    first_contract = contracts[expected_indexes[0]]
    style_context = resume_state.get("style_context") or {}
    if not isinstance(style_context, dict) or not style_context:
        try:
            from app.services.memory_core import CoreMemoryService

            style_context = (
                await CoreMemoryService().get_active_style_profile(str(project_id))
                or {}
            )
        except Exception as exc:
            logger.warning("workbench Skill recheck active style load failed: %s", exc)
            style_context = {}
    result = await get_agent_skill_commit_gate().evaluate(
        project_id=str(project_id),
        db=db,
        final_text=candidate_text,
        scene_contract=first_contract,
        chapter_state=resume_state.get("chapter_state") or {},
        writing_mode_profile=first_contract.get("writing_mode_profile") or {},
        style_context=style_context,
        scene_units=[{
            "text": scene_texts[index],
            "scene_contract": contracts[index],
            "writing_mode_profile": contracts[index].get("writing_mode_profile") or {},
        } for index in expected_indexes],
        allow_llm_repair=False,
    )
    if result.get("allowed", False):
        return {
            "executed": True,
            "allowed": True,
            "reason": "",
            "violations": [],
            "alignment": alignment.get("method"),
        }

    from app.services.fbi.final_acceptance_delta import build_final_acceptance_case_delta
    from app.services.review_case_file import case_file_issues_to_violations

    case_delta = build_final_acceptance_case_delta(
        project_id=str(project_id),
        chapter_number=int(resume_state.get("chapter_number", 0) or 0),
        final_text=candidate_text,
        final_gate_result=result,
        acceptance_round=0,
        parent_case_id="",
        scene_texts=scene_texts,
    )
    # The Skill validator reports every contract miss, while the shared metric
    # registry assigns the product enforcement grade.  Final-acceptance case
    # construction applies that grade (hard correctness remains blocking;
    # subjective prose findings become advisories).  If no blocking issue
    # survives that normalization, keeping ``allowed=False`` would strand the
    # workbench with zero visible issues and contradict the same enforcement
    # policy used by QualityGate and FBI convergence.
    from app.models.review_case_file import ReviewCaseFile

    normalized_case = ReviewCaseFile.model_validate(case_delta)
    blocking_case_issues = [
        issue for issue in normalized_case.all_issues()
        if issue.blocks_commit
    ]
    violations = _prepare_review_violations(
        case_file_issues_to_violations(case_delta),
        candidate_text,
    )
    for violation in violations:
        if isinstance(violation, dict):
            violation["source"] = "workbench_skill_recheck"
    if not blocking_case_issues:
        return {
            "executed": True,
            "allowed": True,
            "reason": "soft_skill_findings_only",
            "violations": [],
            "advisory_count": len(normalized_case.all_issues()),
            "raw_reason": str(result.get("reason") or ""),
            "alignment": alignment.get("method"),
        }
    return {
        "executed": True,
        "allowed": False,
        "reason": str(result.get("reason") or "Skill validation failed"),
        "violations": violations,
        "alignment": alignment.get("method"),
    }


@router.post(
    "/{project_id}/workflow/{execution_id}/recheck",
    summary="Re-verify refresh: re-run quality gate based on current text",
)
async def recheck_scene(
    project_id: uuid.UUID,
    execution_id: str,
    data: dict,
    db: AsyncSession = Depends(get_db),
):
    """Re-run quality gate on current text, return violations based on latest text.

    Frontend should call this API after each smart repair to refresh the issue list.
    All returned violations include text_hash, frontend can use it to determine if expired.
    """
    candidate_text = data.get("candidate_text", "")
    requested_scene_index = data.get("scene_index", -1)

    if not candidate_text:
        raise HTTPException(status_code=400, detail="candidate_text cannot be empty")

    import hashlib
    text_hash = hashlib.md5(candidate_text.encode()).hexdigest()[:12]

    exec_query = await db.execute(
        select(WorkflowExecution).where(
            WorkflowExecution.id == execution_id,
            WorkflowExecution.project_id == project_id,
        )
    )
    execution = exec_query.scalar_one_or_none()
    if not execution:
        raise HTTPException(status_code=404, detail="Workflow execution record not found")
    if execution.status != "waiting_review":
        raise HTTPException(status_code=409, detail="Workflow is not currently waiting for content review")

    persisted = execution.result_context or {}
    resume_state = persisted.get("resume_state", {})
    review = persisted.get("review", {})
    violations = review.get("violations", [])
    try:
        scene_index = int(requested_scene_index)
    except (TypeError, ValueError):
        scene_index = -1
    if scene_index < 0:
        scene_index = int(resume_state.get("scene_index", 0))

    # Get scene_contract
    scene_contract = resume_state.get("scene_contract", {})
    if not scene_contract and scene_index >= 0:
        step_query = await db.execute(
            select(WorkflowStep).where(
                WorkflowStep.execution_id == execution_id,
                WorkflowStep.agent_name == f"core_generation_{int(scene_index) + 1}",
            )
        )
        step = step_query.scalar_one_or_none()
        if step and step.output_snapshot:
            scene_contract = step.output_snapshot.get("scene_contract", {})

    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    from app.services.state_manager import StateManager
    state_manager = StateManager()
    story_state = await state_manager.get_state(str(project_id))

    review_version = max(
        int(review.get("review_version", 0)),
        int(resume_state.get("review_version", 0)),
    ) + 1

    from app.services.quality_gate import QualityGate
    quality_gate = QualityGate()
    previous_scene_context = _review_previous_scene_context(
        resume_state,
        scene_index,
    )

    try:
        gate_result = await quality_gate.evaluate({
            "generated_text": candidate_text,
            "scene_contract": scene_contract,
            "chapter_state": resume_state.get("chapter_state", {}),
            "character_cards": resume_state.get("character_cards", []),
            "character_names": resume_state.get("character_names", []),
            "project": project,
            "project_id": str(project_id),
            "chapter_number": int(resume_state.get("chapter_number", 0)),
            "scene_index": int(scene_index) if scene_index is not None else 0,
            "db": db,
            "core_facts": resume_state.get("core_facts", {}),
            "current_state": story_state.model_dump(),
            "scene_truth_snapshot": resume_state.get("scene_truth_snapshot"),
            "review_version": review_version,
            **previous_scene_context,
        }, level="full")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Re-verify evaluation failed: {str(e)}")

    try:
        skill_recheck = await _workbench_skill_recheck(
            project_id=project_id,
            candidate_text=candidate_text,
            review=review,
            resume_state=resume_state,
            db=db,
        )
    except Exception as exc:
        logger.exception("Workbench Skill recheck failed: %s", exc)
        raise HTTPException(
            status_code=500,
            detail=f"Skill re-verify failed; review remains open: {exc}",
        )

    # Add text_hash + review_version + is_stale to all violations
    new_violations = _prepare_review_violations(
        [
            *(gate_result.get("violations", []) or []),
            *(skill_recheck.get("violations", []) or []),
        ],
        candidate_text,
    )
    for v in new_violations:
        if isinstance(v, dict):
            _annotate_boundary_conflict_sources(
                v,
                scene_index=scene_index,
                scene_contract=scene_contract,
            )
            v["text_hash"] = text_hash
            v["review_version"] = review_version
            v["is_stale"] = False

    # Mark violations as stale (text_hash mismatch)
    stale_violations = []
    for v in violations:
        if isinstance(v, dict):
            old_hash = v.get("text_hash", "")
            if old_hash and old_hash != text_hash:
                v["is_stale"] = True
                stale_violations.append(v)

    validator_violations = _validator_system_violations(new_violations)
    content_blocking = [
        violation
        for violation in new_violations
        if (
            isinstance(violation, dict)
            and violation.get("blocks_commit")
            and _classify_review_violation_for_commit(violation) != "validator_error"
        )
    ]
    passed = bool(
        gate_result.get("passed", False)
        and skill_recheck.get("allowed", True)
        and not validator_violations
    )
    if passed:
        review_message = "Current text re-verify passed. Resuming workflow automatically."
        review_error_code = ""
    elif content_blocking:
        review_message = (
            f"Current text re-verify found {len(content_blocking)} content blocking issue(s)."
        )
        review_error_code = "content_review_required"
    elif validator_violations:
        review_message = str(
            validator_violations[0].get("detail")
            or "Validator temporarily unavailable during content re-verify"
        )
        review_error_code = str(
            validator_violations[0].get("type") or "validator_unavailable"
        )
    else:
        review_message = review.get("message", "")
        review_error_code = review.get("error_code", "")

    # Persist re-verify result to execution.result_context
    updated_review = {
        **review,
        "violations": new_violations,
        "stale_violations": stale_violations,
        "guidance": _build_review_guidance(new_violations, review.get("attempts", [])),
        "review_version": review_version,
        "current_text_hash": text_hash,
        "candidate_text": candidate_text,
        "passed": passed,
        "message": review_message,
        "error_code": review_error_code,
        "skill_recheck": {
            "executed": bool(skill_recheck.get("executed")),
            "allowed": bool(skill_recheck.get("allowed", True)),
            "reason": str(skill_recheck.get("reason") or ""),
            "alignment": skill_recheck.get("alignment"),
            "violation_count": len(skill_recheck.get("violations") or []),
        },
    }
    updated_resume_state = dict(resume_state or {})
    updated_resume_state["candidate_text"] = candidate_text
    updated_resume_state["review_version"] = review_version
    updated_resume_state["current_text_hash"] = text_hash
    if not content_blocking:
        updated_resume_state.pop("validator_retry_deferred_by_content", None)
    if validator_violations and not content_blocking:
        error_detail = str(
            validator_violations[0].get("detail")
            or "Validator temporarily unavailable during content re-verify"
        )
        validator_retry_info = {
            "scene_index": scene_index,
            "candidate_text": candidate_text,
            "pipeline_output": gate_result,
            "error_code": validator_violations[0].get(
                "type",
                "validator_unavailable",
            ),
            "error_detail": error_detail,
            "scene_contract": scene_contract,
            "chapter_state": updated_resume_state.get("chapter_state", {}),
            "character_cards": updated_resume_state.get("character_cards", []),
            "character_names": updated_resume_state.get("character_names", []),
            "scene_truth_snapshot": updated_resume_state.get("scene_truth_snapshot"),
            **previous_scene_context,
            "core_facts": updated_resume_state.get("core_facts", {}),
            "word_budget": updated_resume_state.get("word_budget"),
            "validator_retry_delays": [5, 15, 45],
            "validator_retry_immediate_attempts": 0,
            "validator_retry_delayed_attempts": 0,
            "source_violations": validator_violations,
            "source": "workbench_recheck",
        }
        updated_resume_state["scene_recovery_status"] = "pending_validator_retry"
        execution.status = "pending_validator_retry"
        execution.error_message = error_detail
        execution.result_context = _json_snapshot({
            **persisted,
            "review": updated_review,
            "resume_state": updated_resume_state,
            "validator_retry": validator_retry_info,
        })
        await db.commit()
        _schedule_delayed_validator_retries(
            execution_id=execution_id,
            project_id=str(project_id),
            delays=validator_retry_info["validator_retry_delays"],
        )
        _broadcast_workflow_event(execution_id, {
            "type": "execution_update",
            "status": "pending_validator_retry",
            "timestamp": _utcnow().isoformat(),
        })
        return {
            "violations": new_violations,
            "stale_violations": stale_violations,
            "passed": False,
            "text_hash": text_hash,
            "review_version": review_version,
            "status": "pending_validator_retry",
            "auto_resumed": False,
            "system_retry_scheduled": True,
            "review": updated_review,
        }
    if passed:
        updated_resume_state["error_code"] = ""
        updated_resume_state["scene_recovery_status"] = "validated"
        execution.error_message = ""
        resume_result = await _resume_review_candidate(
            execution=execution,
            project_id=project_id,
            persisted=dict(persisted),
            review=updated_review,
            resume_state=updated_resume_state,
            candidate_text=candidate_text,
            review_version=review_version,
            source="auto_recheck_passed",
            already_validated=True,
            db=db,
        )
        return {
            "violations": new_violations,
            "stale_violations": stale_violations,
            "passed": True,
            "text_hash": text_hash,
            "review_version": review_version,
            **resume_result,
        }

    if content_blocking:
        execution.status = "waiting_review"
        execution.error_message = review_message
        updated_resume_state["scene_recovery_status"] = "waiting_review"
        updated_resume_state["validator_retry_deferred_by_content"] = bool(
            validator_violations
        )
        waiting_steps_query = await db.execute(
            select(WorkflowStep).where(
                WorkflowStep.execution_id == execution_id,
                WorkflowStep.status == "waiting_review",
            )
        )
        for waiting_step in waiting_steps_query.scalars().all():
            waiting_step.error_message = review_message

    execution.result_context = _json_snapshot({
        **persisted,
        "review": updated_review,
        "resume_state": updated_resume_state,
    })
    await db.commit()

    return {
        "violations": new_violations,
        "stale_violations": stale_violations,
        "passed": passed,
        "text_hash": text_hash,
        "review_version": review_version,
        "status": execution.status,
        "auto_resumed": False,
        "review": updated_review,
    }
