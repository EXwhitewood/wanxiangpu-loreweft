import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select, func

from app.db.db_models import (
    Chapter,
    ChapterEffectOutbox,
    CrossSystemEvent,
    DetailSeed,
    ForeshadowingClue,
    ForeshadowingLine,
    Project,
    WorldviewObservation,
)
from app.services.worldview_extractor import (
    WorldviewExtractionError,
    WorldviewExtractor,
    normalize_entity_name,
)
from app.services.foreshadowing_upsert_service import ForeshadowingUpsertService
from app.services.memory_core import CoreMemoryService
from app.services.worldview_projection_service import (
    WorldviewProjectionService,
    compute_fingerprint,
)


def test_core_entity_id_serializes_uuid_for_sqlite_text_columns():
    entity_id = uuid.uuid4()

    assert WorldviewProjectionService._core_entity_id(entity_id) == str(entity_id)
    assert WorldviewProjectionService._core_entity_id("") is None


def test_worldview_extractor_uses_shared_truncated_json_repair():
    result = WorldviewExtractor()._parse_extraction_v2(
        '{"characters": [{"name": "角色甲", "aliases": [], "confidence": 0.8}],'
        ' "locations": [], "world_rules": [], "foreshadowing": [], "facts": []'
    )

    assert result is not None
    assert result["characters"][0]["name"] == "角色甲"


def test_worldview_extractor_preserves_structured_fields():
    result = WorldviewExtractor()._parse_extraction_v2(
        """
        {
          "characters": [{
            "name": "Lin",
            "aliases": ["L"],
            "operation": "new",
            "description": "A careful witness",
            "appearance": "silver coat",
            "personality": "guarded",
            "desire": "find the gate",
            "deep_need": "trust others",
            "arc": "isolation to alliance",
            "relationships": {"Yan": "mentor"},
            "evidence_text": "Lin entered the archive.",
            "confidence": 0.94
          }],
          "locations": [{
            "name": "East Archive",
            "operation": "new",
            "description": "A sealed room",
            "parent_location": "Glass City",
            "location_type": "building",
            "atmosphere": "cold",
            "function": "stores forbidden maps",
            "spatial_relations": {"north": "Clock Tower"},
            "rules": ["no open flames"],
            "evidence_text": "The East Archive waited inside Glass City.",
            "confidence": 0.91
          }],
          "world_rules": [],
          "foreshadowing": [{
            "name": "Red key",
            "operation": "new",
            "description": "The key will matter later",
            "bury_window_start": 1,
            "reveal_window_start": 4,
            "related_characters": ["Lin"],
            "truth_type": "object_secret",
            "priority": "high",
            "salience": "subtle",
            "reader_intended_state": "curious",
            "clue_text": "The red key was warm.",
            "evidence_text": "The red key was warm.",
            "confidence": 0.88
          }],
          "facts": []
        }
        """
    )

    assert result["characters"][0]["appearance"] == "silver coat"
    assert result["characters"][0]["relationships"] == {"Yan": "mentor"}
    assert result["locations"][0]["parent_location"] == "Glass City"
    assert result["locations"][0]["spatial_relations"] == {"north": "Clock Tower"}
    assert result["foreshadowing"][0]["reveal_window_start"] == 4
    assert result["foreshadowing"][0]["related_characters"] == ["Lin"]


def test_location_hierarchy_infers_obvious_parent_relations():
    locations = [
        {"name": "灵霄渡", "confidence": 0.9},
        {"name": "星照台", "confidence": 0.9},
        {"name": "玄天宗外域", "confidence": 0.9},
        {"name": "青云宗", "parent_location": "星照台", "location_type": "sect", "confidence": 0.9},
        {"name": "青云宗密道", "parent_location": "星照台", "location_type": "passage", "confidence": 0.9},
    ]
    content = (
        "凤溪在暮色里抵达灵霄渡。"
        "这里不是玄天宗山门，而是隶属于玄天宗外域的渡口，"
        "渡口中央有一座星照台。"
        "她把从青云宗密道里捡到的青铜铃藏进袖中。"
    )

    WorldviewProjectionService()._infer_location_hierarchy(locations, content)

    by_name = {item["name"]: item for item in locations}
    assert by_name["灵霄渡"]["parent_location"] == "玄天宗外域"
    assert by_name["星照台"]["parent_location"] == "灵霄渡"
    assert by_name["星照台"]["location_type"] == "platform"
    assert "parent_location" not in by_name["青云宗"]
    assert by_name["青云宗密道"]["parent_location"] == "青云宗"


