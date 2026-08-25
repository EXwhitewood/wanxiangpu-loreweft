from app.utils.word_count import count_words


def test_count_words_ignores_editor_html_markup():
    assert count_words("<p>第一段</p><p>Hello 2026</p><br>") == 5


def test_count_words_accepts_plain_text():
    assert count_words("中文 text 42") == 4
