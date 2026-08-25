from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.db_models import (
    Chapter,
    ChapterBaseline,
    ChapterEffectOutbox,
    ChapterSnapshot,
    Project,
    WorkflowExecution,
    WorkflowStep,
)
from app.services.state_manager import StateManager
from app.services.project_chapter_aggregate_service import refresh_project_chapter_aggregates
from app.services.text_coercion import ensure_complete_sentence_ending
from app.utils.word_count import count_words


def _stable_key(
    project_id: str,
    chapter_number: int,
    scene_index: int,
    effect_type: str,
    payload: dict,
) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"{project_id}:{chapter_number}:{scene_index}:{effect_type}:{digest}"


async def discard_chapter_outbox(
    db: AsyncSession,
    project_id: str | uuid.UUID,
    chapter_number: int,
    *,
    from_chapter: bool = False,
) -> None:
    chapter_filter = (
        ChapterEffectOutbox.chapter_number >= chapter_number
        if from_chapter
        else ChapterEffectOutbox.chapter_number == chapter_number
    )
    await db.execute(
        delete(ChapterEffectOutbox).where(
            ChapterEffectOutbox.project_id == project_id,
            chapter_filter,
        )
    )


async def discard_worldview_projection_outbox(
    db: AsyncSession,
    project_id: str | uuid.UUID,
    chapter_number: int,
) -> None:
    await db.execute(
        delete(ChapterEffectOutbox).where(
            ChapterEffectOutbox.project_id == project_id,
            ChapterEffectOutbox.chapter_number == chapter_number,
            ChapterEffectOutbox.effect_type == "worldview_projection",
            ChapterEffectOutbox.applied == False,
        )
    )


async def _next_effect_version(
    db: AsyncSession,
    project_id: str | uuid.UUID,
    chapter_number: int,
    scene_index: int,
    effect_type: str,
) -> int:
    result = await db.execute(
        select(func.max(ChapterEffectOutbox.effect_version)).where(
            ChapterEffectOutbox.project_id == project_id,
            ChapterEffectOutbox.chapter_number == chapter_number,
            ChapterEffectOutbox.scene_index == scene_index,
            ChapterEffectOutbox.effect_type == effect_type,
        )
    )
    return int(result.scalar_one_or_none() or 0) + 1


async def _next_generation_revision(
    db: AsyncSession,
    project_id: str | uuid.UUID,
    chapter_number: int,
) -> int:
    from app.db.db_models import WorldviewObservation
    result = await db.execute(
        select(func.max(WorldviewObservation.generation_revision)).where(
            WorldviewObservation.project_id == project_id,
            WorldviewObservation.chapter_number == chapter_number,
        )
    )
    return int(result.scalar_one_or_none() or 0) + 1


def _deserialize_proposition_payload(raw_propositions: list) -> list:
    from app.models.narrative_proposition import NarrativeProposition

    proposition_models = []
    errors = []
    for idx, raw in enumerate(raw_propositions or []):
        try:
            proposition_models.append(NarrativeProposition(**raw))
        except Exception as exc:
            errors.append(f"{idx}: {exc}")
    if errors:
        raise ValueError(f"Invalid proposition payload: {'; '.join(errors[:3])}")
    return proposition_models


async def _apply_proposition_persistence_item(
    db: AsyncSession,
    *,
    project_id: str | uuid.UUID,
    chapter_number: int,
    payload: dict,
) -> None:
    from app.models.narrative_proposition import AuditReport, FactContract
    from app.services.proposition_store_service import PropositionStoreService

    pss = PropositionStoreService()
    pp_chapter = payload.get("chapter_number", chapter_number)
    pp_scene = payload.get("scene_index", 0)
    pp_revision = payload.get("generation_revision", 1)
    raw_propositions = payload.get("propositions", [])
    proposition_models = _deserialize_proposition_payload(raw_propositions)

    fact_contract_data = payload.get("fact_contract") or {}
    audit_report_data = payload.get("proposition_audit_report") or {}

    fact_contract = FactContract(**fact_contract_data) if fact_contract_data else None
    audit_report = AuditReport(**audit_report_data) if audit_report_data else None

    if proposition_models:
        await pss.save_propositions(
            db,
            project_id=project_id,
            chapter_number=pp_chapter,
            scene_index=pp_scene,
            generation_revision=pp_revision,
            propositions=proposition_models,
        )
    if fact_contract is not None:
        await pss.save_fact_contract(
            db,
            project_id=project_id,
            chapter_number=pp_chapter,
            scene_index=pp_scene,
            generation_revision=pp_revision,
            fact_contract=fact_contract,
            compiler_warnings=payload.get("compiler_warnings", []),
        )
    if audit_report is not None:
        await pss.save_audit_report(
            db,
            project_id=project_id,
            chapter_number=pp_chapter,
            scene_index=pp_scene,
            generation_revision=pp_revision,
            audit_report=audit_report,
        )
    if proposition_models:
        await pss.retract_propositions(
            db,
            project_id=project_id,
            chapter_number=pp_chapter,
            generation_revision=pp_revision,
        )


async def _apply_experience_quality_persistence_item(
    db: AsyncSession,
    *,
    project_id: str | uuid.UUID,
    chapter_number: int,
    payload: dict,
) -> None:
    from app.services.quality_memory_service import QualityMemoryService

    await QualityMemoryService().persist_scene_quality_payload(
        db,
        project_id=project_id,
        chapter_number=chapter_number,
        payload=payload or {},
    )


