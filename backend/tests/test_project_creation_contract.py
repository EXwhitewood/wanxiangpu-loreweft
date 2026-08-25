import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from app.api.projects import create_project, delete_project, update_project
from app.db.db_models import Project
from app.models.project import ProjectCreate


class _FakeSession:
    def __init__(self):
        self.added = []
        self.commits = 0

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        self.commits += 1

    async def refresh(self, _value):
        return None

    async def delete(self, value):
        self.added.remove(value)


def test_project_create_normalizes_and_validates_fields():
    payload = ProjectCreate(
        name="  雾港回声  ",
        description="  一名记忆修复师发现城市正在被改写。  ",
        genre="  赛博朋克悬疑  ",
        genre_profile_id="SCIFI",
        word_count_target=300_000,
    )

    assert payload.name == "雾港回声"
    assert payload.description == "一名记忆修复师发现城市正在被改写。"
    assert payload.genre == "赛博朋克悬疑"
    assert payload.genre_profile_id == "scifi"
    assert payload.word_count_target == 300_000


@pytest.mark.parametrize(
    "overrides",
    [
        {"name": "   "},
        {"name": "a" * 81},
        {"description": "a" * 1001},
        {"genre": "a" * 101},
        {"word_count_target": 9999},
        {"word_count_target": 5_000_001},
        {"genre_profile_id": "cyberpunk"},
    ],
)
def test_project_create_rejects_invalid_contract(overrides):
    payload = {
        "name": "有效作品名",
        "description": "",
        "genre": "",
        "genre_profile_id": "general",
        "word_count_target": 100_000,
        **overrides,
    }
    with pytest.raises(ValidationError):
        ProjectCreate(**payload)


def test_create_project_persists_explicit_genre_profile_without_world_facts():
    db = _FakeSession()
    payload = ProjectCreate(
        name="雾港回声",
        description="初始故事种子",
        genre="赛博朋克悬疑",
        genre_profile_id="scifi",
        word_count_target=300_000,
    )

    project = asyncio.run(create_project(payload, db=db, user=None))

    assert db.added == [project]
    assert db.commits == 1
    assert project.core_data == {"genre_profile_id": "scifi"}
    assert project.outline_data is None
    assert project.genre == "赛博朋克悬疑"


def test_create_project_keeps_legacy_genre_inference_as_fallback():
    db = _FakeSession()
    payload = ProjectCreate(name="旧客户端项目", genre="仙侠")

    project = asyncio.run(create_project(payload, db=db, user=None))

    assert project.core_data == {"genre_profile_id": "xianxia"}


def test_update_project_preserves_or_replaces_genre_profile_explicitly():
    project = Project(
        name="旧名",
        description="",
        genre="仙侠",
        word_count_target=100_000,
        core_data={"genre_profile_id": "xianxia", "characters": []},
    )
    db = _FakeSession()

    asyncio.run(
        update_project(
            ProjectCreate(name="新名", genre="科幻", word_count_target=300_000),
            project=project,
            db=db,
        )
    )
    assert project.core_data == {"genre_profile_id": "xianxia", "characters": []}

    asyncio.run(
        update_project(
            ProjectCreate(
                name="新名",
                genre="科幻",
                genre_profile_id="scifi",
                word_count_target=300_000,
            ),
            project=project,
            db=db,
        )
    )
    assert project.core_data == {"genre_profile_id": "scifi", "characters": []}


def test_delete_project_purges_file_backed_project_artifacts(monkeypatch):
    from app.services import (
        benchmark_deconstruction,
        context_ledger_service,
        editor_feedback_service,
        editor_planning_compiler,
        editor_trace_collector,
        market_intelligence,
    )

    project = Project(id="00000000-0000-0000-0000-000000000123", name="delete-me")
    db = _FakeSession()
    db.add(project)
    purgers = [MagicMock() for _ in range(6)]
    monkeypatch.setattr(
        context_ledger_service,
        "get_context_ledger_service",
        lambda: SimpleNamespace(purge=purgers[0]),
    )
    monkeypatch.setattr(
        editor_trace_collector,
        "get_editor_trace_collector",
        lambda: SimpleNamespace(purge=purgers[1]),
    )
    monkeypatch.setattr(
        editor_planning_compiler,
        "get_editor_planning_compiler",
        lambda: SimpleNamespace(purge=purgers[2]),
    )
    monkeypatch.setattr(editor_feedback_service, "purge_editor_feedback", purgers[3])
    monkeypatch.setattr(
        benchmark_deconstruction,
        "get_benchmark_service",
        lambda: SimpleNamespace(purge_project_binding=purgers[4]),
    )
    monkeypatch.setattr(
        market_intelligence,
        "get_market_intelligence_service",
        lambda: SimpleNamespace(purge_project=purgers[5]),
    )

    result = asyncio.run(delete_project(project=project, db=db))

    assert result == {"message": "项目已删除"}
    assert db.added == []
    assert db.commits == 1
    project_id = str(project.id)
    for purger in purgers:
        purger.assert_called_once_with(project_id)
