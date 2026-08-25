"""Final chapter commit health checks.

These checks sit at the boundary between a successful workflow run and a
persisted chapter. They are intentionally generic: they validate structural
workflow outputs, not project-specific plot facts.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.services.metric_registry import determine_blocks_commit


_RESIDUAL_SCENE_MARKER_RE = re.compile(
    r"(?im)^\s*(?:\[\[SCENE:[^\]]+\]\]|<<<SCENE_ID:[^>]+>>>|Scene\s*\d+\s*[:：]|第[一二三四五六七八九十\d]+场\s*[:：])\s*$"
)
_SECTION_DIVIDER_RE = re.compile(r"(?m)^\s*(?:-{3,}|\*{3,}|#{2,}\s+\S.*)\s*$")


@dataclass(slots=True)
class CommitHealthIssue:
    code: str
    severity: str
    detail: str
    scene_index: int | None = None
    # 方案 7 Part D：补齐 metric 与 text_hash，使终审 issue 与 skill/quality 路径同构，
    # 便于 FBI 去重与 stale finding 检测。
    metric: str = ""
    text_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "detail": self.detail,
            "scene_index": self.scene_index,
            "metric": self.metric or self.code,
            "text_hash": self.text_hash,
        }


def _has_chapter_state_signal(chapter_state: dict[str, Any] | None) -> bool:
    if not isinstance(chapter_state, dict):
        return False
    return bool(
        chapter_state.get("established_facts")
        or chapter_state.get("completed_events")
        or chapter_state.get("character_states")
        or chapter_state.get("active_constraints")
    )


class ChapterCommitHealthChecker:
    """Validate final chapter artifacts before write_chapter commits them."""

    def evaluate(
        self,
        *,
        final_text: str | None = None,
        scene_texts: dict[int, str] | None = None,
        chapter_state: dict[str, Any] | None = None,
        style_result: dict[str, Any] | None = None,
        text_hash: str = "",
    ) -> dict[str, Any]:
        issues: list[CommitHealthIssue] = []
        scene_texts = scene_texts or {}

        if final_text is not None and scene_texts and not str(final_text).strip():
            issues.append(CommitHealthIssue(
                "empty_final_text",
                "hard",
                "Final chapter text is empty although aligned scene text exists.",
                metric="empty_final_text",
            ))
        issues.extend(self._scene_structure_issues(scene_texts))
        issues.extend(self._chapter_state_issues(final_text, chapter_state))
        issues.extend(self._style_policy_issues(style_result or {}))

        # 方案 7 Part D：把 text_hash 统一注入所有 issue，供下游 stale 检测使用。
        if text_hash:
            for issue in issues:
                issue.text_hash = text_hash

        # P2-3: 统一走 determine_blocks_commit，与 review_case_file/quality 路径一致
        hard = [issue for issue in issues if determine_blocks_commit(issue.metric, issue.severity)]
        return {
            "allowed": not hard,
            "issues": [issue.to_dict() for issue in issues],
            "hard_issues": [issue.to_dict() for issue in hard],
        }

    def _scene_structure_issues(self, scene_texts: dict[int, str]) -> list[CommitHealthIssue]:
        issues: list[CommitHealthIssue] = []
        multi_scene_mode = len(scene_texts) > 1
        for scene_index, text in sorted(scene_texts.items()):
            text = text or ""
            if not text.strip():
                issues.append(CommitHealthIssue(
                    "empty_scene_text",
                    "hard",
                    "Scene text is empty at final commit boundary.",
                    scene_index,
                    metric="empty_scene_text",
                ))
                continue

            if _RESIDUAL_SCENE_MARKER_RE.search(text):
                issues.append(CommitHealthIssue(
                    "residual_scene_marker",
                    "hard",
                    "Scene text still contains scene/chapter markers after alignment.",
                    scene_index,
                    metric="residual_scene_marker",
                ))

            if multi_scene_mode and _SECTION_DIVIDER_RE.search(text):
                issues.append(CommitHealthIssue(
                    "internal_scene_separator",
                    "hard",
                    "A single aligned scene contains an internal section separator, suggesting full-chapter leakage or scene splice drift.",
                    scene_index,
                    metric="internal_scene_separator",
                ))

        return issues

    def _chapter_state_issues(
        self,
        final_text: str,
        chapter_state: dict[str, Any] | None,
    ) -> list[CommitHealthIssue]:
        if len(final_text or "") < 800 or _has_chapter_state_signal(chapter_state):
            return []
        return [
            CommitHealthIssue(
                "empty_chapter_state",
                "hard",
                "Final chapter text is substantial but chapter_state has no facts, events, character states, or constraints.",
                metric="empty_chapter_state",
            )
        ]

    def _style_policy_issues(self, style_result: dict[str, Any]) -> list[CommitHealthIssue]:
        if not style_result:
            return []
        if style_result.get("requires_human_review"):
            score = style_result.get("style_score")
            return [
                CommitHealthIssue(
                    "style_requires_human_review",
                    "advisory",
                    f"Style polish recommends human review"
                    + (f" (style_score={score})." if score is not None else "."),
                    metric="style_requires_human_review",
                )
            ]
        return []

