import uuid
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, update, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.db_models import CrossSystemProposal
from app.services.cross_system_event_bus import CrossSystemEventBus

logger = logging.getLogger(__name__)

DOMAIN_SERVICE_MAP = {
    "world_rule": {
        "service_module": "app.services.memory_core",
        "service_class": "CoreMemoryService",
        "methods": {
            "new": "create_world_rule_with_db",
            "modify": "update_world_rule_with_db",
            "delete": "delete_world_rule_with_db",
            "supplement": "update_world_rule_with_db",
        },
    },
    "character": {
        "service_module": "app.services.memory_core",
        "service_class": "CoreMemoryService",
        "methods": {
            "new": "create_character_with_db",
            "modify": "update_character_with_db",
            "delete": "delete_character_with_db",
            "supplement": "update_character_with_db",
        },
    },
    "location": {
        "service_module": "app.services.memory_core",
        "service_class": "CoreMemoryService",
        "methods": {
            "new": "add_location_with_db",
            "modify": "update_location_with_db",
            "delete": "delete_location_with_db",
            "supplement": "update_location_with_db",
        },
    },
    "foreshadowing": {
        "service_module": "app.services.foreshadowing_service",
        "service_class": "ForeshadowingService",
        "methods": {
            "new": "create_foreshadowing_line",
            "modify": "update_foreshadowing_line",
            "delete": "delete_foreshadowing_line",
            "supplement": "update_foreshadowing_line",
        },
    },
    "outline_chapter": {
        "service_module": "app.services.chapter_spine_service",
        "service_class": "ChapterSpineService",
        "methods": {
            "new": "add_chapter",
            "modify": "update_chapter",
            "supplement": "update_chapter",
        },
    },
    "thread_plan": {
        "service_module": "app.services.thread_plan_service",
        "service_class": "ThreadPlanService",
        "methods": {
            "new": "add_thread",
            "modify": "update_thread",
            "delete": "delete_thread",
            "supplement": "update_thread",
        },
    },
    "worldview": {
        "service_module": "app.services.memory_core",
        "service_class": "CoreMemoryService",
        "methods": {
            "new": "create_world_rule_with_db",
            "modify": "update_world_rule_with_db",
            "delete": "delete_world_rule_with_db",
            "supplement": "update_world_rule_with_db",
        },
    },
}

SUPPLEMENT_WHITELIST = {
    "world_rule": ["notes", "description_addendum", "examples"],
    "character": ["notes", "description_addendum", "relationship_notes"],
    "location": ["notes", "description_addendum", "scene_notes"],
    "foreshadowing": ["notes", "reveal_notes", "evidence_notes"],
    "outline_chapter": ["notes", "scene_notes", "author_intent_notes", "chapter_number", "title", "core_conflict", "conflict_text", "value_shift", "pov_character", "hook", "thread_ops", "status"],
    "thread_plan": ["notes", "thread_notes"],
    "worldview": ["notes", "description_addendum"],
}

AUTO_APPROVE_DOMAIN_TYPES = {
    ("outline_chapter", "new"),
    ("outline_chapter", "modify"),
    ("outline_chapter", "supplement"),
    ("thread_plan", "new"),
    ("thread_plan", "modify"),
    ("thread_plan", "supplement"),
    ("foreshadowing", "new"),
    ("foreshadowing", "modify"),
}
AUTO_REJECT_PROPOSAL_TYPES: set[str] = set()

EXECUTING_TTL_SECONDS = 600


def _matches_relevant_chapter(proposal: CrossSystemProposal, relevant_chapter: int) -> bool:
    affected = getattr(proposal, "affected_chapters", None) or []
    if not affected:
        return True
    try:
        return int(relevant_chapter) in {int(chapter) for chapter in affected}
    except (TypeError, ValueError):
        return False


