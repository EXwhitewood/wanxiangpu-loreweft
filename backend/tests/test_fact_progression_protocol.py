from app.services.fact_progression_protocol import (
    apply_protocol_to_violations,
    is_fbi_repairable_violation,
    review_status_for_violations,
    summarize_protocol,
)


def test_time_boundary_jump_is_non_blocking_delta():
    violations = [{
        "type": "timeline_conflict",
        "detail": "scene moved to next day morning after the evening beat ended",
        "blocks_commit": True,
        "suggested_strategy": "rewrite_scene",
    }]
    result = apply_protocol_to_violations(violations, {"scene_contract": {"allowed": ["next day"]}})

    assert result[0]["classification"] == "time_jump"
    assert result[0]["blocks_commit"] is False
    assert result[0]["recommended_route"] == "validate_boundary_then_accept"
    assert is_fbi_repairable_violation(result[0]) is False


def test_referenced_time_is_not_treated_as_baseline_contradiction():
    violations = [{
        "type": "temporal_conflict",
        "detail": "the narration says yesterday's argument still mattered",
        "blocks_commit": True,
    }]
    result = apply_protocol_to_violations(violations, {})

    assert result[0]["classification"] == "referenced_time"
    assert result[0]["blocks_commit"] is False
    assert result[0]["scope"] == "advisory"


def test_character_claim_preserves_truth_uncertainty():
    violations = [{
        "type": "timeline_conflict",
        "detail": "the witness said yesterday they saw the signal",
        "blocks_commit": True,
    }]
    result = apply_protocol_to_violations(violations, {})

    assert result[0]["classification"] == "character_claim"
    assert result[0]["blocks_commit"] is False
    assert result[0]["authority_source"] == "draft_text"


def test_contract_conflict_routes_to_planning_not_fbi_text_repair():
    violations = [{
        "type": "style_contract_conflict",
        "detail": "contract requires incompatible requirements",
        "blocks_commit": True,
        "suggested_strategy": "repair_contract",
    }]
    result = apply_protocol_to_violations(violations, {})

    assert result[0]["classification"] == "contract_conflict"
    assert result[0]["scope"] == "scene_contract"
    assert result[0]["repairable_by_text"] is False
    assert review_status_for_violations(result) == "waiting_contract_repair"
    assert is_fbi_repairable_violation(result[0]) is False


def test_alias_and_retcon_require_human_governance():
    violations = [
        {"type": "identity_conflict", "detail": "two names may refer to one entity", "blocks_commit": True},
        {"type": "event_repeated", "detail": "completed event appears to be replayed", "blocks_commit": True},
    ]
    result = apply_protocol_to_violations(violations, {})

    assert [item["classification"] for item in result] == ["alias_needed", "retcon"]
    assert review_status_for_violations([result[0]]) == "waiting_alias_decision"
    assert review_status_for_violations([result[1]]) == "waiting_retcon_decision"
    assert all(not is_fbi_repairable_violation(item) for item in result)


def test_baseline_contradiction_remains_fbi_repairable():
    violations = [{
        "type": "fact_conflict",
        "detail": "the established object location is contradicted",
        "blocks_commit": True,
        "suggested_strategy": "patch_text",
    }]
    result = apply_protocol_to_violations(violations, {})

    assert result[0]["classification"] == "contradiction"
    assert result[0]["authority_source"] == "baseline_world_state"
    assert is_fbi_repairable_violation(result[0]) is True


def test_same_chapter_state_disappearance_supersedes_opening_baseline():
    violations = [{
        "type": "fact_conflict",
        "detail": (
            "正文声称掌心纹路已经消失，与章节开场时纹路仍然存在的状态冲突"
        ),
        "target_span": "掌心没有任何纹路",
        "expected_behavior": "restore the old mark",
        "blocks_commit": True,
    }]
    context = {
        "generated_text": "她低头看见掌心没有任何纹路。",
        "previous_scene_ending": "禁术结束后，掌心纹路彻底消失。",
        "previous_scenes_summary": "纹路先断裂，随后熄灭。",
    }

    result = apply_protocol_to_violations(violations, context)

    assert result[0]["classification"] == "progression"
    assert result[0]["authority_source"] == "draft_text"
    assert result[0]["recommended_route"] == "accept_as_delta"
    assert result[0]["blocks_commit"] is False
    assert result[0]["scope"] == "advisory"


def test_baseline_conflict_is_not_demoted_when_prior_scene_has_no_transition():
    result = apply_protocol_to_violations([{
        "type": "fact_conflict",
        "detail": "正文声称掌心纹路已经消失，与章节开场状态冲突",
        "target_span": "掌心没有任何纹路",
        "blocks_commit": True,
    }], {
        "generated_text": "她低头看见掌心没有任何纹路，纹路已经消失。",
        "previous_scene_ending": "她走进屋内并关上房门。",
    })

    assert result[0]["classification"] == "contradiction"
    assert result[0]["blocks_commit"] is True


def test_summary_counts_blocking_and_non_blocking_routes():
    violations = apply_protocol_to_violations([
        {"type": "fact_conflict", "detail": "fixed baseline conflict", "blocks_commit": True},
        {"type": "temporal_conflict", "detail": "next day", "blocks_commit": True},
        {"type": "scene_too_short", "detail": "soft advice", "blocks_commit": False},
    ], {})

    summary = summarize_protocol(violations)

    assert sum(summary["by_classification"].values()) == 3
    assert summary["blocking_count"] == 1
    assert summary["by_classification"]["contradiction"] == 1
    assert summary["by_classification"]["time_jump"] == 1
    assert summary["by_classification"]["advisory_quality"] == 1
