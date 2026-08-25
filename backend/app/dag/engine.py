import asyncio
from collections import defaultdict

from app.agents.base import BaseAgent


class AgentNode:
    def __init__(self, name: str, agent: BaseAgent, dependencies: list[str] | None = None):
        self.name = name
        self.agent = agent
        self.dependencies = dependencies or []


class SimpleDAG:
    def __init__(self):
        self.nodes: dict[str, AgentNode] = {}

    def add_node(self, node: AgentNode) -> None:
        self.nodes[node.name] = node

    def _topological_sort(self) -> list[list[str]]:
        in_degree: dict[str, int] = defaultdict(int)
        graph: dict[str, list[str]] = defaultdict(list)

        for name, node in self.nodes.items():
            if name not in in_degree:
                in_degree[name] = 0
            for dep in node.dependencies:
                graph[dep].append(name)
                in_degree[name] += 1

        layers: list[list[str]] = []
        ready = [name for name, deg in in_degree.items() if deg == 0]

        while ready:
            layers.append(sorted(ready))
            next_ready = []
            for name in ready:
                for dependent in graph[name]:
                    in_degree[dependent] -= 1
                    if in_degree[dependent] == 0:
                        next_ready.append(dependent)
            ready = next_ready

        return layers

    async def run(self, initial_context: dict) -> dict:
        context = dict(initial_context)
        layers = self._topological_sort()

        for layer in layers:
            tasks = []
            for name in layer:
                if name in self.nodes:
                    node = self.nodes[name]
                    tasks.append(self._execute_node(node, context))

            if tasks:
                results = await asyncio.gather(*tasks)
                for name, result in zip(layer, results):
                    if result is not None:
                        context.update(result)

        return context

    async def _execute_node(self, node: AgentNode, context: dict) -> dict | None:
        try:
            return await node.agent.execute(context)
        except Exception as e:
            return {"error": {node.name: str(e)}}
