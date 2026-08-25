import copy

import pytest

from app.services.outline_continuity_recovery_service import (
    OutlineContinuityRecoveryError,
    OutlineContinuityRecoveryService,
)


def _anchor(chapter_number: int, title: str) -> dict:
    return {
        "chapter_id": f"ch_{chapter_number:03d}",
        "chapter_number": chapter_number,
        "title": title,
        "conflict_text": f"第 {chapter_number} 章的原始冲突",
        "hook": f"第 {chapter_number} 章的原始钩子",
        "pov_character": "主角",
    }


def test_sparse_outline_becomes_reviewable_contiguous_draft_without_mutating_source():
    source = {
        "story_constitution": {"logline": "原始故事"},
        "chapter_spine": [_anchor(1, "开端"), _anchor(4, "转折")],
        "chapters": [
            {"chapter_number": 1, "title": "开端"},
            {"chapter_number": 4, "title": "转折"},
        ],
        "scene_briefs": {
            "ch_001": {
                "chapter_id": "ch_001",
                "scenes": [{"scene_id": "authored", "goal": "保留作者场景"}],
            }
        },
        "_frozen": False,
        "_frozen_at": "stale-value",
        "_version": 3,
    }
    before = copy.deepcopy(source)

    draft, summary = OutlineContinuityRecoveryService().build_draft(source)

    assert source == before
    assert [item["chapter_number"] for item in draft["chapter_spine"]] == [1, 2, 3, 4]
    assert draft["chapter_spine"][0]["title"] == "开端"
    assert draft["chapter_spine"][3]["conflict_text"] == "第 4 章的原始冲突"
    assert all(draft["chapter_spine"][index]["status"] == "needs_review" for index in (1, 2))
    assert all(draft["chapter_spine"][index]["recovery_generated"] for index in (1, 2))
    assert draft["scene_briefs"]["ch_001"]["scenes"][0]["scene_id"] == "authored"
    assert "_frozen" not in draft
    assert "_frozen_at" not in draft
    assert "_version" not in draft
    assert draft["meta"]["continuity_recovery"]["requires_review"] is True
    assert summary["anchor_chapters"] == [1, 4]
    assert summary["added_chapters"] == [2, 3]
    assert summary["chapter_continuity"]["valid"] is True


@pytest.mark.parametrize(
    "spine",
    [
        [_anchor(1, "一"), _anchor(1, "重复")],
        [_anchor(1, "一"), {"chapter_number": "3", "title": "错误编号"}],
    ],
)
def test_duplicate_or_invalid_numbers_require_manual_resolution(spine):
    with pytest.raises(OutlineContinuityRecoveryError) as exc_info:
        OutlineContinuityRecoveryService().build_draft({"chapter_spine": spine})

    assert exc_info.value.code == "manual_resolution_required"


def test_contiguous_outline_is_not_rewritten_as_recovery_draft():
    source = {"chapter_spine": [_anchor(1, "一"), _anchor(2, "二")]}

    with pytest.raises(OutlineContinuityRecoveryError) as exc_info:
        OutlineContinuityRecoveryService().build_draft(source)

    assert exc_info.value.code == "already_contiguous"
