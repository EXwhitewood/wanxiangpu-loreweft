from app.services.chapter_commit_health import ChapterCommitHealthChecker


def test_commit_health_blocks_empty_final_text_when_scene_text_exists():
    result = ChapterCommitHealthChecker().evaluate(
        final_text="",
        scene_texts={0: "凤溪抬头看向裂缝。"},
        chapter_state={},
    )

    assert result["allowed"] is False
    assert result["hard_issues"][0]["code"] == "empty_final_text"


def test_commit_health_blocks_internal_scene_separator_in_aligned_scene():
    result = ChapterCommitHealthChecker().evaluate(
        scene_texts={
            0: "First part of a scene.\n\n---\n\nA second independent section leaked in.",
            1: "Second scene text.",
        }
    )

    assert result["allowed"] is False
    assert result["hard_issues"][0]["code"] == "internal_scene_separator"


def test_commit_health_blocks_substantial_text_with_empty_chapter_state():
    result = ChapterCommitHealthChecker().evaluate(
        final_text="凤溪走进戒律堂。" * 100,
        chapter_state={
            "established_facts": [],
            "character_states": {},
            "completed_events": [],
            "active_constraints": [],
        },
    )

    assert result["allowed"] is False
    assert result["hard_issues"][0]["code"] == "empty_chapter_state"


def test_commit_health_keeps_style_human_review_advisory():
    result = ChapterCommitHealthChecker().evaluate(
        style_result={"requires_human_review": True, "style_score": 50}
    )

    assert result["allowed"] is True
    assert result["hard_issues"] == []
    assert result["issues"][0]["code"] == "style_requires_human_review"
    assert result["issues"][0]["severity"] == "advisory"


def test_commit_health_allows_clean_artifacts():
    result = ChapterCommitHealthChecker().evaluate(
        final_text="凤溪走进戒律堂。" * 100,
        scene_texts={0: "凤溪走进戒律堂。", 1: "她取出卷宗。"},
        chapter_state={"established_facts": ["凤溪走进戒律堂。"]},
        style_result={"requires_human_review": False, "style_score": 82},
    )

    assert result["allowed"] is True
    assert result["hard_issues"] == []

