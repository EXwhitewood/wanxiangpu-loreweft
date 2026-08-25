import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.db_models import (
    CharacterCognitiveState,
    ForeshadowingCausalEdge,
    ForeshadowingClue,
    ForeshadowingLine,
    ForeshadowingRevisionLog,
)

logger = logging.getLogger(__name__)


FORESHADOWING_STATE_TRANSITIONS = {
    # 方案 11 Part E2：补齐 active → revealing 直接转换。
    # reveal_readiness 达阈值时 active 可直接进入 revealing，不必经过 dormant。
    "planned": {"active", "aborted"},
    "active": {"dormant", "revealing", "revised", "aborted"},
    "dormant": {"active", "revealing", "revised", "aborted"},
    "revealing": {"resolved", "revised", "aborted"},
    "resolved": {"revised"},
    "revised": {"active", "dormant", "revealing", "aborted"},
    "aborted": set(),
}

COGNITIVE_LEVEL_MAP = {
    "blind": "fully_blind",
    "exposed": "fully_blind",
    "noticed": "vague_unease",
    "suspicious": "partial_clue",
    "misled": "misled",
    "verified": "high_suspicion",
    "informed": "fully_aware",
    "internalized": "fully_aware",
}


def _uuid(value: str | uuid.UUID) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def line_to_dict(line: ForeshadowingLine | None) -> dict[str, Any] | None:
    if not line:
        return None
    return {
        "id": str(line.id),
        "project_id": str(line.project_id),
        "name": line.name,
        "version": line.version,
        "status": line.status,
        "priority": line.priority,
        "secret_canonical_statement": line.secret_canonical_statement,
        "secret_truth_type": line.secret_truth_type,
        "secret_impact_level": line.secret_impact_level,
        "secret_spoiler_scope": line.secret_spoiler_scope or {},
        "bury_window_start": line.bury_window_start,
        "bury_window_end": line.bury_window_end,
        "maintenance_window_start": line.maintenance_window_start,
        "maintenance_window_end": line.maintenance_window_end,
        "reveal_window_start": line.reveal_window_start,
        "reveal_window_end": line.reveal_window_end,
        "latest_safe_reveal_chapter": line.latest_safe_reveal_chapter,
        "bury_rhythm": line.bury_rhythm,
        "total_clues_planned": line.total_clues_planned,
        "clues_placed": line.clues_placed,
        "complexity_score": line.complexity_score,
        "salience": line.salience,
        "reader_intended_state": line.reader_intended_state,
        "reader_allowed_interpretations": line.reader_allowed_interpretations or [],
        "reader_forbidden_interpretations": line.reader_forbidden_interpretations or [],
        "reader_fairness_level": line.reader_fairness_level,
        "reveal_readiness_score": line.reveal_readiness_score,
        "reveal_readiness_components": line.reveal_readiness_components or {},
        "resolved_chapter": line.resolved_chapter,
        "resolution_chapter": line.resolved_chapter,
        "resolution_summary": line.resolution_summary or "",
        "aliases": list((line.legacy_payload or {}).get("aliases") or []),
        "lifecycle_flags": dict((line.legacy_payload or {}).get("lifecycle_flags") or {}),
        "revision_count": line.revision_count or 0,
        "created_at": line.created_at.isoformat() if line.created_at else None,
        "updated_at": line.updated_at.isoformat() if line.updated_at else None,
    }


def cognitive_state_to_dict(state: CharacterCognitiveState) -> dict[str, Any]:
    return {
        "id": str(state.id),
        "project_id": str(state.project_id),
        "foreshadowing_line_id": str(state.foreshadowing_line_id),
        "character_name": state.character_name,
        "cognitive_level": state.cognitive_level,
        "cognitive_status": state.cognitive_status,
        "known_clues": state.known_clues or [],
        "last_knowledge_update_chapter": state.last_knowledge_update_chapter,
        "last_knowledge_update_event": state.last_knowledge_update_event,
        "valid_from_chapter": state.valid_from_chapter,
        "valid_to_chapter": state.valid_to_chapter,
        "is_intentionally_misled": state.is_intentionally_misled,
        "misled_by_character": state.misled_by_character,
        "misled_narrative": state.misled_narrative,
    }


