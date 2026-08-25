from io import BytesIO

import pytest
from fastapi import UploadFile

from app.api import agents as agents_api
from app.models.api_config import AgentOverride, AppSettings
from app.services import agent_config


def _detail(name: str, api_key: str) -> dict:
    return {
        "name": name,
        "display_name": "测试智能体",
        "description": "用于验证智能体中心契约。",
        "icon": "bot",
        "default_skills": ["core_query"],
        "enabled_skills": ["core_query"],
        "agent_skills": ["test_skill"],
        "enabled_agent_skills": ["test_skill"],
        "persona": "default",
        "active_persona": "default",
        "config": {
            "api_format": "openai_compatible",
            "api_key": api_key,
            "base_url": "https://example.invalid",
            "model": "test-model",
        },
        "has_override": True,
    }


class _FakeManager:
    def __init__(self):
        self.settings = AppSettings(
            agent_overrides={
                "outline_architect": AgentOverride(
                    api_key="test-secret-key-1234567890",
                    agent_skills=["test_skill"],
                )
            }
        )
        self.saved_settings = None

    async def load_settings(self):
        return self.settings

    async def save_settings(self, settings):
        self.saved_settings = settings
        self.settings = settings

    async def get_agent_detail(self, name, _cached_settings=None):
        override = self.settings.agent_overrides.get(name)
        key = override.api_key if override and override.api_key else "test-global-key-1234567890"
        return _detail(name, key)


@pytest.mark.asyncio
async def test_agent_directory_masks_api_keys_without_dropping_skill_bindings(monkeypatch):
    manager = _FakeManager()
    monkeypatch.setattr(agents_api, "AgentConfigManager", lambda: manager)

    result = await agents_api.list_agents()

    assert result
    assert all("****" in item["config"]["api_key"] for item in result)
    assert all("secret" not in item["config"]["api_key"] for item in result)
    assert result[0]["enabled_skills"] == ["core_query"]
    assert result[0]["enabled_agent_skills"] == ["test_skill"]


@pytest.mark.asyncio
async def test_masked_key_update_preserves_secret_and_skill_assignment(monkeypatch):
    manager = _FakeManager()
    monkeypatch.setattr(agents_api, "AgentConfigManager", lambda: manager)

    result = await agents_api.update_agent(
        "outline_architect",
        {
            "api_format": None,
            "api_key": "sk-s****7890",
            "base_url": None,
            "model": None,
            "skills": ["core_query"],
            "agent_skills": ["test_skill", "extra_skill"],
            "persona": None,
        },
    )

    saved = manager.saved_settings.agent_overrides["outline_architect"]
    assert saved.api_key == "test-secret-key-1234567890"
    assert saved.agent_skills == ["test_skill", "extra_skill"]
    masked_key = result["config"]["api_key"]
    assert masked_key.endswith("7890")
    assert "****" in masked_key
    assert "secret" not in masked_key
    assert masked_key != saved.api_key


@pytest.mark.asyncio
async def test_skill_import_invalidates_registry_cache(monkeypatch):
    stored = {}
    invalidations = 0

    async def fake_load():
        return dict(stored)

    async def fake_save(skills):
        stored.clear()
        stored.update(skills)

    def fake_invalidate():
        nonlocal invalidations
        invalidations += 1

    monkeypatch.setattr(agents_api, "_load_custom_skills", fake_load)
    monkeypatch.setattr(agents_api, "_save_custom_skills", fake_save)
    monkeypatch.setattr(agents_api, "_invalidate_skill_registry_cache", fake_invalidate)

    skill_file = UploadFile(
        filename="SKILL.md",
        file=BytesIO(
            b"---\nname: cache_contract_skill\ndescription: cache contract\n---\nPrompt body.\n"
        ),
    )

    result = await agents_api.import_skills(
        files=[skill_file],
        category="agent",
        agent="outline_architect",
    )

    assert result[0]["name"] == "cache_contract_skill"
    assert stored["cache_contract_skill"]["manifest"]["agents"] == ["outline_architect"]
    assert invalidations == 1


@pytest.mark.asyncio
async def test_skill_delete_invalidates_registry_cache(monkeypatch):
    stored = {"removable_skill": {"name": "removable_skill"}}
    invalidations = 0

    async def fake_load():
        return dict(stored)

    async def fake_save(skills):
        stored.clear()
        stored.update(skills)

    def fake_invalidate():
        nonlocal invalidations
        invalidations += 1

    monkeypatch.setattr(agents_api, "_load_custom_skills", fake_load)
    monkeypatch.setattr(agents_api, "_save_custom_skills", fake_save)
    monkeypatch.setattr(agents_api, "_invalidate_skill_registry_cache", fake_invalidate)

    await agents_api.delete_skill("removable_skill")

    assert "removable_skill" not in stored
    assert invalidations == 1


@pytest.mark.asyncio
async def test_agent_api_rejects_invalid_skill_requests_with_http_errors(monkeypatch):
    with pytest.raises(agents_api.HTTPException) as empty_name:
        await agents_api.create_skill({"name": ""})
    assert empty_name.value.status_code == 422

    async def fake_load():
        return {}

    monkeypatch.setattr(agents_api, "_load_custom_skills", fake_load)
    with pytest.raises(agents_api.HTTPException) as missing:
        await agents_api.delete_skill("missing_custom_skill")
    assert missing.value.status_code == 404


@pytest.mark.asyncio
async def test_corrupt_custom_skill_registry_fails_closed(tmp_path, monkeypatch):
    registry_path = tmp_path / "custom_skills.json"
    registry_path.write_text("{not-json", encoding="utf-8")
    monkeypatch.setattr(agent_config, "_CUSTOM_SKILLS_FILE", registry_path)

    with pytest.raises(RuntimeError, match="Cannot load custom Skill registry"):
        await agent_config._load_custom_skills()


@pytest.mark.asyncio
async def test_agent_settings_write_failure_is_not_reported_as_success(tmp_path):
    blocking_parent = tmp_path / "not-a-directory"
    blocking_parent.write_text("file", encoding="utf-8")
    manager = agent_config.AgentConfigManager()
    manager._file_settings_path = blocking_parent / "agent_settings.json"

    with pytest.raises(OSError):
        await manager.save_settings(AppSettings())
