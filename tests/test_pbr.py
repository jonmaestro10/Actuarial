"""US statutory principle-based reserves — VM-20 and VM-21.

The measurements here are about the *statistic*: what a conditional tail
expectation does that a percentile does not, and why the standard uses one
rather than the other.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from engine.report.pbr import (
    CTE_LEVEL, MinimumReserve, accumulated_surplus, cte, deficiency_dates,
    greatest_present_value_of_accumulated_deficiency, path_discount_factors,
    scenario_reserves, stochastic_reserve, tail_count, tail_standard_error,
    value_at_risk,
)


def va_block(seed=7, periods=30, scenarios=4000, guarantee_load=0.20,
             drift=0.045, vol=0.20):
    """A variable-annuity-shaped block: charges on the fund, guarantee
    claims when it falls below the premium."""
    rng = np.random.default_rng(seed)
    returns = rng.normal(drift, vol, (periods, scenarios))
    fund = 100_000.0 * np.cumprod(1.0 + returns, axis=0)
    charges = 0.012 * fund
    claims = np.maximum(100_000.0 - fund, 0.0) * guarantee_load
    return charges - claims, returns


# --- the tail count ------------------------------------------------------


def test_the_tail_count_is_exact_at_every_round_scenario_count():
    """Found by measurement, and it fires at exactly the counts anyone
    actually runs.

    ``1 - 0.70`` is ``0.30000000000000004``, so ``n * (1 - level)`` lands a
    hair above the integer and a naive ceiling takes one scenario too many
    — 301 out of 1,000, 3,001 out of 10,000 — on every single run. The
    error is invisible in the answer, because one extra scenario barely
    moves a CTE.
    """
    assert tail_count(100) == 30
    assert tail_count(1000) == 300
    assert tail_count(2000) == 600
    assert tail_count(4000) == 1200
    assert tail_count(10000) == 3000


def test_a_genuinely_fractional_tail_still_rounds_up():
    """The snap must not swallow a real fraction: 1,001 scenarios give a
    tail of 300.3, and dropping the 301st would drop the worst of it."""
    assert tail_count(1001) == 301
    assert tail_count(7) == 3            # 2.1 -> 3
    assert tail_count(11, 0.90) == 2     # 1.1 -> 2


def test_the_tail_is_never_empty():
    assert tail_count(1) == 1
    assert tail_count(2, 0.99) == 1


def test_an_impossible_level_is_refused():
    with pytest.raises(ValueError, match="outside"):
        tail_count(100, 1.0)
    with pytest.raises(ValueError, match="at least one scenario"):
        tail_count(0)


# --- the finding: a CTE is not a percentile ------------------------------


def test_a_percentile_can_report_no_reserve_where_a_cte_reports_one():
    """The sharpest statement of the difference, and it is not a corner
    case: a guarantee that bites in under 30% of scenarios puts the 70th
    percentile at **exactly zero**, so a value-at-risk measure says hold
    nothing at all while the CTE says hold real money.

    A percentile is a point on the distribution and knows nothing about
    what lies beyond it. A CTE is the mean of everything beyond.
    """
    net, returns = va_block(guarantee_load=0.02)
    reserves = scenario_reserves(net, returns)
    assert (reserves > 0.0).mean() < 0.30
    assert value_at_risk(reserves) == 0.0
    assert cte(reserves) > 20_000.0


def test_even_where_the_percentile_bites_the_cte_is_a_multiple_of_it():
    net, returns = va_block(guarantee_load=0.20)
    reserves = scenario_reserves(net, returns)
    assert value_at_risk(reserves) > 0.0
    assert cte(reserves) / value_at_risk(reserves) == pytest.approx(6.3, abs=0.5)


def test_a_cte_moves_when_the_tail_moves_and_a_percentile_does_not():
    """Which is the property the whole standard rests on. Make the worst
    scenario ten times worse and the percentile does not notice."""
    values = np.arange(100, dtype=np.float64)
    worse = values.copy()
    worse[-1] *= 10.0
    assert value_at_risk(worse) == value_at_risk(values)
    assert cte(worse) > cte(values)


# --- the finding: CTE is coherent, VaR is not ---------------------------


def test_value_at_risk_is_not_subadditive_and_a_cte_is():
    """The mathematical reason the standard prescribes a CTE, demonstrated
    rather than cited.

    Two independent bonds, each defaulting with probability 4% for a loss
    of 100. At the 95% level neither one alone shows any requirement — 96%
    of the time it pays. Put them together and at least one defaults 7.84%
    of the time, so the 95% point *is* a default.

    Value at risk therefore says: nothing, nothing, and one hundred. The
    requirement appears out of diversification, which is the wrong
    direction for a risk measure to move. The CTE at the same level is
    subadditive, as it is at every level and for every pair.
    """
    rng = np.random.default_rng(11)
    n = 400_000
    a = (rng.random(n) < 0.04) * 100.0
    b = (rng.random(n) < 0.04) * 100.0

    assert value_at_risk(a, 0.95) == 0.0
    assert value_at_risk(b, 0.95) == 0.0
    assert value_at_risk(a + b, 0.95) == 100.0
    assert value_at_risk(a + b, 0.95) > value_at_risk(a, 0.95) + value_at_risk(b, 0.95)

    assert cte(a + b, 0.95) <= cte(a, 0.95) + cte(b, 0.95) + 1e-9
    assert cte(a + b, 0.95) > max(cte(a, 0.95), cte(b, 0.95))


def test_the_cte_stays_subadditive_across_levels_and_seeds():
    rng = np.random.default_rng(3)
    for level in (0.0, 0.5, 0.70, 0.9, 0.99):
        a = rng.lognormal(0.0, 1.0, 5000)
        b = rng.lognormal(0.5, 1.5, 5000)
        assert cte(a + b, level) <= cte(a, level) + cte(b, level) + 1e-9


def test_a_cte_at_level_zero_is_the_mean():
    values = np.arange(1000, dtype=np.float64)
    assert cte(values, 0.0) == pytest.approx(values.mean())


def test_a_cte_is_never_below_its_own_percentile():
    rng = np.random.default_rng(5)
    values = rng.normal(0.0, 1.0, 2000)
    for level in (0.5, 0.70, 0.95):
        assert cte(values, level) >= value_at_risk(values, level)


# --- the accumulated deficiency ------------------------------------------


def test_the_surplus_roll_is_cashflow_then_interest():
    net = np.array([[100.0], [50.0]])
    rates = np.array([[0.10], [0.10]])
    surplus = accumulated_surplus(net, rates, starting_assets=10.0)
    assert surplus[0, 0] == 10.0
    assert surplus[1, 0] == pytest.approx(110.0 * 1.10)
    assert surplus[2, 0] == pytest.approx((121.0 + 50.0) * 1.10)


def test_discounting_runs_along_each_scenarios_own_path():
    """A deficiency and its discounting are two halves of one path; using a
    valuation rate for one and a scenario rate for the other would price a
    scenario that does not exist."""
    rates = np.array([[0.10, 0.02], [0.10, 0.02]])
    factors = path_discount_factors(rates)
    assert factors[0] == pytest.approx([1.0, 1.0])
    assert factors[1] == pytest.approx([1 / 1.10, 1 / 1.02])
    assert factors[2] == pytest.approx([1 / 1.21, 1 / 1.02 ** 2])


def test_a_scenario_that_never_goes_underwater_needs_no_reserve():
    """A surplus is not a negative reserve."""
    net = np.full((10, 1), 100.0)
    rates = np.full((10, 1), 0.04)
    assert greatest_present_value_of_accumulated_deficiency(net, rates)[0] == 0.0


def test_the_greatest_deficiency_is_a_maximum_and_not_the_terminal_one():
    """The whole mechanic. A path that dips underwater and recovers still
    needed the money when it dipped, and a terminal measure reports nothing
    at all for it.
    """
    net = np.array([[-500.0], [-300.0], [400.0], [900.0]])
    rates = np.zeros((4, 1))
    surplus = accumulated_surplus(net, rates)
    assert surplus[-1, 0] > 0.0              # ends solvent
    assert surplus.min() < 0.0               # was not, in the middle
    gpvad = greatest_present_value_of_accumulated_deficiency(net, rates)[0]
    assert gpvad == pytest.approx(800.0)
    assert deficiency_dates(net, rates)[0] == 2


def test_the_greatest_deficiency_is_usually_interior_on_a_real_block():
    """Measured, and it is the argument for a maximum over a terminal
    measure: on a block whose guarantee bites mid-life, most of the paths
    that need a reserve need it before the end."""
    net, returns = va_block()
    gpvad = greatest_present_value_of_accumulated_deficiency(net, returns)
    dates = deficiency_dates(net, returns)
    needing = gpvad > 0.0
    interior = (dates[needing] > 0) & (dates[needing] < net.shape[0])
    assert needing.mean() > 0.5
    assert interior.mean() > 0.6


def test_starting_assets_reduce_the_deficiency_and_raise_the_reserve_one_for_one():
    """Two things that sound contradictory and are not: assets on hand make
    a scenario less likely to go underwater, and the reserve is what the
    scenario needs *in total*."""
    net, returns = va_block(scenarios=500)
    bare = scenario_reserves(net, returns, starting_assets=0.0)
    funded = scenario_reserves(net, returns, starting_assets=50_000.0)
    gpvad_bare = greatest_present_value_of_accumulated_deficiency(net, returns)
    gpvad_funded = greatest_present_value_of_accumulated_deficiency(
        net, returns, starting_assets=50_000.0)
    assert (gpvad_funded <= gpvad_bare + 1e-9).all()
    assert (funded >= bare - 1e-9).all()


def test_mismatched_projection_shapes_are_refused():
    with pytest.raises(ValueError, match="different projections"):
        accumulated_surplus(np.zeros((5, 2)), np.zeros((6, 2)))


# --- the stochastic reserve ----------------------------------------------


def test_the_stochastic_reserve_is_the_cte_of_the_scenario_reserves():
    net, returns = va_block(scenarios=500)
    assert stochastic_reserve(net, returns) == pytest.approx(
        cte(scenario_reserves(net, returns)))


def test_the_prescribed_level_is_seventy_percent():
    assert CTE_LEVEL == 0.70


def test_a_stricter_level_never_gives_a_smaller_reserve():
    net, returns = va_block(scenarios=800)
    reserves = [stochastic_reserve(net, returns, level=lv)
                for lv in (0.0, 0.5, 0.70, 0.90)]
    assert reserves == sorted(reserves)


# --- sampling error ------------------------------------------------------


def test_the_tail_standard_error_falls_with_the_tail_and_not_the_run():
    """The number that decides how many scenarios a run needs. A CTE(70)
    over 1,000 scenarios is an average of **300**, so precision improves
    like ``1/sqrt(n * (1 - level))``: quadrupling the run halves the error,
    and no amount of scenarios helps a level so deep the tail is a handful.
    """
    rng = np.random.default_rng(2)
    errors = {}
    for n in (1000, 4000, 16000):
        values = rng.lognormal(0.0, 1.0, n)
        errors[n] = tail_standard_error(values)
    assert errors[4000] == pytest.approx(errors[1000] / 2.0, rel=0.35)
    assert errors[16000] == pytest.approx(errors[1000] / 4.0, rel=0.35)


def test_a_tail_of_one_has_no_measurable_error():
    """Not zero — unknowable. One observation has no standard error, and
    reporting zero would say the opposite of the truth."""
    assert tail_standard_error(np.arange(10.0), level=0.95) == math.inf


# --- the three-way maximum ----------------------------------------------


def test_the_reserve_is_the_greatest_of_its_components():
    reserve = MinimumReserve(net_premium=800.0, deterministic=1200.0,
                             stochastic=1000.0)
    assert reserve.value == 1200.0
    assert reserve.binding == "deterministic"
    assert reserve.headroom() == {"net_premium": 400.0, "deterministic": 0.0,
                                  "stochastic": 200.0}


def test_moving_a_component_that_does_not_bind_changes_nothing():
    """Which is why "our stochastic reserve fell" is a claim to check
    against the binding component before acting on it."""
    before = MinimumReserve(net_premium=800.0, deterministic=1200.0,
                            stochastic=1000.0)
    after = MinimumReserve(net_premium=800.0, deterministic=1200.0,
                           stochastic=100.0)
    assert after.value == before.value
    assert after.binding == before.binding


def test_an_excluded_component_is_simply_absent():
    reserve = MinimumReserve(net_premium=900.0, deterministic=700.0)
    assert reserve.value == 900.0
    assert "stochastic" not in reserve.components


def test_a_reserve_with_every_component_excluded_is_an_error():
    with pytest.raises(ValueError, match="missing calculation, not a zero"):
        MinimumReserve()


def test_the_repr_names_the_binding_component():
    assert "stochastic" in repr(MinimumReserve(net_premium=1.0, stochastic=2.0))


# --- end to end on a projected block ------------------------------------


def test_a_projected_variable_annuity_reserves_end_to_end():
    """The overlay on a real run: charges and guarantee strain out of the
    unit-linked template, reduced to one statutory number."""
    from engine.core.stochastic import run_stochastic
    from engine.data.assumptions import Assumptions, MortalityTable
    from engine.data.modelpoints import ModelPoint
    from engine.data.scenarios import ScenarioSet
    from engine.library.unit_linked import UnitLinkedGMDB

    mortality = MortalityTable(
        {age: min(0.0004 * 1.09 ** (age - 30), 1.0) for age in range(0, 121)}
    )
    assumptions = Assumptions(mortality=mortality, lapse=0.05, interest=0.03,
                              amc=0.015)
    points = [ModelPoint(id=1, age_at_entry=55, term_years=20,
                         premium=100_000.0, gmdb_guarantee=100_000.0,
                         init_pols=1000.0)]
    scenarios = ScenarioSet.lognormal(600, 21, drift=math.log(1.05), vol=0.20,
                                      seed=13)
    result = run_stochastic(UnitLinkedGMDB, points, assumptions, scenarios, 20,
                            outputs=["fee_income", "gmdb_strain"])

    net = (result.array("fee_income")[:20].sum(axis=1)
           - result.array("gmdb_strain")[:20].sum(axis=1))
    returns = scenarios.series(scenarios.primary).T[:20]

    reserves = scenario_reserves(net, returns)
    stochastic = cte(reserves)
    assert stochastic > 0.0
    assert stochastic >= value_at_risk(reserves)
    assert tail_count(600) == 180
    assert np.isfinite(tail_standard_error(reserves))

    minimum = MinimumReserve(net_premium=0.0, deterministic=stochastic * 0.4,
                             stochastic=stochastic)
    assert minimum.binding == "stochastic"
    assert minimum.value == pytest.approx(stochastic)
