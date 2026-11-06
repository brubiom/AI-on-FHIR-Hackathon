"""Orchestrate the load: read -> transform -> POST to Aidbox.

Entities are processed in the order the structure map lists them, which is also
dependency order (Patient before Encounter before Observation), so references
always point at resources already loaded. Each entity's resources are chunked
into transaction Bundles of idempotent PUT upserts, so a re-run updates in place
instead of duplicating.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .aidbox import AidboxClient, AidboxError
from .engine import build_entity_resources, ConceptMapIndex, make_transaction_bundle
from .source import read_rows


@dataclass
class PipelineResult:
    dry_run: bool = False
    endpoint: str = ""
    entities: list[dict] = field(default_factory=list)   # per-entity summary
    bundles: list[dict] = field(default_factory=list)     # only when dry_run
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def total_resources(self) -> int:
        return sum(e.get("resources", 0) for e in self.entities)

    @property
    def total_loaded(self) -> int:
        return sum(e.get("loaded", 0) for e in self.entities)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict:
        d = {
            "ok": self.ok,
            "dry_run": self.dry_run,
            "endpoint": self.endpoint,
            "total_resources": self.total_resources,
            "total_loaded": self.total_loaded,
            "entities": self.entities,
            "warnings": self.warnings,
            "errors": self.errors,
        }
        if self.dry_run:
            d["bundles"] = self.bundles
        return d


def resolve_configs(structure_map_path=None, conceptmap_path=None,
                    structure_map=None, conceptmap=None, sample_dir=None):
    """Return (structure_map, conceptmap) from in-memory values, files, or sample.

    Precedence: explicit in-memory dicts (what the UI sends) > files on disk >
    the bundled sample (so the button works before any config is saved).
    """
    sm = structure_map
    if sm is None and structure_map_path and Path(structure_map_path).exists():
        sm = json.loads(Path(structure_map_path).read_text(encoding="utf-8"))
    if sm is None and sample_dir:
        p = Path(sample_dir) / "structure_map.json"
        if p.exists():
            sm = json.loads(p.read_text(encoding="utf-8"))
    if sm is None:
        raise ValueError("no structure map provided (no in-memory map, no file, no sample)")

    cm = conceptmap
    if cm is None and conceptmap_path and Path(conceptmap_path).exists():
        cm = json.loads(Path(conceptmap_path).read_text(encoding="utf-8"))
    if cm is None and sample_dir:
        p = Path(sample_dir) / "conceptmap.json"
        if p.exists():
            cm = json.loads(p.read_text(encoding="utf-8"))
    return sm, (cm or {})


def run_pipeline(
    *,
    structure_map: dict | None = None,
    conceptmap: dict | None = None,
    structure_map_path: str | Path | None = None,
    conceptmap_path: str | Path | None = None,
    sample_dir: str | Path | None = None,
    client: AidboxClient | None = None,
    entities: list[str] | None = None,
    row_limit: int | None = None,
    bundle_size: int = 100,
    dry_run: bool = False,
    on_progress=None,
) -> PipelineResult:
    """Run the whole load. Pass either in-memory configs or paths to them.

    - `client`: an AidboxClient (required unless dry_run).
    - `entities`: restrict to these FHIR resource types (default: all in the map).
    - `dry_run`: build and validate only; no network. Bundles are returned.
    """
    sm, cm = resolve_configs(structure_map_path, conceptmap_path,
                             structure_map, conceptmap, sample_dir)
    cm_index = ConceptMapIndex(cm)
    base = sm.get("source_base") or "https://fhir.provider.example"
    result = PipelineResult(dry_run=dry_run, endpoint=(client.fhir_base if client else ""))

    wanted = set(entities) if entities else None

    for entity in sm.get("maps", []):
        name = entity.get("entity")
        if wanted and name not in wanted:
            continue
        summary: dict[str, Any] = {"entity": name, "rows": 0, "resources": 0,
                                   "loaded": 0, "warnings": [], "error": None}
        if on_progress:
            on_progress(f"reading {name}…")
        try:
            rows = read_rows(entity.get("source") or {}, limit=row_limit)
        except Exception as exc:  # noqa: BLE001 — surface read failure per entity
            summary["error"] = f"read failed: {exc}"
            result.errors.append(f"{name}: {summary['error']}")
            result.entities.append(summary)
            continue
        summary["rows"] = len(rows)

        resources, warnings = build_entity_resources(entity, rows, cm_index, base)
        summary["resources"] = len(resources)
        summary["warnings"] = warnings
        result.warnings.extend(f"{name}: {w}" for w in warnings)

        if not resources:
            result.entities.append(summary)
            continue

        # chunk into transaction bundles
        for i in range(0, len(resources), max(1, bundle_size)):
            chunk = resources[i:i + bundle_size]
            bundle = make_transaction_bundle(chunk)
            if dry_run:
                result.bundles.append(bundle)
                summary["loaded"] += len(chunk)
                continue
            if client is None:
                summary["error"] = "no Aidbox client configured"
                result.errors.append(f"{name}: {summary['error']}")
                break
            if on_progress:
                on_progress(f"POST {name} [{i + 1}-{i + len(chunk)}/{len(resources)}]…")
            try:
                client.transaction(bundle)
                summary["loaded"] += len(chunk)
            except AidboxError as exc:
                detail = exc.body if isinstance(exc.body, (str, dict)) else str(exc)
                msg = f"transaction failed (HTTP {exc.status}): {json.dumps(detail)[:500]}" \
                    if isinstance(detail, dict) else f"transaction failed: {detail}"
                summary["error"] = msg
                result.errors.append(f"{name}: {msg}")
                break

        result.entities.append(summary)

    return result
