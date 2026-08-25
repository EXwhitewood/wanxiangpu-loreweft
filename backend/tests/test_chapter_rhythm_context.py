import pytest

from app.agents.core_generation import _ensure_complete_scene_ending
from app.agents.editor_in_chief import EditorInChiefAgent, EditorState
from app.services.chapter_rhythm_context import (
    build_chapter_rhythm_context,
    build_chapter_rhythm_map,
    extract_scene_anchor,
    render_chapter_rhythm_context,
)


def _contracts():
    return [
        {
            "scene_id": "c1-s1",
            "pov_character": "凤溪",
            "goal": "凤溪醒来并获得模糊灾祸预感",
            "conflict": "记忆碎片混乱，不能立刻掌握全部阴谋",
            "must_show": ["只释放穿越和三日后有灾的模糊信息"],
            "forbidden": ["不得提前完整交代苏云清栽赃细节"],
            "ending_state": "苏云清即将来访，凤溪保持警惕",
        },
        {
            "scene_id": "c1-s2",
            "pov_character": "凤溪",
            "goal": "苏云清来访并试探凤溪",
            "conflict": "凤溪必须装作病弱，同时不能暴露自己已经察觉",
            "must_show": ["对话试探逐步升级"],
            "forbidden": ["不得让凤溪完美解码所有试探"],
            "ending_state": "凤溪获得搜查房间的动机",
        },
        {
            "scene_id": "c1-s3",
            "pov_character": "凤溪",
            "goal": "凤溪搜查房间并发现信件",
            "conflict": "眼线可能仍在附近，搜查必须隐蔽",
            "must_show": ["铁针、药方、信三次发现要有递进"],
            "forbidden": ["不得把搜查写成清单"],
            "ending_state": "凤溪意识到幕后还有霍姓人物",
        },
    ]


def test_build_chapter_rhythm_map_is_compact_and_scene_aware():
    rhythm_map = build_chapter_rhythm_map(_contracts(), chapter_number=1)

    assert "第1章3个场景" in rhythm_map
    assert "S1(c1-s1)" in rhythm_map
    assert "S2(c1-s2)" in rhythm_map
    assert "不得提前完整交代" not in rhythm_map
    assert len(rhythm_map) < 900


def test_render_chapter_rhythm_context_contains_previous_and_next_anchors():
    previous_anchor = extract_scene_anchor(
        "凤溪醒来，只记得三日后有灾。门外脚步声渐近。",
        _contracts()[0],
        scene_index=0,
        scene_facts={"established_facts": ["凤溪穿越", "三日后有灾"], "completed_events": ["凤溪醒来"]},
    )
    context = build_chapter_rhythm_context(
        _contracts(),
        scene_index=1,
        previous_scene_anchors=[previous_anchor],
        chapter_number=1,
    )
    rendered = render_chapter_rhythm_context(context)

    assert "章节节奏地图与前后锚点" in rendered
    assert "【S1(c1-s1)已生成】" in rendered
    assert "下一场景预告" in rendered
    assert "c1-s3" in rendered
    assert "本场景不是本章结尾" in rendered
    assert "信件、纸条、遗书" in rendered
    assert "不得替正文一次性解释完整阴谋" in rendered


def test_core_generation_completes_trailing_fragment():
    assert _ensure_complete_scene_ending("凤溪低声道：多谢") == "凤溪低声道：多谢。"
    assert _ensure_complete_scene_ending("凤溪转身离开。") == "凤溪转身离开。"


@pytest.mark.asyncio
async def test_editor_tool_preserves_chapter_rhythm_map():
    agent = EditorInChiefAgent()
    result = await agent._tool_propose_scene_contracts(
        {
            "chapter_number": 1,
            "chapter_rhythm_map": "S1快，S2慢，S3加速。",
            "scene_contracts": _contracts(),
        },
        EditorState(),
    )

    assert result["chapter_rhythm_map"] == "S1快，S2慢，S3加速。"
    assert result["_chapter_rhythm_map"] == "S1快，S2慢，S3加速。"
