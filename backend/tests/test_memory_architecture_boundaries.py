import asyncio
import sqlite3
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.config import settings
from app.models.character import CharacterCreate
from app.services.core_entity_resolver import CoreEntityResolver
from app.services.narrative_proposition_extractor import align_exact_source_span, split_proposition_chunks
from app.services.narrative_proposition_extractor import NarrativePropositionExtractor
from app.models.narrative_proposition import AuditReport, ExtractionResult
from app.services.proposition_selector import PropositionSelector
from app.services.structured_memory_compiler import StructuredMemoryCompiler
from app.services.worldview_extractor import WorldviewExtractor, split_worldview_chunks


def test_character_lifecycle_and_activity_are_separate_and_safe_by_default():
    unknown = CharacterCreate(name="路人")
    assert unknown.lifecycle_status == "unknown"
    assert unknown.narrative_activity == "dormant"
    assert unknown.status == "unknown"

    protagonist = CharacterCreate(name="主角", role="主角", status="active")
    assert protagonist.lifecycle_status == "alive"
    assert protagonist.role_importance == "protagonist"
    assert protagonist.narrative_activity == "core_active"


def test_proposition_source_alignment_returns_original_exact_span():
    source = "凤溪握紧玉佩，抬头看向裂缝。"
    assert align_exact_source_span("凤溪握紧玉佩, 抬头看向裂缝", source) == "凤溪握紧玉佩，抬头看向裂缝"


def test_proposition_source_alignment_recovers_high_confidence_paraphrase_only():
    source = "五个人悬浮在裂缝下方。黑色魔气沿着他们的腿向上蔓延。"
    assert align_exact_source_span(
        "五个人悬在裂缝下方",
        source,
        anchors=["五个人", "悬浮", "裂缝"],
    ) == "五个人悬浮在裂缝下方"
    assert align_exact_source_span(
        "祭坛已经彻底毁灭",
        source,
        anchors=["祭坛", "毁灭"],
    ) is None


def test_proposition_source_alignment_preserves_multi_span_evidence_window():
    source = (
        "五位师兄从她四周飞了出去。中间发生了完整且必须保留的动作。"
        "黑色的魔气同时缠住了他们。"
    )
    aligned = align_exact_source_span(
        "五位师兄从她四周飞了出去。……黑色的魔气同时缠住了他们。",
        source,
        anchors=["五位师兄", "缠住", "魔气"],
    )
    assert aligned == source


def test_proposition_source_alignment_preserves_dialogue_attribution():
    source = "“你的封印不是用来锁我的，”魔君说，“是用来锁你自己。”"
    aligned = align_exact_source_span(
        "“你的封印不是用来锁我的，是用来锁你自己。”",
        source,
        anchors=["魔君", "封印"],
    )
    assert aligned == "你的封印不是用来锁我的，”魔君说，“是用来锁你自己"


def test_core_entity_resolver_matches_id_name_and_alias_without_fuzzy_guessing():
    cards = [
        {"id": "2", "name": "林鹤", "aliases": ["小鹤"]},
        {"id": "1", "name": "苏晚", "aliases": ["晚晚"]},
    ]
    assert CoreEntityResolver.resolve_cards(cards, "1", entity_type="character").canonical_name == "苏晚"
    assert CoreEntityResolver.resolve_cards(cards, " 林 鹤 ", entity_type="character").matched_by == "name"
    assert CoreEntityResolver.resolve_cards(cards, "晚晚", entity_type="character").canonical_name == "苏晚"
    assert CoreEntityResolver.resolve_cards(cards, "不存在", entity_type="character") is None


def test_full_text_chunkers_are_lossless():
    text = ("第一段。" * 900) + "\n\n" + ("第二段！" * 900) + "尾声"
    proposition_chunks = split_proposition_chunks(text, max_chars=500)
    assert "".join(chunk for _, _, chunk in proposition_chunks) == text
    assert proposition_chunks[0][0] == 0
    assert proposition_chunks[-1][1] == len(text)
    assert "".join(split_worldview_chunks(text, max_chars=500)) == text


