from app.models.story_state import StoryState
from app.services.state_manager import normalize_state_patch


def test_normalizes_freeform_entity_fields_without_project_specific_rules():
    patch = {
        "objective_state": {
            "character_a": {
                "位置": "临时住所",
                "情绪": ["警觉", "疲惫"],
                "身体状态": "轻伤",
                "状态": "正在观察环境",
            }
        }
    }

    normalized = normalize_state_patch(patch)
    entity = normalized["objective_state"]["character_a"]

    assert entity["location"] == "临时住所"
    assert entity["emotional_state"] == "警觉、疲惫"
    assert entity["physical_state"] == "轻伤"
    assert entity["extra"]["状态"] == "正在观察环境"
    assert StoryState(**normalized).objective_state["character_a"].location == "临时住所"


def test_flattens_legacy_entities_wrapper_and_normalizes_scalars():
    patch = {
        "objective_state": {
            "entities": {
                "character_b": {
                    "items": "钥匙",
                    "存活": "false",
                }
            }
        }
    }

    normalized = normalize_state_patch(patch)
    entity = normalized["objective_state"]["character_b"]

    assert entity["inventory"] == ["钥匙"]
    assert entity["alive"] is False
    assert "entities" not in normalized["objective_state"]


def test_preserves_location_state_shape():
    patch = {
        "objective_state": {
            "location_a": {
                "atmosphere": "安静",
                "light_source": "窗外晨光",
                "objects_present": ["桌子"],
                "condition": "完好",
            }
        }
    }

    normalized = normalize_state_patch(patch)

    assert normalized == patch
    assert StoryState(**normalized).objective_state["location_a"].condition == "完好"


def test_normalizes_location_state_inventory_scalar():
    normalized = normalize_state_patch({
        "objective_state": {
            "location_b": {
                "objects_present": "桌子",
                "condition": "完好",
            }
        }
    })

    assert normalized["objective_state"]["location_b"]["objects_present"] == ["桌子"]
    assert StoryState(**normalized).objective_state["location_b"].objects_present == ["桌子"]


def test_wraps_scalar_entity_value_as_extra_summary():
    normalized = normalize_state_patch({"objective_state": {"character_c": "昏迷"}})

    assert normalized["objective_state"]["character_c"] == {"extra": {"summary": "昏迷"}}
    assert StoryState(**normalized).objective_state["character_c"].extra["summary"] == "昏迷"


def test_preserves_explicit_null_when_clearing_a_state_field():
    normalized = normalize_state_patch({"objective_state": {"character_d": {"location": None}}})

    assert normalized["objective_state"]["character_d"]["location"] is None


def test_normalizes_adjacent_top_level_patch_shapes():
    normalized = normalize_state_patch({
        "narrative_time": {"elapsed": "2小时"},
        "completed_events": "事件完成",
        "active_constraints": {"description": "约束"},
        "subjective_views": {"character_e": "误以为安全"},
    })

    assert normalized["narrative_time"] == '{"elapsed": "2小时"}'
    assert normalized["completed_events"] == ["事件完成"]
    assert normalized["active_constraints"] == [{"description": "约束"}]
    assert normalized["subjective_views"]["character_e"] == {
        "believed_state": {"summary": "误以为安全"}
    }
    assert StoryState(**normalized).narrative_time == '{"elapsed": "2小时"}'


def test_normalizes_prefixed_scene_and_chapter_indexes():
    normalized = normalize_state_patch({
        "active_chapter": "chapter_12",
        "active_scene": "scene_3",
    })

    assert normalized["active_chapter"] == 12
    assert normalized["active_scene"] == 3
    assert StoryState(**normalized).active_scene == 3


def test_drops_unrecognized_scene_index_instead_of_blocking_commit():
    normalized = normalize_state_patch({"active_scene": "next_scene"})

    assert "active_scene" not in normalized
    assert StoryState(**normalized).active_scene == 1


def test_drops_unrecognized_chapter_index_instead_of_blocking_commit():
    normalized = normalize_state_patch({"active_chapter": "next_chapter"})

    assert "active_chapter" not in normalized
    assert StoryState(**normalized).active_chapter == 1


def test_normalizes_event_and_constraint_items():
    normalized = normalize_state_patch({
        "completed_events": [{"description": "事件完成"}, "另一事件"],
        "active_constraints": ["必须低声交谈", {"description": "门已锁"}],
    })

    assert normalized["completed_events"] == ["事件完成", "另一事件"]
    assert normalized["active_constraints"] == [
        {"description": "必须低声交谈"},
        {"description": "门已锁"},
    ]
    assert StoryState(**normalized).completed_events[0] == "事件完成"
