from __future__ import annotations

from app.agents.base import BaseAgent
from app.services.quality_checkers.literary_quality_checker import LiteraryQualityChecker


class LiteraryQualityAuditorAgent(BaseAgent):
    """Auditor boundary for prose-quality evaluation.

    This agent currently delegates to deterministic checks so the quality layer
    remains cheap and stable. The boundary keeps room for an optional LLM pass
    when the project explicitly asks for deeper auditing.
    """

    name = "literary_quality_auditor"

    async def execute(self, context: dict) -> dict:
        return LiteraryQualityChecker().check(
            context.get("generated_text", ""),
            contract=context.get("literary_quality_contract", {}),
            writing_mode_profile=context.get("writing_mode_profile", {}),
            style_context=context.get("style_context", {}),
        )
