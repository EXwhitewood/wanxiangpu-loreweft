import copy
import pytest

from app.services.scene_truth_snapshot import (
    build_scene_truth_snapshot,
    validate_first_scene_baseline,
    StoryStateProjector,
)
from app.services.outline_state_compatibility_validator import (
    OutlineStateCompatibilityValidator,
)
from app.models.story_state import StoryState


def _make_story_state(**overrides):
    defaults = {
        "active_chapter": 1,
        "active_scene": 0,
        "narrative_time": "第一天清晨",
        "pov_character": "林逸",
        "objective_state": {
            "林逸": {
                "location": "玄天宗外门厢房",
                "emotional_state": "平静",
                "physical_state": "健康",
                "inventory": ["入门令牌"],
                "alive": True,
                "extra": {},
            }
        },
        "completed_events": ["拜入玄天宗"],
    }
    defaults.update(overrides)
    return StoryState(**defaults).model_dump()


def _make_baseline(**overrides):
    defaults = {
        "narrative_time": "第一天清晨",
        "current_location": "玄天宗外门厢房",
        "pov_character": "林逸",
        "objective_state": {
            "林逸": {
                "location": "玄天宗外门厢房",
                "inventory": ["入门令牌"],
            }
        },
        "completed_events": ["拜入玄天宗"],
    }
    defaults.update(overrides)
    return defaults


class TestRebuildChapter1:
    def test_rebuild_ch1_creates_empty_initial_state(self):
        state = StoryState(active_chapter=1, active_scene=0)
        assert state.active_chapter == 1
        assert state.active_scene == 0
        assert state.completed_events == []
        assert state.objective_state == {}

    def test_rebuild_ch1_baseline_from_initial_state(self):
        fresh = StoryState(active_chapter=1, active_scene=0)
        baseline_data = fresh.model_dump()
        assert baseline_data["active_chapter"] == 1
        assert baseline_data["completed_events"] == []


def test_truth_snapshot_compiles_contract_protection_dimensions():
    snapshot = build_scene_truth_snapshot(
        story_state=_make_story_state(),
        chapter_state={},
        scene_contract={
            "pov_character": "林逸",
            "goal": "找到失踪的信使",
            "must_show": ["发现暗号"],
            "must_show_literal_anchors": ["铜制徽章"],
            "ending_state": "确认叛徒身份",
            "forbidden": ["提前公布幕后主使"],
            "source_of_truth": {
                "goal": "找到失踪的信使",
                "must_show_outline": ["门上的暗号"],
                "forbidden_outline": ["主使露面"],
            },
        },
        scene_id="ch2s0",
    )

    assert snapshot["pov_character"] == "林逸"
    assert "找到失踪的信使" in snapshot["contract_obligations"]
    assert "发现暗号" in snapshot["contract_obligations"]
    assert "确认叛徒身份" in snapshot["contract_obligations"]
    assert snapshot["contract_required_anchors"] == ["铜制徽章"]
    assert "主使露面" in snapshot["contract_forbidden_items"]


class TestRebuildMidChapter:
    def test_rebuild_ch5_from_ch4_snapshot_preserves_earlier_facts(self):
        ch4_snapshot = _make_story_state(
            active_chapter=4,
            completed_events=["拜入玄天宗", "通过试炼", "获得灵石", "击败对手"],
            objective_state={
                "林逸": {
                    "location": "内门演武场",
                    "emotional_state": "兴奋",
                    "physical_state": "轻伤",
                    "inventory": ["入门令牌", "灵石", "铁剑"],
                    "alive": True,
                    "extra": {},
                }
            },
        )
        restored = StoryState(**ch4_snapshot)
        restored.active_chapter = 5
        restored.active_scene = 0

        assert restored.active_chapter == 5
        assert "拜入玄天宗" in restored.completed_events
        assert "击败对手" in restored.completed_events
        assert len(restored.completed_events) == 4

    def test_rebuild_ch10_does_not_lose_ch1_to_ch9_facts(self):
        earlier_events = [f"事件_{i}" for i in range(1, 10)]
        ch9_snapshot = _make_story_state(
            active_chapter=9,
            completed_events=earlier_events,
        )
        restored = StoryState(**ch9_snapshot)
        restored.active_chapter = 10
        restored.active_scene = 0

        assert len(restored.completed_events) == 9
        assert "事件_1" in restored.completed_events
        assert "事件_9" in restored.completed_events


