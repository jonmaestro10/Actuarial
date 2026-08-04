"""ESG file adapters: reading an economic scenario generator's output.

PLAN.md §6 lists ESG scenario formats alongside model-point files as the
integration surface that makes the engine usable next to an existing
estate, and §8 makes it a Phase 2 exit. Vendors differ in the header block
and the column names, but underneath there are two layouts and this module
reads both:

**Wide** — one row per scenario, one column per period. The usual dump for
a single series::

    Scenario,1,2,3,...
    1,0.0523,-0.0112,0.0781,...

**Long** — a tidy table, which is what a Parquet extract or a warehouse
query gives you, and the only layout that carries several series at once::

    scenario,period,series,value
    1,1,equity,0.0523
    1,1,bond,0.0210

CSV goes through the standard library; Parquet needs ``pyarrow`` (the
``data`` extra), imported lazily, exactly as model points do.

The traps, and what is done about them
--------------------------------------
Parsing an ESG file is easy. Parsing one *correctly* is where the accuracy
pillar gets tested, because every way of getting it wrong produces a
plausible number rather than an error.

**An index is not a return.** Many generators publish a cumulative
total-return index, not a per-period return. Feeding an index to a template
that compounds it is catastrophic and silent. ``kind="index"`` converts, and
converting needs the value at time zero: either the file carries a period-0
column (``starts_at=0``) or ``index_base`` says what it was. With
``kind="index"`` and neither, this module **raises** rather than guessing —
guessing 1.0 when the file was built on 100.0 is a factor of a hundred that
survives every downstream check.

**Column order is period order.** The k-th value column is the return earned
during projection period ``k``, whatever the header calls it. A file whose
first column is a period-0 index of ones, read as returns, silently shifts
the whole projection by one period. ``describe()`` reports a first period
that is identically constant across every scenario, because that is what
that mistake looks like.

**A risk-neutral file should prove it.** :func:`martingale_error` discounts
each scenario's accumulated fund and reports how far the mean is from 1,
next to its Monte Carlo standard error — so a deviation can be judged as
sampling noise or as a broken file, rather than eyeballed against a
tolerance somebody made up.

**A rectangle with a hole is not a scenario set.** The long reader requires
every (scenario, period) pair for every series, and says which are missing.
"""

from __future__ import annotations

import csv
import hashlib
import os
from collections import defaultdict
from typing import Iterable, Sequence

import numpy as np

from engine.data.scenarios import PRIMARY, ScenarioSet

KINDS = ("return", "index")


# --- provenance ----------------------------------------------------------


def file_digest(path) -> str:
    """BLAKE2b of a file's bytes — what was actually read, not what the
    path was called."""
    digest = hashlib.blake2b(digest_size=16)
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _source(path, layout: str, kind: str) -> dict:
    return {
        "path": os.fspath(path),
        "layout": layout,
        "kind": kind,
        "digest": file_digest(path),
    }


# --- index to return -----------------------------------------------------


def returns_from_index(values, *, index_base=None, starts_at: int = 1):
    """Per-period returns from a cumulative index.

    ``values`` is ``(n_scenarios, n_columns)``. With ``starts_at=0`` the
    first column is the time-zero level and the result has one column
    fewer. With ``starts_at=1`` the first column is already period 1, so
    ``index_base`` supplies the missing level.

    There is no default base. A generator that publishes an index on 100.0
    and one that publishes on 1.0 give identical-looking files, and the
    difference is a hundredfold error in every projected fund.
    """
    arr = np.asarray(values, dtype=np.float64)
    if starts_at not in (0, 1):
        raise ValueError(f"starts_at must be 0 or 1, got {starts_at}")
    if starts_at == 0:
        if index_base is not None:
            raise ValueError(
                "index_base is redundant when the file carries a period-0 "
                "column; drop one of them"
            )
        levels = arr
    else:
        if index_base is None:
            raise ValueError(
                "converting an index to returns needs the level at time "
                "zero: pass index_base=..., or starts_at=0 if the file "
                "already carries a period-0 column"
            )
        levels = np.concatenate(
            [np.full((arr.shape[0], 1), float(index_base)), arr], axis=1
        )
    if np.any(levels <= 0.0):
        raise ValueError("a total-return index must be strictly positive")
    return levels[:, 1:] / levels[:, :-1] - 1.0


