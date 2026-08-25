from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.models.repair_order import RepairOrder
from app.models.review_finding_v2 import ReviewFindingV2
from app.services.fbi_v2.orchestrator import FBIV2Orchestrator
from app.services.quality_finding_hub import QualityFindingHub
from app.services.repair_intent_dispatcher import RepairIntentDispatcher
from app.services.repair_order_compiler import RepairOrderCompiler


BOOK_ANCHOR = "She lowered her eyes to the booklet in her hand."
BOOK_TEXT = "She looked at the booklet on the desk.\n\nWind moved through the window.\n\n" + BOOK_ANCHOR
BOOK_GOAL = "Bridge the booklet moving from the desk into her hand."


class FakeGateway:
    def __init__(self, payload):
        self.payload = payload

    async def generate_json(self, **_kwargs):
        return SimpleNamespace(
            ok=True,
            parsed_json=self.payload,
            model="fake-dispatch-model",
            error_message=None,
            error_type=None,
        )


def _book_gap_finding() -> ReviewFindingV2:
    return QualityFindingHub().normalize_violation({
        "violation_id": "book_gap_001",
        "source": "consistency",
        "type": "unclassified_review_issue",
        "severity": "high",
        "detail": "Last sentence implies she holds a booklet, but the action sequence never bridges it.",
        "expected_behavior": BOOK_GOAL,
        "blocks_commit": True,
        "scope": "prose_text",
    })


@pytest.mark.asyncio
async def test_llm_dispatcher_compiles_unclassified_gap_to_rewrite_window(monkeypatch):
    finding = _book_gap_finding()
    assert finding.repair_lane == "none"

    payload = {
        "semantic_type": "object_state_continuity_gap",
        "repair_class": "action_continuity",
        "repair_lane": "fbi_fact",
        "operation": "rewrite_window",
        "anchor_text": BOOK_ANCHOR,
        "window": {"before_paragraphs": 2, "after_paragraphs": 0},
        "repair_goal": BOOK_GOAL,
        "must_preserve": ["time anchor"],
        "must_avoid": ["Do not add unauthorized events."],
        "recheck": ["object_state_continuity", "action_sequence"],
        "confidence": 0.91,
    }
    import app.services.repair_intent_dispatcher as dispatcher_module

    monkeypatch.setattr(dispatcher_module, "get_llm_gateway", lambda: FakeGateway(payload))

    dispatched, reports = await RepairIntentDispatcher().dispatch_batch(
        [finding],
        candidate_text=BOOK_TEXT,
        context={},
    )

    assert reports[0]["status"] == "dispatched"
    routed = dispatched[0]
    assert routed.repair_lane == "fbi_fact"
    assert routed.repair_intent["operation"] == "rewrite_window"
    assert routed.repair_intent["window_text"] in BOOK_TEXT

    order = RepairOrderCompiler().compile(routed)
    assert order.lane == "fbi_fact"
    assert order.operation == "rewrite_window"
    assert order.target_region["anchor_text"] == BOOK_ANCHOR
    assert order.target_region["repair_goal"] == BOOK_GOAL


@pytest.mark.asyncio
async def test_dispatcher_rejects_missing_replace_span_and_downgrades_to_window(monkeypatch):
    finding = _book_gap_finding()
    payload = {
        "semantic_type": "object_state_continuity_gap",
        "repair_lane": "fbi_fact",
        "operation": "replace_span",
        "target_text": "missing source span",
        "anchor_text": BOOK_ANCHOR,
        "repair_goal": BOOK_GOAL,
        "confidence": 0.83,
    }
    import app.services.repair_intent_dispatcher as dispatcher_module

    monkeypatch.setattr(dispatcher_module, "get_llm_gateway", lambda: FakeGateway(payload))

    dispatched, _reports = await RepairIntentDispatcher().dispatch_batch(
        [finding],
        candidate_text=BOOK_TEXT,
        context={},
    )

    assert dispatched[0].repair_intent["operation"] == "rewrite_window"
    assert dispatched[0].repair_intent["target_text"] == "missing source span"


