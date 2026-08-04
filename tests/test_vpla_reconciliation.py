"""Reference-model reconciliation: the engine against the VPLA system.

PLAN.md §3.2 asks every product family to carry an independent, obviously
correct implementation and to require the engine to match it. For the
VA/VPLA family that reference is ``tests/vpla_reference.py`` — a hand port
of the real VPLA code (docs/vpla-review.md), plain Python, no engine
imports, no NumPy.

Four things are checked here:

1. **Primitive agreement** — the engine's survival run-off and payout-annuity
   present value equal VPLA's ``survival_factors`` and ``annuity_factor``,
   and the one place they *disagree* (VPLA has a limiting age, the engine
   does not) is pinned down rather than left to be discovered later.
2. **Closed forms inside the reference** — VPLA's O(n²) reversionary loop
   equals the textbook ``ä_x + j(ä_y - ä_xy)``, and its certain-period
   handling equals annuity-certain plus deferred life annuity. That is what
   licenses reusing the loop as a specification for the VA library.
3. **The engine feeding those closed forms** — engine-computed ``ä_x`` and
   ``ä_y`` reproduce VPLA's joint factor, which is exactly how a joint
   benefit will be built once model points carry a second life.
4. **The pool invariant** — VPLA's variable-payment step leaves pensions
   untouched when the fund earns the valuation rate and mortality runs to
   assumption, and the contribution asymmetry of review §6.8 is measured
   rather than assumed.
"""

from datetime import date

import pytest

from engine.data.assumptions import Assumptions, MortalityTable
from engine.data.modelpoints import ModelPoint
from engine.library.fixed_annuity import FixedAnnuity
from vpla_reference import (
    OMEGA,
    ReferenceMortalityTable,
    annuity_factor,
    discount_factors,
    joint_annuity_factor,
    joint_life_factor,
    linear_period_mortality,
    reversionary_closed_form,
    udd_period_mortality,
    valuation_step,
)

REL = 1e-12

FREQ = 1  # annual: the frequency the engine's time axis supports today
INTEREST = 0.03

# Gompertz-ish, ages 50-119. Projections below stop at attained age 118 so
# neither side is extrapolating; the one test that deliberately runs past
# VPLA's limiting age is `test_engine_has_no_limiting_age` .
MIN_AGE, MAX_AGE = 50, 119
QX = {age: min(0.0004 * 1.11 ** (age - MIN_AGE), 1.0) for age in range(MIN_AGE, MAX_AGE + 1)}

ASSUMPTIONS = Assumptions(mortality=MortalityTable(QX), interest=INTEREST)

# The reference basis, restricted to the annual on-anniversary case where
# its fractional-age split collapses to the tabular rate — which is what
# makes it comparable with the annual `@var` templates at all.
REFERENCE_BASIS = ReferenceMortalityTable(
    {"M": QX}, year_start=2000, use_improvement=False
)


def period_mortality(qx, age_at_valuation, n):
    born = date(2000, 1, 1)
    valuation = date(2000 + age_at_valuation, 1, 1)
    return [
        REFERENCE_BASIS.mortality_period(
            born, date(valuation.year + k, 1, 1), "M", 1
        )
        for k in range(n)
    ]


def survival_factors(q_period):
    survival = [1.0]
    for k in range(1, len(q_period)):
        survival.append(survival[k - 1] * (1.0 - q_period[k - 1]))
    return survival


def table_q(qx, age):
    return REFERENCE_BASIS.mortality_lookup(age, "M", 2000)


def periods_to_table_end(age: int) -> int:
    """Projection length that keeps attained ages inside the table."""
    return MAX_AGE - age


def reference_annuity(age: int, n: int, certain_periods: int = 0) -> float:
    df = discount_factors([INTEREST], FREQ, n)
    sf = survival_factors(period_mortality(QX, age, n))
    return annuity_factor(df, sf, FREQ, certain_periods)


def engine_annuity(age: int, n: int) -> float:
    """Payout-annuity PV per policy from the engine: a fixed annuity with no
    deferral pays 1 per survivor at the start of each year, so its
    ``pv_payments`` *is* the annuity factor ``Σ v^t · ₜp_x``."""
    mp = ModelPoint(
        age_at_entry=age, defer_years=0, premium=0.0, annual_payment=1.0,
        init_pols=1,
    )
    return FixedAnnuity(mp=mp, assumptions=ASSUMPTIONS, proj_len=n).pv_payments()


# --- 1. primitive agreement ------------------------------------------------


def test_udd_collapses_to_tabular_q_on_an_anniversary():
    # (before, within, second) = (0, 1, 0) is a full policy year starting on
    # the member's birthday; the conditioning factor must vanish exactly.
    for q_first, q_second in ((0.004, 0.005), (0.2, 0.25), (0.0, 0.5)):
        assert udd_period_mortality(0.0, 1.0, 0.0, q_first, q_second) == q_first
        assert linear_period_mortality(1.0, 0.0, q_first, q_second) == q_first


