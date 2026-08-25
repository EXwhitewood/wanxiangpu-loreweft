from __future__ import annotations

import uuid
from copy import deepcopy
import hashlib
import json

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.db_models import Base, Chapter, ChatMessageRecord, ChatSession, Project
from app.services.project_archive_service import ProjectArchiveService


@pytest.mark.asyncio
async def test_lossless_archive_previews_and_transactionally_restores_project(tmp_path):
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _foreign_keys_on(connection, _record):
        connection.execute("PRAGMA foreign_keys = ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    project_id = uuid.uuid4()
    service = ProjectArchiveService()
    try:
        async with sessions() as db:
            project = Project(
                id=project_id,
                name="archive-project",
                outline_data={"chapter_spine": [{"chapter_number": 1}]},
                core_data={"characters": [{"name": "陈长生"}]},
            )
            chapter = Chapter(
                project_id=project_id,
                chapter_number=1,
                title="第一章",
                content="原始正文",
                status="draft",
            )
            session = ChatSession(project_id=project_id, agent_type="outline_architect", title="大纲讨论")
            db.add(project)
            await db.flush()
            db.add_all([chapter, session])
            await db.flush()
            db.add(ChatMessageRecord(session_id=session.id, role="user", content="保留这条消息"))
            await db.commit()

            archive = await service.export_project(db, str(project_id))
            assert archive["format"] == "loreweft.project-archive"
            assert archive["counts"]["projects"] == 1
            assert archive["counts"]["chapters"] == 1
            assert archive["counts"]["chat_message_records"] == 1
            assert all(archive["tables"].values())

            chapter.content = "被错误修改的正文"
            await db.commit()
            preview = await service.preview_restore(
                db,
                project_id=str(project_id),
                archive=archive,
            )
            assert preview["valid"] is True
            assert preview["content_changed"] is True

            result = await service.restore_project(
                db,
                project_id=str(project_id),
                archive=archive,
                confirmation="archive-project",
                backup_directory=tmp_path,
            )
            assert result["restored"] is True
            assert result["backup_path"].startswith(str(tmp_path))

            db.expire_all()
            restored = (
                await db.execute(
                    select(Chapter).where(
                        Chapter.project_id == project_id,
                        Chapter.chapter_number == 1,
                    )
                )
            ).scalar_one()
            assert restored.content == "原始正文"
            messages = (await db.execute(select(ChatMessageRecord))).scalars().all()
            assert [item.content for item in messages] == ["保留这条消息"]
            violations = (await db.execute(text("PRAGMA foreign_key_check"))).all()
            assert violations == []

            # A hand-edited archive can recompute its checksum. Structural
            # validation must still reject cross-project/unknown data before
            # the live project is deleted.
            malformed = deepcopy(archive)
            malformed["tables"]["projects"][0]["unknown_column"] = "bad"
            canonical = json.dumps(
                malformed["tables"],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            malformed["checksum"]["tables"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            with pytest.raises(ValueError, match="未知字段"):
                await service.restore_project(
                    db,
                    project_id=str(project_id),
                    archive=malformed,
                    confirmation="archive-project",
                    backup_directory=tmp_path,
                )
            still_present = (
                await db.execute(
                    select(Chapter).where(
                        Chapter.project_id == project_id,
                        Chapter.chapter_number == 1,
                    )
                )
            ).scalar_one()
            assert still_present.content == "原始正文"
    finally:
        await engine.dispose()


def test_archive_checksum_tampering_is_rejected():
    archive = {
        "format": "loreweft.project-archive",
        "version": 1,
        "project": {"id": "p", "name": "n"},
        "checksum": {"algorithm": "sha256", "tables": "wrong"},
        "tables": {"projects": [{"id": "p"}]},
    }

    with pytest.raises(ValueError, match="校验和"):
        ProjectArchiveService.validate_archive(archive, "p")
