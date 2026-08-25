"""主编规划器增强服务。

从 Project Brief + TopicDecision + SQLite 故事状态摘要 +
BenchmarkStrategyCards + WritingModeProfile 编译出章级合同。
现有 scene_contract 继续保留，但它应该从更强的章级合同编译而来。
"""
from __future__ import annotations
import json
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.config import settings as app_settings
from app.utils.atomic_file import atomic_write_text
from app.models.chapter_plan_contract import (
    ChapterPlanCompileInput,
    ChapterPlanContract,
    ChapterPlanSequence,
)

logger = logging.getLogger(__name__)


_STORAGE_DIR = Path(app_settings.data_dir) / "chapter_plans"


class EditorPlanningCompiler:
    """主编规划编译器"""

    def __init__(self, storage_dir: str | Path | None = None):
        self._storage_dir = Path(storage_dir) if storage_dir is not None else _STORAGE_DIR
        self._plans: dict[str, ChapterPlanSequence] = {}
        self._load_from_disk()

    def _load_from_disk(self):
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        for path in self._storage_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                plan = ChapterPlanSequence(**data)
                self._plans[plan.project_id] = plan
            except Exception as e:
                logger.warning(f"加载章级规划失败: {e}")

    def _save_plan(self, project_id: str):
        plan = self._plans.get(project_id)
        if not plan:
            return
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        path = self._storage_dir / f"{project_id}.json"
        atomic_write_text(
            path,
            json.dumps(plan.model_dump(), ensure_ascii=False, indent=2),
        )

    async def compile(self, input_data: ChapterPlanCompileInput) -> ChapterPlanSequence:
        """编译章级合同

        流程：
        Project Brief
          + TopicDecision
          + Outline
          + SQLite Story State
          + BenchmarkStrategyCards
          + WritingModeProfile
          -> ChapterPlanContract[]
          -> SceneContract (由 SceneContractCompiler 处理)
        """
        chapters = []

        # 基于大纲数据生成章级合同
        outline = input_data.outline_data
        outline_chapters = outline.get("chapters", [])

        for i in range(input_data.start_chapter, input_data.end_chapter + 1):
            # 尝试从大纲获取信息
            outline_ch = outline_chapters[i - 1] if i - 1 < len(outline_chapters) else {}

            chapter = ChapterPlanContract(
                chapter_number=i,
                chapter_function=self._infer_chapter_function(i, input_data.end_chapter),
                target_emotion=outline_ch.get("target_emotion", ""),
                reader_question_to_open=outline_ch.get("question_to_open", ""),
                reader_question_to_close=outline_ch.get("question_to_close", ""),
                main_conflict=outline_ch.get("main_conflict", ""),
                must_progress=outline_ch.get("must_progress", []),
                must_not_resolve=outline_ch.get("must_not_resolve", []),
                payoff_or_setup=outline_ch.get("payoff_or_setup", "setup" if i <= input_data.end_chapter // 2 else "payoff"),
                character_delta=outline_ch.get("character_delta", []),
                relationship_delta=outline_ch.get("relationship_delta", []),
                foreshadowing_ops=outline_ch.get("foreshadowing_ops", []),
                scene_budget=outline_ch.get("scene_budget", 3),
                word_budget=outline_ch.get("word_budget", 3000),
            )
            chapters.append(chapter)

        # 应用选题决策的影响
        topic = input_data.topic_decision
        if topic and chapters:
            # 开篇章节需要更强的钩子
            if chapters[0].chapter_function == "opening_hook":
                chapters[0].target_emotion = topic.get("core_emotion", chapters[0].target_emotion)

        # 应用对标策略
        for strategy in input_data.benchmark_strategies:
            category = strategy.get("category", "")
            if category == "opening_design" and chapters:
                chapters[0].chapter_function = "opening_hook"
            elif category == "chapter_hook":
                for ch in chapters:
                    if not ch.reader_question_to_open:
                        ch.reader_question_to_open = f"策略建议: {strategy.get('strategy', '')}"

        sequence = ChapterPlanSequence(
            project_id=input_data.project_id,
            chapters=chapters,
            total_chapters=len(chapters),
            created_at=datetime.now().isoformat(),
            source="editor_planning_compiler",
        )

        self._plans[input_data.project_id] = sequence
        self._save_plan(input_data.project_id)
        return sequence

    async def compile_from_decision_context(
        self,
        project_id: str,
        chapter_number: int,
        decision_context,  # EditorDecisionContext
        outline_data: dict | None = None,
    ) -> ChapterPlanContract:
        """从主编决策上下文编译章级合同

        这是主编规划的核心产物，不再是独立 API。
        """
        # 基于决策上下文构建章级合同
        chapter = ChapterPlanContract(chapter_number=chapter_number)

        # 从选题合同获取核心情感
        if decision_context.topic_contract and decision_context.topic_contract.core_emotion:
            chapter.target_emotion = decision_context.topic_contract.core_emotion

        # 从故事状态获取必须延续的事实
        if decision_context.story_state_summary:
            chapter.must_progress = [
                f"延续伏笔: {fs}" for fs in decision_context.story_state_summary.open_foreshadowing[:3]
            ]
            chapter.must_not_resolve = [
                f"保持悬念: {q}" for q in decision_context.story_state_summary.open_questions[:2]
            ]

        # 从对标策略获取推荐节奏
        if decision_context.benchmark_guidance and decision_context.benchmark_guidance.strategy_highlights:
            chapter.foreshadowing_ops = decision_context.benchmark_guidance.strategy_highlights[:3]

        # 从方法论获取约束
        if decision_context.methodology_guidance and decision_context.methodology_guidance.key_rules:
            chapter.character_delta = decision_context.methodology_guidance.key_rules[:2]

        # 从大纲数据补充
        if outline_data:
            outline_chapters = outline_data.get("chapters", [])
            if chapter_number - 1 < len(outline_chapters):
                outline_ch = outline_chapters[chapter_number - 1]
                if not chapter.main_conflict:
                    chapter.main_conflict = outline_ch.get("main_conflict", "")
                if not chapter.reader_question_to_open:
                    chapter.reader_question_to_open = outline_ch.get("question_to_open", "")

        # 推断章节功能
        chapter.chapter_function = self._infer_chapter_function(chapter_number, 10)

        # 保存到计划序列
        plan = self._plans.get(project_id)
        if not plan:
            plan = ChapterPlanSequence(project_id=project_id)
            self._plans[project_id] = plan

        # 替换或追加
        found = False
        for i, ch in enumerate(plan.chapters):
            if ch.chapter_number == chapter_number:
                plan.chapters[i] = chapter
                found = True
                break
        if not found:
            plan.chapters.append(chapter)
        plan.total_chapters = len(plan.chapters)

        self._save_plan(project_id)
        return chapter

    def _infer_chapter_function(self, chapter_num: int, total: int) -> str:
        """推断章节功能"""
        if chapter_num == 1:
            return "opening_hook"
        elif chapter_num <= total * 0.2:
            return "setup"
        elif chapter_num <= total * 0.5:
            return "escalation"
        elif chapter_num <= total * 0.7:
            return "reversal"
        elif chapter_num <= total * 0.9:
            return "payoff"
        else:
            return "climax"

    def get_plan(self, project_id: str) -> Optional[ChapterPlanSequence]:
        return self._plans.get(project_id)

    def get_chapter_plan(self, project_id: str, chapter_number: int) -> Optional[ChapterPlanContract]:
        plan = self._plans.get(project_id)
        if plan:
            for ch in plan.chapters:
                if ch.chapter_number == chapter_number:
                    return ch
        return None

    def update_chapter_plan(self, project_id: str, chapter: ChapterPlanContract) -> Optional[ChapterPlanContract]:
        plan = self._plans.get(project_id)
        if not plan:
            return None
        for i, ch in enumerate(plan.chapters):
            if ch.chapter_number == chapter.chapter_number:
                plan.chapters[i] = chapter
                self._save_plan(project_id)
                return chapter
        return None

    def purge(self, project_id: str, chapter_number: int | None = None) -> int:
        plan = self._plans.get(project_id)
        if plan is None:
            (self._storage_dir / f"{project_id}.json").unlink(missing_ok=True)
            return 0
        if chapter_number is None:
            removed = len(plan.chapters)
            self._plans.pop(project_id, None)
            (self._storage_dir / f"{project_id}.json").unlink(missing_ok=True)
            return removed
        retained = [chapter for chapter in plan.chapters if chapter.chapter_number != chapter_number]
        removed = len(plan.chapters) - len(retained)
        if not removed:
            return 0
        plan.chapters = retained
        if retained:
            self._save_plan(project_id)
        else:
            self._plans.pop(project_id, None)
            (self._storage_dir / f"{project_id}.json").unlink(missing_ok=True)
        return removed


# 全局单例
_planning_compiler: EditorPlanningCompiler | None = None

def get_editor_planning_compiler() -> EditorPlanningCompiler:
    global _planning_compiler
    if _planning_compiler is None:
        _planning_compiler = EditorPlanningCompiler()
    return _planning_compiler
