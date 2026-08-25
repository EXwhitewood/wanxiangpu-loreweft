"""对标拆解中心 API"""
from __future__ import annotations
from fastapi import APIRouter, HTTPException
from app.models.benchmark import (
    BenchmarkBook,
    BenchmarkChapter,
    BenchmarkDeconstructionRequest,
    BenchmarkStrategyCard,
)
from app.services.benchmark_deconstruction import get_benchmark_service

router = APIRouter()


@router.get("/books")
async def list_benchmark_books():
    service = get_benchmark_service()
    return service.list_books()


@router.post("/books")
async def register_benchmark_book(book: BenchmarkBook):
    service = get_benchmark_service()
    return service.register_book(book)


@router.get("/books/{book_id}")
async def get_benchmark_book(book_id: str):
    service = get_benchmark_service()
    book = service.get_book(book_id)
    if not book:
        raise HTTPException(status_code=404, detail="对标书不存在")
    return book


@router.put("/books/{book_id}/chapters")
async def replace_benchmark_chapters(book_id: str, chapters: list[BenchmarkChapter]):
    service = get_benchmark_service()
    result = service.set_chapters(book_id, chapters)
    if result is None:
        raise HTTPException(status_code=404, detail="对标书不存在")
    return result


@router.delete("/books/{book_id}")
async def delete_benchmark_book(book_id: str):
    service = get_benchmark_service()
    if not service.delete_book(book_id):
        raise HTTPException(status_code=404, detail="对标书不存在")
    return {"deleted": True}


@router.post("/books/{book_id}/bind/{project_id}")
async def bind_benchmark_to_project(book_id: str, project_id: str):
    service = get_benchmark_service()
    book = service.bind_to_project(book_id, project_id)
    if not book:
        raise HTTPException(status_code=404, detail="对标书不存在")
    return book


@router.post("/books/{book_id}/unbind/{project_id}")
async def unbind_benchmark_from_project(book_id: str, project_id: str):
    service = get_benchmark_service()
    book = service.unbind_from_project(book_id, project_id)
    if not book:
        raise HTTPException(status_code=404, detail="对标书不存在")
    return book


@router.get("/projects/{project_id}/books")
async def get_project_benchmark_books(project_id: str):
    service = get_benchmark_service()
    return service.get_project_books(project_id)


@router.post("/deconstruct")
async def start_deconstruction(request: BenchmarkDeconstructionRequest):
    service = get_benchmark_service()
    return await service.start_deconstruction(request)


@router.get("/books/{book_id}/result")
async def get_deconstruction_result(book_id: str):
    service = get_benchmark_service()
    result = service.get_deconstruction_result(book_id)
    if not result:
        raise HTTPException(status_code=404, detail="拆解结果不存在")
    return result.model_dump()


@router.get("/books/{book_id}/strategies")
async def get_benchmark_strategies(book_id: str):
    service = get_benchmark_service()
    return service.get_strategy_cards(book_id)


@router.get("/projects/{project_id}/strategies")
async def get_project_strategies(project_id: str):
    service = get_benchmark_service()
    return service.get_project_strategy_cards(project_id)
