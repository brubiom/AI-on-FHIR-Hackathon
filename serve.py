"""Run the platform locally for testing.

One command stands the whole thing up:
  1. ensures the Postgres `provider` database is running (docker compose),
  2. extracts real metadata from it -> ui/data/source_metadata.json,
  3. serves the UI at http://localhost:8000 and opens it.

The Mapping tab loads ui/data/structure_map.json if present; otherwise it uses
the bundled sample map (the agent needs the guide CSVs + ANTHROPIC_API_KEY to
produce a real one). The Source tab always shows the real detected provider
metadata when the database is reachable.

Usage:  python serve.py [--port 8000] [--no-open] [--no-db]
"""

from __future__ import annotations

import argparse
import functools
import json
import os
import subprocess
import sys
import threading
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
UI_DIR = ROOT / "ui"
UI_DATA = UI_DIR / "data"
SOURCE_DB_DIR = ROOT / "src" / "source_db"


def ensure_db() -> bool:
    """Bring the provider container up (idempotent). Returns True on success."""
    env = dict(os.environ, MSYS_NO_PATHCONV="1", MSYS2_ARG_CONV_EXCL="*")
    try:
        r = subprocess.run(
            ["docker", "compose", "up", "-d", "--wait", "postgres"],
            cwd=SOURCE_DB_DIR, env=env, capture_output=True, text=True, timeout=240,
        )
    except FileNotFoundError:
        print("  ! docker not found — skipping DB; UI will use sample data.")
        return False
    except subprocess.TimeoutExpired:
        print("  ! docker compose timed out — skipping DB.")
        return False
    if r.returncode != 0:
        print("  ! could not start the database — skipping DB; UI will use sample data.")
        print("   ", (r.stderr or r.stdout).strip().splitlines()[-1:] or "")
        return False
    return True


def extract_metadata() -> bool:
    """Extract live provider metadata into ui/data/source_metadata.json."""
    try:
        from src.extractor.pg_metadata_extractor import extract_to_file
    except Exception as exc:
        print(f"  ! extractor import failed: {exc}")
        return False
    UI_DATA.mkdir(parents=True, exist_ok=True)
    try:
        path = extract_to_file(UI_DATA / "source_metadata.json")
        # keep a copy in data/ for the rest of the pipeline too
        (ROOT / "data").mkdir(exist_ok=True)
        (ROOT / "data" / "source_metadata.json").write_text(
            path.read_text(encoding="utf-8"), encoding="utf-8"
        )
        data = json.loads(path.read_text(encoding="utf-8"))
        print(f"  extracted {len(data['tables'])} tables, "
              f"{len(data['lookups'])} lookups, "
              f"{len(data['candidate_entities'])} candidate entities")
        return True
    except Exception as exc:
        print(f"  ! metadata extraction failed: {exc}")
        return False


def surface_structure_map() -> bool:
    """If the agent has produced data/structure_map.json, expose it to the UI."""
    src = ROOT / "data" / "structure_map.json"
    if not src.exists():
        return False
    UI_DATA.mkdir(parents=True, exist_ok=True)
    (UI_DATA / "structure_map.json").write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return True


def guide_status() -> dict:
    status_file = ROOT / "data" / "guide_status.json"
    if status_file.exists() and (ROOT / "data" / "guide.db").exists():
        try:
            s = json.loads(status_file.read_text(encoding="utf-8"))
            s["loaded"] = True
            return s
        except Exception:
            pass
    return {"loaded": False}


