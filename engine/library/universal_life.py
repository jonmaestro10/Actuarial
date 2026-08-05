"""Universal life — the interest-sensitive family.

PLAN.md §5.2: *universal life / interest-sensitive (account-value mechanics,
secondary guarantees)*. Both halves are here — ``UniversalLife`` rolls the
account forward and ``NoLapseGuarantee`` (engine/data/account.py) runs the
shadow account beside it.

Written in indicator style like every template before it, so one instance
evaluates a whole ``(model point x scenario)`` slab. Projection steps are
payment periods, ``assumptions.freq`` to the year.

Why this one is structurally different
--------------------------------------
Every earlier template projects a benefit the policy document fixes. Here the
benefit is the account, and the account is the running result of charges the
insurer sets against a rate the insurer credits. Two things follow that no
earlier template needed:

- **The contract can lapse from arithmetic.** When the account cannot meet
  its own monthly deductions the policy leaves the book, on a date that is
  an output of the projection rather than an input to it. ``av_exhausted``
  and ``in_force_av`` carry that, and ``in_force_av`` is **absorbing**: a
  contract that lapses for non-payment does not come back when markets
  recover, and writing the indicator as a running product rather than a
  per-period test is what makes sure of it.
- **The crediting floor is a written option.** Under a portfolio-rate basis
  the minimum guaranteed rate costs exactly nothing in a deterministic
  projection that stays above it, and costs real money across a
  distribution that does not. ``guarantee_cost`` is that difference, and it
  is why this family belongs in the stochastic executor.

Order of operations within period ``t``
---------------------------------------
This is the whole product, and every ``@var`` below is one step of it:

1. ``premium(t)`` is received at the start of the period, and
   ``premium_load`` is taken off it.
2. What is left joins the account: ``av_after_premium``.
3. The contractual ``policy_fee`` comes out: ``av_after_fee``.
4. The **death benefit is struck** on the account as it now stands —
   ``max(face + option-B account, corridor x account)``.
5. The **net amount at risk** is the death benefit less that same account,
   and ``coi_due`` is charged on it.
6. Deductions are capped at the account, which cannot go negative:
   ``av_after_charges``. Whether the cap bit is ``av_exhausted``.
7. Interest is credited on the account after all deductions:
   ``interest_credited``, and ``av_eop``.

Steps 3–5 are the one place where a defensible alternative changes real
numbers. Striking the death benefit *before* the policy fee would raise the
net amount at risk and so the COI; deducting the COI before computing the
death benefit is circular and the engine's cycle detector says so rather
than iterating to a fixed point nobody asked for. The order above — fee,
then benefit, then COI — is the textbook monthly deduction sequence, and it
is a stated convention, not an inevitability.

Model point fields: ``age_at_entry`` (int), ``term_years`` (int),
``face_amount``, ``annual_premium``, ``init_pols``, and optionally
``init_av`` (in-force business; 0 for new business), ``db_option``
(1 = level, 2 = increasing; defaults to 1) and ``premium_years`` (how long
premiums are paid; defaults to the term).

Assumption bindings: ``mortality``, ``lapse`` (voluntary), ``interest``
(discounting, and the earned rate when no scenario set is bound),
``account`` (an :class:`~engine.data.account.AccountBasis`).
Scenario binding: earned rate via ``self.scenarios.ret(t)``, optional.

Present values require ``proj_len > term_years`` so the maturity payment at
``t == periods(term_years)`` falls inside the summation.
"""

from __future__ import annotations

import numpy as np

from engine.core.model import Model, var

#: Death-benefit option codes. Option A pays the face amount; option B pays
#: the face amount *plus* the account, which is a larger benefit and a
#: larger net amount at risk for the whole life of the contract.
OPTION_LEVEL, OPTION_INCREASING = 1, 2


