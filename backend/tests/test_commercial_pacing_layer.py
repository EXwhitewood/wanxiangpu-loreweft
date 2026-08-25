from unittest.mock import patch

import pytest

from app.agents.ai_quality_coordinator import AIQualityCoordinatorAgent
from app.models.generation_features import GenerationFeaturePolicy
from app.services.commercial_pacing_compiler import CommercialPacingCompiler
from app.services.quality_checkers.commercial_pacing_checker import CommercialPacingChecker
from app.services.quality_gate import QualityGate


def test_commercial_pacing_policy_defaults_to_current_turn_assist():
    policy = GenerationFeaturePolicy()

    assert policy.commercial_pacing_mode == "assist"
    assert policy.commercial_pacing_profile_id == "general"
    assert policy.commercial_pacing_revision_mode == "off"
    assert policy.is_legacy is False


def test_commercial_pacing_compiler_builds_contract_without_mutating_truth():
    source_of_truth = {"facts": [{"subject": "role_a", "predicate": "owns", "object": "item_x"}]}
    scene_contract = {
        "chapter_number": 1,
        "scene_index": 0,
        "goal": "角色A必须找到物品X",
        "conflict": "角色B阻止角色A进入地点Y",
        "stakes": "错过时限会失去资格",
        "source_of_truth": source_of_truth,
    }

    compiled = CommercialPacingCompiler().compile({
        "chapter_number": 1,
        "scene_index": 0,
        "scene_count": 3,
        "scene_contract": scene_contract,
        "scene_beat": {"goal": "角色A找物品X", "conflict": "角色B阻止"},
    })

    assert compiled.commercial_pacing_contract.scene_position == "opening"
    assert compiled.commercial_pacing_contract.reader_hook
    assert compiled.commercial_pacing_contract.chapter_end_hook
    assert "commercial_pacing_guidance" in compiled.quality_extensions_patch
    assert scene_contract["source_of_truth"] is source_of_truth
    assert "commercial_pacing_contract" not in scene_contract


def test_commercial_pacing_checker_distinguishes_flat_and_driven_text():
    checker = CommercialPacingChecker()
    contract = {
        "opening_hook_required": True,
        "chapter_end_hook_required": True,
        "minimum_event_density": 0.55,
        "minimum_conflict_density": 0.25,
        "minimum_curiosity_density": 0.12,
    }
    flat_text = "角色A站在地点Y。她想了很久，觉得事情复杂。夜色很深，她继续沉默。"
    driven_text = (
        "门刚推开，物品X忽然从桌上滑落，角色A立刻伸手去抢。\n"
        "角色B挡住出口：三息之内交出来，否则资格作废。\n"
        "角色A把碎片藏进袖中，故意摔碎灯盏。火光一灭，她发现墙后还有第二枚印记。\n"
        "可印记亮起时，门外响起了角色C的脚步声。"
    )

    flat = checker.check(flat_text, contract)
    driven = checker.check(driven_text, contract)

    assert flat["scores"]["reader_retention"] < driven["scores"]["reader_retention"]
    assert any(adv["type"] == "weak_opening_hook" for adv in flat["advisories"])
    assert driven["scores"]["event_density"] >= flat["scores"]["event_density"]


@pytest.mark.asyncio
async def test_quality_gate_includes_commercial_pacing_report_without_blocking():
    gate = QualityGate()
    context = {
        "generated_text": "角色A站在地点Y。她想了很久，觉得事情复杂。夜色很深，她继续沉默。",
        "scene_contract": {
            "chapter_number": 1,
            "scene_index": 0,
            "commercial_pacing_contract": {
                "opening_hook_required": True,
                "chapter_end_hook_required": True,
                "minimum_event_density": 0.55,
                "minimum_conflict_density": 0.25,
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
            "commercial_pacing_mode": "report",
        },
    }

    with patch.object(gate, "_run_deterministic", return_value=[]), \
         patch.object(gate, "_run_quality_checkers", return_value=[]), \
         patch.object(gate, "_run_consistency", return_value={"violations": []}), \
         patch.object(gate, "_run_semantic", return_value=[]), \
         patch.object(gate, "_run_fcip", return_value={"violations": [], "detection": {}}):
        result = await gate.evaluate(context)

    assert result["commit_blocked"] is False
    assert "commercial_pacing" in result["reports"]
    assert result["reports"]["commercial_pacing"]["advisories"]
    assert result["layer_summary"]["commercial_pacing"]["total"] == 0


def test_ai_quality_coordinator_routes_commercial_guidance_only_when_assist():
    coordinator = AIQualityCoordinatorAgent()
    advisories = [{
        "type": "weak_chapter_end_hook",
        "severity": "medium",
        "source_checker": "commercial_pacing",
        "confidence": 0.72,
    }]

    report_patch = coordinator._build_guidance_patch(
        advisories,
        AIQualityCoordinatorAgent._resolve_modes(
            GenerationFeaturePolicy(commercial_pacing_mode="report")
        ),
    )
    assist_patch = coordinator._build_guidance_patch(
        advisories,
        AIQualityCoordinatorAgent._resolve_modes(
            GenerationFeaturePolicy(commercial_pacing_mode="assist")
        ),
    )

    assert "commercial_pacing_guidance" not in report_patch
    assert assist_patch["commercial_pacing_guidance"]
    assert "experience_guidance" not in assist_patch
    assert "literary_quality_guidance" not in assist_patch
