"""Counterparty default risk — RFC-028.

Transcription against the Official Journal first, then the identities and
the measured findings.
"""

import math

import numpy as np
import pytest

from engine.report.counterparty import (
    COLLATERAL_FACTORS, PROBABILITY_OF_DEFAULT, RESIDUAL_PD, TYPE_CORRELATION,
    band_boundary_jump, counterparty_default,
    derivative_lgd, insurer_probability_of_default, loss_variance,
    probability_of_default, reinsurance_lgd, type_1_capital, type_2_capital,
)


# --------------------------------------------------------------------------
# Transcription
# --------------------------------------------------------------------------

def test_probability_of_default_table_is_the_published_one():
    """Article 199(2)."""
    assert PROBABILITY_OF_DEFAULT == (0.00002, 0.0001, 0.0005, 0.0024, 0.012,
                                      0.042, 0.042)
    assert float(probability_of_default(np.array(3))) == pytest.approx(0.0024)
    # Steps 5 and 6 share a value, as the published table does.
    assert (float(probability_of_default(np.array(5)))
            == float(probability_of_default(np.array(6))) == RESIDUAL_PD)


def test_an_out_of_range_credit_quality_step_is_refused():
    with pytest.raises(ValueError, match="0 to 6"):
        probability_of_default(np.array(7))


def test_the_insurer_solvency_table_is_the_published_one():
    """Article 199(3): eight points, held flat outside 75% to 196%."""
    for ratio, expected in ((1.96, 0.0001), (1.75, 0.0005), (1.50, 0.001),
                            (1.25, 0.002), (1.22, 0.0024), (1.00, 0.005),
                            (0.95, 0.012), (0.75, 0.042)):
        assert float(insurer_probability_of_default(
            np.array(ratio))) == pytest.approx(expected)
    assert float(insurer_probability_of_default(np.array(0.5))) == 0.042
    assert float(insurer_probability_of_default(np.array(3.0))) == 0.0001
    # "linearly interpolated from the closest values"
    midpoint = float(insurer_probability_of_default(np.array(1.235)))
    assert midpoint == pytest.approx(0.5 * (0.002 + 0.0024))


def test_a_failing_or_undisclosed_insurer_takes_the_prescribed_value():
    """Article 199(4) and (5)."""
    assert float(insurer_probability_of_default(
        np.array(1.96), meets_mcr=False)) == pytest.approx(0.042)
    # Before the first solvency and financial condition report, a 100%
    # ratio is assumed — which is 0.5%, the same figure 199(6) and (7) give.
    assert float(insurer_probability_of_default(
        np.array(0.8), disclosed=False)) == pytest.approx(0.005)


def test_a_solvency_ratio_is_a_credit_quality_step():
    """The cross-check RFC-026 made possible.

    Article 199(3) maps an unrated insurer's own solvency ratio to a
    probability of default; Article 186(2), implemented in RFC-026, maps
    the same quantity to the concentration sub-module's risk factor. The
    two tables have different grids — eight points against five — but on
    the five ratios they share, each maps to a credit quality step's
    parameter in **both** sub-modules, exactly.

    So "122% covered" is not a number somebody picked. It is the standard
    formula's definition of a credit quality step 3 counterparty, and it
    says the same thing in both places it appears.
    """
    from engine.report.market_risk import DELEGATED_2015

    concentration = DELEGATED_2015.concentration
    pairs = ((1.96, 1), (1.75, 2), (1.22, 3), (0.95, 4), (0.75, 5))
    for ratio, step in pairs:
        assert float(insurer_probability_of_default(np.array(ratio))) \
            == float(probability_of_default(np.array(step)))
    # And the same five ratios in Article 186(2)'s table.
    factors = dict(zip((0.95, 1.00, 1.22, 1.75, 1.96),
                       (0.73, 0.645, 0.27, 0.21, 0.12)))
    for ratio, step in pairs:
        if ratio in factors:
            assert factors[ratio] == concentration.factor[step]


def test_the_band_edges_and_multipliers_are_the_published_ones():
    """Article 200(1) to (3)."""
    assert band_boundary_jump(1_000.0) == pytest.approx(140.0)
    for pd, lgd, band in ((0.0001, 1_000.0, "3σ"), (0.012, 1_000.0, "5σ"),
                          (0.042, 1_000.0, "ΣLGD")):
        assert type_1_capital([pd], [lgd]).band == band


def test_type_2_factors_are_the_published_ones():
    """Article 202: 90% on receivables overdue more than three months, 15%
    on everything else."""
    assert type_2_capital([1_000.0]) == pytest.approx(150.0)
    assert type_2_capital([], 1_000.0) == pytest.approx(900.0)


def test_the_reinsurance_and_derivative_shares_are_the_published_ones():
    """Article 192(2) and (3) to (3c)."""
    assert float(reinsurance_lgd(1_000.0, 0.0)) == pytest.approx(500.0)
    assert float(reinsurance_lgd(1_000.0, 200.0)) == pytest.approx(
        0.5 * (1_000.0 + 100.0))
    assert float(reinsurance_lgd(
        1_000.0, 0.0, heavily_collateralised=True)) == pytest.approx(900.0)
    for share in (0.18, 0.16, 0.90):
        assert float(derivative_lgd(1_000.0, 0.0, share=share)) \
            == pytest.approx(share * 1_000.0)
    assert COLLATERAL_FACTORS == {"F": 0.50, "F1": 0.18, "F2": 0.16,
                                  "F3": 0.90}


