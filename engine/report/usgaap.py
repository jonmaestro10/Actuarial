"""US GAAP for long-duration contracts — ASU 2018-12 (LDTI).

PLAN.md §5.3 asks for "**US STAT/GAAP-LDTI**". This is the GAAP half: the
Targeted Improvements to the Accounting for Long-Duration Contracts, which
replaced a forty-year-old model in 2023 and rebuilt three things.

- **The liability for future policy benefits** on traditional and
  limited-payment contracts, measured with a **net premium ratio** that is
  updated as assumptions change rather than locked at issue.
- **Market risk benefits** — the GMxB family — pulled out of the host
  contract and measured at **fair value**.
- **Deferred acquisition costs**, amortized on a constant level basis with
  no interest, no shadow adjustments, and no sensitivity to profitability
  whatever.

Read against RFC-012, because it is the same economics twice
------------------------------------------------------------
IFRS 17 and LDTI both start from a projection of the same contract, both
insist that writing profitable business produces no day-one profit, and both
treat a shortfall asymmetrically. They then disagree about almost everything
else, and the sharpest disagreement is about **time**:

    IFRS 17 is prospective. A change in estimate adjusts the CSM and is
    released over the coverage that remains.

    LDTI is retrospective. A change in estimate re-derives the net premium
    ratio **from the issue date**, restates the whole history, and puts the
    difference in this period's income as a single catch-up.

Same event, same cashflows, opposite timing. That difference is measured in
tests/test_usgaap.py rather than asserted here, and it is large.

The other structural echo is the discount rate. LDTI accretes interest on
the liability at the rate **locked in at issue** and carries the balance
sheet at the **current upper-medium-grade rate**, with the difference in
other comprehensive income. RFC-012's CSM does the same thing with the same
motivation and puts the difference somewhere else.

Sign conventions
----------------
Benefits and expenses are positive outflows; premiums are positive inflows.
A liability is positive. Everything here works in **expected amounts for a
cohort** — LDTI's unit of account is an issue-year cohort, and which
policies belong in which cohort is the entity's grouping policy, not
something a cashflow table can be asked.
"""

from __future__ import annotations

import numpy as np

from engine.data.rates import YieldCurve

#: The net premium ratio cannot exceed one. A cohort whose benefits are
#: worth more than every premium it will ever collect cannot defer the
#: excess against premiums that do not exist.
NPR_CAP = 1.0


def _pv_forward(flows: np.ndarray, curve: YieldCurve, timing: str) -> np.ndarray:
    """Present value of ``flows[t:]`` at the start of each of ``n + 1`` dates.

    ``timing`` is ``"start"`` or ``"end"`` of the period, matching the
    templates' own present-value helpers so a projection's output can be
    handed over without being re-timed.
    """
    if timing not in ("start", "end"):
        raise ValueError(f"timing must be 'start' or 'end', got {timing!r}")
    n = flows.size
    df = curve.discount_factors(n + 1)
    at_zero = flows * (df[:n] if timing == "start" else df[1:n + 1])
    running = np.concatenate([np.cumsum(at_zero[::-1])[::-1], [0.0]])
    return running / df[:n + 1]


