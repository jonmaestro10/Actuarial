"""Fixed-indexed annuity with a guaranteed lifetime withdrawal benefit.

PLAN.md §5.2: *fixed & fixed-indexed annuities (deferred/immediate, GLWB
riders, index crediting)*. The account mechanics are RFC-010's; what is new
here is the crediting rule (RFC-011, `engine/data/index_credit.py`) and the
word **lifetime**.

Indicator style throughout, so one instance evaluates a whole
``(model point x scenario)`` slab. Projection steps are payment periods,
``assumptions.freq`` to the year — and a monthly crediting design **requires**
a monthly step, which the assumption set refuses at construction rather than
at the first anniversary.

Crediting happens at anniversaries and nowhere else
---------------------------------------------------
Between anniversaries an FIA account does not move with the index at all.
At each anniversary it credits a rate derived from what the index did over
the year, floored at zero — so the account is a **ratchet**, and every year
locks in. That is why the path matters and why an average of annual returns
cannot value this contract: a bad year costs the policyholder nothing, so
the distribution of what gets credited is not the distribution of what the
index did.

The two accumulators (``index_level``, ``index_total``) reset at each
anniversary and carry a one-period look-back, which is what the windowed
forward loop is built for. See RFC-011 for why a twelve-period formula would
not survive the graph trace.

What makes a GLWB different from the GMWB in unit_linked.py
-----------------------------------------------------------
One word, and it is worth more than the rest of the rider put together:
**the withdrawal is for life**. The GMWB on a unit-linked contract pays a
guaranteed amount for the contract term; this pays it until the annuitant
dies. So ``glwb_strain`` — the part of the guaranteed withdrawal the
insurer funds out of its own pocket once the account is empty — has **no
end date in the contract**, and the projection has to run to the end of the
mortality table rather than to the end of a term. A projection that stops
at the account's exhaustion, or at some nominal term, values the guarantee
at a fraction of what it is.

Order of operations within period ``t``
---------------------------------------
1. ``av_boy(t)`` — the account at the start of the period.
2. The index accumulators take this period's return.
3. At an anniversary only, ``index_credit_rate(t)`` is credited:
   ``av_after_credit``.
4. The rider fee comes out, charged on the **benefit base** rather than on
   the account — which is the point of the fee, since the base is what the
   insurer has guaranteed and it keeps being charged after the account is
   gone.
5. The guaranteed withdrawal is taken, from the account as far as it goes.
   The shortfall is ``glwb_strain``, and it is the insurer's.
6. Deaths in the period are paid the account (an FIA death benefit is the
   account value); surrenders are paid the account less its surrender
   charge, and **forfeit the rider**, which is what makes the surrender
   charge and the guarantee two halves of one design.

Model point fields: ``age_at_entry`` (int), ``premium``, ``init_pols``, and
for the rider ``glwb_base`` (usually the premium), ``glwb_rate``,
``withdrawal_start_year`` (int), ``glwb_rollup`` and ``glwb_rollup_years``.
Setting ``glwb_rate`` to zero switches the rider off with no branch
anywhere: a zero withdrawal rate guarantees a zero withdrawal.

Assumption bindings: ``mortality``, ``lapse``, ``interest`` (discounting),
``index_credit``, ``glwb_fee``, ``account`` (for the surrender charge).
Scenario binding: index returns via ``self.scenarios.ret(t)``.
"""

from __future__ import annotations

import numpy as np

from engine.core.model import Model, var
from engine.data.index_credit import INIT_LEVEL, INIT_TOTAL


