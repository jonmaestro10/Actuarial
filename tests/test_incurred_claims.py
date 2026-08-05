"""IFRS 17's liability for incurred claims, and the triangle behind it."""

import numpy as np
import pytest

from engine.data.rates import YieldCurve
from engine.report.ifrs17 import RiskAdjustment
from engine.report.incurred_claims import (
    ChainLadder,
    FACTOR_METHODS,
    Triangle,
    combined,
    development_factors,
    expedient_error,
    measure_lic,
)


CURVE = YieldCurve.flat(0.04, freq=1)
SHORT = np.array([0.70, 0.90, 0.97, 0.99, 0.995, 1.00])
LONG = np.array([0.15, 0.35, 0.55, 0.72, 0.88, 1.00])
ULTIMATES = np.array([1000.0, 1100.0, 1210.0, 1331.0, 1464.0, 1610.0])


def triangle(ultimates=ULTIMATES, pattern=SHORT):
    return Triangle.from_pattern(ultimates, pattern)


# --- the triangle -----------------------------------------------------------

def test_a_ragged_list_of_rows_becomes_a_square_with_a_hole():
    tri = Triangle([[100.0, 150.0, 170.0], [110.0, 165.0], [120.0]])
    assert tri.n_periods == 3
    assert tri.cumulative[0, 2] == 170.0
    assert np.isnan(tri.cumulative[2, 1])


def test_rows_must_be_the_lengths_a_triangle_has():
    with pytest.raises(ValueError, match="n − i entries"):
        Triangle([[100.0, 150.0], [110.0, 165.0]])


def test_a_hole_inside_the_triangle_is_a_data_problem():
    """Not a shape to guess at: an unobserved cell in the observed corner
    means something is missing, and filling it silently is worse."""
    data = np.full((3, 3), np.nan)
    data[0, 0], data[0, 2], data[1, 0], data[1, 1], data[2, 0] = (
        100.0, 170.0, 110.0, 165.0, 120.0
    )
    with pytest.raises(ValueError, match="upper-left triangle"):
        Triangle(data)


def test_a_triangle_must_be_square():
    with pytest.raises(ValueError, match="is square"):
        Triangle(np.full((3, 4), 1.0))


def test_one_accident_period_is_not_a_triangle():
    with pytest.raises(ValueError, match="at least two"):
        Triangle([[100.0]])


def test_cumulative_paid_cannot_go_backwards_into_negative():
    with pytest.raises(ValueError, match="cannot be negative"):
        Triangle([[100.0, -50.0], [110.0]])


def test_incremental_input_is_accumulated():
    incremental = Triangle([[100.0, 50.0, 20.0], [110.0, 55.0], [120.0]],
                           cumulative=False)
    assert incremental.cumulative[0].tolist() == [100.0, 150.0, 170.0]


def test_incremental_output_inverts_the_accumulation():
    tri = triangle()
    rebuilt = np.nancumsum(np.nan_to_num(tri.incremental()), axis=1)
    observed = ~np.isnan(tri.cumulative)
    assert np.allclose(rebuilt[observed], tri.cumulative[observed])


def test_the_latest_diagonal_is_what_has_been_paid_so_far():
    tri = triangle()
    assert tri.latest()[0] == pytest.approx(ULTIMATES[0] * SHORT[5])
    assert tri.latest()[-1] == pytest.approx(ULTIMATES[-1] * SHORT[0])
    assert tri.paid_to_date() == pytest.approx(tri.latest().sum())


def test_from_pattern_validates_the_pattern():
    with pytest.raises(ValueError, match="needs 3 development periods"):
        Triangle.from_pattern([1.0, 2.0, 3.0], [0.5, 1.0])
    with pytest.raises(ValueError, match="cannot go down"):
        Triangle.from_pattern([1.0, 2.0], [0.6, 0.5])
    with pytest.raises(ValueError, match="pays nothing"):
        Triangle.from_pattern([1.0, 2.0], [0.0, 0.0])


# --- the chain ladder inverts its own generating process --------------------