class Cohort:
    """An issue-year cohort's expected cashflows.

    ``premiums`` are gross premiums received at the start of each period;
    ``benefits`` are death, surrender and maturity benefits paid at the end.
    ``expenses`` are the directly attributable maintenance costs that LDTI
    includes in the liability — *not* acquisition costs, which are deferred
    separately and would otherwise be counted twice.
    """

    def __init__(self, premiums, benefits, expenses=None):
        premiums = np.asarray(premiums, dtype=np.float64)
        benefits = np.asarray(benefits, dtype=np.float64)
        if premiums.shape != benefits.shape:
            raise ValueError(
                f"premiums {premiums.shape} and benefits {benefits.shape} "
                "cover different numbers of periods"
            )
        if premiums.ndim != 1 or premiums.size == 0:
            raise ValueError("a cohort needs at least one period of cashflows")
        expenses = (np.zeros_like(premiums) if expenses is None
                    else np.asarray(expenses, dtype=np.float64))
        if expenses.shape != premiums.shape:
            raise ValueError(
                f"expenses {expenses.shape} cover a different number of "
                f"periods from the premiums {premiums.shape}"
            )
        self.premiums = premiums
        self.benefits = benefits
        self.expenses = expenses

    @classmethod
    def from_run(cls, result, *, premiums, benefits, expenses=(),
                 periods: int | None = None) -> "Cohort":
        """Build a cohort from a projection's own output series.

        The same bridge RFC-012 uses, for the same reason: the accounting
        reads the projection rather than re-deriving a cashflow.
        """
        def series(names):
            if not names:
                return None
            total = None
            for name in names:
                values = np.asarray(result.aggregate(name), dtype=np.float64)
                total = values if total is None else total + values
            return total if periods is None else total[:periods]

        return cls(series(premiums), series(benefits), series(expenses))

    @property
    def n_periods(self) -> int:
        return int(self.premiums.size)

    @property
    def outflows(self) -> np.ndarray:
        return self.benefits + self.expenses

    def __repr__(self) -> str:
        return f"Cohort({self.n_periods} periods)"

    def __fingerprint__(self):
        return {"premiums": self.premiums, "benefits": self.benefits,
                "expenses": self.expenses}


class LiabilityForFuturePolicyBenefits:
    """The LFPB under the net premium ratio approach.

    At issue the entity solves for the fraction of each gross premium that,
    accumulated at the locked-in rate, exactly funds the benefits:

        NPR = PV(benefits and expenses) / PV(gross premiums)

    and the reserve at any later date is the present value of the future
    benefits less that same fraction of the future gross premiums. At issue
    the two are equal by construction, so **the reserve is nil and no profit
    is recognised** — the same statement RFC-012's CSM makes, arrived at
    from the other end.

    **The ratio is capped at one.** A cohort whose benefits are worth more
    than every premium it will ever collect cannot defer the excess against
    premiums that do not exist, so the cap binds, the reserve opens above
    zero, and the shortfall is a loss on day one. That is LDTI's version of
    RFC-012's loss component, and it is the same asymmetry expressed as a
    ratio instead of a balance.

    **The reserve is floored at zero.** A negative liability would be an
    asset for a contract that has not yet been paid for.
    """

    def __init__(self, cohort: Cohort, *, locked_in: YieldCurve,
                 premium_timing: str = "start", benefit_timing: str = "end"):
        self.cohort = cohort
        self.locked_in = locked_in
        self.premium_timing = premium_timing
        self.benefit_timing = benefit_timing
        self._pv_premiums = _pv_forward(cohort.premiums, locked_in,
                                        premium_timing)
        self._pv_outflows = _pv_forward(cohort.outflows, locked_in,
                                        benefit_timing)
        self.uncapped_ratio = self._solve_ratio()
        self.net_premium_ratio = min(self.uncapped_ratio, NPR_CAP)

    def _solve_ratio(self) -> float:
        if self._pv_premiums[0] <= 0.0:
            raise ValueError(
                "the cohort collects no premium; a net premium ratio is a "
                "share of a premium and there is none to take a share of"
            )
        return float(self._pv_outflows[0] / self._pv_premiums[0])

    @property
    def capped(self) -> bool:
        """Whether the 100% cap bound — LDTI's onerous test."""
        return self.uncapped_ratio > NPR_CAP

    def __repr__(self) -> str:
        return (f"LiabilityForFuturePolicyBenefits(NPR="
                f"{self.net_premium_ratio:.4f}"
                f"{', capped' if self.capped else ''})")

    def balance(self, curve: YieldCurve | None = None) -> np.ndarray:
        """Reserve at the start of each of ``n + 1`` dates.

        With no curve, the **locked-in** measurement that drives the income
        statement. With one, the same reserve rediscounted at that curve —
        which is the balance-sheet measurement, and the difference between
        the two is what goes to other comprehensive income.
        """
        if curve is None:
            pv_premiums, pv_outflows = self._pv_premiums, self._pv_outflows
        else:
            pv_premiums = _pv_forward(self.cohort.premiums, curve,
                                      self.premium_timing)
            pv_outflows = _pv_forward(self.cohort.outflows, curve,
                                      self.benefit_timing)
        return np.maximum(pv_outflows - self.net_premium_ratio * pv_premiums,
                          0.0)

    def net_premiums(self) -> np.ndarray:
        """The part of each gross premium that funds benefits."""
        return self.net_premium_ratio * self.cohort.premiums

    def remeasure(self, revised: Cohort, *, at: int
                  ) -> "LiabilityForFuturePolicyBenefits":
        """Re-derive the net premium ratio with revised expectations.

        **Retrospectively, from the issue date.** LDTI does not adjust the
        ratio going forward; it recalculates it over the whole history using
        actual experience to ``at`` and revised expectations after it, at the
        **original locked-in rate**. The reserve implied by the new ratio is
        then compared with the one carried, and the whole difference is this
        period's income.

        That is the opposite of RFC-012, where the same change adjusts the
        CSM and emerges over the coverage that remains. Same event, same
        cashflows, opposite timing — and the gap is measured rather than
        described.
        """
        if not 0 <= at <= self.cohort.n_periods:
            raise ValueError(
                f"remeasurement date {at} outside the cohort's "
                f"0..{self.cohort.n_periods}"
            )
        if revised.n_periods != self.cohort.n_periods:
            raise ValueError(
                f"the revised cohort covers {revised.n_periods} periods and "
                f"the original covers {self.cohort.n_periods}"
            )
        # Actual experience up to `at`, revised expectations after it. The
        # history is the original cohort's, which is what "actual" means in
        # a projection with no experience variance.
        blended = Cohort(
            np.concatenate([self.cohort.premiums[:at], revised.premiums[at:]]),
            np.concatenate([self.cohort.benefits[:at], revised.benefits[at:]]),
            np.concatenate([self.cohort.expenses[:at], revised.expenses[at:]]),
        )
        return LiabilityForFuturePolicyBenefits(
            blended, locked_in=self.locked_in,
            premium_timing=self.premium_timing,
            benefit_timing=self.benefit_timing,
        )

    def remeasurement_gain(self, revised: "LiabilityForFuturePolicyBenefits",
                           at: int) -> float:
        """The catch-up: the fall in the reserve the new ratio implies.

        Negative is a loss, which is the usual direction — an assumption
        update that costs money raises the ratio and therefore the reserve.
        Reported as its own line, because LDTI requires it separately from
        the ordinary movement.
        """
        return float(self.balance()[at] - revised.balance()[at])


