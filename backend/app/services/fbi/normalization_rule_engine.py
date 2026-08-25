"""自迭代规则匹配引擎（T2.1）。

提供 active 规则的查询能力，被 review_minister._issue_family 调用。

设计要点（讨论稿 §5.1.2 / §5.1.6）：
- then 只到归一化层（family），不碰路由判断
- 路由判断永远留给 LLM 每次推理
- 冲突处理：op 特异性 → shadow 一致率 → 交 LLM 仲裁（返回 None）
- 模块级缓存 _active_rules_cache，通过 refresh_active_rules_cache(async) 刷新
- 缓存为 None 时返回 None（安全 fallback，走原归一化逻辑）
"""
from __future__ import annotations

import logging
import re
from typing import Any

from app.models.normalization_rule import NormalizationRule, RuleCondition
from app.services.fbi.normalization_rule_repository import NormalizationRuleRepository

_logger = logging.getLogger(__name__)

_repo = NormalizationRuleRepository()
_active_rules_cache: list[NormalizationRule] | None = None


async def refresh_active_rules_cache() -> None:
    """刷新 active 规则缓存。

    在审查蓝图官 agent.execute 开头调用一次，确保本次归一化用最新规则。
    失败时保留旧缓存（不置空），避免一次刷新失败导致规则全部失效。
    """
    global _active_rules_cache
    try:
        _active_rules_cache = await _repo.get_active_rules()
    except Exception as exc:  # pragma: no cover - 防御性
        _logger.warning("refresh_active_rules_cache 失败，保留旧缓存: %s", exc)
        if _active_rules_cache is None:
            _active_rules_cache = []


def _get_field_value(field: str, violation: dict[str, Any]) -> str:
    """从 violation 取字段值（兼容多种字段名）。"""
    # field 映射：模型定义的 field 名 -> violation 中的实际字段名
    field_map = {
        "issue_type": ("type", "violation_type", "semantic_type", "original_type"),
        "detail": ("detail", "reason", "evidence_text"),
        "validator": ("validator", "source_validator"),
        "metric": ("metric", "type", "violation_type"),
    }
    keys = field_map.get(field, (field,))
    for key in keys:
        value = violation.get(key)
        if value:
            return str(value)
    return ""


def _match_condition(condition: RuleCondition, violation: dict[str, Any]) -> bool:
    """判断 violation 是否匹配规则条件。

    支持的 op：equals / starts_with / contains / contains_any / regex_match
    （讨论稿 §5.1.2）
    """
    raw_value = _get_field_value(condition.field, violation)
    if not raw_value:
        return False

    if condition.case_insensitive:
        field_value = raw_value.lower()
    else:
        field_value = raw_value

    if condition.op == "equals":
        target = str(condition.value) if not isinstance(condition.value, list) else str(condition.value[0])
        target = target.lower() if condition.case_insensitive else target
        return field_value == target
    elif condition.op == "starts_with":
        target = str(condition.value) if not isinstance(condition.value, list) else str(condition.value[0])
        target = target.lower() if condition.case_insensitive else target
        return field_value.startswith(target)
    elif condition.op == "contains":
        target = str(condition.value) if not isinstance(condition.value, list) else str(condition.value[0])
        target = target.lower() if condition.case_insensitive else target
        return target in field_value
    elif condition.op == "contains_any":
        targets = condition.value if isinstance(condition.value, list) else [condition.value]
        if condition.case_insensitive:
            targets = [str(t).lower() for t in targets]
        else:
            targets = [str(t) for t in targets]
        return any(t in field_value for t in targets)
    elif condition.op == "regex_match":
        pattern = str(condition.value) if not isinstance(condition.value, list) else str(condition.value[0])
        flags = re.IGNORECASE if condition.case_insensitive else 0
        try:
            return bool(re.search(pattern, field_value, flags))
        except re.error:
            return False
    return False


# op 特异性排序（讨论稿 §5.1.6 运行时兜底第1层）
_OP_SPECIFICITY = {
    "equals": 5,
    "starts_with": 4,
    "contains": 3,
    "contains_any": 2,
    "regex_match": 1,
}


def _query_active_rule_family(violation: dict[str, Any]) -> str | None:
    """查 active 规则，返回 family（无匹配返回 None）。

    冲突处理（讨论稿 §5.1.6）：
    1. op 特异性排序：equals > starts_with > contains > contains_any > regex_match
    2. 同特异性按 shadow 一致率排序（一致率高的优先）
    3. 前两层分不出来 → 交给 LLM 当场仲裁（返回 None）

    Returns:
        family 字符串，或 None（无匹配/冲突交 LLM 仲裁）
    """
    if not _active_rules_cache:
        return None

    matched: list[NormalizationRule] = []
    for rule in _active_rules_cache:
        if _match_condition(rule.if_condition, violation):
            matched.append(rule)

    if not matched:
        return None
    if len(matched) == 1:
        return matched[0].then_action.family

    # 多规则匹配：按 op 特异性 → shadow 一致率 排序
    matched.sort(
        key=lambda r: (
            -_OP_SPECIFICITY.get(r.if_condition.op, 0),
            -r.shadow_consistency_rate,
        )
    )
    # 如果前两条 family 不同，交 LLM 仲裁（返回 None）
    if matched[0].then_action.family != matched[1].then_action.family:
        _logger.info(
            "active 规则冲突，交 LLM 仲裁: rules=%s, families=%s vs %s",
            [r.rule_id for r in matched[:2]],
            matched[0].then_action.family,
            matched[1].then_action.family,
        )
        return None
    # 前 family 一致，取第一条（特异性最高的）
    return matched[0].then_action.family


def _find_matched_active_rule_ids(violation: dict[str, Any]) -> list[str]:
    """返回所有匹配的 active 规则 ID（T8 blueprint_source 链路记录用）。

    与 _query_active_rule_family 不同：
    - 本函数返回所有匹配规则 ID（用于追溯）
    - _query_active_rule_family 返回单个 family（用于归一化决策）
    """
    if not _active_rules_cache:
        return []
    return [
        rule.rule_id
        for rule in _active_rules_cache
        if _match_condition(rule.if_condition, violation)
    ]


def get_active_rules_snapshot() -> list[NormalizationRule]:
    """获取当前缓存的 active 规则快照（调试用）。"""
    return list(_active_rules_cache or [])
