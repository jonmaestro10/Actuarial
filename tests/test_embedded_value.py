"""Embedded value and ALM.

Two things are demonstrated rather than asserted: what the time value of a
guarantee does to a reported value, and that matching duration does not
immunise a balance sheet against anything but an infinitesimal move.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from engine.core.stochastic import run_stochastic
from engine.data.account import (
    AccountBasis, Corridor, CostOfInsurance, CreditingBasis,
)
from engine.data.assumptions import Assumptions, MortalityTable
from engine.data.modelpoints import ModelPoint
from engine.data.rates import YieldCurve
from engine.data.scenarios import ScenarioSet
from engine.library.universal_life import UniversalLife
from engine.report.embedded_value import (
    MOVEMENTS, BalanceSheet, EmbeddedValue, analysis_of_change, convexity,
    duration_matched_assets, frictional_cost_of_capital, macaulay_duration,
    present_value, reconciles, shifted,
)

FLAT4 = YieldCurve.flat(0.04, freq=1, horizon_years=60)
LIABILITY = np.full(20, 1000.0)


# --- the pieces ----------------------------------------------------------


def test_a_zero_coupon_bond_has_its_own_maturity_as_its_duration():
    zero = np.zeros(10)
    zero[7] = 1000.0
    assert macaulay_duration(zero, FLAT4) == pytest.approx(8.0)


def test_a_level_annuity_is_shorter_than_half_its_term():
    """Because the early payments are worth more, which is the whole
    content of a present-value weighting."""
    duration = macaulay_duration(LIABILITY, FLAT4)
    assert 9.0 < duration < 10.5
    assert duration < 20 / 2 + 1


def test_something_worth_nothing_has_no_duration():
    with pytest.raises(ValueError, match="no time at which"):
        macaulay_duration(np.zeros(10), FLAT4)
    with pytest.raises(ValueError, match="no convexity"):
        convexity(np.zeros(10), FLAT4)


def test_a_parallel_shift_moves_every_rate_by_the_same_amount():
    up = shifted(FLAT4, 100)
    assert up.rates[0] == pytest.approx(0.05)
    assert up.rates[-1] == pytest.approx(FLAT4.rates[-1] + 0.01)
    assert present_value(LIABILITY, up) < present_value(LIABILITY, FLAT4)


# --- the finding: matching duration does not immunise -------------------


def test_a_duration_matched_barbell_matches_value_and_duration_exactly():
    assets = duration_matched_assets(LIABILITY, FLAT4, short=2, long=25)
    sheet = BalanceSheet(assets, LIABILITY, FLAT4)
    assert sheet.surplus() == pytest.approx(0.0, abs=1e-8)
    assert abs(sheet.duration_gap()) < 1e-12


def test_matching_duration_leaves_the_second_derivative_free():
    """Two portfolios, both matching the liability's value and duration to
    machine precision, and their surplus moves in **opposite directions**
    under the same shift. Only the convexity gap differs.

    A wide barbell holds more convexity than the liability and gains on a
    move either way; a narrow one holds less and loses either way. Neither
    is immunised, and the word is routinely used as though matching
    duration achieved it.
    """
    wide = BalanceSheet(duration_matched_assets(LIABILITY, FLAT4, short=2,
                                                long=25), LIABILITY, FLAT4)
    narrow = BalanceSheet(duration_matched_assets(LIABILITY, FLAT4, short=9,
                                                  long=10), LIABILITY, FLAT4)

    for sheet in (wide, narrow):
        assert abs(sheet.duration_gap()) < 1e-12
        assert sheet.surplus() == pytest.approx(0.0, abs=1e-8)

    assert wide.convexity_gap() > 0.0
    assert narrow.convexity_gap() < 0.0

    for shift in (-300, 300):
        assert wide.surplus_under_shift(shift) > 0.0
        assert narrow.surplus_under_shift(shift) < 0.0


def test_the_immunisation_error_grows_with_the_square_of_the_shift():
    """Which is what "second order" means, and why a small move looks
    immunised and a large one does not.

    Asserted as a **convergence** rather than a fixed ratio, because at any
    shift big enough to matter the third derivative is in it too: halving
    the shift divides the error by 3.12 at 400bp and by 3.97 at 12.5bp,
    approaching the 4 the quadratic term alone would give. Testing for 16
    across a 50-to-200bp jump was the first version and it is wrong by 18%
    for exactly that reason.
    """
    sheet = BalanceSheet(duration_matched_assets(LIABILITY, FLAT4, short=2,
                                                 long=25), LIABILITY, FLAT4)
    shifts = [400, 200, 100, 50, 25, 12.5, 6.25]
    surpluses = [sheet.surplus_under_shift(bp) for bp in shifts]
    ratios = [a / b for a, b in zip(surpluses, surpluses[1:])]

    assert all(r < 4.0 for r in ratios)          # the cubic drags it under
    assert ratios == sorted(ratios)              # and lets go as it shrinks
    assert ratios[-1] == pytest.approx(4.0, rel=0.02)


def test_a_barbell_has_to_bracket_the_liability_it_matches():
    with pytest.raises(ValueError, match="bracket"):
        duration_matched_assets(LIABILITY, FLAT4, short=12, long=25)


def test_the_duration_gap_needs_both_sides_to_exist():
    sheet = BalanceSheet(np.zeros(20), LIABILITY, FLAT4)
    with pytest.raises(ValueError, match="both sides"):
        sheet.duration_gap()


def test_the_balance_sheet_says_what_it_is():
    sheet = BalanceSheet(duration_matched_assets(LIABILITY, FLAT4, short=2,
                                                 long=25), LIABILITY, FLAT4)
    assert "mismatch" in repr(sheet)


# --- embedded value ------------------------------------------------------


def test_a_traditional_embedded_value_carries_no_time_value_at_all():
    """Which is exactly the criticism of it: not that it is approximate,
    but that the line is absent."""
    traditional = EmbeddedValue(free_surplus=500_000.0,
                                required_capital=2_000_000.0,
                                pvfp=7_781_422.0)
    assert not traditional.market_consistent
    assert traditional.time_value_of_guarantees == 0.0
    assert traditional.value == pytest.approx(10_281_422.0)
    assert "EV(" in repr(traditional)


def test_supplying_a_stochastic_value_is_what_makes_it_market_consistent():
    market = EmbeddedValue(free_surplus=500_000.0, required_capital=2_000_000.0,
                           pvfp=1000.0, stochastic_pvfp=600.0)
    assert market.market_consistent
    assert market.time_value_of_guarantees == pytest.approx(400.0)
    assert market.value_of_in_force == pytest.approx(600.0)
    assert "MCEV(" in repr(market)


def test_a_guarantee_cannot_be_worth_less_than_nothing():
    """A stochastic value *above* the deterministic one means the scenario
    set is not centred on the deterministic basis — not that the option has
    value to the shareholder."""
    odd = EmbeddedValue(free_surplus=0.0, required_capital=0.0, pvfp=100.0,
                        stochastic_pvfp=150.0)
    assert odd.time_value_of_guarantees == 0.0


def test_the_components_add_to_the_value():
    market = EmbeddedValue(free_surplus=500.0, required_capital=2_000.0,
                           pvfp=1_000.0, stochastic_pvfp=600.0,
                           frictional_cost=50.0, non_hedgeable_cost=30.0)
    assert sum(market.components().values()) == pytest.approx(market.value)


def test_costs_are_costs_and_capital_is_not_negative():
    with pytest.raises(ValueError, match="is a cost"):
        EmbeddedValue(free_surplus=0.0, required_capital=0.0, pvfp=0.0,
                      frictional_cost=-1.0)
    with pytest.raises(ValueError, match="negative"):
        EmbeddedValue(free_surplus=0.0, required_capital=-1.0, pvfp=0.0)


def test_the_frictional_cost_charges_the_spread_and_not_the_whole_return():
    """A frequent error, and it double counts: the investment income on
    required capital is already inside the projected profits."""
    held = np.full(10, 1_000_000.0)
    cost = frictional_cost_of_capital(held, FLAT4, shareholder_spread=0.03)
    whole_return = frictional_cost_of_capital(held, FLAT4,
                                              shareholder_spread=0.07)
    assert cost < whole_return
    assert cost == pytest.approx(0.03 * present_value(held, FLAT4))


def test_capital_that_costs_nothing_to_hold_needs_no_credit():
    with pytest.raises(ValueError, match="not a credit"):
        frictional_cost_of_capital(np.ones(5), FLAT4, shareholder_spread=-0.01)


# --- the finding: TVOG on a real block -----------------------------------


def universal_life_block(guaranteed=0.01, spread=0.015):
    basis = MortalityTable(
        {age: min(0.0004 * 1.09 ** (age - 30), 1.0) for age in range(0, 121)}
    )
    account = AccountBasis(
        premium_load=0.05, policy_fee=60.0,
        coi=CostOfInsurance(loading=1.1),
        crediting=CreditingBasis(guaranteed=guaranteed, spread=spread,
                                 mode="portfolio"),
        corridor=Corridor.section_7702())
    assumptions = Assumptions(mortality=basis, lapse=0.04, interest=0.05,
                              account=account)
    point = ModelPoint(id=1, age_at_entry=45, term_years=30,
                       face_amount=200_000.0, annual_premium=5_000.0,
                       init_pols=1000.0)
    return assumptions, point


def profits(guaranteed=0.01, spread=0.015, vol=0.06, scenarios=600):
    assumptions, point = universal_life_block(guaranteed, spread)
    discount = np.array([1.05 ** -(t + 1) for t in range(30)])

    model = UniversalLife(mp=point, assumptions=assumptions, proj_len=31)
    for t in range(31):
        model.av_eop(t)
    deterministic = float(sum(
        (model.charge_income(t) - model.guarantee_cost(t)) * discount[t]
        for t in range(30)))

    paths = ScenarioSet.lognormal(scenarios, 31, drift=math.log(1.05),
                                  vol=vol, seed=21)
    result = run_stochastic(UniversalLife, [point], assumptions, paths, 30,
                            outputs=["charge_income", "guarantee_cost"])
    per_path = (result.array("charge_income")[:30, 0, :]
                - result.array("guarantee_cost")[:30, 0, :])
    return deterministic, float((per_path * discount[:, None]).sum(0).mean())


def test_the_time_value_of_a_guarantee_can_exceed_the_whole_deterministic_value():
    """The sharpest statement of why market-consistent value replaced
    traditional, measured on a real projected block rather than an
    illustration.

    A 1% minimum crediting rate on a universal-life account, at 6%
    volatility. Deterministically the floor never binds and the block is
    worth 7.8m of future profit. Across a distribution the same floor costs
    **more than that**: the traditional value reports a positive value of
    in-force where the market-consistent one reports a negative one.

    Not a mis-calibration. RFC-010 established that an annual crediting
    floor is a *strip* of one-year options rather than one long one, and
    the account it applies to is thirty times the annual charge income —
    so a hundred basis points on the account is comparable to the whole
    margin.
    """
    deterministic, stochastic = profits()
    traditional = EmbeddedValue(free_surplus=500_000.0,
                                required_capital=2_000_000.0,
                                pvfp=deterministic)
    market = EmbeddedValue(free_surplus=500_000.0,
                           required_capital=2_000_000.0,
                           pvfp=deterministic, stochastic_pvfp=stochastic)

    assert deterministic > 0.0
    assert stochastic < 0.0
    assert market.time_value_of_guarantees > deterministic
    assert traditional.value_of_in_force > 0.0
    assert market.value_of_in_force < 0.0


def test_the_time_value_rises_with_volatility_and_with_the_floor():
    """Both directions, and neither is visible deterministically — the
    deterministic value does not move at all when volatility changes."""
    base_det, base_sto = profits(vol=0.04, scenarios=400)
    volatile_det, volatile_sto = profits(vol=0.10, scenarios=400)
    higher_det, higher_sto = profits(guaranteed=0.02, vol=0.04, scenarios=400)

    assert volatile_det == pytest.approx(base_det, rel=1e-12)
    assert (base_det - base_sto) < (volatile_det - volatile_sto)
    assert (base_det - base_sto) < (higher_det - higher_sto)


# --- the analysis of change ---------------------------------------------


def test_the_bridge_always_reconciles_because_the_residual_is_solved():
    bridge = analysis_of_change(
        opening=1000.0, closing=1180.0, unwind=60.0, new_business=150.0,
        experience_variance=-20.0, assumption_changes=-10.0, distributed=0.0)
    assert bridge["unexplained"] == pytest.approx(0.0, abs=1e-9)
    assert reconciles(bridge)


def test_a_missing_movement_shows_up_as_a_residual_rather_than_vanishing():
    """The whole design. A report of this kind is judged on whether the
    components add up, so the last one is whatever is left and it is
    reported — a residual that is not tiny means a movement was left out,
    which is information rather than something to absorb into the nearest
    line."""
    bridge = analysis_of_change(opening=1000.0, closing=1180.0, unwind=60.0,
                                new_business=150.0)
    assert bridge["unexplained"] == pytest.approx(-30.0)
    assert reconciles(bridge)


def test_every_named_movement_appears_even_when_it_is_zero():
    bridge = analysis_of_change(opening=100.0, closing=100.0)
    for name in MOVEMENTS:
        assert bridge[name] == 0.0
    assert reconciles(bridge)


def test_an_unknown_movement_is_refused_rather_than_silently_dropped():
    with pytest.raises(ValueError, match="not movements this bridge knows"):
        analysis_of_change(opening=0.0, closing=0.0, foreign_exchange=10.0)


def test_a_distribution_reduces_the_closing_value():
    bridge = analysis_of_change(opening=1000.0, closing=940.0, unwind=40.0,
                                distributed=-100.0)
    assert bridge["unexplained"] == pytest.approx(0.0, abs=1e-9)
    assert reconciles(bridge)


def test_a_bridge_that_does_not_add_up_is_reported_as_not_adding_up():
    broken = analysis_of_change(opening=1000.0, closing=1180.0, unwind=60.0)
    broken["unexplained"] = 0.0          # what hiding it would look like
    assert not reconciles(broken)
