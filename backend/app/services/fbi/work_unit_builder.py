"""Build deterministic FBI work units from existing repair orders."""
from __future__ import annotations

import hashlib
import re
from typing import Any

from app.models.chapter_review import (
    ChapterRepairOrder,
    RepairWorkUnit,
    RevisionBlueprint,
    ToolCommand,
    ToolCommandBatch,
)
from app.models.fbi_diagnosis import FBIReviewDiagnosis
from app.services.fbi.blueprint_route_registry import (
    DETERMINISTIC_PATCH_OPS,
    EXACT_OPERATION_ALIASES,
)
from app.services.fbi.tool_executor import ToolExecutor
from app.services.validator_protocol import ValidatorProtocolAdapter
from app.utils.dash_artifacts import DEFAULT_DASH_ARTIFACT_MAX_PER_1000


def build_revision_blueprint_from_diagnoses(
    case_id: str,
    diagnoses: list[FBIReviewDiagnosis],
    *,
    issue_to_order_ids: dict[str, list[str]] | None = None,
) -> RevisionBlueprint:
    """Build a native RevisionBlueprint directly from Review Minister diagnoses."""

    issue_to_order_ids = issue_to_order_ids or {}
    work_units: list[RepairWorkUnit] = []
    trace: list[dict[str, Any]] = []
    missing_command_count = 0
    # Phase U-D: covered issue 防护——被 compound work unit 覆盖的 issue
    # 不得再单独生成 work unit，防止同窗口多个独立 old_text patch。
    covered_issue_ids: set[str] = set()
    for index, diagnosis in enumerate(diagnoses):
        diagnosis_issues = {issue_id for issue_id in diagnosis.issue_ids if issue_id}
        # U-D: 跳过已被前一个 compound work unit 覆盖的 issue
        already_covered = diagnosis_issues & covered_issue_ids
        if already_covered and diagnosis_issues <= covered_issue_ids:
            trace.append({
                "diagnosis_id": diagnosis.diagnosis_id,
                "issue_ids": list(diagnosis.issue_ids),
                "status": "skipped_covered_by_compound_window",
                "reason": "issues_already_covered_by_compound_work_unit",
                "covered_by": sorted(already_covered),
            })
            continue
        commands = _commands_for_diagnosis(diagnosis)
        if not commands:
            missing_command_count += 1
            trace.append({
                "diagnosis_id": diagnosis.diagnosis_id,
                "issue_ids": list(diagnosis.issue_ids),
                "status": "blueprint_completion_required",
                "reason": "diagnosis_has_no_bounded_tool_command",
            })
            # U-D: 即使 blueprint incomplete，也要标记 issue 为已覆盖，
            # 防止后续单独生成 work unit 偷跑
            covered_issue_ids.update(diagnosis_issues)
            continue
        covered_issue_ids.update(diagnosis_issues)
        owner_scene = diagnosis.scene_index
        target_scenes = [owner_scene] if isinstance(owner_scene, int) else []
        source_order_ids = _dedupe([
            order_id
            for issue_id in diagnosis.issue_ids
            for order_id in issue_to_order_ids.get(issue_id, [])
        ])
        placement = getattr(diagnosis, "placement", None)
        placement_anchor = dict(getattr(placement, "write_anchor", {}) or {})
        anchor = str(placement_anchor.get("text") or "")
        if not anchor and getattr(placement, "status", "") == "resolved":
            anchor = _diagnosis_anchor(diagnosis, commands)
        elif not anchor and getattr(placement, "status", "") != "required":
            anchor = _diagnosis_anchor(diagnosis, commands)
        work_unit_id = _stable_id(case_id, "diag", index, diagnosis.diagnosis_id, anchor)
        for command_index, command in enumerate(commands):
            if not command.command_id:
                command.command_id = _stable_id(
                    work_unit_id,
                    command_index,
                    command.operation,
                    command.target_span,
                )
            if command.scene_index is None and owner_scene is not None:
                command.scene_index = owner_scene
        criteria = _diagnosis_acceptance_criteria(diagnosis, commands)
        work_units.append(RepairWorkUnit(
            work_unit_id=work_unit_id,
            local_id=diagnosis.diagnosis_id,
            owner_scene=owner_scene,
            target_scenes=target_scenes,
            source_order_ids=source_order_ids,
            source_violation_ids=list(diagnosis.issue_ids),
            source_goal_ids=list(getattr(diagnosis, "repair_goal_ids", []) or []),
            read_context_spans=list(getattr(placement, "read_context_spans", []) or []),
            diagnostic_evidence_spans=list(
                getattr(placement, "diagnostic_evidence_spans", []) or []
            ),
            write_anchor=placement_anchor,
            placement_status=str(getattr(placement, "status", "") or ""),
            edit_window={"anchor": anchor, "source": "review_minister_native"},
            blueprint_source="review_minister_native",
            issue_summary=diagnosis.problem_statement[:500],
            tool_batch=ToolCommandBatch(
                batch_id=f"{work_unit_id}:batch",
                source_blueprint_id="review_minister_native",
                source_work_unit_id=diagnosis.diagnosis_id,
                commands=commands,
                target_scenes=target_scenes,
                source_order_ids=source_order_ids,
                acceptance_criteria=criteria,
            ),
            dependencies=list(diagnosis.dependency_policy.depends_on or []),
        ))
        trace.append({
            "work_unit_id": work_unit_id,
            "diagnosis_id": diagnosis.diagnosis_id,
            "issue_ids": list(diagnosis.issue_ids),
            "source_order_ids": source_order_ids,
            "commands": [command.operation for command in commands],
            "anchor": anchor,
            "source": "review_minister_native",
        })

    status = "degraded" if work_units and missing_command_count else "ready" if work_units else "empty"
    snapshot = _validator_snapshot_for_units(work_units)
    blueprint = RevisionBlueprint(
        blueprint_id=_stable_id(case_id, "native_blueprint", len(work_units)),
        case_id=case_id,
        work_units=work_units,
        planning_trace=trace,
        validator_snapshot=snapshot,
        completion_summary=_completion_summary(work_units, expected_issue_count=len(diagnoses)),
        status=status,
    )
    # U-D: 在 validator_snapshot 中记录 covered issue 防护结果
    blueprint.validator_snapshot = {
        **(blueprint.validator_snapshot or {}),
        "covered_issue_ids": sorted(covered_issue_ids),
        "covered_issue_count": len(covered_issue_ids),
        "compound_window_guard": "enabled",
    }
    return blueprint


