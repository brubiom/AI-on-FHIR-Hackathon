# Structure Map Agent — UI Specification

**Project:** Clinical Interoperability Copilot
**Surface:** the workbench where an integrator loads a source database, runs the mapping agent, and reviews/exports the structure map.
**Design approach:** a self-contained visual direction built for this brief (not tied to any house design system).
**Document version:** 2.0 · November 2026

---

## 1. What this UI is

The front end for the Structure Map Agent. It consumes the two contracts already defined — `source_metadata.json` (from the application) and `structure_map.json` (from the agent) — and lets a human **review, correct, and accept each proposed mapping** before it goes to a pipeline.

Users are **integrators**: a technical profile that thinks in FHIR elements and a business profile that thinks in processes. Three product principles drive every screen: every mapping is **cited**, confidence and binding are **always visible**, and every mapping is **actionable** (accept / edit / reject, then export).

---

## 2. Design direction

The tool reconciles **two structured worlds** — a source database and the FHIR guide — and the whole design is built on that one idea. The source side and the FHIR side are visually distinct, and the mapping is the bridge across a central seam. This is the hero: not a marketing hero image, but the reconciliation table itself, because the table *is* the product.

### 2.1 Design plan (tokens)

**Color** — a cool working-paper base, a warm/cool split that encodes the two worlds, one inky interactive accent, and attention colors used as contours only.

| Token | Hex | Role |
| --- | --- | --- |
| `base` | `#F6F7F9` | app background (cool paper) |
| `surface` | `#FFFFFF` | tables, panels |
| `ink` | `#1B2733` | primary text (deep slate, not tinted black) |
| `ink-soft` | `#5A6B7B` | secondary text |
| `source` | `#F4EFE6` | source-side cell tint (warm) |
| `target` | `#EBF0F3` | FHIR-side cell tint (cool) |
| `accent` | `#2F5BD0` | interactive / selected / in-process (deliberate indigo, not acid) |
| `review` | `#9A5B00` | needs review — amber, **contour only** |
| `gap` | `#8A1C3B` | blocking gap — burgundy, **contour only** |
| `done` | `#3F6212` | saved acuse only (toast/chip), never a button |

The warm/cool split is the one place color does real work: a glance tells you which side of the seam you're reading. Everything normal stays neutral; attention states are contours, never filled blocks, so a wall of mappings doesn't turn into a wall of color.

**Type** — IBM Plex Sans for the interface and IBM Plex Mono for identifiers. The pairing is a deliberate fit for the subject: Plex was drawn around the relationship between human and machine, which is exactly the boundary this tool mediates. Mono is used **only for real code identifiers** (`appointment.start_ts`, `Appointment.start`) — its actual job, not decoration — so the two faces also reinforce the two worlds.

Scale: page title 22 / section 15 / body 14 / table 13 / small 12. Line-height 1.5 on body, 1.35 on dense table rows. Weights: 400 body, 500 UI emphasis, 600 headings. No all-caps labels, no tracked-out eyebrows.

**Layout** — a three-zone workbench.

```
┌──────┬───────────────────────────────────────────────┬───────────┐
│ nav  │ header: breadcrumbs · page title · [Export]    │           │
│ rail │  tabs:  Source   Mapping   Gaps                │  Copilot  │
│ 220  │ ┌───────────────────────┬───────────────────┐  │  panel    │
│      │ │  SOURCE      │  seam   │   FHIR TARGET     │  │  360px    │
│      │ │  (warm)      │  conf.  │   (cool)          │  │  (toggle) │
│      │ │  table, full width, keyboard-first          │  │           │
│      │ └───────────────────────┴───────────────────┘  │           │
└──────┴───────────────────────────────────────────────┴───────────┘
```

Left-aligned throughout; the mapping table uses full width (no `max-width` on the listing). Content padding 20–24px. Radii kept quiet and consistent: 8px on controls and panels, 6px on inline chips.

### 2.2 Principles

1. **Two worlds, one seam.** Source and target are always distinguishable; the mapping lives in the gap between them.
2. **Confidence is quiet but never absent.** It is encoded once, consistently, by a small 3-segment meter plus a word — hue only reinforces, never carries alone.
3. **Citations are not footnotes.** Each mapping shows its source inline, because trust is what the tool sells.
4. **Density without noise.** A tool used for hours earns restraint: one accent, contours for attention, motion only on what the user just did.

### 2.3 Self-review against generic defaults

Checked the plan against the common tells and revised:
- Dropped a warm-cream + serif direction — it is both a cliché cluster and wrong for a dense data tool; kept a cool working paper instead.
- Rejected a terracotta accent (reads as a generated-page tell); chose an inky indigo that belongs to an engineering tool.
- Confidence is deliberately **not** a red-amber-green rainbow; it is a weight/meter device first, so the table doesn't glow.
- Mono is confined to identifiers, so it is content, not the "monospace label" tell.
- No `→` on buttons, no middle-dot meta strings, no numbered `01/02/03` markers (the content is not a sequence).

---

## 3. Shell and navigation

A left nav rail (220px, collapsible to 60px icons), breadcrumbs, and **tabs** as the single in-page pattern across the mapping stage. Nav groups: **Sources**, **Mappings**, **Guide** (read-only browser of the loaded guide), **Settings**.

