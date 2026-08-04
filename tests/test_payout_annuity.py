"""The first template on the VPLA basis, and the time axis under it.

``PayoutAnnuity`` is where the promoted basis meets the projection loop, so
the tests are anchored on Layer 0 rather than restating it: the present
value of the template's cashflows must equal
``annuities.block_annuity_factors`` times the payment — **term by term,
bitwise** — and that factor is itself bitwise-parity with VPLA
(scripts/vpla_parity.py). Restating the annuity formula here would only test
that it had been typed twice.

What the template adds on top of the factor is the projection: a cashflow
per period, per policy, at a payment frequency, on calendar dates. So the
rest of the file checks the things a factor cannot: that periods land on the
right dates and ages, that the guarantee and the reversionary benefit appear
in the right periods, that a block agrees with the same policies run singly,
and that monthly and annual axes stand in the expected relation.
"""

from datetime import date

import numpy as np
import pytest

from engine.core.dates import DateArray
from engine.core.timeaxis import TimeAxis
from engine.core.vector import run_vectorized
from engine.data.basis import ValuationBasis
from engine.data.modelpoints import ModelPoint, from_dicts
from engine.data.mortality import MortalityBasis
from engine.data.rates import YieldCurve
from engine.library.annuities import annuity_factor, block_annuity_factors
from engine.library.payout_annuity import PayoutAnnuity

MIN_AGE, MAX_AGE = 18, 115
YEAR_START = 2014
INTEREST = 0.04
VALUATION = date(2021, 1, 1)

RATES = {
    sex: {
        age: min(0.0004 * 1.09 ** (age - MIN_AGE) * (1.0 if sex == "M" else 0.85), 0.6)
        for age in range(MIN_AGE, MAX_AGE + 1)
    }
    for sex in ("M", "F")
}
SCALE = {
    sex: {age: 0.010 if sex == "M" else 0.012 for age in range(MIN_AGE, MAX_AGE + 1)}
    for sex in ("M", "F")
}
MORTALITY = MortalityBasis(RATES, year_start=YEAR_START, improvement=SCALE)


def basis(freq):
    return ValuationBasis(
        mortality=MORTALITY, curve=YieldCurve([INTEREST], freq=freq)
    )


BASE_FIELDS = {
    "id": "A1",
    "dob": date(1956, 1, 1),
    "sex": "M",
    "valuation": VALUATION,
    "annual_payment": 12_000.0,
    "init_lives": 1,
    "certain_years": 0.0,
    "joint_percent": 0.0,
    "spouse_dob": date(1958, 6, 30),
    "spouse_sex": "F",
}


def mp(**overrides):
    unknown = set(overrides) - set(BASE_FIELDS)
    assert not unknown, f"unknown model point fields {unknown}"
    return ModelPoint(**{**BASE_FIELDS, **overrides})


MODELPOINTS = [
    mp(id="A1"),
    mp(id="A2", dob=date(1946, 6, 30), sex="F", annual_payment=6_000.0,
       init_lives=3, certain_years=10.0),
    mp(id="A3", dob=date(1960, 2, 29), sex="M", annual_payment=24_000.0,
       joint_percent=0.6, spouse_dob=date(1962, 11, 15), spouse_sex="F"),
    mp(id="A4", dob=date(1938, 12, 15), sex="F", annual_payment=9_000.0,
       certain_years=5.0, joint_percent=1.0, spouse_dob=date(1940, 3, 1),
       spouse_sex="M"),
]


def run(freq, years, modelpoints=None, outputs=None):
    points = MODELPOINTS if modelpoints is None else modelpoints
    n = years * freq
    return run_vectorized(
        PayoutAnnuity, points, basis(freq), proj_len=n - 1,
        outputs=outputs or ["payments", "v", "lives_if", "survivor_lives",
                            "survival", "age"],
    )


# --- the time axis ---------------------------------------------------------


@pytest.mark.parametrize("freq", [1, 2, 4, 12])
def test_axis_periods_land_on_the_right_dates(freq):
    axis = TimeAxis(freq, 5 * freq, [date(2021, 1, 31), date(2020, 2, 29)])
    assert axis.proj_len == 5 * freq - 1
    assert axis.year_fraction == pytest.approx(1 / freq)
    step = 12 // freq
    for i, valuation in enumerate([date(2021, 1, 31), date(2020, 2, 29)]):
        for t in range(axis.n_periods):
            start = axis.period_start(t)
            months = t * step
            year = valuation.year + (valuation.month - 1 + months) // 12
            month = (valuation.month - 1 + months) % 12 + 1
            assert (int(start.year[i]), int(start.month[i])) == (year, month)


