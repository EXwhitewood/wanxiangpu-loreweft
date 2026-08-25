"""Deterministic FBI tool execution.

The tool layer is deliberately not an Agent and does not call an LLM. It
receives concrete commands from the FBI review/blueprint path, applies them to
bounded scene text, and reports precise pre/postcondition failures.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

from app.models.chapter_review import RepairWorkUnit, ToolCommand
from app.models.fbi_repair import RepairPatch, TextSpan, compute_text_hash
from app.services.fbi.blueprint_route_registry import find_patch_meta_terms
from app.utils.dash_artifacts import (
    DEFAULT_DASH_ARTIFACT_MAX_PER_1000,
    count_dash_artifacts,
    dash_artifact_allowed_count,
    dash_artifact_replacement,
    dash_artifact_replacement_indexes,
    iter_dash_artifacts,
)


def _hash_text(text: str) -> str:
    return hashlib.md5((text or "").encode("utf-8")).hexdigest()[:12]


class ToolExecutor:
    """Execute deterministic edit commands against scene text maps."""

    def execute_work_units_as_patches(
        self,
        work_units: list[RepairWorkUnit],
        scene_texts: dict[int, str],
        *,
        case_id: str = "",
    ) -> tuple[list[RepairPatch], dict[str, dict[str, Any]]]:
        """Dry-run work units against a frozen base and emit RepairPatch objects.

        This is the automatic FBI main-chain path. It never mutates
        ``scene_texts`` and never invents replacement text.
        """

        patches: list[RepairPatch] = []
        audits: dict[str, dict[str, Any]] = {}
        for unit in work_units:
            unit_patches: list[RepairPatch] = []
            command_results: list[dict[str, Any]] = []
            failed: dict[str, Any] | None = None
            for command in unit.tool_batch.commands:
                result = self._command_to_patches(command, unit, scene_texts, case_id=case_id)
                command_results.append(result)
                if not result.get("accepted"):
                    failed = result
                    break
                unit_patches.extend(result.get("patches") or [])
            if failed is not None:
                audits[unit.work_unit_id] = {
                    "accepted": False,
                    "changed": False,
                    "reason": failed.get("reason") or "tool_patch_generation_failed",
                    "failures": failed.get("failures") or ["tool_patch_generation_failed"],
                    "failed_command_id": failed.get("command_id"),
                    "command_results": command_results,
                    "executor_path": "fbi_tool_patch_dry_run",
                }
                continue
            patches.extend(unit_patches)
            audits[unit.work_unit_id] = {
                "accepted": True,
                "changed": bool(unit_patches),
                "patch_ids": [patch.patch_id for patch in unit_patches],
                "patch_count": len(unit_patches),
                "changed_scenes": sorted({
                    int(patch.self_audit.get("scene_index"))
                    for patch in unit_patches
                    if isinstance(patch.self_audit.get("scene_index"), int)
                }),
                "command_results": command_results,
                "executor_path": "fbi_tool_patch_dry_run",
            }
        return patches, audits

    def _command_to_patches(
        self,
        command: ToolCommand,
        unit: RepairWorkUnit,
        scene_texts: dict[int, str],
        *,
        case_id: str,
    ) -> dict[str, Any]:
        scene_index = command.scene_index
        if scene_index is None:
            scene_index = unit.owner_scene if unit.owner_scene is not None else (
                unit.target_scenes[0] if unit.target_scenes else None
            )
        if scene_index is None or scene_index not in scene_texts:
            return self._failed(command, "missing_target_scene", scene_index)
        text = scene_texts.get(scene_index, "")
        precheck = self._check_conditions(command.preconditions, text, scene_index)
        if not precheck["accepted"]:
            return {
                **precheck,
                "command_id": command.command_id,
                "operation": command.operation,
                "scene_index": scene_index,
            }
        replacement_text = str(command.new_text or command.replacement or "")
        meta_terms = find_patch_meta_terms(replacement_text)
        if meta_terms:
            return {
                **self._failed(command, "narrative_meta_text_rejected", scene_index),
                "meta_terms": meta_terms,
            }
        if command.operation == "replace_exact":
            patch_specs = self._replace_exact_patch_specs(command, text)
        elif command.operation == "delete_exact":
            patch_specs = self._delete_exact_patch_specs(command, text)
        elif command.operation in {"insert_before_anchor", "insert_after_anchor"}:
            patch_specs = self._insert_exact_patch_specs(command, text)
        elif command.operation == "normalize_punctuation":
            # T11.8: dash cleanup 改用 normalize_punctuation，在 patch dry-run 路径下
            # 生成多个小 patches（每个对应一个超出阈值的 dash 位置），避免与其它文本修改冲突。
            patch_specs = self._normalize_punctuation_patch_specs(command, text)
        else:
            return self._failed(command, "non_deterministic_tool_command", scene_index)
        if not patch_specs.get("accepted"):
            return {
                **patch_specs,
                "command_id": command.command_id,
                "operation": command.operation,
                "scene_index": scene_index,
            }
        base_hash = compute_text_hash(text)
        patches: list[RepairPatch] = []
        for index, spec in enumerate(patch_specs.get("patch_specs") or []):
            start = int(spec["start"])
            end = int(spec["end"])
            original = str(spec.get("original_text") or text[start:end])
            replacement = str(spec.get("replacement_text") or "")
            patches.append(RepairPatch(
                patch_id=f"{unit.work_unit_id}:{command.command_id}:{index}",
                case_id=case_id or unit.work_unit_id,
                intent_id=unit.work_unit_id,
                base_text_hash=base_hash,
                span=TextSpan(start=start, end=end, label=command.operation),
                original_text=original,
                replacement_text=replacement,
                changed_chars=abs(len(replacement) - len(original)),
                strategy=command.operation,
                resolves_issue_ids=list(command.source_issue_ids or unit.source_violation_ids or []),
                risk_level="low",
                self_audit={
                    "scene_index": scene_index,
                    "work_unit_id": unit.work_unit_id,
                    "command_id": command.command_id,
                    "operation": command.operation,
                    "base_text_hash": base_hash,
                    "source": "fbi_tool_patch_dry_run",
                    "postconditions": command.postconditions,
                },
                requires_full_recheck=True,
            ))
        return {
            "accepted": True,
            "changed": bool(patches),
            "command_id": command.command_id,
            "operation": command.operation,
            "scene_index": scene_index,
            "patches": patches,
            "patch_count": len(patches),
            "original_text": patches[0].original_text if patches else "",
            "replacement_text": patches[0].replacement_text if patches else "",
        }

    @staticmethod
    def _replace_exact_patch_specs(command: ToolCommand, text: str) -> dict[str, Any]:
        old = (command.old_text or command.target_span or "").strip()
        new = command.new_text if command.new_text else command.replacement
        if not old:
            return {"accepted": False, "changed": False, "reason": "missing_old_text", "failures": ["missing_old_text"]}
        if not new:
            return {"accepted": False, "changed": False, "reason": "missing_new_text", "failures": ["missing_new_text"]}
        span_spec = ToolExecutor._span_located_patch_spec(command, text, old, new)
        if span_spec is not None:
            return span_spec
        locator_check = ToolExecutor._check_search_locator_strength(command, old)
        if not locator_check["accepted"]:
            return locator_check
        matches = [match for match in re.finditer(re.escape(old), text)]
        matches = ToolExecutor._filter_matches_by_locator(command, text, matches)
        if not matches:
            return {"accepted": False, "changed": False, "reason": "old_text_not_found", "failures": ["old_text_not_found"]}
        if command.required_unique_anchor and len(matches) > 1 and not command.replace_all:
            return {
                "accepted": False,
                "changed": False,
                "reason": "anchor_not_unique",
                "failures": ["anchor_not_unique"],
                "matches": len(matches),
            }
        selected = matches if command.replace_all else matches[:max(1, int(command.max_replacements or 1))]
        return {
            "accepted": True,
            "changed": True,
            "patch_specs": [
                {
                    "start": match.start(),
                    "end": match.end(),
                    "original_text": old,
                    "replacement_text": new,
                }
                for match in selected
            ],
        }

    @staticmethod
    def _delete_exact_patch_specs(command: ToolCommand, text: str) -> dict[str, Any]:
        old = (command.old_text or command.target_span or "").strip()
        if not old:
            return {"accepted": False, "changed": False, "reason": "missing_old_text", "failures": ["missing_old_text"]}
        span_spec = ToolExecutor._span_located_patch_spec(command, text, old, "")
        if span_spec is not None:
            return span_spec
        locator_check = ToolExecutor._check_search_locator_strength(command, old)
        if not locator_check["accepted"]:
            return locator_check
        matches = [match for match in re.finditer(re.escape(old), text)]
        matches = ToolExecutor._filter_matches_by_locator(command, text, matches)
        if not matches:
            return {"accepted": False, "changed": False, "reason": "old_text_not_found", "failures": ["old_text_not_found"]}
        if command.required_unique_anchor and len(matches) > 1 and not command.replace_all:
            return {
                "accepted": False,
                "changed": False,
                "reason": "anchor_not_unique",
                "failures": ["anchor_not_unique"],
                "matches": len(matches),
            }
        selected = matches if command.replace_all else matches[:max(1, int(command.max_replacements or 1))]
        return {
            "accepted": True,
            "changed": True,
            "patch_specs": [
                {
                    "start": match.start(),
                    "end": match.end(),
                    "original_text": old,
                    "replacement_text": "",
                }
                for match in selected
            ],
        }

    @staticmethod
    def _insert_exact_patch_specs(command: ToolCommand, text: str) -> dict[str, Any]:
        anchor = (
            command.anchor_text
            or command.after_span
            or command.before_span
            or command.target_span
            or ""
        ).strip()
        insertion = command.new_text or command.replacement
        if not anchor:
            return {"accepted": False, "changed": False, "reason": "missing_anchor", "failures": ["missing_anchor"]}
        if not insertion:
            return {"accepted": False, "changed": False, "reason": "missing_insert_text", "failures": ["missing_insert_text"]}
        if command.span_start is not None or command.span_end is not None:
            if command.span_start is None or command.span_end is None:
                return {
                    "accepted": False,
                    "changed": False,
                    "reason": "incomplete_span_locator",
                    "failures": ["incomplete_span_locator"],
                }
            start = int(command.span_start)
            end = int(command.span_end)
            if start < 0 or end < start or end > len(text):
                return {
                    "accepted": False,
                    "changed": False,
                    "reason": "span_locator_out_of_bounds",
                    "failures": ["span_locator_out_of_bounds"],
                }
            actual = text[start:end]
            if actual != anchor:
                return {
                    "accepted": False,
                    "changed": False,
                    "reason": "span_original_text_mismatch",
                    "failures": ["span_original_text_mismatch"],
                    "actual_text": actual,
                    "expected_text": anchor,
                }
            insert_at = start if command.operation == "insert_before_anchor" else end
            if not ToolExecutor._is_safe_insert_boundary(text, insert_at, command.operation):
                return {
                    "accepted": False,
                    "changed": False,
                    "reason": "insert_anchor_not_at_narrative_boundary",
                    "failures": ["insert_anchor_not_at_narrative_boundary"],
                }
            return {
                "accepted": True,
                "changed": True,
                "locator_grade": "A_span",
                "patch_specs": [{
                    "start": insert_at,
                    "end": insert_at,
                    "original_text": "",
                    "replacement_text": insertion,
                }],
            }
        matches = [match for match in re.finditer(re.escape(anchor), text)]
        if not matches:
            return {"accepted": False, "changed": False, "reason": "anchor_not_found", "failures": ["anchor_not_found"]}
        if command.required_unique_anchor and len(matches) > 1:
            return {
                "accepted": False,
                "changed": False,
                "reason": "anchor_not_unique",
                "failures": ["anchor_not_unique"],
                "matches": len(matches),
            }
        occurrence = max(1, int(command.anchor_occurrence or 1))
        if occurrence > len(matches):
            return {
                "accepted": False,
                "changed": False,
                "reason": "anchor_occurrence_not_found",
                "failures": ["anchor_occurrence_not_found"],
                "occurrence": occurrence,
            }
        match = matches[occurrence - 1]
        insert_at = match.start() if command.operation == "insert_before_anchor" else match.end()
        if not ToolExecutor._is_safe_insert_boundary(text, insert_at, command.operation):
            return {
                "accepted": False,
                "changed": False,
                "reason": "insert_anchor_not_at_narrative_boundary",
                "failures": ["insert_anchor_not_at_narrative_boundary"],
            }
        return {
            "accepted": True,
            "changed": True,
            "patch_specs": [{
                "start": insert_at,
                "end": insert_at,
                "original_text": "",
                "replacement_text": insertion,
            }],
        }

    @staticmethod
    def _is_safe_insert_boundary(text: str, position: int, operation: str) -> bool:
        """Require prose insertions to occur at a sentence/paragraph boundary.

        Exact anchors can still be short, but inserting a complete beat after a
        truncated token (for example, between two characters of one clause)
        corrupts the sentence while remaining syntactically executable.  A
        mid-sentence edit must use ``replace_exact`` instead.
        """
        if position <= 0:
            return operation == "insert_before_anchor"
        if position > len(text):
            return False
        left = text[:position].rstrip()
        if not left:
            return operation == "insert_before_anchor"
        closers = '"\'”’」』》〉】）)'
        while left and left[-1] in closers:
            left = left[:-1].rstrip()
        return bool(left and left[-1] in ".。!！?？;；\n")

    @staticmethod
    def _span_located_patch_spec(
        command: ToolCommand,
        text: str,
        old: str,
        new: str,
    ) -> dict[str, Any] | None:
        """Use explicit frozen-base character offsets as the strongest locator."""

        if command.span_start is None and command.span_end is None:
            return None
        if command.span_start is None or command.span_end is None:
            return {
                "accepted": False,
                "changed": False,
                "reason": "incomplete_span_locator",
                "failures": ["incomplete_span_locator"],
            }
        start = int(command.span_start)
        end = int(command.span_end)
        if start < 0 or end < start or end > len(text):
            return {
                "accepted": False,
                "changed": False,
                "reason": "span_locator_out_of_bounds",
                "failures": ["span_locator_out_of_bounds"],
                "span_start": start,
                "span_end": end,
            }
        actual = text[start:end]
        if actual != old:
            return {
                "accepted": False,
                "changed": False,
                "reason": "span_original_text_mismatch",
                "failures": ["span_original_text_mismatch"],
                "span_start": start,
                "span_end": end,
                "actual_text": actual,
                "expected_text": old,
            }
        return {
            "accepted": True,
            "changed": actual != new,
            "locator_grade": "A_span",
            "patch_specs": [{
                "start": start,
                "end": end,
                "original_text": old,
                "replacement_text": new,
            }],
        }

    @staticmethod
    def _check_search_locator_strength(command: ToolCommand, old: str) -> dict[str, Any]:
        """Reject unsafe pure-text search without rejecting short span edits."""

        if command.replace_all:
            return {"accepted": True, "changed": False}
        locator_present = (
            command.before_context
            or command.after_context
            or command.paragraph_index is not None
            or int(command.anchor_occurrence or 1) > 1
        )
        if locator_present:
            return {"accepted": True, "changed": False}
        if (command.guards or {}).get("allow_short_unique_search"):
            return {"accepted": True, "changed": False}
        if ToolExecutor._is_boundary_safe_search_text(old):
            return {"accepted": True, "changed": False}
        return {
            "accepted": False,
            "changed": False,
            "reason": "insufficient_precise_locator",
            "failures": ["insufficient_precise_locator"],
            "detail": "short_or_fragment_text_requires_span_or_context_locator",
        }

    @staticmethod
    def _is_boundary_safe_search_text(value: str) -> bool:
        text = str(value or "").strip()
        if len(text) >= 8:
            return True
        if len(text) >= 6 and re.search(r"[\u4e00-\u9fff]", text):
            return True
        if len(text) >= 6 and re.search(r"[，。！？；：,.!?;:\n]", text):
            return True
        return False

    @staticmethod
    def _filter_matches_by_locator(
        command: ToolCommand,
        text: str,
        matches: list[re.Match[str]],
    ) -> list[re.Match[str]]:
        filtered = matches
        if command.paragraph_index is not None:
            bounds = ToolExecutor._paragraph_bounds(text)
            index = int(command.paragraph_index)
            if 0 <= index < len(bounds):
                start, end = bounds[index]
                filtered = [match for match in filtered if start <= match.start() and match.end() <= end]
            else:
                return []
        if command.before_context:
            before = command.before_context.strip()
            filtered = [
                match for match in filtered
                if before and before in text[max(0, match.start() - 300):match.start()]
            ]
        if command.after_context:
            after = command.after_context.strip()
            filtered = [
                match for match in filtered
                if after and after in text[match.end():min(len(text), match.end() + 300)]
            ]
        occurrence = int(command.anchor_occurrence or 1)
        if occurrence > 1:
            return [filtered[occurrence - 1]] if occurrence <= len(filtered) else []
        return filtered

    @staticmethod
    def _paragraph_bounds(text: str) -> list[tuple[int, int]]:
        bounds: list[tuple[int, int]] = []
        start = 0
        for part in text.split("\n\n"):
            end = start + len(part)
            bounds.append((start, end))
            start = end + 2
        return bounds

    @staticmethod
    def _failed(command: ToolCommand, reason: str, scene_index: int | None) -> dict[str, Any]:
        return {
            "accepted": False,
            "changed": False,
            "reason": reason,
            "failures": [reason],
            "command_id": command.command_id,
            "operation": command.operation,
            "scene_index": scene_index,
        }

    @staticmethod
    def _check_conditions(conditions: dict[str, Any] | None, text: str, scene_index: int) -> dict[str, Any]:
        conditions = conditions or {}
        required = [str(item) for item in conditions.get("required_spans") or [] if str(item)]
        missing = [span for span in required if span not in text]
        if missing:
            return {
                "accepted": False,
                "changed": False,
                "reason": "required_span_missing",
                "failures": ["required_span_missing"],
                "missing_spans": missing,
                "scene_index": scene_index,
            }

        forbidden = [str(item) for item in conditions.get("forbidden_spans") or [] if str(item)]
        present = [span for span in forbidden if span in text]
        if present:
            return {
                "accepted": False,
                "changed": False,
                "reason": "forbidden_span_present",
                "failures": ["forbidden_span_present"],
                "forbidden_spans": present,
                "scene_index": scene_index,
            }

        max_dash = conditions.get("max_dash_per_1000")
        if isinstance(max_dash, (int, float)):
            allowed = dash_artifact_allowed_count(text, float(max_dash))
            count = count_dash_artifacts(text)
            if count > allowed:
                return {
                    "accepted": False,
                    "changed": False,
                    "reason": "dash_density_exceeds_postcondition",
                    "failures": ["dash_density_exceeds_postcondition"],
                    "dash_count": count,
                    "allowed": allowed,
                    "scene_index": scene_index,
                }

        max_dash_count = conditions.get("max_dash_count")
        if isinstance(max_dash_count, (int, float)):
            count = count_dash_artifacts(text)
            if count > int(max_dash_count):
                return {
                    "accepted": False,
                    "changed": False,
                    "reason": "dash_count_exceeds_postcondition",
                    "failures": ["dash_count_exceeds_postcondition"],
                    "dash_count": count,
                    "allowed": int(max_dash_count),
                    "scene_index": scene_index,
                }

        return {"accepted": True, "changed": False}

    # 方案5 B4：以下11个方法为死代码，已删除：
    # _replace_span, _insert_anchor, _find_anchor_span, _compact_with_map,
    # _normalize_anchor_char, _distinctive_anchor_chunks, _sentence_bounds,
    # _insert_by_paragraph_fallback, _insert_time_anchor, _delete_span, _rewrite_window
    @staticmethod
    def _split_paragraph(command: ToolCommand, text: str) -> tuple[str, dict[str, Any]]:
        paragraphs = text.split("\n\n")
        index = command.paragraph_index
        target = command.target_span.strip()
        if not target:
            return text, {"accepted": False, "changed": False, "reason": "missing_target_span", "failures": ["missing_target_span"]}
        if index is None:
            index = next((idx for idx, paragraph in enumerate(paragraphs) if target in paragraph), None)
        if index is None or index < 0 or index >= len(paragraphs):
            return text, {"accepted": False, "changed": False, "reason": "paragraph_index_out_of_range", "failures": ["paragraph_index_out_of_range"]}
        if target not in paragraphs[index]:
            return text, {"accepted": False, "changed": False, "reason": "split_span_not_found", "failures": ["split_span_not_found"]}
        paragraphs[index] = paragraphs[index].replace(target, target + "\n\n", 1)
        return "\n\n".join(paragraphs), {"accepted": True, "changed": True}

    @staticmethod
    def _normalize_punctuation_patch_specs(command: ToolCommand, text: str) -> dict[str, Any]:
        """T11.8: 为 normalize_punctuation 生成 patch_specs（多个小 patches）。

        与 _normalize_punctuation 共享阈值/替换逻辑，但返回 patch_specs 格式，
        让 execute_work_units_as_patches（patch dry-run 路径）能直接生成 RepairPatch。
        每个 patch_spec 对应一个超出 target_count 的 dash 位置，避免覆盖整个文本。
        """
        max_dash = (
            command.postconditions.get("max_dash_per_1000")
            or command.preconditions.get("max_dash_per_1000")
            or DEFAULT_DASH_ARTIFACT_MAX_PER_1000
        )
        try:
            threshold = float(max_dash)
        except Exception:
            threshold = DEFAULT_DASH_ARTIFACT_MAX_PER_1000
        before_count = count_dash_artifacts(text)
        explicit_max_count = command.postconditions.get("max_dash_count")
        target_count = (
            max(0, int(explicit_max_count))
            if isinstance(explicit_max_count, (int, float))
            else dash_artifact_allowed_count(text, threshold)
        )
        if before_count <= target_count:
            return {
                "accepted": True,
                "changed": False,
                "patch_specs": [],
                "dash_count": before_count,
                "allowed": target_count,
            }
        matches = list(iter_dash_artifacts(text))
        # 优先替换最容易用正常句法承接的破折号；允许保留的少数破折号留给
        # 语义承载更强、直接替换更可能损伤节奏的位置。
        replacement_indexes = set(
            dash_artifact_replacement_indexes(
                text,
                matches,
                before_count - target_count,
            )
        )
        patch_specs: list[dict[str, Any]] = []
        for index, match in enumerate(matches):
            if index not in replacement_indexes:
                continue
            replacement = dash_artifact_replacement(text, match.start(), match.end())
            patch_specs.append({
                "start": match.start(),
                "end": match.end(),
                "original_text": match.group(0),
                "replacement_text": replacement,
            })
        return {
            "accepted": True,
            "changed": bool(patch_specs),
            "patch_specs": patch_specs,
            "dash_count_before": before_count,
            "allowed": target_count,
        }

    @staticmethod
    def _normalize_structure_words(command: ToolCommand, text: str) -> tuple[str, dict[str, Any]]:
        target = command.target_span.strip()
        window = target if target and target in text else text
        if not window:
            return text, {"accepted": False, "changed": False, "reason": "missing_structure_window", "failures": ["missing_structure_window"]}
        replacements = {
            "这说明": "",
            "这意味着": "",
            "从某种意义上": "",
            "某种意义上": "",
            "显然": "",
            "事实上": "",
            "换句话说": "",
            "与此同时": "这时",
            "因此": "",
            "然而": "但",
            "于是": "",
            "然后": "",
            "仿佛": "像",
            "似乎": "",
        }
        updated_window = window
        applied: list[dict[str, str]] = []
        for old, new in replacements.items():
            if old not in updated_window:
                continue
            updated_window = updated_window.replace(old, new, 1)
            applied.append({"from": old, "to": new})
            if len(applied) >= int(command.max_replacements or 3):
                break
        updated_window = re.sub(r"，{2,}", "，", updated_window)
        updated_window = re.sub(r"。\s*。", "。", updated_window)
        updated_window = re.sub(r"\s+", " ", updated_window).strip() if "\n" not in updated_window else updated_window
        if not applied or updated_window == window:
            return text, {
                "accepted": False,
                "changed": False,
                "reason": "no_structure_word_change",
                "failures": ["no_structure_word_change"],
            }
        if target and target in text:
            return text.replace(target, updated_window, 1), {
                "accepted": True,
                "changed": True,
                "applied": applied,
            }
        return updated_window, {
            "accepted": True,
            "changed": updated_window != text,
            "applied": applied,
        }

    @staticmethod
    def _replace_tier1_ai_flavor_terms(command: ToolCommand, text: str) -> tuple[str, dict[str, Any]]:
        target = command.target_span.strip()
        window = target if target and target in text else text
        if not window:
            return text, {"accepted": False, "changed": False, "reason": "missing_tier1_window", "failures": ["missing_tier1_window"]}
        replacements = {
            "仿佛": "像",
            "似乎": "",
            "某种意义上": "",
            "某种": "",
            "这说明": "",
            "这意味着": "",
            "显然": "",
            "事实上": "",
            "换句话说": "",
            "真正": "",
            "复杂的情绪": "情绪",
            "复杂": "",
            "内心": "",
            "灵魂": "",
            "命运": "",
            "象征": "像",
            "代表": "是",
            "体现": "露出",
            "无法形容": "",
            "难以言说": "",
            "薄薄的": "一层",
            "微光": "热意",
            "一瞬": "一下",
            "不由得": "",
            "忍不住": "",
        }
        updated_window = window
        applied: list[dict[str, str]] = []
        for old, new in replacements.items():
            if old not in updated_window:
                continue
            updated_window = updated_window.replace(old, new, 1)
            applied.append({"from": old, "to": new})
            if len(applied) >= int(command.max_replacements or 4):
                break
        updated_window = re.sub(r"，{2,}", "，", updated_window)
        updated_window = re.sub(r"。\s*。", "。", updated_window)
        updated_window = re.sub(r"\s+", " ", updated_window).strip() if "\n" not in updated_window else updated_window
        if not applied or updated_window == window:
            return text, {
                "accepted": False,
                "changed": False,
                "reason": "no_tier1_term_change",
                "failures": ["no_tier1_term_change"],
            }
        if target and target in text:
            return text.replace(target, updated_window, 1), {
                "accepted": True,
                "changed": True,
                "applied": applied,
            }
        return updated_window, {
            "accepted": True,
            "changed": updated_window != text,
            "applied": applied,
        }

    @staticmethod
    def _cleanup_ai_flavor_window(command: ToolCommand, text: str) -> tuple[str, dict[str, Any]]:
        target = command.target_span.strip()
        window = target if target and target in text else text
        if not window:
            return text, {"accepted": False, "changed": False, "reason": "missing_ai_flavor_window", "failures": ["missing_ai_flavor_window"]}
        replacements = {
            "值得注意的是": "",
            "不难发现": "",
            "换句话说": "",
            "从某种意义上": "",
            "某种意义上": "",
            "某种": "",
            "事实上": "",
            "显然": "",
            "这说明": "",
            "这意味着": "",
            "标志着": "让",
            "见证了": "留下",
            "至关重要": "要紧",
            "深远意义": "后果",
            "命运": "后路",
            "宿命": "旧账",
            "羁绊": "牵连",
            "执念": "不肯松手的念头",
            "救赎": "补偿",
            "灵魂": "心口",
            "本质": "底子",
            "价值": "用处",
            "勾勒": "露出",
            "渲染": "压住",
            "交织": "缠在一起",
            "流淌": "淌过",
            "弥漫": "散开",
            "烙印": "印子",
            "令人叹为观止": "",
            "美轮美奂": "",
            "无与伦比": "",
            "博大精深": "",
            "未来的路还很长": "眼前的麻烦还没完",
            "一切才刚刚开始": "这件事还没完",
            "终于明白了": "停了一下",
            "真正重要的不是": "要紧的不是",
        }
        updated_window, applied = ToolExecutor._apply_phrase_replacements(
            window,
            replacements,
            max_replacements=int(command.max_replacements or 4),
        )
        updated_window, grounded = ToolExecutor._ground_emotion_labels(updated_window)
        applied.extend(grounded)
        updated_window = ToolExecutor._cleanup_spacing(updated_window)
        if not applied or updated_window == window:
            return text, {
                "accepted": False,
                "changed": False,
                "reason": "no_ai_flavor_cleanup_change",
                "failures": ["no_ai_flavor_cleanup_change"],
            }
        if target and target in text:
            return text.replace(target, updated_window, 1), {"accepted": True, "changed": True, "applied": applied}
        return updated_window, {"accepted": True, "changed": updated_window != text, "applied": applied}

    @staticmethod
    def _trim_discourse_window(command: ToolCommand, text: str) -> tuple[str, dict[str, Any]]:
        target = command.target_span.strip()
        window = target if target and target in text else text
        if not window:
            return text, {"accepted": False, "changed": False, "reason": "missing_discourse_window", "failures": ["missing_discourse_window"]}
        replacements = {
            "答案很简单": "",
            "原因很简单": "",
            "真正重要的是": "要紧的是",
            "真正重要的不是": "要紧的不是",
            "这说明": "",
            "这意味着": "",
            "也就是说": "",
            "换句话说": "",
            "她终于明白": "她停了一下",
            "他终于明白": "他停了一下",
            "学会了珍惜": "把手里的东西攥紧",
            "放下了过去": "没有再回头",
            "你是否也": "",
            "不妨想一想": "",
            "让我们": "",
            "我们不妨": "",
            "值得我们思考": "",
        }
        updated_window, applied = ToolExecutor._apply_phrase_replacements(
            window,
            replacements,
            max_replacements=int(command.max_replacements or 3),
        )
        updated_window = ToolExecutor._remove_adjacent_restatement(updated_window)
        updated_window = ToolExecutor._cleanup_spacing(updated_window)
        changed = updated_window != window
        if not changed:
            return text, {
                "accepted": False,
                "changed": False,
                "reason": "no_discourse_trim_change",
                "failures": ["no_discourse_trim_change"],
            }
        if target and target in text:
            return text.replace(target, updated_window, 1), {"accepted": True, "changed": True, "applied": applied}
        return updated_window, {"accepted": True, "changed": updated_window != text, "applied": applied}

    @staticmethod
    def _rewrite_voice_window(command: ToolCommand, text: str) -> tuple[str, dict[str, Any]]:
        target = command.target_span.strip()
        window = target if target and target in text else text
        if not window:
            return text, {"accepted": False, "changed": False, "reason": "missing_voice_window", "failures": ["missing_voice_window"]}
        replacements = {
            "某种": "",
            "仿佛": "像",
            "似乎": "",
            "显然": "",
            "事实上": "",
            "真正": "",
            "内心": "心口",
            "复杂的情绪": "乱意",
            "复杂": "",
            "命运": "后路",
            "灵魂": "心口",
            "意义": "用处",
            "本质": "底子",
        }
        updated_window, applied = ToolExecutor._apply_phrase_replacements(
            window,
            replacements,
            max_replacements=int(command.max_replacements or 4),
        )
        updated_window, externalized = ToolExecutor._externalize_inner_access(updated_window)
        applied.extend(externalized)
        updated_window = ToolExecutor._cleanup_spacing(updated_window)
        if not applied or updated_window == window:
            return text, {
                "accepted": False,
                "changed": False,
                "reason": "no_voice_window_change",
                "failures": ["no_voice_window_change"],
            }
        if target and target in text:
            return text.replace(target, updated_window, 1), {"accepted": True, "changed": True, "applied": applied}
        return updated_window, {"accepted": True, "changed": updated_window != text, "applied": applied}

    @staticmethod
    def _apply_phrase_replacements(
        window: str,
        replacements: dict[str, str],
        *,
        max_replacements: int,
    ) -> tuple[str, list[dict[str, str]]]:
        updated = window
        applied: list[dict[str, str]] = []
        for old, new in replacements.items():
            if old not in updated:
                continue
            updated = updated.replace(old, new, 1)
            applied.append({"from": old, "to": new})
            if len(applied) >= max(1, max_replacements):
                break
        return updated, applied

    @staticmethod
    def _ground_emotion_labels(window: str) -> tuple[str, list[dict[str, str]]]:
        labels = {
            "害怕": "指节收紧",
            "恐惧": "呼吸卡住",
            "担心": "呼吸放轻",
            "紧张": "掌心发潮",
            "愤怒": "下颌绷紧",
            "生气": "下颌绷紧",
            "悲伤": "眼尾发涩",
            "难过": "眼尾发涩",
            "震惊": "动作停住",
            "开心": "嘴角抬了一下",
            "复杂的情绪": "指节慢慢收紧",
        }
        updated = window
        applied: list[dict[str, str]] = []
        label_pattern = "|".join(map(re.escape, labels))
        pattern = re.compile(rf"([\u4e00-\u9fff]{{1,8}}?)(?:很|非常|有些|更加|显得|变得)?({label_pattern})")
        for match in list(pattern.finditer(updated))[:4]:
            subject = match.group(1)
            label = match.group(2)
            replacement = f"{subject}{labels[label]}"
            updated = updated.replace(match.group(0), replacement, 1)
            applied.append({"from": match.group(0), "to": replacement, "mode": "emotion_grounding"})
        return updated, applied

    @staticmethod
    def _externalize_inner_access(window: str) -> tuple[str, list[dict[str, str]]]:
        markers = ("心里", "心中", "脑中", "知道", "明白", "意识到", "觉得", "认定", "暗想")
        marker_re = "|".join(map(re.escape, markers))
        pattern = re.compile(rf"([\u4e00-\u9fff]{{1,8}})({marker_re})[^。！？!?；;\n]{{0,18}}")
        updated = window
        applied: list[dict[str, str]] = []
        for match in list(pattern.finditer(updated))[:4]:
            subject = match.group(1)
            marker = match.group(2)
            if subject in {"她", "他", "它"}:
                continue
            if marker in {"知道", "明白", "意识到", "认定"}:
                replacement = f"{subject}的目光在痕迹上停了一下"
            elif marker in {"心里", "心中", "脑中", "暗想", "觉得"}:
                replacement = f"{subject}的动作顿了一下"
            else:
                replacement = f"{subject}的指节收紧"
            updated = updated.replace(match.group(0), replacement, 1)
            applied.append({"from": match.group(0), "to": replacement, "mode": "inner_access_externalized"})
        return updated, applied

    @staticmethod
    def _cleanup_spacing(value: str) -> str:
        updated = re.sub(r"，{2,}", "，", value)
        updated = re.sub(r"。{2,}", "。", updated)
        updated = re.sub(r"，\s*。", "。", updated)
        updated = re.sub(r"\s+", " ", updated).strip() if "\n" not in updated else updated
        return updated

    @staticmethod
    def _remove_adjacent_restatement(window: str) -> str:
        sentences = re.findall(r"[^。！？!?；;\n]+[。！？!?；;]?", window)
        if len(sentences) < 2:
            return window
        result: list[str] = []
        previous_clean = ""
        changed = False
        for sentence in sentences:
            clean = re.sub(r"[\W_]+", "", sentence)
            overlap = (
                previous_clean
                and clean
                and len(set(clean) & set(previous_clean)) / max(len(set(clean) | set(previous_clean)), 1)
            )
            if overlap and overlap >= 0.72:
                changed = True
                continue
            result.append(sentence)
            previous_clean = clean
        return "".join(result) if changed and result else window

    @staticmethod
    def _vary_sentence_shape(command: ToolCommand, text: str) -> tuple[str, dict[str, Any]]:
        target = command.target_span.strip()
        window = target if target and target in text else ""
        if not window and command.window_start and command.window_end:
            start = text.find(command.window_start)
            end = text.find(command.window_end, start + len(command.window_start)) if start >= 0 else -1
            if start >= 0 and end >= 0:
                end += len(command.window_end)
                window = text[start:end]
        if not window:
            return text, {"accepted": False, "changed": False, "reason": "missing_sentence_shape_window", "failures": ["missing_sentence_shape_window"]}

        updated = ToolExecutor._rebalance_sentence_window(window)
        if updated == window:
            return text, {
                "accepted": False,
                "changed": False,
                "reason": "sentence_shape_not_changed",
                "failures": ["sentence_shape_not_changed"],
            }
        return text.replace(window, updated, 1), {"accepted": True, "changed": True}

    @staticmethod
    def _rebalance_sentence_window(window: str) -> str:
        if "\n\n" in window:
            parts = [part.strip() for part in window.split("\n\n") if part.strip()]
            if len(parts) >= 2:
                first = parts[0]
                second = parts[1]
                if "，" in first:
                    first = first.replace("，", "。\n\n", 1)
                elif "," in first:
                    first = first.replace(",", ".\n\n", 1)
                elif len(first) > 30:
                    midpoint = len(first) // 2
                    first = first[:midpoint].rstrip("，,") + "。\n\n" + first[midpoint:].lstrip()
                return "\n\n".join([first, second, *parts[2:]])
        for punct in ("，", ",", "；", ";"):
            index = window.find(punct, max(8, len(window) // 4))
            if index > 0:
                replacement = "。\n\n" if punct in {"，", "；"} else ".\n\n"
                return window[:index] + replacement + window[index + 1:].lstrip()
        if len(window) > 40:
            midpoint = len(window) // 2
            return window[:midpoint].rstrip() + "。\n\n" + window[midpoint:].lstrip()
        return window

    @staticmethod
    def _ensure_required_spans(command: ToolCommand, text: str) -> tuple[str, dict[str, Any]]:
        required = ToolExecutor._condition_string_list(command.postconditions.get("required_spans"))
        if not required:
            required = ToolExecutor._condition_string_list(command.preconditions.get("required_spans"))
        forbidden = ToolExecutor._condition_string_list(command.postconditions.get("forbidden_spans"))
        if not forbidden:
            forbidden = ToolExecutor._condition_string_list(command.preconditions.get("forbidden_spans"))

        missing_before = [span for span in required if span and span not in text]
        forbidden_before = [span for span in forbidden if span and span in text]
        if not missing_before and not forbidden_before:
            return text, {
                "accepted": True,
                "changed": False,
                "required_missing_before": [],
                "forbidden_present_before": [],
            }

        updated = text
        applied: list[dict[str, str]] = []
        replacements = ToolExecutor._entity_replacements(command, required, forbidden)
        for old, new in replacements:
            if not old or not new or old == new or old not in updated:
                continue
            updated = updated.replace(old, new)
            applied.append({"from": old, "to": new, "mode": "entity_replace"})

        missing_after_replace = [span for span in required if span and span not in updated]
        if missing_after_replace:
            updated2, insertion = ToolExecutor._insert_missing_required_spans(
                updated,
                required,
                missing_after_replace,
            )
            if updated2 != updated:
                updated = updated2
                applied.append({"from": "", "to": insertion, "mode": "entity_insert"})

        missing_after = [span for span in required if span and span not in updated]
        forbidden_after = [span for span in forbidden if span and span in updated]
        if missing_after or forbidden_after:
            return text, {
                "accepted": False,
                "changed": False,
                "reason": "required_or_forbidden_spans_unresolved",
                "failures": ["required_or_forbidden_spans_unresolved"],
                "required_missing_before": missing_before,
                "required_missing_after": missing_after,
                "forbidden_present_before": forbidden_before,
                "forbidden_present_after": forbidden_after,
                "applied": applied,
            }

        return updated, {
            "accepted": True,
            "changed": updated != text,
            "required_missing_before": missing_before,
            "required_missing_after": [],
            "forbidden_present_before": forbidden_before,
            "forbidden_present_after": [],
            "applied": applied,
        }

    @staticmethod
    def _condition_string_list(value: Any) -> list[str]:
        if isinstance(value, str):
            values = [value]
        elif isinstance(value, (list, tuple, set)):
            values = list(value)
        else:
            return []
        result: list[str] = []
        for item in values:
            text = str(item or "").strip()
            if text and text not in result:
                result.append(text)
        return result

    @staticmethod
    def _entity_replacements(
        command: ToolCommand,
        required: list[str],
        forbidden: list[str],
    ) -> list[tuple[str, str]]:
        raw = command.preconditions.get("entity_replacements") or command.postconditions.get("entity_replacements")
        replacements: list[tuple[str, str]] = []
        if isinstance(raw, list):
            for item in raw:
                if not isinstance(item, dict):
                    continue
                old = str(item.get("from") or item.get("old") or "").strip()
                new = str(item.get("to") or item.get("new") or "").strip()
                if old and new:
                    replacements.append((old, new))
        if replacements:
            return replacements
        if forbidden and required:
            for index, old in enumerate(forbidden):
                new = required[min(index, len(required) - 1)]
                if old and new and old != new:
                    replacements.append((old, new))
        return replacements

    @staticmethod
    def _insert_missing_required_spans(
        text: str,
        required: list[str],
        missing: list[str],
    ) -> tuple[str, str]:
        present_required = [span for span in required if span and span in text]
        insertion = "、" + "、".join(span for span in missing if span)
        if not insertion.strip("、"):
            return text, ""
        for anchor in sorted(present_required, key=len, reverse=True):
            index = text.find(anchor)
            if index < 0:
                continue
            insert_at = index + len(anchor)
            return text[:insert_at] + insertion + text[insert_at:], insertion
        return text, ""

    @staticmethod
    def _postcheck_work_unit(unit: RepairWorkUnit, scene_texts: dict[int, str]) -> dict[str, Any]:
        for criterion in unit.tool_batch.acceptance_criteria:
            if not isinstance(criterion, dict):
                continue
            scene_index = criterion.get("scene_index")
            if not isinstance(scene_index, int):
                scene_index = unit.owner_scene if unit.owner_scene is not None else (
                    unit.target_scenes[0] if unit.target_scenes else None
                )
            if scene_index is None or scene_index not in scene_texts:
                return {
                    "accepted": False,
                    "reason": "missing_target_scene",
                    "failures": ["missing_target_scene"],
                }
            check = ToolExecutor._check_conditions(criterion, scene_texts.get(scene_index, ""), scene_index)
            if not check["accepted"]:
                return check
        return {"accepted": True, "failures": []}
