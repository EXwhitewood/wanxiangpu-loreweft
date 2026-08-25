from __future__ import annotations

import copy
import logging
from pathlib import Path
from typing import Mapping

import yaml

from app.models.writing_mode import WritingModeProfile

logger = logging.getLogger(__name__)


_PROFILE_DIR = Path(__file__).resolve().parents[1] / "config" / "writing_mode_profiles"


_BUILTIN_GENERAL = WritingModeProfile(
    id="general",
    label="通用",
    description="平衡读者体验、清晰度、场景压力和文本质量的默认模式。",
    target_reader="中文小说读者",
    metric_weights={
        "reading_drive": 1.0,
        "clarity": 1.0,
        "dramatic_pressure": 1.0,
        "protagonist_agency": 1.0,
        "information_design": 1.0,
        "emotional_engagement": 1.0,
        "specificity": 1.0,
        "prose_identity": 0.8,
        "cultural_texture": 0.6,
        "experimental_form": 0.2,
        "intertextuality": 0.2,
    },
    experience_defaults={
        "preferred_scene_pressure": ["desire_vs_obstacle", "social_risk", "time_pressure"],
        "preferred_information_carriers": ["action", "dialogue", "object_detail", "sensory_observation"],
        "avoid": ["long_exposition_before_pressure", "abstract_moral_summary"],
    },
    literary_quality_defaults={
        "abstraction_ceiling": "medium",
        "specificity_requirement": "medium",
        "dialogue_pressure_requirement": "medium",
        "prose_distinctiveness_requirement": "medium",
        "intertextuality_requirement": "low",
        "cultural_texture_requirement": "medium",
    },
    style_interaction={
        "style_hard_rules_override_quality": True,
        "allow_quality_to_request_style_negotiation": True,
        "conflict_policy": "negotiate",
    },
    revision_policy={
        "local_patch_allowed": [
            "exposition_to_action",
            "generic_detail_to_specific_detail",
            "repeated_sentence_pattern",
        ],
        "structural_rewrite_allowed": [
            "missing_scene_pressure",
            "passive_protagonist",
            "no_reader_hook",
        ],
        "never_change": [
            "fact_contract",
            "responsibility",
            "pov_boundary",
            "established_event_outcome",
        ],
    },
)


class WritingModeProfileService:
    """Load and resolve writing-mode profiles without project contamination."""

    def __init__(self, profile_dir: Path | None = None):
        self.profile_dir = profile_dir or _PROFILE_DIR
        self._profiles: dict[str, WritingModeProfile] | None = None

    def list_profiles(self) -> list[WritingModeProfile]:
        return list(self._load_profiles().values())

    def get_profile(self, profile_id: str | None = None) -> WritingModeProfile:
        profiles = self._load_profiles()
        pid = (profile_id or "general").strip() or "general"
        return copy.deepcopy(profiles.get(pid) or profiles.get("general") or _BUILTIN_GENERAL)

    def get_project_profile(self, project=None, feature_policy=None) -> WritingModeProfile:
        profile_id = "general"
        if feature_policy is not None and hasattr(feature_policy, "writing_mode_profile_id"):
            profile_id = getattr(feature_policy, "writing_mode_profile_id") or "general"
        elif isinstance(feature_policy, Mapping):
            profile_id = str(feature_policy.get("writing_mode_profile_id") or "general")

        core_data = getattr(project, "core_data", None)
        if isinstance(core_data, Mapping):
            raw_features = core_data.get("generation_features")
            if isinstance(raw_features, Mapping):
                profile_id = str(raw_features.get("writing_mode_profile_id") or profile_id)

            raw_profile = core_data.get("writing_mode_profile")
            if isinstance(raw_profile, Mapping):
                try:
                    profile = WritingModeProfile(**raw_profile)
                    if profile.id:
                        return profile
                except Exception as exc:
                    logger.warning("Invalid project writing_mode_profile ignored: %s", exc)

        return self.get_profile(profile_id)

    def _load_profiles(self) -> dict[str, WritingModeProfile]:
        if self._profiles is not None:
            return self._profiles

        profiles: dict[str, WritingModeProfile] = {"general": copy.deepcopy(_BUILTIN_GENERAL)}
        if self.profile_dir.exists():
            for path in sorted(self.profile_dir.glob("*.y*ml")):
                try:
                    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                    raw_profiles = payload.get("profiles", [])
                    if isinstance(raw_profiles, Mapping):
                        raw_profiles = list(raw_profiles.values())
                    if not isinstance(raw_profiles, list):
                        continue
                    for raw in raw_profiles:
                        if not isinstance(raw, Mapping):
                            continue
                        profile = WritingModeProfile(**dict(raw))
                        profiles[profile.id] = profile
                except Exception as exc:
                    logger.warning("Failed to load writing mode profiles from %s: %s", path, exc)

        self._profiles = profiles
        return profiles
