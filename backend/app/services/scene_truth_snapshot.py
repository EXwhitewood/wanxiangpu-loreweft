import logging

_logger = logging.getLogger(__name__)

_PRIORITY_SOURCE_OF_TRUTH = 4
_PRIORITY_CANONICAL = 3
_PRIORITY_EDITOR_ENRICHMENT = 2
_PRIORITY_MODEL_INFERENCE = 1


class StoryStateProjector:
    @staticmethod
    def get_pov_location(story_state: dict) -> str:
        pov = story_state.get("pov_character", "")
        obj_state = story_state.get("objective_state", {})
        if pov and isinstance(obj_state, dict):
            pov_data = obj_state.get(pov, {})
            if isinstance(pov_data, dict):
                loc = pov_data.get("location", "")
                if loc:
                    return loc
        return story_state.get("current_location", "")

    @staticmethod
    def get_character_location(story_state: dict, character_name: str) -> str:
        obj_state = story_state.get("objective_state", {})
        if isinstance(obj_state, dict):
            char_data = obj_state.get(character_name, {})
            if isinstance(char_data, dict):
                loc = char_data.get("location", "")
                if loc:
                    return loc
        return story_state.get("current_location", "")

    @staticmethod
    def normalize_character_state(state: dict) -> dict:
        physical = state.get("physical_state") or state.get("physical", [])
        if not isinstance(physical, list):
            physical = []
        emotional = state.get("emotional_state") or state.get("emotional", [])
        if not isinstance(emotional, list):
            emotional = []
        inventory = state.get("inventory", [])
        if not isinstance(inventory, list):
            inventory = []
        location = state.get("location", "")
        return {
            "physical": physical,
            "emotional": emotional,
            "inventory": inventory,
            "location": location,
        }

    @staticmethod
    def get_all_characters(story_state: dict) -> dict:
        chars = story_state.get("objective_state", {})
        if not isinstance(chars, dict):
            chars = story_state.get("characters") or story_state.get("character_states", {})
        if not isinstance(chars, dict):
            return {}
        result = {}
        for name, state in chars.items():
            if isinstance(state, dict):
                result[name] = StoryStateProjector.normalize_character_state(state)
        return result


class TruthConflictError(Exception):
    def __init__(self, conflicts: list[dict]):
        self.conflicts = conflicts
        messages = []
        for c in conflicts:
            messages.append(
                f"{c['field']}: {c['all_values']}"
            )
        super().__init__(f"同优先级来源冲突: {'; '.join(messages)}")


def build_scene_truth_snapshot(
    story_state: dict,
    chapter_state: dict,
    scene_contract: dict,
    previous_scene_ending: str = "",
    scene_id: str = "",
    chapter_baseline: dict | None = None,
) -> dict:
    snapshot = {
        "scene_id": scene_id,
        "pov_character": "",
        "narrative_time": "",
        "current_location": "",
        "allowed_locations": [],
        "forbidden_locations": [],
        "character_states": {},
        "completed_events": [],
        "forbidden_recap_events": [],
        # Narrative-contract obligations are kept separately from historical
        # completed events.  Treating the latter as the former makes an
        # apparently non-empty snapshot pass contract protection vacuously.
        "contract_obligations": [],
        "contract_required_anchors": [],
        "contract_forbidden_items": [],
        "previous_scene_ending": previous_scene_ending,
    }

    field_sources: dict[str, list[dict]] = {
        "narrative_time": [],
        "current_location": [],
    }

    _merge_model_inference(snapshot, chapter_state, field_sources)
    _merge_editor_enrichment(snapshot, scene_contract, field_sources)
    _merge_canonical_story_state(snapshot, story_state, field_sources)
    _merge_source_of_truth(snapshot, scene_contract, field_sources)
    _merge_contract_protection(snapshot, scene_contract, story_state)

    _check_high_priority_conflicts(field_sources)

    if snapshot["current_location"]:
        snapshot["allowed_locations"].append(snapshot["current_location"])

    return snapshot


