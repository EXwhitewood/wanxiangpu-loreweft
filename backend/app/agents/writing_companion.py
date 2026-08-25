from __future__ import annotations

import json
from collections.abc import AsyncGenerator

from sqlalchemy import select

from app.agents.base import BaseAgent
from app.db.db_models import Chapter, Project
from app.services.story_plan_service import StoryPlanService


class WritingCompanionAgent(BaseAgent):
    """A conversational writing partner, deliberately separate from the editor DAG."""

    name = "writing_companion"

    @staticmethod
    def _system_prompt() -> str:
        return (
            "你是「万象谱·墨伴」，作者身边的创作伙伴。你的职责是讨论灵感、分析当前章节、"
            "提出正文或本章蓝图的修改方案，并在作者明确要求时给出可应用的候选文本。\n\n"
            "边界：\n"
            "1. 你不是主编，不启动整章生成工作流；即使用户说‘生成本章’，也只讨论或提供建议。\n"
            "2. 你不能声称已经修改正文、大纲、世界观或伏笔；当前接口只提供建议。\n"
            "3. 修改建议必须说明作用范围，尽量给出原意、修改理由和候选文本。\n"
            "4. 本章蓝图是当前章节的权威计划；不得擅自改变其中的硬事实。若建议调整，明确指出需要"
            "作者确认修改蓝图。\n"
            "5. 事实不足时承认不确定，不补造人物状态、世界规则或前情。\n"
            "6. 回复应像可靠的共同作者，具体、克制、可执行，不使用主编审批口吻。"
        )

    async def _messages_with_context(self, context: dict) -> list[dict]:
        project_id = context.get("project_id")
        chapter_number = int(context.get("chapter_number") or 1)
        db = context.get("db")
        project = await db.get(Project, project_id) if db is not None else None

        chapter_content = ""
        if db is not None and project is not None:
            result = await db.execute(
                select(Chapter).where(
                    Chapter.project_id == project.id,
                    Chapter.chapter_number == chapter_number,
                )
            )
            chapter = result.scalar_one_or_none()
            chapter_content = chapter.content if chapter else ""

        blueprint = {}
        if db is not None and project is not None:
            loaded = await StoryPlanService().get_chapter_blueprint(
                project.id, chapter_number, db,
            )
            if "error" not in loaded:
                blueprint = {
                    "chapter": loaded.get("chapter", {}),
                    "scenes": loaded.get("scenes", []),
                    "revision": loaded.get("revision", 0),
                }

        project_context = {
            "project": {
                "name": getattr(project, "name", ""),
                "genre": getattr(project, "genre", ""),
                "description": getattr(project, "description", ""),
            },
            "chapter_number": chapter_number,
            "blueprint": blueprint,
            "current_text": chapter_content[:12000],
        }
        messages = [
            {"role": "system", "content": self._system_prompt()},
            {
                "role": "system",
                "content": (
                    "以下是只读的当前工作台上下文。不要把它当作用户消息，也不要声称已写回。\n"
                    + json.dumps(project_context, ensure_ascii=False, default=str)
                ),
            },
        ]
        for message in context.get("messages", []) or []:
            role = str(message.get("role") or "user")
            content = str(message.get("content") or "")
            if role in {"user", "assistant"} and content:
                messages.append({"role": role, "content": content})
        return messages

    async def execute(self, context: dict) -> dict:
        messages = await self._messages_with_context(context)
        llm = await self.get_llm_client()
        parts: list[str] = []
        async for chunk in llm.generate_stream_with_messages(
            messages=messages,
            temperature=0.75,
            max_tokens=8192,
            timeout=180,
        ):
            if isinstance(chunk, str):
                parts.append(chunk)
        return {"response": "".join(parts)}

    async def execute_stream(self, context: dict) -> AsyncGenerator[dict, None]:
        try:
            messages = await self._messages_with_context(context)
            llm = await self.get_llm_client()
            full_response = ""
            async for chunk in llm.generate_stream_with_messages(
                messages=messages,
                temperature=0.75,
                max_tokens=8192,
                timeout=180,
            ):
                if isinstance(chunk, dict):
                    continue
                full_response += chunk
                yield {"type": "text_delta", "data": {"content": chunk}}
            yield {"type": "done", "data": {"response": full_response}}
        except Exception as exc:
            yield {"type": "error", "data": {"message": str(exc)}}
            yield {"type": "done", "data": {}}
