"""FBI-owned executable repair runtime for Skill contract failures.

Skills may declare validators, failure actions, and preferred repair hooks, but
they do not repair prose themselves. This runtime is the executable repair side
of that protocol: it turns an already-compiled FBI repair step into a candidate
text. Commit safety remains the responsibility of protection and commit gates.
"""
from __future__ import annotations

import re
from typing import Any

from app.services.llm_task_profiles import LLMTaskType
from app.utils.dash_artifacts import (
    DEFAULT_DASH_ARTIFACT_MAX_PER_1000,
    count_dash_artifacts,
    dash_artifact_allowed_count,
    iter_dash_artifacts,
)


class FBISkillRepairRuntime:
    """Execute Skill-triggered repair hooks as FBI repair capabilities."""

    _PROMPTS = {
        "prose_repair": (
            "You are an FBI prose repair specialist for Chinese fiction. Repair only "
            "the reported prose and AI-flavor defects. Preserve facts, names, POV, "
            "chronology, scene order, and chapter meaning. Return the complete repaired "
            "prose only."
        ),
        "style_repair": (
            "You are an FBI style repair specialist for Chinese fiction. Repair only "
            "the reported style defects while preserving plot facts, character voice, "
            "POV, scene order, and chapter meaning. Return the complete repaired prose only."
        ),
        "fact_repair": (
            "You are an FBI fact repair specialist. Correct only the reported contract "
            "or fact defects. Preserve unaffected prose, names, POV, scene order, and "
            "chapter meaning. Return the complete repaired prose only."
        ),
        "break_false_range": (
            "You are an FBI prose micro-repair specialist. Remove or rewrite only false "
            "range expressions, vague numeric spans, and artificial breadth phrases "
            "reported by the validator. Do not change plot facts, quantities that are "
            "story facts, POV, chronology, or scene order. Return the complete repaired prose only."
        ),
        "detail_anchor_repair": (
            "You are an FBI concrete-detail repair specialist. Repair only the reported "
            "specificity defects by replacing abstract or decorative description with "
            "selective POV-relevant sensory evidence tied to declared scene elements. "
            "Prefer precise nouns, active verbs, physical traces, and one dominant anchor. "
            "Do not add new plot facts, change character intent, alter POV, reorder scenes, "
            "or remove required structure. Return the complete repaired prose only."
        ),
        "emotion_to_scene_evidence": (
            "You are an FBI scene-evidence repair specialist. Repair only reported "
            "show-don't-tell defects by replacing standalone abstract claims, emotion "
            "labels, and unsupported conclusions with nearby concrete action, dialogue "
            "pressure, object traces, sensory evidence, or visible consequence. Do not "
            "add new plot facts, change POV, or alter chronology. Return the complete repaired prose only."
        ),
        "paragraph_reconstruction": (
            "You are an FBI paragraph reconstruction specialist for Chinese fiction. "
            "Repair only reported discourse-level AI flavor: paragraph isomorphism, "
            "sentence shells, semantic restatement, action-then-explanation, mirrored "
            "paragraph openings, double conclusions, or over-clean moral conclusions. "
            "You may split, merge, reorder adjacent sentences, and vary paragraph rhythm, "
            "but must preserve facts, numbers, polarity, causality, responsibility, "
            "character intent, information release, foreshadowing, POV, and chronology. "
            "Return the complete repaired prose only."
        ),
        "voice_reconstruction": (
            "You are an FBI voice reconstruction specialist. Restore narrator or character "
            "voice according to provided voice_fingerprint, style_profile, pov_character_card, "
            "or persona_card. Remove generic model action packages without adding typos, "
            "cheap slang, or random disorder. Preserve facts, polarity, causality, POV, "
            "timeline, and information release. Return the complete repaired prose only."
        ),
        "inject_goal": (
            "You are an FBI scene-goal repair specialist. Repair only a missing scene-goal "
            "signal. Add or sharpen a minimal in-scene objective through action, pressure, "
            "dialogue, or immediate tactical intent. Do not add a new plot goal, do not "
            "explain the contract, and do not change the ending state. Return the complete "
            "repaired prose only."
        ),
        "scene_restructure": (
            "You are an FBI scene-structure repair specialist. Repair only reported scene "
            "structure defects such as missing goal, weak obstacle, missing turn, pure "
            "exposition blocks, or experience breaks. You may adjust adjacent sentence "
            "and paragraph order, but must preserve facts, polarity, causality, POV, "
            "timeline, foreshadowing, and outcome contract. Return the complete repaired prose only."
        ),
        "pacing_repair": (
            "You are an FBI pacing repair specialist. Repair only reported rhythm, reader "
            "experience, commercial pacing, uniform sentence length, dense paragraph streaks, "
            "abrupt shifts, weak conflict staging, or weak curiosity engine. Use sentence "
            "breath, paragraph breaks, action-dialogue bridges, concrete obstacles, and "
            "small reversals. Preserve facts, POV, chronology, and outcome. Return the "
            "complete repaired prose only."
        ),
        "voice_repair": (
            "You are an FBI POV and voice repair specialist. Repair only reported POV leaks, "
            "head hopping, tense drift, or character voice inconsistency. Turn non-POV inner "
            "access into observable action, dialogue, object reaction, or POV inference. "
            "Preserve facts, causality, chronology, and information release. Return the "
            "complete repaired prose only."
        ),
    }

    def has_executable_hook(self, hook: str) -> bool:
        return bool(hook and hook in self._PROMPTS)

    async def execute_step(
        self,
        *,
        hook: str,
        step: dict,
        failures: list[dict],
        current_text: str,
        context: dict,
        llm: Any,
        max_tokens: int,
    ) -> dict:
        prompt = self._PROMPTS.get(hook)
        if not prompt:
            return {
                "hook": hook,
                "status": "unsupported",
                "metrics": step.get("metrics", []),
                "executor": "fbi_skill_repair_runtime",
            }

        deterministic_text = self._deterministic_repair(hook, failures, current_text)
        if deterministic_text and deterministic_text != current_text:
            return {
                "hook": hook,
                "status": "completed",
                "metrics": step.get("metrics", []),
                "text": deterministic_text,
                "executor": "fbi_skill_repair_runtime",
                "deterministic": True,
            }

        try:
            repaired_text = await llm.generate(
                system_prompt=(
                    prompt
                    + self._convergence_instruction()
                    + self._structure_policy_instruction(context)
                ),
                user_prompt=(
                    "Skill contract failures routed to FBI repair:\n"
                    f"{failures[:8]}\n\n"
                    "Current repair step:\n"
                    f"{self._compact_step(step)}\n\n"
                    "Failure targets and active constraints:\n"
                    f"{self._failure_brief(failures)}\n\n"
                    "Repair context:\n"
                    f"{self._compact_context(context)}\n\n"
                    "Original prose:\n"
                    f"{current_text}"
                ),
                temperature=0.35,
                task_type=LLMTaskType.FBI_REPAIR,
                max_tokens=max_tokens,
            )
        except Exception as exc:
            return {
                "hook": hook,
                "status": "failed",
                "metrics": step.get("metrics", []),
                "error": str(exc),
                "executor": "fbi_skill_repair_runtime",
            }

        if repaired_text and str(repaired_text).strip():
            return {
                "hook": hook,
                "status": "completed",
                "metrics": step.get("metrics", []),
                "text": str(repaired_text).strip(),
                "executor": "fbi_skill_repair_runtime",
            }
        return {
            "hook": hook,
            "status": "empty_output",
            "metrics": step.get("metrics", []),
            "executor": "fbi_skill_repair_runtime",
        }

    def _deterministic_repair(self, hook: str, failures: list[dict], text: str) -> str | None:
        if hook in {"prose_repair", "style_repair"} and self._has_metric(failures, "dash_per_1000"):
            return self._reduce_dash_density(text, failures)
        return None

    @staticmethod
    def _has_metric(failures: list[dict], metric: str) -> bool:
        return any(str(finding.get("metric") or "") == metric for finding in failures)

    @staticmethod
    def _reduce_dash_density(text: str, failures: list[dict]) -> str | None:
        punctuation = "\uff0c\u3002\uff01\uff1f\uff1b\u3001\uff1a"
        comma = "\uff0c"
        if not text or count_dash_artifacts(text) <= 0:
            return None

        expected_max = DEFAULT_DASH_ARTIFACT_MAX_PER_1000
        for finding in failures:
            if str(finding.get("metric") or "") != "dash_per_1000":
                continue
            value = finding.get("expected_max")
            if isinstance(value, (int, float)):
                expected_max = float(value)
                break

        matches = list(iter_dash_artifacts(text))
        dash_count = len(matches)
        allowed = dash_artifact_allowed_count(text, expected_max)
        if dash_count <= allowed:
            return None

        pieces: list[str] = []
        cursor = 0
        for index, match in enumerate(matches):
            start, end = match.span()
            pieces.append(text[cursor:start])
            if index < allowed:
                pieces.append(match.group(0))
            else:
                next_char = text[end:end + 1]
                pieces.append("" if next_char in punctuation else comma)
            cursor = end
        pieces.append(text[cursor:])
        return "".join(pieces)

    @staticmethod
    def _compact_context(context: dict) -> dict:
        allowed = {
            "scene_contract",
            "chapter_state",
            "must_preserve",
            "must_avoid",
            "style_directive",
            "persona_card",
            "scene_elements",
            "pov_character_card",
            "voice_fingerprint",
            "style_profile",
            "character_cards",
            "output_surface",
            "final_structure_policy",
            "skill_contract_envelope",
        }
        return {key: value for key, value in context.items() if key in allowed and value}

    @staticmethod
    def _compact_step(step: dict) -> dict:
        return {
            "hook": step.get("hook"),
            "metrics": step.get("metrics") or [],
            "validators": step.get("validators") or [],
            "sources": step.get("sources") or [],
        }

    @staticmethod
    def _failure_brief(failures: list[dict]) -> list[dict]:
        brief: list[dict] = []
        for finding in failures[:12]:
            brief.append({
                "validator": finding.get("validator"),
                "metric": finding.get("metric"),
                "actual": finding.get("actual"),
                "expected": finding.get("expected"),
                "expected_min": finding.get("expected_min"),
                "expected_max": finding.get("expected_max"),
                "action": finding.get("action"),
                "scene_index": finding.get("scene_index"),
            })
        return brief

    @staticmethod
    def _convergence_instruction() -> str:
        return (
            "\n\nConvergence policy: reduce the reported metrics without creating "
            "new failures in other active Skill contracts. Prefer the smallest "
            "local rewrite that lowers the measured distance. If a paragraph or "
            "scene needs restructuring, keep the same facts, polarity, causality, "
            "POV boundary, chronology, protected obligations, and information "
            "release. Avoid adding em dashes, generic emotional labels, mirrored "
            "paragraph openings, long exposition blocks, head hopping, or new "
            "abstract claims while repairing another metric."
        )

    @staticmethod
    def _structure_policy_instruction(context: dict) -> str:
        policy = context.get("final_structure_policy") or {}
        if (
            context.get("output_surface") == "final_chapter"
            or policy.get("scene_markers") == "forbidden"
        ):
            return (
                "\n\nOutput surface: final_chapter. Do not preserve or emit "
                "[[SCENE:...]] markers, <<<SCENE_ID:...>>> markers, markdown "
                "scene headings, or other scene labels. Preserve scene order, "
                "facts, POV, chronology, and content boundaries in prose only."
            )
        if policy.get("scene_markers") == "preserve":
            return "\n\nOutput surface requires existing scene markers to be preserved exactly."
        return ""


_runtime: FBISkillRepairRuntime | None = None


def get_fbi_skill_repair_runtime() -> FBISkillRepairRuntime:
    global _runtime
    if _runtime is None:
        _runtime = FBISkillRepairRuntime()
    return _runtime
