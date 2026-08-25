"""Protocol gate for FBI review blueprints and executable work units."""
from __future__ import annotations

from typing import Any

from app.models.chapter_review import RepairWorkUnit, RevisionBlueprint, ToolCommand
from app.services.fbi.blueprint_route_registry import (
    DETERMINISTIC_PATCH_OPS,
    INTENT_OPERATIONS,
    LLM_CREATIVE_OPS,
)


class BlueprintProtocolValidator:
    """Validate that FBI repair intent is compilable into bounded tool work."""

    # Phase U-C: 共享白名单——单一事实来源在 blueprint_route_registry。
    _DETERMINISTIC_PATCH_OPS = DETERMINISTIC_PATCH_OPS
    _INTENT_OPERATIONS = INTENT_OPERATIONS
    # 附录4问题16修复：LLM 创作型 operation 白名单
    _LLM_CREATIVE_OPS = LLM_CREATIVE_OPS
    _TEXT_REPLACEMENT_OPS = {
        "replace_span",
        "replace_literal",
        "rewrite_window",
    }
    _INSERT_OPS = {
        "insert_anchor",
        "insert_before_anchor",
        "insert_after_anchor",
        "insert_transition_anchor",
        "insert_time_anchor",
        "insert_pressure_cost",
        "insert_conflict_beat",
        "insert_hook_beat",
        "insert_micro_payoff",
        "insert_functional_breathing_paragraph",
        "smooth_abrupt_shift",
    }
    _TARGET_SPAN_OPS = {
        "replace_span",
        "replace_literal",
        "delete_span",
        "normalize_structure_words",
        "replace_tier1_ai_flavor_terms",
        "cleanup_ai_flavor_window",
        "trim_discourse_window",
        "vary_sentence_shape",
        "vary_sentence_length_window",
        "rewrite_voice_window",
    }
    _WHOLE_SCENE_OR_POSTCHECK_OPS = {
        "normalize_punctuation",
        "ensure_required_spans",
        "suppress_false_positive",
        "mark_needs_human",
        "mark_unrepairable_by_tool",
        "merge_paragraphs",
    }

    # 英文系统诊断句模式：禁止写入正文
    _ENGLISH_SYSTEM_SENTENCE_PATTERNS = (
        "This beat makes the required state explicit",
        "This beat",
        "required state",
        "Scene goal is missing",
        "Scene goal",
        "must show",
        "ending state",
        "makes the required state",
    )

    def validate_blueprint(self, blueprint: RevisionBlueprint) -> dict[str, Any]:
        return {
            **self.validate_work_units(blueprint.work_units),
            "blueprint_id": blueprint.blueprint_id,
            "case_id": blueprint.case_id,
        }

    def validate_work_units(self, work_units: list[RepairWorkUnit]) -> dict[str, Any]:
        unit_results = [self.validate_work_unit(unit) for unit in work_units]
        failed = [item for item in unit_results if not item.get("accepted")]
        return {
            "accepted": not failed,
            "executor_path": "blueprint_protocol_validator",
            "work_units": len(work_units),
            "failed_work_units": len(failed),
            "invalid_work_unit_ids": [item["work_unit_id"] for item in failed],
            "unit_results": unit_results,
            "failures": sorted({
                failure
                for item in failed
                for failure in (item.get("failures") or ["blueprint_not_compilable"])
            }),
        }

    def validate_work_unit(self, unit: RepairWorkUnit) -> dict[str, Any]:
        """校验单个 work_unit（§7.5.4 BlueprintProtocolValidator）。

        强 schema（§7.5.1）：每个 work_unit 必须有非空 commands。
        空 operation_plan 直接拒绝为 blueprint_not_compilable。
        """
        failures: list[str] = []
        command_results: list[dict[str, Any]] = []

        if not unit.work_unit_id:
            failures.append("missing_work_unit_id")
        if not unit.target_scenes and unit.owner_scene is None:
            failures.append("missing_target_scene")
        if not unit.tool_batch.commands:
            # §7.5.1 强 schema + §H.8 WorkUnitBuilder 不得从 reason 猜命令
            # 空 operation_plan = 蓝图官只给意图没给 patch，必须拒绝
            failures.append("empty_operation_plan")
            failures.append("blueprint_not_compilable")

        for command in unit.tool_batch.commands:
            result = self.validate_command(command, unit)
            command_results.append(result)
            if not result.get("accepted"):
                failures.extend(result.get("failures") or ["command_not_compilable"])

        failures = sorted(set(failures))
        return {
            "accepted": not failures,
            "work_unit_id": unit.work_unit_id,
            "local_id": unit.local_id,
            "failures": failures,
            "command_results": command_results,
            "executor_path": "blueprint_protocol_validator",
        }

    def validate_command(self, command: ToolCommand, unit: RepairWorkUnit) -> dict[str, Any]:
        """校验单个 command（§7.5.4）。

        强 schema：operation 必须在确定性 allowlist 中，
        且必须包含执行文本（old_text/new_text/anchor，根据 operation 类型）。
        """
        failures: list[str] = []
        operation = command.operation

        scene_index = command.scene_index
        if scene_index is None:
            scene_index = unit.owner_scene if unit.owner_scene is not None else (
                unit.target_scenes[0] if unit.target_scenes else None
            )
        if scene_index is None:
            failures.append("missing_target_scene")

        if operation not in self._DETERMINISTIC_PATCH_OPS:
            # Phase U-C: 意图型 operation 必须先被 WorkUnitBuilder 编译为 exact patch。
            # 未知 operation 直接拒绝为 blueprint_not_compilable（§H.8）
            if operation in self._LLM_CREATIVE_OPS:
                # 附录4问题16修复：LLM 创作型 operation 走 SceneRepairer 路径，
                # 不需要编译为确定性 patch。使用专用失败码便于 executor 识别和路由。
                # work_unit 仍被标记为 invalid，由 has_llm_creative_fallback 逻辑保持 order pending。
                failures.append("llm_creative_operation_route_to_scene_repairer")
            elif operation in self._INTENT_OPERATIONS:
                failures.append("intent_operation_not_compiled_to_exact_patch")
            else:
                failures.append("abstract_repair_intent_not_executable")
            failures.append("blueprint_not_compilable")
            return {
                "accepted": False,
                "command_id": command.command_id,
                "operation": operation,
                "scene_index": scene_index,
                "failures": sorted(set(failures)),
            }

        if not command.source_issue_ids:
            failures.append("missing_source_issue_ids")
        if not command.expected_metric_delta:
            failures.append("missing_expected_metric_delta")
        if not command.guards:
            failures.append("missing_patch_guards")

        # replacement_language_gate：禁止英文系统诊断句写入正文
        failures.extend(self._language_gate_failures(command))

        if operation == "replace_exact":
            if not command.old_text:
                failures.append("missing_old_text")
            if not command.new_text:
                failures.append("missing_new_text")
            if command.old_text and command.new_text and command.old_text == command.new_text:
                failures.append("no_effective_patch_text")
            if not (command.anchor_text or command.old_text):
                failures.append("missing_anchor")
            failures.extend(self._locator_failures(command, command.old_text))
        elif operation == "delete_exact":
            if not command.old_text:
                failures.append("missing_old_text")
            if not (command.anchor_text or command.old_text):
                failures.append("missing_anchor")
            failures.extend(self._locator_failures(command, command.old_text))
        elif operation in {"insert_before_anchor", "insert_after_anchor"}:
            if not command.new_text:
                failures.append("missing_insert_text")
            if not (command.anchor_text or command.before_span or command.after_span or command.target_span):
                failures.append("missing_anchor")

        # 通用：如果有缺字段失败，标记 blueprint_not_compilable（§7.5.4）
        if failures:
            compilable_failures = {
                "missing_old_text", "missing_new_text", "missing_insert_text",
                "missing_anchor", "no_effective_patch_text",
                "insufficient_precise_locator", "incomplete_span_locator",
            }
            if set(failures) & compilable_failures:
                failures.append("blueprint_not_compilable")

        return {
            "accepted": not failures,
            "command_id": command.command_id,
            "operation": operation,
            "scene_index": scene_index,
            "failures": sorted(set(failures)),
        }

    @staticmethod
    def _locator_failures(command: ToolCommand, old_text: str) -> list[str]:
        failures: list[str] = []
        if command.span_start is None and command.span_end is not None:
            failures.append("incomplete_span_locator")
        if command.span_start is not None and command.span_end is None:
            failures.append("incomplete_span_locator")
        if command.replace_all:
            return failures
        locator_present = (
            command.span_start is not None
            and command.span_end is not None
        ) or bool(
            command.before_context
            or command.after_context
            or command.paragraph_index is not None
            or int(command.anchor_occurrence or 1) > 1
        )
        if locator_present:
            return failures
        if (command.guards or {}).get("allow_short_unique_search"):
            return failures
        text = str(old_text or "").strip()
        boundary_safe = (
            len(text) >= 8
            or (len(text) >= 6 and any("\u4e00" <= char <= "\u9fff" for char in text))
            or (len(text) >= 6 and any(mark in text for mark in "，。！？；：,.!?;:\n"))
        )
        if text and not boundary_safe:
            failures.append("insufficient_precise_locator")
        return failures

    def _language_gate_failures(self, command: ToolCommand) -> list[str]:
        """replacement_language_gate：禁止英文系统诊断句写入正文。"""
        failures: list[str] = []
        # 检查 new_text 和 replacement 两个可能的输出字段
        for field_name in ("new_text", "replacement"):
            text = getattr(command, field_name, "") or ""
            if not text:
                continue
            for pattern in self._ENGLISH_SYSTEM_SENTENCE_PATTERNS:
                if pattern in text:
                    failures.append("replacement_contains_system_language")
                    return failures
        return []
