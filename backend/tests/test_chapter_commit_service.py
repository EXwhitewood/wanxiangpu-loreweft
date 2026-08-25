import uuid
from unittest.mock import AsyncMock, Mock

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.api import chapters as chapters_api
from app.services import scene_generation_pipeline as generation
from app.db.db_models import (
    Chapter,
    ChapterBaseline,
    ChapterEffectOutbox,
    ChapterSnapshot,
    ChapterSettlementJob,
    ChatSession,
    EntityProgression,
    FBIRepairCaseORM,
    FBIRepairIssueORM,
    GenerationTrace,
    Project,
    WorkflowExecution,
    WorkflowStep,
)
from app.services.chapter_commit_service import (
    apply_pending_chapter_effects,
    discard_chapter_outbox,
    enqueue_worldview_projection,
    enqueue_chapter_effects,
    enqueue_scene_effect_bundle,
    enqueue_scene_memory_effects,
    recover_committing_chapters,
)
from app.services.state_manager import StateManager
from app.services.project_lock import ProjectLockManager
from app.services.worldview_projection_service import WorldviewProjectionService
from app.utils.word_count import count_words


@pytest_asyncio.fixture(autouse=True)
async def close_sqlite_state_manager():
    yield
    await StateManager._sqlite_manager.close()


@pytest.mark.asyncio
async def test_recover_committing_chapter_replays_outbox_and_creates_snapshot(db, monkeypatch):
    project_id = uuid.uuid4()
    db.add(Project(id=project_id, name="transaction-test"))
    await db.flush()
    db.add(Chapter(
        project_id=project_id,
        chapter_number=1,
        title="chapter-1",
        content="body",
        status="committing",
    ))
    await db.commit()

    persist = AsyncMock()
    monkeypatch.setattr(generation, "persist_scene_effects", persist)
    monkeypatch.setattr(
        WorldviewProjectionService,
        "project_committed_chapter_atomically",
        AsyncMock(return_value={
            "observations_created": 0,
            "seeds_written": 0,
            "auto_promoted": 0,
        }),
    )

    await enqueue_chapter_effects(
        db,
        project_id=project_id,
        chapter_number=1,
        pending_scene_effects=[{
            "scene_index": 0,
            "generated_text": "scene body",
            "effects": {"state_patch": {"completed_events": ["event-a"]}},
        }],
        chapter_state={"completed_events": ["event-a"]},
        execution_id="execution-1",
    )
    await db.commit()

    recovered = await recover_committing_chapters(db)
    assert recovered == 1
    persist.assert_awaited_once()

    chapter = (
        await db.execute(
            select(Chapter).where(
                Chapter.project_id == project_id,
                Chapter.chapter_number == 1,
            )
        )
    ).scalar_one()
    assert chapter.status == "committed"

    project = await db.get(Project, project_id)
    assert project.total_words == count_words("body")
    assert project.current_chapter == 2

    snapshot = (
        await db.execute(
            select(ChapterSnapshot).where(
                ChapterSnapshot.project_id == project_id,
                ChapterSnapshot.chapter_number == 1,
            )
        )
    ).scalar_one()
    assert snapshot.chapter_state == {"completed_events": ["event-a"]}
    assert snapshot.execution_id == "execution-1"

    outbox = (
        await db.execute(
            select(ChapterEffectOutbox).where(
                ChapterEffectOutbox.project_id == project_id,
                ChapterEffectOutbox.chapter_number == 1,
            )
        )
    ).scalars().all()
    assert len(outbox) == 3
    assert all(item.applied for item in outbox)


