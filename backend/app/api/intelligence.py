from __future__ import annotations

import logging

from fastapi import APIRouter

from app.services.memory_core import CoreMemoryService
from app.services.memory_shell import ShellMemoryService
from app.services.state_manager import StateManager
from app.db.db_models import async_session, Project
from sqlalchemy import select

router = APIRouter()
logger = logging.getLogger(__name__)


async def _get_project_core_data(project_id: str) -> dict:
    async with async_session() as session:
        result = await session.execute(
            select(Project).where(Project.id == __import__("uuid").UUID(project_id))
        )
        project = result.scalar_one_or_none()
        if project:
            return project.core_data or {}
    return {}


def _to_dict(obj) -> dict:
    if obj is None:
        return {}
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if isinstance(obj, dict):
        return obj
    return {}


@router.post(
    "/commentary",
    summary="生成情节评论",
    description="对指定章节生成深度情节评论报告，涵盖叙事节奏、人物弧光、伏笔分析等8个维度。",
)
async def generate_commentary(project_id: str, data: dict):
    chapter_number = data.get("chapter_number")

    core_data = await _get_project_core_data(project_id)
    core_facts = {
        "characters": core_data.get("characters", []),
        "world_rules": core_data.get("world_rules", []),
        "locations": core_data.get("locations", []),
    }

    foreshadowing = []
    try:
        from app.services.foreshadowing_service import ForeshadowingService
        from app.db.db_models import async_session as session_maker
        async with session_maker() as session:
            service = ForeshadowingService()
            lines = await service.list_foreshadowing_lines(
                __import__("uuid").UUID(project_id), session
            )
            foreshadowing = lines
    except Exception as exc:
        logger.warning("[intelligence] 伏笔线加载失败: %s", exc)

    chapter_content = ""
    outline = {}
    if chapter_number:
        try:
            from app.db.db_models import async_session as session_maker, Chapter
            async with session_maker() as session:
                result = await session.execute(
                    select(Chapter).where(
                        Chapter.project_id == __import__("uuid").UUID(project_id),
                        Chapter.chapter_number == chapter_number,
                    )
                )
                chapter = result.scalar_one_or_none()
                if chapter:
                    chapter_content = chapter.content or ""
        except Exception as exc:
            logger.warning("[intelligence] 章节内容加载失败: %s", exc)

    try:
        from app.db.db_models import async_session as session_maker, Project
        async with session_maker() as session:
            result = await session.execute(
                select(Project).where(Project.id == __import__("uuid").UUID(project_id))
            )
            project = result.scalar_one_or_none()
            if project and project.outline_data:
                outline = project.outline_data
    except Exception as exc:
        logger.warning("[intelligence] 大纲加载失败: %s", exc)

    state_manager = StateManager()
    story_state_obj = await state_manager.get_snapshot(project_id)
    story_state = _to_dict(story_state_obj)

    from app.agents.plot_commentary import PlotCommentaryAgent
    agent = PlotCommentaryAgent()
    result = await agent.execute({
        "project_id": project_id,
        "chapter_number": chapter_number,
        "chapter_content": chapter_content,
        "outline": outline,
        "story_state": story_state,
        "core_facts": core_facts,
        "foreshadowing": foreshadowing,
    })

    return result


@router.post(
    "/detective",
    summary="细节侦探排查",
    description="使用细节侦探Agent对指定章节进行微观矛盾排查，发现时间线、空间、物理细节等8个维度的问题。",
)
async def run_detective(project_id: str, data: dict):
    chapter_number = data.get("chapter_number")
    scan_scope = data.get("scan_scope", "chapter")

    core_data = await _get_project_core_data(project_id)
    core_facts = {
        "characters": core_data.get("characters", []),
        "world_rules": core_data.get("world_rules", []),
        "locations": core_data.get("locations", []),
    }

    shell_service = ShellMemoryService()
    shell_seeds = []
    try:
        seeds = await shell_service.search_seeds(project_id, "*", limit=30)
        shell_seeds = [{"tier": s.get("tier", ""), "fact": s.get("fact", "")} for s in seeds]
    except Exception as exc:
        logger.warning("[intelligence] Shell seeds 搜索失败: %s", exc)

    chapter_content = ""
    if chapter_number:
        try:
            from app.db.db_models import async_session as session_maker, Chapter
            async with session_maker() as session:
                result = await session.execute(
                    select(Chapter).where(
                        Chapter.project_id == __import__("uuid").UUID(project_id),
                        Chapter.chapter_number == chapter_number,
                    )
                )
                chapter = result.scalar_one_or_none()
                if chapter:
                    chapter_content = chapter.content or ""
        except Exception as exc:
            logger.warning("[intelligence] 章节内容加载失败: %s", exc)

    state_manager = StateManager()
    story_state_obj = await state_manager.get_snapshot(project_id)
    story_state = _to_dict(story_state_obj)

    from app.agents.detail_detective import DetailDetectiveAgent
    agent = DetailDetectiveAgent()
    result = await agent.execute({
        "project_id": project_id,
        "chapter_number": chapter_number,
        "chapter_content": chapter_content,
        "core_facts": core_facts,
        "story_state": story_state,
        "shell_seeds": shell_seeds,
        "scan_scope": scan_scope,
    })

    return result


@router.post(
    "/branches",
    summary="生成分支探索",
    description="在指定分支点生成多个逻辑自洽的平行分支，每个分支包含评分和伏笔机会分析。",
)
async def generate_branches(project_id: str, data: dict):
    chapter_number = data.get("chapter_number", 1)
    branch_point = data.get("branch_point", "")
    branch_count = data.get("branch_count", 3)

    core_data = await _get_project_core_data(project_id)
    core_facts = {
        "characters": core_data.get("characters", []),
        "world_rules": core_data.get("world_rules", []),
        "locations": core_data.get("locations", []),
    }

    current_content = ""
    outline = {}
    try:
        from app.db.db_models import async_session as session_maker, Chapter
        async with session_maker() as session:
            result = await session.execute(
                select(Chapter).where(
                    Chapter.project_id == __import__("uuid").UUID(project_id),
                    Chapter.chapter_number == chapter_number,
                )
            )
            chapter = result.scalar_one_or_none()
            if chapter:
                current_content = chapter.content or ""
    except Exception as exc:
        logger.warning("[intelligence] 章节内容加载失败: %s", exc)

    try:
        from app.db.db_models import async_session as session_maker, Project
        async with session_maker() as session:
            result = await session.execute(
                select(Project).where(Project.id == __import__("uuid").UUID(project_id))
            )
            project = result.scalar_one_or_none()
            if project and project.outline_data:
                outline = project.outline_data
    except Exception as exc:
        logger.warning("[intelligence] 大纲加载失败: %s", exc)

    state_manager = StateManager()
    story_state_obj = await state_manager.get_snapshot(project_id)
    story_state = _to_dict(story_state_obj)

    from app.agents.branch_exploration import BranchExplorationAgent
    agent = BranchExplorationAgent()
    result = await agent.execute({
        "project_id": project_id,
        "chapter_number": chapter_number,
        "branch_point": branch_point,
        "current_content": current_content,
        "outline": outline,
        "story_state": story_state,
        "core_facts": core_facts,
        "branch_count": branch_count,
    })

    return result
