from __future__ import annotations

import pytest
from unittest.mock import AsyncMock

from app.agents.core_generation import CoreGenerationAgent
from app.models.agent_skill import CompiledSkillPacket


class FakeChapterWriterLLM:
    def __init__(self):
        self.calls = 0

    async def generate(self, **_kwargs):
        self.calls += 1
        if self.calls == 1:
            return "请提供更多场景内容。"
        return (
            "[[SCENE:c1-s1]]\n"
            "她在暗青色帐顶下醒来，先听见雨声。\n\n"
            "[[SCENE:c1-s2]]\n"
            "她把桌上的书册合拢，指尖压住封皮，顺手拿在掌中。"
        )


@pytest.mark.asyncio
async def test_chapter_writer_retries_when_scene_markers_are_missing(monkeypatch):
    fake_llm = FakeChapterWriterLLM()
    agent = CoreGenerationAgent()

    async def fake_get_llm_client():
        return fake_llm

    async def fake_prepare_skill_packet(_context):
        raise RuntimeError("skip skill runtime in marker retry unit test")

    monkeypatch.setattr(agent, "get_llm_client", fake_get_llm_client)
    monkeypatch.setattr(agent, "prepare_skill_packet", fake_prepare_skill_packet)
    result = await agent.execute({
        "chapter_writer_packet": {
            "system_prompt": "必须输出场景 marker。",
            "user_prompt": "写两个场景。",
            "scene_map": [
                {"scene_id": "c1-s1", "scene_index": 0},
                {"scene_id": "c1-s2", "scene_index": 1},
            ],
            "target_chars": 400,
            "hard_max_chars": 800,
            "token_report": {"reserve_output_tokens": 1200},
        }
    })

    # initial generation + semantic split probe + marker-enforced retry
    assert fake_llm.calls == 3
    assert result["prompt_debug"]["marker_retry_used"] is True
    assert result["prompt_debug"]["marker_check"]["ok"] is True
    assert result["prompt_debug"]["timing_ms"]["chapter_writer_total"] >= 0
    assert [call["phase"] for call in result["prompt_debug"]["llm_calls"]] == [
        "llm_initial_generate",
        "llm_marker_retry_generate",
    ]
    assert result["prompt_debug"]["llm_calls"][0]["max_tokens"] >= 4096
    assert result["prompt_debug"]["llm_calls"][1]["output_chars"] > 0
    assert "[[SCENE:c1-s2]]" in result["generated_text"]


@pytest.mark.asyncio
async def test_chapter_writer_defers_skill_repair_to_review_fbi(monkeypatch):
    agent = CoreGenerationAgent()
    generated = "[[SCENE:s1]]\nThe door stayed shut."

    class FakeLLM:
        async def generate(self, **_kwargs):
            return generated

    class FakeRuntime:
        async def validate_output(self, *_args, **_kwargs):
            return {
                "passed": False,
                "failures": [
                    {
                        "validator": "ai_flavor",
                        "metric": "dash_per_1000",
                        "retry_policy": {"max_retries": 1, "action": "style_repair"},
                    }
                ],
                "validators": {},
            }

    dispatcher = type("Dispatcher", (), {"dispatch": AsyncMock()})()

    async def fake_get_llm_client():
        return FakeLLM()

    async def fake_get_agent_runtime_detail():
        return {}

    async def fake_prepare_skill_packet(_context):
        return CompiledSkillPacket(
            agent_name="core_generation",
            active_skills=["anti_ai_prose"],
            validators=["ai_flavor"],
            validation_contracts={},
            repair_hooks={"ai_flavor": ["prose_repair"]},
        )

    async def fake_execute_skill_services(*_args, **_kwargs):
        return {}

    monkeypatch.setattr(agent, "get_llm_client", fake_get_llm_client)
    monkeypatch.setattr(agent, "get_agent_runtime_detail", fake_get_agent_runtime_detail)
    monkeypatch.setattr(agent, "prepare_skill_packet", fake_prepare_skill_packet)
    monkeypatch.setattr(agent, "_execute_skill_services", fake_execute_skill_services)
    monkeypatch.setattr("app.services.agent_skill_runtime.get_skill_runtime", lambda: FakeRuntime())
    monkeypatch.setattr(
        "app.services.agent_skill_repair_dispatcher.get_skill_repair_dispatcher",
        lambda: dispatcher,
    )

    result = await agent.execute({
        "chapter_writer_packet": {
            "system_prompt": "Write one scene.",
            "user_prompt": "Write.",
            "scene_map": [{"scene_id": "s1", "scene_index": 0}],
            "target_chars": 200,
            "hard_max_chars": 400,
        }
    })

    assert result["generated_text"] == generated.replace(".", "\u3002")
    assert result["prompt_debug"]["skill_trace"]["validation"]["passed"] is False
    assert result["prompt_debug"]["skill_trace"]["repair"]["disabled"] is True
    assert result["prompt_debug"]["skill_trace"]["repair"]["deferred_to"] == "parallel_review_and_fbi_repair"
    assert "skill_validate_only_total" in result["prompt_debug"]["timing_ms"]
    assert "skill_repair_dispatch" not in result["prompt_debug"]["timing_ms"]
    dispatcher.dispatch.assert_not_awaited()
