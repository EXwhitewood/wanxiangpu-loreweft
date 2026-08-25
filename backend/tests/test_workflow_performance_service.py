from datetime import datetime, timedelta, timezone
import uuid

import pytest

from app.api import workflows
from app.db.db_models import Project, WorkflowExecution, WorkflowStep
from app.services.workflow_performance_service import _step_duration_ms, summarize_workflow_performance


async def _add_execution(
    db,
    *,
    project_id,
    created_at: datetime,
    status: str,
    duration_ms: int,
    trigger_type: str = "editor_generate",
    include_review: bool = True,
) -> WorkflowExecution:
    execution = WorkflowExecution(
        id=uuid.uuid4(),
        project_id=project_id,
        status=status,
        trigger_type=trigger_type,
        created_at=created_at,
        updated_at=created_at + timedelta(milliseconds=duration_ms),
    )
    db.add(execution)
    db.add(
        WorkflowStep(
            id=uuid.uuid4(),
            execution_id=execution.id,
            agent_name="chapter_writer",
            layer=0,
            status="completed" if status == "completed" else status,
            started_at=created_at,
            completed_at=created_at + timedelta(milliseconds=duration_ms // 2),
            duration_ms=duration_ms // 2,
        )
    )
    if include_review:
        review_started = created_at + timedelta(milliseconds=duration_ms // 2)
        db.add(
            WorkflowStep(
                id=uuid.uuid4(),
                execution_id=execution.id,
                agent_name="chapter_review",
                layer=1,
                status="completed",
                started_at=review_started,
                completed_at=review_started + timedelta(milliseconds=250),
                duration_ms=250,
            )
        )
    return execution


def test_step_duration_uses_timestamps_when_legacy_column_is_default_zero():
    started = datetime(2026, 7, 1, tzinfo=timezone.utc)
    step = WorkflowStep(
        agent_name="legacy-step",
        started_at=started,
        completed_at=started + timedelta(milliseconds=1_250),
        duration_ms=0,
    )
    assert _step_duration_ms(step) == 1_250


@pytest.mark.asyncio
async def test_performance_summary_keeps_failures_between_ten_recent_completions(db):
    project_id = uuid.uuid4()
    db.add(Project(id=project_id, name="workflow-metrics"))
    base = datetime(2026, 7, 1, tzinfo=timezone.utc)

    # This older completion must fall outside the cohort once ten newer
    # completions have been collected.
    await _add_execution(
        db,
        project_id=project_id,
        created_at=base - timedelta(days=1),
        status="completed",
        duration_ms=99_000,
    )
    for index in range(10):
        await _add_execution(
            db,
            project_id=project_id,
            created_at=base + timedelta(minutes=index),
            status="completed",
            duration_ms=1_000,
        )
    for index in range(2):
        await _add_execution(
            db,
            project_id=project_id,
            created_at=base + timedelta(minutes=10 + index),
            status="failed",
            duration_ms=2_000,
            include_review=False,
        )

    # A different project and trigger must not contaminate the filtered cohort.
    other_project_id = uuid.uuid4()
    db.add(Project(id=other_project_id, name="other"))
    await _add_execution(
        db,
        project_id=other_project_id,
        created_at=base + timedelta(days=1),
        status="failed",
        duration_ms=50_000,
        trigger_type="other_workflow",
        include_review=False,
    )
    await db.commit()

    report = await summarize_workflow_performance(
        db,
        project_id=str(project_id),
        trigger_type="editor_generate",
        completed_samples=10,
    )

    assert report["cohort"] == {
        "requested_completed_samples": 10,
        "completed_sample_count": 10,
        "terminal_sample_count": 12,
        "sample_status": "sufficient",
        "max_terminal_scan": 1000,
        "scan_truncated": False,
    }
    assert report["total"]["sample_count"] == 12
    assert report["total"]["timed_sample_count"] == 12
    assert report["total"]["p50_ms"] == 1_000
    assert report["total"]["p95_ms"] == 2_000
    assert report["total"]["failure_count"] == 2
    assert report["total"]["failure_rate"] == 0.1667
    assert report["total"]["status_distribution"] == {"completed": 10, "failed": 2}

    agents = {item["agent_name"]: item for item in report["agents"]}
    assert agents["chapter_writer"]["sample_count"] == 12
    assert agents["chapter_writer"]["p50_ms"] == 500
    assert agents["chapter_writer"]["p95_ms"] == 1_000
    assert agents["chapter_writer"]["failure_rate"] == 0.1667
    assert agents["chapter_review"]["sample_count"] == 10
    assert agents["chapter_review"]["failure_rate"] == 0.0

    stages = {item["layer"]: item for item in report["stages"]}
    assert stages[0]["sample_count"] == 12
    assert stages[0]["agent_names"] == ["chapter_writer"]
    assert stages[1]["sample_count"] == 10
    assert stages[1]["agent_names"] == ["chapter_review"]
    assert report["trigger_type_distribution"] == {"editor_generate": 12}


@pytest.mark.asyncio
async def test_performance_summary_marks_small_cohort_without_inventing_gate(db):
    project_id = uuid.uuid4()
    db.add(Project(id=project_id, name="small-sample"))
    base = datetime(2026, 7, 2, tzinfo=timezone.utc)
    for index in range(3):
        await _add_execution(
            db,
            project_id=project_id,
            created_at=base + timedelta(minutes=index),
            status="completed",
            duration_ms=1_000 + index * 100,
        )
    await _add_execution(
        db,
        project_id=project_id,
        created_at=base + timedelta(minutes=3),
        status="interrupted",
        duration_ms=500,
        include_review=False,
    )
    await db.commit()

    report = await summarize_workflow_performance(
        db,
        project_id=str(project_id),
        completed_samples=10,
    )

    assert report["cohort"]["sample_status"] == "insufficient_samples"
    assert report["cohort"]["completed_sample_count"] == 3
    assert report["cohort"]["terminal_sample_count"] == 4
    assert report["total"]["status_distribution"] == {"completed": 3, "interrupted": 1}
    assert report["total"]["failure_rate"] == 0.25
    assert "release_threshold" not in report
    assert report["limitations"][-1].endswith("no release threshold is implied.")


def test_performance_summary_static_route_precedes_execution_id_route():
    route_paths = [getattr(route, "path", "") for route in workflows.router.routes]
    assert "/workflows/performance-summary" in route_paths
    assert route_paths.index("/workflows/performance-summary") < route_paths.index(
        "/workflows/{execution_id}"
    )
