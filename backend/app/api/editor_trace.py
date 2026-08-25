"""Editor trace diagnostics API."""
from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.db_models import WorkflowExecution, WorkflowStep, get_db
from app.models.editor_trace import EditorTraceQuery
from app.services.editor_trace_collector import get_editor_trace_collector

router = APIRouter()


def _text_hash(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()[:12]


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _project_key(project_id: str) -> uuid.UUID | str:
    try:
        return uuid.UUID(project_id)
    except (TypeError, ValueError):
        return project_id


def _candidate_text_from_review(review: dict[str, Any]) -> str:
    for key in ("candidate_text", "repaired_text", "text", "content"):
        value = review.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


async def _candidate_text_from_steps(db: AsyncSession, execution_id: str) -> str:
    result = await db.execute(
        select(WorkflowStep)
        .where(WorkflowStep.execution_id == _project_key(execution_id))
        .order_by(WorkflowStep.layer.desc())
    )
    for step in result.scalars().all():
        snapshot = _as_dict(step.output_snapshot)
        for key in ("candidate_text", "repaired_text", "scene_text", "text", "content"):
            value = snapshot.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return ""


def _issues_based_on_current_text(review: dict[str, Any], current_hash: str) -> bool:
    violations = review.get("violations") or []
    if not isinstance(violations, list) or not violations:
        return True
    if not current_hash:
        return False

    issue_hashes = [
        issue.get("text_hash")
        for issue in violations
        if isinstance(issue, dict) and issue.get("text_hash")
    ]
    if not issue_hashes:
        return False
    return all(issue_hash == current_hash for issue_hash in issue_hashes)


@router.get("/summary/{project_id}")
async def get_project_summary(project_id: str, last_n: int = 10):
    """Return project-level editor trace summary."""
    collector = get_editor_trace_collector()
    return collector.get_project_summary(project_id, last_n=last_n)


@router.get("/{trace_id}")
async def get_trace(trace_id: str):
    """Return one trace by id."""
    collector = get_editor_trace_collector()
    trace = collector.get_trace(trace_id)
    if not trace:
        raise HTTPException(status_code=404, detail="Trace not found")
    return trace.model_dump()


@router.post("/query")
async def query_traces(query: EditorTraceQuery):
    """Query trace records."""
    collector = get_editor_trace_collector()
    traces = collector.query_traces(query)
    return {"traces": [t.model_dump() for t in traces], "count": len(traces)}


@router.get("/effectiveness/{project_id}/{execution_id}")
async def get_effectiveness_proof(
    project_id: str,
    execution_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Return the visible proof that V2 inputs and repair lanes actually ran.

    Trace files are the primary evidence. Workflow review state is used as a
    fallback for live waiting_review screens, where trace post metrics may not
    have been finalized yet.
    """
    collector = get_editor_trace_collector()
    traces = collector.query_traces(
        EditorTraceQuery(project_id=project_id, execution_id=execution_id, limit=1)
    )
    if not traces:
        raise HTTPException(status_code=404, detail="Trace not found for execution")

    trace = traces[0]
    snapshots = trace.writer_input_snapshots
    repair_outcomes = trace.repair_outcomes

    execution_result = await db.execute(
        select(WorkflowExecution).where(
            WorkflowExecution.id == _project_key(execution_id),
            WorkflowExecution.project_id == _project_key(project_id),
        )
    )
    execution = execution_result.scalar_one_or_none()
    persisted = _as_dict(execution.result_context) if execution else {}
    review = _as_dict(persisted.get("review"))

    candidate_text = _candidate_text_from_review(review)
    if not candidate_text:
        candidate_text = await _candidate_text_from_steps(db, execution_id)

    trace_hash = trace.scene_text_hashes[-1] if trace.scene_text_hashes else ""
    current_text_hash = str(review.get("current_text_hash") or trace_hash or "")
    if not current_text_hash and candidate_text:
        current_text_hash = _text_hash(candidate_text)

    total_must_include = sum(len(s.must_include) for s in snapshots)
    total_forbidden = sum(len(s.must_avoid) for s in snapshots)
    v2_input_used = any(s.v2_used for s in snapshots)
    active_capabilities: list[str] = []
    writer_input_source_trace: dict[str, list[str]] = {}
    for snapshot in snapshots:
        for capability in snapshot.active_capabilities:
            if capability not in active_capabilities:
                active_capabilities.append(capability)
            sources = snapshot.source_trace.get(capability, [])
            if sources:
                existing = writer_input_source_trace.setdefault(capability, [])
                for source in sources:
                    if source not in existing:
                        existing.append(source)

    scene_index = review.get("scene_index", 0)
    try:
        scene_index = int(scene_index)
    except (TypeError, ValueError):
        scene_index = 0
    scene_snapshot = next((s for s in snapshots if s.scene_index == scene_index), None)
    if scene_snapshot is None and snapshots:
        scene_snapshot = snapshots[0]

    hard_max_chars = scene_snapshot.hard_max_chars if scene_snapshot else 0
    actual_chars = len(candidate_text) if candidate_text else trace.post_metrics.text_char_count

    total_orders = sum(o.total_orders for o in repair_outcomes)
    succeeded_orders = sum(o.succeeded_orders for o in repair_outcomes)
    failed_orders = sum(o.failed_orders for o in repair_outcomes)
    fbi_v2_engaged = any(o.v2_status != "skipped" for o in repair_outcomes)
    legacy_fallback = any(o.legacy_fallback for o in repair_outcomes)
    failure_reasons: list[str] = []
    for outcome in repair_outcomes:
        failure_reasons.extend(outcome.failure_reasons)

    return {
        "trace_id": trace.trace_id,
        "v2_input_used": v2_input_used,
        "must_include_count": total_must_include,
        "forbidden_count": total_forbidden,
        "active_capabilities": active_capabilities,
        "writer_input_source_trace": writer_input_source_trace,
        "writer_input_snapshots": [snapshot.model_dump() for snapshot in snapshots],
        "hard_max_chars": hard_max_chars,
        "actual_chars": actual_chars,
        "fbi_v2_engaged": fbi_v2_engaged,
        "repair_orders_total": total_orders,
        "repair_orders_succeeded": succeeded_orders,
        "repair_orders_failed": failed_orders,
        "failure_reasons": failure_reasons,
        "legacy_fallback": legacy_fallback,
        "issues_based_on_current_text": _issues_based_on_current_text(review, current_text_hash),
        "current_text_hash": current_text_hash,
        "v2_compile_status": trace.v2_compile_status,
        "v2_compile_error": trace.v2_compile_error,
        "final_status": trace.final_status,
        "workflow_status": execution.status if execution else "",
        "review_version": review.get("review_version"),
    }
