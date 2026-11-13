"""Read an Auto-Ontology-shaped schema and sync it into AGE (set AOG_TEST_URL).

The fixture builds only the tables and columns the mapping reads, in a
throwaway schema, plus the two columns that must never be copied.
"""

import uuid

import pytest

from auto_ontology_graph import tools
from auto_ontology_graph.model import NodeRef
from auto_ontology_graph.sources.auto_ontology import (
    AutoOntologySource,
    SchemaMismatch,
    required_columns,
)
from auto_ontology_graph.store.age import AgeStore

SCHEMA = "aog_test_src"
GRAPH = "aog_test_src_graph"

ID = {
    name: str(uuid.uuid5(uuid.NAMESPACE_DNS, name))
    for name in [
        "db",
        "sch",
        "t_address",
        "t_customer",
        "t_staff",
        "t_rental",
        "c_customer_address_id",
        "c_address_id",
        "term_customer",
        "attr_customer",
        "tag_pii",
        "zone_sales",
    ]
}

_TYPES = {
    "synonyms": "text[]",
    "refs": "text[]",
    "pk": "text[]",
    "join_columns": "jsonb",
    "is_junction_table": "boolean",
    "is_unique": "boolean",
    "is_nullable": "boolean",
    "certified": "boolean",
    "enabled": "boolean",
    "use_in_join_paths": "boolean",
    "ordinal_position": "integer",
}


def _ddl() -> list[str]:
    statements = [f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE', f'CREATE SCHEMA "{SCHEMA}"']
    extra = {"catalog_database": {"connection"}, "catalog_column": {"sample_values"}}
    for table, columns in required_columns().items():
        cols = []
        for c in sorted(columns | extra.get(table, set())):
            kind = "uuid" if c == "id" or c.endswith("_id") else _TYPES.get(c, "text")
            cols.append(f'"{c}" {kind}')
        statements.append(f'CREATE TABLE "{SCHEMA}"."{table}" ({", ".join(cols)})')
    return statements


def _seed(conn) -> None:
    def ins(table, **values):
        cols = ", ".join(f'"{k}"' for k in values)
        marks = ", ".join(f"%({k})s" for k in values)
        conn.exec_driver_sql(f'INSERT INTO "{SCHEMA}"."{table}" ({cols}) VALUES ({marks})', values)

    ins(
        "catalog_database",
        id=ID["db"],
        name="shop",
        dialect="postgres",
        connection="postgresql://secret@host/db",
    )
    ins("catalog_schema", id=ID["sch"], name="public", database_id=ID["db"])
    for t in ["address", "customer", "staff", "rental"]:
        ins("catalog_table", id=ID[f"t_{t}"], name=t, schema_id=ID["sch"], pk=["id"])
    ins(
        "catalog_column",
        id=ID["c_customer_address_id"],
        name="address_id",
        table_id=ID["t_customer"],
        sample_values="555-0100",
    )
    ins("catalog_column", id=ID["c_address_id"], name="id", table_id=ID["t_address"])
    ins(
        "column__foreign_key",
        source_column_id=ID["c_customer_address_id"],
        target_column_id=ID["c_address_id"],
    )
    for a, b in [
        ("customer", "address"),
        ("staff", "address"),
        ("rental", "customer"),
        ("rental", "staff"),
    ]:
        ins(
            "table__join",
            source_table_id=ID[f"t_{a}"],
            target_table_id=ID[f"t_{b}"],
            join_columns='[{"source": "x", "target": "y"}]',
        )
    ins("term", id=ID["term_customer"], name="Customer", synonyms=["client", "buyer"])
    ins("table__term", table_id=ID["t_customer"], term_id=ID["term_customer"])
    ins("column_attribute", id=ID["attr_customer"], name="customer address", certified=True)
    ins(
        "column__has_attribute",
        column_id=ID["c_customer_address_id"],
        attribute_id=ID["attr_customer"],
    )
    ins("column_attribute__term", attribute_id=ID["attr_customer"], term_id=ID["term_customer"])
    ins("tag", id=ID["tag_pii"], name="pii")
    ins("tag_target", tag_id=ID["tag_pii"], column_id=ID["c_customer_address_id"])
    ins("zone", id=ID["zone_sales"], name="sales", enabled=True)
    ins("zone_target", zone_id=ID["zone_sales"], table_id=ID["t_rental"])


@pytest.fixture(scope="module")
def source(age_engine):
    with age_engine.begin() as conn:
        for statement in _ddl():
            conn.exec_driver_sql(statement)
        _seed(conn)
    yield AutoOntologySource(age_engine, schema=SCHEMA)
    with age_engine.begin() as conn:
        conn.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE')


def test_check_schema_passes(source, age_engine):
    with age_engine.connect() as conn:
        assert source.check_schema(conn) == []


def test_missing_columns_are_reported(source, age_engine):
    with age_engine.begin() as conn:
        conn.exec_driver_sql(f'ALTER TABLE "{SCHEMA}"."term" RENAME COLUMN synonyms TO aliases')
    try:
        with pytest.raises(SchemaMismatch, match="term.synonyms"):
            source.read()
    finally:
        with age_engine.begin() as conn:
            conn.exec_driver_sql(f'ALTER TABLE "{SCHEMA}"."term" RENAME COLUMN aliases TO synonyms')


def test_read_builds_every_label_and_edge_type(source):
    g = source.read()
    assert set(g.vertex_labels()) == {
        "Database",
        "Schema",
        "Table",
        "Column",
        "Term",
        "ColumnAttribute",
        "Tag",
        "Zone",
    }
    assert set(g.edge_labels()) == {
        "CONTAINS",
        "FOREIGN_KEY",
        "JOIN",
        "REPRESENTS",
        "HAS_ATTRIBUTE",
        "PROPERTY_OF",
        "TAGGED",
        "ZONE_OF",
    }
    assert g.dangling_edges() == []
    term = next(n for n in g.nodes if n.label == "Term")
    assert term.properties["synonyms"] == ["client", "buyer"]


def test_secrets_are_not_copied(source):
    g = source.read()
    for n in g.nodes:
        assert "connection" not in n.properties
        assert "sample_values" not in n.properties
    assert all("secret" not in repr(n.properties) for n in g.nodes)


def test_sync_and_query_end_to_end(source, age_engine):
    store = AgeStore(age_engine)
    report = store.replace(GRAPH, source.read())
    assert report.unmatched_edges == 0
    assert report.vertices["Table"] == 4

    near = tools.neighborhood(store, GRAPH, "Term", ID["term_customer"], depth=2)
    names = {n["properties"].get("name") for n in near["nodes"]}
    assert {"Customer", "customer", "customer address", "address", "rental"} <= names

    out = tools.find_paths(
        store,
        GRAPH,
        {"label": "Table", "id": ID["t_address"]},
        {"label": "Table", "id": ID["t_rental"]},
        k=2,
        boost=[{"label": "Table", "id": ID["t_staff"]}],
    )
    assert [n["id"] for n in out["paths"][0]["nodes"]] == [
        ID["t_address"],
        ID["t_staff"],
        ID["t_rental"],
    ]

    term = NodeRef("Term", ID["term_customer"])
    paths = store.paths(GRAPH, term, NodeRef("Zone", ID["zone_sales"]), max_hops=4)
    assert paths and paths[0].render().endswith(f"(Zone:{ID['zone_sales']})")
