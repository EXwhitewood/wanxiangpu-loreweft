import copy
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import outline


class _MissingProjectSession:
    commits = 0

    async def get(self, _model, _project_id):
        return None

    async def commit(self):
        self.commits += 1


class _ProjectSession:
    def __init__(self, project):
        self.project = project
        self.commits = 0

    async def get(self, _model, _project_id):
        return self.project

    async def commit(self):
        self.commits += 1

    async def refresh(self, _project):
        return None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "endpoint,args",
    [
        (outline.get_story_plan, ()),
        (outline.get_story_plan_layer, ("macro_plan",)),
        (outline.freeze_story_plan_layer, ("macro_plan",)),
        (outline.unfreeze_story_plan_layer, ("macro_plan",)),
        (outline.migrate_to_story_plan, ()),
        (outline.get_thread_plan, ()),
        (outline.get_thread_detail, ("missing-thread",)),
        (outline.get_threads_for_chapter, (1,)),
        (outline.check_orphan_threads, ()),
        (outline.run_full_audit, ()),
        (outline.run_rule_validation, ()),
        (outline.run_thread_audit, ()),
        (outline.run_dependency_audit, ()),
        (outline.recover_outline_continuity_draft, ()),
        (outline.append_chapter_spine_item, (outline.AppendChapterSpineRequest(),)),
    ],
)
async def test_missing_project_never_returns_http_200_service_error(endpoint, args):
    session = _MissingProjectSession()

    with pytest.raises(HTTPException) as exc_info:
        await endpoint(uuid.uuid4(), *args, db=session)

    assert exc_info.value.status_code in {400, 404}
    assert session.commits == 0


@pytest.mark.asyncio
async def test_sparse_outline_cannot_be_frozen():
    outline_data = {
        "chapter_spine": [
            {"chapter_number": 1, "title": "开篇"},
            {"chapter_number": 4, "title": "转折"},
        ]
    }
    before = copy.deepcopy(outline_data)
    project = SimpleNamespace(outline_data=outline_data, draft_outline={})
    session = _ProjectSession(project)

    with pytest.raises(HTTPException) as exc_info:
        await outline.freeze_outline(uuid.uuid4(), db=session)

    assert exc_info.value.status_code == 409
    assert project.outline_data == before
    assert session.commits == 0


@pytest.mark.asyncio
async def test_continuity_recovery_only_writes_draft(monkeypatch):
    outline_data = {
        "chapter_spine": [
            {"chapter_number": 1, "title": "开篇", "conflict_text": "出发"},
            {"chapter_number": 4, "title": "转折", "conflict_text": "受阻"},
        ]
    }
    before = copy.deepcopy(outline_data)
    project = SimpleNamespace(outline_data=outline_data, draft_outline={})
    session = _ProjectSession(project)
    monkeypatch.setattr(outline, "flag_modified", lambda *_args, **_kwargs: None)

    result = await outline.recover_outline_continuity_draft(uuid.uuid4(), db=session)

    assert result["draft_saved"] is True
    assert result["recovery"]["added_chapters"] == [2, 3]
    assert project.outline_data == before
    assert [item["chapter_number"] for item in project.draft_outline["chapter_spine"]] == [1, 2, 3, 4]
    assert session.commits == 1


@pytest.mark.asyncio
async def test_unfrozen_status_hides_stale_timestamp_and_reports_draft():
    project = SimpleNamespace(
        outline_data={
            "chapter_spine": [{"chapter_number": 1}],
            "_frozen": False,
            "_frozen_at": "stale-value",
        },
        draft_outline={"chapter_spine": [{"chapter_number": 1}]},
    )

    status = await outline.get_outline_status(uuid.uuid4(), db=_ProjectSession(project))

    assert status["frozen"] is False
    assert status["frozen_at"] is None
    assert status["has_draft"] is True
