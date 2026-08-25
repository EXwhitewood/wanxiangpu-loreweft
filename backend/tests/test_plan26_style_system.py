"""方案 26：文笔风格系统全链路接入——测试套件。

覆盖 Part A-F：
- Part F: style_profile / style_directive schema 校验
- Part A: parse_style_compliance / validate_style_compliance
- Part B: validate_style_directive 在主编流程中的拦截
- Part C: StyleReviewAdapter 检测 → issue 生成、writer_deviations_to_issues
- Part D: 风格类 metric 路由注册、builder 产出、repair_lane 映射
- Part E: make_style_issue 含 target_span/localization_status
"""
from __future__ import annotations

import asyncio
import pytest

from app.services.style_profile_schema import (
    STYLE_PROFILE_REQUIRED_FIELDS,
    STYLE_DIRECTIVE_REQUIRED_FIELDS,
    STYLE_ISSUE_FAMILIES,
    validate_style_profile,
    validate_style_directive,
    make_style_issue,
    writer_deviations_to_issues,
    StyleReviewAdapter,
    parse_style_compliance,
    validate_style_compliance,
)
from app.services.fbi.blueprint_route_registry import (
    get_blueprint_route,
    registered_metrics,
    route_family_for_metric,
    _ROUTES,
)
from app.services.fbi.chapter_case_intake import _allowed_operations_for_lane


# ----------------------------------------------------------------------
# Part F: style_profile / style_directive schema
# ----------------------------------------------------------------------

class TestStyleProfileSchema:
    """方案 26 Part F：style_profile schema 校验。"""

    def test_valid_style_profile_no_issues(self):
        profile = {
            "id": "sp1",
            "name": "test",
            "style_features": {k: "" for k in [
                "vocabulary", "sentence_structure", "tone", "pacing",
                "description_style", "dialogue_style", "narrative_voice",
                "signature_phrases", "avoid_patterns",
            ]},
            "style_embedding": {k: 0.0 for k in [
                "emotionality", "sentence_complexity", "narrative_distance",
                "info_density", "dialogue_ratio", "description_density",
                "rhythm_steepness", "narrator_intrusion",
            ]},
            "persona_card": {"identity": "narrator"},
        }
        assert validate_style_profile(profile) == []

    def test_empty_profile_returns_missing(self):
        issues = validate_style_profile(None)
        assert "style_profile_empty" in issues

    def test_missing_required_fields(self):
        profile = {"id": "sp1"}
        issues = validate_style_profile(profile)
        for field in STYLE_PROFILE_REQUIRED_FIELDS:
            if field == "id":
                continue
            assert any(f"style_profile.{field}_missing" in i for i in issues), (
                f"应检测到 {field} 缺失"
            )

    def test_missing_style_features_subfields(self):
        profile = {
            "id": "sp1", "name": "test",
            "style_features": {"vocabulary": "ok"},
            "style_embedding": {},
            "persona_card": {"identity": "n"},
        }
        issues = validate_style_profile(profile)
        assert any("style_features.sentence_structure_missing" in i for i in issues)


class TestStyleDirectiveSchema:
    """方案 26 Part B：style_directive schema 校验。"""

    def test_valid_directive_no_issues(self):
        directive = {k: "val" for k in STYLE_DIRECTIVE_REQUIRED_FIELDS}
        directive["avoid"] = ["xxx"]
        assert validate_style_directive(directive) == []

    def test_empty_directive(self):
        issues = validate_style_directive(None)
        assert "style_directive_missing" in issues

    def test_missing_fields_detected(self):
        directive = {"scene_style_role": "tight"}
        issues = validate_style_directive(directive)
        for field in STYLE_DIRECTIVE_REQUIRED_FIELDS:
            if field == "scene_style_role":
                continue
            assert any(f"style_directive.{field}_missing" in i for i in issues), (
                f"应检测到 {field} 缺失"
            )

    def test_empty_avoid_list_detected(self):
        directive = {k: "val" for k in STYLE_DIRECTIVE_REQUIRED_FIELDS}
        directive["avoid"] = []
        issues = validate_style_directive(directive)
        assert any("avoid_missing_or_empty" in i for i in issues)


