from app.services.scene_budget_service import SceneBudgetService
from app.services.story_plan_completeness_service import (
    _build_completion_scenes,
    ensure_complete_scene_briefs,
)


def test_transition_chapter_recommends_two_scenes():
    budget = SceneBudgetService().calculate({"function": "transition"})

    assert budget["recommended_scene_count"] == 2
    assert budget["scene_budget_signature"].startswith("sha256:")
    assert len(budget["scene_budget_signature"]) == len("sha256:") + 16


def test_complex_reversal_chapter_recommends_five_scenes():
    budget = SceneBudgetService().calculate({
        "function": "reversal",
        "core_conflict": {
            "desire": "查明真相",
            "obstacle": "证人失踪",
            "action": "追踪旧档案",
            "turn": "盟友暴露",
        },
        "value_shift": {"from": "信任", "to": "怀疑"},
        "thread_ops": [{}, {}, {}],
        "hook": "真相揭示后盟友背叛",
        "depends_on": ["ch_001", "ch_002"],
    })

    assert budget["recommended_scene_count"] == 5


def test_budget_defensively_handles_legacy_shapes():
    budget = SceneBudgetService().calculate({
        "function": None,
        "core_conflict": "legacy",
        "value_shift": "安全 -> 危险",
        "thread_ops": "legacy",
        "depends_on": "legacy",
    })

    assert budget["recommended_scene_count"] == 2


def test_completion_appends_templates_without_overwriting_existing_scene():
    completed, stats = ensure_complete_scene_briefs({
        "chapter_spine": [{"chapter_number": 1, "function": "reversal"}],
        "scene_briefs": {
            "ch_001": {
                "scenes": [{"scene_id": "authored", "goal": "保留原场景"}],
            }
        },
    })

    scenes = completed["scene_briefs"]["ch_001"]["scenes"]
    assert scenes[0]["scene_id"] == "authored"
    assert scenes[1]["scene_id"] == "ch_001_s2"
    assert scenes[1]["beat_role"] == "pressure"
    assert stats["added_scenes"] == 1


def test_rule_templates_follow_requested_sequence():
    scenes = _build_completion_scenes({"chapter_number": 3}, 3, start_index=3)

    assert [scene["beat_role"] for scene in scenes] == [
        "decision",
        "turning_point",
        "hook",
    ]


def test_budget_signature_change_resets_user_override():
    initial_chapter = {"chapter_number": 4, "function": "transition"}
    initial_budget = SceneBudgetService().calculate(initial_chapter)
    completed, _ = ensure_complete_scene_briefs({
        "chapter_spine": [{
            "chapter_number": 4,
            "function": "reversal",
            "thread_ops": [{}, {}],
        }],
        "scene_briefs": {
            "ch_004": {
                "scene_budget_signature": initial_budget["scene_budget_signature"],
                "scene_count_user_overridden": True,
                "scenes": [{}, {}],
            }
        },
    })

    brief = completed["scene_briefs"]["ch_004"]
    assert brief["scene_count_user_overridden"] is False
    assert brief["recommended_scene_count"] == 3