def merge_revision_blueprints(
    case_id: str,
    primary: RevisionBlueprint,
    fallback: RevisionBlueprint,
) -> RevisionBlueprint:
    """Prefer native work units and fill gaps from compatibility work units."""

    covered_orders = {
        order_id
        for unit in primary.work_units
        for order_id in unit.source_order_ids
        if order_id
    }
    covered_issues = {
        issue_id
        for unit in primary.work_units
        for issue_id in unit.source_violation_ids
        if issue_id
    }
    merged_units = list(primary.work_units)
    for unit in fallback.work_units:
        unit_orders = {order_id for order_id in unit.source_order_ids if order_id}
        unit_issues = {issue_id for issue_id in unit.source_violation_ids if issue_id}
        if unit_orders and unit_orders <= covered_orders:
            continue
        if unit_issues and unit_issues <= covered_issues:
            continue
        merged_units.append(unit)
        covered_orders.update(unit_orders)
        covered_issues.update(unit_issues)

    traces = [
        {"source_blueprint": "review_minister_native", **item}
        for item in (primary.planning_trace or [])
    ] + [
        {"source_blueprint": "order_compatibility", **item}
        for item in (fallback.planning_trace or [])
    ]
    degraded = (
        primary.status == "degraded"
        or fallback.status == "degraded"
        or len(merged_units) < max(len(primary.work_units), len(fallback.work_units))
    )
    status = "degraded" if merged_units and degraded else "ready" if merged_units else "empty"
    snapshot = _validator_snapshot_for_units(merged_units)
    return RevisionBlueprint(
        blueprint_id=_stable_id(case_id, "merged_blueprint", len(merged_units)),
        case_id=case_id,
        work_units=merged_units,
        planning_trace=traces,
        validator_snapshot=snapshot,
        completion_summary=_completion_summary(merged_units),
        status=status,
    )


def build_revision_blueprint(case_id: str, orders: list[ChapterRepairOrder]) -> RevisionBlueprint:
    """Translate structured repair intent into executable work units.

    This is a compatibility layer over the current ChapterRepairOrder model.
    It only emits work units when an order already carries concrete edit intent
    such as patch_plan, explicit tool_commands, fact_repair_goal, or dash cleanup
    metrics. Ambiguous orders remain on the legacy executor path.
    """

    buckets: dict[tuple[int | None, str], list[tuple[ChapterRepairOrder, list[ToolCommand], list[dict]]]] = {}
    trace: list[dict[str, Any]] = []
    legacy_required_count = 0
    for order in orders:
        commands, criteria = _commands_for_order(order)
        if not commands:
            legacy_required_count += 1
            trace.append({
                "order_id": order.order_id,
                "status": "legacy_executor_required",
                "reason": "no_structured_tool_commands",
            })
            continue
        owner_scene = order.owner_scene if order.owner_scene is not None else (
            order.target_scenes[0] if order.target_scenes else None
        )
        anchor = _edit_anchor(order, commands)
        buckets.setdefault((owner_scene, anchor), []).append((order, commands, criteria))

    work_units: list[RepairWorkUnit] = []
    for index, ((owner_scene, anchor), items) in enumerate(buckets.items()):
        source_orders = [order for order, _, _ in items]
        commands = [command for _, command_list, _ in items for command in command_list]
        criteria = [criterion for _, _, criteria_list in items for criterion in criteria_list]
        target_scenes = sorted({
            scene
            for order in source_orders
            for scene in order.target_scenes
            if isinstance(scene, int)
        })
        if not target_scenes and owner_scene is not None:
            target_scenes = [owner_scene]
        work_unit_id = _stable_id(case_id, index, owner_scene, anchor, [o.order_id for o in source_orders])
        for command_index, command in enumerate(commands):
            if not command.command_id:
                command.command_id = _stable_id(
                    work_unit_id,
                    command_index,
                    command.operation,
                    command.target_span,
                )
            if command.scene_index is None and owner_scene is not None:
                command.scene_index = owner_scene
        for order in source_orders:
            order.work_unit_id = work_unit_id
            order.tool_commands = [command.model_dump() for command in commands if command.source_issue_ids and (
                set(command.source_issue_ids) & set(order.source_violation_ids or [order.order_id])
            )] or [command.model_dump() for command in commands]

        work_units.append(RepairWorkUnit(
            work_unit_id=work_unit_id,
            local_id=f"compat_{index}",
            owner_scene=owner_scene,
            target_scenes=target_scenes,
            source_order_ids=[order.order_id for order in source_orders],
            source_violation_ids=[
                violation_id
                for order in source_orders
                for violation_id in order.source_violation_ids
            ],
            edit_window={"anchor": anchor},
            blueprint_source="order_compatibility",
            issue_summary="; ".join(order.reason for order in source_orders if order.reason)[:500],
            tool_batch=ToolCommandBatch(
                batch_id=f"{work_unit_id}:batch",
                source_blueprint_id="order_compatibility",
                source_work_unit_id=f"compat_{index}",
                commands=commands,
                target_scenes=target_scenes,
                source_order_ids=[order.order_id for order in source_orders],
                acceptance_criteria=criteria,
            ),
            dependencies=_dedupe([
                dependency
                for order in source_orders
                for dependency in order.dependencies
            ]),
        ))
        trace.append({
            "work_unit_id": work_unit_id,
            "source_order_ids": [order.order_id for order in source_orders],
            "commands": [command.operation for command in commands],
            "anchor": anchor,
        })

    order_to_unit = {
        order_id: unit.work_unit_id
        for unit in work_units
        for order_id in unit.source_order_ids
    }
    for unit in work_units:
        unit.dependencies = _dedupe([
            dependency_unit
            for dependency in unit.dependencies
            if (dependency_unit := order_to_unit.get(dependency, dependency)) != unit.work_unit_id
        ])

    status = "degraded" if work_units and legacy_required_count else "ready" if work_units else "empty"
    snapshot = _validator_snapshot_for_units(work_units)
    return RevisionBlueprint(
        blueprint_id=_stable_id(case_id, "blueprint", len(work_units)),
        case_id=case_id,
        work_units=work_units,
        planning_trace=trace,
        validator_snapshot=snapshot,
        completion_summary=_completion_summary(work_units, expected_issue_count=len(orders)),
        status=status,
    )


