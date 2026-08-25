from unittest.mock import AsyncMock, patch

import pytest

from app.models.agent_skill import CompiledSkillPacket
from app.services.agent_skill_protection import AgentSkillProtection
from app.services.agent_skill_repair_dispatcher import AgentSkillRepairDispatcher
from app.services.fbi.skill_repair_runtime import FBISkillRepairRuntime


def test_skill_dispatcher_only_compiles_fbi_repair_plan():
    assert not hasattr(AgentSkillRepairDispatcher, "_PROMPTS")
    assert FBISkillRepairRuntime().has_executable_hook("prose_repair") is True
    assert FBISkillRepairRuntime().has_executable_hook("break_false_range") is True
    assert FBISkillRepairRuntime().has_executable_hook("inject_goal") is True


@pytest.mark.asyncio
async def test_final_gate_actions_route_to_fbi_runtime_not_unsupported():
    packet = CompiledSkillPacket(agent_name="core_generation")
    llm = AsyncMock()
    llm.generate.side_effect = ["range fixed", "goal fixed"]

    result = await AgentSkillRepairDispatcher().dispatch(
        packet,
        validation={
            "validators": {},
            "failures": [
                {
                    "validator": "ai_flavor",
                    "metric": "false_range_count",
                    "action": "break_false_range",
                },
                {
                    "validator": "scene_structure",
                    "metric": "goal_present",
                    "action": "inject_goal",
                },
            ],
        },
        generated_text="original",
        context={},
        llm=llm,
        max_tokens=1000,
    )

    assert result["completed"] is True
    assert result["hooks"] == ["break_false_range", "inject_goal"]
    assert {attempt["executor"] for attempt in result["attempts"]} == {"fbi_skill_repair_runtime"}
    assert all(attempt["status"] != "unsupported" for attempt in result["attempts"])


@pytest.mark.asyncio
async def test_repair_hook_dispatches_to_prose_repair():
    packet = CompiledSkillPacket(
        agent_name="core_generation",
        repair_hooks={"ai_flavor": ["prose_repair"]},
    )
    llm = AsyncMock()
    llm.generate.return_value = "repaired prose"

    result = await AgentSkillRepairDispatcher().dispatch(
        packet,
        validation={
            "validators": {
                "ai_flavor": {
                    "repair_hooks": ["prose_repair"],
                }
            },
            "failures": [{"validator": "ai_flavor", "metric": "dash_per_1000"}],
        },
        generated_text="original prose",
        context={},
        llm=llm,
        max_tokens=1000,
    )

    assert result["completed"] is True
    assert result["hook"] == "prose_repair"
    assert result["text"] == "repaired prose"
    llm.generate.assert_awaited_once()


@pytest.mark.asyncio
async def test_detail_anchor_repair_hook_is_executable():
    packet = CompiledSkillPacket(
        agent_name="core_generation",
        repair_hooks={"specificity_budget": ["detail_anchor_repair"]},
    )
    llm = AsyncMock()
    llm.generate.return_value = "\u5899\u76ae\u88c2\u5f00\u4e00\u9053\u7070\u767d\u7684\u7eb9\u3002"

    result = await AgentSkillRepairDispatcher().dispatch(
        packet,
        validation={
            "validators": {
                "specificity_budget": {
                    "repair_hooks": ["detail_anchor_repair"],
                }
            },
            "failures": [
                {
                    "validator": "specificity_budget",
                    "metric": "anchor_coverage",
                    "missing_anchor_elements": ["\u623f\u95f4"],
                }
            ],
        },
        generated_text="\u8fd9\u662f\u4e00\u4e2a\u7834\u65e7\u7684\u623f\u95f4\u3002",
        context={
            "scene_elements": ["\u623f\u95f4"],
            "pov_character_card": {"name": "\u51e4\u6eaa"},
        },
        llm=llm,
        max_tokens=1000,
    )

    assert result["completed"] is True
    assert result["hook"] == "detail_anchor_repair"
    assert result["text"] == "\u5899\u76ae\u88c2\u5f00\u4e00\u9053\u7070\u767d\u7684\u7eb9\u3002"
    prompt = llm.generate.await_args.kwargs["user_prompt"]
    assert "scene_elements" in prompt
    assert "pov_character_card" in prompt