class DeferredAcquisitionCosts:
    """DAC, amortized on a constant level basis.

    LDTI's third change, and the simplest thing in this module by a long
    way. Before it, DAC amortized in proportion to estimated gross profits,
    which made it a function of investment returns, unlocked every period,
    and required a shadow balance for unrealised gains.

    Now it amortizes **on a constant level basis over the expected term**,
    which in practice means straight-line over an in-force driver. There is
    no interest accretion, no unlocking for profitability, and no shadow
    adjustment. **The amortization is identical whether the cohort is wildly
    profitable or deeply onerous**, which is the whole point of the change
    and is worth stating because it is so unlike everything around it.

    The driver is policy count or face amount in force. A cohort that
    terminates faster amortizes faster, because there are fewer contracts
    left to spread over — that is the *only* thing that moves it.
    """

    def __init__(self, capitalized: float, driver):
        driver = np.asarray(driver, dtype=np.float64)
        if driver.ndim != 1 or driver.size == 0:
            raise ValueError("the in-force driver must be a non-empty series")
        if np.any(driver < 0.0):
            raise ValueError("an in-force driver cannot go negative")
        if driver.sum() <= 0.0:
            raise ValueError(
                "the driver sums to zero; there is no expected term to "
                "amortize over"
            )
        if capitalized < 0.0:
            raise ValueError(
                f"capitalized costs {capitalized} are negative"
            )
        self.capitalized = float(capitalized)
        self.driver = driver

    def __repr__(self) -> str:
        return (f"DeferredAcquisitionCosts({self.capitalized:,.2f} over "
                f"{self.driver.size} periods)")

    def __fingerprint__(self):
        return {"capitalized": self.capitalized, "driver": self.driver}

    def amortization(self) -> np.ndarray:
        """Amount charged to income in each period."""
        return self.capitalized * self.driver / self.driver.sum()

    def balance(self) -> np.ndarray:
        """Unamortized balance at the start of each of ``n + 1`` dates."""
        charged = self.amortization()
        return np.concatenate([[self.capitalized],
                               self.capitalized - np.cumsum(charged)])


