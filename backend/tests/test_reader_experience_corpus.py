from __future__ import annotations

import pytest
from sqlalchemy import select

from app.config import settings
from app.db.db_models import ReaderCorpusChapterORM, ReaderCorpusSourceORM, ReaderExperiencePatternORM
from app.services.reader_experience_corpus import (
    ReaderExperienceCorpusService,
    decode_book_bytes,
    split_chapters,
)


@pytest.mark.asyncio
async def test_reader_corpus_ingest_extracts_chapters_and_patterns(db, tmp_path):
    text = """
第一章 异常

门外忽然传来脚步声。她握住桌角，发现信纸上的字正在变淡。
这不对。为什么有人知道她藏在这里？她不能再等。

第二章 选择

他推开门，声音压得很低：“你现在走，还来得及。”
她没有回答，只看向窗外越来越近的火光。
"""
    service = ReaderExperienceCorpusService()
    service.raw_root = str(tmp_path)

    result = await service.ingest_upload(
        db,
        filename="sample.txt",
        content=text.encode("utf-8"),
        genre="悬疑",
        platform="internal",
        quality_tier="A",
        tags=["test"],
    )

    assert result.imported == 1
    source_count = await db.scalar(select(ReaderCorpusSourceORM).where(ReaderCorpusSourceORM.genre == "悬疑"))
    assert source_count is not None
    chapters = (await db.execute(select(ReaderCorpusChapterORM))).scalars().all()
    patterns = (await db.execute(select(ReaderExperiencePatternORM))).scalars().all()
    assert len(chapters) == 2
    assert patterns
    assert any(p.pattern_type == "opening_hook" for p in patterns)


@pytest.mark.asyncio
async def test_reader_corpus_guidance_disabled_by_default(db):
    service = ReaderExperienceCorpusService()
    guidance = await service.build_generation_guidance(
        db,
        genre="鎮枒",
        chapter_number=1,
        scene_index=0,
        limit=5,
    )

    assert settings.reader_corpus_mode == "off"
    assert guidance.mode == "off"
    assert not guidance.has_guidance


@pytest.mark.asyncio
async def test_reader_corpus_guidance_is_compact_and_aggregated(db, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "reader_corpus_mode", "assist")
    text = """
第一章

她醒来时，窗外的钟声已经停了。桌上多了一封没有署名的信。
信里只写了一句话：别相信今晚来找你的人。

第二章

脚步声逼近，她把信藏进袖口，假装没有看见门缝里的影子。
"""
    service = ReaderExperienceCorpusService()
    service.raw_root = str(tmp_path)
    await service.ingest_upload(
        db,
        filename="compact.txt",
        content=text.encode("utf-8"),
        genre="悬疑",
        quality_tier="S",
    )

    guidance = await service.build_generation_guidance(
        db,
        genre="悬疑",
        chapter_number=1,
        scene_index=0,
        limit=5,
    )

    assert guidance.has_guidance
    assert len(guidance.guidance) <= 5
    assert guidance.token_budget_chars <= 600
    patch = service.to_quality_extensions_patch(guidance)
    assert "reader_corpus_guidance" in patch
    assert len("\n".join(patch["reader_corpus_guidance"])) < 700
    assert "桌上多了一封" not in "\n".join(patch["reader_corpus_guidance"])

    fallback = await service.build_generation_guidance(
        db,
        genre="",
        chapter_number=1,
        scene_index=0,
        limit=3,
    )
    assert fallback.has_guidance


def test_reader_corpus_parser_supports_text_and_fallback_chunks():
    raw = "第一章\n" + "门外有人敲门。\n" * 80 + "\n第二章\n" + "她必须离开。\n" * 80
    decoded = decode_book_bytes(raw.encode("utf-8"), ".txt")
    chapters = split_chapters(decoded)
    assert len(chapters) == 2
    assert chapters[0].title.startswith("第一章")
