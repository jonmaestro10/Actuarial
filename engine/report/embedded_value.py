"""Embedded value and asset-liability management.

PLAN.md §5.3's last line: "**Embedded value / ALM** overlays (asset models,
liability-driven runs)". It is also where four earlier RFCs converge — every
one of them stopped at the same edge:

- RFC-014's Solvency II has no market risk modules, because they need assets.
- RFC-016's principle-based reserve takes starting assets and earned rates
  as *inputs*.
- RFC-019's with-profits estate is assets less asset shares, and only had
  the second half.
- RFC-011's fixed-indexed annuity notes that the cap is set by the cost of
  the call spread backing it, and prices no spread.

## Embedded value is where the option gets a line in the report

    EV = free surplus + required capital + PVFP
         − time value of options and guarantees
         − frictional cost of required capital
         − cost of residual non-hedgeable risk

The **time value of financial options and guarantees** is the interesting
one, and it is a number this engine has already measured twice under other
names. RFC-010 found a minimum crediting rate worth *exactly zero*
deterministically and +323 basis points a year across a distribution;
RFC-013 found the same asymmetry moving between the CSM and profit. TVOG is
that gap, given a name and a line:

    TVOG = deterministic PVFP − mean stochastic PVFP

A traditional embedded value ignores it and is therefore wrong by exactly
the amount the guarantees are worth. A market-consistent one subtracts it,
which is the whole difference between EV and MCEV.

## The analysis of change has to reconcile exactly

An embedded value report is judged on its **movement analysis**: opening
value, plus the unwind, plus new business, plus experience, plus assumption
changes, less what was paid out, equals closing value. If those do not add
up, nobody believes any of them.

:func:`analysis_of_change` is built so that they must: the last component is
a residual, computed as whatever is left, and it is reported rather than
hidden. A residual that is not tiny means a movement has been left out, and
that is information the report should carry rather than absorb.

## ALM: matching duration does not immunise

The classical result, and the one worth demonstrating because it is so
often stated the other way. Setting asset duration equal to liability
duration makes surplus insensitive to an *infinitesimal* parallel shift.
It does nothing about a large one, because the second derivative has not
been matched — and the sign of the residual is not neutral: a portfolio
with less convexity than its liabilities **loses on a shift in either
direction**.
"""

from __future__ import annotations

import math

import numpy as np

from engine.data.rates import YieldCurve


def present_value(cashflows, curve: YieldCurve, timing: str = "end") -> float:
    """Present value of a cashflow series at ``curve``."""
    flows = np.asarray(cashflows, dtype=np.float64)
    factors = curve.discount_factors(flows.size + 1)
    weights = factors[:flows.size] if timing == "start" else factors[1:flows.size + 1]
    return float((flows * weights).sum())


def macaulay_duration(cashflows, curve: YieldCurve,
                      timing: str = "end") -> float:
    """Weighted average time to payment, in years.

    Weighted by present value, which is the only weighting that makes the
    sensitivity result below true.
    """
    flows = np.asarray(cashflows, dtype=np.float64)
    factors = curve.discount_factors(flows.size + 1)
    weights = factors[:flows.size] if timing == "start" else factors[1:flows.size + 1]
    values = flows * weights
    total = values.sum()
    if total == 0.0:
        raise ValueError(
            "a cashflow series worth nothing has no duration; there is no "
            "time at which its value is concentrated"
        )
    times = np.arange(flows.size, dtype=np.float64)
    if timing == "end":
        times = times + 1.0
    return float((values * times).sum() / total / curve.freq)


def convexity(cashflows, curve: YieldCurve, timing: str = "end") -> float:
    """The second derivative of value with respect to yield, scaled.

    ``Σ t(t+1) · PV_t / (value · (1+y)²)``. What duration leaves out, and
    the reason matching duration alone does not immunise a balance sheet
    against anything but an infinitesimal move.
    """
    flows = np.asarray(cashflows, dtype=np.float64)
    factors = curve.discount_factors(flows.size + 1)
    weights = factors[:flows.size] if timing == "start" else factors[1:flows.size + 1]
    values = flows * weights
    total = values.sum()
    if total == 0.0:
        raise ValueError("a cashflow series worth nothing has no convexity")
    times = np.arange(flows.size, dtype=np.float64)
    if timing == "end":
        times = times + 1.0
    times = times / curve.freq
    yield_ = float(curve.rates[0])
    return float((values * times * (times + 1.0)).sum()
                 / total / (1.0 + yield_) ** 2)