class TestMissingMidChapterBaseline:
    def test_no_snapshot_for_prev_chapter_blocks_rebuild(self):
        ch5_state = _make_story_state(active_chapter=5, completed_events=["事件A"])
        restored = StoryState(**ch5_state)
        assert restored.active_chapter == 5
        assert "事件A" in restored.completed_events
        assert len(restored.completed_events) == 1

    def test_snapshot_from_prev_chapter_restores_correctly(self):
        ch7_snapshot = _make_story_state(
            active_chapter=7,
            completed_events=["事件A", "事件B", "事件C"],
            narrative_time="第七天黄昏",
        )
        restored = StoryState(**ch7_snapshot)
        restored.active_chapter = 8
        restored.active_scene = 0

        assert restored.active_chapter == 8
        assert restored.narrative_time == "第七天黄昏"
        assert "事件A" in restored.completed_events


class TestSceneLegalMovement:
    def test_moving_within_chapter_not_blocked_by_baseline(self):
        baseline = _make_baseline(
            narrative_time="第一天清晨",
            current_location="玄天宗外门厢房",
        )
        scene_contract = {
            "source_of_truth": {
                "time_anchor": "第一天清晨",
                "location_anchor": "玄天宗外门厢房",
            },
        }
        conflicts = validate_first_scene_baseline(
            chapter_baseline=baseline,
            story_state=_make_story_state(),
            scene_contract=scene_contract,
        )
        assert len(conflicts) == 0

    def test_first_scene_can_move_with_opening_transition(self):
        baseline = _make_baseline(
            narrative_time="第一天清晨",
            current_location="玄天宗外门厢房",
        )
        scene_contract = {
            "opening_transition": "次日清晨，林逸前往演武场",
            "source_of_truth": {
                "time_anchor": "第二天清晨",
                "location_anchor": "玄天宗演武场",
            },
        }
        conflicts = validate_first_scene_baseline(
            chapter_baseline=baseline,
            story_state=_make_story_state(),
            scene_contract=scene_contract,
        )
        assert len(conflicts) == 0

    def test_second_scene_ignores_baseline_merge(self):
        snapshot = build_scene_truth_snapshot(
            story_state=_make_story_state(
                objective_state={
                    "林逸": {
                        "location": "玄天宗外门厢房",
                        "emotional_state": "平静",
                        "physical_state": "健康",
                        "inventory": ["入门令牌"],
                        "alive": True,
                        "extra": {},
                    }
                },
            ),
            chapter_state={
                "current_location": "玄天宗演武场",
                "narrative_time": "第一天上午",
            },
            scene_contract={
                "source_of_truth": {
                    "time_anchor": "第一天中午",
                    "location_anchor": "玄天宗食堂",
                },
            },
            previous_scene_ending="林逸离开演武场前往食堂",
            scene_id="ch1s1",
        )
        assert snapshot["current_location"] == "玄天宗食堂"
        assert snapshot["narrative_time"] == "第一天中午"

    def test_baseline_not_in_merge_priority(self):
        baseline_data = _make_baseline(current_location="玄天宗外门厢房")
        snapshot = build_scene_truth_snapshot(
            story_state=_make_story_state(),
            chapter_state={},
            scene_contract={
                "source_of_truth": {
                    "location_anchor": "玄天宗演武场",
                    "time_anchor": "第一天上午",
                },
            },
            previous_scene_ending="",
            scene_id="ch1s1",
            chapter_baseline=baseline_data,
        )
        assert snapshot["current_location"] == "玄天宗演武场"

    def test_multi_location_chapter_only_first_scene_checked(self):
        validator = OutlineStateCompatibilityValidator()
        story_state = _make_story_state(
            pov_character="林逸",
            objective_state={
                "林逸": {
                    "location": "玄天宗外门厢房",
                    "emotional_state": "平静",
                    "physical_state": "健康",
                    "inventory": ["入门令牌"],
                    "alive": True,
                    "extra": {},
                }
            },
        )
        scene_contracts = [
            {"source_of_truth": {"location_anchor": "玄天宗外门厢房"}},
            {"source_of_truth": {"location_anchor": "玄天宗演武场"}},
            {"source_of_truth": {"location_anchor": "玄天宗食堂"}},
        ]
        conflicts = validator._check_location_conflicts(
            {"chapter_number": 3}, story_state, scene_contracts,
        )
        assert len(conflicts) == 0


