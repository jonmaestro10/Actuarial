"""Level-premium term assurance — the first product template.

Projection steps are payment periods, ``assumptions.freq`` to the year —
annual by default, so ``t`` counts years unless told otherwise. A year of
age is split into sub-periods by ``MortalityBasis.periodic_rate``, and the
other annual assumptions have matching per-period views on ``Assumptions``;
at ``freq = 1`` every one of them is the annual assumption bit for bit.
See tests/test_frequency.py.

Conventions (see Model docstring in engine/core/model.py):

- ``pols_if(t)``: policies in force at the start of period ``t``.
- Premiums and expenses are paid at the start of period ``t`` by in-force
  policies; death claims arising during period ``t`` are paid at its end.
- Cover runs for ``mp.term_years`` years; ``pols_if`` is zero from the end
  of the term onward.

Running sub-annually does not change the decrement basis — the same
policies are in force at every anniversary — but it does change how exits
split between mortality and lapse, because the two decrements interleave
rather than being applied whole in sequence. That is the finer and more
correct answer, and it is what tests/test_frequency.py pins.

The limit that converges to is available directly:
``Assumptions(decrements="constant_force")`` states it at any frequency,
without projecting monthly to approach it. ``"sequential"`` — the default —
is the fixed order this template has always applied.

Term assurance is priced on **select** rates, so ``q_x`` looks mortality up
by duration as well as by age. Supply a ``MortalityBasis`` carrying a select
table and the first years of cover are read from it; supply an ultimate-only
table — a plain ``MortalityTable``, say — and the duration argument cannot
move a number, so the annual golden suite stands unchanged.

Model point fields: ``age_at_entry`` (int), ``term_years`` (int),
``sum_assured``, ``annual_premium``, ``init_pols``, and optionally
``duration_in_force`` (int, default 0) for a block already part way through
its select period at projection time zero.
Assumption bindings: ``mortality`` (table), ``lapse`` (flat annual rate),
``interest`` (flat annual rate), ``expenses``, ``commission``,
``reinsurance``.

Expenses come in three lines because they fall at three different times and
a pricing basis argues about them separately: **acquisition** once at
inception, **renewal** every period and indexed for inflation, and
**claim** costs on each death settled. Each is quoted on the three usual
bases at once — per policy, percent of premium, per mille sum assured — and
each annual figure is divided by the frequency in one place. Commission runs
at an initial rate for the first years of a policy and a renewal rate after,
optionally with clawback from early lapses. A bare ``expense_per_policy``
is the renewal per-policy loading of a basis with nothing else in it, so the
scalar form keeps its exact numbers; commission and clawback are off unless
asked for. See engine/data/expenses.py.

A reinsurance treaty is a property of the block but what it pays depends on
the policy: a surplus treaty cedes nothing on a small sum assured and most
of a large one, and an excess-of-loss cover pays a layer of each claim
rather than a share of it. ``net_claims`` is what the cedant is left
carrying. No treaty is the default. See engine/data/reinsurance.py.

Formulas are written in indicator style — conditions on model-point data
appear as multiplicative ``(t < term) * 1.0`` factors, never as ``if``
branches — so the identical code runs per policy (scalar interpreter) or
across a whole model-point batch (vectorized executor). Mortality lookups
are clipped into table range and masked by the in-term indicator, keeping
the no-extrapolation guarantee while ages beyond the term stay harmless.
"""

from __future__ import annotations

from engine.core.model import Model, var


