import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.db_models import Project

logger = logging.getLogger(__name__)

MAX_TOPICS = 5
MAX_UNFINISHED_TASKS = 5
MAX_KEY_DECISIONS = 10
MAX_NOTIFICATIONS = 20


class EditorMemoryService:

    async def record_session(
        self, project_id: str, session_data: dict, db: AsyncSession = None
    ) -> None:
        """记录会话信息，保留最近的话题、未完成任务、关键决策和用户偏好"""
        project = await db.get(Project, uuid.UUID(project_id))
        if not project:
            raise ValueError(f"项目 {project_id} 不存在")

        core_data = dict(project.core_data or {})
        memory = core_data.get("editor_memory", {})

        new_topics = session_data.get("topics", [])
        existing_topics = memory.get("last_topics", [])
        merged_topics = existing_topics + new_topics
        memory["last_topics"] = merged_topics[-MAX_TOPICS:]

        new_tasks = session_data.get("unfinished_tasks", [])
        existing_tasks = memory.get("unfinished_tasks", [])
        merged_tasks = existing_tasks + new_tasks
        memory["unfinished_tasks"] = merged_tasks[-MAX_UNFINISHED_TASKS:]

        new_decisions = session_data.get("key_decisions", [])
        existing_decisions = memory.get("key_decisions", [])
        merged_decisions = existing_decisions + new_decisions
        memory["key_decisions"] = merged_decisions[-MAX_KEY_DECISIONS:]

        user_prefs = session_data.get("user_preferences", {})
        if user_prefs:
            existing_prefs = memory.get("user_preferences", {})
            existing_prefs.update(user_prefs)
            memory["user_preferences"] = existing_prefs

        memory["last_active_at"] = datetime.now(timezone.utc).isoformat()

        core_data["editor_memory"] = memory
        project.core_data = core_data
        await db.commit()

    async def get_last_session(
        self, project_id: str, db: AsyncSession = None
    ) -> dict:
        """获取上次会话的上下文信息"""
        project = await db.get(Project, uuid.UUID(project_id))
        if not project or not project.core_data:
            return {}

        memory = project.core_data.get("editor_memory")
        if not memory:
            return {}

        return {
            "last_topics": memory.get("last_topics", []),
            "unfinished_tasks": memory.get("unfinished_tasks", []),
            "key_decisions": memory.get("key_decisions", []),
            "user_preferences": memory.get("user_preferences", {}),
            "last_active_at": memory.get("last_active_at"),
        }

    async def record_change_notification(
        self,
        project_id: str,
        source: str,
        message: str,
        db: AsyncSession = None,
    ) -> None:
        """记录来自大纲/世界观的变更通知"""
        project = await db.get(Project, uuid.UUID(project_id))
        if not project:
            raise ValueError(f"项目 {project_id} 不存在")

        core_data = dict(project.core_data or {})
        notifications = core_data.get("editor_notifications", [])

        notifications.append(
            {
                "source": source,
                "message": message,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

        core_data["editor_notifications"] = notifications[-MAX_NOTIFICATIONS:]
        project.core_data = core_data
        await db.commit()

    async def get_pending_notifications(
        self, project_id: str, db: AsyncSession = None
    ) -> list[dict]:
        """获取待处理的变更通知"""
        project = await db.get(Project, uuid.UUID(project_id))
        if not project or not project.core_data:
            return []

        return project.core_data.get("editor_notifications", [])

    async def clear_notifications(
        self, project_id: str, db: AsyncSession = None
    ) -> None:
        """清除所有变更通知"""
        project = await db.get(Project, uuid.UUID(project_id))
        if not project:
            raise ValueError(f"项目 {project_id} 不存在")

        core_data = dict(project.core_data or {})
        core_data["editor_notifications"] = []
        project.core_data = core_data
        await db.commit()
