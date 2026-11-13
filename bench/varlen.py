"""Variable-length reachability: AGE Cypher vs SQL vs in-process BFS.

Question asked of every method: *is there a path of at most h hops between s
and t?* (undirected). Pairs are chosen so that the shortest path is exactly h
hops, so every method must find a path and none can stop at a shorter one.

Methods
  age_cypher      MATCH (a {id})-[*1..h]-(b {id}) RETURN 1 LIMIT 1
  sql_recursive   WITH RECURSIVE over an edge table, per-path cycle check
  sql_level_bfs   one SQL statement per hop with a shared visited set
                  (the shape Auto-Ontology uses for join paths)
  python_bfs      BFS over an adjacency loaded once from SQL (load time
                  reported separately)

The graph is synthetic: preferential attachment, so a few hubs and many
low-degree vertices. Usage:

    uv run python bench/varlen.py --url postgresql://... --nodes 2000 --out bench/results/x.json
"""

from __future__ import annotations

import argparse
import json
import platform
import random
import statistics
import time
from collections import defaultdict, deque

from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from auto_ontology_graph.model import Edge, Graph, Node, NodeRef
from auto_ontology_graph.store.age import AgeStore, cypher, prepare

GRAPH = "aog_bench"
EDGE_TABLE = "aog_bench_edge"


def make_graph(n: int, m: int, seed: int) -> list[tuple[int, int]]:
    """Barabasi-Albert style: each new vertex links to m earlier ones."""
    rng = random.Random(seed)
    edges: set[tuple[int, int]] = set()
    targets: list[int] = list(range(m))
    for v in range(m, n):
        chosen: set[int] = set()
        while len(chosen) < m:
            chosen.add(rng.choice(targets))
        for u in chosen:
            edges.add((v, u))
        targets.extend(chosen)
        targets.extend([v] * m)
    return sorted(edges)


def adjacency(edges: list[tuple[int, int]]) -> dict[int, list[int]]:
    adj: dict[int, list[int]] = defaultdict(list)
    for a, b in edges:
        adj[a].append(b)
        adj[b].append(a)
    return adj


def bfs_distance(adj, s: int, t: int, limit: int) -> int | None:
    seen = {s}
    frontier = [s]
    for depth in range(1, limit + 1):
        nxt = []
        for v in frontier:
            for w in adj.get(v, ()):
                if w == t:
                    return depth
                if w not in seen:
                    seen.add(w)
                    nxt.append(w)
        frontier = nxt
    return None


def pick_pairs(adj, n: int, hops: int, count: int, seed: int) -> list[tuple[int, int]]:
    rng = random.Random(seed * 100 + hops)
    pairs = []
    attempts = 0
    while len(pairs) < count and attempts < 10_000:
        attempts += 1
        s = rng.randrange(n)
        dist = {s: 0}
        q = deque([s])
        while q:
            v = q.popleft()
            if dist[v] == hops:
                continue
            for w in adj[v]:
                if w not in dist:
                    dist[w] = dist[v] + 1
                    q.append(w)
        at = [v for v, d in dist.items() if d == hops]
        if at:
            pairs.append((s, rng.choice(at)))
    return pairs


