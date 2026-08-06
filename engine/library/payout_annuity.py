"""Payout annuity in payment — the first template on the VPLA basis.

Everything before this ran on annual steps and a flat integer-age mortality
table. This one runs on a :class:`~engine.core.timeaxis.TimeAxis` at any
payment frequency, with mortality from
:class:`~engine.data.mortality.MortalityBasis` (fractional-age splits,
improvement scales, limiting age) and discounting from a
:class:`~engine.data.rates.YieldCurve`. It is the benefit side of the VPLA
product, and the first template whose cashflows are held to the
SOA-validated basis rather than to a toy table.

Product mechanics — payments in advance, at the start of every period:

- the **primary annuity** pays ``annual_payment / freq`` per period while
  the annuitant lives;
- a **certain period** pays regardless of survival for its first
  ``certain_years`` years, exactly as VPLA does it, by overwriting survival
  with 1 over that stretch;
- a **reversionary benefit** continues ``joint_percent`` of the payment to
  the spouse after the annuitant dies, for as long as the spouse lives.

Model point fields: ``dob`` (date), ``sex``, ``valuation`` (date),
``annual_payment``, ``init_lives``; optional ``certain_years``,
``joint_percent``, ``spouse_dob``, ``spouse_sex``.
Assumption binding: a :class:`~engine.data.basis.ValuationBasis`.

The template runs under all three executors, and is in §1.2's **per-policy
bitwise class**: interpreted and vectorized agree to the last bit, over a
whole block, asserted in ``tests/test_spouse_binding.py``.

That is a correction rather than a change of design. This docstring used to
say the interpreted executor "is not supported here", and the evidence pack
disagreed — it placed the template in the class and tried anyway. The pack
was right and the sentence was wrong: nothing about a
:class:`~engine.data.basis.ValuationBasis` prevents a per-policy loop, and
what actually failed was ``setup()`` zipping ``spouse_dob`` and ``sex``
without a policy axis (RFC-070). Running a block one policy at a time is
still the loop the basis exists to *avoid* — it is slow, and the vectorized
executor is what you should use — but slow is not the same claim as
unsupported, and only one of the two can be asserted.

Correctness is anchored on Layer 0 rather than restated: the present value
of these cashflows must equal
:func:`engine.library.annuities.block_annuity_factors` times the payment,
term by term, and that factor is bitwise-parity with VPLA.
"""

from __future__ import annotations

import numpy as np

from engine.core.dates import DateArray
from engine.core.model import Model, var
from engine.core.timeaxis import TimeAxis
from engine.data.modelpoints import per_policy_field


class PayoutAnnuity(Model):
    def setup(self):
        basis = self.assumptions
        axis = TimeAxis(basis.freq, self.proj_len + 1, self.mp.valuation)
        self.axis = axis

        self._discount = basis.discount(axis)
        survival = basis.survival(axis, self.mp.dob, self.mp.sex)

        # Attained ages for the whole axis in one call, rather than one
        # DateArray per period.
        born = DateArray.coerce(self.mp.dob)
        self._age = axis.starts.whole_years_since(
            DateArray(born.year[:, None], born.month[:, None], born.day[:, None])
        )

        # The certain period pays whether or not the annuitant survives.
        # VPLA expresses that by overwriting survival with 1 over the
        # guarantee, which also makes the reversionary term fall away there
        # — nobody inherits a payment that is being made anyway.
        guarantee = np.atleast_1d(
            np.asarray(getattr(self.mp, "certain_years", 0.0), dtype=np.float64)
        )
        periods = np.arange(axis.n_periods)
        guaranteed = periods < np.round(guarantee * axis.freq)[:, None]
        self._survival = np.where(guaranteed, 1.0, survival)

        joint = np.atleast_1d(
            np.asarray(getattr(self.mp, "joint_percent", 0.0), dtype=np.float64)
        )
        # Held as a one-period slab so `at` shapes it like everything else.
        self._joint_percent = joint.reshape(-1, 1)
        if np.any(joint > 0.0):
            # A life with no survivor benefit stands in for its own spouse;
            # the zero percentage masks the term out either way, and it
            # keeps the batch rectangular without a sentinel date.
            spouse_dob = [
                spouse if percent > 0 else own
                for spouse, own, percent in zip(
                    per_policy_field(self.mp, "spouse_dob", dtype=object),
                    per_policy_field(self.mp, "dob", dtype=object),
                    joint,
                )
            ]
            spouse_sex = [
                spouse if percent > 0 else own
                for spouse, own, percent in zip(
                    per_policy_field(self.mp, "spouse_sex", dtype=object),
                    per_policy_field(self.mp, "sex", dtype=object),
                    joint,
                )
            ]
            self._spouse_survival = basis.survival(axis, spouse_dob, spouse_sex)
        else:
            self._spouse_survival = np.zeros_like(self._survival)

    # --- the annuitant ----------------------------------------------------

    @var
    def age(self, t):
        """Attained age at the start of period t — the same whole-year count
        the mortality basis uses to pick a table row."""
        return self.at(self._age, t)

    @var(assumption="mortality")
    def survival(self, t):
        """Probability of being in payment at the start of period t, with a
        certain period counting as certain."""
        return self.at(self._survival, t)

    @var
    def lives_if(self, t):
        """Annuitants in payment at the start of period t."""
        return self.mp.init_lives * self.survival(t)

    # --- the survivor -----------------------------------------------------

    @var(assumption="mortality")
    def spouse_survival(self, t):
        """Probability the spouse is alive at the start of period t."""
        return self.at(self._spouse_survival, t)

    @var
    def survivor_lives(self, t):
        """Expected reversionary benefits running at the start of period t,
        expressed as an equivalent number of full payments.

        A survivor benefit is in payment when the spouse is alive and the
        annuitant is not, which under independent lives is
        ``ₖp_y (1 - ₖp_x)`` — the closed form docs/vpla-review.md §3.3
        derives from VPLA's O(n²) accumulation.
        """
        return (
            self.mp.init_lives
            * self.at(self._joint_percent, 0)
            * self.spouse_survival(t)
            * (1.0 - self.survival(t))
        )

    # --- cashflow ---------------------------------------------------------

    @var
    def payments(self, t):
        """Total paid at the start of period t."""
        return (
            (self.lives_if(t) + self.survivor_lives(t))
            * self.mp.annual_payment
            / self.axis.freq
        )

    @var(assumption="interest")
    def v(self, t):
        """Discount factor from the start of period t back to the valuation."""
        return self._discount[t]

    def pv_payments(self):
        """Present value of the whole payment stream, in advance."""
        return sum(self.payments(t) * self.v(t) for t in range(self.proj_len + 1))

    def annuity_factor(self):
        """Present value per unit of annual payment per life — the annuity
        factor this template's cashflows imply."""
        return self.pv_payments() / (self.mp.annual_payment * self.mp.init_lives)