@pytest_asyncio.fixture
async def seed_project(db):
    pid = uuid.uuid4()
    db.add(Project(id=pid, name="wps-test"))
    await db.flush()
    return pid


@pytest_asyncio.fixture
async def seed_chapter(db, seed_project):
    ch = Chapter(
        id=uuid.uuid4(),
        project_id=seed_project,
        chapter_number=1,
        title="test-chapter",
        content="林婉儿走进了庆庙，范闲紧随其后。",
        status="committed",
    )
    db.add(ch)
    await db.flush()
    return ch


async def _insert_observation(db, *, project_id, chapter_number=1, entity_type="character",
                               entity_name="范闲", confidence=0.9, generation_revision=1,
                               status="active", auto_promoted=False, core_entity_id=None,
                               confirmed_by=None, chapter_id=None):
    name_normalized = normalize_entity_name(entity_name)
    fp = compute_fingerprint(
        str(project_id), entity_type, name_normalized, chapter_number, 1, generation_revision,
    )
    obs = WorldviewObservation(
        id=uuid.uuid4(),
        project_id=project_id,
        chapter_number=chapter_number,
        chapter_id=chapter_id,
        entity_type=entity_type,
        entity_name=entity_name,
        entity_name_normalized=name_normalized,
        operation="new",
        payload={"description": f"{entity_type}: {entity_name}"},
        evidence_text="test evidence",
        fingerprint=fp,
        confidence=confidence,
        status=status,
        auto_promoted=auto_promoted,
        core_entity_id=core_entity_id,
        confirmed_by=confirmed_by,
        extraction_version=1,
        generation_revision=generation_revision,
    )
    db.add(obs)
    await db.flush()
    return obs


@pytest.mark.asyncio
async def test_same_chapter_rewrite_uses_new_generation_revision(
    db, seed_project, seed_chapter
):
    wps = WorldviewProjectionService()

    await _insert_observation(
        db, project_id=seed_project, chapter_number=1,
        entity_name="范闲", generation_revision=1,
        chapter_id=seed_chapter.id,
    )
    await db.commit()

    extraction_result = {
        "characters": [
            {"name": "林婉儿", "description": "庆国郡主", "confidence": 0.95,
             "operation": "new", "evidence_text": "林婉儿走进了庆庙"},
        ],
        "locations": [], "world_rules": [], "foreshadowing": [], "facts": [],
    }

    with patch.object(wps._extractor, "extract_from_chapter", new_callable=AsyncMock,
                      return_value=extraction_result):
        with patch.object(wps, "_dual_write_shell_seed", new_callable=AsyncMock, return_value=1):
            with patch.object(wps, "_auto_promote_observation", new_callable=AsyncMock, return_value=False):
                with patch.object(wps, "_sync_foreshadowing_from_observation", new_callable=AsyncMock):
                    with patch.object(wps._event_bus, "publish_event", new_callable=AsyncMock):
                        result = await wps.project_committed_chapter(
                            db, seed_project, 1,
                            project=None, generation_revision=2,
                        )

    assert result["observations_created"] == 1

    all_obs = (await db.execute(
        select(WorldviewObservation).where(
            WorldviewObservation.project_id == seed_project,
            WorldviewObservation.chapter_number == 1,
        )
    )).scalars().all()

    revisions = {obs.generation_revision for obs in all_obs}
    assert 1 in revisions
    assert 2 in revisions

    new_obs = [o for o in all_obs if o.generation_revision == 2]
    assert len(new_obs) == 1
    assert new_obs[0].entity_name == "林婉儿"


