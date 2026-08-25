"""Writer 输入编译器。

核心目标：让 Writer 从"看一堆系统上下文"变成"执行一个短小、清晰、自然的场景任务"。

WriterInputPacket 是 Writer 唯一输入。
不允许 API 层直接拼 Writer prompt。
不允许把原始 ledger、benchmark、methodology 直接塞入 Writer。

明确禁止传入：
- 原始 EvidenceHub
- 原始、未裁剪的状态表
- 全章未分配的 must_progress
- 全量 benchmark
- 全量 methodology
- 全量 quality_extensions
"""
from __future__ import annotations

import json
import logging

from app.models.contract_item import (
    CompiledSceneContract,
    ContractItem,
    WriterInputPacket,
)
from app.services.writer_input_enrichment_adapter import get_writer_input_enrichment_adapter

logger = logging.getLogger(__name__)


class WriterInputCompiler:
    """Writer 输入编译器"""

    def compile(
        self,
        contract: CompiledSceneContract,
        pov_character: str | None = None,
        previous_scene_summary: str = "",
        source_scene_contract: dict | None = None,
    ) -> WriterInputPacket:
        """从编译后的场景合同生成 Writer 输入包

        Args:
            contract: 编译后的场景合同
            pov_character: 视角人物
            previous_scene_summary: 上一场景摘要
            source_scene_contract: 原始场景合同，仅用于提取已有质量/声纹/伏笔信号

        Returns:
            WriterInputPacket
        """
        if hasattr(contract, "model_dump"):
            adapter_contract = contract.model_dump()
        elif hasattr(contract, "dict"):
            adapter_contract = contract.dict()
        else:
            adapter_contract = dict(contract)
        if source_scene_contract:
            for key in (
                "source_of_truth",
                "editor_enrichment",
                "scene_provenance",
                "quality_extensions",
                "commercial_pacing_contract",
                "experience_contract",
                "literary_quality_contract",
                "scene_credibility_contract",
                "reader_corpus_guidance",
                "chapter_rhythm_context",
                "genre_enrichment",
                "scene_function",
                "chapter_function",
                "target_emotion",
                "opening_state",
                "ending_state",
                "recent_repeated_body_language",
                "repeated_body_language",
            ):
                value = source_scene_contract.get(key)
                if value not in (None, "", [], {}):
                    adapter_contract[key] = value

        enrichment = get_writer_input_enrichment_adapter().build(
            adapter_contract,
            pov_character=pov_character,
            previous_scene_summary=previous_scene_summary,
        )

        # 1. system_rules
        system_rules = self._build_system_rules(contract)

        # 2. scene_task
        scene_task = self._build_scene_task(contract, pov_character)

        # 3. visible_facts
        visible_facts = self._build_visible_facts(contract)

        # 4. must_include
        must_include = self._build_must_include(contract)

        # 5. soft_suggestions
        soft_suggestions = self._build_soft_suggestions(contract)

        # 6. must_avoid
        must_avoid = self._build_must_avoid(contract)

        # 7. ending_state
        ending_state = contract.ending_state or ""

        # 8. style_instruction
        style_instruction = self._build_style_instruction(contract)

        # 9. pacing_instruction
        pacing_instruction = self._build_pacing_instruction(contract)

        # 10. output_constraints
        output_constraints = self._build_output_constraints(contract)

        return WriterInputPacket(
            system_rules=system_rules,
            scene_task=scene_task,
            visible_facts=visible_facts,
            must_include=must_include,
            soft_suggestions=soft_suggestions,
            must_avoid=must_avoid,
            ending_state=ending_state,
            style_instruction=style_instruction,
            pacing_instruction=pacing_instruction,
            output_constraints=output_constraints,
            active_capabilities=enrichment.active_capabilities,
            activation_reasons=enrichment.activation_reasons,
            source_trace=enrichment.source_trace,
            writer_input_enrichment=enrichment.packet_patch,
        )

    def compile_to_prompt(self, packet: WriterInputPacket) -> str:
        """将 WriterInputPacket 编译为自然语言提示词

        这是最终给 Writer 的文本，短小、清晰、自然。
        """
        parts = []

        # 场景任务
        if packet.scene_task:
            parts.append(f"【场景任务】\n{packet.scene_task}")

        # 可见事实
        if packet.visible_facts:
            facts_text = "\n".join(f"- {f}" for f in packet.visible_facts)
            parts.append(f"【当前角色状态】\n{facts_text}")

        # 必须自然出现
        if packet.must_include:
            must_text = "\n".join(f"{i+1}. {m}" for i, m in enumerate(packet.must_include))
            parts.append(f"【本场必须自然出现】\n{must_text}")

        # 可以轻轻带到
        if packet.soft_suggestions:
            soft_text = "\n".join(f"{i+1}. {s}" for i, s in enumerate(packet.soft_suggestions))
            parts.append(f"【可以轻轻带到】\n{soft_text}")

        # 禁止
        if packet.must_avoid:
            avoid_text = "\n".join(f"{i+1}. {a}" for i, a in enumerate(packet.must_avoid))
            parts.append(f"【禁止】\n{avoid_text}")

        # 结尾状态
        if packet.ending_state:
            parts.append(f"【结尾状态】\n{packet.ending_state}")

        # 风格要求
        if packet.style_instruction:
            parts.append(f"【风格要求】\n{packet.style_instruction}")

        # 节奏要求
        if packet.pacing_instruction:
            parts.append(f"【节奏要求】\n{packet.pacing_instruction}")

        if packet.active_capabilities:
            cap_lines = []
            for capability in packet.active_capabilities:
                reason = packet.activation_reasons.get(capability, "")
                if reason:
                    cap_lines.append(f"- {capability}：{reason}")
                else:
                    cap_lines.append(f"- {capability}")
            parts.append(f"【输入增强】\n" + "\n".join(cap_lines))

        if packet.source_trace:
            trace_lines = []
            for capability in packet.active_capabilities:
                sources = packet.source_trace.get(capability, [])
                if sources:
                    trace_lines.append(f"- {capability}：{'、'.join(sources)}")
            if trace_lines:
                parts.append(f"【来源追踪】\n" + "\n".join(trace_lines))

        if packet.writer_input_enrichment:
            parts.append(
                "【增强详情】\n"
                + json.dumps(packet.writer_input_enrichment, ensure_ascii=False, indent=2)
            )

        return "\n\n".join(parts)

    def _build_system_rules(self, contract: CompiledSceneContract) -> list[str]:
        """构建系统规则"""
        rules = [
            "用自然叙事写作，不要解释剧情。",
            "不要使用'这说明''她意识到''这意味着'等解释性表达。",
            "不要过量使用破折号（——）和省略号（……）。",
            "人物行为要符合其性格和当前认知，不要让角色知道不该知道的事。",
        ]
        budget = contract.information_budget
        if budget.max_definition_sentences_per_1000_chars <= 1.0:
            rules.append("尽量减少定义句和解释句，用行动和对话展现。")
        return rules

    def _build_scene_task(self, contract: CompiledSceneContract, pov: str | None) -> str:
        """构建场景任务"""
        parts = []
        budget = contract.information_budget

        char_desc = f"约{budget.target_chars}字" if budget.target_chars else ""
        if pov:
            parts.append(f"写一个{char_desc}的场景，视角人物为{pov}。")
        else:
            parts.append(f"写一个{char_desc}的场景。")

        if contract.scene_function:
            parts.append(f"场景功能：{contract.scene_function}。")
        if contract.target_emotion:
            parts.append(f"目标情绪：{contract.target_emotion}。")

        return " ".join(parts)

    def _build_visible_facts(self, contract: CompiledSceneContract) -> list[str]:
        """构建可见事实"""
        facts = []
        for item in contract.hard_facts:
            if item.is_writer_visible():
                facts.append(item.text)
        return facts

    def _build_must_include(self, contract: CompiledSceneContract) -> list[str]:
        """构建必须包含项"""
        items = []
        for item in contract.current_scene_must:
            if item.is_writer_visible():
                items.append(item.text)
        return items

    def _build_soft_suggestions(self, contract: CompiledSceneContract) -> list[str]:
        """构建软提示"""
        items = []
        for item in contract.soft_hints:
            if item.is_writer_visible():
                items.append(item.text)
        return items

    def _build_must_avoid(self, contract: CompiledSceneContract) -> list[str]:
        """构建禁止项"""
        items = []
        for item in contract.forbidden:
            if item.is_writer_visible():
                items.append(item.text)
        return items

    def _build_style_instruction(self, contract: CompiledSceneContract) -> str:
        """构建风格指令"""
        style = contract.style_policy
        # P1-18 修复：合并 style_directive（声纹/修辞/禁忌等更细粒度的风格指令）
        directive = contract.style_directive or {}
        if not style and not directive:
            return ""
        parts = []
        if style.get("name"):
            parts.append(f"保持{style['name']}风格。")
        if style.get("forbidden_patterns"):
            patterns = "、".join(style["forbidden_patterns"][:3])
            parts.append(f"避免：{patterns}。")
        # 合并 style_directive 中的指令
        if directive.get("voice"):
            parts.append(f"声纹：{directive['voice']}。")
        if directive.get("rhetoric"):
            parts.append(f"修辞：{directive['rhetoric']}。")
        if directive.get("forbidden_words"):
            forbidden_words = "、".join(directive["forbidden_words"][:5])
            parts.append(f"禁用词：{forbidden_words}。")
        if directive.get("extra"):
            parts.append(str(directive["extra"]))
        return " ".join(parts)

    def _build_pacing_instruction(self, contract: CompiledSceneContract) -> str:
        """构建节奏指令"""
        pacing = contract.pacing_policy
        if not pacing:
            return ""
        parts = []
        if pacing.get("rhythm"):
            parts.append(f"节奏：{pacing['rhythm']}。")
        if pacing.get("pressure"):
            parts.append(f"压力：{pacing['pressure']}。")
        return " ".join(parts)

    def _build_output_constraints(self, contract: CompiledSceneContract) -> dict:
        """构建输出约束"""
        budget = contract.information_budget
        return {
            "target_chars": budget.target_chars,
            "hard_max_chars": budget.hard_max_chars,
            "max_must_items": budget.max_current_scene_must,
            "max_soft_hints": budget.max_soft_hints,
        }


# 全局单例
_compiler: WriterInputCompiler | None = None


def get_writer_input_compiler() -> WriterInputCompiler:
    global _compiler
    if _compiler is None:
        _compiler = WriterInputCompiler()
    return _compiler
