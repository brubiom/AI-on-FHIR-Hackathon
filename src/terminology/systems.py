"""Code-system resolution for messy provider data.

Source systems name their code systems in every possible way: canonical FHIR URIs
(`http://loinc.org`), HL7 v2 coding-system ids (`LN`, `SCT`, `I10`, `HL70001`),
OIDs (`urn:oid:2.16.840.1.113883.6.1`), spreadsheet headers (`ICD10`, `CPT_HCPCS`,
`LOINC (current)`), or nothing at all — just a code in a column. They also store
standard codes in non-canonical shapes (ICD-10-CM without the dot: `Z3482`).

This module turns all of that into canonical FHIR system URIs:

  resolve_system(hint)        -> canonical URI | None   (alias/OID/URI/column-name)
  is_standard(uri)            -> True if Termbox is expected to know the system
  infer_systems_from_code(c)  -> candidate URIs ranked by how well the code's
                                 shape (and check digit) fits each system
  code_variants(uri, code)    -> spellings to try with lookup-code ("Z3482" -> "Z34.82")

Nothing here calls the network; it is pure, deterministic and cheap.
"""

from __future__ import annotations

import re

SNOMED = "http://snomed.info/sct"
LOINC = "http://loinc.org"
RXNORM = "http://www.nlm.nih.gov/research/umls/rxnorm"
ICD10CM = "http://hl7.org/fhir/sid/icd-10-cm"
ICD10 = "http://hl7.org/fhir/sid/icd-10"
CPT = "http://www.ama-assn.org/go/cpt"
UCUM = "http://unitsofmeasure.org"
CDC_RACE = "urn:oid:2.16.840.1.113883.6.238"

# Short names for prompts / tool schemas / output.
SYSTEM_NAMES = {
    SNOMED: "SNOMED CT",
    LOINC: "LOINC",
    RXNORM: "RxNorm",
    ICD10CM: "ICD-10-CM",
    ICD10: "ICD-10 (WHO)",
    CPT: "CPT",
    UCUM: "UCUM",
    CDC_RACE: "CDC Race & Ethnicity",
}

# Uppercased, punctuation/space-stripped alias -> canonical URI.
_ALIASES = {
    # LOINC
    "LN": LOINC, "LOINC": LOINC, "LNC": LOINC, "LOINCCODE": LOINC,
    # SNOMED CT
    "SCT": SNOMED, "SNM": SNOMED, "SNOMED": SNOMED, "SNOMEDCT": SNOMED, "SNM3": SNOMED,
    "SNOMEDCTUS": SNOMED, "SCTID": SNOMED, "SNOMEDCODE": SNOMED,
    # ICD-10
    "I10": ICD10CM, "I10C": ICD10CM, "ICD10": ICD10CM, "ICD10CM": ICD10CM,
    "ICD10CODE": ICD10CM, "ICD10WHO": ICD10, "ICD10INT": ICD10,
    # RxNorm
    "RXNORM": RXNORM, "RXN": RXNORM, "RXCUI": RXNORM, "RXNORMCODE": RXNORM,
    # CPT
    "CPT": CPT, "C4": CPT, "CPT4": CPT, "CPTHCPCS": CPT, "CPTCODE": CPT,
    # Others
    "UCUM": UCUM, "CDCREC": CDC_RACE,
}

_OIDS = {
    "2.16.840.1.113883.6.1": LOINC,
    "2.16.840.1.113883.6.96": SNOMED,
    "2.16.840.1.113883.6.90": ICD10CM,
    "2.16.840.1.113883.6.3": ICD10,
    "2.16.840.1.113883.6.88": RXNORM,
    "2.16.840.1.113883.6.12": CPT,
    "2.16.840.1.113883.6.8": UCUM,
}

# HL7 v2 "local" coding-system ids: L, 99xxx, LOCAL...
_LOCAL_RE = re.compile(r"^(L|LOCAL|99[A-Z0-9]{1,4})$")

# URI prefixes of code systems Termbox serves (HL7 THO + FHIR core + the big ones).
_STANDARD_PREFIXES = (
    "http://terminology.hl7.org/CodeSystem/",
    "http://hl7.org/fhir/",
    "urn:ietf:bcp:",
    "urn:iso:std:iso:",
)


