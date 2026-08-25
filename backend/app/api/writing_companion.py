import json
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.writing_companion import WritingCompanionAgent
from app.db.db_models import Project, get_db


router = APIRouter()


class WritingCompanionMessage(BaseModel):
    role: str
    content: str


class WritingCompanionRequest(BaseModel):
    messages: list[WritingCompanionMessage]
    chapter_number: int = 1


@router.post(
    "/{project_id}/chat/stream",
    summary="墨伴创作对话（流式）",
)
async def writing_companion_chat_stream(
    project_id: uuid.UUID,
    data: WritingCompanionRequest,
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    agent = WritingCompanionAgent()
    context = {
        "messages": [message.model_dump() for message in data.messages],
        "project_id": project_id,
        "chapter_number": data.chapter_number,
        "db": db,
    }

    async def event_generator():
        async for event in agent.execute_stream(context):
            yield (
                "data: "
                + json.dumps(event, ensure_ascii=False, default=str)
                + "\n\n"
            )
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
