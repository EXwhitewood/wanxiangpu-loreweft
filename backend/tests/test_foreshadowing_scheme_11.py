"""方案 11：伏笔库归位 Core 层与生命周期修复测试。

验证：
1. 状态机扩展：active → revealing 直接转换（方案 11 Part E2）
2. 状态机扩展：dormant → active 唤醒（方案 11 Part E2）
3. 伏笔定义校验：create_foreshadowing 缺少秘密/埋设点/揭示点时抛出 ValueError（Part C）
4. list_foreshadowing 状态分层：include_archived 控制 resolved 是否返回（Part A2）
5. Bug 1修复：scene_generation_pipeline 不再 best-effort 强推 active→resolved（Part D Bug 1）
6. Bug 2修复：fcip_engine.post_gen_writeback 推进伏笔状态（Part D Bug 2）
"""
from __future__ import annotations

import asyncio
import inspect
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.foreshadowing_service import (
    ForeshadowingService,
    FORESHADOWING_STATE_TRANSITIONS,
)


# 测试用真实 UUID，FCIPEngine 内部会尝试转换 project_id
_TEST_PROJECT_ID = str(uuid.uuid4())


# --------------------------------------------------------------- 状态机扩展

class TestStateMachineExtensions:
    """方案 11 Part E2：状态机扩展。"""

    def test_active_can_transition_directly_to_revealing(self):
        """方案 11：active → revealing 直接转换合法（不必经过 dormant）。"""
        assert "revealing" in FORESHADOWING_STATE_TRANSITIONS["active"]

    def test_dormant_can_transition_back_to_active(self):
        """方案 11：dormant → active 唤醒合法（再次被提及时唤醒）。"""
        assert "active" in FORESHADOWING_STATE_TRANSITIONS["dormant"]

    def test_planned_cannot_skip_to_resolved(self):
        """方案 11：planned → resolved 仍然非法（必须经过 active/revealing）。"""
        assert "resolved" not in FORESHADOWING_STATE_TRANSITIONS["planned"]

    def test_aborted_is_still_terminal(self):
        """方案 11：aborted 仍是终止态。"""
        assert FORESHADOWING_STATE_TRANSITIONS["aborted"] == set()

    def test_any_active_state_can_abort(self):
        """方案 11：任意活跃态都能转 aborted。"""
        for state in ("planned", "active", "dormant", "revealing"):
            assert "aborted" in FORESHADOWING_STATE_TRANSITIONS[state]


# --------------------------------------------------------- 伏笔定义校验

class TestForeshadowingDefinitionValidation:
    """方案 11 Part C：伏笔定义校验。"""

    @pytest.mark.asyncio
    async def test_create_without_secret_raises(self):
        """方案 11 Part C：缺少秘密时 create_foreshadowing 抛出 ValueError。"""
        from app.services.memory_core import CoreMemoryService

        service = CoreMemoryService()
        with pytest.raises(ValueError, match="秘密"):
            await service.create_foreshadowing("p1", {
                "name": "测试伏笔",
                "bury_window_start": 1,
                "bury_window_end": 5,
                "reveal_window_start": 10,
                "reveal_window_end": 15,
            })

    @pytest.mark.asyncio
    async def test_create_without_bury_window_raises(self):
        """方案 11 Part C：缺少埋设点时抛出 ValueError。"""
        from app.services.memory_core import CoreMemoryService

        service = CoreMemoryService()
        with pytest.raises(ValueError, match="埋设点"):
            await service.create_foreshadowing("p1", {
                "name": "测试伏笔",
                "description": "这是一个秘密",
                "reveal_window_start": 10,
                "reveal_window_end": 15,
            })

    @pytest.mark.asyncio
    async def test_create_without_reveal_window_raises(self):
        """方案 11 Part C：缺少揭示点时抛出 ValueError。"""
        from app.services.memory_core import CoreMemoryService

        service = CoreMemoryService()
        with pytest.raises(ValueError, match="揭示点"):
            await service.create_foreshadowing("p1", {
                "name": "测试伏笔",
                "description": "这是一个秘密",
                "bury_window_start": 1,
                "bury_window_end": 5,
            })

    @pytest.mark.asyncio
    async def test_create_with_full_definition_passes_validation(self):
        """方案 11 Part C：完整定义通过校验（不抛异常）。"""
        from app.services.memory_core import CoreMemoryService

        service = CoreMemoryService()
        # mock ForeshadowingService.create_foreshadowing_line 避免真访问数据库
        with patch(
            "app.services.foreshadowing_service.ForeshadowingService.create_foreshadowing_line",
            new_callable=AsyncMock,
            return_value={"id": "fs1", "name": "测试伏笔"},
        ), patch(
            "app.services.memory_core.async_session"
        ) as mock_session:
            # async_session() 返回 async context manager，session 需要 async commit
            mock_session_obj = MagicMock()
            mock_session_obj.commit = AsyncMock()
            mock_ctx = MagicMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_session_obj)
            mock_ctx.__aexit__ = AsyncMock(return_value=None)
            mock_session.return_value = mock_ctx

            result = await service.create_foreshadowing(_TEST_PROJECT_ID, {
                "name": "测试伏笔",
                "description": "这是一个秘密",
                "bury_window_start": 1,
                "bury_window_end": 5,
                "reveal_window_start": 10,
                "reveal_window_end": 15,
            })

            assert result["id"] == "fs1"