def validate_first_scene_baseline(
    chapter_baseline: dict,
    story_state: dict,
    scene_contract: dict,
) -> list[dict]:
    if not chapter_baseline or not isinstance(chapter_baseline, dict):
        return []

    conflicts = []
    bl_time = chapter_baseline.get("narrative_time", "")
    bl_location = StoryStateProjector.get_pov_location(chapter_baseline)

    entry_precondition = scene_contract.get("entry_precondition", {}) if isinstance(scene_contract, dict) else {}
    opening_transition = scene_contract.get("opening_transition", "") if isinstance(scene_contract, dict) else ""

    sot = scene_contract.get("source_of_truth", {}) if isinstance(scene_contract, dict) else {}
    sot_time = sot.get("time_anchor", "")
    sot_location = sot.get("location_anchor", "")

    if entry_precondition and isinstance(entry_precondition, dict):
        ep_time = entry_precondition.get("time", "")
        ep_location = entry_precondition.get("location", "")
        if ep_time:
            sot_time = ep_time
        if ep_location:
            sot_location = ep_location

    if opening_transition:
        return conflicts

    if bl_time and sot_time and bl_time != sot_time:
        conflicts.append({
            "field": "narrative_time",
            "baseline_value": bl_time,
            "outline_value": sot_time,
        })

    if bl_location and sot_location and bl_location.strip() != sot_location.strip():
        if bl_location not in sot_location and sot_location not in bl_location:
            conflicts.append({
                "field": "current_location",
                "baseline_value": bl_location,
                "outline_value": sot_location,
            })

    return conflicts


def _record_source(field_sources: dict, field: str, value: str, priority: int, label: str):
    field_sources[field].append({"value": value, "priority": priority, "label": label})


def _check_high_priority_conflicts(field_sources: dict):
    conflicts = []
    for field, sources in field_sources.items():
        by_priority: dict[int, list[dict]] = {}
        for s in sources:
            by_priority.setdefault(s["priority"], []).append(s)
        for priority, group in by_priority.items():
            if priority < _PRIORITY_SOURCE_OF_TRUTH:
                continue
            if len(group) < 2:
                continue
            values = set(s["value"] for s in group)
            if len(values) > 1:
                conflict_values = {s["label"]: s["value"] for s in group}
                conflicts.append({
                    "field": field,
                    "priority": priority,
                    "all_values": conflict_values,
                })
        high_sources = [s for s in sources if s["priority"] >= _PRIORITY_SOURCE_OF_TRUTH]
        if len(high_sources) >= 2:
            labels_seen = set()
            for s in high_sources:
                labels_seen.add(s["label"])
            if len(labels_seen) >= 2:
                values_by_label = {}
                for s in high_sources:
                    values_by_label[s["label"]] = s["value"]
                unique_vals = set(values_by_label.values())
                if len(unique_vals) > 1:
                    already = any(
                        c["field"] == field for c in conflicts
                    )
                    if not already:
                        conflicts.append({
                            "field": field,
                            "priority": -1,
                            "all_values": values_by_label,
                        })
    if conflicts:
        raise TruthConflictError(conflicts)


def _merge_model_inference(snapshot: dict, chapter_state: dict, field_sources: dict):
    if not chapter_state or not isinstance(chapter_state, dict):
        return

    ch_time = chapter_state.get("narrative_time") or chapter_state.get("current_time")
    if ch_time and isinstance(ch_time, str) and not snapshot["narrative_time"]:
        snapshot["narrative_time"] = ch_time
        _record_source(field_sources, "narrative_time", ch_time, _PRIORITY_MODEL_INFERENCE, "model_inference")

    ch_location = chapter_state.get("current_location") or chapter_state.get("location")
    if ch_location and isinstance(ch_location, str) and not snapshot["current_location"]:
        snapshot["current_location"] = ch_location
        _record_source(field_sources, "current_location", ch_location, _PRIORITY_MODEL_INFERENCE, "model_inference")

    ch_left = chapter_state.get("left_locations") or chapter_state.get("departed_locations")
    if isinstance(ch_left, list):
        for loc in ch_left:
            if isinstance(loc, str) and loc not in snapshot["forbidden_locations"]:
                snapshot["forbidden_locations"].append(loc)

    ch_chars = chapter_state.get("characters") or chapter_state.get("character_states")
    if isinstance(ch_chars, dict):
        for name, state in ch_chars.items():
            if isinstance(state, dict) and name not in snapshot["character_states"]:
                snapshot["character_states"][name] = _normalize_character_state(state)

    ch_completed = chapter_state.get("completed_events") or chapter_state.get("events_completed")
    if isinstance(ch_completed, list):
        for evt in ch_completed:
            if isinstance(evt, str) and evt not in snapshot["completed_events"]:
                snapshot["completed_events"].append(evt)


