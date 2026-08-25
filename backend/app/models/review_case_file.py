from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import BaseModel, Field


IssueSeverity = Literal["critical", "high", "medium", "low"]
IssueScope = Literal["scene", "chapter", "span", "marker", "contract"]
Repairability = Literal[
    "deterministic",
    "local_patch",
    "scene_rewrite",
    "manual_review",
]


class ReviewCaseIssue(BaseModel):
    issue_id: str = ""
    source: str = ""
    source_validator: str = ""
    skill_id: str = ""
    scene_index: int | None = None
    scope: IssueScope = "scene"
    type: str = "unknown"
    metric: str = ""
    severity: IssueSeverity = "medium"
    blocks_commit: bool = False
    repairability: Repairability = "local_patch"
    repair_domain: str = "manual"
    detail: str = ""
    target_span: str = ""
    expected_behavior: str = ""
    protected_context: list[str] = Field(default_factory=list)
    evidence: dict = Field(default_factory=dict)
    dedupe_key: str = ""
    sources: list[str] = Field(default_factory=list)
    status: str = "raw"
    diagnosis_id: str = ""
    repair_intent_id: str = ""
    repair_order_id: str = ""
    resolution_evidence: dict = Field(default_factory=dict)
    stale_reason: str = ""
    superseded_by: str = ""

    def stable_key(self) -> str:
        if self.dedupe_key:
            return self.dedupe_key
        scene = "" if self.scene_index is None else str(self.scene_index)
        return f"{scene}:{self.repair_domain}:{self.type}:{self.metric}:{self.target_span}"


class ReviewCaseFile(BaseModel):
    case_id: str = ""
    project_id: str = ""
    chapter_number: int = 0
    draft_hash: str = ""
    created_from: str = "initial_review"
    scene_issues: list[ReviewCaseIssue] = Field(default_factory=list)
    chapter_issues: list[ReviewCaseIssue] = Field(default_factory=list)
    skill_failures: list[ReviewCaseIssue] = Field(default_factory=list)
    quality_advisories: list[ReviewCaseIssue] = Field(default_factory=list)
    protection_obligations: list[dict] = Field(default_factory=list)
    review_trace: dict = Field(default_factory=dict)
    diagnoses: list[dict] = Field(default_factory=list)
    repair_intents: list[dict] = Field(default_factory=list)
    revision_blueprints: list[dict] = Field(default_factory=list)
    issue_status: dict = Field(default_factory=dict)
    round_history: list[dict] = Field(default_factory=list)
    source_trace: dict = Field(default_factory=dict)

    def all_issues(self) -> list[ReviewCaseIssue]:
        return [
            *self.scene_issues,
            *self.chapter_issues,
            *self.skill_failures,
            *self.quality_advisories,
        ]

    def summary(self) -> dict:
        issues = self.all_issues()
        status_counts: dict[str, int] = {}
        for issue in issues:
            status = self.issue_status.get(issue.issue_id) or issue.status or "raw"
            status_counts[status] = status_counts.get(status, 0) + 1
        return {
            "case_id": self.case_id,
            "project_id": self.project_id,
            "chapter_number": self.chapter_number,
            "draft_hash": self.draft_hash,
            "created_from": self.created_from,
            "scene_issues": len(self.scene_issues),
            "chapter_issues": len(self.chapter_issues),
            "skill_failures": len(self.skill_failures),
            "quality_advisories": len(self.quality_advisories),
            "blocking_issues": sum(1 for issue in issues if issue.blocks_commit),
            "current_issues": sum(
                1 for issue in issues
                if (self.issue_status.get(issue.issue_id) or issue.status or "raw")
                not in {"resolved", "stale", "superseded"}
            ),
            "resolved_issues": status_counts.get("resolved", 0),
            "stale_issues": status_counts.get("stale", 0),
            "issue_status_counts": status_counts,
            "diagnoses": len(self.diagnoses),
            "repair_intents": len(self.repair_intents),
            "revision_blueprints": len(self.revision_blueprints),
            "repair_domain_counts": self._domain_counts(issues),
            "samples": [issue.model_dump() for issue in issues[:5]],
        }

    @staticmethod
    def _domain_counts(issues: list[ReviewCaseIssue]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for issue in issues:
            counts[issue.repair_domain] = counts.get(issue.repair_domain, 0) + 1
        return counts


def compute_review_case_id(
    *,
    project_id: str,
    chapter_number: int,
    draft_hash: str,
    created_from: str,
) -> str:
    raw = f"{project_id}:{chapter_number}:{draft_hash}:{created_from}"
    return "rcf_" + hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]
