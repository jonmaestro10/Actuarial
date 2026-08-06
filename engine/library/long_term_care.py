"""Long-term care — two claim states, and the benefit that inflates.

Execution plan §10, item C4, on the multi-state engine
:mod:`engine.data.multistate` with :mod:`engine.library.income_protection`
as the pattern. A life is **active**, receiving **home care**, receiving
**facility care**, or dead; premiums are paid while active, and a benefit is
paid in either claim state at a rate the policy caps.

What LTC adds over income protection
------------------------------------
Income protection has one claim state and a benefit equal to the sum
insured. LTC has three complications, and two of them are what the plan
names.

**Two claim states, and the progression between them.** Home care and
facility care pay differently — a policy typically writes a facility maximum
and expresses home care as a percentage of it — and the movement that
matters is ``home_care → facility_care``, which is common and largely
one-way. :meth:`LongTermCare.progression` is that flow, and it is the
variable a single-claim-state model cannot produce.

**Benefit utilization.** A claimant does not automatically draw the policy
maximum. Home-care claimants typically use fewer hours than the cap allows,
so utilization runs well below 1; facility costs generally *exceed* the cap,
so utilization sits at 1 and the maximum binds. That asymmetry is the real
structure, which is why utilization is per claim state and why
``facility_utilization`` defaults to 1.0. Utilization above 1 is **refused**:
it would pay more than the policy maximum, which no policy does, and it is
what a rate mistaken for a cost-inflation factor looks like.

**Inflation protection.** The maximum grows under a rider, and *how* it grows
is the whole question. ``"simple"`` adds the rate to the original maximum
each year; ``"compound"`` applies it to the grown one. They are nearly the
same for a decade and nothing like each other over the life of a policy
issued at 55 and claimed on at 85 — at 5% over thirty years, compound
reaches 4.32× and simple 2.50×. A module that offered one and called it
inflation protection would be pricing a different product.

Increases land on **anniversaries**, as in
:mod:`engine.library.pension_buyout` and for the same reason: a rider grants
a rise on a policy anniversary, and ``(1 + rate) ** years`` with a fractional
exponent pays part of one.

The benefit pool is not here, and the reason is the interesting part
-------------------------------------------------------------------
Most LTC policies cap the *lifetime* benefit — a pool of money, often
expressed as a number of years of the daily maximum. When the pool is
exhausted the benefit stops, though the claimant is still in a claim state.

**A Markov chain over states cannot express that**, and it is worth being
precise about why: the pool depends on how long *this* claimant has been
claiming, not on the state they are in. Occupancy is a headcount by state;
two lives in ``facility_care`` are indistinguishable to it, one of whom
entered last month and one four years ago. That is the same limitation
RFC-041 hit with a spouse's pension escalating from the date of death — a
quantity that depends on *when* a life entered a state, not merely that it
is there — and it is the second time this shape has come up, which makes it
worth naming rather than rediscovering.

The honest workarounds and what each costs:

- **Add a state.** An ``exhausted`` state with a transition out of each claim
  state gives the right *aggregate* run-off if the exit rate is calibrated,
  and it is memoryless where the real rule is a deterministic countdown.
  Nothing here stops a caller declaring one — the state names come from the
  transition matrix — but this module will not calibrate it for them, and a
  rate this module invented would be a pool nobody bought.
- **Add a duration dimension**, splitting each claim state by time since
  entry. Exact, and it multiplies the state space by the pool length.

Neither is chosen here. What is chosen is to say so, and to leave
``elimination periods`` — LTC's waiting period before benefits begin — out
for exactly the same reason: it is a countdown from the claim date, not a
property of the claim state.

Model point fields: ``age_at_entry`` (int), ``premium_years`` (int),
``annual_premium``, ``annual_benefit_maximum``, ``init_pols``; optional
``home_care_percent``, ``home_care_utilization``, ``facility_utilization``,
``inflation_rate``, ``inflation_mode``.
Assumption bindings: ``transitions`` (a ``TransitionMatrix`` over the states
below), ``interest``.

Premiums stop and benefits do not
---------------------------------
``premium_years`` is the **premium-paying** term, not the cover term. An LTC
policy is guaranteed renewable and pays for as long as the claim lasts, so
benefits run for the whole projection while premiums stop. That is the
opposite arrangement from :class:`~engine.library.income_protection.IncomeProtection`,
where one term masks both, and giving them one field would have made a
limited-pay policy inexpressible.

As there, the chain outlives the contract: states are never masked, because
the person does not cease to exist when premiums do. Occupancy is conserved
across all four states for the whole projection, exactly, and that invariant
is what makes the model checkable.
"""

from __future__ import annotations

import numpy as np

from engine.core.model import Model, var

#: The state names this template expects, in the order a table should list
#: them. A transition matrix over different states raises on the first
#: lookup rather than silently mapping one kind of care onto another.
ACTIVE, HOME_CARE, FACILITY_CARE, DEAD = (
    "active", "home_care", "facility_care", "dead")

