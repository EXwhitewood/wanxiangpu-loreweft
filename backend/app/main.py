from contextlib import asynccontextmanager
import asyncio
import logging
import os

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.services.alert_service import get_alert_service

logging.basicConfig(level=logging.INFO)
from app.api import projects, chapters, state, settings as settings_api, outline, agents, editor_chat, worldbuilding, style as style_api, search, workflows, intelligence, export, chat_history, foreshadowing, weave, generation_trace, progressions, project_health, generation_quality, generation_features, reader_corpus, chapter_extractor, methodology, deslop_gate, chapter_planning, benchmark, market_intelligence, editor_decision_context, review_finding, editor_trace, writing_assistance, writing_companion


async def _recover_pending_validator_retries():
    try:
        from app.db.db_models import async_session, WorkflowExecution
        from sqlalchemy import select
        async with async_session() as db:
            result = await db.execute(
                select(WorkflowExecution).where(
                    WorkflowExecution.status == "pending_validator_retry"
                )
            )
            executions = result.scalars().all()
            if not executions:
                return {"ok": True, "recovered": 0, "finalized": 0}
            recovered = 0
            finalized = 0
            for execution in executions:
                persisted = execution.result_context or {}
                validator_retry = persisted.get("validator_retry") or {}
                resume_state = persisted.get("resume_state") or {}
                delays = validator_retry.get("validator_retry_delays", [5, 15, 45])
                delayed_attempts = int(validator_retry.get("validator_retry_delayed_attempts", 0))
                remaining_delays = delays[delayed_attempts:]
                if not remaining_delays:
                    execution.status = "waiting_human_system_review"
                    execution.error_message = "审校器连续故障，延迟复验耗尽，需要人工确认系统状态"
                    scene_index = int(validator_retry.get("scene_index", 0))
                    validator_retry["validator_retry_delayed_attempts"] = max(
                        delayed_attempts,
                        len(delays),
                    )
                    review = editor_chat._build_system_review(
                        scene_index=scene_index,
                        candidate_text=validator_retry.get("candidate_text", ""),
                        validator_retry=validator_retry,
                        resume_state=resume_state,
                    )
                    resume_state["review_version"] = review["review_version"]
                    execution.result_context = {
                        "review": review,
                        "resume_state": resume_state,
                        "validator_retry": validator_retry,
                    }
                    await db.commit()
                    finalized += 1
                    continue
                editor_chat._schedule_delayed_validator_retries(
                    execution_id=execution.id,
                    project_id=str(execution.project_id),
                    delays=remaining_delays,
                )
                recovered += 1
            await db.commit()
            logging.getLogger(__name__).info(
                "Recovered %d pending_validator_retry executions, finalized %d",
                recovered, finalized,
            )
            return {"ok": True, "recovered": recovered, "finalized": finalized}
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "Failed to recover pending validator retries: %s", exc,
        )
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:240]}


