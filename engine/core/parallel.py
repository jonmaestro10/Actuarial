"""Scale-out: shard a block across worker processes.

PLAN.md §4.3 asks for batches sharded across cores and nodes with results
reduced as streaming aggregations, and §8 lists multi-node scale-out among
the Phase 2 exits. This is the sharding and the reduction, running across
**cores on one machine** through the standard library.

What that is and is not
-----------------------
The hard part of scale-out is not the dispatch mechanism. It is deciding
what may be split, proving the split cannot move a number, and reducing the
pieces back in an order that does not depend on which finished first. All
three are here, and all three are what a cross-machine runner would need
before it could be trusted.

What is **not** here is cross-machine dispatch. Ray or an equivalent would
replace ``ProcessPoolExecutor`` and nothing else in this module, but saying
that is not the same as having done it, and this file runs on one machine.

Why sharding is safe
--------------------
The same argument that licenses chunking in engine/core/vector.py: model
points are independent, so evaluating one has no effect on any other. A
shard is a chunk that happens to live in another process.

The argument fails for exactly the models where chunking is already
disabled — a ``@pool`` variable reduces across the model-point axis, so a
reduction over a shard would be a reduction over the wrong population.
Those are **refused** rather than silently run, because a pooled model
sharded four ways produces plausible numbers that are wrong.

What the measurements decided
-----------------------------
Sharding a block and shipping **per-policy series** back is a *loss* on one
machine, at every size measured — 0.96x at best on four cores, and 0.53x on
200,000 policies. The results are the payload: four outputs over 61 periods
for 200,000 policies is 390 MB going back through pipes, which costs more
than the three seconds of arithmetic that produced it.

Sharding and **reducing in the worker** is 2.3x on the same four cores,
because what comes back is 61 numbers per output instead of 61 x 200,000.
That is what PLAN §4.3 means by "results reduce as streaming aggregations",
and it is the mode worth using on one machine.

:func:`run_parallel` therefore exists but is documented as what it is: the
per-policy form, useful when a worker writes its own results somewhere —
which is the cross-machine case — and a loss when it has to hand them back.
:func:`run_parallel_totals` is the one that pays.

Determinism, and one honest caveat
----------------------------------
Per-policy results are **bitwise** identical for any number of workers:
shards are contiguous, reassembled by index rather than by completion order,
and model points are independent.

Block totals are not. Summing a shard and then summing the shards regroups
the additions, so a four-worker total can differ from a two-worker total in
the last bit — measured at 1e-16 relative, and exactly zero at most sizes.
A given worker count is exactly reproducible; a change of worker count is
not a change of question, so RFC-003's determinism claim needs the worker
count recorded alongside it. Stated here rather than discovered later.
"""

from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor
from typing import Any, Iterable, Type

import numpy as np

from engine.core.model import Model
from engine.core.results import ArrayRunResult
from engine.core.vector import run_vectorized
from engine.data.modelpoints import ModelPointBatch, to_batch

#: Below this many policy-periods, a run stays in-process. Sending a shard
#: costs a pickle of the model points and the assumptions, and on a small
#: block that is more than the projection itself. Measured: at 122,000 cells
#: four workers are 0.49x, at 610,000 they are 2.0x on the reducing path.
MIN_PARALLEL_CELLS = 400_000


def default_workers() -> int:
    """One worker per core, which is what a projection wants.

    A projection is compute-bound in NumPy, not waiting on anything, so
    oversubscribing only adds context switches and pickle traffic.
    """
    return max(1, os.cpu_count() or 1)


def shard_bounds(n: int, workers: int) -> list:
    """Contiguous ``(start, stop)`` ranges, as even as they divide.

    Contiguous rather than round-robin so a shard is a slice of the batch
    and reassembly is a concatenation in index order — which is what keeps
    the result independent of completion order.
    """
    if workers < 1:
        raise ValueError(f"workers {workers} must be >= 1")
    workers = min(workers, n)
    size, extra = divmod(n, workers)
    bounds, start = [], 0
    for i in range(workers):
        stop = start + size + (1 if i < extra else 0)
        bounds.append((start, stop))
        start = stop
    return bounds


def _run_shard(payload):
    """Executed in the worker. Module-level so it pickles."""
    model_cls, batch, assumptions, proj_len, outputs, chunk_size = payload
    result = run_vectorized(
        model_cls, batch, assumptions, proj_len, outputs=outputs,
        chunk_size=chunk_size,
    )
    return dict(result._stacked)


def _run_shard_totals(payload):
    """Executed in the worker, returning per-period totals.

    The reduction happens here rather than in the parent, which is the
    entire reason this path is faster: what crosses the process boundary is
    one number per period per output instead of one per policy per period.
    """
    model_cls, batch, assumptions, proj_len, outputs, chunk_size = payload
    result = run_vectorized(
        model_cls, batch, assumptions, proj_len, outputs=outputs,
        chunk_size=chunk_size,
    )
    return {name: values.sum(axis=1)
            for name, values in result._stacked.items()}


