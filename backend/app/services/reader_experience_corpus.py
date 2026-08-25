from __future__ import annotations

import hashlib
import io
import json
import os
import re
import uuid
import zipfile
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
from typing import Iterable
from xml.etree import ElementTree

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.db_models import (
    ReaderCorpusChapterORM,
    ReaderCorpusImportJobORM,
    ReaderCorpusSourceORM,
    ReaderExperiencePatternORM,
)
from app.models.reader_experience_corpus import (
    ReaderCorpusChapterMetrics,
    ReaderCorpusSource,
    ReaderCorpusUploadResult,
    ReaderExperienceGuidance,
)
from app.utils.dash_artifacts import count_dash_artifacts


_SUPPORTED_SUFFIXES = {".txt", ".md", ".epub", ".docx", ".zip"}
_CHAPTER_HEADING_RE = re.compile(
    r"^\s*(第[零〇一二三四五六七八九十百千万\d]+[章节回卷幕].{0,40}|Chapter\s+\d+.{0,40})\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_SENTENCE_RE = re.compile(r"[^。！？!?；;\n]+[。！？!?；;]?")
_DIALOGUE_RE = re.compile(r"[“「『](.*?)[”」』]|\"([^\"]{2,})\"")
_HTML_TAG_RE = re.compile(r"<[^>]+>")

_ACTION_WORDS = {
    "走", "跑", "推", "拉", "抬", "按", "握", "看", "听", "退", "进", "转",
    "躲", "砸", "拦", "追", "抓", "放", "取", "翻", "站", "坐", "伸", "碰",
}
_CONFLICT_WORDS = {
    "危险", "威胁", "阻止", "不能", "必须", "逼", "压", "争", "冲突", "怀疑",
    "秘密", "背叛", "惩罚", "代价", "失败", "死", "逃", "追", "困", "错",
}
_CURIOSITY_WORDS = {
    "为什么", "怎么", "是谁", "哪里", "秘密", "真相", "异样", "不对", "奇怪",
    "线索", "发现", "疑问", "藏", "隐瞒", "未知", "忽然", "突然",
}
_EXPOSITION_WORDS = {
    "原来", "其实", "显然", "说明", "意味着", "换句话说", "也就是说", "这代表",
    "她知道", "他知道", "意识到", "想明白", "回忆", "记忆", "设定",
}
_AI_ARTIFACT_WORDS = {
    "不是", "而是", "仿佛", "像是", "某种", "一种", "显得", "内心", "情绪",
    "复杂", "平静", "冷静", "——",
}


@dataclass(frozen=True)
class ParsedBook:
    title: str
    text: str
    suffix: str
    raw_name: str


@dataclass(frozen=True)
class ParsedChapter:
    index: int
    title: str
    text: str


def supported_suffix(filename: str) -> str:
    _, suffix = os.path.splitext(filename or "")
    return suffix.lower()


def decode_book_bytes(content: bytes, suffix: str) -> str:
    suffix = suffix.lower()
    if suffix in {".txt", ".md"}:
        for encoding in ("utf-8-sig", "utf-8", "gbk", "gb2312", "big5"):
            try:
                return content.decode(encoding)
            except UnicodeDecodeError:
                continue
        raise ValueError("无法识别文本编码")

    if suffix == ".epub":
        return _parse_epub(content)

    if suffix == ".docx":
        try:
            from docx import Document
        except Exception as exc:  # pragma: no cover - dependency is optional at runtime
            raise ValueError(f"DOCX解析依赖不可用: {exc}") from exc
        doc = Document(io.BytesIO(content))
        return "\n".join(p.text.strip() for p in doc.paragraphs if p.text.strip())

    raise ValueError(f"不支持的文件格式: {suffix}")


def split_chapters(text: str, *, max_chunk_chars: int = 9000) -> list[ParsedChapter]:
    normalized = normalize_text(text)
    if not normalized:
        return []

    matches = list(_CHAPTER_HEADING_RE.finditer(normalized))
    chapters: list[ParsedChapter] = []
    if matches:
        for idx, match in enumerate(matches):
            start = match.end()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(normalized)
            body = normalized[start:end].strip()
            if len(body) < 40:
                continue
            chapters.append(ParsedChapter(index=len(chapters) + 1, title=match.group(1).strip(), text=body))
        if chapters:
            return chapters

    paragraphs = [p.strip() for p in normalized.split("\n") if p.strip()]
    current: list[str] = []
    current_len = 0
    chunk_index = 1
    for paragraph in paragraphs:
        if current and current_len + len(paragraph) > max_chunk_chars:
            chapters.append(ParsedChapter(
                index=chunk_index,
                title=f"自动切分 {chunk_index}",
                text="\n".join(current).strip(),
            ))
            chunk_index += 1
            current = []
            current_len = 0
        current.append(paragraph)
        current_len += len(paragraph)
    if current:
        chapters.append(ParsedChapter(
            index=chunk_index,
            title=f"自动切分 {chunk_index}",
            text="\n".join(current).strip(),
        ))
    return chapters


def normalize_text(text: str) -> str:
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def analyze_chapter(
    chapter: ParsedChapter,
    *,
    genre: str = "",
    source_quality: str = "unknown",
    total_chapters: int = 1,
) -> tuple[ReaderCorpusChapterMetrics, list[str], dict, list[dict]]:
    text = chapter.text
    metrics = _compute_metrics(text)
    position = chapter_position(chapter.index, total_chapters)
    scene_pos = scene_position_from_chapter(chapter.index, total_chapters)
    tags = [position, scene_pos]
    if metrics.conflict_signal >= 0.35:
        tags.append("conflict_forward")
    if metrics.ending_hook_signal >= 0.35:
        tags.append("ending_hook")
    if metrics.dialogue_ratio >= 0.12:
        tags.append("dialogue_active")
    if metrics.exposition_ratio <= 0.28:
        tags.append("controlled_exposition")

    summary = {
        "chapter_position": position,
        "scene_position": scene_pos,
        "dominant_metrics": _dominant_metrics(metrics),
        "quality_tier": source_quality,
    }
    patterns = _patterns_from_metrics(
        metrics,
        genre=genre,
        chapter_position=position,
        scene_position=scene_pos,
        source_quality=source_quality,
    )
    return metrics, tags, summary, patterns


def chapter_position(index: int, total: int) -> str:
    if index <= 1:
        return "opening"
    if total and index >= max(1, total - 1):
        return "ending"
    if index <= 5:
        return "early"
    return "middle"


def scene_position_from_chapter(index: int, total: int) -> str:
    pos = chapter_position(index, total)
    if pos in {"opening", "early"}:
        return "opening"
    if pos == "ending":
        return "outro"
    return "middle"


def guidance_position(chapter_number: int, scene_index: int, scene_count: int | None = None) -> tuple[str, str]:
    if chapter_number <= 1:
        chapter_pos = "opening"
    elif chapter_number <= 5:
        chapter_pos = "early"
    else:
        chapter_pos = "middle"
    if scene_index <= 0:
        scene_pos = "opening"
    elif scene_count and scene_index >= scene_count - 1:
        scene_pos = "outro"
    else:
        scene_pos = "middle"
    return chapter_pos, scene_pos


class ReaderExperienceCorpusService:
    raw_root = os.path.join("data", "private_corpus", "raw")

    async def ingest_upload(
        self,
        db: AsyncSession,
        *,
        filename: str,
        content: bytes,
        genre: str = "",
        platform: str = "",
        quality_tier: str = "A",
        author: str = "",
        tags: list[str] | None = None,
        allowed_uses: list[str] | None = None,
        notes: str = "",
    ) -> ReaderCorpusUploadResult:
        suffix = supported_suffix(filename)
        if suffix not in _SUPPORTED_SUFFIXES:
            return ReaderCorpusUploadResult(imported=0, failed=1, errors=[f"{filename}: 不支持的格式"])

        if suffix == ".zip":
            return await self._ingest_zip(
                db,
                filename=filename,
                content=content,
                genre=genre,
                platform=platform,
                quality_tier=quality_tier,
                author=author,
                tags=tags or [],
                allowed_uses=allowed_uses,
                notes=notes,
            )

        try:
            text = decode_book_bytes(content, suffix)
            source = await self._store_book(
                db,
                ParsedBook(
                    title=_title_from_filename(filename),
                    text=text,
                    suffix=suffix,
                    raw_name=filename,
                ),
                raw_content=content,
                genre=genre,
                platform=platform,
                quality_tier=quality_tier,
                author=author,
                tags=tags or [],
                allowed_uses=allowed_uses,
                notes=notes,
            )
        except Exception as exc:
            return ReaderCorpusUploadResult(imported=0, failed=1, errors=[f"{filename}: {exc}"])

        await db.commit()
        return ReaderCorpusUploadResult(imported=1, failed=0, sources=[source])

    async def import_folder(
        self,
        db: AsyncSession,
        *,
        folder_path: str,
        genre: str = "",
        platform: str = "",
        quality_tier: str = "A",
        tags: list[str] | None = None,
    ) -> dict:
        folder_path = folder_path or os.path.join("data", "private_corpus", "inbox")
        if not os.path.isdir(folder_path):
            raise ValueError(f"导入目录不存在: {folder_path}")

        job = ReaderCorpusImportJobORM(
            id=str(uuid.uuid4()),
            source=folder_path,
            status="running",
            total_files=0,
            processed_files=0,
            failed_files=0,
            report={},
        )
        db.add(job)
        await db.flush()

        files = []
        for root, _, names in os.walk(folder_path):
            for name in names:
                if supported_suffix(name) in _SUPPORTED_SUFFIXES:
                    files.append(os.path.join(root, name))
        job.total_files = len(files)

        imported = []
        errors = []
        for path in files:
            try:
                with open(path, "rb") as fh:
                    content = fh.read()
                result = await self.ingest_upload(
                    db,
                    filename=os.path.basename(path),
                    content=content,
                    genre=genre,
                    platform=platform,
                    quality_tier=quality_tier,
                    tags=tags or [],
                )
                imported.extend(result.sources)
                errors.extend(result.errors)
                job.processed_files += result.imported
                job.failed_files += result.failed
            except Exception as exc:
                errors.append(f"{path}: {exc}")
                job.failed_files += 1

        job.status = "completed" if not errors else "completed_with_errors"
        job.completed_at = datetime.now(timezone.utc)
        job.report = {"imported": len(imported), "errors": errors[:100]}
        await db.commit()
        return {
            "job_id": job.id,
            "imported": len(imported),
            "failed": len(errors),
            "errors": errors[:100],
        }

    async def delete_source(self, db: AsyncSession, source_id: str) -> bool:
        source = await db.get(ReaderCorpusSourceORM, source_id)
        if not source:
            return False
        raw_path = source.raw_path
        await db.delete(source)
        await db.commit()
        if raw_path:
            try:
                os.remove(raw_path)
            except OSError:
                pass
        return True

    async def rebuild_patterns(self, db: AsyncSession) -> dict:
        await db.execute(delete(ReaderExperiencePatternORM))
        result = await db.execute(select(ReaderCorpusChapterORM, ReaderCorpusSourceORM).join(
            ReaderCorpusSourceORM,
            ReaderCorpusSourceORM.id == ReaderCorpusChapterORM.source_id,
        ))
        rows = result.all()
        count = 0
        for chapter, source in rows:
            metrics = ReaderCorpusChapterMetrics(**(chapter.metrics or {}))
            patterns = _patterns_from_metrics(
                metrics,
                genre=source.genre or "",
                chapter_position=(chapter.pattern_summary or {}).get("chapter_position", "unknown"),
                scene_position=(chapter.pattern_summary or {}).get("scene_position", "any"),
                source_quality=source.quality_tier or "unknown",
            )
            for pattern in patterns:
                db.add(ReaderExperiencePatternORM(
                    id=str(uuid.uuid4()),
                    source_id=source.id,
                    chapter_id=chapter.id,
                    **pattern,
                ))
                count += 1
        await db.commit()
        return {"rebuilt_patterns": count, "chapter_count": len(rows)}

    async def list_sources(self, db: AsyncSession, *, limit: int = 100, offset: int = 0) -> dict:
        result = await db.execute(
            select(ReaderCorpusSourceORM)
            .order_by(ReaderCorpusSourceORM.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        rows = result.scalars().all()
        total = await db.scalar(select(func.count(ReaderCorpusSourceORM.id)))
        return {
            "items": [self._source_to_model(row).model_dump(mode="json") for row in rows],
            "total": int(total or 0),
        }

    async def list_patterns(
        self,
        db: AsyncSession,
        *,
        genre: str = "",
        pattern_type: str = "",
        limit: int = 100,
    ) -> dict:
        stmt = select(ReaderExperiencePatternORM)
        filters = []
        if genre:
            filters.append(or_(ReaderExperiencePatternORM.genre == genre, ReaderExperiencePatternORM.genre == ""))
        if pattern_type:
            filters.append(ReaderExperiencePatternORM.pattern_type == pattern_type)
        for item in filters:
            stmt = stmt.where(item)
        stmt = stmt.order_by(ReaderExperiencePatternORM.score.desc()).limit(limit)
        result = await db.execute(stmt)
        rows = result.scalars().all()
        return {
            "items": [self._pattern_to_dict(row) for row in rows],
            "total": len(rows),
        }

    async def summary(self, db: AsyncSession) -> dict:
        source_count = int(await db.scalar(select(func.count(ReaderCorpusSourceORM.id))) or 0)
        chapter_count = int(await db.scalar(select(func.count(ReaderCorpusChapterORM.id))) or 0)
        pattern_count = int(await db.scalar(select(func.count(ReaderExperiencePatternORM.id))) or 0)
        genre_rows = await db.execute(
            select(ReaderCorpusSourceORM.genre, func.count(ReaderCorpusSourceORM.id))
            .group_by(ReaderCorpusSourceORM.genre)
        )
        pattern_rows = await db.execute(
            select(ReaderExperiencePatternORM.pattern_type, func.count(ReaderExperiencePatternORM.id))
            .group_by(ReaderExperiencePatternORM.pattern_type)
        )
        return {
            "source_count": source_count,
            "chapter_count": chapter_count,
            "pattern_count": pattern_count,
            "genres": {str(k or "未标注"): int(v or 0) for k, v in genre_rows.all()},
            "pattern_types": {str(k or "unknown"): int(v or 0) for k, v in pattern_rows.all()},
        }

    async def build_generation_guidance(
        self,
        db: AsyncSession | None,
        *,
        project=None,
        genre: str = "",
        chapter_number: int = 1,
        scene_index: int = 0,
        scene_count: int | None = None,
        scene_contract: dict | None = None,
        limit: int = 5,
    ) -> ReaderExperienceGuidance:
        if settings.reader_corpus_mode == "off":
            return ReaderExperienceGuidance(mode="off")
        if db is None:
            return ReaderExperienceGuidance(mode="unavailable")
        effective_genre = (genre or getattr(project, "genre", "") or "").strip()
        chapter_pos, scene_pos = guidance_position(chapter_number, scene_index, scene_count)

        stmt = select(ReaderExperiencePatternORM)
        if effective_genre:
            stmt = stmt.where(or_(
                ReaderExperiencePatternORM.genre == effective_genre,
                ReaderExperiencePatternORM.genre == "",
            ))
        stmt = (
            stmt.where(
                or_(
                    ReaderExperiencePatternORM.chapter_position == chapter_pos,
                    ReaderExperiencePatternORM.chapter_position == "middle",
                    ReaderExperiencePatternORM.chapter_position == "unknown",
                ),
                or_(
                    ReaderExperiencePatternORM.scene_position == scene_pos,
                    ReaderExperiencePatternORM.scene_position == "any",
                ),
            )
            .order_by(ReaderExperiencePatternORM.score.desc())
            .limit(max(limit * 3, 12))
        )
        result = await db.execute(stmt)
        rows = result.scalars().all()
        selected = _dedupe_patterns(rows, limit=limit)
        if not selected:
            return ReaderExperienceGuidance(
                mode="assist",
                genre=effective_genre,
                chapter_position=chapter_pos,
                scene_position=scene_pos,
            )

        bullets: list[str] = []
        avoid: list[str] = []
        metrics_acc: dict[str, list[float]] = {}
        source_ids = set()
        pattern_ids = []
        for pattern in selected:
            pattern_ids.append(pattern.id)
            if pattern.source_id:
                source_ids.add(pattern.source_id)
            guidance = pattern.guidance or {}
            text = str(guidance.get("prompt") or guidance.get("summary") or "")
            if text:
                bullets.append(_compact(text, 120))
            for item in guidance.get("avoid", []) or []:
                avoid.append(_compact(str(item), 80))
            for key, value in (pattern.metrics or {}).items():
                if isinstance(value, (int, float)):
                    metrics_acc.setdefault(key, []).append(float(value))

        target_metrics = {
            key: round(sum(values) / len(values), 3)
            for key, values in metrics_acc.items()
            if values
        }
        return ReaderExperienceGuidance(
            mode="assist",
            genre=effective_genre,
            chapter_position=chapter_pos,
            scene_position=scene_pos,
            guidance=bullets[:limit],
            avoid_patterns=list(dict.fromkeys(avoid))[:4],
            target_metrics=target_metrics,
            pattern_ids=pattern_ids,
            source_count=len(source_ids),
            token_budget_chars=600,
        )

    @staticmethod
    def to_quality_extensions_patch(guidance: ReaderExperienceGuidance) -> dict:
        if not guidance.has_guidance:
            return {}
        return {
            "reader_corpus_guidance": guidance.guidance[:5],
            "reader_corpus_avoid": guidance.avoid_patterns[:4],
            "reader_corpus_targets": {
                "chapter_position": guidance.chapter_position,
                "scene_position": guidance.scene_position,
                "target_metrics": guidance.target_metrics,
                "pattern_ids": guidance.pattern_ids[:8],
                "source_count": guidance.source_count,
            },
        }

    async def _ingest_zip(self, db: AsyncSession, *, filename: str, content: bytes, **metadata) -> ReaderCorpusUploadResult:
        result = ReaderCorpusUploadResult()
        try:
            zf = zipfile.ZipFile(io.BytesIO(content))
        except zipfile.BadZipFile:
            return ReaderCorpusUploadResult(imported=0, failed=1, errors=[f"{filename}: ZIP文件损坏"])

        for entry in zf.namelist():
            if entry.endswith("/") or supported_suffix(entry) not in _SUPPORTED_SUFFIXES - {".zip"}:
                continue
            try:
                entry_bytes = zf.read(entry)
                partial = await self.ingest_upload(
                    db,
                    filename=os.path.basename(entry),
                    content=entry_bytes,
                    **metadata,
                )
                result.imported += partial.imported
                result.failed += partial.failed
                result.sources.extend(partial.sources)
                result.errors.extend(partial.errors)
            except Exception as exc:
                result.failed += 1
                result.errors.append(f"{entry}: {exc}")
        await db.commit()
        return result

    async def _store_book(
        self,
        db: AsyncSession,
        parsed: ParsedBook,
        *,
        raw_content: bytes,
        genre: str,
        platform: str,
        quality_tier: str,
        author: str,
        tags: list[str],
        allowed_uses: list[str] | None,
        notes: str,
    ) -> ReaderCorpusSource:
        chapters = split_chapters(parsed.text)
        if not chapters:
            raise ValueError("未能解析出有效章节")

        source_id = str(uuid.uuid4())
        raw_path = self._save_raw_file(source_id, parsed.raw_name, raw_content)
        source = ReaderCorpusSourceORM(
            id=source_id,
            title=parsed.title,
            author=author or "",
            genre=genre or "",
            platform=platform or "",
            quality_tier=quality_tier or "unknown",
            allowed_uses=allowed_uses or ["structure", "pacing", "reader_drive", "anti_ai"],
            status="ready",
            raw_path=raw_path,
            word_count=len(parsed.text),
            chapter_count=len(chapters),
            tags=tags or [],
            notes=notes or "",
        )
        db.add(source)
        await db.flush()

        for chapter in chapters:
            metrics, structure_tags, summary, patterns = analyze_chapter(
                chapter,
                genre=genre or "",
                source_quality=quality_tier or "unknown",
                total_chapters=len(chapters),
            )
            chapter_id = str(uuid.uuid4())
            chapter_obj = ReaderCorpusChapterORM(
                id=chapter_id,
                source_id=source_id,
                chapter_index=chapter.index,
                title=chapter.title,
                char_count=len(chapter.text),
                content_hash=_sha256(chapter.text),
                sample_excerpt=_sample_excerpt(chapter.text),
                metrics=metrics.model_dump(),
                structure_tags=structure_tags,
                pattern_summary=summary,
            )
            db.add(chapter_obj)
            for pattern in patterns:
                db.add(ReaderExperiencePatternORM(
                    id=str(uuid.uuid4()),
                    source_id=source_id,
                    chapter_id=chapter_id,
                    **pattern,
                ))

        await db.flush()
        return self._source_to_model(source)

    def _save_raw_file(self, source_id: str, filename: str, content: bytes) -> str:
        base_dir = self.raw_root
        if not os.path.isabs(base_dir):
            base_dir = os.path.join(os.getcwd(), base_dir)
        os.makedirs(base_dir, exist_ok=True)
        suffix = supported_suffix(filename) or ".txt"
        path = os.path.join(base_dir, f"{source_id}{suffix}")
        with open(path, "wb") as fh:
            fh.write(content)
        return path

    @staticmethod
    def _source_to_model(row: ReaderCorpusSourceORM) -> ReaderCorpusSource:
        return ReaderCorpusSource(
            id=row.id,
            title=row.title,
            author=row.author or "",
            genre=row.genre or "",
            platform=row.platform or "",
            quality_tier=row.quality_tier or "unknown",
            status=row.status or "ready",
            word_count=int(row.word_count or 0),
            chapter_count=int(row.chapter_count or 0),
            tags=list(row.tags or []),
            allowed_uses=list(row.allowed_uses or []),
            notes=row.notes or "",
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _pattern_to_dict(row: ReaderExperiencePatternORM) -> dict:
        return {
            "id": row.id,
            "source_id": row.source_id or "",
            "chapter_id": row.chapter_id or "",
            "genre": row.genre or "",
            "chapter_position": row.chapter_position,
            "scene_position": row.scene_position,
            "pattern_type": row.pattern_type,
            "pattern_key": row.pattern_key,
            "score": row.score,
            "evidence_count": row.evidence_count,
            "metrics": row.metrics or {},
            "guidance": row.guidance or {},
            "tags": row.tags or [],
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }


def _parse_epub(content: bytes) -> str:
    parts: list[str] = []
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        for name in sorted(zf.namelist()):
            if not name.lower().endswith((".html", ".xhtml", ".htm")):
                continue
            raw = zf.read(name)
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                text = raw.decode("utf-8", errors="ignore")
            text = unescape(_HTML_TAG_RE.sub("\n", text))
            text = re.sub(r"\n{2,}", "\n", text)
            if text.strip():
                parts.append(text.strip())
    if not parts:
        try:
            root = ElementTree.fromstring(content)
            parts = [elem.text.strip() for elem in root.iter() if elem.text and elem.text.strip()]
        except Exception:
            pass
    return "\n".join(parts)


def _compute_metrics(text: str) -> ReaderCorpusChapterMetrics:
    char_count = len(text)
    paragraphs = [p for p in text.split("\n") if p.strip()]
    sentences = [m.group(0).strip() for m in _SENTENCE_RE.finditer(text) if m.group(0).strip()]
    sentence_count = max(1, len(sentences))
    dialogue_chars = sum(len("".join(group for group in match if group)) for match in _DIALOGUE_RE.findall(text))
    action_hits = _count_hits(text, _ACTION_WORDS)
    conflict_hits = _count_hits(text, _CONFLICT_WORDS)
    curiosity_hits = _count_hits(text, _CURIOSITY_WORDS)
    exposition_hits = _count_hits(text, _EXPOSITION_WORDS)
    ai_hits = _count_hits(text, _AI_ARTIFACT_WORDS)
    dash_count = count_dash_artifacts(text)
    ending = text[-500:] if len(text) > 500 else text

    return ReaderCorpusChapterMetrics(
        char_count=char_count,
        paragraph_count=len(paragraphs),
        sentence_count=sentence_count,
        avg_sentence_chars=round(char_count / sentence_count, 2),
        dialogue_ratio=round(dialogue_chars / max(1, char_count), 3),
        action_ratio=round(action_hits / sentence_count, 3),
        description_ratio=round(_description_ratio(text, sentence_count), 3),
        exposition_ratio=round(exposition_hits / sentence_count, 3),
        conflict_signal=round(min(1.0, conflict_hits / max(4, sentence_count * 0.25)), 3),
        curiosity_signal=round(min(1.0, curiosity_hits / max(3, sentence_count * 0.18)), 3),
        ending_hook_signal=round(min(1.0, (_count_hits(ending, _CURIOSITY_WORDS | _CONFLICT_WORDS) + ending.count("？")) / 5), 3),
        ai_artifact_risk=round(min(1.0, ai_hits / max(5, sentence_count * 0.28)), 3),
        dash_density=round(dash_count / max(1, char_count / 1000), 3),
    )


def _patterns_from_metrics(
    metrics: ReaderCorpusChapterMetrics,
    *,
    genre: str,
    chapter_position: str,
    scene_position: str,
    source_quality: str,
) -> list[dict]:
    quality_weight = {"S": 1.2, "A": 1.0, "B": 0.85, "C": 0.65}.get(source_quality, 0.8)
    patterns: list[dict] = []

    def add(pattern_type: str, key: str, score: float, prompt: str, avoid: list[str] | None = None, tags: list[str] | None = None):
        patterns.append({
            "genre": genre or "",
            "chapter_position": chapter_position,
            "scene_position": scene_position,
            "pattern_type": pattern_type,
            "pattern_key": key,
            "score": round(max(0.1, min(1.0, score * quality_weight)), 3),
            "evidence_count": 1,
            "metrics": {
                "dialogue_ratio": metrics.dialogue_ratio,
                "action_ratio": metrics.action_ratio,
                "exposition_ratio": metrics.exposition_ratio,
                "conflict_signal": metrics.conflict_signal,
                "curiosity_signal": metrics.curiosity_signal,
                "ending_hook_signal": metrics.ending_hook_signal,
            },
            "guidance": {
                "summary": key,
                "prompt": prompt,
                "avoid": avoid or [],
            },
            "tags": tags or [],
        })

    if chapter_position in {"opening", "early"}:
        add(
            "opening_hook",
            "early_abnormal_or_pressure",
            max(metrics.curiosity_signal, metrics.conflict_signal, 0.45),
            "开场优先给可感知的异常、压力或目标，让读者在前几段带着问题进入场景。",
            ["连续背景说明", "先解释设定再发生事件"],
            ["opening", "reader_drive"],
        )

    if metrics.conflict_signal >= 0.25:
        add(
            "conflict_pressure",
            "pressure_through_obstacle_or_cost",
            metrics.conflict_signal,
            "每个场景至少让目标、阻碍、代价或关系压力之一发生可见变化。",
            ["只有氛围没有阻碍", "只解释危险而不让危险进入行动"],
            ["conflict"],
        )

    if metrics.curiosity_signal >= 0.25:
        add(
            "curiosity_engine",
            "partial_answer_then_new_question",
            metrics.curiosity_signal,
            "信息释放采用碎片兑现：先回答一个小疑问，再留下更具体的新问题。",
            ["一次性解释完整答案", "把谜底写成旁白总结"],
            ["suspense", "retention"],
        )

    if metrics.ending_hook_signal >= 0.25 or scene_position == "outro":
        add(
            "ending_hook",
            "stop_on_unresolved_pressure",
            max(metrics.ending_hook_signal, 0.5 if scene_position == "outro" else 0.25),
            "结尾停在未解决威胁、选择、发现或代价上，不要把情绪和解释全部收干净。",
            ["结尾总结主题", "把下一步行动完全交代完"],
            ["ending", "hook"],
        )

    if metrics.exposition_ratio <= 0.32:
        add(
            "info_release",
            "show_information_by_carrier",
            max(0.35, 1.0 - metrics.exposition_ratio),
            "背景和规则优先放进物件、动作、对话压力和后果里，而不是集中说明。",
            ["大段解释世界观", "用作者旁白替代角色发现"],
            ["anti_exposition"],
        )

    if metrics.ai_artifact_risk <= 0.45:
        add(
            "anti_ai",
            "concrete_scene_over_analysis",
            max(0.35, 1.0 - metrics.ai_artifact_risk),
            "心理和判断尽量落到身体反应、停顿、视线、触感、选择后果上。",
            ["不是X而是Y的连续解释", "抽象情绪标签堆叠", "破折号解释链"],
            ["anti_ai", "prose"],
        )

    if metrics.dialogue_ratio >= 0.08:
        add(
            "dialogue_pressure",
            "dialogue_with_intent_and_subtext",
            min(1.0, metrics.dialogue_ratio * 4),
            "对白要带目的、遮掩或试探，让信息在人物互相施压时出现。",
            ["问答式说明", "人物替作者讲设定"],
            ["dialogue"],
        )

    return patterns


def _dedupe_patterns(rows: Iterable[ReaderExperiencePatternORM], *, limit: int) -> list[ReaderExperiencePatternORM]:
    selected: list[ReaderExperiencePatternORM] = []
    seen_types: set[str] = set()
    seen_keys: set[str] = set()
    for row in rows:
        key = f"{row.pattern_type}:{row.pattern_key}"
        if key in seen_keys:
            continue
        if row.pattern_type in seen_types and len(selected) < max(2, limit - 1):
            continue
        selected.append(row)
        seen_keys.add(key)
        seen_types.add(row.pattern_type)
        if len(selected) >= limit:
            break
    return selected


def _dominant_metrics(metrics: ReaderCorpusChapterMetrics) -> list[str]:
    values = {
        "conflict": metrics.conflict_signal,
        "curiosity": metrics.curiosity_signal,
        "ending_hook": metrics.ending_hook_signal,
        "dialogue": metrics.dialogue_ratio,
        "action": metrics.action_ratio,
        "controlled_exposition": max(0.0, 1.0 - metrics.exposition_ratio),
    }
    return [name for name, _ in Counter(values).most_common()]


def _count_hits(text: str, words: set[str]) -> int:
    return sum(text.count(word) for word in words)


def _description_ratio(text: str, sentence_count: int) -> float:
    sensory_words = {"光", "风", "声", "冷", "热", "痛", "香", "臭", "影", "色", "手", "眼", "皮肤"}
    return min(1.0, _count_hits(text, sensory_words) / max(4, sentence_count * 0.25))


def _title_from_filename(filename: str) -> str:
    base = os.path.basename(filename or "未命名作品")
    return os.path.splitext(base)[0][:180]


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


def _sample_excerpt(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    return cleaned[:420]


def _compact(text: str, max_len: int) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_len]
