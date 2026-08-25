import uuid
import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.api import editor_chat
from app.db.db_models import (
    Chapter,
    EntityProgression,
    GenerationTrace,
    Project,
    WorkflowExecution,
    WorkflowStep,
)
from app.models.chapter_review import ChapterRepairOrder, SceneReviewPacket
from app.services.editor_generation_dag_runtime import NODE_TIMEOUTS


def test_parallel_scene_review_outer_timeout_exceeds_worker_timeout():
    assert editor_chat._RECHECK_REVIEW_TIMEOUT_SECONDS >= 480
    assert (
        NODE_TIMEOUTS["parallel_scene_review"]
        > editor_chat._RECHECK_REVIEW_TIMEOUT_SECONDS
    )


def test_repair_failed_delta_reenters_fbi_as_review_packet():
    packets = editor_chat._repair_failed_delta_packets(
        {
            0: [{
                "type": "fact_conflict",
                "severity": "high",
                "detail": "The candidate still contradicts the chapter fact.",
                "blocks_commit": True,
            }],
            1: [{
                "type": "ai_punctuation_artifact",
                "severity": "medium",
                "detail": "Style advisory only.",
                "blocks_commit": False,
            }],
        },
        {0: "scene zero", 1: "scene one"},
    )

    assert len(packets) == 1
    assert packets[0].scene_index == 0
    assert packets[0].candidate_text == "scene zero"
    assert packets[0].blocking_violations[0]["type"] == "fact_conflict"


def test_locked_scene_validation_skip_excludes_in_place_repair_targets():
    overrides = {0: "approved scene", 1: "candidate under repair"}

    assert editor_chat._skip_locked_scene_validation(0, overrides, True, {1}) is True
    assert editor_chat._skip_locked_scene_validation(1, overrides, True, {1}) is False
    assert editor_chat._skip_locked_scene_validation(0, overrides, False, {1}) is False


def test_recheck_budget_extends_only_for_new_hard_finding():
    first = {("pov", "span-a", "1")}
    newly_discovered = {("timeline", "span-b", "1")}

    assert editor_chat._allow_novel_delta_repair_cycle(
        repair_round=1,
        current_keys=newly_discovered,
        attempted_keys=first,
    ) is True
    assert editor_chat._allow_novel_delta_repair_cycle(
        repair_round=1,
        current_keys=first,
        attempted_keys=first,
    ) is False
    assert editor_chat._allow_novel_delta_repair_cycle(
        repair_round=2,
        current_keys=newly_discovered,
        attempted_keys=first,
    ) is False


def test_boundary_conflict_attributes_overshoot_to_prior_scene():
    issue = {
        "type": "timeline_conflict",
        "detail": (
            "正文中凤溪再次使用禁术与魔君战斗，"
            "但前序场景已导致魔君灭亡。"
        ),
        "target_span": "魔君的身体开始崩塌",
    }

    result = editor_chat._annotate_boundary_conflict_sources(
        issue,
        scene_index=1,
        scene_contract={
            "goal": "凤溪用禁忌之术将魔君灵魂摧毁",
            "hard_must_show": ["魔君在湮灭中留下未说完的遗言"],
        },
    )

    assert result["source_scene"] == 1
    assert result["source_scenes"] == [0, 1]
    assert result["affected_scene"] == 0
    assert result["boundary_conflict"] is True


def test_workbench_cross_scene_issue_repairs_only_persisted_owner_scene():
    scope = editor_chat._workbench_issue_repair_scope(
        "current scene",
        {
            "type": "timeline_conflict",
            "source_scene": 1,
            "source_scenes": [0, 1],
            "affected_scene": 0,
            "boundary_conflict": True,
        },
        {
            "scene_index": 1,
            "approved_scene_texts": {"0": "prior scene"},
            "scene_contract": {"scene_id": "scene-2"},
            "scene_contracts": {
                "0": {"scene_id": "scene-1", "ending_state": "decision made"},
                "1": {"scene_id": "scene-2"},
            },
        },
        {"review_scope": "scene"},
    )

    assert scope["cross_scene_scope"] is True
    assert scope["scene_index"] == 0
    assert scope["review_scene_index"] == 1
    assert scope["candidate_text"] == "prior scene"
    assert scope["review_candidate_text"] == "current scene"
    assert scope["scene_texts"] == {0: "prior scene", 1: "current scene"}


def test_workbench_cross_scene_issue_fails_closed_without_owner_contract():
    with pytest.raises(editor_chat.HTTPException) as exc_info:
        editor_chat._workbench_issue_repair_scope(
            "current scene",
            {
                "source_scene": 1,
                "source_scenes": [0, 1],
                "affected_scene": 0,
                "boundary_conflict": True,
            },
            {
                "scene_index": 1,
                "approved_scene_texts": {"0": "prior scene"},
                "scene_contract": {"scene_id": "scene-2"},
            },
            {"review_scope": "scene"},
        )

    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_state_commit_prefers_writer_fact_form_without_llm_reextraction():
    writer_facts = {"established_facts": ["writer fact"]}
    result = {"writer_scene_facts": writer_facts}

    scene_facts, source = await editor_chat._prepare_scene_facts_for_commit(
        chapter_number=26,
        generated_text="candidate",
        result=result,
        character_cards=[],
        scene_contract={},
    )

    assert scene_facts == writer_facts
    assert source == "writer_scene_facts"
    assert result["fact_source"] == "writer_scene_facts"


def test_only_surface_local_patches_reuse_review_claims():
    surface_plan = SimpleNamespace(orders=[ChapterRepairOrder(
        owner_scene=0,
        target_scenes=[0],
        repair_type="local_patch",
        repair_domain="surface",
    )])
    fact_plan = SimpleNamespace(orders=[ChapterRepairOrder(
        owner_scene=0,
        target_scenes=[0],
        repair_type="local_patch",
        repair_domain="fact",
    )])
    rewrite_plan = SimpleNamespace(orders=[ChapterRepairOrder(
        owner_scene=0,
        target_scenes=[0],
        repair_type="scene_rewrite",
        repair_domain="structure",
    )])

    assert editor_chat._repair_plan_preserves_review_claims(surface_plan, 0) is True
    assert editor_chat._repair_plan_preserves_review_claims(fact_plan, 0) is False
    assert editor_chat._repair_plan_preserves_review_claims(rewrite_plan, 0) is False


def test_chapter_workbench_repair_is_scoped_to_issue_scene_and_reassembled():
    resume_state = {
        "review_scope": "chapter",
        "scene_index": 0,
        "scene_map": [
            {"scene_index": 0, "scene_id": "scene_a"},
            {"scene_index": 1, "scene_id": "scene_b"},
        ],
        "chapter_scene_paragraph_counts": [2, 1],
        "scene_contracts": {
            "0": {"scene_id": "scene_a"},
            "1": {"scene_id": "scene_b", "pov_character": "林澈"},
        },
    }
    scope = editor_chat._workbench_issue_repair_scope(
        "scene-a-1\n\nscene-a-2\n\nscene-b",
        {"type": "head_hopping_count", "source_scene": 1},
        resume_state,
        {"review_scope": "chapter"},
    )

    assert scope["scene_index"] == 1
    assert scope["candidate_text"] == "scene-b"
    assert scope["scene_contract"]["scene_id"] == "scene_b"
    assert editor_chat._merge_workbench_repaired_scene(scope, "scene-b-fixed") == (
        "scene-a-1\n\nscene-a-2\n\nscene-b-fixed"
    )


def test_legacy_chapter_workbench_refuses_cross_scene_repair_without_contract():
    with pytest.raises(editor_chat.HTTPException) as exc:
        editor_chat._workbench_issue_repair_scope(
            "scene-a\n\nscene-b",
            {"type": "head_hopping_count", "source_scene": 1},
            {
                "review_scope": "chapter",
                "scene_index": 0,
                "scene_map": [
                    {"scene_index": 0, "scene_id": "scene_a"},
                    {"scene_index": 1, "scene_id": "scene_b"},
                ],
                "chapter_scene_paragraph_counts": [1, 1],
                "scene_contract": {"scene_id": "scene_a"},
            },
            {"review_scope": "chapter"},
        )

    assert exc.value.status_code == 409


def test_l4_validator_error_reuses_only_successful_retry_for_locked_scene():
    overrides = {0: "validated scene", 1: "scene under repair"}
    validator_error = {
        "type": "proposition_extractor_unavailable",
        "issue_classification": "validator_system_error",
    }

    assert editor_chat._reuse_locked_scene_validator_result(
        0,
        validator_error,
        validator_retry_resumed=True,
        reviewed_scene_overrides=overrides,
        skip_reviewed_scene_validation=True,
        reviewed_scene_repair_indexes={1},
    ) is True
    assert editor_chat._reuse_locked_scene_validator_result(
        0,
        validator_error,
        validator_retry_resumed=False,
        reviewed_scene_overrides=overrides,
        skip_reviewed_scene_validation=True,
        reviewed_scene_repair_indexes={1},
    ) is False
    assert editor_chat._reuse_locked_scene_validator_result(
        1,
        validator_error,
        validator_retry_resumed=True,
        reviewed_scene_overrides=overrides,
        skip_reviewed_scene_validation=True,
        reviewed_scene_repair_indexes={1},
    ) is False
    assert editor_chat._reuse_locked_scene_validator_result(
        0,
        {"type": "fact_conflict", "blocks_commit": True},
        validator_retry_resumed=True,
        reviewed_scene_overrides=overrides,
        skip_reviewed_scene_validation=True,
        reviewed_scene_repair_indexes={1},
    ) is False


def test_validator_system_blockers_reject_mixed_content_findings():
    assert editor_chat._validator_system_blockers([{
        "type": "proposition_extractor_unavailable",
        "blocks_commit": True,
    }])
    assert not editor_chat._validator_system_blockers([
        {
            "type": "proposition_extractor_unavailable",
            "blocks_commit": True,
        },
        {
            "type": "fact_conflict",
            "blocks_commit": True,
        },
    ])


def test_validator_only_system_findings_accept_unflagged_recheck_error():
    validator_error = {
        "type": "proposition_extractor_unavailable",
        "blocks_commit": False,
        "issue_classification": "validator_system_error",
    }

    assert editor_chat._validator_only_system_findings([validator_error])
    assert not editor_chat._validator_only_system_findings([
        validator_error,
        {
            "type": "fact_conflict",
            "blocks_commit": True,
        },
    ])


