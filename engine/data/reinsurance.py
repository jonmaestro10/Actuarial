"""Reinsurance treaties: quota share, surplus, and excess of loss.

PLAN.md §5.1 lists these among the Layer 0 primitives, and they are the last
of them outstanding. All three answer the same two questions — *how much of
each claim does the reinsurer pay*, and *what does that cost* — but they
answer the first one differently enough that conflating them is a real
source of error.

**Proportional** treaties cede a share of the risk itself, so the reinsurer
pays that share of every claim and the cedant keeps the rest. The share is a
property of the policy:

- :class:`QuotaShare` cedes the same fraction of every policy.
- :class:`Surplus` cedes only what exceeds a retention, so the fraction
  varies by size — a small policy is wholly retained and a large one is
  mostly ceded.

**Non-proportional** treaties cede a *layer of each claim* rather than a
share of the risk. :class:`ExcessOfLoss` pays what a claim exceeds an excess
point, up to a limit, and nothing at all below it.

The invariant, and where it stops
---------------------------------
For a proportional treaty, **retained plus ceded is the whole sum assured**,
exactly, for every policy — the same shape of statement multiple decrements
make about survival, and asserted the same way. A treaty that failed it
would leave the gross and net views of one block disagreeing about how much
risk exists.

Excess of loss makes no such promise and is not asked to: the layer is a
function of the claim, not a partition of the risk, and above the limit the
excess comes back to the cedant. That is the whole point of a limit, and
:class:`ExcessOfLoss` reports it rather than hiding it.

What is deliberately not here
-----------------------------
**Aggregate and catastrophe covers.** A stop-loss on the year's total claims
or a cat XL on a single event is a statement about the *portfolio*, not
about a policy, and a per-policy projection cannot express one without a
distribution of aggregate losses. Modelling it as if it were per-risk would
give a number, and the number would be wrong in the direction of
understating the cedant's exposure. This module is per-risk only, and says
so rather than approximating.

**Reinstatements, profit commission, sliding scales.** Real treaty wordings
carry all three. Each is a well-defined addition once something needs it;
none of them changes the shape above.
"""

from __future__ import annotations

import math

import numpy as np


class Treaty:
    """What every treaty has to be able to answer.

    Subclasses implement :meth:`recovery_per_claim` and
    :meth:`annual_premium`; the rest follows.
    """

    proportional = False

    def recovery_per_claim(self, sum_assured):
        """What the reinsurer pays on one claim of ``sum_assured``."""
        raise NotImplementedError

    def retained_per_claim(self, sum_assured):
        """What the cedant is left paying — gross less recovery.

        Stated as a subtraction rather than as its own formula so the two
        can never drift apart.
        """
        return np.asarray(sum_assured, dtype=np.float64) - self.recovery_per_claim(
            sum_assured
        )

    def annual_premium(self, *, sum_assured, office_premium):
        """Annual reinsurance premium for one policy."""
        raise NotImplementedError

    def annual_commission(self, *, sum_assured, office_premium):
        """Ceding commission received back on that premium."""
        return 0.0 * np.asarray(sum_assured, dtype=np.float64)


class NoReinsurance(Treaty):
    """The default: everything retained, nothing paid, nothing recovered."""

    proportional = True

    def __repr__(self) -> str:
        return "NoReinsurance()"

    def __bool__(self) -> bool:
        return False

    def __fingerprint__(self):
        return {"treaty": "none"}

    def ceded_fraction(self, sum_assured):
        return 0.0 * np.asarray(sum_assured, dtype=np.float64)

    def recovery_per_claim(self, sum_assured):
        return 0.0 * np.asarray(sum_assured, dtype=np.float64)

    def annual_premium(self, *, sum_assured, office_premium):
        return 0.0 * np.asarray(sum_assured, dtype=np.float64)


