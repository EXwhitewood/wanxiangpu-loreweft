"""执法分级体系单元测试。

验证两条核心产品决策：
1. 破折号 = hard_blocking（铁律硬约束），修复耗尽后 waiting_review
2. 其余主观指标 = advisory，仅记录，不进入自动修订收敛

覆盖：
- metric_registry: get_enforcement / is_hard_blocking / is_advisory / determine_blocks_commit
- structured_memory_compiler: compile_quality_policy 不再生成 quality_gate_candidate
- quality_memory_service: update_quality_memory 去重逻辑
- agent_skill_validator: is_advisory_only_metric 基于 enforcement 分级
"""
from __future__ import annotations

import pytest

from app.services.metric_registry import (
    ENFORCEMENT_OVERRIDES,
    determine_blocks_commit,
    get_enforcement,
    is_advisory,
    is_hard_blocking,
)
from app.services.structured_memory_compiler import StructuredMemoryCompiler
from app.services.quality_memory_service import QualityMemoryService


# ---------------------------------------------------------------------------
# 1. metric_registry: enforcement 分级正确性
# ---------------------------------------------------------------------------

class TestEnforcementGrading:
    """enforcement 分级体系：hard_blocking / advisory / observe_only"""

    # 破折号铁律 = hard_blocking
    @pytest.mark.parametrize("metric", [
        "dash_per_1000",
        "ai_punctuation_artifact",
        "enforced_ai_punctuation_artifact",
        "dash_artifact_hits",
        "emdash_pair_hits",
        "emdash_hits",
        "consecutive_dash_max",
        "forbidden_triggered",
        "missing_must_show",
        "ending_state_not_reached",
    ])
    def test_dash_metrics_are_hard_blocking(self, metric: str):
        """破折号相关指标必须是 hard_blocking"""
        assert is_hard_blocking(metric) is True
        assert is_advisory(metric) is False
        assert determine_blocks_commit(metric, "high") is True
        assert determine_blocks_commit(metric, "medium") is True
        assert determine_blocks_commit(metric, "low") is True

    # 主观指标 = advisory
    @pytest.mark.parametrize("metric", [
        "ai_simile_overuse",
        "ai_false_range",
        "ai_negative_parallel",
        "low_conflict_density",
        "weak_curiosity_engine",
        "low_event_density",
        "weak_opening_hook",
        "low_reader_retention",
        "flat_pressure_ramp",
        "low_reversal_density",
        "missing_micro_payoff",
        "weak_chapter_end_hook",
        "emotion_label_count",
        "sensory_mode_count",
        "style_requires_human_review",
        "forbidden_word",
        "avoid_pattern_violation",
        "style_directive_deviation",
        "voice_phrase_drift",
    ])
    def test_subjective_metrics_are_advisory(self, metric: str):
        """主观指标必须是 advisory，不阻断 commit"""
        assert is_advisory(metric) is True
        assert is_hard_blocking(metric) is False
        # advisory 无论 severity 多高都不阻断
        assert determine_blocks_commit(metric, "high") is False
        assert determine_blocks_commit(metric, "critical") is False

    def test_advisory_has_no_automatic_repair_cycles(self):
        """advisory 指标只记录，不消耗自动修订轮次。"""
        for metric in ["ai_simile_overuse", "low_conflict_density", "weak_curiosity_engine"]:
            info = get_enforcement(metric)
            assert info["max_repair_cycles"] == 0
            assert info["after_exhaustion"] == "allow_commit"

    def test_hard_blocking_after_exhaustion_is_waiting_review(self):
        """hard_blocking 指标的 after_exhaustion 必须为 waiting_review"""
        for metric in ["dash_per_1000", "ai_punctuation_artifact"]:
            info = get_enforcement(metric)
            assert info["after_exhaustion"] == "waiting_review"

    def test_memory_can_promote_always_false(self):
        """所有指标的 memory_can_promote 必须为 False（质量记忆不能改变 enforcement）"""
        for metric, info in ENFORCEMENT_OVERRIDES.items():
            assert info["memory_can_promote"] is False, f"{metric} 的 memory_can_promote 应为 False"
        # 未知 metric 也应该是 False
        assert get_enforcement("unknown_metric_xyz")["memory_can_promote"] is False

    def test_unknown_metric_defaults_to_advisory(self):
        """未知 metric 默认为 advisory（不阻断）"""
        assert is_hard_blocking("unknown_metric_xyz") is False
        assert is_advisory("unknown_metric_xyz") is True
        assert determine_blocks_commit("unknown_metric_xyz", "high") is False

    def test_case_insensitive(self):
        """enforcement 判定应大小写不敏感"""
        assert is_hard_blocking("AI_Punctuation_Artifact") is True
        assert is_hard_blocking("Dash_Per_1000") is True
        assert is_advisory("AI_Simile_Overuse") is True


