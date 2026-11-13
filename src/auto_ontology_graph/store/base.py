"""The contract every graph store implements.

Traversals are **undirected**: an ontology question such as "how is Order
related to Customer" walks ``Term <-REPRESENTS- Table -JOIN-> Table
-REPRESENTS-> Term``, against the stored direction half the time. Each hop of a
returned path still records the stored direction so nothing is lost.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from auto_ontology_graph.model import Edge, Graph, Node, NodeRef

_GRAPH_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")

MAX_DEPTH = 5
DEFAULT_MAX_NODES = 200
MAX_NODES = 5000
MAX_PATH_HOPS = 8


class GraphNotBuilt(LookupError):
    """The named graph has not been written to the store yet."""


def graph_name(value: str) -> str:
    """Validate a graph name, which some stores splice into SQL."""
    if not _GRAPH_NAME.match(value):
        raise ValueError(
            f"{value!r} is not a valid graph name "
            "(lowercase letters, digits and underscores, starting with a letter)"
        )
    return value


def check_bounds(
    *,
    depth: int | None = None,
    max_nodes: int | None = None,
    max_hops: int | None = None,
    limit: int | None = None,
) -> None:
    if depth is not None and not 1 <= depth <= MAX_DEPTH:
        raise ValueError(f"depth must be between 1 and {MAX_DEPTH}")
    if max_nodes is not None and not 1 <= max_nodes <= MAX_NODES:
        raise ValueError(f"max_nodes must be between 1 and {MAX_NODES}")
    if max_hops is not None and not 1 <= max_hops <= MAX_PATH_HOPS:
        raise ValueError(f"max_hops must be between 1 and {MAX_PATH_HOPS}")
    if limit is not None and not 1 <= limit <= 1000:
        raise ValueError("limit must be between 1 and 1000")


@dataclass
class SyncReport:
    graph: str
    vertices: dict[str, int] = field(default_factory=dict)
    edges: dict[str, int] = field(default_factory=dict)
    #: Edges whose endpoints were not found. Non-zero means a source emitted an
    #: edge to a vertex nobody emitted.
    unmatched_edges: int = 0
    seconds: float = 0.0
    synced_at: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "graph": self.graph,
            "synced_at": self.synced_at,
            "vertices": dict(self.vertices),
            "edges": dict(self.edges),
            "unmatched_edges": self.unmatched_edges,
            "seconds": round(self.seconds, 3),
        }


@dataclass
class Subgraph:
    start: NodeRef
    depth: int
    nodes: list[Node]
    edges: list[Edge]
    #: Set when ``max_nodes`` stopped the walk, so the picture is partial.
    truncated: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "start": {"label": self.start.label, "id": self.start.id},
            "depth": self.depth,
            "truncated": self.truncated,
            "nodes": [
                {"label": n.label, "id": n.id, "properties": dict(n.properties)} for n in self.nodes
            ],
            "edges": [
                {
                    "type": e.label,
                    "source": {"label": e.source.label, "id": e.source.id},
                    "target": {"label": e.target.label, "id": e.target.id},
                    "properties": dict(e.properties),
                }
                for e in self.edges
            ],
        }


@dataclass(frozen=True)
class Path:
    """A simple path, start vertex first."""

    nodes: tuple[NodeRef, ...]
    #: ``(edge_type, forward)`` per hop; ``forward`` means the stored edge
    #: points from ``nodes[i]`` to ``nodes[i + 1]``.
    hops: tuple[tuple[str, bool], ...]

    def __len__(self) -> int:
        return len(self.hops)

    def render(self) -> str:
        parts = [f"({self.nodes[0].label}:{self.nodes[0].id})"]
        for (etype, forward), node in zip(self.hops, self.nodes[1:], strict=True):
            arrow = f"-[{etype}]->" if forward else f"<-[{etype}]-"
            parts.append(f"{arrow}({node.label}:{node.id})")
        return "".join(parts)


class GraphStore(Protocol):
    def replace(self, name: str, graph: Graph) -> SyncReport:
        """Replace graph *name* with *graph* atomically."""
        ...

    def status(self, name: str) -> dict[str, Any]:
        """``built``, ``synced_at`` and vertex/edge counts per label."""
        ...

    def neighborhood(
        self, name: str, start: NodeRef, *, depth: int = 1, max_nodes: int = DEFAULT_MAX_NODES
    ) -> Subgraph | None:
        """Vertices and edges within *depth* hops of *start*; ``None`` if absent."""
        ...

    def paths(
        self, name: str, source: NodeRef, target: NodeRef, *, max_hops: int = 4, limit: int = 50
    ) -> list[Path]:
        """Simple paths of at most *max_hops* hops, shortest first."""
        ...
