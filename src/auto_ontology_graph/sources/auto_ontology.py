"""Read an Auto-Ontology Postgres database as a property graph.

This package does not import Auto-Ontology. It reads the tables directly, by
name, from a read-only connection. The mapping below is the one place that
says which rows become which vertices and edges; labels and edge types are the
names Auto-Ontology already uses for the same things (``Database``, ``Term``,
``REPRESENTS``, ``SEMANTIC_FK``, ...).

Because the table layout belongs to another project, :meth:`check_schema`
compares the mapping with ``information_schema`` before every read and fails
with the list of missing tables and columns, instead of producing a graph with
quietly missing parts.

Every edge comes from one of two table shapes, and :class:`EdgeSpec` covers
both with the same fields:

* an **association table** (``column__foreign_key``): source and target are its
  two id columns;
* a **parent foreign key** (``catalog_table.schema_id``): the table is the
  child, the source column is the parent FK and the target column is ``id``.

Polymorphic tables (``tag_target``, ``zone_target``) carry one nullable FK per
target kind, so each kind is its own spec and rows whose target is NULL are
skipped.

**Never copied.** ``catalog_database.connection`` holds connection settings
and ``catalog_column.sample_values`` holds raw customer values. Neither is
metadata a traversal needs, and a second copy is a second place to protect.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, text
from sqlalchemy.engine import Engine

from auto_ontology_graph.model import Edge, Graph, Node, NodeRef, identifier

DB, SCHEMA, TABLE, COLUMN = "Database", "Schema", "Table", "Column"
TERM, COLUMN_ATTRIBUTE, SQL_ATTRIBUTE = "Term", "ColumnAttribute", "SqlAttribute"
TAG, ZONE = "Tag", "Zone"


@dataclass(frozen=True)
class NodeSpec:
    label: str
    table: str
    properties: tuple[str, ...]


@dataclass(frozen=True)
class EdgeSpec:
    label: str
    source_label: str
    target_label: str
    table: str
    source_column: str
    target_column: str
    properties: tuple[str, ...] = ()


NODES: tuple[NodeSpec, ...] = (
    NodeSpec(DB, "catalog_database", ("name", "dialect", "description")),
    NodeSpec(SCHEMA, "catalog_schema", ("name", "description")),
    NodeSpec(
        TABLE, "catalog_table", ("name", "description", "table_type", "is_junction_table", "pk")
    ),
    NodeSpec(
        COLUMN,
        "catalog_column",
        (
            "name",
            "description",
            "data_type",
            "format",
            "is_unique",
            "is_nullable",
            "ordinal_position",
        ),
    ),
    NodeSpec(TERM, "term", ("name", "description", "source", "synonyms")),
    NodeSpec(
        COLUMN_ATTRIBUTE,
        "column_attribute",
        ("name", "description", "datatype", "source", "certified"),
    ),
    NodeSpec(
        SQL_ATTRIBUTE, "sql_attribute", ("name", "description", "expression", "source", "certified")
    ),
    NodeSpec(TAG, "tag", ("name",)),
    NodeSpec(ZONE, "zone", ("name", "description", "enabled")),
)


def _contains(child: str, parent_label: str, child_label: str, fk: str) -> EdgeSpec:
    return EdgeSpec("CONTAINS", parent_label, child_label, child, fk, "id")


EDGES: tuple[EdgeSpec, ...] = (
    _contains("catalog_schema", DB, SCHEMA, "database_id"),
    _contains("catalog_table", SCHEMA, TABLE, "schema_id"),
    _contains("catalog_column", TABLE, COLUMN, "table_id"),
    EdgeSpec(
        "FOREIGN_KEY", COLUMN, COLUMN, "column__foreign_key", "source_column_id", "target_column_id"
    ),
    EdgeSpec(
        "JOIN", TABLE, TABLE, "table__join", "source_table_id", "target_table_id", ("join_columns",)
    ),
    EdgeSpec(
        "JOIN", COLUMN, COLUMN, "column__join", "source_column_id", "target_column_id", ("refs",)
    ),
    EdgeSpec(
        "UNION", COLUMN, COLUMN, "column__union", "source_column_id", "target_column_id", ("refs",)
    ),
    EdgeSpec("REPRESENTS", TABLE, TERM, "table__term", "table_id", "term_id"),
    EdgeSpec(
        "HAS_ATTRIBUTE",
        COLUMN,
        COLUMN_ATTRIBUTE,
        "column__has_attribute",
        "column_id",
        "attribute_id",
    ),
    EdgeSpec(
        "SEMANTIC_FK",
        COLUMN,
        COLUMN_ATTRIBUTE,
        "column__semantic_fk",
        "column_id",
        "attribute_id",
        ("use_in_join_paths",),
    ),
    EdgeSpec(
        "PROPERTY_OF", COLUMN_ATTRIBUTE, TERM, "column_attribute__term", "attribute_id", "term_id"
    ),
    EdgeSpec("PROPERTY_OF", SQL_ATTRIBUTE, TERM, "sql_attribute__term", "attribute_id", "term_id"),
    *(
        EdgeSpec("TAGGED", TAG, target, "tag_target", "tag_id", column)
        for target, column in (
            (TERM, "term_id"),
            (TABLE, "table_id"),
            (COLUMN, "column_id"),
            (COLUMN_ATTRIBUTE, "column_attribute_id"),
            (SQL_ATTRIBUTE, "sql_attribute_id"),
        )
    ),
    *(
        EdgeSpec("ZONE_OF", ZONE, target, "zone_target", "zone_id", column)
        for target, column in ((DB, "database_id"), (SCHEMA, "schema_id"), (TABLE, "table_id"))
    ),
)

#: Checked by the tests so a later edit to NODES cannot copy these by accident.
EXCLUDED_COLUMNS: frozenset[tuple[str, str]] = frozenset(
    {("catalog_database", "connection"), ("catalog_column", "sample_values")}
)


def required_columns() -> dict[str, set[str]]:
    """Every table and column the mapping reads."""
    need: dict[str, set[str]] = {}
    for n in NODES:
        need.setdefault(n.table, set()).update({"id", *n.properties})
    for e in EDGES:
        need.setdefault(e.table, set()).update({e.source_column, e.target_column, *e.properties})
    return need


class SchemaMismatch(RuntimeError):
    """The database does not have the tables or columns the mapping reads."""


def _q(name: str) -> str:
    return f'"{identifier(name)}"'


class AutoOntologySource:
    name = "auto_ontology"

    def __init__(self, engine: Engine, *, schema: str = "public") -> None:
        self.engine = engine
        self.schema = identifier(schema)

    def check_schema(self, conn: Connection) -> list[str]:
        """``table.column`` names the mapping needs but the database lacks."""
        rows = conn.execute(
            text(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = :schema"
            ),
            {"schema": self.schema},
        ).all()
        have: dict[str, set[str]] = {}
        for table, column in rows:
            have.setdefault(table, set()).add(column)
        missing = []
        for table, columns in sorted(required_columns().items()):
            if table not in have:
                missing.append(f"{table} (table)")
                continue
            missing.extend(f"{table}.{c}" for c in sorted(columns - have[table]))
        return missing

    def read(self) -> Graph:
        graph = Graph()
        with self.engine.connect() as conn:
            trans = conn.begin()
            try:
                conn.exec_driver_sql("SET TRANSACTION READ ONLY")
                missing = self.check_schema(conn)
                if missing:
                    raise SchemaMismatch(
                        "the database does not match the Auto-Ontology layout this "
                        f"version reads; missing: {', '.join(missing)}"
                    )
                for spec in NODES:
                    graph.nodes.extend(self._nodes(conn, spec))
                for spec in EDGES:
                    graph.edges.extend(self._edges(conn, spec))
            finally:
                trans.rollback()
        return graph

    def _table(self, table: str) -> str:
        return f"{_q(self.schema)}.{_q(table)}"

    def _nodes(self, conn: Connection, spec: NodeSpec) -> list[Node]:
        cols = ", ".join(_q(c) for c in ("id", *spec.properties))
        result = conn.exec_driver_sql(
            f"SELECT {cols} FROM {self._table(spec.table)} ORDER BY {_q('id')}"
        )
        out = []
        for row in result:
            values = dict(row._mapping)
            node_id = str(values.pop("id"))
            out.append(Node(spec.label, node_id, _clean(values)))
        return out

    def _edges(self, conn: Connection, spec: EdgeSpec) -> list[Edge]:
        src, dst = _q(spec.source_column), _q(spec.target_column)
        props = "".join(f", {_q(c)}" for c in spec.properties)
        result = conn.exec_driver_sql(
            f"SELECT {src} AS s, {dst} AS t{props} FROM {self._table(spec.table)} "
            f"WHERE {src} IS NOT NULL AND {dst} IS NOT NULL ORDER BY 1, 2"
        )
        out = []
        for row in result:
            values = dict(row._mapping)
            s, t = str(values.pop("s")), str(values.pop("t"))
            out.append(
                Edge(
                    spec.label,
                    NodeRef(spec.source_label, s),
                    NodeRef(spec.target_label, t),
                    _clean(values),
                )
            )
        return out


def _clean(values: dict[str, Any]) -> dict[str, Any]:
    return {k: _plain(v) for k, v in values.items() if v is not None}


def _plain(value: Any) -> Any:
    """UUIDs and other driver types become strings; containers are walked."""
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, list | tuple):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    return value