# ------------------------------------------------------- list_foreshadowing 分层

class TestListForeshadowingLayering:
    """方案 11 Part A2：list_foreshadowing 状态分层。"""

    @pytest.mark.asyncio
    async def test_list_foreshadowing_excludes_resolved_by_default(self):
        """方案 11 Part A2：默认不返回 resolved。"""
        from app.services.memory_core import CoreMemoryService

        service = CoreMemoryService()
        captured_statuses = None

        async def fake_list_lines(project_id, session, statuses=None):
            nonlocal captured_statuses
            captured_statuses = statuses
            return [{"status": s} for s in statuses]

        with patch(
            "app.services.foreshadowing_service.ForeshadowingService.list_foreshadowing_lines",
            new_callable=AsyncMock,
            side_effect=fake_list_lines,
        ), patch(
            "app.services.memory_core.async_session"
        ) as mock_session:
            mock_session_obj = MagicMock()
            mock_ctx = MagicMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_session_obj)
            mock_ctx.__aexit__ = AsyncMock(return_value=None)
            mock_session.return_value = mock_ctx

            result = await service.list_foreshadowing(_TEST_PROJECT_ID)

            # 默认不包含 resolved
            assert "resolved" not in captured_statuses
            assert "aborted" not in captured_statuses

    @pytest.mark.asyncio
    async def test_list_foreshadowing_includes_resolved_when_archived_true(self):
        """方案 11 Part A2：include_archived=True 时返回 resolved。"""
        from app.services.memory_core import CoreMemoryService

        service = CoreMemoryService()
        captured_statuses = None

        async def fake_list_lines(project_id, session, statuses=None):
            nonlocal captured_statuses
            captured_statuses = statuses
            return [{"status": s} for s in statuses]

        with patch(
            "app.services.foreshadowing_service.ForeshadowingService.list_foreshadowing_lines",
            new_callable=AsyncMock,
            side_effect=fake_list_lines,
        ), patch(
            "app.services.memory_core.async_session"
        ) as mock_session:
            mock_session_obj = MagicMock()
            mock_ctx = MagicMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_session_obj)
            mock_ctx.__aexit__ = AsyncMock(return_value=None)
            mock_session.return_value = mock_ctx

            await service.list_foreshadowing(_TEST_PROJECT_ID, include_archived=True)

            assert "resolved" in captured_statuses
            # aborted 仍然不返回
            assert "aborted" not in captured_statuses


# ----------------------------------------------------------- Bug 1 修复

class TestBug1BestEffortPushRemoved:
    """方案 11 Part D Bug 1：删除 best-effort 强推 active→resolved。"""

    def test_persist_scene_effects_does_not_force_active_to_resolved(self):
        """方案 11 Part D Bug 1：persist_scene_effects 不再直接强推 active→resolved。

        通过检查源代码不含 update_foreshadowing({"status": "resolved"}) 的强推逻辑。
        """
        import app.services.scene_generation_pipeline as generation_module
        source = inspect.getsource(generation_module.persist_scene_effects)
        # 不应含直接强推 active→resolved 的逻辑
        assert 'fs.get("status") == "active" and fs.get("reveal_window_start")' not in source
        assert 'update_foreshadowing(\n                    project_id, fs.get("id", ""), {"status": "resolved"}' not in source
        # 应含说明注释
        assert "方案 11 Part D Bug 1" in source


# ----------------------------------------------------------- Bug 2 修复

