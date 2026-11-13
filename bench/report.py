"""Render bench/results/*.json as the Markdown table in README.md.

uv run python bench/report.py bench/results/ba-20000.json
"""

from __future__ import annotations

import json
import sys

LABELS = {
    "age_cypher": "AGE Cypher `-[*1..h]-`",
    "sql_recursive": "SQL recursive CTE",
    "sql_level_bfs": "SQL BFS, one query per hop",
    "python_bfs": "Python BFS (adjacency in memory)",
}


def render(path: str) -> str:
    with open(path) as f:
        r = json.load(f)
    hops = sorted(next(iter(r["results"].values())), key=int)
    g = r["graph"]
    lines = [
        f"{g['nodes']:,} vertices, {g['edges']:,} edges (preferential attachment, max degree "
        f"{g['max_degree']}), Postgres {r['environment']['postgres'].split()[0]}, AGE "
        f"{r['environment']['age']}. Median ms over {r['results']['age_cypher'][hops[0]]['pairs']}"
        f" pairs per hop count; timeout {r['timeout_ms'] // 1000} s.",
        "",
        "| Method | " + " | ".join(f"{h} hops" for h in hops) + " |",
        "| --- | " + " | ".join("---:" for _ in hops) + " |",
    ]
    for method, label in LABELS.items():
        cells = []
        for h in hops:
            cell = r["results"][method][h]
            text = f"{cell['median_ms']:,.1f}"
            if cell["timeouts"]:
                text += f" ({cell['timeouts']} timeout{'s' if cell['timeouts'] > 1 else ''})"
            cells.append(text)
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    lines.append("")
    lines.append(
        f"Python BFS excludes loading the adjacency once ({r['python_adjacency_load_ms']:,.0f} ms)."
    )
    return "\n".join(lines)


if __name__ == "__main__":
    print("\n\n".join(render(p) for p in sys.argv[1:]))
