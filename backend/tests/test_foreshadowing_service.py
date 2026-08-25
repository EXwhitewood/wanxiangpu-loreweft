import uuid
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.foreshadowing_service import (
    ForeshadowingService,
    FORESHADOWING_STATE_TRANSITIONS,
)
from app.db.db_models import CrossSystemEvent, ForeshadowingLine, CharacterCognitiveState
from sqlalchemy import select


@pytest_asyncio.fixture
async def service():
    return ForeshadowingService()


@pytest_asyncio.fixture
async def sample_line(project_in_db, db, service):
    data = {
        "name": "身份秘密",
        "description": "主角其实是魔族后裔",
        "status": "planned",
        "priority": "major",
        "secret": {
            "canonical_statement": "主角其实是魔族后裔",
            "truth_type": "identity",
            "impact_level": "major",
            "spoiler_scope": {"characters": ["林逸", "苏清月"]},
        },
        "timeline": {
            "bury_window": [3, 10],
            "reveal_window": [45, 50],
            "latest_safe_reveal_chapter": 50,
        },
        "narrative_structure": {
            "total_clues_planned": 5,
            "bury_rhythm": "gradual",
        },
    }
    return await service.create_foreshadowing_line(project_in_db, data, db)


class TestForeshadowingCRUD:
    @pytest.mark.asyncio
    async def test_create_line(self, sample_line):
        assert sample_line["name"] == "身份秘密"
        assert sample_line["status"] == "planned"
        assert sample_line["secret_truth_type"] == "identity"
        assert sample_line["secret_canonical_statement"] == "主角其实是魔族后裔"
        assert sample_line["bury_window_start"] == 3
        assert sample_line["bury_window_end"] == 10
        assert sample_line["reveal_window_start"] == 45
        assert sample_line["total_clues_planned"] == 5

    @pytest.mark.asyncio
    async def test_create_duplicate_returns_existing(self, project_in_db, db, service, sample_line):
        result = await service.create_foreshadowing_line(project_in_db, {
            "name": "身份秘密",
            "description": "重复创建",
        }, db)
        assert result["id"] == sample_line["id"]

    @pytest.mark.asyncio
    async def test_get_by_name(self, project_in_db, db, service, sample_line):
        result = await service.get_foreshadowing_by_name(project_in_db, "身份秘密", db)
        assert result is not None
        assert result["name"] == "身份秘密"

    @pytest.mark.asyncio
    async def test_get_by_id(self, db, service, sample_line):
        result = await service.get_foreshadowing_by_id(sample_line["id"], db)
        assert result is not None
        assert result["name"] == "身份秘密"

    @pytest.mark.asyncio
    async def test_list_lines(self, project_in_db, db, service, sample_line):
        lines = await service.list_foreshadowing_lines(project_in_db, db)
        assert len(lines) >= 1
        names = [l["name"] for l in lines]
        assert "身份秘密" in names

    @pytest.mark.asyncio
    async def test_list_lines_filter_status(self, project_in_db, db, service, sample_line):
        active = await service.list_foreshadowing_lines(project_in_db, db, status="active")
        assert all(l["status"] == "active" for l in active)

    @pytest.mark.asyncio
    async def test_create_requires_name(self, project_in_db, db, service):
        with pytest.raises(ValueError, match="name is required"):
            await service.create_foreshadowing_line(project_in_db, {"name": ""}, db)


class TestStateMachine:
    @pytest.mark.asyncio
    async def test_valid_transitions(self, project_in_db, db, service, sample_line):
        assert sample_line["status"] == "planned"

        line = await service.transition_state(project_in_db, sample_line["id"], "active", "test", db)
        assert line["status"] == "active"

        line = await service.transition_state(project_in_db, sample_line["id"], "dormant", "test", db)
        assert line["status"] == "dormant"

        line = await service.transition_state(project_in_db, sample_line["id"], "revealing", "test", db)
        assert line["status"] == "revealing"

        line = await service.transition_state(project_in_db, sample_line["id"], "resolved", "test", db)
        assert line["status"] == "resolved"

    @pytest.mark.asyncio
    async def test_invalid_transition(self, project_in_db, db, service, sample_line):
        with pytest.raises(ValueError, match="Invalid transition"):
            await service.transition_state(project_in_db, sample_line["id"], "resolved", "test", db)

    @pytest.mark.asyncio
    async def test_abort_from_any_active_state(self, project_in_db, db, service, sample_line):
        line = await service.transition_state(project_in_db, sample_line["id"], "active", "test", db)
        line = await service.transition_state(project_in_db, sample_line["id"], "aborted", "test", db)
        assert line["status"] == "aborted"

    @pytest.mark.asyncio
    async def test_aborted_is_terminal(self, project_in_db, db, service):
        data = {"name": "终止测试", "description": "test", "status": "planned"}
        line = await service.create_foreshadowing_line(project_in_db, data, db)
        await service.transition_state(project_in_db, line["id"], "aborted", "test", db)
        with pytest.raises(ValueError, match="Invalid transition"):
            await service.transition_state(project_in_db, line["id"], "active", "test", db)

    @pytest.mark.asyncio
    async def test_revision_count_increments(self, project_in_db, db, service, sample_line):
        initial = sample_line.get("revision_count", 0)
        await service.transition_state(project_in_db, sample_line["id"], "active", "test", db)
        result = await service.get_foreshadowing_by_id(sample_line["id"], db)
        assert result["revision_count"] == initial + 1

    @pytest.mark.asyncio
    async def test_advance_lifecycle_for_chapter_moves_window_states_and_publishes_events(
        self, project_in_db, db, service, sample_line
    ):
        updates = await service.advance_lifecycle_for_chapter(
            project_in_db,
            5,
            db,
            changed_by="test_lifecycle",
        )
        assert updates == [{
            "id": sample_line["id"],
            "name": "身份秘密",
            "from_status": "planned",
            "to_status": "active",
            "reason": "bury_window_open",
            "chapter_number": 5,
        }]

        line = await service.get_foreshadowing_by_id(sample_line["id"], db)
        assert line["status"] == "active"

        updates = await service.advance_lifecycle_for_chapter(
            project_in_db,
            20,
            db,
            changed_by="test_lifecycle",
        )
        assert updates[0]["from_status"] == "active"
        assert updates[0]["to_status"] == "dormant"
        assert updates[0]["reason"] == "bury_window_closed"

        updates = await service.advance_lifecycle_for_chapter(
            project_in_db,
            47,
            db,
            changed_by="test_lifecycle",
        )
        assert updates[0]["from_status"] == "dormant"
        assert updates[0]["to_status"] == "revealing"
        assert updates[0]["reason"] == "reveal_window_open"

        events = (await db.execute(
            select(CrossSystemEvent).where(CrossSystemEvent.project_id == project_in_db)
        )).scalars().all()
        event_types = [event.event_type for event in events]
        assert "FORESHADOWING_WINDOW_CHANGED.v1" in event_types
        assert "FORESHADOWING_REVEALED.v1" in event_types


