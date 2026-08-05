"""IFRS 17: the premium allocation approach.

The last of the three measurement models §5.3 names, and the only one that
is a **simplification** rather than a measurement in its own right. The
liability for remaining coverage is an unearned premium balance — premiums
received, less acquisition cashflows amortised, less what has been earned —
and there is no contractual service margin at all.

That absence is the whole thing. Under RFC-012's general model the CSM is
where a group's unearned profit lives and where every change in estimate
lands; under the PAA there is no such balance, so a group's profit emerges
purely as premium is earned and nothing defers anything.

The eligibility test that requires the work it exempts you from
---------------------------------------------------------------
§53 permits the PAA on two grounds. The first is mechanical: **coverage of
a year or less**, and nothing more is needed. The second is that the entity
*reasonably expects* the PAA would not differ materially from the general
model — which can only be established by measuring the general model, the
very thing the simplification exists to avoid.

:func:`eligibility` takes that seriously. The one-year limb is answered from
the coverage period alone; the materiality limb is answered by running both
models and comparing, because an expectation formed any other way is not
one this module can record.

What the divergence actually does with term is measured in
tests/test_paa.py, and it is monotone: the two models agree exactly at a
single period and part company steadily as coverage lengthens.

The onerous test does not go away
---------------------------------
§57 keeps it. A PAA group with facts suggesting it is onerous must hold a
loss component, measured on the fulfilment cashflows — which means falling
back to the general model's machinery for exactly the groups where the
simplification is least safe. The simplification is a presentation of a
profitable group, not an exemption from measuring an unprofitable one.
"""

from __future__ import annotations

import numpy as np

from engine.data.rates import YieldCurve
from engine.report.ifrs17 import CoverageUnits, Group, RiskAdjustment, measure

#: §53(a): coverage of a year or less qualifies on its own.
AUTOMATIC_ELIGIBILITY_PERIODS = 1

#: What "not materially different" is taken to mean when nobody says.
#: Not a number the standard gives — it gives no number at all — so it is
#: a default that must be overridable and must be stated wherever it is
#: used.
DEFAULT_MATERIALITY = 0.05


class Eligibility:
    """Whether a group may be measured under the PAA, and on what ground.

    Two grounds, and they are not equivalent. ``"coverage_period"`` is a
    fact about the contract. ``"not_materially_different"`` is a
    *measurement*, and this class records which one was relied on so a
    reader can tell an answered question from an assumed one.
    """

    def __init__(self, *, periods: int, freq: int = 1,
                 relative_difference: float | None = None,
                 materiality: float = DEFAULT_MATERIALITY):
        self.periods = periods
        self.freq = freq
        self.relative_difference = relative_difference
        self.materiality = materiality

    @property
    def years(self) -> float:
        return self.periods / self.freq

    @property
    def by_coverage_period(self) -> bool:
        return self.years <= AUTOMATIC_ELIGIBILITY_PERIODS

    @property
    def by_materiality(self) -> bool:
        if self.relative_difference is None:
            return False
        return abs(self.relative_difference) <= self.materiality

    @property
    def ground(self) -> str | None:
        if self.by_coverage_period:
            return "coverage_period"
        if self.by_materiality:
            return "not_materially_different"
        return None

    def __bool__(self) -> bool:
        return self.ground is not None

    def __repr__(self) -> str:
        return f"Eligibility(eligible={bool(self)}, ground={self.ground!r})"

    def __fingerprint__(self):
        return {"periods": self.periods, "freq": self.freq,
                "relative_difference": self.relative_difference,
                "materiality": self.materiality}

    def explain(self) -> str:
        if self.by_coverage_period:
            return (f"coverage of {self.years:g} year(s) is one year or less "
                    "(§53(a))")
        if self.relative_difference is None:
            return (f"coverage of {self.years:g} years exceeds one year and "
                    "no comparison against the general model was supplied, "
                    "so §53(b) cannot be relied on")
        if self.by_materiality:
            return (f"the PAA differs from the general model by "
                    f"{self.relative_difference:.2%}, within the "
                    f"{self.materiality:.0%} taken as material (§53(b))")
        return (f"the PAA differs from the general model by "
                f"{self.relative_difference:.2%}, beyond the "
                f"{self.materiality:.0%} taken as material")