@pytest.mark.asyncio
async def test_discourse_failure_prioritizes_paragraph_reconstruction():
    packet = CompiledSkillPacket(
        agent_name="core_generation",
        repair_hooks={"ai_discourse": ["prose_repair", "paragraph_reconstruction"]},
    )
    llm = AsyncMock()
    llm.generate.return_value = "restructured paragraph"

    result = await AgentSkillRepairDispatcher().dispatch(
        packet,
        validation={
            "validators": {
                "ai_discourse": {
                    "repair_hooks": ["prose_repair", "paragraph_reconstruction"],
                }
            },
            "failures": [{
                "validator": "ai_discourse",
                "metric": "paragraph_shape_repeat_count",
                "action": "paragraph_reconstruction",
            }],
        },
        generated_text="original paragraph",
        context={},
        llm=llm,
        max_tokens=1000,
    )

    assert result["hook"] == "paragraph_reconstruction"


@pytest.mark.asyncio
async def test_voice_failure_prioritizes_voice_reconstruction_and_context():
    packet = CompiledSkillPacket(
        agent_name="core_generation",
        repair_hooks={"voice_fingerprint": ["prose_repair", "voice_reconstruction"]},
    )
    llm = AsyncMock()
    llm.generate.return_value = "voice repaired prose"

    result = await AgentSkillRepairDispatcher().dispatch(
        packet,
        validation={
            "validators": {
                "voice_fingerprint": {
                    "repair_hooks": ["prose_repair", "voice_reconstruction"],
                }
            },
            "failures": [{
                "validator": "voice_fingerprint",
                "metric": "voice_fingerprint_score",
                "action": "voice_reconstruction",
            }],
        },
        generated_text="original prose",
        context={"voice_fingerprint": {"avoided_words": ["grand"]}},
        llm=llm,
        max_tokens=1000,
    )

    assert result["hook"] == "voice_reconstruction"
    assert "voice_fingerprint" in llm.generate.await_args.kwargs["user_prompt"]


@pytest.mark.asyncio
async def test_multiple_validator_failures_run_tiered_repair_hooks():
    packet = CompiledSkillPacket(
        agent_name="core_generation",
        repair_hooks={
            "ai_flavor": ["prose_repair"],
            "ai_discourse": ["paragraph_reconstruction"],
            "scene_evidence": ["emotion_to_scene_evidence"],
        },
    )
    llm = AsyncMock()
    llm.generate.side_effect = [
        "dash repaired prose",
        "paragraph repaired prose",
        "scene evidence repaired prose",
    ]

    result = await AgentSkillRepairDispatcher().dispatch(
        packet,
        validation={
            "validators": {
                "ai_flavor": {"validator": "ai_flavor", "repair_hooks": ["prose_repair"]},
                "ai_discourse": {"validator": "ai_discourse", "repair_hooks": ["paragraph_reconstruction"]},
                "scene_evidence": {"validator": "scene_evidence", "repair_hooks": ["emotion_to_scene_evidence"]},
            },
            "failures": [
                {
                    "validator": "ai_flavor",
                    "metric": "dash_per_1000",
                    "action": "prose_repair",
                },
                {
                    "validator": "ai_discourse",
                    "metric": "paragraph_shape_repeat_count",
                    "action": "paragraph_reconstruction",
                },
                {
                    "validator": "scene_evidence",
                    "metric": "standalone_abstract_claims",
                    "action": "abstract_to_sensory",
                },
            ],
        },
        generated_text="original prose",
        context={},
        llm=llm,
        max_tokens=1000,
    )

    assert result["completed"] is True
    assert result["hooks"] == [
        "prose_repair",
        "paragraph_reconstruction",
        "emotion_to_scene_evidence",
    ]
    assert result["hook"] == "emotion_to_scene_evidence"
    assert result["text"] == "scene evidence repaired prose"
    assert llm.generate.await_count == 3
    prompts = [call.kwargs["user_prompt"] for call in llm.generate.await_args_list]
    assert "dash_per_1000" in prompts[0]
    assert "paragraph_shape_repeat_count" in prompts[1]
    assert "standalone_abstract_claims" in prompts[2]


