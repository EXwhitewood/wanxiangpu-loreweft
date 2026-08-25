import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.db_models import Base, Project
from app.db.db_models import Chapter, ChapterBaseline, ChapterDiagnosticRecord, ChapterSnapshot
from app.models.story_state import StoryState
from app.models.writing_assistance import (
    ChapterAdvisoryCheckResponse,
    ChapterAdvisoryFinding,
    ChapterAdvisorySource,
)
from app.services.chapter_diagnostic_service import ChapterDiagnosticService
from app.services.user_chapter_settlement_service import UserChapterSettlementService


@pytest.mark.asyncio
async def test_high_outline_deviation_is_persisted_and_applies_real_amendment():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    project_id = uuid.uuid4()
    service = ChapterDiagnosticService()
    try:
        async with sessions() as db:
            project = Project(
                id=project_id,
                name="diagnostic-test",
                outline_data={
                    "_version": 1,
                    "_frozen": True,
                    "chapter_spine": [
                        {"chapter_number": 1, "core_event": "主角留在宗门"}
                    ],
                },
                core_data={
                    "chapter_summaries": {
                        "1": {
                            "core_event": "主角主动离开宗门",
                            "key_turning_point": "拒绝执法堂邀请",
                            "unsolved_suspense": "山门外有人等候",
                            "summary_text": "主角改变原计划离开宗门。",
                        }
                    }
                },
            )
            db.add(project)
            await db.commit()

            response = ChapterAdvisoryCheckResponse(
                trigger="save",
                revision_hash="revision-1",
                checked_dimensions=["outline_deviation"],
                findings=[
                    ChapterAdvisoryFinding(
                        finding_id="finding-1",
                        fingerprint="fingerprint-1",
                        revision_hash="revision-1",
                        category="outline_deviation",
                        severity="high",
                        title="核心事件偏离",
                        detail="正文改变了本章核心目标。",
                        evidence_quote="他转身走出了山门。",
                        source=ChapterAdvisorySource(
                            source_type="chapter_outline",
                            label="第一章核心事件",
                            excerpt="主角留在宗门",
                        ),
                        confidence=0.95,
                    )
                ],
            )
            records = await service.persist_response(
                db,
                project_id=project_id,
                chapter_number=1,
                response=response,
                reference_snapshot={"outline_data": project.outline_data, "outline_version": 1},
            )
            assert len(records) == 1
            record = records[0]
            assert record.available_actions == [
                "mark_fixed",
                "ignore",
                "ignore_and_amend_outline",
            ]
            proposal = record.decision_payload["proposed_outline_amendment"]
            assert proposal["status"] == "proposed"
            assert proposal["preview"]

            amendment_id = await service.apply_outline_amendment(
                db,
                project=project,
                chapter_number=1,
                record=record,
                reason="用户确认改变故事方向",
            )
            await db.commit()

            chapter = project.outline_data["chapter_spine"][0]
            assert chapter["core_event"] == "主角主动离开宗门"
            assert chapter["key_turning_point"] == "拒绝执法堂邀请"
            assert chapter["hook"] == "山门外有人等候"
            assert chapter["status"] == "written"
            assert chapter["recovery_generated"] is False
            assert chapter["core_conflict"]["desire"] == "主角主动离开宗门"
            assert chapter["user_override"]["source"] == "user_chapter_save"
            assert project.outline_data["scene_briefs"]["ch_001"]["scenes"]
            assert project.outline_data["_amendments"][-1]["id"] == amendment_id
            assert project.outline_data["_amendments"][-1]["status"] == "applied"
            assert project.outline_data["_version"] == 2
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_diagnostics_reference_shared_baseline_instead_of_copying_large_snapshot():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    project_id = uuid.uuid4()
    service = ChapterDiagnosticService()
    try:
        async with sessions() as db:
            project = Project(id=project_id, name="baseline-reference")
            chapter = Chapter(project_id=project_id, chapter_number=1, content="正文")
            baseline = ChapterBaseline(
                project_id=project_id,
                chapter_number=1,
                outline_version=3,
                baseline_story_state={"active_chapter": 1},
                baseline_chapter_state={
                    "_user_save_reference": {
                        "outline_data": {"large": "x" * 20_000},
                        "core_data": {},
                        "outline_version": 3,
                        "story_state": {"active_chapter": 1},
                    }
                },
                snapshot_source="user_chapter_save",
            )
            db.add_all([project, chapter, baseline])
            await db.commit()
            response = ChapterAdvisoryCheckResponse(
                status="complete",
                trigger="save",
                revision_hash="revision-shared",
                checked_dimensions=["fact"],
                findings=[
                    ChapterAdvisoryFinding(
                        finding_id="finding-shared",
                        fingerprint="fingerprint-shared",
                        revision_hash="revision-shared",
                        category="fact",
                        severity="low",
                        title="事实提醒",
                        detail="测试",
                        evidence_quote="正文",
                        source=ChapterAdvisorySource(source_type="test", label="test", excerpt=""),
                        confidence=1.0,
                    )
                ],
            )

            records = await service.persist_response(
                db,
                project_id=project_id,
                chapter_number=1,
                response=response,
                reference_snapshot={"outline_data": {"large": "x" * 20_000}},
            )
            await db.commit()

            record = records[0]
            assert str(record.reference_baseline_id) == str(baseline.id)
            assert record.reference_snapshot == {}
            resolved = await service.resolve_reference_snapshot(db, record)
            assert len(resolved["outline_data"]["large"]) == 20_000
            serialized = service.serialize(record)
            assert "reference_snapshot" not in serialized
            assert serialized["reference_available"] is True
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_legacy_diagnostic_snapshots_compact_only_on_exact_baseline_match():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    project_id = uuid.uuid4()
    try:
        async with sessions() as db:
            reference = {
                "outline_data": {"chapter_spine": [{"chapter_number": 1}]},
                "core_data": {},
                "outline_version": 4,
                "story_state": {"active_chapter": 1},
            }
            db.add(Project(id=project_id, name="legacy-diagnostic"))
            baseline = ChapterBaseline(
                project_id=project_id,
                chapter_number=1,
                outline_version=4,
                baseline_story_state={"active_chapter": 1},
                baseline_chapter_state={"_user_save_reference": reference},
                snapshot_source="user_chapter_save",
            )
            exact = ChapterDiagnosticRecord(
                project_id=project_id,
                chapter_number=1,
                chapter_revision_hash="exact-revision",
                fingerprint="exact-fingerprint",
                reference_snapshot=reference,
            )
            different = ChapterDiagnosticRecord(
                project_id=project_id,
                chapter_number=1,
                chapter_revision_hash="different-revision",
                fingerprint="different-fingerprint",
                reference_snapshot={**reference, "outline_version": 3},
            )
            db.add_all([baseline, exact, different])
            await db.commit()

            result = await ChapterDiagnosticService().compact_legacy_reference_snapshots(db)
            await db.commit()
            await db.refresh(exact)
            await db.refresh(different)

            assert result == {"examined": 2, "compacted": 1, "preserved": 1}
            assert str(exact.reference_baseline_id) == str(baseline.id)
            assert exact.reference_snapshot == {}
            assert different.reference_baseline_id is None
            assert different.reference_snapshot["outline_version"] == 3
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_user_chapter_settlement_updates_state_tasks_and_snapshot(monkeypatch):
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    project_id = uuid.uuid4()

    class FakeStateManager:
        state = StoryState(active_chapter=1, completed_events=[])

        async def get_state(self, _project_id):
            return self.state.model_copy(deep=True)

        async def set_state(self, _project_id, state):
            self.__class__.state = state.model_copy(deep=True)

        async def apply_patch(self, _project_id, patch):
            state = self.__class__.state.model_copy(deep=True)
            state.active_chapter = patch.get("active_chapter", state.active_chapter)
            state.active_scene = patch.get("active_scene", state.active_scene)
            for event in patch.get("completed_events") or []:
                if event not in state.completed_events:
                    state.completed_events.append(event)
            state.active_constraints = list(patch.get("active_constraints") or [])
            self.__class__.state = state
            return state.model_copy(deep=True)

    class FakeRepository:
        async def add_candidate(self, _db, **_kwargs):
            return {"id": "progression-1"}

    class FakeProgressionService:
        def __init__(self):
            self.repository = FakeRepository()

        async def detect_candidates(self, _db, **_kwargs):
            return {
                "mention_count": 1,
                "mentions": [
                    {
                        "entity_type": "character",
                        "entity_id": "林默",
                        "entity_name": "林默",
                        "alias": "",
                        "evidence_text": "林默离开宗门。",
                    }
                ],
            }

    import app.services.user_chapter_settlement_service as settlement_module

    monkeypatch.setattr(settlement_module, "StateManager", FakeStateManager)
    monkeypatch.setattr(settlement_module, "ProgressionService", FakeProgressionService)

    try:
        async with sessions() as db:
            project = Project(
                id=project_id,
                name="settlement-test",
                current_chapter=1,
                core_data={"characters": [{"name": "林默"}]},
                outline_data={"chapter_spine": [{"chapter_number": 1}]},
            )
            chapter = Chapter(
                project_id=project_id,
                chapter_number=1,
                title="第一章",
                content="林默离开宗门。山门在他身后合拢。",
            )
            db.add_all([project, chapter])
            await db.commit()

            result = await UserChapterSettlementService().settle(
                db,
                project=project,
                chapter=chapter,
                reference_snapshot={
                    "story_state": StoryState(active_chapter=1).model_dump(),
                    "outline_version": 0,
                },
                summary={
                    "core_event": "林默离开宗门",
                    "key_turning_point": "林默拒绝留下",
                    "character_changes": ["林默已经离开宗门"],
                    "unsolved_suspense": "必须在天黑前抵达山下",
                },
            )
            await db.commit()

            assert result["active_chapter"] == 2
            assert result["completed_events"] == 2
            assert result["character_states"] == 1
            assert result["progression_records"] == 1
            # Durable project counters are maintained by the prose write
            # transaction, not by this retryable auxiliary settlement. A
            # settlement replay must never advance them independently.
            assert project.current_chapter == 1
            baseline = await db.scalar(
                select(ChapterBaseline).where(ChapterBaseline.project_id == project_id)
            )
            snapshot = await db.scalar(
                select(ChapterSnapshot).where(ChapterSnapshot.project_id == project_id)
            )
            assert baseline.snapshot_source == "user_chapter_save"
            assert baseline.baseline_chapter_state["_user_save_reference"]["outline_version"] == 0
            assert snapshot.chapter_state["completed_events"] == [
                "林默离开宗门",
                "林默拒绝留下",
            ]
            assert snapshot.execution_id.startswith("user_save:")
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_successful_same_revision_recheck_stales_missing_open_findings():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    project_id = uuid.uuid4()
    service = ChapterDiagnosticService()
    try:
        async with sessions() as db:
            db.add(Project(id=project_id, name="same-revision-recheck"))
            await db.commit()
            initial = ChapterAdvisoryCheckResponse(
                status="complete",
                trigger="save",
                revision_hash="revision-1",
                checked_dimensions=["foreshadowing"],
                findings=[
                    ChapterAdvisoryFinding(
                        finding_id="finding-1",
                        fingerprint="fingerprint-1",
                        revision_hash="revision-1",
                        category="foreshadowing",
                        severity="low",
                        title="旧提醒",
                        detail="重检后已不存在",
                        evidence_quote="证据",
                        source=ChapterAdvisorySource(
                            source_type="test",
                            label="test",
                            excerpt="source",
                        ),
                        confidence=1.0,
                    )
                ],
            )
            records = await service.persist_response(
                db,
                project_id=project_id,
                chapter_number=1,
                response=initial,
            )
            await db.commit()
            assert records[0].status == "open"

            clean = ChapterAdvisoryCheckResponse(
                status="complete",
                trigger="save",
                revision_hash="revision-1",
                checked_dimensions=["foreshadowing"],
            )
            assert await service.persist_response(
                db,
                project_id=project_id,
                chapter_number=1,
                response=clean,
            ) == []
            await db.commit()
            await db.refresh(records[0])
            assert records[0].status == "stale"
    finally:
        await engine.dispose()