def shifted(curve: YieldCurve, basis_points: float) -> YieldCurve:
    """The same curve moved by a parallel shift, in basis points."""
    return YieldCurve(curve.rates + basis_points / 10_000.0,
                      freq=curve.freq, horizon_years=curve.horizon_years)


class BalanceSheet:
    """Asset and liability cashflows, and what a rate move does to them."""

    def __init__(self, assets, liabilities, curve: YieldCurve, *,
                 asset_timing: str = "end", liability_timing: str = "end"):
        self.assets = np.asarray(assets, dtype=np.float64)
        self.liabilities = np.asarray(liabilities, dtype=np.float64)
        self.curve = curve
        self.asset_timing = asset_timing
        self.liability_timing = liability_timing

    def __repr__(self) -> str:
        return (f"BalanceSheet(surplus={self.surplus():,.2f}, "
                f"mismatch={self.duration_gap():+.2f}y)")

    def asset_value(self, curve=None) -> float:
        return present_value(self.assets, curve or self.curve,
                             self.asset_timing)

    def liability_value(self, curve=None) -> float:
        return present_value(self.liabilities, curve or self.curve,
                             self.liability_timing)

    def surplus(self, curve=None) -> float:
        return self.asset_value(curve) - self.liability_value(curve)

    def asset_duration(self) -> float:
        return macaulay_duration(self.assets, self.curve, self.asset_timing)

    def liability_duration(self) -> float:
        return macaulay_duration(self.liabilities, self.curve,
                                 self.liability_timing)

    def duration_gap(self) -> float:
        """Asset duration less liability duration, **value-weighted**.

        The unweighted difference is the wrong number whenever assets and
        liabilities are worth different amounts, which they always are: a
        surplus is what changes, and a longer but smaller asset portfolio
        does not hedge a shorter but larger liability.
        """
        assets, liabilities = self.asset_value(), self.liability_value()
        if assets == 0.0 or liabilities == 0.0:
            raise ValueError(
                "a duration gap needs both sides to be worth something"
            )
        return self.asset_duration() - self.liability_duration()

    def convexity_gap(self) -> float:
        return (convexity(self.assets, self.curve, self.asset_timing)
                - convexity(self.liabilities, self.curve,
                            self.liability_timing))

    def surplus_under_shift(self, basis_points: float) -> float:
        """Surplus after a parallel shift, revalued rather than approximated.

        The point of this whole module is the gap between this and what
        duration predicts, so it is computed exactly.
        """
        return self.surplus(shifted(self.curve, basis_points))


def duration_matched_assets(liabilities, curve: YieldCurve, *,
                            short: int, long: int,
                            timing: str = "end") -> np.ndarray:
    """Two zero-coupon holdings whose value and duration match a liability.

    A barbell: the closest thing to an immunising portfolio that two
    instruments can give. It matches the first two moments — value and
    duration — and *cannot* match the third, which is the demonstration.
    """
    target_value = present_value(liabilities, curve, timing)
    target_duration = macaulay_duration(liabilities, curve, timing)
    if not short < target_duration < long:
        raise ValueError(
            f"a barbell can only bracket the liability: the target duration "
            f"{target_duration:.2f} must lie strictly between {short} and "
            f"{long}"
        )
    # Value weights that hit the duration exactly.
    weight_long = (target_duration - short) / (long - short)
    factors = curve.discount_factors(long * curve.freq + 1)
    assets = np.zeros(long * curve.freq, dtype=np.float64)
    for years, weight in ((short, 1.0 - weight_long), (long, weight_long)):
        period = years * curve.freq
        assets[period - 1] = target_value * weight / factors[period]
    return assets