@pytest.mark.asyncio
async def test_scene_effect_bundle_is_idempotent_and_excludes_chapter_wide_work(db):
    project_id = uuid.uuid4()
    db.add(Project(id=project_id, name="scene-bundle-recovery-test"))
    await db.commit()
    pending = {
        "scene_index": 0,
        "generated_text": "Lin found the sealed file.",
        "effects": {
            "state_patch": {},
            "propositions": [{"proposition_id": "prop-1"}],
            "fact_contract": {"schema_version": 1},
            "proposition_audit_report": {"passed": True},
        },
    }

    await enqueue_scene_effect_bundle(
        db,
        project_id=project_id,
        chapter_number=2,
        pending_scene_effect=pending,
        generation_revision=3,
    )
    await enqueue_scene_effect_bundle(
        db,
        project_id=project_id,
        chapter_number=2,
        pending_scene_effect=pending,
        generation_revision=3,
    )
    await db.commit()

    rows = (
        await db.execute(
            select(ChapterEffectOutbox)
            .where(ChapterEffectOutbox.project_id == project_id)
            .order_by(ChapterEffectOutbox.scene_index)
        )
    ).scalars().all()
    assert [row.effect_type for row in rows] == [
        "scene_effects",
        "proposition_persistence",
    ]
    assert rows[1].payload["generation_revision"] == 3
    assert not any(
        row.effect_type in {"worldview_projection", "chapter_finalize"}
        for row in rows
    )


@pytest.mark.asyncio
async def test_scene_memory_effects_can_be_recovered_without_reapplying_scene_state(db):
    project_id = uuid.uuid4()
    db.add(Project(id=project_id, name="scene-memory-recovery-test"))
    await db.commit()

    await enqueue_scene_memory_effects(
        db,
        project_id=project_id,
        chapter_number=2,
        pending_scene_effect={
            "scene_index": 1,
            "generated_text": "accepted text",
            "effects": {
                "state_patch": {"completed_events": ["already applied"]},
                "propositions": [{"proposition_id": "prop-2"}],
            },
        },
        generation_revision=4,
    )
    await db.commit()

    rows = (
        await db.execute(
            select(ChapterEffectOutbox).where(
                ChapterEffectOutbox.project_id == project_id
            )
        )
    ).scalars().all()
    assert [row.effect_type for row in rows] == ["proposition_persistence"]
    assert rows[0].payload["scene_index"] == 1
    assert rows[0].payload["generation_revision"] == 4


@pytest.mark.asyncio
async def test_progressions_and_generation_trace_persist_with_accepted_scene_effects(db):
    project_id = uuid.uuid4()
    db.add(Project(id=project_id, name="accepted-effects-test"))
    await db.commit()
    trace = {
        "id": str(uuid.uuid4()),
        "project_id": str(project_id),
        "chapter_number": 3,
        "scene_index": 0,
        "trace_version": 2,
        "mode": "metadata",
        "status": "completed",
        "context_manifest": {},
        "prompt_preview": {},
        "quality_summary": {},
        "recovery_summary": {},
    }

    await generation.persist_scene_effects(
        project_id=str(project_id),
        chapter_number=3,
        scene_index=0,
        generated_text="Hero opens the sealed door.",
        effects={
            "generation_trace": trace,
            "progression_mentions": [{
                "entity_type": "character",
                "entity_id": "hero",
                "entity_name": "Hero",
                "alias": "Hero",
                "evidence_text": "Hero opens the sealed door.",
            }],
            "progression_status": "candidate",
        },
        db=db,
    )

    progression = (
        await db.execute(
            select(EntityProgression).where(EntityProgression.project_id == project_id)
        )
    ).scalar_one()
    persisted_trace = (
        await db.execute(
            select(GenerationTrace).where(GenerationTrace.project_id == project_id)
        )
    ).scalar_one()
    assert progression.effective_chapter == 3
    assert progression.entity_id == "hero"
    assert str(persisted_trace.id) == trace["id"]


@pytest.mark.asyncio
async def test_recover_committing_chapter_without_outbox_reverts_to_draft(db):
    project_id = uuid.uuid4()
    db.add(Project(id=project_id, name="missing-outbox-test"))
    await db.flush()
    db.add(Chapter(
        project_id=project_id,
        chapter_number=2,
        title="chapter-2",
        content="body",
        status="committing",
    ))
    await db.commit()

    recovered = await recover_committing_chapters(db)
    assert recovered == 0

    chapter = (
        await db.execute(
            select(Chapter).where(
                Chapter.project_id == project_id,
                Chapter.chapter_number == 2,
            )
        )
    ).scalar_one()
    assert chapter.status == "draft"