async def _recover_committing_chapters():
    try:
        from app.db.db_models import async_session
        from app.services.chapter_commit_service import (
            recover_committing_chapters,
            recover_pending_chapter_effects,
        )

        async with async_session() as db:
            recovered = await recover_committing_chapters(db)
            outbox = await recover_pending_chapter_effects(db)
            if recovered:
                logging.getLogger(__name__).info(
                    "Recovered %d committing chapters from effect outbox", recovered,
                )
            return {"ok": True, "recovered": recovered, "outbox": outbox}
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "Failed to recover committing chapters: %s", exc,
        )
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:240]}


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.db.sqlite import create_tables as sqlite_create_tables
    await sqlite_create_tables()
    # 标准桌面模式仍使用 SQLAlchemy async session 管理生成、追踪、outbox 回放
    # 和较新的叙事表。这些 ORM 管理的表也落在同一个 SQLite 数据库中。
    from app.db.db_models import create_tables as orm_create_tables
    await orm_create_tables()
    from app.db.db_models import engine as orm_engine
    from app.services.database_integrity_service import repair_sqlite_foreign_keys
    database_integrity = await repair_sqlite_foreign_keys(orm_engine)
    from app.services.sqlite_fts_service import SqliteFtsService
    fts = SqliteFtsService()
    await fts.ensure_index()

    startup_checks = {
        "database_integrity": database_integrity.to_dict(),
        "validator_retry_recovery": await _recover_pending_validator_retries(),
        "chapter_commit_recovery": await _recover_committing_chapters(),
    }
    from app.db.db_models import async_session
    try:
        from app.services.chapter_diagnostic_service import get_chapter_diagnostic_service
        async with async_session() as diagnostic_db:
            diagnostic_compaction = await get_chapter_diagnostic_service().compact_legacy_reference_snapshots(
                diagnostic_db
            )
            if diagnostic_compaction["compacted"]:
                await diagnostic_db.commit()
            startup_checks["diagnostic_snapshot_compaction"] = {
                "ok": True,
                **diagnostic_compaction,
            }
    except Exception as exc:
        logging.getLogger(__name__).warning("Diagnostic snapshot compaction failed: %s", exc)
        startup_checks["diagnostic_snapshot_compaction"] = {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}"[:240],
        }
    from app.services.user_chapter_settlement_job_service import recover_user_chapter_settlements
    async with async_session() as settlement_db:
        startup_checks["user_chapter_settlement_recovery"] = {
            "ok": True,
            **(await recover_user_chapter_settlements(settlement_db)),
        }
    from app.db.db_models import Project
    from app.services.project_chapter_aggregate_service import refresh_project_totals
    from sqlalchemy import select
    from sqlalchemy.orm import load_only
    async with async_session() as aggregate_db:
        aggregate_projects = list((
            await aggregate_db.execute(
                select(Project).options(
                    load_only(Project.id, Project.total_words, Project.current_chapter)
                )
            )
        ).scalars().all())
        aggregate_changed = await refresh_project_totals(aggregate_db, aggregate_projects)
        if aggregate_changed:
            await aggregate_db.commit()
        startup_checks["project_aggregate_backfill"] = {
            "ok": True,
            "projects": len(aggregate_projects),
            "changed": aggregate_changed,
        }

    # 附录4问题6修复：启动时清理卡死的 running 工作流和步骤
    try:
        from app.services.editor_generation_dag_runtime import EditorGenerationDAGRuntime
        await EditorGenerationDAGRuntime.recover_interrupted()
        logging.getLogger(__name__).info("recover_interrupted completed at startup")
        startup_checks["workflow_interruption_recovery"] = {"ok": True}
    except Exception as exc:
        logging.getLogger(__name__).warning("recover_interrupted at startup failed: %s", exc, exc_info=True)
        startup_checks["workflow_interruption_recovery"] = {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}"[:240],
        }

    app.state.startup_checks = startup_checks

    try:
        yield
    finally:
        from app.services.sqlite_fts_service import SqliteFtsService
        from app.services.state_manager import StateManager
        from app.services.user_chapter_settlement_job_service import shutdown_user_chapter_settlement_tasks

        await shutdown_user_chapter_settlement_tasks()
        await StateManager._sqlite_manager.close()
        await SqliteFtsService().close()