@pytest.mark.asyncio
async def test_worldview_chunk_extraction_uses_bounded_parallelism(monkeypatch):
    empty_response = json.dumps({
        "characters": [],
        "locations": [],
        "world_rules": [],
        "foreshadowing": [],
        "facts": [],
        "items": [],
    })
    active = 0
    max_active = 0

    async def generate(*args, **kwargs):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.05)
        active -= 1
        return empty_response

    monkeypatch.setattr("app.services.memory_core.CoreMemoryService.list_characters", AsyncMock(return_value=[]))
    monkeypatch.setattr("app.services.memory_core.CoreMemoryService.list_locations", AsyncMock(return_value=[]))
    monkeypatch.setattr("app.services.memory_core.CoreMemoryService.list_world_rules", AsyncMock(return_value=[]))
    monkeypatch.setattr("app.services.memory_core.CoreMemoryService.list_items", AsyncMock(return_value=[]))
    monkeypatch.setattr(WorldviewExtractor, "_load_known_foreshadowing", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        "app.services.agent_config.AgentConfigManager.get_agent_config",
        AsyncMock(return_value=SimpleNamespace(
            api_format="openai",
            api_key="test",
            base_url="http://localhost",
            model="test-model",
        )),
    )
    monkeypatch.setattr("app.services.llm_client.LLMClient.generate", generate)

    result = await WorldviewExtractor().extract_from_chapter(
        "project-1",
        1,
        ("第一段。" * 700) + "\n\n" + ("第二段。" * 700),
    )

    assert result["_coverage"]["chunk_count"] >= 2
    assert result["_coverage"]["failed_chunks"] == []
    assert max_active == 2


def test_proposition_selector_uses_lifecycle_relevance_recency_and_trace():
    candidates = []
    for chapter in range(17, 24):
        candidates.append({
            "id": f"old-{chapter}",
            "status": "active",
            "chapter_number": chapter,
            "scene_index": 1,
            "subject_name": "无关角色",
            "predicate_name": "经过",
            "predicate_category": "event",
            "object_name": f"事件{chapter}",
            "truth_layer": "current",
            "certainty": "confirmed",
            "lifecycle_status": "historical_event",
            "importance": 0.2,
        })
    candidates.extend([
        {
            "id": "relevant-current",
            "status": "active",
            "chapter_number": 22,
            "scene_index": 2,
            "subject_name": "林鹤",
            "predicate_name": "持有",
            "predicate_category": "ownership",
            "object_name": "铜钥匙",
            "truth_layer": "current",
            "certainty": "confirmed",
            "lifecycle_status": "current",
            "importance": 0.9,
        },
        {
            "id": "recent-clue",
            "status": "active",
            "chapter_number": 23,
            "scene_index": 1,
            "subject_name": "铜钥匙",
            "predicate_name": "来源不明",
            "predicate_category": "clue",
            "truth_layer": "inference",
            "certainty": "unknown",
            "lifecycle_status": "unresolved",
        },
        {
            "id": "superseded",
            "status": "active",
            "chapter_number": 18,
            "scene_index": 1,
            "subject_name": "林鹤",
            "predicate_name": "持有",
            "predicate_category": "ownership",
            "object_name": "旧钥匙",
            "truth_layer": "current",
            "certainty": "confirmed",
            "lifecycle_status": "superseded",
        },
    ])
    result = PropositionSelector().select(
        candidates,
        chapter_number=24,
        scene_context={"characters": ["林鹤"], "location": "仓库", "item": "铜钥匙"},
        max_items=4,
    )
    ids = [item["id"] for item in result["selected"]]
    assert "relevant-current" in ids
    assert "recent-clue" in ids
    assert "superseded" not in ids
    assert result["trace"]["selected_by_chapter"].get("23", 0) >= 1
    assert result["trace"]["truncated"] is True
    assert result["trace"]["excluded_lifecycle_count"] == 1


