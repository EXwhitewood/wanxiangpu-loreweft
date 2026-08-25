"""T6: 自迭代归一化规则系统测试。

验证：
- CaseRecorder: 记录 miss/correction 案例、聚合检查、LLM 提炼
- ShadowValidator: 旁路验证、转正/拒绝条件、强弱基准区分
- RuleLifecycleManager: 纠正计数、retire/suspect、Final Acceptance 失败

使用 Mock 仓储层和 Mock LLM gateway，避免依赖真实 DB 和 LLM。
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.normalization_rule import (
    NormalizationCase,
    NormalizationRule,
    RuleAction,
    RuleCondition,
)
from app.services.fbi.normalization_evolution import (
    AGGREGATION_THRESHOLD,
    CaseRecorder,
    RETIREMENT_THRESHOLD,
    RuleLifecycleManager,
    SHADOW_CONSISTENCY_THRESHOLD,
    SHADOW_MIN_SAMPLES,
    STRONG_BASELINE_RATIO,
    SUSPECT_THRESHOLD,
    ShadowValidator,
)


class _MockRepo:
    """Mock NormalizationRuleRepository。"""

    def __init__(self):
        self.rules: dict[str, NormalizationRule] = {}
        self.cases: list[NormalizationCase] = []
        self._next_id = 0

    async def save_rule(self, rule: NormalizationRule) -> str:
        if not rule.rule_id:
            self._next_id += 1
            rule.rule_id = f"rule_{self._next_id:03d}"
        self.rules[rule.rule_id] = rule
        return rule.rule_id

    async def save_case(self, case: NormalizationCase) -> str:
        if not case.case_id:
            self._next_id += 1
            case.case_id = f"case_{self._next_id:03d}"
        self.cases.append(case)
        return case.case_id

    async def get_active_rules(self) -> list[NormalizationRule]:
        return [r for r in self.rules.values() if r.status == "active"]

    async def get_shadow_rules(self) -> list[NormalizationRule]:
        return [
            r for r in self.rules.values()
            if r.status in ("shadow", "candidate")
        ]

    async def get_rule(self, rule_id: str) -> NormalizationRule | None:
        return self.rules.get(rule_id)

    async def update_rule_status(self, rule_id: str, new_status: str) -> None:
        if rule_id in self.rules:
            self.rules[rule_id].status = new_status

    async def update_shadow_stats(
        self, rule_id: str, hit: bool, strong: bool
    ) -> None:
        rule = self.rules.get(rule_id)
        if rule is None:
            return
        if hit:
            rule.shadow_hits += 1
            if strong:
                rule.shadow_strong_hits += 1
        else:
            rule.shadow_misses += 1
            if strong:
                rule.shadow_strong_misses += 1

    async def increment_consecutive_corrections(self, rule_id: str) -> int:
        rule = self.rules.get(rule_id)
        if rule is None:
            return 0
        rule.consecutive_corrections += 1
        return rule.consecutive_corrections

    async def reset_consecutive_corrections(self, rule_id: str) -> None:
        rule = self.rules.get(rule_id)
        if rule is None:
            return
        rule.consecutive_corrections = 0

    async def get_cases_by_type_and_family(
        self, case_type: str, llm_family: str
    ) -> list[NormalizationCase]:
        return [
            c for c in self.cases
            if c.case_type == case_type and c.llm_family == llm_family
        ]

    async def get_correction_cases_for_rule(
        self, rule_id: str
    ) -> list[NormalizationCase]:
        return [
            c for c in self.cases
            if c.rule_id == rule_id and c.case_type == "correction"
        ]


class _MockLLMResult:
    def __init__(self, ok: bool, parsed_json: Any = None):
        self.ok = ok
        self.parsed_json = parsed_json


class _MockGateway:
    """Mock LLM gateway。"""

    def __init__(self, result: _MockLLMResult):
        self._result = result

    async def generate_json(self, **kwargs) -> _MockLLMResult:
        return self._result


def _make_rule(
    rule_id: str = "",
    status: str = "candidate",
    family: str = "anti_ai_discourse",
    shadow_hits: int = 0,
    shadow_strong_hits: int = 0,
    shadow_misses: int = 0,
    shadow_strong_misses: int = 0,
    consecutive_corrections: int = 0,
) -> NormalizationRule:
    return NormalizationRule(
        rule_id=rule_id,
        if_condition=RuleCondition(
            field="issue_type", op="equals", value="anti_ai"
        ),
        then_action=RuleAction(family=family),
        status=status,
        shadow_hits=shadow_hits,
        shadow_strong_hits=shadow_strong_hits,
        shadow_misses=shadow_misses,
        shadow_strong_misses=shadow_strong_misses,
        consecutive_corrections=consecutive_corrections,
    )


# ---------------------------------------------------------------------------
# CaseRecorder 测试
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_case_recorder_records_miss_case():
    """记录漏判案例 → 保存到仓储。"""
    repo = _MockRepo()
    recorder = CaseRecorder(repo=repo)

    await recorder.record_miss_case(
        {"issue_id": "iss-1", "type": "anti_ai"}, "anti_ai_discourse"
    )

    assert len(repo.cases) == 1
    case = repo.cases[0]
    assert case.case_type == "miss"
    assert case.llm_action == "supplement"
    assert case.llm_family == "anti_ai_discourse"


@pytest.mark.asyncio
async def test_case_recorder_records_correction_case():
    """记录误判纠正案例 → 保存到仓储。"""
    repo = _MockRepo()
    recorder = CaseRecorder(repo=repo)

    await recorder.record_correction_case(
        {"issue_id": "iss-1", "type": "anti_ai"}, "anti_ai_local"
    )

    assert len(repo.cases) == 1
    case = repo.cases[0]
    assert case.case_type == "correction"
    assert case.llm_action == "correct"


@pytest.mark.asyncio
async def test_case_recorder_triggers_aggregation_when_threshold_reached():
    """攒够 AGGREGATION_THRESHOLD 个案例 → 触发 LLM 提炼。"""
    repo = _MockRepo()
    # 预存 2 个案例（差一个就到阈值）
    for i in range(AGGREGATION_THRESHOLD - 1):
        repo.cases.append(NormalizationCase(
            case_id=f"pre_{i}",
            violation_json={"type": "anti_ai"},
            llm_family="anti_ai_discourse",
            case_type="miss",
            llm_action="supplement",
        ))

    recorder = CaseRecorder(repo=repo)

    mock_rule_json = {
        "if": {"field": "issue_type", "op": "equals", "value": "anti_ai", "case_insensitive": True},
        "then": {"family": "anti_ai_discourse", "operation": ""},
        "evidence": "test rule",
    }
    mock_gateway = _MockGateway(_MockLLMResult(ok=True, parsed_json=mock_rule_json))

    with patch(
        "app.services.fbi.normalization_evolution.get_llm_gateway",
        return_value=mock_gateway,
    ):
        await recorder.record_miss_case(
            {"issue_id": "iss-new", "type": "anti_ai"}, "anti_ai_discourse"
        )

    # 应该提炼出一条 candidate 规则
    assert len(repo.rules) == 1
    rule = list(repo.rules.values())[0]
    assert rule.status == "candidate"
    assert rule.then_action.family == "anti_ai_discourse"
    assert rule.evidence == "test rule"


@pytest.mark.asyncio
async def test_case_recorder_no_aggregation_below_threshold():
    """案例数不足 AGGREGATION_THRESHOLD → 不触发 LLM 提炼。"""
    repo = _MockRepo()
    recorder = CaseRecorder(repo=repo)

    # 只记录 1 个案例（阈值是 3）
    await recorder.record_miss_case(
        {"issue_id": "iss-1", "type": "anti_ai"}, "anti_ai_discourse"
    )

    assert len(repo.rules) == 0  # 没有提炼规则


@pytest.mark.asyncio
async def test_case_recorder_llm_failure_silent():
    """LLM 提炼失败 → 静默降级（不抛异常）。"""
    repo = _MockRepo()
    # 预存足够案例
    for i in range(AGGREGATION_THRESHOLD):
        repo.cases.append(NormalizationCase(
            case_id=f"pre_{i}",
            violation_json={"type": "anti_ai"},
            llm_family="anti_ai_discourse",
            case_type="miss",
            llm_action="supplement",
        ))

    recorder = CaseRecorder(repo=repo)
    mock_gateway = _MockGateway(_MockLLMResult(ok=False))

    with patch(
        "app.services.fbi.normalization_evolution.get_llm_gateway",
        return_value=mock_gateway,
    ):
        # 不应该抛异常
        await recorder.record_miss_case(
            {"issue_id": "iss-new", "type": "anti_ai"}, "anti_ai_discourse"
        )

    assert len(repo.rules) == 0  # LLM 失败，没提炼规则


# ---------------------------------------------------------------------------
# ShadowValidator 测试
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shadow_validator_updates_stats_on_match():
    """shadow 规则匹配 → 更新统计。"""
    repo = _MockRepo()
    rule = _make_rule(rule_id="r1", status="candidate", family="anti_ai_discourse")
    repo.rules["r1"] = rule

    validator = ShadowValidator(repo=repo)
    violation = {"type": "anti_ai", "issue_id": "iss-1"}

    await validator.validate(violation, "anti_ai_discourse", "agree")

    assert rule.shadow_hits == 1
    assert rule.shadow_strong_hits == 0  # agree 是弱基准


@pytest.mark.asyncio
async def test_shadow_validator_strong_baseline_correct():
    """action=correct → 强基准，更新 strong 统计。"""
    repo = _MockRepo()
    rule = _make_rule(rule_id="r1", status="candidate", family="anti_ai_discourse")
    repo.rules["r1"] = rule

    validator = ShadowValidator(repo=repo)
    violation = {"type": "anti_ai", "issue_id": "iss-1"}

    await validator.validate(violation, "anti_ai_discourse", "correct")

    assert rule.shadow_hits == 1
    assert rule.shadow_strong_hits == 1  # correct 是强基准


@pytest.mark.asyncio
async def test_shadow_validator_strong_baseline_supplement():
    """action=supplement → 强基准。"""
    repo = _MockRepo()
    rule = _make_rule(rule_id="r1", status="candidate", family="anti_ai_discourse")
    repo.rules["r1"] = rule

    validator = ShadowValidator(repo=repo)
    violation = {"type": "anti_ai", "issue_id": "iss-1"}

    await validator.validate(violation, "anti_ai_discourse", "supplement")

    assert rule.shadow_hits == 1
    assert rule.shadow_strong_hits == 1  # supplement 是强基准


@pytest.mark.asyncio
async def test_shadow_validator_miss_updates_misses():
    """规则判断与 LLM 不一致 → miss。"""
    repo = _MockRepo()
    rule = _make_rule(rule_id="r1", status="candidate", family="anti_ai_discourse")
    repo.rules["r1"] = rule

    validator = ShadowValidator(repo=repo)
    violation = {"type": "anti_ai", "issue_id": "iss-1"}

    # LLM 说 family 是 anti_ai_local，规则说 anti_ai_discourse → miss
    await validator.validate(violation, "anti_ai_local", "correct")

    assert rule.shadow_hits == 0
    assert rule.shadow_misses == 1
    assert rule.shadow_strong_misses == 1  # correct 是强基准


@pytest.mark.asyncio
async def test_shadow_validator_promotes_when_threshold_reached():
    """达到 SHADOW_MIN_SAMPLES + 一致率≥80% + 强基准占比≥30% → 转 active。"""
    repo = _MockRepo()
    # 构造一个即将达标的 shadow 规则
    # 需要 10 个样本，9 hit 1 miss → 一致率 90%
    # 强基准：3 correct + 1 miss correct → strong_hits=3, strong_misses=1 → 强基准占比 75%
    rule = _make_rule(
        rule_id="r1",
        status="candidate",
        family="anti_ai_discourse",
        shadow_hits=9,
        shadow_strong_hits=3,
        shadow_misses=1,
        shadow_strong_misses=1,
    )
    repo.rules["r1"] = rule

    validator = ShadowValidator(repo=repo)
    violation = {"type": "anti_ai", "issue_id": "iss-1"}

    # 再加一个 hit（强基准）→ 总样本 11，一致率 10/11≈91%，强基准占比 4/5=80%
    await validator.validate(violation, "anti_ai_discourse", "correct")

    updated_rule = repo.rules["r1"]
    assert updated_rule.status == "active"


@pytest.mark.asyncio
async def test_shadow_validator_rejects_low_consistency():
    """一致率 < 50% → retire。"""
    repo = _MockRepo()
    # 10 个样本，3 hit 7 miss → 一致率 30% < 50%
    rule = _make_rule(
        rule_id="r1",
        status="candidate",
        family="anti_ai_discourse",
        shadow_hits=3,
        shadow_strong_hits=2,
        shadow_misses=7,
        shadow_strong_misses=2,
    )
    repo.rules["r1"] = rule

    validator = ShadowValidator(repo=repo)
    violation = {"type": "anti_ai", "issue_id": "iss-1"}

    # 再加一个 miss → 总样本 11，一致率 3/11≈27%
    await validator.validate(violation, "other_family", "correct")

    updated_rule = repo.rules["r1"]
    assert updated_rule.status == "retired"


@pytest.mark.asyncio
async def test_shadow_validator_no_match_skips_rule():
    """规则不匹配 violation → 跳过该规则。"""
    repo = _MockRepo()
    rule = _make_rule(rule_id="r1", status="candidate", family="anti_ai_discourse")
    repo.rules["r1"] = rule

    validator = ShadowValidator(repo=repo)
    # violation type 不匹配规则条件
    violation = {"type": "spatial_conflict", "issue_id": "iss-1"}

    await validator.validate(violation, "anti_ai_discourse", "agree")

    assert rule.shadow_hits == 0
    assert rule.shadow_misses == 0


# ---------------------------------------------------------------------------
# RuleLifecycleManager 测试
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lifecycle_record_correction_increments_count():
    """记录纠正 → 计数+1。"""
    repo = _MockRepo()
    rule = _make_rule(rule_id="r1", status="active", consecutive_corrections=0)
    repo.rules["r1"] = rule

    lifecycle = RuleLifecycleManager(repo=repo)
    await lifecycle.record_correction("r1")

    assert rule.consecutive_corrections == 1


@pytest.mark.asyncio
async def test_lifecycle_suspect_at_threshold():
    """纠正次数达到 SUSPECT_THRESHOLD → status=suspect。"""
    repo = _MockRepo()
    rule = _make_rule(
        rule_id="r1", status="active", consecutive_corrections=SUSPECT_THRESHOLD - 1
    )
    repo.rules["r1"] = rule

    lifecycle = RuleLifecycleManager(repo=repo)
    await lifecycle.record_correction("r1")

    assert rule.consecutive_corrections == SUSPECT_THRESHOLD
    assert rule.status == "suspect"


@pytest.mark.asyncio
async def test_lifecycle_retire_at_threshold():
    """纠正次数达到 RETIREMENT_THRESHOLD → status=retired + 触发修订。"""
    repo = _MockRepo()
    rule = _make_rule(
        rule_id="r1", status="active", consecutive_corrections=RETIREMENT_THRESHOLD - 1
    )
    repo.rules["r1"] = rule

    mock_rule_json = {
        "if": {"field": "issue_type", "op": "contains", "value": "anti", "case_insensitive": True},
        "then": {"family": "anti_ai_local", "operation": ""},
        "evidence": "revised",
    }
    mock_gateway = _MockGateway(_MockLLMResult(ok=True, parsed_json=mock_rule_json))

    lifecycle = RuleLifecycleManager(repo=repo)
    with patch(
        "app.services.fbi.normalization_evolution.get_llm_gateway",
        return_value=mock_gateway,
    ):
        await lifecycle.record_correction("r1")

    assert rule.status == "retired"
    # 应该产生一条新的 candidate 规则（修订版）
    new_rules = [r for r in repo.rules.values() if r.status == "candidate"]
    assert len(new_rules) == 1
    assert new_rules[0].source_rule_id == "r1"
    assert new_rules[0].then_action.family == "anti_ai_local"


@pytest.mark.asyncio
async def test_lifecycle_record_agree_resets_count():
    """记录 agree → 重置连续纠正计数。"""
    repo = _MockRepo()
    rule = _make_rule(rule_id="r1", status="active", consecutive_corrections=2)
    repo.rules["r1"] = rule

    lifecycle = RuleLifecycleManager(repo=repo)
    await lifecycle.record_agree("r1")

    assert rule.consecutive_corrections == 0


@pytest.mark.asyncio
async def test_lifecycle_final_acceptance_failure_to_suspect():
    """Final Acceptance 失败 → active 规则进 suspect。"""
    repo = _MockRepo()
    rule = _make_rule(rule_id="r1", status="active")
    repo.rules["r1"] = rule

    lifecycle = RuleLifecycleManager(repo=repo)
    await lifecycle.record_final_acceptance_failure("r1")

    assert rule.status == "suspect"


@pytest.mark.asyncio
async def test_lifecycle_ignores_non_active_rule():
    """非 active 规则 → 不处理。"""
    repo = _MockRepo()
    rule = _make_rule(rule_id="r1", status="candidate", consecutive_corrections=0)
    repo.rules["r1"] = rule

    lifecycle = RuleLifecycleManager(repo=repo)
    await lifecycle.record_correction("r1")

    assert rule.consecutive_corrections == 0  # 未处理
    assert rule.status == "candidate"  # 状态不变


@pytest.mark.asyncio
async def test_lifecycle_revision_llm_failure_silent():
    """LLM 修订失败 → 静默降级（不抛异常，规则仍 retire）。"""
    repo = _MockRepo()
    rule = _make_rule(
        rule_id="r1", status="active", consecutive_corrections=RETIREMENT_THRESHOLD - 1
    )
    repo.rules["r1"] = rule

    mock_gateway = _MockGateway(_MockLLMResult(ok=False))
    lifecycle = RuleLifecycleManager(repo=repo)
    with patch(
        "app.services.fbi.normalization_evolution.get_llm_gateway",
        return_value=mock_gateway,
    ):
        # 不应该抛异常
        await lifecycle.record_correction("r1")

    assert rule.status == "retired"
    # LLM 失败，没产生新规则
    new_rules = [r for r in repo.rules.values() if r.status == "candidate"]
    assert len(new_rules) == 0
