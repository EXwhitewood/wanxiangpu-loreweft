from app.models.benchmark import (
    BenchmarkBook,
    BenchmarkChapter,
    BenchmarkDeconstructionResult,
    BenchmarkStrategyCard,
)
from app.models.chapter_plan_contract import ChapterPlanContract, ChapterPlanSequence
from app.models.market_intelligence import TopicDecision
from app.services.benchmark_deconstruction import BenchmarkDeconstructionService
from app.services.context_ledger_service import ContextLedgerService
from app.services.editor_planning_compiler import EditorPlanningCompiler
from app.services.editor_trace_collector import EditorTraceCollector
from app.services.market_intelligence import MarketIntelligenceService


def _envelope(task_id: str) -> dict:
    return {
        "task_id": task_id,
        "blocks": [],
        "token_report": {},
        "model_context_profile": {},
    }


def test_context_ledger_purge_removes_only_target_chapter(tmp_path):
    service = ContextLedgerService(base_dir=tmp_path)
    service.record(
        project_id="project-1",
        chapter_number=1,
        task_id="chapter-1",
        agent="chapter_writer",
        envelope=_envelope("chapter-1"),
    )
    service.record(
        project_id="project-1",
        chapter_number=2,
        task_id="chapter-2",
        agent="chapter_writer",
        envelope=_envelope("chapter-2"),
    )

    assert service.purge("project-1", 2) == 2
    assert service.latest("project-1", 2) is None
    assert service.latest("project-1", 1)["task_id"] == "chapter-1"


def test_editor_trace_and_plan_purge_are_restart_safe(tmp_path):
    trace_dir = tmp_path / "traces"
    collector = EditorTraceCollector(storage_dir=trace_dir)
    first = collector.start_trace("project-1", 1, "execution-1")
    second = collector.start_trace("project-1", 2, "execution-2")
    collector.finish_trace(first.trace_id)
    collector.finish_trace(second.trace_id)
    assert collector.purge("project-1", 2) == 1
    assert EditorTraceCollector(storage_dir=trace_dir).get_trace(second.trace_id) is None

    plan_dir = tmp_path / "plans"
    compiler = EditorPlanningCompiler(storage_dir=plan_dir)
    compiler._plans["project-1"] = ChapterPlanSequence(
        project_id="project-1",
        chapters=[
            ChapterPlanContract(chapter_number=1),
            ChapterPlanContract(chapter_number=2),
        ],
    )
    compiler._save_plan("project-1")
    assert compiler.purge("project-1", 2) == 1
    reloaded = EditorPlanningCompiler(storage_dir=plan_dir)
    assert reloaded.get_chapter_plan("project-1", 1) is not None
    assert reloaded.get_chapter_plan("project-1", 2) is None


def test_benchmark_completed_result_survives_service_restart(tmp_path):
    service = BenchmarkDeconstructionService(storage_dir=tmp_path)
    book = service.register_book(BenchmarkBook(id="book-1", title="sample"))
    service.set_chapters(
        book.id,
        [BenchmarkChapter(chapter_number=1, chapter_title="opening", summary="A choice is made.")],
    )
    service._strategy_cards[book.id] = [
        BenchmarkStrategyCard(
            id="strategy-1",
            book_id=book.id,
            title="chapter hook",
            strategy="close on an unresolved choice",
        )
    ]
    service._deconstruction_results[book.id] = BenchmarkDeconstructionResult(
        book_id=book.id,
        status="completed",
        progress=1.0,
        strategy_cards=service._strategy_cards[book.id],
    )
    service._save_to_disk()

    reloaded = BenchmarkDeconstructionService(storage_dir=tmp_path)
    assert reloaded.get_deconstruction_result(book.id).status == "completed"
    assert reloaded.get_strategy_cards(book.id)[0].id == "strategy-1"


def test_market_project_purge_preserves_other_projects(tmp_path):
    service = MarketIntelligenceService(storage_dir=tmp_path)
    service.create_topic_decision(TopicDecision(id="one", project_id="project-1"))
    service.create_topic_decision(TopicDecision(id="two", project_id="project-2"))

    assert service.purge_project("project-1") == 1
    reloaded = MarketIntelligenceService(storage_dir=tmp_path)
    assert reloaded.list_topic_decisions("project-1") == []
    assert [item.id for item in reloaded.list_topic_decisions("project-2")] == ["two"]
