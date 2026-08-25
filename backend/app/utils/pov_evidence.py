from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any


INNER_ACCESS_MARKERS = (
    "意识到",
    "心里",
    "心中",
    "脑中",
    "暗想",
    "想起",
    "觉得",
    "知道",
    "明白",
    "害怕",
    "担心",
    "希望",
    "后悔",
    "认定",
)

_MARKER_RE = re.compile("|".join(re.escape(item) for item in INNER_ACCESS_MARKERS))
_TAIL_RE = re.compile(r"[^。！？!?；;，,：:—\-\n]{0,12}")
_CLAUSE_BOUNDARIES = "。！？!?；;，,\n：:“”‘’\"'"
_LEADING_CONNECTORS = (
    "与此同时",
    "然而",
    "不过",
    "但是",
    "只是",
    "随后",
    "接着",
    "后来",
    "这时",
    "此时",
    "于是",
    "如果",
    "但",
    "而",
    "可",
    "却",
)
_TRAILING_MODIFIERS = (
    "并不",
    "还不",
    "从不",
    "并未",
    "尚未",
    "未曾",
    "已经",
    "终于",
    "忽然",
    "突然",
    "似乎",
    "大概",
    "仍然",
    "依然",
    "这才",
    "必须",
    "应该",
    "应当",
    "可能",
    "也许",
    "一定",
    "恐怕",
    "只想",
    "想要",
    "想",
    "只",
    "仍",
    "还",
    "也",
    "却",
    "才",
    "不",
    "未",
)
_AMBIGUOUS_PRONOUNS = {"他", "她", "它", "他们", "她们", "它们"}
_GROUP_SUBJECTS = {"众人", "所有人"}
_HAN_SUBJECT_RE = re.compile(r"[\u4e00-\u9fff]{1,6}")
_BODY_PART_INNER_PREFIXES = (
    "手",
    "掌",
    "脚",
    "足",
    "胸",
    "怀",
    "掌心",
    "手心",
    "脚心",
    "足心",
)
_DEGREE_COMPLEMENT_PRONOUN_RE = re.compile(r"到\s*(他|她|它|他们|她们|它们)$")

# High-confidence human role mentions used only for local pronoun resolution.
# The rule remains story-agnostic: role words describe Chinese grammar and
# social address, while project-specific character names come from the caller.
_HUMAN_ROLE_SUFFIXES = (
    "师兄", "师弟", "师姐", "师妹", "师父", "师尊", "师叔", "师伯",
    "长老", "掌门", "宗主", "城主", "峰主", "堂主", "宫主",
    "父亲", "母亲", "兄长", "姐姐", "弟弟", "妹妹", "丈夫", "妻子",
    "皇帝", "皇后", "王爷", "公主", "将军", "统领", "队长",
    "医师", "大夫", "掌柜", "管家", "侍卫", "护卫",
    "魔君", "妖王", "鬼王", "少年", "少女", "男人", "女人", "老人", "孩子",
    "主角", "配角", "旁观者", "对手", "敌人", "来客", "客人", "弟子", "修士",
)
_ROLE_ORDINAL = r"(?:第?[\d一二三四五六七八九十两]+|大|小)?"
_HUMAN_ROLE_RE = re.compile(
    rf"(?<![\u4e00-\u9fff])(?P<name>{_ROLE_ORDINAL}(?:{'|'.join(map(re.escape, _HUMAN_ROLE_SUFFIXES))}))"
)
_MALE_ROLE_SUFFIXES = {
    "师兄", "师弟", "师父", "师尊", "师叔", "师伯", "父亲", "兄长", "弟弟",
    "丈夫", "皇帝", "王爷", "魔君", "妖王", "鬼王", "少年", "男人",
}
_FEMALE_ROLE_SUFFIXES = {
    "师姐", "师妹", "母亲", "姐姐", "妹妹", "妻子", "皇后", "公主", "少女", "女人",
}
_COMMON_SINGLE_SURNAMES = frozenset(
    "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张"
    "孔曹严华金魏陶姜戚谢邹喻柏水窦章云苏潘葛奚范"
    "彭郎鲁韦昌马苗凤花方俞任袁柳唐费廉岑薛雷贺倪"
    "汤滕殷罗毕郝鞍常乐于时傅皮卞齐康伍余元卜顾孟"
    "平黄和穆萧尹姚邵湛汪祁毛禹狄米贝明臧计伏成戴"
    "谈宋茅庞熊纪舒屈项祝董梁杜阮蓝闵季贾路娄江童"
    "郭梅盛林刁钟徐邱骆高夏蔡田樊胡凌霍虞万支柯管卢"
    "莫经房裘缪干解应宗丁宣贲邓郁单杭洪包左石崔吉"
    "龚程嵇邢滑裴陆荣翁荀羊惠甄麴家封芈储羁松井段"
    "富巫乌焦巴弓牧山谷车侯宓蓬全郗班仰秋仲伊宫宁"
    "仇栾暴甘厉戎祖武符刘景詹束龙叶幸司韶郜黎蓟薄"
    "印白怀蒲邟从鄂索咸籍赖卓蔺屠蒙池乔阴郁胥能苍"
    "双闻党翟谭贡劳逢姬申扶堵冉宰郦雍却璩桑桂濮牛寿"
    "边扈燕冀郏浦尚农温别庄晏柴瞿阎充慕连茌习宦艾鱼"
    "容向古易慎戈廖庾终暨居衡步都耕满弘匡国文寇广禄"
    "阙东殴沃利蔚越隆师巩厍聂晁勾敖融冷訾辛阚那简饶"
    "空曾毋沙乍养鞠须丰巢关蒯相查后荆红游竺权盍益桓"
    "公万俟司马上官欧阳夏侯诸葛东方皇甫尉迟公羊澹台公冶"
)
_COMMON_DOUBLE_SURNAMES = (
    "万俟", "司马", "上官", "欧阳", "夏侯", "诸葛", "东方", "皇甫",
    "尉迟", "公羊", "澹台", "公冶", "宗政", "濮阳", "淳于", "单于",
)


