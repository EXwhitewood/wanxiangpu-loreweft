"""AgentSkillLoader - loads full skill body from SKILL.md (plan 9.2)."""
from __future__ import annotations

import logging
from pathlib import Path

import yaml

from app.models.agent_skill import AgentSkill, AgentSkillManifest

logger = logging.getLogger(__name__)

_FM = chr(45) * 3


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


def _extract_body(text: str) -> str:
    """Extract the Markdown body after YAML frontmatter."""
    if not text.startswith(_FM):
        return text
    parts = text.split(_FM, 2)
    if len(parts) < 3:
        return text
    return parts[2].strip()


def _extract_prompt_sections(body: str) -> dict[str, str]:
    """Extract prompt sections by looking for ## headings in the body."""
    sections: dict[str, str] = {}
    current_heading: str | None = None
    current_lines: list[str] = []

    for line in body.split("\n"):
        if line.startswith("## "):
            if current_heading is not None:
                sections[current_heading] = "\n".join(current_lines).strip()
            current_heading = line[3:].strip()
            current_lines = []
        else:
            current_lines.append(line)

    if current_heading is not None:
        sections[current_heading] = "\n".join(current_lines).strip()

    return sections


class AgentSkillLoader:
    """Loads full skill body from SKILL.md files based on manifest."""

    async def load(self, manifest: AgentSkillManifest) -> AgentSkill:
        """Load a single skill from its manifest path."""
        if not manifest.path:
            body = await self._load_custom_body(manifest) if manifest.source == "custom" else ""
            return AgentSkill(
                manifest=manifest,
                body=body,
                prompt_sections=_extract_prompt_sections(body) if body else {},
            )

        skill_dir = Path(manifest.path)
        skill_md = skill_dir / "SKILL.md"

        body = ""
        prompt_sections: dict[str, str] = {}
        references: list[str] = []
        resource_packs: dict[str, dict] = {}

        if skill_md.exists():
            try:
                text = skill_md.read_text(encoding="utf-8")
                body = _extract_body(text)
                prompt_sections = _extract_prompt_sections(body)
            except OSError:
                logger.warning("Cannot read skill body: %s", skill_md)

        # Backward compatibility: top-level Markdown files remain prompt sections.
        if skill_dir.exists() and skill_dir.is_dir():
            for section_file in skill_dir.iterdir():
                if not section_file.is_file():
                    continue
                name = section_file.name.lower()
                if name == "skill.md":
                    continue
                if name.endswith(".md"):
                    try:
                        content = section_file.read_text(encoding="utf-8")
                        section_key = section_file.stem
                        if section_key not in prompt_sections:
                            prompt_sections[section_key] = content
                    except OSError:
                        pass

        for relative_path in manifest.reference_files:
            resolved = self._resolve_declared_resource(skill_dir, relative_path)
            if resolved is None or resolved.suffix.lower() != ".md":
                continue
            try:
                references.append(resolved.read_text(encoding="utf-8"))
            except OSError:
                logger.warning("Cannot read skill reference: %s", resolved)

        for relative_path in manifest.resource_pack_files:
            resolved = self._resolve_declared_resource(skill_dir, relative_path)
            if resolved is None or resolved.suffix.lower() not in {".yaml", ".yml"}:
                continue
            try:
                loaded = yaml.safe_load(resolved.read_text(encoding="utf-8")) or {}
            except (OSError, yaml.YAMLError):
                logger.warning("Cannot read skill resource pack: %s", resolved, exc_info=True)
                continue
            if isinstance(loaded, dict):
                resource_packs[resolved.stem] = loaded

        return AgentSkill(
            manifest=manifest,
            body=body,
            prompt_sections=prompt_sections,
            references=references,
            resource_packs=resource_packs,
        )

    @staticmethod
    def _resolve_declared_resource(skill_dir: Path, relative_path: str) -> Path | None:
        """Resolve a declared package resource without allowing path traversal."""
        candidate = (skill_dir / str(relative_path)).resolve()
        try:
            candidate.relative_to(skill_dir.resolve())
        except ValueError:
            logger.warning("Rejected skill resource outside package: %s", relative_path)
            return None
        return candidate if candidate.is_file() else None

    @staticmethod
    async def _load_custom_body(manifest: AgentSkillManifest) -> str:
        """Load body text for legacy/imported custom skills stored in settings."""
        try:
            from app.services.agent_config import _load_custom_skills

            custom = await _load_custom_skills()
        except Exception:
            logger.warning("Cannot load custom skill body: %s", manifest.id, exc_info=True)
            return ""

        data = custom.get(manifest.id, {})
        if not isinstance(data, dict):
            return ""
        return str(data.get("body") or data.get("detail") or data.get("persona") or "")

    async def load_many(self, manifests: list[AgentSkillManifest]) -> list[AgentSkill]:
        """Load multiple skills from their manifests."""
        skills: list[AgentSkill] = []
        for manifest in manifests:
            skill = await self.load(manifest)
            skills.append(skill)
        return skills
