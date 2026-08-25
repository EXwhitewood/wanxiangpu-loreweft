from app.services.story_plan_completeness_service import ensure_complete_scene_briefs
from app.services.story_plan_service import StoryPlanService


def test_scene_completion_fills_late_chapters_without_overwriting_existing_scenes():
    outline = {
        "chapter_spine": [
            {
                "chapter_number": 1,
                "title": "开端",
                "conflict_text": "主角遭遇第一次阻力",
                "hook": "新的线索出现",
            },
            {
                "chapter_number": 8,
                "title": "转折",
                "core_conflict": {
                    "desire": "追查真相",
                    "obstacle": "关键证人失踪",
                    "action": "主角转向调查旧档案",
                    "turn": "旧档案指向盟友",
                },
                "value_shift": {"from": "信任", "to": "怀疑"},
                "hook": "盟友隐瞒了关键事实",
            },
        ],
        "scene_briefs": {
            "1": {
                "chapter_id": "ch_001",
                "source": "generated",
                "scenes": [
                    {
                        "scene_id": "authored_scene",
                        "goal": "保留模型写好的场景",
                    }
                ],
            }
        },
    }

    completed, stats = ensure_complete_scene_briefs(outline)

    assert set(completed["scene_briefs"]) == {"ch_001", "ch_008"}
    assert completed["scene_briefs"]["ch_001"]["scenes"][0]["scene_id"] == "authored_scene"
    assert len(completed["scene_briefs"]["ch_001"]["scenes"]) == 2
    assert len(completed["scene_briefs"]["ch_008"]["scenes"]) == 2
    assert completed["chapter_spine"][1]["scenes"][0]["goal"] == "追查真相"
    assert stats["completed_chapters"] == [1, 8]
    assert stats["added_scenes"] == 3


def test_scene_completion_leaves_complete_brief_unchanged():
    scenes = [
        {"scene_id": "ch_002_s1", "goal": "进入现场"},
        {"scene_id": "ch_002_s2", "goal": "发现证据"},
    ]
    outline = {
        "chapter_spine": [{"chapter_number": 2, "chapter_id": "ch_002"}],
        "scene_briefs": {"ch_002": {"chapter_id": "ch_002", "scenes": scenes}},
    }

    completed, stats = ensure_complete_scene_briefs(outline)

    completed_scenes = completed["scene_briefs"]["ch_002"]["scenes"]
    assert [scene["scene_id"] for scene in completed_scenes] == ["ch_002_s1", "ch_002_s2"]
    assert [scene["goal"] for scene in completed_scenes] == ["进入现场", "发现证据"]
    assert stats["completed_chapters"] == []
    assert stats["added_scenes"] == 0


def test_scene_completion_annotates_existing_brief_with_budget_metadata():
    outline = {
        "chapter_spine": [{
            "chapter_number": 3,
            "function": "reversal",
            "core_conflict": {
                "desire": "追查真相",
                "obstacle": "关键证人失踪",
                "action": "调查旧档案",
                "turn": "盟友暴露",
            },
            "value_shift": {"from": "信任", "to": "怀疑"},
            "thread_ops": [{}, {}],
        }],
        "scene_briefs": {
            "ch_003": {"scenes": [{}, {}]},
        },
    }

    completed, stats = ensure_complete_scene_briefs(outline)

    brief = completed["scene_briefs"]["ch_003"]
    assert brief["minimum_scene_count"] == 2
    assert brief["recommended_scene_count"] == 4
    assert brief["scene_budget_signature"].startswith("sha256:")
    assert stats["under_recommended_chapters"] == [{
        "chapter_number": 3,
        "actual": 2,
        "recommended": 4,
    }]


def test_invalid_serialized_scene_is_marked_instead_of_promoted_to_writable_scene():
    outline = {
        "chapter_spine": [{"chapter_number": 1, "title": "chapter"}],
        "scene_briefs": {
            "ch_001": {"scenes": ["@{scene_id=ch_001_s1; goal=broken}"]},
        },
    }

    completed, _ = ensure_complete_scene_briefs(outline)

    invalid_scene = completed["scene_briefs"]["ch_001"]["scenes"][0]
    assert invalid_scene["_invalid_scene_payload"].startswith("@{")
    assert "goal" not in invalid_scene


def test_chapter_blueprint_rejects_conflicting_structured_state_transition():
    service = StoryPlanService()
    chapter = {
        "state_effects_expected": [{
            "entity_id": "character_zhang",
            "field": "life_status",
            "after": "dead",
        }],
    }
    scenes = [{
        "scene_id": "ch_012_s3",
        "goal": "阻止追兵",
        "conflict": "必须付出代价",
        "state_effects": [{
            "entity_id": "character_zhang",
            "field": "life_status",
            "before": "alive",
            "after": "alive",
        }],
    }]

    diagnostics = service.validate_chapter_blueprint(chapter, scenes)

    assert any(item["code"] == "chapter_scene_state_conflict" for item in diagnostics)
    assert all(item["blocks_generation"] for item in diagnostics)


def test_chapter_blueprint_accepts_continuous_structured_state_transition():
    service = StoryPlanService()
    chapter = {
        "state_effects_expected": [{
            "entity_id": "character_zhang",
            "field": "life_status",
            "after": "dead",
        }],
    }
    scenes = [
        {
            "scene_id": "ch_012_s2",
            "goal": "拖住敌人",
            "conflict": "援军未到",
            "state_effects": [{
                "entity_id": "character_zhang",
                "field": "life_status",
                "before": "alive",
                "after": "injured",
            }],
        },
        {
            "scene_id": "ch_012_s3",
            "goal": "完成掩护",
            "conflict": "退路被封",
            "state_effects": [{
                "entity_id": "character_zhang",
                "field": "life_status",
                "before": "injured",
                "after": "dead",
            }],
        },
    ]

    diagnostics = service.validate_chapter_blueprint(chapter, scenes)

    assert diagnostics == []
