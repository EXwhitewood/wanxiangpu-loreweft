import uuid
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.db_models import ForeshadowingLine, ForeshadowingCausalEdge

logger = logging.getLogger(__name__)

try:
    import networkx as nx
    NETWORKX_AVAILABLE = True
except ImportError:
    NETWORKX_AVAILABLE = False

_VALID_EDGE_TYPES = {"causes", "reveals", "obscures", "depends_on", "conflicts_with", "synergizes_with"}


def _uuid(value: str | uuid.UUID) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def edge_to_dict(edge: ForeshadowingCausalEdge) -> dict[str, Any]:
    return {
        "id": edge.id,
        "project_id": edge.project_id,
        "source_id": edge.source_id,
        "target_id": edge.target_id,
        "edge_type": edge.edge_type,
        "weight": edge.weight,
        "narrative_justification": edge.narrative_justification,
        "active_from_chapter": edge.active_from_chapter,
        "active_until_chapter": edge.active_until_chapter,
        "created_at": edge.created_at.isoformat() if edge.created_at else None,
    }


class ForeshadowingGraphService:
    async def add_edge(
        self,
        project_id: str | uuid.UUID,
        source_id: str | uuid.UUID,
        target_id: str | uuid.UUID,
        edge_type: str,
        db: AsyncSession,
        weight: float = 1.0,
        narrative_justification: str = "",
        active_from_chapter: int | None = None,
        active_until_chapter: int | None = None,
    ) -> dict:
        pid = _uuid(project_id)
        sid = _uuid(source_id)
        tid = _uuid(target_id)

        if sid == tid:
            raise ValueError("Self-referential edges are not allowed")
        if edge_type not in _VALID_EDGE_TYPES:
            raise ValueError(f"Invalid edge_type: {edge_type}. Valid: {sorted(_VALID_EDGE_TYPES)}")

        line_count = (
            await db.execute(
                select(ForeshadowingLine).where(
                    ForeshadowingLine.project_id == pid,
                    ForeshadowingLine.id.in_([sid, tid]),
                )
            )
        ).scalars().all()
        if len({line.id for line in line_count}) != 2:
            raise ValueError("Both edge endpoints must belong to the target project")

        edge = ForeshadowingCausalEdge(
            project_id=pid,
            source_id=sid,
            target_id=tid,
            edge_type=edge_type,
            weight=weight,
            narrative_justification=narrative_justification,
            active_from_chapter=active_from_chapter,
            active_until_chapter=active_until_chapter,
        )
        db.add(edge)
        await db.commit()
        await db.refresh(edge)
        return edge_to_dict(edge)

    async def list_edges(
        self,
        project_id: str | uuid.UUID,
        db: AsyncSession,
        edge_type: str | None = None,
    ) -> list[dict]:
        pid = _uuid(project_id)
        stmt = select(ForeshadowingCausalEdge).where(ForeshadowingCausalEdge.project_id == pid)
        if edge_type:
            stmt = stmt.where(ForeshadowingCausalEdge.edge_type == edge_type)
        result = await db.execute(stmt)
        return [edge_to_dict(row) for row in result.scalars().all() if row]

    async def remove_edge(
        self,
        edge_id: str | uuid.UUID,
        db: AsyncSession,
    ) -> bool:
        eid = _uuid(edge_id)
        edge = await db.get(ForeshadowingCausalEdge, eid)
        if not edge:
            return False
        await db.delete(edge)
        await db.commit()
        return True

    async def build_causal_graph(
        self,
        project_id: str | uuid.UUID,
        db: AsyncSession,
        chapter_number: int | None = None,
    ) -> Any:
        if not NETWORKX_AVAILABLE:
            raise RuntimeError("networkx is required for causal graph operations")

        pid = _uuid(project_id)
        G = nx.DiGraph()

        lines_result = await db.execute(
            select(ForeshadowingLine).where(ForeshadowingLine.project_id == pid)
        )
        for line in lines_result.scalars().all():
            G.add_node(
                str(line.id),
                type="foreshadowing",
                name=line.name,
                status=line.status,
                priority=line.priority,
            )

        edges_result = await db.execute(
            select(ForeshadowingCausalEdge).where(ForeshadowingCausalEdge.project_id == pid)
        )
        for edge in edges_result.scalars().all():
            if chapter_number is not None:
                if edge.active_from_chapter is not None and chapter_number < edge.active_from_chapter:
                    continue
                if edge.active_until_chapter is not None and chapter_number > edge.active_until_chapter:
                    continue
            G.add_edge(
                str(edge.source_id),
                str(edge.target_id),
                type=edge.edge_type,
                weight=edge.weight,
                edge_id=str(edge.id),
            )

        return G

    async def detect_conflicts(
        self,
        project_id: str | uuid.UUID,
        db: AsyncSession,
    ) -> list[dict]:
        pid = _uuid(project_id)
        conflicts: list[dict] = []

        conflict_edges = await db.execute(
            select(ForeshadowingCausalEdge).where(
                ForeshadowingCausalEdge.project_id == pid,
                ForeshadowingCausalEdge.edge_type == "conflicts_with",
            )
        )
        for edge in conflict_edges.scalars().all():
            conflicts.append({
                "type": "explicit_conflict",
                "source_id": str(edge.source_id),
                "target_id": str(edge.target_id),
                "narrative_justification": edge.narrative_justification,
            })

        if NETWORKX_AVAILABLE:
            try:
                G = await self.build_causal_graph(pid, db)
                for cycle in nx.simple_cycles(G):
                    conflicts.append({"type": "dependency_cycle", "cycle": cycle})
            except Exception:
                pass

        return conflicts

    async def get_affected_foreshadowing(
        self,
        project_id: str | uuid.UUID,
        foreshadowing_id: str | uuid.UUID,
        db: AsyncSession,
        radius: int = 2,
    ) -> list[dict]:
        if not NETWORKX_AVAILABLE:
            return []

        pid = _uuid(project_id)
        fid = str(_uuid(foreshadowing_id))
        G = await self.build_causal_graph(pid, db)

        if fid not in G:
            return []

        affected = []
        try:
            for target, path_length in nx.single_source_shortest_path_length(G, fid).items():
                if target != fid and path_length <= radius:
                    node_data = G.nodes[target]
                    affected.append({
                        "foreshadowing_id": target,
                        "distance": path_length,
                        "name": node_data.get("name", ""),
                        "status": node_data.get("status", ""),
                    })
        except Exception:
            pass

        affected.sort(key=lambda x: x["distance"])
        return affected

    async def get_graph_as_dict(
        self,
        project_id: str | uuid.UUID,
        db: AsyncSession,
        chapter_number: int | None = None,
    ) -> dict:
        pid = _uuid(project_id)

        nodes_result = await db.execute(
            select(ForeshadowingLine).where(ForeshadowingLine.project_id == pid)
        )
        nodes = [
            {
                "id": str(line.id),
                "name": line.name,
                "status": line.status,
                "priority": line.priority,
                "truth_type": line.secret_truth_type,
            }
            for line in nodes_result.scalars().all()
        ]

        edges = await self.list_edges(pid, db)

        conflicts = await self.detect_conflicts(pid, db)

        return {"nodes": nodes, "edges": edges, "conflicts": conflicts}