@pytest.mark.asyncio
async def test_recover_committing_chapter_updates_failed_workflow_status(db, monkeypatch):
    project_id = uuid.uuid4()
    execution_id = uuid.uuid4()
    db.add(Project(id=project_id, name="workflow-recovery-test"))
    await db.commit()
    db.add(Chapter(
        project_id=project_id,
        chapter_number=1,
        title="chapter-1",
        content="body",
        status="committing",
    ))
    db.add(WorkflowExecution(
        id=execution_id,
        project_id=project_id,
        status="failed",
        error_message="required effect failed",
    ))
    db.add(WorkflowStep(
        execution_id=execution_id,
        agent_name="write_chapter",
        layer=1,
        status="running",
    ))
    await db.commit()

    monkeypatch.setattr(generation, "persist_scene_effects", AsyncMock())
    monkeypatch.setattr(
        WorldviewProjectionService,
        "project_committed_chapter_atomically",
        AsyncMock(return_value={}),
    )
    await enqueue_chapter_effects(
        db,
        project_id=project_id,
        chapter_number=1,
        pending_scene_effects=[],
        chapter_state={},
        execution_id=str(execution_id),
    )
    await db.commit()

    assert await recover_committing_chapters(db) == 1

    execution = await db.get(WorkflowExecution, execution_id)
    assert execution.status == "completed"
    assert execution.error_message == ""
    assert execution.result_context["recovered_from_outbox"] is True


@pytest.mark.asyncio
async def test_discard_chapter_outbox_can_invalidate_current_and_following_chapters(db):
    project_id = uuid.uuid4()
    db.add(Project(id=project_id, name="discard-outbox-test"))
    await db.flush()
    for chapter_number in (1, 2, 3):
        db.add(ChapterEffectOutbox(
            project_id=project_id,
            chapter_number=chapter_number,
            scene_index=0,
            effect_type="scene_effects",
            effect_version=1,
            payload={},
            idempotency_key=f"discard-{project_id}-{chapter_number}",
        ))
    await db.commit()

    await discard_chapter_outbox(db, project_id, 2, from_chapter=True)
    await db.commit()

    remaining = (
        await db.execute(
            select(ChapterEffectOutbox.chapter_number).where(
                ChapterEffectOutbox.project_id == project_id,
            )
        )
    ).scalars().all()
    assert remaining == [1]


@pytest.mark.asyncio
async def test_worldview_projection_outbox_failure_is_retryable(db, monkeypatch):
    project_id = uuid.uuid4()
    db.add(Project(id=project_id, name="projection-retry-test"))
    await db.flush()
    db.add(Chapter(
        project_id=project_id,
        chapter_number=1,
        title="chapter-1",
        content="body",
        status="committed",
    ))
    execution = WorkflowExecution(
        project_id=project_id,
        trigger_type="editor_generate_ch1",
        input_context={"chapter_number": 1},
        status="completed",
    )
    db.add(execution)
    db.add(ChatSession(
        project_id=project_id,
        agent_type="editor",
        chapter_number=1,
        title="chapter chat",
    ))
    await db.commit()

    await enqueue_worldview_projection(
        db,
        project_id=project_id,
        chapter_number=1,
    )
    await db.commit()
    monkeypatch.setattr(
        WorldviewProjectionService,
        "project_committed_chapter_atomically",
        AsyncMock(side_effect=RuntimeError("projection unavailable")),
    )

    await apply_pending_chapter_effects(
        db,
        project_id=project_id,
        chapter_number=1,
    )

    outbox = (
        await db.execute(
            select(ChapterEffectOutbox).where(
                ChapterEffectOutbox.project_id == project_id,
                ChapterEffectOutbox.effect_type == "worldview_projection",
            )
        )
    ).scalar_one()
    assert outbox.applied is False
    assert outbox.status == "retryable_failed"
    assert outbox.attempts == 1
    assert "projection unavailable" in outbox.error_message

    monkeypatch.setattr(
        WorldviewProjectionService,
        "project_committed_chapter_atomically",
        AsyncMock(return_value={"degraded": False}),
    )
    await apply_pending_chapter_effects(
        db,
        project_id=project_id,
        chapter_number=1,
    )
    await db.refresh(outbox)

    assert outbox.applied is True
    assert outbox.status == "applied"
    assert outbox.error_message == ""


