"""Income protection — the first multi-state template.

PLAN.md §5.2 asks for the health and protection family on a multi-state
Markov engine, and this is the seed of it: a life is **healthy**, **sick**
or **dead**, premiums are paid while healthy, a benefit is paid while sick,
and a sick life can recover.

That last clause is what makes it multi-state rather than multi-decrement.
A decrement model can express falling ill; it cannot express getting better,
because its populations only ever shrink. Here each state is a ``@var`` and
the forward equation is written out as ordinary formulas — the DSL needed no
new primitive for this, which was not obvious in advance.

**Waiver of premium is not a rider here, it is the model.** Premiums are
paid by the healthy, so a life that falls sick stops paying by construction
rather than by a separate benefit switched on beside the sickness one. A
product that charges through sickness would multiply ``sick(t)`` back in.

The chain outlives the contract
-------------------------------
States are **not** masked at the end of the term. A policy's term is a
property of its cashflows, not of the life: the person does not cease to
exist when cover ends, and pretending otherwise would break the invariant
that makes a multi-state model checkable. So occupancy is conserved across
all three states for the whole projection — exactly — and ``in_term`` masks
the premiums and the benefits instead.

Model point fields: ``age_at_entry`` (int), ``term_years`` (int),
``annual_premium``, ``annual_benefit``, ``init_pols``.
Assumption bindings: ``transitions`` (a ``TransitionMatrix`` over the states
below), ``interest``.
"""

from __future__ import annotations

from engine.core.model import Model, var

#: The state names this template expects, in the order a table should list
#: them. A transition matrix over different states raises on the first
#: lookup rather than silently mapping one illness onto another.
HEALTHY, SICK, DEAD = "healthy", "sick", "dead"


class IncomeProtection(Model):
    @var
    def age(self, t):
        """Attained age at the start of period t."""
        return self.mp.age_at_entry + self.assumptions.years_elapsed(t)

    @var
    def in_term(self, t):
        """1 while cover is in force, 0 after. Masks cashflows only — the
        chain itself runs on, because the life does."""
        return (t < self.assumptions.periods(self.mp.term_years)) * 1.0

    def _p(self, t, source, target):
        """One transition probability for period ``t``."""
        matrix = self.assumptions.periodic_transitions()
        age = self.age(t) if matrix.age_dependent else None
        return matrix.p(source, target, age)

    # --- the forward equation, one variable per state ---------------------

    @var(assumption="transitions")
    def healthy(self, t):
        """Lives healthy at the start of period t.

        Not a survivorship: a life can arrive here by recovering, so this
        sequence is not monotone and cannot be written as a running product
        of survival factors.
        """
        if t == 0:
            return self.mp.init_pols * 1.0
        return (
            self.healthy(t - 1) * self._p(t - 1, HEALTHY, HEALTHY)
            + self.sick(t - 1) * self._p(t - 1, SICK, HEALTHY)
        )

    @var(assumption="transitions")
    def sick(self, t):
        """Lives sick at the start of period t."""
        if t == 0:
            return self.mp.init_pols * 0.0
        return (
            self.healthy(t - 1) * self._p(t - 1, HEALTHY, SICK)
            + self.sick(t - 1) * self._p(t - 1, SICK, SICK)
        )

    @var(assumption="transitions")
    def dead(self, t):
        """Lives dead by the start of period t. Absorbing, so this one *is*
        monotone — which makes it the easiest check that the rest is."""
        if t == 0:
            return self.mp.init_pols * 0.0
        return (
            self.dead(t - 1)
            + self.healthy(t - 1) * self._p(t - 1, HEALTHY, DEAD)
            + self.sick(t - 1) * self._p(t - 1, SICK, DEAD)
        )

    @var
    def lives(self, t):
        """Everybody, in whatever state. Constant by construction — the
        invariant a multi-state model is checked against."""
        return self.healthy(t) + self.sick(t) + self.dead(t)

    # --- cashflows --------------------------------------------------------

    @var
    def premiums(self, t):
        """Premiums, paid by the healthy at the start of period t.

        Waiver of premium is not a rider here: a sick life pays nothing
        because premiums are a cashflow of the healthy state.
        """
        return (
            self.healthy(t)
            * self.assumptions.per_period(self.mp.annual_premium)
            * self.in_term(t)
        )

    @var
    def benefits(self, t):
        """Benefit paid to the sick over period t."""
        return (
            self.sick(t)
            * self.assumptions.per_period(self.mp.annual_benefit)
            * self.in_term(t)
        )

    @var
    def incidence(self, t):
        """Lives falling sick during period t — new claims."""
        return self.healthy(t) * self._p(t, HEALTHY, SICK) * self.in_term(t)

    @var
    def recoveries(self, t):
        """Lives recovering during period t. The variable a decrement model
        has no way to produce."""
        return self.sick(t) * self._p(t, SICK, HEALTHY) * self.in_term(t)

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
