import copy
import uuid
import logging
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.db.db_models import Chapter, Project
from app.services.foreshadowing_action_sanitizer import sanitize_foreshadowing_actions
from app.services.story_plan_completeness_service import ensure_complete_scene_briefs
from app.skills.outline_validation import chapter_sequence_diagnostics

logger = logging.getLogger(__name__)


class StoryPlanService:
    SUPPORTED_LAYERS = {
        "story_constitution",
        "macro_plan",
        "arc_plan",
        "thread_plan",
        "chapter_spine",
        "scene_briefs",
        "generation_corridors",
        "meta",
    }

    def _outline_copy(self, outline: dict | None) -> dict:
        return copy.deepcopy(outline or {})

    def _persist_outline(self, project: Project, outline: dict) -> None:
        project.outline_data = copy.deepcopy(outline)
        flag_modified(project, "outline_data")

    @staticmethod
    def _chapter_spine_write_guard(project: Project) -> dict | None:
        outline = project.outline_data or {}
        if outline.get("_frozen"):
            return {
                "error": "正式大纲已冻结，请先解冻或通过修订案修改章节。",
                "code": "outline_frozen",
                "status_code": 403,
            }
        if project.draft_outline:
            return {
                "error": "已有待确认的大纲草稿。请先确认或丢弃草稿，再修改正式大纲章节。",
                "code": "outline_draft_pending",
                "status_code": 409,
            }
        return None

    def _has_valid_plan_data(self, outline: dict) -> bool:
        for key in self.SUPPORTED_LAYERS:
            val = outline.get(key)
            if val is not None and val != "" and val != [] and val != {}:
                return True
        return False

    @staticmethod
    def _chapter_sequence_error(plan: dict) -> dict | None:
        diagnostics = chapter_sequence_diagnostics(plan)
        if diagnostics["valid"]:
            return None

        missing = diagnostics.get("missing", [])
        preview = ", ".join(str(number) for number in missing[:12])
        suffix = "等" if len(missing) > 12 else ""
        if missing:
            message = f"章节脊柱不连续，缺少第{preview}章{suffix}。请按第1章开始连续补齐后再保存。"
        elif diagnostics.get("duplicates"):
            message = "章节脊柱存在重复章节号，请先修正后再保存。"
        else:
            message = "章节脊柱包含无效章节号，必须从第1章开始使用连续正整数。"
        return {
            "error": message,
            "status_code": 422,
            "chapter_continuity": diagnostics,
        }

    @staticmethod
    def _chapter_count(plan: dict | None) -> int:
        spine = (plan or {}).get("chapter_spine")
        return len(spine) if isinstance(spine, list) else 0

    @staticmethod
    def _shift_chapter_number(value, pivot: int, operation: str) -> int | None:
        """Map a reference to an existing chapter across insert/delete."""
        if isinstance(value, bool):
            return None
        try:
            number = int(value)
        except (TypeError, ValueError):
            return None
        if operation == "insert":
            return number + 1 if number >= pivot else number
        if number == pivot:
            return None
        return number - 1 if number > pivot else number

    @classmethod
    def _remap_chapter_references(
        cls,
        value,
        pivot: int,
        operation: str,
        *,
        removed_chapter_id: str | None = None,
        field_name: str = "",
    ):
        """Remap numeric chapter references without rewriting stable chapter IDs."""
        if isinstance(value, dict):
            remapped = {}
            for key, item in value.items():
                if key == "chapter_range" and isinstance(item, list) and len(item) == 2:
                    try:
                        start, end = int(item[0]), int(item[1])
                    except (TypeError, ValueError):
                        remapped[key] = copy.deepcopy(item)
                        continue
                    if operation == "insert":
                        if pivot < start:
                            remapped[key] = [start + 1, end + 1]
                        elif start <= pivot <= end:
                            remapped[key] = [start, end + 1]
                        else:
                            remapped[key] = [start, end]
                    elif pivot < start:
                        remapped[key] = [start - 1, end - 1]
                    elif start <= pivot <= end:
                        remapped[key] = [start, end - 1] if end > start else []
                    else:
                        remapped[key] = [start, end]
                    continue
                remapped[key] = cls._remap_chapter_references(
                    item,
                    pivot,
                    operation,
                    removed_chapter_id=removed_chapter_id,
                    field_name=key,
                )
            return remapped
        if isinstance(value, list):
            if field_name == "depends_on" and removed_chapter_id:
                return [copy.deepcopy(item) for item in value if str(item) != removed_chapter_id]
            if field_name.endswith("_chapters"):
                mapped = [cls._shift_chapter_number(item, pivot, operation) for item in value]
                return list(dict.fromkeys(item for item in mapped if item is not None))
            return [
                cls._remap_chapter_references(
                    item,
                    pivot,
                    operation,
                    removed_chapter_id=removed_chapter_id,
                    field_name=field_name,
                )
                for item in value
            ]
        if field_name in {
            "chapter_number",
            "target_chapter",
            "source_chapter",
            "plant_chapter",
            "payoff_chapter",
            "reveal_chapter",
            "escalation_chapter",
        }:
            mapped = cls._shift_chapter_number(value, pivot, operation)
            return mapped
        return copy.deepcopy(value)

    @staticmethod
    def _sync_manual_chapter_target(plan: dict, chapter_count: int) -> None:
        meta = plan.get("meta") if isinstance(plan.get("meta"), dict) else {}
        if meta.get("generation_target_chapters") is not None:
            meta["generation_target_chapters"] = chapter_count
        job = meta.get("expansion_job")
        if isinstance(job, dict) and job.get("status") == "completed":
            job["target_chapters"] = chapter_count
            job["completed_chapters"] = chapter_count
            job["next_chapter"] = chapter_count + 1
            job["message"] = f"已完成 {chapter_count}/{chapter_count} 章（含后续手工调整）"
        plan["meta"] = meta
        macro = plan.get("macro_plan")
        if isinstance(macro, dict) and macro.get("target_chapters") is not None:
            macro["target_chapters"] = chapter_count

    @classmethod
    def _extend_tail_chapter_ranges(cls, value, previous_count: int):
        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                if key == "chapter_range" and isinstance(item, list) and len(item) == 2:
                    try:
                        start, end = int(item[0]), int(item[1])
                    except (TypeError, ValueError):
                        result[key] = copy.deepcopy(item)
                    else:
                        result[key] = [start, end + 1] if end == previous_count else [start, end]
                else:
                    result[key] = cls._extend_tail_chapter_ranges(item, previous_count)
            return result
        if isinstance(value, list):
            return [cls._extend_tail_chapter_ranges(item, previous_count) for item in value]
        return copy.deepcopy(value)

    @staticmethod
    def _declared_chapter_target(raw_data: dict | None, plan: dict | None) -> int | None:
        """Read an explicit generation target without inferring it from prose.

        A target is deliberately only authoritative when the caller supplied a
        machine-readable field.  This prevents a normal ten-chapter outline
        from being rejected merely because a chat message mentioned 500.
        """
        candidates = [
            (raw_data or {}).get("generation_target_chapters"),
            (raw_data or {}).get("requested_chapters"),
            (raw_data or {}).get("total_chapters"),
            ((raw_data or {}).get("meta") or {}).get("generation_target_chapters"),
            ((plan or {}).get("meta") or {}).get("generation_target_chapters"),
        ]
        for value in candidates:
            try:
                if value not in (None, ""):
                    target = int(value)
                    if target > 0:
                        return target
            except (TypeError, ValueError):
                continue
        return None

    async def get_story_plan(
        self, project_id: uuid.UUID, db: AsyncSession
    ) -> dict:
        project = await db.get(Project, project_id)
        if not project:
            return {"error": "项目不存在"}

        outline = self._outline_copy(project.outline_data)

        if self._has_valid_plan_data(outline):
            plan = self._extract_story_plan(outline)
        else:
            plan = self._migrate_from_legacy(outline)

        plan, _ = ensure_complete_scene_briefs(plan)
        return plan

    async def get_layer(
        self,
        project_id: uuid.UUID,
        layer_name: str,
        db: AsyncSession,
    ) -> dict | list | None:
        if layer_name not in self.SUPPORTED_LAYERS:
            return None
        plan = await self.get_story_plan(project_id, db)
        if "error" in plan:
            return None
        return plan.get(layer_name)

    @staticmethod
    def _state_effect_key(effect: dict) -> tuple[str, str] | None:
        entity = str(
            effect.get("entity_id")
            or effect.get("entity")
            or effect.get("character_id")
            or effect.get("character")
            or ""
        ).strip()
        field_name = str(effect.get("field") or effect.get("attribute") or "").strip()
        if not entity or not field_name:
            return None
        return entity, field_name

    @staticmethod
    def _state_effect_value(effect: dict, *names: str) -> str:
        for name in names:
            value = effect.get(name)
            if value not in (None, ""):
                return str(value).strip()
        return ""

    def validate_chapter_blueprint(self, chapter: dict, scenes: list[dict]) -> list[dict]:
        """Validate hard contradictions inside one canonical chapter blueprint.

        Natural-language prose remains advisory.  This validator only blocks on
        explicit structured state transitions, where the system can prove that
        the chapter goal and scene sequence disagree.
        """
        diagnostics: list[dict] = []

        if chapter.get("recovery_generated") is True or str(chapter.get("status") or "") == "needs_review":
            diagnostics.append({
                "code": "recovery_confirmation_required",
                "severity": "blocking",
                "field_path": "chapter.status",
                "message": "该章由连续性恢复生成，需细化占位内容并明确确认后才能生成",
                "blocks_generation": True,
            })

        placeholder_markers = (
            "待细化",
            "待作者补充",
            "尚待作者补充",
            "待补充",
            "恢复占位",
            "placeholder",
        )

        def _is_placeholder(value: object) -> bool:
            normalized = str(value or "").strip().lower()
            return bool(normalized) and any(marker in normalized for marker in placeholder_markers)

        for field_name in (
            "title",
            "core_conflict",
            "conflict_text",
            "value_shift",
            "hook",
            "pov_character",
        ):
            if _is_placeholder(chapter.get(field_name)):
                diagnostics.append({
                    "code": "recovery_placeholder_present",
                    "severity": "blocking",
                    "field_path": f"chapter.{field_name}",
                    "message": f"章节字段 {field_name} 仍是恢复占位内容，请先细化",
                    "blocks_generation": True,
                })

        for index, scene in enumerate(scenes):
            if not isinstance(scene, dict):
                diagnostics.append({
                    "code": "invalid_scene",
                    "severity": "blocking",
                    "field_path": f"scenes[{index}]",
                    "message": f"场景 {index + 1} 不是有效的结构化场景",
                    "blocks_generation": True,
                })
                continue
            if not str(scene.get("goal") or "").strip():
                diagnostics.append({
                    "code": "scene_goal_missing",
                    "severity": "blocking",
                    "field_path": f"scenes[{index}].goal",
                    "message": f"场景 {index + 1} 缺少目标",
                    "blocks_generation": True,
                })
            if not str(scene.get("conflict") or "").strip():
                diagnostics.append({
                    "code": "scene_conflict_missing",
                    "severity": "blocking",
                    "field_path": f"scenes[{index}].conflict",
                    "message": f"场景 {index + 1} 缺少冲突",
                    "blocks_generation": True,
                })
            for field_name in ("goal", "conflict", "outcome", "result", "info_release", "hook"):
                if _is_placeholder(scene.get(field_name)):
                    diagnostics.append({
                        "code": "recovery_placeholder_present",
                        "severity": "blocking",
                        "field_path": f"scenes[{index}].{field_name}",
                        "message": f"场景 {index + 1} 的 {field_name} 仍是恢复占位内容，请先细化",
                        "blocks_generation": True,
                    })

        chapter_effects: dict[tuple[str, str], str] = {}
        for effect in chapter.get("state_effects_expected", []) or []:
            if not isinstance(effect, dict):
                continue
            key = self._state_effect_key(effect)
            after = self._state_effect_value(effect, "after", "to", "value", "new_value")
            if key and after:
                chapter_effects[key] = after

        scene_final_effects: dict[tuple[str, str], tuple[str, int]] = {}
        continuity: dict[tuple[str, str], str] = {}
        for index, scene in enumerate(scenes):
            if not isinstance(scene, dict):
                continue
            raw_effects = (
                scene.get("state_effects")
                or scene.get("state_changes")
                or []
            )
            if not isinstance(raw_effects, list):
                continue
            for effect in raw_effects:
                if not isinstance(effect, dict):
                    continue
                key = self._state_effect_key(effect)
                if not key:
                    continue
                before = self._state_effect_value(effect, "before", "from", "old_value")
                after = self._state_effect_value(effect, "after", "to", "value", "new_value")
                previous_after = continuity.get(key, "")
                if before and previous_after and before != previous_after:
                    diagnostics.append({
                        "code": "scene_state_continuity_conflict",
                        "severity": "blocking",
                        "field_path": f"scenes[{index}].state_effects",
                        "message": (
                            f"场景 {index + 1} 中 {key[0]} 的 {key[1]} 起始状态为“{before}”，"
                            f"但上一场景结束状态为“{previous_after}”"
                        ),
                        "blocks_generation": True,
                    })
                if after:
                    continuity[key] = after
                    scene_final_effects[key] = (after, index)

        for key, expected_after in chapter_effects.items():
            scene_effect = scene_final_effects.get(key)
            if scene_effect and scene_effect[0] != expected_after:
                diagnostics.append({
                    "code": "chapter_scene_state_conflict",
                    "severity": "blocking",
                    "field_path": f"scenes[{scene_effect[1]}].state_effects",
                    "message": (
                        f"本章要求 {key[0]} 的 {key[1]} 最终变为“{expected_after}”，"
                        f"但场景序列最终写成“{scene_effect[0]}”"
                    ),
                    "blocks_generation": True,
                })

        return diagnostics

    async def get_chapter_blueprint(
        self,
        project_id: uuid.UUID,
        chapter_number: int,
        db: AsyncSession,
    ) -> dict:
        project = await db.get(Project, project_id)
        if not project:
            return {"error": "项目不存在"}

        raw_outline = self._outline_copy(project.outline_data)
        plan = await self.get_story_plan(project_id, db)
        chapter = self.get_chapter_from_spine(plan.get("chapter_spine", []), chapter_number)
        if not chapter:
            return {"error": f"第 {chapter_number} 章不存在于章节脊柱"}

        chapter_id = str(chapter.get("chapter_id") or f"ch_{chapter_number:03d}")
        briefs = plan.get("scene_briefs", {}) if isinstance(plan.get("scene_briefs"), dict) else {}
        brief = copy.deepcopy(briefs.get(chapter_id) or {})
        scenes = brief.get("scenes") if isinstance(brief.get("scenes"), list) else chapter.get("scenes", [])
        scenes = copy.deepcopy(scenes if isinstance(scenes, list) else [])

        raw_briefs = raw_outline.get("scene_briefs", {})
        raw_brief = raw_briefs.get(chapter_id) if isinstance(raw_briefs, dict) else None
        persisted = bool(isinstance(raw_brief, dict) and isinstance(raw_brief.get("scenes"), list))
        meta = plan.get("meta", {}) if isinstance(plan.get("meta"), dict) else {}
        frozen_layers = list(meta.get("frozen_layers", []) or [])
        frozen = bool(
            raw_outline.get("_frozen")
            or "chapter_spine" in frozen_layers
            or "scene_briefs" in frozen_layers
        )

        chapter_copy = copy.deepcopy(chapter)
        chapter_copy["chapter_id"] = chapter_id
        chapter_copy["chapter_number"] = chapter_number
        chapter_copy["scenes"] = copy.deepcopy(scenes)
        brief.update({
            "chapter_id": chapter_id,
            "version": int(brief.get("version", 1) or 1),
            "source": brief.get("source", "auto_completed"),
            "scenes": copy.deepcopy(scenes),
        })

        return {
            "chapter_number": chapter_number,
            "chapter": chapter_copy,
            "scene_brief": brief,
            "scenes": scenes,
            "revision": int(project.outline_version or 0),
            "plan_version": int(meta.get("version", 1) or 1),
            "scene_brief_version": int(brief.get("version", 1) or 1),
            "persisted": persisted,
            "frozen": frozen,
            "frozen_layers": frozen_layers,
            "diagnostics": self.validate_chapter_blueprint(chapter_copy, scenes),
        }

    async def save_chapter_blueprint(
        self,
        project_id: uuid.UUID,
        chapter_number: int,
        chapter_updates: dict,
        scenes: list[dict],
        expected_revision: int | None,
        db: AsyncSession,
        confirm_recovery: bool = False,
    ) -> dict:
        project = await db.get(Project, project_id)
        if not project:
            return {"error": "项目不存在", "status_code": 404}

        current_revision = int(project.outline_version or 0)
        if expected_revision is not None and int(expected_revision) != current_revision:
            return {
                "error": "本章蓝图已被其他修改更新，请重新加载后再保存",
                "status_code": 409,
                "current_revision": current_revision,
            }

        outline = self._outline_copy(project.outline_data)
        plan = await self.get_story_plan(project_id, db)
        meta = plan.get("meta", {}) if isinstance(plan.get("meta"), dict) else {}
        frozen_layers = list(meta.get("frozen_layers", []) or [])
        if outline.get("_frozen") or "chapter_spine" in frozen_layers or "scene_briefs" in frozen_layers:
            return {
                "error": "本章蓝图所在的大纲层已冻结，需通过修订案修改",
                "status_code": 409,
                "current_revision": current_revision,
            }

        spine = copy.deepcopy(plan.get("chapter_spine", []))
        current_chapter = self.get_chapter_from_spine(spine, chapter_number)
        if not current_chapter:
            current_chapter = {
                "chapter_id": f"ch_{chapter_number:03d}",
                "chapter_number": chapter_number,
            }
            spine.append(current_chapter)

        safe_updates = copy.deepcopy(chapter_updates or {})
        safe_updates.pop("chapter_number", None)
        safe_updates.pop("chapter_id", None)
        safe_updates.pop("scenes", None)
        current_chapter.update(safe_updates)
        chapter_id = str(current_chapter.get("chapter_id") or f"ch_{chapter_number:03d}")
        current_chapter["chapter_id"] = chapter_id
        current_chapter["chapter_number"] = chapter_number

        normalized_scenes: list[dict] = []
        for index, scene in enumerate(scenes or []):
            if not isinstance(scene, dict):
                normalized_scenes.append({"_invalid_scene_payload": str(scene)})
                continue
            normalized = copy.deepcopy(scene)
            normalized.setdefault("scene_id", f"{chapter_id}_s{index + 1}")
            normalized.setdefault("required_context_refs", [])
            normalized_scenes.append(normalized)
        current_chapter["scenes"] = copy.deepcopy(normalized_scenes)
        if confirm_recovery:
            blocking = [
                item
                for item in self.validate_chapter_blueprint(current_chapter, normalized_scenes)
                if item.get("blocks_generation")
                and item.get("code") != "recovery_confirmation_required"
            ]
            if blocking:
                return {
                    "error": "恢复章仍有未细化内容：" + "；".join(
                        str(item.get("message") or "蓝图不完整") for item in blocking
                    ),
                    "status_code": 422,
                    "current_revision": current_revision,
                }
            current_chapter.pop("recovery_generated", None)
            if str(current_chapter.get("status") or "") == "needs_review":
                current_chapter["status"] = "planned"
        spine.sort(key=lambda item: item.get("chapter_number", 0) if isinstance(item, dict) else 0)

        briefs = copy.deepcopy(plan.get("scene_briefs", {}))
        briefs = briefs if isinstance(briefs, dict) else {}
        current_brief = copy.deepcopy(briefs.get(chapter_id) or {})
        current_brief.update({
            "chapter_id": chapter_id,
            "version": int(current_brief.get("version", 0) or 0) + 1,
            "source": "user_edited",
            "expires_when": "",
            "expires_scope": current_brief.get("expires_scope", "current"),
            "scenes": copy.deepcopy(normalized_scenes),
        })
        briefs[chapter_id] = current_brief

        plan["chapter_spine"] = spine
        plan["scene_briefs"] = briefs
        sequence_error = self._chapter_sequence_error(plan)
        if sequence_error:
            return sequence_error
        meta["version"] = int(meta.get("version", 1) or 1) + 1
        plan["meta"] = meta
        outline = self._merge_plan_into_outline(
            outline,
            plan,
            force_layers={"chapter_spine", "scene_briefs", "meta"},
        )
        revision_result = await db.execute(
            update(Project)
            .where(
                Project.id == project_id,
                Project.outline_version == current_revision,
            )
            .values(outline_version=current_revision + 1)
        )
        if revision_result.rowcount != 1:
            await db.rollback()
            return {
                "error": "本章蓝图已被其他修改更新，请重新加载后再保存",
                "status_code": 409,
                "current_revision": int((await db.get(Project, project_id)).outline_version or 0),
            }

        self._persist_outline(project, outline)
        project.outline_version = current_revision + 1
        project.updated_at = datetime.now(timezone.utc)
        await db.flush()

        return {
            "chapter_number": chapter_number,
            "chapter": copy.deepcopy(current_chapter),
            "scene_brief": copy.deepcopy(current_brief),
            "scenes": copy.deepcopy(normalized_scenes),
            "revision": int(project.outline_version),
            "plan_version": int(meta["version"]),
            "scene_brief_version": int(current_brief["version"]),
            "persisted": True,
            "frozen": False,
            "frozen_layers": frozen_layers,
            "diagnostics": self.validate_chapter_blueprint(current_chapter, normalized_scenes),
        }

    async def update_layer(
        self,
        project_id: uuid.UUID,
        layer_name: str,
        data: dict | list,
        db: AsyncSession,
    ) -> dict:
        project = await db.get(Project, project_id)
        if not project:
            return {"error": "项目不存在"}
        if layer_name not in self.SUPPORTED_LAYERS:
            return {"error": f"不支持的 Story Plan 层级: {layer_name}"}

        outline = self._outline_copy(project.outline_data)
        plan = await self.get_story_plan(project_id, db)

        meta = plan.get("meta", {})
        frozen_layers = meta.get("frozen_layers", [])
        if layer_name in frozen_layers:
            return {"error": f"层级 {layer_name} 已冻结，需提交修订案修改"}

        plan[layer_name] = data
        if layer_name == "chapter_spine":
            sequence_error = self._chapter_sequence_error(plan)
            if sequence_error:
                return sequence_error
        meta["version"] = meta.get("version", 1) + 1
        plan["meta"] = meta

        outline = self._merge_plan_into_outline(outline, plan, force_layers={layer_name})
        self._persist_outline(project, outline)
        project.outline_version = (project.outline_version or 0) + 1
        project.updated_at = datetime.now(timezone.utc)
        await db.flush()

        return {"layer": layer_name, "version": meta["version"]}

    async def update_chapter_spine_item(
        self,
        project_id: uuid.UUID,
        chapter_number: int,
        updates: dict,
        db: AsyncSession,
    ) -> dict:
        project = await db.get(Project, project_id)
        if not project:
            return {"error": "项目不存在"}

        guard_error = self._chapter_spine_write_guard(project)
        if guard_error:
            return guard_error

        outline = self._outline_copy(project.outline_data)
        plan = await self.get_story_plan(project_id, db)

        spine = plan.get("chapter_spine", [])
        found = False
        meta = plan.get("meta", {})
        frozen_layers = meta.get("frozen_layers", [])
        if "chapter_spine" in frozen_layers:
            return {"error": "chapter_spine 已冻结，需提交修订案修改"}
        for i, item in enumerate(spine):
            if item.get("chapter_number") == chapter_number:
                if isinstance(updates, dict):
                    safe_updates = copy.deepcopy(updates)
                    safe_updates.pop("chapter_number", None)
                    safe_updates.pop("chapter_id", None)
                    spine[i].update(safe_updates)
                found = True
                break

        if not found:
            return {
                "error": f"第 {chapter_number} 章不存在；如需新增，请使用插入章节操作。",
                "code": "chapter_not_found",
                "status_code": 404,
            }

        plan["chapter_spine"] = spine
        sequence_error = self._chapter_sequence_error(plan)
        if sequence_error:
            return sequence_error
        meta = plan.get("meta", {})
        meta["version"] = meta.get("version", 1) + 1
        plan["meta"] = meta

        outline = self._merge_plan_into_outline(outline, plan)
        self._persist_outline(project, outline)
        project.outline_index = {}
        flag_modified(project, "outline_index")
        project.outline_version = (project.outline_version or 0) + 1
        project.updated_at = datetime.now(timezone.utc)
        await db.flush()

        return {
            "chapter_number": chapter_number,
            "chapter": copy.deepcopy(next(
                item for item in spine if item.get("chapter_number") == chapter_number
            )),
            "updated": True,
            "actual_chapter_count": len(spine),
            "version": meta["version"],
        }

    async def append_chapter_spine_item(
        self,
        project_id: uuid.UUID,
        data: dict,
        db: AsyncSession,
    ) -> dict:
        """Append exactly one manual chapter to the contiguous outline tail."""
        project = await db.get(Project, project_id)
        if not project:
            return {"error": "项目不存在", "status_code": 404}

        guard_error = self._chapter_spine_write_guard(project)
        if guard_error:
            return guard_error

        outline = self._outline_copy(project.outline_data)
        plan = await self.get_story_plan(project_id, db)
        meta = plan.get("meta", {})
        if "chapter_spine" in meta.get("frozen_layers", []):
            return {
                "error": "章节脊柱已冻结，需提交修订案后再新增章节。",
                "code": "chapter_spine_frozen",
                "status_code": 403,
            }

        spine = plan.get("chapter_spine", [])
        if not isinstance(spine, list):
            return {"error": "chapter_spine 格式不正确", "status_code": 422}

        continuity = chapter_sequence_diagnostics(plan)
        if spine and not continuity["valid"]:
            return {
                "error": "当前正式大纲章节不连续。请先处理连续草稿，再追加新章节。",
                "code": "chapter_continuity_error",
                "status_code": 409,
                "chapter_continuity": continuity,
            }

        next_number = int(continuity.get("max_chapter") or 0) + 1
        previous_item = spine[-1] if spine and isinstance(spine[-1], dict) else None
        previous_id = str(previous_item.get("chapter_id")) if previous_item and previous_item.get("chapter_id") else None
        incoming = copy.deepcopy(data or {})
        title = str(incoming.get("title") or "").strip() or f"第{next_number}章（待规划）"
        summary = str(incoming.get("summary") or "").strip()
        pov_character = str(incoming.get("pov_character") or "").strip()

        chapter = {
            "chapter_id": f"ch_{next_number:03d}",
            "chapter_number": next_number,
            "title": title,
            "summary": summary,
            "function": "progression",
            "core_conflict": {
                "desire": "",
                "obstacle": "",
                "action": "",
                "turn": "",
            },
            "conflict_text": "",
            "value_shift": {"axis": "", "from": "", "to": ""},
            "pov_character": pov_character,
            "hook": "",
            "thread_ops": [],
            "state_effects_expected": [],
            "depends_on": [previous_id] if previous_id else [],
            "must_not": [],
            "status": "draft",
            "manual_created": True,
        }
        spine.append(chapter)
        plan["chapter_spine"] = spine
        for layer_name in ("macro_plan", "arc_plan"):
            if plan.get(layer_name) is not None:
                plan[layer_name] = self._extend_tail_chapter_ranges(plan[layer_name], next_number - 1)
        self._sync_manual_chapter_target(plan, len(spine))
        meta = plan.get("meta", {})
        meta["version"] = int(meta.get("version", 1) or 1) + 1
        meta["updated_at"] = datetime.now(timezone.utc).isoformat()
        plan["meta"] = meta

        outline = self._merge_plan_into_outline(
            outline,
            plan,
            force_layers={"chapter_spine", "macro_plan", "arc_plan", "meta"},
        )
        self._persist_outline(project, outline)
        project.outline_index = {}
        flag_modified(project, "outline_index")
        project.outline_version = (project.outline_version or 0) + 1
        project.updated_at = datetime.now(timezone.utc)
        await db.flush()

        return {
            "chapter_number": next_number,
            "chapter": chapter,
            "created": True,
            "shifted_chapters": 0,
            "actual_chapter_count": len(spine),
            "version": meta["version"],
        }

    async def insert_chapter_spine_item(
        self,
        project_id: uuid.UUID,
        chapter_number: int,
        data: dict,
        db: AsyncSession,
    ) -> dict:
        """Insert one outline chapter at any position and renumber later plans."""
        project = await db.get(Project, project_id)
        if not project:
            return {"error": "项目不存在", "status_code": 404}
        guard_error = self._chapter_spine_write_guard(project)
        if guard_error:
            return guard_error

        try:
            chapter_number = int(chapter_number)
        except (TypeError, ValueError):
            return {"error": "章节号必须是正整数", "status_code": 422}

        outline = self._outline_copy(project.outline_data)
        plan = await self.get_story_plan(project_id, db)
        meta = plan.get("meta", {}) if isinstance(plan.get("meta"), dict) else {}
        if "chapter_spine" in meta.get("frozen_layers", []):
            return {"error": "chapter_spine 已冻结，无法插入章节", "status_code": 403}
        spine = plan.get("chapter_spine", [])
        if not isinstance(spine, list):
            return {"error": "chapter_spine 格式不正确", "status_code": 422}
        continuity = chapter_sequence_diagnostics(plan)
        if spine and not continuity["valid"]:
            return {
                "error": "当前正式大纲章节不连续，请先修复后再插入章节。",
                "code": "chapter_continuity_error",
                "status_code": 409,
                "chapter_continuity": continuity,
            }
        old_count = len(spine)
        if chapter_number < 1 or chapter_number > old_count + 1:
            return {
                "error": f"插入位置必须在第1章至第{old_count + 1}章之间。",
                "code": "chapter_insert_out_of_range",
                "status_code": 422,
            }

        affected_written = (
            await db.execute(
                select(Chapter.chapter_number)
                .where(
                    Chapter.project_id == project_id,
                    Chapter.chapter_number >= chapter_number,
                )
                .order_by(Chapter.chapter_number)
                .limit(1)
            )
        ).scalar_one_or_none()
        if affected_written is not None:
            return {
                "error": (
                    f"不能在第 {chapter_number} 章插入规划：第 {affected_written} 章及其后已有正文，"
                    "自动重排会使正文与大纲错位。请先在正文侧处理受影响章节。"
                ),
                "code": "chapter_resequence_has_written_chapters",
                "status_code": 409,
                "first_affected_written_chapter": int(affected_written),
            }

        plan = self._remap_chapter_references(plan, chapter_number, "insert")
        if chapter_number == old_count + 1:
            for layer_name in ("macro_plan", "arc_plan"):
                if plan.get(layer_name) is not None:
                    plan[layer_name] = self._extend_tail_chapter_ranges(
                        plan[layer_name], old_count
                    )
        remapped_spine = plan.get("chapter_spine", [])
        incoming = copy.deepcopy(data or {}) if isinstance(data, dict) else {}
        incoming.pop("chapter_number", None)
        incoming.pop("chapter_id", None)
        incoming_scenes = incoming.pop("scenes", [])
        incoming_scenes = incoming_scenes if isinstance(incoming_scenes, list) else []
        previous = next(
            (item for item in remapped_spine if item.get("chapter_number") == chapter_number - 1),
            None,
        )
        chapter_id = f"ch_manual_{uuid.uuid4().hex[:12]}"
        chapter = {
            "chapter_id": chapter_id,
            "chapter_number": chapter_number,
            "title": str(incoming.pop("title", "") or f"第{chapter_number}章（待规划）"),
            "summary": str(incoming.pop("summary", "") or ""),
            "function": "progression",
            "core_conflict": {"desire": "", "obstacle": "", "action": "", "turn": ""},
            "conflict_text": "",
            "value_shift": {"axis": "", "from": "", "to": ""},
            "pov_character": "",
            "hook": "",
            "thread_ops": [],
            "state_effects_expected": [],
            "depends_on": [previous.get("chapter_id")] if previous and previous.get("chapter_id") else [],
            "must_not": [],
            "status": "draft",
            "manual_created": True,
        }
        chapter.update(incoming)
        chapter["scenes"] = copy.deepcopy(incoming_scenes)
        remapped_spine.append(chapter)
        remapped_spine.sort(key=lambda item: int(item.get("chapter_number") or 0))
        # The old chapter at the insertion point now follows the inserted one.
        shifted_next = next(
            (item for item in remapped_spine if item.get("chapter_number") == chapter_number + 1),
            None,
        )
        if isinstance(shifted_next, dict):
            dependencies = shifted_next.get("depends_on")
            dependencies = list(dependencies) if isinstance(dependencies, list) else []
            if chapter_id not in dependencies:
                dependencies.append(chapter_id)
            shifted_next["depends_on"] = dependencies
        plan["chapter_spine"] = remapped_spine
        briefs = plan.get("scene_briefs")
        briefs = briefs if isinstance(briefs, dict) else {}
        briefs[chapter_id] = {
            "chapter_id": chapter_id,
            "version": 1,
            "source": "user_created",
            "scenes": copy.deepcopy(incoming_scenes),
        }
        plan["scene_briefs"] = briefs
        self._sync_manual_chapter_target(plan, old_count + 1)
        meta = plan.get("meta", {})
        meta["version"] = int(meta.get("version", 1) or 1) + 1
        meta["updated_at"] = datetime.now(timezone.utc).isoformat()
        plan["meta"] = meta
        sequence_error = self._chapter_sequence_error(plan)
        if sequence_error:
            return sequence_error

        outline = self._merge_plan_into_outline(
            outline,
            plan,
            force_layers={"chapter_spine", "thread_plan", "macro_plan", "arc_plan", "scene_briefs", "meta"},
        )
        self._persist_outline(project, outline)
        project.outline_index = {}
        flag_modified(project, "outline_index")
        project.outline_version = (project.outline_version or 0) + 1
        project.updated_at = datetime.now(timezone.utc)
        await db.flush()
        return {
            "chapter_number": chapter_number,
            "chapter": copy.deepcopy(chapter),
            "created": True,
            "shifted_chapters": old_count - chapter_number + 1,
            "actual_chapter_count": old_count + 1,
            "version": meta["version"],
        }

    async def delete_chapter_spine_item(
        self,
        project_id: uuid.UUID,
        chapter_number: int,
        db: AsyncSession,
    ) -> dict:
        project = await db.get(Project, project_id)
        if not project:
            return {"error": "项目不存在"}

        guard_error = self._chapter_spine_write_guard(project)
        if guard_error:
            return guard_error

        outline = self._outline_copy(project.outline_data)
        plan = await self.get_story_plan(project_id, db)
        meta = plan.get("meta", {})
        frozen_layers = meta.get("frozen_layers", [])
        if "chapter_spine" in frozen_layers:
            return {"error": "chapter_spine 已冻结，无法删除章节"}

        spine = plan.get("chapter_spine", [])
        if not isinstance(spine, list):
            return {"error": "chapter_spine 格式不正确"}

        removed_chapter = next(
            (
                item for item in spine
                if isinstance(item, dict) and item.get("chapter_number") == chapter_number
            ),
            None,
        )
        if not removed_chapter:
            return {"error": f"第 {chapter_number} 章不存在"}

        affected_written = (
            await db.execute(
                select(Chapter.chapter_number)
                .where(
                    Chapter.project_id == project_id,
                    Chapter.chapter_number >= chapter_number,
                )
                .order_by(Chapter.chapter_number)
                .limit(1)
            )
        ).scalar_one_or_none()
        if affected_written is not None:
            exact_written_target = int(affected_written) == chapter_number
            return {
                "error": (
                    f"不能删除第 {chapter_number} 章规划：第 {affected_written} 章及其后已有正文，"
                    "自动重排会使正文与大纲错位。请先在正文侧处理受影响章节。"
                ),
                "code": (
                    "chapter_outline_has_written_chapter"
                    if exact_written_target
                    else "chapter_resequence_has_written_chapters"
                ),
                "status_code": 409,
                "first_affected_written_chapter": int(affected_written),
            }

        removed_id = str(removed_chapter.get("chapter_id") or f"ch_{chapter_number:03d}")
        removed_dependencies = (
            list(removed_chapter.get("depends_on"))
            if isinstance(removed_chapter.get("depends_on"), list)
            else []
        )
        plan["chapter_spine"] = [
            item for item in spine
            if not (isinstance(item, dict) and item.get("chapter_number") == chapter_number)
        ]
        briefs = plan.get("scene_briefs")
        if isinstance(briefs, dict):
            briefs.pop(removed_id, None)
        plan = self._remap_chapter_references(
            plan,
            chapter_number,
            "delete",
            removed_chapter_id=removed_id,
        )
        new_spine = plan.get("chapter_spine", [])
        shifted_next = next(
            (item for item in new_spine if item.get("chapter_number") == chapter_number),
            None,
        )
        if isinstance(shifted_next, dict) and removed_dependencies:
            dependencies = shifted_next.get("depends_on")
            dependencies = list(dependencies) if isinstance(dependencies, list) else []
            shifted_next["depends_on"] = list(dict.fromkeys(removed_dependencies + dependencies))
        plan["chapter_spine"] = new_spine
        self._sync_manual_chapter_target(plan, len(new_spine))
        sequence_error = self._chapter_sequence_error(plan)
        if sequence_error:
            return sequence_error
        meta = plan.get("meta", {})
        meta["version"] = int(meta.get("version", 1) or 1) + 1
        meta["updated_at"] = datetime.now(timezone.utc).isoformat()
        plan["meta"] = meta

        outline = self._merge_plan_into_outline(
            outline,
            plan,
            force_layers={"chapter_spine", "thread_plan", "macro_plan", "arc_plan", "scene_briefs", "meta"},
        )
        self._persist_outline(project, outline)
        project.outline_index = {}
        flag_modified(project, "outline_index")
        project.outline_version = (project.outline_version or 0) + 1
        project.updated_at = datetime.now(timezone.utc)
        await db.flush()

        return {
            "chapter_number": chapter_number,
            "deleted": True,
            "deleted_chapter_id": removed_id,
            "shifted_chapters": max(0, len(spine) - chapter_number),
            "actual_chapter_count": len(new_spine),
            "version": meta["version"],
        }

    async def freeze_layer(
        self,
        project_id: uuid.UUID,
        layer_name: str,
        db: AsyncSession,
    ) -> dict:
        project = await db.get(Project, project_id)
        if not project:
            return {"error": "项目不存在"}

        plan = await self.get_story_plan(project_id, db)
        meta = plan.get("meta", {})
        frozen = meta.get("frozen_layers", [])
        if layer_name not in frozen:
            frozen.append(layer_name)
        meta["frozen_layers"] = frozen
        meta["version"] = meta.get("version", 1) + 1
        plan["meta"] = meta

        outline = self._outline_copy(project.outline_data)
        outline = self._merge_plan_into_outline(outline, plan)
        self._persist_outline(project, outline)
        project.outline_version = (project.outline_version or 0) + 1
        project.updated_at = datetime.now(timezone.utc)
        await db.flush()

        return {"frozen_layers": frozen}

    async def unfreeze_layer(
        self,
        project_id: uuid.UUID,
        layer_name: str,
        db: AsyncSession,
    ) -> dict:
        project = await db.get(Project, project_id)
        if not project:
            return {"error": "项目不存在"}

        plan = await self.get_story_plan(project_id, db)
        meta = plan.get("meta", {})
        frozen = meta.get("frozen_layers", [])
        if layer_name in frozen:
            frozen.remove(layer_name)
        meta["frozen_layers"] = frozen
        meta["version"] = meta.get("version", 1) + 1
        plan["meta"] = meta

        outline = self._outline_copy(project.outline_data)
        outline = self._merge_plan_into_outline(outline, plan)
        self._persist_outline(project, outline)
        project.outline_version = (project.outline_version or 0) + 1
        project.updated_at = datetime.now(timezone.utc)
        await db.flush()

        return {"frozen_layers": frozen}

    async def merge_generated_plan(
        self, project_id: uuid.UUID, generated: dict, db: AsyncSession
    ) -> dict:
        project = await db.get(Project, project_id)
        if not project:
            return {"error": "项目不存在"}

        outline = self._outline_copy(project.outline_data)

        if "chapters" in generated and "chapter_spine" not in generated:
            plan = self._migrate_from_legacy(generated)
        else:
            plan = generated

        plan, completion = ensure_complete_scene_briefs(plan)
        sequence_error = self._chapter_sequence_error(plan)
        if sequence_error:
            return sequence_error
        outline = self._merge_plan_into_outline(outline, plan)
        self._persist_outline(project, outline)
        project.outline_version = (project.outline_version or 0) + 1
        project.updated_at = datetime.now(timezone.utc)
        await db.flush()

        return {"status": "merged", "scene_completion": completion}

    async def migrate_project(
        self, project_id: uuid.UUID, db: AsyncSession
    ) -> dict:
        project = await db.get(Project, project_id)
        if not project:
            return {"error": "项目不存在"}

        outline = self._outline_copy(project.outline_data)
        if all(key in outline for key in self.SUPPORTED_LAYERS):
            return {"status": "already_migrated"}

        plan = self._migrate_from_legacy(outline)
        outline = self._merge_plan_into_outline(outline, plan)
        self._persist_outline(project, outline)
        project.outline_version = (project.outline_version or 0) + 1
        project.updated_at = datetime.now(timezone.utc)
        await db.flush()

        return {"status": "migrated", "version": plan.get("meta", {}).get("version", 1)}

    async def save_outline_data(
        self,
        project_id: uuid.UUID,
        raw_data: dict,
        db: AsyncSession,
        *,
        mode: str = "patch",
        expected_chapter_count: int | None = None,
    ) -> dict:
        project = await db.get(Project, project_id)
        if not project:
            return {"error": "项目不存在"}

        outline = self._outline_copy(project.outline_data)

        if self._has_valid_plan_data(raw_data):
            incoming_plan = self._extract_story_plan(raw_data)
        else:
            incoming_plan = self._migrate_from_legacy(raw_data)

        if mode == "replace":
            merged_plan = self._replace_plan(incoming_plan)
        else:
            current_plan = await self.get_story_plan(project_id, db)
            merged_plan = self._patch_plan(current_plan, incoming_plan)

        merged_plan, completion = ensure_complete_scene_briefs(merged_plan)
        sequence_error = self._chapter_sequence_error(merged_plan)
        if sequence_error:
            return sequence_error
        actual_chapter_count = self._chapter_count(merged_plan)
        if expected_chapter_count is not None:
            try:
                expected = int(expected_chapter_count)
            except (TypeError, ValueError):
                return {
                    "error": "expected_chapter_count 必须是正整数",
                    "status_code": 422,
                    "code": "invalid_expected_chapter_count",
                }
            if expected < 1 or actual_chapter_count != expected:
                return {
                    "error": (
                        f"章节数量未完成：需要 {expected} 章，实际只有 "
                        f"{actual_chapter_count} 章；本次没有写入正式大纲。"
                    ),
                    "status_code": 422,
                    "code": "chapter_count_mismatch",
                    "expected_chapter_count": expected,
                    "actual_chapter_count": actual_chapter_count,
                }
        declared_target = self._declared_chapter_target(raw_data, merged_plan)
        if mode == "replace" and declared_target is not None and declared_target != actual_chapter_count:
            return {
                "error": (
                    f"声明目标为 {declared_target} 章，但实际只提供了 "
                    f"{actual_chapter_count} 章；不能把部分结果冒充全本大纲。"
                ),
                "status_code": 422,
                "code": "declared_chapter_count_mismatch",
                "expected_chapter_count": declared_target,
                "actual_chapter_count": actual_chapter_count,
            }
        outline = self._merge_plan_into_outline(outline, merged_plan, mode=mode)
        self._persist_outline(project, outline)
        project.outline_version = (project.outline_version or 0) + 1
        project.updated_at = datetime.now(timezone.utc)
        await db.flush()

        return {
            "status": "saved",
            "version": merged_plan.get("meta", {}).get("version", 1),
            "scene_completion": completion,
            "actual_chapter_count": actual_chapter_count,
            "chapter_range": [1, actual_chapter_count] if actual_chapter_count else [],
            "target_chapter_count": declared_target or expected_chapter_count,
            "complete": bool(actual_chapter_count and (
                declared_target is None or declared_target == actual_chapter_count
            )),
        }

    def _replace_plan(self, incoming: dict) -> dict:
        plan = {}
        for layer in self.SUPPORTED_LAYERS:
            plan[layer] = incoming.get(layer)
        meta = plan.get("meta") or {}
        meta["version"] = meta.get("version", 0) + 1
        plan["meta"] = meta
        return plan

    def _patch_plan(self, current: dict, incoming: dict) -> dict:
        merged = dict(current)

        for layer in self.SUPPORTED_LAYERS:
            incoming_val = incoming.get(layer)
            if incoming_val is not None and incoming_val != "" and incoming_val != [] and incoming_val != {}:
                if layer == "chapter_spine" and isinstance(incoming_val, list):
                    current_spine = merged.get("chapter_spine", [])
                    merged["chapter_spine"] = self._patch_spine(current_spine, incoming_val)
                elif layer == "scene_briefs" and isinstance(incoming_val, dict):
                    current_briefs = merged.get("scene_briefs", {})
                    current_briefs.update(incoming_val)
                    merged["scene_briefs"] = current_briefs
                elif layer == "thread_plan" and isinstance(incoming_val, dict):
                    current_tp = merged.get("thread_plan", {})
                    if not current_tp or not current_tp.get("threads"):
                        merged["thread_plan"] = incoming_val
                    else:
                        current_threads = {}
                        for t in current_tp.get("threads", []):
                            key = t.get("thread_id") or t.get("name") or id(t)
                            current_threads[key] = t
                        for t in incoming_val.get("threads", []):
                            key = t.get("thread_id") or t.get("name") or id(t)
                            current_threads[key] = t
                        merged["thread_plan"] = {"threads": list(current_threads.values())}
                else:
                    merged[layer] = incoming_val

        current_meta = merged.get("meta", {})
        current_meta["version"] = current_meta.get("version", 1) + 1
        merged["meta"] = current_meta

        return merged

    def _patch_spine(self, current: list, incoming: list) -> list:
        current_map = {item.get("chapter_number"): item for item in current if isinstance(item, dict)}
        for item in incoming:
            if not isinstance(item, dict):
                continue
            ch_num = item.get("chapter_number", 0)
            if ch_num in current_map:
                current_map[ch_num].update(item)
            else:
                current_map[ch_num] = item
        return sorted(current_map.values(), key=lambda x: x.get("chapter_number", 0))

    def _migrate_from_legacy(self, outline: dict) -> dict:
        chapters = outline.get("chapters", [])

        if not chapters and outline.get("action") in ("new_chapter", "modify_chapter"):
            ch_data = outline.get("chapter_data", {})
            ch_data.setdefault("chapter_number", outline.get("chapter_number", 0))
            chapters = [ch_data]

        existing_chapters = outline.get("chapters", [])
        if existing_chapters and chapters != existing_chapters:
            ch_map = {ch.get("chapter_number"): ch for ch in existing_chapters}
            for ch in chapters:
                ch_map[ch.get("chapter_number")] = ch
            chapters = sorted(ch_map.values(), key=lambda x: x.get("chapter_number", 0))

        macro = outline.get("macro_structure", {})

        spine = []
        scene_briefs = {}
        thread_ops_map = {}

        for ch in chapters:
            ch_num = ch.get("chapter_number", 0)
            ch_id = f"ch_{ch_num:03d}"

            main_conflict = ch.get("main_conflict", "") or ch.get("core_conflict", "")
            core_conflict = None
            conflict_text = main_conflict
            if isinstance(main_conflict, dict):
                core_conflict = main_conflict
                conflict_text = (
                    main_conflict.get("desire", "")
                    or main_conflict.get("obstacle", "")
                    or main_conflict.get("action", "")
                    or main_conflict.get("turn", "")
                    or main_conflict.get("description", "")
                )
                if not conflict_text:
                    conflict_text = str(main_conflict)
                else:
                    parts = [
                        main_conflict.get("desire", ""),
                        main_conflict.get("obstacle", ""),
                        main_conflict.get("action", ""),
                        main_conflict.get("turn", ""),
                    ]
                    non_empty = [p for p in parts if p]
                    if non_empty:
                        conflict_text = "，但".join(non_empty)
            elif main_conflict:
                conflict_text = main_conflict

            value_shift_raw = ch.get("value_shift") or ch.get("value_transformation", "")
            value_shift = None
            if isinstance(value_shift_raw, dict):
                value_shift = value_shift_raw
            elif value_shift_raw:
                value_shift = {"axis": "", "from": value_shift_raw, "to": ""}

            thread_actions = sanitize_foreshadowing_actions(
                ch.get("thread_ops", [])
            )
            thread_ops = []
            for fa in thread_actions:
                fa_name = fa.get("name") or fa.get("thread_id", "")
                fa_action = fa.get("action") or fa.get("operation") or fa.get("op", "plant")
                fa_mode = fa.get("mode", "subtle")
                fa_detail = fa.get("detail", "")
                thread_ops.append({
                    "thread_id": fa_name,
                    "op": fa_action,
                    "mode": fa_mode,
                })
                if fa_name not in thread_ops_map:
                    thread_ops_map[fa_name] = {
                        "plant": [],
                        "reveal": [],
                        "escalate": [],
                        "remind": [],
                    }
                if fa_action in thread_ops_map[fa_name]:
                    thread_ops_map[fa_name][fa_action].append(ch_num)

            scenes = ch.get("scenes", [])
            normalized_scenes = []
            for sc in scenes:
                ns = dict(sc) if isinstance(sc, dict) else {"description": sc}
                if "info_released" in ns and "info_release" not in ns:
                    ns["info_release"] = ns.pop("info_released")
                if "cliffhanger" in ns and "hook" not in ns:
                    ns["hook"] = ns.pop("cliffhanger")
                normalized_scenes.append(ns)
            if normalized_scenes:
                scene_briefs[ch_id] = {
                    "chapter_id": ch_id,
                    "version": 1,
                    "source": "legacy",
                    "expires_when": "chapter_spine_changed",
                    "expires_scope": "current",
                    "scenes": normalized_scenes,
                }

            ch_hook = ch.get("hook", "") or ch.get("cliffhanger", "")
            spine.append({
                "chapter_id": ch_id,
                "chapter_number": ch_num,
                "title": ch.get("title", ""),
                "node_id": ch.get("node_id", ""),
                "function": "setup",
                "external_event": "",
                "core_conflict": core_conflict,
                "conflict_text": conflict_text,
                "value_shift": value_shift,
                "pov_character": ch.get("pov_character", ""),
                "hook": ch_hook,
                "thread_ops": thread_ops,
                "state_effects_expected": [],
                "depends_on": [],
                "must_not": [],
                "scenes": normalized_scenes,
                "status": ch.get("status", "draft"),
            })

        thread_plan = {"threads": []}
        for name, ops in thread_ops_map.items():
            thread_plan["threads"].append({
                "thread_id": name,
                "name": name,
                "thread_type": "foreshadowing",
                "status": "active",
                "description": "",
                "plant_chapters": ops.get("plant", []),
                "escalation_chapters": ops.get("escalate", []),
                "reveal_chapters": ops.get("reveal", []),
                "depends_on_threads": [],
            })

        macro_plan = None
        if macro:
            volumes = []
            for act in macro.get("acts", []):
                act_num = act.get("act_number", 1)
                nodes = []
                for i, node in enumerate(act.get("nodes", [])):
                    nodes.append({
                        "node_id": node.get("node_id", f"act{act_num}_n{i+1}"),
                        "name": node.get("name", ""),
                        "beat_role": node.get("beat_role", ""),
                        "chapter_range": node.get("chapter_range", []),
                        "turning_point": node.get("key_turning_point", node.get("turning_point", "")),
                    })
                volumes.append({
                    "volume_id": f"v{act_num}",
                    "name": act.get("name", f"第{act_num}幕"),
                    "promise": "",
                    "chapter_range": act.get("chapter_range", []),
                    "value_from": "",
                    "value_to": "",
                    "nodes": nodes,
                })
            macro_plan = {
                "structure_model": "three_act",
                "volumes": volumes,
            }

        return {
            "story_constitution": None,
            "macro_plan": macro_plan,
            "arc_plan": None,
            "thread_plan": thread_plan,
            "chapter_spine": spine,
            "scene_briefs": scene_briefs,
            "generation_corridors": {},
            "meta": {
                "version": 1,
                "frozen_layers": [],
                "draft_active": False,
                "amendment_count": 0,
                "index_version": 0,
            },
        }

    def _extract_story_plan(self, outline: dict) -> dict:
        return {
            "story_constitution": outline.get("story_constitution"),
            "macro_plan": outline.get("macro_plan"),
            "arc_plan": outline.get("arc_plan"),
            "thread_plan": outline.get("thread_plan"),
            "chapter_spine": outline.get("chapter_spine", []),
            "scene_briefs": outline.get("scene_briefs", {}),
            "generation_corridors": outline.get("generation_corridors", {}),
            "meta": outline.get("meta", {
                "version": 1,
                "frozen_layers": [],
                "draft_active": False,
                "amendment_count": 0,
                "index_version": 0,
            }),
        }

    def _merge_plan_into_outline(self, outline: dict, plan: dict, *, mode: str = "patch", force_layers: set | None = None) -> dict:
        for layer in self.SUPPORTED_LAYERS:
            val = plan.get(layer)
            if mode == "replace":
                outline[layer] = val
            elif force_layers and layer in force_layers:
                outline[layer] = val
            elif val is not None and val != "" and val != [] and val != {}:
                outline[layer] = val

        spine_written = (
            mode == "replace"
            or (force_layers and "chapter_spine" in force_layers)
            or (plan.get("chapter_spine") is not None and plan.get("chapter_spine") != "" and plan.get("chapter_spine") != [])
        )
        spine = plan.get("chapter_spine", [])
        if spine:
            legacy_chapters = outline.get("chapters", [])
            legacy_map = {ch.get("chapter_number"): ch for ch in legacy_chapters}
            updated_chapters = []
            for item in spine:
                ch_num = item.get("chapter_number", 0)
                legacy = legacy_map.get(ch_num, {})
                updated = dict(legacy)
                updated["chapter_number"] = ch_num
                updated["title"] = item.get("title", updated.get("title", ""))
                if item.get("conflict_text"):
                    updated["main_conflict"] = item["conflict_text"]
                if item.get("core_conflict"):
                    updated["core_conflict"] = item["core_conflict"]
                if item.get("value_shift"):
                    updated["value_shift"] = item["value_shift"]
                if item.get("pov_character"):
                    updated["pov_character"] = item["pov_character"]
                updated.pop("foreshadowing_actions", None)
                updated.pop("foreshadowing_operations", None)
                updated.pop("legacy_payload", None)
                if item.get("node_id"):
                    updated["node_id"] = item["node_id"]
                updated_chapters.append(updated)
            outline["chapters"] = updated_chapters
        elif spine_written:
            outline["chapters"] = []

        return outline

    def get_chapter_from_spine(
        self, spine: list[dict], chapter_number: int
    ) -> dict | None:
        for item in spine:
            if item.get("chapter_number") == chapter_number:
                return item
        return None

    def get_conflict_text(self, chapter: dict) -> str:
        conflict_text = chapter.get("conflict_text", "")
        if conflict_text:
            return conflict_text

        core_conflict = chapter.get("core_conflict")
        if isinstance(core_conflict, dict):
            parts = [
                core_conflict.get("desire", ""),
                core_conflict.get("obstacle", ""),
                core_conflict.get("action", ""),
                core_conflict.get("turn", ""),
            ]
            return "，但".join(filter(None, parts))

        main_conflict = chapter.get("main_conflict", "")
        if isinstance(main_conflict, str):
            return main_conflict
        if isinstance(main_conflict, dict):
            return str(main_conflict)

        return ""

