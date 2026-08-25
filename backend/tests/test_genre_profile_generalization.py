from app.agents.scene_critic import SceneCriticAgent
from app.services.genre_profile_service import GenreProfileService
from app.services.scene_provenance import normalize_scene_contract_v2
from app.skills.attention_director import AttentionDirectorSkill
from app.skills.scene_preparation import ScenePreparationSkill


def test_general_profile_activation_reminder_has_no_power_system():
    profile = GenreProfileService().get_default_profile()

    reminder = AttentionDirectorSkill()._build_activation_reminder(
        {"pov_character": "林夏"},
        relevant_rules=None,
        genre_profile=profile,
    )

    assert "力量体系" not in reminder
    assert "不得违反已确立的世界规则" in reminder


def test_xianxia_profile_keeps_power_system_prohibition():
    service = GenreProfileService()
    profile = service._load_yaml("xianxia")

    reminder = AttentionDirectorSkill()._build_activation_reminder(
        {"pov_character": "林夏"},
        relevant_rules=None,
        genre_profile=profile,
    )

    assert "力量体系" in reminder


def test_profile_keywords_do_not_fall_back_to_xianxia_when_general_loaded():
    profile = GenreProfileService().get_default_profile()
    skill = ScenePreparationSkill()

    categories = skill._extract_abilities(
        {"conflict": "她必须面对一种未知力量带来的压力", "outcome": ""},
        genre_profile=profile,
    )

    assert "magic" not in categories


def test_location_schema_extracts_multiple_matrix_locations():
    profile = GenreProfileService()._load_yaml("mystery")
    skill = ScenePreparationSkill()

    locations = skill._extract_locations(
        {
            "crime_scene": "旧仓库",
            "suspect_locations": ["医院", "码头"],
            "evidence_locations": [{"name": "档案室"}, {"location": "停车场"}],
        },
        genre_profile=profile,
    )

    assert locations == ["旧仓库", "医院", "码头", "档案室", "停车场"]


def test_normalized_contract_carries_genre_profile_for_extension_validation():
    profile = GenreProfileService()._load_yaml("mystery")

    contract = normalize_scene_contract_v2(
        {
            "scene_id": "c1-s1",
            "pov_character": "侦探",
            "goal": "调查现场",
            "conflict": "证词互相矛盾",
            "must_show": ["发现矛盾"],
            "forbidden": [],
            "ending_state": "确认有第二现场",
            "genre_enrichment": {},
        },
        genre_profile=profile,
        chapter_number=1,
        scene_index=0,
    )

    violations = SceneCriticAgent()._deterministic_check(
        "侦探走进旧仓库，发现证词互相矛盾。",
        contract,
        chapter_state={},
        character_names=["侦探"],
    )

    assert any(v["type"] == "missing_genre_extension" for v in violations)
    missing = next(v for v in violations if v["type"] == "missing_genre_extension")
    assert missing["blocks_commit"] is False
