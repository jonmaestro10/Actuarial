"""Economic scenario sets.

A scenario set is a rectangle of ``(n_scenarios, horizon)`` values per named
series: fund/equity returns, bond returns, a short rate, inflation. One
series is the **primary** — the one ``ret(t)`` returns and the unit-linked
templates compound a fund by — and the rest are along for the ride until a
template asks for them.

Generators pin their RNG stream, so a generated set is fully determined by
its parameters and seed. Sets read from an ESG file come through
:mod:`engine.data.esg`, which is where the format-specific traps live.

Two series that hold the same numbers are the same scenario set, whatever
file they came from: ``__fingerprint__`` covers the values and the primary
name, not the provenance. Where the numbers came from is recorded in
``source`` and travels alongside, the same split RFC-003 makes between a
run's inputs and its context.
"""

from __future__ import annotations

from typing import Mapping

import numpy as np

#: Name of the series a template gets from ``ret(t)`` unless told otherwise.
PRIMARY = "return"


class ScenarioSet:
    """Per-period economic series by scenario.

    ``ScenarioSet(returns)`` builds a single-series set named ``"return"``,
    which is what every template that predates named series expects.
    """

    def __init__(self, returns=None, *, series: Mapping | None = None,
                 primary: str = PRIMARY, source: dict | None = None):
        if (returns is None) == (series is None):
            raise ValueError("supply exactly one of `returns` or `series`")
        if returns is not None:
            series = {primary: returns}
        built = {}
        for name, values in series.items():
            arr = np.asarray(values, dtype=np.float64)
            if arr.ndim != 2:
                raise ValueError(
                    f"series {name!r} must be 2-D (n_scenarios, horizon), "
                    f"got {arr.shape}"
                )
            if not np.all(np.isfinite(arr)):
                raise ValueError(f"series {name!r} contains nan or inf")
            built[name] = arr
        if not built:
            raise ValueError("no series supplied")
        shapes = {arr.shape for arr in built.values()}
        if len(shapes) != 1:
            raise ValueError(
                "every series must cover the same scenarios and periods; got "
                + ", ".join(f"{n}{a.shape}" for n, a in sorted(built.items()))
            )
        if primary not in built:
            raise ValueError(
                f"primary series {primary!r} not among {sorted(built)}"
            )
        # Only the primary is checked as a *return*. A short rate or an
        # inflation series is not compounded by anything here, and inventing
        # a bound for it would reject legitimate files.
        if np.any(built[primary] <= -1.0):
            raise ValueError("returns at or below -100% are not valid")

        self.series_by_name = built
        self.primary = primary
        self.returns = built[primary]
        self.n_scenarios, self.horizon = self.returns.shape
        #: Where these numbers came from. Context, not identity — two sets
        #: holding the same values are the same set whatever file they were
        #: read from, so this is deliberately outside the fingerprint.
        self.source = dict(source) if source else None

    def __repr__(self) -> str:
        return (
            f"ScenarioSet({self.n_scenarios} scenarios x {self.horizon} "
            f"periods, series={sorted(self.series_by_name)}, "
            f"primary={self.primary!r})"
        )

    def __fingerprint__(self):
        return {
            "primary": self.primary,
            "series": {name: self.series_by_name[name]
                       for name in sorted(self.series_by_name)},
        }

    # --- access -----------------------------------------------------------

    @property
    def names(self) -> tuple:
        return tuple(sorted(self.series_by_name))

    def series(self, name: str) -> np.ndarray:
        """The whole ``(n_scenarios, horizon)`` rectangle for one series."""
        try:
            return self.series_by_name[name]
        except KeyError:
            raise KeyError(
                f"no series {name!r}; this set has {list(self.names)}"
            ) from None

    def ret(self, t: int) -> np.ndarray:
        """Primary-series values for period t, shape (n_scenarios,)."""
        return self.at(self.primary, t)

    def at(self, name: str, t: int) -> np.ndarray:
        """One series' values for period t, shape (n_scenarios,)."""
        if not 0 <= t < self.horizon:
            raise IndexError(
                f"period {t} outside scenario horizon [0, {self.horizon})"
            )
        return self.series(name)[:, t]

    # --- views ------------------------------------------------------------

    def with_primary(self, name: str) -> "ScenarioSet":
        """The same set, read through a different series.

        Lets a template written against ``ret(t)`` be run on the bond series
        instead of the equity one without touching the template.
        """
        return ScenarioSet(series=self.series_by_name, primary=name,
                           source=self.source)

    def single(self, s: int) -> "ScenarioSet":
        """A one-scenario view of scenario ``s`` (for consistency testing)."""
        return ScenarioSet(
            series={name: values[s : s + 1, :]
                    for name, values in self.series_by_name.items()},
            primary=self.primary, source=self.source,
        )

    def truncate(self, horizon: int) -> "ScenarioSet":
        """The first ``horizon`` periods of every series."""
        if not 0 < horizon <= self.horizon:
            raise ValueError(
                f"horizon {horizon} outside (0, {self.horizon}]"
            )
        return ScenarioSet(
            series={name: values[:, :horizon]
                    for name, values in self.series_by_name.items()},
            primary=self.primary, source=self.source,
        )

    # --- generators -------------------------------------------------------

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