def test_ai_flavor_checker_emits_dash_metrics_without_second_threshold():
    """Checker reports raw data; the Skill contract owns 0.5/1000 and max 2."""
    from app.services.quality_checkers.ai_flavor_checker import AIFlavorChecker

    text = "甲" * 10000 + "—" * 6
    report = AIFlavorChecker().check(text)

    assert report["metrics"]["emdash_pair_hits"] == 3
    assert report["metrics"]["max_consecutive_emdash_pairs"] == 3
    assert not any(
        item.get("type") == "ai_punctuation_artifact"
        for item in report["advisories"]
    )


@pytest.mark.asyncio
async def test_advisory_exhaustion_demotes_authoritative_packet_and_allows_commit():
    from app.api.editor_chat import _commit_gate_check, _mark_advisories_exhausted
    from app.models.chapter_review import SceneReviewPacket

    packet = SceneReviewPacket(
        scene_index=0,
        blocking_violations=[{
            "type": "enforced_ai_simile_overuse",
            "severity": "high",
            "blocks_commit": True,
        }],
    )

    exhausted = _mark_advisories_exhausted([packet], repair_attempts=2)
    result = await _commit_gate_check(
        execution_id="test",
        review_packets=[packet],
        current_repair_plan=None,
        db=None,
        final_text="正文完整。",
        scene_texts={0: "正文完整。"},
        chapter_state={"established_facts": ["事实"]},
        style_result={},
        skill_gate_result={"allowed": True},
    )

    assert len(exhausted) == 1
    assert packet.blocking_violations == []
    assert packet.advisory_violations[0]["advisory_exhausted"] is True
    assert result["allowed"] is True


def test_hard_required_outcome_is_never_demoted_as_advisory():
    from app.api.editor_chat import _mark_advisories_exhausted
    from app.models.chapter_review import SceneReviewPacket

    packet = SceneReviewPacket(
        scene_index=0,
        advisory_violations=[{
            "type": "missing_must_show",
            "severity": "medium",
            "blocks_commit": False,
        }],
    )

    exhausted = _mark_advisories_exhausted([packet], repair_attempts=2)

    assert exhausted == []
    assert packet.advisory_violations == []
    assert packet.blocking_violations[0]["type"] == "missing_must_show"
    assert packet.blocking_violations[0]["blocks_commit"] is True


def test_quality_gate_ai_flavor_uses_metric_enforcement_not_high_severity():
    from app.services.quality_gate import QualityGate

    violations = QualityGate()._enforced_advisory_violations(
        {
            "ai_flavor": {
                "advisories": [
                    {"type": "ai_simile_overuse", "severity": "high", "detail": "simile"},
                    {"type": "ai_punctuation_artifact", "severity": "high", "detail": "dash"},
                ]
            }
        },
        {"ai_flavor_mode": "enforce"},
        "hash",
        1,
    )

    by_type = {item["type"]: item for item in violations}
    assert by_type["enforced_ai_simile_overuse"]["blocks_commit"] is False
    assert by_type["enforced_ai_punctuation_artifact"]["blocks_commit"] is True


# ---------------------------------------------------------------------------
# 2. structured_memory_compiler: 质量记忆不升级 enforcement
# ---------------------------------------------------------------------------

class TestQualityPolicyNoPromotion:
    """质量记忆累计次数不能将 advisory 升级为 quality_gate_candidate"""

    def test_high_count_does_not_produce_quality_gate_candidate(self):
        """即使 count >= 5，也不应生成 quality_gate_candidate level"""
        compiler = StructuredMemoryCompiler()
        quality_memory = {
            "recent_quality_patterns": [
                {
                    "type": "ai_simile_overuse_trend",
                    "count": 10,  # 远超 5
                    "suggestion": "减少明喻使用",
                    "last_seen_chapter": 24,
                    "last_seen_scene": 1,
                },
                {
                    "type": "low_conflict_density_trend",
                    "count": 8,
                    "suggestion": "提高冲突密度",
                    "last_seen_chapter": 24,
                    "last_seen_scene": 2,
                },
            ]
        }
        policy = compiler.compile_quality_policy(quality_memory)

        # quality_gate_candidates 必须为空（不再生成）
        assert policy["quality_gate_candidates"] == []

        # 但 writer_constraints 可以包含高累计项
        writer_constraint_types = [a["type"] for a in policy["writer_constraints"]]
        assert "ai_simile_overuse_trend" in writer_constraint_types
        assert "low_conflict_density_trend" in writer_constraint_types

        # 所有 action 的 level 不应是 quality_gate_candidate
        for action in policy["policy_actions"]:
            assert action["level"] != "quality_gate_candidate"

    def test_low_count_does_not_produce_actions(self):
        """count < 2 的 pattern 不应生成任何 action"""
        compiler = StructuredMemoryCompiler()
        quality_memory = {
            "recent_quality_patterns": [
                {"type": "ai_simile_overuse_trend", "count": 1, "suggestion": "test"},
            ]
        }
        policy = compiler.compile_quality_policy(quality_memory)
        assert policy["policy_actions"] == []
        assert policy["writer_constraints"] == []
        assert policy["quality_gate_candidates"] == []


