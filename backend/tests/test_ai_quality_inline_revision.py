import pytest

from app.agents.scene_repairer import SceneRepairer
from app.services import scene_generation_pipeline as generation
from app.models.generation_features import GenerationFeaturePolicy
from app.services.generation_trace_service import GenerationTraceService


def test_inline_reviser_applies_only_exact_local_patches():
    draft = "role_a felt afraid because someone was outside. in summary, she had to move."
    patches = [{
        "original": "role_a felt afraid because someone was outside.",
        "replacement": "a shadow pressed against the window, and role_a tightened her fingers.",
        "reason": "replace abstract emotion with observable action",
    }]

    revised, applied, skipped = SceneRepairer.apply_patches(draft, patches)

    assert revised == "a shadow pressed against the window, and role_a tightened her fingers. in summary, she had to move."
    assert len(applied) == 1
    assert skipped == []
    assert "felt afraid" not in revised


def test_inline_reviser_matches_whitespace_drift_without_rewriting_scene():
    draft = "role_a opened\nthe door. role_b stood below the eaves."
    patches = [{
        "original": "role_a opened the door.",
        "replacement": "role_a eased the door open.",
        "reason": "use a concrete action",
    }]

    revised, applied, skipped = SceneRepairer.apply_patches(draft, patches)

    assert revised == "role_a eased the door open. role_b stood below the eaves."
    assert skipped == []
    assert applied[0]["matched_by"] == "normalized_whitespace"


def test_inline_reviser_rejects_non_local_or_oversized_patches():
    draft = "role_a opened the door. role_b stood below the eaves."
    patches = [
        {
            "original": "missing span",
            "replacement": "replacement",
            "reason": "cannot locate",
        },
        {
            "original": "role_a opened the door.",
            "replacement": "new " * 400,
            "reason": "too large",
        },
    ]

    revised, applied, skipped = SceneRepairer.apply_patches(draft, patches)

    assert revised == draft
    assert applied == []
    assert {item["reason"] for item in skipped} == {"original_not_found", "replacement_too_large"}


def test_inline_reviser_allows_only_declared_unique_short_semantic_anchor():
    target = "你们是谁？"
    draft = f"她睁开眼。\n\n“{target}”\n\n无人回答。"
    patches = [{
        "original": target,
        "replacement": f"灵魂重塑带走了她的记忆。\n\n{target}",
        "reason": "补足失忆因果",
    }]

    revised, applied, skipped = SceneRepairer.apply_patches(
        draft,
        patches,
        allowed_short_originals=[target],
    )

    assert len(applied) == 1
    assert skipped == []
    assert revised.count(target) == 1
    assert "灵魂重塑带走了她的记忆" in revised


def test_inline_reviser_rejects_declared_short_anchor_when_not_unique():
    target = "你们是谁？"
    draft = f"{target}\n\n{target}"
    patches = [{
        "original": target,
        "replacement": f"记忆消退。\n\n{target}",
        "reason": "补足过渡",
    }]

    revised, applied, skipped = SceneRepairer.apply_patches(
        draft,
        patches,
        allowed_short_originals=[target],
    )

    assert revised == draft
    assert applied == []
    assert skipped[0]["reason"] == "original_too_short"


def test_inline_reviser_exposes_actionable_failure_reason():
    skipped = [
        {"reason": "original_not_found"},
        {"reason": "original_not_found"},
        {"reason": "replacement_too_large"},
    ]
    counts = {}
    for patch in skipped:
        counts[patch["reason"]] = counts.get(patch["reason"], 0) + 1
    result = {
        "attempted_patch_count": len(skipped),
        "skipped_reason_counts": counts,
        "failure_reason": f"no_valid_local_patch:{max(counts, key=counts.get)}",
    }

    assert result["attempted_patch_count"] == 3
    assert result["skipped_reason_counts"] == {
        "original_not_found": 2,
        "replacement_too_large": 1,
    }
    assert result["failure_reason"] == "no_valid_local_patch:original_not_found"


@pytest.mark.asyncio
async def test_inline_revision_skips_when_only_report(monkeypatch):
    class FailingInlineAgent:
        async def execute(self, context):
            raise AssertionError("inline reviser should not run")

    monkeypatch.setattr(generation, "SceneRepairer", lambda: FailingInlineAgent())

    text, report = await generation._run_inline_ai_quality_revision(
        generated_text="in summary, role_a had to move.",
        scene_contract={"goal": "escape"},
        chapter_state={},
        character_cards=[],
        feature_policy=GenerationFeaturePolicy(
            ai_flavor_mode="report",
            reader_experience_mode="off",
            character_voice_mode="off",
            writing_mode_profile_mode="off",
            narrative_experience_mode="off",
                literary_quality_mode="off",
                mode_fit_mode="off",
                style_experience_conflict_mode="off",
                commercial_pacing_mode="off",
                scene_credibility_mode="off",
            ),
        project_quality_memory={},
        chapter_number=1,
        scene_index=0,
    )

    assert text == "in summary, role_a had to move."
    assert report == {
        "status": "skipped",
        "mode": "deterministic_post_process",
        "reason": "no_deslop_issues_detected",
    }


