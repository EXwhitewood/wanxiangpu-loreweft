"""章级规划 API"""
from __future__ import annotations
from fastapi import APIRouter, HTTPException
from app.models.chapter_plan_contract import ChapterPlanCompileInput, ChapterPlanContract
from app.services.editor_planning_compiler import get_editor_planning_compiler

router = APIRouter()


@router.post("/compile")
async def compile_chapter_plans(input_data: ChapterPlanCompileInput):
    """编译章级合同"""
    compiler = get_editor_planning_compiler()
    return await compiler.compile(input_data)


@router.get("/{project_id}")
async def get_chapter_plan_sequence(project_id: str):
    """获取章级规划序列"""
    compiler = get_editor_planning_compiler()
    plan = compiler.get_plan(project_id)
    if not plan:
        return {"project_id": project_id, "chapters": [], "total_chapters": 0}
    return plan.model_dump()


@router.get("/{project_id}/chapters/{chapter_number}")
async def get_chapter_plan(project_id: str, chapter_number: int):
    """获取单章规划"""
    compiler = get_editor_planning_compiler()
    plan = compiler.get_chapter_plan(project_id, chapter_number)
    if not plan:
        raise HTTPException(status_code=404, detail=f"章节 {chapter_number} 规划不存在")
    return plan.model_dump()


@router.put("/{project_id}/chapters/{chapter_number}")
async def update_chapter_plan(project_id: str, chapter_number: int, chapter: ChapterPlanContract):
    """更新单章规划"""
    compiler = get_editor_planning_compiler()
    chapter.chapter_number = chapter_number
    result = compiler.update_chapter_plan(project_id, chapter)
    if not result:
        raise HTTPException(status_code=404, detail=f"章节 {chapter_number} 规划不存在")
    return result.model_dump()


@router.post("/compile-from-context")
async def compile_chapter_plan_from_context(
    project_id: str,
    chapter_number: int,
):
    """从主编决策上下文编译章级合同"""
    from app.services.editor_decision_context_builder import get_editor_decision_context_builder
    from app.models.editor_decision_context import EditorDecisionContextRequest

    builder = get_editor_decision_context_builder()
    context = await builder.build(EditorDecisionContextRequest(
        project_id=project_id,
        chapter_number=chapter_number,
    ))

    compiler = get_editor_planning_compiler()
    plan = await compiler.compile_from_decision_context(
        project_id=project_id,
        chapter_number=chapter_number,
        decision_context=context,
    )
    return plan.model_dump()
