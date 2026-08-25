from __future__ import annotations

import json
from collections import deque
from typing import Any

from app.repositories.base import BaseRepository
from app.repositories.core_repository import CoreRepository
from app.services.memory_core import CoreMemoryService


class NetworkxCoreRepository(CoreRepository):
    _graphs: dict[str, Any] = {}
    _loaded: set[str] = set()

    def __init__(self, project_id: str, entity_type: str):
        super().__init__(project_id, entity_type)

    async def _ensure_graph_loaded(self) -> None:
        if self.project_id in NetworkxCoreRepository._loaded:
            return
        import networkx as nx

        graph = nx.DiGraph()
        project = await self._service._get_project(self.project_id)
        if project and project.core_data:
            relationships = project.core_data.get("relationships", [])
            for rel in relationships:
                from_id = rel.get("from")
                to_id = rel.get("to")
                if from_id and to_id:
                    graph.add_edge(
                        from_id,
                        to_id,
                        type=rel.get("type", ""),
                        properties=rel.get("properties", {}),
                    )
        NetworkxCoreRepository._graphs[self.project_id] = graph
        NetworkxCoreRepository._loaded.add(self.project_id)

    def _get_graph(self) -> Any:
        return NetworkxCoreRepository._graphs.get(self.project_id)

    async def _persist_graph(self) -> None:
        import networkx as nx

        graph = self._get_graph()
        if graph is None:
            return

        relationships = []
        for u, v, data in graph.edges(data=True):
            relationships.append({
                "from": u,
                "to": v,
                "type": data.get("type", ""),
                "properties": data.get("properties", {}),
            })

        project = await self._service._get_project(self.project_id)
        if not project:
            return

        core_data = dict(project.core_data or {})
        core_data["relationships"] = relationships
        project.core_data = core_data
        await self._service._save_project(project)

    async def query_relationships(self, entity_id: str, depth: int = 1) -> list[dict]:
        import networkx as nx

        await self._ensure_graph_loaded()
        graph = self._get_graph()
        if graph is None or entity_id not in graph:
            return []

        results = []
        visited_edges = set()
        queue = deque([(entity_id, 0)])

        while queue:
            current_id, current_depth = queue.popleft()
            if current_depth >= depth:
                continue

            for successor in graph.successors(current_id):
                edge_data = graph.get_edge_data(current_id, successor)
                edge_key = (current_id, successor)
                if edge_key not in visited_edges:
                    visited_edges.add(edge_key)
                    results.append({
                        "from": current_id,
                        "to": successor,
                        "type": edge_data.get("type", ""),
                        "properties": edge_data.get("properties", {}),
                        "depth": current_depth + 1,
                    })
                    queue.append((successor, current_depth + 1))

            for predecessor in graph.predecessors(current_id):
                edge_data = graph.get_edge_data(predecessor, current_id)
                edge_key = (predecessor, current_id)
                if edge_key not in visited_edges:
                    visited_edges.add(edge_key)
                    results.append({
                        "from": predecessor,
                        "to": current_id,
                        "type": edge_data.get("type", ""),
                        "properties": edge_data.get("properties", {}),
                        "depth": current_depth + 1,
                    })
                    queue.append((predecessor, current_depth + 1))

        return results

    async def add_relationship(
        self, from_id: str, to_id: str, rel_type: str, properties: dict = None
    ) -> None:
        import networkx as nx

        await self._ensure_graph_loaded()
        graph = self._get_graph()
        if graph is None:
            return

        graph.add_edge(
            from_id,
            to_id,
            type=rel_type,
            properties=properties or {},
        )

        await self._persist_graph()

    async def remove_relationship(self, from_id: str, to_id: str) -> None:
        import networkx as nx

        await self._ensure_graph_loaded()
        graph = self._get_graph()
        if graph is None:
            return

        if graph.has_edge(from_id, to_id):
            graph.remove_edge(from_id, to_id)
            await self._persist_graph()