def _merge_editor_enrichment(snapshot: dict, scene_contract: dict, field_sources: dict):
    ee = scene_contract.get("editor_enrichment") if isinstance(scene_contract, dict) else None
    if not ee or not isinstance(ee, dict):
        return

    ee_time = ee.get("temporal_anchor") or ee.get("narrative_time")
    if ee_time and isinstance(ee_time, str):
        snapshot["narrative_time"] = ee_time
        _record_source(field_sources, "narrative_time", ee_time, _PRIORITY_EDITOR_ENRICHMENT, "editor_enrichment")

    ee_location = ee.get("spatial_anchor") or ee.get("current_location")
    if ee_location and isinstance(ee_location, str):
        snapshot["current_location"] = ee_location
        _record_source(field_sources, "current_location", ee_location, _PRIORITY_EDITOR_ENRICHMENT, "editor_enrichment")

    opening_state = ee.get("opening_state")
    if isinstance(opening_state, dict):
        for char_name, char_state in opening_state.items():
            if isinstance(char_state, dict):
                snapshot["character_states"][char_name] = _normalize_character_state(char_state)


def _merge_canonical_story_state(snapshot: dict, story_state: dict, field_sources: dict):
    if not story_state or not isinstance(story_state, dict):
        return

    narrative_time = story_state.get("narrative_time") or story_state.get("current_time")
    if narrative_time and isinstance(narrative_time, str):
        snapshot["narrative_time"] = narrative_time
        _record_source(field_sources, "narrative_time", narrative_time, _PRIORITY_CANONICAL, "canonical_story_state")

    location = StoryStateProjector.get_pov_location(story_state)
    if location and isinstance(location, str):
        snapshot["current_location"] = location
        _record_source(field_sources, "current_location", location, _PRIORITY_CANONICAL, "canonical_story_state")

    left_locations = story_state.get("left_locations") or story_state.get("departed_locations")
    if isinstance(left_locations, list):
        for loc in left_locations:
            if isinstance(loc, str) and loc not in snapshot["forbidden_locations"]:
                snapshot["forbidden_locations"].append(loc)

    characters = StoryStateProjector.get_all_characters(story_state)
    for name, state in characters.items():
        snapshot["character_states"][name] = state

    completed = story_state.get("completed_events") or story_state.get("events_completed")
    if isinstance(completed, list):
        for evt in completed:
            if isinstance(evt, str) and evt not in snapshot["completed_events"]:
                snapshot["completed_events"].append(evt)

    forbidden_recap = story_state.get("forbidden_recap_events") or story_state.get("no_replay_events")
    if isinstance(forbidden_recap, list):
        for evt in forbidden_recap:
            if isinstance(evt, str) and evt not in snapshot["forbidden_recap_events"]:
                snapshot["forbidden_recap_events"].append(evt)


def _merge_source_of_truth(snapshot: dict, scene_contract: dict, field_sources: dict):
    sot = scene_contract.get("source_of_truth") if isinstance(scene_contract, dict) else None
    if not sot or not isinstance(sot, dict):
        return

    time_anchor = sot.get("time_anchor") or sot.get("narrative_time")
    if time_anchor and isinstance(time_anchor, str):
        snapshot["narrative_time"] = time_anchor
        _record_source(field_sources, "narrative_time", time_anchor, _PRIORITY_SOURCE_OF_TRUTH, "source_of_truth")

    location_anchor = sot.get("location_anchor") or sot.get("current_location")
    if location_anchor and isinstance(location_anchor, str):
        snapshot["current_location"] = location_anchor
        _record_source(field_sources, "current_location", location_anchor, _PRIORITY_SOURCE_OF_TRUTH, "source_of_truth")