@pytest.mark.asyncio
async def test_repair_rejected_when_marker_missing():
    protection = AgentSkillProtection()
    result = await protection.audit(
        original_text="[[SCENE:1]]original prose",
        candidate_text="repaired prose",
        context={},
        structural_check=lambda text: {
            "ok": "[[SCENE:1]]" in text,
            "missing": ["1"] if "[[SCENE:1]]" not in text else [],
        },
    )

    assert result["ok"] is False
    assert result["structural"]["missing"] == ["1"]


@pytest.mark.asyncio
async def test_repair_rejected_when_quality_gate_adds_blocking_violation():
    protection = AgentSkillProtection()
    original_report = {"violations": []}
    candidate_report = {
        "violations": [
            {"type": "fact_conflict", "blocks_commit": True},
        ]
    }

    with patch(
        "app.services.quality_gate.QualityGate.evaluate",
        new=AsyncMock(side_effect=[original_report, candidate_report]),
    ):
        result = await protection.audit(
            original_text="original prose",
            candidate_text="repaired prose",
            context={"scene_contract": {"goal": "test"}},
        )

    assert result["ok"] is False
    assert result["quality_gate"]["new_blocking_violations"] == ["fact_conflict"]


def test_passed_validator_hooks_are_not_scheduled():
    packet = CompiledSkillPacket(
        agent_name="core_generation",
        repair_hooks={"voice_fingerprint": ["voice_reconstruction"]},
    )

    hooks = AgentSkillRepairDispatcher._collect_hooks(
        packet,
        {
            "validators": {
                "voice_fingerprint": {
                    "validator": "voice_fingerprint",
                    "passed": True,
                    "repair_hooks": ["voice_reconstruction"],
                }
            },
            "failures": [],
        },
    )

    assert hooks == []


@pytest.mark.asyncio
async def test_step_validation_accepts_partial_metric_improvement_without_regression():
    packet = CompiledSkillPacket(agent_name="core_generation")
    llm = AsyncMock()
    llm.generate.return_value = "candidate"
    initial = {
        "passed": False,
        "validators": {},
        "failures": [
            {
                "validator": "ai_flavor",
                "metric": "dash_per_1000",
                "actual": 10.0,
                "expected_max": 2.5,
                "action": "prose_repair",
            },
            {
                "validator": "ai_discourse",
                "metric": "paragraph_shape_repeat_count",
                "actual": 5,
                "expected_max": 1,
                "action": "paragraph_reconstruction",
            },
        ],
    }
    improved = {
        "passed": False,
        "validators": {},
        "failures": [
            {
                "validator": "ai_flavor",
                "metric": "dash_per_1000",
                "actual": 4.0,
                "expected_max": 2.5,
                "action": "prose_repair",
            },
            initial["failures"][1],
        ],
    }
    validate_candidate = AsyncMock(return_value=improved)

    result = await AgentSkillRepairDispatcher().dispatch(
        packet,
        validation=initial,
        generated_text="original",
        context={},
        llm=llm,
        max_tokens=1000,
        validate_candidate=validate_candidate,
    )

    assert result["completed"] is True
    assert result["text"] == "candidate"
    assert result["attempts"][0]["decision"]["accepted"] is True


