import json
import logging
import copy
import re
import uuid

from datetime import datetime, timezone
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.db.db_models import get_db, Project
from app.agents.outline_architect import OutlineArchitectAgent
from app.services.cross_system_event_bus import CrossSystemEventBus
from app.services.story_plan_completeness_service import ensure_complete_scene_briefs
from app.services.llm_task_profiles import LLMTaskType

logger = logging.getLogger(__name__)
router = APIRouter()


class UpdateLayerRequest(BaseModel):
    data: dict


class SaveChapterBlueprintRequest(BaseModel):
    expected_revision: int | None = None
    chapter: dict
    scenes: list[dict]
    confirm_recovery: bool = False


class AppendChapterSpineRequest(BaseModel):
    title: str = ""
    summary: str = ""
    pov_character: str = ""

GUIDED_TOPICS = [
    "core_theme",
    "protagonist_type",
    "conflict_style",
    "antagonist_design",
    "emotional_tone",
    "chapter_count",
    "special_requirements",
]

GUIDED_TOPIC_NAMES = {
    "core_theme": "核心主题",
    "protagonist_type": "主角人设",
    "conflict_style": "冲突节奏",
    "antagonist_design": "对手设计",
    "emotional_tone": "情感基调",
    "chapter_count": "章节数量",
    "special_requirements": "特殊要求",
}

# A long-form target (for example 1000-1200 chapters) describes the book, not
# the size of one model response.  Guided and automatic entry points hand such
# work to the durable expansion workflow; this value remains the per-response
# safety ceiling for the short-outline fallback.
MAX_GUIDED_CHAPTERS_PER_BATCH = 30


def _raise_story_plan_error(result: dict) -> None:
    """Turn service validation failures into a real API failure.

    Several older callers ignored StoryPlanService's structured error result,
    which made an invalid outline look successfully saved to the client.
    """
    if not result.get("error"):
        return
    detail = {
        "code": result.get("code") or ("chapter_continuity_error" if result.get("chapter_continuity") else "outline_save_error"),
        "message": result["error"],
    }
    if result.get("chapter_continuity"):
        detail["chapter_continuity"] = result["chapter_continuity"]
    for field in ("expected_chapter_count", "actual_chapter_count"):
        if result.get(field) is not None:
            detail[field] = result[field]
    raise HTTPException(status_code=int(result.get("status_code", 422)), detail=detail)


class OutlineChatMessage(BaseModel):
    role: str
    content: str


class OutlineChatRequest(BaseModel):
    messages: list[OutlineChatMessage]
    context_mode: Literal["master", "chapter"] = "master"
    selected_chapter_number: int | None = None


class OutlineExpansionRequest(BaseModel):
    target_chapters: int = Field(..., ge=1, le=2000)
    batch_size: int = Field(default=25, ge=5, le=40)
    replace_existing: bool = True
    force_restart: bool = False
    seed_context: dict = Field(default_factory=dict)


class OutlineSplitRequest(BaseModel):
    outline_text: str


