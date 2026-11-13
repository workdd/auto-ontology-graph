from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine

from auto_ontology_graph.model import Edge, Graph, Node, NodeRef

TEST_URL_ENV = "AOG_TEST_URL"


def T(name: str) -> NodeRef:
    return NodeRef("Table", name)


def rentals_graph() -> Graph:
    """address is reachable from rental through customer or through staff.

    The two routes have the same length and the same degrees, so they tie on
    structure alone and mean different things: the customer's address or the
    staff member's address.
    """
    tables = ["address", "customer", "staff", "rental", "film"]
    nodes = [Node("Table", t, {"name": t}) for t in tables]
    nodes.append(Node("Term", "t-customer", {"name": "Customer", "description": 'it\'s "quoted"'}))
    joins = [
        ("customer", "address"),
        ("staff", "address"),
        ("rental", "customer"),
        ("rental", "staff"),
        ("rental", "film"),
    ]
    edges = [Edge("JOIN", T(a), T(b), {"join_columns": [f"{a}.{b}_id"]}) for a, b in joins]
    edges.append(Edge("REPRESENTS", T("customer"), NodeRef("Term", "t-customer")))
    return Graph(nodes, edges)


@pytest.fixture(scope="session")
def age_engine():
    url = os.environ.get(TEST_URL_ENV)
    if not url:
        pytest.skip(f"{TEST_URL_ENV} not set (a Postgres URL with Apache AGE)")
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://") :]
    engine = create_engine(url)
    yield engine
    engine.dispose()