@pytest.mark.asyncio
async def test_same_chapter_rewrite_same_entity_new_fingerprint(
    db, seed_project, seed_chapter
):
    wps = WorldviewProjectionService()

    await _insert_observation(
        db, project_id=seed_project, chapter_number=1,
        entity_name="范闲", generation_revision=1,
        chapter_id=seed_chapter.id,
    )
    await db.commit()

    extraction_result = {
        "characters": [
            {"name": "范闲", "description": "更新描述", "confidence": 0.95,
             "operation": "update", "evidence_text": "范闲紧随其后"},
        ],
        "locations": [], "world_rules": [], "foreshadowing": [], "facts": [],
    }

    with patch.object(wps._extractor, "extract_from_chapter", new_callable=AsyncMock,
                      return_value=extraction_result):
        with patch.object(wps, "_dual_write_shell_seed", new_callable=AsyncMock, return_value=1):
            with patch.object(wps, "_auto_promote_observation", new_callable=AsyncMock, return_value=False):
                with patch.object(wps, "_sync_foreshadowing_from_observation", new_callable=AsyncMock):
                    with patch.object(wps._event_bus, "publish_event", new_callable=AsyncMock):
                        result = await wps.project_committed_chapter(
                            db, seed_project, 1,
                            project=None, generation_revision=2,
                        )

    assert result["observations_created"] == 1

    all_obs = (await db.execute(
        select(WorldviewObservation).where(
            WorldviewObservation.project_id == seed_project,
            WorldviewObservation.entity_name_normalized == "范闲",
        )
    )).scalars().all()

    assert len(all_obs) == 2
    fps = {obs.fingerprint for obs in all_obs}
    assert len(fps) == 2


@pytest.mark.asyncio
async def test_retract_old_revision_preserves_new_promoted_support(
    db, seed_project, seed_chapter
):
    wps = WorldviewProjectionService()
    old_obs = await _insert_observation(
        db,
        project_id=seed_project,
        chapter_number=1,
        entity_name="范闲",
        generation_revision=1,
        status="promoted",
        auto_promoted=True,
        core_entity_id="core-character-1",
        chapter_id=seed_chapter.id,
    )
    new_obs = await _insert_observation(
        db,
        project_id=seed_project,
        chapter_number=1,
        entity_name="范闲",
        generation_revision=2,
        status="promoted",
        auto_promoted=True,
        core_entity_id="core-character-1",
        chapter_id=seed_chapter.id,
    )
    await db.commit()

    result = await wps._retract_old_revisions(
        db,
        seed_project,
        1,
        1,
    )
    await db.commit()
    await db.refresh(old_obs)
    await db.refresh(new_obs)

    assert old_obs.status == "retracted"
    assert new_obs.status == "promoted"
    assert result["entities_archived"] == 0


@pytest.mark.asyncio
async def test_retry_pending_reuses_generation_revision(db, seed_project):
    wps = WorldviewProjectionService()

    outbox_item = ChapterEffectOutbox(
        id=uuid.uuid4(),
        project_id=seed_project,
        chapter_number=1,
        scene_index=999_999,
        effect_type="worldview_projection",
        effect_version=1,
        payload={"chapter_number": 1, "generation_revision": 3},
        idempotency_key=f"retry-test-{seed_project}",
        status="retryable_failed",
        attempts=1,
    )
    db.add(outbox_item)
    await db.commit()

    captured_revision = None

    async def fake_project(db, project_id, chapter_number, *, project=None, generation_revision=None):
        nonlocal captured_revision
        captured_revision = generation_revision
        return {"observations_created": 0, "seeds_written": 0, "auto_promoted": 0}

    with patch.object(wps, "project_committed_chapter", side_effect=fake_project):
        result = await wps.retry_pending_projection(db, seed_project, 1)

    assert result["retried"] == 1
    assert captured_revision == 3


@pytest.mark.asyncio
async def test_retry_all_pending_reuses_generation_revision(db, seed_project):
    wps = WorldviewProjectionService()

    for ch_num in (1, 2):
        db.add(ChapterEffectOutbox(
            id=uuid.uuid4(),
            project_id=seed_project,
            chapter_number=ch_num,
            scene_index=999_999,
            effect_type="worldview_projection",
            effect_version=1,
            payload={"chapter_number": ch_num, "generation_revision": ch_num * 2},
            idempotency_key=f"retry-all-{seed_project}-{ch_num}",
            status="retryable_failed",
            attempts=1,
        ))
    await db.commit()

    captured = {}

    async def fake_project(db, project_id, chapter_number, *, project=None, generation_revision=None):
        captured[chapter_number] = generation_revision
        return {"observations_created": 0, "seeds_written": 0, "auto_promoted": 0}

    with patch.object(wps, "project_committed_chapter", side_effect=fake_project):
        result = await wps.retry_all_pending(db, seed_project)

    assert result["retried"] == 2
    assert captured[1] == 2
    assert captured[2] == 4


