import copy
import uuid
import logging
import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.db_models import Project
from app.services.story_plan_service import StoryPlanService
from app.services.text_coercion import to_entity_name, to_text
from app.utils.word_count import count_words

logger = logging.getLogger(__name__)


@dataclass
class CompileDiagnostic:
    code: str
    severity: str
    field_path: str
    source_path: str
    message: str
    repair_hint: str
    blocks_generation: bool

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "severity": self.severity,
            "field_path": self.field_path,
            "source_path": self.source_path,
            "message": self.message,
            "repair_hint": self.repair_hint,
            "blocks_generation": self.blocks_generation,
        }


@dataclass
class CompiledScenePackage:
    scene_id: str
    scene_index: int
    pov_character: str
    scene_beat: dict
    scene_contract: dict
    source_refs: list[str] = field(default_factory=list)
    story_state_snapshot: dict = field(default_factory=dict)
    word_budget: dict = field(default_factory=dict)
    diagnostics: list[CompileDiagnostic] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "scene_id": self.scene_id,
            "scene_index": self.scene_index,
            "pov_character": self.pov_character,
            "scene_beat": self.scene_beat,
            "scene_contract": self.scene_contract,
            "source_refs": self.source_refs,
            "story_state_snapshot": self.story_state_snapshot,
            "word_budget": self.word_budget,
            "diagnostics": [d.to_dict() for d in self.diagnostics],
        }


@dataclass
class CompiledChapterPackage:
    chapter_number: int
    chapter_spine: dict
    scene_packages: list[CompiledScenePackage]
    diagnostics: list[CompileDiagnostic] = field(default_factory=list)
    source_version: int = 0

    def to_dict(self) -> dict:
        return {
            "chapter_number": self.chapter_number,
            "chapter_spine": self.chapter_spine,
            "scene_packages": [sp.to_dict() for sp in self.scene_packages],
            "diagnostics": [d.to_dict() for d in self.diagnostics],
            "source_version": self.source_version,
        }

    @property
    def has_blocking(self) -> bool:
        return any(d.blocks_generation for d in self.diagnostics) or any(
            d.blocks_generation for sp in self.scene_packages for d in sp.diagnostics
        )

    @property
    def blocking_diagnostics(self) -> list[CompileDiagnostic]:
        chapter_blocking = [d for d in self.diagnostics if d.blocks_generation]
        scene_blocking = [d for sp in self.scene_packages for d in sp.diagnostics if d.blocks_generation]
        return chapter_blocking + scene_blocking


