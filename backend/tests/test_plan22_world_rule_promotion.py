"""World-rule promotion is owned by the post-commit worldview projection."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.worldview_projection_service import (
    AUTO_PROMOTABLE_TYPES,
    AUTO_PROMOTE_THRESHOLD,
    WorldviewProjectionService,
)


def _observation(*, confidence: float = 0.9, priority: str = "minor") -> MagicMock:
    observation = MagicMock()
    observation.entity_type = "world_rule"
    observation.confidence = confidence
    observation.auto_promoted = False
    observation.core_entity_id = None
    observation.project_id = "00000000-0000-0000-0000-000000000001"
    observation.entity_name = "精神力消耗"
    observation.payload = {
        "description": "魔法消耗精神力",
        "category": "magic",
        "constraints": ["不能连续施放"],
        "priority": priority,
    }
    observation.chapter_number = 5
    return observation


def test_world_rule_is_supported_by_canonical_auto_promotion_policy():
    assert "world_rule" in AUTO_PROMOTABLE_TYPES
    assert AUTO_PROMOTE_THRESHOLD["world_rule"] == 0.85


@pytest.mark.asyncio
async def test_high_confidence_world_rule_creates_minor_core_rule():
    service = WorldviewProjectionService()
    observation = _observation()
    core_service = MagicMock()
    core_service.list_world_rules_with_db = AsyncMock(return_value=[])
    core_service.create_world_rule_with_db = AsyncMock(return_value={"id": "rule-new"})

    with patch(
        "app.services.worldview_projection_service.CoreMemoryService",
        return_value=core_service,
    ):
        promoted = await service._auto_promote_observation(MagicMock(), observation)

    assert promoted is True
    assert observation.auto_promoted is True
    payload = core_service.create_world_rule_with_db.await_args.args[2]
    assert payload["name"] == "精神力消耗"
    assert payload["priority"] == "minor"


@pytest.mark.asyncio
async def test_existing_world_rule_is_updated_instead_of_duplicated():
    service = WorldviewProjectionService()
    observation = _observation()
    core_service = MagicMock()
    core_service.list_world_rules_with_db = AsyncMock(return_value=[{
        "id": "rule-existing",
        "name": "精神力消耗",
        "description": "",
        "constraints": ["旧约束"],
    }])
    core_service.update_world_rule_with_db = AsyncMock()
    core_service.create_world_rule_with_db = AsyncMock()

    with patch(
        "app.services.worldview_projection_service.CoreMemoryService",
        return_value=core_service,
    ):
        promoted = await service._auto_promote_observation(MagicMock(), observation)

    assert promoted is True
    core_service.update_world_rule_with_db.assert_awaited_once()
    core_service.create_world_rule_with_db.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("confidence", "priority"),
    [(0.7, "minor"), (0.95, "critical"), (0.95, "high")],
)
async def test_unsafe_world_rules_are_not_auto_promoted(confidence: float, priority: str):
    service = WorldviewProjectionService()
    observation = _observation(confidence=confidence, priority=priority)
    core_service = MagicMock()
    core_service.create_world_rule_with_db = AsyncMock()

    with patch(
        "app.services.worldview_projection_service.CoreMemoryService",
        return_value=core_service,
    ):
        promoted = await service._auto_promote_observation(MagicMock(), observation)

    assert promoted is False
    core_service.create_world_rule_with_db.assert_not_awaited()
