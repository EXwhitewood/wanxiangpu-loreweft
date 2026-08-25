"""AgentSkillSelector - selects which skills to activate (plan 9.3)."""
from __future__ import annotations

import logging

from app.models.agent_skill import (
    AgentSkillManifest,
    SkillSelectionContext,
    SkillSelectionResult,
)
from app.services.agent_skill_feedback import get_skills_for_issues

logger = logging.getLogger(__name__)

MAX_ACTIVE_SKILLS = 15


class AgentSkillSelector:
    """Selects which skills to activate based on context.

    Selection rules:
    1. Only select skills available to current Agent (manifest.agents contains
       agent_name or is empty)
    2. User-explicitly disabled skills must not be selected
    3. enabled_by_default skills can be default-enabled
    4. Context trigger match enables skill
    5. Historical issue type match enables skill
    6. Single activation has a max limit (MAX_ACTIVE_SKILLS = 15)
    7. Service skills have higher priority than prompt skills

    Disable semantics:
    - enabled_skills is None: use default utility skills
    - enabled_skills == []: disable all utility skills
    - enabled_skills == ["x"]: only enable x
    """

    def _matches_trigger(self, manifest: AgentSkillManifest, ctx: SkillSelectionContext) -> bool:
        """Check if the manifest's triggers match the current context."""
        triggers = manifest.triggers

        if triggers.agents and ctx.agent_name not in triggers.agents:
            return False

        has_activation_trigger = any((
            triggers.domains,
            triggers.scene_roles,
            triggers.issue_types,
            triggers.writing_modes,
            triggers.tabs,
            triggers.feature_flags,
        ))
        if not has_activation_trigger:
            return False

        if triggers.tabs and (not ctx.active_tab or ctx.active_tab not in triggers.tabs):
            return False

        if triggers.writing_modes:
            mode = ctx.writing_mode_profile.get("mode") or ""
            if mode not in triggers.writing_modes:
                return False

        if triggers.issue_types:
            issues = ctx.previous_quality_reports.get("issue_types") or []
            if not any(i in triggers.issue_types for i in issues):
                return False

        if triggers.feature_flags:
            flags = ctx.feature_policy.get("enabled_flags") or []
            if not any(f in triggers.feature_flags for f in flags):
                return False

        if triggers.scene_roles:
            role = ctx.scene_contract.get("pov_role") or ctx.scene_context_package.get("pov_role") or ""
            if role not in triggers.scene_roles:
                return False

        if triggers.domains:
            domain = ctx.scene_contract.get("domain") or ctx.scene_context_package.get("domain") or ""
            if domain not in triggers.domains:
                return False

        return True

    def _is_explicitly_enabled(self, manifest: AgentSkillManifest, ctx: SkillSelectionContext) -> bool:
        """Check if the skill is explicitly enabled by the user."""
        if ctx.enabled_skills is not None and manifest.id in ctx.enabled_skills:
            return True
        if ctx.enabled_agent_skills is not None and manifest.id in ctx.enabled_agent_skills:
            return True
        # Also check by name for backward compatibility
        if ctx.enabled_skills is not None and manifest.name in ctx.enabled_skills:
            return True
        if ctx.enabled_agent_skills is not None and manifest.name in ctx.enabled_agent_skills:
            return True
        return False

    def _is_explicitly_disabled(self, manifest: AgentSkillManifest, ctx: SkillSelectionContext) -> bool:
        """Check if the skill is explicitly disabled.

        Disable semantics:
        - enabled_skills controls utility skills.
        - enabled_agent_skills controls agent/category-specific skills.
        - None means use defaults, [] means disable that category.
        """
        enabled_names = (
            ctx.enabled_agent_skills
            if manifest.category == "agent"
            else ctx.enabled_skills
        )
        if enabled_names is None:
            return False
        if len(enabled_names) == 0:
            return True
        if manifest.id not in enabled_names and manifest.name not in enabled_names:
            return True

        return False

    async def select(
        self,
        ctx: SkillSelectionContext,
        manifests: list[AgentSkillManifest],
    ) -> SkillSelectionResult:
        """Select which skills to activate based on context."""
        selected: list[AgentSkillManifest] = []
        skipped: dict[str, str] = {}
        activation_reasons: dict[str, str] = {}

        for manifest in manifests:
            # Rule 1: Agent availability check
            agent_match = not manifest.agents or ctx.agent_name in manifest.agents
            if not agent_match:
                skipped[manifest.id] = "agent_mismatch"
                continue

            # Check if explicitly enabled before category disable filters.
            explicitly = self._is_explicitly_enabled(manifest, ctx)

            if explicitly:
                selected.append(manifest)
                activation_reasons[manifest.id] = "explicitly_enabled"
                continue

            # Rule 2: Explicitly disabled check
            if self._is_explicitly_disabled(manifest, ctx):
                skipped[manifest.id] = "explicitly_disabled"
                continue

            # Rule 3: enabled_by_default
            if manifest.enabled_by_default:
                if self._matches_trigger(manifest, ctx):
                    selected.append(manifest)
                    activation_reasons[manifest.id] = "default_enabled+trigger_match"
                else:
                    selected.append(manifest)
                    activation_reasons[manifest.id] = "default_enabled"
                continue

            # Rule 4 & 5: Context trigger match or historical issue type match
            if self._matches_trigger(manifest, ctx):
                selected.append(manifest)
                activation_reasons[manifest.id] = "trigger_match"
                continue

            skipped[manifest.id] = "not_triggered"

        # Feedback loop: activate skills based on quality issue types
        try:
            from app.services.agent_skill_feedback import get_skills_for_issues
            issues = ctx.previous_quality_reports.get("issue_types") or []
            if issues:
                feedback_skills = get_skills_for_issues(issues)
                for skill_id, reason in feedback_skills.items():
                    # Check if this skill is available to the current agent
                    already_selected = any(m.id == skill_id for m in selected)
                    if already_selected:
                        continue
                    # Find the manifest for this skill
                    manifest_match = next((m for m in manifests if m.id == skill_id), None)
                    if manifest_match is None:
                        continue
                    # Check agent availability
                    if manifest_match.agents and ctx.agent_name not in manifest_match.agents:
                        continue
                    # Check if explicitly disabled
                    if self._is_explicitly_disabled(manifest_match, ctx):
                        continue
                    selected.append(manifest_match)
                    activation_reasons[skill_id] = f"feedback:{reason}"
        except Exception:
            logger.debug("Feedback map check failed", exc_info=True)

        # Rule 7: Service skills have higher priority than prompt skills
        selected.sort(key=lambda m: (
            0 if m.kind in ("service", "tool", "workflow") else 1,
            -m.priority,
        ))

        # Rule 6: Max active skills limit
        if len(selected) > MAX_ACTIVE_SKILLS:
            overflow = selected[MAX_ACTIVE_SKILLS:]
            for m in overflow:
                skipped[m.id] = "max_active_skills_exceeded"
                activation_reasons.pop(m.id, None)
            selected = selected[:MAX_ACTIVE_SKILLS]

        return SkillSelectionResult(
            selected=selected,
            skipped=skipped,
            activation_reasons=activation_reasons,
        )