def _check_shardable(model_cls) -> None:
    if getattr(model_cls, "couples_model_points", False) or model_cls.pooled_names():
        raise ValueError(
            f"{model_cls.__name__} couples its model points — a reduction "
            "over a shard would reduce over the wrong population. Run it "
            "with run_vectorized, which keeps the block whole."
        )


def run_parallel(
    model_cls: Type[Model],
    modelpoints: Iterable[Any],
    assumptions: Any,
    proj_len: int,
    outputs: list[str] | None = None,
    workers: int | None = None,
    chunk_size: int | None = None,
    min_cells: int = MIN_PARALLEL_CELLS,
) -> ArrayRunResult:
    """Project a block across worker processes.

    Bitwise identical to :func:`engine.core.vector.run_vectorized` for any
    number of workers — the shard is a chunk in another process, and model
    points are independent.

    **Slower than running in one process**, measured at every size on four
    cores, because the per-policy results are the payload and moving them
    costs more than computing them. Use it when a worker writes its own
    results rather than handing them back — the cross-machine case — and use
    :func:`run_parallel_totals` when block cashflows are what is wanted.
    """
    batch = to_batch(modelpoints)
    workers = default_workers() if workers is None else int(workers)
    if workers < 1:
        raise ValueError(f"workers {workers} must be >= 1")

    _check_shardable(model_cls)

    cells = batch.n * (proj_len + 1)
    if workers == 1 or batch.n < 2 or cells < min_cells:
        return run_vectorized(
            model_cls, batch, assumptions, proj_len, outputs=outputs,
            chunk_size=chunk_size,
        )

    bounds = shard_bounds(batch.n, workers)
    payloads = [
        (model_cls, batch.take(start, stop), assumptions, proj_len, outputs,
         chunk_size)
        for start, stop in bounds
    ]
    with ProcessPoolExecutor(max_workers=len(payloads)) as pool:
        # `map` yields in submission order, so reassembly follows the shard
        # order rather than whichever worker finished first.
        parts = list(pool.map(_run_shard, payloads))

    names = list(parts[0])
    return ArrayRunResult(
        stacked={
            name: np.hstack([part[name] for part in parts]) for name in names
        },
        mp_ids=batch.ids,
    )


def run_parallel_totals(
    model_cls: Type[Model],
    modelpoints: Iterable[Any],
    assumptions: Any,
    proj_len: int,
    outputs: list[str] | None = None,
    workers: int | None = None,
    chunk_size: int | None = None,
    min_cells: int = MIN_PARALLEL_CELLS,
) -> dict:
    """Per-period block totals, reduced inside the workers.

    The mode that pays: 2.3x on four cores at 100,000 policies, because what
    crosses the process boundary is 61 numbers per output rather than
    61 x 100,000.

    Returns ``{name: (proj_len + 1,) array}``.

    Reproducible for a fixed worker count, and **not** bit-identical across
    worker counts: summing shards and then summing the shard totals regroups
    the additions. The difference is at machine epsilon — 1e-16 relative
    where it is non-zero at all — but it is a difference, and the worker
    count belongs alongside the run id if it is recorded.
    """
    batch = to_batch(modelpoints)
    workers = default_workers() if workers is None else int(workers)
    if workers < 1:
        raise ValueError(f"workers {workers} must be >= 1")
    _check_shardable(model_cls)

    cells = batch.n * (proj_len + 1)
    if workers == 1 or batch.n < 2 or cells < min_cells:
        result = run_vectorized(
            model_cls, batch, assumptions, proj_len, outputs=outputs,
            chunk_size=chunk_size,
        )
        return {name: values.sum(axis=1)
                for name, values in result._stacked.items()}

    payloads = [
        (model_cls, batch.take(start, stop), assumptions, proj_len, outputs,
         chunk_size)
        for start, stop in shard_bounds(batch.n, workers)
    ]
    with ProcessPoolExecutor(max_workers=len(payloads)) as pool:
        parts = list(pool.map(_run_shard_totals, payloads))
    return reduce_totals(parts)


def reduce_totals(parts: Iterable[dict]) -> dict:
    """Sum per-shard totals, in shard order.

    Shard order rather than completion order, for the same reason the
    per-policy shards are reassembled by index: a total that depended on
    which worker finished first would not be reproducible at all, and the
    regrouping above is small enough only because it is fixed.
    """
    totals = None
    for part in parts:
        if totals is None:
            totals = {name: np.array(values, dtype=np.float64)
                      for name, values in part.items()}
        else:
            for name, values in part.items():
                totals[name] = totals[name] + values
    if totals is None:
        raise ValueError("no shard results to reduce")
    return totals
