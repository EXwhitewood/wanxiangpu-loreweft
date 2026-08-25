from fastapi import APIRouter, Query

from app.repositories.factory import create_search_service

router = APIRouter(tags=["search"])


@router.post("/ensure-index")
async def ensure_index():
    service = create_search_service()
    await service.ensure_index()
    return {"status": "ok"}


@router.post("/seeds")
async def index_seed(seed_data: dict):
    service = create_search_service()
    result = await service.index_detail_seed(seed_data)
    return result


@router.post("/seeds/bulk")
async def bulk_index_seeds(seeds: list[dict]):
    service = create_search_service()
    result = await service.bulk_index_seeds(seeds)
    return result


@router.delete("/seeds/{seed_id}")
async def delete_seed(seed_id: str):
    service = create_search_service()
    success = await service.delete_seed(seed_id)
    return {"success": success}


@router.patch("/seeds/{seed_id}")
async def update_seed(seed_id: str, data: dict):
    service = create_search_service()
    result = await service.update_seed(seed_id, data)
    return result


@router.get("/text")
async def search_text(
    project_id: str,
    query: str,
    limit: int = Query(default=10, ge=1, le=100),
    tier: str | None = None,
    entity_id: str | None = None,
):
    service = create_search_service()
    filters = {}
    if tier:
        filters["tier"] = tier
    if entity_id:
        filters["entity_id"] = entity_id
    results = await service.search_text(project_id, query, limit, filters or None)
    return {"results": results}


@router.get("/spatiotemporal")
async def search_spatiotemporal(
    project_id: str,
    limit: int = Query(default=10, ge=1, le=100),
    scene_gte: int | None = None,
    scene_lte: int | None = None,
    location: str | None = None,
):
    service = create_search_service()
    scene_range = (scene_gte, scene_lte) if scene_gte is not None and scene_lte is not None else None
    results = await service.search_spatiotemporal(
        project_id, scene_range=scene_range, location=location, limit=limit
    )
    return {"results": results}


@router.get("/combined")
async def search_combined(
    project_id: str,
    query: str,
    limit: int = Query(default=10, ge=1, le=100),
    tier: str | None = None,
):
    service = create_search_service()
    results = await service.search_combined(project_id, query, tier=tier, limit=limit)
    return {"results": results}


@router.get("/fuzzy")
async def search_fuzzy(
    project_id: str,
    query: str,
    limit: int = Query(default=10, ge=1, le=100),
    fuzziness: str = "AUTO",
):
    service = create_search_service()
    results = await service.search_fuzzy(project_id, query, fuzziness, limit)
    return {"results": results}


@router.get("/aggregate/entity")
async def aggregate_by_entity(
    project_id: str,
    entity_type: str | None = None,
):
    service = create_search_service()
    results = await service.aggregate_by_entity(project_id, entity_type)
    return {"results": results}


@router.get("/aggregate/chapter")
async def aggregate_by_chapter(project_id: str):
    service = create_search_service()
    results = await service.aggregate_by_chapter(project_id)
    return {"results": results}
