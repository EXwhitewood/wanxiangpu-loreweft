"""自迭代归一化规则数据模型（T1.1）。

对应讨论稿 §5.1 自迭代方案：
- NormalizationRule: 自迭代规则（candidate→shadow→active→retired/suspect）
- NormalizationCase: 留痕案例（漏判补判 / 误判纠正）

物理存储见 sqlite.py 的 normalization_rules / normalization_cases 两张表。
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


# 规则生命周期状态（讨论稿 §5.1.5）
# candidate: 候选，只记录不输出
# shadow: 旁路验证中（本方案中 candidate 与 shadow 行为合并，统一用 shadow 表示"旁路观察中"）
# active: 转正，进入归一化工具
# retired: 退役，不再生效
# suspect: 观察期（被 LLM 多次否决或 Final Acceptance 失败，待淘汰）
RuleStatus = Literal["candidate", "shadow", "active", "retired", "suspect"]

# 案例类型
CaseType = Literal["miss", "correction"]

# LLM 复核动作（决定强弱基准）
# agree: 认可归一化结果（弱基准）
# correct: 纠正误判（强基准）
# supplement: 补判漏判（强基准）
LlmAction = Literal["agree", "correct", "supplement"]


class RuleCondition(BaseModel):
    """if 条件（结构化，不用自然语言也不用代码）。"""

    field: Literal["issue_type", "detail", "validator", "metric"]
    op: Literal["equals", "starts_with", "contains", "contains_any", "regex_match"]
    # contains_any 用 list[str]，其余用 str
    value: str | list[str]
    case_insensitive: bool = True


class RuleAction(BaseModel):
    """then 动作。只到归一化层（family + operation），不碰路由判断。"""

    family: str  # 必填，归一化层
    operation: str = ""  # 可选，路由表可查


class NormalizationRule(BaseModel):
    """自迭代归一化规则。

    then 只到归一化层（family + operation），不碰路由判断。
    路由判断永远留给 LLM 每次推理（讨论稿 §5.1.2 边界）。
    """

    rule_id: str = ""
    if_condition: RuleCondition
    then_action: RuleAction
    evidence: str = ""  # LLM 给的理由
    examples: list[dict[str, Any]] = Field(default_factory=list)  # 出生案例

    # 生命周期
    status: RuleStatus = "candidate"

    # shadow 统计（讨论稿 §5.1.4 / §5.1.4a）
    shadow_hits: int = 0  # 强+弱基准命中
    shadow_strong_hits: int = 0  # 强基准命中（correct/supplement）
    shadow_misses: int = 0
    shadow_strong_misses: int = 0

    # active 统计（闸门④ 自动退役）
    consecutive_corrections: int = 0  # 连续被 LLM 纠正次数

    # 元数据
    created_at: str = ""
    updated_at: str = ""
    source_rule_id: str = ""  # 修订前的规则 ID（用于追溯）

    @property
    def shadow_total(self) -> int:
        """shadow 总样本数（命中+偏离）。"""
        return self.shadow_hits + self.shadow_misses

    @property
    def shadow_consistency_rate(self) -> float:
        """shadow 一致率 = 命中 / (命中+偏离)。"""
        total = self.shadow_total
        return self.shadow_hits / total if total > 0 else 0.0

    @property
    def shadow_strong_ratio(self) -> float:
        """强基准占比 = 强命中 / (强命中+强偏离)。

        用于同源污染防护（讨论稿 §5.1.4a）：转正要求强基准占比 ≥ 30%。
        """
        strong_total = self.shadow_strong_hits + self.shadow_strong_misses
        return self.shadow_strong_hits / strong_total if strong_total > 0 else 0.0


class NormalizationCase(BaseModel):
    """留痕案例（S4 异步留痕）。

    漏判补判（miss）和误判纠正（correction）都记录到案例池，
    攒够阈值后触发规则提炼（讨论稿 §5.1.5）。
    """

    case_id: str = ""
    rule_id: str = ""  # 关联的规则（candidate/shadow 时记录）
    violation_json: dict[str, Any] = Field(default_factory=dict)
    llm_family: str = ""  # LLM 复核结果（正确答案）
    rule_family: str = ""  # 规则判断结果
    case_type: CaseType = "miss"  # 漏判补判 / 误判纠正
    llm_action: LlmAction = "agree"  # 强弱基准标记
    created_at: str = ""
