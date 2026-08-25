import uuid
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.engines.reveal_readiness import calculate_reveal_readiness
from app.services.foreshadowing_service import ForeshadowingService
from app.services.foreshadowing_clue_service import ForeshadowingClueService
from app.services.foreshadowing_graph_service import ForeshadowingGraphService


@pytest_asyncio.fixture
async def setup_foreshadowing_with_clues(project_in_db, db):
    service = ForeshadowingService()
    line = await service.create_foreshadowing_line(project_in_db, {
        "name": "准备度测试伏笔",
        "description": "测试揭示准备度",
        "status": "active",
        "priority": "major",
        "secret": {
            "canonical_statement": "宝剑是上古神器",
            "truth_type": "object_function",
            "impact_level": "major",
            "spoiler_scope": {"characters": ["林逸"]},
        },
        "timeline": {
            "bury_window": [3, 10],
            "reveal_window": [45, 50],
        },
        "narrative_structure": {
            "total_clues_planned": 5,
        },
    }, db)

    clue_service = ForeshadowingClueService()
    for i in range(4):
        await clue_service.add_clue(
            project_id=project_in_db,
            foreshadowing_line_id=line["id"],
            clue_type="supportive",
            chapter_number=5 + i * 3,
            clue_text=f"第{i+1}条支持性线索",
            db=db,
        )

    await service.set_character_cognitive_state(
        project_in_db, line["id"], "林逸", "suspicious", 20, db,
        cognitive_level="high_suspicion", event="发现线索",
    )

    return line


class TestRevealReadiness:
    @pytest.mark.asyncio
    async def test_readiness_calculation(self, setup_foreshadowing_with_clues, db):
        line = setup_foreshadowing_with_clues
        result = await calculate_reveal_readiness(line["id"], db)
        assert "readiness" in result
        assert "components" in result
        assert "ready" in result
        assert "recommendation" in result
        assert 0.0 <= result["readiness"] <= 1.0

    @pytest.mark.asyncio
    async def test_evidence_coverage_component(self, setup_foreshadowing_with_clues, db):
        line = setup_foreshadowing_with_clues
        result = await calculate_reveal_readiness(line["id"], db)
        assert result["components"]["evidence_coverage"] == 0.8  # 4/5

    @pytest.mark.asyncio
    async def test_knowledge_alignment_component(self, setup_foreshadowing_with_clues, db):
        line = setup_foreshadowing_with_clues
        result = await calculate_reveal_readiness(line["id"], db)
        assert result["components"]["knowledge_alignment"] == 1.0  # 1 informed / 1 total

    @pytest.mark.asyncio
    async def test_not_found_returns_zero(self, db):
        result = await calculate_reveal_readiness(uuid.uuid4(), db)
        assert result["readiness"] == 0.0
        assert result["ready"] is False

    @pytest.mark.asyncio
    async def test_readiness_below_threshold(self, project_in_db, db):
        service = ForeshadowingService()
        line = await service.create_foreshadowing_line(project_in_db, {
            "name": "未准备伏笔",
            "description": "没有任何线索",
            "status": "planned",
            "secret": {
                "canonical_statement": "某个秘密",
                "truth_type": "past_event",
                "impact_level": "minor",
                "spoiler_scope": {},
            },
            "narrative_structure": {"total_clues_planned": 5},
        }, db)

        result = await calculate_reveal_readiness(line["id"], db)
        assert result["readiness"] < 0.65
        assert result["ready"] is False
        assert "准备度不足" in result["recommendation"]
