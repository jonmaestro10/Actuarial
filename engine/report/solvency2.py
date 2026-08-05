"""Solvency II: technical provisions and the standard-formula SCR.

PLAN.md §5.3 asks for "**Solvency II** (BEL, risk margin, SCR standard
formula stresses)". This is a different kind of overlay from RFC-012's
IFRS 17, and the difference is architectural rather than accounting.

**IFRS 17 reads a projection. Solvency II re-runs one.**

The SCR is defined as the fall in own funds under a prescribed shock, and a
shock is a change of *assumption* — 15% more mortality, half the lapses, a
fifth of the annuitants living longer. There is no formula for what that
does to a liability; the only way to find out is to project it again on the
stressed basis. So this module drives the engine rather than consuming its
output, which makes it the first thing here that needs the projection to be
cheap.

That is a design statement, not a complaint: it is exactly why PLAN.md §4
puts vectorization and scale-out ahead of product breadth. A standard-formula
life SCR is on the order of a dozen full projections of the whole book, and
a nested-stochastic one is far worse.

The three numbers
-----------------
- **Best estimate liability** — the present value of the fulfilment
  cashflows on the base assumptions. Already available from any run.
- **Risk margin** — the cost of holding capital against the non-hedgeable
  risks until the book runs off, at a 6% cost-of-capital rate.
- **SCR** — the 99.5% one-year loss, built by shocking each risk in turn and
  aggregating with a correlation matrix.

Where the standard is a judgement rather than a calculation, this module
takes the judgement as an input. Where it is arithmetic, it does the
arithmetic and checks it.

The rules that are easy to get wrong
------------------------------------
**The lapse module is a maximum, not a sum.** Three lapse shocks — rates up
50%, rates down 50%, and a 40% mass discontinuance — and the module is the
*worst* of them, because a book cannot simultaneously lapse more and less.
Which one bites is a property of the product and not of the standard, and
the two answers can be a long way apart.

**The correlation matrix has to be positive semi-definite.** Otherwise
``sqrt(v' C v)`` is not a norm, and an aggregate can come out *below* the
largest module it aggregates — a diversification benefit larger than
diversification. Checked on construction, because a matrix that fails it
produces a plausible-looking number rather than an error.
"""

from __future__ import annotations

import copy
import math

import numpy as np

from engine.data.assumptions import Assumptions, MortalityTable
from engine.data.expenses import Expenses
from engine.data.mortality import split_annual
from engine.data.rates import YieldCurve

#: Cost-of-capital rate for the risk margin, Article 39 of the Delegated
#: Regulation. A prescribed number, not an assumption.
COST_OF_CAPITAL = 0.06

#: The life underwriting shocks this module can apply, from the Delegated
#: Regulation. Each is the *instantaneous permanent* change the standard
#: prescribes; the market shocks that need an asset model are not here.
STANDARD_SHOCKS = {
    "mortality": dict(mortality=1.15),
    "longevity": dict(mortality=0.80),
    "lapse_up": dict(lapse=1.50),
    "lapse_down": dict(lapse=0.50),
    "mass_lapse": dict(mass_lapse=0.40),
    "expense": dict(expense=1.10, expense_inflation=0.01),
    "cat": dict(mortality_addition=0.0015, addition_years=1),
}

#: The lapse sub-module is the worst of these three and never their sum.
LAPSE_SHOCKS = ("lapse_up", "lapse_down", "mass_lapse")


