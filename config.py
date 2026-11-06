"""Central configuration for the Structure Map Agent.

Paths and knobs live here so every module (extractor, guide store, agent,
review) agrees on where artifacts are read from and written to.
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # python-dotenv is optional at runtime
    pass

# --- Root paths -------------------------------------------------------------
ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
GUIDE_DIR = ROOT / "guide"

DATA_DIR.mkdir(exist_ok=True)

# --- Step 1: synthetic source database -------------------------------------
SOURCE_DB_PATH = DATA_DIR / "source.db"

# --- Step 2: metadata extractor output --------------------------------------
SOURCE_METADATA_PATH = DATA_DIR / "source_metadata.json"

# How many distinct value samples to pull per column (section 3 of the spec).
VALUE_SAMPLE_LIMIT = 5
# A table with at most this many rows and a code+label shape is treated as a lookup
# (spec section 9.4). FK targets are also treated as lookups regardless of this.
LOOKUP_MAX_ROWS = 50

# --- Step 3: guide store ----------------------------------------------------
GUIDE_DB_PATH = DATA_DIR / "guide.db"
# The three CSVs the implementation guide is shipped as. Drop the real files
# from the Bupa Chile Core Implementation Guide into guide/ with these names,
# or point these at wherever they live.
GUIDE_CSVS = {
    "campos": GUIDE_DIR / "campos_perfiles.csv",
    "modelos": GUIDE_DIR / "modelos_logicos.csv",
    "terminologias": GUIDE_DIR / "terminologias.csv",
}
# Optional narrative pages for buscar_en_guia (markdown/plain text). Any .md/.txt
# file in this folder is indexed as searchable narrative.
GUIDE_NARRATIVE_DIR = GUIDE_DIR / "narrative"
# Optional override of logical->CSV column mapping. See guide/README.md.
GUIDE_COLUMNS_CONFIG = GUIDE_DIR / "columns.json"

# --- Step 4: agent ----------------------------------------------------------
# Default model per the Anthropic API guidance. Override with ANTHROPIC_MODEL.
AGENT_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-5-5")
AGENT_MAX_TOKENS = int(os.getenv("AGENT_MAX_TOKENS", "16000"))
# Safety cap on the tool-use loop so a misbehaving run cannot spin forever.
AGENT_MAX_TURNS = int(os.getenv("AGENT_MAX_TURNS", "40"))

STRUCTURE_MAP_PATH = DATA_DIR / "structure_map.json"

# --- Terminology: Termbox MCP (concept-mapping suggestions) -------------------
# Hosted Termbox MCP server (Streamable HTTP + SSE on the same URL, no auth).
TERMBOX_MCP_URL = os.getenv("TERMBOX_MCP_URL", "https://tx.health-samurai.io/mcp/sse")
TERMBOX_TIMEOUT = float(os.getenv("TERMBOX_TIMEOUT", "30"))
# Systems searched when the caller gives no target and the source has no standard system.
TERMBOX_DEFAULT_TARGETS = [
    s.strip() for s in os.getenv(
        "TERMBOX_DEFAULT_TARGETS", "http://snomed.info/sct,http://loinc.org").split(",") if s.strip()
]
# Max text-query variants per input (each is sent to every target system).
TERMBOX_MAX_QUERIES = int(os.getenv("TERMBOX_MAX_QUERIES", "5"))
# Suggestions scoring below this are dropped (unless nothing better exists).
TERMBOX_MIN_SCORE = float(os.getenv("TERMBOX_MIN_SCORE", "0.35"))

# --- Step 5: review view ----------------------------------------------------
REVIEW_HTML_PATH = DATA_DIR / "review.html"

# --- Step 6: load pipeline (source DB -> FHIR -> Aidbox) ---------------------
# The pipeline is CONFIG-DRIVEN and source-agnostic: it executes whatever
# structure_map.json (column -> FHIR element) and conceptmap.json (source code
# -> standard code) the reviewer approved. Swap the source and re-run the agent;
# the same pipeline migrates the new data with no code change.
# STRUCTURE_MAP_PATH is defined in the agent section above; the load step reuses it.
CONCEPTMAP_PATH = DATA_DIR / "conceptmap.json"

# Fallback demo configs (used only when data/ has no approved config yet) so the
# "Create pipeline" button works end-to-end against the seeded provider DB.
PIPELINE_SAMPLE_DIR = ROOT / "src" / "pipeline" / "sample"

# Aidbox FHIR endpoint. Every value is overridable at run time (the UI sends the
# endpoint it has in its field), so nothing here is baked into the engine.
AIDBOX_BASE_URL = os.getenv("AIDBOX_BASE_URL", "https://dakwjlidfo.aidbox.app").rstrip("/")
# Basic auth with an Aidbox Client (client_id:secret). Create a Client resource
# with an AccessPolicy in Aidbox, then put its id/secret here or in the UI.
AIDBOX_CLIENT_ID = os.getenv("AIDBOX_CLIENT_ID", "")
AIDBOX_CLIENT_SECRET = os.getenv("AIDBOX_CLIENT_SECRET", "")
# Alternatives (optional): a pre-issued bearer token, or raw "user:pass" Basic.
AIDBOX_TOKEN = os.getenv("AIDBOX_TOKEN", "")
AIDBOX_BASIC = os.getenv("AIDBOX_BASIC", "")
# Resources per transaction Bundle POST (keeps payloads bounded).
AIDBOX_BUNDLE_SIZE = int(os.getenv("AIDBOX_BUNDLE_SIZE", "100"))
AIDBOX_TIMEOUT = float(os.getenv("AIDBOX_TIMEOUT", "60"))
# Safety cap on rows read per entity (None/0 = all). The UI can lower it.
PIPELINE_ROW_LIMIT = int(os.getenv("PIPELINE_ROW_LIMIT", "0")) or None
