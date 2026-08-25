"""世界观摘要格式化服务（方案 1 改造版）。

职责变更（方案 1）：
- 原职责：格式化 + 信息裁剪（[:N] 切片、字符截断）
- 新职责：仅格式化，不裁剪。数据获取委托 CoreShellService。

裁剪职责已迁移到 CoreShellService。本服务保留 get_digest / get_chapter_context
两个字符串格式化方法，保持调用方向后兼容，便于步骤 5 的渐进迁移。

P1-C4 扩展：
- 新增 format_digest_from_core / format_chapter_context_from_core 两个纯格式化方法。
- 调用方可预先获取 core dict（如通过 UnifiedContextBuilder），再委托本服务格式化，
  避免数据获取路径重复。
- 原有 get_digest / get_chapter_context 改为薄包装：取数 + 委托格式化。
"""
from __future__ import annotations

import logging
from typing import Any

from app.services.core_shell_service import CoreShellService

logger = logging.getLogger(__name__)


class WorldviewDigestService:
    """世界观摘要格式化服务。

    方案 1 后此服务仅负责"如何呈现"，不再负责"呈现多少"。
    Core 层数据全量注入，不裁剪。节省模式请调用方自行使用 CoreShellService.get_compact_core。
    """

    def __init__(self, core_shell: CoreShellService | None = None) -> None:
        self._cs = core_shell or CoreShellService()

    async def get_digest(self, project_id: str, db=None) -> str:
        """格式化完整 Core 层为字符串（不裁剪）。

        保留原 get_digest 接口签名，便于调用方渐进迁移。
        P1-C4：内部委托 format_digest_from_core，数据获取与格式化解耦。
        """
        try:
            core = await self._cs.get_full_core(project_id)
        except Exception as e:
            logger.warning(f"[WorldviewDigest] 获取 Core 数据失败: {e}")
            return ""

        return self.format_digest_from_core(core)

    def format_digest_from_core(self, core: dict[str, Any]) -> str:
        """纯格式化方法：将预取的 Core dict 格式化为完整摘要字符串（不裁剪）。

        P1-C4 新增：调用方可通过 UnifiedContextBuilder 等统一数据层预取 core，
        再委托本方法格式化，避免数据获取路径重复。
        """
        rules = core.get("world_rules", [])
        characters = core.get("characters", [])
        locations = core.get("locations", [])
        foreshadowing_layered = core.get("foreshadowing", {}) or {}

        if not rules and not characters and not locations and not foreshadowing_layered:
            return ""

        parts: list[str] = []
        parts.append("## 世界观约束（推导链组织）")

        critical_rules = [r for r in rules if r.get("priority") == "critical"]
        if critical_rules:
            parts.append("\n### L0 底层铁则（不可违背）")
            for r in critical_rules:
                # 方案 1：去除 description[:100] 截断
                parts.append(f"- **{r.get('name', '')}**：{r.get('description', '')}")
                # 方案 1：去除 constraints[:3] 切片
                for c in r.get("constraints", []):
                    parts.append(f"  - 铁则约束：{c}")

        power_rules = [r for r in rules if r.get("category") in ("magic", "combat", "technology")]
        if power_rules:
            parts.append("\n### L1 核心规则与能力机制推导")
            # 方案 1：去除 power_rules[:5] 切片
            for r in power_rules:
                priority_tag = {"critical": "🔴", "high": "🟡", "normal": "⚪"}.get(r.get("priority", ""), "⚪")
                # 方案 1：去除 description[:80] 截断
                parts.append(f"- {priority_tag} {r.get('name', '')}：{r.get('description', '')}")
                constraints = r.get("constraints", [])
                cost_constraints = [c for c in constraints if any(k in c for k in ["代价", "消耗", "限制", "反噬", "寿命"])]
                if cost_constraints:
                    parts.append(f"  - 代价/限制：{cost_constraints[0]}")
                elif constraints:
                    parts.append(f"  - 约束：{constraints[0]}")

        society_rules = [r for r in rules if r.get("category") == "society"]
        if society_rules:
            parts.append("\n### L2 社会结构（由核心规则+历史推导）")
            # 方案 1：去除 society_rules[:4] 切片
            for r in society_rules:
                # 方案 1：去除 description[:80] 截断
                parts.append(f"- {r.get('name', '')}：{r.get('description', '')}")

        history_rules = [r for r in rules if r.get("category") == "history"]
        if history_rules:
            parts.append("\n### L2 历史因果链")
            # 方案 1：去除 history_rules[:3] 切片
            for r in history_rules:
                # 方案 1：去除 description[:80] 截断
                parts.append(f"- {r.get('name', '')}：{r.get('description', '')}")

        if characters:
            parts.append(f"\n### 人物与核心规则位置（共{len(characters)}人）")
            # 方案 1：去除 characters[:8] 切片
            for c_data in characters:
                name = c_data.get("name", "")
                # 方案 1：去除 desire[:30] / arc[:20] 截断
                desire = c_data.get("desire", "")
                arc = c_data.get("arc", "")
                parts.append(f"- {name} | 欲望：{desire} | 弧光：{arc}")

        if locations:
            parts.append(f"\n### 地理与势力（共{len(locations)}处）")
            # 方案 1：去除 locations[:6] 切片
            for loc in locations:
                parts.append(
                    f"- {loc.get('name', '')}"
                    + (f"（{loc.get('parent_location', '')}）" if loc.get("parent_location") else "")
                    + f" | 氛围：{loc.get('atmosphere', '')}"
                )

        # 伏笔状态分层注入（方案 1 步骤 3）
        active_foreshadowing = foreshadowing_layered.get("active", [])
        resolved_foreshadowing = foreshadowing_layered.get("resolved", [])
        if active_foreshadowing or resolved_foreshadowing:
            parts.append("\n### 伏笔追踪")
            parts.append(
                f"- 待揭示：{len(active_foreshadowing)}条 | 已揭示：{len(resolved_foreshadowing)}条"
            )
            if active_foreshadowing:
                parts.append("- 待揭示（全量注入）：")
                # 方案 1：去除 planted[:5] 切片
                for f in active_foreshadowing:
                    parts.append(
                        f"  - 「{f.get('name', '')}」→ 第{f.get('reveal_window_start', '?')}章揭示"
                    )
            if resolved_foreshadowing:
                parts.append("- 已揭示（摘要注入）：")
                for f in resolved_foreshadowing:
                    resolution_chapter = f.get("resolution_chapter", "?")
                    resolution_summary = f.get("resolution_summary", "")
                    parts.append(
                        f"  - 「{f.get('name', '')}」→ 第{resolution_chapter}章揭示：{resolution_summary}"
                    )

        uncovered = []
        if not critical_rules:
            uncovered.append("底层铁则")
        if not power_rules:
            uncovered.append("核心规则/能力机制")
        if not characters:
            uncovered.append("人物")
        if not locations:
            uncovered.append("地理环境")
        if not society_rules:
            uncovered.append("社会结构")
        if not history_rules:
            uncovered.append("历史因果")
        if uncovered:
            parts.append(f"\n### ⚠️ 未覆盖维度：{'、'.join(uncovered)}")

        return "\n".join(parts)

    async def get_chapter_context(
        self, project_id: str, chapter_number: int, db=None
    ) -> str:
        """格式化章节级 Core 上下文为字符串（不裁剪）。

        保留原 get_chapter_context 接口签名，便于调用方渐进迁移。
        P1-C4：内部委托 format_chapter_context_from_core，数据获取与格式化解耦。
        """
        try:
            core = await self._cs.get_full_core(project_id)
        except Exception as e:
            logger.warning(f"[WorldviewDigest] 获取章节 Core 上下文失败: {e}")
            return ""

        return self.format_chapter_context_from_core(core, chapter_number)

    def format_chapter_context_from_core(self, core: dict[str, Any], chapter_number: int) -> str:
        """纯格式化方法：将预取的 Core dict 格式化为章节级上下文字符串（不裁剪）。

        P1-C4 新增：调用方可通过 UnifiedContextBuilder 等统一数据层预取 core，
        再委托本方法格式化，避免数据获取路径重复。
        """
        rules = core.get("world_rules", [])
        characters = core.get("characters", [])
        locations = core.get("locations", [])
        foreshadowing_layered = core.get("foreshadowing", {}) or {}

        parts: list[str] = [f"## 第{chapter_number}章世界观上下文"]

        critical_rules = [r for r in rules if r.get("priority") == "critical"]
        if critical_rules:
            parts.append("\n### 不可违背的铁则")
            # 方案 1：去除 critical_rules[:3] 切片
            for r in critical_rules:
                # 方案 1：去除 description[:60] 截断
                parts.append(f"- {r.get('name', '')}：{r.get('description', '')}")
                # 方案 1：去除 constraints[:2] 切片
                for c in r.get("constraints", []):
                    parts.append(f"  - {c}")

        power_rules = [r for r in rules if r.get("category") in ("magic", "combat", "technology")]
        if power_rules:
            parts.append("\n### 核心规则约束")
            # 方案 1：去除 power_rules[:3] 切片
            for r in power_rules:
                constraints = r.get("constraints", [])
                cost_constraints = [c for c in constraints if any(k in c for k in ["代价", "消耗", "限制", "反噬", "寿命"])]
                if cost_constraints:
                    parts.append(f"- {r.get('name', '')}：{cost_constraints[0]}")

        if characters:
            parts.append(f"\n### 可出场人物（{len(characters)}人）")
            char_details = []
            # 方案 1：去除 characters[:8] 切片
            for c_data in characters:
                name = c_data.get("name", "")
                # 方案 1：去除 desire[:25] 截断
                desire = c_data.get("desire", "")
                char_details.append(f"{name}(欲:{desire})")
            parts.append("- " + "、".join(char_details))

        if locations:
            parts.append(f"\n### 可用场景（{len(locations)}处）")
            loc_details = []
            # 方案 1：去除 locations[:5] 切片
            for loc in locations:
                name = loc.get("name", "")
                parent = loc.get("parent_location", "")
                label = f"{parent}>{name}" if parent else name
                # 方案 1：去除 atmosphere[:8] 截断
                loc_details.append(f"{label}({loc.get('atmosphere', '')})")
            parts.append("- " + "、".join(loc_details))

        # 伏笔状态分层注入（方案 1 步骤 3）：本章埋设/揭示/近期待收
        active_foreshadowing = foreshadowing_layered.get("active", [])
        if active_foreshadowing:
            to_plant = [
                f for f in active_foreshadowing
                if f.get("bury_window_start") == chapter_number
            ]
            to_reveal = [
                f for f in active_foreshadowing
                if f.get("reveal_window_start") == chapter_number
            ]
            nearby_planted = [
                f for f in active_foreshadowing
                if f.get("reveal_window_start")
                and abs(f.get("reveal_window_start", 0) - chapter_number) <= 2
                and f.get("reveal_window_start") != chapter_number
            ]
            if to_plant:
                names = "、".join(f"「{f.get('name', '')}」" for f in to_plant)
                parts.append(f"\n### ✅ 本章应埋伏笔：{names}")
            if to_reveal:
                names = "、".join(f"「{f.get('name', '')}」" for f in to_reveal)
                parts.append(f"\n### ✅ 本章应收伏笔：{names}")
            if nearby_planted:
                # 方案 1：去除 nearby_planted[:3] 切片
                names = "、".join(
                    f"「{f.get('name', '')}」(第{f.get('reveal_window_start')}章收)"
                    for f in nearby_planted
                )
                parts.append(f"\n### 📌 近期待收伏笔：{names}")

        return "\n".join(parts)
