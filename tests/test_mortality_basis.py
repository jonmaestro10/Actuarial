"""The vectorized mortality basis against a literal VPLA transcription.

``MortalityBasis`` reorganises VPLA's per-period ``relativedelta`` calls into
array kernels and pre-accumulates the improvement scale. The claim that
buys is strong — *the same numbers, to the last bit* — so it is tested
strongly: every period rate is compared for equality, not closeness, across
the full cross product of the options that change the arithmetic.

Two roundings had to be matched deliberately to get there, and both are
pinned below so a future refactor cannot quietly lose them:

- NumPy's vectorized ``power`` differs from libm's scalar ``pow`` in the
  last bit, which shows up in the constant improvement scale;
- the generational scale must be accumulated one calendar year at a time in
  increasing order, because refactoring it into a cumulative product times a
  power of the tail regroups the multiplications.
"""

from datetime import date

import numpy as np
import pytest
from dateutil.relativedelta import relativedelta

from engine.data.mortality import OMEGA, MortalityBasis
from vpla_reference import ReferenceMortalityTable

MIN_AGE, MAX_AGE = 18, 115
YEAR_START = 2014

RATES = {
    sex: {
        age: min(0.0004 * 1.09 ** (age - MIN_AGE) * (1.0 if sex == "M" else 0.85), 1.0)
        for age in range(MIN_AGE, MAX_AGE + 1)
    }
    for sex in ("M", "F")
}
CONSTANT_SCALE = {
    sex: {age: 0.010 if sex == "M" else 0.012 for age in range(MIN_AGE, MAX_AGE + 1)}
    for sex in ("M", "F")
}
GENERATIONAL_SCALE = {
    sex: {
        year: {
            age: 0.008 + 0.00001 * age + (0.001 if sex == "F" else 0.0)
            for age in range(MIN_AGE, MAX_AGE + 1)
        }
        for year in range(YEAR_START + 1, YEAR_START + 17)
    }
    for sex in ("M", "F")
}

SCALES = [
    ("none", None, False),
    ("constant", CONSTANT_SCALE, True),
    ("generational", GENERATIONAL_SCALE, True),
]

# Leap-day and month-end birth dates, and valuations that land on a
# birthday, mid-year, on a month end, on 29 February, and before the
# improvement scale's base year.
DOBS = [
    date(1956, 1, 1), date(1960, 2, 29), date(1947, 7, 31),
    date(1938, 12, 15), date(1930, 6, 30), date(1972, 3, 1),
]
VALUATIONS = [
    date(2021, 1, 1), date(2021, 3, 31), date(2020, 2, 29),
    date(2022, 11, 30), date(2013, 5, 1),
]


def build(scale, use_improvement, calc, actual_daycount):
    kwargs = dict(
        year_start=YEAR_START, improvement=scale, use_improvement=use_improvement,
        calc=calc, actual_daycount=actual_daycount,
    )
    return (
        ReferenceMortalityTable(RATES, **kwargs),
        MortalityBasis(RATES, **kwargs),
    )


@pytest.mark.parametrize("scale_name,scale,use_improvement", SCALES)
@pytest.mark.parametrize("calc", ["udd", "linear"])
@pytest.mark.parametrize("actual_daycount", [True, False])
@pytest.mark.parametrize("freq", [1, 4, 12])
def test_period_mortality_is_bitwise_identical(
    scale_name, scale, use_improvement, calc, actual_daycount, freq
):
    reference, basis = build(scale, use_improvement, calc, actual_daycount)
    step = 12 // freq
    n = 30 * freq
    got = basis.period_mortality(
        [d for d in DOBS for _ in VALUATIONS],
        [v for _ in DOBS for v in VALUATIONS],
        ["M" if i % 2 else "F" for i in range(len(DOBS) * len(VALUATIONS))],
        freq,
        n,
    )
    row = 0
    for dob in DOBS:
        for valuation in VALUATIONS:
            sex = "M" if row % 2 else "F"
            want = [
                reference.mortality_period(
                    dob, valuation + relativedelta(months=k * step), sex, freq
                )
                for k in range(n)
            ]
            assert list(got[row]) == want, f"{dob} {valuation} {sex}"
            row += 1


