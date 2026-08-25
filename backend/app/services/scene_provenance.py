from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return [item for item in value if item not in (None, "")]
    return [value]


def _deep_merge(base: dict, override: dict) -> dict:
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def _merge_valid_provenance(base: dict, provided: dict) -> dict:
    nested_dict_fields = {
        "scene_identity",
        "current_facts",
        "original_facts",
        "spatial_anchor",
        "timeline_anchor",
    }
    for key, value in provided.items():
        if key in nested_dict_fields:
            if not isinstance(value, dict):
                continue
            target = base.get(key)
            if not isinstance(target, dict):
                target = {}
                base[key] = target
            _deep_merge(target, value)
        elif key == "clues":
            if isinstance(value, list):
                base[key] = value
        else:
            base[key] = value
    return base


def _normalize_clue(clue: Any) -> dict:
    if isinstance(clue, dict):
        return {
            "description": clue.get("description", ""),
            "source_actor": clue.get("source_actor", "未知"),
            "placement_time": clue.get("placement_time", "未知"),
            "discovery_condition": clue.get("discovery_condition", "未知"),
        }
    return {
        "description": str(clue),
        "source_actor": "未知",
        "placement_time": "未知",
        "discovery_condition": "未知",
    }


def _normalize_character_fate(card: dict) -> dict | None:
    fate = card.get("original_fate")
    if not fate:
        return None

    name = card.get("name", "未知")
    if isinstance(fate, dict):
        return {
            "name": name,
            "cause": fate.get("cause", ""),
            "timeline": fate.get("timeline", ""),
            "antagonist": fate.get("antagonist", ""),
            "key_events": _as_list(fate.get("key_events")),
            "raw": fate,
        }

    return {
        "name": name,
        "cause": str(fate),
        "timeline": "",
        "antagonist": "",
        "key_events": [],
        "raw": fate,
    }


def build_scene_provenance(
    scene_contract: dict | None,
    chapter_state: dict | None = None,
    character_cards: list | None = None,
) -> dict:
    contract = scene_contract or {}
    chapter_state = chapter_state or {}
    character_cards = character_cards or []

    current_facts = {
        "established_facts": _as_list(chapter_state.get("established_facts")),
        "character_states": chapter_state.get("character_states", {}) or {},
        "completed_events": _as_list(chapter_state.get("completed_events")),
        "active_constraints": _as_list(chapter_state.get("active_constraints")),
    }

    original_facts = []
    for card in character_cards:
        if isinstance(card, dict):
            fate = _normalize_character_fate(card)
            if fate:
                original_facts.append(fate)

    return {
        "scene_identity": {
            "scene_id": contract.get("scene_id", ""),
            "chapter_number": contract.get("chapter_number"),
            "scene_index": contract.get("scene_index"),
            "pov_character": contract.get("pov_character", ""),
        },
        "current_facts": current_facts,
        "original_facts": {
            "character_fates": original_facts,
        },
        "spatial_anchor": {
            "location_anchor": contract.get("location_anchor", ""),
            "current_location": contract.get("current_location", ""),
            "destination_location": contract.get("destination_location", ""),
            "distance_state": contract.get("distance_state", ""),
        },
        "timeline_anchor": {
            "temporal_anchor": contract.get("temporal_anchor", ""),
            "opening_state": contract.get("opening_state", ""),
            "forbidden_recap_events": _as_list(contract.get("forbidden_recap_events")),
            "ending_state": contract.get("ending_state", ""),
        },
        "clues": [
            _normalize_clue(clue)
            for clue in _as_list(contract.get("clues"))
        ],
    }


