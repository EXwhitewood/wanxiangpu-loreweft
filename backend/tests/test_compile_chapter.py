import uuid
import dataclasses
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.services.generation_outline_adapter import (
    GenerationOutlineAdapter,
    CompileDiagnostic,
    CompiledChapterPackage,
    CompiledScenePackage,
)


@pytest.fixture
def mock_db():
    db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_result.scalar_one.return_value = 0
    db.execute = AsyncMock(return_value=mock_result)
    return db


@pytest.fixture
def adapter():
    return GenerationOutlineAdapter()


@pytest.mark.asyncio
async def test_count_written_chars_excludes_uncommitted_drafts(adapter):
    db = AsyncMock()
    result = MagicMock()
    result.all.return_value = [("已提交正文",)]
    db.execute = AsyncMock(return_value=result)

    total = await adapter._count_written_chars(uuid.uuid4(), 3, db)

    assert total > 0
    stmt = db.execute.await_args.args[0]
    compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "draft" not in compiled
    assert "committing" not in compiled
    assert "committed" in compiled


def _make_project(outline_data: dict) -> MagicMock:
    project = MagicMock()
    project.outline_data = outline_data
    project.word_count_target = 100000
    return project


def _base_scene(**overrides) -> dict:
    scene = {
        "scene_id": "ch_001_s1",
        "goal": "找到真相",
        "conflict": "敌人阻挠",
        "outcome": "获得线索",
        "hook": "新发现",
        "type": "narrative",
        "pov_character": "张三",
        "location": "北京",
    }
    scene.update(overrides)
    return scene


def _base_outline(chapter_number: int = 1, scenes: list[dict] | None = None, **overrides) -> dict:
    ch_id = f"ch_{chapter_number:03d}"
    outline = {
        "meta": {"version": 3},
        "chapter_spine": [
            {
                "chapter_number": chapter_number,
                "pov_character": "张三",
                "conflict_text": "核心冲突",
                "value_shift": "旧状态 -> 新状态",
                "hook": "悬念",
                "thread_ops": [],
            }
        ],
        "scene_briefs": {
            ch_id: {
                "scenes": scenes or [_base_scene()],
            }
        },
    }
    outline.update(overrides)
    return outline


def test_compile_diagnostic_fields():
    d = CompileDiagnostic(
        code="test_code",
        severity="blocking",
        field_path="a.b",
        source_path="c.d",
        message="错误消息",
        repair_hint="修复建议",
        blocks_generation=True,
    )
    field_names = {f.name for f in dataclasses.fields(d)}
    expected = {
        "code", "severity", "field_path", "source_path",
        "message", "repair_hint", "blocks_generation",
    }
    assert field_names == expected
    assert len(field_names) == 7

    as_dict = d.to_dict()
    assert set(as_dict.keys()) == expected


@pytest.mark.asyncio
async def test_compile_chapter_no_scenes_blocking(adapter, mock_db):
    project = _make_project({"meta": {"version": 1}})
    mock_db.get = AsyncMock(return_value=project)

    result = await adapter.compile_chapter(uuid.uuid4(), 1, {}, mock_db)

    assert isinstance(result, CompiledChapterPackage)
    assert result.chapter_number == 1
    assert result.scene_packages == []
    blocking_codes = [d.code for d in result.diagnostics if d.blocks_generation]
    assert "no_scenes" in blocking_codes
    assert result.has_blocking is True


@pytest.mark.asyncio
async def test_compile_chapter_blocks_invalid_serialized_scene(adapter, mock_db):
    outline = _base_outline(chapter_number=1)
    outline["scene_briefs"]["ch_001"]["scenes"] = ["@{scene_id=ch_001_s1; goal=broken}"]
    project = _make_project(outline)
    mock_db.get = AsyncMock(return_value=project)

    result = await adapter.compile_chapter(uuid.uuid4(), 1, {}, mock_db)

    assert result.has_blocking is True
    assert result.scene_packages == []
    assert result.blocking_diagnostics[0].code == "invalid_scene_brief_payload"


@pytest.mark.asyncio
async def test_compile_chapter_with_scene_briefs(adapter, mock_db):
    outline = _base_outline(chapter_number=1, scenes=[_base_scene()])
    project = _make_project(outline)
    mock_db.get = AsyncMock(return_value=project)

    result = await adapter.compile_chapter(uuid.uuid4(), 1, {"pov_character": "张三"}, mock_db)

    assert isinstance(result, CompiledChapterPackage)
    assert result.chapter_number == 1
    assert len(result.scene_packages) == 1

    sp = result.scene_packages[0]
    assert isinstance(sp, CompiledScenePackage)
    assert sp.pov_character == "张三"
    assert sp.scene_id == "ch_001_s1"
    assert sp.scene_beat["goal_text"] == "找到真相"
    assert sp.scene_beat["conflict_text"] == "敌人阻挠"
    assert sp.scene_beat["outcome_text"] == "获得线索"
    assert result.source_version == 3


