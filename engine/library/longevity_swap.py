"""Longevity swap — the hedge whose settlement is one number for a book.

Execution plan §10, item C3. A scheme pays a fixed leg agreed at inception
and receives a floating leg equal to the benefits it actually has to pay, so
the swap is worth something exactly when the members live longer than the
contract assumed. This template is the indemnity swap — the floating leg is
the scheme's *own* experience — which is what a pension scheme buys and
which is why it is a **pooled** model.

Why ``@pool`` rather than a per-member cashflow
-----------------------------------------------
The settlement is one payment on one contract covering the whole membership.
A per-member figure is not a smaller version of it; it is a share of it, and
a share is only meaningful once the total exists. Computing member-level
amounts and summing them outside the model would put the reduction somewhere
nothing enforces — which is the shape of error RFC-061 found in
``GroupLife``, where a pooled quantity evaluated one policy at a time
produced a complete set of numbers computed against a scheme of one, with
nothing in the output to say so.

So :meth:`LongevitySwap.net_settlement` is a ``@pool``, and the consequences
are the ones RFC-061 established: the vectorized executor stops chunking the
block, and :func:`engine.core.runner.run` **refuses** a block of more than
one with ``PooledBlockError`` rather than silently hedging a pool of one.
That places this template in RFC-061's block equivalence class — not the
per-policy bitwise class of §1.2, and not by chassis but by what the product
is. An index-based swap settling per life against an external published
survival index would not be pooled; that is a different contract and would
be a different template.

The two bases
-------------
:class:`LongevitySwapBasis` carries **two** valuation bases. The projection
basis is the scheme's best estimate of its own experience and drives the
floating leg; the fixed basis is the survival schedule written into the
contract and drives the fixed leg. A swap struck exactly on the projection
basis settles at zero in every period, which is the identity the tests use
as their zero point, and a real swap's fixed basis carries the counterparty's
margin — lighter mortality, a longer schedule, a fixed leg that costs more
than the expected benefits.

Both legs value the **same** hedged cashflow: the member's pension while
they live and the reversionary pension after, on the mechanics
:mod:`engine.library.pension_buyout` uses, so the floating leg of a swap
over a book equals that template's total payments over the same book. Only
the survival curves differ between the legs, which is the whole content of a
longevity hedge.

Model point fields: ``dob`` (date), ``sex``, ``valuation`` (date),
``annual_pension``, ``init_lives``; optional ``deferred_years``,
``revaluation_rate``, ``escalation_rate``, ``spouse_percent``,
``spouse_dob``, ``spouse_sex``.
Assumption binding: a :class:`LongevitySwapBasis`.

Sign convention: **positive is receivable by the hedger.** A net settlement
above zero means the members outlived the fixed schedule and the swap paid,
which is the direction the scheme bought it for.
"""

from __future__ import annotations

import numpy as np

from engine.core.model import Model, pool, var
from engine.core.timeaxis import TimeAxis
from engine.library.pension_buyout import increase_factors


class LongevitySwapBasis:
    """The projection basis and the contractual fixed basis, together.

    Two :class:`~engine.data.basis.ValuationBasis` objects rather than one
    basis and a mortality loading, because the fixed leg of a real swap is
    negotiated as a survival schedule — a table, an improvement scale and a
    term — and not as a scalar applied to somebody else's assumption.

    Discounting comes from the projection basis. Both legs are cashflows of
    one contract and discounting them on different curves would make the
    swap's value depend on which leg was being looked at.
    """

    def __init__(self, *, projection, fixed):
        if projection.freq != fixed.freq:
            raise ValueError(
                f"the two legs run at different frequencies "
                f"({projection.freq} and {fixed.freq}); they settle against "
                f"each other period by period, so they must share an axis"
            )
        self.projection = projection
        self.fixed = fixed

    @property
    def freq(self) -> int:
        return self.projection.freq

    def __fingerprint__(self):
        return {"projection": self.projection, "fixed": self.fixed}


def _field(mp, name, default):
    return np.atleast_1d(
        np.asarray(getattr(mp, name, default), dtype=np.float64)
    )


