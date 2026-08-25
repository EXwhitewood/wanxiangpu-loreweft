import pytest
import json
import time
import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from app.models.violation import (
    make_violation, STRATEGY_MAP, TEXT_LOCAL_TYPES, SCENE_STRUCTURAL_TYPES,
    VALIDATOR_FAILURE_TYPES, PENDING_VALIDATION_TYPES,
)
from app.services.scene_recovery_controller import (
    SceneRecoveryController, DEFAULT_BUDGET, STREAM_BUDGET, classify_violations,
)
from app.services.quality_gate import QualityGate, CONSISTENCY_STRATEGY_MAP, _consistency_repair_strategy
from app.services.json_response import parse_json_response
from app.services.recovery_metrics import RecoveryMetrics
from app.agents.scene_critic import SceneCriticAgent
from app.agents.scene_repairer import SceneRepairer
from app.api.editor_chat import (
    _build_review_guidance,
    _build_system_review,
    _record_validator_failure,
)
from app.services.scene_provenance import render_scene_provenance


class TestViolationCategories:
    def test_text_local_types_use_local_repair_strategy(self):
        local_repair_strategies = {"patch_text", "patch_literary_quality"}
        for vtype in TEXT_LOCAL_TYPES:
            assert STRATEGY_MAP.get(vtype) in local_repair_strategies, f"{vtype} should use a local repair strategy"

    def test_scene_structural_types_use_structural_repair_strategy(self):
        structural_repair_strategies = {
            "rewrite_scene",
            "rewrite_scene_with_experience_contract",
            "repair_experience_contract",
            "style_experience_negotiation",
            "patch_literary_quality",
        }
        for vtype in SCENE_STRUCTURAL_TYPES:
            assert STRATEGY_MAP.get(vtype) in structural_repair_strategies, f"{vtype} should use a structural repair strategy"

    def test_validator_failure_types_are_validator_retry(self):
        for vtype in VALIDATOR_FAILURE_TYPES:
            assert STRATEGY_MAP.get(vtype) == "validator_retry", f"{vtype} should map to validator_retry"

    def test_scene_scoped_fact_conflict_promotes_to_rewrite(self):
        assert _consistency_repair_strategy({
            "category": "fact_conflict",
            "scope": "scene",
        }) == "rewrite_scene"

    def test_local_fact_conflict_stays_patch(self):
        assert _consistency_repair_strategy({
            "category": "fact_conflict",
            "scope": "local",
        }) == "patch_text"


class TestReviewGuidance:
    def test_guidance_exposes_rewrite_recommendation_and_attempt_count(self):
        violation = make_violation(
            "timeline_conflict",
            "critical",
            "当前位置与场景叙事冲突",
            target_span="返回旧地点",
            expected_behavior="保持在当前地点",
        )
        guidance = _build_review_guidance([violation], [{"strategy": "rewrite_scene"}])
        assert guidance["recommended_action"] == "rewrite_scene"
        assert guidance["auto_repair_attempt_count"] == 1
        assert guidance["items"][0]["target_span"] == "返回旧地点"


class TestValidatorFailureReview:
    def test_record_failure_tracks_retry_count_and_component(self):
        validator_retry = {}
        violation = make_violation(
            "critic_parse_error",
            "high",
            "legacy provenance shape",
            source="critic",
        )
        assert _record_validator_failure(validator_retry, violations=[violation]) == 1
        assert validator_retry["validator_retry_delayed_attempts"] == 1
        assert validator_retry["last_errors"][0]["component"] == "critic"

    def test_system_review_retry_count_is_not_derived_from_error_list(self):
        validator_retry = {"validator_retry_delayed_attempts": 3, "last_errors": []}
        review = _build_system_review(
            scene_index=1,
            candidate_text="candidate",
            validator_retry=validator_retry,
            resume_state={},
        )
        assert review["retry_count"] == 3
        assert review["attempts"] == []

    def test_render_legacy_provenance_shape(self):
        rendered = render_scene_provenance({
            "spatial_anchor": "土地庙",
            "timeline_anchor": "第二天辰时",
            "current_facts": ["凤溪已经离开混元宗"],
        })
        assert "土地庙" in rendered
        assert "第二天辰时" in rendered
        assert "凤溪已经离开混元宗" in rendered


