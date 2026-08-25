from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


CredibilityMode = Literal["off", "shadow", "report", "assist", "enforce"]


class ContractSourceRef(BaseModel):
    source_type: str = ""
    source_id: str = ""
    confidence: Literal["low", "medium", "high"] = "medium"
    note: str = ""


class FactBoundaryItem(BaseModel):
    """A compact, domain-neutral hard fact boundary for one scene."""

    fact_id: str
    label: str = ""
    allowed_claims: list[str] = Field(default_factory=list)
    forbidden_claims: list[str] = Field(default_factory=list)
    certainty: Literal["known", "suspected", "unknown"] = "known"
    source_refs: list[ContractSourceRef] = Field(default_factory=list)


class CharacterKnowledgeSnapshot(BaseModel):
    character_id: str = ""
    display_name: str = ""
    knows: list[str] = Field(default_factory=list)
    can_infer: list[str] = Field(default_factory=list)
    cannot_know: list[str] = Field(default_factory=list)
    evidence_required_for: list[str] = Field(default_factory=list)


class PlausibilityRule(BaseModel):
    rule_id: str
    description: str
    avoid_patterns: list[str] = Field(default_factory=list)
    preferred_methods: list[str] = Field(default_factory=list)


class NarrationCredibilityRule(BaseModel):
    rule_id: str
    description: str
    avoid_patterns: list[str] = Field(default_factory=list)
    preferred_methods: list[str] = Field(default_factory=list)


class SceneCredibilityContract(BaseModel):
    """Scene Credibility Protocol contract.

    This model is intentionally genre-neutral. It uses abstract fact and
    knowledge boundaries rather than project-specific examples.
    """

    schema_version: int = 1
    project_id: str = ""
    chapter_number: int = 0
    scene_index: int = 0
    fact_boundaries: list[FactBoundaryItem] = Field(default_factory=list)
    knowledge_snapshots: list[CharacterKnowledgeSnapshot] = Field(default_factory=list)
    plausibility_rules: list[PlausibilityRule] = Field(default_factory=list)
    narration_rules: list[NarrationCredibilityRule] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    compiler_warnings: list[str] = Field(default_factory=list)
    source_refs: list[ContractSourceRef] = Field(default_factory=list)


class SceneCredibilityCompiled(BaseModel):
    contract: SceneCredibilityContract
    quality_extensions_patch: dict = Field(default_factory=dict)
    compiler_warnings: list[str] = Field(default_factory=list)
