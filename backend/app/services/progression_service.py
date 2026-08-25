from __future__ import annotations

from app.repositories.progression_repository import ProgressionRepository
from app.services.mention_detector import MentionDetector


class ProgressionService:
    def __init__(self):
        self.detector = MentionDetector()
        self.repository = ProgressionRepository()

    async def detect_candidates(
        self,
        db,
        *,
        project,
        project_id: str,
        chapter_number: int,
        scene_index: int,
        text: str,
        write: bool = False,
        status: str = "candidate",
    ) -> dict:
        entities = self.detector.build_entity_index(project)
        mentions = self.detector.detect(text, entities)
        records = []
        if write:
            try:
                from app.db.db_models import async_session

                async with async_session() as progression_db:
                    for mention in mentions:
                        record = await self.repository.add_candidate(
                            progression_db,
                            project_id=project_id,
                            entity_type=mention["entity_type"],
                            entity_id=mention["entity_id"],
                            chapter_number=chapter_number,
                            scene_index=scene_index,
                            change_type="mention",
                            after_value={
                                "entity_name": mention["entity_name"],
                                "alias": mention["alias"],
                            },
                            evidence_text=mention["evidence_text"],
                            status=status,
                        )
                        if record:
                            records.append(record)
                    await progression_db.commit()
            except Exception:
                records = []
        return {
            "schema_version": 1,
            "status": "ok",
            "mention_count": len(mentions),
            "mentions": mentions,
            "records": records,
        }

    async def list_progressions(self, db, project_id: str, limit: int = 100) -> dict:
        items = await self.repository.list_for_project(db, project_id, limit=limit)
        total = await self.repository.count_for_project(db, project_id)
        return {"schema_version": 1, "items": items, "total": total}
