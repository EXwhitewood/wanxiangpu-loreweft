from abc import ABC, abstractmethod
from typing import Any


class BaseRepository(ABC):
    @abstractmethod
    async def get(self, id: str) -> dict | None:
        pass

    @abstractmethod
    async def list(self, filters: dict | None = None) -> list[dict]:
        pass

    @abstractmethod
    async def create(self, data: dict) -> dict:
        pass

    @abstractmethod
    async def update(self, id: str, data: dict) -> dict | None:
        pass

    @abstractmethod
    async def delete(self, id: str) -> bool:
        pass
