"""方案5/方案6 边界与重试测试。

覆盖任务：
- 任务3（方案5 C1/C2/C3）：三层防护（事前约束 / 事中标记 / 事后验证）
- 任务4（方案6 A2）：degraded 蓝图时触发 LLM 重试（最多 MAX_LLM_RETRIES=2 次）
- 任务5（方案6 B3）：handle_parallel_scene_review 异常分类（可重试 vs 不可重试）
- 任务6（方案6 C1完整化）：blocking_failed_orders 时继续循环而非直接退出
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.models.chapter_review import (
    ChapterRepairOrder,
    ChapterRepairPlan,
    RepairWorkUnit,
    RevisionBlueprint,
    ToolCommand,
    ToolCommandBatch,
)
from app.services.fbi.chapter_repair_executor import ChapterRepairExecutor
from app.services.fbi.final_delta_repair_runtime import (
    FinalDeltaRepairRuntime,
    FinalDeltaRepairRuntimeCallbacks,
    FinalDeltaRepairRuntimeConfig,
)


# ---------------------------------------------------------------------------
# 测试辅助构造
# ---------------------------------------------------------------------------


def _make_degraded_plan(*, with_work_units: bool = True) -> ChapterRepairPlan:
    """构造一个 degraded 蓝图的修复计划。"""
    work_units: list[RepairWorkUnit] = []
    if with_work_units:
        work_units.append(
            RepairWorkUnit(
                work_unit_id="wu-1",
                target_scenes=[0],
                owner_scene=0,
                source_order_ids=["order-1"],
                source_violation_ids=["iss-1"],
                tool_batch=ToolCommandBatch(
                    batch_id="batch-1",
                    commands=[
                        ToolCommand(
                            operation="replace_exact",
                            old_text="旧文本",
                            new_text="新文本",
                        )
                    ],
                ),
            )
        )
    return ChapterRepairPlan(
        case_id="case-degraded-1",
        status="needs_repair",
        orders=[
            ChapterRepairOrder(
                order_id="order-1",
                repair_type="local_patch",
                target_scenes=[0],
                status="pending",
            )
        ],
        revision_blueprint=RevisionBlueprint(
            blueprint_id="bp-1",
            case_id="case-degraded-1",
            work_units=work_units,
            status="degraded",
        ),
        work_units=work_units,
    )


def _make_ready_work_units() -> list[RepairWorkUnit]:
    """构造一组 ready 状态的 work_units（重出图成功返回值）。"""
    return [
        RepairWorkUnit(
            work_unit_id="wu-retry-1",
            target_scenes=[0],
            owner_scene=0,
            source_order_ids=["order-1"],
            source_violation_ids=["iss-1"],
            tool_batch=ToolCommandBatch(
                batch_id="batch-retry-1",
                commands=[
                    ToolCommand(
                        operation="replace_exact",
                        old_text="旧文本",
                        new_text="重试后的新文本",
                    )
                ],
            ),
        )
    ]


# ---------------------------------------------------------------------------
# 任务3：方案5 C1/C2/C3 三层防护验证
# ---------------------------------------------------------------------------


def test_c1_review_blueprint_prompt_contains_fact_preservation_constraint():
    """方案5 C1 事前约束：review_blueprint_agent 的 system prompt 包含'不得改变既定事实'。"""
    from app.agents.fbi.review_blueprint_agent import FBIReviewBlueprintAgent

    agent = FBIReviewBlueprintAgent()
    # _build_session_system_prompt 签名：(self, context, annotated) -> str
    prompt = agent._build_session_system_prompt(
        {
            "case_id": "test-case",
            "scene_texts": {0: "测试文本"},
            "violations": [],
        },
        annotated=[],
    )
    # 拼接所有片段（_build_session_system_prompt 可能返回 str 或 list）
    prompt_text = prompt if isinstance(prompt, str) else "".join(str(p) for p in prompt)
    assert "既定事实" in prompt_text, "C1 事前约束缺失：system prompt 应包含'不得改变既定事实'"
    assert "established_facts" in prompt_text or "iron_rules" in prompt_text


def test_c2_submit_work_units_schema_contains_fact_preservation_field():
    """方案5 C2 事中标记：SUBMIT_WORK_UNITS_SCHEMA 包含 fact_preservation 字段。"""
    from app.agents.fbi.blueprint_tool_schemas import SUBMIT_WORK_UNITS_SCHEMA

    schema_text = repr(SUBMIT_WORK_UNITS_SCHEMA)
    assert "fact_preservation" in schema_text, "C2 事中标记缺失：schema 应包含 fact_preservation 字段"


# ---------------------------------------------------------------------------
# 任务4：方案6 A2 degraded 蓝图 LLM 重试
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_degraded_blueprint_triggers_llm_retry_and_succeeds(monkeypatch):
    """degraded 蓝图触发 LLM 重试，第1次重试成功 → 用新 work_units 继续执行。"""
    plan = _make_degraded_plan()
    scene_texts = {0: "旧文本还在这里。"}

    # 记录重试调用
    retry_calls: list[dict[str, Any]] = []

    async def _fake_retry(self, failed_issue_ids, failure_reason, retry_round, context):
        retry_calls.append({
            "failed_issue_ids": list(failed_issue_ids),
            "failure_reason": failure_reason,
            "retry_round": retry_round,
        })
        return {
            "status": "ready",
            "work_units": _make_ready_work_units(),
            "trace": {"retry_round": retry_round},
        }

    # mock RetryBlueprintRuntime.retry
    from app.services.fbi import retry_blueprint_runtime as _rbm
    monkeypatch.setattr(_rbm.RetryBlueprintRuntime, "retry", _fake_retry)

    executor = ChapterRepairExecutor()
    updated_texts, updated_plan = await executor.execute(
        plan, scene_texts, context={}, case_file={},
    )

    # 验证：重试被调用了至少 1 次
    assert len(retry_calls) >= 1, "degraded 蓝图应触发 LLM 重试"
    assert retry_calls[0]["failure_reason"] == "blueprint_degraded"
    # 验证：plan 的 revision_blueprint 已替换为 ready 状态（非 degraded）
    # 注意：executor 下游 protocol gate 可能过滤 work_units，所以只验证 status
    assert updated_plan.revision_blueprint.status == "ready"
    # 验证：orders 没有被标记为 blueprint_degraded_after_retries（说明重试成功后继续执行了）
    for order in updated_plan.orders:
        if order.order_id == "order-1":
            assert order.repair_audit.get("reason") != "blueprint_degraded_after_retries", (
                "重试成功后不应标记为 blueprint_degraded_after_retries"
            )


@pytest.mark.asyncio
async def test_degraded_blueprint_retry_exhausted_marks_skipped(monkeypatch):
    """degraded 蓝图重试 2 次仍失败 → orders 标记为 skipped，reason=blueprint_degraded_after_retries。"""
    plan = _make_degraded_plan()
    scene_texts = {0: "旧文本还在这里。"}

    retry_count = {"value": 0}

    async def _fake_retry_always_fail(self, failed_issue_ids, failure_reason, retry_round, context):
        retry_count["value"] += 1
        return {
            "status": "failed",
            "reason": "max_retry_exceeded",
            "trace": {"retry_round": retry_round},
        }

    from app.services.fbi import retry_blueprint_runtime as _rbm
    monkeypatch.setattr(_rbm.RetryBlueprintRuntime, "retry", _fake_retry_always_fail)

    executor = ChapterRepairExecutor()
    updated_texts, updated_plan = await executor.execute(
        plan, scene_texts, context={}, case_file={},
    )

    # 验证：重试了 MAX_LLM_RETRIES=2 次
    assert retry_count["value"] == 2, f"应重试 2 次，实际 {retry_count['value']}"
    # 验证：orders 标记为 skipped
    for order in updated_plan.orders:
        if order.order_id == "order-1":
            assert order.status == "skipped"
            assert order.repair_audit["reason"] == "blueprint_degraded_after_retries"
            assert order.repair_audit["retry_attempts"] == 2


@pytest.mark.asyncio
async def test_degraded_blueprint_no_issue_ids_skips_retry(monkeypatch):
    """degraded 蓝图但无 source_violation_ids → 无法提取 failed_issue_ids，跳过重试。"""
    # 构造无 source_violation_ids 的 work_units
    plan = _make_degraded_plan(with_work_units=False)
    # orders 也没有 violation_details
    scene_texts = {0: "旧文本。"}

    retry_called = {"value": False}

    async def _fake_retry(self, *args, **kwargs):
        retry_called["value"] = True
        return {"status": "failed", "reason": "should_not_be_called"}

    from app.services.fbi import retry_blueprint_runtime as _rbm
    monkeypatch.setattr(_rbm.RetryBlueprintRuntime, "retry", _fake_retry)

    executor = ChapterRepairExecutor()
    updated_texts, updated_plan = await executor.execute(
        plan, scene_texts, context={}, case_file={},
    )

    # 验证：重试未被调用（因为无法提取 failed_issue_ids）
    assert not retry_called["value"], "无 failed_issue_ids 时不应调用重试"
    # 验证：orders 标记为 skipped（重试耗尽路径）
    for order in updated_plan.orders:
        if order.order_id == "order-1":
            assert order.status == "skipped"
            assert order.repair_audit["reason"] == "blueprint_degraded_after_retries"


@pytest.mark.asyncio
async def test_degraded_blueprint_retry_exception_handled_gracefully(monkeypatch):
    """degraded 蓝图重试时 RetryBlueprintRuntime 抛异常 → 不崩溃，走 skipping 路径。"""
    plan = _make_degraded_plan()
    scene_texts = {0: "旧文本。"}

    async def _fake_retry_raise(self, *args, **kwargs):
        raise RuntimeError("LLM connection refused")

    from app.services.fbi import retry_blueprint_runtime as _rbm
    monkeypatch.setattr(_rbm.RetryBlueprintRuntime, "retry", _fake_retry_raise)

    executor = ChapterRepairExecutor()
    updated_texts, updated_plan = await executor.execute(
        plan, scene_texts, context={}, case_file={},
    )

    # 验证：异常被捕获，orders 标记为 skipped
    for order in updated_plan.orders:
        if order.order_id == "order-1":
            assert order.status == "skipped"
            assert order.repair_audit["reason"] == "blueprint_degraded_after_retries"


# ---------------------------------------------------------------------------
# 任务5：方案6 B3 异常分类
# ---------------------------------------------------------------------------


def test_is_transient_review_error_timeout():
    """asyncio.TimeoutError → 可重试。"""
    from app.api.editor_chat import _is_transient_review_error

    assert _is_transient_review_error(asyncio.TimeoutError()) is True
    assert _is_transient_review_error(TimeoutError("timed out")) is True


def test_is_transient_review_error_db_operational():
    """DB OperationalError（按类名匹配）→ 可重试。"""
    from app.api.editor_chat import _is_transient_review_error

    # 构造一个类名为 OperationalError 的异常
    class OperationalError(Exception):
        pass

    assert _is_transient_review_error(OperationalError("connection lost")) is True


def test_is_transient_review_error_rate_limit():
    """LLM 限流异常（按消息关键词）→ 可重试。"""
    from app.api.editor_chat import _is_transient_review_error

    class RateLimitError(Exception):
        pass

    assert _is_transient_review_error(RateLimitError("rate limit exceeded")) is True


def test_is_transient_review_error_non_retryable():
    """编程错误 / contract 错误 → 不可重试。"""
    from app.api.editor_chat import _is_transient_review_error

    assert _is_transient_review_error(ValueError("invalid argument")) is False
    assert _is_transient_review_error(TypeError("wrong type")) is False
    assert _is_transient_review_error(KeyError("missing_key")) is False


def test_is_transient_review_error_connection_reset_by_message():
    """按消息关键词匹配 connection reset → 可重试。"""
    from app.api.editor_chat import _is_transient_review_error

    class SomeNetworkError(Exception):
        pass

    assert _is_transient_review_error(SomeNetworkError("Connection reset by peer")) is True


# ---------------------------------------------------------------------------
# 任务6：方案6 C1完整化 blocking_failed_orders 循环重试
# ---------------------------------------------------------------------------


def _make_blocking_order() -> ChapterRepairOrder:
    """构造一个 blocking 失败的 order。"""
    return ChapterRepairOrder(
        order_id="order-blocking-1",
        repair_type="local_patch",
        target_scenes=[0],
        status="failed",
        repair_audit={"reason": "old_text_not_found", "failures": ["old_text_not_found"]},
    )


def _callbacks_for_blocking_test(
    update_step,
    get_final_gate,
    set_final_gate,
    *,
    blocking_orders: list[ChapterRepairOrder],
    has_progress: bool = False,
    changed_scenes: list[int] | None = None,
):
    """构造测试用 callbacks，blocking_orders 非空时 is_blocking_order_failure 返回 True。"""
    return FinalDeltaRepairRuntimeCallbacks(
        update_step=update_step,
        get_final_gate=get_final_gate,
        set_final_gate=set_final_gate,
        get_scene_texts=lambda: {0: "文本"},
        build_executor_context=lambda scene_texts: {},
        build_executor_case_file=lambda scene_texts, case_delta, plan: {},
        apply_updated_texts=lambda old, new: changed_scenes if changed_scenes is not None else [0] if new.get(0) else [],
        recheck_scene=lambda scene_idx: _none_async(),
        evaluate_final_acceptance=lambda round_index: _final_gate_async(get_final_gate()),
        update_repair_plan=lambda plan: None,
        review_case_delta_issues=lambda case_delta: list(case_delta.get("issues") or []),
        auto_repair_candidates=lambda case_delta: [{"type": "test", "scene_index": 0}],
        violation_sample=lambda items: items,
        repair_type_counts=lambda orders: {},
        repair_order_sample=lambda orders: [],
        is_blocking_order_failure=lambda order: order in blocking_orders,
        final_failure_summary=lambda gate: {},
        cycle_progress=lambda before, after: {"has_progress": has_progress},
    )


async def _none_async():
    return None


async def _final_gate_async(value):
    return value


@pytest.mark.asyncio
async def test_blocking_failed_orders_continues_cycle_when_rounds_remaining():
    """方案6 C1完整化：blocking_failed_orders 非空但还有剩余轮次 → return True 继续循环。"""
    updates = []
    final_gate = {
        "allowed": False,
        "case_file_delta": {"issues": [{"type": "test", "blocks_commit": True, "issue_id": "iss-1"}]},
    }
    blocking_orders = [_make_blocking_order()]

    async def update_step(step, status, output=None, error=None):
        updates.append((step, status, output or {}, error))

    # mock ChapterRepairExecutor.execute 返回 blocking_failed_orders
    async def _fake_execute(self, plan, scene_texts, *, context=None, case_file=None):
        updated_plan = plan.model_copy(deep=True)
        # 把 plan 的 orders 替换为 blocking orders
        updated_plan.orders = list(blocking_orders)
        return dict(scene_texts), updated_plan

    # mock FBIChapterRepairPlanner.plan 返回非 clean 的 plan（否则 is_clean() 提前退出）
    async def _fake_plan(self, case):
        return ChapterRepairPlan(
            case_id="test-blocking-case",
            status="needs_repair",
            orders=[
                ChapterRepairOrder(
                    order_id="order-blocking-1",
                    repair_type="local_patch",
                    target_scenes=[0],
                    status="pending",
                )
            ],
        )

    from app.services.fbi import chapter_repair_executor as _cre
    from app.services.fbi import chapter_case_intake as _cci
    original_execute = _cre.ChapterRepairExecutor.execute
    original_plan = _cci.FBIChapterRepairPlanner.plan
    _cre.ChapterRepairExecutor.execute = _fake_execute
    _cci.FBIChapterRepairPlanner.plan = _fake_plan
    try:
        # mock _try_retry_blueprint 返回 failed（重出图也失败）
        from app.services.fbi.final_delta_repair_runtime import FinalDeltaRepairRuntime as _FDR
        original_try_retry = _FDR._try_retry_blueprint

        async def _fake_try_retry(self, *args, **kwargs):
            return {"status": "failed", "reason": "max_retry_exceeded", "trace": {}}

        _FDR._try_retry_blueprint = _fake_try_retry
        try:
            callbacks = _callbacks_for_blocking_test(
                update_step,
                lambda: final_gate,
                lambda value: final_gate.update(value),
                blocking_orders=blocking_orders,
                has_progress=False,
                changed_scenes=[0],
            )
            runtime = FinalDeltaRepairRuntime(
                FinalDeltaRepairRuntimeConfig(
                    project_id="project-1",
                    chapter_number=1,
                    chapter_state={},
                    repair_round=0,
                    max_rounds=3,  # 还有剩余轮次
                ),
                callbacks,
            )
            output = await runtime.run()

            # 验证：blocking_failed_orders 时继续循环而非直接退出。
            # max_rounds=3 时应跑满 3 轮（如果直接退出只会跑 1 轮），
            # 最后一轮 final_delta_round == max_rounds 时才退出，reason=blocking_repair_order_failed。
            assert len(output.get("cycles", [])) == 3, (
                f"应跑满 3 轮（继续循环），实际 cycles={len(output.get('cycles', []))}"
            )
            assert output.get("reason") == "blocking_repair_order_failed", (
                f"max_rounds 耗尽后应退出，实际 reason={output.get('reason')}"
            )
        finally:
            _FDR._try_retry_blueprint = original_try_retry
    finally:
        _cre.ChapterRepairExecutor.execute = original_execute
        _cci.FBIChapterRepairPlanner.plan = original_plan


@pytest.mark.asyncio
async def test_blocking_failed_orders_exits_when_max_rounds_reached():
    """方案6 C1完整化：blocking_failed_orders 非空且 max_rounds 耗尽 → return False 退出。"""
    updates = []
    final_gate = {
        "allowed": False,
        "case_file_delta": {"issues": [{"type": "test", "blocks_commit": True, "issue_id": "iss-1"}]},
    }
    blocking_orders = [_make_blocking_order()]

    async def update_step(step, status, output=None, error=None):
        updates.append((step, status, output or {}, error))

    async def _fake_execute(self, plan, scene_texts, *, context=None, case_file=None):
        updated_plan = plan.model_copy(deep=True)
        updated_plan.orders = list(blocking_orders)
        return dict(scene_texts), updated_plan

    async def _fake_plan(self, case):
        return ChapterRepairPlan(
            case_id="test-blocking-case",
            status="needs_repair",
            orders=[
                ChapterRepairOrder(
                    order_id="order-blocking-1",
                    repair_type="local_patch",
                    target_scenes=[0],
                    status="pending",
                )
            ],
        )

    from app.services.fbi import chapter_repair_executor as _cre
    from app.services.fbi import chapter_case_intake as _cci
    original_execute = _cre.ChapterRepairExecutor.execute
    original_plan = _cci.FBIChapterRepairPlanner.plan
    _cre.ChapterRepairExecutor.execute = _fake_execute
    _cci.FBIChapterRepairPlanner.plan = _fake_plan
    try:
        from app.services.fbi.final_delta_repair_runtime import FinalDeltaRepairRuntime as _FDR
        original_try_retry = _FDR._try_retry_blueprint

        async def _fake_try_retry(self, *args, **kwargs):
            return {"status": "failed", "reason": "max_retry_exceeded", "trace": {}}

        _FDR._try_retry_blueprint = _fake_try_retry
        try:
            callbacks = _callbacks_for_blocking_test(
                update_step,
                lambda: final_gate,
                lambda value: final_gate.update(value),
                blocking_orders=blocking_orders,
                has_progress=False,
                changed_scenes=[0],
            )
            runtime = FinalDeltaRepairRuntime(
                FinalDeltaRepairRuntimeConfig(
                    project_id="project-1",
                    chapter_number=1,
                    chapter_state={},
                    repair_round=0,
                    max_rounds=1,  # 无剩余轮次
                ),
                callbacks,
            )
            output = await runtime.run()

            # 验证：reason 应为 blocking_repair_order_failed（退出）
            assert output.get("reason") == "blocking_repair_order_failed", (
                f"max_rounds 耗尽时应退出，实际 reason={output.get('reason')}"
            )
        finally:
            _FDR._try_retry_blueprint = original_try_retry
    finally:
        _cre.ChapterRepairExecutor.execute = original_execute
        _cci.FBIChapterRepairPlanner.plan = original_plan


# ---------------------------------------------------------------------------
# 任务4 辅助：验证 degraded retry 提取的 failed_issue_ids 正确
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_degraded_retry_extracts_failed_issue_ids_from_work_units(monkeypatch):
    """_retry_degraded_blueprint 从 plan.work_units.source_violation_ids 提取 failed_issue_ids。"""
    plan = _make_degraded_plan()
    scene_texts = {0: "旧文本。"}

    captured_issue_ids: list[list[str]] = []

    async def _fake_retry(self, failed_issue_ids, failure_reason, retry_round, context):
        captured_issue_ids.append(list(failed_issue_ids))
        return {
            "status": "ready",
            "work_units": _make_ready_work_units(),
            "trace": {"retry_round": retry_round},
        }

    from app.services.fbi import retry_blueprint_runtime as _rbm
    monkeypatch.setattr(_rbm.RetryBlueprintRuntime, "retry", _fake_retry)

    executor = ChapterRepairExecutor()
    await executor.execute(plan, scene_texts, context={}, case_file={})

    # 验证：提取的 failed_issue_ids 包含 iss-1
    assert len(captured_issue_ids) >= 1
    assert "iss-1" in captured_issue_ids[0], (
        f"应从 work_units.source_violation_ids 提取 iss-1，实际 {captured_issue_ids[0]}"
    )


@pytest.mark.asyncio
async def test_degraded_retry_uses_case_file_violations_when_available(monkeypatch):
    """_retry_degraded_blueprint 优先从 case_file.review_case_file 提取 violations。"""
    plan = _make_degraded_plan()
    scene_texts = {0: "旧文本。"}

    captured_context: list[dict] = []

    async def _fake_retry(self, failed_issue_ids, failure_reason, retry_round, context):
        captured_context.append(context)
        return {
            "status": "ready",
            "work_units": _make_ready_work_units(),
            "trace": {},
        }

    from app.services.fbi import retry_blueprint_runtime as _rbm
    monkeypatch.setattr(_rbm.RetryBlueprintRuntime, "retry", _fake_retry)

    executor = ChapterRepairExecutor()
    case_file = {
        "review_case_file": {
            "issues": [
                {"issue_id": "iss-1", "type": "anti_ai", "evidence": "test"},
            ],
        },
    }
    await executor.execute(plan, scene_texts, context={}, case_file=case_file)

    # 验证：retry_context 的 violations 包含 case_file 中的 issue
    assert len(captured_context) >= 1
    violations = captured_context[0].get("violations") or []
    assert any(v.get("issue_id") == "iss-1" for v in violations), (
        f"应从 case_file 提取 violations，实际 {violations}"
    )
