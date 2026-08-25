from app.services.memory_core import CoreMemoryService
from app.db.db_models import async_session, Project

from sqlalchemy import select
import uuid


class CoreQuerySkill:
    def __init__(self):
        self.core_service = CoreMemoryService()

    async def execute(
        self,
        project_id: str | None = None,
        entity_id: str | None = None,
        chapter_number: int | None = None,
        scene_number: int | None = None,
        **_: dict,
    ) -> dict:
        if not project_id:
            return {"status": "skipped", "reason": "missing_project_id"}
        if entity_id:
            return {"entity_card": await self.get_entity_card(project_id, entity_id)}
        if chapter_number is not None and scene_number is not None:
            return {
                "outline_beat": await self.get_outline_beat(
                    project_id,
                    int(chapter_number),
                    int(scene_number),
                )
            }
        if chapter_number is not None:
            return {"foreshadowing": await self.get_foreshadowing(project_id, int(chapter_number))}
        return {"status": "skipped", "reason": "missing_query_target"}

    async def get_entity_card(self, project_id: str, entity_id: str) -> dict | None:
        result = await self.resolve_entity_card(project_id, entity_id)
        return result.get("card") if result else None

    async def resolve_entity_card(
        self,
        project_id: str,
        entity_ref: str,
        *,
        expected_type: str | None = None,
    ) -> dict | None:
        """Resolve a Core entity and retain match provenance for diagnostics."""
        project = await self.core_service._get_project(project_id)
        if not project:
            return None
        from app.services.core_entity_resolver import CoreEntityResolver

        resolved = CoreEntityResolver.resolve_core_data(
            project.core_data or {},
            entity_ref,
            expected_type=expected_type,
        )
        if resolved is None:
            return None
        card = dict(resolved.card)
        if resolved.entity_type == "character":
            character = await self.core_service.get_character(project_id, str(card.get("id") or entity_ref))
            if character:
                card = character.model_dump(mode="json")
        return {
            "card": card,
            "entity_type": resolved.entity_type,
            "matched_by": resolved.matched_by,
            "query": resolved.query,
            "canonical_name": resolved.canonical_name,
        }

    async def get_foreshadowing(self, project_id: str, chapter_number: int) -> list[dict]:
        async with async_session() as session:
            result = await session.execute(
                select(Project).where(Project.id == uuid.UUID(project_id))
            )
            project = result.scalar_one_or_none()
            if not project:
                return []

            outline_data = project.outline_data or {}
            foreshadowing = outline_data.get("foreshadowing", [])
            return [fs for fs in foreshadowing if fs.get("target_chapter", 0) >= chapter_number]

    async def get_outline_beat(
        self, project_id: str, chapter_number: int, scene_number: int
    ) -> dict | None:
        async with async_session() as session:
            result = await session.execute(
                select(Project).where(Project.id == uuid.UUID(project_id))
            )
            project = result.scalar_one_or_none()
            if not project:
                return None

            outline_data = project.outline_data or {}
            chapters = outline_data.get("chapters", [])

            for ch in chapters:
                if ch.get("chapter_number") == chapter_number:
                    scenes = ch.get("scenes", [])
                    if 0 <= scene_number - 1 < len(scenes):
                        return scenes[scene_number - 1]
            return None
