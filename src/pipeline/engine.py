"""The source-agnostic transform engine.

Inputs are the two approved configs and the source rows. Output is FHIR R4
resources. The engine knows nothing about any specific table or column: every
decision comes from the structure map's rules and the ConceptMap's entries.

structure_map.json (executable shape; a superset of the review shape the UI
shows, with a `source` block added per entity so the row can actually be read):

{
  "source_base": "https://fhir.provider.example",   # provider CodeSystem base
  "maps": [
    {
      "entity": "Patient",                           # FHIR resourceType
      "profile_url": null,                           # optional meta.profile
      "source": {"query"|"table"|..., "id_column": "...", "id_prefix": "pat"},
      "mappings": [ {source:{column}, target:{element,type}, transform, ...} ],
      "references": [ {column, target:{element,type}, reference_to:{entity,id_prefix}} ]
    }
  ]
}

Supported `transform` values: direct, identifier, phone, email, code-translation,
coding, quantity, fixed. Anything else is recorded as a warning and skipped, so a
map the engine does not fully understand still loads what it can.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

DEFAULT_SOURCE_BASE = "https://fhir.provider.example"
# FHIR `dateTime`/`instant` with a time component MUST carry a timezone, but SQL
# `timestamp without time zone` columns arrive as 'YYYY-MM-DDThh:mm:ss' (no offset)
# and Aidbox rejects them ("Datetime is too short: no timezone."). Append this
# offset to timezone-less source datetimes. Override per map with `source_timezone`.
DEFAULT_SOURCE_TZ = "-03:00"  # America/Santiago (Chile) standard offset
_DATETIME_NO_TZ = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?$")


def _ensure_datetime_tz(value: Any, tz: str) -> Any:
    """Append `tz` to a timezone-less datetime string; leave everything else.

    A bare date ('YYYY-MM-DD', no 'T') has no time and stays as-is; non-datetime
    strings never match the pattern, so names/text/codes pass through untouched.
    """
    if isinstance(value, str) and _DATETIME_NO_TZ.match(value):
        return value + (tz or DEFAULT_SOURCE_TZ)
    return value


# --------------------------------------------------------------------------
# config loading
# --------------------------------------------------------------------------
def load_configs(structure_map_path, conceptmap_path):
    """Load structure_map.json and (optional) conceptmap.json from disk."""
    sm = json.loads(Path(structure_map_path).read_text(encoding="utf-8"))
    cm = {}
    if conceptmap_path and Path(conceptmap_path).exists():
        cm = json.loads(Path(conceptmap_path).read_text(encoding="utf-8"))
    return sm, cm


def source_system_uri(base: str, table: str) -> str:
    """Mirror the UI's convention: base + /CodeSystem/<table-with-dashes>."""
    base = (base or DEFAULT_SOURCE_BASE).rstrip("/")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", str(table))
    return f"{base}/CodeSystem/{slug}"


class ConceptMapIndex:
    """Fast (source_system, source_code) -> target coding lookup.

    Built from a FHIR R4 ConceptMap (the shape the Terminology tab exports).
    """

    def __init__(self, conceptmap: dict | None):
        self._by_system: dict[str, dict[str, dict]] = {}
        for group in (conceptmap or {}).get("group", []) or []:
            src = group.get("source")
            tgt = group.get("target")
            table = self._by_system.setdefault(src or "", {})
            for el in group.get("element", []) or []:
                code = str(el.get("code"))
                targets = el.get("target") or []
                if not targets:
                    continue
                t = targets[0]
                entry = {"system": tgt, "code": t.get("code"), "display": t.get("display")}
                table[code] = entry
                table[code.upper()] = entry  # case-tolerant fallback

    def translate(self, system: str, code: str) -> dict | None:
        if code is None:
            return None
        table = self._by_system.get(system or "")
        if not table:
            return None
        return table.get(str(code)) or table.get(str(code).upper())


# --------------------------------------------------------------------------
# placement helpers (dotted FHIR element path -> nested JSON)
# --------------------------------------------------------------------------
def _walk_to_parent(resource: dict, parts: list[str]) -> tuple[dict, str]:
    cur = resource
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
    return cur, parts[-1]