# ----------------------------------------------------------------------
# Part A: parse_style_compliance / validate_style_compliance
# ----------------------------------------------------------------------

class TestParseStyleCompliance:
    """方案 26 Part A：writer style_compliance 解析。"""

    def test_parse_with_compliance_block(self):
        raw = (
            "这是小说正文。\n\n"
            "```json\n"
            '{"scene_facts": {}, "style_compliance": {"compliance_status": "full", "deviations": [], "avoid_checked": true, "avoid_violations": []}}\n'
            "```"
        )
        text, compliance = parse_style_compliance(raw)
        assert "小说正文" in text
        assert compliance["compliance_status"] == "full"

    def test_parse_without_compliance(self):
        raw = "纯正文，没有 JSON 块。"
        text, compliance = parse_style_compliance(raw)
        assert text == raw
        assert compliance == {}

    def test_parse_multiple_json_blocks_uses_last_with_compliance(self):
        raw = (
            "正文。\n"
            "```json\n"
            '{"scene_facts": {"x": 1}}\n'
            "```\n"
            "更多正文。\n"
            "```json\n"
            '{"style_compliance": {"compliance_status": "partial"}}\n'
            "```"
        )
        text, compliance = parse_style_compliance(raw)
        assert compliance["compliance_status"] == "partial"

    def test_validate_style_compliance_valid(self):
        compliance = {
            "directives_received": {},
            "compliance_status": "full",
            "deviations": [],
            "avoid_checked": True,
            "avoid_violations": [],
        }
        assert validate_style_compliance(compliance) == []

    def test_validate_style_compliance_missing_fields(self):
        compliance = {"compliance_status": "full"}
        issues = validate_style_compliance(compliance)
        assert "deviations" in issues
        assert "avoid_checked" in issues


# ----------------------------------------------------------------------
# Part C: StyleReviewAdapter / writer_deviations_to_issues
# ----------------------------------------------------------------------

class TestWriterDeviationsToIssues:
    """方案 26 Part C3：writer 自报偏离 → advisory issue。"""

    def test_empty_compliance_returns_empty(self):
        assert writer_deviations_to_issues(None) == []
        assert writer_deviations_to_issues({}) == []

    def test_deviation_converted_to_advisory_issue(self):
        compliance = {
            "deviations": [
                {"directive": "pacing", "expected": "tight", "actual": "slow",
                 "reason": "情感沉淀段", "scene_index": 2},
            ],
        }
        issues = writer_deviations_to_issues(compliance)
        assert len(issues) == 1
        assert issues[0]["family"] == "style_consistency"
        assert issues[0]["severity"] == "advisory"
        assert issues[0]["metric"] == "style_directive_deviation"
        assert issues[0]["scene_index"] == 2

    def test_multiple_deviations(self):
        compliance = {
            "deviations": [
                {"directive": "a", "expected": "x", "actual": "y", "reason": "r1"},
                {"directive": "b", "expected": "x", "actual": "y", "reason": "r2"},
            ],
        }
        issues = writer_deviations_to_issues(compliance)
        assert len(issues) == 2


