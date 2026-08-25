import uuid
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.db_models import Project
from app.services.outline_index_service import OutlineIndexService
from app.services.memory_core import CoreMemoryService
from app.services.worldview_digest import WorldviewDigestService

logger = logging.getLogger(__name__)


class ForwardConstraintBuilder:

    def _get_chapter_data(self, outline_data: dict, chapter_number: int) -> dict | None:
        chapter_spine = outline_data.get("chapter_spine", [])
        if chapter_spine:
            for ch in chapter_spine:
                if ch.get("chapter_number") == chapter_number:
                    return ch
        chapters = outline_data.get("chapters", [])
        for ch in chapters:
            if ch.get("chapter_number") == chapter_number:
                return ch
        return None

    def _get_conflict_text(self, chapter: dict) -> str:
        conflict_text = chapter.get("conflict_text", "")
        if conflict_text:
            return conflict_text
        core_conflict = chapter.get("core_conflict", "")
        if core_conflict:
            if isinstance(core_conflict, dict):
                desire = core_conflict.get("desire", "")
                obstacle = core_conflict.get("obstacle", "")
                action = core_conflict.get("action", "")
                turn = core_conflict.get("turn", "")
                parts = []
                if desire:
                    parts.append(desire)
                if obstacle:
                    parts.append(f"但{obstacle}")
                if action:
                    parts.append(f"于是{action}")
                if turn:
                    parts.append(f"却{turn}")
                return "，".join(parts) if parts else ""
            return str(core_conflict)
        main_conflict = chapter.get("main_conflict", "")
        if main_conflict:
            return str(main_conflict)
        return ""

    def _get_value_shift_text(self, chapter: dict) -> str:
        value_shift = chapter.get("value_shift", "")
        if not value_shift:
            return ""
        if isinstance(value_shift, dict):
            axis = value_shift.get("axis", "")
            from_val = value_shift.get("from", "")
            to_val = value_shift.get("to", "")
            arrow = f"{from_val}→{to_val}" if from_val or to_val else ""
            if axis and arrow:
                return f"{axis}: {arrow}"
            return arrow or axis or ""
        return str(value_shift)

    def _read_thread_ops(self, chapter: dict) -> list[dict]:
        thread_ops = chapter.get("thread_ops", [])
        if not isinstance(thread_ops, list):
            return []
        return [op for op in thread_ops if isinstance(op, dict)]

    async def build_hard_constraints(
        self, project_id: str, chapter_number: int, db: AsyncSession = None
    ) -> str:
        pid = uuid.UUID(project_id)
        index_service = OutlineIndexService()
        worldview_service = WorldviewDigestService()

        project = await db.get(Project, pid)
        chapter = None
        if project and project.outline_data:
            chapter = self._get_chapter_data(project.outline_data, chapter_number)
        if not chapter:
            chapter = await index_service.get_chapter_detail(pid, chapter_number, db)
        if not chapter:
            return ""

        parts = ["## 硬约束（必须遵守）"]

        conflict = self._get_conflict_text(chapter)
        if conflict:
            parts.append(f"- 本章核心冲突：{conflict}")

        value_shift = self._get_value_shift_text(chapter)
        if value_shift:
            parts.append(f"- 本章价值转变：{value_shift}")

        thread_ops = self._read_thread_ops(chapter)
        thread_plants = [op for op in thread_ops if op.get("op") == "plant"]
        thread_reveals = [op for op in thread_ops if op.get("op") == "reveal"]
        plant_items = []
        reveal_items = []

        if plant_items:
            names = "、".join(f"「{f.get('name', '')}」" for f in plant_items)
            parts.append(f"- 本章必须埋设伏笔：{names}")

        if reveal_items:
            names = "、".join(f"「{f.get('name', '')}」" for f in reveal_items)
            parts.append(f"- 本章必须回收伏笔：{names}")

        if thread_plants:
            names = "、".join(f"「{op.get('thread_id', '')}」" for op in thread_plants)
            parts.append(f"- 本章必须埋线：{names}")

        if thread_reveals:
            names = "、".join(f"「{op.get('thread_id', '')}」" for op in thread_reveals)
            parts.append(f"- 本章必须回收线索：{names}")
        pov_character = chapter.get("pov_character", "")
        if pov_character:
            parts.append(f"- POV角色：{pov_character}")

        core_service = CoreMemoryService()
        rules = await core_service.list_world_rules(project_id)
        critical_rules = [r for r in rules if r.get("priority") == "critical"]
        if critical_rules:
            iron_names = "、".join(r.get("name", "") for r in critical_rules)
            parts.append(f"- 不可违反的世界观铁则：{iron_names}")

        return "\n".join(parts)

    async def build_corridor_constraints(
        self,
        project_id: str,
        chapter_number: int,
        db: AsyncSession = None,
        lookahead: int = 3,
    ) -> str:
        pid = uuid.UUID(project_id)
        index_service = OutlineIndexService()
        core_service = CoreMemoryService()

        project = await db.get(Project, pid)
        if not project or not project.outline_data:
            return ""

        outline = project.outline_data

        future_chapters = []
        chapter_spine = outline.get("chapter_spine", [])
        if chapter_spine:
            for ch in chapter_spine:
                ch_num = ch.get("chapter_number", 0)
                if chapter_number < ch_num <= chapter_number + lookahead:
                    future_chapters.append(ch)
        if not future_chapters:
            chapters = outline.get("chapters", [])
            for ch in chapters:
                ch_num = ch.get("chapter_number", 0)
                if chapter_number < ch_num <= chapter_number + lookahead:
                    future_chapters.append(ch)

        if not future_chapters:
            return ""

        parts = ["## 走廊约束（不得偏离）"]

        future_characters = set()
        future_conflicts = []
        future_reveals = []

        for ch in future_chapters:
            ch_num = ch.get("chapter_number", 0)

            pov = ch.get("pov_character", "")
            if pov:
                future_characters.add(pov)

            for scene in ch.get("scenes", []):
                for char in scene.get("characters", []):
                    if isinstance(char, str):
                        future_characters.add(char)
                    elif isinstance(char, dict):
                        future_characters.add(char.get("name", ""))

            conflict = self._get_conflict_text(ch)
            if conflict:
                future_conflicts.append((ch_num, conflict))

            for op in self._read_thread_ops(ch):
                if op.get("op") == "reveal":
                    future_reveals.append((ch_num, op.get("thread_id", "")))

        future_characters.discard("")

        if future_characters:
            char_list = "、".join(sorted(future_characters))
            parts.append(
                f"- 后续{lookahead}章需要[{char_list}]出场 → 本章不得杀死或永久移除该角色"
            )

        for ch_num, conflict in future_conflicts:
            parts.append(f"- 第{ch_num}章的冲突是[{conflict}] → 本章不得提前解决该冲突")

        for ch_num, f_name in future_reveals:
            parts.append(f"- 第{ch_num}章揭示伏笔「{f_name}」 → 本章不得提前揭示")

        foreshadowing = await core_service.list_foreshadowing(project_id)
        near_reveal = [
            f
            for f in foreshadowing
            if f.get("status") == "active"
            and f.get("reveal_window_start")
            and chapter_number <= f.get("reveal_window_start", 0) <= chapter_number + lookahead
        ]
        if near_reveal:
            names = "、".join(f"「{f.get('name', '')}」(第{f.get('reveal_window_start')}章收)" for f in near_reveal)
            parts.append(f"- 近期待回收伏笔：{names} → 本章应为回收做铺垫，不得遗忘")

        return "\n".join(parts)

    async def build_soft_guidance(
        self,
        project_id: str,
        chapter_number: int,
        db: AsyncSession = None,
        lookahead: int = 5,
    ) -> str:
        pid = uuid.UUID(project_id)
        index_service = OutlineIndexService()
        core_service = CoreMemoryService()

        project = await db.get(Project, pid)
        if not project or not project.outline_data:
            return ""

        outline = project.outline_data

        future_chapters = []
        chapter_spine = outline.get("chapter_spine", [])
        if chapter_spine:
            for ch in chapter_spine:
                ch_num = ch.get("chapter_number", 0)
                if chapter_number < ch_num <= chapter_number + lookahead:
                    future_chapters.append(ch)
        if not future_chapters:
            chapters = outline.get("chapters", [])
            for ch in chapters:
                ch_num = ch.get("chapter_number", 0)
                if chapter_number < ch_num <= chapter_number + lookahead:
                    future_chapters.append(ch)

        parts = ["## 软引导（建议遵循）"]

        if future_chapters:
            trend_parts = []
            for ch in future_chapters[:5]:
                ch_num = ch.get("chapter_number", 0)
                conflict = self._get_conflict_text(ch)
                shift = self._get_value_shift_text(ch)
                if conflict or shift:
                    trend_parts.append(f"第{ch_num}章：{conflict}→{shift}")
            if trend_parts:
                parts.append(f"- 后续趋势：{'；'.join(trend_parts)}")

        current_chapter = self._get_chapter_data(outline, chapter_number)

        pov_character = ""
        if current_chapter:
            pov_character = current_chapter.get("pov_character", "")

        if pov_character:
            characters = await core_service.list_characters(project_id)
            for c in characters:
                c_data = c.model_dump() if hasattr(c, "model_dump") else c
                if c_data.get("name") == pov_character:
                    arc = c_data.get("arc", "")
                    desire = c_data.get("desire", "")
                    arc_info = f"弧光：{arc}" if arc else ""
                    desire_info = f"欲望：{desire}" if desire else ""
                    detail = "，".join(filter(None, [arc_info, desire_info]))
                    if detail:
                        parts.append(f"- 人物弧光：{pov_character}当前处于[{detail}]")
                    break

        past_chapters = []
        chapter_spine = outline.get("chapter_spine", [])
        if chapter_spine:
            for ch in chapter_spine:
                if ch.get("chapter_number", 0) < chapter_number:
                    past_chapters.append(ch)
        if not past_chapters:
            for ch in outline.get("chapters", []):
                if ch.get("chapter_number", 0) < chapter_number:
                    past_chapters.append(ch)

        if len(past_chapters) >= 2:
            recent = past_chapters[-3:]
            tension_levels = []
            for ch in recent:
                conflict = self._get_conflict_text(ch)
                shift = self._get_value_shift_text(ch)
                has_scene = bool(ch.get("scenes"))
                tension = 0
                if conflict:
                    tension += 1
                if shift and ("升" in shift or "激化" in shift or "恶化" in shift):
                    tension += 2
                elif shift:
                    tension += 1
                if has_scene:
                    tension += 1
                tension_levels.append(tension)

            avg_tension = sum(tension_levels) / len(tension_levels) if tension_levels else 0

            if avg_tension >= 2.5:
                parts.append("- 节奏建议：前几章持续高压，建议本章适度放缓节奏，插入喘息或反思场景")
            elif avg_tension <= 1.0:
                parts.append("- 节奏建议：前几章节奏偏缓，建议本章提升冲突强度，推进核心矛盾")
            else:
                parts.append("- 节奏建议：前几章节奏适中，可按大纲自然推进")

        # 角色维度提示：为反派/主要对手角色添加人性化建议
        antagonist_hints = await self._build_antagonist_dimension_hints(
            core_service, project_id, current_chapter
        )
        if antagonist_hints:
            parts.append(antagonist_hints)

        return "\n".join(parts)

    async def _build_antagonist_dimension_hints(
        self, core_service, project_id: str, current_chapter: dict | None,
    ) -> str:
        """为场景中出场的反派/对手角色生成维度丰富度提示。"""
        if not current_chapter:
            return ""

        # 收集本场景出场角色
        scene_characters = set()
        pov = current_chapter.get("pov_character", "")
        if pov:
            scene_characters.add(pov)
        for scene in current_chapter.get("scenes", []):
            for char in scene.get("characters", []):
                if isinstance(char, str):
                    scene_characters.add(char)
                elif isinstance(char, dict):
                    name = char.get("name", "")
                    if name:
                        scene_characters.add(name)

        if not scene_characters:
            return ""

        try:
            characters = await core_service.list_characters(project_id)
        except Exception:
            return ""

        antagonist_names = []
        for c in characters:
            c_data = c.model_dump() if hasattr(c, "model_dump") else c
            name = c_data.get("name", "")
            role = c_data.get("role", "")
            if name in scene_characters and role in ("antagonist", "rival", "opponent"):
                has_humanizing = bool(c_data.get("humanizing_detail"))
                has_contradiction = bool(c_data.get("contradiction_dimension"))
                if not has_humanizing and not has_contradiction:
                    antagonist_names.append(name)

        if not antagonist_names:
            return ""

        names_str = "、".join(antagonist_names)
        return (
            f"- 角色维度建议：{names_str}在本场景中缺少人性化维度，"
            f"建议为其添加一个不完全是算计的微表情或反应"
            f"（如：真实的犹豫、下意识的关心、与主线无关的习惯动作），增加角色层次感"
        )

    async def build_activation_reminder(
        self, project_id: str, chapter_number: int, db: AsyncSession = None
    ) -> str:
        pid = uuid.UUID(project_id)
        index_service = OutlineIndexService()
        core_service = CoreMemoryService()

        project = await db.get(Project, pid)
        chapter = None
        if project and project.outline_data:
            chapter = self._get_chapter_data(project.outline_data, chapter_number)
        if not chapter:
            chapter = await index_service.get_chapter_detail(pid, chapter_number, db)
        if not chapter:
            return ""

        parts = ["【规则激活——本章重新加载】"]

        rules = await core_service.list_world_rules(project_id)
        critical_rules = [r for r in rules if r.get("priority") == "critical"]
        if critical_rules:
            iron_names = "、".join(r.get("name", "") for r in critical_rules)
            parts.append(f"- 世界观铁则：{iron_names}")

        plant_items = []
        reveal_items = []
        thread_ops = self._read_thread_ops(chapter)
        thread_plant_items = [op.get("thread_id", "") for op in thread_ops if op.get("op") == "plant"]
        thread_reveal_items = [op.get("thread_id", "") for op in thread_ops if op.get("op") == "reveal"]

        if plant_items or reveal_items or thread_plant_items or thread_reveal_items:
            plant_str = "、".join(plant_items) if plant_items else "无"
            reveal_str = "、".join(reveal_items) if reveal_items else "无"
            thread_plant_str = "、".join(thread_plant_items) if thread_plant_items else "无"
            thread_reveal_str = "、".join(thread_reveal_items) if thread_reveal_items else "无"
            parts.append(f"- 伏笔检查：本章应埋[{plant_str}]、应收[{reveal_str}]；线索操作应埋[{thread_plant_str}]、应收[{thread_reveal_str}]")

        pov_character = chapter.get("pov_character", "")
        if pov_character:
            characters = await core_service.list_characters(project_id)
            arc_stage = ""
            for c in characters:
                c_data = c.model_dump() if hasattr(c, "model_dump") else c
                if c_data.get("name") == pov_character:
                    arc_stage = c_data.get("arc", "")
                    break
            if arc_stage:
                parts.append(f"- POV角色：{pov_character}，弧光阶段[{arc_stage}]")
            else:
                parts.append(f"- POV角色：{pov_character}")

        # 从 Genre Profile 读取禁忌，无 Profile 时使用通用 fallback
        genre_profile = None
        try:
            from app.services.genre_profile_service import GenreProfileService
            genre_svc = GenreProfileService()
            core_data = project.core_data if project else {}
            genre_profile = await genre_svc.get_project_genre_profile(
                project_id, db=db, core_data=core_data,
            )
            prohibitions = genre_svc.get_activation_prohibitions(genre_profile)
        except Exception:
            from app.services.genre_profile_service import GenreProfileService as _GPS
            prohibitions = _GPS().get_activation_prohibitions(None)

        parts.append(f"- 禁止：{'、'.join(prohibitions)}")

        return "\n".join(parts)

    async def build_all_constraints(
        self, project_id: str, chapter_number: int, db: AsyncSession = None
    ) -> str:
        hard = await self.build_hard_constraints(project_id, chapter_number, db)
        corridor = await self.build_corridor_constraints(project_id, chapter_number, db)
        soft = await self.build_soft_guidance(project_id, chapter_number, db)
        activation = await self.build_activation_reminder(project_id, chapter_number, db)

        sections = []
        if hard:
            sections.append(hard)
        if corridor:
            sections.append(corridor)
        if soft:
            sections.append(soft)
        if activation:
            sections.append(activation)

        return "\n\n".join(sections)
