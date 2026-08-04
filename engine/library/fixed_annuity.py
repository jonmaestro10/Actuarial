"""Single-premium deferred fixed annuity — the second product template.

Projection steps are payment periods, ``assumptions.freq`` to the year
(annual by default). Indicator-style formulas — see term_life.py.

Product mechanics:

- **Deferral phase** (``t < defer_years * freq``): the single premium accumulates
  at the guaranteed crediting rate. Death during the deferral returns the
  end-of-year fund value.
- **Payout phase** (``t >= defer_years``): the fund annuitizes into a level
  annuity-due of ``mp.annual_payment`` per survivor, paid at the start of
  each year for life (truncated at the projection horizon).
- Mortality applies throughout; no lapses/surrenders in Phase 0.

Model point fields: ``age_at_entry`` (int), ``defer_years`` (int),
``premium``, ``annual_payment``, ``init_pols``.
Assumption bindings: ``mortality``, ``interest`` (discounting),
``crediting_rate`` (fund accumulation).
"""

from __future__ import annotations

from engine.core.model import Model, var


class FixedAnnuity(Model):
    @var
    def age(self, t):
        """Attained age at the start of period t."""
        return self.mp.age_at_entry + self.assumptions.years_elapsed(t)

    @var
    def in_defer(self, t):
        """1 during the deferral phase, 0 from vesting onward."""
        return (t < self.assumptions.periods(self.mp.defer_years)) * 1.0

    @var
    def in_payout(self, t):
        """1 from vesting onward."""
        return 1.0 - self.in_defer(t)

    @var(assumption="mortality")
    def q_x(self, t):
        """Mortality rate applying during period t (both phases)."""
        return self.assumptions.periodic_q(
            self.age(t), t, sex=getattr(self.mp, "sex", None)
        )

    @var
    def pols_if(self, t):
        """Surviving annuitants at the start of year t."""
        if t == 0:
            return self.mp.init_pols * 1.0
        return self.pols_if(t - 1) * (1.0 - self.q_x(t - 1))

    @var(assumption="crediting_rate")
    def fund_eoy_per_pol(self, t):
        """Fund per policy at the end of period t of the deferral phase."""
        elapsed = (t + 1) / self.assumptions.freq if self.assumptions.freq != 1 else t + 1
        return (
            self.mp.premium
            * (1.0 + self.assumptions.crediting_rate) ** elapsed
            * self.in_defer(t)
        )

    @var
    def death_benefits(self, t):
        """Deferral-phase death benefits arising in year t, paid at end of year."""
        return self.pols_if(t) * self.q_x(t) * self.fund_eoy_per_pol(t)

    @var
    def payments(self, t):
        """Annuity payments at the start of period t, payout phase only."""
        return (
            self.pols_if(t)
            * self.assumptions.per_period(self.mp.annual_payment)
            * self.in_payout(t)
        )

    @var(assumption="interest")
    def v(self, t):
        """Discount factor from the start of period t back to time 0."""
        return self.assumptions.discount(t)

    def pv_payments(self):
        return sum(self.payments(t) * self.v(t) for t in range(self.proj_len))

    def pv_death_benefits(self):
        return sum(
            self.death_benefits(t) * self.v(t + 1) for t in range(self.proj_len)
        )
