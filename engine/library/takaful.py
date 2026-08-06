"""Family takaful — the risk fund, the operator's two fees, and the qard.

The landscape report's §5 lists takaful as a capability no incumbent ships
and this repo did not either. It sits on the with-profits chassis
(``engine/library/with_profits.py``) because the structure is the same one:
two funds, a rule for moving money between them, and a pooled quantity no
per-policy formula can see. What is different is *whose* money each fund is,
and that difference is the product.

Three funds, and the operator does not underwrite
-------------------------------------------------
A conventional insurer takes a premium, bears the risk, and keeps the
profit. A takaful operator does none of those things. Participants donate
(``tabarru'``) into a **participants' risk fund** which they collectively
own; claims are paid out of it; whatever is left over is theirs. The
operator manages the fund for a fee and is not a party to the risk.

So a contribution splits three ways, exactly, every period:

``contribution = wakala fee + savings + tabarru``

- the **wakala fee** is the operator's agency fee, a stated proportion of
  the gross contribution, taken up front and gone;
- **savings** go to the participant's own investment account (the PIF),
  which is theirs and is paid back on death, maturity or surrender;
- the **tabarru'** is the donation into the risk fund (the PRF), and it is
  the only part that is genuinely at risk.

Two fee models, and they are parameters rather than branches
-----------------------------------------------------------
**Wakala** — the operator takes ``wakala_fee`` of gross contributions and no
share of anything else. Surplus is entirely the participants'.

**Mudarabah** — the operator takes no contribution fee and instead shares
*profit* at a stated ratio, as the managing partner of a capital the
participants supplied.

Most real operators run a **hybrid**: wakala on contributions plus mudarabah
on investment income. Both are carried here as ordinary numbers, so a pure
model of either is the hybrid with one of them at zero, and there is no
branch anywhere. Setting ``wakala_fee = 0`` gives pure mudarabah; setting
both profit shares to zero gives pure wakala.

The two profit shares are **not** the same number and are kept apart
deliberately, because they are close enough to be mistaken for each other:

``mudarabah_share``
    the operator's share of the risk fund's *investment* return.
``operator_surplus_share``
    the operator's share of the *underwriting* surplus at distribution — a
    performance fee, and the thing a pure-mudarabah operator lives on.

The mudarabah share is a call option, and that is not a metaphor
---------------------------------------------------------------
A mudarib shares profit and does **not** share loss: the capital provider
bears a loss alone. So the operator's take is
``mudarabah_share × max(earned, 0)`` — not ``mudarabah_share × earned`` —
and the asymmetry has a consequence nobody quotes. Hold the expected return
fixed and raise the volatility, and the operator's expected fee **rises**
while the participants' expected return **falls** by the same amount. The
operator is long a call on the fund's return, struck at zero, and the
participants are short it.

That is a real feature of the contract rather than a modelling artefact, and
it is invisible in any deterministic projection — the same shape as the
monthly-sum crediting design in ``engine/data/index_credit.py``, and worth
the same warning. ``tests/test_takaful.py`` measures it.

Qard hasan, and who pays for last year's claims
-----------------------------------------------
When the risk fund cannot meet its claims, the operator lends it the
shortfall — **qard hasan**, a benevolent loan, interest-free by
construction. It is repaid only out of the fund's future surplus, ahead of
any distribution to participants, and if the fund never generates one the
operator writes it off. There is no other repayment mechanism, which is why
``qard_outstanding`` at the end of a run-off is the operator's realised loss
and is exposed as a variable rather than left inside the fund arithmetic.

**The repayment ordering is a transfer between generations of
participants, and it is the finding this template exists to make
reportable.** A deficit in year three is funded by the operator and repaid
out of year seven's surplus — so the participants present in year seven pay
for claims incurred before some of them joined, and the participants who
were present in year three and have since left take none of it. The rule is
not a choice: AAOIFI and the IFSB both require the qard to be repaid from
surplus before any distribution. But the size of what it moves is a choice
nobody measures, so this computes the counterfactual **on purpose**:

``surplus_if_qard_ignored``
    what would have been distributable had the loan not had priority.

The gap between that and ``distributable_surplus`` is the transfer, and
``qard_transfer_to_participants`` reports the part of it the participants
bear. Neither is used in any cashflow — they exist to be read, in the same
way as ``vm22.floor_outside_reserve`` and ``MackResult.quadrature_total``.

What cannot be expressed here, and it is the third time
------------------------------------------------------
Surplus is allocated to participants in proportion to the tabarru' they have
donated to date, which is the ordinary basis and is what
``tabarru_paid`` carries. That much is expressible.

What is not is **attribution of the qard to the cohorts whose claims drew
it**. The loan is a property of the fund, not of a participant, and a
repayment made at ``t`` cannot be traced to the deficit at ``s`` that caused
it — the fund has no memory of which period's shortfall each unit of qard
covered, and the participants who caused it may have left. RFC-041's spouse
pension escalating from the date of death and RFC-042's LTC benefit pool are
the same shape of limit: a quantity that depends on **when** something
entered a state rather than merely that it is there. This is the third
instance and the first one to appear in a pooled fund rather than in a state
chain, which is worth recording, because the limit is evidently a property
of the *question* and not of the multi-state engine.

The honest response is the one taken elsewhere: report the aggregate, say
what it cannot be split into, and refuse to invent an attribution the
contract does not define.

The same shape appears once more at the other end of the run. The risk fund
**outlives the participants**: every contract runs off, the fund still holds
a balance, the distribution rule still releases a share of it, and there is
nobody with a claim on it. The contract does not say who gets that, and
practice does not agree — some jurisdictions require the residual to go to
charity, some carry it to the next generation's fund, some let the operator
retain it. ``unallocated_surplus`` reports it, and nothing allocates it.

Model point fields: ``age_at_entry`` (int), ``term_years`` (int),
``sum_covered``, ``annual_contribution``, ``init_pols``, and optionally
``initial_pif`` for in-force business.

Assumption bindings: ``mortality``, ``lapse``, ``interest`` (discounting and,
without scenarios, the earned rate), ``expenses``. Earned rate:
``self.scenarios.ret(t)`` where a scenario set is bound, otherwise the
valuation rate — the same convention ``with_profits.py`` uses.
"""

