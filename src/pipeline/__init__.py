"""Load pipeline: approved mappings -> FHIR resources -> Aidbox.

Source-agnostic. The engine carries no knowledge of any particular table or
column; it executes whatever `structure_map` (column -> FHIR element) and
`conceptmap` (source code -> standard code) the reviewer approved. Point it at a
different source database, re-run the agent to produce new configs, and the same
pipeline migrates the new data unchanged.

Public API:
    from src.pipeline import run_pipeline, AidboxClient, load_configs
"""

from __future__ import annotations

from .aidbox import AidboxClient, AidboxError
from .engine import build_resources, load_configs
from .run import PipelineResult, run_pipeline

__all__ = [
    "AidboxClient",
    "AidboxError",
    "build_resources",
    "load_configs",
    "run_pipeline",
    "PipelineResult",
]
