"""表达多样性检测器 —— 防止同一情绪反复用相同身体语言表达。

检测两类问题：
1. 同一动作/表情在单场景内重复出现超过阈值
2. 同一情绪始终映射到同一身体语言（缺乏变化）

设计原则：纯规则检测，不调用 LLM。
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict

from app.models.violation import make_violation


# 同一动作在单场景内允许出现的最大次数
_ACTION_REPEAT_THRESHOLD = 2

# 情绪-动作映射表：常见情绪及其典型身体语言
_EMOTION_ACTION_MAP: dict[str, list[str]] = {
    "愤怒": ["握紧", "攥紧", "咬紧牙", "攥拳", "攥住", "握拳", "青筋", "咬牙"],
    "恐惧": ["屏住呼吸", "后退", "颤抖", "发抖", "僵住", "瞳孔骤缩", "倒吸"],
    "决心": ["握紧", "攥紧", "攥拳", "咬牙", "挺直脊背", "目光坚定"],
    "紧张": ["屏住呼吸", "心跳加速", "手心出汗", "握紧", "攥紧"],
    "悲伤": ["眼眶发红", "咬唇", "低下头", "垂眸", "闭上眼"],
    "警觉": ["屏住呼吸", "瞳孔骤缩", "侧耳", "目光一凝", "脊背绷紧"],
}

# 需要检测重复的高频动作词（跨情绪通用）
_HIGH_FREQUENCY_ACTIONS: list[str] = [
    "握紧", "攥紧", "攥拳", "攥住", "握拳",
    "屏住呼吸", "咬紧牙", "咬牙",
    "瞳孔骤缩", "目光一凝",
    "脊背绷紧", "挺直脊背",
    "低下头", "垂眸", "闭上眼",
]


class ExpressionVarietyChecker:
    """检查场景正文中身体语言/动作表达的多样性。"""

    def check(
        self,
        text: str,
        scene_contract: dict | None = None,
        chapter_state: dict | None = None,
    ) -> list[dict]:
        violations: list[dict] = []

        if not text:
            return violations

        violations.extend(self._check_action_repetition(text))
        violations.extend(self._check_emotion_monotony(text))

        return violations

    # ------------------------------------------------------------------
    # 1. 同一动作重复检测
    # ------------------------------------------------------------------
    def _check_action_repetition(self, text: str) -> list[dict]:
        violations: list[dict] = []

        action_counts: Counter[str] = Counter()
        action_positions: dict[str, list[int]] = defaultdict(list)

        for action in _HIGH_FREQUENCY_ACTIONS:
            for m in re.finditer(re.escape(action), text):
                action_counts[action] += 1
                action_positions[action].append(m.start())

        for action, count in action_counts.items():
            if count > _ACTION_REPEAT_THRESHOLD:
                # 提取每次出现时的上下文
                contexts = self._extract_contexts(text, action, action_positions[action])
                violations.append(make_violation(
                    "repeated_body_language",
                    "medium",
                    f"身体语言「{action}」在场景内出现{count}次"
                    f"（阈值{_ACTION_REPEAT_THRESHOLD}次），表达方式过于单一。"
                    f"上下文：{contexts}",
                    source="deterministic",
                    target_span=action,
                    expected_behavior="同一情绪应使用不同的身体语言表达，"
                                     "如攥紧→指节泛白→指甲掐进掌心",
                ))

        return violations

    # ------------------------------------------------------------------
    # 2. 情绪-动作单调性检测
    # ------------------------------------------------------------------
    def _check_emotion_monotony(self, text: str) -> list[dict]:
        violations: list[dict] = []

        for emotion, actions in _EMOTION_ACTION_MAP.items():
            # 统计该情绪下每个动作的实际出现次数
            action_counts: list[tuple[str, int]] = []
            for action in actions:
                count = text.count(action)
                if count > 0:
                    action_counts.append((action, count))

            # 只有1种动作被触发，且该动作出现>=2次 → 单调
            if len(action_counts) == 1:
                action, count = action_counts[0]
                if count >= 2:
                    violations.append(make_violation(
                        "emotion_expression_monotone",
                        "low",
                        f"情绪「{emotion}」始终用同一种身体语言「{action}」({count}次)表达，"
                        f"缺乏变化。建议为同一情绪设计不同的外化方式。",
                        source="deterministic",
                        expected_behavior=f"情绪「{emotion}」应有至少2种不同的身体语言表达，"
                                         f"可选：{'、'.join(actions[:4])}",
                    ))

        return violations

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------
    @staticmethod
    def _extract_contexts(
        text: str, action: str, positions: list[int], window: int = 15,
    ) -> str:
        """提取动作词每次出现时的上下文片段。"""
        snippets: list[str] = []
        for pos in positions[:3]:  # 最多展示3处
            start = max(0, pos - window)
            end = min(len(text), pos + len(action) + window)
            snippet = text[start:end].replace("\n", " ")
            snippets.append(f"…{snippet}…")
        return " | ".join(snippets)
