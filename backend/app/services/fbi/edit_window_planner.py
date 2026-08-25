"""Edit-window planning for frozen-base FBI repair.

This module is deliberately deterministic. It groups already diagnosed,
blueprinted work units into frozen-base edit windows and merges same-window
commands when they would otherwise fight over the same anchor.
"""
from __future__ import annotations

import hashlib
import logging
import re
from collections import defaultdict
from typing import Any

from app.models.chapter_review import EditWindow, RepairWorkUnit, ToolCommand, ToolCommandBatch


_logger = logging.getLogger(__name__)

_INSERT_OPS = {"insert_before_anchor", "insert_after_anchor"}


def _hash(value: str) -> str:
    return hashlib.md5((value or "").encode("utf-8")).hexdigest()[:12]


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value or "")


class EditWindowPlanner:
    """Group work units by frozen-base edit windows."""

    def plan_work_units(
        self,
        work_units: list[RepairWorkUnit],
        scene_texts: dict[int, str],
        *,
        case_id: str = "",
    ) -> tuple[list[RepairWorkUnit], dict[str, Any]]:
        groups: dict[str, list[RepairWorkUnit]] = defaultdict(list)
        windows: dict[str, EditWindow] = {}
        for unit in work_units:
            window = self.window_for_unit(unit, scene_texts, case_id=case_id)
            groups[window.window_id].append(unit)
            windows[window.window_id] = window

        planned: list[RepairWorkUnit] = []
        trace: list[dict[str, Any]] = []
        compound_windows = 0
        single_issue_windows = 0
        family_split_windows = 0
        for window_id, items in groups.items():
            # 9.2.3: 按 family 兼容性拆分同窗 unit，不兼容的 family 不进入同一 compound window
            # Creative work is an atomic semantic rewrite.  If any unit in the
            # physical window is creative, the whole window must be handled by
            # one creative unit rather than splitting out deterministic peers.
            sub_batches = (
                [items]
                if any(self._is_creative_unit(unit) for unit in items)
                else self._split_by_family_compatibility(items)
            )
            if len(sub_batches) > 1:
                family_split_windows += 1
            for sub_idx, sub_items in enumerate(sub_batches):
                sub_window_id = window_id
                if len(sub_batches) > 1:
                    sub_window_id = f"{window_id}:sub:{sub_idx}"
                if len(sub_items) == 1:
                    unit = sub_items[0]
                    self._attach_window(unit, windows[window_id])
                    if len(sub_batches) > 1:
                        unit.edit_window_id = sub_window_id
                    planned.append(unit)
                    single_issue_windows += 1
                    trace.append({
                        "window_id": sub_window_id,
                        "work_unit_ids": [unit.work_unit_id],
                        "mode": "single",
                        "commands": len(unit.tool_batch.commands),
                    })
                    continue
                merged = self._merge_same_window_units(sub_window_id, windows[window_id], sub_items, case_id=case_id)
                planned.append(merged)
                compound_windows += 1
                trace.append({
                    "window_id": sub_window_id,
                    "work_unit_ids": [unit.work_unit_id for unit in sub_items],
                    "mode": "compound",
                    "commands": len(merged.tool_batch.commands),
                    "source_issue_ids": merged.source_violation_ids,
                })

        # 9.2.2: 安全网检查——确保同窗没有遗留的可重叠 command
        planned = self._enforce_single_patch_per_window(planned)

        return planned, {
            "component": "edit_window_planner",
            "input_work_units": len(work_units),
            "windows": len(groups),
            "compound_windows": compound_windows,
            "single_issue_windows": single_issue_windows,
            "family_split_windows": family_split_windows,
            "trace": trace,
        }

    def window_for_unit(
        self,
        unit: RepairWorkUnit,
        scene_texts: dict[int, str],
        *,
        case_id: str = "",
    ) -> EditWindow:
        scene_index = unit.owner_scene
        if scene_index is None and unit.target_scenes:
            scene_index = unit.target_scenes[0]
        text = scene_texts.get(scene_index, "") if scene_index is not None else ""
        base_hash = _hash(text)
        primary = self._primary_command(unit)
        declared_anchor = unit.write_anchor if isinstance(unit.write_anchor, dict) else {}
        validated_anchor = self._validated_anchor(
            declared_anchor, text, scene_index
        )
        anchor = str(validated_anchor.get("text") or "")
        if validated_anchor:
            unit.write_anchor = validated_anchor
            unit.placement_status = "resolved"
        elif declared_anchor:
            unit.write_anchor = {}
            unit.placement_status = "required"
        if not anchor:
            candidate_anchor = (
                self._command_anchor(primary)
                if primary
                else str(unit.edit_window.get("anchor") or "")
            )
            # A blueprint candidate becomes a physical write anchor only after
            # the frozen scene proves it is uniquely locatable.  Diagnostic
            # evidence and generated fallback windows never enter this branch
            # merely because they were attached to the finding.
            if candidate_anchor and text and text.count(candidate_anchor) == 1:
                anchor = candidate_anchor
                start = text.find(anchor)
                unit.write_anchor = {
                    "scene_index": scene_index,
                    "text": anchor,
                    "start": start,
                    "end": start + len(anchor),
                    "validated": True,
                    "kind": "blueprint_write_anchor",
                }
                unit.placement_status = "resolved"
        old_text = (primary.old_text if primary else "") or ""
        before_context = primary.before_context if primary else ""
        after_context = primary.after_context if primary else ""
        # Unvalidated old_text/evidence may be used by ToolExecutor as a patch
        # precondition, but it is never a physical grouping key.
        paragraph_index, start, end = self._locate_window(
            text, anchor, primary if anchor else None
        )
        if paragraph_index is not None:
            key = f"{case_id}:scene:{scene_index}:paragraph:{paragraph_index}"
        elif anchor:
            key = f"{case_id}:scene:{scene_index}:anchor:{_hash(_compact(anchor)[:120])}"
        else:
            key = f"{case_id}:scene:{scene_index}:unit:{unit.work_unit_id}"
        return EditWindow(
            window_id=_hash(key),
            scene_index=scene_index,
            base_text_hash=base_hash,
            issue_ids=list(unit.source_violation_ids or []),
            issue_families=self._issue_families(unit),
            paragraph_index=paragraph_index,
            old_text=old_text,
            before_context=before_context,
            after_context=after_context,
            anchor_text=anchor,
            window_start=start,
            window_end=end,
            merge_policy="compound_patch",
            risk_level="normal",
        )

    @staticmethod
    def _primary_command(unit: RepairWorkUnit) -> ToolCommand | None:
        return unit.tool_batch.commands[0] if unit.tool_batch and unit.tool_batch.commands else None

    @staticmethod
    def _is_creative_unit(unit: RepairWorkUnit) -> bool:
        return any(
            command.operation == "llm_creative_rewrite"
            for command in (unit.tool_batch.commands or [])
        )

    @staticmethod
    def _validated_anchor(
        anchor: dict[str, Any],
        scene_text: str,
        scene_index: int | None,
    ) -> dict[str, Any]:
        if not isinstance(anchor, dict) or anchor.get("validated") is not True:
            return {}
        anchor_scene = anchor.get("scene_index", scene_index)
        if not isinstance(anchor_scene, int) or anchor_scene != scene_index:
            return {}
        text = str(anchor.get("text") or "").strip()
        if not text or not scene_text or scene_text.count(text) != 1:
            return {}
        start = scene_text.find(text)
        if isinstance(anchor.get("start"), int) and anchor["start"] != start:
            return {}
        if isinstance(anchor.get("end"), int) and anchor["end"] != start + len(text):
            return {}
        return {
            **anchor,
            "scene_index": anchor_scene,
            "text": text,
            "start": start,
            "end": start + len(text),
            "validated": True,
        }

    @staticmethod
    def _command_anchor(command: ToolCommand | None) -> str:
        if command is None:
            return ""
        return (
            command.anchor_text
            or command.old_text
            or command.target_span
            or command.after_span
            or command.before_span
            or command.window_start
            or ""
        )

    @staticmethod
    def _issue_families(unit: RepairWorkUnit) -> list[str]:
        families: list[str] = []
        for command in unit.tool_batch.commands:
            # 附录4问题14修复：expected_metric_delta 已统一为 {metric, operator, target_delta}
            # schema，不再承载 repair_family。repair_family 只从 command.repair_family 读取。
            family = command.repair_family or ""
            if family and family not in families:
                families.append(str(family))
        if unit.blueprint_source and unit.blueprint_source not in families:
            families.append(unit.blueprint_source)
        return families

    @staticmethod
    def _locate_window(
        text: str,
        anchor: str,
        command: ToolCommand | None,
    ) -> tuple[int | None, int | None, int | None]:
        if command and command.paragraph_index is not None:
            return command.paragraph_index, None, None
        if not text or not anchor:
            return None, None, None
        index = text.find(anchor)
        if index < 0:
            compact_text = _compact(text)
            compact_anchor = _compact(anchor)
            if compact_anchor and compact_anchor in compact_text:
                return None, None, None
            return None, None, None
        paragraph_index = 0
        cursor = 0
        for match in re.finditer(r"\n\s*\n+", text):
            if match.start() >= index:
                break
            paragraph_index += 1
            cursor = match.end()
        next_break = re.search(r"\n\s*\n+", text[index:])
        end = len(text) if next_break is None else index + next_break.start()
        return paragraph_index, cursor, end

    def _merge_same_window_units(
        self,
        window_id: str,
        window: EditWindow,
        units: list[RepairWorkUnit],
        *,
        case_id: str = "",
    ) -> RepairWorkUnit:
        commands = [command for unit in units for command in unit.tool_batch.commands]
        creative_commands = [
            command
            for command in commands
            if command.operation == "llm_creative_rewrite"
        ]
        if creative_commands:
            # A deterministic command may provide evidence, but it may never
            # replace or claim coverage for a creative obligation.  SceneRepairer
            # receives every merged RepairGoal through the canonical order.
            creative = creative_commands[0].model_copy(deep=True)
            creative.command_id = f"creative_{_hash(window_id)}"
            creative.target_span = ""
            creative.old_text = ""
            creative.new_text = ""
            creative.replacement = ""
            creative.anchor_text = window.anchor_text
            creative.rationale = "; ".join(
                command.rationale for command in commands if command.rationale
            )[:500]
            merged_commands = [creative]
        else:
            # 9.2.1: 先尝试 compound replace 合并（同窗多 issue -> 单 replacement）
            compound_cmd = self._build_compound_replace_command(commands)
            if compound_cmd is not None:
                merged_commands = [compound_cmd]
            else:
                merged_commands = self._merge_commands(commands)
        source_order_ids = self._dedupe([
            order_id for unit in units for order_id in unit.source_order_ids
        ])
        source_issue_ids = self._dedupe([
            issue_id for unit in units for issue_id in unit.source_violation_ids
        ])
        target_scenes = self._dedupe_int([
            scene for unit in units for scene in unit.target_scenes
        ])
        owner_scene = window.scene_index
        work_unit_id = _hash(f"{case_id}:compound:{window_id}:{','.join(source_issue_ids)}")
        for index, command in enumerate(merged_commands):
            command.source_issue_ids = self._dedupe([*command.source_issue_ids, *source_issue_ids])
            if not command.command_id:
                command.command_id = f"{work_unit_id}:cmd:{index}"
            if command.scene_index is None:
                command.scene_index = owner_scene
        merged = RepairWorkUnit(
            work_unit_id=work_unit_id,
            local_id=f"compound_{window_id}",
            owner_scene=owner_scene,
            target_scenes=target_scenes or ([] if owner_scene is None else [owner_scene]),
            source_order_ids=source_order_ids,
            source_violation_ids=source_issue_ids,
            source_goal_ids=self._dedupe([
                goal_id for unit in units for goal_id in unit.source_goal_ids
            ]),
            read_context_spans=[
                span for unit in units for span in unit.read_context_spans
            ],
            diagnostic_evidence_spans=[
                span for unit in units for span in unit.diagnostic_evidence_spans
            ],
            write_anchor={
                "scene_index": owner_scene,
                "text": window.anchor_text,
                "start": window.window_start,
                "end": window.window_end,
                "validated": bool(window.anchor_text),
                "kind": "compound_write_anchor",
            },
            placement_status="resolved" if window.anchor_text else "required",
            edit_window=window.model_dump(),
            edit_window_id=window_id,
            base_scene_hash=window.base_text_hash,
            compound_issue_ids=source_issue_ids,
            compound_issue_families=self._dedupe([
                family for unit in units for family in (unit.compound_issue_families or self._issue_families(unit))
            ]),
            merge_policy="compound_patch",
            expected_metric_deltas=[
                delta
                for command in merged_commands
                if (delta := command.expected_metric_delta)
            ],
            protection_boundary=self._merge_dicts([unit.protection_boundary for unit in units]),
            max_delta_chars=min(
                (
                    unit.max_delta_chars
                    for unit in units
                    if isinstance(unit.max_delta_chars, int)
                    and unit.max_delta_chars > 0
                ),
                default=None,
            ),
            blueprint_source="review_minister_compound",
            issue_summary="; ".join(item.issue_summary for item in units if item.issue_summary)[:500],
            tool_batch=ToolCommandBatch(
                batch_id=f"{work_unit_id}:batch",
                source_blueprint_id="review_minister_compound",
                source_work_unit_id=work_unit_id,
                commands=merged_commands,
                target_scenes=target_scenes or ([] if owner_scene is None else [owner_scene]),
                source_order_ids=source_order_ids,
                acceptance_criteria=[
                    criterion
                    for unit in units
                    for criterion in (unit.tool_batch.acceptance_criteria or [])
                ],
            ),
            dependencies=self._dedupe([
                dep for unit in units for dep in unit.dependencies
            ]),
            validator_snapshot=units[0].validator_snapshot if units else {},
        )
        return merged

    def _enforce_single_patch_per_window(
        self, units: list[RepairWorkUnit]
    ) -> list[RepairWorkUnit]:
        """9.2.2: 安全网——确保同窗没有遗留的可重叠 command。

        真正的合并在 _merge_same_window_units 中完成。
        这里只做日志告警，不强制修改，避免破坏已规划的命令。
        """
        for unit in units:
            cmds = unit.tool_batch.commands
            if len(cmds) > 1 and self._commands_overlap(cmds):
                _logger.warning(
                    "EditWindowPlanner: window %s still has %d overlapping commands after merge",
                    unit.edit_window_id,
                    len(cmds),
                )
        return units

    @staticmethod
    def _can_batch_families(
        families_a: list[str], families_b: list[str], policy_map: dict[str, dict]
    ) -> bool:
        """9.2.3: 检查两组 family 是否可以同窗（双向检查，基于 dependency_policy.can_batch_with）。

        policy_map: {family: {"can_batch_with": [...], ...}}
        双向检查：任一方向的 policy 不允许，返回 False。
        """
        def _one_way(a: list[str], b: list[str]) -> bool:
            for fa in a:
                policy = policy_map.get(fa)
                if policy is None:
                    continue
                allowed = set(policy.get("can_batch_with", []))
                if not allowed:
                    continue
                for fb in b:
                    if fa != fb and fb not in allowed:
                        return False
            return True
        return _one_way(families_a, families_b) and _one_way(families_b, families_a)

    @staticmethod
    def _family_policy_map() -> dict[str, dict]:
        """9.2.3: 返回 family -> policy 映射，与 ReviewMinister._dependency_policy 一致。

        未列出的 family（rhythm/pov/voice/show_evidence/system/general）使用默认策略
        （can_batch_with 为空，表示不限制同窗）。
        """
        anti_ai_allowed = ["anti_ai_punctuation", "anti_ai_local", "anti_ai_discourse", "rhythm", "structure"]
        return {
            "fact": {"can_batch_with": ["fact", "structure"]},
            "anti_ai_punctuation": {"can_batch_with": anti_ai_allowed},
            "anti_ai_local": {"can_batch_with": anti_ai_allowed},
            "anti_ai_discourse": {"can_batch_with": anti_ai_allowed},
            "structure": {"can_batch_with": ["structure", "fact", "anti_ai_punctuation", "anti_ai_local", "anti_ai_discourse"]},
        }

    def _split_by_family_compatibility(
        self, units: list[RepairWorkUnit]
    ) -> list[list[RepairWorkUnit]]:
        """9.2.3: 按 family 兼容性把同窗 unit 拆分为多个子组。

        贪心分组：每个子组内的 family 互相兼容（双向 _can_batch_families 通过）。
        不兼容的 unit 被分到不同子组，各自独立处理（不 compound 合并）。
        """
        if len(units) <= 1:
            return [units]
        policy_map = self._family_policy_map()
        batches: list[list[RepairWorkUnit]] = []
        batch_families_cache: list[list[str]] = []
        for unit in units:
            unit_families = unit.compound_issue_families or self._issue_families(unit)
            placed = False
            for idx, batch in enumerate(batches):
                batch_families = batch_families_cache[idx]
                if self._can_batch_families(unit_families, batch_families, policy_map):
                    batch.append(unit)
                    batch_families_cache[idx] = self._dedupe([*batch_families, *unit_families])
                    placed = True
                    break
            if not placed:
                batches.append([unit])
                batch_families_cache.append(list(unit_families))
        return batches

    @staticmethod
    def _commands_overlap(commands: list[ToolCommand]) -> bool:
        """检查命令列表中是否有 span 重叠（基于 span_start/span_end）。"""
        spans: list[tuple[int, int]] = []
        for cmd in commands:
            if cmd.span_start is not None and cmd.span_end is not None:
                spans.append((cmd.span_start, cmd.span_end))
        if len(spans) < 2:
            return False
        spans.sort()
        for i in range(len(spans) - 1):
            if spans[i][1] > spans[i + 1][0]:
                return True
        return False

    def _build_compound_replace_command(
        self, commands: list[ToolCommand]
    ) -> ToolCommand | None:
        """9.2.1: 把同窗命令合并为一个 compound replace_exact。

        必须有 replace_exact 作为基础才能 compound。
        把同窗的 insert 命令吸收到 base_replace.new_text，
        把 delete 命令的目标从 new_text 中移除。
        无法合并时返回 None，让上层回退到 _merge_commands。
        """
        if len(commands) <= 1 or any(
            command.operation == "llm_creative_rewrite" for command in commands
        ):
            return None

        insert_cmds = [c for c in commands if c.operation in _INSERT_OPS]
        replace_cmds = [c for c in commands if c.operation == "replace_exact"]
        delete_cmds = [c for c in commands if c.operation == "delete_exact"]

        # 必须有 replace_exact 作为基础才能 compound
        if not replace_cmds:
            return None

        # 9.2.1: 多个 replace_exact 且 old_text 不同时不做 compound 合并，
        # 因为它们 targeting 不同的 span，强行合并会丢失其中一个的修复。
        # 交由 _merge_commands 保留为独立命令，ToolExecutor 顺序执行。
        distinct_old_texts = {c.old_text for c in replace_cmds if c.old_text}
        if len(distinct_old_texts) > 1:
            return None

        # 取覆盖范围最大的 replace_exact 作为基础
        base_replace = max(replace_cmds, key=lambda c: len(c.old_text or ""))

        # 把 insert 命令的 new_text 追加到 base_replace.new_text 末尾
        combined_new_text = base_replace.new_text or ""
        for insert_cmd in insert_cmds:
            insert_text = insert_cmd.new_text or insert_cmd.replacement or ""
            if insert_text and insert_text not in combined_new_text:
                combined_new_text += insert_text

        # 把 delete 命令的目标从 new_text 中移除
        for delete_cmd in delete_cmds:
            del_text = delete_cmd.old_text or delete_cmd.target_span or ""
            if del_text and del_text in combined_new_text:
                combined_new_text = combined_new_text.replace(del_text, "")

        if not combined_new_text.strip():
            return None

        # 构造 compound command（基于 base_replace 深拷贝）
        compound = base_replace.model_copy(deep=True)
        compound.command_id = f"compound_{base_replace.command_id}"
        compound.operation = "replace_exact"
        compound.new_text = combined_new_text
        compound.replacement = combined_new_text
        compound.source_issue_ids = self._dedupe([
            issue_id
            for cmd in [base_replace] + insert_cmds + delete_cmds
            for issue_id in (cmd.source_issue_ids or [])
        ])
        compound.postconditions = self._merge_postconditions(
            [base_replace] + insert_cmds + delete_cmds
        )
        return compound

    @staticmethod
    def _merge_postconditions(commands: list[ToolCommand]) -> dict[str, Any]:
        """合并多个 command 的 postconditions 字典。"""
        required_spans: list[str] = []
        forbidden_spans: list[str] = []
        for cmd in commands:
            post = cmd.postconditions or {}
            required_spans.extend(post.get("required_spans") or [])
            forbidden_spans.extend(post.get("forbidden_spans") or [])
        return {
            "required_spans": list(dict.fromkeys(required_spans)),
            "forbidden_spans": list(dict.fromkeys(forbidden_spans)),
        }

    def _merge_commands(self, commands: list[ToolCommand]) -> list[ToolCommand]:
        if not commands:
            return []
        insert_groups: dict[tuple[str, str, int | None], list[ToolCommand]] = defaultdict(list)
        passthrough: list[ToolCommand] = []
        for command in commands:
            if command.operation in _INSERT_OPS:
                anchor = self._command_anchor(command)
                insert_groups[(command.operation, anchor, command.scene_index)].append(command)
            else:
                passthrough.append(command)
        merged: list[ToolCommand] = []
        for (operation, anchor, scene_index), group in insert_groups.items():
            if len(group) == 1:
                merged.append(group[0])
                continue
            insert_texts = [
                (command.new_text or command.replacement or "").strip()
                for command in group
                if (command.new_text or command.replacement or "").strip()
            ]
            base = group[0].model_copy(deep=True)
            joined = "\n\n".join(self._dedupe(insert_texts))
            base.operation = operation
            base.anchor_text = anchor
            base.after_span = anchor if operation == "insert_after_anchor" else ""
            base.before_span = anchor if operation == "insert_before_anchor" else ""
            base.old_text = ""
            base.new_text = ("\n\n" + joined) if joined and not joined.startswith("\n") else joined
            base.replacement = base.new_text
            base.scene_index = scene_index
            base.command_id = _hash(f"compound:{operation}:{anchor}:{joined}")
            base.source_issue_ids = self._dedupe([
                issue_id for command in group for issue_id in command.source_issue_ids
            ])
            base.rationale = "; ".join(command.rationale for command in group if command.rationale)[:500]
            required = []
            for command in group:
                required.extend(command.postconditions.get("required_spans") or [])
            if joined:
                required.append(joined[:40])
            base.postconditions["required_spans"] = self._dedupe([str(item) for item in required if str(item)])
            merged.append(base)
        return [*passthrough, *merged]

    @staticmethod
    def _attach_window(unit: RepairWorkUnit, window: EditWindow) -> None:
        unit.edit_window = {**(unit.edit_window or {}), **window.model_dump()}
        unit.edit_window_id = unit.edit_window_id or window.window_id
        unit.base_scene_hash = unit.base_scene_hash or window.base_text_hash
        unit.compound_issue_ids = unit.compound_issue_ids or list(unit.source_violation_ids or [])
        unit.compound_issue_families = unit.compound_issue_families or EditWindowPlanner._issue_families(unit)
        unit.merge_policy = unit.merge_policy or "single_patch"
        if window.anchor_text:
            unit.write_anchor = {
                "scene_index": window.scene_index,
                "text": window.anchor_text,
                "start": window.window_start,
                "end": window.window_end,
                "validated": True,
                "kind": "planned_write_anchor",
            }
            unit.placement_status = "resolved"

    @staticmethod
    def _dedupe(values: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for value in values:
            value = str(value or "")
            if not value or value in seen:
                continue
            seen.add(value)
            out.append(value)
        return out

    @staticmethod
    def _dedupe_int(values: list[int]) -> list[int]:
        out: list[int] = []
        for value in values:
            if isinstance(value, int) and value not in out:
                out.append(value)
        return sorted(out)

    @staticmethod
    def _merge_dicts(values: list[dict]) -> dict:
        merged: dict[str, Any] = {}
        for value in values:
            if not isinstance(value, dict):
                continue
            for key, item in value.items():
                if isinstance(item, list):
                    current = merged.get(key) if isinstance(merged.get(key), list) else []
                    seen = {repr(existing) for existing in current}
                    combined = list(current)
                    for entry in item:
                        marker = repr(entry)
                        if marker not in seen:
                            seen.add(marker)
                            combined.append(entry)
                    merged[key] = combined
                elif isinstance(item, bool):
                    merged[key] = bool(merged.get(key, False) or item)
                elif key == "max_delta_chars" and isinstance(item, int) and item > 0:
                    current = merged.get(key)
                    merged[key] = min(current, item) if isinstance(current, int) else item
                elif key not in merged and item not in (None, ""):
                    merged[key] = item
        return merged
