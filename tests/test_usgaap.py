"""US GAAP for long-duration contracts — ASU 2018-12 (LDTI).

The comparisons against RFC-012's IFRS 17 are the point. Both frameworks
measure the same contract from the same projection; where they disagree,
the disagreement is measured.
"""

from __future__ import annotations

import numpy as np
import pytest

from engine.data.rates import YieldCurve
from engine.report.ifrs17 import (
    CoverageUnits, Group, measure as measure_ifrs17,
)
from engine.report.usgaap import (
    NPR_CAP, Cohort, DeferredAcquisitionCosts,
    LiabilityForFuturePolicyBenefits, MarketRiskBenefit, Measurement, measure,
)

LOCKED = YieldCurve.flat(0.04, freq=1, horizon_years=60)
N = 15


def rising_cohort(benefit_growth=1.18, premium=1000.0, base_benefit=200.0):
    """Rising benefits against a level premium — the shape that makes a
    reserve build, and the shape traditional life business actually has."""
    return Cohort(
        np.full(N, premium),
        base_benefit * benefit_growth ** np.arange(N),
        np.full(N, 30.0),
    )


def net_cash(cohort: Cohort, dac: DeferredAcquisitionCosts | None = None):
    capitalized = 0.0 if dac is None else dac.capitalized
    return float(cohort.premiums.sum() - cohort.outflows.sum() - capitalized)


# --- the reconciliation --------------------------------------------------


@pytest.mark.parametrize("growth,dac", [
    (1.18, None),
    (1.40, None),                       # onerous: the NPR cap binds
    (1.18, DeferredAcquisitionCosts(400.0, 0.95 ** np.arange(N))),
    (1.00, DeferredAcquisitionCosts(250.0, np.full(N, 1.0))),
    (1.30, DeferredAcquisitionCosts(900.0, 0.90 ** np.arange(N))),
])
def test_total_income_is_the_cohorts_net_cash(growth, dac):
    """The same discipline RFC-012 is held to, and it earned its keep twice
    here: it caught acquisition costs being charged when paid *and* again
    when amortized, and interest being deducted after it had already been
    counted inside the change in the reserve."""
    cohort = rising_cohort(growth)
    result = measure(cohort, locked_in=LOCKED, dac=dac)
    assert result.total_income() == pytest.approx(net_cash(cohort, dac),
                                                  abs=1e-8)


def test_the_reserve_is_nil_at_issue_and_at_run_off():
    """Nil at issue is the whole construction: the net premium ratio is
    solved so that it is. Nil at run-off is what says the arithmetic closes."""
    result = measure(rising_cohort(), locked_in=LOCKED)
    assert result.reserve[0] == pytest.approx(0.0, abs=1e-9)
    assert result.reserve[-1] == pytest.approx(0.0, abs=1e-9)
    assert result.reserve[1:-1].min() > 0.0


def test_the_reserve_rolls_forward_the_way_its_definition_says():
    """``reserve[t+1] = reserve[t] + net premium + interest - outflow``,
    which is the rearrangement the interest disclosure is derived from
    rather than an assumption laid beside it."""
    cohort = rising_cohort()
    result = measure(cohort, locked_in=LOCKED)
    rolled = (result.reserve[:N] + result.net_premiums
              + result.interest_accreted - cohort.outflows)
    assert rolled == pytest.approx(result.reserve[1:], abs=1e-9)


def test_a_profitable_cohort_recognises_nothing_on_day_one():
    result = measure(rising_cohort(), locked_in=LOCKED)
    assert not result.capped
    assert result.reserve[0] == pytest.approx(0.0, abs=1e-9)


# --- the cap -------------------------------------------------------------


def test_the_net_premium_ratio_cannot_exceed_one():
    """LDTI's onerous test, and the same asymmetry RFC-012 expresses as a
    loss component: a cohort whose benefits are worth more than every
    premium it will ever collect cannot defer the excess against premiums
    that do not exist."""
    cohort = rising_cohort()
    onerous = Cohort(cohort.premiums, cohort.benefits * 3.0, cohort.expenses)
    lfpb = LiabilityForFuturePolicyBenefits(onerous, locked_in=LOCKED)
    assert lfpb.uncapped_ratio > 2.0
    assert lfpb.net_premium_ratio == NPR_CAP
    assert lfpb.capped
    # The reserve opens above zero, which is the day-one loss.
    assert lfpb.balance()[0] > 0.0


def test_an_uncapped_cohort_reports_its_uncapped_ratio_too():
    """So a reader can see how far past the cap a cohort went, which the
    capped figure alone cannot say."""
    lfpb = LiabilityForFuturePolicyBenefits(rising_cohort(), locked_in=LOCKED)
    assert lfpb.uncapped_ratio == lfpb.net_premium_ratio
    assert not lfpb.capped


