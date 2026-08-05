"""Trading to a duration target, and what immunisation actually costs."""

import numpy as np
import pytest

from engine.data.assets import (
    Bond,
    Portfolio,
    Reinvestment,
    forward_factors,
    project,
)
from engine.data.rates import YieldCurve
from engine.data.rebalance import (
    DurationTarget,
    SurplusTarget,
    TradingCost,
    bond_duration,
    execute_trade,
    liability_position,
    matched_target,
    matched_target_path,
    portfolio_duration,
)


FLAT = YieldCurve.flat(0.04, freq=1)
LIABILITIES = np.concatenate([-np.linspace(30.0, 70.0, 25), np.zeros(15)])


def fund(ladder=15, total=1500.0):
    return Portfolio.ladder(FLAT, ladder, total)


def liability_pv(t, curve):
    outgo = -np.minimum(LIABILITIES[t:], 0.0)
    if outgo.sum() <= 0.0:
        return 0.0
    return float((outgo * forward_factors(curve, t, outgo.size)[1:]).sum())


def run(strategy, horizon, ladder=15):
    return project(fund(ladder), LIABILITIES[:horizon], FLAT,
                   reinvestment=Reinvestment(term=ladder), strategy=strategy)


def surplus_swing(projection, horizon, shift=0.02):
    values = []
    for bump in (-shift, 0.0, shift):
        curve = YieldCurve.flat(0.04 + bump, freq=1)
        values.append(projection.portfolio.market_value(curve, horizon)
                      - liability_pv(horizon, curve))
    return max(values) - min(values)


def profile(strategy, horizons=range(3, 21)):
    """Worst and mean surplus swing over a range of measurement dates.

    A single date measures how recently the fund happened to trade rather
    than how well it is hedged, which is what made the first attempt at
    this comparison unreadable.
    """
    swings = [surplus_swing(run(strategy, T), T) for T in horizons]
    return max(swings), float(np.mean(swings))


# --- durations --------------------------------------------------------------

def test_a_single_period_bond_has_a_duration_of_one_period():
    assert bond_duration(FLAT, 1) == pytest.approx(1.0, rel=1e-14)
    monthly = YieldCurve.flat(0.04, freq=12)
    assert bond_duration(monthly, 1) == pytest.approx(1 / 12, rel=1e-14)


def test_a_longer_par_bond_has_a_longer_duration():
    assert bond_duration(FLAT, 30) > bond_duration(FLAT, 10) > bond_duration(FLAT, 2)


def test_a_portfolio_of_one_bond_has_that_bond_s_duration():
    portfolio = Portfolio.from_bonds([Bond.at_par(FLAT, 12, face=100.0)])
    assert portfolio_duration(portfolio, FLAT) == pytest.approx(
        bond_duration(FLAT, 12), rel=1e-12
    )


def test_a_portfolio_duration_is_value_weighted():
    """Equal *face* is not equal value once the coupons differ, so a
    weighting by anything else would be wrong on the first sloped curve."""
    portfolio = Portfolio.from_bonds([Bond.at_par(FLAT, 2, face=100.0),
                                      Bond.at_par(FLAT, 30, face=100.0)])
    short, long = bond_duration(FLAT, 2), bond_duration(FLAT, 30)
    assert short < portfolio_duration(portfolio, FLAT) < long


def test_an_empty_portfolio_has_no_duration():
    with pytest.raises(ValueError, match="no duration"):
        portfolio_duration(Portfolio([], freq=1), FLAT)


def test_a_ladder_sits_between_its_shortest_and_longest():
    assert bond_duration(FLAT, 1) < portfolio_duration(fund(15), FLAT) \
        < bond_duration(FLAT, 15)


# --- the liability's duration does not hold still ----------------------------

def test_a_liability_s_duration_falls_as_it_runs_off():
    """The whole reason a target fixed at inception stops describing
    anything."""
    path = matched_target_path(LIABILITIES, FLAT)
    assert path[0] == pytest.approx(12.764, abs=0.01)
    assert path[10] == pytest.approx(7.807, abs=0.01)
    assert path[20] == pytest.approx(2.972, abs=0.01)
    assert np.all(np.diff(path[:25]) < 0.0)


def test_the_scalar_helper_is_the_first_entry_of_the_path():
    assert matched_target(LIABILITIES, FLAT) == \
        matched_target_path(LIABILITIES, FLAT)[0]


