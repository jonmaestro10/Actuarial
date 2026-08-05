"""Asset projection: book values, defaults, reinvestment and forced sales."""

import numpy as np
import pytest

from engine.data.assets import (
    Bond,
    DefaultBasis,
    Holding,
    LIQUIDATION_ORDERS,
    Portfolio,
    Reinvestment,
    breakeven_spread,
    earned_rates,
    forward_factors,
    half_life,
    internal_rate,
    par_coupon,
    project,
)
from engine.data.rates import YieldCurve
from engine.report.pbr import accumulated_surplus


FLAT3 = YieldCurve.flat(0.03, freq=1)
FLAT7 = YieldCurve.flat(0.07, freq=1)


# --- discounting ------------------------------------------------------------

def test_forward_factors_at_zero_reproduce_the_curve():
    """The forward-started factors are the curve's own, not a re-derivation."""
    assert np.array_equal(
        forward_factors(FLAT3, 0, 25), FLAT3.discount_factors(26)
    )


def test_forward_factors_start_at_one():
    assert forward_factors(FLAT3, 7, 10)[0] == 1.0


def test_forward_factors_chain():
    """Discounting 0->5 then 5->12 is discounting 0->12."""
    curve = YieldCurve([0.01, 0.02, 0.04, 0.05, 0.03], freq=1)
    near = forward_factors(curve, 0, 5)[5]
    far = forward_factors(curve, 5, 7)[7]
    assert near * far == pytest.approx(forward_factors(curve, 0, 12)[12], rel=1e-15)


def test_forward_factors_reject_a_window_past_the_curve():
    with pytest.raises(ValueError, match="curve covers"):
        forward_factors(FLAT3, 0, FLAT3.n_periods + 1)


def test_forward_factors_reject_negative_windows():
    with pytest.raises(ValueError, match="must not be negative"):
        forward_factors(FLAT3, -1, 5)


# --- par coupons and yields -------------------------------------------------

def test_par_coupon_on_a_flat_curve_is_the_curve_rate():
    """The identity that lets new money be reinvested without a convention."""
    assert par_coupon(FLAT3, 0, 10) == pytest.approx(0.03, rel=1e-14)


def test_par_coupon_monthly_is_the_nominal_equivalent():
    monthly = YieldCurve.flat(0.03, freq=12)
    expected = 12.0 * (1.03 ** (1 / 12) - 1.0)
    assert par_coupon(monthly, 0, 120) == pytest.approx(expected, rel=1e-13)


def test_par_coupon_on_a_rising_curve_sits_between_the_ends():
    curve = YieldCurve([0.01, 0.02, 0.03, 0.04, 0.05], freq=1)
    coupon = par_coupon(curve, 0, 5)
    assert 0.01 < coupon < 0.05


def test_par_coupon_forward_started_follows_the_curve():
    curve = YieldCurve([0.01, 0.02, 0.03, 0.04, 0.05], freq=1)
    assert par_coupon(curve, 4, 3) > par_coupon(curve, 0, 3)


def test_par_coupon_rejects_a_zero_term():
    with pytest.raises(ValueError, match="at least one period"):
        par_coupon(FLAT3, 0, 0)


def test_internal_rate_of_a_par_bond_is_the_coupon():
    payments = np.full(10, 40.0)
    payments[-1] += 1000.0
    assert internal_rate(payments, 1000.0, 1) == pytest.approx(0.04, rel=1e-13)


def test_internal_rate_of_a_premium_bond_is_below_its_coupon():
    bond = Bond(face=1000.0, coupon=0.06, term=10)
    price = bond.market_value(YieldCurve.flat(0.04, freq=1))
    assert price > 1000.0
    assert internal_rate(bond.payments(), price, 1) == pytest.approx(0.04, rel=1e-12)


def test_internal_rate_of_a_discount_bond_is_above_its_coupon():
    bond = Bond(face=1000.0, coupon=0.02, term=10)
    price = bond.market_value(YieldCurve.flat(0.05, freq=1))
    assert price < 1000.0
    assert internal_rate(bond.payments(), price, 1) == pytest.approx(0.05, rel=1e-12)


