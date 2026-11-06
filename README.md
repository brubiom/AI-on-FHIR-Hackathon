# Structure Map Agent — Clinical Interoperability Copilot

Given the **metadata of a source SQL database** and a **FHIR implementation
guide** (CL Core — Guía de Implementación cl core, `hl7.fhir.cl.clcore` 1.9.3, FHIR R4), the agent proposes a
**structure map**: for each source column, which FHIR element it should map to,
with what type, terminology and transformation — **citing the exact source for
every decision**.

See [`docs/context.md`](docs/context.md) for the full design. This repository is
the implementation of its section 8 build order.

## Responsibilities (spec §1)

| The application does (deterministic) | The agent does (judgment) |
| --- | --- |
| Connect to the DB, read the catalog | Understand what each source column means |
| Extract tables, columns, types, nullability | Match each column to a FHIR element |
| Extract declared PK / FK | Turn FKs into FHIR references |
| Extract lookup contents + value samples | Align lookup values to FHIR value sets |
| Produce `source_metadata.json` | Produce `structure_map.json` |

## Architecture

```
synthetic source.db ──► extractor ──► source_metadata.json ─┐
                                                            ├──► Agent ──► structure_map.json ──► review.html
guide CSVs ──► guide.db ──► Phase B tools ──────────────────┘
```

- **`src/source_db/`** — step 1: builds a synthetic SQLite source (appointment,
  patient, practitioner, location + status/type lookups) with declared PK/FK.
- **`src/extractor/`** — step 2: reads the catalog → `source_metadata.json`
  (types, nullability, FKs, value samples, lookup contents).
- **`src/guide/`** — step 3: loads the three guide CSVs into SQLite and exposes
  the five Phase B tools (`consultar_campo`, `ver_modelo_logico`,
  `buscar_binding`, `buscar_en_guia`, `listar_perfiles_por_dominio`).
- **`src/agent/`** — step 4: the Claude tool-use loop (Phase A→B→C) →
  `structure_map.json`.
