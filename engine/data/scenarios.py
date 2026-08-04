"""Economic scenario sets.

Phase 2 seed: a matrix of fund/equity returns, shape
``(n_scenarios, horizon)``, with generators that pin their RNG stream so a
scenario set is fully determined by its parameters and seed. ESG file
adapters (Moody's/Conning-style tables) plug in here later.
"""

from __future__ import annotations

import numpy as np


class ScenarioSet:
    """Annual fund returns per scenario: ``returns[s, t]`` is the return
    earned during period ``t`` in scenario ``s``."""

    def __init__(self, returns):
        arr = np.asarray(returns, dtype=np.float64)
        if arr.ndim != 2:
            raise ValueError(f"returns must be 2-D (n_scenarios, horizon), got {arr.shape}")
        if np.any(arr <= -1.0):
            raise ValueError("returns at or below -100% are not valid")
        self.returns = arr
        self.n_scenarios, self.horizon = arr.shape

    def __fingerprint__(self):
        return {"returns": self.returns}

    def ret(self, t: int) -> np.ndarray:
        """Returns for period t across scenarios, shape (n_scenarios,)."""
        if not 0 <= t < self.horizon:
            raise IndexError(f"period {t} outside scenario horizon [0, {self.horizon})")
        return self.returns[:, t]

    @classmethod
    def flat(cls, rate: float, n_scenarios: int, horizon: int) -> "ScenarioSet":
        return cls(np.full((n_scenarios, horizon), rate))

    @classmethod
    def lognormal(cls, n_scenarios: int, horizon: int, *, drift: float,
                  vol: float, seed: int) -> "ScenarioSet":
        """IID lognormal annual returns: log(1 + r) ~ N(drift - vol^2/2, vol^2).

        With ``drift = log(1 + i)`` the returns are risk-neutral w.r.t. flat
        rate ``i``: E[1 + r] = 1 + i, which the martingale golden test relies
        on. The seed pins the stream — same parameters, same set, bitwise.
        """
        rng = np.random.default_rng(seed)
        z = rng.standard_normal((n_scenarios, horizon))
        return cls(np.exp(drift - 0.5 * vol**2 + vol * z) - 1.0)

    def single(self, s: int) -> "ScenarioSet":
        """A one-scenario view of scenario ``s`` (for consistency testing)."""
        return ScenarioSet(self.returns[s : s + 1, :])
