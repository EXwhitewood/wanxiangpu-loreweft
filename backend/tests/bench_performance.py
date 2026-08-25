"""
性能基准测试：测量伏笔系统关键路径的延迟

用法：py -m tests.bench_performance
"""

import asyncio
import time
import uuid
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.db_models import Base, Project
from app.services.foreshadowing_service import ForeshadowingService
from app.services.foreshadowing_clue_service import ForeshadowingClueService
from app.services.foreshadowing_graph_service import ForeshadowingGraphService
from app.engines.fcip_engine import FCIPEngine, FCIPContext
from app.engines.fcip_detector import run_deterministic_checks
from app.engines.reveal_readiness import calculate_reveal_readiness

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ITERATIONS = 50


async def _async_wrap(sync_fn, *args, **kwargs):
    return sync_fn(*args, **kwargs)


async def bench(name: str, coro_factory, iterations: int = ITERATIONS):
    times = []
    for _ in range(iterations):
        start = time.perf_counter()
        await coro_factory()
        elapsed = time.perf_counter() - start
        times.append(elapsed)

    avg = sum(times) / len(times) * 1000
    p50 = sorted(times)[len(times) // 2] * 1000
    p95 = sorted(times)[int(len(times) * 0.95)] * 1000
    logger.info("%-45s avg=%.1fms  p50=%.1fms  p95=%.1fms", name, avg, p50, p95)
    return {"name": name, "avg_ms": avg, "p50_ms": p50, "p95_ms": p95}


async def run_benchmarks():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as db:
        project_id = uuid.uuid4()
        db.add(Project(id=project_id, name="基准测试项目"))
        await db.commit()

        fs_service = ForeshadowingService()
        clue_service = ForeshadowingClueService()
        graph_service = ForeshadowingGraphService()
        fcip_engine = FCIPEngine(fs_service)

        # 准备数据
        line_ids = []
        for i in range(20):
            line = await fs_service.create_foreshadowing_line(project_id, {
                "name": f"bench-{i:02d}",
                "description": f"基准测试伏笔{i}",
                "status": "active",
                "secret": {
                    "canonical_statement": f"秘密{i}",
                    "truth_type": "past_event",
                    "impact_level": "moderate",
                    "spoiler_scope": {"characters": ["林逸"]},
                },
                "timeline": {
                    "bury_window": [1, 20],
                    "reveal_window": [80, 100],
                },
                "narrative_structure": {"total_clues_planned": 5},
            }, db)
            line_ids.append(line["id"])

        for fs_id in line_ids:
            await fs_service.set_character_cognitive_state(
                project_id, fs_id, "林逸", "blind", 1, db,
            )
            for ch in range(1, 6):
                await clue_service.add_clue(
                    project_id, fs_id, "supportive", ch * 5,
                    f"第{ch}条线索", db, pov_character="林逸",
                )

        for i in range(len(line_ids) - 1):
            if i % 3 == 0:
                await graph_service.add_edge(
                    project_id, line_ids[i], line_ids[i + 1], "depends_on", db,
                )

        # ── 基准测试 ──────────────────────────────────────────
        results = []

        results.append(await bench(
            "ForeshadowingService.list_actionable_for_scene",
            lambda: fs_service.list_actionable_for_scene(project_id, 50, db),
        ))

        results.append(await bench(
            "ForeshadowingService.list_foreshadowing_lines",
            lambda: fs_service.list_foreshadowing_lines(project_id, db),
        ))

        results.append(await bench(
            "ForeshadowingService.get_foreshadowing_by_id",
            lambda: fs_service.get_foreshadowing_by_id(line_ids[0], db),
        ))

        results.append(await bench(
            "ForeshadowingService.transition_state",
            lambda: _safe_transition(fs_service, project_id, line_ids, db),
        ))

        results.append(await bench(
            "ForeshadowingClueService.get_evidence_pool",
            lambda: clue_service.get_evidence_pool(line_ids[0], db),
        ))

        results.append(await bench(
            "ForeshadowingGraphService.get_graph_as_dict",
            lambda: graph_service.get_graph_as_dict(project_id, db),
        ))

        results.append(await bench(
            "ForeshadowingGraphService.detect_conflicts",
            lambda: graph_service.detect_conflicts(project_id, db),
        ))

        results.append(await bench(
            "FCIPEngine.compile_for_scene (20 items)",
            lambda: _async_wrap(fcip_engine.compile_for_scene,
                FCIPContext(project_id=project_id, chapter_number=50, scene_index=0, pov_character="林逸")),
        ))

        results.append(await bench(
            "fcip_detector.run_deterministic_checks (5 items)",
            lambda: _async_wrap(run_deterministic_checks,
                "他看着远方的山，什么也没说。",
                [{"name": "t", "secret_canonical_statement": "秘密", "character_states": [], "recent_clue_texts": []}] * 5),
        ))

        results.append(await bench(
            "calculate_reveal_readiness",
            lambda: calculate_reveal_readiness(line_ids[0], db),
            iterations=20,
        ))

        # ── 输出汇总 ──────────────────────────────────────────
        logger.info("=" * 80)
        logger.info("性能基准汇总 (SQLite 内存, %d 次迭代)", ITERATIONS)
        logger.info("=" * 80)
        for r in results:
            logger.info("  %-45s avg=%6.1fms  p50=%6.1fms  p95=%6.1fms",
                        r["name"], r["avg_ms"], r["p50_ms"], r["p95_ms"])

        # ── 目标检查 ──────────────────────────────────────────
        targets = {
            "FCIPEngine.compile_for_scene (20 items)": 200,
            "fcip_detector.run_deterministic_checks (5 items)": 1000,
            "ForeshadowingGraphService.get_graph_as_dict": 500,
        }
        for r in results:
            target = targets.get(r["name"])
            if target and r["avg_ms"] > target:
                logger.warning("⚠ %s: avg=%.1fms 超过目标 %dms", r["name"], r["avg_ms"], target)

        logger.info("=== 基准测试完成 ===")


async def _safe_transition(service, project_id, line_ids, db):
    line = await service.get_foreshadowing_by_id(line_ids[0], db)
    if not line:
        return
    status = line["status"]
    if status == "active":
        try:
            await service.transition_state(project_id, line_ids[0], "dormant", "bench", db)
        except ValueError:
            pass
    elif status == "dormant":
        try:
            await service.transition_state(project_id, line_ids[0], "revealing", "bench", db)
        except ValueError:
            pass
    elif status == "revealing":
        try:
            await service.transition_state(project_id, line_ids[0], "resolved", "bench", db)
        except ValueError:
            pass
    else:
        try:
            await service.transition_state(project_id, line_ids[0], "revised", "bench", db)
            await service.transition_state(project_id, line_ids[0], "active", "bench", db)
        except ValueError:
            pass


if __name__ == "__main__":
    asyncio.run(run_benchmarks())