def _commands_for_diagnosis(diagnosis: FBIReviewDiagnosis) -> list[ToolCommand]:
    commands: list[ToolCommand] = []
    owner_scene = diagnosis.scene_index
    blueprint = getattr(diagnosis, "revision_blueprint", None)
    if blueprint is not None and getattr(blueprint, "status", "") == "ready":
        item = getattr(blueprint, "tool_blueprint", {}) or {}
        if isinstance(item, dict):
            command = _command_from_patch_item(
                item,
                command_id=f"{diagnosis.diagnosis_id}:blueprint",
                owner_scene=owner_scene,
                issue_ids=diagnosis.issue_ids,
                rationale=diagnosis.problem_statement,
            )
            if command is not None:
                commands.append(command)
    if commands:
        return _dedupe_commands(commands)
    for index, item in enumerate(diagnosis.repair_intent.patch_plan or []):
        if not isinstance(item, dict):
            continue
        command = _command_from_patch_item(
            item,
            command_id=f"{diagnosis.diagnosis_id}:patch:{index}",
            owner_scene=owner_scene,
            issue_ids=diagnosis.issue_ids,
            rationale=diagnosis.problem_statement,
        )
        if command is not None:
            commands.append(command)
    if not commands:
        for index, decision in enumerate(diagnosis.conflict_decisions or []):
            current = str(decision.current_claim or "").strip()
            authority = str(decision.authority_claim or "").strip()
            if not current or not authority or current == authority:
                continue
            command = _determinize_command(ToolCommand(
                command_id=f"{diagnosis.diagnosis_id}:conflict:{index}",
                operation="replace_span",
                scene_index=owner_scene,
                target_span=current,
                replacement=authority,
                source_issue_ids=list(diagnosis.issue_ids),
                postconditions={
                    "forbidden_spans": [current],
                    "required_spans": [authority],
                },
                rationale=decision.rationale or diagnosis.problem_statement,
            ))
            if command is not None:
                commands.append(command)
    if not commands and _diagnosis_requests_dash_cleanup(diagnosis):
        commands.append(ToolCommand(
            command_id=f"{diagnosis.diagnosis_id}:dash",
            operation="normalize_punctuation",
            scene_index=owner_scene,
            target_span="",
            source_issue_ids=list(diagnosis.issue_ids),
            expected_metric_delta={"metric": "dash_per_1000", "operator": "decrease", "target_delta": -1},
            guards={"preserve_facts": True, "preserve_scene_contract": True},
            postconditions={"max_dash_per_1000": _diagnosis_dash_threshold(diagnosis)},
            rationale=diagnosis.problem_statement,
        ))
    return _dedupe_commands(commands)