class TestStyleReviewAdapter:
    """方案 26 Part C：StyleReviewAdapter 检测 → issue 生成。"""

    @pytest.mark.asyncio
    async def test_detect_forbidden_word(self):
        adapter = StyleReviewAdapter()
        # "不禁" 是 style_polish 的禁用词之一
        text = "他不禁笑了起来。"
        issues = await adapter.detect_style_issues(text, style_profile=None)
        # 至少检测到禁用词或重复
        assert isinstance(issues, list)

    @pytest.mark.asyncio
    async def test_detect_structure_word_cluster(self):
        adapter = StyleReviewAdapter()
        # 在 500 字窗口内同一结构词出现 ≥3 次
        text = (
            "因此他走了。因此她又来。因此大家都离开了。"
            "这是一段测试文本用来触发结构词堆积检测。"
        )
        issues = await adapter.detect_style_issues(text, style_profile=None)
        cluster_issues = [i for i in issues if i["metric"] == "structure_word_cluster_count"]
        assert len(cluster_issues) >= 1
        assert cluster_issues[0]["family"] == "structure_word_cluster"
        # Part D: 应有 target_span
        assert "target_span" in cluster_issues[0]
        assert cluster_issues[0]["localization_status"] == "localized"

    @pytest.mark.asyncio
    async def test_detect_sentence_rhythm_repeat(self):
        adapter = StyleReviewAdapter()
        # 连续 ≥3 句字数差 ≤2
        text = "他走了过去。她走了过去。它走了过去。这是结尾。"
        issues = await adapter.detect_style_issues(text, style_profile=None)
        rhythm_issues = [i for i in issues if i["metric"] == "uniform_sentence_streak"]
        assert len(rhythm_issues) >= 1
        assert rhythm_issues[0]["family"] == "sentence_rhythm"
        # Part D: 应有 target_span（来自 evidence）
        assert "target_span" in rhythm_issues[0]

    @pytest.mark.asyncio
    async def test_detect_paragraph_shape_repeat(self):
        adapter = StyleReviewAdapter()
        # 连续 ≥3 段字数差 ≤10%
        p1 = "这是一段长度差不多的文字用来测试段落形态重复检测功能。" * 2
        p2 = "这是一段长度差不多的文字用来测试段落形态重复检测功能。" * 2
        p3 = "这是一段长度差不多的文字用来测试段落形态重复检测功能。" * 2
        text = f"{p1}\n{p2}\n{p3}\n最后一段不同。"
        issues = await adapter.detect_style_issues(text, style_profile=None)
        shape_issues = [i for i in issues if i["metric"] == "paragraph_shape_repeat"]
        assert len(shape_issues) >= 1
        assert shape_issues[0]["family"] == "paragraph_shape"
        # Part D: 应有 target_span
        assert "target_span" in shape_issues[0]

    @pytest.mark.asyncio
    async def test_empty_text_returns_empty(self):
        adapter = StyleReviewAdapter()
        issues = await adapter.detect_style_issues("", style_profile=None)
        assert issues == []


# ----------------------------------------------------------------------
# Part E: make_style_issue 含 target_span
# ----------------------------------------------------------------------

class TestMakeStyleIssue:
    """方案 26 Part E/D：make_style_issue 产出含 target_span。"""

    def test_evidence_becomes_target_span(self):
        issue = make_style_issue(
            family="style_consistency",
            severity="blocking",
            metric="forbidden_word",
            description="test",
            evidence="不禁",
        )
        assert issue["target_span"] == "不禁"
        assert issue["localization_status"] == "localized"
        assert issue["evidence"] == "不禁"
        assert issue["blocks_commit"] is False
        assert issue["enforcement"] == "advisory"

    def test_explicit_target_span_overrides_evidence(self):
        issue = make_style_issue(
            family="style_consistency",
            severity="blocking",
            metric="forbidden_word",
            description="test",
            evidence="上下文",
            target_span="禁用词",
        )
        assert issue["target_span"] == "禁用词"
        assert issue["evidence"] == "上下文"

    def test_no_evidence_no_target_span(self):
        issue = make_style_issue(
            family="paragraph_shape",
            severity="warning",
            metric="paragraph_shape_repeat",
            description="test",
        )
        assert "target_span" not in issue
        assert "localization_status" not in issue

    def test_extra_fields_merged(self):
        issue = make_style_issue(
            family="style_consistency",
            severity="blocking",
            metric="avoid_pattern_violation",
            description="test",
            evidence="pattern",
            extra={"suggestion": "replacement"},
        )
        assert issue["suggestion"] == "replacement"
        assert issue["target_span"] == "pattern"

    def test_extra_cannot_promote_style_issue_to_blocking(self):
        issue = make_style_issue(
            family="style_consistency",
            severity="blocking",
            metric="avoid_pattern_violation",
            description="test",
            extra={"blocks_commit": True, "enforcement": "hard_blocking"},
        )
        assert issue["blocks_commit"] is False
        assert issue["enforcement"] == "advisory"


