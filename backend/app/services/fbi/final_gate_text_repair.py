from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.utils.pov_evidence import analyze_inner_access

RHYTHM_METRICS = {
    "structure_word_cluster_count",
    "paragraph_shape_repeat_count",
    "mirrored_paragraph_opening_count",
    "dense_paragraph_streak_max",
    "abrupt_shift_count",
    "uniform_sentence_streak_max",
    "sentence_length_variance",
    "breathing_paragraph_ratio",
}

EVIDENCE_METRICS = {
    "emotion_label_count",
    "standalone_abstract_claims",
    "abstract_bare_count",
    "evidence_per_emotion_claim",
    "thought_verb_count_per_500",
}

POV_STRUCTURE_METRICS = {
    "pure_exposition_block_chars",
    "single_pov_per_scene",
    "head_hopping_count",
    "narrator_commentary_count",
}

RHYTHM_HINTS = ("段落", "节奏", "句式", "过密", "镜像开头", "结构词", "uniform sentence")
EVIDENCE_HINTS = ("抽象", "情绪标签", "证据", "具象", "悬浮", "abstract", "emotion label")
POV_HINTS = ("pov", "视角", "全知", "跳头", "旁白", "解释段", "pure exposition", "head hopping")

STRUCTURE_PHRASES = (
    "这意味着",
    "这说明",
    "这表明",
    "这体现了",
    "这反映出",
    "由此可见",
    "换句话说",
    "也就是说",
    "事实上",
    "实际上",
    "从某种意义上说",
    "更准确地说",
)

EMOTION_REPLACEMENTS = {
    "愤怒": "指节收紧，袖口被攥出褶痕",
    "生气": "指节收紧，袖口被攥出褶痕",
    "悲伤": "眼睫垂下，声音低了一截",
    "难过": "眼睫垂下，声音低了一截",
    "痛苦": "指节按住伤处，呼吸短了一拍",
    "恐惧": "指尖贴住掌心，脚步停在原地",
    "害怕": "指尖贴住掌心，脚步停在原地",
    "震惊": "动作停住，目光钉在那道痕迹上",
    "绝望": "肩背塌下，手指慢慢松开",
    "开心": "眼角亮了一下，声音轻了半分",
    "高兴": "眼角亮了一下，声音轻了半分",
    "复杂": "目光避开，指节在袖口上停住",
    "不安": "指尖扣住掌心，呼吸压得很短",
    "紧张": "指尖扣住掌心，呼吸压得很短",
    "焦虑": "脚尖在地面蹭了一下，视线来回扫动",
    "羞愧": "目光垂下，指节贴着袖口停住",
    "后悔": "手指停在半空，迟迟没有落下",
    "委屈": "喉咙动了一下，声音压得发哑",
    "孤独": "身边的空处冷下来，只剩脚步声贴着地面",
}

EVIDENCE_MARKERS = (
    "门",
    "窗",
    "桌",
    "椅",
    "灯",
    "墙",
    "地面",
    "衣",
    "袖",
    "鞋",
    "刀",
    "纸",
    "信",
    "钥匙",
    "木",
    "石",
    "灰",
    "尘",
    "血",
    "雨",
    "雪",
    "火",
    "影",
    "光",
    "纹",
    "痕",
    "裂",
    "锈",
    "推",
    "拉",
    "抓",
    "按",
    "握",
    "抬",
    "放",
    "退",
    "停",
    "躲",
    "擦",
    "扣",
    "响",
    "看",
    "听",
    "声",
    "脚步",
    "冷",
    "热",
    "疼",
    "痛",
    "湿",
    "硬",
)

POV_OMNISCIENT_PATTERNS = (
    (re.compile(r"无人知道[，,]?"), ""),
    (re.compile(r"谁也不知道[，,]?"), ""),
    (re.compile(r"(?m)(^|[。！？!?；;\n])\s*与此同时[，,].{0,12}在"), r"\1"),
    (re.compile(r"(?m)(^|[。！？!?；;\n])\s*另一边[，,]"), r"\1"),
    (re.compile(r"\[\[POV:[^\]]+\]\]"), ""),
)

