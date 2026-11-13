import pytest

from auto_ontology_graph.model import Edge, Graph, Node
from auto_ontology_graph.query import flow_resources, ranked_paths
from auto_ontology_graph.store import MemoryStore
from tests.conftest import T, rentals_graph


def test_flow_decays_and_divides_by_fan_out():
    adj = {"a": {"b", "c"}, "b": {"a", "d"}, "c": {"a"}, "d": {"b"}}
    r = flow_resources(adj, "a", alpha=0.5, theta=0.0)
    assert r["a"] == 1.0
    assert r["b"] == pytest.approx(0.25)  # 0.5 * 1 / 2
    assert r["d"] == pytest.approx(0.0625)  # 0.5 * 0.25 / 2


def test_flow_sets_each_vertex_once():
    adj = {"a": {"b", "c"}, "b": {"a", "c"}, "c": {"a", "b"}}
    r = flow_resources(adj, "a", alpha=0.7, theta=0.0)
    assert r["b"] == r["c"] == pytest.approx(0.35)


def test_flow_stops_below_theta():
    adj = {"a": {"b"}, "b": {"a", "c"}, "c": {"b"}}
    r = flow_resources(adj, "a", alpha=0.7, theta=0.5)
    assert "c" not in r  # b holds 0.7, passes 0.7/2 = 0.35 < 0.5


@pytest.fixture
def store():
    s = MemoryStore()
    s.replace("g", rentals_graph())
    return s


def test_symmetric_routes_tie_on_structure(store):
    ranked = ranked_paths(store, "g", T("address"), T("rental"), k=5, max_hops=4)
    best_two = ranked[:2]
    assert best_two[0].score == best_two[1].score
    # Deterministic tie-break on the rendered path, never on storage order.
    assert "customer" in best_two[0].path.render()


@pytest.mark.parametrize("hint", ["customer", "staff"])
def test_boost_picks_the_route_through_the_hinted_vertex(store, hint):
    ranked = ranked_paths(store, "g", T("address"), T("rental"), k=2, boost_nodes=[T(hint)])
    assert T(hint) in ranked[0].path.nodes
    assert ranked[0].score > ranked[1].score


def test_extra_hops_bound():
    # a - b - d and a - c - x - d: the second is one hop longer.
    nodes = [Node("Table", n) for n in "abcdx"]
    edges = [
        Edge("JOIN", T(s), T(t))
        for s, t in [("a", "b"), ("b", "d"), ("a", "c"), ("c", "x"), ("x", "d")]
    ]
    s = MemoryStore()
    s.replace("g", Graph(nodes, edges))
    assert len(ranked_paths(s, "g", T("a"), T("d"), k=5, max_extra_hops=0)) == 1
    assert len(ranked_paths(s, "g", T("a"), T("d"), k=5, max_extra_hops=1)) == 2


def test_dilution_applies_past_a_hub():
    # Both routes a->?->z are 3 hops. One passes through hub h (many leaves)
    # before its last middle vertex; the other through a quiet vertex q.
    nodes = [Node("Table", n) for n in ["a", "h", "h2", "q", "q2", "z"]]
    nodes += [Node("Table", f"leaf{i}") for i in range(8)]
    pairs = [("a", "h"), ("h", "h2"), ("h2", "z"), ("a", "q"), ("q", "q2"), ("q2", "z")]
    pairs += [("h", f"leaf{i}") for i in range(8)]
    s = MemoryStore()
    s.replace("g", Graph(nodes, [Edge("JOIN", T(x), T(y)) for x, y in pairs]))
    ranked = ranked_paths(s, "g", T("a"), T("z"), k=2)
    assert T("q") in ranked[0].path.nodes
    assert ranked[0].score > ranked[1].score


def test_no_path_returns_empty(store):
    s = MemoryStore()
    s.replace("g", Graph([Node("Table", "a"), Node("Table", "b")]))
    assert ranked_paths(s, "g", T("a"), T("b")) == []
    assert ranked_paths(store, "g", T("address"), T("rental"), k=0) == []
