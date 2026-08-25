"""Candidate-first success rules for chapter writer generation."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.utils.word_count import count_words


HARD_BLOCKER_TYPES = {
    "fact_conflict",
    "truth_layer_conflict",
    "identity_conflict",
    "canon_identity_confusion",
    "timeline_conflict",
    "timeline_confusion",
    "temporal_conflict",
    "temporal_layer_confusion",
    "spatial_conflict",
    "spatial_consistency_error",
    "ownership_conflict",
    "knowledge_boundary_violation",
    "pov_conflict",
    "forbidden_assertion_triggered",
    "forbidden_triggered",
    "forbidden_recap_violation",
    "forbidden_event",
    "required_ambiguity_broken",
    "ending_state_not_reached",
    "missing_must_show",
    "contract_integrity_error",
    "scene_alignment_failed",
}

SYSTEM_BLOCKER_TYPES = {
    "quality_checker_unavailable",
    "validator_system_error",
    "fcip_registration_error",
}

SOFT_QUALITY_TYPES = {
    "ai_flavor",
    "ai_style_artifact",
    "ai_punctuation_artifact",
    "explanatory_punctuation_artifact",
    "low_reading_drive",
    "low_scene_pressure",
    "passive_protagonist",
    "conflict_only_explained",
    "exposition_driven_reveal",
    "missing_dialogue_pressure",
    "weak_hook_out",
    "mode_mismatch",
    "specificity_budget_unmet",
    "info_dump_high_density",
    "info_dump_moderate_density",
    "setting_paragraph_too_long",
    "info_reveal_burst",
    "repeated_body_language",
    "emotion_expression_monotone",
    "commercial_pacing",
    "literary_quality",
    "reader_experience",
    "narrative_experience",
    "style_quality",
    "semantic_quality_error",
}

SOFT_QUALITY_SOURCES = {
    "ai_flavor",
    "deslop_gate",
    "reader_experience",
    "narrative_experience",
    "literary_quality",
    "commercial_pacing",
    "style_quality",
    "experience_quality",
    "quality_gate",
}

SOFT_BYPASS_ERROR_CODES = {
    "",
    "commit_blocked",
    "recovery_exhausted",
    "recovery_failed",
    "needs_review",
    "waiting_human_content_review",
}


def _violation_type(violation: Any) -> str:
    if not isinstance(violation, dict):
        return ""
    return str(
        violation.get("type")
        or violation.get("issue_type")
        or violation.get("code")
        or ""
    ).strip()


def _violation_source(violation: Any) -> str:
    if not isinstance(violation, dict):
        return ""
    return str(
        violation.get("source")
        or violation.get("validator")
        or violation.get("checker")
        or ""
    ).strip()


def is_hard_blocking_violation(violation: Any) -> bool:
    if not isinstance(violation, dict):
        return False
    if not violation.get("blocks_commit"):
        return False
    violation_type = _violation_type(violation)
    source = _violation_source(violation)
    if violation_type in HARD_BLOCKER_TYPES or violation_type in SYSTEM_BLOCKER_TYPES:
        return True
    if violation_type in SOFT_QUALITY_TYPES:
        return False
    if source in SOFT_QUALITY_SOURCES and violation_type not in HARD_BLOCKER_TYPES:
        return False
    severity = str(violation.get("severity") or "").lower()
    if severity in {"critical", "fatal"}:
        return True
    return False


def split_candidate_violations(violations: Any) -> dict:
    items = violations if isinstance(violations, list) else []
    hard = [v for v in items if is_hard_blocking_violation(v)]
    soft = [v for v in items if isinstance(v, dict) and v not in hard]
    return {
        "hard": hard,
        "soft": soft,
        "hard_count": len(hard),
        "soft_count": len(soft),
    }


def classify_candidate_block(
    *,
    chapter_writer_mode: bool,
    generated_text: str,
    error_code: str = "",
    commit_blocked: bool = False,
    violations: Any = None,
) -> dict:
    """Classify whether a chapter-writer candidate must block or can report only."""
    if not chapter_writer_mode:
        return {
            "block": bool(commit_blocked or error_code),
            "reason": "legacy_scene_writer_rules",
            **split_candidate_violations(violations),
        }
    if not str(generated_text or "").strip():
        return {
            "block": True,
            "reason": "empty_candidate",
            **split_candidate_violations(violations),
        }

    split = split_candidate_violations(violations)
    normalized_error = str(error_code or "")
    if normalized_error and normalized_error not in SOFT_BYPASS_ERROR_CODES:
        return {
            "block": True,
            "reason": f"hard_error_code:{normalized_error}",
            **split,
        }
    if split["hard"]:
        return {
            "block": True,
            "reason": "hard_quality_blocker",
            **split,
        }
    if commit_blocked or normalized_error:
        return {
            "block": False,
            "reason": "quality_report_only",
            **split,
        }
    return {
        "block": False,
        "reason": "passed",
        **split,
    }


def build_chapter_candidate_payload(
    *,
    chapter_number: int,
    candidate_text: str,
    scene_count: int,
    alignment: dict | None = None,
    source: str = "chapter_writer",
    context_ledger_id: str = "",
) -> dict:
    alignment = alignment if isinstance(alignment, dict) else {}
    return {
        "status": "candidate_ready",
        "source": source,
        "chapter_number": chapter_number,
        "scene_count": scene_count,
        "word_count": count_words(candidate_text or ""),
        "alignment_method": alignment.get("method", ""),
        "alignment_warnings": alignment.get("warnings", []),
        "context_ledger_id": context_ledger_id,
        "candidate_text": candidate_text or "",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
