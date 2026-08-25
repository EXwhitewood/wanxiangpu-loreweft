import re
from html.parser import HTMLParser


class _VisibleTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _visible_text(text: str) -> str:
    if "<" not in text and "&" not in text:
        return text
    parser = _VisibleTextExtractor()
    try:
        parser.feed(text)
        parser.close()
        return " ".join(parser.parts)
    except Exception:
        # Malformed editor HTML must not break saving or aggregate refresh.
        return re.sub(r"<[^>]*>", " ", text)


def count_words(text: str) -> int:
    """统计文本的真实字数（网文平台标准）。
    
    规则：
    - 每个中文汉字（CJK 统一表意文字）= 1 字
    - 每个连续英文字母序列 = 1 词
    - 每个连续数字序列 = 1 词
    - 标点符号、空格、换行 → 不计
    """
    if not text:
        return 0
    visible_text = _visible_text(text)
    chinese = len(re.findall(r'[\u4e00-\u9fff\u3400-\u4dbf]', visible_text))
    english = len(re.findall(r'[a-zA-Z]+', visible_text))
    numbers = len(re.findall(r'\d+', visible_text))
    return chinese + english + numbers
