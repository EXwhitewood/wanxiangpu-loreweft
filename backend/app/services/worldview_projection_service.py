import hashlib
import uuid
import logging
import re

from datetime import datetime, timezone
from sqlalchemy import select, delete as sql_delete, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.db_models import WorldviewObservation, Chapter, ChapterEffectOutbox, DetailSeed, Project
from app.services.worldview_extractor import WorldviewExtractor, normalize_entity_name
from app.services.memory_core import CoreMemoryService
from app.services.cross_system_event_bus import CrossSystemEventBus

logger = logging.getLogger(__name__)

AUTO_PROMOTABLE_TYPES = {"character", "location", "item", "world_rule"}
MANUALLY_PROMOTABLE_TYPES = AUTO_PROMOTABLE_TYPES | {"foreshadowing"}

AUTO_PROMOTE_THRESHOLD = {
    "character": 0.85,
    "location": 0.85,
    # 方案 3 B3：物品自动提升阈值（与人物/地点一致）
    "item": 0.85,
    # 方案 22 B2：世界规则自动晋升阈值（高置信度才自动晋升，避免规则爆炸）
    "world_rule": 0.85,
}


def compute_fingerprint(
    project_id: str,
    entity_type: str,
    entity_name_normalized: str,
    chapter_number: int,
    extraction_version: int,
    generation_revision: int = 1,
) -> str:
    raw = f"{project_id}:{entity_type}:{entity_name_normalized}:{chapter_number}:v{extraction_version}:r{generation_revision}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class WorldviewProjectionService:
    def __init__(self) -> None:
        self._extractor = WorldviewExtractor()
        self._event_bus = CrossSystemEventBus()

    @staticmethod
    def _core_entity_id(value) -> str | None:
        if value is None or value == "":
            return None
        return str(value)

    @staticmethod
    def _observation_payload(item: dict) -> dict:
        excluded = {"name", "operation", "evidence_text", "confidence"}
        payload = {k: v for k, v in item.items() if k not in excluded}
        payload.setdefault("description", item.get("description", ""))
        payload.setdefault("aliases", item.get("aliases", []))
        return payload

    @staticmethod
    def _merge_list(existing, incoming) -> list:
        result = []
        seen = set()
        for value in list(existing or []) + list(incoming or []):
            text = str(value).strip()
            key = normalize_entity_name(text)
            if not text or key in seen:
                continue
            seen.add(key)
            result.append(text)
        return result

    @staticmethod
    def _merge_dict(existing, incoming) -> dict:
        merged = dict(existing or {})
        for key, value in dict(incoming or {}).items():
            if key and value and not merged.get(key):
                merged[key] = value
        return merged

    def _build_character_data(self, name: str, description: str, aliases: list, payload: dict) -> dict:
        labeled = self._parse_labeled_character_description(description)
        lifecycle = payload.get("lifecycle_status") or payload.get("status") or labeled.get("lifecycle_status") or "unknown"
        importance = payload.get("role_importance") or labeled.get("role_importance") or "supporting"
        activity = payload.get("narrative_activity") or labeled.get("narrative_activity")
        if not activity:
            activity = "core_active" if importance in {"protagonist", "main"} else "dormant"
        return {
            "name": name,
            "aliases": aliases or [],
            "description": description,
            "appearance": payload.get("appearance") or labeled.get("appearance", ""),
            "personality": payload.get("personality") or labeled.get("personality", ""),
            "desire": payload.get("desire") or labeled.get("desire", ""),
            "deep_need": payload.get("deep_need") or labeled.get("deep_need", ""),
            "arc": payload.get("arc") or labeled.get("arc", ""),
            "relationships": payload.get("relationships", {}) if isinstance(payload.get("relationships"), dict) else {},
            "role": payload.get("role") or labeled.get("role", ""),
            "faction": payload.get("faction") or labeled.get("faction", ""),
            "status": lifecycle,
            "lifecycle_status": lifecycle,
            "narrative_activity": activity,
            "role_importance": importance,
        }

    @staticmethod
    def _parse_labeled_character_description(description: str) -> dict:
        """Recover only explicitly labelled legacy fields; never infer prose."""
        labels = {
            "appearance": ("appearance", "外貌", "外观"),
            "personality": ("personality", "性格"),
            "desire": ("desire", "欲望", "目标"),
            "deep_need": ("deep_need", "深层需求", "内在需求"),
            "arc": ("arc", "人物弧", "弧光"),
            "role": ("role", "角色定位"),
            "faction": ("faction", "阵营"),
            "lifecycle_status": ("lifecycle_status", "生命状态"),
            "narrative_activity": ("narrative_activity", "叙事活跃度"),
            "role_importance": ("role_importance", "重要性"),
        }
        result = {}
        for target, variants in labels.items():
            joined = "|".join(re.escape(label) for label in variants)
            match = re.search(
                rf"(?:^|[\n；;])\s*(?:{joined})\s*[:：]\s*([^\n；;]+)",
                description or "",
                flags=re.IGNORECASE,
            )
            if match and match.group(1).strip():
                result[target] = match.group(1).strip()
        return result

    def _build_location_data(self, name: str, description: str, payload: dict, chapter_number: int) -> dict:
        data = {
            "name": name,
            "description": description,
            "atmosphere": payload.get("atmosphere", ""),
            "parent_location": payload.get("parent_location", ""),
            "location_type": payload.get("location_type", "place"),
            "function": payload.get("function", ""),
            "spatial_relations": payload.get("spatial_relations", {}) if isinstance(payload.get("spatial_relations"), dict) else {},
            "rules": payload.get("rules", []) if isinstance(payload.get("rules"), list) else [],
            "source": "worldview_projection",
            "first_seen_chapter": chapter_number,
        }
        return {k: v for k, v in data.items() if v not in ("", None, [], {})}

    def _build_item_data(self, name: str, description: str, payload: dict, chapter_number: int) -> dict:
        """方案 3 B3：构建物品卡数据，用于自动提升到 Core 层。"""
        data = {
            "name": name,
            "aliases": payload.get("aliases", []) if isinstance(payload.get("aliases"), list) else [],
            "appearance": payload.get("appearance", "") or description,
            "function": payload.get("function", ""),
            "origin": payload.get("origin", ""),
            "owner": payload.get("owner", ""),
            "status": payload.get("status", "intact"),
            "description": description,
            "importance": payload.get("importance", "minor"),
            "first_appear_chapter": chapter_number,
            "related_foreshadowing": payload.get("related_foreshadowing", ""),
        }
        return {k: v for k, v in data.items() if v not in ("", None, [], {})}

    @staticmethod
    def _split_sentences(text: str) -> list[str]:
        return [part.strip() for part in re.split(r"(?<=[。！？!?；;])\s*", text or "") if part.strip()]

    @staticmethod
    def _infer_location_type(name: str, current: str = "") -> str:
        if current and current != "place":
            return current
        suffix_map = (
            ("外域", "region"),
            ("山门", "gate"),
            ("密道", "passage"),
            ("渡", "port"),
            ("渡口", "port"),
            ("台", "platform"),
            ("宗", "sect"),
            ("城", "city"),
            ("阁", "building"),
            ("殿", "building"),
            ("峰", "mountain"),
            ("谷", "valley"),
            ("库", "building"),
        )
        for suffix, loc_type in suffix_map:
            if name.endswith(suffix):
                return loc_type
        return current or "place"

    @staticmethod
    def _relation_score(parent: str, child: str, sentence: str, prev_sentence: str = "") -> int:
        if not parent or not child or parent == child:
            return 0
        compact = re.sub(r"\s+", "", sentence or "")
        prev_compact = re.sub(r"\s+", "", prev_sentence or "")
        if parent not in compact and child not in compact:
            return 0

        score = 0
        if child in prev_compact and parent in compact:
            parent_re = re.escape(parent)
            if re.search(rf"(有一[座处个]?{parent_re}|有[座处个]?{parent_re})", compact):
                return 0

        if parent in compact and child in compact:
            score += 2
            parent_pos = compact.find(parent)
            child_pos = compact.find(child)
            between = compact[min(parent_pos, child_pos):max(parent_pos, child_pos) + len(child)]
            if re.search(r"(隶属于|属于|位于|坐落于|在|里|内|中|中央|下|上|外域|山门|渡口)", between):
                score += 4
            if child_pos > parent_pos:
                score += 1

        if child in prev_compact and parent in compact and re.search(r"(这里|此处|此地|其地|该地|隶属于|属于|辖下|境内)", compact):
            score += 5

        if child in compact and parent in prev_compact and re.search(r"(这里|此处|其中|中央|里面|里|内|台阶|门内|殿内|阁内|渡口|码头)", compact):
            score += 8

        if child in compact and parent.endswith(("渡", "渡口")) and re.search(r"(渡口|中央|台阶|岸边|码头)", compact):
            score += 3

        if child in compact and parent.endswith(("外域", "山门", "宗", "城")) and re.search(r"(隶属于|外域|宗门|山门|辖下|境内)", compact):
            score += 3

        return score

    @staticmethod
    def _is_plausible_location_parent(parent_type: str, child_type: str, parent: str, child: str) -> bool:
        parent_type = parent_type or "place"
        child_type = child_type or "place"
        if normalize_entity_name(parent) == normalize_entity_name(child):
            return False
        if child.startswith(parent) and len(child) > len(parent):
            return True
        small_types = {"platform", "passage", "building", "gate"}
        large_types = {"sect", "region", "city"}
        if child_type in large_types and parent_type in small_types | {"port"}:
            return False
        if parent_type in {"platform", "passage"}:
            return False
        if child_type == "passage":
            return parent_type in large_types | {"building", "place"}
        if child_type == "platform":
            return parent_type in large_types | {"port", "building", "place"}
        if child_type == "port":
            return parent_type in large_types | {"place"}
        return True

    def _infer_location_hierarchy(self, locations: list[dict], chapter_content: str) -> None:
        if not locations:
            return

        names = [str(item.get("name", "")).strip() for item in locations if str(item.get("name", "")).strip()]
        if not names:
            return

        by_name = {normalize_entity_name(item.get("name", "")): item for item in locations}
        for item in locations:
            name = str(item.get("name", "")).strip()
            if name:
                item["location_type"] = self._infer_location_type(name, str(item.get("location_type", "")))

        type_by_name = {
            normalize_entity_name(item.get("name", "")): item.get("location_type", "place")
            for item in locations
        }
        sentences = self._split_sentences(chapter_content)
        mentioned_by_sentence = [
            [name for name in names if name and name in sentence]
            for sentence in sentences
        ]

        for item in locations:
            name = str(item.get("name", "")).strip()
            if not name:
                continue

            existing_parent = str(item.get("parent_location", "")).strip()
            existing_parent_key = normalize_entity_name(existing_parent)
            if existing_parent and existing_parent_key in by_name:
                parent_type = type_by_name.get(existing_parent_key, "place")
                child_type = item.get("location_type", "place")
                if self._is_plausible_location_parent(parent_type, child_type, existing_parent, name):
                    continue
                item.pop("parent_location", None)
            elif existing_parent:
                continue

            for parent in names:
                if parent != name and name.startswith(parent) and len(name) > len(parent):
                    item["parent_location"] = parent
                    break
            if item.get("parent_location"):
                continue

            best_parent = ""
            best_score = 0
            for idx, sentence in enumerate(sentences):
                prev_sentence = sentences[idx - 1] if idx > 0 else ""
                if name not in sentence and not (
                    name in prev_sentence and re.search(r"(这里|此处|此地|其地|该地|隶属于|属于|辖下|境内)", sentence)
                ):
                    continue
                candidates = list(mentioned_by_sentence[idx])
                if idx > 0:
                    candidates.extend(mentioned_by_sentence[idx - 1])
                for parent in candidates:
                    if normalize_entity_name(parent) == normalize_entity_name(name):
                        continue
                    parent_type = type_by_name.get(normalize_entity_name(parent), "place")
                    child_type = item.get("location_type", "place")
                    if child_type in {"sect", "region", "city"} and parent not in sentence:
                        continue
                    if not self._is_plausible_location_parent(parent_type, child_type, parent, name):
                        continue
                    score = self._relation_score(parent, name, sentence, prev_sentence)
                    if score > best_score:
                        best_parent = parent
                        best_score = score

            if best_parent and best_score >= 4 and normalize_entity_name(best_parent) in by_name:
                item["parent_location"] = best_parent

        for item in locations:
            name = str(item.get("name", "")).strip()
            parent = str(item.get("parent_location", "")).strip()
            if not parent:
                continue
            parent_item = by_name.get(normalize_entity_name(parent))
            if parent_item and normalize_entity_name(parent_item.get("parent_location", "")) == normalize_entity_name(name):
                parent_type = parent_item.get("location_type", "place")
                child_type = item.get("location_type", "place")
                if not self._is_plausible_location_parent(parent_type, child_type, parent, name):
                    item.pop("parent_location", None)

    async def project_committed_chapter(
        self,
        db: AsyncSession,
        project_id: str | uuid.UUID,
        chapter_number: int,
        *,
        project: Project | None = None,
        generation_revision: int | None = None,
    ) -> dict:
        pid = uuid.UUID(str(project_id))

        chapter_stmt = select(Chapter).where(
            Chapter.project_id == pid,
            Chapter.chapter_number == chapter_number,
        )
        chapter_result = await db.execute(chapter_stmt)
        chapter = chapter_result.scalar_one_or_none()
        if not chapter or not chapter.content:
            logger.warning("[WPS] chapter %s/%s not found or empty", project_id, chapter_number)
            return {"observations_created": 0, "seeds_written": 0, "auto_promoted": 0}

        chapter_content = chapter.content
        chapter_id = chapter.id

        extraction_version = self._extractor.EXTRACTION_VERSION

        if generation_revision is not None:
            old_max_revision = generation_revision - 1
        else:
            existing_obs = await db.execute(
                select(func.max(WorldviewObservation.generation_revision)).where(
                    WorldviewObservation.project_id == pid,
                    WorldviewObservation.chapter_number == chapter_number,
                )
            )
            old_max_revision = existing_obs.scalar() or 0
            generation_revision = old_max_revision + 1

        chapter_outline = ""
        if project:
            outline_data = project.outline_data or {}
            spine = outline_data.get("chapter_spine", [])
            if isinstance(spine, list):
                for item in spine:
                    if isinstance(item, dict) and item.get("chapter_number") == chapter_number:
                        chapter_outline = item.get("conflict_text", "") or item.get("title", "")
                        break

        extractions = await self._extractor.extract_from_chapter(
            str(pid),
            chapter_number,
            chapter_content,
            chapter_outline=chapter_outline,
            db=db,
        )
        self._infer_location_hierarchy(extractions.get("locations", []), chapter_content)

        type_map = {
            "characters": "character",
            "locations": "location",
            "world_rules": "world_rule",
            "foreshadowing": "foreshadowing",
            "facts": "fact",
            # 方案 3 B3：物品类目纳入观测投影
            "items": "item",
        }

        observations_created = 0
        seeds_written = 0
        auto_promoted = 0
        created_observations = []
        created_observation_items: list[tuple[WorldviewObservation, dict]] = []

        for extraction_key, entity_type in type_map.items():
            items = extractions.get(extraction_key, [])
            for item in items:
                name = item.get("name", "").strip()
                if not name:
                    continue

                name_normalized = normalize_entity_name(name)
                fp = compute_fingerprint(
                    str(pid), entity_type, name_normalized, chapter_number, extraction_version, generation_revision,
                )

                existing = await db.execute(
                    select(WorldviewObservation).where(
                        WorldviewObservation.fingerprint == fp,
                    )
                )
                if existing.scalar_one_or_none():
                    continue

                obs = WorldviewObservation(
                    id=uuid.uuid4(),
                    project_id=pid,
                    chapter_number=chapter_number,
                    scene_index=None,
                    chapter_id=chapter_id,
                    entity_type=entity_type,
                    entity_name=name,
                    entity_name_normalized=name_normalized,
                    operation=item.get("operation", "new"),
                    payload=self._observation_payload(item),
                    evidence_text=item.get("evidence_text", ""),
                    fingerprint=fp,
                    confidence=float(item.get("confidence", 0.0)),
                    status="active",
                    extraction_version=extraction_version,
                    extraction_source="chapter_commit",
                    generation_revision=generation_revision,
                )
                db.add(obs)
                await db.flush()
                observations_created += 1
                created_observations.append(obs)
                created_observation_items.append((obs, item))

                seed_written = await self._dual_write_shell_seed(
                    db, pid, obs, item, chapter_id, chapter_number,
                )
                seeds_written += seed_written

        await db.flush()

        for obs in created_observations:
            if obs.entity_type in AUTO_PROMOTABLE_TYPES:
                threshold = AUTO_PROMOTE_THRESHOLD.get(obs.entity_type, 1.0)
                if obs.confidence >= threshold:
                    promoted = await self._auto_promote_observation(db, obs)
                    if promoted:
                        auto_promoted += 1

        foreshadowing_items = []
        for observation, item in created_observation_items:
            if observation.entity_type != "foreshadowing":
                continue
            foreshadowing_items.append({
                **item,
                "source_observation_id": str(observation.id),
                "source_fingerprint": observation.fingerprint,
                "core_entity_id": observation.core_entity_id or item.get("foreshadowing_id"),
            })
        if foreshadowing_items:
            foreshadowing_updates = await self._sync_foreshadowing_from_observation(
                db, pid, chapter_number, foreshadowing_items,
            )
            observations_by_id = {
                str(observation.id): observation
                for observation, _ in created_observation_items
                if observation.entity_type == "foreshadowing"
            }
            for update in foreshadowing_updates:
                observation = observations_by_id.get(str(update.get("source_observation_id") or ""))
                if not observation:
                    continue
                observation.core_entity_id = str(update.get("id") or "") or None
                observation.auto_promoted = True
                observation.status = "promoted"
                observation.confirmed_by = "system"
                observation.confirmed_at = datetime.now(timezone.utc)
                auto_promoted += 1

        # Degraded extraction: LLM parse failed, no new observations created.
        # Must NOT retract old revisions — doing so would wipe existing
        # worldview data with nothing to replace it.
        is_degraded = bool(extractions.get("_degraded"))
        degraded_reason = extractions.get("_degraded_reason", "")

        if is_degraded:
            logger.warning(
                "[WPS] degraded extraction for chapter %s (reason=%s), skipping old revision retraction",
                chapter_number, degraded_reason,
            )
            await db.flush()
            return {
                "observations_created": 0,
                "seeds_written": 0,
                "auto_promoted": 0,
                "old_revisions_retracted": 0,
                "degraded": True,
                "degraded_reason": degraded_reason,
            }

        # Two-phase commit: retract old generation_revisions only AFTER new
        # observations are successfully created and promoted.  If anything
        # above raises, old worldview data is preserved untouched.
        retract_result = {"observations_retracted": 0, "seeds_deleted": 0, "entities_reconciled": 0, "entities_archived": 0, "orphan_warning_set": 0, "foreshadowing_orphaned": 0}
        retract_failed = False
        if old_max_revision > 0:
            try:
                retract_result = await self._retract_old_revisions(
                    db, pid, chapter_number, old_max_revision,
                )
            except Exception as e:
                retract_failed = True
                logger.warning(
                    "[WPS] failed to retract old revisions (chapter %s, <=r%s): %s",
                    chapter_number, old_max_revision, e,
                )

        await self._event_bus.publish_event(
            db=db,
            project_id=str(pid),
            event_type="WORLDVIEW_PROJECTED.v1",
            source_system="worldview_projection",
            priority="normal",
            payload={
                "entity_type": "worldview_observation",
                "change_type": "created",
                "chapter_number": chapter_number,
                "observations_created": observations_created,
                "auto_promoted": auto_promoted,
                "old_revisions_retracted": retract_result.get("observations_retracted", 0),
            },
        )

        await db.flush()

        result = {
            "observations_created": observations_created,
            "seeds_written": seeds_written,
            "auto_promoted": auto_promoted,
            "old_revisions_retracted": retract_result.get("observations_retracted", 0),
        }
        if retract_failed:
            result["old_revisions_retract_failed"] = True
        return result

    async def project_committed_chapter_atomically(
        self,
        db: AsyncSession,
        project_id: str | uuid.UUID,
        chapter_number: int,
        *,
        project: Project | None = None,
        generation_revision: int | None = None,
    ) -> dict:
        async with db.begin_nested():
            return await self.project_committed_chapter(
                db,
                project_id,
                chapter_number,
                project=project,
                generation_revision=generation_revision,
            )

    async def _retract_old_revisions(
        self,
        db: AsyncSession,
        project_id: uuid.UUID,
        chapter_number: int,
        max_generation_revision: int,
    ) -> dict:
        """Retract observations whose generation_revision <= max_generation_revision.

        Called by ``project_committed_chapter`` *after* new observations have
        been created so that old worldview data is only removed when the
        replacement is guaranteed to exist.
        """
        obs_stmt = select(WorldviewObservation).where(
            WorldviewObservation.project_id == project_id,
            WorldviewObservation.chapter_number == chapter_number,
            WorldviewObservation.generation_revision <= max_generation_revision,
            WorldviewObservation.status.in_(["active", "promoted"]),
        )
        obs_result = await db.execute(obs_stmt)
        observations = obs_result.scalars().all()

        if not observations:
            return {
                "observations_retracted": 0,
                "seeds_deleted": 0,
                "entities_reconciled": 0,
                "entities_archived": 0,
                "orphan_warning_set": 0,
                "foreshadowing_orphaned": 0,
            }

        now = datetime.now(timezone.utc)
        retracted_ids = []
        affected_entities = set()

        for obs in observations:
            obs.status = "retracted"
            obs.retracted_at = now
            retracted_ids.append(obs.id)
            affected_entities.add((obs.entity_type, obs.entity_name or obs.entity_name_normalized))

        await db.flush()

        if retracted_ids:
            await db.execute(
                sql_delete(DetailSeed).where(
                    DetailSeed.source_observation_id.in_(retracted_ids),
                )
            )
            await db.flush()

        reconciled = 0
        archived = 0
        warned = 0

        for entity_type, entity_name_norm in affected_entities:
            result = await self.reconcile_entity(db, project_id, entity_type, entity_name_norm, hard=False)
            reconciled += 1
            if result.get("archived"):
                archived += 1
            if result.get("orphan_warning"):
                warned += 1

        from app.services.narrative_sync_service import NarrativeSyncService
        nss = NarrativeSyncService()
        orphaned = await nss._orphan_foreshadowing_lines(project_id, chapter_number, db)

        logger.info(
            "[WPS] retracted old revisions for chapter %s (<=r%s): %d obs, %d archived, %d orphaned",
            chapter_number, max_generation_revision, len(retracted_ids), archived, orphaned,
        )

        return {
            "observations_retracted": len(retracted_ids),
            "seeds_deleted": len(retracted_ids),
            "entities_reconciled": reconciled,
            "entities_archived": archived,
            "orphan_warning_set": warned,
            "foreshadowing_orphaned": orphaned,
        }

    async def retract_chapter_sources(
        self,
        db: AsyncSession,
        project_id: str | uuid.UUID,
        chapter_number: int,
        *,
        hard: bool = False,
    ) -> dict:
        pid = uuid.UUID(str(project_id))

        statuses = ["active", "promoted", "retracted"] if hard else ["active", "promoted"]
        obs_stmt = select(WorldviewObservation).where(
            WorldviewObservation.project_id == pid,
            WorldviewObservation.chapter_number == chapter_number,
            WorldviewObservation.status.in_(statuses),
        )
        obs_result = await db.execute(obs_stmt)
        observations = obs_result.scalars().all()

        now = datetime.now(timezone.utc)
        retracted_ids = []
        affected_entities = set()

        for obs in observations:
            obs.status = "retracted"
            obs.retracted_at = now
            retracted_ids.append(obs.id)
            affected_entities.add((obs.entity_type, obs.entity_name or obs.entity_name_normalized))

        await db.flush()

        if retracted_ids:
            await db.execute(
                sql_delete(DetailSeed).where(
                    DetailSeed.source_observation_id.in_(retracted_ids),
                )
            )
            await db.flush()

        reconciled = 0
        archived = 0
        warned = 0

        for entity_type, entity_name_norm in affected_entities:
            result = await self.reconcile_entity(db, pid, entity_type, entity_name_norm, hard=hard)
            reconciled += 1
            if result.get("archived"):
                archived += 1
            if result.get("orphan_warning"):
                warned += 1

        from app.services.narrative_sync_service import NarrativeSyncService

        nss = NarrativeSyncService()
        orphaned = await nss._orphan_foreshadowing_lines(pid, chapter_number, db)

        await self._event_bus.publish_event(
            db=db,
            project_id=str(pid),
            event_type="WORLDVIEW_PROJECTION_RETRACTED.v1",
            source_system="worldview_projection",
            priority="high",
            payload={
                "entity_type": "worldview_observation",
                "change_type": "retracted",
                "chapter_number": chapter_number,
                "observations_retracted": len(retracted_ids),
                "entities_reconciled": reconciled,
                "entities_archived": archived,
                "orphan_warning_set": warned,
                "foreshadowing_orphaned": orphaned,
            },
        )

        await db.flush()

        return {
            "observations_retracted": len(retracted_ids),
            "seeds_deleted": len(retracted_ids),
            "entities_reconciled": reconciled,
            "entities_archived": archived,
            "orphan_warning_set": warned,
            "foreshadowing_orphaned": orphaned,
        }

    async def reconcile_entity(
        self,
        db: AsyncSession,
        project_id: str | uuid.UUID,
        entity_type: str,
        entity_name: str,
        *,
        hard: bool = False,
    ) -> dict:
        pid = uuid.UUID(str(project_id))
        name_normalized = normalize_entity_name(entity_name)

        supporting_count = await db.execute(
            select(func.count()).select_from(WorldviewObservation).where(
                WorldviewObservation.project_id == pid,
                WorldviewObservation.entity_type == entity_type,
                WorldviewObservation.entity_name_normalized == name_normalized,
                WorldviewObservation.status.in_(["active", "promoted"]),
            )
        )
        has_support = (supporting_count.scalar() or 0) > 0

        if has_support:
            return {"entity": entity_name, "has_support": True, "archived": False, "orphan_warning": False}

        obs_list = await db.execute(
            select(WorldviewObservation).where(
                WorldviewObservation.project_id == pid,
                WorldviewObservation.entity_type == entity_type,
                WorldviewObservation.entity_name_normalized == name_normalized,
                WorldviewObservation.auto_promoted == True,
            )
        )
        promoted_obs = obs_list.scalars().all()

        if not promoted_obs:
            return {"entity": entity_name, "has_support": False, "archived": False, "orphan_warning": False}

        core_service = CoreMemoryService()
        any_archived = False
        any_warned = False

        for obs in promoted_obs:
            if not obs.core_entity_id:
                continue

            user_confirmed = obs.confirmed_by and obs.confirmed_by != "system"

            if hard or not user_confirmed:
                try:
                    if entity_type == "character":
                        await core_service.delete_character_with_db(db, str(pid), obs.core_entity_id)
                    elif entity_type == "location":
                        await core_service.delete_location_with_db(db, str(pid), obs.core_entity_id)
                    elif entity_type == "foreshadowing":
                        from app.services.foreshadowing_service import ForeshadowingService
                        fs = ForeshadowingService()
                        await fs.update_foreshadowing_line(
                            str(pid), obs.core_entity_id,
                            {"status": "aborted"},
                            db,
                            changed_by="worldview_projection",
                        )

                    obs.status = "retracted"
                    obs.archived_at = datetime.now(timezone.utc)
                    any_archived = True
                except Exception as e:
                    logger.warning("[WPS] failed to archive core entity %s: %s", obs.core_entity_id, e)
                    raise
            else:
                obs.orphan_warning = True
                any_warned = True

        await db.flush()

        return {
            "entity": entity_name,
            "has_support": False,
            "archived": any_archived,
            "orphan_warning": any_warned,
        }

    async def promote_observation(
        self,
        db: AsyncSession,
        observation_id: str,
        *,
        confirmed_by: str = "user",
    ) -> dict:
        obs = await db.get(WorldviewObservation, uuid.UUID(str(observation_id)))
        if not obs:
            return {"error": "observation not found"}

        if obs.status not in ("active",):
            return {"error": f"observation status is {obs.status}, cannot promote"}
        if obs.entity_type not in MANUALLY_PROMOTABLE_TYPES:
            return {"error": f"observation type {obs.entity_type} has no Core promotion target"}

        promoted = await self._auto_promote_observation(db, obs, force=True)
        if not promoted:
            return {"error": "observation promotion produced no durable Core entity"}
        obs.confirmed_by = confirmed_by
        obs.confirmed_at = datetime.now(timezone.utc)
        obs.status = "promoted"
        await db.flush()

        return {
            "observation_id": str(obs.id),
            "entity_type": obs.entity_type,
            "entity_name": obs.entity_name,
            "confirmed_by": confirmed_by,
            "promoted": promoted,
        }

    async def reject_observation(
        self,
        db: AsyncSession,
        observation_id: str,
        *,
        rejected_by: str = "user",
        reason: str = "",
    ) -> dict:
        obs = await db.get(WorldviewObservation, uuid.UUID(str(observation_id)))
        if not obs:
            return {"error": "observation not found"}

        original_confirmed_by = obs.confirmed_by

        obs.status = "rejected"

        if obs.id:
            await db.execute(
                sql_delete(DetailSeed).where(
                    DetailSeed.source_observation_id == obs.id,
                )
            )

        if obs.auto_promoted and obs.core_entity_id:
            if original_confirmed_by and original_confirmed_by != "system":
                obs.orphan_warning = True
            else:
                await self.reconcile_entity(
                    db, obs.project_id, obs.entity_type, obs.entity_name_normalized,
                )
                obs.status = "rejected"

        obs.confirmed_by = rejected_by
        obs.confirmed_at = datetime.now(timezone.utc)

        await db.flush()

        return {
            "observation_id": str(obs.id),
            "entity_type": obs.entity_type,
            "entity_name": obs.entity_name,
            "rejected_by": rejected_by,
            "reason": reason,
        }

    async def retry_pending_projection(
        self,
        db: AsyncSession,
        project_id: str | uuid.UUID,
        chapter_number: int,
    ) -> dict:
        pid = uuid.UUID(str(project_id))

        stmt = select(ChapterEffectOutbox).where(
            ChapterEffectOutbox.project_id == pid,
            ChapterEffectOutbox.chapter_number == chapter_number,
            ChapterEffectOutbox.effect_type == "worldview_projection",
            ChapterEffectOutbox.status == "retryable_failed",
        )
        result = await db.execute(stmt)
        outbox_items = result.scalars().all()

        if not outbox_items:
            return {"retried": 0, "message": "no retryable_failed items found"}

        project = await db.get(Project, pid)
        retried = 0

        for item in outbox_items:
            try:
                item.status = "applying"
                item.attempts = (item.attempts or 0) + 1
                await db.flush()

                projection_result = await self.project_committed_chapter_atomically(
                    db, pid, chapter_number, project=project,
                    generation_revision=(item.payload or {}).get("generation_revision"),
                )

                item.status = "applied"
                item.applied = True
                item.applied_at = datetime.now(timezone.utc)
                item.error_message = ""
                retried += 1
            except Exception as e:
                item.status = "retryable_failed"
                item.error_message = str(e)[:500]
                logger.warning("[WPS] retry failed for chapter %s: %s", chapter_number, e)

            await db.flush()

        return {"retried": retried}

    async def retry_all_pending(
        self,
        db: AsyncSession,
        project_id: str | uuid.UUID,
    ) -> dict:
        pid = uuid.UUID(str(project_id))

        stmt = select(ChapterEffectOutbox).where(
            ChapterEffectOutbox.project_id == pid,
            ChapterEffectOutbox.effect_type == "worldview_projection",
            ChapterEffectOutbox.status == "retryable_failed",
        )
        result = await db.execute(stmt)
        outbox_items = result.scalars().all()

        if not outbox_items:
            return {"retried": 0, "message": "no retryable_failed items found"}

        project = await db.get(Project, pid)
        retried = 0

        for item in outbox_items:
            ch_num = item.chapter_number
            try:
                item.status = "applying"
                item.attempts = (item.attempts or 0) + 1
                await db.flush()

                projection_result = await self.project_committed_chapter_atomically(
                    db, pid, ch_num, project=project,
                    generation_revision=(item.payload or {}).get("generation_revision"),
                )

                item.status = "applied"
                item.applied = True
                item.applied_at = datetime.now(timezone.utc)
                item.error_message = ""
                retried += 1
            except Exception as e:
                item.status = "retryable_failed"
                item.error_message = str(e)[:500]
                logger.warning("[WPS] retry failed for chapter %s: %s", ch_num, e)

            await db.flush()

        return {"retried": retried}

    async def re_extract_project(
        self,
        db: AsyncSession,
        project_id: str | uuid.UUID,
        *,
        from_version: int = 0,
        to_version: int | None = None,
    ) -> dict:
        pid = uuid.UUID(str(project_id))

        if to_version is None:
            to_version = self._extractor.EXTRACTION_VERSION

        stmt = (
            select(WorldviewObservation.chapter_number)
            .where(
                WorldviewObservation.project_id == pid,
                WorldviewObservation.extraction_version <= from_version,
                WorldviewObservation.status.in_(["active", "promoted"]),
            )
            .distinct()
        )
        result = await db.execute(stmt)
        chapter_numbers = sorted(row[0] for row in result.all())

        if not chapter_numbers:
            all_ch_stmt = (
                select(WorldviewObservation.chapter_number)
                .where(WorldviewObservation.project_id == pid)
                .distinct()
            )
            all_ch_result = await db.execute(all_ch_stmt)
            chapter_numbers = sorted(row[0] for row in all_ch_result.all())

        project = await db.get(Project, pid)
        total_retracted = 0
        total_projected = 0
        total_promoted = 0

        for ch_num in chapter_numbers:
            # No separate retract_chapter_sources call needed —
            # project_committed_chapter now handles old-revision retraction
            # internally after new observations are created (two-phase commit).
            saved_version = self._extractor.EXTRACTION_VERSION
            self._extractor.EXTRACTION_VERSION = to_version
            try:
                project_result = await self.project_committed_chapter(
                    db, pid, ch_num, project=project,
                )
            finally:
                self._extractor.EXTRACTION_VERSION = saved_version
            total_retracted += project_result.get("old_revisions_retracted", 0)
            total_projected += project_result.get("observations_created", 0)
            total_promoted += project_result.get("auto_promoted", 0)

        return {
            "chapters_re_extracted": len(chapter_numbers),
            "observations_retracted": total_retracted,
            "observations_created": total_projected,
            "auto_promoted": total_promoted,
        }

    async def _dual_write_shell_seed(
        self,
        db: AsyncSession,
        project_id: uuid.UUID,
        observation: WorldviewObservation,
        item: dict,
        chapter_id: uuid.UUID,
        chapter_number: int,
    ) -> int:
        try:
            entity_type = observation.entity_type
            name = observation.entity_name

            tier = "T3"
            if entity_type == "foreshadowing":
                tier = "T1"
            elif entity_type in ("character", "location"):
                tier = "T2"

            if entity_type == "character":
                entity_id = name
                fact = f"新人物：{name} - {item.get('description', '')}"
            elif entity_type == "location":
                entity_id = name
                fact = f"新地点：{name} - {item.get('description', '')}"
            elif entity_type == "world_rule":
                entity_id = f"rule:{name}"
                fact = f"规则暗示：{name} - {item.get('description', '')}"
            elif entity_type == "foreshadowing":
                entity_id = f"fs:{name}"
                fact = f"新伏笔：{name} - {item.get('description', '')}"
            elif entity_type == "fact":
                entity_id = f"fact:{name}"
                fact = f"新事实：{name} - {item.get('description', '')}"
            else:
                entity_id = name
                fact = f"{entity_type}: {name} - {item.get('description', '')}"

            seed = DetailSeed(
                id=uuid.uuid4(),
                project_id=project_id,
                chapter_id=chapter_id,
                scene_number=1,
                entity_id=entity_id,
                fact=fact,
                tier=tier,
                source_text=item.get("evidence_text", ""),
                entity_type=entity_type,
                source_kind="observation",
                source_observation_id=observation.id,
                metadata_json={
                    "name": name,
                    "aliases": item.get("aliases", []),
                    "auto_extracted": True,
                    "confidence": observation.confidence,
                    "operation": observation.operation,
                },
            )
            db.add(seed)
            return 1
        except Exception as e:
            logger.warning("[WPS] dual-write shell seed failed for obs %s: %s", observation.id, e)
            raise

    async def _auto_promote_observation(
        self,
        db: AsyncSession,
        observation: WorldviewObservation,
        *,
        force: bool = False,
    ) -> bool:
        entity_type = observation.entity_type
        if entity_type not in AUTO_PROMOTABLE_TYPES and not force:
            return False

        if not force:
            threshold = AUTO_PROMOTE_THRESHOLD.get(entity_type, 1.0)
            if observation.confidence < threshold:
                # P2-16：低于阈值的 rule 不晋升，记录 info 日志
                logger.info(
                    "[WPS] %s '%s' confidence=%.2f below threshold %.2f, skip auto-promote",
                    entity_type, observation.entity_name,
                    float(observation.confidence or 0.0), threshold,
                )
                return False

        if observation.auto_promoted and observation.core_entity_id:
            return False

        core_service = CoreMemoryService()
        pid = str(observation.project_id)
        name = observation.entity_name
        payload = observation.payload or {}
        description = payload.get("description", "")
        aliases = payload.get("aliases", [])

        def _mark_promoted(obs: WorldviewObservation) -> None:
            obs.auto_promoted = True
            obs.status = "promoted"
            if not force:
                obs.confirmed_by = "system"
            obs.confirmed_at = datetime.now(timezone.utc)

        try:
            if entity_type == "character":
                incoming = self._build_character_data(name, description, aliases, payload)
                resolved = await core_service.resolve_entity_with_db(
                    db,
                    pid,
                    name,
                    expected_type="character",
                )
                if resolved:
                    c_data = resolved["card"]
                    char_id = self._core_entity_id(c_data.get("id"))
                    updates = {}
                    merged_aliases = self._merge_list(c_data.get("aliases", []), incoming.get("aliases", []))
                    if merged_aliases != (c_data.get("aliases", []) or []):
                        updates["aliases"] = merged_aliases
                    merged_relationships = self._merge_dict(c_data.get("relationships", {}), incoming.get("relationships", {}))
                    if merged_relationships != (c_data.get("relationships", {}) or {}):
                        updates["relationships"] = merged_relationships
                    for field in (
                        "description", "appearance", "personality", "desire", "deep_need", "arc",
                        "role", "faction", "lifecycle_status", "narrative_activity", "role_importance",
                    ):
                        if incoming.get(field) not in (None, "", "unknown") and not c_data.get(field):
                            updates[field] = incoming[field]
                    chapter = int(observation.chapter_number or 0)
                    if chapter > 0:
                        previous_first = c_data.get("first_seen_chapter")
                        updates["first_seen_chapter"] = min(chapter, int(previous_first)) if previous_first else chapter
                        updates["last_seen_chapter"] = max(chapter, int(c_data.get("last_seen_chapter") or 0))
                        updates["last_mentioned_chapter"] = max(chapter, int(c_data.get("last_mentioned_chapter") or 0))
                    if updates and char_id:
                        await core_service.update_character_with_db(db, pid, char_id, updates)
                    observation.core_entity_id = char_id
                    _mark_promoted(observation)
                    return True

                incoming["first_seen_chapter"] = observation.chapter_number
                incoming["last_seen_chapter"] = observation.chapter_number
                incoming["last_mentioned_chapter"] = observation.chapter_number
                result = await core_service.create_character_with_db(
                    db, pid, incoming,
                )
                char_id = result.id if hasattr(result, "id") else str(result.get("id", ""))
                observation.core_entity_id = self._core_entity_id(char_id)
                _mark_promoted(observation)
                return True

            elif entity_type == "location":
                incoming = self._build_location_data(name, description, payload, observation.chapter_number)
                existing_locs = await core_service.list_locations_with_db(db, pid)
                for loc in existing_locs:
                    if normalize_entity_name(loc.get("name", "")) == normalize_entity_name(name):
                        loc_id = self._core_entity_id(loc.get("id"))
                        updates = {}
                        for field in ("description", "atmosphere", "parent_location", "location_type", "function"):
                            if incoming.get(field) and not loc.get(field):
                                updates[field] = incoming[field]
                        merged_relations = self._merge_dict(loc.get("spatial_relations", {}), incoming.get("spatial_relations", {}))
                        if merged_relations != (loc.get("spatial_relations", {}) or {}):
                            updates["spatial_relations"] = merged_relations
                        merged_rules = self._merge_list(loc.get("rules", []), incoming.get("rules", []))
                        if merged_rules != (loc.get("rules", []) or []):
                            updates["rules"] = merged_rules
                        if updates and loc_id:
                            await core_service.update_location_with_db(db, pid, loc_id, updates)
                        observation.core_entity_id = loc_id
                        _mark_promoted(observation)
                        return True

                result = await core_service.add_location_with_db(
                    db, pid, incoming,
                )
                loc_id = result.get("id", "") if isinstance(result, dict) else str(result)
                observation.core_entity_id = self._core_entity_id(loc_id)
                _mark_promoted(observation)
                return True

            elif entity_type == "foreshadowing":
                from app.services.foreshadowing_upsert_service import ForeshadowingUpsertService

                updates = await ForeshadowingUpsertService().upsert_from_observation(
                    db,
                    pid,
                    observation.chapter_number,
                    [{
                        **payload,
                        "name": name,
                        "description": description,
                        "operation": observation.operation,
                        "evidence_text": observation.evidence_text,
                        "confidence": max(float(observation.confidence or 0.0), 1.0 if force else 0.0),
                        "source_observation_id": str(observation.id),
                        "source_fingerprint": observation.fingerprint,
                        "core_entity_id": observation.core_entity_id,
                    }],
                )
                if not updates:
                    return False
                fs_id = updates[0].get("id", "")
                observation.core_entity_id = self._core_entity_id(fs_id)
                _mark_promoted(observation)
                return True

            elif entity_type == "world_rule":
                # 方案 22 B3：自动晋升的规则默认 priority="minor"，
                # 重大规则（critical/high）需人工提升，不自动晋升。
                incoming_priority = payload.get("priority", "minor")
                if incoming_priority in ("critical", "high") and not force:
                    logger.info(
                        "[WPS] world_rule '%s' priority=%s requires manual promotion, skip auto-promote",
                        name, incoming_priority,
                    )
                    return False

                existing_rules = await core_service.list_world_rules_with_db(db, pid)
                for r in existing_rules:
                    if normalize_entity_name(r.get("name", "")) == normalize_entity_name(name):
                        # 同名规则只更新描述和约束，不新增（避免规则爆炸）
                        updates: dict = {}
                        if description and not r.get("description"):
                            updates["description"] = description
                        incoming_constraints = payload.get("constraints", [])
                        if incoming_constraints:
                            merged_constraints = self._merge_list(
                                r.get("constraints", []), incoming_constraints,
                            )
                            if merged_constraints != (r.get("constraints", []) or []):
                                updates["constraints"] = merged_constraints
                        if updates:
                            await core_service.update_world_rule_with_db(
                                db, pid, r.get("id"), updates,
                            )
                        observation.core_entity_id = self._core_entity_id(r.get("id"))
                        _mark_promoted(observation)
                        return True

                result = await core_service.create_world_rule_with_db(
                    db, pid, {
                        "name": name,
                        "description": description,
                        "category": payload.get("category", "inferred"),
                        "priority": "minor",  # 方案 22 B3：默认 minor
                        "constraints": payload.get("constraints", []),
                    },
                )
                rule_id = result.get("id", "") if isinstance(result, dict) else str(result)
                observation.core_entity_id = self._core_entity_id(rule_id)
                _mark_promoted(observation)
                return True

            # 方案 3 B3：物品自动提升到 Core 层
            elif entity_type == "item":
                incoming = self._build_item_data(name, description, payload, observation.chapter_number)
                existing_item = await core_service.get_item_by_name_with_db(db, pid, name)
                if existing_item:
                    item_id = existing_item.get("id")
                    updates = {}
                    for field in ("appearance", "function", "origin", "owner", "importance", "related_foreshadowing"):
                        if incoming.get(field) and not existing_item.get(field):
                            updates[field] = incoming[field]
                    merged_aliases = self._merge_list(existing_item.get("aliases", []), incoming.get("aliases", []))
                    if merged_aliases != (existing_item.get("aliases", []) or []):
                        updates["aliases"] = merged_aliases
                    if updates and item_id:
                        await core_service.update_item_with_db(db, pid, item_id, updates)
                    observation.core_entity_id = self._core_entity_id(item_id)
                    _mark_promoted(observation)
                    return True

                result = await core_service.create_item_with_db(db, pid, incoming)
                item_id = result.get("id", "") if isinstance(result, dict) else str(result)
                observation.core_entity_id = self._core_entity_id(item_id)
                _mark_promoted(observation)
                return True

        except Exception as e:
            logger.warning("[WPS] auto-promote failed for %s/%s: %s", entity_type, name, e)
            raise

        return False

    async def _sync_foreshadowing_from_observation(
        self,
        db: AsyncSession,
        project_id: uuid.UUID,
        chapter_number: int,
        foreshadowing_items: list[dict],
    ) -> list[dict]:
        from app.services.foreshadowing_upsert_service import ForeshadowingUpsertService
        fus = ForeshadowingUpsertService()
        return await fus.upsert_from_observation(
            db, project_id, chapter_number, foreshadowing_items,
        )
