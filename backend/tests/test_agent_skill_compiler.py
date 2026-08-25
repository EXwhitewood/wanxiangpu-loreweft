"""Tests for AgentSkillCompiler (plan 16.5)."""
import pytest

from app.models.agent_skill import (
    AgentSkill,
    AgentSkillManifest,
    AgentSkill,
    CompiledSkillPacket,
    SkillRuntimeBinding,
)


@pytest.fixture
def prompt_skill():
    return AgentSkill(
        manifest=AgentSkillManifest(
            id="show_dont_tell",
            name="展示而非讲述",
            description="Force text to convey emotions through scene details",
            kind="prompt",
            domain="writing",
            category="agent",
            agents=["core_generation"],
            priority=80,
            enabled_by_default=True,
            version="2",
            validators=["ai_flavor"],
            constraints={"ai_flavor": {"dash_per_1000_max": 2.5}},
            repair_hooks=["prose_repair"],
        ),
        body="禁止情感标签，必须通过行为、对话、身体反应来展示。",
        prompt_sections={
            "规则": "1. 禁止情感标签\n2. 场景证据原则",
            "must_avoid": "- 他感到\n- 她很\n- 令人",
        },
    )


@pytest.fixture
def service_skill():
    return AgentSkill(
        manifest=AgentSkillManifest(
            id="scene_preparation",
            name="场景准备",
            description="Prepare scene context",
            kind="service",
            domain="generation",
            category="agent",
            agents=["core_generation"],
            priority=90,
            enabled_by_default=True,
            runtime=SkillRuntimeBinding(
                kind="service",
                service_class="app.skills.scene_preparation.ScenePreparationSkill",
                service_method="prepare",
                execution_phase="pre_generation",
            ),
        ),
        body="",
        prompt_sections={},
    )


@pytest.fixture
def tool_skill():
    return AgentSkill(
        manifest=AgentSkillManifest(
            id="shell_search",
            name="Shell搜索",
            description="Search the shell",
            kind="tool",
            domain="generation",
            category="agent",
            agents=["core_generation"],
            priority=70,
            enabled_by_default=True,
            runtime=SkillRuntimeBinding(
                kind="tool",
                tool_names=["shell_search"],
                min_permission="readonly",
            ),
        ),
        body="",
        prompt_sections={},
    )


@pytest.fixture
def validator_skill():
    return AgentSkill(
        manifest=AgentSkillManifest(
            id="literary_quality",
            name="文学质量",
            description="Validate literary quality",
            kind="validator",
            domain="writing",
            category="agent",
            agents=["core_generation"],
            priority=60,
            enabled_by_default=True,
            validators=["literary_quality", "ai_flavor"],
        ),
        body="",
        prompt_sections={},
    )