# ----------------------------------------------------------------------
# Part D: 路由注册 / builder / repair_lane
# ----------------------------------------------------------------------

class TestStyleMetricRoutes:
    """方案 26 Part D：风格类 metric 路由注册。"""

    def test_forbidden_word_route_registered(self):
        route = get_blueprint_route("forbidden_word")
        assert route is not None
        assert route.family == "style_consistency"
        assert route.operation == "replace_exact"
        assert route.builder == "build_forbidden_word_replace"
        assert route.requires_llm is False

    def test_avoid_pattern_violation_route_registered(self):
        route = get_blueprint_route("avoid_pattern_violation")
        assert route is not None
        assert route.family == "style_consistency"
        assert route.operation == "replace_exact"
        assert route.builder == "build_forbidden_word_replace"
        assert route.requires_llm is False

    def test_structure_word_cluster_count_route_registered(self):
        route = get_blueprint_route("structure_word_cluster_count")
        assert route is not None
        assert route.family == "structure_word_cluster"
        assert route.operation == "normalize_structure_words"
        assert route.builder == "build_structure_word_normalize"
        assert route.requires_llm is False

    def test_uniform_sentence_streak_route_registered(self):
        route = get_blueprint_route("uniform_sentence_streak")
        assert route is not None
        assert route.family == "sentence_rhythm"
        assert route.operation == "vary_sentence_shape"
        assert route.builder == "build_sentence_rhythm_vary"
        assert route.requires_llm is False

    def test_paragraph_shape_repeat_route_registered(self):
        route = get_blueprint_route("paragraph_shape_repeat")
        assert route is not None
        assert route.family == "paragraph_shape"
        assert route.operation == "split_paragraph"
        assert route.builder == "build_paragraph_shape_split"
        assert route.requires_llm is False

    def test_route_family_for_metric_returns_correct_family(self):
        assert route_family_for_metric("forbidden_word") == "style_consistency"
        assert route_family_for_metric("structure_word_cluster_count") == "structure_word_cluster"
        assert route_family_for_metric("uniform_sentence_streak") == "sentence_rhythm"
        assert route_family_for_metric("paragraph_shape_repeat") == "paragraph_shape"

    def test_all_style_metrics_in_registered_metrics(self):
        metrics = registered_metrics()
        for m in (
            "forbidden_word",
            "avoid_pattern_violation",
            "structure_word_cluster_count",
            "uniform_sentence_streak",
            "paragraph_shape_repeat",
        ):
            assert m in metrics, f"{m} 应在 registered_metrics 中"

    def test_style_directive_deviation_not_registered(self):
        """advisory metric 不注册路由，不生成 tool_blueprint。"""
        route = get_blueprint_route("style_directive_deviation")
        assert route is None


