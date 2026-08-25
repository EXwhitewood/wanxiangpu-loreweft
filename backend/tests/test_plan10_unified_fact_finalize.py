"""Fact extraction and persistence must converge on the chapter outbox."""

from pathlib import Path


BACKEND_APP = Path(__file__).resolve().parents[1] / "app"


def _function_body(path: Path, signature: str) -> str:
    content = path.read_text(encoding="utf-8")
    start = content.find(signature)
    assert start >= 0, f"function not found: {signature}"
    candidates = [
        index
        for marker in ("\nasync def ", "\ndef ")
        if (index := content.find(marker, start + len(signature))) >= 0
    ]
    end = min(candidates) if candidates else len(content)
    return content[start:end]


def test_editor_fact_preparation_has_no_precommit_persistence():
    body = _function_body(
        BACKEND_APP / "api" / "editor_chat.py",
        "async def _prepare_scene_facts_for_commit(",
    )

    assert "extract_chapter_facts" in body
    assert "finalize_from_writer_facts" not in body


def test_editor_retains_extraction_fallback_but_records_its_source():
    content = (BACKEND_APP / "api" / "editor_chat.py").read_text(encoding="utf-8")
    pipeline = (BACKEND_APP / "services" / "scene_generation_pipeline.py").read_text(encoding="utf-8")

    assert "deterministic_extractor_fallback" in pipeline
    assert '"fact_source": _fact_source' in content


def test_scene_postprocess_does_not_repeat_worldview_extraction():
    body = _function_body(
        BACKEND_APP / "services" / "scene_generation_pipeline.py",
        "async def run_scene_postprocess_fanout(",
    )

    assert "WorldviewExtractor" not in body
    assert '"worldview_extractions"' not in body


def test_persist_scene_effects_has_no_parallel_shell_writeback():
    body = _function_body(
        BACKEND_APP / "services" / "scene_generation_pipeline.py",
        "async def persist_scene_effects(",
    )
    active_lines = [
        line.strip()
        for line in body.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    assert not any("write_to_shell" in line for line in active_lines)
    assert not any("apply_writeback=True" in line for line in active_lines)
