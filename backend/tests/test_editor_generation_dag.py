import asyncio
import time
import uuid

import pytest
from sqlalchemy import select

from app.services.editor_generation_dag import EditorGenerationDAGBuilder
from app.services.editor_generation_dag import EditorDagNode, EditorGenerationDAG
from app.services.editor_generation_dag_runtime import (
    EditorDAGAbort,
    EditorDAGSafetyError,
    EditorGenerationDAGRuntime,
)
from app.api.editor_chat import (
    _complete_editor_workflow_step_names,
    _update_workflow_step,
    _workflow_step_layers_from_dag,
)
from app.db.db_models import Project, WorkflowExecution, WorkflowStep


def test_editor_generation_dag_has_ordered_state_commits():
    dag = EditorGenerationDAGBuilder().build(scene_count=3)
    data = dag.to_dict()
    nodes = {node["node_id"]: node for node in data["nodes"]}

    assert nodes["scene_state_commit_2"]["dependencies"] == [
        "scene_accept_2",
        "scene_state_commit_1",
    ]
    assert nodes["scene_quality_fanout_1"]["parallel_group"] == "scene_1_fanout"
    assert nodes["scene_post_extract_fanout_1"]["parallel_group"] == "scene_1_fanout"
    assert "write_chapter" in dag.step_names()


def test_editor_generation_dag_rejects_empty_scene_count():
    try:
        EditorGenerationDAGBuilder().build(scene_count=0)
    except ValueError as exc:
        assert "scene_count" in str(exc)
    else:
        raise AssertionError("Expected ValueError")


def test_workflow_steps_use_dag_layers_for_fanout():
    dag = EditorGenerationDAGBuilder().build(scene_count=2)
    step_layers = dict(_workflow_step_layers_from_dag(dag.step_names(), dag.to_dict()))

    assert step_layers["consistency_check_1"] == step_layers["detail_harvest_1"]
    assert step_layers["consistency_check_2"] == step_layers["detail_harvest_2"]
    assert step_layers["state_update_2"] > step_layers["state_update_1"]


def test_parallel_review_workflow_includes_serial_head_and_tail_steps():
    dag = EditorGenerationDAGBuilder().build(scene_count=2, mode="parallel_review")
    step_names = _complete_editor_workflow_step_names(dag.step_names())
    step_layers = dict(_workflow_step_layers_from_dag(step_names, dag.to_dict()))

    assert step_layers["editor_planning"] == -1
    assert step_layers["context_compile"] == -1
    assert step_layers["chapter_writer"] == -1
    assert step_layers["scene_alignment"] == -1
    assert step_layers["fact_extraction"] == len(dag.layers())
    assert step_layers["style_polish"] == len(dag.layers())
    assert step_layers["final_acceptance"] == len(dag.layers())
    assert step_layers["write_chapter"] == len(dag.layers())


@pytest.mark.asyncio
async def test_workflow_step_update_persists_unplanned_lifecycle_step(db):
    project_id = uuid.uuid4()
    execution_id = uuid.uuid4()
    db.add(Project(id=project_id, name="workflow-observability-test"))
    db.add(WorkflowExecution(
        id=execution_id,
        project_id=project_id,
        status="running",
        trigger_type="editor_generate_ch1",
        current_layer=0,
        total_layers=11,
    ))
    await db.commit()

    await _update_workflow_step(
        str(execution_id),
        "chapter_writer",
        "running",
        db=db,
    )
    await _update_workflow_step(
        str(execution_id),
        "chapter_writer",
        "completed",
        output={"word_count": 1200},
        db=db,
    )

    step = (
        await db.execute(
            select(WorkflowStep).where(
                WorkflowStep.execution_id == execution_id,
                WorkflowStep.agent_name == "chapter_writer",
            )
        )
    ).scalar_one()
    assert step.layer == -1
    assert step.status == "completed"
    assert step.output_snapshot == {"word_count": 1200}


