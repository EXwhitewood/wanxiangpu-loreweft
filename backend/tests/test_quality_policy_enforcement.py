from app.services.quality_gate import QualityGate


def test_quality_policy_candidate_keeps_matching_advisory_non_blocking():
    gate = QualityGate()
    scene_contract = {
        "quality_extensions": {
            "quality_gate_candidates": [
                {
                    "type": "low_conflict_density_trend",
                    "count": 8,
                    "instruction": "Make conflict visible on page.",
                }
            ]
        }
    }
    reports = {
        "commercial_pacing": {
            "advisories": [
                {
                    "type": "low_conflict_density",
                    "severity": "medium",
                    "detail": "Conflict is not staged as an active obstacle.",
                    "expected_behavior": "Add visible obstacle, cost, or opposition.",
                    "detector": "commercial_pacing",
                }
            ]
        }
    }

    violations = gate._quality_policy_enforced_violations(
        scene_contract,
        reports,
        text_hash="abc123",
        review_version=1,
    )

    assert len(violations) == 1
    assert violations[0]["type"] == "low_conflict_density"
    assert violations[0]["blocks_commit"] is False
    assert violations[0]["evidence"]["enforcement"] == "advisory"
    assert violations[0]["source"] == "commercial_pacing"
    assert violations[0]["evidence"]["enforcement_reason"] == "quality_memory_candidate_matched_current_advisory"


def test_quality_policy_enforcement_respects_switches_and_thresholds():
    gate = QualityGate()
    scene_contract = {
        "quality_extensions": {
            "quality_policy_enforcement": {
                "enabled": True,
                "min_candidate_count": 5,
                "allow_types": ["low_conflict_density"],
                "deny_types": ["weak_opening_hook"],
            },
            "quality_gate_candidates": [
                {"type": "low_conflict_density_trend", "level": "quality_gate_candidate", "count": 4},
                {"type": "weak_opening_hook_trend", "level": "quality_gate_candidate", "count": 8},
                {"type": "abstraction_over_budget_trend", "level": "quality_gate_candidate", "count": 8},
            ],
        }
    }
    reports = {
        "commercial_pacing": {
            "advisories": [
                {"type": "low_conflict_density", "severity": "high", "detail": "low conflict"},
                {"type": "weak_opening_hook", "severity": "high", "detail": "weak hook"},
                {"type": "abstraction_over_budget", "severity": "high", "detail": "abstract"},
            ]
        }
    }

    violations, metrics = gate._quality_policy_enforced_violations(
        scene_contract,
        reports,
        "hash",
        1,
        return_metrics=True,
    )

    assert violations == []
    assert metrics["candidates_total"] == 3
    assert metrics["candidates_considered"] == 0
    skipped = {(item.get("type"), item.get("reason")) for item in metrics["skipped"]}
    assert ("low_conflict_density", "below_min_candidate_count") in skipped
    assert ("weak_opening_hook", "not_in_allow_types") in skipped or ("weak_opening_hook", "in_deny_types") in skipped
    assert ("abstraction_over_budget", "not_in_allow_types") in skipped


def test_foreshadowing_guardrail_blocks_premature_forbidden_interpretation():
    gate = QualityGate()
    scene_contract = {
        "long_term_constraints": {
            "foreshadowing_constraints": [
                {
                    "name": "Mirror clue",
                    "action": "protect_secret",
                    "constraint": "forbid_premature_reveal",
                    "reader_forbidden_interpretations": ["the mirror is controlled by the missing brother"],
                    "reader_intended_state": "suspicious",
                }
            ]
        }
    }

    violations = gate._foreshadowing_guardrail_violations(
        scene_contract,
        "Lin finally knew the mirror is controlled by the missing brother.",
        "hash",
        1,
    )

    assert len(violations) == 1
    assert violations[0]["type"] == "premature_foreshadowing_reveal"
    assert violations[0]["blocks_commit"] is True
    assert violations[0]["evidence"]["enforcement_reason"] == "protected_foreshadowing_phrase_matched_candidate_text"
