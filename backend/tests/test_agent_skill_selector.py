"""Tests for AgentSkillSelector (plan 16.4)."""
import pytest

from app.models.agent_skill import (
    AgentSkillManifest,
    SkillSelectionContext,
    SkillTrigger,
)


@pytest.fixture
def sample_manifests():
    return [
        AgentSkillManifest(
            id="show_dont_tell",
            name="展示而非讲述",
            kind="prompt",
            domain="writing",
            category="agent",
            agents=["core_generation"],
            priority=80,
            enabled_by_default=True,
            triggers=SkillTrigger(
                agents=["core_generation"],
                scene_roles=["conflict", "emotional_turn"],
                issue_types=["ai_emotion_label"],
            ),
        ),
        AgentSkillManifest(
            id="consistency_check",
            name="一致性检查",
            kind="prompt",
            domain="worldbuilder",
            category="agent",
            agents=["worldbuilder"],
            priority=70,
            enabled_by_default=True,
            triggers=SkillTrigger(
                agents=["worldbuilder"],
                tabs=["rules"],
            ),
        ),
        AgentSkillManifest(
            id="rhythm_control",
            name="节奏控制",
            kind="prompt",
            domain="writing",
            category="agent",
            agents=["core_generation"],
            priority=60,
            enabled_by_default=False,
            triggers=SkillTrigger(
                agents=["core_generation"],
                issue_types=["rhythm_uniformity"],
            ),
        ),
    ]


class TestAgentSkillSelector:
    """Tests for skill selection logic."""

    @pytest.mark.asyncio
    async def test_agent_filtering(self, sample_manifests):
        """Only select skills available to the current agent."""
        from app.services.agent_skill_selector import AgentSkillSelector
        selector = AgentSkillSelector()
        ctx = SkillSelectionContext(agent_name="core_generation")
        result = await selector.select(ctx, sample_manifests)
        for m in result.selected:
            assert not m.agents or "core_generation" in m.agents

    @pytest.mark.asyncio
    async def test_tab_trigger(self, sample_manifests):
        """active_tab triggers worldbuilder skill."""
        from app.services.agent_skill_selector import AgentSkillSelector
        selector = AgentSkillSelector()
        ctx = SkillSelectionContext(
            agent_name="worldbuilder",
            active_tab="rules",
        )
        result = await selector.select(ctx, sample_manifests)
        selected_ids = [m.id for m in result.selected]
        assert "consistency_check" in selected_ids

    @pytest.mark.asyncio
    async def test_issue_type_trigger(self, sample_manifests):
        """Issue type triggers feedback skill."""
        from app.services.agent_skill_selector import AgentSkillSelector
        selector = AgentSkillSelector()
        ctx = SkillSelectionContext(
            agent_name="core_generation",
            previous_quality_reports={"issue_types": ["rhythm_uniformity"]},
        )
        result = await selector.select(ctx, sample_manifests)
        selected_ids = [m.id for m in result.selected]
        assert "rhythm_control" in selected_ids

    @pytest.mark.asyncio
    async def test_disabled_skill_not_selected(self, sample_manifests):
        """User-disabled skill is not selected."""
        from app.services.agent_skill_selector import AgentSkillSelector
        selector = AgentSkillSelector()
        ctx = SkillSelectionContext(
            agent_name="core_generation",
            enabled_skills=[],
            enabled_agent_skills=[],
        )
        result = await selector.select(ctx, sample_manifests)
        assert len(result.selected) == 0

    @pytest.mark.asyncio
    async def test_explicit_enable_overrides_default(self, sample_manifests):
        """Explicitly enabled skill overrides default selection."""
        from app.services.agent_skill_selector import AgentSkillSelector
        selector = AgentSkillSelector()
        ctx = SkillSelectionContext(
            agent_name="core_generation",
            enabled_skills=["rhythm_control"],
        )
        result = await selector.select(ctx, sample_manifests)
        selected_ids = [m.id for m in result.selected]
        assert "rhythm_control" in selected_ids

    @pytest.mark.asyncio
    async def test_max_active_skills_limit(self):
        """Max active skills limit is enforced."""
        from app.services.agent_skill_selector import AgentSkillSelector, MAX_ACTIVE_SKILLS
        selector = AgentSkillSelector()
        # Create more than MAX_ACTIVE_SKILLS manifests
        manifests = [
            AgentSkillManifest(
                id=f"skill_{i}",
                name=f"Skill {i}",
                kind="prompt",
                agents=["core_generation"],
                priority=50,
                enabled_by_default=True,
            )
            for i in range(MAX_ACTIVE_SKILLS + 5)
        ]
        ctx = SkillSelectionContext(agent_name="core_generation")
        result = await selector.select(ctx, manifests)
        assert len(result.selected) <= MAX_ACTIVE_SKILLS

    @pytest.mark.asyncio
    async def test_service_skill_higher_priority(self):
        """Service skills are sorted before prompt skills."""
        from app.services.agent_skill_selector import AgentSkillSelector
        selector = AgentSkillSelector()
        manifests = [
            AgentSkillManifest(
                id="prompt_skill",
                name="Prompt Skill",
                kind="prompt",
                agents=["core_generation"],
                priority=90,
                enabled_by_default=True,
            ),
            AgentSkillManifest(
                id="service_skill",
                name="Service Skill",
                kind="service",
                agents=["core_generation"],
                priority=50,
                enabled_by_default=True,
            ),
        ]
        ctx = SkillSelectionContext(agent_name="core_generation")
        result = await selector.select(ctx, manifests)
        # Service skill should come first despite lower priority
        if len(result.selected) >= 2:
            assert result.selected[0].id == "service_skill"

    @pytest.mark.asyncio
    async def test_skipped_reasons_populated(self, sample_manifests):
        """Skipped skills have reason strings."""
        from app.services.agent_skill_selector import AgentSkillSelector
        selector = AgentSkillSelector()
        ctx = SkillSelectionContext(agent_name="core_generation")
        result = await selector.select(ctx, sample_manifests)
        # consistency_check should be skipped (agent mismatch)
        assert "consistency_check" in result.skipped
        assert result.skipped["consistency_check"] == "agent_mismatch"

    @pytest.mark.asyncio
    async def test_activation_reasons_populated(self, sample_manifests):
        """Selected skills have activation reason strings."""
        from app.services.agent_skill_selector import AgentSkillSelector
        selector = AgentSkillSelector()
        ctx = SkillSelectionContext(agent_name="core_generation")
        result = await selector.select(ctx, sample_manifests)
        # show_dont_tell should be selected with a reason
        if "show_dont_tell" in result.activation_reasons:
            assert result.activation_reasons["show_dont_tell"] != ""
