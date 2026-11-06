# Structure Map Agent — Workbench UI

The front end described in [`docs/context_ui.md`](../docs/context_ui.md): the
workbench where an integrator loads a source database, runs the mapping agent,
and **reviews / corrects / accepts / exports** each proposed mapping.

Self-contained, no build step, no framework. Follows the spec's own design
direction (warm/cool "two worlds, one seam", IBM Plex Sans/Mono, the 3-segment
confidence meter, contour-only attention colors).

## Open it

Just open `ui/index.html` in a browser — it runs from `file://` with inlined
sample data (the "disparate providers" source mapped to Bupa Chile Core). For a
clean path with no `file://` quirks you can also serve it:

```bash
cd ui && python -m http.server 8000
# then open http://localhost:8000
```

> Google Fonts loads IBM Plex over the network; offline it falls back to system
> fonts, everything else still works.

## What's implemented (spec §7 "prototype first")

- **Shell** — nav rail (Sources / Mappings / Guide / Settings, collapsible),
  breadcrumbs with an **entity switcher**, tabs (Source / Mapping / Gaps).
- **Mapping workbench** — full-width reconciliation table split by the seam:
  Source (warm) · confidence meter + transform · FHIR target (cool) · state ·
  actions. Selecting a row opens the **detail panel** (citation, source column
  profile, target `min/max` + binding, agent reasoning).
- **Confidence meter**, **citation chip**, **binding chip**, **gap banner** as
  reusable pieces.
- **Gaps tab** — unfilled required (one burgundy banner), unmapped source,
  ambiguities.
- **Terminology tab** — maps the source's coded values (the detected lookups)
  to standard concepts. Per value, **Suggest** calls the Termbox terminology
  server (`/api/suggest`, no API key) and lists up to 3 verified concepts
  (system · code · display · score); **accept** one and it's recorded. Pick the
  target system (SNOMED / LOINC / RxNorm / ICD-10-CM / CPT) at the top, and
  **Export concept map** downloads `concept_map.json`. Needs the running server.
- **Copilot panel** — cited-answer stub (toggle in the header). Ask "why this
  element?", "what's required?", "show bindings".
- **Review flow** — accept / edit (with note) / reject per row, persisted in
  `localStorage`; a header counter; **Export** downloads `structure_map.json`
  with the review decisions folded in. Export is gated until gaps are resolved
  or explicitly overridden.
- Responsive (table stacks into source→target pairs), visible focus,
  reduced-motion respected.

## Loading real data

In the **Source** tab, drop a real `source_metadata.json` (updates the
detection summary) or a `structure_map.json` (loads the mapping workbench). The
file shapes are the two contracts from [`docs/context.md`](../docs/context.md).
