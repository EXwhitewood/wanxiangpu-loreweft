"""方案 7：统一 validator（含 metric 命名统一）测试。

覆盖场景：
1. METRIC_REGISTRY 完整性（包含关键 metric，category/severity 字段齐全）
2. determine_blocks_commit 行为（按 severity 和 metric 精细决定阻断）
3. text_hash 补齐（ChapterCommitHealthChecker.evaluate 输出含 text_hash 和 metric）
4. literary_quality 统一（metric=literary_quality_advisory + source_implementation + delegated_to）
5. ValidatorCollection.validate 合并 violations（三套 validator 输出归一化）
6. is_stale_finding 行为（stale 检测保守策略）
7. 阻断一致性（advisory issue blocks_commit=False，三套 validator 一致）
"""
from __future__ import annotations

import pytest

from app.services.chapter_commit_health import ChapterCommitHealthChecker
from app.services.metric_registry import (
    METRIC_REGISTRY,
    MetricRegistry,
    determine_blocks_commit,
    get_metric_info,
)
from app.services.validator_collection import ValidatorCollection


# ----------------------------------------------------------------------
# 1. METRIC_REGISTRY 完整性
# ----------------------------------------------------------------------


def test_metric_registry_contains_key_metrics_with_category_and_severity():
    """METRIC_REGISTRY 应包含各域的关键 metric，且每条都有 category 和 severity。"""
    # 抽样检查各域的关键 metric 都在注册表中
    expected_metrics = [
        # skill 域
        "tier1_hit_count",
        "dash_per_1000",
        "sentence_shell_count",
        "head_hopping_count",
        "goal_present",
        "literary_quality_advisory",
        # quality 域
        "low_conflict_density",
        "weak_curiosity_engine",
        "missing_micro_payoff",
        # consistency 域
        "fact_conflict",
        "naming_conflict",
        "timeline_conflict",
        # health 域
        "empty_scene_text",
        "residual_scene_marker",
        "style_requires_human_review",
        # system 域
        "consistency_check_unavailable",
        "skill_validator_not_implemented",
    ]
    for metric in expected_metrics:
        assert metric in METRIC_REGISTRY, f"METRIC_REGISTRY 缺少 metric: {metric}"
        info = METRIC_REGISTRY[metric]
        assert "category" in info, f"metric {metric} 缺少 category 字段"
        assert "severity" in info, f"metric {metric} 缺少 severity 字段"
        assert info["category"] in {
            "skill", "quality", "consistency", "health", "system"
        }, f"metric {metric} 的 category 值非法: {info['category']}"
        assert info["severity"] in {"blocking", "advisory"}, (
            f"metric {metric} 的 severity 值非法: {info['severity']}"
        )


def test_metric_registry_preserves_legacy_constants():
    """方案 7 约束：不删除原有 5 个 MetricRegistry 常量。"""
    assert MetricRegistry.CONSISTENCY_CHECK_UNAVAILABLE == "consistency_check_unavailable"
    assert MetricRegistry.CRITIC_PARSE_ERROR == "critic_parse_error"
    assert MetricRegistry.FCIP_CHECK_UNAVAILABLE == "fcip_check_unavailable"
    assert MetricRegistry.SKILL_VALIDATOR_IMPLEMENTED == "skill_validator_implemented"
    assert MetricRegistry.SKILL_VALIDATOR_NOT_IMPLEMENTED == "skill_validator_not_implemented"


def test_get_metric_info_returns_unknown_for_unregistered():
    """get_metric_info 对未知 metric 返回 advisory 默认值。"""
    info = get_metric_info("nonexistent_metric_xyz")
    assert info["category"] == "unknown"
    assert info["severity"] == "advisory"

    # 空 metric 也返回 advisory
    info = get_metric_info("")
    assert info["severity"] == "advisory"


# ----------------------------------------------------------------------
# 2. determine_blocks_commit 行为
# ----------------------------------------------------------------------


def test_determine_blocks_commit_by_enforcement_not_severity():
    """severity 不能把未知或 advisory metric 临时升级成阻断。"""
    for severity in ("critical", "blocking", "high", "advisory", "low", "info"):
        assert determine_blocks_commit("any_metric", severity) is False

    # 已注册硬边界也不能被较低 severity 临时降级。
    assert determine_blocks_commit("fact_conflict", "low") is True


def test_determine_blocks_commit_uses_registry_enforcement():
    """所有 severity 都由统一 enforcement 表决定阻断。"""
    # AI 味阈值是可观测风格指标，不阻断。
    assert determine_blocks_commit("tier1_hit_count", "medium") is False

    # 事实冲突是硬边界。
    assert determine_blocks_commit("fact_conflict", "medium") is True

    # literary_quality_advisory 在 registry 中是 advisory → False
    assert determine_blocks_commit("literary_quality_advisory", "medium") is False

    # 未知 metric → advisory → False
    assert determine_blocks_commit("nonexistent_metric_xyz", "medium") is False


