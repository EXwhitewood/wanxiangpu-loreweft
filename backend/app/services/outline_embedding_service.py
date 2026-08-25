import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.db_models import Project, OutlineChunk, PGVECTOR_AVAILABLE

logger = logging.getLogger(__name__)

_model = None
_model_name = "paraphrase-multilingual-MiniLM-L12-v2"
_embedding_dim = 384


def _get_model():
    global _model
    if _model is not None:
        return _model
    try:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(_model_name)
        logger.info(f"[Embedding] Loaded model: {_model_name}")
        return _model
    except ImportError:
        logger.warning("[Embedding] sentence-transformers not installed, semantic search unavailable")
        return None
    except Exception as e:
        logger.warning(f"[Embedding] Failed to load model: {e}")
        return None


def _encode(text: str) -> list[float] | None:
    if not PGVECTOR_AVAILABLE:
        return None
    model = _get_model()
    if model is None:
        return None
    try:
        embedding = model.encode(text, normalize_embeddings=True)
        return embedding.tolist()
    except Exception as e:
        logger.warning(f"[Embedding] encode failed: {e}")
        return None


class OutlineEmbeddingService:

    async def generate_embeddings_for_project(
        self, project_id: uuid.UUID, db: AsyncSession
    ) -> int:
        if not PGVECTOR_AVAILABLE:
            logger.info("[Embedding] pgvector not available, skipping embedding generation")
            return 0

        project = await db.get(Project, project_id)
        outline = project.outline_data or {}
        chapters = outline.get("chapters", [])
        if not chapters:
            await self.delete_project_embeddings(project_id, db)
            return 0

        generated = 0
        for ch in chapters:
            ch_num = ch.get("chapter_number", 0)
            summary = self._build_chapter_summary(ch)
            if not summary.strip():
                continue

            embedding = _encode(summary)
            if embedding is None:
                continue

            await self._upsert_chunk(project_id, ch_num, "chapter_summary", summary, embedding, db)
            generated += 1

            for i, scene in enumerate(ch.get("scenes", [])):
                scene_text = self._build_scene_summary(ch_num, scene)
                if not scene_text.strip():
                    continue
                scene_embedding = _encode(scene_text)
                if scene_embedding is None:
                    continue
                await self._upsert_chunk(
                    project_id, ch_num, f"scene_{i+1}", scene_text, scene_embedding, db
                )
                generated += 1

        await db.commit()
        logger.info(f"[Embedding] Generated {generated} embeddings for project {project_id}")
        return generated

    async def search_semantic(
        self, project_id: uuid.UUID, query: str, db: AsyncSession, top_k: int = 5
    ) -> list[dict]:
        if not PGVECTOR_AVAILABLE:
            return []

        query_embedding = _encode(query)
        if query_embedding is None:
            return []

        try:
            stmt = (
                select(OutlineChunk)
                .where(OutlineChunk.project_id == project_id)
                .order_by(OutlineChunk.embedding.cosine_distance(query_embedding))
                .limit(top_k)
            )
            result = await db.execute(stmt)
            chunks = result.scalars().all()

            results = []
            for chunk in chunks:
                distance_stmt = (
                    select(OutlineChunk.embedding.cosine_distance(query_embedding).label("distance"))
                    .where(OutlineChunk.id == chunk.id)
                )
                dist_result = await db.execute(distance_stmt)
                distance = dist_result.scalar_one()
                similarity = 1.0 - distance

                results.append({
                    "chapter_number": chunk.chapter_number,
                    "chunk_type": chunk.chunk_type,
                    "content": chunk.content,
                    "similarity": round(similarity, 4),
                })
            return results
        except Exception as e:
            logger.warning(f"[Embedding] semantic search failed: {e}")
            return []

    async def delete_project_embeddings(
        self, project_id: uuid.UUID, db: AsyncSession
    ) -> int:
        stmt = delete(OutlineChunk).where(OutlineChunk.project_id == project_id)
        result = await db.execute(stmt)
        await db.commit()
        return result.rowcount

    async def _upsert_chunk(
        self,
        project_id: uuid.UUID,
        chapter_number: int,
        chunk_type: str,
        content: str,
        embedding: list[float],
        db: AsyncSession,
    ):
        stmt = select(OutlineChunk).where(
            OutlineChunk.project_id == project_id,
            OutlineChunk.chapter_number == chapter_number,
            OutlineChunk.chunk_type == chunk_type,
        )
        result = await db.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            existing.content = content
            existing.embedding = embedding
            existing.created_at = datetime.now(timezone.utc)
        else:
            chunk = OutlineChunk(
                project_id=project_id,
                chapter_number=chapter_number,
                chunk_type=chunk_type,
                content=content,
                embedding=embedding,
                created_at=datetime.now(timezone.utc),
            )
            db.add(chunk)

    def _build_chapter_summary(self, ch: dict) -> str:
        parts = [
            f"第{ch.get('chapter_number', '')}章：{ch.get('title', '')}",
            f"核心冲突：{ch.get('main_conflict', ch.get('core_conflict', ''))}",
            f"价值转变：{ch.get('value_shift', '')}",
        ]
        pov = ch.get("pov_character", "")
        if pov:
            parts.append(f"视角角色：{pov}")
        for f in ch.get("thread_ops", []):
            parts.append(f"伏笔[{f.get('action', '')}]：{f.get('name', '')}")
        for scene in ch.get("scenes", []):
            goal = scene.get("goal", "")
            conflict = scene.get("conflict", "")
            if goal or conflict:
                parts.append(f"场景：{goal} - {conflict}")
        return "\n".join(p for p in parts if p and not p.endswith("："))

    def _build_scene_summary(self, chapter_number: int, scene: dict) -> str:
        parts = [f"第{chapter_number}章场景"]
        goal = scene.get("goal", "")
        if goal:
            parts.append(f"目标：{goal}")
        conflict = scene.get("conflict", "")
        if conflict:
            parts.append(f"冲突：{conflict}")
        outcome = scene.get("outcome", "")
        if outcome:
            parts.append(f"结果：{outcome}")
        return " | ".join(p for p in parts if p)
