"""阶段2/3失败后的新会话重出图运行时（T5）。

讨论稿 §5.2.2 机制3 / §5.3：
- 阶段2（执行）或阶段3（复检）失败后，开新会话重出图
- 新会话只有 submit_work_units tool（闸门②），不带确定性工具
- 最多 2 轮重试
- 失败原因 + 轮次 + issue 列表 + 归一化结果作为上下文
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

from app.agents.base import BaseAgent
from app.agents.fbi.blueprint_tool_schemas import SUBMIT_WORK_UNITS_SCHEMA
from app.models.chapter_review import RepairWorkUnit, ToolCommand, ToolCommandBatch
from app.services.llm_task_profiles import LLMTaskType

_logger = logging.getLogger(__name__)

MAX_RETRY_ROUNDS = 2  # 新会话重出图上限

# LLM 只允许输出这 4 个确定性 patch operation（与 agent _LLM_ALLOWED_OPERATIONS 一致）
_RETRY_ALLOWED_OPERATIONS = {
    "replace_exact",
    "delete_exact",
    "insert_before_anchor",
    "insert_after_anchor",
}


class RetryBlueprintRuntime(BaseAgent):
    """阶段2/3失败后开新会话重出图。

    新会话约束：
    - 只有 submit_work_units tool（闸门②校验）
    - 不带确定性工具（本会话无确定性工具能力）
    - 每个 issue 必须在某个 work_unit 的 source_issue_ids 里
    """

    name = "fbi_retry_blueprint"

    async def execute(self, context: dict) -> dict:
        """BaseAgent 抽象方法实现：从 context 提取参数并调用 retry()。

        context 约定：
        - failed_issue_ids: list[str]  失败 issue ID
        - failure_reason: str          失败原因
        - retry_round: int             起始轮次（默认 1）
        - violations / all_blocking:   原始违规列表
        - scene_texts: dict[int, str]  场景原文
        - diagnosis_set: 可选          归一化结果
        """
        failed_issue_ids = list(context.get("failed_issue_ids") or [])
        failure_reason = str(context.get("failure_reason") or "unknown")
        retry_round = int(context.get("retry_round") or 1)
        return await self.retry(failed_issue_ids, failure_reason, retry_round, context)

    async def retry(
        self,
        failed_issue_ids: list[str],
        failure_reason: str,
        retry_round: int,
        context: dict,
    ) -> dict:
        """开新会话重出图。

        Args:
            failed_issue_ids: 失败的 issue ID 列表
            failure_reason: 失败原因（old_text_not_found / recheck_failed 等）
            retry_round: 第几轮重试（1 或 2）
            context: 含归一化结果、原始 violations、scene_texts

        Returns:
            {"status": "ready"|"failed", "work_units": [...], "trace": {...}}
        """
        if retry_round > MAX_RETRY_ROUNDS:
            return {
                "status": "failed",
                "reason": "max_retry_exceeded",
                "retry_round": retry_round,
            }

        llm_client = await self._get_llm_client(context)

        # 构造新会话消息（含失败原因+轮次+issue列表+归一化结果）
        messages = self._build_retry_messages(
            failed_issue_ids, failure_reason, retry_round, context
        )

        # 新会话只有 submit_work_units，没有确定性工具
        tools = [SUBMIT_WORK_UNITS_SCHEMA]

        # 调用 LLM
        result = await self._call_llm_with_tools(llm_client, messages, tools)

        # 解析+闸门②校验
        work_units, gate_errors = self._parse_and_validate(
            result, failed_issue_ids, retry_round, context
        )

        trace = {
            "retry_round": retry_round,
            "failure_reason": failure_reason,
            "failed_issue_ids": failed_issue_ids,
            "has_tool_calls": result.get("has_tool_calls", False),
            "gate_errors": gate_errors,
            "work_unit_count": len(work_units),
        }

        if work_units:
            return {
                "status": "ready",
                "work_units": work_units,
                "trace": trace,
            }

        # 本轮失败，递归到下一轮
        _logger.warning(
            "RetryBlueprintRuntime: 第 %d 轮重出图失败，gate_errors=%s, 递归下一轮",
            retry_round, gate_errors,
        )
        # P1-27 修复：递归重试前添加指数退避，避免立即重试导致雪崩
        await asyncio.sleep(2 ** (retry_round - 1))
        return await self.retry(
            failed_issue_ids, failure_reason, retry_round + 1, context
        )

    # ------------------------------------------------------------------
    # 消息构造
    # ------------------------------------------------------------------

    def _build_retry_messages(
        self,
        failed_issue_ids: list[str],
        failure_reason: str,
        retry_round: int,
        context: dict,
    ) -> list[dict]:
        """构造新会话消息。"""
        violations = context.get("violations") or context.get("all_blocking") or []
        failed_violations = [
            v for v in violations
            if isinstance(v, dict) and str(v.get("issue_id") or "") in failed_issue_ids
        ]

        scene_texts = context.get("scene_texts") or {}

        system = (
            f"你是 FBI 审查蓝图官，正在进行第 {retry_round} 轮重出图。\n"
            f"失败原因：{failure_reason}\n"
            f"需要重新出图的 issue：{failed_issue_ids}\n\n"
            "约束：\n"
            "1. 直接调用 submit_work_units 提交出图\n"
            "2. 不调确定性工具（本会话无确定性工具能力）\n"
            "3. 每个 issue 必须在某个 work_unit 的 source_issue_ids 里\n"
            "4. operation 只允许：replace_exact / delete_exact / insert_before_anchor / insert_after_anchor\n"
            "5. old_text 必须是原文中存在的精确片段\n"
            "6. new_text 必须是中文自然陈述句，不含英文系统诊断句\n"
        )
        user = self._format_retry_context(failed_violations, scene_texts, context)

        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

    def _format_retry_context(
        self,
        failed_violations: list[dict],
        scene_texts: dict[int, str],
        context: dict,
    ) -> str:
        """格式化重出图上下文为用户消息。"""
        parts: list[str] = []

        parts.append("=== 失败的违规列表 ===")
        for v in failed_violations:
            issue_id = v.get("issue_id", "")
            v_type = v.get("type") or v.get("violation_type") or "unknown"
            detail = v.get("detail") or v.get("reason") or ""
            scene = v.get("source_scene") or v.get("scene_index")
            parts.append(f"[{issue_id}] scene={scene} type={v_type}\n  {detail}")

        parts.append("\n=== 场景原文 ===")
        for idx, text in sorted(scene_texts.items()):
            preview = (text or "")[:800]
            parts.append(f"--- scene {idx} ---\n{preview}")

        # 失败反馈
        failure_feedback = context.get("tool_failure_feedback") or []
        if failure_feedback:
            parts.append("\n=== 失败反馈 ===")
            for fb in failure_feedback:
                parts.append(json.dumps(fb, ensure_ascii=False, indent=2))

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # 解析 + 闸门②校验
    # ------------------------------------------------------------------

    def _parse_and_validate(
        self,
        llm_result: dict,
        failed_issue_ids: list[str],
        retry_round: int,
        context: dict,
    ) -> tuple[list[RepairWorkUnit], list[str]]:
        """解析 submit_work_units 调用并执行闸门②校验。

        Returns:
            (work_units, gate_errors) — work_units 为空表示校验失败
        """
        if not llm_result.get("has_tool_calls"):
            return [], ["no_tool_call"]

        # 找到 submit_work_units 调用
        submit_call = None
        for tc in llm_result.get("tool_calls", []):
            name = tc.get("function", {}).get("name", "")
            if name == "submit_work_units":
                submit_call = tc
                break

        if submit_call is None:
            return [], ["missing_submit_work_units_call"]

        # 解析参数
        args_raw = submit_call.get("function", {}).get("arguments", "{}")
        try:
            args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
        except json.JSONDecodeError as exc:
            return [], [f"arguments_json_parse_error: {exc}"]

        work_units_data = args.get("work_units") or []
        if not work_units_data:
            return [], ["empty_work_units"]

        # 解析 + 闸门②校验
        scene_texts = context.get("scene_texts") or {}
        violations = context.get("violations") or context.get("all_blocking") or []
        issue_to_violation: dict[str, dict] = {}
        for v in violations:
            if isinstance(v, dict):
                issue_id = str(v.get("issue_id") or "")
                if issue_id:
                    issue_to_violation[issue_id] = v

        work_units: list[RepairWorkUnit] = []
        errors: list[str] = []
        covered_issue_ids: set[str] = set()

        for idx, wu in enumerate(work_units_data):
            if not isinstance(wu, dict):
                errors.append(f"work_unit[{idx}] not a dict")
                continue

            operation = str(wu.get("operation") or "")
            source_issue_ids = [
                str(i) for i in (wu.get("source_issue_ids") or []) if i
            ]

            # 闸门②-1: operation 合法性（越权检测）
            if operation not in _RETRY_ALLOWED_OPERATIONS:
                errors.append(
                    f"work_unit[{idx}] invalid operation '{operation}', "
                    f"allowed: {_RETRY_ALLOWED_OPERATIONS}"
                )
                continue

            # 闸门②-2: source_issue_ids 非空
            if not source_issue_ids:
                errors.append(f"work_unit[{idx}] missing source_issue_ids")
                continue

            # 闸门②-3: 字段完整性
            new_text = str(wu.get("new_text") or "")
            if not new_text and operation != "delete_exact":
                errors.append(f"work_unit[{idx}] missing new_text")
                continue
            if operation in ("replace_exact", "delete_exact") and not wu.get("old_text"):
                errors.append(f"work_unit[{idx}] missing old_text for {operation}")
                continue
            if operation in ("insert_before_anchor", "insert_after_anchor") and not wu.get("anchor_text"):
                errors.append(f"work_unit[{idx}] missing anchor_text for {operation}")
                continue

            # 构造 RepairWorkUnit
            scene_index = wu.get("scene_index")
            if scene_index is None:
                # 从 issue 推断 scene
                for iid in source_issue_ids:
                    v = issue_to_violation.get(iid)
                    if v:
                        scene_index = v.get("source_scene") or v.get("scene_index")
                        if scene_index is not None:
                            break
            scene_index = int(scene_index) if scene_index is not None else 0

            # 推断 family
            repair_family = ""
            for iid in source_issue_ids:
                v = issue_to_violation.get(iid)
                if v:
                    repair_family = str(v.get("repair_family") or v.get("family") or "")
                    if repair_family:
                        break

            # 方案22修复：补齐 expected_metric_delta 和 guards 字段，
            # 否则 BlueprintProtocolValidator 会标记 blueprint_not_compilable，
            # 导致 retry 生成的 work_units 全部被过滤为空，tool_execution.attempted=false。
            # 与 FBIReviewBlueprintAgent._build_tool_command 保持一致。
            expected_metric_delta = wu.get("expected_metric_delta") or {}
            if not expected_metric_delta:
                expected_metric_delta = {
                    "metric": repair_family or "unknown",
                    "operator": "decrease",
                    "target_delta": -1,
                }
            guards = wu.get("guards") or {}
            if not guards:
                guards = {"preserve_facts": True, "preserve_pov": True}

            cmd = ToolCommand(
                operation=operation,
                scene_index=scene_index,
                target_span=str(wu.get("target_span") or wu.get("old_text") or ""),
                replacement=new_text,
                anchor_text=str(wu.get("anchor_text") or ""),
                anchor_occurrence=int(wu.get("anchor_occurrence") or 1),
                old_text=str(wu.get("old_text") or ""),
                new_text=new_text,
                source_issue_ids=source_issue_ids,
                repair_family=repair_family,
                expected_metric_delta=expected_metric_delta,
                guards=guards,
            )

            work_unit = RepairWorkUnit(
                work_unit_id=f"retry_wu_{uuid.uuid4().hex[:8]}",
                owner_scene=scene_index,
                target_scenes=[scene_index],
                source_violation_ids=source_issue_ids,
                compound_issue_families=[repair_family] if repair_family else [],
                tool_batch=ToolCommandBatch(
                    batch_id=f"retry_batch_{uuid.uuid4().hex[:8]}",
                    target_scenes=[scene_index],
                    commands=[cmd],
                ),
                blueprint_source=f"fbi_retry_blueprint[retry:round{retry_round}]",
            )
            work_units.append(work_unit)
            covered_issue_ids.update(source_issue_ids)

        # 闸门②-4: 覆盖完整性
        uncovered = set(failed_issue_ids) - covered_issue_ids
        if uncovered:
            errors.append(f"uncovered_issue_ids: {sorted(uncovered)}")

        if errors:
            return [], errors

        return work_units, []

    # ------------------------------------------------------------------
    # LLM 调用辅助
    # ------------------------------------------------------------------

    async def _get_llm_client(self, context: dict):
        """获取 LLM 客户端（支持 context 覆盖）。"""
        override = context.get("_llm_client")
        if override is not None:
            return override
        return await self.get_llm_client()

    async def _call_llm_with_tools(
        self, llm_client, messages: list[dict], tools: list[dict]
    ) -> dict:
        """调 LLM with tools，统一错误处理。

        方案 29：使用 REPAIR_WITH_TOOLS 预设（max_tokens=65536, timeout=120s, retries=3），
        替代旧的 timeout=30s + max_tokens=4096（对推理模型完全不可用）。
        """
        try:
            return await llm_client.generate_with_tools(
                messages=messages,
                tools=tools,
                temperature=0.2,
                task_type=LLMTaskType.REPAIR_WITH_TOOLS,
            )
        except Exception as exc:
            _logger.warning("RetryBlueprintRuntime: LLM 调用失败: %s", exc)
            return {
                "has_tool_calls": False,
                "content": f"LLM call failed: {exc}",
                "reasoning_content": None,
            }
