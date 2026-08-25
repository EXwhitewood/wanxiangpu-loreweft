"""Persistence and user decisions for post-save chapter diagnostics.

This service is intentionally outside the AI generation/FBI repair path.  It
stores findings created by an explicit user chapter save and applies only the
decision the user selected.
"""
from __future__ import annotations

import copy
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.db.db_models import Chapter, ChapterBaseline, ChapterDiagnosticRecord, Project
from app.services.user_chapter_settlement_service import load_user_save_reference
from app.models.writing_assistance import ChapterAdvisoryCheckResponse
from app.services.story_plan_service import StoryPlanService


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _actions_for(finding: dict[str, Any]) -> list[str]:
    actions = ["mark_fixed", "ignore"]
    if (
        str(finding.get("category") or "") == "outline_deviation"
        and str(finding.get("severity") or "low") == "high"
    ):
        actions.append("ignore_and_amend_outline")
    return actions


def _affected_domains(finding: dict[str, Any]) -> list[str]:
    category = str(finding.get("category") or "")
    mapping = {
        "fact": ["worldview", "story_state"],
        "character": ["entity_progression", "story_state"],
        "timeline": ["timeline", "story_state"],
        "world_rule": ["worldview"],
        "pov_knowledge": ["story_state"],
        "foreshadowing": ["foreshadowing"],
        "outline_deviation": ["outline", "task_progress"],
        "task_progress": ["task_progress", "outline"],
    }
    return mapping.get(category, ["quality"])


