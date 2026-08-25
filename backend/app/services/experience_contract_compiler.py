from __future__ import annotations

from pydantic import BaseModel, Field

from app.agents.narrative_experience_architect import NarrativeExperienceArchitectAgent
from app.models.literary_quality import LiteraryQualityContract
from app.models.narrative_experience import NarrativeExperienceContract
from app.models.writing_mode import WritingModeProfile
from app.services.style_experience_conflict_resolver import StyleExperienceConflictResolver


class CompiledExperienceContracts(BaseModel):
    schema_version: int = 1
    experience_contract: NarrativeExperienceContract
    literary_quality_contract: LiteraryQualityContract
    writing_mode_profile: WritingModeProfile
    compiler_warnings: list[str] = Field(default_factory=list)
    style_conflict_report: dict = Field(default_factory=dict)
    quality_extensions_patch: dict = Field(default_factory=dict)


class ExperienceContractCompiler:
    """Compile mode-aware quality contracts from existing scene context."""

    async def compile(self, context: dict) -> CompiledExperienceContracts:
        profile = WritingModeProfile(**(context.get("writing_mode_profile") or {"id": "general"}))
        warnings: list[str] = []

        try:
            payload = await NarrativeExperienceArchitectAgent().execute(context)
            exp = NarrativeExperienceContract(**(payload.get("experience_contract") or {}))
            lit = LiteraryQualityContract(**(payload.get("literary_quality_contract") or {}))
            warnings.extend(payload.get("compiler_warnings") or [])
        except Exception as exc:
            warnings.append(f"experience_architect_degraded: {exc}")
            exp = NarrativeExperienceContract(
                project_id=str(context.get("project_id", "")),
                chapter_number=int(context.get("chapter_number") or 0),
                scene_index=int(context.get("scene_index") or 0),
                writing_mode_id=profile.id,
                scene_role="推进场景",
                reader_promise=str((context.get("scene_contract") or {}).get("hook") or ""),
                agency_requirement="核心角色必须有可观察选择或行动。",
                success_metrics={key: profile.weight(key) for key in profile.metric_weights},
            )
            lit = LiteraryQualityContract(
                project_id=str(context.get("project_id", "")),
                chapter_number=int(context.get("chapter_number") or 0),
                scene_index=int(context.get("scene_index") or 0),
                writing_mode_id=profile.id,
                prose_goal="具体、清楚、有节奏，不改变事实合同。",
            )

        style_report = StyleExperienceConflictResolver().resolve(
            experience_contract=exp,
            literary_quality_contract=lit,
            writing_mode_profile=profile,
            style_context=context.get("style_context") or {},
        )
        patch = self._build_quality_extensions_patch(exp, lit, profile, style_report)
        return CompiledExperienceContracts(
            experience_contract=exp,
            literary_quality_contract=lit,
            writing_mode_profile=profile,
            compiler_warnings=warnings,
            style_conflict_report=style_report,
            quality_extensions_patch=patch,
        )

    @staticmethod
    def _build_quality_extensions_patch(
        exp: NarrativeExperienceContract,
        lit: LiteraryQualityContract,
        profile: WritingModeProfile,
        style_report: dict,
    ) -> dict:
        experience_guidance = []
        if exp.scene_role:
            experience_guidance.append(f"本场体验功能：{exp.scene_role}")
        if exp.protagonist_desire or exp.obstacle:
            experience_guidance.append(
                f"让核心角色在「{exp.obstacle or '阻碍'}」下追求「{exp.protagonist_desire or '当前目标'}」。"
            )
        if exp.agency_requirement:
            experience_guidance.append(exp.agency_requirement)
        if exp.curiosity_question:
            experience_guidance.append(f"读者追问：{exp.curiosity_question}")
        if exp.hook_out:
            experience_guidance.append(f"结尾钩子应指向：{exp.hook_out}")

        literary_guidance = []
        if lit.prose_goal:
            literary_guidance.append(lit.prose_goal)
        budget = lit.specificity_budget or {}
        min_details = budget.get("minimum_specific_details")
        if min_details:
            literary_guidance.append(f"至少安排 {min_details} 个承担信息或压力的具体细节。")
        if lit.abstraction_ceiling:
            literary_guidance.append(f"抽象解释上限：{lit.abstraction_ceiling}，优先场景化呈现。")
        for move in (lit.required_textual_moves or [])[:2]:
            literary_guidance.append(move)

        mode_guardrails = {
            "writing_mode_id": profile.id,
            "label": profile.label,
            "target_reader": profile.target_reader,
            "metric_weights": {
                key: profile.metric_weights.get(key)
                for key in (
                    "reading_drive",
                    "clarity",
                    "dramatic_pressure",
                    "protagonist_agency",
                    "specificity",
                    "prose_identity",
                )
                if key in profile.metric_weights
            },
        }

        patch = {
            "experience_guidance": experience_guidance[:6],
            "literary_quality_guidance": literary_guidance[:6],
            "mode_guardrails": mode_guardrails,
        }
        conflict_patch = (style_report or {}).get("quality_extensions_patch")
        if isinstance(conflict_patch, dict):
            patch.update(conflict_patch)
        return patch