def _command_from_patch_item(
    item: dict[str, Any],
    *,
    command_id: str,
    owner_scene: int | None,
    issue_ids: list[str],
    rationale: str,
) -> ToolCommand | None:
    operation = str(item.get("operation") or item.get("op") or "replace_phrase")
    # Phase U-C: exact operation 别名来自共享白名单，避免与 route registry 不一致。
    mapped = {
        **EXACT_OPERATION_ALIASES,
        "replace_phrase": "replace_span",
        "replace_conflicting_phrase": "replace_span",
        "replace_span": "replace_span",
        "replace_literal": "replace_literal",
        "delete_span": "delete_span",
        "insert_anchor": "insert_anchor",
        "insert_after": "insert_anchor",
        "insert_before": "insert_anchor",
        "insert_before_anchor": "insert_before_anchor",
        "insert_after_anchor": "insert_after_anchor",
        "insert_transition_anchor": "insert_transition_anchor",
        "insert_time_anchor": "insert_time_anchor",
        "insert_pressure_cost": "insert_pressure_cost",
        "insert_conflict_beat": "insert_conflict_beat",
        "insert_hook_beat": "insert_hook_beat",
        "insert_micro_payoff": "insert_micro_payoff",
        "rewrite_window": "rewrite_window",
        "normalize_punctuation": "normalize_punctuation",
        "normalize_structure_words": "normalize_structure_words",
        "replace_tier1_ai_flavor_terms": "replace_tier1_ai_flavor_terms",
        "cleanup_ai_flavor_window": "cleanup_ai_flavor_window",
        "trim_discourse_window": "trim_discourse_window",
        "vary_sentence_shape": "vary_sentence_shape",
        "vary_sentence_length_window": "vary_sentence_length_window",
        "insert_functional_breathing_paragraph": "insert_functional_breathing_paragraph",
        "smooth_abrupt_shift": "smooth_abrupt_shift",
        "rewrite_voice_window": "rewrite_voice_window",
        "ensure_required_spans": "ensure_required_spans",
        "split_paragraph": "split_paragraph",
        "merge_paragraphs": "merge_paragraphs",
    }.get(operation)
    if not mapped:
        return None
    # Phase U-C: route builder 产出的 exact operation 直接带 old_text/new_text，
    # 必须优先读取，避免把 replace_exact 误当抽象意图丢弃。
    old_text_field = str(item.get("old_text") or "")
    new_text_field = str(item.get("new_text") or "")
    target = str(item.get("target_span") or item.get("from") or item.get("old") or old_text_field or "")
    replacement = str(item.get("replacement") or item.get("to") or item.get("new") or item.get("target") or new_text_field or "")
    if replacement and _looks_like_abstract_recommendation(replacement):
        return None
    command = ToolCommand(
        command_id=command_id,
        operation=mapped,
        scene_index=owner_scene,
        target_span=target,
        replacement=replacement,
        old_text=old_text_field or target,
        new_text=new_text_field or replacement,
        anchor_text=str(item.get("anchor_text") or old_text_field or ""),
        before_span=str(item.get("before_span") or ""),
        after_span=str(item.get("after_span") or ""),
        window_start=str(item.get("window_start") or ""),
        window_end=str(item.get("window_end") or ""),
        span_start=item.get("span_start") if isinstance(item.get("span_start"), int) else None,
        span_end=item.get("span_end") if isinstance(item.get("span_end"), int) else None,
        before_context=str(item.get("before_context") or ""),
        after_context=str(item.get("after_context") or ""),
        anchor_occurrence=int(item.get("anchor_occurrence") or item.get("occurrence_index") or 1),
        paragraph_index=item.get("paragraph_index") if isinstance(item.get("paragraph_index"), int) else None,
        replace_all=bool(item.get("replace_all", False)),
        max_replacements=int(item.get("max_replacements") or 1),
        rationale=str(item.get("rationale") or rationale or ""),
        repair_family=str(item.get("repair_family") or ""),
        evidence=dict(item.get("evidence") or {}) if isinstance(item.get("evidence"), dict) else {},
        protection_policy=dict(item.get("protection_policy") or {}) if isinstance(item.get("protection_policy"), dict) else {},
        fallback=dict(item.get("fallback") or {}) if isinstance(item.get("fallback"), dict) else {},
        source_issue_ids=list(issue_ids),
        preconditions=dict(item.get("preconditions") or {}),
        postconditions=dict(item.get("postconditions") or {}),
    )
    if operation == "insert_before" and not command.before_span:
        command.before_span = target
        command.target_span = ""
    if operation == "insert_after" and not command.after_span:
        command.after_span = target
        command.target_span = ""
    if operation in {"insert_before_anchor", "insert_transition_anchor"} and target and not command.before_span:
        command.before_span = target
        command.target_span = ""
    if operation in {
        "insert_after_anchor",
        "insert_pressure_cost",
        "insert_conflict_beat",
        "insert_hook_beat",
        "insert_micro_payoff",
        "insert_time_anchor",
        "insert_functional_breathing_paragraph",
        "smooth_abrupt_shift",
    } and target and not command.after_span:
        command.after_span = target
        command.target_span = ""
    if command.operation in {"replace_span", "replace_literal"} and command.target_span and command.replacement:
        command.postconditions.setdefault("forbidden_spans", [command.target_span])
        command.postconditions.setdefault("required_spans", [command.replacement])
    if command.operation in {
        "insert_anchor",
        "insert_before_anchor",
        "insert_after_anchor",
        "insert_transition_anchor",
        "insert_pressure_cost",
        "insert_conflict_beat",
        "insert_hook_beat",
        "insert_micro_payoff",
        "insert_time_anchor",
        "insert_functional_breathing_paragraph",
        "smooth_abrupt_shift",
    } and command.replacement:
        required = list(command.postconditions.get("required_spans") or [])
        if command.replacement not in required:
            required.append(command.replacement)
        command.postconditions["required_spans"] = required
    return _determinize_command(command)


def _determinize_command(command: ToolCommand) -> ToolCommand | None:
    """Compile repair intent into a Phase-H exact patch command.

    The previous FBI chain allowed tool-like operations such as
    cleanup_ai_flavor_window to reach ToolExecutor. Phase H makes those
    operations blueprint-only intents: by this point every command must carry
    exact old/new text or an exact insertion anchor.
    """

    op = command.operation
    if op == "llm_creative_rewrite":
        # This is a control-plane command, not a deterministic ToolExecutor
        # operation.  ChapterRepairExecutor separates creative work units
        # before tool execution and routes their canonical order through the
        # bounded SceneRepairer lane.  Dropping the command here leaves a
        # visible work unit with no executable order and makes smart repair a
        # no-op for semantic bridges and other non-literal corrections.
        return command
    if op in DETERMINISTIC_PATCH_OPS:
        _fill_deterministic_defaults(command)
        return command

    if op in {"replace_span", "replace_literal", "rewrite_window"}:
        old = (command.old_text or command.target_span or "").strip()
        new = command.new_text or command.replacement
        if not old or not new or old == new:
            return None
        return _exact_command_from(
            command,
            operation="replace_exact",
            old_text=old,
            new_text=new,
            anchor_text=old,
        )

    if op == "delete_span":
        old = (command.old_text or command.target_span or "").strip()
        if not old:
            return None
        return _exact_command_from(
            command,
            operation="delete_exact",
            old_text=old,
            new_text="",
            anchor_text=old,
        )

    if op in {
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
    }:
        anchor = command.after_span or command.before_span or command.target_span or command.anchor_text
        new = command.new_text or command.replacement
        if not anchor or not new:
            return None
        operation = "insert_before_anchor" if command.before_span and not command.after_span else "insert_after_anchor"
        return _exact_command_from(
            command,
            operation=operation,
            old_text="",
            new_text=new,
            anchor_text=anchor,
        )

    transformed = _compile_transform_window(command)
    if transformed is not None:
        return transformed

    return None


def _compile_transform_window(command: ToolCommand) -> ToolCommand | None:
    target = (command.old_text or command.target_span or "").strip()
    if not target:
        return None

    probe = command.model_copy(deep=True)
    probe.target_span = target
    operation = command.operation
    if operation == "normalize_structure_words":
        updated, result = ToolExecutor._normalize_structure_words(probe, target)
    elif operation == "replace_tier1_ai_flavor_terms":
        updated, result = ToolExecutor._replace_tier1_ai_flavor_terms(probe, target)
    elif operation == "cleanup_ai_flavor_window":
        updated, result = ToolExecutor._cleanup_ai_flavor_window(probe, target)
    elif operation == "trim_discourse_window":
        updated, result = ToolExecutor._trim_discourse_window(probe, target)
    elif operation in {"vary_sentence_shape", "vary_sentence_length_window"}:
        updated, result = ToolExecutor._vary_sentence_shape(probe, target)
    elif operation == "rewrite_voice_window":
        updated, result = ToolExecutor._rewrite_voice_window(probe, target)
    elif operation == "split_paragraph":
        updated, result = ToolExecutor._split_paragraph(probe, target)
    else:
        return None

    if not result.get("accepted") or updated == target:
        return None
    return _exact_command_from(
        command,
        operation="replace_exact",
        old_text=target,
        new_text=updated,
        anchor_text=target,
    )


