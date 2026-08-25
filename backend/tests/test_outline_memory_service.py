from app.services.outline_memory_service import OutlineMemoryService


def _chapter(number: int, *, node_id: str | None = None) -> dict:
    item = {
        "chapter_id": f"ch_{number:03d}",
        "chapter_number": number,
        "title": f"第{number}章",
        "conflict_text": f"第{number}章冲突",
        "core_conflict": {"desire": "目标", "obstacle": "阻力"},
        "value_shift": {"axis": "局势", "from": "旧", "to": "新"},
        "pov_character": "主角",
        "hook": "章末钩子",
        "thread_ops": [],
    }
    if node_id is not None:
        item["node_id"] = node_id
    return item


def test_sparse_spine_without_node_ids_reports_added_chapters_instead_of_none_conflict():
    old_outline = {"chapter_spine": [_chapter(1), _chapter(4)]}
    new_outline = {
        "chapter_spine": [_chapter(1), _chapter(2), _chapter(3), _chapter(4)]
    }

    changes = OutlineMemoryService()._diff_outlines(old_outline, new_outline)

    assert [change["change_type"] for change in changes] == [
        "chapter_add",
        "chapter_add",
    ]
    assert [change["target"]["chapter_number"] for change in changes] == [2, 3]
    assert all("None" not in change["reason"] for change in changes)


def test_spine_identity_falls_back_when_newer_item_gains_node_id():
    old_outline = {"chapter_spine": [_chapter(1)]}
    new_outline = {"chapter_spine": [_chapter(1, node_id="opening_hook")]}

    changes = OutlineMemoryService()._diff_outlines(old_outline, new_outline)

    assert len(changes) == 1
    assert changes[0]["change_type"] == "chapter_modify"
    assert changes[0]["target"]["chapter_number"] == 1
    assert changes[0]["target"]["node_id"] == "opening_hook"
    assert "None" not in changes[0]["reason"]
