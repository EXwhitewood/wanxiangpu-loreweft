from __future__ import annotations

import re
from typing import Any


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？!?])\s+|\n+")


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        return "；".join(_clean_text(item) for item in value if _clean_text(item))
    if isinstance(value, dict):
        for key in ("text", "description", "summary", "value", "content"):
            if value.get(key):
                return _clean_text(value.get(key))
        return "；".join(
            f"{key}:{_clean_text(val)}"
            for key, val in value.items()
            if _clean_text(val)
        )
    return re.sub(r"\s+", " ", str(value)).strip()


def _clip(value: str, limit: int) -> str:
    value = _clean_text(value)
    if limit <= 0 or len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return [item for item in value if item not in (None, "")]
    return [value]


def _unique_texts(items: list[Any], limit: int = 5) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = _clean_text(item)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
        if len(result) >= limit:
            break
    return result


def _nested_dict(contract: dict, key: str) -> dict:
    value = contract.get(key)
    return value if isinstance(value, dict) else {}


def _contract_text(contract: dict, key: str) -> str:
    source = _nested_dict(contract, "source_of_truth")
    enrichment = _nested_dict(contract, "editor_enrichment")
    if key in ("goal", "conflict"):
        return _clean_text(source.get(key) or contract.get(key))
    if key == "ending_state":
        return _clean_text(
            enrichment.get("ending_state")
            or contract.get("ending_state")
            or source.get("outline_outcome")
        )
    return _clean_text(enrichment.get(key) or contract.get(key) or source.get(key))


def _contract_list(contract: dict, key: str) -> list[str]:
    source = _nested_dict(contract, "source_of_truth")
    enrichment = _nested_dict(contract, "editor_enrichment")
    values: list[Any] = []
    if key == "must_show":
        values.extend(_as_list(source.get("must_show_outline")))
        values.extend(_as_list(contract.get("must_show")))
        values.extend(_as_list(enrichment.get("additional_must_show")))
    elif key == "forbidden":
        values.extend(_as_list(source.get("forbidden_outline")))
        values.extend(_as_list(contract.get("forbidden")))
        values.extend(_as_list(enrichment.get("additional_forbidden")))
    else:
        values.extend(_as_list(source.get(key)))
        values.extend(_as_list(contract.get(key)))
        values.extend(_as_list(enrichment.get(key)))
    return _unique_texts(values)


def _scene_label(index: int, contract: dict | None = None) -> str:
    contract = contract or {}
    scene_id = _clean_text(contract.get("scene_id"))
    if scene_id:
        return f"S{index + 1}({scene_id})"
    return f"S{index + 1}"


def _scene_position(index: int, total: int) -> str:
    if total <= 1:
        return "独立完整场景"
    if index == 0:
        return "章节开端，负责建立处境和悬念"
    if index == total - 1:
        return "章节结尾，负责收束情绪并留下钩子"
    if index == 1 and total >= 4:
        return "章节前中段，负责推进试探和转折"
    if index == total - 2:
        return "章节后中段，负责加速和加压"
    return "章节中段，负责承接并推进"


def _pacing_hint(contract: dict) -> str:
    style_directive = _nested_dict(contract, "style_directive")
    skeleton = style_directive.get("skeleton") if isinstance(style_directive.get("skeleton"), dict) else {}
    pacing = _clean_text(skeleton.get("pacing")) if isinstance(skeleton, dict) else ""
    if pacing:
        return pacing

    goal = _contract_text(contract, "goal")
    conflict = _contract_text(contract, "conflict")
    text = f"{goal} {conflict}"
    fast_markers = ("追", "战", "逃", "搜查", "发现", "危机", "爆发", "揭示", "冲突")
    slow_markers = ("试探", "对话", "复盘", "犹豫", "观察", "铺垫")
    if any(marker in text for marker in fast_markers):
        return "节奏偏快"
    if any(marker in text for marker in slow_markers):
        return "节奏偏慢"
    return "节奏平稳"


