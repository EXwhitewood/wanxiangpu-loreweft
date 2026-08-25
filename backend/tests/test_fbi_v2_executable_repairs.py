from __future__ import annotations

import pytest

from app.services.deterministic_repair_engine import (
    TimeAnchorRepairer,
    get_deterministic_repair_engine,
)
from app.services.fbi_v2.orchestrator import FBIV2Orchestrator
from app.services.quality_finding_hub import QualityFindingHub
from app.services.repair_order_compiler import RepairOrderCompiler


def _timeline_violation(target_span: str = "是正午的光，白晃晃的") -> dict:
    return {
        "violation_id": "tl_case_001",
        "source": "consistency",
        "type": "timeline_conflict",
        "severity": "high",
        "detail": "时间锚点指定为辰时三刻，但正文暗示正午。",
        "target_span": target_span,
        "expected_behavior": "时间应保持辰时三刻，不应出现正午描写。",
        "blocks_commit": True,
        "scope": "prose_text",
    }


def _relative_day_violation() -> dict:
    return {
        "violation_id": "tl_case_002",
        "source": "critic",
        "type": "canon_timeline_confusion",
        "severity": "high",
        "detail": "正文中苏云清和凤溪均提及两日后就是宗门小比，但事实层明确三日后宗门将举行小比，时间不一致。",
        "target_span": "“听说两日后就是宗门小比”、“两日后就是宗门小比”",
        "expected_behavior": "正文应使用“三日后”以匹配事实层时间锚点。",
        "blocks_commit": True,
        "scope": "prose_text",
    }


def test_temporal_anchor_pre_gate_detects_noon_conflict():
    text = "角色A下意识地眯了眯眼。是正午的光，白晃晃的。"
    conflicts = TimeAnchorRepairer.detect_conflicts(
        text,
        {"scene_contract": {"temporal_anchor": "辰时三刻"}},
    )

    assert len(conflicts) == 1
    assert conflicts[0]["type"] == "timeline_conflict"
    assert "正午" in conflicts[0]["target_span"]


def test_timeline_conflict_compiles_to_executable_time_order():
    finding = QualityFindingHub().normalize_violation(_timeline_violation())
    order = RepairOrderCompiler().compile(finding)

    assert finding.repair_lane == "deterministic"
    assert order.lane == "deterministic"
    assert order.operation == "repair_time_anchor"
    assert order.target_region
    assert order.target_region["text"] == "是正午的光，白晃晃的"


@pytest.mark.asyncio
async def test_time_anchor_repairer_replaces_noon_light():
    text = "角色A下意识地眯了眯眼。是正午的光，白晃晃的。"
    finding = QualityFindingHub().normalize_violation(_timeline_violation())
    order = RepairOrderCompiler().compile(finding)

    result = await get_deterministic_repair_engine().execute(
        order,
        text,
        {"scene_contract": {"temporal_anchor": "辰时三刻"}},
    )

    assert result["changed"] is True
    assert "正午" not in result["text"]
    assert "晨间" in result["text"]
    assert result["patches"][0]["operation"] == "replace_span"
    assert result["patches"][0]["start"] >= 0
    assert result["patches"][0]["affected_issue_ids"] == ["tl_case_001"]


@pytest.mark.asyncio
async def test_fbi_v2_resolves_timeline_conflict_with_deterministic_patch(monkeypatch):
    text = "角色A下意识地眯了眯眼。是正午的光，白晃晃的。"
    finding = QualityFindingHub().normalize_violation(_timeline_violation())
    orchestrator = FBIV2Orchestrator()

    async def fake_recheck(*, text, context, round_num):
        return {
            "round": round_num,
            "passed": True,
            "violations": [],
            "findings": [],
            "report": {"passed": True, "violations": []},
        }

    monkeypatch.setattr(orchestrator, "_run_recheck", fake_recheck)

    outcome = await orchestrator.run(
        project_id="proj_test",
        candidate_text=text,
        findings=[finding],
        context={"scene_contract": {"temporal_anchor": "辰时三刻"}},
        max_rounds=3,
    )

    assert outcome["status"] == "resolved"
    assert outcome["summary"]["succeeded"] == 1
    assert outcome["patches"]
    assert "正午" not in outcome["text"]
    assert outcome["protected_spans"]


