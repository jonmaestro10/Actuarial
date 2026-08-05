"""IFRS 17 general measurement model — the contractual service margin.

PLAN.md §5.3 asks for reporting overlays *(products x frameworks)*, with
IFRS 17 first: "GMM/VFA/PAA: CSM roll-forward, risk adjustment, coverage
units". This module is the GMM, which is the one the other two are defined
against.

It is the first thing in the engine that is not a projection. Everything in
``engine/library`` answers "what will happen"; this answers "what does the
accounting say happened", and the two are different questions with different
right answers. The input here is a projection's output — a stream of
expected cashflows for a **group of contracts**, which is IFRS 17's unit of
account — and the output is a roll-forward and a profit and loss.

The three building blocks
-------------------------
An insurance liability under the GMM is:

    fulfilment cashflows  =  PV(outflows) - PV(inflows)
    risk adjustment       =  the margin for non-financial risk
    contractual service margin  =  the unearned profit, released as service
                                   is provided

The CSM is the interesting one, because it exists to **stop profit being
recognised at inception**. Whatever a group is worth on day one is put into
the CSM and released over the coverage period; the accounting result of
writing profitable business is therefore zero, by construction.

The asymmetry that makes the standard worth modelling
-----------------------------------------------------
That construction runs one way only. A group whose fulfilment cashflows and
risk adjustment exceed the premium is **onerous**, and there is no negative
CSM to hold the loss: it goes straight to profit and loss, in full, on day
one. A later improvement does **not** reverse into profit — it must first
extinguish the loss component, and only what is left over rebuilds a CSM.

So two groups with identical total profit over their lives report entirely
different things, and the difference is measured in
:mod:`tests.test_ifrs17` rather than asserted here.

Locked-in rates
---------------
The CSM accretes interest at **the rate that applied when the group was
recognised** and never at today's rate, while the fulfilment cashflows are
discounted at today's. The two curves therefore drift apart for the whole
life of the group, on purpose: the CSM is a historic-cost balance inside a
current-value liability.

What is here and what is not
----------------------------
Here: the roll-forward, the loss component, coverage units (with the
discounting policy choice), a risk adjustment with a release driver, and the
statement of insurance service result. Not here: VFA, PAA, experience
variance, reinsurance held, and transition. Each is its own RFC; see
docs/rfc-012-ifrs17.md.
"""

from __future__ import annotations

import numpy as np

from engine.data.rates import YieldCurve

#: Cashflow timing within a period. ``start`` discounts at ``v(t)``,
#: ``end`` at ``v(t + 1)`` — the same two conventions the templates' present
#: value helpers use, so a projection's output can be handed over without
#: being re-timed.
TIMINGS = ("start", "end")


def _timed_factors(curve: YieldCurve, n: int, timing: str) -> np.ndarray:
    if timing not in TIMINGS:
        raise ValueError(f"timing must be one of {TIMINGS}, got {timing!r}")
    df = curve.discount_factors(n + 1)
    return df[:n] if timing == "start" else df[1:n + 1]


