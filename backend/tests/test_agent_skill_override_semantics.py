"""Tests for override semantics (plan 16.2)."""
import pytest


class TestOverrideSemantics:
    """Test that skills=[] means disable all, skills=None means use default."""

    def test_none_means_default(self):
        """skills=None should use default skills."""
        override_skills = None
        default_skills = ["scene_preparation", "attention_director"]
        enabled = override_skills if override_skills is not None else default_skills
        assert enabled == default_skills

    def test_empty_list_means_disable_all(self):
        """skills=[] should disable all skills."""
        override_skills = []
        default_skills = ["scene_preparation", "attention_director"]
        enabled = override_skills if override_skills is not None else default_skills
        assert enabled == []

    def test_explicit_list_means_only_those(self):
        """skills=['x'] should only enable x."""
        override_skills = ["show_dont_tell"]
        default_skills = ["scene_preparation", "attention_director"]
        enabled = override_skills if override_skills is not None else default_skills
        assert enabled == ["show_dont_tell"]

    def test_agent_skills_same_semantics(self):
        """agent_skills follows same None/[]/['x'] semantics."""
        override_agent_skills = None
        default_agent_skills = ["show_dont_tell", "anti_ai_prose"]
        enabled = override_agent_skills if override_agent_skills is not None else default_agent_skills
        assert enabled == default_agent_skills

        override_agent_skills = []
        enabled = override_agent_skills if override_agent_skills is not None else default_agent_skills
        assert enabled == []

        override_agent_skills = ["rhythm_control"]
        enabled = override_agent_skills if override_agent_skills is not None else default_agent_skills
        assert enabled == ["rhythm_control"]

    def test_has_override_with_empty_list(self):
        """has_override should be True when skills=[] (explicit disable)."""
        override = type("Override", (), {"skills": [], "agent_skills": None, "api_key": None, "base_url": None, "model": None, "persona": None})()
        has_override = override is not None and (
            bool(override.api_key) or bool(override.base_url) or bool(override.model)
            or override.skills is not None or override.agent_skills is not None or bool(override.persona)
        )
        assert has_override is True

    @pytest.mark.asyncio
    async def test_agent_detail_merges_runtime_skill_defaults(self):
        """Agent detail should expose runtime skill-map defaults to the runtime/UI."""
        from app.models.api_config import AppSettings
        from app.services.agent_config import AgentConfigManager

        manager = AgentConfigManager()

        core = await manager.get_agent_detail("core_generation", _cached_settings=AppSettings())
        assert "show_dont_tell" in core["enabled_agent_skills"]
        assert "rhythm_control" in core["enabled_agent_skills"]

        worldbuilder = await manager.get_agent_detail("worldbuilder", _cached_settings=AppSettings())
        assert "worldbuilder_rule_tools" in worldbuilder["enabled_skills"]

        content_repair = await manager.get_agent_detail("content_repair", _cached_settings=AppSettings())
        assert content_repair is not None
        assert "state_query" in content_repair["enabled_skills"]
        assert "core_query" in content_repair["enabled_skills"]
        assert "fact_repair" in content_repair["enabled_agent_skills"]
        assert "scene_restructure" in content_repair["enabled_agent_skills"]
        assert "protected_span_policy" in content_repair["enabled_agent_skills"]

        style_repair = await manager.get_agent_detail("style_repair", _cached_settings=AppSettings())
        assert style_repair is not None
        assert "prose_repair" in style_repair["enabled_agent_skills"]
        assert "voice_repair" in style_repair["enabled_agent_skills"]
        assert "pacing_repair" in style_repair["enabled_agent_skills"]
        assert "style_guard" in style_repair["enabled_agent_skills"]

    @pytest.mark.asyncio
    async def test_context_builder_keeps_defaults_for_unoverridden_category(self, monkeypatch):
        """Partial overrides should not turn the other skill category into all-eligible."""
        from app.models.api_config import AgentOverride, AppSettings
        from app.services.agent_skill_context import SkillContextBuilder

        builder = SkillContextBuilder()

        async def fake_load_settings():
            return AppSettings(
                agent_overrides={
                    "core_generation": AgentOverride(skills=[]),
                }
            )

        monkeypatch.setattr(builder._config_manager, "load_settings", fake_load_settings)

        ctx = await builder.build("core_generation")

        assert ctx.enabled_skills == []
        assert "narrative_writing" in ctx.enabled_agent_skills
        assert "show_dont_tell" in ctx.enabled_agent_skills