@pytest.mark.asyncio
async def test_relative_day_conflict_flows_through_fbi_v2_dispatch():
    text = "苏云清低声道：“听说两日后就是宗门小比。”凤溪点头，又重复了一遍两日后就是宗门小比。"
    finding = QualityFindingHub().normalize_violation(_relative_day_violation())
    order = RepairOrderCompiler().compile(finding)
    result = await get_deterministic_repair_engine().execute(
        order,
        text,
        {"scene_contract": {"temporal_anchor": "三日后宗门将举行小比"}},
    )

    assert finding.type == "timeline_conflict"
    assert finding.repair_lane == "deterministic"
    assert order.operation == "repair_time_anchor"
    assert result["changed"] is True
    assert "两日后" not in result["text"]
    assert result["text"].count("三日后") >= 2
    assert len(result["patches"]) == 2


@pytest.mark.asyncio
async def test_fbi_v2_escalates_after_no_op_repair(monkeypatch):
    finding = QualityFindingHub().normalize_violation(_timeline_violation(target_span="不存在的冲突片段"))
    orchestrator = FBIV2Orchestrator()
    llm_lanes: list[str] = []

    async def fake_recheck(*, text, context, round_num):
        return {
            "round": round_num,
            "passed": False,
            "violations": [],
            "findings": [finding],
            "report": {"passed": False, "violations": []},
        }

    async def fake_execute_llm(state, order, context):
        llm_lanes.append(order.lane)
        order.mark_failed("forced no patch")
        return {"changed": False, "patches": []}

    monkeypatch.setattr(orchestrator, "_run_recheck", fake_recheck)
    monkeypatch.setattr(orchestrator, "_execute_llm", fake_execute_llm)

    outcome = await orchestrator.run(
        project_id="proj_test",
        candidate_text="角色A站在地点Y，天色平平。",
        findings=[finding],
        context={"scene_contract": {"temporal_anchor": "辰时三刻"}},
        max_rounds=3,
    )

    assert outcome["status"] == "needs_workbench"
    assert llm_lanes == ["fbi_fact", "fbi_scene_restructure"]
    assert [item["to_lane"] for item in outcome["escalations"]] == [
        "fbi_fact",
        "fbi_scene_restructure",
    ]


def test_forbidden_triggered_without_scope_compiles_to_text_delete():
    finding = QualityFindingHub().normalize_violation({
        "violation_id": "fb_case_001",
        "source": "narrative_contract",
        "type": "forbidden_triggered",
        "severity": "high",
        "detail": "forbidden content appeared",
        "target_span": "她搜查了密室",
    })
    order = RepairOrderCompiler().compile(finding)

    assert finding.repair_scope == "prose_text"
    assert order.lane == "deterministic"
    assert order.operation == "delete_span"
    assert order.target_region == {"text": "她搜查了密室"}


@pytest.mark.asyncio
async def test_forbidden_event_repairer_deletes_containing_sentence():
    text = "她停在门口。她搜查了密室，翻出一封信。她转身离开。"
    finding = QualityFindingHub().normalize_violation({
        "violation_id": "fb_case_002",
        "source": "narrative_proposition",
        "type": "forbidden_assertion_triggered",
        "severity": "high",
        "detail": "禁止提前搜查密室",
        "target_span": "她搜查了密室",
    })
    order = RepairOrderCompiler().compile(finding)

    result = await get_deterministic_repair_engine().execute(order, text, {})

    assert order.operation == "remove_forbidden_event"
    assert result["changed"] is True
    assert "搜查了密室" not in result["text"]
    assert "翻出一封信" not in result["text"]
    assert result["patches"][0]["repairer"] == "ForbiddenEventRepairer"
    assert result["patches"][0]["operation"] == "delete_span"


