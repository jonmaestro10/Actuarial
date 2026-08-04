"""The unit-linked / variable annuity family.

``UnitLinkedGMDB`` is the Phase 2 seed: single premium, one GMDB rider,
flat lapse. ``UnitLinkedGMxB`` below is the full template — GMDB, GMAB and
GMWB riders on the same contract, rider charges, and dynamic lapse driven by
guarantee moneyness. Switching every rider off makes the two **bitwise
identical**, which is how the seed's golden tests keep protecting the richer
template (see tests/test_gmxb.py).

Both are written in indicator style: conditions on model-point data are
multiplicative factors, never ``if`` branches, so one model instance
evaluates a whole ``(model point x scenario)`` slab.

--------------------------------------------------------------------------

Single-premium unit-linked contract with a GMDB rider — the seed of the
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
        return self.assumptions.annual_q(
            self.age(t), sex=getattr(self.mp, "sex", None), offset=t
        ) * self.in_term(t)

    @var(assumption="lapse")
    def lapse_rate(self, t):
        """Annual lapse rate applying during year t (after mortality)."""
        return self.assumptions.lapse * self.in_term(t)

    def _decrements(self, t):
        """Independent rates competing during year t, in the order the
        sequential method applies them (see engine/data/decrements.py)."""
        return {"mortality": self.q_x(t), "lapse": self.lapse_rate(t)}

    def _survivors(self, t):
        return self.assumptions.decrements.split(
            self.pols_if(t), self._decrements(t)
        )[1]

    def _exits(self, t, cause):
        return self.assumptions.decrements.split(
            self.pols_if(t), self._decrements(t)
        )[0][cause]

    @var
    def pols_if(self, t):
        """Policies in force at the start of year t."""
        if t == 0:
            return self.mp.init_pols * 1.0
        return self._survivors(t - 1) * (t <= self.mp.term_years - 1)

    @var
    def pols_death(self, t):
        """Deaths during year t."""
        return self._exits(t, "mortality")

    @var
    def pols_lapse(self, t):
        """Lapses during year t."""
        return self._exits(t, "lapse")

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
        return (
            self._survivors(t - 1)
            * self.fund_eoy(t - 1)
            * (t == self.mp.term_years)
        )

    @var(assumption="interest")
    def v(self, t):
        """Discount factor from time t back to time 0."""
        return (1.0 + self.assumptions.interest) ** (-t)

    def pv_fee_income(self):
        return sum(self.fee_income(t) * self.v(t + 1) for t in range(self.proj_len))

    def pv_gmdb_strain(self):
        return sum(self.gmdb_strain(t) * self.v(t + 1) for t in range(self.proj_len))


class UnitLinkedGMxB(Model):
    """Single-premium unit-linked contract carrying GMDB, GMAB and GMWB
    riders together, with dynamic lapse.

    **Order of operations within year ``t``** — every variable below is one
    step of this, and the discounting convention follows from it:

    1. ``fund_boy(t)`` — the unit fund at the start of the year, which is
       last year's fund after that year's withdrawal.
    2. The fund earns the scenario return.
    3. Charges are deducted: the AMC on the grown fund, plus rider fees on
       the amounts guaranteed. Charges are capped at the fund, so a contract
       whose account has run dry stops paying for its riders rather than
       going negative.
    4. The guaranteed withdrawal is taken, from the fund as far as it goes;
       the shortfall is the GMWB claim on the insurer.
    5. Deaths during the year are paid ``max(gmdb_guarantee, fund)`` at the
       end of the year, on the fund after (2)–(4).
    6. Lapses surrender that same fund; they forfeit every guarantee, which
       is what dynamic lapse prices.
    7. At ``t == term_years`` survivors take ``max(fund, gmab_guarantee)``.

    Per-policy vs total: ``fund_*``, ``charges_*`` and ``*_per_pol``
    variables are per policy; every cashflow (``fee_income``,
    ``*_claims``, ``*_strain``, ``withdrawals``, ``surrenders``,
    ``maturity_payments``) is a total over in-force policies.

    Model point fields: ``age_at_entry`` (int), ``term_years`` (int),
    ``premium``, ``init_pols``, ``gmdb_guarantee``, ``gmab_guarantee``,
    ``gmwb_base``, ``gmwb_rate``, ``gmwb_ratchet`` (1.0 on, 0.0 off).
    A rider is switched off by setting its guaranteed amount to zero:
    ``max(0, fund)`` is the fund, and a zero benefit base guarantees a zero
    withdrawal, so no branch is needed anywhere.

    Assumption bindings: ``mortality``, ``interest`` (discounting), ``amc``,
    ``dynamic_lapse``, ``gmdb_fee``, ``gmab_fee``, ``gmwb_fee``.
    Scenario binding: fund returns via ``self.scenarios.ret(t)``.

    Present values require ``proj_len > term_years``, so the maturity
    payment at ``t == term_years`` falls inside the summation.
    """

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
        return self.assumptions.annual_q(
            self.age(t), sex=getattr(self.mp, "sex", None), offset=t
        ) * self.in_term(t)

    # --- GMWB benefit base ------------------------------------------------

    @var
    def benefit_base(self, t):
        """GMWB benefit base at the start of year t.

        With ``gmwb_ratchet`` on, the base steps up to the account value at
        each anniversary and never steps back down — locking in investment
        gains as a higher guaranteed withdrawal for the rest of the
        contract.
        """
        if t == 0:
            return self.mp.gmwb_base * 1.0
        previous = self.benefit_base(t - 1)
        ratcheted = np.maximum(previous, self.fund_eoy(t - 1))
        stepped = (
            self.mp.gmwb_ratchet * ratcheted
            + (1.0 - self.mp.gmwb_ratchet) * previous
        )
        return stepped * self.in_term(t)

    @var
    def gaw(self, t):
        """Guaranteed annual withdrawal available in year t, per policy."""
        return self.benefit_base(t) * self.mp.gmwb_rate * self.in_term(t)

    # --- fund roll-forward ------------------------------------------------

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
        """Fund after year-t growth, before charges and withdrawals."""
        return self.fund_boy(t) * (1.0 + self.fund_ret(t)) * self.in_term(t)

    @var(assumption="amc")
    def charges_due(self, t):
        """AMC plus rider fees falling due at the end of year t, per policy.

        Rider fees are charged on the amounts guaranteed — the exposure the
        insurer is actually running — not on the account value.
        """
        a = self.assumptions
        return (
            self.fund_grown(t) * a.amc
            + self.mp.gmdb_guarantee * a.gmdb_fee
            + self.mp.gmab_guarantee * a.gmab_fee
            + self.benefit_base(t) * a.gmwb_fee
        ) * self.in_term(t)

    @var
    def charges_taken(self, t):
        """Charges actually collected: an exhausted account cannot pay."""
        return np.minimum(self.charges_due(t), self.fund_grown(t))

    @var
    def fund_after_charges(self, t):
        """Fund per policy after year-t charges, before the withdrawal."""
        return self.fund_grown(t) - self.charges_taken(t)

    @var
    def withdrawal_from_fund(self, t):
        """Part of the guaranteed withdrawal the account itself can pay."""
        return np.minimum(self.gaw(t), self.fund_after_charges(t))

    @var
    def fund_eoy(self, t):
        """Fund per policy at the end of year t, after charges and the
        guaranteed withdrawal."""
        return self.fund_after_charges(t) - self.withdrawal_from_fund(t)

    # --- decrements -------------------------------------------------------

    @var
    def guarantee_value(self, t):
        """The guaranteed amount a lapse in year t would forfeit.

        The most valuable of the three riders. Products that weigh them
        differently — a death benefit moves a lapse decision less than a
        living benefit does — override this one variable and leave the rest
        of the template alone.
        """
        return np.maximum(
            self.mp.gmdb_guarantee * 1.0,
            np.maximum(self.mp.gmab_guarantee * 1.0, self.benefit_base(t)),
        )

    @var(assumption="lapse")
    def lapse_rate(self, t):
        """Annual lapse rate during year t, dynamic in the funded ratio of
        the guarantees measured after that year's charges and withdrawal.

        A flat lapse assumption is the zero-sensitivity case of the same
        formula, so there is no branch here — see ``DynamicLapse``.
        """
        return self.assumptions.dynamic_lapse.rate(
            self.guarantee_value(t), self.fund_eoy(t)
        ) * self.in_term(t)

    def _decrements(self, t):
        """Independent rates competing during year t, in the order the
        sequential method applies them (see engine/data/decrements.py)."""
        return {"mortality": self.q_x(t), "lapse": self.lapse_rate(t)}

    def _survivors(self, t):
        return self.assumptions.decrements.split(
            self.pols_if(t), self._decrements(t)
        )[1]

    def _exits(self, t, cause):
        return self.assumptions.decrements.split(
            self.pols_if(t), self._decrements(t)
        )[0][cause]

    @var
    def pols_if(self, t):
        """Policies in force at the start of year t."""
        if t == 0:
            return self.mp.init_pols * 1.0
        return self._survivors(t - 1) * (t <= self.mp.term_years - 1)

    @var
    def pols_death(self, t):
        """Deaths during year t."""
        return self._exits(t, "mortality")

    @var
    def pols_lapse(self, t):
        """Lapses during year t."""
        return self._exits(t, "lapse")

    @var
    def pols_maturity(self, t):
        """Policies reaching maturity at t — non-zero only at
        ``t == term_years``."""
        if t == 0:
            return self.mp.init_pols * 0.0
        return self._survivors(t - 1) * (t == self.mp.term_years)

    # --- cashflows --------------------------------------------------------

    @var
    def fee_income(self, t):
        """Total charges collected in year t from in-force policies."""
        return self.pols_if(t) * self.charges_taken(t)

    @var
    def withdrawals(self, t):
        """Total guaranteed withdrawals paid in year t.

        Paid to policies in force at the start of the year: with
        end-of-year death and lapse conventions, a policy leaving during the
        year has already taken that year's withdrawal, and ``fund_eoy`` —
        which every exit benefit is measured on — is net of it.
        """
        return self.pols_if(t) * self.gaw(t)

    @var
    def gmwb_strain(self, t):
        """Guaranteed withdrawals the insurer funds itself once the account
        is exhausted — the GMWB claim."""
        return self.pols_if(t) * (self.gaw(t) - self.withdrawal_from_fund(t))

    @var
    def death_benefit_per_pol(self, t):
        """Greater of the GMDB guarantee and the fund, per death."""
        return np.maximum(self.mp.gmdb_guarantee * 1.0, self.fund_eoy(t))

    @var
    def gmdb_claims(self, t):
        """Total death benefits in year t, paid at end of year."""
        return self.pols_death(t) * self.death_benefit_per_pol(t) * self.in_term(t)

    @var
    def gmdb_strain(self, t):
        """Death claims in excess of the fund released — the GMDB cost."""
        excess = np.maximum(self.mp.gmdb_guarantee - self.fund_eoy(t), 0.0)
        return self.pols_death(t) * excess * self.in_term(t)

    @var
    def surrenders(self, t):
        """Fund value surrendered by lapses in year t. Guarantees are
        forfeited, so nothing else is paid."""
        return self.pols_lapse(t) * self.fund_eoy(t)

    @var
    def maturity_value_per_pol(self, t):
        """Greater of the fund and the GMAB guarantee, per maturing policy."""
        if t == 0:
            return self.mp.premium * 0.0
        return np.maximum(self.fund_eoy(t - 1), self.mp.gmab_guarantee * 1.0)

    @var
    def maturity_payments(self, t):
        """Total paid to survivors when the contract matures."""
        return self.pols_maturity(t) * self.maturity_value_per_pol(t)

    @var
    def gmab_strain(self, t):
        """Maturity payments in excess of the fund — the GMAB cost."""
        if t == 0:
            return self.mp.premium * 0.0
        excess = np.maximum(self.mp.gmab_guarantee - self.fund_eoy(t - 1), 0.0)
        return self.pols_maturity(t) * excess

    @var
    def guarantee_strain(self, t):
        """Total cost of all three guarantees in year t."""
        return self.gmdb_strain(t) + self.gmwb_strain(t) + self.gmab_strain(t)

    @var(assumption="interest")
    def v(self, t):
        """Discount factor from time t back to time 0."""
        return (1.0 + self.assumptions.interest) ** (-t)

    # End-of-year flows discount at v(t + 1) over t = 0 .. proj_len - 1.

    def pv_fee_income(self):
        return sum(self.fee_income(t) * self.v(t + 1) for t in range(self.proj_len))

    def pv_guarantee_strain(self):
        return sum(
            self.guarantee_strain(t) * self.v(t + 1) for t in range(self.proj_len)
        )

    def pv_rider_result(self):
        """Rider profit before expenses: charges collected less guarantee cost."""
        return self.pv_fee_income() - self.pv_guarantee_strain()