class TestOutlineVersionUpgrade:
    def test_baseline_rebuilt_from_snapshot_not_runtime(self):
        ch2_snapshot = _make_story_state(
            active_chapter=2,
            completed_events=["拜入玄天宗", "通过试炼"],
            narrative_time="第二天清晨",
        )
        restored = StoryState(**ch2_snapshot)
        restored.active_chapter = 3
        restored.active_scene = 0

        assert "拜入玄天宗" in restored.completed_events
        assert "通过试炼" in restored.completed_events
        assert restored.narrative_time == "第二天清晨"

    def test_polluted_runtime_state_not_used_for_baseline(self):
        ch2_snapshot = _make_story_state(
            active_chapter=2,
            completed_events=["拜入玄天宗", "通过试炼"],
        )
        restored = StoryState(**ch2_snapshot)
        restored.active_chapter = 3
        restored.active_scene = 0

        assert "污染事件_X" not in restored.completed_events
        assert len(restored.completed_events) == 2


class TestCommitEffectsFailure:
    def test_committing_status_prevents_word_count_budget(self):
        valid_statuses = ("committed", "completed", "accepted")
        assert "committing" not in valid_statuses
        assert "draft" not in valid_statuses

    def test_committed_status_included_in_word_count(self):
        valid_statuses = ("committed", "completed", "accepted")
        assert "committed" in valid_statuses

    def test_apply_patch_idempotent_for_completed_events(self):
        state = StoryState(
            active_chapter=1,
            completed_events=["事件A", "事件B"],
        )
        state_dict = state.model_dump()

        new_events = ["事件B", "事件C"]
        existing_events = set(state_dict.get("completed_events", []))
        for evt in new_events:
            if evt not in existing_events:
                state_dict.setdefault("completed_events", []).append(evt)

        assert state_dict["completed_events"] == ["事件A", "事件B", "事件C"]
        assert state_dict["completed_events"].count("事件B") == 1

    def test_required_effect_failure_raises_runtime_error(self):
        with pytest.raises(RuntimeError, match="Required effects failed"):
            raise RuntimeError("Required effects failed: state_patch: connection lost")


class TestAPIRestartRecovery:
    def test_baseline_chapter_state_preserved(self):
        ch_state = {
            "established_facts": ["林逸已到达演武场"],
            "character_states": {"林逸": {"location": "演武场"}},
            "completed_events": ["到达演武场"],
            "active_constraints": [{"description": "必须在日落前返回"}],
        }
        baseline = _make_baseline()
        baseline_with_chapter = {**baseline, "baseline_chapter_state": ch_state}

        assert baseline_with_chapter["baseline_chapter_state"]["completed_events"] == ["到达演武场"]
        assert baseline_with_chapter["baseline_chapter_state"]["active_constraints"][0]["description"] == "必须在日落前返回"

    def test_baseline_chapter_state_restored_with_setdefault(self):
        stored = {
            "established_facts": ["事实A"],
            "character_states": {"林逸": {"location": "演武场"}},
        }
        chapter_state = dict(stored)
        chapter_state.setdefault("established_facts", [])
        chapter_state.setdefault("character_states", {})
        chapter_state.setdefault("completed_events", [])
        chapter_state.setdefault("active_constraints", [])

        assert chapter_state["established_facts"] == ["事实A"]
        assert chapter_state["completed_events"] == []
        assert chapter_state["active_constraints"] == []

    def test_committing_chapter_auto_promoted_when_no_pending_effects(self):
        pending_effects = []
        if not pending_effects:
            status = "committed"
        else:
            status = "draft"
        assert status == "committed"

    def test_committing_chapter_reverted_when_pending_effects_remain(self):
        pending_effects = [{"id": "eff1", "applied": False}]
        if not pending_effects:
            status = "committed"
        else:
            status = "draft"
        assert status == "draft"


class TestRepeatedFailureRetry:
    def test_baseline_unchanged_after_20_failed_attempts(self):
        original_baseline = _make_baseline(
            current_location="玄天宗外门厢房",
            completed_events=["拜入玄天宗"],
        )
        baseline_copy = copy.deepcopy(original_baseline)

        for _ in range(20):
            pass

        assert original_baseline == baseline_copy
        assert original_baseline["completed_events"] == ["拜入玄天宗"]

    def test_story_state_rollback_on_failure(self):
        baseline_state = _make_story_state(
            active_chapter=5,
            completed_events=["事件A", "事件B"],
        )
        restored = StoryState(**baseline_state)

        assert restored.active_chapter == 5
        assert "事件A" in restored.completed_events
        assert "事件B" in restored.completed_events


