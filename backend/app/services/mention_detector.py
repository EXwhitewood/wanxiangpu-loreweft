from __future__ import annotations

import re


class MentionDetector:
    def build_entity_index(self, project=None, extra_entities: list[dict] | None = None) -> list[dict]:
        entities: list[dict] = []
        core_data = getattr(project, "core_data", None) or {}
        if isinstance(core_data, dict):
            for char in core_data.get("characters", []) or []:
                if not isinstance(char, dict):
                    continue
                name = char.get("name")
                if not name:
                    continue
                aliases = [a for a in char.get("aliases", []) or [] if a]
                entities.append({
                    "entity_type": "character",
                    "entity_id": char.get("id") or name,
                    "name": name,
                    "aliases": aliases,
                })
            for loc in core_data.get("locations", []) or []:
                if not isinstance(loc, dict):
                    continue
                name = loc.get("name")
                if name:
                    entities.append({
                        "entity_type": "location",
                        "entity_id": loc.get("id") or name,
                        "name": name,
                        "aliases": [a for a in loc.get("aliases", []) or [] if a],
                    })
        for entity in extra_entities or []:
            if isinstance(entity, dict) and entity.get("name"):
                entities.append(entity)
        return entities

    def detect(self, text: str, entities: list[dict]) -> list[dict]:
        mentions = []
        seen = set()
        for entity in entities:
            names = [entity.get("name", "")] + list(entity.get("aliases", []) or [])
            for name in [n for n in names if n]:
                for match in re.finditer(re.escape(str(name)), text or ""):
                    key = (entity.get("entity_type"), entity.get("entity_id"), match.start(), match.end())
                    if key in seen:
                        continue
                    seen.add(key)
                    mentions.append({
                        "entity_type": entity.get("entity_type", "unknown"),
                        "entity_id": str(entity.get("entity_id") or entity.get("name") or name),
                        "entity_name": entity.get("name") or name,
                        "alias": name,
                        "start": match.start(),
                        "end": match.end(),
                        "evidence_text": self._snip(text, match.start(), match.end()),
                        "confidence": 0.9 if name == entity.get("name") else 0.75,
                        "status": "candidate",
                    })
        return mentions

    @staticmethod
    def _snip(text: str, start: int, end: int, window: int = 30) -> str:
        return (text[max(0, start - window):min(len(text), end + window)]).replace("\n", " ")
