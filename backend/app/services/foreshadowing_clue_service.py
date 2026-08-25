import uuid
import logging
from typing import Any

from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.db_models import ForeshadowingClue, ForeshadowingLine
from app.services.embedding_service import EmbeddingService

logger = logging.getLogger(__name__)


def _uuid(value: str | uuid.UUID) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def clue_to_dict(clue: ForeshadowingClue) -> dict[str, Any]:
    return {
        "id": clue.id,
        "project_id": clue.project_id,
        "foreshadowing_line_id": clue.foreshadowing_line_id,
        "clue_type": clue.clue_type,
        "chapter_number": clue.chapter_number,
        "scene_index": clue.scene_index,
        "clue_text": clue.clue_text,
        "pov_character": clue.pov_character,
        "salience_at_time": clue.salience_at_time,
        "is_revealed_clue": clue.is_revealed_clue,
        "revealed_in_chapter": clue.revealed_in_chapter,
        "created_at": clue.created_at.isoformat() if clue.created_at else None,
    }


class ForeshadowingClueService:
    def __init__(self):
        self._embedding = EmbeddingService()

    async def add_clue(
        self,
        project_id: str | uuid.UUID,
        foreshadowing_line_id: str | uuid.UUID,
        clue_type: str,
        chapter_number: int,
        clue_text: str,
        db: AsyncSession,
        pov_character: str = "",
        scene_index: int = 0,
        salience_at_time: str = "subtle",
    ) -> dict:
        pid = _uuid(project_id)
        fid = _uuid(foreshadowing_line_id)

        line = (
            await db.execute(
                select(ForeshadowingLine).where(
                    ForeshadowingLine.id == fid,
                    ForeshadowingLine.project_id == pid,
                )
            )
        ).scalar_one_or_none()
        if not line:
            raise ValueError(f"Foreshadowing line not found in project: {fid}")

        existing = (
            await db.execute(
                select(ForeshadowingClue).where(
                    ForeshadowingClue.project_id == pid,
                    ForeshadowingClue.foreshadowing_line_id == fid,
                    ForeshadowingClue.chapter_number == chapter_number,
                    ForeshadowingClue.scene_index == scene_index,
                    ForeshadowingClue.clue_text == clue_text,
                )
            )
        ).scalar_one_or_none()
        if existing:
            await self._update_clue_count(fid, db)
            return clue_to_dict(existing)

        embedding = await self._embedding.embed_text(clue_text)

        clue = ForeshadowingClue(
            project_id=pid,
            foreshadowing_line_id=fid,
            clue_type=clue_type,
            chapter_number=chapter_number,
            scene_index=scene_index,
            clue_text=clue_text,
            clue_text_embedding=embedding,
            pov_character=pov_character,
            salience_at_time=salience_at_time,
        )
        db.add(clue)
        await db.flush()
        await self._update_clue_count(fid, db)
        await db.commit()
        await db.refresh(clue)
        return clue_to_dict(clue)

    async def list_clues(
        self,
        foreshadowing_line_id: str | uuid.UUID,
        db: AsyncSession,
        clue_type: str | None = None,
        chapter_number: int | None = None,
    ) -> list[dict]:
        fid = _uuid(foreshadowing_line_id)
        stmt = select(ForeshadowingClue).where(ForeshadowingClue.foreshadowing_line_id == fid)
        if clue_type:
            stmt = stmt.where(ForeshadowingClue.clue_type == clue_type)
        if chapter_number is not None:
            stmt = stmt.where(ForeshadowingClue.chapter_number == chapter_number)
        stmt = stmt.order_by(ForeshadowingClue.chapter_number, ForeshadowingClue.scene_index)
        result = await db.execute(stmt)
        return [clue_to_dict(row) for row in result.scalars().all() if row]

    async def get_evidence_pool(
        self,
        foreshadowing_line_id: str | uuid.UUID,
        db: AsyncSession,
    ) -> dict:
        fid = _uuid(foreshadowing_line_id)
        stmt = select(
            ForeshadowingClue.clue_type,
            func.count().label("count"),
        ).where(
            ForeshadowingClue.foreshadowing_line_id == fid,
        ).group_by(ForeshadowingClue.clue_type)
        result = await db.execute(stmt)
        rows = result.mappings().all()

        pool: dict[str, Any] = {
            "supportive": 0,
            "distractive": 0,
            "contradictory": 0,
            "missing": 0,
        }
        for row in rows:
            key = row["clue_type"]
            if key in pool:
                pool[key] = row["count"]
        pool["total"] = sum(pool.values())

        all_clues = await self.list_clues(fid, db)
        pool["clues"] = all_clues
        return pool

    async def search_similar_clues(
        self,
        project_id: str | uuid.UUID,
        query_text: str,
        db: AsyncSession,
        top_k: int = 5,
        exclude_foreshadowing_id: str | uuid.UUID | None = None,
    ) -> list[dict]:
        pid = _uuid(project_id)
        query_embedding = await self._embedding.embed_text(query_text)
        if not query_embedding:
            return []

        stmt = select(ForeshadowingClue).where(
            ForeshadowingClue.project_id == pid,
            ForeshadowingClue.clue_text_embedding.isnot(None),
        )
        if exclude_foreshadowing_id:
            stmt = stmt.where(
                ForeshadowingClue.foreshadowing_line_id != _uuid(exclude_foreshadowing_id)
            )

        result = await db.execute(stmt)
        all_clues = result.scalars().all()

        scored = []
        for clue in all_clues:
            if clue.clue_text_embedding is None:
                continue
            try:
                dist = _cosine_distance(query_embedding, clue.clue_text_embedding)
                scored.append((dist, clue))
            except Exception:
                continue

        scored.sort(key=lambda x: x[0])
        return [clue_to_dict(c) for _, c in scored[:top_k]]

    async def delete_clues_for_chapter(
        self,
        foreshadowing_line_id: str | uuid.UUID,
        chapter_number: int,
        db: AsyncSession,
    ) -> int:
        fid = _uuid(foreshadowing_line_id)
        stmt = delete(ForeshadowingClue).where(
            ForeshadowingClue.foreshadowing_line_id == fid,
            ForeshadowingClue.chapter_number == chapter_number,
        )
        result = await db.execute(stmt)
        await self._update_clue_count(fid, db)
        await db.commit()
        return result.rowcount or 0

    async def _update_clue_count(
        self, foreshadowing_line_id: uuid.UUID, db: AsyncSession,
    ) -> None:
        result = await db.execute(
            select(func.count()).where(
                ForeshadowingClue.foreshadowing_line_id == foreshadowing_line_id,
            )
        )
        count = result.scalar() or 0
        await db.execute(
            ForeshadowingLine.__table__.update()
            .where(ForeshadowingLine.id == foreshadowing_line_id)
            .values(clues_placed=count)
        )


def _cosine_distance(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 1.0
    return 1.0 - dot / (norm_a * norm_b)
