from app.services.scene_provenance import build_scene_provenance, normalize_scene_contract, render_scene_provenance


def test_build_scene_provenance_keeps_layers_separate():
    contract = {
        "scene_id": "c1-s1",
        "chapter_number": 1,
        "scene_index": 0,
        "pov_character": "Feng Xi",
        "goal": "escape",
        "conflict": "hunt",
        "must_show": ["open the door"],
        "forbidden": ["flashback dump"],
        "ending_state": "on the road",
        "location_anchor": "north road shrine",
        "current_location": "shrine",
        "destination_location": "Xuantian Sect",
        "distance_state": "about thirty li",
        "temporal_anchor": "dawn",
        "opening_state": "starts at the shrine",
        "forbidden_recap_events": ["left the sect"],
        "clues": [
            {
                "description": "carved note on the tree",
                "source_actor": "servant",
                "placement_time": "before dawn",
                "discovery_condition": "only visible at the tree",
            }
        ],
    }
    chapter_state = {
        "established_facts": ["the escape already happened"],
        "character_states": {"Feng Xi": "tired but alert"},
        "completed_events": ["left the sect"],
        "active_constraints": [{"description": "stay hidden"}],
    }
    character_cards = [
        {
            "name": "Lin Ruoyun",
            "original_fate": {
                "cause": "dies in chapter 3",
                "timeline": "chapter 3",
                "antagonist": "Feng Xi",
                "key_events": ["betrayal", "fall"],
            },
        }
    ]

    provenance = build_scene_provenance(contract, chapter_state, character_cards)

    assert provenance["current_facts"]["established_facts"] == ["the escape already happened"]
    assert provenance["current_facts"]["completed_events"] == ["left the sect"]
    assert provenance["original_facts"]["character_fates"][0]["name"] == "Lin Ruoyun"
    assert provenance["spatial_anchor"]["destination_location"] == "Xuantian Sect"
    assert provenance["timeline_anchor"]["temporal_anchor"] == "dawn"
    assert provenance["clues"][0]["source_actor"] == "servant"

    rendered = render_scene_provenance(provenance)
    assert "当前事实层" in rendered
    assert "参考设定层" in rendered
    assert "线索层" in rendered


def test_normalize_scene_contract_adds_provenance_and_defaults():
    contract = {
        "scene_beat": {"location": "forest hut", "goal": "hide"},
        "goal": "hide",
        "conflict": "chase",
    }

    normalized = normalize_scene_contract(
        contract,
        chapter_number=2,
        scene_index=1,
        scene_beat={"location": "forest hut"},
        chapter_state={"completed_events": ["left home"]},
        pov_character="Feng Xi",
        character_cards=[],
    )

    assert normalized["chapter_number"] == 2
    assert normalized["scene_index"] == 1
    assert normalized["pov_character"] == "Feng Xi"
    assert normalized["current_location"] == "forest hut"
    assert normalized["location_anchor"]
    assert normalized["scene_provenance"]["current_facts"]["completed_events"] == ["left home"]


def test_normalize_scene_contract_rejects_shorthand_provenance_overrides():
    normalized = normalize_scene_contract(
        {
            "scene_provenance": {
                "current_facts": ["planner shorthand"],
                "original_facts": ["planner shorthand"],
                "spatial_anchor": "room",
                "timeline_anchor": "dawn",
            },
        },
        chapter_number=1,
        scene_index=0,
        scene_beat={"location": "room"},
        chapter_state={"completed_events": ["existing event"]},
        pov_character="Feng Xi",
    )

    provenance = normalized["scene_provenance"]
    assert provenance["current_facts"]["completed_events"] == ["existing event"]
    assert provenance["original_facts"] == {"character_fates": []}
    assert provenance["spatial_anchor"]["current_location"] == "room"
    assert provenance["timeline_anchor"]["temporal_anchor"]
