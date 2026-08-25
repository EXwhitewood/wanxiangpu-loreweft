from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.agent_skill import AgentSkill, AgentSkillManifest


def test_parse_open_skill_md_preserves_protocol_and_disables_executable_runtime():
    from app.api.agents import _parse_skill_md

    parsed = _parse_skill_md(
        """---
name: portable-skill
description: Portable test skill
version: 2
x-loreweft:
  id: portable_skill
  kind: service
  domain: writing
  validators:
    - ai_flavor
  constraints:
    ai_flavor:
      dash_per_1000_max: 2.5
  runtime:
    service_class: external.module.Service
    service_method: run
---

## Instructions
Follow the contract.
"""
    )

    assert parsed["name"] == "portable_skill"
    assert parsed["manifest"]["format_version"] == "open_skill_v1"
    assert parsed["manifest"]["kind"] == "prompt"
    assert parsed["manifest"]["validators"] == ["ai_flavor"]
    assert parsed["manifest"]["constraints"]["ai_flavor"]["dash_per_1000_max"] == 2.5
    assert parsed["import_warnings"]


def test_import_single_skill_keeps_open_manifest():
    from app.api.agents import _import_single_skill

    custom = {}
    result = _import_single_skill(
        {
            "name": "portable_skill",
            "display_name": "Portable",
            "body": "Instructions",
            "manifest": {
                "kind": "prompt",
                "domain": "writing",
                "validators": ["ai_flavor"],
                "format_version": "open_skill_v1",
                "source_format": "x-loreweft",
            },
        },
        "agent",
        "core_generation",
        custom,
    )

    assert result["manifest"]["agents"] == ["core_generation"]
    assert custom["portable_skill"]["manifest"]["validators"] == ["ai_flavor"]


@pytest.mark.asyncio
async def test_export_custom_skill_returns_open_skill_md():
    from app.api.agents import export_skill

    manifest = AgentSkillManifest(
        id="portable_skill",
        name="Portable",
        display_name="Portable",
        description="Portable test skill",
        format_version="open_skill_v1",
        source_format="x-loreweft",
        source="custom",
        kind="prompt",
        domain="writing",
        validators=["ai_flavor"],
    )
    registry = MagicMock()
    registry.get_manifest = AsyncMock(return_value=manifest)

    with (
        patch("app.services.agent_skill_registry.get_skill_registry", return_value=registry),
        patch(
            "app.services.agent_skill_loader.AgentSkillLoader.load",
            new=AsyncMock(return_value=AgentSkill(manifest=manifest, body="## Instructions\nWrite.")),
        ),
    ):
        response = await export_skill("portable_skill")

    body = response.body.decode("utf-8")
    assert "x-loreweft:" in body
    assert "id: portable_skill" in body
    assert "## Instructions" in body
