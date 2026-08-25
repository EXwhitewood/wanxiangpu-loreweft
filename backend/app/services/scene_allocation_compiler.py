"""场景分配编译器。

核心目标：彻底解决"章级内容散射到每个场景"的问题。

分配规则：
1. 每个 current_scene_must 最多分配到一个主场景
2. 硬事实可跨场景可见，但不能反复解释
3. 伏笔操作必须有操作类型：plant / reveal / reinforce / payoff / keep_hidden
4. keep_hidden 不给 Writer 明示
5. 每场最多一个 reader question
6. 每场最多一个 foreshadowing operation
7. 同一 item 不得在连续场景重复消费，除非它是硬事实
"""
from __future__ import annotations

import logging
from typing import Optional

from app.models.contract_item import (
    CompiledSceneContract,
    ContractItem,
    InformationBudget,
)

logger = logging.getLogger(__name__)


class SceneAllocation:
    """场景分配结果"""
    def __init__(
        self,
        scene_id: str,
        scene_function: str = "",
        target_emotion: str = "",
    ):
        self.scene_id = scene_id
        self.scene_function = scene_function
        self.target_emotion = target_emotion
        self.allocated_items: list[ContractItem] = []
        self.deferred_items: list[ContractItem] = []
        self.forbidden_items: list[ContractItem] = []
        self.max_information_load: int = 5


class SceneAllocationPlan:
    """章级场景分配计划"""
    def __init__(self, chapter_id: str):
        self.chapter_id = chapter_id
        self.scenes: list[SceneAllocation] = []