def test_the_factors_are_the_generating_pattern_ratios():
    assert np.allclose(development_factors(triangle()), SHORT[1:] / SHORT[:-1],
                       rtol=1e-13)


def test_it_recovers_the_ultimates_exactly():
    """The golden test. An estimator that cannot invert the process that
    generated its data is not estimating anything."""
    ladder = ChainLadder(triangle())
    assert np.allclose(ladder.ultimates, ULTIMATES, rtol=1e-12)


@pytest.mark.parametrize("pattern", [SHORT, LONG])
def test_it_recovers_them_on_any_pattern(pattern):
    ladder = ChainLadder(Triangle.from_pattern(ULTIMATES, pattern))
    assert np.allclose(ladder.ultimates, ULTIMATES, rtol=1e-12)


@pytest.mark.parametrize("method", FACTOR_METHODS)
def test_both_factor_methods_agree_on_a_single_pattern_triangle(method):
    """They can only disagree about a mixture, because on one pattern every
    individual ratio is the same number."""
    assert np.allclose(development_factors(triangle(), method),
                       SHORT[1:] / SHORT[:-1], rtol=1e-13)


def test_the_reserve_is_the_ultimate_less_what_has_been_paid():
    ladder = ChainLadder(triangle())
    assert np.allclose(ladder.reserve, ULTIMATES - triangle().latest(),
                       rtol=1e-12)
    assert ladder.reserve[0] == pytest.approx(0.0, abs=1e-9)


def test_the_future_payments_add_up_to_the_reserve():
    """The reduction onto calendar diagonals cannot invent or lose a claim."""
    ladder = ChainLadder(triangle())
    assert ladder.future_payments().sum() == pytest.approx(
        ladder.total_reserve, rel=1e-12
    )


def test_development_runs_diagonally_across_calendar_time():
    """Accident period i's development period j is paid in calendar period
    i + j, so a reserve cannot be discounted off the triangle's columns."""
    ladder = ChainLadder(triangle())
    payments = ladder.future_payments()
    assert payments[0] > payments[1] > payments[2]
    assert payments[-1] == pytest.approx(0.0, abs=1e-9)


def test_a_tail_factor_lifts_every_ultimate_proportionately():
    plain = ChainLadder(triangle())
    tailed = ChainLadder(triangle(), tail=1.05)
    assert np.allclose(tailed.ultimates, plain.ultimates * 1.05, rtol=1e-12)
    assert tailed.future_payments().sum() == pytest.approx(
        tailed.total_reserve, rel=1e-12
    )


def test_a_tail_below_one_is_refused():
    with pytest.raises(ValueError, match="below 1"):
        ChainLadder(triangle(), tail=0.98)


def test_an_unknown_factor_method_is_refused():
    with pytest.raises(ValueError, match="factor method"):
        development_factors(triangle(), "judgement")


def test_a_factor_off_a_zero_base_is_refused():
    with pytest.raises(ValueError, match="not a factor"):
        development_factors(Triangle([[0.0, 5.0], [0.0]]))


# --- the finding: additive only when the mix holds still ---------------------

def _segments(short_ultimates, long_ultimates):
    return (Triangle.from_pattern(np.asarray(short_ultimates, float), SHORT),
            Triangle.from_pattern(np.asarray(long_ultimates, float), LONG))


def _gap(short_ultimates, long_ultimates):
    first, second = _segments(short_ultimates, long_ultimates)
    parts = ChainLadder(first).total_reserve + ChainLadder(second).total_reserve
    whole = ChainLadder(combined(first, second)).total_reserve
    return whole / parts - 1.0


def test_the_chain_ladder_is_additive_when_the_mix_holds_still():
    """Two patterns blended in a constant proportion *are* a third pattern,
    so the combined triangle is chain-ladder consistent and the reserve for
    the whole is exactly the sum of the parts."""
    gap = _gap([2000.0, 2100.0, 2200.0, 2300.0, 2400.0, 2500.0],
               [800.0, 840.0, 880.0, 920.0, 960.0, 1000.0])
    assert gap == pytest.approx(0.0, abs=1e-12)