def test_udd_exceeds_a_linear_split_part_way_through_a_year():
    # Half a year in, UDD re-bases on survival to that point, so it must be
    # strictly heavier than a naive pro-rata blend.
    q_first, q_second = 0.2, 0.25
    udd = udd_period_mortality(0.5, 0.5, 0.0, q_first, q_second)
    linear = linear_period_mortality(0.5, 0.0, q_first, q_second)
    assert udd > linear
    assert udd == pytest.approx(0.5 / (1 - 0.5 * q_first) * q_first, rel=REL)


def test_both_sides_hold_the_last_tabulated_age_flat():
    table = ASSUMPTIONS.mortality
    for age in (110, MAX_AGE, MAX_AGE + 5):
        assert table.q_at(table.clip_age(age)) == table_q(QX, min(age, MAX_AGE))


def test_engine_has_no_limiting_age():
    """VPLA treats attained age 120 as certain death; the engine holds the
    last tabulated rate flat forever. Below 120 the two agree exactly; run a
    projection past it and the engine keeps paying an annuitant VPLA has
    already killed. Recorded here so the gap is a known Layer 0 item
    (review §7.2), not a surprise in a future reconciliation."""
    age = 85
    inside = periods_to_table_end(age)
    assert engine_annuity(age, inside) == pytest.approx(
        reference_annuity(age, inside), rel=REL
    )

    past_omega = OMEGA - age + 10
    assert past_omega > inside
    assert engine_annuity(age, past_omega) > reference_annuity(age, past_omega)


def test_engine_in_force_run_off_matches_vpla_survival_factors():
    age = 65
    n = periods_to_table_end(age)
    engine = FixedAnnuity(
        mp=ModelPoint(age_at_entry=age, defer_years=0, premium=0.0,
                      annual_payment=1.0, init_pols=1),
        assumptions=ASSUMPTIONS,
        proj_len=n,
    ).series("pols_if")
    reference = survival_factors(period_mortality(QX, age, n + 1))
    for t, (got, want) in enumerate(zip(engine, reference)):
        assert got == pytest.approx(want, rel=REL), f"pols_if[{t}]"


@pytest.mark.parametrize("age", [55, 65, 75, 85])
def test_engine_payout_pv_matches_vpla_annuity_factor(age):
    n = periods_to_table_end(age)
    assert engine_annuity(age, n) == pytest.approx(reference_annuity(age, n), rel=REL)


# --- 2. closed forms inside the reference ----------------------------------


@pytest.mark.parametrize("joint_percent", [0.0, 0.5, 0.6, 1.0])
def test_vpla_joint_loop_equals_the_reversionary_closed_form(joint_percent):
    """Review §3.3: exchanging the order of summation in VPLA's O(n²) loop
    gives ``ä_x + j(ä_y - ä_xy)``. Establishing that here is what makes the
    closed form usable as the VA library's joint-benefit specification."""
    age_x, age_y = 65, 62
    n = periods_to_table_end(age_x)
    df = discount_factors([INTEREST], FREQ, n)
    q_x = period_mortality(QX, age_x, n)
    sf_x = survival_factors(q_x)
    sf_y = survival_factors(period_mortality(QX, age_y, n))

    loop = joint_annuity_factor(df, sf_x, q_x, sf_y, joint_percent, FREQ)
    closed = reversionary_closed_form(
        a_x=annuity_factor(df, sf_x, FREQ),
        a_y=annuity_factor(df, sf_y, FREQ),
        a_xy=joint_life_factor(df, sf_x, sf_y) / FREQ,
        joint_percent=joint_percent,
    )
    assert loop == pytest.approx(closed, rel=REL)


def test_zero_joint_percent_reduces_to_the_single_life_factor():
    age = 65
    n = periods_to_table_end(age)
    df = discount_factors([INTEREST], FREQ, n)
    q = period_mortality(QX, age, n)
    sf = survival_factors(q)
    assert joint_annuity_factor(df, sf, q, sf, 0.0, FREQ) == pytest.approx(
        annuity_factor(df, sf, FREQ), rel=REL
    )


@pytest.mark.parametrize("certain_years", [0, 5, 10, 20])
def test_life_and_certain_equals_annuity_certain_plus_deferred_life(certain_years):
    age = 65
    n = periods_to_table_end(age)
    df = discount_factors([INTEREST], FREQ, n)
    sf = survival_factors(period_mortality(QX, age, n))
    guaranteed = certain_years * FREQ
    want = sum(df[k] for k in range(guaranteed)) + sum(
        df[k] * sf[k] for k in range(guaranteed, n)
    )
    assert reference_annuity(age, n, certain_periods=guaranteed) == pytest.approx(
        want / FREQ, rel=REL
    )


def test_certain_period_never_reduces_the_annuity_factor():
    age = 85  # heavy mortality, so the guarantee bites hard
    n = periods_to_table_end(age)
    factors = [reference_annuity(age, n, certain_periods=c) for c in (0, 5, 10, 20)]
    assert factors[0] == reference_annuity(age, n)
    assert factors == sorted(factors)


# --- 3. the engine feeding the closed forms --------------------------------


