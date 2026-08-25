import asyncio
from collections import defaultdict


class ProjectLockManager:
    _locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    @classmethod
    def get_lock(cls, project_id: str) -> asyncio.Lock:
        return cls._locks[project_id]

    @classmethod
    def cleanup(cls, project_id: str):
        if project_id in cls._locks:
            lock = cls._locks[project_id]
            if not lock.locked():
                del cls._locks[project_id]
