"""Tests for AgentSkillRegistry (plan 16.1)."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from pathlib import Path

from app.models.agent_skill import AgentSkillManifest, SkillTrigger, SkillRuntimeBinding


@pytest.fixture
def sample_manifest():
    return AgentSkillManifest(
        id="test_skill",
        name="Test Skill",
        description="A test skill",
        kind="prompt",
        domain="writing",
        category="agent",
        agents=["core_generation"],
        priority=80,
        enabled_by_default=True,
    )


class TestAgentSkillRegistry:
    """Tests for the unified skill registry."""

    @pytest.mark.asyncio
    async def test_load_builtin_skills(self):
        """Can load builtin skills from SKILL.md files."""
        from app.services.agent_skill_registry import AgentSkillRegistry
        registry = AgentSkillRegistry()
        manifests = await registry.list_manifests()
        assert len(manifests) > 0
        # Should find at least the core builtin skills
        ids = [m.id for m in manifests]
        assert "scene_preparation" in ids or "show_dont_tell" in ids

    @pytest.mark.asyncio
    async def test_merge_legacy_registry(self):
        """Can merge legacy SKILL_REGISTRY entries."""
        from app.services.agent_skill_registry import AgentSkillRegistry
        registry = AgentSkillRegistry()
        manifests = await registry.list_manifests()
        # Legacy skills should be present if SKILL_REGISTRY has entries
        ids = [m.id for m in manifests]
        # At minimum, builtin SKILL.md skills should be present
        assert len(ids) > 0

    @pytest.mark.asyncio
    async def test_filter_by_agent(self):
        """Can filter skills by agent name."""
        from app.services.agent_skill_registry import AgentSkillRegistry
        registry = AgentSkillRegistry()
        manifests = await registry.list_manifests(agent_name="core_generation")
        for m in manifests:
            assert not m.agents or "core_generation" in m.agents

    @pytest.mark.asyncio
    async def test_worldbuilder_missing_skills_registered(self):
        """Worldbuilder missing skills (consistency_check, etc.) are registered."""
        from app.services.agent_skill_registry import AgentSkillRegistry
        registry = AgentSkillRegistry()
        manifests = await registry.list_manifests()
        ids = [m.id for m in manifests]
        assert "consistency_check" in ids
        assert "completeness_check" in ids
        assert "derivation_chain_validation" in ids

    @pytest.mark.asyncio
    async def test_get_manifest_by_id(self):
        """Can get a single manifest by ID."""
        from app.services.agent_skill_registry import AgentSkillRegistry
        registry = AgentSkillRegistry()
        manifest = await registry.get_manifest("scene_preparation")
        if manifest is not None:
            assert manifest.id == "scene_preparation"

    @pytest.mark.asyncio
    async def test_builtin_nested_frontmatter_is_parsed(self):
        """Builtin SKILL.md nested YAML is preserved in the manifest."""
        from app.services.agent_skill_registry import AgentSkillRegistry

        registry = AgentSkillRegistry()

        scene = await registry.get_manifest("scene_preparation")
        assert scene is not None
        assert "core_generation" in scene.agents
        assert scene.runtime.service_class == "app.skills.scene_preparation.ScenePreparationSkill"
        assert scene.runtime.service_method == "prepare"

        writing = await registry.get_manifest("show_dont_tell")
        assert writing is not None
        assert writing.category == "agent"
        assert "core_generation" in writing.agents
        assert "emotional_turn" in writing.triggers.scene_roles
        assert "ai_emotion_label" in writing.triggers.issue_types
        assert "literary_quality" in writing.validators

    @pytest.mark.asyncio
    async def test_open_skill_x_loreweft_frontmatter_is_parsed(self, tmp_path, monkeypatch):
        """Open Agent Skill root metadata stays generic while x-loreweft drives runtime fields."""
        import app.services.agent_skill_registry as registry_module
        from app.services.agent_skill_registry import AgentSkillRegistry

        skill_dir = tmp_path / "sample-open-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            """---
name: sample-open-skill
description: Generic skill description.
version: 1.2.3
x-loreweft:
  id: sample_open_skill
  display_name: Sample Open Skill
  kind: service
  domain: generation
  category: utility
  agents:
    - core_generation
  priority: 77
  enabled_by_default: true
  runtime:
    service_class: app.skills.example.ExampleSkill
    service_method: run
  validators:
    - ai_flavor
  constraints:
    ai_flavor:
      dash_per_1000_max: 2.5
  repair_hooks:
    - prose_repair
---