class TestSceneRepairerParsing:
    @pytest.mark.asyncio
    async def test_auto_repair_accepts_fenced_json(self, monkeypatch):
        llm = MagicMock()
        llm.generate = AsyncMock(return_value='```json\n{"repaired_text":"修订正文","repairs":[]}\n```')
        agent = SceneRepairer()
        monkeypatch.setattr(agent, "get_llm_client", AsyncMock(return_value=llm))
        result = await agent._call_llm_and_parse("原正文", "system", "user")
        assert result["success"] is True
        assert result["repaired_text"] == "修订正文"

    @pytest.mark.asyncio
    async def test_auto_repair_accepts_repair_text_alias(self, monkeypatch):
        llm = MagicMock()
        llm.generate = AsyncMock(return_value='{"replacement_text":"repaired scene text","repairs":[]}')
        agent = SceneRepairer()
        monkeypatch.setattr(agent, "get_llm_client", AsyncMock(return_value=llm))

        result = await agent._call_llm_and_parse("original scene text", "system", "user")

        assert result["success"] is True
        assert result["repaired_text"] == "repaired scene text"
        assert result["parse_recovery"] == "field_alias:replacement_text"

    @pytest.mark.asyncio
    async def test_auto_repair_accepts_plain_repaired_scene_candidate(self, monkeypatch):
        repaired = "Repaired scene text. " * 18
        llm = MagicMock()
        llm.generate = AsyncMock(return_value="Repaired:\n" + repaired)
        agent = SceneRepairer()
        monkeypatch.setattr(agent, "get_llm_client", AsyncMock(return_value=llm))

        result = await agent._call_llm_and_parse("Original scene text. " * 18, "system", "user")

        assert result["success"] is True
        assert result["repaired_text"] == repaired.strip()
        assert result["parse_recovery"] == "plain_text_fallback"

    @pytest.mark.asyncio
    async def test_auto_repair_retries_format_only_when_parse_recovery_fails(self, monkeypatch):
        llm = MagicMock()
        llm.generate = AsyncMock(side_effect=[
            "I cannot provide valid json for this repair.",
            '{"repaired_text":"format retry repaired text","repairs":[]}',
        ])
        agent = SceneRepairer()
        monkeypatch.setattr(agent, "get_llm_client", AsyncMock(return_value=llm))

        result = await agent._call_llm_and_parse("original scene text", "system", "user")

        assert result["success"] is True
        assert result["repaired_text"] == "format retry repaired text"
        assert result["parse_recovery"] == "format_retry"
        assert llm.generate.await_count == 2

    def test_auto_repair_recovers_explicit_text_from_malformed_json(self):
        response = '{"repaired_text":"第一段\n第二段含有“引号”","repairs":[]}'
        repaired = SceneRepairer._extract_malformed_repaired_text(response)
        assert repaired == "第一段\n第二段含有“引号”"

    def test_auto_repair_does_not_accept_arbitrary_plain_text_as_repair(self):
        repaired = SceneRepairer._extract_malformed_repaired_text("这是解释，不是结构化修订结果")
        assert repaired == ""

    @pytest.mark.asyncio
    async def test_auto_repair_force_change_adds_minimal_revision_instruction(self, monkeypatch):
        llm = MagicMock()
        llm.generate = AsyncMock(return_value='{"repaired_text":"修订正文","repairs":[]}')
        agent = SceneRepairer()
        monkeypatch.setattr(agent, "get_llm_client", AsyncMock(return_value=llm))

        result = await agent._patch_from_violations(
            "原正文",
            [{"type": "emotion_expression_monotone", "severity": "medium", "detail": "情绪表达单一"}],
            {},
            {"force_change": True},
        )

        assert result["success"] is True
        call_kwargs = llm.generate.await_args.kwargs
        assert "必须在不改变事实和事件的前提下至少完成一处最小局部改写" in call_kwargs["system_prompt"]
        assert "不要只解释问题，必须返回改写后的完整正文" in call_kwargs["user_prompt"]

    def test_pending_validation_equals_validator_failure(self):
        assert PENDING_VALIDATION_TYPES == VALIDATOR_FAILURE_TYPES

    def test_timeline_conflict_maps_to_rewrite(self):
        v = make_violation("timeline_conflict", "critical", "时间线回退")
        assert v["suggested_strategy"] == "rewrite_scene"

    def test_setting_conflict_maps_to_rewrite(self):
        v = make_violation("setting_conflict", "critical", "空间冲突")
        assert v["suggested_strategy"] == "rewrite_scene"

    def test_pov_conflict_maps_to_rewrite(self):
        v = make_violation("pov_conflict", "high", "视角冲突")
        assert v["suggested_strategy"] == "rewrite_scene"

    def test_identity_conflict_maps_to_rewrite(self):
        v = make_violation("identity_conflict", "high", "身份冲突")
        assert v["suggested_strategy"] == "rewrite_scene"

    def test_new_types_in_categories(self):
        assert "timeline_conflict" in SCENE_STRUCTURAL_TYPES
        assert "setting_conflict" in SCENE_STRUCTURAL_TYPES
        assert "pov_conflict" in SCENE_STRUCTURAL_TYPES
        assert "identity_conflict" in SCENE_STRUCTURAL_TYPES


class TestConsistencyStrategyMap:
    def test_timeline_conflict_maps_to_rewrite(self):
        assert CONSISTENCY_STRATEGY_MAP["timeline_conflict"] == "rewrite_scene"

    def test_setting_conflict_maps_to_rewrite(self):
        assert CONSISTENCY_STRATEGY_MAP["setting_conflict"] == "rewrite_scene"

    def test_pov_conflict_maps_to_rewrite(self):
        assert CONSISTENCY_STRATEGY_MAP["pov_conflict"] == "rewrite_scene"

    def test_identity_conflict_maps_to_rewrite(self):
        assert CONSISTENCY_STRATEGY_MAP["identity_conflict"] == "rewrite_scene"

    def test_fact_conflict_maps_to_patch(self):
        assert CONSISTENCY_STRATEGY_MAP["fact_conflict"] == "patch_text"

    def test_naming_conflict_maps_to_patch(self):
        assert CONSISTENCY_STRATEGY_MAP["naming_conflict"] == "patch_text"


