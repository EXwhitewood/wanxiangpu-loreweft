from __future__ import annotations

import re
from collections.abc import Iterator


DASH_ARTIFACT_RE = re.compile(r"\u2014\u2014|--|\u2014|-")
DEFAULT_DASH_ARTIFACT_MAX_PER_1000 = 0.5


def iter_dash_artifacts(text: str | None) -> Iterator[re.Match[str]]:
    """Yield dash artifacts using the shared review/repair definition.

    Count each Chinese em-dash pair, single em dash, ASCII double hyphen, and
    ASCII hyphen as one artifact. The alternation keeps multi-character forms
    from being split into smaller matches.
    """

    return DASH_ARTIFACT_RE.finditer(text or "")


def count_dash_artifacts(text: str | None) -> int:
    return sum(1 for _ in iter_dash_artifacts(text))


def dash_artifact_density_per_1000(text: str | None) -> float:
    source = text or ""
    return count_dash_artifacts(source) / max(len(source) / 1000, 1)


def dash_artifact_allowed_count(text: str | None, max_per_1000: float) -> int:
    source = text or ""
    return max(0, int(float(max_per_1000) * max(len(source), 1) / 1000))


def dash_artifact_replacement(text: str, start: int, end: int) -> str:
    """Choose a punctuation replacement from the local sentence context."""
    before = _previous_non_space(text, start)
    after = _next_non_space(text, end)
    left = text[max(0, start - 24):start].strip()
    right = text[end:end + 24].strip()

    if before in "，、；;：:。！？!?\n\r" or after in "，、；;：:。！？!?\n\r":
        return ""
    if after in "“\"'‘「『":
        return "："
    if re.search(r"(?:不是|并非|并不是)[^，。！？!?；;：:、]{0,16}$", left) and right.startswith("是"):
        return "，而"
    if right.startswith("而是"):
        return "，"
    if len(left) >= 18 or re.match(
        r"^(?:他|她|它|他们|她们|这|那|但|可|而|然后|于是|接着|下一刻)",
        right,
    ):
        return "。"
    return "，"


def dash_artifact_replacement_indexes(
    text: str,
    matches: list[re.Match[str]],
    replacements_needed: int,
) -> list[int]:
    """Prioritize dash positions that can be replaced without semantic loss."""
    scored: list[tuple[int, int, int]] = []
    for index, match in enumerate(matches):
        start, end = match.span()
        left = text[max(0, start - 24):start].strip()
        right = text[end:end + 24].strip()
        before = _previous_non_space(text, start)
        after = _next_non_space(text, end)
        priority = 30
        if re.search(r"(?:不是|并非|并不是)[^，。！？!?；;：:、]{0,16}$", left) and right.startswith("是"):
            priority = 0
        elif right.startswith("而是") or after in "“\"'‘「『":
            priority = 5
        elif before in "，、；;：:。！？!?\n\r" or after in "，、；;：:。！？!?\n\r":
            priority = 10
        elif len(left) >= 18 or re.match(
            r"^(?:他|她|它|他们|她们|这|那|但|可|而|然后|于是|接着|下一刻)",
            right,
        ):
            priority = 20
        scored.append((priority, start, index))
    return [
        index
        for _, _, index in sorted(scored)[:max(0, replacements_needed)]
    ]


def _previous_non_space(text: str, index: int) -> str:
    for pos in range(index - 1, -1, -1):
        if not text[pos].isspace():
            return text[pos]
    return ""


def _next_non_space(text: str, index: int) -> str:
    for pos in range(index, len(text)):
        if not text[pos].isspace():
            return text[pos]
    return ""
