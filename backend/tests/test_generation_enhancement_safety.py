from unittest.mock import AsyncMock, patch

import pytest

from app.models.generation_features import GenerationFeaturePolicy
from app.models.reader_experience import ReaderExperienceReport
from app.services.scene_generation_pipeline import (
    _collect_quality_reports_from_gate,
    _experience_effects_from_quality_report,
    _recovery_budget_for_generation_mode,
)
from app.repositories.progression_repository import ProgressionRepository
from app.services.concept_budget_service import ConceptBudgetService
from app.services.dialogue_attribution_service import DialogueAttributionService
from app.services.generation_feature_policy import GenerationFeaturePolicyService
from app.services.generation_trace_service import GenerationTraceService
from app.services.mention_detector import MentionDetector
from app.services.quality_checkers.ai_flavor_checker import AIFlavorChecker
from app.services.quality_checkers.concept_budget_checker import ConceptBudgetChecker
from app.services.quality_checkers.narrative_coherence_checker import NarrativeCoherenceChecker
from app.services.quality_gate import QualityGate
from app.services.quality_memory_service import QualityMemoryService
from app.services.reader_context_builder import ReaderContextBuilder
from app.services.scene_recovery_controller import classify_violations
from app.services.tool_permission_service import ToolPermissionService


def test_generation_feature_policy_defaults_are_safe():
    policy = GenerationFeaturePolicyService().resolve()
    assert policy.generation_mode == "legacy"
    assert policy.ai_flavor_mode == "report"
    assert policy.narrative_experience_mode == "assist"
    assert policy.literary_quality_mode == "assist"
    assert policy.commercial_pacing_mode == "assist"
    assert policy.prompt_trace_mode == "off"
    assert policy.is_legacy is False


def test_quality_advisory_is_not_recovery_violation():
    advisory = {
        "type": "ai_template_phrase",
        "severity": "high",
        "detail": "模板化表达",
        "detector": "ai_flavor_checker",
    }
    categories = classify_violations([advisory])
    assert not categories["text_local"]
    assert categories["non_repairable"]


def test_ai_flavor_checker_reports_advisories_without_violations():
    text = (
        "值得注意的是，她在这一刻感到震惊。与此同时，命运的齿轮开始转动。"
        "总而言之，这一切都说明故事才刚刚开始。"
    ) * 4
    report = AIFlavorChecker().check(text, {}, {})
    assert report["status"] == "ok"
    assert report["overall_level"] in {"yellow", "red"}
    assert report["advisories"]
    assert "violations" not in report


def test_ai_flavor_checker_reports_raw_emdash_metrics_only():
    text = "她停住脚步——风从门缝里钻进来。" * 10
    report = AIFlavorChecker().check(text, {}, {})
    assert report["metrics"]["emdash_pair_hits"] == 10
    assert report["metrics"]["max_consecutive_emdash_pairs"] == 1
    assert not any(a["type"] == "ai_punctuation_artifact" for a in report["advisories"])


def test_concept_budget_checker_uses_optional_contract_extension():
    contract = ConceptBudgetService().with_quality_extensions(
        {"scene_id": "s1"},
        {
            "new_concept_budget": 1,
            "allowed_new_concepts": ["灵契"],
            "forbidden_future_concepts": ["王庭真相"],
        },
    )
    report = ConceptBudgetChecker().check("所谓灵契，是一种誓约。王庭真相是一种禁忌。", contract, {})
    assert report["detected_concepts"]
    assert any(a["type"] == "forbidden_future_concept" for a in report["advisories"])


def test_narrative_coherence_checker_flags_incompatible_spatial_domains():
    text = "她应该在公司午休，趴在办公桌上，枕边是那本翻到一半的小说。"
    violations = NarrativeCoherenceChecker().check(text, {}, {})
    assert any(v["type"] == "spatial_consistency_error" for v in violations)


def test_narrative_coherence_checker_allows_explicit_spatial_transition():
    text = "她应该在公司午休，趴在办公桌上。不对，她睁开眼，手按下床板。"
    violations = NarrativeCoherenceChecker().check(text, {}, {})
    assert not any(v["type"] == "spatial_consistency_error" for v in violations)