def test_internal_rate_refuses_a_series_that_changes_sign():
    """Several roots; a solver that picks one silently is worse than none."""
    with pytest.raises(ValueError, match="changes sign"):
        internal_rate([100.0, -50.0, 100.0], 90.0, 1)


def test_internal_rate_refuses_a_non_positive_price():
    with pytest.raises(ValueError, match="must be positive"):
        internal_rate([100.0], 0.0, 1)


def test_internal_rate_refuses_a_worthless_series():
    with pytest.raises(ValueError, match="no positive payments"):
        internal_rate([0.0, 0.0], 10.0, 1)


# --- bonds ------------------------------------------------------------------

def test_bond_payments_carry_the_principal_at_the_end():
    bond = Bond(face=1000.0, coupon=0.05, term=4)
    assert np.array_equal(bond.payments(), np.array([50.0, 50.0, 50.0, 1050.0]))


def test_bond_defaults_to_par():
    assert Bond(face=250.0, coupon=0.03, term=5).cost == 250.0


def test_bond_at_par_prices_to_one_on_its_own_curve():
    bond = Bond.at_par(FLAT3, 12, face=500.0)
    assert bond.market_value(FLAT3) == pytest.approx(500.0, rel=1e-13)


def test_bond_at_par_with_a_spread_is_worth_more_than_par():
    bond = Bond.at_par(FLAT3, 12, face=500.0, spread=0.015)
    assert bond.market_value(FLAT3) > 500.0
    assert bond.book_yield() == pytest.approx(0.045, abs=2e-4)


def test_bond_market_value_rejects_a_curve_on_another_frequency():
    with pytest.raises(ValueError, match="times a year"):
        Bond(face=1.0, coupon=0.03, term=5).market_value(
            YieldCurve.flat(0.03, freq=12)
        )


@pytest.mark.parametrize("kwargs, message", [
    ({"face": 0.0, "coupon": 0.03, "term": 5}, "must be positive"),
    ({"face": 1.0, "coupon": 0.03, "term": 0}, "at least one period"),
    ({"face": 1.0, "coupon": 0.03, "term": 5, "price": -1.0}, "must be positive"),
])
def test_bond_validates_its_terms(kwargs, message):
    with pytest.raises(ValueError, match=message):
        Bond(**kwargs)


# --- holdings ---------------------------------------------------------------

def test_a_holding_amortises_to_exactly_nothing():
    """The book value of a bond that has paid everything is zero, by
    construction of its yield — not by a convention that writes it off."""
    holding = Holding.from_bond(Bond(face=1000.0, coupon=0.06, term=15,
                                     price=1200.0))
    for _ in range(15):
        holding.book += holding.book * holding.rate - float(holding.flows[0])
        holding.flows = holding.flows[1:]
    assert holding.book == pytest.approx(0.0, abs=1e-9)


def test_income_on_a_premium_bond_is_below_its_coupon():
    """The excess coupon is return of capital. Booking it as income is the
    most common way an asset projection reports a rate it did not earn."""
    bond = Bond(face=1000.0, coupon=0.06, term=10)
    priced = Bond(face=1000.0, coupon=0.06, term=10,
                  price=bond.market_value(YieldCurve.flat(0.04, freq=1)))
    holding = Holding.from_bond(priced)
    income = holding.book * holding.rate
    assert income == pytest.approx(46.489, abs=0.001)
    assert float(holding.flows[0]) == 60.0


def test_income_on_a_discount_bond_is_above_its_coupon():
    bond = Bond(face=1000.0, coupon=0.02, term=10)
    priced = Bond(face=1000.0, coupon=0.02, term=10,
                  price=bond.market_value(YieldCurve.flat(0.05, freq=1)))
    holding = Holding.from_bond(priced)
    assert holding.book * holding.rate > float(holding.flows[0])