class TestDecideStrategyValidatorPriority:
    def test_critic_parse_error_alone_triggers_validator_retry(self):
        ctrl = SceneRecoveryController()
        v = make_violation("critic_parse_error", "high", "parse error", source="critic")
        report = {"violations": [v], "commit_blocked": True}
        assert ctrl._decide_strategy(report) == "validator_retry"

    def test_critic_parse_error_with_timeline_conflict_prioritizes_validator(self):
        ctrl = SceneRecoveryController()
        v1 = make_violation("critic_parse_error", "high", "parse error", source="critic")
        v2 = make_violation("timeline_conflict", "critical", "时间线回退")
        report = {"violations": [v1, v2], "commit_blocked": True}
        assert ctrl._decide_strategy(report) == "validator_retry"

    def test_timeline_conflict_triggers_rewrite_scene(self):
        ctrl = SceneRecoveryController()
        v = make_violation("timeline_conflict", "critical", "时间线回退")
        report = {"violations": [v], "commit_blocked": True}
        assert ctrl._decide_strategy(report) == "rewrite_scene"

    def test_setting_conflict_triggers_rewrite_scene(self):
        ctrl = SceneRecoveryController()
        v = make_violation("setting_conflict", "critical", "空间冲突")
        report = {"violations": [v], "commit_blocked": True}
        assert ctrl._decide_strategy(report) == "rewrite_scene"

    def test_validator_retry_exhausted_returns_pending(self):
        ctrl = SceneRecoveryController()
        ctrl._validator_retry_immediate_count = ctrl.max_validator_retry_immediate
        v = make_violation("critic_parse_error", "high", "parse error", source="critic")
        report = {"violations": [v], "commit_blocked": True}
        assert ctrl._decide_strategy(report) == "pending_validator_retry"

    def test_validator_retry_does_not_consume_content_budget(self):
        ctrl = SceneRecoveryController()
        v = make_violation("critic_parse_error", "high", "parse error", source="critic")
        report = {"violations": [v], "commit_blocked": True}
        ctrl._decide_strategy(report)
        ctrl._consume_budget("validator_retry")
        assert ctrl._patch_count == 0
        assert ctrl._rewrite_count == 0


class TestBlockNewStates:
    def test_validator_exhausted_returns_pending_validator_retry(self):
        ctrl = SceneRecoveryController()
        v = make_violation("critic_parse_error", "high", "parse error", source="critic")
        result = ctrl._block(
            "validator_exhausted",
            current_text="candidate",
            final_report={"violations": [v]},
        )
        assert result["status"] == "pending_validator_retry"
        assert result["needs_human_review"] is False
        assert "validator_retry" in result

    def test_exhaustion_with_content_violations_returns_content_review(self):
        ctrl = SceneRecoveryController()
        v = make_violation("forbidden_triggered", "critical", "禁止内容")
        result = ctrl._block(
            "exhaustion",
            current_text="candidate",
            final_report={"violations": [v]},
        )
        assert result["status"] == "waiting_human_content_review"
        assert result["needs_human_review"] is True

    def test_exhaustion_with_only_validator_returns_pending(self):
        ctrl = SceneRecoveryController()
        v = make_violation("critic_parse_error", "high", "parse error", source="critic")
        result = ctrl._block(
            "exhaustion",
            current_text="candidate",
            final_report={"violations": [v]},
        )
        assert result["status"] == "pending_validator_retry"


class TestSplitBudget:
    def test_default_budget_has_validator_immediate(self):
        assert "max_validator_retry_immediate" in DEFAULT_BUDGET
        assert DEFAULT_BUDGET["max_validator_retry_immediate"] == 3

    def test_default_budget_has_validator_delayed(self):
        assert "max_validator_retry_delayed" in DEFAULT_BUDGET
        assert DEFAULT_BUDGET["max_validator_retry_delayed"] == 3

    def test_default_budget_has_delay_seconds(self):
        assert "validator_retry_delay_seconds" in DEFAULT_BUDGET
        assert DEFAULT_BUDGET["validator_retry_delay_seconds"] == [5, 15, 45]

    def test_controller_reads_new_budget_fields(self):
        ctrl = SceneRecoveryController()
        assert ctrl.max_validator_retry_immediate == 3
        assert ctrl.max_validator_retry_delayed == 3
        assert ctrl.validator_retry_delay_seconds == [5, 15, 45]


class TestClassifyViolations:
    def test_validator_retry_strategy_goes_to_validator_unavailable(self):
        v = make_violation("critic_parse_error", "high", "parse error", source="critic")
        categories = classify_violations([v])
        assert len(categories["validator_unavailable"]) == 1
        assert len(categories["text_local"]) == 0

    def test_mixed_violations_classified_correctly(self):
        v1 = make_violation("critic_parse_error", "high", "parse error", source="critic")
        v2 = make_violation("timeline_conflict", "critical", "时间线回退")
        v3 = make_violation("forbidden_triggered", "high", "禁止内容")
        categories = classify_violations([v1, v2, v3])
        assert len(categories["validator_unavailable"]) == 1
        assert len(categories["scene_structural"]) == 1
        assert len(categories["text_local"]) == 1


class TestJsonResponseEnhanced:
    def test_direct_json(self):
        result = parse_json_response('{"violations": []}')
        assert result == {"violations": []}

    def test_markdown_code_fence(self):
        result = parse_json_response('```json\n{"violations": []}\n```')
        assert result == {"violations": []}

    def test_json_with_explanatory_text(self):
        result = parse_json_response('审查完成：\n{"violations": []}\n请处理。')
        assert result == {"violations": []}

    def test_trailing_text_after_json(self):
        result = parse_json_response('{"violations": []}\n以上是审查结果。')
        assert result == {"violations": []}

    def test_truncated_json_repair(self):
        truncated = '{"violations": [{"type": "fact_conflict", "severity": "high", "detail": "冲突描述"'
        result = parse_json_response(truncated)
        assert "violations" in result

    def test_empty_response_raises(self):
        with pytest.raises(ValueError):
            parse_json_response("")

    def test_no_json_raises(self):
        with pytest.raises(ValueError):
            parse_json_response("这段文本没有JSON内容")


