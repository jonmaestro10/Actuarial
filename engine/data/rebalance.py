"""Trading to a duration target, and what immunisation actually costs.

RFC-020 demonstrated that **matching duration does not immunise** — two
portfolios matching a liability's value and duration to machine precision
moved in opposite directions under the same shift, because the second
derivative was left free. RFC-021 then built a real portfolio and scoped
this out in as many words:

> **A trading strategy.** Sales happen only to meet cash, never to
> rebalance to a duration target — which means this module can *measure*
> RFC-020's duration gap and cannot yet close one.

This closes one. A :class:`DurationTarget` is consulted by
:func:`engine.data.assets.project` after each period's cash has settled: it
computes what the portfolio's duration has drifted to, and trades the
**minimum notional** that puts it back.

The trade is a single equation
------------------------------
Selling a notional ``x`` of a portfolio worth ``A`` at duration ``D`` and
buying ``x`` of something at duration ``d`` moves the whole portfolio to

    D + (x / A) · (d − D)

so hitting a target ``D*`` needs

    x = A · (D* − D) / (d − D)

and no search. Lengthening buys the long bond, shortening buys the short
one, and the sale is pro rata across what is held — which is the neutral
choice, and the one RFC-021 showed changes the *timing* of a realised loss
and not its total.

Nothing here is free
--------------------
Every trade crosses a :class:`TradingCost` spread, charged on the notional
traded and taken out of the book value as it happens. That is the whole
reason this module is interesting: **duration matching is a subscription,
not a purchase**. A portfolio drifts as it ages and as rates move, so a
target held to the basis point is a target rebought every period, and what
it costs is a function of how often you look rather than of how well you
hedge.

:class:`DurationTarget` therefore carries a ``tolerance`` — a no-trade band
— and an ``every``, so the trade-off can be measured instead of assumed.
See RFC-025 for what it comes to.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from engine.data.assets import Bond, Holding, Portfolio, forward_factors
from engine.data.rates import YieldCurve


def portfolio_duration(portfolio: Portfolio, curve: YieldCurve,
                       start: int = 0) -> float:
    """Macaulay duration of everything held, in years.

    Value-weighted across holdings, and computed off the aggregate cashflow
    rather than by averaging each holding's own duration — the two agree,
    but only one of them keeps agreeing when a holding is worth nothing.
    """
    length = max((h.periods_remaining for h in portfolio.holdings), default=0)
    if length == 0:
        raise ValueError("an empty portfolio has no duration")
    flows = np.zeros(length, dtype=np.float64)
    for holding in portfolio.holdings:
        flows[:holding.periods_remaining] += holding.flows
    df = forward_factors(curve, start, length)
    values = flows * df[1:]
    total = values.sum()
    if total <= 0.0:
        raise ValueError(
            "a portfolio worth nothing has no duration; there is no time at "
            "which its value is concentrated"
        )
    times = np.arange(1, length + 1, dtype=np.float64) / curve.freq
    return float((values * times).sum() / total)


def bond_duration(curve: YieldCurve, term: int, start: int = 0,
                  spread: float = 0.0) -> float:
    """Duration of the par bond this module would buy at ``term``."""
    bond = Bond.at_par(curve, term, start=start, spread=spread)
    df = forward_factors(curve, start, term)
    values = bond.payments() * df[1:]
    times = np.arange(1, term + 1, dtype=np.float64) / curve.freq
    return float((values * times).sum() / values.sum())


@dataclass(frozen=True)
class TradingCost:
    """What crossing the market costs, as a fraction of notional traded.

    One number, charged on the notional rather than on the change in
    duration, because that is what a dealer charges. It is taken out of the
    book value at the moment of the trade, which is what makes it show up
    in the earned rate rather than hiding in a valuation.
    """

    spread: float = 0.0

    def __post_init__(self):
        if not 0.0 <= self.spread < 1.0:
            raise ValueError(
                f"trading spread {self.spread} must be in [0, 1); a spread "
                "of one means the trade takes the whole notional"
            )

    def charge(self, notional: float) -> float:
        return float(abs(notional) * self.spread)

    def __fingerprint__(self):
        return {"spread": self.spread}


def execute_trade(portfolio: Portfolio, curve: YieldCurve, t: int,
                  target: float, *, long_term: int, short_term: int,
                  tolerance: float = 0.0, spread: float = 0.0,
                  cost: TradingCost = TradingCost()) -> tuple:
    """Trade the minimum notional that puts the duration on ``target``.

    Shared by every strategy here, because the *trade* is the same
    arithmetic whatever decided the target — and a second copy of it would
    be a second place for the sign to be wrong.

    Returns ``(notional, cost, realised)``: what was sold and rebought,
    what the spread took, and the gain or loss crystallised on the part
    sold. Zeros when there is nothing to trade, the drift is inside the
    band, or the available maturities are on the wrong side of the gap to
    close it.
    """
    if not portfolio.holdings:
        return 0.0, 0.0, 0.0
    values = [h.market_value(curve, t) for h in portfolio.holdings]
    assets = float(sum(values))
    if assets <= 0.0:
        return 0.0, 0.0, 0.0
    current = portfolio_duration(portfolio, curve, t)
    drift = target - current
    if abs(drift) <= tolerance:
        return 0.0, 0.0, 0.0

    term = long_term if drift > 0.0 else short_term
    bought = bond_duration(curve, term, start=t, spread=spread)
    if bought == current:
        # Buying what is already held cannot move the duration, so the
        # honest answer is to do nothing rather than to trade forever.
        return 0.0, 0.0, 0.0
    notional = assets * drift / (bought - current)
    if notional <= 0.0:
        # The available maturity is on the wrong side of the current
        # duration to close the gap — a portfolio already longer than the
        # long bond, say. Trading would move it further away.
        return 0.0, 0.0, 0.0
    notional = min(notional, assets)

    fraction = notional / assets
    realised = 0.0
    for holding, value in zip(portfolio.holdings, values):
        realised += fraction * (value - holding.book)
        holding.scale(1.0 - fraction)
    portfolio.holdings = [h for h in portfolio.holdings if h.book != 0.0]

    charged = cost.charge(notional)
    reinvested = notional - charged
    if reinvested > 0.0:
        bond = Bond.at_par(curve, term, face=reinvested, start=t,
                           spread=spread)
        portfolio.holdings.append(Holding.from_bond(bond, label=f"rebal{t}"))
    return float(notional), float(charged), float(realised)


@dataclass(frozen=True)
class DurationTarget:
    """Trade the portfolio back to a duration, on a schedule, within a band.

    ``target`` is the duration in **years** — either one number, held for
    the whole projection, or a series giving the target at each date. The
    difference is not cosmetic: a liability's own duration **falls as it
    runs off**, so a target fixed at inception stops describing the
    liability almost immediately and the fund trades hard to hold a number
    that has stopped meaning anything. :meth:`from_liabilities` builds the
    moving one, and RFC-025 measures what the fixed one costs.

    ``long_term`` and
    ``short_term`` are the maturities, in periods, that the strategy buys
    to lengthen and to shorten — it needs both, because a portfolio drifts
    in either direction and a strategy that can only lengthen is not a
    strategy.

    ``tolerance`` is a no-trade band in years: inside it nothing happens.
    ``every`` rebalances only on periods that are a multiple of it. The two
    are different instruments — a band responds to *drift*, a schedule to
    the *calendar* — and RFC-025 measures which one buys more.
    """

    target: float | tuple
    long_term: int
    short_term: int
    tolerance: float = 0.0
    every: int = 1
    spread: float = 0.0
    cost: TradingCost = TradingCost()

    def __post_init__(self):
        targets = np.atleast_1d(np.asarray(self.target, dtype=np.float64))
        if targets.size == 0 or np.any(targets <= 0.0):
            raise ValueError(
                f"target duration {self.target} must be positive at every date"
            )
        object.__setattr__(self, "_targets", targets)
        if self.long_term < 1 or self.short_term < 1:
            raise ValueError("both maturities must be at least one period")
        if self.long_term <= self.short_term:
            raise ValueError(
                f"long term {self.long_term} must exceed short term "
                f"{self.short_term}; a strategy that buys the same thing "
                "either way cannot move a duration"
            )
        if self.tolerance < 0.0:
            raise ValueError(f"tolerance {self.tolerance} is negative")
        if self.every < 1:
            raise ValueError(f"every {self.every} must be at least one period")

    @classmethod
    def from_liabilities(cls, liabilities, curve: YieldCurve, **kwargs
                         ) -> "DurationTarget":
        """A target that tracks the liability's own duration at each date.

        The honest version of duration matching, and the one a fixed target
        only coincides with on day one.
        """
        return cls(target=tuple(matched_target_path(liabilities, curve)),
                   **kwargs)

    def target_at(self, t: int) -> float:
        """The target in force at date ``t``, held flat past the last one."""
        targets = self._targets
        return float(targets[min(t, targets.size - 1)])

    def due(self, t: int) -> bool:
        return (t + 1) % self.every == 0

    def rebalance(self, portfolio: Portfolio, curve: YieldCurve,
                  t: int) -> tuple:
        """Trade toward the target. Returns ``(notional, cost, realised)``.

        ``notional`` is what was sold and rebought, ``cost`` what the
        spread took, and ``realised`` the gain or loss crystallised on the
        part sold — a real trade realises one exactly as a forced sale
        does, which is the point RFC-021 makes about liquidation order and
        applies here for a different reason.

        Returns zeros and touches nothing when the trade is not due, the
        drift is inside the band, or the portfolio has nothing to trade.
        """
        if not self.due(t):
            return 0.0, 0.0, 0.0
        return execute_trade(portfolio, curve, t, self.target_at(t),
                             long_term=self.long_term,
                             short_term=self.short_term,
                             tolerance=self.tolerance, spread=self.spread,
                             cost=self.cost)

    def __fingerprint__(self):
        return {"target": self._targets, "long_term": self.long_term,
                "short_term": self.short_term, "tolerance": self.tolerance,
                "every": self.every, "spread": self.spread,
                "cost": self.cost.spread}


def matched_target_path(liabilities, curve: YieldCurve) -> np.ndarray:
    """The liability's duration measured at **each** date, not just the first.

    A liability does not hold its duration: as it runs off, the payments
    that remain are the later ones, and the average time to them falls.
    Between the first entry and the last this typically halves and more, so
    a fund holding the first entry is deliberately mismatched by the
    difference — which is the point RFC-025 measures.

    One entry per date the liabilities cover. Dates with nothing left to pay
    carry the previous target rather than raising, because a fund with no
    liability left has nothing to match and should not be made to trade.
    """
    flows = np.asarray(liabilities, dtype=np.float64).ravel()
    outgo = -np.minimum(flows, 0.0)
    if outgo.sum() <= 0.0:
        raise ValueError(
            "these liabilities never pay anything out; there is nothing to "
            "match a duration to"
        )
    path = np.zeros(outgo.size, dtype=np.float64)
    carried = None
    for t in range(outgo.size):
        remaining = outgo[t:]
        if remaining.sum() <= 0.0:
            path[t] = carried if carried is not None else 0.0
            continue
        df = forward_factors(curve, t, remaining.size)
        values = remaining * df[1:]
        times = np.arange(1, remaining.size + 1, dtype=np.float64) / curve.freq
        carried = float((values * times).sum() / values.sum())
        path[t] = carried
    if carried is None:  # pragma: no cover - guarded by the sum check above
        raise ValueError("no date has anything left to pay")
    path[path == 0.0] = carried
    return path


def matched_target(liabilities, curve: YieldCurve) -> float:
    """The liability's own duration — what a matched fund would target.

    Provided so a target is derived from the liability rather than typed
    in. **The value at date zero only**, which is exactly the trap: hold it
    and the fund is matched on day one and progressively mismatched
    afterwards. :func:`matched_target_path` is the one to use.

    RFC-020's warning stands on top of that: hitting a duration pins the
    first derivative and leaves the second free, and this module cannot
    change that.
    """
    return float(matched_target_path(liabilities, curve)[0])


def liability_position(liabilities, curve: YieldCurve, t: int) -> tuple:
    """``(present value, duration)`` of what is left to pay from date ``t``.

    Valued at the curve prevailing then, not at the one that applied when
    the target was set, because a target computed off a stale valuation is
    a target for a liability the fund no longer has.
    """
    outgo = -np.minimum(np.asarray(liabilities, dtype=np.float64).ravel(),
                        0.0)[t:]
    if outgo.size == 0 or outgo.sum() <= 0.0:
        return 0.0, 0.0
    df = forward_factors(curve, t, outgo.size)
    values = outgo * df[1:]
    times = np.arange(1, outgo.size + 1, dtype=np.float64) / curve.freq
    total = float(values.sum())
    return total, float((values * times).sum() / total)


@dataclass(frozen=True)
class SurplusTarget:
    """Equate **dollar** durations, which is what immunising surplus means.

    :class:`DurationTarget` matches a *time*. That is only the right thing
    to match when the two sides are worth the same amount, and they never
    are — a fund with assets of 1,777 at duration 7.81 against liabilities
    of 635 at the same 7.81 has a dollar-duration gap of **8,912**, nearly
    three times the liability's own. It has matched its duration and hedged
    almost nothing.

    RFC-020 states the principle — "``duration_gap`` is value-weighted,
    because the unweighted difference is the wrong number whenever the two
    sides are worth different amounts" — and this is that principle as a
    strategy. The asset duration that equates the two is

        D* = D_liability · (liability value / asset value)

    which on the fund above is **2.79 years**, not 7.81. It is recomputed
    at every rebalance because both the ratio and the liability's own
    duration move, and it collapses to :class:`DurationTarget` exactly when
    the fund holds no surplus.

    A surplus large enough to make ``D*`` shorter than ``short_term``'s own
    duration cannot be reached by buying bonds; the trade then closes as
    much of the gap as it can, which is the honest failure and is what
    ``turnover`` will show.
    """

    liabilities: tuple
    long_term: int
    short_term: int
    tolerance: float = 0.0
    every: int = 1
    spread: float = 0.0
    cost: TradingCost = TradingCost()

    def __post_init__(self):
        flows = np.asarray(self.liabilities, dtype=np.float64).ravel()
        if flows.size == 0 or -np.minimum(flows, 0.0).sum() <= 0.0:
            raise ValueError(
                "these liabilities never pay anything out; there is nothing "
                "to immunise against"
            )
        object.__setattr__(self, "_flows", flows)
        if self.long_term <= self.short_term:
            raise ValueError(
                f"long term {self.long_term} must exceed short term "
                f"{self.short_term}"
            )
        if self.tolerance < 0.0:
            raise ValueError(f"tolerance {self.tolerance} is negative")
        if self.every < 1:
            raise ValueError(f"every {self.every} must be at least one period")

    def due(self, t: int) -> bool:
        return (t + 1) % self.every == 0

    def target_at_date(self, portfolio: Portfolio, curve: YieldCurve,
                       t: int) -> float:
        """``D_liability · L / A`` at the prevailing curve."""
        assets = portfolio.market_value(curve, t)
        value, duration = liability_position(self._flows, curve, t)
        if assets <= 0.0 or value <= 0.0:
            return 0.0
        return duration * value / assets

    def rebalance(self, portfolio: Portfolio, curve: YieldCurve,
                  t: int) -> tuple:
        if not self.due(t) or not portfolio.holdings:
            return 0.0, 0.0, 0.0
        target = self.target_at_date(portfolio, curve, t)
        if target <= 0.0:
            return 0.0, 0.0, 0.0
        return execute_trade(portfolio, curve, t, target,
                             long_term=self.long_term,
                             short_term=self.short_term,
                             tolerance=self.tolerance, spread=self.spread,
                             cost=self.cost)

    def __fingerprint__(self):
        return {"liabilities": self._flows, "long_term": self.long_term,
                "short_term": self.short_term, "tolerance": self.tolerance,
                "every": self.every, "spread": self.spread,
                "cost": self.cost.spread}
