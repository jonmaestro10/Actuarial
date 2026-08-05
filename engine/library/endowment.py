"""Whole life and endowment assurance — the rest of PLAN §5.2's first line.

``TermLife`` covered term assurance; these two complete the traditional
family. Both are the same contract with one switch: **does a survivor get
paid at the end?**

- ``WholeLife`` pays on death, whenever it comes, and never matures. Its
  ``term_years`` is the run-off horizon, not a cover period, so cover does
  not stop when the projection does.
- ``Endowment`` pays on death *or* on survival to the end of the term,
  which by the identity in ``reserves.benefit_value`` is a term assurance
  plus a pure endowment and is written that way.

Indicator style throughout, so one instance evaluates a whole
``(model point × scenario)`` slab. Projection steps are payment periods,
``assumptions.freq`` to the year.

Reserves, and why they are here rather than in the overlays
-----------------------------------------------------------
These are the first templates to carry a **policy reserve**. Every other
template projects cashflows and lets ``engine/report`` build a liability
out of them, which is right for products whose statutory liability *is* a
projection. A traditional assurance is valued on a reserve — a closed-form
prospective value on a stated basis — and that reserve is a property of the
contract rather than of a reporting framework, so it belongs beside the
cashflows. See ``engine/library/reserves.py``.

The reserve is per policy in force, and the total held is
``reserve_per_policy × pols_if``. It is struck on the **valuation** basis,
which need not be the basis the projection runs on: an office projects on
its best estimate and reserves on something more prudent, and conflating
the two is how a reserve stops being a check on the projection.

Model point fields: ``age_at_entry`` (int), ``term_years`` (int),
``sum_assured``, ``annual_premium``, ``init_pols``, and optionally
``maturity_value`` (endowment; defaults to the sum assured) and
``premium_years`` (limited payment; defaults to the term).

Assumption bindings: ``mortality``, ``lapse``, ``interest`` (discounting),
``expenses``, ``commission``. Valuation basis for the reserve is passed to
``reserve_series`` rather than bound, because it is a different basis.
"""

from __future__ import annotations

import numpy as np

from engine.core.model import Model, var
from engine.library import reserves


