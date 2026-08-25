"""Regression tests for parallel workflow DB session isolation.

The editor DAG runs sibling scene nodes with ``asyncio.gather``. Those nodes
must not share one live SQLAlchemy ``AsyncSession``; each node should open its
own short-lived session, and workers should receive a session factory instead
of a request-scoped session.
"""

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.quality_gate import QualityGate


def _make_factory(engine) -> async_sessionmaker:
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest.mark.asyncio
async def test_parallel_workers_use_distinct_sessions(engine):
    factory = _make_factory(engine)
    seen_session_ids: dict[int, int] = {}

    async def worker(scene_idx: int) -> int:
        async with factory() as node_db:
            (await node_db.execute(text("SELECT 1"))).scalar_one()
            seen_session_ids[scene_idx] = id(node_db)
            await asyncio.sleep(0.02)
            return id(node_db)

    scene_count = 4
    results = await asyncio.gather(*(worker(i) for i in range(scene_count)))

    assert len(results) == scene_count
    assert len(set(results)) == scene_count
    assert set(seen_session_ids) == set(range(scene_count))


@pytest.mark.asyncio
async def test_quality_gate_fcip_opens_distinct_session_per_call(engine, monkeypatch):
    factory = _make_factory(engine)
    used_session_ids: list[int] = []
    gate = QualityGate()

    async def _fake_run_foreshadowing_post_check(**kwargs):
        db = kwargs["db"]
        (await db.execute(text("SELECT 1"))).scalar_one()
        used_session_ids.append(id(db))
        await asyncio.sleep(0.02)
        return {"violations_found": 0, "violations": []}

    monkeypatch.setattr(
        "app.services.scene_generation_pipeline.run_foreshadowing_post_check",
        _fake_run_foreshadowing_post_check,
    )

    project = object()
    calls = [
        gate._run_fcip(
            f"text-{i}",
            {},
            project,
            "proj",
            1,
            i,
            factory,
            text_hash=f"h{i}",
            review_version=1,
        )
        for i in range(3)
    ]
    results = await asyncio.gather(*calls)

    assert len(results) == 3
    assert all(isinstance(result, dict) for result in results)
    assert len(set(used_session_ids)) == 3