@pytest.mark.asyncio
async def test_step_validation_rejects_candidate_that_adds_new_failure():
    packet = CompiledSkillPacket(agent_name="core_generation")
    llm = AsyncMock()
    llm.generate.return_value = "regressed candidate"
    initial = {
        "passed": False,
        "validators": {},
        "failures": [
            {
                "validator": "ai_flavor",
                "metric": "dash_per_1000",
                "actual": 10.0,
                "expected_max": 2.5,
                "action": "prose_repair",
            }
        ],
    }
    regressed = {
        "passed": False,
        "validators": {},
        "failures": [
            {
                "validator": "fact_consistency",
                "metric": "fact_conflict",
                "actual": 1,
                "expected_max": 0,
                "action": "fact_repair",
            }
        ],
    }

    result = await AgentSkillRepairDispatcher().dispatch(
        packet,
        validation=initial,
        generated_text="original",
        context={},
        llm=llm,
        max_tokens=1000,
        validate_candidate=AsyncMock(return_value=regressed),
    )

    assert result["completed"] is False
    assert result["text"] == "original"
    assert result["attempts"][0]["status"] == "rejected_regression"
    assert result["attempts"][0]["decision"]["new_failures"] == [
        "fact_consistency:fact_conflict"
    ]
    assert result["attempts"][0]["decision"]["fatal_regressions"] == [
        "fact_consistency:fact_conflict"
    ]


@pytest.mark.asyncio
async def test_repairable_regression_enters_cascade_cleanup():
    packet = CompiledSkillPacket(agent_name="core_generation")
    llm = AsyncMock()
    llm.generate.side_effect = ["paragraph candidate", "cleanup candidate"]
    initial = {
        "passed": False,
        "validators": {},
        "failures": [
            {
                "validator": "ai_discourse",
                "metric": "paragraph_shape_repeat_count",
                "actual": 10,
                "expected_max": 1,
                "action": "paragraph_reconstruction",
            }
        ],
    }
    staged = {
        "passed": False,
        "validators": {},
        "failures": [
            {
                "validator": "ai_discourse",
                "metric": "paragraph_shape_repeat_count",
                "actual": 2,
                "expected_max": 1,
                "action": "paragraph_reconstruction",
            },
            {
                "validator": "ai_flavor",
                "metric": "dash_per_1000",
                "actual": 4.0,
                "expected_max": 2.5,
                "action": "prose_repair",
            },
        ],
    }
    passed = {"passed": True, "validators": {}, "failures": []}

    result = await AgentSkillRepairDispatcher().dispatch(
        packet,
        validation=initial,
        generated_text="original",
        context={},
        llm=llm,
        max_tokens=1000,
        validate_candidate=AsyncMock(side_effect=[staged, passed]),
    )

    assert result["completed"] is True
    assert result["text"] == "cleanup candidate"
    assert result["hooks"] == ["paragraph_reconstruction", "prose_repair"]
    assert result["attempts"][0]["status"] == "staged_repairable_regression"
    assert result["attempts"][0]["decision"]["repairable_regressions"] == [
        "ai_flavor:dash_per_1000"
    ]
    assert result["attempts"][1]["hook"] == "prose_repair"
    assert result["contract_envelope"]["role"] == "skill_contract_envelope"


@pytest.mark.asyncio
async def test_step_validation_skips_later_hook_when_first_hook_resolves_it():
    packet = CompiledSkillPacket(agent_name="core_generation")
    llm = AsyncMock()
    llm.generate.return_value = "fully repaired"
    initial = {
        "passed": False,
        "validators": {},
        "failures": [
            {
                "validator": "ai_flavor",
                "metric": "dash_per_1000",
                "actual": 10.0,
                "expected_max": 2.5,
                "action": "prose_repair",
            },
            {
                "validator": "ai_discourse",
                "metric": "paragraph_shape_repeat_count",
                "actual": 5,
                "expected_max": 1,
                "action": "paragraph_reconstruction",
            },
        ],
    }
    passed = {"passed": True, "validators": {}, "failures": []}

    result = await AgentSkillRepairDispatcher().dispatch(
        packet,
        validation=initial,
        generated_text="original",
        context={},
        llm=llm,
        max_tokens=1000,
        validate_candidate=AsyncMock(return_value=passed),
    )

    assert result["hooks"] == ["prose_repair"]
    assert result["attempts"][1]["status"] == "skipped_resolved"
    assert llm.generate.await_count == 1

