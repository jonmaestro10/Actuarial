"""Loan amortisation, and the three ways a single premium gets refunded.

Credit life is the one product in this library whose sum assured is written
down by somebody else's schedule. The benefit is the borrower's outstanding
balance, so the shape of the cover is the shape of the loan, and the loan
is not actuarial machinery at all — it is a fixed schedule that has to be
reproduced exactly rather than approximated.

The premium is usually **single**, charged at inception and financed within
the loan itself. That makes the interesting question not what the cover
costs but what happens when the borrower settles early: how much of a
premium paid for five years of cover is given back after two.

Three bases, and they are not close to each other
-------------------------------------------------
``"pro_rata"``
    ``(n − k) / n``. Unearned per unit of **time**.

``"rule_of_78"``
    ``(n−k)(n−k+1) / (n(n+1))``. The sum-of-digits rule, named for the 78
    month-digits in a one-year loan. Unearned per unit of **remaining
    instalment count**.

``"sum_at_risk"``
    The share of the total outstanding balance that is still to run. This
    is what the cover is actually for.

Their order is fixed and provable: **rule of 78 ≤ sum at risk ≤ pro rata**,
for any declining balance. Pro rata over-refunds because a decreasing term
assurance is not half used up at the half-way point of its term — most of
the risk was in the first half.

The finding the rule was built for, and lost
--------------------------------------------
At a **zero** interest rate the outstanding balance declines linearly, and
then

    sum-at-risk unearned  =  K(K+1) / (n(n+1))  =  rule of 78

*algebraically*, and not approximately — the two are the same expression.
As arithmetic they round differently and agree to **one ulp**: 1.1e-16 at
every duration of a sixty-period loan, and bit for bit on short ones. Both
endpoints are exact by construction, 1.0 and 0.0, because the run-off is
accumulated from the far end rather than subtracted from a total.

The Rule of 78 is the correct sum-at-risk refund for a flat, interest-free
loan — which is the product it was written for — and it stops being correct
the moment the balance stops amortising in a straight line.

At any positive rate the balance runs off more slowly than a straight line,
so more risk is left than the rule admits, and the borrower is short-changed
by the gap. It widens with the rate: on a five-year monthly loan the
maximum shortfall is 2.82% of the whole premium at 12% nominal and 5.31% at
24%.

Against pro rata the gap is much larger — a maximum of exactly
``n / (4(n+1))`` of the premium, at the mid-point of the term, approaching
a quarter of everything paid — but that comparison flatters the borrower,
because pro rata is not the right answer either.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from engine.core.dates import months_per_period

#: How the principal comes down.
AMORTISATION_METHODS = ("level_instalment", "interest_only", "straight_line")

#: How an unused single premium is given back.
UNEARNED_BASES = ("sum_at_risk", "pro_rata", "rule_of_78")


def annuity_certain(periods, rate: float) -> np.ndarray:
    """``a_k|`` at ``rate`` per period, with the zero-rate limit ``k``.

    The limit is taken rather than approached: a 0% loan is a real product
    — interest-free credit, dealer finance — and the naive expression is
    ``0/0`` on it.
    """
    k = np.asarray(periods, dtype=np.float64)
    if np.any(k < 0.0):
        raise ValueError("a term cannot be negative")
    if rate == 0.0:
        return k
    return (1.0 - (1.0 + rate) ** (-k)) / rate


@dataclass(frozen=True)
class Loan:
    """A repayment loan, and the cover a credit life policy writes over it.

    ``rate`` is the annual **nominal** rate charged, convertible ``freq``
    times a year, because that is how consumer credit is quoted — the
    period rate is ``rate / freq`` and no effective-rate conversion is
    applied anywhere. ``term`` is in periods.
    """

    principal: float
    rate: float
    term: int
    freq: int = 12
    method: str = "level_instalment"

    def __post_init__(self):
        months_per_period(self.freq)
        if self.principal <= 0.0:
            raise ValueError(f"principal {self.principal} must be positive")
        if self.term < 1:
            raise ValueError(f"term {self.term} must be at least one period")
        if self.rate < 0.0:
            raise ValueError(f"rate {self.rate} is negative")
        if self.method not in AMORTISATION_METHODS:
            raise ValueError(
                f"amortisation method must be one of {AMORTISATION_METHODS}, "
                f"got {self.method!r}"
            )

    @property
    def periodic_rate(self) -> float:
        return self.rate / self.freq

    @property
    def instalment(self) -> float:
        """The level payment, at the end of each period."""
        i = self.periodic_rate
        if self.method == "interest_only":
            return float(self.principal * i)
        if self.method == "straight_line":
            raise ValueError(
                "a straight-line loan has no level instalment; its payments "
                "fall as the balance does"
            )
        return float(self.principal / annuity_certain(self.term, i))

    def balances(self) -> np.ndarray:
        """Outstanding balance at the **start** of each period.

        ``term + 1`` long: ``balances[0]`` is the principal and
        ``balances[term]`` is zero, both exactly, so a schedule that does
        not close is a failure rather than a rounding.
        """
        n, i = self.term, self.periodic_rate
        remaining = np.arange(n, -1, -1, dtype=np.float64)
        if self.method == "straight_line":
            return self.principal * remaining / n
        if self.method == "interest_only":
            balance = np.full(n + 1, float(self.principal))
            balance[n] = 0.0
            return balance
        return self.instalment * annuity_certain(remaining, i)

    def sum_at_risk(self) -> np.ndarray:
        """The cover in force during each period, ``term`` long.

        The balance outstanding at the **start** of the period, so a death
        during a period is covered for the debt before that period's
        instalment falls due. The alternative — covering the balance after
        it — pays off a debt the estate has already serviced.
        """
        return self.balances()[:-1]

    def interest(self) -> np.ndarray:
        return self.sum_at_risk() * self.periodic_rate

    def principal_repaid(self) -> np.ndarray:
        return -np.diff(self.balances())

    def unearned(self, basis: str = "sum_at_risk") -> np.ndarray:
        """Fraction of a single premium unearned at each duration.

        ``term + 1`` long: 1.0 at inception, 0.0 once the loan has run.
        """
        if basis not in UNEARNED_BASES:
            raise ValueError(
                f"unearned basis must be one of {UNEARNED_BASES}, "
                f"got {basis!r}"
            )
        if basis == "pro_rata":
            return pro_rata_unearned(self.term)
        if basis == "rule_of_78":
            return rule_of_78_unearned(self.term)
        return exposure_unearned(self.sum_at_risk())

    def refund(self, premium: float, duration: int,
               basis: str = "sum_at_risk") -> float:
        """What settling at ``duration`` completed periods gives back."""
        if not 0 <= duration <= self.term:
            raise ValueError(
                f"duration {duration} is outside the loan's 0..{self.term}"
            )
        return float(premium * self.unearned(basis)[duration])

    def __fingerprint__(self):
        return {"principal": self.principal, "rate": self.rate,
                "term": self.term, "freq": self.freq, "method": self.method}


def pro_rata_unearned(n: int) -> np.ndarray:
    """``(n − k) / n`` — unearned per unit of time."""
    if n < 1:
        raise ValueError(f"a term of {n} periods has nothing to earn")
    return (n - np.arange(n + 1, dtype=np.float64)) / n


def rule_of_78_unearned(n: int) -> np.ndarray:
    """``(n−k)(n−k+1) / (n(n+1))`` — the sum-of-digits rule.

    Exactly the sum-at-risk answer on a zero-interest loan, and only there.
    """
    if n < 1:
        raise ValueError(f"a term of {n} periods has nothing to earn")
    remaining = n - np.arange(n + 1, dtype=np.float64)
    return remaining * (remaining + 1.0) / (n * (n + 1.0))


def exposure_unearned(exposure) -> np.ndarray:
    """The share of a total exposure still to run at each duration.

    Generic on purpose: the exposure is the sum at risk here, but a fully
    actuarial refund would weight it by survival and discount it, and that
    is the same reduction over a different vector.
    """
    values = np.asarray(exposure, dtype=np.float64).ravel()
    if values.size == 0:
        raise ValueError("an empty exposure has nothing to earn")
    if np.any(values < 0.0):
        raise ValueError("a negative exposure is not an amount at risk")
    if values.sum() <= 0.0:
        raise ValueError("an exposure of nothing has no unearned share")
    # Accumulated from the far end rather than subtracted from a total, so
    # the series reaches **exactly** zero when the exposure has run. The
    # subtractive form leaves a residual of the accumulated rounding at the
    # one duration where the answer is known in advance.
    remaining = np.concatenate([np.cumsum(values[::-1])[::-1], [0.0]])
    return remaining / remaining[0]


def rule_of_78_shortfall(loan: Loan) -> np.ndarray:
    """How much of the premium the Rule of 78 keeps that it should not.

    ``sum at risk − rule of 78``, as a fraction of the whole premium, at
    each duration. Zero to one ulp on a 0% loan, and positive everywhere
    inside the term on any other.
    """
    return loan.unearned("sum_at_risk") - loan.unearned("rule_of_78")


def worst_shortfall(loan: Loan) -> tuple:
    """``(shortfall, duration)`` at its worst — measured, not assumed.

    Where the maximum falls is a property of the loan; on a five-year
    monthly loan at 12% it is period 21, not the mid-point, because the
    two curves are not symmetric about it.
    """
    gap = rule_of_78_shortfall(loan)
    where = int(np.argmax(gap))
    return float(gap[where]), where


def pro_rata_excess(n: int) -> np.ndarray:
    """``pro rata − rule of 78``. Its maximum is exactly ``n/(4(n+1))``.

    The comparison usually quoted against the Rule of 78, and the one that
    overstates the case: pro rata is not the right answer either, because a
    decreasing term assurance is not half used up half way through.
    """
    return pro_rata_unearned(n) - rule_of_78_unearned(n)
