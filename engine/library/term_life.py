"""Level-premium term assurance — the first product template.

Projection steps are payment periods, ``assumptions.freq`` to the year —
annual by default, so ``t`` counts years unless told otherwise. A year of
age is split into sub-periods by ``MortalityBasis.periodic_rate``, and the
other annual assumptions have matching per-period views on ``Assumptions``;
at ``freq = 1`` every one of them is the annual assumption bit for bit.
See tests/test_frequency.py.

Conventions (see Model docstring in engine/core/model.py):

- ``pols_if(t)``: policies in force at the start of period ``t``.
- Premiums and expenses are paid at the start of period ``t`` by in-force
  policies; death claims arising during period ``t`` are paid at its end.
- Cover runs for ``mp.term_years`` years; ``pols_if`` is zero from the end
  of the term onward.

Running sub-annually does not change the decrement basis — the same
policies are in force at every anniversary — but it does change how exits
split between mortality and lapse, because the two decrements interleave
rather than being applied whole in sequence. That is the finer and more
correct answer, and it is what tests/test_frequency.py pins.

Term assurance is priced on **select** rates, so ``q_x`` looks mortality up
by duration as well as by age. Supply a ``MortalityBasis`` carrying a select
table and the first years of cover are read from it; supply an ultimate-only
table — a plain ``MortalityTable``, say — and the duration argument cannot
move a number, so the annual golden suite stands unchanged.

Model point fields: ``age_at_entry`` (int), ``term_years`` (int),
``sum_assured``, ``annual_premium``, ``init_pols``, and optionally
``duration_in_force`` (int, default 0) for a block already part way through
its select period at projection time zero.
Assumption bindings: ``mortality`` (table), ``lapse`` (flat annual rate),
``interest`` (flat annual rate), ``expense_per_policy``.

Formulas are written in indicator style — conditions on model-point data
appear as multiplicative ``(t < term) * 1.0`` factors, never as ``if``
branches — so the identical code runs per policy (scalar interpreter) or
across a whole model-point batch (vectorized executor). Mortality lookups
are clipped into table range and masked by the in-term indicator, keeping
the no-extrapolation guarantee while ages beyond the term stay harmless.
"""

from __future__ import annotations

from engine.core.model import Model, var


class TermLife(Model):
    @var
    def age(self, t):
        """Attained age at the start of period t."""
        return self.mp.age_at_entry + self.assumptions.years_elapsed(t)

    @var
    def duration(self, t):
        """Whole years since underwriting at the start of period t.

        ``duration_in_force`` is how long the policy had already run at
        projection time zero — zero for new business, which is why it
        defaults, and non-zero for an in-force block valued part way
        through its select period.
        """
        return (
            getattr(self.mp, "duration_in_force", 0)
            + self.assumptions.years_elapsed(t)
        )

    @var
    def in_term(self, t):
        """1 during the cover term, 0 after."""
        return (t < self.assumptions.periods(self.mp.term_years)) * 1.0

    @var(assumption="mortality")
    def q_x(self, t):
        """Mortality rate applying during period t (0 after the term).

        Duration is passed on every lookup. On an ultimate-only basis it is
        inert — the rate is the same bits either way — and on a
        select-and-ultimate basis it is what makes a recently underwritten
        life cheaper than an identically aged one selected long ago.
        """
        return self.assumptions.periodic_q(
            self.age(t), t, sex=getattr(self.mp, "sex", None),
            duration=self.duration(t),
        ) * self.in_term(t)

    @var(assumption="lapse")
    def lapse_rate(self, t):
        """Lapse rate applying during period t (after mortality)."""
        return self.assumptions.periodic_lapse() * self.in_term(t)

    @var
    def pols_if(self, t):
        """Policies in force at the start of year t."""
        if t == 0:
            return self.mp.init_pols * 1.0
        survived = (
            self.pols_if(t - 1)
            * (1.0 - self.q_x(t - 1))
            * (1.0 - self.lapse_rate(t - 1))
        )
        return survived * (t <= self.assumptions.periods(self.mp.term_years) - 1)

    @var
    def pols_death(self, t):
        """Deaths during year t."""
        return self.pols_if(t) * self.q_x(t)

    @var
    def claims(self, t):
        """Death claims arising during year t, paid at end of year."""
        return self.pols_death(t) * self.mp.sum_assured

    @var
    def premiums(self, t):
        """Premium income at the start of period t."""
        return (
            self.pols_if(t)
            * self.assumptions.per_period(self.mp.annual_premium)
            * self.in_term(t)
        )

    @var(assumption="expense_per_policy")
    def expenses(self, t):
        """Maintenance expenses at the start of period t."""
        return (
            self.pols_if(t)
            * self.assumptions.per_period(self.assumptions.expense_per_policy)
            * self.in_term(t)
        )

    @var(assumption="interest")
    def v(self, t):
        """Discount factor from the start of period t back to time 0."""
        return self.assumptions.discount(t)

    # Present values are scalars over the whole projection, not @var series.
    # Start-of-year flows discount at v(t), end-of-year flows at v(t + 1);
    # both sum over periods t = 0 .. proj_len - 1.

    def pv_premiums(self):
        return sum(self.premiums(t) * self.v(t) for t in range(self.proj_len))

    def pv_claims(self):
        return sum(self.claims(t) * self.v(t + 1) for t in range(self.proj_len))

    def pv_expenses(self):
        return sum(self.expenses(t) * self.v(t) for t in range(self.proj_len))

    def net_pv(self):
        return self.pv_premiums() - self.pv_claims() - self.pv_expenses()
