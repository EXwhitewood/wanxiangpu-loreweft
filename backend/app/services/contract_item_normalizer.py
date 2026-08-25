"""ContractItem 归一化器。

将现有各种合同字段（must_show、forbidden、foreshadowing_ops 等）
统一转换为 ContractItem，建立来源、优先级、可见性等语义。

映射规则（来自重构方案）：
| 来源 | 旧字段 | 新 obligation_type |
|---|---|---|
| 大纲事实 | goal/conflict/outcome | chapter_goal 或 current_scene_must |
| 原始 must_show | must_show | current_scene_must / soft_hint |
| 伏笔 | foreshadowing_ops | carry_forward / current_scene_must |
| 禁止项 | forbidden | forbidden |
| SQLite 状态摘要已知事实 | active_characters | hard_fact |
| SQLite 状态摘要未解问题 | open_questions | carry_forward |
| 方法论 | key_rules | advisory / quality_check_only |
| 对标 | strategies | advisory / soft_hint |
| 情报 | trend | editor_only |
| 风格层 | style_profile | style_policy |
"""
from __future__ import annotations

import hashlib
import logging

from app.models.contract_item import ContractItem

logger = logging.getLogger(__name__)


class ContractItemNormalizer:
    """ContractItem 归一化器"""

    @staticmethod
    def _scene_key(scene_contract: dict, fallback_index: int | str | None = None) -> str:
        """返回与 SceneAllocationCompiler 一致的场景键。

        旧合同里 scene_index 可能是 int、"0"、"scene_0"，也可能缺失。
        V2 分配层统一使用 scene_N，避免不同格式导致场景归属丢失。
        """
        raw = scene_contract.get("scene_index", fallback_index)
        if raw is None:
            raw = 0
        raw_text = str(raw)
        if raw_text.startswith("scene_"):
            return raw_text
        return f"scene_{raw_text}"

    def normalize_chapter_plan(self, chapter_plan: dict) -> list[ContractItem]:
        """从 ChapterPlanContract 归一化为 ContractItem 列表"""
        items = []

        # target_emotion → chapter_goal
        if chapter_plan.get("target_emotion"):
            items.append(ContractItem(
                id=self._make_id("chapter_plan", "target_emotion", chapter_plan["target_emotion"]),
                text=chapter_plan["target_emotion"],
                source="chapter_plan",
                layer="emotion",
                obligation_type="chapter_goal",
                priority="high",
                certainty="confirmed",
                visibility="writer_visible",
            ))

        # chapter_function → chapter_goal
        if chapter_plan.get("chapter_function"):
            items.append(ContractItem(
                id=self._make_id("chapter_plan", "chapter_function", chapter_plan["chapter_function"]),
                text=f"章节功能: {chapter_plan['chapter_function']}",
                source="chapter_plan",
                layer="plot",
                obligation_type="chapter_goal",
                priority="high",
                certainty="confirmed",
                visibility="writer_visible",
            ))

        # main_conflict → chapter_goal
        if chapter_plan.get("main_conflict"):
            items.append(ContractItem(
                id=self._make_id("chapter_plan", "main_conflict", chapter_plan["main_conflict"]),
                text=chapter_plan["main_conflict"],
                source="chapter_plan",
                layer="plot",
                obligation_type="chapter_goal",
                priority="high",
                certainty="confirmed",
                visibility="writer_visible",
            ))

        # must_progress → carry_forward (章级，不直接分配到场景)
        for prog in chapter_plan.get("must_progress", []):
            if isinstance(prog, str) and prog:
                items.append(ContractItem(
                    id=self._make_id("chapter_plan", "must_progress", prog),
                    text=prog,
                    source="chapter_plan",
                    layer="plot",
                    obligation_type="carry_forward",
                    priority="medium",
                    certainty="likely",
                    visibility="editor_only",
                ))

        # must_not_resolve → carry_forward
        for nr in chapter_plan.get("must_not_resolve", []):
            if isinstance(nr, str) and nr:
                items.append(ContractItem(
                    id=self._make_id("chapter_plan", "must_not_resolve", nr),
                    text=f"不得解决: {nr}",
                    source="chapter_plan",
                    layer="plot",
                    obligation_type="carry_forward",
                    priority="medium",
                    certainty="likely",
                    visibility="editor_only",
                ))

        # foreshadowing_ops → carry_forward
        for fs in chapter_plan.get("foreshadowing_ops", []):
            if isinstance(fs, str) and fs:
                items.append(ContractItem(
                    id=self._make_id("chapter_plan", "foreshadowing", fs),
                    text=fs,
                    source="chapter_plan",
                    layer="foreshadowing",
                    obligation_type="carry_forward",
                    priority="medium",
                    certainty="likely",
                    visibility="editor_only",
                ))

        # reader_question_to_open → chapter_goal
        if chapter_plan.get("reader_question_to_open"):
            items.append(ContractItem(
                id=self._make_id("chapter_plan", "reader_question", chapter_plan["reader_question_to_open"]),
                text=f"读者问题: {chapter_plan['reader_question_to_open']}",
                source="chapter_plan",
                layer="pacing",
                obligation_type="chapter_goal",
                priority="medium",
                certainty="likely",
                visibility="writer_visible",
            ))

        return items

    def normalize_scene_contract(
        self,
        scene_contract: dict,
        scene_index: int | str | None = None,
    ) -> list[ContractItem]:
        """从场景合同归一化为 ContractItem 列表"""
        items = []
        scene_key = self._scene_key(scene_contract, scene_index)

        # must_show → current_scene_must 或 soft_hint
        must_show = scene_contract.get("must_show", [])
        if isinstance(must_show, list):
            for i, ms in enumerate(must_show):
                if isinstance(ms, str) and ms:
                    is_critical = i < 3  # 前3条为 must，之后为 soft_hint
                    items.append(ContractItem(
                        id=self._make_id("scene", f"{scene_key}:must_show", ms),
                        text=ms,
                        source="scene_plan",
                        layer="plot",
                        obligation_type="current_scene_must" if is_critical else "current_scene_soft_hint",
                        priority="high" if is_critical else "medium",
                        certainty="confirmed",
                        visibility="writer_visible",
                        scene_scope=[scene_key],
                        due_scene_id=scene_key,
                    ))

        # forbidden → forbidden
        forbidden = scene_contract.get("forbidden", [])
        if isinstance(forbidden, list):
            for fb in forbidden:
                if isinstance(fb, str) and fb:
                    items.append(ContractItem(
                        id=self._make_id("scene", f"{scene_key}:forbidden", fb),
                        text=fb,
                        source="scene_plan",
                        layer="safety",
                        obligation_type="forbidden",
                        priority="high",
                        certainty="confirmed",
                        visibility="writer_visible",
                        scene_scope=[scene_key],
                        due_scene_id=scene_key,
                    ))

        # ending_state → current_scene_must
        if scene_contract.get("ending_state"):
            items.append(ContractItem(
                id=self._make_id("scene", f"{scene_key}:ending_state", scene_contract["ending_state"]),
                text=f"结尾状态: {scene_contract['ending_state']}",
                source="scene_plan",
                layer="plot",
                obligation_type="current_scene_must",
                priority="high",
                certainty="confirmed",
                visibility="writer_visible",
                scene_scope=[scene_key],
                due_scene_id=scene_key,
            ))

        # pov_character → hard_fact
        if scene_contract.get("pov_character"):
            items.append(ContractItem(
                id=self._make_id("scene", f"{scene_key}:pov", scene_contract["pov_character"]),
                text=f"视角人物: {scene_contract['pov_character']}",
                source="scene_plan",
                layer="character",
                obligation_type="hard_fact",
                priority="critical",
                certainty="confirmed",
                visibility="writer_visible",
                scene_scope=[scene_key],
                due_scene_id=scene_key,
            ))

        # source_of_truth 中的 must_show_outline → current_scene_must
        sot = scene_contract.get("source_of_truth", {})
        if isinstance(sot, dict):
            for ms in sot.get("must_show_outline", []):
                if isinstance(ms, str) and ms:
                    items.append(ContractItem(
                        id=self._make_id("scene", f"{scene_key}:must_show_outline", ms),
                        text=ms,
                        source="outline",
                        layer="plot",
                        obligation_type="current_scene_must",
                        priority="high",
                        certainty="confirmed",
                        visibility="writer_visible",
                        scene_scope=[scene_key],
                        due_scene_id=scene_key,
                    ))
            for fb in sot.get("forbidden_outline", []):
                if isinstance(fb, str) and fb:
                    items.append(ContractItem(
                        id=self._make_id("scene", f"{scene_key}:forbidden_outline", fb),
                        text=fb,
                        source="outline",
                        layer="safety",
                        obligation_type="forbidden",
                        priority="high",
                        certainty="confirmed",
                        visibility="writer_visible",
                        scene_scope=[scene_key],
                        due_scene_id=scene_key,
                    ))

        return items

    def normalize_state_summary(self, state_summary: dict) -> list[ContractItem]:
        """从 SQLite 故事状态摘要归一化为 ContractItem 列表"""
        items = []

        # 已知实体 → hard_fact
        for name in state_summary.get("active_characters", []):
            if isinstance(name, str) and name:
                items.append(ContractItem(
                    id=self._make_id("state", "character", name),
                    text=f"活跃角色: {name}",
                    source="sqlite_story_memory",
                    layer="character",
                    obligation_type="hard_fact",
                    priority="medium",
                    certainty="confirmed",
                    visibility="writer_visible",
                ))

        # 未解问题 → carry_forward
        for q in state_summary.get("open_questions", []):
            if isinstance(q, str) and q:
                items.append(ContractItem(
                    id=self._make_id("state", "question", q),
                    text=f"未解问题: {q}",
                    source="sqlite_story_memory",
                    layer="plot",
                    obligation_type="carry_forward",
                    priority="low",
                    certainty="likely",
                    visibility="editor_only",
                ))

        # 伏笔 → carry_forward
        for fs in state_summary.get("open_foreshadowing", []):
            if isinstance(fs, str) and fs:
                items.append(ContractItem(
                    id=self._make_id("state", "foreshadowing", fs),
                    text=f"待回收伏笔: {fs}",
                    source="sqlite_story_memory",
                    layer="foreshadowing",
                    obligation_type="carry_forward",
                    priority="medium",
                    certainty="likely",
                    visibility="editor_only",
                ))

        return items

    def normalize_methodology(self, rules: list[str]) -> list[ContractItem]:
        """从方法论规则归一化为 ContractItem 列表"""
        items = []
        for rule in rules:
            if isinstance(rule, str) and rule:
                items.append(ContractItem(
                    id=self._make_id("methodology", "rule", rule),
                    text=rule,
                    source="methodology",
                    layer="literary",
                    obligation_type="advisory",
                    priority="low",
                    certainty="likely",
                    visibility="quality_only",
                ))
        return items

    def normalize_benchmark(self, strategies: list[str]) -> list[ContractItem]:
        """从对标策略归一化为 ContractItem 列表"""
        items = []
        for strategy in strategies:
            if isinstance(strategy, str) and strategy:
                items.append(ContractItem(
                    id=self._make_id("benchmark", "strategy", strategy),
                    text=strategy,
                    source="benchmark",
                    layer="pacing",
                    obligation_type="advisory",
                    priority="low",
                    certainty="inferred",
                    visibility="editor_only",
                ))
        return items

    def normalize_all(
        self,
        chapter_plan: dict | None = None,
        scene_contracts: list[dict] | None = None,
        story_state_summary: dict | None = None,
        methodology_rules: list[str] | None = None,
        benchmark_strategies: list[str] | None = None,
    ) -> list[ContractItem]:
        """归一化所有来源的合同信息"""
        items = []

        if chapter_plan:
            items.extend(self.normalize_chapter_plan(chapter_plan))
        if scene_contracts:
            for idx, sc in enumerate(scene_contracts):
                items.extend(self.normalize_scene_contract(sc, scene_index=idx))
        if story_state_summary:
            items.extend(self.normalize_state_summary(story_state_summary))
        if methodology_rules:
            items.extend(self.normalize_methodology(methodology_rules))
        if benchmark_strategies:
            items.extend(self.normalize_benchmark(benchmark_strategies))

        return items

    @staticmethod
    def _make_id(source: str, field: str, text: str) -> str:
        """生成唯一 ID"""
        raw = f"{source}:{field}:{text[:100]}"
        return hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]


# 全局单例
_normalizer: ContractItemNormalizer | None = None


def get_contract_item_normalizer() -> ContractItemNormalizer:
    global _normalizer
    if _normalizer is None:
        _normalizer = ContractItemNormalizer()
    return _normalizer