def test_scaling_a_holding_scales_book_and_flows_together():
    holding = Holding.from_bond(Bond.at_par(FLAT3, 10, face=100.0))
    before = holding.flows.copy()
    holding.scale(0.25)
    assert holding.book == pytest.approx(25.0)
    assert np.allclose(holding.flows, before * 0.25)


# --- portfolios -------------------------------------------------------------

def test_a_par_ladder_is_worth_what_it_cost():
    ladder = Portfolio.ladder(FLAT3, 20, total=1000.0)
    assert ladder.book_value == pytest.approx(1000.0, rel=1e-13)
    assert ladder.market_value(FLAT3) == pytest.approx(1000.0, rel=1e-12)


def test_a_par_ladder_yields_the_curve():
    assert Portfolio.ladder(FLAT3, 20, 1000.0).book_yield == pytest.approx(
        0.03, rel=1e-12
    )


def test_portfolio_cashflows_add_the_holdings_up():
    ladder = Portfolio.ladder(FLAT3, 3, total=300.0)
    flows = ladder.cashflows()
    assert flows.size == 3
    assert flows.sum() == pytest.approx(
        sum(h.flows.sum() for h in ladder.holdings)
    )


def test_portfolio_refuses_bonds_on_different_frequencies():
    with pytest.raises(ValueError, match="share a frequency"):
        Portfolio.from_bonds([Bond(face=1.0, coupon=0.03, term=5, freq=1),
                              Bond(face=1.0, coupon=0.03, term=5, freq=12)])


def test_portfolio_refuses_a_unit_count_that_does_not_line_up():
    with pytest.raises(ValueError, match="unit counts"):
        Portfolio.from_bonds([Bond(face=1.0, coupon=0.03, term=5)], [1.0, 2.0])


def test_an_empty_portfolio_has_no_yield():
    with pytest.raises(ValueError, match="no yield"):
        Portfolio([], freq=1).book_yield


# --- the default basis ------------------------------------------------------

def test_expected_loss_is_the_rate_net_of_recovery():
    assert DefaultBasis(0.025, 0.40).expected_loss == pytest.approx(0.015)


def test_per_period_default_rates_compound_to_the_annual_one():
    basis = DefaultBasis(0.02, 0.0)
    monthly = basis.per_period(12)
    assert (1.0 - monthly) ** 12 == pytest.approx(1.0 - 0.02, rel=1e-14)


@pytest.mark.parametrize("rate, recovery", [(-0.01, 0.4), (1.0, 0.4),
                                            (0.01, -0.1), (0.01, 1.5)])
def test_default_basis_validates_its_inputs(rate, recovery):
    with pytest.raises(ValueError):
        DefaultBasis(rate, recovery)


def test_reinvestment_rejects_an_unknown_liquidation_order():
    with pytest.raises(ValueError, match="liquidation order"):
        Reinvestment(term=10, liquidation="cheapest")


def test_reinvestment_rejects_a_zero_term():
    with pytest.raises(ValueError, match="must be positive"):
        Reinvestment(term=0)


# --- the projection identity ------------------------------------------------

def test_a_static_curve_earns_exactly_the_curve():
    """Forward-starting one curve is its arbitrage-free rollforward, so a
    portfolio run against it earns the curve and nothing else."""
    projection = project(Portfolio.ladder(FLAT3, 10, 1000.0), np.zeros(30),
                         FLAT3, reinvestment=Reinvestment(term=10))
    assert np.allclose(projection.earned_rate, 0.03, atol=1e-12)


def test_the_book_identity_holds_period_by_period():
    liabilities = np.concatenate([np.full(5, 40.0), np.full(25, -60.0)])
    projection = project(Portfolio.ladder(FLAT3, 15, 1000.0), liabilities,
                         [FLAT3] + [FLAT7] * 40,
                         reinvestment=Reinvestment(term=15),
                         defaults=DefaultBasis(0.01, 0.4))
    assert projection.reconciles()
    assert np.abs(projection.residual()).max() < 1e-9