def test_the_reserve_is_floored_at_zero():
    """A negative liability would be an asset for a contract that has not
    yet been paid for."""
    cohort = Cohort(np.full(N, 5000.0), 200.0 * 1.18 ** np.arange(N))
    assert (measure(cohort, locked_in=LOCKED).reserve >= 0.0).all()


def test_a_cohort_with_no_premium_is_refused():
    with pytest.raises(ValueError, match="no premium"):
        LiabilityForFuturePolicyBenefits(
            Cohort(np.zeros(N), np.full(N, 100.0)), locked_in=LOCKED)


# --- the finding: retrospective against prospective ---------------------


def _same_change(at=8, factor=1.25):
    cohort = rising_cohort()
    revised = Cohort(cohort.premiums, cohort.benefits * factor,
                     cohort.expenses)
    return cohort, revised


def test_ldti_recognises_a_change_at_once_where_ifrs_17_spreads_it():
    """The sharpest disagreement between the two frameworks, measured.

    A 25% deterioration in benefits, discovered in year 8 of a fifteen-year
    cohort. LDTI re-derives the net premium ratio **from the issue date**,
    restates the whole history and puts the difference in this period's
    income. IFRS 17 adjusts the CSM and releases it over the seven years of
    coverage that remain.

    Same event, same cashflows. LDTI's hit is over four times IFRS 17's.
    """
    at = 8
    cohort, revised = _same_change(at=at)

    base = LiabilityForFuturePolicyBenefits(cohort, locked_in=LOCKED)
    after = base.remeasure(revised, at=at)
    ldti_hit = -base.remeasurement_gain(after, at)

    group = Group(cohort.premiums, cohort.outflows)
    worse = Group(cohort.premiums, revised.outflows)
    change = np.zeros(N)
    change[at] = (worse.fulfilment_cashflows(LOCKED)[at]
                  - group.fulfilment_cashflows(LOCKED)[at])
    units = CoverageUnits(np.full(N, 1.0))
    before = measure_ifrs17(group, coverage=units, current=LOCKED)
    stressed = measure_ifrs17(group, coverage=units, current=LOCKED,
                              changes_in_estimate=change)
    ifrs_hit = -(stressed.insurance_service_result[at]
                 - before.insurance_service_result[at])

    assert ldti_hit > 0.0 and ifrs_hit > 0.0
    assert after.net_premium_ratio > base.net_premium_ratio
    assert ldti_hit / ifrs_hit == pytest.approx(4.24, abs=0.15)


def test_the_remeasurement_restates_the_history_not_just_the_future():
    """What "retrospective" means here: the revised ratio is solved over
    the whole cohort, so periods already reported are part of the answer.

    A change applied at the very first period and the same change applied
    late give different ratios, because a different amount of history has
    been fixed by then.
    """
    cohort, revised = _same_change()
    base = LiabilityForFuturePolicyBenefits(cohort, locked_in=LOCKED)
    early = base.remeasure(revised, at=0)
    late = base.remeasure(revised, at=12)
    assert early.net_premium_ratio > late.net_premium_ratio > base.net_premium_ratio


def test_a_remeasured_cohort_still_reconciles_to_its_own_cash():
    cohort, revised = _same_change()
    base = LiabilityForFuturePolicyBenefits(cohort, locked_in=LOCKED)
    after = base.remeasure(revised, at=8)
    result = measure(after.cohort, locked_in=LOCKED)
    assert result.total_income() == pytest.approx(net_cash(after.cohort),
                                                  abs=1e-8)


def test_a_remeasurement_outside_the_cohort_is_refused():
    cohort, revised = _same_change()
    base = LiabilityForFuturePolicyBenefits(cohort, locked_in=LOCKED)
    with pytest.raises(ValueError, match="outside the cohort"):
        base.remeasure(revised, at=N + 1)


def test_a_revised_cohort_of_a_different_length_is_refused():
    cohort, _ = _same_change()
    base = LiabilityForFuturePolicyBenefits(cohort, locked_in=LOCKED)
    short = Cohort(np.full(5, 1000.0), np.full(5, 200.0))
    with pytest.raises(ValueError, match="covers 5 periods"):
        base.remeasure(short, at=3)


# --- the two discount rates ----------------------------------------------


def test_a_rate_move_changes_the_balance_sheet_and_not_earnings():
    """LDTI's other structural echo of RFC-012's locked-in CSM, and the
    reason insurers report a separate AOCI line: the liability accretes at
    the rate locked in at issue and is *carried* at today's, with the whole
    difference in other comprehensive income.
    """
    cohort = rising_cohort()
    income_only = measure(cohort, locked_in=LOCKED)
    for rate, direction in ((0.02, 1.0), (0.06, -1.0)):
        current = YieldCurve.flat(rate, freq=1, horizon_years=60)
        result = measure(cohort, locked_in=LOCKED, current=current)
        assert result.net_income == pytest.approx(income_only.net_income,
                                                  abs=1e-9)
        assert direction * result.aoci[5] > 0.0
        assert result.balance_sheet_reserve[5] != result.reserve[5]