def _exact_command_from(
    command: ToolCommand,
    *,
    operation: str,
    old_text: str,
    new_text: str,
    anchor_text: str,
) -> ToolCommand:
    deterministic = command.model_copy(deep=True)
    deterministic.operation = operation
    deterministic.anchor_text = anchor_text
    deterministic.old_text = old_text
    deterministic.new_text = new_text
    deterministic.target_span = old_text or anchor_text
    deterministic.replacement = new_text
    deterministic.before_span = anchor_text if operation == "insert_before_anchor" else ""
    deterministic.after_span = anchor_text if operation == "insert_after_anchor" else ""
    _fill_deterministic_defaults(deterministic)
    return deterministic


def _fill_deterministic_defaults(command: ToolCommand) -> None:
    if command.operation == "replace_exact":
        command.old_text = command.old_text or command.target_span
        command.new_text = command.new_text or command.replacement
        command.anchor_text = command.anchor_text or command.old_text
        command.target_span = command.target_span or command.old_text
        command.replacement = command.replacement or command.new_text
        if command.old_text and command.old_text not in command.new_text:
            command.postconditions.setdefault("forbidden_spans", [command.old_text])
        if command.new_text:
            command.postconditions.setdefault("required_spans", [command.new_text])
    elif command.operation == "delete_exact":
        command.old_text = command.old_text or command.target_span
        command.anchor_text = command.anchor_text or command.old_text
        command.target_span = command.target_span or command.old_text
        if command.old_text:
            command.postconditions.setdefault("forbidden_spans", [command.old_text])
    elif command.operation in {"insert_before_anchor", "insert_after_anchor"}:
        command.new_text = command.new_text or command.replacement
        command.anchor_text = command.anchor_text or command.after_span or command.before_span or command.target_span
        command.replacement = command.replacement or command.new_text
        if command.operation == "insert_before_anchor":
            command.before_span = command.before_span or command.anchor_text
        else:
            command.after_span = command.after_span or command.anchor_text
        if command.new_text:
            required = list(command.postconditions.get("required_spans") or [])
            if command.new_text not in required:
                required.append(command.new_text)
            command.postconditions["required_spans"] = required

    if not command.expected_metric_delta:
        # 附录4问题14修复：统一 expected_metric_delta schema 为
        # {"metric", "operator", "target_delta"}，与 retry_blueprint_runtime /
        # review_blueprint_agent 保持一致。repair_family 已存在于 command.repair_family
        # 字段，不再重复放入 expected_metric_delta。
        command.expected_metric_delta = {
            "metric": command.repair_family or "unknown",
            "operator": "decrease",
            "target_delta": -1,
        }
    if not command.guards:
        command.guards = {
            "preserve_facts": True,
            "preserve_scene_contract": True,
            "preserve_pov": True,
            "forbid_unrelated_edits": True,
        }


def _diagnosis_acceptance_criteria(
    diagnosis: FBIReviewDiagnosis,
    commands: list[ToolCommand],
) -> list[dict]:
    criteria: list[dict] = []
    for command in commands:
        if command.postconditions:
            criteria.append({**command.postconditions, "scene_index": command.scene_index})
    for criterion in diagnosis.acceptance_criteria or []:
        metric = str(criterion.metric or "").lower()
        if "dash" in metric and isinstance(criterion.expected, (int, float)):
            criteria.append({
                "scene_index": diagnosis.scene_index,
                "max_dash_per_1000": float(criterion.expected),
            })
    return criteria


def _diagnosis_anchor(
    diagnosis: FBIReviewDiagnosis,
    commands: list[ToolCommand],
) -> str:
    for command in commands:
        for value in (
            command.anchor_text,
            command.old_text,
            command.target_span,
            command.before_span,
            command.after_span,
            command.window_start,
        ):
            if value:
                return value[:80]
    for decision in diagnosis.conflict_decisions or []:
        if decision.current_claim:
            return decision.current_claim[:80]
    return diagnosis.issue_family or diagnosis.diagnosis_id


def _diagnosis_requests_dash_cleanup(diagnosis: FBIReviewDiagnosis) -> bool:
    values = [
        diagnosis.issue_family,
        diagnosis.problem_statement,
        diagnosis.repair_intent.repair_lane,
        diagnosis.repair_intent.preferred_operation,
    ]
    values.extend(str(metric.get("metric") or "") for metric in diagnosis.repair_intent.target_metrics or [])
    return any("dash" in str(value).lower() or "破折号" in str(value) for value in values)


def _diagnosis_dash_threshold(diagnosis: FBIReviewDiagnosis) -> float:
    for metric in diagnosis.repair_intent.target_metrics or []:
        for key in ("expected_max", "max", "expected"):
            value = metric.get(key)
            if isinstance(value, (int, float)):
                return float(value)
    return DEFAULT_DASH_ARTIFACT_MAX_PER_1000


def _commands_for_order(order: ChapterRepairOrder) -> tuple[list[ToolCommand], list[dict]]:
    explicit = _explicit_tool_commands(order)
    if explicit:
        return explicit, _acceptance_criteria_for_order(order)

    commands: list[ToolCommand] = []
    commands.extend(_patch_plan_commands(order))
    if not commands:
        commands.extend(_conflict_decision_commands(order))
    commands.extend(_fact_goal_commands(order))
    if not commands and _is_dash_cleanup_order(order) and str(order.repair_lane or "") == "deterministic_surface_cleanup":
        max_dash_count = _dash_max_count(order)
        dash_span = _dash_target_span(order)
        dash_postconditions = (
            {"max_dash_count": max_dash_count}
            if max_dash_count is not None
            else {"max_dash_per_1000": _dash_threshold(order)}
        )
        commands.append(ToolCommand(
            command_id=f"{order.order_id}:dash",
            operation="replace_exact",
            scene_index=_owner_scene(order),
            target_span=dash_span,
            old_text=dash_span,
            new_text="，",
            replacement="，",
            anchor_text=dash_span,
            replace_all=True,
            required_unique_anchor=False,
            source_issue_ids=order.source_violation_ids or [order.order_id],
            expected_metric_delta={"metric": "dash_per_1000", "operator": "decrease", "target_delta": -1},
            guards={"preserve_facts": True, "preserve_scene_contract": True},
            postconditions=dash_postconditions,
            rationale=order.reason,
        ))
    return _dedupe_commands(commands), _acceptance_criteria_for_order(order)


