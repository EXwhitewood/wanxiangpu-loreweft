import uuid
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, delete, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.db_models import get_db, ChatSession, ChatMessageRecord

logger = logging.getLogger(__name__)
router = APIRouter(tags=["chat-history"])


class MessageInput(BaseModel):
    role: str
    content: str


class SaveMessagesRequest(BaseModel):
    agent_type: str
    messages: list[MessageInput]
    session_id: str | None = None
    chapter_number: int | None = None
    title: str | None = None


class CreateSessionRequest(BaseModel):
    agent_type: str
    chapter_number: int | None = None
    title: str | None = None


class RenameRequest(BaseModel):
    name: str


def _session_to_dict(s: ChatSession, message_count_override: int | None = None) -> dict:
    return {
        "session_id": str(s.id),
        "agent_type": s.agent_type,
        "title": s.title or "",
        "chapter_number": s.chapter_number,
        "is_pinned": s.is_pinned if s.is_pinned is not None else False,
        "summary": s.summary or "",
        "message_count": message_count_override if message_count_override is not None else (s.message_count or 0),
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "updated_at": s.updated_at.isoformat() if s.updated_at else None,
    }


@router.post(
    "/chat-history/sessions",
    summary="创建聊天会话",
)
async def create_session(
    project_id: uuid.UUID,
    data: CreateSessionRequest,
    db: AsyncSession = Depends(get_db),
):
    session = ChatSession(
        project_id=project_id,
        agent_type=data.agent_type,
        title=data.title or "",
        chapter_number=data.chapter_number,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return _session_to_dict(session, 0)


@router.get(
    "/chat-history/sessions",
    summary="获取项目聊天会话列表",
)
async def list_sessions(
    project_id: uuid.UUID,
    agent_type: str | None = None,
    pinned: bool | None = None,
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(ChatSession)
        .where(ChatSession.project_id == project_id)
    )
    if agent_type:
        stmt = stmt.where(ChatSession.agent_type == agent_type)
    if pinned is not None:
        stmt = stmt.where(ChatSession.is_pinned == pinned)
    stmt = stmt.order_by(
        ChatSession.is_pinned.desc(),
        ChatSession.updated_at.desc(),
    )
    result = await db.execute(stmt)
    sessions = result.scalars().all()

    items = []
    for s in sessions:
        mc = s.message_count or 0
        if mc == 0:
            count_stmt = select(func.count()).where(ChatMessageRecord.session_id == s.id)
            mc = (await db.execute(count_stmt)).scalar() or 0
        items.append(_session_to_dict(s, mc))
    return {"sessions": items}


@router.get(
    "/chat-history/sessions/search",
    summary="搜索会话",
)
async def search_sessions(
    project_id: uuid.UUID,
    q: str,
    agent_type: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    pattern = f"%{q}%"
    stmt = (
        select(ChatSession)
        .where(ChatSession.project_id == project_id)
        .where(
            ChatSession.title.ilike(pattern)
            | ChatSession.id.in_(
                select(ChatMessageRecord.session_id).where(
                    ChatMessageRecord.content.ilike(pattern)
                )
            )
        )
        .order_by(ChatSession.updated_at.desc())
    )
    if agent_type:
        stmt = stmt.where(ChatSession.agent_type == agent_type)
    result = await db.execute(stmt)
    sessions = result.scalars().all()

    items = []
    for s in sessions:
        mc = s.message_count or 0
        items.append(_session_to_dict(s, mc))
    return {"sessions": items}


@router.get(
    "/chat-history/sessions/{session_id}",
    summary="获取会话消息",
)
async def get_session_messages(
    project_id: uuid.UUID,
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(ChatSession).where(
        ChatSession.id == uuid.UUID(session_id),
        ChatSession.project_id == project_id,
    )
    session = (await db.execute(stmt)).scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    msg_stmt = (
        select(ChatMessageRecord)
        .where(ChatMessageRecord.session_id == uuid.UUID(session_id))
        .order_by(ChatMessageRecord.created_at.asc())
    )
    result = await db.execute(msg_stmt)
    messages = result.scalars().all()

    return {
        **_session_to_dict(session, len(messages)),
        "messages": [
            {
                "id": str(m.id),
                "role": m.role,
                "content": m.content,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in messages
        ],
    }


@router.post(
    "/chat-history/save",
    summary="保存聊天消息（增量保存）",
)
async def save_messages(
    project_id: uuid.UUID,
    data: SaveMessagesRequest,
    db: AsyncSession = Depends(get_db),
):
    if data.session_id:
        try:
            sid = uuid.UUID(data.session_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="无效的 session_id")
        stmt = select(ChatSession).where(
            ChatSession.id == sid,
            ChatSession.project_id == project_id,
        )
        session = (await db.execute(stmt)).scalar_one_or_none()
        if not session:
            raise HTTPException(status_code=404, detail="会话不存在")

        existing_stmt = (
            select(ChatMessageRecord)
            .where(ChatMessageRecord.session_id == sid)
            .order_by(ChatMessageRecord.created_at.asc())
        )
        existing_messages = (await db.execute(existing_stmt)).scalars().all()
        existing_count = len(existing_messages)
        incoming_pairs = [(m.role, m.content) for m in data.messages]
        existing_pairs = [(m.role, m.content) for m in existing_messages]
        prefix_matches = (
            existing_count <= len(incoming_pairs)
            and existing_pairs == incoming_pairs[:existing_count]
        )

        if not prefix_matches:
            await db.execute(
                delete(ChatMessageRecord).where(ChatMessageRecord.session_id == sid)
            )
            existing_count = 0

        new_messages = data.messages[existing_count:] if prefix_matches else data.messages
        for msg in new_messages:
            record = ChatMessageRecord(
                session_id=session.id,
                role=msg.role,
                content=msg.content,
            )
            db.add(record)
        session.message_count = len(data.messages)
    else:
        session = ChatSession(
            project_id=project_id,
            agent_type=data.agent_type,
            title=data.title or "",
            chapter_number=data.chapter_number,
            message_count=len(data.messages),
        )
        db.add(session)
        await db.flush()

        for msg in data.messages:
            record = ChatMessageRecord(
                session_id=session.id,
                role=msg.role,
                content=msg.content,
            )
            db.add(record)

    if not session.title and data.messages:
        first_user_msg = next(
            (m.content[:50] for m in data.messages if m.role == "user"), ""
        )
        if first_user_msg:
            session.title = first_user_msg

    session.updated_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(session)

    return {
        "session_id": str(session.id),
        "message_count": session.message_count or len(data.messages),
    }


@router.delete(
    "/chat-history/sessions/{session_id}",
    summary="删除聊天会话",
)
async def delete_session(
    project_id: uuid.UUID,
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(ChatSession).where(
        ChatSession.id == uuid.UUID(session_id),
        ChatSession.project_id == project_id,
    )
    session = (await db.execute(stmt)).scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    await db.execute(
        delete(ChatMessageRecord).where(
            ChatMessageRecord.session_id == uuid.UUID(session_id)
        )
    )
    await db.delete(session)
    await db.commit()
    return {"message": "会话已删除"}


@router.get(
    "/chat-history/latest/{agent_type}",
    summary="获取指定Agent类型的最近会话消息",
)
async def get_latest_session(
    project_id: uuid.UUID,
    agent_type: str,
    chapter_number: int | None = None,
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(ChatSession)
        .where(ChatSession.project_id == project_id, ChatSession.agent_type == agent_type)
        .order_by(ChatSession.updated_at.desc())
        .limit(1)
    )
    if chapter_number is not None:
        stmt = stmt.where(ChatSession.chapter_number == chapter_number)
    session = (await db.execute(stmt)).scalar_one_or_none()
    if not session:
        return {"session_id": None, "messages": []}

    msg_stmt = (
        select(ChatMessageRecord)
        .where(ChatMessageRecord.session_id == session.id)
        .order_by(ChatMessageRecord.created_at.asc())
    )
    result = await db.execute(msg_stmt)
    messages = result.scalars().all()

    return {
        **_session_to_dict(session, len(messages)),
        "messages": [
            {"role": m.role, "content": m.content}
            for m in messages
        ],
    }


@router.patch(
    "/chat-history/sessions/{session_id}/rename",
    summary="重命名会话",
)
async def rename_session(
    project_id: uuid.UUID,
    session_id: str,
    data: RenameRequest,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(ChatSession).where(
        ChatSession.id == uuid.UUID(session_id),
        ChatSession.project_id == project_id,
    )
    session = (await db.execute(stmt)).scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    session.title = data.name
    session.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(session)
    return {"session_id": str(session.id), "title": session.title}


@router.patch(
    "/chat-history/sessions/{session_id}/pin",
    summary="置顶会话",
)
async def pin_session(
    project_id: uuid.UUID,
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(ChatSession).where(
        ChatSession.id == uuid.UUID(session_id),
        ChatSession.project_id == project_id,
    )
    session = (await db.execute(stmt)).scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    session.is_pinned = True
    session.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return {"session_id": str(session.id), "is_pinned": True}


@router.patch(
    "/chat-history/sessions/{session_id}/unpin",
    summary="取消置顶会话",
)
async def unpin_session(
    project_id: uuid.UUID,
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(ChatSession).where(
        ChatSession.id == uuid.UUID(session_id),
        ChatSession.project_id == project_id,
    )
    session = (await db.execute(stmt)).scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    session.is_pinned = False
    session.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return {"session_id": str(session.id), "is_pinned": False}