def normalize_scene_contract(
    scene_contract: dict | None,
    *,
    chapter_number: int | None = None,
    scene_index: int | None = None,
    scene_beat: dict | None = None,
    chapter_state: dict | None = None,
    character_cards: list | None = None,
    pov_character: str | None = None,
    previous_scene_ending: str = "",
) -> dict:
    contract = dict(scene_contract) if isinstance(scene_contract, dict) else {}
    beat = scene_beat if isinstance(scene_beat, dict) else {}
    chapter_state = chapter_state if isinstance(chapter_state, dict) else {}
    character_cards = character_cards if isinstance(character_cards, list) else []

    if chapter_number is not None:
        contract["chapter_number"] = chapter_number
    if scene_index is not None:
        contract["scene_index"] = scene_index

    pov = contract.get("pov_character") or pov_character or ""
    if pov:
        contract["pov_character"] = pov

    has_layers = "source_of_truth" in contract and "editor_enrichment" in contract

    if not contract.get("pov_lock") and pov:
        contract["pov_lock"] = f"仅限{pov}的感知和思维，不得描写其他角色内心"

    if not contract.get("temporal_anchor"):
        contract["temporal_anchor"] = "故事开始" if (chapter_number == 1 and scene_index == 0) else "接续上一场景结尾"

    if not contract.get("current_location"):
        contract["current_location"] = beat.get("location", beat.get("setting", "")) or "未知"

    if not contract.get("destination_location"):
        contract["destination_location"] = beat.get("destination_location", "")

    if not contract.get("distance_state"):
        contract["distance_state"] = beat.get("distance_state", "")

    if not contract.get("location_anchor"):
        parts = []
        current_location = contract.get("current_location", "")
        destination_location = contract.get("destination_location", "")
        distance_state = contract.get("distance_state", "")
        if current_location and current_location != "未知":
            parts.append(f"当前位置：{current_location}")
        if destination_location:
            parts.append(f"目的地：{destination_location}")
        if distance_state:
            parts.append(f"距离：{distance_state}")
        contract["location_anchor"] = "；".join(parts) if parts else "当前位置：未知；目的地：未知；距离：未知"

    if not contract.get("opening_state"):
        if previous_scene_ending:
            contract["opening_state"] = "承接上一场景结尾"
        else:
            contract["opening_state"] = "故事开始"

    if not contract.get("forbidden_recap_events"):
        contract["forbidden_recap_events"] = _as_list(chapter_state.get("completed_events"))[:5]

    if not contract.get("clues"):
        contract["clues"] = []

    if has_layers:
        enrichment = contract.get("editor_enrichment", {})
        if not isinstance(enrichment, dict):
            enrichment = {}
        if not enrichment.get("pov_lock") and contract.get("pov_lock"):
            enrichment["pov_lock"] = contract["pov_lock"]
        if not enrichment.get("temporal_anchor") and contract.get("temporal_anchor"):
            enrichment["temporal_anchor"] = contract["temporal_anchor"]
        if not enrichment.get("opening_state") and contract.get("opening_state"):
            enrichment["opening_state"] = contract["opening_state"]
        if not enrichment.get("forbidden_recap_events") and contract.get("forbidden_recap_events"):
            enrichment["forbidden_recap_events"] = contract["forbidden_recap_events"]
        if not enrichment.get("current_location") and contract.get("current_location"):
            enrichment["current_location"] = contract["current_location"]
        if not enrichment.get("destination_location") and contract.get("destination_location"):
            enrichment["destination_location"] = contract["destination_location"]
        if not enrichment.get("distance_state") and contract.get("distance_state"):
            enrichment["distance_state"] = contract["distance_state"]
        if not enrichment.get("location_anchor") and contract.get("location_anchor"):
            enrichment["location_anchor"] = contract["location_anchor"]
        if not enrichment.get("clues") and contract.get("clues"):
            enrichment["clues"] = contract["clues"]
        contract["editor_enrichment"] = enrichment

    provenance = build_scene_provenance(contract, chapter_state=chapter_state, character_cards=character_cards)
    provided_provenance = contract.get("scene_provenance")
    if isinstance(provided_provenance, dict):
        provenance = _merge_valid_provenance(provenance, provided_provenance)
    contract["scene_provenance"] = provenance
    return contract


# ---------------------------------------------------------------------------
# 旧字段 → 新结构映射表（Phase 3 双轨兼容）
# ---------------------------------------------------------------------------
_GENRE_ENRICHMENT_FIELDS = {
    "current_location", "destination_location", "distance_state",
    "location_anchor", "clues",
}

_POV_LOCK_TO_POV_MODE = {
    "limited": "limited",
    "omniscient": "omniscient",
    "objective": "objective",
    "multi": "multi",
}


