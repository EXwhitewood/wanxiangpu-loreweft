from __future__ import annotations

import uuid

from sqlalchemy import select

from app.db.db_models import Chapter, ChapterEffectOutbox, Project
from app.services import chapter_commit_service


async def test_recovery_replays_pending_outbox_for_draft_user_chapter(db, monkeypatch):
    project_id = uuid.uuid4()
    db.add(Project(id=project_id, name="user project"))
    db.add(
        Chapter(
            project_id=project_id,
            chapter_number=1,
            title="手写章",
            content="用户正文",
            status="draft",
        )
    )
    outbox = ChapterEffectOutbox(
        project_id=project_id,
        chapter_number=1,
        scene_index=999_999,
        effect_type="worldview_projection",
        effect_version=1,
        payload={"generation_revision": 1},
        idempotency_key=f"{project_id}:draft-recovery",
        status="pending",
        applied=False,
    )
    db.add(outbox)
    await db.commit()

    calls: list[tuple[str, int]] = []

    async def fake_apply(session, *, project_id, chapter_number, project=None):
        calls.append((str(project_id), chapter_number))
        row = (
            await session.execute(
                select(ChapterEffectOutbox).where(ChapterEffectOutbox.id == outbox.id)
            )
        ).scalar_one()
        row.applied = True
        row.status = "applied"
        await session.commit()

    monkeypatch.setattr(chapter_commit_service, "apply_pending_chapter_effects", fake_apply)

    result = await chapter_commit_service.recover_pending_chapter_effects(db)

    assert calls == [(str(project_id), 1)]
    assert result["replayed_groups"] == 1
    assert result["remaining"] == 0
