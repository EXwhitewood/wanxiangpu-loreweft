from app.services.memory_shell import ShellMemoryService
from app.services.text_coercion import to_text


class ShellSearchSkill:
    def __init__(self):
        self.shell_service = ShellMemoryService()

    async def execute(self, context: dict | None = None, **kwargs) -> dict:
        context = {**(context or {}), **kwargs}
        project_id = context.get("project_id", "")
        query = to_text(context.get("query", ""))
        limit = context.get("limit", 5)
        iterative = context.get("iterative", False)

        if iterative:
            results = await self.shell_service.iterative_search(project_id, query, max_rounds=3)
        else:
            results = await self.shell_service.search_details(project_id, query, limit=limit)

        return {"results": results}

    async def search_relevant_details(
        self,
        project_id: str,
        scene_context: dict,
        limit: int = 5,
    ) -> list[dict]:
        scene_beat = scene_context.get("scene_beat", {})
        query_parts = []
        for key in ("characters", "location", "goal", "conflict", "outcome", "hook", "info_release"):
            value = scene_beat.get(key)
            if value:
                query_parts.append(to_text(value))
        query = " ".join(query_parts)

        return await self.shell_service.search_details(
            project_id=project_id,
            query=query,
            limit=limit,
        )

    async def search_chapter_context(
        self,
        project_id: str,
        chapter_number: int,
        limit: int = 3,
    ) -> list[dict]:
        return await self.shell_service.search_chapters(
            project_id=project_id,
            query=f"第{chapter_number}章",
            limit=limit,
        )