def test_dates_with_nothing_left_to_pay_carry_the_last_target():
    """Rather than raising: a fund with no liability left has nothing to
    match and should not be made to trade."""
    path = matched_target_path(LIABILITIES, FLAT)
    assert path[25] == path[-1] == pytest.approx(path[24], rel=1e-12)


def test_liabilities_that_never_pay_out_have_no_target():
    with pytest.raises(ValueError, match="nothing to match"):
        matched_target_path(np.full(5, 100.0), FLAT)


def test_liability_position_reports_value_and_duration_together():
    value, duration = liability_position(LIABILITIES, FLAT, 10)
    assert value == pytest.approx(liability_pv(10, FLAT), rel=1e-12)
    assert duration == pytest.approx(matched_target_path(LIABILITIES, FLAT)[10],
                                     rel=1e-12)


def test_a_liability_with_nothing_left_reports_nothing():
    assert liability_position(LIABILITIES, FLAT, 30) == (0.0, 0.0)


# --- the trade --------------------------------------------------------------

def test_a_trade_puts_the_duration_exactly_on_target():
    """One equation, no search — so the answer is exact rather than close."""
    portfolio = fund()
    execute_trade(portfolio, FLAT, 0, 10.0, long_term=30, short_term=2)
    assert portfolio_duration(portfolio, FLAT) == pytest.approx(10.0, rel=1e-10)


def test_it_shortens_as_readily_as_it_lengthens():
    portfolio = fund()
    before = portfolio_duration(portfolio, FLAT)
    execute_trade(portfolio, FLAT, 0, before - 3.0, long_term=30, short_term=2)
    assert portfolio_duration(portfolio, FLAT) == pytest.approx(before - 3.0,
                                                                rel=1e-10)


def test_nothing_happens_inside_the_no_trade_band():
    portfolio = fund()
    before = portfolio_duration(portfolio, FLAT)
    traded = execute_trade(portfolio, FLAT, 0, before + 0.2, long_term=30,
                           short_term=2, tolerance=0.5)
    assert traded == (0.0, 0.0, 0.0)
    assert portfolio_duration(portfolio, FLAT) == before


def test_an_empty_portfolio_is_left_alone_rather_than_raising():
    assert execute_trade(Portfolio([], freq=1), FLAT, 0, 10.0,
                         long_term=30, short_term=2) == (0.0, 0.0, 0.0)


def test_an_unreachable_target_gets_as_close_as_the_bonds_allow():
    """Capped at the whole portfolio rather than refused: 40 years is not
    buyable out of a 12-year bond, and selling everything into it is the
    closest the fund can get."""
    portfolio = fund()
    notional, _, _ = execute_trade(portfolio, FLAT, 0, 40.0, long_term=12,
                                   short_term=2)
    assert notional == pytest.approx(1500.0, rel=1e-9)
    assert portfolio_duration(portfolio, FLAT) == pytest.approx(
        bond_duration(FLAT, 12), rel=1e-9
    )


def test_a_maturity_on_the_wrong_side_of_the_gap_is_not_traded_into():
    """Lengthening by buying something shorter than what is held would move
    the portfolio away from the target, so nothing happens."""
    portfolio = Portfolio.from_bonds([Bond.at_par(FLAT, 30, face=1000.0)])
    assert portfolio_duration(portfolio, FLAT) > bond_duration(FLAT, 12)
    assert execute_trade(portfolio, FLAT, 0, 25.0, long_term=12,
                         short_term=2) == (0.0, 0.0, 0.0)


def test_the_spread_comes_out_of_the_notional_traded():
    portfolio = fund()
    notional, cost, _ = execute_trade(portfolio, FLAT, 0, 10.0, long_term=30,
                                      short_term=2,
                                      cost=TradingCost(0.002))
    assert cost == pytest.approx(0.002 * notional, rel=1e-12)


def test_a_trade_on_the_curve_it_was_bought_on_realises_nothing():
    portfolio = fund()
    _, _, realised = execute_trade(portfolio, FLAT, 0, 10.0, long_term=30,
                                   short_term=2)
    assert realised == pytest.approx(0.0, abs=1e-9)


def test_a_trade_after_a_rate_rise_realises_a_loss():
    portfolio = fund()
    higher = YieldCurve.flat(0.07, freq=1)
    _, _, realised = execute_trade(portfolio, higher, 0, 10.0, long_term=30,
                                   short_term=2)
    assert realised < 0.0


@pytest.mark.parametrize("spread, message", [
    (-0.1, "must be in"), (1.0, "must be in"),
])
def test_trading_cost_validates_its_spread(spread, message):
    with pytest.raises(ValueError, match=message):
        TradingCost(spread)


