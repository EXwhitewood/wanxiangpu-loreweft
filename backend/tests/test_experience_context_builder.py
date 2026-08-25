import pytest

from app.services.experience_context_builder import ExperienceContextBuilder


def test_experience_context_builder_redacts_hidden_keys():
    ctx = ExperienceContextBuilder().build_for_scene(
        project_id="project-id",
        chapter_number=1,
        scene_index=0,
        scene_beat={"goal": "角色A想拿到物品X"},
        story_state={"truth_snapshot": {"secret": True}, "pov_character": "角色A"},
        scene_contract={
            "goal": "角色A想拿到物品X",
            "secret_canonical_statement": "隐藏真相",
            "scene_provenance": {
                "secret_spoiler_scope": {"chapter": 9},
                "current_facts": {"established_facts": ["角色A在地点Y"]},
            },
        },
        prepared={},
        writing_mode_profile={"id": "general"},
        quality_memory={"recent_quality_patterns": [{"type": "low_scene_pressure", "count": 2}]},
    )

    ExperienceContextBuilder.assert_no_hidden_keys(ctx)
    assert "secret_canonical_statement" not in str(ctx)
    assert "secret_spoiler_scope" not in str(ctx)


def test_assert_no_hidden_keys_rejects_direct_leak():
    with pytest.raises(ValueError):
        ExperienceContextBuilder.assert_no_hidden_keys({"truth_snapshot": {}})
