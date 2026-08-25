from __future__ import annotations

from collections.abc import Mapping


_HIDDEN_KEYS = {
    "truth_snapshot",
    "scene_truth_snapshot",
    "core_facts",
    "future_outline",
    "secret_canonical_statement",
    "secret_truth_type",
    "secret_spoiler_scope",
    "forbidden_future_concepts",
    "api_key",
    "authorization",
    "headers",
}


class ExperienceContextBuilder:
    """Build a compact context for experience and literary-quality planning."""

    def build_for_scene(
        self,
        *,
        project_id: str,
        project=None,
        chapter_number: int = 0,
        scene_index: int = 0,
        scene_beat: dict | None = None,
        story_state: dict | None = None,
        scene_contract: dict | None = None,
        prepared: dict | None = None,
        writing_mode_profile=None,
        quality_memory: dict | None = None,
    ) -> dict:
        scene_beat = scene_beat or {}
        scene_contract = scene_contract or {}
        prepared = prepared or {}

        return {
            "schema_version": 1,
            "project_id": str(project_id),
            "chapter_number": int(chapter_number or 0),
            "scene_index": int(scene_index or 0),
            "genre": str(getattr(project, "genre", "") or "")[:80],
            "project_description": str(getattr(project, "description", "") or "")[:500],
            "scene_beat": self._safe_copy(scene_beat, max_text=400),
            "scene_contract": self._safe_copy(scene_contract, max_text=500),
            "chapter_state": self._safe_copy((scene_contract or {}).get("chapter_state", {}) or {}, max_text=300),
            "story_position": self._story_position(story_state or {}, chapter_number, scene_index),
            "character_count": len(prepared.get("character_cards", []) or []),
            "location_count": len(prepared.get("location_cards", []) or []),
            "style_context": self._style_summary(prepared),
            "writing_mode_profile": (
                writing_mode_profile.model_dump()
                if hasattr(writing_mode_profile, "model_dump")
                else dict(writing_mode_profile or {})
            ),
            "quality_memory": self._quality_memory_summary(quality_memory or {}),
        }

    @classmethod
    def assert_no_hidden_keys(cls, context: dict) -> None:
        leaked = cls._find_hidden_keys(context)
        if leaked:
            raise ValueError(f"Experience context contains hidden keys: {', '.join(sorted(leaked))}")

    @classmethod
    def _find_hidden_keys(cls, value) -> set[str]:
        found: set[str] = set()
        if isinstance(value, Mapping):
            for key, child in value.items():
                key_str = str(key)
                if key_str in _HIDDEN_KEYS:
                    found.add(key_str)
                found |= cls._find_hidden_keys(child)
        elif isinstance(value, list):
            for child in value:
                found |= cls._find_hidden_keys(child)
        return found

    @classmethod
    def _safe_copy(cls, value, *, max_text: int):
        if isinstance(value, Mapping):
            result = {}
            for key, child in value.items():
                key_str = str(key)
                if key_str in _HIDDEN_KEYS:
                    continue
                if key_str in {"raw_outline", "full_outline", "future_chapters"}:
                    continue
                result[key_str] = cls._safe_copy(child, max_text=max_text)
            return result
        if isinstance(value, list):
            return [cls._safe_copy(item, max_text=max_text) for item in value[:12]]
        if isinstance(value, str):
            return value[:max_text]
        return value

    @staticmethod
    def _story_position(story_state: dict, chapter_number: int, scene_index: int) -> dict:
        return {
            "chapter_number": int(chapter_number or story_state.get("active_chapter") or 0),
            "scene_index": int(scene_index or story_state.get("active_scene") or 0),
            "pov_character": str(story_state.get("pov_character", ""))[:80],
        }

    @classmethod
    def _style_summary(cls, prepared: dict) -> dict:
        style_profile = prepared.get("style_profile") or {}
        if not isinstance(style_profile, Mapping):
            style_profile = {}
        style_embedding = prepared.get("style_embedding") or {}
        if not isinstance(style_embedding, Mapping):
            style_embedding = {}
        persona_card = prepared.get("persona_card") or {}
        if not isinstance(persona_card, Mapping):
            persona_card = {}
        return {
            "style_prompt": str(prepared.get("style_prompt") or "")[:500],
            "style_embedding": cls._safe_copy(style_embedding, max_text=120),
            "persona_card": cls._safe_copy(persona_card, max_text=160),
            "profile_id": str(style_profile.get("id") or style_profile.get("profile_id") or "")[:80],
            "hard_rules": cls._safe_copy(style_profile.get("hard_rules", []), max_text=160),
        }

    @staticmethod
    def _quality_memory_summary(quality_memory: dict) -> dict:
        patterns = quality_memory.get("recent_quality_patterns", [])
        if not isinstance(patterns, list):
            patterns = []
        mode_history = quality_memory.get("mode_fit_history", [])
        if not isinstance(mode_history, list):
            mode_history = []
        return {
            "recent_quality_patterns": [
                {
                    "type": str(item.get("type", ""))[:80],
                    "count": int(item.get("count", 0) or 0),
                    "suggestion": str(item.get("suggestion", ""))[:180],
                }
                for item in patterns[:8]
                if isinstance(item, Mapping)
            ],
            "mode_fit_history": [
                {
                    "writing_mode_id": str(item.get("writing_mode_id", ""))[:80],
                    "avg_mode_fit": float(item.get("avg_mode_fit", 0) or 0),
                }
                for item in mode_history[:5]
                if isinstance(item, Mapping)
            ],
        }
