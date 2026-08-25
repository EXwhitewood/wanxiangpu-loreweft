import copy
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.outline_architect import OutlineArchitectAgent
from app.api.outline import _guided_generate
from app.db.db_models import Project
from app.services.outline_expansion_service import OutlineExpansionService
from app.services.story_plan_service import StoryPlanService


def _chapter(number: int, *, prefix: str = "chapter") -> dict:
    return {
        "chapter_id": f"ch_{number:03d}",
        "chapter_number": number,
        "title": f"{prefix}-{number}",
        "summary": f"第{number}章的单一剧情推进",
        "conflict_text": f"主角要推进第{number}步，但遇到对应阻力",
        "core_conflict": {
            "desire": "推进目标",
            "obstacle": "遭遇阻力",
            "action": "采取行动",
            "turn": "得到意外结果",
        },
        "value_shift": {"axis": "掌控", "from": "被动", "to": "主动"},
        "pov_character": "林然",
        "hook": f"第{number}章末出现新问题",
        "thread_ops": [],
    }


def _outline(count: int, *, prefix: str = "chapter") -> dict:
    return {
        "chapter_spine": [_chapter(number, prefix=prefix) for number in range(1, count + 1)],
        "meta": {"version": 1},
    }


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("请帮我规划12章大纲，直接开始", 12),
        ("生成80章的逐章规划", 80),
        ("先生成前30章，直接开始", 30),
        ("我需要你制定一百二十章的章节大纲", 120),
        ("把故事扩展成两千章", 2000),
    ],
)
def test_multi_chapter_planning_detects_arbitrary_explicit_targets(message, expected):
    request = OutlineArchitectAgent()._detect_multi_chapter_planning_request(
        [{"role": "user", "content": message}],
        _outline(3),
        "master",
        uuid.uuid4(),
        object(),
    )

    assert request == {
        "target_chapters": expected,
        "batch_size": 25,
        "replace_existing": True,
        "force_restart": False,
    }


def test_multi_chapter_planning_does_not_mutate_for_question_or_single_chapter():
    agent = OutlineArchitectAgent()
    for message in ("如何规划120章的大纲？", "能不能生成80章？", "你会生成80章吗？", "请修改第十章"):
        request = agent._detect_multi_chapter_planning_request(
            [{"role": "user", "content": message}],
            _outline(10),
            "master",
            uuid.uuid4(),
            object(),
        )
        assert request is None


def test_regeneration_uses_previous_declared_target_not_deleted_chapter_count():
    request = OutlineArchitectAgent()._detect_multi_chapter_planning_request(
        [
            {"role": "user", "content": "全书原定八十章"},
            {"role": "assistant", "content": "明白。"},
            {"role": "user", "content": "请删除现在的十章并重新生成"},
        ],
        _outline(10),
        "master",
        uuid.uuid4(),
        object(),
    )

    assert request["target_chapters"] == 80
    assert request["force_restart"] is True


@pytest.mark.asyncio
async def test_guided_generation_uses_managed_workflow_for_any_multi_chapter_target(monkeypatch):
    captured = {}

    async def fake_start(self, project_id, target_chapters, db, **kwargs):
        captured.update({"target": target_chapters, "kwargs": kwargs})
        return {
            "status": "started",
            "has_job": True,
            "target_chapters": target_chapters,
            "completed_chapters": 0,
            "actual_chapters": 0,
        }

    monkeypatch.setattr(OutlineExpansionService, "start", fake_start)
    result = await _guided_generate(
        None,
        {"name": "generic-plan", "description": "", "genre": "奇幻"},
        {"chapter_count": "12章", "core_theme": "自由"},
        uuid.uuid4(),
        object(),
    )

    assert captured["target"] == 12
    assert result["staged"] is True
    assert result["generated_chapters"] == 0
    assert result["expansion"]["target_chapters"] == 12


@pytest.mark.asyncio
async def test_expected_500_rejects_ten_chapters_without_mutating_official_outline(db):
    project_id = uuid.uuid4()
    official = _outline(10, prefix="official")
    db.add(Project(
        id=project_id,
        name="long-form-count-guard",
        outline_data=copy.deepcopy(official),
        draft_outline={},
    ))
    await db.commit()

    incoming = _outline(10, prefix="incoming")
    incoming["meta"]["generation_target_chapters"] = 500
    result = await StoryPlanService().save_outline_data(
        project_id,
        incoming,
        db,
        mode="replace",
        expected_chapter_count=500,
    )

    project = await db.get(Project, project_id)
    assert result["code"] == "chapter_count_mismatch"
    assert result["actual_chapter_count"] == 10
    assert result["expected_chapter_count"] == 500
    assert project.outline_data == official