def test_it_stops_being_additive_the_moment_the_mix_moves():
    """Measured, after the constant-mix case showed no gap at all — which
    is what located the condition."""
    shifting = _gap([2500.0, 2400.0, 2300.0, 2200.0, 2100.0, 2000.0],
                    [400.0, 560.0, 720.0, 880.0, 1040.0, 1200.0])
    assert shifting == pytest.approx(-0.3402, abs=1e-3)


def test_a_growing_long_tail_book_makes_the_combined_reserve_understate():
    """The factors are volume-weighted towards older, shorter-tailed
    accident periods, so they are too small for the newer business."""
    gap = _gap([2500.0] * 6, [250.0, 500.0, 750.0, 1000.0, 1250.0, 1500.0])
    assert gap == pytest.approx(-0.3971, abs=1e-3)


def test_a_growing_short_tail_book_makes_it_overstate():
    """The same mechanism with the mix moving the other way — and the sign
    flips, which is what makes this a segmentation question rather than a
    prudence one."""
    gap = _gap([500.0, 900.0, 1300.0, 1700.0, 2100.0, 2500.0], [1500.0] * 6)
    assert gap == pytest.approx(0.5989, abs=1e-3)


def test_combining_needs_a_common_shape():
    with pytest.raises(ValueError, match="share a shape"):
        combined(triangle(), Triangle([[1.0, 2.0], [3.0]]))


def test_combining_nothing_is_refused():
    with pytest.raises(ValueError, match="nothing to combine"):
        combined()


def test_the_two_factor_methods_disagree_on_a_mixed_triangle():
    """One accident period developing on a different pattern, and a third of
    the reserve between the two averages."""
    rows = [list(row[~np.isnan(row)]) for row in triangle().cumulative]
    rows[0] = list(ULTIMATES[0] * LONG)
    mixed = Triangle(rows)
    volume = ChainLadder(mixed, method="volume").total_reserve
    simple = ChainLadder(mixed, method="simple").total_reserve
    assert simple / volume - 1.0 == pytest.approx(0.3739, abs=1e-3)


# --- the liability ----------------------------------------------------------

def payments(pattern=SHORT):
    return ChainLadder(Triangle.from_pattern(ULTIMATES, pattern)
                       ).future_payments()


def test_the_liability_is_the_present_value_plus_the_risk_adjustment():
    flows = payments()
    margin = RiskAdjustment.percent_of(flows, 0.06)
    measured = measure_lic(flows, curve=CURVE, risk_adjustment=margin)
    factors = CURVE.discount_factors(flows.size + 1)[1:]
    assert measured.liability[0] == pytest.approx(
        (flows * factors).sum() + margin.total, rel=1e-12
    )


def test_it_runs_off_to_nothing():
    measured = measure_lic(payments(), curve=CURVE,
                           risk_adjustment=RiskAdjustment.percent_of(
                               payments(), 0.06))
    assert measured.liability[-1] == pytest.approx(0.0, abs=1e-9)


def test_the_opening_balance_is_not_an_expense_of_the_run_off():
    """It was recognised as the claims were incurred. An invariant written
    without that term is wrong by the whole liability."""
    flows = payments()
    measured = measure_lic(flows, curve=CURVE,
                           risk_adjustment=RiskAdjustment.percent_of(flows, 0.06))
    assert measured.reconciles()
    assert measured.residual() == pytest.approx(0.0, abs=1e-9)
    # Dropping the term does not make the invariant approximately right; it
    # makes it wrong by the opening liability, exactly.
    without_opening = measured.total_expense - flows.sum()
    assert without_opening == pytest.approx(-measured.liability[0], rel=1e-12)


@pytest.mark.parametrize("discounted", [True, False])
@pytest.mark.parametrize("timing", ["start", "end"])
@pytest.mark.parametrize("margin", [0.0, 0.06])
def test_the_invariant_holds_in_every_configuration(discounted, timing, margin):
    flows = payments()
    adjustment = (RiskAdjustment.percent_of(flows, margin) if margin
                  else None)
    measured = measure_lic(flows, curve=CURVE, risk_adjustment=adjustment,
                           discounted=discounted, timing=timing)
    assert measured.reconciles()


