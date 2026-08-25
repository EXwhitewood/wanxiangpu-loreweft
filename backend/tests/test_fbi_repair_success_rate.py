import asyncio
import json
from pathlib import Path

import pytest

from app.models.chapter_review import ChapterRepairOrder, ChapterRepairPlan
from app.services.fbi.chapter_repair_executor import ChapterRepairExecutor


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "fbi_repair_success" / "core_domains.json"


@pytest.mark.parametrize("fixture", json.loads(FIXTURE_PATH.read_text(encoding="utf-8")))
def test_core_repair_domain_fixture_succeeds(fixture):
    order = ChapterRepairOrder(
        order_id=f"order-{fixture['name']}",
        target_scenes=[0],
        owner_scene=0,
        repair_type="local_patch",
        repair_domain=fixture["domain"],
        repair_brief={
            "tool_commands": [{
                "operation": "replace_span",
                "scene_index": 0,
                "target_span": fixture["target_span"],
                "replacement": fixture["replacement"],
                "postconditions": {
                    "required_spans": [fixture["required_span"]],
                    "forbidden_spans": [fixture["forbidden_span"]],
                },
            }]
        },
        source_violation_ids=[f"issue-{fixture['name']}"],
        violation_details=[{
            "type": f"{fixture['domain']}_fixture",
            "target_span": fixture["target_span"],
        }],
    )
    plan = ChapterRepairPlan(
        case_id=f"case-{fixture['name']}",
        status="needs_repair",
        orders=[order],
    )

    updated, updated_plan = asyncio.run(
        ChapterRepairExecutor().execute(plan, {0: fixture["before"]})
    )

    assert fixture["required_span"] in updated[0]
    assert fixture["forbidden_span"] not in updated[0]
    assert updated_plan.orders[0].status == "succeeded"
    assert updated_plan.repair_execution_summary["status_counts"]["succeeded"] == 1


def test_anti_ai_dash_fixture_succeeds_and_is_reported():
    before = "She stopped——looked back——and shut the door——before the wind rose."
    order = ChapterRepairOrder(
        order_id="order-anti-ai-dash",
        target_scenes=[0],
        owner_scene=0,
        repair_type="local_patch",
        repair_domain="anti_ai",
        repair_lane="deterministic_surface_cleanup",
        expected_after_repair={"max_dash_count": 1},
        violation_details=[{
            "type": "ai_punctuation_artifact",
            "metric": "dash_per_1000",
            "target_span": "——",
        }],
    )
    plan = ChapterRepairPlan(case_id="case-anti-ai", status="needs_repair", orders=[order])

    updated, updated_plan = asyncio.run(ChapterRepairExecutor().execute(plan, {0: before}))

    assert updated[0].count("——") <= 1
    assert updated_plan.orders[0].status == "succeeded"
    tool_summary = updated_plan.repair_execution_summary["tool_execution"]
    assert tool_summary["accepted"] == 1