class ScaledMortality:
    """A mortality basis with every annual rate multiplied by a factor.

    The Solvency II mortality and longevity shocks are exactly this — "a
    permanent 15% increase in the mortality rates used" — so the wrapper is
    a faithful reading rather than an approximation.

    The scaling is applied to the **annual** rate, and the sub-annual split
    then divides the stressed year through
    :func:`engine.data.mortality.split_annual` — the same function the
    unstressed basis uses. Scaling a periodic rate instead would stress the
    split as well as the mortality, which is a different and smaller shock.
    """

    def __init__(self, base, factor: float, *, addition: float = 0.0,
                 addition_years: int = 0):
        if factor < 0.0:
            raise ValueError(f"mortality factor {factor} is negative")
        if addition < 0.0:
            raise ValueError(f"mortality addition {addition} is negative")
        self.base = base
        self.factor = factor
        #: Absolute addition to ``q``, for the catastrophe shock — 0.15
        #: percentage points in the first year only. Absolute and not
        #: relative, because a pandemic does not scale with a life's
        #: underlying mortality.
        self.addition = addition
        self.addition_years = addition_years
        self.min_age, self.max_age = base.min_age, base.max_age
        self.year_start = getattr(base, "year_start", 0)

    def __repr__(self) -> str:
        return (f"ScaledMortality(x{self.factor}, +{self.addition} for "
                f"{self.addition_years}y)")

    def __fingerprint__(self):
        return {"base": self.base, "factor": self.factor,
                "addition": self.addition,
                "addition_years": self.addition_years}

    @property
    def ages(self) -> range:
        return self.base.ages

    def clip_age(self, ages):
        return self.base.clip_age(ages)

    def _addition_at(self, year):
        if not self.addition or year is None:
            return 0.0
        elapsed = np.asarray(year) - self.year_start
        return self.addition * (elapsed < self.addition_years)

    def q_at(self, ages, sex=None, year=None, duration=None):
        q = self.base.q_at(ages, sex=sex, year=year, duration=duration)
        return np.minimum(q * self.factor + self._addition_at(year), 1.0)

    def periodic_rate(self, ages, sub_period, freq, sex=None, year=None,
                      method="udd", duration=None):
        q = self.q_at(ages, sex=sex, year=year, duration=duration)
        return split_annual(q, sub_period, freq, method=method)


class Stress:
    """A named transformation of an assumption set.

    Applying one returns a **new** assumption set; nothing is mutated, so a
    base run and a dozen stressed runs can share the same object and the
    fingerprint of each is distinct.

    ``mass_lapse`` is different in kind from the others. It is not a change
    of rate but a one-off discontinuance of a proportion of the book at
    time zero, so it scales the *starting policy count* rather than an
    assumption — which is why it is applied to the model points and
    :meth:`apply` cannot express it alone.
    """

    def __init__(self, name: str, *, mortality: float = 1.0,
                 lapse: float = 1.0, expense: float = 1.0,
                 expense_inflation: float = 0.0, interest: float = 0.0,
                 mass_lapse: float = 0.0, mortality_addition: float = 0.0,
                 addition_years: int = 0):
        if not 0.0 <= mass_lapse < 1.0:
            raise ValueError(f"mass lapse {mass_lapse} outside [0, 1)")
        self.name = name
        self.mortality = mortality
        self.lapse = lapse
        self.expense = expense
        self.expense_inflation = expense_inflation
        #: Absolute shift in the valuation rate, in percentage points.
        self.interest = interest
        self.mass_lapse = mass_lapse
        self.mortality_addition = mortality_addition
        self.addition_years = addition_years

    @classmethod
    def standard(cls, name: str) -> "Stress":
        """One of the prescribed life underwriting shocks by name."""
        if name not in STANDARD_SHOCKS:
            raise ValueError(
                f"unknown standard shock {name!r}; this module carries "
                f"{tuple(STANDARD_SHOCKS)}"
            )
        return cls(name, **STANDARD_SHOCKS[name])

    @classmethod
    def base(cls) -> "Stress":
        """The identity. Applying it returns an assumption set that
        projects the same numbers, which is what makes the unstressed run
        and the stressed runs the same code path."""
        return cls("base")

    def __repr__(self) -> str:
        return f"Stress({self.name!r})"

    def __bool__(self) -> bool:
        return bool(
            self.mortality != 1.0 or self.lapse != 1.0 or self.expense != 1.0
            or self.expense_inflation or self.interest or self.mass_lapse
            or self.mortality_addition
        )

    def __fingerprint__(self):
        return {"name": self.name, "mortality": self.mortality,
                "lapse": self.lapse, "expense": self.expense,
                "expense_inflation": self.expense_inflation,
                "interest": self.interest, "mass_lapse": self.mass_lapse,
                "mortality_addition": self.mortality_addition,
                "addition_years": self.addition_years}

    def apply(self, assumptions: Assumptions) -> Assumptions:
        """The stressed assumption set.

        A shallow copy with the shocked fields replaced. Everything the
        stress does not name is shared with the base rather than rebuilt,
        so a stress cannot quietly change something it was not asked to.
        """
        stressed = copy.copy(assumptions)
        if (self.mortality != 1.0 or self.mortality_addition):
            stressed.mortality = ScaledMortality(
                assumptions.mortality, self.mortality,
                addition=self.mortality_addition,
                addition_years=self.addition_years,
            )
        if self.lapse != 1.0:
            shocked = min(assumptions.lapse * self.lapse, 0.999999)
            stressed.dynamic_lapse = copy.copy(assumptions.dynamic_lapse)
            stressed.dynamic_lapse.base = shocked
            stressed.lapse = shocked
        if self.expense != 1.0 or self.expense_inflation:
            stressed.expenses = _shock_expenses(
                assumptions.expenses, self.expense, self.expense_inflation
            )
        if self.interest:
            stressed.interest = assumptions.interest + self.interest
        return stressed

    def apply_to_points(self, modelpoints):
        """Model points after a mass discontinuance.

        Every other shock is a change of assumption. A mass lapse is an
        event: 40% of the book walks out at time zero, so what changes is
        how many policies there are. Returns the points unchanged when the
        stress carries no mass lapse, so a caller applies it
        unconditionally.
        """
        if not self.mass_lapse:
            return list(modelpoints)
        survivors = 1.0 - self.mass_lapse
        shocked = []
        for point in modelpoints:
            clone = copy.copy(point)
            clone.init_pols = point.init_pols * survivors
            shocked.append(clone)
        return shocked


