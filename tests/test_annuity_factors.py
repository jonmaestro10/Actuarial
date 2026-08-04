"""Annuity factors: against VPLA, against closed forms, and against exact.

Two of the factors in engine/library/annuities.py are reorganised rather
than transcribed — the reversionary factor uses the closed form instead of
VPLA's O(n²) loop, and sums are pairwise instead of left-to-right. Both
change the last bits, so "bitwise identical" is not the standard here and
pretending otherwise would be the wrong test.

The standard used instead is anchored on ``math.fsum`` of the same terms,
which is the correctly rounded value. Each factor must land within a couple
of units in the last place of it, and — across a block of lives — the
engine's worst and average error must be no larger than VPLA's. The claim
is deliberately aggregate: pairwise summation has the better error *bound*,
not a guarantee of winning every individual case, and asserting the latter
would be asserting something untrue.
"""

import math
from datetime import date

import numpy as np
import pytest

from engine.data.mortality import MortalityBasis
from engine.data.rates import YieldCurve
from engine.library.annuities import (
    annuity_factor,
    block_annuity_factors,
    certain_periods,
    deferred_annuity_values,
    joint_life_factor,
    reversionary_annuity_factor,
)
from vpla_reference import (
    ReferenceMortalityTable,
    annuity_factor as reference_annuity_factor,
    deferred_annuity_values as reference_deferred,
    discount_factors as reference_discount,
    joint_annuity_factor as reference_joint,
    joint_life_factor as reference_joint_life,
)

MIN_AGE, MAX_AGE = 18, 115
YEAR_START = 2014
INTEREST = 0.03
VALUATION = date(2021, 1, 1)

RATES = {
    sex: {
        age: min(0.0004 * 1.09 ** (age - MIN_AGE) * (1.0 if sex == "M" else 0.85), 1.0)
        for age in range(MIN_AGE, MAX_AGE + 1)
    }
    for sex in ("M", "F")
}
SCALE = {
    sex: {age: 0.010 if sex == "M" else 0.012 for age in range(MIN_AGE, MAX_AGE + 1)}
    for sex in ("M", "F")
}

BASIS_KWARGS = dict(year_start=YEAR_START, improvement=SCALE, calc="udd")
REFERENCE = ReferenceMortalityTable(RATES, **BASIS_KWARGS)
BASIS = MortalityBasis(RATES, **BASIS_KWARGS)

LIVES = [
    (date(1956, 1, 1), "M"),
    (date(1946, 6, 30), "F"),
    (date(1960, 2, 29), "M"),
    (date(1938, 12, 15), "F"),
]


def setup(freq, n_periods=None):
    curve = YieldCurve([INTEREST], freq=freq)
    n = n_periods or 60 * freq
    return curve, curve.discount_factors(n), n


def survival(dob, sex, freq, n):
    return BASIS.survival_curve([dob], [VALUATION], [sex], freq, n)[0]


def reference_survival(dob, sex, freq, n):
    return REFERENCE.survival_factors(dob, VALUATION, sex, freq, n)


def error_in_ulp(value, terms):
    """Distance from the correctly rounded sum of ``terms``, in units in the
    last place — the only scale on which a summation order is worth
    comparing."""
    exact = math.fsum(terms)
    return abs(value - exact) / np.spacing(exact)


# --- against VPLA ----------------------------------------------------------


@pytest.mark.parametrize("freq", [1, 4, 12])
@pytest.mark.parametrize("dob,sex", LIVES)
def test_single_life_factor_matches_vpla(freq, dob, sex):
    curve, discount, n = setup(freq)
    sf = survival(dob, sex, freq, n)
    reference_sf = reference_survival(dob, sex, freq, n)
    assert list(sf) == reference_sf  # the inputs are identical to the last bit

    got = float(annuity_factor(discount, sf, freq))
    want = reference_annuity_factor(list(discount), reference_sf, freq)
    assert got == pytest.approx(want, rel=1e-14)

    terms = [d * s / freq for d, s in zip(discount, sf)]
    assert error_in_ulp(got, terms) <= 4.0


