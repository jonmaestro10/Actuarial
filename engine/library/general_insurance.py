"""Premium liabilities — the unearned half of a general insurance book.

Execution plan §10, item C5, second half. `engine/report/incurred_claims.py`
measures claims that have already happened; this measures the ones that have
not. A policy is written, its premium is **earned** over the period it
covers, and what has not yet been earned is a liability — the unearned
premium reserve — against which the insurer still owes cover.

That pairing is the point: the two halves of a general insurer's balance
sheet are the liability for incurred claims and the liability for remaining
coverage, and this repo had only the first.

Executor class
--------------
Annual steps, scalar assumptions, a flat mortality-free model point — so
unlike :mod:`engine.library.pension_buyout` and
:mod:`engine.library.long_term_care`, this one is in §1.2's **per-policy
bitwise class** and runs under the interpreted executor as well as the
vectorized one. It needed nothing from the basis chassis, so it did not take
it.

The reserve is a stock and the premium is a flow
------------------------------------------------
The identity worth stating because it is what everything else is checked
against:

    UPR(t) = written premium − premium earned up to t

The unearned premium reserve is not projected on its own recursion; it is
what is left. A model that rolled the reserve forward *and* accumulated the
earnings would have two representations of one quantity, and they would
disagree in the last bits at best and by an earning pattern at worst.
:meth:`GeneralInsurance.unearned_premium` is therefore defined as the
residual and :meth:`~GeneralInsurance.premium_earned` as the flow, and the
test asserts they close to zero at the end of the term.

The catastrophe load is not part of the loss ratio
--------------------------------------------------
``expected_loss_ratio`` is the attritional cost — the claims that arrive
every year in roughly the same volume. ``cat_load`` is the expected cost of
events that do not: a windstorm year in ten, a flood in twenty.

Rolling the second into the first changes **nothing** about the expected
cashflow, which is exactly why it is tempting and why it is wrong. The two
have different *distributions* around the same mean, and every downstream
question that matters — the risk adjustment under IFRS 17, the capital, the
reinsurance attachment — is a question about the distribution rather than
the mean. Keeping them apart costs one field and preserves the only
information that distinguishes them. :attr:`GeneralInsurance.cat_load` is
reported separately for that reason, and
:meth:`~GeneralInsurance.claims` adds them.

Model point fields: ``written_premium``, ``policy_term_years`` (int),
``expected_loss_ratio``, ``init_pols``; optional ``cat_load_ratio``,
``expense_ratio``, ``written_in_period`` (int), ``earning_pattern``.
Assumption bindings: ``interest``.

Earning patterns
----------------
``"uniform"`` earns the premium evenly across the term, which is what a
policy covering a constant exposure does and what the 365ths method
approximates. ``"front"`` earns half the first period's share immediately —
the shape of a policy whose risk is concentrated at inception — and is
offered because the *pattern* is the assumption an actuary argues about,
so it belongs in the open rather than hard-coded.

An unrecognised pattern is refused rather than defaulting to uniform: a
typo that silently earns the premium a different way changes every reserve
in the projection and nothing in the output says so.
"""

from __future__ import annotations

import numpy as np

from engine.core.model import Model, var

#: How the written premium is earned across the term.
EARNING_PATTERNS = ("uniform", "front")


def earning_fractions(pattern: str, term: int) -> np.ndarray:
    """The share of the written premium earned in each period of the term.

    Sums to exactly 1 for every pattern, which is the property that makes
    the unearned premium reserve run off to zero rather than to a rounding.
    """
    if term < 1:
        raise ValueError(
            f"a policy term of {term} periods earns no premium; a policy "
            f"that covers nothing is not a policy"
        )
    if pattern == "uniform":
        shares = np.full(term, 1.0 / term, dtype=np.float64)
    elif pattern == "front":
        shares = np.full(term, 1.0, dtype=np.float64)
        shares[0] = 1.5
        shares = shares / shares.sum()
    else:
        raise ValueError(
            f"earning pattern {pattern!r} is not one of "
            f"{list(EARNING_PATTERNS)}. Defaulting to uniform would change "
            f"every reserve in the projection and say nothing in the output."
        )
    return shares


