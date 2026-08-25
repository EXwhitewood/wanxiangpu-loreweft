"""Tests for AIQualityCoordinatorAgent — 各模块开关独立生效.

覆盖要点：
- source_modes 替代全局 policy_mode
- _collect_advisories 标记 source_checker
- guidance/revision 按来源模式独立过滤
- report 模式的检测器不生成 guidance/revision
- enforce 模式的检测器不会带升级 report 模式的 advisory
- 深合并 quality_extensions
- 质量记忆按来源模式过滤
- new_concept_budget int() 安全转换
"""

import pytest

from app.agents.ai_quality_coordinator import (
    AIQualityCoordinatorAgent,
    CoordinatorInput,
    CoordinatorOutput,
    _update_quality_memory,
    _effective_mode_for_advisory,
    _any_mode_at_least,
    _ADVISORY_TYPE_LABELS,
    _ADVISORY_TYPE_SOURCE,
)
from app.agents.editor_in_chief import _deep_merge_quality_extensions
from app.models.generation_features import GenerationFeaturePolicy


# ---------------------------------------------------------------------------
# 基础模式行为
# ---------------------------------------------------------------------------

class TestCoordinatorOffMode:
    """所有检测器 off：零行为变化. """

    def test_all_off_returns_empty(self):
        coord = AIQualityCoordinatorAgent()
        result = coord.coordinate(CoordinatorInput(source_modes={
            "ai_flavor": "off", "concept_budget": "off",
            "character_voice": "off", "reader_experience": "off",
        }))
        assert result.coordinator_mode == "off"
        assert result.risk_summary == ""
        assert result.quality_guidance_patch == {}
        assert result.revision_hints == []

    def test_empty_source_modes_returns_off(self):
        coord = AIQualityCoordinatorAgent()
        result = coord.coordinate(CoordinatorInput(source_modes={}))
        assert result.coordinator_mode == "off"


class TestCoordinatorShadowMode:
    """shadow 模式：只生成内部摘要. """

    def test_shadow_produces_risk_summary_only(self):
        coord = AIQualityCoordinatorAgent()
        result = coord.coordinate(CoordinatorInput(
            source_modes={"ai_flavor": "shadow"},
            quality_reports={
                "ai_flavor": {"advisories": [
                    {"type": "template_phrase", "severity": "high", "detail": "test"},
                ]},
            },
        ))
        assert result.coordinator_mode == "shadow"
        assert result.risk_summary
        assert result.quality_guidance_patch == {}
        assert result.revision_hints == []


class TestCoordinatorReportMode:
    """report 模式：只展示报告，不影响生成. """

    def test_report_no_guidance_no_revision(self):
        coord = AIQualityCoordinatorAgent()
        result = coord.coordinate(CoordinatorInput(
            source_modes={"ai_flavor": "report"},
            quality_reports={
                "ai_flavor": {"advisories": [
                    {"type": "template_phrase", "severity": "high", "detail": "test"},
                ]},
            },
        ))
        assert result.coordinator_mode == "report"
        assert result.risk_summary
        assert result.quality_guidance_patch == {}
        assert result.revision_hints == []


# ---------------------------------------------------------------------------
# 核心：各模块开关独立生效
# ---------------------------------------------------------------------------