def _to_returns(values, kind: str, index_base, starts_at: int):
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}, got {kind!r}")
    if kind == "return":
        if index_base is not None:
            raise ValueError("index_base only applies to kind='index'")
        return np.asarray(values, dtype=np.float64)
    return returns_from_index(values, index_base=index_base,
                              starts_at=starts_at)


# --- wide layout ---------------------------------------------------------


def read_wide(path, *, name: str = PRIMARY, kind: str = "return",
              index_base=None, starts_at: int = 1, has_header: bool = True,
              id_columns: int = 1, delimiter: str = ",",
              skip_lines: int = 0) -> ScenarioSet:
    """One row per scenario, one column per period.

    ``id_columns`` leading columns identify the scenario and are dropped;
    pass 0 for a file that is nothing but numbers. ``skip_lines`` discards a
    vendor metadata block ahead of the header.
    """
    rows = _read_delimited(path, delimiter, skip_lines)
    if has_header:
        if not rows:
            raise ValueError(f"{path}: file is empty")
        rows = rows[1:]
    if not rows:
        raise ValueError(f"{path}: no scenario rows")
    widths = {len(row) for row in rows}
    if len(widths) != 1:
        raise ValueError(
            f"{path}: rows have differing column counts {sorted(widths)}"
        )
    values = _floats(
        [row[id_columns:] for row in rows], path, "value"
    )
    if values.shape[1] == 0:
        raise ValueError(f"{path}: no value columns after {id_columns} id column(s)")
    returns = _to_returns(values, kind, index_base, starts_at)
    return ScenarioSet(series={name: returns}, primary=name,
                       source=_source(path, "wide", kind))


# --- long layout ---------------------------------------------------------


def read_long(path, *, scenario_column: str = "scenario",
              period_column: str = "period", value_column: str = "value",
              series_column: str | None = None, primary: str | None = None,
              kind: str = "return", index_base=None, starts_at: int = 1,
              delimiter: str = ",", skip_lines: int = 0) -> ScenarioSet:
    """A tidy table: one row per (scenario, period[, series]).

    With ``series_column`` set, every distinct value in it becomes a series
    and ``primary`` names the one templates compound by (the only series,
    if there is just one). Scenarios and periods are ordered by their
    values, not by their position in the file, so a file sorted differently
    reads to the same set.
    """
    rows = _read_dicts(path, delimiter, skip_lines)
    if not rows:
        raise ValueError(f"{path}: no rows")
    built = _long_from_records(
        rows, str(path), scenario_column=scenario_column,
        period_column=period_column, value_column=value_column,
        series_column=series_column, primary=primary, kind=kind,
        index_base=index_base, starts_at=starts_at,
    )
    built.source = _source(path, "long", kind)
    return built


# --- parquet -------------------------------------------------------------


def read_parquet_long(path, **kwargs) -> ScenarioSet:
    """The long layout out of Parquet — what a warehouse extract looks
    like, and the format an ESG run is usually archived in."""
    import pyarrow.parquet as pq

    rows = pq.read_table(path).to_pylist()
    if not rows:
        raise ValueError(f"{path}: no rows")
    # Parquet gives typed values; stringifying them costs a coercion and
    # buys one implementation of "is this rectangle complete" instead of
    # two, which is the half worth being careful about.
    built = _long_from_records(
        [{k: ("" if v is None else str(v)) for k, v in row.items()}
         for row in rows],
        str(path), **kwargs,
    )
    built.source = _source(path, "long-parquet", kwargs.get("kind", "return"))
    return built


# --- writing -------------------------------------------------------------