@pytest.mark.asyncio
async def test_manual_chapter_edit_keeps_failed_projection_retryable(db, monkeypatch):
    project_id = uuid.uuid4()
    db.add(Project(
        id=project_id,
        name="manual-edit-test",
        core_data={
            "writing_assistance": {
                "consistency_reminders": {
                    "enabled": False,
                    "check_on_save": False,
                    "dimensions": [],
                },
                "ai_edit_permission": {"mode": "ask_every_time"},
            }
        },
    ))
    await db.flush()
    db.add(Chapter(
        project_id=project_id,
        chapter_number=1,
        title="chapter-1",
        content="old body",
        status="committed",
    ))
    await db.commit()

    # Two-phase design: no pre-retract before new projection.
    # retract_chapter_sources should NOT be called on chapter edit.
    retract = AsyncMock(return_value={"observations_retracted": 0})
    monkeypatch.setattr(WorldviewProjectionService, "retract_chapter_sources", retract)
    schedule = Mock()
    monkeypatch.setattr(chapters_api, "schedule_user_chapter_settlement", schedule)

    result = await chapters_api.update_chapter(
        project_id,
        1,
        {"content": "new body"},
        db,
    )

    assert result["content"] == "new body"
    # Two-phase commit: no pre-retract on edit
    retract.assert_not_awaited()
    outbox = (
        await db.execute(
            select(ChapterEffectOutbox).where(
                ChapterEffectOutbox.project_id == project_id,
                ChapterEffectOutbox.effect_type == "worldview_projection",
            )
        )
    ).scalar_one_or_none()
    assert outbox is None
    settlement = (
        await db.execute(
            select(ChapterSettlementJob).where(
                ChapterSettlementJob.project_id == project_id,
                ChapterSettlementJob.chapter_number == 1,
            )
        )
    ).scalar_one()
    assert settlement.status == "pending"
    schedule.assert_called_once_with(str(settlement.id))