def _contract_text_items(value) -> list[str]:
    """Normalize heterogeneous contract fields into stable audit text."""
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, (list, tuple, set)):
        result: list[str] = []
        for item in value:
            result.extend(_contract_text_items(item))
        return result
    if isinstance(value, dict):
        for key in (
            "text",
            "description",
            "content",
            "requirement",
            "event",
            "outcome",
            "goal",
            "state",
            "anchor_text",
            "literal_anchor",
        ):
            if value.get(key):
                return _contract_text_items(value.get(key))
    return []


def _extend_unique(target: list[str], value) -> None:
    for text in _contract_text_items(value):
        if text not in target:
            target.append(text)


def _merge_contract_protection(snapshot: dict, scene_contract: dict, story_state: dict) -> None:
    """Compile protection evidence from the existing scene contract.

    This is intentionally schema-driven and genre-neutral.  Semantic
    obligations are recorded for the formal validator; only explicitly
    declared literal anchors are used for exact preservation checks.
    """
    contract = scene_contract if isinstance(scene_contract, dict) else {}
    source = contract.get("source_of_truth")
    if not isinstance(source, dict):
        source = {}
    enrichment = contract.get("editor_enrichment")
    if not isinstance(enrichment, dict):
        enrichment = {}
    state = story_state if isinstance(story_state, dict) else {}

    snapshot["pov_character"] = str(
        contract.get("pov_character")
        or enrichment.get("pov_lock")
        or source.get("pov_character")
        or state.get("pov_character")
        or ""
    ).strip()

    obligations = snapshot["contract_obligations"]
    for value in (
        source.get("goal"),
        source.get("outline_outcome"),
        source.get("must_show_outline"),
        contract.get("goal"),
        contract.get("scene_goal"),
        contract.get("must_show"),
        contract.get("hard_must_show"),
        contract.get("ending_state"),
        enrichment.get("additional_must_show"),
        enrichment.get("ending_state"),
    ):
        _extend_unique(obligations, value)

    # Only explicit literal anchors are safe for exact string preservation.
    # Natural-language must_show items remain semantic validator obligations.
    anchors = snapshot["contract_required_anchors"]
    _extend_unique(anchors, contract.get("must_show_literal_anchors"))
    _extend_unique(anchors, source.get("literal_anchors"))

    forbidden = snapshot["contract_forbidden_items"]
    for value in (
        source.get("forbidden_outline"),
        contract.get("forbidden"),
        enrichment.get("additional_forbidden"),
    ):
        _extend_unique(forbidden, value)


def _normalize_character_state(state: dict) -> dict:
    return StoryStateProjector.normalize_character_state(state)


def render_truth_snapshot(snapshot: dict) -> str:
    if not snapshot:
        return ""

    lines = []

    if snapshot.get("narrative_time"):
        lines.append(f"当前叙事时间：{snapshot['narrative_time']}")

    if snapshot.get("current_location"):
        lines.append(f"当前所在地点：{snapshot['current_location']}")

    if snapshot.get("allowed_locations"):
        lines.append("允许出现的地点：" + "、".join(snapshot["allowed_locations"]))

    if snapshot.get("forbidden_locations"):
        lines.append("禁止回退的地点（角色已离开，不得重新出现）：")
        for loc in snapshot["forbidden_locations"]:
            lines.append(f"  ❌ {loc}")

    if snapshot.get("character_states"):
        lines.append("角色当前状态：")
        for name, state in snapshot["character_states"].items():
            parts = [f"  **{name}**"]
            if state.get("location"):
                parts.append(f"位置：{state['location']}")
            if state.get("physical"):
                parts.append(f"身体：{'、'.join(state['physical'])}")
            if state.get("emotional"):
                parts.append(f"情绪：{'、'.join(state['emotional'])}")
            if state.get("inventory"):
                parts.append(f"物品：{'、'.join(state['inventory'])}")
            lines.append(" | ".join(parts))

    if snapshot.get("completed_events"):
        lines.append("已完成事件（不得重演）：")
        for evt in snapshot["completed_events"]:
            lines.append(f"  ✓ {evt}")

    if snapshot.get("forbidden_recap_events"):
        lines.append("禁止回放的事件（不得重新描写全过程）：")
        for evt in snapshot["forbidden_recap_events"]:
            lines.append(f"  ❌ {evt}")

    if snapshot.get("previous_scene_ending"):
        lines.append(f"上一场景结尾：{snapshot['previous_scene_ending'][:500]}")

    return "\n".join(lines)


