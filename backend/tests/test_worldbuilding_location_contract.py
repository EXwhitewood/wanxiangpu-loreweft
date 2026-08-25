import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.api import worldbuilding
from app.db.db_models import Project, WorldviewObservation


class _FakeDb:
    def __init__(self, project):
        self.project = project
        self.commit = AsyncMock()

    async def get(self, _model, _project_id):
        return self.project


@pytest.mark.asyncio
async def test_location_crud_uses_canonical_transaction_service(monkeypatch):
    project_id = uuid.uuid4()
    project = SimpleNamespace(core_data={"locations": []})
    db = _FakeDb(project)
    service = SimpleNamespace(
        add_location_with_db=AsyncMock(return_value={"id": "loc-1", "name": "渡口"}),
        update_location_with_db=AsyncMock(return_value={"id": "loc-1", "name": "旧渡口"}),
        delete_location_with_db=AsyncMock(return_value=True),
    )
    monkeypatch.setattr(worldbuilding, "CoreMemoryService", lambda: service)

    created = await worldbuilding.create_location(project_id, {"name": "渡口"}, db)
    updated = await worldbuilding.update_location(project_id, "loc-1", {"name": "旧渡口"}, db)
    deleted = await worldbuilding.delete_location(project_id, "loc-1", db)

    assert created["id"] == "loc-1"
    assert updated["name"] == "旧渡口"
    assert deleted == {"message": "已删除"}
    service.add_location_with_db.assert_awaited_once_with(db, str(project_id), {"name": "渡口"})
    service.update_location_with_db.assert_awaited_once_with(
        db, str(project_id), "loc-1", {"name": "旧渡口"},
    )
    service.delete_location_with_db.assert_awaited_once_with(db, str(project_id), "loc-1")
    assert db.commit.await_count == 3


@pytest.mark.asyncio
async def test_location_write_contract_rejects_missing_resources(monkeypatch):
    project_id = uuid.uuid4()
    missing_db = _FakeDb(None)
    with pytest.raises(HTTPException) as project_missing:
        await worldbuilding.create_location(project_id, {"name": "渡口"}, missing_db)
    assert project_missing.value.status_code == 404

    project = SimpleNamespace(core_data={"locations": []})
    db = _FakeDb(project)
    service = SimpleNamespace(update_location_with_db=AsyncMock(return_value=None))
    monkeypatch.setattr(worldbuilding, "CoreMemoryService", lambda: service)
    with pytest.raises(HTTPException) as location_missing:
        await worldbuilding.update_location(project_id, "missing", {"name": "渡口"}, db)
    assert location_missing.value.status_code == 404


@pytest.mark.asyncio
async def test_observation_actions_are_scoped_to_route_project(db):
    owner_id = uuid.uuid4()
    other_id = uuid.uuid4()
    observation_id = uuid.uuid4()
    db.add_all([
        Project(id=owner_id, name="owner"),
        Project(id=other_id, name="other"),
        WorldviewObservation(
            id=observation_id,
            project_id=owner_id,
            chapter_number=1,
            entity_type="fact",
            entity_name="只属于 owner 的事实",
            entity_name_normalized="只属于owner的事实",
            operation="new",
            payload={},
            evidence_text="正文证据",
            fingerprint=f"test-{observation_id}",
            confidence=0.9,
            status="active",
            extraction_version=1,
            extraction_source="test",
            generation_revision=1,
        ),
    ])
    await db.commit()

    with pytest.raises(HTTPException) as missing:
        await worldbuilding.promote_observation(other_id, str(observation_id), db)
    assert missing.value.status_code == 404


@pytest.mark.asyncio
async def test_observation_actions_reject_invalid_id_before_service(db):
    project_id = uuid.uuid4()
    db.add(Project(id=project_id, name="project"))
    await db.commit()

    with pytest.raises(HTTPException) as invalid:
        await worldbuilding.reject_observation(project_id, "not-a-uuid", {}, db)
    assert invalid.value.status_code == 422


@pytest.mark.asyncio
async def test_fact_observation_cannot_report_fake_core_promotion(db):
    project_id = uuid.uuid4()
    observation_id = uuid.uuid4()
    observation = WorldviewObservation(
        id=observation_id,
        project_id=project_id,
        chapter_number=1,
        entity_type="fact",
        entity_name="只有观察层的事实",
        entity_name_normalized="只有观察层的事实",
        operation="new",
        payload={},
        evidence_text="正文证据",
        fingerprint=f"fact-{observation_id}",
        confidence=0.95,
        status="active",
        extraction_version=1,
        extraction_source="test",
        generation_revision=1,
    )
    db.add_all([Project(id=project_id, name="project"), observation])
    await db.commit()

    with pytest.raises(HTTPException) as unsupported:
        await worldbuilding.promote_observation(project_id, str(observation_id), db)
    assert unsupported.value.status_code == 409
    await db.refresh(observation)
    assert observation.status == "active"
    assert observation.auto_promoted is False
