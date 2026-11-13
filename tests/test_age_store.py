"""AGE store against a real Postgres with Apache AGE (set AOG_TEST_URL)."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import DBAPIError

from auto_ontology_graph.model import Edge, Graph, Node, NodeRef
from auto_ontology_graph.query import ranked_paths
from auto_ontology_graph.store import GraphNotBuilt, MemoryStore
from auto_ontology_graph.store.age import AgeStore, cypher, prepare
from tests.conftest import T, rentals_graph

GRAPH = "aog_test_store"


@pytest.fixture(scope="module")
def store(age_engine):
    s = AgeStore(age_engine, batch_size=2)  # small batches exercise batching
    report = s.replace(GRAPH, rentals_graph())
    assert report.unmatched_edges == 0
    return s


@pytest.fixture(scope="module")
def memory():
    m = MemoryStore()
    m.replace(GRAPH, rentals_graph())
    return m


def test_status(store):
    st = store.status(GRAPH)
    assert st["built"] is True and st["synced_at"]
    assert st["vertices"] == {"Table": 5, "Term": 1}
    assert st["edges"] == {"JOIN": 5, "REPRESENTS": 1}


def test_status_of_missing_graph(store):
    assert store.status("aog_never_built")["built"] is False
    with pytest.raises(GraphNotBuilt):
        store.paths("aog_never_built", T("a"), T("b"))


def test_properties_round_trip_including_quotes(store):
    sub = store.neighborhood(GRAPH, NodeRef("Term", "t-customer"), depth=1)
    term = next(n for n in sub.nodes if n.label == "Term")
    assert term.properties["description"] == 'it\'s "quoted"'
    edge = sub.edges[0]
    assert (edge.label, edge.source, edge.target) == (
        "REPRESENTS",
        T("customer"),
        NodeRef("Term", "t-customer"),
    )


@pytest.mark.parametrize("start,depth", [(T("address"), 1), (T("address"), 2), (T("rental"), 3)])
def test_neighborhood_matches_memory_store(store, memory, start, depth):
    a = store.neighborhood(GRAPH, start, depth=depth)
    b = memory.neighborhood(GRAPH, start, depth=depth)
    assert {n.ref for n in a.nodes} == {n.ref for n in b.nodes}
    key = lambda e: (e.label, e.source, e.target)  # noqa: E731
    assert sorted(map(key, a.edges)) == sorted(map(key, b.edges))


def test_neighborhood_missing_start(store):
    assert store.neighborhood(GRAPH, T("nope")) is None


@pytest.mark.parametrize(
    "src,dst,hops",
    [
        (T("address"), T("rental"), 4),
        (T("address"), T("film"), 3),
        (NodeRef("Term", "t-customer"), T("film"), 4),
    ],
)
def test_paths_match_memory_store(store, memory, src, dst, hops):
    a = store.paths(GRAPH, src, dst, max_hops=hops)
    b = memory.paths(GRAPH, src, dst, max_hops=hops)
    assert sorted(p.render() for p in a) == sorted(p.render() for p in b)
    assert [len(p) for p in a] == sorted(len(p) for p in a)


def test_ranked_paths_same_as_memory(store, memory):
    kwargs = {"k": 3, "boost_nodes": [T("staff")]}
    a = ranked_paths(store, GRAPH, T("address"), T("rental"), **kwargs)
    b = ranked_paths(memory, GRAPH, T("address"), T("rental"), **kwargs)
    assert [(r.path.render(), r.score) for r in a] == [(r.path.render(), r.score) for r in b]


def test_value_is_parameter_not_cypher(store):
    evil = "x'}) MATCH (n) DETACH DELETE n //"
    assert store.neighborhood(GRAPH, NodeRef("Table", evil)) is None
    assert store.status(GRAPH)["vertices"]["Table"] == 5


def test_run_cypher_is_read_only(store):
    rows = store.run_cypher(GRAPH, "MATCH (t:Table) RETURN count(t)")
    assert rows == [(5,)]
    with pytest.raises(DBAPIError):
        store.run_cypher(GRAPH, "CREATE (n:Table {id: 'x'}) RETURN n")
    assert store.status(GRAPH)["vertices"]["Table"] == 5


def test_replace_is_atomic_on_failure(store, age_engine):
    bad = Graph([Node("Table", "only")])
    bad.nodes.append(Node("Table", "y", {"v": object()}))  # not JSON serialisable
    with pytest.raises(TypeError):
        store.replace(GRAPH, bad)
    assert store.status(GRAPH)["vertices"]["Table"] == 5


def _many_labels(n: int = 10) -> Graph:
    labels = [f"L{i}" for i in range(n)]
    nodes = [Node(label, "1") for label in labels]
    edges = [
        Edge(f"E{i}", NodeRef(labels[i], "1"), NodeRef(labels[(i + 1) % n], "1")) for i in range(n)
    ]
    return Graph(nodes, edges)


def test_rebuild_with_many_labels_leaves_pool_usable(age_engine):
    # Regression: AGE 1.6 corrupts its label cache on the backend that dropped
    # and recreated a graph with ~16 labels; the next read there failed.
    engine = create_engine(age_engine.url, pool_size=1, max_overflow=0)
    s = AgeStore(engine)
    for _ in range(3):
        s.replace("aog_test_labels", _many_labels())
        assert s.neighborhood("aog_test_labels", NodeRef("L0", "1")) is not None
    engine.dispose()


def test_rebuild_does_not_break_other_backends(age_engine):
    s = AgeStore(age_engine)
    s.replace("aog_test_labels", _many_labels())
    reader = create_engine(age_engine.url, pool_size=1, max_overflow=0)
    query = "MATCH (n:L0) RETURN count(n)"
    with reader.connect() as conn:
        for _ in range(3):
            trans = conn.begin()
            prepare(conn)
            assert cypher(conn, "aog_test_labels", query) == [(1,)]
            trans.rollback()
            s.replace("aog_test_labels", _many_labels())
    reader.dispose()
