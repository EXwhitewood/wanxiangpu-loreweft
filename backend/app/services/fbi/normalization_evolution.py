"""自迭代归一化规则系统（T6）。

讨论稿 §5.1.8 / §5.3 定稿架构：
- CaseRecorder: S4 异步留痕 → 攒够同类案例 → LLM 提炼 candidate 规则
- ShadowValidator: 对 shadow/candidate 规则跑旁路验证 → 达标转 active / 拒绝 retire
- RuleLifecycleManager: active 规则被纠正计数 → retire/suspect → 触发 LLM 修订

生命周期（讨论稿 §5.1.8）：
    candidate → shadow → active → retired/suspect

转正要求（讨论稿 §5.3 同源污染防护）：
- 一致率 ≥ 80%（SHADOW_CONSISTENCY_THRESHOLD）
- 强基准占比 ≥ 30%（STRONG_BASELINE_RATIO）
- 最少样本 ≥ 10（SHADOW_MIN_SAMPLES）

强基准：correct / supplement（LLM 主动纠错或补判）
弱基准：agree（LLM 认可规则判断，但不主动纠错）
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from app.models.normalization_rule import (
    CaseType,
    NormalizationCase,
    NormalizationRule,
    RuleAction,
    RuleCondition,
)
from app.services.fbi.normalization_rule_engine import _match_condition
from app.services.fbi.normalization_rule_repository import (
    NormalizationRuleRepository,
    _gen_case_id,
    _now_iso,
)
from app.services.llm_gateway import get_llm_gateway

_logger = logging.getLogger(__name__)

# 参数（讨论稿 §5.1.8 / §5.3）
AGGREGATION_THRESHOLD = 3  # 同类案例攒够才提炼
SHADOW_MIN_SAMPLES = 10  # shadow 最少样本
SHADOW_CONSISTENCY_THRESHOLD = 0.8  # 一致率门槛
STRONG_BASELINE_RATIO = 0.3  # 强基准占比门槛
RETIREMENT_THRESHOLD = 5  # 连续被纠正次数 → retire
SUSPECT_THRESHOLD = 3  # 观察期阈值 → suspect


class CaseRecorder:
    """案例记录器（S4 异步留痕）。

    讨论稿 §5.1.8 机制1：留痕 → 聚合 → 提炼。
    漏判补判（supplement）和误判纠正（correct）都触发聚合检查。
    """

    def __init__(self, repo: NormalizationRuleRepository | None = None):
        self._repo = repo or NormalizationRuleRepository()

    async def record_miss_case(
        self, violation: dict[str, Any], llm_family: str
    ) -> None:
        """记录漏判补判案例（LLM 补判，规则没匹配到）。"""
        case = NormalizationCase(
            case_id=_gen_case_id(),
            violation_json=violation,
            llm_family=llm_family,
            rule_family="",  # 漏判时规则没匹配
            case_type="miss",
            llm_action="supplement",
            created_at=_now_iso(),
        )
        await self._repo.save_case(case)
        # 触发聚合检查
        await self._try_aggregate(violation, llm_family, "miss")

    async def record_correction_case(
        self, violation: dict[str, Any], llm_family: str
    ) -> None:
        """记录误判纠正案例（规则判错了，LLM 纠正）。"""
        case = NormalizationCase(
            case_id=_gen_case_id(),
            violation_json=violation,
            llm_family=llm_family,
            rule_family="",  # 误判时规则判错了，留空
            case_type="correction",
            llm_action="correct",
            created_at=_now_iso(),
        )
        await self._repo.save_case(case)
        # 触发聚合检查
        await self._try_aggregate(violation, llm_family, "correction")

    async def _try_aggregate(
        self, violation: dict[str, Any], llm_family: str, case_type: CaseType
    ) -> None:
        """检查是否攒够同类案例，触发规则提炼。

        聚合维度：case_type + llm_family（讨论稿 §5.1.8 机制1）。
        """
        try:
            cases = await self._repo.get_cases_by_type_and_family(
                case_type, llm_family
            )
        except Exception as exc:  # pragma: no cover - 防御性
            _logger.warning("CaseRecorder 聚合查询失败: %s", exc)
            return

        if len(cases) < AGGREGATION_THRESHOLD:
            return

        # 攒够了，调 LLM 提炼规则
        await self._extract_rule(cases, llm_family)

    async def _extract_rule(
        self,
        cases: list[NormalizationCase],
        llm_family: str,
    ) -> None:
        """调 LLM 提炼结构化条件规则。

        讨论稿 §5.1.8 机制2：LLM 提炼 candidate 规则。
        失败时静默降级（只 log，不抛异常）。
        """
        gateway = get_llm_gateway()
        prompt = self._build_extraction_prompt(cases, llm_family)
        try:
            result = await gateway.generate_json(
                prompt=prompt,
                system="你是归一化规则提炼器，输出结构化条件规则 JSON。",
                temperature=0.2,
                timeout=30.0,
            )
        except Exception as exc:  # pragma: no cover - 防御性
            _logger.warning("CaseRecorder LLM 提炼失败: %s", exc)
            return

        if not result.ok or not result.parsed_json:
            return

        # 解析 LLM 输出为 NormalizationRule
        rule = self._parse_llm_rule(result.parsed_json, cases)
        if rule is None:
            return

        rule.status = "candidate"  # 新规则从 candidate 开始
        await self._repo.save_rule(rule)
        _logger.info(
            "CaseRecorder 提炼新规则: rule_id=%s family=%s cases=%d",
            rule.rule_id, rule.then_action.family, len(cases),
        )

    def _build_extraction_prompt(
        self, cases: list[NormalizationCase], llm_family: str
    ) -> str:
        """构造 LLM 规则提炼 prompt。"""
        sample_lines: list[str] = []
        for i, case in enumerate(cases[:5], 1):  # 最多取5个样本
            v = case.violation_json or {}
            sample_lines.append(
                f"案例{i}: type={v.get('type') or v.get('violation_type') or ''}, "
                f"detail={v.get('detail') or v.get('reason') or ''}, "
                f"validator={v.get('validator') or ''}"
            )
        return (
            f"以下是 {len(cases)} 个归一化案例，LLM 复核结果 family = '{llm_family}'。\n"
            f"请提炼一个结构化条件规则，使未来类似 violation 能被自动归一化为该 family。\n\n"
            + "\n".join(sample_lines)
            + "\n\n输出 JSON 格式：\n"
            "{\n"
            '  "if": {"field": "issue_type|detail|validator|metric", "op": "equals|starts_with|contains|contains_any|regex_match", "value": "...", "case_insensitive": true},\n'
            '  "then": {"family": "...", "operation": ""},\n'
            '  "evidence": "提炼理由"\n'
            "}"
        )

    def _parse_llm_rule(
        self, parsed_json: Any, cases: list[NormalizationCase]
    ) -> NormalizationRule | None:
        """解析 LLM 输出为 NormalizationRule。"""
        if not isinstance(parsed_json, dict):
            return None
        try:
            if_data = parsed_json.get("if") or {}
            then_data = parsed_json.get("then") or {}
            if not if_data or not then_data:
                return None
            return NormalizationRule(
                rule_id="",  # save_rule 会自动生成
                if_condition=RuleCondition(**if_data),
                then_action=RuleAction(**then_data),
                evidence=str(parsed_json.get("evidence") or ""),
                examples=[
                    {"violation": c.violation_json, "llm_family": c.llm_family}
                    for c in cases[:3]  # 出生案例
                ],
                status="candidate",
                created_at=_now_iso(),
                updated_at=_now_iso(),
            )
        except Exception as exc:
            _logger.warning("CaseRecorder 解析 LLM 规则失败: %s", exc)
            return None


class ShadowValidator:
    """shadow 验证器。

    讨论稿 §5.1.8 机制3：candidate/shadow 规则旁路验证。
    每次 LLM 复核后，对所有 shadow/candidate 规则跑旁路：
    - 规则判断与 LLM 一致 → hit
    - 不一致 → miss
    - 强基准（correct/supplement）→ 同时更新 strong 统计
    """

    def __init__(self, repo: NormalizationRuleRepository | None = None):
        self._repo = repo or NormalizationRuleRepository()

    async def validate(
        self,
        violation: dict[str, Any],
        llm_family: str,
        llm_action: str,  # agree/correct/supplement
    ) -> None:
        """对 shadow/candidate 规则跑旁路验证。

        讨论稿 §5.3 同源污染防护：
        - is_strong = llm_action in ("correct", "supplement")
        - 转正要求：一致率 ≥ 80% + 强基准占比 ≥ 30%
        """
        try:
            shadow_rules = await self._repo.get_shadow_rules()
        except Exception as exc:  # pragma: no cover - 防御性
            _logger.warning("ShadowValidator 查询 shadow 规则失败: %s", exc)
            return

        is_strong = llm_action in ("correct", "supplement")

        for rule in shadow_rules:
            if not _match_condition(rule.if_condition, violation):
                continue

            rule_family = rule.then_action.family
            is_hit = rule_family == llm_family

            # 更新统计
            try:
                await self._repo.update_shadow_stats(rule.rule_id, is_hit, is_strong)
            except Exception as exc:  # pragma: no cover - 防御性
                _logger.warning(
                    "ShadowValidator update_shadow_stats 失败 rule=%s: %s",
                    rule.rule_id, exc,
                )
                continue

            # 检查是否达标转正/拒绝
            if rule.shadow_total + 1 >= SHADOW_MIN_SAMPLES:
                # 重新查询最新统计（update_shadow_stats 是增量更新）
                updated_rule = await self._repo.get_rule(rule.rule_id)
                if updated_rule is None:
                    continue
                if self._should_promote(updated_rule):
                    updated_rule.status = "active"
                    await self._repo.save_rule(updated_rule)
                    _logger.info(
                        "ShadowValidator 规则转正: rule_id=%s family=%s "
                        "consistency=%.2f strong_ratio=%.2f",
                        updated_rule.rule_id, updated_rule.then_action.family,
                        updated_rule.shadow_consistency_rate,
                        updated_rule.shadow_strong_ratio,
                    )
                elif self._should_reject(updated_rule):
                    updated_rule.status = "retired"
                    await self._repo.save_rule(updated_rule)
                    _logger.info(
                        "ShadowValidator 规则拒绝: rule_id=%s family=%s "
                        "consistency=%.2f strong_ratio=%.2f",
                        updated_rule.rule_id, updated_rule.then_action.family,
                        updated_rule.shadow_consistency_rate,
                        updated_rule.shadow_strong_ratio,
                    )

    def _should_promote(self, rule: NormalizationRule) -> bool:
        """转正条件：一致率 ≥ 80% + 强基准占比 ≥ 30%。"""
        return (
            rule.shadow_consistency_rate >= SHADOW_CONSISTENCY_THRESHOLD
            and rule.shadow_strong_ratio >= STRONG_BASELINE_RATIO
        )

    def _should_reject(self, rule: NormalizationRule) -> bool:
        """拒绝条件：一致率 < 50% 或样本足够但强基准为 0。"""
        return (
            rule.shadow_consistency_rate < 0.5
            or (
                rule.shadow_total >= SHADOW_MIN_SAMPLES * 2
                and rule.shadow_strong_ratio == 0
            )
        )


class RuleLifecycleManager:
    """规则生命周期管理。

    讨论稿 §5.1.8 机制4：active 规则被 LLM 纠正 → 计数 → retire/suspect → 修订。
    讨论稿 §5.3 同源污染防护：Final Acceptance 失败 → active 规则进观察期。
    """

    def __init__(self, repo: NormalizationRuleRepository | None = None):
        self._repo = repo or NormalizationRuleRepository()

    async def record_correction(self, rule_id: str) -> None:
        """记录 active 规则被 LLM 纠正一次。

        - consecutive_corrections >= RETIREMENT_THRESHOLD(5) → retire + 触发修订
        - consecutive_corrections >= SUSPECT_THRESHOLD(3) → suspect
        """
        rule = await self._repo.get_rule(rule_id)
        if rule is None or rule.status != "active":
            return

        try:
            new_count = await self._repo.increment_consecutive_corrections(rule_id)
        except Exception as exc:  # pragma: no cover - 防御性
            _logger.warning(
                "RuleLifecycleManager increment_corrections 失败 rule=%s: %s",
                rule_id, exc,
            )
            return

        if new_count >= RETIREMENT_THRESHOLD:
            await self._repo.update_rule_status(rule_id, "retired")
            _logger.info(
                "RuleLifecycleManager 规则退役: rule_id=%s corrections=%d",
                rule_id, new_count,
            )
            # 触发 LLM 修订
            await self._trigger_revision(rule_id)
        elif new_count >= SUSPECT_THRESHOLD:
            await self._repo.update_rule_status(rule_id, "suspect")
            _logger.info(
                "RuleLifecycleManager 规则进观察期: rule_id=%s corrections=%d",
                rule_id, new_count,
            )

    async def record_agree(self, rule_id: str) -> None:
        """记录 active 规则被 LLM 认可一次（agree），重置连续纠正计数。"""
        rule = await self._repo.get_rule(rule_id)
        if rule is None or rule.status != "active":
            return
        if rule.consecutive_corrections > 0:
            await self._repo.reset_consecutive_corrections(rule_id)

    async def record_final_acceptance_failure(self, rule_id: str) -> None:
        """Final Acceptance 失败 → active 规则进观察期（外部校准）。

        讨论稿 §5.3：Final Acceptance 是外部基准，失败说明规则可能有问题。
        """
        rule = await self._repo.get_rule(rule_id)
        if rule is None or rule.status != "active":
            return
        await self._repo.update_rule_status(rule_id, "suspect")
        _logger.info(
            "RuleLifecycleManager 规则因 Final Acceptance 失败进观察期: rule_id=%s",
            rule_id,
        )

    async def _trigger_revision(self, rule_id: str) -> None:
        """触发 LLM 修订退役规则。

        讨论稿 §5.1.8 机制4：retire 后调 LLM 修订，新规则从 candidate 重新 shadow。
        失败时静默降级（只 log，不抛异常）。
        """
        rule = await self._repo.get_rule(rule_id)
        if rule is None:
            return

        try:
            correction_cases = await self._repo.get_correction_cases_for_rule(rule_id)
        except Exception as exc:  # pragma: no cover - 防御性
            _logger.warning(
                "RuleLifecycleManager 查询纠正案例失败 rule=%s: %s", rule_id, exc
            )
            return

        gateway = get_llm_gateway()
        prompt = self._build_revision_prompt(rule, correction_cases)
        try:
            result = await gateway.generate_json(
                prompt=prompt,
                system="你是归一化规则修订器，输出修订后的结构化条件规则 JSON。",
                temperature=0.2,
                timeout=30.0,
            )
        except Exception as exc:  # pragma: no cover - 防御性
            _logger.warning("RuleLifecycleManager LLM 修订失败: %s", exc)
            return

        if not result.ok or not result.parsed_json:
            return

        new_rule = self._parse_revised_rule(result.parsed_json, rule)
        if new_rule is None:
            return

        new_rule.status = "candidate"  # 重新 shadow
        new_rule.source_rule_id = rule.rule_id  # 追溯
        await self._repo.save_rule(new_rule)
        _logger.info(
            "RuleLifecycleManager 规则修订完成: old=%s new=%s",
            rule.rule_id, new_rule.rule_id,
        )

    def _build_revision_prompt(
        self, rule: NormalizationRule, correction_cases: list[NormalizationCase]
    ) -> str:
        """构造 LLM 规则修订 prompt。"""
        case_lines: list[str] = []
        for i, case in enumerate(correction_cases[:5], 1):
            v = case.violation_json or {}
            case_lines.append(
                f"纠正案例{i}: type={v.get('type') or ''}, "
                f"detail={v.get('detail') or ''}, "
                f"llm_family={case.llm_family}"
            )
        return (
            f"原规则被连续纠正 {rule.consecutive_corrections} 次后退役，需要修订。\n"
            f"原规则: if={rule.if_condition.model_dump()}, "
            f"then={rule.then_action.model_dump()}\n\n"
            + "\n".join(case_lines)
            + "\n\n请基于纠正案例修订规则，输出 JSON 格式：\n"
            "{\n"
            '  "if": {"field": "...", "op": "...", "value": "...", "case_insensitive": true},\n'
            '  "then": {"family": "...", "operation": ""},\n'
            '  "evidence": "修订理由"\n'
            "}"
        )

    def _parse_revised_rule(
        self, parsed_json: Any, old_rule: NormalizationRule
    ) -> NormalizationRule | None:
        """解析 LLM 修订输出为新 NormalizationRule。"""
        if not isinstance(parsed_json, dict):
            return None
        try:
            if_data = parsed_json.get("if") or {}
            then_data = parsed_json.get("then") or {}
            if not if_data or not then_data:
                return None
            return NormalizationRule(
                rule_id="",  # save_rule 自动生成
                if_condition=RuleCondition(**if_data),
                then_action=RuleAction(**then_data),
                evidence=str(parsed_json.get("evidence") or ""),
                examples=list(old_rule.examples or []),
                status="candidate",
                created_at=_now_iso(),
                updated_at=_now_iso(),
                source_rule_id=old_rule.rule_id,
            )
        except Exception as exc:
            _logger.warning("RuleLifecycleManager 解析修订规则失败: %s", exc)
            return None
