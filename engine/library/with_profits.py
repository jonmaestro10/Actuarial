"""With-profits — asset shares, bonuses, smoothing and the estate.

PLAN.md §5.2 lists "with-profits/par funds (asset shares, bonus mechanisms)"
under *later*, and this is it. It is also the first template to use the
``@pool`` variable for the thing that variable was written for: RFC-001
introduced it for "a variable-payment adjustment, **a with-profits bonus or
an asset share**", and only the first of those three had ever exercised it.

A with-profits policy is the opposite of everything else in this library.
Every other template computes what the office **owes**; this one computes
what each policy has **earned**, and then decides how much of that to give
back. The two questions have different answers and the gap between them is
the whole business.

The asset share is retrospective, and that is the point
------------------------------------------------------
    asset share(t+1) = (asset share(t) + premium − expenses)·(1 + earned)
                       − cost of cover
                       + share of the profits on the lives who left

It accumulates what actually happened at what the fund actually earned. Set
against RFC-018's prospective reserve — which values what is still to come
on a basis chosen in advance — it answers a different question, and an
office that confuses the two will pay out the wrong amount.

The **mortality profit** term is the one that makes it a pooled calculation
rather than a per-policy one. When a policyholder dies the office pays the
guaranteed sum assured and releases that life's asset share; the difference
falls on everybody else. A per-policy formula cannot see it.

Two bonuses, and only one of them is a promise
----------------------------------------------
**Reversionary bonus** is added to the sum assured and, once declared, is
**guaranteed** — it cannot be taken back, it increases every future death
and maturity payment, and it therefore increases the reserve. Declaring it
is cheap in the year and expensive for the rest of the contract.

**Terminal bonus** is declared at the moment of payment and guarantees
nothing until then. It is the office's shock absorber, which is why a fund
under pressure cuts terminal bonus first and reversionary bonus last.

The cost of a reversionary bonus is not the bonus
-------------------------------------------------
Adding 2% to the sum assured does not cost 2% of anything current: it costs
the *present value of that increase over the whole remaining term*, on the
valuation basis. Early in a long contract that is a multiple of the annual
declaration, which is measured in tests/test_with_profits.py.

Smoothing, and who pays for it
------------------------------
Payouts are smoothed towards the asset share rather than set equal to it,
so a policy maturing in a bad year is paid more than it earned and one
maturing in a good year less. The difference comes out of — or falls into —
the **estate**: the fund's assets less the aggregate asset share, which is
a pooled quantity by construction and cannot be attributed to any policy.

Model point fields: ``age_at_entry`` (int), ``term_years`` (int),
``sum_assured``, ``annual_premium``, ``init_pols``, and optionally
``initial_asset_share`` for in-force business.

Assumption bindings: ``mortality``, ``lapse``, ``interest`` (discounting),
``expenses``. Earned rate: ``self.scenarios.ret(t)`` where a scenario set is
bound, otherwise the valuation rate.
"""

from __future__ import annotations

import numpy as np

from engine.core.model import Model, pool, var

#: Bonus declared as a proportion of the sum assured (``"simple"``) or of
#: the sum assured plus bonuses already attaching (``"compound"``). The
#: choice compounds, so on a long contract it is not a detail.
BONUS_BASES = ("simple", "compound")


