from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


CorpusStatus = Literal["ready", "processing", "failed"]
CorpusQualityTier = Literal["S", "A", "B", "C", "unknown"]


class ReaderCorpusSource(BaseModel):
    id: str
    title: str
    author: str = ""
    genre: str = ""
    platform: str = ""
    quality_tier: CorpusQualityTier = "unknown"
    status: CorpusStatus = "ready"
    word_count: int = 0
    chapter_count: int = 0
    tags: list[str] = Field(default_factory=list)
    allowed_uses: list[str] = Field(default_factory=lambda: [
        "structure",
        "pacing",
        "reader_drive",
        "anti_ai",
    ])
    notes: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ReaderCorpusChapterMetrics(BaseModel):
    char_count: int = 0
    paragraph_count: int = 0
    sentence_count: int = 0
    avg_sentence_chars: float = 0.0
    dialogue_ratio: float = 0.0
    action_ratio: float = 0.0
    description_ratio: float = 0.0
    exposition_ratio: float = 0.0
    conflict_signal: float = 0.0
    curiosity_signal: float = 0.0
    ending_hook_signal: float = 0.0
    ai_artifact_risk: float = 0.0
    dash_density: float = 0.0


class ReaderCorpusChapter(BaseModel):
    id: str
    source_id: str
    chapter_index: int
    title: str
    char_count: int
    content_hash: str
    sample_excerpt: str = ""
    metrics: ReaderCorpusChapterMetrics = Field(default_factory=ReaderCorpusChapterMetrics)
    structure_tags: list[str] = Field(default_factory=list)
    pattern_summary: dict = Field(default_factory=dict)
    created_at: datetime | None = None


class ReaderExperiencePattern(BaseModel):
    id: str
    source_id: str = ""
    chapter_id: str = ""
    genre: str = ""
    chapter_position: str = "unknown"
    scene_position: str = "any"
    pattern_type: str
    pattern_key: str
    score: float = 0.0
    evidence_count: int = 1
    metrics: dict = Field(default_factory=dict)
    guidance: dict = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    created_at: datetime | None = None


class ReaderExperienceGuidance(BaseModel):
    schema_version: int = 1
    mode: str = "off"
    genre: str = ""
    chapter_position: str = "unknown"
    scene_position: str = "any"
    guidance: list[str] = Field(default_factory=list)
    target_metrics: dict = Field(default_factory=dict)
    avoid_patterns: list[str] = Field(default_factory=list)
    pattern_ids: list[str] = Field(default_factory=list)
    source_count: int = 0
    token_budget_chars: int = 600

    @property
    def has_guidance(self) -> bool:
        return bool(self.guidance or self.avoid_patterns or self.target_metrics)


class ReaderCorpusUploadResult(BaseModel):
    imported: int = 0
    failed: int = 0
    sources: list[ReaderCorpusSource] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

