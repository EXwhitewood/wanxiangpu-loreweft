"""Runtime service for the visible Final Acceptance delta repair chain.

Final Acceptance remains read-only. This service owns the four explicit delta
nodes after Final Acceptance:

- final_acceptance_delta_intake
- final_acceptance_delta_blueprint
- final_acceptance_delta_execute
- final_acceptance_delta_recheck

The API layer supplies project-specific callbacks for workflow persistence,
scene text storage, and final acceptance evaluation. The repair policy itself
stays in FBI intake/planner/executor services.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import logging
from typing import Any, Awaitable, Callable

from app.models.chapter_review import ChapterRepairPlan, ChapterReviewCase, RevisionBlueprint, SceneReviewPacket
from app.models.review_case_file import ReviewCaseFile, ReviewCaseIssue
from app.services.editor_generation_dag import EditorDagNode
from app.services.fbi.chapter_case_intake import FBIChapterCaseIntakeService, FBIChapterRepairPlanner
from app.services.fbi.chapter_repair_executor import ChapterRepairExecutor
from app.agents.fbi.review_blueprint_agent import FBIReviewBlueprintAgent


logger = logging.getLogger(__name__)


StepUpdater = Callable[[str, str, dict[str, Any] | None, str | None], Awaitable[None]]


@dataclass
class FinalDeltaRepairRuntimeConfig:
    project_id: str
    chapter_number: int
    chapter_state: dict[str, Any]
    repair_round: int
    max_rounds: int


@dataclass
class FinalDeltaRepairRuntimeCallbacks:
    update_step: StepUpdater
    get_final_gate: Callable[[], dict[str, Any]]
    set_final_gate: Callable[[dict[str, Any]], None]
    get_scene_texts: Callable[[], dict[int, str]]
    build_executor_context: Callable[[dict[int, str]], dict[str, Any]]
    build_executor_case_file: Callable[[dict[int, str], dict[str, Any], ChapterRepairPlan], dict[str, Any]]
    apply_updated_texts: Callable[[dict[int, str], dict[int, str]], list[int]]
    recheck_scene: Callable[[int], Awaitable[dict[str, Any] | None]]
    evaluate_final_acceptance: Callable[[int], Awaitable[dict[str, Any]]]
    update_repair_plan: Callable[[ChapterRepairPlan], None]
    review_case_delta_issues: Callable[[dict[str, Any]], list[dict[str, Any]]]
    auto_repair_candidates: Callable[[dict[str, Any]], list[dict[str, Any]]]
    violation_sample: Callable[[list[dict[str, Any]]], list[dict[str, Any]]]
    repair_type_counts: Callable[[list[Any]], dict[str, int]]
    repair_order_sample: Callable[[list[Any]], list[dict[str, Any]]]
    is_blocking_order_failure: Callable[[Any], bool]
    final_failure_summary: Callable[[dict[str, Any]], dict[str, Any]]
    cycle_progress: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]


class FinalDeltaRepairRuntime:
    """Execute the Final Acceptance delta repair chain as FBI-owned runtime."""

    intake_step = "final_acceptance_delta_intake"
    blueprint_step = "final_acceptance_delta_blueprint"
    execute_step = "final_acceptance_delta_execute"
    recheck_step = "final_acceptance_delta_recheck"

    def __init__(
        self,
        config: FinalDeltaRepairRuntimeConfig,
        callbacks: FinalDeltaRepairRuntimeCallbacks,
    ) -> None:
        self.config = config
        self.callbacks = callbacks
        self.result: dict[str, Any] | None = None

    def node_handlers(self) -> dict[str, Callable[[EditorDagNode], Awaitable[dict[str, Any]]]]:
        """Return DAG-runtime handlers for the four final-delta node types.

        The repair cycle may contain more than one internal round. Therefore
        the first visible delta node that the DAG runtime reaches starts the
        whole FBI-owned delta runtime, and later visible nodes become idempotent
        observers of the same result.
        """

        async def _handler(_node: EditorDagNode, _context) -> dict[str, Any]:
            if self.result is None:
                self.result = await self.run()
            return self.result

        return {
            self.intake_step: _handler,
            self.blueprint_step: _handler,
            self.execute_step: _handler,
            self.recheck_step: _handler,
        }

    async def run(self) -> dict[str, Any]:
        if self.result is not None:
            return self.result
        final_gate = self.callbacks.get_final_gate()
        initial_case_delta = final_gate.get("case_file_delta") or {}
        initial_candidates = self.callbacks.auto_repair_candidates(initial_case_delta)
        output: dict[str, Any] = {
            "attempted": False,
            "delta_only": True,
            "runtime": "final_delta_repair_runtime",
            "visible_nodes": [
                self.intake_step,
                self.blueprint_step,
                self.execute_step,
                self.recheck_step,
            ],
            "initial_issue_count": len(self.callbacks.review_case_delta_issues(initial_case_delta)),
            "auto_repairable_issue_count": len(initial_candidates),
            "cycles": [],
        }

        if final_gate.get("allowed", False):
            output["reason"] = "final_acceptance_allowed"
            await self._skip_remaining(0, output["reason"], output)
            return output
        if not initial_case_delta:
            output["reason"] = "no_case_file_delta"
            await self._skip_remaining(0, output["reason"], output)
            return output

        await self.callbacks.update_step(self.intake_step, "running", output, None)
        await self.callbacks.update_step(
            self.intake_step,
            "completed",
            {
                **output,
                "case_file_delta": initial_case_delta,
                "auto_repairable_samples": self.callbacks.violation_sample(initial_candidates[:5]),
            },
            None,
        )
        if not initial_candidates:
            output["reason"] = "no_auto_repairable_final_delta"
            await self._skip_remaining(1, output["reason"], output)
            return output

        output["attempted"] = True
        previous_summary = self.callbacks.final_failure_summary(final_gate)
        _cancelled_during_repair = False

        try:
            for final_delta_round in range(1, self.config.max_rounds + 1):
                should_continue = await self._run_one_round(
                    output,
                    previous_summary,
                    final_delta_round,
                )
                final_gate = self.callbacks.get_final_gate()
                current_summary = self.callbacks.final_failure_summary(final_gate)
                if not should_continue:
                    break
                previous_summary = current_summary
            else:
                output["reason"] = "max_final_delta_cycles_exhausted"
        except Exception as exc:
            logger.warning("final_delta_repair runtime failed: %s", exc, exc_info=True)
            output.update({
                "reason": "final_delta_repair_exception",
                "error": str(exc),
            })
            for step in (self.blueprint_step, self.execute_step, self.recheck_step):
                await self.callbacks.update_step(step, "failed", output, str(exc))
        except BaseException:
            # asyncio.CancelledError（节点超时取消）是 BaseException，不被 except Exception 捕获。
            # 标记后由 finally 块清理卡在 running 的 step。
            _cancelled_during_repair = True
            output.update({
                "reason": "final_delta_repair_cancelled",
                "error": "node cancelled (likely timeout)",
            })
            raise
        finally:
            # 节点被超时取消时，blueprint/execute/recheck step 可能卡在 running。
            # 用独立后台任务做 best-effort 清理，避免共享 db session 在取消过程中的状态问题。
            if _cancelled_during_repair:
                import asyncio as _asyncio

                async def _cleanup_steps_on_cancel():
                    cancelled_output = {
                        "reason": output.get("reason", "final_delta_repair_cancelled"),
                        "error": output.get("error", "node cancelled (likely timeout)"),
                        "delta_only": True,
                        "runtime": output.get("runtime"),
                    }
                    for step in (self.blueprint_step, self.execute_step, self.recheck_step):
                        try:
                            await self.callbacks.update_step(
                                step, "failed", cancelled_output,
                                "node cancelled (likely timeout)",
                            )
                        except Exception:
                            pass
                try:
                    _asyncio.create_task(_cleanup_steps_on_cancel())
                except Exception:
                    pass

        final_gate = self.callbacks.get_final_gate()
        output["final_allowed"] = bool(final_gate.get("allowed", False))
        output["final_reason"] = final_gate.get("reason", "")
        output["final_case_file_delta"] = final_gate.get("case_file_delta", {})
        final_gate["fbi_delta_repair_cycle"] = output
        self.callbacks.set_final_gate(final_gate)
        self.result = output
        return output

    async def _run_one_round(
        self,
        output: dict[str, Any],
        previous_summary: dict[str, Any],
        final_delta_round: int,
    ) -> bool:
        final_gate = self.callbacks.get_final_gate()
        case_delta = final_gate.get("case_file_delta") or {}
        candidates = self.callbacks.auto_repair_candidates(case_delta)
        cycle: dict[str, Any] = {
            "round": final_delta_round,
            "input_issue_count": len(self.callbacks.review_case_delta_issues(case_delta)),
            "auto_repairable_issue_count": len(candidates),
        }
        if not candidates:
            cycle["reason"] = "remaining_delta_not_auto_repairable"
            output["cycles"].append(cycle)
            return False

        scene_texts = self.callbacks.get_scene_texts()
        cycle_case = self._build_cycle_case(scene_texts, case_delta, final_delta_round)
        await self.callbacks.update_step(
            self.blueprint_step,
            "running",
            {
                "round": final_delta_round,
                "delta_only": True,
                "input_issue_count": cycle["input_issue_count"],
                "auto_repairable_issue_count": cycle["auto_repairable_issue_count"],
            },
            None,
        )
        validated_cycle_case = await FBIChapterCaseIntakeService().intake(cycle_case)
        # 9.4.2: 如果 delta 包含同窗多 issue，优先走 LLM 蓝图官
        cycle_plan: ChapterRepairPlan | None = None
        if self._delta_has_compound_issues(candidates):
            blueprint_packet = self._build_blueprint_packet(
                candidates, scene_texts, validated_cycle_case, case_delta
            )
            try:
                agent = FBIReviewBlueprintAgent()
                agent_result = await agent.execute(blueprint_packet)
            except Exception as exc:
                logger.warning(
                    "FinalDeltaRepairRuntime: LLM blueprint agent failed: %s",
                    exc,
                )
                agent_result = {"status": "degraded"}
            if agent_result.get("status") == "ready":
                cycle_plan = await FBIChapterRepairPlanner().plan(validated_cycle_case)
                llm_blueprint_data = agent_result.get("revision_blueprint") or {}
                try:
                    if isinstance(llm_blueprint_data, dict):
                        llm_blueprint = RevisionBlueprint(**llm_blueprint_data)
                    elif isinstance(llm_blueprint_data, RevisionBlueprint):
                        llm_blueprint = llm_blueprint_data
                    else:
                        llm_blueprint = None
                except Exception:
                    llm_blueprint = None
                if llm_blueprint is not None and llm_blueprint.work_units:
                    cycle_plan.revision_blueprint = llm_blueprint
                    cycle_plan.work_units = list(llm_blueprint.work_units)
                    cycle.update({"blueprint_source": "llm_review_blueprint_agent"})
        if cycle_plan is None:
            # 降级到现有路径
            cycle_plan = await FBIChapterRepairPlanner().plan(validated_cycle_case)
        self.callbacks.update_repair_plan(cycle_plan)
        # Phase U-E: delta recheck 接线 EditWindowGrouper——
        # 从 revision_blueprint 提取 window_id 信息，确保 delta failures trace 可观测。
        delta_window_trace: list[dict[str, Any]] = []
        if cycle_plan.revision_blueprint:
            for unit in cycle_plan.revision_blueprint.work_units or []:
                delta_window_trace.append({
                    "work_unit_id": unit.work_unit_id,
                    "window_id": unit.edit_window_id or unit.edit_window.get("window_id", ""),
                    "source_issue_ids": list(unit.source_violation_ids or []),
                    "source_order_ids": list(unit.source_order_ids or []),
                    "commands": [cmd.operation for cmd in unit.tool_batch.commands],
                })
        cycle.update({
            "case_id": cycle_plan.case_id,
            "status": cycle_plan.status,
            "orders": len(cycle_plan.orders),
            "repair_type_counts": self.callbacks.repair_type_counts(cycle_plan.orders),
            "execution_layers": cycle_plan.execution_layers,
            "order_samples": self.callbacks.repair_order_sample(cycle_plan.orders),
            # U-E: window-first trace，让 delta failures 能显示 window_id
            "window_trace": delta_window_trace,
        })
        await self.callbacks.update_step(
            self.blueprint_step,
            "completed",
            {
                "round": final_delta_round,
                "delta_only": True,
                "case_id": cycle_plan.case_id,
                "status": cycle_plan.status,
                "orders": len(cycle_plan.orders),
                "repair_type_counts": self.callbacks.repair_type_counts(cycle_plan.orders),
                "execution_layers": cycle_plan.execution_layers,
                "revision_blueprint": (
                    cycle_plan.revision_blueprint.model_dump()
                    if cycle_plan.revision_blueprint
                    else {}
                ),
                "order_samples": self.callbacks.repair_order_sample(cycle_plan.orders),
            },
            None,
        )

        if cycle_plan.is_clean():
            cycle["reason"] = "planner_clean"
            output["cycles"].append(cycle)
            await self.callbacks.update_step(
                self.execute_step,
                "skipped",
                {"round": final_delta_round, "reason": "planner_clean", "delta_only": True},
                None,
            )
            await self.callbacks.update_step(
                self.recheck_step,
                "skipped",
                {"round": final_delta_round, "reason": "planner_clean", "delta_only": True},
                None,
            )
            return False

        await self.callbacks.update_step(
            self.execute_step,
            "running",
            {
                "round": final_delta_round,
                "case_id": cycle_plan.case_id,
                "orders": len(cycle_plan.orders),
                "delta_only": True,
            },
            None,
        )
        updated_texts, updated_plan = await ChapterRepairExecutor().execute(
            cycle_plan,
            scene_texts,
            context=self.callbacks.build_executor_context(scene_texts),
            case_file=self.callbacks.build_executor_case_file(scene_texts, case_delta, cycle_plan),
        )
        self.callbacks.update_repair_plan(updated_plan)
        blocking_failed_orders = [
            order for order in updated_plan.orders
            if self.callbacks.is_blocking_order_failure(order)
        ]

        # T5.2: 执行器失败 → 调用新会话重出图入口（讨论稿 §5.2.2 机制3）
        # 当 blocking 订单失败时，开新会话重出图，用新 work_units 重新执行一次。
        # RetryBlueprintRuntime.retry() 内部递归最多 MAX_RETRY_ROUNDS(=2) 轮。
        retry_traces: list[dict[str, Any]] = []
        if blocking_failed_orders:
            retry_result = await self._try_retry_blueprint(
                blocking_failed_orders, cycle_plan, scene_texts, case_delta,
                updated_plan, final_delta_round,
            )
            if retry_result.get("trace"):
                retry_traces.append(retry_result["trace"])
            if retry_result.get("status") == "ready":
                retry_work_units = retry_result.get("work_units") or []
                if retry_work_units and cycle_plan.revision_blueprint is not None:
                    cycle_plan.revision_blueprint.work_units = retry_work_units
                    cycle_plan.work_units = list(retry_work_units)
                    # 用新 work_units 重新执行一次
                    updated_texts, updated_plan = await ChapterRepairExecutor().execute(
                        cycle_plan,
                        scene_texts,
                        context=self.callbacks.build_executor_context(scene_texts),
                        case_file=self.callbacks.build_executor_case_file(
                            scene_texts, case_delta, cycle_plan
                        ),
                    )
                    self.callbacks.update_repair_plan(updated_plan)
                    blocking_failed_orders = [
                        order for order in updated_plan.orders
                        if self.callbacks.is_blocking_order_failure(order)
                    ]
        if retry_traces:
            cycle["retry_blueprint_traces"] = retry_traces

        changed_scenes = self.callbacks.apply_updated_texts(scene_texts, updated_texts)
        cycle.update({
            "changed_scenes": changed_scenes,
            "failed_orders": len(blocking_failed_orders),
            "updated_order_samples": self.callbacks.repair_order_sample(updated_plan.orders),
            "repair_execution_summary": updated_plan.repair_execution_summary,
        })
        await self.callbacks.update_step(
            self.execute_step,
            "completed" if not blocking_failed_orders else "degraded",
            {
                "round": final_delta_round,
                "case_id": updated_plan.case_id,
                "orders": len(updated_plan.orders),
                "changed_scenes": changed_scenes,
                "failed_orders": len(blocking_failed_orders),
                "repair_execution_summary": updated_plan.repair_execution_summary,
                "order_samples": self.callbacks.repair_order_sample(updated_plan.orders),
            },
            None,
        )

        await self.callbacks.update_step(
            self.recheck_step,
            "running",
            {"round": final_delta_round, "changed_scenes": changed_scenes, "delta_only": True},
            None,
        )
        recheck_warnings: list[dict[str, Any]] = []
        # 并行复检所有变更场景（原串行循环，N 场景 N×120s → 并行 max(120s)）
        # 场景间复检无依赖关系，可安全并行。零质量风险。
        #
        # 修复（循环 #17 D17-1）：扩大复检范围到 case_delta 中有违规的所有场景。
        # 原逻辑只复检 changed_scenes，导致"修复成功但文本无变化"的场景不会被复检，
        # 终验无法发现这些场景的违规仍然存在（recheck 盲区）。
        # 修复：合并 changed_scenes 和 case_delta 中有违规的场景，一起复检。
        # 大局观评估（铁律 12 五问）：
        #   1. 能解决目标问题吗？是——消除 recheck 盲区，让"成功但无效"的修复能被发现
        #   2. 会引入新问题吗？否——多复检几个场景只是增加 LLM 调用，不改文本
        #   3. 耗时成本？微增——每个额外场景复检 ~2min，但并行执行取 max
        #   4. 更低成本的替代方案？否——D17-2（文本无变化标记 failed）是 complementary 修复
        #   5. 对下一轮修复的影响？正面——更准确的 recheck 结果让下一轮修复更有针对性
        current_case_delta = self.callbacks.get_final_gate().get("case_file_delta") or {}
        scenes_to_recheck: set[int] = set(changed_scenes)
        for issue in self.callbacks.review_case_delta_issues(current_case_delta):
            if isinstance(issue, dict):
                scene_idx = issue.get("scene_index")
                if isinstance(scene_idx, int):
                    scenes_to_recheck.add(scene_idx)
        if scenes_to_recheck:
            recheck_results = await asyncio.gather(
                *[self.callbacks.recheck_scene(idx) for idx in sorted(scenes_to_recheck)],
                return_exceptions=True,
            )
            for warning in recheck_results:
                if isinstance(warning, dict) and warning:
                    recheck_warnings.append(warning)

        final_gate = await self.callbacks.evaluate_final_acceptance(final_delta_round)
        self.callbacks.set_final_gate(final_gate)
        cycle.update({
            "final_allowed": bool(final_gate.get("allowed", False)),
            "final_reason": final_gate.get("reason", ""),
        })
        current_summary = self.callbacks.final_failure_summary(final_gate)
        cycle_progress = self.callbacks.cycle_progress(previous_summary, current_summary)
        cycle["failure_progress"] = cycle_progress
        if recheck_warnings:
            cycle["recheck_warnings"] = recheck_warnings
        output["cycles"].append(cycle)

        # delta recheck 反馈：L9 的 scene_rewrite 可能破坏 L2 已修复的硬约束
        # (如 missing_must_show 字面锚点)。final_gate 的 skill_gate 不含字面锚点
        # 检查，会错误地 allowed=true。此处将 recheck 发现的 content_blocking
        # 注入 case_file_delta.scene_issues 并标记 allowed=false，让下一轮循环
        # 重新修复。这是通用管线修复，适用于任何被 L9 破坏的 content_blocking。
        recheck_blocking_issues: list[dict[str, Any]] = []
        for warning in recheck_warnings:
            if not isinstance(warning, dict):
                continue
            if warning.get("error"):
                continue
            scene_idx = warning.get("scene_index")
            for violation in warning.get("content_blocking") or []:
                if not isinstance(violation, dict):
                    continue
                issue = self._violation_to_case_issue(violation, scene_idx)
                recheck_blocking_issues.append(issue.model_dump())

        if recheck_blocking_issues:
            case_delta = final_gate.get("case_file_delta") or {}
            scene_issues = list(case_delta.get("scene_issues") or [])
            existing_keys = {
                str(item.get("issue_id") or item.get("dedupe_key") or "")
                for item in scene_issues
                if isinstance(item, dict)
            }
            injected = 0
            for issue_dict in recheck_blocking_issues:
                key = str(issue_dict.get("issue_id") or issue_dict.get("dedupe_key") or "")
                if key and key in existing_keys:
                    continue
                scene_issues.append(issue_dict)
                existing_keys.add(key)
                injected += 1
            if injected > 0:
                case_delta = dict(case_delta)
                case_delta["scene_issues"] = scene_issues
                final_gate = dict(final_gate)
                final_gate["allowed"] = False
                if recheck_blocking_issues:
                    final_gate["reason"] = (
                        "final acceptance recheck content blocking remains: "
                        f"{injected} issue(s) from scene_validator"
                    )
                final_gate["case_file_delta"] = case_delta
                self.callbacks.set_final_gate(final_gate)
                cycle["final_allowed"] = False
                cycle["final_reason"] = final_gate["reason"]
                cycle["recheck_blocking_injected"] = injected

        await self.callbacks.update_step(
            self.recheck_step,
            "completed" if final_gate.get("allowed", False) else "degraded",
            {
                "round": final_delta_round,
                "changed_scenes": changed_scenes,
                "final_allowed": bool(final_gate.get("allowed", False)),
                "final_reason": final_gate.get("reason", ""),
                "failure_progress": cycle_progress,
                "recheck_warnings": recheck_warnings,
            },
            None,
        )

        if final_gate.get("allowed", False):
            output["reason"] = "resolved"
            return False
        if blocking_failed_orders:
            # 通用修复（循环 #18 D18-3）：blocking_failed_orders 时不再直接退出，
            #   而是允许 Round 2 重试（当 max_rounds 还有剩余时）。
            # 根因：循环 #14 的直接退出逻辑假设"blocking_failed_orders 说明问题超出
            #   当前蓝图策略的自动修复能力，外层重试成功率极低"。但 D18-2 发现了一个
            #   反例：goal_present direction bug 导致订单 [1] 误判 succeeded（修复实际
            #   破坏了 goal_present），同时订单 [4] 误判 failed（goal_present 实际没变）。
            #   D18-2 修复后，订单 [1] 正确判定 failed——但这是修复工具破坏了 goal_present，
            #   Round 2 用不同策略（如 scene_rewrite 替代 contract_completion_patch）
            #   可能避免破坏。同时终验 recheck 可能检测到新的违规（修复引入的副作用），
            #   这些新违规只有在 Round 2 的 case_delta 中才会出现。
            # 大局观评估（铁律 12 五问）：
            #   1. 能解决目标问题吗？是——Round 2 能修复 D18-2 后新发现的失败 + recheck 新违规
            #   2. 会引入新问题吗？否——max_rounds 上限防止无限循环
            #   3. 耗时成本？增加——终验从 1 轮增加到 2 轮（+~10min），
            #      但循环 #14 的时间优化前提（"外层重试成功率极低"）已被 D18-2 反例打破
            #   4. 更低成本的替代方案？否——修复工具本身的 bug（破坏 goal_present）
            #      需要更深入的修复，不在本次范围内
            #   5. 对下一轮修复的影响？正面——终验通过后才能进入 waiting_review
            if final_delta_round < self.config.max_rounds:
                output["reason"] = f"blocking_failed_orders_round_{final_delta_round}_will_retry"
                return True
            output["reason"] = "blocking_repair_order_failed"
            return False
        # 修复（循环 #18 D18-1）：移除 D17-4 的 recheck_warnings 跳过逻辑。
        # 根因：D17-4 假设"recheck_warnings 为空说明 L9 修复没有引入新的
        #   content_blocking，Round 2 用相同策略再修一次不会改善"。但这个假设
        #   是错误的——终验 recheck（_delta_recheck_scene）只检测 content_blocking，
        #   不检测 skill_gate metric（three_part_escalation_count 等）。
        #   当 final_gate.allowed=False 是因为 skill_gate 违规（而非 content_blocking）
        #   时，recheck_warnings 为空，但终验仍然失败。
        #   Round 2 会重新从 final_gate_result 提取 case_delta（只包含剩余违规，
        #   比 Round 1 少），LLM 蓝图官会看到不同的违规集合，生成不同的修复计划。
        #   所以 Round 2 不是"用相同策略再修一次"，而是针对剩余违规的精准修复。
        # 大局观评估（铁律 12 五问）：
        #   1. 能解决目标问题吗？是——Round 2 能修复 skill_gate 剩余违规
        #   2. 会引入新问题吗？否——max_rounds 上限防止无限循环
        #   3. 耗时成本？增加——终验从 1 轮增加到 2 轮，但终验通过比节省时间更重要
        #   4. 更低成本的替代方案？否——让 recheck 检测 skill_gate metric 需要大改
        #   5. 对下一轮修复的影响？正面——终验通过后才能进入 waiting_review
        # 方案6 C1：not changed_scenes / not has_progress 时不再直接退出。
        # 如果还有剩余轮次，继续循环让下一轮换策略重试（而非直接放弃）。
        # max_rounds 上限防止无限循环。
        if not changed_scenes:
            if final_delta_round < self.config.max_rounds:
                output["reason"] = f"no_effective_text_change_round_{final_delta_round}_will_retry"
                return True
            output["reason"] = "no_effective_text_change"
            return False
        if not cycle_progress.get("has_progress"):
            if final_delta_round < self.config.max_rounds:
                output["reason"] = f"no_progress_round_{final_delta_round}_will_retry"
                return True
            output["reason"] = "no_progress"
            return False
        return True

    @staticmethod
    def _violation_to_case_issue(violation: dict[str, Any], scene_idx: int | None) -> ReviewCaseIssue:
        """将 recheck 返回的 violation dict 转换为 ReviewCaseIssue。

        让下一轮 _final_delta_auto_repair_candidates 能通过 case_file_delta
        识别并修复这些被 L9 scene_rewrite 破坏的 content_blocking violations。
        """
        v_type = str(violation.get("type") or violation.get("violation_type") or "unknown")
        severity = str(violation.get("severity") or "high").lower()
        if severity not in ("critical", "high", "medium", "low"):
            severity = "high"
        metric = str(violation.get("metric") or v_type)
        detail = str(violation.get("detail") or f"{v_type} violation in scene {scene_idx}")
        # 修复（循环 #14 大局观修正）：正确映射上游 repairability 到 ReviewCaseIssue
        # 允许的 4 值枚举，保留策略语义。
        # 根因分析（大局观）：
        #   迭代 12 将 cross_scene_fixable→scene_rewrite，虽然修复了 internal_conflict
        #   的路由问题，但 scene_rewrite 是最激进策略，会重写整个场景，破坏 L2 已修复的
        #   must_show 锚点，引入 missing_must_show 新违规，导致终验从 2 轮增至 3 轮，
        #   耗时从 55min 增至 85min（违反铁律 11+12）。
        #   迭代 11 的 local_patch "无效"根因不是策略错误，而是 old_text_not_found
        #   （场景正文被截断到 3000 字，LLM 看不到实际文本）。迭代 13 已将截断上限
        #   提升到 20000 字，消除了 old_text_not_found。
        #   因此正确修复是：cross_scene_fixable→local_patch（轻量策略），
        #   依赖迭代 13 的截断修复使 local_patch 有效，同时通过 evidence.suggested_strategy
        #   保留上游策略语义（STRATEGY_MAP 中 internal_conflict=patch_text）。
        # 映射规则：
        #   cross_scene_fixable → local_patch（轻量局部修复，依赖精准 target_span）
        #   contract_repair_required → local_patch（局部插入/替换补齐缺失内容）
        #   auto_fixable → local_patch（通用自动修复）
        #   human_review_required → manual_review（需人工审查）
        _REPAIRABILITY_MAP = {
            "deterministic": "deterministic",
            "local_patch": "local_patch",
            "scene_rewrite": "scene_rewrite",
            "manual_review": "manual_review",
            "cross_scene_fixable": "local_patch",
            "contract_repair_required": "local_patch",
            "auto_fixable": "local_patch",
            "human_review_required": "manual_review",
        }
        raw_repairability = str(violation.get("repairability") or "local_patch").lower()
        repairability = _REPAIRABILITY_MAP.get(raw_repairability, "local_patch")
        # 在 evidence 中保留原始 repairability 和 suggested_strategy，
        # 便于调试和下游覆盖（case_file_issues_to_violations 可从 evidence 恢复）。
        original_strategy = str(violation.get("suggested_strategy") or "").lower()
        # dedupe_key 生成：优先用上游提供的 dedupe_key，其次组合 scene/type/metric
        # 加上 detail/target_span 的短哈希，避免同场景同类型但不同文本内容的
        # 多个 violation（如多个 identity_conflict）被误判为同一个而互相覆盖。
        existing_dedupe = str(violation.get("dedupe_key") or "")
        if existing_dedupe:
            dedupe_key = existing_dedupe
        else:
            fingerprint_src = f"{detail[:80]}|{str(violation.get('target_span') or '')[:80]}"
            fingerprint = hashlib.md5(fingerprint_src.encode("utf-8")).hexdigest()[:8]
            dedupe_key = f"{scene_idx}:{v_type}:{metric}:{fingerprint}"
        return ReviewCaseIssue(
            issue_id=str(violation.get("issue_id") or violation.get("violation_id") or ""),
            source="final_acceptance_recheck",
            source_validator="scene_validator",
            scene_index=scene_idx,
            scope="scene",
            type=v_type,
            metric=metric,
            severity=severity,
            blocks_commit=True,
            repairability=repairability,
            repair_domain=str(violation.get("repair_domain") or "prose"),
            detail=detail,
            target_span=str(violation.get("target_span") or ""),
            expected_behavior=str(violation.get("expected_behavior") or ""),
            evidence={
                **dict(violation.get("evidence") or {}),
                # 保留原始 repairability 和 suggested_strategy，让下游
                # case_file_issues_to_violations 能恢复正确的策略语义。
                "original_repairability": raw_repairability,
                "suggested_strategy": original_strategy,
            },
            dedupe_key=dedupe_key,
            sources=["final_acceptance_recheck"],
            status="raw",
        )

    @staticmethod
    def _delta_has_compound_issues(candidates: list[dict[str, Any]]) -> bool:
        """9.4.2: 检查 delta 是否包含同窗多 issue（需要 compound patch）。

        按 scene_index 分组，如果任一场景有 >1 个 candidate，返回 True。
        9.4.1+: 如果存在可通过路由表修复的 violation（如 spatial_conflict），
        也返回 True，确保 delta 阶段走 LLM 蓝图官补全 executable blueprint。
        """
        if not candidates:
            return False
        # 条件 1：同窗多 issue
        if len(candidates) >= 2:
            scene_counts: dict[int | None, int] = {}
            for candidate in candidates:
                if not isinstance(candidate, dict):
                    continue
                scene_index = candidate.get("scene_index")
                if scene_index is None:
                    target_scenes = candidate.get("target_scenes") or []
                    if target_scenes:
                        scene_index = target_scenes[0]
                scene_counts[scene_index] = scene_counts.get(scene_index, 0) + 1
            if any(count > 1 for count in scene_counts.values()):
                return True
        # 条件 2：存在可通过路由表修复的 violation
        # 9.4.1+: delta recheck 发现的 spatial_conflict / fact_conflict 等必须走蓝图官
        from app.services.fbi.blueprint_route_registry import get_blueprint_route
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            metric = str(
                candidate.get("metric")
                or candidate.get("type")
                or candidate.get("violation_type")
                or ""
            ).lower()
            if metric and get_blueprint_route(metric) is not None:
                return True
        return False

    def _build_blueprint_packet(
        self,
        candidates: list[dict[str, Any]],
        scene_texts: dict[int, str],
        validated_cycle_case: ChapterReviewCase,
        case_delta: dict[str, Any],
    ) -> dict[str, Any]:
        """9.4.2: 构造 LLM 蓝图官的输入 context。"""
        # 收集 intake 处理后的 violations，按 (type, target_span) 建索引。
        # intake 会将 delta 违规分发到 scene packet 并覆写 source_scene 为 packet.scene_index，
        # orders 的 owner_scene 来自 intake 处理后的 violations。
        # LLM 蓝图官必须使用与 orders 一致的 source_scene，否则 work_units 生成的 patch
        # 会路由到错误场景（打地鼠问题：修 scene=1 引入 scene=0 新问题）。
        intake_violation_index: dict[tuple[str, str], dict[str, Any]] = {}
        for packet in validated_cycle_case.scene_packets:
            for iv in packet.blocking_violations:
                if not isinstance(iv, dict):
                    continue
                iv_type = str(iv.get("violation_type") or iv.get("type") or "")
                iv_span = iv.get("target_span") or iv.get("span") or ""
                if isinstance(iv_span, dict):
                    iv_span = f"{iv_span.get('start', '')}:{iv_span.get('end', '')}"
                intake_violation_index[(iv_type.lower(), str(iv_span))] = iv

        # 收集 violations
        violations: list[dict[str, Any]] = []
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            violation = dict(candidate)
            # 确保有 issue_id
            if not violation.get("issue_id"):
                violation["issue_id"] = violation.get("id") or ""
            # 用 intake 处理后的 source_scene 校正，确保与 orders 路由一致
            c_type = str(
                violation.get("violation_type")
                or violation.get("type")
                or violation.get("metric")
                or ""
            ).lower()
            c_span = violation.get("target_span") or violation.get("span") or ""
            if isinstance(c_span, dict):
                c_span = f"{c_span.get('start', '')}:{c_span.get('end', '')}"
            matched = intake_violation_index.get((c_type, str(c_span)))
            if matched is not None:
                intake_scene = matched.get("source_scene")
                if isinstance(intake_scene, int):
                    violation["source_scene"] = intake_scene
                    violation["scene_index"] = intake_scene
                    violation["target_scenes"] = [intake_scene]
            violations.append(violation)

        # 收集场景正文（只包含有问题的场景）
        problem_scenes: set[int] = set()
        for v in violations:
            scene_index = v.get("scene_index")
            if scene_index is None:
                target_scenes = v.get("target_scenes") or []
                if target_scenes:
                    scene_index = target_scenes[0]
            if isinstance(scene_index, int):
                problem_scenes.add(scene_index)
        relevant_scene_texts = {
            idx: text for idx, text in scene_texts.items()
            if idx in problem_scenes
        }
        if not relevant_scene_texts:
            relevant_scene_texts = dict(scene_texts)

        # 场景合同
        scene_contract = {}
        chapter_state = self.config.chapter_state or {}
        if isinstance(chapter_state, dict):
            scene_contract = chapter_state.get("scene_contract") or {}

        return {
            "case_id": str(validated_cycle_case.case_id or ""),
            "violations": violations,
            "scene_texts": relevant_scene_texts,
            "scene_contract": scene_contract,
            "protection_boundary": chapter_state.get("protection_boundary") or {},
            "llm_planning_enabled": True,
            "tool_failure_feedback": case_delta.get("tool_failure_feedback") or [],
        }

    def _build_cycle_case(
        self,
        scene_texts: dict[int, str],
        case_delta: dict[str, Any],
        final_delta_round: int,
    ) -> ChapterReviewCase:
        synthetic_packets = [
            SceneReviewPacket(
                scene_index=index,
                candidate_text=text,
                text_hash=hashlib.md5(text.encode("utf-8")).hexdigest()[:12],
                repairability="clean",
            )
            for index, text in scene_texts.items()
        ]
        return ChapterReviewCase(
            project_id=self.config.project_id,
            chapter_number=self.config.chapter_number,
            chapter_initial_state=self.config.chapter_state,
            scene_packets=synthetic_packets,
            chapter_draft_hash=hashlib.md5(
                "\n\n".join(scene_texts.values()).encode("utf-8")
            ).hexdigest()[:12],
            review_round=self.config.repair_round + final_delta_round,
            review_case_file={},
            case_file_deltas=[case_delta],
        )

    async def _skip_remaining(
        self,
        start_index: int,
        reason: str,
        output: dict[str, Any],
    ) -> None:
        steps = [self.intake_step, self.blueprint_step, self.execute_step, self.recheck_step]
        for step in steps[start_index:]:
            await self.callbacks.update_step(
                step,
                "skipped",
                {"reason": reason, "delta_only": True, "runtime": output.get("runtime")},
                None,
            )

    async def _try_retry_blueprint(
        self,
        blocking_failed_orders: list[Any],
        cycle_plan: ChapterRepairPlan,
        scene_texts: dict[int, str],
        case_delta: dict[str, Any],
        updated_plan: ChapterRepairPlan,
        final_delta_round: int,
    ) -> dict[str, Any]:
        """T5.2: 执行器失败后开新会话重出图。

        讨论稿 §5.2.2 机制3：阶段2（执行）失败后，开新会话重出图。
        新会话只有 submit_work_units tool（闸门②），不带确定性工具。
        RetryBlueprintRuntime.retry() 内部递归最多 MAX_RETRY_ROUNDS(=2) 轮。

        Returns:
            {"status": "ready"|"failed", "work_units": [...], "trace": {...}}
        """
        # 提取失败的 issue_ids
        failed_issue_ids: list[str] = []
        for order in blocking_failed_orders:
            # 优先从 violation_details 提取 issue_id
            violation_details = getattr(order, "violation_details", None) or []
            for v in violation_details:
                if isinstance(v, dict):
                    issue_id = str(v.get("issue_id") or v.get("id") or "")
                    if issue_id and issue_id not in failed_issue_ids:
                        failed_issue_ids.append(issue_id)
            # 兜底：从 order 的 source_violation_ids 提取
            if not violation_details:
                for wu in (updated_plan.work_units or []):
                    if getattr(order, "order_id", "") in (wu.source_order_ids or []):
                        for vid in (wu.source_violation_ids or []):
                            if vid and vid not in failed_issue_ids:
                                failed_issue_ids.append(vid)

        if not failed_issue_ids:
            logger.warning(
                "FinalDeltaRepairRuntime._try_retry_blueprint: "
                "无法从 blocking_failed_orders 提取 failed_issue_ids，跳过重出图"
            )
            return {"status": "failed", "reason": "no_failed_issue_ids", "trace": {}}

        # 构造 retry context
        # 从 case_delta 或 cycle_plan 提取 violations
        violations: list[dict[str, Any]] = []
        delta_issues = self.callbacks.review_case_delta_issues(case_delta)
        for issue in delta_issues:
            if isinstance(issue, dict):
                v = dict(issue)
                if not v.get("issue_id"):
                    v["issue_id"] = v.get("id") or ""
                violations.append(v)

        # 如果 case_delta 没有 issues，从 cycle_plan.work_units 的 source_violation_ids 兜底
        # 附录4问题33修复：补齐 violation 字段，避免下游 retry_blueprint_runtime
        # 取不到 repair_family/family/scene_id 导致 expected_metric_delta metric="unknown"。
        if not violations:
            for wu in (cycle_plan.work_units or []):
                wu_family = ""
                if getattr(wu, "compound_issue_families", None):
                    wu_family = str(wu.compound_issue_families[0])
                wu_scene = getattr(wu, "owner_scene", None)
                for vid in (wu.source_violation_ids or []):
                    violations.append({
                        "issue_id": vid,
                        "type": "unknown",
                        "family": wu_family,
                        "repair_family": wu_family,
                        "severity": "high",
                        "source": "fbi_review",
                        "layer": "hard_correctness",
                        "scene_id": wu_scene,
                        "scene_index": wu_scene,
                        "metric": wu_family or "unknown",
                    })

        retry_context = {
            "failed_issue_ids": failed_issue_ids,
            "failure_reason": "blocking_repair_order_failed",
            "retry_round": 1,
            "violations": violations,
            "scene_texts": scene_texts,
            "case_delta": case_delta,
            "tool_failure_feedback": case_delta.get("tool_failure_feedback") or [],
        }

        try:
            from app.services.fbi.retry_blueprint_runtime import RetryBlueprintRuntime
            runtime = RetryBlueprintRuntime()
            result = await runtime.retry(
                failed_issue_ids=failed_issue_ids,
                failure_reason="blocking_repair_order_failed",
                retry_round=1,
                context=retry_context,
            )
        except Exception as exc:
            logger.warning(
                "FinalDeltaRepairRuntime._try_retry_blueprint: RetryBlueprintRuntime 失败: %s",
                exc,
                exc_info=True,
            )
            return {
                "status": "failed",
                "reason": f"retry_runtime_exception: {exc}",
                "trace": {"failed_issue_ids": failed_issue_ids},
            }

        return result or {"status": "failed", "reason": "empty_result", "trace": {}}