@pytest.mark.parametrize("freq", [1, 12])
@pytest.mark.parametrize("certain_years", [0, 5, 10, 20])
def test_life_and_certain_matches_vpla(freq, certain_years):
    curve, discount, n = setup(freq)
    dob, sex = LIVES[0]
    guaranteed = certain_periods(certain_years, freq)
    assert guaranteed == certain_years * freq

    got = float(annuity_factor(discount, survival(dob, sex, freq, n), freq, guaranteed))
    want = reference_annuity_factor(
        list(discount), reference_survival(dob, sex, freq, n), freq, guaranteed
    )
    assert got == pytest.approx(want, rel=1e-14)


@pytest.mark.parametrize("freq", [1, 12])
def test_deferred_values_match_vplas_quadratic_loop(freq):
    curve, discount, n = setup(freq, 20 * freq)
    dob, sex = LIVES[0]
    got = deferred_annuity_values(discount, survival(dob, sex, freq, n))
    want = reference_deferred(list(discount), reference_survival(dob, sex, freq, n))
    assert got == pytest.approx(want, rel=1e-13)


@pytest.mark.parametrize("freq", [1, 12])
def test_joint_life_factor_matches_vpla(freq):
    curve, discount, n = setup(freq)
    (dob_x, sex_x), (dob_y, sex_y) = LIVES[0], LIVES[1]
    got = float(
        joint_life_factor(
            discount, survival(dob_x, sex_x, freq, n), survival(dob_y, sex_y, freq, n)
        )
    )
    want = reference_joint_life(
        list(discount),
        reference_survival(dob_x, sex_x, freq, n),
        reference_survival(dob_y, sex_y, freq, n),
    )
    assert got == pytest.approx(want, rel=1e-14)


@pytest.mark.parametrize("freq", [1, 4, 12])
@pytest.mark.parametrize("joint_percent", [0.0, 0.5, 0.6, 1.0])
def test_reversionary_closed_form_matches_vplas_double_loop(freq, joint_percent):
    """The headline reorganisation: O(n) closed form against VPLA's O(n²)
    accumulation. At monthly frequency that is ~1,440 operations against
    ~1,036,800, and the closed form removes roundings rather than adding
    them, so it must stay within a couple of ulp of exact."""
    curve, discount, n = setup(freq)
    (dob_x, sex_x), (dob_y, sex_y) = LIVES[0], LIVES[1]
    sf_x = survival(dob_x, sex_x, freq, n)
    sf_y = survival(dob_y, sex_y, freq, n)
    q_x = BASIS.period_mortality([dob_x], [VALUATION], [sex_x], freq, n)[0]

    got = float(
        reversionary_annuity_factor(discount, sf_x, sf_y, joint_percent, freq)
    )
    want = reference_joint(
        list(discount),
        reference_survival(dob_x, sex_x, freq, n),
        list(q_x),
        reference_survival(dob_y, sex_y, freq, n),
        joint_percent,
        freq,
    )
    assert got == pytest.approx(want, rel=1e-13)

    exact_terms = [
        (d * sx + joint_percent * d * sy * (1.0 - sx)) / freq
        for d, sx, sy in zip(discount, sf_x, sf_y)
    ]
    assert error_in_ulp(got, exact_terms) <= 4.0


def test_zero_joint_percent_is_exactly_the_single_life_factor():
    curve, discount, n = setup(12)
    dob, sex = LIVES[0]
    sf = survival(dob, sex, 12, n)
    other = survival(*LIVES[1], 12, n)
    assert float(
        reversionary_annuity_factor(discount, sf, other, 0.0, 12)
    ) == pytest.approx(float(annuity_factor(discount, sf, 12)), rel=1e-14)


# --- closed forms ----------------------------------------------------------


