"""State settlement for an explicitly saved, user-authored chapter.

This is an auxiliary editor service.  It reuses factual extraction helpers but
does not invoke Writer, FBI repair, generation validation, or generation commit
gates.
"""
from __future__ import annotations

import copy
import hashlib
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.db_models import Chapter, ChapterBaseline, ChapterSnapshot, Project
from app.models.story_state import StoryState
from app.services.progression_service import ProgressionService
from app.services.scene_generation_pipeline import _fallback_extract_chapter_facts
from app.services.state_manager import StateManager


_USER_SAVE_REFERENCE_KEY = "_user_save_reference"


async def ensure_user_save_baseline(
    db: AsyncSession,
    *,
    project: Project,
    chapter_number: int,
    reference_snapshot: dict[str, Any],
) -> ChapterBaseline:
    """Persist the immutable pre-writeback reference before save returns."""
    result = await db.execute(
        select(ChapterBaseline).where(
            ChapterBaseline.project_id == project.id,
            ChapterBaseline.chapter_number == chapter_number,
        )
    )
    baseline = result.scalar_one_or_none()
    stored_reference = {
        key: copy.deepcopy(reference_snapshot[key])
        for key in ("outline_data", "core_data", "outline_version", "story_state")
        if key in reference_snapshot
    }
    if baseline is None:
        baseline = ChapterBaseline(
            project_id=project.id,
            chapter_number=chapter_number,
            outline_version=int(reference_snapshot.get("outline_version", 0) or 0),
            baseline_story_state=copy.deepcopy(reference_snapshot.get("story_state") or {}),
            baseline_chapter_state={_USER_SAVE_REFERENCE_KEY: stored_reference},
            snapshot_source="user_chapter_save",
        )
        db.add(baseline)
    else:
        metadata = copy.deepcopy(baseline.baseline_chapter_state or {})
        if _USER_SAVE_REFERENCE_KEY not in metadata:
            metadata[_USER_SAVE_REFERENCE_KEY] = stored_reference
            baseline.baseline_chapter_state = metadata
    await db.flush()
    return baseline


def load_user_save_reference(baseline: ChapterBaseline) -> dict[str, Any]:
    metadata = baseline.baseline_chapter_state if isinstance(baseline.baseline_chapter_state, dict) else {}
    stored = metadata.get(_USER_SAVE_REFERENCE_KEY)
    reference = copy.deepcopy(stored) if isinstance(stored, dict) else {}
    reference.setdefault("outline_version", int(baseline.outline_version or 0))
    reference.setdefault("story_state", copy.deepcopy(baseline.baseline_story_state or {}))
    return reference


def _character_cards(project: Project) -> list[dict[str, Any]]:
    core = project.core_data if isinstance(project.core_data, dict) else {}
    characters = core.get("characters")
    return [dict(item) for item in characters or [] if isinstance(item, dict)]