@pytest.mark.asyncio
async def test_workflow_task_registry_cancels_running_background_task():
    started = asyncio.Event()

    async def sleeper():
        started.set()
        await asyncio.sleep(60)

    task = editor_chat._spawn_workflow_task("exec-cancel-test", sleeper())
    await started.wait()

    assert editor_chat._cancel_workflow_tasks("exec-cancel-test") == 1
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_cancel_workflow_purges_draft_artifacts_and_scrubs_review_payload(db):
    project_id = uuid.uuid4()
    execution_id = str(uuid.uuid4())
    chapter = Chapter(
        project_id=project_id,
        chapter_number=28,
        title="chapter-28",
        content="",
        status="draft",
    )
    db.add(Project(id=project_id, name="cancel-cleanup-test"))
    db.add(chapter)
    db.add(WorkflowExecution(
        id=execution_id,
        project_id=project_id,
        status="waiting_review",
        trigger_type="editor_generate_ch28",
        input_context={"chapter_number": 28, "candidate_text": "stale candidate"},
        result_context={
            "resume_state": {"chapter_number": 28, "candidate_text": "stale candidate"},
            "review": {"orders": [{"issue": "stale"}]},
        },
    ))
    db.add(WorkflowStep(
        execution_id=execution_id,
        agent_name="parallel_scene_repair",
        status="running",
        input_snapshot={"orders": [{"issue": "stale"}]},
        output_snapshot={"candidate_text": "stale candidate"},
        phase_trace=[{"phase": "repair"}],
    ))
    db.add(EntityProgression(
        project_id=project_id,
        entity_type="character",
        entity_id="hero",
        effective_chapter=28,
        evidence_text="stale mention",
        fingerprint=uuid.uuid4().hex,
    ))
    db.add(GenerationTrace(
        project_id=project_id,
        chapter_number=28,
        scene_index=0,
    ))
    await db.commit()

    result = await editor_chat.cancel_workflow_execution(project_id, execution_id, db)

    assert result["status"] == "cancelled"
    assert result["cleanup"]["entity_progressions"] == 1
    assert result["cleanup"]["generation_traces"] == 1
    execution = await db.get(WorkflowExecution, uuid.UUID(execution_id))
    assert execution.input_context == {"chapter_number": 28}
    assert execution.result_context["cancelled"] is True
    assert "review" not in execution.result_context
    step = (
        await db.execute(
            select(WorkflowStep).where(WorkflowStep.execution_id == execution_id)
        )
    ).scalar_one()
    assert step.input_snapshot == {}
    assert step.output_snapshot == {}
    assert step.phase_trace == []
    assert (
        await db.execute(
            select(EntityProgression).where(EntityProgression.project_id == project_id)
        )
    ).scalar_one_or_none() is None
    assert (
        await db.execute(
            select(GenerationTrace).where(GenerationTrace.project_id == project_id)
        )
    ).scalar_one_or_none() is None
    assert "exec-cancel-test" not in editor_chat._WORKFLOW_TASKS


@pytest.mark.asyncio
async def test_workflow_task_registry_deduplicates_same_execution_role():
    started = asyncio.Event()

    async def sleeper():
        started.set()
        await asyncio.sleep(60)

    first = editor_chat._spawn_workflow_task(
        "exec-role-test",
        sleeper(),
        role="validator_retry_scheduler",
    )
    await started.wait()
    second = editor_chat._spawn_workflow_task(
        "exec-role-test",
        sleeper(),
        role="validator_retry_scheduler",
    )

    assert second is first
    assert editor_chat._cancel_workflow_tasks("exec-role-test") == 1
    with pytest.raises(asyncio.CancelledError):
        await first
    assert ("exec-role-test", "validator_retry_scheduler") not in editor_chat._WORKFLOW_TASK_ROLES


@pytest.mark.asyncio
async def test_cancelled_workflow_cannot_be_resumed_or_reopen_steps(db):
    project_id = uuid.uuid4()
    execution_id = uuid.uuid4()
    db.add(Project(id=project_id, name="cancel-sticky-test"))
    execution = WorkflowExecution(
        id=execution_id,
        project_id=project_id,
        status="cancelled",
    )
    db.add(execution)
    db.add(WorkflowStep(
        execution_id=execution_id,
        agent_name="review_case_delta_merge",
        layer=4,
        status="cancelled",
    ))
    await db.commit()

    prepared = await editor_chat._prepare_workflow_resume(
        execution,
        db=db,
        from_layer=4,
    )
    step_updated = await editor_chat._update_workflow_step(
        str(execution_id),
        "review_case_delta_merge",
        "running",
        db=db,
    )

    assert prepared is False
    assert step_updated is False
    await db.refresh(execution)
    assert execution.status == "cancelled"
    step = (
        await db.execute(
            editor_chat.select(WorkflowStep).where(
                WorkflowStep.execution_id == str(execution_id),
                WorkflowStep.agent_name == "review_case_delta_merge",
            )
        )
    ).scalar_one()
    assert step.status == "cancelled"


@pytest.mark.asyncio
async def test_resumed_step_duration_starts_at_current_attempt(db):
    project_id = uuid.uuid4()
    execution_id = uuid.uuid4()
    old_started_at = editor_chat._utcnow() - timedelta(hours=2)
    db.add(Project(id=project_id, name="duration-reset-test"))
    db.add(WorkflowExecution(
        id=execution_id,
        project_id=project_id,
        status="running",
    ))
    db.add(WorkflowStep(
        execution_id=execution_id,
        agent_name="review_case_delta_merge",
        layer=4,
        status="waiting_review",
        started_at=old_started_at,
        completed_at=editor_chat._utcnow() - timedelta(hours=1),
    ))
    await db.commit()
    attempt_started_after = editor_chat._utcnow() - timedelta(seconds=1)

    updated = await editor_chat._update_workflow_step(
        str(execution_id),
        "review_case_delta_merge",
        "running",
        db=db,
    )

    assert updated is True
    step = (
        await db.execute(
            editor_chat.select(WorkflowStep).where(
                WorkflowStep.execution_id == str(execution_id),
                WorkflowStep.agent_name == "review_case_delta_merge",
            )
        )
    ).scalar_one()
    assert step.started_at >= attempt_started_after
    assert step.completed_at is None
    assert step.duration_ms is None


async def _make_waiting_review(db):
    project_id = uuid.uuid4()
    execution_id = uuid.uuid4()
    db.add(Project(id=project_id, name="review-resume-test"))
    db.add(
        WorkflowExecution(
            id=execution_id,
            project_id=project_id,
            status="waiting_review",
            result_context={
                "resume_state": {
                    "chapter_number": 1,
                    "scene_index": 0,
                    "candidate_text": "old text",
                    "approved_scene_texts": {},
                    "review_version": 3,
                }
            },
        )
    )
    await db.commit()
    return project_id, execution_id


async def _make_waiting_chapter_review(db):
    project_id = uuid.uuid4()
    execution_id = uuid.uuid4()
    db.add(Project(id=project_id, name="chapter-review-resume-test"))
    db.add(
        WorkflowExecution(
            id=execution_id,
            project_id=project_id,
            status="waiting_review",
            result_context={
                "review": {
                    "scene_index": 0,
                    "review_scope": "chapter",
                    "candidate_text": "s1-a\n\ns1-b\n\ns2-a",
                    "violations": [],
                    "attempts": [],
                    "error_code": "final_commit_gate_needs_human_review",
                    "message": "final gate needs chapter review",
                    "review_version": 5,
                },
                "resume_state": {
                    "chapter_number": 1,
                    "scene_index": 0,
                    "review_scope": "chapter",
                    "candidate_text": "s1-a\n\ns1-b\n\ns2-a",
                    "approved_scene_texts": {},
                    "review_version": 5,
                    "scene_map": [
                        {"scene_index": 0, "scene_id": "scene_1"},
                        {"scene_index": 1, "scene_id": "scene_2"},
                    ],
                    "chapter_scene_paragraph_counts": [2, 1],
                },
            },
        )
    )
    await db.commit()
    return project_id, execution_id


async def _make_waiting_partial_scene_review(db):
    project_id = uuid.uuid4()
    execution_id = uuid.uuid4()
    db.add(Project(id=project_id, name="partial-scene-review-retry-test"))
    db.add(
        WorkflowExecution(
            id=execution_id,
            project_id=project_id,
            status="waiting_review",
            result_context={
                "review": {
                    "scene_index": 1,
                    "review_scope": "scene",
                    "candidate_text": "old target candidate",
                    "review_version": 7,
                },
                "resume_state": {
                    "chapter_number": 1,
                    "scene_index": 1,
                    "review_scope": "scene",
                    "candidate_text": "old target candidate",
                    "approved_scene_texts": {"0": "approved scene zero"},
                    "review_version": 7,
                },
            },
        )
    )
    await db.commit()
    return project_id, execution_id


async def _make_pending_validator_retry(db):
    project_id = uuid.uuid4()
    execution_id = uuid.uuid4()
    db.add(Project(id=project_id, name="validator-retry-degraded-resume-test"))
    db.add(
        WorkflowExecution(
            id=execution_id,
            project_id=project_id,
            status="pending_validator_retry",
            result_context={
                "validator_retry": {
                    "scene_index": 0,
                    "candidate_text": "candidate text",
                    "scene_contract": {"scene_id": "scene_1"},
                },
                "resume_state": {
                    "chapter_number": 1,
                    "scene_index": 0,
                    "candidate_text": "candidate text",
                    "approved_scene_texts": {},
                    "review_version": 2,
                },
            },
        )
    )
    await db.commit()
    return project_id, execution_id


@pytest.mark.asyncio
async def test_scene_workbench_recheck_uses_approved_prior_scene_as_newer_context(
    db,
    monkeypatch,
):
    project_id, execution_id = await _make_waiting_partial_scene_review(db)
    captured = {}

    async def fake_evaluate(self, context, level="full"):
        captured.update(context)
        return {
            "passed": False,
            "violations": [{
                "type": "test_blocker",
                "severity": "high",
                "blocks_commit": True,
                "detail": "keep workbench open for assertion",
            }],
        }

    async def fake_get_state(self, project_id):
        return SimpleNamespace(model_dump=lambda: {"mark": "old baseline"})

    monkeypatch.setattr("app.services.quality_gate.QualityGate.evaluate", fake_evaluate)
    monkeypatch.setattr("app.services.state_manager.StateManager.get_state", fake_get_state)

    result = await editor_chat.recheck_scene(
        project_id=project_id,
        execution_id=str(execution_id),
        data={"candidate_text": "current scene after transition", "scene_index": 1},
        db=db,
    )

    assert result["status"] == "waiting_review"
    assert captured["previous_scene_ending"] == "approved scene zero"
    assert captured["previous_scenes_summary"] == "approved scene zero"


