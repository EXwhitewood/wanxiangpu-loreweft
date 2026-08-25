from __future__ import annotations


class ReaderContextBuilder:
    """Build a restricted reader-visible context.

    This builder intentionally excludes hidden outline, truth snapshots,
    future chapter plans, and secret foreshadowing payloads.
    """

    def build(
        self,
        *,
        generated_text: str,
        project=None,
        chapter_number: int = 0,
        scene_index: int = 0,
        previous_public_summary: str = "",
        target_reader: str = "",
    ) -> dict:
        genre = getattr(project, "genre", "") if project is not None else ""
        description = getattr(project, "description", "") if project is not None else ""
        return {
            "schema_version": 1,
            "generated_text": generated_text or "",
            "chapter_number": chapter_number,
            "scene_index": scene_index,
            "genre": genre,
            "project_description": description[:300],
            "previous_public_summary": previous_public_summary[:1200],
            "target_reader": target_reader or "中文网文读者",
        }

    @staticmethod
    def assert_no_hidden_keys(context: dict) -> None:
        forbidden = {
            "truth_snapshot",
            "scene_truth_snapshot",
            "core_facts",
            "future_outline",
            "secret_canonical_statement",
            "secret_spoiler_scope",
            "forbidden_future_concepts",
        }
        leaked = forbidden & set(context.keys())
        if leaked:
            raise ValueError(f"Reader context contains hidden keys: {', '.join(sorted(leaked))}")