def test_editor_generation_dag_runtime_runs_same_layer_nodes_concurrently():
    async def run_case():
        dag = EditorGenerationDAGBuilder().build(scene_count=1)
        runtime = EditorGenerationDAGRuntime(dag)
        starts = {}

        async def handler(node, context):
            starts[node.node_id] = time.perf_counter()
            if node.node_type in {"scene_quality_fanout", "scene_post_extract_fanout"}:
                await asyncio.sleep(0.05)

        report = await runtime.run(
            {
                "scene_prepare": handler,
                "scene_generate": handler,
                "scene_quality_fanout": handler,
                "scene_post_extract_fanout": handler,
                "scene_accept": handler,
                "scene_state_commit": handler,
            },
            node_types={
                "scene_prepare",
                "scene_generate",
                "scene_quality_fanout",
                "scene_post_extract_fanout",
                "scene_accept",
                "scene_state_commit",
            },
        )
        delta = abs(starts["scene_quality_fanout_1"] - starts["scene_post_extract_fanout_1"])
        assert delta < 0.03
        assert report.max_parallel_width == 2

    asyncio.run(run_case())


def test_editor_generation_dag_runtime_abort_stops_later_layers():
    async def run_case():
        dag = EditorGenerationDAGBuilder().build(scene_count=1)
        runtime = EditorGenerationDAGRuntime(dag)
        seen = []

        async def handler(node, context):
            seen.append(node.node_id)
            if node.node_type == "scene_quality_fanout":
                raise EditorDAGAbort({"status": "waiting_review"})

        try:
            await runtime.run(
                {
                    "scene_prepare": handler,
                    "scene_generate": handler,
                    "scene_quality_fanout": handler,
                    "scene_post_extract_fanout": handler,
                    "scene_accept": handler,
                    "scene_state_commit": handler,
                },
                node_types={
                    "scene_prepare",
                    "scene_generate",
                    "scene_quality_fanout",
                    "scene_post_extract_fanout",
                    "scene_accept",
                    "scene_state_commit",
                },
            )
        except EditorDAGAbort as exc:
            assert exc.result == {"status": "waiting_review"}
        else:
            raise AssertionError("Expected EditorDAGAbort")

        assert "scene_accept_1" not in seen
        assert "scene_state_commit_1" not in seen

    asyncio.run(run_case())


def test_editor_generation_dag_runtime_persists_aborting_node_terminal_status():
    async def run_case():
        dag = EditorGenerationDAG(
            dag_version="test",
            mode="test",
            scene_count=1,
            nodes=[EditorDagNode("repair_1", "Repair", "parallel_scene_repair")],
        )
        runtime = EditorGenerationDAGRuntime(dag, execution_id="exec-test")
        persisted = {}

        async def noop(*args, **kwargs):
            return None

        async def capture_abort(node, layer_idx, started_at, status, result, phase_trace):
            persisted.update({
                "node_id": node.node_id,
                "status": status,
                "result": result,
                "phase": phase_trace[-1]["phase"],
            })

        runtime._persist_node_start = noop
        runtime._persist_node_abort = capture_abort
        runtime._update_execution_layer = noop
        runtime._update_execution_status = noop

        async def handler(node, context):
            raise EditorDAGAbort({
                "status": "pending_validator_retry",
                "error": "validator unavailable",
            })

        try:
            await runtime.run({"parallel_scene_repair": handler})
        except EditorDAGAbort:
            pass
        else:
            raise AssertionError("Expected EditorDAGAbort")

        assert persisted == {
            "node_id": "repair_1",
            "status": "pending_validator_retry",
            "result": {
                "status": "pending_validator_retry",
                "error": "validator unavailable",
            },
            "phase": "aborted",
        }

    asyncio.run(run_case())


def test_editor_generation_dag_runtime_rejects_parallel_write_scope_conflict():
    async def run_case():
        dag = EditorGenerationDAG(
            dag_version="test",
            mode="test",
            scene_count=1,
            nodes=[
                EditorDagNode(
                    "repair_a",
                    "Repair A",
                    "parallel_scene_repair",
                    write_scope=["scene:0"],
                    can_mutate_text=True,
                    parallel_group="repair",
                ),
                EditorDagNode(
                    "repair_b",
                    "Repair B",
                    "parallel_scene_repair",
                    write_scope=["scene:0"],
                    can_mutate_text=True,
                    parallel_group="repair",
                ),
            ],
        )
        runtime = EditorGenerationDAGRuntime(dag)

        async def handler(node, context):
            return None

        try:
            await runtime.run({"parallel_scene_repair": handler})
        except EditorDAGSafetyError as exc:
            assert "write_scope conflict" in str(exc)
        else:
            raise AssertionError("Expected EditorDAGSafetyError")

    asyncio.run(run_case())