#: The four states, for a caller building the matrix.
LTC_STATES = (ACTIVE, HOME_CARE, FACILITY_CARE, DEAD)

#: How an inflation-protection rider grows the maximum. ``"none"`` is a
#: policy without the rider, which is a real product and not an omission.
INFLATION_MODES = ("none", "simple", "compound")


def inflation_factors(mode, rate, freq: int, n_periods: int) -> np.ndarray:
    """The benefit maximum at each period, per unit of the original.

    ``(n_lives, n_periods)``. Compounding is over **completed years**: a
    rider grants its increase on a policy anniversary, so the maximum is
    flat between them and steps on the date. ``(1 + rate) ** years`` with a
    fractional exponent would pay part of a rise nobody has granted.

    The distinction between the modes is not cosmetic and is the reason both
    are here. Simple adds the rate to the *original* maximum every year;
    compound applies it to the grown one. Over the thirty years between a
    policy issued at 55 and a claim at 85, 5% simple reaches 2.50× and 5%
    compound 4.32×.
    """
    mode = np.atleast_1d(np.asarray(mode, dtype=object))
    unknown = sorted({str(m) for m in mode} - set(INFLATION_MODES))
    if unknown:
        raise ValueError(
            f"inflation_mode {unknown} is not one of {list(INFLATION_MODES)}"
        )
    rate = np.atleast_1d(np.asarray(rate, dtype=np.float64))
    if np.any(rate < 0.0):
        raise ValueError(
            "inflation_rate is negative: an inflation-protection rider does "
            "not reduce the maximum it protects. A policy without the rider "
            "is inflation_mode='none'."
        )
    years = np.floor(np.arange(n_periods, dtype=np.float64) / freq)[None, :]
    rate = rate[:, None]
    simple = 1.0 + rate * years
    compound = (1.0 + rate) ** years
    picked = np.where(mode[:, None] == "compound", compound,
                      np.where(mode[:, None] == "simple", simple, 1.0))
    return np.broadcast_to(picked, (mode.size, n_periods)).astype(np.float64)


def _field(mp, name, default):
    return np.atleast_1d(np.asarray(getattr(mp, name, default),
                                    dtype=np.float64))