async def enqueue_scene_effect(
    db: AsyncSession,
    *,
    project_id: str | uuid.UUID,
    chapter_number: int,
    pending_scene_effect: dict,
) -> ChapterEffectOutbox | None:
    """Persist one idempotent scene effect without enqueueing chapter-wide work."""
    project_key = str(project_id)
    scene_index = int(pending_scene_effect.get("scene_index", 0))
    payload = {
        "generated_text": pending_scene_effect.get("generated_text", ""),
        "effects": pending_scene_effect.get("effects", {}),
    }
    effect_type = "scene_effects"
    key = _stable_key(project_key, chapter_number, scene_index, effect_type, payload)
    existing = await db.execute(
        select(ChapterEffectOutbox.id).where(
            ChapterEffectOutbox.idempotency_key == key
        )
    )
    if existing.scalar_one_or_none():
        return None
    item = ChapterEffectOutbox(
        project_id=project_id,
        chapter_number=chapter_number,
        scene_index=scene_index,
        effect_type=effect_type,
        effect_version=await _next_effect_version(
            db,
            project_id,
            chapter_number,
            scene_index,
            effect_type,
        ),
        payload=payload,
        idempotency_key=key,
    )
    db.add(item)
    return item


async def purge_chapter_artifacts(
    db: AsyncSession,
    *,
    project_id: str | uuid.UUID,
    chapter_number: int,
    chapter_id: str | uuid.UUID | None = None,
    execution_id: str = "",
    created_at_from: datetime | None = None,
    full_chapter: bool = False,
) -> dict:
    """Remove workflow/chapter-derived data without deleting the chapter row.

    A cancelled draft uses ``full_chapter`` because none of its derived data is
    canonical.  Cancelling a rewrite of an already committed chapter removes
    only records created by that execution window, preserving the committed
    chapter's earlier facts.  The chapter DELETE endpoint calls this before it
    removes the latest chapter itself.
    """
    from sqlalchemy import or_

    from app.db.db_models import (
        DetailSeed,
        EntityProgression,
        ExperienceQualityReport,
        FBIAutoRepairRunORM,
        FBIPatchConflictORM,
        FBIProtectedSpanORM,
        FBIRecheckReportORM,
        FBIRepairAttemptORM,
        FBIRepairAuditORM,
        FBIRepairCaseORM,
        FBIRepairIssueORM,
        FBIRepairPatchORM,
        FBIResolvedIssueORM,
        GenerationTrace,
        ChatSession,
        ChapterDiagnosticRecord,
        NarrativePropositionORM,
        OutlineChunk,
        PropositionAuditReport,
        PropositionLink,
        QualityRevisionAudit,
        SceneExperienceContract,
        SceneFactContract,
        SceneLiteraryQualityContract,
        WorldviewObservation,
    )

    pid = project_id
    chapter_number = int(chapter_number)
    cleanup_counts: dict[str, int] = {}

    def _time_scoped(model, conditions: list) -> list:
        scoped = list(conditions)
        if not full_chapter and created_at_from is not None and hasattr(model, "created_at"):
            scoped.append(model.created_at >= created_at_from)
        return scoped

    async def _delete_model(model, conditions: list, label: str) -> None:
        result = await db.execute(delete(model).where(*_time_scoped(model, conditions)))
        cleanup_counts[label] = max(int(result.rowcount or 0), 0)

    if full_chapter:
        # Reconcile promoted state cards first, then physically remove the
        # chapter observations and foreshadowing lines that have no other
        # chapter evidence.
        from app.services.foreshadowing_upsert_service import ForeshadowingUpsertService
        from app.services.worldview_projection_service import WorldviewProjectionService

        await WorldviewProjectionService().retract_chapter_sources(
            db,
            pid,
            chapter_number,
            hard=True,
        )
        await ForeshadowingUpsertService().retract_chapter_evidence(
            db,
            pid,
            chapter_number,
            source="chapter_artifact_purge",
            hard=True,
        )

    proposition_conditions = [
        NarrativePropositionORM.project_id == pid,
        NarrativePropositionORM.chapter_number == chapter_number,
    ]
    if not full_chapter and created_at_from is not None:
        proposition_conditions.append(NarrativePropositionORM.created_at >= created_at_from)
    proposition_ids = list(
        (
            await db.execute(
                select(NarrativePropositionORM.id).where(*proposition_conditions)
            )
        ).scalars().all()
    )
    if proposition_ids:
        link_result = await db.execute(
            delete(PropositionLink).where(
                PropositionLink.project_id == pid,
                or_(
                    PropositionLink.source_proposition_id.in_(proposition_ids),
                    PropositionLink.target_proposition_id.in_(proposition_ids),
                ),
            )
        )
        cleanup_counts["proposition_links"] = max(int(link_result.rowcount or 0), 0)

    chapter_models = (
        (NarrativePropositionORM, "narrative_propositions"),
        (SceneFactContract, "scene_fact_contracts"),
        (PropositionAuditReport, "proposition_audits"),
        (SceneExperienceContract, "scene_experience_contracts"),
        (SceneLiteraryQualityContract, "scene_literary_contracts"),
        (ExperienceQualityReport, "experience_quality_reports"),
        (QualityRevisionAudit, "quality_revision_audits"),
        (EntityProgression, "entity_progressions"),
        (GenerationTrace, "generation_traces"),
        (OutlineChunk, "outline_chunks"),
        (WorldviewObservation, "worldview_observations"),
        (ChapterDiagnosticRecord, "chapter_diagnostic_records"),
    )
    chapter_columns = {
        NarrativePropositionORM: NarrativePropositionORM.chapter_number,
        SceneFactContract: SceneFactContract.chapter_number,
        PropositionAuditReport: PropositionAuditReport.chapter_number,
        SceneExperienceContract: SceneExperienceContract.chapter_number,
        SceneLiteraryQualityContract: SceneLiteraryQualityContract.chapter_number,
        ExperienceQualityReport: ExperienceQualityReport.chapter_number,
        QualityRevisionAudit: QualityRevisionAudit.chapter_number,
        EntityProgression: EntityProgression.effective_chapter,
        GenerationTrace: GenerationTrace.chapter_number,
        OutlineChunk: OutlineChunk.chapter_number,
        WorldviewObservation: WorldviewObservation.chapter_number,
        ChapterDiagnosticRecord: ChapterDiagnosticRecord.chapter_number,
    }
    for model, label in chapter_models:
        await _delete_model(
            model,
            [model.project_id == pid, chapter_columns[model] == chapter_number],
            label,
        )

    if full_chapter:
        for model, label in (
            (ChapterEffectOutbox, "chapter_outbox"),
            (ChapterBaseline, "chapter_baselines"),
            (ChapterSnapshot, "chapter_snapshots"),
        ):
            await _delete_model(
                model,
                [model.project_id == pid, model.chapter_number == chapter_number],
                label,
            )

        if not execution_id:
            chat_result = await db.execute(
                delete(ChatSession).where(
                    ChatSession.project_id == pid,
                    ChatSession.chapter_number == chapter_number,
                )
            )
            cleanup_counts["chat_sessions"] = max(int(chat_result.rowcount or 0), 0)

            workflow_rows = list(
                (
                    await db.execute(
                        select(WorkflowExecution).where(WorkflowExecution.project_id == pid)
                    )
                ).scalars().all()
            )
            workflow_ids = []
            for workflow in workflow_rows:
                context = workflow.input_context or {}
                value = context.get("chapter_number") if isinstance(context, dict) else None
                if value is None:
                    match = re.search(r"(?:^|_)ch(?:apter)?(\d+)(?:_|$)", workflow.trigger_type or "")
                    value = match.group(1) if match else None
                try:
                    matches_chapter = int(value) == chapter_number
                except (TypeError, ValueError):
                    matches_chapter = False
                if matches_chapter:
                    workflow_ids.append(workflow.id)
            if workflow_ids:
                workflow_result = await db.execute(
                    delete(WorkflowExecution).where(WorkflowExecution.id.in_(workflow_ids))
                )
                cleanup_counts["workflow_executions"] = max(
                    int(workflow_result.rowcount or 0), 0
                )
    else:
        await _delete_model(
            ChapterEffectOutbox,
            [
                ChapterEffectOutbox.project_id == pid,
                ChapterEffectOutbox.chapter_number == chapter_number,
                ChapterEffectOutbox.applied == False,
            ],
            "chapter_outbox",
        )
        if execution_id:
            snapshot_result = await db.execute(
                delete(ChapterSnapshot).where(
                    ChapterSnapshot.project_id == pid,
                    ChapterSnapshot.chapter_number == chapter_number,
                    ChapterSnapshot.execution_id == execution_id,
                )
            )
            cleanup_counts["chapter_snapshots"] = max(int(snapshot_result.rowcount or 0), 0)

    chapter_uuid = str(chapter_id or "")
    if chapter_uuid:
        detail_conditions = [
            DetailSeed.project_id == pid,
            DetailSeed.chapter_id == chapter_id,
        ]
        await _delete_model(DetailSeed, detail_conditions, "detail_seeds")

    case_filters = [FBIRepairCaseORM.project_id == pid]
    case_scope = []
    if execution_id:
        case_scope.append(FBIRepairCaseORM.workflow_execution_id == execution_id)
    if full_chapter:
        # Historical window-repair writers used either the chapter row UUID,
        # the plain chapter number, or ``ch_###``.  Cover all three so deleting
        # the latest chapter cannot leave an orphaned legacy FBI audit trail.
        legacy_chapter_ids = {str(chapter_number), f"ch_{chapter_number:03d}"}
        if chapter_uuid:
            legacy_chapter_ids.add(chapter_uuid)
        case_scope.append(FBIRepairCaseORM.chapter_id.in_(legacy_chapter_ids))
    case_ids: list[str] = []
    if case_scope:
        case_ids = list(
            (
                await db.execute(
                    select(FBIRepairCaseORM.id).where(
                        *case_filters,
                        or_(*case_scope),
                    )
                )
            ).scalars().all()
        )
    if case_ids:
        for model, label in (
            (FBIRecheckReportORM, "fbi_recheck_reports"),
            (FBIPatchConflictORM, "fbi_patch_conflicts"),
            (FBIProtectedSpanORM, "fbi_protected_spans"),
            (FBIResolvedIssueORM, "fbi_resolved_issues"),
            (FBIAutoRepairRunORM, "fbi_auto_repair_runs"),
            (FBIRepairAuditORM, "fbi_repair_audits"),
            (FBIRepairPatchORM, "fbi_repair_patches"),
            (FBIRepairAttemptORM, "fbi_repair_attempts"),
            (FBIRepairIssueORM, "fbi_repair_issues"),
        ):
            result = await db.execute(delete(model).where(model.case_id.in_(case_ids)))
            cleanup_counts[label] = max(int(result.rowcount or 0), 0)
        result = await db.execute(delete(FBIRepairCaseORM).where(FBIRepairCaseORM.id.in_(case_ids)))
        cleanup_counts["fbi_repair_cases"] = max(int(result.rowcount or 0), 0)

    project = await db.get(Project, pid)
    if project is not None:
        core = dict(project.core_data or {})
        history = core.get("generation_history", [])
        if isinstance(history, list):
            core["generation_history"] = [
                item
                for item in history
                if not (
                    isinstance(item, dict)
                    and (
                        (full_chapter and int(item.get("chapter", 0) or 0) == chapter_number)
                        or (execution_id and str(item.get("execution_id") or "") == execution_id)
                    )
                )
            ]
        project.core_data = core

    from app.services.quality_memory_service import QualityMemoryService

    await QualityMemoryService().rebuild_project_quality_memory(
        db,
        pid,
        excluded_chapter_numbers={chapter_number} if full_chapter else set(),
    )
    await db.flush()

    if full_chapter:
        from app.services.context_ledger_service import get_context_ledger_service
        from app.services.editor_feedback_service import purge_editor_feedback
        from app.services.editor_planning_compiler import get_editor_planning_compiler
        from app.services.editor_trace_collector import get_editor_trace_collector

        project_key = str(pid)
        cleanup_counts["context_ledger_files"] = get_context_ledger_service().purge(
            project_key, chapter_number
        )
        cleanup_counts["editor_trace_files"] = get_editor_trace_collector().purge(
            project_key, chapter_number
        )
        cleanup_counts["chapter_plan_files"] = get_editor_planning_compiler().purge(
            project_key, chapter_number
        )
        cleanup_counts["editor_feedback_buckets"] = purge_editor_feedback(
            project_key, chapter_number
        )

    if full_chapter and chapter_uuid:
        bind = db.get_bind()
        if bind is not None and bind.dialect.name == "sqlite":
            from sqlalchemy import text

            fts_table_exists = await db.scalar(
                text(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type = 'table' AND name = 'fts_detail_seeds' LIMIT 1"
                )
            )
            if fts_table_exists:
                fts_result = await db.execute(
                    text(
                        "DELETE FROM fts_detail_seeds "
                        "WHERE project_id = :project_id AND chapter_id = :chapter_id"
                    ),
                    {"project_id": str(pid), "chapter_id": chapter_uuid},
                )
                cleanup_counts["fts_detail_seeds"] = max(int(fts_result.rowcount or 0), 0)
    cleanup_counts["total"] = sum(cleanup_counts.values())
    return cleanup_counts


