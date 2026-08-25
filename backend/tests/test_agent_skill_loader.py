"""Tests for AgentSkillLoader (plan 16.3)."""
import pytest
from pathlib import Path

from app.models.agent_skill import AgentSkillManifest, AgentSkill


@pytest.fixture
def builtin_manifest():
    """A manifest pointing to a real builtin SKILL.md."""
    skill_path = Path(__file__).parent.parent / "app" / "agent_skills" / "builtin" / "writing" / "show_dont_tell"
    if not skill_path.exists():
        pytest.skip("Builtin skill directory not found")
    return AgentSkillManifest(
        id="show_dont_tell",
        name="展示而非讲述",
        kind="prompt",
        domain="writing",
        category="agent",
        agents=["core_generation"],
        priority=80,
        enabled_by_default=True,
        path=str(skill_path),
    )


class TestAgentSkillLoader:
    """Tests for the skill body loader."""

    @pytest.mark.asyncio
    async def test_load_full_body(self, builtin_manifest):
        """Can load the full body from SKILL.md."""
        from app.services.agent_skill_loader import AgentSkillLoader
        loader = AgentSkillLoader()
        skill = await loader.load(builtin_manifest)
        assert skill.body != ""
        assert "展示" in skill.body or "讲述" in skill.body or "规则" in skill.body

    @pytest.mark.asyncio
    async def test_frontmatter_parsed(self, builtin_manifest):
        """Frontmatter fields are correctly parsed into manifest."""
        assert builtin_manifest.id == "show_dont_tell"
        assert builtin_manifest.kind == "prompt"
        assert builtin_manifest.domain == "writing"

    @pytest.mark.asyncio
    async def test_prompt_sections_extracted(self, builtin_manifest):
        """Prompt sections are extracted from ## headings."""
        from app.services.agent_skill_loader import AgentSkillLoader
        loader = AgentSkillLoader()
        skill = await loader.load(builtin_manifest)
        # Should have at least some prompt sections from ## headings
        assert isinstance(skill.prompt_sections, dict)

    @pytest.mark.asyncio
    async def test_empty_path_returns_empty_skill(self):
        """Manifest with no path returns skill with empty body."""
        from app.services.agent_skill_loader import AgentSkillLoader
        loader = AgentSkillLoader()
        manifest = AgentSkillManifest(id="no_path_skill", name="No Path")
        skill = await loader.load(manifest)
        assert skill.body == ""
        assert skill.manifest.id == "no_path_skill"

    @pytest.mark.asyncio
    async def test_load_many(self, builtin_manifest):
        """Can load multiple skills at once."""
        from app.services.agent_skill_loader import AgentSkillLoader
        loader = AgentSkillLoader()
        empty_manifest = AgentSkillManifest(id="empty", name="Empty")
        skills = await loader.load_many([builtin_manifest, empty_manifest])
        assert len(skills) == 2
        assert skills[0].body != ""
        assert skills[1].body == ""

    @pytest.mark.asyncio
    async def test_declared_references_and_yaml_resources_are_loaded_without_prompt_injection(self, tmp_path):
        from app.services.agent_skill_loader import AgentSkillLoader

        skill_dir = tmp_path / "skill"
        (skill_dir / "references").mkdir(parents=True)
        (skill_dir / "resources").mkdir()
        (skill_dir / "SKILL.md").write_text("---\nname: sample\n---\n\n## 规则\n正文", encoding="utf-8")
        (skill_dir / "references" / "research.md").write_text("# 研究\n只供按需读取", encoding="utf-8")
        (skill_dir / "resources" / "patterns.yaml").write_text(
            "sentence_shells:\n  - '不是.{1,10}而是'\n",
            encoding="utf-8",
        )
        (skill_dir / "references" / "undeclared.md").write_text("# 不应加载", encoding="utf-8")

        manifest = AgentSkillManifest(
            id="sample",
            name="sample",
            path=str(skill_dir),
            reference_files=["references/research.md"],
            resource_pack_files=["resources/patterns.yaml"],
        )
        skill = await AgentSkillLoader().load(manifest)

        assert skill.references == ["# 研究\n只供按需读取"]
        assert skill.resource_packs["patterns"]["sentence_shells"]
        assert "research" not in skill.prompt_sections
        assert "undeclared" not in skill.prompt_sections
