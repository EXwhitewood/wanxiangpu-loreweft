import uuid
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.db_models import (
    CrossSystemEvent,
    CrossSystemEventDelivery,
    _IS_SQLITE,
)

logger = logging.getLogger(__name__)

EVENT_ROUTING = {
    "WORLD_RULE_CREATED.v1": {"target_systems": ["outline", "editor"]},
    "WORLD_RULE_MODIFIED.v1": {"target_systems": ["outline", "editor"]},
    "CHARACTER_MODIFIED.v1": {"target_systems": ["outline", "editor"]},
    "OUTLINE_SAVED.v1": {"target_systems": ["outline", "editor", "worldbuilder"]},
    "OUTLINE_DELETED.v1": {"target_systems": ["outline", "editor", "worldbuilder"]},
    "FORESHADOWING_WINDOW_CHANGED.v1": {"target_systems": ["outline", "editor", "worldbuilder"]},
    "FORESHADOWING_CLUE_RECORDED.v1": {"target_systems": ["outline", "editor", "worldbuilder"]},
    "CHAPTER_GENERATED.v1": {"target_systems": ["worldbuilder", "outline"]},
    "CHAPTER_SYNCED.v1": {"target_systems": ["outline", "editor", "worldbuilder"]},
    "CHAPTER_UPDATED.v1": {"target_systems": ["outline", "editor", "worldbuilder"]},
    "CHAPTER_DELETED.v1": {"target_systems": ["outline", "editor", "worldbuilder"]},
    "FORESHADOWING_REVEALED.v1": {"target_systems": ["outline", "worldbuilder"]},
    "CONSISTENCY_ALERT.v1": {"target_systems": ["outline", "editor", "worldbuilder"]},
    "PROPOSAL_CREATED.v1": {"target_systems": []},
    "PROPOSAL_RESOLVED.v1": {"target_systems": []},
    "PROPOSAL_STALE.v1": {"target_systems": []},
}


def _event_matches_relevant_chapter(
    delivery: CrossSystemEventDelivery,
    event: CrossSystemEvent,
    relevant_chapter: int,
) -> bool:
    payload = getattr(event, "payload", None) or {}
    affected = payload.get("affected_chapters") or []
    if affected:
        try:
            return int(relevant_chapter) in {int(chapter) for chapter in affected}
        except (TypeError, ValueError):
            return False

    explicit = payload.get("relevant_chapter")
    if explicit is not None:
        try:
            return int(explicit) == int(relevant_chapter)
        except (TypeError, ValueError):
            return False

    chapter_range = getattr(delivery, "relevant_chapter_range", None)
    if chapter_range:
        try:
            if "-" in chapter_range:
                start, end = chapter_range.split("-", 1)
                return int(start) <= int(relevant_chapter) <= int(end)
            return int(relevant_chapter) in {
                int(chapter.strip()) for chapter in chapter_range.split(",")
            }
        except (TypeError, ValueError):
            return False

    return True


