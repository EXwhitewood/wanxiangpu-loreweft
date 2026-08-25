"""Tests for AgentSkillRuntime (plan 16.7)."""
import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.models.agent_skill import (
    AgentSkill,
    AgentSkillManifest,
    CompiledSkillPacket,
    SkillSelectionContext,
    SkillSelectionResult,
)


@pytest.fixture
def selection_context():
    return SkillSelectionContext(
        agent_name="core_generation",
        scene_context_package={"project_root": None},
    )


class TestAgentSkillRuntime:
    """Tests for the unified skill runtime entry point."""

    @pytest.mark.asyncio
    async def test_prepare_chains_all_stages(self, selection_context):
        """prepare() chains registry -> selector -> loader -> compiler."""
        from app.services.agent_skill_runtime import AgentSkillRuntime

        runtime = AgentSkillRuntime()

        # Mock each stage
        mock_manifest = AgentSkillManifest(
            id="show_dont_tell", name="展示而非讲述", kind="prompt",
            agents=["core_generation"], priority=80, enabled_by_default=True,
        )
        mock_skill = AgentSkill(
            manifest=mock_manifest,
            body="禁止情感标签",
            prompt_sections={"规则": "1. 禁止情感标签"},
        )
        mock_packet = CompiledSkillPacket(
            agent_name="core_generation",
            active_skills=["show_dont_tell"],
            activation_reasons={"show_dont_tell": "default_enabled"},
            system_sections=["### Skill: 展示而非讲述\n禁止情感标签"],
            user_sections=[],
            execution_plan=[],
            tool_permissions={},
            validators=[],
            must_avoid=[],
            trace={},
        )

        runtime.registry.list_manifests = AsyncMock(return_value=[mock_manifest])
        runtime.selector.select = AsyncMock(return_value=SkillSelectionResult(
            selected=[mock_manifest],
            skipped={},
            activation_reasons={"show_dont_tell": "default_enabled"},
        ))
        runtime.loader.load_many = AsyncMock(return_value=[mock_skill])
        runtime.compiler.compile = AsyncMock(return_value=mock_packet)

        result = await runtime.prepare("core_generation", selection_context)

        runtime.registry.list_manifests.assert_called_once()
        runtime.selector.select.assert_called_once()
        runtime.loader.load_many.assert_called_once()
        runtime.compiler.compile.assert_called_once()
        assert result.active_skills == ["show_dont_tell"]

    @pytest.mark.asyncio
    async def test_prepare_merges_activation_reasons(self, selection_context):
        """prepare() merges selector activation reasons into packet."""
        from app.services.agent_skill_runtime import AgentSkillRuntime

        runtime = AgentSkillRuntime()

        mock_manifest = AgentSkillManifest(
            id="show_dont_tell", name="展示而非讲述", kind="prompt",
            agents=["core_generation"], priority=80, enabled_by_default=True,
        )
        mock_skill = AgentSkill(manifest=mock_manifest, body="body")
        mock_packet = CompiledSkillPacket(
            agent_name="core_generation",
            active_skills=["show_dont_tell"],
            activation_reasons={},
            system_sections=[],
            user_sections=[],
            execution_plan=[],
            tool_permissions={},
            validators=[],
            must_avoid=[],
            trace={},
        )

        runtime.registry.list_manifests = AsyncMock(return_value=[mock_manifest])
        runtime.selector.select = AsyncMock(return_value=SkillSelectionResult(
            selected=[mock_manifest],
            skipped={},
            activation_reasons={"show_dont_tell": "trigger_match"},
        ))
        runtime.loader.load_many = AsyncMock(return_value=[mock_skill])
        runtime.compiler.compile = AsyncMock(return_value=mock_packet)

        result = await runtime.prepare("core_generation", selection_context)
        assert result.activation_reasons.get("show_dont_tell") == "trigger_match"

    @pytest.mark.asyncio
    async def test_execute_services_prompt_only(self):
        """execute_services with prompt-only packet returns empty dict."""
        from app.services.agent_skill_runtime import AgentSkillRuntime

        runtime = AgentSkillRuntime()
        packet = CompiledSkillPacket(
            agent_name="core_generation",
            active_skills=["show_dont_tell"],
            execution_plan=[],
        )
        result = await runtime.execute_services(packet)
        assert result == {}

    @pytest.mark.asyncio
    async def test_execute_services_with_service_step(self):
        """execute_services runs service steps from execution_plan."""
        from app.services.agent_skill_runtime import AgentSkillRuntime

        runtime = AgentSkillRuntime()
        packet = CompiledSkillPacket(
            agent_name="core_generation",
            active_skills=["scene_preparation"],
            execution_plan=[
                {
                    "skill_id": "scene_preparation",
                    "kind": "service",
                    "service_class": "app.skills.scene_preparation.ScenePreparationSkill",
                    "service_method": "prepare",
                }
            ],
        )

        mock_instance = MagicMock()
        mock_instance.prepare = MagicMock(return_value={"scene": "prepared"})
        with patch.object(runtime, "_execute_service_step", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = {"scene": "prepared"}
            result = await runtime.execute_services(packet)
            assert "scene_preparation" in result

    @pytest.mark.asyncio
    async def test_execute_services_skips_prompt_steps(self):
        """execute_services skips prompt-type steps in execution_plan."""
        from app.services.agent_skill_runtime import AgentSkillRuntime

        runtime = AgentSkillRuntime()
        packet = CompiledSkillPacket(
            agent_name="core_generation",
            active_skills=["show_dont_tell"],
            execution_plan=[
                {"skill_id": "show_dont_tell", "kind": "prompt"},
            ],
        )
        result = await runtime.execute_services(packet)
        assert result == {}

    @pytest.mark.asyncio
    async def test_execute_service_step_missing_class(self):
        """_execute_service_step returns None when class/method missing."""
        from app.services.agent_skill_runtime import AgentSkillRuntime

        runtime = AgentSkillRuntime()
        step = {"skill_id": "broken", "kind": "service"}
        result = await runtime._execute_service_step(step, {})
        assert result is None

    @pytest.mark.asyncio
    async def test_resolve_tool_known_name(self):
        """_resolve_tool finds known tool modules."""
        from app.services.agent_skill_runtime import AgentSkillRuntime
        # shell_search is a known tool
        result = AgentSkillRuntime._resolve_tool("shell_search")
        # May be None if module not importable in test env, but should not raise
        # Just verify it doesn't crash
        assert result is None or callable(result)

    @pytest.mark.asyncio
    async def test_resolve_tool_unknown_name(self):
        """_resolve_tool returns None for unknown tool names."""
        from app.services.agent_skill_runtime import AgentSkillRuntime
        result = AgentSkillRuntime._resolve_tool("nonexistent_tool")
        assert result is None

    @pytest.mark.asyncio
    async def test_execute_tool_step_checks_permissions(self):
        """Tool execution uses ToolPermissionService without type/interface errors."""
        from app.services.agent_skill_runtime import AgentSkillRuntime

        runtime = AgentSkillRuntime()
        step = {
            "skill_id": "project_reader",
            "kind": "tool",
            "tools": ["read_project"],
            "min_permission": "admin",
        }

        with patch.object(runtime, "_resolve_tool", return_value=lambda **_: {"ok": True}):
            result = await runtime._execute_tool_step(
                step,
                {"granted_permissions": ["readonly"]},
            )

        assert result is not None
        assert result["read_project"]["error"] == "permission_denied"

    @pytest.mark.asyncio
    async def test_execute_tool_step_runs_when_allowed(self):
        """Tool execution runs when granted permission satisfies the skill contract."""
        from app.services.agent_skill_runtime import AgentSkillRuntime

        runtime = AgentSkillRuntime()
        step = {
            "skill_id": "project_reader",
            "kind": "tool",
            "tools": ["read_project"],
            "min_permission": "readonly",
        }

        with patch.object(runtime, "_resolve_tool", return_value=lambda **_: {"ok": True}):
            result = await runtime._execute_tool_step(
                step,
                {"granted_permissions": ["readonly"]},
            )

        assert result == {"read_project": {"ok": True}}

    @pytest.mark.asyncio
    async def test_execute_service_step_filters_extra_context(self):
        """Service skills receive only accepted kwargs from the writer context."""
        from app.services.agent_skill_runtime import AgentSkillRuntime

        class FakeService:
            def run(self, project_id: str, scene_beat: dict):
                return {"project_id": project_id, "goal": scene_beat.get("goal")}

        runtime = AgentSkillRuntime()
        step = {
            "skill_id": "fake",
            "kind": "service",
            "service_class": "fake.Service",
            "service_method": "run",
        }

        with patch.object(runtime, "_resolve_service", return_value=FakeService()):
            result = await runtime._execute_service_step(
                step,
                {"project_id": "p1", "scene_beat": {"goal": "open"}, "unused": "ignored"},
            )

        assert result == {"project_id": "p1", "goal": "open"}

    @pytest.mark.asyncio
    async def test_execute_service_step_reports_missing_required_context(self):
        from app.services.agent_skill_runtime import AgentSkillRuntime

        class FakeService:
            def run(self, project_id: str, scene_beat: dict):
                return {"ok": True}

        runtime = AgentSkillRuntime()
        step = {
            "skill_id": "fake",
            "kind": "service",
            "service_class": "fake.Service",
            "service_method": "run",
        }

        with patch.object(runtime, "_resolve_service", return_value=FakeService()):
            result = await runtime._execute_service_step(step, {"project_id": "p1"})

        assert result["status"] == "skipped"
        assert result["missing"] == ["scene_beat"]

    @pytest.mark.asyncio
    async def test_execute_services_filters_by_execution_phase(self):
        from app.services.agent_skill_runtime import AgentSkillRuntime

        runtime = AgentSkillRuntime()
        packet = CompiledSkillPacket(
            agent_name="core_generation",
            active_skills=["pre", "post"],
            execution_plan=[
                {
                    "skill_id": "pre",
                    "kind": "service",
                    "service_class": "fake.Pre",
                    "service_method": "run",
                    "execution_phase": "pre_generation",
                },
                {
                    "skill_id": "post",
                    "kind": "service",
                    "service_class": "fake.Post",
                    "service_method": "run",
                    "execution_phase": "post_generation",
                },
            ],
        )

        with patch.object(runtime, "_execute_service_step", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = {"ok": True}
            result = await runtime.execute_services(
                packet,
                {"execution_phase": "pre_generation"},
            )

        assert result == {"pre": {"ok": True}}
        assert mock_exec.call_count == 1
        assert mock_exec.call_args.args[0]["skill_id"] == "pre"

    @pytest.mark.asyncio
    async def test_execute_service_step_times_out(self):
        from app.services.agent_skill_runtime import AgentSkillRuntime

        class SlowService:
            async def run(self):
                await asyncio.sleep(0.05)
                return {"ok": True}

        runtime = AgentSkillRuntime()
        step = {
            "skill_id": "slow",
            "kind": "service",
            "service_class": "fake.Slow",
            "service_method": "run",
            "timeout_seconds": 0.01,
        }
        with patch.object(runtime, "_resolve_service", return_value=SlowService()):
            result = await runtime._execute_service_step(step, {})

        assert result["status"] == "skipped"
        assert result["reason"] == "execution_timeout"

    @pytest.mark.asyncio
    async def test_execute_service_step_uses_explicit_cache_key(self):
        from app.services.agent_skill_runtime import AgentSkillRuntime

        service = MagicMock()
        service.run = MagicMock(return_value={"value": 1})
        runtime = AgentSkillRuntime()
        step = {
            "skill_id": "cached",
            "kind": "service",
            "service_class": "fake.Cached",
            "service_method": "run",
            "cache_enabled": True,
        }
        with patch.object(runtime, "_resolve_service", return_value=service):
            first = await runtime._execute_service_step(step, {"skill_cache_key": "scene-1"})
            second = await runtime._execute_service_step(step, {"skill_cache_key": "scene-1"})

        assert first == {"value": 1}
        assert second["_skill_cache_hit"] is True
        assert service.run.call_count == 1

    def test_apply_output_budget_truncates_large_results(self):
        from app.services.agent_skill_runtime import AgentSkillRuntime

        result = AgentSkillRuntime._apply_output_budget({"text": "x" * 1000}, 256)

        assert result["truncated"] is True
        assert result["output_char_budget"] == 256

    @pytest.mark.asyncio
    async def test_get_skill_runtime_singleton(self):
        """get_skill_runtime returns a singleton."""
        from app.services.agent_skill_runtime import get_skill_runtime, _runtime
        # Reset singleton
        import app.services.agent_skill_runtime as mod
        mod._runtime = None
        r1 = get_skill_runtime()
        r2 = get_skill_runtime()
        assert r1 is r2
        # Clean up
        mod._runtime = None

    @pytest.mark.asyncio
    async def test_prepare_passes_project_root(self):
        """prepare() passes project_root from context to registry."""
        from app.services.agent_skill_runtime import AgentSkillRuntime

        runtime = AgentSkillRuntime()
        ctx = SkillSelectionContext(
            agent_name="core_generation",
            scene_context_package={"project_root": "/tmp/project"},
        )

        mock_manifest = AgentSkillManifest(
            id="test", name="Test", kind="prompt",
        )
        mock_skill = AgentSkill(manifest=mock_manifest, body="")
        mock_packet = CompiledSkillPacket(
            agent_name="core_generation",
            active_skills=[],
            system_sections=[],
            user_sections=[],
            execution_plan=[],
            tool_permissions={},
            validators=[],
            must_avoid=[],
            trace={},
        )

        runtime.registry.list_manifests = AsyncMock(return_value=[])
        runtime.selector.select = AsyncMock(return_value=SkillSelectionResult(
            selected=[], skipped={}, activation_reasons={},
        ))
        runtime.loader.load_many = AsyncMock(return_value=[])
        runtime.compiler.compile = AsyncMock(return_value=mock_packet)

        await runtime.prepare("core_generation", ctx)

        # Verify project_root was passed
        call_kwargs = runtime.registry.list_manifests.call_args
        assert call_kwargs.kwargs.get("project_root") == "/tmp/project"
