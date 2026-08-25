from __future__ import annotations

import logging
from typing import Any

from app.models.agent_skill import CompiledSkillPacket
from app.services.chapter_commit_health import ChapterCommitHealthChecker
from app.services.fbi.final_acceptance_delta import (
    build_commit_health_case_delta,
    build_final_acceptance_case_delta,
)

_logger = logging.getLogger(__name__)


class FBIFinalAcceptanceService:
    """FBI-owned final acceptance unit.

    This service validates final candidates and produces FBI case deltas. It
    never repairs prose directly.
    """

    async def evaluate(
        self,
        *,
        project_id: str,
        chapter_number: int,
        db,
        final_text: str,
        scene_contract: dict | None = None,
        chapter_state: dict | None = None,
        writing_mode_profile: dict | None = None,
        style_context: dict | None = None,
        scene_units: list[dict] | None = None,
        scene_texts: dict[int, str] | None = None,
        style_result: dict | None = None,
        packet: CompiledSkillPacket | None = None,
        acceptance_round: int = 0,
        parent_case_id: str = "",
    ) -> dict[str, Any]:
        from app.services.agent_skill_commit_gate import get_agent_skill_commit_gate

        skill_gate = await get_agent_skill_commit_gate().evaluate(
            project_id=project_id,
            db=db,
            final_text=final_text,
            scene_contract=scene_contract or {},
            chapter_state=chapter_state or {},
            writing_mode_profile=writing_mode_profile or {},
            style_context=style_context or {},
            scene_units=scene_units or [],
            packet=packet,
            allow_llm_repair=False,
        )
        health = ChapterCommitHealthChecker().evaluate(
            final_text=skill_gate.get("text", final_text),
            scene_texts=scene_texts or {},
            chapter_state=chapter_state or {},
            style_result=style_result or {},
        )
        skill_allowed = bool(skill_gate.get("allowed", False))
        health_allowed = bool(health.get("allowed", False))

        # P0-3: 接入 ValidatorCollection，合并三套 validator 输出。
        # try/except 保护：ValidatorCollection 失败时回退到原 skill/health 二元判定。
        collection_passed = True
        unified_violations: list[dict] = []
        unified_text_hash = ""
        unified_sources: dict = {}
        try:
            from app.services.validator_collection import get_validator_collection
            validator_collection = get_validator_collection()
            collection_result = validator_collection.validate(
                text=skill_gate.get("text", final_text),
                skill_validation=skill_gate,
                health_check_result=health,
                text_hash="",
            )
            unified_violations = list(collection_result.get("violations", []))
            unified_text_hash = str(collection_result.get("text_hash", ""))
            unified_sources = dict(collection_result.get("sources", {}))
            collection_passed = bool(collection_result.get("passed", True))
        except Exception as exc:
            _logger.warning(
                "FBIFinalAcceptanceService: ValidatorCollection.validate failed, "
                "fallback to skill/health binary decision: %s",
                exc,
            )
            collection_passed = skill_allowed and health_allowed

        result = {
            **skill_gate,
            "allowed": skill_allowed and health_allowed and collection_passed,
            "commit_health": health,
            "unified_violations": unified_violations,
            "unified_text_hash": unified_text_hash,
            "unified_sources": unified_sources,
            "fbi_final_acceptance": {
                "department": "fbi",
                "unit": "final_acceptance",
                "acceptance_round": acceptance_round,
                "skill_allowed": skill_allowed,
                "commit_health_allowed": health_allowed,
                "collection_passed": collection_passed,
            },
        }
        if not result["allowed"]:
            reason_parts: list[str] = []
            if not skill_allowed:
                reason_parts.append(str(skill_gate.get("reason") or "final skill validation failed"))
            if not health_allowed:
                codes = ", ".join(
                    str(issue.get("code") or "commit_health_failed")
                    for issue in health.get("hard_issues") or []
                    if isinstance(issue, dict)
                )
                reason_parts.append(f"final commit health failed ({codes or 'unknown'})")
            result["reason"] = "; ".join(reason_parts)
            result["case_file_delta"] = self.build_delta(
                project_id=project_id,
                chapter_number=chapter_number,
                final_text=skill_gate.get("text", final_text),
                final_gate_result=result,
                scene_texts=scene_texts or {},
                acceptance_round=acceptance_round,
                parent_case_id=parent_case_id,
            )
            if not skill_allowed and health_allowed:
                blocking_delta_count = _blocking_issue_count(result.get("case_file_delta") or {})
                if blocking_delta_count == 0:
                    result["allowed"] = True
                    result["reason"] = ""
                    result["fbi_final_acceptance"]["skill_allowed"] = True
                    result["fbi_final_acceptance"]["soft_skill_failure_count"] = _all_issue_count(
                        result.get("case_file_delta") or {}
                    )
                    result["fbi_final_acceptance"]["soft_failure_policy"] = (
                        "non_blocking_low_confidence_final_delta"
                    )
        return result

    def build_delta(
        self,
        *,
        project_id: str,
        chapter_number: int,
        final_text: str,
        final_gate_result: dict[str, Any],
        scene_texts: dict[int, str] | None = None,
        acceptance_round: int = 0,
        parent_case_id: str = "",
    ) -> dict[str, Any]:
        if not final_gate_result.get("allowed", True):
            trace = final_gate_result.get("trace") or {}
            validation = trace.get("repair", {}).get("validation") or trace.get("initial_validation") or {}
            if validation.get("failures"):
                delta = build_final_acceptance_case_delta(
                    project_id=project_id,
                    chapter_number=chapter_number,
                    final_text=final_text,
                    final_gate_result=final_gate_result,
                    acceptance_round=acceptance_round,
                    parent_case_id=parent_case_id,
                    scene_texts=scene_texts or {},
                )
                health = final_gate_result.get("commit_health") or {}
                if health.get("hard_issues"):
                    health_delta = build_commit_health_case_delta(
                        project_id=project_id,
                        chapter_number=chapter_number,
                        final_text=final_text,
                        health_result=health,
                        acceptance_round=acceptance_round,
                        parent_case_id=parent_case_id,
                        scene_texts=scene_texts or {},
                    )
                    _merge_case_delta(delta, health_delta)
                return delta
        health = final_gate_result.get("commit_health") or {}
        return build_commit_health_case_delta(
            project_id=project_id,
            chapter_number=chapter_number,
            final_text=final_text,
            health_result=health,
            acceptance_round=acceptance_round,
            parent_case_id=parent_case_id,
            scene_texts=scene_texts or {},
        )