def test_narrative_coherence_checker_flags_responsibility_polarity_flip():
    text = (
        "原书里说，凤溪嫉妒苏云清，偷了苏云清的寒玉簪藏在床铺下，被当场人赃俱获。"
        "三天后，苏云清就会来栽赃。"
    )
    violations = NarrativeCoherenceChecker().check(text, {}, {})
    assert any(v["type"] == "fact_conflict" for v in violations)


def test_narrative_coherence_checker_generalizes_beyond_theft():
    text = "族谱写着陆衡亲手泄露了密令。可下一段又说，泄密案其实是被人栽赃。"
    violations = NarrativeCoherenceChecker().check(text, {}, {})
    assert any(v["type"] == "fact_conflict" for v in violations)


def test_narrative_coherence_checker_does_not_treat_generic_hiding_as_theft():
    text = "她把信藏起。后来有人说另一件失窃案是栽赃。"
    violations = NarrativeCoherenceChecker().check(text, {}, {})
    assert not any(v["type"] == "fact_conflict" for v in violations)


def test_reader_context_builder_excludes_hidden_keys():
    ctx = ReaderContextBuilder().build(
        generated_text="正文",
        chapter_number=1,
        scene_index=0,
    )
    ReaderContextBuilder.assert_no_hidden_keys(ctx)
    with pytest.raises(ValueError):
        ReaderContextBuilder.assert_no_hidden_keys({"scene_truth_snapshot": {}})


def test_mention_detector_finds_aliases():
    detector = MentionDetector()
    mentions = detector.detect(
        "凤溪走进灰塔，阿溪没有回头。",
        [{"entity_type": "character", "entity_id": "c1", "name": "凤溪", "aliases": ["阿溪"]}],
    )
    assert len(mentions) == 2
    assert all(m["status"] == "candidate" for m in mentions)


def test_generation_trace_metadata_is_sanitized():
    trace = GenerationTraceService().build_metadata(
        project_id="00000000-0000-0000-0000-000000000001",
        chapter_number=1,
        scene_index=0,
        context={"api_key": "secret", "scene_contract": {"goal": "逃离"}},
        generated_text="正文",
        quality_report={"violations": []},
        recovery={"attempts": []},
    )
    assert "api_key" not in trace["context_manifest"]
    assert "scene_contract" in trace["context_manifest"]
    assert trace["generated_text_hash"]


def test_generation_trace_prompt_preview_includes_quality_extensions_only_in_full_mode():
    context = {
        "scene_contract": {
            "goal": "逃离",
            "quality_extensions": {
                "anti_ai_guidance": ["减少直接心理解释", "结尾停在角色行动上"],
                "reveal_control": {
                    "new_concept_budget": 1,
                    "forbidden_future_concepts": ["大师伯真实身份"],
                    "preferred_carriers": ["action", "dialogue"],
                },
                "character_voice_guidance": {
                    "凤溪": ["对白短促", "不要完整说出真实意图"],
                },
            },
        },
    }

    full_trace = GenerationTraceService().build_metadata(
        project_id="00000000-0000-0000-0000-000000000001",
        chapter_number=1,
        scene_index=0,
        context=context,
        generated_text="正文内容",
        mode="full",
    )
    metadata_trace = GenerationTraceService().build_metadata(
        project_id="00000000-0000-0000-0000-000000000001",
        chapter_number=1,
        scene_index=0,
        context=context,
        generated_text="正文内容",
        mode="metadata",
    )

    preview = full_trace["prompt_preview"]["quality_extensions"]
    assert preview["anti_ai_guidance"] == ["减少直接心理解释", "结尾停在角色行动上"]
    assert preview["reveal_control"]["new_concept_budget"] == 1
    assert preview["reveal_control"]["forbidden_future_concepts"] == ["大师伯真实身份"]
    assert preview["character_voice_guidance"]["凤溪"] == ["对白短促", "不要完整说出真实意图"]
    assert metadata_trace["prompt_preview"] == {}


