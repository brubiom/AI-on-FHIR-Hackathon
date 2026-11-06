"""Anthropic tool definitions for concept-mapping suggestions (plug into any agent loop).

Usage in an agent loop (same pattern as src/guide/tools.py):

    from src.terminology.tools import TERMINOLOGY_TOOL_DEFINITIONS, TerminologyTools
    term = TerminologyTools()
    tools = GUIDE_TOOL_DEFINITIONS + TERMINOLOGY_TOOL_DEFINITIONS
    ...
    if term.handles(block.name):
        result_json = term.dispatch(block.name, block.input)

The tool returns verified codes only (everything comes from Termbox), so the
agent can cite them instead of recalling codes from memory.
"""

from __future__ import annotations

import json

from src.terminology.client import TermboxClient
from src.terminology.suggest import suggest_concepts

_CONCEPT_SCHEMA = {
    "type": "object",
    "properties": {
        "code": {"type": ["string", "null"], "description": "Source code, local or standard (e.g. 'HGB', '718-7', 'Z3482')."},
        "system": {"type": ["string", "null"], "description": "Source code system if known: URI, OID, HL7 v2 id ('LN','SCT','I10','L') or name ('LOINC'). Null if unknown."},
        "display": {"type": ["string", "null"], "description": "Source label / free text (e.g. 'Hemoglobin', 'CBC W/O DIFF')."},
        "synonyms": {"type": "array", "items": {"type": "string"}, "description": "Alternative labels from the source (may be empty)."},
        "context": {"type": ["string", "null"], "description": "Hints that disambiguate: specimen, units, table/column name, domain (e.g. 'lab test, specimen EDTA blood, units g/dL')."},
    },
    "required": ["code", "system", "display", "synonyms", "context"],
    "additionalProperties": False,
}

TERMINOLOGY_TOOL_DEFINITIONS = [
    {
        "name": "sugerir_conceptos",
        "description": (
            "Suggest up to 3 standard terminology concepts (SNOMED CT, LOINC, RxNorm, ICD-10-CM, "
            "CPT, HL7 code systems...) for ONE source concept, using the Termbox terminology "
            "server. Works with local made-up codes (searches by label), standard codes "
            "(verifies them, flags wrong/inactive maps) and free text. Returns ranked "
            "suggestions with score (0-1), match_type (exact-code | code-variant | "
            "inferred-system | text-search), a ready FHIR coding, plus warnings. Use it for "
            "every code/lookup value you need to map; never invent codes."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "concept": _CONCEPT_SCHEMA,
                "target_systems": {
                    "type": ["array", "null"], "items": {"type": "string"},
                    "description": "Systems to suggest in (URIs or names like 'LOINC', 'SNOMED', 'RxNorm', 'ICD-10-CM', 'http://terminology.hl7.org/CodeSystem/v3-MaritalStatus'). Null = source's own standard system, else SNOMED CT + LOINC.",
                },
                "max_suggestions": {"type": ["integer", "null"], "description": "1-3, default 3."},
            },
            "required": ["concept", "target_systems", "max_suggestions"],
            "additionalProperties": False,
        },
    },
]


class TerminologyTools:
    """Dispatches terminology tool calls; JSON in, JSON out, errors as JSON."""

    names = {t["name"] for t in TERMINOLOGY_TOOL_DEFINITIONS}

    def __init__(self, client: TermboxClient | None = None):
        self.client = client or TermboxClient()

    def handles(self, name: str) -> bool:
        return name in self.names

    def dispatch(self, name: str, args: dict) -> str:
        try:
            if name != "sugerir_conceptos":
                raise ValueError(f"unknown tool: {name}")
            res = suggest_concepts(
                args.get("concept") or {},
                target_systems=args.get("target_systems"),
                max_suggestions=min(args.get("max_suggestions") or 3, 3),
                client=self.client,
            )
            return json.dumps(res.to_dict(), ensure_ascii=False)
        except Exception as exc:
            return json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False)
