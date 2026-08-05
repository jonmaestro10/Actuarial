"""US statutory principle-based reserves — VM-20 and VM-21.

PLAN.md §5.3 asks for "**US STAT/GAAP-LDTI**, **VM-20/VM-21** (VA/annuity
reserves — pairs with the VA library)". RFC-015 did the GAAP half; this is
the statutory one, and it is a third kind of overlay again.

- RFC-012's IFRS 17 **reads** a projection.
- RFC-014's Solvency II **re-runs** one on a shocked basis.
- A principle-based reserve **reduces a distribution of them**. The answer
  is not a number the projection produced; it is a statistic over a
  thousand of them, and which statistic is the whole design.

The statistic is a conditional tail expectation
-----------------------------------------------
``CTE(70)`` is the **average of the worst 30%** of scenario results. It is
not the 70th percentile, and the two are routinely conflated: a percentile
is a point on the distribution and says nothing about what lies beyond it,
while a CTE is the mean of everything beyond and moves when the tail moves.

That difference is why the standard uses it, and it is a mathematical
property rather than a preference. **CTE is coherent and value-at-risk is
not**: CTE is subadditive, so splitting a book into two and adding the
reserves can never produce less than reserving the book whole. VaR has no
such guarantee and a counterexample is easy to construct — which is
demonstrated in tests/test_pbr.py rather than asserted here.

The per-scenario number is a greatest present value
---------------------------------------------------
Each scenario contributes a **greatest present value of accumulated
deficiency**. Roll starting assets forward with the scenario's own
cashflows and earned rates; wherever the accumulated surplus goes negative,
discount that deficiency back to the valuation date; take the **largest**
such present value over the whole projection.

The word "greatest" is the whole mechanic. A path that dips underwater in
year 12 and recovers by year 30 still needs the money in year 12, so a
terminal measure would report nothing and a maximum reports what the
contract actually costs to support.

What is here and what is not
----------------------------
Here: CTE at any level, the accumulated-deficiency roll, scenario reserves,
the stochastic reserve, and the three-way minimum VM-20 takes. Not here:
the prescribed assumption sets, the exclusion tests, VM-21's standard
projection amount, and the asset model that would make starting assets and
earned rates something other than inputs. See docs/rfc-016-pbr.md.
"""

from __future__ import annotations

import math

import numpy as np

#: The tail level VM-20 and VM-21 both prescribe.
CTE_LEVEL = 0.70


def tail_count(n_scenarios: int, level: float = CTE_LEVEL) -> int:
    """How many scenarios fall in the tail at ``level``.

    ``ceil(n * (1 - level))``, and at least one. Rounding **up** is the
    conservative direction and the usual reading: a tail of 300.3 scenarios
    is taken as 301, not 300, because dropping the 301st would drop the
    worst part of it.

    The snap to an exact integer before the ceiling is not defensive
    tidying, it is the whole correctness of this function at the levels the
    standard prescribes. ``1 - 0.70`` is ``0.30000000000000004``, so
    ``n * (1 - level)`` lands a hair **above** the integer at every round
    scenario count — 1,000 scenarios give 300.00000000000006 and a naive
    ceiling takes 301. The tail would be one scenario deeper than
    prescribed on every run at 1,000 or 10,000 paths, silently, and the
    error is invisible in the answer because one extra scenario moves a CTE
    only slightly. A genuinely fractional tail is untouched: 1,001
    scenarios give 300.3 and still round up to 301.
    """
    if not 0.0 <= level < 1.0:
        raise ValueError(f"CTE level {level} outside [0, 1)")
    if n_scenarios < 1:
        raise ValueError("a tail measure needs at least one scenario")
    raw = n_scenarios * (1.0 - level)
    nearest = round(raw)
    if math.isclose(raw, nearest, rel_tol=1e-12, abs_tol=1e-9):
        raw = nearest
    return max(1, math.ceil(raw))


def cte(values, level: float = CTE_LEVEL) -> float:
    """Conditional tail expectation: the mean of the worst ``1 - level``.

    Larger values are worse — these are reserves and deficiencies, not
    returns — so the tail is the **upper** one.

    At ``level = 0`` this is the mean of everything, which is the correct
    degenerate answer and worth having rather than excluding.
    """
    values = np.asarray(values, dtype=np.float64).ravel()
    k = tail_count(values.size, level)
    worst = np.partition(values, values.size - k)[values.size - k:]
    return float(worst.mean())


def value_at_risk(values, level: float = CTE_LEVEL) -> float:
    """The ``level`` quantile — the point a CTE averages beyond.

    Here to be compared against, not to be used: see the subadditivity
    demonstration in the tests. ``numpy``'s linear interpolation is used
    deliberately, because the sharpest counterexamples to VaR's coherence
    are not artefacts of a rounding rule.
    """
    values = np.asarray(values, dtype=np.float64).ravel()
    return float(np.quantile(values, level))


def accumulated_surplus(net_cashflows, earned_rates, starting_assets=0.0
                        ) -> np.ndarray:
    """Assets on hand at each of ``n + 1`` dates, per scenario.

    ``S[0] = starting assets``; each period the net cashflow is received and
    the result earns the scenario's rate:

        S[t + 1] = (S[t] + net cashflow[t]) * (1 + earned[t])

    ``net_cashflows`` and ``earned_rates`` are ``(periods, scenarios)``,
    which is the shape ``run_stochastic`` produces once a template's series
    have been combined. A positive cashflow is money coming in.
    """
    net_cashflows = np.atleast_2d(np.asarray(net_cashflows, dtype=np.float64))
    earned_rates = np.atleast_2d(np.asarray(earned_rates, dtype=np.float64))
    if earned_rates.shape != net_cashflows.shape:
        raise ValueError(
            f"earned rates {earned_rates.shape} and net cashflows "
            f"{net_cashflows.shape} cover different projections"
        )
    n, scenarios = net_cashflows.shape
    surplus = np.empty((n + 1, scenarios), dtype=np.float64)
    surplus[0] = starting_assets
    for t in range(n):
        surplus[t + 1] = (surplus[t] + net_cashflows[t]) * (1.0 + earned_rates[t])
    return surplus


