from abc import ABC, abstractmethod

from app.services.agent_config import AgentConfigManager
from app.services.llm_client import LLMClient


class BaseAgent(ABC):
    name: str = ""
    agent_config_manager: AgentConfigManager = AgentConfigManager()

    async def get_agent_config(self):
        return await self.agent_config_manager.get_agent_config(self.name)

    async def get_llm_client(self) -> LLMClient:
        config = await self.get_agent_config()
        return LLMClient(
            api_format=config.api_format,
            api_key=config.api_key,
            base_url=config.base_url,
            model=config.model,
        )

    @abstractmethod
    async def execute(self, context: dict) -> dict:
        pass

    async def get_agent_runtime_detail(self) -> dict:
        """Return agent detail including skill configuration for runtime use."""
        return await self.agent_config_manager.get_agent_detail(self.name)

    async def prepare_skill_packet(self, context: dict) -> "CompiledSkillPacket":
        """Prepare a CompiledSkillPacket for this agent using the Skill Runtime.

        This is the main entry point for agents to consume the skill system.
        The returned packet can be used to inject skill content into prompts.

        Args:
            context: Execution context dict with keys like:
                - project_id: Optional project ID
                - db: Optional database session
                - active_tab: Optional active tab (for worldbuilder)
                - scene_contract: Optional scene contract
                - scene_context_package: Optional scene context
                - writing_mode_profile: Optional writing mode
                - quality_extensions: Optional quality extensions
                - previous_quality_reports: Optional previous quality reports
                - feature_policy: Optional feature policy

        Returns:
            CompiledSkillPacket with rendered system/user sections
        """
        from app.models.agent_skill import SkillSelectionContext, CompiledSkillPacket
        from app.services.agent_skill_runtime import get_skill_runtime

        # Build selection context from agent detail and execution context
        detail = await self.get_agent_runtime_detail()

        selection_ctx = SkillSelectionContext(
            agent_name=self.name,
            enabled_skills=detail.get("enabled_skills"),
            enabled_agent_skills=detail.get("enabled_agent_skills"),
            project_id=context.get("project_id"),
            active_tab=context.get("active_tab"),
            scene_contract=context.get("scene_contract", {}),
            scene_context_package=context.get("scene_context_package", {}),
            writing_mode_profile=context.get("writing_mode_profile", {}),
            quality_extensions=context.get("quality_extensions", {}),
            previous_quality_reports=context.get("previous_quality_reports", {}),
            feature_policy=context.get("feature_policy", {}),
        )

        runtime = get_skill_runtime()
        return await runtime.prepare(
            agent_name=self.name,
            context=selection_ctx,
            project_id=context.get("project_id"),
            db=context.get("db"),
        )