def test_determine_blocks_commit_case_insensitive():
    """severity 大小写不会改变 metric 的 enforcement。"""
    assert determine_blocks_commit("any_metric", "HIGH") is False
    assert determine_blocks_commit("any_metric", "Critical") is False
    assert determine_blocks_commit("any_metric", "Advisory") is False
    assert determine_blocks_commit("fact_conflict", "Advisory") is True


# ----------------------------------------------------------------------
# 3. text_hash 补齐（ChapterCommitHealthChecker）
# ----------------------------------------------------------------------


def test_chapter_commit_health_evaluate_injects_text_hash_and_metric():
    """ChapterCommitHealthChecker.evaluate 应为所有 issue 注入 text_hash 和 metric。"""
    checker = ChapterCommitHealthChecker()
    # 构造一个会触发 empty_scene_text 的场景
    result = checker.evaluate(
        scene_texts={0: "", 1: "有内容的场景文本。"},
        text_hash="abc123",
    )

    issues = result.get("issues") or []
    assert len(issues) >= 1
    empty_scene_issue = next(
        (i for i in issues if i.get("code") == "empty_scene_text"), None
    )
    assert empty_scene_issue is not None, "应检测到 empty_scene_text issue"
    # Part D：text_hash 和 metric 字段应被注入
    assert empty_scene_issue["text_hash"] == "abc123"
    assert empty_scene_issue["metric"] == "empty_scene_text"


def test_chapter_commit_health_issue_metric_defaults_to_code():
    """CommitHealthIssue.to_dict() 中 metric 默认等于 code。"""
    checker = ChapterCommitHealthChecker()
    result = checker.evaluate(
        scene_texts={0: ""},
        text_hash="test_hash",
    )
    issues = result.get("issues") or []
    for issue in issues:
        # metric 字段应存在且非空（等于 code 或显式设置的值）
        assert issue.get("metric"), f"issue 缺少 metric 字段: {issue}"
        assert issue.get("text_hash") == "test_hash"


# ----------------------------------------------------------------------
# 4. literary_quality 统一（Part E）
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_literary_quality_metric_unified_to_advisory():
    """AgentSkillValidator 的 literary_quality finding metric 应统一为 literary_quality_advisory。"""
    from app.models.agent_skill import CompiledSkillPacket
    from app.services.agent_skill_validator import AgentSkillValidator

    packet = CompiledSkillPacket(
        agent_name="core_generation",
        active_skills=["strict_literary_quality"],
        validators=["literary_quality"],
        validation_contracts={
            "literary_quality": {
                "strict_literary_quality": {
                    "max_medium_advisories": 0,
                }
            }
        },
    )
    text = "命运与意义交织，复杂情绪在内心深处浮动。" * 20

    result = await AgentSkillValidator().validate_output(
        packet,
        generated_text=text,
        context={
            "literary_quality_contract": {
                "specificity_budget": {"abstract_explanation_limit": 1}
            }
        },
    )

    assert result["passed"] is True
    # Part E：metric 统一为 literary_quality_advisory
    literary_findings = [
        item
        for item in result["validators"]["literary_quality"]["findings"]
        if item.get("metric") == "literary_quality_advisory"
    ]
    assert literary_findings, "应存在 metric=literary_quality_advisory 的 finding"

    finding = literary_findings[0]
    # original_metric 保留原始 metric（medium_advisories / high_advisories / mode_fit）
    assert finding.get("original_metric"), "original_metric 字段不应为空"
    # source_implementation 标注来源为确定性 metrics
    assert finding.get("source_implementation") == "deterministic_metrics"
    # delegated_to 标注 LLM 判断由 QualityGate 负责
    assert finding.get("delegated_to") == "quality_gate_llm"


@pytest.mark.asyncio
async def test_literary_quality_text_hash_injected():
    """AgentSkillValidator 应为 literary_quality finding 注入 text_hash。"""
    from app.models.agent_skill import CompiledSkillPacket
    from app.services.agent_skill_validator import AgentSkillValidator

    packet = CompiledSkillPacket(
        agent_name="core_generation",
        active_skills=["strict_literary_quality"],
        validators=["literary_quality"],
        validation_contracts={
            "literary_quality": {
                "strict_literary_quality": {
                    "max_medium_advisories": 0,
                }
            }
        },
    )
    text = "命运与意义交织，复杂情绪在内心深处浮动。" * 20

    result = await AgentSkillValidator().validate_output(
        packet,
        generated_text=text,
        context={
            "literary_quality_contract": {
                "specificity_budget": {"abstract_explanation_limit": 1}
            },
            "text_hash": "explicit_hash_123",
        },
    )

    # Part D：所有 finding 应有 text_hash
    assert result.get("text_hash"), "validate_output 返回值应有 text_hash"
    for finding in result.get("failures") or []:
        assert finding.get("text_hash"), f"finding 缺少 text_hash: {finding}"