async def enqueue_scene_effect_bundle(
    db: AsyncSession,
    *,
    project_id: str | uuid.UUID,
    chapter_number: int,
    pending_scene_effect: dict,
    generation_revision: int,
) -> None:
    """Enqueue one scene and its scene-scoped durable memory effects.

    This is also the narrow recovery boundary for a scene whose DAG commit
    finished but whose in-memory pending-effect list was lost before the
    chapter-wide outbox was created.  Chapter-wide projection/finalization is
    deliberately excluded so recovery cannot enqueue duplicate chapter work.
    """
    await enqueue_scene_effect(
        db,
        project_id=project_id,
        chapter_number=chapter_number,
        pending_scene_effect=pending_scene_effect,
    )

    await enqueue_scene_memory_effects(
        db,
        project_id=project_id,
        chapter_number=chapter_number,
        pending_scene_effect=pending_scene_effect,
        generation_revision=generation_revision,
    )


async def enqueue_scene_memory_effects(
    db: AsyncSession,
    *,
    project_id: str | uuid.UUID,
    chapter_number: int,
    pending_scene_effect: dict,
    generation_revision: int,
) -> None:
    """Enqueue only proposition/quality memory for one accepted scene."""

    project_key = str(project_id)
    effects = pending_scene_effect.get("effects", {})
    scene_index = int(pending_scene_effect.get("scene_index", 0))

    propositions_data = effects.get("propositions")
    fact_contract_data = effects.get("fact_contract")
    audit_report_data = effects.get("proposition_audit_report")
    if propositions_data or fact_contract_data or audit_report_data:
        prop_index = 999_997
        prop_type = "proposition_persistence"
        prop_payload = {
            "chapter_number": chapter_number,
            "scene_index": scene_index,
            "generation_revision": generation_revision,
            "propositions": propositions_data or [],
            "fact_contract": fact_contract_data or {},
            "compiler_warnings": effects.get("compiler_warnings", []),
            "proposition_audit_report": audit_report_data or {},
        }
        prop_key = _stable_key(
            project_key,
            chapter_number,
            prop_index,
            prop_type,
            prop_payload,
        )
        existing = await db.execute(
            select(ChapterEffectOutbox.id).where(
                ChapterEffectOutbox.idempotency_key == prop_key
            )
        )
        if not existing.scalar_one_or_none():
            db.add(ChapterEffectOutbox(
                project_id=project_id,
                chapter_number=chapter_number,
                scene_index=prop_index,
                effect_type=prop_type,
                effect_version=await _next_effect_version(
                    db, project_id, chapter_number, prop_index, prop_type
                ),
                payload=prop_payload,
                idempotency_key=prop_key,
            ))

    if (
        effects.get("experience_contract")
        or effects.get("literary_quality_contract")
        or effects.get("experience_quality_reports")
        or effects.get("mode_fit")
        or effects.get("style_conflict_report")
    ):
        exp_index = 999_998
        exp_type = "experience_quality_persistence"
        exp_payload = {
            "chapter_number": chapter_number,
            "scene_index": scene_index,
            "generation_revision": generation_revision,
            "writing_mode_id": effects.get("writing_mode_id", "general"),
            "experience_contract": effects.get("experience_contract") or {},
            "literary_quality_contract": effects.get("literary_quality_contract") or {},
            "quality_reports": effects.get("experience_quality_reports") or {},
            "mode_fit": effects.get("mode_fit") or {},
            "style_conflict_report": effects.get("style_conflict_report") or {},
            "compiler_warnings": effects.get("experience_compiler_warnings", []),
        }
        exp_key = _stable_key(
            project_key,
            chapter_number,
            exp_index,
            exp_type,
            exp_payload,
        )
        existing = await db.execute(
            select(ChapterEffectOutbox.id).where(
                ChapterEffectOutbox.idempotency_key == exp_key
            )
        )
        if not existing.scalar_one_or_none():
            db.add(ChapterEffectOutbox(
                project_id=project_id,
                chapter_number=chapter_number,
                scene_index=exp_index,
                effect_type=exp_type,
                effect_version=await _next_effect_version(
                    db, project_id, chapter_number, exp_index, exp_type
                ),
                payload=exp_payload,
                idempotency_key=exp_key,
            ))


