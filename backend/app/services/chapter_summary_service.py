import json
import logging
import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.db_models import Chapter, Project
from app.utils.word_count import count_words
from app.services.llm_task_profiles import LLMTaskType

logger = logging.getLogger(__name__)


def _estimate_tokens(text: str) -> int:
    if not text:
        return 0
    chinese_chars = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    other_chars = len(text) - chinese_chars
    return int(chinese_chars * 1.5 + other_chars * 0.4)


SUMMARY_PROMPT = """请为以下小说章节生成结构化摘要。要求简洁精准，每个字段一句话。

章节标题：{title}
章节内容（节选）：
{content}

请输出以下格式的摘要：
- 核心事件：[一句话概括本章发生的最重要的事]
- 关键转折：[价值转变，如"从绝望到决心"]
- 伏笔操作：[本章埋设或回收了什么伏笔，如"埋设：XXX"或"回收：XXX"]
- 角色状态变化：[谁发生了什么变化，如"角色A：恐惧→决心"]
- 未解决悬念：[章末钩子]
- 摘要：[200字以内的章节概要]"""

COMPRESS_PROMPT = """请将以下多个章节的摘要压缩为一段远期摘要（300字以内），保留关键情节转折、伏笔操作和角色状态变化。

{summaries_text}

输出格式：一段连贯的叙事概要，包含关键转折点和伏笔线索。"""


