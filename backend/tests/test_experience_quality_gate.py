from unittest.mock import patch

import pytest

from app.services.quality_gate import QualityGate


@pytest.mark.asyncio
async def test_quality_gate_includes_experience_layers_without_blocking():
    gate = QualityGate()
    context = {
        "generated_text": "角色A想了很多。她意识到事情很复杂。这一刻，她终于明白命运注定如此。",
        "scene_contract": {
            "chapter_number": 1,
            "scene_index": 0,
            "goal": "角色A寻找物品X",
            "conflict": "角色B阻拦",
            "experience_contract": {
                "writing_mode_id": "commercial_web",
                "obstacle": "角色B阻拦",
                "agency_requirement": "角色A必须有行动",
            },
            "literary_quality_contract": {
                "writing_mode_id": "commercial_web",
                "specificity_budget": {"minimum_specific_details": 4},
            },
        },
        "chapter_state": {},
        "character_cards": [],
        "character_names": ["角色A"],
        "project_id": "project-id",
        "chapter_number": 1,
        "scene_index": 0,
        "core_facts": {},
        "current_state": {},
        "generation_feature_policy": {
            "narrative_experience_mode": "report",
            "literary_quality_mode": "report",
            "mode_fit_mode": "report",
            "style_experience_conflict_mode": "report",
        },
    }

    with patch.object(gate, "_run_deterministic", return_value=[]), \
         patch.object(gate, "_run_quality_checkers", return_value=[]), \
         patch.object(gate, "_run_consistency", return_value={"violations": []}), \
         patch.object(gate, "_run_semantic", return_value=[]), \
         patch.object(gate, "_run_fcip", return_value={"violations": [], "detection": {}}):
        result = await gate.evaluate(context)

    assert result["commit_blocked"] is False
    assert "narrative_experience" in result["reports"]
    assert "literary_quality" in result["reports"]
    assert "mode_fit" in result["reports"]
    assert result["reports"]["narrative_experience"]["advisories"]
