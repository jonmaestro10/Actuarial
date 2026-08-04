"""Vectorized executor: one model instance evaluates every model point.

The same model class the interpreter runs per policy is instantiated once
over a ``ModelPointBatch``; each ``@var`` then evaluates to an array of
shape ``(n_modelpoints,)`` per time step, so the projection loops over time
only. This requires templates written in indicator style (no ``if`` on
model-point data) — the library conventions. The golden suite asserts
bitwise equality between the two executors on every template.
"""

from __future__ import annotations

from typing import Any, Iterable, Type

import numpy as np

from engine.core.model import Model
from engine.core.results import ArrayRunResult
from engine.data.modelpoints import to_batch


def run_vectorized(
    model_cls: Type[Model],
    modelpoints: Iterable[Any],
    assumptions: Any,
    proj_len: int,
    outputs: list[str] | None = None,
) -> ArrayRunResult:
    batch = to_batch(modelpoints)
    model = model_cls(mp=batch, assumptions=assumptions, proj_len=proj_len)
    names = outputs or model.var_names()

    # (proj_len + 1, n) per variable; scalar-valued vars (e.g. discount
    # factors that don't depend on the model point) broadcast across the batch.
    stacked = {
        name: np.vstack(
            [
                np.broadcast_to(
                    np.asarray(value, dtype=np.float64), (batch.n,)
                )
                for value in model.series(name)
            ]
        )
        for name in names
    }
    return ArrayRunResult(stacked=stacked, mp_ids=batch.ids)
