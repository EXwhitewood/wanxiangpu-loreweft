import logging

logger = logging.getLogger(__name__)


class EmbeddingService:
    """Unified text embedding service with a safe no-vector fallback."""

    dimension = 384

    async def embed_text(self, text: str) -> list[float] | None:
        if not text:
            return None
        try:
            from app.services.outline_embedding_service import _encode

            return _encode(text)
        except Exception as exc:
            logger.warning("[EmbeddingService] embedding unavailable: %s", exc)
            return None

