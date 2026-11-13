"""Read operations as plain functions returning JSON-ready dicts.

The MCP server and the CLI both call these, so their behaviour is tested once,
against any :class:`GraphStore`.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from auto_ontology_graph.model import NodeRef
from auto_ontology_graph.query.ranking import ranked_paths
from auto_ontology_graph.store.base import DEFAULT_MAX_NODES, GraphStore


def _ref(value: dict[str, str] | Sequence[str]) -> NodeRef:
    if isinstance(value, dict):
        return NodeRef(value["label"], str(value["id"]))
    label, node_id = value
    return NodeRef(label, str(node_id))


def graph_status(store: GraphStore, graph: str) -> dict[str, Any]:
    return store.status(graph)


def neighborhood(
    store: GraphStore,
    graph: str,
    label: str,
    node_id: str,
    *,
    depth: int = 1,
    max_nodes: int = DEFAULT_MAX_NODES,
) -> dict[str, Any]:
    sub = store.neighborhood(graph, NodeRef(label, node_id), depth=depth, max_nodes=max_nodes)
    if sub is None:
        return {"found": False, "start": {"label": label, "id": node_id}}
    return {"found": True, **sub.as_dict()}


def find_paths(
    store: GraphStore,
    graph: str,
    source: dict[str, str],
    target: dict[str, str],
    *,
    k: int = 5,
    max_hops: int = 4,
    boost: Sequence[dict[str, str]] = (),
) -> dict[str, Any]:
    ranked = ranked_paths(
        store,
        graph,
        _ref(source),
        _ref(target),
        k=k,
        max_hops=max_hops,
        boost_nodes=[_ref(b) for b in boost],
    )
    return {
        "source": source,
        "target": target,
        "paths": [
            {
                "rank": i + 1,
                "score": r.score,
                "reliability": r.reliability,
                "hops": len(r.path),
                "text": r.path.render(),
                "nodes": [{"label": n.label, "id": n.id} for n in r.path.nodes],
                "edges": [{"type": t, "forward": f} for t, f in r.path.hops],
            }
            for i, r in enumerate(ranked)
        ],
    }
