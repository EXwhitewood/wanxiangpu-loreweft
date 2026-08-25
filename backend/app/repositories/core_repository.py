from typing import Any
from app.repositories.base import BaseRepository
from app.services.memory_core import CoreMemoryService


class CoreRepository(BaseRepository):
    def __init__(self, project_id: str, entity_type: str):
        self.project_id = project_id
        self.entity_type = entity_type
        self._service = CoreMemoryService()

    async def get(self, id: str) -> dict | None:
        methods = {
            "character": self._service.get_character,
            "world_rule": self._service.get_world_rule,
            "location": self._service.get_location,
            "foreshadowing": self._service.get_foreshadowing,
            "style_profile": self._service.get_style_profile,
            "promotion": self._service.get_promotion_proposal,
        }
        method = methods.get(self.entity_type)
        if method:
            return await method(self.project_id, id)
        return None

    async def list(self, filters: dict | None = None) -> list[dict]:
        methods = {
            "character": self._service.list_characters,
            "world_rule": self._service.list_world_rules,
            "location": self._service.list_locations,
            "foreshadowing": self._service.list_foreshadowing,
            "style_profile": self._service.list_style_profiles,
            "promotion": self._service.list_promotion_proposals,
        }
        method = methods.get(self.entity_type)
        if method:
            return await method(self.project_id)
        return []

    async def create(self, data: dict) -> dict:
        methods = {
            "character": self._service.create_character,
            "world_rule": self._service.create_world_rule,
            "location": self._service.create_location,
            "foreshadowing": self._service.create_foreshadowing,
            "style_profile": self._service.create_style_profile,
            "promotion": self._service.create_promotion_proposal,
        }
        method = methods.get(self.entity_type)
        if method:
            return await method(self.project_id, data)
        return data

    async def update(self, id: str, data: dict) -> dict | None:
        methods = {
            "character": self._service.update_character,
            "world_rule": self._service.update_world_rule,
            "location": self._service.update_location,
            "foreshadowing": self._service.update_foreshadowing,
            "style_profile": self._service.update_style_profile,
            "promotion": self._service.update_promotion_proposal,
        }
        method = methods.get(self.entity_type)
        if method:
            return await method(self.project_id, id, data)
        return None

    async def delete(self, id: str) -> bool:
        methods = {
            "character": self._service.delete_character,
            "world_rule": self._service.delete_world_rule,
            "location": self._service.delete_location,
            "foreshadowing": self._service.delete_foreshadowing,
            "style_profile": self._service.delete_style_profile,
            "promotion": self._service.delete_promotion_proposal,
        }
        method = methods.get(self.entity_type)
        if method:
            return await method(self.project_id, id)
        return False