class WithProfitsEndowment(Model):
    """A conventional with-profits endowment.

    The guaranteed sum assured plus attaching reversionary bonuses on death
    or maturity, and a terminal bonus on top at the end.
    """

    #: Reversionary bonus rate declared each year, and the basis it applies
    #: to. Class attributes rather than assumptions because a bonus is a
    #: management action — the office decides it, it is not given to the
    #: office — and a subclass expressing a bonus *rule* overrides
    #: ``declared_bonus`` rather than setting a number here.
    bonus_rate = 0.02
    bonus_basis = "compound"

    #: How hard payouts are pulled towards the asset share. 1.0 pays the
    #: asset share exactly and smooths nothing; 0.0 pays the guarantee and
    #: nothing else. The estate absorbs the difference either way.
    smoothing = 0.75

    def __init_subclass__(cls, **kw):
        super().__init_subclass__(**kw)
        if cls.bonus_basis not in BONUS_BASES:
            raise ValueError(
                f"bonus basis must be one of {BONUS_BASES}, got "
                f"{cls.bonus_basis!r}"
            )

    def _initial_asset_share(self):
        return getattr(self.mp, "initial_asset_share", 0.0) * 1.0

    # --- the policy ------------------------------------------------------

    @var
    def age(self, t):
        return self.mp.age_at_entry + self.assumptions.years_elapsed(t)

    @var
    def in_term(self, t):
        return (t < self.assumptions.periods(self.mp.term_years)) * 1.0

    @var(assumption="mortality")
    def q_x(self, t):
        return self.assumptions.periodic_q(
            self.age(t), t, sex=getattr(self.mp, "sex", None)
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

    # --- the guarantee ---------------------------------------------------

    @var
    def declared_bonus(self, t):
        """Reversionary bonus rate declared for period ``t``.

        A flat rate here. A subclass expressing a real bonus rule —
        smoothed towards the fund's return, or cut when the estate thins —
        overrides this, and everything downstream follows without change.
        """
        return self.bonus_rate * self.in_term(t)

    @var
    def guaranteed_benefit(self, t):
        """Sum assured plus the bonuses attaching at the start of period t.

        **Once declared, never removed.** That is what makes a reversionary
        bonus a guarantee rather than a hope, and it is why the series is
        monotone by construction rather than by assumption.
        """
        if t == 0:
            return self.mp.sum_assured * 1.0
        previous = self.guaranteed_benefit(t - 1)
        base = (previous if self.bonus_basis == "compound"
                else self.mp.sum_assured * 1.0)
        return previous + base * self.declared_bonus(t - 1)

    # --- the asset share -------------------------------------------------

    @var
    def earned_rate(self, t):
        """What the fund earned over period ``t``.

        A scenario return where a scenario set is bound, the valuation rate
        otherwise. The asset share accumulates at *this*, not at the rate
        the reserve is discounted on — the whole distinction between a
        retrospective and a prospective quantity.
        """
        if self.scenarios is None:
            return self.assumptions.period_accumulation() - 1.0
        return self.scenarios.ret(min(t, self.scenarios.horizon - 1))

    @var(assumption="expenses")
    def expense_per_pol(self, t):
        scale = self.assumptions.expenses.renewal
        amount = scale.amount(premium=self.mp.annual_premium,
                              sum_assured=self.mp.sum_assured)
        return (self.assumptions.per_period(amount)
                * self.assumptions.inflation_index(t) * self.in_term(t))

    @var
    def premium_per_pol(self, t):
        return (self.assumptions.per_period(self.mp.annual_premium)
                * self.in_term(t))

    @var
    def asset_share(self, t):
        """What one surviving policy has earned, at the start of period t.

        The retrospective accumulation, including its share of the profit
        released by the lives who left. ``mortality_profit_rate`` is the
        pooled term, and it is what makes this a with-profits calculation
        rather than a unit-linked one.
        """
        if t == 0:
            return self._initial_asset_share() + 0.0 * self.mp.sum_assured
        opening = (self.asset_share(t - 1)
                   + self.premium_per_pol(t - 1)
                   - self.expense_per_pol(t - 1))
        grown = opening * (1.0 + self.earned_rate(t - 1))
        # **Not** masked by ``in_term``. The asset share at ``t == term`` is
        # precisely what the maturity payout is struck against, and zeroing
        # it there paid every policy its guarantee and no terminal bonus at
        # all — the same lesson `IncomeProtection` records as "the chain
        # outlives the contract". The flows into it are already masked, so
        # nothing accrues past the term.
        return (grown
                - self.cost_of_cover(t - 1)
                + self.mortality_profit_rate(t - 1))

    @var
    def cost_of_cover(self, t):
        """The charge one policy bears for the sum at risk this period.

        The guarantee less the asset share, at the mortality rate: what it
        costs the fund to promise more than the policy has earned. Floored
        at zero — a policy whose asset share already exceeds its guarantee
        is not owed a rebate for being over-funded.
        """
        at_risk = np.maximum(self.guaranteed_benefit(t) - self.asset_share(t),
                             0.0)
        return self.q_x(t) * at_risk

    @pool
    def mortality_profit_rate(self, t):
        """Profit released by deaths in period ``t``, per surviving policy.

        When a policyholder dies the fund pays the guarantee and releases
        that life's asset share; the difference falls on everybody else.
        Positive when the released asset shares exceed the claims — which
        happens on a mature block whose policies have out-earned their
        guarantees — and negative when the guarantee is expensive.

        This is the pooled term, and the reason ``@pool`` exists. A
        per-policy formula cannot see a transfer *between* policies.
        """
        released = self.pool_sum(self.pols_death(t) * self.asset_share(t))
        paid = self.pool_sum(self.pols_death(t) * self.guaranteed_benefit(t))
        survivors = self.pool_sum(self._split(t)[1])
        profit = released - paid
        return np.divide(profit, survivors,
                         out=np.zeros_like(np.asarray(profit, np.float64)),
                         where=survivors > 0.0)

    # --- payouts ---------------------------------------------------------

    @var
    def maturity_payout(self, t):
        """What a maturing policy is paid, per policy.

        The guarantee, plus a **terminal bonus** that is the smoothed
        excess of the asset share over it. Smoothing at 1.0 pays the asset
        share exactly; at 0.0 it pays the guarantee and nothing more. The
        payout never falls below the guarantee, which is the promise.
        """
        excess = np.maximum(self.asset_share(t) - self.guaranteed_benefit(t),
                            0.0)
        return self.guaranteed_benefit(t) + self.smoothing * excess

    @var
    def terminal_bonus(self, t):
        """The discretionary part of a maturity payout, per policy."""
        return self.maturity_payout(t) - self.guaranteed_benefit(t)

    @var
    def maturities(self, t):
        """Total paid to survivors at the end of the term."""
        if t == 0:
            return 0.0 * self.mp.sum_assured
        matures = (t == self.assumptions.periods(self.mp.term_years))
        return self._split(t - 1)[1] * self.maturity_payout(t) * matures

    @var
    def death_claims(self, t):
        """Deaths are paid the guarantee — no terminal bonus.

        Which is the ordinary convention and a real economic feature: a
        death claim gives up the discretionary upside, and that is part of
        why the fund can afford to smooth maturities at all.
        """
        return self.pols_death(t) * self.guaranteed_benefit(t)

    @var
    def surrenders(self, t):
        """Surrenders are paid their asset share, less nothing.

        A surrender value scale is contractual and varies by office; paying
        the asset share is the neutral treatment and the one that leaves
        the estate untouched by lapses.
        """
        return self.pols_lapse(t) * self.asset_share(t)

    # --- the estate ------------------------------------------------------

    @pool
    def aggregate_asset_share(self, t):
        """Total asset share carried by the block at the start of period t."""
        return self.pool_sum(self.pols_if(t) * self.asset_share(t))

    @pool
    def smoothing_cost(self, t):
        """What smoothing took out of — or put into — the estate in period t.

        Positive where the fund paid more than the policies had earned.
        Over a whole run-off on a fund that earns its assumption this
        nets towards zero; in any single year it does not, which is the
        point of an estate.
        """
        if t == 0:
            return self.pool_sum(0.0 * self.mp.sum_assured)
        matures = (t == self.assumptions.periods(self.mp.term_years))
        survivors = self._split(t - 1)[1]
        paid = self.pool_sum(survivors * self.maturity_payout(t) * matures)
        earned = self.pool_sum(survivors * self.asset_share(t) * matures)
        return paid - earned

    @var(assumption="interest")
    def v(self, t):
        return self.assumptions.discount(t)

    def pv_maturities(self):
        return sum(self.maturities(t) * self.v(t) for t in range(self.proj_len))

    def pv_death_claims(self):
        return sum(self.death_claims(t) * self.v(t + 1)
                   for t in range(self.proj_len))

    def pv_surrenders(self):
        return sum(self.surrenders(t) * self.v(t + 1)
                   for t in range(self.proj_len))


def reversionary_bonus_cost(basis, age: int, term: int, rate: float, *,
                            sum_assured: float, bonus_rate: float,
                            duration: int = 0) -> float:
    """What declaring one year's reversionary bonus costs, at ``duration``.

    Not the bonus, and the direction runs the opposite way from the first
    guess. A bonus of ``bonus_rate`` on the sum assured raises **every**
    future death and maturity payment by that amount, so its cost is the
    present value of a whole extra endowment of
    ``bonus_rate × sum_assured`` over the remaining term — and a present
    value of something payable in twenty-five years is a *fraction* of its
    face.

    So a declaration is **cheapest at issue and dearest at maturity**: on a
    25-year endowment at 5%, 2% of the sum assured costs 31% of its nominal
    amount at duration 0 and 95% at duration 24. The same declaration, made
    three times over, is three times as expensive at the end as at the
    start — which is why a bonus decision is not the same decision at every
    duration, and why an office declaring a flat rate is not making a flat
    commitment.
    """
    from engine.library import reserves

    remaining = term - duration
    if remaining < 1:
        return 0.0
    unit = reserves.benefit_value(
        basis, age + duration, remaining, rate, product="endowment",
        sum_assured=1.0,
    )[0]
    return float(bonus_rate * sum_assured * unit)
