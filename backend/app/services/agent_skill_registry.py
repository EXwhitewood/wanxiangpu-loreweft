"""AgentSkillRegistry - unified skill manifest registry (plan 9.1).

Scans builtin SKILL.md files, merges the legacy SKILL_REGISTRY from
agent_config, merges custom skills from Redis/file, merges project-level
.loreweft/skills, and merges worldbuilder sub-agent default skills.

Priority: project_file > custom > builtin_file > legacy.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from app.models.agent_skill import AgentSkillManifest, SkillTrigger, SkillRuntimeBinding

logger = logging.getLogger(__name__)

_BUILTIN_ROOT = Path(__file__).parent.parent / "agent_skills" / "builtin"

_FM = chr(45) * 3


# ---------------------------------------------------------------------------
# YAML front-matter parser
# ---------------------------------------------------------------------------

def _parse_yaml_frontmatter(text: str) -> dict:
    """Parse YAML frontmatter from SKILL.md text."""
    if not text.startswith(_FM):
        return {}
    parts = text.split(_FM, 2)
    if len(parts) < 3:
        return {}
    frontmatter = parts[1].strip()
    try:
        meta = yaml.safe_load(frontmatter) or {}
    except yaml.YAMLError:
        logger.warning("Invalid SKILL.md frontmatter", exc_info=True)
        return {}
    return meta if isinstance(meta, dict) else {}


def _as_list(value: Any) -> list[str]:
    """Normalize YAML scalar/list fields into a clean list of strings."""
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        return [item.strip() for item in text.split(",") if item.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"true", "1", "yes", "on"}


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _build_trigger(meta: dict, agents: list[str]) -> SkillTrigger:
    raw = _as_dict(meta.get("triggers"))
    return SkillTrigger(
        agents=_as_list(raw.get("agents")) or agents,
        domains=_as_list(raw.get("domains")),
        scene_roles=_as_list(raw.get("scene_roles")),
        issue_types=_as_list(raw.get("issue_types")),
        writing_modes=_as_list(raw.get("writing_modes")),
        tabs=_as_list(raw.get("tabs")),
        feature_flags=_as_list(raw.get("feature_flags")),
    )


def _build_runtime(meta: dict, kind: str) -> SkillRuntimeBinding:
    raw = _as_dict(meta.get("runtime"))
    return SkillRuntimeBinding(
        kind=raw.get("kind") or kind,
        service_class=raw.get("service_class"),
        service_method=raw.get("service_method"),
        tool_names=_as_list(raw.get("tool_names")),
        required_context=_as_list(raw.get("required_context")),
        optional_context=_as_list(raw.get("optional_context")),
        min_permission=str(raw.get("min_permission") or "readonly"),
        execution_phase=str(raw.get("execution_phase") or "pre_generation"),
        timeout_seconds=float(raw.get("timeout_seconds") or 30.0),
        output_char_budget=int(raw.get("output_char_budget") or 4000),
        cache_enabled=bool(raw.get("cache_enabled", False)),
    )


def _infer_category(meta: dict, kind: str, domain: str) -> str:
    category = meta.get("category")
    if category:
        return str(category)
    if kind in {"service", "tool"}:
        return "utility"
    if domain in {"writing", "editor", "fbi", "outline", "worldbuilder"}:
        return "agent"
    return "utility"


def _runtime_meta(meta: dict) -> tuple[dict, str, str]:
    """Return Loreweft runtime metadata and its source format."""
    extension = _as_dict(meta.get("x-loreweft"))
    if extension:
        return extension, str(extension.get("format_version") or "open_skill_v1"), "x-loreweft"
    return meta, "legacy", "legacy_root"


def _extract_body(text: str) -> str:
    """Extract the Markdown body after YAML frontmatter."""
    if not text.startswith(_FM):
        return text
    parts = text.split(_FM, 2)
    if len(parts) < 3:
        return text
    return parts[2].strip()


# ---------------------------------------------------------------------------
# Builtin SKILL.md scanner
# ---------------------------------------------------------------------------

def _scan_builtin_skills() -> list[AgentSkillManifest]:
    """Walk _BUILTIN_ROOT looking for SKILL.md files, parse frontmatter,
    create AgentSkillManifest objects."""
    manifests: list[AgentSkillManifest] = []
    if not _BUILTIN_ROOT.exists():
        logger.warning("Builtin skills root not found: %s", _BUILTIN_ROOT)
        return manifests

    for skill_md in _BUILTIN_ROOT.rglob("SKILL.md"):
        try:
            text = skill_md.read_text(encoding="utf-8")
        except OSError:
            logger.warning("Failed to read builtin skill: %s", skill_md)
            continue

        meta = _parse_yaml_frontmatter(text)
        if not meta:
            continue

        runtime_meta, format_version, source_format = _runtime_meta(meta)

        skill_id = runtime_meta.get("id") or meta.get("id") or skill_md.parent.name
        name = meta.get("name") or runtime_meta.get("name") or skill_id
        display_name = runtime_meta.get("display_name") or meta.get("display_name") or name
        description = meta.get("description") or runtime_meta.get("description", "")

        kind = runtime_meta.get("kind", "prompt")
        domain = runtime_meta.get("domain", "general")
        agents = _as_list(runtime_meta.get("agents"))

        manifest = AgentSkillManifest(
            id=skill_id,
            name=name,
            display_name=display_name,
            description=description,
            version=str(meta.get("version", "1")),
            format_version=format_version,
            source_format=source_format,
            source="builtin",
            kind=kind,
            domain=domain,
            category=_infer_category(runtime_meta, kind, domain),
            agents=agents,
            priority=_as_int(runtime_meta.get("priority"), 50),
            enabled_by_default=_as_bool(runtime_meta.get("enabled_by_default")),
            triggers=_build_trigger(runtime_meta, agents),
            runtime=_build_runtime(runtime_meta, kind),
            constraints=_as_dict(runtime_meta.get("constraints")),
            validators=_as_list(runtime_meta.get("validators")),
            validation_contracts=_as_dict(runtime_meta.get("validation_contracts")),
            repair_strategies=_as_list(runtime_meta.get("repair_strategies")),
            repair_hooks=_as_list(runtime_meta.get("repair_hooks")),
            reference_files=_as_list(runtime_meta.get("references")),
            resource_pack_files=_as_list(runtime_meta.get("resource_packs")),
            path=str(skill_md.parent),
        )
        manifests.append(manifest)

    return manifests


# ---------------------------------------------------------------------------
# Legacy SKILL_REGISTRY merger
# ---------------------------------------------------------------------------

def _merge_legacy_registry(manifests: dict[str, AgentSkillManifest]) -> None:
    """Merge from agent_config.SKILL_REGISTRY, only add if id not already present."""
    try:
        from app.services.agent_config import SKILL_REGISTRY
    except ImportError:
        return

    for key, skill in SKILL_REGISTRY.items():
        if key in manifests:
            continue
        name = skill.get("name", key)
        display_name = skill.get("display_name", name)
        description = skill.get("description", "")
        category = skill.get("category", "utility")
        agent_name = skill.get("agent", "")
        agents: list[str] = [agent_name] if agent_name else []

        manifests[key] = AgentSkillManifest(
            id=key,
            name=display_name,
            description=description,
            version="1",
            source="builtin",
            kind="prompt",
            domain="general",
            category=category,
            agents=agents,
            priority=50,
            enabled_by_default=False,
            triggers=SkillTrigger(agents=agents),
            runtime=SkillRuntimeBinding(),
            path="",
        )


def _append_unique(values: list[str], item: str) -> None:
    if item and item not in values:
        values.append(item)


def _augment_agent_availability(manifests: dict[str, AgentSkillManifest]) -> None:
    """Make manifest agent filters honor agent_config default assignments."""
    try:
        from app.services.agent_config import AGENT_REGISTRY, _load_runtime_agent_defaults
    except ImportError:
        return

    runtime_defaults = _load_runtime_agent_defaults()
    agent_names = list(dict.fromkeys(list(AGENT_REGISTRY.keys()) + list(runtime_defaults.keys())))
    for agent_name in agent_names:
        config = AGENT_REGISTRY.get(agent_name, {})
        skill_ids = list(config.get("default_skills", [])) + list(config.get("agent_skills", []))
        runtime_config = runtime_defaults.get(agent_name, {})
        skill_ids.extend(runtime_config.get("utility", []))
        skill_ids.extend(runtime_config.get("agent", []))

        for skill_id in dict.fromkeys(skill_ids):
            manifest = manifests.get(skill_id)
            if manifest is None:
                continue
            _append_unique(manifest.agents, agent_name)
            _append_unique(manifest.triggers.agents, agent_name)


# ---------------------------------------------------------------------------
# Custom skills merger
# ---------------------------------------------------------------------------

async def _merge_custom_skills(manifests: dict[str, AgentSkillManifest]) -> None:
    """Load from _load_custom_skills(), set source="custom"."""
    try:
        from app.services.agent_config import _load_custom_skills
    except ImportError:
        return

    try:
        custom = await _load_custom_skills()
    except Exception:
        logger.warning("Failed to load custom skills", exc_info=True)
        return

    for key, skill in custom.items():
        if key in manifests:
            continue
        extension = _as_dict(skill.get("manifest"))
        name = extension.get("name") or skill.get("name", key)
        display_name = (
            extension.get("display_name")
            or skill.get("display_name")
            or name
        )
        description = extension.get("description") or skill.get("description", "")
        kind = str(extension.get("kind") or "prompt")
        domain = str(extension.get("domain") or "general")
        category = str(extension.get("category") or skill.get("category", "utility"))
        agent_name = skill.get("agent", "")
        agents = _as_list(extension.get("agents"))
        if agent_name:
            _append_unique(agents, agent_name)

        manifests[key] = AgentSkillManifest(
            id=key,
            name=display_name,
            display_name=display_name,
            description=description,
            version=str(extension.get("version") or "1"),
            format_version=str(extension.get("format_version") or "open_skill_v1"),
            source_format=str(extension.get("source_format") or "x-loreweft"),
            source="custom",
            kind=kind,
            domain=domain,
            category=category,
            agents=agents,
            priority=int(extension.get("priority") or 60),
            enabled_by_default=bool(extension.get("enabled_by_default", False)),
            triggers=_build_trigger(extension, agents),
            runtime=_build_runtime(extension, kind),
            constraints=_as_dict(extension.get("constraints")),
            validators=_as_list(extension.get("validators")),
            repair_strategies=_as_list(extension.get("repair_strategies")),
            repair_hooks=_as_list(extension.get("repair_hooks")),
            reference_files=_as_list(extension.get("references")),
            resource_pack_files=_as_list(extension.get("resource_packs")),
            path="",
        )


# ---------------------------------------------------------------------------
# Project skills merger
# ---------------------------------------------------------------------------

def _merge_project_skills(manifests: dict[str, AgentSkillManifest], project_root: str | Path | None) -> None:
    """Scan .loreweft/skills/ for SKILL.md files."""
    if project_root is None:
        return

    try:
        from app.services.file_skill_loader import FileSkillLoader
    except ImportError:
        return

    try:
        loader = FileSkillLoader()
        project_skills = loader.list_project_skills(project_root)
    except Exception:
        logger.warning("Failed to load project skills", exc_info=True)
        return

    for skill in project_skills:
        skill_id = skill.get("name", "")
        if not skill_id:
            continue
        # project_file has highest priority, always overwrite
        manifests[skill_id] = AgentSkillManifest(
            id=skill_id,
            name=skill.get("display_name", skill_id),
            description=skill.get("description", ""),
            version=skill.get("version", "1"),
            source="project_file",
            kind="prompt",
            domain="general",
            category=skill.get("category", "utility"),
            agents=[],
            priority=70,
            enabled_by_default=True,
            triggers=SkillTrigger(),
            runtime=SkillRuntimeBinding(),
            path=skill.get("path", ""),
        )


# ---------------------------------------------------------------------------
# Worldbuilder sub-agent skills merger
# ---------------------------------------------------------------------------

_WORLDBUILDER_DEFAULT_SKILLS = [
    ("consistency_check", "Consistency check for worldbuilding data"),
    ("completeness_check", "Completeness check for worldbuilding coverage"),
    ("derivation_chain_validation", "Validate derivation chains in worldbuilding"),
]


def _merge_worldbuilder_skills(manifests: dict[str, AgentSkillManifest]) -> None:
    """Add consistency_check, completeness_check, derivation_chain_validation if missing."""
    for skill_name, description in _WORLDBUILDER_DEFAULT_SKILLS:
        if skill_name in manifests:
            continue
        manifests[skill_name] = AgentSkillManifest(
            id=skill_name,
            name=skill_name,
            description=description,
            version="1",
            source="worldbuilder_sub_agent",
            kind="service",
            domain="worldbuilding",
            category="agent",
            agents=["worldbuilder"],
            priority=55,
            enabled_by_default=True,
            triggers=SkillTrigger(agents=["worldbuilder"]),
            runtime=SkillRuntimeBinding(kind="service"),
            path="",
        )

    # Also load from worldbuilder module if available
    try:
        from app.agents.worldbuilder import SUB_AGENT_DEFAULT_SKILLS
    except ImportError:
        return

    for tab, skill_names in SUB_AGENT_DEFAULT_SKILLS.items():
        agent_name = f"wb_{tab}"
        for skill_name in skill_names:
            unique_id = f"wb_{tab}__{skill_name}"
            if unique_id in manifests:
                continue
            manifests[unique_id] = AgentSkillManifest(
                id=unique_id,
                name=skill_name,
                description=f"Worldbuilder sub-agent ({tab}) default skill: {skill_name}",
                version="1",
                source="worldbuilder_sub_agent",
                kind="prompt",
                domain="worldbuilding",
                category="agent",
                agents=[agent_name],
                priority=55,
                enabled_by_default=True,
                triggers=SkillTrigger(agents=[agent_name]),
                runtime=SkillRuntimeBinding(),
                path="",
            )


# ---------------------------------------------------------------------------
# Main registry class
# ---------------------------------------------------------------------------

class AgentSkillRegistry:
    """Unified skill registry that merges skills from all sources.

    Priority order (highest to lowest):
        project_file > custom > builtin_file > legacy

    When duplicate IDs are found, the higher-priority source wins.
    """

    def __init__(self) -> None:
        self._cache: dict[str, AgentSkillManifest] | None = None

    async def list_manifests(
        self,
        agent_name: str | None = None,
        project_root: str | None = None,
        project_id: str | None = None,
        db=None,
    ) -> list[AgentSkillManifest]:
        """Return the full deduplicated manifest list, optionally filtered by agent_name."""
        manifests = await self._load_all(project_root)

        if agent_name:
            manifests = [
                m for m in manifests
                if agent_name in m.agents or agent_name in m.triggers.agents or not m.agents
            ]

        return manifests

    async def get_manifest(self, skill_id: str, **scope) -> AgentSkillManifest | None:
        """Look up a single manifest by skill id."""
        manifests = await self._load_all(scope.get("project_root"))
        for m in manifests:
            if m.id == skill_id:
                return m
        return None

    def invalidate_cache(self) -> None:
        """Clear the in-memory cache so next call re-scans."""
        self._cache = None

    async def _load_all(self, project_root: str | None = None) -> list[AgentSkillManifest]:
        """Load and merge skills from all sources."""
        if self._cache is not None:
            return list(self._cache.values())

        by_id: dict[str, AgentSkillManifest] = {}

        # 1. Builtin SKILL.md files (source=builtin)
        for m in _scan_builtin_skills():
            by_id[m.id] = m

        # 2. Legacy SKILL_REGISTRY - only add if id not already present
        _merge_legacy_registry(by_id)
        _augment_agent_availability(by_id)

        # 3. Worldbuilder sub-agent skills - add if missing
        _merge_worldbuilder_skills(by_id)

        # 4. Custom skills - only add if id not already present
        await _merge_custom_skills(by_id)

        # 5. Project .loreweft/skills - highest priority, always overwrite
        _merge_project_skills(by_id, project_root)

        self._cache = by_id
        return sorted(by_id.values(), key=lambda m: (-m.priority, m.id))


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_registry: AgentSkillRegistry | None = None


def get_registry() -> AgentSkillRegistry:
    """Return the module-level registry singleton."""
    global _registry
    if _registry is None:
        _registry = AgentSkillRegistry()
    return _registry


def get_skill_registry() -> AgentSkillRegistry:
    """Alias for get_registry()."""
    return get_registry()
