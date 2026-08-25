import pytest

from app.services.chapter_parallel_review import (
    ChapterParallelReviewService,
    SceneReviewWorker,
    _classify_repairability,
    _comparable_state_claims_from_delta,
    _normalize_scene_violation,
)


def test_scene_review_context_carries_bounded_prior_scene_chronology():
    context = ChapterParallelReviewService._build_scene_context(
        2,
        "current scene",
        {"chapter_number": 28},
        {"current_state": {"mark": "gold"}},
        all_scene_texts=[
            "first transition " + "A" * 900,
            "B" * 1900 + " the gold mark disappears",
            "current scene",
        ],
    )

    assert context["previous_scene_ending"].endswith("the gold mark disappears")
    assert len(context["previous_scene_ending"]) == 1800
    assert "the gold mark disappears" in context["previous_scene_ending"]
    assert len(context["previous_scenes_summary"]) <= 1202
    assert context["prior_scene_context_is_chronological"] is True


def test_state_delta_metadata_is_not_cross_scene_state_claim():
    claims = _comparable_state_claims_from_delta(
        {
            "project_id": "project-1",
            "chapter_number": 10,
            "scene_index": 0,
            "entity_deltas": [{"entity_name": "凤溪"}],
            "foreshadowing_ops": [{"operation": "plant"}],
            "active_constraints_add": [{"description": "keep ambiguity"}],
            "active_constraints_remove": [],
            "raw_patch": {},
        }
    )

    assert claims == {}


def test_state_delta_extracts_only_comparable_story_claims():
    claims = _comparable_state_claims_from_delta(
        {
            "project_id": "project-1",
            "scene_index": 0,
            "narrative_time": "午后",
            "raw_patch": {
                "objective_state": {"凤溪": {"location": "戒律堂"}},
                "active_scene": "庭审",
                "completed_events": ["not directly compared"],
            },
        }
    )

    assert claims == {
        "narrative_time": "午后",
        "objective_state": {"凤溪": {"location": "戒律堂"}},
        "active_scene": "庭审",
    }


def test_validator_failure_does_not_poison_content_issue_repairability():
    system_issue = {
        "type": "proposition_extractor_unavailable",
        "blocks_commit": False,
    }
    content_issue = {
        "type": "missing_must_show",
        "blocks_commit": True,
    }

    packet_lane = _classify_repairability([system_issue, content_issue], [])
    normalized_system = _normalize_scene_violation(system_issue, 0, packet_lane)
    normalized_content = _normalize_scene_violation(content_issue, 0, packet_lane)

    assert packet_lane == "human_review_required"
    assert normalized_system["repairability"] == "degraded_system_issue"
    assert normalized_content["repairability"] == "contract_repair_required"


@pytest.mark.asyncio
async def test_scene_review_reuses_precomputed_postprocess_without_second_llm(monkeypatch):
    async def fake_evaluate(self, context, level="full"):
        assert level == "fast"
        return {"passed": True, "violations": []}

    monkeypatch.setattr("app.services.quality_gate.QualityGate.evaluate", fake_evaluate)

    packet = await SceneReviewWorker(scene_index=0).review({
        "generated_text": "A final scene candidate.",
        "scene_contract": {},
        "current_state": {"narrative_time": "night"},
        "quality_level": "fast",
        "precomputed_review_extraction": {
            "available": True,
            "state_delta": {"narrative_time": "dawn"},
            "detail_seeds": [],
            "foreshadowing_clues": [],
        },
    })

    assert packet.state_delta == {"narrative_time": "dawn"}
    assert packet.opening_state_claims == {"narrative_time": "night"}
    assert packet.ending_state_claims == {"narrative_time": "dawn"}
