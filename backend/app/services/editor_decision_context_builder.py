"""主编决策上下文构建器。

核心原则：这些模块不能直接抢主编的方向盘，只能给主编提供结构化证据、约束和复检结果。
上下文必须有 token 预算，不然会撑爆生成链。

各模块职责：
- Market Intelligence: 只提供选题/读者预期/卖点方向，不直接写剧情
- Methodology Adapter: 提供方法论规则卡
- Benchmark Deconstruction: 提供抽象策略，不提供具体作品文本
- Story State Ledger: 提供当前人物状态、伏笔状态、未解决问题、关系变化
- Style Layer: 提供风格边界
- Reader Corpus: 当前默认 off
"""
from __future__ import annotations
import logging
from typing import Optional

from app.models.editor_decision_context import (
    ActiveConstraint,
    BenchmarkGuidanceSummary,
    ChapterDecisionBrief,
    EditorDecisionContext,
    EditorDecisionContextRequest,
    MethodologyGuidanceSummary,
    RiskWarning,
    StoryStateSummary,
    StylePolicy,
    TopicContractSummary,
)

logger = logging.getLogger(__name__)


class EditorDecisionContextBuilder:
    """主编决策上下文构建器"""

    async def build(self, request: EditorDecisionContextRequest) -> EditorDecisionContext:
        """构建主编决策上下文

        从各模块收集信息，整理成一份短小、分层、可裁剪的上下文。
        """
        ctx = EditorDecisionContext(
            project_id=request.project_id,
            chapter_number=request.chapter_number,
            enabled_modules=request.enabled_modules or [],
        )

        remaining_budget = request.max_token_budget
        modules = request.enabled_modules

        # 1. 写作模式
        try:
            writing_mode = await self._get_writing_mode(request.project_id)
            ctx.writing_mode = writing_mode
            remaining_budget -= 20
        except Exception as e:
            logger.debug(f"获取写作模式失败: {e}")

        # 2. 风格策略
        if modules is None or "style" in modules:
            try:
                style_policy = await self._get_style_policy(request.project_id)
                ctx.style_policy = style_policy
                remaining_budget -= 50
            except Exception as e:
                logger.debug(f"获取风格策略失败: {e}")

        # 3. 选题合同
        if modules is None or "market_intelligence" in modules:
            try:
                topic_contract = await self._get_topic_contract(request.project_id)
                ctx.topic_contract = topic_contract
                remaining_budget -= 80
            except Exception as e:
                logger.debug(f"获取选题合同失败: {e}")

        # 4. 方法论指导
        if modules is None or "methodology" in modules:
            try:
                methodology = await self._get_methodology_guidance(remaining_budget)
                ctx.methodology_guidance = methodology
                remaining_budget -= len(methodology.key_rules) * 30
            except Exception as e:
                logger.debug(f"获取方法论指导失败: {e}")

        # 5. 对标指导
        if modules is None or "benchmark" in modules:
            try:
                benchmark = await self._get_benchmark_guidance(request.project_id, remaining_budget)
                ctx.benchmark_guidance = benchmark
                remaining_budget -= len(benchmark.strategy_highlights) * 30
            except Exception as e:
                logger.debug(f"获取对标指导失败: {e}")

        # 6. 故事状态摘要
        if modules is None or "story_state" in modules:
            try:
                state_summary = await self._get_story_state_summary(request.project_id)
                ctx.story_state_summary = state_summary
                remaining_budget -= 200
            except Exception as e:
                logger.debug(f"获取故事状态摘要失败: {e}")

        # 7. 活跃约束
        ctx.active_constraints = self._collect_constraints(ctx)

        # 8. 风险提示
        ctx.risk_warnings = self._collect_risk_warnings(ctx)

        # 计算已使用预算
        ctx.token_budget_used = request.max_token_budget - max(0, remaining_budget)

        return ctx

    def build_brief(self, context: EditorDecisionContext) -> ChapterDecisionBrief:
        """从上下文构建主编 Agent 可读的决策依据"""
        brief = ChapterDecisionBrief()

        # 章级目标
        parts = []
        if context.topic_contract.core_emotion:
            parts.append(f"核心情感: {context.topic_contract.core_emotion}")
        if context.topic_contract.opening_pressure:
            parts.append(f"开篇压力: {context.topic_contract.opening_pressure}")
        brief.chapter_goal = "；".join(parts) if parts else "按大纲推进"

        # 当前人物状态
        chars = context.story_state_summary.active_characters
        brief.character_states = "、".join(chars[:5]) if chars else "无特别状态"

        # 必须延续的事实
        facts = []
        for c in context.active_constraints:
            if c.constraint_type == "fact":
                facts.append(c.description)
        brief.facts_to_continue = facts[:5]

        # 推荐节奏
        if context.benchmark_guidance.strategy_highlights:
            brief.recommended_pacing = context.benchmark_guidance.strategy_highlights[0]
        elif context.topic_contract.reader_expectation:
            brief.recommended_pacing = f"满足{context.topic_contract.reader_expectation}"

        # 信息释放上限
        for c in context.active_constraints:
            if "信息释放" in c.description:
                brief.info_release_limit = c.description
                break

        # 禁止事项
        forbidden = []
        forbidden.extend(context.style_policy.forbidden_patterns[:3])
        for c in context.active_constraints:
            if c.constraint_type == "style":
                forbidden.append(c.description)
        brief.forbidden_items = forbidden[:5]

        # 风格保护
        if context.style_policy.frozen_style_name:
            brief.style_protection = f"保持{context.style_policy.frozen_style_name}风格"
        elif context.style_policy.allowed_styles:
            brief.style_protection = f"允许: {'、'.join(context.style_policy.allowed_styles[:3])}"

        # 可选商业爽点
        hooks = []
        if context.methodology_guidance.key_rules:
            hooks.extend(context.methodology_guidance.key_rules[:2])
        if context.benchmark_guidance.strategy_highlights:
            hooks.extend(context.benchmark_guidance.strategy_highlights[:2])
        brief.commercial_hooks = hooks[:4]

        # 风险提示
        brief.risk_notes = [w.description for w in context.risk_warnings][:3]

        return brief

    # ---- 各模块数据获取 ----

    async def _get_writing_mode(self, project_id: str) -> str:
        """获取写作模式"""
        try:
            from app.services.writing_mode_profile_service import WritingModeProfileService
            service = WritingModeProfileService()
            profile = service.get_profile()
            if profile:
                return profile.label
        except Exception:
            pass
        return ""

    async def _get_style_policy(self, project_id: str) -> StylePolicy:
        """获取风格策略"""
        policy = StylePolicy()
        try:
            from app.db.db_models import async_session, Project
            from sqlalchemy import select
            async with async_session() as db:
                result = await db.execute(select(Project).where(Project.id == project_id))
                project = result.scalar_one_or_none()
                if project and hasattr(project, 'style_profile'):
                    style = project.style_profile
                    if isinstance(style, dict):
                        policy.frozen_style_name = style.get("name", "")
                        policy.allowed_styles = style.get("allowed_patterns", [])
                        policy.forbidden_patterns = style.get("forbidden_patterns", [])
        except Exception as e:
            logger.debug(f"获取风格策略失败: {e}")
        return policy

    async def _get_topic_contract(self, project_id: str) -> TopicContractSummary:
        """获取选题合同"""
        try:
            from app.services.market_intelligence import get_market_intelligence_service
            service = get_market_intelligence_service()
            contract = service.get_topic_contract(project_id)
            if contract:
                return TopicContractSummary(
                    target_platform=contract.target_platform,
                    core_emotion=contract.core_emotion,
                    reader_expectation=contract.reader_expectation,
                    opening_pressure=contract.opening_pressure,
                )
        except Exception as e:
            logger.debug(f"获取选题合同失败: {e}")
        return TopicContractSummary()

    async def _get_methodology_guidance(self, token_budget: int) -> MethodologyGuidanceSummary:
        """获取方法论指导"""
        try:
            from app.services.methodology_adapter import get_methodology_adapter
            adapter = get_methodology_adapter()
            result = adapter.build_guidance(max_token_budget=token_budget)
            return MethodologyGuidanceSummary(
                active_cards=[c.title for c in result.cards],
                key_rules=[r.guidance for r in result.rules[:5]],
            )
        except Exception as e:
            logger.debug(f"获取方法论指导失败: {e}")
        return MethodologyGuidanceSummary()

    async def _get_benchmark_guidance(self, project_id: str, token_budget: int) -> BenchmarkGuidanceSummary:
        """获取对标指导"""
        try:
            from app.services.benchmark_deconstruction import get_benchmark_service
            service = get_benchmark_service()
            cards = service.get_project_strategy_cards(project_id)
            return BenchmarkGuidanceSummary(
                active_benchmarks=list(set(c.book_id for c in cards))[:3],
                strategy_highlights=[f"{c.title}: {c.strategy[:50]}" for c in cards[:5]],
            )
        except Exception as e:
            logger.debug(f"获取对标指导失败: {e}")
        return BenchmarkGuidanceSummary()

    async def _get_story_state_summary(self, project_id: str) -> StoryStateSummary:
        """Compile a bounded summary from canonical SQLite state and tables."""
        try:
            from app.db.db_models import Project, async_session
            from app.services.foreshadowing_service import ForeshadowingService
            from app.services.state_manager import StateManager

            state = await StateManager().get_state(project_id)
            active_characters = []
            for name, entity in state.objective_state.items():
                alive = getattr(entity, "alive", True)
                if alive:
                    active_characters.append(str(name))

            async with async_session() as db:
                project = await db.get(Project, project_id)
                lines = await ForeshadowingService().list_foreshadowing_lines(
                    project_id,
                    db,
                    statuses=["planned", "active", "dormant", "revealing", "revised"],
                )
                lines.sort(
                    key=lambda line: (
                        line.get("status") != "revealing",
                        line.get("latest_safe_reveal_chapter") or line.get("reveal_window_end") or 10**9,
                        str(line.get("name") or ""),
                    )
                )

                open_questions: list[str] = []
                recent_relationships: list[str] = []
                core_data = project.core_data or {} if project else {}
                summaries = core_data.get("chapter_summaries") or {}
                if isinstance(summaries, dict):
                    ordered = sorted(
                        summaries.items(),
                        key=lambda item: int(item[0]) if str(item[0]).isdigit() else -1,
                        reverse=True,
                    )
                    for _, summary in ordered:
                        if not isinstance(summary, dict):
                            continue
                        suspense = summary.get("unsolved_suspense")
                        values = suspense if isinstance(suspense, list) else [suspense]
                        for value in values:
                            text = str(value or "").strip()
                            if text and text not in open_questions:
                                open_questions.append(text)
                        if len(open_questions) >= 8:
                            break

                for character in list(core_data.get("characters") or []):
                    if not isinstance(character, dict):
                        continue
                    relationships = character.get("relationships") or {}
                    if not isinstance(relationships, dict):
                        continue
                    for other, relation in relationships.items():
                        recent_relationships.append(
                            f"{character.get('name', '')}-{other}: {relation}"
                        )
                        if len(recent_relationships) >= 5:
                            break
                    if len(recent_relationships) >= 5:
                        break

            return StoryStateSummary(
                active_characters=active_characters[:10],
                open_foreshadowing=[
                    f"{line.get('name', '')}({line.get('status', '')})" for line in lines[:8]
                ],
                open_questions=open_questions[:8],
                recent_relationships=recent_relationships[:5],
            )
        except Exception as e:
            logger.debug(f"获取故事状态摘要失败: {e}")
        return StoryStateSummary()

    def _collect_constraints(self, ctx: EditorDecisionContext) -> list[ActiveConstraint]:
        """收集所有活跃约束"""
        constraints = []

        # 风格约束
        for pattern in ctx.style_policy.forbidden_patterns:
            constraints.append(ActiveConstraint(
                constraint_type="style",
                description=f"禁止: {pattern}",
                source="style_layer",
            ))

        # 选题约束
        if ctx.topic_contract.opening_pressure:
            constraints.append(ActiveConstraint(
                constraint_type="pacing",
                description=f"开篇压力: {ctx.topic_contract.opening_pressure}",
                source="market_intelligence",
            ))

        # 方法论约束
        for rule in ctx.methodology_guidance.key_rules:
            constraints.append(ActiveConstraint(
                constraint_type="methodology",
                description=rule,
                source="methodology_adapter",
            ))

        # 伏笔约束
        for fs in ctx.story_state_summary.open_foreshadowing:
            constraints.append(ActiveConstraint(
                constraint_type="fact",
                description=f"伏笔待回收: {fs}",
                source="sqlite_story_memory",
            ))

        return constraints

    def _collect_risk_warnings(self, ctx: EditorDecisionContext) -> list[RiskWarning]:
        """收集风险提示"""
        warnings = []

        # 未解问题过多
        if len(ctx.story_state_summary.open_questions) > 5:
            warnings.append(RiskWarning(
                risk_type="consistency",
                description=f"未解问题过多({len(ctx.story_state_summary.open_questions)}个)，注意回收",
                source="sqlite_story_memory",
            ))

        # 对标与风格冲突
        if ctx.benchmark_guidance.strategy_highlights and ctx.style_policy.frozen_style_name:
            warnings.append(RiskWarning(
                risk_type="style_conflict",
                description="对标策略可能与冻结风格冲突，以风格为准",
                source="editor_decision_context_builder",
            ))

        return warnings


# 全局单例
_context_builder: EditorDecisionContextBuilder | None = None

def get_editor_decision_context_builder() -> EditorDecisionContextBuilder:
    global _context_builder
    if _context_builder is None:
        _context_builder = EditorDecisionContextBuilder()
    return _context_builder