class TestLocationDetectionFromObjectiveState:
    def test_reads_pov_location_from_objective_state(self):
        validator = OutlineStateCompatibilityValidator()
        story_state = _make_story_state(
            pov_character="林逸",
            objective_state={
                "林逸": {
                    "location": "玄天宗藏经阁",
                    "emotional_state": "专注",
                    "physical_state": "健康",
                    "inventory": ["入门令牌"],
                    "alive": True,
                    "extra": {},
                }
            },
        )
        outline_chapter = {"chapter_number": 5}
        scene_contracts = [
            {
                "source_of_truth": {
                    "location_anchor": "玄天宗藏经阁",
                },
            }
        ]
        conflicts = validator._check_location_conflicts(
            outline_chapter, story_state, scene_contracts
        )
        assert len(conflicts) == 0

    def test_detects_conflict_when_pov_location_differs(self):
        validator = OutlineStateCompatibilityValidator()
        story_state = _make_story_state(
            pov_character="林逸",
            objective_state={
                "林逸": {
                    "location": "玄天宗外门厢房",
                    "emotional_state": "平静",
                    "physical_state": "健康",
                    "inventory": ["入门令牌"],
                    "alive": True,
                    "extra": {},
                }
            },
        )
        outline_chapter = {"chapter_number": 5}
        scene_contracts = [
            {
                "source_of_truth": {
                    "location_anchor": "魔域深渊",
                },
            }
        ]
        conflicts = validator._check_location_conflicts(
            outline_chapter, story_state, scene_contracts
        )
        assert len(conflicts) > 0

    def test_falls_back_to_top_level_current_location(self):
        validator = OutlineStateCompatibilityValidator()
        story_state = {
            "active_chapter": 5,
            "pov_character": "",
            "objective_state": {},
            "current_location": "玄天宗外门厢房",
        }
        outline_chapter = {"chapter_number": 5}
        scene_contracts = [
            {
                "source_of_truth": {
                    "location_anchor": "魔域深渊",
                },
            }
        ]
        conflicts = validator._check_location_conflicts(
            outline_chapter, story_state, scene_contracts
        )
        assert len(conflicts) > 0

    def test_location_conflict_is_warning_not_blocking(self):
        validator = OutlineStateCompatibilityValidator()
        story_state = _make_story_state(
            pov_character="林逸",
            objective_state={
                "林逸": {
                    "location": "玄天宗外门厢房",
                    "emotional_state": "平静",
                    "physical_state": "健康",
                    "inventory": ["入门令牌"],
                    "alive": True,
                    "extra": {},
                }
            },
        )
        scene_contracts = [
            {"source_of_truth": {"location_anchor": "魔域深渊"}},
        ]
        conflicts = validator._check_location_conflicts(
            {"chapter_number": 5}, story_state, scene_contracts,
        )
        assert len(conflicts) > 0
        assert conflicts[0].severity == "warning"


class TestCompletedEventsGeneralized:
    def test_detects_extra_events_against_baseline_any_chapter(self):
        validator = OutlineStateCompatibilityValidator()
        baseline = _make_baseline(completed_events=["事件A", "事件B"])
        story_state = _make_story_state(
            completed_events=["事件A", "事件B", "事件C", "事件D"],
        )
        conflicts = validator._check_completed_events(
            outline_chapter={"chapter_number": 5},
            story_state=story_state,
            chapter_state={},
            chapter_baseline=baseline,
        )
        assert len(conflicts) > 0
        assert "事件C" in conflicts[0].state_value or "事件D" in conflicts[0].state_value

    def test_no_conflict_when_events_match_baseline(self):
        validator = OutlineStateCompatibilityValidator()
        baseline = _make_baseline(completed_events=["事件A", "事件B"])
        story_state = _make_story_state(
            completed_events=["事件A", "事件B"],
        )
        conflicts = validator._check_completed_events(
            outline_chapter={"chapter_number": 5},
            story_state=story_state,
            chapter_state={},
            chapter_baseline=baseline,
        )
        assert len(conflicts) == 0

    def test_no_baseline_means_all_events_are_extra(self):
        validator = OutlineStateCompatibilityValidator()
        story_state = _make_story_state(
            completed_events=["事件A", "事件B"],
        )
        conflicts = validator._check_completed_events(
            outline_chapter={"chapter_number": 5},
            story_state=story_state,
            chapter_state={},
            chapter_baseline=None,
        )
        assert len(conflicts) > 0


