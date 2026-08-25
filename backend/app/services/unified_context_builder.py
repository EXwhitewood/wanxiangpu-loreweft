"""统一上下文架构（方案 2）—— 重新定位为数据层统一获取服务。

定位变迁：
- 原方案 2 设计为"统一 prompt 拼装"，但彻查发现三路（主编/writer/审查官）现实与设计不匹配。
- 现重新定位为"上下文数据层统一获取服务"：只做数据获取，不做 prompt 拼装。
- 三路各自保留协议层拼装，但数据层统一从此处获取。

职责边界：
1. 数据层（本服务）：统一获取章节大纲、前瞻约束、场景合同、风格画像、修复历史等子数据。
2. 协议层（三路各自）：主编/writer/审查官各自保留 prompt 拼装逻辑，但从本服务获取数据。

设计原则：
1. 主编是大脑：规划场景合同，用摘要级前文即可
2. writer 是主力：创作正文，需要上一章完整原文 + 远期前文摘要
3. 审查官是修复大脑：需要 writer 完整上下文 + Issue 列表 + 修复历史
4. 三者共享完整 Core 层（方案 1 保证）

缓存友好（方案 2 步骤 5）：
- stable_prefix：系统persona+固定协议+Skill规则+项目静态摘要（跨章节稳定）
- semi_stable_prefix：风格画像+大纲索引+前章摘要（按章变化但同章内稳定）
- dynamic_suffix：场景合同+候选正文+repair order+review delta（每次变化）

stable prefix 严禁包含：envelope_id（时间戳）、created_at、task_id、trace_id、
execution_round、attempt_count 等动态字段。
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.services.core_shell_service import CoreShellService

if TYPE_CHECKING:
    from app.db.db_models import Project
    from app.services.memory_core import CoreMemoryService
    from app.services.outline_index_service import OutlineIndexService

logger = logging.getLogger(__name__)


@dataclass
class EditorContext:
    """主编上下文。"""
    core: dict[str, Any]
    recent_summaries: list[dict]
    outline: dict[str, Any] = field(default_factory=dict)
    lookahead: dict[str, Any] = field(default_factory=dict)


@dataclass
class WriterContext:
    """writer 上下文。"""
    core: dict[str, Any]
    shell: dict[str, Any]
    outline: dict[str, Any] = field(default_factory=dict)
    contracts: list[dict] = field(default_factory=list)
    style: dict[str, Any] = field(default_factory=dict)


@dataclass
class ReviewerContext:
    """审查官上下文。"""
    writer_context: WriterContext
    candidate_text: str = ""
    issues: list[dict] = field(default_factory=list)
    repair_history: list[dict] = field(default_factory=list)


@dataclass
class PromptLayers:
    """缓存友好的 prompt 三层结构（方案 2 步骤 5）。"""
    stable_prefix: str
    semi_stable_prefix: str
    dynamic_suffix: str

    def to_dict(self) -> dict[str, Any]:
        # P2-1: block_hashes 纳入 to_dict() 输出，避免成为死字段（供方案 28 缓存指标消费）
        return {
            "stable_prefix": self.stable_prefix,
            "semi_stable_prefix": self.semi_stable_prefix,
            "dynamic_suffix": self.dynamic_suffix,
            "block_hashes": self.block_hashes(),
        }

    def block_hashes(self) -> dict[str, str]:
        """各 block 的 sha256 hash 前 16 位（供方案 28 缓存指标使用）。"""
        return {
            "stable_prefix_hash": _sha256_short(self.stable_prefix),
            "semi_stable_hash": _sha256_short(self.semi_stable_prefix),
            "dynamic_suffix_hash": _sha256_short(self.dynamic_suffix),
        }


def _sha256_short(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _stable_json_dumps(data: Any) -> str:
    """稳定的 JSON 序列化（sort_keys=True），保证同一语义内容字面相同。"""
    return json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)


class UnifiedContextBuilder:
    """统一上下文构建器，按角色差异组装上下文。

    重新定位（数据层统一获取服务）：
    - 只做数据获取，不做 prompt 拼装。
    - 三路（主编/writer/审查官）各自保留协议层拼装，但数据层统一从此处获取。
    - 现有 build_*_context 方法保留以兼容存量调用，但内部子数据获取已对接真实服务。

    可注入子服务以便测试替换：
    - core_shell: Core/Shell 数据访问
    - outline_index: 章节大纲索引服务
    - core_memory: 风格画像等 Core 数据服务
    """

    def __init__(
        self,
        core_shell: CoreShellService | None = None,
        outline_index: OutlineIndexService | None = None,
        core_memory: CoreMemoryService | None = None,
    ) -> None:
        self._cs = core_shell or CoreShellService()
        self._outline_index = outline_index
        self._core_memory = core_memory

    # ----------------------------------------------------------- 主编上下文

    async def build_editor_context(
        self,
        project_id: str,
        chapter_num: int,
        db=None,
    ) -> EditorContext:
        """主编上下文：完整 Core + 前文摘要 + 章节大纲 + 前瞻约束。

        主编是大脑，规划场景合同，用摘要级前文即可，不需要前文原文。
        """
        core = await self._cs.get_full_core(project_id)
        recent_summaries = await self._cs._get_recent_summaries(
            project_id, chapter_num, count=10, db=db
        )
        outline = await self._get_chapter_outline(project_id, chapter_num, db=db)
        lookahead = await self._get_lookahead_constraints(project_id, chapter_num, db=db)

        return EditorContext(
            core=core,
            recent_summaries=recent_summaries,
            outline=outline,
            lookahead=lookahead,
        )

    # ---------------------------------------------------------- writer 上下文

    async def build_writer_context(
        self,
        project_id: str,
        chapter_num: int,
        db=None,
    ) -> WriterContext:
        """writer 上下文：完整 Core + 上一章原文 + 前6章摘要 + 大纲 + 合同。

        writer 是主力，创作正文，需要上一章完整原文衔接叙事节奏。
        """
        core = await self._cs.get_full_core(project_id)
        shell = await self._cs.get_full_shell(project_id, chapter_num, db=db)
        outline = await self._get_chapter_outline(project_id, chapter_num, db=db)
        contracts = await self._get_scene_contracts(project_id, chapter_num, db=db)
        style = await self._get_style_constraints(project_id)

        return WriterContext(
            core=core,
            shell=shell,
            outline=outline,
            contracts=contracts,
            style=style,
        )

    # -------------------------------------------------------- 审查官上下文

    async def build_reviewer_context(
        self,
        project_id: str,
        chapter_num: int,
        candidate_text: str,
        issues: list[dict],
        db=None,
    ) -> ReviewerContext:
        """审查官上下文：writer 上下文 + 候选正文 + Issue 列表 + 修复历史。

        审查官是修复大脑，需要 writer 完整上下文 + Issue 列表 + 修复历史。
        """
        writer_ctx = await self.build_writer_context(project_id, chapter_num, db=db)
        repair_history = await self._get_repair_history(project_id, chapter_num, db=db)

        return ReviewerContext(
            writer_context=writer_ctx,
            candidate_text=candidate_text,
            issues=issues,
            repair_history=repair_history,
        )

    # --------------------------------------------------- 缓存友好的 prompt 分层

    def build_prompt_layers(self, ctx: EditorContext | WriterContext | ReviewerContext) -> PromptLayers:
        """输出三层结构，stable prefix 在前，dynamic suffix 在后。

        方案 2 步骤 5：为方案 28（API 缓存命中率优化）预留接口。
        stable prefix 跨章节稳定，semi_stable 按章稳定，dynamic_suffix 每次变化。
        """
        if isinstance(ctx, EditorContext):
            return self._build_editor_prompt_layers(ctx)
        if isinstance(ctx, WriterContext):
            return self._build_writer_prompt_layers(ctx)
        if isinstance(ctx, ReviewerContext):
            return self._build_reviewer_prompt_layers(ctx)
        raise TypeError(f"Unsupported context type: {type(ctx)}")

    def _build_editor_prompt_layers(self, ctx: EditorContext) -> PromptLayers:
        """主编 prompt 三层结构。"""
        # stable_prefix：项目级静态信息（跨章节稳定）
        stable_parts = [
            "### 世界观 Core 层（绝对真理）",
            self._format_core_stable(ctx.core),
        ]
        # semi_stable：按章变化但同章内稳定
        semi_stable_parts = [
            "### 前文摘要（最近10章）",
            self._format_summaries(ctx.recent_summaries),
            "### 章节大纲",
            _stable_json_dumps(ctx.outline),
            "### 前瞻约束",
            _stable_json_dumps(ctx.lookahead),
        ]
        # dynamic_suffix：每次变化（主编的执行轮次、动态指令等）
        dynamic_parts = [
            "### 动态指令",
            "请基于上述稳定信息规划本章场景合同。",
        ]
        return PromptLayers(
            stable_prefix="\n".join(stable_parts),
            semi_stable_prefix="\n".join(semi_stable_parts),
            dynamic_suffix="\n".join(dynamic_parts),
        )

    def _build_writer_prompt_layers(self, ctx: WriterContext) -> PromptLayers:
        """writer prompt 三层结构。"""
        stable_parts = [
            "### 世界观 Core 层（绝对真理）",
            self._format_core_stable(ctx.core),
            "### 风格约束",
            _stable_json_dumps(ctx.style),
        ]
        semi_stable_parts = [
            "### 章节大纲",
            _stable_json_dumps(ctx.outline),
            "### 上一章完整原文",
            ctx.shell.get("previous_chapter_text") or "(无上一章原文)",
            "### 前文摘要（最近6章）",
            self._format_summaries(ctx.shell.get("recent_summaries", [])),
        ]
        dynamic_parts = [
            "### 场景合同",
            _stable_json_dumps(ctx.contracts),
            "### Detail Seeds（相关细节）",
            _stable_json_dumps(ctx.shell.get("detail_seeds", [])),
            "### 已确立事实",
            _stable_json_dumps(ctx.shell.get("established_facts", [])),
        ]
        return PromptLayers(
            stable_prefix="\n".join(stable_parts),
            semi_stable_prefix="\n".join(semi_stable_parts),
            dynamic_suffix="\n".join(dynamic_parts),
        )

    def _build_reviewer_prompt_layers(self, ctx: ReviewerContext) -> PromptLayers:
        """审查官 prompt 三层结构。

        审查官的 stable_prefix 复用 writer 的 stable_prefix（保证缓存命中）。
        """
        writer_layers = self._build_writer_prompt_layers(ctx.writer_context)
        dynamic_parts = [
            "### 候选正文",
            ctx.candidate_text,
            "### Issue 列表",
            _stable_json_dumps(ctx.issues),
            "### 修复历史",
            _stable_json_dumps(ctx.repair_history),
        ]
        return PromptLayers(
            stable_prefix=writer_layers.stable_prefix,
            semi_stable_prefix=writer_layers.semi_stable_prefix,
            dynamic_suffix="\n".join(dynamic_parts),
        )

    # ----------------------------------------------------------- 格式化辅助

    def _format_core_stable(self, core: dict[str, Any]) -> str:
        """格式化 Core 层为稳定字符串（排序后保证字面稳定）。"""
        return _stable_json_dumps({
            "world_rules": sorted(
                core.get("world_rules", []),
                key=lambda r: (r.get("priority", ""), r.get("name", "")),
            ),
            "characters": sorted(
                core.get("characters", []),
                key=lambda c: c.get("name", ""),
            ),
            "locations": sorted(
                core.get("locations", []),
                key=lambda l: l.get("name", ""),
            ),
            "foreshadowing": core.get("foreshadowing", {}),
            "items": sorted(
                core.get("items", []),
                key=lambda i: i.get("name", ""),
            ),
        })

    def _format_summaries(self, summaries: list[dict]) -> str:
        """格式化摘要列表为稳定字符串。"""
        if not summaries:
            return "(无前文摘要)"
        sorted_summaries = sorted(
            summaries,
            key=lambda s: s.get("chapter_number", 0),
            reverse=True,
        )
        return _stable_json_dumps(sorted_summaries)

    # ------------------------------------------------------- 子数据获取（数据层）

    async def _get_chapter_outline(
        self, project_id: str, chapter_num: int, db=None
    ) -> dict[str, Any]:
        """获取章节大纲详情。

        数据层统一获取服务：对接 OutlineIndexService.get_chapter_detail。
        需要 db 参数；db 为 None 时返回最小占位结构（保持向后兼容）。
        """
        if db is None:
            return {"chapter_number": chapter_num, "_note": "db_not_provided"}

        try:
            from app.services.outline_index_service import OutlineIndexService

            service = self._outline_index or OutlineIndexService()
            detail = await service.get_chapter_detail(
                uuid.UUID(project_id), chapter_num, db
            )
            if detail is None:
                return {
                    "chapter_number": chapter_num,
                    "_note": "chapter_not_found_in_outline",
                }
            return detail
        except Exception as e:
            logger.warning(
                "[UnifiedContextBuilder] _get_chapter_outline 失败: %s", e
            )
            return {"chapter_number": chapter_num, "_note": f"error: {e}"}

    async def _get_lookahead_constraints(
        self, project_id: str, chapter_num: int, db=None
    ) -> dict[str, Any]:
        """获取前瞻约束（后续章节需求）。

        数据层统一获取服务：从 project.outline_data 提取当前章后 3 章的关键约束。
        逻辑参考 editor_in_chief.py 的 _build_layer5（行 912-955）。
        """
        if db is None:
            return {"chapter_number": chapter_num, "_note": "db_not_provided"}

        try:
            from app.db.db_models import Project

            project = await db.get(Project, uuid.UUID(project_id))
            if not project:
                return {
                    "chapter_number": chapter_num,
                    "_note": "project_not_found",
                }

            outline = project.outline_data or {}
            chapters = outline.get("chapters", [])
            if not chapters:
                return {
                    "chapter_number": chapter_num,
                    "_note": "no_chapters_in_outline",
                }

            # 找到当前章索引
            current_idx = None
            for i, ch in enumerate(chapters):
                if ch.get("chapter_number") == chapter_num:
                    current_idx = i
                    break

            if current_idx is None:
                return {
                    "chapter_number": chapter_num,
                    "_note": "current_chapter_not_in_outline",
                }

            # 取后 3 章
            future_chapters = chapters[current_idx + 1 : current_idx + 4]
            if not future_chapters:
                return {
                    "chapter_number": chapter_num,
                    "future_chapters": [],
                }

            # 提取关键约束字段（与 editor_in_chief._build_layer5 一致）
            constraints = []
            for fc in future_chapters:
                fc_conflict = fc.get(
                    "main_conflict", fc.get("core_conflict", "")
                )
                reveal_actions = [
                    op
                    for op in fc.get("thread_ops", [])
                    if isinstance(op, dict) and op.get("op") == "reveal"
                ]
                reveal_names = [
                    f.get("thread_id", "") for f in reveal_actions
                ]
                constraints.append(
                    {
                        "chapter_number": fc.get("chapter_number", "?"),
                        "title": fc.get("title", ""),
                        "main_conflict": fc_conflict,
                        "pov_character": fc.get("pov_character", ""),
                        "reveal_actions": reveal_names,
                    }
                )

            return {
                "chapter_number": chapter_num,
                "future_chapters": constraints,
            }
        except Exception as e:
            logger.warning(
                "[UnifiedContextBuilder] _get_lookahead_constraints 失败: %s", e
            )
            return {"chapter_number": chapter_num, "_note": f"error: {e}"}

    async def _get_scene_contracts(
        self, project_id: str, chapter_num: int, db=None
    ) -> list[dict]:
        """获取场景合同列表。

        数据层统一获取服务：从 project.core_data 获取已生成的场景合同。
        场景合同由主编产出后存储于 core_data.scene_contracts（如有）。
        """
        if db is None:
            return []

        try:
            from app.db.db_models import Project

            project = await db.get(Project, uuid.UUID(project_id))
            if not project:
                return []

            core_data = project.core_data or {}
            # 优先查 core_data.scene_contracts
            contracts = core_data.get("scene_contracts", [])
            if not isinstance(contracts, list):
                return []

            # 过滤当前章节的合同
            chapter_contracts = [
                c
                for c in contracts
                if isinstance(c, dict) and c.get("chapter_number") == chapter_num
            ]

            # 按章节过滤后为空但有合同数据时，返回全部（兼容无 chapter_number 字段的旧数据）
            if not chapter_contracts and contracts:
                return list(contracts)
            return chapter_contracts
        except Exception as e:
            logger.warning(
                "[UnifiedContextBuilder] _get_scene_contracts 失败: %s", e
            )
            return []

    async def _get_style_constraints(self, project_id: str) -> dict[str, Any]:
        """获取激活的风格画像。

        数据层统一获取服务：对接 memory_core.get_active_style_profile。
        不需要 db 参数（CoreMemoryService 内部管理会话）。
        """
        try:
            from app.services.memory_core import CoreMemoryService

            service = self._core_memory or CoreMemoryService()
            profile = await service.get_active_style_profile(project_id)
            if profile is None:
                return {}
            return profile
        except Exception as e:
            logger.warning(
                "[UnifiedContextBuilder] _get_style_constraints 失败: %s", e
            )
            return {}

    async def _get_repair_history(
        self, project_id: str, chapter_num: int, db=None
    ) -> list[dict]:
        """获取修复历史（DB 兜底）。

        ⚠️ 注意：审查官运行时的 repair_history 应优先从 context 传入
        （case_file["repair_plan"] 是运行时对象，非 DB 持久化数据）。
        此方法仅作为 DB 兜底，从 project.core_data 查询历史修复记录。
        """
        if db is None:
            return []

        try:
            from app.db.db_models import Project

            project = await db.get(Project, uuid.UUID(project_id))
            if not project:
                return []

            core_data = project.core_data or {}
            # 尝试从 core_data 获取历史修复记录
            repair_history = core_data.get("repair_history", [])
            if not isinstance(repair_history, list):
                return []

            # 过滤当前章节的修复记录
            return [
                r
                for r in repair_history
                if isinstance(r, dict) and r.get("chapter_number") == chapter_num
            ]
        except Exception as e:
            logger.warning(
                "[UnifiedContextBuilder] _get_repair_history 失败: %s", e
            )
            return []