def test_the_identity_survives_the_fund_running_out():
    """A shortfall is cash that was demanded and not paid, so it belongs in
    the identity — otherwise insolvent and broken look the same."""
    projection = project(Portfolio.ladder(FLAT3, 10, 100.0),
                         np.full(20, -30.0), FLAT3,
                         reinvestment=Reinvestment(term=10))
    assert projection.exhausted_at is not None
    assert projection.shortfall.sum() > 0.0
    assert projection.reconciles()


def test_closing_book_chains_into_the_next_opening_book():
    projection = project(Portfolio.ladder(FLAT3, 10, 1000.0),
                         np.full(20, -20.0), FLAT3,
                         reinvestment=Reinvestment(term=10))
    assert np.allclose(projection.closing_book[:-1],
                       projection.opening_book[1:], atol=1e-9)


def test_a_projection_rejects_a_curve_on_another_frequency():
    with pytest.raises(ValueError, match="times a year"):
        project(Portfolio.ladder(FLAT3, 5, 100.0), np.zeros(3),
                YieldCurve.flat(0.03, freq=12))


def test_a_monthly_projection_earns_the_monthly_curve():
    monthly = YieldCurve.flat(0.03, freq=12)
    projection = project(Portfolio.ladder(monthly, 120, 1000.0),
                         np.zeros(240), monthly,
                         reinvestment=Reinvestment(term=120))
    assert np.allclose(projection.annual_earned_rate(12), 0.03, atol=1e-12)
    assert np.allclose(earned_rates(projection, 12), 0.03, atol=1e-12)


# --- the portfolio rate lags the market -------------------------------------

def test_the_earned_rate_lags_a_rate_rise():
    """The number RFC-010's portfolio crediting mode has been taking as an
    input. A fund does not adopt a new rate; it converges on one."""
    projection = project(Portfolio.ladder(FLAT3, 10, 1000.0), np.zeros(40),
                         [FLAT3] + [FLAT7] * 45,
                         reinvestment=Reinvestment(term=10))
    rates = projection.earned_rate
    assert rates[0] == pytest.approx(0.03, abs=1e-12)
    assert rates[1] < 0.04
    assert rates[10] == pytest.approx(0.07, abs=1e-9)


def test_a_longer_portfolio_lags_further():
    short = project(Portfolio.ladder(FLAT3, 10, 1000.0), np.zeros(40),
                    [FLAT3] + [FLAT7] * 45,
                    reinvestment=Reinvestment(term=10))
    long = project(Portfolio.ladder(FLAT3, 20, 1000.0), np.zeros(40),
                   [FLAT3] + [FLAT7] * 45,
                   reinvestment=Reinvestment(term=20))
    assert long.earned_rate[5] < short.earned_rate[5]
    assert half_life(long.earned_rate, 0.03, 0.07) > \
        half_life(short.earned_rate, 0.03, 0.07)


def test_half_life_finds_the_midpoint_crossing():
    assert half_life([0.03, 0.04, 0.05, 0.06], 0.03, 0.07) == 2
    assert half_life([0.05, 0.04, 0.03, 0.01], 0.05, 0.01) == 2


def test_half_life_reports_a_gap_that_never_closes():
    assert half_life([0.03, 0.031, 0.032], 0.03, 0.07) is None


def test_half_life_refuses_a_gap_that_does_not_exist():
    with pytest.raises(ValueError, match="no gap"):
        half_life([0.03], 0.03, 0.03)


def test_a_falling_market_takes_years_to_reach_the_crediting_floor():
    """New money is under a 3% guarantee from day one; the portfolio is not.
    An office pricing the floor off new-money rates mis-times it by years."""
    high, low = YieldCurve.flat(0.05, freq=1), YieldCurve.flat(0.01, freq=1)
    projection = project(Portfolio.ladder(high, 10, 1000.0), np.zeros(40),
                         [high] + [low] * 45,
                         reinvestment=Reinvestment(term=10))
    below = np.flatnonzero(projection.earned_rate < 0.03)
    assert below[0] == 5


# --- book against market ----------------------------------------------------