class TestSourceModeIndependence:
    """各模块开关独立生效，report 不会被 assist/enforce 带升级. """

    def test_report_advisory_not_upgraded_by_enforce(self):
        """ai_flavor=report + concept_budget=enforce 时，AI味advisory不应生成guidance/revision. """
        coord = AIQualityCoordinatorAgent()
        result = coord.coordinate(CoordinatorInput(
            source_modes={"ai_flavor": "report", "concept_budget": "enforce"},
            quality_reports={
                "ai_flavor": {"advisories": [
                    {"type": "template_phrase", "severity": "high", "detail": "AI味问题"},
                ]},
                "concept_budget": {"advisories": [
                    {"type": "concept_budget_exceeded", "severity": "high", "detail": "概念超预算"},
                ]},
            },
        ))
        # concept_budget 的 advisory 应生成 guidance
        assert result.quality_guidance_patch  # 有 guidance
        # 但 AI味 的 guidance 不应出现
        guidance = result.quality_guidance_patch.get("anti_ai_guidance", [])
        for g in guidance:
            assert "总结" not in g and "套话" not in g  # AI味 guidance 不应出现
        # concept_budget 的 revision_hint 应出现
        assert result.revision_hints
        # AI味 的 revision_hint 不应出现
        for hint in result.revision_hints:
            assert hint["problem"] != "模板化表达"

    def test_report_advisory_enters_risk_summary(self):
        """report 模式的 advisory 仍应进入 risk_summary. """
        coord = AIQualityCoordinatorAgent()
        result = coord.coordinate(CoordinatorInput(
            source_modes={"ai_flavor": "report"},
            quality_reports={
                "ai_flavor": {"advisories": [
                    {"type": "template_phrase", "severity": "high", "detail": "test"},
                ]},
            },
        ))
        assert "模板化表达" in result.risk_summary

    def test_mixed_modes_guidance_only_from_active(self):
        """混合模式下，只有 assist/enforce 的检测器生成 guidance. """
        coord = AIQualityCoordinatorAgent()
        result = coord.coordinate(CoordinatorInput(
            source_modes={
                "ai_flavor": "report",
                "character_voice": "assist",
                "concept_budget": "off",
            },
            quality_reports={
                "ai_flavor": {"advisories": [
                    {"type": "template_phrase", "severity": "high", "detail": "AI味"},
                ]},
                "character_voice": {"advisories": [
                    {"type": "voice_merge", "severity": "high", "detail": "声纹趋同"},
                ]},
            },
        ))
        # character_voice 的 guidance 应出现
        has_voice_guidance = (
            "character_voice_guidance" in result.quality_guidance_patch
            or any("对白" in g for g in result.quality_guidance_patch.get("anti_ai_guidance", []))
        )
        assert has_voice_guidance
        # AI味 的 guidance 不应出现
        for g in result.quality_guidance_patch.get("anti_ai_guidance", []):
            assert "总结" not in g and "套话" not in g

    def test_mixed_modes_revision_only_from_active(self):
        """混合模式下，只有 assist/enforce 的检测器生成 revision_hint. """
        coord = AIQualityCoordinatorAgent()
        result = coord.coordinate(CoordinatorInput(
            source_modes={
                "ai_flavor": "report",
                "concept_budget": "enforce",
            },
            quality_reports={
                "ai_flavor": {"advisories": [
                    {"type": "template_phrase", "severity": "high", "detail": "AI味"},
                ]},
                "concept_budget": {"advisories": [
                    {"type": "concept_budget_exceeded", "severity": "high", "detail": "超预算"},
                ]},
            },
        ))
        # 只有 concept_budget 的 revision_hint
        assert len(result.revision_hints) == 1
        assert result.revision_hints[0]["problem"] == "概念投放超预算"

    def test_enforce_prefixed_ai_advisory_is_auto_revisable(self):
        """实际 AIFlavorChecker 的 ai_ 前缀类型也应进入 enforce 内联修订. """
        coord = AIQualityCoordinatorAgent()
        result = coord.coordinate(CoordinatorInput(
            source_modes={"ai_flavor": "enforce"},
            quality_reports={
                "ai_flavor": {"advisories": [
                    {"type": "ai_template_phrase", "severity": "medium", "detail": "模板化", "confidence": 0.8},
                ]},
            },
        ))

        assert result.revision_hints
        assert result.revision_hints[0]["problem"] == "模板化表达"
        assert result.revision_hints[0]["auto_revise_allowed"] is True
        assert result.revision_hints[0]["needs_user_confirm"] is False


# ---------------------------------------------------------------------------
# _collect_advisories 标记来源
# ---------------------------------------------------------------------------

class TestCollectAdvisories:
    """_collect_advisories 应标记 source_checker. """

    def test_source_checker_tagged(self):
        coord = AIQualityCoordinatorAgent()
        advisories = coord._collect_advisories({
            "ai_flavor": {"advisories": [
                {"type": "template_phrase", "severity": "high", "detail": "test"},
            ]},
            "concept_budget": {"advisories": [
                {"type": "concept_budget_exceeded", "severity": "medium", "detail": "test2"},
            ]},
        })
        assert len(advisories) == 2
        sources = {adv["source_checker"] for adv in advisories}
        assert sources == {"ai_flavor", "concept_budget"}

    def test_original_advisory_not_mutated(self):
        """_collect_advisories 不应修改原始 advisory dict. """
        coord = AIQualityCoordinatorAgent()
        original = {"type": "template_phrase", "severity": "high", "detail": "test"}
        advisories = coord._collect_advisories({
            "ai_flavor": {"advisories": [original]},
        })
        assert "source_checker" not in original  # 原始 dict 不应被修改
        assert advisories[0]["source_checker"] == "ai_flavor"


