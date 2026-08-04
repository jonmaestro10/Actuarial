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

That independence is a property of the DSL as it stands today, not a
permanent one. A model whose variables reduce across the model-point axis —
the pooled variable-payment adjustment in docs/vpla-review.md §7.1 is the
motivating case — must set ``couples_model_points = True``, and the runner
will keep its block whole.
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
    model = model_cls(mp=batch, assumptions=assumptions, proj_len=proj_len)
    names = outputs or model.var_names()
    # (proj_len + 1, n) per variable; scalar-valued vars (e.g. discount
    # factors that don't depend on the model point) broadcast across the batch.
    return {
        name: np.vstack(
            [
                np.broadcast_to(np.asarray(value, dtype=np.float64), (batch.n,))
                for value in model.series(name)
            ]
        )
        for name in names
    }


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
    if getattr(model_cls, "couples_model_points", False):
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
