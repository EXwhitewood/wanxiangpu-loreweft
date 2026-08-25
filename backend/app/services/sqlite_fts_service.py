from __future__ import annotations

import logging
import uuid
from pathlib import Path

import aiosqlite

from app.config import settings

logger = logging.getLogger(__name__)

_CREATE_DATA_TABLE = """
CREATE TABLE IF NOT EXISTS fts_detail_seeds (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    chapter_id TEXT NOT NULL DEFAULT '',
    scene_number INTEGER NOT NULL DEFAULT 1,
    entity_id TEXT NOT NULL DEFAULT '',
    fact TEXT NOT NULL DEFAULT '',
    narrative_time TEXT NOT NULL DEFAULT '',
    tier TEXT NOT NULL DEFAULT 'T3',
    source_text TEXT NOT NULL DEFAULT '',
    entity_type TEXT NOT NULL DEFAULT '',
    location TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT ''
)
"""

_CREATE_FTS_TABLE = """
CREATE VIRTUAL TABLE IF NOT EXISTS detail_seeds_fts_v2
USING fts5(fact, source_text, entity_id, content='fts_detail_seeds', content_rowid='rowid', tokenize='trigram')
"""

_CREATE_FTS_TRIGGERS = (
    """CREATE TRIGGER IF NOT EXISTS fts_detail_seeds_v2_ai AFTER INSERT ON fts_detail_seeds BEGIN
        INSERT INTO detail_seeds_fts_v2(rowid, fact, source_text, entity_id)
        VALUES (new.rowid, new.fact, new.source_text, new.entity_id);
    END""",
    """CREATE TRIGGER IF NOT EXISTS fts_detail_seeds_v2_ad AFTER DELETE ON fts_detail_seeds BEGIN
        INSERT INTO detail_seeds_fts_v2(detail_seeds_fts_v2, rowid, fact, source_text, entity_id)
        VALUES ('delete', old.rowid, old.fact, old.source_text, old.entity_id);
    END""",
    """CREATE TRIGGER IF NOT EXISTS fts_detail_seeds_v2_au AFTER UPDATE ON fts_detail_seeds BEGIN
        INSERT INTO detail_seeds_fts_v2(detail_seeds_fts_v2, rowid, fact, source_text, entity_id)
        VALUES ('delete', old.rowid, old.fact, old.source_text, old.entity_id);
        INSERT INTO detail_seeds_fts_v2(rowid, fact, source_text, entity_id)
        VALUES (new.rowid, new.fact, new.source_text, new.entity_id);
    END""",
)

_CREATE_FTS_INDEX_PROJECT = """
CREATE INDEX IF NOT EXISTS idx_fts_seeds_project_id ON fts_detail_seeds(project_id)
"""

_CREATE_FTS_INDEX_CHAPTER = """
CREATE INDEX IF NOT EXISTS idx_fts_seeds_chapter_id ON fts_detail_seeds(chapter_id)
"""

_CREATE_FTS_INDEX_ENTITY = """
CREATE INDEX IF NOT EXISTS idx_fts_seeds_entity_id ON fts_detail_seeds(entity_id)
"""

_CREATE_FTS_INDEX_TIER = """
CREATE INDEX IF NOT EXISTS idx_fts_seeds_tier ON fts_detail_seeds(tier)
"""

_CREATE_FTS_INDEX_LOCATION = """
CREATE INDEX IF NOT EXISTS idx_fts_seeds_location ON fts_detail_seeds(location)
"""

_SEED_COLUMNS = [
    "id", "project_id", "chapter_id", "scene_number", "entity_id",
    "fact", "narrative_time", "tier", "source_text", "entity_type",
    "location", "created_at",
]


