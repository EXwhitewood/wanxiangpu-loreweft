"""风格-合同冲突检测器。

检测 style_directive 与 must_show / forbidden / editor_enrichment 之间的矛盾。
按文档方案，规则检测只标记 possible_style_contract_conflict，
需要 LLM Critic 或主编复核后才能升级为 style_contract_conflict。

设计原则：
- 规则层只做关键词级别的粗筛，不做语义判断
- 检测结果为 possible_* 前缀，不直接阻断
- 矛盾不意味着一定无法同时满足（如"内心挣扎"可以通过动作呈现，不一定要"大段独白"）
"""

from __future__ import annotations

from app.models.violation import make_violation


# 风格禁用表达 → 合同要求关键词 的冲突映射
_CONFLICT_PATTERNS: list[dict] = [
    {
        "avoid_pattern": "心理独白",
        "must_show_keywords": ["内心独白", "心理活动", "内心挣扎", "心路历程", "心理描写"],
        "dimension": "narrative_distance",
        "hint": "降低内心独白要求，改为动作/对话呈现",
    },
    {
        "avoid_pattern": "大段描写",
        "must_show_keywords": ["环境描写", "场景描写", "景物描写", "氛围描写"],
        "dimension": "description_density",
        "hint": "减少环境描写要求，改为关键细节点染",
    },
    {
        "avoid_pattern": "解释性",
        "must_show_keywords": ["解释", "说明", "交代背景", "信息交代"],
        "dimension": "info_density",
        "hint": "将信息交代改为角色体验中的碎片化揭示",
    },
]


class StyleContractConflictDetector:
    """检测 style_directive 与场景合同之间的潜在冲突。"""

    def check(self, scene_contract: dict) -> list[dict]:
        violations: list[dict] = []

        style_directive = scene_contract.get("style_directive", {})
        if not style_directive or not isinstance(style_directive, dict):
            return violations

        avoid_list = style_directive.get("avoid", [])
        if not isinstance(avoid_list, list) or not avoid_list:
            return violations

        # 收集所有 must_show 来源
        all_must_show: list[str] = []
        source_of_truth = scene_contract.get("source_of_truth", {})
        if isinstance(source_of_truth, dict):
            all_must_show.extend(source_of_truth.get("must_show_outline", []))
        editor_enrichment = scene_contract.get("editor_enrichment", {})
        if isinstance(editor_enrichment, dict):
            all_must_show.extend(editor_enrichment.get("additional_must_show", []))
        all_must_show.extend(scene_contract.get("must_show", []))

        if not all_must_show:
            return violations

        # 检测冲突
        for avoid_item in avoid_list:
            if not isinstance(avoid_item, str):
                continue
            for pattern in _CONFLICT_PATTERNS:
                if pattern["avoid_pattern"] not in avoid_item:
                    continue
                for keyword in pattern["must_show_keywords"]:
                    for must_item in all_must_show:
                        if isinstance(must_item, str) and keyword in must_item:
                            violations.append(make_violation(
                                "possible_style_contract_conflict",
                                "medium",
                                f"风格禁用「{avoid_item}」与必须展示「{must_item}」可能冲突"
                                f"（维度：{pattern['dimension']}）",
                                source="deterministic",
                                expected_behavior=pattern["hint"],
                            ))
                            break  # 每个 avoid_item 对每个 pattern 只报一次

        # 检测 skeleton 维度与 must_show 数量的冲突
        skeleton = style_directive.get("skeleton", {})
        if isinstance(skeleton, dict):
            if skeleton.get("info_density") in ("低", "极低") and len(all_must_show) >= 5:
                violations.append(make_violation(
                    "possible_style_contract_conflict",
                    "low",
                    f"风格要求低信息密度，但 must_show 包含{len(all_must_show)}条必须展示项，可能无法同时满足",
                    source="deterministic",
                    expected_behavior="降低 must_show 数量或提高信息密度允许值",
                ))

        return violations
