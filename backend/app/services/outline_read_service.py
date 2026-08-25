import json
import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.db_models import Project
from app.services.text_coercion import to_search_text


class OutlineReadService:

    @staticmethod
    async def get_outline(project_id: uuid.UUID, db: AsyncSession) -> dict:
        project = await db.get(Project, project_id)
        if not project or not project.outline_data:
            return {}
        return project.outline_data

    @staticmethod
    async def get_chapter(project_id: uuid.UUID, chapter_number: int, db: AsyncSession) -> dict | None:
        project = await db.get(Project, project_id)
        if not project or not project.outline_data:
            return None
        chapters = project.outline_data.get("chapters", [])
        for chapter in chapters:
            if chapter.get("chapter_number") == chapter_number:
                return chapter
        return None

    @staticmethod
    async def search_outline(project_id: uuid.UUID, query: str, db: AsyncSession) -> list[dict]:
        project = await db.get(Project, project_id)
        if not project or not project.outline_data:
            return []
        chapters = project.outline_data.get("chapters", [])
        query_lower = to_search_text(query)
        results = []
        for chapter in chapters:
            if query_lower in json.dumps(chapter, ensure_ascii=False).lower():
                results.append(chapter)
        return results

    @staticmethod
    async def suggest_modification(project_id: uuid.UUID, suggestion: dict, db: AsyncSession) -> dict:
        project = await db.get(Project, project_id)
        if not project:
            raise ValueError(f"Project {project_id} not found")
        if not project.outline_data:
            project.outline_data = {}
        pending = project.outline_data.get("_pending_suggestions", [])
        record = {
            "id": str(uuid.uuid4()),
            "source_agent": suggestion["source_agent"],
            "suggestion": suggestion["suggestion"],
            "target_chapters": suggestion["target_chapters"],
            "reason": suggestion["reason"],
            "status": "pending",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        pending.append(record)
        project.outline_data["_pending_suggestions"] = pending
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(project, "outline_data")
        db.add(project)
        await db.commit()
        return record