@pytest.mark.asyncio
async def test_validator_retry_resume_uses_narrowest_persisted_dag_layer(db):
    project_id = uuid.uuid4()
    execution_id = uuid.uuid4()
    db.add(Project(id=project_id, name="validator-resume-boundary-test"))
    db.add(WorkflowExecution(
        id=execution_id,
        project_id=project_id,
        status="pending_validator_retry",
    ))
    db.add_all([
        WorkflowStep(
            execution_id=execution_id,
            agent_name="parallel_recheck_1",
            layer=3,
            status="pending_validator_retry",
        ),
        WorkflowStep(
            execution_id=execution_id,
            agent_name="review_case_delta_merge",
            layer=4,
            status="pending_validator_retry",
        ),
    ])
    await db.commit()

    layer, resume_existing = await editor_chat._prepare_validator_retry_resume_boundary(
        str(execution_id),
        validator_retry={},
        resume_state={},
        db=db,
    )

    assert layer == 5
    assert resume_existing is True
    steps = (
        await db.execute(
            editor_chat.select(WorkflowStep).where(
                WorkflowStep.execution_id == str(execution_id)
            )
        )
    ).scalars().all()
    by_name = {step.agent_name: step for step in steps}
    assert by_name["parallel_recheck_1"].status == "completed"
    assert by_name["review_case_delta_merge"].status == "completed"
    assert by_name["review_case_delta_merge"].output_snapshot[
        "validator_retry_resume"
    ]["status"] == "validated_component"


@pytest.mark.asyncio
async def test_failed_dag_timeout_resumes_from_owned_layer_without_lower_replay(
    db, monkeypatch,
):
    project_id = uuid.uuid4()
    execution_id = uuid.uuid4()
    db.add(Project(id=project_id, name="timeout-resume-test"))
    db.add(WorkflowExecution(
        id=execution_id,
        project_id=project_id,
        status="failed",
        error_message="state_commit_2: TimeoutError timeout after 60s",
        input_context={"chapter_number": 26},
    ))
    db.add_all([
        WorkflowStep(
            execution_id=execution_id,
            agent_name="parallel_recheck_1",
            layer=3,
            status="failed",
            error_message="chapter aborted: TimeoutError timeout after 60s",
        ),
        WorkflowStep(
            execution_id=execution_id,
            agent_name="state_commit_2",
            layer=6,
            status="failed",
            error_message="[TimeoutError] timeout after 60s",
            phase_trace=[{"phase": "timeout"}],
        ),
    ])
    await db.commit()
    scheduled = []
    monkeypatch.setattr(editor_chat, "_run_editor_generation_resume", lambda **kwargs: kwargs)
    monkeypatch.setattr(
        editor_chat,
        "_spawn_workflow_task",
        lambda execution_id, work: scheduled.append((execution_id, work)),
    )

    result = await editor_chat.resume_interrupted_workflow(
        project_id=project_id,
        execution_id=str(execution_id),
        data=editor_chat.ResumeInterruptedRequest(force=True),
        db=db,
    )

    assert result["resume_from_timeout"] is True
    assert result["timeout_layer"] == 6
    execution = await db.get(WorkflowExecution, execution_id)
    assert execution.status == "running"
    steps = (
        await db.execute(
            editor_chat.select(WorkflowStep).where(
                WorkflowStep.execution_id == str(execution_id)
            )
        )
    ).scalars().all()
    by_name = {step.agent_name: step for step in steps}
    assert by_name["parallel_recheck_1"].status == "completed"
    assert by_name["state_commit_2"].status == "failed"
    assert scheduled and scheduled[0][1]["execution_id"] == str(execution_id)


@pytest.mark.asyncio
async def test_resume_restores_persisted_scene_effects_from_completed_steps(db):
    project_id = uuid.uuid4()
    execution_id = uuid.uuid4()
    db.add(Project(id=project_id, name="scene-effect-restore-test"))
    db.add(WorkflowExecution(
        id=execution_id,
        project_id=project_id,
        status="interrupted",
    ))
    payload = {
        "scene_index": 0,
        "generated_text": "scene zero",
        "effects": {"state_patch": {"location": "gate"}},
    }
    db.add_all([
        WorkflowStep(
            execution_id=execution_id,
            agent_name="state_commit_1",
            layer=5,
            status="completed",
            output_snapshot={"pending_scene_effect": payload},
        ),
        WorkflowStep(
            execution_id=execution_id,
            agent_name="state_commit_2",
            layer=6,
            status="failed",
            output_snapshot={"pending_scene_effect": {
                "scene_index": 1,
                "generated_text": "failed scene",
                "effects": {},
            }},
        ),
    ])
    await db.commit()

    restored = await editor_chat._load_persisted_pending_scene_effects(
        str(execution_id),
        db=db,
    )

    assert restored == [payload]


def test_dag_abort_preserves_resumable_validator_and_review_statuses():
    assert editor_chat._workflow_status_after_dag_abort("pending_validator_retry") is None
    assert editor_chat._workflow_status_after_dag_abort("waiting_review") is None
    assert editor_chat._workflow_status_after_dag_abort("needs_human_review") == "blocked"
    assert editor_chat._workflow_status_after_dag_abort("failed") == "failed"


def test_interrupted_resume_reuses_validator_candidate_and_approved_scenes():
    overrides, resume_state = editor_chat._interrupted_resume_overrides({
        "validator_retry": {
            "scene_index": 0,
            "candidate_text": "validated scene zero",
        },
        "resume_state": {
            "chapter_number": 24,
            "scene_index": 0,
            "approved_scene_texts": {"1": "validated scene one"},
            "review_version": 9,
        },
    })

    assert overrides == {
        0: "validated scene zero",
        1: "validated scene one",
    }
    assert resume_state["chapter_number"] == 24


def test_review_retry_disables_full_scene_rewrite_and_regeneration():
    budget = editor_chat._review_retry_recovery_budget(1, {1})
    assert budget == {
        "max_patch": 2,
        "max_rewrite": 0,
    }
    assert editor_chat._review_retry_recovery_budget(0, {1}) is None

    original = ChapterRepairOrder(
        order_id="rewrite-1",
        owner_scene=1,
        target_scenes=[1],
        repair_type="scene_rewrite",
        instruction="Resolve the structural finding.",
        work_unit_id="rewrite-unit",
        tool_commands=[{"operation": "llm_creative_rewrite", "scene_index": 1}],
        repair_brief={"patch_plan": [{"mode": "rewrite"}]},
    )
    patched = editor_chat._as_in_place_review_order(original)

    assert original.repair_type == "scene_rewrite"
    assert original.tool_commands
    assert patched.repair_type == "local_patch"
    assert patched.work_unit_id == ""
    assert patched.tool_commands == []
    assert patched.repair_brief == {"in_place_only": True}
    assert "do not regenerate or rewrite the full scene" in patched.instruction


async def _make_waiting_parallel_validator_review(db):
    project_id = uuid.uuid4()
    execution_id = uuid.uuid4()
    candidate_text = "parallel candidate text"
    violation = {
        "type": "proposition_extractor_unavailable",
        "severity": "high",
        "blocks_commit": True,
        "detail": "Extractor temporarily returned an incomplete parse.",
        "suggested_strategy": "validator_retry",
        "issue_classification": "validator_system_error",
    }
    db.add(Project(id=project_id, name="parallel-validator-retry-test"))
    db.add(
        WorkflowExecution(
            id=execution_id,
            project_id=project_id,
            status="waiting_review",
            result_context={
                "review": {
                    "scene_index": 0,
                    "review_scope": "scene",
                    "candidate_text": candidate_text,
                    "violations": [violation],
                    "error_code": "parallel_repair_needs_human_review",
                },
                "resume_state": {
                    "chapter_number": 1,
                    "scene_index": 0,
                    "review_scope": "scene",
                    "candidate_text": candidate_text,
                    "approved_scene_texts": {"1": "approved scene one"},
                    "review_version": 4,
                    "scene_contract": {"scene_id": "scene_1"},
                    "parallel_review_breakpoint": True,
                },
            },
        )
    )
    await db.commit()
    return project_id, execution_id


@pytest.mark.asyncio
async def test_completed_workflow_closes_open_and_pending_steps(db):
    project_id = uuid.uuid4()
    execution_id = uuid.uuid4()
    db.add(Project(id=project_id, name="workflow-step-finalization-test"))
    execution = WorkflowExecution(
        id=execution_id,
        project_id=project_id,
        status="running",
    )
    running = WorkflowStep(
        execution_id=execution_id,
        agent_name="redundant_review",
        layer=0,
        status="running",
    )
    pending = WorkflowStep(
        execution_id=execution_id,
        agent_name="unused_repair",
        layer=1,
        status="pending",
    )
    db.add_all([execution, running, pending])
    await db.commit()

    await editor_chat._complete_workflow_execution(
        str(execution_id),
        "completed",
        result={"candidate_ready": True},
        db=db,
    )

    await db.refresh(execution)
    await db.refresh(running)
    await db.refresh(pending)
    assert execution.status == "completed"
    assert running.status == "skipped"
    assert pending.status == "skipped"


def test_chapter_review_split_prefers_explicit_scene_markers():
    overrides, alignment = editor_chat._chapter_review_scene_overrides(
        "[[SCENE:scene_1]]\n第一场第一段。\n\n第一场第二段。\n\n[[SCENE:scene_2]]\n第二场第一段。\n\n第二场第二段。",
        {
            "scene_map": [
                {"scene_index": 0, "scene_id": "scene_1"},
                {"scene_index": 1, "scene_id": "scene_2"},
            ],
            "chapter_scene_paragraph_counts": [1, 3],
        },
    )

    assert alignment["method"] == "explicit_markers"
    assert overrides == {
        0: "第一场第一段。\n\n第一场第二段。",
        1: "第二场第一段。\n\n第二场第二段。",
    }



@pytest.mark.asyncio
async def test_commit_gate_allows_protection_rejected_quality_candidate():
    quality_order = SimpleNamespace(
        status="failed",
        repair_type="local_patch",
        repair_audit={"failures": ["protected_obligation_removed"]},
        violation_details=[{
            "type": "low_conflict_density",
            "severity": "medium",
            "blocks_commit": True,
        }],
    )

    result = await editor_chat._commit_gate_check(
        execution_id="exec-quality-rollback",
        review_packets=[],
        current_repair_plan=SimpleNamespace(orders=[quality_order]),
        db=None,
    )

    assert result == {"allowed": True, "reason": ""}

@pytest.mark.asyncio
async def test_commit_gate_accepts_list_review_packets_without_blocking():
    result = await editor_chat._commit_gate_check(
        execution_id="exec-1",
        review_packets=[
            SceneReviewPacket(scene_index=0, blocking_violations=[]),
            SceneReviewPacket(scene_index=1, blocking_violations=[]),
        ],
        current_repair_plan=None,
        db=None,
    )

    assert result == {"allowed": True, "reason": ""}


@pytest.mark.asyncio
async def test_commit_gate_blocks_content_violations_from_list_packets():
    result = await editor_chat._commit_gate_check(
        execution_id="exec-1",
        review_packets=[
            SceneReviewPacket(
                scene_index=0,
                blocking_violations=[
                    {
                        "type": "fact_conflict",
                        "severity": "high",
                        "detail": "Text conflicts with established fact.",
                    }
                ],
            )
        ],
        current_repair_plan=None,
        db=None,
    )

    assert result["allowed"] is False
    assert "content blocking violation" in result["reason"]