# ----------------------------------------------------------------------
# 5. ValidatorCollection.validate 合并 violations
# ----------------------------------------------------------------------


def test_validator_collection_merges_three_sources():
    """ValidatorCollection.validate 应合并三套 validator 的输出。"""
    collection = ValidatorCollection()

    quality_gate_result = {
        "violations": [
            {
                "type": "fact_conflict",
                "severity": "critical",
                "detail": "事实冲突",
                "blocks_commit": True,
                "text_hash": "shared_hash",
                "source": "consistency",
                "target_span": "某段文本",
            }
        ]
    }
    skill_validation = {
        "failures": [
            {
                "metric": "tier1_hit_count",
                "validator": "ai_flavor",
                "severity": "high",
                "actual": 5,
                "expected_max": 0,
                "reason": "tier1 命中过多",
                "text_hash": "shared_hash",
            }
        ]
    }
    health_check_result = {
        "issues": [
            {
                "code": "empty_scene_text",
                "severity": "hard",
                "detail": "场景文本为空",
                "metric": "empty_scene_text",
                "text_hash": "shared_hash",
                "scene_index": 0,
            }
        ]
    }

    result = collection.validate(
        text="some text",
        quality_gate_result=quality_gate_result,
        skill_validation=skill_validation,
        health_check_result=health_check_result,
        text_hash="shared_hash",
    )

    violations = result["violations"]
    # 三套 validator 各贡献 1 个 violation，共 3 个
    assert len(violations) == 3

    # 验证各 source_collection 标记
    collections = {v["source_collection"] for v in violations}
    assert collections == {"quality_gate", "skill_validator", "health_checker"}

    # 验证统一 metric / type 字段
    metrics = {v["metric"] for v in violations}
    assert "fact_conflict" in metrics
    assert "tier1_hit_count" in metrics
    assert "empty_scene_text" in metrics

    # 验证 text_hash 统一
    for v in violations:
        assert v["text_hash"] == "shared_hash"

    # 有 blocking violation → passed=False
    assert result["passed"] is False


def test_validator_collection_normalizes_health_hard_to_critical():
    """health checker 的 'hard' severity 应映射为 'critical'。"""
    collection = ValidatorCollection()
    health_check_result = {
        "issues": [
            {
                "code": "empty_scene_text",
                "severity": "hard",
                "detail": "场景为空",
                "metric": "empty_scene_text",
            }
        ]
    }
    result = collection.validate(
        text="",
        health_check_result=health_check_result,
        text_hash="hash1",
    )
    violations = result["violations"]
    assert len(violations) == 1
    assert violations[0]["severity"] == "critical"
    assert violations[0]["blocks_commit"] is True


def test_validator_collection_dedupes_same_metric_and_validator():
    """同一 metric + target_span + source_validator 应去重。"""
    collection = ValidatorCollection()
    skill_validation = {
        "failures": [
            {
                "metric": "tier1_hit_count",
                "validator": "ai_flavor",
                "severity": "high",
                "actual": 5,
                "expected_max": 0,
                "reason": "第一次报告",
                "target_span": "某段",
            },
            {
                "metric": "tier1_hit_count",
                "validator": "ai_flavor",
                "severity": "high",
                "actual": 5,
                "expected_max": 0,
                "reason": "第二次报告（重复）",
                "target_span": "某段",
            },
        ]
    }
    result = collection.validate(
        text="text",
        skill_validation=skill_validation,
        text_hash="hash",
    )
    # 去重后只保留 1 个
    assert len(result["violations"]) == 1


def test_validator_collection_passed_when_no_blocking():
    """无 blocking violation 时 passed=True。"""
    collection = ValidatorCollection()
    skill_validation = {
        "failures": [
            {
                "metric": "literary_quality_advisory",
                "validator": "literary_quality",
                "severity": "advisory",
                "actual": 3,
                "expected_max": 0,
                "reason": "advisory 问题",
            }
        ]
    }
    result = collection.validate(
        text="text",
        skill_validation=skill_validation,
        text_hash="hash",
    )
    # literary_quality_advisory → advisory → blocks_commit=False → passed=True
    assert result["passed"] is True
    assert result["violations"][0]["blocks_commit"] is False


# ----------------------------------------------------------------------
# 6. is_stale_finding 行为
# ----------------------------------------------------------------------


def test_is_stale_finding_returns_false_when_hash_matches():
    """finding 的 text_hash 与当前一致时，非 stale。"""
    collection = ValidatorCollection()
    finding = {"metric": "tier1_hit_count", "text_hash": "abc123"}
    assert collection.is_stale_finding(finding, current_text_hash="abc123") is False