def _shock_expenses(expenses: Expenses, factor: float,
                    inflation_addition: float) -> Expenses:
    """An expense basis with every loading scaled and inflation raised.

    Rebuilt rather than mutated — ``ExpenseScale`` is frozen, which is the
    right shape for an assumption and means a stress cannot reach back into
    the base basis it was derived from.

    Every scale is scaled, on all three bases. A shock to "expenses" that
    missed the claim expenses, or the per-mille-of-sum-assured loading,
    would understate the module by whatever share of the expense base they
    happen to be.
    """
    def scale(component):
        return type(component)(
            per_policy=component.per_policy * factor,
            percent_premium=component.percent_premium * factor,
            per_mille_sum_assured=component.per_mille_sum_assured * factor,
        )

    return Expenses(
        initial=scale(expenses.initial),
        renewal=scale(expenses.renewal),
        claim=scale(expenses.claim),
        inflation=expenses.inflation + inflation_addition,
    )


class CorrelationMatrix:
    """A symmetric correlation matrix over named risks.

    Validated on construction rather than trusted, because every failure
    mode here produces a **number** rather than an error:

    - a non-unit diagonal silently rescales a module;
    - an asymmetric entry makes the aggregate depend on the order the risks
      happen to be listed in;
    - a matrix that is not positive semi-definite makes ``v' C v`` capable
      of going **negative**, at which point the square root is not defined
      and any floor at zero reports an SCR of zero. Three modules of 100
      each under ``[[1,-.9,-.9],[-.9,1,-.9],[-.9,-.9,1]]`` — symmetric,
      unit diagonal, every entry inside [-1, 1] — give ``v' C v = -24,000``
      and therefore no capital requirement at all.
    """

    def __init__(self, risks, matrix, *, tolerance: float = 1e-9):
        self.risks = tuple(risks)
        matrix = np.asarray(matrix, dtype=np.float64)
        n = len(self.risks)
        if matrix.shape != (n, n):
            raise ValueError(
                f"matrix is {matrix.shape} for {n} risks"
            )
        if len(set(self.risks)) != n:
            raise ValueError("risk names must be distinct")
        if not np.allclose(np.diag(matrix), 1.0, atol=tolerance):
            raise ValueError("the diagonal of a correlation matrix is all ones")
        if not np.allclose(matrix, matrix.T, atol=tolerance):
            raise ValueError(
                "the matrix is not symmetric; the aggregate would depend on "
                "the order the risks are listed in"
            )
        if np.any(np.abs(matrix) > 1.0 + tolerance):
            raise ValueError("correlations must lie in [-1, 1]")
        smallest = float(np.linalg.eigvalsh(matrix).min())
        if smallest < -tolerance:
            raise ValueError(
                f"the matrix is not positive semi-definite (smallest "
                f"eigenvalue {smallest:.6g}); sqrt(v' C v) is not a norm on "
                "it, so an aggregate could come out below the largest "
                "module it aggregates"
            )
        self.matrix = matrix
        self.index = {name: i for i, name in enumerate(self.risks)}

    @classmethod
    def life_underwriting(cls) -> "CorrelationMatrix":
        """The life underwriting sub-module matrix, Annex IV of the
        Delegated Regulation, for the risks this module can shock."""
        risks = ("mortality", "longevity", "lapse", "expense", "cat")
        matrix = [
            [1.00, -0.25, 0.00, 0.25, 0.25],
            [-0.25, 1.00, 0.25, 0.25, 0.00],
            [0.00, 0.25, 1.00, 0.50, 0.25],
            [0.25, 0.25, 0.50, 1.00, 0.25],
            [0.25, 0.00, 0.25, 0.25, 1.00],
        ]
        return cls(risks, matrix)

    def __repr__(self) -> str:
        return f"CorrelationMatrix({self.risks})"

    def __fingerprint__(self):
        return {"risks": self.risks, "matrix": self.matrix}

    def vector(self, values: dict) -> np.ndarray:
        """Order a mapping of risk to capital the way the matrix expects.

        A risk the caller did not measure contributes zero, and a risk the
        matrix does not know raises — a typo in a module name would
        otherwise silently drop a whole risk from the SCR.
        """
        unknown = set(values) - set(self.risks)
        if unknown:
            raise ValueError(
                f"{sorted(unknown)} are not risks of this matrix "
                f"{self.risks}"
            )
        return np.array([values.get(name, 0.0) for name in self.risks])

    def aggregate(self, values: dict) -> float:
        """``sqrt(v' C v)`` — the standard formula's aggregation."""
        v = self.vector(values)
        total = float(v @ self.matrix @ v)
        # A tiny negative can only be rounding on a semi-definite matrix,
        # which construction has already established.
        return math.sqrt(max(total, 0.0))


