"""The results warehouse: every number, and where it came from.

RFC-046. An actuarial function's numbers end up in a dashboard, and the
dashboard is where the trust goes to die: a figure in a BI tool is a figure
whose provenance is a filename in somebody's head. This writes results into
a star schema in partitioned Parquet — one fact table, three dimensions —
with **the run fingerprint on every fact row**, so any number in any
downstream tool joins back to a registered, reproducible run.

    fact_cashflow(run_id, modelpoint_id, scenario, t, variable, value)
    dim_run(run_id, model, assumptions_digest, modelpoints_digest,
            results_digest, engine_version, executor, code_version, …)
    dim_modelpoint(run_id, modelpoint_id, ordinal)
    dim_variable(run_id, variable, assumption, pooled, documented, doc)

Behind the ``[data]`` extra, like every other pyarrow surface in the repo.

**Long, not wide.** One row per (model point, scenario, t, variable) rather
than a column per variable. A wide table has to be migrated every time a
template gains a variable, and a warehouse whose schema changes with the
model is a warehouse that breaks a dashboard every quarter. Long costs
storage and buys a schema that never moves.

**Partitioned by run, and the run id is a column rather than the path.**
``fact_cashflow/<run digest>/`` — so writing a run creates a directory
nobody else's data is in, rewriting one replaces exactly that directory, and
reading one is not a filter over everything. The directory is deliberately
*not* Hive-style (``run_id=<digest>``): the fingerprint lives in a column in
every file, so a fact file copied out of the tree still knows which run it
came from, and a reader has exactly one place to get it from rather than two
that can disagree.

**Nothing is rounded and nothing is aggregated on the way in.** The values
written are the float64 the engine produced, and the test that matters
reconstructs the arrays out of Parquet and fingerprints them against the
run's own ``results_digest``. A warehouse whose numbers merely *look* like
the run's is the thing this exists to replace.

Reading it needs nothing from this repo. DuckDB, over the directory::

    SELECT r.model, f.t, sum(f.value) AS claims
    FROM read_parquet('warehouse/fact_cashflow/**/*.parquet') f
    JOIN read_parquet('warehouse/dim_run/*.parquet') r USING (run_id)
    WHERE f.variable = 'claims'
    GROUP BY 1, 2 ORDER BY 2;

Power BI and Tableau read the same directory through their Parquet
connectors; the partition column is the join key in both.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

FACT = "fact_cashflow"
DIM_RUN = "dim_run"
DIM_MODELPOINT = "dim_modelpoint"
DIM_VARIABLE = "dim_variable"
TABLES = (FACT, DIM_RUN, DIM_MODELPOINT, DIM_VARIABLE)


class WarehouseError(ValueError):
    """A write the warehouse will not perform silently."""


def _pyarrow():
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover - exercised by the extra
        raise ImportError(
            "the results warehouse needs pyarrow: pip install -e '.[data]'"
        ) from exc
    return pa, pq


def _variable_rows(record, model_cls=None) -> list[dict]:
    """The variable dimension, off the model class where there is one.

    ``@var`` carries a docstring and a declared assumption, which is what a
    dashboard author actually wants next to a variable name — "is this
    gross or net" is answered by the docstring or by nothing. The model is
    imported from the run record when the caller did not pass it, and a
    failure to import is not fatal: the dimension degrades to names, and
    says so by leaving the other columns null.
    """
    if model_cls is None:
        try:
            import importlib

            module = importlib.import_module(record.model_module)
            model_cls = getattr(module, record.model_name, None)
        except Exception:  # pragma: no cover - a model that moved
            model_cls = None

    rows = []
    pooled = set(model_cls.pooled_names()) if model_cls is not None else set()
    for name in record.outputs:
        spec = None
        if model_cls is not None:
            fn = getattr(model_cls, name, None)
            spec = getattr(fn, "__var_spec__", None)
        doc = (spec.doc or "").strip().splitlines()[0] if spec and spec.doc \
            else None
        rows.append({
            "run_id": record.run_id,
            "variable": name,
            "assumption": spec.assumption if spec else None,
            "pooled": name in pooled,
            "documented": bool(doc),
            "doc": doc,
        })
    return rows


def _arrays(result, names: Sequence[str]) -> dict:
    """``(t, model point[, scenario])`` arrays out of any executor's result.

    The interpreted executor returns per-model-point lists rather than
    slabs. Transposing them here rather than refusing is the same choice
    the run registry makes when it digests an interpreted run: which
    executor produced a number is provenance, not schema.
    """
    if hasattr(result, "array"):
        return {name: np.asarray(result.array(name)) for name in names}
    return {name: np.array([mp[name] for mp in result.per_mp],
                           dtype=np.float64).T
            for name in names}


def _run_row(record) -> dict:
    return {
        "run_id": record.run_id,
        "model": f"{record.model_module}.{record.model_name}",
        "model_name": record.model_name,
        "model_source_digest": record.model_source_digest,
        "assumptions_digest": record.assumptions_digest,
        "modelpoints_digest": record.modelpoints_digest,
        "scenarios_digest": record.scenarios_digest,
        "results_digest": record.results_digest,
        "engine_version": record.engine_version,
        "executor": record.executor,
        "code_version": record.code_version,
        "n_modelpoints": record.n_modelpoints,
        "n_scenarios": record.n_scenarios,
        "proj_len": record.proj_len,
        "outputs": list(record.outputs),
        "created_at": record.created_at,
    }


@dataclass(frozen=True)
class WarehouseWrite:
    """What one write put where."""

    run_id: str
    n_facts: int
    n_modelpoints: int
    n_variables: int
    n_scenarios: int | None
    path: Path


class Warehouse:
    """A directory of Parquet, in a star schema, keyed by run fingerprint."""

    def __init__(self, root: Path | str):
        self.root = Path(root)

    def path_for(self, table: str, run_id: str | None = None) -> Path:
        if run_id is not None:
            return self.root / table / run_id
        return self.root / table

    # --- writing ----------------------------------------------------------

    def write_run(self, result, record, *, model_cls=None,
                  scenarios: Sequence[int] | str | None = None
                  ) -> WarehouseWrite:
        """Write one registered run into the warehouse.

        ``record`` is a :class:`~engine.core.registry.RunRecord`: the
        warehouse takes the *registered* form of a run rather than a bare
        result, because a fact row whose provenance columns were assembled
        by the caller is a fact row with no provenance at all.

        A stochastic result must name its scenarios — ``"all"`` or a list of
        indices. A thousand-scenario run is a thousand times the rows, and
        that is a decision somebody should make on purpose.
        """
        pa, pq = _pyarrow()
        names = list(record.outputs)
        if not names:
            raise WarehouseError(f"run {record.run_id} names no outputs")

        arrays = _arrays(result, names)
        shapes = {name: arr.shape for name, arr in arrays.items()}
        if len(set(shapes.values())) != 1:
            raise WarehouseError(f"variables disagree on shape: {shapes}")
        shape = next(iter(shapes.values()))
        mp_ids = [str(mp_id) for mp_id in result.mp_ids]

        if len(shape) == 2:
            if scenarios not in (None, "none"):
                raise WarehouseError(
                    "this run has no scenario axis, so there are no "
                    "scenarios to select"
                )
            chosen: list[int] | None = None
        elif len(shape) == 3:
            if scenarios is None:
                raise WarehouseError(
                    f"run {record.run_id} is stochastic ({shape[2]:,} "
                    f"scenarios): writing every scenario multiplies the rows "
                    f"by that, so name them — scenarios='all' or a list of "
                    f"indices"
                )
            chosen = (list(range(shape[2])) if scenarios == "all"
                      else [int(s) for s in scenarios])
            out_of_range = [s for s in chosen if not 0 <= s < shape[2]]
            if out_of_range:
                raise WarehouseError(
                    f"scenario(s) {out_of_range} outside 0..{shape[2] - 1}"
                )
        else:
            raise WarehouseError(
                f"cannot write a {len(shape)}-dimensional result"
            )

        n_steps = shape[0]
        n_mp = shape[1]
        if n_mp != len(mp_ids):
            raise WarehouseError(
                f"{n_mp} model-point columns but {len(mp_ids)} ids"
            )

        # Long format, built column-wise: a row-at-a-time loop over a
        # 100k x 60y x 20-variable run is minutes, and this is seconds.
        run_ids, variables, mps, steps, scens, values = [], [], [], [], [], []
        scenario_list = chosen if chosen is not None else [None]
        block = n_steps * n_mp
        t_column = np.repeat(np.arange(n_steps, dtype=np.int32), n_mp)
        mp_column = np.tile(np.array(mp_ids, dtype=object), n_steps)
        for name in names:
            array = arrays[name]
            for scenario in scenario_list:
                flat = (array.reshape(block) if scenario is None
                        else array[:, :, scenario].reshape(block))
                values.append(np.asarray(flat, dtype=np.float64))
                variables.append(np.full(block, name, dtype=object))
                steps.append(t_column)
                mps.append(mp_column)
                scens.append(np.full(block, -1 if scenario is None else scenario,
                                     dtype=np.int32)
                             if scenario is not None
                             else np.full(block, None, dtype=object))
                run_ids.append(np.full(block, record.run_id, dtype=object))

        facts = pa.table({
            "run_id": pa.array(np.concatenate(run_ids), type=pa.string()),
            "modelpoint_id": pa.array(np.concatenate(mps), type=pa.string()),
            "scenario": pa.array(np.concatenate(scens).tolist(),
                                 type=pa.int32()),
            "t": pa.array(np.concatenate(steps), type=pa.int32()),
            "variable": pa.array(np.concatenate(variables), type=pa.string()),
            "value": pa.array(np.concatenate(values), type=pa.float64()),
        })

        fact_dir = self.path_for(FACT, record.run_id)
        fact_dir.mkdir(parents=True, exist_ok=True)
        pq.write_table(facts, fact_dir / "part-0.parquet")

        self._write_dim(DIM_RUN, record.run_id, [_run_row(record)])
        self._write_dim(DIM_MODELPOINT, record.run_id, [
            {"run_id": record.run_id, "modelpoint_id": mp_id, "ordinal": i}
            for i, mp_id in enumerate(mp_ids)
        ])
        self._write_dim(DIM_VARIABLE, record.run_id,
                        _variable_rows(record, model_cls))

        return WarehouseWrite(
            run_id=record.run_id, n_facts=facts.num_rows, n_modelpoints=n_mp,
            n_variables=len(names),
            n_scenarios=None if chosen is None else len(chosen),
            path=fact_dir,
        )

    def _write_dim(self, table: str, run_id: str, rows: list[dict]) -> None:
        pa, pq = _pyarrow()
        directory = self.root / table
        directory.mkdir(parents=True, exist_ok=True)
        # One file per run, named by the run: rewriting a run replaces its
        # own rows and touches nobody else's, which is what makes a re-load
        # idempotent rather than duplicating.
        payload = [dict(row) for row in rows]
        for row in payload:
            for key, value in list(row.items()):
                if isinstance(value, list):
                    row[key] = json.dumps(value)
        pq.write_table(pa.Table.from_pylist(payload),
                       directory / f"part-{run_id}.parquet")

    # --- reading ----------------------------------------------------------

    def _read(self, table: str, run_id: str | None = None):
        pa, pq = _pyarrow()
        directory = (self.path_for(table, run_id) if run_id
                     else self.root / table)
        if not directory.exists():
            return None
        files = sorted(directory.rglob("*.parquet"))
        if not files:
            return None
        # ``partitioning=None``: the directory name is a run digest and not
        # a Hive key, and letting the dataset reader guess otherwise would
        # give run_id two sources that can disagree.
        return pq.read_table(files, partitioning=None)

    def runs(self) -> list[dict]:
        """Every run in the warehouse, newest last."""
        table = self._read(DIM_RUN)
        if table is None:
            return []
        rows = table.to_pylist()
        for row in rows:
            if isinstance(row.get("outputs"), str):
                row["outputs"] = json.loads(row["outputs"])
        return sorted(rows, key=lambda row: row["created_at"])

    def run(self, run_id: str) -> dict | None:
        for row in self.runs():
            if row["run_id"] == run_id:
                return row
        return None

    def variables(self, run_id: str | None = None) -> list[dict]:
        table = self._read(DIM_VARIABLE)
        if table is None:
            return []
        rows = table.to_pylist()
        return [row for row in rows
                if run_id is None or row["run_id"] == run_id]

    def modelpoints(self, run_id: str | None = None) -> list[dict]:
        table = self._read(DIM_MODELPOINT)
        if table is None:
            return []
        rows = table.to_pylist()
        return [row for row in rows
                if run_id is None or row["run_id"] == run_id]

    def facts(self, run_id: str | None = None, variable: str | None = None):
        """The fact table, optionally for one run and one variable."""
        table = self._read(FACT, run_id)
        if table is None:
            return None
        if variable is not None:
            import pyarrow.compute as pc

            table = table.filter(pc.equal(table["variable"], variable))
        return table

    def array(self, run_id: str, variable: str) -> np.ndarray:
        """Rebuild a ``(t, model point)`` array out of the warehouse.

        The operation that makes the provenance claim checkable: fingerprint
        what comes back and it must equal the run's ``results_digest``.
        """
        table = self.facts(run_id, variable)
        if table is None:
            raise WarehouseError(f"no facts for run {run_id}")
        record = self.run(run_id)
        order = {row["modelpoint_id"]: row["ordinal"]
                 for row in self.modelpoints(run_id)}
        n_steps = record["proj_len"] + 1
        out = np.full((n_steps, len(order)), np.nan)
        columns = table.to_pydict()
        for mp_id, t, value, scenario in zip(columns["modelpoint_id"],
                                             columns["t"], columns["value"],
                                             columns["scenario"]):
            if scenario is not None:
                continue
            out[t, order[mp_id]] = value
        return out


def write_run(root: Path | str, result, record, **options) -> WarehouseWrite:
    """Convenience: write one run into the warehouse at ``root``."""
    return Warehouse(root).write_run(result, record, **options)