def eligibility(group: Group, *, coverage: CoverageUnits,
                current: YieldCurve,
                risk_adjustment: RiskAdjustment | None = None,
                freq: int = 1, materiality: float = DEFAULT_MATERIALITY,
                acquisition_periods: int | None = None) -> Eligibility:
    """Test a group for PAA eligibility, doing the work if it has to.

    A group covering a year or less is answered immediately and no general
    model is run. Anything longer is measured **both ways** and compared on
    the liability for remaining coverage at each date, because that is what
    §53(b) is a statement about.

    The irony is deliberate and worth stating rather than hiding: proving
    you may use the simplification costs a full run of the thing it
    simplifies. It is only free for the contracts that never needed it.
    """
    periods = group.n_periods
    if periods / freq <= AUTOMATIC_ELIGIBILITY_PERIODS:
        return Eligibility(periods=periods, freq=freq, materiality=materiality)

    simplified = measure_paa(group, current=current,
                             risk_adjustment=risk_adjustment,
                             acquisition_periods=acquisition_periods)
    general = measure(group, coverage=coverage, current=current,
                      risk_adjustment=risk_adjustment)
    difference = relative_difference(simplified.liability,
                                     general.liability, group.inflows)
    return Eligibility(periods=periods, freq=freq,
                       relative_difference=difference,
                       materiality=materiality)


def relative_difference(simplified, general, premium) -> float:
    """How far the PAA's liability sits from the general model's.

    The largest absolute gap at any date, over the group's **total
    premium**. A maximum rather than a mean, because §53(b) asks whether
    the answer *could* differ materially and an average hides the date
    where it does.

    The denominator is the premium and not the general model's own
    liability, which was the first thing tried and is unusable: a
    profitable group's liability under RFC-012 is nil at issue by
    construction and passes through zero again on its way to an insurance
    contract *asset*, so scaling by it reported gaps of 120% to 200% on
    groups the two models actually agree about, and infinity on a
    single-period one. Premium is stable, non-zero for any group that can
    be tested at all, and is the scale materiality is judged against in
    practice.
    """
    simplified = np.asarray(simplified, dtype=np.float64)
    general = np.asarray(general, dtype=np.float64)
    scale = float(np.abs(np.asarray(premium, dtype=np.float64)).sum())
    if scale == 0.0:
        raise ValueError(
            "a group with no premium has no scale to judge materiality "
            "against, and no unearned premium for the PAA to measure"
        )
    return float(np.abs(simplified - general).max() / scale)


class Measurement:
    """A PAA group's balances and income statement."""

    def __init__(self, **arrays):
        self.__dict__.update(arrays)

    def __repr__(self) -> str:
        return (f"Measurement(LRC[0]={self.liability[0]:,.2f}, "
                f"{self.liability.size - 1} periods"
                f"{', onerous' if self.onerous else ''})")

    @property
    def onerous(self) -> bool:
        return bool(self.loss_component[0] > 0.0)

    def total_profit(self) -> float:
        """The same reconciliation RFC-012 is held to: over a run-off this
        equals the group's undiscounted net cash."""
        return float(self.profit.sum())


