"""Credit life — decreasing term assurance on somebody else's schedule.

PLAN.md §5.2's last unbuilt line, "**Group & credit life**". Credit life is
a term assurance whose sum assured is the borrower's outstanding loan
balance, so the shape of the cover is fixed by the amortisation schedule
rather than chosen by the actuary. See :mod:`engine.data.loan` for the
schedule and the refund bases.

Three things make it a different template rather than a `TermLife` with a
declining sum assured:

**The premium is single, and the reserve is the unearned part of it.** A
level-premium contract earns its premium as it goes; a single-premium one
collects everything at inception and owes the unused part back from the
first day. The liability is a *premium* measure, not a benefit measure, and
which of the two a regulator wants is the whole question.

**Lapse is settlement, and settlement is refunded.** On a protection
contract a lapse is money kept. Here it is the borrower repaying the loan
early, which extinguishes the cover, triggers a refund, and is the single
largest cashflow the template produces after the premium itself. So
``refunds`` is a first-class output rather than a footnote.

**The sum at risk is not a decision.** ``outstanding_balance`` is a closed
form in ``t`` — no schedule object, no lookup — because the batch executor
evaluates one expression across every model point at once and each of them
has its own principal, rate and term. It is a ``@var`` so that an
interest-only, balloon or straight-line loan is an **override** rather than
a rewrite of the template.

Model point fields: ``age_at_entry`` (int), ``loan_principal``,
``loan_rate`` (annual nominal, convertible at ``assumptions.freq``),
``loan_term_years`` (int), ``single_premium``, ``init_pols``.

Assumption bindings: ``mortality``, ``lapse`` (early settlement),
``interest``, ``expenses``, ``commission``.

The class attribute ``refund_basis`` is a **contract term**, not an
assumption — the policy document says how a refund is worked out, and two
otherwise identical books can be sold on different bases. It is typed the
way :class:`WithProfitsEndowment`'s bonus basis is typed, and for the same
reason.

Formulas are in indicator style throughout: conditions on model-point data
are multiplicative factors, so one instance evaluates the whole batch.
"""

from __future__ import annotations

import numpy as np

from engine.core.model import Model, var
from engine.data.loan import UNEARNED_BASES


