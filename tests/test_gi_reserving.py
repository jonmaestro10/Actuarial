"""Reserve variability: Mack's prediction error, and the bootstrap's.

Execution plan §10, item C5, first half. The chain ladder itself is tested in
tests/test_incurred_claims.py and its point estimates are already checked
against the published Taylor–Ashe results in tests/test_published_sources.py;
neither is retested here. This suite is about the *range*.

Two numbers that are not the same number, which is most of what this file
exists to keep straight:

- **Mack's** is a prediction error — process variance plus estimation
  variance — computed in closed form. It is 13% of the total reserve on the
  Taylor–Ashe triangle, and every accident year's figure is published.
- **The ODP bootstrap's** is an estimation error by default, about 15% on the
  same triangle, and becomes a prediction error of about 16% once the process
  step is asked for. Quoting the 15% against Mack's 13% would be comparing
  two different quantities and finding them reassuringly close.

The reproducibility claim is the one no reserving tool makes: the same seed
gives the same simulations element for element, so a published reserve range
can be recomputed rather than taken on trust.
"""

from __future__ import annotations

import numpy as np
import pytest

from engine.report.incurred_claims import (
    ChainLadder,
    Triangle,
    mack_standard_error,
    odp_bootstrap,
)


@pytest.fixture(scope="module")
def ladder(published):
    return ChainLadder(Triangle(published.rows), method="volume")


@pytest.fixture(scope="module")
def mack(ladder):
    return mack_standard_error(ladder)


# --------------------------------------------------------------------------
# Mack, against the paper
# --------------------------------------------------------------------------

def test_the_standard_errors_are_macks_table_3(mack, published):
    """The whole point of C5's first half: reserve *ranges* checked against a
    published source rather than against a second implementation of the same
    formula.

    Table 3, p. 222, is quoted as a percentage of each accident year's
    reserve, so that is what is compared — nine accident years from the
    second, since the first is fully developed and has no reserve.
    """
    ours = [round(100 * cv) for cv in mack.coefficient_of_variation[1:]]
    assert tuple(ours) == published.standard_error_pct
    assert round(100 * mack.total_coefficient_of_variation) == \
        published.total_standard_error_pct


def test_the_first_accident_year_has_no_reserve_and_no_error(mack):
    """It is fully developed. A standard error attached to a reserve of zero
    would be a number with nothing to be a fraction of, and the coefficient
    of variation says zero rather than dividing."""
    assert mack.reserve[0] == 0.0
    assert mack.by_period[0] == 0.0
    assert mack.coefficient_of_variation[0] == 0.0


def test_the_total_exceeds_the_periods_added_in_quadrature(mack):
    """**The correlation term, and why it is reported.** Every accident
    period is developed with the *same* estimated factors, so their reserves
    are positively correlated and the total's error is larger than the root
    sum of squares.

    Adding in quadrature is the natural thing to do and understates the
    total by 17% here. The module computes the quadrature figure on purpose
    so the gap is reportable rather than a thing somebody has to know."""
    assert mack.quadrature_total < mack.total
    assert mack.total / mack.quadrature_total == pytest.approx(1.20, abs=0.02)


def test_the_last_sigma_is_extrapolated_rather_than_set_to_zero(mack):
    """Mack §3: the final development period has one observation and no
    degrees of freedom, so σ² is extrapolated as
    ``min(σ⁴_{n-3}/σ²_{n-4}, σ²_{n-4}, σ²_{n-3})``.

    Zero is the tempting alternative and it would declare the last factor
    known exactly — at the one development period where the data says
    least. It would also drag the oldest accident years' errors down, which
    is where the reserve is."""
    sigma2 = mack.sigma_squared
    assert sigma2[-1] > 0.0
    n = sigma2.size + 1
    assert sigma2[-1] == pytest.approx(
        min(sigma2[n - 3] ** 2 / sigma2[n - 4], sigma2[n - 4],
            sigma2[n - 3]))
    assert sigma2[-1] <= sigma2[-2]


def test_simple_average_factors_are_refused(published):
    """The derivation is conditional on the volume-weighted estimator. A
    simple-average triangle has different factors, and σ² is estimated
    *around* ``f_j`` — so these formulae do not describe its error, and
    computing them anyway would return a plausible number for a model
    nobody fitted."""
    ladder = ChainLadder(Triangle(published.rows), method="simple")
    with pytest.raises(ValueError, match="volume-weighted"):
        mack_standard_error(ladder)


def test_a_tail_factor_is_refused(published):
    """A tail's own uncertainty is not in the triangle. Mack's formulae stop
    where the data does, and extending them through a tail would attach an
    error bar to an assumption."""
    ladder = ChainLadder(Triangle(published.rows), method="volume", tail=1.05)
    with pytest.raises(ValueError, match="tail"):
        mack_standard_error(ladder)


def test_a_triangle_too_small_for_the_extrapolation_is_refused():
    """The σ² extrapolation needs three earlier development periods. A
    triangle without them cannot supply the last one, and guessing it is the
    thing this refusal exists to avoid."""
    small = Triangle([[100.0, 150.0, 160.0], [120.0, 170.0], [130.0]])
    with pytest.raises(ValueError, match="too few"):
        mack_standard_error(ChainLadder(small))