# ---------------------------------------------------------------------------
# _effective_mode_for_advisory
# ---------------------------------------------------------------------------

class TestEffectiveModeForAdvisory:
    """_effective_mode_for_advisory 应正确获取来源模式. """

    def test_from_source_checker(self):
        adv = {"type": "template_phrase", "source_checker": "ai_flavor"}
        mode = _effective_mode_for_advisory(adv, {"ai_flavor": "assist"})
        assert mode == "assist"

    def test_fallback_to_type_source(self):
        adv = {"type": "template_phrase"}  # 无 source_checker
        mode = _effective_mode_for_advisory(adv, {"ai_flavor": "enforce"})
        assert mode == "enforce"  # 从 _ADVISORY_TYPE_SOURCE 推断

    def test_unknown_type_returns_off(self):
        adv = {"type": "unknown_type_xyz"}
        mode = _effective_mode_for_advisory(adv, {"ai_flavor": "assist"})
        assert mode == "off"

    def test_ai_prefixed_type_falls_back_to_source(self):
        adv = {"type": "ai_template_phrase"}  # 无 source_checker
        mode = _effective_mode_for_advisory(adv, {"ai_flavor": "enforce"})
        assert mode == "enforce"


# ---------------------------------------------------------------------------
# _any_mode_at_least
# ---------------------------------------------------------------------------

class TestAnyModeAtLeast:
    def test_enforce_detected(self):
        assert _any_mode_at_least({"ai_flavor": "off", "concept_budget": "enforce"}, "enforce")

    def test_below_threshold(self):
        assert not _any_mode_at_least({"ai_flavor": "report"}, "assist")

    def test_assist_meets_report(self):
        assert _any_mode_at_least({"ai_flavor": "assist"}, "report")


# ---------------------------------------------------------------------------
# build_pre_generation_patch（使用完整 GenerationFeaturePolicy）
# ---------------------------------------------------------------------------

class TestBuildPreGenerationPatch:
    def test_explicit_off_returns_empty(self):
        coord = AIQualityCoordinatorAgent()
        patch = coord.build_pre_generation_patch(
            project_id="p1", scene_contract={},
            quality_memory={"recent_quality_patterns": [
                {"type": "summary_ending_overuse", "count": 5, "suggestion": "test"},
            ]},
            feature_policy={"ai_flavor_mode": "off"},
        )
        assert patch == {}

    def test_default_policy_keeps_ai_flavor_in_report_mode(self):
        coord = AIQualityCoordinatorAgent()
        patch = coord.build_pre_generation_patch(
            project_id="p1", scene_contract={},
            quality_memory={"recent_quality_patterns": [
                {"type": "summary_ending_overuse", "count": 5, "suggestion": "test"},
            ]},
            feature_policy=GenerationFeaturePolicy(),
        )
        assert patch == {}

    def test_report_returns_empty(self):
        coord = AIQualityCoordinatorAgent()
        patch = coord.build_pre_generation_patch(
            project_id="p1", scene_contract={},
            quality_memory={"recent_quality_patterns": [
                {"type": "summary_ending_overuse", "count": 5, "suggestion": "test"},
            ]},
            feature_policy=GenerationFeaturePolicy(ai_flavor_mode="report"),
        )
        assert patch == {}

    def test_assist_returns_patch(self):
        coord = AIQualityCoordinatorAgent()
        patch = coord.build_pre_generation_patch(
            project_id="p1", scene_contract={},
            quality_memory={"recent_quality_patterns": [
                {"type": "summary_ending_overuse", "count": 5, "suggestion": "test"},
            ]},
            feature_policy=GenerationFeaturePolicy(ai_flavor_mode="assist"),
        )
        assert "anti_ai_guidance" in patch

    def test_concept_budget_independent(self):
        """只开启 concept_budget=enforce 时，AI味趋势不注入. """
        coord = AIQualityCoordinatorAgent()
        patch = coord.build_pre_generation_patch(
            project_id="p1", scene_contract={},
            quality_memory={"recent_quality_patterns": [
                {"type": "summary_ending_overuse", "count": 5, "suggestion": "test"},
                {"type": "concept_overload_trend", "count": 4, "suggestion": "test"},
            ]},
            feature_policy=GenerationFeaturePolicy(
                ai_flavor_mode="off",
                reader_experience_mode="off",
                character_voice_mode="off",
                writing_mode_profile_mode="off",
                narrative_experience_mode="off",
                literary_quality_mode="off",
                mode_fit_mode="off",
                style_experience_conflict_mode="off",
                commercial_pacing_mode="off",
                concept_budget_mode="enforce",
            ),
        )
        assert "reveal_control" in patch
        assert "anti_ai_guidance" not in patch  # ai_flavor 未开启

    def test_dict_feature_policy(self):
        coord = AIQualityCoordinatorAgent()
        patch = coord.build_pre_generation_patch(
            project_id="p1", scene_contract={},
            quality_memory={"recent_quality_patterns": [
                {"type": "summary_ending_overuse", "count": 5, "suggestion": "test"},
            ]},
            feature_policy={"ai_flavor_mode": "assist"},
        )
        assert "anti_ai_guidance" in patch


