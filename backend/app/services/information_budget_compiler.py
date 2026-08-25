"""信息预算编译器。

核心目标：在 Writer 之前拦截过载合同。

编译流程：
1. 接收 SceneAllocation 结果
2. 按 priority 排序 ContractItem
3. 估算 word_cost / explanation_risk
4. 检查是否超预算
5. 超预算时降级 soft_hint / defer / hidden
6. 输出 CompiledSceneContract

默认预算（普通商业网文模式）：
- 当前场景 must item：最多 3 条
- soft hint：最多 2 条
- 新术语：最多 2 个
- 新人物：最多 2 个
- 伏笔操作：最多 1 个
- 解释型句子：每 1000 字最多 1 句
"""
from __future__ import annotations

import logging

from app.models.contract_item import (
    CompiledSceneContract,
    ContractItem,
    InformationBudget,
)

logger = logging.getLogger(__name__)

# 解释腔风险关键词
_EXPLANATION_MARKERS = [
    "这说明", "这意味着", "他意识到", "她明白了",
    "也就是说", "换句话说", "不是X，而是Y",
    "本质上", "事实上", "实际上",
    "——", "……",
]


class InformationBudgetCompiler:
    """信息预算编译器"""

    # 不同模式的预算配置
    BUDGET_PROFILES: dict[str, InformationBudget] = {
        "commercial": InformationBudget(
            target_chars=3000,
            hard_max_chars=5000,
            max_current_scene_must=3,
            max_soft_hints=2,
            max_new_named_entities=2,
            max_new_terms=2,
            max_definition_sentences_per_1000_chars=1.0,
            max_explanation_markers_per_1000_chars=2.0,
            max_foreshadowing_ops=1,
            max_reader_questions_opened=1,
        ),
        "suspense": InformationBudget(
            target_chars=3000,
            hard_max_chars=5000,
            max_current_scene_must=4,
            max_soft_hints=3,
            max_new_named_entities=2,
            max_new_terms=2,
            max_definition_sentences_per_1000_chars=0.8,
            max_explanation_markers_per_1000_chars=1.5,
            max_foreshadowing_ops=2,
            max_reader_questions_opened=2,
        ),
        "literary": InformationBudget(
            target_chars=4000,
            hard_max_chars=6000,
            max_current_scene_must=2,
            max_soft_hints=4,
            max_new_named_entities=3,
            max_new_terms=3,
            max_definition_sentences_per_1000_chars=0.5,
            max_explanation_markers_per_1000_chars=1.0,
            max_foreshadowing_ops=1,
            max_reader_questions_opened=1,
        ),
    }

    def compile(
        self,
        contract: CompiledSceneContract,
        mode: str = "commercial",
    ) -> CompiledSceneContract:
        """编译场景合同，确保不超预算

        Args:
            contract: 待编译的场景合同
            mode: 写作模式（commercial / suspense / literary）

        Returns:
            预算调整后的 CompiledSceneContract
        """
        budget = self.BUDGET_PROFILES.get(mode, self.BUDGET_PROFILES["commercial"])

        # 估算每个 item 的 word_cost 和 explanation_risk
        all_must = list(contract.current_scene_must)
        all_soft = list(contract.soft_hints)

        for item in all_must + all_soft:
            item.word_cost = self._estimate_word_cost(item)
            item.explanation_risk = self._estimate_explanation_risk(item)

        # 按 priority 排序
        priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        all_must.sort(key=lambda i: priority_order.get(i.priority, 99))
        all_soft.sort(key=lambda i: priority_order.get(i.priority, 99))

        # 预算检查与降级
        must_over = len(all_must) - budget.max_current_scene_must
        soft_over = len(all_soft) - budget.max_soft_hints

        deferred_must = []
        deferred_soft = []

        # 降级 must：低优先级的降为 carry_forward
        if must_over > 0:
            for item in reversed(all_must):  # 从低优先级开始降级
                if must_over <= 0:
                    break
                if item.priority in ("medium", "low"):
                    deferred = item.model_copy(update={
                        "obligation_type": "carry_forward",
                        "visibility": "editor_only",
                        "status": "deferred",
                    })
                    deferred_must.append(deferred)
                    must_over -= 1

            # 如果仍超预算，降级为 soft_hint
            if must_over > 0:
                for item in reversed(all_must):
                    if must_over <= 0:
                        break
                    if item.priority == "high" and item.obligation_type != "hard_fact":
                        deferred = item.model_copy(update={
                            "obligation_type": "current_scene_soft_hint",
                            "status": "allocated",
                        })
                        deferred_soft.append(deferred)
                        must_over -= 1

        # 降级 soft：低优先级的降为 carry_forward
        if soft_over > 0:
            for item in reversed(all_soft):
                if soft_over <= 0:
                    break
                deferred = item.model_copy(update={
                    "obligation_type": "carry_forward",
                    "visibility": "editor_only",
                    "status": "deferred",
                })
                contract.hidden_carry_forward.append(deferred)
                soft_over -= 1

        # 重建合同
        kept_must = [i for i in all_must if i not in [d.model_copy() for d in deferred_must]]
        kept_soft = [i for i in all_soft] + deferred_soft

        # 去重（降级后的 item 不再出现在 must 列表中）
        must_ids = {i.id for i in kept_must}
        kept_must = [i for i in kept_must if i.id not in {d.id for d in deferred_must}]

        contract.current_scene_must = kept_must[:budget.max_current_scene_must]
        contract.soft_hints = kept_soft[:budget.max_soft_hints]
        contract.hidden_carry_forward.extend(deferred_must)
        contract.information_budget = budget

        # 预算检查结果
        check = contract.budget_check()
        if check["must_over_budget"] or check["soft_over_budget"]:
            logger.warning(
                "[InformationBudget] 场景 %s 仍超预算: must=%d/%d soft=%d/%d",
                contract.scene_id,
                check["must_count"], check["max_must"],
                check["soft_count"], check["max_soft"],
            )

        return contract

    def compile_scene_contract_budget(
        self,
        scene_contract: dict,
        *,
        max_hard_must_show: int | None = None,
        max_soft_guidance: int | None = None,
    ) -> dict:
        """Partition raw scene-contract `must_show` before it reaches writer.

        This is the planning-boundary companion to `compile()`: editor planning
        may produce a dense `must_show` list, but downstream systems should see
        a capped hard list plus explicit soft/deferred buckets.
        """
        dynamic_budget = self.resolve_scene_contract_budget(scene_contract)
        max_hard_must_show = int(max_hard_must_show or dynamic_budget["max_hard_must_show"])
        max_soft_guidance = int(max_soft_guidance or dynamic_budget["max_soft_guidance"])

        existing_budget = self._as_dict(scene_contract.get("information_budget"))
        must_show = self._as_list(
            existing_budget.get("original_must_show")
            or scene_contract.get("must_show")
            or scene_contract.get("must_show_outline")
            or self._as_dict(scene_contract.get("source_of_truth")).get("must_show_outline")
        )
        hard = must_show[:max_hard_must_show]
        soft = must_show[max_hard_must_show:max_hard_must_show + max_soft_guidance]
        deferred = must_show[max_hard_must_show + max_soft_guidance:]

        warnings: list[str] = []
        if soft or deferred:
            warnings.append(
                "must_show_over_budget: "
                f"{len(must_show)} item(s), hard={len(hard)}, soft={len(soft)}, deferred={len(deferred)}"
            )

        result = {
            "must_show": hard,
            "hard_must_show": hard,
            "soft_guidance": soft,
            "deferred_items": deferred,
            "information_budget": {
                "schema_version": 1,
                "source": "information_budget_compiler",
                "original_must_show": must_show,
                "hard_must_show": hard,
                "soft_guidance": soft,
                "deferred_items": deferred,
                "max_hard_must_show": max_hard_must_show,
                "max_soft_guidance": max_soft_guidance,
                "budget_profile": dynamic_budget["budget_profile"],
                "budget_reason": dynamic_budget["budget_reason"],
                "over_budget": bool(soft or deferred),
            },
        }
        if warnings:
            existing_warnings = self._as_list(scene_contract.get("compiler_warnings"))
            for warning in warnings:
                if warning not in existing_warnings:
                    existing_warnings.append(warning)
            result["compiler_warnings"] = existing_warnings
        return result

    def apply_to_scene_contract(
        self,
        scene_contract: dict,
        *,
        max_hard_must_show: int | None = None,
        max_soft_guidance: int | None = None,
    ) -> dict:
        """Return a copy of a raw scene contract with information budget applied."""
        contract = dict(scene_contract or {})
        contract.update(self.compile_scene_contract_budget(
            contract,
            max_hard_must_show=max_hard_must_show,
            max_soft_guidance=max_soft_guidance,
        ))
        return contract

    def resolve_scene_contract_budget(self, scene_contract: dict) -> dict:
        """Choose raw-contract information budget from scene role and length."""
        contract = scene_contract if isinstance(scene_contract, dict) else {}
        text_parts = [
            contract.get("chapter_function"),
            contract.get("scene_function"),
            contract.get("function"),
            contract.get("goal"),
            contract.get("conflict"),
        ]
        role_text = " ".join(str(part or "").lower() for part in text_parts)
        try:
            target_chars = int(contract.get("target_chars") or contract.get("target_characters") or 0)
        except (TypeError, ValueError):
            target_chars = 0

        if target_chars >= 4500 or any(marker in role_text for marker in (
            "climax", "reveal", "battle", "trial", "turning",
            "高潮", "揭示", "决战", "审判", "反转", "爆发",
        )):
            return {
                "max_hard_must_show": 6,
                "max_soft_guidance": 3,
                "budget_profile": "high_intensity",
                "budget_reason": "long_or_climactic_scene",
            }
        if 0 < target_chars <= 2400 or any(marker in role_text for marker in (
            "transition", "aftermath", "breather", "quiet", "setup",
            "过渡", "余波", "铺垫", "缓冲", "静场",
        )):
            return {
                "max_hard_must_show": 4,
                "max_soft_guidance": 2,
                "budget_profile": "lean",
                "budget_reason": "short_or_transition_scene",
            }
        return {
            "max_hard_must_show": 5,
            "max_soft_guidance": 3,
            "budget_profile": "standard",
            "budget_reason": "default_scene_budget",
        }

    def _estimate_word_cost(self, item: ContractItem) -> int:
        """估算 item 需要多少字来呈现"""
        text_len = len(item.text)
        if item.obligation_type == "hard_fact":
            return 0  # 硬事实不需要额外字数，自然带过
        elif item.obligation_type == "current_scene_must":
            return max(50, text_len // 2)  # must 通常需要展开
        elif item.obligation_type == "current_scene_soft_hint":
            return max(20, text_len // 4)  # soft hint 轻轻带到
        return text_len // 3

    def _estimate_explanation_risk(self, item: ContractItem) -> float:
        """估算 item 导致解释腔的风险"""
        text = item.text
        risk = 0.0
        for marker in _EXPLANATION_MARKERS:
            if marker in text:
                risk += 0.2
        # 伏笔和情绪类 item 解释风险更高
        if item.layer in ("foreshadowing", "emotion"):
            risk += 0.1
        return min(1.0, risk)

    @staticmethod
    def _as_dict(value) -> dict:
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _as_list(value) -> list:
        if value in (None, ""):
            return []
        if isinstance(value, list):
            return value
        if isinstance(value, tuple):
            return list(value)
        return [value]


# 全局单例
_compiler: InformationBudgetCompiler | None = None


def get_information_budget_compiler() -> InformationBudgetCompiler:
    global _compiler
    if _compiler is None:
        _compiler = InformationBudgetCompiler()
    return _compiler
