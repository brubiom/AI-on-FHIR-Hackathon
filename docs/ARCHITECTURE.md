# Architecture — Clinical Interoperability Copilot

**Audience:** engineers working on this repo.
**What it is:** how the Structure Map Agent works end to end — the data flow,
the components, the two JSON contracts, and the entry points.

The core idea ([context.md](context.md) §1): the **application does the
deterministic work** (read the database, extract facts) and the **agent does the
judgment** (propose the FHIR mapping, fully cited). Two JSON contracts sit
between them so each half can evolve independently.

---

## End-to-end flow

```
┌───────────────────────────────────────────────────────────────────────────┐
│ 1. SOURCE (disparate data)                              src/source_db/      │
│    Postgres `provider` (ADT/LIS/PACS/RIS) + vocab/*.csv                      │
│    local made-up codes AND standard codes, mixed                            │
└───────────────┬─────────────────────────────────────────────────────────────┘
                │  deterministic — the "application"
                ▼
   2. EXTRACTOR            src/extractor/pg_metadata_extractor.py
      reads the catalog → types, nullability, PK/FK, lookup contents
                │
                ▼
        ┌────────────────────────┐
        │  source_metadata.json  │  ◄── CONTRACT 1 (context.md §3): facts only
        └──────────┬─────────────┘
                   │
   3. GUIDE (IG)   │                     4. TERMINOLOGY
   load_ig.py  /   │                     src/terminology/  (Termbox MCP)
   load_guide.py → guide.db              tool: sugerir_conceptos
   tools: consultar_campo,               verified SNOMED/LOINC/RxNorm/ICD-10…
          ver_modelo_logico,                      │
          buscar_binding,                         │
          buscar_en_guia,                         │
          listar_perfiles_por_dominio             │
                   │                              │
                   ▼                              ▼
        ┌──────────────────────────────────────────────────┐
        │ 5. AGENT          src/agent/   (the "judgment")    │
        │    Phase A — understand the source                 │
        │    Phase B — query the guide + terminology (tools) │
        │    Phase C — propose the map, cited                │
        └───────────────────────┬────────────────────────────┘
                                ▼
                  ┌────────────────────────┐
                  │   structure_map.json   │  ◄── CONTRACT 2 (context.md §4)
                  └──────────┬─────────────┘      mappings · references ·
                             │                     unfilled_required ·
                             │                     unmapped_source · ambiguities
                             ▼                     (each with citation + confidence)
   6. REVIEW / EXPORT       ui/   (workbench)
      the integrator reviews / corrects / accepts each mapping,
      sees the gaps, asks the Copilot, and exports structure_map.json
```

---

## Components

| # | Component | Path | Role |
| --- | --- | --- | --- |
| 1 | **Source database** | `src/source_db/` | A Postgres `provider` DB (docker-compose, `localhost:5440`) simulating a hospital's ADT/LIS/PACS/RIS + interface engine, plus spreadsheet code lists in `vocab/*.csv`. Deliberately messy: local codes and standard codes mixed. Seed with `reset-db.sh`. |
| 2 | **Metadata extractor** | `src/extractor/pg_metadata_extractor.py` | Deterministic. Reads the catalog via the container's `psql` (no driver needed) → `source_metadata.json`: tables, columns, SQL types, nullability, declared PK/FK, detected lookups + contents. Never interprets meaning. (`metadata_extractor.py` is the SQLite equivalent for the original synthetic fixture.) |
| 3 | **Guide store** | `src/guide/` | The Implementation Guide (`fhir.bupa.core`) loaded into `guide.db`. Load it from a **FHIR package** (`load_ig.py`: StructureDefinition → `campos`, logical models → `modelos`, ValueSet/CodeSystem → `terminologias`) or **CSVs** (`load_guide.py`). Queried by five Phase-B tools in `tools.py`. |
| 4 | **Terminology** | `src/terminology/` | Suggests up to 3 **verified** standard concepts (SNOMED CT, LOINC, RxNorm, ICD-10-CM…) for one source concept, via the Termbox MCP server. Exposed as the `sugerir_conceptos` agent tool. The agent never writes a standard code from memory. |
| 5 | **Agent** | `src/agent/` | The judgment. A manual Anthropic tool-use loop (`agent.py`) driven by the system prompt (`prompts.py`): Phase A (understand source) → B (query guide + terminology) → C (propose). Emits `structure_map.json`. Model `claude-opus-5-5`. |
| 6 | **Review UI** | `ui/` | The integrator's workbench (see [context_ui.md](context_ui.md)): the reconciliation table with the warm/cool "two worlds, one seam", a detail **pop-up** per row, confidence meter, citations, the Gaps tab, the Copilot, and Export. Reads the two contracts; writes review decisions into the exported map. |