def normalize_scene_contract_v2(
    scene_contract: dict | None,
    *,
    genre_profile: dict | None = None,
    chapter_number: int | None = None,
    scene_index: int | None = None,
    scene_beat: dict | None = None,
    chapter_state: dict | None = None,
    character_cards: list | None = None,
    pov_character: str | None = None,
    previous_scene_ending: str = "",
) -> dict:
    """V2 标准化：在 V1 基础上映射旧字段到 pov / genre_enrichment / style_directive。

    双轨兼容策略：
    - 写入新字段（pov / genre_enrichment / style_directive）
    - 保留旧字段（pov_lock / current_location 等）
    - Writer / Critic 读取优先级：新结构 > 旧字段 > scene_beat fallback
    """
    # 先走 V1 标准化，确保旧字段完整
    contract = normalize_scene_contract(
        scene_contract,
        chapter_number=chapter_number,
        scene_index=scene_index,
        scene_beat=scene_beat,
        chapter_state=chapter_state,
        character_cards=character_cards,
        pov_character=pov_character,
        previous_scene_ending=previous_scene_ending,
    )

    # --- 映射 pov_lock → pov 结构 ---
    if "pov" not in contract or not isinstance(contract.get("pov"), dict):
        pov_lock = contract.get("pov_lock", "")
        mode = "limited"
        for key, val in _POV_LOCK_TO_POV_MODE.items():
            if key in pov_lock.lower():
                mode = val
                break
        contract["pov"] = {
            "mode": mode,
            "character": contract.get("pov_character", ""),
            "knowledge_boundary": pov_lock,
        }

    # --- 映射题材偏移字段 → genre_enrichment ---
    if "genre_enrichment" not in contract or not isinstance(contract.get("genre_enrichment"), dict):
        genre_enrichment = {}
        for field in _GENRE_ENRICHMENT_FIELDS:
            value = contract.get(field)
            if value and value != "未知" and value != []:
                genre_enrichment[field] = value
        # 如果有 Genre Profile 的扩展字段，也从合同顶层提取
        if genre_profile:
            extensions = genre_profile.get("contract_extensions", [])
            for ext in extensions:
                if isinstance(ext, dict):
                    field_name = ext.get("field", "")
                    if field_name and field_name not in genre_enrichment:
                        value = contract.get(field_name)
                        if value:
                            genre_enrichment[field_name] = value
        if genre_enrichment:
            contract["genre_enrichment"] = genre_enrichment
        else:
            contract["genre_enrichment"] = {}

    # --- 确保 style_directive 存在 ---
    if "style_directive" not in contract:
        contract["style_directive"] = {}

    if genre_profile:
        contract["_genre_profile"] = genre_profile

    return contract


