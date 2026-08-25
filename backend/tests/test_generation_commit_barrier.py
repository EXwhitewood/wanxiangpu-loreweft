from unittest.mock import AsyncMock

import pytest

from app.services import scene_generation_pipeline as generation
from app.engines import fcip_engine
from app.engines.fcip_engine import FCIPEngine


def test_editor_contract_normalizer_rejects_non_list_and_non_object_items():
    assert generation.normalize_editor_scene_contracts({"scene_id": "s1"}) == []
    assert generation.normalize_editor_scene_contracts([{"scene_id": "s1"}, "bad"]) == [
        {"scene_id": "s1"}
    ]


def test_append_final_violation_detail_includes_blocking_reason():
    result = {
        "consistency_report": {
            "quality_gate": {
                "violations": [
                    {
                        "type": "canon_timeline_confusion",
                        "detail": "当前时间线与原著命运混淆",
                        "blocks_commit": True,
                    }
                ]
            }
        }
    }

    message = generation._append_final_violation_detail(
        "场景存在不可修复的违规",
        result,
        "recovery_non_repairable",
    )

    assert "canon_timeline_confusion" in message
    assert "当前时间线与原著命运混淆" in message


@pytest.mark.asyncio
async def test_fcip_detection_can_run_without_writeback(monkeypatch):
    service = AsyncMock()
    service.list_actionable_for_scene.return_value = [
        {
            "id": "fs-1",
            "status": "revealing",
            "secret_spoiler_scope": {"characters": ["凤溪"]},
        }
    ]
    service.list_cognitive_states_for_foreshadowing.return_value = []

    clue_service = AsyncMock()
    clue_service.get_evidence_pool.return_value = {"clues": []}
    monkeypatch.setattr(fcip_engine, "_lazy_clue_service", lambda: clue_service)

    engine = FCIPEngine(service)
    await engine.post_gen_writeback(
        project_id="00000000-0000-0000-0000-000000000001",
        chapter_number=1,
        generated_text="正文",
        db=AsyncMock(),
        apply_writeback=False,
    )

    service.set_character_cognitive_state.assert_not_awaited()

    await engine.post_gen_writeback(
        project_id="00000000-0000-0000-0000-000000000001",
        chapter_number=1,
        generated_text="正文",
        db=AsyncMock(),
        apply_writeback=True,
    )

    service.set_character_cognitive_state.assert_awaited_once()


@pytest.mark.asyncio
async def test_persist_scene_effects_normalizes_seed_and_preserves_patch(monkeypatch):
    shell = AsyncMock()
    monkeypatch.setattr(generation, "ShellMemoryService", lambda: shell)

    state_manager = AsyncMock()
    monkeypatch.setattr(generation, "StateManager", lambda: state_manager)

    monkeypatch.setattr(
        fcip_engine.FCIPEngine,
        "post_gen_writeback",
        AsyncMock(return_value={"violations_found": 0}),
    )

    from app.services import memory_core

    core_service = AsyncMock()
    core_service.list_foreshadowing.return_value = []
    monkeypatch.setattr(memory_core, "CoreMemoryService", lambda: core_service)

    patch = {"completed_events": ["离开宗门"]}
    db = AsyncMock()
    await generation.persist_scene_effects(
        project_id="00000000-0000-0000-0000-000000000001",
        chapter_number=2,
        scene_index=1,
        generated_text="正文",
        effects={
            "detail_seeds": [{"entity_id": "凤溪", "fact": "凤溪已离开宗门"}],
            "state_patch": patch,
        },
        db=db,
    )

    stored_seed = shell.store_detail_seed.await_args.args[1]
    assert stored_seed["chapter_number"] == 2
    assert stored_seed["scene_number"] == 2
    assert patch == {"completed_events": ["离开宗门"]}
    state_manager.apply_patch.assert_awaited_once()
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_persist_scene_effects_stops_before_best_effort_when_state_patch_fails(monkeypatch):
    shell = AsyncMock()
    monkeypatch.setattr(generation, "ShellMemoryService", lambda: shell)

    state_manager = AsyncMock()
    state_manager.apply_patch.side_effect = RuntimeError("state store unavailable")
    monkeypatch.setattr(generation, "StateManager", lambda: state_manager)

    with pytest.raises(RuntimeError, match="Required effects failed"):
        await generation.persist_scene_effects(
            project_id="00000000-0000-0000-0000-000000000001",
            chapter_number=2,
            scene_index=1,
            generated_text="正文",
            effects={
                "detail_seeds": [{"entity_id": "凤溪", "fact": "凤溪离开宗门"}],
                "state_patch": {"completed_events": ["离开宗门"]},
            },
            db=AsyncMock(),
        )

    shell.store_detail_seed.assert_not_awaited()
