from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class FBIAuthority(BaseModel):
    """Evidence the repair layer must treat as authoritative."""

    authority_type: str = ""
    source: str = ""
    statement: str = ""
    confidence: str = "medium"
    evidence: dict[str, Any] = Field(default_factory=dict)


class FBIConflictDecision(BaseModel):
    """Review Minister decision for a conflicting span or claim."""

    conflict_id: str = ""
    current_claim: str = ""
    authority_claim: str = ""
    decision: str = "preserve_authority"
    rationale: str = ""


class FBIRepairIntent(BaseModel):
    """Actionable repair instruction consumed by the FBI planner/executor."""

    intent_id: str = ""
    issue_ids: list[str] = Field(default_factory=list)
    repair_domain: str = ""
    repair_lane: str = ""
    repair_strength: str = ""
    allowed_max_strength: str = ""
    preferred_operation: str = ""
    fallback_operations: list[str] = Field(default_factory=list)
    target_behavior: str = ""
    patch_plan: list[dict[str, Any]] = Field(default_factory=list)
    target_metrics: list[dict[str, Any]] = Field(default_factory=list)
    source_trace: list[str] = Field(default_factory=list)


class FBIRevisionBlueprint(BaseModel):
    """Review Minister's executable repair design before tool compilation."""

    blueprint_id: str = ""
    status: str = "blueprint_incomplete"
    repair_family: str = ""
    repair_intent: str = ""
    strategy: str = ""
    target_scene: int | None = None
    edit_window: dict[str, Any] = Field(default_factory=dict)
    proposed_change: dict[str, Any] = Field(default_factory=dict)
    protection_policy: dict[str, Any] = Field(default_factory=dict)
    acceptance: dict[str, Any] = Field(default_factory=dict)
    tool_blueprint: dict[str, Any] = Field(default_factory=dict)
    missing: list[str] = Field(default_factory=list)
    rejection_reason: str = ""
    route: str = ""


class FBIProtectionBoundary(BaseModel):
    """Hard boundaries that repair candidates must preserve."""

    preserve_scene_markers: bool = True
    preserve_hard_facts: list[str] = Field(default_factory=list)
    preserve_protected_spans: list[str] = Field(default_factory=list)
    preserve_pov: str = ""
    forbidden_operations: list[str] = Field(default_factory=lambda: [
        "change_fact",
        "change_pov",
        "remove_scene_marker",
        "remove_protected_obligation",
    ])


class FBIAcceptanceCriterion(BaseModel):
    """A repair-local acceptance check."""

    criterion_id: str = ""
    metric: str = ""
    operator: str = "resolved"
    expected: Any = ""
    source: str = ""


class FBIRepairGoal(BaseModel):
    """A semantic repair obligation independent from validator findings.

    Multiple findings may support one goal.  ``goal_id`` is derived from the
    authority, ownership, mutation kind and desired/prohibited state, never
    from the number or ordering of findings that happened to report it.
    """

    goal_id: str = ""
    authority_ref: str = ""
    owner_scope: dict[str, Any] = Field(default_factory=dict)
    mutation_kind: str = "rewrite_window"
    desired_state: str = ""
    prohibited_state: str = ""
    source_issue_ids: list[str] = Field(default_factory=list)
    source_findings: list[dict[str, Any]] = Field(default_factory=list)
    blocks_commit: bool = True
    acceptance_criteria: list[FBIAcceptanceCriterion] = Field(default_factory=list)


class FBIPlacementDecision(BaseModel):
    """Separates evidence/read context from the physical write location."""

    placement_id: str = ""
    status: str = "required"
    scene_index: int | None = None
    read_context_spans: list[dict[str, Any]] = Field(default_factory=list)
    diagnostic_evidence_spans: list[dict[str, Any]] = Field(default_factory=list)
    write_anchor: dict[str, Any] = Field(default_factory=dict)
    confidence: str = ""
    source: str = ""
    reason: str = ""


