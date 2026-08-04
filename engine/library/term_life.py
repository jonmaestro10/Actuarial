"""Level-premium term assurance — the first product template.

Annual projection steps. Conventions (see Model docstring in
engine/core/model.py):

- ``pols_if(t)``: policies in force at the start of year ``t``.
- Premiums and expenses are paid at the start of year ``t`` by in-force
  policies; death claims arising during year ``t`` are paid at its end.
- Cover runs for ``mp.term_years`` years; ``pols_if`` is zero from the end
  of the term onward.

Model point fields: ``age_at_entry`` (int), ``term_years`` (int),
``sum_assured``, ``annual_premium``, ``init_pols``.
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
        """Attained age at the start of year t."""
        return self.mp.age_at_entry + t

    @var
    def in_term(self, t):
        """1 during the cover term, 0 after."""
        return (t < self.mp.term_years) * 1.0

    @var(assumption="mortality")
    def q_x(self, t):
        """Annual mortality rate applying during year t (0 after the term)."""
        table = self.assumptions.mortality
        return table.q_at(table.clip_age(self.age(t))) * self.in_term(t)

    @var(assumption="lapse")
    def lapse_rate(self, t):
        """Annual lapse rate applying during year t (after mortality)."""
        return self.assumptions.lapse * self.in_term(t)

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
        return survived * (t <= self.mp.term_years - 1)

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
        """Premium income at the start of year t."""
        return self.pols_if(t) * self.mp.annual_premium * self.in_term(t)

    @var(assumption="expense_per_policy")
    def expenses(self, t):
        """Maintenance expenses at the start of year t."""
        return self.pols_if(t) * self.assumptions.expense_per_policy * self.in_term(t)

    @var(assumption="interest")
    def v(self, t):
        """Discount factor from time t back to time 0."""
        return (1.0 + self.assumptions.interest) ** (-t)

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