class CrossSystemProposalService:
    def __init__(self) -> None:
        self._event_bus = CrossSystemEventBus()

    async def create_proposal(
        self,
        db: AsyncSession,
        project_id: str,
        source_system: str,
        target_domain: str,
        target_entity_type: str,
        proposal_type: str,
        title: str,
        description: str = "",
        proposal_data: dict | None = None,
        base_version: int | None = None,
    ) -> CrossSystemProposal | None:
        from sqlalchemy.exc import IntegrityError

        try:
            import json
            data_hash = hashlib.sha256(
                json.dumps(proposal_data or {}, sort_keys=True, ensure_ascii=False).encode()
            ).hexdigest()[:16]
            raw = f"{project_id}{source_system}{target_domain}{target_entity_type}{proposal_type}{title}{data_hash}"
            if base_version is not None:
                raw += f"v{base_version}"
            idempotency_key = hashlib.sha256(raw.encode()).hexdigest()

            existing_stmt = (
                select(CrossSystemProposal)
                .where(
                    CrossSystemProposal.project_id == project_id,
                    CrossSystemProposal.idempotency_key == idempotency_key,
                    CrossSystemProposal.status.in_(["pending", "approved", "executing", "executed"]),
                )
            )
            existing_result = await db.execute(existing_stmt)
            existing = existing_result.scalar_one_or_none()
            if existing:
                return existing

            if base_version is not None:
                version_stmt = (
                    select(CrossSystemProposal)
                    .where(
                        CrossSystemProposal.project_id == project_id,
                        CrossSystemProposal.target_domain == target_domain,
                        CrossSystemProposal.target_entity_type == target_entity_type,
                    )
                    .order_by(CrossSystemProposal.created_at.desc())
                    .limit(1)
                )
                version_result = await db.execute(version_stmt)
                latest = version_result.scalar_one_or_none()
                if latest and hasattr(latest, "base_version") and latest.base_version is not None:
                    try:
                        if int(latest.base_version) > int(base_version):
                            logger.warning(
                                "Stale base_version %d for proposal, latest is %d",
                                int(base_version), int(latest.base_version),
                            )
                    except (ValueError, TypeError):
                        pass

            auto_action = self._determine_auto_action(proposal_type, target_domain)

            initial_status = auto_action if auto_action else "pending"

            proposal = CrossSystemProposal(
                project_id=project_id,
                source_system=source_system,
                target_domain=target_domain,
                target_entity_type=target_entity_type,
                proposal_type=proposal_type,
                title=title,
                description=description,
                proposal_data=proposal_data or {},
                idempotency_key=idempotency_key,
                base_version=str(base_version) if base_version is not None else None,
                status=initial_status,
            )
            db.add(proposal)

            nested = await db.begin_nested()
            try:
                await db.flush()
            except IntegrityError:
                await nested.rollback()
                existing_result = await db.execute(existing_stmt)
                existing = existing_result.scalar_one_or_none()
                if existing:
                    return existing
                raise

            if auto_action == "approved":
                proposal.reviewed_by = "auto"
                proposal.reviewed_at = datetime.now(timezone.utc)
                proposal.review_notes = "Auto-approved"

            try:
                await self._event_bus.publish_event(
                    db, project_id, "PROPOSAL_CREATED.v1", source_system,
                    priority="normal",
                    payload={
                        "proposal_id": str(proposal.id),
                        "proposal_type": proposal_type,
                        "target_domain": target_domain,
                        "auto_action": auto_action,
                    },
                )
                await nested.commit()
            except Exception:
                await nested.rollback()
                logger.warning(
                    "Failed to publish PROPOSAL_CREATED event, rolling back proposal for project %s",
                    project_id, exc_info=True,
                )
                return None

            if auto_action == "approved":
                try:
                    exec_result = await self.execute_proposal(db, str(proposal.id))
                    if exec_result.get("status") == "executed":
                        logger.info(
                            "Auto-executed proposal %s for project %s",
                            proposal.id, project_id,
                        )
                    else:
                        logger.warning(
                            "Auto-approve succeeded but auto-execute failed for proposal %s: %s",
                            proposal.id, exec_result,
                        )
                except Exception:
                    logger.warning(
                        "Auto-execute exception for proposal %s",
                        proposal.id, exc_info=True,
                    )

            return proposal
        except Exception:
            logger.warning("Failed to create proposal for project %s", project_id, exc_info=True)
            return None

    async def get_pending_for_system(
        self,
        db: AsyncSession,
        project_id: str,
        target_system: str,
        relevant_chapter: int | None = None,
    ) -> list[CrossSystemProposal]:
        try:
            stmt = (
                select(CrossSystemProposal)
                .where(
                    CrossSystemProposal.project_id == project_id,
                    CrossSystemProposal.target_domain == target_system,
                    CrossSystemProposal.status == "pending",
                )
                .order_by(CrossSystemProposal.created_at.asc())
            )
            result = await db.execute(stmt)
            proposals = list(result.scalars().all())
            if relevant_chapter is not None:
                proposals = [
                    proposal for proposal in proposals
                    if _matches_relevant_chapter(proposal, relevant_chapter)
                ]
            return proposals
        except Exception:
            logger.warning("Failed to get pending proposals for %s/%s", project_id, target_system, exc_info=True)
            return []

    async def review_proposal(
        self,
        db: AsyncSession,
        proposal_id: str,
        action: str,
        reviewed_by: str,
        review_notes: str = "",
        modified_data: dict | None = None,
    ) -> CrossSystemProposal | None:
        try:
            stmt = select(CrossSystemProposal).where(CrossSystemProposal.id == proposal_id)
            result = await db.execute(stmt)
            proposal = result.scalar_one_or_none()
            if not proposal:
                return None
            if proposal.status != "pending":
                return None

            if action == "approve":
                proposal.status = "approved"
            elif action == "reject":
                proposal.status = "rejected"
            else:
                return None

            proposal.reviewed_by = reviewed_by
            proposal.reviewed_at = datetime.now(timezone.utc)
            proposal.review_notes = review_notes
            if modified_data:
                proposal.proposal_data = {**(proposal.proposal_data or {}), **modified_data}

            await db.flush()
            return proposal
        except Exception:
            logger.warning("Failed to review proposal %s", proposal_id, exc_info=True)
            return None

    async def execute_proposal(self, db: AsyncSession, proposal_id: str) -> dict[str, Any]:
        stmt = select(CrossSystemProposal).where(CrossSystemProposal.id == proposal_id)
        result = await db.execute(stmt)
        proposal = result.scalar_one_or_none()
        if not proposal:
            return {"status": "not_found"}
        if proposal.status != "approved":
            return {"status": "invalid_state", "current": proposal.status}

        proposal.status = "executing"
        proposal.executing_until = datetime.now(timezone.utc) + timedelta(seconds=EXECUTING_TTL_SECONDS)
        proposal.heartbeat_at = datetime.now(timezone.utc)
        await db.flush()

        domain_info = DOMAIN_SERVICE_MAP.get(proposal.target_domain)
        if not domain_info:
            proposal.status = "failed"
            proposal.review_notes = f"Unknown target domain: {proposal.target_domain}"
            await db.flush()
            return {"status": "failed", "reason": proposal.review_notes}

        method_name = domain_info["methods"].get(proposal.proposal_type)
        if not method_name:
            proposal.status = "failed"
            proposal.review_notes = f"No handler for {proposal.proposal_type} in {proposal.target_domain}"
            await db.flush()
            return {"status": "failed", "reason": proposal.review_notes}

        import importlib
        try:
            module = importlib.import_module(domain_info["service_module"])
            service_class = getattr(module, domain_info["service_class"])
            service = service_class()
            method = getattr(service, method_name)

            pd = proposal.proposal_data or {}
            if proposal.proposal_type == "supplement":
                whitelist = SUPPLEMENT_WHITELIST.get(proposal.target_domain, [])
                if whitelist:
                    pd = {k: v for k, v in pd.items() if k in whitelist}
            if proposal.target_domain == "world_rule":
                if proposal.proposal_type == "new":
                    result = await method(db, str(proposal.project_id), pd)
                elif proposal.proposal_type in ("modify", "supplement"):
                    result = await method(db, str(proposal.project_id), pd.get("rule_id", ""), pd)
                elif proposal.proposal_type == "delete":
                    result = await method(db, str(proposal.project_id), pd.get("rule_id", ""))
            elif proposal.target_domain == "character":
                if proposal.proposal_type == "new":
                    from app.models.character import CharacterCreate
                    char_data = CharacterCreate(**pd) if isinstance(pd, dict) else pd
                    result = await method(db, str(proposal.project_id), char_data)
                elif proposal.proposal_type in ("modify", "supplement"):
                    result = await method(db, str(proposal.project_id), pd.get("character_id", ""), pd)
                elif proposal.proposal_type == "delete":
                    result = await method(db, str(proposal.project_id), pd.get("character_id", ""))
            elif proposal.target_domain == "location":
                if proposal.proposal_type == "new":
                    result = await method(db, str(proposal.project_id), pd)
                elif proposal.proposal_type in ("modify", "supplement"):
                    result = await method(db, str(proposal.project_id), pd.get("location_id", ""), pd)
                elif proposal.proposal_type == "delete":
                    result = await method(db, str(proposal.project_id), pd.get("location_id", ""))
            elif proposal.target_domain == "foreshadowing":
                if proposal.proposal_type == "new":
                    result = await method(str(proposal.project_id), pd, db)
                else:
                    result = await method(str(proposal.project_id), pd.get("foreshadowing_id", ""), pd, db)
            elif proposal.target_domain == "outline_chapter":
                chapter_num = pd.get("chapter_number")
                if chapter_num is not None:
                    chapter_num = int(chapter_num)
                if chapter_num is None and proposal.proposal_type != "new":
                    proposal.status = "failed"
                    proposal.review_notes = "Missing chapter_number in proposal_data"
                    await db.flush()
                    return {"status": "failed", "reason": "Missing chapter_number"}
                if proposal.proposal_type == "new":
                    result = await method(str(proposal.project_id), pd, db)
                else:
                    result = await method(str(proposal.project_id), chapter_num, pd, db)
            elif proposal.target_domain == "thread_plan":
                if proposal.proposal_type == "new":
                    result = await method(str(proposal.project_id), pd, db)
                elif proposal.proposal_type in ("modify", "supplement"):
                    result = await method(str(proposal.project_id), pd.get("thread_id", ""), pd, db)
                elif proposal.proposal_type == "delete":
                    result = await method(str(proposal.project_id), pd.get("thread_id", ""), db)
            elif proposal.target_domain == "worldview":
                if proposal.proposal_type == "new":
                    result = await method(db, str(proposal.project_id), pd)
                elif proposal.proposal_type in ("modify", "supplement"):
                    result = await method(db, str(proposal.project_id), pd.get("rule_id", ""), pd)
                elif proposal.proposal_type == "delete":
                    result = await method(db, str(proposal.project_id), pd.get("rule_id", ""))
            else:
                result = None

            if result is None or result is False:
                proposal.status = "failed"
                proposal.review_notes = f"Domain method {method_name} returned {result!r}"
                await db.flush()
                return {"status": "failed", "reason": proposal.review_notes}

            if isinstance(result, dict) and result.get("error"):
                proposal.status = "failed"
                proposal.review_notes = f"Domain error: {result['error']}"
                await db.flush()
                return {"status": "failed", "reason": proposal.review_notes}

            proposal.status = "executed"
            proposal.executed_by_service = f"{domain_info['service_class']}.{method_name}"
            proposal.executed_at = datetime.now(timezone.utc)
            await db.flush()

            await self._event_bus.publish_event(
                db, str(proposal.project_id), "PROPOSAL_RESOLVED.v1",
                proposal.source_system,
                priority="normal",
                payload={
                    "proposal_id": str(proposal.id),
                    "proposal_type": proposal.proposal_type,
                    "target_domain": proposal.target_domain,
                    "outcome": "executed",
                    "executed_by_service": proposal.executed_by_service,
                },
            )
            return {"status": "executed", "proposal_id": str(proposal.id), "result": str(result) if result else None}
        except Exception as exc:
            proposal.status = "failed"
            proposal.review_notes = f"Execution error: {exc!r}"
            try:
                await db.flush()
            except Exception:
                logger.warning("Failed to flush failed status for proposal %s", proposal_id, exc_info=True)
            return {"status": "failed", "reason": proposal.review_notes}

    async def reap_stale_executing_proposals(self, db: AsyncSession) -> int:
        try:
            now = datetime.now(timezone.utc)
            stmt = (
                select(CrossSystemProposal)
                .where(
                    CrossSystemProposal.status == "executing",
                    CrossSystemProposal.executing_until < now,
                )
            )
            result = await db.execute(stmt)
            stale_proposals = list(result.scalars().all())

            count = 0
            for proposal in stale_proposals:
                proposal.status = "stale"
                proposal.stale_detected_at = now
                count += 1

                try:
                    await self._event_bus.publish_event(
                        db, str(proposal.project_id), "PROPOSAL_STALE.v1",
                        proposal.source_system,
                        priority="high",
                        payload={
                            "proposal_id": str(proposal.id),
                            "proposal_type": proposal.proposal_type,
                            "target_domain": proposal.target_domain,
                        },
                    )
                except Exception:
                    logger.warning("Failed to publish stale event for proposal %s", proposal.id, exc_info=True)

            if count > 0:
                await db.flush()
            return count
        except Exception:
            logger.warning("Failed to reap stale proposals", exc_info=True)
            return 0

    def _determine_auto_action(self, proposal_type: str, target_domain: str) -> str | None:
        if (target_domain, proposal_type) in AUTO_APPROVE_DOMAIN_TYPES:
            return "approved"
        if proposal_type in AUTO_REJECT_PROPOSAL_TYPES:
            return "rejected"
        return None