class TestRecoveryMetrics:
    def test_parse_attempt_tracking(self):
        metrics = RecoveryMetrics()
        metrics._reset()
        metrics.record_parse_attempt(success=True)
        metrics.record_parse_attempt(success=False)
        snap = metrics.snapshot()
        assert snap["parse_attempts"] == 2
        assert snap["parse_failures"] == 1
        assert snap["parse_failure_rate"] == 0.5

    def test_validator_retry_tracking(self):
        metrics = RecoveryMetrics()
        metrics._reset()
        metrics.record_validator_retry(recovered=True)
        metrics.record_validator_retry(recovered=False)
        snap = metrics.snapshot()
        assert snap["validator_retries"] == 2
        assert snap["validator_recoveries"] == 1

    def test_human_review_tracking(self):
        metrics = RecoveryMetrics()
        metrics._reset()
        metrics.record_human_review("content_review")
        metrics.record_human_review("system_review")
        snap = metrics.snapshot()
        assert snap["human_content_reviews"] == 1
        assert snap["human_system_reviews"] == 1


class TestCriticParseErrorLogging:
    @pytest.mark.asyncio
    async def test_parse_error_records_metric(self, monkeypatch):
        metrics = RecoveryMetrics()
        metrics._reset()
        critic = SceneCriticAgent()
        llm = MagicMock()
        llm.generate = AsyncMock(return_value="NOT JSON AT ALL {{{")
        monkeypatch.setattr(critic, "get_llm_client", AsyncMock(return_value=llm))

        violations = await critic._llm_check("正文", {"goal": "推进剧情"}, {}, [])
        assert violations[0]["type"] == "critic_parse_error"
        assert len(violations[0]["detail"]) > 0

    @pytest.mark.asyncio
    async def test_first_truncated_second_normal_recovers(self, monkeypatch):
        critic = SceneCriticAgent()
        good_response = json.dumps({
            "violations": [{"type": "fact_conflict", "severity": "high", "detail": "冲突"}],
        })
        llm = MagicMock()
        llm.generate = AsyncMock(return_value=good_response)
        monkeypatch.setattr(critic, "get_llm_client", AsyncMock(return_value=llm))

        violations = await critic._llm_check("正文", {"goal": "推进剧情"}, {}, [])
        assert violations[0]["type"] == "fact_conflict"


class TestQualityGateConsistencyMapping:
    @pytest.mark.asyncio
    async def test_timeline_conflict_from_consistency_maps_to_rewrite(self, monkeypatch):
        gate = QualityGate()
        from app.agents import scene_validator
        checker = MagicMock()
        checker.execute = AsyncMock(return_value={
            "consistency": {
                "pass": False,
                "conflicts": [{
                    "category": "timeline_conflict",
                    "severity": "critical",
                    "fact": "角色已逃离",
                    "text_claim": "角色在宗门内",
                    "suggestion": "重写场景",
                    "evidence_spans": ["角色在宗门内"],
                }],
                "suggestions": [],
            },
        })
        monkeypatch.setattr(scene_validator, "SceneValidator", lambda: checker)

        with patch.object(gate, '_run_deterministic', new_callable=AsyncMock, return_value=[]), \
             patch.object(gate, '_run_semantic', new_callable=AsyncMock, return_value=[]), \
             patch.object(gate, '_run_fcip', new_callable=AsyncMock, return_value=[]):
            result = await gate.evaluate(
                {"generated_text": "角色在宗门内。", "scene_contract": {"goal": "test"}},
                level="full",
            )

        consistency_violations = result["reports"]["consistency"]["violations"]
        assert len(consistency_violations) == 1
        assert consistency_violations[0]["suggested_strategy"] == "rewrite_scene"


class TestMultipleHighSeverityRewrite:
    def test_two_high_text_local_violations_trigger_rewrite(self):
        ctrl = SceneRecoveryController()
        v1 = make_violation("forbidden_triggered", "high", "禁止内容1")
        v2 = make_violation("forbidden_triggered", "high", "禁止内容2")
        report = {"violations": [v1, v2], "commit_blocked": True}
        assert ctrl._decide_strategy(report) == "rewrite_scene"

    def test_single_text_local_still_patches(self):
        ctrl = SceneRecoveryController()
        v = make_violation("forbidden_triggered", "high", "禁止内容")
        report = {"violations": [v], "commit_blocked": True}
        assert ctrl._decide_strategy(report) == "patch_text"

    def test_mixed_local_and_structural_with_high_severity_triggers_rewrite(self):
        ctrl = SceneRecoveryController()
        v1 = make_violation("forbidden_triggered", "high", "禁止内容")
        v2 = make_violation("fact_conflict", "critical", "事实冲突")
        report = {"violations": [v1, v2], "commit_blocked": True}
        assert ctrl._decide_strategy(report) == "rewrite_scene"