def test_parallel_review_dag_declares_read_write_scopes():
    dag = EditorGenerationDAGBuilder().build(scene_count=2, mode="parallel_review")
    data = dag.to_dict()
    review = next(node for node in data["nodes"] if node["node_id"] == "scene_review_1")
    repair = next(node for node in data["nodes"] if node["node_id"] == "scene_repair_1")
    intake = next(node for node in data["nodes"] if node["node_id"] == "fbi_chapter_case_intake")
    delta_merge = next(node for node in data["nodes"] if node["node_id"] == "review_case_delta_merge")
    commit_1 = next(node for node in data["nodes"] if node["node_id"] == "state_commit_1")
    final_delta_intake = next(node for node in data["nodes"] if node["node_id"] == "final_acceptance_delta_intake")
    final_delta_blueprint = next(node for node in data["nodes"] if node["node_id"] == "final_acceptance_delta_blueprint")
    final_delta_execute = next(node for node in data["nodes"] if node["node_id"] == "final_acceptance_delta_execute")
    final_delta_recheck = next(node for node in data["nodes"] if node["node_id"] == "final_acceptance_delta_recheck")

    assert review["can_mutate_text"] is False
    assert review["write_scope"] == ["issue_output:0"]
    assert repair["can_mutate_text"] is True
    assert repair["write_scope"] == ["scene:0"]
    assert "chapter_review" in intake["dependencies"]
    assert delta_merge["dependencies"] == ["scene_recheck_1", "scene_recheck_2"]
    assert commit_1["dependencies"] == ["review_case_delta_merge"]
    # R4-9：final_acceptance_delta_intake 应依赖最后的 ordered_state_commit，而非空列表
    assert final_delta_intake["dependencies"] == ["state_commit_2"]
    assert final_delta_intake["can_mutate_text"] is False
    assert final_delta_blueprint["dependencies"] == ["final_acceptance_delta_intake"]
    assert final_delta_blueprint["can_mutate_text"] is False
    assert final_delta_execute["dependencies"] == ["final_acceptance_delta_blueprint"]
    assert final_delta_execute["can_mutate_text"] is True
    assert final_delta_recheck["dependencies"] == ["final_acceptance_delta_execute"]
    assert final_delta_recheck["can_mutate_text"] is False


def test_parallel_review_dag_runs_chapter_reviews_with_scene_reviews():
    dag = EditorGenerationDAGBuilder().build(scene_count=2, mode="parallel_review")
    layers = dag.layers()
    review_layer = next(layer for layer in layers if "scene_review_1" in layer)

    assert "scene_review_2" in review_layer
    assert "chapter_review" in review_layer


# 方案14 Part A：屏障测试——repair 和 recheck 不在同一层
def test_plan14_part_a_repair_and_recheck_in_separate_layers():
    dag = EditorGenerationDAGBuilder().build(scene_count=3, mode="parallel_review")
    layers = dag.layers()

    repair_layer_idx = next(
        i for i, layer in enumerate(layers) if "scene_repair_1" in layer
    )
    recheck_layer_idx = next(
        i for i, layer in enumerate(layers) if "scene_recheck_1" in layer
    )

    assert repair_layer_idx < recheck_layer_idx, (
        "repair 层必须在 recheck 层之前，确保屏障生效"
    )
    # 所有 repair 节点在同一层，所有 recheck 节点在另一层
    repair_layer = layers[repair_layer_idx]
    recheck_layer = layers[recheck_layer_idx]
    assert all(node_id.startswith("scene_repair_") for node_id in repair_layer if node_id.startswith("scene_"))
    assert all(node_id.startswith("scene_recheck_") for node_id in recheck_layer if node_id.startswith("scene_"))