def test_not_discounting_recognises_no_finance_expense():
    flows = payments()
    measured = measure_lic(flows, curve=CURVE, discounted=False)
    assert np.all(measured.finance_expense == 0.0)
    assert measured.liability[0] == pytest.approx(flows.sum(), rel=1e-12)


def test_discounting_lowers_the_liability_and_charges_the_difference_back():
    flows = payments()
    discounted = measure_lic(flows, curve=CURVE)
    undiscounted = measure_lic(flows, curve=CURVE, discounted=False)
    assert discounted.liability[0] < undiscounted.liability[0]
    assert discounted.finance_expense.sum() == pytest.approx(
        undiscounted.liability[0] - discounted.liability[0], rel=1e-9
    )


def test_the_unwind_is_never_a_credit():
    measured = measure_lic(payments(), curve=CURVE)
    assert np.all(measured.finance_expense >= -1e-12)


def test_the_risk_adjustment_release_is_a_credit_to_expense():
    flows = payments()
    measured = measure_lic(flows, curve=CURVE,
                           risk_adjustment=RiskAdjustment.percent_of(flows, 0.06))
    assert np.all(measured.expense <= 1e-12)
    assert measured.expense.sum() == pytest.approx(
        -0.06 * flows.sum(), rel=1e-9
    )


def test_paying_earlier_costs_less_to_hold():
    early = measure_lic(payments(SHORT), curve=CURVE)
    late = measure_lic(payments(LONG), curve=CURVE)
    assert (early.finance_expense.sum() / payments(SHORT).sum()
            < late.finance_expense.sum() / payments(LONG).sum())


@pytest.mark.parametrize("flows, message", [
    ([], "not a liability"),
    ([100.0, -20.0], "recovery"),
])
def test_the_measurement_validates_its_payments(flows, message):
    with pytest.raises(ValueError, match=message):
        measure_lic(flows, curve=CURVE)


def test_an_unknown_timing_is_refused():
    with pytest.raises(ValueError, match="timing must be"):
        measure_lic([100.0], curve=CURVE, timing="midpoint")


# --- what §59(b) is worth ---------------------------------------------------

def test_at_the_boundary_the_expedient_costs_exactly_one_year_of_interest():
    """Which is why the standard draws it at claims paid within a year: a
    single payment one year out is overstated by exactly the curve rate."""
    assert expedient_error([100.0], CURVE) == pytest.approx(0.04, rel=1e-12)


def test_the_error_grows_with_the_tail():
    measured = {}
    for label, pattern in (("short", SHORT), ("long", LONG)):
        flows = payments(pattern)
        mean_term = float((flows * np.arange(1, flows.size + 1)).sum()
                          / flows.sum())
        measured[label] = (mean_term, expedient_error(flows, CURVE))
    assert measured["short"] == pytest.approx((1.474, 0.0590), abs=1e-3)
    assert measured["long"] == pytest.approx((2.279, 0.0923), abs=1e-3)


def test_the_error_is_about_the_curve_compounded_over_the_mean_term():
    flows = payments(LONG)
    mean_term = float((flows * np.arange(1, flows.size + 1)).sum()
                      / flows.sum())
    # Close but not equal: a spread of payments is not a single one at the
    # mean term, and the difference is the convexity of the discount.
    assert expedient_error(flows, CURVE) == pytest.approx(
        1.04 ** mean_term - 1.0, abs=2e-3
    )
    assert expedient_error(flows, CURVE) < 1.04 ** mean_term - 1.0


def test_start_timing_discounts_one_period_less():
    flows = payments()
    assert expedient_error(flows, CURVE, timing="start") < expedient_error(
        flows, CURVE
    )


def test_a_liability_of_nothing_has_no_discounting_error():
    with pytest.raises(ValueError, match="no discounting error"):
        expedient_error([0.0, 0.0], CURVE)
