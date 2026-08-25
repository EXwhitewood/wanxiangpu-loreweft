import pytest

from app.services.experience_contract_compiler import ExperienceContractCompiler
from app.services.writing_mode_profile_service import WritingModeProfileService


@pytest.mark.asyncio
async def test_compiler_builds_contracts_and_quality_extensions():
    profile = WritingModeProfileService().get_profile("commercial_web")
    compiled = await ExperienceContractCompiler().compile({
        "project_id": "project-id",
        "chapter_number": 1,
        "scene_index": 0,
        "scene_beat": {
            "goal": "角色A进入地点Y",
            "conflict": "角色B阻拦角色A",
            "hook": "物品X为什么出现？",
        },
        "scene_contract": {
            "goal": "角色A进入地点Y",
            "conflict": "角色B阻拦角色A",
            "must_show": ["角色A发现物品X"],
        },
        "writing_mode_profile": profile.model_dump(),
        "style_context": {},
    })

    assert compiled.experience_contract.writing_mode_id == "commercial_web"
    assert compiled.literary_quality_contract.writing_mode_id == "commercial_web"
    assert compiled.quality_extensions_patch["experience_guidance"]
    assert compiled.quality_extensions_patch["literary_quality_guidance"]
    assert "source_of_truth" not in compiled.experience_contract.model_dump()