class SceneAllocationCompiler:
    """场景分配编译器"""

    @staticmethod
    def _canonical_scene_id(value: str | int | None) -> str | None:
        if value is None:
            return None
        text = str(value)
        if not text:
            return None
        if text.startswith("scene_"):
            return text
        return f"scene_{text}"

    def _find_scene(self, plan: SceneAllocationPlan, scene_id: str | int | None) -> SceneAllocation | None:
        target_id = self._canonical_scene_id(scene_id)
        if not target_id:
            return None
        for scene in plan.scenes:
            if scene.scene_id == target_id:
                return scene
        return None

    def _target_scene_for_item(self, plan: SceneAllocationPlan, item: ContractItem) -> SceneAllocation | None:
        """按 due_scene_id / scene_scope 找到 item 的原始场景。

        场景级合同项必须留在原场景；只有没有任何场景归属的章级 item
        才允许由分配器轮转或复制。
        """
        if item.due_scene_id:
            scene = self._find_scene(plan, item.due_scene_id)
            if scene:
                return scene
        for scope in item.scene_scope:
            scene = self._find_scene(plan, scope)
            if scene:
                return scene
        return None

    @staticmethod
    def _allocated_copy(item: ContractItem, scene_id: str) -> ContractItem:
        return item.model_copy(update={
            "status": "allocated",
            "scene_scope": [scene_id],
            "due_scene_id": scene_id,
        })

    def compile(
        self,
        chapter_id: str,
        items: list[ContractItem],
        scene_count: int,
        scene_functions: list[str] | None = None,
        scene_emotions: list[str] | None = None,
        budget: InformationBudget | None = None,
    ) -> SceneAllocationPlan:
        """编译场景分配计划

        Args:
            chapter_id: 章节 ID
            items: 所有 ContractItem
            scene_count: 场景数量
            scene_functions: 每个场景的功能（可选）
            scene_emotions: 每个场景的目标情绪（可选）
            budget: 信息预算（可选）
        """
        if budget is None:
            budget = InformationBudget()

        plan = SceneAllocationPlan(chapter_id=chapter_id)

        # 创建场景
        for i in range(scene_count):
            func = (scene_functions or [])[i] if scene_functions and i < len(scene_functions) else ""
            emotion = (scene_emotions or [])[i] if scene_emotions and i < len(scene_emotions) else ""
            plan.scenes.append(SceneAllocation(
                scene_id=f"scene_{i}",
                scene_function=func,
                target_emotion=emotion,
            ))

        if not plan.scenes:
            return plan

        # 分类 items
        chapter_goals = []
        hard_facts = []
        must_items = []
        soft_hints = []
        carry_forward = []
        forbidden_items = []
        future_hidden = []
        advisory_items = []
        quality_only = []

        for item in items:
            if item.obligation_type == "chapter_goal":
                chapter_goals.append(item)
            elif item.obligation_type == "hard_fact":
                hard_facts.append(item)
            elif item.obligation_type == "current_scene_must":
                must_items.append(item)
            elif item.obligation_type == "current_scene_soft_hint":
                soft_hints.append(item)
            elif item.obligation_type == "carry_forward":
                carry_forward.append(item)
            elif item.obligation_type == "forbidden":
                forbidden_items.append(item)
            elif item.obligation_type == "future_hidden":
                future_hidden.append(item)
            elif item.obligation_type == "advisory":
                advisory_items.append(item)
            elif item.obligation_type == "quality_check_only":
                quality_only.append(item)

        # 分配策略
        # 1. forbidden：有场景归属则只进入原场景；无归属的全局 forbidden 才进入所有场景
        for item in forbidden_items:
            target_scene = self._target_scene_for_item(plan, item)
            if target_scene:
                target_scene.forbidden_items.append(self._allocated_copy(item, target_scene.scene_id))
            else:
                for scene in plan.scenes:
                    scene.forbidden_items.append(self._allocated_copy(item, scene.scene_id))

        # 2. hard_facts：场景级硬事实只进入原场景；全局硬事实才跨场景可见
        for fact in hard_facts:
            target_scene = self._target_scene_for_item(plan, fact)
            if target_scene:
                target_scene.allocated_items.append(self._allocated_copy(fact, target_scene.scene_id))
            else:
                for scene in plan.scenes:
                    scene.allocated_items.append(self._allocated_copy(fact, scene.scene_id))

        # 3. current_scene_must → 按场景轮转分配
        must_idx = 0
        for item in must_items:
            if not item.is_allocatable():
                continue
            # 场景级 item 必须留在原场景，预算超限由 InformationBudgetCompiler 降级
            target_scene = self._target_scene_for_item(plan, item)

            # 否则轮转分配
            if target_scene is None:
                # 检查目标场景是否已满
                attempts = 0
                while attempts < len(plan.scenes):
                    scene = plan.scenes[must_idx % len(plan.scenes)]
                    current_must = sum(
                        1 for i in scene.allocated_items
                        if i.obligation_type == "current_scene_must"
                    )
                    if current_must < budget.max_current_scene_must:
                        target_scene = scene
                        must_idx += 1
                        break
                    must_idx += 1
                    attempts += 1

            if target_scene:
                target_scene.allocated_items.append(self._allocated_copy(item, target_scene.scene_id))
            else:
                # 无法分配，延迟
                deferred = item.model_copy(update={"status": "deferred"})
                for scene in plan.scenes:
                    scene.deferred_items.append(deferred)
                    break

        # 4. soft_hints → 分配到非满场景
        for item in soft_hints:
            if not item.is_allocatable():
                continue
            best_scene = self._target_scene_for_item(plan, item)
            if best_scene is None:
                # 找一个 soft 最少的场景
                best_scene = min(
                    plan.scenes,
                    key=lambda s: sum(1 for i in s.allocated_items if i.obligation_type == "current_scene_soft_hint"),
                )
            current_soft = sum(
                1 for i in best_scene.allocated_items
                if i.obligation_type == "current_scene_soft_hint"
            )
            if current_soft < budget.max_soft_hints:
                best_scene.allocated_items.append(self._allocated_copy(item, best_scene.scene_id))
            else:
                # 超预算，降级为 carry_forward
                deferred = item.model_copy(update={
                    "status": "deferred",
                    "obligation_type": "carry_forward",
                    "visibility": "editor_only",
                })
                best_scene.deferred_items.append(deferred)

        # 5. carry_forward → 不进入 Writer，只给主编/质量门
        for scene in plan.scenes:
            scene.deferred_items.extend(carry_forward)

        # 6. future_hidden → 不分配
        # (这些 item 不会出现在任何场景的分配列表中)

        # 7. advisory → 不分配到场景，只记录
        # (quality_check_only 同理)

        # 计算每场景信息负载
        for scene in plan.scenes:
            scene.max_information_load = sum(
                1 for i in scene.allocated_items
                if i.obligation_type in ("current_scene_must", "current_scene_soft_hint")
            )

        return plan

    def compile_to_contracts(
        self,
        plan: SceneAllocationPlan,
        budget: InformationBudget | None = None,
    ) -> list[CompiledSceneContract]:
        """将分配计划编译为 CompiledSceneContract 列表"""
        if budget is None:
            budget = InformationBudget()

        contracts = []
        for scene in plan.scenes:
            hard_facts = []
            must_items = []
            soft_hints = []
            forbidden = []
            carry_forward = []
            quality_only = []

            for item in scene.allocated_items:
                if item.obligation_type == "hard_fact":
                    hard_facts.append(item)
                elif item.obligation_type == "current_scene_must":
                    must_items.append(item)
                elif item.obligation_type == "current_scene_soft_hint":
                    soft_hints.append(item)
                elif item.obligation_type == "forbidden":
                    forbidden.append(item)

            forbidden.extend(scene.forbidden_items)

            for item in scene.deferred_items:
                if item.obligation_type == "carry_forward":
                    carry_forward.append(item)
                elif item.obligation_type == "quality_check_only":
                    quality_only.append(item)

            contract = CompiledSceneContract(
                scene_id=scene.scene_id,
                chapter_id=plan.chapter_id,
                scene_function=scene.scene_function,
                target_emotion=scene.target_emotion,
                target_chars=budget.target_chars,
                hard_facts=hard_facts,
                current_scene_must=must_items,
                soft_hints=soft_hints,
                forbidden=forbidden,
                information_budget=budget,
                hidden_carry_forward=carry_forward,
                quality_check_only=quality_only,
            )
            contracts.append(contract)

        return contracts


# 全局单例
_compiler: SceneAllocationCompiler | None = None


def get_scene_allocation_compiler() -> SceneAllocationCompiler:
    global _compiler
    if _compiler is None:
        _compiler = SceneAllocationCompiler()
    return _compiler