def test_generation_trace_metadata_does_not_store_advisory_text_spans():
    trace = GenerationTraceService().build_metadata(
        project_id="00000000-0000-0000-0000-000000000001",
        chapter_number=1,
        scene_index=0,
        context={},
        generated_text="完整正文",
        quality_report={
            "reports": {
                "experimental": {
                    "ai_flavor": {
                        "status": "ok",
                        "advisories": [{
                            "type": "ai_template_phrase",
                            "target_span": "这是不能进入 metadata Trace 的正文片段",
                            "detail": "详细建议",
                        }],
                    },
                },
            },
        },
    )
    serialized = str(trace["quality_summary"])
    assert "不能进入" not in serialized
    assert "详细建议" not in serialized
    assert "ai_template_phrase" in serialized


def test_concept_budget_forbidden_matching_does_not_match_short_prefix():
    checker = ConceptBudgetChecker()
    assert checker._matches_forbidden("王", {"王庭"}) is False
    assert checker._matches_forbidden("王庭真相", {"王庭"}) is True


def test_dialogue_attribution_supports_common_quote_styles():
    items = DialogueAttributionService().extract_dialogues(
        '凤溪说：“走吧。”苏云清说："等等。"守门人说：「不能走。」'
    )
    assert [item["text"] for item in items] == ["走吧。", "等等。", "不能走。"]


def test_rhythm_checker_only_reports_low_variation():
    checker = AIFlavorChecker()
    assert checker._rhythm_advisories("x", {
        "sentence_count": 10,
        "sentence_length_cv": 0.2,
        "paragraph_count": 4,
        "paragraph_length_cv": 0.1,
    })
    assert checker._rhythm_advisories("x", {
        "sentence_count": 10,
        "sentence_length_cv": 0.4,
        "paragraph_count": 4,
        "paragraph_length_cv": 0.1,
    }) == []


def test_reader_experience_scores_are_bounded():
    with pytest.raises(ValueError):
        ReaderExperienceReport(scores={"clarity": 11})


def test_progression_fingerprint_is_idempotent():
    repository = ProgressionRepository()
    args = ("p1", "character", "c1", 1, 0, "mention", "凤溪走进门内")
    assert repository._fingerprint(*args) == repository._fingerprint(*args)
    assert repository._fingerprint(*args) != repository._fingerprint(*args[:-1], "凤溪离开")


def test_generation_modes_have_distinct_recovery_budgets():
    assert _recovery_budget_for_generation_mode("legacy") is None
    assert _recovery_budget_for_generation_mode("standard") is None
    assert _recovery_budget_for_generation_mode("quick")["max_rewrite"] == 0
    assert _recovery_budget_for_generation_mode("deep")["max_rewrite"] > 0


def test_tool_permission_service_blocks_unknown_tool_by_default():
    decision = ToolPermissionService().can_use("readonly", "update_project_state")
    assert decision.allowed is False
    assert "project_write" in decision.reason


def test_quality_report_collection_reads_top_level_and_experimental_layers():
    quality_gate = {
        "reports": {
            "narrative_experience": {
                "mode": "assist",
                "scores": {"reading_drive": 0.4},
                "advisories": [{
                    "type": "low_reading_drive",
                    "severity": "medium",
                    "confidence": 0.8,
                }],
                "mode_fit": {"writing_mode_id": "commercial_web", "fit_score": 0.63},
            },
            "experimental": {
                "ai_flavor": {
                    "mode": "assist",
                    "advisories": [{
                        "type": "punctuation_artifact",
                        "severity": "medium",
                        "confidence": 0.7,
                    }],
                }
            },
        }
    }

    reports = _collect_quality_reports_from_gate(quality_gate)

    assert "narrative_experience" in reports
    assert "ai_flavor" in reports
    assert reports["mode_fit"]["writing_mode_id"] == "commercial_web"


