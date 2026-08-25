"""方案 1：Core-Shell 记忆宫殿落地测试。

验证：
1. CoreShellService.get_full_core 不裁剪：人物/规则/地点全量返回
2. WorldviewDigestService.get_digest 不再截断 description/desire/arc
3. 伏笔状态分层注入：active 全量、resolved 摘要、aborted 不注入
4. 节省模式 get_compact_core 仅保留 critical/high 规则和精简人物
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.core_shell_service import (
    CoreShellService,
    FORESHADOWING_ACTIVE_STATES,
    FORESHADOWING_RESOLVED_STATES,
    FORESHADOWING_SKIPPED_STATES,
)
from app.services.worldview_digest import WorldviewDigestService


class _FakeCore:
    """伪造的 CoreMemoryService，绕过数据库。"""

    def __init__(
        self,
        rules: list[dict] | None = None,
        characters: list | None = None,
        locations: list[dict] | None = None,
        foreshadowing: list[dict] | None = None,
    ) -> None:
        self._rules = rules or []
        self._characters = characters or []
        self._locations = locations or []
        self._foreshadowing = foreshadowing or []

    async def list_world_rules(self, project_id: str) -> list[dict]:
        return list(self._rules)

    async def list_characters(self, project_id: str) -> list:
        return list(self._characters)

    async def list_locations(self, project_id: str) -> list[dict]:
        return list(self._locations)

    async def list_foreshadowing(
        self, project_id: str, include_archived: bool = False
    ) -> list[dict]:
        # 方案 11 Part A2：模拟状态分层
        active_states = ("planned", "active", "dormant", "revealing", "revised")
        if include_archived:
            allowed = active_states + ("resolved",)
        else:
            allowed = active_states
        return [f for f in self._foreshadowing if f.get("status") in allowed]


class _FakeShell:
    """伪造的 ShellMemoryService。"""

    def __init__(self, seeds: list[dict] | None = None) -> None:
        self._seeds = seeds or []

    async def get_seeds_by_tier(self, project_id: str, tier=None) -> list[dict]:
        return list(self._seeds)


def _make_service(
    rules: list[dict] | None = None,
    characters: list | None = None,
    locations: list[dict] | None = None,
    foreshadowing: list[dict] | None = None,
    seeds: list[dict] | None = None,
) -> CoreShellService:
    core = _FakeCore(rules=rules, characters=characters, locations=locations, foreshadowing=foreshadowing)
    shell = _FakeShell(seeds=seeds)
    return CoreShellService(core_service=core, shell_service=shell)


# --------------------------------------------------------------------- Core

@pytest.mark.asyncio
async def test_get_full_core_returns_all_characters_without_slicing():
    """方案 1：get_full_core 必须返回全部人物，不做 [:8] 切片。"""
    def _make_char(i: int):
        return SimpleNamespace(model_dump=lambda i=i: {"name": f"角色{i}", "desire": f"欲望{i}" * 10, "arc": f"弧光{i}" * 5})
    characters = [_make_char(i) for i in range(20)]
    service = _make_service(characters=characters)

    core = await service.get_full_core("p1")

    assert len(core["characters"]) == 20
    assert core["characters"][0]["name"] == "角色0"
    assert core["characters"][19]["name"] == "角色19"


@pytest.mark.asyncio
async def test_get_full_core_returns_all_rules_without_slicing():
    """方案 1：get_full_core 必须返回全部规则，不做 [:5]/[:4]/[:3] 切片。"""
    rules = [
        {"name": f"规则{i}", "description": f"描述{i}" * 20, "priority": "critical", "category": "magic", "constraints": [f"约束{j}" for j in range(5)]}
        for i in range(15)
    ]
    service = _make_service(rules=rules)

    core = await service.get_full_core("p1")

    assert len(core["world_rules"]) == 15
    # 约束也不应被裁剪
    assert len(core["world_rules"][0]["constraints"]) == 5


@pytest.mark.asyncio
async def test_get_full_core_returns_all_locations_without_slicing():
    """方案 1：get_full_core 必须返回全部地点，不做 [:6] 切片。"""
    locations = [{"name": f"地点{i}", "parent_location": f"父{i}", "atmosphere": f"氛围{i}" * 3} for i in range(10)]
    service = _make_service(locations=locations)

    core = await service.get_full_core("p1")

    assert len(core["locations"]) == 10


# ------------------------------------------------------------- 伏笔状态分层

@pytest.mark.asyncio
async def test_foreshadowing_layering_active_fully_injected():
    """方案 1 步骤 3：active 类伏笔全量注入。"""
    foreshadowing = [
        {"name": f"伏笔{i}", "status": status, "secret": f"秘密{i}", "bury_window_start": 1, "reveal_window_start": 10}
        for i, status in enumerate(FORESHADOWING_ACTIVE_STATES)
    ]
    service = _make_service(foreshadowing=foreshadowing)

    core = await service.get_full_core("p1")

    layered = core["foreshadowing"]
    assert len(layered["active"]) == len(FORESHADOWING_ACTIVE_STATES)
    # active 保留所有字段
    assert layered["active"][0]["secret"] == "秘密0"
    assert layered["resolved"] == []


@pytest.mark.asyncio
async def test_foreshadowing_layering_resolved_summary_only():
    """方案 1 步骤 3：resolved 类伏笔只保留摘要字段。"""
    foreshadowing = [
        {
            "name": "已揭示伏笔",
            "status": "resolved",
            "secret": "不应注入的完整秘密",
            "resolution_chapter": 15,
            "resolution_text": "揭示结果摘要",
            "reveal_window_end": 16,
            "reveal_text": "备选揭示文本",
        }
    ]
    service = _make_service(foreshadowing=foreshadowing)

    core = await service.get_full_core("p1")

    layered = core["foreshadowing"]
    assert layered["active"] == []
    assert len(layered["resolved"]) == 1
    resolved = layered["resolved"][0]
    assert resolved["name"] == "已揭示伏笔"
    assert resolved["resolution_chapter"] == 15
    assert resolved["resolution_summary"] == "揭示结果摘要"
    # secret 不应注入
    assert "secret" not in resolved


@pytest.mark.asyncio
async def test_foreshadowing_layering_aborted_not_injected():
    """方案 1 步骤 3：aborted 类伏笔不注入。"""
    foreshadowing = [
        {"name": "已中止伏笔", "status": "aborted", "secret": "不应出现"}
    ]
    service = _make_service(foreshadowing=foreshadowing)

    core = await service.get_full_core("p1")

    layered = core["foreshadowing"]
    assert layered["active"] == []
    assert layered["resolved"] == []


# --------------------------------------------------------------- 节省模式

@pytest.mark.asyncio
async def test_get_compact_core_keeps_only_critical_and_high_rules():
    """方案 1：节省模式仅保留 critical/high 规则。"""
    rules = [
        {"name": "铁则", "priority": "critical", "category": "magic", "description": "d1", "constraints": []},
        {"name": "高优", "priority": "high", "category": "society", "description": "d2", "constraints": []},
        {"name": "普通", "priority": "normal", "category": "history", "description": "d3", "constraints": []},
    ]
    service = _make_service(rules=rules)

    core = await service.get_compact_core("p1")

    priorities = {r["priority"] for r in core["world_rules"]}
    assert priorities == {"critical", "high"}


@pytest.mark.asyncio
async def test_get_compact_core_keeps_only_active_foreshadowing():
    """方案 1：节省模式只注入活跃态伏笔。"""
    foreshadowing = [
        {"name": "活跃", "status": "active", "bury_window_start": 1, "reveal_window_start": 10},
        {"name": "已揭示", "status": "resolved", "resolution_chapter": 5, "resolution_text": "r"},
        {"name": "已中止", "status": "aborted"},
    ]
    service = _make_service(foreshadowing=foreshadowing)

    core = await service.get_compact_core("p1")

    layered = core["foreshadowing"]
    # 节省模式只保留活跃态
    assert len(layered["active"]) == 1
    assert layered["active"][0]["name"] == "活跃"
    # 节省模式不注入 resolved（active 集合已经过滤掉了 resolved）
    assert layered["resolved"] == []


# ----------------------------------------------- WorldviewDigestService 不裁剪

@pytest.mark.asyncio
async def test_worldview_digest_get_digest_does_not_truncate_description():
    """方案 1：get_digest 不再截断 description 到 80/100 字符。"""
    long_description = "这是一段非常非常非常长的规则描述，" * 20  # 远超 100 字符
    rules = [
        {"name": "铁则1", "priority": "critical", "description": long_description, "constraints": []},
        {"name": "能力1", "priority": "high", "category": "magic", "description": long_description, "constraints": []},
        {"name": "社会1", "priority": "normal", "category": "society", "description": long_description, "constraints": []},
        {"name": "历史1", "priority": "normal", "category": "history", "description": long_description, "constraints": []},
    ]
    service = _make_service(rules=rules)
    digest_service = WorldviewDigestService(core_shell=service)

    result = await digest_service.get_digest("p1")

    # 完整 description 必须出现在结果中
    assert long_description in result
    # 至少出现 4 次（critical + power + society + history）
    assert result.count(long_description) == 4


@pytest.mark.asyncio
async def test_worldview_digest_get_digest_does_not_slice_characters():
    """方案 1：get_digest 不再做 characters[:8] 切片。"""
    characters = [
        SimpleNamespace(model_dump=lambda i=i: {"name": f"角色{i}", "desire": f"欲望{i}", "arc": f"弧光{i}"})
        for i in range(20)
    ]
    service = _make_service(characters=characters)
    digest_service = WorldviewDigestService(core_shell=service)

    result = await digest_service.get_digest("p1")

    # 20 个人物都要出现
    for i in range(20):
        assert f"角色{i}" in result


@pytest.mark.asyncio
async def test_worldview_digest_get_digest_does_not_truncate_desire_and_arc():
    """方案 1：get_digest 不再截断 desire[:30] / arc[:20]。"""
    long_desire = "这是非常长的人物欲望描述文本" * 5  # 60+ 字符
    long_arc = "这是非常长的人物弧光描述文本" * 3  # 36+ 字符
    characters = [
        SimpleNamespace(model_dump=lambda: {"name": "凤溪", "desire": long_desire, "arc": long_arc})
    ]
    service = _make_service(characters=characters)
    digest_service = WorldviewDigestService(core_shell=service)

    result = await digest_service.get_digest("p1")

    assert long_desire in result
    assert long_arc in result


@pytest.mark.asyncio
async def test_worldview_digest_get_chapter_context_does_not_truncate_atmosphere():
    """方案 1：get_chapter_context 不再截断 atmosphere[:8]。"""
    long_atmosphere = "非常非常长的氛围描述" * 5  # 45+ 字符
    locations = [{"name": "溪边", "parent_location": "山脉", "atmosphere": long_atmosphere}]
    service = _make_service(locations=locations)
    digest_service = WorldviewDigestService(core_shell=service)

    result = await digest_service.get_chapter_context("p1", 5)

    assert long_atmosphere in result


@pytest.mark.asyncio
async def test_worldview_digest_get_chapter_context_does_not_slice_locations():
    """方案 1：get_chapter_context 不再做 locations[:5] 切片。"""
    locations = [{"name": f"地点{i}", "parent_location": "", "atmosphere": "氛围"} for i in range(10)]
    service = _make_service(locations=locations)
    digest_service = WorldviewDigestService(core_shell=service)

    result = await digest_service.get_chapter_context("p1", 5)

    for i in range(10):
        assert f"地点{i}" in result


# -------------------------------------------------------- 伏笔追踪分层注入

@pytest.mark.asyncio
async def test_worldview_digest_tracks_active_and_resolved_foreshadowing_separately():
    """方案 1 步骤 3：get_digest 区分 active 全量注入和 resolved 摘要注入。"""
    foreshadowing = [
        {"name": "活跃伏笔", "status": "active", "secret": "完整秘密", "reveal_window_start": 10},
        {"name": "已揭示伏笔", "status": "resolved", "resolution_chapter": 5, "resolution_text": "揭示结果"},
        {"name": "已中止伏笔", "status": "aborted", "secret": "不应出现"},
    ]
    service = _make_service(foreshadowing=foreshadowing)
    digest_service = WorldviewDigestService(core_shell=service)

    result = await digest_service.get_digest("p1")

    assert "活跃伏笔" in result
    assert "已揭示伏笔" in result
    assert "揭示结果" in result
    # 已中止的不应注入
    assert "已中止伏笔" not in result


@pytest.mark.asyncio
async def test_worldview_digest_does_not_inject_aborted_foreshadowing():
    """方案 1 步骤 3：aborted 伏笔不出现在 digest 中。"""
    foreshadowing = [
        {"name": "不应出现的伏笔", "status": "aborted", "secret": "完整秘密"}
    ]
    service = _make_service(foreshadowing=foreshadowing)
    digest_service = WorldviewDigestService(core_shell=service)

    result = await digest_service.get_digest("p1")

    assert "不应出现的伏笔" not in result
    assert "完整秘密" not in result