def test_structured_memory_projects_state_instead_of_copying_canonical_blob():
    state = {
        "character_states": {f"角色{i}": {"location": f"地点{i}"} for i in range(20)},
        "established_facts": [f"事实{i}" for i in range(40)],
        "completed_events": [f"事件{i}" for i in range(40)],
        "active_constraints": [f"约束{i}" for i in range(40)],
        "scene_endings": ["大块正文"] * 100,
    }
    memory = StructuredMemoryCompiler().compile_memory(
        latest_state=state,
        active_propositions=[],
        relevant_entity_names=["角色19"],
        max_characters=4,
        max_facts=9,
    )
    assert "latest_chapter_state" not in memory
    assert "active_propositions" not in memory
    assert "scene_endings" not in memory["state_projection"]
    assert "角色19" in memory["state_projection"]["character_states"]
    assert memory["state_projection"]["selection_metadata"]["omitted_counts"]["character_states"] == 16


@pytest.mark.asyncio
async def test_fts_syncs_primary_store_and_supports_chinese_substrings(tmp_path):
    from app.services.sqlite_fts_service import SqliteFtsService

    db_path = tmp_path / "fts-sync.db"
    connection = sqlite3.connect(db_path)
    connection.execute(
        "CREATE TABLE detail_seeds("
        "id TEXT PRIMARY KEY, project_id TEXT, chapter_id TEXT, scene_number INTEGER, "
        "entity_id TEXT, fact TEXT, narrative_time TEXT, tier TEXT, source_text TEXT, "
        "entity_type TEXT, created_at TEXT)"
    )
    connection.execute(
        "INSERT INTO detail_seeds VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("seed-1", "project-1", "chapter-1", 1, "铜钥匙", "林鹤在仓库取得铜钥匙", "", "T1", "他拾起铜钥匙", "item", "2026-01-01"),
    )
    connection.commit()
    connection.close()

    service = SqliteFtsService()
    old_path = settings.sqlite_fts_path
    await service.close()
    settings.sqlite_fts_path = str(db_path)
    try:
        await service.ensure_index()
        rows = await service.search_text("project-1", "铜钥匙", limit=5)
        assert rows and rows[0]["entity_id"] == "铜钥匙"
        count = sqlite3.connect(db_path).execute("SELECT COUNT(*) FROM fts_detail_seeds").fetchone()[0]
        assert count == 1
    finally:
        await service.close()
        settings.sqlite_fts_path = old_path


@pytest.mark.asyncio
async def test_fts_normalizes_nullable_seed_fields_without_leaving_write_lock(tmp_path):
    from app.services.sqlite_fts_service import SqliteFtsService

    db_path = tmp_path / "fts-nullable-seed.db"
    service = SqliteFtsService()
    old_path = settings.sqlite_fts_path
    await service.close()
    settings.sqlite_fts_path = str(db_path)
    try:
        await service.ensure_index()
        first = await service.index_detail_seed({
            "id": "seed-null-time",
            "project_id": "project-1",
            "entity_id": "item-1",
            "fact": "The item remains in the character inventory.",
            "narrative_time": None,
            "source_text": None,
        })
        second = await service.index_detail_seed({
            "id": "seed-after-null",
            "project_id": "project-1",
            "entity_id": "item-2",
            "fact": "A later seed can still be indexed.",
        })

        assert first and first["result"] == "created"
        assert second and second["result"] == "created"
        connection = sqlite3.connect(db_path)
        try:
            row = connection.execute(
                "SELECT narrative_time, source_text FROM fts_detail_seeds WHERE id = ?",
                ("seed-null-time",),
            ).fetchone()
        finally:
            connection.close()
        assert row == ("", "")
    finally:
        await service.close()
        settings.sqlite_fts_path = old_path