def _conflict_decision_commands(order: ChapterRepairOrder) -> list[ToolCommand]:
    minister = (order.repair_brief or {}).get("review_minister")
    if not isinstance(minister, dict):
        return []
    decisions = minister.get("conflict_decisions")
    if not isinstance(decisions, list):
        return []

    commands: list[ToolCommand] = []
    for index, decision in enumerate(decisions):
        if not isinstance(decision, dict):
            continue
        action = str(decision.get("decision") or "")
        if action not in {"replace_current_with_authority", "preserve_authority"}:
            continue
        current = str(decision.get("current_claim") or "").strip()
        authority = str(decision.get("authority_claim") or "").strip()
        if not current or not authority or current == authority:
            continue
        if _looks_like_abstract_recommendation(authority):
            continue
        command = _determinize_command(ToolCommand(
            command_id=f"{order.order_id}:conflict:{index}",
            operation="replace_span",
            scene_index=_owner_scene(order),
            target_span=current,
            replacement=authority,
            source_issue_ids=order.source_violation_ids or [order.order_id],
            postconditions={
                "forbidden_spans": [current],
                "required_spans": [authority],
            },
            rationale=str(decision.get("rationale") or order.reason or ""),
        ))
        if command is not None:
            commands.append(command)
    return commands



def _fact_goal_commands(order: ChapterRepairOrder) -> list[ToolCommand]:
    fact_goal = (order.repair_brief or {}).get("fact_repair_goal")
    if not isinstance(fact_goal, dict) or not fact_goal:
        return []
    required = _fact_goal_required_spans(fact_goal)
    forbidden = _fact_goal_forbidden_spans(fact_goal, required)
    if not required:
        return []
    replacements = _entity_replacement_pairs(forbidden, required)
    commands: list[ToolCommand] = []
    missing_after_replacements = required[len(replacements):] if len(required) > len(replacements) else []
    for index, pair in enumerate(replacements):
        replacement_text = pair["to"]
        required_spans = [pair["to"]]
        if index == len(replacements) - 1 and missing_after_replacements:
            replacement_text = pair["to"] + "、" + "、".join(missing_after_replacements)
            required_spans.extend(missing_after_replacements)
        command = _determinize_command(ToolCommand(
            command_id=f"{order.order_id}:fact_goal:{index}",
            operation="replace_span",
            scene_index=_owner_scene(order),
            target_span=pair["from"],
            replacement=replacement_text,
            source_issue_ids=order.source_violation_ids or [order.order_id],
            postconditions={
                "required_spans": required_spans,
                "forbidden_spans": [pair["from"]],
            },
            guards={
                "preserve_facts": True,
                "preserve_scene_contract": True,
                "forbid_unrelated_edits": True,
                "allow_short_unique_search": True,
            },
            rationale=order.reason,
            repair_family="fact_goal",
        ))
        if command is not None:
            commands.append(command)
    return commands


def _fact_goal_required_spans(fact_goal: dict[str, Any]) -> list[str]:
    required: list[str] = []
    suggested = _clean_fact_required_span(
        fact_goal.get("suggested_correction") or fact_goal.get("required_relation") or ""
    )
    conflict_type = str(fact_goal.get("conflict_type") or "").lower()
    has_text_claim = bool(str(fact_goal.get("text_claim") or "").strip())
    if suggested and has_text_claim and conflict_type != "missing_required_components":
        return [suggested]
    for item in fact_goal.get("required_entities") or []:
        text = str(item or "").strip()
        if text and text not in required:
            required.append(text)
    if required:
        return required[:8]
    for key in ("authority_fact", "suggested_correction", "required_relation"):
        for item in _extract_fact_entity_terms(str(fact_goal.get(key) or "")):
            if item not in required:
                required.append(item)
    return required[:8]


def _clean_fact_required_span(value: Any) -> str:
    text = str(value or "").strip(" \t\r\n'\"“”‘’`，。；、：: ")
    if not text:
        return ""
    replacement_match = re.search(
        r"(?:delete\s+or\s+)?(?:replace|change|correct)\s+(?:with|to|as)\s+['\"]([^'\"]{2,80})['\"]",
        text,
        re.IGNORECASE,
    )
    if replacement_match:
        text = replacement_match.group(1).strip()
    lowered = text.lower()
    if any(marker in lowered for marker in ("should ", "must ", "expected", "conflicts with")):
        return ""
    if any(marker in text for marker in ("应当", "应该", "必须", "不得", "冲突", "建议", "修复", "改为")):
        return ""
    if len(text) > 48:
        return ""
    return text


def _fact_goal_forbidden_spans(fact_goal: dict[str, Any], required: list[str]) -> list[str]:
    forbidden: list[str] = []
    for key in ("forbidden_claims", "old_error_signatures", "text_claim"):
        value = fact_goal.get(key)
        values = value if isinstance(value, list) else [value]
        for item in values:
            item_text = str(item or "")
            for term in _extract_fact_entity_terms(item_text):
                if term in required or term in forbidden:
                    continue
                forbidden.append(term)
    return forbidden[:8]


def _extract_fact_entity_terms(value: str) -> list[str]:
    if not value:
        return []
    terms: list[str] = []
    pattern = r"[\u4e00-\u9fff]{1,8}(?:草|果|苓|花|粉末|粉|丹|药|瓶|炉)"
    for match in re.finditer(pattern, value):
        term = _normalize_fact_entity_term(match.group(0))
        if len(term) < 2 or term in terms:
            continue
        if term in {"正文", "事实", "材料", "药草", "丹药"}:
            continue
        terms.append(term)
    return terms[:12]