class _Proportional(Treaty):
    """Shared machinery for the treaties that cede a share of the risk.

    Two premium bases, because reinsurers quote both:

    ``"original"``
        The reinsurer takes its share of the **office premium** and hands
        back a ceding commission, which is how the cedant recovers the
        acquisition costs it has already paid on the ceded portion. The
        commission is a percentage of the reinsurance premium.
    ``"risk"``
        The reinsurer charges its own rate per mille of **ceded sum
        assured** per year, and there is no ceding commission because there
        is no office premium being shared. Passing one is an error rather
        than a no-op, since it would silently value a treaty nobody wrote.
    """

    proportional = True

    def __init__(self, *, premium_basis: str = "original",
                 commission: float = 0.0, risk_rate_per_mille: float = 0.0):
        if premium_basis not in ("original", "risk"):
            raise ValueError(
                f"premium_basis must be 'original' or 'risk', got "
                f"{premium_basis!r}"
            )
        if not 0.0 <= commission <= 1.0:
            raise ValueError(f"ceding commission {commission} outside [0, 1]")
        if risk_rate_per_mille < 0.0:
            raise ValueError(
                f"risk_rate_per_mille {risk_rate_per_mille} is negative"
            )
        if premium_basis == "risk" and commission:
            raise ValueError(
                "ceding commission only applies on original terms: on a risk "
                "premium basis there is no office premium to share, so there "
                "is nothing to hand back"
            )
        if premium_basis == "original" and risk_rate_per_mille:
            raise ValueError(
                "risk_rate_per_mille only applies on a risk premium basis"
            )
        self.premium_basis = premium_basis
        self.commission = commission
        self.risk_rate_per_mille = risk_rate_per_mille

    def ceded_fraction(self, sum_assured):
        """Share of each policy the reinsurer carries."""
        raise NotImplementedError

    def recovery_per_claim(self, sum_assured):
        sum_assured = np.asarray(sum_assured, dtype=np.float64)
        return self.ceded_fraction(sum_assured) * sum_assured

    def annual_premium(self, *, sum_assured, office_premium):
        ceded = self.ceded_fraction(sum_assured)
        if self.premium_basis == "original":
            return ceded * np.asarray(office_premium, dtype=np.float64)
        return (
            self.risk_rate_per_mille
            * self.recovery_per_claim(sum_assured)
            / 1000.0
        )

    def annual_commission(self, *, sum_assured, office_premium):
        return self.commission * self.annual_premium(
            sum_assured=sum_assured, office_premium=office_premium
        )


class QuotaShare(_Proportional):
    """A fixed share of every policy, whatever its size.

    Simple, and simple in a way that matters: because the fraction does not
    depend on the sum assured, a quota share leaves the *shape* of the block
    alone and only scales it. A cedant worried about large individual
    exposures wants a surplus treaty instead — quota share cedes as much of
    a small policy as of a large one.
    """

    def __init__(self, ceded: float, **kwargs):
        if not 0.0 <= ceded <= 1.0:
            raise ValueError(f"ceded share {ceded} outside [0, 1]")
        super().__init__(**kwargs)
        self.ceded = ceded

    def __repr__(self) -> str:
        return (f"QuotaShare(ceded={self.ceded}, "
                f"premium_basis={self.premium_basis!r})")

    def __bool__(self) -> bool:
        return bool(self.ceded)

    def __fingerprint__(self):
        return {"treaty": "quota_share", "ceded": self.ceded,
                "premium_basis": self.premium_basis,
                "commission": self.commission,
                "risk_rate_per_mille": self.risk_rate_per_mille}

    def ceded_fraction(self, sum_assured):
        return self.ceded * np.ones_like(
            np.asarray(sum_assured, dtype=np.float64)
        )