# --- the strategies ---------------------------------------------------------

@pytest.mark.parametrize("kwargs, message", [
    (dict(target=0.0, long_term=30, short_term=2), "must be positive"),
    (dict(target=10.0, long_term=2, short_term=30), "must exceed short term"),
    (dict(target=10.0, long_term=30, short_term=0), "at least one period"),
    (dict(target=10.0, long_term=30, short_term=2, tolerance=-1.0),
     "is negative"),
    (dict(target=10.0, long_term=30, short_term=2, every=0), "at least one"),
])
def test_a_duration_target_validates_its_terms(kwargs, message):
    with pytest.raises(ValueError, match=message):
        DurationTarget(**kwargs)


def test_a_scalar_target_holds_at_every_date():
    target = DurationTarget(target=9.0, long_term=30, short_term=2)
    assert target.target_at(0) == target.target_at(50) == 9.0


def test_a_target_path_is_read_by_date_and_held_flat_past_its_end():
    target = DurationTarget.from_liabilities(LIABILITIES, FLAT, long_term=30,
                                             short_term=2)
    path = matched_target_path(LIABILITIES, FLAT)
    assert target.target_at(10) == pytest.approx(path[10], rel=1e-12)
    assert target.target_at(999) == pytest.approx(path[-1], rel=1e-12)


def test_a_schedule_only_trades_on_its_own_periods():
    target = DurationTarget(target=9.0, long_term=30, short_term=2, every=5)
    assert [t for t in range(12) if target.due(t)] == [4, 9]


def test_the_surplus_target_equates_dollar_durations():
    """`D_liability · L / A`, which is 2.79 years on a fund whose assets are
    1,777 against liabilities of 635 at a shared duration of 7.81."""
    strategy = SurplusTarget(liabilities=tuple(LIABILITIES), long_term=30,
                             short_term=2)
    projection = run(DurationTarget.from_liabilities(
        LIABILITIES, FLAT, long_term=30, short_term=2), 10)
    target = strategy.target_at_date(projection.portfolio, FLAT, 10)
    value, duration = liability_position(LIABILITIES, FLAT, 10)
    assets = projection.portfolio.market_value(FLAT, 10)
    assert target == pytest.approx(duration * value / assets, rel=1e-12)
    assert target == pytest.approx(2.791, abs=0.01)


def test_it_collapses_to_plain_duration_matching_with_no_surplus():
    """The two only agree when the two sides are worth the same amount,
    which is exactly the case the naive rule is right in."""
    strategy = SurplusTarget(liabilities=tuple(LIABILITIES), long_term=30,
                             short_term=2)
    value, duration = liability_position(LIABILITIES, FLAT, 0)
    exact = Portfolio.from_bonds([Bond.at_par(FLAT, 12, face=value)])
    assert strategy.target_at_date(exact, FLAT, 0) == pytest.approx(
        duration, rel=1e-10
    )


def test_a_surplus_target_needs_a_liability_to_immunise_against():
    with pytest.raises(ValueError, match="nothing to immunise"):
        SurplusTarget(liabilities=(100.0, 100.0), long_term=30, short_term=2)


def test_a_surplus_target_validates_its_maturities():
    with pytest.raises(ValueError, match="must exceed short term"):
        SurplusTarget(liabilities=tuple(LIABILITIES), long_term=2,
                      short_term=30)


# --- the identity survives trading ------------------------------------------

def test_the_book_identity_holds_with_a_strategy_running():
    strategy = SurplusTarget(liabilities=tuple(LIABILITIES), long_term=30,
                             short_term=2, cost=TradingCost(0.002))
    projection = run(strategy, 20)
    assert projection.reconciles()
    assert np.abs(projection.residual()).max() < 1e-8


def test_trading_costs_reduce_the_earned_rate():
    """They leave the fund for good, so they belong in what it earned rather
    than in a valuation."""
    free = run(SurplusTarget(liabilities=tuple(LIABILITIES), long_term=30,
                             short_term=2), 20)
    charged = run(SurplusTarget(liabilities=tuple(LIABILITIES), long_term=30,
                                short_term=2, cost=TradingCost(0.002)), 20)
    traded = free.traded > 0.0
    assert np.all(charged.earned_rate[traded] < free.earned_rate[traded])
    assert charged.trading_cost.sum() > 0.0


def test_a_projection_without_a_strategy_never_trades():
    projection = run(None, 20)
    assert projection.turnover == 0.0
    assert np.all(projection.trading_cost == 0.0)