def path_discount_factors(earned_rates) -> np.ndarray:
    """Discount factors along each scenario's **own** path.

    A principle-based reserve discounts at the rate the scenario earns, not
    at a valuation rate chosen once — the deficiency and the discounting
    are two halves of one path and using a different rate for each would
    price a scenario that does not exist.
    """
    earned_rates = np.atleast_2d(np.asarray(earned_rates, dtype=np.float64))
    n, scenarios = earned_rates.shape
    factors = np.ones((n + 1, scenarios), dtype=np.float64)
    np.cumprod(1.0 / (1.0 + earned_rates), axis=0, out=factors[1:])
    return factors


def greatest_present_value_of_accumulated_deficiency(
        net_cashflows, earned_rates, starting_assets=0.0) -> np.ndarray:
    """The GPVAD for each scenario.

    The largest present value, over every date in the projection, of the
    amount by which accumulated assets have gone negative. Floored at zero:
    a scenario that never goes underwater needs no reserve, and a *surplus*
    is not a negative reserve.

    The **maximum** is the point. A path that dips underwater in year 12 and
    recovers by year 30 still needed the money in year 12; a terminal
    measure would report nothing at all for it.
    """
    surplus = accumulated_surplus(net_cashflows, earned_rates, starting_assets)
    discounted_deficiency = -surplus * path_discount_factors(earned_rates)
    return np.maximum(discounted_deficiency.max(axis=0), 0.0)


def deficiency_dates(net_cashflows, earned_rates, starting_assets=0.0
                     ) -> np.ndarray:
    """Which date each scenario's greatest deficiency falls on.

    Reported because it is checkable and surprising: on a contract whose
    guarantee bites mid-life, the greatest deficiency is overwhelmingly
    **interior** rather than terminal, which is the whole argument for a
    maximum over a terminal measure.
    """
    surplus = accumulated_surplus(net_cashflows, earned_rates, starting_assets)
    discounted_deficiency = -surplus * path_discount_factors(earned_rates)
    return discounted_deficiency.argmax(axis=0)


def scenario_reserves(net_cashflows, earned_rates, starting_assets=0.0
                      ) -> np.ndarray:
    """Starting assets plus the GPVAD, per scenario.

    The quantity the stochastic reserve is a tail expectation *of*. With no
    starting assets it is the GPVAD itself, which is the common
    simplification and the shape a greenfield valuation takes.
    """
    return starting_assets + greatest_present_value_of_accumulated_deficiency(
        net_cashflows, earned_rates, starting_assets
    )


def stochastic_reserve(net_cashflows, earned_rates, starting_assets=0.0,
                       level: float = CTE_LEVEL) -> float:
    """``CTE(level)`` of the scenario reserves — VM-20 §5 and VM-21 §4."""
    return cte(scenario_reserves(net_cashflows, earned_rates, starting_assets),
               level)


def tail_standard_error(values, level: float = CTE_LEVEL) -> float:
    """Sampling error on a CTE, from the tail scenarios alone.

    The number that decides how many scenarios a run needs, and it is
    smaller than it looks only if you forget the divisor. **A CTE(70) over
    1,000 scenarios is an average of 300**, so its standard error falls
    like ``1 / sqrt(n * (1 - level))`` and not like ``1 / sqrt(n)`` —
    tripling the run buys the same precision a threefold increase in the
    tail would.
    """
    values = np.asarray(values, dtype=np.float64).ravel()
    k = tail_count(values.size, level)
    worst = np.partition(values, values.size - k)[values.size - k:]
    if k < 2:
        return math.inf
    return float(worst.std(ddof=1) / math.sqrt(k))


class MinimumReserve:
    """VM-20's three-way maximum.

    The reserve is the **greatest** of a formulaic floor, a single
    deterministic projection, and the stochastic reserve — so improving the
    one that is not binding changes nothing at all, and knowing which binds
    is the first question about any block.

    ``net_premium`` is VM-20's formulaic net premium reserve;
    ``deterministic`` is the gross premium valuation on prudent estimate
    assumptions under one prescribed scenario; ``stochastic`` is the CTE.
    Any of the three may be omitted where an exclusion test has been passed,
    and a reserve with all three omitted is an error rather than a zero.
    """

    def __init__(self, *, net_premium: float | None = None,
                 deterministic: float | None = None,
                 stochastic: float | None = None):
        components = {"net_premium": net_premium,
                      "deterministic": deterministic,
                      "stochastic": stochastic}
        self.components = {k: float(v) for k, v in components.items()
                           if v is not None}
        if not self.components:
            raise ValueError(
                "every component was excluded; a reserve with nothing in it "
                "is a missing calculation, not a zero"
            )

    @property
    def value(self) -> float:
        return max(self.components.values())

    @property
    def binding(self) -> str:
        return max(self.components, key=self.components.get)

    def headroom(self) -> dict:
        """How far each component sits below the binding one.

        Zero for the component that binds. A component with large headroom
        can move a long way before it changes the reserve at all, which is
        what makes "our stochastic reserve fell" a claim worth checking
        against this before it is worth acting on.
        """
        return {name: self.value - value
                for name, value in self.components.items()}

    def __repr__(self) -> str:
        return (f"MinimumReserve({self.value:,.2f}, binding="
                f"{self.binding!r})")

    def __fingerprint__(self):
        return dict(self.components)