@pytest.mark.asyncio
async def test_commit_gate_blocks_dash_left_in_blocking_list():
    result = await editor_chat._commit_gate_check(
        execution_id="exec-1",
        review_packets=[
            SceneReviewPacket(
                scene_index=0,
                blocking_violations=[
                    {
                        "type": "ai_punctuation_artifact",
                        "severity": "medium",
                        "detail": "Dash density remains above the hard Skill contract.",
                        "blocks_commit": True,
                    }
                ],
            )
        ],
        current_repair_plan=None,
        db=None,
    )

    assert result["allowed"] is False
    assert "ai_punctuation_artifact" in result["reason"]


@pytest.mark.asyncio
async def test_commit_gate_allows_style_human_review_as_advisory():
    result = await editor_chat._commit_gate_check(
        execution_id="exec-1",
        review_packets=[],
        current_repair_plan=None,
        db=None,
        final_text="凤溪走进戒律堂。" * 100,
        scene_texts={0: "凤溪走进戒律堂。"},
        chapter_state={"established_facts": ["凤溪走进戒律堂。"]},
        style_result={"requires_human_review": True, "style_score": 50},
    )

    assert result["allowed"] is True
    assert result["reason"] == ""


@pytest.mark.asyncio
async def test_commit_gate_blocks_empty_chapter_state_for_substantial_text():
    result = await editor_chat._commit_gate_check(
        execution_id="exec-1",
        review_packets=[],
        current_repair_plan=None,
        db=None,
        final_text="凤溪走进戒律堂。" * 100,
        scene_texts={0: "凤溪走进戒律堂。"},
        chapter_state={
            "established_facts": [],
            "character_states": {},
            "completed_events": [],
            "active_constraints": [],
        },
        style_result={"requires_human_review": False, "style_score": 80},
    )

    assert result["allowed"] is False
    assert "empty_chapter_state" in result["reason"]


@pytest.mark.asyncio
async def test_commit_gate_blocks_failed_final_skill_validation():
    result = await editor_chat._commit_gate_check(
        execution_id="exec",
        review_packets=[],
        current_repair_plan=None,
        db=None,
        final_text="正文",
        scene_texts={0: "正文"},
        chapter_state={"established_facts": ["fact"]},
        skill_gate_result={
            "allowed": False,
            "reason": "final skill validation failed (ai_flavor:dash_per_1000)",
        },
    )

    assert result["allowed"] is False
    assert "ai_flavor:dash_per_1000" in result["reason"]


@pytest.mark.asyncio
async def test_workbench_dash_issue_uses_deterministic_repair(db):
    project_id = uuid.uuid4()
    execution_id = uuid.uuid4()
    candidate_text = (
        "石斫猛然停住——风从廊下卷过——灯影晃了一下——她听见身后有声响。"
        "她没有回头——只是把银线草攥紧——伤口又裂开——血顺着袖口滴落。"
        "这不是退路——是最后的机会。"
    )
    db.add(Project(id=project_id, name="dash-workbench-test"))
    db.add(
        WorkflowExecution(
            id=execution_id,
            project_id=project_id,
            status="waiting_review",
            result_context={
                "review": {
                    "scene_index": 0,
                    "candidate_text": candidate_text,
                    "review_version": 1,
                    "violations": [
                        {
                            "issue_id": "dash-issue",
                            "violation_id": "dash-issue",
                            "type": "ai_punctuation_artifact",
                            "metric": "dash_per_1000",
                            "severity": "high",
                            "scope": "prose_text",
                            "blocks_commit": True,
                            "detail": "破折号使用密度偏高。",
                            "target_span": "——",
                            "threshold": 2,
                            "max_dash_count": 2,
                        }
                    ],
                    "attempts": [],
                },
                "resume_state": {
                    "chapter_number": 1,
                    "scene_index": 0,
                    "candidate_text": candidate_text,
                    "review_version": 1,
                },
            },
        )
    )
    await db.commit()

    result = await editor_chat.chat_workflow_review_issue(
        project_id=project_id,
        execution_id=str(execution_id),
        issue_id="dash-issue",
        data=editor_chat.ReviewIssueActionRequest(review_version=1),
        db=db,
    )

    review = result["review"]
    issue = review["violations"][0]
    assert review["candidate_text"].count("——") <= 2
    assert "不是退路，而是" in review["candidate_text"]
    assert issue["review_status"] == "pending_recheck"
    assert issue["repair_engine"] == "deterministic_dash"
    assert issue["revision_diff"]["deleted_chars"] > 0


@pytest.mark.asyncio
async def test_workbench_fact_issue_uses_current_fbi_mini_plan(db):
    project_id = uuid.uuid4()
    execution_id = uuid.uuid4()
    old = "clay jar base crack ran from the rim to the center."
    expected = "stone trough base crack ran from the rim to the center."
    candidate_text = f"The pill formed. {old} Fengxi picked it up."
    detail = (
        f"Text claim '{old}' conflicts with established fact 'only a stone trough was used'; "
        "suggested correction: \"delete or replace with 'stone trough base crack' to match context\""
    )
    db.add(Project(id=project_id, name="fact-workbench-test"))
    db.add(
        WorkflowExecution(
            id=execution_id,
            project_id=project_id,
            status="waiting_review",
            result_context={
                "review": {
                    "scene_index": 0,
                    "candidate_text": candidate_text,
                    "review_version": 1,
                    "violations": [
                        {
                            "issue_id": "fact-issue",
                            "violation_id": "fact-issue",
                            "type": "internal_conflict",
                            "severity": "high",
                            "scope": "prose_text",
                            "blocks_commit": True,
                            "detail": detail,
                            "target_span": old,
                            "expected_behavior": "replace the wrong object with the established object",
                        }
                    ],
                    "attempts": [],
                },
                "resume_state": {
                    "chapter_number": 1,
                    "scene_index": 0,
                    "candidate_text": candidate_text,
                    "review_version": 1,
                },
            },
        )
    )
    await db.commit()

    result = await editor_chat.chat_workflow_review_issue(
        project_id=project_id,
        execution_id=str(execution_id),
        issue_id="fact-issue",
        data=editor_chat.ReviewIssueActionRequest(review_version=1),
        db=db,
    )

    review = result["review"]
    issue = review["violations"][0]
    assert expected in review["candidate_text"]
    assert old not in review["candidate_text"]
    assert len(review["candidate_text"]) < len(candidate_text) + 30
    assert issue["review_status"] == "pending_recheck"
    assert issue["repair_engine"] == "fbi_chapter_mini_plan"
    assert issue["fbi_status"] == "needs_repair"
    assert issue["revision_diff"]["added_chars"] > 0


@pytest.mark.asyncio
async def test_workbench_external_protection_is_compiled_before_scope_and_updates_boundaries(
    db,
    monkeypatch,
):
    project_id = uuid.uuid4()
    execution_id = uuid.uuid4()
    scene_zero = "battle end\n\npremature wake-up\n\npremature recognition"
    scene_one = "single later wake-up\n\n你们是谁？"
    candidate_text = f"{scene_zero}\n\n{scene_one}"
    db.add(Project(id=project_id, name="external-protection-workbench-test"))
    db.add(
        WorkflowExecution(
            id=execution_id,
            project_id=project_id,
            status="waiting_review",
            trigger_type="editor_generation",
            current_layer=11,
            total_layers=11,
            result_context={
                "review": {
                    "review_type": "content_review",
                    "candidate_text": candidate_text,
                    "review_version": 1,
                    "violations": [{
                        "violation_id": "external-target",
                        "issue_id": "external-target",
                        "type": "internal_conflict",
                        "severity": "critical",
                        "blocks_commit": True,
                        "repairable_by_text": True,
                        "source_scene": 0,
                        "source_scenes": [0],
                        "target_span": "你们是谁？",
                        "detail": "The earlier scene duplicates the later wake-up.",
                        "expected_behavior": "Delete one duplicate branch.",
                    }],
                    "attempts": [],
                },
                "resume_state": {
                    "chapter_number": 28,
                    "scene_index": 0,
                    "review_scope": "chapter",
                    "candidate_text": candidate_text,
                    "review_version": 1,
                    "scene_map": [
                        {"scene_index": 0, "scene_id": "scene-0"},
                        {"scene_index": 1, "scene_id": "scene-1"},
                    ],
                    "chapter_scene_paragraph_counts": [3, 2],
                    "scene_contracts": {
                        "0": {"scene_id": "scene-0"},
                        "1": {"scene_id": "scene-1"},
                    },
                },
            },
        )
    )
    await db.commit()
    captured = {}

    async def fake_fbi_repair(**kwargs):
        captured.update(kwargs)
        return {
            "repaired_text": "battle end\n\nvoices reached her",
            "repairs": [{"reason": "consolidate duplicate event"}],
            "success": True,
            "repair_engine": "fbi_chapter_mini_plan",
            "fbi_status": "needs_repair",
            "fbi_case_id": "case-external-target",
            "failed_orders": [],
        }

    monkeypatch.setattr(editor_chat, "_run_fbi_workbench_issue_repair", fake_fbi_repair)
    result = await editor_chat.chat_workflow_review_issue(
        project_id=project_id,
        execution_id=str(execution_id),
        issue_id="external-target",
        data=editor_chat.ReviewIssueActionRequest(
            review_version=1,
            message="保留“你们是谁？”，只收束前一场重复的苏醒内容。",
        ),
        db=db,
    )

    assert captured["candidate_text"] == scene_zero
    assert captured["targeted_issue"]["source_scene"] == 0
    assert captured["targeted_issue"]["source_scenes"] == [0, 1]
    assert captured["targeted_issue"]["external_protected_span"]["scene_index"] == 1
    review = result["review"]
    assert review["candidate_text"].count("你们是谁？") == 1
    assert "premature wake-up" not in review["candidate_text"]
    issue = review["violations"][0]
    assert issue["review_status"] == "pending_recheck"
    assert issue["protected_span_checks"][0]["passed"] is True
    execution = await db.get(WorkflowExecution, execution_id)
    assert execution.result_context["resume_state"]["chapter_scene_paragraph_counts"] == [2, 2]