class ChapterDiagnosticService:
    async def compact_legacy_reference_snapshots(
        self,
        db: AsyncSession,
    ) -> dict[str, int]:
        """Deduplicate legacy snapshot copies only when a baseline is identical.

        The equality check is intentional: historical evidence that differs
        from today's shared baseline must remain self-contained rather than be
        compacted merely because the project/chapter numbers match.
        """
        records = list(
            (
                await db.execute(
                    select(ChapterDiagnosticRecord).where(
                        ChapterDiagnosticRecord.reference_baseline_id.is_(None)
                    )
                )
            ).scalars().all()
        )
        candidates = [record for record in records if record.reference_snapshot]
        if not candidates:
            return {"examined": 0, "compacted": 0, "preserved": 0}

        pairs = {(record.project_id, int(record.chapter_number)) for record in candidates}
        project_ids = {project_id for project_id, _ in pairs}
        baselines = list(
            (
                await db.execute(
                    select(ChapterBaseline).where(ChapterBaseline.project_id.in_(project_ids))
                )
            ).scalars().all()
        )
        by_pair = {
            (baseline.project_id, int(baseline.chapter_number)): baseline
            for baseline in baselines
        }
        compacted = 0
        for record in candidates:
            baseline = by_pair.get((record.project_id, int(record.chapter_number)))
            if baseline is None:
                continue
            if load_user_save_reference(baseline) != (record.reference_snapshot or {}):
                continue
            record.reference_baseline_id = baseline.id
            record.reference_snapshot = {}
            compacted += 1
        if compacted:
            await db.flush()
        return {
            "examined": len(candidates),
            "compacted": compacted,
            "preserved": len(candidates) - compacted,
        }

    @staticmethod
    def _is_placeholder(value: Any) -> bool:
        text = str(value or "").lower()
        return any(
            marker in text
            for marker in ("待细化", "待作者补充", "尚待作者补充", "待补充", "恢复占位", "placeholder")
        )

    async def _build_outline_amendment_proposal(
        self,
        db: AsyncSession,
        *,
        project: Project,
        chapter_number: int,
        record_id: str,
        finding: dict[str, Any],
    ) -> dict[str, Any]:
        outline = copy.deepcopy(project.outline_data or {})
        chapter = (
            await db.execute(
                select(Chapter).where(
                    Chapter.project_id == project.id,
                    Chapter.chapter_number == chapter_number,
                )
            )
        ).scalar_one_or_none()
        summaries = (project.core_data or {}).get("chapter_summaries", {}) if isinstance(project.core_data, dict) else {}
        summary = summaries.get(str(chapter_number), {}) if isinstance(summaries, dict) else {}
        summary = summary if isinstance(summary, dict) else {}
        existing: dict[str, Any] = {}
        chapter_id = f"ch_{chapter_number:03d}"
        for key in ("chapter_spine", "chapters"):
            for item in outline.get(key, []) if isinstance(outline.get(key), list) else []:
                if isinstance(item, dict) and int(item.get("chapter_number", 0) or 0) == chapter_number:
                    existing = copy.deepcopy(item)
                    chapter_id = str(item.get("chapter_id") or chapter_id)
                    break
            if existing:
                break

        core_event = str(summary.get("core_event") or summary.get("summary_text") or "").strip()
        turning_point = str(summary.get("key_turning_point") or "").strip()
        suspense = str(summary.get("unsolved_suspense") or "").strip()
        detail = str(finding.get("detail") or "").strip()
        evidence = str(finding.get("evidence_quote") or "").strip()
        fallback_event = core_event or evidence or f"第{chapter_number}章正文中的实际事件"
        fallback_turn = turning_point or detail or fallback_event
        fallback_hook = suspense or evidence or fallback_turn
        override = {
            "source": "user_chapter_save",
            "diagnostic_id": record_id,
            "reason": detail or str(finding.get("title") or "用户确认有意偏离大纲"),
            "evidence_quote": evidence,
            "accepted_at": _now().isoformat(),
        }
        chapter_patch: dict[str, Any] = {
            "chapter_number": chapter_number,
            "chapter_id": chapter_id,
            "title": (chapter.title if chapter else "") or existing.get("title") or f"第{chapter_number}章",
            "status": "written",
            "recovery_generated": False,
            "user_override": override,
            "core_event": fallback_event,
            "key_turning_point": fallback_turn,
            "hook": fallback_hook,
            "summary_text": str(summary.get("summary_text") or fallback_event),
        }
        chapter_patch["core_conflict"] = {
            "desire": fallback_event,
            "obstacle": detail or fallback_turn,
            "action": fallback_turn,
            "turn": fallback_hook,
        }
        chapter_patch["conflict_text"] = detail or fallback_turn
        chapter_patch["value_shift"] = {"axis": "剧情方向", "from": "原大纲", "to": fallback_turn}

        briefs = outline.get("scene_briefs") if isinstance(outline.get("scene_briefs"), dict) else {}
        existing_brief = copy.deepcopy(briefs.get(chapter_id) or {})
        source_scenes = existing_brief.get("scenes") if isinstance(existing_brief.get("scenes"), list) else existing.get("scenes")
        source_scenes = source_scenes if isinstance(source_scenes, list) else []
        scenes: list[dict[str, Any]] = []
        for index, raw_scene in enumerate(source_scenes or [{}]):
            scene = copy.deepcopy(raw_scene) if isinstance(raw_scene, dict) else {}
            scene.setdefault("scene_id", f"{chapter_id}_s{index + 1}")
            replacements = {
                "goal": fallback_event,
                "conflict": detail or fallback_turn,
                "outcome": fallback_turn,
                "result": fallback_turn,
                "info_release": str(summary.get("summary_text") or fallback_event),
                "hook": fallback_hook,
            }
            for field_name, replacement in replacements.items():
                if not str(scene.get(field_name) or "").strip() or self._is_placeholder(scene.get(field_name)):
                    scene[field_name] = replacement
            scenes.append(scene)
        chapter_patch["scenes"] = copy.deepcopy(scenes)
        brief_patch = {
            **existing_brief,
            "chapter_id": chapter_id,
            "source": "user_intentional_deviation",
            "version": int(existing_brief.get("version", 0) or 0) + 1,
            "scenes": copy.deepcopy(scenes),
        }
        patch: dict[str, Any] = {"chapter_spine": [copy.deepcopy(chapter_patch)]}
        if isinstance(outline.get("chapters"), list):
            patch["chapters"] = [copy.deepcopy(chapter_patch)]
        patch["scene_briefs"] = {chapter_id: brief_patch}

        preview: list[dict[str, Any]] = []
        for field_name in ("status", "core_conflict", "conflict_text", "value_shift", "hook"):
            before = existing.get(field_name)
            after = chapter_patch.get(field_name)
            if before != after:
                preview.append({"field": field_name, "before": before, "after": after})
        if source_scenes != scenes:
            preview.append({"field": "scene_briefs", "before": source_scenes, "after": scenes})
        return {
            "status": "proposed",
            "base_outline_version": int(project.outline_version or 0),
            "preview": preview,
            "patch": patch,
        }

    async def persist_response(
        self,
        db: AsyncSession,
        *,
        project_id: str | uuid.UUID,
        chapter_number: int,
        response: ChapterAdvisoryCheckResponse,
        reference_snapshot: dict[str, Any] | None = None,
    ) -> list[ChapterDiagnosticRecord]:
        """Persist one diagnostic response idempotently for a revision."""
        snapshot = copy.deepcopy(reference_snapshot or {})
        baseline = (
            await db.execute(
                select(ChapterBaseline).where(
                    ChapterBaseline.project_id == project_id,
                    ChapterBaseline.chapter_number == chapter_number,
                )
            )
        ).scalar_one_or_none()
        await db.execute(
            update(ChapterDiagnosticRecord)
            .where(
                ChapterDiagnosticRecord.project_id == project_id,
                ChapterDiagnosticRecord.chapter_number == chapter_number,
                ChapterDiagnosticRecord.chapter_revision_hash != response.revision_hash,
                ChapterDiagnosticRecord.status.in_(["open", "pending_recheck", "degraded"]),
            )
            .values(status="stale", updated_at=_now())
        )
        finding_payloads = [item.model_dump(mode="json") for item in response.findings]
        if response.status == "degraded" and not finding_payloads:
            message = "; ".join(item.message for item in response.diagnostics) or "章节诊断暂时不可用"
            finding_payloads.append(
                {
                    "finding_id": f"diagnostic_degraded_{response.revision_hash}",
                    "fingerprint": f"degraded:{response.revision_hash}",
                    "category": "quality",
                    "severity": "low",
                    "title": "诊断未完整完成",
                    "detail": message,
                    "evidence_quote": "",
                    "source": {"source_type": "diagnostic_system", "label": "保存后诊断", "excerpt": message},
                    "confidence": 1.0,
                }
            )
        if response.status == "complete":
            incoming_fingerprints = [
                str(item.get("fingerprint") or item.get("finding_id") or "")
                for item in finding_payloads
            ]
            stale_stmt = (
                update(ChapterDiagnosticRecord)
                .where(
                    ChapterDiagnosticRecord.project_id == project_id,
                    ChapterDiagnosticRecord.chapter_number == chapter_number,
                    ChapterDiagnosticRecord.chapter_revision_hash == response.revision_hash,
                    ChapterDiagnosticRecord.status.in_(["open", "pending_recheck", "degraded"]),
                )
            )
            if incoming_fingerprints:
                stale_stmt = stale_stmt.where(
                    ChapterDiagnosticRecord.fingerprint.not_in(incoming_fingerprints)
                )
            await db.execute(stale_stmt.values(status="stale", updated_at=_now()))
        records: list[ChapterDiagnosticRecord] = []
        for finding in finding_payloads:
            fingerprint = str(finding.get("fingerprint") or finding.get("finding_id") or "")
            if not fingerprint:
                continue
            result = await db.execute(
                select(ChapterDiagnosticRecord).where(
                    ChapterDiagnosticRecord.project_id == project_id,
                    ChapterDiagnosticRecord.chapter_number == chapter_number,
                    ChapterDiagnosticRecord.chapter_revision_hash == response.revision_hash,
                    ChapterDiagnosticRecord.fingerprint == fingerprint,
                )
            )
            record = result.scalar_one_or_none()
            if record is None:
                record = ChapterDiagnosticRecord(
                    project_id=project_id,
                    chapter_number=chapter_number,
                    chapter_revision_hash=response.revision_hash,
                    fingerprint=fingerprint,
                    status="open",
                )
                db.add(record)

            # A user decision is authoritative for the same revision.  A
            # repeated check must not silently reopen an ignored/resolved row.
            if record.status not in {
                "ignored",
                "resolved",
                "accepted_as_intentional_deviation",
                "outline_amendment_applied",
            }:
                record.status = "open" if response.status != "degraded" else "degraded"
            record.category = str(finding.get("category") or "state_consistency")
            record.severity = str(finding.get("severity") or "low")
            record.title = str(finding.get("title") or "")[:255]
            record.detail = str(finding.get("detail") or "")
            record.evidence_quote = str(finding.get("evidence_quote") or "")
            record.source = finding.get("source") or {}
            record.confidence = float(finding.get("confidence") or 0.0)
            if baseline is not None:
                record.reference_baseline_id = baseline.id
                record.reference_snapshot = {}
            else:
                record.reference_snapshot = snapshot
            record.affected_domains = _affected_domains(finding)
            record.available_actions = _actions_for(finding)
            if "ignore_and_amend_outline" in record.available_actions and record.status == "open":
                if record.id is None:
                    await db.flush()
                project = await db.get(Project, project_id)
                if project is not None:
                    proposal = await self._build_outline_amendment_proposal(
                        db,
                        project=project,
                        chapter_number=chapter_number,
                        record_id=str(record.id),
                        finding=finding,
                    )
                    record.decision_payload = {"proposed_outline_amendment": proposal}
            records.append(record)

        await db.flush()
        return records

    async def resolve_reference_snapshot(
        self,
        db: AsyncSession,
        record: ChapterDiagnosticRecord,
    ) -> dict[str, Any]:
        if record.reference_baseline_id:
            baseline = await db.get(ChapterBaseline, record.reference_baseline_id)
            if baseline is not None:
                return load_user_save_reference(baseline)
        return copy.deepcopy(record.reference_snapshot or {})

    async def list_records(
        self,
        db: AsyncSession,
        *,
        project_id: str | uuid.UUID,
        chapter_number: int,
        include_resolved: bool = False,
    ) -> list[ChapterDiagnosticRecord]:
        stmt = select(ChapterDiagnosticRecord).where(
            ChapterDiagnosticRecord.project_id == project_id,
            ChapterDiagnosticRecord.chapter_number == chapter_number,
        )
        if not include_resolved:
            stmt = stmt.where(
                ChapterDiagnosticRecord.status.not_in(
                    [
                        "resolved",
                        "ignored",
                        "accepted_as_intentional_deviation",
                        "stale",
                        "outline_amendment_applied",
                    ]
                )
            )
        stmt = stmt.order_by(
            ChapterDiagnosticRecord.created_at.desc(),
            ChapterDiagnosticRecord.severity.desc(),
        )
        return list((await db.execute(stmt)).scalars().all())

    async def get_record(
        self,
        db: AsyncSession,
        *,
        project_id: str | uuid.UUID,
        chapter_number: int,
        diagnostic_id: str | uuid.UUID,
    ) -> ChapterDiagnosticRecord | None:
        result = await db.execute(
            select(ChapterDiagnosticRecord).where(
                ChapterDiagnosticRecord.id == diagnostic_id,
                ChapterDiagnosticRecord.project_id == project_id,
                ChapterDiagnosticRecord.chapter_number == chapter_number,
            )
        )
        return result.scalar_one_or_none()

    async def apply_outline_amendment(
        self,
        db: AsyncSession,
        *,
        project: Project,
        chapter_number: int,
        record: ChapterDiagnosticRecord,
        outline_changes: dict[str, Any] | None = None,
        reason: str = "",
        decided_by: str = "user",
    ) -> str:
        """Apply an explicit user override and preserve an amendment record."""
        outline = copy.deepcopy(project.outline_data or {})
        changes = copy.deepcopy(outline_changes or {})
        if not changes:
            proposed = (record.decision_payload or {}).get("proposed_outline_amendment")
            if isinstance(proposed, dict) and isinstance(proposed.get("patch"), dict):
                changes = copy.deepcopy(proposed["patch"])
        if not changes:
            summaries = (
                (project.core_data or {}).get("chapter_summaries", {})
                if isinstance(project.core_data, dict)
                else {}
            )
            summary = summaries.get(str(chapter_number), {}) if isinstance(summaries, dict) else {}
            actual = {
                "chapter_number": chapter_number,
                "status": "written",
                "user_override": {
                    "source": "user_chapter_save",
                    "diagnostic_id": str(record.id),
                    "reason": reason or record.detail or record.title,
                    "evidence_quote": record.evidence_quote,
                    "accepted_at": _now().isoformat(),
                },
            }
            field_map = {
                "core_event": "core_event",
                "key_turning_point": "key_turning_point",
                "unsolved_suspense": "hook",
                "summary_text": "summary_text",
                "character_changes": "character_changes",
            }
            if isinstance(summary, dict):
                for source_key, target_key in field_map.items():
                    value = summary.get(source_key)
                    if value not in (None, "", [], {}):
                        actual[target_key] = copy.deepcopy(value)
            changes = {}
            for key in ("chapter_spine", "chapters"):
                if isinstance(outline.get(key), list):
                    changes[key] = [copy.deepcopy(actual)]
            if not changes:
                changes["chapter_spine"] = [copy.deepcopy(actual)]
        if changes:
            self._merge_outline_patch(outline, changes)

        chapter_record = None
        for item in outline.get("chapter_spine", []) if isinstance(outline.get("chapter_spine"), list) else []:
            if isinstance(item, dict) and int(item.get("chapter_number", 0) or 0) == chapter_number:
                chapter_record = item
                break
        if chapter_record is None:
            raise ValueError("大纲修订后找不到目标章节")
        chapter_id = str(chapter_record.get("chapter_id") or f"ch_{chapter_number:03d}")
        briefs = outline.get("scene_briefs") if isinstance(outline.get("scene_briefs"), dict) else {}
        brief = briefs.get(chapter_id) if isinstance(briefs.get(chapter_id), dict) else {}
        scenes = brief.get("scenes") if isinstance(brief.get("scenes"), list) else chapter_record.get("scenes", [])
        blocking = [
            item
            for item in StoryPlanService().validate_chapter_blueprint(
                chapter_record,
                scenes if isinstance(scenes, list) else [],
            )
            if item.get("blocks_generation")
        ]
        if blocking:
            raise ValueError(
                "大纲修订未通过蓝图校验："
                + "；".join(str(item.get("message") or "蓝图不完整") for item in blocking)
            )

        amendment_id = str(uuid.uuid4())
        amendments = outline.get("_amendments")
        if not isinstance(amendments, list):
            amendments = []
        amendments.append(
            {
                "id": amendment_id,
                "status": "applied",
                "source": "user_chapter_save",
                "chapter_number": chapter_number,
                "diagnostic_id": str(record.id),
                "description": reason or record.detail or record.title,
                "evidence_quote": record.evidence_quote,
                "changes": changes,
                "created_by": decided_by,
                "created_at": _now().isoformat(),
            }
        )
        outline["_amendments"] = amendments
        outline["_version"] = int(outline.get("_version", 0) or 0) + 1
        outline["_frozen"] = False
        project.outline_data = outline
        project.outline_version = int(project.outline_version or 0) + 1
        flag_modified(project, "outline_data")
        await db.flush()
        return amendment_id

    @staticmethod
    def _merge_outline_patch(outline: dict[str, Any], patch: dict[str, Any]) -> None:
        """Merge an editor patch while preserving chapter-list semantics."""
        for key, value in patch.items():
            if key in {"chapter_spine", "chapters"} and isinstance(value, list):
                target = outline.setdefault(key, [])
                by_number = {
                    int(item.get("chapter_number")): item
                    for item in target
                    if isinstance(item, dict) and item.get("chapter_number") is not None
                }
                for item in value:
                    if not isinstance(item, dict) or item.get("chapter_number") is None:
                        target.append(copy.deepcopy(item))
                        continue
                    number = int(item["chapter_number"])
                    if number in by_number:
                        by_number[number].update(copy.deepcopy(item))
                    else:
                        target.append(copy.deepcopy(item))
                continue
            if isinstance(value, dict) and isinstance(outline.get(key), dict):
                outline[key].update(copy.deepcopy(value))
            else:
                outline[key] = copy.deepcopy(value)

    @staticmethod
    def serialize(record: ChapterDiagnosticRecord) -> dict[str, Any]:
        return {
            "diagnostic_id": str(record.id),
            "project_id": str(record.project_id),
            "chapter_number": record.chapter_number,
            "chapter_revision_hash": record.chapter_revision_hash,
            "fingerprint": record.fingerprint,
            "category": record.category,
            "severity": record.severity,
            "title": record.title,
            "detail": record.detail,
            "evidence_quote": record.evidence_quote,
            "source": record.source or {},
            "confidence": record.confidence,
            "reference_available": bool(record.reference_baseline_id or record.reference_snapshot),
            "affected_domains": record.affected_domains or [],
            "available_actions": record.available_actions or [],
            "status": record.status,
            "decision_by": record.decision_by,
            "decision_reason": record.decision_reason,
            "decision_payload": record.decision_payload or {},
            "created_at": record.created_at,
            "updated_at": record.updated_at,
            "decided_at": record.decided_at,
        }


def get_chapter_diagnostic_service() -> ChapterDiagnosticService:
    return ChapterDiagnosticService()