class Surplus(_Proportional):
    """Retain up to ``retention``, cede the excess — up to ``lines`` of it.

    A surplus treaty is quoted in **lines**, each line being one retention:
    a four-line treaty on a retention of 50,000 will take up to 200,000, so
    a 500,000 policy cedes 200,000 and the cedant is left carrying 300,000,
    not 50,000.

    That cap is the trap this class exists to make visible. It is easy to
    write a surplus treaty as ``max(0, SA - retention)`` and conclude the
    cedant never keeps more than its retention, which is false for exactly
    the policies where being wrong matters most. ``lines`` defaults to
    unlimited, so a treaty that really has no cap says so explicitly rather
    than getting one by accident.
    """

    def __init__(self, retention: float, *, lines: float = math.inf, **kwargs):
        if retention < 0.0:
            raise ValueError(f"retention {retention} is negative")
        if lines <= 0.0:
            raise ValueError(f"lines {lines} must be positive")
        super().__init__(**kwargs)
        self.retention = retention
        self.lines = lines

    def __repr__(self) -> str:
        return (f"Surplus(retention={self.retention}, lines={self.lines}, "
                f"premium_basis={self.premium_basis!r})")

    def __bool__(self) -> bool:
        return self.retention < math.inf

    def __fingerprint__(self):
        return {"treaty": "surplus", "retention": self.retention,
                "lines": self.lines, "premium_basis": self.premium_basis,
                "commission": self.commission,
                "risk_rate_per_mille": self.risk_rate_per_mille}

    @property
    def capacity(self) -> float:
        """The most this treaty will take on one policy.

        ``lines * retention`` — except that an unlimited treaty on a zero
        retention would evaluate ``inf * 0``, which is ``nan``, and a
        ``nan`` cap silently poisons every cession. A zero-retention
        surplus is a full quota share, which is a perfectly ordinary thing
        to write, so the case is handled rather than assumed away.
        """
        if math.isinf(self.lines):
            return math.inf
        return self.lines * self.retention

    def ceded_sum_assured(self, sum_assured):
        """The amount ceded, capped at ``lines`` retentions."""
        sum_assured = np.asarray(sum_assured, dtype=np.float64)
        excess = np.maximum(sum_assured - self.retention, 0.0)
        return np.minimum(excess, self.capacity)

    def ceded_fraction(self, sum_assured):
        sum_assured = np.asarray(sum_assured, dtype=np.float64)
        # A zero sum assured cedes nothing; the guard keeps the division
        # defined rather than relying on nobody passing one.
        nonzero = sum_assured > 0.0
        return np.where(
            nonzero,
            self.ceded_sum_assured(sum_assured)
            / np.where(nonzero, sum_assured, 1.0),
            0.0,
        )


class ExcessOfLoss(Treaty):
    """Per-risk excess of loss: the reinsurer pays a layer of each claim.

    ``recovery = min(max(claim - excess, 0), limit)`` — nothing below the
    excess point, the layer above it, and nothing above ``excess + limit``,
    which comes back to the cedant.

    **Per risk, not per event.** This recovers on each individual claim. A
    catastrophe cover, which responds to the total of many claims from one
    event, is a statement about the portfolio and cannot be evaluated from a
    per-policy projection without a distribution of aggregate losses.
    Treating one as the other would understate the cedant's exposure, so
    this class does not offer it.

    The premium is a percentage of office premium, which is how per-risk
    covers are usually quoted, and carries no ceding commission: nothing
    proportional has been ceded, so there is no acquisition cost to hand
    back.
    """

    def __init__(self, *, excess: float, limit: float = math.inf,
                 premium_percent: float = 0.0):
        if excess < 0.0:
            raise ValueError(f"excess point {excess} is negative")
        if limit <= 0.0:
            raise ValueError(f"limit {limit} must be positive")
        if not 0.0 <= premium_percent <= 1.0:
            raise ValueError(
                f"premium_percent {premium_percent} outside [0, 1]"
            )
        self.excess = excess
        self.limit = limit
        self.premium_percent = premium_percent

    def __repr__(self) -> str:
        return (f"ExcessOfLoss(excess={self.excess}, limit={self.limit}, "
                f"premium_percent={self.premium_percent})")

    def __bool__(self) -> bool:
        return self.excess < math.inf

    def __fingerprint__(self):
        return {"treaty": "xol", "excess": self.excess, "limit": self.limit,
                "premium_percent": self.premium_percent}

    def recovery_per_claim(self, sum_assured):
        above = np.maximum(
            np.asarray(sum_assured, dtype=np.float64) - self.excess, 0.0
        )
        return np.minimum(above, self.limit)

    def annual_premium(self, *, sum_assured, office_premium):
        return self.premium_percent * np.asarray(
            office_premium, dtype=np.float64
        )