@pytest.mark.asyncio
async def test_accept_edited_review_resumes_canonical_commit_path(db, monkeypatch):
    project_id, execution_id = await _make_waiting_review(db)
    captured = {}

    def fake_run_editor_generation(**kwargs):
        captured.update(kwargs)
        return "scheduled"

    monkeypatch.setattr(editor_chat, "_run_editor_generation", fake_run_editor_generation)
    monkeypatch.setattr(editor_chat.asyncio, "create_task", lambda task: task)

    result = await editor_chat.resume_workflow_review(
        project_id=project_id,
        execution_id=str(execution_id),
        data=editor_chat.ResumeWorkflowReviewRequest(
            action="accept_edited",
            edited_text="manually fixed text",
            review_version=3,
        ),
        db=db,
    )

    assert result["status"] == "running"
    execution = await db.get(WorkflowExecution, execution_id)
    assert execution.status == "running"
    persisted = execution.result_context
    assert persisted["resumed_from_review"] is True
    assert persisted["human_revision_accepted"] is True
    assert "candidate_ready" not in persisted
    assert persisted["review_resume"]["canonical_commit_required"] is True
    assert persisted["resume_state"]["chapter_number"] == 1
    assert persisted["resume_state"]["candidate_text"] == "manually fixed text"
    assert persisted["resume_state"]["approved_scene_texts"] == {"0": "manually fixed text"}
    assert persisted["review"]["candidate_text"] == "manually fixed text"
    assert captured["reviewed_scene_overrides"] == {0: "manually fixed text"}
    assert captured["skip_editor_planning"] is True
    assert captured["skip_reviewed_scene_validation"] is True
    assert captured["reviewed_scene_repair_indexes"] == {0}


@pytest.mark.asyncio
async def test_accept_without_recheck_records_human_override_and_skips_content_validation(
    db,
    monkeypatch,
):
    project_id, execution_id = await _make_waiting_review(db)
    execution = await db.get(WorkflowExecution, execution_id)
    persisted = dict(execution.result_context or {})
    persisted["review"] = {
        "scene_index": 0,
        "review_scope": "scene",
        "candidate_text": "old text",
        "current_text_hash": editor_chat.hashlib.md5("old text".encode()).hexdigest()[:12],
        "review_version": 3,
        "passed": False,
        "violations": [{
            "issue_id": "blocking-fact-1",
            "type": "fact_conflict",
            "blocks_commit": True,
            "review_status": "open",
        }],
    }
    execution.result_context = persisted
    await db.commit()
    captured = {}

    async def forbidden_quality_gate(*args, **kwargs):
        raise AssertionError("accept_without_recheck must not call QualityGate")

    def fake_run_editor_generation(**kwargs):
        captured.update(kwargs)
        return "scheduled"

    monkeypatch.setattr("app.services.quality_gate.QualityGate.evaluate", forbidden_quality_gate)
    monkeypatch.setattr(editor_chat, "_run_editor_generation", fake_run_editor_generation)
    monkeypatch.setattr(editor_chat.asyncio, "create_task", lambda task: task)

    result = await editor_chat.resume_workflow_review(
        project_id=project_id,
        execution_id=str(execution_id),
        data=editor_chat.ResumeWorkflowReviewRequest(
            action="accept_without_recheck",
            edited_text="human approved text",
            review_version=3,
        ),
        db=db,
    )

    assert result["status"] == "running"
    execution = await db.get(WorkflowExecution, execution_id)
    persisted = execution.result_context
    override = persisted["human_review_override"]
    assert override["skip_content_recheck"] is True
    assert override["source"] == "manual_accept_without_recheck"
    assert override["unresolved_issue_ids"] == ["blocking-fact-1"]
    assert persisted["review"]["passed"] is False
    assert persisted["review"]["review_resolution"] == "human_override"
    assert persisted["review_resume"]["content_recheck_skipped"] is True
    assert persisted["review_resume"]["canonical_commit_required"] is True
    assert captured["reviewed_scene_overrides"] == {0: "human approved text"}
    assert captured["reviewed_scene_repair_indexes"] == set()
    assert captured["skip_editor_planning"] is True
    assert captured["skip_reviewed_scene_validation"] is True
    assert captured["human_review_override"] is True


@pytest.mark.asyncio
async def test_recheck_pass_auto_resumes_and_maps_candidate_to_scene(db, monkeypatch):
    project_id, execution_id = await _make_waiting_review(db)
    captured = {}

    async def fake_evaluate(self, context, level="full"):
        assert context["generated_text"] == "automatically repaired text"
        return {"passed": True, "violations": []}

    async def fake_get_state(self, project_id):
        return SimpleNamespace(model_dump=lambda: {})

    def fake_run_editor_generation(**kwargs):
        captured.update(kwargs)
        return "scheduled"

    monkeypatch.setattr("app.services.quality_gate.QualityGate.evaluate", fake_evaluate)
    monkeypatch.setattr("app.services.state_manager.StateManager.get_state", fake_get_state)
    monkeypatch.setattr(editor_chat, "_run_editor_generation", fake_run_editor_generation)
    monkeypatch.setattr(editor_chat.asyncio, "create_task", lambda task: task)

    result = await editor_chat.recheck_scene(
        project_id=project_id,
        execution_id=str(execution_id),
        data={"candidate_text": "automatically repaired text"},
        db=db,
    )

    assert result["passed"] is True
    assert result["auto_resumed"] is True
    assert result["status"] == "running"
    assert result["review_version"] == 4
    execution = await db.get(WorkflowExecution, execution_id)
    assert execution.status == "running"
    assert execution.result_context["resume_state"]["approved_scene_texts"] == {
        "0": "automatically repaired text",
    }
    assert execution.result_context["review_resume"] == {
        "source": "auto_recheck_passed",
        "status": "running",
        "review_scope": "scene",
        "scene_indexes": [0],
        "text_hash": editor_chat.hashlib.md5("automatically repaired text".encode()).hexdigest()[:12],
        "already_validated": True,
        "resolution": "already_validated",
        "content_recheck_skipped": False,
        "canonical_commit_required": True,
        "resume_from_layer": 0,
        "resume_existing_dag": False,
    }
    assert captured["reviewed_scene_overrides"] == {0: "automatically repaired text"}
    assert captured["reviewed_scene_repair_indexes"] == set()
    assert captured["skip_editor_planning"] is True
    assert captured["skip_reviewed_scene_validation"] is True
    assert captured["resume_existing_dag"] is False


@pytest.mark.asyncio
async def test_chapter_workbench_skill_recheck_uses_all_persisted_scene_contracts(db, monkeypatch):
    captured = {}

    class FakeGate:
        async def evaluate(self, **kwargs):
            captured.update(kwargs)
            return {"allowed": True, "reason": "", "trace": {}}

    monkeypatch.setattr(
        "app.services.agent_skill_commit_gate.get_agent_skill_commit_gate",
        lambda: FakeGate(),
    )
    resume_state = {
        "chapter_number": 7,
        "review_scope": "chapter",
        "scene_map": [
            {"scene_index": 0, "scene_id": "scene_a"},
            {"scene_index": 1, "scene_id": "scene_b"},
        ],
        "chapter_scene_paragraph_counts": [2, 1],
        "scene_contracts": {
            "0": {"scene_id": "scene_a", "pov_character": "林澈"},
            "1": {"scene_id": "scene_b", "pov_character": "林澈"},
        },
    }

    result = await editor_chat._workbench_skill_recheck(
        project_id=uuid.uuid4(),
        candidate_text="s1-a\n\ns1-b\n\ns2-a",
        review={"review_scope": "chapter"},
        resume_state=resume_state,
        db=db,
    )

    assert result["executed"] is True
    assert result["allowed"] is True
    assert [unit["text"] for unit in captured["scene_units"]] == [
        "s1-a\n\ns1-b",
        "s2-a",
    ]
    assert [unit["scene_contract"]["scene_id"] for unit in captured["scene_units"]] == [
        "scene_a",
        "scene_b",
    ]
    assert captured["allow_llm_repair"] is False


@pytest.mark.asyncio
async def test_chapter_workbench_skill_recheck_does_not_block_on_soft_findings(db, monkeypatch):
    class FakeGate:
        async def evaluate(self, **kwargs):
            return {
                "allowed": False,
                "reason": "final skill validation failed (scene_evidence:emotion_label_count)",
                "trace": {
                    "initial_validation": {
                        "passed": False,
                        "failures": [{
                            "skill_id": "show_dont_tell",
                            "validator": "scene_evidence",
                            "metric": "emotion_label_count",
                            "actual": 1,
                            "expected": 0,
                            "severity": "high",
                            "target_span": "她心里没有恐惧",
                            "reason": "Emotion label remains.",
                        }],
                    },
                },
            }

    monkeypatch.setattr(
        "app.services.agent_skill_commit_gate.get_agent_skill_commit_gate",
        lambda: FakeGate(),
    )
    resume_state = {
        "chapter_number": 7,
        "review_scope": "chapter",
        "scene_map": [{"scene_index": 0, "scene_id": "scene_a"}],
        "chapter_scene_paragraph_counts": [1],
        "scene_contracts": {
            "0": {"scene_id": "scene_a", "pov_character": "林澈"},
        },
    }

    result = await editor_chat._workbench_skill_recheck(
        project_id=uuid.uuid4(),
        candidate_text="她心里没有恐惧。",
        review={"review_scope": "chapter"},
        resume_state=resume_state,
        db=db,
    )

    assert result["executed"] is True
    assert result["allowed"] is True
    assert result["reason"] == "soft_skill_findings_only"
    assert result["violations"] == []
    assert result["advisory_count"] == 1
    assert "emotion_label_count" in result["raw_reason"]


@pytest.mark.asyncio
async def test_recheck_keeps_workbench_open_when_skill_gate_still_blocks(db, monkeypatch):
    project_id, execution_id = await _make_waiting_chapter_review(db)

    async def fake_evaluate(self, context, level="full"):
        return {"passed": True, "violations": []}

    async def fake_get_state(self, project_id):
        return SimpleNamespace(model_dump=lambda: {})

    async def fake_skill_recheck(**kwargs):
        return {
            "executed": True,
            "allowed": False,
            "reason": "POV contract failed",
            "alignment": "paragraph_counts",
            "violations": [{
                "type": "head_hopping_count",
                "metric": "head_hopping_count",
                "severity": "high",
                "detail": "Non-POV inner access remains.",
                "target_span": "他觉得门后已经没人了",
                "blocks_commit": True,
                "scope": "scene",
                "source_scene": 1,
            }],
        }

    monkeypatch.setattr("app.services.quality_gate.QualityGate.evaluate", fake_evaluate)
    monkeypatch.setattr("app.services.state_manager.StateManager.get_state", fake_get_state)
    monkeypatch.setattr(editor_chat, "_workbench_skill_recheck", fake_skill_recheck)

    result = await editor_chat.recheck_scene(
        project_id=project_id,
        execution_id=str(execution_id),
        data={"candidate_text": "s1-a\n\ns1-b\n\ns2-a"},
        db=db,
    )

    assert result["passed"] is False
    assert result["status"] == "waiting_review"
    assert [item["type"] for item in result["violations"]] == ["head_hopping_count"]
    assert result["review"]["skill_recheck"] == {
        "executed": True,
        "allowed": False,
        "reason": "POV contract failed",
        "alignment": "paragraph_counts",
        "violation_count": 1,
    }


