"""Expenses, expense inflation, and commission.

PLAN.md §5.1 lists these among the Layer 0 primitives, and until now the
engine had one of them: a flat annual amount per policy, never indexed.
That is enough for a closed-form golden test and not enough to price
anything, because a real expense basis is stated on three bases at once and
distinguishes what it costs to *write* a policy from what it costs to keep
one.

Three bases, one object
-----------------------
An expense loading is quoted as some of each:

- **per policy** — a flat amount, the cost of an administration record;
- **percent of premium** — commission-like costs that scale with the money
  coming in;
- **per mille sum assured** — underwriting and medical costs, which scale
  with the risk written rather than with the premium.

:class:`ExpenseScale` is one such loading. :class:`Expenses` carries three
of them — ``initial`` at inception, ``renewal`` every period thereafter,
``claim`` on each claim settled — plus the inflation rate that indexes the
recurring ones.

Everything is quoted **annually**, and the template divides once. A renewal
loading of £60 a year is £5 a month, and a 4% -of-premium loading applies to
whatever premium the period actually collected; stating both annually and
converting in one place is what keeps ``freq = 1`` an exact identity.

Commission
----------
:class:`Commission` is separate rather than a fourth ``ExpenseScale``,
because it is not an expense loading with a different number in it: it has
its own clock (a higher rate for the first years, a lower one after) and it
can be **clawed back** when a policy lapses early, which nothing else here
does. Clawback is off by default, so an assumption set that does not ask
for it cannot be changed by it.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class ExpenseScale:
    """One expense loading, quoted annually on three bases at once.

    ``per_mille_sum_assured`` is per **thousand** of sum assured, which is
    how underwriting costs are quoted; the division by 1,000 lives here so
    that no template has to remember it.
    """

    per_policy: float = 0.0
    percent_premium: float = 0.0
    per_mille_sum_assured: float = 0.0

    def __post_init__(self):
        for name in ("per_policy", "percent_premium", "per_mille_sum_assured"):
            if getattr(self, name) < 0.0:
                raise ValueError(f"{name} {getattr(self, name)} is negative")

    def amount(self, *, premium=0.0, sum_assured=0.0):
        """The annual loading for one policy."""
        return (
            self.per_policy
            + self.percent_premium * premium
            + self.per_mille_sum_assured * sum_assured / 1000.0
        )

    def __bool__(self) -> bool:
        return bool(
            self.per_policy or self.percent_premium
            or self.per_mille_sum_assured
        )

    def __fingerprint__(self):
        return {"per_policy": self.per_policy,
                "percent_premium": self.percent_premium,
                "per_mille_sum_assured": self.per_mille_sum_assured}


NIL = ExpenseScale()


class Expenses:
    """An expense basis: what it costs to write, keep and settle a policy.

    ``inflation`` indexes the **recurring** loadings — renewal and claim —
    from projection time zero. Initial expenses are not indexed: they are
    incurred at inception, which *is* time zero, so there is nothing to
    index them over.
    """

    def __init__(self, *, initial: ExpenseScale = NIL,
                 renewal: ExpenseScale = NIL, claim: ExpenseScale = NIL,
                 inflation: float = 0.0):
        if inflation <= -1.0:
            raise ValueError(f"expense inflation {inflation} is at or below -100%")
        self.initial = initial
        self.renewal = renewal
        self.claim = claim
        self.inflation = inflation

    def __repr__(self) -> str:
        return (f"Expenses(initial={self.initial}, renewal={self.renewal}, "
                f"claim={self.claim}, inflation={self.inflation})")

    def __bool__(self) -> bool:
        return bool(self.initial or self.renewal or self.claim)

    def __fingerprint__(self):
        return {"initial": self.initial, "renewal": self.renewal,
                "claim": self.claim, "inflation": self.inflation}

    def index(self, years):
        """Inflation factor after ``years`` — a float, not whole years, so a
        monthly projection indexes monthly.

        ``(1 + inflation) ** years``. At ``inflation = 0`` this is
        ``1.0 ** years``, which is exactly 1.0 for every finite exponent, so
        an un-indexed basis costs nothing and moves nothing.
        """
        return (1.0 + self.inflation) ** years


class Commission:
    """Initial and renewal commission on premium, with optional clawback.

    Commission runs at ``initial_percent`` of premium for the first
    ``initial_years`` years of a policy and ``renewal_percent`` after. The
    boundary is a policy-duration boundary, so it survives a change of
    projection frequency untouched.

    **Clawback** recovers initial commission from a policy that lapses
    early. The form here is the common one — a straight-line run-off of one
    year's initial commission over ``clawback_years``, so a lapse on day one
    returns all of it and a lapse at the end returns none — and it is one
    accepted form rather than the only one. A product on a different basis
    overrides the template's ``commission_clawback`` variable; that is what
    the escape hatch in docs/rfc-001-dsl.md is for.

    ``clawback_years = 0`` (the default) means no clawback at all, so an
    assumption set that does not ask for it cannot be changed by it.
    """

    def __init__(self, *, initial_percent: float = 0.0,
                 renewal_percent: float = 0.0, initial_years: float = 1.0,
                 clawback_years: float = 0.0):
        for name, value in (("initial_percent", initial_percent),
                            ("renewal_percent", renewal_percent)):
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} {value} outside [0, 1]")
        if initial_years < 0.0:
            raise ValueError(f"initial_years {initial_years} is negative")
        if clawback_years < 0.0:
            raise ValueError(f"clawback_years {clawback_years} is negative")
        self.initial_percent = initial_percent
        self.renewal_percent = renewal_percent
        self.initial_years = initial_years
        self.clawback_years = clawback_years

    def __repr__(self) -> str:
        return (f"Commission(initial_percent={self.initial_percent}, "
                f"renewal_percent={self.renewal_percent}, "
                f"initial_years={self.initial_years}, "
                f"clawback_years={self.clawback_years})")

    def __bool__(self) -> bool:
        return bool(self.initial_percent or self.renewal_percent)

    def __fingerprint__(self):
        return {"initial_percent": self.initial_percent,
                "renewal_percent": self.renewal_percent,
                "initial_years": self.initial_years,
                "clawback_years": self.clawback_years}

    def rate(self, years_elapsed):
        """Commission rate applying to a premium paid after ``years_elapsed``.

        Indicator style: the duration test is a multiplicative factor, so
        the same expression evaluates for one policy or a whole batch.
        """
        initial = (np.asarray(years_elapsed) < self.initial_years) * 1.0
        return (
            initial * self.initial_percent
            + (1.0 - initial) * self.renewal_percent
        )

    def clawback_fraction(self, years_elapsed):
        """Proportion of one year's initial commission recovered from a
        policy lapsing after ``years_elapsed``.

        Straight-line to zero over ``clawback_years``; identically zero when
        no clawback period is set, which is the default.
        """
        if not self.clawback_years:
            return np.zeros_like(np.asarray(years_elapsed, dtype=np.float64))
        remaining = 1.0 - np.asarray(years_elapsed) / self.clawback_years
        return np.clip(remaining, 0.0, 1.0)
