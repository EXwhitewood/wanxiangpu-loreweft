import copy
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.db_models import Chapter, ChapterBaseline, ChapterSnapshot, Project, get_db
from app.models.story_state import StoryState
from app.services.chapter_commit_service import (
    discard_worldview_projection_outbox,
    purge_chapter_artifacts,
)
from app.services.foreshadowing_action_sanitizer import sanitize_foreshadowing_actions
from app.services.narrative_sync_service import NarrativeSyncService
from app.services.state_manager import StateManager
from app.services.project_chapter_aggregate_service import refresh_project_chapter_aggregates
from app.services.user_chapter_settlement_job_service import (
    enqueue_user_chapter_settlement,
    latest_user_chapter_settlement,
    schedule_user_chapter_settlement,
    serialize_settlement_job,
)
from app.utils.word_count import count_words

router = APIRouter()

_USER_SAVE_REFERENCE_KEY = "_user_save_reference"


def _find_chapter_record(outline_data: dict, chapter_number: int) -> tuple[dict | None, str | None]:
    chapter_spine = outline_data.get("chapter_spine", []) or []
    for chapter in chapter_spine:
        if chapter.get("chapter_number") == chapter_number:
            return chapter, "chapter_spine"

    legacy_chapters = outline_data.get("chapters", []) or []
    for chapter in legacy_chapters:
        if chapter.get("chapter_number") == chapter_number:
            return chapter, "chapters"

    return None, None


def _find_all_chapter_records(outline_data: dict, chapter_number: int) -> list[tuple[dict, str]]:
    records: list[tuple[dict, str]] = []
    for chapter in outline_data.get("chapter_spine", []) or []:
        if chapter.get("chapter_number") == chapter_number:
            records.append((chapter, "chapter_spine"))
            break

    for chapter in outline_data.get("chapters", []) or []:
        if chapter.get("chapter_number") == chapter_number:
            records.append((chapter, "chapters"))
            break

    return records