NARRATOR_COMMENTARY_PATTERNS = (
    (re.compile(r"读者(?:应该|可以|会)[^。！？!?]{0,30}[。！？!?]?"), ""),
    (re.compile(r"这(?:意味着|说明|象征|预示)"), "眼前只留下"),
    (re.compile(r"命运(?:已经|正在|从不|不会)"), "眼前的局面"),
    (re.compile(r"故事(?:由此|将在|就此)[^。！？!?；;，,\n]{0,16}"), "事态暂时压在眼前"),
)

PUNCTUATION_RE = re.compile(r"([。！？!?；;])")
SENTENCE_RE = re.compile(r"[^。！？!?；;\n]+[。！？!?；;]?")


@dataclass(slots=True)
class FinalGateRepairResult:
    text: str
    applied: list[str] = field(default_factory=list)
    metrics: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.applied)

    def to_audit(self) -> dict[str, Any]:
        return {
            "executor_path": "final_gate_text_repair",
            "applied": list(self.applied),
            "metrics": list(self.metrics),
        }


def repair_final_gate_text(
    text: str,
    violations: list[dict],
    *,
    reason: str = "",
    instruction: str = "",
    scene_contract: dict | None = None,
) -> FinalGateRepairResult:
    source = text or ""
    metrics = _issue_keys(violations, reason=reason, instruction=instruction)
    repaired = source
    applied: list[str] = []

    if metrics & RHYTHM_METRICS:
        before = repaired
        repaired = _repair_rhythm_and_shape(repaired)
        if repaired != before:
            applied.append("rhythm_paragraph_shape")

    if metrics & EVIDENCE_METRICS:
        before = repaired
        repaired = _repair_evidence_locality(repaired)
        if repaired != before:
            applied.append("evidence_localizer")

    if metrics & POV_STRUCTURE_METRICS:
        before = repaired
        repaired = _repair_pov_and_exposition(
            repaired,
            pov_name=_pov_name(scene_contract or {}),
        )
        if repaired != before:
            applied.append("pov_exposition_lock")

    if (metrics & EVIDENCE_METRICS) and "pov_exposition_lock" in applied:
        before = repaired
        repaired = _repair_evidence_locality(repaired)
        if repaired != before and "evidence_localizer" not in applied:
            applied.append("evidence_localizer")

    repaired = _cleanup_spacing(repaired)
    if repaired == source:
        applied = []
    return FinalGateRepairResult(
        text=repaired,
        applied=applied,
        metrics=sorted(metrics),
    )


def _issue_keys(
    violations: list[dict],
    *,
    reason: str = "",
    instruction: str = "",
) -> set[str]:
    keys: set[str] = set()
    for violation in violations or []:
        if not isinstance(violation, dict):
            continue
        for key in (
            "metric",
            "metric_name",
            "metric_key",
            "type",
            "violation_type",
            "semantic_type",
            "action",
            "validator",
            "rule",
            "rule_id",
            "check_id",
            "code",
            "name",
        ):
            value = str(violation.get(key) or "").strip().lower()
            if value:
                keys.add(value)
    blob = f"{reason} {instruction}".lower()
    for known in RHYTHM_METRICS | EVIDENCE_METRICS | POV_STRUCTURE_METRICS:
        if known in blob:
            keys.add(known)
    if any(hint in blob for hint in RHYTHM_HINTS):
        keys.add("paragraph_shape_repeat_count")
    if any(hint in blob for hint in EVIDENCE_HINTS):
        keys.add("abstract_bare_count")
    if any(hint in blob for hint in POV_HINTS):
        keys.add("single_pov_per_scene")
    return keys


def _repair_rhythm_and_shape(text: str) -> str:
    text = _remove_structure_phrases(text)
    text = _break_sentence_shells(text)
    paragraphs = _paragraphs(text)
    paragraphs = _split_dense_paragraphs(paragraphs)
    paragraphs = _reduce_mirrored_openings(paragraphs)
    return "\n\n".join(paragraphs)


def _remove_structure_phrases(text: str) -> str:
    repaired = text or ""
    for phrase in STRUCTURE_PHRASES:
        repaired = re.sub(rf"(?<![\u4e00-\u9fff]){re.escape(phrase)}[，,：:]?", "", repaired)
        repaired = repaired.replace(f"，{phrase}", "，")
        repaired = repaired.replace(f"。{phrase}", "。")
    return repaired