def render_scene_provenance(provenance: dict | None) -> str:
    if not isinstance(provenance, dict) or not provenance:
        return ""

    lines: list[str] = []

    identity = provenance.get("scene_identity", {}) or {}
    if not isinstance(identity, dict):
        identity = {}
    identity_parts = []
    for key, label in [
        ("scene_id", "场景ID"),
        ("chapter_number", "章节"),
        ("scene_index", "场景序号"),
        ("pov_character", "视角角色"),
    ]:
        value = identity.get(key)
        if value not in (None, "", []):
            identity_parts.append(f"{label}：{value}")
    if identity_parts:
        lines.append("场景身份：" + "；".join(identity_parts))

    spatial = provenance.get("spatial_anchor", {}) or {}
    spatial_parts = []
    if isinstance(spatial, str):
        spatial_parts.append(f"地理位置：{spatial}")
        spatial = {}
    elif not isinstance(spatial, dict):
        spatial = {}
    for key, label in [
        ("location_anchor", "地理位置"),
        ("current_location", "当前位置"),
        ("destination_location", "目的地"),
        ("distance_state", "距离状态"),
    ]:
        value = spatial.get(key)
        if value:
            spatial_parts.append(f"{label}：{value}")
    if spatial_parts:
        lines.append("空间层：" + "；".join(spatial_parts))

    timeline = provenance.get("timeline_anchor", {}) or {}
    timeline_parts = []
    if isinstance(timeline, str):
        timeline_parts.append(f"时间锚点：{timeline}")
        timeline = {}
    elif not isinstance(timeline, dict):
        timeline = {}
    for key, label in [
        ("temporal_anchor", "时间锚点"),
        ("opening_state", "开场状态"),
        ("ending_state", "结束状态"),
    ]:
        value = timeline.get(key)
        if value:
            timeline_parts.append(f"{label}：{value}")
    forbidden_recap_events = _as_list(timeline.get("forbidden_recap_events"))
    if forbidden_recap_events:
        timeline_parts.append("禁止重写事件：" + "；".join(forbidden_recap_events))
    if timeline_parts:
        lines.append("时间层：" + "；".join(timeline_parts))

    current_facts = provenance.get("current_facts", {}) or {}
    if isinstance(current_facts, list):
        if current_facts:
            lines.append("当前事实层（已确立事实）：" + "；".join(map(str, current_facts)))
        current_facts = {}
    elif not isinstance(current_facts, dict):
        current_facts = {"established_facts": _as_list(current_facts)}
    established_facts = _as_list(current_facts.get("established_facts"))
    if established_facts:
        lines.append("当前事实层（已确立事实）：" + "；".join(established_facts))

    character_states = current_facts.get("character_states", {}) or {}
    if character_states:
        state_parts = []
        for name, state in character_states.items():
            if isinstance(state, dict):
                # dict 结构渲染为可读键值对，避免输出 Python dict repr
                kv_parts = []
                for sk, sv in state.items():
                    if sv in (None, "", [], {}):
                        continue
                    if isinstance(sv, list):
                        sv = "、".join(str(v) for v in sv)
                    kv_parts.append(f"{sk}：{sv}")
                state_str = "；".join(kv_parts) if kv_parts else "无已知状态"
            else:
                state_str = str(state)
            state_parts.append(f"{name}（{state_str}）")
        lines.append("当前事实层（角色状态）：" + "；".join(state_parts))

    completed_events = _as_list(current_facts.get("completed_events"))
    if completed_events:
        lines.append("当前事实层（已完成事件）：" + "；".join(completed_events))

    original_facts = provenance.get("original_facts", {}) or {}
    if not isinstance(original_facts, dict):
        original_facts = {}
    character_fates = original_facts.get("character_fates", []) or []
    if character_fates:
        fate_parts = []
        for fate in character_fates:
            if not isinstance(fate, dict):
                continue
            part = f"{fate.get('name', '未知')}：{fate.get('cause', '')}"
            if fate.get("timeline"):
                part += f"（时间：{fate['timeline']}）"
            if fate.get("antagonist"):
                part += f"（加害者：{fate['antagonist']}）"
            key_events = _as_list(fate.get("key_events"))
            if key_events:
                part += f"（关键事件：{'；'.join(map(str, key_events))}）"
            fate_parts.append(part)
        if fate_parts:
            lines.append("参考设定层（角色命运预设，非当前时间线事实，禁止在正文中写成已发生）：" + "；".join(fate_parts))

    clues = provenance.get("clues", []) or []
    if clues:
        clue_parts = []
        for clue in clues:
            if not isinstance(clue, dict):
                continue
            clue_parts.append(
                f"{clue.get('description', '')}｜放置者：{clue.get('source_actor', '未知')}｜放置时间：{clue.get('placement_time', '未知')}｜发现条件：{clue.get('discovery_condition', '未知')}"
            )
        if clue_parts:
            lines.append("线索层：" + "；".join(clue_parts))

    return "\n".join(lines)


import logging

_logger = logging.getLogger(__name__)

_DICT_FIELDS_CONTRACT = {
    "source_of_truth": "critical",
    "editor_enrichment": "optional",
    "scene_provenance": "optional",
}

_DICT_FIELDS_CONTEXT = {
    "scene_beat": "optional",
    "chapter_rhythm_context": "optional",
    "relevant_rules": "optional",
    "style_embedding": "optional",
    "persona_card": "optional",
    "style_statistics": "optional",
    "evolution_report": "optional",
}

_LIST_FIELDS_CONTEXT = {
    "character_cards": "optional",
    "location_cards": "optional",
    "historical_details": "optional",
    "style_sample_passages": "optional",
}


