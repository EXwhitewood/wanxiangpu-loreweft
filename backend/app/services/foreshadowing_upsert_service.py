from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.db_models import ForeshadowingClue, ForeshadowingLine
from app.services.foreshadowing_service import ForeshadowingService, line_to_dict

logger = logging.getLogger(__name__)


_OPERATION_ALIASES = {
    "plant": "plant",
    "new": "plant",
    "bury": "plant",
    "planted": "plant",
    "reinforce": "reinforce",
    "advance": "reinforce",
    "advanced": "reinforce",
    "supplement": "reinforce",
    "mention": "reinforce",
    "escalate": "reinforce",
    "remind": "reinforce",
    "maintain": "reinforce",
    "reveal": "reveal",
    "resolve": "reveal",
    "payoff": "reveal",
    "pay_off": "reveal",
    "paid_off": "reveal",
    "defer": "defer",
    "revise": "defer",
    "revised": "defer",
    "conflict": "defer",
    "abandon": "abandon",
    "abandoned": "abandon",
    "abort": "abandon",
    "aborted": "abandon",
}

_INVALID_NAMES = {"", "无", "无数据", "未知", "none", "null", "n/a", "伏笔", "线索"}


def normalize_foreshadowing_operation(value: Any, *, default: str = "plant") -> str:
    raw = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return _OPERATION_ALIASES.get(raw, default)


