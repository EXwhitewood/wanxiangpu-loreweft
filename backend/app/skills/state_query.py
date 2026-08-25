from app.services.state_manager import StateManager


class StateQuerySkill:
    def __init__(self):
        self.state_manager = StateManager()

    async def execute(
        self,
        project_id: str | None = None,
        entity_id: str | None = None,
        character_id: str | None = None,
        **_: dict,
    ) -> dict:
        if not project_id:
            return {"status": "skipped", "reason": "missing_project_id"}
        if entity_id:
            return {"entity_state": await self.get_entity_state(project_id, entity_id)}
        if character_id:
            return {"pov_knowledge": await self.get_pov_knowledge(project_id, character_id)}
        return {"snapshot": await self.get_snapshot(project_id)}

    async def get_snapshot(self, project_id: str) -> dict:
        state = await self.state_manager.get_snapshot(project_id)
        return state.model_dump()

    async def get_entity_state(self, project_id: str, entity_id: str) -> dict | None:
        state = await self.state_manager.get_state(project_id)
        entity = state.objective_state.get(entity_id)
        if entity:
            return entity.model_dump()
        return None

    async def get_pov_knowledge(self, project_id: str, character_id: str) -> dict:
        state = await self.state_manager.get_state(project_id)
        view = state.subjective_views.get(character_id)
        if view:
            return view.model_dump()
        return {"believed_state": {}, "last_known": {}}