def lapse_module(values: dict) -> tuple:
    """The lapse sub-module: the **worst** of the three lapse shocks.

    Not their sum, and not their aggregate. A book cannot simultaneously
    lapse more and lapse less, so the standard takes the maximum — and
    which shock bites is a property of the product. Protection business
    fears lapse *down*, because more policies survive to claim; savings
    business fears lapse *up* and mass discontinuance, because the charges
    that pay for it walk out of the door.

    Returns ``(capital, which)`` so the binding shock is reported rather
    than buried.
    """
    considered = {name: values[name] for name in LAPSE_SHOCKS if name in values}
    if not considered:
        raise ValueError(
            f"no lapse shocks supplied; expected some of {LAPSE_SHOCKS}"
        )
    which = max(considered, key=considered.get)
    return max(considered[which], 0.0), which


def diversification_benefit(standalone: dict, aggregated: float) -> float:
    """The share of the standalone total that aggregation gives back.

    Zero when everything is perfectly correlated, and the headline number
    on any standard-formula report.
    """
    total = sum(max(v, 0.0) for v in standalone.values())
    if total <= 0.0:
        return 0.0
    return 1.0 - aggregated / total


class RiskMargin:
    """Cost of capital on the non-hedgeable risks until the book runs off.

    ``CoC * sum over t of SCR(t) / (1 + r_{t+1}) ** (t + 1)`` — Article 37.

    The circularity in the definition is real: the SCR at each future date
    depends on the technical provisions then, which include the risk margin
    then. Article 58's simplifications exist because of it, and the one
    here is the common first: the future SCRs run off in proportion to a
    **driver** — best-estimate liability, sum at risk, policy count —
    scaled to the SCR calculated today.

    The driver is the choice, and it is the caller's. A run-off in
    proportion to the BEL and one in proportion to the sum at risk are
    different numbers for the same book, and neither is more correct in
    general.
    """

    def __init__(self, driver, *, cost_of_capital: float = COST_OF_CAPITAL):
        driver = np.asarray(driver, dtype=np.float64)
        if driver.ndim != 1 or driver.size == 0:
            raise ValueError("the run-off driver must be a non-empty series")
        if np.any(driver < 0.0):
            raise ValueError("a run-off driver cannot go negative")
        if driver[0] <= 0.0:
            raise ValueError(
                "the driver is zero at time zero; there is nothing to scale "
                "today's SCR by"
            )
        if cost_of_capital < 0.0:
            raise ValueError(
                f"cost of capital {cost_of_capital} is negative"
            )
        self.driver = driver
        self.cost_of_capital = cost_of_capital

    def __repr__(self) -> str:
        return (f"RiskMargin(CoC={self.cost_of_capital}, "
                f"{self.driver.size} periods)")

    def __fingerprint__(self):
        return {"driver": self.driver,
                "cost_of_capital": self.cost_of_capital}

    def projected_scr(self, scr_today: float) -> np.ndarray:
        """Future SCRs under the proportional run-off simplification."""
        return scr_today * self.driver / self.driver[0]

    def value(self, scr_today: float, curve: YieldCurve) -> float:
        """The risk margin.

        Each future year's capital charge is discounted from the **end** of
        the year it is held over, which is when the cost of holding it is
        actually incurred.
        """
        scrs = self.projected_scr(scr_today)
        df = curve.discount_factors(scrs.size + 1)[1:]
        return float(self.cost_of_capital * np.sum(scrs * df))


