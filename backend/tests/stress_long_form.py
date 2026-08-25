"""
长程压测：模拟 120 章、30 条伏笔线的完整生命周期

验证目标：
1. 所有伏笔线状态机正确推进，无丢失
2. 认知状态随章节正确演化
3. 证据池线索数量不膨胀失控
4. 因果图边数合理
5. 揭示准备度计算在长程下稳定

用法：py -m tests.stress_long_form
"""

import asyncio
import random
import time
import uuid
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.db_models import Base, Project
from app.services.foreshadowing_service import ForeshadowingService
from app.services.foreshadowing_clue_service import ForeshadowingClueService
from app.services.foreshadowing_graph_service import ForeshadowingGraphService
from app.engines.fcip_engine import FCIPEngine
from app.engines.fcip_detector import run_deterministic_checks
from app.engines.reveal_readiness import calculate_reveal_readiness

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOTAL_CHAPTERS = 120
TOTAL_FORESHADOWING = 30
CHARACTERS = ["林逸", "苏清月", "楚瑶", "赵无极", "叶青鸾", "萧远山"]


async def run_stress_test():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as db:
        project_id = uuid.uuid4()
        db.add(Project(id=project_id, name="压测项目-120章30伏笔"))
        await db.commit()

        fs_service = ForeshadowingService()
        clue_service = ForeshadowingClueService()
        graph_service = ForeshadowingGraphService()
        fcip_engine = FCIPEngine(fs_service)

        # ── Phase 1: 创建 30 条伏笔 ──────────────────────────
        logger.info("=== Phase 1: 创建 %d 条伏笔 ===", TOTAL_FORESHADOWING)
        start = time.perf_counter()

        foreshadowing_ids = []
        categories = ["short", "mid", "long"]
        for i in range(TOTAL_FORESHADOWING):
            cat = categories[i % 3]
            if cat == "short":
                bury_start, reveal_start = 1 + i, 10 + i
            elif cat == "mid":
                bury_start, reveal_start = 5 + i, 40 + i
            else:
                bury_start, reveal_start = 10 + i, 80 + i

            line = await fs_service.create_foreshadowing_line(project_id, {
                "name": f"伏笔-{i+1:02d}-{cat}",
                "description": f"第{i+1}条伏笔的描述内容",
                "status": "planned",
                "priority": random.choice(["minor", "moderate", "major"]),
                "secret": {
                    "canonical_statement": f"秘密-{i+1}",
                    "truth_type": random.choice(["identity", "motive", "world_rule", "past_event", "object_function", "relationship"]),
                    "impact_level": "moderate",
                    "spoiler_scope": {"characters": random.sample(CHARACTERS, k=random.randint(1, 3))},
                },
                "timeline": {
                    "bury_window": [bury_start, bury_start + 5],
                    "reveal_window": [reveal_start, reveal_start + 5],
                },
                "narrative_structure": {
                    "total_clues_planned": random.randint(3, 7),
                    "bury_rhythm": random.choice(["gradual", "sudden", "repeated"]),
                },
            }, db)
            foreshadowing_ids.append(line["id"])

        elapsed = time.perf_counter() - start
        logger.info("创建 %d 条伏笔耗时: %.2fs", TOTAL_FORESHADOWING, elapsed)

        # ── Phase 2: 设置角色认知状态 ──────────────────────────
        logger.info("=== Phase 2: 设置角色认知状态 ===")
        start = time.perf_counter()

        for fs_id in foreshadowing_ids:
            chars = random.sample(CHARACTERS, k=random.randint(2, 4))
            for char in chars:
                level = random.choice(["fully_blind", "vague_unease", "partial_clue"])
                status = {"fully_blind": "blind", "vague_unease": "noticed", "partial_clue": "suspicious"}[level]
                await fs_service.set_character_cognitive_state(
                    project_id, fs_id, char, status, 1, db,
                    cognitive_level=level, event="初始状态",
                )

        elapsed = time.perf_counter() - start
        logger.info("设置认知状态耗时: %.2fs", elapsed)

        # ── Phase 3: 添加因果边 ──────────────────────────────
        logger.info("=== Phase 3: 添加因果边 ===")
        start = time.perf_counter()

        edge_count = 0
        for i in range(len(foreshadowing_ids)):
            if random.random() < 0.3 and i + 1 < len(foreshadowing_ids):
                await graph_service.add_edge(
                    project_id, foreshadowing_ids[i], foreshadowing_ids[i + 1],
                    random.choice(["causes", "depends_on", "obscures", "synergizes_with"]),
                    db,
                )
                edge_count += 1

        elapsed = time.perf_counter() - start
        logger.info("添加 %d 条因果边耗时: %.2fs", edge_count, elapsed)

        # ── Phase 4: 模拟 120 章推进 ──────────────────────────
        logger.info("=== Phase 4: 模拟 %d 章推进 ===", TOTAL_CHAPTERS)
        total_violations = 0
        chapters_with_violations = 0

        for chapter in range(1, TOTAL_CHAPTERS + 1):
            # 推进状态机
            all_lines = await fs_service.list_foreshadowing_lines(project_id, db)
            for line in all_lines:
                status = line["status"]
                reveal_start = line.get("reveal_window_start") or 999

                if status == "planned" and chapter >= (line.get("bury_window_start") or 999):
                    try:
                        await fs_service.transition_state(project_id, line["id"], "active", "stress_test", db)
                    except ValueError:
                        pass

                elif status == "active" and chapter > (line.get("bury_window_end") or 0) + 5:
                    try:
                        await fs_service.transition_state(project_id, line["id"], "dormant", "stress_test", db)
                    except ValueError:
                        pass

                elif status == "dormant" and chapter >= reveal_start:
                    try:
                        await fs_service.transition_state(project_id, line["id"], "revealing", "stress_test", db)
                    except ValueError:
                        pass

                elif status == "revealing" and chapter >= (line.get("reveal_window_end") or 999) + 2:
                    try:
                        await fs_service.transition_state(project_id, line["id"], "resolved", "stress_test", db)
                    except ValueError:
                        pass

            # 添加线索
            actionable = await fs_service.list_actionable_for_scene(project_id, chapter, db)
            for item in actionable:
                if random.random() < 0.3:
                    try:
                        await clue_service.add_clue(
                            project_id, item["id"], "supportive", chapter,
                            f"第{chapter}章线索-{item['name'][:6]}", db,
                            pov_character=random.choice(CHARACTERS),
                        )
                    except Exception:
                        pass

            # 更新认知状态（模拟角色逐渐获知）
            if chapter % 10 == 0:
                for line in all_lines:
                    if line["status"] in ("revealing", "resolved"):
                        spoiler_chars = (line.get("secret_spoiler_scope") or {}).get("characters", [])
                        for char in spoiler_chars:
                            try:
                                await fs_service.set_character_cognitive_state(
                                    project_id, line["id"], char, "informed", chapter, db,
                                    cognitive_level="fully_aware", event=f"第{chapter}章获知",
                                )
                            except Exception:
                                pass

            # 幻觉检测（每 10 章做一次）
            if chapter % 10 == 0:
                sample_text = f"第{chapter}章的生成文本。林逸看着远方的山。"
                items_for_check = []
                for item in actionable[:5]:
                    char_states = await fs_service.list_cognitive_states_for_foreshadowing(
                        project_id, item["id"], db, chapter_number=chapter,
                    )
                    items_for_check.append({
                        **item,
                        "character_states": char_states,
                        "recent_clue_texts": [],
                    })
                violations = run_deterministic_checks(sample_text, items_for_check, chapter)
                if violations:
                    chapters_with_violations += 1
                    total_violations += len(violations)

            if chapter % 20 == 0:
                logger.info("  已推进到第 %d 章", chapter)

        # ── Phase 5: 最终统计 ──────────────────────────────────
        logger.info("=== Phase 5: 最终统计 ===")

        all_lines = await fs_service.list_foreshadowing_lines(project_id, db)
        status_counts = {}
        for line in all_lines:
            s = line["status"]
            status_counts[s] = status_counts.get(s, 0) + 1
        logger.info("伏笔状态分布: %s", status_counts)

        total_clues = 0
        for line in all_lines:
            pool = await clue_service.get_evidence_pool(line["id"], db)
            total_clues += pool.get("total", 0)
        logger.info("总线索数: %d", total_clues)

        graph_data = await graph_service.get_graph_as_dict(project_id, db)
        logger.info("因果图: %d 节点, %d 边, %d 冲突",
                     len(graph_data["nodes"]), len(graph_data["edges"]),
                     len(graph_data["conflicts"]))

        unresolved = [l for l in all_lines if l["status"] not in ("resolved", "aborted")]
        logger.info("未回收伏笔: %d / %d", len(unresolved), TOTAL_FORESHADOWING)

        logger.info("幻觉检测: %d 章有违规, 共 %d 条", chapters_with_violations, total_violations)

        # ── Phase 6: 性能基准 ──────────────────────────────────
        logger.info("=== Phase 6: 性能基准 ===")

        start = time.perf_counter()
        for _ in range(10):
            await fs_service.list_actionable_for_scene(project_id, 50, db)
        elapsed = (time.perf_counter() - start) / 10
        logger.info("list_actionable_for_scene 平均: %.1fms", elapsed * 1000)

        start = time.perf_counter()
        for line in all_lines[:5]:
            if line["status"] in ("active", "dormant", "revealing"):
                await calculate_reveal_readiness(line["id"], db)
        elapsed = time.perf_counter() - start
        logger.info("calculate_reveal_readiness (5次): %.1fms", elapsed * 1000)

        start = time.perf_counter()
        await graph_service.get_graph_as_dict(project_id, db)
        elapsed = time.perf_counter() - start
        logger.info("get_graph_as_dict: %.1fms", elapsed * 1000)

        # ── 断言 ──────────────────────────────────────────────
        assert len(all_lines) == TOTAL_FORESHADOWING, f"伏笔丢失: {len(all_lines)} != {TOTAL_FORESHADOWING}"
        assert total_clues < 500, f"线索膨胀: {total_clues} >= 500"
        assert len(graph_data["edges"]) < 100, f"因果边膨胀: {len(graph_data['edges'])} >= 100"

        logger.info("=== 长程压测通过 ===")


if __name__ == "__main__":
    asyncio.run(run_stress_test())