@pytest.mark.asyncio
async def test_thread_ops_full_mapping(adapter, mock_db):
    thread_ops = [
        {"op": "plant", "thread_id": "伏笔A", "detail": "埋下种子"},
        {"op": "reveal", "thread_id": "伏笔B", "detail": "真相大白"},
        {"op": "remind", "thread_id": "伏笔C", "detail": "再次提及"},
        {"op": "escalate", "thread_id": "伏笔D", "detail": "矛盾升级"},
        {"op": "abandon", "thread_id": "伏笔E"},
    ]
    outline = _base_outline(
        chapter_number=1,
        scenes=[_base_scene()],
        chapter_spine=[
            {
                "chapter_number": 1,
                "pov_character": "张三",
                "conflict_text": "核心冲突",
                "value_shift": "旧状态 -> 新状态",
                "hook": "悬念",
                "thread_ops": thread_ops,
            }
        ],
    )
    project = _make_project(outline)
    mock_db.get = AsyncMock(return_value=project)

    result = await adapter.compile_chapter(uuid.uuid4(), 1, {}, mock_db)
    sp = result.scene_packages[0]
    must_show = sp.scene_contract["must_show"]
    forbidden = sp.scene_contract["forbidden"]
    operations = sp.scene_contract["source_of_truth"]["foreshadowing_ops"]

    assert must_show == []
    assert forbidden == []
    assert [op["op"] for op in operations] == ["plant", "reveal", "remind", "escalate", "abandon"]
    assert operations[0]["narrative_instruction"] == "埋下种子"
    assert operations[1]["narrative_instruction"] == "真相大白"
    assert "伏笔A" not in sp.scene_contract["must_show"]


@pytest.mark.asyncio
async def test_source_of_truth_immutable_fields(adapter, mock_db):
    outline = _base_outline(chapter_number=1, scenes=[_base_scene()])
    project = _make_project(outline)
    mock_db.get = AsyncMock(return_value=project)

    result = await adapter.compile_chapter(uuid.uuid4(), 1, {}, mock_db)
    sp = result.scene_packages[0]
    sot = sp.scene_contract["source_of_truth"]

    required_keys = {
        "goal", "conflict", "outline_outcome",
        "must_show_outline", "forbidden_outline", "foreshadowing_ops",
    }
    assert required_keys.issubset(set(sot.keys()))


@pytest.mark.asyncio
async def test_internal_thread_id_is_not_exposed_as_narrative_instruction(adapter, mock_db):
    outline = _base_outline(
        chapter_number=1,
        scenes=[_base_scene()],
        chapter_spine=[{
            "chapter_number": 1,
            "pov_character": "张三",
            "conflict_text": "核心冲突",
            "value_shift": "旧状态 -> 新状态",
            "thread_ops": [{"op": "plant", "thread_id": "thread_1"}],
        }],
        thread_plan={"threads": [{"thread_id": "thread_1", "name": "thread_1"}]},
    )
    mock_db.get = AsyncMock(return_value=_make_project(outline))

    result = await adapter.compile_chapter(uuid.uuid4(), 1, {}, mock_db)
    operation = result.scene_packages[0].scene_contract["source_of_truth"]["foreshadowing_ops"][0]

    assert operation["thread_id"] == "thread_1"
    assert "thread_1" not in operation["narrative_instruction"]


@pytest.mark.asyncio
async def test_spatial_blocking_when_moving_unknown(adapter, mock_db):
    scene = _base_scene(location="", destination_location="上海")
    outline = _base_outline(chapter_number=1, scenes=[scene])
    project = _make_project(outline)
    mock_db.get = AsyncMock(return_value=project)

    result = await adapter.compile_chapter(uuid.uuid4(), 1, {}, mock_db)
    sp = result.scene_packages[0]

    spatial_codes = [d.code for d in sp.diagnostics if d.code == "blocking_spatial_required"]
    assert len(spatial_codes) == 1
    spatial_diag = next(d for d in sp.diagnostics if d.code == "blocking_spatial_required")
    assert spatial_diag.blocks_generation is True


@pytest.mark.asyncio
async def test_clue_no_source_blocking(adapter, mock_db):
    scene = _base_scene(
        clues=[{"description": "关键证据", "source_actor": ""}]
    )
    outline = _base_outline(chapter_number=1, scenes=[scene])
    project = _make_project(outline)
    mock_db.get = AsyncMock(return_value=project)

    result = await adapter.compile_chapter(uuid.uuid4(), 1, {}, mock_db)
    sp = result.scene_packages[0]

    clue_codes = [d.code for d in sp.diagnostics if d.code == "blocking_clue_no_source"]
    assert len(clue_codes) == 1
    clue_diag = next(d for d in sp.diagnostics if d.code == "blocking_clue_no_source")
    assert clue_diag.blocks_generation is True


@pytest.mark.asyncio
async def test_string_clue_without_source_is_blocking(adapter, mock_db):
    outline = _base_outline(chapter_number=1, scenes=[_base_scene(clues=["关键证据"])])
    mock_db.get = AsyncMock(return_value=_make_project(outline))

    result = await adapter.compile_chapter(uuid.uuid4(), 1, {}, mock_db)

    assert any(d.code == "blocking_clue_no_source" for d in result.scene_packages[0].diagnostics)


