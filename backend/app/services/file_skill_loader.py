from __future__ import annotations

from pathlib import Path

import yaml

from app.models.file_skill import FileSkillManifest


class FileSkillLoader:
    def list_project_skills(self, project_root: str | Path) -> list[dict]:
        root = Path(project_root) / ".loreweft" / "skills"
        if not root.exists():
            return []
        skills = []
        for skill_dir in root.iterdir():
            if not skill_dir.is_dir():
                continue
            manifest = self.load_skill(skill_dir)
            if manifest:
                skills.append(manifest.model_dump())
        return skills

    def load_skill(self, skill_dir: str | Path) -> FileSkillManifest | None:
        path = Path(skill_dir)
        skill_md = path / "SKILL.md"
        skill_yaml = path / "skill.yaml"
        if not skill_md.exists():
            return None
        data = {}
        if skill_yaml.exists():
            loaded = yaml.safe_load(skill_yaml.read_text(encoding="utf-8")) or {}
            if isinstance(loaded, dict):
                data.update(loaded)
        data.setdefault("name", path.name)
        data.setdefault("display_name", data["name"])
        data.setdefault("description", self._read_description(skill_md))
        data.setdefault("permission", "readonly")
        data["path"] = str(path)
        return FileSkillManifest(**data)

    @staticmethod
    def _read_description(skill_md: Path) -> str:
        for line in skill_md.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                return line[:200]
        return ""
