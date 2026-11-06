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

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
