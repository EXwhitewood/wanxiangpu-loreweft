"""自迭代规则仓储层（T1.3）。

提供 NormalizationRule / NormalizationCase 的持久化能力。
物理存储见 sqlite.py 的 normalization_rules / normalization_cases 两张表。

注意：Session.execute 对 SELECT 返回 _Result，fetchall() 是同步方法返回 list[dict]。
写操作后需要 await db.commit()。
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from app.db.sqlite import get_db
from app.models.normalization_rule import (
    CaseType,
    NormalizationCase,
    NormalizationRule,
    RuleAction,
    RuleCondition,
)

_logger = logging.getLogger(__name__)


def _now_iso() -> str:
    """UTC ISO 时间戳。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds") + "Z"


def _gen_rule_id() -> str:
    """生成规则 ID。"""
    return f"rule_{uuid.uuid4().hex[:12]}"


def _gen_case_id() -> str:
    """生成案例 ID。"""
    return f"case_{uuid.uuid4().hex[:12]}"


def _rule_to_row(rule: NormalizationRule) -> dict[str, Any]:
    """模型 -> DB 行。"""
    return {
        "rule_id": rule.rule_id or _gen_rule_id(),
        "if_condition": json.dumps(rule.if_condition.model_dump(), ensure_ascii=False),
        "then_action": json.dumps(rule.then_action.model_dump(), ensure_ascii=False),
        "evidence": rule.evidence,
        "examples": json.dumps(rule.examples, ensure_ascii=False),
        "status": rule.status,
        "shadow_hits": rule.shadow_hits,
        "shadow_strong_hits": rule.shadow_strong_hits,
        "shadow_misses": rule.shadow_misses,
        "shadow_strong_misses": rule.shadow_strong_misses,
        "consecutive_corrections": rule.consecutive_corrections,
        "created_at": rule.created_at or _now_iso(),
        "updated_at": _now_iso(),
        "source_rule_id": rule.source_rule_id,
    }


def _row_to_rule(row: dict[str, Any]) -> NormalizationRule | None:
    """DB 行 -> 模型。"""
    if not row:
        return None
    try:
        if_data = json.loads(row.get("if_condition") or "{}")
        then_data = json.loads(row.get("then_action") or "{}")
        examples = json.loads(row.get("examples") or "[]")
        return NormalizationRule(
            rule_id=row.get("rule_id") or "",
            if_condition=RuleCondition(**if_data) if if_data else RuleCondition(
                field="issue_type", op="equals", value=""
            ),
            then_action=RuleAction(**then_data) if then_data else RuleAction(family=""),
            evidence=row.get("evidence") or "",
            examples=examples if isinstance(examples, list) else [],
            status=row.get("status") or "candidate",
            shadow_hits=int(row.get("shadow_hits") or 0),
            shadow_strong_hits=int(row.get("shadow_strong_hits") or 0),
            shadow_misses=int(row.get("shadow_misses") or 0),
            shadow_strong_misses=int(row.get("shadow_strong_misses") or 0),
            consecutive_corrections=int(row.get("consecutive_corrections") or 0),
            created_at=row.get("created_at") or "",
            updated_at=row.get("updated_at") or "",
            source_rule_id=row.get("source_rule_id") or "",
        )
    except Exception as exc:  # pragma: no cover - 防御性
        _logger.warning("normalization_rule 反序列化失败: %s", exc)
        return None


def _case_to_row(case: NormalizationCase) -> dict[str, Any]:
    """模型 -> DB 行。"""
    return {
        "case_id": case.case_id or _gen_case_id(),
        "rule_id": case.rule_id,
        "violation_json": json.dumps(case.violation_json, ensure_ascii=False),
        "llm_family": case.llm_family,
        "rule_family": case.rule_family,
        "case_type": case.case_type,
        "llm_action": case.llm_action,
        "created_at": case.created_at or _now_iso(),
    }


