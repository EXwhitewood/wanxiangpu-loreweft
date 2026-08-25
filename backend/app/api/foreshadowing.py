import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.db_models import ForeshadowingCausalEdge, ForeshadowingLine, get_db
from app.services.cross_system_event_bus import CrossSystemEventBus
from app.services.foreshadowing_service import ForeshadowingService

router = APIRouter()
logger = logging.getLogger(__name__)


async def _require_project_line(
    db: AsyncSession,
    project_id: uuid.UUID,
    line_id: uuid.UUID,
) -> ForeshadowingLine:
    line = (
        await db.execute(
            select(ForeshadowingLine).where(
                ForeshadowingLine.id == line_id,
                ForeshadowingLine.project_id == project_id,
            )
        )
    ).scalar_one_or_none()
    if not line:
        raise HTTPException(status_code=404, detail="Foreshadowing line not found")
    return line


class ForeshadowingCreateRequest(BaseModel):
    name: str
    description: str = ""
    status: str = "planned"
    priority: str = "moderate"
    secret: dict[str, Any] | None = None
    timeline: dict[str, Any] | None = None
    narrative_structure: dict[str, Any] | None = None


class StatusUpdateRequest(BaseModel):
    new_status: str
    changed_by: str = "author"


class CognitiveStateRequest(BaseModel):
    foreshadowing_line_id: uuid.UUID
    character_name: str
    cognitive_status: str
    cognitive_level: str | None = None
    chapter_number: int = 1
    event: str = ""


class ClueCreateRequest(BaseModel):
    foreshadowing_line_id: uuid.UUID
    clue_type: str = "supportive"
    chapter_number: int
    clue_text: str
    pov_character: str = ""
    scene_index: int = 0
    salience_at_time: str = "subtle"


class EdgeCreateRequest(BaseModel):
    source_id: uuid.UUID
    target_id: uuid.UUID
    edge_type: str
    weight: float = 1.0
    narrative_justification: str = ""
    active_from_chapter: int | None = None
    active_until_chapter: int | None = None


class DetectRequest(BaseModel):
    chapter_number: int
    generated_text: str