def _coerce_to_dict(value: Any, field_path: str, severity: str) -> tuple[dict, list[dict]]:
    warnings = []
    if isinstance(value, dict):
        return value, warnings
    if value is None or value == "":
        return {}, warnings
    if isinstance(value, str):
        _logger.warning(f"[normalize_writer_context] {field_path} expected dict, got str: {value[:80]}")
        warnings.append({
            "field": field_path,
            "expected_type": "dict",
            "received_type": "str",
            "severity": severity,
            "coerced_to": "{}",
            "original_preview": value[:100],
        })
        return {}, warnings
    if isinstance(value, list):
        _logger.warning(f"[normalize_writer_context] {field_path} expected dict, got list")
        warnings.append({
            "field": field_path,
            "expected_type": "dict",
            "received_type": "list",
            "severity": severity,
            "coerced_to": "{}",
        })
        return {}, warnings
    _logger.warning(f"[normalize_writer_context] {field_path} expected dict, got {type(value).__name__}")
    warnings.append({
        "field": field_path,
        "expected_type": "dict",
        "received_type": type(value).__name__,
        "severity": severity,
        "coerced_to": "{}",
    })
    return {}, warnings


def _coerce_to_list(value: Any, field_path: str) -> tuple[list, list[dict]]:
    warnings = []
    if isinstance(value, list):
        return value, warnings
    if value is None or value == "":
        return [], warnings
    if isinstance(value, str):
        _logger.warning(f"[normalize_writer_context] {field_path} expected list, got str")
        warnings.append({
            "field": field_path,
            "expected_type": "list",
            "received_type": "str",
            "coerced_to": "[]",
        })
        return [], warnings
    if isinstance(value, dict):
        _logger.warning(f"[normalize_writer_context] {field_path} expected list, got dict")
        warnings.append({
            "field": field_path,
            "expected_type": "list",
            "received_type": "dict",
            "coerced_to": "[]",
        })
        return [], warnings
    return [value], warnings


def _keep_dict_items(value: list, field_path: str) -> tuple[list[dict], list[dict]]:
    kept = [item for item in value if isinstance(item, dict)]
    dropped_count = len(value) - len(kept)
    if not dropped_count:
        return kept, []
    _logger.warning(f"[normalize_writer_context] {field_path} dropped {dropped_count} non-dict item(s)")
    return kept, [{
        "field": field_path,
        "expected_type": "list[dict]",
        "received_type": "mixed_list",
        "severity": "optional",
        "dropped_items": dropped_count,
    }]


