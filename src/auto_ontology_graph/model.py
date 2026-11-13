"""The storage-neutral graph that sources produce and stores keep.

A vertex is identified by ``(label, id)``. Ids are strings and only need to be
unique within their label, so two sources can use the same id space as long as
they use different labels. A source that shares a label with another one is
responsible for namespacing its ids.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

#: Labels and edge types are written into Cypher, so they are restricted to
#: plain identifiers. Values never are.
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


def identifier(value: str) -> str:
    """Return *value* if it is a safe label or edge type, else raise."""
    if not _IDENTIFIER.match(value):
        raise ValueError(f"{value!r} is not a valid label or edge type")
    return value


@dataclass(frozen=True, order=True)
class NodeRef:
    """A pointer to one vertex."""

    label: str
    id: str

    def __post_init__(self) -> None:
        identifier(self.label)


@dataclass(frozen=True)
class Node:
    label: str
    id: str
    properties: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        identifier(self.label)
        if "id" in self.properties:
            raise ValueError("'id' is the vertex key; do not repeat it in properties")

    @property
    def ref(self) -> NodeRef:
        return NodeRef(self.label, self.id)


@dataclass(frozen=True)
class Edge:
    label: str
    source: NodeRef
    target: NodeRef
    properties: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        identifier(self.label)


@dataclass
class Graph:
    """Vertices and edges, plus the bookkeeping a full rebuild needs."""

    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)

    def vertex_labels(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(n.label for n in self.nodes))

    def edge_labels(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(e.label for e in self.edges))

    def extend(self, other: Graph) -> Graph:
        """Append *other* in place and return self, rejecting duplicate vertices."""
        seen = {n.ref for n in self.nodes}
        clash = [n.ref for n in other.nodes if n.ref in seen]
        if clash:
            raise ValueError(f"duplicate vertices across sources: {clash[:5]}")
        self.nodes.extend(other.nodes)
        self.edges.extend(other.edges)
        return self

    def dangling_edges(self) -> list[Edge]:
        """Edges whose source or target vertex is not in the graph."""
        refs = {n.ref for n in self.nodes}
        return [e for e in self.edges if e.source not in refs or e.target not in refs]


def merge(graphs: Iterable[Graph]) -> Graph:
    out = Graph()
    for g in graphs:
        out.extend(g)
    return out