class SqliteFtsService:
    _instance = None
    _db = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def _db_path(cls) -> str:
        path = getattr(settings, "sqlite_fts_path", "data/loreweft.db")
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        return str(p)

    async def _get_db(self) -> aiosqlite.Connection:
        if self._db is None:
            self._db = await aiosqlite.connect(self._db_path())
            self._db.row_factory = aiosqlite.Row
            await self._db.execute("PRAGMA journal_mode=WAL")
            await self._db.execute("PRAGMA synchronous=NORMAL")
        return self._db

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def ensure_index(self) -> None:
        try:
            db = await self._get_db()
            await db.execute(_CREATE_DATA_TABLE)
            await db.execute(_CREATE_FTS_TABLE)
            for trigger in _CREATE_FTS_TRIGGERS:
                await db.execute(trigger)
            await db.execute(_CREATE_FTS_INDEX_PROJECT)
            await db.execute(_CREATE_FTS_INDEX_CHAPTER)
            await db.execute(_CREATE_FTS_INDEX_ENTITY)
            await db.execute(_CREATE_FTS_INDEX_TIER)
            await db.execute(_CREATE_FTS_INDEX_LOCATION)
            await db.commit()
            await self.sync_from_primary_store()
        except Exception as e:
            logger.error("Failed to ensure index: %s", e)

    async def index_detail_seed(self, seed_data: dict) -> dict | None:
        try:
            db = await self._get_db()
            doc_id = seed_data.get("id") or str(uuid.uuid4())
            await db.execute(
                """
                INSERT INTO fts_detail_seeds
                    (id, project_id, chapter_id, scene_number, entity_id,
                     fact, narrative_time, tier, source_text, entity_type,
                     location, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    project_id=excluded.project_id, chapter_id=excluded.chapter_id,
                    scene_number=excluded.scene_number, entity_id=excluded.entity_id,
                    fact=excluded.fact, narrative_time=excluded.narrative_time,
                    tier=excluded.tier, source_text=excluded.source_text,
                    entity_type=excluded.entity_type, location=excluded.location,
                    created_at=excluded.created_at
                """,
                (
                    doc_id,
                    seed_data.get("project_id") or "",
                    seed_data.get("chapter_id") or "",
                    seed_data.get("scene_number") or 1,
                    seed_data.get("entity_id") or "",
                    seed_data.get("fact") or "",
                    seed_data.get("narrative_time") or "",
                    seed_data.get("tier") or "T3",
                    seed_data.get("source_text") or "",
                    seed_data.get("entity_type") or "",
                    seed_data.get("location") or "",
                    seed_data.get("created_at") or "",
                ),
            )
            await db.commit()
            return {"id": doc_id, "result": "created"}
        except Exception as e:
            logger.error("Failed to index detail seed: %s", e)
            try:
                if self._db is not None:
                    await self._db.rollback()
            except Exception as rollback_error:
                logger.warning("Failed to roll back detail seed FTS write: %s", rollback_error)
            return None

    async def bulk_index_seeds(self, seeds: list[dict]) -> dict:
        try:
            db = await self._get_db()
            success = 0
            errors = []
            for seed in seeds:
                doc_id = seed.get("id") or str(uuid.uuid4())
                try:
                    await db.execute(
                        """
                        INSERT INTO fts_detail_seeds
                            (id, project_id, chapter_id, scene_number, entity_id,
                             fact, narrative_time, tier, source_text, entity_type,
                             location, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(id) DO UPDATE SET
                            project_id=excluded.project_id, chapter_id=excluded.chapter_id,
                            scene_number=excluded.scene_number, entity_id=excluded.entity_id,
                            fact=excluded.fact, narrative_time=excluded.narrative_time,
                            tier=excluded.tier, source_text=excluded.source_text,
                            entity_type=excluded.entity_type, location=excluded.location,
                            created_at=excluded.created_at
                        """,
                        (
                            doc_id,
                            seed.get("project_id") or "",
                            seed.get("chapter_id") or "",
                            seed.get("scene_number") or 1,
                            seed.get("entity_id") or "",
                            seed.get("fact") or "",
                            seed.get("narrative_time") or "",
                            seed.get("tier") or "T3",
                            seed.get("source_text") or "",
                            seed.get("entity_type") or "",
                            seed.get("location") or "",
                            seed.get("created_at") or "",
                        ),
                    )
                    success += 1
                except Exception as inner_e:
                    errors.append(str(inner_e))
            await db.commit()
            return {"success": success, "errors": errors}
        except Exception as e:
            logger.error("Failed to bulk index seeds: %s", e)
            try:
                if self._db is not None:
                    await self._db.rollback()
            except Exception as rollback_error:
                logger.warning("Failed to roll back bulk FTS write: %s", rollback_error)
            return {"success": 0, "errors": [str(e)]}

    async def delete_seed(self, seed_id: str) -> bool:
        try:
            db = await self._get_db()
            cursor = await db.execute(
                "SELECT rowid FROM fts_detail_seeds WHERE id = ?", (seed_id,)
            )
            row = await cursor.fetchone()
            if row is None:
                return False
            rowid = row[0]
            await db.execute(
                "DELETE FROM fts_detail_seeds WHERE id = ?", (seed_id,)
            )
            await db.commit()
            return True
        except Exception as e:
            logger.error("Failed to delete seed %s: %s", seed_id, e)
            try:
                if self._db is not None:
                    await self._db.rollback()
            except Exception as rollback_error:
                logger.warning("Failed to roll back FTS seed delete: %s", rollback_error)
            return False

    async def delete_chapter_seeds(self, project_id: str, chapter_id: str) -> int:
        """Remove every FTS mirror belonging to one chapter."""
        if not chapter_id:
            return 0
        try:
            db = await self._get_db()
            cursor = await db.execute(
                "DELETE FROM fts_detail_seeds WHERE project_id = ? AND chapter_id = ?",
                (str(project_id), str(chapter_id)),
            )
            await db.commit()
            return max(int(cursor.rowcount or 0), 0)
        except Exception as e:
            logger.error("Failed to delete chapter FTS seeds %s/%s: %s", project_id, chapter_id, e)
            try:
                if self._db is not None:
                    await self._db.rollback()
            except Exception as rollback_error:
                logger.warning("Failed to roll back chapter FTS delete: %s", rollback_error)
            return 0

    async def update_seed(self, seed_id: str, data: dict) -> dict | None:
        try:
            db = await self._get_db()
            allowed = {
                "project_id", "chapter_id", "scene_number", "entity_id",
                "fact", "narrative_time", "tier", "source_text",
                "entity_type", "location", "created_at",
            }
            updates = {k: v for k, v in data.items() if k in allowed}
            if not updates:
                return {"id": seed_id, "result": "noop"}
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            values = list(updates.values()) + [seed_id]
            await db.execute(
                f"UPDATE fts_detail_seeds SET {set_clause} WHERE id = ?",
                values,
            )
            await db.commit()
            return {"id": seed_id, "result": "updated"}
        except Exception as e:
            logger.error("Failed to update seed %s: %s", seed_id, e)
            return None

    async def search_text(
        self,
        project_id: str,
        query: str,
        limit: int = 10,
        filters: dict | None = None,
    ) -> list[dict]:
        try:
            db = await self._get_db()
            fts_query = self._escape_fts_query(query)
            sql = """
                SELECT s.*, f.rank
                FROM fts_detail_seeds s
                JOIN detail_seeds_fts_v2 f ON s.rowid = f.rowid
                WHERE detail_seeds_fts_v2 MATCH ?
                  AND s.project_id = ?
            """
            params: list = [fts_query, project_id]
            if filters:
                if filters.get("tier"):
                    sql += " AND s.tier = ?"
                    params.append(filters["tier"])
                if filters.get("entity_id"):
                    sql += " AND s.entity_id = ?"
                    params.append(filters["entity_id"])
                if filters.get("chapter_range"):
                    cr = filters["chapter_range"]
                    sql += " AND s.scene_number >= ? AND s.scene_number <= ?"
                    params.extend([cr[0], cr[1]])
            sql += " ORDER BY f.rank LIMIT ?"
            params.append(limit)
            cursor = await db.execute(sql, params)
            rows = await cursor.fetchall()
            return [self._row_to_dict(row) for row in rows]
        except Exception as e:
            logger.error("Failed to search text: %s", e)
            return []

    async def search_spatiotemporal(
        self,
        project_id: str,
        chapter_range: tuple | None = None,
        scene_range: tuple | None = None,
        location: str | None = None,
        narrative_time_range: tuple | None = None,
        limit: int = 10,
    ) -> list[dict]:
        try:
            db = await self._get_db()
            sql = "SELECT * FROM fts_detail_seeds WHERE project_id = ?"
            params: list = [project_id]
            if chapter_range:
                sql += " AND chapter_id >= ? AND chapter_id <= ?"
                params.extend([chapter_range[0], chapter_range[1]])
            if scene_range:
                sql += " AND scene_number >= ? AND scene_number <= ?"
                params.extend([scene_range[0], scene_range[1]])
            if location:
                sql += " AND location = ?"
                params.append(location)
            if narrative_time_range:
                sql += " AND narrative_time >= ? AND narrative_time <= ?"
                params.extend([narrative_time_range[0], narrative_time_range[1]])
            sql += " ORDER BY narrative_time ASC, scene_number ASC LIMIT ?"
            params.append(limit)
            cursor = await db.execute(sql, params)
            rows = await cursor.fetchall()
            return [self._row_to_dict(row, score=None) for row in rows]
        except Exception as e:
            logger.error("Failed to search spatiotemporal: %s", e)
            return []

    async def search_combined(
        self,
        project_id: str,
        query: str,
        chapter_range: tuple | None = None,
        tier: str | None = None,
        limit: int = 10,
    ) -> list[dict]:
        try:
            db = await self._get_db()
            fts_query = self._escape_fts_query(query)
            sql = """
                SELECT s.*, f.rank
                FROM fts_detail_seeds s
                JOIN detail_seeds_fts_v2 f ON s.rowid = f.rowid
                WHERE detail_seeds_fts_v2 MATCH ?
                  AND s.project_id = ?
            """
            params: list = [fts_query, project_id]
            if chapter_range:
                sql += " AND s.chapter_id >= ? AND s.chapter_id <= ?"
                params.extend([chapter_range[0], chapter_range[1]])
            if tier:
                sql += " AND s.tier = ?"
                params.append(tier)
            sql += " ORDER BY f.rank LIMIT ?"
            params.append(limit)
            cursor = await db.execute(sql, params)
            rows = await cursor.fetchall()
            return [self._row_to_dict(row) for row in rows]
        except Exception as e:
            logger.error("Failed to search combined: %s", e)
            return []

    async def search_fuzzy(
        self,
        project_id: str,
        query: str,
        fuzziness: str = "AUTO",
        limit: int = 10,
    ) -> list[dict]:
        try:
            db = await self._get_db()
            fts_query = self._build_fuzzy_query(query)
            sql = """
                SELECT s.*, f.rank
                FROM fts_detail_seeds s
                JOIN detail_seeds_fts_v2 f ON s.rowid = f.rowid
                WHERE detail_seeds_fts_v2 MATCH ?
                  AND s.project_id = ?
                ORDER BY f.rank
                LIMIT ?
            """
            params = [fts_query, project_id, limit]
            cursor = await db.execute(sql, params)
            rows = await cursor.fetchall()
            return [self._row_to_dict(row) for row in rows]
        except Exception as e:
            logger.error("Failed to search fuzzy: %s", e)
            return []

    async def aggregate_by_entity(
        self, project_id: str, entity_type: str | None = None
    ) -> list[dict]:
        try:
            db = await self._get_db()
            sql = """
                SELECT entity_id, COUNT(*) as cnt,
                       fact, tier, entity_type
                FROM fts_detail_seeds
                WHERE project_id = ?
            """
            params: list = [project_id]
            if entity_type:
                sql += " AND entity_type = ?"
                params.append(entity_type)
            sql += " GROUP BY entity_id ORDER BY cnt DESC LIMIT 100"
            cursor = await db.execute(sql, params)
            rows = await cursor.fetchall()
            results = []
            for row in rows:
                results.append({
                    "entity_id": row["entity_id"],
                    "count": row["cnt"],
                    "top_fact": {
                        "fact": row["fact"],
                        "tier": row["tier"],
                        "entity_type": row["entity_type"],
                    } if row["fact"] else None,
                })
            return results
        except Exception as e:
            logger.error("Failed to aggregate by entity: %s", e)
            return []

    async def aggregate_by_chapter(self, project_id: str) -> list[dict]:
        try:
            db = await self._get_db()
            sql = """
                SELECT chapter_id, COUNT(*) as cnt, tier
                FROM fts_detail_seeds
                WHERE project_id = ?
                GROUP BY chapter_id, tier
                ORDER BY chapter_id ASC
            """
            cursor = await db.execute(sql, (project_id,))
            rows = await cursor.fetchall()
            chapter_map: dict[str, dict] = {}
            for row in rows:
                ch = row["chapter_id"]
                if ch not in chapter_map:
                    chapter_map[ch] = {"chapter_id": ch, "count": 0, "tier_distribution": {}}
                chapter_map[ch]["count"] += row["cnt"]
                chapter_map[ch]["tier_distribution"][row["tier"]] = row["cnt"]
            return list(chapter_map.values())
        except Exception as e:
            logger.error("Failed to aggregate by chapter: %s", e)
            return []

    @staticmethod
    def _row_to_dict(row: aiosqlite.Row, score=None) -> dict:
        d = dict(row)
        d["id"] = d.pop("id", d.get("id"))
        if "rank" in d:
            d["_score"] = d.pop("rank")
        else:
            d["_score"] = score
        return d

    @staticmethod
    def _escape_fts_query(query: str) -> str:
        compact = "".join(str(query or "").split())
        tokens = query.split()
        if compact and compact not in tokens:
            tokens.append(compact)
        escaped = []
        for token in tokens:
            clean = token.replace('"', "")
            if clean:
                escaped.append(f'"{clean}"')
        return " OR ".join(escaped) if escaped else '""'

    async def sync_from_primary_store(self) -> dict:
        """Idempotently mirror canonical detail_seeds into the derived index."""
        try:
            db = await self._get_db()
            exists = await db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='detail_seeds'"
            )
            if await exists.fetchone() is None:
                return {"synced": 0, "reason": "primary_table_missing"}
            await db.execute(
                """
                INSERT OR REPLACE INTO fts_detail_seeds
                    (id, project_id, chapter_id, scene_number, entity_id, fact,
                     narrative_time, tier, source_text, entity_type, location, created_at)
                SELECT CAST(id AS TEXT), CAST(project_id AS TEXT), CAST(chapter_id AS TEXT),
                       COALESCE(scene_number, 1), COALESCE(entity_id, ''), COALESCE(fact, ''),
                       COALESCE(narrative_time, ''), COALESCE(tier, 'T3'),
                       COALESCE(source_text, ''), COALESCE(entity_type, ''), '',
                       COALESCE(CAST(created_at AS TEXT), '')
                FROM detail_seeds
                """
            )
            await db.execute("INSERT INTO detail_seeds_fts_v2(detail_seeds_fts_v2) VALUES('rebuild')")
            await db.commit()
            cursor = await db.execute("SELECT COUNT(*) FROM fts_detail_seeds")
            row = await cursor.fetchone()
            return {"synced": int(row[0] if row else 0)}
        except Exception as exc:
            logger.error("Failed to sync FTS from primary store: %s", exc)
            return {"synced": 0, "error": str(exc)}

    @staticmethod
    def _build_fuzzy_query(query: str) -> str:
        chars = list(query.replace(" ", ""))
        if not chars:
            return '""'
        or_terms = " OR ".join(f'"{c}"' for c in chars if c.strip())
        words = query.split()
        if len(words) > 1:
            word_terms = " OR ".join(f'"{w}"' for w in words if w.strip())
            or_terms = f"{or_terms} OR {word_terms}"
        return or_terms