def _place(resource: dict, element: str, kind: str, value: Any) -> None:
    # element is a dotted path rooted at the resourceType, e.g. "Patient.birthDate"
    parts = element.split(".")[1:]  # drop the resourceType prefix
    if not parts:
        return
    if kind == "name-family":
        name = resource.setdefault("name", [{}])
        name[0]["family"] = value
        return
    if kind == "name-given":
        name = resource.setdefault("name", [{}])
        name[0].setdefault("given", []).append(value)
        return
    if kind == "append":
        parent, key = _walk_to_parent(resource, parts)
        parent.setdefault(key, []).append(value)
        return
    # kind == "set": set a scalar or object at the dotted path
    parent, key = _walk_to_parent(resource, parts)
    parent[key] = value


# --------------------------------------------------------------------------
# per-rule value builders -> (kind, value) or None to skip
# --------------------------------------------------------------------------
def _clean(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, str):
        v = v.strip()
        return v or None
    return v


def _target_type(rule: dict) -> str:
    return (rule.get("target") or {}).get("type") or ""


def _coding_placement(rule: dict, coding: dict) -> tuple[str, Any]:
    """Shape a coding for the target: bare code, Coding, or CodeableConcept."""
    ttype = _target_type(rule)
    if ttype == "code":
        return "set", coding.get("code")
    cc_coding = {k: coding[k] for k in ("system", "code", "display") if coding.get(k) is not None}
    if ttype == "CodeableConcept":
        text = coding.get("display")
        obj = {"coding": [cc_coding]}
        if text:
            obj["text"] = text
        return "set", obj
    return "set", cc_coding  # Coding (e.g. Encounter.class in R4)


def build_rule_value(rule: dict, row: dict, cm: ConceptMapIndex,
                     source_base: str, table_hint: str, warnings: list,
                     source_tz: str = DEFAULT_SOURCE_TZ) -> tuple[str, Any] | None:
    transform = rule.get("transform", "direct")
    element = (rule.get("target") or {}).get("element", "")
    col = (rule.get("source") or {}).get("column")
    raw = _clean(row.get(col)) if col else None

    if transform == "fixed":
        val = rule.get("value")
        return ("set", val) if val is not None else None

    if raw is None and transform != "fixed":
        # nothing in the source; honour an explicit default, else skip.
        if "default" in rule:
            return ("set", rule["default"]) if not element.endswith(("name.family", "name.given")) else None
        return None

    if transform == "direct":
        if element.endswith(".name.family"):
            return ("name-family", raw)
        if element.endswith(".name.given"):
            return ("name-given", raw)
        return ("set", _ensure_datetime_tz(raw, source_tz))

    if transform == "identifier":
        ident = {"value": str(raw)}
        if rule.get("system"):
            ident["system"] = rule["system"]
        if rule.get("use"):
            ident["use"] = rule["use"]
        return ("append", ident)

    if transform in ("phone", "email"):
        cp = {"system": transform, "value": str(raw)}
        if rule.get("use"):
            cp["use"] = rule["use"]
        return ("append", cp)

    if transform == "quantity":
        try:
            num = float(raw)
            num = int(num) if num.is_integer() else num
        except (TypeError, ValueError):
            num = raw
        q: dict[str, Any] = {"value": num}
        unit = _clean(row.get(rule.get("unit_column"))) if rule.get("unit_column") else rule.get("unit")
        if unit:
            q["unit"] = unit
            q["system"] = rule.get("unit_system", "http://unitsofmeasure.org")
            q["code"] = unit
        return ("set", q)

    if transform == "coding":
        coding = {
            "system": rule.get("system"),
            "code": str(raw),
        }
        disp = _clean(row.get(rule.get("display_column"))) if rule.get("display_column") else rule.get("display")
        if disp:
            coding["display"] = disp
        return _coding_placement(rule, coding)

    if transform == "code-translation":
        src_system = rule.get("concept_map_source") or source_system_uri(source_base, table_hint)
        hit = cm.translate(src_system, raw)
        if not hit:
            if "default" in rule:
                hit = {"system": rule.get("default_system"), "code": rule["default"],
                       "display": rule.get("default_display")}
                warnings.append(
                    f"{element}: no ConceptMap entry for '{raw}' in {src_system}; used default '{rule['default']}'")
            else:
                warnings.append(f"{element}: no ConceptMap entry for '{raw}' in {src_system}; left unset")
                return None
        return _coding_placement(rule, hit)

    warnings.append(f"{element}: unsupported transform '{transform}'; skipped")
    return None


