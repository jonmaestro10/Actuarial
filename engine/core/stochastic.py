"""Stochastic executor: model points x scenarios in one evaluation.

Model-point fields are reshaped to column vectors ``(n_mp, 1)`` and
scenario returns keep shape ``(n_scenarios,)``, so ordinary NumPy
broadcasting turns every formula into a ``(n_mp, n_scenarios)`` slab per
time step — the execution shape from PLAN.md §2.2. Templates need no
changes beyond the indicator style they already use.
"""

from __future__ import annotations

from typing import Any, Iterable, Type

import numpy as np

from engine.core.model import Model
from engine.core.results import StochasticRunResult
from engine.data.modelpoints import to_batch
from engine.data.scenarios import ScenarioSet


class _ColumnBatch:
    """Model-point batch with numeric fields reshaped (n_mp, 1) so they
    broadcast against per-scenario vectors."""

    def __init__(self, batch):
        for name, value in batch.__dict__.items():
            if isinstance(value, np.ndarray) and value.dtype != object:
                value = value[:, None]
            setattr(self, name, value)


def run_stochastic(
    model_cls: Type[Model],
    modelpoints: Iterable[Any],
    assumptions: Any,
    scenarios: ScenarioSet,
    proj_len: int,
    outputs: list[str] | None = None,
) -> StochasticRunResult:
    if scenarios.horizon < proj_len:
        raise ValueError(
            f"scenario horizon {scenarios.horizon} shorter than "
            f"projection length {proj_len}"
        )
    batch = to_batch(modelpoints)
    model = model_cls(
        mp=_ColumnBatch(batch),
        assumptions=assumptions,
        proj_len=proj_len,
        scenarios=scenarios,
    )
    names = outputs or model.var_names()
    shape = (batch.n, scenarios.n_scenarios)
    stacked = {
        name: np.stack(
            [
                np.broadcast_to(np.asarray(value, dtype=np.float64), shape)
                for value in model.series(name)
            ]
        )
        for name in names
    }
    return StochasticRunResult(stacked=stacked, mp_ids=batch.ids)
