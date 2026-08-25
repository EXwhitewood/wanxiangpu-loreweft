import pytest

from app.agents.outline_architect import OutlineArchitectAgent


@pytest.mark.asyncio
async def test_chapter_mode_blocks_story_plan_overwrite():
    result = await OutlineArchitectAgent()._execute_tool(
        "apply_story_plan",
        {"plan_json": {}},
        "project",
        None,
        "chapter",
        5,
    )

    assert "禁止覆盖整本大纲" in result["error"]


@pytest.mark.asyncio
async def test_chapter_mode_blocks_cross_chapter_update():
    result = await OutlineArchitectAgent()._execute_tool(
        "apply_chapter_change",
        {"chapter_number": 6, "updates": {}},
        "project",
        None,
        "chapter",
        5,
    )

    assert result == {"error": "章级模式下只能修改第5章。"}


@pytest.mark.parametrize(
    ("tool_name", "result", "expected"),
    [
        ("apply_story_plan", {"status": "applied"}, True),
        ("apply_chapter_change", {"status": "applied"}, True),
        ("create_chapter_outline", {"status": "created"}, True),
        ("delete_chapter_outline", {"status": "deleted"}, True),
        ("delete_chapter_outline", {"error": "blocked"}, False),
        ("apply_scene_brief", {"status": "applied"}, True),
        ("weave_propose", {"status": "executed"}, True),
        ("weave_propose", {"status": "pending"}, False),
        ("apply_scene_brief", {"error": "save failed"}, False),
        ("get_chapter_outline", {"chapter": {}}, False),
    ],
)
def test_tool_result_changed_only_marks_persisted_mutations(tool_name, result, expected):
    assert OutlineArchitectAgent()._tool_result_changed(tool_name, result) is expected


@pytest.mark.asyncio
async def test_chapter_mode_blocks_global_thread_plan_proposal():
    result = await OutlineArchitectAgent()._execute_tool(
        "weave_propose",
        {"target_domain": "thread_plan"},
        "project",
        None,
        "chapter",
        5,
    )

    assert "禁止修改全局线索计划" in result["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["create_chapter_outline", "delete_chapter_outline"])
async def test_chapter_mode_blocks_structural_chapter_resequence(tool_name):
    result = await OutlineArchitectAgent()._execute_tool(
        tool_name,
        {"chapter_number": 5, "chapter_data": {}},
        "project",
        None,
        "chapter",
        5,
    )

    assert "大纲总文件" in result["error"]