class ChapterSummaryService:

    async def _get_llm_client(self):
        from app.services.agent_config import AgentConfigManager
        from app.services.llm_client import LLMClient

        manager = AgentConfigManager()
        config = await manager.get_agent_config("outline_architect")
        return LLMClient(
            api_format=config.api_format,
            api_key=config.api_key,
            base_url=config.base_url,
            model=config.model,
        )

    async def generate_summary(
        self,
        project_id: str,
        chapter_number: int,
        content: str,
        chapter_title: str = "",
        db: AsyncSession = None,
    ) -> dict:
        project = await db.get(Project, uuid.UUID(project_id))
        if not project:
            raise ValueError(f"项目不存在: {project_id}")

        content_excerpt = content[:6000] if len(content) > 6000 else content
        user_prompt = SUMMARY_PROMPT.format(
            title=chapter_title or f"第{chapter_number}章",
            content=content_excerpt,
        )

        try:
            llm = await self._get_llm_client()
            response = await llm.generate(
                system_prompt="你是小说章节摘要生成器，为章节生成结构化摘要。严格按指定格式输出。",
                user_prompt=user_prompt,
                temperature=0.3,
                task_type=LLMTaskType.CHAPTER_SUMMARY,
            )
            summary = self._parse_summary_response(response, chapter_number, content)
        except Exception as e:
            logger.error(f"[ChapterSummaryService] 生成摘要失败: {e}")
            summary = {
                "chapter_number": chapter_number,
                "core_event": "",
                "key_turning_point": "",
                "foreshadowing_updates": [],
                "character_changes": [],
                "unsolved_suspense": "",
                "summary_text": content[:200] if content else "",
                "word_count": count_words(content),
                "generated_at": datetime.now(timezone.utc).isoformat(),
            }

        core_data = project.core_data or {}
        chapter_summaries = core_data.get("chapter_summaries", {})
        chapter_summaries[str(chapter_number)] = summary
        core_data["chapter_summaries"] = chapter_summaries
        project.core_data = core_data
        await db.commit()

        if chapter_number % 10 == 0:
            try:
                start = chapter_number - 9
                await self.compress_summaries(
                    project_id, start, chapter_number, db=db
                )
            except Exception as e:
                logger.error(f"[ChapterSummaryService] 递归压缩失败: {e}")

        return summary

    async def get_summary(
        self,
        project_id: str,
        chapter_number: int,
        db: AsyncSession = None,
    ) -> dict | None:
        project = await db.get(Project, uuid.UUID(project_id))
        if not project:
            return None

        core_data = project.core_data or {}
        chapter_summaries = core_data.get("chapter_summaries", {})
        return chapter_summaries.get(str(chapter_number))

    async def get_recent_summaries(
        self,
        project_id: str,
        count: int,
        db: AsyncSession = None,
    ) -> list[dict]:
        project = await db.get(Project, uuid.UUID(project_id))
        if not project:
            return []

        core_data = project.core_data or {}
        chapter_summaries = core_data.get("chapter_summaries", {})

        all_numbers = []
        for key in chapter_summaries:
            try:
                all_numbers.append(int(key))
            except (ValueError, TypeError):
                continue

        all_numbers.sort(reverse=True)
        recent_numbers = all_numbers[:count]

        return [
            chapter_summaries[str(num)]
            for num in recent_numbers
            if str(num) in chapter_summaries
        ]

    async def compress_summaries(
        self,
        project_id: str,
        start_chapter: int,
        end_chapter: int,
        db: AsyncSession = None,
    ) -> str:
        project = await db.get(Project, uuid.UUID(project_id))
        if not project:
            raise ValueError(f"项目不存在: {project_id}")

        core_data = project.core_data or {}
        chapter_summaries = core_data.get("chapter_summaries", {})

        summaries_text_parts = []
        for ch_num in range(start_chapter, end_chapter + 1):
            ch_summary = chapter_summaries.get(str(ch_num))
            if ch_summary:
                summaries_text_parts.append(
                    f"第{ch_num}章：{ch_summary.get('summary_text', '')}"
                )

        if not summaries_text_parts:
            return ""

        summaries_text = "\n".join(summaries_text_parts)
        user_prompt = COMPRESS_PROMPT.format(summaries_text=summaries_text)

        try:
            llm = await self._get_llm_client()
            compressed = await llm.generate(
                system_prompt="你是小说摘要压缩器，将多章摘要压缩为远期概要，保留关键转折和伏笔线索。",
                user_prompt=user_prompt,
                temperature=0.3,
                task_type=LLMTaskType.CHAPTER_SUMMARY,
            )
        except Exception as e:
            logger.error(f"[ChapterSummaryService] 压缩摘要失败: {e}")
            compressed = summaries_text[:300]

        compressed_key = f"{start_chapter}-{end_chapter}"
        compressed_summaries = core_data.get("compressed_summaries", {})
        compressed_summaries[compressed_key] = {
            "chapter_range": [start_chapter, end_chapter],
            "summary_text": compressed.strip(),
            "compressed_at": datetime.now(timezone.utc).isoformat(),
        }
        core_data["compressed_summaries"] = compressed_summaries
        project.core_data = core_data
        await db.commit()

        return compressed.strip()

    async def get_context_window(
        self,
        project_id: str,
        current_chapter: int,
        db: AsyncSession = None,
    ) -> str:
        project = await db.get(Project, uuid.UUID(project_id))
        if not project:
            return ""

        core_data = project.core_data or {}
        chapter_summaries = core_data.get("chapter_summaries", {})
        compressed_summaries = core_data.get("compressed_summaries", {})

        context_parts = []

        prev_chapter = await self._get_chapter_content(
            project_id, current_chapter - 1, db
        )
        if prev_chapter:
            tail = prev_chapter[-1000:] if len(prev_chapter) > 1000 else prev_chapter
            context_parts.append(f"【前一章末尾】\n{tail}")

        recent_summaries = []
        for ch_num in range(max(1, current_chapter - 3), current_chapter):
            ch_summary = chapter_summaries.get(str(ch_num))
            if ch_summary:
                recent_summaries.append(
                    f"第{ch_num}章：{ch_summary.get('summary_text', '')}"
                )
        if recent_summaries:
            context_parts.append(
                f"【前2-3章摘要】\n" + "\n".join(recent_summaries)
            )

        mid_summaries = []
        mid_start = max(1, current_chapter - 10)
        mid_end = current_chapter - 4
        if mid_end >= mid_start:
            for group_start in range(mid_start, mid_end + 1, 3):
                group_end = min(group_start + 2, mid_end)
                group_key = f"{group_start}-{group_end}"
                compressed = compressed_summaries.get(group_key)
                if compressed:
                    mid_summaries.append(
                        f"第{group_start}-{group_end}章：{compressed.get('summary_text', '')}"
                    )
                else:
                    group_parts = []
                    for ch_num in range(group_start, group_end + 1):
                        ch_summary = chapter_summaries.get(str(ch_num))
                        if ch_summary:
                            group_parts.append(
                                f"第{ch_num}章：{ch_summary.get('summary_text', '')}"
                            )
                    if group_parts:
                        mid_summaries.append("\n".join(group_parts))
        if mid_summaries:
            context_parts.append(
                f"【前4-10章近期摘要】\n" + "\n".join(mid_summaries)
            )

        far_summaries = []
        far_end = current_chapter - 11
        if far_end >= 1:
            for group_start in range(1, far_end + 1, 10):
                group_end = min(group_start + 9, far_end)
                group_key = f"{group_start}-{group_end}"
                compressed = compressed_summaries.get(group_key)
                if compressed:
                    far_summaries.append(
                        f"第{group_start}-{group_end}章：{compressed.get('summary_text', '')}"
                    )
        if far_summaries:
            context_parts.append(
                f"【前10+章远期摘要】\n" + "\n".join(far_summaries)
            )

        return "\n\n".join(context_parts)

    async def _get_chapter_content(
        self,
        project_id: str,
        chapter_number: int,
        db: AsyncSession,
    ) -> str | None:
        if chapter_number < 1:
            return None
        result = await db.execute(
            select(Chapter).where(
                Chapter.project_id == uuid.UUID(project_id),
                Chapter.chapter_number == chapter_number,
            )
        )
        chapter = result.scalar_one_or_none()
        if chapter and chapter.content:
            return chapter.content
        return None

    def _parse_summary_response(
        self, response: str, chapter_number: int, content: str
    ) -> dict:
        summary = {
            "chapter_number": chapter_number,
            "core_event": "",
            "key_turning_point": "",
            "foreshadowing_updates": [],
            "character_changes": [],
            "unsolved_suspense": "",
            "summary_text": "",
            "word_count": count_words(content),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

        field_map = {
            "核心事件": "core_event",
            "关键转折": "key_turning_point",
            "伏笔操作": "foreshadowing_updates",
            "角色状态变化": "character_changes",
            "未解决悬念": "unsolved_suspense",
            "摘要": "summary_text",
        }

        for line in response.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            match = re.match(r"[-•]\s*(.+?)[:：]\s*(.+)", line)
            if not match:
                continue
            label = match.group(1).strip()
            value = match.group(2).strip()

            field_name = None
            for cn_name, en_name in field_map.items():
                if cn_name in label:
                    field_name = en_name
                    break

            if not field_name:
                continue

            if field_name in ("foreshadowing_updates", "character_changes"):
                items = [item.strip() for item in re.split(r"[，,；;]", value) if item.strip()]
                summary[field_name] = items
            else:
                summary[field_name] = value

        return summary