class GeneralInsurance(Model):
    def setup(self):
        term = int(np.max(np.atleast_1d(self.mp.policy_term_years)))
        patterns = np.atleast_1d(
            np.asarray(getattr(self.mp, "earning_pattern", "uniform"),
                       dtype=object))
        terms = np.atleast_1d(np.asarray(self.mp.policy_term_years, dtype=int))
        if terms.size == 1 and patterns.size > 1:
            terms = np.repeat(terms, patterns.size)
        if patterns.size == 1 and terms.size > 1:
            patterns = np.repeat(patterns, terms.size)

        n = self.proj_len + 1
        shares = np.zeros((terms.size, n), dtype=np.float64)
        for i, (pattern, own_term) in enumerate(zip(patterns, terms)):
            fractions = earning_fractions(str(pattern), int(own_term))
            width = min(fractions.size, n)
            shares[i, :width] = fractions[:width]
        self._shares = shares
        self._cumulative = np.cumsum(shares, axis=1)
        self.term = term

    @var
    def written_premium(self, t):
        """Premium written, all of it in period 0.

        A single cohort written at inception rather than a book renewing
        through the projection. The renewal book is the same template run
        once per underwriting period and added, which is what a caller with
        a real portfolio does.
        """
        return self.mp.written_premium * self.mp.init_pols * (t == 0)

    @var
    def earned_fraction(self, t):
        """The share of the written premium earned in period t."""
        return self.at(self._shares, t)

    @var
    def premium_earned(self, t):
        """Premium earned in period t — the flow."""
        return (self.mp.written_premium * self.mp.init_pols
                * self.earned_fraction(t))

    @var
    def unearned_premium(self, t):
        """The unearned premium reserve at the **end** of period t — the
        stock, defined as the residual.

        Written less earned to date. Not rolled forward on its own
        recursion, because two representations of one quantity disagree
        eventually and there is no reason to have two.
        """
        return (self.mp.written_premium * self.mp.init_pols
                * (1.0 - self.at(self._cumulative, t)))

    # --- what the premium is expected to cost -----------------------------

    @var
    def attritional_claims(self, t):
        """Expected attritional claims in period t.

        The loss ratio applied to premium *earned*, not premium written:
        a claim arises against cover provided, and cover is provided as the
        premium earns.
        """
        return self.premium_earned(t) * self.mp.expected_loss_ratio

    @var
    def cat_load(self, t):
        """Expected catastrophe cost in period t, reported separately.

        Same mean treatment as the attritional claims and a completely
        different distribution around it — which is the whole reason it is
        its own variable. See the module docstring.
        """
        return self.premium_earned(t) * getattr(self.mp, "cat_load_ratio",
                                                0.0)

    @var
    def claims(self, t):
        """Everything expected to be paid on the cover earned in period t."""
        return self.attritional_claims(t) + self.cat_load(t)

    @var
    def expenses(self, t):
        """Expenses, earned alongside the premium they are incurred against."""
        return self.premium_earned(t) * getattr(self.mp, "expense_ratio", 0.0)

    @var
    def underwriting_result(self, t):
        """Earned premium less claims and expenses in period t."""
        return self.premium_earned(t) - self.claims(t) - self.expenses(t)

    @var(assumption="interest")
    def v(self, t):
        """Discount factor from the start of period t back to time 0."""
        return self.assumptions.discount(t)

    def combined_ratio(self) -> float:
        """Claims plus expenses over earned premium, undiscounted.

        The number a general insurer is judged on, and a ratio rather than
        an amount because it is the one figure that compares across books of
        different size. Above 1 is an underwriting loss.
        """
        earned = sum(self.premium_earned(t) for t in range(self.proj_len + 1))
        cost = sum(self.claims(t) + self.expenses(t)
                   for t in range(self.proj_len + 1))
        return cost / earned

    def pv_underwriting_result(self):
        return sum(self.underwriting_result(t) * self.v(t)
                   for t in range(self.proj_len + 1))
