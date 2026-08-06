"""Prophet model points and results, read into the engine.

RFC-034. A migration starts with somebody else's data, and the data arrives
as delimited text with a header block: a model point file (MPF) of policies,
and a results extract of the numbers the incumbent produced from them. This
module reads both — the first into :class:`~engine.data.modelpoints.ModelPoint`
objects the engine can run, the second into an
:class:`~engine.parity.diff.ExternalTable` the parity core can reconcile
against.

**Dialect-driven, because estates vary.** No two Prophet installations write
the same file. The delimiter, whether data rows carry a record prefix,
whether the type line is present, what a missing value looks like, how dates
are written — all of it moves. :class:`ProphetDialect` is those choices as
data, with a default matching the layout as publicly documented. The reader
holds no proprietary files and claims no coverage beyond what the fixtures
in ``tests/fixtures/prophet/`` prove; the dialect is the mechanism that
absorbs a real client's variant during a pilot, in a dataclass rather than
in a fork.

**Nothing is renamed by resemblance.** ``DEFAULT_FIELD_MAP`` is a small,
explicit map from Prophet's conventional field names onto the model-point
field catalogue the library actually reads (RFC-032's ``modelpoint_fields``).
Every column ends up in the :class:`MappingReport` as consumed, renamed,
carried or ignored — and a field whose *unit* is not the engine's, like
``DURATION_IF_M`` in months against ``duration_in_force`` in years, is
deliberately absent from the map. It shows up as ignored, in the report,
where a human can see it. A reader that guessed at that would silently
divide a number by twelve or fail to.

**A malformed file raises, naming the line and the column.** A reader that
skips what it cannot parse produces a model office missing policies nobody
knows about.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Mapping, Sequence

from engine.data.modelpoints import ModelPoint
from engine.parity.diff import ExternalTable


class ProphetFormatError(ValueError):
    """A file that does not match the dialect it was read with.

    Always names the file and the line, and the column where there is one:
    the person holding a 400,000-policy extract needs to know which row to
    look at, and "could not parse" is not an answer.
    """


# --------------------------------------------------------------------------
# The dialect
# --------------------------------------------------------------------------

#: Prophet's type codes, as far as they are documented: text, integer,
#: number, date. An unknown code raises rather than defaulting to text —
#: a column silently read as text is a column silently not reconciled.
DEFAULT_TYPE_CODES = {
    "T": "text", "S": "text", "C": "text",
    "I": "integer", "N": "number", "D": "date",
}


@dataclass(frozen=True)
class ProphetDialect:
    """How one estate writes its files.

    ``header_prefix`` is the marker on the line naming the variables (``!``
    in the documented MPF layout); ``None`` means the first non-metadata
    line *is* the header, which is how most results extracts arrive.
    ``record_prefix`` is the marker some estates put in the first field of
    every data row (``*``); it is dropped when present and its absence is
    not an error, because the count check below is the real guard.
    """

    delimiter: str = ","
    comment_prefix: str | None = "#"
    header_prefix: str | None = "!"
    record_prefix: str | None = "*"
    types_keyword: str | None = "VARIABLE_TYPES"
    type_codes: Mapping[str, str] = field(
        default_factory=lambda: dict(DEFAULT_TYPE_CODES)
    )
    missing: tuple[str, ...] = ("", "NA", "NULL", ".")
    date_formats: tuple[str, ...] = ("%d/%m/%Y", "%Y-%m-%d", "%d-%b-%Y")

    def __fingerprint__(self):
        return {
            "delimiter": self.delimiter,
            "comment_prefix": self.comment_prefix,
            "header_prefix": self.header_prefix,
            "record_prefix": self.record_prefix,
            "types_keyword": self.types_keyword,
            "type_codes": dict(self.type_codes),
            "missing": list(self.missing),
            "date_formats": list(self.date_formats),
        }


#: The documented model-point-file layout: metadata lines, a
#: ``VARIABLE_TYPES`` line, a ``!``-prefixed header, ``*``-prefixed records.
MPF_DIALECT = ProphetDialect()

#: A results extract: a plain header line and rows, no prefixes.
RESULTS_DIALECT = ProphetDialect(
    header_prefix=None, record_prefix=None, types_keyword=None,
)


# --------------------------------------------------------------------------
# The file
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class ProphetFile:
    """A parsed Prophet file: typed columns, plus the header block."""

    columns: Mapping[str, Sequence[Any]]
    types: Mapping[str, str]
    metadata: Mapping[str, Any]
    source: str

    @property
    def n_rows(self) -> int:
        return len(next(iter(self.columns.values()))) if self.columns else 0

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self.columns)

    def to_external(self) -> ExternalTable:
        """The same table, as the external side of a parity spec."""
        return ExternalTable({name: list(values)
                              for name, values in self.columns.items()},
                             source=self.source)


def _convert(value: str, kind: str, dialect: ProphetDialect, *,
             where: str) -> Any:
    if value in dialect.missing:
        return None
    if kind == "text":
        return value
    if kind == "integer":
        try:
            return int(value)
        except ValueError:
            # Prophet writes whole numbers into integer columns as `20` but
            # an estate that exported through a spreadsheet writes `20.0`,
            # and refusing that would be pedantry rather than diligence.
            try:
                as_float = float(value)
            except ValueError:
                raise ProphetFormatError(
                    f"{where}: {value!r} is not an integer"
                ) from None
            if as_float != int(as_float):
                raise ProphetFormatError(
                    f"{where}: {value!r} is not an integer"
                ) from None
            return int(as_float)
    if kind == "number":
        try:
            return float(value)
        except ValueError:
            raise ProphetFormatError(
                f"{where}: {value!r} is not a number"
            ) from None
    if kind == "date":
        for fmt in dialect.date_formats:
            try:
                return datetime.strptime(value, fmt).date()
            except ValueError:
                continue
        raise ProphetFormatError(
            f"{where}: {value!r} is not a date in any of "
            f"{list(dialect.date_formats)}"
        )
    raise ProphetFormatError(f"{where}: unknown column type {kind!r}")


def _infer(values: Sequence[str | None]) -> str:
    """The narrowest type a column of text fits, when no type line says.

    Integers stay integers so an identifier column keeps its identity;
    anything not wholly numeric is text.
    """
    present = [v for v in values if v is not None]
    for kind, cast in (("integer", int), ("number", float)):
        try:
            for v in present:
                cast(v)
        except ValueError:
            continue
        return kind
    return "text"


def read_table(path, dialect: ProphetDialect = MPF_DIALECT) -> ProphetFile:
    """Parse a Prophet file into typed columns.

    The header block is read in whatever order it arrives: ``key,value``
    metadata lines, an optional ``VARIABLE_TYPES`` line, and the header. A
    file that reaches its end without a header raises — a "table" with no
    column names is not a table, and inventing positional names would let a
    reconciliation compare the wrong column against the wrong variable.
    """
    with open(path, encoding="utf-8-sig") as handle:
        lines = handle.read().splitlines()

    names: list[str] | None = None
    codes: list[str] | None = None
    metadata: dict[str, Any] = {}
    rows: list[tuple[int, list[str]]] = []

    for line_no, raw in enumerate(lines, start=1):
        line = raw.strip()
        if not line:
            continue
        if dialect.comment_prefix and line.startswith(dialect.comment_prefix):
            continue
        fields = [part.strip() for part in line.split(dialect.delimiter)]

        if names is None:
            if dialect.header_prefix and line.startswith(dialect.header_prefix):
                fields[0] = fields[0][len(dialect.header_prefix):].strip()
                names = fields
                continue
            if (dialect.types_keyword
                    and fields[0].upper() == dialect.types_keyword.upper()):
                codes = fields[1:]
                continue
            if dialect.header_prefix is None:
                names = fields
                continue
            if len(fields) >= 2:
                metadata[fields[0]] = (fields[1] if len(fields) == 2
                                       else fields[1:])
                continue
            raise ProphetFormatError(
                f"{path}:{line_no}: expected a metadata line or a header "
                f"beginning with {dialect.header_prefix!r}, got {line!r}"
            )

        if dialect.record_prefix and fields and fields[0] == dialect.record_prefix:
            fields = fields[1:]
        if len(fields) != len(names):
            raise ProphetFormatError(
                f"{path}:{line_no}: {len(fields)} fields, the header names "
                f"{len(names)} ({', '.join(names)})"
            )
        rows.append((line_no, fields))

    if names is None:
        raise ProphetFormatError(
            f"{path}: no header line"
            + (f" beginning with {dialect.header_prefix!r}"
               if dialect.header_prefix else "")
        )
    if not names or any(not name for name in names):
        raise ProphetFormatError(f"{path}: header has an empty column name")
    if len(set(names)) != len(names):
        duplicated = sorted({n for n in names if names.count(n) > 1})
        raise ProphetFormatError(
            f"{path}: header repeats {duplicated} — a reconciliation cannot "
            f"say which one it compared"
        )
    if codes is not None and len(codes) != len(names):
        raise ProphetFormatError(
            f"{path}: {dialect.types_keyword} gives {len(codes)} types for "
            f"{len(names)} columns"
        )

    raw_columns = {name: [] for name in names}
    for _, fields in rows:
        for name, value in zip(names, fields):
            raw_columns[name].append(value)

    types: dict[str, str] = {}
    if codes is not None:
        for name, code in zip(names, codes):
            if code not in dialect.type_codes:
                raise ProphetFormatError(
                    f"{path}: column {name!r} has type code {code!r}, which "
                    f"the dialect does not define "
                    f"({sorted(dialect.type_codes)})"
                )
            types[name] = dialect.type_codes[code]
    else:
        for name in names:
            cleaned = [None if v in dialect.missing else v
                       for v in raw_columns[name]]
            types[name] = _infer(cleaned)

    columns: dict[str, list] = {name: [] for name in names}
    for (line_no, fields) in rows:
        for name, value in zip(names, fields):
            columns[name].append(_convert(
                value, types[name], dialect,
                where=f"{path}:{line_no}: column {name!r}",
            ))
    return ProphetFile(columns=columns, types=types, metadata=metadata,
                       source=str(path))


# --------------------------------------------------------------------------
# Fields
# --------------------------------------------------------------------------

#: Prophet's conventional model-point field names, mapped onto the fields the
#: library's templates read. Deliberately short and deliberately literal:
#: every entry is a rename of the same quantity in the same unit. Anything
#: needing a conversion, a convention, or a guess is absent — it surfaces in
#: the mapping report as ignored, which is a question a human can answer,
#: rather than as a number that is quietly wrong.
DEFAULT_FIELD_MAP: Mapping[str, str] = {
    "POL_NUMBER": "id",
    "POLICY_NUMBER": "id",
    "MP_ID": "id",
    "AGE_AT_ENTRY": "age_at_entry",
    "ENTRY_AGE": "age_at_entry",
    "SEX": "sex",
    "GENDER": "sex",
    "POL_TERM": "term_years",
    "POLICY_TERM": "term_years",
    "PREM_PAYBL_Y": "premium_years",
    "SUM_ASSURED": "sum_assured",
    "FACE_AMOUNT": "face_amount",
    "ANNUAL_PREM": "annual_premium",
    "ANNUAL_PREMIUM": "annual_premium",
    "SINGLE_PREM": "single_premium",
    "NO_POLS": "init_pols",
    "INIT_POLS_IF": "init_pols",
    "NO_LIVES": "init_lives",
    "DURATION_IF_Y": "duration_in_force",
    "DATE_OF_BIRTH": "dob",
    "DOB": "dob",
    "SPOUSE_DOB": "spouse_dob",
    "SPOUSE_SEX": "spouse_sex",
    "DEFER_YEARS": "defer_years",
    "CERTAIN_YEARS": "certain_years",
    "ACCOUNT_VALUE": "account_value",
    "INIT_AV": "init_av",
}


@functools.lru_cache(maxsize=1)
def catalogue() -> frozenset[str]:
    """Every model-point field the shipped templates read.

    Read off the library rather than curated, through RFC-032's
    ``modelpoint_fields``: a column already carrying an engine field name is
    *consumed* rather than renamed, and the set of names that qualify has to
    be the real one or the mapping report is describing a fiction.
    """
    import importlib
    import inspect
    import pkgutil

    import engine.library as library
    from engine.core.model import Model
    from engine.core.modeldoc import modelpoint_fields

    names: set[str] = {"id"}
    for module_info in pkgutil.iter_modules(library.__path__):
        module = importlib.import_module(f"engine.library.{module_info.name}")
        for cls in vars(module).values():
            if (inspect.isclass(cls) and issubclass(cls, Model)
                    and cls is not Model and cls.__module__ == module.__name__
                    and cls.var_names()):
                fields = modelpoint_fields(cls)
                names.update(fields.required)
                names.update(fields.optional)
    return frozenset(names)


@dataclass(frozen=True)
class FieldMapping:
    """What became of one incumbent field."""

    source: str
    target: str | None
    action: str          # consumed | renamed | carried | ignored
    type: str

    def __fingerprint__(self):
        return {"source": self.source, "target": self.target,
                "action": self.action, "type": self.type}


@dataclass(frozen=True)
class MappingReport:
    """Every incumbent field, and what the reader did with it.

    The same instinct as a template stating what it needs from a model
    point: the useful document is not the list of fields that worked, it is
    the list of fields nobody looked at.
    """

    fields: tuple[FieldMapping, ...]
    source: str | None = None
    n_rows: int = 0

    def of(self, action: str) -> tuple[FieldMapping, ...]:
        return tuple(f for f in self.fields if f.action == action)

    @property
    def ignored(self) -> tuple[str, ...]:
        return tuple(f.source for f in self.fields if f.action == "ignored")

    def to_markdown(self) -> str:
        out = ["# Model point mapping", ""]
        if self.source:
            out += [f"Source: `{self.source}` — {self.n_rows:,} rows.", ""]
        out += ["| incumbent field | engine field | type | |",
                "|---|---|---|---|"]
        for entry in self.fields:
            target = f"`{entry.target}`" if entry.target else "—"
            out.append(f"| `{entry.source}` | {target} | {entry.type} "
                       f"| {entry.action} |")
        if self.ignored:
            out += ["", f"**{len(self.ignored)} field(s) ignored**: "
                    + ", ".join(f"`{name}`" for name in self.ignored)
                    + ". Nothing in the engine reads them; if one of them "
                      "matters, it needs a mapping written down."]
        return "\n".join(out).rstrip() + "\n"

    def __fingerprint__(self):
        return {"fields": list(self.fields), "n_rows": self.n_rows}


@dataclass(frozen=True)
class ProphetModelPoints:
    """Model points read from an MPF, with the mapping that produced them.

    Iterates as its model points, so it can be handed straight to a runner
    or to :func:`~engine.core.registry.record_run` — but carries the mapping
    report alongside, because the two travel together in a pilot.
    """

    modelpoints: tuple[ModelPoint, ...]
    mapping: MappingReport
    file: ProphetFile

    def __iter__(self):
        return iter(self.modelpoints)

    def __len__(self) -> int:
        return len(self.modelpoints)

    def __getitem__(self, index):
        return self.modelpoints[index]


def read_modelpoints(path, dialect: ProphetDialect = MPF_DIALECT, *,
                     mapping: Mapping[str, str | None] | None = None,
                     keep_unmapped: bool = False) -> ProphetModelPoints:
    """Read a Prophet model point file into engine model points.

    ``mapping`` overlays :data:`DEFAULT_FIELD_MAP` — an entry maps an
    incumbent field onto an engine field, and an entry whose value is
    ``None`` drops one of the defaults. ``keep_unmapped`` carries the
    remaining columns onto the model points under their own lower-cased
    names instead of dropping them; either way every column appears in the
    returned :class:`MappingReport`.
    """
    table = read_table(path, dialect)
    overlay = dict(DEFAULT_FIELD_MAP)
    suppressed: set[str] = set()
    for key, value in (mapping or {}).items():
        if value is None:
            # Not merely "drop the default": an explicit None means the
            # caller does not want this column, and it must not come back in
            # through the catalogue fallback below.
            overlay.pop(key.upper(), None)
            suppressed.add(key.upper())
        else:
            overlay[key.upper()] = value

    known = catalogue()
    entries: list[FieldMapping] = []
    targets: dict[str, str] = {}
    for name in table.names:
        kind = table.types[name]
        upper = name.upper()
        if upper in suppressed:
            target, action = None, "ignored"
        elif upper in overlay:
            target = overlay[upper]
            action = "consumed" if target == name.lower() else "renamed"
        elif name.lower() in known:
            target, action = name.lower(), "consumed"
        elif keep_unmapped:
            target, action = name.lower(), "carried"
        else:
            target, action = None, "ignored"
        if target is not None:
            if target in targets:
                raise ProphetFormatError(
                    f"{path}: both {targets[target]!r} and {name!r} map to "
                    f"{target!r}"
                )
            targets[target] = name
        entries.append(FieldMapping(source=name, target=target, action=action,
                                    type=kind))

    kept = [entry for entry in entries if entry.target is not None]
    if not kept:
        raise ProphetFormatError(
            f"{path}: no column maps onto a model point field; the file has "
            f"{list(table.names)}"
        )
    points = tuple(
        ModelPoint(**{entry.target: table.columns[entry.source][row]
                      for entry in kept})
        for row in range(table.n_rows)
    )
    report = MappingReport(fields=tuple(entries), source=str(path),
                           n_rows=table.n_rows)
    return ProphetModelPoints(modelpoints=points, mapping=report, file=table)


def read_results(path, dialect: ProphetDialect = RESULTS_DIALECT, *,
                 rename: Mapping[str, str] | None = None) -> ExternalTable:
    """Read a Prophet results extract as the external side of a parity spec.

    ``rename`` is for the two columns a :class:`~engine.parity.diff.ParitySpec`
    has to find by name — the identifier and the time step. Everything else
    keeps the incumbent's own column names, because the parity mapping is
    written against those and a reader that tidied them would be one more
    place a name could be quietly wrong.
    """
    table = read_table(path, dialect)
    columns = {name: list(values) for name, values in table.columns.items()}
    for source, target in (rename or {}).items():
        if source not in columns:
            raise ProphetFormatError(
                f"{path}: cannot rename {source!r} — the file has "
                f"{list(columns)}"
            )
        if target in columns:
            raise ProphetFormatError(
                f"{path}: cannot rename {source!r} to {target!r}, which the "
                f"file already has"
            )
        columns = {(target if name == source else name): values
                   for name, values in columns.items()}
    return ExternalTable(columns, source=str(path))
