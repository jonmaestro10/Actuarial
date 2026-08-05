"""Index crediting for fixed-indexed annuities.

PLAN.md §5.2 asks for **fixed & fixed-indexed annuities (deferred/immediate,
GLWB riders, index crediting)**. RFC-010 built the account; this is the
crediting rule that sits on top of it, and it is the part that makes an FIA
a different product rather than a universal-life contract with a different
name.

An FIA credits nothing at all between contract anniversaries. At each
anniversary it looks at what an equity index did over the year and credits a
rate derived from it — floored at zero, so the account never falls, and
capped or geared so the insurer can hedge what it has promised. The floor is
the whole proposition: the policyholder is buying equity participation
without equity downside, and paying for it in the cap.

**The account never falls, so the credit is a ratchet.** Every anniversary
locks in. That is why an FIA cannot be valued by averaging annual returns
and applying a formula at the end — the path matters, and it matters in a
direction (a bad year is free to the policyholder and expensive to nobody,
because zero is zero) that averaging destroys.

Three designs, and the one that is not what it looks like
--------------------------------------------------------
:class:`AnnualPointToPoint`
    Start of year to end of year, geared by a participation rate, less a
    spread, floored at zero and capped. The plain design, and the one every
    other design is sold against.

:class:`MonthlyAverage`
    The index is averaged over the twelve month-ends rather than read once
    at the end. Sold as "smoothing"; what it actually does is systematically
    truncate a rising year, because the average of a rising path is below
    its endpoint.

:class:`MonthlySum`
    Each month's return is capped, the twelve capped returns are **summed**,
    and only the total is floored at zero. A 2% monthly cap advertises 24% a
    year. It is worth far less than that, and less than it looks against a
    plain annual cap, because **the cap truncates the good months while the
    bad months come through in full**. The asymmetry is the product, and it
    is invisible in any deterministic projection.

Why these accumulate rather than take a path
--------------------------------------------
Each design is written as two running accumulators reset at every
anniversary, rather than as a function of the year's twelve returns. That is
not a stylistic choice: the engine's windowed forward loop prunes the memo
behind a look-back window discovered from the dependency graph, and a
formula reading twelve periods back would need a window twelve deep — traced
from the first three periods, which is exactly where a twelve-period edge
does not exist yet. Two state variables with a one-period look-back are
correct, cheap, and the shape the executor is built for.
"""

from __future__ import annotations

import math

import numpy as np

#: Accumulator names, for anyone reading the template that carries them.
#: ``level`` runs the index forward within the crediting year; ``total``
#: accumulates whatever the design needs to add up. Every design uses one,
#: both, or neither, and resets both at each anniversary.
INIT_LEVEL, INIT_TOTAL = 1.0, 0.0