async def enqueue_chapter_effects(
    db: AsyncSession,
    *,
    project_id: str | uuid.UUID,
    chapter_number: int,
    pending_scene_effects: list[dict],
    chapter_state: dict,
    execution_id: str = "",
) -> None:
    generation_revision = await _next_generation_revision(db, project_id, chapter_number)
    for pending in pending_scene_effects:
        await enqueue_scene_effect_bundle(
            db,
            project_id=project_id,
            chapter_number=chapter_number,
            pending_scene_effect=pending,
            generation_revision=generation_revision,
        )

    project_key = str(project_id)

    # Determine whether any pending scene effects contain proposition data.
    has_propositions = any(
        bool(
            pending.get("effects", {}).get("propositions")
            or pending.get("effects", {}).get("fact_contract")
            or pending.get("effects", {}).get("proposition_audit_report")
        )
        for pending in pending_scene_effects
    )

    has_experience_quality = any(
        bool(
            pending.get("effects", {}).get("experience_contract")
            or pending.get("effects", {}).get("literary_quality_contract")
            or pending.get("effects", {}).get("experience_quality_reports")
            or pending.get("effects", {}).get("mode_fit")
            or pending.get("effects", {}).get("style_conflict_report")
        )
        for pending in pending_scene_effects
    )

    finalize_payload = {
        "chapter_state": chapter_state,
        "execution_id": execution_id,
        "has_propositions": has_propositions,
        "has_experience_quality": has_experience_quality,
    }
    finalize_type = "chapter_finalize"
    finalize_index = 1_000_000
    finalize_key = _stable_key(
        project_key, chapter_number, finalize_index, finalize_type, finalize_payload
    )
    existing = await db.execute(
        select(ChapterEffectOutbox.id).where(
            ChapterEffectOutbox.idempotency_key == finalize_key
        )
    )
    if not existing.scalar_one_or_none():
        db.add(ChapterEffectOutbox(
            project_id=project_id,
            chapter_number=chapter_number,
            scene_index=finalize_index,
            effect_type=finalize_type,
            effect_version=await _next_effect_version(
                db, project_id, chapter_number, finalize_index, finalize_type
            ),
            payload=finalize_payload,
            idempotency_key=finalize_key,
        ))

    await enqueue_worldview_projection(
        db,
        project_id=project_id,
        chapter_number=chapter_number,
        generation_revision=generation_revision,
    )


