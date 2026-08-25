import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
import pytest_asyncio

from app.engines.fcip_engine import FCIPEngine, FCIPContext, META_TERMS
from app.services.foreshadowing_service import ForeshadowingService


@pytest_asyncio.fixture
async def engine():
    return FCIPEngine(foreshadowing_service=ForeshadowingService())


class TestFCIPCompile:
    def test_compile_plant_action(self, engine):
        ctx = FCIPContext(
            project_id=uuid.uuid4(),
            chapter_number=5,
            scene_index=0,
            pov_character="林逸",
        )
        compiled = engine.compile_for_scene(ctx, raw_instructions=[
            {"name": "身份秘密", "action": "plant", "description": "主角其实是魔族后裔"},
        ])
        assert len(compiled) >= 1
        item = compiled[0]
        assert item["name"] == "身份秘密"
        assert item["action"] == "plant"

    def test_compile_reveal_action(self, engine):
        ctx = FCIPContext(
            project_id=uuid.uuid4(),
            chapter_number=50,
            scene_index=0,
            pov_character="林逸",
        )
        compiled = engine.compile_for_scene(ctx, raw_instructions=[
            {"name": "身份秘密", "action": "reveal"},
        ])
        assert len(compiled) >= 1
        assert compiled[0]["action"] == "reveal"

    def test_no_meta_terms_in_compiled(self, engine):
        ctx = FCIPContext(
            project_id=uuid.uuid4(),
            chapter_number=5,
            scene_index=0,
            pov_character="林逸",
        )
        compiled = engine.compile_for_scene(ctx, raw_instructions=[
            {"name": "身份秘密", "action": "plant", "description": "这是一个伏笔，需要埋设线索暗示读者"},
        ])
        for item in compiled:
            assert not engine.contains_meta_terms(item["compiled_hint"]), (
                f"Meta term leaked: {item['compiled_hint']}"
            )
            for term in META_TERMS:
                assert term not in item["compiled_hint"], (
                    f"Term '{term}' found in: {item['compiled_hint']}"
                )

    def test_cognitive_level_constraints(self, engine):
        for level in ["fully_blind", "vague_unease", "partial_clue", "high_suspicion", "fully_aware", "misled"]:
            ctx = FCIPContext(
                project_id=uuid.uuid4(),
                chapter_number=5,
                scene_index=0,
                pov_character="角色",
                character_cognitive_map={"角色": [{"foreshadowing_line_id": "test-id", "cognitive_level": level}]},
                active_foreshadowing=[{
                    "id": "test-id",
                    "name": "测试伏笔",
                    "action": "plant",
                }],
            )
            compiled = engine.compile_for_scene(ctx)
            assert len(compiled) >= 1
            assert compiled[0]["constraint_level"] == level

    def test_deduplication_by_name(self, engine):
        ctx = FCIPContext(
            project_id=uuid.uuid4(),
            chapter_number=5,
            scene_index=0,
        )
        compiled = engine.compile_for_scene(ctx, raw_instructions=[
            {"name": "重复伏笔", "action": "plant", "description": "第一次"},
            {"name": "重复伏笔", "action": "plant", "description": "第二次"},
        ])
        names = [c["name"] for c in compiled]
        assert names.count("重复伏笔") == 1


class TestFCIPContainsMetaTerms:
    def test_detects_meta_terms(self):
        for term in META_TERMS:
            assert FCIPEngine.contains_meta_terms(f"这是{term}的文本"), f"Failed for: {term}"

    def test_clean_text(self):
        assert not FCIPEngine.contains_meta_terms("他走进了那间旧屋子。")


class TestFCIPBudget:
    @pytest.mark.asyncio
    async def test_budget_within_limits(self, engine):
        ctx = FCIPContext(
            project_id=uuid.uuid4(),
            chapter_number=5,
            scene_index=0,
            active_foreshadowing=[{"id": uuid.uuid4(), "name": f"伏笔{i}"} for i in range(3)],
        )
        result = await engine.check_budget(ctx, db=None)
        assert result["within_budget"] is True
        assert result["active_count"] == 3

    @pytest.mark.asyncio
    async def test_budget_exceeds_active_count(self, engine):
        ctx = FCIPContext(
            project_id=uuid.uuid4(),
            chapter_number=5,
            scene_index=0,
            active_foreshadowing=[{"id": uuid.uuid4(), "name": f"伏笔{i}"} for i in range(5)],
        )
        result = await engine.check_budget(ctx, db=None)
        assert result["within_budget"] is False
        assert any("活跃伏笔数" in w for w in result["warnings"])


@pytest.mark.asyncio
async def test_post_gen_check_excludes_clues_from_the_same_chapter(monkeypatch):
    foreshadowing = SimpleNamespace(
        list_actionable_for_scene=AsyncMock(
            return_value=[{"id": str(uuid.uuid4()), "repetition_distance": 3}]
        ),
        list_cognitive_states_for_foreshadowing=AsyncMock(return_value=[]),
    )
    clue_service = SimpleNamespace(
        get_evidence_pool=AsyncMock(
            return_value={
                "clues": [
                    {"chapter_number": 2, "clue_text": "本章已经写回的线索"},
                    {"chapter_number": 1, "clue_text": "上一章的线索"},
                ]
            }
        )
    )
    captured = {}

    def fake_checks(_text, items, _chapter_number):
        captured["items"] = items
        return []

    import app.engines.fcip_detector as detector_module
    import app.engines.fcip_engine as engine_module

    monkeypatch.setattr(engine_module, "_lazy_clue_service", lambda: clue_service)
    monkeypatch.setattr(detector_module, "run_deterministic_checks", fake_checks)

    result = await FCIPEngine(foreshadowing_service=foreshadowing).post_gen_writeback(
        uuid.uuid4(),
        2,
        "本章已经写回的线索",
        db=SimpleNamespace(),
        apply_writeback=False,
    )

    assert result["violations"] == []
    assert captured["items"][0]["recent_clue_texts"] == ["上一章的线索"]