@pytest.mark.asyncio
async def test_reject_auto_promoted_observation_deletes_core_card(
    db, seed_project, seed_chapter
):
    wps = WorldviewProjectionService()

    obs = await _insert_observation(
        db, project_id=seed_project, chapter_number=1,
        entity_name="范闲", confidence=0.9, generation_revision=1,
        auto_promoted=True, core_entity_id="core-char-123",
        confirmed_by="system",
        chapter_id=seed_chapter.id,
    )
    await db.commit()

    mock_core = AsyncMock()
    mock_core.delete_character_with_db = AsyncMock(return_value={"deleted": True})

    with patch("app.services.worldview_projection_service.CoreMemoryService",
               return_value=mock_core):
        result = await wps.reject_observation(db, str(obs.id), rejected_by="user", reason="不合适")

    assert result["rejected_by"] == "user"
    mock_core.delete_character_with_db.assert_awaited_once()

    refreshed = await db.get(WorldviewObservation, obs.id)
    assert refreshed.status == "rejected"
    assert refreshed.confirmed_by == "user"


@pytest.mark.asyncio
async def test_reject_auto_promoted_user_confirmed_sets_orphan_warning(
    db, seed_project, seed_chapter
):
    wps = WorldviewProjectionService()

    obs = await _insert_observation(
        db, project_id=seed_project, chapter_number=1,
        entity_name="范闲", confidence=0.9, generation_revision=1,
        auto_promoted=True, core_entity_id="core-char-456",
        confirmed_by="user",
        chapter_id=seed_chapter.id,
    )
    await db.commit()

    mock_core = AsyncMock()

    with patch("app.services.worldview_projection_service.CoreMemoryService",
               return_value=mock_core):
        result = await wps.reject_observation(db, str(obs.id), rejected_by="user", reason="误操作")

    assert result["rejected_by"] == "user"
    mock_core.delete_character_with_db.assert_not_awaited()

    refreshed = await db.get(WorldviewObservation, obs.id)
    assert refreshed.orphan_warning is True


@pytest.mark.asyncio
async def test_hard_retract_deletes_user_confirmed_auto_promoted_core_card(
    db, seed_project, seed_chapter
):
    core = CoreMemoryService()
    character = await core.create_character_with_db(
        db,
        str(seed_project),
        {
            "name": "Hard Delete Character",
            "aliases": [],
            "appearance": "chapter sourced",
            "personality": "",
            "desire": "",
            "deep_need": "",
            "arc": "",
            "relationships": {},
        },
    )
    obs = await _insert_observation(
        db,
        project_id=seed_project,
        chapter_number=1,
        entity_name="Hard Delete Character",
        entity_type="character",
        status="retracted",
        auto_promoted=True,
        core_entity_id=WorldviewProjectionService._core_entity_id(character.id),
        confirmed_by="user",
        chapter_id=seed_chapter.id,
    )
    await db.commit()

    wps = WorldviewProjectionService()
    with patch.object(wps._event_bus, "publish_event", new_callable=AsyncMock):
        result = await wps.retract_chapter_sources(db, seed_project, 1, hard=True)

    assert result["entities_archived"] == 1
    project = await db.get(Project, seed_project)
    await db.refresh(project)
    names = [item.get("name") for item in (project.core_data or {}).get("characters", [])]
    assert "Hard Delete Character" not in names
    refreshed = await db.get(WorldviewObservation, obs.id)
    assert refreshed.status == "retracted"
    assert refreshed.archived_at is not None