# 方案14 Part B：并发限制测试——semaphore 限制同时运行节点数
def test_plan14_part_b_semaphore_limits_concurrency():
    async def run_case():
        dag = EditorGenerationDAGBuilder().build(scene_count=1)
        # max_concurrent=2 限制
        runtime = EditorGenerationDAGRuntime(dag, max_concurrent=2)

        current_running = 0
        max_running = 0

        async def handler(node, context):
            nonlocal current_running, max_running
            current_running += 1
            max_running = max(max_running, current_running)
            await asyncio.sleep(0.05)
            current_running -= 1

        await runtime.run(
            {
                "scene_prepare": handler,
                "scene_generate": handler,
                "scene_quality_fanout": handler,
                "scene_post_extract_fanout": handler,
                "scene_accept": handler,
                "scene_state_commit": handler,
            },
            node_types={
                "scene_prepare",
                "scene_generate",
                "scene_quality_fanout",
                "scene_post_extract_fanout",
                "scene_accept",
                "scene_state_commit",
            },
        )
        # scene_quality_fanout 和 scene_post_extract_fanout 同层并行，
        # semaphore=2 应允许两个同时运行，但不应超过 2
        assert max_running <= 2, f"max_running={max_running} 超过 semaphore 限制"

    asyncio.run(run_case())


# 方案14 Part D：read-write 竞态检查测试
def test_plan14_part_d_rejects_read_write_conflict():
    async def run_case():
        # 构造一个 reader 和 writer 在同层且 read_scope/write_scope 重叠的场景
        dag = EditorGenerationDAG(
            dag_version="test",
            mode="test",
            scene_count=1,
            nodes=[
                EditorDagNode(
                    "writer_a",
                    "Writer A",
                    "parallel_scene_repair",
                    write_scope=["scene:0"],
                    can_mutate_text=True,
                    parallel_group="group",
                ),
                EditorDagNode(
                    "reader_b",
                    "Reader B",
                    "parallel_scene_recheck",
                    read_scope=["scene:0"],
                    write_scope=["issue:0"],
                    can_mutate_text=False,
                    parallel_group="group",
                ),
            ],
        )
        runtime = EditorGenerationDAGRuntime(dag)

        async def handler(node, context):
            return None

        try:
            await runtime.run(
                {"parallel_scene_repair": handler, "parallel_scene_recheck": handler}
            )
        except EditorDAGSafetyError as exc:
            assert "read-write conflict" in str(exc)
        else:
            raise AssertionError("Expected EditorDAGSafetyError for read-write conflict")

    asyncio.run(run_case())


# 方案14 Part D：合法的 read-write 不冲突（不同 scope）
def test_plan14_part_d_allows_non_overlapping_read_write():
    async def run_case():
        dag = EditorGenerationDAG(
            dag_version="test",
            mode="test",
            scene_count=1,
            nodes=[
                EditorDagNode(
                    "writer_a",
                    "Writer A",
                    "parallel_scene_repair",
                    write_scope=["scene:0"],
                    can_mutate_text=True,
                    parallel_group="group",
                ),
                EditorDagNode(
                    "reader_b",
                    "Reader B",
                    "parallel_scene_recheck",
                    read_scope=["scene:1"],  # 读不同的场景
                    write_scope=["issue:1"],
                    can_mutate_text=False,
                    parallel_group="group",
                ),
            ],
        )
        runtime = EditorGenerationDAGRuntime(dag)

        async def handler(node, context):
            return None

        report = await runtime.run(
            {"parallel_scene_repair": handler, "parallel_scene_recheck": handler}
        )
        # 两个节点都应成功执行
        assert "writer_a" in report.executed_nodes
        assert "reader_b" in report.executed_nodes

    asyncio.run(run_case())


# 方案14 Part B：默认 max_concurrent=5
def test_plan14_part_b_default_max_concurrent_is_5():
    dag = EditorGenerationDAGBuilder().build(scene_count=1)
    runtime = EditorGenerationDAGRuntime(dag)
    # semaphore 的 _value 反映剩余许可数
    assert runtime.semaphore._value == 5

