"""Postgres metadata extractor for the `provider` database.

Reads the catalog of the running provider DB (the "disparate providers" source
seeded in src/source_db) and emits source_metadata.json in the section-3 shape
from docs/context.md: tables with columns (type, nullability), declared PK/FK,
and lookup-table contents. Extra top-level fields (schemas, candidate_entities,
guide) are harmless and let the UI render a richer detection summary.

It talks to Postgres through the container's own `psql` (docker compose exec),
so no Python Postgres driver is needed — robust on any Python including 3.15a.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE_DB_DIR = ROOT / "src" / "source_db"
SCHEMAS = ["adt", "lis", "pacs", "ris", "integration"]
LOOKUP_MAX_ROWS = 50

_SCHEMA_LIST = ", ".join(f"'{s}'" for s in SCHEMAS)


def _psql(sql: str) -> list[list[str]]:
    """Run one SQL statement against the provider DB; rows as lists of cells.

    Two modes:
    - direct (container / CI): if PGHOST is set, call `psql` straight at the
      server (reads PGHOST/PGUSER/PGPASSWORD/PGDATABASE from the environment).
    - host: otherwise go through the running container with `docker compose exec`.
    """
    env = dict(os.environ, MSYS_NO_PATHCONV="1", MSYS2_ARG_CONV_EXCL="*")
    if os.getenv("PGHOST"):
        cmd = ["psql", "-tAqF", "\t", "-c", sql]
        cwd = None
    else:
        cmd = [
            "docker", "compose", "exec", "-T", "postgres",
            "psql", "-U", "postgres", "-d", "provider",
            "-tAqF", "\t", "-c", sql,
        ]
        cwd = SOURCE_DB_DIR
    out = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"psql failed: {out.stderr.strip() or out.stdout.strip()}")
    rows = []
    for line in out.stdout.splitlines():
        if line == "":
            continue
        rows.append(line.split("\t"))
    return rows


def _columns():
    sql = f"""
    SELECT table_schema, table_name, column_name,
           CASE WHEN character_maximum_length IS NOT NULL
                THEN data_type || '(' || character_maximum_length || ')'
                WHEN numeric_precision IS NOT NULL AND data_type IN ('numeric','decimal')
                THEN data_type || '(' || numeric_precision || ',' || COALESCE(numeric_scale,0) || ')'
                ELSE data_type END AS sql_type,
           is_nullable
    FROM information_schema.columns
    WHERE table_schema IN ({_SCHEMA_LIST})
    ORDER BY table_schema, table_name, ordinal_position
    """
    return _psql(sql)


def _primary_keys():
    sql = f"""
    SELECT tc.table_schema, tc.table_name, kcu.column_name, kcu.ordinal_position
    FROM information_schema.table_constraints tc
    JOIN information_schema.key_column_usage kcu
      ON tc.constraint_name = kcu.constraint_name
     AND tc.table_schema = kcu.table_schema
    WHERE tc.constraint_type = 'PRIMARY KEY'
      AND tc.table_schema IN ({_SCHEMA_LIST})
    ORDER BY 1,2,4
    """
    return _psql(sql)


def _foreign_keys():
    sql = f"""
    SELECT tc.constraint_name, tc.table_schema, tc.table_name, kcu.column_name,
           ccu.table_schema, ccu.table_name, ccu.column_name
    FROM information_schema.table_constraints tc
    JOIN information_schema.key_column_usage kcu
      ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
    JOIN information_schema.constraint_column_usage ccu
      ON tc.constraint_name = ccu.constraint_name
    WHERE tc.constraint_type = 'FOREIGN KEY'
      AND tc.table_schema IN ({_SCHEMA_LIST})
    ORDER BY 1
    """
    return _psql(sql)


def _row_counts():
    sql = f"""
    SELECT n.nspname, c.relname, c.reltuples::bigint
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relkind = 'r' AND n.nspname IN ({_SCHEMA_LIST})
    """
    return {(r[0], r[1]): int(r[2]) for r in _psql(sql)}


def _lookup_values(schema: str, table: str, code_col: str, display_col: str):
    sql = (
        f'SELECT "{code_col}", "{display_col}" FROM "{schema}"."{table}" '
        f"ORDER BY 1 LIMIT {LOOKUP_MAX_ROWS}"
    )
    rows = _psql(sql)
    return [{"code": r[0], "display": r[1] if len(r) > 1 else None} for r in rows]


def extract() -> dict:
    cols = _columns()
    pks = _primary_keys()
    fks = _foreign_keys()
    counts = _row_counts()

    # group columns by table
    tables: dict[tuple[str, str], dict] = {}
    for schema, table, col, sql_type, nullable in cols:
        key = (schema, table)
        t = tables.setdefault(key, {"columns": [], "primary_key": [], "foreign_keys": []})
        t["columns"].append(
            {
                "name": col,
                "sql_type": (sql_type or "").upper(),
                "nullable": nullable == "YES",
                "comment": None,
                "samples": [],
            }
        )

    for schema, table, col, _pos in pks:
        key = (schema, table)
        if key in tables:
            tables[key]["primary_key"].append(col)
            for c in tables[key]["columns"]:
                if c["name"] == col:
                    c["nullable"] = False

    # group FKs by constraint (supports composite)
    fk_by_constraint: dict[str, dict] = {}
    for cname, schema, table, col, rschema, rtable, rcol in fks:
        key = (schema, table)
        entry = fk_by_constraint.setdefault(
            cname,
            {"_key": key, "columns": [], "ref_table": f"{rschema}.{rtable}", "ref_columns": []},
        )
        entry["columns"].append(col)
        entry["ref_columns"].append(rcol)
    fk_targets: set[str] = set()
    for entry in fk_by_constraint.values():
        key = entry.pop("_key")
        if key in tables:
            tables[key]["foreign_keys"].append(entry)
        fk_targets.add(entry["ref_table"])

    # assemble tables + detect lookups
    out_tables = []
    lookups = []
    for (schema, table), t in sorted(tables.items()):
        name = f"{schema}.{table}"
        row_count = counts.get((schema, table), 0)
        is_fk_target = name in fk_targets
        is_lookup = len(t["columns"]) <= 3 and (row_count <= LOOKUP_MAX_ROWS or is_fk_target)
        role = "lookup" if is_lookup else ("entity" if is_fk_target else "table")
        out_tables.append(
            {
                "name": name,
                "comment": None,
                "row_count": row_count,
                "primary_key": t["primary_key"],
                "columns": t["columns"],
                "foreign_keys": t["foreign_keys"],
                "role": role,
            }
        )
        if is_lookup and len(t["columns"]) >= 2:
            code_col = t["primary_key"][0] if t["primary_key"] else t["columns"][0]["name"]
            # A real lookup has a *text* display column (code + label shape).
            text_cols = [
                c["name"] for c in t["columns"]
                if c["name"] != code_col
                and any(k in c["sql_type"] for k in ("CHAR", "TEXT", "VARCHAR"))
            ]
            if not text_cols:
                # not a code+label lookup after all — treat as an ordinary table
                out_tables[-1]["role"] = "entity" if is_fk_target else "table"
                continue
            display_col = text_cols[0]
            try:
                vals = _lookup_values(schema, table, code_col, display_col)
            except RuntimeError:
                vals = []
            lookups.append(
                {
                    "table": name,
                    "code_column": code_col,
                    "display_column": display_col,
                    "values": vals,
                }
            )

    candidate_entities = sorted(
        {t["name"] for t in out_tables if t["role"] == "entity"}
    )

    return {
        "engine": "postgresql",
        "database": "provider",
        "guide": {"package": "fhir.bupa.core", "version": "4.6.0", "indexed": False},
        "schemas": SCHEMAS,
        "candidate_entities": candidate_entities,
        "tables": out_tables,
        "lookups": lookups,
    }


def extract_to_file(out_path: Path | str) -> Path:
    data = extract()
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return out_path


if __name__ == "__main__":
    import sys

    target = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data" / "source_metadata.json"
    path = extract_to_file(target)
    data = json.loads(path.read_text(encoding="utf-8"))
    print(f"wrote {path}")
    print(f"  tables={len(data['tables'])}  lookups={len(data['lookups'])}  "
          f"candidate_entities={len(data['candidate_entities'])}")