app = FastAPI(
    title="万象谱 Loreweft",
    description="""
## 智能小说创作引擎 API

万象谱是一个模拟专业小说家完整认知与创作流程的 AI 原生系统。

### 核心能力
- **Core-Shell 记忆宫殿**：分层存储世界知识，保证一致性
- **显式状态层**：实时追踪人物、地点、物品状态
- **多智能体编排**：主编Agent协调生成、校验、状态更新等Agent协作
- **宪法级大纲**：一次性生成并锁定大纲，确保剧情不偏离

### 使用流程
1. 创建项目并配置 API 密钥（设置页）
2. 录入人物卡、世界观（Core层）
3. 生成并冻结大纲
4. 逐章/逐场景生成正文
5. 通过状态编辑器精细调控
""",
    version=settings.app_version,
    lifespan=lifespan,
    redirect_slashes=False,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(projects.router, prefix="/api/projects", tags=["项目"])
app.include_router(chapters.router, prefix="/api/projects/{project_id}/chapters", tags=["章节"])
app.include_router(state.router, prefix="/api/projects/{project_id}/state", tags=["状态"])
app.include_router(settings_api.router, prefix="/api/settings", tags=["设置"])
app.include_router(outline.router, prefix="/api/outline", tags=["大纲"])
app.include_router(agents.router, prefix="/api/agents", tags=["智能体"])
app.include_router(editor_chat.router, prefix="/api/editor", tags=["主编工作流"])
app.include_router(writing_companion.router, prefix="/api/writing-companion", tags=["墨伴"])
app.include_router(worldbuilding.router, prefix="/api/worldbuilding", tags=["世界观设定"])
app.include_router(style_api.router, prefix="/api/style", tags=["文笔风格"])
app.include_router(search.router, prefix="/api/search", tags=["全文检索"])
app.include_router(workflows.router, prefix="/api", tags=["工作流"])
app.include_router(intelligence.router, prefix="/api/projects/{project_id}/intelligence", tags=["情报分析"])
app.include_router(export.router, prefix="/api/projects/{project_id}/export", tags=["导入导出"])
app.include_router(chat_history.router, prefix="/api/projects/{project_id}", tags=["聊天历史"])
app.include_router(foreshadowing.router, prefix="/api/projects/{project_id}/foreshadowing", tags=["伏笔管理"])
app.include_router(weave.router, prefix="/api/projects/{project_id}/weave", tags=["织梦协调"])
app.include_router(generation_trace.router, prefix="/api/projects/{project_id}", tags=["生成轨迹"])
app.include_router(progressions.router, prefix="/api/projects/{project_id}", tags=["状态演进"])
app.include_router(project_health.router, prefix="/api/projects/{project_id}", tags=["项目健康"])
app.include_router(generation_quality.router, prefix="/api/projects/{project_id}", tags=["生成质量"])
app.include_router(generation_features.router, prefix="/api/projects/{project_id}", tags=["生成增强"])
app.include_router(writing_assistance.router, prefix="/api/projects/{project_id}/writing-assistance", tags=["写作辅助"])
app.include_router(reader_corpus.router, prefix="/api/admin/reader-corpus", tags=["Reader Corpus"])
app.include_router(chapter_extractor.router, prefix="/api/chapter-extractor", tags=["章节抽取"])
app.include_router(methodology.router, prefix="/api/methodology", tags=["方法论"])
app.include_router(deslop_gate.router, prefix="/api/deslop", tags=["去AI味"])
app.include_router(chapter_planning.router, prefix="/api/chapter-planning", tags=["章级规划"])
app.include_router(benchmark.router, prefix="/api/benchmark", tags=["对标拆解"])
app.include_router(market_intelligence.router, prefix="/api/market-intelligence", tags=["市场情报"])
app.include_router(editor_decision_context.router, prefix="/api/editor-decision-context", tags=["主编决策上下文"])
app.include_router(review_finding.router, prefix="/api/review-finding", tags=["统一审查发现"])
app.include_router(editor_trace.router, prefix="/api/editor-trace", tags=["主编诊断追踪"])


@app.get(
    "/api/health",
    summary="健康检查",
    description="检查服务是否正常运行。返回服务状态和版本号。",
)
async def health_check(response: Response):
    from sqlalchemy import text

    from app.db.db_models import async_session

    checks = dict(getattr(app.state, "startup_checks", {}) or {})
    try:
        async with async_session() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = {"ok": True}
    except Exception as exc:
        checks["database"] = {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}"[:240],
        }

    healthy = bool(checks) and all(
        isinstance(result, dict) and result.get("ok") is True
        for result in checks.values()
    )
    if not healthy:
        response.status_code = 503
    return {
        "status": "ok" if healthy else "degraded",
        "version": settings.app_version,
        # The desktop host uses this together with the executable path of the
        # owning process before it adopts or terminates a backend left behind
        # by an abnormal UI exit.  A PID marker on disk is only a hint and is
        # never sufficient process identity on its own.
        "process_id": os.getpid(),
        "checks": checks,
    }


@app.get(
    "/api/metrics/llm",
    summary="LLM 调用指标",
    description="导出 LLM 调用指标（次数/耗时/重试/活跃数/token 用量）。",
)
async def llm_metrics():
    from app.services.llm_metrics import get_llm_metrics
    return get_llm_metrics().to_dict()


@app.get(
    "/api/alerts",
    summary="告警列表",
    description="返回所有告警（按时间倒序）。",
)
async def list_alerts():
    return {"alerts": get_alert_service().list_alerts()}


@app.delete(
    "/api/alerts",
    summary="清空告警",
    description="清空所有告警，返回被清除的数量。",
)
async def clear_alerts():
    cleared = get_alert_service().clear_alerts()
    return {"cleared": cleared}