class MarketRiskBenefit:
    """A GMxB-type guarantee, at fair value.

    LDTI pulls these out of the host contract and measures them at fair
    value every period. The measurement is *net*: the guarantee's expected
    cost less an **attributed fee** — a fixed share of the contract's total
    fees, solved at inception so that the market risk benefit is exactly
    zero on day one.

    **The attributed fee ratio is capped at one.** A guarantee whose
    expected cost exceeds every fee the contract will ever charge cannot
    attribute more than all of them, so the cap binds and the benefit opens
    as a **liability at issue** — the third cap in this module with the same
    shape as the other two, and the same meaning: a contract that was sold
    too cheaply says so immediately.

    Changes in fair value go to income, **except** the portion attributable
    to a change in the entity's own credit standing, which goes to other
    comprehensive income. That carve-out exists so an insurer's own distress
    does not flatter its earnings, and it is taken here as an input rather
    than derived — own credit is a market observation, not a projection.
    """

    def __init__(self, guarantee_cost, fees, *, own_credit_change=None):
        guarantee_cost = np.asarray(guarantee_cost, dtype=np.float64)
        fees = np.asarray(fees, dtype=np.float64)
        if guarantee_cost.shape != fees.shape:
            raise ValueError(
                f"guarantee cost {guarantee_cost.shape} and fees "
                f"{fees.shape} cover different numbers of dates"
            )
        if guarantee_cost.ndim != 1 or guarantee_cost.size < 2:
            raise ValueError(
                "a market risk benefit needs a value at each date, opening "
                "and closing included"
            )
        if fees[0] <= 0.0:
            raise ValueError(
                "the contract charges no fees at inception; there is nothing "
                "to attribute a share of"
            )
        self.guarantee_cost = guarantee_cost
        self.fees = fees
        self.uncapped_ratio = float(guarantee_cost[0] / fees[0])
        self.attributed_fee_ratio = min(self.uncapped_ratio, 1.0)
        self.own_credit_change = (
            np.zeros(guarantee_cost.size - 1) if own_credit_change is None
            else np.asarray(own_credit_change, dtype=np.float64)
        )
        if self.own_credit_change.shape != (guarantee_cost.size - 1,):
            raise ValueError(
                "own credit changes need one entry per period"
            )

    @property
    def capped(self) -> bool:
        return self.uncapped_ratio > 1.0

    def __repr__(self) -> str:
        return (f"MarketRiskBenefit(attributed_fee_ratio="
                f"{self.attributed_fee_ratio:.4f}"
                f"{', capped' if self.capped else ''})")

    def __fingerprint__(self):
        return {"guarantee_cost": self.guarantee_cost, "fees": self.fees,
                "own_credit_change": self.own_credit_change}

    def fair_value(self) -> np.ndarray:
        """The market risk benefit at each date. Positive is a liability."""
        return self.guarantee_cost - self.attributed_fee_ratio * self.fees

    def income_statement_change(self) -> np.ndarray:
        """Change in fair value charged to income each period."""
        return np.diff(self.fair_value()) - self.own_credit_change

    def oci_change(self) -> np.ndarray:
        """The own-credit portion, which goes to OCI instead."""
        return self.own_credit_change.copy()


