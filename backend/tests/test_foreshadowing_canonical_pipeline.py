from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.config import _absolute_backend_path, settings
from app.db.db_models import ForeshadowingClue, ForeshadowingLine
from app.services.foreshadowing_upsert_service import (
    ForeshadowingUpsertService,
    normalize_foreshadowing_operation,
)
from app.services.narrative_sync_service import NarrativeSyncService
from app.services.structured_memory_compiler import StructuredMemoryCompiler


def test_legacy_foreshadowing_operations_converge_to_one_vocabulary():
    assert normalize_foreshadowing_operation("new") == "plant"
    assert normalize_foreshadowing_operation("advance") == "reinforce"
    assert normalize_foreshadowing_operation("escalate") == "reinforce"
    assert normalize_foreshadowing_operation("pay_off") == "reveal"
    assert normalize_foreshadowing_operation("conflict") == "defer"
    assert normalize_foreshadowing_operation("abandoned") == "abandon"


@pytest.mark.asyncio
async def test_plant_and_alias_reinforcement_share_one_line_and_deduplicate_evidence(
    db,
    project_in_db,
):
    service = ForeshadowingUpsertService()
    planted = await service.upsert_from_observation(
        db,
        project_in_db,
        3,
        [{
            "name": "镜中第三只手",
            "aliases": ["镜中手"],
            "operation": "new",
            "description": "镜面中出现不属于现场的手。",
            "evidence_text": "镜面里多出了一只苍白的手。",
            "confidence": 0.95,
        }],
    )
    assert len(planted) == 1
    assert planted[0]["reveal_window_start"] is None

    reinforced = await service.upsert_from_observation(
        db,
        project_in_db,
        4,
        [{
            "name": "镜中手",
            "operation": "supplement",
            "description": "同一只手再次出现。",
            "evidence_text": "那只手又一次掠过镜面。",
            "confidence": 0.9,
        }],
    )
    await service.upsert_from_observation(
        db,
        project_in_db,
        4,
        [{
            "name": "镜中手",
            "operation": "supplement",
            "description": "同一只手再次出现。",
            "evidence_text": "那只手又一次掠过镜面。",
            "confidence": 0.9,
        }],
    )
    await db.commit()

    assert reinforced[0]["id"] == planted[0]["id"]
    assert await db.scalar(
        select(func.count()).select_from(ForeshadowingLine).where(
            ForeshadowingLine.project_id == project_in_db
        )
    ) == 1
    assert await db.scalar(
        select(func.count()).select_from(ForeshadowingClue).where(
            ForeshadowingClue.foreshadowing_line_id == planted[0]["id"]
        )
    ) == 2


@pytest.mark.asyncio
async def test_reveal_requires_evidence_and_resolves_in_one_atomic_call(db, project_in_db):
    service = ForeshadowingUpsertService()
    planted = (
        await service.upsert_from_observation(
            db,
            project_in_db,
            2,
            [{
                "name": "空棺之谜",
                "operation": "plant",
                "evidence_text": "棺中没有尸体。",
                "description": "尸体去向成谜。",
                "confidence": 0.92,
            }],
        )
    )[0]

    awaiting = (
        await service.upsert_from_outline(
            db,
            project_in_db,
            8,
            [{
                "name": "空棺之谜",
                "foreshadowing_id": planted["id"],
                "action": "reveal",
                "description": "计划在本章回收。",
            }],
        )
    )[0]
    assert awaiting["status"] == "revealing"
    assert awaiting["resolved_chapter"] is None

    resolved = (
        await service.upsert_from_observation(
            db,
            project_in_db,
            8,
            [{
                "name": "空棺之谜",
                "foreshadowing_id": planted["id"],
                "operation": "reveal",
                "evidence_text": "死者推门走了进来，承认空棺是他布置的。",
                "description": "死者以假死布置空棺。",
                "confidence": 0.99,
            }],
        )
    )[0]
    await db.commit()

    assert resolved["status"] == "resolved"
    assert resolved["resolved_chapter"] == 8
    assert "假死" in resolved["resolution_summary"] or "死者" in resolved["resolution_summary"]
    reveal_clue = (
        await db.execute(
            select(ForeshadowingClue).where(
                ForeshadowingClue.foreshadowing_line_id == planted["id"],
                ForeshadowingClue.is_revealed_clue.is_(True),
            )
        )
    ).scalar_one()
    assert reveal_clue.revealed_in_chapter == 8