def build_chapter_rhythm_map(
    scene_contracts: list[dict] | None,
    *,
    chapter_number: int | None = None,
    editor_map: str | None = None,
    max_chars: int = 900,
) -> str:
    """Build a compact per-chapter rhythm map for the Writer.

    The editor may supply a hand-authored map. If it does not, this deterministic
    fallback compresses scene contracts into a small stable overview.
    """
    editor_map = _clean_text(editor_map)
    if editor_map:
        return _clip(editor_map, max_chars)

    contracts = [contract for contract in (scene_contracts or []) if isinstance(contract, dict)]
    if not contracts:
        return ""

    prefix = f"第{chapter_number}章" if chapter_number else "本章"
    lines = [f"{prefix}{len(contracts)}个场景："]
    total = len(contracts)
    for index, contract in enumerate(contracts):
        goal = _contract_text(contract, "goal")
        must_show = "；".join(_contract_list(contract, "must_show")[:2])
        ending = _contract_text(contract, "ending_state")
        parts = [
            f"{_scene_label(index, contract)}：{_scene_position(index, total)}",
            _pacing_hint(contract),
        ]
        if goal:
            parts.append(f"目标={_clip(goal, 28)}")
        if must_show:
            parts.append(f"释放/呈现={_clip(must_show, 42)}")
        if ending:
            parts.append(f"结尾={_clip(ending, 36)}")
        lines.append("；".join(part for part in parts if part))
    return _clip("\n".join(lines), max_chars)


def build_next_scene_anchor(scene_contracts: list[dict] | None, scene_index: int) -> str:
    contracts = [contract for contract in (scene_contracts or []) if isinstance(contract, dict)]
    next_index = scene_index + 1
    if next_index >= len(contracts):
        return "无下一场景。本场景是本章结尾，需要完成本章情绪收束，但仍可留下后续章节钩子。"

    contract = contracts[next_index]
    goal = _contract_text(contract, "goal")
    conflict = _contract_text(contract, "conflict")
    opening = _contract_text(contract, "opening_state")
    ending = _contract_text(contract, "ending_state")
    parts = [f"{_scene_label(next_index, contract)}：{_pacing_hint(contract)}"]
    if goal:
        parts.append(f"目标={_clip(goal, 42)}")
    if conflict:
        parts.append(f"冲突={_clip(conflict, 38)}")
    if opening:
        parts.append(f"开场承接={_clip(opening, 38)}")
    if ending:
        parts.append(f"趋向={_clip(ending, 38)}")
    return _clip("；".join(parts), 240)


def build_chapter_rhythm_context(
    scene_contracts: list[dict] | None,
    *,
    scene_index: int,
    previous_scene_anchors: list[dict] | None = None,
    chapter_number: int | None = None,
    editor_map: str | None = None,
) -> dict:
    contracts = [contract for contract in (scene_contracts or []) if isinstance(contract, dict)]
    total = len(contracts)
    return {
        "chapter_rhythm_map": build_chapter_rhythm_map(
            contracts,
            chapter_number=chapter_number,
            editor_map=editor_map,
        ),
        "current_scene_position": _scene_position(scene_index, total),
        "previous_scene_anchors": list(previous_scene_anchors or []),
        "next_scene_anchor": build_next_scene_anchor(contracts, scene_index),
        "is_final_scene": bool(total and scene_index >= total - 1),
    }


def _text_summary(generated_text: str, limit: int = 90) -> str:
    text = _clean_text(generated_text)
    if not text:
        return ""
    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(text) if s.strip()]
    if not sentences:
        return _clip(text, limit)
    if len(sentences) == 1:
        return _clip(sentences[0], limit)
    return _clip(f"{sentences[0]} {sentences[-1]}", limit)


def _infer_mood(generated_text: str, contract: dict) -> str:
    combined = f"{generated_text[:1200]} {_contract_text(contract, 'goal')} {_contract_text(contract, 'conflict')}"
    if any(word in combined for word in ("试探", "旁敲侧击", "对话", "问话")):
        return "对话试探、暗流压迫"
    if any(word in combined for word in ("追", "战", "逃", "爆", "杀", "危机")):
        return "紧迫、高压"
    if any(word in combined for word in ("搜查", "发现", "线索", "证据")):
        return "探索、递进"
    if any(word in combined for word in ("恐惧", "发抖", "冷汗", "慌", "血")):
        return "紧张、不安"
    if any(word in combined for word in ("复盘", "决定", "决心", "收束")):
        return "冷静、收束"
    return "承接推进"