def to_wide_csv(scenarios: ScenarioSet, path, *, name: str | None = None,
                delimiter: str = ",") -> None:
    """Write one series in the wide layout.

    Enough to hand a generated set to another tool, and enough to
    round-trip it back through :func:`read_wide` — which is how the reader
    is tested against something other than its own assumptions.
    """
    values = scenarios.series(name or scenarios.primary)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter=delimiter)
        writer.writerow(["scenario", *range(1, values.shape[1] + 1)])
        for s, row in enumerate(values, start=1):
            writer.writerow([s, *(repr(float(v)) for v in row)])


# --- diagnostics ---------------------------------------------------------


def describe(scenarios: ScenarioSet, name: str | None = None) -> dict:
    """Per-series summary, plus the checks a reader cannot make itself.

    ``constant_first_period`` is the one worth looking at: a first period
    identical across every scenario is what reading an index column as a
    return looks like, and no amount of validation inside the parser can
    tell that from a genuinely deterministic first year.
    """
    values = scenarios.series(name or scenarios.primary)
    per_period = values.mean(axis=0)
    return {
        "n_scenarios": int(values.shape[0]),
        "horizon": int(values.shape[1]),
        "mean": float(values.mean()),
        "std": float(values.std(ddof=1)) if values.size > 1 else 0.0,
        "min": float(values.min()),
        "max": float(values.max()),
        "mean_by_period": per_period,
        "constant_first_period": bool(
            values.shape[0] > 1 and np.all(values[:, 0] == values[0, 0])
        ),
    }


def martingale_error(scenarios: ScenarioSet, rate, *, name: str | None = None):
    """How far a risk-neutral set is from pricing its own numeraire.

    Under the risk-neutral measure a unit invested in the fund and
    discounted at the risk-free rate is a martingale, so for every horizon
    ``h``::

        E[ Π_{t<h} (1 + r_t) ] · v_h  =  1

    Returns one record per horizon with the deviation, its Monte Carlo
    standard error, and the ratio of the two. The standard error is the
    point: a set of 1,000 scenarios at 16% vol *will* miss by a percent or
    so at long horizons, and that is sampling noise rather than a defect.
    Judging it needs the error bar, not a tolerance somebody chose.

    ``rate`` is a flat annual effective rate, or a per-period sequence of
    them.
    """
    values = scenarios.series(name or scenarios.primary)
    n, horizon = values.shape
    rates = np.broadcast_to(np.asarray(rate, dtype=np.float64), (horizon,))
    discount = np.cumprod(1.0 / (1.0 + rates))
    accumulated = np.cumprod(1.0 + values, axis=1)
    out = []
    for h in range(horizon):
        deflated = accumulated[:, h] * discount[h]
        mean = float(deflated.mean())
        stderr = float(deflated.std(ddof=1) / np.sqrt(n)) if n > 1 else 0.0
        out.append({
            "horizon": h + 1,
            "mean_deflated": mean,
            "error": mean - 1.0,
            "stderr": stderr,
            "sigmas": abs(mean - 1.0) / stderr if stderr > 0 else 0.0,
        })
    return out


def check_risk_neutral(scenarios: ScenarioSet, rate, *, name: str | None = None,
                       sigmas: float = 5.0) -> None:
    """Raise if any horizon misses the martingale property by more than
    ``sigmas`` standard errors.

    Deliberately stated in standard errors rather than in basis points: the
    same absolute deviation is fine on 1,000 scenarios and damning on
    1,000,000, and a fixed tolerance cannot tell those apart.
    """
    worst = max(
        martingale_error(scenarios, rate, name=name),
        key=lambda row: row["sigmas"],
    )
    if worst["sigmas"] > sigmas:
        raise ValueError(
            f"scenario set is not risk-neutral at rate {rate}: at horizon "
            f"{worst['horizon']} the deflated fund averages "
            f"{worst['mean_deflated']:.8f}, off by {worst['error']:+.2e} "
            f"= {worst['sigmas']:.1f} standard errors "
            f"(stderr {worst['stderr']:.2e})"
        )