from __future__ import annotations

import numpy as np

from engine.core.model import Model, pool, var


class FamilyTakaful(Model):
    """A regular-contribution family takaful plan on the hybrid model.

    Death pays the sum covered out of the risk fund and the participant's
    own account balance out of the investment fund; maturity and surrender
    pay the account balance alone.
    """

    #: The operator's agency fee, as a proportion of the **gross**
    #: contribution. Zero gives a pure mudarabah plan with no branch.
    wakala_fee = 0.30

    #: The share of the post-fee contribution that goes to the participant's
    #: own investment account rather than being donated to the risk fund.
    #: Zero gives a pure protection plan whose only asset is the risk fund.
    savings_share = 0.60

    #: The operator's share of the risk fund's **investment** return.
    #: Applied to the positive part only — a mudarib shares profit and not
    #: loss, which is what makes it an option rather than a fee.
    mudarabah_share = 0.20

    #: The operator's share of the **underwriting** surplus at distribution.
    #: A performance fee. Distinct from ``mudarabah_share`` and easy to
    #: confuse with it, which is why the two are named apart.
    operator_surplus_share = 0.0

    #: The proportion of the risk fund's post-qard balance released at each
    #: distribution. 1.0 empties the fund every period and leaves nothing to
    #: meet the next year's claims; 0.0 never distributes and lets the fund
    #: grow without limit. Neither is a real operator's rule, and the number
    #: is a management action rather than an assumption — the same status as
    #: ``WithProfitsEndowment.bonus_rate``.
    distribution_rate = 0.25

    #: Seed capital in the risk fund at time zero, for the whole block. A
    #: fund starting at zero draws qard the first time claims arrive before
    #: contributions have accumulated, which is the usual position of a new
    #: takaful window and is why the default is zero rather than something
    #: comfortable.
    initial_risk_fund = 0.0

    def _initial_pif(self):
        return getattr(self.mp, "initial_pif", 0.0) * 1.0

    # --- the policy --------------------------------------------------------

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

    # --- the contribution, split three ways --------------------------------

    @var
    def contribution(self, t):
        """Gross contribution due in period t, per policy."""
        return (self.assumptions.per_period(self.mp.annual_contribution)
                * self.in_term(t))

    @var
    def wakala_fee_charged(self, t):
        """The operator's agency fee, per policy.

        Taken off the gross contribution before anything else, and it is
        gone: it is not a charge against the risk fund and does not come
        back to the participants in any surplus.
        """
        return self.contribution(t) * self.wakala_fee

    @var
    def savings_in(self, t):
        """Into the participant's own investment account, per policy."""
        return ((self.contribution(t) - self.wakala_fee_charged(t))
                * self.savings_share)

    @var
    def tabarru(self, t):
        """The donation into the risk fund, per policy.

        Written as the residual rather than as its own proportion, so the
        three-way split of a contribution closes **exactly** rather than to
        floating-point tolerance. Two of the three are stated and the third
        is what is left, which is the same discipline
        ``unit_linked.unearned_premium`` applies to a stock defined against
        a flow.
        """
        return (self.contribution(t) - self.wakala_fee_charged(t)
                - self.savings_in(t))

    @var
    def tabarru_paid(self, t):
        """Tabarru' donated by one policy up to and **including** period t.

        The basis surplus is allocated on. Inclusive rather than opening,
        because period ``t``'s surplus arises *after* period ``t``'s
        donation has gone into the fund — allocating it on the opening
        figure would give the first period's surplus to nobody, since at
        ``t = 0`` nobody has donated anything yet.

        It carries *when* a participant joined — a late entrant has donated
        less — which is as much of the timing question as an allocation rule
        can express. What it cannot carry is the qard; see the module
        docstring.
        """
        if t == 0:
            return self.tabarru(0)
        return self.tabarru_paid(t - 1) + self.tabarru(t)

    # --- the earned rate ---------------------------------------------------

    @var
    def earned_rate(self, t):
        """What the funds earned over period t, gross of the operator's share.

        A scenario return where a scenario set is bound, the valuation rate
        otherwise — the convention ``with_profits.py`` sets.
        """
        if self.scenarios is None:
            return self.assumptions.period_accumulation() - 1.0
        return self.scenarios.ret(min(t, self.scenarios.horizon - 1))

    @var
    def participant_rate(self, t):
        """The return the participants actually keep.

        ``earned − mudarabah_share × max(earned, 0)``. The maximum is the
        whole point: a mudarib shares profit and not loss, so a negative
        return reaches the participants in full and a positive one does
        not. Raise the volatility at a fixed mean and this falls.
        """
        earned = self.earned_rate(t)
        return earned - self.mudarabah_share * np.maximum(earned, 0.0)

    # --- the participant's investment account ------------------------------

    @var
    def pif(self, t):
        """The participant's own account balance at the start of period t.

        Retrospective, like a with-profits asset share, and for the same
        reason: it is what this participant has put in and earned, not what
        anybody has promised them. Nothing is guaranteed about it.
        """
        if t == 0:
            return self._initial_pif() + 0.0 * self.mp.annual_contribution
        opening = self.pif(t - 1) + self.savings_in(t - 1)
        return opening * (1.0 + self.participant_rate(t - 1))

    @var
    def pif_management_fee(self, t):
        """The operator's mudarabah share of the investment return on one
        participant's account, per policy.

        The same option as on the risk fund and struck at the same place:
        ``earned − participant_rate`` is zero whenever the fund lost money.
        """
        opening = self.pif(t) + self.savings_in(t)
        return opening * (self.earned_rate(t) - self.participant_rate(t))

    # --- the risk fund -----------------------------------------------------

    @pool
    def tabarru_received(self, t):
        """Total tabarru' donated into the risk fund in period t."""
        return self.pool_sum(self.pols_if(t) * self.tabarru(t))

    @pool
    def claims_paid(self, t):
        """Sum covered paid out of the risk fund on deaths in period t.

        The risk fund pays the **sum covered** and nothing else. The
        participant's own account balance is also paid on death, out of the
        investment fund, and is not a claim on the pool — treating it as one
        is the commonest way to make a takaful fund look insolvent when it
        is not.
        """
        return self.pool_sum(self.pols_death(t) * self.mp.sum_covered)

    @pool
    def risk_fund_boy(self, t):
        """The risk fund's balance at the start of period t.

        After every movement of the period before it: experience, the qard
        drawn or repaid, and the surplus distributed.
        """
        if t == 0:
            return self.pool_sum(0.0 * self.mp.sum_covered) + self.initial_risk_fund
        return (self.risk_fund_after_qard(t - 1)
                - self.distributable_surplus(t - 1))

    @pool
    def fund_investment_income(self, t):
        """What the risk fund earned in period t, net of the mudarabah share.

        On the opening balance plus the period's tabarru', which assumes
        contributions arrive at the start of the period — the same timing
        the rest of the library uses for a start-of-period flow.
        """
        base = self.risk_fund_boy(t) + self.tabarru_received(t)
        return base * self.participant_rate(t)

    @pool
    def fund_mudarabah_fee(self, t):
        """The operator's share of the risk fund's investment return.

        Zero whenever the fund lost money, which is the asymmetry the
        module docstring calls an option. Reported separately from
        ``wakala_fee_charged`` because the two are earned in completely
        different ways and an operator's income statement that adds them up
        without saying so is hiding where its money comes from.
        """
        base = self.risk_fund_boy(t) + self.tabarru_received(t)
        return base * (self.earned_rate(t) - self.participant_rate(t))

    @pool
    def risk_fund_after_experience(self, t):
        """The fund after the period's tabarru', investment and claims.

        Free to be negative. That is the whole reason a qard facility
        exists, and flooring it here would remove the product's central
        mechanism while leaving every number looking plausible.
        """
        return (self.risk_fund_boy(t) + self.tabarru_received(t)
                + self.fund_investment_income(t) - self.claims_paid(t))

    # --- qard hasan --------------------------------------------------------

    @pool
    def qard_drawn(self, t):
        """The interest-free loan the operator makes in period t.

        Exactly the shortfall and not a penny more. An operator that lent a
        buffer would be capitalising the fund rather than rescuing it, and
        the surplus that buffer then earned would be distributed to
        participants who had not provided it.
        """
        return np.maximum(-self.risk_fund_after_experience(t), 0.0)

    @pool
    def qard_repaid(self, t):
        """Repayment out of period t's surplus, ahead of any distribution.

        Bounded twice — by what is outstanding and by what the fund has —
        because either bound alone permits a nonsense: repaying more than
        was borrowed, or repaying out of a fund that is still in deficit.
        """
        available = np.maximum(self.risk_fund_after_experience(t), 0.0)
        return np.minimum(self.qard_outstanding_boy(t), available)

    @pool
    def qard_outstanding_boy(self, t):
        """The loan owed by the risk fund at the start of period t."""
        if t == 0:
            return self.pool_sum(0.0 * self.mp.sum_covered)
        return (self.qard_outstanding_boy(t - 1) + self.qard_drawn(t - 1)
                - self.qard_repaid(t - 1))

    @pool
    def qard_outstanding(self, t):
        """The loan owed at the **end** of period t.

        A run that finishes with this above zero has finished with the
        operator out of pocket: there is no other repayment route, and the
        loan carries no return, so the closing balance is the operator's
        realised loss on the arrangement.
        """
        return (self.qard_outstanding_boy(t) + self.qard_drawn(t)
                - self.qard_repaid(t))

    @pool
    def risk_fund_after_qard(self, t):
        """The fund after the loan is drawn or repaid, before distribution.

        Never negative, and never negative *by construction* rather than by
        a clip: the draw is exactly the shortfall, so adding it back lands
        on zero.
        """
        return (self.risk_fund_after_experience(t) + self.qard_drawn(t)
                - self.qard_repaid(t))

    # --- surplus distribution ----------------------------------------------

    @pool
    def distributable_surplus(self, t):
        """What the fund releases in period t, to everybody.

        A stated proportion of what is left once the loan is served. The
        proportion is a management action; what is *not* discretionary is
        that the loan comes first.
        """
        return self.distribution_rate * np.maximum(
            self.risk_fund_after_qard(t), 0.0
        )

    @pool
    def surplus_if_qard_ignored(self, t):
        """The same rule with the loan given no priority — **computed on
        purpose, and used by nothing**.

        The tempting reading, and the one an operator's own management
        accounts often show: distributable surplus struck on the fund before
        the loan is served. The gap between this and
        ``distributable_surplus`` is what the repayment rule moves, and it
        is a transfer between generations of participants rather than a
        timing difference — see the module docstring.

        Computing it costs one multiplication and makes a number reportable
        that is otherwise only visible to somebody who reruns the projection
        with the rule turned off.
        """
        return self.distribution_rate * np.maximum(
            self.risk_fund_after_experience(t) + self.qard_drawn(t), 0.0
        )

    @pool
    def qard_transfer_to_participants(self, t):
        """The part of the repayment that comes out of the participants'
        share of surplus.

        ``(1 − operator_surplus_share)`` of the gap above. This is the
        number the finding is about: it is paid by whoever is in the fund at
        ``t``, on account of claims incurred before ``t``, and no part of it
        can be attributed to the cohorts that caused those claims.
        """
        gap = self.surplus_if_qard_ignored(t) - self.distributable_surplus(t)
        return gap * (1.0 - self.operator_surplus_share)

    @pool
    def surplus_to_operator(self, t):
        """The operator's performance fee out of the distributed surplus."""
        return self.distributable_surplus(t) * self.operator_surplus_share

    @pool
    def surplus_to_participants(self, t):
        """The rest of it, which belongs to the participants."""
        return self.distributable_surplus(t) - self.surplus_to_operator(t)

    @pool
    def allocation_base(self, t):
        """Total tabarru' donated to date by the participants still in the
        fund — the denominator surplus is shared on."""
        return self.pool_sum(self.pols_if(t) * self.tabarru_paid(t))

    @var
    def surplus_share_per_pol(self, t):
        """One participant's share of period t's surplus, per policy.

        Pro rata to tabarru' donated to date, over the participants **still
        in the fund**. A participant who has died or surrendered donated
        into the same pool and receives nothing from it, which is not an
        oversight: the tabarru' was a donation, and a donation that came
        back on exit would not be one.
        """
        base = self.allocation_base(t)
        safe = np.where(base > 0.0, base, 1.0)
        share = np.where(base > 0.0, self.tabarru_paid(t) / safe, 0.0)
        return self.surplus_to_participants(t) * share

    @var
    def surplus_paid(self, t):
        """Total surplus paid to this model point's participants in period t.

        Summed over the block, this and ``unallocated_surplus`` come to
        ``surplus_to_participants`` exactly, which is the property the
        allocation has to have and the one ``tests/test_takaful.py``
        asserts.
        """
        return self.pols_if(t) * self.surplus_share_per_pol(t)

    @pool
    def unallocated_surplus(self, t):
        """Surplus declared for participants when there are none left.

        The risk fund outlives the participants. Every contract in the block
        has run off, the fund still holds a balance, the distribution rule
        still releases a share of it — and there is nobody with a claim on
        it, because ``allocation_base`` is zero.

        **The contract does not say who gets this**, and practice does not
        agree: some jurisdictions require the residual to go to charity,
        some carry it forward to the next generation's fund, and some let
        the operator retain it. So it is reported and not allocated, which
        is the same choice ``engine/report/regdiff.py`` makes about its
        named residual and ``engine/report/vm22.py`` about the guarantees
        §13's own arithmetic does not keep. Inventing a rule here would put
        a number in a cashflow that no participant agreed to.
        """
        return np.where(self.allocation_base(t) > 0.0, 0.0,
                        self.surplus_to_participants(t))

    # --- benefits ----------------------------------------------------------

    @var
    def death_benefits(self, t):
        """Total paid on deaths in period t: the sum covered plus the
        participant's own account balance.

        Two funds pay it, and the split matters — ``claims_paid`` is the
        risk fund's part alone.
        """
        return self.pols_death(t) * (self.mp.sum_covered + self.pif(t))

    @pool
    def pif_paid_on_death(self, t):
        """The investment fund's part of period t's death benefits."""
        return self.pool_sum(self.pols_death(t) * self.pif(t))

    @var
    def surrenders(self, t):
        """Surrenders take their account balance and no share of surplus."""
        return self.pols_lapse(t) * self.pif(t)

    @var
    def maturities(self, t):
        """Survivors at the end of the term take their account balance.

        And nothing else: their share of that period's surplus is paid
        through ``surplus_paid`` like everybody else's, and adding it here
        as well would pay it twice.
        """
        if t == 0:
            return 0.0 * self.mp.sum_covered
        matures = (t == self.assumptions.periods(self.mp.term_years))
        return self._split(t - 1)[1] * self.pif(t) * matures

    @pool
    def operator_income(self, t):
        """Everything the operator earns in period t, per block.

        The agency fee on contributions, the mudarabah share of both funds'
        investment return, and the performance fee on surplus. Added up in
        one place because that is the operator's income, and kept as three
        separate variables because they are earned three different ways and
        only one of them is a fee for work.
        """
        return (self.pool_sum(self.pols_if(t) * self.wakala_fee_charged(t))
                + self.pool_sum(self.pols_if(t) * self.pif_management_fee(t))
                + self.fund_mudarabah_fee(t)
                + self.surplus_to_operator(t))

    @var(assumption="interest")
    def v(self, t):
        return self.assumptions.discount(t)

    def pv_operator_income(self):
        return sum(self.operator_income(t) * self.v(t)
                   for t in range(self.proj_len))

    def pv_claims(self):
        return sum(self.claims_paid(t) * self.v(t + 1)
                   for t in range(self.proj_len))