class LongTermCare(Model):
    def setup(self):
        n = self.proj_len + 1
        freq = self.assumptions.freq
        mode = np.atleast_1d(
            np.asarray(getattr(self.mp, "inflation_mode", "none"),
                       dtype=object))
        self._inflation = inflation_factors(
            mode, _field(self.mp, "inflation_rate", 0.0), freq, n)

        home_pct = _field(self.mp, "home_care_percent", 1.0)
        if np.any(home_pct < 0.0) or np.any(home_pct > 1.0):
            raise ValueError(
                "home_care_percent is the home-care maximum as a fraction of "
                "the facility maximum, and lies in [0, 1]. A policy paying "
                "more at home than in a facility is not one this template "
                "has seen; state it as a larger annual_benefit_maximum."
            )
        self._home_percent = home_pct.reshape(-1, 1)

        for name, default in (("home_care_utilization", 1.0),
                              ("facility_utilization", 1.0)):
            used = _field(self.mp, name, default)
            if np.any(used < 0.0) or np.any(used > 1.0):
                raise ValueError(
                    f"{name} is the fraction of the policy maximum actually "
                    f"claimed and lies in [0, 1]. Above 1 would pay more "
                    f"than the maximum, which is what a cost-inflation "
                    f"factor mistaken for a utilization rate looks like."
                )
            setattr(self, f"_{name}", used.reshape(-1, 1))

    def _p(self, t, source, target):
        """One transition probability for period ``t``."""
        matrix = self.assumptions.periodic_transitions()
        age = self.age(t) if matrix.age_dependent else None
        return matrix.p(source, target, age)

    @var
    def age(self, t):
        """Attained age at the start of period t."""
        return self.mp.age_at_entry + self.assumptions.years_elapsed(t)

    @var
    def paying_premium(self, t):
        """1 while premiums are still due, 0 after.

        Masks the premium only. Benefits run on, because an LTC policy is
        guaranteed renewable and a limited-pay one stops collecting long
        before it stops paying.
        """
        return (t < self.assumptions.periods(self.mp.premium_years)) * 1.0

    # --- the forward equation, one variable per state ---------------------

    @var(assumption="transitions")
    def active(self, t):
        """Lives active at the start of period t.

        Not a survivorship: a life can arrive here by recovering from home
        care, so this sequence is not monotone and cannot be written as a
        running product.
        """
        if t == 0:
            return self.mp.init_pols * 1.0
        return (
            self.active(t - 1) * self._p(t - 1, ACTIVE, ACTIVE)
            + self.home_care(t - 1) * self._p(t - 1, HOME_CARE, ACTIVE)
            + self.facility_care(t - 1) * self._p(t - 1, FACILITY_CARE, ACTIVE)
        )

    @var(assumption="transitions")
    def home_care(self, t):
        """Lives receiving home care at the start of period t."""
        if t == 0:
            return self.mp.init_pols * 0.0
        return (
            self.active(t - 1) * self._p(t - 1, ACTIVE, HOME_CARE)
            + self.home_care(t - 1) * self._p(t - 1, HOME_CARE, HOME_CARE)
            + self.facility_care(t - 1) * self._p(t - 1, FACILITY_CARE,
                                                  HOME_CARE)
        )

    @var(assumption="transitions")
    def facility_care(self, t):
        """Lives in facility care at the start of period t.

        Reached from ``active`` directly — an acute event can put a life
        straight into a facility — and from ``home_care`` by progression,
        which is the flow that dominates in practice.
        """
        if t == 0:
            return self.mp.init_pols * 0.0
        return (
            self.active(t - 1) * self._p(t - 1, ACTIVE, FACILITY_CARE)
            + self.home_care(t - 1) * self._p(t - 1, HOME_CARE,
                                              FACILITY_CARE)
            + self.facility_care(t - 1) * self._p(t - 1, FACILITY_CARE,
                                                  FACILITY_CARE)
        )

    @var(assumption="transitions")
    def dead(self, t):
        """Lives dead by the start of period t. Absorbing, so this one *is*
        monotone — which makes it the easiest check that the rest is."""
        if t == 0:
            return self.mp.init_pols * 0.0
        return (
            self.dead(t - 1)
            + self.active(t - 1) * self._p(t - 1, ACTIVE, DEAD)
            + self.home_care(t - 1) * self._p(t - 1, HOME_CARE, DEAD)
            + self.facility_care(t - 1) * self._p(t - 1, FACILITY_CARE, DEAD)
        )

    @var
    def lives(self, t):
        """Everybody, in whatever state. Constant by construction — the
        invariant a multi-state model is checked against."""
        return (self.active(t) + self.home_care(t) + self.facility_care(t)
                + self.dead(t))

    @var
    def in_claim(self, t):
        """Lives receiving care of either kind at the start of period t."""
        return self.home_care(t) + self.facility_care(t)

    # --- the benefit the policy caps --------------------------------------

    @var
    def benefit_maximum(self, t):
        """The facility maximum per annum at period t, after the rider.

        The policy's stated maximum grown by :func:`inflation_factors`. Home
        care is a percentage of this, which is how policies are written and
        one fewer independent input than two separate maxima.
        """
        return self.mp.annual_benefit_maximum * self.at(self._inflation, t)

    @var
    def home_care_benefits(self, t):
        """Paid to home-care claimants over period t.

        Maximum × the home-care percentage × utilization. Utilization is
        where home care differs from facility care: a claimant using fewer
        hours than the cap allows draws less than the policy would pay.
        """
        return (
            self.home_care(t) * self.benefit_maximum(t)
            * self.at(self._home_percent, 0)
            * self.at(self._home_care_utilization, 0)
            / self.assumptions.freq
        )

    @var
    def facility_benefits(self, t):
        """Paid to facility claimants over period t.

        Utilization here is normally 1: facility costs generally exceed the
        cap, so the maximum binds and the claimant draws all of it.
        """
        return (
            self.facility_care(t) * self.benefit_maximum(t)
            * self.at(self._facility_utilization, 0)
            / self.assumptions.freq
        )

    @var
    def benefits(self, t):
        """Everything paid to claimants over period t."""
        return self.home_care_benefits(t) + self.facility_benefits(t)

    @var
    def premiums(self, t):
        """Premiums, paid by the active at the start of period t.

        Waiver of premium is not a rider here: a life on claim pays nothing
        because premiums are a cashflow of the active state.
        """
        return (
            self.active(t)
            * self.assumptions.per_period(self.mp.annual_premium)
            * self.paying_premium(t)
        )

    # --- the flows worth reporting ----------------------------------------

    @var
    def incidence(self, t):
        """Lives entering claim from active during period t — new claims,
        of either kind."""
        return self.active(t) * (self._p(t, ACTIVE, HOME_CARE)
                                 + self._p(t, ACTIVE, FACILITY_CARE))

    @var
    def progression(self, t):
        """Lives moving from home care to facility care during period t.

        The flow a single-claim-state model has no way to produce, and the
        one that drives the cost: a facility claimant draws the full
        maximum where a home-care claimant draws a fraction of a fraction.
        """
        return self.home_care(t) * self._p(t, HOME_CARE, FACILITY_CARE)

    @var
    def recoveries(self, t):
        """Lives returning to active from either claim state in period t."""
        return (self.home_care(t) * self._p(t, HOME_CARE, ACTIVE)
                + self.facility_care(t) * self._p(t, FACILITY_CARE, ACTIVE))

    @var(assumption="interest")
    def v(self, t):
        """Discount factor from the start of period t back to time 0."""
        return self.assumptions.discount(t)

    def pv_premiums(self):
        return sum(self.premiums(t) * self.v(t) for t in range(self.proj_len))

    def pv_benefits(self):
        return sum(self.benefits(t) * self.v(t) for t in range(self.proj_len))

    def net_pv(self):
        return self.pv_premiums() - self.pv_benefits()
