from auto_ontology_graph.model import identifier
from auto_ontology_graph.sources.auto_ontology import (
    EDGES,
    EXCLUDED_COLUMNS,
    NODES,
    required_columns,
)


def test_excluded_columns_are_never_read():
    for table, column in EXCLUDED_COLUMNS:
        assert column not in required_columns().get(table, set())


def test_all_names_are_identifiers():
    for n in NODES:
        identifier(n.label)
        identifier(n.table)
        for p in n.properties:
            identifier(p)
    for e in EDGES:
        for name in (
            e.label,
            e.source_label,
            e.target_label,
            e.table,
            e.source_column,
            e.target_column,
            *e.properties,
        ):
            identifier(name)


def test_edges_connect_known_labels():
    labels = {n.label for n in NODES}
    for e in EDGES:
        assert e.source_label in labels and e.target_label in labels


def test_vocabulary():
    assert [n.label for n in NODES] == [
        "Database",
        "Schema",
        "Table",
        "Column",
        "Term",
        "ColumnAttribute",
        "SqlAttribute",
        "Tag",
        "Zone",
    ]
    assert list(dict.fromkeys(e.label for e in EDGES)) == [
        "CONTAINS",
        "FOREIGN_KEY",
        "JOIN",
        "UNION",
        "REPRESENTS",
        "HAS_ATTRIBUTE",
        "SEMANTIC_FK",
        "PROPERTY_OF",
        "TAGGED",
        "ZONE_OF",
    ]
