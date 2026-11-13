"""A derived property graph of an Auto-Ontology semantic layer.

Sources turn rows from somewhere (today: an Auto-Ontology Postgres database)
into a :class:`~auto_ontology_graph.model.Graph`; a store keeps the graph and
answers neighbourhood and path questions over it.
"""

from auto_ontology_graph.model import Edge, Graph, Node, NodeRef

__all__ = ["Edge", "Graph", "Node", "NodeRef"]
__version__ = "0.1.0"
