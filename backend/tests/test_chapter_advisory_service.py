import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api.writing_assistance import (
    load_writing_assistance_settings,
    update_writing_assistance_settings,
)
from app.db.db_models import Base, Project
from app.main import app
from app.models.story_state import StoryState
from app.models.writing_assistance import (
    AIEditPermissionSettings,
    ChapterAdvisoryCheckRequest,
    ConsistencyReminderSettings,
    WritingAssistanceSettings,
)
from app.services.chapter_advisory_service import ChapterAdvisoryService
from app.services.llm_gateway import LLMGatewayResult
from app.services.llm_task_profiles import LLMTaskType


def _project() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        name="提醒测试",
        description="",
        genre="悬疑",
        core_data={
            "characters": [{"name": "林澈", "status": "左臂受伤"}],
            "world_rules": [{"name": "灵钥", "rule": "碎裂后不能复原"}],
        },
        outline_data={
            "chapter_spine": [
                {"chapter_number": 3, "title": "废塔", "pov_character": "林澈"}
            ]
        },
    )


def _enabled_settings(*dimensions: str) -> WritingAssistanceSettings:
    return WritingAssistanceSettings(
        consistency_reminders=ConsistencyReminderSettings(
            enabled=True,
            check_on_save=True,
            dimensions=list(dimensions),
        )
    )


@pytest.mark.asyncio
async def test_disabled_reminders_do_not_touch_detectors_or_llm():
    gateway = SimpleNamespace(generate_json=AsyncMock())
    fcip = SimpleNamespace(post_gen_writeback=AsyncMock())
    state = SimpleNamespace(get_state=AsyncMock())
    service = ChapterAdvisoryService(
        gateway=gateway,
        fcip_engine=fcip,
        state_manager=state,
    )

    response = await service.check(
        project=_project(),
        project_id=str(uuid.uuid4()),
        chapter_number=3,
        request=ChapterAdvisoryCheckRequest(content="<p>正文</p>"),
        settings=WritingAssistanceSettings(
            consistency_reminders=ConsistencyReminderSettings(
                enabled=False,
                check_on_save=False,
            )
        ),
        db=object(),
    )

    assert response.status == "disabled"
    assert response.blocks_commit is False
    gateway.generate_json.assert_not_awaited()
    fcip.post_gen_writeback.assert_not_awaited()
    state.get_state.assert_not_awaited()


@pytest.mark.asyncio
async def test_save_trigger_is_opt_in_even_when_manual_reminders_are_enabled():
    gateway = SimpleNamespace(generate_json=AsyncMock())
    fcip = SimpleNamespace(post_gen_writeback=AsyncMock())
    state = SimpleNamespace(get_state=AsyncMock())
    service = ChapterAdvisoryService(
        gateway=gateway,
        fcip_engine=fcip,
        state_manager=state,
    )
    settings = WritingAssistanceSettings(
        consistency_reminders=ConsistencyReminderSettings(
            enabled=True,
            check_on_save=False,
            dimensions=["fact"],
        )
    )

    response = await service.check(
        project=_project(),
        project_id=str(uuid.uuid4()),
        chapter_number=3,
        request=ChapterAdvisoryCheckRequest(content="正文", trigger="save"),
        settings=settings,
        db=object(),
    )

    assert response.status == "disabled"
    assert response.diagnostics[0].code == "save_check_disabled"
    gateway.generate_json.assert_not_awaited()
    fcip.post_gen_writeback.assert_not_awaited()
    state.get_state.assert_not_awaited()


@pytest.mark.asyncio
async def test_check_combines_read_only_fcip_and_semantic_advisories():
    gateway = SimpleNamespace(
        generate_json=AsyncMock(
            return_value=LLMGatewayResult(
                ok=True,
                parsed_json={
                    "findings": [
                        {
                            "category": "fact",
                            "severity": "high",
                            "title": "灵钥状态冲突",
                            "detail": "正文称灵钥完好，但设定记录它已经碎裂。",
                            "evidence_quote": "他把完好的灵钥放回口袋",
                            "source": {
                                "source_type": "world_rule",
                                "label": "灵钥",
                                "excerpt": "碎裂后不能复原",
                            },
                            "confidence": 0.91,
                        }
                    ]
                },
            )
        )
    )
    fcip = SimpleNamespace(
        post_gen_writeback=AsyncMock(
            return_value={
                "violations": [
                    {
                        "rule_id": "FH-07",
                        "severity": "High",
                        "description": "角色已知真相却表现为不知情",
                        "matched_text": "他对此毫无头绪",
                        "suggestion": "核对该角色当前认知状态",
                    }
                ]
            }
        )
    )
    state = SimpleNamespace(
        get_state=AsyncMock(
            return_value=StoryState(
                active_chapter=3,
                pov_character="林澈",
                completed_events=["灵钥碎裂"],
            )
        )
    )
    service = ChapterAdvisoryService(
        gateway=gateway,
        fcip_engine=fcip,
        state_manager=state,
    )
    content = "<p>他把完好的灵钥放回口袋。</p><p>他对此毫无头绪。</p>"

    response = await service.check(
        project=_project(),
        project_id=str(uuid.uuid4()),
        chapter_number=3,
        request=ChapterAdvisoryCheckRequest(content=content, trigger="manual"),
        settings=_enabled_settings("fact", "foreshadowing"),
        db=object(),
    )

    assert response.status == "complete"
    assert {finding.category for finding in response.findings} == {
        "fact",
        "foreshadowing",
    }
    assert all(finding.blocks_commit is False for finding in response.findings)
    assert all(finding.repair_scope == "advisory" for finding in response.findings)
    assert all(finding.repair_lane == "none" for finding in response.findings)
    fcip.post_gen_writeback.assert_awaited_once()
    assert fcip.post_gen_writeback.await_args.kwargs["apply_writeback"] is False
    assert gateway.generate_json.await_args.kwargs["task_type"] == LLMTaskType.JSON_DETECTION