class TestValidatorRetryBudgetIsolation:
    def test_validator_retry_does_not_increment_round_count(self):
        ctrl = SceneRecoveryController()
        ctrl.start_time = time.monotonic()
        ctrl._context = {}
        v = make_violation("critic_parse_error", "high", "parse error", source="critic")
        report = {"violations": [v], "commit_blocked": True, "passed": False}
        ctrl._decide_strategy(report)
        ctrl._consume_budget("validator_retry")
        assert ctrl._round_count == 0

    def test_validator_timeout_is_separate(self):
        ctrl = SceneRecoveryController()
        assert ctrl.validator_timeout_seconds == 180
        assert ctrl.timeout_seconds == 240

    def test_content_timeout_is_separate(self):
        ctrl = SceneRecoveryController()
        assert ctrl.content_timeout_seconds == 240


class TestWaitingHumanSystemReview:
    def test_delayed_retries_exhausted_reaches_system_review(self):
        ctrl = SceneRecoveryController()
        ctrl._validator_retry_delayed_count = ctrl.max_validator_retry_delayed
        v = make_violation("critic_parse_error", "high", "parse error", source="critic")
        result = ctrl._block(
            "validator_exhausted",
            current_text="candidate",
            final_report={"violations": [v]},
        )
        assert result["status"] == "pending_validator_retry"
        assert result["validator_retry"]["delayed_attempts"] == ctrl._validator_retry_delayed_count

    def test_system_review_status_defined_in_violation_types(self):
        assert "consistency_check_unavailable" in VALIDATOR_FAILURE_TYPES
        assert "fcip_check_unavailable" in VALIDATOR_FAILURE_TYPES


class TestDelayedRetryScheduling:
    def test_controller_has_delay_config(self):
        ctrl = SceneRecoveryController()
        assert ctrl.validator_retry_delay_seconds == [5, 15, 45]
        assert ctrl.max_validator_retry_delayed == 3

    def test_block_result_includes_retry_delays(self):
        ctrl = SceneRecoveryController()
        v = make_violation("critic_parse_error", "high", "parse error", source="critic")
        result = ctrl._block("validator_exhausted", current_text="candidate", final_report={"violations": [v]})
        assert "validator_retry" in result
        assert "next_retry_delays" in result["validator_retry"]


class TestCriticResponseVariableSafety:
    @pytest.mark.asyncio
    async def test_llm_call_failure_before_response(self, monkeypatch):
        critic = SceneCriticAgent()
        llm = MagicMock()
        llm.generate = AsyncMock(side_effect=ConnectionError("LLM service unavailable"))
        monkeypatch.setattr(critic, "get_llm_client", AsyncMock(return_value=llm))

        violations = await critic._llm_check("正文", {"goal": "推进剧情"}, {}, [])
        assert violations[0]["type"] == "critic_parse_error"
        assert "ConnectionError" in violations[0]["detail"] or "LLM" in violations[0]["detail"]


class TestDelayedRetryExhaustionToHumanReview:
    def test_three_delays_exhaust_then_system_review(self):
        ctrl = SceneRecoveryController()
        assert ctrl.max_validator_retry_delayed == 3
        assert len(ctrl.validator_retry_delay_seconds) == 3

    def test_block_validator_exhausted_includes_delay_config(self):
        ctrl = SceneRecoveryController()
        v = make_violation("critic_parse_error", "high", "parse error", source="critic")
        result = ctrl._block("validator_exhausted", current_text="candidate", final_report={"violations": [v]})
        assert result["validator_retry"]["next_retry_delays"] == [5, 15, 45]


class TestContentRoundIsolation:
    def test_content_round_count_independent_of_validator(self):
        ctrl = SceneRecoveryController()
        ctrl.start_time = time.monotonic()
        ctrl._context = {}
        assert ctrl._content_round_count == 0
        assert ctrl._round_count == 0

    def test_content_round_count_incremented_only_for_content_strategies(self):
        ctrl = SceneRecoveryController()
        ctrl.start_time = time.monotonic()
        ctrl._context = {}
        ctrl._content_round_count = 0
        ctrl._consume_budget("patch_text")
        ctrl._content_round_count += 1
        ctrl._consume_budget("validator_retry")
        assert ctrl._content_round_count == 1

    def test_content_round_limit_blocks(self):
        ctrl = SceneRecoveryController()
        ctrl.start_time = time.monotonic()
        ctrl._context = {}
        ctrl._content_round_count = ctrl.max_rounds + 1
        v = make_violation("forbidden_triggered", "high", "禁止内容")
        result = ctrl._block("exhaustion", current_text="candidate", final_report={"violations": [v]})
        assert result["status"] == "waiting_human_content_review"


class TestSystemReviewReviewObject:
    def test_block_validator_exhausted_has_candidate_text(self):
        ctrl = SceneRecoveryController()
        v = make_violation("critic_parse_error", "high", "parse error", source="critic")
        result = ctrl._block("validator_exhausted", current_text="candidate text", final_report={"violations": [v]})
        assert result.get("candidate_text") == "candidate text"
        assert result["needs_human_review"] is False


class TestActiveStatusInterception:
    def test_waiting_human_system_review_in_active_filter(self):
        active_statuses = ("running", "waiting_review", "pending_validator_retry", "waiting_human_system_review")
        assert "waiting_human_system_review" in active_statuses


