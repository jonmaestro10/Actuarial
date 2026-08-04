"""Assumption bundle for basis-driven templates.

``engine.data.assumptions.Assumptions`` is the flat-rate bundle the annual
toy templates use: one mortality table by integer age, scalar lapse and
interest. This is the real one — the VPLA basis promoted in RFC-002 — for
templates that run on calendar dates at a payment frequency:

- a :class:`~engine.data.mortality.MortalityBasis`, carrying fractional-age
  splits, improvement scales and a limiting age;
- a :class:`~engine.data.rates.YieldCurve` at the same frequency.

The two are checked against each other on construction, because a monthly
curve paired with an annual axis is the kind of mistake that produces a
plausible number rather than an error.
"""

from __future__ import annotations

from engine.data.mortality import MortalityBasis
from engine.data.rates import YieldCurve


class ValuationBasis:
    def __init__(self, *, mortality: MortalityBasis, curve: YieldCurve):
        self.mortality = mortality
        self.curve = curve

    @property
    def freq(self) -> int:
        return self.curve.freq

    def check_axis(self, axis) -> None:
        """Reject an axis whose frequency or horizon the curve cannot serve."""
        if axis.freq != self.curve.freq:
            raise ValueError(
                f"axis frequency {axis.freq} does not match curve frequency "
                f"{self.curve.freq}"
            )
        if axis.n_periods > self.curve.n_periods:
            raise ValueError(
                f"axis covers {axis.n_periods} periods, curve covers "
                f"{self.curve.n_periods}"
            )

    def survival(self, axis, dob, sex):
        """``(n_policies, n_periods)`` survival curves over ``axis``."""
        self.check_axis(axis)
        return self.mortality.survival_curve(
            dob, axis.valuation, sex, axis.freq, axis.n_periods
        )

    def discount(self, axis):
        """``(n_periods,)`` discount factors over ``axis``."""
        self.check_axis(axis)
        return self.curve.discount_factors(axis.n_periods)
