# Implementation guide store — input files

The guide store is what the agent validates against. You can load it from a
**FHIR IG package** (recommended — the real guide) or from **three CSVs**.

## Option A — FHIR IG package (recommended)

Drop the **Bupa Chile Core** package (`fhir.bupa.core` 4.6.0, FHIR R4) here as
a `.tgz` (npm tarball) or an unpacked folder of FHIR JSON resources, then:

```bash
python main.py load-ig guide/fhir.bupa.core-4.6.0.tgz
# or a folder:
python main.py load-ig guide/fhir.bupa.core
```

The loader ([`src/guide/load_ig.py`](../src/guide/load_ig.py)) reads the IG
resources and builds the same `guide.db` the tools query:

| FHIR resource | becomes | backs the tool |
| --- | --- | --- |
| `StructureDefinition` (profile) | `campos` (one row per element: min/max, type, binding, fixed, mustSupport, targetProfile) | `consultar_campo`, `listar_perfiles_por_dominio` |
| `StructureDefinition` (`kind: logical`) | `modelos` | `ver_modelo_logico` |
| `ValueSet` / `CodeSystem` | `terminologias` (codes + display; a ValueSet that includes a whole CodeSystem is expanded) | `buscar_binding` |

Accepts a `.tgz`, a folder, a `package/` folder, and resources wrapped in a
`Bundle`. Column names are canonical, so no `columns.json` mapping is needed.

## Option B — three CSVs

Alternatively, drop the guide as three CSVs and run `python main.py load-guide`:

Expected file names (configurable in [`config.py`](../config.py) → `GUIDE_CSVS`):

| File | Loaded as table | Backs the tool |
| --- | --- | --- |
| `campos_perfiles.csv` | `campos` | `consultar_campo`, `listar_perfiles_por_dominio` |
| `modelos_logicos.csv` | `modelos` | `ver_modelo_logico` |
| `terminologias.csv` | `terminologias` | `buscar_binding` |

## Column resolution

The loader does **not** require specific headers. It loads each CSV verbatim,
then resolves the *logical* columns the tools need against whatever headers your
CSVs actually have (case- and accent-insensitive, Spanish or English). After
loading, it prints a warning for any logical column it could not resolve.

Logical columns the tools expect:

- **campos** → `profile`, `element`, `min`, `max`, `type`, `binding`,
  `binding_strength`, `fixed`, `must_support`, `logical_model`
- **modelos** → `logical_model`, `field`, `definition`, `type`
- **terminologias** → `valueset`, `code`, `display`, `system`

## Overriding the mapping

If a column does not resolve automatically (or resolves wrongly), copy
[`columns.example.json`](columns.example.json) to `columns.json` and set the
real header name for each logical column you need. Overrides win over
auto-resolution.

## Narrative pages (optional)

Put any `.md`/`.txt` narrative pages of the guide under `guide/narrative/`.
They are indexed by section heading and made searchable through `buscar_en_guia`.
Without them, `buscar_en_guia` falls back to searching element names and
definitions in the CSVs.

> These files are listed in `.gitignore` — they are treated as confidential and
> are not committed.
