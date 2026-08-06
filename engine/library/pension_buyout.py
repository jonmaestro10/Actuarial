"""Bulk annuity — a pension scheme's liabilities, priced as one policy.

Execution plan §10, item C3. A buy-in or buy-out transfers a scheme's
pensioner and deferred members to an insurer for a single premium, and the
premium *is* the present value of the benefits this template projects. It
runs on the :class:`~engine.data.basis.ValuationBasis` chassis
:mod:`engine.library.payout_annuity` established, because the members whose
value dominates a scheme are the deferreds — people twenty years from
retirement, whose value moves on the improvement scale and the fractional-age
split that the annual-step templates do not carry.

What this adds over a payout annuity in payment
-----------------------------------------------
- **Deferment.** A member below their retirement date is not being paid yet,
  and their pension is **revalued** in the meantime. Two different rates
  therefore act on one pension: revaluation in deferment and escalation in
  payment.
- **Increases fall on anniversaries.** ``(1 + rate) ** years`` with a
  fractional exponent would give a member three days past their anniversary
  three days' worth of an increase, and no scheme pays that way. Both rates
  compound over **completed years**, so a monthly axis holds the pension flat
  between anniversaries, which is what the payslip does.
- **The spouse's pension runs from the member's death, not from
  retirement.** A deferred member who dies at 50 leaves a spouse's pension
  payable immediately, so the reversionary term is not gated by the
  deferment. Its amount tracks the same revaluation-then-escalation path the
  member's own pension would have followed.

Model point fields: ``dob`` (date), ``sex``, ``valuation`` (date),
``annual_pension``, ``init_lives``; optional ``deferred_years``,
``revaluation_rate``, ``escalation_rate``, ``spouse_percent``,
``spouse_dob``, ``spouse_sex``, ``contract``.
Assumption binding: a :class:`~engine.data.basis.ValuationBasis`.

Buy-in and buy-out are the same projection
------------------------------------------
``contract`` records which one this is and **changes no number here**, which
is worth saying plainly rather than leaving someone to discover by diffing
two runs. A buy-in is an asset of the scheme and a buy-out discharges the
scheme's obligation to the member; the benefit cashflows the insurer prices
are identical, and what differs is whose balance sheet holds the policy and
which residual risks stay behind — data, expenses, and the covenant. Those
are not projection terms, and a template that quietly moved a number when
the flag changed would be asserting an actuarial difference that does not
exist.

Executor class
--------------
In §1.2's **per-policy bitwise class**, like every template on this chassis:
interpreted and vectorized agree bitwise over a whole block, asserted in
``tests/test_spouse_binding.py``.

RFC-041 wrote this section the other way round — "vectorized and stochastic
only", offered as a stated class rather than an omission. It was neither. The
per-policy loop is the thing the basis exists to *avoid* for speed, and
RFC-041 read that as a statement about correctness; what actually failed was
``setup()`` zipping ``spouse_dob`` and ``sex`` without a policy axis, and
only for a member carrying a survivor pension (RFC-070). The exclusion was a
bug wearing a design decision's clothes, which is worth recording: an
executor class asserted by a docstring and by nothing else is not asserted.

It carries an ``EXAMPLES`` entry as of RFC-066, which taught the request
schema a ``ValuationBasis`` — so the evidence pack's specimen set reaches
here, and it is the pack that caught the above.

Correctness is anchored on Layer 0 rather than restated. A pensioner with
level increases must reproduce
:func:`engine.library.annuities.reversionary_annuity_factor` exactly, and a
deferred member must reproduce
:func:`engine.library.annuities.deferred_annuity_values` at the retirement
period — the same closed forms that are bitwise-parity with VPLA.
"""

from __future__ import annotations

import numpy as np

from engine.core.model import Model, var
from engine.core.timeaxis import TimeAxis
from engine.data.modelpoints import per_policy_field

#: What ``contract`` may say. Both project the same benefits; see the module
#: docstring for why that is a finding rather than an omission.
CONTRACTS = ("buy_in", "buy_out")


def _field(mp, name, default):
    """Numeric model-point field with a policy axis. See
    :func:`engine.data.modelpoints.per_policy_field`, which this is now a
    name for: the object-valued variant lives beside it, and keeping the two
    apart in three separate modules is how RFC-070's bug survived."""
    return per_policy_field(mp, name, default)


def increase_factors(freq: int, n_periods: int, deferred_years,
                     revaluation_rate, escalation_rate) -> np.ndarray:
    """The pension in force at each period, per unit of pension today.

    ``(n_lives, n_periods)``. Revaluation compounds over completed years in
    deferment and escalation over completed years in payment, so a member
    ``d`` years from retirement holds
    ``(1 + rev) ** floor(d)`` at retirement and grows from there.

    Both counts are of **completed** years. An increase awarded on an
    anniversary is worth nothing the day before it and its full value the day
    after, and interpolating between the two would pay a member for part of a
    rise nobody has granted.
    """
    years = np.arange(n_periods, dtype=np.float64) / freq
    deferred = np.asarray(deferred_years, dtype=np.float64)[:, None]
    revalued = np.floor(np.minimum(years[None, :], deferred))
    escalated = np.floor(np.maximum(years[None, :] - deferred, 0.0))
    rev = np.asarray(revaluation_rate, dtype=np.float64)[:, None]
    esc = np.asarray(escalation_rate, dtype=np.float64)[:, None]
    return (1.0 + rev) ** revalued * (1.0 + esc) ** escalated


