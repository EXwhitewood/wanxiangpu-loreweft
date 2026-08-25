"""主编决策上下文 API"""
from __future__ import annotations
from fastapi import APIRouter
from app.models.editor_decision_context import (
    ChapterDecisionBrief,
    EditorDecisionContext,
    EditorDecisionContextRequest,
)
from app.services.editor_decision_context_builder import get_editor_decision_context_builder

router = APIRouter()


@router.post("/build", response_model=EditorDecisionContext)
async def build_editor_decision_context(request: EditorDecisionContextRequest):
    """构建主编决策上下文"""
    builder = get_editor_decision_context_builder()
    return await builder.build(request)


@router.post("/brief", response_model=ChapterDecisionBrief)
async def build_chapter_decision_brief(request: EditorDecisionContextRequest):
    """构建本章决策依据（主编 Agent 可读格式）"""
    builder = get_editor_decision_context_builder()
    context = await builder.build(request)
    return builder.build_brief(context)