def test_is_stale_finding_returns_true_when_hash_mismatches():
    """finding 的 text_hash 与当前不一致时，stale。"""
    collection = ValidatorCollection()
    finding = {"metric": "tier1_hit_count", "text_hash": "old_hash"}
    assert collection.is_stale_finding(finding, current_text_hash="new_hash") is True


def test_is_stale_finding_returns_false_when_no_finding_hash():
    """finding 无 text_hash 时，保守不标记 stale。"""
    collection = ValidatorCollection()
    finding = {"metric": "tier1_hit_count"}
    assert collection.is_stale_finding(finding, current_text_hash="current_hash") is False


def test_is_stale_finding_returns_false_when_no_current_hash():
    """当前 text_hash 为空时，保守不标记 stale。"""
    collection = ValidatorCollection()
    finding = {"metric": "tier1_hit_count", "text_hash": "old_hash"}
    assert collection.is_stale_finding(finding, current_text_hash="") is False


# ----------------------------------------------------------------------
# 7. 阻断一致性（advisory issue blocks_commit=False）
# ----------------------------------------------------------------------


def test_advisory_metric_blocks_commit_false_across_validators():
    """advisory metric 在三套 validator 中 blocks_commit 都应为 False。"""
    collection = ValidatorCollection()

    # QualityGate: literary_quality_advisory metric，advisory severity
    quality_gate_result = {
        "violations": [
            {
                "type": "literary_quality_advisory",
                "metric": "literary_quality_advisory",
                "severity": "advisory",
                "detail": "文学质量 advisory",
                "source": "literary_quality",
            }
        ]
    }
    # skill: literary_quality_advisory metric，advisory severity
    skill_validation = {
        "failures": [
            {
                "metric": "literary_quality_advisory",
                "validator": "literary_quality",
                "severity": "advisory",
                "actual": 3,
                "expected_max": 0,
                "reason": "advisory",
            }
        ]
    }
    # health: advisory 问题（非 hard）
    health_check_result = {
        "issues": [
            {
                "code": "scene_too_long",
                "severity": "advisory",
                "detail": "场景过长",
                "metric": "scene_too_long",
            }
        ]
    }

    result = collection.validate(
        text="text",
        quality_gate_result=quality_gate_result,
        skill_validation=skill_validation,
        health_check_result=health_check_result,
        text_hash="hash",
    )

    # 所有 advisory issue 都不应阻断
    for v in result["violations"]:
        assert v["blocks_commit"] is False, (
            f"advisory metric {v['metric']} 不应阻断 commit，但 blocks_commit=True"
        )
    assert result["passed"] is True


def test_validator_collection_preserves_hard_boundaries_and_style_advisories():
    """统一收集时只让事实/健康硬边界阻断，AI 风格指标保持 advisory。"""
    collection = ValidatorCollection()

    quality_gate_result = {
        "violations": [
            {
                "type": "fact_conflict",
                "metric": "fact_conflict",
                "severity": "critical",
                "detail": "事实冲突",
                "source": "consistency",
            }
        ]
    }
    skill_validation = {
        "failures": [
            {
                "metric": "tier1_hit_count",
                "validator": "ai_flavor",
                "severity": "high",
                "actual": 5,
                "expected_max": 0,
                "reason": "tier1 命中",
            }
        ]
    }
    health_check_result = {
        "issues": [
            {
                "code": "empty_scene_text",
                "severity": "hard",
                "detail": "场景为空",
                "metric": "empty_scene_text",
            }
        ]
    }

    result = collection.validate(
        text="text",
        quality_gate_result=quality_gate_result,
        skill_validation=skill_validation,
        health_check_result=health_check_result,
        text_hash="hash",
    )

    by_metric = {item["metric"]: item for item in result["violations"]}
    assert by_metric["fact_conflict"]["blocks_commit"] is True
    assert by_metric["empty_scene_text"]["blocks_commit"] is True
    assert by_metric["tier1_hit_count"]["blocks_commit"] is False
    assert result["passed"] is False


def test_validator_collection_text_hash_fallback_from_text():
    """text_hash 为空时，应从 text 计算 md5 兜底。"""
    collection = ValidatorCollection()
    skill_validation = {
        "failures": [
            {
                "metric": "tier1_hit_count",
                "validator": "ai_flavor",
                "severity": "high",
                "actual": 5,
                "expected_max": 0,
                "reason": "tier1",
            }
        ]
    }
    result = collection.validate(
        text="hello world",
        skill_validation=skill_validation,
    )
    # 应计算 md5("hello world")
    import hashlib
    expected_hash = hashlib.md5(b"hello world").hexdigest()
    assert result["text_hash"] == expected_hash
    # finding 也应被注入兜底 hash
    assert result["violations"][0]["text_hash"] == expected_hash