@pytest.mark.asyncio
async def test_validated_l4_review_reruns_convergence_gate_before_ordered_commit(db, monkeypatch):
    project_id, execution_id = await _make_waiting_review(db)
    execution = await db.get(WorkflowExecution, execution_id)
    persisted = dict(execution.result_context or {})
    resume_state = dict(persisted.get("resume_state") or {})
    resume_state["human_review_step"] = "review_case_delta_merge"
    persisted["resume_state"] = resume_state
    execution.result_context = persisted
    db.add_all([
        WorkflowStep(
            execution_id=str(execution_id),
            agent_name="chapter_review",
            layer=0,
            status="completed",
            output_snapshot={"kept": True},
        ),
        WorkflowStep(
            execution_id=str(execution_id),
            agent_name="review_case_delta_merge",
            layer=4,
            status="waiting_review",
            output_snapshot={"checkpoint": True},
        ),
        WorkflowStep(
            execution_id=str(execution_id),
            agent_name="ordered_commit_1",
            layer=5,
            status="completed",
            output_snapshot={"old": True},
        ),
    ])
    await db.commit()
    captured = {}

    async def fake_evaluate(self, context, level="full"):
        return {"passed": True, "violations": []}

    async def fake_get_state(self, project_id):
        return SimpleNamespace(model_dump=lambda: {})

    def fake_run_editor_generation(**kwargs):
        captured.update(kwargs)
        return "scheduled"

    monkeypatch.setattr("app.services.quality_gate.QualityGate.evaluate", fake_evaluate)
    monkeypatch.setattr("app.services.state_manager.StateManager.get_state", fake_get_state)
    monkeypatch.setattr(editor_chat, "_run_editor_generation", fake_run_editor_generation)
    monkeypatch.setattr(editor_chat.asyncio, "create_task", lambda task: task)

    result = await editor_chat.recheck_scene(
        project_id=project_id,
        execution_id=str(execution_id),
        data={"candidate_text": "validated merge candidate"},
        db=db,
    )

    assert result["status"] == "running"
    assert captured["resume_existing_dag"] is True
    execution = await db.get(WorkflowExecution, execution_id)
    await db.refresh(execution)
    assert execution.current_layer == 4
    assert execution.result_context["review_resume"]["resume_from_layer"] == 4
    assert execution.result_context["review_resume"]["resume_existing_dag"] is True

    steps = {
        step.agent_name: step
        for step in (
            await db.execute(
                editor_chat.select(WorkflowStep).where(
                    WorkflowStep.execution_id == str(execution_id)
                )
            )
        ).scalars().all()
    }
    assert steps["chapter_review"].status == "completed"
    assert steps["chapter_review"].output_snapshot == {"kept": True}
    assert steps["review_case_delta_merge"].status == "pending"
    assert steps["review_case_delta_merge"].output_snapshot == {}
    assert steps["ordered_commit_1"].status == "pending"
    assert steps["ordered_commit_1"].output_snapshot == {}


@pytest.mark.asyncio
async def test_recheck_failure_keeps_workbench_and_does_not_resume(db, monkeypatch):
    project_id, execution_id = await _make_waiting_review(db)
    scheduled = []

    async def fake_evaluate(self, context, level="full"):
        return {
            "passed": False,
            "violations": [{
                "type": "fact_conflict",
                "blocks_commit": True,
                "detail": "Conflict remains.",
            }],
        }

    async def fake_get_state(self, project_id):
        return SimpleNamespace(model_dump=lambda: {})

    monkeypatch.setattr("app.services.quality_gate.QualityGate.evaluate", fake_evaluate)
    monkeypatch.setattr("app.services.state_manager.StateManager.get_state", fake_get_state)
    monkeypatch.setattr(editor_chat, "_spawn_workflow_task", lambda *args: scheduled.append(args))

    result = await editor_chat.recheck_scene(
        project_id=project_id,
        execution_id=str(execution_id),
        data={"candidate_text": "still conflicting text"},
        db=db,
    )

    assert result["passed"] is False
    assert result["auto_resumed"] is False
    assert result["status"] == "waiting_review"
    assert scheduled == []
    execution = await db.get(WorkflowExecution, execution_id)
    assert execution.status == "waiting_review"
    assert execution.result_context["review"]["guidance"]["blocking_count"] == 1


@pytest.mark.asyncio
async def test_recheck_validator_failure_leaves_content_workbench_for_system_retry(db, monkeypatch):
    project_id, execution_id = await _make_waiting_review(db)
    scheduled = {}

    async def fake_evaluate(self, context, level="full"):
        return {
            "passed": False,
            "violations": [{
                "type": "proposition_extractor_unavailable",
                "severity": "high",
                "blocks_commit": False,
                "issue_classification": "validator_system_error",
                "detail": "Extractor returned no reliable propositions.",
            }],
        }

    async def fake_get_state(self, project_id):
        return SimpleNamespace(model_dump=lambda: {})

    monkeypatch.setattr("app.services.quality_gate.QualityGate.evaluate", fake_evaluate)
    monkeypatch.setattr("app.services.state_manager.StateManager.get_state", fake_get_state)
    monkeypatch.setattr(
        editor_chat,
        "_schedule_delayed_validator_retries",
        lambda **kwargs: scheduled.update(kwargs),
    )

    result = await editor_chat.recheck_scene(
        project_id=project_id,
        execution_id=str(execution_id),
        data={"candidate_text": "unchanged candidate text"},
        db=db,
    )

    assert result["status"] == "pending_validator_retry"
    assert result["system_retry_scheduled"] is True
    assert result["auto_resumed"] is False
    assert scheduled["delays"] == [5, 15, 45]
    execution = await db.get(WorkflowExecution, execution_id)
    assert execution.status == "pending_validator_retry"
    assert execution.result_context["validator_retry"]["source"] == "workbench_recheck"


@pytest.mark.asyncio
async def test_recheck_keeps_mixed_validator_and_content_findings_in_correct_lanes(db, monkeypatch):
    project_id, execution_id = await _make_waiting_review(db)
    scheduled = []

    async def fake_evaluate(self, context, level="full"):
        return {
            "passed": False,
            "violations": [
                {
                    "type": "proposition_extractor_unavailable",
                    "severity": "high",
                    "blocks_commit": False,
                    "detail": "Extractor returned no reliable propositions.",
                },
                {
                    "type": "missing_must_show",
                    "severity": "high",
                    "blocks_commit": True,
                    "detail": "The required final line is missing.",
                    "target_span": "unchanged candidate text",
                },
            ],
        }

    async def fake_get_state(self, project_id):
        return SimpleNamespace(model_dump=lambda: {})

    monkeypatch.setattr("app.services.quality_gate.QualityGate.evaluate", fake_evaluate)
    monkeypatch.setattr("app.services.state_manager.StateManager.get_state", fake_get_state)
    monkeypatch.setattr(
        editor_chat,
        "_schedule_delayed_validator_retries",
        lambda **kwargs: scheduled.append(kwargs),
    )

    result = await editor_chat.recheck_scene(
        project_id=project_id,
        execution_id=str(execution_id),
        data={"candidate_text": "unchanged candidate text"},
        db=db,
    )

    assert result["status"] == "waiting_review"
    assert result["auto_resumed"] is False
    assert scheduled == []
    by_type = {item["type"]: item for item in result["review"]["violations"]}
    assert by_type["proposition_extractor_unavailable"]["review_status"] == "pending_validator_retry"
    assert by_type["missing_must_show"]["review_status"] == "open"
    execution = await db.get(WorkflowExecution, execution_id)
    assert execution.error_message == "Current text re-verify found 1 content blocking issue(s)."
    assert execution.result_context["resume_state"]["validator_retry_deferred_by_content"] is True


def test_review_guidance_does_not_count_fbi_resolved_issue_as_pending():
    guidance = editor_chat._build_review_guidance([{
        "type": "fact_conflict",
        "blocks_commit": True,
        "user_visible": True,
        "review_status": "open",
        "fbi_status": "resolved",
    }], [])

    assert guidance["blocking_count"] == 0


def test_workbench_exact_missing_utterance_uses_deterministic_repair():
    text = "魔君的声音越来越弱。\n\n“他需要你活着。”\n\n最后一句没有说完。"
    result = editor_chat._try_deterministic_workbench_repair(
        text,
        {
            "type": "missing_must_show",
            "target_span": "“他需要你活着。”",
            "expected_behavior": "魔君应说出'你毁掉的不仅是……'（未说完）",
        },
        scene_index=1,
    )

    assert result is not None
    assert result["success"] is True
    assert result["repair_engine"] == "deterministic_required_utterance"
    assert "“你毁掉的不仅是……”" in result["repaired_text"]
    assert "他需要你活着" not in result["repaired_text"]


def test_final_gate_dash_cleanup_repairs_whole_chapter_without_llm():
    text = ("甲——乙。" * 11) + ("正文" * 4000)
    result = editor_chat._apply_deterministic_final_gate_repairs(
        text,
        [{
            "issue_id": "dash-final",
            "type": "dash_per_1000",
            "metric": "dash_per_1000",
            "source_scene": 0,
            "expected_max": 0.5,
            "blocks_commit": True,
        }],
    )

    from app.utils.dash_artifacts import count_dash_artifacts, dash_artifact_allowed_count

    assert result["applied"] is True
    assert result["llm_calls"] == 0
    assert count_dash_artifacts(result["repaired_text"]) <= dash_artifact_allowed_count(
        result["repaired_text"],
        0.5,
    )


def test_chapter_dash_workbench_repairs_whole_candidate_not_one_scene():
    candidate = "scene zero——x\n\nscene one——y"
    scope = editor_chat._workbench_issue_repair_scope(
        candidate,
        {
            "type": "dash_per_1000",
            "metric": "dash_per_1000",
            "source_scene": 0,
        },
        {
            "scene_index": 0,
            "review_scope": "chapter",
            "scene_contract": {"scene_id": "scene-0"},
            "scene_contracts": {"0": {"scene_id": "scene-0"}},
        },
        {"review_scope": "chapter"},
    )

    assert scope["whole_chapter_text"] is True
    assert scope["candidate_text"] == candidate
    assert editor_chat._merge_workbench_repaired_scene(scope, "cleaned") == "cleaned"


def test_chapter_workbench_uses_unique_target_span_to_correct_scene_owner():
    scope = editor_chat._workbench_issue_repair_scope(
        "first scene\n\nsecond scene has target",
        {
            "type": "internal_conflict",
            "source_scene": 0,
            "source_scenes": [0],
            "target_span": "target",
        },
        {
            "scene_index": 0,
            "review_scope": "chapter",
            "scene_map": [
                {"scene_index": 0, "scene_id": "scene-0"},
                {"scene_index": 1, "scene_id": "scene-1"},
            ],
            "chapter_scene_paragraph_counts": [1, 1],
            "scene_contracts": {
                "0": {"scene_id": "scene-0"},
                "1": {"scene_id": "scene-1"},
            },
        },
        {"review_scope": "chapter"},
    )

    assert scope["scene_index"] == 1
    assert scope["candidate_text"] == "second scene has target"
    assert scope["scene_contract"]["scene_id"] == "scene-1"


