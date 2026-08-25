from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from sqlalchemy import select

from app.db.db_models import Chapter, ChapterEffectOutbox, ChapterSettlementJob, Project
from app.services import user_chapter_settlement_job_service as jobs


def _reference() -> dict:
    return {
        "outline_data": {"chapter_spine": [{"chapter_number": 1}]},
        "core_data": {},
        "outline_version": 2,
        "story_state": {"active_chapter": 1, "active_scene": 1},
    }


async def test_new_revision_supersedes_queued_job_without_touching_prose(db):
    project_id = uuid.uuid4()
    project = Project(id=project_id, name="manual", core_data={})
    chapter = Chapter(
        project_id=project_id,
        chapter_number=1,
        title="第一章",
        content="第一版正文",
        status="draft",
    )
    db.add_all([project, chapter])
    await db.flush()
    first = await jobs.enqueue_user_chapter_settlement(
        db,
        project=project,
        chapter=chapter,
        reference_snapshot=_reference(),
    )
    await db.commit()

    chapter.content = "第二版正文"
    second = await jobs.enqueue_user_chapter_settlement(
        db,
        project=project,
        chapter=chapter,
        reference_snapshot=_reference(),
    )
    await db.commit()
    await db.refresh(first)

    assert first.status == "superseded"
    assert second.status == "pending"
    assert second.chapter_revision_hash != first.chapter_revision_hash
    assert chapter.content == "第二版正文"


async def test_worker_completes_all_auxiliary_stages_without_generation_route(db, monkeypatch):
    project_id = uuid.uuid4()
    project = Project(
        id=project_id,
        name="manual",
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
    chapter = Chapter(
        project_id=project_id,
        chapter_number=1,
        title="第一章",
        content="用户手写正文",
        status="draft",
    )
    db.add_all([project, chapter])
    await db.flush()
    job = await jobs.enqueue_user_chapter_settlement(
        db,
        project=project,
        chapter=chapter,
        reference_snapshot=_reference(),
    )
    await db.commit()
    job_id = job.id

    monkeypatch.setattr(
        jobs.ChapterSummaryService,
        "generate_summary",
        AsyncMock(return_value={"core_event": "事件完成"}),
    )
    monkeypatch.setattr(
        jobs.NarrativeSyncService,
        "sync_chapter_write",
        AsyncMock(return_value={}),
    )

    async def fake_enqueue(session, *, project_id, chapter_number, **_kwargs):
        session.add(
            ChapterEffectOutbox(
                project_id=project_id,
                chapter_number=chapter_number,
                scene_index=999_999,
                effect_type="worldview_projection",
                effect_version=1,
                payload={"generation_revision": 1},
                idempotency_key=f"{project_id}:{chapter_number}:test-worldview",
                status="applied",
                applied=True,
            )
        )
        await session.flush()

    monkeypatch.setattr(jobs, "enqueue_worldview_projection", fake_enqueue)
    monkeypatch.setattr(jobs, "apply_pending_chapter_effects", AsyncMock(return_value=None))
    settle = AsyncMock(return_value={"active_chapter": 2})
    monkeypatch.setattr(
        jobs,
        "get_user_chapter_settlement_service",
        lambda: SimpleNamespace(settle=settle),
    )

    await jobs.process_user_chapter_settlement(job_id)

    db.expire_all()
    completed = await db.get(ChapterSettlementJob, job_id)
    assert completed.status == "completed"
    assert completed.phase == "completed"
    assert completed.stage_results["worldview"]["status"] == "completed"
    assert completed.stage_results["state"]["status"] == "completed"
    settle.assert_awaited_once()
    saved_chapter = (
        await db.execute(
            select(Chapter).where(
                Chapter.project_id == project_id,
                Chapter.chapter_number == 1,
            )
        )
    ).scalar_one()
    assert saved_chapter.content == "用户手写正文"


async def test_recovery_schedules_due_jobs_and_not_completed_jobs(db, monkeypatch):
    project_id = uuid.uuid4()
    project = Project(id=project_id, name="manual")
    chapter = Chapter(project_id=project_id, chapter_number=1, content="正文", status="draft")
    db.add_all([project, chapter])
    await db.flush()
    job = await jobs.enqueue_user_chapter_settlement(
        db,
        project=project,
        chapter=chapter,
        reference_snapshot=_reference(),
    )
    await db.commit()
    schedule = Mock()
    monkeypatch.setattr(jobs, "schedule_user_chapter_settlement", schedule)

    result = await jobs.recover_user_chapter_settlements(db)

    assert result == {"scheduled": 1, "recoverable": 1}
    schedule.assert_called_once()
    assert str(schedule.call_args.args[0]) == str(job.id)