@pytest.mark.asyncio
async def test_low_confidence_llm_intent_uses_conservative_fallback(monkeypatch):
    text = (
        "\u8fb0\u65f6\u4e09\u523b\uff0c\u5979\u4f4e\u5934\u770b\u7740\u684c\u4e0a\u7684\u4e66\u518c\u3002\n\n"
        "\u6700\u540e\uff0c\u5979\u5782\u773c\u770b\u7740\u624b\u91cc\u7684\u4e66\u518c\u3002"
    )
    anchor = "\u6700\u540e\uff0c\u5979\u5782\u773c\u770b\u7740\u624b\u91cc\u7684\u4e66\u518c\u3002"
    finding = ReviewFindingV2(
        id="book_gap_low_confidence",
        source="smoke",
        type="unclassified_review_issue",
        title="booklet action gap",
        description=(
            "\u6700\u540e\u4e00\u53e5\u6697\u793a\u5979\u62ff\u7740\u4e66\u518c\uff0c"
            "\u4f46\u524d\u6587\u52a8\u4f5c\u5e8f\u5217\u672a\u4ea4\u4ee3\u4e66\u518c\u4f4d\u7f6e\u3002"
        ),
        severity="S2",
        blocks_commit=True,
        repair_scope="prose_text",
        repair_lane="none",
        evidence_spans=[{"evidence": anchor}],
    )
    payload = {
        "semantic_type": "needs_manual",
        "repair_lane": "manual_only",
        "operation": "rewrite_scene",
        "anchor_text": anchor,
        "repair_goal": "Unsure.",
        "confidence": 0.0,
    }
    import app.services.repair_intent_dispatcher as dispatcher_module

    monkeypatch.setattr(dispatcher_module, "get_llm_gateway", lambda: FakeGateway(payload))

    dispatched, reports = await RepairIntentDispatcher().dispatch_batch(
        [finding],
        candidate_text=text,
        context={},
    )

    assert reports[0]["status"] == "fallback_dispatched"
    assert dispatched[0].repair_intent["semantic_type"] == "object_state_continuity_gap"
    assert dispatched[0].repair_intent["operation"] == "rewrite_window"


@pytest.mark.asyncio
async def test_manual_only_prose_issue_still_dispatches_to_llm(monkeypatch):
    finding = ReviewFindingV2(
        id="manual_only_001",
        source="critic",
        type="timeline_conflict",
        title="timeline conflict",
        description="正文写成两日后，但事实层要求三日后。",
        severity="S2",
        blocks_commit=True,
        user_visible=True,
        repair_scope="prose_text",
        repair_lane="manual_only",
        evidence_spans=[{"span": "两日后", "raw": {"expected_behavior": "正文应使用三日后。"}}],
    )
    payload = {
        "semantic_type": "temporal_direct_repair",
        "repair_lane": "fbi_fact",
        "operation": "rewrite_window",
        "anchor_text": "两日后",
        "repair_goal": "Replace the conflicting time phrase.",
        "confidence": 0.9,
    }
    import app.services.repair_intent_dispatcher as dispatcher_module

    monkeypatch.setattr(dispatcher_module, "get_llm_gateway", lambda: FakeGateway(payload))

    dispatched, reports = await RepairIntentDispatcher().dispatch_batch(
        [finding],
        candidate_text="苏云清说两日后见。",
        context={},
    )

    assert reports[0]["status"] == "dispatched"
    assert dispatched[0].repair_intent["operation"] == "rewrite_window"


@pytest.mark.asyncio
async def test_foreshadowing_gap_dispatches_to_insert_bridge_hint(monkeypatch):
    text = "She woke to rain and an old scar on her palm.\n\nWhen pressed, she did not answer at once."
    anchor = "She woke to rain and an old scar on her palm."
    finding = QualityFindingHub().normalize_violation({
        "violation_id": "fs_gap_001",
        "source": "fcip",
        "type": "causal_chain_error",
        "severity": "high",
        "detail": "No reincarnation or former-life foreshadowing appears before the reveal.",
        "expected_behavior": "Plant one clear but non-spoiling former-life clue.",
        "blocks_commit": True,
        "scope": "prose_text",
    })
    payload = {
        "semantic_type": "foreshadowing_completion_gap",
        "repair_class": "foreshadowing_completion",
        "repair_lane": "fbi_fact",
        "operation": "insert_bridge_hint",
        "anchor_text": anchor,
        "repair_goal": "Plant one non-spoiling former-life clue.",
        "must_preserve": [],
        "must_avoid": ["Do not reveal the identity truth early."],
        "recheck": ["foreshadowing_presence"],
        "confidence": 0.9,
    }
    import app.services.repair_intent_dispatcher as dispatcher_module

    monkeypatch.setattr(dispatcher_module, "get_llm_gateway", lambda: FakeGateway(payload))

    dispatched, reports = await RepairIntentDispatcher().dispatch_batch(
        [finding],
        candidate_text=text,
        context={},
    )
    order = RepairOrderCompiler().compile(dispatched[0])

    assert reports[0]["semantic_type"] == "foreshadowing_completion_gap"
    assert order.lane == "fbi_fact"
    assert order.operation == "insert_bridge_hint"
    assert order.target_region["anchor_text"] == anchor


