"""Structure Map Agent — pipeline CLI.

End-to-end orchestration of the five build steps (spec section 8):

  1. build-db     Build the synthetic source database (fixture).
  2. extract      Extract source_metadata.json from the source database.
  3. load-guide   Load the implementation-guide CSVs into the guide store.
  4. agent        Run the agent -> structure_map.json.
  5. review       Render the HTML review view.
  all             Run every step in order.

Examples:
  python main.py build-db
  python main.py extract
  python main.py load-guide
  python main.py agent
  python main.py review
  python main.py all
"""

from __future__ import annotations

import argparse
import sys

import config


def cmd_build_db(_args) -> int:
    from src.source_db.build_synthetic_db import build

    path = build()
    print(f"[1/5] synthetic source DB -> {path}")
    return 0


def cmd_extract(_args) -> int:
    from src.extractor.metadata_extractor import extract_to_file

    if not config.SOURCE_DB_PATH.exists():
        print("source DB missing; run `python main.py build-db` first.", file=sys.stderr)
        return 1
    path = extract_to_file()
    print(f"[2/5] source metadata -> {path}")
    return 0


def cmd_load_guide(_args) -> int:
    from src.guide.load_guide import load

    try:
        summary = load()
    except FileNotFoundError as exc:
        print(f"[3/5] {exc}", file=sys.stderr)
        return 1
    print(f"[3/5] guide store -> {summary['db_path']}  rows={summary['row_counts']}  "
          f"narrative_sections={summary['narrative_sections']}")
    for table, cols in summary["resolved_columns"].items():
        unresolved = [k for k, v in cols.items() if not v]
        if unresolved:
            print(f"      [warn] '{table}': unresolved columns {unresolved} — "
                  "set them in guide/columns.json if tools need them.")
    return 0


def cmd_load_ig(args) -> int:
    from src.guide.load_ig import load_ig

    path = args.path
    if not path:
        print("usage: python main.py load-ig <path-to-.tgz-or-folder>", file=sys.stderr)
        return 1
    try:
        summary = load_ig(path)
    except (FileNotFoundError, ValueError) as exc:
        print(f"[3/5] {exc}", file=sys.stderr)
        return 1
    print(f"[3/5] guide store (FHIR IG) -> {summary['db_path']}")
    print(f"      profiles={summary['profiles']}  logical_models={summary['logical_models']}  "
          f"campos={summary['campos_rows']}  modelos={summary['modelos_rows']}  "
          f"terminologias={summary['terminologias_rows']}  (from {summary['resources_read']} resources)")
    return 0


def cmd_agent(args) -> int:
    from src.agent.agent import run_to_file

    if not config.SOURCE_METADATA_PATH.exists():
        print("source_metadata.json missing; run `extract` first.", file=sys.stderr)
        return 1
    if not config.GUIDE_DB_PATH.exists():
        print("guide store missing; run `load-guide` first.", file=sys.stderr)
        return 1
    path = run_to_file(verbose=not args.quiet)
    print(f"[4/5] structure map -> {path}")
    return 0


def cmd_review(_args) -> int:
    from src.review.render import render_to_file

    if not config.STRUCTURE_MAP_PATH.exists():
        print("structure_map.json missing; run `agent` first.", file=sys.stderr)
        return 1
    path = render_to_file()
    print(f"[5/5] review view -> {path}")
    return 0


def cmd_suggest(args) -> int:
    """Concept-mapping suggestions for one concept via the Termbox MCP server."""
    import json

    from src.terminology import TermboxError, suggest_concepts

    if args.json:
        concept = json.loads(args.json)
    elif args.text and not (args.code or args.system):
        concept = args.text  # free text, HL7 CE ("HGB^Hemoglobin^L") or "system|code"
    else:
        concept = {"code": args.code, "system": args.system, "display": args.text,
                   "synonyms": args.synonym or [], "context": args.context}
    try:
        res = suggest_concepts(concept, target_systems=args.target or None, max_suggestions=args.max)
    except TermboxError as exc:
        print(f"termbox unavailable: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(res.to_dict(), indent=2, ensure_ascii=False))
    return 1 if res.analysis.get("degraded") and not res.suggestions else 0


def cmd_all(args) -> int:
    for step in (cmd_build_db, cmd_extract, cmd_load_guide, cmd_agent, cmd_review):
        rc = step(args)
        if rc != 0:
            return rc
    print("\nDone. Open the review view in a browser:")
    print(f"  {config.REVIEW_HTML_PATH}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Structure Map Agent pipeline")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, fn, help_ in [
        ("build-db", cmd_build_db, "Build the synthetic source database"),
        ("extract", cmd_extract, "Extract source_metadata.json"),
        ("load-guide", cmd_load_guide, "Load guide CSVs into the guide store"),
        ("load-ig", cmd_load_ig, "Load a FHIR IG package (.tgz/folder) into the guide store"),
        ("agent", cmd_agent, "Run the agent -> structure_map.json"),
        ("review", cmd_review, "Render the HTML review view"),
        ("all", cmd_all, "Run every step in order"),
    ]:
        p = sub.add_parser(name, help=help_)
        p.add_argument("--quiet", action="store_true", help="suppress agent tool-call trace")
        if name == "load-ig":
            p.add_argument("path", nargs="?", help="path to the FHIR IG .tgz or folder")
        p.set_defaults(func=fn)

    p = sub.add_parser("suggest", help="Suggest up to 3 standard concepts for one source concept (Termbox)")
    p.add_argument("text", nargs="?", help="label / free text, HL7 CE 'code^text^system', or 'system|code'")
    p.add_argument("--code", help="source code (local or standard)")
    p.add_argument("--system", help="source system: URI, OID, HL7 v2 id (LN, SCT, I10, L) or name")
    p.add_argument("--synonym", action="append", help="alternative label (repeatable)")
    p.add_argument("--context", help="disambiguation hints: specimen, units, table/column, domain")
    p.add_argument("--target", action="append", help="target system (repeatable), e.g. LOINC, SNOMED")
    p.add_argument("--max", type=int, default=3, help="max suggestions (default 3)")
    p.add_argument("--json", help="full concept as JSON (Coding, CodeableConcept or row dict)")
    p.set_defaults(func=cmd_suggest)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
