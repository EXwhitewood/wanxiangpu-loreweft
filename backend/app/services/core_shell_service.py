"""Core-Shell 记忆宫殿统一访问入口（方案 1）。

替代 WorldviewDigestService 的"信息裁剪"职责。Core 层全量返回，不裁剪；
Shell 层按章节相关性检索。格式化职责仍归 WorldviewDigestService 等格式化层。

设计原则：
1. Core 层是绝对真理，所有 Agent 都应看到完整 Core，不经过任何裁剪。
2. Shell 层按章节相关性检索，数据量可能极大（百万字小说）。
3. 默认模式：完整 Core + 完整 Shell；节省模式：精简版（仅当用户显式开启）。
4. 伏笔状态分层注入：active 类全量，resolved 摘要，aborted 不注入。
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.db_models import Chapter, Project
from app.services.chapter_summary_service import ChapterSummaryService
from app.services.memory_core import CoreMemoryService
from app.services.memory_shell import ShellMemoryService

logger = logging.getLogger(__name__)


# 伏笔状态分层（方案 1 步骤 3）
FORESHADOWING_ACTIVE_STATES = ("planned", "active", "dormant", "revealing")
FORESHADOWING_RESOLVED_STATES = ("resolved",)
FORESHADOWING_SKIPPED_STATES = ("aborted",)


class CoreShellService:
    """Core-Shell 统一访问入口，替代 WorldviewDigestService 的裁剪职责。"""

    def __init__(
        self,
        core_service: CoreMemoryService | None = None,
        shell_service: ShellMemoryService | None = None,
        summary_service: ChapterSummaryService | None = None,
    ) -> None:
        self._core = core_service or CoreMemoryService()
        self._shell = shell_service or ShellMemoryService()
        self._summary = summary_service or ChapterSummaryService()

    # ------------------------------------------------------------------ Core

    async def get_full_core(self, project_id: str) -> dict[str, Any]:
        """获取完整 Core 层数据，不裁剪。

        返回结构化 dict，由调用方决定如何格式化。
        """
        rules = await self._core.list_world_rules(project_id)
        characters = await self._core.list_characters(project_id)
        locations = await self._core.list_locations(project_id)
        # 方案 11 Part A2：include_archived=True 获取 resolved 用于摘要注入
        foreshadowing = await self._core.list_foreshadowing(
            project_id, include_archived=True
        )
        items = await self._list_items(project_id)

        # 伏笔状态分层（方案 1 步骤 3）
        foreshadowing_layered = self._layer_foreshadowing(foreshadowing)

        return {
            "world_rules": rules,
            "characters": [self._dump(c) for c in characters],
            "locations": [self._dump(loc) for loc in locations],
            "foreshadowing": foreshadowing_layered,
            "items": items,
        }

    async def get_compact_core(self, project_id: str) -> dict[str, Any]:
        """节省模式：Core 层精简版。

        - 人物：只保留 name + desire + arc + relationships
        - 规则：只保留 priority=critical/high 的
        - 地点：只保留 name + parent_location
        - 伏笔：只注入活跃态（planned/active/dormant/revealing）
        """
        rules = await self._core.list_world_rules(project_id)
        characters = await self._core.list_characters(project_id)
        locations = await self._core.list_locations(project_id)
        # 方案 11 Part A2：include_archived=True 获取 resolved 用于摘要注入
        foreshadowing = await self._core.list_foreshadowing(
            project_id, include_archived=True
        )
        items = await self._list_items(project_id)

        compact_rules = [
            r for r in rules
            if r.get("priority") in ("critical", "high")
        ]
        compact_characters = [
            {
                "name": self._dump(c).get("name", ""),
                "desire": self._dump(c).get("desire", ""),
                "arc": self._dump(c).get("arc", ""),
                "relationships": self._dump(c).get("relationships", []),
            }
            for c in characters
        ]
        compact_locations = [
            {
                "name": self._dump(loc).get("name", ""),
                "parent_location": self._dump(loc).get("parent_location", ""),
            }
            for loc in locations
        ]
        compact_foreshadowing = [
            f for f in foreshadowing
            if f.get("status") in FORESHADOWING_ACTIVE_STATES
        ]

        return {
            "world_rules": compact_rules,
            "characters": compact_characters,
            "locations": compact_locations,
            "foreshadowing": self._layer_foreshadowing(compact_foreshadowing),
            "items": items,
        }

    # ----------------------------------------------------------------- Shell

    async def get_full_shell(
        self,
        project_id: str,
        chapter_number: int,
        db: AsyncSession | None = None,
    ) -> dict[str, Any]:
        """获取与当前章节相关的 Shell 层数据。

        Shell 数据量大，按章节相关性检索：
        - 上一章完整原文（writer/审查官需要衔接叙事节奏）
        - 最近 6 章摘要（远期前文摘要）
        - 与当前章节相关的 detail_seeds
        - 已确立事实
        """
        previous_chapter_text = await self._get_previous_chapter_text(
            project_id, chapter_number, db
        )
        recent_summaries = await self._get_recent_summaries(
            project_id, chapter_number, count=6, db=db
        )
        detail_seeds = await self._get_relevant_detail_seeds(
            project_id, chapter_number
        )
        established_facts = await self._get_established_facts(
            project_id, chapter_number
        )

        return {
            "previous_chapter_text": previous_chapter_text,
            "recent_summaries": recent_summaries,
            "detail_seeds": detail_seeds,
            "established_facts": established_facts,
        }

    async def get_compact_shell(
        self,
        project_id: str,
        chapter_number: int,
        db: AsyncSession | None = None,
    ) -> dict[str, Any]:
        """节省模式：Shell 层精简版。

        - 上一章完整文本（保留，writer 衔接必需）
        - 前 6 章摘要（保留）
        - detail_seeds：仅 T1/T2
        - established_facts：仅 critical
        """
        previous_chapter_text = await self._get_previous_chapter_text(
            project_id, chapter_number, db
        )
        recent_summaries = await self._get_recent_summaries(
            project_id, chapter_number, count=6, db=db
        )
        all_seeds = await self._get_relevant_detail_seeds(
            project_id, chapter_number
        )
        compact_seeds = [s for s in all_seeds if s.get("tier") in ("T1", "T2")]
        established_facts = await self._get_established_facts(
            project_id, chapter_number
        )
        compact_facts = [f for f in established_facts if f.get("priority") == "critical"]

        return {
            "previous_chapter_text": previous_chapter_text,
            "recent_summaries": recent_summaries,
            "detail_seeds": compact_seeds,
            "established_facts": compact_facts,
        }

    # ---------------------------------------------------------- 内部辅助方法

    def _dump(self, obj: Any) -> dict:
        if hasattr(obj, "model_dump"):
            return obj.model_dump()
        if isinstance(obj, dict):
            return obj
        return {"value": str(obj)}

    def _layer_foreshadowing(self, foreshadowing: list[dict]) -> dict[str, list[dict]]:
        """伏笔状态分层注入（方案 1 步骤 3）。

        - active 类（planned/active/dormant/revealing）：全量注入，所有字段
        - resolved 类：摘要注入，仅 name + resolution_chapter + 一句话揭示结果
        - aborted 类：不注入
        """
        active: list[dict] = []
        resolved: list[dict] = []

        for f in foreshadowing:
            status = f.get("status", "")
            if status in FORESHADOWING_SKIPPED_STATES:
                continue
            if status in FORESHADOWING_ACTIVE_STATES:
                active.append(f)
            elif status in FORESHADOWING_RESOLVED_STATES:
                resolved.append({
                    "name": f.get("name", ""),
                    "resolution_chapter": f.get("resolution_chapter")
                    or f.get("reveal_window_end"),
                    "resolution_summary": f.get("resolution_text")
                    or f.get("reveal_text", ""),
                })

        return {
            "active": active,
            "resolved": resolved,
        }

    async def _list_items(self, project_id: str) -> list[dict]:
        """物品卡列表（依赖方案 3 新增的物品卡；当前返回空列表兜底）。"""
        # 方案 3 实施后改为 await self._core.list_items(project_id)
        try:
            list_items = getattr(self._core, "list_items", None)
            if list_items is None:
                return []
            items = await list_items(project_id)
            return [self._dump(i) for i in items]
        except Exception as exc:
            logger.debug("CoreShellService._list_items fallback to empty: %s", exc)
            return []

    async def _get_previous_chapter_text(
        self,
        project_id: str,
        chapter_number: int,
        db: AsyncSession | None = None,
    ) -> str | None:
        """获取上一章完整原文。"""
        if chapter_number <= 1:
            return None
        target_chapter = chapter_number - 1
        if db is None:
            from app.db.db_models import async_session

            async with async_session() as session:
                return await self._fetch_chapter_content(
                    project_id, target_chapter, session
                )
        return await self._fetch_chapter_content(project_id, target_chapter, db)

    async def _fetch_chapter_content(
        self,
        project_id: str,
        chapter_number: int,
        db: AsyncSession,
    ) -> str | None:
        try:
            result = await db.execute(
                select(Chapter).where(
                    Chapter.project_id == uuid.UUID(project_id),
                    Chapter.chapter_number == chapter_number,
                )
            )
            chapter = result.scalar_one_or_none()
            if chapter and chapter.content:
                return chapter.content
        except Exception as exc:
            logger.warning(
                "CoreShellService._fetch_chapter_content failed ch=%s: %s",
                chapter_number,
                exc,
            )
        return None

    async def _get_recent_summaries(
        self,
        project_id: str,
        chapter_number: int,
        count: int = 6,
        db: AsyncSession | None = None,
    ) -> list[dict]:
        """获取最近 N 章摘要（仅 chapter_number < 当前章）。"""
        if db is None:
            from app.db.db_models import async_session

            async with async_session() as session:
                project = await session.get(Project, uuid.UUID(project_id))
                if not project:
                    return []
                return self._extract_recent_summaries(
                    project, chapter_number, count
                )
        project = await db.get(Project, uuid.UUID(project_id))
        if not project:
            return []
        return self._extract_recent_summaries(project, chapter_number, count)

    def _extract_recent_summaries(
        self,
        project: Project,
        chapter_number: int,
        count: int,
    ) -> list[dict]:
        core_data = project.core_data or {}
        chapter_summaries: dict = core_data.get("chapter_summaries", {}) or {}

        prior_numbers: list[int] = []
        for key in chapter_summaries:
            try:
                num = int(key)
                if num < chapter_number:
                    prior_numbers.append(num)
            except (ValueError, TypeError):
                continue

        prior_numbers.sort(reverse=True)
        recent_numbers = prior_numbers[:count]
        return [
            chapter_summaries[str(num)]
            for num in recent_numbers
            if str(num) in chapter_summaries
        ]

    async def _get_relevant_detail_seeds(
        self,
        project_id: str,
        chapter_number: int,
    ) -> list[dict]:
        """获取与当前章节相关的 detail_seeds。"""
        try:
            seeds = await self._shell.get_seeds_by_tier(project_id, tier=None)
        except Exception as exc:
            logger.warning(
                "CoreShellService._get_relevant_detail_seeds failed: %s", exc
            )
            return []

        relevant: list[dict] = []
        for seed in seeds:
            seed_chapter = (
                seed.get("chapter_number")
                or seed.get("scene_number")
                or 0
            )
            try:
                seed_chapter_int = int(seed_chapter)
            except (ValueError, TypeError):
                seed_chapter_int = 0

            # 当前章 + 前 5 章 + T1/T2 全部（伏笔/人物状态变化必须可见）
            if (
                seed_chapter_int == 0
                or abs(seed_chapter_int - chapter_number) <= 5
                or seed.get("tier") in ("T1", "T2")
            ):
                relevant.append(seed)

        return relevant

    async def _get_established_facts(
        self,
        project_id: str,
        chapter_number: int,
    ) -> list[dict]:
        """获取已确立事实。当前从 detail_seeds 中提取 entity_type=fact 的条目。"""
        try:
            seeds = await self._shell.get_seeds_by_tier(project_id, tier=None)
        except Exception as exc:
            logger.warning(
                "CoreShellService._get_established_facts failed: %s", exc
            )
            return []

        facts: list[dict] = []
        for seed in seeds:
            entity_type = seed.get("entity_type", "")
            if entity_type in ("fact", "established_fact", "world_fact"):
                facts.append(seed)
        return facts