@pytest.mark.asyncio
async def test_manual_chapter_delete_syncs_before_physical_delete(db, monkeypatch):
    project_id = uuid.uuid4()
    db.add(Project(id=project_id, name="manual-delete-test"))
    await db.flush()
    chapter = Chapter(
        project_id=project_id,
        chapter_number=1,
        title="chapter-1",
        content="body",
        status="committed",
    )

    db.add(chapter)
    db.add(WorkflowExecution(
        project_id=project_id,
        status="completed",
        trigger_type="editor_chapter_generation",
        input_context={"chapter_number": 1},
    ))
    db.add(ChatSession(
        project_id=project_id,
        agent_type="editor_in_chief",
        chapter_number=1,
    ))
    legacy_case = FBIRepairCaseORM(
        id="legacy-case-chapter-1",
        project_id=project_id,
        chapter_id="1",
        source="window_repair_pipeline",
        status="failed",
    )
    db.add(legacy_case)
    db.add(FBIRepairIssueORM(
        id="legacy-issue-chapter-1",
        case_id=legacy_case.id,
        source_layer="L0",
        violation_type="dash_per_1000",
    ))
    await db.commit()

    retract = AsyncMock(return_value={"observations_retracted": 0})
    sync_delete = AsyncMock(return_value={})
    monkeypatch.setattr(WorldviewProjectionService, "retract_chapter_sources", retract)
    monkeypatch.setattr(
        chapters_api.NarrativeSyncService,
        "sync_chapter_delete",
        sync_delete,
    )

    await chapters_api.delete_chapter(project_id, 1, db)

    retract.assert_awaited_once()
    assert sync_delete.await_args.kwargs["retract_worldview"] is False
    chapter = (
        await db.execute(
            select(Chapter).where(
                Chapter.project_id == project_id,
                Chapter.chapter_number == 1,
            )
        )
    ).scalar_one_or_none()
    assert chapter is None
    assert (
        await db.execute(
            select(WorkflowExecution).where(WorkflowExecution.project_id == project_id)
        )
    ).scalar_one_or_none() is None
    assert (
        await db.execute(
            select(ChatSession).where(ChatSession.project_id == project_id)
        )
    ).scalar_one_or_none() is None
    assert (
        await db.execute(
            select(FBIRepairCaseORM).where(FBIRepairCaseORM.project_id == project_id)
        )
    ).scalar_one_or_none() is None
    assert (
        await db.execute(
            select(FBIRepairIssueORM).where(FBIRepairIssueORM.case_id == legacy_case.id)
        )
    ).scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_unchanged_user_save_reuses_worldview_projection_outbox(db):
    project_id = uuid.uuid4()
    db.add(Project(id=project_id, name="same-content-worldview-test"))
    db.add(Chapter(
        project_id=project_id,
        chapter_number=2,
        title="chapter-2",
        content="unchanged body",
        status="draft",
    ))
    await db.commit()

    await enqueue_worldview_projection(
        db,
        project_id=project_id,
        chapter_number=2,
        reuse_existing_content=True,
    )
    await db.commit()
    await enqueue_worldview_projection(
        db,
        project_id=project_id,
        chapter_number=2,
        reuse_existing_content=True,
    )
    await db.commit()

    rows = (
        await db.execute(
            select(ChapterEffectOutbox).where(
                ChapterEffectOutbox.project_id == project_id,
                ChapterEffectOutbox.chapter_number == 2,
                ChapterEffectOutbox.effect_type == "worldview_projection",
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].payload["content_hash"]
    assert rows[0].payload["generation_revision"] == 1


@pytest.mark.asyncio
async def test_manual_first_chapter_delete_invalidates_state_history(db, monkeypatch):
    project_id = uuid.uuid4()
    db.add(Project(id=project_id, name="delete-first-chapter-state-test", current_chapter=1, total_words=4))
    await db.flush()
    db.add(Chapter(project_id=project_id, chapter_number=1, content="body", status="committed"))
    db.add(ChapterBaseline(project_id=project_id, chapter_number=1, baseline_story_state={}))
    db.add(ChapterSnapshot(
        project_id=project_id,
        chapter_number=1,
        story_state={"active_chapter": 1, "active_scene": 4},
        stale=False,
    ))
    db.add(ChapterEffectOutbox(
        project_id=project_id,
        chapter_number=1,
        scene_index=0,
        effect_type="scene_effects",
        payload={},
        idempotency_key=f"delete-first-{project_id}",
    ))
    await db.commit()

    monkeypatch.setattr(WorldviewProjectionService, "retract_chapter_sources", AsyncMock(return_value={}))
    monkeypatch.setattr(chapters_api.NarrativeSyncService, "sync_chapter_delete", AsyncMock(return_value={}))
    set_state = AsyncMock()
    monkeypatch.setattr(StateManager, "set_state", set_state)

    await chapters_api.delete_chapter(project_id, 1, db)

    baseline = (
        await db.execute(
            select(ChapterBaseline).where(ChapterBaseline.project_id == project_id)
        )
    ).scalar_one_or_none()
    snapshot = (
        await db.execute(
            select(ChapterSnapshot).where(ChapterSnapshot.project_id == project_id)
        )
    ).scalar_one_or_none()
    outbox = (
        await db.execute(
            select(ChapterEffectOutbox).where(ChapterEffectOutbox.project_id == project_id)
        )
    ).scalar_one_or_none()
    restored = set_state.await_args.args[1]
    project = await db.get(Project, project_id)

    assert baseline is None
    assert snapshot is None
    assert outbox is None
    assert project.current_chapter == 1
    assert project.total_words == 0
    assert restored.active_chapter == 1
    assert restored.active_scene == 0
    assert restored.completed_events == []


@pytest.mark.asyncio
async def test_manual_latest_chapter_delete_recomputes_next_chapter_semantic(db, monkeypatch):
    project_id = uuid.uuid4()
    project = Project(
        id=project_id,
        name="delete-latest-progress-test",
        current_chapter=99,
        total_words=999,
    )
    first_content = "第一章正文"
    db.add_all([
        project,
        Chapter(project_id=project_id, chapter_number=1, content=first_content, status="committed"),
        Chapter(project_id=project_id, chapter_number=2, content="第二章正文", status="committed"),
        ChapterSnapshot(
            project_id=project_id,
            chapter_number=1,
            story_state={"active_chapter": 1, "active_scene": 2},
            stale=False,
        ),
        ChapterSnapshot(
            project_id=project_id,
            chapter_number=2,
            story_state={"active_chapter": 2, "active_scene": 2},
            stale=False,
        ),
    ])
    await db.commit()

    monkeypatch.setattr(WorldviewProjectionService, "retract_chapter_sources", AsyncMock(return_value={}))
    monkeypatch.setattr(chapters_api.NarrativeSyncService, "sync_chapter_delete", AsyncMock(return_value={}))
    set_state = AsyncMock()
    monkeypatch.setattr(StateManager, "set_state", set_state)

    await chapters_api.delete_chapter(project_id, 2, db)
    await db.refresh(project)

    assert project.total_words == count_words(first_content)
    assert project.current_chapter == 2
    restored = set_state.await_args.args[1]
    assert restored.active_chapter == 1
    assert restored.active_scene == 0


@pytest.mark.asyncio
async def test_manual_middle_chapter_delete_restores_previous_snapshot(db, monkeypatch):
    project_id = uuid.uuid4()
    db.add(Project(id=project_id, name="delete-middle-chapter-state-test"))
    await db.flush()
    db.add(Chapter(project_id=project_id, chapter_number=2, content="body", status="committed"))
    db.add(ChapterSnapshot(
        project_id=project_id,
        chapter_number=1,
        story_state={"active_chapter": 1, "active_scene": 3, "completed_events": ["chapter-1"]},
        stale=False,
    ))
    db.add(ChapterSnapshot(
        project_id=project_id,
        chapter_number=2,
        story_state={"active_chapter": 2, "active_scene": 3, "completed_events": ["chapter-1", "chapter-2"]},
        stale=False,
    ))
    await db.commit()

    monkeypatch.setattr(WorldviewProjectionService, "retract_chapter_sources", AsyncMock(return_value={}))
    monkeypatch.setattr(chapters_api.NarrativeSyncService, "sync_chapter_delete", AsyncMock(return_value={}))
    set_state = AsyncMock()
    monkeypatch.setattr(StateManager, "set_state", set_state)

    await chapters_api.delete_chapter(project_id, 2, db)

    snapshots = (
        await db.execute(
            select(ChapterSnapshot)
            .where(ChapterSnapshot.project_id == project_id)
            .order_by(ChapterSnapshot.chapter_number)
        )
    ).scalars().all()
    restored = set_state.await_args.args[1]

    assert snapshots[0].stale is False
    assert len(snapshots) == 1
    assert restored.active_chapter == 1
    assert restored.active_scene == 0
    assert restored.completed_events == ["chapter-1"]


@pytest.mark.asyncio
async def test_manual_chapter_delete_rejects_non_latest_chapter_before_cleanup(db, monkeypatch):
    project_id = uuid.uuid4()
    db.add(Project(id=project_id, name="delete-order-test"))
    db.add(Chapter(project_id=project_id, chapter_number=1, content="one", status="committed"))
    db.add(Chapter(project_id=project_id, chapter_number=2, content="two", status="committed"))
    await db.commit()

    retract = AsyncMock(return_value={})
    monkeypatch.setattr(WorldviewProjectionService, "retract_chapter_sources", retract)

    with pytest.raises(Exception) as exc_info:
        await chapters_api.delete_chapter(project_id, 1, db)

    assert getattr(exc_info.value, "status_code", None) == 409
    assert "只能删除最新章节" in str(getattr(exc_info.value, "detail", ""))
    retract.assert_not_awaited()
    remaining = (
        await db.execute(
            select(Chapter)
            .where(Chapter.project_id == project_id)
            .order_by(Chapter.chapter_number)
        )
    ).scalars().all()
    assert [chapter.chapter_number for chapter in remaining] == [1, 2]


@pytest.mark.asyncio
async def test_manual_chapter_delete_rejects_running_project(db):
    project_id = uuid.uuid4()
    lock = ProjectLockManager.get_lock(str(project_id))
    await lock.acquire()
    try:
        with pytest.raises(Exception) as exc_info:
            await chapters_api.delete_chapter(project_id, 1, db)
        assert getattr(exc_info.value, "status_code", None) == 409
    finally:
        lock.release()
        ProjectLockManager.cleanup(str(project_id))