@pytest.mark.asyncio
async def test_step_validation_rejects_regression_in_sibling_target_metric():
    packet = CompiledSkillPacket(agent_name="core_generation")
    llm = AsyncMock()
    llm.generate.return_value = "mixed candidate"
    initial = {
        "passed": False,
        "validators": {},
        "failures": [
            {
                "validator": "ai_flavor",
                "metric": "dash_per_1000",
                "actual": 5.0,
                "expected_max": 2.5,
                "action": "prose_repair",
            },
            {
                "validator": "ai_flavor",
                "metric": "high_advisories",
                "actual": 5,
                "expected_max": 0,
                "action": "prose_repair",
            },
        ],
    }
    mixed = {
        "passed": False,
        "validators": {},
        "failures": [
            {
                "validator": "ai_flavor",
                "metric": "dash_per_1000",
                "actual": 10.0,
                "expected_max": 2.5,
                "action": "prose_repair",
            },
            {
                "validator": "ai_flavor",
                "metric": "high_advisories",
                "actual": 1,
                "expected_max": 0,
                "action": "prose_repair",
            },
        ],
    }

    result = await AgentSkillRepairDispatcher().dispatch(
        packet,
        validation=initial,
        generated_text="original",
        context={},
        llm=llm,
        max_tokens=1000,
        validate_candidate=AsyncMock(return_value=mixed),
    )

    assert result["completed"] is False
    assert result["attempts"][0]["status"] == "rejected_regression"
    assert result["attempts"][0]["decision"]["regressed_target_failures"] == [
        "ai_flavor:dash_per_1000"
    ]


@pytest.mark.asyncio
async def test_dash_density_uses_deterministic_micro_repair_before_llm():
    packet = CompiledSkillPacket(agent_name="core_generation")
    llm = AsyncMock()
    dash = "\u2014\u2014"
    original = f"a{dash}b{dash}c{dash}d"

    result = await AgentSkillRepairDispatcher().dispatch(
        packet,
        validation={
            "validators": {},
            "failures": [
                {
                    "validator": "ai_flavor",
                    "metric": "dash_per_1000",
                    "actual": 10.0,
                    "expected_max": 2.5,
                    "action": "prose_repair",
                }
            ],
        },
        generated_text=original,
        context={},
        llm=llm,
        max_tokens=1000,
    )

    assert result["completed"] is True
    assert result["attempts"][0]["deterministic"] is True
    assert result["text"] != original
    assert dash not in result["text"]
    llm.generate.assert_not_awaited()

@pytest.mark.asyncio
async def test_staged_cleanup_can_reuse_completed_hook(monkeypatch):
    packet = CompiledSkillPacket(agent_name="core_generation")
    runtime = AsyncMock()
    runtime.has_executable_hook = lambda hook: True
    runtime.execute_step.side_effect = [
        {"status": "completed", "hook": "prose_repair", "text": "best prose"},
        {"status": "completed", "hook": "pacing_repair", "text": "staged pacing"},
        {"status": "completed", "hook": "prose_repair", "text": "cleaned pacing"},
    ]
    monkeypatch.setattr(
        "app.services.agent_skill_repair_dispatcher.get_fbi_skill_repair_runtime",
        lambda: runtime,
    )
    initial = {
        "passed": False,
        "validators": {},
        "failures": [
            {
                "validator": "ai_flavor",
                "metric": "dash_per_1000",
                "actual": 6.0,
                "expected_max": 2.5,
                "action": "prose_repair",
            },
            {
                "validator": "rhythm_control",
                "metric": "abrupt_shift_count",
                "actual": 8,
                "expected_max": 0,
                "action": "pacing_repair",
            },
        ],
    }
    after_prose = {
        "passed": False,
        "validators": {},
        "failures": [initial["failures"][1]],
    }
    staged = {
        "passed": False,
        "validators": {},
        "failures": [
            {
                "validator": "rhythm_control",
                "metric": "abrupt_shift_count",
                "actual": 2,
                "expected_max": 0,
                "action": "pacing_repair",
            },
            {
                "validator": "ai_flavor",
                "metric": "dash_per_1000",
                "actual": 10.0,
                "expected_max": 2.5,
                "action": "prose_repair",
            },
        ],
    }
    passed = {"passed": True, "validators": {}, "failures": []}

    result = await AgentSkillRepairDispatcher().dispatch(
        packet,
        validation=initial,
        generated_text="original",
        context={},
        llm=AsyncMock(),
        max_tokens=1000,
        validate_candidate=AsyncMock(side_effect=[after_prose, staged, passed]),
    )

    assert result["completed"] is True
    assert result["text"] == "cleaned pacing"
    assert result["hooks"] == ["prose_repair", "pacing_repair", "prose_repair"]
    assert [call.kwargs["hook"] for call in runtime.execute_step.await_args_list] == [
        "prose_repair",
        "pacing_repair",
        "prose_repair",
    ]
    assert result["attempts"][1]["status"] == "staged_repairable_regression"
    assert result["convergence"]["final_passed"] is True