class TestAgentSkillCompiler:
    """Tests for the skill compiler."""

    @pytest.mark.asyncio
    async def test_prompt_skill_compiled_to_system_sections(self, prompt_skill):
        """Prompt skill body goes into system_sections."""
        from app.services.agent_skill_compiler import AgentSkillCompiler
        compiler = AgentSkillCompiler()
        packet = await compiler.compile(
            agent_name="core_generation",
            context={},
            selected_skills=[prompt_skill],
        )
        assert len(packet.system_sections) > 0
        assert "展示而非讲述" in packet.system_sections[0]

    @pytest.mark.asyncio
    async def test_prompt_sections_go_to_user_sections(self, prompt_skill):
        """Prompt sections from skill go into user_sections."""
        from app.services.agent_skill_compiler import AgentSkillCompiler
        compiler = AgentSkillCompiler()
        packet = await compiler.compile(
            agent_name="core_generation",
            context={},
            selected_skills=[prompt_skill],
        )
        assert len(packet.user_sections) > 0
        # Should contain the section content
        user_text = "\n".join(packet.user_sections)
        assert "规则" in user_text

    @pytest.mark.asyncio
    async def test_service_skill_compiled_to_execution_plan(self, service_skill):
        """Service skill goes into execution_plan."""
        from app.services.agent_skill_compiler import AgentSkillCompiler
        compiler = AgentSkillCompiler()
        packet = await compiler.compile(
            agent_name="core_generation",
            context={},
            selected_skills=[service_skill],
        )
        assert len(packet.execution_plan) > 0
        step = packet.execution_plan[0]
        assert step["skill_id"] == "scene_preparation"
        assert step["kind"] == "service"
        assert step["service_class"] == "app.skills.scene_preparation.ScenePreparationSkill"
        assert step["service_method"] == "prepare"
        assert step["execution_phase"] == "pre_generation"

    @pytest.mark.asyncio
    async def test_tool_skill_compiled_to_tool_permissions(self, tool_skill):
        """Tool skill goes into tool_permissions and execution_plan."""
        from app.services.agent_skill_compiler import AgentSkillCompiler
        compiler = AgentSkillCompiler()
        packet = await compiler.compile(
            agent_name="core_generation",
            context={},
            selected_skills=[tool_skill],
        )
        assert "shell_search" in packet.tool_permissions
        assert packet.tool_permissions["shell_search"] == "readonly"
        assert len(packet.execution_plan) > 0
        assert packet.execution_plan[0]["min_permission"] == "readonly"

    @pytest.mark.asyncio
    async def test_validator_skill_compiled_to_validators(self, validator_skill):
        """Validator skill goes into validators list."""
        from app.services.agent_skill_compiler import AgentSkillCompiler
        compiler = AgentSkillCompiler()
        packet = await compiler.compile(
            agent_name="core_generation",
            context={},
            selected_skills=[validator_skill],
        )
        assert "literary_quality" in packet.validators
        assert "ai_flavor" in packet.validators

    @pytest.mark.asyncio
    async def test_must_avoid_extracted(self, prompt_skill):
        """must_avoid section is extracted into packet.must_avoid."""
        from app.services.agent_skill_compiler import AgentSkillCompiler
        compiler = AgentSkillCompiler()
        packet = await compiler.compile(
            agent_name="core_generation",
            context={},
            selected_skills=[prompt_skill],
        )
        assert len(packet.must_avoid) > 0
        assert "他感到" in packet.must_avoid

    @pytest.mark.asyncio
    async def test_active_skills_list_populated(self, prompt_skill, service_skill):
        """active_skills list contains all selected skill IDs."""
        from app.services.agent_skill_compiler import AgentSkillCompiler
        compiler = AgentSkillCompiler()
        packet = await compiler.compile(
            agent_name="core_generation",
            context={},
            selected_skills=[prompt_skill, service_skill],
        )
        assert "show_dont_tell" in packet.active_skills
        assert "scene_preparation" in packet.active_skills

    @pytest.mark.asyncio
    async def test_explicit_validation_contracts_and_context_are_compiled(self):
        from app.services.agent_skill_compiler import AgentSkillCompiler

        skill = AgentSkill(
            manifest=AgentSkillManifest(
                id="scene_crafting",
                name="scene-crafting",
                description="Scene craft protocol",
                kind="prompt",
                domain="writing",
                validators=["scene_structure"],
                validation_contracts={
                    "scene_structure": {
                        "goal_present": "required",
                        "conflict_present": "required",
                    }
                },
                repair_hooks=["scene_restructure"],
                runtime=SkillRuntimeBinding(
                    kind="prompt",
                    required_context=["scene_contract", "chapter_outline"],
                    optional_context=["foreshadowing_map"],
                ),
            ),
            body="Scene crafting protocol",
        )

        packet = await AgentSkillCompiler().compile(
            agent_name="core_generation",
            context={},
            selected_skills=[skill],
        )

        assert packet.validation_contracts["scene_structure"]["scene_crafting"] == {
            "goal_present": "required",
            "conflict_present": "required",
        }
        assert packet.required_context["scene_crafting"] == [
            "scene_contract",
            "chapter_outline",
        ]
        assert packet.optional_context["scene_crafting"] == ["foreshadowing_map"]

    @pytest.mark.asyncio
    async def test_empty_skills_produces_empty_packet(self):
        """No skills selected produces an empty packet."""
        from app.services.agent_skill_compiler import AgentSkillCompiler
        compiler = AgentSkillCompiler()
        packet = await compiler.compile(
            agent_name="core_generation",
            context={},
            selected_skills=[],
        )
        assert packet.active_skills == []
        assert packet.system_sections == []
        assert packet.user_sections == []
        assert packet.execution_plan == []
        assert packet.validators == []
        assert packet.must_avoid == []

    @pytest.mark.asyncio
    async def test_render_system_section_format(self, prompt_skill):
        """System section has correct header format."""
        from app.services.agent_skill_compiler import AgentSkillCompiler
        compiler = AgentSkillCompiler()
        packet = await compiler.compile(
            agent_name="core_generation",
            context={},
            selected_skills=[prompt_skill],
        )
        section = packet.system_sections[0]
        assert "### Skill: 展示而非讲述 (v2)" in section

    @pytest.mark.asyncio
    async def test_trace_populated(self, prompt_skill):
        """Trace dict contains summary counts."""
        from app.services.agent_skill_compiler import AgentSkillCompiler
        compiler = AgentSkillCompiler()
        packet = await compiler.compile(
            agent_name="core_generation",
            context={},
            selected_skills=[prompt_skill],
        )
        assert packet.trace["total_selected"] == 1
        assert packet.trace["total_system_sections"] >= 1

    @pytest.mark.asyncio
    async def test_validators_deduplicated(self, validator_skill):
        """Validators list is deduplicated."""
        from app.services.agent_skill_compiler import AgentSkillCompiler
        compiler = AgentSkillCompiler()
        packet = await compiler.compile(
            agent_name="core_generation",
            context={},
            selected_skills=[validator_skill],
        )
        # literary_quality appears both as skill id and in validators list
        # deduplication should ensure no duplicates
        assert len(packet.validators) == len(set(packet.validators))

    @pytest.mark.asyncio
    async def test_constraints_and_repair_hooks_compiled(self, prompt_skill):
        """Skill-owned validation contracts are preserved in the compiled packet."""
        from app.services.agent_skill_compiler import AgentSkillCompiler
        compiler = AgentSkillCompiler()
        packet = await compiler.compile(
            agent_name="core_generation",
            context={},
            selected_skills=[prompt_skill],
        )
        assert packet.validation_contracts["ai_flavor"]["show_dont_tell"]["dash_per_1000_max"] == 2.5
        assert packet.repair_hooks["ai_flavor"] == ["prose_repair"]

    @pytest.mark.asyncio
    async def test_repair_strategies_do_not_become_executable_hooks(self):
        from app.services.agent_skill_compiler import AgentSkillCompiler

        skill = AgentSkill(
            manifest=AgentSkillManifest(
                id="strategy_only",
                name="strategy_only",
                validators=["ai_flavor"],
                validation_contracts={"ai_flavor": {"dash_per_1000_max": 2.5}},
                repair_hooks=["prose_repair"],
                repair_strategies=["reduce_dash", "vary_sentence_length"],
            ),
        )
        packet = await AgentSkillCompiler().compile(
            agent_name="core_generation",
            context={},
            selected_skills=[skill],
        )

        assert packet.repair_hooks["ai_flavor"] == ["prose_repair"]

    @pytest.mark.asyncio
    async def test_resource_packs_are_compiled_but_references_are_not_injected(self):
        from app.services.agent_skill_compiler import AgentSkillCompiler

        manifest = AgentSkillManifest(
            id="anti_ai_prose",
            name="anti-ai-prose",
            kind="prompt",
            validators=["ai_discourse"],
        )
        skill = AgentSkill(
            manifest=manifest,
            body="核心协议",
            references=["外部研究正文不应自动注入"],
            resource_packs={"discourse_patterns": {"sentence_shells": ["不是.+而是"]}},
        )

        packet = await AgentSkillCompiler().compile(
            agent_name="core_generation",
            context={},
            selected_skills=[skill],
        )

        assert packet.resource_packs["anti_ai_prose"]["discourse_patterns"]["sentence_shells"]
        assert "外部研究正文不应自动注入" not in packet.render_system()
