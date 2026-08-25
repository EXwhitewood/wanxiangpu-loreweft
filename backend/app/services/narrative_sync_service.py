import logging
import copy
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.db_models import Project
from app.services.chapter_spine_service import ChapterSpineService
from app.services.cross_system_event_bus import CrossSystemEventBus
from app.services.foreshadowing_upsert_service import (
    ForeshadowingUpsertService,
    normalize_foreshadowing_name,
    normalize_foreshadowing_operation,
)
from app.services.foreshadowing_action_sanitizer import sanitize_foreshadowing_action
from app.services.scene_brief_service import SceneBriefService
from app.services.story_plan_service import StoryPlanService
from app.utils.word_count import count_words

logger = logging.getLogger(__name__)


class NarrativeSyncService:
    def __init__(self) -> None:
        self._event_bus = CrossSystemEventBus()
        self._plan_service = StoryPlanService()
        self._chapter_spine_service = ChapterSpineService()
        self._scene_brief_service = SceneBriefService()
        self._foreshadowing_upsert = ForeshadowingUpsertService()

    async def sync_chapter_write(
        self,
        project_id: str | uuid.UUID,
        chapter_number: int,
        db: AsyncSession,
        *,
        title: str = "",
        content: str = "",
        summary: dict | None = None,
        chapter_state: dict | None = None,
        scene_packages: list[dict] | None = None,
        source_system: str = "editor",
    ) -> dict[str, Any]:
        project = await self._get_project(project_id, db)
        if not project:
            return {"error": "project_not_found"}

        outline = copy.deepcopy(project.outline_data or {})
        chapter_entry = self._extract_chapter_entry(outline, chapter_number)
        summary = summary or {}
        actions = self._collect_foreshadowing_actions(
            chapter_number=chapter_number,
            chapter_entry=chapter_entry,
            summary=summary,
            scene_packages=scene_packages or [],
        )

        chapter_updates = self._build_chapter_updates(
            chapter_number=chapter_number,
            title=title,
            content=content,
            summary=summary,
            chapter_state=chapter_state or {},
            actions=actions,
        )
        await self._merge_chapter_into_outline(project, chapter_number, chapter_updates, db)
        await self._merge_chapter_summary(
            project,
            chapter_number,
            summary,
            db,
        )

        await self._append_change_notification(
            project,
            db,
            {
                "type": "chapter_sync",
                "chapter_number": chapter_number,
                "title": title,
                "word_count": count_words(content or ""),
                "foreshadowing_count": len(actions),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

        await db.flush()

        foreshadowing_result = await self._foreshadowing_upsert.upsert_from_outline(
            db=db,
            project_id=project_id,
            chapter_number=chapter_number,
            actions=actions,
            title=title,
            source_system=source_system,
        )

        await self._event_bus.publish_event(
            db=db,
            project_id=str(project_id),
            event_type="CHAPTER_SYNCED.v1",
            source_system=source_system,
            priority="normal",
            payload={
                "entity_type": "chapter",
                "entity_id": str(chapter_number),
                "entity_name": title or f"Chapter {chapter_number}",
                "change_type": "synced",
                "summary": f"Chapter {chapter_number} synced",
                "meta": {
                    "chapter_number": chapter_number,
                    "word_count": count_words(content or ""),
                    "foreshadowing_count": len(actions),
                },
            },
        )

        if actions:
            await self._event_bus.publish_event(
                db=db,
                project_id=str(project_id),
                event_type="FORESHADOWING_WINDOW_CHANGED.v1",
                source_system=source_system,
                priority="normal",
                payload={
                    "entity_type": "foreshadowing",
                    "entity_id": str(chapter_number),
                    "entity_name": title or f"Chapter {chapter_number}",
                    "change_type": "updated",
                    "summary": f"Chapter {chapter_number} updated {len(actions)} foreshadowing lines",
                    "meta": {
                        "chapter_number": chapter_number,
                        "foreshadowing_names": [a["name"] for a in actions],
                    },
                },
            )

        await db.commit()
        return {
            "chapter_number": chapter_number,
            "updated": True,
            "foreshadowing_count": len(actions),
            "foreshadowing_updates": foreshadowing_result,
        }

    async def sync_chapter_delete(
        self,
        project_id: str | uuid.UUID,
        chapter_number: int,
        db: AsyncSession,
        *,
        source_system: str = "editor",
        retract_worldview: bool = True,
        commit: bool = True,
    ) -> dict[str, Any]:
        project = await self._get_project(project_id, db)
        if not project:
            return {"error": "project_not_found"}

        outline = copy.deepcopy(project.outline_data or {})
        removed_brief = await self._scene_brief_service.discard_brief(uuid.UUID(str(project_id)), chapter_number, db)
        removed_foreshadowing = 0

        core_data = copy.deepcopy(project.core_data or {})
        summaries = core_data.get("chapter_summaries", {})
        if isinstance(summaries, dict) and str(chapter_number) in summaries:
            summaries = dict(summaries)
            summaries.pop(str(chapter_number), None)
            if summaries:
                core_data["chapter_summaries"] = summaries
            else:
                core_data.pop("chapter_summaries", None)
        project.core_data = core_data

        await self._append_change_notification(
            project,
            db,
            {
                "type": "chapter_delete",
                "chapter_number": chapter_number,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

        if commit:
            await db.commit()
        else:
            await db.flush()

        removed_foreshadowing = await self._orphan_foreshadowing_lines(project_id, chapter_number, db)

        if retract_worldview:
            try:
                from app.services.worldview_projection_service import WorldviewProjectionService
                wps = WorldviewProjectionService()
                await wps.retract_chapter_sources(db, project_id, chapter_number)
            except Exception as e:
                logger.warning("[NarrativeSyncService] worldview retraction failed: %s", e)
                raise

        await self._event_bus.publish_event(
            db=db,
            project_id=str(project_id),
            event_type="CHAPTER_DELETED.v1",
            source_system=source_system,
            priority="high",
            payload={
                "entity_type": "chapter",
                "entity_id": str(chapter_number),
                "entity_name": f"Chapter {chapter_number}",
                "change_type": "deleted",
                "summary": f"Chapter {chapter_number} deleted",
                "meta": {
                    "chapter_number": chapter_number,
                    "outline_preserved": True,
                    "removed_scene_brief": bool(removed_brief),
                    "orphaned_foreshadowing_count": removed_foreshadowing,
                },
            },
        )

        await self._event_bus.publish_event(
            db=db,
            project_id=str(project_id),
            event_type="CONSISTENCY_ALERT.v1",
            source_system=source_system,
            priority="high",
            payload={
                "entity_type": "chapter",
                "entity_id": str(chapter_number),
                "entity_name": f"Chapter {chapter_number}",
                "change_type": "deleted",
                "summary": f"Chapter {chapter_number} deleted; outline preserved",
                "meta": {
                    "chapter_number": chapter_number,
                    "outline_preserved": True,
                    "orphaned_foreshadowing_count": removed_foreshadowing,
                },
            },
        )

        if commit:
            await db.commit()
        else:
            await db.flush()
        return {
            "chapter_number": chapter_number,
            "outline_preserved": True,
            "removed_scene_brief": bool(removed_brief),
            "orphaned_foreshadowing_count": removed_foreshadowing,
        }

    async def sync_outline_save(
        self,
        project_id: str | uuid.UUID,
        db: AsyncSession,
        *,
        old_outline: dict | None = None,
        new_outline: dict | None = None,
        source_system: str = "outline",
        publish_event: bool = True,
    ) -> dict[str, Any]:
        project = await self._get_project(project_id, db)
        if not project:
            return {"error": "project_not_found"}

        old_outline = old_outline or {}
        new_outline = new_outline or (project.outline_data or {})
        changed_chapters = self._changed_chapters(old_outline, new_outline)

        await self._append_change_notification(
            project,
            db,
            {
                "type": "outline_save",
                "changed_chapters": changed_chapters,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

        if publish_event:
            await self._event_bus.publish_event(
                db=db,
                project_id=str(project_id),
                event_type="OUTLINE_SAVED.v1",
                source_system=source_system,
                priority="normal",
                payload={
                    "entity_type": "outline",
                    "entity_id": str(project_id),
                    "entity_name": project.name or "",
                    "change_type": "saved",
                    "summary": f"大纲已保存，影响章节: {changed_chapters[:10]}",
                    "meta": {
                        "changed_chapters": changed_chapters,
                        "chapter_count": len(new_outline.get("chapter_spine") or new_outline.get("chapters") or []),
                    },
                },
            )

        if changed_chapters:
            for chapter_number in changed_chapters[:10]:
                try:
                    await self._scene_brief_service.invalidate_briefs(
                        uuid.UUID(str(project_id)),
                        "chapter_spine_changed",
                        chapter_number,
                        db,
                    )
                except Exception:
                    logger.warning(
                        "Failed to invalidate briefs for chapter %s in project %s",
                        chapter_number,
                        project_id,
                        exc_info=True,
                    )

        await db.commit()
        return {
            "changed_chapters": changed_chapters,
            "chapter_count": len(new_outline.get("chapter_spine") or new_outline.get("chapters") or []),
        }

    async def sync_outline_delete(
        self,
        project_id: str | uuid.UUID,
        db: AsyncSession,
        *,
        old_outline: dict | None = None,
        source_system: str = "outline",
        publish_event: bool = True,
    ) -> dict[str, Any]:
        project = await self._get_project(project_id, db)
        if not project:
            return {"error": "project_not_found"}

        outline = old_outline or (project.outline_data or {})
        chapters = self._outline_chapter_numbers(outline)

        await self._append_change_notification(
            project,
            db,
            {
                "type": "outline_delete",
                "chapter_numbers": chapters,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

        if publish_event:
            await self._event_bus.publish_event(
                db=db,
                project_id=str(project_id),
                event_type="OUTLINE_DELETED.v1",
                source_system=source_system,
                priority="high",
                payload={
                    "entity_type": "outline",
                    "entity_id": str(project_id),
                    "entity_name": project.name or "",
                    "change_type": "deleted",
                    "summary": "大纲已删除，相关章节需重新建立映射",
                    "meta": {
                        "chapter_numbers": chapters,
                        "chapter_count": len(chapters),
                    },
                },
            )

            await self._event_bus.publish_event(
                db=db,
                project_id=str(project_id),
                event_type="CONSISTENCY_ALERT.v1",
                source_system=source_system,
                priority="high",
                payload={
                    "entity_type": "outline",
                    "entity_id": str(project_id),
                    "entity_name": project.name or "",
                    "change_type": "deleted",
                    "summary": "Outline cleared; chapter, worldview, and foreshadowing panels need refresh",
                    "meta": {
                        "chapter_numbers": chapters,
                    },
                },
            )

        await db.commit()
        return {"chapter_numbers": chapters, "deleted": True}

    async def _orphan_foreshadowing_lines(
        self,
        project_id: str | uuid.UUID,
        chapter_number: int,
        db: AsyncSession,
    ) -> int:
        return await self._foreshadowing_upsert.retract_chapter_evidence(
            db,
            project_id,
            chapter_number,
            source="narrative_sync_delete",
        )

    def _build_chapter_updates(
        self,
        *,
        chapter_number: int,
        title: str,
        content: str,
        summary: dict,
        chapter_state: dict,
        actions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        updates = {
            "chapter_number": chapter_number,
            "title": title or f"Chapter {chapter_number}",
            "status": "written",
            "word_count": count_words(content or ""),
            "last_written_at": datetime.now(timezone.utc).isoformat(),
            "chapter_state": chapter_state or {},
        }
        if summary:
            for key in ("core_event", "key_turning_point", "unsolved_suspense", "summary_text"):
                if summary.get(key):
                    updates[key] = summary.get(key)
            if summary.get("character_changes"):
                updates["character_changes"] = summary.get("character_changes")
            if summary.get("completed_events"):
                updates["completed_events"] = summary.get("completed_events")
            if summary.get("active_constraints"):
                updates["active_constraints"] = summary.get("active_constraints")
            if summary.get("scene_ending"):
                updates["scene_ending"] = summary.get("scene_ending")
        return updates

    async def _merge_chapter_into_outline(
        self,
        project: Project,
        chapter_number: int,
        updates: dict[str, Any],
        db: AsyncSession,
    ) -> None:
        outline = copy.deepcopy(project.outline_data or {})
        changed = False
        for key in ("chapter_spine", "chapters"):
            chapters = outline.get(key)
            if not isinstance(chapters, list):
                continue
            new_chapters = []
            found = False
            for item in chapters:
                next_item = dict(item) if isinstance(item, dict) else item
                if isinstance(next_item, dict) and next_item.get("chapter_number") == chapter_number:
                    previous_item = copy.deepcopy(next_item)
                    next_item.update(updates)
                    next_item.pop("foreshadowing_actions", None)
                    next_item.pop("foreshadowing_operations", None)
                    next_item.pop("legacy_payload", None)
                    found = True
                    previous_timestamp = previous_item.get("last_written_at")
                    previous_semantic = copy.deepcopy(previous_item)
                    next_semantic = copy.deepcopy(next_item)
                    previous_semantic.pop("last_written_at", None)
                    next_semantic.pop("last_written_at", None)
                    if previous_timestamp and next_semantic == previous_semantic:
                        next_item["last_written_at"] = previous_timestamp
                    elif next_item != previous_item:
                        changed = True
                new_chapters.append(next_item)
            if not found:
                new_chapters.append(dict(updates))
                changed = True
            outline[key] = new_chapters

        if changed:
            project.outline_data = outline
            project.outline_version = (project.outline_version or 0) + 1
            project.updated_at = datetime.now(timezone.utc)
            await db.flush()

    async def _merge_chapter_summary(
        self,
        project: Project,
        chapter_number: int,
        summary: dict[str, Any],
        db: AsyncSession,
    ) -> None:
        if not summary:
            return

        core_data = copy.deepcopy(project.core_data or {})
        chapter_summaries = dict(core_data.get("chapter_summaries") or {})
        chapter_summaries[str(chapter_number)] = {
            **chapter_summaries.get(str(chapter_number), {}),
            **summary,
            "chapter_number": chapter_number,
        }
        core_data["chapter_summaries"] = chapter_summaries
        project.core_data = core_data
        project.updated_at = datetime.now(timezone.utc)
        await db.flush()

    def _extract_chapter_entry(self, outline: dict, chapter_number: int) -> dict | None:
        for key in ("chapter_spine", "chapters"):
            for item in outline.get(key, []) or []:
                if item.get("chapter_number") == chapter_number:
                    return item
        return None

    def _collect_foreshadowing_actions(
        self,
        *,
        chapter_number: int,
        chapter_entry: dict | None,
        summary: dict,
        scene_packages: list[dict],
    ) -> list[dict[str, Any]]:
        collected: dict[str, dict[str, Any]] = {}
        # A same-chapter summary often repeats an outline plant as "mention".
        # Keep the explicit plant so the canonical line can be created; reveal,
        # abandon and defer still override it.
        operation_rank = {"reinforce": 1, "plant": 2, "defer": 3, "abandon": 4, "reveal": 5}

        def merge_action(action: dict[str, Any], source: str) -> None:
            if not isinstance(action, dict):
                return
            action = sanitize_foreshadowing_action(action)
            if not action:
                return
            name = str(action.get("name", "")).strip()
            if not name:
                return
            operation = normalize_foreshadowing_operation(action.get("action"))
            identity = str(
                action.get("foreshadowing_id")
                or action.get("core_entity_id")
                or normalize_foreshadowing_name(name)
            )
            bucket = collected.setdefault(identity, {
                "name": name,
                "description": action.get("description") or action.get("summary") or name,
                "priority": action.get("priority", "moderate"),
                "action": operation,
                "source_system": source,
            })
            if operation_rank[operation] >= operation_rank.get(str(bucket.get("action")), 0):
                bucket["action"] = operation
            for field in (
                "foreshadowing_id", "core_entity_id", "clue_id", "description",
                "priority", "truth_type", "impact_level", "spoiler_scope",
                "evidence_text", "scene_index", "pov_character", "salience",
                "aliases", "timeline", "reveal_window", "latest_safe_reveal_chapter",
            ):
                value = action.get(field)
                if value not in (None, "", [], {}):
                    bucket[field] = value
            if operation == "plant" and "timeline" not in bucket:
                bucket["timeline"] = {"bury_window": [chapter_number, chapter_number]}

        def merge_container(container: dict | None, source: str, *, strings_are_plants: bool) -> None:
            if not isinstance(container, dict):
                return
            for key in ("foreshadowing_updates", "foreshadowing_actions", "foreshadowing_operations"):
                values = container.get(key) or []
                if not isinstance(values, list):
                    continue
                for item in values:
                    if isinstance(item, dict):
                        merge_action(item, source)
                    elif isinstance(item, str) and item.strip():
                        merge_action({
                            "name": item.strip(),
                            "description": item.strip(),
                            "action": "plant" if strings_are_plants else "reinforce",
                        }, source)

        merge_container(chapter_entry, "outline", strings_are_plants=True)
        merge_container(summary, "summary", strings_are_plants=False)
        for scene in scene_packages or []:
            merge_container(scene, "scene_package", strings_are_plants=False)
            if isinstance(scene, dict):
                merge_container(scene.get("output_effects"), "scene_effects", strings_are_plants=False)

        return list(collected.values())

    async def _append_change_notification(self, project: Project, db: AsyncSession, item: dict[str, Any]) -> None:
        core_data = copy.deepcopy(project.core_data or {})
        notifications = list(core_data.get("change_notifications", []))
        if not isinstance(notifications, list):
            notifications = []
        notifications.append(item)
        core_data["change_notifications"] = notifications[-20:]
        project.core_data = core_data
        project.updated_at = datetime.now(timezone.utc)
        await db.flush()

    def _changed_chapters(self, old_outline: dict, new_outline: dict) -> list[int]:
        old_numbers = set(self._outline_chapter_numbers(old_outline))
        new_numbers = set(self._outline_chapter_numbers(new_outline))
        changed = sorted(old_numbers.symmetric_difference(new_numbers))
        if changed:
            return changed

        old_map = self._outline_chapter_map(old_outline)
        new_map = self._outline_chapter_map(new_outline)
        changed = []
        for chapter_number in sorted(old_map.keys() & new_map.keys()):
            if old_map[chapter_number] != new_map[chapter_number]:
                changed.append(chapter_number)
        return changed

    def _outline_chapter_numbers(self, outline: dict) -> list[int]:
        numbers = []
        for key in ("chapter_spine", "chapters"):
            for ch in outline.get(key, []) or []:
                num = ch.get("chapter_number")
                if isinstance(num, int):
                    numbers.append(num)
        return sorted(set(numbers))

    def _outline_chapter_map(self, outline: dict) -> dict[int, dict]:
        result = {}
        for key in ("chapter_spine", "chapters"):
            for ch in outline.get(key, []) or []:
                num = ch.get("chapter_number")
                if isinstance(num, int) and num not in result:
                    result[num] = ch
        return result

    def _remove_chapter_from_outline(self, outline: dict, chapter_number: int) -> bool:
        changed = False
        for key in ("chapter_spine", "chapters", "scene_briefs"):
            value = outline.get(key)
            if key == "scene_briefs" and isinstance(value, dict):
                ch_id = f"ch_{chapter_number:03d}"
                if ch_id in value:
                    value.pop(ch_id, None)
                    outline[key] = value
                    changed = True
                continue
            if not isinstance(value, list):
                continue
            new_items = [item for item in value if item.get("chapter_number") != chapter_number]
            if len(new_items) != len(value):
                outline[key] = new_items
                changed = True
        return changed

    async def _get_project(self, project_id: str | uuid.UUID, db: AsyncSession) -> Project | None:
        pid = project_id if isinstance(project_id, uuid.UUID) else uuid.UUID(str(project_id))
        return await db.get(Project, pid)
