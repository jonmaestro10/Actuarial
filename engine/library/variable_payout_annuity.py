"""Variable payout life annuity — the pooled product, and the first
template with a ``@pool`` variable.

This is the product ``jonmaestro10/VPLA`` exists to administer, and the one
docs/vpla-review.md §7.1 said the DSL could not express. Members share a
fund; each member's pension is their account value divided by their own
annuity factor; and at every valuation the whole pool's pensions are scaled
by one number — the ratio of what the pool actually has to what it actually
owes. That number is a reduction across every member in the block, computed
inside the time loop and fed back into each member's next step, which is
exactly what ``@pool`` is for.

Mechanics of one period ``t``
-----------------------------

1. ``assets(t)`` — the pool rolls forward: last period's assets, less the
   pensions paid out of them, grown at the fund return. Assets belonging to
   members who died stay in — the mortality release needs no separate term.
2. ``liability(t)`` — what the pool owes if pensions were left alone:
   surviving members' carried pension times their annuity factor at their
   *new* age.
3. ``adjustment(t)`` — ``Σ assets / Σ liability - 1`` over the whole pool,
   on revaluation periods and zero otherwise. **The pooled variable.**
4. ``pension(t)`` — every member's pension scaled by ``1 + adjustment(t)``.
5. ``account_value(t)`` — the member's share, restated as their reserve.

Two properties follow, and they are what make the product what it is:

- **The pool balances by construction.** After a revaluation
  ``Σ account_value = Σ assets`` exactly: assets equal liabilities, always.
- **It is neutral when experience matches assumption.** Earn the valuation
  rate, lose exactly the expected members, and every pension is unchanged —
  the deceased members' reserves are already funding the survivors.

Both are asserted in tests/test_variable_payout_annuity.py, and both were
established against the reference implementation of the real system first
(tests/test_vpla_reconciliation.py).

Model point fields: ``dob``, ``sex``, ``valuation`` (dates), ``pension``
(per period, at outset), ``account_value`` (at outset), ``init_lives``.
Assumption binding: a :class:`~engine.data.basis.ValuationBasis`, whose
``revalue_every`` sets how many payment periods pass between revaluations.
Scenario binding: pool returns via ``self.scenarios.ret(t)``, **per period**
— a monthly axis wants a monthly return, so a pool earning its 4% annual
valuation rate is a scenario of ``1.04 ** (1/12) - 1``, not 0.04.

One consequence of projecting cohorts rather than individuals is worth
stating, because it is easy to misread the output. Each model point carries
a fractional survival weight, and its deceased members' assets stay with it,
so a cohort funds its own mortality release internally. What crosses between
model points through the adjustment is therefore investment experience and
mortality *deviation from assumption* — not the release itself. Run a pool
whose mortality is exactly as assumed and the adjustment is zero even though
members are dying, which is the point of the neutrality test.

Not modelled, deliberately: contributions arriving at a valuation. VPLA puts
new money in the adjustment's denominator but adds it to the new pension
unadjusted, so its pool is short by exactly ``adjustment x contributions``
(review §6.8). Reproducing that here would bake a defect into a template;
handling it correctly is a design question about whether new money shares in
the period's experience, and belongs with the accumulation phase.
"""

from __future__ import annotations

import numpy as np

from engine.core.model import Model, pool, var
from engine.core.timeaxis import TimeAxis
from engine.library.annuities import prospective_annuity_factors


