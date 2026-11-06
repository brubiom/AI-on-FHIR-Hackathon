/* Structure Map Agent — workbench prototype logic.
   Reads a structure_map.json (sample inlined; real files via the Source tab),
   renders the reconciliation table + detail panel + gaps, tracks review state,
   and exports. The Copilot is a cited stub (no live model in the prototype). */

(function () {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const STORAGE_KEY = "sma-review-state";

  const state = {
    structureMap: window.SAMPLE_STRUCTURE_MAP,
    sourceMeta: window.SAMPLE_SOURCE_METADATA,
    entityIndex: 0,
    tab: "mapping",      // source | mapping | gaps
    selected: null,      // "map:<i>" | "ref:<i>"
    review: loadReview() // { "<entity>:<kind>:<i>": {s:'accepted'|'edited'|'rejected', note} }
  };

  function loadReview() {
    try { return JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}"); }
    catch (e) { return {}; }
  }
  function saveReview() {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(state.review)); } catch (e) {}
  }

  const entity = () => state.structureMap.maps[state.entityIndex];
  const rkey = (kind, i) => `${entity().entity}:${kind}:${i}`;
  const reviewOf = (kind, i) => state.review[rkey(kind, i)] || null;

  // ---- confidence device -------------------------------------------------
  function confClass(c) { return c >= 0.9 ? "high" : c >= 0.7 ? "review" : "low"; }
  function confWord(c) { return c >= 0.9 ? "high" : c >= 0.7 ? "review" : "low"; }
  function confMeter(c) {
    const cls = confClass(c);
    return `<span class="conf ${cls}" title="confidence ${c.toFixed(2)}">
      <span class="meter"><i></i><i></i><i></i></span>
      <span class="word">${confWord(c)}</span>
      <span class="num">${c.toFixed(2)}</span></span>`;
  }

  // ---- render: header / counters ----------------------------------------
  function renderHeader() {
    const maps = state.structureMap.maps;
    const sel = $("#entitySelect");
    sel.innerHTML = maps.map((m, i) =>
      `<option value="${i}" ${i === state.entityIndex ? "selected" : ""}>${esc(m.entity)}</option>`).join("");
    $("#targetProfile").textContent = entity().target_profile;

    // counts from current entity mappings + their review state
    const ms = entity().mappings || [];
    let accepted = 0, review = 0, rejected = 0, edited = 0;
    ms.forEach((m, i) => {
      const r = reviewOf("map", i);
      if (r && r.s === "accepted") accepted++;
      else if (r && r.s === "rejected") rejected++;
      else if (r && r.s === "edited") edited++;
      else if (confClass(m.confidence) !== "high") review++;
    });
    $("#counter").innerHTML =
      `<b>${ms.length}</b> proposed · <b>${accepted}</b> accepted` +
      (edited ? ` · <b>${edited}</b> edited` : "") +
      ` · <b>${review}</b> review` +
      (rejected ? ` · <b>${rejected}</b> rejected` : "");

    // gaps tab pill
    const gaps = (entity().unfilled_required || []).length;
    const gp = $("#gapsPill");
    gp.textContent = gaps || "";
    gp.classList.toggle("hidden", !gaps);

    // export enabled once no blocking gap remains (override checkbox aside)
    const override = $("#overrideGaps").checked;
    $("#exportBtn").disabled = gaps > 0 && !override;
    $("#overrideWrap").classList.toggle("hidden", gaps === 0);
  }

  // ---- render: mapping table --------------------------------------------
  function transformChip(t) { return `<span class="chip transform">${esc(t)}</span>`; }

  function stateLabel(kind, i, fallbackConf) {
    const r = reviewOf(kind, i);
    if (r) return r.s;
    return "proposed";
  }

  function mappingRow(m, i) {
    const src = m.source || {};
    const tgt = m.target || {};
    const lookup = src.via_lookup
      ? `<span class="chip lookup" title="resolved via lookup">via ${esc(src.via_lookup)}</span>` : "";
    const binding = m.binding
      ? `<span class="chip binding" title="binding ${esc(m.binding.strength)}">${esc(m.binding.valueset)} · ${esc(m.binding.strength)}</span>` : "";
    const st = stateLabel("map", i, m.confidence);
    const r = reviewOf("map", i);
    return `<tr data-key="map:${i}" tabindex="0" class="${state.selected === "map:" + i ? "selected" : ""}">
      <td class="src-cell">
        <span class="id">${esc(src.table)}<span class="dim">.</span>${esc(src.column)}</span>
        ${lookup ? `<div class="sub">${lookup}</div>` : ""}
      </td>
      <td>${confMeter(m.confidence)}<div class="sub">${transformChip(m.transform)}</div></td>
      <td class="tgt-cell">
        <span class="id">${esc(tgt.profile)} <span class="dim">/</span> ${esc(tgt.element)}</span>
        <div class="sub"><span class="chip type">${esc(tgt.type)}</span> ${binding} ${m.required ? '<span class="chip" title="min &ge; 1">required</span>' : ""}</div>
      </td>
      <td><span class="state" data-s="${esc(st)}">${esc(st)}</span>${r && r.note ? `<div class="sub">${esc(r.note)}</div>` : ""}</td>
      <td>
        <div class="row-actions" data-i="${i}" data-kind="map">
          <button class="icon-btn ${r && r.s === "accepted" ? "on" : ""}" data-act="accept" title="accept">✓</button>
          <button class="icon-btn ${r && r.s === "edited" ? "on" : ""}" data-act="edit" title="edit">✎</button>
          <button class="icon-btn ${r && r.s === "rejected" ? "on" : ""}" data-act="reject" title="reject">✕</button>
        </div>
      </td></tr>`;
  }

  function referenceRow(r, i) {
    const tgt = r.target || {};
    const rev = reviewOf("ref", i);
    const st = rev ? rev.s : "proposed";
    return `<tr data-key="ref:${i}" tabindex="0" class="${state.selected === "ref:" + i ? "selected" : ""}">
      <td class="src-cell"><span class="id">${esc(r.source_fk)}</span><div class="sub"><span class="chip">reference (FK)</span></div></td>
      <td>${confMeter(r.confidence)}<div class="sub">${transformChip("reference")}</div></td>
      <td class="tgt-cell"><span class="id">${esc(tgt.element)}</span>
        <div class="sub"><span class="chip type">${esc(tgt.type)}</span> <span class="chip binding">→ ${esc(tgt.target_profile)}</span></div></td>
      <td><span class="state" data-s="${esc(st)}">${esc(st)}</span></td>
      <td><div class="row-actions" data-i="${i}" data-kind="ref">
        <button class="icon-btn ${rev && rev.s === "accepted" ? "on" : ""}" data-act="accept" title="accept">✓</button>
        <button class="icon-btn ${rev && rev.s === "edited" ? "on" : ""}" data-act="edit" title="edit">✎</button>
        <button class="icon-btn ${rev && rev.s === "rejected" ? "on" : ""}" data-act="reject" title="reject">✕</button>
      </div></td></tr>`;
  }

  function renderMapping() {
    const e = entity();
    const rows = (e.mappings || []).map(mappingRow).join("") +
      (e.references || []).map(referenceRow).join("");
    $("#stage").innerHTML = `
      <div class="recon-wrap">
        <table class="recon" aria-label="reconciliation table">
          <thead><tr>
            <th class="src">Source</th><th>Seam</th><th class="tgt">FHIR target</th><th>State</th><th>Actions</th>
          </tr></thead>
          <tbody id="reconBody">${rows}</tbody>
        </table>
      </div>
      <aside class="detail" id="detail">${detailHtml()}</aside>`;
    $("#stage").className = "stage with-detail";
    bindRows();
  }

  function detailHtml() {
    if (!state.selected) {
      return `<div class="empty">Select a row to see its citation, source profile, target constraints and the agent's reasoning.</div>`;
    }
    const [kind, idx] = state.selected.split(":");
    const i = +idx;
    if (kind === "map") return mappingDetail(entity().mappings[i], i);
    return referenceDetail(entity().references[i], i);
  }

  function mappingDetail(m, i) {
    if (!m) return "";
    const src = m.source || {}, tgt = m.target || {}, cit = m.citation || {};
    const rev = reviewOf("map", i);
    const sm = findSourceCol(src.table, src.column);
    return `
      <div class="d-head">
        <div class="id">${esc(src.table)}.${esc(src.column)}</div>
        <div class="seam-kind">${esc(m.transform)} → ${esc(tgt.element)}</div>
      </div>
      <div class="d-body">
        <div class="d-sec"><h4>Citation</h4>
          <span class="cite"><span class="src">${esc(cit.source)}</span> · ${esc(cit.ref)}</span></div>
        <div class="d-sec"><h4>Confidence</h4>${confMeter(m.confidence)}</div>
        <div class="d-sec"><h4>Source column</h4>
          <dl class="kv">
            <dt>type</dt><dd>${esc(sm ? sm.sql_type : "—")}</dd>
            <dt>nullable</dt><dd>${sm ? (sm.nullable ? "yes" : "no") : "—"}</dd>
            ${src.via_lookup ? `<dt>lookup</dt><dd class="mono">${esc(src.via_lookup)}</dd>` : ""}
            <dt>samples</dt><dd>${sm && sm.samples ? esc(sm.samples.join(", ")) : "—"}</dd>
          </dl></div>
        <div class="d-sec"><h4>Target element</h4>
          <dl class="kv">
            <dt>profile</dt><dd class="mono">${esc(tgt.profile)}</dd>
            <dt>type</dt><dd>${esc(tgt.type)}</dd>
            <dt>required</dt><dd>${m.required ? "yes (min ≥ 1)" : "no"}</dd>
            ${m.binding ? `<dt>binding</dt><dd>${esc(m.binding.valueset)} <span class="chip binding">${esc(m.binding.strength)}</span></dd>` : ""}
          </dl></div>
        ${m.terminology_notes ? `<div class="d-sec"><h4>Terminology</h4><div class="note-amber">${esc(m.terminology_notes)}</div></div>` : ""}
        <div class="d-sec"><h4>Agent reasoning</h4><div class="rationale">${esc(m.rationale)}</div></div>
        ${rev && rev.note ? `<div class="d-sec"><h4>Your note</h4><div class="rationale">${esc(rev.note)}</div></div>` : ""}
        <div class="d-actions" data-i="${i}" data-kind="map">
          <button class="btn small" data-act="accept">Accept</button>
          <button class="btn small" data-act="edit">Edit</button>
          <button class="btn small ghost" data-act="reject">Reject</button>
        </div>
      </div>`;
  }

  function referenceDetail(r, i) {
    if (!r) return "";
    const tgt = r.target || {}, cit = r.citation || {};
    return `
      <div class="d-head"><div class="id">${esc(r.source_fk)}</div><div class="seam-kind">reference → ${esc(tgt.element)}</div></div>
      <div class="d-body">
        <div class="d-sec"><h4>Citation</h4><span class="cite"><span class="src">${esc(cit.source)}</span> · ${esc(cit.ref)}</span></div>
        <div class="d-sec"><h4>Confidence</h4>${confMeter(r.confidence)}</div>
        <div class="d-sec"><h4>Target</h4>
          <dl class="kv"><dt>element</dt><dd class="mono">${esc(tgt.element)}</dd>
          <dt>type</dt><dd>${esc(tgt.type)}</dd>
          <dt>targetProfile</dt><dd class="mono">${esc(tgt.target_profile)}</dd></dl></div>
        <div class="d-sec"><h4>Agent reasoning</h4><div class="rationale">${esc(r.rationale)}</div></div>
        <div class="d-actions" data-i="${i}" data-kind="ref">
          <button class="btn small" data-act="accept">Accept</button>
          <button class="btn small ghost" data-act="reject">Reject</button>
        </div>
      </div>`;
  }

  function findSourceCol(table, column) {
    const profiles = window.SAMPLE_COLUMN_PROFILES || {};
    return profiles[`${table}.${column}`] || null;
  }

  // ---- gaps tab ----------------------------------------------------------
  function renderGaps() {
    const e = entity();
    const unfilled = e.unfilled_required || [];
    const unmapped = e.unmapped_source || [];
    const amb = e.ambiguities || [];
    const banner = unfilled.length
      ? `<div class="gap-banner">${unfilled.length} required element${unfilled.length > 1 ? "s" : ""} unmapped — resolve or override before export.</div>` : "";
    $("#stage").className = "stage";
    $("#stage").innerHTML = `
      ${banner}
      <section class="gap-section">
        <h3>Unfilled required</h3>
        <p class="lead">Required target elements with no source column. The real gaps an integration must close.</p>
        <div class="card-list">${unfilled.length ? unfilled.map(u =>
          `<div class="card gap-card"><div class="c-title mono">${esc(u.element)}</div>
           <div class="c-meta">${esc(u.reason)} — <b>${esc(u.action)}</b></div></div>`).join("") :
          `<div class="card"><div class="c-meta">None. Every required element is covered.</div></div>`}</div>
      </section>
      <section class="gap-section">
        <h3>Unmapped source</h3>
        <p class="lead">Source columns with no destination; candidates to drop or escalate.</p>
        <div class="card-list">${unmapped.length ? unmapped.map(u =>
          `<div class="card"><div class="c-title mono">${esc(u.table)}.${esc(u.column)}</div>
           <div class="c-meta">${esc(u.reason)}</div></div>`).join("") :
          `<div class="card"><div class="c-meta">None.</div></div>`}</div>
      </section>
      <section class="gap-section">
        <h3>Ambiguities</h3>
        <p class="lead">Questions that need a human — the honest handoff to the integrator's judgment.</p>
        <div class="card-list">${amb.length ? amb.map(a =>
          `<div class="card"><div class="c-title mono">${esc(a.location)}</div>
           <div class="c-meta">${esc(a.question)}<br><span style="color:var(--review)">Impact:</span> ${esc(a.impact)}</div></div>`).join("") :
          `<div class="card"><div class="c-meta">None.</div></div>`}</div>
      </section>`;
  }

  // ---- source tab --------------------------------------------------------
  function lookupName(x) { return typeof x === "string" ? x : (x && x.table) || ""; }

  function renderSource() {
    const meta = state.sourceMeta || {};
    const tables = (meta.tables || []).map(t => ({
      name: t.name,
      rows: t.rows != null ? t.rows : (t.row_count != null ? t.row_count : "—"),
      role: t.role || (t.columns ? "table" : "—")
    }));
    const entities = meta.candidate_entities || [];
    const lookups = (meta.lookups || []).map(lookupName);
    $("#stage").className = "stage";
    $("#stage").innerHTML = `
      <div class="panel">
        <h3>Connect or upload</h3>
        <p class="lead">Point at a database or drop its metadata to begin. Drop a <code>source_metadata.json</code> or a <code>structure_map.json</code>.</p>
        <div class="dropzone" id="dropzone">Drop a .json here, or click to choose a file
          <input type="file" id="fileInput" accept=".json,application/json" class="hidden">
        </div>
      </div>
      <div class="panel">
        <h3>Detection summary</h3>
        <p class="lead">${esc(meta.engine || "—")} · database <span class="mono">${esc(meta.database || "—")}</span></p>
        <div class="kpi-row">
          <div class="kpi"><div class="n">${tables.length}</div><div class="l">tables</div></div>
          <div class="kpi"><div class="n">${entities.length}</div><div class="l">candidate entities</div></div>
          <div class="kpi"><div class="n">${lookups.length}</div><div class="l">lookup tables</div></div>
        </div>
        <div class="chipset" style="margin-bottom:14px">
          ${entities.slice(0, 30).map(x => `<span class="chip">${esc(x)}</span>`).join("")}
          ${lookups.map(x => `<span class="chip lookup">${esc(x)}</span>`).join("")}
        </div>
        <table class="detect-table"><thead><tr><th>Table</th><th>Rows</th><th>Role</th></tr></thead>
          <tbody>${tables.map(t => `<tr><td class="mono">${esc(t.name)}</td><td>${esc(t.rows)}</td><td>${esc(t.role)}</td></tr>`).join("")}</tbody>
        </table>
      </div>
      <div class="panel" id="guidePanel">
        <h3>Implementation Guide</h3>
        <p class="lead" id="guideStatus">checking guide status…</p>
        <div class="dropzone" id="igDrop">Drop the FHIR IG package (<span class="mono">.tgz</span>) here, or click to choose
          <input type="file" id="igInput" accept=".tgz,.gz,application/gzip,application/x-gzip" class="hidden">
        </div>
        <div id="igResult" class="sub" style="margin-top:10px"></div>
        <div style="margin-top:16px; display:flex; align-items:center; gap:10px;">
          <button class="btn primary" id="generateBtn">Generate structure map</button>
          <span class="sub" id="genResult"></span>
        </div>
      </div>`;
    bindSource();
    refreshGuideStatus();
  }

  // ---- events ------------------------------------------------------------
  function bindRows() {
    $$("#reconBody tr").forEach(tr => {
      tr.addEventListener("click", (ev) => {
        if (ev.target.closest(".row-actions")) return; // handled below
        selectRow(tr.getAttribute("data-key"));
      });
      tr.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); selectRow(tr.getAttribute("data-key")); }
      });
    });
    $$("#reconBody .row-actions .icon-btn").forEach(btn => {
      btn.addEventListener("click", (ev) => {
        ev.stopPropagation();
        const wrap = btn.closest(".row-actions");
        applyReview(wrap.getAttribute("data-kind"), +wrap.getAttribute("data-i"), btn.getAttribute("data-act"));
      });
    });
    // detail panel action buttons
    $$("#detail .d-actions .btn").forEach(btn => {
      btn.addEventListener("click", () => {
        const wrap = btn.closest(".d-actions");
        applyReview(wrap.getAttribute("data-kind"), +wrap.getAttribute("data-i"), btn.getAttribute("data-act"));
      });
    });
  }

  function selectRow(key) {
    state.selected = key;
    renderMapping();
  }

  function applyReview(kind, i, act) {
    const key = rkey(kind, i);
    if (act === "edit") {
      const current = state.review[key] || {};
      const note = window.prompt("Correction / note for this mapping:", current.note || "");
      if (note === null) return;
      state.review[key] = { s: "edited", note };
    } else if (act === "accept") {
      state.review[key] = { s: "accepted", note: (state.review[key] || {}).note || "" };
      toast("Accepted");
    } else if (act === "reject") {
      state.review[key] = { s: "rejected", note: (state.review[key] || {}).note || "" };
    }
    saveReview();
    renderHeader();
    renderMapping();
  }

  function bindSource() {
    const dz = $("#dropzone"), fi = $("#fileInput");
    if (dz && fi) {
      dz.addEventListener("click", () => fi.click());
      dz.addEventListener("dragover", (e) => { e.preventDefault(); dz.classList.add("drag"); });
      dz.addEventListener("dragleave", () => dz.classList.remove("drag"));
      dz.addEventListener("drop", (e) => { e.preventDefault(); dz.classList.remove("drag"); if (e.dataTransfer.files[0]) loadFile(e.dataTransfer.files[0]); });
      fi.addEventListener("change", () => { if (fi.files[0]) loadFile(fi.files[0]); });
    }
    // IG uploader (.tgz) → server
    const ig = $("#igDrop"), igi = $("#igInput");
    if (ig && igi) {
      ig.addEventListener("click", () => igi.click());
      ig.addEventListener("dragover", (e) => { e.preventDefault(); ig.classList.add("drag"); });
      ig.addEventListener("dragleave", () => ig.classList.remove("drag"));
      ig.addEventListener("drop", (e) => { e.preventDefault(); ig.classList.remove("drag"); if (e.dataTransfer.files[0]) uploadIG(e.dataTransfer.files[0]); });
      igi.addEventListener("change", () => { if (igi.files[0]) uploadIG(igi.files[0]); });
    }
    const gen = $("#generateBtn");
    if (gen) gen.addEventListener("click", runGenerate);
  }

  function refreshGuideStatus() {
    const el = $("#guideStatus");
    if (!el) return;
    fetch("/api/guide-status", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((s) => {
        if (!s) throw new Error("no api");
        if (s.loaded) {
          el.innerHTML = `Loaded — <b>${s.profiles}</b> profiles · <b>${s.campos_rows}</b> elements · ` +
            `<b>${s.modelos_rows}</b> logical-model fields · <b>${s.terminologias_rows}</b> terminology codes.`;
        } else {
          el.textContent = "No guide loaded yet. Upload the FHIR IG package below.";
        }
      })
      .catch(() => {
        // served statically / file:// — no backend
        el.innerHTML = "Guide loading needs the running server. Start it with " +
          "<code>python serve.py</code>, then reload.";
        const ig = $("#igDrop"); if (ig) { ig.style.opacity = ".5"; ig.style.pointerEvents = "none"; }
        const gb = $("#generateBtn"); if (gb) gb.disabled = true;
      });
  }

  function uploadIG(file) {
    const res = $("#igResult");
    res.textContent = `Uploading ${file.name} and building the guide store…`;
    fetch("/api/load-ig?name=" + encodeURIComponent(file.name), { method: "POST", body: file })
      .then((r) => r.json().then((j) => ({ ok: r.ok, j })))
      .then(({ ok, j }) => {
        if (ok && j.ok) {
          res.innerHTML = `Loaded <b>${esc(file.name)}</b> → ${j.profiles} profiles, ` +
            `${j.campos_rows} elements, ${j.terminologias_rows} terminology codes.`;
          toast("Guide loaded");
          refreshGuideStatus();
        } else {
          res.innerHTML = `<span style="color:var(--gap)">Error:</span> ${esc(j.error || "upload failed")}`;
        }
      })
      .catch((e) => { res.innerHTML = `<span style="color:var(--gap)">Error:</span> ${esc(e.message)}`; });
  }

  function runGenerate() {
    const out = $("#genResult");
    out.textContent = "Running the agent…";
    fetch("/api/generate", { method: "POST" })
      .then((r) => r.json().then((j) => ({ ok: r.ok, j })))
      .then(({ ok, j }) => {
        if (ok && j.ok) {
          out.textContent = "Done — loading the structure map.";
          toast("Structure map generated");
          tryLoad("./data/structure_map.json", (d) => {
            if (d && d.maps) state.structureMap = d;
            else if (d && d.entity) state.structureMap = { generated_at: d.generated_at || "", maps: [d] };
          }).then(() => { state.entityIndex = 0; state.selected = null; setTab("mapping"); });
        } else {
          out.innerHTML = `<span style="color:var(--gap)">${esc(j.error || "failed")}</span>`;
        }
      })
      .catch((e) => { out.innerHTML = `<span style="color:var(--gap)">${esc(e.message)}</span>`; });
  }

  function loadFile(file) {
    const reader = new FileReader();
    reader.onload = () => {
      try {
        const data = JSON.parse(reader.result);
        if (data.maps || data.entity) {
          state.structureMap = data.maps ? data : { generated_at: data.generated_at || "", maps: [data] };
          state.entityIndex = 0; state.selected = null; state.tab = "mapping";
          toast(`Loaded structure map (${file.name})`);
        } else if (data.tables) {
          state.sourceMeta = data;
          toast(`Loaded source metadata (${file.name})`);
        } else {
          toast("Unrecognized JSON shape");
        }
        render();
      } catch (e) { toast("Invalid JSON: " + e.message); }
    };
    reader.readAsText(file);
  }

  // ---- copilot (cited stub) ---------------------------------------------
  function copilotAnswer(q) {
    const e = entity();
    const ql = q.toLowerCase();
    // very small canned, cited responder keyed off the selection / keywords
    if (state.selected && (ql.includes("why") || ql.includes("por qu"))) {
      const [k, idx] = state.selected.split(":"); const i = +idx;
      const m = k === "map" ? e.mappings[i] : e.references[i];
      if (m) return { text: m.rationale, cite: m.citation ? `${m.citation.source} · ${m.citation.ref}` : null };
    }
    if (ql.includes("required") || ql.includes("requerid")) {
      const u = e.unfilled_required || [];
      return { text: u.length ? `${u.length} required element(s) still unmapped in ${e.target_profile}: ${u.map(x => x.element).join(", ")}.` :
        `All required elements in ${e.target_profile} are covered.`, cite: `campos_perfiles.csv · ${e.target_profile}` };
    }
    if (ql.includes("binding") || ql.includes("terminolog")) {
      const withB = (e.mappings || []).filter(m => m.binding);
      return { text: withB.length ? `Bound elements: ${withB.map(m => `${m.target.element} → ${m.binding.valueset} (${m.binding.strength})`).join("; ")}.` :
        "No terminology bindings on this entity.", cite: `terminologias.csv` };
    }
    return { text: `In the prototype the Copilot returns cited answers about the selected mapping, the guide, and the source. Ask "why this element?", "what's required in ${e.target_profile}?", or about bindings.`, cite: null };
  }

  function pushMsg(role, html) {
    const log = $("#copilotLog");
    const div = document.createElement("div");
    div.className = "msg " + role;
    div.innerHTML = html;
    log.appendChild(div);
    log.scrollTop = log.scrollHeight;
  }

  function askCopilot(q) {
    if (!q.trim()) return;
    pushMsg("user", esc(q));
    const a = copilotAnswer(q);
    const cite = a.cite ? `<div class="cite"><span class="src">${esc(a.cite.split(" · ")[0])}</span>${a.cite.includes(" · ") ? " · " + esc(a.cite.split(" · ").slice(1).join(" · ")) : ""}</div>` : "";
    pushMsg("bot", esc(a.text) + cite);
  }

  // ---- export ------------------------------------------------------------
  function exportMap() {
    const out = JSON.parse(JSON.stringify(state.structureMap));
    // fold review decisions into the exported map
    out.maps.forEach((m) => {
      (m.mappings || []).forEach((mm, i) => {
        const r = state.review[`${m.entity}:map:${i}`];
        if (r) { mm.review_state = r.s; if (r.note) mm.review_note = r.note; }
      });
      (m.references || []).forEach((rr, i) => {
        const r = state.review[`${m.entity}:ref:${i}`];
        if (r) { rr.review_state = r.s; if (r.note) rr.review_note = r.note; }
      });
    });
    const blob = new Blob([JSON.stringify(out, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = "structure_map.json"; a.click();
    URL.revokeObjectURL(url);
    toast("Exported");
  }

  // ---- toast -------------------------------------------------------------
  let toastTimer = null;
  function toast(msg) {
    const t = $("#toast");
    t.querySelector(".label").textContent = msg;
    t.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.remove("show"), 1800);
  }

  // ---- tabs / render -----------------------------------------------------
  function setTab(tab) {
    state.tab = tab;
    $$(".tabs .tab").forEach(b => b.classList.toggle("active", b.getAttribute("data-tab") === tab));
    render();
  }

  function render() {
    renderHeader();
    $$(".tabs .tab").forEach(b => b.classList.toggle("active", b.getAttribute("data-tab") === state.tab));
    if (state.tab === "mapping") renderMapping();
    else if (state.tab === "gaps") renderGaps();
    else renderSource();
  }

  // ---- init --------------------------------------------------------------
  function init() {
    // tabs
    $$(".tabs .tab").forEach(b => b.addEventListener("click", () => setTab(b.getAttribute("data-tab"))));
    // entity switcher
    $("#entitySelect").addEventListener("change", (e) => {
      state.entityIndex = +e.target.value; state.selected = null; render();
    });
    // rail
    $("#railToggle").addEventListener("click", () => $("#app").classList.toggle("rail-collapsed"));
    $$("#railNav a").forEach(a => a.addEventListener("click", (e) => {
      e.preventDefault();
      const to = a.getAttribute("data-nav");
      $$("#railNav a").forEach(x => x.classList.remove("active"));
      a.classList.add("active");
      if (to === "sources") setTab("source");
      else if (to === "mappings") setTab("mapping");
      else if (to === "guide") { toast("Guide browser — read-only (not in prototype)"); }
      else toast(to.charAt(0).toUpperCase() + to.slice(1));
    }));
    // copilot
    $("#copilotToggle").addEventListener("click", () => $("#app").classList.toggle("copilot-open"));
    $("#copilotClose").addEventListener("click", () => $("#app").classList.remove("copilot-open"));
    $("#copilotForm").addEventListener("submit", (e) => {
      e.preventDefault(); const inp = $("#copilotInput"); askCopilot(inp.value); inp.value = "";
    });
    $$(".suggests button").forEach(b => b.addEventListener("click", () => askCopilot(b.textContent)));
    // export + override
    $("#exportBtn").addEventListener("click", exportMap);
    $("#overrideGaps").addEventListener("change", renderHeader);

    bootstrap();
  }

  // Load real artifacts from the server when present; fall back to the inlined
  // samples (e.g. opened from file://, where fetch is blocked).
  function tryLoad(url, apply) {
    return fetch(url, { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (d) apply(d); })
      .catch(() => {});
  }

  function bootstrap() {
    Promise.all([
      tryLoad("./data/source_metadata.json", (d) => { if (d && d.tables) state.sourceMeta = d; }),
      tryLoad("./data/structure_map.json", (d) => {
        if (d && d.maps) state.structureMap = d;
        else if (d && d.entity) state.structureMap = { generated_at: d.generated_at || "", maps: [d] };
      })
    ]).then(() => render());
  }

  document.addEventListener("DOMContentLoaded", init);
})();