# --------------------------------------------------------------------------
# The bootstrap, and the range that can be recomputed
# --------------------------------------------------------------------------

def test_the_same_seed_gives_the_same_range_element_for_element(published):
    """**The claim no reserving tool makes.** A reserve range that cannot be
    reproduced is an opinion. Asserted on the simulations themselves rather
    than on their standard deviation, because two different streams can
    agree on a summary statistic and disagree on every draw."""
    triangle = Triangle(published.rows)
    first = odp_bootstrap(triangle, n_samples=200, seed=11)
    again = odp_bootstrap(triangle, n_samples=200, seed=11)
    assert np.array_equal(first.reserves, again.reserves)
    assert first.seed == again.seed == 11

    other = odp_bootstrap(triangle, n_samples=200, seed=12)
    assert not np.array_equal(first.reserves, other.reserves)


def test_the_over_dispersion_is_the_published_scale(published):
    """φ = Σr²/(N − p) on the Taylor–Ashe triangle is 52,601, which is the
    figure the England–Verrall literature quotes for it. It does not depend
    on the seed — it is a property of the fit, not of the resampling — and
    asserting it pins the residual definition and the degrees of freedom
    together."""
    result = odp_bootstrap(Triangle(published.rows), n_samples=50, seed=3)
    assert result.scale == pytest.approx(52_601, rel=5e-4)
    other_seed = odp_bootstrap(Triangle(published.rows), n_samples=50, seed=99)
    assert other_seed.scale == result.scale


def test_estimation_and_prediction_error_are_different_numbers(published):
    """**The distinction the default protects.** Resampling residuals gives
    the estimation error alone — about 15% here. Mack's 13% is a *prediction*
    error. Quoting one against the other would be comparing two different
    quantities and finding them reassuringly close.

    Asking for the process step adds a gamma draw with mean ``R*`` and
    variance ``φR*``, which takes it to about 16% — and that decomposes
    exactly, which is the check that the process step is the one it claims
    to be rather than extra noise of the right size."""
    triangle = Triangle(published.rows)
    estimation = odp_bootstrap(triangle, n_samples=4_000, seed=7)
    prediction = odp_bootstrap(triangle, n_samples=4_000, seed=7,
                               process_variance=True)

    assert estimation.coefficient_of_variation == pytest.approx(0.154,
                                                                abs=0.01)
    assert prediction.coefficient_of_variation == pytest.approx(0.163,
                                                                abs=0.01)
    assert prediction.standard_error > estimation.standard_error

    process = np.sqrt(estimation.scale * estimation.point_estimate)
    combined = np.hypot(estimation.standard_error, process)
    assert combined == pytest.approx(prediction.standard_error, rel=0.02)


def test_the_bootstrap_centres_on_the_chain_ladder_reserve(published):
    """The resampled triangles are built around the fitted one, so their
    reserves have to average out at the point estimate. A bootstrap that
    drifted would be resampling the wrong residuals — which is a mistake
    that shows up in the *mean*, not in the spread, and would leave a
    plausible-looking range around the wrong centre."""
    result = odp_bootstrap(Triangle(published.rows), n_samples=4_000, seed=5)
    assert result.reserves.mean() / result.point_estimate == \
        pytest.approx(1.0, abs=0.03)


def test_the_percentiles_bracket_the_point_estimate(published):
    """What a reserve range is actually asked for. The 75th percentile of a
    right-skewed reserve distribution sits above the point estimate and the
    25th below it, and both are a long way from the mean on a triangle this
    volatile."""
    result = odp_bootstrap(Triangle(published.rows), n_samples=2_000, seed=13)
    low, high = result.percentile([25, 75])
    assert low < result.point_estimate < high
    assert result.percentile(99) > result.percentile(1)


def test_a_bootstrap_with_nothing_to_bootstrap_is_refused(published):
    """One sample has no distribution to describe — the standard error would
    be undefined. And a triangle with no degrees of freedom cannot estimate
    the over-dispersion the resampling scales by."""
    with pytest.raises(ValueError, match="no distribution"):
        odp_bootstrap(Triangle(published.rows), n_samples=1)
    tiny = Triangle([[100.0, 150.0], [120.0]])
    with pytest.raises(ValueError, match="degrees of freedom"):
        odp_bootstrap(tiny, n_samples=10)


def test_the_two_methods_disagree_and_are_not_reconciled(published):
    """Mack's 13% and the bootstrap's 16% are two *models*, not two
    estimates of one number, and nothing here averages them or picks one.

    The gap is worth reporting to an actuary and worth refusing to close in
    code: a reserving tool that quietly reported the smaller of the two —
    or their mean — would be making a modelling choice on the user's behalf
    at exactly the point where the user's judgement is what is wanted."""
    mack_cv = mack_standard_error(
        ChainLadder(Triangle(published.rows))).total_coefficient_of_variation
    bootstrap_cv = odp_bootstrap(Triangle(published.rows), n_samples=4_000,
                                 seed=7, process_variance=True
                                 ).coefficient_of_variation
    assert mack_cv == pytest.approx(0.131, abs=0.005)
    assert bootstrap_cv > mack_cv
    assert bootstrap_cv / mack_cv == pytest.approx(1.24, abs=0.08)
