"""Load a FHIR Implementation Guide package into the guide store.

A real FHIR IG (e.g. fhir.bupa.core 4.6.0) ships as an npm tarball or a folder
of FHIR JSON resources: StructureDefinition (profiles + logical models),
ValueSet and CodeSystem. This loader reads those resources and builds the same
SQLite guide store (`guide.db`) the Phase B tools already query — so the agent
validates against the real guide with no other changes:

  StructureDefinition (profile)       -> campos        (one row per element)
  StructureDefinition (kind=logical)  -> modelos       (business-language fields)
  ValueSet / CodeSystem               -> terminologias (codes + display)

Because this loader writes the canonical column names directly, the column
resolution stored in `_meta` is the identity map.

Run:  python -m src.guide.load_ig <path-to-.tgz-or-folder>
      python main.py load-ig <path>
"""

from __future__ import annotations

import json
import sqlite3
import tarfile
import tempfile
from pathlib import Path

import config

# canonical columns per table (what store.py / tools.py expect)
CAMPOS_COLS = [
    "profile", "element", "min", "max", "type", "binding", "binding_strength",
    "fixed", "must_support", "logical_model", "target_profile", "profile_url", "profile_name",
]
MODELOS_COLS = ["logical_model", "field", "definition", "type"]
TERM_COLS = ["valueset", "code", "display", "system", "vs_name"]

RESOLVED = {
    "campos": {k: k for k in [
        "profile", "element", "min", "max", "type", "binding",
        "binding_strength", "fixed", "must_support", "logical_model"]},
    "modelos": {k: k for k in MODELOS_COLS},
    "terminologias": {k: k for k in ["valueset", "code", "display", "system"]},
}


# --------------------------------------------------------------------------
# Gather resources from a .tgz, a folder, a package/ folder, or Bundles.
# --------------------------------------------------------------------------
def _iter_json_texts(path: Path):
    if path.is_file() and path.suffixes[-2:] == [".tar", ".gz"] or path.suffix in (".tgz", ".gz"):
        with tarfile.open(path, "r:gz") as tf:
            for member in tf.getmembers():
                if member.isfile() and member.name.endswith(".json"):
                    f = tf.extractfile(member)
                    if f:
                        yield member.name, f.read().decode("utf-8", "replace")
    elif path.is_dir():
        for p in sorted(path.rglob("*.json")):
            yield p.name, p.read_text(encoding="utf-8", errors="replace")
    else:
        raise FileNotFoundError(f"not a .tgz or folder: {path}")


def _collect_resources(path: Path) -> list[dict]:
    resources: list[dict] = []
    for name, text in _iter_json_texts(path):
        if name in ("package.json", ".index.json") or name.endswith("/package.json"):
            continue
        try:
            doc = json.loads(text)
        except json.JSONDecodeError:
            continue
        if not isinstance(doc, dict):
            continue
        rt = doc.get("resourceType")
        if rt == "Bundle":
            for entry in doc.get("entry", []):
                res = entry.get("resource")
                if isinstance(res, dict) and res.get("resourceType"):
                    resources.append(res)
        elif rt:
            resources.append(doc)
    return resources


# --------------------------------------------------------------------------
# Extract rows from FHIR resources.
# --------------------------------------------------------------------------
def _strip_version(canonical: str | None) -> str | None:
    if not canonical:
        return canonical
    return canonical.split("|", 1)[0]


def _element_rows(sd: dict) -> list[dict]:
    profile = sd.get("id") or sd.get("name") or sd.get("url", "")
    profile_url = sd.get("url", "")
    profile_name = sd.get("name", "")
    base = sd.get("type", "")  # the resource/type this profile constrains
    elements = (sd.get("snapshot", {}).get("element")
                or sd.get("differential", {}).get("element") or [])
    rows = []
    for el in elements:
        path = el.get("path") or el.get("id") or ""
        if "." not in path:
            continue  # skip the root element (the resource itself)
        types = el.get("type", []) or []
        type_codes = "|".join(t.get("code", "") for t in types if t.get("code"))
        target_profiles = "|".join(
            tp for t in types for tp in (t.get("targetProfile") or [])
        )
        binding = el.get("binding", {}) or {}
        fixed = ""
        for k, v in el.items():
            if k.startswith("fixed") or k.startswith("pattern"):
                fixed = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
                break
        rows.append({
            "profile": profile,
            "element": path,
            "min": el.get("min"),
            "max": el.get("max"),
            "type": type_codes,
            "binding": _strip_version(binding.get("valueSet")) or "",
            "binding_strength": binding.get("strength") or "",
            "fixed": fixed,
            "must_support": "true" if el.get("mustSupport") else "false",
            "logical_model": base,
            "target_profile": target_profiles,
            "profile_url": profile_url,
            "profile_name": profile_name,
        })
    return rows


def _logical_rows(sd: dict) -> list[dict]:
    model = sd.get("id") or sd.get("name") or sd.get("url", "")
    elements = (sd.get("snapshot", {}).get("element")
                or sd.get("differential", {}).get("element") or [])
    rows = []
    for el in elements:
        path = el.get("path") or el.get("id") or ""
        if "." not in path:
            continue
        types = el.get("type", []) or []
        rows.append({
            "logical_model": model,
            "field": path,
            "definition": el.get("definition") or el.get("short") or "",
            "type": "|".join(t.get("code", "") for t in types if t.get("code")),
        })
    return rows


