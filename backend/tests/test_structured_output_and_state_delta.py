from app.models.state_delta import ChapterStateDelta, EntityDelta
from app.services.prose_lint_service import ProseLintService
from app.services.state_delta_reducer import StateDeltaReducer
from app.services.structured_output_parser import StructuredOutputParser


def test_structured_output_parser_strips_fence_and_trailing_comma():
    parsed = StructuredOutputParser.parse('```json\n{"a": 1,}\n```')
    assert parsed.data == {"a": 1}
    assert "strip_code_fence" in parsed.repairs_applied


def test_state_delta_reducer_merges_entity_delta_into_patch():
    delta = ChapterStateDelta(
        project_id="p1",
        chapter_number=2,
        scene_index=0,
        entity_deltas=[
            EntityDelta(
                entity_name="赵小蝶",
                delta_type="update",
                attribute_changes={"location": "讲经堂", "mood": "紧张"},
                evidence_span="赵小蝶站在讲经堂门侧。",
            )
        ],
        completed_events=["铜钱交到风溪手中"],
    )

    patch = StateDeltaReducer().reduce({"objective_state": {}}, delta)

    assert patch["objective_state"]["赵小蝶"]["location"] == "讲经堂"
    assert patch["objective_state"]["赵小蝶"]["extra"]["mood"] == "紧张"
    assert patch["completed_events"] == ["铜钱交到风溪手中"]


def test_prose_lint_detects_hard_and_soft_findings():
    text = "第3章\n这不是恐惧而是清醒。\n核心动机浮出水面。"
    findings = ProseLintService().lint(text)
    types = {finding.type for finding in findings}

    assert "chapter_reference" in types
    assert "analysis_jargon" in types
    assert "not_but_pattern" in types
    assert any(finding.blocks_commit for finding in findings)


def test_prose_lint_blocks_malformed_action_aspect_particle():
    findings = ProseLintService().lint("五师兄的动作顿了一下了，随后收回手。")

    assert [(finding.type, finding.target_span, finding.blocks_commit) for finding in findings] == [
        ("malformed_aspect_particle", "顿了一下了", True),
    ]

