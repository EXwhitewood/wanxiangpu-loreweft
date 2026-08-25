from __future__ import annotations

import json
import logging
import os
import uuid
from typing import Any

import aiosqlite

from app.config import settings
from app.repositories.base import BaseRepository
from app.services.memory_shell import ShellMemoryService

logger = logging.getLogger(__name__)

_db_initialized: bool = False
_vss_available: bool | None = None
_SQLITE_BUSY_TIMEOUT_SECONDS = 30
_SQLITE_BUSY_TIMEOUT_MS = _SQLITE_BUSY_TIMEOUT_SECONDS * 1000


def _get_db_path() -> str:
    path = getattr(settings, "sqlite_path", None) or "data/loreweft.db"
    db_dir = os.path.dirname(path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    return path


def _embedding_to_blob(embedding: list[float]) -> bytes:
    import numpy as np
    return np.array(embedding, dtype=np.float32).tobytes()


def _blob_to_embedding(blob: bytes) -> list[float]:
    import numpy as np
    return np.frombuffer(blob, dtype=np.float32).tolist()


def _row_to_dict(row: aiosqlite.Row) -> dict:
    d = dict(row)
    if "embedding" in d and isinstance(d["embedding"], bytes):
        d["embedding"] = _blob_to_embedding(d["embedding"])
    return d


async def _check_vss_available(db: aiosqlite.Connection) -> bool:
    global _vss_available
    if _vss_available is not None:
        return _vss_available
    try:
        await db.execute("SELECT vss_version()")
        _vss_available = True
    except Exception:
        _vss_available = False
        logger.info("sqlite-vss extension not available, falling back to numpy cosine similarity")
    return _vss_available


async def _ensure_tables(db: aiosqlite.Connection) -> None:
    global _db_initialized
    if _db_initialized:
        return
    await db.execute("""
        CREATE TABLE IF NOT EXISTS shell_seeds (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            chapter_id TEXT NOT NULL DEFAULT '',
            scene_number INTEGER NOT NULL DEFAULT 1,
            entity_id TEXT NOT NULL DEFAULT '',
            fact TEXT NOT NULL DEFAULT '',
            embedding BLOB,
            narrative_time TEXT NOT NULL DEFAULT '',
            tier TEXT NOT NULL DEFAULT 'T3',
            source_text TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT ''
        )
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_shell_seeds_project_id
        ON shell_seeds(project_id)
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_shell_seeds_tier
        ON shell_seeds(project_id, tier)
    """)
    vss_ok = await _check_vss_available(db)
    if vss_ok:
        try:
            await db.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS shell_seeds_vss
                USING vss0(
                    embedding(1536)
                )
            """)
        except Exception:
            logger.exception("Failed to create vss virtual table, falling back to numpy")
            global _vss_available
            _vss_available = False
    _db_initialized = True


class SqliteVssShellRepository(BaseRepository):
    def __init__(self, project_id: str):
        self.project_id = project_id
        self._service = ShellMemoryService()
        self._db_path = _get_db_path()

    async def _get_db(self) -> aiosqlite.Connection:
        db = await aiosqlite.connect(
            self._db_path,
            timeout=_SQLITE_BUSY_TIMEOUT_SECONDS,
        )
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA journal_mode = WAL")
        await db.execute(f"PRAGMA busy_timeout = {_SQLITE_BUSY_TIMEOUT_MS}")
        await _ensure_tables(db)
        return db

    async def get(self, id: str) -> dict | None:
        seeds = await self._service.get_seeds_by_tier(self.project_id)
        for s in seeds:
            if s.get("id") == id:
                return s
        return None

    async def list(self, filters: dict | None = None) -> list[dict]:
        tier = filters.get("tier") if filters else None
        return await self._service.get_seeds_by_tier(self.project_id, tier)

    async def create(self, data: dict) -> dict:
        result = await self._service.store_detail_seed(self.project_id, data)
        embedding = data.get("embedding")
        if embedding:
            try:
                await self._store_vector(result["id"], embedding)
            except Exception:
                logger.exception("Failed to store vector in sqlite for seed %s", result["id"])
        return result

    async def update(self, id: str, data: dict) -> dict | None:
        if "tier" in data:
            result = await self._service.update_seed_tier(self.project_id, id, data["tier"])
        else:
            result = None
        embedding = data.get("embedding")
        if embedding and result:
            try:
                await self._update_vector(id, embedding)
            except Exception:
                logger.exception("Failed to update vector in sqlite for seed %s", id)
        return result

    async def delete(self, id: str) -> bool:
        try:
            db = await self._get_db()
            try:
                await db.execute("DELETE FROM shell_seeds WHERE id = ?", (id,))
                await db.commit()
                return True
            finally:
                await db.close()
        except Exception:
            logger.exception("Failed to delete seed %s from sqlite", id)
            return False

    async def _store_vector(self, seed_id: str, embedding: list[float]) -> None:
        db = await self._get_db()
        try:
            blob = _embedding_to_blob(embedding)
            await db.execute(
                "INSERT OR REPLACE INTO shell_seeds (id, project_id, embedding, created_at) VALUES (?, ?, ?, datetime('now'))",
                (seed_id, self.project_id, blob),
            )
            vss_ok = await _check_vss_available(db)
            if vss_ok:
                try:
                    import numpy as np
                    vec_json = json.dumps(embedding)
                    await db.execute(
                        "INSERT OR REPLACE INTO shell_seeds_vss (rowid, embedding) VALUES (?, ?)",
                        (hash(seed_id) & 0x7FFFFFFF, vec_json),
                    )
                except Exception:
                    logger.exception("Failed to insert into vss table for seed %s", seed_id)
            await db.commit()
        finally:
            await db.close()

    async def _update_vector(self, seed_id: str, embedding: list[float]) -> None:
        db = await self._get_db()
        try:
            blob = _embedding_to_blob(embedding)
            await db.execute(
                "UPDATE shell_seeds SET embedding = ? WHERE id = ?",
                (blob, seed_id),
            )
            vss_ok = await _check_vss_available(db)
            if vss_ok:
                try:
                    vec_json = json.dumps(embedding)
                    rowid = hash(seed_id) & 0x7FFFFFFF
                    await db.execute(
                        "DELETE FROM shell_seeds_vss WHERE rowid = ?",
                        (rowid,),
                    )
                    await db.execute(
                        "INSERT OR REPLACE INTO shell_seeds_vss (rowid, embedding) VALUES (?, ?)",
                        (rowid, vec_json),
                    )
                except Exception:
                    logger.exception("Failed to update vss table for seed %s", seed_id)
            await db.commit()
        finally:
            await db.close()

    async def search_by_vector(
        self,
        project_id: str,
        embedding: list[float],
        limit: int = 10,
        tier: str | None = None,
    ) -> list[dict]:
        db = await self._get_db()
        try:
            vss_ok = await _check_vss_available(db)
            if vss_ok:
                return await self._search_vss(db, project_id, embedding, limit, tier)
            else:
                return await self._search_bruteforce(db, project_id, embedding, limit, tier)
        finally:
            await db.close()

    async def _search_vss(
        self,
        db: aiosqlite.Connection,
        project_id: str,
        embedding: list[float],
        limit: int,
        tier: str | None,
    ) -> list[dict]:
        try:
            vec_json = json.dumps(embedding)
            cursor = await db.execute(
                "SELECT rowid, distance FROM shell_seeds_vss WHERE vss_search(embedding, ?) LIMIT ?",
                (vec_json, limit * 3),
            )
            vss_rows = await cursor.fetchall()
            if not vss_rows:
                return []
            rowids = [r[0] for r in vss_rows]
            distance_map = {r[0]: r[1] for r in vss_rows}
            placeholders = ",".join("?" for _ in rowids)
            conditions = [f"s.id IN (SELECT id FROM shell_seeds WHERE rowid IN ({placeholders}))"]
            params: list = list(rowids)
            conditions.append("s.project_id = ?")
            params.append(project_id)
            if tier:
                conditions.append("s.tier = ?")
                params.append(tier)
            params.append(limit)
            where_clause = " AND ".join(conditions)
            cursor = await db.execute(
                f"SELECT s.* FROM shell_seeds s WHERE {where_clause} LIMIT ?",
                params,
            )
            rows = await cursor.fetchall()
            results = []
            for row in rows:
                d = _row_to_dict(row)
                rowid_val = None
                cursor2 = await db.execute("SELECT rowid FROM shell_seeds WHERE id = ?", (d["id"],))
                r2 = await cursor2.fetchone()
                if r2:
                    rowid_val = r2[0]
                if rowid_val is not None and rowid_val in distance_map:
                    d["score"] = 1.0 - distance_map[rowid_val]
                else:
                    d["score"] = 0.0
                results.append(d)
            results.sort(key=lambda x: x.get("score", 0.0), reverse=True)
            return results[:limit]
        except Exception:
            logger.exception("VSS search failed, falling back to brute-force")
            return await self._search_bruteforce(db, project_id, embedding, limit, tier)

    async def _search_bruteforce(
        self,
        db: aiosqlite.Connection,
        project_id: str,
        embedding: list[float],
        limit: int,
        tier: str | None,
    ) -> list[dict]:
        import numpy as np

        conditions = ["project_id = ?"]
        params: list = [project_id]
        if tier:
            conditions.append("tier = ?")
            params.append(tier)
        conditions.append("embedding IS NOT NULL")
        where_clause = " AND ".join(conditions)
        cursor = await db.execute(
            f"SELECT * FROM shell_seeds WHERE {where_clause}",
            params,
        )
        rows = await cursor.fetchall()
        if not rows:
            return []

        query_vec = np.array(embedding, dtype=np.float32)
        query_norm = np.linalg.norm(query_vec)
        if query_norm == 0:
            return []
        query_vec = query_vec / query_norm

        scored = []
        for row in rows:
            d = _row_to_dict(row)
            emb = d.pop("embedding", None)
            if emb is None:
                continue
            seed_vec = np.array(emb, dtype=np.float32)
            seed_norm = np.linalg.norm(seed_vec)
            if seed_norm == 0:
                continue
            similarity = float(np.dot(query_vec, seed_vec / seed_norm))
            d["score"] = similarity
            scored.append(d)

        scored.sort(key=lambda x: x.get("score", 0.0), reverse=True)
        return scored[:limit]
