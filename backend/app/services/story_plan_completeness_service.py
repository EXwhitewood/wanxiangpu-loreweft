import copy

from app.services.scene_budget_service import SceneBudgetService


def ensure_complete_scene_briefs(outline: dict) -> tuple[dict, dict]:
    """Guarantee the minimum scene count and annotate dynamic recommendations."""
    normalized = copy.deepcopy(outline or {})
    spine = normalized.get("chapter_spine")
    if not isinstance(spine, list):
        return normalized, _completion_stats()

    raw_briefs = normalized.get("scene_briefs")
    raw_briefs = raw_briefs if isinstance(raw_briefs, dict) else {}
    briefs: dict[str, dict] = {}
    completed_chapters: list[int] = []
    under_recommended_chapters: list[dict] = []
    added_scenes = 0
    budget_service = SceneBudgetService()

    for chapter in spine:
        if not isinstance(chapter, dict):
            continue
        chapter_number = chapter.get("chapter_number")
        if not isinstance(chapter_number, int) or chapter_number <= 0:
            continue

        chapter_id = str(chapter.get("chapter_id") or f"ch_{chapter_number:03d}")
        chapter["chapter_id"] = chapter_id
        brief = (
            raw_briefs.get(chapter_id)
            or raw_briefs.get(str(chapter_number))
            or next(
                (
                    item
                    for item in raw_briefs.values()
                    if isinstance(item, dict) and item.get("chapter_id") == chapter_id
                ),
                None,
            )
            or {}
        )
        brief = copy.deepcopy(brief) if isinstance(brief, dict) else {}
        budget = budget_service.calculate(chapter)

        scenes = brief.get("scenes")
        if not isinstance(scenes, list):
            scenes = chapter.get("scenes")
        scenes = copy.deepcopy(scenes) if isinstance(scenes, list) else []
        scenes = [
            dict(scene) if isinstance(scene, dict) else {"_invalid_scene_payload": str(scene)}
            for scene in scenes
        ]

        needed = budget["minimum_scene_count"] - len(scenes)
        if needed > 0:
            scenes.extend(
                _build_completion_scenes(
                    chapter, needed, start_index=len(scenes) + 1
                )
            )
            added_scenes += needed
            completed_chapters.append(chapter_number)

        for index, scene in enumerate(scenes, start=1):
            scene.setdefault("scene_id", f"{chapter_id}_s{index}")
            if scene.get("_invalid_scene_payload"):
                continue
            scene.setdefault("type", "development")
            scene.setdefault("goal", chapter.get("conflict_text") or chapter.get("title") or "推进本章目标")
            scene.setdefault("conflict", chapter.get("conflict_text") or "本章阻力尚待细化")
            scene.setdefault("outcome", chapter.get("hook") or "形成下一步行动")
            scene.setdefault("info_release", chapter.get("summary") or "补充本章关键信息")
            scene.setdefault("hook", chapter.get("hook") or "留下后续推进点")
            scene.setdefault("required_context_refs", [])

        old_signature = brief.get("scene_budget_signature")
        if old_signature and old_signature != budget["scene_budget_signature"]:
            brief["scene_count_user_overridden"] = False

        brief.update({
            "chapter_id": chapter_id,
            "version": brief.get("version", 1),
            "source": brief.get("source", "auto_completed"),
            "expires_when": brief.get("expires_when", ""),
            "expires_scope": brief.get("expires_scope", "current"),
            "minimum_scene_count": budget["minimum_scene_count"],
            "recommended_scene_count": budget["recommended_scene_count"],
            "scene_budget_reasons": budget["scene_budget_reasons"],
            "scene_budget_signature": budget["scene_budget_signature"],
            "scene_count_user_overridden": brief.get("scene_count_user_overridden", False),
            "scenes": scenes,
        })
        briefs[chapter_id] = brief
        chapter["scenes"] = copy.deepcopy(scenes)

        if (
            len(scenes) < budget["recommended_scene_count"]
            and not brief["scene_count_user_overridden"]
        ):
            under_recommended_chapters.append({
                "chapter_number": chapter_number,
                "actual": len(scenes),
                "recommended": budget["recommended_scene_count"],
            })

    normalized["scene_briefs"] = briefs
    if added_scenes:
        meta = normalized.get("meta")
        meta = copy.deepcopy(meta) if isinstance(meta, dict) else {}
        meta["scene_briefs_auto_completed"] = {
            "chapters": completed_chapters,
            "added_scenes": added_scenes,
        }
        normalized["meta"] = meta

    return normalized, _completion_stats(
        completed_chapters, added_scenes, under_recommended_chapters
    )