def test_chapter_workbench_protected_external_target_keeps_original_owner():
    candidate = (
        "owner opening\n\nowner duplicate wake-up\n\nowner recognition\n\n"
        "later unique ending\n\n你们是谁？"
    )
    scope = editor_chat._workbench_issue_repair_scope(
        candidate,
        {
            "type": "internal_conflict",
            "source_scene": 0,
            "source_scenes": [0],
            "target_span": "你们是谁？",
            "user_repair_instruction": "保留“你们是谁？”，只收束前一场重复的苏醒内容。",
        },
        {
            "scene_index": 0,
            "review_scope": "chapter",
            "scene_map": [
                {"scene_index": 0, "scene_id": "scene-0"},
                {"scene_index": 1, "scene_id": "scene-1"},
            ],
            "chapter_scene_paragraph_counts": [3, 2],
            "scene_contracts": {
                "0": {"scene_id": "scene-0"},
                "1": {"scene_id": "scene-1"},
            },
        },
        {"review_scope": "chapter"},
    )

    assert scope["scene_index"] == 0
    assert scope["candidate_text"].endswith("owner recognition")
    assert scope["external_protected_span"] == {
        "span": "你们是谁？",
        "scene_index": 1,
        "expected_count": 1,
    }
    repaired_scene = "owner opening\n\nowner transition"
    merged = editor_chat._merge_workbench_repaired_scene(scope, repaired_scene)
    checks = editor_chat._validate_workbench_protected_spans(scope, candidate, merged)

    assert merged.count("你们是谁？") == 1
    assert checks == [{
        "span": "你们是谁？",
        "expected_count": 1,
        "actual_count": 1,
        "passed": True,
    }]
    assert editor_chat._workbench_scene_paragraph_counts(scope, repaired_scene) == [2, 2]


def test_workbench_external_protected_target_uses_bounded_consolidation_route():
    from app.models.chapter_review import ChapterRepairOrder, ChapterRepairPlan
    from app.services.fbi.chapter_repair_executor import ChapterRepairExecutor
    from app.services.fbi.work_unit_builder import build_revision_blueprint

    plan = ChapterRepairPlan(
        case_id="workbench-external-protected",
        status="needs_repair",
        orders=[ChapterRepairOrder(
            order_id="repair-owner-scene",
            owner_scene=0,
            target_scenes=[0],
            repair_type="local_patch",
            source_violation_ids=["issue-external"],
            repair_brief={
                "fact_repair_goal": {
                    "forbidden_claims": ["你们是谁？"],
                },
                "repair_goals": [{
                    "goal_id": "goal-external",
                    "desired_state": "delete the later protected line",
                    "acceptance_criteria": [{"metric": "internal_conflict"}],
                }],
                "patch_plan": [{
                    "operation": "replace_phrase",
                    "from": "你们是谁？",
                    "to": "",
                }],
            },
        )],
    )
    issue = {
        "issue_id": "issue-external",
        "type": "internal_conflict",
        "target_span": "你们是谁？",
        "detail": "Two scenes describe the same event with incompatible outcomes.",
        "expected_behavior": "Delete either duplicate branch.",
        "user_repair_instruction": "保留“你们是谁？”，只收束前一场的重复事件。",
        "external_protected_span": {
            "span": "你们是谁？",
            "scene_index": 1,
            "expected_count": 1,
        },
    }

    assert editor_chat._preserve_workbench_fact_repair_hint(
        plan,
        issue,
        ChapterRepairExecutor,
    ) is False
    assert editor_chat._route_workbench_semantic_repair(plan, issue) is True
    blueprint = build_revision_blueprint(plan.case_id, plan.orders)
    command = blueprint.work_units[0].tool_batch.commands[0]
    order = plan.orders[0]

    assert command.operation == "llm_creative_rewrite"
    assert command.scene_index == 0
    assert command.postconditions == {}
    assert order.repair_brief["workbench_semantic_route"] == "bounded_cross_scene_consolidation"
    assert "fact_repair_goal" not in order.repair_brief
    assert order.repair_brief["repair_goals"][0]["desired_state"].startswith("保留")
    assert order.write_scope == ["scene:0"]
    assert "scene:1" in order.read_scope


def test_workbench_non_literal_fact_suggestion_routes_to_one_creative_unit():
    from app.models.chapter_review import ChapterRepairOrder, ChapterRepairPlan
    from app.services.fbi.work_unit_builder import build_revision_blueprint

    plan = ChapterRepairPlan(
        case_id="workbench-semantic-route",
        status="needs_repair",
        orders=[ChapterRepairOrder(
            order_id="repair-one",
            owner_scene=1,
            target_scenes=[1],
            repair_type="local_patch",
            source_violation_ids=["issue-one"],
            repair_brief={
                "fact_repair_goal": {"conflict_type": "missing_context_bridge"},
                "patch_plan": [{
                    "operation": "replace_phrase",
                    "from": "你们是谁？",
                    "to": "你们是谁？",
                }],
            },
        )],
    )
    issue = {
        "issue_id": "issue-one",
        "type": "internal_conflict",
        "target_span": "你们是谁？",
        "detail": "删除问句，或在前文铺垫记忆丧失。",
        "expected_behavior": "保留问句并补足失忆因果。",
        "user_repair_instruction": "保留“你们是谁？”，只补足失忆因果。",
    }

    assert editor_chat._route_workbench_semantic_repair(plan, issue) is True
    blueprint = build_revision_blueprint(plan.case_id, plan.orders)

    assert len(blueprint.work_units) == 1
    command = blueprint.work_units[0].tool_batch.commands[0]
    assert command.operation == "llm_creative_rewrite"
    assert command.scene_index == 1
    assert command.target_span == "你们是谁？"
    assert command.guards["forbid_unrelated_edits"] is True
    assert command.postconditions["required_spans"] == ["你们是谁？"]
    assert plan.orders[0].repair_brief["workbench_semantic_route"] == "bounded_creative_work_unit"
    assert plan.orders[0].repair_brief["target_behavior"].startswith("保留")
    assert plan.orders[0].expected_after_repair["preserve"] == ["你们是谁？"]


@pytest.mark.asyncio
async def test_retry_scene_review_repairs_existing_candidate_without_replanning(db, monkeypatch):
    project_id, execution_id = await _make_waiting_review(db)
    captured = {}

    def fake_run_editor_generation(**kwargs):
        captured.update(kwargs)
        return "scheduled"

    monkeypatch.setattr(editor_chat, "_run_editor_generation", fake_run_editor_generation)
    monkeypatch.setattr(editor_chat.asyncio, "create_task", lambda task: task)

    result = await editor_chat.resume_workflow_review(
        project_id=project_id,
        execution_id=str(execution_id),
        data=editor_chat.ResumeWorkflowReviewRequest(
            action="retry",
            review_version=3,
        ),
        db=db,
    )

    assert result["status"] == "running"
    assert captured["skip_editor_planning"] is True
    assert captured["skip_reviewed_scene_validation"] is True
    assert captured["reviewed_scene_overrides"] == {0: "old text"}
    assert captured["reviewed_scene_repair_indexes"] == {0}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("execution_status", "error_message"),
    [
        ("failed", "scene_review_1: TimeoutError timeout after 360s"),
        ("interrupted", ""),
    ],
)
async def test_validated_candidate_can_resume_after_transient_review_timeout(
    db,
    monkeypatch,
    execution_status,
    error_message,
):
    project_id, execution_id = await _make_waiting_review(db)
    execution = await db.get(WorkflowExecution, execution_id)
    text_hash = editor_chat.hashlib.md5("old text".encode()).hexdigest()[:12]
    execution.status = execution_status
    execution.error_message = error_message
    execution.result_context = {
        **(execution.result_context or {}),
        "review": {
            "scene_index": 0,
            "review_scope": "scene",
            "candidate_text": "old text",
            "current_text_hash": text_hash,
            "review_version": 3,
            "passed": True,
            "violations": [],
        },
    }
    await db.commit()
    captured = {}

    def fake_run_editor_generation(**kwargs):
        captured.update(kwargs)
        return "scheduled"

    monkeypatch.setattr(editor_chat, "_run_editor_generation", fake_run_editor_generation)
    monkeypatch.setattr(editor_chat.asyncio, "create_task", lambda task: task)

    result = await editor_chat.resume_workflow_review(
        project_id=project_id,
        execution_id=str(execution_id),
        data=editor_chat.ResumeWorkflowReviewRequest(
            action="accept_edited",
            edited_text="old text",
            review_version=3,
        ),
        db=db,
    )

    assert result["status"] == "running"
    execution = await db.get(WorkflowExecution, execution_id)
    assert execution.status == "running"
    assert captured["reviewed_scene_overrides"] == {0: "old text"}
    assert captured["skip_editor_planning"] is True


@pytest.mark.asyncio
async def test_accept_edited_chapter_review_splits_and_resumes_commit_path(db, monkeypatch):
    project_id, execution_id = await _make_waiting_chapter_review(db)
    captured = {}

    def fake_run_editor_generation(**kwargs):
        captured.update(kwargs)
        return "scheduled"

    monkeypatch.setattr(editor_chat, "_run_editor_generation", fake_run_editor_generation)
    monkeypatch.setattr(editor_chat.asyncio, "create_task", lambda task: task)

    result = await editor_chat.resume_workflow_review(
        project_id=project_id,
        execution_id=str(execution_id),
        data=editor_chat.ResumeWorkflowReviewRequest(
            action="accept_edited",
            edited_text="s1-a revised\n\ns1-b revised\n\ns2-a revised",
            review_version=5,
        ),
        db=db,
    )

    assert result["status"] == "running"
    execution = await db.get(WorkflowExecution, execution_id)
    assert execution.status == "running"
    persisted = execution.result_context
    assert persisted["resume_state"]["approved_scene_texts"] == {
        "0": "s1-a revised\n\ns1-b revised",
        "1": "s2-a revised",
    }
    assert persisted["resume_state"]["candidate_text"] == "s1-a revised\n\ns1-b revised\n\ns2-a revised"
    assert "candidate_ready" not in persisted
    assert persisted["review_resume"]["canonical_commit_required"] is True
    assert captured["reviewed_scene_overrides"] == {
        0: "s1-a revised\n\ns1-b revised",
        1: "s2-a revised",
    }
    assert captured["reviewed_scene_repair_indexes"] == {0, 1}