def test_a_rate_rise_opens_a_gap_between_book_and_market():
    projection = project(Portfolio.ladder(FLAT3, 20, 1000.0), np.zeros(10),
                         [FLAT3] + [FLAT7] * 15,
                         reinvestment=Reinvestment(term=20))
    assert projection.opening_market[0] == pytest.approx(
        projection.opening_book[0], rel=1e-12
    )
    assert projection.closing_book[0] == pytest.approx(1030.0, rel=1e-12)
    assert projection.closing_market[0] == pytest.approx(782.45, abs=0.01)
    assert projection.unrealised_gain()[0] == pytest.approx(-247.55, abs=0.01)


def test_the_gap_closes_again_as_the_portfolio_turns_over():
    projection = project(Portfolio.ladder(FLAT3, 20, 1000.0), np.zeros(40),
                         [FLAT3] + [FLAT7] * 45,
                         reinvestment=Reinvestment(term=20))
    gap = projection.unrealised_gain() / projection.closing_book
    assert gap[0] < -0.15
    assert abs(gap[-1]) < 1e-9


# --- the credit spread is not income ----------------------------------------

def test_the_breakeven_spread_exceeds_the_expected_loss():
    basis = DefaultBasis(0.025, 0.40)
    spread = breakeven_spread(basis, 0.03)
    assert spread == pytest.approx(0.0161538, abs=1e-6)
    assert spread > basis.expected_loss


def test_a_spread_equal_to_expected_loss_still_loses_money():
    """It misses the coupon the defaulted capital would have paid — exactly
    the default rate times the book yield."""
    basis = DefaultBasis(0.025, 0.40)
    spread = basis.expected_loss
    risky = project(Portfolio.ladder(FLAT3, 10, 1000.0, spread=spread),
                    np.zeros(30), FLAT3,
                    reinvestment=Reinvestment(term=10, spread=spread),
                    defaults=basis)
    shortfall = 0.03 - risky.earned_rate[0]
    assert shortfall == pytest.approx(0.025 * 0.045, abs=1e-6)


def test_the_breakeven_spread_nets_back_to_the_risk_free_rate():
    basis = DefaultBasis(0.025, 0.40)
    spread = breakeven_spread(basis, 0.03)
    risky = project(Portfolio.ladder(FLAT3, 10, 1000.0, spread=spread),
                    np.zeros(30), FLAT3,
                    reinvestment=Reinvestment(term=10, spread=spread),
                    defaults=basis)
    assert np.allclose(risky.earned_rate, 0.03, atol=1e-9)
    assert risky.book_yield[0] > 0.04
    assert risky.reconciles()


def test_the_breakeven_spread_nets_back_monthly_too():
    monthly = YieldCurve.flat(0.03, freq=12)
    basis = DefaultBasis(0.02, 0.35)
    spread = breakeven_spread(basis, 0.03, 12)
    risky = project(Portfolio.ladder(monthly, 120, 1000.0, spread=spread),
                    np.zeros(240), monthly,
                    reinvestment=Reinvestment(term=120, spread=spread),
                    defaults=basis)
    assert np.allclose(risky.annual_earned_rate(12), 0.03, atol=1e-9)


def test_defaults_take_the_coupon_with_them():
    """A holding that fails does not pay the coupon it failed on. Booking
    the coupon first collects income from bonds that did not survive."""
    basis = DefaultBasis(0.50, 0.0)
    projection = project(Portfolio.ladder(FLAT3, 4, 1000.0), np.zeros(4),
                         FLAT3, reinvestment=Reinvestment(term=4),
                         defaults=basis)
    surviving = 1000.0 * (1.0 - basis.per_period(1))
    assert projection.investment_income[0] == pytest.approx(
        surviving * 0.03, rel=1e-10
    )
    assert projection.default_loss[0] == pytest.approx(500.0, rel=1e-10)


# --- forced sales -----------------------------------------------------------

def _spike_projection(order):
    liabilities = np.full(30, -40.0)
    liabilities[0] = -340.0
    return project(Portfolio.ladder(FLAT3, 20, 1000.0), liabilities,
                   [FLAT3] + [FLAT7] * 40,
                   reinvestment=Reinvestment(term=20, liquidation=order))