def _completion_stats(
    completed_chapters: list[int] | None = None,
    added_scenes: int = 0,
    under_recommended_chapters: list[dict] | None = None,
) -> dict:
    return {
        "completed_chapters": completed_chapters or [],
        "added_scenes": added_scenes,
        "under_recommended_chapters": under_recommended_chapters or [],
    }


def _build_completion_scenes(
    chapter_spine_item: dict, count: int, *, start_index: int = 1
) -> list[dict]:
    return [
        _build_completion_scene(chapter_spine_item, scene_index)
        for scene_index in range(start_index, start_index + count)
    ]


def _build_completion_scene(chapter: dict, scene_index: int) -> dict:
    chapter_number = chapter.get("chapter_number", 0)
    chapter_id = str(chapter.get("chapter_id") or f"ch_{chapter_number:03d}")
    conflict = chapter.get("core_conflict")
    conflict = conflict if isinstance(conflict, dict) else {}
    value_shift = chapter.get("value_shift")
    value_shift = value_shift if isinstance(value_shift, dict) else {}
    shift_from = value_shift.get("from") or "原有状态"
    shift_to = value_shift.get("to") or "新的状态"

    templates = [
        {
            "beat_role": "setup",
            "type": "discovery",
            "goal": conflict.get("desire") or chapter.get("conflict_text") or "建立本章行动目标",
            "conflict": conflict.get("obstacle") or chapter.get("conflict_text") or "遭遇本章核心阻力",
            "outcome": "明确本章必须解决的问题",
            "info_release": chapter.get("summary") or "释放推动本章冲突的必要信息",
            "hook": conflict.get("action") or "角色必须采取行动",
        },
        {
            "beat_role": "pressure",
            "type": "action",
            "goal": conflict.get("action") or "推进本章核心冲突",
            "conflict": conflict.get("obstacle") or chapter.get("conflict_text") or "阻力进一步升级",
            "outcome": "原有方案无法轻易奏效",
            "info_release": "补充改变判断的新信息",
            "hook": conflict.get("turn") or "角色面临新的代价",
        },
        {
            "beat_role": "decision",
            "type": "dialogue",
            "goal": "做出推动局势的关键选择",
            "conflict": conflict.get("turn") or "选择会带来新的损失",
            "outcome": conflict.get("action") or "角色确定下一步行动",
            "info_release": "释放影响选择的必要信息",
            "hook": "选择触发不可逆后果",
        },
        {
            "beat_role": "turning_point",
            "type": "action",
            "goal": "落实本章关键转折",
            "conflict": conflict.get("turn") or chapter.get("conflict_text") or "行动引发新的代价",
            "outcome": f"价值状态由「{shift_from}」转向「{shift_to}」",
            "info_release": chapter.get("hook") or "揭示下一章需要承接的新问题",
            "hook": chapter.get("hook") or "局势发生反转",
        },
        {
            "beat_role": "hook",
            "type": "transition",
            "goal": "收束本章并建立下一章驱动力",
            "conflict": "转折带来的余波仍未解决",
            "outcome": f"价值状态落在「{shift_to}」",
            "info_release": chapter.get("hook") or "留下必须继续追查的新问题",
            "hook": chapter.get("hook") or "留下下一章必须处理的推进点",
        },
    ]
    template = templates[min(max(scene_index, 1), len(templates)) - 1].copy()
    template.update({
        "scene_id": f"{chapter_id}_s{scene_index}",
        "required_context_refs": [],
    })
    return template