# ---------------------------------------------------------------------------
# 深合并 quality_extensions
# ---------------------------------------------------------------------------

class TestDeepMergeQualityExtensions:
    def test_anti_ai_guidance_appended(self):
        result = _deep_merge_quality_extensions(
            {"anti_ai_guidance": ["原有约束"]},
            {"anti_ai_guidance": ["新增约束"]},
        )
        assert "原有约束" in result["anti_ai_guidance"]
        assert "新增约束" in result["anti_ai_guidance"]

    def test_forbidden_future_concepts_union(self):
        result = _deep_merge_quality_extensions(
            {"reveal_control": {"forbidden_future_concepts": ["大师伯身份"]}},
            {"reveal_control": {"forbidden_future_concepts": ["王庭真相"]}},
        )
        assert "大师伯身份" in result["reveal_control"]["forbidden_future_concepts"]
        assert "王庭真相" in result["reveal_control"]["forbidden_future_concepts"]

    def test_new_concept_budget_minimum(self):
        result = _deep_merge_quality_extensions(
            {"reveal_control": {"new_concept_budget": 2}},
            {"reveal_control": {"new_concept_budget": 1}},
        )
        assert result["reveal_control"]["new_concept_budget"] == 1

    def test_new_concept_budget_string_safety(self):
        """字符串类型的 budget 应安全转换. """
        result = _deep_merge_quality_extensions(
            {"reveal_control": {"new_concept_budget": "2"}},
            {"reveal_control": {"new_concept_budget": 1}},
        )
        assert result["reveal_control"]["new_concept_budget"] == 1

    def test_new_concept_budget_invalid_string_preserves_existing(self):
        """无法转换的字符串不应导致异常，保留原合同值. """
        result = _deep_merge_quality_extensions(
            {"reveal_control": {"new_concept_budget": "abc"}},
            {"reveal_control": {"new_concept_budget": 1}},
        )
        assert result["reveal_control"]["new_concept_budget"] == 1  # patch 有效，取 min

    def test_allowed_new_concepts_existing_wins(self):
        result = _deep_merge_quality_extensions(
            {"reveal_control": {"allowed_new_concepts": ["灵契"]}},
            {"reveal_control": {"allowed_new_concepts": ["新概念"]}},
        )
        assert result["reveal_control"]["allowed_new_concepts"] == ["灵契"]

    def test_character_voice_guidance_appended(self):
        result = _deep_merge_quality_extensions(
            {"character_voice_guidance": {"凤溪": ["短促"]}},
            {"character_voice_guidance": {"凤溪": ["谨慎"], "苏云清": ["温和"]}},
        )
        assert "短促" in result["character_voice_guidance"]["凤溪"]
        assert "谨慎" in result["character_voice_guidance"]["凤溪"]
        assert "苏云清" in result["character_voice_guidance"]


# ---------------------------------------------------------------------------
# 质量记忆
# ---------------------------------------------------------------------------

