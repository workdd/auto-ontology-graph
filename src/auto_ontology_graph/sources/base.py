"""The contract a source implements: read something, return a Graph."""

from __future__ import annotations

from typing import Protocol

from auto_ontology_graph.model import Graph


class Source(Protocol):
    #: Short name used in logs and the CLI.
    name: str

    def read(self) -> Graph:
        """Return the source's current vertices and edges in full."""
        ...
