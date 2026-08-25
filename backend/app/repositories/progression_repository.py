from __future__ import annotations

import uuid
import hashlib
from datetime import datetime

from sqlalchemy import func, select


class ProgressionRepository:
    async def list_for_project(self, db, project_id: str, limit: int = 100) -> list[dict]:
        from app.db.db_models import EntityProgression

        result = await db.execute(
            select(EntityProgression)
            .where(EntityProgression.project_id == self._coerce_uuid(project_id))
            .order_by(EntityProgression.created_at.desc())
            .limit(limit)
        )
        return [self._to_dict(item) for item in result.scalars().all()]

    async def count_for_project(self, db, project_id: str) -> int:
        from app.db.db_models import EntityProgression

        result = await db.execute(
            select(func.count()).select_from(EntityProgression).where(
                EntityProgression.project_id == self._coerce_uuid(project_id)
            )
        )
        return result.scalar() or 0

    async def add_candidate(
        self,
        db,
        *,
        project_id: str,
        entity_type: str,
        entity_id: str,
        chapter_number: int,
        scene_index: int = 0,
        change_type: str = "mention",
        after_value: dict | None = None,
        evidence_text: str = "",
        source: str = "mention_detector",
        status: str = "candidate",
    ) -> dict:
        from app.db.db_models import EntityProgression

        fingerprint = self._fingerprint(
            project_id, entity_type, entity_id, chapter_number, scene_index,
            change_type, evidence_text,
        )
        existing = await db.execute(
            select(EntityProgression).where(EntityProgression.fingerprint == fingerprint)
        )
        existing_obj = existing.scalars().first()
        if existing_obj:
            return self._to_dict(existing_obj)
        obj = EntityProgression(
            project_id=self._coerce_uuid(project_id),
            entity_type=entity_type,
            entity_id=entity_id,
            effective_chapter=chapter_number,
            effective_scene=scene_index,
            change_type=change_type,
            before_value={},
            after_value=after_value or {},
            evidence_text=evidence_text,
            source=source,
            status=status,
            schema_version=1,
            fingerprint=fingerprint,
        )
        db.add(obj)
        await db.flush()
        return self._to_dict(obj)

    @staticmethod
    def _fingerprint(
        project_id: str,
        entity_type: str,
        entity_id: str,
        chapter_number: int,
        scene_index: int,
        change_type: str,
        evidence_text: str,
    ) -> str:
        raw = "|".join(map(str, (
            project_id, entity_type, entity_id, chapter_number, scene_index,
            change_type, evidence_text.strip(),
        )))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _coerce_uuid(value: str):
        try:
            return uuid.UUID(str(value))
        except Exception:
            return value

    @staticmethod
    def _to_dict(obj) -> dict:
        return {
            "id": str(obj.id),
            "project_id": str(obj.project_id),
            "entity_type": obj.entity_type,
            "entity_id": obj.entity_id,
            "effective_chapter": obj.effective_chapter,
            "effective_scene": obj.effective_scene,
            "change_type": obj.change_type,
            "before_value": obj.before_value or {},
            "after_value": obj.after_value or {},
            "evidence_text": obj.evidence_text,
            "source": obj.source,
            "status": obj.status,
            "schema_version": obj.schema_version,
            "fingerprint": obj.fingerprint,
            "created_at": obj.created_at.isoformat() if isinstance(obj.created_at, datetime) else obj.created_at,
        }
