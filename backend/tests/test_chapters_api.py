import uuid
from unittest.mock import Mock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api import chapters as chapters_api
from app.db.db_models import Base, Chapter, ChapterBaseline, ChapterSettlementJob, Project
from app.utils.word_count import count_words


@pytest.mark.asyncio
@pytest.mark.parametrize("empty_content", ["", "<p></p><br>"])
async def test_create_empty_chapter_skips_heavy_post_processing(monkeypatch, empty_content):
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    project_id = uuid.uuid4()

    schedule = Mock()
    monkeypatch.setattr(chapters_api, "schedule_user_chapter_settlement", schedule)

    try:
        async with async_session() as db:
            db.add(Project(id=project_id, name="empty-chapter-test"))
            await db.commit()

            result = await chapters_api.update_chapter(
                project_id,
                6,
                {"title": "chapter-6", "content": empty_content, "status": "draft"},
                db,
            )
    finally:
        await engine.dispose()

    assert result == {
        "chapter_number": 6,
        "title": "chapter-6",
        "content": empty_content,
        "status": "draft",
    }
    schedule.assert_not_called()


@pytest.mark.asyncio
async def test_explicit_resave_retries_auxiliary_settlement_without_narrative_resync(monkeypatch):
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    project_id = uuid.uuid4()
    schedule = Mock()
    monkeypatch.setattr(chapters_api, "schedule_user_chapter_settlement", schedule)

    try:
        async with async_session() as db:
            project = Project(
                id=project_id,
                name="same-revision-test",
                current_chapter=3,
                outline_version=4,
                outline_data={"chapter_spine": [{"chapter_number": 2}]},
                core_data={
                    "writing_assistance": {
                        "consistency_reminders": {
                            "enabled": True,
                            "check_on_save": True,
                            "dimensions": ["fact"],
                        }
                    }
                },
            )
            chapter = Chapter(
                project_id=project_id,
                chapter_number=2,
                title="第二章",
                content="正文没有变化。",
                status="draft",
            )
            baseline = ChapterBaseline(
                project_id=project_id,
                chapter_number=2,
                outline_version=4,
                baseline_story_state={"active_chapter": 2},
                baseline_chapter_state={},
                snapshot_source="user_chapter_save",
            )
            db.add_all([project, chapter, baseline])
            await db.commit()

            result = await chapters_api.update_chapter(
                project_id,
                2,
                {"title": "第二章", "content": "正文没有变化。", "status": "draft"},
                db,
            )
            await db.refresh(project)
    finally:
        await engine.dispose()

    assert result["settlement"]["status"] == "pending"
    assert result["settlement"]["phase"] == "queued"
    assert project.outline_version == 4
    schedule.assert_called_once()


@pytest.mark.asyncio
async def test_manual_chapter_save_refreshes_project_word_total_and_next_chapter(monkeypatch):
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    project_id = uuid.uuid4()
    schedule = Mock()
    monkeypatch.setattr(chapters_api, "schedule_user_chapter_settlement", schedule)

    first_content = "<p>第一章正文</p>"
    second_content = "<p>第二章的新正文</p>"
    try:
        async with async_session() as db:
            project = Project(
                id=project_id,
                name="manual-total-test",
                current_chapter=1,
                total_words=0,
                core_data={
                    "writing_assistance": {
                        "consistency_reminders": {
                            "enabled": False,
                            "check_on_save": False,
                            "dimensions": [],
                        }
                    }
                },
            )
            db.add_all([
                project,
                Chapter(
                    project_id=project_id,
                    chapter_number=1,
                    title="第一章",
                    content=first_content,
                    status="draft",
                ),
                Chapter(
                    project_id=project_id,
                    chapter_number=2,
                    title="第二章",
                    content="旧正文",
                    status="draft",
                ),
                ChapterBaseline(
                    project_id=project_id,
                    chapter_number=2,
                    outline_version=0,
                    baseline_story_state={"active_chapter": 2},
                    baseline_chapter_state={},
                    snapshot_source="user_chapter_save",
                ),
            ])
            await db.commit()

            await chapters_api.update_chapter(
                project_id,
                2,
                {"content": second_content, "status": "draft"},
                db,
            )
            await db.refresh(project)

            assert project.total_words == count_words(first_content) + count_words(second_content)
            assert project.current_chapter == 3
            jobs = (await db.execute(select(ChapterSettlementJob))).scalars().all()
            assert len(jobs) == 1
            assert jobs[0].status == "pending"
            schedule.assert_called_once()
    finally:
        await engine.dispose()
