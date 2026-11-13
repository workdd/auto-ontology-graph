"""``aog``: sync an Auto-Ontology database into AGE and read the graph back.

aog sync                                   # AOG_SOURCE_URL -> AOG_STORE_URL
aog status
aog neighborhood Term <id> --depth 2
aog paths Term <id> Term <id> --k 3
aog query "MATCH (t:Term) RETURN t.name LIMIT 5"
"""

from __future__ import annotations

import argparse
import json
import sys

from auto_ontology_graph import tools
from auto_ontology_graph.config import (
    SOURCE_URL_ENV,
    STORE_URL_ENV,
    engine_from_env,
    graph_from_env,
)
from auto_ontology_graph.store.age import AgeStore


def _print(value) -> None:
    json.dump(value, sys.stdout, indent=2, ensure_ascii=False, default=str)
    sys.stdout.write("\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="aog", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--graph", default=None, help="graph name (default $AOG_GRAPH)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sync", help="rebuild the graph from the Auto-Ontology database")
    sub.add_parser("status", help="counts per label")
    nb = sub.add_parser("neighborhood", help="vertices and edges around one vertex")
    nb.add_argument("label")
    nb.add_argument("id")
    nb.add_argument("--depth", type=int, default=1)
    nb.add_argument("--max-nodes", type=int, default=200)
    pt = sub.add_parser("paths", help="ranked paths between two vertices")
    pt.add_argument("source_label")
    pt.add_argument("source_id")
    pt.add_argument("target_label")
    pt.add_argument("target_id")
    pt.add_argument("--k", type=int, default=5)
    pt.add_argument("--max-hops", type=int, default=4)
    q = sub.add_parser("query", help="read-only Cypher")
    q.add_argument("cypher")
    q.add_argument("--columns", default="v", help="comma-separated result column names")
    args = parser.parse_args(argv)

    graph = args.graph or graph_from_env()
    store = AgeStore(engine_from_env(STORE_URL_ENV))

    if args.cmd == "sync":
        from auto_ontology_graph.sources.auto_ontology import AutoOntologySource

        source = AutoOntologySource(engine_from_env(SOURCE_URL_ENV))
        graph_data = source.read()
        _print(store.replace(graph, graph_data).as_dict())
    elif args.cmd == "status":
        _print(tools.graph_status(store, graph))
    elif args.cmd == "neighborhood":
        _print(
            tools.neighborhood(
                store, graph, args.label, args.id, depth=args.depth, max_nodes=args.max_nodes
            )
        )
    elif args.cmd == "paths":
        _print(
            tools.find_paths(
                store,
                graph,
                {"label": args.source_label, "id": args.source_id},
                {"label": args.target_label, "id": args.target_id},
                k=args.k,
                max_hops=args.max_hops,
            )
        )
    elif args.cmd == "query":
        columns = tuple(c.strip() for c in args.columns.split(",") if c.strip())
        _print([list(r) for r in store.run_cypher(graph, args.cypher, columns=columns)])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
