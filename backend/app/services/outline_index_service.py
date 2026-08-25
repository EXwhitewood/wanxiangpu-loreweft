import re
import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.text_coercion import to_search_text, to_text

from app.db.db_models import Project


def _estimate_tokens(text: str) -> int:
    if not text:
        return 0
    chinese_chars = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    other_chars = len(text) - chinese_chars
    return int(chinese_chars * 1.5 + other_chars * 0.4)


def _read_conflict_text(ch: dict) -> str:
    conflict = ch.get("conflict_text")
    if conflict:
        if isinstance(conflict, dict):
            return "；".join(str(v) for v in conflict.values() if v)
        return str(conflict)
    core = ch.get("core_conflict")
    if core:
        if isinstance(core, dict):
            return "；".join(str(v) for v in core.values() if v)
        return str(core)
    return ch.get("main_conflict", "")


def _read_value_shift(ch: dict) -> str:
    shift = ch.get("value_shift")
    if shift:
        if isinstance(shift, dict):
            parts = []
            for axis, detail in shift.items():
                if isinstance(detail, dict):
                    frm = detail.get("from", detail.get("from_state", ""))
                    to = detail.get("to", detail.get("to_state", ""))
                    if frm and to:
                        parts.append(f"{axis}: {frm}→{to}")
                    elif to:
                        parts.append(f"{axis}: →{to}")
                    elif frm:
                        parts.append(f"{axis}: {frm}→")
                    else:
                        parts.append(str(axis))
                else:
                    parts.append(f"{axis}: {detail}")
            return "；".join(parts)
        return str(shift)
    return ""


def _get_chapters_from_outline(outline: dict) -> list:
    spine = outline.get("chapter_spine")
    if spine and isinstance(spine, list) and len(spine) > 0:
        return spine
    return outline.get("chapters", [])


