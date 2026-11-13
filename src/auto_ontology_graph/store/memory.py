"""An in-process store: the reference behaviour, and the baseline in benchmarks."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from auto_ontology_graph.model import Edge, Graph, Node, NodeRef
from auto_ontology_graph.store.base import (
    DEFAULT_MAX_NODES,
    GraphNotBuilt,
    Path,
    Subgraph,
    SyncReport,
    check_bounds,
    graph_name,
)


@dataclass
class _Built:
    nodes: dict[NodeRef, Node]
    #: Undirected adjacency: vertex -> [(edge, neighbour)], in insertion order.
    adjacency: dict[NodeRef, list[tuple[Edge, NodeRef]]]
    synced_at: str
    vertex_counts: dict[str, int]
    edge_counts: dict[str, int]


class MemoryStore:
    def __init__(self) -> None:
        self._graphs: dict[str, _Built] = {}

    def _get(self, name: str) -> _Built:
        try:
            return self._graphs[graph_name(name)]
        except KeyError:
            raise GraphNotBuilt(f"graph {name!r} has not been built") from None

    def replace(self, name: str, graph: Graph) -> SyncReport:
        started = time.monotonic()
        graph_name(name)
        nodes = {n.ref: n for n in graph.nodes}
        adjacency: dict[NodeRef, list[tuple[Edge, NodeRef]]] = defaultdict(list)
        report = SyncReport(graph=name)
        for n in graph.nodes:
            report.vertices[n.label] = report.vertices.get(n.label, 0) + 1
        for e in graph.edges:
            if e.source not in nodes or e.target not in nodes:
                report.unmatched_edges += 1
                continue
            adjacency[e.source].append((e, e.target))
            if e.target != e.source:
                adjacency[e.target].append((e, e.source))
            report.edges[e.label] = report.edges.get(e.label, 0) + 1
        report.synced_at = datetime.now(UTC).isoformat()
        self._graphs[name] = _Built(
            nodes, dict(adjacency), report.synced_at, dict(report.vertices), dict(report.edges)
        )
        report.seconds = time.monotonic() - started
        return report

    def status(self, name: str) -> dict[str, Any]:
        graph_name(name)
        built = self._graphs.get(name)
        if built is None:
            return {"graph": name, "built": False, "synced_at": None, "vertices": {}, "edges": {}}
        return {
            "graph": name,
            "built": True,
            "synced_at": built.synced_at,
            "vertices": dict(built.vertex_counts),
            "edges": dict(built.edge_counts),
        }

    def neighborhood(
        self, name: str, start: NodeRef, *, depth: int = 1, max_nodes: int = DEFAULT_MAX_NODES
    ) -> Subgraph | None:
        check_bounds(depth=depth, max_nodes=max_nodes)
        built = self._get(name)
        if start not in built.nodes:
            return None
        seen: dict[NodeRef, Node] = {start: built.nodes[start]}
        edges: dict[int, Edge] = {}
        frontier = [start]
        truncated = False
        for _ in range(depth):
            nxt: list[NodeRef] = []
            for ref in frontier:
                for edge, other in built.adjacency.get(ref, ()):
                    if other not in seen:
                        if len(seen) >= max_nodes:
                            truncated = True
                            continue
                        seen[other] = built.nodes[other]
                        nxt.append(other)
                    edges.setdefault(id(edge), edge)
            frontier = nxt
            if not frontier:
                break
        return Subgraph(start, depth, list(seen.values()), list(edges.values()), truncated)

    def paths(
        self, name: str, source: NodeRef, target: NodeRef, *, max_hops: int = 4, limit: int = 50
    ) -> list[Path]:
        check_bounds(max_hops=max_hops, limit=limit)
        built = self._get(name)
        if source not in built.nodes or target not in built.nodes or source == target:
            return []
        # Breadth-first over partial paths, so results come out shortest first.
        found: list[Path] = []
        queue: deque[tuple[tuple[NodeRef, ...], tuple[tuple[str, bool], ...]]] = deque(
            [((source,), ())]
        )
        while queue and len(found) < limit:
            nodes, hops = queue.popleft()
            here = nodes[-1]
            for edge, other in built.adjacency.get(here, ()):
                if other in nodes:
                    continue
                step = (nodes + (other,), hops + ((edge.label, edge.source == here),))
                if other == target:
                    found.append(Path(*step))
                    if len(found) >= limit:
                        break
                elif len(step[1]) < max_hops:
                    queue.append(step)
        return found
