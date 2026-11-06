"""Read rows from the source database, source-agnostically.

Like the metadata extractor, this talks to Postgres through `psql` so no Python
Postgres driver is required (robust on any Python, including 3.15a). Two modes,
same precedence as src/extractor/pg_metadata_extractor.py:

  - direct: PGHOST set (the app container, or a host with psql) -> call psql
    straight at the server.
  - docker: otherwise go through a running container. `docker compose exec` in
    src/source_db by default; set PROVIDER_PSQL_CONTAINER=<name> to target a
    specific container with `docker exec` instead.

Each entity in the structure map supplies either an explicit read `query`
(needed when the FHIR resource draws on a join) or just a `table`; this module
turns whatever it is into a list of row dicts via Postgres `row_to_json`, so the
engine never has to know the shape in advance.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE_DB_DIR = ROOT / "src" / "source_db"


def _run_psql(sql: str) -> str:
    env = dict(os.environ, MSYS_NO_PATHCONV="1", MSYS2_ARG_CONV_EXCL="*")
    container = os.getenv("PROVIDER_PSQL_CONTAINER")
    db = os.getenv("PGDATABASE", "provider")
    if os.getenv("PGHOST"):
        cmd = ["psql", "-tAq", "-c", sql]
        cwd = None
    elif container:
        cmd = ["docker", "exec", "-e", "PGPASSWORD=" + os.getenv("PGPASSWORD", "postgres"),
               container, "psql", "-U", os.getenv("PGUSER", "postgres"), "-d", db,
               "-tAq", "-c", sql]
        cwd = None
    else:
        cmd = ["docker", "compose", "exec", "-T", "postgres",
               "psql", "-U", "postgres", "-d", db, "-tAq", "-c", sql]
        cwd = SOURCE_DB_DIR
    out = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"psql failed: {out.stderr.strip() or out.stdout.strip()}")
    return out.stdout


def _as_json_rows(sql_select: str, limit: int | None) -> list[dict]:
    """Wrap a SELECT so Postgres hands back one JSON array of row objects."""
    inner = sql_select.strip().rstrip(";")
    if limit:
        inner = f"SELECT * FROM ({inner}) _sub LIMIT {int(limit)}"
    wrapped = f"SELECT COALESCE(json_agg(row_to_json(_t)), '[]'::json) FROM ({inner}) _t"
    raw = _run_psql(wrapped).strip()
    if not raw:
        return []
    return json.loads(raw)


def read_rows(source: dict, limit: int | None = None) -> list[dict]:
    """Return the source rows for one entity as a list of dicts.

    `source` is the structure map entity's `source` block:
      {"query": "SELECT ..."}            -- used verbatim (joins, filters, aliases)
      {"table": "adt.patient", "columns": ["mrn", ...], "where": "active = true"}
    """
    query = (source or {}).get("query")
    if not query:
        table = (source or {}).get("table")
        if not table:
            raise ValueError("entity.source needs either a 'query' or a 'table'")
        cols = source.get("columns")
        col_sql = ", ".join(f'"{c}"' if "." not in c else c for c in cols) if cols else "*"
        query = f"SELECT {col_sql} FROM {table}"
        where = source.get("where")
        if where:
            query += f" WHERE {where}"
    return _as_json_rows(query, limit)
