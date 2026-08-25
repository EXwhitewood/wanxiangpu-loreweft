from app.services.writing_mode_profile_service import WritingModeProfileService


def test_profile_service_loads_project_independent_modes():
    profiles = WritingModeProfileService().list_profiles()
    ids = {profile.id for profile in profiles}

    assert "general" in ids
    assert "commercial_web" in ids
    assert "literary" in ids

    serialized = "\n".join(str(profile.model_dump()) for profile in profiles)
    polluted_terms = ["特定角色A", "特定角色B", "特定物品X", "特定作品名Y"]
    assert not any(term in serialized for term in polluted_terms)


def test_get_unknown_profile_falls_back_to_general():
    profile = WritingModeProfileService().get_profile("unknown-mode")

    assert profile.id == "general"
    assert profile.metric_weights