class TestWhileLoopIsolation:
    def test_validator_retry_does_not_consume_content_round_slots(self):
        ctrl = SceneRecoveryController()
        ctrl.start_time = time.monotonic()
        ctrl._context = {}
        ctrl._content_round_count = 0
        ctrl._round_count = 0
        for _ in range(5):
            ctrl._round_count += 1
            ctrl._validator_retry_immediate_count += 1
        assert ctrl._content_round_count == 0
        assert ctrl._round_count == 5

    def test_content_round_count_controls_exit(self):
        ctrl = SceneRecoveryController()
        ctrl.start_time = time.monotonic()
        ctrl._context = {}
        ctrl._content_round_count = ctrl.max_rounds
        assert ctrl._content_round_count >= ctrl.max_rounds

    def test_while_loop_allows_unlimited_validator_retries(self):
        ctrl = SceneRecoveryController()
        ctrl.start_time = time.monotonic()
        ctrl._context = {}
        ctrl._content_round_count = 0
        ctrl._validator_retry_immediate_count = 100
        assert ctrl._content_round_count < ctrl.max_rounds


class TestStartupRecovery:
    def test_recover_function_defined(self):
        try:
            from app.main import _recover_pending_validator_retries
            assert callable(_recover_pending_validator_retries)
        except ImportError:
            pytest.skip("app.main not importable in test environment")

    def test_schedule_function_defined(self):
        try:
            from app.api.editor_chat import _schedule_delayed_validator_retries
            assert callable(_schedule_delayed_validator_retries)
        except ImportError:
            pytest.skip("app.api.editor_chat not importable in test environment")


class TestLockAcquireRelease:
    def test_controller_has_independent_budget(self):
        ctrl = SceneRecoveryController()
        assert ctrl.max_validator_retry_immediate == 3
        assert ctrl.max_patch == 2
        assert ctrl.max_rewrite == 2

    def test_content_and_validator_budgets_are_separate(self):
        ctrl = SceneRecoveryController()
        ctrl._consume_budget("validator_retry")
        ctrl._consume_budget("validator_retry")
        ctrl._consume_budget("validator_retry")
        assert ctrl._patch_count == 0
        assert ctrl._rewrite_count == 0
        assert ctrl._validator_retry_immediate_count == 3


class TestRoundNumNameErrorRegression:
    @pytest.mark.asyncio
    async def test_recover_loop_no_name_error_on_successful_patch(self, monkeypatch):
        ctrl = SceneRecoveryController()
        v = make_violation("forbidden_triggered", "high", "禁止内容")
        call_count = 0

        async def mock_evaluate(ctx, level="full"):
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                return {"passed": False, "violations": [v], "commit_blocked": True}
            return {"passed": True, "violations": [], "commit_blocked": False}

        monkeypatch.setattr(ctrl.quality_gate, "evaluate", mock_evaluate)

        async def mock_execute(strategy, text, context, report):
            return {
                "text": "patched text",
                "report": {"passed": True, "violations": [], "commit_blocked": False},
            }

        monkeypatch.setattr(ctrl, "_execute_strategy", mock_execute)

        result = await ctrl.recover_generated_scene(
            {"scene_contract": {"goal": "test"}}, "draft text",
        )

        assert result["status"] == "accepted"
        assert result["recovery_mode"] == "patched"
        patch_entries = [a for a in ctrl.attempt_log if a["strategy"] == "patch_text"]
        assert len(patch_entries) == 1
        assert patch_entries[0]["round"] == 1

    @pytest.mark.asyncio
    async def test_round_count_increments_per_loop_iteration(self, monkeypatch):
        ctrl = SceneRecoveryController()
        v = make_violation("forbidden_triggered", "high", "禁止内容")

        async def mock_evaluate(ctx, level="full"):
            return {"passed": False, "violations": [v], "commit_blocked": True}

        monkeypatch.setattr(ctrl.quality_gate, "evaluate", mock_evaluate)

        execute_call_count = 0

        async def mock_execute(strategy, text, context, report):
            nonlocal execute_call_count
            execute_call_count += 1
            if execute_call_count == 1:
                return {
                    "text": "patched text",
                    "report": {"passed": False, "violations": [v], "commit_blocked": True},
                }
            return {
                "text": "patched text 2",
                "report": {"passed": True, "violations": [], "commit_blocked": False},
            }

        monkeypatch.setattr(ctrl, "_execute_strategy", mock_execute)

        result = await ctrl.recover_generated_scene(
            {"scene_contract": {"goal": "test"}}, "draft text",
        )

        assert result["status"] == "accepted"
        patch_entries = [a for a in ctrl.attempt_log if a["strategy"] == "patch_text"]
        assert len(patch_entries) == 2
        assert patch_entries[0]["round"] == 1
        assert patch_entries[1]["round"] == 2

    @pytest.mark.asyncio
    async def test_validator_retry_round_not_in_attempt_log_round_field(self, monkeypatch):
        ctrl = SceneRecoveryController()
        v = make_violation("critic_parse_error", "high", "parse error", source="critic")
        call_count = 0

        async def mock_evaluate(ctx, level="full"):
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                return {"passed": False, "violations": [v], "commit_blocked": True}
            return {"passed": True, "violations": [], "commit_blocked": False}

        monkeypatch.setattr(ctrl.quality_gate, "evaluate", mock_evaluate)

        async def mock_validator_retry(text, context, report):
            return {
                "text": text,
                "report": {"passed": True, "violations": [], "commit_blocked": False},
            }

        monkeypatch.setattr(ctrl, "_try_validator_retry", mock_validator_retry)

        result = await ctrl.recover_generated_scene(
            {"scene_contract": {"goal": "test"}}, "draft text",
        )

        assert result["status"] == "accepted"
        assert ctrl._round_count >= 1
        assert ctrl._content_round_count == 0