async def enqueue_worldview_projection(
    db: AsyncSession,
    *,
    project_id: str | uuid.UUID,
    chapter_number: int,
    generation_revision: int | None = None,
    reuse_existing_content: bool = False,
) -> None:
    project_key = str(project_id)
    wv_index = 999_999
    wv_type = "worldview_projection"
    chapter_result = await db.execute(
        select(Chapter).where(
            Chapter.project_id == project_id,
            Chapter.chapter_number == chapter_number,
        )
    )
    chapter_obj = chapter_result.scalar_one_or_none()
    chapter_id_str = str(chapter_obj.id) if chapter_obj else ""
    import hashlib
    content_hash = ""
    if chapter_obj and chapter_obj.content:
        content_hash = hashlib.sha256(chapter_obj.content.encode("utf-8")).hexdigest()[:16]
    if reuse_existing_content:
        existing_rows = (
            await db.execute(
                select(ChapterEffectOutbox).where(
                    ChapterEffectOutbox.project_id == project_id,
                    ChapterEffectOutbox.chapter_number == chapter_number,
                    ChapterEffectOutbox.effect_type == wv_type,
                )
            )
        ).scalars().all()
        if any(
            isinstance(item.payload, dict)
            and item.payload.get("content_hash") == content_hash
            for item in existing_rows
        ):
            # An explicit save of an unchanged user revision may retry a
            # pending/retryable outbox item, but it must not create a new
            # generation revision and replace valid worldview cards with a
            # second non-deterministic extraction of the same prose.
            return

    wv_payload = {
        "chapter_number": chapter_number,
        "chapter_id": chapter_id_str,
        "content_hash": content_hash,
        "generation_revision": generation_revision or await _next_generation_revision(db, project_id, chapter_number),
    }
    wv_key = _stable_key(project_key, chapter_number, wv_index, wv_type, wv_payload)
    existing = await db.execute(
        select(ChapterEffectOutbox.id).where(
            ChapterEffectOutbox.idempotency_key == wv_key
        )
    )
    if not existing.scalar_one_or_none():
        db.add(ChapterEffectOutbox(
            project_id=project_id,
            chapter_number=chapter_number,
            scene_index=wv_index,
            effect_type=wv_type,
            effect_version=await _next_effect_version(
                db, project_id, chapter_number, wv_index, wv_type
            ),
            payload=wv_payload,
            idempotency_key=wv_key,
        ))