def test_axis_attained_age_matches_the_basis_lookup():
    axis = TimeAxis(12, 60, [VALUATION, VALUATION])
    dobs = [date(1956, 1, 1), date(1960, 2, 29)]
    for t in (0, 11, 12, 13, 47, 59):
        ages = axis.attained_age(dobs, t)
        want = axis.period_start(t).whole_years_since(DateArray.coerce(dobs))
        assert np.array_equal(ages, want)
    # A 1 January birth date turns a year exactly at each 12th monthly period.
    assert int(axis.attained_age(dobs, 0)[0]) == 65
    assert int(axis.attained_age(dobs, 11)[0]) == 65
    assert int(axis.attained_age(dobs, 12)[0]) == 66


def test_axis_rejects_a_bad_period_or_frequency():
    axis = TimeAxis(12, 24, [VALUATION])
    with pytest.raises(IndexError, match="outside axis"):
        axis.period_start(24)
    with pytest.raises(IndexError, match="outside axis"):
        axis.period_start(-1)
    with pytest.raises(ValueError, match="divide 12"):
        TimeAxis(5, 10, [VALUATION])
    with pytest.raises(ValueError, match="n_periods"):
        TimeAxis(12, 0, [VALUATION])


def test_basis_rejects_a_mismatched_axis():
    monthly = basis(12)
    with pytest.raises(ValueError, match="frequency"):
        monthly.check_axis(TimeAxis(1, 10, [VALUATION]))
    with pytest.raises(ValueError, match="curve covers"):
        monthly.check_axis(TimeAxis(12, 120 * 12 + 1, [VALUATION]))


# --- against Layer 0 -------------------------------------------------------


@pytest.mark.parametrize("freq", [1, 4, 12])
def test_present_value_equals_the_layer_0_annuity_factor(freq):
    years = 60
    n = years * freq
    result = run(freq, years)
    want = block_annuity_factors(
        MORTALITY, YieldCurve([INTEREST], freq=freq),
        dob=[p.dob for p in MODELPOINTS],
        sex=[p.sex for p in MODELPOINTS],
        valuation=[p.valuation for p in MODELPOINTS],
        joint_percent=[p.joint_percent for p in MODELPOINTS],
        spouse_dob=[p.spouse_dob for p in MODELPOINTS],
        spouse_sex=[p.spouse_sex for p in MODELPOINTS],
        n_periods=n,
    )
    payments = result.array("payments")
    discount = result.array("v")
    for i, point in enumerate(MODELPOINTS):
        if point.certain_years:
            continue  # block factors take one guarantee for the whole block
        pv = float(np.sum(payments[:, i] * discount[:, i]))
        factor = pv / (point.annual_payment * point.init_lives)
        assert factor == pytest.approx(want[i], rel=1e-13), point.id


@pytest.mark.parametrize("freq", [1, 12])
def test_cashflow_terms_match_the_factor_term_by_term_bitwise(freq):
    """Stronger than matching the present value: every discounted period
    payment must equal the corresponding term of the annuity factor exactly,
    so the two cannot agree by cancelling errors."""
    years = 40
    n = years * freq
    single = [mp(id="S", joint_percent=0.0, certain_years=0.0)]
    result = run(freq, years, modelpoints=single)
    curve = YieldCurve([INTEREST], freq=freq)
    discount = curve.discount_factors(n)
    survival = MORTALITY.survival_curve(
        [single[0].dob], [VALUATION], [single[0].sex], freq, n
    )[0]
    payments = result.array("payments")[:, 0]
    for t in range(n):
        want = survival[t] * single[0].annual_payment / freq
        assert payments[t] == want, f"payments[{t}]"
        assert result.array("v")[t, 0] == discount[t]


@pytest.mark.parametrize("certain_years", [0.0, 5.0, 10.0, 20.0])
def test_certain_period_matches_the_layer_0_guarantee(certain_years):
    freq, years = 12, 50
    n = years * freq
    points = [mp(id="C", certain_years=certain_years, joint_percent=0.0)]
    result = run(freq, years, modelpoints=points)
    pv = float(
        np.sum(result.array("payments")[:, 0] * result.array("v")[:, 0])
    )
    curve = YieldCurve([INTEREST], freq=freq)
    survival = MORTALITY.survival_curve(
        [points[0].dob], [VALUATION], [points[0].sex], freq, n
    )
    want = float(
        annuity_factor(
            curve.discount_factors(n), survival, freq,
            guaranteed=int(round(certain_years * freq)),
        )[0]
    )
    assert pv / points[0].annual_payment == pytest.approx(want, rel=1e-13)


# --- what a factor cannot check --------------------------------------------


def test_a_guarantee_pays_regardless_of_survival_then_stops():
    freq, years, certain = 12, 40, 10.0
    guaranteed = int(certain * freq)
    points = [mp(id="C", certain_years=certain, joint_percent=0.0)]
    result = run(freq, years, modelpoints=points)
    survival = result.array("survival")[:, 0]
    assert np.array_equal(survival[:guaranteed], np.ones(guaranteed))
    assert np.all(survival[guaranteed:] < 1.0)
    assert np.all(np.diff(survival[guaranteed:]) <= 0.0)