@router.post(
    "/{project_id}/chat",
    summary="大纲设计对话",
    description="与大纲架构师 Agent 进行对话，设计小说大纲。支持多轮对话，Agent 会根据项目信息和已有大纲提供建议。当用户确认大纲后，Agent 会输出 JSON 格式的完整大纲。",
)
async def outline_chat(
    project_id: uuid.UUID,
    data: OutlineChatRequest,
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    if data.context_mode == "chapter" and data.selected_chapter_number is None:
        raise HTTPException(status_code=422, detail="章级模式必须指定章节号")

    agent = OutlineArchitectAgent()
    result = await agent.execute({
        "messages": [msg.model_dump() for msg in data.messages],
        "project_info": {
            "name": project.name,
            "description": project.description or "",
            "genre": project.genre or "",
            "word_count_target": project.word_count_target,
        },
        "existing_outline": project.outline_data,
        "project_id": str(project_id),
        "db": db,
        "context_mode": data.context_mode,
        "selected_chapter_number": data.selected_chapter_number,
    })

    return {"response": result["response"]}


@router.post(
    "/{project_id}/chat/stream",
    summary="流式对话（SSE）",
    description="与大纲架构师流式对话。工具调用阶段发送中间事件，最终文本阶段流式输出。",
)
async def outline_chat_stream(
    project_id: uuid.UUID,
    data: OutlineChatRequest,
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    if data.context_mode == "chapter" and data.selected_chapter_number is None:
        raise HTTPException(status_code=422, detail="章级模式必须指定章节号")

    async def event_generator():
        agent = OutlineArchitectAgent()
        async for event in agent.execute_stream({
            "messages": [msg.model_dump() for msg in data.messages],
            "project_info": {
                "name": project.name,
                "description": project.description or "",
                "genre": project.genre or "",
                "word_count_target": project.word_count_target,
            },
            "existing_outline": project.outline_data,
            "project_id": str(project_id),
            "db": db,
            "context_mode": data.context_mode,
            "selected_chapter_number": data.selected_chapter_number,
        }):
            yield f"event: {event['type']}\ndata: {json.dumps(event['data'], ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/{project_id}/expansion/start",
    summary="启动分批章节扩展",
    description=(
        "执行服务器托管的多章节规划。系统按目标规模选择一个或多个批次，全部完成并通过连续性校验后才替换正式大纲。"
    ),
)
async def start_outline_expansion(
    project_id: uuid.UUID,
    data: OutlineExpansionRequest,
    db: AsyncSession = Depends(get_db),
):
    from app.services.outline_expansion_service import OutlineExpansionService

    result = await OutlineExpansionService().start(
        project_id,
        data.target_chapters,
        db,
        batch_size=data.batch_size,
        replace_existing=data.replace_existing,
        force_restart=data.force_restart,
        seed_context=data.seed_context,
    )
    if result.get("error"):
        _raise_story_plan_error(result)
    return result


@router.get(
    "/{project_id}/expansion/status",
    summary="读取章节扩展进度",
)
async def get_outline_expansion_status(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    from app.services.outline_expansion_service import OutlineExpansionService

    result = await OutlineExpansionService().get_status(project_id, db)
    if result.get("error"):
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.post(
    "/{project_id}/expansion/resume",
    summary="续跑章节扩展",
)
async def resume_outline_expansion(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    from app.services.outline_expansion_service import OutlineExpansionService

    result = await OutlineExpansionService().resume(project_id, db)
    if result.get("error"):
        _raise_story_plan_error(result)
    return result


@router.post(
    "/{project_id}/expansion/cancel",
    summary="取消章节扩展",
)
async def cancel_outline_expansion(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    from app.services.outline_expansion_service import OutlineExpansionService

    result = await OutlineExpansionService().cancel(project_id, db)
    if result.get("error"):
        _raise_story_plan_error(result)
    return result


@router.get(
    "/{project_id}/read",
    summary="读取大纲（其他Agent只读接口）",
)
async def read_outline(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    from app.services.outline_read_service import OutlineReadService
    service = OutlineReadService()
    return await service.get_outline(project_id, db)


@router.get(
    "/{project_id}/read/{chapter_number}",
    summary="读取单章大纲（其他Agent只读接口）",
)
async def read_chapter(
    project_id: uuid.UUID,
    chapter_number: int,
    db: AsyncSession = Depends(get_db),
):
    from app.services.outline_read_service import OutlineReadService
    service = OutlineReadService()
    result = await service.get_chapter(project_id, chapter_number, db)
    if result is None:
        raise HTTPException(status_code=404, detail=f"第 {chapter_number} 章不存在")
    return result


@router.post(
    "/{project_id}/suggest",
    summary="提交修改建议（其他Agent用，仅记录不执行）",
)
async def suggest_modification(
    project_id: uuid.UUID,
    data: dict,
    db: AsyncSession = Depends(get_db),
):
    from app.services.outline_read_service import OutlineReadService
    service = OutlineReadService()
    return await service.suggest_modification(project_id, data, db)


@router.post(
    "/{project_id}/save",
    summary="保存大纲",
    description="将 JSON 格式的大纲数据保存到项目中。大纲保存后会覆盖项目原有的大纲数据。",
)
async def save_outline(
    project_id: uuid.UUID,
    data: dict,
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    data, completion = _ensure_complete_scene_briefs(data)
    existing_outline = project.outline_data or {}
    if existing_outline.get("_frozen"):
        raise HTTPException(status_code=403, detail="大纲已冻结，无法修改。请先解冻大纲或通过修订案机制修改。")

    from app.services.outline_memory_service import OutlineMemoryService
    memory_service = OutlineMemoryService()
    old_outline = dict(existing_outline)

    from app.services.story_plan_service import StoryPlanService
    sp_service = StoryPlanService()
    save_result = await sp_service.save_outline_data(project_id, data, db, mode="replace")
    _raise_story_plan_error(save_result)

    cd = project.core_data or {}
    if cd.get("weave_coordinator", {}).get("enabled", False):
        event_bus = CrossSystemEventBus()
        await event_bus.publish_event(
            db=db,
            project_id=str(project_id),
            event_type="OUTLINE_SAVED.v1",
            source_system="outline",
            priority="normal",
            payload={
                "entity_type": "outline",
                "entity_id": str(project_id),
                "entity_name": project.name or "",
                "change_type": "saved",
                "summary": f"大纲已保存：{project.name or str(project_id)}",
                "meta": {"chapter_count": len(data.get("chapters", []))},
            },
        )

    await db.commit()
    await db.refresh(project)

    try:
        from app.services.narrative_sync_service import NarrativeSyncService

        sync_service = NarrativeSyncService()
        await sync_service.sync_outline_save(
            project_id,
            db,
            old_outline=old_outline,
            new_outline=data,
            source_system="outline",
            publish_event=False,
        )
        await db.refresh(project)
    except Exception as e:
        logger.warning(f"[OutlineAPI] narrative sync failed: {e}")

    from app.services.outline_index_service import OutlineIndexService
    index_service = OutlineIndexService()
    await index_service.generate_and_save_index(project_id, db)

    if old_outline.get("chapters") or data.get("chapters"):
        try:
            change_records = await memory_service.auto_record_outline_changes(
                project_id, old_outline, data, db
            )
        except Exception as e:
            change_records = []
            logger.warning(f"[OutlineAPI] auto_record_changes failed: {e}")
    else:
        change_records = []

    from app.skills.outline_validation import OutlineValidationSkill
    validator = OutlineValidationSkill()
    validation = validator.validate(data, project.core_data)

    try:
        from app.services.outline_embedding_service import OutlineEmbeddingService
        from app.db.db_models import PGVECTOR_AVAILABLE
        if PGVECTOR_AVAILABLE:
            emb_service = OutlineEmbeddingService()
            await emb_service.generate_embeddings_for_project(project_id, db)
    except Exception as e:
        logger.warning(f"[OutlineAPI] embedding generation failed: {e}")

    return {
        "message": "大纲已保存",
        "outline_data": project.outline_data,
        "validation": validation,
        "change_records": len(change_records),
        "scene_completion": completion,
    }


@router.post(
    "/{project_id}/parse",
    summary="解析大纲文本",
    description="从 Agent 输出的文本中提取 JSON 格式的大纲数据。自动识别 ```json 代码块或直接 JSON 内容。",
)
async def parse_outline(
    project_id: uuid.UUID,
    data: OutlineSplitRequest,
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    outline_json = _extract_json(data.outline_text)
    if outline_json is None:
        raise HTTPException(status_code=400, detail="无法从文本中解析出有效的大纲 JSON")
    outline_json, completion = _ensure_complete_scene_briefs(outline_json)

    existing_outline = project.outline_data or {}
    if existing_outline.get("_frozen"):
        raise HTTPException(status_code=403, detail="大纲已冻结，无法修改。请先解冻大纲或通过修订案机制修改。")

    from app.services.outline_memory_service import OutlineMemoryService
    memory_service = OutlineMemoryService()
    old_outline = dict(existing_outline)

    from app.services.story_plan_service import StoryPlanService
    sp_service = StoryPlanService()
    save_result = await sp_service.save_outline_data(project_id, outline_json, db, mode="replace")
    _raise_story_plan_error(save_result)
    await db.commit()
    await db.refresh(project)

    if old_outline.get("chapters") or project.outline_data.get("chapters"):
        try:
            change_records = await memory_service.auto_record_outline_changes(
                project_id, old_outline, project.outline_data, db
            )
        except Exception as e:
            change_records = []
            logger.warning(f"[OutlineAPI] auto_record_changes failed: {e}")
    else:
        change_records = []

    from app.skills.outline_validation import OutlineValidationSkill
    validator = OutlineValidationSkill()
    validation = validator.validate(outline_json, project.core_data)

    try:
        from app.services.outline_embedding_service import OutlineEmbeddingService
        from app.db.db_models import PGVECTOR_AVAILABLE
        if PGVECTOR_AVAILABLE:
            emb_service = OutlineEmbeddingService()
            await emb_service.generate_embeddings_for_project(project_id, db)
    except Exception as e:
        logger.warning(f"[OutlineAPI] embedding generation failed: {e}")

    return {
        "message": "大纲已解析并保存",
        "outline_data": project.outline_data,
        "validation": validation,
        "change_records": len(change_records),
        "scene_completion": completion,
    }


def _extract_json(text: str) -> dict | None:
    json_block = re.search(r"```\s*(?:json|JSON)?\s*(.*?)\s*```", text, re.DOTALL)
    if json_block:
        try:
            return json.loads(json_block.group(1))
        except json.JSONDecodeError:
            repaired = _repair_json_text(json_block.group(1))
            if repaired:
                try:
                    return json.loads(repaired)
                except json.JSONDecodeError:
                    pass

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        repaired = _repair_json_text(text)
        if repaired:
            try:
                return json.loads(repaired)
            except json.JSONDecodeError:
                pass

    for candidate in _iter_json_object_candidates(text):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            repaired = _repair_json_text(candidate)
            if repaired:
                try:
                    return json.loads(repaired)
                except json.JSONDecodeError:
                    pass

    return None


def _repair_json_text(text: str) -> str:
    repaired = text.strip()
    repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
    return repaired


def _iter_json_object_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    stack = 0
    start = -1
    in_string = False
    escape = False

    for idx, char in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
            continue
        if char == "{":
            if stack == 0:
                start = idx
            stack += 1
        elif char == "}" and stack > 0:
            stack -= 1
            if stack == 0 and start != -1:
                candidates.append(text[start:idx + 1])
                start = -1

    if not candidates:
        brace_start = text.find("{")
        brace_end = text.rfind("}")
        if brace_start != -1 and brace_end != -1 and brace_end > brace_start:
            candidates.append(text[brace_start:brace_end + 1])

    return sorted(candidates, key=len, reverse=True)


def _ensure_complete_scene_briefs(outline: dict) -> tuple[dict, dict]:
    return ensure_complete_scene_briefs(outline)


@router.get(
    "/{project_id}/status",
    summary="获取大纲状态",
    description="获取大纲的冻结状态和版本信息。",
)
async def get_outline_status(project_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    # JSON columns do not track in-place mutations.  Work on a fresh mapping
    # and explicitly flag the column so the freeze state is actually persisted
    # for the next request (and after an app restart).
    outline_data = dict(project.outline_data or {})
    from app.skills.outline_validation import chapter_sequence_diagnostics
    continuity = chapter_sequence_diagnostics(outline_data)
    frozen = bool(outline_data.get("_frozen", False))
    return {
        "frozen": frozen,
        "frozen_at": outline_data.get("_frozen_at") if frozen else None,
        "version": outline_data.get("_version", 1),
        "has_outline": bool(outline_data and ("chapters" in outline_data or "chapter_spine" in outline_data)),
        "has_draft": bool(project.draft_outline),
        "chapter_continuity": continuity,
        "generated_at": (outline_data.get("meta") or {}).get("generated_at"),
    }


@router.post(
    "/{project_id}/freeze",
    summary="冻结大纲",
    description="冻结大纲，使其变为只读。冻结后写作阶段的任何 Agent 无权修改大纲，只能查询。需要通过修订案机制才能修改。",
)
async def freeze_outline(project_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    outline_data = project.outline_data or {}
    if not outline_data.get("chapters") and not outline_data.get("chapter_spine"):
        raise HTTPException(status_code=400, detail="大纲为空，无法冻结")

    if outline_data.get("_frozen"):
        raise HTTPException(status_code=400, detail="大纲已经处于冻结状态")

    from app.skills.outline_validation import chapter_sequence_diagnostics
    continuity = chapter_sequence_diagnostics(outline_data)
    if not continuity["valid"]:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "大纲章节不连续，不能冻结。请先生成并确认连续草稿。",
                "chapter_continuity": continuity,
            },
        )

    from datetime import datetime, timezone
    outline_data["_frozen"] = True
    outline_data["_frozen_at"] = datetime.now(timezone.utc).isoformat()
    outline_data["_version"] = outline_data.get("_version", 1)

    project.outline_data = outline_data
    flag_modified(project, "outline_data")
    await db.commit()
    await db.refresh(project)

    return {
        "message": "大纲已冻结",
        "frozen": True,
        "frozen_at": outline_data["_frozen_at"],
        "version": outline_data["_version"],
    }


@router.post(
    "/{project_id}/unfreeze",
    summary="解冻大纲",
    description="解冻大纲，允许修改。这相当于提出修订案，应谨慎操作。",
)
async def unfreeze_outline(project_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    # See freeze_outline: assigning a copied mapping plus flag_modified is
    # required for SQLAlchemy's plain JSON column to persist this transition.
    outline_data = dict(project.outline_data or {})
    if not outline_data.get("_frozen"):
        raise HTTPException(status_code=400, detail="大纲未冻结")

    outline_data["_frozen"] = False
    outline_data.pop("_frozen_at", None)
    outline_data["_version"] = outline_data.get("_version", 1) + 1

    project.outline_data = outline_data
    flag_modified(project, "outline_data")
    await db.commit()
    await db.refresh(project)

    return {
        "message": "大纲已解冻",
        "frozen": False,
        "version": outline_data["_version"],
    }


@router.post(
    "/{project_id}/amendment",
    summary="创建修订案",
    description="在冻结大纲上创建修订案。系统会自动执行影响分析，评估修改对整体大纲的影响。修订案创建后状态为 pending，需要手动应用。",
)
async def create_amendment(project_id: uuid.UUID, data: dict, db: AsyncSession = Depends(get_db)):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    outline_data = project.outline_data or {}
    if not outline_data.get("_frozen"):
        raise HTTPException(status_code=400, detail="大纲未冻结，无需修订案")

    from app.skills.outline_validation import OutlineValidationSkill
    validator = OutlineValidationSkill()
    validation = validator.validate(outline_data, project.core_data)

    amendment = {
        "id": str(uuid.uuid4()),
        "description": data.get("description", ""),
        "changes": data.get("changes", {}),
        "impact_analysis": validation,
        "status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    amendments = outline_data.get("_amendments", [])
    amendments.append(amendment)
    outline_data["_amendments"] = amendments

    project.outline_data = outline_data
    await db.commit()
    await db.refresh(project)

    return amendment


@router.post(
    "/{project_id}/amendment/{amendment_id}/apply",
    summary="应用修订案",
    description="应用指定的修订案。应用后大纲自动解冻，版本号递增，修订案状态变为 applied。",
)
async def apply_amendment(project_id: uuid.UUID, amendment_id: str, db: AsyncSession = Depends(get_db)):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    outline_data = project.outline_data or {}
    amendments = outline_data.get("_amendments", [])

    target = None
    for a in amendments:
        if a.get("id") == amendment_id:
            target = a
            a["status"] = "applied"
            break

    if not target:
        raise HTTPException(status_code=404, detail="修订案不存在")

    outline_data["_frozen"] = False
    outline_data["_version"] = outline_data.get("_version", 1) + 1

    changes = target.get("changes", {})
    if changes:
        from app.services.story_plan_service import StoryPlanService
        sp_service = StoryPlanService()
        save_result = await sp_service.save_outline_data(project_id, changes, db, mode="patch")
        _raise_story_plan_error(save_result)
        outline_data = project.outline_data or {}
        outline_data["_frozen"] = False
        outline_data["_version"] = outline_data.get("_version", 1) + 1

    project.outline_data = outline_data
    await db.commit()
    await db.refresh(project)

    return {"message": "修订案已应用，大纲已解冻", "version": outline_data.get("_version", 1)}


@router.post(
    "/{project_id}/auto-generate",
    summary="自动生成全本大纲",
    description="基于项目信息和世界观设定生成全本大纲。多章节目标统一进入可恢复、可校验的规划工作流。",
)
async def auto_generate_outline(
    project_id: uuid.UUID,
    data: dict,
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    existing_outline = project.outline_data or {}
    if existing_outline.get("_frozen"):
        raise HTTPException(status_code=403, detail="大纲已冻结，无法修改。请先解冻大纲或通过修订案机制修改。")

    core_data = project.core_data or {}
    characters = core_data.get("characters", [])
    world_rules = core_data.get("world_rules", [])
    locations = core_data.get("locations", [])

    if "total_chapters" not in data:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "total_chapters_required",
                "message": "必须明确提供 total_chapters；系统不会默认为10章。",
            },
        )
    try:
        total_chapters = int(data.get("total_chapters"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="total_chapters 必须是正整数")
    if total_chapters < 1 or total_chapters > 2000:
        raise HTTPException(status_code=422, detail="total_chapters 必须在1-2000之间")

    # Every multi-chapter generation request uses the same server-owned target
    # contract.  Small plans may finish in one batch; larger plans continue in
    # several batches without changing the API semantics.
    if total_chapters > 1:
        from app.services.outline_expansion_service import OutlineExpansionService

        expansion = await OutlineExpansionService().start(
            project_id,
            total_chapters,
            db,
            batch_size=int(data.get("batch_size", 25) or 25),
            replace_existing=True,
            force_restart=bool(data.get("force_restart", False)),
            seed_context={
                "core_conflict": data.get("core_conflict", ""),
                "theme": data.get("theme", ""),
                "style_notes": data.get("style_notes", ""),
            },
        )
        if expansion.get("error"):
            _raise_story_plan_error(expansion)
        return {
            "status": "expansion_started",
            "message": (
                f"已启动 {total_chapters} 章规划任务；当前正式大纲会保持不变，"
                "全部批次通过校验后才会一次性替换。"
            ),
            "expansion": expansion,
        }
    genre = data.get("genre", project.genre or "未指定")
    theme = data.get("theme", "")
    core_conflict = data.get("core_conflict", "")
    style_notes = data.get("style_notes", "")

    system_prompt = (
        "你是「万象谱」大纲架构师，现在需要一次性生成完整的全本小说大纲。\n\n"
        "生成策略（分层生成）：\n"
        "1. **故事宪法**：确定核心梗、主题论证、读者承诺、主角核心（欲望/缺陷/需要/谎言）、结局方向\n"
        "2. **宏观结构**：确定三幕式/多幕式结构，规划起承转合\n"
        "3. **章节脊柱**：为每章确定标题、核心冲突四元组（渴望/障碍/行动/转折）、价值转变、钩子\n"
        "4. **线索计划**：规划主线/支线/伏笔线的埋设和回收\n"
        "5. **场景简报**：根据章节复杂度为每章动态设计 2-5 个场景，过渡章可精简，反转、高潮和多线汇合章必须增加场景密度，包含目标、冲突、结果\n\n"
        "关键原则：\n"
        "- 每章必须有明确的冲突四元组和价值转变\n"
        "- chapter_spine 必须从第1章开始连续编号，不得只输出关键节点或跳号章节\n"
        "- 如果无法一次输出目标章数，宁可明确失败，也不能用 1、4、7 这类稀疏节点冒充完整大纲\n"
        "- 伏笔要在大纲阶段就规划好埋设和揭示\n"
        "- 人物弧光要贯穿始终\n"
        "- 节奏要有张有弛，不能一直紧绷或一直平淡\n"
        "- 高潮前的铺垫要充分，高潮后的收束要干净\n\n"
        "输出严格的 JSON 格式：\n"
        "{\n"
        '  "story_constitution": {\n'
        '    "logline": "一句话核心梗",\n'
        '    "reader_promise": ["读者期待1", "读者期待2"],\n'
        '    "controlling_idea": "主题论证",\n'
        '    "ending_direction": "结局方向",\n'
        '    "protagonist_core": {"desire": "外在欲望", "need": "内在需要", "flaw": "核心缺陷", "lie": "错误信念"}\n'
        '  },\n'
        '  "macro_plan": {\n'
        '    "structure_model": "three_act",\n'
        '    "volumes": [{"volume_id": "v1", "name": "第一卷", "chapter_range": [1, N], "nodes": []}]\n'
        '  },\n'
        '  "chapter_spine": [\n'
        "    {\n"
        '      "chapter_number": 1,\n'
        '      "title": "章节标题",\n'
        '      "core_conflict": {"desire": "渴望", "obstacle": "障碍", "action": "行动", "turn": "转折"},\n'
        '      "conflict_text": "渴望，但障碍，于是行动，却转折",\n'
        '      "value_shift": {"axis": "价值轴", "from": "从", "to": "到"},\n'
        '      "pov_character": "视角角色",\n'
        '      "hook": "章节钩子",\n'
        '      "thread_ops": [{"thread_id": "thread_1", "op": "plant", "mode": "subtle"}]\n'
        "    }\n"
        "  ],\n"
        '  "thread_plan": {\n'
        '    "threads": [{"thread_id": "thread_1", "name": "线索名", "type": "main", "status": "planned", "plant_chapters": [1], "payoff_chapters": [10]}]\n'
        '  },\n'
        '  "scene_briefs": {\n'
        '    "1": {"chapter_id": "ch_001", "source": "generated", "scenes": [{"scene_id": "ch_001_s1", "type": "dialogue", "goal": "目标", "conflict": "冲突", "outcome": "结果", "info_release": "信息", "hook": "钩子"}]}\n'
        "  }\n"
        "}"
    )

    char_summary = "\n".join([
        f"- {c.get('name', '未命名')}: {c.get('personality', '')[:50]}（欲望: {c.get('desire', '')[:30]}）"
        for c in characters[:10]
    ]) if characters else "暂无人物设定"

    rule_summary = "\n".join([
        f"- {r.get('name', '')}: {r.get('description', '')[:50]}"
        for r in world_rules[:10]
    ]) if world_rules else "暂无世界规则"

    loc_summary = "\n".join([
        f"- {l.get('name', '')}: {l.get('atmosphere', '')[:30]}"
        for l in locations[:10]
    ]) if locations else "暂无地点设定"

    user_prompt = (
        f"项目：{project.name}\n"
        f"简介：{project.description or '暂无'}\n"
        f"题材：{genre}\n"
        f"目标章数：{total_chapters}\n"
        f"核心冲突：{core_conflict or '请根据设定推导'}\n"
        f"主题：{theme or '请根据设定推导'}\n"
        f"风格备注：{style_notes or '无特殊要求'}\n\n"
        f"人物设定：\n{char_summary}\n\n"
        f"世界规则：\n{rule_summary}\n\n"
        f"地点设定：\n{loc_summary}\n\n"
        f"请生成完整的 {total_chapters} 章全本大纲："
    )

    agent = OutlineArchitectAgent()
    result = await agent.execute({
        "messages": [{"role": "user", "content": user_prompt}],
        "project_info": {
            "name": project.name,
            "description": project.description or "",
            "genre": genre,
            "word_count_target": project.word_count_target,
        },
    })

    response_text = result.get("response", "")
    outline_json = _extract_json(response_text)

    if outline_json is None:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "outline_json_invalid",
                "message": "模型返回无法解析为 JSON；正式大纲没有变化。",
            },
        )

    outline_json, completion = _ensure_complete_scene_briefs(outline_json)
    outline_json.setdefault("meta", {})["generation_target_chapters"] = total_chapters
    from app.services.story_plan_service import StoryPlanService
    sp_service = StoryPlanService()
    save_result = await sp_service.save_outline_data(
        project_id,
        outline_json,
        db,
        mode="replace",
        expected_chapter_count=total_chapters,
    )
    _raise_story_plan_error(save_result)
    await db.commit()

    project = await db.get(Project, project_id)
    await db.refresh(project)

    from app.skills.outline_validation import OutlineValidationSkill
    validator = OutlineValidationSkill()
    validation = validator.validate(project.outline_data, project.core_data)

    return {
        "status": "saved",
        "message": f"全本大纲已生成并保存（{save_result.get('actual_chapter_count', total_chapters)}/{total_chapters}章）",
        "outline_data": project.outline_data,
        "validation": validation,
        "scene_completion": completion,
    }


class GuidedStepRequest(BaseModel):
    step: str
    answers: dict = {}
    supplement: str = ""


@router.post(
    "/{project_id}/guided/step",
    summary="宝宝巴士引导模式步骤",
    description="宝宝巴士引导模式的核心端点。根据当前步骤和已有回答，LLM参与分析并提供定制化的引导。",
)
async def guided_step(
    project_id: uuid.UUID,
    data: GuidedStepRequest,
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    agent = OutlineArchitectAgent()
    llm = await agent.get_llm_client()

    project_info = {
        "name": project.name,
        "description": project.description or "",
        "genre": project.genre or "",
        "word_count_target": project.word_count_target or 0,
    }

    step = data.step
    answers = data.answers or {}
    supplement = data.supplement or ""

    if step == "init":
        return await _guided_init(llm, project_info)
    elif step in GUIDED_TOPICS:
        return await _guided_question(llm, project_info, step, answers, supplement)
    elif step == "review":
        return await _guided_review(llm, project_info, answers)
    elif step == "generate":
        return await _guided_generate(llm, project_info, answers, project_id, db)
    else:
        raise HTTPException(status_code=400, detail=f"未知的引导步骤: {step}")


async def _guided_init(llm, project_info: dict) -> dict:
    system_prompt = (
        "你是「万象谱」大纲架构师，正在用引导模式帮助一位作者设计小说大纲。\n\n"
        "你的第一个任务是：读取项目信息，分析这个项目的核心要素，然后以友好的语气告诉作者你的初步发现，"
        "并询问作者是否有补充或调整。\n\n"
        "回复格式要求（严格 JSON）：\n"
        "```json\n"
        "{\n"
        '  "analysis": "你对项目的分析和初步构思（200-400字），包括你发现的亮点、可能的叙事方向、需要注意的风险点",\n'
        '  "core_themes": ["主题1", "主题2", "主题3"],\n'
        '  "question": "引导性提问，询问作者是否有补充"\n'
        "}\n"
        "```\n\n"
        "要求：\n"
        "- 分析要有深度，不要只复述项目信息\n"
        "- 指出的可能方向要具体，不要说泛泛的空话\n"
        "- 问题要友好、开放，鼓励作者说出自己的想法\n"
        "- 只输出 JSON，不要其他内容"
    )

    user_prompt = (
        f"项目名称：{project_info['name']}\n"
        f"项目简介：{project_info['description'] or '暂无'}\n"
        f"题材类型：{project_info['genre'] or '未指定'}\n"
        f"目标字数：{project_info['word_count_target'] or '未指定'}\n\n"
        "请分析这个项目，并给出你的初步构思。"
    )

    response = await llm.generate(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        temperature=0.8,
        task_type=LLMTaskType.JSON_DETECTION,
    )

    parsed = _extract_json(response)
    if not parsed:
        return {
            "step_type": "analysis",
            "content": response,
            "question": "基于以上分析，你有什么想补充或调整的吗？",
            "core_themes": [],
        }

    return {
        "step_type": "analysis",
        "content": parsed.get("analysis", ""),
        "core_themes": parsed.get("core_themes", []),
        "question": parsed.get("question", "你有什么想补充或调整的吗？"),
    }


async def _guided_question(
    llm, project_info: dict, topic: str, answers: dict, supplement: str
) -> dict:
    topic_name = GUIDED_TOPIC_NAMES.get(topic, topic)
    # Find the next unanswered topic's index
    topic_index = GUIDED_TOPICS.index(topic) if topic in GUIDED_TOPICS else 0

    system_prompt = (
        "你是「万象谱」大纲架构师，正在用引导模式帮助作者设计小说大纲。\n\n"
        f"你的当前任务是：围绕「{topic_name}」这个维度，结合项目信息和作者已有的回答，"
        "给出你的专业分析和建议，然后提出一个针对性问题，并提供 3-5 个选项供作者选择。\n\n"
        "回复格式要求（严格 JSON）：\n"
        "```json\n"
        "{\n"
        '  "thinking": "你关于这个维度的专业分析（100-200字），要结合项目特点和已有回答，不要泛泛而谈",\n'
        '  "question": "围绕{topic_name}的引导性问题",\n'
        '  "options": [\n'
        '    {"label": "选项显示名", "value": "选项值", "recommended": true/false, "reasoning": "推荐理由（一句话）"}\n'
        "  ]\n"
        "}\n"
        "```\n\n"
        "要求：\n"
        "- thinking 要体现你的专业判断，结合项目的题材、简介、目标字数来分析\n"
        "- 选项应该是你根据项目特点「定制」的，而不是通用模板\n"
        "- 至少标记一个 recommended: true 的推荐选项\n"
        "- 每个选项的 reasoning 说明为什么这个选项适合或不适合当前项目\n"
        "- 只输出 JSON，不要其他内容"
    )

    answered_summary = "\n".join([
        f"- {GUIDED_TOPIC_NAMES.get(k, k)}: {v}"
        for k, v in answers.items()
    ]) if answers else "暂无"

    user_prompt = (
        f"项目：{project_info['name']}\n"
        f"简介：{project_info['description'] or '暂无'}\n"
        f"题材：{project_info['genre'] or '未指定'}\n"
        f"目标字数：{project_info['word_count_target'] or '未指定'}\n"
        f"作者补充：{supplement or '无'}\n\n"
        f"作者已回答的问题：\n{answered_summary}\n\n"
        f"请针对「{topic_name}」这个维度进行分析和提问。"
    )

    response = await llm.generate(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        temperature=0.8,
        task_type=LLMTaskType.JSON_DETECTION,
    )

    parsed = _extract_json(response)
    if not parsed:
        return {
            "step_type": "question",
            "content": f"（LLM 响应解析失败，以下是原始回复）\n\n{response}",
            "question": f"关于{topic_name}，你有什么想法？",
            "options": [],
        }

    return {
        "step_type": "question",
        "content": parsed.get("thinking", ""),
        "question": parsed.get("question", ""),
        "options": parsed.get("options", []),
    }


async def _guided_review(llm, project_info: dict, answers: dict) -> dict:
    answered_summary = "\n".join([
        f"- {GUIDED_TOPIC_NAMES.get(k, k)}: {v}"
        for k, v in answers.items()
    ])

    system_prompt = (
        "你是「万象谱」大纲架构师，正在用引导模式帮助作者设计小说大纲。\n\n"
        "你的当前任务是：审阅作者已经回答的所有问题，从专业角度指出：\n"
        "1. 有哪些潜在矛盾或需要澄清的地方\n"
        "2. 有哪些重要的伏笔或设定还没有被提出来\n"
        "3. 基于整体构思，提出 2-4 个补充问题\n\n"
        "回复格式要求（严格 JSON）：\n"
        "```json\n"
        "{\n"
        '  "review": "你的审阅分析（150-300字），要点式列出你发现的问题和建议",\n'
        '  "followup_questions": [\n'
        '    {"question": "问题1", "placeholder": "输入提示"},\n'
        '    {"question": "问题2", "placeholder": "输入提示"}\n'
        "  ]\n"
        "}\n"
        "```\n\n"
        "要求：\n"
        "- 审阅要有洞察力，不能只说「很好没问题」\n"
        "- 补充问题要真正能够帮助完善大纲，不是走形式\n"
        "- 只输出 JSON，不要其他内容"
    )

    user_prompt = (
        f"项目：{project_info['name']}\n"
        f"简介：{project_info['description'] or '暂无'}\n"
        f"题材：{project_info['genre'] or '未指定'}\n"
        f"目标字数：{project_info['word_count_target'] or '未指定'}\n\n"
        f"作者的回答汇总：\n{answered_summary}\n\n"
        "请审阅以上内容，找出问题并提问。"
    )

    response = await llm.generate(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        temperature=0.8,
        task_type=LLMTaskType.JSON_DETECTION,
    )

    parsed = _extract_json(response)
    if not parsed:
        return {
            "step_type": "followup",
            "content": response,
            "followup_questions": [],
        }

    return {
        "step_type": "followup",
        "content": parsed.get("review", ""),
        "followup_questions": parsed.get("followup_questions", []),
    }


async def _guided_generate(llm, project_info: dict, answers: dict, project_id: uuid.UUID, db: AsyncSession) -> dict:
    answered_summary = "\n".join([
        f"- {GUIDED_TOPIC_NAMES.get(k, k)}: {v}"
        for k, v in answers.items()
    ])

    raw_chapters = answers.get("chapter_count", "10")
    try:
        total_chapters = int(re.search(r'\d+', str(raw_chapters)).group()) if re.search(r'\d+', str(raw_chapters)) else 10
    except (ValueError, AttributeError):
        total_chapters = 10
    total_chapters = max(1, total_chapters)
    requested_chapters = total_chapters
    generated_chapters = min(total_chapters, MAX_GUIDED_CHAPTERS_PER_BATCH)
    # Guided mode follows the same contract as free chat and auto-generation:
    # any request for multiple chapters is a server-managed planning job.
    staged_generation = requested_chapters > 1
    batch_note = ""
    if staged_generation:
        batch_note = (
            f"全书目标为 {requested_chapters} 章，本次先生成第 1-{generated_chapters} 章的连续首批；"
            "后续章节必须在首批确认后按连续编号继续追加。"
        )
    if staged_generation:
        # Guided mode used to make one 30-chapter draft and then stop, leaving
        # the author with no way to reach the declared target.  Hand the same
        # answers to the durable expansion workflow instead.
        from app.services.outline_expansion_service import OutlineExpansionService

        expansion = await OutlineExpansionService().start(
            project_id,
            requested_chapters,
            db,
            batch_size=min(MAX_GUIDED_CHAPTERS_PER_BATCH, 25),
            replace_existing=True,
            seed_context={
                "answers": copy.deepcopy(answers),
                "project_info": copy.deepcopy(project_info),
            },
        )
        if expansion.get("error"):
            _raise_story_plan_error(expansion)
        return {
            "step_type": "generate",
            "outline_text": "",
            "outline_json": None,
            "saved": False,
            "draft_saved": True,
            "recovered": False,
            "recovery_note": (
                f"已启动 {requested_chapters} 章规划任务；系统会自动选择安全批次，"
                "全部完成并校验后才会替换正式大纲。"
            ),
            "staged": True,
            "requested_chapters": requested_chapters,
            "generated_chapters": 0,
            "expansion": expansion,
        }
    genre = project_info.get("genre", "未指定")

    system_prompt = (
        "你是「万象谱」大纲架构师。现在根据作者在引导模式下的所有回答，生成完整的小说大纲。\n\n"
        "生成策略（分层生成）：\n"
        "1. **故事宪法**：确定核心梗、主题论证、读者承诺、主角核心（欲望/缺陷/需要/谎言）、结局方向\n"
        "2. **宏观结构**：确定叙事结构、高潮位置、转折点\n"
        "3. **章节脊柱**：每章有明确的标题、核心冲突四元组（渴望/障碍/行动/转折）、价值转变、钩子\n"
        "4. **线索计划**：规划主线/支线/伏笔线的埋设和回收\n"
        "5. **场景简报**：根据章节复杂度动态分配 2-5 个场景，避免所有章节统一使用最低数量；每个场景包含目标、冲突、结果、信息释放、悬念钩子\n\n"
        "关键原则：\n"
        "- 每章必须有冲突四元组和价值转变\n"
        "- chapter_spine 必须从第1章开始连续编号，不得只输出关键节点或跳号章节\n"
        "- 如果无法一次输出目标章数，宁可明确失败，也不能用 1、4、7 这类稀疏节点冒充完整大纲\n"
        "- 节奏有张有弛\n"
        "- 伏笔在大纲阶段就规划好\n"
        "- 人物弧光贯穿始终\n\n"
        "输出严格的 JSON 格式：\n"
        "```json\n"
        "{\n"
        '  "story_constitution": {\n'
        '    "logline": "一句话核心梗",\n'
        '    "reader_promise": ["读者期待1", "读者期待2"],\n'
        '    "controlling_idea": "主题论证",\n'
        '    "ending_direction": "结局方向",\n'
        '    "protagonist_core": {"desire": "外在欲望", "need": "内在需要", "flaw": "核心缺陷", "lie": "错误信念"}\n'
        '  },\n'
        '  "chapter_spine": [\n'
        "    {\n"
        '      "chapter_number": 1,\n'
        '      "title": "章节标题",\n'
        '      "core_conflict": {"desire": "渴望", "obstacle": "障碍", "action": "行动", "turn": "转折"},\n'
        '      "conflict_text": "渴望，但障碍，于是行动，却转折",\n'
        '      "value_shift": {"axis": "价值轴", "from": "从", "to": "到"},\n'
        '      "pov_character": "视角角色",\n'
        '      "hook": "章节钩子",\n'
        '      "thread_ops": [{"thread_id": "thread_1", "op": "plant", "mode": "subtle"}]\n'
        "    }\n"
        "  ],\n"
        '  "thread_plan": {\n'
        '    "threads": [{"thread_id": "thread_1", "name": "线索名", "type": "main", "status": "planned", "plant_chapters": [1], "payoff_chapters": [10]}]\n'
        '  },\n'
        '  "scene_briefs": {\n'
        '    "1": {"chapter_id": "ch_001", "source": "generated", "scenes": [{"scene_id": "ch_001_s1", "type": "dialogue", "goal": "目标", "conflict": "冲突", "outcome": "结果", "info_release": "信息", "hook": "钩子"}]}\n'
        "  }\n"
        "}\n"
        "```\n"
        "只输出 JSON，不要其他内容。"
    )

    user_prompt = (
        f"项目：{project_info['name']}\n"
        f"简介：{project_info['description'] or '暂无'}\n"
        f"题材：{genre}\n"
        f"全书目标章节数：{requested_chapters}\n"
        f"本次生成范围：第 1-{generated_chapters} 章（单批上限 {MAX_GUIDED_CHAPTERS_PER_BATCH} 章）\n"
        f"目标字数：{project_info.get('word_count_target', '未指定')}\n\n"
        f"作者的回答：\n{answered_summary}\n\n"
        f"请只生成第 1-{generated_chapters} 章的逐章大纲；不得跳号、不得只输出关键节点。"
    )

    response = await llm.generate(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        temperature=0.8,
        task_type=LLMTaskType.OUTLINE_GENERATION,
    )

    recovered = False
    recovery_note = batch_note
    outline_json = _extract_json(response)
    if outline_json is None:
        outline_json = await _coerce_outline_json(llm, response, total_chapters=generated_chapters)
        if outline_json is not None:
            recovered = True
            recovery_note = f"{batch_note} 生成结果不是严格 JSON，已通过二次转换恢复为结构化草稿。".strip()
    if outline_json is None:
        outline_json = _build_fallback_story_plan(
            project_info=project_info,
            answers=answers,
            outline_text=response,
            total_chapters=generated_chapters,
        )
        recovered = True
        recovery_note = (
            f"{batch_note} 生成结果无法解析为 JSON，已根据宝宝巴士问答自动构造一份可编辑的降级草稿。"
        ).strip()

    if outline_json is not None:
        from app.skills.outline_validation import chapter_sequence_diagnostics
        continuity = chapter_sequence_diagnostics(outline_json)
        if not continuity["valid"]:
            missing = continuity.get("missing", [])
            preview = ", ".join(str(number) for number in missing[:12])
            suffix = "等" if len(missing) > 12 else ""
            recovery_note = (
                f"生成结果未保存：章节号不连续，缺少第{preview}章{suffix}。"
                "系统不会把稀疏节点冒充完整大纲，请降低本次生成范围后重试。"
            )
            return {
                "step_type": "generate",
                "outline_text": response,
                "outline_json": outline_json,
                "saved": False,
                "draft_saved": False,
                "recovered": True,
                "recovery_note": recovery_note,
                "staged": staged_generation,
                "requested_chapters": requested_chapters,
                "generated_chapters": generated_chapters,
                "validation": {
                    "valid": False,
                    "issues": [{
                        "type": "chapter_gap",
                        "severity": "error",
                        "message": recovery_note,
                    }],
                    "chapter_continuity": continuity,
                },
            }
        outline_json, completion = _ensure_complete_scene_briefs(outline_json)
        generation_meta = outline_json.setdefault("meta", {})
        generation_meta["generated_at"] = datetime.now(timezone.utc).isoformat()
        generation_meta["generation_target_chapters"] = requested_chapters
        generation_meta["generation_batch_range"] = [1, generated_chapters]
        if completion["added_scenes"]:
            recovered = True
            completion_note = (
                f"检测到模型省略了部分场景简报，系统已为 "
                f"{len(completion['completed_chapters'])} 个章节补齐 "
                f"{completion['added_scenes']} 个可编辑基础场景。"
            )
            recovery_note = f"{recovery_note} {completion_note}".strip()
        if completion.get("under_recommended_chapters"):
            density_note = (
                f"场景密度检查：其中 "
                f"{len(completion['under_recommended_chapters'])} 个章节低于建议场景数，"
                "已保留为可编辑草稿。"
            )
            recovery_note = f"{recovery_note} {density_note}".strip()
        project = await db.get(Project, project_id)
        if project:
            existing_outline = project.outline_data or {}
            if not existing_outline.get("_frozen"):
                project.draft_outline = copy.deepcopy(outline_json)
                flag_modified(project, "draft_outline")
                await db.commit()

                await db.refresh(project)

                from app.skills.outline_validation import OutlineValidationSkill
                validator = OutlineValidationSkill()
                validation = validator.validate(outline_json, project.core_data)

                return {
                    "step_type": "generate",
                    "outline_text": response,
                    "outline_json": outline_json,
                    "saved": False,
                    "draft_saved": True,
                    "recovered": recovered,
                    "recovery_note": recovery_note,
                    "staged": staged_generation,
                    "requested_chapters": requested_chapters,
                    "generated_chapters": generated_chapters,
                    "validation": validation,
                }

    return {
        "step_type": "generate",
        "outline_text": response,
        "outline_json": outline_json,
        "saved": False,
        "draft_saved": False,
        "recovered": recovered,
        "recovery_note": recovery_note,
        "staged": staged_generation,
        "requested_chapters": requested_chapters,
        "generated_chapters": generated_chapters,
    }


async def _coerce_outline_json(llm, outline_text: str, total_chapters: int | None = None) -> dict | None:
    if not outline_text or not outline_text.strip():
        return None

    chapter_hint = total_chapters or "按原文"
    system_prompt = (
        "你是小说大纲数据清洗器。你的任务是把输入的大纲文本转换为严格 JSON。"
        "输入可能是自然语言、Markdown、半截 JSON、带解释文字的 JSON，或字段名不完全一致的旧格式。"
        "你必须只输出一个 JSON 对象，不要 Markdown，不要代码块，不要解释。"
        "如果原文缺少某些字段，请根据上下文补齐简短可用值。"
        "JSON 顶层只允许这些字段：story_constitution, macro_plan, arc_plan, thread_plan, chapter_spine, scene_briefs。"
        "chapter_spine 必须是数组，每章至少包含 chapter_number, title, core_conflict, conflict_text, value_shift, pov_character, hook, thread_ops。"
        "chapter_spine 的 chapter_number 必须从1开始连续递增，不得保留稀疏关键节点。"
        "core_conflict 使用 {desire, obstacle, action, turn}。"
        "value_shift 使用 {axis, from, to}。"
        "scene_briefs 使用对象，key 可用章节号字符串，每项包含 chapter_id, source, scenes。"
        "scenes 每项至少包含 scene_id, goal, conflict, outcome, info_release, hook。"
    )
    user_prompt = (
        f"目标章数：{chapter_hint}\n\n"
        "请把下面的大纲转换为严格 JSON：\n\n"
        f"{outline_text[:50000]}"
    )

    try:
        repaired = await llm.generate(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.1,
            task_type=LLMTaskType.OUTLINE_GENERATION,
        )
    except Exception as e:
        logger.warning(f"[OutlineAPI] outline JSON coercion failed: {e}")
        return None

    parsed = _extract_json(repaired)
    if parsed is None:
        logger.warning("[OutlineAPI] outline JSON coercion returned non-JSON text")
    return parsed


def _build_fallback_story_plan(
    project_info: dict | None = None,
    answers: dict | None = None,
    outline_text: str = "",
    total_chapters: int | None = None,
) -> dict:
    project_info = project_info or {}
    answers = answers or {}
    raw_text = (outline_text or "").strip()

    chapter_count = total_chapters or _infer_chapter_count(raw_text) or _infer_chapter_count(str(answers)) or 10
    chapter_count = max(1, min(int(chapter_count), 200))

    project_name = project_info.get("name") or "未命名项目"
    genre = project_info.get("genre") or answers.get("genre") or "未指定题材"
    theme = answers.get("core_theme") or project_info.get("description") or raw_text[:120] or "待细化的核心主题"
    protagonist = answers.get("protagonist_type") or "待细化主角"
    conflict_style = answers.get("conflict_style") or "待细化冲突"
    antagonist = answers.get("antagonist_design") or "待细化对手"
    emotional_tone = answers.get("emotional_tone") or "待细化情绪基调"
    special = answers.get("special_requirements") or ""

    chapter_spine = []
    scene_briefs = {}
    for idx in range(1, chapter_count + 1):
        chapter_id = f"ch_{idx:03d}"
        phase = _fallback_phase(idx, chapter_count)
        title = f"{phase} {idx}"
        conflict_text = f"{protagonist}围绕「{theme}」推进目标，但受到「{antagonist}」与「{conflict_style}」的阻碍。"
        chapter_spine.append({
            "chapter_id": chapter_id,
            "chapter_number": idx,
            "title": title,
            "summary": f"{phase}阶段章节。该章需要依据原始生成文本继续细化。",
            "core_conflict": {
                "desire": f"推进「{theme}」相关目标",
                "obstacle": f"{antagonist}与{conflict_style}",
                "action": "主角采取新的选择并推动局势变化",
                "turn": "局势出现新的代价或线索",
            },
            "conflict_text": conflict_text,
            "value_shift": {
                "axis": "主动性",
                "from": "受局势推动",
                "to": "主动改变局面",
            },
            "pov_character": protagonist,
            "hook": "新的问题或线索迫使下一章继续推进。",
            "thread_ops": [{
                "thread_id": "main_thread",
                "op": "plant" if idx == 1 else "develop" if idx < chapter_count else "payoff",
                "mode": "subtle",
            }],
            "state_effects_expected": [],
            "depends_on": [],
            "must_not": [],
            "status": "draft",
        })
        scene_briefs[str(idx)] = {
            "chapter_id": chapter_id,
            "version": 1,
            "source": "fallback",
            "expires_when": "chapter_spine_changed",
            "expires_scope": "current",
            "scenes": [{
                "scene_id": f"{chapter_id}_s1",
                "type": "planning",
                "goal": f"完成第 {idx} 章的阶段目标",
                "conflict": conflict_text,
                "outcome": "形成下一步推进条件",
                "info_release": "从原始生成文本中提炼关键信息后补充",
                "hook": "留下下一章推进点",
                "required_context_refs": [],
            }],
        }

    return {
        "story_constitution": {
            "logline": f"《{project_name}》是一部{genre}故事，核心围绕「{theme}」。",
            "reader_promise": [emotional_tone, conflict_style],
            "controlling_idea": theme,
            "ending_direction": "结局方向待根据草稿继续细化",
            "protagonist_core": {
                "desire": protagonist,
                "need": "在冲突中完成关键成长",
                "flaw": "待细化的核心缺陷",
                "lie": "待细化的错误信念",
            },
            "notes": special,
        },
        "macro_plan": {
            "structure_model": "guided_fallback",
            "volumes": [{
                "volume_id": "v1",
                "name": "第一卷",
                "chapter_range": [1, chapter_count],
                "nodes": [],
            }],
        },
        "arc_plan": {
            "arcs": [{
                "character_id": "protagonist",
                "character_name": protagonist,
                "start_state": "被动卷入",
                "end_state": "主动选择",
                "turning_points": [1, max(1, chapter_count // 2), chapter_count],
            }],
        },
        "thread_plan": {
            "threads": [{
                "thread_id": "main_thread",
                "name": "主线",
                "type": "main",
                "status": "planned",
                "plant_chapters": [1],
                "payoff_chapters": [chapter_count],
            }],
        },
        "chapter_spine": chapter_spine,
        "scene_briefs": scene_briefs,
        "generation_corridors": {
            "source": "fallback_from_guided_mode",
            "raw_generation_excerpt": raw_text[:3000],
            "warning": "原始生成结果未能解析为严格 JSON，本草稿为系统自动构造的可编辑兜底版本。",
        },
        "meta": {
            "version": 1,
            "draft_active": True,
            "fallback": True,
        },
    }


def _infer_chapter_count(text: str) -> int | None:
    if not text:
        return None
    nums = [int(n) for n in re.findall(r"(?:第\s*)?(\d{1,3})\s*章", text)]
    if nums:
        return max(nums)
    match = re.search(r"(\d{1,3})\s*章", text)
    if match:
        return int(match.group(1))
    return None


def _fallback_phase(idx: int, total: int) -> str:
    if total <= 1:
        return "核心章"
    ratio = idx / total
    if ratio <= 0.25:
        return "开端"
    if ratio <= 0.5:
        return "发展"
    if ratio <= 0.75:
        return "转折"
    return "收束"


@router.post(
    "/{project_id}/draft",
    summary="保存草案大纲",
    description="将大纲保存为草案，不影响正式大纲数据。草案可预览、对比，确认后才会生效。",
)
async def save_draft_outline(
    project_id: uuid.UUID,
    data: dict,
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    outline_json, completion = _ensure_complete_scene_briefs(data)
    from app.skills.outline_validation import chapter_sequence_diagnostics
    continuity = chapter_sequence_diagnostics(outline_json)
    if not continuity["valid"]:
        _raise_story_plan_error({
            "error": "草案章节必须从第1章开始连续规划，不能只保存稀疏节点。",
            "status_code": 422,
            "chapter_continuity": continuity,
        })
    project.draft_outline = copy.deepcopy(outline_json)
    flag_modified(project, "draft_outline")
    await db.commit()
    await db.refresh(project)

    return {
        "message": "草案已保存，正式大纲未受影响",
        "draft_saved": True,
        "scene_completion": completion,
    }


@router.post(
    "/{project_id}/draft/recover-continuity",
    summary="从稀疏锚点生成连续草稿",
    description="保留正式大纲不变，把现有章节视为锚点，为缺失章节创建明确标记的待审阅过渡章，并仅保存到草稿区。",
)
async def recover_outline_continuity_draft(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    outline_data = copy.deepcopy(project.outline_data or {})
    if not outline_data:
        raise HTTPException(status_code=400, detail="当前没有可恢复的正式大纲")
    if outline_data.get("_frozen"):
        raise HTTPException(status_code=403, detail="大纲已冻结，请先解冻后再生成恢复草稿")
    if project.draft_outline:
        raise HTTPException(status_code=409, detail="已有待确认草稿，请先查看、确认或丢弃现有草稿")

    from app.services.outline_continuity_recovery_service import (
        OutlineContinuityRecoveryError,
        OutlineContinuityRecoveryService,
    )

    service = OutlineContinuityRecoveryService()
    try:
        draft, recovery = service.build_draft(outline_data)
    except OutlineContinuityRecoveryError as exc:
        status_code = 409 if exc.code == "already_contiguous" else 422
        raise HTTPException(
            status_code=status_code,
            detail={
                "message": str(exc),
                "code": exc.code,
                "chapter_continuity": exc.diagnostics,
            },
        ) from exc

    project.draft_outline = copy.deepcopy(draft)
    flag_modified(project, "draft_outline")
    await db.commit()
    await db.refresh(project)

    return {
        "message": (
            f"已保留 {recovery['anchor_count']} 个原有锚点，"
            f"补齐 {recovery['added_count']} 个待审阅章节；正式大纲未发生变化。"
        ),
        "draft_saved": True,
        "recovery": recovery,
    }


class DraftParseRequest(BaseModel):
    outline_text: str


@router.post(
    "/{project_id}/draft/parse",
    summary="解析文本并保存为草稿大纲",
)
async def parse_draft_outline(
    project_id: uuid.UUID,
    data: DraftParseRequest,
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    recovered = False
    recovery_note = ""
    outline_json = _extract_json(data.outline_text)
    if outline_json is None:
        agent = OutlineArchitectAgent()
        llm = await agent.get_llm_client()
        outline_json = await _coerce_outline_json(llm, data.outline_text)
        if outline_json is not None:
            recovered = True
            recovery_note = "生成结果不是严格 JSON，已通过二次转换恢复为结构化草稿。"

    if outline_json is None:
        outline_json = _build_fallback_story_plan(
            project_info={
                "name": project.name,
                "description": project.description or "",
                "genre": project.genre or "",
                "word_count_target": project.word_count_target or 0,
            },
            outline_text=data.outline_text,
        )
        recovered = True
        recovery_note = "生成结果无法解析为 JSON，已自动构造一份可编辑的降级草稿。"

    outline_json, completion = _ensure_complete_scene_briefs(outline_json)
    if completion["added_scenes"]:
        recovered = True
        completion_note = (
            f"检测到部分章节缺少场景简报，系统已为 "
            f"{len(completion['completed_chapters'])} 个章节补齐 "
            f"{completion['added_scenes']} 个可编辑基础场景。"
        )
        recovery_note = f"{recovery_note} {completion_note}".strip()

    from app.skills.outline_validation import chapter_sequence_diagnostics
    continuity = chapter_sequence_diagnostics(outline_json)
    if not continuity["valid"]:
        _raise_story_plan_error({
            "error": "草案章节必须从第1章开始连续规划，不能只保存稀疏节点。",
            "status_code": 422,
            "chapter_continuity": continuity,
        })

    existing_outline = project.outline_data or {}
    if existing_outline.get("_frozen"):
        raise HTTPException(status_code=403, detail="大纲已冻结，无法保存草稿")

    project.draft_outline = copy.deepcopy(outline_json)
    flag_modified(project, "draft_outline")
    await db.commit()
    await db.refresh(project)

    return {
        "message": "草稿已保存，正式大纲未受影响",
        "draft_saved": True,
        "outline_data": outline_json,
        "recovered": recovered,
        "recovery_note": recovery_note,
        "scene_completion": completion,
    }


@router.get(
    "/{project_id}/draft",
    summary="读取草案大纲",
    description="读取当前草案大纲。返回草案内容以及与正式大纲的差异摘要。",
)
async def read_draft_outline(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    draft = copy.deepcopy(project.draft_outline or {})
    if not draft:
        return {"has_draft": False, "draft": None}
    draft, completion = _ensure_complete_scene_briefs(draft)
    if completion["added_scenes"]:
        project.draft_outline = copy.deepcopy(draft)
        flag_modified(project, "draft_outline")
        await db.commit()
        await db.refresh(project)

    from app.services.outline_memory_service import OutlineMemoryService
    memory_service = OutlineMemoryService()
    old_outline = project.outline_data or {}
    diff = memory_service._diff_outlines(old_outline, draft)

    return {
        "has_draft": True,
        "draft": draft,
        "scene_completion": completion,
        "diff_summary": {
            "total_changes": len(diff),
            "change_types": list({c["change_type"] for c in diff}),
            "changes": diff[:10],
        },
    }


@router.post(
    "/{project_id}/draft/confirm",
    summary="确认草案",
    description="将草案确认为正式大纲。确认后草案清空，正式大纲更新，变更记录自动生成。",
)
async def confirm_draft_outline(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    draft, completion = _ensure_complete_scene_briefs(project.draft_outline or {})
    if not draft:
        raise HTTPException(status_code=400, detail="没有待确认的草案")

    existing_outline = project.outline_data or {}
    if existing_outline.get("_frozen"):
        raise HTTPException(status_code=403, detail="大纲已冻结，无法确认草案")

    old_outline = dict(existing_outline)

    from app.services.story_plan_service import StoryPlanService
    sp_service = StoryPlanService()
    save_result = await sp_service.save_outline_data(project_id, draft, db, mode="replace")
    _raise_story_plan_error(save_result)
    project.draft_outline = {}
    flag_modified(project, "draft_outline")
    await db.flush()

    outline_after_save = project.outline_data or {}
    if not (
        outline_after_save.get("chapters")
        or outline_after_save.get("chapter_spine")
        or outline_after_save.get("story_constitution")
    ):
        raise HTTPException(status_code=500, detail="草稿确认失败：正式大纲未成功写入")

    await db.commit()
    await db.refresh(project)

    from app.services.outline_index_service import OutlineIndexService
    index_service = OutlineIndexService()
    await index_service.generate_and_save_index(project_id, db)

    from app.services.outline_memory_service import OutlineMemoryService
    memory_service = OutlineMemoryService()
    change_records = []
    if old_outline.get("chapters") or project.outline_data.get("chapters"):
        try:
            change_records = await memory_service.auto_record_outline_changes(
                project_id, old_outline, project.outline_data, db
            )
        except Exception as e:
            logger.warning(f"[OutlineAPI] auto_record_changes failed: {e}")

    from app.skills.outline_validation import OutlineValidationSkill
    validator = OutlineValidationSkill()
    validation = validator.validate(draft, project.core_data)

    try:
        from app.services.outline_embedding_service import OutlineEmbeddingService
        from app.db.db_models import PGVECTOR_AVAILABLE
        if PGVECTOR_AVAILABLE:
            emb_service = OutlineEmbeddingService()
            await emb_service.generate_embeddings_for_project(project_id, db)
    except Exception as e:
        logger.warning(f"[OutlineAPI] embedding generation failed: {e}")

    return {
        "message": "草案已确认为正式大纲",
        "outline_data": project.outline_data,
        "validation": validation,
        "change_records": len(change_records),
        "scene_completion": completion,
    }


@router.post(
    "/{project_id}/draft/discard",
    summary="丢弃草案",
    description="丢弃当前草案，正式大纲不受影响。",
)
async def discard_draft_outline(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    expansion_job = ((project.draft_outline or {}).get("meta") or {}).get("expansion_job")
    if isinstance(expansion_job, dict) and expansion_job.get("status") in {
        "queued", "running", "completing", "paused", "failed",
    }:
        from app.services.outline_expansion_service import OutlineExpansionService

        await OutlineExpansionService().cancel(project_id, db)
    project.draft_outline = {}
    flag_modified(project, "draft_outline")
    await db.commit()

    return {"message": "草案已丢弃"}


@router.delete(
    "/{project_id}",
    summary="删除大纲",
    description="清空项目的正式大纲、草案和索引，并删除相关向量缓存。",
)
async def delete_outline(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    expansion_job = ((project.draft_outline or {}).get("meta") or {}).get("expansion_job")
    if isinstance(expansion_job, dict) and expansion_job.get("status") in {
        "queued", "running", "completing", "paused", "failed",
    }:
        from app.services.outline_expansion_service import OutlineExpansionService

        await OutlineExpansionService().cancel(project_id, db)

    outline_data = project.outline_data or {}
    has_outline = bool(
        outline_data.get("chapters")
        or outline_data.get("chapter_spine")
        or outline_data.get("story_constitution")
        or outline_data.get("macro_plan")
        or outline_data.get("arc_plan")
        or outline_data.get("thread_plan")
        or outline_data.get("scene_briefs")
    )
    has_draft = bool(project.draft_outline)
    has_index = bool(project.outline_index)

    if not has_outline and not has_draft and not has_index:
        return {
            "message": "当前没有可删除的大纲",
            "deleted": False,
        }

    project.outline_data = {}
    project.draft_outline = {}
    project.outline_index = {}
    project.outline_version = 0

    await db.commit()
    await db.refresh(project)

    try:
        from app.services.narrative_sync_service import NarrativeSyncService

        sync_service = NarrativeSyncService()
        await sync_service.sync_outline_delete(
            project_id,
            db,
            old_outline=outline_data,
            source_system="outline",
            publish_event=True,
        )
    except Exception as e:
        logger.warning(f"[OutlineAPI] narrative delete sync failed: {e}")

    try:
        from app.db.db_models import PGVECTOR_AVAILABLE
        if PGVECTOR_AVAILABLE:
            from app.services.outline_embedding_service import OutlineEmbeddingService

            emb_service = OutlineEmbeddingService()
            await emb_service.delete_project_embeddings(project_id, db)
    except Exception as e:
        logger.warning(f"[OutlineAPI] delete outline embeddings failed: {e}")

    return {
        "message": "大纲已删除",
        "deleted": True,
    }


# ── Story Plan API ──────────────────────────────────────


@router.get(
    "/{project_id}/story-plan",
    summary="获取完整 Story Plan",
)
async def get_story_plan(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    from app.services.story_plan_service import StoryPlanService
    service = StoryPlanService()
    result = await service.get_story_plan(project_id, db)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result

@router.get(
    "/{project_id}/story-plan/chapter-blueprint/{chapter_number}",
    summary="获取统一的本章蓝图",
)
async def get_chapter_blueprint(
    project_id: uuid.UUID,
    chapter_number: int,
    db: AsyncSession = Depends(get_db),
):
    from app.services.story_plan_service import StoryPlanService

    result = await StoryPlanService().get_chapter_blueprint(
        project_id, chapter_number, db,
    )
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.put(
    "/{project_id}/story-plan/chapter-blueprint/{chapter_number}",
    summary="原子保存统一的本章蓝图",
)
async def save_chapter_blueprint(
    project_id: uuid.UUID,
    chapter_number: int,
    req: SaveChapterBlueprintRequest,
    db: AsyncSession = Depends(get_db),
):
    from app.services.story_plan_service import StoryPlanService

    result = await StoryPlanService().save_chapter_blueprint(
        project_id=project_id,
        chapter_number=chapter_number,
        chapter_updates=req.chapter,
        scenes=req.scenes,
        expected_revision=req.expected_revision,
        confirm_recovery=req.confirm_recovery,
        db=db,
    )
    if "error" in result:
        raise HTTPException(
            status_code=int(result.get("status_code", 400)),
            detail={
                "message": result["error"],
                "current_revision": result.get("current_revision"),
                "chapter_continuity": result.get("chapter_continuity"),
            },
        )
    await db.commit()
    return result


@router.get(
    "/{project_id}/story-plan/layer/{layer_name}",
    summary="获取 Story Plan 单层数据",
)
async def get_story_plan_layer(
    project_id: uuid.UUID,
    layer_name: str,
    db: AsyncSession = Depends(get_db),
):
    from app.services.story_plan_service import StoryPlanService
    service = StoryPlanService()
    plan = await service.get_story_plan(project_id, db)
    if "error" in plan:
        raise HTTPException(status_code=404, detail=plan["error"])
    if layer_name not in service.SUPPORTED_LAYERS or layer_name not in plan:
        raise HTTPException(status_code=404, detail=f"层级 {layer_name} 不存在")
    return {"layer": layer_name, "data": plan.get(layer_name)}
@router.put(
    "/{project_id}/story-plan/layer/{layer_name}",
    summary="更新 Story Plan 单层数据",
)
async def update_story_plan_layer(
    project_id: uuid.UUID,
    layer_name: str,
    req: UpdateLayerRequest,
    db: AsyncSession = Depends(get_db),
):
    from app.services.story_plan_service import StoryPlanService
    service = StoryPlanService()
    result = await service.update_layer(project_id, layer_name, req.data, db)
    if "error" in result:
        raise HTTPException(
            status_code=int(result.get("status_code", 400)),
            detail={
                "message": result["error"],
                "chapter_continuity": result.get("chapter_continuity"),
            },
        )
    await db.commit()
    return result


@router.patch(
    "/{project_id}/story-plan/chapter-spine/{chapter_number}",
    summary="更新章节脊柱中的单章",
)
async def update_chapter_spine_item(
    project_id: uuid.UUID,
    chapter_number: int,
    updates: dict,
    db: AsyncSession = Depends(get_db),
):
    from app.services.story_plan_service import StoryPlanService
    service = StoryPlanService()
    result = await service.update_chapter_spine_item(project_id, chapter_number, updates, db)
    if "error" in result:
        raise HTTPException(
            status_code=int(result.get("status_code", 400)),
            detail={
                "message": result["error"],
                "chapter_continuity": result.get("chapter_continuity"),
            },
        )
    await db.commit()
    return result


@router.post(
    "/{project_id}/story-plan/chapter-spine",
    summary="手动追加一个大纲章节",
    description="在连续章节脊柱的末尾原子追加下一章。不会创建章节正文；有待确认草稿或冻结状态时拒绝修改。",
)
async def append_chapter_spine_item(
    project_id: uuid.UUID,
    req: AppendChapterSpineRequest,
    db: AsyncSession = Depends(get_db),
):
    from app.services.story_plan_service import StoryPlanService

    result = await StoryPlanService().append_chapter_spine_item(
        project_id,
        req.model_dump(),
        db,
    )
    _raise_story_plan_error(result)
    await db.commit()
    return result


@router.delete(
    "/{project_id}/story-plan/chapter-spine/{chapter_number}",
    summary="删除章节脊柱中的单章",
)
async def delete_chapter_spine_item(
    project_id: uuid.UUID,
    chapter_number: int,
    db: AsyncSession = Depends(get_db),
):
    from app.services.story_plan_service import StoryPlanService
    service = StoryPlanService()
    result = await service.delete_chapter_spine_item(project_id, chapter_number, db)
    if "error" in result:
        raise HTTPException(
            status_code=int(result.get("status_code", 400)),
            detail={
                "message": result["error"],
                "chapter_continuity": result.get("chapter_continuity"),
            },
        )
    await db.commit()
    return result


@router.post(
    "/{project_id}/story-plan/freeze/{layer_name}",
    summary="冻结 Story Plan 层级",
)
async def freeze_story_plan_layer(
    project_id: uuid.UUID,
    layer_name: str,
    db: AsyncSession = Depends(get_db),
):
    from app.services.story_plan_service import StoryPlanService
    service = StoryPlanService()
    result = await service.freeze_layer(project_id, layer_name, db)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    await db.commit()
    return result


@router.post(
    "/{project_id}/story-plan/unfreeze/{layer_name}",
    summary="解冻 Story Plan 层级",
)
async def unfreeze_story_plan_layer(
    project_id: uuid.UUID,
    layer_name: str,
    db: AsyncSession = Depends(get_db),
):
    from app.services.story_plan_service import StoryPlanService
    service = StoryPlanService()
    result = await service.unfreeze_layer(project_id, layer_name, db)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    await db.commit()
    return result


@router.post(
    "/{project_id}/story-plan/migrate",
    summary="迁移旧大纲数据到 Story Plan",
)
async def migrate_to_story_plan(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    from app.services.story_plan_service import StoryPlanService
    service = StoryPlanService()
    result = await service.migrate_project(project_id, db)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    await db.commit()
    return result


# ── Scene Brief API ─────────────────────────────────────


@router.get(
    "/{project_id}/scene-brief/{chapter_number}",
    summary="获取章节场景简报",
)
async def get_scene_brief(
    project_id: uuid.UUID,
    chapter_number: int,
    db: AsyncSession = Depends(get_db),
):
    from app.services.scene_brief_service import SceneBriefService
    service = SceneBriefService()
    brief = await service.get_scene_brief(project_id, chapter_number, db)
    return brief or {"scene_brief": None}


@router.get(
    "/{project_id}/scene-briefs-range",
    summary="获取章节范围场景简报",
)
async def get_scene_briefs_range(
    project_id: uuid.UUID,
    start: int = 1,
    end: int = 10,
    db: AsyncSession = Depends(get_db),
):
    from app.services.scene_brief_service import SceneBriefService
    service = SceneBriefService()
    return await service.get_scene_briefs_range(project_id, start, end, db)


@router.put(
    "/{project_id}/scene-brief/{chapter_number}",
    summary="保存章节场景简报",
)
async def save_scene_brief(
    project_id: uuid.UUID,
    chapter_number: int,
    brief_data: dict,
    db: AsyncSession = Depends(get_db),
):
    from app.services.scene_brief_service import SceneBriefService
    service = SceneBriefService()
    result = await service.save_scene_brief(project_id, chapter_number, brief_data, db)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    await db.commit()
    return result


@router.delete(
    "/{project_id}/scene-brief/{chapter_number}",
    summary="丢弃章节场景简报",
)
async def discard_scene_brief(
    project_id: uuid.UUID,
    chapter_number: int,
    db: AsyncSession = Depends(get_db),
):
    from app.services.scene_brief_service import SceneBriefService
    service = SceneBriefService()
    result = await service.discard_brief(project_id, chapter_number, db)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    await db.commit()
    return result


@router.get(
    "/{project_id}/scene-briefs/expired",
    summary="获取已过期的场景简报",
)
async def get_expired_scene_briefs(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    from app.services.scene_brief_service import SceneBriefService
    service = SceneBriefService()
    return await service.get_expired_briefs(project_id, db)


@router.post(
    "/{project_id}/scene-brief/{chapter_number}/generate",
    summary="生成章节场景简报",
)
async def generate_scene_brief(
    project_id: uuid.UUID,
    chapter_number: int,
    db: AsyncSession = Depends(get_db),
):
    from app.services.scene_brief_service import SceneBriefService
    service = SceneBriefService()
    result = await service.generate_brief_for_chapter(project_id, chapter_number, db)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    await db.commit()
    return result


# ── Thread Plan API ─────────────────────────────────────


@router.get(
    "/{project_id}/thread-plan",
    summary="获取线索计划",
)
async def get_thread_plan(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    from app.services.thread_plan_service import ThreadPlanService
    service = ThreadPlanService()
    result = await service.get_thread_plan(project_id, db)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.get(
    "/{project_id}/thread-plan/detail/{thread_id}",
    summary="获取单条线索详情",
)
async def get_thread_detail(
    project_id: uuid.UUID,
    thread_id: str,
    db: AsyncSession = Depends(get_db),
):
    from app.services.thread_plan_service import ThreadPlanService
    service = ThreadPlanService()
    thread = await service.get_thread(project_id, thread_id, db)
    if isinstance(thread, dict) and "error" in thread:
        raise HTTPException(status_code=404, detail=thread["error"])
    if not thread:
        raise HTTPException(status_code=404, detail="线索不存在")
    return thread


@router.get(
    "/{project_id}/thread-plan/chapter/{chapter_number}",
    summary="获取章节涉及的线索",
)
async def get_threads_for_chapter(
    project_id: uuid.UUID,
    chapter_number: int,
    db: AsyncSession = Depends(get_db),
):
    from app.services.thread_plan_service import ThreadPlanService
    service = ThreadPlanService()
    result = await service.get_threads_for_chapter(project_id, chapter_number, db)
    if isinstance(result, dict) and "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.get(
    "/{project_id}/thread-plan/orphans",
    summary="检查悬空线索",
)
async def check_orphan_threads(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    from app.services.thread_plan_service import ThreadPlanService
    service = ThreadPlanService()
    result = await service.check_orphan_threads(project_id, db)
    if isinstance(result, dict) and "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


# ── Audit & Orchestration API ───────────────────────────


@router.post(
    "/{project_id}/audit",
    summary="执行完整审查",
)
async def run_full_audit(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    from app.services.story_plan_audit_service import StoryPlanAuditService
    service = StoryPlanAuditService()
    result = await service.full_audit(project_id, db)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.post(
    "/{project_id}/audit/rule",
    summary="执行规则校验",
)
async def run_rule_validation(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    from app.services.story_plan_audit_service import StoryPlanAuditService
    service = StoryPlanAuditService()
    result = await service.rule_validation_only(project_id, db)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.post(
    "/{project_id}/audit/thread",
    summary="执行线索图校验",
)
async def run_thread_audit(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    from app.services.story_plan_audit_service import StoryPlanAuditService
    service = StoryPlanAuditService()
    result = await service.thread_audit_only(project_id, db)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.post(
    "/{project_id}/audit/dependency",
    summary="执行依赖图校验",
)
async def run_dependency_audit(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    from app.services.story_plan_audit_service import StoryPlanAuditService
    service = StoryPlanAuditService()
    result = await service.dependency_audit_only(project_id, db)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result
