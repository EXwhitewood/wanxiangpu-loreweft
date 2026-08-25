"""Unified routing for automatic recovery and FBI repair lanes.

The router is deterministic and side-effect free. It decides *where* an issue
should go; individual lanes still own repair execution and QualityGate owns
final acceptance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from app.models.violation import VALIDATOR_FAILURE_TYPES
from app.services.fact_progression_protocol import is_fbi_repairable_violation


RepairLane = Literal[
    "accept",
    "non_blocking",
    "validator_retry",
    "contract_repair",
    "human_governance",
    "fbi_prose",
    "deterministic",
    "scene_rewrite",
    "legacy_patch",
]


LANE_TO_STRATEGY: dict[RepairLane, str] = {
    "accept": "accept",
    "non_blocking": "accept",
    "validator_retry": "validator_retry",
    "contract_repair": "repair_contract",
    "human_governance": "blocked",
    "fbi_prose": "fbi_repair",
    "deterministic": "patch_text",
    "scene_rewrite": "rewrite_scene",
    "legacy_patch": "patch_text",
}


HARD_DETERMINISTIC_TYPES = {
    "scene_too_long",
    "forbidden_triggered",
    "forbidden_assertion_triggered",
}

STRUCTURAL_STRATEGIES = {
    "rewrite_scene",
    "rewrite_scene_with_fact_contract",
    "rewrite_scene_with_experience_contract",
}


@dataclass(slots=True)
class RepairRoute:
    lane: RepairLane
    strategy: str
    issues: list[dict] = field(default_factory=list)
    reason: str = ""
    blocking_count: int = 0
    fbi_repairable_count: int = 0
    validator_count: int = 0

    def as_dict(self) -> dict:
        return {
            "lane": self.lane,
            "strategy": self.strategy,
            "reason": self.reason,
            "blocking_count": self.blocking_count,
            "fbi_repairable_count": self.fbi_repairable_count,
            "validator_count": self.validator_count,
        }


class UnifiedRepairRouter:
    """Route enriched QualityGate violations to a single repair lane."""

    def route(self, violations: list[dict], budgets: dict | None = None, fbi_mode: str = "assist") -> RepairRoute:
        blocking = [
            v for v in violations
            if isinstance(v, dict) and v.get("blocks_commit") and not v.get("is_stale")
        ]
        if not blocking:
            return self._route("accept", [], "no blocking issues", blocking_count=0)

        validator = [
            v for v in blocking
            if self._is_validator_issue(v)
        ]
        non_validator = [v for v in blocking if v not in validator]
        if not non_validator:
            return self._route(
                "validator_retry",
                validator,
                "only validator/system issues remain",
                blocking_count=len(blocking),
                validator_count=len(validator),
            )

        classifications = {str(v.get("classification") or "") for v in non_validator}
        if classifications and classifications <= {"contract_conflict"}:
            return self._route(
                "contract_repair",
                non_validator,
                "scene contract conflict",
                blocking_count=len(blocking),
                validator_count=len(validator),
            )

        if classifications & {"alias_needed", "retcon"}:
            return self._route(
                "human_governance",
                non_validator,
                "governance decision required",
                blocking_count=len(blocking),
                validator_count=len(validator),
            )

        deterministic = [v for v in non_validator if str(v.get("type") or "") in HARD_DETERMINISTIC_TYPES]
        if deterministic and self._budget_available(budgets, "patch"):
            return self._route(
                "deterministic",
                deterministic,
                "hard deterministic text repair",
                blocking_count=len(blocking),
                validator_count=len(validator),
            )

        fbi_repairable = [v for v in non_validator if is_fbi_repairable_violation(v)]
        if fbi_mode != "off" and fbi_repairable:
            return self._route(
                "fbi_prose",
                fbi_repairable,
                "FBI prose lane handles repairable blocking text issues",
                blocking_count=len(blocking),
                fbi_repairable_count=len(fbi_repairable),
                validator_count=len(validator),
            )

        structural = [
            v for v in non_validator
            if str(v.get("suggested_strategy") or "") in STRUCTURAL_STRATEGIES
        ]
        if structural and self._budget_available(budgets, "rewrite"):
            return self._route(
                "scene_rewrite",
                structural,
                "structural scene repair",
                blocking_count=len(blocking),
                validator_count=len(validator),
            )

        if self._budget_available(budgets, "patch"):
            return self._route(
                "legacy_patch",
                non_validator,
                "fallback local patch lane",
                blocking_count=len(blocking),
                validator_count=len(validator),
            )

        return self._route(
            "human_governance",
            non_validator,
            "automatic repair budget exhausted",
            blocking_count=len(blocking),
            validator_count=len(validator),
        )

    @staticmethod
    def _route(
        lane: RepairLane,
        issues: list[dict],
        reason: str,
        blocking_count: int,
        fbi_repairable_count: int = 0,
        validator_count: int = 0,
    ) -> RepairRoute:
        return RepairRoute(
            lane=lane,
            strategy=LANE_TO_STRATEGY[lane],
            issues=issues,
            reason=reason,
            blocking_count=blocking_count,
            fbi_repairable_count=fbi_repairable_count,
            validator_count=validator_count,
        )

    @staticmethod
    def _is_validator_issue(violation: dict) -> bool:
        vtype = str(violation.get("type") or "")
        scope = str(violation.get("repair_scope") or violation.get("scope") or "")
        return (
            vtype in VALIDATOR_FAILURE_TYPES
            or scope == "validator_system"
            or bool(violation.get("is_system_issue"))
        )

    @staticmethod
    def _budget_available(budgets: dict | None, key: str) -> bool:
        if not budgets:
            return True
        used = int(budgets.get(f"{key}_used", 0) or 0)
        limit = int(budgets.get(f"{key}_limit", 0) or 0)
        return limit <= 0 or used < limit
