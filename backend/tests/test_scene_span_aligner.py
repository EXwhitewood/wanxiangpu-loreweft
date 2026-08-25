from __future__ import annotations

from app.services.scene_span_aligner import SceneSpanAligner


def test_scene_span_aligner_uses_explicit_markers_and_strips_them():
    chapter_text = """[[SCENE:c1-s1]]
She wakes under the rough canopy.

The old woman asks her name.

[[SCENE:c1-s2]]
She closes the booklet and keeps it in her hand.
"""
    scene_map = [
        {"scene_id": "c1-s1", "goal": "wake"},
        {"scene_id": "c1-s2", "goal": "bridge object state"},
    ]

    result = SceneSpanAligner().align(chapter_text, scene_map)

    assert result["passed"] is True
    assert result["method"] == "explicit_markers"
    assert len(result["spans"]) == 2
    assert result["spans"][0]["scene_index"] == 0
    assert result["spans"][1]["scene_id"] == "c1-s2"
    assert "[[SCENE:" not in result["clean_text"]
    assert "booklet and keeps it in her hand" in result["spans"][1]["text"]


def test_scene_span_aligner_falls_back_to_paragraph_split():
    result = SceneSpanAligner().align(
        "Opening paragraph.\n\nMiddle paragraph.\n\nClosing paragraph.",
        [{"scene_id": "s1"}, {"scene_id": "s2"}],
    )

    assert result["passed"] is True
    assert result["method"] == "paragraph_fallback"
    assert len(result["spans"]) == 2
    assert result["warnings"]


def test_scene_span_aligner_strips_residual_headers_before_fallback_split():
    chapter_text = """[[SCENE:s1]]
Opening paragraph.

Scene 2:
Second scene text.

第十五章：不应写入正文的结构标题
More scene text.
"""

    result = SceneSpanAligner().align(chapter_text, [{"scene_id": "s1"}, {"scene_id": "s2"}])
    joined = "\n\n".join(span["text"] for span in result["spans"])

    assert result["method"] == "paragraph_fallback"
    assert "[[SCENE:" not in joined
    assert "Scene 2:" not in joined
    assert "第十五章：" not in joined
    assert "Opening paragraph." in joined
    assert "Second scene text." in joined


def test_scene_span_aligner_keeps_inline_chapter_reference_prose():
    text = "这个气味她很熟悉，第十五章藏经阁的木箱碎开时也有。"

    result = SceneSpanAligner().align(text, [{"scene_id": "s1"}])

    assert result["spans"][0]["text"] == text


# 方案20：语义切分测试
def test_plan20_semantic_split_succeeds_when_markers_missing():
    """标记缺失时，如果 semantic_split_fn 返回正确数量的场景，应使用语义切分。"""
    text = "场景1的内容。\n\n场景2的内容。"
    scene_map = [{"scene_id": "s1"}, {"scene_id": "s2"}]

    def mock_semantic_split(text: str, scene_ids: list[str]) -> list[str] | None:
        return ["场景1的内容。", "场景2的内容。"]

    result = SceneSpanAligner().align(text, scene_map, semantic_split_fn=mock_semantic_split)

    assert result["method"] == "semantic_split"
    assert result["passed"] is True
    assert len(result["spans"]) == 2
    assert result["spans"][0]["text"] == "场景1的内容。"
    assert result["spans"][1]["text"] == "场景2的内容。"


def test_plan20_semantic_split_returns_wrong_count_falls_back_to_paragraph():
    """语义切分返回错误数量时，降级为段落均分。"""
    text = "段落1。\n\n段落2。\n\n段落3。"
    scene_map = [{"scene_id": "s1"}, {"scene_id": "s2"}]

    def mock_semantic_split(text: str, scene_ids: list[str]) -> list[str] | None:
        return ["only one scene"]  # 数量不对

    result = SceneSpanAligner().align(text, scene_map, semantic_split_fn=mock_semantic_split)

    assert result["method"] == "paragraph_fallback"
    assert len(result["spans"]) == 2


def test_plan20_semantic_split_raises_falls_back_to_paragraph():
    """语义切分抛异常时，降级为段落均分。"""
    text = "段落1。\n\n段落2。"
    scene_map = [{"scene_id": "s1"}, {"scene_id": "s2"}]

    def mock_semantic_split(text: str, scene_ids: list[str]) -> list[str] | None:
        raise RuntimeError("LLM unavailable")

    result = SceneSpanAligner().align(text, scene_map, semantic_split_fn=mock_semantic_split)

    assert result["method"] == "paragraph_fallback"
    assert any("semantic split failed" in w for w in result["warnings"])


def test_plan20_semantic_split_not_used_when_markers_present():
    """有正确标记时，不调用 semantic_split_fn。"""
    chapter_text = """[[SCENE:s1]]
Scene 1 text.

[[SCENE:s2]]
Scene 2 text.
"""
    scene_map = [{"scene_id": "s1"}, {"scene_id": "s2"}]
    called = False

    def mock_semantic_split(text: str, scene_ids: list[str]) -> list[str] | None:
        nonlocal called
        called = True
        return None

    result = SceneSpanAligner().align(chapter_text, scene_map, semantic_split_fn=mock_semantic_split)

    assert result["method"] == "explicit_markers"
    assert not called


def test_plan20_semantic_split_none_fn_uses_paragraph_fallback():
    """未提供 semantic_split_fn 时，直接用段落均分（向后兼容）。"""
    text = "段落1。\n\n段落2。"
    scene_map = [{"scene_id": "s1"}, {"scene_id": "s2"}]

    result = SceneSpanAligner().align(text, scene_map)

    assert result["method"] == "paragraph_fallback"
    assert len(result["spans"]) == 2