class TermLife(Model):
    @var
    def age(self, t):
        """Attained age at the start of period t."""
        return self.mp.age_at_entry + self.assumptions.years_elapsed(t)

    @var
    def duration(self, t):
        """Whole years since underwriting at the start of period t.

        ``duration_in_force`` is how long the policy had already run at
        projection time zero — zero for new business, which is why it
        defaults, and non-zero for an in-force block valued part way
        through its select period.
        """
        return (
            getattr(self.mp, "duration_in_force", 0)
            + self.assumptions.years_elapsed(t)
        )

    @var
    def in_term(self, t):
        """1 during the cover term, 0 after."""
        return (t < self.assumptions.periods(self.mp.term_years)) * 1.0

    @var(assumption="mortality")
    def q_x(self, t):
        """Mortality rate applying during period t (0 after the term).

        Duration is passed on every lookup. On an ultimate-only basis it is
        inert — the rate is the same bits either way — and on a
        select-and-ultimate basis it is what makes a recently underwritten
        life cheaper than an identically aged one selected long ago.
        """
        return self.assumptions.periodic_q(
            self.age(t), t, sex=getattr(self.mp, "sex", None),
            duration=self.duration(t),
        ) * self.in_term(t)

    @var(assumption="lapse")
    def lapse_rate(self, t):
        """Lapse rate applying during period t (after mortality)."""
        return self.assumptions.periodic_lapse() * self.in_term(t)

    # Mortality and lapse compete for the same lives during a period. The
    # assumption set owns how they combine — see engine/data/decrements.py.
    # The default, `sequential`, is the fixed order this template always
    # applied, reproduced operand for operand.

    def _decrements(self, t):
        """Independent rates competing during period t, in the order the
        sequential method applies them."""
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
        return self._survivors(t - 1) * (
            t <= self.assumptions.periods(self.mp.term_years) - 1
        )

    @var
    def pols_death(self, t):
        """Deaths during year t."""
        return self._exits(t, "mortality")

    @var
    def pols_lapse(self, t):
        """Lapses during year t."""
        return self._exits(t, "lapse")

    @var
    def claims(self, t):
        """Death claims arising during year t, paid at end of year."""
        return self.pols_death(t) * self.mp.sum_assured

    @var
    def premiums(self, t):
        """Premium income at the start of period t."""
        return (
            self.pols_if(t)
            * self.assumptions.per_period(self.mp.annual_premium)
            * self.in_term(t)
        )

    # --- expenses and commission -----------------------------------------

    def _expense_bases(self):
        """What the three expense loadings are quoted against.

        Annual figures, both of them: the loadings are annual and get
        divided once, in one place, which is what keeps ``freq = 1`` an
        exact identity.
        """
        return {"premium": self.mp.annual_premium,
                "sum_assured": self.mp.sum_assured}

    @var(assumption="expenses")
    def initial_expenses(self, t):
        """Acquisition expenses, incurred once at inception.

        Not indexed: they fall at projection time zero, so there is nothing
        to index them over.
        """
        amount = self.assumptions.expenses.initial.amount(**self._expense_bases())
        return self.mp.init_pols * amount * (t == 0)

    @var(assumption="expenses")
    def expenses(self, t):
        """Maintenance expenses at the start of period t, indexed.

        The annual renewal loading divided by the frequency, inflated to the
        start of the period. A bare ``expense_per_policy`` is the
        per-policy component of this with everything else zero, so the
        scalar form keeps its exact numbers.
        """
        a = self.assumptions
        annual = a.expenses.renewal.amount(**self._expense_bases())
        return (
            self.pols_if(t)
            * (a.per_period(annual) * a.inflation_index(t))
            * self.in_term(t)
        )

    @var(assumption="expenses")
    def claim_expenses(self, t):
        """Cost of settling the claims arising in period t.

        Indexed to the **end** of the period, where the claim is paid — the
        same convention the discounting uses for a claim.
        """
        a = self.assumptions
        amount = a.expenses.claim.amount(**self._expense_bases())
        return self.pols_death(t) * amount * a.inflation_index(t + 1)

    @var(assumption="commission")
    def commission(self, t):
        """Commission on the premium collected at the start of period t.

        The initial/renewal boundary is a **policy duration** boundary, so
        it sits where it sits whatever the projection frequency.
        """
        a = self.assumptions
        return self.premiums(t) * a.commission.rate(self.duration(t))

    # --- reinsurance ------------------------------------------------------

    @var(assumption="reinsurance")
    def reinsurance_recovery(self, t):
        """Recovered from the reinsurer on the claims arising in period t.

        A treaty is a property of the block, but what it pays depends on the
        policy: a surplus treaty cedes nothing on a small sum assured and
        most of a large one, and an excess-of-loss cover pays a layer of
        each claim rather than a share of it. Both are the same call.
        """
        treaty = self.assumptions.reinsurance
        return self.pols_death(t) * treaty.recovery_per_claim(
            self.mp.sum_assured
        )

    @var
    def net_claims(self, t):
        """Death claims the cedant is left carrying."""
        return self.claims(t) - self.reinsurance_recovery(t)

    @var(assumption="reinsurance")
    def reinsurance_premium(self, t):
        """Premium ceded at the start of period t, by policies in force."""
        a = self.assumptions
        annual = a.reinsurance.annual_premium(
            sum_assured=self.mp.sum_assured,
            office_premium=self.mp.annual_premium,
        )
        return self.pols_if(t) * a.per_period(annual) * self.in_term(t)

    @var(assumption="reinsurance")
    def reinsurance_commission(self, t):
        """Ceding commission received back on the premium ceded.

        Income, not outgo: it is how the cedant recovers the acquisition
        cost it has already paid on the ceded portion of the risk.
        """
        a = self.assumptions
        annual = a.reinsurance.annual_commission(
            sum_assured=self.mp.sum_assured,
            office_premium=self.mp.annual_premium,
        )
        return self.pols_if(t) * a.per_period(annual) * self.in_term(t)

    @var(assumption="commission")
    def commission_clawback(self, t):
        """Initial commission recovered from policies lapsing in period t.

        A negative cashflow to the insurer's outgo — recorded positive here
        and subtracted where it is used, so the sign is stated at the point
        of use rather than carried around.
        """
        a = self.assumptions
        recoverable = self.mp.annual_premium * a.commission.initial_percent
        return self.pols_lapse(t) * recoverable * a.commission.clawback_fraction(
            self.duration(t)
        )

    @var(assumption="interest")
    def v(self, t):
        """Discount factor from the start of period t back to time 0."""
        return self.assumptions.discount(t)

    # Present values are scalars over the whole projection, not @var series.
    # Start-of-year flows discount at v(t), end-of-year flows at v(t + 1);
    # both sum over periods t = 0 .. proj_len - 1.

    def pv_premiums(self):
        return sum(self.premiums(t) * self.v(t) for t in range(self.proj_len))

    def pv_claims(self):
        return sum(self.claims(t) * self.v(t + 1) for t in range(self.proj_len))

    def pv_expenses(self):
        """Maintenance expenses only — acquisition and claim costs are
        separate lines because they fall at different times and a pricing
        basis argues about them separately."""
        return sum(self.expenses(t) * self.v(t) for t in range(self.proj_len))

    def pv_initial_expenses(self):
        return sum(
            self.initial_expenses(t) * self.v(t) for t in range(self.proj_len)
        )

    def pv_claim_expenses(self):
        return sum(
            self.claim_expenses(t) * self.v(t + 1) for t in range(self.proj_len)
        )

    def pv_commission(self):
        """Commission paid, net of what early lapses give back."""
        return sum(
            (self.commission(t) - self.commission_clawback(t)) * self.v(t)
            for t in range(self.proj_len)
        )

    def pv_reinsurance(self):
        """Net cost of the treaty: premium ceded, less commission received
        and less what it recovers.

        One line rather than three, because a treaty is bought or not as a
        whole — but the three are available separately as variables for
        anyone who wants to see where the money went.
        """
        return sum(
            (self.reinsurance_premium(t) - self.reinsurance_commission(t))
            * self.v(t)
            - self.reinsurance_recovery(t) * self.v(t + 1)
            for t in range(self.proj_len)
        )

    def net_pv(self):
        """Premiums less every outgo: claims, all three expense lines,
        commission net of clawback, and reinsurance net of recoveries.

        With no expense basis, no commission and no treaty beyond the legacy
        per-policy expense, every added term is identically zero and this is
        the figure it always was.
        """
        return (
            self.pv_premiums()
            - self.pv_claims()
            - self.pv_expenses()
            - self.pv_initial_expenses()
            - self.pv_claim_expenses()
            - self.pv_commission()
            - self.pv_reinsurance()
        )