@router.get(
    "",
    summary="获取章节列表",
    description="获取指定项目的所有章节列表，包含章节号、标题、状态和字数统计。",
)
async def list_chapters(project_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    result = await db.execute(
        select(Chapter)
        .where(Chapter.project_id == project_id)
        .order_by(Chapter.chapter_number)
    )
    chapters = result.scalars().all()
    return [
        {
            "chapter_number": chapter.chapter_number,
            "title": chapter.title,
            "status": chapter.status,
            "word_count": count_words(chapter.content) if chapter.content else 0,
        }
        for chapter in chapters
    ]


@router.get(
    "/history",
    summary="获取生成历史",
    description="获取项目的所有生成历史记录，包含章节号、时间戳、字数和校验报告摘要。",
)
async def get_generation_history(project_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    core_data = project.core_data or {}
    history = core_data.get("generation_history", [])
    if not isinstance(history, list):
        history = []
    return history


@router.get(
    "/{chapter_number}",
    summary="获取章节内容",
    description="获取指定章节的完整内容，包括正文、标题、状态和创建/更新时间。",
)
async def get_chapter(project_id: uuid.UUID, chapter_number: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Chapter).where(
            Chapter.project_id == project_id,
            Chapter.chapter_number == chapter_number,
        )
    )
    chapter = result.scalar_one_or_none()
    if not chapter:
        raise HTTPException(status_code=404, detail="章节不存在")
    return {
        "chapter_number": chapter.chapter_number,
        "title": chapter.title,
        "content": chapter.content,
        "status": chapter.status,
        "created_at": chapter.created_at,
        "updated_at": chapter.updated_at,
    }


@router.put(
    "/{chapter_number}",
    summary="更新章节内容",
    description="更新或创建指定章节的内容。可更新字段：title（标题）、content（正文）、status（状态）。",
)
async def update_chapter(
    project_id: uuid.UUID,
    chapter_number: int,
    data: dict,
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    result = await db.execute(
        select(Chapter).where(
            Chapter.project_id == project_id,
            Chapter.chapter_number == chapter_number,
        )
    )
    chapter = result.scalar_one_or_none()
    content_changed = "content" in data and (
        chapter is None or chapter.content != data.get("content", "")
    )
    saved_content = data.get("content", chapter.content if chapter is not None else "") or ""
    has_substantive_content = count_words(saved_content) > 0
    created_empty_chapter = chapter is None and not has_substantive_content
    should_run_auxiliary_settlement = has_substantive_content and not created_empty_chapter

    # Freeze the original pre-chapter references before any new projection is
    # applied.  A repeated explicit save reuses its durable baseline so the
    # chapter cannot be compared with facts it wrote back itself.
    reference_snapshot: dict = {
        "outline_data": copy.deepcopy(project.outline_data or {}),
        "core_data": copy.deepcopy(project.core_data or {}),
        "outline_version": int(project.outline_version or 0),
    }
    baseline_result = await db.execute(
        select(ChapterBaseline).where(
            ChapterBaseline.project_id == project_id,
            ChapterBaseline.chapter_number == chapter_number,
        )
    )
    baseline = baseline_result.scalar_one_or_none()
    if baseline is not None:
        reference_snapshot["outline_version"] = int(baseline.outline_version or 0)
        reference_snapshot["story_state"] = copy.deepcopy(
            baseline.baseline_story_state or {}
        )
        baseline_metadata = (
            baseline.baseline_chapter_state
            if isinstance(baseline.baseline_chapter_state, dict)
            else {}
        )
        stored_reference = baseline_metadata.get(_USER_SAVE_REFERENCE_KEY)
        if isinstance(stored_reference, dict):
            for key in ("outline_data", "core_data", "outline_version", "story_state"):
                if key in stored_reference:
                    reference_snapshot[key] = copy.deepcopy(stored_reference[key])
    elif should_run_auxiliary_settlement:
        try:
            baseline_state = await StateManager().get_state(str(project_id))
            reference_snapshot["story_state"] = (
                baseline_state.model_dump()
                if hasattr(baseline_state, "model_dump")
                else dict(baseline_state or {})
            )
        except Exception:
            reference_snapshot["story_state"] = {}
    if chapter and content_changed:
        # Two-phase commit: do NOT retract old worldview here.
        # project_committed_chapter will retract old generation_revisions
        # after new observations are successfully created.
        await discard_worldview_projection_outbox(db, project_id, chapter_number)

    if not chapter:
        chapter = Chapter(
            project_id=project_id,
            chapter_number=chapter_number,
            title=data.get("title", f"第{chapter_number}章"),
            content=data.get("content", ""),
            status=data.get("status", "draft"),
        )
        db.add(chapter)
    else:
        if "title" in data:
            chapter.title = data["title"]
        if "content" in data:
            chapter.content = data["content"]
        if "status" in data:
            chapter.status = data["status"]

    await db.flush()
    await refresh_project_chapter_aggregates(
        db,
        project_id=project_id,
        project=project,
    )
    settlement_job = None
    if should_run_auxiliary_settlement:
        settlement_job = await enqueue_user_chapter_settlement(
            db,
            project=project,
            chapter=chapter,
            reference_snapshot=reference_snapshot,
        )
    await db.commit()
    await db.refresh(chapter)

    if created_empty_chapter:
        return {
            "chapter_number": chapter.chapter_number,
            "title": chapter.title,
            "content": chapter.content,
            "status": chapter.status,
        }

    if settlement_job is not None and settlement_job.status in {"pending", "retryable_failed"}:
        schedule_user_chapter_settlement(settlement_job.id)

    return {
        "chapter_number": chapter.chapter_number,
        "title": chapter.title,
        "content": chapter.content,
        "status": chapter.status,
        "settlement": serialize_settlement_job(settlement_job) if settlement_job else None,
    }


@router.get(
    "/{chapter_number}/settlement",
    summary="获取用户章节结算状态",
    description="返回该章最新一次手写保存的附属结算阶段，不影响正文读取。",
)
async def get_chapter_settlement(
    project_id: uuid.UUID,
    chapter_number: int,
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    job = await latest_user_chapter_settlement(
        db,
        project_id=project_id,
        chapter_number=chapter_number,
    )
    return serialize_settlement_job(job) if job else None


@router.post(
    "/{chapter_number}/settlement/retry",
    summary="重试用户章节结算",
    description="只重试世界观、状态和诊断等附属工作，不改写章节正文。",
)
async def retry_chapter_settlement(
    project_id: uuid.UUID,
    chapter_number: int,
    db: AsyncSession = Depends(get_db),
):
    job = await latest_user_chapter_settlement(
        db,
        project_id=project_id,
        chapter_number=chapter_number,
    )
    if job is None:
        raise HTTPException(status_code=404, detail="没有可重试的结算作业")
    if job.status == "completed":
        return serialize_settlement_job(job)
    if job.status == "superseded":
        raise HTTPException(status_code=409, detail="该作业已被更新正文取代，请重新保存当前正文")
    if int(job.attempts or 0) >= 3 and job.status == "degraded":
        job.attempts = 0
    job.status = "pending"
    job.phase = "queued"
    job.error_message = ""
    job.next_retry_at = None
    await db.commit()
    schedule_user_chapter_settlement(job.id)
    return serialize_settlement_job(job)


@router.delete(
    "/{chapter_number}",
    summary="删除章节",
    description="删除指定项目的指定章节。删除后不可恢复。",
)
async def delete_chapter(project_id: uuid.UUID, chapter_number: int, db: AsyncSession = Depends(get_db)):
    from app.services.project_lock import ProjectLockManager
    from app.services.state_manager import StateManager

    project_lock = ProjectLockManager.get_lock(str(project_id))
    if project_lock.locked():
        raise HTTPException(status_code=409, detail="该项目正在生成中，请先终止工作流再删除章节")

    result = await db.execute(
        select(Chapter).where(
            Chapter.project_id == project_id,
            Chapter.chapter_number == chapter_number,
        )
    )
    chapters = result.scalars().all()
    if not chapters:
        raise HTTPException(status_code=404, detail="章节不存在")

    latest_chapter_number = await db.scalar(
        select(func.max(Chapter.chapter_number)).where(Chapter.project_id == project_id)
    )
    if latest_chapter_number is not None and int(chapter_number) != int(latest_chapter_number):
        raise HTTPException(
            status_code=409,
            detail=(
                f"只能删除最新章节。当前最新章节是第{latest_chapter_number}章，"
                f"请先删除第{latest_chapter_number}章。"
            ),
        )

    chapter_id = chapters[0].id

    previous_snapshot = None
    if chapter_number > 1:
        previous_snapshot_result = await db.execute(
            select(ChapterSnapshot).where(
                ChapterSnapshot.project_id == project_id,
                ChapterSnapshot.chapter_number == chapter_number - 1,
                ChapterSnapshot.stale == False,
            )
        )
        previous_snapshot = previous_snapshot_result.scalar_one_or_none()

    cleanup = await purge_chapter_artifacts(
        db,
        project_id=project_id,
        chapter_number=chapter_number,
        chapter_id=chapter_id,
        full_chapter=True,
    )
    sync_service = NarrativeSyncService()
    await sync_service.sync_chapter_delete(
        project_id,
        chapter_number,
        db,
        source_system="chapter_api",
        retract_worldview=False,
        commit=False,
    )
    for chapter in chapters:
        await db.delete(chapter)
    await db.flush()

    project = await db.get(Project, project_id)
    if project:
        await refresh_project_chapter_aggregates(
            db,
            project_id=project_id,
            project=project,
        )
    await db.commit()

    restored_chapter_number = (
        max(1, int(project.current_chapter or 1) - 1)
        if project is not None
        else 1
    )
    if previous_snapshot and previous_snapshot.story_state:
        restored_state = StoryState(**previous_snapshot.story_state)
        restored_state.active_chapter = restored_chapter_number
        restored_state.active_scene = 0
    else:
        restored_state = StoryState(active_chapter=restored_chapter_number, active_scene=0)
    await StateManager().set_state(str(project_id), restored_state)

    return {
        "message": f"第{chapter_number}章已删除",
        "cleanup": cleanup,
    }


@router.get(
    "/{chapter_number}/outline",
    summary="获取章节大纲",
    description="获取指定章节的大纲节拍卡，包含场景列表、主冲突、价值转折等信息。",
)
async def get_chapter_outline(project_id: uuid.UUID, chapter_number: int, db: AsyncSession = Depends(get_db)):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    outline_data = project.outline_data or {}
    target, _ = _find_chapter_record(outline_data, chapter_number)
    if target:
        return target

    raise HTTPException(status_code=404, detail="章节大纲不存在")


@router.put(
    "/{chapter_number}/outline",
    summary="更新章节大纲",
    description="更新指定章节的大纲数据（标题、主冲突、价值转折、场景等）。只在未冻结状态下可修改，修改会同步到大纲总文件。",
)
async def update_chapter_outline(
    project_id: uuid.UUID,
    chapter_number: int,
    data: dict,
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    outline_data = project.outline_data or {}
    if outline_data.get("_frozen"):
        raise HTTPException(status_code=403, detail="大纲已冻结，无法修改章节大纲")

    records = _find_all_chapter_records(outline_data, chapter_number)
    if not records:
        raise HTTPException(status_code=404, detail="章节大纲不存在")

    for target, source in records:
        if "title" in data:
            target["title"] = data["title"]
        if "main_conflict" in data:
            target["main_conflict"] = data["main_conflict"]
        if "value_shift" in data:
            target["value_shift"] = data["value_shift"]
        if "pov_character" in data:
            target["pov_character"] = data["pov_character"]
        if "foreshadowing_actions" in data:
            target["thread_ops"] = [
                {
                    "thread_id": item.get("name", ""),
                    "op": item.get("action", "plant"),
                    "mode": item.get("mode", "subtle"),
                }
                for item in sanitize_foreshadowing_actions(data["foreshadowing_actions"])
                if item.get("name")
            ]
            target.pop("foreshadowing_actions", None)
            target.pop("foreshadowing_operations", None)
            target.pop("legacy_payload", None)
        if "thread_ops" in data and isinstance(data["thread_ops"], list):
            target["thread_ops"] = data["thread_ops"]
            target.pop("foreshadowing_actions", None)
            target.pop("foreshadowing_operations", None)
            target.pop("legacy_payload", None)
        if "scenes" in data:
            target["scenes"] = data["scenes"]

        if source == "chapter_spine":
            if "main_conflict" in data:
                target["conflict_text"] = data["main_conflict"]
                target["core_conflict"] = data["main_conflict"]

    project.outline_data = outline_data
    await db.commit()
    await db.refresh(project)
    return records[0][0]


@router.get(
    "/{chapter_number}/scenes",
    summary="获取章节场景列表",
    description="获取指定章节的所有场景节拍列表，包含目标、冲突、结果等信息。",
)
async def list_chapter_scenes(project_id: uuid.UUID, chapter_number: int, db: AsyncSession = Depends(get_db)):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    outline_data = project.outline_data or {}
    target, _ = _find_chapter_record(outline_data, chapter_number)
    if target:
        return target.get("scenes", [])

    raise HTTPException(status_code=404, detail="章节大纲不存在")


@router.put(
    "/{chapter_number}/scenes",
    summary="更新章节场景列表",
    description="替换指定章节的场景节拍列表。只在未冻结状态下可修改。",
)
async def update_chapter_scenes(
    project_id: uuid.UUID,
    chapter_number: int,
    data: dict,
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    outline_data = project.outline_data or {}
    if outline_data.get("_frozen"):
        raise HTTPException(status_code=403, detail="大纲已冻结，无法修改场景")

    records = _find_all_chapter_records(outline_data, chapter_number)
    if not records:
        raise HTTPException(status_code=404, detail="章节大纲不存在")

    scenes = data.get("scenes", [])
    for target, _ in records:
        target["scenes"] = scenes

    ch_id = f"ch_{chapter_number:03d}"
    scene_briefs = outline_data.get("scene_briefs", {})
    if ch_id in scene_briefs:
        scene_briefs[ch_id]["scenes"] = scenes
        outline_data["scene_briefs"] = scene_briefs

    project.outline_data = outline_data
    await db.commit()
    await db.refresh(project)
    return records[0][0].get("scenes", [])


@router.post(
    "/{chapter_number}/scenes",
    summary="添加场景",
    description="在指定章节末尾添加一个新场景节拍。只在未冻结状态下可修改。",
)
async def add_chapter_scene(
    project_id: uuid.UUID,
    chapter_number: int,
    data: dict,
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    outline_data = project.outline_data or {}
    if outline_data.get("_frozen"):
        raise HTTPException(status_code=403, detail="大纲已冻结，无法添加场景")

    records = _find_all_chapter_records(outline_data, chapter_number)
    if not records:
        raise HTTPException(status_code=404, detail="章节大纲不存在")

    scene = data.get("scene", {})
    for target, _ in records:
        scenes = target.get("scenes", [])
        scenes.append(scene)
        target["scenes"] = scenes

    ch_id = f"ch_{chapter_number:03d}"
    scene_briefs = outline_data.get("scene_briefs", {})
    if ch_id in scene_briefs:
        scene_briefs[ch_id]["scenes"] = scene_briefs[ch_id].get("scenes", [])
        scene_briefs[ch_id]["scenes"].append(scene)
        outline_data["scene_briefs"] = scene_briefs

    project.outline_data = outline_data
    await db.commit()
    await db.refresh(project)
    return {"message": "场景已添加", "scene_count": len(records[0][0].get("scenes", []))}