# --- parsing helpers -----------------------------------------------------


def _read_delimited(path, delimiter: str, skip_lines: int) -> list:
    with open(path, encoding="utf-8-sig", newline="") as handle:
        for _ in range(skip_lines):
            handle.readline()
        return [row for row in csv.reader(handle, delimiter=delimiter) if row]


def _read_dicts(path, delimiter: str, skip_lines: int) -> list:
    with open(path, encoding="utf-8-sig", newline="") as handle:
        for _ in range(skip_lines):
            handle.readline()
        return list(csv.DictReader(handle, delimiter=delimiter))


def _float(text, path, column, line) -> float:
    try:
        return float(text)
    except (TypeError, ValueError):
        raise ValueError(
            f"{path}: row {line}, column {column!r}: {text!r} is not a number"
        ) from None


def _number(text, path, column, line):
    """An identifier that sorts numerically when it can, and lexically when
    it cannot — so ``scenario`` may be ``1`` or ``S1`` and either orders
    sensibly."""
    try:
        return (0, float(text), "")
    except (TypeError, ValueError):
        return (1, 0.0, str(text))


def _floats(rows: Iterable[Sequence[str]], path, what) -> np.ndarray:
    out = []
    for line, row in enumerate(rows, start=1):
        out.append([_float(cell, path, f"{what} {i + 1}", line)
                    for i, cell in enumerate(row)])
    return np.asarray(out, dtype=np.float64)


def _long_from_records(records, where, *, scenario_column="scenario",
                       period_column="period", value_column="value",
                       series_column=None, primary=None, kind="return",
                       index_base=None, starts_at=1):
    """The long-layout core, over already-parsed records.

    Kept separate from :func:`read_long` so the Parquet path reuses the
    validation rather than reimplementing it — a second implementation of
    "is this rectangle complete" is a second place for it to be wrong.
    """
    needed = [scenario_column, period_column, value_column]
    if series_column:
        needed.append(series_column)
    missing = [c for c in needed if c not in records[0]]
    if missing:
        raise ValueError(
            f"{where}: no column(s) {missing}; the file has "
            f"{sorted(records[0])}"
        )
    cells: dict = defaultdict(dict)
    scenarios: set = set()
    periods: set = set()
    for n, row in enumerate(records, start=1):
        series = row[series_column] if series_column else (primary or PRIMARY)
        key = (_number(row[scenario_column], where, scenario_column, n),
               _number(row[period_column], where, period_column, n))
        if key in cells[series]:
            raise ValueError(
                f"{where}: duplicate row for series {series!r}, scenario "
                f"{key[0]}, period {key[1]}"
            )
        cells[series][key] = _float(row[value_column], where, value_column, n)
        scenarios.add(key[0])
        periods.add(key[1])

    order_s, order_p = sorted(scenarios), sorted(periods)
    built = {}
    for series, by_key in cells.items():
        grid = np.empty((len(order_s), len(order_p)), dtype=np.float64)
        holes = []
        for i, s in enumerate(order_s):
            for j, p in enumerate(order_p):
                try:
                    grid[i, j] = by_key[(s, p)]
                except KeyError:
                    holes.append((s[1] if s[0] == 0 else s[2],
                                  p[1] if p[0] == 0 else p[2]))
        if holes:
            shown = ", ".join(f"({s}, {p})" for s, p in holes[:5])
            more = "" if len(holes) <= 5 else f" and {len(holes) - 5} more"
            raise ValueError(
                f"{where}: series {series!r} is missing (scenario, period) "
                f"{shown}{more}"
            )
        built[series] = _to_returns(grid, kind, index_base, starts_at)

    if primary is None:
        if len(built) != 1:
            raise ValueError(
                f"{where} carries series {sorted(built)}; name one of them "
                "as `primary` so templates know which to compound by"
            )
        primary = next(iter(built))
    return ScenarioSet(series=built, primary=primary)