@pytest.mark.asyncio
async def test_retry_chapter_review_repairs_aligned_existing_scenes(db, monkeypatch):
    project_id, execution_id = await _make_waiting_chapter_review(db)
    captured = {}

    def fake_run_editor_generation(**kwargs):
        captured.update(kwargs)
        return "scheduled"

    monkeypatch.setattr(editor_chat, "_run_editor_generation", fake_run_editor_generation)
    monkeypatch.setattr(editor_chat.asyncio, "create_task", lambda task: task)

    result = await editor_chat.resume_workflow_review(
        project_id=project_id,
        execution_id=str(execution_id),
        data=editor_chat.ResumeWorkflowReviewRequest(
            action="retry",
            review_version=5,
        ),
        db=db,
    )

    assert result["status"] == "running"
    assert captured["skip_editor_planning"] is True
    assert captured["skip_reviewed_scene_validation"] is True
    assert captured["reviewed_scene_overrides"] == {
        0: "s1-a\n\ns1-b",
        1: "s2-a",
    }
    assert captured["reviewed_scene_repair_indexes"] == {0, 1}
    execution = await db.get(WorkflowExecution, execution_id)
    assert execution.result_context["review_retry"] == {
        "mode": "in_place_repair",
        "scene_indexes": [0, 1],
        "source_text_hashes": {
            "0": editor_chat.hashlib.md5("s1-a\n\ns1-b".encode()).hexdigest()[:12],
            "1": editor_chat.hashlib.md5("s2-a".encode()).hexdigest()[:12],
        },
        "skip_editor_planning": True,
        "skip_chapter_writer": True,
    }


@pytest.mark.asyncio
async def test_retry_partial_scene_repairs_target_and_preserves_approved_scenes(db, monkeypatch):
    project_id, execution_id = await _make_waiting_partial_scene_review(db)
    captured = {}

    def fake_run_editor_generation(**kwargs):
        captured.update(kwargs)
        return "scheduled"

    monkeypatch.setattr(editor_chat, "_run_editor_generation", fake_run_editor_generation)
    monkeypatch.setattr(editor_chat.asyncio, "create_task", lambda task: task)

    result = await editor_chat.resume_workflow_review(
        project_id=project_id,
        execution_id=str(execution_id),
        data=editor_chat.ResumeWorkflowReviewRequest(action="retry", review_version=7),
        db=db,
    )

    assert result["status"] == "running"
    assert captured["skip_editor_planning"] is True
    assert captured["skip_reviewed_scene_validation"] is True
    assert captured["reviewed_scene_overrides"] == {
        0: "approved scene zero",
        1: "old target candidate",
    }
    assert captured["reviewed_scene_repair_indexes"] == {1}


@pytest.mark.asyncio
async def test_retry_validator_resumes_when_only_nonblocking_violations_remain(db, monkeypatch):
    project_id, execution_id = await _make_pending_validator_retry(db)
    captured = {}

    async def fake_evaluate(self, context, level="full"):
        return {
            "passed": False,
            "violations": [
                {
                    "type": "style_advisory",
                    "severity": "low",
                    "blocks_commit": False,
                    "detail": "Non-blocking advisory remains.",
                }
            ],
        }

    async def fake_get_state(self, project_id):
        return SimpleNamespace(model_dump=lambda: {})

    def fake_run_editor_generation(**kwargs):
        captured.update(kwargs)
        return "scheduled"

    monkeypatch.setattr("app.services.quality_gate.QualityGate.evaluate", fake_evaluate)
    monkeypatch.setattr("app.services.state_manager.StateManager.get_state", fake_get_state)
    monkeypatch.setattr(editor_chat, "_run_editor_generation", fake_run_editor_generation)
    monkeypatch.setattr(editor_chat.asyncio, "create_task", lambda task: task)

    result = await editor_chat.retry_validator(
        project_id=project_id,
        execution_id=str(execution_id),
        db=db,
    )

    assert result["status"] == "running"
    assert result["gate_passed"] is False
    assert captured["reviewed_scene_overrides"] == {0: "candidate text"}
    assert captured["skip_editor_planning"] is True
    assert captured["skip_reviewed_scene_validation"] is True
    execution = await db.get(WorkflowExecution, execution_id)
    assert execution.status == "running"
    assert execution.result_context["validator_retry_degraded_resume"] is True
    assert execution.result_context["last_retry_gate_result"]["violations"][0]["blocks_commit"] is False


@pytest.mark.asyncio
async def test_retry_validator_does_not_resume_when_validator_error_was_degraded(db, monkeypatch):
    project_id, execution_id = await _make_pending_validator_retry(db)
    scheduled = []

    async def fake_evaluate(self, context, level="full"):
        return {
            "passed": True,
            "violations": [{
                "type": "proposition_extractor_unavailable",
                "severity": "high",
                "blocks_commit": False,
                "detail": "Extractor retry still returned an incomplete parse.",
            }],
        }

    async def fake_get_state(self, project_id):
        return SimpleNamespace(model_dump=lambda: {})

    monkeypatch.setattr("app.services.quality_gate.QualityGate.evaluate", fake_evaluate)
    monkeypatch.setattr("app.services.state_manager.StateManager.get_state", fake_get_state)
    monkeypatch.setattr(editor_chat, "_spawn_workflow_task", lambda *args: scheduled.append(args))

    result = await editor_chat.retry_validator(
        project_id=project_id,
        execution_id=str(execution_id),
        db=db,
    )

    assert result["status"] == "pending_validator_retry"
    assert result["gate_passed"] is False
    assert scheduled == []
    execution = await db.get(WorkflowExecution, execution_id)
    assert execution.status == "pending_validator_retry"
    assert execution.result_context["validator_retry"]["validator_retry_delayed_attempts"] == 1


@pytest.mark.asyncio
async def test_retry_validator_recovers_parallel_system_review_without_content_bypass(db, monkeypatch):
    project_id, execution_id = await _make_waiting_parallel_validator_review(db)
    captured = {}

    async def fake_evaluate(self, context, level="full"):
        assert context["generated_text"] == "parallel candidate text"
        return {"passed": True, "violations": []}

    async def fake_targeted_evaluate(self, context, violation_types=None):
        assert violation_types == {"proposition_extractor_unavailable"}
        return await fake_evaluate(self, context)

    async def fake_get_state(self, project_id):
        return SimpleNamespace(model_dump=lambda: {})

    def fake_run_editor_generation(**kwargs):
        captured.update(kwargs)
        return "scheduled"

    monkeypatch.setattr("app.services.quality_gate.QualityGate.evaluate", fake_evaluate)
    monkeypatch.setattr(
        "app.services.quality_gate.QualityGate.evaluate_validator_retry",
        fake_targeted_evaluate,
    )
    monkeypatch.setattr("app.services.state_manager.StateManager.get_state", fake_get_state)
    monkeypatch.setattr(editor_chat, "_run_editor_generation", fake_run_editor_generation)
    monkeypatch.setattr(editor_chat.asyncio, "create_task", lambda task: task)

    result = await editor_chat.retry_validator(
        project_id=project_id,
        execution_id=str(execution_id),
        db=db,
    )

    assert result == {
        "execution_id": str(execution_id),
        "status": "running",
        "gate_passed": True,
    }
    assert captured["reviewed_scene_overrides"] == {
        0: "parallel candidate text",
        1: "approved scene one",
    }
    assert captured["skip_editor_planning"] is True
    assert captured["skip_reviewed_scene_validation"] is True
    execution = await db.get(WorkflowExecution, execution_id)
    assert execution.status == "running"
    assert execution.result_context["resume_state"]["candidate_text"] == "parallel candidate text"
    assert execution.result_context["validator_retry"]["candidate_text"] == "parallel candidate text"


@pytest.mark.asyncio
async def test_recovered_parallel_validator_failure_schedules_remaining_retries(db, monkeypatch):
    project_id, execution_id = await _make_waiting_parallel_validator_review(db)
    scheduled = {}

    async def fake_evaluate(self, context, level="full"):
        return {
            "passed": False,
            "violations": [{
                "type": "proposition_extractor_unavailable",
                "severity": "high",
                "blocks_commit": False,
                "issue_classification": "validator_system_error",
                "detail": "Extractor still unavailable.",
            }],
        }

    async def fake_targeted_evaluate(self, context, violation_types=None):
        assert violation_types == {"proposition_extractor_unavailable"}
        return await fake_evaluate(self, context)

    async def fake_get_state(self, project_id):
        return SimpleNamespace(model_dump=lambda: {})

    monkeypatch.setattr("app.services.quality_gate.QualityGate.evaluate", fake_evaluate)
    monkeypatch.setattr(
        "app.services.quality_gate.QualityGate.evaluate_validator_retry",
        fake_targeted_evaluate,
    )
    monkeypatch.setattr("app.services.state_manager.StateManager.get_state", fake_get_state)
    monkeypatch.setattr(
        editor_chat,
        "_schedule_delayed_validator_retries",
        lambda **kwargs: scheduled.update(kwargs),
    )

    result = await editor_chat.retry_validator(
        project_id=project_id,
        execution_id=str(execution_id),
        db=db,
    )

    assert result["status"] == "pending_validator_retry"
    assert scheduled == {
        "execution_id": str(execution_id),
        "project_id": str(project_id),
        "delays": [15, 45],
    }
    execution = await db.get(WorkflowExecution, execution_id)
    assert execution.status == "pending_validator_retry"
    assert execution.result_context["validator_retry"]["validator_retry_delayed_attempts"] == 1


def test_parallel_content_review_cannot_be_coerced_to_validator_retry():
    persisted = {
        "review": {
            "scene_index": 0,
            "candidate_text": "candidate",
            "violations": [{
                "type": "fact_conflict",
                "blocks_commit": True,
            }],
        },
        "resume_state": {
            "scene_index": 0,
            "candidate_text": "candidate",
            "parallel_review_breakpoint": True,
        },
    }

    assert editor_chat._validator_retry_from_parallel_review(persisted) is None


@pytest.mark.asyncio
async def test_style_polish_auto_repair_retries_reviewable_style_failure():
    llm = AsyncMock()
    llm.generate.return_value = "[[SCENE:1]]\nclean repaired prose without repeated phrases."

    result = await editor_chat._attempt_style_polish_auto_repair(
        text="仿佛旧文本仿佛旧文本仿佛旧文本",
        polish_result={
            "requires_human_review": True,
            "style_score": 40,
            "issues": [
                {
                    "type": "forbidden_word",
                    "word": "仿佛",
                    "suggestion": "avoid the repeated abstract comparison",
                }
            ],
            "summary": {"contract_style_conflict": False},
        },
        active_style={},
        llm=llm,
    )

    assert result["attempted"] is True
    assert result["accepted"] is True
    assert "[[SCENE:" not in result["text"]
    assert result["new_style_score"] >= 55
    assert result["dispatch"]["hooks"] == ["style_repair"]
    assert "final_chapter" in llm.generate.await_args.kwargs["system_prompt"]