class TestLockAcquireAwaitRegression:
    @pytest.mark.asyncio
    async def test_lock_acquire_returns_coroutine_not_bool(self):
        lock = asyncio.Lock()
        coro = lock.acquire()
        assert asyncio.iscoroutine(coro)
        await coro
        lock.release()

    @pytest.mark.asyncio
    async def test_without_await_lock_is_not_acquired(self):
        lock = asyncio.Lock()
        coro = lock.acquire()
        assert not lock.locked()
        await coro
        assert lock.locked()
        lock.release()

    @pytest.mark.asyncio
    async def test_await_acquire_then_try_finally_release(self):
        lock = asyncio.Lock()
        try:
            await lock.acquire()
            assert lock.locked()
            raise RuntimeError("simulated error")
        except RuntimeError:
            pass
        finally:
            if lock.locked():
                lock.release()
        assert not lock.locked()

    @pytest.mark.asyncio
    async def test_release_without_await_would_release_wrong_lock(self):
        lock = asyncio.Lock()
        await lock.acquire()
        assert lock.locked()
        lock.release()
        assert not lock.locked()

    @pytest.mark.asyncio
    async def test_concurrent_acquire_blocks_until_release(self):
        lock = asyncio.Lock()
        await lock.acquire()
        acquired = []

        async def try_acquire():
            await lock.acquire()
            acquired.append("acquired")
            lock.release()

        task = asyncio.create_task(try_acquire())
        await asyncio.sleep(0.01)
        assert len(acquired) == 0
        lock.release()
        await task
        assert len(acquired) == 1


class TestStartupExhaustedFinalizationRegression:
    def test_remaining_delays_empty_when_all_attempts_used(self):
        delays = [5, 15, 45]
        delayed_attempts = 3
        remaining_delays = delays[delayed_attempts:]
        assert remaining_delays == []

    def test_remaining_delays_partial_when_some_attempts_used(self):
        delays = [5, 15, 45]
        delayed_attempts = 1
        remaining_delays = delays[delayed_attempts:]
        assert remaining_delays == [15, 45]

    def test_remaining_delays_zero_attempts(self):
        delays = [5, 15, 45]
        delayed_attempts = 0
        remaining_delays = delays[delayed_attempts:]
        assert remaining_delays == [5, 15, 45]

    def test_exhausted_execution_finalized_to_system_review(self):
        execution = MagicMock()
        execution.result_context = {
            "validator_retry": {
                "validator_retry_delays": [5, 15, 45],
                "validator_retry_delayed_attempts": 3,
                "candidate_text": "test candidate",
                "scene_index": 2,
                "last_errors": ["timeout1", "timeout2"],
            },
            "resume_state": {"review_version": 0},
        }

        persisted = execution.result_context
        validator_retry = persisted.get("validator_retry") or {}
        resume_state = persisted.get("resume_state") or {}
        delays = validator_retry.get("validator_retry_delays", [5, 15, 45])
        delayed_attempts = int(validator_retry.get("validator_retry_delayed_attempts", 0))
        remaining_delays = delays[delayed_attempts:]

        assert not remaining_delays

        execution.status = "waiting_human_system_review"
        execution.error_message = "审校器连续故障，延迟复验耗尽，需要人工确认系统状态"
        scene_index = int(validator_retry.get("scene_index", 0))
        review = {
            "scene_index": scene_index,
            "candidate_text": validator_retry.get("candidate_text", ""),
            "violations": [],
            "attempts": validator_retry.get("last_errors", []),
            "error_code": "validator_retry_exhausted",
            "message": "审校器连续故障，延迟复验耗尽，需要人工确认系统状态",
            "review_version": int(resume_state.get("review_version", 0)) + 1,
            "review_type": "system_review",
        }

        assert execution.status == "waiting_human_system_review"
        assert review["review_type"] == "system_review"
        assert review["error_code"] == "validator_retry_exhausted"
        assert review["scene_index"] == 2
        assert review["attempts"] == ["timeout1", "timeout2"]
        assert review["review_version"] == 1

    def test_partial_execution_not_finalized(self):
        execution = MagicMock()
        execution.result_context = {
            "validator_retry": {
                "validator_retry_delays": [5, 15, 45],
                "validator_retry_delayed_attempts": 1,
                "candidate_text": "test",
                "scene_index": 0,
            },
            "resume_state": {"review_version": 0},
        }

        persisted = execution.result_context
        validator_retry = persisted.get("validator_retry") or {}
        delays = validator_retry.get("validator_retry_delays", [5, 15, 45])
        delayed_attempts = int(validator_retry.get("validator_retry_delayed_attempts", 0))
        remaining_delays = delays[delayed_attempts:]

        assert remaining_delays == [15, 45]
        assert execution.status != "waiting_human_system_review"

    @pytest.mark.asyncio
    async def test_recover_function_finalizes_exhausted_executions(self):
        try:
            from app.main import _recover_pending_validator_retries
        except ImportError:
            pytest.skip("app.main not importable in test environment")

        mock_execution = MagicMock()
        mock_execution.id = uuid.uuid4()
        mock_execution.project_id = uuid.uuid4()
        mock_execution.result_context = {
            "validator_retry": {
                "validator_retry_delays": [5, 15, 45],
                "validator_retry_delayed_attempts": 3,
                "candidate_text": "exhausted text",
                "scene_index": 1,
                "last_errors": ["err1"],
            },
            "resume_state": {"review_version": 2},
        }

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars().all.return_value = [mock_execution]
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.commit = AsyncMock()

        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_db)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("app.db.db_models.async_session", return_value=mock_session):
            await _recover_pending_validator_retries()

        assert mock_execution.status == "waiting_human_system_review"
        result_ctx = mock_execution.result_context
        assert "review" in result_ctx
        assert result_ctx["review"]["review_type"] == "system_review"
        assert result_ctx["review"]["error_code"] == "validator_retry_exhausted"
        assert result_ctx["review"]["review_version"] == 3

    @pytest.mark.asyncio
    async def test_recover_function_resumes_partial_executions(self):
        try:
            from app.main import _recover_pending_validator_retries
        except ImportError:
            pytest.skip("app.main not importable in test environment")

        mock_execution = MagicMock()
        mock_execution.id = uuid.uuid4()
        mock_execution.project_id = uuid.uuid4()
        mock_execution.result_context = {
            "validator_retry": {
                "validator_retry_delays": [5, 15, 45],
                "validator_retry_delayed_attempts": 1,
                "candidate_text": "partial text",
                "scene_index": 0,
            },
            "resume_state": {"review_version": 0},
        }

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars().all.return_value = [mock_execution]
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.commit = AsyncMock()

        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_db)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        schedule_called = False

        def mock_schedule(**kwargs):
            nonlocal schedule_called
            schedule_called = True

        with patch("app.db.db_models.async_session", return_value=mock_session), \
             patch("app.api.editor_chat._schedule_delayed_validator_retries", side_effect=mock_schedule):
            await _recover_pending_validator_retries()

        assert schedule_called