class CrossSystemEventBus:
    async def _next_sequence_number(self, db: AsyncSession, project_id: str) -> int:
        try:
            stmt = select(
                text("COALESCE(MAX(sequence_number), 0) + 1")
            ).select_from(CrossSystemEvent).where(CrossSystemEvent.project_id == project_id)
            result = await db.execute(stmt)
            row = result.scalar()
            return row if row else 1
        except Exception:
            logger.warning("Failed to get next sequence number for %s", project_id, exc_info=True)
            return 1

    async def publish_event(
        self,
        db: AsyncSession,
        project_id: str,
        event_type: str,
        source_system: str,
        priority: str = "normal",
        payload: dict | None = None,
        max_retries: int = 3,
    ) -> CrossSystemEvent:
        from sqlalchemy.exc import IntegrityError

        for attempt in range(max_retries):
            nested = await db.begin_nested()
            try:
                seq = await self._next_sequence_number(db, project_id)
                event = CrossSystemEvent(
                    project_id=project_id,
                    event_type=event_type,
                    source_system=source_system,
                    priority=priority,
                    payload=payload or {},
                    sequence_number=seq,
                )
                db.add(event)
                await db.flush()

                routing = EVENT_ROUTING.get(event_type, {})
                target_systems = routing.get("target_systems", [])
                for ts in target_systems:
                    delivery = CrossSystemEventDelivery(
                        event_id=event.id,
                        project_id=project_id,
                        target_system=ts,
                        status="pending",
                    )
                    db.add(delivery)
                await db.flush()
                await nested.commit()
                return event
            except IntegrityError:
                await nested.rollback()
                logger.warning(
                    "Sequence collision on attempt %d for project %s, retrying",
                    attempt + 1, project_id,
                )
                if attempt == max_retries - 1:
                    raise
            except Exception:
                await nested.rollback()
                raise
        raise RuntimeError(f"Failed to publish event after {max_retries} retries")

    async def claim_deliveries(
        self,
        db: AsyncSession,
        project_id: str,
        target_system: str,
        consumer_id: str,
        lease_ttl_seconds: int = 300,
        limit: int = 20,
    ) -> list[CrossSystemEventDelivery]:
        try:
            batch_id = str(uuid.uuid4())
            now = datetime.now(timezone.utc)
            lease_cutoff = now - timedelta(seconds=max(int(lease_ttl_seconds), 1))

            # A consumer can disappear after claiming a delivery.  Claims are
            # represented by status=delivered with no delivered_at timestamp;
            # return expired claims to pending instead of losing the event.
            await db.execute(
                update(CrossSystemEventDelivery)
                .where(
                    CrossSystemEventDelivery.project_id == project_id,
                    CrossSystemEventDelivery.target_system == target_system,
                    CrossSystemEventDelivery.status == "delivered",
                    CrossSystemEventDelivery.delivered_at.is_(None),
                    CrossSystemEventDelivery.claimed_at <= lease_cutoff,
                )
                .values(
                    status="pending",
                    claimed_by=None,
                    claim_batch_id=None,
                    claimed_at=None,
                )
            )
            await db.execute(
                update(CrossSystemEventDelivery)
                .where(
                    CrossSystemEventDelivery.project_id == project_id,
                    CrossSystemEventDelivery.target_system == target_system,
                    CrossSystemEventDelivery.status == "snoozed",
                    CrossSystemEventDelivery.snoozed_until <= now,
                )
                .values(status="pending", snoozed_until=None)
            )

            if _IS_SQLITE:
                stmt = (
                    select(CrossSystemEventDelivery)
                    .where(
                        CrossSystemEventDelivery.project_id == project_id,
                        CrossSystemEventDelivery.target_system == target_system,
                        CrossSystemEventDelivery.status == "pending",
                    )
                    .limit(limit)
                )
                result = await db.execute(stmt)
                rows = result.scalars().all()
                if not rows:
                    return []
                ids = [r.id for r in rows]
                await db.execute(
                    update(CrossSystemEventDelivery)
                    .where(
                        CrossSystemEventDelivery.id.in_(ids),
                        CrossSystemEventDelivery.status == "pending",
                    )
                    .values(
                        status="delivered",
                        claimed_by=consumer_id,
                        claim_batch_id=batch_id,
                        claimed_at=now,
                        lease_ttl_seconds=max(int(lease_ttl_seconds), 1),
                    )
                )
                await db.flush()
                verify_stmt = (
                    select(CrossSystemEventDelivery)
                    .where(CrossSystemEventDelivery.claim_batch_id == batch_id)
                    .execution_options(populate_existing=True)
                )
                verify_result = await db.execute(verify_stmt)
                return list(verify_result.scalars().all())
            else:
                stmt = (
                    select(CrossSystemEventDelivery)
                    .where(
                        CrossSystemEventDelivery.project_id == project_id,
                        CrossSystemEventDelivery.target_system == target_system,
                        CrossSystemEventDelivery.status == "pending",
                    )
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
                result = await db.execute(stmt)
                rows = result.scalars().all()
                if not rows:
                    return []
                ids = [r.id for r in rows]
                await db.execute(
                    update(CrossSystemEventDelivery)
                    .where(
                        CrossSystemEventDelivery.id.in_(ids),
                        CrossSystemEventDelivery.status == "pending",
                    )
                    .values(
                        status="delivered",
                        claimed_by=consumer_id,
                        claim_batch_id=batch_id,
                        claimed_at=now,
                        lease_ttl_seconds=max(int(lease_ttl_seconds), 1),
                    )
                )
                await db.flush()
                for r in rows:
                    r.status = "delivered"
                    r.claimed_by = consumer_id
                    r.claim_batch_id = batch_id
                    r.claimed_at = now
                return list(rows)
        except Exception:
            logger.warning("Failed to claim deliveries for %s/%s", project_id, target_system, exc_info=True)
            raise

    async def get_deliveries(
        self,
        db: AsyncSession,
        project_id: str,
        target_system: str,
        status_filter: str | None = None,
        relevant_chapter: int | None = None,
        claimed_by: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        try:
            stmt = (
                select(CrossSystemEventDelivery, CrossSystemEvent)
                .join(CrossSystemEvent, CrossSystemEventDelivery.event_id == CrossSystemEvent.id)
                .where(
                    CrossSystemEventDelivery.project_id == project_id,
                    CrossSystemEventDelivery.target_system == target_system,
                )
            )
            if status_filter:
                stmt = stmt.where(CrossSystemEventDelivery.status == status_filter)
            if claimed_by:
                stmt = stmt.where(CrossSystemEventDelivery.claimed_by == claimed_by)
            result = await db.execute(stmt)
            rows = result.all()
            if relevant_chapter is not None:
                rows = [
                    (delivery, event) for delivery, event in rows
                    if _event_matches_relevant_chapter(delivery, event, relevant_chapter)
                ]
            return [
                {
                    "delivery": delivery,
                    "event": event,
                }
                for delivery, event in rows[:limit]
            ]
        except Exception:
            logger.warning("Failed to get deliveries for %s/%s", project_id, target_system, exc_info=True)
            return []

    async def mark_delivered(self, db: AsyncSession, delivery_ids: list) -> None:
        if not delivery_ids:
            return
        try:
            await db.execute(
                update(CrossSystemEventDelivery)
                .where(CrossSystemEventDelivery.id.in_(delivery_ids))
                .values(status="delivered", delivered_at=datetime.now(timezone.utc))
            )
            await db.flush()
        except Exception:
            logger.warning("Failed to mark delivered for ids %s", delivery_ids, exc_info=True)

    async def mark_acknowledged(
        self,
        db: AsyncSession,
        delivery_ids: list,
        resolved_by: str = "user",
        consumer_id: str | None = None,
    ) -> None:
        if not delivery_ids:
            return
        try:
            stmt = (
                update(CrossSystemEventDelivery)
                .where(CrossSystemEventDelivery.id.in_(delivery_ids))
                .values(status="acknowledged", resolved_by=resolved_by, acknowledged_at=datetime.now(timezone.utc))
            )
            if consumer_id:
                stmt = stmt.where(CrossSystemEventDelivery.claimed_by == consumer_id)
            await db.execute(stmt)
            await db.flush()
        except Exception:
            logger.warning("Failed to mark acknowledged for ids %s", delivery_ids, exc_info=True)

    async def release_claim(self, db: AsyncSession, delivery_ids: list, consumer_id: str) -> None:
        if not delivery_ids:
            return
        try:
            await db.execute(
                update(CrossSystemEventDelivery)
                .where(
                    CrossSystemEventDelivery.id.in_(delivery_ids),
                    CrossSystemEventDelivery.claimed_by == consumer_id,
                )
                .values(
                    status="pending",
                    claimed_by=None,
                    claim_batch_id=None,
                    claimed_at=None,
                )
            )
            await db.flush()
        except Exception:
            logger.warning("Failed to release claim for ids %s", delivery_ids, exc_info=True)

    async def snooze(self, db: AsyncSession, delivery_ids: list, until: datetime) -> None:
        if not delivery_ids:
            return
        try:
            await db.execute(
                update(CrossSystemEventDelivery)
                .where(CrossSystemEventDelivery.id.in_(delivery_ids))
                .values(status="snoozed", snoozed_until=until)
            )
            await db.flush()
        except Exception:
            logger.warning("Failed to snooze for ids %s", delivery_ids, exc_info=True)

    async def _fetch_deliveries_with_events(self, db: AsyncSession, ids: list) -> list[dict[str, Any]]:
        if not ids:
            return []
        try:
            stmt = (
                select(CrossSystemEventDelivery, CrossSystemEvent)
                .join(CrossSystemEvent, CrossSystemEventDelivery.event_id == CrossSystemEvent.id)
                .where(CrossSystemEventDelivery.id.in_(ids))
            )
            result = await db.execute(stmt)
            rows = result.all()
            return [
                {
                    "delivery": delivery,
                    "event": event,
                }
                for delivery, event in rows
            ]
        except Exception:
            logger.warning("Failed to fetch deliveries with events for ids %s", ids, exc_info=True)
            return []