@pytest.mark.asyncio
async def test_inline_revision_runs_before_full_pipeline_when_enforced(monkeypatch):
    calls = {}

    class FakeInlineAgent:
        async def execute(self, context):
            calls["hints"] = context["revision_hints"]
            draft = context["draft_text"]
            revised = draft.replace("in summary, role_a had to move. ", "role_a gripped the doorframe. ", 1)
            return {
                "success": True,
                "revised_text": revised,
                "draft_hash": "draft-hash",
                "final_hash": "final-hash",
                "applied_patches": [{"original_hash": "a", "replacement_hash": "b"}],
                "skipped_patches": [],
                "applied_patch_count": 1,
                "error": "",
            }

    monkeypatch.setattr(generation, "SceneRepairer", lambda: FakeInlineAgent())
    monkeypatch.setattr(
        generation,
        "_build_inline_quality_reports",
        lambda **kwargs: {
            "ai_flavor": {
                "advisories": [{
                    "type": "template_phrase",
                    "severity": "medium",
                    "confidence": 0.9,
                    "source_checker": "ai_flavor",
                    "detail": "template phrase",
                }]
            }
        },
    )
    draft = "in summary, role_a had to move. " * 6

    text, report = await generation._run_inline_ai_quality_revision(
        generated_text=draft,
        scene_contract={"goal": "escape"},
        chapter_state={},
        character_cards=[],
        feature_policy=GenerationFeaturePolicy(ai_flavor_mode="enforce"),
        project_quality_memory={},
        chapter_number=1,
        scene_index=0,
    )

    assert text != draft
    assert text.startswith("role_a gripped the doorframe.")
    assert report["status"] == "applied"
    assert report["mode"] == "inline_pre_gate"
    assert report["applied_patch_count"] == 1
    assert report["draft_hash"] == "draft-hash"
    assert calls["hints"]
    assert calls["hints"][0]["auto_revise_allowed"] is True
    assert calls["hints"][0]["needs_user_confirm"] is False


@pytest.mark.asyncio
async def test_inline_revision_runs_in_current_turn_when_assist(monkeypatch):
    calls = {}

    class FakeInlineAgent:
        async def execute(self, context):
            calls["hints"] = context["revision_hints"]
            draft = context["draft_text"]
            revised = draft.replace("in summary, role_a had to move. ", "role_a gripped the doorframe. ", 1)
            return {
                "success": True,
                "revised_text": revised,
                "draft_hash": "draft-hash",
                "final_hash": "final-hash",
                "applied_patches": [{"original_hash": "a", "replacement_hash": "b"}],
                "skipped_patches": [],
                "applied_patch_count": 1,
                "error": "",
            }

    monkeypatch.setattr(generation, "SceneRepairer", lambda: FakeInlineAgent())
    monkeypatch.setattr(
        generation,
        "_build_inline_quality_reports",
        lambda **kwargs: {
            "ai_flavor": {
                "advisories": [{
                    "type": "template_phrase",
                    "severity": "medium",
                    "confidence": 0.9,
                    "source_checker": "ai_flavor",
                    "detail": "template phrase",
                }]
            }
        },
    )
    draft = "in summary, role_a had to move. " * 6

    text, report = await generation._run_inline_ai_quality_revision(
        generated_text=draft,
        scene_contract={"goal": "escape"},
        chapter_state={},
        character_cards=[],
        feature_policy=GenerationFeaturePolicy(ai_flavor_mode="assist"),
        project_quality_memory={},
        chapter_number=1,
        scene_index=0,
    )

    assert text != draft
    assert report["status"] == "applied"
    assert report["mode"] == "inline_pre_gate"
    assert calls["hints"]
    assert calls["hints"][0]["source_mode"] == "assist"
    assert calls["hints"][0]["auto_revise_allowed"] is True


def test_generation_trace_records_inline_revision_summary_without_text():
    trace = GenerationTraceService().build_metadata(
        project_id="00000000-0000-0000-0000-000000000001",
        chapter_number=1,
        scene_index=0,
        context={"scene_contract": {"goal": "escape"}},
        generated_text="final text",
        recovery={
            "status": "accepted",
            "recovery_mode": "none",
            "attempts": [],
            "ai_quality_inline_revision": {
                "status": "applied",
                "mode": "inline_pre_gate",
                "applied_patch_count": 1,
                "eligible_hint_count": 1,
                "draft_hash": "draft-hash",
                "final_hash": "final-hash",
                "applied_patches": [{"original": "must not store text"}],
            },
        },
    )

    summary = trace["recovery_summary"]["ai_quality_inline_revision"]
    assert summary == {
        "status": "applied",
        "mode": "inline_pre_gate",
        "applied_patch_count": 1,
        "eligible_hint_count": 1,
        "draft_hash": "draft-hash",
        "final_hash": "final-hash",
    }
    assert "must not store text" not in str(trace)


def test_generation_trace_records_inline_revision_failure_reason_without_text():
    trace = GenerationTraceService().build_metadata(
        project_id="00000000-0000-0000-0000-000000000001",
        chapter_number=1,
        scene_index=0,
        context={"scene_contract": {"goal": "escape"}},
        generated_text="final text",
        recovery={
            "status": "accepted",
            "recovery_mode": "none",
            "attempts": [],
            "ai_quality_inline_revision": {
                "status": "failed",
                "mode": "inline_pre_gate",
                "applied_patch_count": 0,
                "eligible_hint_count": 1,
                "draft_hash": "draft-hash",
                "final_hash": "draft-hash",
                "failure_reason": "no_valid_local_patch:original_not_found",
                "skipped_reason_counts": {"original_not_found": 1},
                "skipped_patches": [{"original_preview": "must not store text"}],
            },
        },
    )

    summary = trace["recovery_summary"]["ai_quality_inline_revision"]
    assert summary["failure_reason"] == "no_valid_local_patch:original_not_found"
    assert summary["skipped_reason_counts"] == {"original_not_found": 1}
    assert "must not store text" not in str(trace)