class ForeshadowingService:
    async def create_foreshadowing_line(
        self, project_id: str | uuid.UUID, data: dict, db: AsyncSession
    ) -> dict:
        pid = _uuid(project_id)
        name = data.get("name", "").strip()
        if not name:
            raise ValueError("Foreshadowing name is required")

        existing = await self.get_foreshadowing_by_name(pid, name, db)
        if existing:
            return existing

        secret = data.get("secret", {})
        timeline = data.get("timeline", {})
        if not isinstance(timeline, dict):
            timeline = {}
        narrative = data.get("narrative_structure", {})

        bury_window = timeline.get("bury_window") or [None, None]
        reveal_window = timeline.get("reveal_window") or [None, None]

        if not isinstance(bury_window, (list, tuple)):
            bury_window = [None, None]
        if not isinstance(reveal_window, (list, tuple)):
            reveal_window = [None, None]

        bury_start = timeline.get(
            "bury_window_start",
            data.get("bury_window_start", bury_window[0] if len(bury_window) > 0 else None),
        )
        bury_end = timeline.get(
            "bury_window_end",
            data.get("bury_window_end", bury_window[1] if len(bury_window) > 1 else None),
        )
        reveal_start = timeline.get(
            "reveal_window_start",
            data.get("reveal_window_start", reveal_window[0] if len(reveal_window) > 0 else None),
        )
        reveal_end = timeline.get(
            "reveal_window_end",
            data.get("reveal_window_end", reveal_window[1] if len(reveal_window) > 1 else None),
        )

        line = ForeshadowingLine(
            project_id=pid,
            name=name,
            status=data.get("status", "planned"),
            priority=data.get("priority", "moderate"),
            secret_canonical_statement=(
                secret.get("canonical_statement")
                or data.get("description")
                or name
            ),
            secret_truth_type=secret.get("truth_type", "past_event"),
            secret_impact_level=secret.get("impact_level", data.get("priority", "moderate")),
            secret_spoiler_scope=secret.get("spoiler_scope", {}),
            bury_window_start=bury_start,
            bury_window_end=bury_end,
            reveal_window_start=reveal_start,
            reveal_window_end=reveal_end,
            latest_safe_reveal_chapter=timeline.get(
                "latest_safe_reveal_chapter",
                data.get("latest_safe_reveal_chapter"),
            ),
            total_clues_planned=narrative.get(
                "total_clues_planned",
                timeline.get("total_clues_planned", data.get("total_clues_planned", 0)),
            ),
            bury_rhythm=narrative.get("bury_rhythm", "gradual"),
            complexity_score=narrative.get("complexity_score", 0.0),
        )
        db.add(line)
        await db.flush()
        await self._log_revision(pid, line.id, "create", "system", "Created foreshadowing line", {}, line_to_dict(line), db)
        return line_to_dict(line) or {}

    async def get_foreshadowing_by_name(
        self, project_id: str | uuid.UUID, name: str, db: AsyncSession
    ) -> dict | None:
        result = await db.execute(
            select(ForeshadowingLine).where(
                ForeshadowingLine.project_id == _uuid(project_id),
                ForeshadowingLine.name == name,
            )
        )
        return line_to_dict(result.scalar_one_or_none())

    async def get_foreshadowing_by_id(
        self, foreshadowing_id: str | uuid.UUID, db: AsyncSession
    ) -> dict | None:
        result = await db.execute(
            select(ForeshadowingLine).where(ForeshadowingLine.id == _uuid(foreshadowing_id))
        )
        return line_to_dict(result.scalar_one_or_none())

    async def list_foreshadowing_lines(
        self,
        project_id: str | uuid.UUID,
        db: AsyncSession,
        status: str | None = None,
        statuses: list[str] | None = None,
        priority: str | None = None,
    ) -> list[dict]:
        stmt = select(ForeshadowingLine).where(ForeshadowingLine.project_id == _uuid(project_id))
        if status:
            stmt = stmt.where(ForeshadowingLine.status == status)
        if statuses:
            stmt = stmt.where(ForeshadowingLine.status.in_(statuses))
        if priority:
            stmt = stmt.where(ForeshadowingLine.priority == priority)
        stmt = stmt.order_by(ForeshadowingLine.created_at)
        result = await db.execute(stmt)
        return [line_to_dict(row) for row in result.scalars().all() if row]

    async def list_actionable_for_scene(
        self, project_id: str | uuid.UUID, chapter_number: int, db: AsyncSession
    ) -> list[dict]:
        lines = await self.list_foreshadowing_lines(
            project_id, db, statuses=["active", "dormant", "revealing", "planned"]
        )
        actionable = []
        for line in lines:
            action = self._action_for_chapter(line, chapter_number)
            if action:
                actionable.append({**line, "action": action})
        return actionable

    async def advance_lifecycle_for_chapter(
        self,
        project_id: str | uuid.UUID,
        chapter_number: int,
        db: AsyncSession,
        *,
        changed_by: str = "foreshadowing_lifecycle",
        publish_events: bool = True,
    ) -> list[dict]:
        """Advance foreshadowing statuses from chapter windows.

        This keeps lifecycle movement deterministic and conservative:
        planned lines become active in bury windows, active lines become
        dormant after bury/maintenance windows, and any non-terminal line in
        or past its reveal window becomes revealing.
        """
        pid = _uuid(project_id)
        stmt = select(ForeshadowingLine).where(
            ForeshadowingLine.project_id == pid,
            ForeshadowingLine.status.notin_(["resolved", "aborted"]),
        )
        result = await db.execute(stmt)
        lines = list(result.scalars().all())
        updates: list[dict] = []

        for line in lines:
            next_status, reason = self._lifecycle_status_for_chapter(line, chapter_number)
            if not next_status or next_status == line.status:
                continue

            before = line_to_dict(line)
            old_status = line.status
            line.status = next_status
            line.revision_count = (line.revision_count or 0) + 1
            line.updated_at = datetime.now(timezone.utc)
            if reason == "overdue_reveal":
                legacy = dict(line.legacy_payload or {})
                flags = dict(legacy.get("lifecycle_flags") or {})
                flags["overdue_reveal"] = True
                flags["overdue_since_chapter"] = int(chapter_number)
                legacy["lifecycle_flags"] = flags
                line.legacy_payload = legacy

            after = line_to_dict(line)
            await self._log_revision(
                pid,
                line.id,
                "lifecycle_auto_advance",
                changed_by,
                f"Lifecycle auto advance at chapter {chapter_number}: {old_status} -> {next_status} ({reason})",
                before,
                after,
                db,
            )
            updates.append({
                "id": str(line.id),
                "name": line.name,
                "from_status": old_status,
                "to_status": next_status,
                "reason": reason,
                "chapter_number": int(chapter_number),
            })
            if publish_events:
                await self._publish_lifecycle_event(
                    db,
                    pid,
                    line,
                    chapter_number,
                    old_status,
                    next_status,
                    reason,
                    changed_by,
                )

        if updates:
            await db.flush()
        return updates

    def _action_for_chapter(self, line: dict, chapter_number: int) -> str | None:
        status = line.get("status")
        bury_start = line.get("bury_window_start")
        bury_end = line.get("bury_window_end") or bury_start
        reveal_start = line.get("reveal_window_start")
        reveal_end = line.get("reveal_window_end") or reveal_start
        if status in {"planned", "active"} and bury_start and bury_start <= chapter_number <= (bury_end or bury_start):
            return "plant"
        if status in {"dormant", "revealing", "active"} and reveal_start and reveal_start <= chapter_number <= (reveal_end or reveal_start):
            return "reveal"
        return None

    def _lifecycle_status_for_chapter(
        self,
        line: ForeshadowingLine,
        chapter_number: int,
    ) -> tuple[str | None, str]:
        status = line.status
        bury_start = line.bury_window_start
        bury_end = line.bury_window_end or bury_start
        maintenance_start = line.maintenance_window_start
        maintenance_end = line.maintenance_window_end or maintenance_start
        reveal_start = line.reveal_window_start
        reveal_end = line.reveal_window_end or reveal_start
        latest_safe = line.latest_safe_reveal_chapter

        overdue_boundary = latest_safe or reveal_end
        if overdue_boundary and chapter_number > overdue_boundary and status != "revealing":
            return "revealing", "overdue_reveal"
        if reveal_start and reveal_start <= chapter_number <= (reveal_end or reveal_start):
            if status != "revealing":
                return "revealing", "reveal_window_open"
            return None, ""
        if bury_start and bury_start <= chapter_number <= (bury_end or bury_start):
            if status == "planned":
                return "active", "bury_window_open"
            return None, ""
        if maintenance_start and maintenance_start <= chapter_number <= (maintenance_end or maintenance_start):
            if status == "active":
                return "dormant", "maintenance_window_open"
            return None, ""
        if status == "active" and bury_end and chapter_number > bury_end:
            return "dormant", "bury_window_closed"
        return None, ""

    async def _publish_lifecycle_event(
        self,
        db: AsyncSession,
        project_id: uuid.UUID,
        line: ForeshadowingLine,
        chapter_number: int,
        old_status: str,
        new_status: str,
        reason: str,
        changed_by: str,
    ) -> None:
        try:
            from app.services.cross_system_event_bus import CrossSystemEventBus

            event_type = "FORESHADOWING_REVEALED.v1" if new_status == "revealing" else "FORESHADOWING_WINDOW_CHANGED.v1"
            await CrossSystemEventBus().publish_event(
                db=db,
                project_id=str(project_id),
                event_type=event_type,
                source_system=changed_by,
                priority="high" if reason in {"overdue_reveal", "reveal_window_open"} else "normal",
                payload={
                    "entity_type": "foreshadowing",
                    "entity_id": str(line.id),
                    "entity_name": line.name,
                    "change_type": "lifecycle_auto_advance",
                    "from_status": old_status,
                    "to_status": new_status,
                    "reason": reason,
                    "chapter_number": int(chapter_number),
                    "relevant_chapter": int(chapter_number),
                    "summary": f"伏笔线 {line.name} 自动推进：{old_status} -> {new_status}",
                    "meta": {
                        "bury_window_start": line.bury_window_start,
                        "bury_window_end": line.bury_window_end,
                        "reveal_window_start": line.reveal_window_start,
                        "reveal_window_end": line.reveal_window_end,
                        "latest_safe_reveal_chapter": line.latest_safe_reveal_chapter,
                    },
                },
            )
        except Exception as exc:
            # P1-C3 修复：Lifecycle event tracing 失败不应阻断生成主流程，
            # 但必须记录告警以便排查（如 CrossSystemEventBus 不可用或 DB 异常）。
            logger.warning(
                "foreshadowing lifecycle event publish failed for line %s: %s",
                line.id, exc,
            )

    async def transition_state(
        self,
        project_id: str | uuid.UUID,
        foreshadowing_id: str | uuid.UUID,
        new_status: str,
        changed_by: str,
        db: AsyncSession,
    ) -> dict:
        line = await db.get(ForeshadowingLine, _uuid(foreshadowing_id))
        if not line or line.project_id != _uuid(project_id):
            raise ValueError(f"Foreshadowing line not found: {foreshadowing_id}")
        allowed = FORESHADOWING_STATE_TRANSITIONS.get(line.status, set())
        if new_status not in allowed:
            raise ValueError(f"Invalid transition: {line.status} -> {new_status}. Allowed: {sorted(allowed)}")
        before = line_to_dict(line)
        line.status = new_status
        line.revision_count = (line.revision_count or 0) + 1
        line.updated_at = datetime.now(timezone.utc)
        await self._log_revision(
            _uuid(project_id), line.id, "state_change", changed_by,
            f"State: {before.get('status')} -> {new_status}", before, line_to_dict(line), db
        )
        return line_to_dict(line) or {}

    async def update_foreshadowing_line(
        self,
        project_id: str | uuid.UUID,
        foreshadowing_id: str | uuid.UUID,
        data: dict,
        db: AsyncSession,
        changed_by: str = "author",
    ) -> dict | None:
        line = await db.get(ForeshadowingLine, _uuid(foreshadowing_id))
        if not line or line.project_id != _uuid(project_id):
            return None

        before = line_to_dict(line)

        if "name" in data:
            line.name = data.get("name") or line.name
        if "priority" in data:
            line.priority = data.get("priority") or line.priority
        if "description" in data:
            line.secret_canonical_statement = data.get("description") or line.secret_canonical_statement
        if "status" in data:
            new_status = data.get("status")
            if new_status and new_status != line.status:
                # P1-17 修复：status 变更委托统一状态机 transition_state，避免绕过迁移规则
                try:
                    await self.transition_state(
                        project_id, foreshadowing_id, new_status, changed_by, db
                    )
                except Exception as exc:
                    # transition_state 校验失败（非法迁移/状态机异常）不应阻断其他字段更新
                    logger.warning(
                        "foreshadowing state transition failed for line %s: %s",
                        foreshadowing_id, exc,
                    )
        if "bury_window_start" in data:
            line.bury_window_start = data.get("bury_window_start")
        if "bury_window_end" in data:
            line.bury_window_end = data.get("bury_window_end")
        if "reveal_window_start" in data:
            line.reveal_window_start = data.get("reveal_window_start")
        if "reveal_window_end" in data:
            line.reveal_window_end = data.get("reveal_window_end")
        if "resolved_chapter" in data or "resolution_chapter" in data:
            line.resolved_chapter = data.get("resolved_chapter", data.get("resolution_chapter"))
        if "resolution_summary" in data:
            line.resolution_summary = str(data.get("resolution_summary") or "")
        if "aliases" in data:
            legacy = dict(line.legacy_payload or {})
            aliases = []
            seen = set()
            for alias in list(legacy.get("aliases") or []) + list(data.get("aliases") or []):
                text_alias = str(alias).strip()
                key = "".join(text_alias.lower().split())
                if text_alias and key not in seen and text_alias != line.name:
                    seen.add(key)
                    aliases.append(text_alias)
            legacy["aliases"] = aliases
            line.legacy_payload = legacy
        if "secret" in data and isinstance(data["secret"], dict):
            secret = data["secret"]
            if secret.get("canonical_statement"):
                line.secret_canonical_statement = secret["canonical_statement"]
            if secret.get("truth_type"):
                line.secret_truth_type = secret["truth_type"]
            if secret.get("impact_level"):
                line.secret_impact_level = secret["impact_level"]
            if isinstance(secret.get("spoiler_scope"), dict):
                line.secret_spoiler_scope = secret["spoiler_scope"]
        if "timeline" in data and isinstance(data["timeline"], dict):
            timeline = data["timeline"]
            bury_window = timeline.get("bury_window")
            reveal_window = timeline.get("reveal_window")
            if isinstance(bury_window, (list, tuple)) and bury_window:
                line.bury_window_start = bury_window[0] if len(bury_window) > 0 else line.bury_window_start
                line.bury_window_end = bury_window[1] if len(bury_window) > 1 else line.bury_window_end
            if "bury_window_start" in timeline:
                line.bury_window_start = timeline.get("bury_window_start")
            if "bury_window_end" in timeline:
                line.bury_window_end = timeline.get("bury_window_end")
            if isinstance(reveal_window, (list, tuple)) and reveal_window:
                line.reveal_window_start = reveal_window[0] if len(reveal_window) > 0 else line.reveal_window_start
                line.reveal_window_end = reveal_window[1] if len(reveal_window) > 1 else line.reveal_window_end
            if "reveal_window_start" in timeline:
                line.reveal_window_start = timeline.get("reveal_window_start")
            if "reveal_window_end" in timeline:
                line.reveal_window_end = timeline.get("reveal_window_end")
            if timeline.get("latest_safe_reveal_chapter") is not None:
                line.latest_safe_reveal_chapter = timeline.get("latest_safe_reveal_chapter")
            if timeline.get("maintenance_window"):
                maintenance_window = timeline.get("maintenance_window")
                if isinstance(maintenance_window, (list, tuple)) and maintenance_window:
                    line.maintenance_window_start = maintenance_window[0] if len(maintenance_window) > 0 else line.maintenance_window_start
                    line.maintenance_window_end = maintenance_window[1] if len(maintenance_window) > 1 else line.maintenance_window_end
        if "narrative_structure" in data and isinstance(data["narrative_structure"], dict):
            narrative = data["narrative_structure"]
            if narrative.get("total_clues_planned") is not None:
                line.total_clues_planned = narrative.get("total_clues_planned", line.total_clues_planned)
            if narrative.get("bury_rhythm"):
                line.bury_rhythm = narrative.get("bury_rhythm") or line.bury_rhythm
            if narrative.get("complexity_score") is not None:
                line.complexity_score = narrative.get("complexity_score", line.complexity_score)

        line.revision_count = (line.revision_count or 0) + 1
        line.updated_at = datetime.now(timezone.utc)
        await self._log_revision(
            _uuid(project_id),
            line.id,
            "update",
            changed_by,
            "Updated foreshadowing line",
            before,
            line_to_dict(line),
            db,
        )
        return line_to_dict(line)

    async def delete_foreshadowing_line(
        self,
        project_id: str | uuid.UUID,
        foreshadowing_id: str | uuid.UUID,
        db: AsyncSession,
    ) -> bool:
        pid = _uuid(project_id)
        fid = _uuid(foreshadowing_id)
        line = await db.get(ForeshadowingLine, fid)
        if not line or line.project_id != pid:
            return False
        await db.execute(
            delete(ForeshadowingCausalEdge).where(
                ForeshadowingCausalEdge.project_id == pid,
                or_(ForeshadowingCausalEdge.source_id == fid, ForeshadowingCausalEdge.target_id == fid),
            )
        )
        await db.execute(
            delete(ForeshadowingClue).where(
                ForeshadowingClue.project_id == pid,
                ForeshadowingClue.foreshadowing_line_id == fid,
            )
        )
        await db.execute(
            delete(CharacterCognitiveState).where(
                CharacterCognitiveState.project_id == pid,
                CharacterCognitiveState.foreshadowing_line_id == fid,
            )
        )
        await db.execute(
            delete(ForeshadowingRevisionLog).where(
                ForeshadowingRevisionLog.project_id == pid,
                ForeshadowingRevisionLog.foreshadowing_line_id == fid,
            )
        )
        await db.delete(line)
        await db.flush()
        return True

    async def set_character_cognitive_state(
        self,
        project_id: str | uuid.UUID,
        foreshadowing_line_id: str | uuid.UUID,
        character_name: str,
        cognitive_status: str,
        chapter_number: int,
        db: AsyncSession,
        cognitive_level: str | None = None,
        event: str = "",
    ) -> dict:
        pid = _uuid(project_id)
        fid = _uuid(foreshadowing_line_id)
        level = cognitive_level or COGNITIVE_LEVEL_MAP.get(cognitive_status, "fully_blind")

        line = (
            await db.execute(
                select(ForeshadowingLine).where(
                    ForeshadowingLine.id == fid,
                    ForeshadowingLine.project_id == pid,
                )
            )
        ).scalar_one_or_none()
        if not line:
            raise ValueError(f"Foreshadowing line not found in project: {fid}")

        await db.execute(
            update(CharacterCognitiveState)
            .where(
                CharacterCognitiveState.project_id == pid,
                CharacterCognitiveState.foreshadowing_line_id == fid,
                CharacterCognitiveState.character_name == character_name,
                CharacterCognitiveState.valid_to_chapter.is_(None),
            )
            .values(valid_to_chapter=max(chapter_number - 1, 0), updated_at=datetime.now(timezone.utc))
        )
        state = CharacterCognitiveState(
            project_id=pid,
            foreshadowing_line_id=fid,
            character_name=character_name,
            cognitive_status=cognitive_status,
            cognitive_level=level,
            last_knowledge_update_chapter=chapter_number,
            last_knowledge_update_event=event,
            valid_from_chapter=chapter_number,
        )
        db.add(state)
        await db.flush()
        return cognitive_state_to_dict(state)

    async def get_character_cognitive_states(
        self,
        project_id: str | uuid.UUID,
        character_name: str,
        db: AsyncSession,
        chapter_number: int | None = None,
    ) -> list[dict]:
        stmt = select(CharacterCognitiveState).where(
            CharacterCognitiveState.project_id == _uuid(project_id),
            CharacterCognitiveState.character_name == character_name,
        )
        stmt = self._apply_time_slice(stmt, chapter_number)
        result = await db.execute(stmt)
        return [cognitive_state_to_dict(row) for row in result.scalars().all()]

    async def list_cognitive_states_for_foreshadowing(
        self,
        project_id: str | uuid.UUID,
        foreshadowing_line_id: str | uuid.UUID,
        db: AsyncSession,
        chapter_number: int | None = None,
    ) -> list[dict]:
        stmt = select(CharacterCognitiveState).where(
            CharacterCognitiveState.project_id == _uuid(project_id),
            CharacterCognitiveState.foreshadowing_line_id == _uuid(foreshadowing_line_id),
        )
        stmt = self._apply_time_slice(stmt, chapter_number)
        result = await db.execute(stmt)
        return [cognitive_state_to_dict(row) for row in result.scalars().all()]

    def _apply_time_slice(self, stmt, chapter_number: int | None):
        if chapter_number is None:
            return stmt.where(CharacterCognitiveState.valid_to_chapter.is_(None))
        return stmt.where(
            CharacterCognitiveState.valid_from_chapter <= chapter_number,
            (
                CharacterCognitiveState.valid_to_chapter.is_(None)
                | (CharacterCognitiveState.valid_to_chapter >= chapter_number)
            ),
        )

    async def _log_revision(
        self,
        project_id: uuid.UUID,
        foreshadowing_line_id: uuid.UUID,
        revision_type: str,
        changed_by: str,
        summary: str,
        before: dict | None,
        after: dict | None,
        db: AsyncSession,
    ) -> None:
        db.add(
            ForeshadowingRevisionLog(
                project_id=project_id,
                foreshadowing_line_id=foreshadowing_line_id,
                revision_type=revision_type,
                changed_by=changed_by,
                change_summary=summary,
                before_snapshot=_json_safe(before) if before else {},
                after_snapshot=_json_safe(after) if after else {},
            )
        )


def _json_safe(obj):
    if isinstance(obj, uuid.UUID):
        return str(obj)
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, tuple):
        return [_json_safe(v) for v in obj]
    return obj