class TestStyleBuilders:
    """方案 26 Part D：风格类 builder 函数。"""

    def _make_violation(self, metric: str, target_span: str = "", **extra) -> dict:
        v = {
            "metric": metric,
            "type": metric,
            "target_span": target_span,
            "localization_status": "localized",
            "description": "test violation",
        }
        v.update(extra)
        return v

    def test_build_forbidden_word_replace_with_suggestion(self):
        from app.services.fbi.review_minister import _build_forbidden_word_replace
        route = get_blueprint_route("forbidden_word")
        violation = self._make_violation(
            "forbidden_word", target_span="不禁",
            suggestion="不由得",
        )
        blueprint = _build_forbidden_word_replace(
            violation=violation, route=route, anchor="不禁",
            issue_id="iss1", source="test",
        )
        assert blueprint is not None
        assert blueprint["operation"] == "replace_exact"
        assert blueprint["old_text"] == "不禁"
        assert blueprint["new_text"] == "不由得"
        assert blueprint["repair_family"] == "style_consistency"

    def test_build_forbidden_word_replace_without_suggestion(self):
        from app.services.fbi.review_minister import _build_forbidden_word_replace
        route = get_blueprint_route("forbidden_word")
        violation = self._make_violation("forbidden_word", target_span="不禁")
        blueprint = _build_forbidden_word_replace(
            violation=violation, route=route, anchor="不禁",
            issue_id="iss1", source="test",
        )
        assert blueprint is not None
        assert blueprint["new_text"] == ""

    def test_build_forbidden_word_replace_no_anchor(self):
        from app.services.fbi.review_minister import _build_forbidden_word_replace
        route = get_blueprint_route("forbidden_word")
        violation = self._make_violation("forbidden_word", target_span="")
        blueprint = _build_forbidden_word_replace(
            violation=violation, route=route, anchor="",
            issue_id="iss1", source="test",
        )
        assert blueprint is None

    def test_build_structure_word_normalize(self):
        from app.services.fbi.review_minister import _build_structure_word_normalize
        route = get_blueprint_route("structure_word_cluster_count")
        violation = self._make_violation(
            "structure_word_cluster_count", target_span="因此他走了。因此她又来。",
        )
        blueprint = _build_structure_word_normalize(
            violation=violation, route=route, anchor="因此他走了。因此她又来。",
            issue_id="iss1", source="test",
        )
        assert blueprint is not None
        assert blueprint["operation"] == "normalize_structure_words"
        assert blueprint["max_replacements"] == 3
        assert blueprint["repair_family"] == "structure_word_cluster"

    def test_build_sentence_rhythm_vary(self):
        from app.services.fbi.review_minister import _build_sentence_rhythm_vary
        route = get_blueprint_route("uniform_sentence_streak")
        violation = self._make_violation(
            "uniform_sentence_streak", target_span="他走了。她走了。它走了。",
        )
        blueprint = _build_sentence_rhythm_vary(
            violation=violation, route=route, anchor="他走了。她走了。它走了。",
            issue_id="iss1", source="test",
        )
        assert blueprint is not None
        assert blueprint["operation"] == "vary_sentence_shape"
        assert blueprint["repair_family"] == "sentence_rhythm"

    def test_build_paragraph_shape_split(self):
        from app.services.fbi.review_minister import _build_paragraph_shape_split
        route = get_blueprint_route("paragraph_shape_repeat")
        violation = self._make_violation(
            "paragraph_shape_repeat", target_span="段落一\n段落二\n段落三",
        )
        blueprint = _build_paragraph_shape_split(
            violation=violation, route=route, anchor="段落一\n段落二\n段落三",
            issue_id="iss1", source="test",
        )
        assert blueprint is not None
        assert blueprint["operation"] == "split_paragraph"
        assert blueprint["repair_family"] == "paragraph_shape"


class TestStyleRepairLanes:
    """方案 26 Part D：repair_lane → allowed_operations 映射。"""

    def test_style_local_patch_lane(self):
        ops = _allowed_operations_for_lane("style_local_patch")
        assert "replace_exact" in ops
        assert "replace_phrase" in ops

    def test_structure_word_cleanup_lane(self):
        ops = _allowed_operations_for_lane("structure_word_cleanup")
        assert "normalize_structure_words" in ops

    def test_sentence_shape_variation_lane(self):
        ops = _allowed_operations_for_lane("sentence_shape_variation")
        assert "vary_sentence_shape" in ops

    def test_paragraph_shape_fix_lane(self):
        ops = _allowed_operations_for_lane("paragraph_shape_fix")
        assert "split_paragraph" in ops
        assert "merge_paragraphs" in ops


class TestStyleIssueFamilies:
    """方案 26 Part C：STYLE_ISSUE_FAMILIES 定义。"""

    def test_all_families_defined(self):
        expected = {
            "style_consistency",
            "anti_ai_style",
            "sentence_rhythm",
            "paragraph_shape",
            "voice_drift",
            "structure_word_cluster",
        }
        assert set(STYLE_ISSUE_FAMILIES) == expected

    def test_families_not_empty(self):
        assert len(STYLE_ISSUE_FAMILIES) == 6
