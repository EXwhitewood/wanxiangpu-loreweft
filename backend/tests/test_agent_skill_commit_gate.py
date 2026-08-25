from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.models.agent_skill import CompiledSkillPacket
from app.services.agent_skill_commit_gate import AgentSkillCommitGate


def _anti_ai_packet() -> CompiledSkillPacket:
    return CompiledSkillPacket(
        agent_name="core_generation",
        active_skills=["anti_ai_prose"],
        validators=["ai_flavor"],
        validation_contracts={
            "ai_flavor": {
                "anti_ai_prose": {
                    "dash_per_1000_max": 2.5,
                    "retry_policy": {
                        "max_retries": 1,
                        "action": "style_repair",
                    },
                }
            }
        },
        repair_hooks={"ai_flavor": ["prose_repair"]},
    )


def _dash_heavy_text() -> str:
    return (
        "Footsteps stopped outside\u2014\u2014she held the hilt"
        "\u2014\u2014and did not turn."
        * 80
    )


def _clean_text() -> str:
    return "Footsteps stopped outside. She held the hilt and did not turn." * 80


@pytest.mark.asyncio
async def test_final_skill_gate_strips_repair_scene_markers_before_protection(monkeypatch):
    llm = AsyncMock()
    dispatcher = SimpleNamespace(
        dispatch=AsyncMock(
            return_value={
                "completed": True,
                "text": "[[SCENE:1]]\n" + _clean_text(),
                "attempts": [],
            }
        )
    )
    monkeypatch.setattr(
        "app.services.agent_skill_repair_dispatcher.get_skill_repair_dispatcher",
        lambda: dispatcher,
    )

    result = await AgentSkillCommitGate().evaluate(
        project_id="project",
        db=None,
        final_text=_dash_heavy_text(),
        packet=_anti_ai_packet(),
        llm=llm,
    )

    assert result["allowed"] is True
    assert "[[SCENE:" not in result["text"]
    assert result["trace"]["repair"]["structure_cleanup"]["removed_scene_markers"] is True
    assert dispatcher.dispatch.await_count == 1


@pytest.mark.asyncio
async def test_final_skill_gate_accepts_repaired_text():
    llm = AsyncMock()
    llm.generate.return_value = _clean_text()

    result = await AgentSkillCommitGate().evaluate(
        project_id="project",
        db=None,
        final_text=_dash_heavy_text(),
        packet=_anti_ai_packet(),
        llm=llm,
    )

    assert result["allowed"] is True
    assert result["text"].count("\u2014\u2014") < _dash_heavy_text().count("\u2014\u2014")
    assert result["trace"]["repair"]["accepted"] is True
    assert "text" not in result["trace"]["repair"]["dispatch"]
    assert result["trace"]["repair"]["dispatch"]["attempts"][0]["deterministic"] is True
    llm.generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_final_skill_gate_blocks_when_dispatch_does_not_complete(monkeypatch):
    llm = AsyncMock()
    original = _dash_heavy_text()
    dispatcher = SimpleNamespace(
        dispatch=AsyncMock(
            return_value={
                "completed": False,
                "text": original,
                "attempts": [{"status": "rejected_no_progress"}],
            }
        )
    )
    monkeypatch.setattr(
        "app.services.agent_skill_repair_dispatcher.get_skill_repair_dispatcher",
        lambda: dispatcher,
    )

    result = await AgentSkillCommitGate().evaluate(
        project_id="project",
        db=None,
        final_text=original,
        packet=_anti_ai_packet(),
        llm=llm,
    )

    assert result["allowed"] is False
    assert result["text"] == original
    assert "ai_flavor:dash_per_1000" in result["reason"]
    assert result["trace"]["repair"]["accepted"] is False


@pytest.mark.asyncio
async def test_final_skill_gate_validation_only_does_not_dispatch(monkeypatch):
    dispatcher = SimpleNamespace(dispatch=AsyncMock())
    monkeypatch.setattr(
        "app.services.agent_skill_repair_dispatcher.get_skill_repair_dispatcher",
        lambda: dispatcher,
    )

    result = await AgentSkillCommitGate().evaluate(
        project_id="project",
        db=None,
        final_text=_dash_heavy_text(),
        packet=_anti_ai_packet(),
        llm=AsyncMock(),
        allow_llm_repair=False,
    )

    assert result["allowed"] is False
    assert result["trace"]["repair"]["disabled"] is True
    dispatcher.dispatch.assert_not_awaited()


