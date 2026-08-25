"""AgentSkillCompiler - compiles selected skills into CompiledSkillPacket (plan 9.4)."""
from __future__ import annotations

import logging
from pathlib import Path

from app.models.agent_skill import (
    AgentSkill,
    AgentSkillManifest,
    CompiledSkillPacket,
    SkillSelectionResult,
)

logger = logging.getLogger(__name__)

_FM = chr(45) * 3


def _extract_body(text: str) -> str:
    """Extract the Markdown body after YAML frontmatter."""
    if not text.startswith(_FM):
        return text.strip()
    parts = text.split(_FM, 2)
    if len(parts) < 3:
        return text.strip()
    return parts[2].strip()


def _extract_prompt_sections(body: str) -> dict[str, str]:
    """Extract prompt sections by looking for ## headings in the body."""
    sections: dict[str, str] = {}
    current_heading: str | None = None
    current_lines: list[str] = []

    for line in body.split("\n"):
        if line.startswith("## "):
            if current_heading is not None:
                sections[current_heading] = "\n".join(current_lines).strip()
            current_heading = line[3:].strip()
            current_lines = []
        else:
            current_lines.append(line)

    if current_heading is not None:
        sections[current_heading] = "\n".join(current_lines).strip()

    return sections


class AgentSkillCompiler:
    """Compiles selected skills into a CompiledSkillPacket.

    Compilation rules:
    - prompt skill -> system_sections / user_sections
    - service skill -> execution_plan
    - tool skill -> tool_permissions
    - workflow skill -> system_sections + execution_plan
    - validator skill -> validators

    Also builds the trace dict and must_avoid list.
    """

    async def compile(
        self,
        agent_name: str,
        context: dict | None,
        selected_skills: list[AgentSkill],
    ) -> CompiledSkillPacket:
        """Compile selected skills into a CompiledSkillPacket."""
        system_sections: list[str] = []
        user_sections: list[str] = []
        execution_plan: list[dict] = []
        tool_permissions: dict[str, str] = {}
        validators: list[str] = []
        validation_contracts: dict[str, dict] = {}
        repair_hooks: dict[str, list[str]] = {}
        must_avoid: list[str] = []
        required_context: dict[str, list[str]] = {}
        optional_context: dict[str, list[str]] = {}
        resource_packs: dict[str, dict[str, dict]] = {}
        active_skill_ids: list[str] = []
        activation_reasons: dict[str, str] = {}

        for skill in selected_skills:
            manifest = skill.manifest
            active_skill_ids.append(manifest.id)
            if manifest.runtime.required_context:
                required_context[manifest.id] = list(manifest.runtime.required_context)
            if manifest.runtime.optional_context:
                optional_context[manifest.id] = list(manifest.runtime.optional_context)
            if skill.resource_packs:
                resource_packs[manifest.id] = dict(skill.resource_packs)

            # prompt skill -> system_sections / user_sections
            if manifest.kind in ("prompt", "workflow"):
                if skill.body:
                    system_sections.append(self._render_system_section(manifest, skill))
                for key, content in skill.prompt_sections.items():
                    user_sections.append(f"### {manifest.name} - {key}\n{content}")

            # service skill -> execution_plan
            if manifest.kind == "service":
                execution_plan.append(self._build_execution_step(manifest))

            # tool skill -> tool_permissions
            if manifest.kind == "tool":
                for tool_name in manifest.runtime.tool_names:
                    tool_permissions[tool_name] = manifest.runtime.min_permission
                execution_plan.append(self._build_execution_step(manifest))

            # workflow skill -> system_sections + execution_plan
            if manifest.kind == "workflow":
                execution_plan.append(self._build_execution_step(manifest))

            # validator skill -> validators
            if manifest.kind == "validator":
                validators.append(manifest.id)
            validators.extend(manifest.validators)
            for validator_id in ([manifest.id] if manifest.kind == "validator" else []) + list(manifest.validators):
                explicit_contract = manifest.validation_contracts.get(validator_id)
                if isinstance(explicit_contract, dict) and explicit_contract:
                    validation_contracts.setdefault(validator_id, {})
                    validation_contracts[validator_id][manifest.id] = explicit_contract
                elif manifest.constraints:
                    validation_contracts.setdefault(validator_id, {})
                    validation_contracts[validator_id][manifest.id] = manifest.constraints.get(
                        validator_id,
                        manifest.constraints,
                    )
                # repair_hooks are executable runtime entry points. Repair
                # strategies remain declarative guidance and must not be
                # dispatched as if they were registered hooks.
                hooks = list(dict.fromkeys(manifest.repair_hooks))
                if hooks:
                    repair_hooks.setdefault(validator_id, [])
                    repair_hooks[validator_id] = list(dict.fromkeys(repair_hooks[validator_id] + hooks))

            # must_avoid from prompt_sections
            avoid = skill.prompt_sections.get("must_avoid", "")
            if avoid:
                for line in avoid.strip().splitlines():
                    line = line.strip().lstrip("- ")
                    if line:
                        must_avoid.append(line)

        # Style polish constraints are skill-owned. They should only be active
        # when the relevant skill was selected for this turn.
        if {"style_polish", "anti_ai_prose"} & set(active_skill_ids):
            try:
                from app.skills.style_polish import StylePolishSkill
                must_avoid.extend(StylePolishSkill.FORBIDDEN_WORDS)
            except ImportError:
                pass

        # Deduplicate must_avoid
        seen_avoid: set[str] = set()
        deduped_avoid: list[str] = []
        for item in must_avoid:
            if item not in seen_avoid:
                seen_avoid.add(item)
                deduped_avoid.append(item)

        return CompiledSkillPacket(
            agent_name=agent_name,
            active_skills=active_skill_ids,
            activation_reasons=activation_reasons,
            system_sections=system_sections,
            user_sections=user_sections,
            execution_plan=execution_plan,
            tool_permissions=tool_permissions,
            validators=list(dict.fromkeys(validators)),
            validation_contracts=validation_contracts,
            repair_hooks=repair_hooks,
            must_avoid=deduped_avoid[:20],
            required_context=required_context,
            optional_context=optional_context,
            resource_packs=resource_packs,
            trace={
                "total_selected": len(selected_skills),
                "total_system_sections": len(system_sections),
                "total_user_sections": len(user_sections),
                "total_execution_steps": len(execution_plan),
                "total_validators": len(validators),
                "total_validation_contracts": len(validation_contracts),
                "required_context": required_context,
                "optional_context": optional_context,
                "resource_pack_skills": sorted(resource_packs),
            },
        )

    @staticmethod
    def _render_system_section(manifest: AgentSkillManifest, skill: AgentSkill) -> str:
        """Render a single skill as a system prompt section."""
        header = f"### Skill: {manifest.name} (v{manifest.version})"
        desc = f"\n{manifest.description}" if manifest.description else ""
        body = f"\n\n{skill.body}" if skill.body else ""
        return f"{header}{desc}{body}"

    @staticmethod
    def _build_execution_step(manifest: AgentSkillManifest) -> dict:
        """Build an execution plan step for service/tool/workflow skills."""
        step: dict = {
            "skill_id": manifest.id,
            "kind": manifest.kind,
            "priority": manifest.priority,
        }
        if manifest.runtime.service_class:
            step["service_class"] = manifest.runtime.service_class
        if manifest.runtime.service_method:
            step["service_method"] = manifest.runtime.service_method
        if manifest.runtime.required_context:
            step["required_context"] = list(manifest.runtime.required_context)
        if manifest.runtime.optional_context:
            step["optional_context"] = list(manifest.runtime.optional_context)
        if manifest.runtime.execution_phase:
            step["execution_phase"] = manifest.runtime.execution_phase
        step["timeout_seconds"] = manifest.runtime.timeout_seconds
        step["output_char_budget"] = manifest.runtime.output_char_budget
        step["cache_enabled"] = manifest.runtime.cache_enabled
        if manifest.runtime.tool_names:
            step["tools"] = manifest.runtime.tool_names
            step["min_permission"] = manifest.runtime.min_permission
        return step
