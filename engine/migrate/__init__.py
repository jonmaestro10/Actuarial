"""Reading an incumbent's model office.

RFC-034 onward. One module per incumbent format, each dialect-driven and
each producing the two things a migration needs: model points the engine can
run, and a results table :mod:`engine.parity` can reconcile against.
"""

from engine.migrate.prophet import (
    DEFAULT_FIELD_MAP,
    MPF_DIALECT,
    RESULTS_DIALECT,
    FieldMapping,
    MappingReport,
    ProphetDialect,
    ProphetFile,
    ProphetFormatError,
    ProphetModelPoints,
    read_modelpoints,
    read_results,
    read_table,
)
from engine.migrate.scaffold import (
    DEFAULT_VARIABLE_ALIASES,
    Scaffold,
    VariableSuggestion,
    library_variables,
    scaffold,
    scaffold_from_results,
    suggest,
)

__all__ = [
    "DEFAULT_VARIABLE_ALIASES",
    "Scaffold",
    "VariableSuggestion",
    "library_variables",
    "scaffold",
    "scaffold_from_results",
    "suggest",
    "DEFAULT_FIELD_MAP",
    "MPF_DIALECT",
    "RESULTS_DIALECT",
    "FieldMapping",
    "MappingReport",
    "ProphetDialect",
    "ProphetFile",
    "ProphetFormatError",
    "ProphetModelPoints",
    "read_modelpoints",
    "read_results",
    "read_table",
]