def _normalize_fact_entity_term(value: str) -> str:
    term = str(value or "").strip("，。；、：: ")
    for prefix in ("正文中使用", "正文中", "使用", "需要", "要", "作为", "提到", "未使用", "未找到", "和", "与", "及", "或"):
        if term.startswith(prefix) and len(term) > len(prefix) + 1:
            term = term[len(prefix):]
            break
    if len(term) > 4:
        term = term[-4:]
    return term.strip("，。；、：: 和与及或")

def _entity_replacement_pairs(forbidden: list[str], required: list[str]) -> list[dict[str, str]]:
    pairs: list[dict[str, str]] = []
    if not forbidden or not required:
        return pairs
    for index, old in enumerate(forbidden):
        new = required[min(index, len(required) - 1)]
        if old and new and old != new and _is_safe_fact_entity_pair(old, new):
            pairs.append({"from": old, "to": new})
    return pairs


def _is_safe_fact_entity_pair(old: str, new: str) -> bool:
    """Only let compatibility fact goals produce obvious entity-name swaps."""

    old_text = str(old or "").strip()
    new_text = str(new or "").strip()
    if not old_text or not new_text or old_text == new_text:
        return False
    unsafe_fragments = {"石", "树", "根", "壁", "洞", "声", "光", "气", "脸", "嘴", "眼"}
    if old_text in unsafe_fragments or new_text in unsafe_fragments:
        return False
    allowed_suffixes = ("草", "果", "苓", "花", "粉末", "粉", "丹", "药", "瓶", "炉")
    return old_text.endswith(allowed_suffixes) and new_text.endswith(allowed_suffixes)


