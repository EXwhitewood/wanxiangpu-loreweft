import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.auth import get_current_user, verify_project_ownership
from app.db.db_models import get_db, Project, User
from app.models.project import ProjectCreate, ProjectResponse, ProjectSummary
from app.services.story_plan_completeness_service import ensure_complete_scene_briefs
from app.services.agent_config import AgentConfigManager
from app.services.genre_profile_service import GenreProfileService
from app.services.llm_client import LLMClient

router = APIRouter()

_daily_inspiration_cache: dict[tuple[str, str], str] = {}
_daily_inspiration_fallback = (
    "试着让本章的阻力来自一个看似合理的选择：当主角做出决定时，谁会因此失去最重要的东西？"
)


def _resolve_genre_profile_id(data: ProjectCreate) -> str:
    """Resolve the operational genre profile without treating free text as fact."""
    if data.genre_profile_id:
        return data.genre_profile_id
    return GenreProfileService._match_genre_to_profile(data.genre) or "general"


@router.get(
    "/{project_id}/daily-inspiration",
    summary="获取今日 AI 灵感",
)
async def get_daily_inspiration(
    project: Project = Depends(verify_project_ownership),
    date_key: str | None = Query(default=None, alias="date"),
):
    """Generate one project-aware inspiration per local calendar day.

    The cache keeps dashboard refreshes from issuing another LLM call. Internet
    content is intentionally not used here: the prompt should stay grounded in
    the author's own project rather than introduce unrelated or copyrighted text.
    """
    requested_date = date_key or datetime.now(timezone.utc).date().isoformat()
    cache_key = (str(project.id), requested_date)
    cached = _daily_inspiration_cache.get(cache_key)
    if cached:
        return {"date": requested_date, "content": cached, "source": "llm"}

    try:
        config = await AgentConfigManager().get_agent_config("writing_companion")
        llm = LLMClient(
            api_format=config.api_format,
            api_key=config.api_key,
            base_url=config.base_url,
            model=config.model,
        )
        system_prompt = (
            "You are a thoughtful Chinese fiction writing companion. "
            "Return exactly one concise paragraph in Simplified Chinese, 45-90 characters. "
            "Offer a concrete, open-ended story question or craft experiment. "
            "Do not use markdown, headings, quotation marks, hype, or mention current events."
        )
        user_prompt = (
            f"Today: {requested_date}\n"
            f"Project: {project.name}\n"
            f"Genre: {project.genre or '未设定'}\n"
            f"Description: {(project.description or '暂无').strip()[:600]}\n"
            f"Current chapter: {project.current_chapter or 1}\n"
            "Create an inspiration that is useful for this project and does not invent fixed facts."
        )
        content = (await llm.generate(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.88,
            max_tokens=180,
            timeout=20,
        )).strip()
        if not content:
            raise RuntimeError("LLM returned empty inspiration")
        _daily_inspiration_cache[cache_key] = content
        # Keep this process-local cache bounded during long-running sessions.
        if len(_daily_inspiration_cache) > 256:
            oldest_key = next(iter(_daily_inspiration_cache))
            _daily_inspiration_cache.pop(oldest_key, None)
        return {"date": requested_date, "content": content, "source": "llm"}
    except Exception:
        return {
            "date": requested_date,
            "content": _daily_inspiration_fallback,
            "source": "fallback",
        }


@router.post(
    "",
    response_model=ProjectResponse,
    summary="创建项目",
    description="创建一个新的小说创作项目，设置项目名称、描述、题材和目标字数。",
)
async def create_project(
    data: ProjectCreate,
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(get_current_user),
):
    genre_profile_id = _resolve_genre_profile_id(data)
    project = Project(
        name=data.name,
        description=data.description,
        genre=data.genre,
        word_count_target=data.word_count_target,
        core_data={"genre_profile_id": genre_profile_id},
        owner_id=user.id if user is not None else None,
    )
    db.add(project)
    await db.commit()
    await db.refresh(project)
    return project


@router.get(
    "",
    response_model=list[ProjectSummary],
    summary="获取项目列表",
    description="获取所有小说创作项目的列表，按创建时间倒序排列。",
)
async def list_projects(
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(get_current_user),
):
    stmt = select(
        Project.id,
        Project.name,
        Project.description,
        Project.genre,
        Project.word_count_target,
        Project.current_chapter,
        Project.total_words,
        Project.created_at,
        Project.updated_at,
    ).order_by(Project.created_at.desc(), Project.id.desc())
    if user is not None and not user.is_admin:
        stmt = stmt.where(Project.owner_id == user.id)
    result = await db.execute(stmt)
    return [dict(row._mapping) for row in result.all()]


@router.get(
    "/{project_id}",
    response_model=ProjectResponse,
    summary="获取项目详情",
    description="根据项目ID获取单个项目的详细信息，包括名称、描述、题材、当前章节、总字数等。",
)
async def get_project(
    project: Project = Depends(verify_project_ownership),
    db: AsyncSession = Depends(get_db),
):
    outline, completion = ensure_complete_scene_briefs(project.outline_data or {})
    if completion["added_scenes"]:
        project.outline_data = outline
        flag_modified(project, "outline_data")
        await db.commit()
        await db.refresh(project)
    return project


@router.put(
    "/{project_id}",
    response_model=ProjectResponse,
    summary="更新项目",
    description="更新指定项目的基本信息，包括名称、描述、题材和目标字数。",
)
async def update_project(
    data: ProjectCreate,
    project: Project = Depends(verify_project_ownership),
    db: AsyncSession = Depends(get_db),
):
    project.name = data.name
    project.description = data.description
    project.genre = data.genre
    project.word_count_target = data.word_count_target
    if data.genre_profile_id:
        core_data = dict(project.core_data or {})
        core_data["genre_profile_id"] = data.genre_profile_id
        project.core_data = core_data
        flag_modified(project, "core_data")
    await db.commit()
    await db.refresh(project)
    return project


@router.delete(
    "/{project_id}",
    summary="删除项目",
    description="删除指定的小说创作项目及其所有相关数据（章节、细节种子等）。",
)
async def delete_project(
    project: Project = Depends(verify_project_ownership),
    db: AsyncSession = Depends(get_db),
):
    project_id = str(project.id)
    from app.services.benchmark_deconstruction import get_benchmark_service
    from app.services.context_ledger_service import get_context_ledger_service
    from app.services.editor_feedback_service import purge_editor_feedback
    from app.services.editor_planning_compiler import get_editor_planning_compiler
    from app.services.editor_trace_collector import get_editor_trace_collector
    from app.services.market_intelligence import get_market_intelligence_service

    get_context_ledger_service().purge(project_id)
    get_editor_trace_collector().purge(project_id)
    get_editor_planning_compiler().purge(project_id)
    purge_editor_feedback(project_id)
    get_benchmark_service().purge_project_binding(project_id)
    get_market_intelligence_service().purge_project(project_id)
    await db.delete(project)
    await db.commit()
    return {"message": "项目已删除"}