class FixedIndexedAnnuity(Model):
    def _field(self, name, default):
        return getattr(self.mp, name, default) * 1.0

    @var
    def age(self, t):
        """Attained age at the start of period t."""
        return self.mp.age_at_entry + self.assumptions.years_elapsed(t)

    @var
    def withdrawing(self, t):
        """1 once guaranteed withdrawals have started.

        **No end date.** This is the whole difference between a GLWB and the
        term-limited GMWB in the unit-linked template, and it is one missing
        ``in_term`` factor rather than a mechanism.
        """
        start = self.assumptions.periods(
            self._field("withdrawal_start_year", 0.0)
        )
        return (t >= start) * 1.0

    @var
    def deferring(self, t):
        """1 before withdrawals start."""
        return 1.0 - self.withdrawing(t)

    # --- the index accumulators -------------------------------------------

    @var
    def index_return(self, t):
        """Index return over period t, from the scenario set."""
        if self.scenarios is None:
            raise ValueError(
                "a fixed-indexed annuity needs index scenarios; run it "
                "through run_stochastic with a ScenarioSet"
            )
        return self.scenarios.ret(min(t, self.scenarios.horizon - 1))

    def _accumulated(self, t):
        """The pair of accumulators after period ``t``.

        Whether ``t`` opens a crediting year is a property of ``t`` and the
        frequency — not of any model point — so it is an ordinary Python
        branch, the same kind as ``t == 0``, and the reset costs nothing.
        """
        method = self.assumptions.index_credit
        if t == 0 or self.assumptions.sub_period(t) == 0:
            level, total = INIT_LEVEL, INIT_TOTAL
        else:
            level, total = self.index_level(t - 1), self.index_total(t - 1)
        return method.accumulate(level, total, self.index_return(t))

    @var(assumption="index_credit")
    def index_level(self, t):
        """Index level within the crediting year, reset at each anniversary."""
        return self._accumulated(t)[0]

    @var(assumption="index_credit")
    def index_total(self, t):
        """Running total within the crediting year, reset at each
        anniversary. What it totals depends on the design — capped monthly
        returns for a monthly-sum contract, index levels for an averaging
        one, nothing at all for a point-to-point one."""
        return self._accumulated(t)[1]

    @var(assumption="index_credit")
    def index_credit_rate(self, t):
        """Rate credited at the end of period t.

        Zero at every period that is not an anniversary, which is most of
        them under a monthly step — an FIA account does not move with the
        index between anniversaries, and the zero here is the product rather
        than a placeholder.
        """
        a = self.assumptions
        if a.sub_period(t) != a.freq - 1:
            return 0.0 * self.index_level(t)
        return a.index_credit.credit(self.index_level(t), self.index_total(t),
                                     a.freq)

    # --- the account -------------------------------------------------------

    @var
    def av_boy(self, t):
        """Account value per policy at the start of period t."""
        if t == 0:
            return self.mp.premium * 1.0
        return self.av_eop(t - 1)

    @var
    def av_after_credit(self, t):
        """Account after the anniversary credit (step 3)."""
        return self.av_boy(t) * (1.0 + self.index_credit_rate(t))

    @var(assumption="glwb_fee")
    def rider_fee(self, t):
        """Rider fee for period t, charged on the benefit base.

        On the base and not on the account, which is the point: the insurer
        guarantees the base, and the fee keeps being charged on it while the
        account still exists to charge it against. Capped at the account,
        so an empty one stops paying rather than going negative.
        """
        due = self.assumptions.per_period(
            self.benefit_base(t) * self.assumptions.glwb_fee
        )
        return np.minimum(due, np.maximum(self.av_after_credit(t), 0.0))

    @var
    def av_after_fee(self, t):
        return self.av_after_credit(t) - self.rider_fee(t)

    @var
    def gaw(self, t):
        """Guaranteed annual withdrawal due in period t, per policy."""
        return (
            self.assumptions.per_period(
                self.benefit_base(t) * self._field("glwb_rate", 0.0)
            )
            * self.withdrawing(t)
        )

    @var
    def withdrawal_from_account(self, t):
        """The part of the guaranteed withdrawal the account itself pays."""
        return np.minimum(self.gaw(t), np.maximum(self.av_after_fee(t), 0.0))

    @var
    def av_eop(self, t):
        """Account value per policy at the end of period t."""
        return self.av_after_fee(t) - self.withdrawal_from_account(t)

    # --- the benefit base --------------------------------------------------

    @var
    def benefit_base(self, t):
        """The guaranteed amount the withdrawal is a percentage of.

        Two things happen to it, both at anniversaries and both only while
        withdrawals have not started:

        - it **rolls up** at a contractual rate for a stated number of
          years, which is what an FIA is marketed on; and
        - it **ratchets** to the account value whenever the account is
          higher, which is what turns a good year into a permanently larger
          guarantee.

        Once withdrawals start the base is frozen. A contract that keeps
        ratcheting in payment is a different product and a different
        formula, not a parameter of this one.

        **The base takes its last step on the anniversary withdrawals begin**,
        not the one before it. The test is whether withdrawals had started
        *before* this period, so a ten-year roll-up against a tenth-year
        start compounds ten times rather than nine. Written as
        ``deferring(t - 1)`` rather than ``deferring(t)``, which is the
        entire difference and is worth a sentence because it is a whole
        year of roll-up.
        """
        if t == 0:
            return self._field("glwb_base", 0.0) + 0.0 * self.mp.premium
        previous = self.benefit_base(t - 1)
        a = self.assumptions
        if a.sub_period(t) != 0:
            return previous                      # steps at anniversaries only
        still_deferred = self.deferring(t - 1)
        rollup_years = a.periods(self._field("glwb_rollup_years", 0.0))
        rolling = (t <= rollup_years) * still_deferred
        rolled = previous * (1.0 + self._field("glwb_rollup", 0.0) * rolling)
        ratcheted = np.maximum(rolled, self.av_eop(t - 1))
        return ratcheted * still_deferred + previous * (1.0 - still_deferred)

    # --- decrements --------------------------------------------------------

    @var(assumption="mortality")
    def q_x(self, t):
        return self.assumptions.periodic_q(
            self.age(t), t, sex=getattr(self.mp, "sex", None)
        )

    @var(assumption="lapse")
    def lapse_rate(self, t):
        """Voluntary surrender rate.

        Zero once withdrawals have started: a policyholder taking a lifetime
        income does not surrender it for a cash value that is, by
        construction, worth less than the income they have started. Stating
        that as a factor rather than leaving a flat rate running is the
        difference between a guarantee that costs something and one the
        model quietly lapses away.
        """
        return self.assumptions.periodic_lapse() * self.deferring(t)

    def _split(self, t):
        return self.assumptions.decrements.split(
            self.pols_if(t), {"mortality": self.q_x(t), "lapse": self.lapse_rate(t)}
        )

    @var
    def pols_if(self, t):
        """Policies in force at the start of period t."""
        if t == 0:
            return self.mp.init_pols * 1.0
        return self._split(t - 1)[1]

    @var
    def pols_death(self, t):
        return self._split(t)[0]["mortality"]

    @var
    def pols_lapse(self, t):
        return self._split(t)[0]["lapse"]

    # --- cashflows ---------------------------------------------------------

    @var
    def withdrawals(self, t):
        """Total guaranteed withdrawals paid in period t."""
        return self.pols_if(t) * self.gaw(t)

    @var
    def glwb_strain(self, t):
        """The part of those withdrawals the insurer funds itself.

        Zero while the account can pay, and then — for a contract whose
        annuitant outlives their account — every penny, for the rest of
        their life. This is the number a GLWB is priced on.
        """
        return self.pols_if(t) * (self.gaw(t) - self.withdrawal_from_account(t))

    @var
    def death_benefits(self, t):
        """Deaths in period t are paid the account value."""
        return self.pols_death(t) * self.av_eop(t)

    @var(assumption="account")
    def surrender_charge_factor(self, t):
        return self.assumptions.account.surrender_charge.factor(
            self.assumptions.years_elapsed(t)
        )

    @var
    def cash_value(self, t):
        """Cash surrender value per policy at the end of period t."""
        return self.av_eop(t) * (1.0 - self.surrender_charge_factor(t))

    @var
    def surrenders(self, t):
        """Cash paid to voluntary surrenders, who forfeit the rider."""
        return self.pols_lapse(t) * self.cash_value(t)

    @var
    def rider_fee_income(self, t):
        return self.pols_if(t) * self.rider_fee(t)

    @var
    def account_value(self, t):
        """Total account value held at the end of period t."""
        return self._split(t)[1] * self.av_eop(t)

    @var(assumption="interest")
    def v(self, t):
        return self.assumptions.discount(t)

    # --- present values ----------------------------------------------------

    def pv_withdrawals(self):
        return sum(self.withdrawals(t) * self.v(t) for t in range(self.proj_len))

    def pv_glwb_strain(self):
        return sum(self.glwb_strain(t) * self.v(t) for t in range(self.proj_len))

    def pv_death_benefits(self):
        return sum(
            self.death_benefits(t) * self.v(t + 1) for t in range(self.proj_len)
        )

    def pv_surrenders(self):
        return sum(
            self.surrenders(t) * self.v(t + 1) for t in range(self.proj_len)
        )

    def pv_rider_fee_income(self):
        return sum(
            self.rider_fee_income(t) * self.v(t) for t in range(self.proj_len)
        )
