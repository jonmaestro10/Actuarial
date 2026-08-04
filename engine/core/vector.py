"""Vectorized executor: one model instance evaluates every model point.

The same model class the interpreter runs per policy is instantiated once
over a ``ModelPointBatch``; each ``@var`` then evaluates to an array of
shape ``(n_modelpoints,)`` per time step, so the projection loops over time
only. This requires templates written in indicator style (no ``if`` on
model-point data) — the library conventions. The golden suite asserts
bitwise equality between the two executors on every template.

Blocks are processed in **chunks** of model points. Vectorizing the whole
axis at once is compute-optimal but not memory-optimal: a 60-year monthly
projection holds a ``(policies, 720)`` array for every intermediate, and
past a few hundred policies the working set stops fitting in cache and the
run goes from compute-bound to bandwidth-bound. Chunking gave ~3.8x on a
10,000-annuitant monthly block. It changes no number — every model point is
independent, so a chunked run is **bitwise identical** to an unchunked one,
which tests/test_vector.py asserts.

That independence holds only for models built from ``@var`` alone. A model
declaring a ``@pool`` variable reduces across the model-point axis, so a
chunk would reduce over the wrong population; the runner detects those and
keeps their block whole. ``couples_model_points = True`` forces the same
treatment for a model that couples policies some other way.
"""

from __future__ import annotations

from typing import Any, Iterable, Type

import numpy as np

from engine.core.model import Model
from engine.core.results import ArrayRunResult
from engine.data.modelpoints import ModelPointBatch, to_batch

# Cells (policies x periods) to aim for in one chunk. Sized so the working
# set of a projection stays in cache rather than in main memory; the exact
# figure is not sensitive, but the order of magnitude is.
CHUNK_CELLS = 200_000
MIN_CHUNK_POLICIES = 64

#: Periods to trace a model over before running it. Three is the fewest
#: that exercises both a variable's ``t == 0`` branch and its recursive one.
TRACE_PERIODS = 3


def default_chunk_size(n_periods: int) -> int:
    """Policies per chunk for a projection of ``n_periods`` steps."""
    return max(MIN_CHUNK_POLICIES, CHUNK_CELLS // max(n_periods, 1))


def _evaluate(
    model_cls: Type[Model],
    batch: ModelPointBatch,
    assumptions: Any,
    proj_len: int,
    outputs: list[str] | None,
) -> dict[str, np.ndarray]:
    """A forward loop over preallocated slabs, keeping a rolling window.

    PLAN §4.2 asks for the recursion over ``t`` to become "a forward loop
    over preallocated arrays", and this is that loop — without the compiled
    kernels, which are still ahead.

    Two things it does that the previous list-and-stack did not. Each
    period is written straight into its row of the output slab rather than
    accumulated into a list and copied at the end. And once a period is
    older than the dependency graph's look-back window, its cached values
    are dropped: a 100,000-policy 60-year projection holds hundreds of
    megabytes of arrays that nothing will read again, and freeing them is
    what keeps the working set in cache. Same expressions, same order, same
    bits — measured, not assumed.
    """
    model = model_cls(mp=batch, assumptions=assumptions, proj_len=proj_len,
                      record_graph=True)
    names = list(outputs or model.var_names())
    # (proj_len + 1, n) per variable; scalar-valued vars (e.g. discount
    # factors that don't depend on the model point) broadcast across the batch.
    slabs = {
        name: np.empty((proj_len + 1, batch.n), dtype=np.float64)
        for name in names
    }
    return fill(model, slabs, names, batch.n, proj_len)


def fill(model, slabs, names, width, proj_len):
    """The forward loop, shared by both array executors.

    The first few periods run with the dependency graph recording, which
    costs a set insert per evaluation and is not wasted work — those periods
    are needed anyway. The look-back window comes off the graph, recording
    stops, and every period after that drops what nothing can read again.
    """
    traced = min(TRACE_PERIODS, proj_len + 1)
    for t in range(traced):
        for name in names:
            slabs[name][t] = np.broadcast_to(
                np.asarray(getattr(model, name)(t), dtype=np.float64), width
            )
    window = model.graph().horizon()
    model.record_graph = False
    for t in range(traced, proj_len + 1):
        for name in names:
            slabs[name][t] = np.broadcast_to(
                np.asarray(getattr(model, name)(t), dtype=np.float64), width
            )
        model.prune(t - window)
    return slabs


def run_vectorized(
    model_cls: Type[Model],
    modelpoints: Iterable[Any],
    assumptions: Any,
    proj_len: int,
    outputs: list[str] | None = None,
    chunk_size: int | None = None,
) -> ArrayRunResult:
    batch = to_batch(modelpoints)
    if chunk_size is None:
        chunk_size = default_chunk_size(proj_len + 1)
    if getattr(model_cls, "couples_model_points", False) or model_cls.pooled_names():
        chunk_size = batch.n
    chunk_size = max(1, min(int(chunk_size), batch.n))

    if chunk_size >= batch.n:
        stacked = _evaluate(model_cls, batch, assumptions, proj_len, outputs)
        return ArrayRunResult(stacked=stacked, mp_ids=batch.ids)

    parts = [
        _evaluate(
            model_cls, batch.take(start, start + chunk_size),
            assumptions, proj_len, outputs,
        )
        for start in range(0, batch.n, chunk_size)
    ]
    stacked = {
        name: np.concatenate([part[name] for part in parts], axis=1)
        for name in parts[0]
    }
    return ArrayRunResult(stacked=stacked, mp_ids=batch.ids)
