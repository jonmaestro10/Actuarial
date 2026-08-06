"""The parity core: aligning two sets of numbers and saying where they differ.

RFC-033. ``scripts/vpla_parity.py`` reconciled one engine object against one
incumbent's code; this generalises that into a diff engine any migration can
point at — an engine result on one side, an incumbent's results table on the
other, an explicit mapping between them, and a tolerance policy that says
what "agrees" means for each variable.

Three rules shape the module, and each of them is a refusal:

**Nothing is mapped by guessing.** ``ParitySpec.mapping`` goes external
column → engine variable, written by a human. A reconciliation whose column
matching was inferred is a reconciliation that can agree for the wrong
reason. Columns nobody mapped are *reported* — they appear in the report as
unmapped, because a column silently dropped is a column nobody reconciled
and nobody knows it.

**Rows the engine cannot answer for are failures, not omissions.** An
external row whose ``(model point, t)`` has no engine cell does not quietly
vanish from the denominator; it is counted, listed, and it makes the report
not-ok. Coverage in the other direction — engine cells the external file
says nothing about — is reported as a fraction rather than an error,
because an incumbent extract legitimately covers a subset of the horizon.

**Non-finite values never round to agreement.** A NaN on either side
compares false against every bound, so it is counted as a difference and
excluded from the max-deviation statistics rather than poisoning them.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from engine.core.fingerprint import fingerprint


class ParityError(ValueError):
    """A reconciliation that cannot be set up, stated rather than fudged.

    Raised for the mistakes that would otherwise produce a report which
    looks like agreement: a mapping naming a variable the run does not
    carry, a key column missing from the external table, a column of text
    where numbers were promised.
    """


# --------------------------------------------------------------------------
# Tolerance
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Tolerance:
    """A deterministic agreement bound.

    ``|engine - external| <= absolute + relative * max(|engine|, |external|)``

    The default is relative ``1e-10`` and absolute zero: two implementations
    of the same deterministic formula, in float64, agree to about that. It is
    deliberately far tighter than an actuarial materiality threshold —
    materiality is a judgement about a *result*, and this is a check on an
    *implementation*. Loosening it is a per-variable decision somebody has to
    write down (:class:`TolerancePolicy`), not a default anybody inherits.
    """

    absolute: float = 0.0
    relative: float = 1e-10

    def accepts(self, engine, external) -> np.ndarray:
        """Elementwise: is each pair within the bound?"""
        engine = np.asarray(engine, dtype=float)
        external = np.asarray(external, dtype=float)
        bound = self.absolute + self.relative * np.maximum(
            np.abs(engine), np.abs(external)
        )
        return np.abs(engine - external) <= bound

    def describe(self) -> str:
        return (f"|Δ| ≤ {self.absolute:g} + {self.relative:g}·"
                f"max(|engine|, |external|)")

    def __fingerprint__(self):
        return {"kind": "deterministic", "absolute": self.absolute,
                "relative": self.relative}


@dataclass(frozen=True)
class StatisticalTolerance:
    """An agreement bound for a Monte Carlo output.

    Two stochastic implementations of the same model do not agree to 1e-10
    unless they share scenarios *and* op order; what they can be held to is
    agreement within sampling error. ``standard_error`` is the standard error
    of the quantity being compared — the caller's, because only the caller
    knows how many scenarios produced it — and ``multiple`` is how many of
    them a difference may span before it stops being sampling noise.

    Stating the standard error rather than deriving one is the point: a
    tolerance nobody can reconstruct is a tolerance nobody can dispute.
    """

    standard_error: float
    multiple: float = 3.0

    def accepts(self, engine, external) -> np.ndarray:
        engine = np.asarray(engine, dtype=float)
        external = np.asarray(external, dtype=float)
        return np.abs(engine - external) <= self.multiple * self.standard_error

    def describe(self) -> str:
        return (f"|Δ| ≤ {self.multiple:g}× standard error "
                f"({self.standard_error:g})")

    def __fingerprint__(self):
        return {"kind": "statistical", "standard_error": self.standard_error,
                "multiple": self.multiple}


DEFAULT_TOLERANCE = Tolerance()


@dataclass(frozen=True)
class TolerancePolicy:
    """The tolerance to judge each variable by.

    A single default with per-variable overrides, rather than one number for
    the run: a reconciliation typically holds cashflows to float agreement
    and a stochastic reserve to sampling error, and collapsing the two would
    mean either accepting a wrong cashflow or rejecting a right reserve.
    """

    default: Tolerance | StatisticalTolerance = DEFAULT_TOLERANCE
    per_variable: Mapping[str, Tolerance | StatisticalTolerance] = field(
        default_factory=dict
    )

    def for_variable(self, name: str) -> Tolerance | StatisticalTolerance:
        return self.per_variable.get(name, self.default)

    def __fingerprint__(self):
        return {"default": self.default,
                "per_variable": dict(self.per_variable)}


# --------------------------------------------------------------------------
# The external side
# --------------------------------------------------------------------------

def _infer_column(values: Sequence[str], missing: Sequence[str]) -> list:
    """Text from a delimited file, given the narrowest type that holds it.

    Integers stay integers so an identifier column keeps comparing equal to
    an integer model point id; anything that is not wholly numeric stays
    text. Missing markers become ``None`` rather than zero — a blank cell is
    not a number and a reconciliation that reads it as one is worse than one
    that refuses.
    """
    cleaned = [None if v.strip() in missing else v.strip() for v in values]
    present = [v for v in cleaned if v is not None]
    for cast in (int, float):
        try:
            typed = [cast(v) for v in present]
        except ValueError:
            continue
        out, i = [], 0
        for v in cleaned:
            if v is None:
                out.append(None)
            else:
                out.append(typed[i])
                i += 1
        return out
    return cleaned


@dataclass(frozen=True)
class ExternalTable:
    """A columnar table of somebody else's results.

    Content-addressed by its columns alone: the same extract read from two
    paths is the same evidence, and a report that digested the path would
    claim otherwise. ``source`` is carried for the report's benefit and is
    deliberately outside the digest.
    """

    columns: Mapping[str, Sequence[Any]]
    source: str | None = None

    def __post_init__(self):
        if not self.columns:
            raise ParityError("external table has no columns")
        lengths = {name: len(values) for name, values in self.columns.items()}
        if len(set(lengths.values())) != 1:
            raise ParityError(f"external columns have unequal lengths: {lengths}")

    @property
    def n_rows(self) -> int:
        return len(next(iter(self.columns.values())))

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self.columns)

    def column(self, name: str) -> Sequence[Any]:
        if name not in self.columns:
            raise ParityError(
                f"external table has no column {name!r}; it has "
                f"{sorted(self.columns)}"
            )
        return self.columns[name]

    @property
    def digest(self) -> str:
        """Digest of the content, so a reconciliation names its evidence."""
        return fingerprint({name: list(values)
                            for name, values in self.columns.items()})

    def __fingerprint__(self):
        return {name: list(values) for name, values in self.columns.items()}

    @classmethod
    def from_rows(cls, rows: Iterable[Mapping[str, Any]], *,
                  source: str | None = None) -> "ExternalTable":
        """Row dicts to columns, refusing a ragged set of keys."""
        rows = [dict(row) for row in rows]
        if not rows:
            raise ParityError("external table has no rows")
        names = list(rows[0])
        for i, row in enumerate(rows):
            if list(row) != names:
                raise ParityError(
                    f"external row {i} has fields {sorted(row)}, first row "
                    f"has {sorted(names)}"
                )
        return cls({name: [row[name] for row in rows] for name in names},
                   source=source)

    @classmethod
    def read_csv(cls, path, *, delimiter: str = ",",
                 missing: Sequence[str] = ("", "NA", "NULL")) -> "ExternalTable":
        """Read a delimited results extract with no third-party dependency.

        The plain-CSV path is the one that always works; :meth:`read_parquet`
        is the same table behind the ``[data]`` extra.
        """
        with open(path, newline="", encoding="utf-8-sig") as handle:
            reader = csv.reader(handle, delimiter=delimiter)
            try:
                header = next(reader)
            except StopIteration:
                raise ParityError(f"{path}: empty file") from None
            raw: list[list[str]] = [[] for _ in header]
            for line_no, row in enumerate(reader, start=2):
                if not row:
                    continue
                if len(row) != len(header):
                    raise ParityError(
                        f"{path}:{line_no}: {len(row)} fields, header has "
                        f"{len(header)}"
                    )
                for i, value in enumerate(row):
                    raw[i].append(value)
        columns = {name.strip(): _infer_column(values, missing)
                   for name, values in zip(header, raw)}
        return cls(columns, source=str(path))

    @classmethod
    def read_parquet(cls, path) -> "ExternalTable":
        """Read a results extract via pyarrow (the ``[data]`` extra)."""
        import pyarrow.parquet as pq

        table = pq.read_table(path)
        return cls({name: table.column(name).to_pylist()
                    for name in table.column_names}, source=str(path))


# --------------------------------------------------------------------------
# The specification
# --------------------------------------------------------------------------

def _engine_arrays(result, variables: Sequence[str]) -> dict[str, np.ndarray]:
    """``(t, model point)`` float arrays out of whatever the executor returned."""
    arrays = {}
    for name in variables:
        if hasattr(result, "array"):
            try:
                raw = result.array(name)
            except KeyError:
                raise ParityError(
                    f"the run carries no variable {name!r}"
                ) from None
        else:
            try:
                raw = np.array([mp[name] for mp in result.per_mp]).T
            except KeyError:
                raise ParityError(
                    f"the run carries no variable {name!r}"
                ) from None
        array = np.asarray(raw, dtype=float)
        if array.ndim != 2:
            raise ParityError(
                f"{name!r} is {array.ndim}-dimensional; a parity spec needs a "
                f"(time, model point) view — reduce a stochastic result to "
                f"one first"
            )
        arrays[name] = array
    return arrays


@dataclass(frozen=True)
class ParitySpec:
    """One reconciliation: what to compare, against what, how closely.

    ``engine`` is one ``(n_steps, n_modelpoints)`` array per variable,
    ``mp_ids`` names its columns, and the external table's ``id_column`` and
    ``time_column`` locate each of its rows in that grid. ``time_offset`` is
    added to the external ``t`` to reach the engine row, for the common case
    of an extract that calls the first projected period 1 where the engine
    calls it 0 — stated in the spec, and therefore in the report, rather
    than applied by a script nobody reads.
    """

    engine: Mapping[str, np.ndarray]
    mp_ids: Sequence[Any]
    external: ExternalTable
    mapping: Mapping[str, str]
    id_column: str = "modelpoint_id"
    time_column: str = "t"
    tolerance: TolerancePolicy = field(default_factory=TolerancePolicy)
    time_offset: int = 0
    label: str | None = None

    def __post_init__(self):
        if not self.mapping:
            raise ParityError("a parity spec needs at least one mapped column")
        for column, variable in self.mapping.items():
            self.external.column(column)
            if variable not in self.engine:
                raise ParityError(
                    f"column {column!r} maps to {variable!r}, which the run "
                    f"does not carry; it carries {sorted(self.engine)}"
                )
        self.external.column(self.id_column)
        self.external.column(self.time_column)
        shapes = {name: arr.shape for name, arr in self.engine.items()}
        if len(set(shapes.values())) != 1:
            raise ParityError(f"engine arrays disagree on shape: {shapes}")
        n_mp = next(iter(shapes.values()))[1]
        if n_mp != len(self.mp_ids):
            raise ParityError(
                f"engine arrays have {n_mp} model-point columns but "
                f"{len(self.mp_ids)} ids were given"
            )

    @property
    def n_steps(self) -> int:
        return next(iter(self.engine.values())).shape[0]

    @property
    def unmapped_columns(self) -> tuple[str, ...]:
        """External columns nobody mapped — reported, never dropped silently."""
        keys = {self.id_column, self.time_column}
        return tuple(name for name in self.external.names
                     if name not in keys and name not in self.mapping)

    def __fingerprint__(self):
        return {
            "engine": {name: np.asarray(arr)
                       for name, arr in self.engine.items()},
            "mp_ids": list(self.mp_ids),
            "external": self.external.digest,
            "mapping": dict(self.mapping),
            "id_column": self.id_column,
            "time_column": self.time_column,
            "tolerance": self.tolerance,
            "time_offset": self.time_offset,
        }

    @classmethod
    def from_results(cls, result, external: ExternalTable,
                     mapping: Mapping[str, str], **options) -> "ParitySpec":
        """Build a spec from a run result and an external table."""
        arrays = _engine_arrays(result, sorted(set(mapping.values())))
        return cls(arrays, list(result.mp_ids), external, dict(mapping),
                   **options)

    @classmethod
    def from_arrays(cls, arrays: Mapping[str, np.ndarray], mp_ids: Sequence[Any],
                    external: ExternalTable, mapping: Mapping[str, str],
                    **options) -> "ParitySpec":
        """Build a spec from bare ``(t, model point)`` arrays.

        The entry point for reconciliations whose engine side is not a run —
        a mortality basis compared rate by rate, say.
        """
        return cls({name: np.asarray(arr, dtype=float)
                    for name, arr in arrays.items()},
                   list(mp_ids), external, dict(mapping), **options)


# --------------------------------------------------------------------------
# The report
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class CellDeviation:
    """One disagreeing cell, named well enough to go and look at it."""

    variable: str
    column: str
    modelpoint: Any
    t: int
    engine: float
    external: float
    absolute: float
    relative: float
    within: bool

    def __fingerprint__(self):
        return {"variable": self.variable, "column": self.column,
                "modelpoint": str(self.modelpoint), "t": self.t,
                "engine": self.engine, "external": self.external,
                "within": self.within}


@dataclass(frozen=True)
class VariableParity:
    """How one variable came out.

    ``n_compared`` counts cells actually held against the tolerance;
    ``n_within`` how many passed. ``max_absolute`` / ``max_relative`` carry
    their own location, because the size of the worst deviation is a number
    and the *reason* for it is a model point.
    """

    variable: str
    column: str
    tolerance: Tolerance | StatisticalTolerance
    n_compared: int
    n_within: int
    n_nonfinite: int
    max_absolute: float
    max_relative: float
    worst_modelpoint: Any
    worst_t: int | None
    worst_engine: float
    worst_external: float
    deviations: tuple[CellDeviation, ...] = ()

    @property
    def n_outside(self) -> int:
        return self.n_compared - self.n_within

    @property
    def ok(self) -> bool:
        return self.n_outside == 0

    def __fingerprint__(self):
        return {
            "variable": self.variable, "column": self.column,
            "tolerance": self.tolerance, "n_compared": self.n_compared,
            "n_within": self.n_within, "n_nonfinite": self.n_nonfinite,
            "max_absolute": self.max_absolute,
            "max_relative": self.max_relative,
            "worst_modelpoint": str(self.worst_modelpoint),
            "worst_t": self.worst_t,
            "deviations": list(self.deviations),
        }


@dataclass(frozen=True)
class ParityReport:
    """The deliverable: what was compared, what agreed, what did not.

    ``ok`` is the whole claim in one boolean, and it is deliberately strict —
    every mapped cell within tolerance *and* every external row matched to an
    engine cell. Coverage the other way (``coverage``) is a number rather
    than a verdict.
    """

    variables: tuple[VariableParity, ...]
    external_digest: str
    external_source: str | None
    spec_digest: str
    mapping: Mapping[str, str]
    unmapped_columns: tuple[str, ...]
    n_external_rows: int
    n_matched_rows: int
    unmatched_rows: tuple[dict, ...]
    n_engine_cells: int
    n_covered_cells: int
    label: str | None = None
    results_digest: str | None = None

    @property
    def ok(self) -> bool:
        return (all(v.ok for v in self.variables)
                and self.n_matched_rows == self.n_external_rows)

    @property
    def n_unmatched_rows(self) -> int:
        return self.n_external_rows - self.n_matched_rows

    @property
    def coverage(self) -> float:
        """Share of engine cells the external extract had something to say about."""
        if self.n_engine_cells == 0:
            return 0.0
        return self.n_covered_cells / self.n_engine_cells

    @property
    def max_absolute(self) -> float:
        return max((v.max_absolute for v in self.variables), default=0.0)

    @property
    def max_relative(self) -> float:
        return max((v.max_relative for v in self.variables), default=0.0)

    def variable(self, name: str) -> VariableParity:
        for entry in self.variables:
            if entry.variable == name:
                return entry
        raise KeyError(name)

    def cells(self, name: str) -> tuple[CellDeviation, ...]:
        """Drill-down: the worst individual cells for one variable."""
        return self.variable(name).deviations

    @property
    def digest(self) -> str:
        """Content digest of the report itself."""
        return fingerprint(self.__fingerprint__())

    def __fingerprint__(self):
        return {
            "variables": list(self.variables),
            "external_digest": self.external_digest,
            "spec_digest": self.spec_digest,
            "mapping": dict(self.mapping),
            "unmapped_columns": list(self.unmapped_columns),
            "n_external_rows": self.n_external_rows,
            "n_matched_rows": self.n_matched_rows,
            "unmatched_rows": [dict(row) for row in self.unmatched_rows],
            "n_engine_cells": self.n_engine_cells,
            "n_covered_cells": self.n_covered_cells,
            "label": self.label,
            "results_digest": self.results_digest,
        }

    def to_markdown(self) -> str:
        """Render the pilot deliverable."""
        from engine.parity.report import render_markdown

        return render_markdown(self)


def _index_of(values: Sequence[Any]) -> dict:
    """First occurrence of each id, so a duplicated id resolves stably."""
    index = {}
    for i, value in enumerate(values):
        key = value.item() if isinstance(value, np.generic) else value
        index.setdefault(key, i)
    return index


def diff(spec: ParitySpec, *, max_cells: int = 10,
         results_digest: str | None = None) -> ParityReport:
    """Compare an engine result with an external results table.

    Alignment is by ``(model point id, t)`` and by nothing else: no
    positional assumption, so a table in a different row order reconciles
    identically. Rows whose key is not in the engine grid are collected and
    reported; ``max_cells`` bounds the per-variable drill-down list, which is
    the worst cells by absolute deviation, ties broken by position.
    """
    ids = list(spec.external.column(spec.id_column))
    times = list(spec.external.column(spec.time_column))
    index = _index_of(spec.mp_ids)
    n_steps = spec.n_steps

    col_index = np.full(len(ids), -1, dtype=np.int64)
    row_index = np.full(len(ids), -1, dtype=np.int64)
    unmatched: list[dict] = []
    for row, (mp_id, t) in enumerate(zip(ids, times)):
        key = mp_id.item() if isinstance(mp_id, np.generic) else mp_id
        column = index.get(key, -1)
        step = None
        if isinstance(t, (int, np.integer)) and not isinstance(t, bool):
            step = int(t) + spec.time_offset
        if column < 0 or step is None or not 0 <= step < n_steps:
            unmatched.append({
                "row": row, spec.id_column: mp_id, spec.time_column: t,
                "reason": ("no such model point in the run" if column < 0
                           else f"t outside the projection (0..{n_steps - 1})"),
            })
            continue
        col_index[row] = column
        row_index[row] = step

    matched = col_index >= 0
    n_matched = int(matched.sum())
    covered = set(zip(row_index[matched].tolist(), col_index[matched].tolist()))

    variables: list[VariableParity] = []
    for column, name in sorted(spec.mapping.items(), key=lambda kv: kv[1]):
        values = spec.external.column(column)
        try:
            external = np.asarray([np.nan if v is None else v for v in values],
                                  dtype=float)
        except (TypeError, ValueError):
            raise ParityError(
                f"column {column!r} is not numeric; a reconciliation cannot "
                f"compare it with {name!r}"
            ) from None
        tolerance = spec.tolerance.for_variable(name)
        array = spec.engine[name]
        engine = np.full(len(values), np.nan)
        engine[matched] = array[row_index[matched], col_index[matched]]
        external_matched = external[matched]
        engine_matched = engine[matched]

        within = np.zeros(n_matched, dtype=bool)
        if n_matched:
            within = tolerance.accepts(engine_matched, external_matched)
        absolute = np.abs(engine_matched - external_matched)
        denominator = np.maximum(np.abs(engine_matched),
                                 np.abs(external_matched))
        with np.errstate(divide="ignore", invalid="ignore"):
            relative = np.where(denominator > 0.0, absolute / denominator, 0.0)
        finite = np.isfinite(absolute)
        n_nonfinite = int(n_matched - finite.sum())

        max_abs = float(absolute[finite].max()) if finite.any() else 0.0
        max_rel = float(relative[finite].max()) if finite.any() else 0.0
        # A variable that agrees everywhere has no worst cell, and naming one
        # anyway would make the report depend on the order the rows arrived
        # in — the one thing alignment by key exists to remove.
        worst_mp, worst_t = None, None
        worst_engine = worst_external = 0.0
        if finite.any() and max_abs > 0.0:
            positions = np.flatnonzero(matched)[finite]
            worst = positions[int(np.argmax(absolute[finite]))]
            worst_mp = ids[worst]
            worst_t = int(times[worst])
            worst_engine = float(engine[worst])
            worst_external = float(external[worst])

        # Drill-down: the worst cells, non-finite first — a NaN is a
        # difference no magnitude can rank, and hiding it under ten large
        # deviations is exactly the report this module refuses to write.
        positions = np.flatnonzero(matched)
        matched_cols = col_index[positions]
        matched_steps = row_index[positions]
        order = sorted(
            range(n_matched),
            key=lambda i: (np.isfinite(absolute[i]),
                           -absolute[i] if np.isfinite(absolute[i]) else 0.0,
                           int(matched_cols[i]), int(matched_steps[i])),
        )
        deviations = tuple(
            CellDeviation(
                variable=name, column=column, modelpoint=ids[positions[i]],
                t=int(times[positions[i]]),
                engine=float(engine_matched[i]),
                external=float(external_matched[i]),
                absolute=float(absolute[i]), relative=float(relative[i]),
                within=bool(within[i]),
            )
            for i in order[:max_cells]
        )
        variables.append(VariableParity(
            variable=name, column=column, tolerance=tolerance,
            n_compared=n_matched, n_within=int(within.sum()),
            n_nonfinite=n_nonfinite, max_absolute=max_abs,
            max_relative=max_rel, worst_modelpoint=worst_mp, worst_t=worst_t,
            worst_engine=worst_engine, worst_external=worst_external,
            deviations=deviations,
        ))

    n_cells = n_steps * len(spec.mp_ids)
    return ParityReport(
        variables=tuple(variables),
        external_digest=spec.external.digest,
        external_source=spec.external.source,
        spec_digest=fingerprint(spec),
        mapping=dict(spec.mapping),
        unmapped_columns=spec.unmapped_columns,
        n_external_rows=spec.external.n_rows,
        n_matched_rows=n_matched,
        unmatched_rows=tuple(unmatched),
        n_engine_cells=n_cells,
        n_covered_cells=len(covered),
        label=spec.label,
        results_digest=results_digest,
    )