def _dedupe_commands(commands: list[ToolCommand]) -> list[ToolCommand]:
    seen: set[tuple] = set()
    deduped: list[ToolCommand] = []
    for command in commands:
        key = (
            command.operation,
            command.scene_index,
            command.anchor_text,
            command.old_text,
            command.new_text,
            command.span_start,
            command.span_end,
            command.before_context,
            command.after_context,
            command.anchor_occurrence,
            command.target_span,
            command.replacement,
            command.before_span,
            command.after_span,
            command.window_start,
            command.window_end,
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(command)
    return deduped


def _explicit_tool_commands(order: ChapterRepairOrder) -> list[ToolCommand]:
    raw_commands = (order.repair_brief or {}).get("tool_commands")
    if not isinstance(raw_commands, list):
        return []
    commands: list[ToolCommand] = []
    for item in raw_commands:
        if not isinstance(item, dict):
            continue
        data = dict(item)
        data.setdefault("scene_index", _owner_scene(order))
        data.setdefault("source_issue_ids", order.source_violation_ids or [order.order_id])
        command = _determinize_command(ToolCommand(**data))
        if command is not None:
            commands.append(command)
    return commands


def _patch_plan_commands(order: ChapterRepairOrder) -> list[ToolCommand]:
    blueprint = (order.repair_brief or {}).get("revision_blueprint")
    if isinstance(blueprint, dict):
        status = str(blueprint.get("status") or "")
        tool_blueprint = blueprint.get("tool_blueprint")
        if status == "ready" and isinstance(tool_blueprint, dict):
            command = _command_from_patch_item(
                tool_blueprint,
                command_id=f"{order.order_id}:blueprint",
                owner_scene=_owner_scene(order),
                issue_ids=order.source_violation_ids or [order.order_id],
                rationale=order.reason,
            )
            return [command] if command is not None else []
        if status and status != "ready":
            return []
    tool_blueprint = (order.repair_brief or {}).get("tool_blueprint")
    if isinstance(tool_blueprint, dict):
        command = _command_from_patch_item(
            tool_blueprint,
            command_id=f"{order.order_id}:tool_blueprint",
            owner_scene=_owner_scene(order),
            issue_ids=order.source_violation_ids or [order.order_id],
            rationale=order.reason,
        )
        return [command] if command is not None else []
    patch_plan = (order.repair_brief or {}).get("patch_plan")
    if not isinstance(patch_plan, list):
        return []
    commands: list[ToolCommand] = []
    for index, item in enumerate(patch_plan):
        if not isinstance(item, dict):
            continue
        command = _command_from_patch_item(
            item,
            command_id=f"{order.order_id}:patch:{index}",
            owner_scene=_owner_scene(order),
            issue_ids=order.source_violation_ids or [order.order_id],
            rationale=order.reason,
        )
        if command is not None:
            commands.append(command)
    return commands


def _acceptance_criteria_for_order(order: ChapterRepairOrder) -> list[dict]:
    criteria: list[dict] = []
    for command in order.tool_commands or []:
        if isinstance(command, dict) and isinstance(command.get("postconditions"), dict):
            criteria.append({**command["postconditions"], "scene_index": _owner_scene(order)})
    if _is_dash_cleanup_order(order):
        max_dash_count = _dash_max_count(order)
        dash_criterion = {"scene_index": _owner_scene(order)}
        if max_dash_count is None:
            dash_criterion["max_dash_per_1000"] = _dash_threshold(order)
        else:
            dash_criterion["max_dash_count"] = max_dash_count
        criteria.append(dash_criterion)
    fact_goal = (order.repair_brief or {}).get("fact_repair_goal")
    if isinstance(fact_goal, dict):
        required = _fact_goal_required_spans(fact_goal)
        forbidden = _fact_goal_forbidden_spans(fact_goal, required)
        if forbidden or required:
            criteria.append({
                "scene_index": _owner_scene(order),
                "forbidden_spans": forbidden,
                "required_spans": required,
            })
    minister = (order.repair_brief or {}).get("review_minister")
    if isinstance(minister, dict):
        for decision in minister.get("conflict_decisions") or []:
            if not isinstance(decision, dict):
                continue
            current = str(decision.get("current_claim") or "").strip()
            authority = str(decision.get("authority_claim") or "").strip()
            if current and authority and current != authority:
                if _looks_like_abstract_recommendation(authority):
                    continue
                criteria.append({
                    "scene_index": _owner_scene(order),
                    "forbidden_spans": [current],
                    "required_spans": [authority],
                })
    return criteria

def _is_dash_cleanup_order(order: ChapterRepairOrder) -> bool:
    sources: list[dict] = []
    if isinstance(order.repair_brief, dict):
        sources.append(order.repair_brief)
        sources.extend(item for item in order.repair_brief.get("target_metrics") or [] if isinstance(item, dict))
    sources.extend(item for item in order.violation_details or [] if isinstance(item, dict))
    for source in sources:
        metric = str(source.get("metric") or source.get("type") or "").lower()
        target_span = str(source.get("target_span") or "")
        if "dash" in metric or "dash" in target_span or "——" in target_span:
            return True
    return str(order.repair_lane or "") == "deterministic_surface_cleanup"


def _dash_threshold(order: ChapterRepairOrder) -> float:
    sources: list[dict] = [order.repair_brief or {}]
    sources.extend(item for item in (order.repair_brief or {}).get("target_metrics") or [] if isinstance(item, dict))
    sources.extend(item for item in order.violation_details or [] if isinstance(item, dict))
    for source in sources:
        for key in ("expected_max", "max", "dash_per_1000_max", "expected"):
            value = source.get(key)
            if isinstance(value, (int, float)):
                return float(value)
    return DEFAULT_DASH_ARTIFACT_MAX_PER_1000


def _dash_max_count(order: ChapterRepairOrder) -> int | None:
    value = (order.expected_after_repair or {}).get("max_dash_count")
    if isinstance(value, (int, float)):
        return max(0, int(value))
    return None


def _dash_target_span(order: ChapterRepairOrder) -> str:
    sources: list[dict] = []
    sources.extend(item for item in order.violation_details or [] if isinstance(item, dict))
    if isinstance(order.repair_brief, dict):
        sources.append(order.repair_brief)
        sources.extend(item for item in order.repair_brief.get("target_metrics") or [] if isinstance(item, dict))
    for source in sources:
        span = str(source.get("target_span") or source.get("old_text") or "").strip()
        if not span:
            continue
        metric = str(source.get("metric") or source.get("type") or "").lower()
        if "dash" in metric or "破折号" in metric or span in {"——", "--", "—", "-"}:
            return span
    return "——"


def _edit_anchor(order: ChapterRepairOrder, commands: list[ToolCommand]) -> str:
    for command in commands:
        for value in (
            command.anchor_text,
            command.old_text,
            command.target_span,
            command.after_span,
            command.before_span,
            command.window_start,
        ):
            if value:
                return value[:80]
    for violation in order.violation_details or []:
        if isinstance(violation, dict):
            for key in ("target_span", "evidence_span", "actual"):
                value = str(violation.get(key) or "")
                if value:
                    return value[:80]
    return order.repair_domain or order.repair_type


def _owner_scene(order: ChapterRepairOrder) -> int | None:
    if order.owner_scene is not None:
        return order.owner_scene
    return order.target_scenes[0] if order.target_scenes else None


def _string_list(value: Any) -> list[str]:
    values = value if isinstance(value, list) else [value]
    return [str(item).strip() for item in values if str(item or "").strip()]


def _looks_like_abstract_recommendation(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    lowered = text.lower()
    tokens = [
        "conflict density",
        "curiosity engine",
        "micro payoff",
        "pressure ramp",
        "skill contract",
        "quality contract",
        "target behavior",
        "repair intent",
        "should",
        "must",
        "needs to",
        "\u51b2\u7a81\u5bc6\u5ea6",
        "\u60ac\u5ff5",
        "\u5fae\u5151\u73b0",
        "\u8bfb\u8005",
        "\u538b\u529b",
        "\u963b\u788d",
        "\u73b0\u573a\u53d8\u5316",
        "\u4eba\u7269\u4e92\u52a8",
        "\u65f6\u95f4\u9650\u5236",
        "\u8d44\u6e90\u4ee3\u4ef7",
        "\u5e94\u8be5",
        "\u5e94\u5f53",
        "\u9700\u8981",
        "\u4fee\u8ba2\u76ee\u6807",
        "\u4fee\u590d\u76ee\u6807",
        "\u5408\u540c",
        "\u6307\u6807",
    ]
    if any(token in lowered or token in text for token in tokens):
        return True
    return len(text) > 100 and any(token in text for token in ["\u8ba9", "\u589e\u5f3a", "\u51cf\u5c11", "\u4fdd\u7559"])


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(item for item in values if item))


def _validator_snapshot_for_units(work_units: list[RepairWorkUnit]) -> dict[str, Any]:
    criteria = [
        criterion
        for unit in work_units
        for criterion in (unit.tool_batch.acceptance_criteria or [])
        if isinstance(criterion, dict)
    ]
    snapshot = ValidatorProtocolAdapter().build_snapshot(criteria=criteria)
    for unit in work_units:
        if not unit.validator_snapshot:
            unit.validator_snapshot = snapshot
    return snapshot


def _completion_summary(
    work_units: list[RepairWorkUnit],
    *,
    expected_issue_count: int | None = None,
) -> dict[str, Any]:
    covered_orders = {
        order_id
        for unit in work_units
        for order_id in (unit.source_order_ids or [])
        if order_id
    }
    covered_issues = {
        issue_id
        for unit in work_units
        for issue_id in (unit.source_violation_ids or [])
        if issue_id
    }
    executable_units = [
        unit for unit in work_units
        if unit.tool_batch.commands
        and all(command.operation not in {"mark_needs_human", "mark_unrepairable_by_tool"} for command in unit.tool_batch.commands)
    ]
    denominator = expected_issue_count if expected_issue_count is not None else max(len(covered_orders), len(covered_issues), len(work_units), 1)
    executable_count = len(executable_units)
    return {
        "work_unit_count": len(work_units),
        "executable_work_unit_count": executable_count,
        "covered_order_count": len(covered_orders),
        "covered_issue_count": len(covered_issues),
        "expected_issue_count": denominator,
        "executable_unit_rate": round(executable_count / denominator, 4) if denominator else 1.0,
    }


def _stable_id(*parts: Any) -> str:
    raw = ":".join(str(part) for part in parts)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]

