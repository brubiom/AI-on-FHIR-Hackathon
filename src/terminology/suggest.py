"""Concept-mapping suggestions: one source concept in, up to 3 standard concepts out.

Entry points
------------
  suggest_concepts(concept, target_systems=None, max_suggestions=3) -> SuggestionResult
  suggest_many(concepts, ...)                                         -> list[SuggestionResult]

`concept` can be almost anything a provider system holds (see `coerce_input`):
  * free text                     "Hemoglobin A1c"
  * HL7 v2 CE/CWE                 "HGB^Hemoglobin^L"  /  "718-7^Hemoglobin^LN"
  * "system|code" token           "http://loinc.org|718-7"
  * FHIR Coding / CodeableConcept {"system":..., "code":..., "display":...}
  * a source row / dict           {"code": "ALG00118", "display": "Shellfish",
                                   "synonyms": "shrimp|crab|lobster", "context": "allergen"}
  * ConceptInput

Pipeline (all lookups go through the Termbox MCP server):
  1. Resolve the source system (alias / OID / HL7 v2 id / URI); if none is given,
     infer candidates from the code's shape (LOINC & SNOMED check digits, ICD-10, CPT, RxNorm).
  2. Exact path — standard (or inferred) system + code: `lookup-code`, trying code
     spellings (ICD-10 with/without dot). A hit whose display agrees with the source
     text is the top suggestion; a hit that disagrees is kept but down-scored and warned.
  3. Text path — display + synonyms (+ the verified display, which translates across
     systems, e.g. ICD-10-CM -> SNOMED CT) are expanded into a few queries and sent to
     `find-code` in every target system in parallel; every hit is re-scored locally.
  4. Merge by (system, code), drop weak/inactive noise, return the top N with evidence.

Scores are heuristic match strengths in [0, 1], not probabilities:
  1.0        code verified in Termbox and its display agrees with the source text
  0.80-0.95  strong text match (0.95 = normalised text identical)
  0.5-0.8    plausible; a human/agent should confirm
  < 0.5      weak; only returned when nothing better exists
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field

import config
from src.terminology import matching, systems
from src.terminology.client import TermboxClient, TermboxConcept, TermboxError

MATCH_EXACT = "exact-code"            # source code verified as-is in its system
MATCH_VARIANT = "code-variant"        # verified after normalising the spelling (Z3482 -> Z34.82)
MATCH_INFERRED = "inferred-system"    # code had no system; shape-inferred system verified it
MATCH_TEXT = "text-search"            # found by searching display/synonym text

_default_client: TermboxClient | None = None


def default_client() -> TermboxClient:
    global _default_client
    if _default_client is None:
        _default_client = TermboxClient()
    return _default_client


# ===================================================================== model
@dataclass
class ConceptInput:
    code: str | None = None
    system: str | None = None
    display: str | None = None
    synonyms: list[str] = field(default_factory=list)
    context: str | None = None   # free-form hints: specimen, units, column/table name, domain

    def texts(self) -> list[str]:
        out = [t for t in [self.display, *self.synonyms] if t and t.strip()]
        seen: set[str] = set()
        return [t for t in out if not (t.lower() in seen or seen.add(t.lower()))]


@dataclass
class Suggestion:
    system: str
    system_name: str
    code: str
    display: str | None
    score: float
    match_type: str
    inactive: bool = False
    matched_query: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def coding(self) -> dict:
        """FHIR Coding for direct use in resources / ConceptMaps."""
        c = {"system": self.system, "code": self.code}
        if self.display:
            c["display"] = self.display
        return c


@dataclass
class SuggestionResult:
    input: ConceptInput
    analysis: dict
    suggestions: list[Suggestion]
    warnings: list[str]

    def to_dict(self) -> dict:
        return {
            "input": asdict(self.input),
            "analysis": self.analysis,
            "suggestions": [{**asdict(s), "coding": s.coding} for s in self.suggestions],
            "warnings": self.warnings,
        }


# ============================================================ input coercion
def _split_synonyms(v) -> list[str]:
    if not v:
        return []
    if isinstance(v, str):
        return [s.strip() for s in re.split(r"[|;\n]", v) if s.strip()]
    return [str(s).strip() for s in v if str(s).strip()]


def coerce_input(obj) -> ConceptInput:
    """Normalise any supported concept shape into a ConceptInput."""
    if isinstance(obj, ConceptInput):
        return obj
    if obj is None:
        return ConceptInput()
    if isinstance(obj, str):
        s = obj.strip()
        if "^" in s:  # HL7 v2 CE / CWE: code^text^system[^alt-code^alt-text^alt-system]
            p = (s.split("^") + [""] * 6)[:6]
            syn = [p[4]] if p[4] else []
            return ConceptInput(code=p[0] or None, display=p[1] or None,
                                system=p[2] or None, synonyms=syn)
        m = re.fullmatch(r"((?:https?://|urn:)\S+)\|(\S+)", s)
        if m:
            return ConceptInput(system=m.group(1), code=m.group(2))
        return ConceptInput(display=s)
    if isinstance(obj, dict):
        d = {str(k).lower(): v for k, v in obj.items()}
        if isinstance(d.get("coding"), list) and d["coding"]:  # CodeableConcept
            first = coerce_input(d["coding"][0])
            first.synonyms += [c.get("display") for c in d["coding"][1:] if c.get("display")]
            if d.get("text"):
                if first.display:
                    first.synonyms.insert(0, d["text"])
                else:
                    first.display = d["text"]
            return first
        code = d.get("code") or d.get("local_code") or d.get("id")
        display = (d.get("display") or d.get("text") or d.get("name") or d.get("label")
                   or d.get("description") or d.get("term"))
        system = d.get("system") or d.get("code_system") or d.get("codesystem")
        ctx = d.get("context")
        if isinstance(ctx, dict):
            ctx = " ".join(f"{k} {v}" for k, v in ctx.items() if v)
        extra_desc = d.get("description") if display != d.get("description") else None
        syn = _split_synonyms(d.get("synonyms") or d.get("aliases"))
        if extra_desc:
            syn.append(str(extra_desc))
        return ConceptInput(code=str(code) if code is not None else None,
                            system=system, display=display, synonyms=syn,
                            context=str(ctx) if ctx else None)
    raise TypeError(f"unsupported concept input: {type(obj).__name__}")


# ================================================================== engine
def suggest_concepts(concept, target_systems: list[str] | None = None,
                     max_suggestions: int = 3, *,
                     client: TermboxClient | None = None) -> SuggestionResult:
    """Suggest up to `max_suggestions` standard concepts for one source concept.

    target_systems: code systems to suggest in (URIs or aliases like "LOINC", "SNOMED").
      None -> the source's own standard system if it has one, else config.TERMBOX_DEFAULT_TARGETS.
    """
    client = client or default_client()
    ci = coerce_input(concept)
    max_suggestions = max(1, min(int(max_suggestions), 10))
    warnings: list[str] = []
    texts = ci.texts()

    targets = _resolve_targets(target_systems, warnings)
    src_system = systems.resolve_system(ci.system)
    local = systems.is_local_hint(ci.system) or bool(src_system and not systems.is_standard(src_system))
    if ci.system and not src_system and not local:
        warnings.append(f"unrecognised system hint {ci.system!r}; treated as local")
        local = True

    analysis: dict = {
        "source_system": src_system or ci.system,
        "source_system_kind": "local" if local else ("standard" if src_system else "unknown"),
        "inferred_systems": [],
        "code_status": "no-code" if not ci.code else "unverified",
        "target_systems": None,
        "queries": [],
    }

    # --- 1+2. exact path -------------------------------------------------
    verified: list[tuple[TermboxConcept, str]] = []
    if ci.code and not local:
        lookup_systems = [src_system] if src_system else systems.infer_systems_from_code(ci.code)
        analysis["inferred_systems"] = [] if src_system else lookup_systems
        verified = _verify_code(client, ci.code, lookup_systems, src_system is None, warnings)
        if verified:
            analysis["code_status"] = "verified"
        elif src_system:
            analysis["code_status"] = "not-found"
            warnings.append(f"code {ci.code!r} not found in {systems.system_name(src_system)}")
        else:
            analysis["code_status"] = "local"  # no standard shape matched / confirmed
    elif ci.code:
        analysis["code_status"] = "local"

    if targets is None:
        own = {c.system for c, _ in verified} or ({src_system} if src_system and not local else set())
        targets = sorted(own) or list(config.TERMBOX_DEFAULT_TARGETS)
    analysis["target_systems"] = targets

    candidates: dict[tuple[str, str], Suggestion] = {}
    for concept_hit, match_type in verified:
        text_score = matching.score_candidate(texts, concept_hit.display, ci.context) if texts else None
        s = _exact_suggestion(concept_hit, match_type, text_score, warnings, ci.code)
        if concept_hit.system in targets:
            _keep(candidates, s)

    # --- 3. text path ----------------------------------------------------
    search_texts = list(texts)
    for t in texts:  # lab-speak -> terminology wording, scored as secondary labels
        for v in matching.phrase_variants(t):
            if v not in search_texts:
                search_texts.append(v)
    for concept_hit, _ in verified:  # verified display bridges to other target systems
        if concept_hit.display and concept_hit.display.lower() not in {t.lower() for t in search_texts}:
            search_texts.append(concept_hit.display)
    queries = matching.build_queries(search_texts, max_queries=config.TERMBOX_MAX_QUERIES,
                                     context=ci.context)
    analysis["queries"] = queries
    if not queries and not verified:
        warnings.append("nothing to search: the input has no display/synonym text and the code "
                        "could not be verified")

    jobs = [(sys, q, kind) for sys in targets for q in queries
            for kind in (("text", "rxnorm-ingredient") if sys == systems.RXNORM else ("text",))]
    src_is_loinc_part = bool(ci.code and re.match(r"L[AP]\d", ci.code.upper()))
    if jobs:
        with ThreadPoolExecutor(max_workers=min(8, len(jobs))) as pool:
            results = list(pool.map(lambda j: _safe_find(client, *j, warnings), jobs))
        for (sys, q, _kind), hits in zip(jobs, results):
            for h in hits:
                score = matching.score_candidate(search_texts, h.display, ci.context)
                if (h.system or sys) == systems.LOINC:
                    score += matching.loinc_unit_adjustment(h.display, ci.context)
                # With no source text, same-system text hits only echo the verified display:
                # keep them below the verified code. Cross-system hits keep their score.
                echo_only = not texts and (h.system or sys) in {c.system for c, _ in verified}
                score = max(0.0, min(score, 0.75 if echo_only else 0.95))
                notes = []
                if h.inactive:
                    score *= 0.6
                    notes.append("inactive code")
                if h.display and h.display.lower().startswith("deprecated"):
                    score *= 0.6
                    notes.append("deprecated")
                if (h.system or sys) == systems.LOINC and h.code[:2] in ("LP", "LA", "LL") \
                        and not src_is_loinc_part:
                    score *= 0.7  # LOINC Part / Answer / Answer-list codes are not observation codes
                    notes.append("LOINC part/answer code, not an observation code")
                _keep(candidates, Suggestion(
                    system=h.system or sys, system_name=systems.system_name(h.system or sys),
                    code=h.code, display=h.display, score=round(score, 3), match_type=MATCH_TEXT,
                    inactive=h.inactive, matched_query=q, notes=notes))

    # --- 4. rank -----------------------------------------------------------
    ranked = sorted(candidates.values(), key=lambda s: (-s.score, s.inactive, s.code))
    strong = [s for s in ranked if s.score >= config.TERMBOX_MIN_SCORE]
    picked = (strong or ranked[:1])[:max_suggestions]
    if not picked:
        warnings.append("no candidate concepts found in " + ", ".join(map(systems.system_name, targets)))
    elif picked[0].score < 0.5:
        warnings.append("only weak matches found; needs human review")

    # True when any Termbox call failed: "no suggestions" may then mean "server down", not "no match".
    analysis["degraded"] = any(" failed" in w for w in warnings)
    return SuggestionResult(input=ci, analysis=analysis, suggestions=picked,
                            warnings=list(dict.fromkeys(warnings)))


def suggest_many(concepts, target_systems: list[str] | None = None, max_suggestions: int = 3,
                 *, client: TermboxClient | None = None, workers: int = 4) -> list[SuggestionResult]:
    """Batch helper: same as suggest_concepts for each input, shared client + cache."""
    client = client or default_client()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(
            lambda c: suggest_concepts(c, target_systems, max_suggestions, client=client), concepts))


# ================================================================ helpers
def _resolve_targets(target_systems, warnings) -> list[str] | None:
    if not target_systems:
        return None
    out = []
    for t in target_systems:
        uri = systems.resolve_system(t)
        if uri is None:
            warnings.append(f"unknown target system {t!r} ignored")
        elif uri not in out:
            out.append(uri)
    return out or None


def _verify_code(client, code, lookup_systems, inferred, warnings) -> list[tuple[TermboxConcept, str]]:
    def one(sys):
        for i, variant in enumerate(systems.code_variants(sys, code)):
            try:
                hit = client.lookup_code(sys, variant)
            except TermboxError as exc:
                warnings.append(f"lookup failed in {systems.system_name(sys)}: {exc}")
                return None
            if hit:
                mt = MATCH_INFERRED if inferred else (MATCH_EXACT if i == 0 else MATCH_VARIANT)
                return hit, mt
        return None

    if not lookup_systems:
        return []
    with ThreadPoolExecutor(max_workers=len(lookup_systems)) as pool:
        return [r for r in pool.map(one, lookup_systems) if r]


def _exact_suggestion(hit: TermboxConcept, match_type, text_score, warnings, src_code) -> Suggestion:
    notes = []
    if match_type == MATCH_VARIANT:
        notes.append(f"source code {src_code!r} normalised to {hit.code!r}")
    if match_type == MATCH_INFERRED:
        notes.append("system inferred from the code's shape; confirm the source really uses it")
    base = {MATCH_EXACT: 1.0, MATCH_VARIANT: 0.97, MATCH_INFERRED: 0.9}[match_type]
    if text_score is None:
        score = base if match_type != MATCH_INFERRED else 0.8  # bare code, nothing to corroborate
    elif text_score >= 0.45:
        score = base
    else:
        # Code exists but means something else than the source label: likely a bad legacy map.
        score = round(0.4 + 0.4 * text_score, 3)
        notes.append("source text disagrees with the code's display")
        if match_type != MATCH_INFERRED:
            warnings.append(f"code {hit.code!r} exists but its display {hit.display!r} does not match "
                            "the source text; the existing mapping may be wrong")
    if hit.inactive:
        score = round(score * 0.6, 3)
        notes.append("inactive code")
        warnings.append(f"code {hit.code!r} is inactive in {systems.system_name(hit.system)}")
    return Suggestion(system=hit.system, system_name=systems.system_name(hit.system), code=hit.code,
                      display=hit.display, score=score, match_type=match_type,
                      inactive=hit.inactive, notes=notes)


def _keep(candidates: dict, s: Suggestion) -> None:
    key = (s.system, s.code)
    cur = candidates.get(key)
    if cur is None or s.score > cur.score:
        if cur is not None and cur.match_type != MATCH_TEXT and s.match_type == MATCH_TEXT:
            return  # never let a text hit replace a verified-code hit for the same concept
        candidates[key] = s


def _safe_find(client, system, query, kind, warnings) -> list[TermboxConcept]:
    try:
        if kind == "rxnorm-ingredient":
            # find-code over RxNorm drowns ingredients in products/combinations (50-hit cap),
            # so ask for single ingredients (tty=IN) explicitly. Products still come via find-code.
            return client.search_rxnorm(search=query, include=[
                {"property": "tty", "op": "=", "value": "IN"}])
        return client.find_code(system, query)
    except TermboxError as exc:
        warnings.append(f"search failed in {systems.system_name(system)} for {query!r}: {exc}")
        return []