def _flatten_cs_concepts(concepts, system, out):
    for c in concepts or []:
        if c.get("code"):
            out.append({"code": c["code"], "display": c.get("display", ""), "system": system})
        if c.get("concept"):
            _flatten_cs_concepts(c["concept"], system, out)


def _terminology_rows(resources: list[dict]) -> list[dict]:
    # 1. index CodeSystems by url
    cs_concepts: dict[str, list[dict]] = {}
    cs_name: dict[str, str] = {}
    for r in resources:
        if r.get("resourceType") == "CodeSystem" and r.get("url"):
            out: list[dict] = []
            _flatten_cs_concepts(r.get("concept", []), r["url"], out)
            cs_concepts[r["url"]] = out
            cs_name[r["url"]] = r.get("name", r.get("id", ""))

    rows: list[dict] = []
    # 2. CodeSystems emitted under their own url
    for url, concepts in cs_concepts.items():
        for c in concepts:
            rows.append({"valueset": url, "code": c["code"], "display": c["display"],
                         "system": c["system"], "vs_name": cs_name.get(url, "")})

    # 3. ValueSets: prefer expansion, else compose (bridging whole-system includes)
    for r in resources:
        if r.get("resourceType") != "ValueSet":
            continue
        vs_url = r.get("url", r.get("id", ""))
        vs_nm = r.get("name", r.get("id", ""))
        collected: list[dict] = []
        contains = r.get("expansion", {}).get("contains")
        if contains:
            for c in contains:
                if c.get("code"):
                    collected.append({"code": c["code"], "display": c.get("display", ""),
                                      "system": c.get("system", "")})
        else:
            for inc in r.get("compose", {}).get("include", []):
                system = inc.get("system", "")
                if inc.get("concept"):
                    for c in inc["concept"]:
                        collected.append({"code": c.get("code", ""),
                                          "display": c.get("display", ""), "system": system})
                elif system in cs_concepts:  # include the whole code system
                    collected.extend(cs_concepts[system])
        for c in collected:
            rows.append({"valueset": vs_url, "code": c["code"], "display": c["display"],
                         "system": c.get("system", ""), "vs_name": vs_nm})
    return rows


# --------------------------------------------------------------------------
# Build guide.db
# --------------------------------------------------------------------------
def _is_profile(sd: dict) -> bool:
    if sd.get("kind") == "logical":
        return False
    return sd.get("derivation") == "constraint" or (
        sd.get("kind") in ("resource", "complex-type") and sd.get("baseDefinition"))


def build_guide_db(resources: list[dict], db_path: Path) -> dict:
    campos, modelos = [], []
    n_profiles = n_logical = 0
    for r in resources:
        if r.get("resourceType") != "StructureDefinition":
            continue
        if r.get("kind") == "logical":
            modelos.extend(_logical_rows(r)); n_logical += 1
        elif _is_profile(r):
            campos.extend(_element_rows(r)); n_profiles += 1
    terms = _terminology_rows(resources)

    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(f"CREATE TABLE campos ({', '.join(f'\"{c}\" TEXT' for c in CAMPOS_COLS)})")
        conn.execute(f"CREATE TABLE modelos ({', '.join(f'\"{c}\" TEXT' for c in MODELOS_COLS)})")
        conn.execute(f"CREATE TABLE terminologias ({', '.join(f'\"{c}\" TEXT' for c in TERM_COLS)})")
        conn.execute("CREATE TABLE narrative (source TEXT, section TEXT, body TEXT)")
        conn.execute("CREATE TABLE _meta (key TEXT PRIMARY KEY, value TEXT)")

        _insert(conn, "campos", CAMPOS_COLS, campos)
        _insert(conn, "modelos", MODELOS_COLS, modelos)
        _insert(conn, "terminologias", TERM_COLS, terms)
        conn.execute("INSERT INTO _meta VALUES ('resolved', ?)",
                     (json.dumps(RESOLVED, ensure_ascii=False),))
        conn.commit()
    finally:
        conn.close()

    return {
        "db_path": str(db_path),
        "profiles": n_profiles,
        "logical_models": n_logical,
        "campos_rows": len(campos),
        "modelos_rows": len(modelos),
        "terminologias_rows": len(terms),
    }


def _insert(conn, table, cols, rows):
    if not rows:
        return
    ph = ", ".join("?" for _ in cols)
    conn.executemany(
        f"INSERT INTO {table} VALUES ({ph})",
        [[_as_text(r.get(c)) for c in cols] for r in rows],
    )


def _as_text(v):
    if v is None:
        return None
    return v if isinstance(v, str) else str(v)


def load_ig(path: Path | str, db_path: Path | str = config.GUIDE_DB_PATH) -> dict:
    path = Path(path)
    resources = _collect_resources(path)
    if not resources:
        raise ValueError(f"no FHIR JSON resources found in {path}")
    summary = build_guide_db(resources, Path(db_path))
    summary["resources_read"] = len(resources)
    return summary


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        sys.exit("usage: python -m src.guide.load_ig <path-to-.tgz-or-folder>")
    s = load_ig(sys.argv[1])
    print(json.dumps(s, indent=2, ensure_ascii=False))