@dataclass(frozen=True)
class InnerAccessRuleConfig:
    """Language-level evidence config for limited-POV prose.

    The parser is story-agnostic: callers provide a POV name and optional
    language markers, while the rule only classifies evidence by subject
    certainty.
    """

    markers: tuple[str, ...] = INNER_ACCESS_MARKERS
    clause_boundaries: str = _CLAUSE_BOUNDARIES
    leading_connectors: tuple[str, ...] = _LEADING_CONNECTORS
    trailing_modifiers: tuple[str, ...] = _TRAILING_MODIFIERS
    ambiguous_pronouns: frozenset[str] = frozenset(_AMBIGUOUS_PRONOUNS)
    group_subjects: frozenset[str] = frozenset(_GROUP_SUBJECTS)


DEFAULT_INNER_ACCESS_CONFIG = InnerAccessRuleConfig()


@lru_cache(maxsize=16)
def _marker_re(markers: tuple[str, ...]) -> re.Pattern[str]:
    escaped = [re.escape(item) for item in markers if item]
    if not escaped:
        return re.compile(r"(?!x)x")
    return re.compile("|".join(escaped))


def analyze_inner_access(
    text: str,
    *,
    pov_name: str = "",
    known_character_names: tuple[str, ...] | list[str] | set[str] = (),
    config: InnerAccessRuleConfig | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Return confirmed non-POV access and non-blocking ambiguous matches.

    Pronouns become blocking only when a nearby, explicit antecedent can be
    resolved with high confidence.  This prevents modal phrases such as
    ``她必须知道`` from being mistaken for a character name while still
    catching ``五师兄…他觉得`` as access to a non-POV character.
    """
    config = config or DEFAULT_INNER_ACCESS_CONFIG
    confirmed: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []
    source = text or ""

    for marker_match in _marker_re(config.markers).finditer(source):
        if _is_inside_quoted_text(source, marker_match.start()):
            continue
        if _is_lexical_body_part_marker(source, marker_match.start(), marker_match.group(0)):
            continue
        clause_start = _clause_start(source, marker_match.start(), config.clause_boundaries)
        parsed = _parse_subject_prefix(source[clause_start:marker_match.start()], config)
        if parsed is None:
            continue

        subject, raw_subject, relative_start = parsed
        subject_start = clause_start + relative_start
        tail_match = _TAIL_RE.match(source, marker_match.end())
        repair_end = tail_match.end() if tail_match else marker_match.end()
        evidence = {
            "subject": subject,
            "raw_subject": raw_subject,
            "marker": marker_match.group(0),
            "tail": source[marker_match.end():repair_end],
            "start": subject_start,
            "end": repair_end,
            "target_span": source[subject_start:repair_end],
            "snippet": _snippet(source, subject_start, repair_end),
            "rule_id": "narrative.inner_access",
            "scope": "pov_boundary",
        }

        if _is_pov_subject(subject, pov_name):
            continue
        if subject in config.ambiguous_pronouns:
            resolution = _resolve_pronoun_antecedent(
                source,
                subject_start=subject_start,
                pronoun=subject,
                pov_name=pov_name,
                known_character_names=known_character_names,
            )
            if resolution:
                antecedent = str(resolution["antecedent"])
                evidence.update({
                    "surface_subject": subject,
                    "subject": antecedent,
                    "antecedent": antecedent,
                    "antecedent_start": resolution["start"],
                    "antecedent_source": resolution["source"],
                })
                if _is_pov_subject(antecedent, pov_name):
                    continue
                evidence["confidence"] = "resolved_pronoun_antecedent"
                confirmed.append(evidence)
                continue

            evidence["confidence"] = "ambiguous_pronoun"
            ambiguous.append(evidence)
            continue

        # A short Han-character prefix is not automatically a person.  The
        # previous rule promoted objects and connectives such as ``纸不觉得``
        # and ``也不知道`` to named characters, which produced a false
        # head-hopping order and let the repairer damage otherwise valid prose.
        # Only project-provided character names or high-confidence human roles
        # can become commit-blocking explicit-subject evidence.
        if not _is_explicit_human_subject(
            subject,
            known_character_names=known_character_names,
            group_subjects=config.group_subjects,
        ):
            continue

        evidence["confidence"] = (
            "explicit_group_subject" if subject in config.group_subjects else "explicit_named_subject"
        )
        confirmed.append(evidence)

    return {"confirmed": confirmed, "ambiguous": ambiguous}


def _clause_start(text: str, marker_start: int, clause_boundaries: str) -> int:
    boundary = max((text.rfind(char, 0, marker_start) for char in clause_boundaries), default=-1)
    return boundary + 1


def _is_inside_quoted_text(text: str, index: int) -> bool:
    """Return True when an index is inside direct speech/quoted prose."""
    for opener, closer in (("“", "”"), ("‘", "’"), ("「", "」"), ("『", "』")):
        last_open = text.rfind(opener, 0, index)
        if last_open < 0:
            continue
        last_close = text.rfind(closer, 0, index)
        if last_open > last_close and text.find(closer, index) >= 0:
            return True

    for quote in ("\"", "'"):
        count = 0
        pos = 0
        while True:
            found = text.find(quote, pos, index)
            if found < 0:
                break
            if found == 0 or text[found - 1] != "\\":
                count += 1
            pos = found + 1
        if count % 2 == 1 and text.find(quote, index) >= 0:
            return True
    return False


def _is_lexical_body_part_marker(text: str, marker_start: int, marker: str) -> bool:
    """Skip matches where mental markers are inside body-part words.

    Examples such as "手心里" and "掌心里" are physical locations, not access
    to another character's inner state. Treating the embedded "心里" as a POV
    marker creates false head-hopping evidence.
    """
    if marker not in {"心里", "心中"}:
        return False
    left = text[max(0, marker_start - 4):marker_start]
    return any(left.endswith(prefix) for prefix in _BODY_PART_INNER_PREFIXES)


def _parse_subject_prefix(prefix: str, config: InnerAccessRuleConfig) -> tuple[str, str, int] | None:
    if not prefix:
        return None

    left_trimmed = prefix.lstrip()
    relative_start = len(prefix) - len(left_trimmed)
    candidate = left_trimmed.rstrip()
    if not candidate:
        return None

    changed = True
    while changed:
        changed = False
        for connector in config.leading_connectors:
            if candidate.startswith(connector) and len(candidate) > len(connector):
                candidate = candidate[len(connector):].lstrip()
                relative_start = prefix.find(candidate, relative_start)
                changed = True
                break

    raw_subject = candidate
    changed = True
    while changed:
        changed = False
        for modifier in config.trailing_modifiers:
            if candidate.endswith(modifier) and len(candidate) > len(modifier):
                candidate = candidate[:-len(modifier)].rstrip()
                changed = True
                break

    if candidate.endswith("的") and len(candidate) > 1:
        candidate = candidate[:-1]

    for causative in ("让", "令", "使"):
        if causative in candidate:
            candidate = candidate.rsplit(causative, 1)[-1].strip()
            relative_start = prefix.rfind(candidate)

    degree_match = _DEGREE_COMPLEMENT_PRONOUN_RE.search(candidate)
    if degree_match:
        candidate = degree_match.group(1)
        relative_start = prefix.rfind(candidate)

    if not _HAN_SUBJECT_RE.fullmatch(candidate):
        return None
    return candidate, raw_subject, max(relative_start, 0)


def _is_pov_subject(subject: str, pov_name: str) -> bool:
    pov = (pov_name or "").strip()
    if not pov:
        return False
    return subject == pov or (
        len(subject) >= 2
        and len(pov) >= 2
        and (subject.endswith(pov) or pov.endswith(subject))
    )


def _is_explicit_human_subject(
    subject: str,
    *,
    known_character_names: tuple[str, ...] | list[str] | set[str],
    group_subjects: frozenset[str],
) -> bool:
    value = str(subject or "").strip()
    if not value:
        return False
    if value in group_subjects:
        return True
    for name in known_character_names or ():
        known = str(name or "").strip()
        if not known:
            continue
        if value == known or (
            len(value) >= 2
            and len(known) >= 2
            and (value.endswith(known) or known.endswith(value))
        ):
            return True
    if any(value.endswith(suffix) for suffix in _HUMAN_ROLE_SUFFIXES):
        return True
    return bool(
        2 <= len(value) <= 4
        and (
            value[0] in _COMMON_SINGLE_SURNAMES
            or any(value.startswith(surname) for surname in _COMMON_DOUBLE_SURNAMES)
        )
    )


def _resolve_pronoun_antecedent(
    text: str,
    *,
    subject_start: int,
    pronoun: str,
    pov_name: str,
    known_character_names: tuple[str, ...] | list[str] | set[str],
) -> dict[str, Any] | None:
    """Resolve a pronoun only from a bounded, explicit local antecedent.

    Candidates in the same sentence outrank candidates in preceding text.  A
    project-provided character name outranks a generic human role mention.  If
    no traceable candidate exists, the pronoun remains ambiguous and cannot
    create a commit-blocking finding.
    """
    if subject_start <= 0:
        return None

    window_start = max(0, subject_start - 220)
    sentence_start = max(
        (text.rfind(mark, window_start, subject_start) for mark in "。！？!?\n"),
        default=-1,
    ) + 1
    previous_sentence_end = max(sentence_start - 1, window_start)
    previous_sentence_start = max(
        (text.rfind(mark, window_start, previous_sentence_end) for mark in "。！？!?\n"),
        default=window_start - 1,
    ) + 1
    before = text[window_start:subject_start]
    candidates: list[dict[str, Any]] = []

    names = {
        str(name).strip()
        for name in [pov_name, *list(known_character_names or ())]
        if str(name).strip()
    }
    for name in names:
        cursor = 0
        while True:
            found = before.find(name, cursor)
            if found < 0:
                break
            absolute = window_start + found
            candidates.append({
                "antecedent": name,
                "start": absolute,
                "end": absolute + len(name),
                "source": "known_character",
                "priority": 2,
            })
            cursor = found + len(name)

    for match in _HUMAN_ROLE_RE.finditer(before):
        name = match.group("name")
        if not _pronoun_role_compatible(pronoun, name):
            continue
        candidates.append({
            "antecedent": name,
            "start": window_start + match.start("name"),
            "end": window_start + match.end("name"),
            "source": "local_role_mention",
            "priority": 1,
        })

    candidates = [item for item in candidates if int(item["end"]) <= subject_start]
    if not candidates:
        return None

    same_sentence = [item for item in candidates if int(item["start"]) >= sentence_start]
    immediate_context = [
        item for item in candidates
        if int(item["start"]) >= previous_sentence_start
    ]
    pool = same_sentence or immediate_context
    if not pool:
        return None
    # Prefer the nearest explicit mention; source priority breaks exact ties.
    return max(pool, key=lambda item: (int(item["end"]), int(item["priority"])))


def _pronoun_role_compatible(pronoun: str, role_name: str) -> bool:
    if pronoun.startswith("她"):
        return not any(role_name.endswith(suffix) for suffix in _MALE_ROLE_SUFFIXES)
    if pronoun.startswith("他"):
        return not any(role_name.endswith(suffix) for suffix in _FEMALE_ROLE_SUFFIXES)
    if pronoun.startswith("它"):
        return False
    return True


def _snippet(text: str, start: int, end: int, radius: int = 24) -> str:
    return text[max(0, start - radius):min(len(text), end + radius)]