@pytest.mark.asyncio
async def test_ending_completion_repairer_appends_required_state():
    text = "角色A低头不语。"
    finding = QualityFindingHub().normalize_violation({
        "violation_id": "end_case_001",
        "source": "critic",
        "type": "ending_state_not_reached",
        "severity": "high",
        "detail": "结尾状态未达成",
        "expected_behavior": "角色A握住玉佩，决定留下",
    })
    order = RepairOrderCompiler().compile(finding)

    result = await get_deterministic_repair_engine().execute(order, text, {})

    assert order.operation == "complete_ending_state"
    assert result["changed"] is True
    assert result["text"].endswith("角色A握住玉佩，决定留下。")
    assert result["patches"][0]["operation"] == "append_tail"
    assert result["patches"][0]["repairer"] == "EndingCompletionRepairer"


@pytest.mark.asyncio
async def test_fact_state_repairer_replaces_direct_conflict_span():
    text = "众人都知道，他如今已是内门弟子。"
    finding = QualityFindingHub().normalize_violation({
        "violation_id": "fact_case_001",
        "source": "consistency",
        "type": "identity_conflict",
        "severity": "high",
        "detail": "身份称谓冲突",
        "target_span": "内门弟子",
        "expected_behavior": "改为「外门弟子」",
    })
    order = RepairOrderCompiler().compile(finding)

    result = await get_deterministic_repair_engine().execute(order, text, {})

    assert order.operation == "repair_fact_state"
    assert result["changed"] is True
    assert "外门弟子" in result["text"]
    assert "内门弟子" not in result["text"]
    assert result["patches"][0]["repairer"] == "FactStateRepairer"


@pytest.mark.asyncio
async def test_ai_style_cleanup_removes_formulaic_explanation():
    text = "她攥紧袖口。这意味着，她已经没有退路。"
    finding = QualityFindingHub().normalize_violation({
        "violation_id": "style_case_001",
        "source": "scene_credibility",
        "type": "narration_explanation_artifact",
        "severity": "high",
        "detail": "解释腔过重",
        "target_span": "这意味着",
    })
    order = RepairOrderCompiler().compile(finding)

    result = await get_deterministic_repair_engine().execute(order, text, {})

    assert order.operation == "cleanup_ai_style"
    assert result["changed"] is True
    assert "这意味着" not in result["text"]
    assert result["patches"][0]["repairer"] == "StyleCleanupRepairer"


@pytest.mark.asyncio
async def test_preflight_failure_retries_original_specialist_before_restructure(monkeypatch):
    finding = QualityFindingHub().normalize_violation({
        "violation_id": "fact_case_002",
        "source": "consistency",
        "type": "identity_conflict",
        "severity": "high",
        "detail": "身份称谓冲突",
        "target_span": "不存在的身份片段",
        "expected_behavior": "改为「外门弟子」",
    })
    orchestrator = FBIV2Orchestrator()
    llm_lanes: list[str] = []
    llm_operations: list[str] = []

    async def fake_recheck(*, text, context, round_num):
        return {
            "round": round_num,
            "passed": False,
            "violations": [],
            "findings": [finding],
            "report": {"passed": False, "violations": []},
        }

    async def fake_execute_llm(state, order, context):
        llm_lanes.append(order.lane)
        llm_operations.append(order.operation)
        order.mark_failed("forced no patch")
        return {"changed": False, "patches": []}

    monkeypatch.setattr(orchestrator, "_run_recheck", fake_recheck)
    monkeypatch.setattr(orchestrator, "_execute_llm", fake_execute_llm)

    outcome = await orchestrator.run(
        project_id="proj_test",
        candidate_text="众人都知道，他仍在外门。",
        findings=[finding],
        context={},
        max_rounds=3,
    )

    assert outcome["status"] == "needs_workbench"
    assert llm_lanes == ["fbi_fact", "fbi_scene_restructure"]
    assert llm_operations == ["replace_span", "rewrite_scene"]
    assert outcome["escalations"][0]["from_lane"] == "deterministic_preflight"
