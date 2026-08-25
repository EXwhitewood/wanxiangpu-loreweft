from typing import Literal

from pydantic import BaseModel, Field


FeatureMode = Literal["off", "shadow", "report", "assist", "enforce"]


class GenerationFeaturePolicy(BaseModel):
    """Project-scoped feature modes.

    Assist-capable quality layers default to current-turn assistance. Structural
    correctness gates remain opt-in to avoid hard blocking new projects.
    """
    schema_version: int = 1
    ai_flavor_mode: FeatureMode = "report"
    reader_experience_mode: FeatureMode = "assist"
    concept_budget_mode: Literal["off", "shadow", "report", "enforce"] = "off"
    character_voice_mode: FeatureMode = "assist"
    prompt_trace_mode: Literal["off", "metadata", "full", "full_debug_when_failed"] = "off"
    mention_detector_mode: Literal["off", "shadow", "candidate"] = "off"
    progression_mode: Literal["off", "read", "candidate", "active"] = "off"
    generation_mode: Literal["legacy", "quick", "standard", "deep"] = "legacy"
    # 叙事命题层模式（通用叙事命题层重构方案 Phase 0-4）
    proposition_extraction_mode: Literal["off", "shadow", "report", "enforce"] = "off"
    proposition_audit_mode: Literal["off", "shadow", "report", "enforce"] = "off"
    hard_correctness_mode: Literal["off", "shadow", "report", "enforce"] = "off"
    narrative_contract_mode: Literal["off", "shadow", "report", "enforce"] = "off"
    style_quality_mode: Literal["off", "shadow", "report", "assist"] = "assist"
    # 叙事体验 / 文学质量层
    writing_mode_profile_id: str = "general"
    writing_mode_profile_mode: FeatureMode = "assist"
    narrative_experience_mode: FeatureMode = "assist"
    literary_quality_mode: FeatureMode = "assist"
    mode_fit_mode: FeatureMode = "assist"
    style_experience_conflict_mode: FeatureMode = "assist"
    experience_revision_mode: Literal["off", "local_patch", "scene_rewrite", "full"] = "off"
    # Commercial pacing / reader-retention layer defaults to current-turn assist.
    # 商业节奏 / 爽点对标 / 留读驱动层。默认关闭，避免影响旧流程。
    commercial_pacing_mode: FeatureMode = "assist"
    commercial_pacing_profile_id: str = "general"
    commercial_pacing_revision_mode: Literal["off", "local_patch", "scene_rewrite"] = "off"
    # Private reader corpus / commercial experience knowledge layer.
    reader_corpus_mode: FeatureMode = "assist"
    reader_corpus_profile_id: str = "global"
    # 场景可信度协议（事实边界 / 知识边界 / 行为可信度 / 叙事可信度）
    scene_credibility_mode: FeatureMode = "assist"
    # 去AI味 Gate 模式（Deslop Gate A-F 六个检测器）
    deslop_gate_mode: FeatureMode = "off"
    # FBI 独立修订局模式
    fbi_repair_mode: Literal["off", "shadow", "assist", "auto", "enforce"] = "assist"
    fbi_patch_mode: Literal["patch_only", "scoped_rewrite", "allow_scene_rewrite"] = "patch_only"
    fbi_frontend_workbench_enabled: bool = False
    fbi_auto_recheck_enabled: bool = True
    fbi_repair_memory_enabled: bool = False
    fbi_max_attempts_per_case: int = 3
    fbi_max_changed_ratio: float = 0.3
    enabled_by: str = "default"
    notes: list[str] = Field(default_factory=list)

    @property
    def is_legacy(self) -> bool:
        return self.generation_mode == "legacy" and all(
            getattr(self, field) == "off"
            for field in (
                "ai_flavor_mode",
                "reader_experience_mode",
                "concept_budget_mode",
                "character_voice_mode",
                "prompt_trace_mode",
                "mention_detector_mode",
                "progression_mode",
                "proposition_extraction_mode",
                "proposition_audit_mode",
                "hard_correctness_mode",
                "narrative_contract_mode",
                "style_quality_mode",
                "writing_mode_profile_mode",
                "narrative_experience_mode",
                "literary_quality_mode",
                "mode_fit_mode",
                "style_experience_conflict_mode",
                "experience_revision_mode",
                "commercial_pacing_mode",
                "commercial_pacing_revision_mode",
                "reader_corpus_mode",
                "scene_credibility_mode",
                "deslop_gate_mode",
                "fbi_repair_mode",
                "fbi_patch_mode",
            )
        )

    def mode_enabled(self, field: str) -> bool:
        return getattr(self, field, "off") != "off"
