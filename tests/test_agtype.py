from datetime import date, datetime
from decimal import Decimal

from auto_ontology_graph.store.age import parse_agtype, to_agtype_value


def test_parse_strips_type_annotations():
    text = '[{"id": 1, "label": "N", "properties": {"id": "a"}}::vertex]'
    assert parse_agtype(text) == [{"id": 1, "label": "N", "properties": {"id": "a"}}]


def test_parse_keeps_annotation_text_inside_strings():
    text = '{"properties": {"d": "x}::vertex y"}}::vertex'
    assert parse_agtype(text) == {"properties": {"d": "x}::vertex y"}}


def test_parse_handles_escaped_quotes():
    assert parse_agtype(r'"a \"b\"::edge"') == 'a "b"::edge'


def test_parse_numeric_and_none():
    assert parse_agtype("1.5::numeric") == 1.5
    assert parse_agtype(None) is None


def test_to_agtype_value():
    value = {
        "d": date(2026, 1, 2),
        "t": datetime(2026, 1, 2, 3, 4),
        "n": Decimal("1.5"),
        "l": (1, Decimal("2")),
    }
    assert to_agtype_value(value) == {
        "d": "2026-01-02",
        "t": "2026-01-02T03:04:00",
        "n": 1.5,
        "l": [1, 2.0],
    }
