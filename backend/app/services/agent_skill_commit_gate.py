"""Final Skill 2.0 validation boundary before chapter commit."""
from __future__ import annotations

import re
from typing import Any

from app.models.agent_skill import CompiledSkillPacket
from app.services.agent_skill_candidate_pipeline import SkillContractEnvelope
from app.services.metric_registry import MetricRegistry


class AgentSkillCommitGate:
    """Recompile and enforce Writer skills against the final chapter text."""

    _SCENE_VALIDATORS = {
        "scene_evidence",
        "scene_structure",
        "pov_consistency",
        "tense_consistency",
        "specificity_budget",
    }

    async def evaluate(
        self,
        *,
        project_id: str,
        db,
        final_text: str,
        scene_contract: dict | None = None,
        chapter_state: dict | None = None,
        writing_mode_profile: dict | None = None,
        style_context: dict | None = None,
        max_tokens: int = 16000,
        packet: CompiledSkillPacket | None = None,
        llm: Any | None = None,
        scene_units: list[dict] | None = None,
        allow_llm_repair: bool = True,
    ) -> dict:
        from app.services.agent_skill_context import get_skill_context_builder
        from app.services.agent_skill_runtime import get_skill_runtime

        runtime = get_skill_runtime()
        scene_contract = scene_contract or {}
        chapter_state = chapter_state or {}
        writing_mode_profile = writing_mode_profile or {}
        style_context = style_context or {}

        if packet is None:
            selection_context = await get_skill_context_builder().build(
                agent_name="core_generation",
                project_id=project_id,
                scene_contract=scene_contract,
                scene_context_package={"chapter_state": chapter_state},
                writing_mode_profile=writing_mode_profile,
                quality_extensions=scene_contract.get("quality_extensions") or {},
            )
            packet = await runtime.prepare(
                agent_name="core_generation",
                context=selection_context,
                project_id=project_id,
                db=db,
            )

        validation_context = {
            "scene_contract": scene_contract,
            "chapter_state": chapter_state,
            "literary_quality_contract": scene_contract.get(
                "literary_quality_contract", {}
            ),
            "writing_mode_profile": writing_mode_profile,
            "style_context": style_context,
            "scene_elements": (
                scene_contract.get("scene_elements")
                or scene_contract.get("key_elements")
                or []
            ),
            "output_surface": "final_chapter",
            "final_structure_policy": {
                "scene_markers": "forbidden",
            },
        }
        chapter_packet = self._packet_for_validators(
            packet,
            [item for item in packet.validators if item not in self._SCENE_VALIDATORS],
        )
        scene_packet = self._packet_for_validators(
            packet,
            [item for item in packet.validators if item in self._SCENE_VALIDATORS],
        )
        initial_bundle = await self._validate_text(
            runtime=runtime,
            chapter_packet=chapter_packet,
            scene_packet=scene_packet,
            text=final_text,
            scene_units=scene_units or [],
            base_context=validation_context,
        )
        chapter_initial = initial_bundle["chapter_validation"]
        scene_results = initial_bundle["scene_validations"]
        initial = initial_bundle["validation"]
        envelope = SkillContractEnvelope.from_packet(packet, initial, validation_context)
        validation_context["skill_contract_envelope"] = envelope.to_trace()
        trace = {
            "active_skills": packet.active_skills,
            "validators": packet.validators,
            "contract_envelope": envelope.to_trace(),
            "initial_validation": initial,
            "chapter_validation": chapter_initial,
            "scene_validations": scene_results,
            "repair": {"attempted": False},
        }
        if initial.get("passed"):
            return {
                "allowed": True,
                "text": final_text,
                "reason": "",
                "trace": trace,
            }

        retry_policy = self._retry_policy(initial)
        if not allow_llm_repair:
            trace["repair"] = {
                "attempted": False,
                "disabled": True,
                "reason": "final_acceptance_validation_only",
            }
            return self._blocked(final_text, initial, trace)

        if int(retry_policy.get("max_retries") or 0) < 1:
            return self._blocked(final_text, initial, trace)

        if llm is None:
            from app.agents.core_generation import CoreGenerationAgent

            llm = await CoreGenerationAgent().get_llm_client()

        from app.services.agent_skill_protection import get_skill_protection
        from app.services.agent_skill_repair_dispatcher import (
            get_skill_repair_dispatcher,
        )

        dispatch = await get_skill_repair_dispatcher().dispatch(
            packet,
            validation=initial,
            generated_text=final_text,
            context=validation_context,
            llm=llm,
            max_tokens=max_tokens,
            validate_candidate=lambda text: self._validate_candidate(
                runtime=runtime,
                chapter_packet=chapter_packet,
                scene_packet=scene_packet,
                text=text,
                scene_units=scene_units or [],
                base_context=validation_context,
            ),
        )
        dispatch_trace = {
            key: value
            for key, value in dispatch.items()
            if key != "text"
        }
        trace["repair"] = {
            "attempted": True,
            "action": retry_policy.get("action", "repair"),
            "dispatch": dispatch_trace,
            "accepted": False,
        }
        if not dispatch.get("completed"):
            return self._blocked(final_text, initial, trace)

        raw_candidate = str(dispatch.get("text") or "").strip()
        candidate = self._strip_final_scene_markers(raw_candidate)
        if candidate != raw_candidate:
            trace["repair"]["structure_cleanup"] = {
                "removed_scene_markers": True,
                "raw_length": len(raw_candidate),
                "cleaned_length": len(candidate),
            }
        protection = await get_skill_protection().audit(
            original_text=final_text,
            candidate_text=candidate,
            context=validation_context,
            structural_check=self._final_text_structure,
        )
        trace["repair"]["protection_check"] = protection
        if not protection.get("ok"):
            return self._blocked(final_text, initial, trace)

        repaired_bundle = await self._validate_text(
            runtime=runtime,
            chapter_packet=chapter_packet,
            scene_packet=scene_packet,
            text=candidate,
            scene_units=scene_units or [],
            base_context=validation_context,
        )
        repaired_validation = repaired_bundle["validation"]
        trace["repair"]["chapter_validation"] = repaired_bundle["chapter_validation"]
        trace["repair"]["scene_validations"] = repaired_bundle["scene_validations"]
        trace["repair"]["validation"] = repaired_validation
        if not repaired_validation.get("passed"):
            return self._blocked(final_text, repaired_validation, trace)

        trace["repair"]["accepted"] = True
        return {
            "allowed": True,
            "text": candidate,
            "reason": "",
            "trace": trace,
        }

    async def _validate_candidate(
        self,
        *,
        runtime,
        chapter_packet: CompiledSkillPacket,
        scene_packet: CompiledSkillPacket,
        text: str,
        scene_units: list[dict],
        base_context: dict,
    ) -> dict:
        bundle = await self._validate_text(
            runtime=runtime,
            chapter_packet=chapter_packet,
            scene_packet=scene_packet,
            text=self._strip_final_scene_markers(text),
            scene_units=scene_units,
            base_context=base_context,
        )
        return bundle["validation"]

    async def _validate_text(
        self,
        *,
        runtime,
        chapter_packet: CompiledSkillPacket,
        scene_packet: CompiledSkillPacket,
        text: str,
        scene_units: list[dict],
        base_context: dict,
    ) -> dict:
        chapter_validation = await runtime.validate_output(
            chapter_packet,
            generated_text=text,
            context=base_context,
        )
        candidate_scene_units = self._scene_units_for_text(text, scene_units)
        scene_validations = await self._validate_scene_units(
            runtime=runtime,
            packet=scene_packet,
            scene_units=candidate_scene_units,
            base_context=base_context,
        )
        return {
            "chapter_validation": chapter_validation,
            "scene_validations": scene_validations,
            "validation": self._merge_validations(chapter_validation, scene_validations),
        }

    @classmethod
    def _scene_units_for_text(cls, text: str, scene_units: list[dict]) -> list[dict]:
        if not scene_units:
            return []
        parts = cls._split_text_by_scene_units(text, scene_units)
        rebuilt: list[dict] = []
        for index, unit in enumerate(scene_units):
            rebuilt.append({
                **unit,
                "text": parts[index] if index < len(parts) else "",
            })
        return rebuilt

    @staticmethod
    def _split_text_by_scene_units(text: str, scene_units: list[dict]) -> list[str]:
        unit_count = len(scene_units)
        if unit_count <= 0:
            return []
        stripped = (text or "").strip()
        if unit_count == 1:
            return [stripped]
        original_lengths = [max(len(str(unit.get("text") or "")), 1) for unit in scene_units]
        total_original = max(sum(original_lengths), 1)
        target_cuts: list[float] = []
        running = 0
        for length in original_lengths[:-1]:
            running += length
            target_cuts.append(running / total_original)

        paragraphs = [part.strip() for part in re.split(r"\n{2,}", stripped) if part.strip()]
        if len(paragraphs) < unit_count:
            return AgentSkillCommitGate._split_text_by_char_ratio(stripped, target_cuts, unit_count)

        total_chars = max(sum(len(part) for part in paragraphs), 1)
        parts: list[str] = []
        current: list[str] = []
        consumed = 0
        cut_index = 0
        for paragraph_index, paragraph in enumerate(paragraphs):
            current.append(paragraph)
            consumed += len(paragraph)
            remaining_paragraphs = len(paragraphs) - paragraph_index - 1
            remaining_parts = unit_count - len(parts) - 1
            if cut_index < len(target_cuts) and remaining_paragraphs >= remaining_parts:
                if consumed / total_chars >= target_cuts[cut_index]:
                    parts.append("\n\n".join(current).strip())
                    current = []
                    cut_index += 1
        parts.append("\n\n".join(current).strip())
        while len(parts) < unit_count:
            parts.append("")
        if len(parts) > unit_count:
            parts = parts[: unit_count - 1] + ["\n\n".join(parts[unit_count - 1:]).strip()]
        return parts

    @staticmethod
    def _split_text_by_char_ratio(text: str, target_cuts: list[float], unit_count: int) -> list[str]:
        stripped = (text or "").strip()
        if unit_count <= 1:
            return [stripped]
        parts: list[str] = []
        start = 0
        total = len(stripped)
        for ratio in target_cuts:
            raw_cut = int(total * ratio)
            cut = max(start, min(raw_cut, total))
            window_start = max(start, cut - 80)
            window_end = min(total, cut + 80)
            window = stripped[window_start:window_end]
            punctuation_offsets = [
                window.rfind(mark, 0, max(cut - window_start, 0) + 1)
                for mark in ("。", "！", "？", ".", "!", "?")
            ]
            punctuation_offsets = [item for item in punctuation_offsets if item >= 0]
            if punctuation_offsets:
                cut = window_start + max(punctuation_offsets) + 1
            parts.append(stripped[start:cut].strip())
            start = cut
        parts.append(stripped[start:].strip())
        while len(parts) < unit_count:
            parts.append("")
        return parts[:unit_count]

    @staticmethod
    def _retry_policy(validation: dict) -> dict:
        policies = [
            item.get("retry_policy")
            for item in validation.get("failures") or []
            if isinstance(item.get("retry_policy"), dict)
        ]
        if not policies:
            return {}
        return max(
            policies,
            key=lambda item: int(item.get("max_retries") or 0),
        )

    @staticmethod
    def _final_text_structure(text: str) -> dict:
        stripped = (text or "").strip()
        return {
            "ok": bool(stripped) and "[[SCENE:" not in stripped,
            "empty": not bool(stripped),
            "residual_scene_marker": "[[SCENE:" in stripped,
        }

    @staticmethod
    def _strip_final_scene_markers(text: str) -> str:
        cleaned = re.sub(r"\[\[SCENE:[^\]]+\]\]", "", text or "")
        cleaned = re.sub(r"<<<SCENE_ID:[^>]+>>>", "", cleaned)
        cleaned = re.sub(r"(?im)^\s*Scene\s*\d+\s*[:\uFF1A].*$", "", cleaned)
        cleaned = re.sub(r"(?im)^\s*Chapter\s*\d+\s*Scene\s*[:\uFF1A].*$", "", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned.strip()

    @classmethod
    def _packet_for_validators(
        cls,
        packet: CompiledSkillPacket,
        validators: list[str],
    ) -> CompiledSkillPacket:
        validator_set = set(validators)
        return packet.model_copy(update={
            "validators": validators,
            "validation_contracts": {
                key: value
                for key, value in packet.validation_contracts.items()
                if key in validator_set
            },
            "repair_hooks": {
                key: value
                for key, value in packet.repair_hooks.items()
                if key in validator_set
            },
        })

    async def _validate_scene_units(
        self,
        *,
        runtime,
        packet: CompiledSkillPacket,
        scene_units: list[dict],
        base_context: dict,
    ) -> list[dict]:
        if not packet.validators:
            return []
        results: list[dict] = []
        for index, unit in enumerate(scene_units):
            text = str(unit.get("text") or "").strip()
            contract = unit.get("scene_contract") or {}
            if not text:
                continue
            context = {
                **base_context,
                "scene_contract": contract,
                "writing_mode_profile": unit.get("writing_mode_profile") or {},
                "scene_elements": (
                    contract.get("scene_elements")
                    or contract.get("key_elements")
                    or []
                ),
                "output_surface": "final_scene",
                "scene_index": index,
            }
            validation = await runtime.validate_output(
                packet,
                generated_text=text,
                context=context,
            )
            results.append({
                "scene_index": index,
                **validation,
            })
        return results

    @staticmethod
    def _merge_validations(chapter: dict, scene_results: list[dict]) -> dict:
        validators = dict(chapter.get("validators") or {})
        failures = list(chapter.get("failures") or [])
        for scene_result in scene_results:
            scene_index = scene_result.get("scene_index")
            for validator_id, result in (scene_result.get("validators") or {}).items():
                validators[f"scene_{scene_index + 1}:{validator_id}"] = result
            for finding in scene_result.get("failures") or []:
                failures.append({
                    **finding,
                    "scene_index": scene_index,
                })
        return {
            "passed": bool(chapter.get("passed")) and all(
                result.get("passed") for result in scene_results
            ),
            "validators": validators,
            "failures": failures,
            "requested": [
                *(chapter.get("requested") or []),
                *[
                    f"scene_{result.get('scene_index') + 1}:{validator}"
                    for result in scene_results
                    for validator in result.get("requested") or []
                ],
            ],
        }

    @staticmethod
    def _blocked(text: str, validation: dict, trace: dict) -> dict:
        failures = validation.get("failures") or []
        summary = ", ".join(
            MetricRegistry.format_failure_summary(
                item.get("validator", ""), item.get("metric", "")
            )
            for item in failures[:5]
        )
        return {
            "allowed": False,
            "text": text,
            "reason": f"final skill validation failed ({summary or 'unknown'})",
            "trace": trace,
        }


_gate: AgentSkillCommitGate | None = None


def get_agent_skill_commit_gate() -> AgentSkillCommitGate:
    global _gate
    if _gate is None:
        _gate = AgentSkillCommitGate()
    return _gate
