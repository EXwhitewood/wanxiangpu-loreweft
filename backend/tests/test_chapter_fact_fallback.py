from app.services.scene_generation_pipeline import _fallback_extract_chapter_facts


def test_fallback_extract_chapter_facts_uses_verbatim_sentences():
    text = (
        "凤溪走进戒律堂，取出一卷旧案卷。"
        "苏云清站在审判席旁，脸色微白。"
        "戒律长老确认案卷上的出入记录属实。"
    )

    result = _fallback_extract_chapter_facts(
        text,
        [{"name": "凤溪"}, {"name": "苏云清"}],
        scene_contract={"ending_state": "案卷被公开"},
    )

    assert result["established_facts"]
    assert result["completed_events"]
    assert result["character_states"]["凤溪"] == "appears in this scene"
    assert result["active_constraints"] == ["Expected ending state: 案卷被公开"]
    assert result["scene_ending"] == text[-300:]