class TestQualityMemory:
    def test_low_severity_not_accumulated(self):
        result = _update_quality_memory({}, [
            {"type": "template_phrase", "severity": "low", "detail": "test", "confidence": 0.9, "source_checker": "ai_flavor"},
        ])
        assert result == []

    def test_report_mode_not_written_to_memory(self):
        """report 模式的检测器不应写入质量记忆. """
        result = _update_quality_memory(
            {},
            [{"type": "template_phrase", "severity": "high", "detail": "test", "confidence": 0.8, "source_checker": "ai_flavor"}],
            source_modes={"ai_flavor": "report"},
        )
        assert result == []

    def test_assist_mode_writes_to_memory(self):
        result = _update_quality_memory(
            {},
            [{"type": "template_phrase", "severity": "high", "detail": "test", "confidence": 0.8, "source_checker": "ai_flavor"}],
            source_modes={"ai_flavor": "assist"},
        )
        assert len(result) == 1

    def test_decay(self):
        existing = {
            "recent_quality_patterns": [
                {"type": "summary_ending_overuse", "count": 8, "suggestion": "test",
                 "last_seen_chapter": 1, "last_seen_scene": 0},
            ],
        }
        result = _update_quality_memory(existing, [], current_chapter=5, current_scene=0, window_size=10,
                                        source_modes={"ai_flavor": "assist"})
        assert result[0]["count"] == 4

    def test_records_last_seen(self):
        result = _update_quality_memory(
            {},
            [{"type": "template_phrase", "severity": "high", "detail": "test", "confidence": 0.8, "source_checker": "ai_flavor"}],
            source_modes={"ai_flavor": "assist"},
            current_chapter=3, current_scene=2,
        )
        assert result[0]["last_seen_chapter"] == 3
        assert result[0]["last_seen_scene"] == 2

    def test_none_source_modes_no_accumulation(self):
        """source_modes=None 时按 off 处理，不应累计记忆. """
        result = _update_quality_memory(
            {},
            [{"type": "template_phrase", "severity": "high", "detail": "test", "confidence": 0.8, "source_checker": "ai_flavor"}],
            source_modes=None,
        )
        assert result == []


# ---------------------------------------------------------------------------
# Risk Summary
# ---------------------------------------------------------------------------

class TestRiskSummary:
    def test_no_advisories(self):
        coord = AIQualityCoordinatorAgent()
        assert "未检测到" in coord._build_risk_summary([])

    def test_high_severity_first(self):
        coord = AIQualityCoordinatorAgent()
        summary = coord._build_risk_summary([
            {"type": "explanatory_narration", "severity": "medium", "detail": "test"},
            {"type": "template_phrase", "severity": "high", "detail": "test"},
        ])
        assert "高风险" in summary
        assert "模板化表达" in summary


# ---------------------------------------------------------------------------
# 安全性
# ---------------------------------------------------------------------------

class TestCoordinatorSafety:
    def test_malformed_reports(self):
        coord = AIQualityCoordinatorAgent()
        result = coord.coordinate(CoordinatorInput(
            source_modes={"ai_flavor": "report"},
            quality_reports={"ai_flavor": "not_a_dict"},
        ))
        assert result.coordinator_mode == "report"

    def test_empty_advisories(self):
        coord = AIQualityCoordinatorAgent()
        result = coord.coordinate(CoordinatorInput(
            source_modes={"ai_flavor": "assist"},
            quality_reports={"ai_flavor": {"advisories": []}},
        ))
        assert result.risk_summary

    def test_empty_memory_no_patch(self):
        coord = AIQualityCoordinatorAgent()
        patch = coord.build_pre_generation_patch(
            project_id="p1", scene_contract={}, quality_memory={},
            feature_policy=GenerationFeaturePolicy(ai_flavor_mode="assist"),
        )
        assert patch == {}


# ---------------------------------------------------------------------------
# _resolve_modes
# ---------------------------------------------------------------------------

class TestResolveModes:
    def test_none_returns_all_off(self):
        modes = AIQualityCoordinatorAgent._resolve_modes(None)
        assert all(m == "off" for m in modes.values())

    def test_policy_object(self):
        policy = GenerationFeaturePolicy(ai_flavor_mode="assist", concept_budget_mode="enforce")
        modes = AIQualityCoordinatorAgent._resolve_modes(policy)
        assert modes["ai_flavor"] == "assist"
        assert modes["concept_budget"] == "enforce"
        assert modes["character_voice"] == "assist"

    def test_dict_policy(self):
        modes = AIQualityCoordinatorAgent._resolve_modes({
            "ai_flavor_mode": "shadow",
            "character_voice_mode": "assist",
        })
        assert modes["ai_flavor"] == "shadow"
        assert modes["character_voice"] == "assist"
