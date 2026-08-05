"""Solvency II: stresses, aggregation and the risk margin.

The measurements are the point: which lapse stress bites is a property of
the product and not of the standard, and the aggregation rules have failure
modes that produce numbers rather than errors.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from engine.core.runner import run
from engine.core.stochastic import run_stochastic
from engine.data.assumptions import Assumptions, MortalityTable
from engine.data.expenses import ExpenseScale, Expenses
from engine.data.modelpoints import ModelPoint
from engine.data.rates import YieldCurve
from engine.data.scenarios import ScenarioSet
from engine.library.term_life import TermLife
from engine.library.unit_linked import UnitLinkedGMDB
from engine.report.solvency2 import (
    COST_OF_CAPITAL, LAPSE_SHOCKS, STANDARD_SHOCKS, CorrelationMatrix,
    RiskMargin, ScaledMortality, SolvencyPosition, Stress,
    capital_requirements, diversification_benefit, lapse_module,
    stressed_liabilities,
)

RISING = MortalityTable(
    {age: min(0.0004 * 1.09 ** (age - 30), 1.0) for age in range(0, 121)}
)
FLAT3 = YieldCurve.flat(0.03, freq=1, horizon_years=60)


def protection_basis():
    return Assumptions(
        mortality=RISING, lapse=0.06, interest=0.03,
        expenses=Expenses(initial=ExpenseScale(per_policy=200.0),
                          renewal=ExpenseScale(per_policy=35.0),
                          claim=ExpenseScale(per_policy=250.0),
                          inflation=0.02),
    )


def protection_points():
    return [ModelPoint(id=1, age_at_entry=45, term_years=25,
                       sum_assured=200_000.0, annual_premium=900.0,
                       init_pols=1000.0)]


def protection_bel(points, assumptions):
    result = run(TermLife, points, assumptions, 26,
                 outputs=["claims", "expenses", "premiums", "claim_expenses"])
    i = assumptions.interest
    out = sum(
        (result.aggregate("claims")[t] + result.aggregate("expenses")[t]
         + result.aggregate("claim_expenses")[t]) * (1 + i) ** -(t + 1)
        for t in range(26)
    )
    inflow = sum(result.aggregate("premiums")[t] * (1 + i) ** -t
                 for t in range(26))
    return out - inflow


SAVINGS_SCENARIOS = ScenarioSet.lognormal(200, 21, drift=math.log(1.05),
                                          vol=0.16, seed=4)


def savings_basis():
    return Assumptions(
        mortality=RISING, lapse=0.06, interest=0.03, amc=0.015,
        expenses=Expenses(renewal=ExpenseScale(per_policy=40.0),
                          inflation=0.02),
    )


def savings_points():
    return [ModelPoint(id=1, age_at_entry=50, term_years=20,
                       premium=50_000.0, gmdb_guarantee=50_000.0,
                       init_pols=1000.0)]


def savings_bel(points, assumptions):
    result = run_stochastic(UnitLinkedGMDB, points, assumptions,
                            SAVINGS_SCENARIOS, 20,
                            outputs=["gmdb_strain", "fee_income"])
    v = np.array([(1 + assumptions.interest) ** -(t + 1) for t in range(20)])
    strain = (result.array("gmdb_strain")[:20].sum(axis=1) * v[:, None]).sum(0)
    fees = (result.array("fee_income")[:20].sum(axis=1) * v[:, None]).sum(0)
    return float(strain.mean() - fees.mean())


def lapse_capitals(liability, points, assumptions):
    shocks = [Stress.standard(name) for name in LAPSE_SHOCKS]
    return capital_requirements(
        stressed_liabilities(liability, points, assumptions, shocks)
    )


# --- stresses are transformations, not mutations -------------------------


def test_the_base_stress_is_the_identity():
    assumptions = protection_basis()
    base = Stress.base()
    assert not base
    assert base.apply(assumptions).mortality is assumptions.mortality
    assert base.apply(assumptions).lapse == assumptions.lapse
    assert protection_bel(base.apply_to_points(protection_points()),
                          base.apply(assumptions)) == pytest.approx(
        protection_bel(protection_points(), assumptions), rel=1e-15)


def test_applying_a_stress_leaves_the_base_basis_alone():
    assumptions = protection_basis()
    before = (assumptions.lapse, assumptions.mortality,
              assumptions.expenses.renewal.per_policy)
    Stress.standard("lapse_up").apply(assumptions)
    Stress.standard("expense").apply(assumptions)
    Stress.standard("mortality").apply(assumptions)
    assert (assumptions.lapse, assumptions.mortality,
            assumptions.expenses.renewal.per_policy) == before


def test_a_stress_changes_only_what_it_names():
    assumptions = protection_basis()
    stressed = Stress.standard("mortality").apply(assumptions)
    assert stressed.lapse == assumptions.lapse
    assert stressed.expenses is assumptions.expenses
    assert stressed.interest == assumptions.interest
    assert stressed.mortality is not assumptions.mortality


def test_the_mortality_shock_scales_the_annual_rate_not_the_split():
    """The Delegated Regulation shocks "the mortality rates used", which are
    annual. Scaling a periodic rate instead stresses the sub-annual split
    as well, and gives a different answer.

    The two coincide exactly at the first sub-period, where the UDD
    denominator ``1 - (k/m) q`` is 1 — which is why testing there proves
    nothing. They separate through the year and by more at heavier ages:
    at 85, the twelfth month of a 15% stress differs by 66 basis points of
    itself between the two orders.
    """
    scaled = ScaledMortality(RISING, 1.15)
    assert scaled.q_at(60) == pytest.approx(1.15 * RISING.q_at(60), rel=1e-15)
    assert scaled.periodic_rate(60, 0, 12) == pytest.approx(
        1.15 * RISING.q_at(60) / 12.0, rel=1e-15)
    # First sub-period: identical, because the denominator is one.
    assert scaled.periodic_rate(60, 0, 12) == pytest.approx(
        1.15 * RISING.periodic_rate(60, 0, 12), rel=1e-15)
    # Last sub-period at a heavy age: they part company.
    stress_then_split = float(scaled.periodic_rate(85, 11, 12))
    split_then_stress = 1.15 * float(RISING.periodic_rate(85, 11, 12))
    assert stress_then_split > split_then_stress
    assert stress_then_split / split_then_stress == pytest.approx(1.0066,
                                                                 abs=5e-4)


def test_a_scaled_rate_cannot_exceed_one():
    scaled = ScaledMortality(MortalityTable.flat(0.9), 1.15)
    assert scaled.q_at(50) == 1.0


def test_the_catastrophe_shock_is_absolute_and_lasts_one_year():
    """0.15 percentage points added to q, in the first year only. Absolute
    and not relative, because a pandemic does not scale with a life's
    underlying mortality."""
    shock = Stress.standard("cat")
    stressed = shock.apply(protection_basis()).mortality
    base_q = RISING.q_at(40, year=0)
    assert stressed.q_at(40, year=0) == pytest.approx(base_q + 0.0015)
    assert stressed.q_at(40, year=1) == pytest.approx(base_q)


def test_the_expense_shock_scales_every_loading_on_every_basis():
    """Missing one would understate the module by whatever share of the
    expense base it happens to be."""
    assumptions = Assumptions(
        mortality=RISING, interest=0.03,
        expenses=Expenses(
            initial=ExpenseScale(per_policy=100.0, percent_premium=0.05),
            renewal=ExpenseScale(per_policy=20.0, per_mille_sum_assured=0.4),
            claim=ExpenseScale(per_policy=300.0), inflation=0.02),
    )
    stressed = Stress.standard("expense").apply(assumptions).expenses
    assert stressed.initial.per_policy == pytest.approx(110.0)
    assert stressed.initial.percent_premium == pytest.approx(0.055)
    assert stressed.renewal.per_policy == pytest.approx(22.0)
    assert stressed.renewal.per_mille_sum_assured == pytest.approx(0.44)
    assert stressed.claim.per_policy == pytest.approx(330.0)
    assert stressed.inflation == pytest.approx(0.03)


def test_a_mass_lapse_changes_the_book_and_not_a_rate():
    """Every other shock is a change of assumption. A mass lapse is an
    event, so what changes is how many policies there are."""
    points = protection_points()
    shocked = Stress.standard("mass_lapse").apply_to_points(points)
    assert shocked[0].init_pols == pytest.approx(600.0)
    assert points[0].init_pols == 1000.0            # untouched
    assert Stress.standard("mass_lapse").apply(
        protection_basis()).lapse == 0.06


def test_a_stress_without_a_mass_lapse_returns_the_points_unchanged():
    points = protection_points()
    same = Stress.standard("mortality").apply_to_points(points)
    assert same[0] is points[0]


def test_an_unknown_standard_shock_is_refused():
    with pytest.raises(ValueError, match="unknown standard shock"):
        Stress.standard("hurricane")


def test_a_mass_lapse_of_everything_is_refused():
    with pytest.raises(ValueError, match="outside"):
        Stress("silly", mass_lapse=1.0)


# --- the finding: which lapse shock bites is the product's business ------


def test_protection_fears_lapse_down_and_savings_fears_lapse_up():
    """The same shock, opposite signs, on two books at the same insurer.

    A protection book loses money when policies *stay*, because more of
    them survive to claim. A savings book loses money when they *go*,
    because the charges that pay for the guarantee walk out with them.
    Neither is a rule of the standard — the standard applies all three and
    takes the worst.
    """
    protection = lapse_capitals(protection_bel, protection_points(),
                                protection_basis())
    savings = lapse_capitals(savings_bel, savings_points(), savings_basis())

    assert protection["lapse_down"] > 0.0
    assert protection["lapse_up"] == 0.0
    assert savings["lapse_up"] > 0.0
    assert savings["lapse_down"] == 0.0


def test_the_lapse_module_is_the_worst_shock_and_not_their_sum():
    """A book cannot simultaneously lapse more and lapse less. Adding them
    would overstate the module by more than half on this book."""
    capitals = lapse_capitals(protection_bel, protection_points(),
                              protection_basis())
    module, which = lapse_module(capitals)
    total = sum(capitals[name] for name in LAPSE_SHOCKS)
    assert module == max(capitals[name] for name in LAPSE_SHOCKS)
    assert which in LAPSE_SHOCKS
    assert total > 1.5 * module


def test_a_mass_discontinuance_bites_on_a_profitable_book_either_way():
    """Worth stating because it cuts across the direction above: where the
    best estimate is *negative* — a book that is an asset — losing 40% of
    it at a stroke destroys future profit whichever way the rates were
    going to move."""
    for liability, points, basis in (
        (protection_bel, protection_points(), protection_basis()),
        (savings_bel, savings_points(), savings_basis()),
    ):
        assert liability(points, basis) < 0.0
        assert lapse_capitals(liability, points, basis)["mass_lapse"] > 0.0


def test_no_lapse_shocks_at_all_is_an_error_and_not_a_zero():
    with pytest.raises(ValueError, match="no lapse shocks"):
        lapse_module({"mortality": 10.0})


# --- capital requirements ------------------------------------------------


def test_capital_is_the_increase_in_the_liability_floored_at_zero():
    """A shock that makes a book more valuable does not release capital:
    the SCR is the loss in the 99.5% scenario, and a gain is not a
    negative loss."""
    capitals = capital_requirements({"base": 100.0, "up": 150.0, "down": 60.0})
    assert capitals == {"up": 50.0, "down": 0.0}


def test_longevity_releases_capital_on_a_protection_book():
    """People living longer is good news for a term assurance, so the
    module is zero rather than negative."""
    shocks = [Stress.standard("longevity"), Stress.standard("mortality")]
    capitals = capital_requirements(stressed_liabilities(
        protection_bel, protection_points(), protection_basis(), shocks))
    assert capitals["longevity"] == 0.0
    assert capitals["mortality"] > 0.0


def test_every_standard_shock_runs_end_to_end():
    shocks = [Stress.standard(name) for name in STANDARD_SHOCKS]
    liabilities = stressed_liabilities(protection_bel, protection_points(),
                                       protection_basis(), shocks)
    assert set(liabilities) == set(STANDARD_SHOCKS) | {"base"}
    assert all(np.isfinite(v) for v in liabilities.values())


# --- aggregation ---------------------------------------------------------


def test_the_standard_matrix_is_valid():
    matrix = CorrelationMatrix.life_underwriting()
    assert matrix.risks == ("mortality", "longevity", "lapse", "expense", "cat")
    assert np.linalg.eigvalsh(matrix.matrix).min() > 0.0


def test_a_matrix_that_is_not_positive_semi_definite_is_refused():
    """The failure mode that produces a number rather than an error.

    This one is symmetric, has a unit diagonal, and every entry lies inside
    [-1, 1]. It is also not positive semi-definite, and three modules of
    100 each give ``v' C v = -24,000`` — so the square root is undefined
    and any floor at zero reports **no capital requirement at all** for a
    book with three material risks.
    """
    bad = [[1.0, -0.9, -0.9], [-0.9, 1.0, -0.9], [-0.9, -0.9, 1.0]]
    v = np.array([100.0, 100.0, 100.0])
    assert v @ np.array(bad) @ v == pytest.approx(-24_000.0)
    with pytest.raises(ValueError, match="positive semi-definite"):
        CorrelationMatrix(("a", "b", "c"), bad)


def test_an_asymmetric_matrix_is_refused():
    with pytest.raises(ValueError, match="not symmetric"):
        CorrelationMatrix(("a", "b"), [[1.0, 0.5], [0.2, 1.0]])


def test_a_non_unit_diagonal_is_refused():
    with pytest.raises(ValueError, match="all ones"):
        CorrelationMatrix(("a", "b"), [[1.0, 0.5], [0.5, 0.9]])


def test_a_correlation_outside_minus_one_to_one_is_refused():
    with pytest.raises(ValueError, match=r"\[-1, 1\]"):
        CorrelationMatrix(("a", "b"), [[1.0, 1.5], [1.5, 1.0]])


def test_duplicate_risk_names_are_refused():
    with pytest.raises(ValueError, match="distinct"):
        CorrelationMatrix(("a", "a"), np.eye(2))


def test_a_typo_in_a_module_name_raises_rather_than_dropping_a_risk():
    matrix = CorrelationMatrix.life_underwriting()
    with pytest.raises(ValueError, match="not risks of this matrix"):
        matrix.aggregate({"mortalty": 100.0})


def test_a_risk_that_was_not_measured_contributes_nothing():
    matrix = CorrelationMatrix.life_underwriting()
    assert matrix.aggregate({"mortality": 100.0}) == pytest.approx(100.0)


def test_perfect_correlation_gives_no_diversification():
    matrix = CorrelationMatrix(("a", "b"), [[1.0, 1.0], [1.0, 1.0]])
    values = {"a": 60.0, "b": 40.0}
    assert matrix.aggregate(values) == pytest.approx(100.0)
    assert diversification_benefit(values, 100.0) == pytest.approx(0.0)


def test_independence_gives_the_pythagorean_answer():
    matrix = CorrelationMatrix(("a", "b"), np.eye(2))
    assert matrix.aggregate({"a": 30.0, "b": 40.0}) == pytest.approx(50.0)


def test_the_standard_matrix_gives_a_material_diversification_benefit():
    """Measured on the protection book: the modules add to more than half
    again what the aggregate comes to."""
    shocks = [Stress.standard(name) for name in STANDARD_SHOCKS]
    capitals = capital_requirements(stressed_liabilities(
        protection_bel, protection_points(), protection_basis(), shocks))
    lapse, _ = lapse_module(capitals)
    modules = {"mortality": capitals["mortality"],
               "longevity": capitals["longevity"], "lapse": lapse,
               "expense": capitals["expense"], "cat": capitals["cat"]}
    scr = CorrelationMatrix.life_underwriting().aggregate(modules)
    benefit = diversification_benefit(modules, scr)
    assert scr < sum(modules.values())
    assert scr > max(modules.values())
    assert 0.25 < benefit < 0.40


# --- the risk margin -----------------------------------------------------


def test_the_risk_margin_is_cost_of_capital_on_a_run_off():
    margin = RiskMargin([100.0, 50.0], cost_of_capital=0.06)
    value = margin.value(1000.0, YieldCurve.flat(0.0, freq=1, horizon_years=10))
    # SCRs of 1000 and 500, undiscounted, at 6%.
    assert value == pytest.approx(0.06 * 1500.0)


def test_the_projected_scr_runs_off_with_the_driver():
    margin = RiskMargin([200.0, 150.0, 50.0, 0.0])
    assert margin.projected_scr(80.0) == pytest.approx([80.0, 60.0, 20.0, 0.0])


def test_the_driver_choice_moves_the_margin():
    """A run-off in proportion to the best estimate and one in proportion
    to the sum at risk are different numbers for the same book, and
    neither is more correct in general — which is why the driver is the
    caller's and not this module's."""
    by_bel = RiskMargin([100.0, 80.0, 60.0, 40.0, 20.0])
    by_count = RiskMargin([100.0, 95.0, 90.0, 85.0, 80.0])
    a = by_bel.value(1000.0, FLAT3)
    b = by_count.value(1000.0, FLAT3)
    assert b / a == pytest.approx(1.476, abs=0.01)


def test_the_default_cost_of_capital_is_the_prescribed_six_percent():
    assert COST_OF_CAPITAL == 0.06
    assert RiskMargin([1.0]).cost_of_capital == 0.06


def test_a_driver_that_starts_at_zero_is_refused():
    with pytest.raises(ValueError, match="nothing to scale"):
        RiskMargin([0.0, 1.0])


def test_a_negative_driver_is_refused():
    with pytest.raises(ValueError, match="cannot go negative"):
        RiskMargin([1.0, -1.0])


# --- the position --------------------------------------------------------


def test_the_position_reports_a_ratio_a_regulator_would_read():
    position = SolvencyPosition(best_estimate=1000.0, risk_margin=100.0,
                                scr=400.0, assets=2000.0,
                                modules={"mortality": 300.0, "lapse": 250.0})
    assert position.technical_provisions == 1100.0
    assert position.own_funds == 900.0
    assert position.solvency_ratio == pytest.approx(2.25)
    assert position.diversification == pytest.approx(1 - 400.0 / 550.0)
    assert "ratio=225.0%" in repr(position)


def test_a_book_with_no_risk_has_an_infinite_ratio_rather_than_a_crash():
    position = SolvencyPosition(best_estimate=0.0, risk_margin=0.0, scr=0.0,
                                assets=100.0, modules={})
    assert position.solvency_ratio == math.inf
    assert position.diversification == 0.0


# --- end to end ----------------------------------------------------------


def test_a_full_standard_formula_position_on_a_projected_book():
    """The whole overlay: project, shock, re-project, aggregate, add a risk
    margin. Every stress is a full re-projection, which is where a
    standard-formula SCR's cost comes from."""
    points, basis = protection_points(), protection_basis()
    shocks = [Stress.standard(name) for name in STANDARD_SHOCKS]
    liabilities = stressed_liabilities(protection_bel, points, basis, shocks)
    capitals = capital_requirements(liabilities)
    lapse, binding = lapse_module(capitals)
    modules = {"mortality": capitals["mortality"],
               "longevity": capitals["longevity"], "lapse": lapse,
               "expense": capitals["expense"], "cat": capitals["cat"]}
    scr = CorrelationMatrix.life_underwriting().aggregate(modules)

    in_force = run(TermLife, points, basis, 26, outputs=["pols_if"])
    margin = RiskMargin(np.asarray(in_force.aggregate("pols_if")[:25]))
    position = SolvencyPosition(
        best_estimate=liabilities["base"], risk_margin=margin.value(scr, FLAT3),
        scr=scr, assets=0.0, modules=modules, binding_lapse=binding,
    )

    assert position.best_estimate < 0.0        # a profitable book is an asset
    assert position.risk_margin > 0.0
    assert position.own_funds > 0.0
    assert position.solvency_ratio > 0.0
    assert position.binding_lapse in LAPSE_SHOCKS
    assert 0.25 < position.diversification < 0.40