@router.get("/lines")
async def list_lines(
    project_id: uuid.UUID,
    status: str | None = None,
    priority: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    service = ForeshadowingService()
    lines = await service.list_foreshadowing_lines(project_id, db, status=status, priority=priority)
    return {"lines": lines, "total": len(lines)}


@router.post("/lines")
async def create_line(
    project_id: uuid.UUID,
    data: ForeshadowingCreateRequest,
    db: AsyncSession = Depends(get_db),
):
    service = ForeshadowingService()
    payload = data.model_dump()
    if not payload.get("secret"):
        payload["secret"] = {
            "canonical_statement": data.description or data.name,
            "truth_type": "past_event",
            "impact_level": data.priority,
            "spoiler_scope": {},
        }
    line = await service.create_foreshadowing_line(project_id, payload, db)
    await db.commit()

    try:
        event_bus = CrossSystemEventBus()
        await event_bus.publish_event(
            db=db,
            project_id=str(project_id),
            event_type="FORESHADOWING_WINDOW_CHANGED.v1",
            source_system="worldbuilder",
            priority="normal",
            payload={
                "entity_type": "foreshadowing",
                "entity_id": str(line.get("id", "")),
                "entity_name": line.get("name", payload.get("name", "")),
                "change_type": "created",
                "summary": f"新建伏笔线：{line.get('name', payload.get('name', ''))}",
                "meta": {
                    "status": line.get("status"),
                    "bury_window_start": line.get("bury_window_start"),
                    "reveal_window_start": line.get("reveal_window_start"),
                },
            },
        )
    except Exception:
        pass

    return {"line": line}


@router.get("/lines/{line_id}")
async def get_line(
    project_id: uuid.UUID,
    line_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    await _require_project_line(db, project_id, line_id)
    service = ForeshadowingService()
    line = await service.get_foreshadowing_by_id(line_id, db)
    if not line:
        raise HTTPException(status_code=404, detail="Foreshadowing line not found")
    return {"line": line}


@router.patch("/lines/{line_id}/status")
async def update_status(
    project_id: uuid.UUID,
    line_id: uuid.UUID,
    data: StatusUpdateRequest,
    db: AsyncSession = Depends(get_db),
):
    service = ForeshadowingService()
    await _require_project_line(db, project_id, line_id)
    try:
        line = await service.transition_state(project_id, line_id, data.new_status, data.changed_by, db)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await db.commit()

    try:
        event_bus = CrossSystemEventBus()
        await event_bus.publish_event(
            db=db,
            project_id=str(project_id),
            event_type="FORESHADOWING_WINDOW_CHANGED.v1",
            source_system="worldbuilder",
            priority="normal",
            payload={
                "entity_type": "foreshadowing",
                "entity_id": str(line_id),
                "entity_name": line.get("name", "") if isinstance(line, dict) else "",
                "change_type": "status_changed",
                "summary": f"伏笔状态已更新为 {data.new_status}",
                "meta": {
                    "new_status": data.new_status,
                    "changed_by": data.changed_by,
                },
            },
        )
    except Exception:
        pass

    return {"line": line}


@router.get("/lines/{line_id}/clues")
async def list_clues(
    project_id: uuid.UUID,
    line_id: uuid.UUID,
    clue_type: str | None = None,
    chapter_number: int | None = None,
    db: AsyncSession = Depends(get_db),
):
    await _require_project_line(db, project_id, line_id)
    from app.services.foreshadowing_clue_service import ForeshadowingClueService
    service = ForeshadowingClueService()
    clues = await service.list_clues(line_id, db, clue_type=clue_type, chapter_number=chapter_number)
    return {"clues": clues, "total": len(clues)}


@router.post("/lines/{line_id}/clues")
async def create_clue(
    project_id: uuid.UUID,
    line_id: uuid.UUID,
    data: ClueCreateRequest,
    db: AsyncSession = Depends(get_db),
):
    await _require_project_line(db, project_id, line_id)
    if data.foreshadowing_line_id != line_id:
        raise HTTPException(status_code=422, detail="正文中的伏笔 ID 与路由不一致")
    from app.services.foreshadowing_clue_service import ForeshadowingClueService
    service = ForeshadowingClueService()
    clue = await service.add_clue(
        project_id=project_id,
        foreshadowing_line_id=line_id,
        clue_type=data.clue_type,
        chapter_number=data.chapter_number,
        clue_text=data.clue_text,
        db=db,
        pov_character=data.pov_character,
        scene_index=data.scene_index,
        salience_at_time=data.salience_at_time,
    )
    return {"clue": clue}


@router.get("/lines/{line_id}/evidence-pool")
async def get_evidence_pool(
    project_id: uuid.UUID,
    line_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    await _require_project_line(db, project_id, line_id)
    from app.services.foreshadowing_clue_service import ForeshadowingClueService
    service = ForeshadowingClueService()
    pool = await service.get_evidence_pool(line_id, db)
    return pool


@router.get("/lines/{line_id}/readiness")
async def get_readiness(
    project_id: uuid.UUID,
    line_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    await _require_project_line(db, project_id, line_id)
    from app.engines.reveal_readiness import calculate_reveal_readiness
    result = await calculate_reveal_readiness(line_id, db)
    return result


@router.patch("/cognitive-map")
async def set_cognitive_state(
    project_id: uuid.UUID,
    data: CognitiveStateRequest,
    db: AsyncSession = Depends(get_db),
):
    await _require_project_line(db, project_id, data.foreshadowing_line_id)
    service = ForeshadowingService()
    state = await service.set_character_cognitive_state(
        project_id=project_id,
        foreshadowing_line_id=data.foreshadowing_line_id,
        character_name=data.character_name,
        cognitive_status=data.cognitive_status,
        cognitive_level=data.cognitive_level,
        chapter_number=data.chapter_number,
        event=data.event,
        db=db,
    )
    await db.commit()
    return {"state": state}


@router.get("/cognitive-map")
async def get_cognitive_map(
    project_id: uuid.UUID,
    character: str | None = None,
    line_id: uuid.UUID | None = None,
    chapter_number: int | None = None,
    db: AsyncSession = Depends(get_db),
):
    service = ForeshadowingService()
    if character:
        states = await service.get_character_cognitive_states(
            project_id, character, db, chapter_number=chapter_number
        )
        return {"character_name": character, "states": states}
    if line_id:
        await _require_project_line(db, project_id, line_id)
        states = await service.list_cognitive_states_for_foreshadowing(
            project_id, line_id, db, chapter_number=chapter_number
        )
        return {"foreshadowing_line_id": line_id, "states": states}
    raise HTTPException(status_code=400, detail="character or line_id is required")


@router.get("/graph")
async def get_graph(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    from app.services.foreshadowing_graph_service import ForeshadowingGraphService
    service = ForeshadowingGraphService()
    return await service.get_graph_as_dict(project_id, db)


@router.post("/graph/edges")
async def create_edge(
    project_id: uuid.UUID,
    data: EdgeCreateRequest,
    db: AsyncSession = Depends(get_db),
):
    from app.services.foreshadowing_graph_service import ForeshadowingGraphService
    await _require_project_line(db, project_id, data.source_id)
    await _require_project_line(db, project_id, data.target_id)
    service = ForeshadowingGraphService()
    try:
        edge = await service.add_edge(
            project_id=project_id,
            source_id=data.source_id,
            target_id=data.target_id,
            edge_type=data.edge_type,
            db=db,
            weight=data.weight,
            narrative_justification=data.narrative_justification,
            active_from_chapter=data.active_from_chapter,
            active_until_chapter=data.active_until_chapter,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"edge": edge}


@router.delete("/graph/edges/{edge_id}")
async def delete_edge(
    project_id: uuid.UUID,
    edge_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    from app.services.foreshadowing_graph_service import ForeshadowingGraphService
    edge = (
        await db.execute(
            select(ForeshadowingCausalEdge).where(
                ForeshadowingCausalEdge.id == edge_id,
                ForeshadowingCausalEdge.project_id == project_id,
            )
        )
    ).scalar_one_or_none()
    if not edge:
        raise HTTPException(status_code=404, detail="Edge not found")
    service = ForeshadowingGraphService()
    removed = await service.remove_edge(edge_id, db)
    if not removed:
        raise HTTPException(status_code=404, detail="Edge not found")
    return {"removed": True}


@router.get("/graph/conflicts")
async def get_conflicts(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    from app.services.foreshadowing_graph_service import ForeshadowingGraphService
    service = ForeshadowingGraphService()
    conflicts = await service.detect_conflicts(project_id, db)
    return {"conflicts": conflicts, "total": len(conflicts)}


@router.get("/budget")
async def get_budget(
    project_id: uuid.UUID,
    chapter_number: int,
    pov_character: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    from app.engines.fcip_engine import FCIPEngine
    service = ForeshadowingService()
    fcip = FCIPEngine(service)
    ctx = await fcip.build_context(project_id, chapter_number, db, pov_character=pov_character)
    return await fcip.check_budget(ctx, db)


@router.post("/detect")
async def detect_violations(
    project_id: uuid.UUID,
    data: DetectRequest,
    db: AsyncSession = Depends(get_db),
):
    from app.engines.fcip_engine import FCIPEngine
    service = ForeshadowingService()
    fcip = FCIPEngine(service)
    result = await fcip.post_gen_writeback(
        project_id=project_id,
        chapter_number=data.chapter_number,
        generated_text=data.generated_text,
        db=db,
    )
    return result


@router.get("/dashboard")
async def get_dashboard(
    project_id: uuid.UUID,
    chapter_number: int | None = None,
    db: AsyncSession = Depends(get_db),
):
    from app.services.foreshadowing_graph_service import ForeshadowingGraphService
    from app.engines.reveal_readiness import calculate_reveal_readiness

    service = ForeshadowingService()
    graph_service = ForeshadowingGraphService()

    lines = await service.list_foreshadowing_lines(project_id, db)

    recycle_progress: dict[str, int] = {}
    for line in lines:
        s = line.get("status", "planned")
        recycle_progress[s] = recycle_progress.get(s, 0) + 1

    cognitive_distribution: dict[str, int] = {}
    for line in lines:
        line_id = line.get("id")
        if not line_id:
            continue
        states = await service.list_cognitive_states_for_foreshadowing(
            project_id, line_id, db, chapter_number=chapter_number,
        )
        for s in states:
            level = s.get("cognitive_level", "fully_blind")
            cognitive_distribution[level] = cognitive_distribution.get(level, 0) + 1

    graph_data = await graph_service.get_graph_as_dict(project_id, db)

    readiness_ranking = []
    for line in lines:
        line_id = line.get("id")
        if not line_id:
            continue
        if line.get("status") in ("active", "dormant", "revealing"):
            try:
                r = await calculate_reveal_readiness(line_id, db, service, graph_service=graph_service)
                readiness_ranking.append({
                    "id": str(line_id),
                    "name": line.get("name", ""),
                    "readiness": r.get("readiness", 0),
                    "ready": r.get("ready", False),
                })
            except Exception:
                pass
    readiness_ranking.sort(key=lambda x: x["readiness"], reverse=True)

    return {
        "network": {"nodes": graph_data.get("nodes", []), "edges": graph_data.get("edges", [])},
        "cognitive_distribution": cognitive_distribution,
        "recycle_progress": {"total": len(lines), **recycle_progress},
        "conflicts": graph_data.get("conflicts", []),
        "reveal_readiness_ranking": readiness_ranking[:10],
    }