@pytest.mark.asyncio
async def test_fbi_v2_invokes_dispatcher_for_lane_none_finding(monkeypatch):
    finding = _book_gap_finding()
    orchestrator = FBIV2Orchestrator()

    class FakeDispatcher:
        async def dispatch_batch(self, findings, *, candidate_text, context):
            intent = {
                "semantic_type": "object_state_continuity_gap",
                "repair_class": "action_continuity",
                "repair_lane": "fbi_fact",
                "operation": "insert_bridge_action",
                "anchor_text": "She looked at the booklet on the desk.",
                "repair_goal": BOOK_GOAL,
                "must_preserve": [],
                "must_avoid": [],
                "recheck": ["object_state_continuity"],
                "confidence": 0.88,
                "source": "test_dispatcher",
            }
            return [
                findings[0].model_copy(update={
                    "repair_lane": "fbi_fact",
                    "repair_intent": intent,
                    "retryable": True,
                })
            ], [{"finding_id": findings[0].id, "status": "dispatched"}]

    import app.services.repair_intent_dispatcher as dispatcher_module

    monkeypatch.setattr(dispatcher_module, "get_repair_intent_dispatcher", lambda: FakeDispatcher())

    async def fake_execute_llm(state, order, context):
        assert order.operation == "insert_bridge_action"
        patch = {
            "operation": "insert_after",
            "target_text": "She looked at the booklet on the desk.",
            "insert_text": "She closed it, kept a finger against the spine, and drew it into her palm.",
            "affected_issue_ids": list(order.issue_ids),
        }
        new_text, applied, _skipped = orchestrator._apply_patches(
            state.current_text,
            [patch],
            order=order,
            protected_spans=[],
        )
        order.mark_succeeded(applied)
        return {"text": new_text, "patches": applied, "changed": True}

    async def fake_recheck(*, text, context, round_num):
        return {
            "round": round_num,
            "passed": True,
            "violations": [],
            "findings": [],
            "report": {"passed": True, "violations": []},
        }

    monkeypatch.setattr(orchestrator, "_execute_llm", fake_execute_llm)
    monkeypatch.setattr(orchestrator, "_run_recheck", fake_recheck)

    outcome = await orchestrator.run(
        project_id="proj_dispatch",
        candidate_text=BOOK_TEXT,
        findings=[finding],
        context={},
        max_rounds=2,
    )

    assert outcome["status"] == "resolved"
    assert outcome["summary"]["dispatch_count"] == 1
    assert outcome["summary"]["succeeded"] == 1
    assert "drew it into her palm" in outcome["text"]
    assert outcome["dispatch_reports"][0]["status"] == "dispatched"


def test_fbi_v2_applies_rewrite_window_patch():
    orchestrator = FBIV2Orchestrator()
    text = "Paragraph one.\n\nParagraph two has a gap.\n\nParagraph three."
    order = RepairOrder(
        id="ro_window",
        issue_ids=["issue_window"],
        lane="fbi_fact",
        operation="rewrite_window",
    )

    new_text, applied, skipped = orchestrator._apply_patches(
        text,
        [{
            "operation": "rewrite_window",
            "target_text": "Paragraph two has a gap.",
            "replacement": "Paragraph two now bridges the missing action.",
        }],
        order=order,
        protected_spans=[],
    )

    assert not skipped
    assert applied[0]["operation"] == "rewrite_window"
    assert applied[0]["start"] >= 0
    assert "bridges the missing action" in new_text
