"""Rank the paths between two vertices, PathRAG-style.

Scoring follows PathRAG (Chen et al., 2025, arXiv:2502.14902), sections 3.2 and
3.3:

* A unit of resource starts at the source vertex and flows outward one layer
  at a time. Each vertex passes ``alpha * S(u) / |N(u)|`` to every neighbour,
  so resource decays with distance and is diluted by fan-out: whatever lies
  *past* a hub that connects to everything gets a small share. A vertex that
  would pass on less than ``theta`` per neighbour stops expanding. Each vertex
  is set once, in the first layer that reaches it.
* A path's **reliability** is the resource summed over its vertices divided by
  its number of hops.

PathRAG then multiplies by how well a path's start matches the query. The
counterpart here is a **boost**: every intermediate vertex the caller marks
as relevant multiplies the score by ``1 + boost``. Two paths with the same
shape have the same reliability, and the boost is what tells them apart.

Ranking is storage-neutral: candidates come from :meth:`GraphStore.paths` and
the flow runs over :meth:`GraphStore.neighborhood` of the source.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Hashable, Iterable, Mapping
from dataclasses import dataclass
from typing import TypeVar

from auto_ontology_graph.model import NodeRef
from auto_ontology_graph.store.base import MAX_DEPTH, GraphStore, Path, Subgraph

DEFAULT_ALPHA = 0.7
DEFAULT_THETA = 0.005
DEFAULT_BOOST = 0.5

K = TypeVar("K", bound=Hashable)


def flow_resources[K: Hashable](
    adjacency: Mapping[K, Collection[K]],
    source: K,
    *,
    alpha: float = DEFAULT_ALPHA,
    theta: float = DEFAULT_THETA,
) -> dict[K, float]:
    """Resource reaching each vertex from *source* (PathRAG eq. 2 and 3)."""
    resources: dict[K, float] = {source: 1.0}
    frontier = [source]
    while frontier:
        incoming: dict[K, float] = defaultdict(float)
        for vertex in frontier:
            neighbours = set(adjacency.get(vertex, ()))
            if not neighbours:
                continue
            per_neighbour = resources[vertex] / len(neighbours)
            if per_neighbour < theta:
                continue
            for neighbour in neighbours:
                if neighbour not in resources:
                    incoming[neighbour] += alpha * per_neighbour
        resources.update(incoming)
        frontier = sorted(incoming, key=repr)
    return resources


def adjacency_of(subgraph: Subgraph) -> dict[NodeRef, set[NodeRef]]:
    """Undirected adjacency of a neighbourhood."""
    adjacency: dict[NodeRef, set[NodeRef]] = defaultdict(set)
    for edge in subgraph.edges:
        if edge.source != edge.target:
            adjacency[edge.source].add(edge.target)
            adjacency[edge.target].add(edge.source)
    return dict(adjacency)


@dataclass(frozen=True)
class RankedPath:
    path: Path
    reliability: float
    score: float


def score_paths(
    paths: Iterable[Path],
    resources: Mapping[NodeRef, float],
    *,
    boost_nodes: Collection[NodeRef] = (),
    boost: float = DEFAULT_BOOST,
) -> list[RankedPath]:
    """Score and sort, best first. Ties break on length, then on rendered text."""
    boosted = set(boost_nodes)
    ranked = []
    for path in paths:
        reliability = sum(resources.get(n, 0.0) for n in path.nodes) / len(path)
        factor = 1.0 + boost * sum(1 for n in path.nodes[1:-1] if n in boosted)
        ranked.append(RankedPath(path, round(reliability, 6), round(reliability * factor, 6)))
    ranked.sort(key=lambda r: (-r.score, len(r.path), r.path.render()))
    return ranked


def ranked_paths(
    store: GraphStore,
    name: str,
    source: NodeRef,
    target: NodeRef,
    *,
    k: int = 5,
    max_hops: int = 4,
    max_extra_hops: int = 1,
    boost_nodes: Collection[NodeRef] = (),
    alpha: float = DEFAULT_ALPHA,
    theta: float = DEFAULT_THETA,
    boost: float = DEFAULT_BOOST,
    max_nodes: int = 2000,
) -> list[RankedPath]:
    """The *k* best simple paths from *source* to *target*, best first.

    Candidates are the paths at most ``max_extra_hops`` longer than the
    shortest one (and never longer than ``max_hops``).
    """
    if k < 1:
        return []
    candidates = store.paths(name, source, target, max_hops=max_hops, limit=200)
    if not candidates:
        return []
    shortest = min(len(p) for p in candidates)
    candidates = [p for p in candidates if len(p) <= shortest + max_extra_hops]
    subgraph = store.neighborhood(name, source, depth=min(max_hops, MAX_DEPTH), max_nodes=max_nodes)
    adjacency = adjacency_of(subgraph) if subgraph is not None else {}
    resources = flow_resources(adjacency, source, alpha=alpha, theta=theta)
    return score_paths(candidates, resources, boost_nodes=boost_nodes, boost=boost)[:k]