---

## 4. Screens

### 4.1 Source setup

Where the integrator points the app at a database (or uploads `source_metadata.json`) and loads the guide.

- **Connect / upload:** engine + read-only connection, or drop a `source_metadata.json` / `.sql` dump. One primary action: *Load source*.
- **Detection summary:** tables with row counts, detected **candidate entities** (Appointment, Patient), and **lookup tables** found — each a neutral chip. The normal case carries no color.
- **Guide status:** which guide loaded (`hl7.fhir.cl.clcore 1.9.3`) and that it indexed.
- Primary action when ready: *Generate structure map* → opens the Mapping tab with an explicit progress state (never a silent wait).

Empty state copy is an invitation, not decoration: "Point at a database or drop its metadata to begin."

### 4.2 Mapping workbench (the core screen)

A full-width reconciliation table split by the central seam, plus a context panel (~340px, sticky) for the selected row.

Across the seam, per row:

| Zone | Content |
| --- | --- |
| **Source** (warm) | `appointment.start_ts` in mono; a `via lookup: appointment_status` tag when present |
| **Seam** | confidence meter + word; transform kind (`direct` · `code` · `calculated` · `fixed`) |
| **Target** (cool) | `appointment-bupa / Appointment.start` in mono; type `dateTime`; binding chip when present |
| **State** | `proposed` · `accepted` · `edited` · `rejected` |
| **Actions** | accept · edit · reject |

Selecting a row opens the context panel: the full citation, the source column profile (type, nulls, samples), the target element's `min/max` and binding, and the agent's reasoning. Editing shows a **diff before saving**, and switching tabs with unsaved edits prompts rather than discarding.

### 4.3 Gaps tab

The three non-one-to-one blocks:

- **Unfilled required** — required target elements with no source column. Headed by **one** burgundy contour banner ("3 required elements unmapped"), one per section, never one per row. Each entry names the element and the suggested resolution.
- **Unmapped source** — source columns with no destination; neutral; candidates to drop or escalate.
- **Ambiguities** — questions needing a human, each with its impact. This is the honest handoff to the integrator's judgment.

### 4.4 Copilot panel

The 360px right panel is the conversational copilot — the "copilot" in the project name. It toggles in and pushes the content (not an overlay) so the integrator works with it open. It answers about a selected mapping ("why this element?"), the guide ("what's required in `observation-bupa`?"), and the source — **always with a citation**. Answers stay in the panel; a cited answer links back to the row or guide section it came from.

### 4.5 Export

One action in the header. Exports `structure_map.json`; a secondary, disabled-until-scoped option compiles to a formal FHIR `StructureMap`. Success shows a confirmation **acuse** (a "Downloaded" toast), never a green button. The action keeps its name through the flow: the button says *Export*, the toast says *Exported*.

---

## 5. Confidence and attention (the one place signal lives)

Confidence rides the seam as a 3-segment meter with a word:

| Meaning | Device |
| --- | --- |
| High confidence / accepted | filled meter (3/3) + "high", neutral ink |
| Needs review (medium, ambiguity) | partial meter (2/3) + "review", amber contour |
| Low / routed to ambiguity | empty meter (1/3) + "low", amber contour |
| Blocking gap (Gaps tab) | burgundy contour banner, section-level |
| Interactive / selected | accent indigo |
| Saved | confirmation green on the acuse only |

The meter means hue is never the sole carrier (colorblind-safe, and legible in grayscale). Every attention state shows a word alongside its color.

---

## 6. States and review flow

- **Loading:** generation and re-runs always show progress.
- **Error:** connection/guide failures are explicit — what failed and how to fix it, in the interface's voice, no apology.
- **Review flow:** each mapping cycles `proposed → accepted | edited | rejected`. A header counter ("18 proposed · 11 accepted · 2 review") mirrors the Gaps banner from the same count. Export enables once no blocking gap remains, or with an explicit override.
- **Quality floor:** responsive to mobile (the table becomes stacked source→target pairs), visible keyboard focus, reduced-motion respected, full keyboard operation for a tool used for hours.

Motion is confined to what the user did: the panel sliding in, a row confirming on accept, the diff expanding. No section-entrance animations.

---

## 7. What to prototype first

1. The **shell** (rail + breadcrumbs + tabs) with the warm/cool seam established.
2. The **mapping table + context panel** reading a static `structure_map.json` — the screen that carries the demo.
3. The **confidence meter**, **citation chip**, and **gap banner** as reusable pieces.
4. The **Copilot panel**, which can start as a stub returning cited answers.
5. Source setup and Export last — they bookend the flow; the value is the review screen.

---

## 8. Open items

1. **Formal StructureMap export** — enable only when an execution engine is in scope.
2. **Two profiles** — does the toggle change only the Copilot's language, or also offer a "business view" that hides raw FHIR paths and shows concepts/processes? Recommended: both, behind one toggle.
3. **Bulk accept** — accept all high-confidence mappings at once, leaving only review items? Speeds the demo.
4. **Multiple entities** — one entity per screen with a switcher in breadcrumbs (recommended), vs all entities in one table.