class RiskAdjustment:
    """The margin for non-financial risk, and how it runs off.

    IFRS 17 says what the risk adjustment *is* — compensation for bearing
    uncertainty about the amount and timing of cashflows — and pointedly
    does not say how to calculate it. A library that shipped one method as
    "the" risk adjustment would be wrong for every entity that chose
    another, so this takes the **answer** rather than the method: a total
    amount, and a driver saying how it releases.

    ``driver`` is any series that runs off with the risk — claims expected
    in each period, policies in force, sum assured. The risk adjustment
    held at each date is the total scaled by the driver still to come, so
    it reaches zero exactly when the last exposure does.
    """

    def __init__(self, total: float, driver):
        driver = np.asarray(driver, dtype=np.float64)
        if driver.ndim != 1 or driver.size == 0:
            raise ValueError("the release driver must be a non-empty series")
        if np.any(driver < 0.0):
            raise ValueError("a release driver cannot go negative")
        if driver.sum() <= 0.0:
            raise ValueError(
                "the release driver sums to zero; there is no exposure to "
                "release a risk adjustment over"
            )
        if total < 0.0:
            raise ValueError(
                f"risk adjustment {total} is negative; it is compensation "
                "for bearing risk, not a benefit from it"
            )
        self.total = float(total)
        self.driver = driver

    @classmethod
    def percent_of(cls, claims, margin: float) -> "RiskAdjustment":
        """A percentage of expected claims, released as the claims arise.

        The commonest simple parameterisation, and the one an entity using a
        confidence-level technique will still calibrate *to*.
        """
        claims = np.asarray(claims, dtype=np.float64)
        return cls(margin * float(claims.sum()), claims)

    def __repr__(self) -> str:
        return f"RiskAdjustment(total={self.total}, {self.driver.size} periods)"

    def __fingerprint__(self):
        return {"total": self.total, "driver": self.driver}

    def balance(self, n: int) -> np.ndarray:
        """Risk adjustment held at the start of each of ``n + 1`` dates.

        ``balance[0]`` is the amount at initial recognition and
        ``balance[n]`` is zero, because everything the driver covers has by
        then run off.
        """
        driver = self._sized(n)
        remaining = np.concatenate([[driver.sum()], driver.sum() - np.cumsum(driver)])
        return self.total * remaining / driver.sum()

    def release(self, n: int) -> np.ndarray:
        """Amount released to profit in each period."""
        balance = self.balance(n)
        return balance[:-1] - balance[1:]

    def _sized(self, n: int) -> np.ndarray:
        if self.driver.size < n:
            return np.concatenate(
                [self.driver, np.zeros(n - self.driver.size)]
            )
        return self.driver[:n]


class CoverageUnits:
    """The quantity of service a group provides, period by period.

    This is the single choice that decides *when* a group's profit appears,
    and the standard leaves it open: the units are "the quantity of benefits
    provided and the expected coverage duration", which an entity reads as
    policies in force, or sum assured in force, or expected claims, and each
    reading spreads the same total profit differently.

    ``discount`` is the other open choice. The CSM is released in proportion
    to units provided this period out of units still to be provided; whether
    those future units are discounted first is a policy the standard permits
    either way, and it moves profit **earlier** when switched on because
    later units count for less.
    """

    def __init__(self, units, *, discount: bool = False):
        units = np.asarray(units, dtype=np.float64)
        if units.ndim != 1 or units.size == 0:
            raise ValueError("coverage units must be a non-empty series")
        if np.any(units < 0.0):
            raise ValueError("coverage units cannot be negative")
        if units.sum() <= 0.0:
            raise ValueError(
                "coverage units sum to zero; a group that provides no "
                "service has nothing to release a CSM over"
            )
        self.units = units
        self.discount = discount

    def __repr__(self) -> str:
        return (f"CoverageUnits({self.units.size} periods, "
                f"discount={self.discount})")

    def __fingerprint__(self):
        return {"units": self.units, "discount": self.discount}

    def release_fractions(self, n: int, curve: YieldCurve) -> np.ndarray:
        """Fraction of the CSM released in each period.

        ``units[t] / sum(units[t:])``, optionally with the future units
        discounted at the **locked-in** curve — the same rate the CSM
        accretes at, because a weight and an accretion that disagreed about
        the rate would leave a residue at run-off.

        The last period's fraction is 1.0 by construction, which is what
        drives the closing CSM to exactly zero.
        """
        units = self._sized(n)
        if self.discount:
            df = curve.discount_factors(n + 1)[:n]
            weighted = units * df
        else:
            weighted = units
        remaining = np.cumsum(weighted[::-1])[::-1]
        fractions = np.zeros(n, dtype=np.float64)
        live = remaining > 0.0
        fractions[live] = weighted[live] / remaining[live]
        return fractions

    def _sized(self, n: int) -> np.ndarray:
        if self.units.size < n:
            return np.concatenate([self.units, np.zeros(n - self.units.size)])
        return self.units[:n]