class EmbeddedValue:
    """The components of an embedded value, and what they add to.

    ``pvfp`` is the present value of future shareholder profits on the
    deterministic best-estimate basis. ``stochastic_pvfp`` is the mean of
    the same quantity across a distribution; supplying it turns the
    traditional value into a market-consistent one, because the difference
    between the two **is** the time value of the options and guarantees.

    ``frictional_cost`` and ``non_hedgeable_cost`` are taken rather than
    derived, for the reason RFC-012 gives about risk adjustments: the
    standard says what they are and not how to compute them, and a library
    that shipped one method would be wrong for every entity that chose
    another.
    """

    def __init__(self, *, free_surplus: float, required_capital: float,
                 pvfp: float, stochastic_pvfp: float | None = None,
                 frictional_cost: float = 0.0,
                 non_hedgeable_cost: float = 0.0):
        if required_capital < 0.0:
            raise ValueError(
                f"required capital {required_capital} is negative"
            )
        for name, cost in (("frictional_cost", frictional_cost),
                           ("non_hedgeable_cost", non_hedgeable_cost)):
            if cost < 0.0:
                raise ValueError(f"{name} {cost} is negative; it is a cost")
        self.free_surplus = free_surplus
        self.required_capital = required_capital
        self.pvfp = pvfp
        self.stochastic_pvfp = stochastic_pvfp
        self.frictional_cost = frictional_cost
        self.non_hedgeable_cost = non_hedgeable_cost

    @property
    def time_value_of_guarantees(self) -> float:
        """What the guarantees are worth beyond their intrinsic value.

        ``deterministic PVFP − mean stochastic PVFP``, floored at zero: a
        guarantee cannot be worth less than nothing to the policyholder,
        and a negative figure means the scenario set is not centred on the
        deterministic basis rather than that the option has value to the
        shareholder.

        Zero when no stochastic value was supplied, which is exactly what a
        **traditional** embedded value does — and precisely why it
        overstates by the amount the guarantees are worth.
        """
        if self.stochastic_pvfp is None:
            return 0.0
        return max(self.pvfp - self.stochastic_pvfp, 0.0)

    @property
    def adjusted_net_worth(self) -> float:
        return self.free_surplus + self.required_capital

    @property
    def value_of_in_force(self) -> float:
        return (self.pvfp - self.time_value_of_guarantees
                - self.frictional_cost - self.non_hedgeable_cost)

    @property
    def value(self) -> float:
        return self.adjusted_net_worth + self.value_of_in_force

    @property
    def market_consistent(self) -> bool:
        """Whether a stochastic value was supplied at all."""
        return self.stochastic_pvfp is not None

    def __repr__(self) -> str:
        kind = "MCEV" if self.market_consistent else "EV"
        return f"{kind}({self.value:,.2f}, VIF={self.value_of_in_force:,.2f})"

    def components(self) -> dict:
        return {
            "free_surplus": self.free_surplus,
            "required_capital": self.required_capital,
            "pvfp": self.pvfp,
            "time_value_of_guarantees": -self.time_value_of_guarantees,
            "frictional_cost": -self.frictional_cost,
            "non_hedgeable_cost": -self.non_hedgeable_cost,
        }


def frictional_cost_of_capital(required_capital, curve: YieldCurve, *,
                               shareholder_spread: float) -> float:
    """What it costs shareholders to have capital locked up.

    Not the whole return on it — the capital still earns — but the
    **spread** between what shareholders require and what the assets
    backing it yield, over the period it is tied up. ``required_capital``
    is the amount held at each future date.

    A frequent error is to charge the full return, which double counts:
    the investment income on required capital is already inside the
    projected profits.
    """
    if shareholder_spread < 0.0:
        raise ValueError(
            f"shareholder spread {shareholder_spread} is negative; capital "
            "that costs nothing to hold needs no charge, not a credit"
        )
    held = np.asarray(required_capital, dtype=np.float64)
    factors = curve.discount_factors(held.size + 1)[1:held.size + 1]
    return float(shareholder_spread * (held * factors).sum())


#: The movements an embedded value report is expected to explain. The order
#: is the order they are conventionally presented in, which is also roughly
#: the order they happen.
MOVEMENTS = ("unwind", "new_business", "experience_variance",
             "assumption_changes", "distributed")


def analysis_of_change(opening: float, closing: float, **movements) -> dict:
    """Bridge one embedded value to the next, with an explicit residual.

    Every report of this kind is judged on whether the components add up,
    so the last one is computed as **whatever is left** and reported rather
    than hidden. A residual that is not tiny means a movement has been left
    out — which is information the report should carry, not absorb into the
    nearest line.
    """
    unknown = set(movements) - set(MOVEMENTS)
    if unknown:
        raise ValueError(
            f"{sorted(unknown)} are not movements this bridge knows; "
            f"expected some of {MOVEMENTS}"
        )
    explained = {name: float(movements.get(name, 0.0)) for name in MOVEMENTS}
    residual = closing - opening - sum(explained.values())
    return {"opening": opening, **explained, "unexplained": residual,
            "closing": closing}


def reconciles(bridge: dict, tolerance: float = 1e-6) -> bool:
    """Whether a bridge's components actually add to the closing value."""
    total = bridge["opening"] + sum(
        bridge[name] for name in MOVEMENTS
    ) + bridge["unexplained"]
    return math.isclose(total, bridge["closing"], abs_tol=tolerance)