class PensionBuyout(Model):
    def setup(self):
        basis = self.assumptions
        axis = TimeAxis(basis.freq, self.proj_len + 1, self.mp.valuation)
        self.axis = axis

        self._discount = basis.discount(axis)
        self._survival = basis.survival(axis, self.mp.dob, self.mp.sex)

        contract = np.atleast_1d(
            np.asarray(getattr(self.mp, "contract", "buy_out"), dtype=object)
        )
        unknown = sorted({str(c) for c in contract} - set(CONTRACTS))
        if unknown:
            raise ValueError(
                f"contract {unknown} is not one of {list(CONTRACTS)}. Both "
                f"project the same benefits, so this is a label on the "
                f"transaction rather than a switch — but an unrecognised one "
                f"means the caller believes something about the run that is "
                f"not true."
            )

        deferred = _field(self.mp, "deferred_years", 0.0)
        if np.any(deferred < 0.0):
            raise ValueError(
                "deferred_years is negative: a member already in payment has "
                "zero deferment, not a past one. Their pension's history is "
                "in annual_pension."
            )
        self._deferred = deferred
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
        if np.any(spouse > 0.0):
            # A member with no survivor benefit stands in for their own
            # spouse: the zero percentage masks the term out, and it keeps
            # the batch rectangular without a sentinel date. The same trick
            # PayoutAnnuity uses, for the same reason.
            spouse_dob = [s if pct > 0 else own for s, own, pct
                          in zip(per_policy_field(self.mp, "spouse_dob",
                                                  dtype=object),
                                 per_policy_field(self.mp, "dob",
                                                  dtype=object),
                                 spouse)]
            spouse_sex = [s if pct > 0 else own for s, own, pct
                          in zip(per_policy_field(self.mp, "spouse_sex",
                                                  dtype=object),
                                 per_policy_field(self.mp, "sex",
                                                  dtype=object),
                                 spouse)]
            self._spouse_survival = basis.survival(axis, spouse_dob,
                                                   spouse_sex)
        else:
            self._spouse_survival = np.zeros_like(self._survival)

    # --- the member -------------------------------------------------------

    @var
    def pension_amount(self, t):
        """The member's pension per annum at period t, had they lived.

        Defined in deferment as well as in payment, because that is the
        amount the spouse's pension is a percentage of — a deferred member's
        death does not wait for their retirement date.
        """
        return self.mp.annual_pension * self.at(self._increase, t)

    @var(assumption="mortality")
    def survival(self, t):
        """Probability the member is alive at the start of period t."""
        return self.at(self._survival, t)

    @var
    def in_payment(self, t):
        """1 where the member's own pension is being paid, 0 in deferment."""
        return self.at(self._in_payment, t)

    @var
    def lives_if(self, t):
        """Members alive at the start of period t, whether or not in
        payment — a deferred member is in force and costing nothing yet."""
        return self.mp.init_lives * self.survival(t)

    @var
    def member_payments(self, t):
        """Paid to members at the start of period t."""
        return (self.lives_if(t) * self.in_payment(t)
                * self.pension_amount(t) / self.axis.freq)

    # --- the survivor -----------------------------------------------------

    @var(assumption="mortality")
    def spouse_survival(self, t):
        """Probability the spouse is alive at the start of period t."""
        return self.at(self._spouse_survival, t)

    @var
    def spouse_payments(self, t):
        """Reversionary pensions running at the start of period t.

        Payable when the spouse is alive and the member is not, which under
        independent lives is ``ₖp_y (1 − ₖp_x)`` — the closed form
        docs/vpla-review.md §3.3 derives. **Not gated by deferment**: a
        member dying before retirement leaves a spouse's pension in payment
        straight away, at the percentage applied to the pension the member
        had earned and had revalued to that date.
        """
        return (self.mp.init_lives * self.at(self._spouse_percent, 0)
                * self.spouse_survival(t) * (1.0 - self.survival(t))
                * self.pension_amount(t) / self.axis.freq)

    # --- cashflow ---------------------------------------------------------

    @var
    def payments(self, t):
        """Everything the insurer pays at the start of period t."""
        return self.member_payments(t) + self.spouse_payments(t)

    @var(assumption="interest")
    def v(self, t):
        """Discount factor from the start of period t back to the valuation."""
        return self._discount[t]

    def premium(self):
        """The single premium: the present value of the whole benefit
        stream, in advance.

        The same number for a buy-in and a buy-out — see the module
        docstring. Loadings, expenses and the insurer's margin are priced on
        top of this and are not part of it.
        """
        return sum(self.payments(t) * self.v(t)
                   for t in range(self.proj_len + 1))
