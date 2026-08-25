from app.services.text_coercion import ensure_complete_sentence_ending


def test_ensure_complete_sentence_ending_appends_period_to_fragment():
    assert ensure_complete_sentence_ending("凤溪把铁针收进袖中") == "凤溪把铁针收进袖中。"


def test_ensure_complete_sentence_ending_preserves_complete_sentence():
    assert ensure_complete_sentence_ending("凤溪把铁针收进袖中。") == "凤溪把铁针收进袖中。"


def test_ensure_complete_sentence_ending_normalizes_ascii_period():
    assert ensure_complete_sentence_ending("凤溪在黑暗中等着.") == "凤溪在黑暗中等着。"