def test_engine_annuity_factors_reproduce_the_vpla_joint_factor():
    """The joint benefit the VA library owes: every term except ``ä_xy``
    already comes out of the engine. What is missing is a second life on the
    model point, not the mathematics (review §7.2)."""
    age_x, age_y, joint_percent = 65, 62, 0.6
    n = periods_to_table_end(age_x)
    df = discount_factors([INTEREST], FREQ, n)
    q_x = period_mortality(QX, age_x, n)
    sf_x = survival_factors(q_x)
    sf_y = survival_factors(period_mortality(QX, age_y, n))

    vpla = joint_annuity_factor(df, sf_x, q_x, sf_y, joint_percent, FREQ)
    from_engine = reversionary_closed_form(
        a_x=engine_annuity(age_x, n),
        a_y=engine_annuity(age_y, n),
        a_xy=joint_life_factor(df, sf_x, sf_y) / FREQ,
        joint_percent=joint_percent,
    )
    assert from_engine == pytest.approx(vpla, rel=REL)


# --- 4. the pool invariant -------------------------------------------------

POOL_AGES = [62, 68, 74, 78]
POOL_PENSIONS = [1_200.0, 2_400.0, 900.0, 3_000.0]
POOL_N = min(periods_to_table_end(age) for age in POOL_AGES)


def build_pool():
    """A pool one year on, split the way VPLA's member table is: for each
    starting cohort a surviving row and a deceased row.

    The deceased row keeps its rolled-forward account value — that is the
    mortality release entering the pool — and carries no liability. Every
    account value starts at the member's own reserve, ``pension x ä_x``,
    and the annuity factors supplied to the step are the advanced ones,
    ``ä_{x+1}``, because VPLA recalculates factors at each valuation.
    """
    accounts, pensions, factors, alive = [], [], [], []
    for age, pension in zip(POOL_AGES, POOL_PENSIONS):
        reserve = pension * reference_annuity(age, POOL_N) * FREQ
        advanced = reference_annuity(age + 1, POOL_N - 1)
        survival = 1.0 - QX[age]
        for weight, live in ((survival, True), (QX[age], False)):
            accounts.append(weight * reserve)
            pensions.append(weight * pension)
            factors.append(advanced)
            alive.append(live)
    return accounts, pensions, factors, alive


def run_step(contributions=None, fund_return=INTEREST):
    accounts, pensions, factors, alive = build_pool()
    if contributions is None:
        contributions = [0.0] * len(accounts)
    return valuation_step(
        account_values=accounts,
        pensions=pensions,
        annuity_factors=factors,
        contributions=contributions,
        fund_return=fund_return,
        freq=FREQ,
        alive=alive,
    )


def test_pool_is_neutral_when_experience_matches_assumption():
    """The defining property of a variable payment pool: earn the valuation
    rate, lose exactly the expected members, and every pension is unchanged.
    Deceased members' reserves are released to survivors by the adjustment
    itself — there is no separate mortality-credit term.

    This is the specification the engine's future cross-model-point
    reduction (review §7.1) has to satisfy."""
    result = run_step()
    assert result["adjustment"] == pytest.approx(0.0, abs=1e-12)
    _, pensions, _, alive = build_pool()
    for got, want, live in zip(result["pensions"], pensions, alive):
        assert got == pytest.approx(want if live else 0.0, abs=1e-9)


@pytest.mark.parametrize("fund_return", [-0.15, 0.0, 0.03, 0.20])
def test_pool_balances_exactly_when_no_contributions_arrive(fund_return):
    """Review §4.2: assets equal liabilities after every valuation, whatever
    the fund did. The whole of the period's experience, and nothing else,
    lands in the pensions."""
    result = run_step(fund_return=fund_return)
    assert sum(result["account_values"]) == pytest.approx(
        sum(result["retrospective"]), rel=REL
    )


def test_pool_adjustment_tracks_the_fund_return_one_for_one():
    """With no contributions the adjustment is affine in the fund return:
    ``1 + adjustment`` scales exactly with ``1 + r``."""
    base = run_step(fund_return=0.0)["adjustment"]
    for fund_return in (-0.15, 0.03, 0.20):
        got = run_step(fund_return=fund_return)["adjustment"]
        assert 1.0 + got == pytest.approx(
            (1.0 + base) * (1.0 + fund_return), rel=REL
        )


def test_contributions_at_a_valuation_leak_the_adjustment():
    """Review §6.8. New money sits in the adjustment's denominator but is
    added to the new pension unadjusted, so the pool ends up short by exactly
    ``adjustment x contributions``. Asserted as an equality, not a bound, so
    fixing it upstream fails this test rather than passing it silently."""
    contributions = [10_000.0, 0.0, 25_000.0, 0.0, 5_000.0, 0.0, 0.0, 0.0]
    result = run_step(contributions=contributions, fund_return=0.20)
    leak = result["adjustment"] * sum(contributions)
    assert leak != 0.0
    assert sum(result["account_values"]) == pytest.approx(
        sum(result["retrospective"]) - leak, rel=REL
    )
