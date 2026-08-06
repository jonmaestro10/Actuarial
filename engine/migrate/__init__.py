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

__all__ = [
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