class LongevitySwap(Model):
    def setup(self):
        terms = self.assumptions
        axis = TimeAxis(terms.freq, self.proj_len + 1, self.mp.valuation)
        self.axis = axis

        self._discount = terms.projection.discount(axis)
        deferred = _field(self.mp, "deferred_years", 0.0)
        self._increase = increase_factors(
            axis.freq, axis.n_periods, deferred,
            _field(self.mp, "revaluation_rate", 0.0),
            _field(self.mp, "escalation_rate", 0.0),
        )
        periods = np.arange(axis.n_periods)
        self._in_payment = (periods[None, :] >= np.round(
            deferred * axis.freq)[:, None]).astype(np.float64)

        spouse = _field(self.mp, "spouse_percent", 0.0)
        self._spouse_percent = spouse.reshape(-1, 1)
        spouse_dob = spouse_sex = None
        if np.any(spouse > 0.0):
            spouse_dob = [s if pct > 0 else own for s, own, pct
                          in zip(self.mp.spouse_dob, self.mp.dob, spouse)]
            spouse_sex = [s if pct > 0 else own for s, own, pct
                          in zip(self.mp.spouse_sex, self.mp.sex, spouse)]

        self._survival, self._spouse_survival = self._curves(
            terms.projection, axis, spouse_dob, spouse_sex, spouse)
        self._fixed_survival, self._fixed_spouse_survival = self._curves(
            terms.fixed, axis, spouse_dob, spouse_sex, spouse)

    def _curves(self, basis, axis, spouse_dob, spouse_sex, spouse_percent):
        """Member and spouse survival on one of the two bases."""
        member = basis.survival(axis, self.mp.dob, self.mp.sex)
        if spouse_dob is None:
            return member, np.zeros_like(member)
        return member, basis.survival(axis, spouse_dob, spouse_sex)

    # --- what is hedged ---------------------------------------------------

    @var
    def pension_amount(self, t):
        """The member's pension per annum at period t, had they lived."""
        return self.mp.annual_pension * self.at(self._increase, t)

    @var
    def in_payment(self, t):
        """1 where the member's own pension is being paid, 0 in deferment."""
        return self.at(self._in_payment, t)

    def _leg(self, t, member_survival, spouse_survival):
        """One member's hedged cashflow at period t on one survival basis.

        Member's own pension while alive and in payment, plus the
        reversionary pension where the spouse is alive and the member is
        not — the same benefit :class:`engine.library.pension_buyout.
        PensionBuyout` projects, which is what makes the two comparable.
        """
        amount = self.pension_amount(t) / self.axis.freq
        alive = self.at(member_survival, t)
        widowed = self.at(spouse_survival, t) * (1.0 - alive)
        return self.mp.init_lives * amount * (
            alive * self.in_payment(t)
            + self.at(self._spouse_percent, 0) * widowed
        )

    @var(assumption="mortality")
    def expected_payment(self, t):
        """One member's hedged cashflow on the **projection** basis."""
        return self._leg(t, self._survival, self._spouse_survival)

    @var(assumption="mortality")
    def contracted_payment(self, t):
        """One member's hedged cashflow on the **fixed** basis.

        Not a cashflow anybody pays — the fixed leg settles at book level —
        but the per-member term the fixed leg reduces over, kept as a
        variable so the two legs are visibly the same formula on two
        survival curves.
        """
        return self._leg(t, self._fixed_survival, self._fixed_spouse_survival)

    # --- the contract -----------------------------------------------------

    @pool
    def floating_leg(self, t):
        """Received by the hedger at period t: the book's actual benefits."""
        return self.pool_sum(self.expected_payment(t))

    @pool
    def fixed_leg(self, t):
        """Paid by the hedger at period t, on the schedule agreed at
        inception."""
        return self.pool_sum(self.contracted_payment(t))

    @pool
    def net_settlement(self, t):
        """Floating less fixed: **positive is receivable by the hedger**.

        Zero in every period where the fixed basis is the projection basis,
        which is the swap struck at expectation with no margin. Positive
        where the members outlive the contractual schedule, which is what
        the hedge is for.
        """
        return self.floating_leg(t) - self.fixed_leg(t)

    @var(assumption="interest")
    def v(self, t):
        """Discount factor from the start of period t back to the valuation."""
        return self._discount[t]

    def value(self):
        """Present value of the net settlements — the swap's mark at outset.

        Positive means the swap is an asset of the hedger: the benefits
        expected on the projection basis exceed the fixed leg they have
        contracted to pay. A swap carrying the counterparty's margin values
        **negative** at inception, which is the price of the hedge and not a
        mispricing.
        """
        return sum(self.net_settlement(t) * self.v(t)
                   for t in range(self.proj_len + 1))
