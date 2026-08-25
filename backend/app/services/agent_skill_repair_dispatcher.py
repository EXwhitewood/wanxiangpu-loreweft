"""Compile Skill contract failures into FBI repair runtime calls."""
from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from app.models.agent_skill import CompiledSkillPacket
from app.services.agent_skill_candidate_pipeline import (
    SkillCandidatePipeline,
    SkillContractEnvelope,
)
from app.services.fbi.skill_repair_runtime import get_fbi_skill_repair_runtime


class AgentSkillRepairDispatcher:
    """Compile failed Skill contracts into an ordered FBI repair plan.

    Skills declare standards and preferred failure actions. This dispatcher does
    not own prose repair prompts and does not behave as a repair agent; executable
    candidate generation is delegated to the FBI repair runtime. Protection and
    commit gates still decide whether a candidate may take effect.
    """

    _ACTION_ALIASES = {
        "abstract_to_sensory": "emotion_to_scene_evidence",
        "emotion_to_scene_evidence": "emotion_to_scene_evidence",
        "pair_abstract_with_evidence": "emotion_to_scene_evidence",
        "add_missing_anchor": "detail_anchor_repair",
        "select_anchor_object": "detail_anchor_repair",
        "generic_detail_to_specific_detail": "detail_anchor_repair",
        "diversify_sensory_modes": "detail_anchor_repair",
        "reduce_detail_overload": "detail_anchor_repair",
        "break_three_part": "paragraph_reconstruction",
        "break_false_range": "break_false_range",
        "tier2_decluster": "paragraph_reconstruction",
        "tier1_replace": "prose_repair",
        "exposition_to_action": "scene_restructure",
        "inject_goal": "inject_goal",
        "scene_restructure": "scene_restructure",
        "vary_sentence_length": "pacing_repair",
        "inject_breathing_paragraph": "pacing_repair",
        "inject_transition": "pacing_repair",
        "break_dense_streak": "pacing_repair",
        "fix_abrupt_shift": "pacing_repair",
        "pacing_repair": "pacing_repair",
        "fix_pov_leak": "voice_repair",
        # 通用修复（循环 #1）：correct_tense 路由从 voice_repair 改为 tense_repair
        # 根因：原路由把 correct_tense 映射到 voice_repair，但 voice_repair 的 lane rule
        #   是 POV 修复文本（"移除非 POV 内心直写"），与闪回/时间线无关，LLM 收到的
        #   专科指令与实际问题语义脱节。
        # 修复：新建 tense_repair lane，专门处理时态漂移和闪回闭合问题。
        # 通用性：适用于所有题材——闪回闭合和时序一致性规则在所有题材中一致。
        "correct_tense": "tense_repair",
        "voice_repair": "voice_repair",
        "tense_repair": "tense_repair",
        "style_repair": "style_repair",
        "prose_repair": "prose_repair",
        "paragraph_reconstruction": "paragraph_reconstruction",
        "voice_reconstruction": "voice_reconstruction",
        "detail_anchor_repair": "detail_anchor_repair",
        "fact_repair": "fact_repair",
    }

    async def dispatch(
        self,
        packet: CompiledSkillPacket,
        *,
        validation: dict,
        generated_text: str,
        context: dict,
        llm: Any,
        max_tokens: int,
        validate_candidate: Callable[[str], Awaitable[dict]] | None = None,
    ) -> dict:
        repair_plan = self._collect_repair_plan(packet, validation)
        envelope = SkillContractEnvelope.from_packet(packet, validation, context)
        candidate_pipeline = SkillCandidatePipeline(
            normalize_hook=self._normalize_hook,
            has_executable_hook=get_fbi_skill_repair_runtime().has_executable_hook,
        )
        attempts: list[dict] = []
        current_text = generated_text
        current_validation = validation
        best_text = generated_text
        best_validation = validation
        best_hooks: list[str] = []
        best_from_staged = False
        current_hooks: list[str] = []
        had_staged_candidate = False
        index = 0
        max_steps = max(len(repair_plan) + 16, 20)

        while index < len(repair_plan) and len(attempts) < max_steps:
            attempt_started = time.perf_counter()
            step = repair_plan[index]
            index += 1
            hook = step["hook"]
            step_failures = self._matching_failures(
                current_validation.get("failures") or [],
                step,
            )
            if validate_candidate is not None and not step_failures:
                attempts.append({
                    "hook": hook,
                    "status": "skipped_resolved",
                    "metrics": step.get("metrics", []),
                    "duration_ms": int((time.perf_counter() - attempt_started) * 1000),
                })
                continue

            attempt = await get_fbi_skill_repair_runtime().execute_step(
                hook=hook,
                step=step,
                failures=step_failures,
                current_text=current_text,
                context=context,
                llm=llm,
                max_tokens=max_tokens,
            )
            attempt.setdefault("hook", hook)
            attempt.setdefault("metrics", step.get("metrics", []))
            candidate_text = str(attempt.pop("text", "") or "").strip()
            if attempt.get("status") == "completed" and candidate_text:
                if validate_candidate is not None:
                    candidate_validation = await validate_candidate(candidate_text)
                    decision = candidate_pipeline.assess(
                        current_validation,
                        candidate_validation,
                        step_failures,
                    )
                    attempt["validation"] = candidate_validation
                    attempt["decision"] = decision
                    if not decision["accepted"] and not decision.get("staged"):
                        if (
                            candidate_pipeline.is_recoverable_decision(decision)
                            and candidate_pipeline.is_better_validation(
                                candidate_validation,
                                best_validation,
                            )
                        ):
                            best_text = candidate_text
                            best_validation = candidate_validation
                            best_hooks = [*current_hooks, hook]
                            best_from_staged = False
                            attempt["best_candidate"] = True
                        attempt["status"] = decision["status"]
                        attempt["duration_ms"] = int((time.perf_counter() - attempt_started) * 1000)
                        attempts.append(attempt)
                        continue
                    next_hooks = [*current_hooks, hook]
                    if decision.get("staged"):
                        had_staged_candidate = True
                        attempt["status"] = decision["status"]
                        self._append_cleanup_steps(
                            repair_plan,
                            decision.get("cleanup_steps") or [],
                            decision.get("cleanup_hooks") or [],
                            pending_from=index,
                        )
                    if (
                        decision["accepted"]
                        or candidate_pipeline.is_better_validation(
                            candidate_validation,
                            best_validation,
                        )
                    ):
                        best_text = candidate_text
                        best_validation = candidate_validation
                        best_hooks = next_hooks
                        best_from_staged = bool(decision.get("staged"))
                        attempt["best_candidate"] = True
                    current_validation = candidate_validation
                    current_hooks = next_hooks
                current_text = candidate_text
                attempt["duration_ms"] = int((time.perf_counter() - attempt_started) * 1000)
                attempts.append(attempt)
                continue
            attempt["duration_ms"] = int((time.perf_counter() - attempt_started) * 1000)
            attempts.append(attempt)

        final_passed = True if validate_candidate is None else bool(current_validation.get("passed"))
        if validate_candidate is not None and not final_passed:
            current_text = best_text
            current_validation = best_validation
            current_hooks = best_hooks
        completed = current_text != generated_text
        completed_hooks = current_hooks if validate_candidate is not None else [
            item.get("hook")
            for item in attempts
            if item.get("status") == "completed" and item.get("hook")
        ]
        return {
            "completed": completed,
            "hook": completed_hooks[-1] if completed_hooks else None,
            "hooks": completed_hooks,
            "text": current_text if completed else generated_text,
            "attempts": attempts,
            "repair_plan": [
                {key: value for key, value in step.items() if key != "failures"}
                for step in repair_plan
            ],
            "contract_envelope": envelope.to_trace(),
            "validation": current_validation,
            "convergence": {
                "final_passed": final_passed,
                "used_best_candidate_fallback": not final_passed and best_text != generated_text,
                "used_staged_candidate_fallback": (
                    not final_passed
                    and best_from_staged
                    and best_text != generated_text
                ),
                "discarded_staged_candidate": (
                    not final_passed
                    and had_staged_candidate
                    and best_text == generated_text
                ),
                "best_score": candidate_pipeline.validation_score(current_validation)
                if validate_candidate is not None
                else None,
            },
            "timing_ms": {
                "attempt_total_ms": sum(
                    int(item.get("duration_ms") or 0)
                    for item in attempts
                    if isinstance(item, dict)
                ),
                "attempt_count": len(attempts),
                "slowest_attempt_ms": max(
                    [int(item.get("duration_ms") or 0) for item in attempts if isinstance(item, dict)]
                    or [0]
                ),
            },
        }

    @staticmethod
    def _append_cleanup_steps(
        repair_plan: list[dict],
        cleanup_steps: list[dict],
        cleanup_hooks: list[str],
        *,
        pending_from: int,
    ) -> None:
        pending_keys = {
            AgentSkillRepairDispatcher._step_identity(step)
            for step in repair_plan[pending_from:]
        }
        normalized_steps: list[dict] = []
        for cleanup_step in cleanup_steps:
            hook = str(cleanup_step.get("hook") or "")
            if not hook:
                continue
            normalized_steps.append({
                "hook": hook,
                "metrics": list(cleanup_step.get("metrics") or []),
                "validators": list(cleanup_step.get("validators") or []),
                "sources": ["candidate_pipeline.cleanup"],
                "failures": list(cleanup_step.get("failures") or []),
            })
        structured_hooks = {str(step.get("hook") or "") for step in normalized_steps}
        for hook in cleanup_hooks:
            if not hook or hook in structured_hooks:
                continue
            normalized_steps.append({
                "hook": hook,
                "metrics": [],
                "validators": [],
                "sources": ["candidate_pipeline.cleanup"],
                "failures": [],
            })
        for step in normalized_steps:
            key = AgentSkillRepairDispatcher._step_identity(step)
            if key in pending_keys:
                continue
            repair_plan.append(step)
            pending_keys.add(key)

    @staticmethod
    def _step_identity(step: dict) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
        return (
            str(step.get("hook") or ""),
            tuple(sorted(str(item) for item in (step.get("metrics") or []))),
            tuple(sorted(str(item) for item in (step.get("validators") or []))),
        )

    @classmethod
    def _normalize_hook(cls, value: Any) -> str:
        hook = str(value or "").strip()
        return cls._ACTION_ALIASES.get(hook, hook)

    @classmethod
    def _has_executable_action(cls, finding: dict) -> bool:
        action = finding.get("action")
        hook = cls._normalize_hook(action)
        return bool(action and get_fbi_skill_repair_runtime().has_executable_hook(hook))

    @classmethod
    def _collect_hooks(cls, packet: CompiledSkillPacket, validation: dict) -> list[str]:
        return [step["hook"] for step in cls._collect_repair_plan(packet, validation)]

    @classmethod
    def _collect_repair_plan(cls, packet: CompiledSkillPacket, validation: dict) -> list[dict]:
        steps: list[dict] = []
        by_hook: dict[str, dict] = {}

        def add(hook_value: Any, finding: dict | None = None, *, source: str = "") -> None:
            hook = cls._normalize_hook(hook_value)
            if not hook:
                return
            step = by_hook.get(hook)
            if step is None:
                step = {"hook": hook, "metrics": [], "validators": [], "sources": [], "failures": []}
                by_hook[hook] = step
                steps.append(step)
            if source and source not in step["sources"]:
                step["sources"].append(source)
            if finding:
                metric = finding.get("metric")
                validator = finding.get("validator")
                if metric and metric not in step["metrics"]:
                    step["metrics"].append(metric)
                if validator and validator not in step["validators"]:
                    step["validators"].append(validator)
                if finding not in step["failures"]:
                    step["failures"].append(finding)

        for finding in validation.get("failures") or []:
            action = finding.get("action")
            if action:
                add(action, finding, source="finding.action")
            validator_id = finding.get("validator")
            if validator_id and not cls._has_executable_action(finding):
                for hook in packet.repair_hooks.get(str(validator_id), []):
                    add(hook, finding, source="packet.repair_hooks")
        for validator_key, result in (validation.get("validators") or {}).items():
            validator_id = result.get("validator") or validator_key
            validator_failures = [
                finding
                for finding in validation.get("failures") or []
                if finding.get("validator") == validator_id
            ]
            related_failures = [
                finding
                for finding in validator_failures
                if not cls._has_executable_action(finding)
            ]
            for hook in result.get("repair_hooks") or []:
                if related_failures:
                    for finding in related_failures:
                        add(hook, finding, source="validator.repair_hooks")
        for finding in validation.get("failures") or []:
            retry_policy = finding.get("retry_policy")
            if isinstance(retry_policy, dict):
                add(retry_policy.get("action"), finding, source="retry_policy.action")

        return steps

    @staticmethod
    def _matching_failures(failures: list[dict], step: dict) -> list[dict]:
        validators = set(step.get("validators") or [])
        metrics = set(step.get("metrics") or [])
        matched = []
        for finding in failures:
            if validators and finding.get("validator") not in validators:
                continue
            if metrics and finding.get("metric") not in metrics:
                continue
            matched.append(finding)
        return matched

    @classmethod
    def _assess_candidate(
        cls,
        before: dict,
        after: dict,
        target_failures: list[dict],
    ) -> dict:
        before_failures = before.get("failures") or []
        after_failures = after.get("failures") or []
        before_map = {cls._finding_key(item): item for item in before_failures}
        after_map = {cls._finding_key(item): item for item in after_failures}
        target_keys = {cls._finding_key(item) for item in target_failures}

        target_before = sum(cls._finding_distance(before_map[key]) for key in target_keys)
        target_after = sum(
            cls._finding_distance(after_map[key])
            for key in target_keys
            if key in after_map
        )
        new_failures = sorted(set(after_map) - set(before_map))
        regressed_target_failures = sorted(
            key
            for key in target_keys & set(after_map)
            if cls._finding_distance(after_map[key]) > cls._finding_distance(before_map[key])
        )
        regressed_failures = sorted(
            key
            for key in set(before_map) & set(after_map)
            if key not in target_keys
            and cls._finding_distance(after_map[key]) > cls._finding_distance(before_map[key])
        )
        target_improved = target_after + 1e-9 < target_before
        accepted = bool(after.get("passed")) or (
            target_improved
            and not new_failures
            and not regressed_target_failures
            and not regressed_failures
        )
        if accepted:
            status = "accepted"
        elif new_failures or regressed_target_failures or regressed_failures:
            status = "rejected_regression"
        else:
            status = "rejected_no_progress"
        return {
            "accepted": accepted,
            "status": status,
            "target_distance_before": target_before,
            "target_distance_after": target_after,
            "new_failures": [cls._format_finding_key(key) for key in new_failures],
            "regressed_target_failures": [
                cls._format_finding_key(key) for key in regressed_target_failures
            ],
            "regressed_failures": [
                cls._format_finding_key(key) for key in regressed_failures
            ],
        }

    @staticmethod
    def _finding_key(finding: dict) -> tuple[str, str]:
        return (
            str(finding.get("validator") or ""),
            str(finding.get("metric") or ""),
        )

    @staticmethod
    def _format_finding_key(key: tuple[str, str]) -> str:
        return f"{key[0]}:{key[1]}"

    @staticmethod
    def _finding_distance(finding: dict) -> float:
        actual = finding.get("actual")
        if isinstance(actual, bool):
            expected = finding.get("expected")
            return 0.0 if expected is actual else 1.0
        if not isinstance(actual, (int, float)):
            return 1.0
        if isinstance(finding.get("expected_min"), (int, float)):
            return max(float(finding["expected_min"]) - float(actual), 0.0)
        if isinstance(finding.get("expected_max"), (int, float)):
            return max(float(actual) - float(finding["expected_max"]), 0.0)
        expected = finding.get("expected")
        if isinstance(expected, (int, float)):
            return abs(float(actual) - float(expected))
        return 1.0


_dispatcher: AgentSkillRepairDispatcher | None = None


def get_skill_repair_dispatcher() -> AgentSkillRepairDispatcher:
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = AgentSkillRepairDispatcher()
    return _dispatcher
