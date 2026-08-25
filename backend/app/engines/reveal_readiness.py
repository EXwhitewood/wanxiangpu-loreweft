import uuid
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.foreshadowing_service import ForeshadowingService
from app.services.foreshadowing_clue_service import ForeshadowingClueService
from app.services.foreshadowing_graph_service import ForeshadowingGraphService
from app.engines.foreshadowing_config import FORESHADOWING_CONFIG

logger = logging.getLogger(__name__)

_FAIRNESS_SCORES = {
    "unfair": 0.3,
    "partially_fair": 0.6,
    "fair_but_hidden": 0.85,
    "fully_fair": 1.0,
}


def _uuid(value: str | uuid.UUID) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


async def calculate_reveal_readiness(
    foreshadowing_id: str | uuid.UUID,
    db: AsyncSession,
    foreshadowing_service: ForeshadowingService | None = None,
    clue_service: ForeshadowingClueService | None = None,
    graph_service: ForeshadowingGraphService | None = None,
) -> dict:
    fid = _uuid(foreshadowing_id)

    if foreshadowing_service is None:
        foreshadowing_service = ForeshadowingService()
    if clue_service is None:
        clue_service = ForeshadowingClueService()
    if graph_service is None:
        graph_service = ForeshadowingGraphService()

    fs = await foreshadowing_service.get_foreshadowing_by_id(fid, db)
    if not fs:
        return {"readiness": 0.0, "ready": False, "error": "not_found"}

    pool = await clue_service.get_evidence_pool(fid, db)

    planned = fs.get("total_clues_planned", 1) or 1
    placed = pool.get("supportive", 0)
    evidence_coverage = min(placed / planned, 1.0)

    causal_precondition = 1.0
    try:
        G = await graph_service.build_causal_graph(fs["project_id"], db)
        node_id = str(fid)
        if node_id in G:
            import networkx as nx
            predecessors = list(G.predecessors(node_id))
            if predecessors:
                ready_count = sum(
                    1 for p in predecessors
                    if G.nodes[p].get("status") in ("resolved", "revealing")
                )
                causal_precondition = ready_count / len(predecessors)
    except Exception as e:
        logger.warning("causal_precondition computation failed: %s, using default", e)

    cognitive_states = await foreshadowing_service.list_cognitive_states_for_foreshadowing(
        fs["project_id"], fid, db
    )
    total_chars = len(cognitive_states)
    informed_chars = sum(
        1 for s in cognitive_states
        if s.get("cognitive_level") in ("fully_aware", "high_suspicion")
    )
    knowledge_alignment = informed_chars / max(total_chars, 1) if total_chars > 0 else 0.5

    fairness_key = fs.get("reader_fairness_level", "fair_but_hidden")
    reader_score = _FAIRNESS_SCORES.get(fairness_key, 0.5)

    contradictory_count = pool.get("contradictory", 0)
    contradiction_absence = 1.0 if contradictory_count == 0 else max(0.2, 1.0 - contradictory_count * 0.2)

    weights = FORESHADOWING_CONFIG["readiness"]["weights"]
    threshold = FORESHADOWING_CONFIG["readiness"]["threshold"]

    readiness = (
        weights["evidence_coverage"] * evidence_coverage
        + weights["causal_precondition"] * causal_precondition
        + weights["knowledge_alignment"] * knowledge_alignment
        + weights["reader_fairness"] * reader_score
        + weights["contradiction_absence"] * contradiction_absence
    )

    components = {
        "evidence_coverage": round(evidence_coverage, 3),
        "causal_precondition": round(causal_precondition, 3),
        "knowledge_alignment": round(knowledge_alignment, 3),
        "reader_fairness": round(reader_score, 3),
        "contradiction_absence": round(contradiction_absence, 3),
    }

    weakest = min(components, key=components.get)

    return {
        "readiness": round(readiness, 3),
        "components": components,
        "ready": readiness >= threshold,
        "recommendation": (
            "可以进入揭示阶段"
            if readiness >= threshold
            else f"准备度不足，最弱维度：{weakest}={components[weakest]}，建议优先补充"
        ),
    }
