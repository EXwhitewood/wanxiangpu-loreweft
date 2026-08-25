from app.services.generation_candidate_service import (
    build_chapter_candidate_payload,
    classify_candidate_block,
    split_candidate_violations,
)
from app.utils.word_count import count_words


def test_chapter_writer_soft_quality_block_becomes_report_only():
    decision = classify_candidate_block(
        chapter_writer_mode=True,
        generated_text="A complete chapter draft.",
        error_code="commit_blocked",
        commit_blocked=True,
        violations=[
            {
                "type": "ai_style_artifact",
                "source": "deslop_gate",
                "severity": "high",
                "blocks_commit": True,
            }
        ],
    )

    assert decision["block"] is False
    assert decision["reason"] == "quality_report_only"
    assert decision["hard_count"] == 0
    assert decision["soft_count"] == 1


def test_chapter_writer_hard_fact_conflict_still_blocks():
    decision = classify_candidate_block(
        chapter_writer_mode=True,
        generated_text="A complete chapter draft.",
        error_code="commit_blocked",
        commit_blocked=True,
        violations=[
            {
                "type": "fact_conflict",
                "source": "hard_correctness",
                "severity": "critical",
                "blocks_commit": True,
            }
        ],
    )

    assert decision["block"] is True
    assert decision["reason"] == "hard_quality_blocker"
    assert decision["hard_count"] == 1


def test_empty_chapter_writer_candidate_blocks_even_without_violations():
    decision = classify_candidate_block(
        chapter_writer_mode=True,
        generated_text="",
        error_code="",
        commit_blocked=False,
        violations=[],
    )

    assert decision["block"] is True
    assert decision["reason"] == "empty_candidate"


def test_scene_writer_keeps_legacy_blocking_rules():
    decision = classify_candidate_block(
        chapter_writer_mode=False,
        generated_text="A scene draft.",
        error_code="commit_blocked",
        commit_blocked=True,
        violations=[
            {
                "type": "ai_style_artifact",
                "source": "deslop_gate",
                "severity": "high",
                "blocks_commit": True,
            }
        ],
    )

    assert decision["block"] is True
    assert decision["reason"] == "legacy_scene_writer_rules"


def test_candidate_payload_contains_alignment_and_text():
    payload = build_chapter_candidate_payload(
        chapter_number=2,
        candidate_text="Scene one.\n\nScene two.",
        scene_count=2,
        alignment={"method": "explicit_markers", "warnings": []},
        context_ledger_id="ledger-1",
    )

    assert payload["status"] == "candidate_ready"
    assert payload["chapter_number"] == 2
    assert payload["scene_count"] == 2
    assert payload["alignment_method"] == "explicit_markers"
    assert payload["context_ledger_id"] == "ledger-1"
    assert payload["candidate_text"].startswith("Scene one")
    assert payload["word_count"] == count_words("Scene one.\n\nScene two.")


def test_split_candidate_violations_separates_hard_and_soft():
    split = split_candidate_violations([
        {"type": "fact_conflict", "blocks_commit": True},
        {"type": "weak_hook_out", "blocks_commit": True},
        {"type": "advisory_note", "blocks_commit": False},
    ])

    assert split["hard_count"] == 1
    assert split["soft_count"] == 2
