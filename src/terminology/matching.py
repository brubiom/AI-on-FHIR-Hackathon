"""Text normalisation, query building and scoring for concept suggestions.

Termbox's `find-code` is a word match on display text: no synonyms, no relevance
ranking, a fixed top-20. Provider labels are full of abbreviations and shouting
("CBC W/O DIFF", "XR CHEST 1 VIEW PORTABLE", "Hgb"). So we:

  1. normalise + expand abbreviations before searching (build_queries),
  2. run several query variants, and
  3. re-rank every hit locally against the source text (score_candidate).

The abbreviation table is deliberately small and generic. Extend ABBREVIATIONS
when a new source system brings its own shorthand — that is the main tuning knob.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

# lowercase token -> expansion (may be several words). Applied to whole tokens only.
ABBREVIATIONS: dict[str, str] = {
    "w/o": "without", "wo": "without", "w/": "with", "w": "with", "&": "and",
    "hgb": "hemoglobin", "hct": "hematocrit", "wbc": "leukocytes", "rbc": "erythrocytes",
    "plt": "platelets", "cbc": "complete blood count", "bmp": "basic metabolic panel",
    "cmp": "comprehensive metabolic panel", "lytes": "electrolytes", "diff": "differential",
    "auto": "automated", "abd": "abdomen", "pelv": "pelvis", "cxr": "chest xr",
    "sob": "shortness of breath", "uti": "urinary tract infection", "htn": "hypertension",
    "dm": "diabetes mellitus", "dm2": "type 2 diabetes mellitus", "t2dm": "type 2 diabetes mellitus",
    "copd": "chronic obstructive pulmonary disease", "chf": "congestive heart failure",
    "cva": "cerebrovascular accident", "afib": "atrial fibrillation",
    # NB: no "dx" (clashes with DICOM modality DX) and no "mi" (clashes with severity "MI").
    "pcn": "penicillin", "hx": "history", "fx": "fracture",
    "bilat": "bilateral", "lt": "left", "rt": "right", "ped": "pediatric", "peds": "pediatric",
    "vit": "vitamin", "ser": "serum", "plas": "plasma", "bld": "blood", "ur": "urine",
    # Specimen / tube codes -> the specimen word LOINC uses (mostly arrives via `context`).
    "edta": "blood", "lav": "blood", "k2edta": "blood", "bcb": "blood", "sst": "serum",
    "naf": "plasma", "lih": "plasma", "pst": "plasma", "cit": "plasma", "csf": "cerebral spinal fluid",
}

# Whole-phrase rewrites that produce extra search/score variants (the original is kept).
# Mostly lab-speak -> LOINC component names. Regexes run on lowercase text.
PHRASE_SYNONYMS: list[tuple[str, str]] = [
    (r"\bwhite blood cells?\b", "leukocytes"),
    (r"\bred blood cells?\b", "erythrocytes"),
    (r"\bplatelet count\b", "platelets"),
    (r"\bmean corpuscular volume\b", "mcv"),
    (r"\bmean corpuscular hemoglobin concentration\b", "mchc"),
    (r"\bmean corpuscular hemoglobin\b", "mch"),
    (r"\blactic acid\b", "lactate"),
    (r"\bprothrombin time inr\b|\bpt ?/ ?inr\b", "inr"),
    (r"\bblood urea nitrogen\b|\bbun\b", "urea nitrogen"),
    (r"\bvitamin d 25[- ]?oh\b|\b25[- ]?oh vitamin d\b", "25-hydroxyvitamin d"),
    (r"\bhemoglobin a1c\b|\bhba1c\b", "hemoglobin a1c/hemoglobin.total"),
    (r"^(\w+) culture$", r"bacteria identified \1 culture"),
    (r"\s*\((send ?out|stat|ref lab|reference lab)\)", ""),
]


def phrase_variants(text: str | None) -> list[str]:
    """Rewritten forms of `text` (excluding the original), e.g. 'White blood cell count' -> 'leukocytes count'."""
    if not text:
        return []
    low = text.lower()
    out: list[str] = []
    cur = low
    for pattern, repl in PHRASE_SYNONYMS:
        new = re.sub(pattern, repl, cur).strip()
        if new != cur:
            cur = new
    if cur != low and cur:
        out.append(cur)
    return out

# Words that carry no meaning for matching.
STOPWORDS = {"of", "in", "the", "a", "an", "by", "for", "to", "on", "at", "or", "per", "from",
             "nos", "unspecified", "finding", "disorder", "procedure", "substance",
             "organism", "observable", "entity",
             # connectors: "X with Y" vs "Y due to X". ("without" is kept: it changes meaning.)
             "with", "due", "and", "&"}

# Source unit (lowercase) -> LOINC property shown in brackets in the display.
# Lets "Hemoglobin" + units g/dL prefer "Hemoglobin [Mass/volume] in Blood".
_UNIT_PROPERTY = [
    (re.compile(r"^(m|u|n|p)?g/(d|m)?l$|^g/l$"), "mass/volume"),
    (re.compile(r"^(m|u|n|p)?mol/(d|m)?l$|^meq/l$"), "moles/volume"),
    (re.compile(r"^(x10e\d+|10\*\d+|k|10\^\d+)?/(u|n|m)?l$|^x10e\d+/ul$|^cells/ul$"), "#/volume"),
    (re.compile(r"^%$"), "fraction"),
    (re.compile(r"^(u|iu|miu)/l$|^u/ml$"), "enzymatic activity/volume"),
    (re.compile(r"^fl$"), "entitic mean volume"),
    (re.compile(r"^pg$"), "entitic mass"),
    (re.compile(r"^(sec|s|min)$"), "time"),
    (re.compile(r"^mm\[?hg\]?$"), "pressure"),
]
_UNIT_RE = re.compile(r"\bunits?\s*[:=]?\s*([^\s,;]+)", re.I)


def loinc_unit_adjustment(display: str | None, context: str | None) -> float:
    """Score delta for a LOINC candidate given units in the context (+0.08 agree, -0.08 clash).

    Quantitative units mean the source is a measurement, so [Presence]/[Type]/ordinal
    LOINC codes are penalised; a matching bracketed property is rewarded.
    """
    if not display or not context:
        return 0.0
    m = _UNIT_RE.search(context)
    if not m:
        return 0.0
    unit = m.group(1).lower().strip("()[]")
    prop = next((p for rx, p in _UNIT_PROPERTY if rx.search(unit)), None)
    bracket = re.search(r"\[([^\]]+)\]", display)
    if not bracket:
        return 0.0
    b = bracket.group(1).lower()
    if prop and prop in b:
        return 0.08
    if b in {"presence", "type", "identifier", "interpretation"} or "ord" in b:
        return -0.08
    return 0.0

_TOKEN_RE = re.compile(r"w/o|w/|[a-z0-9]+(?:\.[0-9]+)?|&", re.I)


def tokens(text: str | None, expand: bool = True) -> list[str]:
    if not text:
        return []
    out: list[str] = []
    for t in _TOKEN_RE.findall(text.lower()):
        if expand and t in ABBREVIATIONS:
            out.extend(ABBREVIATIONS[t].split())
        else:
            out.append(t)
    return out


def content_tokens(text: str | None, expand: bool = True) -> set[str]:
    return {_stem(t) for t in tokens(text, expand) if t not in STOPWORDS}


def _stem(t: str) -> str:
    """Very light stemming so 'platelets'~'platelet', 'counts'~'count'."""
    if len(t) > 4 and t.endswith("ies"):
        return t[:-3] + "y"
    if len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
        return t[:-1]
    return t


def normalise(text: str | None, expand: bool = True) -> str:
    return " ".join(t for t in tokens(text, expand) if t not in STOPWORDS)


# Connectors dropped from search queries (word-AND search: "with" fails on "due to").
_QUERY_DROP = {"with", "and", "&"}
# Context words that describe the hint itself rather than the concept.
_CONTEXT_NOISE = {"specimen", "unit", "units", "table", "column", "value", "values", "code",
                  "codes", "test", "domain", "source", "field", "lookup", "dictionary", "type"}


def context_terms(context: str | None, limit: int = 2) -> list[str]:
    """Meaningful words from a context hint ('specimen EDTA blood, units g/dL' -> ['edta', 'blood'])."""
    out: list[str] = []
    for t in tokens(context):
        if (len(t) >= 4 and t.isalpha() and t not in STOPWORDS and t not in _CONTEXT_NOISE
                and t not in out):
            out.append(t)
    return out[:limit]


def build_queries(texts: list[str], max_queries: int = 4, context: str | None = None) -> list[str]:
    """Search strings for find-code, best first, de-duplicated.

    Primary text: expanded form (connectors dropped), then — because find-code is an
    unranked top-20 — the same refined with each context word ("hemoglobin blood"),
    then the raw form and a 3-word short form. Synonyms follow with their expanded form.
    """
    def expanded(t: str) -> str:
        return " ".join(w for w in normalise(t).split() if w not in _QUERY_DROP)

    out: list[str] = []

    def add(q: str) -> None:
        q = q.strip()
        if len(q) >= 2 and q not in out:
            out.append(q)

    texts = [t for t in texts if t]
    if texts:
        ctx = context_terms(context)
        # Terminology-style rewrites first ("leukocytes count"), then the label as written.
        for primary in [*phrase_variants(texts[0]), texts[0]]:
            first = expanded(primary)
            add(first)
            for c in ctx:
                if c not in first.split():
                    add(f"{first} {c}")
        add(" ".join(tokens(texts[0], expand=False)))
        add(" ".join(expanded(texts[0]).split()[:3]))
    for t in texts[1:]:
        add(expanded(t))
    return out[:max_queries]


def score_candidate(source_texts: list[str], candidate_display: str | None,
                    context: str | None = None) -> float:
    """Similarity in [0, 1] between source text(s) and a candidate display.

    Weighted mix of token recall (how much of the source the candidate covers),
    token precision (how little extra the candidate adds) and character-level
    ratio, best over all source texts, plus a small bonus for words from the
    context (specimen, units, column name...) that also appear in the candidate.
    """
    if not candidate_display:
        return 0.0
    cand = content_tokens(candidate_display)
    # LOINC-style axes in brackets ("[Mass/volume]") should not count as "extra" words.
    core = content_tokens(re.sub(r"\[[^\]]*\]", " ", candidate_display)) or cand
    cand_norm = normalise(candidate_display)
    best = 0.0
    for i, text in enumerate(source_texts):
        src = content_tokens(text)
        if not src or not cand:
            continue
        recall = len(src & cand) / len(src)
        precision = len(src & core) / len(core)
        ratio = SequenceMatcher(None, normalise(text), cand_norm).ratio()
        s = 0.5 * recall + 0.2 * precision + 0.3 * ratio
        if normalise(text) == cand_norm:
            s = 1.0
        if i > 0:  # synonyms / secondary labels count a bit less than the primary label
            s *= 0.92
        best = max(best, s)
    if context and best > 0:
        own = set().union(*(content_tokens(t) for t in source_texts))
        ctx = {_stem(t) for t in context_terms(context, limit=6)} - own
        if ctx:
            best = min(1.0, best + min(0.15, 0.07 * len(ctx & cand)))
    return round(best, 3)
