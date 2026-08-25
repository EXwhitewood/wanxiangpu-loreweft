"""Agent Skill Feedback - loads issue-to-skill mappings and integrates with selector.

Plan reference: Section 13 - Feedback Loop
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_FEEDBACK_MAP_PATH = Path(__file__).parent.parent / "config" / "skill_feedback_map.yaml"

_feedback_cache: dict | None = None


def load_feedback_map() -> dict:
    """Load the skill feedback map from YAML config.

    Returns:
        Dict mapping issue_type -> {activate: [skill_ids], reason: str}
    """
    global _feedback_cache
    if _feedback_cache is not None:
        return _feedback_cache

    if not _FEEDBACK_MAP_PATH.exists():
        logger.warning("Feedback map not found: %s", _FEEDBACK_MAP_PATH)
        _feedback_cache = {}
        return _feedback_cache

    try:
        import yaml
        with open(_FEEDBACK_MAP_PATH, "r", encoding="utf-8") as f:
            _feedback_cache = yaml.safe_load(f) or {}
        return _feedback_cache
    except Exception:
        logger.warning("Failed to load feedback map", exc_info=True)
        _feedback_cache = {}
        return _feedback_cache


def get_skills_for_issues(issue_types: list[str]) -> dict[str, str]:
    """Get skills that should be activated based on reported issues.

    Args:
        issue_types: List of issue type identifiers from quality checkers

    Returns:
        Dict mapping skill_id -> activation_reason
    """
    feedback_map = load_feedback_map()
    skill_reasons: dict[str, str] = {}

    for issue_type in issue_types:
        entry = feedback_map.get(issue_type)
        if entry is None:
            continue
        for skill_id in entry.get("activate", []):
            if skill_id not in skill_reasons:
                skill_reasons[skill_id] = entry.get("reason", f"issue_type={issue_type}")

    return skill_reasons


def get_all_feedback_issue_types() -> list[str]:
    """List all issue types that have feedback mappings."""
    feedback_map = load_feedback_map()
    return list(feedback_map.keys())


def invalidate_cache() -> None:
    """Clear the cached feedback map."""
    global _feedback_cache
    _feedback_cache = None