def _break_sentence_shells(text: str) -> str:
    repaired = text or ""
    repaired = re.sub(r"不是([^。！？!?]{1,36})而是", r"不是\1。", repaired)
    repaired = re.sub(r"并非([^。！？!?]{1,36})而是", r"并非\1。", repaired)
    repaired = re.sub(r"不仅([^。！？!?]{1,36})(?:而且|更)", r"\1。", repaired)
    repaired = re.sub(r"真正.{0,12}的是", "", repaired)
    repaired = re.sub(r"答案(?:其实)?很简单[，,:：]?", "", repaired)
    return repaired


def _split_dense_paragraphs(paragraphs: list[str]) -> list[str]:
    result: list[str] = []
    for paragraph in paragraphs:
        sentences = _sentences(paragraph)
        if len(paragraph) < 90 and len(sentences) <= 3:
            result.append(paragraph)
            continue
        if not sentences:
            result.append(paragraph)
            continue
        chunk: list[str] = []
        chunk_chars = 0
        for sentence in sentences:
            for piece in _split_long_sentence(sentence, max_chars=70):
                chunk.append(piece)
                chunk_chars += len(piece)
                if chunk_chars >= 70 or len(chunk) >= 2:
                    result.append("".join(chunk).strip())
                    chunk = []
                    chunk_chars = 0
        if chunk:
            result.append("".join(chunk).strip())
    return [item for item in result if item]


