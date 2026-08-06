"""Parity: reconciling this engine against somebody else's numbers.

RFC-033. :mod:`engine.parity.diff` holds the comparison — the tolerance
policy, the external table, the alignment and the report;
:mod:`engine.parity.report` renders that report as Markdown and records it,
content-addressed, in the artifact registry.
"""

from engine.parity.diff import (
    DEFAULT_TOLERANCE,
    CellDeviation,
    ExternalTable,
    ParityError,
    ParityReport,
    ParitySpec,
    StatisticalTolerance,
    Tolerance,
    TolerancePolicy,
    VariableParity,
    diff,
)
from engine.parity.report import parity_artifact, record_parity, render_markdown

__all__ = [
    "DEFAULT_TOLERANCE",
    "CellDeviation",
    "ExternalTable",
    "ParityError",
    "ParityReport",
    "ParitySpec",
    "StatisticalTolerance",
    "Tolerance",
    "TolerancePolicy",
    "VariableParity",
    "diff",
    "parity_artifact",
    "record_parity",
    "render_markdown",
]
