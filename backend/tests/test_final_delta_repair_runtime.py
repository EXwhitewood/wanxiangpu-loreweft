import pytest

from app.services.fbi.final_delta_repair_runtime import (
    FinalDeltaRepairRuntime,
    FinalDeltaRepairRuntimeCallbacks,
    FinalDeltaRepairRuntimeConfig,
)
from app.services.editor_generation_dag import EditorGenerationDAGBuilder
from app.services.editor_generation_dag_runtime import EditorGenerationDAGRuntime


@pytest.mark.asyncio
async def test_final_delta_runtime_skips_visible_nodes_when_final_acceptance_allowed():
    updates = []
    final_gate = {"allowed": True}

    async def update_step(step, status, output=None, error=None):
        updates.append((step, status, output or {}, error))

    runtime = FinalDeltaRepairRuntime(
        FinalDeltaRepairRuntimeConfig(
            project_id="project-1",
            chapter_number=1,
            chapter_state={},
            repair_round=0,
            max_rounds=1,
        ),
        _callbacks(update_step, lambda: final_gate, lambda value: final_gate.update(value)),
    )

    output = await runtime.run()

    assert output["reason"] == "final_acceptance_allowed"
    assert [item[0] for item in updates] == [
        "final_acceptance_delta_intake",
        "final_acceptance_delta_blueprint",
        "final_acceptance_delta_execute",
        "final_acceptance_delta_recheck",
    ]
    assert {item[1] for item in updates} == {"skipped"}


@pytest.mark.asyncio
async def test_final_delta_runtime_intake_then_skips_when_no_auto_repairable_delta():
    updates = []
    final_gate = {
        "allowed": False,
        "case_file_delta": {"issues": [{"type": "manual", "blocks_commit": True}]},
    }

    async def update_step(step, status, output=None, error=None):
        updates.append((step, status, output or {}, error))

    callbacks = _callbacks(update_step, lambda: final_gate, lambda value: final_gate.update(value))
    callbacks.auto_repair_candidates = lambda case_delta: []
    callbacks.review_case_delta_issues = lambda case_delta: list(case_delta.get("issues") or [])

    runtime = FinalDeltaRepairRuntime(
        FinalDeltaRepairRuntimeConfig(
            project_id="project-1",
            chapter_number=1,
            chapter_state={},
            repair_round=0,
            max_rounds=1,
        ),
        callbacks,
    )

    output = await runtime.run()

    assert output["reason"] == "no_auto_repairable_final_delta"
    assert updates[0][0:2] == ("final_acceptance_delta_intake", "running")
    assert updates[1][0:2] == ("final_acceptance_delta_intake", "completed")
    assert updates[2][0:2] == ("final_acceptance_delta_blueprint", "skipped")
    assert updates[3][0:2] == ("final_acceptance_delta_execute", "skipped")
    assert updates[4][0:2] == ("final_acceptance_delta_recheck", "skipped")


@pytest.mark.asyncio
async def test_final_delta_runtime_exposes_dag_node_handlers():
    updates = []
    final_gate = {"allowed": True}

    async def update_step(step, status, output=None, error=None):
        updates.append((step, status, output or {}, error))

    runtime = FinalDeltaRepairRuntime(
        FinalDeltaRepairRuntimeConfig(
            project_id="project-1",
            chapter_number=1,
            chapter_state={},
            repair_round=0,
            max_rounds=1,
        ),
        _callbacks(update_step, lambda: final_gate, lambda value: final_gate.update(value)),
    )
    dag = EditorGenerationDAGBuilder().build(scene_count=1, mode="parallel_review")

    report = await EditorGenerationDAGRuntime(dag).run(
        runtime.node_handlers(),
        node_types={
            "final_acceptance_delta_intake",
            "final_acceptance_delta_blueprint",
            "final_acceptance_delta_execute",
            "final_acceptance_delta_recheck",
        },
    )

    assert runtime.result["reason"] == "final_acceptance_allowed"
    assert {
        "final_acceptance_delta_intake",
        "final_acceptance_delta_blueprint",
        "final_acceptance_delta_execute",
        "final_acceptance_delta_recheck",
    } <= set(report.executed_nodes)
    assert len(updates) == 4


def _callbacks(update_step, get_final_gate, set_final_gate):
    return FinalDeltaRepairRuntimeCallbacks(
        update_step=update_step,
        get_final_gate=get_final_gate,
        set_final_gate=set_final_gate,
        get_scene_texts=lambda: {0: "text"},
        build_executor_context=lambda scene_texts: {},
        build_executor_case_file=lambda scene_texts, case_delta, plan: {},
        apply_updated_texts=lambda old, new: [],
        recheck_scene=lambda scene_idx: _none_async(),
        evaluate_final_acceptance=lambda round_index: _final_gate_async(get_final_gate()),
        update_repair_plan=lambda plan: None,
        review_case_delta_issues=lambda case_delta: [],
        auto_repair_candidates=lambda case_delta: [],
        violation_sample=lambda items: items,
        repair_type_counts=lambda orders: {},
        repair_order_sample=lambda orders: [],
        is_blocking_order_failure=lambda order: False,
        final_failure_summary=lambda gate: {},
        cycle_progress=lambda before, after: {"has_progress": False},
    )


async def _none_async():
    return None


async def _final_gate_async(value):
    return value
