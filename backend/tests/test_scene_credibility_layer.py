from __future__ import annotations

import pytest

from app.models.generation_features import GenerationFeaturePolicy
from app.services.quality_checkers.scene_credibility_checker import SceneCredibilityChecker
from app.services.quality_gate import QualityGate
from app.services.scene_credibility_compiler import SceneCredibilityCompiler


def test_scene_credibility_compiler_is_domain_neutral_and_prompt_ready():
    compiled = SceneCredibilityCompiler().compile({
        "project_id": "p1",
        "chapter_number": 1,
        "scene_index": 0,
        "scene_contract": {
            "scene_id": "s1",
            "pov_character": "角色A",
            "goal": "确认对象Y的状态",
            "conflict": "角色A无法直接获得完整信息",
            "forbidden": ["角色A直接确认未公开真相"],
            "scene_provenance": {
                "current_facts": {
                    "established_facts": ["角色A当前身份状态为S1"],
                    "character_states": {"角色A": "身体状态受限"},
                },
                "spatial_anchor": {"current_location": "地点X"},
                "timeline_anchor": {"opening_state": "事件后", "ending_state": "做出有限判断"},
            },
        },
    })

    contract = compiled.contract.model_dump()
    assert contract["fact_boundaries"]
    assert contract["knowledge_snapshots"][0]["display_name"] == "角色A"
    assert compiled.quality_extensions_patch["scene_credibility_guidance"]
    dumped = str(contract)
    assert "外门" not in dumped
    assert "宗门" not in dumped


def test_scene_credibility_checker_detects_forbidden_claim():
    contract = {
        "fact_boundaries": [
            {
                "label": "对象状态",
                "allowed_claims": ["对象Y状态未知"],
                "forbidden_claims": ["对象Y已经被角色A确认"],
            }
        ]
    }
    report = SceneCredibilityChecker().check(
        "角色A看了一眼，断定对象Y已经被角色A确认。",
        contract,
        mode="assist",
    )
    assert report["violations"][0]["type"] == "fact_boundary_conflict"
    assert report["violations"][0]["blocks_commit"] is True


def test_scene_credibility_checker_detects_implausible_memory():
    report = SceneCredibilityChecker().check(
        "她闭上眼，一瞬间从头到尾完整想起所有细节。",
        {},
        mode="assist",
    )
    assert any(v["type"] == "memory_plausibility_break" for v in report["violations"])


def test_scene_credibility_checker_detects_explanatory_narration_as_advisory_or_violation():
    report = SceneCredibilityChecker().check(
        "他确认了——不是临时起意，并非误会，而是对方早就设计好的选择。",
        {},
        mode="assist",
    )
    found = report["violations"] + report["advisories"]
    assert any(item["type"] in {"narration_explanation_artifact", "explanatory_punctuation_artifact"} for item in found)
    assert all(item.get("blocks_commit") is not True for item in found)


@pytest.mark.asyncio
async def test_quality_gate_runs_scene_credibility_layer():
    policy = GenerationFeaturePolicy(
        scene_credibility_mode="assist",
        narrative_contract_mode="off",
        proposition_extraction_mode="off",
        hard_correctness_mode="off",
        reader_experience_mode="off",
        narrative_experience_mode="off",
        literary_quality_mode="off",
        commercial_pacing_mode="off",
    )
    report = await QualityGate().evaluate({
        "generated_text": "角色A直接确认未公开真相。",
        "scene_contract": {
            "scene_credibility_contract": {
                "fact_boundaries": [
                    {
                        "label": "知识边界",
                        "allowed_claims": [],
                        "forbidden_claims": ["角色A直接确认未公开真相"],
                    }
                ],
                "knowledge_snapshots": [],
                "plausibility_rules": [],
                "narration_rules": [],
            }
        },
        "generation_feature_policy": policy,
    }, level="full")

    assert report["commit_blocked"] is True
    assert report["reports"]["scene_credibility"]["violations"]
    assert report["layer_summary"]["scene_credibility"]["blocking"] == 1
