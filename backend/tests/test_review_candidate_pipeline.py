from app.api.editor_chat import (
    _classify_review_violation_for_commit,
    _recheck_repair_type,
)
from app.services.agent_skill_candidate_pipeline import ReviewCandidatePipeline, SkillCandidatePipeline
from app.services.agent_skill_repair_dispatcher import AgentSkillRepairDispatcher


def test_ai_punctuation_is_hard_but_uses_local_patch():
    violation = {
        "type": "ai_punctuation_artifact",
        "severity": "high",
        "blocks_commit": True,
        "detail": "破折号使用密度偏高：全文 40 处，每千字约 7.1 处。",
    }

    assert _classify_review_violation_for_commit(violation) == "content_blocking"
    assert _recheck_repair_type(violation) == "local_patch"


def test_recheck_repair_route_is_stable_across_content_issue_types():
    assert _recheck_repair_type({"type": "fact_conflict"}) == "local_patch"
    assert _recheck_repair_type({"type": "ending_state_not_reached"}) == "scene_rewrite"
    assert _recheck_repair_type({"type": "missing_must_show"}) == "scene_rewrite"


def test_review_candidate_rejects_dash_regression_after_content_fix():
    before = [
        {
            "type": "fact_conflict",
            "severity": "high",
            "detail": "正文与既定事实冲突。",
        },
        {
            "type": "ai_punctuation_artifact",
            "severity": "medium",
            "detail": "破折号使用密度偏高：全文 7 处，每千字约 2.3 处。",
        },
    ]
    after = [
        {
            "type": "ai_punctuation_artifact",
            "severity": "high",
            "detail": "破折号使用密度偏高：全文 40 处，每千字约 7.1 处。",
        },
    ]

    decision = ReviewCandidatePipeline().assess(
        before_violations=before,
        after_violations=after,
        target_violations=[before[0]],
        classify_lane=_classify_review_violation_for_commit,
    )

    assert decision["accepted"] is False
    assert decision["status"] == "rejected_quality_regression"
    assert decision["quality_regressions"] == ["ai_punctuation_artifact"]
    assert decision["atomic_disposition"] == "rollback_round"


def test_review_candidate_accepts_content_fix_without_quality_regression():
    before = [
        {
            "type": "fact_conflict",
            "severity": "high",
            "detail": "正文与既定事实冲突。",
        },
        {
            "type": "ai_punctuation_artifact",
            "severity": "medium",
            "detail": "破折号使用密度偏高：全文 7 处，每千字约 2.3 处。",
        },
    ]
    after = [
        {
            "type": "ai_punctuation_artifact",
            "severity": "medium",
            "detail": "破折号使用密度偏高：全文 6 处，每千字约 2.0 处。",
        },
    ]

    decision = ReviewCandidatePipeline().assess(
        before_violations=before,
        after_violations=after,
        target_violations=[before[0]],
        classify_lane=_classify_review_violation_for_commit,
    )

    assert decision["accepted"] is True
    assert decision["target_resolved"] is True
    assert decision["atomic_disposition"] == "commit_candidate"

def test_skill_candidate_stages_repairable_breathing_regression():
    pipeline = SkillCandidatePipeline(
        normalize_hook=AgentSkillRepairDispatcher._normalize_hook,
        has_executable_hook=lambda hook: hook in {"paragraph_reconstruction", "pacing_repair"},
    )
    before = {
        "passed": False,
        "failures": [
            {
                "validator": "ai_discourse",
                "metric": "paragraph_shape_repeat_count",
                "actual": 39,
                "expected_max": 1,
                "action": "paragraph_reconstruction",
            },
            {
                "validator": "rhythm_metrics",
                "metric": "dense_paragraph_streak_max",
                "actual": 8,
                "expected_max": 5,
                "action": "break_dense_streak",
            },
        ],
    }
    after = {
        "passed": False,
        "failures": [
            {
                "validator": "ai_discourse",
                "metric": "paragraph_shape_repeat_count",
                "actual": 2,
                "expected_max": 1,
                "action": "paragraph_reconstruction",
            },
            {
                "validator": "rhythm_metrics",
                "metric": "breathing_paragraph_ratio",
                "actual": 0.125,
                "expected_min": 0.15,
                "action": "inject_breathing_paragraph",
            },
            {
                "validator": "rhythm_metrics",
                "metric": "dense_paragraph_streak_max",
                "actual": 9,
                "expected_max": 5,
                "action": "break_dense_streak",
            },
        ],
    }

    decision = pipeline.assess(before, after, [before["failures"][0]])

    assert decision["status"] == "staged_repairable_regression"
    assert decision["staged"] is True
    assert decision["unresolved_regressions"] == []
    assert "rhythm_metrics:breathing_paragraph_ratio" in decision["repairable_regressions"]
    assert decision["cleanup_hooks"] == ["pacing_repair"]