def test_a_heavily_collateralised_reinsurer_is_treated_as_worse():
    """Article 192(2), second subparagraph. Where 60% or more of the
    counterparty's assets are subject to collateral arrangements the share
    goes from 50% to **90%** — because the collateral its *other* cedants
    hold is exactly what is not available to this one."""
    plain = float(reinsurance_lgd(1_000.0, 0.0))
    collateralised = float(reinsurance_lgd(1_000.0, 0.0,
                                           heavily_collateralised=True))
    assert collateralised > plain
    assert collateralised / plain == pytest.approx(1.8)


def test_collateral_can_only_reduce_the_loss_to_zero():
    """The ``max(..., 0)`` in every one of Article 192's formulas."""
    assert float(reinsurance_lgd(100.0, 0.0, 10_000.0)) == 0.0
    assert float(derivative_lgd(100.0, 0.0, 10_000.0)) == 0.0


# --------------------------------------------------------------------------
# The variance
# --------------------------------------------------------------------------

def test_the_variance_splits_into_a_scale_term_and_a_concentration_term():
    """Article 201. ``V_inter`` depends only on the total loss-given-default
    at each probability of default, so spreading a book over more names
    does not move it at all; ``V_intra`` is ``Σ LGD²`` and collapses.

    On a thousand of credit quality step 3 exposure the concentration term
    is 60% of the variance held against one name and 2.9% held against
    fifty — with ``V_inter`` identical to the last digit in both.
    """
    measured = {}
    for n in (1, 5, 50):
        measured[n] = loss_variance(np.full(n, 0.0024), np.full(n, 1_000.0 / n))
    assert measured[1][0] == measured[5][0] == measured[50][0]
    shares = [intra / (inter + intra) for inter, intra in measured.values()]
    assert shares[0] == pytest.approx(0.6006, abs=5e-4)
    assert shares[-1] == pytest.approx(0.0292, abs=5e-4)
    assert shares == sorted(shares, reverse=True)


def test_a_zero_probability_counterparty_carries_no_variance():
    """Article 199(8) gives 0% to the counterparties in Article 180(2)(a) to
    (d) — central banks and the like. The coefficient in Article 201(2) is
    ``0/0`` there, which is a removable singularity and not an error."""
    with_zero = loss_variance([0.0, 0.0024], [500.0, 500.0])
    without = loss_variance([0.0024], [500.0])
    assert with_zero == without
    assert all(math.isfinite(v) for v in with_zero)


def test_mismatched_inputs_and_impossible_probabilities_are_refused():
    with pytest.raises(ValueError, match="losses-given-default"):
        loss_variance([0.01, 0.02], [100.0])
    with pytest.raises(ValueError, match=r"\[0, 1\]"):
        loss_variance([1.5], [100.0])


def test_an_empty_book_has_no_variance_and_no_capital():
    assert loss_variance([], []) == (0.0, 0.0)
    result = type_1_capital([], [])
    assert result.capital == 0.0
    assert result.band == "empty"
    assert result.ratio == 0.0


# --------------------------------------------------------------------------
# The finding: Article 200's lower boundary is a cliff
# --------------------------------------------------------------------------

def test_the_lower_band_boundary_is_discontinuous_and_the_upper_one_is_not():
    """The upper boundary is continuous by construction — ``5 × 20% = 100%``
    of the total loss-given-default, which is exactly what the third band
    gives. The lower one is not: ``3 × 7% = 21%`` against ``5 × 7% = 35%``,
    so an arbitrarily small change in the portfolio moves the requirement
    by **14 percentage points of ΣLGD**, a 66.7% increase.

    RFC-026 reported a 10 basis point discontinuity in Article 176(3)'s
    spread table as a defect worth naming. This one is 140 times larger and
    it is load-bearing.
    """
    assert 5.0 * 0.20 == pytest.approx(1.0)
    assert band_boundary_jump(1_000.0) == pytest.approx(140.0)
    assert (5.0 * 0.07) / (3.0 * 0.07) - 1.0 == pytest.approx(0.6667,
                                                              abs=5e-5)


