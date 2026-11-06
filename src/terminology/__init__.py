"""Terminology integration: concept-mapping suggestions backed by the Termbox MCP server.

    from src.terminology import suggest_concepts
    suggest_concepts({"code": "HGB", "display": "Hemoglobin", "system": "L"},
                     target_systems=["LOINC"]).to_dict()

See README.md ("Terminology integration") for the contract.
"""

from src.terminology.client import TermboxClient, TermboxConcept, TermboxError
from src.terminology.suggest import (
    ConceptInput,
    Suggestion,
    SuggestionResult,
    coerce_input,
    suggest_concepts,
    suggest_many,
)

__all__ = [
    "ConceptInput", "Suggestion", "SuggestionResult", "TermboxClient", "TermboxConcept",
    "TermboxError", "coerce_input", "suggest_concepts", "suggest_many",
]