def _row_to_case(row: dict[str, Any]) -> NormalizationCase | None:
    """DB 行 -> 模型。"""
    if not row:
        return None
    try:
        violation = json.loads(row.get("violation_json") or "{}")
        return NormalizationCase(
            case_id=row.get("case_id") or "",
            rule_id=row.get("rule_id") or "",
            violation_json=violation if isinstance(violation, dict) else {},
            llm_family=row.get("llm_family") or "",
            rule_family=row.get("rule_family") or "",
            case_type=row.get("case_type") or "miss",
            llm_action=row.get("llm_action") or "agree",
            created_at=row.get("created_at") or "",
        )
    except Exception as exc:  # pragma: no cover
        _logger.warning("normalization_case 反序列化失败: %s", exc)
        return None


class NormalizationRuleRepository:
    """自迭代规则仓储层。

    所有方法都是 async，调用方用 await。
    """

    # ------------------------------------------------------------------
    # 规则查询
    # ------------------------------------------------------------------

    async def get_active_rules(self) -> list[NormalizationRule]:
        """获取所有 active 规则（归一化工具用）。"""
        return await self._get_rules_by_status("active")

    async def get_shadow_rules(self) -> list[NormalizationRule]:
        """获取所有 shadow/candidate 规则（旁路验证用）。

        本方案 candidate 与 shadow 行为合并：candidate 状态的规则也在做旁路观察。
        """
        rules = await self._get_rules_by_status("shadow")
        rules.extend(await self._get_rules_by_status("candidate"))
        return rules

    async def get_suspect_rules(self) -> list[NormalizationRule]:
        """获取所有观察期规则。"""
        return await self._get_rules_by_status("suspect")

    async def get_rule(self, rule_id: str) -> NormalizationRule | None:
        """获取单条规则。"""
        async with get_db() as db:
            result = await db.execute(
                "SELECT * FROM normalization_rules WHERE rule_id = ?",
                (rule_id,),
            )
            row = result.fetchone()
            return _row_to_rule(row) if row else None

    async def _get_rules_by_status(self, status: str) -> list[NormalizationRule]:
        async with get_db() as db:
            result = await db.execute(
                "SELECT * FROM normalization_rules WHERE status = ? ORDER BY updated_at DESC",
                (status,),
            )
            rows = result.fetchall() or []
        rules: list[NormalizationRule] = []
        for row in rows:
            rule = _row_to_rule(row)
            if rule is not None:
                rules.append(rule)
        return rules

    # ------------------------------------------------------------------
    # 规则写入
    # ------------------------------------------------------------------

    async def save_rule(self, rule: NormalizationRule) -> str:
        """保存/更新规则（INSERT OR REPLACE）。

        Returns:
            rule_id
        """
        if not rule.rule_id:
            rule.rule_id = _gen_rule_id()
        if not rule.created_at:
            rule.created_at = _now_iso()
        rule.updated_at = _now_iso()
        row = _rule_to_row(rule)
        async with get_db() as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO normalization_rules
                (rule_id, if_condition, then_action, evidence, examples, status,
                 shadow_hits, shadow_strong_hits, shadow_misses, shadow_strong_misses,
                 consecutive_corrections, created_at, updated_at, source_rule_id)
                VALUES
                (:rule_id, :if_condition, :then_action, :evidence, :examples, :status,
                 :shadow_hits, :shadow_strong_hits, :shadow_misses, :shadow_strong_misses,
                 :consecutive_corrections, :created_at, :updated_at, :source_rule_id)
                """,
                row,
            )
            await db.commit()
        return rule.rule_id

    async def update_rule_status(self, rule_id: str, new_status: str) -> None:
        """更新规则状态。"""
        async with get_db() as db:
            await db.execute(
                "UPDATE normalization_rules SET status = ?, updated_at = ? WHERE rule_id = ?",
                (new_status, _now_iso(), rule_id),
            )
            await db.commit()

    async def update_shadow_stats(
        self, rule_id: str, hit: bool, strong: bool
    ) -> None:
        """更新 shadow 统计。

        Args:
            hit: 规则判断是否命中 LLM 复核结果
            strong: 是否强基准（correct/supplement）
        """
        if hit:
            sql = "UPDATE normalization_rules SET shadow_hits = shadow_hits + 1"
            if strong:
                sql += ", shadow_strong_hits = shadow_strong_hits + 1"
        else:
            sql = "UPDATE normalization_rules SET shadow_misses = shadow_misses + 1"
            if strong:
                sql += ", shadow_strong_misses = shadow_strong_misses + 1"
        sql += ", updated_at = ? WHERE rule_id = ?"
        async with get_db() as db:
            await db.execute(sql, (_now_iso(), rule_id))
            await db.commit()

    async def increment_consecutive_corrections(self, rule_id: str) -> int:
        """active 规则被 LLM 纠正一次，返回纠正后的连续次数。"""
        async with get_db() as db:
            await db.execute(
                "UPDATE normalization_rules SET consecutive_corrections = consecutive_corrections + 1, updated_at = ? WHERE rule_id = ?",
                (_now_iso(), rule_id),
            )
            await db.commit()
            result = await db.execute(
                "SELECT consecutive_corrections FROM normalization_rules WHERE rule_id = ?",
                (rule_id,),
            )
            row = result.fetchone()
        return int(row.get("consecutive_corrections") or 0) if row else 0

    async def reset_consecutive_corrections(self, rule_id: str) -> None:
        """active 规则被 LLM 认可一次（agree），重置连续纠正计数。"""
        async with get_db() as db:
            await db.execute(
                "UPDATE normalization_rules SET consecutive_corrections = 0, updated_at = ? WHERE rule_id = ?",
                (_now_iso(), rule_id),
            )
            await db.commit()

    # ------------------------------------------------------------------
    # 案例查询
    # ------------------------------------------------------------------

    async def save_case(self, case: NormalizationCase) -> str:
        """保存留痕案例。"""
        if not case.case_id:
            case.case_id = _gen_case_id()
        if not case.created_at:
            case.created_at = _now_iso()
        row = _case_to_row(case)
        async with get_db() as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO normalization_cases
                (case_id, rule_id, violation_json, llm_family, rule_family, case_type, llm_action, created_at)
                VALUES
                (:case_id, :rule_id, :violation_json, :llm_family, :rule_family, :case_type, :llm_action, :created_at)
                """,
                row,
            )
            await db.commit()
        return case.case_id

    async def get_cases_by_type(self, case_type: CaseType) -> list[NormalizationCase]:
        """按类型查询案例。"""
        async with get_db() as db:
            result = await db.execute(
                "SELECT * FROM normalization_cases WHERE case_type = ? ORDER BY created_at DESC",
                (case_type,),
            )
            rows = result.fetchall() or []
        cases: list[NormalizationCase] = []
        for row in rows:
            case = _row_to_case(row)
            if case is not None:
                cases.append(case)
        return cases

    async def get_cases_by_type_and_family(
        self, case_type: CaseType, llm_family: str
    ) -> list[NormalizationCase]:
        """按类型 + LLM family 查询案例（聚合用）。"""
        async with get_db() as db:
            result = await db.execute(
                "SELECT * FROM normalization_cases WHERE case_type = ? AND llm_family = ? ORDER BY created_at DESC",
                (case_type, llm_family),
            )
            rows = result.fetchall() or []
        cases: list[NormalizationCase] = []
        for row in rows:
            case = _row_to_case(row)
            if case is not None:
                cases.append(case)
        return cases

    async def get_correction_cases_for_rule(
        self, rule_id: str
    ) -> list[NormalizationCase]:
        """获取某条 active 规则被纠正的案例（修订用）。"""
        async with get_db() as db:
            result = await db.execute(
                "SELECT * FROM normalization_cases WHERE rule_id = ? AND case_type = 'correction' ORDER BY created_at DESC LIMIT 50",
                (rule_id,),
            )
            rows = result.fetchall() or []
        cases: list[NormalizationCase] = []
        for row in rows:
            case = _row_to_case(row)
            if case is not None:
                cases.append(case)
        return cases
