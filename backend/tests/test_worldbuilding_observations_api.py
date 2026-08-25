import uuid

import pytest

from app.api.worldbuilding import list_observations
from app.db.db_models import Project, WorldviewObservation


def _observation(
    project_id: uuid.UUID,
    *,
    chapter: int,
    entity_type: str,
    status: str,
    auto_promoted: bool = False,
    confirmed_by: str | None = None,
) -> WorldviewObservation:
    observation_id = uuid.uuid4()
    return WorldviewObservation(
        id=observation_id,
        project_id=project_id,
        chapter_number=chapter,
        entity_type=entity_type,
        entity_name=f"entity-{chapter}",
        entity_name_normalized=f"entity-{chapter}",
        operation="new",
        payload={"chapter": chapter},
        evidence_text=f"evidence-{chapter}",
        fingerprint=f"observation-{observation_id}",
        confidence=0.9,
        status=status,
        auto_promoted=auto_promoted,
        confirmed_by=confirmed_by,
        extraction_version=1,
        extraction_source="test",
        generation_revision=1,
    )


@pytest.mark.asyncio
async def test_observation_list_paginates_pending_without_losing_full_counts(db):
    project_id = uuid.uuid4()
    db.add(Project(id=project_id, name="observations"))
    db.add_all([
        _observation(project_id, chapter=1, entity_type="character", status="active"),
        _observation(project_id, chapter=2, entity_type="fact", status="active"),
        _observation(
            project_id,
            chapter=3,
            entity_type="character",
            status="promoted",
            auto_promoted=True,
            confirmed_by="system",
        ),
        _observation(
            project_id,
            chapter=4,
            entity_type="location",
            status="promoted",
            confirmed_by="user",
        ),
        _observation(project_id, chapter=5, entity_type="fact", status="rejected"),
    ])
    await db.commit()

    first = await list_observations(
        project_id,
        review_state="pending",
        page=1,
        page_size=1,
        db=db,
    )
    second = await list_observations(
        project_id,
        review_state="pending",
        page=2,
        page_size=1,
        db=db,
    )

    assert first["total"] == 2
    assert len(first["items"]) == 1
    assert first["items"][0]["chapter_number"] == 1
    assert first["has_more"] is True
    assert first["total_pages"] == 2
    assert first["by_type"] == {"character": 1, "fact": 1}
    assert first["by_review_state"] == {
        "all": 5,
        "pending": 2,
        "auto": 1,
        "manual": 1,
        "confirmed": 2,
    }
    assert second["items"][0]["chapter_number"] == 2
    assert second["has_more"] is False


@pytest.mark.asyncio
async def test_observation_list_supports_review_and_entity_filters(db):
    project_id = uuid.uuid4()
    db.add(Project(id=project_id, name="filtered-observations"))
    db.add_all([
        _observation(
            project_id,
            chapter=1,
            entity_type="character",
            status="promoted",
            auto_promoted=True,
            confirmed_by="system",
        ),
        _observation(
            project_id,
            chapter=2,
            entity_type="location",
            status="promoted",
            auto_promoted=True,
            confirmed_by="system",
        ),
        _observation(
            project_id,
            chapter=3,
            entity_type="character",
            status="promoted",
            confirmed_by="user",
        ),
    ])
    await db.commit()

    result = await list_observations(
        project_id,
        entity_type="character",
        review_state="auto",
        page=1,
        page_size=20,
        db=db,
    )

    assert result["total"] == 1
    assert [item["chapter_number"] for item in result["items"]] == [1]
    assert result["by_type"] == {"character": 1}
    assert result["by_review_state"]["auto"] == 1
    assert result["by_review_state"]["manual"] == 1


@pytest.mark.asyncio
async def test_observation_list_without_page_size_keeps_legacy_all_items_contract(db):
    project_id = uuid.uuid4()
    db.add(Project(id=project_id, name="legacy-observations"))
    db.add_all([
        _observation(project_id, chapter=1, entity_type="fact", status="active"),
        _observation(project_id, chapter=2, entity_type="fact", status="retracted"),
    ])
    await db.commit()

    result = await list_observations(
        project_id,
        page=1,
        page_size=None,
        db=db,
    )

    assert result["total"] == 2
    assert len(result["items"]) == 2
    assert result["page_size"] is None
    assert result["has_more"] is False