@pytest.mark.asyncio
async def test_unresolved_staged_candidate_falls_back_to_best_accepted_text(monkeypatch):
    packet = CompiledSkillPacket(agent_name="core_generation")
    runtime = AsyncMock()
    runtime.has_executable_hook = lambda hook: True
    runtime.execute_step.side_effect = [
        {"status": "completed", "hook": "prose_repair", "text": "best prose"},
        {"status": "completed", "hook": "pacing_repair", "text": "staged pacing"},
        {"status": "failed", "hook": "prose_repair", "reason": "cleanup_failed"},
    ]
    monkeypatch.setattr(
        "app.services.agent_skill_repair_dispatcher.get_fbi_skill_repair_runtime",
        lambda: runtime,
    )
    initial = {
        "passed": False,
        "validators": {},
        "failures": [
            {
                "validator": "ai_flavor",
                "metric": "dash_per_1000",
                "actual": 6.0,
                "expected_max": 2.5,
                "action": "prose_repair",
            },
            {
                "validator": "rhythm_control",
                "metric": "abrupt_shift_count",
                "actual": 8,
                "expected_max": 0,
                "action": "pacing_repair",
            },
        ],
    }
    after_prose = {
        "passed": False,
        "validators": {},
        "failures": [initial["failures"][1]],
    }
    staged = {
        "passed": False,
        "validators": {},
        "failures": [
            {
                "validator": "rhythm_control",
                "metric": "abrupt_shift_count",
                "actual": 2,
                "expected_max": 0,
                "action": "pacing_repair",
            },
            {
                "validator": "ai_flavor",
                "metric": "dash_per_1000",
                "actual": 10.0,
                "expected_max": 2.5,
                "action": "prose_repair",
            },
        ],
    }

    result = await AgentSkillRepairDispatcher().dispatch(
        packet,
        validation=initial,
        generated_text="original",
        context={},
        llm=AsyncMock(),
        max_tokens=1000,
        validate_candidate=AsyncMock(side_effect=[after_prose, staged]),
    )

    assert result["completed"] is True
    assert result["text"] == "best prose"
    assert result["validation"] == after_prose
    assert result["attempts"][-1]["status"] == "failed"
    assert result["convergence"]["final_passed"] is False
    assert result["convergence"]["used_best_candidate_fallback"] is True
    assert result["convergence"]["used_staged_candidate_fallback"] is False
    assert result["convergence"]["discarded_staged_candidate"] is False