class Group:
    """A group of contracts — IFRS 17's unit of account.

    Cashflows are **expected** amounts for the whole group, one entry per
    projection period, in the engine's usual sense: ``inflows`` at the start
    of the period, ``outflows`` at the end, unless told otherwise.

    ``acquisition`` is a single amount paid at initial recognition. It is an
    outflow of the fulfilment cashflows like any other and therefore reduces
    the CSM — the deferral everyone reaches for is already in the CSM, and
    a separate deferred acquisition cost asset would double count it.
    """

    def __init__(self, inflows, outflows, *, acquisition: float = 0.0,
                 inflow_timing: str = "start", outflow_timing: str = "end"):
        inflows = np.asarray(inflows, dtype=np.float64)
        outflows = np.asarray(outflows, dtype=np.float64)
        if inflows.shape != outflows.shape:
            raise ValueError(
                f"inflows {inflows.shape} and outflows {outflows.shape} "
                "cover different numbers of periods"
            )
        if inflows.ndim != 1 or inflows.size == 0:
            raise ValueError("a group needs at least one period of cashflows")
        self.inflows = inflows
        self.outflows = outflows
        self.acquisition = float(acquisition)
        self.inflow_timing = inflow_timing
        self.outflow_timing = outflow_timing

    @classmethod
    def from_run(cls, result, *, inflows, outflows, acquisition: float = 0.0,
                 periods: int | None = None, **timing) -> "Group":
        """Build a group from a projection's output.

        ``inflows`` and ``outflows`` name output series on the run —
        ``["premiums"]`` and ``["claims", "expenses"]`` for a term
        assurance — and each is summed across the block, which is what
        makes this an *overlay* rather than a separate calculator. The
        accounting reads the projection; it does not re-derive it.

        A model point's worth of contracts is not a group; a group is a
        cohort with a shared profitability. Which policies belong in which
        group is the entity's aggregation policy, so this takes whatever
        run it is handed and measures that as one group — running a
        different aggregation means running it on a different subset.
        """
        def series(names):
            total = None
            for name in names:
                values = np.asarray(result.aggregate(name), dtype=np.float64)
                total = values if total is None else total + values
            if total is None:
                raise ValueError("at least one series name is required")
            return total if periods is None else total[:periods]

        return cls(series(inflows), series(outflows), acquisition=acquisition,
                   **timing)

    @property
    def n_periods(self) -> int:
        return int(self.inflows.size)

    def __repr__(self) -> str:
        return f"Group({self.n_periods} periods)"

    def __fingerprint__(self):
        return {"inflows": self.inflows, "outflows": self.outflows,
                "acquisition": self.acquisition,
                "inflow_timing": self.inflow_timing,
                "outflow_timing": self.outflow_timing}

    def fulfilment_cashflows(self, curve: YieldCurve) -> np.ndarray:
        """PV of outflows less inflows, at the start of each of ``n + 1``
        dates, valued *at that date*.

        Positive is a liability. ``fcf[0]`` includes the acquisition
        cashflow; ``fcf[n]`` is zero, because a group with nothing left to
        pay is worth nothing.
        """
        n = self.n_periods
        v_in = _timed_factors(curve, n, self.inflow_timing)
        v_out = _timed_factors(curve, n, self.outflow_timing)
        net = self.outflows * v_out - self.inflows * v_in
        # Value at each date by dividing out that date's own factor, so the
        # balance is a present value at t rather than at time zero.
        at_zero = np.concatenate([np.cumsum(net[::-1])[::-1], [0.0]])
        df = curve.discount_factors(n + 1)
        fcf = at_zero / df
        fcf[0] += self.acquisition
        return fcf