class Handler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, *args):  # quiet
        pass

    # --- tiny JSON API ---------------------------------------------------
    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.split("?")[0] == "/api/guide-status":
            return self._json(guide_status())
        return super().do_GET()

    def do_POST(self):
        route = self.path.split("?")[0]
        if route == "/api/load-ig":
            return self._load_ig()
        if route == "/api/generate":
            return self._generate()
        if route == "/api/suggest":
            return self._suggest()
        self._json({"ok": False, "error": "not found"}, 404)

    def _suggest(self):
        length = int(self.headers.get("Content-Length", "0"))
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
            from src.terminology.suggest import suggest_concepts
            res = suggest_concepts(
                data.get("concept") or {},
                target_systems=data.get("target_systems"),
                max_suggestions=min(int(data.get("max_suggestions") or 3), 3),
            )
            self._json(res.to_dict())
        except Exception as exc:
            self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)

    def _load_ig(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return self._json({"ok": False, "error": "empty upload"}, 400)
        data = self.rfile.read(length)
        (ROOT / "data").mkdir(exist_ok=True)
        tmp = ROOT / "data" / "_upload_ig.tgz"
        tmp.write_bytes(data)
        try:
            from src.guide.load_ig import load_ig
            summary = load_ig(tmp)
            (ROOT / "data" / "guide_status.json").write_text(
                json.dumps(summary, ensure_ascii=False), encoding="utf-8")
            summary["ok"] = True
            self._json(summary)
        except Exception as exc:
            self._json({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, 400)
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass

    def _generate(self):
        if not (ROOT / "data" / "guide.db").exists():
            return self._json({"ok": False, "error": "load an Implementation Guide first"}, 400)
        if not (ROOT / "data" / "source_metadata.json").exists():
            return self._json({"ok": False, "error": "no source_metadata.json — start the DB and extract first"}, 400)
        if not os.getenv("ANTHROPIC_API_KEY"):
            return self._json({"ok": False, "error": "set ANTHROPIC_API_KEY to run the agent"}, 400)
        length = int(self.headers.get("Content-Length", "0"))
        entities = None
        if length:
            try:
                entities = (json.loads(self.rfile.read(length)) or {}).get("entities") or None
            except Exception:
                entities = None
        try:
            from src.agent.agent import run_to_file
            run_to_file(source_metadata=ROOT / "data" / "source_metadata.json",
                        entities=entities, verbose=False)
            surface_structure_map()
            self._json({"ok": True})
        except Exception as exc:
            self._json({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, 500)


def serve(port: int, open_browser: bool) -> None:
    handler = functools.partial(Handler, directory=str(UI_DIR))
    # In a container, bind 0.0.0.0 so Docker's published port can reach us;
    # on the host, keep it on loopback.
    bind = "0.0.0.0" if os.getenv("APP_IN_DOCKER") else "127.0.0.1"
    httpd = ThreadingHTTPServer((bind, port), handler)
    url = f"http://localhost:{port}"
    print(f"\n  platform ready → {url}")
    print("  (Ctrl+C to stop; the database keeps running — `cd src/source_db && docker compose down` to stop it)\n")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopped.")
        httpd.server_close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Run the Structure Map Agent platform")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--no-open", action="store_true", help="do not open a browser")
    ap.add_argument("--no-db", action="store_true", help="skip DB start + extraction (sample data only)")
    args = ap.parse_args(argv)

    print("Starting the Structure Map Agent platform…")
    in_docker = bool(os.getenv("APP_IN_DOCKER") or os.getenv("PGHOST"))
    if args.no_db:
        print("[1-2/3] --no-db: using bundled sample data.")
    elif in_docker:
        # compose guarantees the db service is healthy before we start
        print("[1/3] database managed by docker compose (depends_on healthy).")
        print("[2/3] extracting live source metadata…")
        extract_metadata()
    else:
        print("[1/3] ensuring the provider database is up…")
        if ensure_db():
            print("[2/3] extracting live source metadata…")
            extract_metadata()
        else:
            print("[2/3] skipped extraction.")
    if surface_structure_map():
        print("      found data/structure_map.json → serving the real map.")
    else:
        print("      no structure_map.json yet → Mapping tab shows the sample "
              "(load a guide + run `python main.py agent`).")
    print("[3/3] serving the UI…")
    serve(args.port, not args.no_open)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
