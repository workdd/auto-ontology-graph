# auto-ontology-graph

A derived property graph of an [NVIDIA Auto-Ontology](https://github.com/NVIDIA/auto-ontology) semantic layer, stored in [Apache AGE](https://age.apache.org/), with a source and store interface for adding knowledge that does not live in a relational database.

Auto-Ontology stays as it is. This package reads its Postgres tables (read-only, no import of Auto-Ontology code), writes them as a graph, and answers neighbourhood and ranked-path questions over it through a CLI, Python, and an MCP server.

Independent project. Not affiliated with or endorsed by NVIDIA. The upstream discussion is in [NVIDIA/auto-ontology#500](https://github.com/NVIDIA/auto-ontology/issues/500) (AGE projection) and [#501](https://github.com/NVIDIA/auto-ontology/issues/501) (PathRAG join routes).

## When a graph store is needed, and when it is not

The deciding question is where the ontology's raw material lives.

| Raw material | Standard approach | Separate graph store |
| --- | --- | --- |
| Relational database | Map rows to a graph as a view. W3C [R2RML](https://www.w3.org/TR/r2rml/) (2012) allows "virtual access" over the database; PostgreSQL 19 [SQL/PGQ](https://www.postgresql.org/docs/19/ddl-property-graphs.html) defines a property graph as a read-only view over tables. | Not needed. This is the case Auto-Ontology covers. |
| Documents, APIs, logs, other graphs | Extracted entities and relations must be stored somewhere. There is no table to put a view on. | Needed. |

Auto-Ontology's connectors are all SQL engines (Postgres, MySQL, Snowflake, Databricks, Trino, ClickHouse, DuckDB, ...), and its end product is text-to-SQL. Knowledge from outside those systems has no place in it. This repository is where the two meet.

### Reasons a graph model helps there

1. **Relation types are not known in advance.** Relations extracted from text or APIs keep adding types. In a relational store each new type is a table, a migration and data-access code (Auto-Ontology keeps one table per edge type). A property graph creates labels and edge types at write time. Knowledge graphs are described as suited to "diverse, dynamic, large-scale collections of data" ([Hogan et al., 2021, ACM Computing Surveys, arXiv:2003.02320](https://arxiv.org/abs/2003.02320)).
2. **Paths of unknown length are a pattern, not a program.** `(a)-[*1..5]-(b)` in Cypher. In SQL this is a recursive CTE, which blows up with per-path cycle checks (Auto-Ontology replaced it with a hand-written breadth-first search for that reason, [PR #188](https://github.com/NVIDIA/auto-ontology/pull/188)). PostgreSQL 19's SQL/PGQ does not support quantified (variable-length) patterns yet ([The Build, 2026-06-01](https://thebuild.com/blog/sqlpgq-in-postgresql-19-graph-queries-without-the-graph-database/)).
3. **There is a standard graph query language.** ISO/IEC 39075:2024 GQL, published 2024-04-12, the first new ISO database language since SQL ([IEC](https://webstore.iec.ch/publication/94107)). A Cypher-style query layer is not tied to one product.

### What this project does not claim: speed

Reason 2 is about how a query is written, not how fast it runs. On AGE it runs slower than plain SQL. Measured with [`bench/varlen.py`](bench/varlen.py) (question: is there a path of at most *h* hops between two vertices whose shortest path is exactly *h* hops):

<!-- bench:start -->
20,000 vertices, 59,991 edges (preferential attachment, max degree 465), Postgres 16.10, AGE 1.6.0. Median ms over 5 pairs per hop count; timeout 20 s.

| Method | 2 hops | 3 hops | 4 hops | 5 hops | 6 hops |
| --- | ---: | ---: | ---: | ---: | ---: |
| AGE Cypher `-[*1..h]-` | 18.5 | 9.5 | 35.5 | 185.1 | 3,573.9 (2 timeouts) |
| SQL recursive CTE | 1.7 | 2.8 | 14.6 | 56.1 | 733.1 |
| SQL BFS, one query per hop | 2.7 | 5.8 | 11.7 | 49.4 | 85.1 |
| Python BFS (adjacency in memory) | 0.0 | 0.2 | 0.6 | 1.5 | 10.7 |

Python BFS excludes loading the adjacency once (147 ms).
<!-- bench:end -->

Single machine (Docker on a laptop), synthetic graph, five pairs per cell: read it as orders of magnitude. A 2,000-vertex run is in [`bench/results/ba-2000.json`](bench/results/ba-2000.json) and shows the same order.

This agrees with other evidence:

- AGE stores vertices and edges as Postgres tables and relies on indexes; it does not have the index-free adjacency that native graph engines use ([The Build](https://thebuild.com/blog/sqlpgq-in-postgresql-19-graph-queries-without-the-graph-database/)).
- On short traversals, relational systems had the lowest read latency in an LDBC Social Network Benchmark study ([Apaci et al., GRADES 2017](https://event.cwi.nl/grades/2017/12-Apaci.pdf)).
- On Auto-Ontology's own WWI evaluation (41 questions), the Neo4j and relational versions gave the same quality ([PR #188](https://github.com/NVIDIA/auto-ontology/pull/188)).
- LightRAG plans to replace its AGE-based Postgres graph storage with a plain-table one ([v1.5.6 release notes](https://newreleases.io/project/github/HKUDS/LightRAG/release/v1.5.6)).

So the store sits behind an interface ([`GraphStore`](src/auto_ontology_graph/store/base.py)): AGE today, and the path search can run where it is fastest.

## Architecture

```
Auto-Ontology Postgres ──read-only SQL──▶ AutoOntologySource ─┐
                                                             ├─▶ Graph ─▶ GraphStore ─▶ neighbourhood
(future) documents, APIs, other graphs ──▶ other Sources ────┘            (AGE,         paths
                                                                          memory)       ranked paths (PathRAG)
                                                                                         │
                                                                         CLI · Python · MCP server
```

| Module | Role |
| --- | --- |
| `model` | `Node`, `Edge`, `Graph`. A vertex is `(label, id)`. |
| `sources.auto_ontology` | Table-to-graph mapping, checked against `information_schema` before each read. |
| `store.age` | Full rebuild in one transaction, GIN index on vertex properties, btree on edge endpoints, `READ ONLY` reads. |
| `store.memory` | Reference implementation and test oracle. The AGE store is tested to return the same results. |
| `query.ranking` | PathRAG flow scoring of candidate paths. |
| `tools`, `cli`, `mcp_server` | The same three reads: status, neighbourhood, ranked paths. |

## Quick start

```bash
# Postgres with AGE for the graph (can be the same server as Auto-Ontology)
docker run -d --name age -e POSTGRES_PASSWORD=age -p 55432:5432 apache/age:release_PG16_1.6.0

uv sync
export AOG_SOURCE_URL=postgresql://user:pass@host:5432/auto_ontology   # read-only is enough
export AOG_STORE_URL=postgresql://postgres:age@localhost:55432/postgres
uv run aog sync
uv run aog status
uv run aog neighborhood Term <term-id> --depth 2
uv run aog paths Table <table-id> Table <table-id> --k 3
uv run aog query "MATCH (t:Term)<-[:REPRESENTS]-(x:Table) RETURN t.name, x.name LIMIT 5" --columns term,table
```

MCP server (tools `graph_status`, `explore_neighborhood`, `find_paths`; none take Cypher):

```bash
uv sync --extra mcp
AOG_STORE_URL=... uv run python -m auto_ontology_graph.mcp_server
```

## Graph vocabulary

Labels and edge types are the names Auto-Ontology already uses.

| Edge | From | To | Stored in |
| --- | --- | --- | --- |
| `CONTAINS` | Database, Schema, Table | Schema, Table, Column | parent foreign keys |
| `FOREIGN_KEY` | Column | Column | `column__foreign_key` |
| `JOIN` | Table / Column | Table / Column | `table__join`, `column__join` |
| `UNION` | Column | Column | `column__union` |
| `REPRESENTS` | Table | Term | `table__term` |
| `HAS_ATTRIBUTE` | Column | ColumnAttribute | `column__has_attribute` |
| `SEMANTIC_FK` | Column | ColumnAttribute | `column__semantic_fk` |
| `PROPERTY_OF` | ColumnAttribute, SqlAttribute | Term | `*_attribute__term` |
| `TAGGED` | Tag | Term, Table, Column, attributes | `tag_target` |
| `ZONE_OF` | Zone | Database, Schema, Table | `zone_target` |

Never copied: `catalog_database.connection` (connection settings) and `catalog_column.sample_values` (raw values). A test enforces it.

## Path ranking

`ranked_paths` follows PathRAG ([Chen et al., 2025, arXiv:2502.14902](https://arxiv.org/abs/2502.14902)): resource flows from the source vertex with decay `alpha` (0.7) and is divided by fan-out at each vertex, stopping below `theta` (0.005). A path's reliability is the resource on its vertices divided by its hops. Paths that tie on structure (same length, same degrees) are separated by `boost_nodes`: each relevant intermediate vertex multiplies the score by `1 + boost`. Candidates are the simple paths at most one hop longer than the shortest.

## Development

```bash
uv sync
uv run pytest                                  # unit tests
AOG_TEST_URL=postgresql://postgres:age@localhost:55432/postgres uv run pytest   # plus AGE integration
uv run ruff check . && uv run ruff format --check .
```

CI runs the integration tests against `apache/age:release_PG16_1.6.0`.

## Status

- Source: Auto-Ontology only. Mapping checked against Auto-Ontology `main` at `fc43a82`.
- AGE 1.6.0 leaves its label cache inconsistent on the backend that dropped and recreated a graph with about 16 or more labels; the next transaction there fails with `label (relation) cache corrupted`. The store discards the writing connection after each rebuild, and a regression test covers it.
- Next: a first non-relational source, a plain-table store, and a path strategy that does not use variable-length Cypher (see the table above).

## License

Apache-2.0. See [NOTICE](NOTICE).