class Measurement:
    """A cohort's LDTI balances and income statement."""

    def __init__(self, **arrays):
        self.__dict__.update(arrays)

    def __repr__(self) -> str:
        return (f"Measurement(NPR={self.net_premium_ratio:.4f}, "
                f"{self.reserve.size - 1} periods)")

    def total_income(self) -> float:
        """Net income over the cohort's life.

        The reconciliation this module is held to, exactly as RFC-012's is:
        over a run-off with no experience variance it equals the cohort's
        undiscounted net cash, because accounting cannot invent or destroy
        money.
        """
        return float(self.net_income.sum())


def measure(cohort: Cohort, *, locked_in: YieldCurve,
            current: YieldCurve | None = None,
            dac: DeferredAcquisitionCosts | None = None,
            premium_timing: str = "start",
            benefit_timing: str = "end") -> Measurement:
    """Measure a cohort under LDTI.

    ``current`` is the upper-medium-grade rate at the reporting date. Supply
    it and the balance sheet carries the reserve at that rate while income
    still accretes at ``locked_in``, with the difference presented in other
    comprehensive income. Omit it and the two coincide, which is what they
    do at issue.

    Acquisition costs are **not** an argument here. They are capitalized at
    inception — cash out, an asset up — and reach income only through
    ``dac``'s amortization. Charging them again when they were paid would
    count them twice, which is what the reconciliation to net cash caught
    when this function first took them separately.
    """
    lfpb = LiabilityForFuturePolicyBenefits(
        cohort, locked_in=locked_in, premium_timing=premium_timing,
        benefit_timing=benefit_timing,
    )
    n = cohort.n_periods
    reserve = lfpb.balance()
    balance_sheet = reserve if current is None else lfpb.balance(current)
    #: Unrealised gain or loss on the liability, held in accumulated OCI.
    #: The whole reason LDTI splits the two curves: a rate move changes what
    #: the balance sheet says without touching what income reports.
    aoci = balance_sheet - reserve

    rate = (1.0 + locked_in.rates[:n]) ** (1.0 / locked_in.freq) - 1.0
    # Interest accreted on the reserve, derived from the roll rather than
    # assumed. Rearranging the reserve's own definition gives
    # ``reserve[t+1] = (reserve[t] + net premium[t]) * (1 + i) - outflow[t]``,
    # so what accretes is the balance carried *after* the start-of-period
    # net premium — the same derivation RFC-012 uses for its fulfilment
    # cashflows.
    #
    # This is a **disclosure line, not a deduction**: it is already inside
    # the change in the reserve below, and subtracting it again was the
    # second thing the reconciliation to net cash caught.
    held = lfpb.net_premiums() if premium_timing == "start" else np.zeros(n)
    interest = (reserve[:n] + held) * rate

    amortization = (np.zeros(n) if dac is None
                    else dac.amortization()[:n])
    dac_balance = (np.zeros(n + 1) if dac is None else dac.balance()[:n + 1])

    # The income statement: gross premium earned, less benefits and expenses
    # incurred, less the movement in the reserve, less DAC amortization.
    change_in_reserve = reserve[1:] - reserve[:n]
    net_income = (
        cohort.premiums - cohort.outflows - change_in_reserve - amortization
    )

    # A cohort that failed the cap opens with a reserve it has been paid
    # nothing for, and that shortfall is a loss on day one. Without this the
    # opening balance telescopes out of the change-in-reserve line and total
    # income overstates the cohort's net cash by exactly ``reserve[0]``,
    # which is how it was found. Zero whenever the cap did not bind, so an
    # ordinary cohort is untouched.
    day_one_loss = float(reserve[0])
    net_income[0] -= day_one_loss

    return Measurement(
        net_premium_ratio=lfpb.net_premium_ratio,
        uncapped_ratio=lfpb.uncapped_ratio,
        capped=lfpb.capped,
        reserve=reserve,
        balance_sheet_reserve=balance_sheet,
        aoci=aoci,
        oci=np.diff(aoci),
        net_premiums=lfpb.net_premiums(),
        interest_accreted=interest,
        day_one_loss=day_one_loss,
        dac_balance=dac_balance,
        dac_amortization=amortization,
        net_income=net_income,
        lfpb=lfpb,
    )