class SolvencyPosition:
    """Best estimate, risk margin, SCR, and what is left over."""

    def __init__(self, *, best_estimate: float, risk_margin: float,
                 scr: float, assets: float, modules: dict,
                 binding_lapse: str | None = None):
        self.best_estimate = best_estimate
        self.risk_margin = risk_margin
        self.scr = scr
        self.assets = assets
        self.modules = dict(modules)
        self.binding_lapse = binding_lapse

    @property
    def technical_provisions(self) -> float:
        return self.best_estimate + self.risk_margin

    @property
    def own_funds(self) -> float:
        return self.assets - self.technical_provisions

    @property
    def solvency_ratio(self) -> float:
        """Own funds over the SCR. The number a regulator reads first.

        Infinite when the SCR is zero, which is a book with no risk rather
        than a division to guard against.
        """
        if self.scr == 0.0:
            return math.inf
        return self.own_funds / self.scr

    @property
    def diversification(self) -> float:
        return diversification_benefit(self.modules, self.scr)

    def __repr__(self) -> str:
        return (f"SolvencyPosition(BEL={self.best_estimate:,.0f}, "
                f"RM={self.risk_margin:,.0f}, SCR={self.scr:,.0f}, "
                f"ratio={self.solvency_ratio:.1%})")


def stressed_liabilities(liability, modelpoints, assumptions, stresses) -> dict:
    """Run ``liability`` on the base and on each stress.

    ``liability(modelpoints, assumptions) -> float`` is whatever the caller
    calls a best estimate — a present value out of a template, an aggregate
    out of a run. This module deliberately does not define it: the BEL of a
    unit-linked book and of an annuity book are different sums over
    different series, and the framework's job is to shock the basis, not to
    decide what a liability is.

    Returns ``{"base": ..., name: ...}``. Each stress is a **full
    re-projection**, which is where a standard-formula SCR's cost comes
    from.
    """
    results = {"base": float(liability(list(modelpoints), assumptions))}
    for stress in stresses:
        results[stress.name] = float(liability(
            stress.apply_to_points(modelpoints), stress.apply(assumptions)
        ))
    return results


def capital_requirements(liabilities: dict) -> dict:
    """Capital for each shock: the **increase** in the liability it causes.

    Floored at zero, because a shock that makes a book more valuable does
    not release capital under the standard formula — the SCR is the loss in
    the 99.5% scenario, and a gain in it is not a negative loss.
    """
    base = liabilities["base"]
    return {name: max(value - base, 0.0)
            for name, value in liabilities.items() if name != "base"}