def normalize_foreshadowing_name(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[\s\-_—–·，。！？、：；‘’“”\"'（）()\[\]【】]+", "", text)
    return text


def is_valid_foreshadowing_name(value: Any) -> bool:
    text = str(value or "").strip()
    normalized = normalize_foreshadowing_name(text)
    if normalized in _INVALID_NAMES or len(normalized) < 2 or len(text) > 80:
        return False
    if not re.search(r"[\w\u3400-\u9fff]", text):
        return False
    return True


class ForeshadowingUpsertService:
    """Canonical SQLite writer for foreshadowing lines and their evidence."""

    def __init__(self) -> None:
        self._foreshadowing_service = ForeshadowingService()

    async def upsert_from_outline(
        self,
        db: AsyncSession,
        project_id: str | uuid.UUID,
        chapter_number: int,
        actions: list[dict],
        *,
        title: str = "",
        source_system: str = "narrative_sync",
    ) -> list[dict]:
        updates: list[dict] = []
        for action in actions or []:
            if not isinstance(action, dict):
                continue
            operation = normalize_foreshadowing_operation(
                action.get("action") or action.get("operation") or action.get("op")
            )
            payload = self._build_payload(
                chapter_number=chapter_number,
                item=action,
                operation=operation,
                title=title,
                source="outline",
            )
            result = await self._upsert_line(
                db,
                project_id,
                chapter_number,
                payload,
                operation=operation,
                source=source_system,
                allow_create=operation == "plant",
            )
            if not result:
                continue
            await self._publish_line_event(
                db,
                project_id,
                result,
                chapter_number,
                source_system=source_system,
                change_type="outline_upserted",
                operation=operation,
            )
            updates.append(result)
        return updates

    async def upsert_from_observation(
        self,
        db: AsyncSession,
        project_id: str | uuid.UUID,
        chapter_number: int,
        foreshadowing_items: list[dict],
        *,
        source_system: str = "worldview_projection",
    ) -> list[dict]:
        updates: list[dict] = []
        for item in foreshadowing_items or []:
            if not isinstance(item, dict):
                continue
            operation = normalize_foreshadowing_operation(item.get("operation") or item.get("action"))
            name = str(item.get("name") or "").strip()
            evidence_text = str(item.get("evidence_text") or "").strip()
            confidence = float(item.get("confidence") or 0.0)
            has_identity = bool(item.get("foreshadowing_id") or item.get("core_entity_id"))
            if not is_valid_foreshadowing_name(name) or not evidence_text:
                logger.info("Skipping foreshadowing observation without valid identity/evidence: %r", name)
                continue
            if operation == "plant" and not has_identity and confidence < 0.80:
                logger.info("Skipping low-confidence new foreshadowing observation %r: %.3f", name, confidence)
                continue

            payload = self._build_payload(
                chapter_number=chapter_number,
                item=item,
                operation=operation,
                title="",
                source="observation",
            )
            result = await self._upsert_line(
                db,
                project_id,
                chapter_number,
                payload,
                operation=operation,
                source=source_system,
                allow_create=operation == "plant",
            )
            if not result:
                continue
            if item.get("source_observation_id"):
                result = {**result, "source_observation_id": str(item["source_observation_id"])}
            await self._publish_line_event(
                db,
                project_id,
                result,
                chapter_number,
                source_system=source_system,
                change_type="observation_upserted",
                operation=operation,
            )
            updates.append(result)
        return updates

    async def _upsert_line(
        self,
        db: AsyncSession,
        project_id: str | uuid.UUID,
        chapter_number: int,
        payload: dict,
        *,
        operation: str,
        source: str,
        allow_create: bool,
    ) -> dict | None:
        pid = project_id if isinstance(project_id, uuid.UUID) else uuid.UUID(str(project_id))
        line = await self._resolve_line(db, pid, payload)
        if line is None:
            if not allow_create:
                logger.warning(
                    "Ignoring %s for unknown foreshadowing line %r",
                    operation,
                    payload.get("name"),
                )
                return None
            if not is_valid_foreshadowing_name(payload.get("name")):
                return None
            created = await self._foreshadowing_service.create_foreshadowing_line(
                pid,
                {
                    **payload,
                    "status": "active",
                    "timeline": self._creation_timeline(payload, chapter_number),
                },
                db,
            )
            line = await db.get(ForeshadowingLine, uuid.UUID(str(created["id"])))
        elif line.status == "aborted":
            logger.warning("Ignoring %s for aborted foreshadowing line %s", operation, line.name)
            return None

        if line is None:
            return None

        self._record_source_ref(line, chapter_number, source)

        update_data = self._metadata_updates(line, payload, chapter_number, operation)
        if update_data:
            await self._foreshadowing_service.update_foreshadowing_line(
                pid,
                line.id,
                update_data,
                db,
                changed_by=source,
            )

        evidence_recorded = await self._record_clue(
            db,
            pid,
            line,
            chapter_number,
            payload,
            operation,
        )
        await self._apply_operation(
            db,
            pid,
            line,
            operation,
            chapter_number,
            evidence_recorded=evidence_recorded,
            source=source,
        )
        await db.flush()
        await db.refresh(line)
        return line_to_dict(line)

    async def _resolve_line(
        self,
        db: AsyncSession,
        project_id: uuid.UUID,
        payload: dict,
    ) -> ForeshadowingLine | None:
        for key in ("foreshadowing_id", "core_entity_id", "clue_id"):
            candidate = payload.get(key)
            if not candidate:
                continue
            try:
                line = await db.get(ForeshadowingLine, uuid.UUID(str(candidate)))
            except (TypeError, ValueError):
                line = None
            if line and line.project_id == project_id:
                return line

        incoming_keys = {
            normalize_foreshadowing_name(payload.get("name")),
            *{
                normalize_foreshadowing_name(alias)
                for alias in list(payload.get("aliases") or [])
            },
        }
        incoming_keys.discard("")
        if not incoming_keys:
            return None

        rows = (
            await db.execute(
                select(ForeshadowingLine).where(ForeshadowingLine.project_id == project_id)
            )
        ).scalars().all()
        matches = []
        for row in rows:
            row_keys = {normalize_foreshadowing_name(row.name)}
            row_keys.update(
                normalize_foreshadowing_name(alias)
                for alias in list((row.legacy_payload or {}).get("aliases") or [])
            )
            if incoming_keys & row_keys:
                matches.append(row)
        if not matches:
            return None
        matches.sort(
            key=lambda row: (
                row.status == "aborted",
                -(row.clues_placed or 0),
                row.created_at or datetime.max,
            )
        )
        return matches[0]

    def _build_payload(
        self,
        *,
        chapter_number: int,
        item: dict,
        operation: str,
        title: str,
        source: str,
    ) -> dict:
        name = str(item.get("name") or item.get("thread_id") or "").strip()
        description = str(item.get("description") or item.get("summary") or name).strip()
        priority = str(item.get("priority") or ("high" if item.get("salience") == "high" else "moderate"))
        related = item.get("related_characters") or []
        if not isinstance(related, list):
            related = []
        aliases = item.get("aliases") or []
        if not isinstance(aliases, list):
            aliases = []
        timeline = dict(item.get("timeline") or {}) if isinstance(item.get("timeline"), dict) else {}
        explicit_reveal = item.get("reveal_window")
        if explicit_reveal and "reveal_window" not in timeline:
            timeline["reveal_window"] = explicit_reveal
        return {
            "name": name,
            "aliases": aliases,
            "description": description,
            "priority": priority,
            "operation": operation,
            "foreshadowing_id": item.get("foreshadowing_id"),
            "core_entity_id": item.get("core_entity_id"),
            "clue_id": item.get("clue_id"),
            "evidence_text": str(item.get("evidence_text") or "").strip(),
            "scene_index": int(item.get("scene_index") or 0),
            "pov_character": str(item.get("pov_character") or ""),
            "salience": str(item.get("salience") or "moderate"),
            "source_observation_id": item.get("source_observation_id"),
            "source_fingerprint": item.get("source_fingerprint"),
            "source_system": item.get("source_system") or source,
            "secret": {
                "canonical_statement": description,
                "truth_type": item.get("truth_type", "past_event"),
                "impact_level": item.get("impact_level", priority),
                "spoiler_scope": item.get("spoiler_scope")
                if isinstance(item.get("spoiler_scope"), dict)
                else {"characters": related},
            },
            "timeline": timeline,
            "narrative_structure": {
                "total_clues_planned": int(item.get("total_clues_planned") or 1),
                "bury_rhythm": item.get("mode") or item.get("salience") or "gradual",
                "complexity_score": float(item.get("complexity_score") or 0.0),
                "reader_intended_state": item.get("reader_intended_state", ""),
            },
            "title": title,
            "chapter_number": int(chapter_number),
        }

    @staticmethod
    def _creation_timeline(payload: dict, chapter_number: int) -> dict:
        incoming = dict(payload.get("timeline") or {})
        incoming["bury_window"] = incoming.get("bury_window") or [chapter_number, chapter_number]
        reveal = incoming.get("reveal_window")
        if reveal and isinstance(reveal, (list, tuple)):
            reveal_values = [int(value) for value in reveal if value is not None]
            if reveal_values and min(reveal_values) <= chapter_number:
                incoming.pop("reveal_window", None)
                incoming.pop("latest_safe_reveal_chapter", None)
        return incoming

    def _metadata_updates(
        self,
        line: ForeshadowingLine,
        payload: dict,
        chapter_number: int,
        operation: str,
    ) -> dict:
        updates: dict[str, Any] = {}
        current_alias_keys = {
            normalize_foreshadowing_name(alias)
            for alias in list((line.legacy_payload or {}).get("aliases") or [])
        }
        incoming_aliases = [
            str(alias).strip()
            for alias in list(payload.get("aliases") or [])
            if str(alias).strip()
            and normalize_foreshadowing_name(alias) not in current_alias_keys
            and normalize_foreshadowing_name(alias) != normalize_foreshadowing_name(line.name)
        ]
        if incoming_aliases:
            updates["aliases"] = incoming_aliases
        description = str(payload.get("description") or "").strip()
        if description and (not line.secret_canonical_statement or line.secret_canonical_statement == line.name):
            updates["description"] = description

        timeline = dict(payload.get("timeline") or {})
        if operation == "plant":
            bury = timeline.get("bury_window") or [chapter_number, chapter_number]
            starts = [value for value in (line.bury_window_start, bury[0]) if value is not None]
            ends = [value for value in (line.bury_window_end, bury[-1]) if value is not None]
            updates["bury_window_start"] = min(starts) if starts else chapter_number
            updates["bury_window_end"] = max(ends) if ends else chapter_number

        reveal = timeline.get("reveal_window")
        if reveal and isinstance(reveal, (list, tuple)):
            reveal_values = [int(value) for value in reveal if value is not None]
            if reveal_values and (operation == "reveal" or min(reveal_values) > chapter_number):
                updates["reveal_window_start"] = min(reveal_values)
                updates["reveal_window_end"] = max(reveal_values)
        elif operation == "reveal":
            updates["reveal_window_start"] = line.reveal_window_start or chapter_number
            updates["reveal_window_end"] = max(line.reveal_window_end or chapter_number, chapter_number)
        if timeline.get("latest_safe_reveal_chapter") is not None:
            updates["timeline"] = {
                "latest_safe_reveal_chapter": int(timeline["latest_safe_reveal_chapter"])
            }
        return updates

    async def _apply_operation(
        self,
        db: AsyncSession,
        project_id: uuid.UUID,
        line: ForeshadowingLine,
        operation: str,
        chapter_number: int,
        *,
        evidence_recorded: bool,
        source: str,
    ) -> None:
        async def transition(target: str) -> None:
            if line.status == target:
                return
            await self._foreshadowing_service.transition_state(
                project_id, line.id, target, source, db
            )

        if operation == "plant":
            if line.status == "planned":
                await transition("active")
            return
        if operation == "reinforce":
            if line.status == "planned":
                await transition("active")
            return
        if operation == "defer":
            if line.status in {"active", "dormant", "revealing"}:
                await transition("revised")
            self._set_lifecycle_flag(line, "explicitly_deferred_at", chapter_number)
            return
        if operation == "abandon":
            if line.status not in {"resolved", "aborted"}:
                await transition("aborted")
            return
        if operation != "reveal" or line.status == "resolved":
            return

        if line.status == "planned":
            await transition("active")
        if line.status in {"active", "dormant", "revised"}:
            await transition("revealing")
        if not evidence_recorded:
            self._set_lifecycle_flag(line, "awaiting_reveal_evidence", chapter_number)
            return
        if line.status == "revealing":
            await transition("resolved")
        line.resolved_chapter = int(chapter_number)
        line.resolution_summary = str(
            next(
                (
                    clue.clue_text
                    for clue in await self._line_clues(db, line.id)
                    if clue.is_revealed_clue and clue.chapter_number == int(chapter_number)
                ),
                line.resolution_summary or "",
            )
        )
        legacy = dict(line.legacy_payload or {})
        flags = dict(legacy.get("lifecycle_flags") or {})
        flags.pop("awaiting_reveal_evidence", None)
        flags.pop("overdue_reveal", None)
        legacy["lifecycle_flags"] = flags
        line.legacy_payload = legacy

    @staticmethod
    def _set_lifecycle_flag(line: ForeshadowingLine, key: str, value: Any) -> None:
        legacy = dict(line.legacy_payload or {})
        flags = dict(legacy.get("lifecycle_flags") or {})
        flags[key] = value
        legacy["lifecycle_flags"] = flags
        line.legacy_payload = legacy

    @staticmethod
    def _record_source_ref(line: ForeshadowingLine, chapter_number: int, source: str) -> None:
        legacy = dict(line.legacy_payload or {})
        refs = [dict(ref) for ref in list(legacy.get("source_refs") or []) if isinstance(ref, dict)]
        key = (int(chapter_number), str(source or ""))
        if key not in {(int(ref.get("chapter_number") or 0), str(ref.get("source") or "")) for ref in refs}:
            refs.append({"chapter_number": int(chapter_number), "source": str(source or "")})
            legacy["source_refs"] = refs
            line.legacy_payload = legacy

    async def _record_clue(
        self,
        db: AsyncSession,
        project_id: uuid.UUID,
        line: ForeshadowingLine,
        chapter_number: int,
        payload: dict,
        operation: str,
    ) -> bool:
        evidence_text = str(payload.get("evidence_text") or "").strip()
        if not evidence_text:
            return False
        scene_index = int(payload.get("scene_index") or 0)
        source_observation_id = self._optional_uuid(payload.get("source_observation_id"))
        source_fingerprint = str(payload.get("source_fingerprint") or "").strip() or None

        duplicate_filters = [
            ForeshadowingClue.project_id == project_id,
            ForeshadowingClue.foreshadowing_line_id == line.id,
        ]
        identity_filters = [
            (
                (ForeshadowingClue.chapter_number == int(chapter_number))
                & (ForeshadowingClue.scene_index == scene_index)
                & (ForeshadowingClue.clue_text == evidence_text)
            )
        ]
        if source_observation_id:
            identity_filters.append(ForeshadowingClue.source_observation_id == source_observation_id)
        if source_fingerprint:
            identity_filters.append(ForeshadowingClue.source_fingerprint == source_fingerprint)
        existing = await db.scalar(
            select(ForeshadowingClue.id).where(*duplicate_filters, or_(*identity_filters)).limit(1)
        )
        if existing:
            await self._refresh_clue_count(db, line.id)
            return True

        is_revealed = operation == "reveal"
        db.add(
            ForeshadowingClue(
                project_id=project_id,
                foreshadowing_line_id=line.id,
                clue_type="confirmation" if is_revealed else "supportive",
                chapter_number=int(chapter_number),
                scene_index=scene_index,
                clue_text=evidence_text,
                pov_character=str(payload.get("pov_character") or ""),
                salience_at_time=str(payload.get("salience") or "moderate"),
                is_revealed_clue=is_revealed,
                revealed_in_chapter=int(chapter_number) if is_revealed else None,
                source_observation_id=source_observation_id,
                source_fingerprint=source_fingerprint,
            )
        )
        await db.flush()
        await self._refresh_clue_count(db, line.id)
        await self._publish_clue_event(
            db,
            project_id,
            line_to_dict(line) or {},
            chapter_number,
            payload,
            evidence_text,
            is_revealed,
        )
        return True

    async def retract_chapter_evidence(
        self,
        db: AsyncSession,
        project_id: str | uuid.UUID,
        chapter_number: int,
        *,
        source: str = "chapter_retraction",
        hard: bool = False,
    ) -> int:
        pid = project_id if isinstance(project_id, uuid.UUID) else uuid.UUID(str(project_id))
        clues = (
            await db.execute(
                select(ForeshadowingClue).where(
                    ForeshadowingClue.project_id == pid,
                    ForeshadowingClue.chapter_number == int(chapter_number),
                )
            )
        ).scalars().all()
        affected_ids = {clue.foreshadowing_line_id for clue in clues}
        source_lines = list(
            (
                await db.execute(
                    select(ForeshadowingLine).where(ForeshadowingLine.project_id == pid)
                )
            ).scalars().all()
        )
        for source_line in source_lines:
            refs = list((source_line.legacy_payload or {}).get("source_refs") or [])
            if any(
                isinstance(ref, dict)
                and int(ref.get("chapter_number") or 0) == int(chapter_number)
                for ref in refs
            ):
                affected_ids.add(source_line.id)
        removed_reveal_ids = {
            clue.foreshadowing_line_id for clue in clues if clue.is_revealed_clue
        }
        for clue in clues:
            await db.delete(clue)
        await db.flush()

        orphan_line_ids: list[uuid.UUID] = []
        for line_id in affected_ids:
            line = await db.get(ForeshadowingLine, line_id)
            if not line:
                continue
            legacy = dict(line.legacy_payload or {})
            source_refs = [
                dict(ref)
                for ref in list(legacy.get("source_refs") or [])
                if isinstance(ref, dict)
                and int(ref.get("chapter_number") or 0) != int(chapter_number)
            ]
            legacy["source_refs"] = source_refs
            line.legacy_payload = legacy
            await self._refresh_clue_count(db, line_id)
            remaining = int(
                await db.scalar(
                    select(func.count()).select_from(ForeshadowingClue).where(
                        ForeshadowingClue.foreshadowing_line_id == line_id
                    )
                )
                or 0
            )
            if line_id in removed_reveal_ids:
                remaining_reveal = int(
                    await db.scalar(
                        select(func.count()).select_from(ForeshadowingClue).where(
                            ForeshadowingClue.foreshadowing_line_id == line_id,
                            ForeshadowingClue.is_revealed_clue.is_(True),
                        )
                    )
                    or 0
                )
                if not remaining_reveal and line.status == "resolved":
                    await self._foreshadowing_service.transition_state(
                        pid, line.id, "revised", source, db
                    )
                    line.resolved_chapter = None
                    line.resolution_summary = ""
            if remaining == 0 and not source_refs:
                if hard:
                    orphan_line_ids.append(line.id)
                else:
                    self._set_lifecycle_flag(line, "orphaned_source_chapter", int(chapter_number))
                    if line.status in {"active", "dormant", "revealing"}:
                        await self._foreshadowing_service.transition_state(
                            pid, line.id, "revised", source, db
                        )
        for line_id in orphan_line_ids:
            await self._foreshadowing_service.delete_foreshadowing_line(
                pid,
                line_id,
                db,
            )
        await db.flush()
        return len(affected_ids)

    @staticmethod
    def _optional_uuid(value: Any) -> uuid.UUID | None:
        if not value:
            return None
        try:
            return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
        except (TypeError, ValueError):
            return None

    @staticmethod
    async def _line_clues(db: AsyncSession, line_id: uuid.UUID) -> list[ForeshadowingClue]:
        return list(
            (
                await db.execute(
                    select(ForeshadowingClue)
                    .where(ForeshadowingClue.foreshadowing_line_id == line_id)
                    .order_by(ForeshadowingClue.chapter_number, ForeshadowingClue.scene_index)
                )
            ).scalars().all()
        )

    async def _refresh_clue_count(self, db: AsyncSession, line_id: uuid.UUID) -> None:
        count = await db.scalar(
            select(func.count()).select_from(ForeshadowingClue).where(
                ForeshadowingClue.foreshadowing_line_id == line_id
            )
        )
        line = await db.get(ForeshadowingLine, line_id)
        if line:
            line.clues_placed = int(count or 0)

    async def _publish_line_event(
        self,
        db: AsyncSession,
        project_id: str | uuid.UUID,
        line: dict,
        chapter_number: int,
        *,
        source_system: str,
        change_type: str,
        operation: str,
    ) -> None:
        try:
            from app.services.cross_system_event_bus import CrossSystemEventBus

            event_type = (
                "FORESHADOWING_REVEALED.v1"
                if operation == "reveal" and line.get("status") == "resolved"
                else "FORESHADOWING_WINDOW_CHANGED.v1"
            )
            await CrossSystemEventBus().publish_event(
                db=db,
                project_id=str(project_id),
                event_type=event_type,
                source_system=source_system,
                priority="high" if operation == "reveal" else "normal",
                payload={
                    "entity_type": "foreshadowing",
                    "entity_id": str(line.get("id") or ""),
                    "entity_name": line.get("name") or "",
                    "change_type": change_type,
                    "operation": operation,
                    "chapter_number": int(chapter_number),
                    "relevant_chapter": int(chapter_number),
                    "summary": f"Foreshadowing {line.get('name', '')} updated by {source_system}",
                    "meta": {
                        "status": line.get("status"),
                        "bury_window_start": line.get("bury_window_start"),
                        "bury_window_end": line.get("bury_window_end"),
                        "reveal_window_start": line.get("reveal_window_start"),
                        "reveal_window_end": line.get("reveal_window_end"),
                        "resolved_chapter": line.get("resolved_chapter"),
                    },
                },
            )
        except Exception:
            logger.warning("Failed to publish foreshadowing line event", exc_info=True)

    async def _publish_clue_event(
        self,
        db: AsyncSession,
        project_id: str | uuid.UUID,
        line: dict,
        chapter_number: int,
        item: dict,
        evidence_text: str,
        is_revealed: bool,
    ) -> None:
        try:
            from app.services.cross_system_event_bus import CrossSystemEventBus

            await CrossSystemEventBus().publish_event(
                db=db,
                project_id=str(project_id),
                event_type="FORESHADOWING_CLUE_RECORDED.v1",
                source_system=str(item.get("source_system") or "worldview_projection"),
                priority="high" if is_revealed else "normal",
                payload={
                    "entity_type": "foreshadowing_clue",
                    "foreshadowing_id": str(line.get("id") or ""),
                    "foreshadowing_name": line.get("name") or "",
                    "chapter_number": int(chapter_number),
                    "scene_index": int(item.get("scene_index") or 0),
                    "relevant_chapter": int(chapter_number),
                    "is_revealed_clue": bool(is_revealed),
                    "summary": f"Foreshadowing {line.get('name', '')} recorded new evidence",
                    "evidence_text": evidence_text,
                },
            )
        except Exception:
            logger.warning("Failed to publish foreshadowing clue event", exc_info=True)