class TestActionableForScene:
    @pytest.mark.asyncio
    async def test_plant_in_bury_window(self, project_in_db, db, service, sample_line):
        await service.transition_state(project_in_db, sample_line["id"], "active", "test", db)
        actionable = await service.list_actionable_for_scene(project_in_db, 5, db)
        actions = {a["name"]: a["action"] for a in actionable}
        assert actions.get("身份秘密") == "plant"

    @pytest.mark.asyncio
    async def test_reveal_in_reveal_window(self, project_in_db, db, service, sample_line):
        await service.transition_state(project_in_db, sample_line["id"], "active", "test", db)
        await service.transition_state(project_in_db, sample_line["id"], "dormant", "test", db)
        await service.transition_state(project_in_db, sample_line["id"], "revealing", "test", db)
        actionable = await service.list_actionable_for_scene(project_in_db, 47, db)
        actions = {a["name"]: a["action"] for a in actionable}
        assert actions.get("身份秘密") == "reveal"

    @pytest.mark.asyncio
    async def test_no_action_outside_windows(self, project_in_db, db, service, sample_line):
        await service.transition_state(project_in_db, sample_line["id"], "active", "test", db)
        actionable = await service.list_actionable_for_scene(project_in_db, 30, db)
        names = [a["name"] for a in actionable]
        assert "身份秘密" not in names


class TestCharacterCognitiveState:
    @pytest.mark.asyncio
    async def test_set_and_get(self, project_in_db, db, service, sample_line):
        state = await service.set_character_cognitive_state(
            project_in_db, sample_line["id"], "林逸", "suspicious", 5, db,
            cognitive_level="partial_clue", event="发现异常",
        )
        assert state["character_name"] == "林逸"
        assert state["cognitive_level"] == "partial_clue"
        assert state["cognitive_status"] == "suspicious"

    @pytest.mark.asyncio
    async def test_time_slice_query(self, project_in_db, db, service, sample_line):
        await service.set_character_cognitive_state(
            project_in_db, sample_line["id"], "林逸", "blind", 1, db,
            cognitive_level="fully_blind", event="初始",
        )
        await service.set_character_cognitive_state(
            project_in_db, sample_line["id"], "林逸", "suspicious", 10, db,
            cognitive_level="partial_clue", event="发现异常",
        )

        states_ch5 = await service.get_character_cognitive_states(
            project_in_db, "林逸", db, chapter_number=5,
        )
        assert len(states_ch5) == 1
        assert states_ch5[0]["cognitive_level"] == "fully_blind"

        states_ch15 = await service.get_character_cognitive_states(
            project_in_db, "林逸", db, chapter_number=15,
        )
        assert len(states_ch15) == 1
        assert states_ch15[0]["cognitive_level"] == "partial_clue"

    @pytest.mark.asyncio
    async def test_list_for_foreshadowing(self, project_in_db, db, service, sample_line):
        await service.set_character_cognitive_state(
            project_in_db, sample_line["id"], "林逸", "blind", 1, db,
        )
        await service.set_character_cognitive_state(
            project_in_db, sample_line["id"], "苏清月", "informed", 1, db,
            cognitive_level="fully_aware",
        )
        states = await service.list_cognitive_states_for_foreshadowing(
            project_in_db, sample_line["id"], db,
        )
        names = {s["character_name"] for s in states}
        assert "林逸" in names
        assert "苏清月" in names