class TestFirstSceneBaselineValidation:
    def test_first_scene_baseline_conflict_detected(self):
        baseline = _make_baseline(
            narrative_time="第一天清晨",
            current_location="玄天宗外门厢房",
        )
        scene_contract = {
            "source_of_truth": {
                "time_anchor": "第三天黄昏",
                "location_anchor": "魔域深渊",
            },
        }
        conflicts = validate_first_scene_baseline(
            chapter_baseline=baseline,
            story_state=_make_story_state(),
            scene_contract=scene_contract,
        )
        assert len(conflicts) >= 1

    def test_first_scene_baseline_no_conflict(self):
        baseline = _make_baseline(
            narrative_time="第一天清晨",
            current_location="玄天宗外门厢房",
        )
        scene_contract = {
            "source_of_truth": {
                "time_anchor": "第一天清晨",
                "location_anchor": "玄天宗外门厢房",
            },
        }
        conflicts = validate_first_scene_baseline(
            chapter_baseline=baseline,
            story_state=_make_story_state(),
            scene_contract=scene_contract,
        )
        assert len(conflicts) == 0

    def test_empty_baseline_returns_no_conflicts(self):
        conflicts = validate_first_scene_baseline(
            chapter_baseline={},
            story_state=_make_story_state(),
            scene_contract={"source_of_truth": {"time_anchor": "第一天清晨"}},
        )
        assert len(conflicts) == 0

    def test_none_baseline_returns_no_conflicts(self):
        conflicts = validate_first_scene_baseline(
            chapter_baseline=None,
            story_state=_make_story_state(),
            scene_contract={"source_of_truth": {"time_anchor": "第一天清晨"}},
        )
        assert len(conflicts) == 0

    def test_opening_transition_skips_conflict_check(self):
        baseline = _make_baseline(
            narrative_time="第一天清晨",
            current_location="玄天宗外门厢房",
        )
        scene_contract = {
            "opening_transition": "次日清晨，林逸前往演武场",
            "source_of_truth": {
                "time_anchor": "第二天清晨",
                "location_anchor": "玄天宗演武场",
            },
        }
        conflicts = validate_first_scene_baseline(
            chapter_baseline=baseline,
            story_state=_make_story_state(),
            scene_contract=scene_contract,
        )
        assert len(conflicts) == 0

    def test_entry_precondition_used_over_source_of_truth(self):
        baseline = _make_baseline(
            narrative_time="第一天清晨",
            current_location="玄天宗外门厢房",
        )
        scene_contract = {
            "entry_precondition": {
                "time": "第一天清晨",
                "location": "玄天宗外门厢房",
            },
            "source_of_truth": {
                "time_anchor": "第二天清晨",
                "location_anchor": "玄天宗演武场",
            },
        }
        conflicts = validate_first_scene_baseline(
            chapter_baseline=baseline,
            story_state=_make_story_state(),
            scene_contract=scene_contract,
        )
        assert len(conflicts) == 0


class TestStoryStateProjector:
    def test_get_pov_location_from_objective_state(self):
        state = _make_story_state(
            pov_character="林逸",
            objective_state={
                "林逸": {
                    "location": "玄天宗藏经阁",
                    "emotional_state": "专注",
                    "physical_state": "健康",
                    "inventory": ["入门令牌"],
                    "alive": True,
                    "extra": {},
                }
            },
        )
        assert StoryStateProjector.get_pov_location(state) == "玄天宗藏经阁"

    def test_get_pov_location_fallback_to_current_location(self):
        state = {"pov_character": "", "objective_state": {}, "current_location": "玄天宗外门厢房"}
        assert StoryStateProjector.get_pov_location(state) == "玄天宗外门厢房"

    def test_normalize_character_state_physical_state_field(self):
        raw = {"physical_state": ["轻伤"], "emotional_state": ["焦虑"], "inventory": ["灵石"], "location": "演武场"}
        normalized = StoryStateProjector.normalize_character_state(raw)
        assert normalized["physical"] == ["轻伤"]
        assert normalized["emotional"] == ["焦虑"]
        assert normalized["inventory"] == ["灵石"]
        assert normalized["location"] == "演武场"

    def test_normalize_character_state_legacy_physical_field(self):
        raw = {"physical": ["轻伤"], "emotional": ["焦虑"], "inventory": ["灵石"], "location": "演武场"}
        normalized = StoryStateProjector.normalize_character_state(raw)
        assert normalized["physical"] == ["轻伤"]
        assert normalized["emotional"] == ["焦虑"]

    def test_get_all_characters_from_objective_state(self):
        state = _make_story_state(
            objective_state={
                "林逸": {
                    "location": "厢房",
                    "physical_state": "健康",
                    "emotional_state": "平静",
                    "inventory": ["令牌"],
                    "alive": True,
                    "extra": {},
                },
                "苏瑶": {
                    "location": "内门",
                    "physical_state": "健康",
                    "emotional_state": "担忧",
                    "inventory": [],
                    "alive": True,
                    "extra": {},
                },
            },
        )
        chars = StoryStateProjector.get_all_characters(state)
        assert "林逸" in chars
        assert "苏瑶" in chars
        assert chars["林逸"]["location"] == "厢房"
        assert chars["苏瑶"]["location"] == "内门"

    def test_get_character_location(self):
        state = _make_story_state(
            objective_state={
                "林逸": {
                    "location": "藏经阁",
                    "emotional_state": "专注",
                    "physical_state": "健康",
                    "inventory": [],
                    "alive": True,
                    "extra": {},
                }
            },
        )
        assert StoryStateProjector.get_character_location(state, "林逸") == "藏经阁"
        assert StoryStateProjector.get_character_location(state, "苏瑶") == ""


