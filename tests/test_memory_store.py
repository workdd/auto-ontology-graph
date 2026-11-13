import pytest

from auto_ontology_graph.model import Edge, NodeRef
from auto_ontology_graph.store import GraphNotBuilt, MemoryStore
from tests.conftest import T, rentals_graph


@pytest.fixture
def store():
    s = MemoryStore()
    s.replace("g", rentals_graph())
    return s


def test_status_counts(store):
    st = store.status("g")
    assert st["built"] is True
    assert st["vertices"] == {"Table": 5, "Term": 1}
    assert st["edges"] == {"JOIN": 5, "REPRESENTS": 1}
    assert store.status("other")["built"] is False


def test_unknown_graph_raises():
    with pytest.raises(GraphNotBuilt):
        MemoryStore().paths("g", T("a"), T("b"))


def test_unmatched_edges_are_counted():
    g = rentals_graph()
    g.edges.append(Edge("JOIN", T("rental"), T("nowhere")))
    assert MemoryStore().replace("g", g).unmatched_edges == 1


def test_neighborhood_is_undirected(store):
    sub = store.neighborhood("g", T("address"), depth=1)
    assert {n.id for n in sub.nodes} == {"address", "customer", "staff"}
    assert all(e.label == "JOIN" for e in sub.edges)


def test_neighborhood_depth_two_reaches_term(store):
    sub = store.neighborhood("g", T("address"), depth=2)
    assert NodeRef("Term", "t-customer") in {n.ref for n in sub.nodes}


def test_neighborhood_truncates(store):
    sub = store.neighborhood("g", T("rental"), depth=2, max_nodes=3)
    assert sub.truncated is True
    assert len(sub.nodes) == 3


def test_neighborhood_missing_start(store):
    assert store.neighborhood("g", T("nope")) is None


def test_paths_shortest_first_and_simple(store):
    paths = store.paths("g", T("address"), T("rental"), max_hops=4)
    assert [len(p) for p in paths] == sorted(len(p) for p in paths)
    assert {p.render() for p in paths if len(p) == 2} == {
        "(Table:address)<-[JOIN]-(Table:customer)<-[JOIN]-(Table:rental)",
        "(Table:address)<-[JOIN]-(Table:staff)<-[JOIN]-(Table:rental)",
    }
    for p in paths:
        assert len(set(p.nodes)) == len(p.nodes)


def test_paths_respect_max_hops_and_limit(store):
    assert store.paths("g", T("address"), T("film"), max_hops=2) == []
    assert len(store.paths("g", T("address"), T("film"), max_hops=3)) == 2
    assert len(store.paths("g", T("address"), T("film"), max_hops=3, limit=1)) == 1


@pytest.mark.parametrize("kwargs", [{"depth": 0}, {"depth": 6}, {"max_nodes": 0}])
def test_neighborhood_bounds(store, kwargs):
    with pytest.raises(ValueError):
        store.neighborhood("g", T("address"), **kwargs)


def test_graph_name_is_validated(store):
    with pytest.raises(ValueError):
        store.status("Bad-Name")