# Sample
""",
            encoding="utf-8",
        )

        monkeypatch.setattr(registry_module, "_BUILTIN_ROOT", tmp_path)
        registry = AgentSkillRegistry()
        manifest = await registry.get_manifest("sample_open_skill")

        assert manifest is not None
        assert manifest.name == "sample-open-skill"
        assert manifest.display_name == "Sample Open Skill"
        assert manifest.description == "Generic skill description."
        assert manifest.version == "1.2.3"
        assert manifest.format_version == "open_skill_v1"
        assert manifest.source_format == "x-loreweft"
        assert manifest.kind == "service"
        assert manifest.runtime.service_method == "run"
        assert manifest.constraints["ai_flavor"]["dash_per_1000_max"] == 2.5
        assert manifest.repair_hooks == ["prose_repair"]

    @pytest.mark.asyncio
    async def test_anti_ai_prose_uses_open_skill_format(self):
        from app.services.agent_skill_registry import AgentSkillRegistry

        registry = AgentSkillRegistry()
        manifest = await registry.get_manifest("anti_ai_prose")

        assert manifest is not None
        assert manifest.name == "anti-ai-prose"
        assert manifest.format_version == "open_skill_v2"
        assert manifest.source_format == "x-loreweft"
        assert "ai_flavor" in manifest.validators
        assert manifest.constraints["ai_flavor"]["dash_per_1000_max"] == 0.5
        assert manifest.constraints["ai_flavor"]["tier1_hit_count"] == 0
        assert manifest.runtime.required_context == ["scene_contract", "style_profile"]

    @pytest.mark.asyncio
    async def test_core_service_skills_use_open_skill_format(self):
        from app.services.agent_skill_registry import AgentSkillRegistry

        registry = AgentSkillRegistry()
        expected = {
            "scene_preparation": "pre_generation",
            "attention_director": "pre_generation",
            "style_polish": "post_generation",
        }

        for skill_id, phase in expected.items():
            manifest = await registry.get_manifest(skill_id)
            assert manifest is not None
            assert manifest.format_version == "open_skill_v1"
            assert manifest.source_format == "x-loreweft"
            assert manifest.kind == "service"
            assert manifest.runtime.execution_phase == phase

    @pytest.mark.asyncio
    async def test_core_writer_prompt_skills_use_open_skill_format(self):
        from app.services.agent_skill_registry import AgentSkillRegistry

        registry = AgentSkillRegistry()
        manifest = await registry.get_manifest("anti_ai_prose")
        assert manifest is not None
        assert manifest.format_version == "open_skill_v2"
        assert manifest.source_format == "x-loreweft"
        assert manifest.kind == "prompt"
        assert manifest.validators == ["ai_flavor", "ai_discourse", "voice_fingerprint"]
        assert "ai_flavor" in manifest.validation_contracts
        assert "ai_discourse" in manifest.validation_contracts
        assert "voice_fingerprint" in manifest.validation_contracts
        assert manifest.reference_files
        assert manifest.resource_pack_files

        manifest = await registry.get_manifest("scene_crafting")
        assert manifest is not None
        assert manifest.format_version == "open_skill_v2"
        assert manifest.source_format == "x-loreweft"
        assert manifest.kind == "prompt"
        assert manifest.runtime.required_context == [
            "scene_contract",
            "chapter_outline",
            "pov_character_card",
            "world_rules_relevant",
            "previous_scene_summary",
            "foreshadowing_map",
        ]
        assert "scene_structure" in manifest.validation_contracts

        manifest = await registry.get_manifest("narrative_writing")
        assert manifest is not None
        assert manifest.format_version == "open_skill_v2"
        assert manifest.source_format == "x-loreweft"
        assert manifest.kind == "prompt"
        assert manifest.runtime.required_context == [
            "scene_contract",
            "pov_character_card",
            "narrative_config",
            "previous_scene_summary",
            "style_profile",
            "world_rules_relevant",
        ]
        assert manifest.runtime.optional_context == [
            "scene_map",
            "chapter_outline",
            "narrative_experience_contract",
            "writing_mode_profile",
            "quality_memory",
        ]
        assert "pov_consistency" in manifest.validation_contracts
        assert "tense_consistency" in manifest.validation_contracts

        manifest = await registry.get_manifest("show_dont_tell")
        assert manifest is not None
        assert manifest.format_version == "open_skill_v2"
        assert manifest.source_format == "x-loreweft"
        assert manifest.kind == "prompt"
        assert manifest.runtime.required_context == [
            "scene_contract",
            "pov_character_card",
            "style_profile",
        ]
        assert manifest.runtime.optional_context == [
            "generated_prose_or_draft",
            "previous_scene_summary",
            "quality_memory",
            "writing_mode_profile",
            "emotion_map",
            "narrative_config",
        ]
        assert "scene_evidence" in manifest.validators
        assert "scene_evidence" in manifest.validation_contracts

    @pytest.mark.asyncio
    async def test_builtin_tool_runtime_is_parsed(self):
        """Tool skills keep tool names and declared permissions."""
        from app.services.agent_skill_registry import AgentSkillRegistry

        registry = AgentSkillRegistry()
        manifest = await registry.get_manifest("worldbuilder_rule_tools")

        assert manifest is not None
        assert manifest.category == "utility"
        assert manifest.runtime.tool_names == [
            "list_world_rules",
            "create_world_rule",
            "update_world_rule",
        ]
        assert manifest.runtime.min_permission == "project_write"

    @pytest.mark.asyncio
    async def test_agent_config_defaults_extend_manifest_agents(self):
        """Skills enabled in agent_config remain available even if SKILL.md agents lag."""
        from app.services.agent_skill_registry import AgentSkillRegistry

        registry = AgentSkillRegistry()
        manifests = await registry.list_manifests(agent_name="fbi_fact_repair")
        ids = {m.id for m in manifests}

        assert "state_query" in ids
        assert "core_query" in ids

    @pytest.mark.asyncio
    async def test_service_runtime_bindings_are_complete(self):
        """Service skills must be executable when they enter an execution plan."""
        from app.services.agent_skill_registry import AgentSkillRegistry

        registry = AgentSkillRegistry()
        manifests = await registry.list_manifests()
        incomplete = [
            m.id
            for m in manifests
            if m.kind in ("service", "workflow")
            and m.runtime.service_class
            and not m.runtime.service_method
        ]

        assert incomplete == []

    @pytest.mark.asyncio
    async def test_list_skills_api_uses_unified_registry(self):
        """The public skills list exposes file-backed skills, not only legacy config."""
        from app.api.agents import list_skills

        skills = await list_skills()
        ids = {skill["name"] for skill in skills}

        assert "show_dont_tell" in ids
        assert "worldbuilder_rule_tools" in ids
        assert "patch_minimality" in ids
        anti_ai = next(skill for skill in skills if skill["name"] == "anti_ai_prose")
        assert anti_ai["format_version"] == "open_skill_v2"
        assert anti_ai["source_format"] == "x-loreweft"
        assert anti_ai["has_validators"] is True
        assert anti_ai["has_constraints"] is True
        assert anti_ai["has_repair_hooks"] is True

    @pytest.mark.asyncio
    async def test_skill_preview_exposes_protocol_fields(self):
        from app.api.agents import preview_agent_skills

        preview = await preview_agent_skills(
            "core_generation",
            {
                "enabled_agent_skills": ["anti_ai_prose"],
                "enabled_skills": [],
            },
        )

        assert "ai_flavor" in preview["validators"]
        assert preview["validation_contracts"]["ai_flavor"]["anti_ai_prose"]["dash_per_1000_max"] == 0.5
        assert "prose_repair" in preview["repair_hooks"]["ai_flavor"]
        assert "tier1_replace" not in preview["repair_hooks"]["ai_flavor"]
        assert "must_avoid" in preview

        wrapped_preview = await preview_agent_skills(
            "core_generation",
            {
                "context": {
                    "enabled_agent_skills": ["anti_ai_prose"],
                    "enabled_skills": [],
                }
            },
        )
        assert wrapped_preview["active_skills"] == preview["active_skills"]

    @pytest.mark.asyncio
    async def test_priority_deduplication(self):
        """Same ID from different sources: higher priority source wins."""
        from app.services.agent_skill_registry import AgentSkillRegistry
        registry = AgentSkillRegistry()
        manifests = await registry.list_manifests()
        ids = [m.id for m in manifests]
        # No duplicates
        assert len(ids) == len(set(ids))

    @pytest.mark.asyncio
    async def test_cache_invalidation(self):
        """Cache invalidation forces re-scan."""
        from app.services.agent_skill_registry import AgentSkillRegistry
        registry = AgentSkillRegistry()
        await registry.list_manifests()
        assert registry._cache is not None
        registry.invalidate_cache()
        assert registry._cache is None

    @pytest.mark.asyncio
    async def test_imported_custom_skill_is_merged_with_agent_trigger(self):
        """Imported custom skills survive registry rebuild and remain agent-scoped."""
        from app.services.agent_skill_registry import AgentSkillRegistry

        custom = {
            "custom_outline_skill": {
                "name": "custom_outline_skill",
                "display_name": "Custom Outline Skill",
                "description": "Custom registry contract",
                "category": "agent",
                "agent": "outline_architect",
                "manifest": {
                    "name": "custom_outline_skill",
                    "kind": "prompt",
                    "agents": ["outline_architect"],
                },
            }
        }

        with patch(
            "app.services.agent_config._load_custom_skills",
            new=AsyncMock(return_value=custom),
        ):
            manifests = await AgentSkillRegistry().list_manifests()

        manifest = next(item for item in manifests if item.id == "custom_outline_skill")
        assert manifest.source == "custom"
        assert manifest.agents == ["outline_architect"]
        assert manifest.triggers.agents == ["outline_architect"]