def _reduce_mirrored_openings(paragraphs: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for paragraph in paragraphs:
        prefix = re.sub(r"[\W_]+", "", paragraph)[:6]
        candidate = paragraph
        if len(prefix) >= 4 and prefix in seen:
            candidate = _drop_opening_subject(candidate)
        seen.add(re.sub(r"[\W_]+", "", candidate)[:6])
        result.append(candidate)
    return result


def _drop_opening_subject(paragraph: str) -> str:
    repaired = re.sub(r"^(他|她|它|他们|她们|众人|所有人)(?=[\u4e00-\u9fff])", "", paragraph, count=1)
    if repaired != paragraph:
        return repaired
    return re.sub(
        r"^([\u4e00-\u9fff]{2,4})(?=(?:抬|低|转|伸|抓|按|看|望|听|退|停|走|把|将))",
        "",
        paragraph,
        count=1,
    )


def _repair_evidence_locality(text: str) -> str:
    repaired = text or ""
    complex_replacement = EMOTION_REPLACEMENTS.get("复杂")
    if complex_replacement:
        repaired = re.sub(
            r"(?:感到|觉得)复杂|(?:神色|表情|心情|目光)(?:有些|显得)?复杂",
            complex_replacement,
            repaired,
        )
    for label, replacement in EMOTION_REPLACEMENTS.items():
        if label == "复杂":
            continue
        repaired = re.sub(rf"(?:感到|觉得)?{re.escape(label)}", replacement, repaired)
    paragraphs = _paragraphs(repaired)
    if not paragraphs:
        return repaired
    fixed_paragraphs: list[str] = []
    for paragraph in paragraphs:
        sentences = _sentences(paragraph)
        if not sentences:
            fixed_paragraphs.append(paragraph)
            continue
        fixed_paragraphs.append("".join(_ground_abstract_sentence(sentence) for sentence in sentences))
    return "\n\n".join(fixed_paragraphs)


def _ground_abstract_sentence(sentence: str) -> str:
    # Keep the deterministic fallback deliberately narrow: it grounds only a
    # bare danger assertion and adds the cue at most once per sentence.  More
    # open-ended abstractions still fall through to the semantic repair path.
    if (
        any(marker in sentence for marker in ("危险", "危急", "危机"))
        and "门缝里的风声" not in sentence
        and not any(marker in sentence for marker in ("指尖", "脚步", "呼吸", "风声", "响", "看", "听"))
    ):
        return sentence + "门缝里的风声贴着地面擦过去。"
    return sentence


def _repair_pov_and_exposition(text: str, *, pov_name: str = "") -> str:
    repaired = text or ""
    for pattern, replacement in POV_OMNISCIENT_PATTERNS:
        repaired = pattern.sub(replacement, repaired)
    for pattern, replacement in NARRATOR_COMMENTARY_PATTERNS:
        repaired = pattern.sub(replacement, repaired)
    inner_access = analyze_inner_access(repaired, pov_name=pov_name)
    for evidence in reversed(inner_access["confirmed"]):
        replacement = _pov_inner_replacement(
            str(evidence.get("subject") or ""),
            str(evidence.get("marker") or ""),
            str(evidence.get("tail") or ""),
        )
        repaired = (
            repaired[:int(evidence["start"])]
            + replacement
            + repaired[int(evidence["end"]):]
        )
    repaired = "\n\n".join(_split_exposition_paragraphs(_paragraphs(repaired)))
    return repaired


def _pov_inner_replacement(subject: str, marker: str, tail: str) -> str:
    if marker in {"害怕", "担心", "希望", "后悔"} or any(label in tail for label in EMOTION_REPLACEMENTS):
        return f"{subject}的指节收紧"
    if marker in {"知道", "明白", "意识到", "认定"}:
        return f"{subject}的目光在痕迹上停了一瞬"
    return f"{subject}的动作顿了一下"


def _split_exposition_paragraphs(paragraphs: list[str]) -> list[str]:
    result: list[str] = []
    for paragraph in paragraphs:
        if len(paragraph) < 80 or _has_action_or_dialogue(paragraph):
            result.append(paragraph)
            continue
        sentences = _sentences(paragraph)
        if not sentences:
            result.append(paragraph)
            continue
        chunk: list[str] = []
        chars = 0
        for sentence in sentences:
            for piece in _split_long_sentence(sentence, max_chars=65):
                chunk.append(piece)
                chars += len(piece)
                if chars >= 65:
                    result.append("".join(chunk).strip())
                    chunk = []
                    chars = 0
        if chunk:
            result.append("".join(chunk).strip())
    return [item for item in result if item]


def _has_action_or_dialogue(text: str) -> bool:
    action_marks = len(re.findall(r"(说|问|喊|走|退|抓|按|看|听|抬|落|砸|撞|握|拔|伸)", text or ""))
    dialogue_marks = (text or "").count("“") + (text or "").count('"')
    return action_marks > 1 or dialogue_marks > 0


def _pov_name(scene_contract: dict) -> str:
    for key in ("pov", "pov_character", "pov_name", "character_name"):
        value = str(scene_contract.get(key) or "").strip()
        if value:
            return value
    card = scene_contract.get("pov_character_card")
    if isinstance(card, dict):
        return str(card.get("name") or card.get("character_name") or "").strip()
    return ""


def _paragraphs(text: str) -> list[str]:
    return [item.strip() for item in re.split(r"\n+", text or "") if item.strip()]


def _sentences(text: str) -> list[str]:
    return [match.group(0).strip() for match in SENTENCE_RE.finditer(text or "") if match.group(0).strip()]


def _split_long_sentence(sentence: str, *, max_chars: int) -> list[str]:
    body = (sentence or "").strip()
    if len(body) <= max_chars:
        return [body] if body else []

    units = [
        match.group(0).strip()
        for match in re.finditer(r"[^，,；;。！？!?\n]+[，,；;。！？!?]?", body)
        if match.group(0).strip()
    ]
    if len(units) <= 1:
        return [body]

    chunks: list[str] = []
    current = ""
    for unit in units:
        if current and len(current) + len(unit) > max_chars:
            chunks.append(_close_clause_as_sentence(current))
            current = unit
        else:
            current += unit
    if current:
        chunks.append(_close_clause_as_sentence(current))
    return chunks or [body]


def _close_clause_as_sentence(text: str) -> str:
    clause = (text or "").strip()
    if not clause:
        return ""
    if clause[-1] in "，,；;":
        return f"{clause[:-1]}。"
    if clause[-1] not in "。！？!?":
        return f"{clause}。"
    return clause


def _cleanup_spacing(text: str) -> str:
    repaired = re.sub(r"[ \t]+", " ", text or "")
    repaired = re.sub(r"\n{3,}", "\n\n", repaired)
    repaired = re.sub(r"([。！？!?])([，,；;])", r"\1", repaired)
    repaired = re.sub(r"，([。！？!?])", r"\1", repaired)
    repaired = re.sub(r"。{2,}", "。", repaired)
    return repaired.strip()