@pytest.mark.parametrize("freq", [1, 12])
def test_survival_curve_is_bitwise_identical(freq):
    reference, basis = build(GENERATIONAL_SCALE, True, "udd", True)
    n = 60 * freq
    got = basis.survival_curve(DOBS, [date(2021, 1, 1)] * len(DOBS),
                               ["M"] * len(DOBS), freq, n)
    for i, dob in enumerate(DOBS):
        want = reference.survival_factors(dob, date(2021, 1, 1), "M", freq, n)
        assert list(got[i]) == want, dob


def test_one_call_for_many_lives_equals_many_calls_for_one():
    """The batch axis must not leak: a slab of lives has to agree bitwise
    with the same lives run singly."""
    _, basis = build(GENERATIONAL_SCALE, True, "udd", True)
    sexes = ["M", "F", "M", "F", "M", "F"]
    valuations = [date(2021, 1, 1)] * len(DOBS)
    slab = basis.survival_curve(DOBS, valuations, sexes, 12, 400)
    for i, dob in enumerate(DOBS):
        alone = basis.survival_curve([dob], [valuations[i]], [sexes[i]], 12, 400)
        assert np.array_equal(slab[i], alone[0])


def test_certain_death_at_the_limiting_age():
    """Attained age 120 is certain death regardless of the table, which is
    what stops a held-flat final rate keeping annuitants alive forever."""
    light = {sex: {age: 0.05 for age in range(MIN_AGE, MAX_AGE + 1)}
             for sex in ("M", "F")}
    basis = MortalityBasis(light, year_start=YEAR_START, use_improvement=False)
    born = date(1900, 1, 1)
    q = basis.period_mortality([born], [date(2019, 1, 1)], ["M"], 1, 4)
    assert q[0, 0] == 0.05  # age 119, held flat from the table
    assert list(q[0, 1:]) == [1.0, 1.0, 1.0]  # age 120 onwards
    survival = basis.survival_curve([born], [date(2019, 1, 1)], ["M"], 1, 4)
    assert survival[0, 2] == 0.0


def test_last_tabulated_age_is_held_flat():
    _, basis = build(None, False, "udd", True)
    year = np.array([2021])
    at_end = basis.q(np.array([MAX_AGE]), np.array([0]), year)
    beyond = basis.q(np.array([MAX_AGE + 3]), np.array([0]), year)
    assert at_end == beyond


def test_age_below_the_table_is_refused():
    _, basis = build(None, False, "udd", True)
    with pytest.raises(KeyError, match="below the mortality table"):
        basis.q(np.array([MIN_AGE - 1]), np.array([0]), np.array([2021]))


def test_improvement_reduces_mortality_over_time():
    for scale in (CONSTANT_SCALE, GENERATIONAL_SCALE):
        _, basis = build(scale, True, "udd", True)
        rates = [
            basis.q(np.array([70]), np.array([0]), np.array([year]))[0]
            for year in (2014, 2020, 2030, 2050)
        ]
        assert rates == sorted(rates, reverse=True)


def test_improvement_is_neutral_in_the_base_year():
    for scale in (CONSTANT_SCALE, GENERATIONAL_SCALE):
        _, basis = build(scale, True, "udd", True)
        improved = basis.q(np.array([70]), np.array([0]), np.array([YEAR_START]))
        _, plain = build(None, False, "udd", True)
        assert improved == plain.q(np.array([70]), np.array([0]), np.array([YEAR_START]))


def test_generational_scale_holds_its_last_year_flat():
    """Beyond the tabulated years the scale keeps compounding at the final
    year's rate — repeated multiplication, matching the reference's loop,
    not a power applied to a partial product."""
    reference, basis = build(GENERATIONAL_SCALE, True, "udd", True)
    last = max(GENERATIONAL_SCALE["M"])
    for year in (last, last + 1, last + 7):
        got = basis.q(np.array([70]), np.array([0]), np.array([year]))[0]
        # sex index 0 is "F" (sexes are sorted); check the same one.
        assert got == reference.mortality_lookup(70, basis.sexes[0], year)


