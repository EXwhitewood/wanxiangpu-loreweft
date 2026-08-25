import uuid
import logging
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.story_plan_service import StoryPlanService

logger = logging.getLogger(__name__)


class ThreadPlanService:

    def __init__(self):
        self._plan_service = StoryPlanService()

    async def get_thread_plan(
        self, project_id: uuid.UUID, db: AsyncSession
    ) -> dict:
        plan = await self._plan_service.get_story_plan(project_id, db)
        if "error" in plan:
            return plan
        return self._normalize_thread_plan(plan.get("thread_plan"))

    def _normalize_thread_plan(self, thread_plan: dict | None) -> dict:
        if not isinstance(thread_plan, dict):
            return {"threads": []}

        normalized_threads = []
        raw_threads = thread_plan.get("threads", [])
        if not isinstance(raw_threads, list):
            raw_threads = []

        for index, raw_thread in enumerate(raw_threads, start=1):
            if not isinstance(raw_thread, dict):
                continue
            thread = dict(raw_thread)
            thread_id = str(thread.get("thread_id") or thread.get("name") or f"thread_{index:03d}")
            thread_type = thread.get("thread_type") or thread.get("type") or "subplot"
            if thread_type not in ("main", "subplot", "hidden", "foreshadowing", "suspense"):
                thread_type = "subplot"
            status = thread.get("status") or "planned"
            if status not in ("planned", "active", "resolved", "abandoned"):
                status = "planned"

            thread.update({
                "thread_id": thread_id,
                "name": str(thread.get("name") or thread_id),
                "thread_type": thread_type,
                "status": status,
                "description": str(thread.get("description") or ""),
                "plant_chapters": self._chapter_numbers(thread.get("plant_chapters")),
                "escalation_chapters": self._chapter_numbers(
                    thread.get("escalation_chapters") or thread.get("develop_chapters")
                ),
                "reveal_chapters": self._chapter_numbers(
                    thread.get("reveal_chapters") or thread.get("payoff_chapters")
                ),
                "depends_on_threads": self._string_list(thread.get("depends_on_threads")),
            })
            normalized_threads.append(thread)

        return {**thread_plan, "threads": normalized_threads}

    def _chapter_numbers(self, value) -> list[int]:
        if not isinstance(value, list):
            return []
        result = []
        for item in value:
            try:
                chapter_number = int(item)
            except (TypeError, ValueError):
                continue
            if chapter_number > 0 and chapter_number not in result:
                result.append(chapter_number)
        return result

    def _string_list(self, value) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item) for item in value if item is not None and str(item)]

    async def get_thread(
        self, project_id: uuid.UUID, thread_id: str, db: AsyncSession
    ) -> dict | None:
        tp = await self.get_thread_plan(project_id, db)
        if "error" in tp:
            return tp
        for t in tp.get("threads", []):
            if t.get("thread_id") == thread_id or t.get("name") == thread_id:
                return t
        return None

    async def add_thread(
        self, project_id: uuid.UUID, thread_data: dict, db: AsyncSession
    ) -> dict:
        tp = await self.get_thread_plan(project_id, db)
        if "error" in tp:
            return tp
        threads = tp.get("threads", [])

        thread_id = thread_data.get("thread_id", "")
        if not thread_id:
            thread_id = f"thread_{len(threads) + 1:03d}"
            thread_data["thread_id"] = thread_id

        for t in threads:
            if t.get("thread_id") == thread_id or t.get("name") == thread_data.get("name"):
                return {"error": f"线索 {thread_id} 已存在"}

        threads.append(thread_data)
        tp["threads"] = threads
        return await self._plan_service.update_layer(
            project_id, "thread_plan", tp, db
        )

    async def update_thread(
        self,
        project_id: uuid.UUID,
        thread_id: str,
        updates: dict,
        db: AsyncSession,
    ) -> dict:
        tp = await self.get_thread_plan(project_id, db)
        if "error" in tp:
            return tp
        threads = tp.get("threads", [])

        found = False
        for i, t in enumerate(threads):
            if t.get("thread_id") == thread_id or t.get("name") == thread_id:
                threads[i].update(updates)
                found = True
                break

        if not found:
            return {"error": f"线索 {thread_id} 不存在"}

        tp["threads"] = threads
        return await self._plan_service.update_layer(
            project_id, "thread_plan", tp, db
        )

    async def delete_thread(
        self, project_id: uuid.UUID, thread_id: str, db: AsyncSession
    ) -> dict:
        tp = await self.get_thread_plan(project_id, db)
        if "error" in tp:
            return tp
        threads = tp.get("threads", [])
        new_threads = [
            t for t in threads
            if t.get("thread_id") != thread_id and t.get("name") != thread_id
        ]
        if len(new_threads) == len(threads):
            return {"error": f"线索 {thread_id} 不存在"}

        tp["threads"] = new_threads
        return await self._plan_service.update_layer(
            project_id, "thread_plan", tp, db
        )

    async def get_thread_chapter_map(
        self, project_id: uuid.UUID, db: AsyncSession
    ) -> dict:
        tp = await self.get_thread_plan(project_id, db)
        if "error" in tp:
            return tp
        result = {}
        for t in tp.get("threads", []):
            tid = t.get("thread_id", "")
            all_chapters = (
                t.get("plant_chapters", [])
                + t.get("escalation_chapters", [])
                + t.get("reveal_chapters", [])
            )
            result[tid] = {
                "name": t.get("name", ""),
                "type": t.get("thread_type", ""),
                "status": t.get("status", ""),
                "all_chapters": sorted(set(all_chapters)),
            }
        return result

    async def get_threads_for_chapter(
        self, project_id: uuid.UUID, chapter_number: int, db: AsyncSession
    ) -> list[dict]:
        tp = await self.get_thread_plan(project_id, db)
        if "error" in tp:
            return tp
        result = []
        for t in tp.get("threads", []):
            all_ch = (
                t.get("plant_chapters", [])
                + t.get("escalation_chapters", [])
                + t.get("reveal_chapters", [])
            )
            if chapter_number in all_ch:
                ops = []
                if chapter_number in t.get("plant_chapters", []):
                    ops.append("plant")
                if chapter_number in t.get("escalation_chapters", []):
                    ops.append("escalate")
                if chapter_number in t.get("reveal_chapters", []):
                    ops.append("reveal")
                result.append({
                    "thread_id": t.get("thread_id", ""),
                    "name": t.get("name", ""),
                    "type": t.get("thread_type", ""),
                    "status": t.get("status", ""),
                    "operations": ops,
                })
        return result

    async def check_orphan_threads(
        self, project_id: uuid.UUID, db: AsyncSession
    ) -> list[dict]:
        tp = await self.get_thread_plan(project_id, db)
        if "error" in tp:
            return tp
        orphans = []
        for t in tp.get("threads", []):
            status = t.get("status", "")
            if status in ("active", "planned"):
                plant = t.get("plant_chapters", [])
                reveal = t.get("reveal_chapters", [])
                if plant and not reveal:
                    orphans.append({
                        "thread_id": t.get("thread_id", ""),
                        "name": t.get("name", ""),
                        "issue": "已埋设但无回收计划",
                        "plant_chapters": plant,
                    })
                elif not plant and not reveal:
                    orphans.append({
                        "thread_id": t.get("thread_id", ""),
                        "name": t.get("name", ""),
                        "issue": "无任何章节操作",
                    })
        return orphans