def extract_scene_anchor(
    generated_text: str,
    scene_contract: dict | None,
    *,
    scene_index: int,
    scene_facts: dict | None = None,
    max_chars: int = 420,
) -> dict:
    """Extract a lightweight, deterministic anchor after a scene is generated."""
    contract = scene_contract if isinstance(scene_contract, dict) else {}
    facts = scene_facts if isinstance(scene_facts, dict) else {}
    completed_events = _unique_texts(_as_list(facts.get("completed_events")), limit=2)
    established_facts = _unique_texts(_as_list(facts.get("established_facts")), limit=3)
    must_show = _contract_list(contract, "must_show")[:2]
    forbidden = _contract_list(contract, "forbidden")[:3]

    summary_bits = completed_events or [
        _contract_text(contract, "goal"),
        _contract_text(contract, "ending_state"),
    ]
    summary = "；".join(_unique_texts(summary_bits, limit=2)) or _text_summary(generated_text)
    released = _unique_texts(established_facts + must_show, limit=4)
    scene_ending = _clean_text(facts.get("scene_ending")) or _contract_text(contract, "ending_state")

    anchor = {
        "scene_index": scene_index,
        "scene_id": contract.get("scene_id") or f"s{scene_index + 1}",
        "summary": _clip(summary, 120),
        "released_info": [_clip(item, 60) for item in released],
        "withheld_or_forbidden": [_clip(item, 60) for item in forbidden],
        "mood": _infer_mood(generated_text, contract),
        "ending_state": _clip(scene_ending, 100),
    }

    rendered = render_scene_anchor(anchor)
    if len(rendered) > max_chars:
        anchor["ending_state"] = _clip(anchor["ending_state"], 55)
        anchor["released_info"] = anchor["released_info"][:3]
        anchor["withheld_or_forbidden"] = anchor["withheld_or_forbidden"][:2]
    return anchor


def render_scene_anchor(anchor: dict) -> str:
    label = f"S{int(anchor.get('scene_index', 0)) + 1}"
    scene_id = _clean_text(anchor.get("scene_id"))
    if scene_id:
        label += f"({scene_id})"
    parts = [f"【{label}已生成】{_clean_text(anchor.get('summary'))}"]
    released = _unique_texts(_as_list(anchor.get("released_info")), limit=4)
    if released:
        parts.append("已释放：" + "；".join(released))
    withheld = _unique_texts(_as_list(anchor.get("withheld_or_forbidden")), limit=3)
    if withheld:
        parts.append("未释放/仍禁止：" + "；".join(withheld))
    mood = _clean_text(anchor.get("mood"))
    if mood:
        parts.append(f"氛围：{mood}")
    ending = _clean_text(anchor.get("ending_state"))
    if ending:
        parts.append("结尾状态：" + ending)
    return "。".join(part for part in parts if part)


def render_chapter_rhythm_context(context: dict | None) -> str:
    if not isinstance(context, dict) or not context:
        return ""

    lines = [
        "### 章节节奏地图与前后锚点",
        "使用规则：只写当前场景合同覆盖的正文；已释放信息只短句承接，不重复详写；下一场景锚点只用于控制过渡和留气口，不得提前写出下一场景正文或结论。",
        "证据释放规则：信件、纸条、遗书、碑文、药方等只能给碎片线索和疑问，不得替正文一次性解释完整阴谋、同谋关系或最终答案。",
    ]

    rhythm_map = _clean_text(context.get("chapter_rhythm_map"))
    if rhythm_map:
        lines.append(f"\n#### 章节节奏地图\n{rhythm_map}")

    position = _clean_text(context.get("current_scene_position"))
    if position:
        lines.append(f"\n#### 当前场景位置\n{position}")

    previous = context.get("previous_scene_anchors") or []
    if isinstance(previous, list) and previous:
        lines.append("\n#### 前场景摘要锚点")
        for anchor in previous[-5:]:
            if isinstance(anchor, dict):
                lines.append("- " + render_scene_anchor(anchor))

    next_anchor = _clean_text(context.get("next_scene_anchor"))
    if next_anchor:
        lines.append(f"\n#### 下一场景预告\n{next_anchor}")

    if context.get("is_final_scene"):
        lines.append("\n本场景是本章最后一场：允许情绪收束，但不要把后续章节需要展开的谜底一次性说尽。")
    else:
        lines.append("\n本场景不是本章结尾：结尾应留下行动动机、问题压力或自然转场，不要写成全章终局。")

    return "\n".join(lines).strip()