def test_annuity_certain_when_nobody_dies():
    """With zero mortality the factor collapses to ``ä_n| = (1 - v^n)/d``
    at the payment frequency."""
    freq = 12
    curve = YieldCurve([INTEREST], freq=freq)
    n = 30 * freq
    discount = curve.discount_factors(n)
    got = float(annuity_factor(discount, np.ones(n), freq))
    v = (1 + INTEREST) ** (-1 / freq)
    want = (1 - v**n) / (1 - v) / freq
    assert got == pytest.approx(want, rel=1e-12)


def test_a_guarantee_covering_the_horizon_is_an_annuity_certain():
    freq = 4
    curve, discount, n = setup(freq, 25 * freq)
    dob, sex = LIVES[3]
    everything = annuity_factor(discount, survival(dob, sex, freq, n), freq, n)
    certain = annuity_factor(discount, np.ones(n), freq)
    assert float(everything) == pytest.approx(float(certain), rel=1e-14)


def test_joint_life_is_never_worth_more_than_either_single_life():
    freq = 12
    curve, discount, n = setup(freq)
    sf_x, sf_y = survival(*LIVES[0], freq, n), survival(*LIVES[1], freq, n)
    both = float(joint_life_factor(discount, sf_x, sf_y)) / freq
    assert both <= float(annuity_factor(discount, sf_x, freq))
    assert both <= float(annuity_factor(discount, sf_y, freq))


def test_reversionary_factor_rises_with_the_survivor_percentage():
    freq = 12
    curve, discount, n = setup(freq)
    sf_x, sf_y = survival(*LIVES[0], freq, n), survival(*LIVES[1], freq, n)
    factors = [
        float(reversionary_annuity_factor(discount, sf_x, sf_y, j, freq))
        for j in (0.0, 0.25, 0.5, 0.75, 1.0)
    ]
    assert factors == sorted(factors)
    # At 100% the benefit is a last-survivor annuity: ä_x + ä_y - ä_xy.
    last_survivor = (
        float(annuity_factor(discount, sf_x, freq))
        + float(annuity_factor(discount, sf_y, freq))
        - float(joint_life_factor(discount, sf_x, sf_y)) / freq
    )
    assert factors[-1] == pytest.approx(last_survivor, rel=1e-13)


def test_a_higher_interest_rate_lowers_every_factor():
    freq, n = 12, 720
    dob, sex = LIVES[0]
    sf = survival(dob, sex, freq, n)
    factors = [
        float(annuity_factor(YieldCurve([i], freq=freq).discount_factors(n), sf, freq))
        for i in (0.0, 0.02, 0.04, 0.08)
    ]
    assert factors == sorted(factors, reverse=True)


# --- whole blocks ----------------------------------------------------------


def test_block_factors_match_life_by_life():
    """The vectorized replacement for VPLA's ``iterrows()`` loop over a
    member table, including a mix of single and joint members."""
    freq, n = 12, 720
    curve = YieldCurve([INTEREST], freq=freq)
    discount = curve.discount_factors(n)
    dobs = [d for d, _ in LIVES]
    sexes = [s for _, s in LIVES]
    spouse_dobs = [date(1958, 4, 3), date(1949, 9, 9), date(1962, 1, 1),
                   date(1940, 5, 5)]
    spouse_sexes = ["F", "M", "F", "M"]
    joint = [0.0, 0.6, 1.0, 0.0]

    got = block_annuity_factors(
        BASIS, curve, dob=dobs, sex=sexes, valuation=[VALUATION] * 4,
        joint_percent=joint, spouse_dob=spouse_dobs, spouse_sex=spouse_sexes,
        n_periods=n,
    )
    for i in range(4):
        sf_x = survival(dobs[i], sexes[i], freq, n)
        if joint[i] == 0.0:
            want = float(annuity_factor(discount, sf_x, freq))
        else:
            sf_y = survival(spouse_dobs[i], spouse_sexes[i], freq, n)
            want = float(
                reversionary_annuity_factor(discount, sf_x, sf_y, joint[i], freq)
            )
        assert got[i] == pytest.approx(want, rel=1e-14)