class Measurement:
    """The result of measuring a group: balances and a profit and loss.

    Every array runs over ``n`` periods except the balances, which run over
    ``n + 1`` dates so the closing position is visible.
    """

    def __init__(self, **arrays):
        self.__dict__.update(arrays)

    def __repr__(self) -> str:
        return (f"Measurement(csm[0]={self.csm[0]:,.2f}, "
                f"loss[0]={self.loss_component[0]:,.2f}, "
                f"{self.csm.size - 1} periods)")

    @property
    def onerous(self) -> bool:
        """Whether the group was onerous at initial recognition."""
        return bool(self.loss_component[0] > 0.0)

    def total_service_result(self) -> float:
        return float(self.insurance_service_result.sum())

    def total_profit(self) -> float:
        """Service result less finance expense, over the group's life.

        The number the whole statement has to reconcile to: on the expected
        basis, over a run-off, it equals the group's undiscounted net cash,
        because accounting cannot invent or destroy money — only move which
        period it appears in.

        An ``experience`` variance and a ``changes_in_estimate`` are the two
        things that move the total, and each moves it by **exactly itself**,
        because each *is* a difference in cash. What they do not do is move
        it by different amounts: the same adverse 400 costs 400 of total
        profit whichever of the two arguments it arrives in. All that
        differs is which years pay for it.
        """
        return float(self.profit.sum())

    def total_experience(self) -> float:
        """Total experience variance charged to current and past service."""
        return float(self.experience_variance.sum())


