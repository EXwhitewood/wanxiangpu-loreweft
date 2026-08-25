import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.reader_experience import ReaderExperienceAgent
from app.db.db_models import Project, get_db
from app.services.concept_budget_service import ConceptBudgetService
from app.services.quality_checkers.ai_flavor_checker import AIFlavorChecker
from app.services.quality_checkers.character_voice_checker import CharacterVoiceChecker
from app.services.quality_checkers.commercial_pacing_checker import CommercialPacingChecker
from app.services.quality_checkers.concept_budget_checker import ConceptBudgetChecker
from app.services.quality_checkers.literary_quality_checker import LiteraryQualityChecker
from app.services.quality_checkers.narrative_experience_checker import NarrativeExperienceChecker
from app.services.quality_checkers.scene_credibility_checker import SceneCredibilityChecker
from app.services.scene_credibility_compiler import SceneCredibilityCompiler
from app.services.quality_memory_service import QualityMemoryService
from app.services.reader_context_builder import ReaderContextBuilder
from app.services.generation_feature_policy import resolve_generation_feature_policy
from app.services.writing_mode_profile_service import WritingModeProfileService

router = APIRouter()


class QualityPreviewRequest(BaseModel):
    text: str = Field(min_length=1, max_length=100_000)
    scene_contract: dict | None = None
    chapter_state: dict | None = None
    character_cards: list[dict] | None = None
    include_reader: bool = False


@router.post("/quality/preview")
async def preview_quality(
    project_id: uuid.UUID,
    data: QualityPreviewRequest,
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    scene_contract = data.scene_contract or {}
    chapter_state = data.chapter_state or {}
    result = {
        "ai_flavor": AIFlavorChecker().check(data.text, scene_contract, chapter_state),
        "concept_budget": ConceptBudgetChecker().check(data.text, scene_contract, chapter_state),
        "character_voice": CharacterVoiceChecker().check(
            data.text,
            scene_contract,
            chapter_state,
            data.character_cards or (project.core_data or {}).get("characters", []),
        ),
        "concept_budget_suggestion": ConceptBudgetService().suggest_for_contract(scene_contract),
    }
    policy = resolve_generation_feature_policy(project)
    writing_profile = WritingModeProfileService().get_project_profile(project, policy)
    if policy.narrative_experience_mode != "off" or policy.mode_fit_mode != "off":
        result["narrative_experience"] = NarrativeExperienceChecker().check(
            data.text,
            scene_contract.get("experience_contract", {}),
            writing_profile,
            scene_contract=scene_contract,
        )
    if policy.literary_quality_mode != "off":
        result["literary_quality"] = LiteraryQualityChecker().check(
            data.text,
            scene_contract.get("literary_quality_contract", {}),
            writing_profile,
        )
    if policy.commercial_pacing_mode != "off":
        commercial_report = CommercialPacingChecker().check(
            data.text,
            scene_contract.get("commercial_pacing_contract", {}),
            scene_contract=scene_contract,
        )
        commercial_report["mode"] = policy.commercial_pacing_mode
        result["commercial_pacing"] = commercial_report
    if policy.scene_credibility_mode != "off":
        credibility_contract = scene_contract.get("scene_credibility_contract")
        if not isinstance(credibility_contract, dict):
            credibility_contract = SceneCredibilityCompiler().compile({
                "project_id": str(project_id),
                "chapter_number": int(scene_contract.get("chapter_number") or 0),
                "scene_index": int(scene_contract.get("scene_index") or 0),
                "scene_contract": scene_contract,
                "chapter_state": chapter_state,
                "character_cards": data.character_cards or (project.core_data or {}).get("characters", []),
                "quality_memory": QualityMemoryService().get_project_quality_memory(project),
            }).contract.model_dump()
        credibility_report = SceneCredibilityChecker().check(
            data.text,
            credibility_contract,
            mode=policy.scene_credibility_mode,
        )
        credibility_report["mode"] = policy.scene_credibility_mode
        result["scene_credibility"] = credibility_report
    if data.include_reader and policy.reader_experience_mode != "off":
        reader_context = ReaderContextBuilder().build(
            generated_text=data.text,
            project=project,
            chapter_number=int(scene_contract.get("chapter_number") or 0),
            scene_index=int(scene_contract.get("scene_index") or 0),
        )
        result["reader_experience"] = await ReaderExperienceAgent().execute(reader_context)
    elif data.include_reader:
        result["reader_experience"] = {
            "schema_version": 1,
            "status": "disabled",
            "degraded": False,
            "error": "reader_experience_mode_off",
        }
    return result


@router.get("/chapters/{chapter_number}/experience-quality")
async def get_chapter_experience_quality(
    project_id: uuid.UUID,
    chapter_number: int,
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    reports = await QualityMemoryService().get_chapter_experience_quality(
        db,
        project_id=project_id,
        chapter_number=chapter_number,
    )
    return {
        "chapter_number": chapter_number,
        "reports": reports,
        "quality_memory": QualityMemoryService().get_project_quality_memory(project),
    }