def test_one_more_counterparty_cuts_the_requirement_by_forty_percent():
    """Walked across the cliff on a real book.

    Thirty-seven equal credit quality step 4 counterparties sharing a
    thousand of loss-given-default put σ at 7.0009% of it — just inside the
    5σ band, capital **350.05**. Add a thirty-eighth, identical in every
    way, and σ falls to 6.9973%: the 3σ band, capital **209.92**.

    A change of 0.0036 percentage points in the standard deviation, on the
    same total exposure to the same quality of counterparty, removes
    **40.03%** of the requirement.
    """
    def book(n):
        return type_1_capital(np.full(n, 0.012), np.full(n, 1_000.0 / n))

    inside, outside = book(37), book(38)
    assert inside.band == "5σ"
    assert outside.band == "3σ"
    assert inside.ratio == pytest.approx(0.0700091, abs=5e-7)
    assert outside.ratio == pytest.approx(0.0699728, abs=5e-7)
    assert inside.capital == pytest.approx(350.046, abs=5e-3)
    assert outside.capital == pytest.approx(209.918, abs=5e-3)
    assert 1.0 - outside.capital / inside.capital == pytest.approx(0.4003,
                                                                   abs=5e-4)
    # The total exposure is identical, and so is the credit quality.
    assert inside.total_lgd == pytest.approx(outside.total_lgd)


def test_a_single_unrated_counterparty_clears_the_upper_band_by_a_hair():
    """A book consisting of one 4.2% counterparty has σ at **20.0589%** of
    its loss-given-default — over Article 200(3)'s twenty per cent boundary
    by six hundredths of a percentage point, so the capital is the whole
    exposure.

    The third band is, on these parameters, calibrated to catch exactly
    that case, and it does so by a margin thinner than any rounding in the
    published tables.
    """
    single = type_1_capital([0.042], [1_000.0])
    assert single.ratio == pytest.approx(0.2005891, abs=5e-7)
    assert single.ratio - 0.20 == pytest.approx(0.00058913, abs=5e-8)
    assert single.band == "ΣLGD"
    assert single.capital == pytest.approx(1_000.0)
    # Split it in two and the same exposure drops into the 5σ band.
    split = type_1_capital([0.042, 0.042], [500.0, 500.0])
    assert split.band == "5σ"
    assert split.capital == pytest.approx(836.046, abs=5e-3)
    assert 1.0 - split.capital / single.capital == pytest.approx(0.1640,
                                                                 abs=5e-4)


def test_capital_never_exceeds_the_total_loss_given_default():
    """The third band's purpose: whatever the concentration, an undertaking
    cannot be required to hold more than everything it could lose."""
    for pd in (0.0001, 0.0024, 0.012, 0.042):
        for n in (1, 2, 7, 30):
            result = type_1_capital(np.full(n, pd), np.full(n, 500.0))
            assert result.capital <= result.total_lgd + 1e-9


# --------------------------------------------------------------------------
# The finding: Article 202's other cliff
# --------------------------------------------------------------------------

def test_a_receivable_crossing_three_months_costs_six_times_as_much():
    """Article 202. The same money owed by the same intermediary, one day
    later: 15% becomes 90%, with no transition of any kind."""
    before = type_2_capital([100.0])
    after = type_2_capital([], 100.0)
    assert before == pytest.approx(15.0)
    assert after == pytest.approx(90.0)
    assert after / before == pytest.approx(6.0)


# --------------------------------------------------------------------------
# Article 189: the aggregation
# --------------------------------------------------------------------------

def test_the_aggregation_coefficient_is_twice_a_correlation_of_three_quarters():
    """Article 189(1) writes ``1.5 · SCR_def,1 · SCR_def,2`` with the 2 of
    ``2ρ`` already multiplied in. A reader who takes 1.5 for the
    correlation gets an aggregate **above the undiversified sum**, which is
    what the bound in :meth:`CounterpartyDefault.reconciles` exists to
    catch."""
    assert TYPE_CORRELATION == 0.75
    position = counterparty_default([0.042], [1_000.0],
                                    type_2_losses=[2_000.0])
    assert position.type_1.capital == pytest.approx(1_000.0)
    assert position.type_2 == pytest.approx(300.0)
    assert position.capital == pytest.approx(1_240.97, abs=5e-3)
    assert position.undiversified == pytest.approx(1_300.0)
    assert position.reconciles()
    misread = math.sqrt(1_000.0 ** 2 + 2 * 1.5 * 1_000.0 * 300.0 + 300.0 ** 2)
    assert misread > position.undiversified
    assert position.capital < misread


def test_the_aggregate_lies_between_the_larger_leg_and_the_sum():
    for type_2 in (0.0, 50.0, 300.0, 5_000.0):
        position = counterparty_default([0.0024], [1_000.0],
                                        type_2_losses=[type_2 / 0.15])
        assert position.reconciles()
        assert (max(position.type_1.capital, position.type_2)
                <= position.capital <= position.undiversified + 1e-9)


def test_a_book_with_only_one_exposure_class_aggregates_to_itself():
    only_type_1 = counterparty_default([0.0024], [1_000.0])
    assert only_type_1.type_2 == 0.0
    assert only_type_1.capital == pytest.approx(only_type_1.type_1.capital)
    only_type_2 = counterparty_default([], [], type_2_losses=[1_000.0])
    assert only_type_2.type_1.capital == 0.0
    assert only_type_2.capital == pytest.approx(150.0)


def test_the_result_says_which_band_produced_it():
    position = counterparty_default([0.042], [1_000.0],
                                    type_2_losses=[2_000.0])
    assert "ΣLGD" in repr(position.type_1)
    assert "SCR=" in repr(position)
