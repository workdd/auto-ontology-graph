"""Settings from the environment."""

from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

SOURCE_URL_ENV = "AOG_SOURCE_URL"
STORE_URL_ENV = "AOG_STORE_URL"
GRAPH_ENV = "AOG_GRAPH"
DEFAULT_GRAPH = "auto_ontology"


def _url(value: str) -> str:
    # Accept plain libpq URLs; SQLAlchemy needs the driver named.
    for prefix in ("postgresql://", "postgres://"):
        if value.startswith(prefix):
            return "postgresql+psycopg://" + value[len(prefix) :]
    return value


def engine_from_env(var: str) -> Engine:
    value = os.environ.get(var, "").strip()
    if not value:
        raise SystemExit(f"{var} is not set (a Postgres URL)")
    return create_engine(_url(value), pool_pre_ping=True)


def graph_from_env() -> str:
    return os.environ.get(GRAPH_ENV, DEFAULT_GRAPH).strip() or DEFAULT_GRAPH