def timed(fn, timeout_s: float):
    start = time.perf_counter()
    try:
        ok = fn()
    except OperationalError as exc:
        if "statement timeout" in str(exc) or "canceling statement" in str(exc):
            return None, timeout_s * 1000
        raise
    return ok, (time.perf_counter() - start) * 1000


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--url", required=True)
    p.add_argument("--nodes", type=int, default=2000)
    p.add_argument("--m", type=int, default=2, help="edges per new vertex")
    p.add_argument("--hops", type=int, nargs="+", default=[2, 3, 4, 5])
    p.add_argument("--pairs", type=int, default=5)
    p.add_argument("--timeout-ms", type=int, default=10_000)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--out")
    args = p.parse_args()

    url = args.url.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_engine(url)
    edges = make_graph(args.nodes, args.m, args.seed)
    adj = adjacency(edges)

    # Load the same graph into AGE and into a plain edge table (both directions).
    graph = Graph(
        [Node("V", str(i)) for i in range(args.nodes)],
        [Edge("E", NodeRef("V", str(a)), NodeRef("V", str(b))) for a, b in edges],
    )
    load = AgeStore(engine, batch_size=2000).replace(GRAPH, graph)
    with engine.begin() as conn:
        conn.exec_driver_sql(f"DROP TABLE IF EXISTS {EDGE_TABLE}")
        conn.exec_driver_sql(f"CREATE TABLE {EDGE_TABLE} (src int NOT NULL, dst int NOT NULL)")
        rows = [{"s": a, "d": b} for a, b in edges] + [{"s": b, "d": a} for a, b in edges]
        conn.execute(text(f"INSERT INTO {EDGE_TABLE} VALUES (:s, :d)"), rows)
        conn.exec_driver_sql(f"CREATE INDEX ON {EDGE_TABLE} (src)")
        conn.exec_driver_sql(f"ANALYZE {EDGE_TABLE}")
        versions = conn.exec_driver_sql(
            "SELECT current_setting('server_version'), "
            "(SELECT extversion FROM pg_extension WHERE extname = 'age')"
        ).one()

    timeout_s = args.timeout_ms / 1000

    def run_age(s, t, h):
        with engine.begin() as conn:
            conn.exec_driver_sql(f"SET LOCAL statement_timeout = {args.timeout_ms}")
            prepare(conn)
            rows = cypher(
                conn,
                GRAPH,
                f"MATCH (a:V {{id: $s}})-[*1..{h}]-(b:V {{id: $t}}) RETURN 1 LIMIT 1",
                {"s": str(s), "t": str(t)},
            )
            return bool(rows)

    def run_recursive(s, t, h):
        sql = text(f"""
            WITH RECURSIVE walk(node, depth, path) AS (
                SELECT CAST(:s AS int), 0, ARRAY[CAST(:s AS int)]
                UNION ALL
                SELECT e.dst, w.depth + 1, w.path || e.dst
                FROM walk w JOIN {EDGE_TABLE} e ON e.src = w.node
                WHERE w.depth < :h AND NOT e.dst = ANY(w.path)
            )
            SELECT 1 FROM walk WHERE node = :t LIMIT 1""")
        with engine.begin() as conn:
            conn.exec_driver_sql(f"SET LOCAL statement_timeout = {args.timeout_ms}")
            return conn.execute(sql, {"s": s, "t": t, "h": h}).first() is not None

    def run_level_bfs(s, t, h):
        sql = text(f"SELECT DISTINCT dst FROM {EDGE_TABLE} WHERE src = ANY(:f)")
        with engine.begin() as conn:
            conn.exec_driver_sql(f"SET LOCAL statement_timeout = {args.timeout_ms}")
            seen, frontier = {s}, [s]
            for _ in range(h):
                nxt = [d for (d,) in conn.execute(sql, {"f": frontier}) if d not in seen]
                if t in nxt:
                    return True
                seen.update(nxt)
                frontier = nxt
            return False

    started = time.perf_counter()
    with engine.connect() as conn:
        loaded = adjacency(
            [
                tuple(r)
                for r in conn.exec_driver_sql(f"SELECT src, dst FROM {EDGE_TABLE} WHERE src < dst")
            ]
        )
    adjacency_load_ms = (time.perf_counter() - started) * 1000

    def run_python(s, t, h):
        return bfs_distance(loaded, s, t, h) is not None

    methods = {
        "age_cypher": run_age,
        "sql_recursive": run_recursive,
        "sql_level_bfs": run_level_bfs,
        "python_bfs": run_python,
    }
    results: dict[str, dict[str, dict]] = {m: {} for m in methods}
    for h in args.hops:
        pairs = pick_pairs(adj, args.nodes, h, args.pairs, args.seed)
        for name, fn in methods.items():
            times, timeouts, wrong = [], 0, 0
            for s, t in pairs:
                ok, ms = timed(lambda fn=fn, s=s, t=t, h=h: fn(s, t, h), timeout_s)
                if ok is None:
                    timeouts += 1
                elif not ok:
                    wrong += 1
                times.append(ms)
            results[name][str(h)] = {
                "median_ms": round(statistics.median(times), 2) if times else None,
                "max_ms": round(max(times), 2) if times else None,
                "pairs": len(pairs),
                "timeouts": timeouts,
                "not_found": wrong,
            }
            print(f"h={h} {name:14s} {results[name][str(h)]}", flush=True)

    degrees = sorted((len(v) for v in adj.values()), reverse=True)
    report = {
        "question": "path of at most h hops exists (undirected); shortest path is exactly h",
        "graph": {
            "generator": "preferential attachment",
            "nodes": args.nodes,
            "edges": len(edges),
            "m": args.m,
            "seed": args.seed,
            "max_degree": degrees[0],
            "median_degree": statistics.median(degrees),
        },
        "environment": {
            "postgres": versions[0],
            "age": versions[1],
            "client": platform.platform(),
            "python": platform.python_version(),
        },
        "timeout_ms": args.timeout_ms,
        "age_load_seconds": round(load.seconds, 2),
        "python_adjacency_load_ms": round(adjacency_load_ms, 2),
        "results": results,
    }
    if args.out:
        with open(args.out, "w") as f:
            json.dump(report, f, indent=2)
            f.write("\n")
    with engine.begin() as conn:
        conn.exec_driver_sql(f"DROP TABLE IF EXISTS {EDGE_TABLE}")


if __name__ == "__main__":
    main()