def _constraint_items(values: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in values if isinstance(values, list) else []:
        if isinstance(item, dict):
            result.append(dict(item))
        elif str(item or "").strip():
            result.append({"description": str(item).strip()})
    return result


class UserChapterSettlementService:
    async def settle(
        self,
        db: AsyncSession,
        *,
        project: Project,
        chapter: Chapter,
        reference_snapshot: dict[str, Any],
        summary: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        project_id = str(project.id)
        chapter_number = int(chapter.chapter_number)
        content = chapter.content or ""

        baseline = await ensure_user_save_baseline(
            db,
            project=project,
            chapter_number=chapter_number,
            reference_snapshot=reference_snapshot,
        )

        # Re-saving the latest user-authored chapter replaces, rather than
        # accumulates, its previous state effects.  Older-chapter cascading is
        # intentionally not performed here; downstream chapters are left for
        # explicit re-planning/reconciliation.
        baseline_state = baseline.baseline_story_state or reference_snapshot.get("story_state") or {}
        current_state = await StateManager().get_state(project_id)
        is_latest_settlement = int(current_state.active_chapter or 1) <= chapter_number + 1
        if is_latest_settlement:
            await StateManager().set_state(project_id, StoryState(**baseline_state))

        cards = _character_cards(project)
        summary = summary if isinstance(summary, dict) else {}
        facts = self._facts_from_summary(summary, content, cards)
        if facts is None:
            facts = _fallback_extract_chapter_facts(
                content,
                cards,
                scene_contract=None,
            )
            extraction_source = "deterministic_extractor_fallback"
        else:
            extraction_source = "chapter_summary_reuse"
        character_states = facts.get("character_states") if isinstance(facts, dict) else {}
        state_patch: dict[str, Any] = {
            "active_chapter": max(int(current_state.active_chapter or 1), chapter_number + 1),
            "active_scene": 1,
            "completed_events": list(facts.get("completed_events") or []),
            "active_constraints": _constraint_items(facts.get("active_constraints")),
        }
        if isinstance(character_states, dict) and character_states:
            state_patch["objective_state"] = {
                str(name): value
                for name, value in character_states.items()
                if str(name or "").strip()
            }

        settled_state = await StateManager().apply_patch(project_id, state_patch)

        chapter_state = {
            "established_facts": list(facts.get("established_facts") or []),
            "character_states": character_states if isinstance(character_states, dict) else {},
            "completed_events": list(facts.get("completed_events") or []),
            "active_constraints": list(facts.get("active_constraints") or []),
            "scene_ending": str(facts.get("scene_ending") or ""),
            "source": "user_chapter_save",
            "extraction_source": extraction_source,
        }
        snapshot_result = await db.execute(
            select(ChapterSnapshot).where(
                ChapterSnapshot.project_id == project.id,
                ChapterSnapshot.chapter_number == chapter_number,
            )
        )
        snapshot = snapshot_result.scalar_one_or_none()
        revision_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
        if snapshot is None:
            snapshot = ChapterSnapshot(
                project_id=project.id,
                chapter_number=chapter_number,
            )
            db.add(snapshot)
        snapshot.story_state = settled_state.model_dump()
        snapshot.chapter_state = chapter_state
        snapshot.execution_id = f"user_save:{revision_hash}"
        snapshot.lineage_version = max(int(snapshot.lineage_version or 0), chapter_number)
        snapshot.stale = False
        snapshot.committed_at = datetime.now(timezone.utc)

        progression_service = ProgressionService()
        progression_result = await progression_service.detect_candidates(
            db,
            project=project,
            project_id=project_id,
            chapter_number=chapter_number,
            scene_index=0,
            text=content,
            write=False,
            status="active",
        )
        progression_records = 0
        for mention in progression_result.get("mentions") or []:
            record = await progression_service.repository.add_candidate(
                db,
                project_id=project_id,
                entity_type=str(mention.get("entity_type") or "unknown"),
                entity_id=str(mention.get("entity_id") or mention.get("entity_name") or ""),
                chapter_number=chapter_number,
                scene_index=0,
                change_type="mention",
                after_value={
                    "entity_name": mention.get("entity_name", ""),
                    "alias": mention.get("alias", ""),
                },
                evidence_text=str(mention.get("evidence_text") or ""),
                status="active",
            )
            if record is not None:
                progression_records += 1
        await db.flush()
        return {
            "revision_hash": revision_hash,
            "extraction_source": extraction_source,
            "state_patch": state_patch,
            "chapter_state": chapter_state,
            "completed_events": len(chapter_state["completed_events"]),
            "character_states": len(chapter_state["character_states"]),
            "active_constraints": len(chapter_state["active_constraints"]),
            "progression_mentions": int(progression_result.get("mention_count", 0) or 0),
            "progression_records": progression_records,
            "active_chapter": settled_state.active_chapter,
        }

    @staticmethod
    def _facts_from_summary(
        summary: dict[str, Any],
        content: str,
        character_cards: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        core_event = str(summary.get("core_event") or "").strip()
        turning_point = str(summary.get("key_turning_point") or "").strip()
        changes = [
            str(item).strip()
            for item in summary.get("character_changes") or []
            if str(item or "").strip()
        ]
        if not core_event and not turning_point and not changes:
            return None

        events = []
        for item in (core_event, turning_point):
            if item and item not in events:
                events.append(item)
        character_states: dict[str, str] = {}
        for card in character_cards:
            name = str(card.get("name") or "").strip()
            if not name:
                continue
            related = [item for item in changes if name in item]
            if related:
                character_states[name] = "；".join(related)
        suspense = str(summary.get("unsolved_suspense") or "").strip()
        constraints = (
            [{"description": suspense, "source": "chapter_unsolved_suspense"}]
            if suspense
            else []
        )
        return {
            "established_facts": events,
            "character_states": character_states,
            "completed_events": events,
            "active_constraints": constraints,
            "scene_ending": content[-300:] if content else "",
        }


def get_user_chapter_settlement_service() -> UserChapterSettlementService:
    return UserChapterSettlementService()
