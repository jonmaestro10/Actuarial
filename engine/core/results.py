"""Result containers and deterministic aggregation."""

from __future__ import annotations

import numpy as np


def stable_sum(values) -> float:
    """Kahan–Babuška–Neumaier compensated summation.

    Order-stable and far less float drift than naive ``sum`` on large
    aggregations — part of the determinism guarantee. The Neumaier variant
    (rather than plain Kahan) also compensates when an addend exceeds the
    running total, e.g. sign-alternating large cashflows.
    """
    total = 0.0
    c = 0.0
    for v in values:
        t = total + v
        if abs(total) >= abs(v):
            c += (total - t) + v
        else:
            c += (v - t) + total
        total = t
    return total + c


class RunResult:
    """Per-model-point series plus deterministic aggregates.

    ``per_mp`` is a list (one entry per model point, input order preserved) of
    ``{var_name: [values by t]}``. ``aggregate`` sums each variable across
    model points per time step using compensated summation.
    """

    def __init__(self, per_mp: list[dict[str, list]], mp_ids: list):
        self.per_mp = per_mp
        self.mp_ids = mp_ids

    def aggregate(self, name: str) -> list[float]:
        n_steps = len(self.per_mp[0][name])
        return [
            stable_sum(mp_result[name][t] for mp_result in self.per_mp)
            for t in range(n_steps)
        ]


class ArrayRunResult(RunResult):
    """Array-backed result from the vectorized executor.

    Holds one ``(proj_len + 1, n_modelpoints)`` float64 array per variable;
    the per-model-point dict view is materialized lazily so bulk workflows
    (aggregation, writing results tables) never pay for it. Aggregation uses
    NumPy's pairwise summation — deterministic for a fixed model-point order
    and comparably accurate to compensated summation.
    """

    def __init__(self, stacked: dict[str, np.ndarray], mp_ids: list):
        self._stacked = stacked
        self.mp_ids = mp_ids
        self._per_mp: list[dict[str, list]] | None = None

    @property
    def per_mp(self) -> list[dict[str, list]]:
        if self._per_mp is None:
            n = len(self.mp_ids)
            self._per_mp = [
                {name: arr[:, i].tolist() for name, arr in self._stacked.items()}
                for i in range(n)
            ]
        return self._per_mp

    def array(self, name: str) -> np.ndarray:
        return self._stacked[name]

    def aggregate(self, name: str) -> list[float]:
        return self._stacked[name].sum(axis=1).tolist()
