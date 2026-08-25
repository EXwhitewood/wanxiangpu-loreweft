"""Converge legacy foreshadowing data onto the canonical SQLite evidence model.

Dry-run is the default. Use ``--apply`` only after backing up loreweft.db.
The migration is conservative: it never invents reveal windows or semantically
merges differently named lines.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import func, select, text

from app.db.db_models import (
    Chapter,
    ForeshadowingClue,
    ForeshadowingLine,
    ForeshadowingRevisionLog,
    WorldviewObservation,
    async_session,
    create_tables,
)
from app.services.foreshadowing_service import line_to_dict
from app.services.foreshadowing_upsert_service import (
    is_valid_foreshadowing_name,
    normalize_foreshadowing_name,
)


def _uuid_or_none(value) -> uuid.UUID | None:
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


async def converge(
    *,
    apply: bool,
    project_id: str | None,
    cleanup_orphan_states: bool = False,
) -> dict:
    await create_tables()
    report = {
        "mode": "apply" if apply else "dry_run",
        "projects": 0,
        "lines_scanned": 0,
        "aliases_backfilled": 0,
        "source_refs_backfilled": 0,
        "clues_deduplicated": 0,
        "clues_linked_to_observations": 0,
        "false_resolutions_reopened": 0,
        "resolution_evidence_backfilled": 0,
        "same_chapter_reveal_windows_cleared": 0,
        "lines_marked_needs_reveal_plan": 0,
        "overdue_lines_marked_revealing": 0,
        "invalid_lines_aborted": 0,
        "exact_identity_duplicate_groups": [],
        "orphan_story_states_found": 0,
        "orphan_story_states_deleted": 0,
    }

    async with async_session() as db:
        has_story_states = bool(await db.scalar(text(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='story_states'"
        )))
        orphan_state_ids = []
        if has_story_states:
            orphan_state_ids = list(
                (
                    await db.execute(text(
                        "SELECT s.project_id FROM story_states AS s "
                        "LEFT JOIN projects AS p ON CAST(p.id AS TEXT) = s.project_id "
                        "WHERE p.id IS NULL"
                    ))
                ).scalars().all()
            )
        report["orphan_story_states_found"] = len(orphan_state_ids)
        if apply and cleanup_orphan_states and orphan_state_ids:
            await db.execute(text(
                "DELETE FROM story_states WHERE project_id NOT IN (SELECT CAST(id AS TEXT) FROM projects)"
            ))
            report["orphan_story_states_deleted"] = len(orphan_state_ids)

        line_stmt = select(ForeshadowingLine)
        if project_id:
            line_stmt = line_stmt.where(ForeshadowingLine.project_id == uuid.UUID(project_id))
        lines = list((await db.execute(line_stmt)).scalars().all())
        report["lines_scanned"] = len(lines)
        report["projects"] = len({str(line.project_id) for line in lines})

        by_identity: dict[tuple[str, str], list[ForeshadowingLine]] = defaultdict(list)
        for line in lines:
            by_identity[(str(line.project_id), normalize_foreshadowing_name(line.name))].append(line)
        report["exact_identity_duplicate_groups"] = [
            {
                "project_id": pid,
                "normalized_name": normalized,
                "line_ids": [str(line.id) for line in group],
                "names": [line.name for line in group],
            }
            for (pid, normalized), group in by_identity.items()
            if normalized and len(group) > 1
        ]

        observations = list(
            (
                await db.execute(
                    select(WorldviewObservation).where(
                        WorldviewObservation.entity_type == "foreshadowing",
                        WorldviewObservation.status.in_(["active", "promoted"]),
                    )
                )
            ).scalars().all()
        )
        observations_by_line: dict[str, list[WorldviewObservation]] = defaultdict(list)
        observations_by_name: dict[tuple[str, str], list[WorldviewObservation]] = defaultdict(list)
        for observation in observations:
            if observation.core_entity_id:
                observations_by_line[str(observation.core_entity_id)].append(observation)
            observations_by_name[
                (str(observation.project_id), normalize_foreshadowing_name(observation.entity_name))
            ].append(observation)

        max_chapter_by_project: dict[str, int] = {}
        for pid in {line.project_id for line in lines}:
            max_chapter_by_project[str(pid)] = int(
                await db.scalar(
                    select(func.max(Chapter.chapter_number)).where(Chapter.project_id == pid)
                )
                or 0
            )

        for line in lines:
            before = line_to_dict(line) or {}
            changed_reasons: list[str] = []
            line_observations = list(observations_by_line.get(str(line.id), []))
            line_observations.extend(
                observation
                for observation in observations_by_name.get(
                    (str(line.project_id), normalize_foreshadowing_name(line.name)), []
                )
                if observation not in line_observations
            )

            legacy = dict(line.legacy_payload or {})
            existing_aliases = list(legacy.get("aliases") or [])
            alias_keys = {normalize_foreshadowing_name(alias) for alias in existing_aliases}
            aliases = list(existing_aliases)
            for observation in line_observations:
                payload = observation.payload or {}
                for alias in list(payload.get("aliases") or []):
                    alias_text = str(alias).strip()
                    key = normalize_foreshadowing_name(alias_text)
                    if alias_text and key and key not in alias_keys and key != normalize_foreshadowing_name(line.name):
                        alias_keys.add(key)
                        aliases.append(alias_text)
                        report["aliases_backfilled"] += 1
            if aliases != existing_aliases:
                legacy["aliases"] = aliases
                changed_reasons.append("aliases_backfilled")

            source_refs = [
                dict(ref) for ref in list(legacy.get("source_refs") or []) if isinstance(ref, dict)
            ]
            source_ref_keys = {
                (int(ref.get("chapter_number") or 0), str(ref.get("source") or ""))
                for ref in source_refs
            }
            for observation in line_observations:
                key = (int(observation.chapter_number), str(observation.extraction_source or "worldview_projection"))
                if key not in source_ref_keys:
                    source_ref_keys.add(key)
                    source_refs.append({"chapter_number": key[0], "source": key[1]})
                    report["source_refs_backfilled"] += 1
                    changed_reasons.append("source_ref_backfilled")
            if source_refs:
                legacy["source_refs"] = source_refs

            clues = list(
                (
                    await db.execute(
                        select(ForeshadowingClue)
                        .where(ForeshadowingClue.foreshadowing_line_id == line.id)
                        .order_by(ForeshadowingClue.created_at, ForeshadowingClue.id)
                    )
                ).scalars().all()
            )
            seen_clues: set[tuple[int, int, str]] = set()
            kept_clues: list[ForeshadowingClue] = []
            for clue in clues:
                key = (int(clue.chapter_number), int(clue.scene_index or 0), clue.clue_text.strip())
                if key in seen_clues:
                    await db.delete(clue)
                    report["clues_deduplicated"] += 1
                    changed_reasons.append("duplicate_clue_removed")
                    continue
                seen_clues.add(key)
                kept_clues.append(clue)

            for clue in kept_clues:
                if clue.source_observation_id:
                    continue
                match = next(
                    (
                        observation
                        for observation in line_observations
                        if observation.chapter_number == clue.chapter_number
                        and observation.evidence_text.strip() == clue.clue_text.strip()
                    ),
                    None,
                )
                if match:
                    clue.source_observation_id = match.id
                    clue.source_fingerprint = match.fingerprint
                    report["clues_linked_to_observations"] += 1
                    changed_reasons.append("clue_provenance_linked")

            reveal_clues = [clue for clue in kept_clues if clue.is_revealed_clue]
            if line.status == "resolved" and not reveal_clues:
                line.status = "revised"
                line.resolved_chapter = None
                line.resolution_summary = ""
                report["false_resolutions_reopened"] += 1
                changed_reasons.append("missing_reveal_evidence")
            elif reveal_clues:
                reveal_clue = min(reveal_clues, key=lambda clue: (clue.revealed_in_chapter or clue.chapter_number, clue.scene_index or 0))
                resolved_chapter = int(reveal_clue.revealed_in_chapter or reveal_clue.chapter_number)
                if line.resolved_chapter != resolved_chapter or not line.resolution_summary:
                    line.resolved_chapter = resolved_chapter
                    line.resolution_summary = line.resolution_summary or reveal_clue.clue_text
                    report["resolution_evidence_backfilled"] += 1
                    changed_reasons.append("resolution_evidence_backfilled")

            if (
                not reveal_clues
                and line.reveal_window_start is not None
                and line.reveal_window_start == line.bury_window_start
                and line.reveal_window_end == line.bury_window_end
            ):
                line.reveal_window_start = None
                line.reveal_window_end = None
                line.latest_safe_reveal_chapter = None
                report["same_chapter_reveal_windows_cleared"] += 1
                changed_reasons.append("synthetic_reveal_window_cleared")

            flags = dict(legacy.get("lifecycle_flags") or {})
            if line.status not in {"resolved", "aborted"} and line.reveal_window_start is None:
                if not flags.get("needs_reveal_plan"):
                    flags["needs_reveal_plan"] = True
                    report["lines_marked_needs_reveal_plan"] += 1
                    changed_reasons.append("needs_reveal_plan")

            boundary = line.latest_safe_reveal_chapter or line.reveal_window_end
            current_chapter = max_chapter_by_project.get(str(line.project_id), 0)
            if (
                boundary
                and current_chapter > boundary
                and line.status not in {"resolved", "aborted", "revealing"}
            ):
                line.status = "revealing"
                flags["overdue_reveal"] = True
                flags["overdue_since_chapter"] = boundary + 1
                report["overdue_lines_marked_revealing"] += 1
                changed_reasons.append("overdue_reveal")

            if not is_valid_foreshadowing_name(line.name) and line.status != "aborted":
                line.status = "aborted"
                flags["invalid_identity"] = True
                report["invalid_lines_aborted"] += 1
                changed_reasons.append("invalid_identity")

            legacy["lifecycle_flags"] = flags
            line.legacy_payload = legacy
            line.clues_placed = len(kept_clues)
            if changed_reasons:
                line.revision_count = int(line.revision_count or 0) + 1
                line.updated_at = datetime.now(timezone.utc)
                db.add(
                    ForeshadowingRevisionLog(
                        project_id=line.project_id,
                        foreshadowing_line_id=line.id,
                        revision_type="database_convergence",
                        changed_by="converge_story_memory",
                        change_summary=",".join(sorted(set(changed_reasons))),
                        before_snapshot=before,
                        after_snapshot=line_to_dict(line) or {},
                    )
                )

        await db.flush()
        if apply:
            await db.commit()
        else:
            await db.rollback()
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--project-id")
    parser.add_argument("--cleanup-orphan-states", action="store_true")
    args = parser.parse_args()
    report = asyncio.run(converge(
        apply=args.apply,
        project_id=args.project_id,
        cleanup_orphan_states=args.cleanup_orphan_states,
    ))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