- **`src/review/`** — step 5: renders the five output blocks to `review.html`.
- **`src/terminology/`** — concept-mapping suggestions backed by the Termbox MCP
  terminology server: one source concept in, up to 3 verified standard concepts
  out. Standalone building block; see [Terminology integration](#terminology-integration).
- **`src/source_db/docker-compose.yaml`** — a richer provider database (Postgres,
  `localhost:5440/provider`) simulating a hospital's ADT/LIS/RIS/PACS/interface
  engine, plus spreadsheet code lists in `src/source_db/vocab/*.csv`. This is the
  "disparate data" the terminology integration is designed for.

## Run with Docker (recommended)

One command brings up the provider database **and** the app (UI + agent) on a
stable Python 3.12 — so you don't need a stable Python on the host:

```bash
docker compose up --build                            # → http://localhost:8000
ANTHROPIC_API_KEY=sk-... docker compose up --build   # agent enabled
# one-off steps in the app container:
docker compose run --rm app python main.py load-ig guide/clcore.tgz
docker compose run --rm app python main.py agent
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full flow.

## Setup (without Docker)

> **Python version:** use a **stable Python 3.11–3.13** for the agent step. The
> Anthropic SDK depends on `jiter`, whose compiled wheel does not load on
> Python 3.15 alpha (`ImportError: DLL load failed while importing jiter`).
> Steps 1–3 and 5 run on any Python 3.10+; only step 4 needs the SDK. Docker
> (above) avoids this entirely.

```bash
py -3.12 -m venv .venv && .venv\Scripts\activate   # Windows (PowerShell)
pip install -r requirements.txt
copy .env.example .env    # then set ANTHROPIC_API_KEY (or use `ant auth login`)
```

Put the three Implementation Guide CSVs in [`guide/`](guide/) — see
[`guide/README.md`](guide/README.md) for the expected names and the
schema-tolerant column resolution.

## Run

```bash
python main.py build-db      # 1. synthetic source DB (fixture)
python main.py extract       # 2. source_metadata.json
python main.py load-guide    # 3. guide store (needs the CSVs)
python main.py agent         # 4. structure_map.json  (needs ANTHROPIC_API_KEY)
python main.py review        # 5. review.html
# or everything at once:
python main.py all
```

All artifacts are written to [`data/`](data/). Open `data/review.html` in a
browser to accept/correct each proposed mapping.

## Terminology integration

> **For agents:** use this module for *every* code you need to map or validate.
> Never write a SNOMED CT / LOINC / RxNorm / ICD-10 code from memory. Every code
> it returns has come back from the Termbox server.

`src/terminology/` suggests **up to 3 standard concepts for one source concept**.
It calls Health Samurai's hosted **Termbox MCP server**
(`https://tx.health-samurai.io/mcp/sse`, no auth) over MCP JSON-RPC/HTTP using
only the standard library, so no extra dependency is needed. Content includes SNOMED CT
International, LOINC 2.82, RxNorm, ICD-10-CM, CPT, UCUM, HL7 v2/v3/FHIR code
systems (THO) and CDC Race & Ethnicity.

### Why it exists: the source data is disparate

The provider DB and the `vocab/*.csv` sheets mix every flavour of coding, and
the module handles each one:

| Source shape (real examples in `src/source_db`) | What the module does |
| --- | --- |
| **Local made-up codes** with a label: `HGB` "Hemoglobin", `ALG00118` "Shellfish", `CC001` "Chest pain", `XRCH2V` "XR CHEST PA AND LATERAL" | Treats the code as local, then searches the label (+ synonyms, + context) in the target systems and re-ranks the hits |
| **Standard codes with a URI**: `lis.dictionary_map.target_system`, `adt.code_value_alias.alias_system` | `lookup-code` to verify; flags inactive codes; flags a **wrong legacy map** when the code's display contradicts the source label |
| **Standard codes in a column, no system**: `snomed_code`, `rxnorm_code`, `loinc_code`, `icd10_code`, `cpt_code` | Infers the system from the code's shape (LOINC and SNOMED check digits, ICD-10, CPT, RxNorm patterns) and verifies by lookup |
| **System given as a name, HL7 v2 id or OID**: `LN`, `SCT`, `I10`, `HL70001`, `L`/`99xxx` (local), `urn:oid:2.16.840.1.113883.6.1`, headers like `LOINC (current)` | Resolved to the canonical FHIR URI (`systems.resolve_system`) |
| **Non-canonical spellings**: ICD-10-CM without dot `Z3482` | Tries variants (`Z34.82`) and reports `code-variant` |
| **Abbreviated labels**: `CBC W/O DIFF`, `Hgb`, specimen `EDTA`/`SST`, units `g/dL` | Expands abbreviations; lab phrases map to LOINC wording ("White blood cell" → "Leukocytes"); a unit pushes the matching LOINC property up (`g/dL` → `[Mass/volume]`) |
| **Free text** and **HL7 v2 CE strings** (`BCX^Blood culture^L` in `integration.hl7_message`) | Parsed into code/text/system automatically |
| **Unmapped rows** (`snomed_code` NULL, "Not mapped yet") | Text search gives candidates for a human to confirm |

### Python API (call from any backend feature)

```python
from src.terminology import suggest_concepts, suggest_many

res = suggest_concepts(
    {"code": "HGB", "system": "L", "display": "Hemoglobin",
     "context": "lab test, specimen EDTA, units g/dL"},
    target_systems=["LOINC"],          # URIs or names; None = see below
    max_suggestions=3,
)
res.suggestions[0].coding   # {'system': 'http://loinc.org', 'code': '718-7', 'display': 'Hemoglobin [Mass/volume] in Blood'}
res.to_dict()               # JSON-ready (input, analysis, suggestions, warnings)

suggest_many([row1, row2, ...], target_systems=["SNOMED"])  # batch: shared client + cache, parallel
```

**Input** (`concept`): any of these shapes. `coerce_input()` normalises them into a `ConceptInput`.
- free text: `"chest pain"`
- HL7 v2 CE/CWE: `"718-7^Hemoglobin^LN"`
- token: `"http://loinc.org|718-7"`
- FHIR `Coding` or `CodeableConcept` dict
- a row dict with keys `code`, `system`, `display`/`text`/`name`/`label`/`description`, `synonyms` (list or `a|b|c`), `context` (str or dict)
- `ConceptInput(code, system, display, synonyms, context)`

`context` is optional but strongly recommended. Pass anything that disambiguates:
specimen, units, table/column name, domain (`"allergen"`, `"microorganism"`, `"lab test"`).

**`target_systems`**: which code systems to suggest in. If `None`, it uses the
source's own standard system when there is one (so the code gets verified and
alternatives come from that system), otherwise `TERMBOX_DEFAULT_TARGETS`
(SNOMED CT + LOINC). Pass a target to **translate across systems**. For example,
an ICD-10-CM code with `target_systems=["SNOMED"]`: the verified ICD display is
used as search text in SNOMED CT.

**Output** (`SuggestionResult.to_dict()`):

```jsonc
{
  "input": {...normalised ConceptInput...},
  "analysis": {
    "source_system": "http://loinc.org" | "L" | null,
    "source_system_kind": "standard" | "local" | "unknown",
    "inferred_systems": [...],        // when no system was given
    "code_status": "verified" | "not-found" | "local" | "no-code" | "unverified",
    "target_systems": [...],
    "queries": [...],                 // text queries sent to Termbox (debugging)
    "degraded": false                 // true if any Termbox call failed
  },
  "suggestions": [                    // 0..3, best first
    {"system", "system_name", "code", "display",
     "score": 0.0-1.0,
     "match_type": "exact-code" | "code-variant" | "inferred-system" | "text-search",
     "inactive": false, "matched_query": "...", "notes": [...],
     "coding": {"system", "code", "display"}}   // ready for FHIR
  ],
  "warnings": [...]                   // e.g. wrong legacy mapping, inactive code, weak matches
}
```