@pytest.mark.asyncio
async def test_proposition_extractor_calls_llm_for_every_lossless_chunk():
    text = ("甲在山门停下。" * 1100) + "尾声。"
    chunks = split_proposition_chunks(text)
    responses = []
    for index, (_, _, chunk) in enumerate(chunks):
        responses.append(json.dumps({
            "propositions": [{
                "subject": {"name": f"人物{index}", "entity_type": "character"},
                "predicate": {"name": "出现", "category": "event"},
                "object": None,
                "truth_layer": "current",
                "certainty": "confirmed",
                "polarity": "affirmed",
                "responsibility": "active_actor",
                "source_text": chunk[:6],
                "confidence": 0.9,
            }]
        }, ensure_ascii=False))
    llm = AsyncMock()
    llm.generate.side_effect = responses
    extractor = NarrativePropositionExtractor()
    with patch.object(extractor, "_get_llm_client", return_value=llm):
        result = await extractor.extract({
            "generated_text": text,
            "project_id": "project",
            "chapter_number": 24,
            "scene_index": 0,
            "strict_source_text": True,
        })
    assert llm.generate.await_count == len(chunks)
    assert result.complete is True
    assert result.covered_chars == len(text)
    assert result.chunk_count == len(chunks)


@pytest.mark.asyncio
async def test_incomplete_proposition_extraction_is_fail_closed_by_mode():
    from app.services.quality_gate import QualityGate

    incomplete = ExtractionResult(
        propositions=[],
        extractor_warnings=["incomplete_extraction"],
        input_chars=100,
        covered_chars=60,
        chunk_count=2,
        failed_chunks=[1],
        complete=False,
    )
    context = {"generated_text": "正文", "fact_contract": {}}
    with patch(
        "app.services.narrative_proposition_extractor.NarrativePropositionExtractor.extract",
        new=AsyncMock(return_value=incomplete),
    ):
        _, report_mode, _, report_violations = await QualityGate()._run_proposition_layer(
            dict(context),
            {"hard_correctness_mode": "report", "proposition_audit_mode": "report"},
            "hash",
            1,
        )
        _, enforce_mode, _, enforce_violations = await QualityGate()._run_proposition_layer(
            dict(context),
            {"hard_correctness_mode": "enforce", "proposition_audit_mode": "enforce"},
            "hash",
            1,
        )
    assert report_mode.passed is False and report_mode.commit_blocked is False
    assert report_violations[0]["blocks_commit"] is False
    assert enforce_mode.passed is False and enforce_mode.commit_blocked is True
    assert enforce_violations[0]["blocks_commit"] is True


@pytest.mark.asyncio
async def test_targeted_validator_retry_runs_only_proposition_layer():
    from app.services.quality_gate import QualityGate

    gate = QualityGate()
    extraction = ExtractionResult(complete=True)
    audit = AuditReport(passed=True, commit_blocked=False)
    gate._run_proposition_layer = AsyncMock(
        return_value=(extraction, audit, {}, []),
    )
    gate.evaluate = AsyncMock(side_effect=AssertionError("full gate must not run"))

    result = await gate.evaluate_validator_retry(
        {
            "generated_text": "candidate",
            "scene_contract": {"scene_id": "scene_1"},
            "fact_contract": {},
            "generation_feature_policy": {
                "proposition_extraction_mode": "enforce",
                "proposition_audit_mode": "enforce",
                "hard_correctness_mode": "enforce",
            },
        },
        {"proposition_extractor_unavailable"},
    )

    assert result["passed"] is True
    assert result["level_executed"] == "validator_retry:proposition"
    assert result["validator_retry_scope"] == "proposition"
    gate._run_proposition_layer.assert_awaited_once()
    gate.evaluate.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_validator_retry_type_keeps_full_gate_fallback():
    from app.services.quality_gate import QualityGate

    gate = QualityGate()
    gate.evaluate = AsyncMock(return_value={"passed": True, "level_executed": "full"})
    result = await gate.evaluate_validator_retry(
        {"generated_text": "candidate", "scene_contract": {"scene_id": "scene_1"}},
        {"parallel_review_unavailable"},
    )

    assert result["level_executed"] == "full"
    gate.evaluate.assert_awaited_once()