class CreditLife(Model):
    """Single-premium decreasing term assurance over a repayment loan."""

    #: How an unused premium is given back on early settlement. A contract
    #: term. ``"rule_of_78"`` is the historical default in consumer credit
    #: and the one :mod:`engine.data.loan` shows short-changes the
    #: borrower everywhere except on an interest-free loan.
    refund_basis = "sum_at_risk"

    def __init_subclass__(cls, **kw):
        super().__init_subclass__(**kw)
        if cls.refund_basis not in UNEARNED_BASES:
            raise ValueError(
                f"refund basis must be one of {UNEARNED_BASES}, got "
                f"{cls.refund_basis!r}"
            )

    def _field(self, name, default):
        return getattr(self.mp, name, default) * 1.0

    # --- the loan --------------------------------------------------------

    def _term(self):
        """The loan's term in projection periods."""
        return self.assumptions.periods(self.mp.loan_term_years)

    def _periodic_rate(self):
        """The rate charged per period.

        Consumer credit is quoted as an annual **nominal** rate convertible
        at the payment frequency, so this is a division and not an
        effective-rate conversion. Converting would change the instalment,
        and the instalment is contractual.
        """
        return self._field("loan_rate", 0.0) / self.assumptions.freq

    def _annuity(self, periods):
        """``a_k|`` at the loan rate, with the zero-rate limit taken.

        A 0% loan is a real product and the naive expression is ``0/0`` on
        it. The guard is an indicator on model-point data, so it costs a
        `where` and no branch.
        """
        rate = self._periodic_rate()
        k = np.maximum(periods, 0.0)
        zero = rate == 0.0
        safe = np.where(zero, 1.0, rate)
        return np.where(zero, k, (1.0 - (1.0 + safe) ** (-k)) / safe)

    @var
    def outstanding_balance(self, t):
        """The debt at the start of period t, and so the cover in force.

        ``P · a_{n−t|} / a_{n|}`` — the level-instalment schedule in closed
        form. Zero once the loan has run, which is what makes it the sum
        assured of a term assurance without a separate ``in_term`` factor
        on the benefit.

        Override this for any other amortisation. Everything downstream
        reads the balance and nothing reads the schedule.
        """
        n = self._term()
        remaining = self._annuity(n - t)
        whole = self._annuity(n)
        return self.mp.loan_principal * (remaining / whole) * self.in_term(t)

    @var
    def in_term(self, t):
        """1 while the loan is running, 0 after."""
        return (t < self._term()) * 1.0

    def _unearned(self, t):
        """The unearned fraction at ``t``, as a plain method.

        Not a ``@var``, because the roll-forward below needs it one period
        ahead of wherever the projection has reached and the graph
        (rightly) refuses to evaluate a variable past the horizon. It has
        no dependencies to cache, so nothing is lost.
        """
        n = self._term()
        remaining = np.maximum(n - t, 0.0)
        if self.refund_basis == "pro_rata":
            return remaining / n
        if self.refund_basis == "rule_of_78":
            return remaining * (remaining + 1.0) / (n * (n + 1.0))
        # Sum at risk: the share of total outstanding balance still to run.
        # sum of a_{m|} for m = 1..K is (K − a_{K|}) / i, and K(K+1)/2 at
        # i = 0 — the same limit the annuity takes, one order further out.
        rate = self._periodic_rate()
        zero = rate == 0.0
        safe = np.where(zero, 1.0, rate)

        def exposure(k):
            return np.where(zero, k * (k + 1.0) / 2.0,
                            (k - self._annuity(k)) / safe)

        return exposure(remaining) / exposure(n * 1.0)

    @var
    def unearned_fraction(self, t):
        """Share of the single premium unearned at the start of period t.

        The three bases as closed forms in ``t``. They agree at ``t = 0``
        (nothing earned) and at ``t = n`` (nothing left), and disagree
        everywhere in between — by up to a quarter of the whole premium
        between the outer two.
        """
        return self._unearned(t)

    # --- decrements ------------------------------------------------------

    @var
    def age(self, t):
        return self.mp.age_at_entry + self.assumptions.years_elapsed(t)

    @var(assumption="mortality")
    def q_x(self, t):
        return self.assumptions.periodic_q(
            self.age(t), t, sex=getattr(self.mp, "sex", None)
        ) * self.in_term(t)

    @var(assumption="lapse")
    def settlement_rate(self, t):
        """Early settlement. A lapse by another name, and refunded.

        Bound to the ``lapse`` assumption because it is the same decrement
        mechanically, and named for what it is because the cashflow it
        causes runs the other way.
        """
        return self.assumptions.periodic_lapse() * self.in_term(t)

    def _split(self, t):
        return self.assumptions.decrements.split(
            self.pols_if(t),
            {"mortality": self.q_x(t), "lapse": self.settlement_rate(t)},
        )

    @var
    def pols_if(self, t):
        if t == 0:
            return self.mp.init_pols * 1.0
        return self._survivors(t - 1)

    def _survivors(self, t):
        """Policies carried from period t into period t+1.

        The body of ``pols_if``'s recursion, factored out so the reserve
        roll below can look one period ahead without the graph having to.
        """
        return self._split(t)[1] * (t + 1 <= self._term() - 1)

    @var
    def pols_death(self, t):
        return self._split(t)[0]["mortality"]

    @var
    def pols_settled(self, t):
        """Loans repaid early during period t."""
        return self._split(t)[0]["lapse"]

    # --- cashflows -------------------------------------------------------

    @var
    def claims(self, t):
        """Death claims arising in period t, paid at its end.

        The balance outstanding at the **start** of the period: a death
        part way through a period is covered for the debt before that
        period's instalment fell due, not after it.
        """
        return self.pols_death(t) * self.outstanding_balance(t)

    @var
    def premiums(self, t):
        """The single premium, collected once at inception."""
        return self.mp.init_pols * self.mp.single_premium * (t == 0)

    @var
    def refunds(self, t):
        """Given back to borrowers settling during period t.

        Refunded on the fraction unearned at the **end** of the period —
        the settlement has consumed that period's cover, so the borrower
        does not get it back.
        """
        return (self.pols_settled(t) * self.mp.single_premium
                * self._unearned(t + 1))

    @var
    def unearned_premium_reserve(self, t):
        """The liability at the start of period t on a premium basis.

        Not a benefit reserve. It answers "what would we owe if everyone
        settled today", which is what a consumer-credit regulator asks and
        is not the same question as "what do we expect to pay".
        """
        return (self.pols_if(t) * self.mp.single_premium
                * self.unearned_fraction(t))

    @var
    def closing_unearned_reserve(self, t):
        """The reserve carried into period t+1, computed forward.

        Equal to ``unearned_premium_reserve(t + 1)`` wherever both exist,
        which is asserted rather than assumed — it is the only thing
        holding the roll below together.
        """
        return (self._survivors(t) * self.mp.single_premium
                * self._unearned(t + 1))

    @var
    def earned_premium(self, t):
        """Premium recognised in period t: the fall in the reserve.

        Derived from the reserve's own roll rather than computed alongside
        it, so the two cannot disagree. Refunds leave with the reserve they
        released and do not appear here.

        **A death earns the rest of the premium.** The claim is paid in
        full and there is no refund on top, so the whole unearned balance
        on a dying life falls into this line at once. That is the reverse
        of a settlement, where the same balance walks out of the door, and
        it is why the two decrements cannot be netted.
        """
        return (self.unearned_premium_reserve(t)
                - self.closing_unearned_reserve(t)
                - self.refunds(t))

    # --- expenses and commission -----------------------------------------

    def _expense_bases(self):
        return {"premium": self.mp.single_premium,
                "sum_assured": self.mp.loan_principal}

    @var(assumption="expenses")
    def initial_expenses(self, t):
        amount = self.assumptions.expenses.initial.amount(**self._expense_bases())
        return self.mp.init_pols * amount * (t == 0)

    @var(assumption="expenses")
    def expenses(self, t):
        a = self.assumptions
        annual = a.expenses.renewal.amount(**self._expense_bases())
        return (self.pols_if(t)
                * (a.per_period(annual) * a.inflation_index(t))
                * self.in_term(t))

    @var(assumption="expenses")
    def claim_expenses(self, t):
        a = self.assumptions
        amount = a.expenses.claim.amount(**self._expense_bases())
        return self.pols_death(t) * amount * a.inflation_index(t + 1)

    @var(assumption="commission")
    def commission(self, t):
        """Paid once, on the single premium.

        Credit life commission is famously large and famously front-ended,
        which is exactly why the refund basis matters: the money has gone
        to the intermediary before the first refund falls due.
        """
        return self.premiums(t) * self.assumptions.commission.rate(0)

    # --- profit ----------------------------------------------------------

    @var
    def net_cashflow(self, t):
        """Everything in period t, without discounting."""
        return (self.premiums(t) - self.claims(t) - self.refunds(t)
                - self.initial_expenses(t) - self.expenses(t)
                - self.claim_expenses(t) - self.commission(t))

    @var
    def profit_before_tax(self, t):
        """The profit signature: start-of-period flows carried to its end.

        The same convention `TermLife` uses, so the two are comparable and
        a present value at ``v(t+1)`` equals discounting each cashflow at
        its own date.
        """
        a = self.assumptions
        accumulation = a.period_accumulation()
        start = (self.premiums(t) - self.initial_expenses(t)
                 - self.expenses(t) - self.commission(t))
        end = -self.claims(t) - self.claim_expenses(t) - self.refunds(t)
        return start * accumulation + end
