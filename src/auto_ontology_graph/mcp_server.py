"""MCP server over the AGE store (``pip install auto-ontology-graph[mcp]``).

Tools take no Cypher: a status read, a neighbourhood walk and ranked paths.
Run with ``python -m auto_ontology_graph.mcp_server`` and ``AOG_STORE_URL`` set.
"""

from __future__ import annotations

from typing import Any

from auto_ontology_graph import tools
from auto_ontology_graph.config import STORE_URL_ENV, engine_from_env, graph_from_env
from auto_ontology_graph.store.age import AgeStore


def build_server():  # pragma: no cover - needs the optional mcp package
    from mcp.server.fastmcp import FastMCP

    store = AgeStore(engine_from_env(STORE_URL_ENV))
    graph = graph_from_env()
    server = FastMCP("auto-ontology-graph")

    @server.tool()
    def graph_status() -> dict[str, Any]:
        """Whether the graph is built, when it was synced, and counts per label."""
        return tools.graph_status(store, graph)

    @server.tool()
    def explore_neighborhood(label: str, id: str, depth: int = 1, max_nodes: int = 200) -> dict:
        """Vertices and edges within `depth` hops (1-5) of one vertex, e.g. label='Term'."""
        return tools.neighborhood(store, graph, label, id, depth=depth, max_nodes=max_nodes)

    @server.tool()
    def find_paths(
        source_label: str,
        source_id: str,
        target_label: str,
        target_id: str,
        k: int = 5,
        max_hops: int = 4,
    ) -> dict:
        """The k best simple paths between two vertices, scored PathRAG-style."""
        return tools.find_paths(
            store,
            graph,
            {"label": source_label, "id": source_id},
            {"label": target_label, "id": target_id},
            k=k,
            max_hops=max_hops,
        )

    return server


def main() -> None:  # pragma: no cover
    build_server().run()


if __name__ == "__main__":  # pragma: no cover
    main()