---

## The two contracts (the interfaces)

- **`source_metadata.json`** (context.md §3) — what the application guarantees:
  engine, tables, columns (`sql_type`, `nullable`, `comment`, `samples`),
  `primary_key`, `foreign_keys`, and `lookups` (code + display). All **facts**;
  the agent treats them as ground truth and does not re-derive them.
- **`structure_map.json`** (context.md §4) — what the agent proposes, per entity:
  `mappings` (column → FHIR element, with transform / binding / confidence /
  citation), `references` (FHIR References from declared FKs), and the three gap
  blocks `unfilled_required`, `unmapped_source`, `ambiguities`.

Because the UI and the agent only ever speak these two contracts, you can run,
test or replace either half in isolation (e.g. drop a hand-written
`structure_map.json` into the UI with no agent).

---

## Entry points

| Entry | Command | What it does |
| --- | --- | --- |
| **`serve.py`** (the platform) | `python serve.py` | Ensures the DB is up, extracts live metadata, serves the UI at `http://localhost:8000`, and exposes `/api/load-ig` (upload the IG), `/api/guide-status`, `/api/generate` (run the agent). |
| **`main.py`** (the pipeline CLI) | `python main.py <cmd>` | `build-db · extract · load-guide · load-ig · agent · review · all · suggest`. |
| **`ui/index.html`** | open in a browser | The front end alone, with bundled sample data (no server). |

Each starts in its own `if __name__ == "__main__":` block; `serve.py` →
`ensure_db` → `extract_metadata` → `serve`, `main.py` → `argparse` dispatch.

---

## How to run it with a Claude API key (the real end-to-end)

The agent (step 5) is the only part that calls the Anthropic API.

> **Why Docker:** the Anthropic SDK depends on `jiter`, whose compiled wheel does
> not load on Python 3.15 alpha (`ImportError: DLL load failed while importing
> jiter`). The `app` container runs a **stable Python 3.12** where the SDK works,
> so you do not need a stable Python on the host. Steps 1–4 and the UI run on any
> host Python; only the agent needs the SDK.

### Recommended: Docker (`docker-compose.yml`)

```bash
# DB + UI (agent disabled until a key is present)
docker compose up --build                 # → http://localhost:8000

# with the key, the agent is enabled
ANTHROPIC_API_KEY=sk-... docker compose up --build

# one-off pipeline steps in the app container
docker compose run --rm app python main.py load-ig guide/fhir.bupa.core-4.6.0.tgz
docker compose run --rm app python main.py agent        # → data/structure_map.json
```

Two services: `db` (the provider Postgres, seeded on first boot) and `app`
(Python 3.12, serves the UI, extracts metadata, runs the agent). Or do it all
from the UI: `docker compose up`, upload the `.tgz` in the **Source** tab, then
**Generate structure map**.

### Alternative: local stable Python (no Docker)

Only if you install a stable Python 3.11–3.13 on the host (the host's 3.15 alpha
will not work for the agent):

```bash
py -3.12 -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
set ANTHROPIC_API_KEY=...                  # PowerShell: $env:ANTHROPIC_API_KEY="..."
python main.py extract                      # → data/source_metadata.json
python main.py load-ig guide/fhir.bupa.core-4.6.0.tgz
python main.py agent                        # → data/structure_map.json
python serve.py                             # http://localhost:8000
```

---

## Current state

Steps 1–4 run today, and the whole platform runs in Docker (Python 3.12, SDK
working). Step 5 (the agent) is wired and runnable; to produce a **real**
structure map it needs `ANTHROPIC_API_KEY` and the real guide loaded. Until then
the UI shows **real** source metadata in the Source tab and a **sample**
structure map in Mapping/Gaps.