@pytest.mark.asyncio
async def test_provider_failure_degrades_without_blocking_or_raising():
    gateway = SimpleNamespace(
        generate_json=AsyncMock(
            return_value=LLMGatewayResult(
                ok=False,
                error_type="provider_timeout",
                error_message="timeout",
            )
        )
    )
    service = ChapterAdvisoryService(
        gateway=gateway,
        fcip_engine=SimpleNamespace(post_gen_writeback=AsyncMock()),
        state_manager=SimpleNamespace(get_state=AsyncMock(return_value=StoryState())),
    )

    response = await service.check(
        project=_project(),
        project_id=str(uuid.uuid4()),
        chapter_number=3,
        request=ChapterAdvisoryCheckRequest(content="正文仍可保存"),
        settings=_enabled_settings("fact"),
        db=object(),
    )

    assert response.status == "degraded"
    assert response.blocks_commit is False
    assert response.findings == []
    assert response.diagnostics[0].code == "provider_timeout"


@pytest.mark.asyncio
async def test_semantic_finding_requires_exact_draft_evidence_and_has_stable_fingerprint():
    payload = {
        "findings": [
            {
                "category": "world_rule",
                "severity": "medium",
                "title": "有效提醒",
                "detail": "存在冲突",
                "evidence_quote": "火焰在水下燃烧",
                "source": {
                    "source_type": "world_rule",
                    "label": "水域规则",
                    "excerpt": "普通火焰无法在水下燃烧",
                },
                "confidence": 0.88,
            },
            {
                "category": "world_rule",
                "severity": "high",
                "title": "幻觉证据",
                "detail": "模型补造了正文",
                "evidence_quote": "正文里不存在的句子",
                "source": {
                    "source_type": "world_rule",
                    "label": "水域规则",
                    "excerpt": "普通火焰无法在水下燃烧",
                },
                "confidence": 0.99,
            },
        ]
    }
    gateway = SimpleNamespace(
        generate_json=AsyncMock(return_value=LLMGatewayResult(ok=True, parsed_json=payload))
    )
    service = ChapterAdvisoryService(
        gateway=gateway,
        fcip_engine=SimpleNamespace(post_gen_writeback=AsyncMock()),
        state_manager=SimpleNamespace(get_state=AsyncMock(return_value=StoryState())),
    )
    settings = _enabled_settings("world_rule")
    project = _project()
    project.core_data["world_rules"] = [
        {"name": "水域规则", "rule": "普通火焰无法在水下燃烧"}
    ]

    first = await service.check(
        project=project,
        project_id=str(uuid.uuid4()),
        chapter_number=3,
        request=ChapterAdvisoryCheckRequest(content="火焰在水下燃烧。"),
        settings=settings,
        db=object(),
    )
    second = await service.check(
        project=project,
        project_id=str(uuid.uuid4()),
        chapter_number=3,
        request=ChapterAdvisoryCheckRequest(content="他停了一下。火焰在水下燃烧。"),
        settings=settings,
        db=object(),
    )

    assert len(first.findings) == 1
    assert len(second.findings) == 1
    assert first.findings[0].fingerprint == second.findings[0].fingerprint
    assert first.revision_hash != second.revision_hash


@pytest.mark.asyncio
async def test_settings_are_stored_under_core_data_without_losing_other_domains():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    project_id = uuid.uuid4()
    try:
        async with session_factory() as db:
            project = Project(
                id=project_id,
                name="settings-test",
                core_data={"characters": [{"name": "林澈"}]},
            )
            db.add(project)
            await db.commit()
            data = WritingAssistanceSettings(
                consistency_reminders=ConsistencyReminderSettings(
                    enabled=True,
                    check_on_save=False,
                    dimensions=["fact", "timeline"],
                ),
                ai_edit_permission=AIEditPermissionSettings(mode="proposal_only"),
            )

            result = await update_writing_assistance_settings(data, project, db)

            assert result == data
            assert project.core_data["characters"] == [{"name": "林澈"}]
            assert load_writing_assistance_settings(project) == data
    finally:
        await engine.dispose()


def test_writing_assistance_routes_are_registered_on_the_main_app():
    paths = set(app.openapi()["paths"])
    prefix = "/api/projects/{project_id}/writing-assistance"
    assert f"{prefix}/settings" in paths
    assert f"{prefix}/chapters/{{chapter_number}}/check" in paths
    assert f"{prefix}/chapters/{{chapter_number}}/diagnostics" in paths
    assert (
        f"{prefix}/chapters/{{chapter_number}}/diagnostics/{{diagnostic_id}}/decision"
        in paths
    )
