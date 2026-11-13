import pytest

from auto_ontology_graph.model import Edge, Graph, Node, NodeRef, identifier, merge


@pytest.mark.parametrize("bad", ["", "1abc", "a-b", "a b", "x'); DROP", "A" * 64])
def test_identifier_rejects_unsafe_names(bad):
    with pytest.raises(ValueError):
        identifier(bad)


def test_identifier_accepts_plain_names():
    assert identifier("SEMANTIC_FK") == "SEMANTIC_FK"


def test_node_rejects_id_in_properties():
    with pytest.raises(ValueError):
        Node("Term", "1", {"id": "1"})


def test_labels_are_in_first_seen_order():
    g = Graph(
        [Node("B", "1"), Node("A", "2"), Node("B", "3")],
        [
            Edge("Y", NodeRef("B", "1"), NodeRef("A", "2")),
            Edge("X", NodeRef("A", "2"), NodeRef("B", "3")),
        ],
    )
    assert g.vertex_labels() == ("B", "A")
    assert g.edge_labels() == ("Y", "X")


def test_merge_rejects_duplicate_vertices_across_sources():
    a = Graph([Node("Term", "1")])
    with pytest.raises(ValueError, match="duplicate"):
        merge([a, Graph([Node("Term", "1")])])


def test_merge_allows_same_id_under_different_labels():
    g = merge([Graph([Node("Term", "1")]), Graph([Node("Document", "1")])])
    assert len(g.nodes) == 2


def test_dangling_edges():
    g = Graph([Node("A", "1")], [Edge("E", NodeRef("A", "1"), NodeRef("A", "2"))])
    assert len(g.dangling_edges()) == 1
