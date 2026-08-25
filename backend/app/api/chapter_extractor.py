"""章节结构抽取 API"""
from __future__ import annotations
from fastapi import APIRouter
from app.models.chapter_structure import ChapterExtractionInput, ChapterExtractionResult
from app.services.chapter_extractor import get_chapter_extractor

router = APIRouter()


@router.post("/extract", response_model=ChapterExtractionResult)
async def extract_chapter_structure(input_data: ChapterExtractionInput):
    """抽取章节结构"""
    extractor = get_chapter_extractor()
    return await extractor.extract(input_data)