def test_every_liquidation_order_raises_the_same_cash():
    raised = {order: _spike_projection(order).sold[0]
              for order in LIQUIDATION_ORDERS}
    assert set(np.round(list(raised.values()), 9)) == {260.0}


def test_liquidation_order_moves_the_realised_loss_by_a_factor_of_four():
    """Nothing defaulted. Same portfolio, same cash raised, and the loss
    booked in the year ranges from 34 to 151."""
    losses = {order: _spike_projection(order).realised_gain[0]
              for order in LIQUIDATION_ORDERS}
    assert losses["shortest"] == pytest.approx(-34.12, abs=0.01)
    assert losses["pro_rata"] == pytest.approx(-91.63, abs=0.01)
    assert losses["longest"] == pytest.approx(-150.74, abs=0.01)


def test_selling_short_holdings_first_leaves_the_fund_earning_less():
    """The loss is timing, not value: take it now or earn less later."""
    yields = {order: _spike_projection(order).book_yield[10]
              for order in LIQUIDATION_ORDERS}
    assert yields["shortest"] == pytest.approx(0.0300, abs=5e-5)
    assert yields["pro_rata"] == pytest.approx(0.0375, abs=5e-5)
    assert yields["longest"] == pytest.approx(0.0595, abs=5e-5)


def test_liquidation_order_changes_nothing_cumulative():
    """The whole difference washes out by the time the pre-existing ladder
    has run off — to floating point, not to a tolerance."""
    liabilities = np.zeros(40)
    liabilities[0] = -300.0
    totals = {}
    for order in LIQUIDATION_ORDERS:
        projection = project(
            Portfolio.ladder(FLAT3, 20, 1000.0), liabilities,
            [FLAT3] + [FLAT7] * 45,
            reinvestment=Reinvestment(term=20, liquidation=order),
        )
        totals[order] = np.cumsum(projection.net_investment_income)
    assert totals["shortest"][0] - totals["longest"][0] > 100.0
    for order in ("pro_rata", "longest"):
        assert np.allclose(totals[order][19:], totals["shortest"][19:],
                           atol=1e-8)


def test_a_forced_sale_raises_exactly_the_cash_it_needed():
    """260 of outgo the coupons could not cover, and 260 sold — no more, so
    a fund under pressure does not quietly de-risk itself as well."""
    projection = _spike_projection("pro_rata")
    assert projection.sold[0] == pytest.approx(260.0, rel=1e-10)
    assert projection.purchased[0] == 0.0
    assert projection.reconciles()


# --- what the rest of the platform wanted -----------------------------------

def test_the_earned_rate_feeds_a_principle_based_reserve():
    """RFC-016 takes earned rates as an input. This is where they come from."""
    liabilities = np.full(20, -50.0)
    projection = project(Portfolio.ladder(FLAT3, 10, 1000.0), liabilities,
                         [FLAT3] + [FLAT7] * 25,
                         reinvestment=Reinvestment(term=10))
    surplus = accumulated_surplus(
        liabilities.reshape(-1, 1), projection.earned_rate.reshape(-1, 1),
        starting_assets=1000.0,
    )
    assert surplus.shape == (21, 1)
    assert surplus[-1, 0] > 0.0


def test_a_purchase_and_a_sale_never_happen_in_the_same_period():
    projection = project(Portfolio.ladder(FLAT3, 10, 1000.0),
                         np.concatenate([np.full(10, 50.0),
                                         np.full(10, -150.0)]),
                         FLAT3, reinvestment=Reinvestment(term=10))
    assert not np.any((projection.purchased > 0.0) & (projection.sold > 0.0))


def test_a_projection_with_no_liabilities_only_ever_buys():
    projection = project(Portfolio.ladder(FLAT3, 10, 1000.0), np.zeros(25),
                         FLAT3, reinvestment=Reinvestment(term=10))
    assert np.all(projection.sold == 0.0)
    assert np.all(projection.realised_gain == 0.0)
    assert projection.exhausted_at is None