def test_the_survivor_benefit_is_zero_at_outset_and_rises():
    """Nobody inherits at time zero — the annuitant is alive with certainty —
    and the expected reversionary benefit grows as that stops being true."""
    freq, years = 12, 60
    points = [mp(id="J", joint_percent=0.6, certain_years=0.0)]
    result = run(freq, years, modelpoints=points)
    survivor = result.array("survivor_lives")[:, 0]
    assert survivor[0] == 0.0
    assert survivor[1] > 0.0
    assert survivor.max() <= 0.6


def test_no_survivor_benefit_leaves_the_single_life_annuity():
    freq, years = 12, 60
    single = [mp(id="X", joint_percent=0.0)]
    result = run(freq, years, modelpoints=single)
    assert np.array_equal(
        result.array("survivor_lives")[:, 0], np.zeros(years * freq)
    )
    assert np.array_equal(
        result.array("payments")[:, 0],
        result.array("lives_if")[:, 0] * single[0].annual_payment / freq,
    )


def test_a_larger_survivor_percentage_is_worth_more():
    freq, years = 12, 60
    values = []
    for percent in (0.0, 0.25, 0.5, 0.75, 1.0):
        points = [mp(id="J", joint_percent=percent)]
        result = run(freq, years, modelpoints=points)
        values.append(
            float(np.sum(result.array("payments")[:, 0] * result.array("v")[:, 0]))
        )
    assert values == sorted(values)


def test_ages_advance_once_a_year_whatever_the_frequency():
    years = 30
    for freq in (1, 4, 12):
        points = [mp(id="A", dob=date(1956, 1, 1))]
        result = run(freq, years, modelpoints=points)
        ages = result.array("age")[:, 0]
        assert ages[0] == 65
        for t in range(years * freq):
            assert ages[t] == 65 + t // freq


def test_a_block_agrees_with_the_same_policies_run_singly():
    freq, years = 12, 50
    slab = run(freq, years)
    for i, point in enumerate(MODELPOINTS):
        alone = run(freq, years, modelpoints=[point])
        for name in ("payments", "lives_if", "survivor_lives", "survival", "age"):
            assert np.array_equal(
                slab.array(name)[:, i], alone.array(name)[:, 0]
            ), f"{point.id} {name}"


def test_monthly_is_worth_less_than_annual_for_an_annuity_due():
    """Payments are in advance, so the annual version hands over a whole
    year's money up front where the monthly version spreads it out. The
    monthly factor is therefore the *smaller* of the two, by the standard
    ``ä - ä^(m) ~ (m - 1) / 2m`` — about 11/24 of a year's payment at m = 12.
    """
    years = 60
    points = [mp(id="F", joint_percent=0.0, certain_years=0.0)]
    annual = run(1, years, modelpoints=points)
    monthly = run(12, years, modelpoints=points)
    assert monthly.array("payments")[0, 0] == pytest.approx(
        annual.array("payments")[0, 0] / 12, rel=1e-15
    )
    pv_annual = float(np.sum(annual.array("payments")[:, 0] * annual.array("v")[:, 0]))
    pv_monthly = float(
        np.sum(monthly.array("payments")[:, 0] * monthly.array("v")[:, 0])
    )
    assert pv_monthly < pv_annual
    gap = (pv_annual - pv_monthly) / points[0].annual_payment
    assert gap == pytest.approx(11 / 24, abs=0.05)


def test_the_limiting_age_stops_payments_one_period_later():
    """VPLA's omega is carried through the projection, with its exact
    timing. ``q = 1`` applies to the period *beginning* at attained age 120,
    so the annuitant is still alive at the start of that period and collects
    one final payment; survival — and every payment after it — is exactly
    zero. Without omega a held-flat final rate would keep paying forever."""
    freq, years = 1, 60
    points = [mp(id="O", dob=date(1936, 1, 1), joint_percent=0.0)]
    result = run(freq, years, modelpoints=points)
    ages = result.array("age")[:, 0]
    payments = result.array("payments")[:, 0]
    survival = result.array("survival")[:, 0]

    reached = np.flatnonzero(ages >= 120)
    assert reached.size, "the projection must run past the limiting age"
    last = reached[0]
    assert payments[last] > 0.0
    assert survival[last] > 0.0
    assert np.all(survival[last + 1:] == 0.0)
    assert np.all(payments[last + 1:] == 0.0)


def test_setup_runs_once_and_before_any_variable():
    """The hook's contract: it is called exactly once per model instance and
    nothing it sets may depend on a ``@var``."""
    calls = []

    class Counting(PayoutAnnuity):
        def setup(self):
            calls.append(len(self._cache))
            super().setup()

    run_vectorized(
        Counting, MODELPOINTS[:1], basis(12), proj_len=59, outputs=["payments"]
    )
    assert calls == [0]