_service: FBIFinalAcceptanceService | None = None


def get_fbi_final_acceptance_service() -> FBIFinalAcceptanceService:
    global _service
    if _service is None:
        _service = FBIFinalAcceptanceService()
    return _service


def _merge_case_delta(target: dict[str, Any], incoming: dict[str, Any]) -> None:
    for bucket in ("scene_issues", "chapter_issues", "skill_failures", "quality_advisories"):
        target.setdefault(bucket, [])
        seen = {
            str(item.get("issue_id") or item.get("dedupe_key") or item)
            for item in target.get(bucket) or []
            if isinstance(item, dict)
        }
        for item in incoming.get(bucket) or []:
            if not isinstance(item, dict):
                continue
            key = str(item.get("issue_id") or item.get("dedupe_key") or item)
            if key in seen:
                continue
            seen.add(key)
            target[bucket].append(item)
    target_trace = target.setdefault("review_trace", {})
    incoming_trace = incoming.get("review_trace") or {}
    if incoming_trace:
        target_trace.setdefault("merged_final_acceptance_sources", []).append(incoming_trace)


def _all_issue_count(case_delta: dict[str, Any]) -> int:
    total = 0
    for bucket in ("scene_issues", "chapter_issues", "skill_failures", "quality_advisories"):
        values = case_delta.get(bucket) or []
        if isinstance(values, list):
            total += len([item for item in values if isinstance(item, dict)])
    return total


def _blocking_issue_count(case_delta: dict[str, Any]) -> int:
    count = 0
    for bucket in ("scene_issues", "chapter_issues", "skill_failures", "quality_advisories"):
        values = case_delta.get(bucket) or []
        if not isinstance(values, list):
            continue
        for item in values:
            if isinstance(item, dict) and item.get("blocks_commit", True):
                count += 1
    return count