async def _finalize_chapter(
    db: AsyncSession,
    *,
    chapter: Chapter,
    chapter_state: dict,
    execution_id: str,
) -> None:
    state = await StateManager().get_state(str(chapter.project_id))
    snap_result = await db.execute(
        select(ChapterSnapshot).where(
            ChapterSnapshot.project_id == chapter.project_id,
            ChapterSnapshot.chapter_number == chapter.chapter_number,
        )
    )
    snapshot = snap_result.scalar_one_or_none()
    prev_result = await db.execute(
        select(ChapterSnapshot).where(
            ChapterSnapshot.project_id == chapter.project_id,
            ChapterSnapshot.chapter_number == chapter.chapter_number - 1,
            ChapterSnapshot.stale == False,
        )
    )
    previous = prev_result.scalar_one_or_none()
    lineage_version = int(getattr(previous, "lineage_version", 0) or 0) + 1
    if snapshot:
        snapshot.story_state = state.model_dump()
        snapshot.chapter_state = chapter_state
        snapshot.execution_id = execution_id
        snapshot.lineage_version = lineage_version
        snapshot.stale = False
        snapshot.committed_at = datetime.now(timezone.utc)
    else:
        db.add(ChapterSnapshot(
            project_id=chapter.project_id,
            chapter_number=chapter.chapter_number,
            story_state=state.model_dump(),
            chapter_state=chapter_state,
            execution_id=execution_id,
            lineage_version=lineage_version,
            stale=False,
        ))

    baseline_result = await db.execute(
        select(ChapterBaseline).where(
            ChapterBaseline.project_id == chapter.project_id,
            ChapterBaseline.chapter_number == chapter.chapter_number,
        )
    )
    baseline = baseline_result.scalar_one_or_none()
    if baseline:
        await db.delete(baseline)
    chapter.content = ensure_complete_sentence_ending(chapter.content or "")
    chapter.status = "committed"
    await refresh_project_chapter_aggregates(
        db,
        project_id=chapter.project_id,
    )
    # Canonical post-commit extraction is queued as worldview_projection below.
    # It writes observations, state cards and foreshadowing evidence into the
    # same SQLite transaction; no filesystem ledger is written here.

MAX_WORLDVIEW_RETRY_ATTEMPTS = 3


async def apply_pending_chapter_effects(
    db: AsyncSession,
    *,
    project_id: str | uuid.UUID,
    chapter_number: int,
    project: Project | None = None,
) -> None:
    chapter_result = await db.execute(
        select(Chapter).where(
            Chapter.project_id == project_id,
            Chapter.chapter_number == chapter_number,
        )
    )
    chapter = chapter_result.scalar_one_or_none()
    if not chapter:
        return

    if project is None:
        project = await db.get(Project, project_id)

    result = await db.execute(
        select(ChapterEffectOutbox)
        .where(
            ChapterEffectOutbox.project_id == project_id,
            ChapterEffectOutbox.chapter_number == chapter_number,
            ChapterEffectOutbox.applied == False,
        )
        .order_by(ChapterEffectOutbox.scene_index, ChapterEffectOutbox.effect_version)
        )
    pending = result.scalars().all()
    for item in pending:
        if item.effect_type == "scene_effects":
            from app.services.scene_generation_pipeline import persist_scene_effects

            payload = item.payload or {}
            await persist_scene_effects(
                project_id=str(project_id),
                chapter_number=chapter_number,
                scene_index=item.scene_index,
                generated_text=payload.get("generated_text", ""),
                effects=payload.get("effects", {}),
                db=db,
                project=project,
            )
        elif item.effect_type == "chapter_finalize":
            payload = item.payload or {}
            await _finalize_chapter(
                db,
                chapter=chapter,
                chapter_state=payload.get("chapter_state", {}),
                execution_id=payload.get("execution_id", ""),
            )
        elif item.effect_type == "proposition_persistence":
            try:
                await _apply_proposition_persistence_item(
                    db,
                    project_id=project_id,
                    chapter_number=chapter_number,
                    payload=item.payload or {},
                )
            except Exception as e:
                # Proposition persistence failure is not critical — mark as
                # retryable_failed so the auto-retry cycle can pick it up.
                item.status = "retryable_failed"
                item.attempts = (item.attempts or 0) + 1
                item.error_message = str(e)[:500]
                await db.commit()
                continue
        elif item.effect_type == "experience_quality_persistence":
            try:
                await _apply_experience_quality_persistence_item(
                    db,
                    project_id=project_id,
                    chapter_number=chapter_number,
                    payload=item.payload or {},
                )
            except Exception as e:
                item.status = "retryable_failed"
                item.attempts = (item.attempts or 0) + 1
                item.error_message = str(e)[:500]
                await db.commit()
                continue
        elif item.effect_type == "worldview_projection":
            from app.services.worldview_projection_service import WorldviewProjectionService
            wps = WorldviewProjectionService()
            try:
                wv_payload = item.payload or {}
                projection_result = await wps.project_committed_chapter_atomically(
                    db,
                    project_id=project_id,
                    chapter_number=chapter_number,
                    project=project,
                    generation_revision=wv_payload.get("generation_revision"),
                )
            except Exception as e:
                item.status = "retryable_failed"
                item.attempts = (item.attempts or 0) + 1
                item.error_message = str(e)[:500]
                await db.commit()
                continue

            # Degraded extraction: LLM parse failed, no observations created.
            # Mark as retryable_failed so auto-retry can pick it up later.
            if projection_result.get("degraded"):
                item.status = "retryable_failed"
                item.attempts = (item.attempts or 0) + 1
                item.error_message = f"degraded extraction: {projection_result.get('degraded_reason', 'unknown')}"[:500]
                await db.commit()
                continue
        item.applied = True
        item.status = "applied"
        item.error_message = ""
        item.next_retry_at = None
        item.applied_at = datetime.now(timezone.utc)
        await db.commit()