@pytest.mark.asyncio
async def test_unresolved_staged_candidate_can_be_best_recoverable_fallback(monkeypatch):
    packet = CompiledSkillPacket(agent_name="core_generation")
    runtime = AsyncMock()
    runtime.has_executable_hook = lambda hook: True
    runtime.execute_step.side_effect = [
        {"status": "completed", "hook": "prose_repair", "text": "minor prose"},
        {"status": "completed", "hook": "paragraph_reconstruction", "text": "strong paragraph"},
        {"status": "failed", "hook": "pacing_repair", "reason": "cleanup_failed"},
    ]
    monkeypatch.setattr(
        "app.services.agent_skill_repair_dispatcher.get_fbi_skill_repair_runtime",
        lambda: runtime,
    )
    initial = {
        "passed": False,
        "validators": {},
        "failures": [
            {
                "validator": "ai_flavor",
                "metric": "dash_per_1000",
                "actual": 6.0,
                "expected_max": 2.5,
                "action": "prose_repair",
            },
            {
                "validator": "ai_discourse",
                "metric": "paragraph_shape_repeat_count",
                "actual": 42,
                "expected_max": 1,
                "action": "paragraph_reconstruction",
            },
        ],
    }
    after_prose = {
        "passed": False,
        "validators": {},
        "failures": [
            initial["failures"][1],
        ],
    }
    staged = {
        "passed": False,
        "validators": {},
        "failures": [
            {
                "validator": "ai_discourse",
                "metric": "paragraph_shape_repeat_count",
                "actual": 3,
                "expected_max": 1,
                "action": "paragraph_reconstruction",
            },
            {
                "validator": "rhythm_metrics",
                "metric": "uniform_sentence_streak_max",
                "actual": 5,
                "expected_max": 3,
                "action": "vary_sentence_length",
            },
        ],
    }

    result = await AgentSkillRepairDispatcher().dispatch(
        packet,
        validation=initial,
        generated_text="original",
        context={},
        llm=AsyncMock(),
        max_tokens=1000,
        validate_candidate=AsyncMock(side_effect=[after_prose, staged]),
    )

    assert result["completed"] is True
    assert result["text"] == "strong paragraph"
    assert result["validation"] == staged
    assert result["hooks"] == ["prose_repair", "paragraph_reconstruction"]
    assert result["attempts"][1]["status"] == "staged_repairable_regression"
    assert result["attempts"][1]["best_candidate"] is True
    assert result["convergence"]["final_passed"] is False
    assert result["convergence"]["used_best_candidate_fallback"] is True
    assert result["convergence"]["used_staged_candidate_fallback"] is True


@pytest.mark.asyncio
async def test_cleanup_step_is_deduped_by_metric_not_hook(monkeypatch):
    packet = CompiledSkillPacket(agent_name="core_generation")
    runtime = AsyncMock()
    runtime.has_executable_hook = lambda hook: True
    runtime.execute_step.side_effect = [
        {"status": "completed", "hook": "paragraph_reconstruction", "text": "staged paragraph"},
        {"status": "completed", "hook": "prose_repair", "text": "dash cleaned"},
    ]
    monkeypatch.setattr(
        "app.services.agent_skill_repair_dispatcher.get_fbi_skill_repair_runtime",
        lambda: runtime,
    )
    initial = {
        "passed": False,
        "validators": {},
        "failures": [
            {
                "validator": "ai_discourse",
                "metric": "paragraph_shape_repeat_count",
                "actual": 30,
                "expected_max": 1,
                "action": "paragraph_reconstruction",
            },
            {
                "validator": "ai_flavor",
                "metric": "structure_word_cluster_count",
                "actual": 1,
                "expected_max": 0,
                "action": "prose_repair",
            },
        ],
    }
    staged = {
        "passed": False,
        "validators": {},
        "failures": [
            {
                "validator": "ai_discourse",
                "metric": "paragraph_shape_repeat_count",
                "actual": 2,
                "expected_max": 1,
                "action": "paragraph_reconstruction",
            },
            {
                "validator": "ai_flavor",
                "metric": "dash_per_1000",
                "actual": 3.33,
                "expected_max": 2.5,
                "action": "prose_repair",
            },
        ],
    }
    passed = {"passed": True, "validators": {}, "failures": []}

    result = await AgentSkillRepairDispatcher().dispatch(
        packet,
        validation=initial,
        generated_text="original",
        context={},
        llm=AsyncMock(),
        max_tokens=1000,
        validate_candidate=AsyncMock(side_effect=[staged, passed]),
    )

    assert result["completed"] is True
    assert result["text"] == "dash cleaned"
    assert [call.kwargs["hook"] for call in runtime.execute_step.await_args_list] == [
        "paragraph_reconstruction",
        "prose_repair",
    ]
    assert result["attempts"][1]["status"] == "skipped_resolved"
    assert result["attempts"][1]["metrics"] == ["structure_word_cluster_count"]
    assert result["attempts"][2]["hook"] == "prose_repair"
    assert result["attempts"][2]["metrics"] == ["dash_per_1000"]
    assert result["repair_plan"][-1]["metrics"] == ["dash_per_1000"]