def measure(group: Group, *, coverage: CoverageUnits,
            risk_adjustment: RiskAdjustment | None = None,
            current: YieldCurve, locked_in: YieldCurve | None = None,
            changes_in_estimate=None, financial_changes=None,
            csm_growth=None, experience=None) -> Measurement:
    """Measure a group of contracts under the general measurement model.

    ``current`` discounts the fulfilment cashflows. ``locked_in`` accretes
    the CSM and defaults to ``current``, which is what it *is* at initial
    recognition — so a group measured on the day it was written needs only
    one curve, and supplying two is how a later reporting date is expressed.

    ``changes_in_estimate`` is an optional per-period series of changes in
    fulfilment cashflows relating to **future** service, signed as the
    fulfilment cashflows are: a positive entry is a worsening, and reduces
    the CSM. Changes relating to *past* service go to profit and loss
    directly and are not this argument.

    ``experience`` is that other argument: the excess of what actually
    happened over what was expected, in the period it happened, signed the
    same way. It relates to **current or past** service, so it does not
    touch the CSM at all — it is an insurance service expense in the period
    it arises and nothing else. Which of the two arguments a given number
    belongs in is a judgement the standard does not make for you; see
    :mod:`engine.report.experience`, which makes the split explicit and
    refuses to guess at it.

    The two arguments answer the same news in different currencies. An
    adverse 100 in ``experience`` costs 100 of this period's profit; the
    same 100 in ``changes_in_estimate`` costs nothing now and reduces every
    future period's CSM release. Total profit is the same either way,
    because the cash is the same — only the years differ.

    ``financial_changes`` is the series of changes in fulfilment cashflows
    caused by **financial** variables. Under this model they do **not**
    touch the CSM: they go to insurance finance income and expense in the
    period they arise, so a market move is a market move and shows up as
    one. That is the single difference the variable fee approach exists to
    make, and keeping it a separate argument here is what lets the two
    models be compared on one line of code.

    ``csm_growth`` replaces the locked-in accretion with an explicit
    per-period amount. It exists for the variable fee approach
    (``engine/report/vfa.py``), where the CSM grows with the entity's share
    of the underlying items rather than at a fixed rate — see RFC-013.
    Whatever it contains, it cannot change the group's total profit: it
    moves the same money between periods, which the reconciliation to net
    cash keeps honest.

    The roll-forward, in the order IFRS 17 B96-B99 sets out:

    1. opening CSM,
    2. plus interest accreted at the locked-in rate,
    3. plus changes in fulfilment cashflows for future service — floored at
       zero, with the excess becoming a loss component,
    4. less the amount released for service provided this period,
    5. equals closing CSM.
    """
    n = group.n_periods
    locked_in = current if locked_in is None else locked_in
    if changes_in_estimate is None:
        changes = np.zeros(n, dtype=np.float64)
    else:
        changes = np.asarray(changes_in_estimate, dtype=np.float64)
        if changes.shape != (n,):
            raise ValueError(
                f"changes_in_estimate covers {changes.shape} periods, the "
                f"group covers {n}"
            )

    fcf = group.fulfilment_cashflows(current)
    ra = (risk_adjustment.balance(n) if risk_adjustment is not None
          else np.zeros(n + 1))
    ra_release = (risk_adjustment.release(n) if risk_adjustment is not None
                  else np.zeros(n))
    fractions = coverage.release_fractions(n, locked_in)
    # One period of accretion at the locked-in curve's own frequency.
    accretion_factor = (
        1.0 + locked_in.rates[:n]
    ) ** (1.0 / locked_in.freq) - 1.0
    if csm_growth is not None:
        csm_growth = np.asarray(csm_growth, dtype=np.float64)
        if csm_growth.shape != (n,):
            raise ValueError(
                f"csm_growth covers {csm_growth.shape} periods, the group "
                f"covers {n}"
            )
    if experience is not None:
        experience = np.asarray(experience, dtype=np.float64)
        if experience.shape != (n,):
            raise ValueError(
                f"experience covers {experience.shape} periods, the group "
                f"covers {n}"
            )
    if financial_changes is None:
        financial = np.zeros(n)
    else:
        financial = np.asarray(financial_changes, dtype=np.float64)
        if financial.shape != (n,):
            raise ValueError(
                f"financial_changes covers {financial.shape} periods, the "
                f"group covers {n}"
            )

    csm = np.zeros(n + 1)
    loss = np.zeros(n + 1)
    accreted = np.zeros(n)
    csm_release = np.zeros(n)
    loss_recognised = np.zeros(n)
    loss_reversed = np.zeros(n)

    # Initial recognition. A group worth more than it costs holds that
    # value as CSM; one worth less has no negative CSM to hold it in, and
    # the shortfall goes to profit and loss **immediately** — §47-48. That
    # is the whole asymmetry, so it is recorded in the first period's
    # ``loss_recognised`` rather than sitting quietly as an opening balance
    # that never reaches the statement.
    day_one = -(fcf[0] + ra[0])
    csm[0] = max(day_one, 0.0)
    loss[0] = max(-day_one, 0.0)
    loss_recognised[0] = loss[0]

    loss_amortised = np.zeros(n)
    #: What a loss component is made of, period by period — the amounts a
    #: systematic allocation can set against it.
    loss_basis = group.outflows + ra_release

    for t in range(n):
        accreted[t] = (csm[t] * accretion_factor[t] if csm_growth is None
                       else csm_growth[t])
        # Everything that moves the CSM this period, signed so that
        # positive is favourable: the growth (accretion under the GMM, the
        # entity's share of the underlying items under the VFA) less the
        # change in fulfilment cashflows, which is signed the other way.
        #
        # One rule for both, because the loss component does not care what
        # made a group better or worse off — a recovery in the underlying
        # items has to extinguish it before rebuilding a margin exactly as
        # a favourable estimate does. Splitting them let a rising pool
        # rebuild the CSM straight past a loss component that was still
        # sitting there.
        balance = csm[t]
        carried_loss = loss[t]
        favourable = accreted[t] - changes[t]
        if favourable >= 0.0:
            # The asymmetry: the loss went to profit the day it appeared,
            # and its reversal does not come back the same way.
            reversal = min(favourable, carried_loss)
            loss_reversed[t] = reversal
            carried_loss -= reversal
            balance += favourable - reversal
        else:
            absorbed = min(-favourable, balance)
            balance -= absorbed
            # ``+=`` and not ``=``: period 0 may already carry the day-one
            # loss, and an adverse movement in the same period adds to it.
            loss_recognised[t] += -favourable - absorbed
            carried_loss += -favourable - absorbed

        csm_release[t] = balance * fractions[t]
        csm[t + 1] = balance - csm_release[t]

        # B119: once a loss component exists, the period's expected claims
        # and risk adjustment release are split between it and insurance
        # revenue on a systematic basis. The basis is the loss's own
        # constituents — this period's service expenses as a share of all
        # that remain — NOT the coverage units that release the CSM. The
        # difference matters exactly when claims and coverage part company:
        # allocated on coverage units and capped by the period's outflows, a
        # group whose claims land early froze the unamortised remainder the
        # day its outflows stopped, and carried a loss component forever
        # inside a fulfilment-cashflow balance of zero. On its own basis the
        # loss telescopes to nothing with the last service expense.
        #
        # The cap keeps the split from making either revenue or expenses
        # negative. It now binds only when the loss exceeds every service
        # expense left to allocate it against — an acquisition-driven loss,
        # which B125's recovery mechanism would amortise and this module
        # does not model (see the RFC's scope note).
        remaining_basis = loss_basis[t:].sum()
        loss_fraction = (
            loss_basis[t] / remaining_basis if remaining_basis > 0.0 else 0.0
        )
        loss_amortised[t] = min(carried_loss * loss_fraction, loss_basis[t])
        loss[t + 1] = carried_loss - loss_amortised[t]

    # Revenue is what the group earned for service provided: the expected
    # claims and risk released, *less* the part allocated to a loss
    # component (which was recognised when the loss arose and cannot be
    # earned twice), plus the CSM released.
    revenue = group.outflows + ra_release - loss_amortised + csm_release
    service_expenses = (
        group.outflows - loss_amortised + loss_recognised - loss_reversed
    )
    # An experience variance on current service is an expense of the period
    # it arose in and nothing else — it earns no revenue, because no extra
    # service was provided for it, and it does not reach the CSM, because
    # the CSM is unearned profit on service still to come.
    #
    # Added under a branch rather than as ``+ np.zeros(n)`` so that a call
    # without it evaluates the identical expression it always did, bit for
    # bit, and the whole existing golden suite stands as the regression
    # test for this change.
    if experience is not None:
        service_expenses = service_expenses + experience
    service_result = revenue - service_expenses

    # Insurance finance expense: the unwind of discount on the fulfilment
    # cashflows, plus the CSM's accretion at the locked-in rate.
    #
    # The unwind is derived from the roll rather than assumed, which is
    # what makes it exact. Rearranging
    # ``fcf[t] = -inflow[t] + outflow[t]*v + fcf[t+1]*v`` gives
    # ``fcf[t+1] = (fcf[t] + inflow[t]) * (1 + i) - outflow[t]``, so what
    # accretes over the period is the balance *after* the start-of-period
    # inflow has been received.
    #
    # The risk adjustment here carries no unwind, because it is an amount
    # allocated by a driver rather than a discounted balance — an entity
    # discounting its risk adjustment supplies the discounted series and
    # gets the same treatment as the cashflows.
    current_rate = (1.0 + current.rates[:n]) ** (1.0 / current.freq) - 1.0
    inflow_held = (
        group.inflows if group.inflow_timing == "start" else np.zeros(n)
    )
    # The acquisition cashflow is paid at initial recognition, so it is out
    # of the door before the first period accretes and must come out of the
    # balance that unwinds. Leaving it in finances it for a period it was
    # never outstanding, and total profit misses the group's net cash by
    # exactly ``acquisition * i`` — which is how this was found.
    paid_at_zero = np.zeros(n)
    paid_at_zero[0] = group.acquisition
    fcf_unwind = (fcf[:n] + inflow_held - paid_at_zero) * current_rate
    # A change in fulfilment cashflows from a financial variable belongs in
    # the finance line, signed as the cashflows are: a worsening is a
    # positive change and an expense.
    finance_expense = fcf_unwind + accreted + financial
    profit = service_result - finance_expense

    return Measurement(
        fulfilment_cashflows=fcf,
        risk_adjustment=ra,
        risk_adjustment_release=ra_release,
        csm=csm,
        csm_accreted=accreted,
        csm_release=csm_release,
        release_fractions=fractions,
        loss_component=loss,
        loss_recognised=loss_recognised,
        loss_reversed=loss_reversed,
        loss_amortised=loss_amortised,
        insurance_revenue=revenue,
        insurance_service_expenses=service_expenses,
        experience_variance=(experience if experience is not None
                             else np.zeros(n)),
        insurance_service_result=service_result,
        fcf_unwind=fcf_unwind,
        financial_changes=financial,
        insurance_finance_expense=finance_expense,
        profit=profit,
        #: Liability for remaining coverage. The loss component is a
        #: sub-balance *of* the fulfilment cashflows, not an addition to
        #: them, so it does not appear here.
        liability=fcf + ra + csm,
    )
