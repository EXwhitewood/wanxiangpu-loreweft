from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.db_models import get_db
from app.services.reader_experience_corpus import ReaderExperienceCorpusService

router = APIRouter()


@router.get("/status")
async def get_reader_corpus_status():
    from app.config import settings
    return {
        "mode": settings.reader_corpus_mode,
        "description": "私有读者经验库" if settings.reader_corpus_mode != "off" else "私有读者经验库：已停用",
        "enabled": settings.reader_corpus_mode != "off",
    }


class ImportFolderRequest(BaseModel):
    folder_path: str = "data/private_corpus/inbox"
    genre: str = ""
    platform: str = ""
    quality_tier: str = "A"
    tags: list[str] = []


class GuidancePreviewRequest(BaseModel):
    genre: str = ""
    chapter_number: int = 1
    scene_index: int = 0
    scene_count: int | None = None
    scene_contract: dict = {}
    limit: int = 5


@router.get("/summary")
async def get_reader_corpus_summary(db: AsyncSession = Depends(get_db)):
    return await ReaderExperienceCorpusService().summary(db)


@router.get("/sources")
async def list_reader_corpus_sources(
    limit: int = 100,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    return await ReaderExperienceCorpusService().list_sources(db, limit=limit, offset=offset)


@router.post("/upload")
async def upload_reader_corpus(
    files: list[UploadFile] = File(...),
    genre: str = Form(""),
    platform: str = Form(""),
    quality_tier: str = Form("A"),
    author: str = Form(""),
    tags: str = Form(""),
    allowed_uses: str = Form(""),
    notes: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    service = ReaderExperienceCorpusService()
    parsed_tags = _parse_csv_or_json_list(tags)
    parsed_allowed_uses = _parse_csv_or_json_list(allowed_uses) or None
    imported = 0
    failed = 0
    sources = []
    errors = []
    for file in files:
        content = await file.read()
        result = await service.ingest_upload(
            db,
            filename=file.filename or "uploaded.txt",
            content=content,
            genre=genre,
            platform=platform,
            quality_tier=quality_tier,
            author=author,
            tags=parsed_tags,
            allowed_uses=parsed_allowed_uses,
            notes=notes,
        )
        imported += result.imported
        failed += result.failed
        sources.extend([item.model_dump(mode="json") for item in result.sources])
        errors.extend(result.errors)
    return {
        "imported": imported,
        "failed": failed,
        "sources": sources,
        "errors": errors,
    }


@router.post("/import-folder")
async def import_reader_corpus_folder(
    data: ImportFolderRequest,
    db: AsyncSession = Depends(get_db),
):
    try:
        return await ReaderExperienceCorpusService().import_folder(
            db,
            folder_path=data.folder_path,
            genre=data.genre,
            platform=data.platform,
            quality_tier=data.quality_tier,
            tags=data.tags,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/rebuild-patterns")
async def rebuild_reader_corpus_patterns(db: AsyncSession = Depends(get_db)):
    return await ReaderExperienceCorpusService().rebuild_patterns(db)


@router.get("/patterns")
async def list_reader_corpus_patterns(
    genre: str = "",
    pattern_type: str = "",
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
):
    return await ReaderExperienceCorpusService().list_patterns(
        db,
        genre=genre,
        pattern_type=pattern_type,
        limit=limit,
    )


@router.post("/guidance/preview")
async def preview_reader_corpus_guidance(
    data: GuidancePreviewRequest,
    db: AsyncSession = Depends(get_db),
):
    guidance = await ReaderExperienceCorpusService().build_generation_guidance(
        db,
        genre=data.genre,
        chapter_number=data.chapter_number,
        scene_index=data.scene_index,
        scene_count=data.scene_count,
        scene_contract=data.scene_contract,
        limit=data.limit,
    )
    return guidance.model_dump(mode="json")


@router.delete("/sources/{source_id}")
async def delete_reader_corpus_source(
    source_id: str,
    db: AsyncSession = Depends(get_db),
):
    deleted = await ReaderExperienceCorpusService().delete_source(db, source_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="语料不存在")
    return {"deleted": True}


def _parse_csv_or_json_list(raw: str) -> list[str]:
    raw = (raw or "").strip()
    if not raw:
        return []
    if raw.startswith("["):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = []
        if isinstance(data, list):
            return [str(item).strip() for item in data if str(item).strip()]
    return [item.strip() for item in raw.replace("，", ",").split(",") if item.strip()]