async def recover_committing_chapters(db: AsyncSession) -> int:
    result = await db.execute(select(Chapter).where(Chapter.status == "committing"))
    chapters = result.scalars().all()
    recovered = 0
    for chapter in chapters:
        pending = await db.execute(
            select(ChapterEffectOutbox.id).where(
                ChapterEffectOutbox.project_id == chapter.project_id,
                ChapterEffectOutbox.chapter_number == chapter.chapter_number,
                ChapterEffectOutbox.applied == False,
            )
        )
        if not pending.scalars().all():
            chapter.status = "draft"
            await db.commit()
            continue
        await apply_pending_chapter_effects(
            db,
            project_id=chapter.project_id,
            chapter_number=chapter.chapter_number,
        )
        if chapter.status == "committed":
            snapshot_result = await db.execute(
                select(ChapterSnapshot).where(
                    ChapterSnapshot.project_id == chapter.project_id,
                    ChapterSnapshot.chapter_number == chapter.chapter_number,
                )
            )
            snapshot = snapshot_result.scalar_one_or_none()
            execution_id = snapshot.execution_id if snapshot else ""
            if execution_id:
                try:
                    workflow_execution_id = uuid.UUID(str(execution_id))
                except (TypeError, ValueError):
                    workflow_execution_id = None
                if not workflow_execution_id:
                    recovered += 1
                    continue
                execution_result = await db.execute(
                    select(WorkflowExecution).where(
                        WorkflowExecution.id == workflow_execution_id,
                    )
                )
                execution = execution_result.scalar_one_or_none()
                if execution and execution.status == "failed":
                    execution.status = "completed"
                    execution.error_message = ""
                    execution.result_context = {
                        "chapter_number": chapter.chapter_number,
                        "word_count": count_words(chapter.content or ""),
                        "recovered_from_outbox": True,
                    }
                    execution.updated_at = datetime.now(timezone.utc)
                    step_result = await db.execute(
                        select(WorkflowStep).where(
                            WorkflowStep.execution_id == execution.id,
                            WorkflowStep.agent_name == "write_chapter",
                        )
                    )
                    write_step = step_result.scalar_one_or_none()
                    if write_step and write_step.status != "completed":
                        write_step.status = "completed"
                        write_step.error_message = ""
                        write_step.completed_at = datetime.now(timezone.utc)
                        if write_step.started_at:
                            # SQLite 读出的 started_at 是 naive，统一为 aware (UTC) 再与 completed_at 比较
                            started = write_step.started_at
                            if started.tzinfo is None:
                                started = started.replace(tzinfo=timezone.utc)
                            write_step.duration_ms = int(
                                (write_step.completed_at - started).total_seconds() * 1000
                            )
                    await db.commit()
        recovered += 1

    return recovered


async def recover_pending_chapter_effects(db: AsyncSession) -> dict[str, int]:
    """Replay every durable, unapplied outbox group, regardless of chapter status.

    User-authored chapters intentionally remain ``draft``.  Tying recovery to
    ``Chapter.status == 'committing'`` therefore stranded their worldview
    projection after a process exit.  The outbox itself is the recovery source
    of truth; chapter status only controls the AI generation commit lifecycle.
    """
    groups = (
        await db.execute(
            select(
                ChapterEffectOutbox.project_id,
                ChapterEffectOutbox.chapter_number,
            )
            .where(
                ChapterEffectOutbox.applied == False,
                ChapterEffectOutbox.status.in_(("pending", "applying")),
            )
            .distinct()
        )
    ).all()

    replayed = 0
    for project_id, chapter_number in groups:
        await apply_pending_chapter_effects(
            db,
            project_id=project_id,
            chapter_number=int(chapter_number),
        )
        replayed += 1

    worldview_retried = await _retry_all_failed_worldview_projections(db)
    proposition_retried = await _retry_all_failed_proposition_persistence(db)
    experience_retried = await _retry_all_failed_experience_quality_persistence(db)
    remaining = (
        await db.execute(
            select(ChapterEffectOutbox.id).where(
                ChapterEffectOutbox.applied == False,
                ChapterEffectOutbox.status.in_(("pending", "applying", "retryable_failed")),
            )
        )
    ).scalars().all()
    return {
        "replayed_groups": replayed,
        "worldview_retried": worldview_retried,
        "proposition_retried": proposition_retried,
        "experience_retried": experience_retried,
        "remaining": len(remaining),
    }