@pytest.mark.asyncio
async def test_extraction_failure_raises_worldview_extraction_error(
    db, seed_project, seed_chapter
):
    wps = WorldviewProjectionService()

    with patch.object(
        wps._extractor, "extract_from_chapter", new_callable=AsyncMock,
        side_effect=WorldviewExtractionError("LLM returned empty response"),
    ):
        with pytest.raises(WorldviewExtractionError, match="LLM returned empty response"):
            await wps.project_committed_chapter(
                db, seed_project, 1, project=None, generation_revision=1,
            )


@pytest.mark.asyncio
async def test_extraction_parse_failure_raises_error(
    db, seed_project, seed_chapter
):
    wps = WorldviewProjectionService()

    with patch.object(
        wps._extractor, "extract_from_chapter", new_callable=AsyncMock,
        side_effect=WorldviewExtractionError("Failed to parse LLM response as valid worldview JSON"),
    ):
        with pytest.raises(WorldviewExtractionError):
            await wps.project_committed_chapter(
                db, seed_project, 1, project=None, generation_revision=1,
            )


@pytest.mark.asyncio
async def test_next_generation_revision_increments(db, seed_project, seed_chapter):
    result = await db.execute(
        select(func.max(WorldviewObservation.generation_revision)).where(
            WorldviewObservation.project_id == seed_project,
            WorldviewObservation.chapter_number == 1,
        )
    )
    assert (result.scalar() or 0) == 0

    await _insert_observation(
        db, project_id=seed_project, chapter_number=1,
        generation_revision=1, chapter_id=seed_chapter.id,
    )
    await db.flush()

    result = await db.execute(
        select(func.max(WorldviewObservation.generation_revision)).where(
            WorldviewObservation.project_id == seed_project,
            WorldviewObservation.chapter_number == 1,
        )
    )
    assert result.scalar() == 1

    await _insert_observation(
        db, project_id=seed_project, chapter_number=1,
        entity_name="林婉儿", generation_revision=2,
        chapter_id=seed_chapter.id,
    )
    await db.flush()

    result = await db.execute(
        select(func.max(WorldviewObservation.generation_revision)).where(
            WorldviewObservation.project_id == seed_project,
            WorldviewObservation.chapter_number == 1,
        )
    )
    assert result.scalar() == 2


@pytest.mark.asyncio
async def test_retract_then_reproject_uses_next_revision(
    db, seed_project, seed_chapter
):
    wps = WorldviewProjectionService()

    obs1 = await _insert_observation(
        db, project_id=seed_project, chapter_number=1,
        entity_name="范闲", generation_revision=1,
        chapter_id=seed_chapter.id,
    )
    await db.commit()

    with patch.object(wps._event_bus, "publish_event", new_callable=AsyncMock):
        retract_result = await wps.retract_chapter_sources(db, seed_project, 1, hard=True)

    assert retract_result["observations_retracted"] == 1

    refreshed = await db.get(WorldviewObservation, obs1.id)
    assert refreshed.status == "retracted"

    extraction_result = {
        "characters": [
            {"name": "范闲", "description": "重写后描述", "confidence": 0.95,
             "operation": "new", "evidence_text": "范闲出现了"},
        ],
        "locations": [], "world_rules": [], "foreshadowing": [], "facts": [],
    }

    with patch.object(wps._extractor, "extract_from_chapter", new_callable=AsyncMock,
                      return_value=extraction_result):
        with patch.object(wps, "_dual_write_shell_seed", new_callable=AsyncMock, return_value=1):
            with patch.object(wps, "_auto_promote_observation", new_callable=AsyncMock, return_value=False):
                with patch.object(wps, "_sync_foreshadowing_from_observation", new_callable=AsyncMock):
                    with patch.object(wps._event_bus, "publish_event", new_callable=AsyncMock):
                        project_result = await wps.project_committed_chapter(
                            db, seed_project, 1,
                            project=None, generation_revision=2,
                        )

    assert project_result["observations_created"] == 1

    all_obs = (await db.execute(
        select(WorldviewObservation).where(
            WorldviewObservation.project_id == seed_project,
            WorldviewObservation.chapter_number == 1,
            WorldviewObservation.entity_name_normalized == "范闲",
        )
    )).scalars().all()

    assert len(all_obs) == 2
    statuses = {obs.status for obs in all_obs}
    assert "retracted" in statuses
    assert "active" in statuses