# --------------------------------------------------------------------------
# resource building
# --------------------------------------------------------------------------
def _mint_id(entity_source: dict, row: dict) -> str | None:
    idc = entity_source.get("id_column")
    if not idc or row.get(idc) is None:
        return None
    prefix = entity_source.get("id_prefix") or ""
    rawid = re.sub(r"[^A-Za-z0-9.\-]", "-", str(row[idc]))
    return f"{prefix}-{rawid}" if prefix else rawid


def _dominant_table(entity: dict) -> str:
    tables = [(m.get("source") or {}).get("table") for m in entity.get("mappings", [])]
    tables = [t for t in tables if t]
    return max(set(tables), key=tables.count) if tables else ""


def build_entity_resources(entity: dict, rows: list[dict], cm: ConceptMapIndex,
                           source_base: str,
                           source_tz: str = DEFAULT_SOURCE_TZ) -> tuple[list[dict], list[str]]:
    """Turn one entity's source rows into FHIR resources (+ warnings)."""
    rtype = entity["entity"]
    esource = entity.get("source") or {}
    table_hint = esource.get("table") or _dominant_table(entity)
    warnings: list[str] = []
    resources: list[dict] = []

    for row in rows:
        res: dict[str, Any] = {"resourceType": rtype}
        rid = _mint_id(esource, row)
        if rid:
            res["id"] = rid
        if entity.get("profile_url"):
            res["meta"] = {"profile": [entity["profile_url"]]}

        for rule in entity.get("mappings", []):
            built = build_rule_value(rule, row, cm, source_base, table_hint, warnings, source_tz)
            if built:
                kind, value = built
                if value is not None:
                    _place(res, (rule.get("target") or {}).get("element", ""), kind, value)

        for ref in entity.get("references", []):
            col = ref.get("column")
            val = _clean(row.get(col)) if col else None
            if val is None:
                continue
            rt = (ref.get("reference_to") or {})
            tgt_type = rt.get("entity")
            prefix = rt.get("id_prefix") or ""
            rawid = re.sub(r"[^A-Za-z0-9.\-]", "-", str(val))
            ref_id = f"{prefix}-{rawid}" if prefix else rawid
            reference = {"reference": f"{tgt_type}/{ref_id}"}
            if ref.get("display_column") and row.get(ref["display_column"]):
                reference["display"] = str(row[ref["display_column"]])
            _place(res, (ref.get("target") or {}).get("element", ""), "set", reference)

        resources.append(res)

    return resources, warnings


def build_resources(structure_map: dict, conceptmap: dict | None,
                    rows_by_entity: dict[str, list[dict]]):
    """Build resources for every entity that has rows. Returns {entity: {...}}."""
    cm = ConceptMapIndex(conceptmap)
    base = structure_map.get("source_base") or DEFAULT_SOURCE_BASE
    tz = structure_map.get("source_timezone") or DEFAULT_SOURCE_TZ
    out: dict[str, dict] = {}
    for entity in structure_map.get("maps", []):
        name = entity["entity"]
        rows = rows_by_entity.get(name, [])
        resources, warnings = build_entity_resources(entity, rows, cm, base, tz)
        out[name] = {"resources": resources, "warnings": warnings}
    return out


def make_transaction_bundle(resources: list[dict]) -> dict:
    """Wrap resources as a FHIR transaction Bundle of idempotent PUT upserts."""
    entries = []
    for res in resources:
        rtype = res["resourceType"]
        rid = res.get("id")
        if rid:
            request = {"method": "PUT", "url": f"{rtype}/{rid}"}
            full = f"{rtype}/{rid}"
        else:
            request = {"method": "POST", "url": rtype}
            full = None
        entry = {"resource": res, "request": request}
        if full:
            entry["fullUrl"] = full
        entries.append(entry)
    return {"resourceType": "Bundle", "type": "transaction", "entry": entries}