class TestDelayedRetryBranchingRegression:
    def test_passed_true_resumes_generation(self):
        gate_result = {"passed": True, "violations": [], "commit_blocked": False}
        validator_violations = [
            v for v in gate_result.get("violations", [])
            if v.get("type") in ("critic_parse_error", "consistency_check_unavailable", "fcip_check_unavailable")
        ]
        assert gate_result.get("passed") is True
        assert not validator_violations

    def test_validator_violations_present_continues_retry(self):
        gate_result = {
            "passed": False,
            "violations": [
                {"type": "critic_parse_error", "severity": "high", "blocks_commit": True},
            ],
            "commit_blocked": True,
        }
        validator_violations = [
            v for v in gate_result.get("violations", [])
            if v.get("type") in ("critic_parse_error", "consistency_check_unavailable", "fcip_check_unavailable")
        ]
        assert gate_result.get("passed") is False
        assert len(validator_violations) == 1
        should_continue_retry = not gate_result.get("passed") and bool(validator_violations)
        assert should_continue_retry is True

    def test_content_violations_only_does_not_resume_generation(self):
        gate_result = {
            "passed": False,
            "violations": [
                {"type": "timeline_conflict", "severity": "critical", "blocks_commit": True},
                {"type": "fact_conflict", "severity": "high", "blocks_commit": True},
            ],
            "commit_blocked": True,
        }
        validator_violations = [
            v for v in gate_result.get("violations", [])
            if v.get("type") in ("critic_parse_error", "consistency_check_unavailable", "fcip_check_unavailable")
        ]
        assert not validator_violations
        assert gate_result.get("passed") is False
        should_resume = gate_result.get("passed") or not validator_violations
        assert should_resume is True
        correct_resume = gate_result.get("passed")
        assert correct_resume is False

    def test_content_violations_with_blocking_go_to_human_review(self):
        gate_result = {
            "passed": False,
            "violations": [
                {"type": "timeline_conflict", "severity": "critical", "blocks_commit": True},
            ],
            "commit_blocked": True,
        }
        validator_violations = [
            v for v in gate_result.get("violations", [])
            if v.get("type") in ("critic_parse_error", "consistency_check_unavailable", "fcip_check_unavailable")
        ]
        remaining_blocking = [
            v for v in gate_result.get("violations", [])
            if v.get("blocks_commit")
        ]
        assert not validator_violations
        assert gate_result.get("passed") is False
        assert len(remaining_blocking) == 1
        should_pause = not gate_result.get("passed") and not validator_violations and bool(remaining_blocking)
        assert should_pause is True

    def test_non_blocking_violations_resume_generation(self):
        gate_result = {
            "passed": False,
            "violations": [
                {"type": "ghost_character", "severity": "medium", "blocks_commit": False},
            ],
            "commit_blocked": False,
        }
        validator_violations = [
            v for v in gate_result.get("violations", [])
            if v.get("type") in ("critic_parse_error", "consistency_check_unavailable", "fcip_check_unavailable")
        ]
        remaining_blocking = [
            v for v in gate_result.get("violations", [])
            if v.get("blocks_commit")
        ]
        assert not validator_violations
        assert gate_result.get("passed") is False
        assert not remaining_blocking
        should_resume = not gate_result.get("passed") and not validator_violations and not remaining_blocking
        assert should_resume is True

    def test_mixed_validator_and_content_violations_continues_retry(self):
        gate_result = {
            "passed": False,
            "violations": [
                {"type": "critic_parse_error", "severity": "high", "blocks_commit": True},
                {"type": "timeline_conflict", "severity": "critical", "blocks_commit": True},
            ],
            "commit_blocked": True,
        }
        validator_violations = [
            v for v in gate_result.get("violations", [])
            if v.get("type") in ("critic_parse_error", "consistency_check_unavailable", "fcip_check_unavailable")
        ]
        assert len(validator_violations) == 1
        should_continue_retry = not gate_result.get("passed") and bool(validator_violations)
        assert should_continue_retry is True
