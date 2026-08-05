"""A projected asset portfolio: book values, defaults, reinvestment, sales.

PLAN.md §5.3's last line asks for "**Embedded value / ALM** overlays (asset
models, liability-driven runs)". RFC-020 built the second half and scoped
the first out explicitly, because five earlier RFCs had each stopped at the
same edge and every one of them wanted the same missing piece:

- RFC-010's universal life credits a ``"portfolio"`` rate — *what the
  backing assets earned* — and takes that rate as an input.
- RFC-011's fixed-indexed annuity sets its cap from an option budget, and
  the budget is the yield on the assets less what is needed elsewhere.
- RFC-014's Solvency II has no market-risk modules, because they need
  assets to shock.
- RFC-016's principle-based reserve takes starting assets and earned rates
  as *given*, and CTE(70) of a number somebody handed you is not a reserve.
- RFC-019's with-profits estate is assets less asset shares, and had only
  the second term.

This module is the earned rate. It projects a portfolio of fixed-income
holdings forward alongside a liability cashflow, buying when there is cash
spare and selling when there is not, and reports what the fund actually
earned on a book basis.

The one identity everything is checked against
----------------------------------------------
Every roll-forward in this library is verified against a statement of cash
that cannot be argued with. Here it is::

    closing book = opening book
                 + investment income
                 − default loss
                 + realised gain on sales
                 − trading cost
                 + net liability cashflow
                 + shortfall

Nothing else may move the book value. The last term is zero until the fund
runs out of assets to sell, at which point it is cash that was demanded and
not paid — and it belongs in the identity rather than in the residual,
because otherwise an insolvent projection is indistinguishable from a
broken one. Purchases, sales, coupons and
maturities are *transfers* — they change what the fund holds without
changing what it is worth — and they cancel out of the identity exactly.
:meth:`AssetProjection.reconciles` asserts it period by period, and it is
what catches a projection that has quietly created or destroyed money.

Book value, and why it is not the coupon
----------------------------------------
A holding is carried at **amortised cost** on a constant-yield basis: the
income recognised in a period is the book value times the holding's
purchase yield, and the difference between that and the cash coupon is
amortisation of premium or accretion of discount.

    income[t] = book[t] · ((1 + y)^(1/freq) − 1)
    book[t+1] = book[t] + income[t] − coupon[t]

This matters more than it sounds. A bond bought at a premium pays a coupon
larger than its yield, and the excess is **return of capital, not income**.
An office that books the coupon as earnings reports an earned rate it did
not earn and runs down its assets while its accounts say otherwise. The
recursion above cannot make that mistake: the book value of a holding is
exactly zero when its last payment has been received, by construction of
the yield, and that is asserted rather than assumed.

Defaults happen at the start of the period
------------------------------------------
A defaulted holding pays neither the coupon nor the principal due in the
period it fails, and the recovery arrives as cash immediately. Written the
other way round — coupon first, default afterwards — a portfolio collects
income from bonds that did not survive to pay it. The convention here is
the conservative one and it is stated so it can be argued with.

Recovery is a fraction of **carrying value**, not of face, so a premium
bond loses its premium along with everything else.

The spread is not income
------------------------
A portfolio bought at a spread over the risk-free curve earns that spread
and then loses it again to the defaults the spread exists to compensate.
When ``spread == DefaultBasis.expected_loss`` the net earned rate returns
to the risk-free rate, which is the check that both halves are on the same
scale. Modelling one without the other is the single most common way an
asset projection flatters itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

from engine.core.dates import months_per_period
from engine.data.rates import YieldCurve

# How a shortfall is funded when the fund has to sell.
LIQUIDATION_ORDERS = ("pro_rata", "shortest", "longest")


def forward_factors(curve: YieldCurve, start: int, n: int) -> np.ndarray:
    """``df[k]`` = value at time ``start`` of 1 paid at time ``start + k``.

    ``df[0] == 1``. This is :meth:`YieldCurve.discount_factors` forward
    started: ``forward_factors(curve, 0, n)`` reproduces
    ``curve.discount_factors(n + 1)`` exactly.
    """
    if start < 0 or n < 0:
        raise ValueError(f"start {start} and n {n} must not be negative")
    if start + n > curve.n_periods:
        raise ValueError(
            f"asked for periods {start}..{start + n}, curve covers "
            f"{curve.n_periods}"
        )
    df = np.empty(n + 1, dtype=np.float64)
    df[0] = 1.0
    if n:
        steps = (1.0 + curve.rates[start:start + n]) ** (-1.0 / curve.freq)
        np.cumprod(steps, out=df[1:])
    return df


def par_coupon(curve: YieldCurve, start: int, term: int) -> float:
    """The annual coupon rate at which a ``term``-period bond issues at par.

    On a flat curve this returns the per-period effective rate times the
    frequency — the coupon whose internal rate of return is the curve's own
    rate, to the last bit. That identity is the reason new money can be
    reinvested without a convention argument.
    """
    if term < 1:
        raise ValueError(f"a bond needs at least one period, got {term}")
    df = forward_factors(curve, start, term)
    annuity = df[1:].sum() / curve.freq
    if annuity <= 0.0:
        raise ValueError("the discount curve gives this bond no value")
    return float((1.0 - df[term]) / annuity)


def internal_rate(payments, price: float, freq: int = 1) -> float:
    """The annual effective yield at which ``payments`` are worth ``price``.

    Solved in the per-period discount factor ``v``, in which the present
    value is a polynomial with non-negative coefficients and therefore
    strictly increasing — so bisection cannot land on the wrong root and
    there is no starting guess to get wrong. Newton polishes the last few
    bits.
    """
    flows = np.asarray(payments, dtype=np.float64).ravel()
    if flows.size == 0 or flows.sum() <= 0.0:
        raise ValueError("a holding with no positive payments has no yield")
    if np.any(flows < 0.0):
        raise ValueError(
            "internal_rate needs non-negative payments; a series that "
            "changes sign can have several roots and this solver would "
            "silently pick one"
        )
    if price <= 0.0:
        raise ValueError(f"price {price} must be positive")

    powers = np.arange(1, flows.size + 1, dtype=np.float64)

    def value(v: float) -> float:
        return float((flows * v ** powers).sum())

    lo, hi = 0.0, 1.0
    for _ in range(200):
        if value(hi) >= price:
            break
        hi *= 2.0
    else:  # pragma: no cover - unreachable for finite prices
        raise ValueError(f"no yield reaches a price of {price}")
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if mid <= lo or mid >= hi:
            break
        if value(mid) < price:
            lo = mid
        else:
            hi = mid
    v = 0.5 * (lo + hi)
    for _ in range(3):
        slope = float((flows * powers * v ** (powers - 1.0)).sum())
        if slope <= 0.0:
            break
        step = (value(v) - price) / slope
        if not np.isfinite(step):
            break
        v -= step
    if v <= 0.0:
        raise ValueError(f"price {price} implies an infinite yield")
    return float(v ** (-freq) - 1.0)


@dataclass(frozen=True)
class Bond:
    """A fixed-coupon bullet bond.

    ``coupon`` is the annual rate on ``face``, paid ``freq`` times a year.
    ``price`` defaults to par; a bond bought away from par carries the
    premium or discount into its book yield, where the amortisation
    recursion works it off over the life.
    """

    face: float
    coupon: float
    term: int
    freq: int = 1
    price: float | None = None

    def __post_init__(self):
        months_per_period(self.freq)
        if self.face <= 0.0:
            raise ValueError(f"face {self.face} must be positive")
        if self.term < 1:
            raise ValueError(f"term {self.term} must be at least one period")
        if self.price is not None and self.price <= 0.0:
            raise ValueError(f"price {self.price} must be positive")

    @property
    def cost(self) -> float:
        return float(self.face if self.price is None else self.price)

    def payments(self) -> np.ndarray:
        """The remaining cash payments, one per period, ``term`` long."""
        flows = np.full(self.term, self.face * self.coupon / self.freq)
        flows[-1] += self.face
        return flows

    def book_yield(self) -> float:
        """The constant yield at which the purchase price amortises to zero."""
        return internal_rate(self.payments(), self.cost, self.freq)

    def market_value(self, curve: YieldCurve, start: int = 0) -> float:
        if curve.freq != self.freq:
            raise ValueError(
                f"bond pays {self.freq} times a year, curve is on "
                f"{curve.freq}"
            )
        df = forward_factors(curve, start, self.term)
        return float((self.payments() * df[1:]).sum())

    @classmethod
    def at_par(cls, curve: YieldCurve, term: int, face: float = 1.0,
               start: int = 0, spread: float = 0.0) -> "Bond":
        """A bond issued at par off ``curve``, optionally at a spread.

        The spread is added to the coupon, so the holding's book yield
        exceeds the curve by roughly ``spread`` — which is the point, and
        which :class:`DefaultBasis` is then expected to take back.
        """
        coupon = par_coupon(curve, start, term) + spread
        return cls(face=face, coupon=coupon, term=term, freq=curve.freq)


@dataclass(frozen=True)
class DefaultBasis:
    """Expected credit loss on the portfolio.

    ``annual_rate`` of the carrying value defaults each year, recovering
    ``recovery`` of it in cash. The loss is the rest.

    This is the *expected* loss, applied deterministically — every period
    loses its share rather than a few periods losing everything. That is
    the right shape for a best-estimate projection and the wrong shape for
    a capital calculation, where the whole question is the tail. RFC-016's
    stochastic machinery is where the second one belongs.
    """

    annual_rate: float = 0.0
    recovery: float = 0.0

    def __post_init__(self):
        if not 0.0 <= self.annual_rate < 1.0:
            raise ValueError(
                f"annual default rate {self.annual_rate} must be in [0, 1)"
            )
        if not 0.0 <= self.recovery <= 1.0:
            raise ValueError(f"recovery {self.recovery} must be in [0, 1]")

    @property
    def expected_loss(self) -> float:
        """Annual loss rate — what a credit spread has to cover to break even."""
        return self.annual_rate * (1.0 - self.recovery)

    def per_period(self, freq: int) -> float:
        """The default rate over one period, compounding to the annual rate."""
        return float(1.0 - (1.0 - self.annual_rate) ** (1.0 / freq))

    def __fingerprint__(self):
        return {"annual_rate": self.annual_rate, "recovery": self.recovery}


@dataclass(frozen=True)
class Reinvestment:
    """What the fund does with cash, and what it sells when it needs some.

    ``term`` is the maturity bought with surplus cash, ``spread`` the credit
    spread earned on new money, and ``liquidation`` the order in which
    holdings are sold to meet a shortfall.

    The order is not cosmetic. Selling long holdings first in a market that
    has sold off crystallises the largest loss available; selling short
    ones first crystallises the smallest and leaves the fund longer than it
    started. Both are real strategies and the difference between them is
    measured in RFC-021.
    """

    term: int = 10
    spread: float = 0.0
    liquidation: str = "pro_rata"

    def __post_init__(self):
        if self.term < 1:
            raise ValueError(f"reinvestment term {self.term} must be positive")
        if self.liquidation not in LIQUIDATION_ORDERS:
            raise ValueError(
                f"liquidation order must be one of {LIQUIDATION_ORDERS}, "
                f"got {self.liquidation!r}"
            )

    def __fingerprint__(self):
        return {"term": self.term, "spread": self.spread,
                "liquidation": self.liquidation}


class Holding:
    """One tranche: what is still owed to it, and what it is carried at.

    Deliberately not a :class:`Bond`. A bond is a contract; a holding is a
    position that gets partly sold, partly defaulted and steadily amortised,
    and none of those leave a bond behind.
    """

    __slots__ = ("flows", "book", "rate", "label")

    def __init__(self, flows, book: float, rate: float, label: str = ""):
        self.flows = np.asarray(flows, dtype=np.float64).ravel().copy()
        self.book = float(book)
        self.rate = float(rate)
        self.label = label

    @classmethod
    def from_bond(cls, bond: Bond, units: float = 1.0,
                  label: str = "") -> "Holding":
        rate = (1.0 + bond.book_yield()) ** (1.0 / bond.freq) - 1.0
        return cls(bond.payments() * units, bond.cost * units, rate, label)

    @property
    def periods_remaining(self) -> int:
        return int(self.flows.size)

    def market_value(self, curve: YieldCurve, start: int = 0) -> float:
        if self.flows.size == 0:
            return 0.0
        df = forward_factors(curve, start, self.flows.size)
        return float((self.flows * df[1:]).sum())

    def scale(self, factor: float) -> None:
        self.flows = self.flows * factor
        self.book *= factor

    def copy(self) -> "Holding":
        return Holding(self.flows, self.book, self.rate, self.label)


class Portfolio:
    """A book of holdings, all on the same payment frequency."""

    def __init__(self, holdings: Sequence[Holding] = (), freq: int = 1):
        months_per_period(freq)
        self.freq = freq
        self.holdings = [h.copy() for h in holdings]

    @classmethod
    def from_bonds(cls, bonds: Sequence[Bond], units: Sequence[float] | None = None
                   ) -> "Portfolio":
        bonds = list(bonds)
        if not bonds:
            raise ValueError("a portfolio needs at least one bond")
        freqs = {b.freq for b in bonds}
        if len(freqs) > 1:
            raise ValueError(f"bonds must share a frequency, got {sorted(freqs)}")
        units = [1.0] * len(bonds) if units is None else list(units)
        if len(units) != len(bonds):
            raise ValueError(
                f"{len(bonds)} bonds but {len(units)} unit counts"
            )
        holdings = [Holding.from_bond(b, u, label=f"bond{i}")
                    for i, (b, u) in enumerate(zip(bonds, units))]
        return cls(holdings, freq=bonds[0].freq)

    @classmethod
    def ladder(cls, curve: YieldCurve, longest: int, total: float = 1.0,
               spread: float = 0.0) -> "Portfolio":
        """Equal amounts of par bonds maturing in 1, 2, ... ``longest`` periods.

        The standard starting portfolio for an ALM demonstration, and the
        one whose rolling behaviour makes the portfolio-rate lag visible.
        """
        if longest < 1:
            raise ValueError(f"longest {longest} must be at least one period")
        each = total / longest
        bonds = [Bond.at_par(curve, term, face=each, spread=spread)
                 for term in range(1, longest + 1)]
        return cls.from_bonds(bonds)

    @property
    def book_value(self) -> float:
        return float(sum(h.book for h in self.holdings))

    def market_value(self, curve: YieldCurve, start: int = 0) -> float:
        return float(sum(h.market_value(curve, start) for h in self.holdings))

    @property
    def book_yield(self) -> float:
        """Book-weighted yield, as an annual effective rate."""
        book = self.book_value
        if book == 0.0:
            raise ValueError("an empty portfolio has no yield")
        rate = sum(h.book * h.rate for h in self.holdings) / book
        return float((1.0 + rate) ** self.freq - 1.0)

    def cashflows(self, n_periods: int | None = None) -> np.ndarray:
        """Aggregate payments still due, one entry per period."""
        length = max([h.periods_remaining for h in self.holdings], default=0)
        if n_periods is not None:
            length = n_periods
        total = np.zeros(length, dtype=np.float64)
        for h in self.holdings:
            take = min(h.periods_remaining, length)
            total[:take] += h.flows[:take]
        return total

    def copy(self) -> "Portfolio":
        return Portfolio(self.holdings, freq=self.freq)


@dataclass
class AssetProjection:
    """What the fund held, earned and lost, period by period."""

    opening_book: np.ndarray
    closing_book: np.ndarray
    opening_market: np.ndarray
    closing_market: np.ndarray
    investment_income: np.ndarray
    default_loss: np.ndarray
    realised_gain: np.ndarray
    coupons: np.ndarray
    purchased: np.ndarray
    sold: np.ndarray
    liability_cashflow: np.ndarray
    earned_rate: np.ndarray
    book_yield: np.ndarray
    shortfall: np.ndarray
    traded: np.ndarray
    trading_cost: np.ndarray
    exhausted_at: int | None
    portfolio: Portfolio

    @property
    def net_investment_income(self) -> np.ndarray:
        return (self.investment_income - self.default_loss
                + self.realised_gain - self.trading_cost)

    @property
    def turnover(self) -> float:
        """Total notional traded to hold a duration target."""
        return float(self.traded.sum())

    def annual_earned_rate(self, freq: int) -> np.ndarray:
        """The per-period earned rate restated as an annual effective one."""
        return (1.0 + self.earned_rate) ** freq - 1.0

    def unrealised_gain(self) -> np.ndarray:
        """Market less book at each period end — what has not been taken yet."""
        return self.closing_market - self.closing_book

    def residual(self) -> np.ndarray:
        """Departure from the book identity, period by period.

        ``shortfall`` appears because cash the fund could not raise is cash
        it did not pay. Leaving it out would make an insolvent projection
        look like a broken one.
        """
        return (self.closing_book - self.opening_book
                - self.net_investment_income - self.liability_cashflow
                - self.shortfall)

    def reconciles(self, tolerance: float = 1e-9) -> bool:
        """Does every period satisfy the identity in the module docstring?"""
        scale = max(1.0, float(np.abs(self.opening_book).max()))
        return bool(np.all(np.abs(self.residual()) <= tolerance * scale))


def _curve_at(rates, t: int) -> YieldCurve:
    if isinstance(rates, YieldCurve):
        return rates
    return rates[min(t, len(rates) - 1)]


def _sale_order(holdings, order: str):
    if order == "shortest":
        return sorted(range(len(holdings)),
                      key=lambda i: holdings[i].periods_remaining)
    if order == "longest":
        return sorted(range(len(holdings)),
                      key=lambda i: -holdings[i].periods_remaining)
    return list(range(len(holdings)))


def project(portfolio: Portfolio, liability_cashflows, rates, *,
            reinvestment: Reinvestment | None = None,
            defaults: DefaultBasis | None = None,
            strategy=None) -> AssetProjection:
    """Roll a portfolio forward against a liability cashflow.

    ``liability_cashflows[t]`` is net cash **into** the fund over period
    ``t`` — premiums less claims, benefits and expenses — the same sign
    convention :mod:`engine.report.pbr` uses.

    ``rates`` is either one :class:`YieldCurve`, used forward-started at
    every date, or a sequence of curves observed at times ``0, 1, ...``.
    Forward-starting a single curve is not a simplification: it is the
    arbitrage-free rollforward of that curve, and under it the projected
    earned rate reproduces the curve's own forwards.

    Cash is settled at the end of each period: income accrues on the
    opening book, coupons and maturities arrive, the liability cashflow is
    applied, and whatever is left over is invested or raised. Because
    nothing moves mid-period there is no interest-on-flow convention to
    argue about.

    ``strategy`` is an optional trading rule consulted **after** the cash
    has settled — see :class:`engine.data.rebalance.DurationTarget`. Without
    one the fund never trades except to meet cash, which is the behaviour
    every earlier result here was measured on.
    """
    reinvestment = reinvestment or Reinvestment()
    flows = np.asarray(liability_cashflows, dtype=np.float64).ravel()
    n = flows.size
    freq = portfolio.freq
    default_rate = defaults.per_period(freq) if defaults else 0.0
    recovery = defaults.recovery if defaults else 0.0

    book = portfolio.copy()
    opening_book = np.zeros(n)
    closing_book = np.zeros(n)
    opening_market = np.zeros(n)
    closing_market = np.zeros(n)
    income = np.zeros(n)
    loss = np.zeros(n)
    realised = np.zeros(n)
    coupons = np.zeros(n)
    purchased = np.zeros(n)
    sold = np.zeros(n)
    earned = np.zeros(n)
    yields = np.zeros(n)
    shortfall = np.zeros(n)
    traded = np.zeros(n)
    trading_cost = np.zeros(n)
    exhausted_at: int | None = None

    for t in range(n):
        curve = _curve_at(rates, t)
        end_curve = _curve_at(rates, t + 1)
        for label, c in ((t, curve), (t + 1, end_curve)):
            if c.freq != freq:
                raise ValueError(
                    f"portfolio pays {freq} times a year, curve at {label} "
                    f"is on {c.freq}"
                )
        holdings = book.holdings
        opening_book[t] = sum(h.book for h in holdings)
        opening_market[t] = sum(h.market_value(curve, t) for h in holdings)
        if opening_book[t] > 0.0:
            yields[t] = ((1.0 + sum(h.book * h.rate for h in holdings)
                          / opening_book[t]) ** freq - 1.0)

        # Defaults, before the period's payments: a holding that fails does
        # not pay the coupon it failed on.
        cash = 0.0
        if default_rate > 0.0:
            for h in holdings:
                written = h.book * default_rate
                cash += written * recovery
                loss[t] += written * (1.0 - recovery)
                h.scale(1.0 - default_rate)

        # Income on the surviving book, then the payments it produced.
        for h in holdings:
            income[t] += h.book * h.rate
        for h in holdings:
            due = float(h.flows[0])
            coupons[t] += due
            h.book += h.book * h.rate - due
            h.flows = h.flows[1:]
        cash += coupons[t]
        book.holdings = [h for h in holdings if h.flows.size > 0]

        net = cash + flows[t]
        if net >= 0.0:
            purchased[t] = net
            if net > 0.0:
                bond = Bond.at_par(end_curve, reinvestment.term, face=net,
                                   start=t + 1, spread=reinvestment.spread)
                book.holdings.append(
                    Holding.from_bond(bond, label=f"new{t}")
                )
        else:
            needed = -net
            values = [h.market_value(end_curve, t + 1) for h in book.holdings]
            available = float(sum(values))
            if available < needed:
                # Everything goes, and the rest is a hole in the fund.
                for h, value in zip(book.holdings, values):
                    realised[t] += value - h.book
                sold[t] = available
                shortfall[t] = needed - available
                book.holdings = []
                if exhausted_at is None:
                    exhausted_at = t
            elif reinvestment.liquidation == "pro_rata":
                fraction = needed / available
                for h, value in zip(book.holdings, values):
                    realised[t] += fraction * (value - h.book)
                    h.scale(1.0 - fraction)
                sold[t] = needed
            else:
                raised = 0.0
                for i in _sale_order(book.holdings, reinvestment.liquidation):
                    if raised >= needed:
                        break
                    h, value = book.holdings[i], values[i]
                    if value <= 0.0:
                        continue
                    fraction = min(1.0, (needed - raised) / value)
                    realised[t] += fraction * (value - h.book)
                    raised += fraction * value
                    h.scale(1.0 - fraction)
                sold[t] = raised
            book.holdings = [h for h in book.holdings if h.book != 0.0]

        # Trading to a duration target, after the cash has settled. A
        # rebalance realises a gain or loss on the part sold exactly as a
        # forced sale does, and the spread it crosses leaves the fund for
        # good — so both land in the identity rather than in a valuation.
        if strategy is not None and book.holdings:
            notional, charged, gain = strategy.rebalance(book, end_curve, t + 1)
            traded[t] = notional
            trading_cost[t] = charged
            realised[t] += gain

        closing_book[t] = sum(h.book for h in book.holdings)
        closing_market[t] = sum(h.market_value(end_curve, t + 1)
                                for h in book.holdings)
        if opening_book[t] > 0.0:
            earned[t] = ((income[t] - loss[t] + realised[t]
                          - trading_cost[t]) / opening_book[t])

    return AssetProjection(
        opening_book=opening_book, closing_book=closing_book,
        opening_market=opening_market, closing_market=closing_market,
        investment_income=income, default_loss=loss, realised_gain=realised,
        coupons=coupons, purchased=purchased, sold=sold,
        liability_cashflow=flows, earned_rate=earned, book_yield=yields,
        shortfall=shortfall, traded=traded, trading_cost=trading_cost,
        exhausted_at=exhausted_at, portfolio=book,
    )


def breakeven_spread(defaults: DefaultBasis, risk_free: float,
                     freq: int = 1) -> float:
    """The spread at which a defaulting portfolio nets back to ``risk_free``.

    It is **not** :attr:`DefaultBasis.expected_loss`, and the gap is not a
    rounding error. A holding that defaults at the start of a period pays
    neither its principal nor the income that principal would have earned,
    so the spread has to cover the lost coupon as well as the lost capital::

        s = freq · d · (i + 1 − recovery) / (1 − d)

    for the per-period default rate ``d`` and per-period risk-free rate
    ``i``. On a 2.5% default rate at 40% recovery over a 3% curve that is
    161.5 bp against an expected loss of 150 bp — a 3.9% understatement of
    the compensation required, every year, compounding.
    """
    d = defaults.per_period(freq)
    if d >= 1.0:  # pragma: no cover - DefaultBasis already excludes this
        raise ValueError("a portfolio that always defaults has no spread")
    i = (1.0 + risk_free) ** (1.0 / freq) - 1.0
    return float(freq * d * (i + 1.0 - defaults.recovery) / (1.0 - d))


def earned_rates(projection: AssetProjection, freq: int) -> np.ndarray:
    """The projected earned rate, ready for :mod:`engine.report.pbr`.

    Named because it is the whole point of the module: RFC-016 takes
    earned rates as an input and this is where they now come from.
    """
    return projection.annual_earned_rate(freq)


def half_life(rates_path, start: float, target: float) -> int | None:
    """The first period at which a series has closed half the gap.

    A portfolio does not adopt a new interest rate; it converges on one as
    the old holdings mature. This reports how long that takes, which is the
    number a crediting-rate strategy actually depends on.
    """
    path = np.asarray(rates_path, dtype=np.float64).ravel()
    if start == target:
        raise ValueError("there is no gap to close")
    midpoint = 0.5 * (start + target)
    if target > start:
        reached = np.flatnonzero(path >= midpoint)
    else:
        reached = np.flatnonzero(path <= midpoint)
    return int(reached[0]) if reached.size else None
