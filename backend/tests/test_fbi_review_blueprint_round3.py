from __future__ import annotations

import asyncio

from app.agents.fbi.review_blueprint_agent import FBIReviewBlueprintAgent


def test_run_session_passes_diagnosis_set_to_round3(monkeypatch) -> None:
    agent = FBIReviewBlueprintAgent()
    diagnosis_set = object()
    captured: dict[str, object] = {}

    async def fake_get_llm_client(context):
        return object()

    async def fake_round1(llm_client, messages, annotated, round_traces):
        return {
            "status": "ready",
            "review_results": [{"issue_id": "issue-1", "route": "llm"}],
            "new_messages": [],
        }

    async def fake_round2(
        llm_client,
        messages,
        annotated,
        received_diagnosis_set,
        review_results,
        round_traces,
    ):
        assert received_diagnosis_set is diagnosis_set
        return {"det_work_units": [], "new_messages": []}

    async def fake_round3(
        llm_client,
        messages,
        annotated,
        llm_issues,
        det_work_units,
        round_traces,
        *,
        diagnosis_set,
        context=None,
    ):
        captured["diagnosis_set"] = diagnosis_set
        captured["context"] = context
        return {"status": "ready", "llm_work_units": []}

    monkeypatch.setattr(agent, "_get_llm_client", fake_get_llm_client)
    monkeypatch.setattr(agent, "_build_initial_messages", lambda *args: [])
    monkeypatch.setattr(agent, "_run_round1", fake_round1)
    monkeypatch.setattr(agent, "_run_round2", fake_round2)
    monkeypatch.setattr(agent, "_run_round3", fake_round3)

    context = {"project_id": "project-1"}
    result = asyncio.run(agent._run_session(context, [], diagnosis_set))

    assert result["status"] == "ready"
    assert captured == {"diagnosis_set": diagnosis_set, "context": context}