def test_the_unrealised_gain_unwinds_to_nothing():
    """It is unrealised: the two curves value the same run-off, so whatever
    the rate did, the difference is zero once there is nothing left.

    It is *not* zero at the start. At issue the two curves are the same
    curve — the locked-in rate is the current rate that day — so an AOCI
    balance at time zero is a rate move that has already happened, and
    supplying a different current curve is how that is expressed.
    """
    current = YieldCurve.flat(0.02, freq=1, horizon_years=60)
    result = measure(rising_cohort(), locked_in=LOCKED, current=current)
    assert result.aoci[0] > 0.0
    assert result.aoci[-1] == pytest.approx(0.0, abs=1e-9)
    assert result.oci.sum() == pytest.approx(-result.aoci[0], abs=1e-9)


def test_a_two_hundred_basis_point_fall_is_material_against_the_reserve():
    current = YieldCurve.flat(0.02, freq=1, horizon_years=60)
    result = measure(rising_cohort(), locked_in=LOCKED, current=current)
    peak = int(np.argmax(result.reserve))
    assert result.aoci[peak] / result.reserve[peak] == pytest.approx(0.114, abs=0.01)


def test_without_a_current_curve_the_two_measurements_coincide():
    """Which is what they do at issue, so a cohort measured on the day it
    was written needs one curve and not two."""
    result = measure(rising_cohort(), locked_in=LOCKED)
    assert (result.balance_sheet_reserve == result.reserve).all()
    assert (result.aoci == 0.0).all()


# --- deferred acquisition costs -----------------------------------------


def test_dac_amortization_is_insensitive_to_profitability():
    """LDTI's third change, and the whole point of it. Before, DAC
    amortized in proportion to estimated gross profits and had to be
    unlocked every period; now it is straight-line over an in-force
    driver, and a wildly profitable cohort and a deeply onerous one
    amortize **identically**."""
    driver = 0.92 ** np.arange(N)
    dac = DeferredAcquisitionCosts(1000.0, driver)
    profitable = measure(rising_cohort(1.05), locked_in=LOCKED, dac=dac)
    onerous = measure(rising_cohort(1.40), locked_in=LOCKED, dac=dac)
    assert (profitable.dac_amortization == onerous.dac_amortization).all()
    assert (profitable.dac_balance == onerous.dac_balance).all()


def test_dac_amortizes_exactly_what_was_capitalized():
    dac = DeferredAcquisitionCosts(1000.0, 0.92 ** np.arange(N))
    assert dac.amortization().sum() == pytest.approx(1000.0, rel=1e-12)
    assert dac.balance()[0] == 1000.0
    assert dac.balance()[-1] == pytest.approx(0.0, abs=1e-9)


def test_a_cohort_that_terminates_faster_amortizes_faster():
    """The only thing that moves the amortization: there are fewer
    contracts left to spread over."""
    slow = DeferredAcquisitionCosts(1000.0, 0.98 ** np.arange(N))
    fast = DeferredAcquisitionCosts(1000.0, 0.80 ** np.arange(N))
    assert fast.amortization()[0] > 1.5 * slow.amortization()[0]
    assert fast.balance()[5] < slow.balance()[5]


def test_dac_carries_no_interest():
    """No accretion, no shadow balance. The balance is the capitalized
    amount less what has been charged, and nothing else."""
    dac = DeferredAcquisitionCosts(600.0, np.full(N, 1.0))
    assert dac.balance()[1:] == pytest.approx(
        600.0 - np.cumsum(np.full(N, 40.0)))


def test_a_driver_that_never_runs_is_refused():
    with pytest.raises(ValueError, match="no expected term"):
        DeferredAcquisitionCosts(100.0, np.zeros(5))


def test_negative_capitalized_costs_are_refused():
    with pytest.raises(ValueError, match="negative"):
        DeferredAcquisitionCosts(-1.0, np.ones(5))


# --- market risk benefits ------------------------------------------------


def _fees(n=12, start=500.0, decay=0.93):
    return start * decay ** np.arange(n + 1)


def test_a_market_risk_benefit_is_zero_at_inception_by_construction():
    """The attributed fee ratio is solved so that it is — the same
    statement the net premium ratio makes about the reserve, and the CSM
    about day-one profit."""
    fees = _fees()
    mrb = MarketRiskBenefit(0.24 * fees, fees)
    assert mrb.attributed_fee_ratio == pytest.approx(0.24)
    assert not mrb.capped
    assert mrb.fair_value()[0] == pytest.approx(0.0, abs=1e-12)