def measure_paa(group: Group, *, current: YieldCurve,
                risk_adjustment: RiskAdjustment | None = None,
                acquisition_periods: int | None = None,
                discount_lrc: bool = False,
                onerous_test: bool = True) -> Measurement:
    """Measure a group under the premium allocation approach.

    The liability for remaining coverage rolls:

        LRC[t+1] = LRC[t] + premium received - acquisition amortised
                   - revenue earned

    Revenue is the premium allocated to the period **on the passage of
    time**, which is the PAA's default basis and the reason it is a
    simplification: no coverage units, no CSM, no release pattern to choose.

    ``acquisition_periods`` amortises acquisition cashflows over that many
    periods, or over the whole coverage period by default. §59(a) lets an
    entity expense them immediately when coverage is a year or less; pass
    ``0`` for that, which charges the lot in the first period.

    ``discount_lrc`` accretes the liability where §56 requires it — a
    significant financing component, which by exception does *not* arise
    when coverage is a year or less. Off by default because the groups the
    PAA is written for are the ones where it does not arise.

    ``onerous_test`` keeps §57 alive: a group whose fulfilment cashflows
    exceed its unearned premium carries a loss component, measured on the
    general model's fulfilment cashflows. Switching it off is available for
    isolating the simplification in a test and is not a reporting option.
    """
    n = group.n_periods
    if acquisition_periods is None:
        acquisition_periods = n
    if acquisition_periods < 0 or acquisition_periods > n:
        raise ValueError(
            f"acquisition amortisation over {acquisition_periods} periods "
            f"does not fit a group covering {n}"
        )

    # Acquisition cashflows: spread over the stated periods, or charged in
    # full at once when the entity has elected to expense them.
    acquisition_amortised = np.zeros(n)
    if group.acquisition:
        if acquisition_periods == 0:
            acquisition_amortised[0] = group.acquisition
        else:
            acquisition_amortised[:acquisition_periods] = (
                group.acquisition / acquisition_periods
            )

    rate = (1.0 + current.rates[:n]) ** (1.0 / current.freq) - 1.0

    # Revenue on the passage of time: a level amount per period, solved so
    # that the liability runs to **exactly zero** at the end of the
    # coverage. Solved rather than divided, because two things drain the
    # same balance and a division knows about only one of them.
    #
    # Rolling ``LRC' = (LRC + premium - acquisition) * (1 + i) - R`` to
    # zero at ``n`` gives
    #
    #     R = sum_t (premium[t] - acquisition[t]) * A_t / sum_t A_t
    #
    # where ``A_t`` accumulates from ``t`` to the end. Dividing the premium
    # by ``n`` instead left the liability closing at *minus the acquisition
    # cost* undiscounted, and short by the accreted acquisition when
    # discounted — an unearned premium that is never earned, in both
    # directions.
    #
    # §B126: the part of revenue recovering acquisition cashflows is
    # reported gross, appearing in revenue and in expenses at the same
    # amount, so it nets out of the service result and cannot flatter it.
    growth = 1.0 + rate if discount_lrc else np.ones(n)
    # Two accumulation vectors, not one. A cashflow joins the balance
    # *before* the period's growth and so carries it; the revenue is taken
    # *after* and does not. Using one vector for both leaves the liability
    # short by a period of interest, which is small enough to look like
    # rounding and is not.
    on_entry = np.cumprod(growth[::-1])[::-1]
    on_release = np.concatenate([on_entry[1:], [1.0]])
    drained = group.inflows - acquisition_amortised
    premium_revenue = np.full(
        n, float((drained * on_entry).sum() / on_release.sum())
    )
    revenue = premium_revenue + acquisition_amortised

    liability = np.zeros(n + 1)
    accretion = np.zeros(n)
    for t in range(n):
        opening = liability[t] + group.inflows[t] - acquisition_amortised[t]
        accretion[t] = opening * rate[t] if discount_lrc else 0.0
        # The roll releases the **premium** revenue. The acquisition
        # recovery has already left the balance in ``opening``; subtracting
        # the grossed-up figure here would take it out twice and close the
        # liability at minus the acquisition cost.
        liability[t + 1] = opening + accretion[t] - premium_revenue[t]

    # §57: the onerous test does not go away, and answering it needs the
    # general model's fulfilment cashflows for exactly the groups where the
    # simplification is least safe.
    loss_component = np.zeros(n + 1)
    if onerous_test:
        fcf = group.fulfilment_cashflows(current)
        ra = (risk_adjustment.balance(n) if risk_adjustment is not None
              else np.zeros(n + 1))
        # A group is onerous when the cost of fulfilling the remaining
        # coverage exceeds the unearned premium held against it. The
        # future premium is already inside ``fcf`` as a negative, so the
        # test **subtracts** what has been received and not yet earned.
        #
        # Adding it instead was the first version, and it manufactured a
        # loss component on a group with a 30% margin — the fulfilment
        # cashflows of a profitable group are negative, so adding a
        # positive unearned premium to them flipped the sign of the whole
        # test.
        unearned = liability[:-1]
        shortfall = fcf[:-1] + ra[:-1] - unearned
        loss_component[:-1] = np.maximum(shortfall, 0.0)

    # The loss established at inception is recognised then, not smuggled
    # into a difference. ``diff`` alone telescopes the opening balance out
    # of the income statement entirely — the same fault RFC-015's day-one
    # loss had, in the same shape, caught by the same reconciliation.
    loss_recognised = np.diff(loss_component)
    loss_recognised[0] += loss_component[0]
    ra_release = (risk_adjustment.release(n) if risk_adjustment is not None
                  else np.zeros(n))

    insurance_revenue = revenue
    service_expenses = group.outflows + acquisition_amortised + loss_recognised
    service_result = insurance_revenue - service_expenses
    profit = service_result - accretion

    return Measurement(
        liability=liability + loss_component,
        unearned_premium=liability,
        loss_component=loss_component,
        loss_recognised=loss_recognised,
        acquisition_amortised=acquisition_amortised,
        risk_adjustment_release=ra_release,
        accretion=accretion,
        premium_revenue=premium_revenue,
        insurance_revenue=insurance_revenue,
        insurance_service_expenses=service_expenses,
        insurance_service_result=service_result,
        profit=profit,
    )