def test_block_without_spouses_needs_no_spouse_data():
    freq, n = 12, 720
    curve = YieldCurve([INTEREST], freq=freq)
    got = block_annuity_factors(
        BASIS, curve, dob=[d for d, _ in LIVES], sex=[s for _, s in LIVES],
        valuation=[VALUATION] * 4, n_periods=n,
    )
    assert got.shape == (4,)
    with pytest.raises(ValueError, match="spouse_dob"):
        block_annuity_factors(
            BASIS, curve, dob=[d for d, _ in LIVES], sex=[s for _, s in LIVES],
            valuation=[VALUATION] * 4, joint_percent=[0.5] * 4, n_periods=n,
        )


# --- the yield curve -------------------------------------------------------


@pytest.mark.parametrize("freq", [1, 4, 12])
def test_discount_factors_match_vplas_recursion(freq):
    rates = [0.01, 0.02, 0.025, 0.03]
    n = 40 * freq
    got = YieldCurve(rates, freq=freq).discount_factors(n)
    want = reference_discount(rates, freq, n)
    assert got == pytest.approx(want, rel=1e-14)
    assert got[0] == 1.0


def test_rates_are_held_flat_past_the_end_of_the_curve():
    curve = YieldCurve([0.01, 0.05], freq=1, horizon_years=10)
    assert list(curve.rates) == [0.01] + [0.05] * 9


def test_accumulation_factors_invert_discount_factors():
    curve = YieldCurve([0.03], freq=12)
    n = 240
    assert curve.accumulation_factors(n) * curve.discount_factors(n) == pytest.approx(
        np.ones(n), rel=1e-15
    )


def test_converting_to_a_finer_frequency_preserves_the_annual_rate():
    annual = YieldCurve([0.02, 0.04, 0.06], freq=1, horizon_years=10)
    monthly = annual.convert_freq(12)
    assert monthly.freq == 12
    assert list(monthly.rates[:12]) == [0.02] * 12
    assert list(monthly.rates[12:24]) == [0.04] * 12


def test_bad_curves_are_refused():
    with pytest.raises(ValueError, match="divide 12"):
        YieldCurve([0.03], freq=5)
    with pytest.raises(ValueError, match="at least one rate"):
        YieldCurve([], freq=12)
    with pytest.raises(ValueError, match="-100%"):
        YieldCurve([-1.0], freq=12)
    with pytest.raises(ValueError, match="curve covers"):
        YieldCurve([0.03], freq=1, horizon_years=10).discount_factors(50)


# --- accuracy of the reorganised summation ---------------------------------

BLOCK = [
    (date(1935 + 3 * i % 35, 1 + i % 12, 1 + (7 * i) % 28), "M" if i % 2 else "F")
    for i in range(24)
]


@pytest.mark.parametrize("freq", [1, 4, 12])
def test_pairwise_summation_beats_vplas_left_to_right_accumulation(freq):
    """The aggregate accuracy claim, measured rather than asserted.

    VPLA accumulates left to right, where error grows with the number of
    terms; NumPy's reduction is pairwise, where it grows with its logarithm.
    Over a 60-year monthly projection that is 720 terms, and the gap is
    visible: the engine stays within about one unit in the last place while
    the original drifts by an order of magnitude more.
    """
    curve, discount, n = setup(freq)
    engine_errors, reference_errors = [], []
    for dob, sex in BLOCK:
        sf = survival(dob, sex, freq, n)
        reference_sf = reference_survival(dob, sex, freq, n)
        terms = [d * s / freq for d, s in zip(discount, sf)]
        engine_errors.append(
            error_in_ulp(float(annuity_factor(discount, sf, freq)), terms)
        )
        reference_errors.append(
            error_in_ulp(
                reference_annuity_factor(list(discount), reference_sf, freq), terms
            )
        )
    assert max(engine_errors) <= max(reference_errors)
    assert np.mean(engine_errors) <= np.mean(reference_errors)
    assert max(engine_errors) <= 4.0
