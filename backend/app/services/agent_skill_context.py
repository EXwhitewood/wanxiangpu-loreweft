"""SkillContext — build the SkillSelectionContext for a given agent call (plan §9.5).

This service gathers all the contextual information needed by SkillResolver
to decide which skills should activate:

- Agent name and enabled skill lists (from agent_config)
- Project ID and active tab (for worldbuilder sub-agents)
- Scene contract and context package (from scene preparation)
- Writing mode profile
- Quality extensions and previous quality reports
- Feature policy

It acts as the bridge between the existing agent execution pipeline and
the new skill runtime.
"""
from __future__ import annotations

import logging

from app.models.agent_skill import SkillSelectionContext
from app.services.agent_config import AgentConfigManager

logger = logging.getLogger(__name__)


class SkillContextBuilder:
    """Build SkillSelectionContext from the existing agent execution pipeline."""

    def __init__(self) -> None:
        self._config_manager = AgentConfigManager()

    # -- public API ----------------------------------------------------------

    async def build(
        self,
        agent_name: str,
        project_id: str | None = None,
        active_tab: str | None = None,
        scene_contract: dict | None = None,
        scene_context_package: dict | None = None,
        writing_mode_profile: dict | None = None,
        quality_extensions: dict | None = None,
        previous_quality_reports: dict | None = None,
        feature_policy: dict | None = None,
    ) -> SkillSelectionContext:
        """Build a SkillSelectionContext from the given parameters.

        Automatically fills in enabled_skills and enabled_agent_skills
        from the agent_config if not explicitly provided.
        """
        enabled_skills, enabled_agent_skills = await self._resolve_enabled_skills(agent_name)

        return SkillSelectionContext(
            agent_name=agent_name,
            enabled_skills=enabled_skills,
            enabled_agent_skills=enabled_agent_skills,
            project_id=project_id,
            active_tab=active_tab,
            scene_contract=scene_contract or {},
            scene_context_package=scene_context_package or {},
            writing_mode_profile=writing_mode_profile or {},
            quality_extensions=quality_extensions or {},
            previous_quality_reports=previous_quality_reports or {},
            feature_policy=feature_policy or {},
        )

    async def build_from_context(self, context: dict) -> SkillSelectionContext:
        """Build a SkillSelectionContext from a generic agent execution context dict.

        This is a convenience method that extracts known keys from the
        context dict used by BaseAgent.execute().
        """
        agent_name = context.get("agent_name", context.get("name", ""))
        project_id = context.get("project_id")
        active_tab = context.get("active_tab")

        scene_contract = context.get("scene_contract", {})
        scene_context_package = context.get("scene_context_package", {})
        writing_mode_profile = context.get("writing_mode_profile", {})
        quality_extensions = context.get("quality_extensions", {})
        previous_quality_reports = context.get("previous_quality_reports", {})
        feature_policy = context.get("feature_policy", {})

        return await self.build(
            agent_name=agent_name,
            project_id=project_id,
            active_tab=active_tab,
            scene_contract=scene_contract,
            scene_context_package=scene_context_package,
            writing_mode_profile=writing_mode_profile,
            quality_extensions=quality_extensions,
            previous_quality_reports=previous_quality_reports,
            feature_policy=feature_policy,
        )

    # -- internal helpers ----------------------------------------------------

    async def _resolve_enabled_skills(
        self, agent_name: str,
    ) -> tuple[list[str] | None, list[str] | None]:
        """Resolve enabled skills from agent_config overrides.

        Returns (enabled_skills, enabled_agent_skills) where None means
        "use all available skills" (no override).
        """
        try:
            settings = await self._config_manager.load_settings()
            detail = await self._config_manager.get_agent_detail(agent_name, _cached_settings=settings)
            if detail:
                return detail.get("enabled_skills"), detail.get("enabled_agent_skills")

        except Exception as exc:
            logger.warning(
                "Failed to resolve enabled skills for '%s': %s",
                agent_name, exc,
            )

        return None, None

    # -- convenience: extract scene contract from scene preparation output ----

    @staticmethod
    def extract_scene_contract(scene_prep_output: dict) -> dict:
        """Extract a scene contract dict from ScenePreparationSkill output.

        The scene contract contains the key fields that SkillResolver uses
        for trigger matching (domain, scene_role, etc.).
        """
        contract: dict = {}

        focus_prompt = scene_prep_output.get("focus_prompt", "")
        story_state = scene_prep_output.get("story_state_snapshot", {})

        # Derive domain from scene context
        writing_mode = scene_prep_output.get("writing_mode_profile", {})
        if writing_mode:
            contract["domain"] = writing_mode.get("domain", "general")

        # Derive scene_role from scene beat type
        scene_beat = scene_prep_output.get("scene_beat", {})
        if scene_beat:
            contract["scene_role"] = scene_beat.get("type", "")

        # Include POV info
        pov = scene_prep_output.get("pov_character", "")
        if pov:
            contract["pov_character"] = pov

        return contract

    @staticmethod
    def extract_quality_extensions(
        quality_reports: list[dict] | dict,
    ) -> dict:
        """Extract quality extension info from quality checker reports.

        Used for trigger matching on issue_types.
        """
        extensions: dict = {}
        issue_types: list[str] = []

        reports = quality_reports if isinstance(quality_reports, list) else [quality_reports]
        for report in reports:
            if not isinstance(report, dict):
                continue
            findings = report.get("findings", [])
            for finding in findings:
                if isinstance(finding, dict):
                    issue_type = finding.get("type", finding.get("category", ""))
                    if issue_type and issue_type not in issue_types:
                        issue_types.append(issue_type)

        if issue_types:
            extensions["issue_types"] = issue_types

        return extensions


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_builder: SkillContextBuilder | None = None


def get_skill_context_builder() -> SkillContextBuilder:
    """Return the module-level context builder singleton."""
    global _builder
    if _builder is None:
        _builder = SkillContextBuilder()
    return _builder