**Reading scores.** They are heuristic match strengths, not probabilities.
`1.0` means the code was verified and its display agrees with the source label.
`0.8–0.95` is a strong text match. `0.5–0.8` is plausible and needs confirmation.
Below `0.5`, a warning is added. Text matches are capped at 0.95, so only a verified
code reaches 1.0. A code that exists but contradicts the source label drops to
~0.4–0.6 with a warning, so better text alternatives rank above it.

### As an agent tool (Anthropic tool-use)

`src/terminology/tools.py` follows the same pattern as `src/guide/tools.py`:

```python
from src.guide.tools import TOOL_DEFINITIONS as GUIDE_TOOLS, GuideTools
from src.terminology.tools import TERMINOLOGY_TOOL_DEFINITIONS, TerminologyTools

tools = GUIDE_TOOLS + TERMINOLOGY_TOOL_DEFINITIONS          # pass to messages.create(tools=...)
term = TerminologyTools()
result_json = term.dispatch(block.name, block.input) if term.handles(block.name) \
    else guide_tools.dispatch(block.name, block.input)
```

The tool is named `sugerir_conceptos` and has a strict schema:
`{concept: {code, system, display, synonyms, context}, target_systems, max_suggestions}`.
It is **not wired into the structure-map agent yet**, so add it to `src/agent/agent.py`
when the mapping workflow needs terminology. Cite results as
`{"source": "termbox", "ref": "<system>|<code>"}`.

### CLI (manual testing)

```bash
python main.py suggest "chest pain" --target SNOMED
python main.py suggest --code HGB --system L "Hemoglobin" --context "specimen EDTA, units g/dL" --target LOINC
python main.py suggest --code Z3482                      # bare code: system inferred, Z34.82 verified
python main.py suggest "BCX^Blood culture^L" --target LOINC
python main.py suggest --json '{"code":"ALG00118","display":"Shellfish","synonyms":"shrimp|crab|lobster"}' --target SNOMED
```

### Internals and tuning knobs

| File | Role |
| --- | --- |
| `client.py` | `TermboxClient`: MCP JSON-RPC over HTTP (JSON or SSE replies), in-memory cache, thread-safe. Wraps `find-code`, `lookup-code`, `expand-valueset`, `search-rxnorm`. A "not found" `OperationOutcome` comes back as `None` or `[]`; transport errors raise `TermboxError`. |
| `systems.py` | Pure functions: alias/OID/HL7-id → URI, code-shape inference with check digits, code spelling variants. Add new aliases to `_ALIASES`. |
| `matching.py` | Abbreviations (`ABBREVIATIONS`), lab phrase rewrites (`PHRASE_SYNONYMS`), unit → LOINC property, query building and scoring. **The main place to tune when a new source brings new shorthand.** |
| `suggest.py` | Orchestration: exact path → text path → merge/rank. |
| `tools.py` | Anthropic tool definition + dispatcher. |

Env vars (see `.env.example`): `TERMBOX_MCP_URL`, `TERMBOX_TIMEOUT`,
`TERMBOX_DEFAULT_TARGETS`, `TERMBOX_MAX_QUERIES` (default 5), `TERMBOX_MIN_SCORE` (0.35).

**Measured against the provider DB's own mappings.** The test hides the existing
standard code and suggests from the local code + label + context only:
- organisms → SNOMED CT: 88% top-1, 100% top-3
- allergens → RxNorm/SNOMED CT: 83% top-3
- lab worksheet → LOINC: 50% top-1, 72% top-3

The lab misses are close siblings (the same analyte by a different method or
specimen variant), which is why the output is a ranked suggestion list and not
an automatic decision.

### Limits (from Termbox; design around them)

- `find-code` matches words in the display text. It has no synonyms, no relevance
  ranking and a fixed top 20 with no paging. That is why the module runs several
  query variants and re-ranks locally. A very generic label ("Sodium") without
  context can miss the best code, so pass `context`.
- Results come from Termbox's latest loaded versions (e.g. SNOMED CT Intl
  2026-10-01, LOINC 2.82, RxNorm 05042026). There is no `$translate` over MCP, so
  cross-system mapping goes through display text.
- A few HL7 systems return non-English displays (e.g. `v2-0001` `F` → "weiblich").
- Latency is ~0.4–1.5 s per concept (parallel calls). Use `suggest_many` for batches;
  identical calls are cached for the client's lifetime.
- Suggestions are for review: the terminology server gives no clinical/coding advice.

## Notes

- Default model is `claude-opus-5-5` (override with `ANTHROPIC_MODEL`).
- Steps 1–3 and 5 run offline; only step 4 calls the API. The terminology
  integration (`python main.py suggest`) needs network access to Termbox.
- The output is a readable JSON map, **not** a formal FHIR `StructureMap`
  resource — that compile step is open item 1 in the spec.
- Synthetic data only; the agent never invents codes or columns to fill a gap.