class IndexCredit:
    """One anniversary crediting rule.

    Subclasses implement :meth:`accumulate`, one projection period at a
    time, and :meth:`credit`, evaluated at the anniversary. Both are pure
    functions of arrays, so a template calls them on a whole
    ``(model point x scenario)`` slab.
    """

    #: Projection periods per year this design needs. A design that reads
    #: the index once a year works at any frequency; one that reads it
    #: monthly needs a monthly projection, and there is no conversion.
    min_freq = 1

    def __init__(self, *, cap: float = math.inf, participation: float = 1.0,
                 spread: float = 0.0, floor: float = 0.0):
        if cap <= 0.0:
            raise ValueError(f"cap {cap} must be positive")
        if participation <= 0.0:
            raise ValueError(f"participation rate {participation} must be positive")
        if spread < 0.0:
            raise ValueError(f"spread {spread} is negative")
        if floor > cap:
            raise ValueError(f"floor {floor} is above the cap {cap}")
        self.cap = cap
        self.participation = participation
        self.spread = spread
        #: The floor is what makes this an *indexed* annuity rather than a
        #: fund. Zero is the near-universal value and the default; a
        #: contract with a positive floor is crediting a guaranteed minimum
        #: on top of the index, and one with a negative floor is a
        #: unit-linked contract in disguise.
        self.floor = floor

    def check_freq(self, freq: int) -> None:
        """Refuse a projection step this design cannot be evaluated on.

        A monthly design on an annual scenario file would have to invent the
        intra-year path, and inventing volatility is not a conversion — the
        same rule the unit-linked template states about scenario returns.
        """
        if freq < self.min_freq:
            raise ValueError(
                f"{type(self).__name__} reads the index {self.min_freq} times "
                f"a year and the projection runs {freq}; it needs a "
                f"projection at least that fine, and an annual scenario "
                "cannot be split into one"
            )

    def __repr__(self) -> str:
        return (f"{type(self).__name__}(cap={self.cap}, "
                f"participation={self.participation}, spread={self.spread}, "
                f"floor={self.floor})")

    def __fingerprint__(self):
        return {"kind": type(self).__name__, "cap": self.cap,
                "participation": self.participation, "spread": self.spread,
                "floor": self.floor}

    def accumulate(self, level, total, index_return):
        """One projection period of the year's accumulation."""
        raise NotImplementedError

    def credit(self, level, total, freq: int):
        """The rate credited at the anniversary."""
        raise NotImplementedError

    def _bound(self, rate):
        """Gear, deduct the spread, and clamp between floor and cap.

        One place, because every design does exactly this to whatever it has
        measured, and three copies of it would be three chances to put the
        spread on the wrong side of the participation rate.
        """
        geared = self.participation * rate - self.spread
        return np.clip(geared, self.floor, self.cap)


class AnnualPointToPoint(IndexCredit):
    """Start of year to end of year.

    ``clip(participation * index_growth - spread, floor, cap)``.
    """

    def accumulate(self, level, total, index_return):
        return level * (1.0 + index_return), total

    def credit(self, level, total, freq: int):
        return self._bound(level - 1.0)


class MonthlyAverage(IndexCredit):
    """The index averaged over the periods of the year rather than read at
    the end of it.

    Sold as smoothing, and it does smooth. It also **truncates a rising
    year**: the average of a path that ends higher than it started is below
    its endpoint, always, so this design credits less than a point-to-point
    one on every up year and the difference is not a fee anybody quotes.
    """

    min_freq = 12

    def accumulate(self, level, total, index_return):
        grown = level * (1.0 + index_return)
        return grown, total + grown

    def credit(self, level, total, freq: int):
        return self._bound(total / freq - 1.0)


class MonthlySum(IndexCredit):
    """Each period's return capped, the capped returns summed, the total
    floored.

    The design whose advertised cap is not the number that matters. A 2%
    monthly cap is quoted as 24% a year; what it delivers is
    ``sum(min(r_m, 2%))``, in which **a good month is truncated and a bad
    month is not**. One −8% month cancels four capped good ones, and the
    annual floor at zero cannot help until the whole year is negative.

    The per-period cap is ``cap``; there is no separate annual cap, because
    the sum of twelve capped returns is already bounded by twelve times it.
    ``floor`` still applies to the total, which is the guarantee the
    contract actually gives.
    """

    min_freq = 12

    def __init__(self, *, cap: float, participation: float = 1.0,
                 spread: float = 0.0, floor: float = 0.0):
        if not math.isfinite(cap):
            raise ValueError(
                "a monthly-sum design needs a finite per-period cap; without "
                "one it is a point-to-point design written the long way"
            )
        super().__init__(cap=cap, participation=participation,
                         spread=spread, floor=floor)

    def accumulate(self, level, total, index_return):
        return level, total + np.minimum(index_return, self.cap)

    def credit(self, level, total, freq: int):
        # `_bound` would clamp the total at `self.cap`, which is the
        # *monthly* cap here and not a limit on the year. Only the floor
        # applies to the sum.
        geared = self.participation * total - self.spread
        return np.maximum(geared, self.floor)