# --- the findings -----------------------------------------------------------

def test_matching_the_liability_s_duration_leaves_the_exposure_untouched():
    """Assets of 1,777 at duration 7.81 against liabilities of 635 at the
    same 7.81: a dollar-duration gap of 8,912, nearly three times the
    liability's own. Matched, and hedged almost nothing."""
    projection = run(DurationTarget.from_liabilities(
        LIABILITIES, FLAT, long_term=30, short_term=2), 10)
    assets = projection.portfolio.market_value(FLAT, 10)
    value, duration = liability_position(LIABILITIES, FLAT, 10)
    asset_duration = portfolio_duration(projection.portfolio, FLAT, 10)
    assert asset_duration == pytest.approx(duration, rel=1e-9)
    assert assets * asset_duration - value * duration == pytest.approx(
        8_912, abs=50
    )


def test_a_target_fixed_at_inception_gets_worse_every_year():
    path = matched_target_path(LIABILITIES, FLAT)
    fixed = DurationTarget(target=path[0], long_term=30, short_term=2)
    gaps = []
    for horizon in (1, 5, 10, 15, 20):
        held = portfolio_duration(run(fixed, horizon).portfolio, FLAT, horizon)
        gaps.append(held - path[horizon])
    assert gaps[0] == pytest.approx(0.515, abs=0.01)
    assert gaps[-1] == pytest.approx(9.792, abs=0.01)
    assert all(a < b for a, b in zip(gaps, gaps[1:]))


def test_duration_matching_buys_almost_nothing_on_average():
    """The classic prescription, measured: 9,839 of turnover for a mean
    surplus swing no better than never trading at all."""
    never_worst, never_mean = profile(None)
    matched_worst, matched_mean = profile(DurationTarget.from_liabilities(
        LIABILITIES, FLAT, long_term=30, short_term=2))
    assert never_mean == pytest.approx(314.8, abs=2.0)
    assert matched_mean == pytest.approx(317.7, abs=2.0)
    assert matched_mean > never_mean
    assert matched_worst < never_worst


def test_matching_dollar_durations_removes_nearly_all_of_it():
    """94% off the mean swing — and the residual is the convexity RFC-020
    said duration matching would leave behind."""
    _, never_mean = profile(None)
    worst, mean = profile(SurplusTarget(liabilities=tuple(LIABILITIES),
                                        long_term=30, short_term=2))
    assert mean == pytest.approx(19.0, abs=2.0)
    assert 1.0 - mean / never_mean > 0.93
    assert worst == pytest.approx(102.8, abs=3.0)


def test_what_is_left_is_convexity_and_it_is_positive():
    """Surplus rises under a shift in *either* direction, which is what a
    portfolio holding more convexity than its liabilities does."""
    projection = run(SurplusTarget(liabilities=tuple(LIABILITIES),
                                   long_term=30, short_term=2), 10)
    values = [projection.portfolio.market_value(YieldCurve.flat(0.04 + b, freq=1), 10)
              - liability_pv(10, YieldCurve.flat(0.04 + b, freq=1))
              for b in (-0.02, -0.01, 0.0, 0.01, 0.02)]
    assert values[2] == min(values)
    assert max(values) - min(values) < 2.0


def test_a_calendar_does_not_know_when_the_market_moved():
    """Rebalancing every fifth period saves 59% of the turnover and is worse
    than never rebalancing at all — a schedule and a band are not two dials
    on the same instrument."""
    _, never_mean = profile(None)

    def surplus(**kwargs):
        return SurplusTarget(liabilities=tuple(LIABILITIES), long_term=30,
                             short_term=2, **kwargs)

    scheduled_worst, scheduled_mean = profile(surplus(every=5))
    assert scheduled_worst > profile(None)[0]
    assert scheduled_mean == pytest.approx(296.0, abs=5.0)
    banded_worst, banded_mean = profile(surplus(tolerance=0.5))
    assert banded_mean < 0.1 * scheduled_mean


def test_a_wider_band_does_not_always_trade_less():
    """Measured, and the opposite of the obvious guess: a band trades less
    *often* and each trade is larger, because the duration was allowed to
    drift further before anything happened."""
    def turnover(tolerance):
        return run(SurplusTarget(liabilities=tuple(LIABILITIES), long_term=30,
                                 short_term=2, tolerance=tolerance), 20).turnover

    tight, wide = turnover(0.0), turnover(1.0)
    assert wide > tight
    assert wide / tight == pytest.approx(1.018, abs=0.01)