def test_the_stress_passes_through_a_full_basis_and_a_monthly_projection():
    """The stress machinery was built against the unisex ``MortalityTable``;
    this holds it to the full ``MortalityBasis`` — sex-distinct rates and a
    generational improvement scale — on a monthly step. The claims ratio
    lands just under the 15% stress because stressed mortality removes
    exposure earlier, which is the economics and not an error."""
    from engine.data.mortality import MortalityBasis
    from engine.core.runner import run as run_model
    from engine.library.term_life import TermLife

    rates = {"M": {a: min(0.0005 * 1.09 ** (a - 30), 1.0) for a in range(121)},
             "F": {a: min(0.0004 * 1.09 ** (a - 30), 1.0) for a in range(121)}}
    improvement = {"M": {a: 0.01 for a in range(121)},
                   "F": {a: 0.012 for a in range(121)}}
    basis = MortalityBasis(rates, improvement=improvement, year_start=2026)
    scaled = ScaledMortality(basis, 1.15)
    for sex in ("M", "F"):
        assert scaled.q_at(60, sex=sex, year=2030) == pytest.approx(
            1.15 * basis.q_at(60, sex=sex, year=2030), rel=1e-15)

    assumptions = Assumptions(mortality=basis, lapse=0.05, interest=0.03,
                              freq=12)
    point = ModelPoint(id=1, age_at_entry=45, term_years=10,
                       sum_assured=100_000.0, annual_premium=800.0,
                       init_pols=1000.0, sex="F")
    stressed = Stress.standard("mortality").apply(assumptions)
    base = sum(run_model(TermLife, [point], assumptions, 121,
                         outputs=["claims"]).aggregate("claims"))
    shocked = sum(run_model(TermLife, [point], stressed, 121,
                            outputs=["claims"]).aggregate("claims"))
    assert 1.10 < shocked / base < 1.15
