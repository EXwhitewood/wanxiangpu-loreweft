"""FBI workflow-level metrics aggregation.

This module is deliberately read-only. It converts planner/executor/final-delta
trace payloads into a stable observability envelope so the workflow can report
repair capability without mixing metrics collection into repair policy.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.models.chapter_review import ChapterRepairPlan


class FBIWorkflowMetrics:
    """Normalize FBI planner and executor summaries for workflow traces."""

    @staticmethod
    def from_plan(plan: ChapterRepairPlan | None) -> dict[str, Any]:
        if plan is None:
            return {
                "available": False,
                "reason": "missing_repair_plan",
            }
        strategy = plan.repair_strategy_summary or {}
        execution = plan.repair_execution_summary or {}
        blueprint = strategy.get("revision_blueprint") or {}
        execution_blueprint = execution.get("blueprint_coverage") or {}
        tool_execution = execution.get("tool_execution") or {}
        executor_paths = execution.get("executor_path_counts") or {}
        false_positive_suppressed = (
            FBIWorkflowMetrics._false_positive_suppressed_count(plan)
            + FBIWorkflowMetrics._false_positive_suppressed_from_execution(execution)
        )
        total_orders = int(
            execution_blueprint.get("total_order_count")
            or blueprint.get("total_order_count")
            or len(plan.orders)
            or 0
        )
        executable_orders = int(
            execution_blueprint.get("executable_order_count")
            or blueprint.get("executable_order_count")
            or 0
        )
        return {
            "available": True,
            "case_id": plan.case_id,
            "plan_status": plan.status,
            "total_order_count": total_orders,
            "work_unit_count": len(plan.work_units or []),
            "executable_order_count": executable_orders,
            "executable_blueprint_rate": (
                round(executable_orders / total_orders, 4)
                if total_orders
                else 1.0
            ),
            "blueprint_completion_rate": (
                round(executable_orders / total_orders, 4)
                if total_orders
                else 1.0
            ),
            "blueprint_completion_required_count": int(
                execution_blueprint.get("blueprint_completion_required_count")
                or blueprint.get("blueprint_completion_required_count")
                or 0
            ),
            "false_positive_suppressed_count": false_positive_suppressed,
            "false_positive_suppression_rate": (
                round(false_positive_suppressed / total_orders, 4)
                if total_orders
                else 0.0
            ),
            "legacy_executor_disabled_count": int(
                ((tool_execution.get("legacy_disabled") or {}).get("disabled_order_count"))
                or 0
            ),
            "legacy_fallback_after_tool_failure": int(
                execution.get("legacy_fallback_after_tool_failure") or 0
            ),
            "tool_accepted_count": int(tool_execution.get("accepted") or 0),
            "tool_failed_count": int(tool_execution.get("failed") or 0),
            "executor_path_counts": dict(executor_paths),
            "target_improved_rate": execution.get("target_improved_rate"),
            "protection_rejection_rate": execution.get("protection_rejection_rate"),
            "validator_snapshot_hash": blueprint.get("validator_snapshot_hash"),
        }

    @staticmethod
    def _false_positive_suppressed_count(plan: ChapterRepairPlan) -> int:
        count = 0
        for order in plan.orders or []:
            audit = order.repair_audit or {}
            if audit.get("reason") == "false_positive_suppressed":
                count += 1
                continue
            tool_audit = audit.get("tool_audit") if isinstance(audit.get("tool_audit"), dict) else {}
            if tool_audit.get("reason") == "false_positive_suppressed":
                count += 1
                continue
            for result in tool_audit.get("command_results") or []:
                if isinstance(result, dict) and result.get("reason") == "false_positive_suppressed":
                    count += 1
                    break
            for failure in audit.get("tool_failures") or []:
                if isinstance(failure, dict) and failure.get("reason") == "false_positive_suppressed":
                    count += 1
                    break
        return count

    @staticmethod
    def _false_positive_suppressed_from_execution(execution: dict[str, Any]) -> int:
        audits = ((execution.get("tool_execution") or {}).get("audits") or {})
        count = 0
        for audit in audits.values():
            if not isinstance(audit, dict):
                continue
            for result in audit.get("command_results") or []:
                if isinstance(result, dict) and result.get("reason") == "false_positive_suppressed":
                    count += 1
                    break
        return count

    @staticmethod
    def from_final_delta_cycle(cycle_output: dict[str, Any] | None) -> dict[str, Any]:
        output = cycle_output or {}
        cycles = [item for item in output.get("cycles") or [] if isinstance(item, dict)]
        return {
            "attempted": bool(output.get("attempted")),
            "delta_only": bool(output.get("delta_only", True)),
            "initial_issue_count": int(output.get("initial_issue_count") or 0),
            "auto_repairable_issue_count": int(output.get("auto_repairable_issue_count") or 0),
            "cycle_count": len(cycles),
            "changed_scene_count": len({
                scene
                for cycle in cycles
                for scene in (cycle.get("changed_scenes") or [])
                if isinstance(scene, int)
            }),
            "failed_order_count": sum(int(cycle.get("failed_orders") or 0) for cycle in cycles),
            "last_reason": str(output.get("reason") or (cycles[-1].get("reason") if cycles else "") or ""),
        }

    @classmethod
    def combine(
        cls,
        *,
        plan: ChapterRepairPlan | None = None,
        final_delta_cycle: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "fbi_workflow_metrics_version": 1,
            "repair_plan": cls.from_plan(plan),
            "final_delta_cycle": cls.from_final_delta_cycle(final_delta_cycle),
        }

    @staticmethod
    def summarize_sqlite_workflows(connection, *, limit: int = 20) -> dict[str, Any]:
        """Build a read-only success/timing report from local SQLite workflow tables."""
        connection.row_factory = getattr(connection, "row_factory", None) or connection.row_factory
        columns = [
            row[1] if not isinstance(row, dict) else row["name"]
            for row in connection.execute("pragma table_info(workflow_executions)").fetchall()
        ]
        id_col = "execution_id" if "execution_id" in columns else "id"
        created_col = "created_at" if "created_at" in columns else ""
        updated_col = "updated_at" if "updated_at" in columns else ""
        trigger_col = "trigger_type" if "trigger_type" in columns else ""
        order_col = updated_col or created_col or id_col
        select_cols = [id_col, "status"]
        for col in (created_col, updated_col, trigger_col, "error_message"):
            if col and col in columns and col not in select_cols:
                select_cols.append(col)
        rows = connection.execute(
            f"select {', '.join(select_cols)} from workflow_executions order by {order_col} desc limit ?",
            (int(limit),),
        ).fetchall()
        executions: list[dict[str, Any]] = []
        status_counts: dict[str, int] = {}
        durations: list[float] = []
        for row in rows:
            item = dict(row) if hasattr(row, "keys") else dict(zip(select_cols, row))
            status = str(item.get("status") or "")
            status_counts[status] = status_counts.get(status, 0) + 1
            duration = FBIWorkflowMetrics._duration_seconds(
                item.get(created_col) if created_col else None,
                item.get(updated_col) if updated_col else None,
            )
            if duration is not None:
                durations.append(duration)
            executions.append({
                "id": item.get(id_col),
                "status": status,
                "trigger_type": item.get(trigger_col) if trigger_col else "",
                "duration_seconds": duration,
                "error_message": item.get("error_message", ""),
            })
        total = len(executions)
        completed = status_counts.get("completed", 0) + status_counts.get("success", 0)
        terminal = sum(
            count for status, count in status_counts.items()
            if status in {"completed", "success", "failed", "cancelled", "waiting_review"}
        )
        return {
            "fbi_workflow_report_version": 1,
            "sample_size": total,
            "status_counts": status_counts,
            "success_rate": round(completed / terminal, 4) if terminal else 0.0,
            "terminal_count": terminal,
            "average_duration_seconds": round(sum(durations) / len(durations), 3) if durations else None,
            "max_duration_seconds": round(max(durations), 3) if durations else None,
            "executions": executions,
        }

    @staticmethod
    def _duration_seconds(start: Any, end: Any) -> float | None:
        if not start or not end:
            return None
        try:
            start_dt = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
            end_dt = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
            return max(0.0, (end_dt - start_dt).total_seconds())
        except Exception:
            return None
