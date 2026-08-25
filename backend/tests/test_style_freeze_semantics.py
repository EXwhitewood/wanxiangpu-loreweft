from unittest.mock import AsyncMock

import pytest

from app.services.memory_core import CoreMemoryService


@pytest.mark.asyncio
async def test_frozen_ready_style_profile_remains_generation_baseline(monkeypatch):
    service = CoreMemoryService()
    monkeypatch.setattr(
        service,
        "list_style_profiles",
        AsyncMock(return_value=[
            {
                "id": "style-1",
                "name": "Frozen Baseline",
                "status": "ready",
                "active": False,
                "frozen": True,
                "style_prompt": "保持既定风格。",
            }
        ]),
    )

    profile = await service.get_active_style_profile("project-1")

    assert profile is not None
    assert profile["id"] == "style-1"
    assert profile["frozen"] is True


@pytest.mark.asyncio
async def test_disabled_frozen_style_profile_does_not_influence_generation(monkeypatch):
    service = CoreMemoryService()
    monkeypatch.setattr(
        service,
        "list_style_profiles",
        AsyncMock(return_value=[
            {
                "id": "style-1",
                "name": "Disabled Frozen Baseline",
                "status": "ready",
                "active": True,
                "frozen": True,
                "editor_influence_enabled": False,
                "style_prompt": "不应进入生成。",
            }
        ]),
    )

    profile = await service.get_active_style_profile("project-1")

    assert profile is None


def test_normalized_frozen_profile_keeps_active_flag():
    service = CoreMemoryService()

    profile = service._normalize_style_profile({
        "id": "style-1",
        "name": "Frozen Active",
        "status": "ready",
        "active": True,
        "frozen": True,
    })

    assert profile is not None
    assert profile["active"] is True
    assert profile["frozen"] is True


def test_normalized_profile_defaults_editor_influence_enabled():
    service = CoreMemoryService()

    profile = service._normalize_style_profile({
        "id": "style-1",
        "name": "Legacy Style",
        "status": "ready",
    })

    assert profile is not None
    assert profile["editor_influence_enabled"] is True