@pytest.mark.asyncio
async def test_retry_failure_goes_to_retryable_failed(db, seed_project):
    wps = WorldviewProjectionService()

    outbox_item = ChapterEffectOutbox(
        id=uuid.uuid4(),
        project_id=seed_project,
        chapter_number=1,
        scene_index=999_999,
        effect_type="worldview_projection",
        effect_version=1,
        payload={"chapter_number": 1, "generation_revision": 1},
        idempotency_key=f"retry-fail-{seed_project}",
        status="retryable_failed",
        attempts=1,
    )
    db.add(outbox_item)
    await db.commit()

    with patch.object(
        wps, "project_committed_chapter", new_callable=AsyncMock,
        side_effect=WorldviewExtractionError("LLM unavailable"),
    ):
        result = await wps.retry_pending_projection(db, seed_project, 1)

    assert result["retried"] == 0

    refreshed = await db.get(ChapterEffectOutbox, outbox_item.id)
    assert refreshed.status == "retryable_failed"
    assert refreshed.attempts == 2
    assert "LLM unavailable" in refreshed.error_message


@pytest.mark.asyncio
async def test_atomic_projection_rolls_back_partial_observations(
    db, seed_project, seed_chapter
):
    wps = WorldviewProjectionService()
    extraction_result = {
        "characters": [
            {
                "name": "rollback-character",
                "description": "must disappear after rollback",
                "confidence": 0.2,
                "operation": "new",
                "evidence_text": "rollback evidence",
            },
        ],
        "locations": [], "world_rules": [], "foreshadowing": [], "facts": [],
    }

    with patch.object(
        wps._extractor, "extract_from_chapter", new_callable=AsyncMock,
        return_value=extraction_result,
    ):
        with patch.object(
            wps._event_bus, "publish_event", new_callable=AsyncMock,
            side_effect=RuntimeError("event bus unavailable"),
        ):
            with pytest.raises(RuntimeError, match="event bus unavailable"):
                await wps.project_committed_chapter_atomically(
                    db, seed_project, 1, generation_revision=1,
                )

    observations = (
        await db.execute(
            select(WorldviewObservation).where(
                WorldviewObservation.project_id == seed_project,
            )
        )
    ).scalars().all()
    seeds = (
        await db.execute(
            select(DetailSeed).where(DetailSeed.project_id == seed_project)
        )
    ).scalars().all()
    assert observations == []
    assert seeds == []


@pytest.mark.asyncio
async def test_auto_promotion_failure_is_retryable(
    db, seed_project, seed_chapter
):
    wps = WorldviewProjectionService()
    extraction_result = {
        "characters": [
            {
                "name": "promotion-failure",
                "description": "must make the projection fail",
                "confidence": 0.99,
                "operation": "new",
                "evidence_text": "promotion evidence",
            },
        ],
        "locations": [], "world_rules": [], "foreshadowing": [], "facts": [],
    }

    with patch.object(
        wps._extractor, "extract_from_chapter", new_callable=AsyncMock,
        return_value=extraction_result,
    ):
        with patch.object(
            wps, "_auto_promote_observation", new_callable=AsyncMock,
            side_effect=RuntimeError("core unavailable"),
        ):
            with pytest.raises(RuntimeError, match="core unavailable"):
                await wps.project_committed_chapter_atomically(
                    db, seed_project, 1, generation_revision=1,
                )

    count = await db.scalar(
        select(func.count()).select_from(WorldviewObservation).where(
            WorldviewObservation.project_id == seed_project,
        )
    )
    assert count == 0


