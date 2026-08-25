"""AgentSkillRuntime - unified entry point for skill preparation and execution (plan 9.5)."""
from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import logging
from typing import Any

from app.models.agent_skill import (
    AgentSkill,
    AgentSkillManifest,
    CompiledSkillPacket,
    SkillSelectionContext,
)
from app.services.agent_skill_registry import AgentSkillRegistry
from app.services.agent_skill_loader import AgentSkillLoader
from app.services.agent_skill_selector import AgentSkillSelector
from app.services.agent_skill_compiler import AgentSkillCompiler

logger = logging.getLogger(__name__)


class AgentSkillRuntime:
    """Unified entry point for skill preparation and execution.

    The prepare method chains: registry.list_manifests -> selector.select ->
    loader.load_many -> compiler.compile

    The execute_services method runs service skills from the execution_plan
    by dynamically importing and calling the service class/method.
    """

    def __init__(self) -> None:
        self.registry = AgentSkillRegistry()
        self.loader = AgentSkillLoader()
        self.selector = AgentSkillSelector()
        self.compiler = AgentSkillCompiler()
        self._service_cache: dict[tuple[str, str], dict] = {}

    async def prepare(
        self,
        agent_name: str,
        context: SkillSelectionContext,
        project_id: str | None = None,
        db=None,
    ) -> CompiledSkillPacket:
        """Prepare a CompiledSkillPacket for the given agent and context.

        Chains: registry.list_manifests -> selector.select ->
        loader.load_many -> compiler.compile
        """
        # 1. Get all manifests from registry
        project_root = context.scene_context_package.get("project_root") if context.scene_context_package else None
        manifests = await self.registry.list_manifests(
            agent_name=agent_name,
            project_root=project_root,
            project_id=project_id,
            db=db,
        )

        # 2. Select which skills to activate
        selection = await self.selector.select(context, manifests)

        # 3. Load full skill bodies
        skills = await self.loader.load_many(selection.selected)

        # 4. Compile into packet
        context_dict = context.model_dump() if hasattr(context, "model_dump") else {}
        packet = await self.compiler.compile(
            agent_name=agent_name,
            context=context_dict,
            selected_skills=skills,
        )

        # Merge activation reasons from selector
        packet.activation_reasons.update(selection.activation_reasons)

        return packet

    async def execute_services(self, packet: CompiledSkillPacket, context: dict | None = None) -> dict:
        """Run service skills from the execution_plan.

        Dynamically imports and calls the service class/method for each
        step in the execution_plan.
        """
        context = context or {}
        results: dict[str, dict] = {}
        execution_phase = str(context.get("execution_phase") or "pre_generation")

        for step in packet.execution_plan:
            skill_id = step.get("skill_id", "")
            kind = step.get("kind", "prompt")
            step_phase = str(step.get("execution_phase") or "pre_generation")
            if step_phase != execution_phase:
                continue

            if kind == "prompt":
                continue

            if kind in ("service", "workflow"):
                result = await self._execute_service_step(step, context)
                if result is not None:
                    results[skill_id] = result
                else:
                    results[skill_id] = {"status": "failed"}

            elif kind == "tool":
                result = await self._execute_tool_step(step, context)
                if result is not None:
                    results[skill_id] = result
                else:
                    results[skill_id] = {"status": "failed"}

        return results

    async def validate_output(
        self,
        packet: CompiledSkillPacket,
        *,
        generated_text: str,
        context: dict | None = None,
    ) -> dict:
        """Run post-generation skill validators and return pass/fail trace data."""
        from app.services.agent_skill_validator import get_skill_validator

        return await get_skill_validator().validate_output(
            packet,
            generated_text=generated_text,
            context=context or {},
        )

    async def _execute_service_step(self, step: dict, context: dict) -> dict | None:
        """Dynamically import and call a service class/method."""
        service_class_path = step.get("service_class")
        service_method = step.get("service_method")
        if not service_class_path or not service_method:
            logger.warning("Service step missing class or method: %s", step)
            return None

        cache_key = self._execution_cache_key(step, context)
        if cache_key and cache_key in self._service_cache:
            return {**self._service_cache[cache_key], "_skill_cache_hit": True}

        try:
            instance = self._resolve_service(service_class_path)
            method = getattr(instance, service_method, None)
            if method is None:
                logger.warning("Method %s not found on %s", service_method, service_class_path)
                return None
            if callable(method):
                kwargs, missing = self._filter_call_kwargs(method, context)
                if missing:
                    return {
                        "status": "skipped",
                        "reason": "missing_required_context",
                        "missing": missing,
                    }
                result = method(**kwargs)
                if hasattr(result, "__await__"):
                    result = await asyncio.wait_for(
                        result,
                        timeout=max(float(step.get("timeout_seconds") or 30.0), 0.001),
                    )
                normalized = result if isinstance(result, dict) else {"result": result}
                normalized = self._apply_output_budget(
                    normalized,
                    int(step.get("output_char_budget") or 4000),
                )
                if cache_key:
                    self._service_cache[cache_key] = normalized
                return normalized
        except asyncio.TimeoutError:
            return {
                "status": "skipped",
                "reason": "execution_timeout",
                "timeout_seconds": float(step.get("timeout_seconds") or 30.0),
            }
        except Exception:
            logger.exception("Service execution failed for %s", service_class_path)
        return None

    @staticmethod
    def _resolve_service(class_path: str):
        if ":" in class_path:
            module_path, class_name = class_path.rsplit(":", 1)
        else:
            module_path, class_name = class_path.rsplit(".", 1)
        module = importlib.import_module(module_path)
        cls = getattr(module, class_name)
        return cls()

    async def _execute_tool_step(self, step: dict, context: dict) -> dict | None:
        """Execute a tool-type skill step with permission checking."""
        tools = step.get("tools", [])
        min_permission = step.get("min_permission", "readonly")
        results: dict[str, Any] = {}

        # Check tool permissions via ToolPermissionService
        try:
            from app.services.tool_permission_service import ToolPermissionService
            permission_svc = ToolPermissionService()
            granted = self._resolve_granted_permission(
                context.get("granted_permission", context.get("granted_permissions", "readonly"))
            )
        except ImportError:
            permission_svc = None
            granted = "readonly"

        for tool_name in tools:
            # Permission check
            if permission_svc is not None:
                decision = permission_svc.can_use(granted, tool_name, min_permission)
                if not decision.allowed:
                    results[tool_name] = {
                        "error": "permission_denied",
                        "reason": decision.reason or "Insufficient permissions",
                        "required": min_permission,
                    }
                    continue

            try:
                tool_fn = self._resolve_tool(tool_name)
                if tool_fn is None:
                    results[tool_name] = {"error": "tool not found"}
                    continue
                kwargs, missing = self._filter_call_kwargs(tool_fn, context)
                if missing:
                    results[tool_name] = {
                        "status": "skipped",
                        "reason": "missing_required_context",
                        "missing": missing,
                    }
                    continue
                result = tool_fn(**kwargs)
                if hasattr(result, "__await__"):
                    result = await asyncio.wait_for(
                        result,
                        timeout=max(float(step.get("timeout_seconds") or 30.0), 0.001),
                    )
                normalized = result if isinstance(result, dict) else {"result": result}
                results[tool_name] = self._apply_output_budget(
                    normalized,
                    int(step.get("output_char_budget") or 4000),
                )
            except asyncio.TimeoutError:
                results[tool_name] = {
                    "status": "skipped",
                    "reason": "execution_timeout",
                    "timeout_seconds": float(step.get("timeout_seconds") or 30.0),
                }
            except Exception:
                logger.exception("Tool execution failed: %s", tool_name)
                results[tool_name] = {"error": "execution failed"}

        return results if results else None

    @staticmethod
    def _resolve_granted_permission(value: Any) -> str:
        """Normalize permission context into a single highest granted level."""
        order = ["readonly", "safe_write", "project_write", "admin"]
        if isinstance(value, str):
            return value
        if isinstance(value, (list, tuple, set)):
            ranked = [item for item in value if isinstance(item, str) and item in order]
            if ranked:
                return max(ranked, key=order.index)
        return "readonly"

    @staticmethod
    def _resolve_tool(tool_name: str) -> Any | None:
        """Attempt to resolve a tool function by name from known modules."""
        _TOOL_MODULES = {
            "read_project": "app.skills.core_query",
            "read_context": "app.skills.core_query",
            "state_query": "app.skills.state_query",
            "shell_search": "app.skills.shell_search",
        }
        module_path = _TOOL_MODULES.get(tool_name)
        if not module_path:
            return None
        try:
            module = importlib.import_module(module_path)
            return getattr(module, tool_name, None)
        except (ImportError, AttributeError):
            return None

    @staticmethod
    def _filter_call_kwargs(method, context: dict) -> tuple[dict, list[str]]:
        """Pass only parameters accepted by the skill method and report missing required ones."""
        try:
            signature = inspect.signature(method)
        except (TypeError, ValueError):
            return dict(context), []

        accepts_kwargs = any(
            param.kind == inspect.Parameter.VAR_KEYWORD
            for param in signature.parameters.values()
        )
        if accepts_kwargs:
            return dict(context), []

        kwargs: dict[str, Any] = {}
        missing: list[str] = []
        for name, param in signature.parameters.items():
            if name == "self":
                continue
            if param.kind in {inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.POSITIONAL_ONLY}:
                continue
            if name in context:
                kwargs[name] = context[name]
            elif param.default is inspect.Parameter.empty:
                missing.append(name)
        return kwargs, missing

    @staticmethod
    def _execution_cache_key(step: dict, context: dict) -> tuple[str, str] | None:
        if not step.get("cache_enabled"):
            return None
        cache_key = context.get("skill_cache_key") or context.get("context_ledger_id")
        if not cache_key:
            return None
        return str(step.get("skill_id") or ""), str(cache_key)

    @staticmethod
    def _apply_output_budget(result: dict, budget: int) -> dict:
        budget = max(int(budget or 4000), 256)
        rendered = json.dumps(result, ensure_ascii=False, default=str)
        if len(rendered) <= budget:
            return result
        return {
            "status": result.get("status", "ok"),
            "truncated": True,
            "original_chars": len(rendered),
            "output_char_budget": budget,
            "summary": rendered[:budget],
        }


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_runtime: AgentSkillRuntime | None = None


def get_skill_runtime() -> AgentSkillRuntime:
    """Return the module-level runtime singleton."""
    global _runtime
    if _runtime is None:
        _runtime = AgentSkillRuntime()
    return _runtime