@pytest.mark.asyncio
async def test_unknown_reveal_does_not_create_a_new_line(db, project_in_db):
    result = await ForeshadowingUpsertService().upsert_from_observation(
        db,
        project_in_db,
        9,
        [{
            "name": "不存在的旧伏笔",
            "operation": "reveal",
            "evidence_text": "一个没有前置身份的揭示。",
            "confidence": 0.99,
        }],
    )
    assert result == []
    assert await db.scalar(
        select(func.count()).select_from(ForeshadowingLine).where(
            ForeshadowingLine.project_id == project_in_db
        )
    ) == 0


@pytest.mark.asyncio
async def test_retracting_a_chapter_removes_evidence_not_the_canonical_line(db, project_in_db):
    service = ForeshadowingUpsertService()
    line = (
        await service.upsert_from_observation(
            db,
            project_in_db,
            5,
            [{
                "name": "门后的呼吸",
                "operation": "plant",
                "evidence_text": "门后传来第二个人的呼吸。",
                "confidence": 0.95,
            }],
        )
    )[0]
    removed = await service.retract_chapter_evidence(db, project_in_db, 5)
    await db.commit()

    assert removed == 1
    persisted = await db.get(ForeshadowingLine, line["id"])
    assert persisted is not None
    assert persisted.status == "revised"
    assert persisted.clues_placed == 0


def test_narrative_sync_preserves_operations_from_outline_summary_and_scene_packages():
    actions = NarrativeSyncService()._collect_foreshadowing_actions(
        chapter_number=7,
        chapter_entry={
            "foreshadowing_actions": [
                {"name": "旧钥匙", "action": "plant", "description": "埋设钥匙"}
            ]
        },
        summary={
            "foreshadowing_updates": [
                {"name": "旧钥匙", "operation": "mention", "description": "再次出现"}
            ]
        },
        scene_packages=[{
            "foreshadowing_operations": [{
                "name": "旧钥匙",
                "operation": "payoff",
                "evidence_text": "钥匙打开了密室。",
            }]
        }],
    )
    assert len(actions) == 1
    assert actions[0]["action"] == "reveal"
    assert actions[0]["evidence_text"] == "钥匙打开了密室。"


def test_structured_memory_ranks_before_capping():
    lines = [
        {
            "id": f"line-{index}",
            "name": f"普通伏笔{index}",
            "status": "active",
            "priority": "low",
            "reveal_window_start": 50,
            "reveal_window_end": 55,
        }
        for index in range(20)
    ]
    lines.append({
        "id": "urgent",
        "name": "已经逾期的伏笔",
        "status": "dormant",
        "priority": "critical",
        "reveal_window_start": 5,
        "reveal_window_end": 6,
    })
    summary = StructuredMemoryCompiler()._summarize_foreshadowing(
        lines,
        chapter_number=10,
        max_items=2,
    )
    assert summary["items"][0]["foreshadowing_id"] == "urgent"
    assert summary["items"][0]["action"] == "overdue_reveal"


def test_sqlite_paths_are_independent_of_process_working_directory():
    backend_root = Path(__file__).resolve().parents[1]
    assert Path(settings.sqlite_path).is_absolute()
    assert Path(_absolute_backend_path("data/loreweft.db")).resolve() == (
        backend_root / "data" / "loreweft.db"
    ).resolve()