# ---------------------------------------------------------------------------
# 3. quality_memory_service: 去重逻辑
# ---------------------------------------------------------------------------

class TestQualityMemoryDedup:
    """同一 chapter+scene 的同一 advisory_type 只累计一次"""

    def test_same_scene_does_not_double_count(self):
        """同一场景多次复检不应重复增加计数"""
        service = QualityMemoryService()
        existing_memory = {"recent_quality_patterns": [], "recent_ai_flavor_excerpts": []}

        advisories = [
            {
                "type": "ai_simile_overuse",
                "severity": "high",
                "confidence": 0.9,
                "detector": "ai_flavor_checker",
                "target_span": "像一阵风似的",
            }
        ]

        # 第一次：chapter 24, scene 1
        memory = service.update_quality_memory(
            existing_memory,
            advisories=advisories,
            chapter_number=24,
            scene_index=1,
        )
        pattern = memory["recent_quality_patterns"][0]
        assert pattern["count"] == 1

        # 第二次：同一 chapter 24, scene 1（复检）
        memory = service.update_quality_memory(
            memory,
            advisories=advisories,
            chapter_number=24,
            scene_index=1,
        )
        pattern = memory["recent_quality_patterns"][0]
        assert pattern["count"] == 1  # 不应增加

        # 第三次：不同 chapter 24, scene 2
        memory = service.update_quality_memory(
            memory,
            advisories=advisories,
            chapter_number=24,
            scene_index=2,
        )
        pattern = memory["recent_quality_patterns"][0]
        assert pattern["count"] == 2  # 应增加

    def test_different_advisory_types_count_separately(self):
        """不同 advisory_type 在同一场景应分别计数"""
        service = QualityMemoryService()
        existing_memory = {"recent_quality_patterns": [], "recent_ai_flavor_excerpts": []}

        # scene 1: ai_simile_overuse
        memory = service.update_quality_memory(
            existing_memory,
            advisories=[
                {"type": "ai_simile_overuse", "severity": "high", "confidence": 0.9,
                 "detector": "ai_flavor_checker", "target_span": "像一阵风"},
            ],
            chapter_number=24,
            scene_index=1,
        )

        # scene 1: low_conflict_density（不同 type）
        memory = service.update_quality_memory(
            memory,
            advisories=[
                {"type": "low_conflict_density", "severity": "high", "confidence": 0.9,
                 "detector": "commercial_pacing_checker"},
            ],
            chapter_number=24,
            scene_index=1,
        )

        patterns = {p["type"]: p for p in memory["recent_quality_patterns"]}
        assert patterns["ai_simile_overuse"]["count"] == 1
        # low_conflict_density 被 _QUALITY_MEMORY_PATTERN_TYPES 映射为 low_conflict_density_trend
        assert patterns["low_conflict_density_trend"]["count"] == 1


# ---------------------------------------------------------------------------
# 4. agent_skill_validator: is_advisory_only_metric 基于 enforcement
# ---------------------------------------------------------------------------

class TestAgentSkillValidatorAdvisory:
    """is_advisory_only_metric 必须基于 metric_registry 的 enforcement 分级"""

    def test_advisory_metric_returns_true(self):
        """advisory 指标应返回 True"""
        from app.services.agent_skill_validator import is_advisory_only_metric
        assert is_advisory_only_metric("ai_simile_overuse") is True
        assert is_advisory_only_metric("low_conflict_density") is True
        assert is_advisory_only_metric("weak_curiosity_engine") is True

    def test_hard_blocking_metric_returns_false(self):
        """hard_blocking 指标应返回 False"""
        from app.services.agent_skill_validator import is_advisory_only_metric
        assert is_advisory_only_metric("ai_punctuation_artifact") is False
        assert is_advisory_only_metric("dash_per_1000") is False

    def test_unknown_metric_defaults_to_advisory(self):
        """未知 metric 默认为 advisory，应返回 True"""
        from app.services.agent_skill_validator import is_advisory_only_metric
        assert is_advisory_only_metric("unknown_metric_xyz") is True
