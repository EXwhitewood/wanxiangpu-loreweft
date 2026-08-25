from __future__ import annotations

import json
import logging
import os
import asyncio
from collections import OrderedDict
from datetime import datetime, timedelta, timezone

import aiosqlite

from app.config import settings

logger = logging.getLogger(__name__)

_DB_PATH = getattr(settings, "sqlite_path", "data/loreweft.db")

_LRU_MAX = 50
_SQLITE_BUSY_TIMEOUT_SECONDS = 30
_SQLITE_BUSY_TIMEOUT_MS = _SQLITE_BUSY_TIMEOUT_SECONDS * 1000
_DEFAULT_TTL_SECONDS = 3600
_DEFAULT_LONG_TTL_SECONDS = 86400

_CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS story_states (
    project_id TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cache_entries (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
"""


def _ensure_data_dir():
    db_dir = os.path.dirname(_DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)


def _deep_merge(base: dict, override: dict) -> dict:
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


class SqliteStateManager:
    _instance = None
    _initialized = False

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._db: aiosqlite.Connection | None = None
        self._lru: OrderedDict = OrderedDict()
        self._connection_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        self._op_count = 0
        self._cleanup_interval = 100
        SqliteStateManager._initialized = True

    async def _get_db(self) -> aiosqlite.Connection:
        if self._db is None:
            async with self._connection_lock:
                if self._db is None:
                    _ensure_data_dir()
                    self._db = await aiosqlite.connect(
                        _DB_PATH,
                        timeout=_SQLITE_BUSY_TIMEOUT_SECONDS,
                    )
                    self._db.row_factory = aiosqlite.Row
                    await self._db.execute("PRAGMA journal_mode = WAL")
                    await self._db.execute(f"PRAGMA busy_timeout = {_SQLITE_BUSY_TIMEOUT_MS}")
                    await self._db.executescript(_CREATE_TABLES_SQL)
                    await self._db.commit()
        return self._db

    async def _maybe_cleanup(self):
        self._op_count += 1
        if self._op_count % self._cleanup_interval == 0:
            await self._cleanup_expired()

    async def _cleanup_expired(self):
        try:
            async with self._write_lock:
                db = await self._get_db()
                now = datetime.now(timezone.utc).isoformat()
                await db.execute("DELETE FROM cache_entries WHERE expires_at < ?", (now,))
                await db.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
                await db.commit()
        except Exception:
            logger.exception("Failed to cleanup expired entries")

    def _lru_get(self, project_id: str) -> dict | None:
        if project_id in self._lru:
            self._lru.move_to_end(project_id)
            return self._lru[project_id]
        return None

    def _lru_set(self, project_id: str, state: dict):
        if project_id in self._lru:
            self._lru.move_to_end(project_id)
        self._lru[project_id] = state
        while len(self._lru) > _LRU_MAX:
            self._lru.popitem(last=False)

    def _lru_delete(self, project_id: str):
        self._lru.pop(project_id, None)

    async def get_state(self, project_id: str) -> dict | None:
        await self._maybe_cleanup()
        cached = self._lru_get(project_id)
        if cached is not None:
            return cached
        try:
            db = await self._get_db()
            cursor = await db.execute(
                "SELECT state FROM story_states WHERE project_id = ?",
                (project_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            state = json.loads(row["state"])
            self._lru_set(project_id, state)
            return state
        except Exception:
            logger.exception("Failed to get state for project %s", project_id)
            return None

    async def set_state(self, project_id: str, state: dict) -> None:
        await self._maybe_cleanup()
        try:
            async with self._write_lock:
                db = await self._get_db()
                now = datetime.now(timezone.utc).isoformat()
                state_json = json.dumps(state, ensure_ascii=False)
                await db.execute(
                    "INSERT OR REPLACE INTO story_states (project_id, state, updated_at) VALUES (?, ?, ?)",
                    (project_id, state_json, now),
                )
                await db.commit()
                self._lru_set(project_id, state)
        except Exception:
            logger.exception("Failed to set state for project %s", project_id)
            raise

    async def update_state(self, project_id: str, updates: dict) -> dict:
        await self._maybe_cleanup()
        current = await self.get_state(project_id)
        if current is None:
            current = {}
        merged = _deep_merge(current, updates)
        await self.set_state(project_id, merged)
        return merged

    async def delete_state(self, project_id: str) -> bool:
        await self._maybe_cleanup()
        try:
            async with self._write_lock:
                db = await self._get_db()
                cursor = await db.execute(
                    "DELETE FROM story_states WHERE project_id = ?",
                    (project_id,),
                )
                await db.commit()
                self._lru_delete(project_id)
                return cursor.rowcount > 0
        except Exception:
            logger.exception("Failed to delete state for project %s", project_id)
            return False

    async def get_cache(self, key: str) -> str | None:
        await self._maybe_cleanup()
        try:
            db = await self._get_db()
            cursor = await db.execute(
                "SELECT value, expires_at FROM cache_entries WHERE key = ?",
                (key,),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            expires_at = datetime.fromisoformat(row["expires_at"])
            if expires_at < datetime.now(timezone.utc):
                await db.execute("DELETE FROM cache_entries WHERE key = ?", (key,))
                await db.commit()
                return None
            return row["value"]
        except Exception:
            logger.exception("Failed to get cache for key %s", key)
            return None

    async def set_cache(self, key: str, value: str, ttl: int = _DEFAULT_TTL_SECONDS) -> None:
        await self._maybe_cleanup()
        try:
            async with self._write_lock:
                db = await self._get_db()
                expires_at = (datetime.now(timezone.utc) + timedelta(seconds=ttl)).isoformat()
                await db.execute(
                    "INSERT OR REPLACE INTO cache_entries (key, value, expires_at) VALUES (?, ?, ?)",
                    (key, value, expires_at),
                )
                await db.commit()
        except Exception:
            logger.exception("Failed to set cache for key %s", key)

    async def delete_cache(self, key: str) -> bool:
        await self._maybe_cleanup()
        try:
            async with self._write_lock:
                db = await self._get_db()
                cursor = await db.execute(
                    "DELETE FROM cache_entries WHERE key = ?",
                    (key,),
                )
                await db.commit()
                return cursor.rowcount > 0
        except Exception:
            logger.exception("Failed to delete cache for key %s", key)
            return False

    async def get_session(self, session_id: str) -> dict | None:
        await self._maybe_cleanup()
        try:
            db = await self._get_db()
            cursor = await db.execute(
                "SELECT data, expires_at FROM sessions WHERE session_id = ?",
                (session_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            expires_at = datetime.fromisoformat(row["expires_at"])
            if expires_at < datetime.now(timezone.utc):
                await db.execute(
                    "DELETE FROM sessions WHERE session_id = ?",
                    (session_id,),
                )
                await db.commit()
                return None
            return json.loads(row["data"])
        except Exception:
            logger.exception("Failed to get session %s", session_id)
            return None

    async def set_session(self, session_id: str, data: dict, ttl: int = _DEFAULT_LONG_TTL_SECONDS) -> None:
        await self._maybe_cleanup()
        try:
            async with self._write_lock:
                db = await self._get_db()
                expires_at = (datetime.now(timezone.utc) + timedelta(seconds=ttl)).isoformat()
                data_json = json.dumps(data, ensure_ascii=False)
                await db.execute(
                    "INSERT OR REPLACE INTO sessions (session_id, data, expires_at) VALUES (?, ?, ?)",
                    (session_id, data_json, expires_at),
                )
                await db.commit()
        except Exception:
            logger.exception("Failed to set session %s", session_id)

    async def delete_session(self, session_id: str) -> bool:
        await self._maybe_cleanup()
        try:
            async with self._write_lock:
                db = await self._get_db()
                cursor = await db.execute(
                    "DELETE FROM sessions WHERE session_id = ?",
                    (session_id,),
                )
                await db.commit()
                return cursor.rowcount > 0
        except Exception:
            logger.exception("Failed to delete session %s", session_id)
            return False

    async def close(self) -> None:
        if self._db is not None:
            try:
                await self._db.close()
            except Exception:
                logger.exception("Failed to close database connection")
            finally:
                self._db = None
        self._lru.clear()