@pytest.mark.asyncio
async def test_corridor_constraints_compiled(adapter, mock_db):
    outline = _base_outline(
        chapter_number=1,
        scenes=[_base_scene()],
        generation_corridors={
            "ch_001": {
                "constraints": ["必须展示角色犹豫", "必须提及旧伤疤"],
                "must_avoid": ["不得出现新角色"],
            }
        },
    )
    project = _make_project(outline)
    mock_db.get = AsyncMock(return_value=project)

    result = await adapter.compile_chapter(uuid.uuid4(), 1, {}, mock_db)
    sp = result.scene_packages[0]
    must_show = sp.scene_contract["must_show"]
    forbidden = sp.scene_contract["forbidden"]

    assert "必须展示角色犹豫" in must_show
    assert "必须提及旧伤疤" in must_show
    assert "不得出现新角色" in forbidden


@pytest.mark.asyncio
async def test_string_corridor_constraint_is_not_split_into_characters(adapter, mock_db):
    outline = _base_outline(
        chapter_number=1,
        scenes=[_base_scene()],
        generation_corridors={
            "ch_001": {
                "constraints": "必须保留雨夜氛围",
                "must_avoid": "不得提前揭示身份",
            }
        },
    )
    mock_db.get = AsyncMock(return_value=_make_project(outline))

    result = await adapter.compile_chapter(uuid.uuid4(), 1, {}, mock_db)
    contract = result.scene_packages[0].scene_contract

    assert "必须保留雨夜氛围" in contract["must_show"]
    assert "不得提前揭示身份" in contract["forbidden"]
    assert "必" not in contract["must_show"]


@pytest.mark.asyncio
async def test_structured_list_fields_are_wrapped_instead_of_iterating_keys(adapter, mock_db):
    scene = _base_scene(
        must_show={"description": "保留证物"},
        forbidden={"description": "不得离场"},
        required_context_refs={"id": "thread-1"},
        destination_location={"name": "玄天宗"},
        distance_state={"description": "三十里"},
    )
    outline = _base_outline(chapter_number=1, scenes=[scene])
    mock_db.get = AsyncMock(return_value=_make_project(outline))

    result = await adapter.compile_chapter(uuid.uuid4(), 1, {}, mock_db)
    contract = result.scene_packages[0].scene_contract

    assert len(contract["must_show"]) == 1
    assert "保留证物" in contract["must_show"][0]
    assert len(contract["forbidden"]) == 1
    assert contract["destination_location"] == "玄天宗"
    assert "三十里" in contract["distance_state"]


@pytest.mark.asyncio
async def test_structured_conflict_outcome(adapter, mock_db):
    scene = _base_scene(
        conflict={"desire": "找到真相", "obstacle": "敌人阻挠", "action": "奋力抗争", "turn": "意外转折"},
        outcome={"axis": "信任", "from": "怀疑", "to": "信任"},
    )
    outline = _base_outline(chapter_number=1, scenes=[scene])
    project = _make_project(outline)
    mock_db.get = AsyncMock(return_value=project)

    adapter._plan_service.get_conflict_text = MagicMock(
        return_value="找到真相，但敌人阻挠，但奋力抗争，但意外转折"
    )

    result = await adapter.compile_chapter(uuid.uuid4(), 1, {}, mock_db)
    sp = result.scene_packages[0]

    assert sp.scene_beat["conflict_text"] == "找到真相，但敌人阻挠，但奋力抗争，但意外转折"
    assert sp.scene_beat["outcome_text"] == "信任: 怀疑 -> 信任"


def test_has_blocking_property():
    blocking_diag = CompileDiagnostic(
        code="test", severity="blocking", field_path="", source_path="",
        message="", repair_hint="", blocks_generation=True,
    )
    warning_diag = CompileDiagnostic(
        code="test", severity="warning", field_path="", source_path="",
        message="", repair_hint="", blocks_generation=False,
    )

    pkg_no_blocking = CompiledChapterPackage(
        chapter_number=1, chapter_spine={}, scene_packages=[],
        diagnostics=[warning_diag], source_version=0,
    )
    assert pkg_no_blocking.has_blocking is False

    pkg_chapter_blocking = CompiledChapterPackage(
        chapter_number=1, chapter_spine={}, scene_packages=[],
        diagnostics=[blocking_diag], source_version=0,
    )
    assert pkg_chapter_blocking.has_blocking is True

    scene_with_blocking = CompiledScenePackage(
        scene_id="s1", scene_index=0, pov_character="A",
        scene_beat={}, scene_contract={}, word_budget={},
        diagnostics=[blocking_diag],
    )
    pkg_scene_blocking = CompiledChapterPackage(
        chapter_number=1, chapter_spine={}, scene_packages=[scene_with_blocking],
        diagnostics=[], source_version=0,
    )
    assert pkg_scene_blocking.has_blocking is True