def resolve_system(hint: str | None) -> str | None:
    """Map any system hint (URI, OID, HL7 v2 id, column header) to a canonical URI.

    Unknown hints that are already URIs are returned as-is (they may be local
    systems like `urn:segh:lis:test`); unknown short names return None.
    """
    if not hint:
        return None
    h = hint.strip()
    if not h:
        return None
    if h.startswith("urn:oid:"):
        return _OIDS.get(h[8:], h)
    if re.fullmatch(r"\d+(\.\d+){3,}", h):  # bare OID
        return _OIDS.get(h, "urn:oid:" + h)
    if "://" in h or h.startswith("urn:"):
        return h.rstrip("/")
    key = re.sub(r"[^A-Z0-9]", "", h.upper())
    # Spreadsheet headers like "LOINC (current)" / "ICD10 Code": try the leading word too.
    if key in _ALIASES:
        return _ALIASES[key]
    m = re.fullmatch(r"HL7(\d{4})", key)
    if m:
        return f"http://terminology.hl7.org/CodeSystem/v2-{m.group(1)}"
    first = re.sub(r"[^A-Z0-9]", "", re.split(r"[\s(_\-/]+", h.upper())[0])
    return _ALIASES.get(first)


def is_local_hint(hint: str | None) -> bool:
    """True for HL7 v2 local coding-system ids (`L`, `99LAB`, `LOCAL`)."""
    return bool(hint) and bool(_LOCAL_RE.match(re.sub(r"[^A-Z0-9]", "", hint.upper())))


def is_standard(uri: str | None) -> bool:
    """True when the system is one Termbox is expected to serve."""
    if not uri:
        return False
    return uri in SYSTEM_NAMES or uri.startswith(_STANDARD_PREFIXES)


def system_name(uri: str) -> str:
    return SYSTEM_NAMES.get(uri) or uri.rsplit("/", 1)[-1]


# ------------------------------------------------------------ code shapes
def _luhn_ok(payload: str, check: str) -> bool:
    """LOINC uses a mod-10 (Luhn) check digit over the part before the dash."""
    total = 0
    for i, ch in enumerate(reversed(payload)):
        d = int(ch)
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return (10 - total % 10) % 10 == int(check)


_VERHOEFF_D = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
    [2, 3, 4, 0, 1, 7, 8, 9, 5, 6], [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
    [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
    [8, 7, 6, 5, 9, 3, 2, 1, 0, 4], [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
]
_VERHOEFF_P = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
    [5, 8, 0, 3, 7, 9, 6, 1, 4, 2], [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
    [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
]


def _verhoeff_ok(num: str) -> bool:
    c = 0
    for i, ch in enumerate(reversed(num)):
        c = _VERHOEFF_D[c][_VERHOEFF_P[i % 8][int(ch)]]
    return c == 0


def looks_like_snomed(code: str) -> bool:
    """SCTID: 6-18 digits, concept partition (00/10), valid Verhoeff check digit."""
    return (bool(re.fullmatch(r"[1-9]\d{5,17}", code))
            and code[-3:-1] in {"00", "10"} and _verhoeff_ok(code))


def looks_like_loinc(code: str) -> bool:
    m = re.fullmatch(r"(LP)?(\d{1,7})-(\d)", code)
    return bool(m) and _luhn_ok(m.group(2), m.group(3))


_ICD10_RE = re.compile(r"[A-TV-Z]\d[0-9A-Z](\.?[0-9A-Z]{1,4})?")


def infer_systems_from_code(code: str | None) -> list[str]:
    """Candidate systems for a bare code, most likely first. Empty -> looks local.

    Check digits (LOINC Luhn, SNOMED Verhoeff) make LOINC/SNOMED guesses strong;
    ICD-10/CPT/RxNorm guesses are shape-only and must be confirmed by lookup.
    """
    if not code:
        return []
    c = code.strip().upper()
    out: list[str] = []
    if looks_like_loinc(c):
        out.append(LOINC)
    if looks_like_snomed(c):
        out.append(SNOMED)
    if _ICD10_RE.fullmatch(c) and (len(c) > 3 or c[1:3].isdigit()):
        out += [ICD10CM, ICD10]
    if re.fullmatch(r"\d{4}[0-9FTU]", c):
        out.append(CPT)
    if re.fullmatch(r"[1-9]\d{0,7}", c) and SNOMED not in out:
        out.append(RXNORM)
    return out


def code_variants(system: str, code: str) -> list[str]:
    """Spellings of `code` worth trying with lookup-code, original first."""
    c = code.strip()
    out = [c]
    if system in (ICD10CM, ICD10):
        u = c.upper().replace(" ", "")
        if "." not in u and len(u) > 3:
            out.append(u[:3] + "." + u[3:])
        elif "." in u:
            out.append(u.replace(".", ""))
        if u != c:
            out.insert(1, u)
    elif system == LOINC and re.fullmatch(r"\d{2,8}", c):
        out.append(c[:-1] + "-" + c[-1])  # "67902" -> "6790-2"
    seen: set[str] = set()
    return [v for v in out if not (v in seen or seen.add(v))]