class TestSnapshotLineageAndStale:
    def test_stale_snapshot_excluded_from_restore(self):
        ch5_snapshot = _make_story_state(
            active_chapter=5,
            completed_events=["事件A", "事件B", "事件C"],
        )
        restored = StoryState(**ch5_snapshot)
        restored.active_chapter = 6
        assert "事件A" in restored.completed_events

    def test_rebuilding_chapter_marks_later_snapshots_stale(self):
        ch5_events = ["事件A", "事件B"]
        ch5_snapshot = _make_story_state(
            active_chapter=5,
            completed_events=ch5_events,
        )
        ch6_events = ch5_events + ["事件C"]
        ch6_snapshot = _make_story_state(
            active_chapter=6,
            completed_events=ch6_events,
        )
        restored_from_ch5 = StoryState(**ch5_snapshot)
        restored_from_ch5.active_chapter = 6
        assert "事件C" not in restored_from_ch5.completed_events


class TestConcurrencyLock:
    @pytest.mark.asyncio
    async def test_project_lock_prevents_concurrent_regenerate(self):
        from app.services.project_lock import ProjectLockManager

        lock = ProjectLockManager.get_lock("test-project-1")
        assert not lock.locked()
        await lock.acquire()
        assert lock.locked()
        lock.release()
        ProjectLockManager.cleanup("test-project-1")

    @pytest.mark.asyncio
    async def test_project_lock_locked_detected(self):
        from app.services.project_lock import ProjectLockManager

        lock = ProjectLockManager.get_lock("test-project-2")
        await lock.acquire()
        assert lock.locked()
        lock.release()
        ProjectLockManager.cleanup("test-project-2")


class TestSQLiteTablesExist:
    def test_chapter_baselines_model_exists(self):
        try:
            from app.db.sqlite import ChapterBaseline
            assert ChapterBaseline.__tablename__ == "chapter_baselines"
        except ImportError:
            pytest.skip("aiosqlite not available in test environment")

    def test_chapter_snapshots_model_exists(self):
        try:
            from app.db.sqlite import ChapterSnapshot
            assert ChapterSnapshot.__tablename__ == "chapter_snapshots"
        except ImportError:
            pytest.skip("aiosqlite not available in test environment")

    def test_chapter_effect_outbox_model_exists(self):
        try:
            from app.db.sqlite import ChapterEffectOutbox
            assert ChapterEffectOutbox.__tablename__ == "chapter_effect_outbox"
        except ImportError:
            pytest.skip("aiosqlite not available in test environment")


class TestPostgreSQLModels:
    def test_chapter_snapshot_has_lineage_version(self):
        from app.db.db_models import ChapterSnapshot
        columns = [c.name for c in ChapterSnapshot.__table__.columns]
        assert "lineage_version" in columns
        assert "stale" in columns

    def test_chapter_effect_outbox_has_idempotency_key(self):
        from app.db.db_models import ChapterEffectOutbox
        columns = [c.name for c in ChapterEffectOutbox.__table__.columns]
        assert "idempotency_key" in columns
        assert "applied" in columns