class GenerationOutlineAdapter:

    def __init__(self):
        self._plan_service = StoryPlanService()

    @staticmethod
    def _as_list(value) -> list:
        if value in (None, ""):
            return []
        if isinstance(value, list):
            return value
        if isinstance(value, (tuple, set)):
            return list(value)
        return [value]

    @staticmethod
    def _thread_narrative_instruction(thread_id: str, thread: dict, detail: str) -> str:
        if detail:
            return to_text(detail)
        name = to_text(thread.get("name", ""))
        looks_internal = not name or name == str(thread_id) or bool(
            re.fullmatch(r"(thread|clue|line)[_-]?\d+", name, flags=re.IGNORECASE)
        )
        mojibake_markers = ("Ã", "Â", "â", "ä¸", "å", "ç", "è")
        looks_mangled = "�" in name or sum(marker in name for marker in mojibake_markers) >= 2
        if looks_internal or looks_mangled:
            return "围绕本场景目标自然埋设一处读者可感知的线索，保持克制，不直接解释其含义"
        return name

    @classmethod
    async def _count_written_chars(cls, project_id: uuid.UUID, chapter_number: int, db: AsyncSession) -> int:
        """Count committed chapter text in the canonical SQLAlchemy store."""
        try:
            from sqlalchemy import select
            from app.db.db_models import Chapter as DbChapter

            stmt = (
                select(DbChapter.content)
                .where(DbChapter.project_id == project_id)
                .where(DbChapter.chapter_number < chapter_number)
                .where(DbChapter.status.in_(("committed", "completed", "accepted")))
            )
            result = await db.execute(stmt)
            return sum(count_words(row[0]) for row in result.all())
        except Exception as exc:
            logger.warning("[outline-compile] failed to query written word count: %s", exc)
            return 0

    async def get_chapter(
        self, project_id: uuid.UUID, chapter_number: int, db: AsyncSession
    ) -> dict | None:
        project = await db.get(Project, project_id)
        if not project:
            return None
        outline = project.outline_data or {}

        for ch in self._as_list(outline.get("chapter_spine")):
            if isinstance(ch, dict) and ch.get("chapter_number") == chapter_number:
                return dict(ch)

        for ch in self._as_list(outline.get("chapters")):
            if isinstance(ch, dict) and ch.get("chapter_number") == chapter_number:
                return dict(ch)
        return None

    def _chapter_from_outline(self, outline: dict, chapter_number: int) -> dict | None:
        for ch in self._as_list(outline.get("chapter_spine")):
            if isinstance(ch, dict) and ch.get("chapter_number") == chapter_number:
                return dict(ch)
        for ch in self._as_list(outline.get("chapters")):
            if isinstance(ch, dict) and ch.get("chapter_number") == chapter_number:
                return dict(ch)
        return None

    def _scene_beats_from_outline(self, outline: dict, chapter_number: int) -> list[dict]:
        ch_id = f"ch_{chapter_number:03d}"
        briefs = outline.get("scene_briefs") or {}
        brief = briefs.get(ch_id) if isinstance(briefs, dict) else None
        if isinstance(brief, dict) and isinstance(brief.get("scenes"), list):
            return [dict(scene) for scene in brief.get("scenes", []) if isinstance(scene, dict)]
        chapter = self._chapter_from_outline(outline, chapter_number)
        if chapter and isinstance(chapter.get("scenes"), list):
            return [dict(scene) for scene in chapter.get("scenes", []) if isinstance(scene, dict)]
        if chapter:
            return [self._fallback_scene_from_chapter(chapter)]
        return []

    async def get_scene_beats(
        self, project_id: uuid.UUID, chapter_number: int, db: AsyncSession
    ) -> list[dict]:
        project = await db.get(Project, project_id)
        if not project:
            return []
        outline = project.outline_data or {}
        ch_id = f"ch_{chapter_number:03d}"

        briefs = outline.get("scene_briefs") or {}
        brief = briefs.get(ch_id) if isinstance(briefs, dict) else None
        if isinstance(brief, dict) and isinstance(brief.get("scenes"), list):
            return [dict(scene) for scene in brief.get("scenes", []) if isinstance(scene, dict)]

        chapter = await self.get_chapter(project_id, chapter_number, db)
        if chapter and isinstance(chapter.get("scenes"), list):
            return [dict(scene) for scene in chapter.get("scenes", []) if isinstance(scene, dict)]

        if chapter:
            return [self._fallback_scene_from_chapter(chapter)]
        return []

    @staticmethod
    def _invalid_scene_brief_entries(outline: dict, chapter_number: int) -> list[str]:
        briefs = outline.get("scene_briefs") or {}
        if not isinstance(briefs, dict):
            return ["scene_briefs"]

        ch_id = f"ch_{chapter_number:03d}"
        brief = briefs.get(ch_id) or briefs.get(str(chapter_number))
        if brief is None:
            return []
        if not isinstance(brief, dict):
            return [f"scene_briefs.{ch_id}"]

        scenes = brief.get("scenes")
        if not isinstance(scenes, list):
            return [f"scene_briefs.{ch_id}.scenes"]

        invalid = []
        for index, scene in enumerate(scenes):
            if not isinstance(scene, dict) or scene.get("_invalid_scene_payload"):
                invalid.append(f"scene_briefs.{ch_id}.scenes[{index}]")
        return invalid

    async def build_scene_packages(
        self,
        project_id: uuid.UUID,
        chapter_number: int,
        story_state: dict,
        db: AsyncSession,
        pov_character: str | None = None,
    ) -> list[dict]:
        compiled = await self.compile_chapter(
            project_id, chapter_number, story_state, db, pov_character
        )
        return [sp.to_dict() for sp in compiled.scene_packages]

    async def build_scene_package(
        self,
        project_id: uuid.UUID,
        chapter_number: int,
        scene_number: int,
        story_state: dict,
        db: AsyncSession,
        pov_character: str | None = None,
    ) -> dict:
        compiled = await self.compile_chapter(
            project_id, chapter_number, story_state, db, pov_character
        )
        index = max(scene_number - 1, 0)
        if 0 <= index < len(compiled.scene_packages):
            return compiled.scene_packages[index].to_dict()
        return {
            "scene_index": index,
            "scene_beat": {},
            "chapter_number": chapter_number,
            "dependencies": [],
            "story_state_snapshot": story_state,
            "pov_character": pov_character or story_state.get("pov_character", ""),
        }

    async def compile_chapter(
        self,
        project_id: uuid.UUID,
        chapter_number: int,
        story_state: dict,
        db: AsyncSession,
        pov_character: str | None = None,
        chapter_baseline: dict | None = None,
        outline_override: dict | None = None,
    ) -> CompiledChapterPackage:
        project = await db.get(Project, project_id)
        if not project:
            return CompiledChapterPackage(
                chapter_number=chapter_number,
                chapter_spine={},
                scene_packages=[],
                diagnostics=[CompileDiagnostic(
                    code="project_not_found", severity="blocking",
                    field_path="", source_path="",
                    message="项目不存在", repair_hint="检查 project_id",
                    blocks_generation=True,
                )],
                source_version=0,
            )

        outline = copy.deepcopy(
            outline_override if outline_override is not None else (project.outline_data or {})
        )
        meta = outline.get("meta") or {}
        source_version = meta.get("version", 0) if isinstance(meta, dict) else 0

        invalid_scene_entries = self._invalid_scene_brief_entries(outline, chapter_number)
        if invalid_scene_entries:
            paths = ", ".join(invalid_scene_entries[:3])
            if len(invalid_scene_entries) > 3:
                paths += f" 等{len(invalid_scene_entries)}处"
            return CompiledChapterPackage(
                chapter_number=chapter_number,
                chapter_spine={},
                scene_packages=[],
                diagnostics=[CompileDiagnostic(
                    code="invalid_scene_brief_payload",
                    severity="blocking",
                    field_path=paths,
                    source_path="scene_briefs",
                    message=f"第{chapter_number}章场景大纲含非法序列化数据：{paths}",
                    repair_hint="请重新生成或重新保存该章 scene_briefs，确保 scenes 中每一项都是结构化对象",
                    blocks_generation=True,
                )],
                source_version=source_version,
            )

        chapter = self._chapter_from_outline(outline, chapter_number)
        scenes = self._scene_beats_from_outline(outline, chapter_number)
        corridors = self._get_corridors(outline, chapter_number)
        chapter_diagnostics: list[CompileDiagnostic] = []

        if not scenes and chapter:
            scenes = [self._fallback_scene_from_chapter(chapter)]
            chapter_diagnostics.append(CompileDiagnostic(
                code="fallback_scene", severity="warning",
                field_path="scene_packages", source_path="chapter_spine",
                message=f"第{chapter_number}章无scene_briefs，从chapter_spine降级生成",
                repair_hint="在scene_briefs中补充场景数据", blocks_generation=False,
            ))

        if not scenes:
            chapter_diagnostics.append(CompileDiagnostic(
                code="no_scenes", severity="blocking",
                field_path="scene_packages", source_path="scene_briefs/chapters",
                message=f"第{chapter_number}章无场景数据，无法编译",
                repair_hint="在大纲中添加场景数据", blocks_generation=True,
            ))
            return CompiledChapterPackage(
                chapter_number=chapter_number,
                chapter_spine=chapter or {},
                scene_packages=[],
                diagnostics=chapter_diagnostics,
                source_version=source_version,
            )

        words_already_written = await self._count_written_chars(project_id, chapter_number, db)

        compiled_scenes: list[CompiledScenePackage] = []
        for i, scene in enumerate(scenes):
            pov = scene.get("pov_character") or scene.get("pov") or pov_character
            if not pov and chapter:
                pov = chapter.get("pov_character")
            if not pov:
                pov = story_state.get("pov_character", "")

            scene_id = scene.get("scene_id", f"ch_{chapter_number:03d}_s{i+1}")
            scene_diagnostics: list[CompileDiagnostic] = []

            compiled_beat = self._compile_scene_beat(scene, chapter)
            source_refs = self._compile_source_refs(scene, chapter)
            outline_must_show, outline_forbidden, thread_clues, foreshadowing_ops = self._compile_thread_ops(
                scene, chapter, corridors, outline.get("thread_plan")
            )
            spatial_info = self._compile_spatial(scene, chapter)

            if spatial_info.get("current_location") == "未知" and spatial_info.get("destination_location"):
                scene_diagnostics.append(CompileDiagnostic(
                    code="blocking_spatial_required", severity="blocking",
                    field_path=f"scene_packages[{i}].current_location",
                    source_path=f"scene_briefs.ch_{chapter_number:03d}.scenes[{i}].location",
                    message=f"场景{scene_id}涉及移动但当前位置未知",
                    repair_hint="请在scene_briefs中补充location字段", blocks_generation=True,
                ))

            goal = compiled_beat.get("goal_text", "")
            conflict = compiled_beat.get("conflict_text", "")
            outline_outcome = compiled_beat.get("outcome_text", "")

            if not goal:
                scene_diagnostics.append(CompileDiagnostic(
                    code="blocking_core_missing", severity="blocking",
                    field_path=f"scene_packages[{i}].goal", source_path=f"scene_briefs.ch_{chapter_number:03d}.scenes[{i}].goal",
                    message=f"场景{scene_id}缺少goal",
                    repair_hint="请在scene_briefs中补充goal字段", blocks_generation=True,
                ))
            if not conflict:
                scene_diagnostics.append(CompileDiagnostic(
                    code="blocking_core_missing", severity="blocking",
                    field_path=f"scene_packages[{i}].conflict", source_path=f"scene_briefs.ch_{chapter_number:03d}.scenes[{i}].conflict",
                    message=f"场景{scene_id}缺少conflict",
                    repair_hint="请在scene_briefs中补充conflict字段或core_conflict", blocks_generation=True,
                ))

            if not outline_outcome:
                scene_diagnostics.append(CompileDiagnostic(
                    code="outcome_missing", severity="warning",
                    field_path=f"scene_packages[{i}].outline_outcome", source_path=f"scene_briefs.ch_{chapter_number:03d}.scenes[{i}].outcome",
                    message=f"场景{scene_id}缺少outline_outcome，主编需补齐ending_state",
                    repair_hint="请在scene_briefs中补充outcome或value_shift", blocks_generation=False,
                ))

            source_of_truth = {
                "goal": goal,
                "conflict": conflict,
                "outline_outcome": outline_outcome,
                "required_context_refs": source_refs,
                "must_show_outline": outline_must_show,
                "forbidden_outline": outline_forbidden,
                "foreshadowing_ops": foreshadowing_ops,
            }

            all_clues = self._compile_clues(scene) + thread_clues
            for clue_index, clue in enumerate(all_clues):
                if clue.get("description") and clue.get("source_actor") in ("", "未知"):
                    scene_diagnostics.append(CompileDiagnostic(
                        code="blocking_clue_no_source", severity="blocking",
                        field_path=f"scene_packages[{i}].clues[{clue_index}].source_actor",
                        source_path=f"scene_briefs.ch_{chapter_number:03d}.scenes[{i}].clues",
                        message=f"场景{scene_id}线索'{clue.get('description')}'缺少来源",
                        repair_hint="请为线索补充source_actor字段", blocks_generation=True,
                    ))

            editor_enrichment = {
                "ending_state": outline_outcome,
                "pov_lock": f"仅限{pov}的感知和思维，不得描写其他角色内心" if pov else "",
                "temporal_anchor": "故事开始" if (chapter_number == 1 and i == 0) else "接续上一场景结尾",
                "opening_state": "故事开始" if (chapter_number == 1 and i == 0) else "承接上一场景结尾",
                "forbidden_recap_events": [],
                "clues": all_clues,
                "foreshadowing_ops": foreshadowing_ops,
                "additional_must_show": [],
                "additional_forbidden": [],
            }
            editor_enrichment.update(spatial_info)

            scene_contract = {
                "scene_id": scene_id,
                "chapter_number": chapter_number,
                "scene_index": i,
                "pov_character": pov,
                "source_of_truth": source_of_truth,
                "editor_enrichment": editor_enrichment,
                "scene_provenance": {},
                "goal": goal,
                "conflict": conflict,
                "must_show": outline_must_show,
                "forbidden": outline_forbidden,
                "ending_state": outline_outcome,
                "pov_lock": editor_enrichment["pov_lock"],
                "temporal_anchor": editor_enrichment["temporal_anchor"],
                "opening_state": editor_enrichment["opening_state"],
                "current_location": spatial_info.get("current_location", "未知"),
                "destination_location": spatial_info.get("destination_location", ""),
                "distance_state": spatial_info.get("distance_state", ""),
                "location_anchor": spatial_info.get("location_anchor", "当前位置：未知；目的地：未知；距离：未知"),
                "forbidden_recap_events": [],
                "clues": all_clues,
            }

            from app.services.scene_truth_snapshot import compute_chapter_scene_budgets
            from app.services.scene_budget_service import SceneBudgetService

            chapter_function = ""
            complexity_scores = []
            if isinstance(chapter, dict):
                chapter_function = chapter.get("function", "")
            for s in scenes:
                if isinstance(s, dict):
                    budget_result = SceneBudgetService().calculate(s | (chapter or {}))
                    complexity_scores.append(budget_result.get("complexity_score", 0))
                else:
                    complexity_scores.append(0)

            outline_data = getattr(project, "outline_data", None) if project else None
            total_chapters = 20
            if isinstance(outline_data, dict):
                chapter_spine_list = outline_data.get("chapter_spine", [])
                if isinstance(chapter_spine_list, list) and len(chapter_spine_list) > 0:
                    total_chapters = len(chapter_spine_list)

            all_scene_budgets = compute_chapter_scene_budgets(
                book_target_words=getattr(project, "word_count_target", None) or 100000,
                total_chapters=total_chapters,
                scene_count=len(scenes),
                chapter_function=chapter_function,
                scene_complexity_scores=complexity_scores,
                words_already_written=words_already_written,
                current_chapter=chapter_number,
            )
            scene_word_budget = all_scene_budgets[i] if i < len(all_scene_budgets) else all_scene_budgets[0]
            scene_contract["word_budget"] = scene_word_budget

            compiled_scenes.append(CompiledScenePackage(
                scene_id=scene_id,
                scene_index=i,
                pov_character=pov,
                scene_beat=compiled_beat,
                scene_contract=scene_contract,
                source_refs=source_refs,
                story_state_snapshot=story_state,
                word_budget=scene_word_budget,
                diagnostics=scene_diagnostics,
            ))

        from app.services.outline_state_compatibility_validator import OutlineStateCompatibilityValidator
        validator = OutlineStateCompatibilityValidator()
        outline_chapter_info = {"chapter_number": chapter_number}

        effective_baseline = chapter_baseline
        if effective_baseline is None and db is not None:
            try:
                from app.db.db_models import ChapterSnapshot as PgSnapshot
                snap_q = await db.execute(
                    select(PgSnapshot).where(
                        PgSnapshot.project_id == project_id,
                        PgSnapshot.chapter_number == chapter_number - 1,
                        PgSnapshot.stale == False,
                    )
                )
                prev_snap = snap_q.scalar_one_or_none()
                if prev_snap and prev_snap.story_state:
                    effective_baseline = prev_snap.story_state
            except Exception:
                pass

        scene_contracts_for_check = [cs.scene_contract for cs in compiled_scenes]
        first_scene_contract = scene_contracts_for_check[0] if scene_contracts_for_check else {}
        conflicts = validator.validate(
            outline_chapter=outline_chapter_info,
            story_state=story_state,
            chapter_state={},
            scene_contracts=[first_scene_contract],
            chapter_baseline=effective_baseline,
        )
        for conflict in conflicts:
            if conflict.severity == "blocking":
                chapter_diagnostics.append(CompileDiagnostic(
                    code="outline_state_baseline_conflict",
                    severity="blocking",
                    field_path=conflict.field,
                    source_path="story_state",
                    message=f"大纲与当前状态冲突：{conflict.outline_value} vs {conflict.state_value}",
                    repair_hint="使用「按新大纲重建本章」重置章节基线",
                    blocks_generation=True,
                ))

        return CompiledChapterPackage(
            chapter_number=chapter_number,
            chapter_spine=chapter or {},
            scene_packages=compiled_scenes,
            diagnostics=chapter_diagnostics,
            source_version=source_version,
        )

    def _compile_scene_beat(self, scene: dict, chapter: dict | None) -> dict:
        goal_text = scene.get("goal", "")
        if isinstance(goal_text, dict):
            goal_text = goal_text.get("description", goal_text.get("text", str(goal_text)))
        elif not isinstance(goal_text, str):
            goal_text = str(goal_text) if goal_text else ""

        conflict_text = scene.get("conflict", "")
        if isinstance(conflict_text, dict):
            conflict_text = self._plan_service.get_conflict_text({"core_conflict": conflict_text}) if conflict_text else ""
        elif not isinstance(conflict_text, str):
            conflict_text = str(conflict_text) if conflict_text else ""
        if not conflict_text and chapter:
            conflict_text = self._plan_service.get_conflict_text(chapter)

        outcome_text = scene.get("outcome", "")
        if isinstance(outcome_text, dict):
            outcome_text = self._value_shift_text(outcome_text)
        elif not isinstance(outcome_text, str):
            outcome_text = str(outcome_text) if outcome_text else ""
        if not outcome_text and chapter:
            outcome_text = self._value_shift_text(chapter.get("value_shift", ""))

        info_release_text = scene.get("info_release", scene.get("info_released", ""))
        if isinstance(info_release_text, dict):
            info_release_text = info_release_text.get("description", info_release_text.get("text", str(info_release_text)))
        elif not isinstance(info_release_text, str):
            info_release_text = str(info_release_text) if info_release_text else ""

        hook_text = scene.get("hook", scene.get("cliffhanger", ""))
        if isinstance(hook_text, dict):
            hook_text = hook_text.get("description", hook_text.get("text", str(hook_text)))
        elif not isinstance(hook_text, str):
            hook_text = str(hook_text) if hook_text else ""

        return {
            "goal_text": goal_text or "",
            "conflict_text": conflict_text or "",
            "outcome_text": outcome_text or "",
            "info_release_text": info_release_text or "",
            "hook_text": hook_text or "",
            "type": scene.get("type", "narrative"),
            "beat_role": scene.get("beat_role", ""),
            "_raw": scene,
        }

    def _compile_source_refs(self, scene: dict, chapter: dict | None) -> list[str]:
        refs = [to_text(ref) for ref in self._as_list(scene.get("required_context_refs")) if ref]
        if chapter:
            for op in self._as_list(chapter.get("thread_ops")):
                if isinstance(op, dict):
                    tid = op.get("thread_id")
                    if tid and tid not in refs:
                        refs.append(tid)
            for dep in self._as_list(chapter.get("depends_on")):
                dep_text = to_text(dep)
                if dep_text and dep_text not in refs:
                    refs.append(dep_text)
        return refs

    def _compile_thread_ops(
        self, scene: dict, chapter: dict | None, corridors: dict,
        thread_plan: dict | None = None,
    ) -> tuple[list[str], list[str], list[dict], list[dict]]:
        must_show = [to_text(item) for item in self._as_list(scene.get("must_show")) if item]
        forbidden_value = scene.get("forbidden", []) or scene.get("must_not", [])
        forbidden = [to_text(item) for item in self._as_list(forbidden_value) if item]
        clues: list[dict] = []
        operations: list[dict] = []
        thread_map = {}
        if isinstance(thread_plan, dict):
            for thread in self._as_list(thread_plan.get("threads")):
                if not isinstance(thread, dict):
                    continue
                thread_id = thread.get("thread_id") or thread.get("name")
                if thread_id:
                    thread_map[str(thread_id)] = thread

        if chapter:
            for op in self._as_list(chapter.get("thread_ops")):
                if not isinstance(op, dict):
                    continue
                op_type = op.get("op", op.get("action", ""))
                tid = op.get("thread_id", op.get("name", ""))
                detail = op.get("detail", "")
                mode = op.get("mode", "")
                thread = thread_map.get(str(tid), {})
                thread_name = thread.get("name") or tid
                narrative_detail = self._thread_narrative_instruction(
                    str(tid),
                    thread,
                    detail
                    or thread.get("description")
                    or thread.get("summary")
                    or thread.get("content")
                    or "",
                )

                if op_type in {"plant", "reveal", "remind", "escalate", "abandon"} and tid:
                    operations.append({
                        "thread_id": tid,
                        "thread_name": thread_name,
                        "op": op_type,
                        "mode": mode or "natural",
                        "narrative_instruction": narrative_detail,
                    })

                if op_type == "reveal" and tid:
                    clues.append({
                        "description": f"揭示线索「{thread_name}」：{narrative_detail}",
                        "source_actor": op.get("source_actor") or "既有线索",
                        "placement_time": "本场景揭示",
                        "discovery_condition": "通过本场景行动自然揭示",
                    })

        corridor_avoid = self._as_list(corridors.get("must_avoid"))
        for item in corridor_avoid:
            item_text = to_text(item)
            if item_text and item_text not in forbidden:
                forbidden.append(item_text)

        corridor_constraints = self._as_list(corridors.get("constraints"))
        for item in corridor_constraints:
            item_text = to_text(item)
            if item_text and item_text not in must_show:
                must_show.append(item_text)

        return must_show, forbidden, clues, operations

    def _compile_spatial(self, scene: dict, chapter: dict | None) -> dict:
        location = scene.get("location", scene.get("setting", ""))
        if isinstance(location, dict):
            current_location = location.get("name", location.get("description", ""))
        elif isinstance(location, str):
            current_location = location
        else:
            current_location = to_entity_name(location)

        if not current_location and chapter:
            ch_loc = chapter.get("location", chapter.get("setting", ""))
            if isinstance(ch_loc, dict):
                current_location = ch_loc.get("name", ch_loc.get("description", ""))
            elif isinstance(ch_loc, str):
                current_location = ch_loc
            else:
                current_location = to_entity_name(ch_loc)

        destination = to_entity_name(scene.get("destination_location", ""))
        distance = to_text(scene.get("distance_state", ""))

        if not current_location:
            current_location = "未知"

        parts = []
        if current_location and current_location != "未知":
            parts.append(f"当前位置：{current_location}")
        if destination:
            parts.append(f"目的地：{destination}")
        if distance:
            parts.append(f"距离：{distance}")
        location_anchor = "；".join(parts) if parts else "当前位置：未知；目的地：未知；距离：未知"

        return {
            "current_location": current_location,
            "destination_location": destination,
            "distance_state": distance,
            "location_anchor": location_anchor,
        }

    def _compile_clues(self, scene: dict) -> list[dict]:
        clues = scene.get("clues", []) or []
        if isinstance(clues, str):
            clues = [{"description": clues, "source_actor": "未知", "placement_time": "未知", "discovery_condition": "未知"}]
        elif isinstance(clues, dict):
            clues = [clues]
        elif not isinstance(clues, list):
            clues = list(clues) if isinstance(clues, (tuple, set)) else [clues]
        compiled = []
        for clue in clues:
            if isinstance(clue, dict):
                compiled.append({
                    "description": clue.get("description", ""),
                    "source_actor": clue.get("source_actor", "未知"),
                    "placement_time": clue.get("placement_time", "未知"),
                    "discovery_condition": clue.get("discovery_condition", "未知"),
                })
            else:
                compiled.append({
                    "description": str(clue),
                    "source_actor": "未知",
                    "placement_time": "未知",
                    "discovery_condition": "未知",
                })
        return compiled

    def _get_corridors(self, outline: dict, chapter_number: int) -> dict:
        corridors = outline.get("generation_corridors", {}) or {}
        if isinstance(corridors, dict):
            ch_id = f"ch_{chapter_number:03d}"
            selected = corridors.get(ch_id, corridors)
            return selected if isinstance(selected, dict) else {}
        return {}

    def _fallback_scene_from_chapter(self, chapter: dict) -> dict:
        value_shift = chapter.get("value_shift", "")
        return {
            "type": "narrative",
            "goal": self._plan_service.get_conflict_text(chapter),
            "conflict": self._plan_service.get_conflict_text(chapter),
            "outcome": self._value_shift_text(value_shift),
            "hook": chapter.get("hook", ""),
            "required_context_refs": [
                op.get("thread_id")
                for op in self._as_list(chapter.get("thread_ops"))
                if isinstance(op, dict) and op.get("thread_id")
            ],
        }

    def _value_shift_text(self, value_shift) -> str:
        if not value_shift:
            return ""
        if isinstance(value_shift, str):
            return value_shift
        if isinstance(value_shift, dict):
            axis = value_shift.get("axis", "")
            from_value = value_shift.get("from", value_shift.get("from_value", ""))
            to_value = value_shift.get("to", value_shift.get("to_value", ""))
            if from_value or to_value:
                prefix = f"{axis}: " if axis else ""
                return f"{prefix}{from_value} -> {to_value}"
            return "; ".join(f"{k}: {v}" for k, v in value_shift.items() if v)
        return str(value_shift)