class UniversalLife(Model):
    # --- policy data, read defensively ------------------------------------
    #
    # Three model-point fields are optional because the great majority of
    # blocks do not carry them. Each is read through a helper that supplies
    # the neutral value, so a model point written before they existed
    # projects to the same numbers rather than raising.

    def _init_av(self):
        return getattr(self.mp, "init_av", 0.0) * 1.0

    def _db_option(self):
        return getattr(self.mp, "db_option", OPTION_LEVEL) * 1.0

    def _premium_periods(self):
        years = getattr(self.mp, "premium_years", None)
        if years is None:
            years = self.mp.term_years
        return self.assumptions.periods(years)

    @var
    def age(self, t):
        """Attained age at the start of period t."""
        return self.mp.age_at_entry + self.assumptions.years_elapsed(t)

    @var
    def duration(self, t):
        """Completed policy years at the start of period t."""
        return self.assumptions.years_elapsed(t)

    @var
    def in_term(self, t):
        """1 during the contract term, 0 after."""
        return (t < self.assumptions.periods(self.mp.term_years)) * 1.0

    @var
    def paying(self, t):
        """1 while premiums are still due, 0 after.

        A universal-life premium is flexible by design; this template
        projects the premium the model point states, for as long as it
        states, which is what a valuation basis actually holds. A policy
        that stops paying early is a different model point, not a branch.
        """
        return (t < self._premium_periods()) * self.in_term(t)

    # --- the account roll-forward -----------------------------------------

    @var
    def av_boy(self, t):
        """Account value per policy at the start of period t."""
        if t == 0:
            return self._init_av() + 0.0 * self.mp.face_amount
        return self.av_eop(t - 1) * self.in_term(t)

    @var
    def premium_per_pol(self, t):
        """Premium received at the start of period t, per policy."""
        return self.assumptions.per_period(self.mp.annual_premium) * self.paying(t)

    @var(assumption="account")
    def premium_load(self, t):
        """Percent-of-premium charge taken off the premium before it
        reaches the account."""
        return self.premium_per_pol(t) * self.assumptions.account.premium_load

    @var
    def av_after_premium(self, t):
        """Account after the net premium goes in (step 2)."""
        return self.av_boy(t) + self.premium_per_pol(t) - self.premium_load(t)

    @var(assumption="account")
    def policy_fee(self, t):
        """Contractual per-policy charge for period t (step 3).

        A charge written into the contract, not an expense the insurer
        incurs. The two are different numbers and live in different bases —
        this one is not inflated, because the policy document does not
        inflate it.
        """
        return (
            self.assumptions.per_period(self.assumptions.account.policy_fee)
            * self.in_term(t)
        )

    @var
    def av_after_fee(self, t):
        """Account after the policy fee. May be negative — that is the
        signal the contract cannot pay for itself, and it is read as such
        two steps below rather than hidden by a floor here."""
        return self.av_after_premium(t) - self.policy_fee(t)

    @var(assumption="account")
    def corridor_factor(self, t):
        """Minimum ratio of death benefit to account at this attained age."""
        return self.assumptions.account.corridor.factor(self.age(t))

    @var
    def death_benefit(self, t):
        """Death benefit per policy for period t (step 4).

        The account enters floored at zero: a contract whose account has
        gone negative still pays its face amount, and letting a negative
        account reduce the benefit would be an arithmetic accident rather
        than a product feature.
        """
        account = np.maximum(self.av_after_fee(t), 0.0)
        option_b = (self._db_option() == OPTION_INCREASING) * 1.0
        stated = self.mp.face_amount + option_b * account
        return np.maximum(stated, self.corridor_factor(t) * account) * self.in_term(t)

    @var
    def nar(self, t):
        """Net amount at risk — the part of the death benefit the insurer
        is actually exposed to, the rest being the policyholder's own
        account."""
        return np.maximum(
            self.death_benefit(t) - np.maximum(self.av_after_fee(t), 0.0), 0.0
        )

    @var(assumption="mortality")
    def q_x(self, t):
        """Mortality rate applying during period t (0 after the term)."""
        return self.assumptions.periodic_q(
            self.age(t), t, sex=getattr(self.mp, "sex", None)
        ) * self.in_term(t)

    @var(assumption="account")
    def coi_due(self, t):
        """Cost-of-insurance charge for period t (step 5)."""
        return self.assumptions.account.coi.rate(self.q_x(t)) * self.nar(t)

    @var
    def charges_due(self, t):
        """Everything the account owes this period."""
        return self.policy_fee(t) + self.coi_due(t)

    @var
    def av_after_charges(self, t):
        """Account after deductions, floored at zero (step 6).

        The floor is the product: an account cannot go overdrawn, so the
        deduction simply takes what is there and the shortfall becomes the
        lapse below.
        """
        return np.maximum(self.av_after_premium(t) - self.charges_due(t), 0.0)

    @var
    def av_exhausted(self, t):
        """1 where the account could not meet its charges this period."""
        return (
            self.av_after_premium(t) - self.charges_due(t) < 0.0
        ) * self.in_term(t)

    @var
    def earned_rate(self, t):
        """Return on the assets backing the account over period t.

        A scenario return when a scenario set is bound, the valuation
        interest rate otherwise. Which of the two applies is a property of
        the *run*, not of a model point, so it is an ordinary Python branch
        — the same kind as ``t == 0`` — and not an indicator.
        """
        if self.scenarios is None:
            return self.assumptions.period_accumulation() - 1.0
        lookup_t = min(t, self.scenarios.horizon - 1)
        return self.scenarios.ret(lookup_t)

    @var(assumption="account")
    def credited_rate(self, t):
        """Rate credited to the account over period t, floor included."""
        return self.assumptions.periodic_credited(self.earned_rate(t))

    @var(assumption="account")
    def credited_rate_unfloored(self, t):
        """The same rate with the minimum guarantee switched off."""
        return self.assumptions.periodic_credited_unfloored(self.earned_rate(t))

    @var
    def interest_credited(self, t):
        """Interest added to the account at the end of period t (step 7)."""
        return self.av_after_charges(t) * self.credited_rate(t)

    @var
    def av_eop(self, t):
        """Account value per policy at the end of period t."""
        return self.av_after_charges(t) + self.interest_credited(t)

    @var
    def guarantee_cost_per_pol(self, t):
        """What the crediting floor paid the policyholder in period t.

        Zero wherever the floor did not bite — which is every period of a
        deterministic projection above it, and the reason a deterministic
        valuation of this contract values the guarantee at nothing.
        """
        excess = self.credited_rate(t) - self.credited_rate_unfloored(t)
        return self.av_after_charges(t) * np.maximum(excess, 0.0)

    # --- the shadow account -----------------------------------------------
    #
    # Identical arithmetic on its own parameters, with two deliberate
    # omissions: no corridor and no surrender charge, because neither
    # concept applies to an amount nobody can ever receive. With the
    # guarantee off, `nlg_in_period` is zero at every t and the whole
    # shadow account is exactly zero.

    @var(assumption="account")
    def nlg_in_period(self, t):
        """1 while the secondary guarantee is capable of running."""
        guarantee = self.assumptions.account.no_lapse_guarantee
        return (t < self.assumptions.periods(guarantee.years)) * self.in_term(t)

    @var
    def shadow_boy(self, t):
        """Shadow account per policy at the start of period t."""
        if t == 0:
            return 0.0 * self.mp.face_amount
        return self.shadow_eop(t - 1) * self.nlg_in_period(t)

    @var(assumption="account")
    def shadow_after_charges(self, t):
        """Shadow account after its own premium, fee and COI."""
        guarantee = self.assumptions.account.no_lapse_guarantee
        net_premium = self.premium_per_pol(t) * (1.0 - guarantee.premium_load)
        fee = self.assumptions.per_period(guarantee.policy_fee)
        account = self.shadow_boy(t) + net_premium - fee
        # The shadow account is always tested on guaranteed charges, and its
        # amount at risk is the stated face — a shadow account carries no
        # corridor, so there is no benefit here to strike one against.
        coi = guarantee.coi.rate(self.q_x(t), guaranteed=True) * np.maximum(
            self.mp.face_amount - np.maximum(account, 0.0), 0.0
        )
        return np.maximum(account - coi, 0.0) * self.nlg_in_period(t)

    @var(assumption="account")
    def shadow_eop(self, t):
        """Shadow account per policy at the end of period t."""
        guarantee = self.assumptions.account.no_lapse_guarantee
        credited = guarantee.crediting.credited(
            self.earned_rate(t), freq=self.assumptions.freq
        )
        return self.shadow_after_charges(t) * (1.0 + credited)

    @var
    def guarantee_holding(self, t):
        """1 where the secondary guarantee is keeping the contract alive.

        Which is more than "the shadow account is positive": it has to be
        positive at a moment when the real account could not pay, or the
        guarantee is doing nothing and should not be reported as doing
        something.
        """
        return (self.shadow_eop(t) > 0.0) * self.av_exhausted(t)

    # --- persistency -------------------------------------------------------

    @var
    def in_force_av(self, t):
        """1 while the contract has not lapsed for non-payment.

        **Absorbing.** A contract that could not meet its charges is off the
        book, and a later recovery in the account — which cannot happen, but
        the projection does not know that — must not resurrect it. Writing
        this as a running product rather than a per-period test is what
        makes that structural instead of incidental.
        """
        if t == 0:
            return 1.0 + 0.0 * self.mp.face_amount
        lapsed = self.av_exhausted(t - 1) * (1.0 - self.guarantee_holding(t - 1))
        return self.in_force_av(t - 1) * (1.0 - lapsed)

    @var(assumption="lapse")
    def lapse_rate(self, t):
        """Voluntary lapse rate applying during period t.

        Distinct from lapse for non-payment above, and the two must not be
        merged: this one surrenders an account and receives its cash value,
        the other one walks away from an account that is already empty.
        """
        return self.assumptions.periodic_lapse() * self.in_term(t)

    def _decrements(self, t):
        return {"mortality": self.q_x(t), "lapse": self.lapse_rate(t)}

    def _split(self, t):
        return self.assumptions.decrements.split(
            self.pols_if(t), self._decrements(t)
        )

    @var
    def pols_if(self, t):
        """Policies in force at the start of period t.

        Both decrements act, and then the non-payment indicator applies on
        top: a block can lose policies to death, to surrender, and to the
        account running dry, and only the first two are rates.
        """
        if t == 0:
            return self.mp.init_pols * 1.0
        survivors = self._split(t - 1)[1]
        return survivors * self.in_term(t) * self.in_force_av(t)

    @var
    def pols_death(self, t):
        return self._split(t)[0]["mortality"]

    @var
    def pols_lapse(self, t):
        return self._split(t)[0]["lapse"]

    # --- cashflows ---------------------------------------------------------

    @var
    def premiums(self, t):
        """Premium income at the start of period t."""
        return self.pols_if(t) * self.premium_per_pol(t)

    @var
    def death_claims(self, t):
        """Death benefits arising in period t, paid at the end of it."""
        return self.pols_death(t) * self.death_benefit(t)

    @var(assumption="account")
    def surrender_charge_factor(self, t):
        return self.assumptions.account.surrender_charge.factor(self.duration(t))

    @var
    def cash_value(self, t):
        """Cash surrender value per policy at the end of period t."""
        return self.av_eop(t) * (1.0 - self.surrender_charge_factor(t))

    @var
    def surrenders(self, t):
        """Cash paid to voluntary surrenders in period t."""
        return self.pols_lapse(t) * self.cash_value(t)

    @var
    def maturity_benefits(self, t):
        """Account paid to survivors at the end of the term."""
        if t == 0:
            return 0.0 * self.mp.face_amount
        return (
            self._split(t - 1)[1]
            * self.in_force_av(t)
            * self.av_eop(t - 1)
            * (t == self.assumptions.periods(self.mp.term_years))
        )

    @var
    def charge_income(self, t):
        """Contractual charges collected from in-force policies in period t.

        An internal deduction, not a company cashflow — the money moves from
        the policyholder's account to the insurer's, and the premium that
        funded it has already been counted in ``premiums``. Reported because
        it is where a universal-life contract's margin actually comes from.
        """
        return self.pols_if(t) * (
            self.premium_load(t) + self.policy_fee(t) + self.coi_due(t)
        )

    @var
    def guarantee_cost(self, t):
        """Cost of the crediting floor across in-force policies in period t."""
        return self.pols_if(t) * self.guarantee_cost_per_pol(t)

    @var
    def account_value(self, t):
        """Total account value held at the end of period t — the liability
        this contract carries, before any reserve strengthening."""
        return self._split(t)[1] * self.in_force_av(t) * self.av_eop(t)

    @var
    def nlg_claims(self, t):
        """Death claims paid in period t on contracts the secondary
        guarantee is keeping alive.

        The guarantee's own cost, and the number a no-lapse guarantee is
        bought and sold on: benefits the insurer pays out of an account that
        is empty.
        """
        return self.death_claims(t) * self.guarantee_holding(t)

    @var(assumption="interest")
    def v(self, t):
        """Discount factor from the start of period t back to time 0."""
        return self.assumptions.discount(t)

    # --- present values ----------------------------------------------------

    def pv_premiums(self):
        return sum(self.premiums(t) * self.v(t) for t in range(self.proj_len))

    def pv_death_claims(self):
        return sum(
            self.death_claims(t) * self.v(t + 1) for t in range(self.proj_len)
        )

    def pv_surrenders(self):
        return sum(
            self.surrenders(t) * self.v(t + 1) for t in range(self.proj_len)
        )

    def pv_maturity_benefits(self):
        return sum(
            self.maturity_benefits(t) * self.v(t) for t in range(self.proj_len)
        )

    def pv_charge_income(self):
        return sum(
            self.charge_income(t) * self.v(t) for t in range(self.proj_len)
        )

    def pv_guarantee_cost(self):
        return sum(
            self.guarantee_cost(t) * self.v(t + 1) for t in range(self.proj_len)
        )

    def pv_nlg_claims(self):
        return sum(
            self.nlg_claims(t) * self.v(t + 1) for t in range(self.proj_len)
        )
