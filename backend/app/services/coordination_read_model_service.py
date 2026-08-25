import uuid
import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.db_models import CrossSystemEvent, CrossSystemEventDelivery, CrossSystemProposal
from app.services.cross_system_event_bus import CrossSystemEventBus
from app.services.cross_system_proposal_service import CrossSystemProposalService

logger = logging.getLogger(__name__)

SYSTEM_DOMAIN_MAP = {
    "outline": ["outline_chapter", "thread_plan", "worldview"],
    "worldbuilder": ["world_rule", "character", "location", "foreshadowing", "worldview"],
    "editor": ["world_rule", "character", "location", "foreshadowing", "outline_chapter", "thread_plan", "worldview"],
}


class CoordinationReadModelService:
    def __init__(self) -> None:
        self._event_bus = CrossSystemEventBus()
        self._proposal_service = CrossSystemProposalService()

    async def build_coordination_prompt(
        self,
        db: AsyncSession,
        project_id: str,
        target_system: str,
        relevant_chapter: int | None = None,
        session_id: str | None = None,
        token_budget: int = 300,
    ) -> str:
        try:
            consumer_id = session_id or f"{target_system}-{uuid.uuid4().hex[:8]}"

            new_deliveries = await self._event_bus.claim_deliveries(
                db, project_id, target_system, consumer_id,
            )
            new_ids = {d.id for d in new_deliveries} if new_deliveries else set()
            if new_deliveries:
                await db.commit()

            all_delivered = await self._event_bus.get_deliveries(
                db, project_id, target_system,
                status_filter="delivered", claimed_by=consumer_id,
                relevant_chapter=relevant_chapter,
            )

            domains = SYSTEM_DOMAIN_MAP.get(target_system, [])
            pending_proposals = []
            for domain in domains:
                proposals = await self._proposal_service.get_pending_for_system(
                    db, project_id, domain, relevant_chapter=relevant_chapter,
                )
                pending_proposals.extend(proposals)

            critical_items: list[str] = []
            high_items: list[str] = []
            normal_items: list[str] = []

            for item in all_delivered:
                delivery = item.get("delivery")
                event = item.get("event")
                source_tag = "new" if delivery and delivery.id in new_ids else "prior"

                event_type = getattr(event, "event_type", "unknown") if event else "unknown"
                priority = getattr(event, "priority", "normal") if event else "normal"
                payload = getattr(event, "payload", {}) if event else {}

                line = f"[{source_tag}] {event_type}"
                if payload:
                    summary_parts = []
                    for k, v in list(payload.items())[:3]:
                        summary_parts.append(f"{k}={v}")
                    line += f" ({', '.join(str(p) for p in summary_parts)})"

                if priority == "critical":
                    critical_items.append(line)
                elif priority == "high":
                    high_items.append(line)
                else:
                    normal_items.append(line)

            proposal_lines: list[str] = []
            for p in pending_proposals:
                line = f"[proposal] {p.proposal_type}: {p.title}"
                if p.description:
                    line += f" - {p.description[:80]}"
                proposal_lines.append(line)

            sections: list[str] = []
            if critical_items:
                sections.append("## 紧急行动\n" + "\n".join(f"- {i}" for i in critical_items))
            if high_items:
                sections.append("## 高优先级\n" + "\n".join(f"- {i}" for i in high_items))
            if proposal_lines:
                sections.append("## 待审批提案\n" + "\n".join(f"- {i}" for i in proposal_lines))
            if normal_items:
                sections.append("## 常规通知\n" + "\n".join(f"- {i}" for i in normal_items))

            if not sections:
                return ""

            result = "\n\n".join(sections)

            estimated_tokens = len(result) // 2
            if estimated_tokens > token_budget:
                ratio = token_budget / estimated_tokens
                char_limit = int(len(result) * ratio)
                result = result[:char_limit] + "\n...[截断]"

            return result
        except Exception:
            logger.warning(
                "Failed to build coordination prompt for %s/%s",
                project_id, target_system, exc_info=True,
            )
            return ""

    async def get_status_summary(
        self,
        db: AsyncSession,
        project_id: str,
        target_system: str,
    ) -> dict[str, Any]:
        try:
            pending_count_stmt = (
                select(func.count())
                .select_from(CrossSystemEventDelivery)
                .where(
                    CrossSystemEventDelivery.project_id == project_id,
                    CrossSystemEventDelivery.target_system == target_system,
                    CrossSystemEventDelivery.status.in_(["pending", "delivered"]),
                )
            )
            pending_result = await db.execute(pending_count_stmt)
            pending_deliveries_count = pending_result.scalar() or 0

            conflict_count_stmt = (
                select(func.count())
                .select_from(CrossSystemEventDelivery)
                .join(CrossSystemEvent, CrossSystemEventDelivery.event_id == CrossSystemEvent.id)
                .where(
                    CrossSystemEventDelivery.project_id == project_id,
                    CrossSystemEventDelivery.target_system == target_system,
                    CrossSystemEvent.event_type == "CONSISTENCY_ALERT.v1",
                    CrossSystemEventDelivery.status.in_(["pending", "delivered"]),
                )
            )
            conflict_result = await db.execute(conflict_count_stmt)
            active_conflicts_count = conflict_result.scalar() or 0

            domains = SYSTEM_DOMAIN_MAP.get(target_system, [])
            proposal_count_stmt = (
                select(func.count())
                .select_from(CrossSystemProposal)
                .where(
                    CrossSystemProposal.project_id == project_id,
                    CrossSystemProposal.target_domain.in_(domains) if domains else CrossSystemProposal.target_domain == "__none__",
                    CrossSystemProposal.status == "pending",
                )
            )
            proposal_result = await db.execute(proposal_count_stmt)
            pending_proposals_count = proposal_result.scalar() or 0

            total_active = pending_deliveries_count + active_conflicts_count
            consistency_score = max(0.0, 1.0 - (active_conflicts_count * 0.15)) if total_active > 0 else 1.0

            return {
                "pending_deliveries_count": pending_deliveries_count,
                "active_conflicts_count": active_conflicts_count,
                "pending_proposals_count": pending_proposals_count,
                "consistency_score": round(consistency_score, 2),
            }
        except Exception:
            logger.warning(
                "Failed to get status summary for %s/%s",
                project_id, target_system, exc_info=True,
            )
            return {
                "pending_deliveries_count": 0,
                "active_conflicts_count": 0,
                "pending_proposals_count": 0,
                "consistency_score": 1.0,
            }

    async def get_proposal_list(
        self,
        db: AsyncSession,
        project_id: str,
        filters: dict | None = None,
    ) -> dict[str, Any]:
        try:
            filters = filters or {}
            stmt = select(CrossSystemProposal).where(
                CrossSystemProposal.project_id == project_id,
            )

            if filters.get("status"):
                stmt = stmt.where(CrossSystemProposal.status == filters["status"])
            if filters.get("target_domain"):
                stmt = stmt.where(CrossSystemProposal.target_domain == filters["target_domain"])
            if filters.get("proposal_type"):
                stmt = stmt.where(CrossSystemProposal.proposal_type == filters["proposal_type"])

            stmt = stmt.order_by(CrossSystemProposal.created_at.desc())

            limit = filters.get("limit", 50)
            offset = filters.get("offset", 0)
            stmt = stmt.limit(limit).offset(offset)

            result = await db.execute(stmt)
            proposals = list(result.scalars().all())

            items = []
            for p in proposals:
                items.append({
                    "id": str(p.id),
                    "source_system": p.source_system,
                    "target_domain": p.target_domain,
                    "target_entity_type": p.target_entity_type,
                    "proposal_type": p.proposal_type,
                    "title": p.title,
                    "description": p.description,
                    "status": p.status,
                    "created_at": p.created_at.isoformat() if p.created_at else None,
                    "reviewed_by": getattr(p, "reviewed_by", None),
                    "reviewed_at": p.reviewed_at.isoformat() if getattr(p, "reviewed_at", None) else None,
                })

            return {
                "items": items,
                "total": len(items),
                "limit": limit,
                "offset": offset,
            }
        except Exception:
            logger.warning("Failed to get proposal list for %s", project_id, exc_info=True)
            return {"items": [], "total": 0, "limit": 50, "offset": 0}