async def _retry_all_failed_worldview_projections(db: AsyncSession) -> int:
    """Retry all retryable_failed worldview_projection outbox items across all projects."""
    import logging
    _logger = logging.getLogger(__name__)

    stmt = select(ChapterEffectOutbox).where(
        ChapterEffectOutbox.effect_type == "worldview_projection",
        ChapterEffectOutbox.status == "retryable_failed",
        ChapterEffectOutbox.attempts < MAX_WORLDVIEW_RETRY_ATTEMPTS,
    )
    result = await db.execute(stmt)
    items = result.scalars().all()

    if not items:
        return 0

    from app.services.worldview_projection_service import WorldviewProjectionService
    wps = WorldviewProjectionService()
    retried = 0

    for item in items:
        project = await db.get(Project, item.project_id)
        item.attempts = (item.attempts or 0) + 1
        item.status = "applying"
        await db.flush()

        try:
            wv_payload = item.payload or {}
            projection_result = await wps.project_committed_chapter_atomically(
                db,
                project_id=item.project_id,
                chapter_number=item.chapter_number,
                project=project,
                generation_revision=wv_payload.get("generation_revision"),
            )
            if projection_result.get("degraded"):
                raise RuntimeError(
                    "degraded extraction: "
                    + str(projection_result.get("degraded_reason") or "unknown")
                )
            item.status = "applied"
            item.applied = True
            item.applied_at = datetime.now(timezone.utc)
            item.error_message = ""
            retried += 1
            _logger.info(
                "[chapter_commit] worldview_projection retry succeeded for chapter %s (attempt %d)",
                item.chapter_number, item.attempts,
            )
        except Exception as e:
            item.status = "retryable_failed"
            item.error_message = str(e)[:500]
            _logger.warning(
                "[chapter_commit] worldview_projection retry failed for chapter %s (attempt %d): %s",
                item.chapter_number, item.attempts, e,
            )

        await db.flush()

    await db.commit()
    return retried


async def _retry_all_failed_proposition_persistence(db: AsyncSession) -> int:
    """Retry all retryable_failed proposition_persistence outbox items across all projects."""
    import logging
    _logger = logging.getLogger(__name__)

    MAX_PROPOSITION_RETRY_ATTEMPTS = 3

    stmt = select(ChapterEffectOutbox).where(
        ChapterEffectOutbox.effect_type == "proposition_persistence",
        ChapterEffectOutbox.status == "retryable_failed",
        ChapterEffectOutbox.attempts < MAX_PROPOSITION_RETRY_ATTEMPTS,
    )
    result = await db.execute(stmt)
    items = result.scalars().all()

    if not items:
        return 0

    retried = 0

    for item in items:
        item.attempts = (item.attempts or 0) + 1
        item.status = "applying"
        await db.flush()

        try:
            await _apply_proposition_persistence_item(
                db,
                project_id=item.project_id,
                chapter_number=item.chapter_number,
                payload=item.payload or {},
            )
            item.status = "applied"
            item.applied = True
            item.applied_at = datetime.now(timezone.utc)
            item.error_message = ""
            retried += 1
            _logger.info(
                "[chapter_commit] proposition_persistence retry succeeded for chapter %s (attempt %d)",
                item.chapter_number, item.attempts,
            )
        except Exception as e:
            item.status = "retryable_failed"
            item.error_message = str(e)[:500]
            _logger.warning(
                "[chapter_commit] proposition_persistence retry failed for chapter %s (attempt %d): %s",
                item.chapter_number, item.attempts, e,
            )

        await db.flush()

    await db.commit()
    return retried


async def _retry_all_failed_experience_quality_persistence(db: AsyncSession) -> int:
    """Retry all retryable_failed experience_quality_persistence outbox items."""
    import logging
    _logger = logging.getLogger(__name__)

    MAX_EXPERIENCE_RETRY_ATTEMPTS = 3

    stmt = select(ChapterEffectOutbox).where(
        ChapterEffectOutbox.effect_type == "experience_quality_persistence",
        ChapterEffectOutbox.status == "retryable_failed",
        ChapterEffectOutbox.attempts < MAX_EXPERIENCE_RETRY_ATTEMPTS,
    )
    result = await db.execute(stmt)
    items = result.scalars().all()

    if not items:
        return 0

    retried = 0
    for item in items:
        item.attempts = (item.attempts or 0) + 1
        item.status = "applying"
        await db.flush()

        try:
            await _apply_experience_quality_persistence_item(
                db,
                project_id=item.project_id,
                chapter_number=item.chapter_number,
                payload=item.payload or {},
            )
            item.status = "applied"
            item.applied = True
            item.applied_at = datetime.now(timezone.utc)
            item.error_message = ""
            retried += 1
            _logger.info(
                "[chapter_commit] experience_quality_persistence retry succeeded for chapter %s (attempt %d)",
                item.chapter_number, item.attempts,
            )
        except Exception as e:
            item.status = "retryable_failed"
            item.error_message = str(e)[:500]
            _logger.warning(
                "[chapter_commit] experience_quality_persistence retry failed for chapter %s (attempt %d): %s",
                item.chapter_number, item.attempts, e,
            )

        await db.flush()

    await db.commit()
    return retried