def test_final_skill_gate_partitions_chapter_and_scene_validators():
    packet = CompiledSkillPacket(
        agent_name="core_generation",
        validators=[
            "ai_flavor",
            "scene_structure",
            "specificity_budget",
            "narrative_experience",
        ],
        validation_contracts={
            "ai_flavor": {"anti_ai_prose": {"dash_per_1000_max": 2.5}},
            "scene_structure": {"scene_crafting": {"goal_required": True}},
            "specificity_budget": {"specific_detail_anchor": {"anchor_per_element_min": 1}},
            "narrative_experience": {"narrative_writing": {"minimum_score": 6.0}},
        },
        repair_hooks={
            "ai_flavor": ["prose_repair"],
            "scene_structure": ["scene_restructure"],
            "specificity_budget": ["detail_anchor_repair"],
            "narrative_experience": ["pacing_repair"],
        },
    )

    chapter_packet = AgentSkillCommitGate._packet_for_validators(
        packet,
        ["ai_flavor", "narrative_experience"],
    )
    scene_packet = AgentSkillCommitGate._packet_for_validators(
        packet,
        ["scene_structure", "specificity_budget"],
    )

    assert chapter_packet.validators == ["ai_flavor", "narrative_experience"]
    assert scene_packet.validators == ["scene_structure", "specificity_budget"]
    assert set(chapter_packet.validation_contracts) == {"ai_flavor", "narrative_experience"}
    assert set(scene_packet.validation_contracts) == {"scene_structure", "specificity_budget"}
    assert set(chapter_packet.repair_hooks) == {"ai_flavor", "narrative_experience"}
    assert set(scene_packet.repair_hooks) == {"scene_structure", "specificity_budget"}

@pytest.mark.asyncio
async def test_final_skill_gate_dispatches_scene_validator_failures(monkeypatch):
    packet = CompiledSkillPacket(
        agent_name="core_generation",
        active_skills=["narrative_writing"],
        validators=["pov_consistency"],
        validation_contracts={
            "pov_consistency": {
                "narrative_writing": {
                    "single_pov_per_scene": True,
                    "retry_policy": {"max_retries": 1, "action": "fix_pov_leak"},
                }
            }
        },
        repair_hooks={"pov_consistency": ["voice_repair"]},
    )

    class FakeRuntime:
        async def validate_output(self, packet, *, generated_text, context):
            if "pov_consistency" not in packet.validators:
                return {"passed": True, "validators": {}, "failures": [], "requested": []}
            if "leak" in generated_text:
                finding = {
                    "skill_id": "narrative_writing",
                    "validator": "pov_consistency",
                    "metric": "single_pov_per_scene",
                    "actual": False,
                    "expected": True,
                    "severity": "high",
                    "action": "fix_pov_leak",
                    "retry_policy": {"max_retries": 1, "action": "fix_pov_leak"},
                }
                return {
                    "passed": False,
                    "validators": {
                        "pov_consistency": {
                            "validator": "pov_consistency",
                            "passed": False,
                            "findings": [finding],
                            "repair_hooks": ["voice_repair"],
                        }
                    },
                    "failures": [finding],
                    "requested": ["pov_consistency"],
                }
            return {
                "passed": True,
                "validators": {
                    "pov_consistency": {
                        "validator": "pov_consistency",
                        "passed": True,
                        "findings": [],
                        "repair_hooks": ["voice_repair"],
                    }
                },
                "failures": [],
                "requested": ["pov_consistency"],
            }

    async def fake_dispatch(packet, *, validation, generated_text, context, llm, max_tokens, validate_candidate):
        assert packet.validators == ["pov_consistency"]
        assert validation["failures"][0]["validator"] == "pov_consistency"
        assert validation["failures"][0]["scene_index"] == 0
        candidate_validation = await validate_candidate("clean scene text")
        assert candidate_validation["passed"] is True
        return {
            "completed": True,
            "hook": "voice_repair",
            "hooks": ["voice_repair"],
            "text": "clean scene text",
            "attempts": [{"hook": "voice_repair", "status": "completed"}],
        }

    dispatcher = SimpleNamespace(dispatch=AsyncMock(side_effect=fake_dispatch))
    monkeypatch.setattr(
        "app.services.agent_skill_runtime.get_skill_runtime",
        lambda: FakeRuntime(),
    )
    monkeypatch.setattr(
        "app.services.agent_skill_repair_dispatcher.get_skill_repair_dispatcher",
        lambda: dispatcher,
    )

    result = await AgentSkillCommitGate().evaluate(
        project_id="project",
        db=None,
        final_text="leak scene text",
        packet=packet,
        llm=AsyncMock(),
        scene_units=[{"text": "leak scene text", "scene_contract": {}}],
    )

    assert result["allowed"] is True
    assert result["text"] == "clean scene text"
    assert result["trace"]["repair"]["accepted"] is True
    assert dispatcher.dispatch.await_count == 1
