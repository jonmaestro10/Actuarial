"""Single-premium unit-linked contract with a GMDB rider — the seed of the
VA/VPLA family.

Annual steps, indicator style. Fund mechanics per policy, per scenario:

- ``fund_boy(t)``: unit fund at the start of year ``t``; the single premium
  buys units at ``t = 0``.
- During year ``t`` the fund earns the scenario return, then the annual
  management charge (AMC) is deducted from the grown fund.
- Death during year ``t`` pays ``max(guarantee, fund after growth and
  charges)`` at the end of the year — a return-of-premium GMDB when
  ``mp.gmdb_guarantee == mp.premium``.
- Contract runs ``mp.term_years`` years; survivors take the fund at
  maturity. Lapses surrender the fund (no penalty, no P&L impact beyond
  lost future charges).

Model point fields: ``age_at_entry`` (int), ``term_years`` (int),
``premium``, ``gmdb_guarantee``, ``init_pols``.
Assumption bindings: ``mortality``, ``lapse``, ``interest`` (discounting),
``amc``. Scenario binding: fund returns via ``self.scenarios.ret(t)``.
"""

from __future__ import annotations

import numpy as np

from engine.core.model import Model, var


class UnitLinkedGMDB(Model):
    @var
    def age(self, t):
        """Attained age at the start of year t."""
        return self.mp.age_at_entry + t

    @var
    def in_term(self, t):
        """1 during the contract term, 0 after."""
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
    def fund_ret(self, t):
        """Scenario fund return earned during year t (masked past the term,
        with the lookup clipped so the horizon is never over-read)."""
        lookup_t = min(t, self.scenarios.horizon - 1)
        return self.scenarios.ret(lookup_t) * self.in_term(t)

    @var
    def fund_boy(self, t):
        """Unit fund per policy at the start of year t."""
        if t == 0:
            return self.mp.premium * 1.0
        return self.fund_eoy(t - 1) * (t <= self.mp.term_years - 1)

    @var
    def fund_grown(self, t):
        """Fund after year-t growth, before charges."""
        return self.fund_boy(t) * (1.0 + self.fund_ret(t)) * self.in_term(t)

    @var(assumption="amc")
    def charges_per_pol(self, t):
        """Annual management charge deducted at the end of year t."""
        return self.fund_grown(t) * self.assumptions.amc

    @var
    def fund_eoy(self, t):
        """Fund per policy at the end of year t, after charges."""
        return self.fund_grown(t) - self.charges_per_pol(t)

    @var
    def fee_income(self, t):
        """Total charges collected in year t from in-force policies."""
        return self.charges_per_pol(t) * self.pols_if(t)

    @var
    def gmdb_claims(self, t):
        """GMDB death claims in year t: greater of guarantee and fund,
        paid at end of year."""
        per_death = np.maximum(self.mp.gmdb_guarantee * 1.0, self.fund_eoy(t))
        return self.pols_death(t) * per_death * self.in_term(t)

    @var
    def gmdb_strain(self, t):
        """Guarantee cost in year t: claims in excess of the fund released."""
        excess = np.maximum(self.mp.gmdb_guarantee - self.fund_eoy(t), 0.0)
        return self.pols_death(t) * excess * self.in_term(t)

    @var
    def maturity_payments(self, t):
        """Fund paid to survivors when the contract matures at
        ``t == term_years`` (zero at every other time)."""
        if t == 0:
            return self.mp.premium * 0.0
        survivors = (
            self.pols_if(t - 1)
            * (1.0 - self.q_x(t - 1))
            * (1.0 - self.lapse_rate(t - 1))
        )
        return survivors * self.fund_eoy(t - 1) * (t == self.mp.term_years)

    @var(assumption="interest")
    def v(self, t):
        """Discount factor from time t back to time 0."""
        return (1.0 + self.assumptions.interest) ** (-t)

    def pv_fee_income(self):
        return sum(self.fee_income(t) * self.v(t + 1) for t in range(self.proj_len))

    def pv_gmdb_strain(self):
        return sum(self.gmdb_strain(t) * self.v(t + 1) for t in range(self.proj_len))