def test_experience_effects_include_collected_quality_reports_for_persistence():
    scene_contract = {
        "experience_contract": {"writing_mode_id": "commercial_web", "reader_drive": "choice"},
        "literary_quality_contract": {"writing_mode_id": "commercial_web", "specificity_budget": {}},
        "commercial_pacing_contract": {"profile_id": "general", "scene_position": "opening"},
    }
    quality_gate = {
        "reports": {
            "commercial_pacing": {
                "mode": "assist",
                "scores": {"conflict_density": 0.2},
                "advisories": [{
                    "type": "low_conflict_density",
                    "severity": "medium",
                    "confidence": 0.82,
                }],
            }
        }
    }

    effects = _experience_effects_from_quality_report(quality_gate, scene_contract)

    assert effects["experience_contract"] == scene_contract["experience_contract"]
    assert effects["literary_quality_contract"] == scene_contract["literary_quality_contract"]
    assert effects["commercial_pacing_contract"] == scene_contract["commercial_pacing_contract"]
    assert "commercial_pacing" in effects["experience_quality_reports"]


def test_quality_memory_accumulates_current_turn_commercial_advisories():
    memory = QualityMemoryService().update_quality_memory(
        {},
        advisories=[{
            "type": "low_conflict_density",
            "severity": "medium",
            "confidence": 0.82,
            "source_checker": "commercial_pacing",
        }],
        mode_fit={"writing_mode_id": "commercial_web", "fit_score": 0.6},
        writing_mode_id="commercial_web",
        chapter_number=2,
        scene_index=1,
    )

    assert memory["recent_quality_patterns"][0]["type"] == "low_conflict_density_trend"
    assert memory["recent_quality_patterns"][0]["last_seen_chapter"] == 2
    assert memory["mode_fit_history"][0]["writing_mode_id"] == "commercial_web"


@pytest.mark.asyncio
async def test_quality_gate_shadow_experimental_does_not_block():
    gate = QualityGate()
    policy = GenerationFeaturePolicy(
        ai_flavor_mode="shadow",
        reader_experience_mode="off",
        character_voice_mode="off",
        style_quality_mode="off",
        writing_mode_profile_mode="off",
        narrative_experience_mode="off",
        literary_quality_mode="off",
        mode_fit_mode="off",
        style_experience_conflict_mode="off",
        commercial_pacing_mode="off",
    )
    with patch.object(gate, "_run_deterministic", new_callable=AsyncMock, return_value=[]), \
         patch.object(gate, "_run_consistency", new_callable=AsyncMock, return_value={"violations": []}), \
         patch.object(gate, "_run_semantic", new_callable=AsyncMock, return_value=[]), \
         patch.object(gate, "_run_fcip", new_callable=AsyncMock, return_value={"violations": [], "detection": {}}):
        result = await gate.evaluate(
            {
                "generated_text": "值得注意的是，她在这一刻感到震惊。" * 6,
                "scene_contract": {"goal": "逃离", "conflict": "追兵逼近"},
                "generation_feature_policy": policy,
            },
            level="full",
        )

    assert result["passed"] is True
    assert result["commit_blocked"] is False
    assert result["violations"] == []
    assert "experimental" in result["reports"]
    assert "ai_flavor" in result["reports"]["experimental"]


@pytest.mark.asyncio
async def test_quality_gate_enforce_routes_high_advisory_without_blocking():
    gate = QualityGate()
    policy = GenerationFeaturePolicy(ai_flavor_mode="enforce")
    with patch.object(gate, "_run_deterministic", new_callable=AsyncMock, return_value=[]), \
         patch.object(gate, "_run_quality_checkers", return_value=[]):
        result = await gate.evaluate(
            {
                "generated_text": "值得注意的是，总而言之，与此同时，这一切都说明。" * 12,
                "scene_contract": {"goal": "逃离", "conflict": "追兵逼近"},
                "generation_feature_policy": policy,
            },
            level="fast",
        )
    assert result["commit_blocked"] is False
    assert any(v["type"].startswith("enforced_") for v in result["violations"])
    enforced = [v for v in result["violations"] if v["type"].startswith("enforced_")]
    assert all(v["blocks_commit"] is False for v in enforced)
    assert all(v["suggested_strategy"] == "patch_text" for v in enforced)
    assert classify_violations(enforced)["text_local"]