class TestBug2PostGenWritebackAdvancesState:
    """方案 11 Part D Bug 2：post_gen_writeback 推进伏笔状态。"""

    def test_post_gen_writeback_returns_state_advancements(self):
        """方案 11 Part D Bug 2：返回值含 state_advancements 字段。"""
        import app.engines.fcip_engine as fcip_module
        source = inspect.getsource(fcip_module.FCIPEngine.post_gen_writeback)
        # 必须含 transition_state 调用
        assert "transition_state" in source
        # 必须含 state_advancements 返回字段
        assert "state_advancements" in source
        # 必须含三种状态推进
        assert '"resolved"' in source
        assert '"revealing"' in source
        assert '"active"' in source

    @pytest.mark.asyncio
    async def test_post_gen_writeback_advances_revealing_to_resolved_in_window(self):
        """方案 11 Part D Bug 2：revealing 状态在揭示窗口内推进到 resolved。"""
        from app.engines.fcip_engine import FCIPEngine

        # 模拟 ForeshadowingService
        mock_fs = MagicMock()
        mock_fs.list_actionable_for_scene = AsyncMock(return_value=[
            {
                "id": "fs-1",
                "name": "测试伏笔",
                "status": "revealing",
                "reveal_window_start": 10,
                "reveal_window_end": 15,
                "secret_spoiler_scope": {"characters": []},
            }
        ])
        mock_fs.list_cognitive_states_for_foreshadowing = AsyncMock(return_value=[])
        mock_fs.transition_state = AsyncMock(return_value={"id": "fs-1", "status": "resolved"})
        mock_fs.set_character_cognitive_state = AsyncMock()

        # 模拟 clue_service
        mock_clue = MagicMock()
        mock_clue.get_evidence_pool = AsyncMock(return_value={"clues": []})

        engine = FCIPEngine(mock_fs)

        with patch("app.engines.fcip_engine._lazy_clue_service", return_value=mock_clue), \
             patch("app.engines.fcip_detector.run_deterministic_checks", return_value=[]):
            result = await engine.post_gen_writeback(
                project_id=_TEST_PROJECT_ID,
                chapter_number=12,  # 在揭示窗口 10-15 内
                generated_text="测试文本",
                db=MagicMock(),
                apply_writeback=True,
            )

        # 应推进 revealing → resolved
        mock_fs.transition_state.assert_called()
        call_args = mock_fs.transition_state.call_args
        assert call_args.args[2] == "resolved"  # new_status
        assert result["state_advancements"]
        assert result["state_advancements"][0]["from"] == "revealing"
        assert result["state_advancements"][0]["to"] == "resolved"

    @pytest.mark.asyncio
    async def test_post_gen_writeback_advances_planned_to_active_in_bury_window(self):
        """方案 11 Part D Bug 2：planned 状态在埋设窗口内推进到 active。"""
        from app.engines.fcip_engine import FCIPEngine

        mock_fs = MagicMock()
        mock_fs.list_actionable_for_scene = AsyncMock(return_value=[
            {
                "id": "fs-2",
                "name": "待埋设伏笔",
                "status": "planned",
                "bury_window_start": 1,
                "bury_window_end": 5,
                "reveal_window_start": 10,
                "reveal_window_end": 15,
                "secret_spoiler_scope": {"characters": []},
            }
        ])
        mock_fs.list_cognitive_states_for_foreshadowing = AsyncMock(return_value=[])
        mock_fs.transition_state = AsyncMock(return_value={"id": "fs-2", "status": "active"})

        mock_clue = MagicMock()
        mock_clue.get_evidence_pool = AsyncMock(return_value={"clues": []})

        engine = FCIPEngine(mock_fs)

        with patch("app.engines.fcip_engine._lazy_clue_service", return_value=mock_clue), \
             patch("app.engines.fcip_detector.run_deterministic_checks", return_value=[]):
            result = await engine.post_gen_writeback(
                project_id=_TEST_PROJECT_ID,
                chapter_number=3,  # 在埋设窗口 1-5 内
                generated_text="测试文本",
                db=MagicMock(),
                apply_writeback=True,
            )

        mock_fs.transition_state.assert_called()
        call_args = mock_fs.transition_state.call_args
        assert call_args.args[2] == "active"
        assert result["state_advancements"][0]["from"] == "planned"
        assert result["state_advancements"][0]["to"] == "active"

    @pytest.mark.asyncio
    async def test_post_gen_writeback_does_not_advance_when_not_in_window(self):
        """方案 11 Part D Bug 2：不在窗口内时不推进状态。"""
        from app.engines.fcip_engine import FCIPEngine

        mock_fs = MagicMock()
        mock_fs.list_actionable_for_scene = AsyncMock(return_value=[
            {
                "id": "fs-3",
                "name": "未到窗口的伏笔",
                "status": "planned",
                "bury_window_start": 10,
                "bury_window_end": 15,
                "reveal_window_start": 20,
                "reveal_window_end": 25,
                "secret_spoiler_scope": {"characters": []},
            }
        ])
        mock_fs.list_cognitive_states_for_foreshadowing = AsyncMock(return_value=[])
        mock_fs.transition_state = AsyncMock()

        mock_clue = MagicMock()
        mock_clue.get_evidence_pool = AsyncMock(return_value={"clues": []})

        engine = FCIPEngine(mock_fs)

        with patch("app.engines.fcip_engine._lazy_clue_service", return_value=mock_clue), \
             patch("app.engines.fcip_detector.run_deterministic_checks", return_value=[]):
            result = await engine.post_gen_writeback(
                project_id=_TEST_PROJECT_ID,
                chapter_number=3,  # 不在任何窗口内
                generated_text="测试文本",
                db=MagicMock(),
                apply_writeback=True,
            )

        mock_fs.transition_state.assert_not_called()
        assert result["state_advancements"] == []