class FBIDependencyPolicy(BaseModel):
    """Scheduling guidance for this diagnosis."""

    depends_on: list[str] = Field(default_factory=list)
    blocks: list[str] = Field(default_factory=list)
    must_run_before_domains: list[str] = Field(default_factory=list)
    can_batch_with: list[str] = Field(default_factory=list)


class FBIReviewDiagnosis(BaseModel):
    """Review Minister normalized diagnosis."""

    diagnosis_id: str = ""
    issue_ids: list[str] = Field(default_factory=list)
    repair_goal_ids: list[str] = Field(default_factory=list)
    scene_index: int | None = None
    scope: str = "scene"
    issue_family: str = "general"
    problem_statement: str = ""
    authorities: list[FBIAuthority] = Field(default_factory=list)
    conflict_decisions: list[FBIConflictDecision] = Field(default_factory=list)
    repair_intent: FBIRepairIntent = Field(default_factory=FBIRepairIntent)
    revision_blueprint: FBIRevisionBlueprint = Field(default_factory=FBIRevisionBlueprint)
    protection_boundary: FBIProtectionBoundary = Field(default_factory=FBIProtectionBoundary)
    acceptance_criteria: list[FBIAcceptanceCriterion] = Field(default_factory=list)
    placement: FBIPlacementDecision = Field(default_factory=FBIPlacementDecision)
    dependency_policy: FBIDependencyPolicy = Field(default_factory=FBIDependencyPolicy)
    blocks_commit: bool = True
    source_trace: list[str] = Field(default_factory=list)

    def to_repair_brief_patch(self) -> dict[str, Any]:
        """Return fields that can be merged into ChapterRepairOrder.repair_brief."""
        intent = self.repair_intent
        boundary = self.protection_boundary
        return {
            "diagnosis_id": self.diagnosis_id,
            "repair_goal_ids": list(self.repair_goal_ids),
            "placement": self.placement.model_dump(),
            "review_minister": {
                "issue_family": self.issue_family,
                "problem_statement": self.problem_statement,
                "authorities": [item.model_dump() for item in self.authorities],
                "conflict_decisions": [item.model_dump() for item in self.conflict_decisions],
                "acceptance_criteria": [item.model_dump() for item in self.acceptance_criteria],
                "source_trace": list(self.source_trace),
            },
            "repair_intent": intent.model_dump(),
            "revision_blueprint": self.revision_blueprint.model_dump(),
            "tool_blueprint": dict(self.revision_blueprint.tool_blueprint or {}),
            "target_metrics": [item for item in intent.target_metrics if isinstance(item, dict)],
            "target_behavior": intent.target_behavior,
            "patch_plan": [item for item in intent.patch_plan if isinstance(item, dict)],
            "preserve": {
                "scene_markers": boundary.preserve_scene_markers,
                "hard_facts": list(boundary.preserve_hard_facts),
                "protected_spans": list(boundary.preserve_protected_spans),
                "pov": boundary.preserve_pov,
            },
            "edit_scope": {
                "forbidden_operations": list(boundary.forbidden_operations),
            },
            "self_check": {
                "required": True,
                "target_metric_must_improve": True,
                "protection_must_pass": True,
                "no_new_hard_failure": True,
                "acceptance_criteria": [item.model_dump() for item in self.acceptance_criteria],
            },
        }


class FBIReviewDiagnosisSet(BaseModel):
    """Review Minister output for a repair round."""

    case_id: str = ""
    review_round: int = 0
    diagnoses: list[FBIReviewDiagnosis] = Field(default_factory=list)
    repair_goals: list[FBIRepairGoal] = Field(default_factory=list)
    issue_status: dict[str, str] = Field(default_factory=dict)
    trace: dict[str, Any] = Field(default_factory=dict)

    def by_issue_id(self) -> dict[str, FBIReviewDiagnosis]:
        mapping: dict[str, FBIReviewDiagnosis] = {}
        for diagnosis in self.diagnoses:
            for issue_id in diagnosis.issue_ids:
                if issue_id and issue_id not in mapping:
                    mapping[issue_id] = diagnosis
        return mapping
