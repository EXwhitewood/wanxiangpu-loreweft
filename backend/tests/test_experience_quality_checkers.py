from app.services.quality_checkers.literary_quality_checker import LiteraryQualityChecker
from app.services.quality_checkers.narrative_experience_checker import NarrativeExperienceChecker
from app.models.writing_mode import WritingModeProfile
from app.services.writing_mode_profile_service import WritingModeProfileService


def test_narrative_experience_checker_reports_weak_drive():
    profile = WritingModeProfileService().get_profile("commercial_web")
    report = NarrativeExperienceChecker().check(
        "Character A thinks for a long time. She realizes the matter is complicated. At last, she understands everything.",
        {
            "writing_mode_id": "commercial_web",
            "obstacle": "Character B blocks her",
            "agency_requirement": "Character A must take visible action",
        },
        profile,
    )

    advisory_types = {adv["type"] for adv in report["advisories"]}
    assert "low_reading_drive" in advisory_types or "weak_hook_out" in advisory_types
    assert "mode_fit" in report


def test_literary_quality_checker_reports_specificity_budget():
    profile = WritingModeProfileService().get_profile("literary")
    report = LiteraryQualityChecker().check(
        "\u590d\u6742\u7684\u60c5\u7eea\u548c\u547d\u8fd0\u7684\u610f\u4e49\u4ee3\u8868\u7075\u9b42\u7684\u672c\u8d28\uff0c\u8fd9\u610f\u5473\u7740\u5979\u610f\u8bc6\u5230\u5185\u5fc3\u7684\u67d0\u79cd\u53d8\u5316\u3002",
        {
            "writing_mode_id": "literary",
            "specificity_budget": {
                "minimum_specific_details": 4,
                "abstract_explanation_limit": 1,
            },
            "cultural_texture_policy": {"requirement": "high"},
        },
        profile,
    )

    advisory_types = {adv["type"] for adv in report["advisories"]}
    assert "specificity_budget_unmet" in advisory_types
    assert "abstraction_over_budget" in advisory_types


def test_experience_quality_checkers_accept_preview_shaped_contracts():
    profile = WritingModeProfileService().get_profile("commercial_web")

    narrative = NarrativeExperienceChecker().check(
        "Actor A opens the door, sees the cup, and stops.",
        {
            "writing_mode_id": "commercial_web",
            "scene_embodiment": ["cup", "door", "gesture"],
            "withheld_information": "identity of the caller",
            "reveal_policy": ["hold back one fact"],
        },
        profile,
    )
    literary = LiteraryQualityChecker().check(
        "Actor A opens the door, sees the cup, and stops.",
        {
            "writing_mode_id": "commercial_web",
            "specificity_budget": ["cup", "door"],
            "avoid_patterns": "generic abstraction",
        },
        profile,
    )

    assert narrative["status"] == "ok"
    assert literary["status"] == "ok"



def test_narrative_mode_fit_uses_average_when_profile_has_no_weights():
    report = NarrativeExperienceChecker().check(
        "Actor opens the door. Footsteps stop outside. She grips the knife and waits.",
        {},
        WritingModeProfile(id="empty", label="Empty", metric_weights={}),
    )

    assert report["mode_fit"]["fit_score"] > 0
    assert report["mode_fit"]["mismatches"] == []