@pytest.mark.asyncio
async def test_structured_projection_writes_rich_character_and_location_tree(
    db, seed_project, seed_chapter
):
    wps = WorldviewProjectionService()
    extraction_result = {
        "characters": [
            {
                "name": "Lin",
                "aliases": ["L"],
                "operation": "new",
                "description": "A careful witness",
                "appearance": "silver coat",
                "personality": "guarded",
                "desire": "find the gate",
                "deep_need": "trust others",
                "arc": "isolation to alliance",
                "relationships": {"Yan": "mentor"},
                "evidence_text": "Lin entered the archive.",
                "confidence": 0.95,
            }
        ],
        "locations": [
            {
                "name": "Glass City",
                "operation": "new",
                "description": "A parent city of mirrored streets",
                "location_type": "city",
                "atmosphere": "bright and brittle",
                "evidence_text": "The East Archive waited inside Glass City.",
                "confidence": 0.95,
            },
            {
                "name": "East Archive",
                "operation": "new",
                "description": "A sealed room",
                "parent_location": "Glass City",
                "location_type": "building",
                "atmosphere": "cold",
                "function": "stores forbidden maps",
                "spatial_relations": {"north": "Clock Tower"},
                "rules": ["no open flames"],
                "evidence_text": "The East Archive waited inside Glass City.",
                "confidence": 0.95,
            },
        ],
        "world_rules": [],
        "foreshadowing": [],
        "facts": [],
    }

    with patch.object(wps._extractor, "extract_from_chapter", new_callable=AsyncMock, return_value=extraction_result):
        with patch.object(wps._event_bus, "publish_event", new_callable=AsyncMock):
            result = await wps.project_committed_chapter(
                db, seed_project, 1, project=None, generation_revision=1,
            )

    assert result["auto_promoted"] == 3

    project = await db.get(Project, seed_project)
    await db.refresh(project)
    core = project.core_data or {}
    character = next(c for c in core.get("characters", []) if c["name"] == "Lin")
    assert character["appearance"] == "silver coat"
    assert character["personality"] == "guarded"
    assert character["relationships"] == {"Yan": "mentor"}

    child = next(loc for loc in core.get("locations", []) if loc["name"] == "East Archive")
    assert child["parent_location"] == "Glass City"
    assert child["location_type"] == "building"
    assert child["spatial_relations"] == {"north": "Clock Tower"}

    with patch.object(wps._event_bus, "publish_event", new_callable=AsyncMock):
        retract_result = await wps.retract_chapter_sources(db, seed_project, 1)

    assert retract_result["observations_retracted"] == 3
    project = await db.get(Project, seed_project)
    await db.refresh(project)
    core = project.core_data or {}
    assert all(c["name"] != "Lin" for c in core.get("characters", []))
    assert all(loc["name"] not in {"Glass City", "East Archive"} for loc in core.get("locations", []))


def test_aborted_foreshadowing_is_terminal_in_canonical_state_machine():
    from app.services.foreshadowing_service import FORESHADOWING_STATE_TRANSITIONS

    assert FORESHADOWING_STATE_TRANSITIONS["aborted"] == set()


@pytest.mark.asyncio
async def test_foreshadowing_observation_records_evidence_clue(db):
    project_id = uuid.uuid4()
    db.add(Project(id=project_id, name="foreshadowing-clue-sync-test"))
    await db.commit()

    updates = await ForeshadowingUpsertService().upsert_from_observation(
        db,
        project_id,
        5,
        [
            {
                "name": "Mirror clue",
                "description": "The mirror reflects a hidden hand.",
                "evidence_text": "The mirror showed a hand that was not in the room.",
                "scene_index": 1,
                "operation": "new",
                "salience": "high",
                "confidence": 0.95,
            }
        ],
    )
    await db.commit()

    assert len(updates) == 1
    line = (await db.execute(
        select(ForeshadowingLine).where(ForeshadowingLine.project_id == project_id)
    )).scalar_one()
    clue = (await db.execute(
        select(ForeshadowingClue).where(ForeshadowingClue.foreshadowing_line_id == line.id)
    )).scalar_one()

    assert line.clues_placed == 1
    assert clue.chapter_number == 5
    assert clue.scene_index == 1
    assert clue.clue_text == "The mirror showed a hand that was not in the room."

    events = (await db.execute(
        select(CrossSystemEvent).where(CrossSystemEvent.project_id == project_id)
    )).scalars().all()
    event_types = {event.event_type for event in events}
    assert "FORESHADOWING_WINDOW_CHANGED.v1" in event_types
    assert "FORESHADOWING_CLUE_RECORDED.v1" in event_types
    clue_event = next(event for event in events if event.event_type == "FORESHADOWING_CLUE_RECORDED.v1")
    assert clue_event.payload["foreshadowing_name"] == "Mirror clue"
    assert clue_event.payload["evidence_text"] == "The mirror showed a hand that was not in the room."