def test_the_attributed_fee_ratio_cannot_exceed_every_fee_there_is():
    """The third cap in this module with the same shape as the other two,
    and the same meaning: a contract sold too cheaply says so on day one
    rather than later."""
    fees = _fees()
    mrb = MarketRiskBenefit(1.8 * fees, fees)
    assert mrb.uncapped_ratio == pytest.approx(1.8)
    assert mrb.attributed_fee_ratio == 1.0
    assert mrb.capped
    assert mrb.fair_value()[0] == pytest.approx(0.8 * fees[0])


def test_a_market_move_goes_straight_to_income():
    """No deferral of any kind — which is the contrast with RFC-013's
    variable fee approach, where the same move adjusts the CSM."""
    fees = _fees()
    cost = 0.24 * fees
    shocked = cost.copy()
    shocked[5:] *= 4.0
    mrb = MarketRiskBenefit(shocked, fees)
    income = mrb.income_statement_change()
    assert income[4] > 200.0
    assert (np.abs(income[:4]) < 1e-9).all()


def test_the_own_credit_portion_goes_to_oci_instead():
    """So an insurer's own distress cannot flatter its earnings."""
    fees = _fees()
    cost = 0.24 * fees
    cost[5:] *= 4.0
    plain = MarketRiskBenefit(cost, fees)
    own = np.zeros(12)
    own[5] = 300.0
    split = MarketRiskBenefit(cost, fees, own_credit_change=own)
    assert split.oci_change()[5] == 300.0
    assert split.income_statement_change()[5] == pytest.approx(
        plain.income_statement_change()[5] - 300.0)
    # Nothing is created: the two lines still add to the same movement.
    assert (split.income_statement_change() + split.oci_change()
            == pytest.approx(plain.income_statement_change()))


def test_a_contract_with_no_fees_cannot_attribute_any():
    fees = np.zeros(6)
    with pytest.raises(ValueError, match="nothing to attribute"):
        MarketRiskBenefit(np.ones(6), fees)


def test_mismatched_mrb_series_are_refused():
    with pytest.raises(ValueError, match="different numbers of dates"):
        MarketRiskBenefit(np.ones(5), np.ones(6))


# --- inputs and the bridge ----------------------------------------------


def test_mismatched_cohort_lengths_are_refused():
    with pytest.raises(ValueError, match="different numbers of periods"):
        Cohort(np.ones(5), np.ones(6))


def test_mismatched_expenses_are_refused():
    with pytest.raises(ValueError, match="expenses"):
        Cohort(np.ones(5), np.ones(5), np.ones(4))


def test_an_unknown_timing_is_refused():
    with pytest.raises(ValueError, match="timing must be"):
        LiabilityForFuturePolicyBenefits(
            rising_cohort(), locked_in=LOCKED, premium_timing="middle")


def test_a_projected_term_assurance_measures_end_to_end():
    """The overlay working as one: the same run RFC-012 measures under
    IFRS 17, measured again under LDTI. Neither framework is in the
    template, and neither re-derives a cashflow."""
    from engine.core.runner import run
    from engine.data.assumptions import Assumptions, MortalityTable
    from engine.data.expenses import ExpenseScale, Expenses
    from engine.data.modelpoints import ModelPoint
    from engine.library.term_life import TermLife

    mortality = MortalityTable(
        {age: min(0.0004 * 1.09 ** (age - 30), 1.0) for age in range(0, 121)}
    )
    assumptions = Assumptions(
        mortality=mortality, lapse=0.05, interest=0.04,
        expenses=Expenses(initial=ExpenseScale(per_policy=180.0),
                          renewal=ExpenseScale(per_policy=30.0)),
    )
    points = [ModelPoint(id=1, age_at_entry=40, term_years=20,
                         sum_assured=150_000.0, annual_premium=520.0,
                         init_pols=1000.0)]
    result = run(TermLife, points, assumptions, 21,
                 outputs=["premiums", "claims", "expenses", "initial_expenses",
                          "pols_if"])

    periods = 20
    cohort = Cohort.from_run(result, premiums=["premiums"], benefits=["claims"],
                             expenses=["expenses"], periods=periods)
    dac = DeferredAcquisitionCosts(
        float(result.aggregate("initial_expenses")[0]),
        np.asarray(result.aggregate("pols_if")[:periods]),
    )
    measured = measure(cohort, locked_in=LOCKED, dac=dac)

    assert isinstance(measured, Measurement)
    assert measured.total_income() == pytest.approx(net_cash(cohort, dac),
                                                    abs=1e-7)
    assert measured.reserve[0] == pytest.approx(0.0, abs=1e-7)
    assert measured.reserve[-1] == pytest.approx(0.0, abs=1e-7)
    assert 0.0 < measured.net_premium_ratio < 1.0
    assert "NPR=" in repr(measured)
