import uuid
import logging
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.db_models import Project
from app.services.story_plan_service import StoryPlanService

logger = logging.getLogger(__name__)


class ChapterSpineService:

    def __init__(self):
        self._plan_service = StoryPlanService()

    async def get_spine(
        self, project_id: uuid.UUID, db: AsyncSession
    ) -> list[dict]:
        plan = await self._plan_service.get_story_plan(project_id, db)
        return plan.get("chapter_spine", [])

    async def get_chapter(
        self, project_id: uuid.UUID, chapter_number: int, db: AsyncSession
    ) -> dict | None:
        spine = await self.get_spine(project_id, db)
        return self._plan_service.get_chapter_from_spine(spine, chapter_number)

    async def add_chapter(
        self, project_id: uuid.UUID, chapter_data: dict, db: AsyncSession
    ) -> dict:
        chapter_number = chapter_data.get("chapter_number", 0)
        if not chapter_number:
            return {"error": "chapter_number 必填"}

        existing = await self.get_chapter(project_id, chapter_number, db)
        if existing:
            return {"error": f"第 {chapter_number} 章已存在"}

        chapter_data.setdefault("chapter_id", f"ch_{chapter_number:03d}")
        chapter_data.setdefault("status", "draft")

        spine = await self.get_spine(project_id, db)
        spine.append(chapter_data)
        spine.sort(key=lambda x: x.get("chapter_number", 0))

        return await self._plan_service.update_layer(
            project_id, "chapter_spine", spine, db
        )

    async def update_chapter(
        self,
        project_id: uuid.UUID,
        chapter_number: int,
        updates: dict,
        db: AsyncSession,
    ) -> dict:
        return await self._plan_service.update_chapter_spine_item(
            project_id, chapter_number, updates, db
        )

    async def delete_chapter(
        self, project_id: uuid.UUID, chapter_number: int, db: AsyncSession
    ) -> dict:
        spine = await self.get_spine(project_id, db)
        new_spine = [ch for ch in spine if ch.get("chapter_number") != chapter_number]
        if len(new_spine) == len(spine):
            return {"error": f"第 {chapter_number} 章不存在"}

        return await self._plan_service.update_layer(
            project_id, "chapter_spine", new_spine, db
        )

    async def get_dependencies(
        self, project_id: uuid.UUID, chapter_number: int, db: AsyncSession
    ) -> dict:
        chapter = await self.get_chapter(project_id, chapter_number, db)
        if not chapter:
            return {"error": f"第 {chapter_number} 章不存在"}

        depends_on = chapter.get("depends_on", [])
        spine = await self.get_spine(project_id, db)

        depended_by = []
        for ch in spine:
            if chapter_number in [
                self._chapter_id_to_number(d, spine) for d in ch.get("depends_on", [])
            ]:
                depended_by.append({
                    "chapter_number": ch.get("chapter_number"),
                    "title": ch.get("title", ""),
                })

        return {
            "chapter_number": chapter_number,
            "depends_on": depends_on,
            "depended_by": depended_by,
        }

    async def get_chapters_by_node(
        self, project_id: uuid.UUID, node_id: str, db: AsyncSession
    ) -> list[dict]:
        spine = await self.get_spine(project_id, db)
        return [ch for ch in spine if ch.get("node_id") == node_id]

    async def batch_update(
        self,
        project_id: uuid.UUID,
        updates: list[dict],
        db: AsyncSession,
    ) -> dict:
        spine = await self.get_spine(project_id, db)
        spine_map = {ch.get("chapter_number"): ch for ch in spine}

        updated_count = 0
        for upd in updates:
            ch_num = upd.get("chapter_number", 0)
            if ch_num in spine_map:
                spine_map[ch_num].update(upd)
                updated_count += 1

        result = await self._plan_service.update_layer(
            project_id, "chapter_spine", list(spine_map.values()), db
        )
        result["updated_count"] = updated_count
        return result

    def _chapter_id_to_number(self, chapter_id: str, spine: list[dict]) -> int:
        for ch in spine:
            if ch.get("chapter_id") == chapter_id:
                return ch.get("chapter_number", 0)
        try:
            return int(chapter_id)
        except (ValueError, TypeError):
            return 0