@pytest.mark.asyncio
async def test_apply_story_plan_requires_long_form_workflow_for_500_chapters(db):
    project_id = uuid.uuid4()
    official = _outline(10, prefix="official")
    db.add(Project(
        id=project_id,
        name="agent-count-guard",
        outline_data=copy.deepcopy(official),
        draft_outline={},
    ))
    await db.commit()

    result = await OutlineArchitectAgent()._execute_tool(
        "apply_story_plan",
        {
            "plan_json": _outline(10, prefix="incoming"),
            "expected_chapter_count": 500,
            "mode": "replace",
        },
        str(project_id),
        db,
    )

    project = await db.get(Project, project_id)
    assert result["code"] == "outline_expansion_required"
    assert result["actual_chapter_count"] == 10
    assert project.outline_data == official


@pytest.mark.asyncio
async def test_stream_detects_historical_500_target_and_latest_regeneration_request(monkeypatch):
    captured = {}

    async def fake_execute_tool(self, name, args, project_id, db, context_mode, selected_chapter_number):
        captured.update({"name": name, "args": args})
        return {
            "status": "started",
            "target_chapters": args["target_chapters"],
            "completed_chapters": 0,
        }

    monkeypatch.setattr(OutlineArchitectAgent, "_execute_tool", fake_execute_tool)
    messages = [
        {"role": "user", "content": "我定的不是500章吗？怎么只有十章"},
        {"role": "assistant", "content": "建议把十个节点展开。"},
        {"role": "user", "content": "我需要你删除现在的十章重新生成"},
    ]

    events = [event async for event in OutlineArchitectAgent().execute_stream({
        "messages": messages,
        "existing_outline": _outline(10),
        "project_id": str(uuid.uuid4()),
        "db": object(),
        "context_mode": "master",
    })]

    assert captured["name"] == "start_outline_expansion"
    assert captured["args"] == {
        "target_chapters": 500,
        "batch_size": 25,
        "replace_existing": True,
        "force_restart": True,
    }
    assert [event["type"] for event in events] == [
        "tool_call", "tool_result", "text_delta", "done",
    ]
    assert "0/500" in events[-1]["data"]["response"]
    assert "任务已完成" not in events[-1]["data"]["response"]


@pytest.mark.asyncio
async def test_500_chapter_expansion_resumes_after_failed_batch_and_replaces_atomically(db):
    project_id = uuid.uuid4()
    official = _outline(10, prefix="old-official")
    official["macro_plan"] = {
        "structure_model": "ten_anchor",
        "volumes": [{"name": f"旧节点{number}", "chapter_range": [number, number]} for number in range(1, 11)],
    }
    official["thread_plan"] = {
        "threads": [{
            "thread_id": "main",
            "name": "主线",
            "plant_chapters": [1],
            "escalation_chapters": [5],
            "payoff_chapters": [10],
        }],
    }
    db.add(Project(
        id=project_id,
        name="resumable-500",
        description="一部需要展开为五百章的长篇",
        genre="奇幻",
        outline_data=copy.deepcopy(official),
        draft_outline={},
        outline_version=0,
    ))
    await db.commit()

    failed_once = False

    async def generate_batch(context):
        nonlocal failed_once
        start = context["start_chapter"]
        end = context["end_chapter"]
        if start == 51 and not failed_once:
            failed_once = True
            raise TimeoutError("simulated upstream timeout")
        return {
            "chapter_spine": [
                _chapter(number, prefix="expanded")
                for number in range(start, end + 1)
            ],
        }

    session_factory = async_sessionmaker(
        db.bind,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    service = OutlineExpansionService(
        session_factory=session_factory,
        batch_generator=generate_batch,
    )

    start = await service.start(
        project_id,
        500,
        db,
        batch_size=25,
        force_restart=True,
        schedule=False,
    )
    job_id = start["job_id"]
    await service._run(str(project_id), job_id)

    async with session_factory() as check_db:
        project = await check_db.get(Project, project_id)
        job = project.draft_outline["meta"]["expansion_job"]
        assert job["status"] == "failed"
        assert job["completed_chapters"] == 50
        assert len(project.draft_outline["chapter_spine"]) == 50
        assert project.outline_data == official

        resumed = await service.resume(project_id, check_db, schedule=False)
        assert resumed["status"] == "resumed"

    await service._run(str(project_id), job_id)

    async with session_factory() as check_db:
        project = await check_db.get(Project, project_id)
        numbers = [item["chapter_number"] for item in project.outline_data["chapter_spine"]]
        assert numbers == list(range(1, 501))
        assert len(project.outline_data["chapters"]) == 500
        assert project.outline_data["meta"]["generation_target_chapters"] == 500
        assert project.outline_data["meta"]["expansion_job"]["status"] == "completed"
        assert len(project.outline_data["macro_plan"]["volumes"]) == 10
        assert project.outline_data["macro_plan"]["volumes"][-1]["chapter_range"] == [451, 500]
        thread = project.outline_data["thread_plan"]["threads"][0]
        assert thread["plant_chapters"] == [1]
        assert thread["escalation_chapters"] == [225]
        assert thread["payoff_chapters"] == [500]
        assert project.draft_outline == {}