class OutlineIndexService:

    async def generate_and_save_index(
        self, project_id: uuid.UUID, db: AsyncSession
    ) -> str:
        project = await db.get(Project, project_id)
        outline = project.outline_data or {}
        chapters = _get_chapters_from_outline(outline)

        if not chapters:
            project.outline_version = 0
            project.outline_index = {}
            await db.commit()
            return ""

        node_map, act_map, second_level = self._build_index_structure(
            chapters, project
        )
        full_index_md = self._build_full_index(chapters, project, act_map)
        first_level_md = self._build_first_level_index(chapters, project, act_map)

        project.outline_version = (project.outline_version or 0) + 1
        project.outline_index = {
            "markdown": full_index_md,
            "first_level": first_level_md,
            "version": project.outline_version,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "node_map": node_map,
            "act_map": act_map,
            "second_level": second_level,
        }
        await db.commit()
        return full_index_md

    async def get_index(
        self, project_id: uuid.UUID, db: AsyncSession
    ) -> str | None:
        project = await db.get(Project, project_id)
        outline = project.outline_data or {}
        chapters = _get_chapters_from_outline(outline)
        if not chapters:
            return None

        index_data = project.outline_index
        if not index_data:
            return await self.generate_and_save_index(project_id, db)

        if index_data.get("version") != project.outline_version:
            return await self.generate_and_save_index(project_id, db)

        return index_data.get("markdown")

    async def get_first_level_index(
        self, project_id: uuid.UUID, db: AsyncSession
    ) -> str | None:
        project = await db.get(Project, project_id)
        outline = project.outline_data or {}
        chapters = _get_chapters_from_outline(outline)
        if not chapters:
            return None

        index_data = project.outline_index
        if not index_data or index_data.get("version") != project.outline_version:
            await self.generate_and_save_index(project_id, db)
            project = await db.get(Project, project_id)
            index_data = project.outline_index

        first_level = index_data.get("first_level")
        if first_level:
            return first_level

        return index_data.get("markdown")

    async def get_chapter_detail(
        self, project_id: uuid.UUID, chapter_number: int, db: AsyncSession
    ) -> dict | None:
        project = await db.get(Project, project_id)
        outline = project.outline_data or {}
        index_data = project.outline_index or {}
        node_map = index_data.get("node_map", {})

        spine = outline.get("chapter_spine", [])
        for ch in spine:
            if ch.get("chapter_number") == chapter_number:
                result = dict(ch)
                for node_id, node_info in node_map.items():
                    if chapter_number in node_info.get("chapters", []):
                        result["node"] = node_info.get("name", "")
                        result["node_id"] = node_id
                        result["act"] = node_info.get("act", "")
                        break
                return result

        for ch in outline.get("chapters", []):
            if ch.get("chapter_number") == chapter_number:
                result = dict(ch)
                for node_id, node_info in node_map.items():
                    if chapter_number in node_info.get("chapters", []):
                        result["node"] = node_info.get("name", "")
                        result["node_id"] = node_id
                        result["act"] = node_info.get("act", "")
                        break
                return result
        return None

    async def get_chapter_spine_detail(
        self, project_id: uuid.UUID, chapter_number: int, db: AsyncSession
    ) -> dict | None:
        project = await db.get(Project, project_id)
        outline = project.outline_data or {}
        spine = outline.get("chapter_spine", [])

        for ch in spine:
            if ch.get("chapter_number") == chapter_number:
                result = dict(ch)
                result["source"] = "chapter_spine"
                for key in ("node_id", "function", "thread_ops"):
                    if key not in result:
                        result[key] = ch.get(key)
                return result
        return None

    async def get_constitution_summary(
        self, project_id: uuid.UUID, db: AsyncSession
    ) -> str:
        project = await db.get(Project, project_id)
        outline = project.outline_data or {}
        constitution = outline.get("story_constitution", {})

        if not constitution:
            return ""

        lines = ["## 故事体质摘要\n"]

        for key, value in constitution.items():
            if isinstance(value, str) and value:
                lines.append(f"- **{key}**：{value}")
            elif isinstance(value, dict):
                parts = []
                for sub_key, sub_val in value.items():
                    if isinstance(sub_val, str) and sub_val:
                        parts.append(f"{sub_key}: {sub_val}")
                    elif isinstance(sub_val, list):
                        parts.append(
                            f"{sub_key}: {', '.join(str(v) for v in sub_val)}"
                        )
                    elif sub_val:
                        parts.append(f"{sub_key}: {sub_val}")
                if parts:
                    lines.append(f"- **{key}**：{'；'.join(parts)}")
            elif isinstance(value, list) and value:
                items = ", ".join(str(v) for v in value)
                lines.append(f"- **{key}**：{items}")

        return "\n".join(lines)

    async def get_thread_plan_summary(
        self, project_id: uuid.UUID, db: AsyncSession
    ) -> str:
        project = await db.get(Project, project_id)
        outline = project.outline_data or {}
        thread_plan = outline.get("thread_plan", {})

        if not thread_plan:
            return ""

        lines = ["## 线程计划摘要\n"]

        if isinstance(thread_plan, list):
            for i, thread in enumerate(thread_plan):
                if isinstance(thread, dict):
                    name = thread.get(
                        "name", thread.get("thread_name", f"线程{i + 1}")
                    )
                    lines.append(f"### {name}")
                    for key, value in thread.items():
                        if key in ("name", "thread_name"):
                            continue
                        if isinstance(value, str) and value:
                            lines.append(f"- **{key}**：{value}")
                        elif isinstance(value, list) and value:
                            items = ", ".join(str(v) for v in value)
                            lines.append(f"- **{key}**：{items}")
                        elif isinstance(value, dict):
                            parts = []
                            for sk, sv in value.items():
                                if sv:
                                    parts.append(f"{sk}: {sv}")
                            if parts:
                                lines.append(f"- **{key}**：{'；'.join(parts)}")
                    lines.append("")
        elif isinstance(thread_plan, dict):
            for thread_name, thread_data in thread_plan.items():
                lines.append(f"### {thread_name}")
                if isinstance(thread_data, dict):
                    for key, value in thread_data.items():
                        if isinstance(value, str) and value:
                            lines.append(f"- **{key}**：{value}")
                        elif isinstance(value, list) and value:
                            items = ", ".join(str(v) for v in value)
                            lines.append(f"- **{key}**：{items}")
                elif isinstance(thread_data, str) and thread_data:
                    lines.append(f"- {thread_data}")
                lines.append("")

        return "\n".join(lines)

    async def get_node_detail(
        self, project_id: uuid.UUID, node_id: str, db: AsyncSession
    ) -> dict:
        project = await db.get(Project, project_id)
        outline = project.outline_data or {}
        chapters = _get_chapters_from_outline(outline)
        if not chapters:
            return {"error": "当前大纲为空"}

        index_data = project.outline_index or {}

        if not index_data or index_data.get("version") != project.outline_version:
            await self.generate_and_save_index(project_id, db)
            project = await db.get(Project, project_id)
            index_data = project.outline_index

        node_map = index_data.get("node_map", {})
        node_info = node_map.get(node_id)
        if not node_info:
            return {"error": f"未找到叙事节点: {node_id}"}

        outline = project.outline_data or {}
        chapter_numbers = node_info.get("chapters", [])
        matched_chapters = []
        for ch in _get_chapters_from_outline(outline):
            if ch.get("chapter_number") in chapter_numbers:
                matched_chapters.append(ch)

        return {
            "node_id": node_id,
            "node_name": node_info.get("name", ""),
            "act": node_info.get("act", ""),
            "chapter_range": node_info.get("chapter_range", []),
            "key_turning_point": node_info.get("key_turning_point", ""),
            "chapters": matched_chapters,
            "chapter_count": len(matched_chapters),
            "_hint": "已获取叙事节点全部章节。基于此信息回答用户问题或给出修改建议。",
        }

    async def get_act_index(
        self, project_id: uuid.UUID, act_number: int, db: AsyncSession
    ) -> dict:
        project = await db.get(Project, project_id)
        outline = project.outline_data or {}
        chapters = _get_chapters_from_outline(outline)
        if not chapters:
            return {"error": "当前大纲为空"}

        index_data = project.outline_index or {}

        if not index_data or index_data.get("version") != project.outline_version:
            await self.generate_and_save_index(project_id, db)
            project = await db.get(Project, project_id)
            index_data = project.outline_index

        act_map = index_data.get("act_map", {})
        act_key = str(act_number)
        act_info = act_map.get(act_key)

        if not act_info:
            return {"error": f"未找到第 {act_number} 幕"}

        second_level = index_data.get("second_level", {})
        act_detail_md = second_level.get(act_key, "")

        return {
            "act_number": act_number,
            "act_name": act_info.get("name", f"第{act_number}幕"),
            "chapter_range": act_info.get("chapter_range", []),
            "nodes": act_info.get("nodes", []),
            "detail_markdown": act_detail_md,
            "_hint": "已获取该幕的节点详情。基于此信息回答用户问题。",
        }

    async def search_chapters(
        self, project_id: uuid.UUID, query: str, db: AsyncSession
    ) -> list[dict]:
        project = await db.get(Project, project_id)
        outline = project.outline_data or {}
        query = to_text(query)
        query_lower = to_search_text(query)

        keyword_results = self._keyword_search(outline, query_lower)

        semantic_results = []
        try:
            from app.services.outline_embedding_service import OutlineEmbeddingService
            from app.db.db_models import PGVECTOR_AVAILABLE
            if PGVECTOR_AVAILABLE:
                emb_service = OutlineEmbeddingService()
                semantic_results = await emb_service.search_semantic(
                    project_id, query, db, top_k=5
                )
        except Exception:
            pass

        if not semantic_results:
            return keyword_results

        return self._merge_search_results(keyword_results, semantic_results, outline)

    async def get_foreshadowing(
        self, project_id: uuid.UUID, name: str, db: AsyncSession
    ) -> dict:
        from app.services.foreshadowing_service import ForeshadowingService

        line = await ForeshadowingService().get_foreshadowing_by_name(
            project_id, name, db
        )
        if not line:
            return {
                "name": name,
                "entries": [],
                "total_planted": 0,
                "total_revealed": 0,
                "pending": 0,
                "source": "foreshadowing_lines",
            }

        bury_start = line.get("bury_window_start")
        bury_end = line.get("bury_window_end") or bury_start
        reveal_start = line.get("reveal_window_start")
        reveal_end = line.get("reveal_window_end") or reveal_start
        entries = []
        if bury_start:
            entries.append({"chapter": bury_start, "action": "plant"})
        if bury_end and bury_end != bury_start:
            entries.append({"chapter": bury_end, "action": "plant"})
        if reveal_start:
            entries.append({"chapter": reveal_start, "action": "reveal"})
        if reveal_end and reveal_end != reveal_start:
            entries.append({"chapter": reveal_end, "action": "reveal"})

        return {
            "name": name,
            "entries": entries,
            "total_planted": sum(1 for item in entries if item["action"] == "plant"),
            "total_revealed": sum(1 for item in entries if item["action"] == "reveal"),
            "pending": 0 if line.get("status") == "resolved" else 1,
            "source": "foreshadowing_lines",
        }

    def _build_index_structure(self, chapters: list, project) -> tuple:
        outline = project.outline_data or {}
        macro = outline.get("macro_structure", {})
        acts = macro.get("acts", [])

        if acts and any(a.get("nodes") for a in acts):
            return self._build_from_macro(chapters, acts)

        if any(ch.get("node_id") for ch in chapters):
            return self._build_from_node_ids(chapters)

        return self._auto_generate_structure(chapters)

    def _build_from_macro(self, chapters: list, acts: list) -> tuple:
        node_map = {}
        act_map = {}
        second_level = {}

        for act in acts:
            act_num = act.get("act_number", 1)
            act_name = act.get("name", f"第{act_num}幕")
            ch_range = act.get("chapter_range", [])
            nodes = act.get("nodes", [])

            act_nodes = []
            for i, node in enumerate(nodes):
                node_id = node.get("node_id", f"act{act_num}_n{i + 1}")
                node_name = node.get("name", "")
                node_ch_range = node.get("chapter_range", [])

                if (
                    isinstance(node_ch_range, list)
                    and len(node_ch_range) == 2
                    and isinstance(node_ch_range[0], int)
                    and isinstance(node_ch_range[1], int)
                ):
                    node_ch_list = list(
                        range(node_ch_range[0], node_ch_range[1] + 1)
                    )
                elif isinstance(node_ch_range, list):
                    node_ch_list = node_ch_range
                else:
                    node_ch_list = []

                node_map[node_id] = {
                    "name": node_name,
                    "chapters": node_ch_list,
                    "chapter_range": node_ch_range,
                    "act": act_name,
                    "key_turning_point": node.get("key_turning_point", ""),
                }
                act_nodes.append(
                    {
                        "node_id": node_id,
                        "name": node_name,
                        "chapter_range": node_ch_range,
                        "key_turning_point": node.get("key_turning_point", ""),
                    }
                )

            all_ch_list = []
            if len(ch_range) >= 2 and isinstance(ch_range[0], int):
                all_ch_list = list(range(ch_range[0], ch_range[1] + 1))

            act_map[str(act_num)] = {
                "name": act_name,
                "chapter_range": ch_range,
                "nodes": act_nodes,
                "chapters": all_ch_list,
            }

        for act_key, act_info in act_map.items():
            second_level[act_key] = self._build_act_detail_md(act_info, node_map)

        return node_map, act_map, second_level

    def _build_from_node_ids(self, chapters: list) -> tuple:
        node_map = {}
        act_map = {}
        second_level = {}

        for ch in chapters:
            node_id = ch.get("node_id", "")
            act_name = ch.get("act", "")
            if not node_id:
                continue

            if node_id not in node_map:
                node_map[node_id] = {
                    "name": ch.get("node", ""),
                    "chapters": [],
                    "act": act_name,
                    "key_turning_point": _read_conflict_text(ch)[:30],
                }
            node_map[node_id]["chapters"].append(ch.get("chapter_number"))

        for node_id, info in node_map.items():
            ch_nums = info["chapters"]
            if ch_nums:
                info["chapter_range"] = [min(ch_nums), max(ch_nums)]

            act_name = info.get("act", "")
            act_num = self._extract_act_number(node_id)
            act_key = str(act_num)

            if act_key not in act_map:
                act_map[act_key] = {
                    "name": act_name or f"第{act_num}幕",
                    "chapter_range": [],
                    "nodes": [],
                    "chapters": [],
                }
            act_map[act_key]["nodes"].append(
                {
                    "node_id": node_id,
                    "name": info["name"],
                    "chapter_range": info.get("chapter_range", []),
                    "key_turning_point": info.get("key_turning_point", ""),
                }
            )
            act_map[act_key]["chapters"].extend(ch_nums)

        for act_key, act_info in act_map.items():
            ch_list = act_info.get("chapters", [])
            if ch_list:
                act_info["chapter_range"] = [min(ch_list), max(ch_list)]
            second_level[act_key] = self._build_act_detail_md(act_info, node_map)

        return node_map, act_map, second_level

    def _auto_generate_structure(self, chapters: list) -> tuple:
        if not chapters:
            return {}, {}, {}

        total = len(chapters)
        if total <= 3:
            return self._auto_generate_simple(chapters)

        act1_end = max(1, int(total * 0.25))
        act2_end = max(act1_end + 1, int(total * 0.75))

        acts_config = [
            {"num": 1, "name": "第一幕：建制", "start": 1, "end": act1_end},
            {
                "num": 2,
                "name": "第二幕：对抗",
                "start": act1_end + 1,
                "end": act2_end,
            },
            {
                "num": 3,
                "name": "第三幕：结局",
                "start": act2_end + 1,
                "end": total,
            },
        ]

        node_templates = {
            1: ["触发事件", "适应挣扎", "第一幕转折"],
            2: ["上升冲突", "中点转折", "下降危机", "一切尽失"],
            3: ["最终决战", "结局收束"],
        }

        node_map = {}
        act_map = {}
        second_level = {}

        for act_cfg in acts_config:
            act_num = act_cfg["num"]
            act_name = act_cfg["name"]
            act_chapters = [
                ch
                for ch in chapters
                if act_cfg["start"] <= ch.get("chapter_number", 0) <= act_cfg["end"]
            ]

            if not act_chapters:
                continue

            templates = node_templates.get(act_num, ["叙事节点"])
            ch_per_node = max(1, len(act_chapters) // len(templates))

            act_nodes = []
            for i, template_name in enumerate(templates):
                node_id = f"act{act_num}_n{i + 1}"
                start_idx = i * ch_per_node
                end_idx = (
                    start_idx + ch_per_node
                    if i < len(templates) - 1
                    else len(act_chapters)
                )
                node_chapters = act_chapters[start_idx:end_idx]

                if not node_chapters:
                    continue

                ch_numbers = [ch.get("chapter_number", 0) for ch in node_chapters]
                key_conflict = ""
                for ch in node_chapters:
                    conflict = _read_conflict_text(ch)
                    if conflict:
                        key_conflict = conflict[:30]
                        break

                node_map[node_id] = {
                    "name": template_name,
                    "chapters": ch_numbers,
                    "chapter_range": [min(ch_numbers), max(ch_numbers)],
                    "act": act_name,
                    "key_turning_point": key_conflict,
                }

                act_nodes.append(
                    {
                        "node_id": node_id,
                        "name": template_name,
                        "chapter_range": [min(ch_numbers), max(ch_numbers)],
                        "key_turning_point": key_conflict,
                    }
                )

            all_ch_numbers = [
                ch.get("chapter_number", 0) for ch in act_chapters
            ]
            act_map[str(act_num)] = {
                "name": act_name,
                "chapter_range": [min(all_ch_numbers), max(all_ch_numbers)],
                "nodes": act_nodes,
                "chapters": all_ch_numbers,
            }

        for act_key, act_info in act_map.items():
            second_level[act_key] = self._build_act_detail_md(act_info, node_map)

        return node_map, act_map, second_level

    def _auto_generate_simple(self, chapters: list) -> tuple:
        node_map = {}
        act_map = {}
        second_level = {}

        ch_numbers = [ch.get("chapter_number", 0) for ch in chapters]
        node_id = "act1_n1"
        node_name = "完整叙事"

        key_conflict = ""
        for ch in chapters:
            conflict = _read_conflict_text(ch)
            if conflict:
                key_conflict = conflict[:30]
                break

        node_map[node_id] = {
            "name": node_name,
            "chapters": ch_numbers,
            "chapter_range": [min(ch_numbers), max(ch_numbers)],
            "act": "单幕",
            "key_turning_point": key_conflict,
        }

        act_map["1"] = {
            "name": "单幕",
            "chapter_range": [min(ch_numbers), max(ch_numbers)],
            "nodes": [
                {
                    "node_id": node_id,
                    "name": node_name,
                    "chapter_range": [min(ch_numbers), max(ch_numbers)],
                    "key_turning_point": key_conflict,
                }
            ],
            "chapters": ch_numbers,
        }

        second_level["1"] = self._build_act_detail_md(act_map["1"], node_map)

        return node_map, act_map, second_level

    def _build_act_detail_md(self, act_info: dict, node_map: dict) -> str:
        lines = [f"### {act_info.get('name', '')}"]
        ch_range = act_info.get("chapter_range", [])
        if len(ch_range) >= 2:
            lines.append(f"章节范围：{ch_range[0]}-{ch_range[1]}\n")

        lines.append("| 节点 | 节点ID | 章节范围 | 关键转折 | 状态 |")
        lines.append("|------|--------|----------|----------|------|")

        for node in act_info.get("nodes", []):
            node_id = node.get("node_id", "")
            name = node.get("name", "")
            n_ch_range = node.get("chapter_range", [])
            if isinstance(n_ch_range, list) and len(n_ch_range) >= 2:
                range_str = f"{n_ch_range[0]}-{n_ch_range[1]}"
            elif isinstance(n_ch_range, list) and len(n_ch_range) == 1:
                range_str = str(n_ch_range[0])
            else:
                range_str = str(n_ch_range)
            turning = node.get("key_turning_point", "")[:20]
            status = "✅"
            lines.append(f"| {name} | {node_id} | {range_str} | {turning} | {status} |")

        return "\n".join(lines)

    def _build_first_level_index(
        self, chapters: list, project, act_map: dict
    ) -> str:
        lines = ["## 大纲结构索引\n"]

        if project:
            lines.append(f"项目：{project.name}")
        lines.append(f"结构：{len(chapters)} 章")

        foreshadowing_count = self._count_foreshadowing_from_chapters(chapters)
        if foreshadowing_count:
            lines.append(f"伏笔线：{foreshadowing_count}条")

        if act_map:
            lines.append("\n### 幕级概览")
            for act_key in sorted(
                act_map.keys(), key=lambda x: int(x) if x.isdigit() else 0
            ):
                act_info = act_map[act_key]
                act_name = act_info.get("name", f"第{act_key}幕")
                ch_range = act_info.get("chapter_range", [])
                node_count = len(act_info.get("nodes", []))
                range_str = (
                    f"{ch_range[0]}-{ch_range[1]}" if len(ch_range) >= 2 else ""
                )
                lines.append(
                    f"- {act_name}（{range_str}章）| {node_count}个节点"
                )

            lines.append("\n获取某幕的节点详情，使用 get_act_index 工具。")
            lines.append("获取具体章节详情，使用 get_chapter_outline 工具。")
            lines.append("获取叙事节点全部章节，使用 get_node_outline 工具。")
        else:
            lines.append("\n| # | 章节 | 核心冲突 | 价值转变 |")
            lines.append("|---|------|----------|----------|")
            for ch in chapters:
                num = ch.get("chapter_number", "")
                title = ch.get("title", "")
                conflict = _read_conflict_text(ch)
                shift = _read_value_shift(ch)
                lines.append(f"| {num} | {title} | {conflict} | {shift} |")

        return "\n".join(lines)

    def _build_full_index(
        self, chapters: list, project, act_map: dict
    ) -> str:
        lines = ["## 大纲结构索引\n"]

        if project:
            lines.append(f"项目：{project.name}")
        lines.append(f"结构：{len(chapters)} 章")

        foreshadowing = {}
        for ch in chapters:
            for f in ch.get("thread_ops", []):
                f_name = f.get("thread_id", "")
                action = f.get("op", "")
                if f_name not in foreshadowing:
                    foreshadowing[f_name] = {"plant": 0, "reveal": 0}
                if action in foreshadowing[f_name]:
                    foreshadowing[f_name][action] += 1

        if foreshadowing:
            lines.append(f"伏笔线：{len(foreshadowing)}条")

        if act_map:
            for act_key in sorted(
                act_map.keys(), key=lambda x: int(x) if x.isdigit() else 0
            ):
                act_info = act_map[act_key]
                act_name = act_info.get("name", f"第{act_key}幕")
                ch_range = act_info.get("chapter_range", [])
                range_str = (
                    f"（{ch_range[0]}-{ch_range[1]}章）"
                    if len(ch_range) >= 2
                    else ""
                )
                lines.append(f"\n### {act_name}{range_str}")

                lines.append(
                    "| 节点 | 节点ID | 章节范围 | 关键转折 | 状态 |"
                )
                lines.append(
                    "|------|--------|----------|----------|------|"
                )

                for node in act_info.get("nodes", []):
                    node_id = node.get("node_id", "")
                    name = node.get("name", "")
                    n_ch_range = node.get("chapter_range", [])
                    if (
                        isinstance(n_ch_range, list) and len(n_ch_range) >= 2
                    ):
                        range_str = f"{n_ch_range[0]}-{n_ch_range[1]}"
                    else:
                        range_str = str(n_ch_range)
                    turning = node.get("key_turning_point", "")[:20]
                    status = "✅"
                    lines.append(
                        f"| {name} | {node_id} | {range_str} | {turning} | {status} |"
                    )
        else:
            lines.append("\n| # | 章节 | 核心冲突 | 价值转变 | 状态 |")
            lines.append("|---|------|----------|----------|------|")
            for ch in chapters:
                num = ch.get("chapter_number", "")
                title = ch.get("title", "")
                conflict = _read_conflict_text(ch)
                shift = _read_value_shift(ch)
                status = "✅"
                lines.append(
                    f"| {num} | {title} | {conflict} | {shift} | {status} |"
                )

        if foreshadowing:
            lines.append("\n### 伏笔线状态")
            lines.append("| 伏笔线 | 已埋 | 已收 | 待收 |")
            lines.append("|--------|------|------|------|")
            for f_name, counts in foreshadowing.items():
                planted = counts["plant"]
                revealed = counts["reveal"]
                pending = planted - revealed
                lines.append(f"| {f_name} | {planted} | {revealed} | {pending} |")

        return "\n".join(lines)

    def _count_foreshadowing_from_chapters(self, chapters: list) -> int:
        names = set()
        for ch in chapters:
            for f in ch.get("thread_ops", []):
                name = f.get("thread_id", "")
                if name:
                    names.add(name)
        return len(names)

    def _extract_act_number(self, node_id: str) -> int:
        match = re.search(r"act(\d+)", node_id)
        if match:
            return int(match.group(1))
        return 1

    def _match_score(self, d: dict, query: str) -> int:
        score = 0
        high_fields = {"title", "conflict_text", "main_conflict", "core_conflict", "value_shift"}
        for k, v in d.items():
            if isinstance(v, str) and query in v.lower():
                weight = 3 if k in high_fields else 1
                score += weight
            elif isinstance(v, dict):
                score += self._match_score(v, query)
            elif isinstance(v, list):
                for item in v:
                    if isinstance(item, str) and query in item.lower():
                        score += 1
                    elif isinstance(item, dict):
                        score += self._match_score(item, query)
        return score

    def _extract_match_context(self, ch: dict, query: str) -> str:
        high_fields = ["title", "conflict_text", "main_conflict", "core_conflict", "value_shift"]
        for field in high_fields:
            val = ch.get(field, "")
            if isinstance(val, str) and query in val.lower():
                return f"[{field}] {val[:80]}"

        for k, v in ch.items():
            if isinstance(v, str) and query in v.lower() and k not in high_fields:
                return f"[{k}] {v[:80]}"

        for scene in ch.get("scenes", []):
            if isinstance(scene, dict):
                for sk, sv in scene.items():
                    if isinstance(sv, str) and query in sv.lower():
                        return f"[scene.{sk}] {sv[:80]}"

        return ""

    def _dict_contains(self, d: dict, query: str) -> bool:
        for v in d.values():
            if isinstance(v, str) and query in v.lower():
                return True
            if isinstance(v, dict) and self._dict_contains(v, query):
                return True
            if isinstance(v, list):
                for item in v:
                    if isinstance(item, str) and query in item.lower():
                        return True
                    if isinstance(item, dict) and self._dict_contains(item, query):
                        return True
        return False

    def _keyword_search(self, outline: dict, query_lower: str) -> list[dict]:
        results = []
        seen_numbers = set()

        spine = outline.get("chapter_spine", [])
        for ch in spine:
            score = self._match_score(ch, query_lower)
            if score > 0:
                ch_num = ch.get("chapter_number")
                if ch_num is not None:
                    if ch_num in seen_numbers:
                        continue
                    seen_numbers.add(ch_num)
                context = self._extract_match_context(ch, query_lower)
                results.append({"chapter": ch, "score": score, "context": context})

        for ch in outline.get("chapters", []):
            ch_num = ch.get("chapter_number")
            if ch_num is not None and ch_num in seen_numbers:
                continue
            score = self._match_score(ch, query_lower)
            if score > 0:
                if ch_num is not None:
                    seen_numbers.add(ch_num)
                context = self._extract_match_context(ch, query_lower)
                results.append({"chapter": ch, "score": score, "context": context})

        results.sort(key=lambda x: x["score"], reverse=True)
        return [
            {"chapter": r["chapter"], "match_context": r["context"]}
            for r in results
        ]

    def _merge_search_results(
        self, keyword_results: list[dict], semantic_results: list[dict], outline: dict
    ) -> list[dict]:
        ch_map = {}
        for ch in _get_chapters_from_outline(outline):
            ch_num = ch.get("chapter_number")
            if ch_num is not None:
                ch_map[ch_num] = ch

        seen = set()
        merged = []

        for kr in keyword_results:
            ch = kr.get("chapter", {})
            ch_num = ch.get("chapter_number")
            if ch_num is not None and ch_num not in seen:
                seen.add(ch_num)
                merged.append(kr)

        for sr in semantic_results:
            ch_num = sr.get("chapter_number")
            if ch_num is not None and ch_num not in seen and ch_num in ch_map:
                seen.add(ch_num)
                merged.append({
                    "chapter": ch_map[ch_num],
                    "match_context": f"[语义匹配 {sr.get('similarity', 0):.2f}] {sr.get('content', '')[:80]}",
                })

        return merged
