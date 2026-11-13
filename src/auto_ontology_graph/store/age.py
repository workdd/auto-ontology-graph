"""Apache AGE store.

Each graph lives in its own Postgres schema, which AGE creates. Writes rebuild
the whole graph in **one transaction**, so readers keep the previous graph
until commit and a failure leaves it in place. Reads run in ``READ ONLY``
transactions that are always rolled back.

AGE is loaded per transaction (``LOAD 'age'`` plus ``SET LOCAL search_path``),
so it does not need to be in ``shared_preload_libraries``.

Values reach Cypher only as an agtype parameter (``$rows``, ``$id``), never as
text. Labels and edge types are written into the statement, so they are
checked with :func:`auto_ontology_graph.model.identifier` first.

**Indexes.** Every vertex label gets a GIN index on ``properties``: edge
loading and lookups do ``MATCH (a:L {id: ...})``, which AGE answers with a
``@>`` containment test, and the GIN index turns that from a scan into a
lookup. Every edge label gets btree indexes on ``start_id`` and ``end_id`` so
that each hop of a walk is an index lookup.
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection
from sqlalchemy.engine import Engine

from auto_ontology_graph.model import Edge, Graph, Node, NodeRef, identifier
from auto_ontology_graph.store.base import (
    DEFAULT_MAX_NODES,
    GraphNotBuilt,
    Path,
    Subgraph,
    SyncReport,
    check_bounds,
    graph_name,
)

DEFAULT_BATCH_SIZE = 1000
_DOLLAR_TAG = "$cypher$"
_TYPE_SUFFIXES = ("::vertex", "::edge", "::path", "::numeric")


class AgeUnavailable(RuntimeError):
    """The database has no usable Apache AGE extension."""


# --------------------------------------------------------------------------
# Low-level helpers
# --------------------------------------------------------------------------


def prepare(conn: Connection) -> None:
    """Load AGE and put ``ag_catalog`` on the path for this transaction only."""
    try:
        conn.exec_driver_sql("LOAD 'age'")
    except Exception as exc:  # noqa: BLE001 - re-raised with the fix attached
        raise AgeUnavailable(
            "Apache AGE is not installed in this Postgres server. Use an image that "
            "ships it (for example apache/age) and run CREATE EXTENSION age."
        ) from exc
    # UNWIND resolves helper functions through the search path.
    conn.exec_driver_sql('SET LOCAL search_path = ag_catalog, "$user", public')


def graph_exists(conn: Connection, name: str) -> bool:
    row = conn.exec_driver_sql("SELECT 1 FROM ag_catalog.ag_graph WHERE name = %s", (name,)).first()
    return row is not None


@contextmanager
def read_transaction(engine: Engine) -> Iterator[Connection]:
    """A read-only transaction that is always rolled back."""
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            conn.exec_driver_sql("SET TRANSACTION READ ONLY")
            prepare(conn)
            yield conn
        finally:
            trans.rollback()


def to_agtype_value(value: Any) -> Any:
    """Coerce a Python or Postgres value into something JSON can carry."""
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, list | tuple):
        return [to_agtype_value(v) for v in value]
    if isinstance(value, Mapping):
        return {str(k): to_agtype_value(v) for k, v in value.items()}
    return value


def cypher(
    conn: Connection,
    graph: str,
    query: str,
    params: Mapping[str, Any] | None = None,
    columns: Sequence[str] = ("v",),
) -> list[tuple[Any, ...]]:
    """Run one Cypher statement and return its rows, each value parsed."""
    if _DOLLAR_TAG in query:
        raise ValueError(f"Cypher text may not contain {_DOLLAR_TAG}")
    graph_name(graph)
    projection = ", ".join(f"{identifier(c)} agtype" for c in columns)
    if params is None:
        sql = (
            f"SELECT * FROM cypher('{graph}', {_DOLLAR_TAG} {query} {_DOLLAR_TAG}) "
            f"AS ({projection})"
        )
        result = conn.exec_driver_sql(sql)
    else:
        # With bind parameters the driver reads `%` as a placeholder marker.
        body = query.replace("%", "%%")
        sql = (
            f"SELECT * FROM cypher('{graph}', {_DOLLAR_TAG} {body} {_DOLLAR_TAG}, %s) "
            f"AS ({projection})"
        )
        payload = json.dumps(to_agtype_value(dict(params)))
        result = conn.exec_driver_sql(sql, (payload,))
    return [tuple(parse_agtype(v) for v in row) for row in result]


def parse_agtype(text: str | None) -> Any:
    """Parse agtype's text form: JSON plus ``::vertex``-style annotations.

    The annotations are removed by a scanner that skips string literals, so a
    property value containing ``}::vertex`` is left as written.
    """
    if text is None:
        return None
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            j = i + 1
            while j < n:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == '"':
                    break
                j += 1
            out.append(text[i : j + 1])
            i = j + 1
            continue
        if ch == ":":
            suffix = next((s for s in _TYPE_SUFFIXES if text.startswith(s, i)), None)
            if suffix is not None:
                i += len(suffix)
                continue
        out.append(ch)
        i += 1
    return json.loads("".join(out))


def _batches(rows: Sequence[Any], size: int) -> Iterator[list[Any]]:
    for start in range(0, len(rows), size):
        yield list(rows[start : start + size])


def _props(properties: Mapping[str, Any]) -> dict[str, Any]:
    """Drop NULLs: an absent property and a null one read the same in Cypher."""
    return {k: to_agtype_value(v) for k, v in properties.items() if v is not None}


def _node_from_agtype(vertex: Mapping[str, Any]) -> Node:
    props = dict(vertex.get("properties") or {})
    return Node(vertex["label"], str(props.pop("id")), props)


# --------------------------------------------------------------------------
# Store
# --------------------------------------------------------------------------


class AgeStore:
    def __init__(self, engine: Engine, *, batch_size: int = DEFAULT_BATCH_SIZE) -> None:
        self.engine = engine
        self.batch_size = batch_size

    # -- writes ------------------------------------------------------------

    def replace(self, name: str, graph: Graph) -> SyncReport:
        graph_name(name)
        report = SyncReport(graph=name)
        started = time.monotonic()
        by_label: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for node in graph.nodes:
            by_label[node.label].append({"id": node.id, **_props(node.properties)})
        by_edge: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
        for edge in graph.edges:
            key = (edge.label, edge.source.label, edge.target.label)
            by_edge[key].append(
                {"s": edge.source.id, "t": edge.target.id, "p": _props(edge.properties)}
            )
        vertex_labels = graph.vertex_labels()
        edge_labels = graph.edge_labels()
        # An edge may point at a label no vertex was emitted for; it then
        # matches nothing, but the label must exist for MATCH to compile.
        for _, src, dst in by_edge:
            for label in (src, dst):
                if label not in vertex_labels:
                    vertex_labels += (label,)

        # AGE 1.6 keeps a per-backend label cache that a drop_graph + create_graph
        # of the same name can leave inconsistent once the graph has about 16
        # labels: the next transaction on that backend fails with "label
        # (relation) cache corrupted". The writing connection is therefore
        # discarded after use instead of going back to the pool. Other backends
        # are not affected (tested in test_age_store.py).
        with self.engine.connect() as conn:
            try:
                with conn.begin():
                    conn.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS age")
                    prepare(conn)
                    if graph_exists(conn, name):
                        conn.exec_driver_sql("SELECT drop_graph(%s, true)", (name,))
                    conn.exec_driver_sql("SELECT create_graph(%s)", (name,))
                    for label in vertex_labels:
                        conn.exec_driver_sql("SELECT create_vlabel(%s, %s)", (name, label))
                        conn.exec_driver_sql(
                            f'CREATE INDEX ON "{name}"."{identifier(label)}" USING gin (properties)'
                        )
                    for label in edge_labels:
                        conn.exec_driver_sql("SELECT create_elabel(%s, %s)", (name, label))
                        table = f'"{name}"."{identifier(label)}"'
                        conn.exec_driver_sql(f"CREATE INDEX ON {table} (start_id)")
                        conn.exec_driver_sql(f"CREATE INDEX ON {table} (end_id)")

                    for label, rows in by_label.items():
                        statement = (
                            f"UNWIND $rows AS r CREATE (n:{label}) SET n = r RETURN count(n)"
                        )
                        report.vertices[label] = self._load(conn, name, statement, rows)
                    for label in vertex_labels:
                        # Fresh statistics so the planner picks the GIN index below.
                        conn.exec_driver_sql(f'ANALYZE "{name}"."{label}"')
                    for (label, src, dst), rows in by_edge.items():
                        statement = (
                            "UNWIND $rows AS r "
                            f"MATCH (a:{src} {{id: r.s}}), (b:{dst} {{id: r.t}}) "
                            # AGE's SET takes a map variable, not a property expression.
                            "WITH a, b, r.p AS p "
                            f"CREATE (a)-[e:{label}]->(b) SET e = p RETURN count(e)"
                        )
                        written = self._load(conn, name, statement, rows)
                        report.edges[label] = report.edges.get(label, 0) + written
                        report.unmatched_edges += len(rows) - written
                    synced_at = datetime.now(UTC).isoformat()
                    # COMMENT takes only a literal; an ISO timestamp contains no quote.
                    conn.exec_driver_sql(f"COMMENT ON SCHEMA \"{name}\" IS '{synced_at}'")
                    report.synced_at = synced_at
            finally:
                conn.invalidate()

        report.seconds = time.monotonic() - started
        return report

    def _load(self, conn: Connection, graph: str, statement: str, rows: list[Any]) -> int:
        written = 0
        for batch in _batches(rows, self.batch_size):
            ((count,),) = cypher(conn, graph, statement, {"rows": batch})
            written += int(count)
        return written

    # -- reads -------------------------------------------------------------

    def status(self, name: str) -> dict[str, Any]:
        graph_name(name)
        with read_transaction(self.engine) as conn:
            if not graph_exists(conn, name):
                return {
                    "graph": name,
                    "built": False,
                    "synced_at": None,
                    "vertices": {},
                    "edges": {},
                }
            (synced_at,) = conn.exec_driver_sql(
                "SELECT obj_description(%s::regnamespace, 'pg_namespace')", (name,)
            ).one()
            labels = conn.exec_driver_sql(
                "SELECT l.name, l.kind FROM ag_catalog.ag_label l "
                "JOIN ag_catalog.ag_graph g ON g.graphid = l.graph "
                "WHERE g.name = %s AND l.name NOT LIKE %s ORDER BY l.id",
                (name, "\\_ag\\_label\\_%"),
            ).all()
            counts: dict[str, dict[str, int]] = {"v": {}, "e": {}}
            for label, kind in labels:
                (count,) = conn.exec_driver_sql(
                    f'SELECT count(*) FROM "{name}"."{identifier(label)}"'
                ).one()
                counts[kind][label] = int(count)
        return {
            "graph": name,
            "built": True,
            "synced_at": synced_at,
            "vertices": counts["v"],
            "edges": counts["e"],
        }

    def _require(self, conn: Connection, name: str) -> None:
        if not graph_exists(conn, name):
            raise GraphNotBuilt(f"graph {name!r} has not been built")

    def neighborhood(
        self, name: str, start: NodeRef, *, depth: int = 1, max_nodes: int = DEFAULT_MAX_NODES
    ) -> Subgraph | None:
        """Hop-by-hop BFS: one Cypher statement per label per hop."""
        check_bounds(depth=depth, max_nodes=max_nodes)
        graph_name(name)
        with read_transaction(self.engine) as conn:
            self._require(conn, name)
            first = cypher(
                conn,
                name,
                f"MATCH (n:{identifier(start.label)} {{id: $id}}) RETURN n",
                {"id": start.id},
            )
            if not first:
                return None
            nodes: dict[NodeRef, Node] = {start: _node_from_agtype(first[0][0])}
            edges: dict[int, Edge] = {}
            frontier: dict[str, list[str]] = {start.label: [start.id]}
            truncated = False
            for _ in range(depth):
                nxt: dict[str, list[str]] = defaultdict(list)
                for label, ids in frontier.items():
                    rows = cypher(
                        conn,
                        name,
                        f"UNWIND $ids AS i MATCH (a:{identifier(label)} {{id: i}})-[e]-(b) "
                        "RETURN a.id, e, start_id(e) = id(a), b",
                        {"ids": ids},
                        columns=("a", "e", "outgoing", "b"),
                    )
                    for a_id, e, outgoing, b in rows:
                        other = _node_from_agtype(b)
                        if other.ref not in nodes:
                            if len(nodes) >= max_nodes:
                                truncated = True
                                continue
                            nodes[other.ref] = other
                            nxt[other.label].append(other.id)
                        here = NodeRef(label, str(a_id))
                        src, dst = (here, other.ref) if outgoing else (other.ref, here)
                        edges.setdefault(
                            int(e["id"]), Edge(e["label"], src, dst, e.get("properties") or {})
                        )
                frontier = nxt
                if not frontier:
                    break
        return Subgraph(start, depth, list(nodes.values()), list(edges.values()), truncated)

    def paths(
        self, name: str, source: NodeRef, target: NodeRef, *, max_hops: int = 4, limit: int = 50
    ) -> list[Path]:
        """Variable-length undirected match, filtered to simple paths.

        Cypher's path semantics forbid repeating an edge but allow repeating a
        vertex, so non-simple paths are dropped here. The statement over-fetches
        to leave room for them.
        """
        check_bounds(max_hops=max_hops, limit=limit)
        graph_name(name)
        if source == target:
            return []
        query = (
            f"MATCH p = (a:{identifier(source.label)} {{id: $s}})"
            f"-[*1..{int(max_hops)}]-"
            f"(b:{identifier(target.label)} {{id: $t}}) "
            f"RETURN nodes(p), relationships(p) ORDER BY length(p) LIMIT {int(limit) * 4}"
        )
        with read_transaction(self.engine) as conn:
            self._require(conn, name)
            rows = cypher(conn, name, query, {"s": source.id, "t": target.id}, columns=("n", "r"))
        out: list[Path] = []
        for vertices, relationships in rows:
            refs = tuple(_node_from_agtype(v).ref for v in vertices)
            if len(set(refs)) != len(refs):
                continue
            hops = tuple(
                (r["label"], r["start_id"] == vertices[i]["id"])
                for i, r in enumerate(relationships)
            )
            out.append(Path(refs, hops))
            if len(out) >= limit:
                break
        return out

    def run_cypher(
        self,
        name: str,
        query: str,
        params: Mapping[str, Any] | None = None,
        columns: Sequence[str] = ("v",),
    ) -> list[tuple[Any, ...]]:
        """An arbitrary Cypher read for operators; writes are refused by READ ONLY."""
        graph_name(name)
        with read_transaction(self.engine) as conn:
            self._require(conn, name)
            return cypher(conn, name, query, params, columns)
