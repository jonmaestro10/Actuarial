"""Fixed-indexed annuities: the crediting designs and the lifetime guarantee.

Two things are measured here rather than asserted from the product
literature: what each crediting design actually delivers against what it is
quoted at, and how much of a lifetime guarantee a projection loses by
stopping early.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from engine.core.model import var
from engine.core.stochastic import run_stochastic
from engine.data.account import AccountBasis, SurrenderCharge
from engine.data.assumptions import Assumptions, MortalityTable
from engine.data.index_credit import (
    AnnualPointToPoint, IndexCredit, MonthlyAverage, MonthlySum,
)
from engine.data.modelpoints import ModelPoint
from engine.data.scenarios import ScenarioSet
from engine.library.fixed_indexed_annuity import FixedIndexedAnnuity

RISING = MortalityTable(
    {age: min(0.0004 * 1.09 ** (age - 30), 1.0) for age in range(0, 121)}
)


def basis(method, freq=1, lapse=0.05, glwb_fee=0.01, **kw):
    return Assumptions(mortality=RISING, lapse=lapse, interest=0.04, freq=freq,
                       glwb_fee=glwb_fee, index_credit=method, **kw)


def point(**kw):
    fields = dict(id=1, age_at_entry=60, premium=100_000.0, init_pols=1000.0,
                  glwb_base=100_000.0, glwb_rate=0.05,
                  withdrawal_start_year=10, glwb_rollup=0.07,
                  glwb_rollup_years=10)
    fields.update(kw)
    return ModelPoint(**fields)


def no_rider(**kw):
    """A model point with the rider switched off — a zero withdrawal rate
    guarantees a zero withdrawal, so nothing branches."""
    return point(glwb_base=0.0, glwb_rate=0.0, withdrawal_start_year=999,
                 glwb_rollup=0.0, glwb_rollup_years=0, **kw)


def index_paths(n, years, freq, seed=99, drift=math.log(1.06), vol=0.18):
    """A monthly index path and the *same* path compounded to annual steps.

    Every design in the comparisons below then sees an identical index,
    which is what makes the differences between them the designs rather
    than two draws of a random number generator.
    """
    monthly = ScenarioSet.lognormal(n, years * 12, drift=drift / 12,
                                    vol=vol / math.sqrt(12), seed=seed)
    m = monthly.series(monthly.primary)
    annual = ScenarioSet((1.0 + m).reshape(n, years, 12).prod(axis=2) - 1.0)
    return annual if freq == 1 else monthly


# --- the crediting designs, arithmetic ----------------------------------


def test_point_to_point_caps_gears_and_floors():
    method = AnnualPointToPoint(cap=0.06, participation=0.8, spread=0.01)
    # A 20% year: 0.8 * 0.20 - 0.01 = 0.15, capped at 0.06.
    assert method.credit(1.20, 0.0, 1) == pytest.approx(0.06)
    # A 5% year: 0.8 * 0.05 - 0.01 = 0.03, inside both bounds.
    assert method.credit(1.05, 0.0, 1) == pytest.approx(0.03)
    # A bad year floors at zero rather than crediting a loss.
    assert method.credit(0.70, 0.0, 1) == 0.0


def test_the_spread_comes_off_after_the_participation_rate():
    """Two orders, two different answers, and only one of them is the
    contract. Geared first: 0.5 * 0.10 - 0.02 = 0.03. Spread first:
    0.5 * (0.10 - 0.02) = 0.04."""
    method = AnnualPointToPoint(participation=0.5, spread=0.02)
    assert method.credit(1.10, 0.0, 1) == pytest.approx(0.03)


def test_the_floor_is_what_makes_it_an_indexed_annuity():
    plain = AnnualPointToPoint(cap=0.06)
    assert plain.credit(0.60, 0.0, 1) == 0.0
    guaranteed = AnnualPointToPoint(cap=0.06, floor=0.01)
    assert guaranteed.credit(0.60, 0.0, 1) == pytest.approx(0.01)


def test_a_monthly_sum_lets_the_bad_months_through_in_full():
    """The asymmetry, in one line of arithmetic.

    Eleven months at +3% capped to 2% each, and one month at -8%. The
    capped total is 11 * 2% - 8% = 14%. Uncapped it would have been
    11 * 3% - 8% = 25%. The cap took 11 percentage points off the good
    months and nothing off the bad one.
    """
    method = MonthlySum(cap=0.02)
    level, total = 1.0, 0.0
    for ret in [0.03] * 11 + [-0.08]:
        level, total = method.accumulate(level, total, ret)
    assert total == pytest.approx(0.22 - 0.08)
    assert method.credit(level, total, 12) == pytest.approx(0.14)


def test_a_monthly_sum_floors_only_the_total():
    method = MonthlySum(cap=0.02)
    level, total = 1.0, 0.0
    for ret in [0.03] * 6 + [-0.10] * 6:
        level, total = method.accumulate(level, total, ret)
    assert total < 0.0
    assert method.credit(level, total, 12) == 0.0


def test_the_monthly_cap_is_not_an_annual_cap():
    """A monthly-sum design is *not* bounded by its per-period cap. Twelve
    good months at a 2% cap credit 24%, which is the number the design is
    sold on and the number it almost never reaches."""
    method = MonthlySum(cap=0.02)
    level, total = 1.0, 0.0
    for _ in range(12):
        level, total = method.accumulate(level, total, 0.05)
    assert method.credit(level, total, 12) == pytest.approx(0.24)


def test_a_monthly_sum_without_a_cap_is_refused():
    with pytest.raises(ValueError, match="finite per-period cap"):
        MonthlySum(cap=math.inf)


def test_monthly_averaging_truncates_a_rising_year():
    """The average of a path that ends higher than it started is below its
    endpoint — always — so this design credits less than a point-to-point
    one on every up year, and the shortfall is not a fee anybody quotes."""
    steady = [0.01] * 12
    average, endpoint = MonthlyAverage(), AnnualPointToPoint()
    a_level, a_total = 1.0, 0.0
    p_level, p_total = 1.0, 0.0
    for ret in steady:
        a_level, a_total = average.accumulate(a_level, a_total, ret)
        p_level, p_total = endpoint.accumulate(p_level, p_total, ret)
    credited = average.credit(a_level, a_total, 12)
    point_to_point = endpoint.credit(p_level, p_total, 12)
    assert credited < point_to_point
    assert credited == pytest.approx(0.06744, abs=5e-5)
    assert point_to_point == pytest.approx(0.12683, abs=5e-5)


def test_a_monthly_design_refuses_an_annual_projection():
    """It would have to invent the intra-year path, and inventing
    volatility is not a conversion. Refused at construction of the
    assumption set, not at the first anniversary."""
    with pytest.raises(ValueError, match="reads the index 12 times"):
        basis(MonthlySum(cap=0.02), freq=1)
    with pytest.raises(ValueError, match="reads the index 12 times"):
        basis(MonthlyAverage(cap=0.06), freq=4)


def test_a_point_to_point_design_runs_at_any_frequency():
    for freq in (1, 2, 4, 12):
        basis(AnnualPointToPoint(cap=0.06), freq=freq)


def test_a_cap_below_the_floor_is_refused():
    with pytest.raises(ValueError, match="above the cap"):
        AnnualPointToPoint(cap=0.02, floor=0.05)


def test_the_base_class_has_no_crediting_rule_of_its_own():
    with pytest.raises(NotImplementedError):
        IndexCredit().credit(1.0, 0.0, 1)


# --- what the designs deliver -------------------------------------------


def _delivered(method, freq, years=20, n=600):
    a = basis(method, freq=freq, lapse=0.0, glwb_fee=0.0)
    periods = years * freq
    scenarios = index_paths(n, years, freq)
    res = run_stochastic(FixedIndexedAnnuity, [no_rider()], a, scenarios,
                         periods, outputs=["index_credit_rate", "av_eop"])
    anniversaries = np.arange(freq - 1, periods, freq)
    credited = res.array("index_credit_rate")[anniversaries, 0, :]
    return credited.mean(), (credited == 0.0).mean()


def test_a_two_percent_monthly_cap_advertises_24_percent_and_pays_under_one():
    """The measurement this template exists to make possible.

    On one shared index path — 6% expected return, 18% volatility, twenty
    years — a monthly-sum design with a 2% monthly cap quotes four times
    the headline of a 6% annual cap and delivers less than a third of it,
    while crediting nothing in four years out of five.
    """
    annual_mean, annual_zero = _delivered(AnnualPointToPoint(cap=0.06), 1)
    sum_mean, sum_zero = _delivered(MonthlySum(cap=0.02), 12)

    assert annual_mean == pytest.approx(0.032, abs=0.004)
    assert sum_mean == pytest.approx(0.010, abs=0.004)
    assert sum_mean < annual_mean / 3
    assert annual_zero == pytest.approx(0.41, abs=0.04)
    assert sum_zero > 0.75


def test_even_a_three_percent_monthly_cap_loses_to_a_six_percent_annual_one():
    annual_mean, _ = _delivered(AnnualPointToPoint(cap=0.06), 1)
    sum_mean, _ = _delivered(MonthlySum(cap=0.03), 12)
    assert sum_mean < annual_mean          # 36% advertised against 6%


def test_averaging_costs_about_a_tenth_of_the_credit_at_the_same_cap():
    """Same cap, same index, no change to any quoted number."""
    endpoint, _ = _delivered(AnnualPointToPoint(cap=0.06), 1)
    averaged, _ = _delivered(MonthlyAverage(cap=0.06), 12)
    assert averaged < endpoint
    assert 0.05 < (endpoint - averaged) / endpoint < 0.20


# --- the account ---------------------------------------------------------


def test_the_account_does_not_move_between_anniversaries():
    """An FIA credits at anniversaries and nowhere else. The zeros in
    between are the product, not a placeholder."""
    a = basis(AnnualPointToPoint(cap=0.06), freq=12, glwb_fee=0.0)
    scenarios = index_paths(20, 3, 12)
    res = run_stochastic(FixedIndexedAnnuity, [no_rider()], a, scenarios, 36,
                         outputs=["index_credit_rate", "av_eop"])
    rate = res.array("index_credit_rate")[:36, 0, :]
    for t in range(36):
        if (t + 1) % 12:
            assert (rate[t] == 0.0).all()
    account = res.array("av_eop")[:36, 0, :]
    assert (account[3] == account[8]).all()          # flat within the year
    assert not (account[10] == account[11]).all()    # steps at the anniversary


def test_the_account_is_a_ratchet_and_never_falls_from_the_index():
    a = basis(AnnualPointToPoint(cap=0.06), lapse=0.0, glwb_fee=0.0)
    scenarios = index_paths(200, 20, 1)
    res = run_stochastic(FixedIndexedAnnuity, [no_rider()], a, scenarios, 20,
                         outputs=["av_eop"])
    account = res.array("av_eop")[:20, 0, :]
    assert (np.diff(account, axis=0) >= -1e-9).all()


def test_the_rider_fee_is_charged_on_the_base_not_on_the_account():
    """Which is the point of it: the base is what the insurer guaranteed,
    and it is charged for while there is still an account to charge it
    against."""
    a = basis(AnnualPointToPoint(cap=0.06), glwb_fee=0.01)
    scenarios = index_paths(20, 12, 1)
    res = run_stochastic(FixedIndexedAnnuity, [point()], a, scenarios, 12,
                         outputs=["rider_fee", "benefit_base", "av_after_credit"])
    fee = res.array("rider_fee")[5, 0, :]
    base = res.array("benefit_base")[5, 0, :]
    account = res.array("av_after_credit")[5, 0, :]
    assert fee == pytest.approx(0.01 * base)
    assert not np.allclose(fee, 0.01 * account)


def test_the_rider_fee_stops_at_an_empty_account():
    a = basis(AnnualPointToPoint(cap=0.06), glwb_fee=0.01)
    mp = point(glwb_rate=0.15, withdrawal_start_year=0, glwb_rollup=0.0)
    scenarios = index_paths(20, 40, 1)
    res = run_stochastic(FixedIndexedAnnuity, [mp], a, scenarios, 40,
                         outputs=["av_eop", "rider_fee", "av_after_credit"])
    account = res.array("av_eop")[:40, 0, :]
    available = res.array("av_after_credit")[:40, 0, :]
    fee = res.array("rider_fee")[:40, 0, :]
    assert (account >= -1e-9).all()
    # Capped at the account the fee is charged against, which is the one
    # before that period's withdrawal — a withdrawal can empty an account
    # the fee was legitimately taken from.
    assert (fee <= available + 1e-9).all()
    assert (fee[available <= 1e-9] == 0.0).all()
    assert (available <= 1e-9).any()


# --- the benefit base ----------------------------------------------------


def test_the_rollup_compounds_for_the_full_stated_period():
    """The base takes its last step *on* the anniversary withdrawals begin.

    A ten-year roll-up against a tenth-year start compounds ten times. The
    off-by-one here is a whole year of roll-up — 7% of the guarantee.
    """
    a = basis(AnnualPointToPoint(cap=0.06))
    scenarios = ScenarioSet.flat(0.0, 5, 31)
    res = run_stochastic(FixedIndexedAnnuity, [point()], a, scenarios, 30,
                         outputs=["benefit_base"])
    base = res.array("benefit_base")[:, 0, :]
    assert base[10] == pytest.approx(100_000.0 * 1.07 ** 10)
    assert base[9] == pytest.approx(100_000.0 * 1.07 ** 9)


def test_the_base_freezes_once_withdrawals_start():
    a = basis(AnnualPointToPoint(cap=0.06))
    scenarios = index_paths(20, 30, 1)
    res = run_stochastic(FixedIndexedAnnuity, [point()], a, scenarios, 30,
                         outputs=["benefit_base"])
    base = res.array("benefit_base")[:, 0, :]
    assert (base[10] == base[29]).all()


def test_the_base_ratchets_to_a_higher_account_during_deferral():
    """A good year turns into a permanently larger guarantee. With no
    roll-up at all the base still climbs, purely from the ratchet."""
    a = basis(AnnualPointToPoint(cap=0.06), glwb_fee=0.0)
    mp = point(glwb_rollup=0.0, glwb_rollup_years=0)
    scenarios = ScenarioSet.flat(0.20, 3, 21)      # capped to 6% a year
    res = run_stochastic(FixedIndexedAnnuity, [mp], a, scenarios, 20,
                         outputs=["benefit_base", "av_eop"])
    base = res.array("benefit_base")[:, 0, :]
    account = res.array("av_eop")[:, 0, :]
    assert base[9] == pytest.approx(account[8])
    assert (base[9] > base[1]).all()


# --- the L in GLWB -------------------------------------------------------


def _lifetime_run(cls=FixedIndexedAnnuity, n=400, periods=50):
    a = basis(AnnualPointToPoint(cap=0.06))
    scenarios = ScenarioSet.lognormal(n, periods + 1, drift=math.log(1.06),
                                      vol=0.18, seed=5)
    return run_stochastic(cls, [point()], a, scenarios, periods,
                          outputs=["glwb_strain", "withdrawals", "av_eop",
                                   "pols_if"])


def _pv(series, periods):
    v = np.array([1.04 ** -t for t in range(periods)])
    return (series[:periods] * v[:, None]).sum(axis=0).mean()


def test_the_guarantee_keeps_paying_after_the_account_is_empty():
    res = _lifetime_run()
    account = res.array("av_eop")[:50, 0, :]
    strain = res.array("glwb_strain")[:50, 0, :]
    withdrawals = res.array("withdrawals")[:50, 0, :]
    empty = account[40] <= 1e-9
    assert empty.any()
    assert (withdrawals[40][empty] > 0.0).all()
    assert (strain[40][empty] > 0.0).all()


def test_stopping_the_projection_early_values_the_guarantee_at_nothing():
    """The headline of the rider, and the reason a GLWB is not a GMWB.

    The account survives a median 22 years, so a projection cut off at
    twenty sees essentially no strain at all. Every penny of the guarantee
    is in the tail — which is exactly the part a term-limited model throws
    away.
    """
    res = _lifetime_run()
    strain = res.array("glwb_strain")[:, 0, :]
    lifetime = _pv(strain, 50)
    assert lifetime > 0.0
    assert _pv(strain, 20) < 0.02 * lifetime
    assert _pv(strain, 30) < 0.75 * lifetime
    assert _pv(strain, 45) > 0.90 * lifetime


def test_the_guarantee_costs_a_material_share_of_the_income_it_promises():
    res = _lifetime_run()
    strain = _pv(res.array("glwb_strain")[:, 0, :], 50)
    withdrawals = _pv(res.array("withdrawals")[:, 0, :], 50)
    assert 0.2 < strain / withdrawals < 0.45


def test_letting_lapses_run_in_payment_lapses_the_guarantee_away():
    """Measured, because the size of it is the argument.

    A policyholder drawing a lifetime income does not surrender it for a
    cash value worth less than the income. Leaving a flat 5% lapse running
    through the withdrawal phase cuts the cost of the guarantee by about
    60% — not a refinement, a different answer.
    """

    class LapsingInPayment(FixedIndexedAnnuity):
        @var
        def lapse_rate(self, t):
            return self.assumptions.periodic_lapse()

    correct = _pv(_lifetime_run().array("glwb_strain")[:, 0, :], 50)
    lapsed = _pv(_lifetime_run(LapsingInPayment).array("glwb_strain")[:, 0, :], 50)
    assert lapsed < 0.5 * correct


def test_switching_the_rider_off_needs_no_branch():
    """A zero withdrawal rate guarantees a zero withdrawal, so the rider is
    off without a single conditional anywhere in the template."""
    a = basis(AnnualPointToPoint(cap=0.06), glwb_fee=0.0)
    scenarios = index_paths(20, 20, 1)
    res = run_stochastic(FixedIndexedAnnuity, [no_rider()], a, scenarios, 20,
                         outputs=["withdrawals", "glwb_strain", "rider_fee"])
    for name in ("withdrawals", "glwb_strain", "rider_fee"):
        assert (res.array(name)[:20] == 0.0).all()


# --- surrenders and plumbing ---------------------------------------------


def test_the_surrender_charge_applies_to_the_account():
    a = basis(AnnualPointToPoint(cap=0.06), glwb_fee=0.0,
              account=AccountBasis(
                  surrender_charge=SurrenderCharge.declining(0.10, 10)))
    scenarios = index_paths(20, 15, 1)
    res = run_stochastic(FixedIndexedAnnuity, [no_rider()], a, scenarios, 15,
                         outputs=["cash_value", "av_eop"])
    cash = res.array("cash_value")[:15, 0, :]
    account = res.array("av_eop")[:15, 0, :]
    assert cash[0] == pytest.approx(0.90 * account[0])
    assert (cash[12] == account[12]).all()


def test_running_it_without_scenarios_says_so():
    a = basis(AnnualPointToPoint(cap=0.06))
    m = FixedIndexedAnnuity(mp=point(), assumptions=a, proj_len=5)
    with pytest.raises(ValueError, match="needs index scenarios"):
        m.av_eop(0)


def test_the_basis_fingerprints_the_crediting_design():
    from engine.core.fingerprint import fingerprint

    six = basis(AnnualPointToPoint(cap=0.06))
    seven = basis(AnnualPointToPoint(cap=0.07))
    assert fingerprint(six) != fingerprint(seven)
    assert fingerprint(six) == fingerprint(basis(AnnualPointToPoint(cap=0.06)))


def test_a_design_of_a_different_kind_fingerprints_differently():
    from engine.core.fingerprint import fingerprint

    assert (fingerprint(basis(AnnualPointToPoint(cap=0.02)))
            != fingerprint(basis(MonthlySum(cap=0.02), freq=12)))
