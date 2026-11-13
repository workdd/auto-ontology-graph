from auto_ontology_graph import tools
from auto_ontology_graph.store import MemoryStore
from tests.conftest import rentals_graph


def _store():
    s = MemoryStore()
    s.replace("g", rentals_graph())
    return s


def test_neighborhood_found_and_missing():
    s = _store()
    out = tools.neighborhood(s, "g", "Term", "t-customer", depth=1)
    assert out["found"] is True
    assert {n["id"] for n in out["nodes"]} == {"t-customer", "customer"}
    assert tools.neighborhood(s, "g", "Term", "nope")["found"] is False


def test_find_paths_shape():
    out = tools.find_paths(
        _store(),
        "g",
        {"label": "Table", "id": "address"},
        {"label": "Table", "id": "rental"},
        k=2,
        boost=[{"label": "Table", "id": "staff"}],
    )
    first = out["paths"][0]
    assert first["rank"] == 1
    assert first["hops"] == 2
    assert [n["id"] for n in first["nodes"]] == ["address", "staff", "rental"]
    assert first["edges"] == [{"type": "JOIN", "forward": False}] * 2