@pytest.mark.parametrize("calc", ["udd", "linear"])
def test_udd_is_heavier_than_a_linear_split_mid_year(calc):
    """UDD re-bases the first age's rate on survival to the start of the
    period; a linear split does not, so UDD must be the heavier of the two
    whenever the period starts part way through a year of age."""
    rates, valuation, dob = RATES, date(2021, 7, 1), date(1956, 1, 1)
    _, udd = build(None, False, "udd", True)
    _, linear = build(None, False, "linear", True)
    q_udd = udd.period_mortality([dob], [valuation], ["M"], 12, 24)[0]
    q_linear = linear.period_mortality([dob], [valuation], ["M"], 12, 24)[0]
    assert np.all(q_udd >= q_linear)
    assert np.any(q_udd > q_linear)


def test_thirty_360_rounds_the_period_split_to_whole_periods():
    _, actual = build(None, False, "udd", True)
    _, rounded = build(None, False, "udd", False)
    dob, valuation = date(1956, 5, 17), date(2021, 1, 1)
    q_actual = actual.period_mortality([dob], [valuation], ["M"], 4, 20)[0]
    q_rounded = rounded.period_mortality([dob], [valuation], ["M"], 4, 20)[0]
    assert not np.array_equal(q_actual, q_rounded)
    assert np.allclose(q_actual, q_rounded, atol=5e-4)


def test_blending_defaults_to_improving_consistently():
    """VPLA blends the base rates and then applies one sex's improvement
    scale (review §6.4). The default here blends the improved rates;
    ``blend="base"`` reproduces the original exactly."""
    common = dict(year_start=YEAR_START, improvement=CONSTANT_SCALE, calc="udd")
    consistent = MortalityBasis(RATES, blend_male_percent=0.6, **common)
    like_vpla = MortalityBasis(
        RATES, blend_male_percent=0.6, blend="base", **common
    )
    reference = ReferenceMortalityTable(
        RATES, use_blended_rate=True, blended_male_percent=0.6, **common
    )
    age, year = np.array([70]), np.array([2040])
    for sex_index, sex in enumerate(consistent.sexes):
        assert like_vpla.q(age, np.array([sex_index]), year)[0] == (
            reference.mortality_lookup(70, sex, 2040)
        )
    # The two blends disagree, and the inconsistent one depends on the sex
    # asked for even though the rate is supposed to be unisex.
    blended = [
        consistent.q(age, np.array([i]), year)[0]
        for i in range(len(consistent.sexes))
    ]
    vpla_blended = [
        like_vpla.q(age, np.array([i]), year)[0]
        for i in range(len(like_vpla.sexes))
    ]
    assert blended[0] == blended[1]
    assert vpla_blended[0] != vpla_blended[1]


def test_vpla_json_shapes_load():
    """VPLA's own file layout: mortality and a constant improvement scale
    share one table keyed by age."""
    combined = {
        str(age): {
            "Mortality.M": RATES["M"][age],
            "Mortality.F": RATES["F"][age],
            "Improvement.M": CONSTANT_SCALE["M"][age],
            "Improvement.F": CONSTANT_SCALE["F"][age],
        }
        for age in range(MIN_AGE, MAX_AGE + 1)
    }
    basis = MortalityBasis.from_vpla_tables(
        combined, combined, year_start=YEAR_START
    )
    direct = MortalityBasis(
        RATES, improvement=CONSTANT_SCALE, year_start=YEAR_START
    )
    age, year = np.array([70]), np.array([2035])
    for i in range(len(basis.sexes)):
        assert basis.q(age, np.array([i]), year) == direct.q(
            age, np.array([i]), year
        )


def test_bad_configuration_is_refused():
    with pytest.raises(ValueError, match="calc"):
        MortalityBasis(RATES, year_start=YEAR_START, calc="quadratic")
    with pytest.raises(ValueError, match="contiguous"):
        MortalityBasis({"M": {20: 0.1, 25: 0.2}}, year_start=YEAR_START)
    with pytest.raises(ValueError, match="nesting depth"):
        MortalityBasis(RATES, year_start=YEAR_START, improvement={"M": 0.01})


def test_omega_is_the_vpla_limiting_age():
    assert OMEGA == 120
