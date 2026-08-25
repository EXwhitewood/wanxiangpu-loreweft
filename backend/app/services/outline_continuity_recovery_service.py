from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any

from app.services.story_plan_completeness_service import ensure_complete_scene_briefs
from app.skills.outline_validation import chapter_sequence_diagnostics


class OutlineContinuityRecoveryError(ValueError):
    def __init__(self, code: str, message: str, diagnostics: dict[str, Any]):
        super().__init__(message)
        self.code = code
        self.diagnostics = diagnostics


class OutlineContinuityRecoveryService:
    """Build a reviewable contiguous draft from a sparse saved outline.

    Existing chapters are treated as immutable anchors. Missing chapter numbers
    receive explicit ``needs_review`` bridge placeholders. The caller decides
    where to persist the returned draft; this service never mutates the source.
    """

    def build_draft(self, outline_data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        source_outline = copy.deepcopy(outline_data or {})
        diagnostics = chapter_sequence_diagnostics(source_outline)

        if diagnostics["valid"]:
            raise OutlineContinuityRecoveryError(
                "already_contiguous",
                "当前大纲章节已经连续，无需恢复。",
                diagnostics,
            )
        if diagnostics.get("duplicates") or diagnostics.get("invalid_entries"):
            raise OutlineContinuityRecoveryError(
                "manual_resolution_required",
                "大纲存在重复或无效章节号，必须先人工确认编号，系统不会擅自取舍。",
                diagnostics,
            )

        missing = list(diagnostics.get("missing") or [])
        if not missing:
            raise OutlineContinuityRecoveryError(
                "no_recoverable_gap",
                "当前大纲没有可自动补齐的章节缺口。",
                diagnostics,
            )

        source_name = diagnostics.get("source")
        raw_spine = source_outline.get(source_name)
        if not isinstance(raw_spine, list):
            raise OutlineContinuityRecoveryError(
                "missing_spine",
                "未找到可用于恢复的章节脊柱。",
                diagnostics,
            )

        anchors: dict[int, dict[str, Any]] = {}
        for item in raw_spine:
            if not isinstance(item, dict):
                continue
            chapter_number = item.get("chapter_number")
            if isinstance(chapter_number, int) and not isinstance(chapter_number, bool) and chapter_number > 0:
                anchors[chapter_number] = copy.deepcopy(item)

        anchor_numbers = sorted(anchors)
        max_chapter = int(diagnostics.get("max_chapter") or 0)
        recovered_spine: list[dict[str, Any]] = []
        for chapter_number in range(1, max_chapter + 1):
            anchor = anchors.get(chapter_number)
            if anchor is not None:
                anchor.setdefault("chapter_id", f"ch_{chapter_number:03d}")
                if not anchor.get("conflict_text") and isinstance(anchor.get("main_conflict"), str):
                    anchor["conflict_text"] = anchor["main_conflict"]
                recovered_spine.append(anchor)
                continue

            previous_number = max((n for n in anchor_numbers if n < chapter_number), default=None)
            next_number = min((n for n in anchor_numbers if n > chapter_number), default=None)
            previous_anchor = anchors.get(previous_number) if previous_number is not None else None
            next_anchor = anchors.get(next_number) if next_number is not None else None
            recovered_spine.append(
                self._build_bridge_chapter(
                    chapter_number,
                    previous_number,
                    previous_anchor,
                    next_number,
                    next_anchor,
                )
            )

        draft = copy.deepcopy(source_outline)
        draft["chapter_spine"] = recovered_spine
        draft.pop("_frozen", None)
        draft.pop("_frozen_at", None)
        draft.pop("_version", None)

        meta = copy.deepcopy(draft.get("meta") or {})
        meta["draft_active"] = True
        meta["continuity_recovery"] = {
            "source": "sparse_saved_outline",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "anchor_chapters": anchor_numbers,
            "added_chapters": missing,
            "requires_review": True,
        }
        draft["meta"] = meta
        draft, completion = ensure_complete_scene_briefs(draft)

        recovered_diagnostics = chapter_sequence_diagnostics(draft)
        if not recovered_diagnostics["valid"]:
            raise OutlineContinuityRecoveryError(
                "recovery_failed",
                "连续草稿构造失败，正式大纲未发生变化。",
                recovered_diagnostics,
            )

        summary = {
            "anchor_chapters": anchor_numbers,
            "anchor_count": len(anchor_numbers),
            "added_chapters": missing,
            "added_count": len(missing),
            "target_chapter_count": max_chapter,
            "chapter_continuity": recovered_diagnostics,
            "scene_completion": completion,
        }
        return draft, summary

    @staticmethod
    def _build_bridge_chapter(
        chapter_number: int,
        previous_number: int | None,
        previous_anchor: dict[str, Any] | None,
        next_number: int | None,
        next_anchor: dict[str, Any] | None,
    ) -> dict[str, Any]:
        previous_title = str((previous_anchor or {}).get("title") or "开篇")
        next_title = str((next_anchor or {}).get("title") or "后续情节")
        previous_hook = str((previous_anchor or {}).get("hook") or "承接前一锚点的结果")
        pov_character = str(
            (previous_anchor or {}).get("pov_character")
            or (next_anchor or {}).get("pov_character")
            or ""
        )

        from_label = f"第 {previous_number} 章《{previous_title}》" if previous_number else "故事开端"
        to_label = f"第 {next_number} 章《{next_title}》" if next_number else "后续情节"
        conflict_text = (
            f"本章是连续性恢复生成的待细化过渡章，需要承接{from_label}，"
            f"并建立通往{to_label}的明确行动、阻力与转折。"
        )

        return {
            "chapter_id": f"ch_{chapter_number:03d}",
            "chapter_number": chapter_number,
            "title": f"待细化 · 第 {chapter_number} 章",
            "summary": f"连续性恢复占位章：承接{from_label}并衔接{to_label}，确认正式大纲前需要审阅。",
            "function": "bridge",
            "core_conflict": {
                "desire": previous_hook,
                "obstacle": "本章的具体阻力尚待作者补充",
                "action": "补充连接前后锚点的关键行动",
                "turn": f"形成通往{to_label}的必要变化",
            },
            "conflict_text": conflict_text,
            "value_shift": {
                "axis": "剧情推进",
                "from": from_label,
                "to": to_label,
            },
            "pov_character": pov_character,
            "hook": f"把情节推进到{to_label}",
            "thread_ops": [],
            "state_effects_expected": [],
            "depends_on": [f"ch_{previous_number:03d}"] if previous_number else [],
            "must_not": [],
            "status": "needs_review",
            "recovery_generated": True,
            "recovery_source": {
                "previous_anchor": previous_number,
                "next_anchor": next_number,
            },
        }
