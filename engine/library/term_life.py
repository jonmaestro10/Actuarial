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
"""

from __future__ import annotations

from engine.core.model import Model, var


class TermLife(Model):
    @var
    def age(self, t):
        """Attained age at the start of year t."""
        return self.mp.age_at_entry + t

    @var(assumption="mortality")
    def q_x(self, t):
        """Annual mortality rate applying during year t."""
        if t >= self.mp.term_years:
            return 0.0
        return self.assumptions.mortality.q(self.age(t))

    @var(assumption="lapse")
    def lapse_rate(self, t):
        """Annual lapse rate applying during year t (after mortality)."""
        if t >= self.mp.term_years:
            return 0.0
        return self.assumptions.lapse

    @var
    def pols_if(self, t):
        """Policies in force at the start of year t."""
        if t == 0:
            return float(self.mp.init_pols)
        if t > self.mp.term_years:
            return 0.0
        survived = (
            self.pols_if(t - 1)
            * (1.0 - self.q_x(t - 1))
            * (1.0 - self.lapse_rate(t - 1))
        )
        return 0.0 if t == self.mp.term_years else survived

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
        if t >= self.mp.term_years:
            return 0.0
        return self.pols_if(t) * self.mp.annual_premium

    @var(assumption="expense_per_policy")
    def expenses(self, t):
        """Maintenance expenses at the start of year t."""
        if t >= self.mp.term_years:
            return 0.0
        return self.pols_if(t) * self.assumptions.expense_per_policy

    @var(assumption="interest")
    def v(self, t):
        """Discount factor from time t back to time 0."""
        return (1.0 + self.assumptions.interest) ** (-t)

    # Present values are scalars over the whole projection, not @var series.

    def pv_premiums(self) -> float:
        return sum(self.premiums(t) * self.v(t) for t in range(self.proj_len))

    def pv_claims(self) -> float:
        return sum(self.claims(t) * self.v(t + 1) for t in range(self.proj_len))

    def pv_expenses(self) -> float:
        return sum(self.expenses(t) * self.v(t) for t in range(self.proj_len))

    def net_pv(self) -> float:
        return self.pv_premiums() - self.pv_claims() - self.pv_expenses()