def normalize_writer_context(core_context: dict) -> tuple[dict, list[dict]]:
    all_warnings: list[dict] = []
    ctx = dict(core_context)
    scp = ctx.get("scene_context_package", {})
    if not isinstance(scp, dict):
        scp = {}
        ctx["scene_context_package"] = scp
    else:
        scp = dict(scp)
        ctx["scene_context_package"] = scp

    scene_contract = ctx.get("scene_contract") or scp.get("scene_contract")
    if scene_contract is not None and not isinstance(scene_contract, dict):
        _logger.warning(f"[normalize_writer_context] scene_contract expected dict, got {type(scene_contract).__name__}")
        all_warnings.append({
            "field": "scene_contract",
            "expected_type": "dict",
            "received_type": type(scene_contract).__name__,
            "severity": "critical",
            "coerced_to": "None",
        })
        scene_contract = None
        if "scene_contract" in scp:
            scp["scene_contract"] = None
        ctx["scene_contract"] = None

    if isinstance(scene_contract, dict):
        for field, severity in _DICT_FIELDS_CONTRACT.items():
            value = scene_contract.get(field)
            if value is not None:
                coerced, ws = _coerce_to_dict(value, f"scene_contract.{field}", severity)
                scene_contract[field] = coerced
                all_warnings.extend(ws)

        source_of_truth = scene_contract.get("source_of_truth", {})
        if isinstance(source_of_truth, dict):
            for list_field in ("required_context_refs", "must_show_outline", "forbidden_outline", "foreshadowing_ops"):
                value = source_of_truth.get(list_field)
                if value is not None:
                    coerced, ws = _coerce_to_list(value, f"scene_contract.source_of_truth.{list_field}")
                    source_of_truth[list_field] = coerced
                    all_warnings.extend(ws)

        editor_enrichment = scene_contract.get("editor_enrichment", {})
        if isinstance(editor_enrichment, dict):
            for list_field in ("additional_must_show", "additional_forbidden", "forbidden_recap_events", "clues"):
                value = editor_enrichment.get(list_field)
                if value is not None:
                    coerced, ws = _coerce_to_list(value, f"scene_contract.editor_enrichment.{list_field}")
                    editor_enrichment[list_field] = coerced
                    all_warnings.extend(ws)

        scene_provenance = scene_contract.get("scene_provenance", {})
        if isinstance(scene_provenance, dict):
            for dict_field in ("scene_identity", "current_facts", "original_facts", "spatial_anchor", "timeline_anchor"):
                value = scene_provenance.get(dict_field)
                if value is not None:
                    coerced, ws = _coerce_to_dict(value, f"scene_contract.scene_provenance.{dict_field}", "optional")
                    scene_provenance[dict_field] = coerced
                    all_warnings.extend(ws)
            value = scene_provenance.get("clues")
            if value is not None:
                coerced, ws = _coerce_to_list(value, "scene_contract.scene_provenance.clues")
                all_warnings.extend(ws)
                scene_provenance["clues"], ws = _keep_dict_items(
                    coerced, "scene_contract.scene_provenance.clues",
                )
                all_warnings.extend(ws)

            current_facts = scene_provenance.get("current_facts", {})
            if isinstance(current_facts, dict):
                value = current_facts.get("character_states")
                if value is not None:
                    coerced, ws = _coerce_to_dict(
                        value, "scene_contract.scene_provenance.current_facts.character_states", "optional",
                    )
                    current_facts["character_states"] = coerced
                    all_warnings.extend(ws)

        for list_field in ("must_show", "forbidden", "must_show_outline", "forbidden_outline",
                           "additional_must_show", "additional_forbidden", "foreshadowing_ops"):
            value = scene_contract.get(list_field)
            if value is not None:
                coerced, ws = _coerce_to_list(value, f"scene_contract.{list_field}")
                scene_contract[list_field] = coerced
                all_warnings.extend(ws)

        if "scene_contract" in scp:
            scp["scene_contract"] = scene_contract
        ctx["scene_contract"] = scene_contract

    for field, severity in _DICT_FIELDS_CONTEXT.items():
        value = scp.get(field)
        if value is not None:
            coerced, ws = _coerce_to_dict(value, f"scene_context_package.{field}", severity)
            scp[field] = coerced
            all_warnings.extend(ws)

    style_statistics = scp.get("style_statistics", {})
    if isinstance(style_statistics, dict):
        value = style_statistics.get("global")
        if value is not None:
            coerced, ws = _coerce_to_dict(value, "scene_context_package.style_statistics.global", "optional")
            style_statistics["global"] = coerced
            all_warnings.extend(ws)

    relevant_rules = scp.get("relevant_rules", {})
    if isinstance(relevant_rules, dict):
        for list_field in ("critical_index", "relevant_details"):
            value = relevant_rules.get(list_field)
            if value is not None:
                coerced, ws = _coerce_to_list(value, f"scene_context_package.relevant_rules.{list_field}")
                all_warnings.extend(ws)
                relevant_rules[list_field], ws = _keep_dict_items(
                    coerced, f"scene_context_package.relevant_rules.{list_field}",
                )
                all_warnings.extend(ws)

    for field, _severity in _LIST_FIELDS_CONTEXT.items():
        value = scp.get(field)
        if value is not None:
            coerced, ws = _coerce_to_list(value, f"scene_context_package.{field}")
            scp[field] = coerced
            all_warnings.extend(ws)

    chapter_state = scp.get("chapter_state")
    if chapter_state is not None and not isinstance(chapter_state, dict):
        coerced, ws = _coerce_to_dict(chapter_state, "scene_context_package.chapter_state", "optional")
        scp["chapter_state"] = coerced
        all_warnings.extend(ws)

    return ctx, all_warnings
