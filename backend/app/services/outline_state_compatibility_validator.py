import logging

_logger = logging.getLogger(__name__)


class OutlineStateConflict:
    def __init__(self, field: str, outline_value: str, state_value: str, severity: str = "blocking"):
        self.field = field
        self.outline_value = outline_value
        self.state_value = state_value
        self.severity = severity

    def to_dict(self) -> dict:
        return {
            "field": self.field,
            "outline_value": self.outline_value,
            "state_value": self.state_value,
            "severity": self.severity,
        }


class OutlineStateCompatibilityValidator:
    def validate(
        self,
        outline_chapter: dict,
        story_state: dict,
        chapter_state: dict,
        scene_contracts: list[dict],
        chapter_baseline: dict | None = None,
    ) -> list[OutlineStateConflict]:
        conflicts = []

        conflicts.extend(self._check_chapter_progression(outline_chapter, story_state))
        conflicts.extend(self._check_location_conflicts(outline_chapter, story_state, scene_contracts))
        conflicts.extend(self._check_time_conflicts(outline_chapter, story_state, scene_contracts))
        conflicts.extend(self._check_completed_events(outline_chapter, story_state, chapter_state, chapter_baseline))
        conflicts.extend(self._check_character_states(outline_chapter, story_state, scene_contracts, chapter_baseline))

        return conflicts

    def _check_chapter_progression(
        self, outline_chapter: dict, story_state: dict
    ) -> list[OutlineStateConflict]:
        conflicts = []
        outline_ch = outline_chapter.get("chapter_number", 0)
        state_ch = story_state.get("active_chapter", 1)
        state_scene = story_state.get("active_scene", 1)

        if outline_ch > 0 and state_ch > outline_ch:
            conflicts.append(OutlineStateConflict(
                field="active_chapter",
                outline_value=f"第{outline_ch}章",
                state_value=f"第{state_ch}章第{state_scene}场景",
                severity="blocking",
            ))
        elif outline_ch > 0 and state_ch == outline_ch and state_scene > 1:
            conflicts.append(OutlineStateConflict(
                field="active_scene",
                outline_value=f"第{outline_ch}章从第1场景开始",
                state_value=f"第{state_ch}章第{state_scene}场景",
                severity="blocking",
            ))
        return conflicts

    def _check_location_conflicts(
        self, outline_chapter: dict, story_state: dict, scene_contracts: list[dict]
    ) -> list[OutlineStateConflict]:
        conflicts = []
        first_contract = scene_contracts[0] if scene_contracts else {}
        outline_location = ""
        entry_precondition = first_contract.get("entry_precondition", {})
        if isinstance(entry_precondition, dict):
            outline_location = entry_precondition.get("location", "")
        if not outline_location:
            sot = first_contract.get("source_of_truth", {})
            if isinstance(sot, dict):
                outline_location = sot.get("location_anchor") or sot.get("current_location", "")
        if not outline_location:
            return conflicts

        pov = story_state.get("pov_character", "")
        obj_state = story_state.get("objective_state", {})
        state_location = ""
        if pov and isinstance(obj_state, dict):
            pov_data = obj_state.get(pov, {})
            if isinstance(pov_data, dict):
                state_location = pov_data.get("location", "")
        if not state_location:
            state_location = story_state.get("current_location", "")

        state_left = story_state.get("left_locations", [])

        if not state_location and not state_left:
            return conflicts

        for left_loc in state_left:
            if self._locations_conflict(outline_location, left_loc):
                conflicts.append(OutlineStateConflict(
                    field="current_location",
                    outline_value=outline_location,
                    state_value=f"已离开{left_loc}",
                    severity="blocking",
                ))

        if state_location and not self._locations_compatible(outline_location, state_location):
            opening_transition = first_contract.get("opening_transition", "")
            if not opening_transition:
                conflicts.append(OutlineStateConflict(
                    field="current_location",
                    outline_value=outline_location,
                    state_value=state_location,
                    severity="warning",
                ))
        return conflicts

    def _check_time_conflicts(
        self, outline_chapter: dict, story_state: dict, scene_contracts: list[dict]
    ) -> list[OutlineStateConflict]:
        conflicts = []
        first_contract = scene_contracts[0] if scene_contracts else {}
        outline_time = ""
        entry_precondition = first_contract.get("entry_precondition", {})
        if isinstance(entry_precondition, dict):
            outline_time = entry_precondition.get("time", "")
        if not outline_time:
            outline_time = self._extract_first_scene_time(first_contract)
        state_time = story_state.get("narrative_time", "")

        if not outline_time or not state_time:
            return conflicts

        opening_transition = first_contract.get("opening_transition", "")
        if opening_transition:
            return conflicts

        if self._time_advances_state(outline_time, state_time):
            conflicts.append(OutlineStateConflict(
                field="narrative_time",
                outline_value=outline_time,
                state_value=state_time,
                severity="warning",
            ))
        return conflicts

    def _extract_first_scene_time(self, contract: dict) -> str:
        sot = contract.get("source_of_truth", {})
        if isinstance(sot, dict):
            t = sot.get("time_anchor") or sot.get("narrative_time", "")
            if t:
                return t
        ee = contract.get("editor_enrichment", {})
        if isinstance(ee, dict):
            t = ee.get("temporal_anchor") or ee.get("narrative_time", "")
            if t:
                return t
        return ""

    def _check_completed_events(
        self, outline_chapter: dict, story_state: dict, chapter_state: dict,
        chapter_baseline: dict | None,
    ) -> list[OutlineStateConflict]:
        conflicts = []
        outline_ch = outline_chapter.get("chapter_number", 0)
        if outline_ch <= 0:
            return conflicts

        baseline_events = set()
        if chapter_baseline and isinstance(chapter_baseline, dict):
            baseline_events = set(chapter_baseline.get("completed_events", []))

        state_events = set(story_state.get("completed_events", []))
        chapter_events = set(chapter_state.get("completed_events", []))
        all_current_events = state_events | chapter_events

        extra_events = all_current_events - baseline_events
        if not extra_events:
            return conflicts

        sample = sorted(extra_events)[:5]
        conflicts.append(OutlineStateConflict(
            field="completed_events",
            outline_value=f"基线包含{len(baseline_events)}个事件",
            state_value=f"当前多出{len(extra_events)}个事件：{'、'.join(sample)}{'...' if len(extra_events) > 5 else ''}",
            severity="blocking",
        ))
        return conflicts

    def _check_character_states(
        self, outline_chapter: dict, story_state: dict, scene_contracts: list[dict],
        chapter_baseline: dict | None,
    ) -> list[OutlineStateConflict]:
        conflicts = []
        outline_ch = outline_chapter.get("chapter_number", 0)
        if outline_ch <= 0:
            return conflicts

        baseline_obj = {}
        if chapter_baseline and isinstance(chapter_baseline, dict):
            baseline_obj = chapter_baseline.get("objective_state", {})

        state_chars = story_state.get("characters") or story_state.get("objective_state", {})
        if not isinstance(state_chars, dict):
            return conflicts

        chars_with_extra = []
        for name, state in state_chars.items():
            if not isinstance(state, dict):
                continue
            current_inv = set(state.get("inventory", []))
            baseline_char = baseline_obj.get(name, {})
            baseline_inv = set(baseline_char.get("inventory", [])) if isinstance(baseline_char, dict) else set()
            extra = current_inv - baseline_inv
            if extra:
                chars_with_extra.append(f"{name}新增{len(extra)}件物品")

        if chars_with_extra:
            conflicts.append(OutlineStateConflict(
                field="character_states",
                outline_value="基线状态",
                state_value="；".join(chars_with_extra),
                severity="warning",
            ))
        return conflicts

    def _extract_outline_locations(self, outline_chapter: dict, scene_contracts: list[dict]) -> list[str]:
        locations = []
        for contract in scene_contracts:
            sot = contract.get("source_of_truth", {})
            if isinstance(sot, dict):
                loc = sot.get("location_anchor") or sot.get("current_location", "")
                if loc and loc not in locations:
                    locations.append(loc)
            ee = contract.get("editor_enrichment", {})
            if isinstance(ee, dict):
                loc = ee.get("spatial_anchor") or ee.get("current_location", "")
                if loc and loc not in locations:
                    locations.append(loc)
        return locations

    def _extract_outline_time(self, outline_chapter: dict, scene_contracts: list[dict]) -> str:
        for contract in scene_contracts:
            sot = contract.get("source_of_truth", {})
            if isinstance(sot, dict):
                t = sot.get("time_anchor") or sot.get("narrative_time", "")
                if t:
                    return t
            ee = contract.get("editor_enrichment", {})
            if isinstance(ee, dict):
                t = ee.get("temporal_anchor") or ee.get("narrative_time", "")
                if t:
                    return t
        return ""

    def _locations_conflict(self, loc_a: str, loc_b: str) -> bool:
        return loc_a.strip() == loc_b.strip()

    def _locations_compatible(self, loc_a: str, loc_b: str) -> bool:
        a = loc_a.strip()
        b = loc_b.strip()
        if a == b:
            return True
        if a in b or b in a:
            return True
        return False

    def _time_advances_state(self, outline_time: str, state_time: str) -> bool:
        if not outline_time or not state_time:
            return False
        ot = outline_time.strip()
        st = state_time.strip()
        if ot == st:
            return False
        if "开始" in ot or "初" in ot or "第一天" in ot:
            if st != ot:
                return True
        return False
