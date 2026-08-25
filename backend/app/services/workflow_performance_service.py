"""Read-only workflow performance summaries for release-candidate measurement.

The service deliberately derives metrics only from the timestamps and statuses
already persisted by ``WorkflowExecution`` and ``WorkflowStep``.  It does not
invent SLO thresholds or infer business-stage names that are absent from the
workflow schema.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.db_models import WorkflowExecution, WorkflowStep


TERMINAL_EXECUTION_STATUSES = (
    "completed",
    "failed",
    "interrupted",
    "partial",
    "cancelled",
    "canceled",
    "aborted",
    "timed_out",
    "timeout",
)
FAILURE_STATUSES = frozenset(
    {
        "failed",
        "interrupted",
        "partial",
        "aborted",
        "cancelled",
        "canceled",
        "timed_out",
        "timeout",
    }
)
MAX_TERMINAL_EXECUTIONS_SCANNED = 1_000


@dataclass(frozen=True)
class _MetricSample:
    status: str
    duration_ms: int | None


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _elapsed_ms(started_at: datetime | None, completed_at: datetime | None) -> int | None:
    start = _as_utc(started_at)
    end = _as_utc(completed_at)
    if start is None or end is None or end < start:
        return None
    return int((end - start).total_seconds() * 1_000)


def _step_duration_ms(step: WorkflowStep) -> int | None:
    calculated = _elapsed_ms(step.started_at, step.completed_at)
    recorded = step.duration_ms
    if recorded is not None:
        recorded = max(0, int(recorded))
        if recorded > 0:
            return recorded
    # Older rows can carry the column default 0 even though their timestamps
    # contain the real wall-clock duration. Prefer that evidence over a
    # default zero; an unstarted/cancelled row has no complete timestamp pair.
    return calculated


def _percentile(values: Iterable[int], percentile: float) -> int | None:
    ordered = sorted(int(value) for value in values)
    if not ordered:
        return None
    if len(ordered) == 1:
        return ordered[0]

    rank = (len(ordered) - 1) * percentile
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[lower]
    weight = rank - lower
    return int(round(ordered[lower] + (ordered[upper] - ordered[lower]) * weight))


def _metric_summary(samples: Iterable[_MetricSample], *, sample_unit: str) -> dict[str, Any]:
    materialized = list(samples)
    durations = [sample.duration_ms for sample in materialized if sample.duration_ms is not None]
    statuses = Counter(sample.status or "unknown" for sample in materialized)
    failure_count = sum(
        count for status, count in statuses.items() if status.lower() in FAILURE_STATUSES
    )
    sample_count = len(materialized)
    return {
        "sample_unit": sample_unit,
        "sample_count": sample_count,
        "timed_sample_count": len(durations),
        "p50_ms": _percentile(durations, 0.50),
        "p95_ms": _percentile(durations, 0.95),
        "failure_count": failure_count,
        "failure_rate": round(failure_count / sample_count, 4) if sample_count else None,
        "status_distribution": dict(sorted(statuses.items())),
    }


def _stage_status(steps: list[WorkflowStep]) -> str:
    statuses = [str(step.status or "unknown").lower() for step in steps]
    for status in ("failed", "interrupted", "timed_out", "timeout", "cancelled", "canceled"):
        if status in statuses:
            return status
    if "degraded" in statuses:
        return "degraded"
    if statuses and all(status in {"completed", "skipped"} for status in statuses):
        return "completed"
    if "completed" in statuses:
        return "partial"
    return statuses[0] if statuses else "unknown"


def _stage_duration_ms(steps: list[WorkflowStep]) -> int | None:
    starts = [_as_utc(step.started_at) for step in steps if step.started_at is not None]
    ends = [_as_utc(step.completed_at) for step in steps if step.completed_at is not None]
    if starts and ends:
        elapsed = _elapsed_ms(min(starts), max(ends))
        if elapsed is not None:
            return elapsed

    # DAG nodes in the same layer may run in parallel.  The maximum recorded
    # node duration is a safer wall-clock fallback than summing parallel work.
    durations = [duration for step in steps if (duration := _step_duration_ms(step)) is not None]
    return max(durations) if durations else None


async def summarize_workflow_performance(
    session: AsyncSession,
    *,
    project_id: str | None = None,
    trigger_type: str | None = None,
    completed_samples: int = 20,
) -> dict[str, Any]:
    """Summarize the newest terminal attempts needed to reach N completions.

    Failed and interrupted attempts between those completed executions remain
    in the cohort so failure rate and retry cost are not hidden.  Percentiles
    use linear interpolation over every valid timing sample in that cohort.
    """

    query = select(WorkflowExecution).where(
        WorkflowExecution.status.in_(TERMINAL_EXECUTION_STATUSES)
    )
    if project_id:
        query = query.where(WorkflowExecution.project_id == project_id)
    if trigger_type:
        query = query.where(WorkflowExecution.trigger_type == trigger_type)
    query = query.order_by(
        WorkflowExecution.updated_at.desc(),
        WorkflowExecution.created_at.desc(),
    ).limit(MAX_TERMINAL_EXECUTIONS_SCANNED)

    result = await session.execute(query)
    scanned = list(result.scalars().all())

    selected: list[WorkflowExecution] = []
    completed_count = 0
    for execution in scanned:
        selected.append(execution)
        if str(execution.status or "").lower() == "completed":
            completed_count += 1
        if completed_count >= completed_samples:
            break

    scan_truncated = (
        completed_count < completed_samples
        and len(scanned) >= MAX_TERMINAL_EXECUTIONS_SCANNED
    )

    steps: list[WorkflowStep] = []
    if selected:
        execution_ids = [execution.id for execution in selected]
        step_result = await session.execute(
            select(WorkflowStep)
            .where(WorkflowStep.execution_id.in_(execution_ids))
            .order_by(WorkflowStep.execution_id, WorkflowStep.layer, WorkflowStep.agent_name)
        )
        steps = list(step_result.scalars().all())

    total_samples = [
        _MetricSample(
            status=str(execution.status or "unknown"),
            duration_ms=_elapsed_ms(execution.created_at, execution.updated_at),
        )
        for execution in selected
    ]

    agent_samples: dict[str, list[_MetricSample]] = defaultdict(list)
    for step in steps:
        agent_samples[str(step.agent_name or "unknown")].append(
            _MetricSample(
                status=str(step.status or "unknown"),
                duration_ms=_step_duration_ms(step),
            )
        )

    stage_instances: dict[tuple[str, int], list[WorkflowStep]] = defaultdict(list)
    for step in steps:
        stage_instances[(str(step.execution_id), int(step.layer or 0))].append(step)

    stage_samples: dict[int, list[_MetricSample]] = defaultdict(list)
    stage_agents: dict[int, set[str]] = defaultdict(set)
    for (_, layer), layer_steps in stage_instances.items():
        stage_samples[layer].append(
            _MetricSample(
                status=_stage_status(layer_steps),
                duration_ms=_stage_duration_ms(layer_steps),
            )
        )
        stage_agents[layer].update(str(step.agent_name or "unknown") for step in layer_steps)

    if not selected:
        sample_status = "no_data"
    elif completed_count < completed_samples:
        sample_status = "insufficient_samples"
    else:
        sample_status = "sufficient"

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "filters": {
            "project_id": project_id,
            "trigger_type": trigger_type,
        },
        "cohort": {
            "requested_completed_samples": completed_samples,
            "completed_sample_count": completed_count,
            "terminal_sample_count": len(selected),
            "sample_status": sample_status,
            "max_terminal_scan": MAX_TERMINAL_EXECUTIONS_SCANNED,
            "scan_truncated": scan_truncated,
        },
        "metric_contract": {
            "duration_unit": "ms",
            "percentile_method": "linear_interpolation",
            "total_sample_unit": "terminal_execution_attempt",
            "agent_sample_unit": "workflow_step",
            "stage_sample_unit": "execution_dag_layer",
            "stage_definition": "persisted WorkflowStep.layer",
            "failure_statuses": sorted(FAILURE_STATUSES),
        },
        "total": _metric_summary(total_samples, sample_unit="terminal_execution_attempt"),
        "agents": [
            {
                "agent_name": agent_name,
                **_metric_summary(samples, sample_unit="workflow_step"),
            }
            for agent_name, samples in sorted(agent_samples.items())
        ],
        "stages": [
            {
                "stage_key": f"layer:{layer}",
                "layer": layer,
                "agent_names": sorted(stage_agents[layer]),
                **_metric_summary(samples, sample_unit="execution_dag_layer"),
            }
            for layer, samples in sorted(stage_samples.items())
        ],
        "trigger_type_distribution": dict(
            sorted(Counter(str(execution.trigger_type or "unknown") for execution in selected).items())
        ),
        "limitations": [
            "Total elapsed time uses execution created_at to updated_at and can include queue, pause, or human-review waiting.",
            "Stage metrics use persisted DAG layers because no canonical business-stage timestamp is stored.",
            "This endpoint reports measurements only; no release threshold is implied.",
        ],
    }