@pytest.mark.asyncio
async def test_proposition_extractor_retries_partial_parse_with_exact_source_text():
    text = "凤溪握紧玉佩，抬头看向裂缝。"
    malformed = json.dumps({
        "propositions": [{
            "subject": {"name": "凤溪", "entity_type": "character"},
            "predicate": {"name": "握紧", "category": "action"},
            "object": {"name": "玉佩", "entity_type": "item"},
            "truth_layer": "current",
            "certainty": "confirmed",
            "polarity": "affirmed",
            "responsibility": "active_actor",
            "source_text": "凤溪拿紧了玉佩",
            "confidence": 0.9,
        }]
    }, ensure_ascii=False)
    repaired = json.dumps({
        "propositions": [{
            "subject": {"name": "凤溪", "entity_type": "character"},
            "predicate": {"name": "握紧", "category": "action"},
            "object": {"name": "玉佩", "entity_type": "item"},
            "truth_layer": "current",
            "certainty": "confirmed",
            "polarity": "affirmed",
            "responsibility": "active_actor",
            "source_text": "凤溪握紧玉佩",
            "confidence": 0.9,
        }]
    }, ensure_ascii=False)
    llm = AsyncMock()
    llm.generate.side_effect = [malformed, repaired]
    extractor = NarrativePropositionExtractor()

    with patch.object(extractor, "_get_llm_client", return_value=llm):
        result = await extractor.extract({
            "generated_text": text,
            "project_id": "project",
            "chapter_number": 24,
            "scene_index": 0,
            "strict_source_text": True,
        })

    assert llm.generate.await_count == 2
    retry_kwargs = llm.generate.await_args_list[1].kwargs
    assert retry_kwargs["max_tokens"] == 2048
    assert result.complete is True
    assert len(result.propositions) == 1
    assert result.propositions[0].source_text == "凤溪握紧玉佩"


@pytest.mark.asyncio
async def test_proposition_extractor_uses_bounded_second_validator_retry():
    text = "凤溪握紧玉佩，抬头看向裂缝。"
    malformed = json.dumps({
        "propositions": [{
            "subject": {"name": "凤溪", "entity_type": "character"},
            "predicate": {"name": "握紧", "category": "action"},
            "object": {"name": "玉佩", "entity_type": "item"},
            "truth_layer": "current",
            "certainty": "confirmed",
            "polarity": "affirmed",
            "responsibility": "active_actor",
            "source_text": "凤溪拿紧了玉佩",
            "confidence": 0.9,
        }]
    }, ensure_ascii=False)
    repaired_result = ExtractionResult(
        propositions=[NarrativePropositionExtractor()._build_proposition(
            {
                "subject": {"name": "凤溪", "entity_type": "character"},
                "predicate": {"name": "握紧", "category": "action"},
                "object": {"name": "玉佩", "entity_type": "item"},
                "truth_layer": "current",
                "certainty": "confirmed",
                "polarity": "affirmed",
                "responsibility": "active_actor",
                "source_text": "凤溪握紧玉佩",
                "confidence": 0.9,
            },
            "project",
            24,
            0,
            text,
            True,
        )],
    )
    llm = AsyncMock()
    llm.generate.return_value = malformed
    extractor = NarrativePropositionExtractor()

    with (
        patch.object(extractor, "_get_llm_client", return_value=llm),
        patch.object(
            extractor,
            "_validator_retry",
            new=AsyncMock(side_effect=[None, repaired_result]),
        ) as retry,
    ):
        result = await extractor.extract({
            "generated_text": text,
            "project_id": "project",
            "chapter_number": 24,
            "scene_index": 0,
            "strict_source_text": True,
        })

    assert retry.await_count == 2
    assert result.complete is True
    assert result.extractor_warnings == []
    assert result.propositions[0].source_text == "凤溪握紧玉佩"
