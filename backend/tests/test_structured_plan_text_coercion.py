from unittest.mock import AsyncMock

import pytest

from app.services.text_coercion import to_entity_name, to_search_text
from app.skills.scene_preparation import ScenePreparationSkill
from app.skills.shell_search import ShellSearchSkill


def test_text_coercion_supports_structured_values():
    assert "escape" in to_search_text({"desire": "escape", "obstacle": "guards"})
    assert to_entity_name({"name": "north road shrine", "id": "loc_001"}) == "north road shrine"


def test_scene_preparation_extracts_abilities_from_structured_scene_fields():
    skill = ScenePreparationSkill()

    abilities = skill._extract_abilities(
        {
            "conflict": {"obstacle": "战斗中避开守卫"},
            "outcome": {"result": "魔法封印已经解除"},
        }
    )

    assert "combat" in abilities
    assert "magic" in abilities


def test_scene_preparation_filters_rules_with_structured_fields():
    skill = ScenePreparationSkill()

    rules = skill._filter_rules_by_relevance(
        [
            {
                "name": {"label": "north road rule"},
                "description": {"text": "applies at north road shrine"},
                "category": {"kind": "magic"},
            }
        ],
        pov_character="Feng Xi",
        involved_abilities=["magic"],
        involved_locations=[{"name": "north road shrine"}],
    )

    assert len(rules) == 1


@pytest.mark.asyncio
async def test_shell_search_accepts_structured_goal_and_conflict():
    skill = ShellSearchSkill()
    skill.shell_service.search_details = AsyncMock(return_value=[])

    await skill.search_relevant_details(
        "project-1",
        {
            "scene_beat": {
                "goal": {"action": "escape"},
                "conflict": {"obstacle": "guards"},
            }
        },
    )

    query = skill.shell_service.search_details.await_args.kwargs["query"]
    assert "escape" in query
    assert "guards" in query