class _TraditionalAssurance(Model):
    """What whole life and endowment share: a level premium, a death
    benefit, and a decrementing block of policies."""

    #: Whether survivors are paid at the end of the term. The single
    #: difference between the two products, and it is a class attribute
    #: rather than a model-point field because it is the product's
    #: identity — a contract does not become an endowment part way through.
    pays_on_survival = False

    def _premium_periods(self):
        years = getattr(self.mp, "premium_years", None)
        if years is None:
            years = self.mp.term_years
        return self.assumptions.periods(years)

    def _maturity_value(self):
        return getattr(self.mp, "maturity_value", None) or self.mp.sum_assured

    @var
    def age(self, t):
        """Attained age at the start of period t."""
        return self.mp.age_at_entry + self.assumptions.years_elapsed(t)

    @var
    def duration(self, t):
        return (getattr(self.mp, "duration_in_force", 0)
                + self.assumptions.years_elapsed(t))

    @var
    def in_term(self, t):
        """1 while cover is in force, 0 after."""
        return (t < self.assumptions.periods(self.mp.term_years)) * 1.0

    @var
    def paying(self, t):
        """1 while premiums are still due."""
        return (t < self._premium_periods()) * self.in_term(t)

    @var(assumption="mortality")
    def q_x(self, t):
        return self.assumptions.periodic_q(
            self.age(t), t, sex=getattr(self.mp, "sex", None),
            duration=self.duration(t),
        ) * self.in_term(t)

    @var(assumption="lapse")
    def lapse_rate(self, t):
        return self.assumptions.periodic_lapse() * self.in_term(t)

    def _split(self, t):
        return self.assumptions.decrements.split(
            self.pols_if(t),
            {"mortality": self.q_x(t), "lapse": self.lapse_rate(t)},
        )

    @var
    def pols_if(self, t):
        """Policies in force at the start of period t."""
        if t == 0:
            return self.mp.init_pols * 1.0
        return self._split(t - 1)[1] * (
            t <= self.assumptions.periods(self.mp.term_years) - 1
        )

    @var
    def pols_death(self, t):
        return self._split(t)[0]["mortality"]

    @var
    def pols_lapse(self, t):
        return self._split(t)[0]["lapse"]

    @var
    def claims(self, t):
        """Death claims arising in period t, paid at the end of it."""
        return self.pols_death(t) * self.mp.sum_assured

    @var
    def maturities(self, t):
        """Survival benefit paid at the end of the term.

        Zero at every period for a whole life, by the class attribute
        rather than by a model-point field — which is what makes the two
        products one template without a branch in any formula.
        """
        if t == 0 or not self.pays_on_survival:
            return 0.0 * self.mp.sum_assured
        return (
            self._split(t - 1)[1]
            * self._maturity_value()
            * (t == self.assumptions.periods(self.mp.term_years))
        )

    @var
    def premiums(self, t):
        return (self.pols_if(t)
                * self.assumptions.per_period(self.mp.annual_premium)
                * self.paying(t))

    @var(assumption="expenses")
    def expenses(self, t):
        """Renewal expenses at the start of period t, inflated."""
        scale = self.assumptions.expenses.renewal
        amount = scale.amount(premium=self.mp.annual_premium,
                              sum_assured=self.mp.sum_assured)
        return (self.pols_if(t)
                * self.assumptions.per_period(amount)
                * self.assumptions.inflation_index(t)
                * self.in_term(t))

    @var(assumption="expenses")
    def initial_expenses(self, t):
        """Acquisition expenses, incurred once at issue."""
        scale = self.assumptions.expenses.initial
        amount = scale.amount(premium=self.mp.annual_premium,
                              sum_assured=self.mp.sum_assured)
        return self.mp.init_pols * amount * (t == 0)

    @var(assumption="interest")
    def v(self, t):
        return self.assumptions.discount(t)

    # --- present values ---------------------------------------------------

    def pv_premiums(self):
        return sum(self.premiums(t) * self.v(t) for t in range(self.proj_len))

    def pv_claims(self):
        return sum(self.claims(t) * self.v(t + 1) for t in range(self.proj_len))

    def pv_maturities(self):
        return sum(self.maturities(t) * self.v(t) for t in range(self.proj_len))

    def pv_expenses(self):
        return sum(
            (self.expenses(t) + self.initial_expenses(t)) * self.v(t)
            for t in range(self.proj_len)
        )

    # --- the reserve ------------------------------------------------------

    #: Which of ``reserves.PRODUCTS`` this template values as.
    reserve_product = "term"

    def reserve_series(self, basis, rate, *, net_premium: bool = True,
                       initial_expense: float = 0.0,
                       renewal_expense: float = 0.0,
                       claim_expense: float = 0.0) -> np.ndarray:
        """Reserve per policy at each whole year, on a **valuation** basis.

        ``basis`` and ``rate`` are the valuation mortality and interest,
        which need not be the ones the projection runs on. An office
        projects on its best estimate and reserves on something more
        prudent; taking the projection's own basis by default would make
        the reserve a restatement of the projection rather than a check on
        it, so both are required arguments.

        ``net_premium=True`` solves the premium from the basis and ignores
        what the contract actually charges — the statutory net premium
        reserve. ``False`` uses the office premium on the model point and
        the expense loadings supplied, which is the gross premium reserve
        and the one that shows the first year honestly.
        """
        term, age = int(self.mp.term_years), int(self.mp.age_at_entry)
        maturity = (self._maturity_value() if self.pays_on_survival else None)
        premium_years = getattr(self.mp, "premium_years", None)
        shared = dict(product=self.reserve_product,
                      sum_assured=float(self.mp.sum_assured),
                      maturity=maturity, premium_term=premium_years)
        if net_premium:
            premium = reserves.net_premium(basis, age, term, rate, **shared)
            return reserves.prospective_reserve(
                basis, age, term, rate, premium=premium, **shared)
        return reserves.gross_premium_reserve(
            basis, age, term, rate, premium=float(self.mp.annual_premium),
            initial_expense=initial_expense,
            renewal_expense=renewal_expense,
            claim_expense=claim_expense, **shared)

    def reserve_held(self, basis, rate, **kw) -> np.ndarray:
        """Total reserve carried by the block at each whole year."""
        per_policy = self.reserve_series(basis, rate, **kw)
        freq = self.assumptions.freq
        in_force = np.array(
            [np.sum(self.pols_if(t * freq)) for t in range(per_policy.size)]
        )
        return per_policy * in_force


class WholeLife(_TraditionalAssurance):
    """Level-premium whole life assurance.

    Pays the sum assured on death, whenever it comes. ``term_years`` is the
    run-off horizon rather than a cover period — a whole life does not
    expire, so the number is a statement about how far the projection goes
    and should reach the end of the mortality table.

    ``premium_years`` makes it a limited-payment contract, which raises the
    premium without touching the benefit.
    """

    pays_on_survival = False
    reserve_product = "term"


class Endowment(_TraditionalAssurance):
    """Endowment assurance: the sum assured on death **or** on survival.

    A term assurance plus a pure endowment, and both this template and
    ``reserves.benefit_value`` write it that way rather than giving it a
    formula of its own — the identity *is* the product, and a second
    implementation would be a second chance to disagree with it.

    ``maturity_value`` pays a different amount on survival from the death
    benefit; it defaults to the sum assured, which is the ordinary contract.
    """

    pays_on_survival = True
    reserve_product = "endowment"
