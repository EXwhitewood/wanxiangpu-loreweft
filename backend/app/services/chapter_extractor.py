"""章节结构抽取服务。

从已有正文或对标正文中抽取结构资产，包括：
- 章节摘要
- 情节点
- 角色提及与状态变化
- 伏笔操作
- 时间线事件
- 钩子点
- 信息释放点

使用位置：
1. 手动章节结构分析与质量复检
2. 对标拆解时构建 benchmark assets
3. FBI 修复后复检是否破坏剧情功能
4. 情报/质量页面展示章节结构
"""
from __future__ import annotations
import json
import logging
import re
from typing import Optional

from app.models.chapter_structure import (
    ChapterExtractionInput,
    ChapterExtractionResult,
    CharacterMention,
    ForeshadowingOp,
    HookPoint,
    PlotEvent,
    TimelineEvent,
)
from app.services.llm_task_profiles import LLMTaskType

logger = logging.getLogger(__name__)


class ChapterExtractor:
    """章节结构抽取器"""

    def __init__(self):
        self._llm_client = None

    async def _get_llm_client(self):
        """延迟加载LLM客户端"""
        if self._llm_client is None:
            from app.services.agent_config import AgentConfigManager
            from app.services.llm_client import LLMClient

            manager = AgentConfigManager()
            config = await manager.get_agent_config("outline_architect")
            self._llm_client = LLMClient(
                api_format=config.api_format,
                api_key=config.api_key,
                base_url=config.base_url,
                model=config.model,
            )
        return self._llm_client

    async def extract(self, input_data: ChapterExtractionInput) -> ChapterExtractionResult:
        """抽取章节结构

        Args:
            input_data: 抽取输入

        Returns:
            抽取结果
        """
        if not input_data.chapter_text.strip():
            return ChapterExtractionResult(
                chapter_number=input_data.chapter_number,
                extraction_quality="low",
                extraction_notes="章节正文为空",
            )

        # 先做确定性抽取（不需要LLM）
        result = self._deterministic_extract(input_data)

        # 再做LLM增强抽取
        try:
            llm_result = await self._llm_extract(input_data)
            result = self._merge_results(result, llm_result)
        except Exception as e:
            logger.warning(f"LLM增强抽取失败，仅使用确定性结果: {e}")
            result.extraction_notes = f"LLM增强抽取失败: {e}"

        return result

    def _deterministic_extract(self, input_data: ChapterExtractionInput) -> ChapterExtractionResult:
        """确定性抽取（不依赖LLM）"""
        text = input_data.chapter_text
        result = ChapterExtractionResult(chapter_number=input_data.chapter_number)

        # 1. 提取已知实体提及
        for entity in input_data.known_entities:
            if entity in text:
                result.character_mentions.append(
                    CharacterMention(name=entity, action="mentioned")
                )

        # 2. 提取已知术语提及
        for term in input_data.known_terms:
            if term in text:
                result.setting_mentions.append(term)

        # 3. 基于段落数估算情绪曲线
        paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
        if paragraphs:
            # 简单的基于段落长度和标点的情绪估算
            for i, para in enumerate(paragraphs):
                if "！" in para or "？" in para:
                    result.emotion_curve.append("tense")
                elif "…" in para or "——" in para:
                    result.emotion_curve.append("reflective")
                elif any(c in para for c in ["笑", "喜", "乐"]):
                    result.emotion_curve.append("positive")
                elif any(c in para for c in ["怒", "恨", "悲"]):
                    result.emotion_curve.append("negative")
                else:
                    result.emotion_curve.append("neutral")

        # 4. 提取时间标记
        time_patterns = [
            r'第[一二三四五六七八九十百千万]+天',
            r'[一二三四五六七八九十]+月[一二三四五六七八九十]+[日号]',
            r'\d+年\d+月\d+日',
            r'(早上|上午|中午|下午|傍晚|晚上|深夜|凌晨)',
            r'(三天后|一周后|半月后|一个月后)',
        ]
        for pattern in time_patterns:
            matches = re.findall(pattern, text)
            for match in matches:
                result.timeline_events.append(
                    TimelineEvent(time_marker=match, event="", location="")
                )

        result.extraction_quality = "medium"
        return result

    async def _llm_extract(self, input_data: ChapterExtractionInput) -> ChapterExtractionResult:
        """LLM增强抽取"""
        llm = await self._get_llm_client()

        prompt = f"""请分析以下小说章节内容，抽取结构化信息。

章节编号: {input_data.chapter_number}
已知实体: {', '.join(input_data.known_entities) if input_data.known_entities else '无'}
已知术语: {', '.join(input_data.known_terms) if input_data.known_terms else '无'}

章节正文:
{input_data.chapter_text[:8000]}

请以JSON格式输出以下信息：
{{
  "chapter_summary": "100字以内的章节摘要",
  "plot_events": [
    {{"description": "事件描述", "event_type": "action|revelation|decision|conflict|resolution|twist", "characters_involved": ["角色名"], "impact_level": "major|moderate|minor"}}
  ],
  "character_mentions": [
    {{"name": "角色名", "action": "行为", "state_change": "状态变化", "emotion": "情绪"}}
  ],
  "state_changes": ["状态变化1", "状态变化2"],
  "foreshadowing_ops": [
    {{"operation": "plant|advance|payoff|abandon", "clue_id": "", "description": "描述"}}
  ],
  "hook_points": [
    {{"position": "opening|mid|closing", "hook_type": "question|mystery|conflict|reversal|promise", "content": "钩子内容"}}
  ],
  "information_release_points": ["释放的信息1"]
}}

注意：
1. 只抽取明确出现在文本中的信息，不要推断
2. 伏笔操作要基于文本证据，不要猜测
3. 情节点的event_type要准确分类"""

        try:
            response = await llm.generate(
                system_prompt="你是小说章节结构抽取器。只输出合法 JSON，不要添加解释。",
                user_prompt=prompt,
                temperature=0.2,
                task_type=LLMTaskType.SHORT_EXTRACTION,
                response_format={"type": "json_object"},
            )
            # 解析LLM返回的JSON
            content = response.strip()
            # 尝试提取JSON部分
            json_match = re.search(r'\{[\s\S]*\}', content)
            if json_match:
                data = json.loads(json_match.group())
                return ChapterExtractionResult(
                    chapter_number=input_data.chapter_number,
                    chapter_summary=data.get("chapter_summary", ""),
                    plot_events=[PlotEvent(**e) for e in data.get("plot_events", [])],
                    character_mentions=[CharacterMention(**c) for c in data.get("character_mentions", [])],
                    state_changes=data.get("state_changes", []),
                    foreshadowing_ops=[ForeshadowingOp(**f) for f in data.get("foreshadowing_ops", [])],
                    hook_points=[HookPoint(**h) for h in data.get("hook_points", [])],
                    information_release_points=data.get("information_release_points", []),
                    extraction_quality="high",
                )
        except Exception as e:
            logger.warning(f"LLM抽取解析失败: {e}")

        return ChapterExtractionResult(
            chapter_number=input_data.chapter_number,
            extraction_quality="low",
            extraction_notes="LLM解析失败",
        )

    def _merge_results(
        self,
        deterministic: ChapterExtractionResult,
        llm_result: ChapterExtractionResult,
    ) -> ChapterExtractionResult:
        """合并确定性结果和LLM结果"""
        merged = ChapterExtractionResult(
            chapter_number=deterministic.chapter_number,
            chapter_summary=llm_result.chapter_summary or deterministic.chapter_summary,
            extraction_quality=llm_result.extraction_quality,
            extraction_notes=llm_result.extraction_notes,
        )

        # 合并情节点（LLM优先）
        seen_descriptions = set()
        for event in llm_result.plot_events + deterministic.plot_events:
            if event.description not in seen_descriptions:
                merged.plot_events.append(event)
                seen_descriptions.add(event.description)

        # 合并角色提及（LLM优先）
        seen_names = set()
        for mention in llm_result.character_mentions + deterministic.character_mentions:
            if mention.name not in seen_names:
                merged.character_mentions.append(mention)
                seen_names.add(mention.name)

        # 合并其他字段
        merged.state_changes = list(set(llm_result.state_changes + deterministic.state_changes))
        merged.foreshadowing_ops = llm_result.foreshadowing_ops or deterministic.foreshadowing_ops
        merged.timeline_events = llm_result.timeline_events or deterministic.timeline_events
        merged.setting_mentions = list(set(llm_result.setting_mentions + deterministic.setting_mentions))
        merged.emotion_curve = llm_result.emotion_curve or deterministic.emotion_curve
        merged.hook_points = llm_result.hook_points or deterministic.hook_points
        merged.information_release_points = list(set(
            llm_result.information_release_points + deterministic.information_release_points
        ))

        return merged


# 全局单例
_chapter_extractor: ChapterExtractor | None = None


def get_chapter_extractor() -> ChapterExtractor:
    global _chapter_extractor
    if _chapter_extractor is None:
        _chapter_extractor = ChapterExtractor()
    return _chapter_extractor
