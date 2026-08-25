"""Shared Skill contract envelope and candidate convergence policy.

This module is not a repair executor. It carries the Skill contract through
prose mutation stages and classifies candidate regressions before any text can
be committed.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Callable

from app.models.agent_skill import CompiledSkillPacket


FindingKey = tuple[str, str]

_FATAL_VALIDATOR_PREFIXES = (
    "fact",
    "contract",
    "scene_alignment",
    "protected",
)

_FATAL_METRIC_FRAGMENTS = (
    "marker",
    "protected",
    "fact",
    "contract",
    "scene_alignment",
    "missing_scene",
)


@dataclass(frozen=True)
class SkillContractEnvelope:
    """Compact contract summary carried by all prose mutation stages."""

    active_skills: list[str]
    validators: list[str]
    initial_failures: list[dict]
    context_keys: list[str]

    @classmethod
    def from_packet(
        cls,
        packet: CompiledSkillPacket,
        validation: dict,
        context: dict | None = None,
    ) -> "SkillContractEnvelope":
        return cls(
            active_skills=list(packet.active_skills or []),
            validators=list(packet.validators or []),
            initial_failures=list(validation.get("failures") or []),
            context_keys=sorted((context or {}).keys()),
        )

    def to_trace(self) -> dict:
        return {
            "active_skills": self.active_skills,
            "validators": self.validators,
            "initial_failure_count": len(self.initial_failures),
            "context_keys": self.context_keys,
            "role": "skill_contract_envelope",
        }


class SkillCandidatePipeline:
    """Assess candidate validation deltas under a Skill contract."""

    def __init__(
        self,
        *,
        normalize_hook: Callable[[Any], str],
        has_executable_hook: Callable[[str], bool],
    ) -> None:
        self._normalize_hook = normalize_hook
        self._has_executable_hook = has_executable_hook

    def assess(self, before: dict, after: dict, target_failures: list[dict]) -> dict:
        before_failures = before.get("failures") or []
        after_failures = after.get("failures") or []
        before_map = {self.finding_key(item): item for item in before_failures}
        after_map = {self.finding_key(item): item for item in after_failures}
        target_keys = {self.finding_key(item) for item in target_failures}

        target_before = sum(self.finding_distance(before_map[key]) for key in target_keys)
        target_after = sum(
            self.finding_distance(after_map[key])
            for key in target_keys
            if key in after_map
        )
        target_improved = target_after + 1e-9 < target_before
        target_resolved = all(key not in after_map for key in target_keys)

        new_keys = sorted(set(after_map) - set(before_map))
        regressed_target_keys = sorted(
            key
            for key in target_keys & set(after_map)
            if self.finding_distance(after_map[key]) > self.finding_distance(before_map[key])
        )
        regressed_keys = sorted(
            key
            for key in set(before_map) & set(after_map)
            if key not in target_keys
            and self.finding_distance(after_map[key]) > self.finding_distance(before_map[key])
        )

        regression_keys = new_keys + regressed_target_keys + regressed_keys
        fatal_regressions = [
            key for key in regression_keys if self._is_fatal_regression(after_map.get(key) or {})
        ]
        repairable_regressions = [
            key
            for key in regression_keys
            if key not in fatal_regressions and self._is_repairable(after_map.get(key) or {})
        ]
        unresolved_regressions = [
            key
            for key in regression_keys
            if key not in fatal_regressions and key not in repairable_regressions
        ]

        accepted = bool(after.get("passed")) or (target_improved and not regression_keys)
        staged = (
            not accepted
            and target_improved
            and not fatal_regressions
            and not unresolved_regressions
            and not regressed_target_keys
            and bool(repairable_regressions)
        )

        if accepted:
            status = "accepted"
        elif staged:
            status = "staged_repairable_regression"
        elif fatal_regressions or unresolved_regressions or regressed_target_keys or regressed_keys or new_keys:
            status = "rejected_regression"
        else:
            status = "rejected_no_progress"

        return {
            "accepted": accepted,
            "staged": staged,
            "status": status,
            "before_score": self.validation_score(before),
            "after_score": self.validation_score(after),
            "target_resolved": target_resolved,
            "target_distance_before": target_before,
            "target_distance_after": target_after,
            "new_failures": [self.format_finding_key(key) for key in new_keys],
            "regressed_target_failures": [self.format_finding_key(key) for key in regressed_target_keys],
            "regressed_failures": [self.format_finding_key(key) for key in regressed_keys],
            "fatal_regressions": [self.format_finding_key(key) for key in fatal_regressions],
            "repairable_regressions": [self.format_finding_key(key) for key in repairable_regressions],
            "unresolved_regressions": [self.format_finding_key(key) for key in unresolved_regressions],
            "cleanup_hooks": self._cleanup_hooks(
                [after_map[key] for key in repairable_regressions if key in after_map]
            ),
            "cleanup_steps": self._cleanup_steps(
                [after_map[key] for key in repairable_regressions if key in after_map]
            ),
        }

    def is_recoverable_decision(self, decision: dict) -> bool:
        return not (
            decision.get("fatal_regressions")
            or decision.get("unresolved_regressions")
            or decision.get("regressed_target_failures")
        )

    def is_better_validation(self, candidate: dict, incumbent: dict) -> bool:
        return self.validation_score(candidate) < self.validation_score(incumbent)

    def validation_score(self, validation: dict) -> tuple[int, float, int]:
        failures = validation.get("failures") or []
        fatal_count = sum(1 for item in failures if self._is_fatal_regression(item))
        distance = sum(self.finding_distance(item) for item in failures)
        return (fatal_count, distance, len(failures))

    @staticmethod
    def finding_key(finding: dict) -> FindingKey:
        return (str(finding.get("validator") or ""), str(finding.get("metric") or ""))

    @staticmethod
    def format_finding_key(key: FindingKey) -> str:
        return f"{key[0]}:{key[1]}"

    @staticmethod
    def finding_distance(finding: dict) -> float:
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

    def _is_repairable(self, finding: dict) -> bool:
        retry_policy = finding.get("retry_policy") if isinstance(finding.get("retry_policy"), dict) else {}
        hook = self._normalize_hook(finding.get("action") or retry_policy.get("action"))
        return bool(hook and self._has_executable_hook(hook))

    def _cleanup_hooks(self, findings: list[dict]) -> list[str]:
        hooks: list[str] = []
        for finding in findings:
            retry_policy = finding.get("retry_policy") if isinstance(finding.get("retry_policy"), dict) else {}
            hook = self._normalize_hook(finding.get("action") or retry_policy.get("action"))
            if hook and self._has_executable_hook(hook) and hook not in hooks:
                hooks.append(hook)
        return hooks

    def _cleanup_steps(self, findings: list[dict]) -> list[dict]:
        steps: list[dict] = []
        seen: set[tuple[str, str, str]] = set()
        for finding in findings:
            retry_policy = finding.get("retry_policy") if isinstance(finding.get("retry_policy"), dict) else {}
            hook = self._normalize_hook(finding.get("action") or retry_policy.get("action"))
            if not hook or not self._has_executable_hook(hook):
                continue
            validator = str(finding.get("validator") or "")
            metric = str(finding.get("metric") or "")
            key = (hook, validator, metric)
            if key in seen:
                continue
            seen.add(key)
            steps.append({
                "hook": hook,
                "metrics": [metric] if metric else [],
                "validators": [validator] if validator else [],
                "failures": [self._compact_cleanup_finding(finding)],
            })
        return steps

    @staticmethod
    def _compact_cleanup_finding(finding: dict) -> dict:
        allowed = {
            "validator",
            "metric",
            "actual",
            "expected",
            "expected_min",
            "expected_max",
            "action",
            "scene_index",
            "retry_policy",
        }
        return {
            key: value
            for key, value in finding.items()
            if key in allowed and value is not None
        }

    @staticmethod
    def _is_fatal_regression(finding: dict) -> bool:
        validator = str(finding.get("validator") or "").lower()
        metric = str(finding.get("metric") or "").lower()
        if validator.startswith(_FATAL_VALIDATOR_PREFIXES):
            return True
        return any(fragment in metric for fragment in _FATAL_METRIC_FRAGMENTS)


class ReviewCandidatePipeline:
    """Reject FBI recheck candidates that introduce measurable regressions."""

    _SEVERITY_DISTANCE = {
        "low": 1.0,
        "medium": 2.0,
        "high": 3.0,
        "critical": 4.0,
        "blocking": 4.0,
    }
    _COUNT_PATTERNS = (
        re.compile(r"全文\s*(\d+)\s*处"),
        re.compile(r"抽象解释超过预算[：:]\s*(\d+)\s*处"),
        re.compile(r"(\d+)\s*处"),
    )
    _DENSITY_PATTERN = re.compile(r"每千字约\s*([\d.]+)\s*处")

    def assess(
        self,
        *,
        before_violations: list[dict],
        after_violations: list[dict],
        target_violations: list[dict],
        classify_lane: Callable[[dict], str],
    ) -> dict:
        before = self._summarize(before_violations, classify_lane)
        after = self._summarize(after_violations, classify_lane)
        target_types = {
            str(item.get("type") or "unknown").lower()
            for item in target_violations
        }

        target_before = sum(
            score for (lane, issue_type), score in before.items()
            if lane == "content_blocking" and issue_type in target_types
        )
        target_after = sum(
            score for (lane, issue_type), score in after.items()
            if lane == "content_blocking" and issue_type in target_types
        )
        target_improved = target_after + 1e-9 < target_before
        target_resolved = target_after <= 0

        new_content = sorted(
            issue_type
            for (lane, issue_type), score in after.items()
            if lane == "content_blocking"
            and score > before.get((lane, issue_type), 0.0) + 1e-9
            and issue_type not in target_types
        )
        regressed_targets = sorted(
            issue_type
            for issue_type in target_types
            if after.get(("content_blocking", issue_type), 0.0)
            > before.get(("content_blocking", issue_type), 0.0) + 1e-9
        )
        quality_regressions = sorted(
            issue_type
            for (lane, issue_type), score in after.items()
            if lane == "style_advisory"
            and score > before.get((lane, issue_type), 0.0) + 1e-9
        )

        accepted = (
            target_improved
            and not new_content
            and not regressed_targets
            and not quality_regressions
        )
        if accepted:
            status = "accepted"
        elif new_content or regressed_targets:
            status = "rejected_content_regression"
        elif quality_regressions:
            status = "rejected_quality_regression"
        else:
            status = "rejected_no_progress"

        return {
            "accepted": accepted,
            "status": status,
            "target_resolved": target_resolved,
            "target_distance_before": target_before,
            "target_distance_after": target_after,
            "new_content_failures": new_content,
            "regressed_target_failures": regressed_targets,
            "quality_regressions": quality_regressions,
            "atomic_disposition": "commit_candidate" if accepted else "rollback_round",
        }

    def _summarize(
        self,
        violations: list[dict],
        classify_lane: Callable[[dict], str],
    ) -> dict[tuple[str, str], float]:
        summary: dict[tuple[str, str], float] = {}
        for violation in violations:
            issue_type = str(violation.get("type") or "unknown").lower()
            lane = classify_lane(violation)
            key = (lane, issue_type)
            summary[key] = summary.get(key, 0.0) + self._distance(violation)
        return summary

    def _distance(self, violation: dict) -> float:
        detail = str(violation.get("detail") or violation.get("reason") or "")
        density_match = self._DENSITY_PATTERN.search(detail)
        if density_match:
            return float(density_match.group(1))
        for pattern in self._COUNT_PATTERNS:
            match = pattern.search(detail)
            if match:
                return float(match.group(1))
        severity = str(violation.get("severity") or "medium").lower()
        return self._SEVERITY_DISTANCE.get(severity, 2.0)