def compute_chapter_scene_budgets(
    book_target_words: int,
    total_chapters: int,
    scene_count: int,
    chapter_function: str = "",
    scene_complexity_scores: list[int] | None = None,
    words_already_written: int = 0,
    current_chapter: int = 0,
) -> list[dict]:
    if total_chapters <= 0:
        total_chapters = 1
    if scene_count <= 0:
        scene_count = 1

    if words_already_written > 0 and current_chapter > 0:
        chapter_target = compute_adjusted_chapter_budget(
            book_target_words, total_chapters, current_chapter, words_already_written,
        )
    else:
        chapter_target = max(2000, book_target_words // total_chapters)

    if scene_complexity_scores is None:
        scene_complexity_scores = [0] * scene_count
    while len(scene_complexity_scores) < scene_count:
        scene_complexity_scores.append(0)

    base_weights = []
    for i in range(scene_count):
        w = 1.0
        score = scene_complexity_scores[i] if i < len(scene_complexity_scores) else 0
        if score >= 4:
            w *= 1.15
        elif score <= 1:
            w *= 0.85
        base_weights.append(w)

    chapter_weight = 1.0
    if chapter_function in ("reversal", "payoff", "climax"):
        chapter_weight = 1.2
    elif chapter_function in ("transition", "recovery", "setup"):
        chapter_weight = 0.85

    weighted_chapter_target = int(chapter_target * chapter_weight)

    total_base_weight = sum(base_weights)
    budgets = []
    allocated = 0
    for i in range(scene_count):
        target_chars = int(weighted_chapter_target * base_weights[i] / total_base_weight)
        soft_min = int(target_chars * 0.75)
        soft_max = int(target_chars * 1.25)
        hard_max = int(target_chars * 1.6)
        hard_min = int(target_chars * 0.5)
        budgets.append({
            "target_chars": target_chars,
            "soft_min_chars": soft_min,
            "soft_max_chars": soft_max,
            "hard_max_chars": hard_max,
            "hard_min_chars": hard_min,
        })
        allocated += target_chars

    remainder = weighted_chapter_target - allocated
    if remainder != 0 and budgets:
        last_idx = len(budgets) - 1
        budgets[last_idx]["target_chars"] += remainder
        budgets[last_idx]["soft_min_chars"] = int(budgets[last_idx]["target_chars"] * 0.75)
        budgets[last_idx]["soft_max_chars"] = int(budgets[last_idx]["target_chars"] * 1.25)
        budgets[last_idx]["hard_max_chars"] = int(budgets[last_idx]["target_chars"] * 1.6)
        budgets[last_idx]["hard_min_chars"] = int(budgets[last_idx]["target_chars"] * 0.5)

    return budgets


def compute_scene_word_budget(
    book_target_words: int,
    total_chapters: int,
    scene_count: int,
    scene_index: int,
    chapter_function: str = "",
    scene_complexity_score: int = 0,
    words_already_written: int = 0,
    current_chapter: int = 0,
) -> dict:
    budgets = compute_chapter_scene_budgets(
        book_target_words, total_chapters, scene_count,
        chapter_function=chapter_function,
        scene_complexity_scores=[scene_complexity_score] * scene_count,
        words_already_written=words_already_written,
        current_chapter=current_chapter,
    )
    idx = max(0, min(scene_index, len(budgets) - 1))
    return budgets[idx]


def chars_to_max_tokens(chars: int) -> int:
    tokens = int(chars * 1.4)
    return max(1024, min(tokens, 8192))


def compute_adjusted_chapter_budget(
    book_target_words: int,
    total_chapters: int,
    current_chapter: int,
    words_already_written: int,
) -> int:
    if total_chapters <= 0:
        total_chapters = 1
    if current_chapter <= 0:
        current_chapter = 1
    remaining_chapters = max(1, total_chapters - current_chapter + 1)
    remaining_budget = max(0, book_target_words - words_already_written)
    return max(2000, remaining_budget // remaining_chapters)
