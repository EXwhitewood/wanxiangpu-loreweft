from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


AdvisoryDimension = Literal[
    "fact",
    "character",
    "timeline",
    "world_rule",
    "pov_knowledge",
    "foreshadowing",
    "outline_deviation",
    "pacing",
    "clarity",
    "character_expression",
    "dialogue",
    "hook",
    "prose_style",
]
AdvisoryTrigger = Literal["manual", "save", "ai_edit_before", "ai_edit_after"]
AIEditPermissionMode = Literal["proposal_only", "ask_every_time", "chapter_session"]


DEFAULT_ADVISORY_DIMENSIONS: list[AdvisoryDimension] = [
    "fact",
    "character",
    "timeline",
    "world_rule",
    "pov_knowledge",
    "foreshadowing",
    "outline_deviation",
    "pacing",
    "clarity",
    "character_expression",
    "dialogue",
    "hook",
    "prose_style",
]


class ConsistencyReminderSettings(BaseModel):
    # Post-save diagnostics are the default auxiliary path.  They are
    # non-blocking and do not alter the AI generation route.
    enabled: bool = True
    check_on_save: bool = True
    dimensions: list[AdvisoryDimension] = Field(
        default_factory=lambda: list(DEFAULT_ADVISORY_DIMENSIONS)
    )

    @field_validator("dimensions")
    @classmethod
    def deduplicate_dimensions(
        cls, dimensions: list[AdvisoryDimension]
    ) -> list[AdvisoryDimension]:
        return list(dict.fromkeys(dimensions))


class AIEditPermissionSettings(BaseModel):
    # This is a default interaction policy, not an edit authorization. Actual
    # mutations still require a scoped user action in the workbench.
    mode: AIEditPermissionMode = "ask_every_time"


class WritingAssistanceSettings(BaseModel):
    consistency_reminders: ConsistencyReminderSettings = Field(
        default_factory=ConsistencyReminderSettings
    )
    ai_edit_permission: AIEditPermissionSettings = Field(
        default_factory=AIEditPermissionSettings
    )


class ChapterAdvisoryCheckRequest(BaseModel):
    content: str = ""
    trigger: AdvisoryTrigger = "manual"
    dimensions: list[AdvisoryDimension] | None = None
    # Optional frozen reference supplied by the chapter-save flow.  When it is
    # present, diagnostics compare against the pre-save state rather than the
    # newly projected state.
    reference_snapshot: dict | None = None

    @field_validator("dimensions")
    @classmethod
    def deduplicate_requested_dimensions(
        cls, dimensions: list[AdvisoryDimension] | None
    ) -> list[AdvisoryDimension] | None:
        if dimensions is None:
            return None
        return list(dict.fromkeys(dimensions))


class ChapterAdvisorySource(BaseModel):
    source_type: str
    label: str
    excerpt: str


class ChapterAdvisoryFinding(BaseModel):
    finding_id: str
    fingerprint: str
    revision_hash: str
    category: AdvisoryDimension
    severity: Literal["high", "medium", "low"] = "low"
    title: str
    detail: str
    evidence_quote: str
    source: ChapterAdvisorySource
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    suggested_actions: list[
        Literal[
            "ignore",
            "intentional_exception",
            "update_source",
            "ask_ai",
            "authorize_edit",
        ]
    ] = Field(
        default_factory=lambda: [
            "ignore",
            "intentional_exception",
            "update_source",
            "ask_ai",
            "authorize_edit",
        ]
    )
    blocks_commit: Literal[False] = False
    repair_scope: Literal["advisory"] = "advisory"
    repair_lane: Literal["none"] = "none"
    diagnostic_id: str | None = None
    status: str = "open"
    available_actions: list[str] = Field(default_factory=list)
    affected_domains: list[str] = Field(default_factory=list)


class ChapterAdvisoryDiagnostic(BaseModel):
    code: str
    message: str
    level: Literal["info", "warning"] = "info"


class ChapterAdvisoryCheckResponse(BaseModel):
    status: Literal["complete", "degraded", "disabled"] = "complete"
    trigger: AdvisoryTrigger
    revision_hash: str
    checked_dimensions: list[AdvisoryDimension] = Field(default_factory=list)
    findings: list[ChapterAdvisoryFinding] = Field(default_factory=list)
    diagnostics: list[ChapterAdvisoryDiagnostic] = Field(default_factory=list)
    blocks_commit: Literal[False] = False
    persisted: bool = False
    diagnostic_count: int = 0


class ChapterDiagnosticDecisionRequest(BaseModel):
    action: Literal["mark_fixed", "ignore", "ignore_and_amend_outline"]
    reason: str = ""
    # Optional explicit patch supplied by the editor when accepting an
    # intentional deviation.  The server still records an amendment even
    # when this is empty, so the decision is never silent.
    outline_changes: dict = Field(default_factory=dict)
    decided_by: str = "user"
