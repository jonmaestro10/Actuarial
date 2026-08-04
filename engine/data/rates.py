"""Interest rate term structure — the VPLA ``RateTable``, made first class.

Rates are **annual effective**, one per payment period, discounted at the
payment frequency:

``df[0] = 1``, ``df[i] = df[i-1] * (1 + rates[i-1]) ** (-1 / freq)``

A list of rates is expanded by repeating each entry ``freq`` times and
holding the last one flat to the horizon, so ``YieldCurve([0.03], freq=12)``
is a flat 3% curve and ``YieldCurve([0.02, 0.025, 0.03], freq=1)`` is a
three-point curve levelling off at 3%.

Behaviour is taken from ``application/rate_table.py`` in jonmaestro10/VPLA,
including ``convert_freq``'s known limitation (documented below); the two
validator bugs recorded in docs/vpla-review.md §6.2 are not carried over.
"""

from __future__ import annotations

from typing import Sequence

import numpy as np

from engine.core.dates import months_per_period

# VPLA sizes every curve and survival vector at 120 years of periods.
DEFAULT_HORIZON_YEARS = 120


class YieldCurve:
    def __init__(self, rates: Sequence[float], freq: int = 12,
                 horizon_years: int = DEFAULT_HORIZON_YEARS):
        months_per_period(freq)  # validates that freq divides 12
        rates = np.asarray(rates, dtype=np.float64).ravel()
        if rates.size == 0:
            raise ValueError("at least one rate is required")
        if np.any(rates <= -1.0):
            raise ValueError("rates at or below -100% are not valid")
        n_periods = horizon_years * freq
        expanded = np.repeat(rates, freq)
        if expanded.size < n_periods:
            expanded = np.concatenate(
                [expanded, np.full(n_periods - expanded.size, expanded[-1])]
            )
        self.freq = freq
        self.horizon_years = horizon_years
        self.n_periods = n_periods
        self.rates = expanded[:n_periods]

    @classmethod
    def flat(cls, rate: float, freq: int = 12,
             horizon_years: int = DEFAULT_HORIZON_YEARS) -> "YieldCurve":
        return cls([rate], freq=freq, horizon_years=horizon_years)

    def discount_factors(self, n_periods: int | None = None) -> np.ndarray:
        """``v_k`` from time 0 to the start of period ``k``.

        The cumulative-product form is the same recursion VPLA writes as a
        Python loop, and is exact to within one rounding of the loop.
        """
        n = self.n_periods if n_periods is None else n_periods
        if n > self.n_periods:
            raise ValueError(
                f"asked for {n} periods, curve covers {self.n_periods}"
            )
        steps = (1.0 + self.rates[: max(n - 1, 0)]) ** (-1.0 / self.freq)
        df = np.empty(n, dtype=np.float64)
        df[0] = 1.0
        if n > 1:
            np.cumprod(steps, out=df[1:])
        return df

    def accumulation_factors(self, n_periods: int | None = None) -> np.ndarray:
        return 1.0 / self.discount_factors(n_periods)

    def forward_rate(self, period: int) -> float:
        """The annual effective rate applying over period ``period``."""
        return float(self.rates[period])

    def convert_freq(self, freq: int) -> "YieldCurve":
        """Re-sample to a new payment frequency.

        VPLA's semantics, kept deliberately: the rate covering each new
        period is the *same annual effective rate* that covered the month it
        starts in, rather than one implied by the discount curve. Going from
        less frequent to more frequent — the normal direction, since rates
        are usually prescribed annually — that is exact. Going the other way
        it picks up whichever rate happens to sit at the period start.
        """
        months_per_period(freq)
        monthly = np.repeat(self.rates, 12 // self.freq)
        index = (np.arange(self.horizon_years * freq) * (12 // freq))
        converted = YieldCurve([0.0], freq=freq, horizon_years=self.horizon_years)
        converted.rates = monthly[index]
        return converted
