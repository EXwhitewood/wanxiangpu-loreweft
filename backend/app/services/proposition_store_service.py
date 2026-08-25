"""命题存储服务：管理叙事命题、事实合同和审计报告的持久化。"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update, and_, or_

from app.models.narrative_proposition import NarrativeProposition, FactContract, AuditReport

logger = logging.getLogger(__name__)


def _coerce_project_id(project_id):
    try:
        return uuid.UUID(str(project_id))
    except Exception:
        return project_id


class PropositionStoreService:
    """命题存储服务，负责叙事命题、事实合同和审计报告的 CRUD 操作。"""

    # ------------------------------------------------------------------
    # 命题操作
    # ------------------------------------------------------------------

    async def save_propositions(
        self,
        db,
        project_id,
        chapter_number: int,
        scene_index: int,
        generation_revision: int,
        propositions: list[NarrativeProposition],
    ) -> int:
        """保存命题列表到 narrative_propositions 表。"""
        from app.db.db_models import NarrativePropositionORM

        if not propositions:
            return 0
        pid = _coerce_project_id(project_id)

        count = 0
        for prop in propositions:
            from app.services.proposition_selector import infer_proposition_lifecycle

            prop_data = {
                "lifecycle_status": prop.lifecycle_status,
                "predicate_category": prop.predicate.category,
                "truth_layer": prop.truth_layer,
            }
            if prop.lifecycle_status == "current" and (
                prop.predicate.category in {"action", "event", "clue", "intention"}
                or prop.truth_layer in {"plan", "future_hint"}
            ):
                prop_data["lifecycle_status"] = ""
            lifecycle = infer_proposition_lifecycle(prop_data)
            supersedes_id = prop.supersedes_id
            if lifecycle == "current" and prop.predicate.category in {
                "state", "location", "ownership", "relationship", "knowledge",
            }:
                conditions = [
                    NarrativePropositionORM.project_id == pid,
                    NarrativePropositionORM.status == "active",
                    NarrativePropositionORM.lifecycle_status == "current",
                    NarrativePropositionORM.predicate_category == prop.predicate.category,
                ]
                if prop.subject.entity_id:
                    conditions.append(NarrativePropositionORM.subject_id == prop.subject.entity_id)
                else:
                    conditions.append(NarrativePropositionORM.subject_name == prop.subject.name)
                if prop.predicate.category in {"state", "relationship", "knowledge"}:
                    conditions.append(NarrativePropositionORM.predicate_name == prop.predicate.name)
                if prop.predicate.category == "relationship" and prop.object:
                    if prop.object.entity_id:
                        conditions.append(NarrativePropositionORM.object_id == prop.object.entity_id)
                    else:
                        conditions.append(NarrativePropositionORM.object_name == prop.object.name)
                previous_rows = (
                    await db.execute(
                        select(NarrativePropositionORM)
                        .where(and_(*conditions))
                        .order_by(
                            NarrativePropositionORM.chapter_number.desc(),
                            NarrativePropositionORM.scene_index.desc(),
                            NarrativePropositionORM.created_at.desc(),
                        )
                    )
                ).scalars().all()
                if previous_rows:
                    supersedes_id = previous_rows[0].id
                    for previous in previous_rows:
                        previous.lifecycle_status = "superseded"
                        previous.valid_to_chapter = max(
                            int(previous.valid_from_chapter or previous.chapter_number or 1),
                            chapter_number - 1,
                        )

            obj = NarrativePropositionORM(
                id=prop.proposition_id or str(uuid.uuid4()),
                project_id=pid,
                chapter_number=chapter_number,
                scene_index=scene_index,
                generation_revision=generation_revision,
                subject_name=prop.subject.name,
                subject_type=prop.subject.entity_type,
                subject_id=prop.subject.entity_id,
                predicate_name=prop.predicate.name,
                predicate_category=prop.predicate.category,
                object_name=prop.object.name if prop.object else None,
                object_type=prop.object.entity_type if prop.object else None,
                object_id=prop.object.entity_id if prop.object else None,
                truth_layer=prop.truth_layer,
                certainty=prop.certainty,
                polarity=prop.polarity,
                responsibility=prop.responsibility,
                time_scope=prop.time_scope,
                location_scope=prop.location_scope,
                source_text=prop.source_text,
                source_agent=prop.source_agent,
                confidence=prop.confidence,
                status="active",
                lifecycle_status=lifecycle,
                valid_from_chapter=prop.valid_from_chapter or chapter_number,
                valid_to_chapter=prop.valid_to_chapter,
                supersedes_id=supersedes_id,
                importance=prop.importance,
                last_confirmed_chapter=prop.last_confirmed_chapter or chapter_number,
                source_chunk_index=prop.source_chunk_index,
                created_at=datetime.now(timezone.utc),
            )
            db.add(obj)
            count += 1

        await db.flush()
        logger.info(
            "Saved %d propositions for project=%s chapter=%d scene=%d revision=%d",
            count, project_id, chapter_number, scene_index, generation_revision,
        )
        return count

    async def retract_propositions(
        self,
        db,
        project_id,
        chapter_number: int,
        generation_revision: int,
    ) -> int:
        """将旧修订的命题标记为 retracted，保留审计轨迹。"""
        from app.db.db_models import NarrativePropositionORM
        pid = _coerce_project_id(project_id)

        stmt = (
            update(NarrativePropositionORM)
            .where(
                and_(
                    NarrativePropositionORM.project_id == pid,
                    NarrativePropositionORM.chapter_number == chapter_number,
                    NarrativePropositionORM.generation_revision < generation_revision,
                    NarrativePropositionORM.status == "active",
                )
            )
            .values(
                status="retracted",
                lifecycle_status="retracted",
                retracted_at=datetime.now(timezone.utc),
            )
        )
        result = await db.execute(stmt)
        await db.flush()
        rowcount = getattr(result, "rowcount", None)
        retracted_count = rowcount if rowcount is not None else 0
        logger.info(
            "Retracted %d propositions for project=%s chapter=%d (before revision=%d)",
            retracted_count, project_id, chapter_number, generation_revision,
        )
        return retracted_count

    async def get_active_propositions(
        self,
        db,
        project_id,
        chapter_number: int | None = None,
        scene_index: int | None = None,
    ) -> list[dict]:
        """查询活跃命题，可选按章节/场景过滤。"""
        from app.db.db_models import NarrativePropositionORM
        pid = _coerce_project_id(project_id)

        conditions = [
            NarrativePropositionORM.project_id == pid,
            NarrativePropositionORM.status == "active",
            NarrativePropositionORM.lifecycle_status.notin_(["superseded", "retracted"]),
        ]
        if chapter_number is not None:
            conditions.append(NarrativePropositionORM.chapter_number == chapter_number)
        if scene_index is not None:
            conditions.append(NarrativePropositionORM.scene_index == scene_index)

        stmt = select(NarrativePropositionORM).where(and_(*conditions))
        result = await db.execute(stmt)
        rows = result.scalars().all()

        return [self._proposition_orm_to_dict(r) for r in rows]

    # ------------------------------------------------------------------
    # 事实合同操作
    # ------------------------------------------------------------------

    async def save_fact_contract(
        self,
        db,
        project_id,
        chapter_number: int,
        scene_index: int,
        generation_revision: int,
        fact_contract: FactContract,
        compiler_warnings: list[str],
    ) -> str:
        """保存事实合同到 scene_fact_contracts 表。"""
        from app.db.db_models import SceneFactContract
        pid = _coerce_project_id(project_id)

        contract_id = str(uuid.uuid4())
        obj = SceneFactContract(
            id=contract_id,
            project_id=pid,
            chapter_number=chapter_number,
            scene_index=scene_index,
            generation_revision=generation_revision,
            fact_contract=fact_contract.model_dump(),
            compiler_warnings=compiler_warnings,
            created_at=datetime.now(timezone.utc),
        )
        db.add(obj)
        await db.flush()
        logger.info(
            "Saved fact_contract %s for project=%s chapter=%d scene=%d",
            contract_id, project_id, chapter_number, scene_index,
        )
        return contract_id

    async def get_fact_contract(
        self,
        db,
        project_id,
        chapter_number: int,
        scene_index: int,
    ) -> dict | None:
        """获取场景最新的事实合同。"""
        from app.db.db_models import SceneFactContract
        pid = _coerce_project_id(project_id)

        stmt = (
            select(SceneFactContract)
            .where(
                and_(
                    SceneFactContract.project_id == pid,
                    SceneFactContract.chapter_number == chapter_number,
                    SceneFactContract.scene_index == scene_index,
                )
            )
            .order_by(SceneFactContract.generation_revision.desc())
            .limit(1)
        )
        result = await db.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            return None
        return {
            "id": row.id,
            "proposition_id": row.id,
            "project_id": str(row.project_id),
            "chapter_number": row.chapter_number,
            "scene_index": row.scene_index,
            "generation_revision": row.generation_revision,
            "scene_id": row.scene_id,
            "fact_contract": row.fact_contract,
            "compiler_warnings": row.compiler_warnings,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }

    # ------------------------------------------------------------------
    # 审计报告操作
    # ------------------------------------------------------------------

    async def save_audit_report(
        self,
        db,
        project_id,
        chapter_number: int,
        scene_index: int,
        generation_revision: int,
        audit_report: AuditReport,
    ) -> str:
        """保存审计报告到 proposition_audit_reports 表。"""
        from app.db.db_models import PropositionAuditReport
        pid = _coerce_project_id(project_id)

        report_id = str(uuid.uuid4())
        obj = PropositionAuditReport(
            id=report_id,
            project_id=pid,
            chapter_number=chapter_number,
            scene_index=scene_index,
            generation_revision=generation_revision,
            passed=audit_report.passed,
            commit_blocked=audit_report.commit_blocked,
            violations=[v.model_dump() for v in audit_report.violations],
            proposition_ids=audit_report.proposition_ids,
            created_at=datetime.now(timezone.utc),
        )
        db.add(obj)
        await db.flush()
        logger.info(
            "Saved audit_report %s for project=%s chapter=%d scene=%d passed=%s",
            report_id, project_id, chapter_number, scene_index, audit_report.passed,
        )
        return report_id

    # ------------------------------------------------------------------
    # 场景准备 / 世界观投影查询
    # ------------------------------------------------------------------

    async def get_propositions_for_scene_preparation(
        self,
        db,
        project_id,
        chapter_number: int,
        max_chapters_back: int | None = 3,
    ) -> list[dict]:
        """获取近几章的活跃命题，供场景准备使用。"""
        from app.db.db_models import NarrativePropositionORM
        pid = _coerce_project_id(project_id)

        start_chapter = max(1, chapter_number - max_chapters_back) if max_chapters_back is not None else 1
        recent_start = max(1, chapter_number - 6)
        stmt = (
            select(NarrativePropositionORM)
            .where(
                and_(
                    NarrativePropositionORM.project_id == pid,
                    NarrativePropositionORM.chapter_number >= start_chapter,
                    NarrativePropositionORM.chapter_number <= chapter_number,
                    NarrativePropositionORM.status == "active",
                    NarrativePropositionORM.lifecycle_status.notin_(["superseded", "retracted"]),
                    or_(
                        NarrativePropositionORM.lifecycle_status.in_(["current", "unresolved", "planned"]),
                        NarrativePropositionORM.chapter_number >= recent_start,
                    ),
                )
            )
            .order_by(
                NarrativePropositionORM.chapter_number,
                NarrativePropositionORM.scene_index,
            )
        )
        result = await db.execute(stmt)
        rows = result.scalars().all()

        return [self._proposition_orm_to_dict(r) for r in rows]

    async def get_proposition_models_for_scene_preparation(
        self,
        db,
        project_id,
        chapter_number: int,
        max_chapters_back: int = 3,
    ) -> list[NarrativeProposition]:
        """Return recent active propositions as NarrativeProposition models."""
        from app.db.db_models import NarrativePropositionORM
        pid = _coerce_project_id(project_id)

        start_chapter = max(1, chapter_number - max_chapters_back)
        stmt = (
            select(NarrativePropositionORM)
            .where(
                and_(
                    NarrativePropositionORM.project_id == pid,
                    NarrativePropositionORM.chapter_number >= start_chapter,
                    NarrativePropositionORM.chapter_number <= chapter_number,
                    NarrativePropositionORM.status == "active",
                    NarrativePropositionORM.lifecycle_status.notin_(["superseded", "retracted"]),
                )
            )
            .order_by(
                NarrativePropositionORM.chapter_number,
                NarrativePropositionORM.scene_index,
                NarrativePropositionORM.created_at,
            )
        )
        result = await db.execute(stmt)
        rows = result.scalars().all()
        return [self._proposition_orm_to_model(r) for r in rows]

    async def get_propositions_for_worldview_projection(
        self,
        db,
        project_id,
        chapter_number: int,
    ) -> list[dict]:
        """获取指定章节的活跃命题，供世界观投影使用。"""
        from app.db.db_models import NarrativePropositionORM
        pid = _coerce_project_id(project_id)

        stmt = (
            select(NarrativePropositionORM)
            .where(
                and_(
                    NarrativePropositionORM.project_id == pid,
                    NarrativePropositionORM.chapter_number <= chapter_number,
                    NarrativePropositionORM.status == "active",
                    NarrativePropositionORM.lifecycle_status.notin_(["superseded", "retracted"]),
                )
            )
            .order_by(
                NarrativePropositionORM.chapter_number,
                NarrativePropositionORM.scene_index,
            )
        )
        result = await db.execute(stmt)
        rows = result.scalars().all()

        return [self._proposition_orm_to_dict(r) for r in rows]

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    @staticmethod
    def _proposition_orm_to_dict(row) -> dict:
        """将 NarrativePropositionORM 转换为字典。"""
        return {
            "id": row.id,
            # Keep the canonical model-facing identifier alongside the ORM
            # compatibility key.  Older scene-preparation callers consume
            # ``proposition_id`` and must not depend on storage naming.
            "proposition_id": row.id,
            "project_id": str(row.project_id),
            "chapter_number": row.chapter_number,
            "scene_index": row.scene_index,
            "generation_revision": row.generation_revision,
            "subject_name": row.subject_name,
            "subject_type": row.subject_type,
            "subject_id": row.subject_id,
            "predicate_name": row.predicate_name,
            "predicate_category": row.predicate_category,
            "object_name": row.object_name,
            "object_type": row.object_type,
            "object_id": row.object_id,
            "truth_layer": row.truth_layer,
            "certainty": row.certainty,
            "polarity": row.polarity,
            "responsibility": row.responsibility,
            "time_scope": row.time_scope,
            "location_scope": row.location_scope,
            "source_text": row.source_text,
            "source_agent": row.source_agent,
            "confidence": row.confidence,
            "status": row.status,
            "lifecycle_status": row.lifecycle_status,
            "valid_from_chapter": row.valid_from_chapter,
            "valid_to_chapter": row.valid_to_chapter,
            "supersedes_id": row.supersedes_id,
            "importance": row.importance,
            "last_confirmed_chapter": row.last_confirmed_chapter,
            "source_chunk_index": row.source_chunk_index,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "retracted_at": row.retracted_at.isoformat() if row.retracted_at else None,
        }

    @staticmethod
    def _proposition_orm_to_model(row) -> NarrativeProposition:
        from app.models.narrative_proposition import NarrativeEntity, NarrativePredicate

        return NarrativeProposition(
            proposition_id=row.id,
            project_id=str(row.project_id),
            chapter_number=row.chapter_number,
            scene_index=row.scene_index,
            generation_revision=row.generation_revision,
            subject=NarrativeEntity(
                name=row.subject_name,
                entity_type=row.subject_type or "unknown",
                entity_id=row.subject_id,
            ),
            predicate=NarrativePredicate(
                name=row.predicate_name,
                category=row.predicate_category or "event",
            ),
            object=(
                NarrativeEntity(
                    name=row.object_name,
                    entity_type=row.object_type or "unknown",
                    entity_id=row.object_id,
                )
                if row.object_name
                else None
            ),
            truth_layer=row.truth_layer,
            certainty=row.certainty,
            polarity=row.polarity,
            responsibility=row.responsibility,
            time_scope=row.time_scope or "",
            location_scope=row.location_scope or "",
            source_text=row.source_text,
            source_agent=row.source_agent,
            confidence=row.confidence,
            lifecycle_status=row.lifecycle_status,
            valid_from_chapter=row.valid_from_chapter,
            valid_to_chapter=row.valid_to_chapter,
            supersedes_id=row.supersedes_id,
            importance=row.importance,
            last_confirmed_chapter=row.last_confirmed_chapter,
            source_chunk_index=row.source_chunk_index,
        )
