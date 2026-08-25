from __future__ import annotations

import os
from collections.abc import Mapping

from app.models.generation_features import GenerationFeaturePolicy


_ENV_TO_FIELD = {
    "LOREWEFT_AI_FLAVOR_MODE": "ai_flavor_mode",
    "LOREWEFT_READER_EXPERIENCE_MODE": "reader_experience_mode",
    "LOREWEFT_CONCEPT_BUDGET_MODE": "concept_budget_mode",
    "LOREWEFT_CHARACTER_VOICE_MODE": "character_voice_mode",
    "LOREWEFT_PROMPT_TRACE_MODE": "prompt_trace_mode",
    "LOREWEFT_MENTION_DETECTOR_MODE": "mention_detector_mode",
    "LOREWEFT_PROGRESSION_MODE": "progression_mode",
    "LOREWEFT_GENERATION_MODE": "generation_mode",
    # 叙事命题层模式
    "LOREWEFT_PROPOSITION_EXTRACTION_MODE": "proposition_extraction_mode",
    "LOREWEFT_PROPOSITION_AUDIT_MODE": "proposition_audit_mode",
    "LOREWEFT_HARD_CORRECTNESS_MODE": "hard_correctness_mode",
    "LOREWEFT_NARRATIVE_CONTRACT_MODE": "narrative_contract_mode",
    "LOREWEFT_STYLE_QUALITY_MODE": "style_quality_mode",
    # 叙事体验 / 文学质量层
    "LOREWEFT_WRITING_MODE_PROFILE_ID": "writing_mode_profile_id",
    "LOREWEFT_WRITING_MODE_PROFILE_MODE": "writing_mode_profile_mode",
    "LOREWEFT_NARRATIVE_EXPERIENCE_MODE": "narrative_experience_mode",
    "LOREWEFT_LITERARY_QUALITY_MODE": "literary_quality_mode",
    "LOREWEFT_MODE_FIT_MODE": "mode_fit_mode",
    "LOREWEFT_STYLE_EXPERIENCE_CONFLICT_MODE": "style_experience_conflict_mode",
    "LOREWEFT_EXPERIENCE_REVISION_MODE": "experience_revision_mode",
    "LOREWEFT_COMMERCIAL_PACING_MODE": "commercial_pacing_mode",
    "LOREWEFT_COMMERCIAL_PACING_PROFILE_ID": "commercial_pacing_profile_id",
    "LOREWEFT_COMMERCIAL_PACING_REVISION_MODE": "commercial_pacing_revision_mode",
    "LOREWEFT_READER_CORPUS_MODE": "reader_corpus_mode",
    "LOREWEFT_READER_CORPUS_PROFILE_ID": "reader_corpus_profile_id",
    "LOREWEFT_SCENE_CREDIBILITY_MODE": "scene_credibility_mode",
    # 去AI味 Gate
    "LOREWEFT_DESLOP_GATE_MODE": "deslop_gate_mode",
    # FBI 独立修订局
    "LOREWEFT_FBI_REPAIR_MODE": "fbi_repair_mode",
    "LOREWEFT_FBI_PATCH_MODE": "fbi_patch_mode",
    "LOREWEFT_FBI_FRONTEND_WORKBENCH_ENABLED": "fbi_frontend_workbench_enabled",
    "LOREWEFT_FBI_AUTO_RECHECK_ENABLED": "fbi_auto_recheck_enabled",
    "LOREWEFT_FBI_REPAIR_MEMORY_ENABLED": "fbi_repair_memory_enabled",
    "LOREWEFT_FBI_MAX_ATTEMPTS_PER_CASE": "fbi_max_attempts_per_case",
    "LOREWEFT_FBI_MAX_CHANGED_RATIO": "fbi_max_changed_ratio",
}


class GenerationFeaturePolicyService:
    """Resolve generation feature switches.

    Missing quality-layer settings fall back to the model defaults, which now
    enable current-turn assistance for assist-capable quality layers. Structural
    correctness gates remain opt-in.
    """

    def resolve(self, project=None, request_overrides: Mapping | None = None) -> GenerationFeaturePolicy:
        data: dict = {}
        notes: list[str] = []

        project_data = getattr(project, "core_data", None)
        if isinstance(project_data, Mapping):
            raw_project_policy = project_data.get("generation_features")
            if isinstance(raw_project_policy, Mapping):
                data.update(dict(raw_project_policy))
                notes.append("project")

        for env_key, field in _ENV_TO_FIELD.items():
            value = os.getenv(env_key)
            if value:
                data[field] = value.strip().lower()
                notes.append(f"env:{env_key}")

        if isinstance(request_overrides, Mapping):
            raw = request_overrides.get("generation_features") or request_overrides
            if isinstance(raw, Mapping):
                data.update(dict(raw))
                notes.append("request")

        try:
            policy = GenerationFeaturePolicy(**data)
        except Exception:
            return GenerationFeaturePolicy(
                enabled_by="default",
                notes=["invalid_config_fallback"],
            )

        if notes:
            policy.enabled_by = ",".join(notes)
            policy.notes = notes
        return policy


def resolve_generation_feature_policy(project=None, request_overrides: Mapping | None = None) -> GenerationFeaturePolicy:
    return GenerationFeaturePolicyService().resolve(project, request_overrides)
