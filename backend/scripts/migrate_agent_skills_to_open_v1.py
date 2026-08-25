"""Migrate builtin legacy SKILL.md frontmatter to open_skill_v1."""
from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1] / "app" / "agent_skills" / "builtin"
ROOT_FIELDS = {"name", "description", "version", "license", "tags"}
EXTENSION_EXCLUDES = ROOT_FIELDS | {"id", "display_name", "x-loreweft"}


def migrate(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return False
    parts = text.split("---", 2)
    if len(parts) != 3:
        return False
    metadata = yaml.safe_load(parts[1]) or {}
    if not isinstance(metadata, dict) or "x-loreweft" in metadata:
        return False

    skill_id = str(metadata.get("id") or path.parent.name)
    display_name = str(
        metadata.get("display_name")
        or metadata.get("name")
        or skill_id
    )
    root = {
        "name": skill_id.replace("_", "-"),
        "description": str(metadata.get("description") or ""),
        "version": str(metadata.get("version") or "1"),
    }
    for key in ("license", "tags"):
        if key in metadata:
            root[key] = metadata[key]

    extension = {
        "id": skill_id,
        "display_name": display_name,
        "format_version": "open_skill_v1",
    }
    for key, value in metadata.items():
        if key not in EXTENSION_EXCLUDES:
            extension[key] = value
    root["x-loreweft"] = extension

    frontmatter = yaml.safe_dump(
        root,
        allow_unicode=True,
        sort_keys=False,
        width=100,
    ).strip()
    body = parts[2].lstrip("\r\n")
    path.write_text(f"---\n{frontmatter}\n---\n\n{body}", encoding="utf-8", newline="\n")
    return True


def main() -> None:
    migrated = [path for path in ROOT.rglob("SKILL.md") if migrate(path)]
    print(f"Migrated {len(migrated)} skills")
    for path in migrated:
        print(path.relative_to(ROOT))


if __name__ == "__main__":
    main()