class VariablePayoutAnnuity(Model):
    def setup(self):
        basis = self.assumptions
        axis = TimeAxis(basis.freq, self.proj_len + 1, self.mp.valuation)
        self.axis = axis
        self.revalue_every = getattr(basis, "revalue_every", 1)

        discount = basis.discount(axis)
        self._survival = basis.survival(axis, self.mp.dob, self.mp.sex)
        # The factor a survivor attracts at each future period — the
        # member's own annuity factor, recalculated at every valuation
        # exactly as VPLA does it.
        self._factor = prospective_annuity_factors(
            discount, self._survival, axis.freq
        )

    # --- the members ------------------------------------------------------

    @var(assumption="mortality")
    def survival(self, t):
        """Probability a member is still in payment at the start of period t."""
        return self.at(self._survival, t)

    @var
    def lives(self, t):
        """Members in payment at the start of period t."""
        return self.mp.init_lives * self.survival(t)

    @var(assumption="mortality")
    def annuity_factor(self, t):
        """The member's annuity factor at their attained age at period t."""
        return self.at(self._factor, t)

    @var
    def is_valuation(self, t):
        """1 on a revaluation period, 0 otherwise."""
        return (t % self.revalue_every == 0) * 1.0

    # --- the fund ---------------------------------------------------------

    @var
    def fund_return(self, t):
        """Return earned by the pool during period t."""
        return self.scenarios.ret(min(t, self.scenarios.horizon - 1))

    @var
    def payments(self, t):
        """Pension paid to this model point at the start of period t."""
        return self.lives(t) * self.pension(t)

    @var
    def assets(self, t):
        """Pool assets attributable to this model point at the start of
        period t, before any revaluation.

        Payments leave before the return is credited. Nothing is removed on
        death: a deceased member's assets stay in the pool, which is how the
        mortality credit reaches the survivors.
        """
        if t == 0:
            return self.mp.account_value * self.mp.init_lives * 1.0
        return (self.assets(t - 1) - self.payments(t - 1)) * (
            1.0 + self.fund_return(t - 1)
        )

    # --- the pool ---------------------------------------------------------

    @var
    def pension_carried(self, t):
        """The pension coming into period t, before this period's adjustment."""
        if t == 0:
            return self.mp.pension * 1.0
        return self.pension(t - 1)

    @var
    def liability(self, t):
        """What the pool owes this model point if pensions are left alone —
        surviving members' carried pension at their new annuity factor."""
        return (
            self.lives(t)
            * self.pension_carried(t)
            * self.annuity_factor(t)
            * self.axis.freq
        )

    @pool
    def adjustment(self, t):
        """The pool-wide pension adjustment for period t.

        ``Σ assets / Σ liability - 1`` across every member of the block.
        This is the reduction across the model-point axis that gives the
        product its name: no member's pension can be computed from that
        member's own data alone.

        A pool with nothing left to owe is not adjusted rather than divided
        by zero.
        """
        # Early periods can be scenario-independent while later ones are
        # not, so the two totals may not arrive with the same shape.
        held, owed = np.broadcast_arrays(
            np.asarray(self.pool_sum(self.assets(t)), dtype=np.float64),
            np.asarray(self.pool_sum(self.liability(t)), dtype=np.float64),
        )
        ratio = np.divide(held, owed, out=np.ones_like(held), where=owed > 0.0)
        return (ratio - 1.0) * self.is_valuation(t)

    @var
    def pension(self, t):
        """Pension per member for period t, after the pool adjustment."""
        return self.pension_carried(t) * (1.0 + self.adjustment(t))

    @var
    def account_value(self, t):
        """This model point's share of the pool at the start of period t.

        Prospective on a revaluation period — the surviving members' reserve
        at the adjusted pension — and the rolled-forward assets between
        revaluations. That change of meaning is the product's, not an
        artefact: docs/vpla-review.md §4.
        """
        restated = (
            self.lives(t)
            * self.pension(t)
            * self.annuity_factor(t)
            * self.axis.freq
        )
        keep = 1.0 - self.is_valuation(t)
        return restated * self.is_valuation(t) + self.assets(t) * keep

    def pool_assets(self, t):
        """Total pool assets at the start of period t."""
        return self.pool_sum(self.assets(t))

    def pool_liability(self, t):
        """Total pool liability at the start of period t, before adjustment."""
        return self.pool_sum(self.liability(t))
