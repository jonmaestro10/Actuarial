"""Actual against expected: analysis of surplus and where a variance lands."""

from itertools import permutations

import numpy as np
import pytest

from engine.core.runner import run
from engine.data.assumptions import Assumptions, MortalityTable
from engine.data.expenses import Expenses, ExpenseScale
from engine.data.modelpoints import from_dicts
from engine.library.term_life import TermLife
from engine.report.experience import (
    ALLOCATIONS,
    Attribution,
    MAX_DRIVERS,
    SERVICE_PERIODS,
    allocate,
    cached,
    contribution_range,
    cross_terms,
    isolated,
    order_sensitivity,
    sequential,
    shapley,
    swap,
    to_table,
)


DRIVERS = ("mortality", "lapse", "interest", "expenses")


# --- the allocation methods, on functions whose answers are known -----------

def additive(active):
    """No interaction at all: the three methods must agree exactly."""
    return sum({"a": 10.0, "b": 20.0, "c": 30.0}[name] for name in active)


def multiplicative(active):
    """Pure interaction: 100 grown by each factor that is switched on."""
    value = 100.0
    for name in active:
        value *= {"a": 1.1, "b": 1.2, "c": 1.5}[name]
    return value


def test_the_methods_agree_exactly_when_nothing_interacts():
    names = ("a", "b", "c")
    expected = {"a": 10.0, "b": 20.0, "c": 30.0}
    for method in (sequential(additive, names), isolated(additive, names),
                   shapley(additive, names)):
        for name in names:
            assert method.contributions[name] == pytest.approx(
                expected[name], rel=1e-12
            )
        assert method.residual == pytest.approx(0.0, abs=1e-12)


def test_ordering_cannot_matter_without_interaction():
    forward = sequential(additive, ("a", "b", "c")).contributions
    backward = sequential(additive, ("c", "b", "a")).contributions
    assert forward == backward


def test_a_sequential_analysis_always_adds_up():
    """Which is exactly why it is the commonest one, and exactly why its
    dependence on the order goes unremarked."""
    for order in (("a", "b", "c"), ("c", "a", "b"), ("b", "c", "a")):
        assert sequential(multiplicative, order).reconciles()


def test_but_it_gives_a_different_answer_in_a_different_order():
    first = sequential(multiplicative, ("a", "b", "c")).contributions["a"]
    last = sequential(multiplicative, ("c", "b", "a")).contributions["a"]
    assert first == pytest.approx(10.0, rel=1e-12)
    assert last == pytest.approx(18.0, rel=1e-12)


def test_an_isolated_analysis_does_not_add_up():
    """And the gap is the interaction, reported rather than pushed into
    whichever line happened to be measured last."""
    measured = isolated(multiplicative, ("a", "b", "c"))
    assert not measured.reconciles()
    assert measured.residual == pytest.approx(
        cross_terms(multiplicative, ("a", "b", "c")), rel=1e-12
    )


def test_shapley_adds_up_and_does_not_depend_on_an_order():
    """The two properties the other methods have one each of."""
    measured = shapley(multiplicative, ("a", "b", "c"))
    assert measured.reconciles()
    assert measured.residual == pytest.approx(0.0, abs=1e-12)
    shuffled = shapley(multiplicative, ("c", "a", "b"))
    for name in ("a", "b", "c"):
        assert measured.contributions[name] == pytest.approx(
            shuffled.contributions[name], rel=1e-12
        )


def test_shapley_gives_a_driver_that_changes_nothing_nothing():
    """The null-player property, and the check that a driver which was
    listed by mistake cannot be handed a share of somebody else's result."""
    def with_a_dead_driver(active):
        return multiplicative(set(active) - {"dead"})

    measured = shapley(with_a_dead_driver, ("a", "b", "c", "dead"))
    assert measured.contributions["dead"] == pytest.approx(0.0, abs=1e-12)
    assert measured.reconciles()


def test_shapley_gives_two_interchangeable_drivers_the_same_share():
    """Symmetry. A sequential analysis breaks it on the ordering alone."""
    def twins(active):
        value = 100.0
        for name in active:
            value *= {"a": 1.3, "b": 1.3}[name]
        return value

    measured = shapley(twins, ("a", "b"))
    assert measured.contributions["a"] == pytest.approx(
        measured.contributions["b"], rel=1e-12
    )
    peeled = sequential(twins, ("a", "b")).contributions
    assert peeled["a"] != pytest.approx(peeled["b"], rel=1e-6)


def test_shapley_sits_inside_the_range_the_orderings_span():
    values = shapley(multiplicative, ("a", "b", "c")).contributions
    for name, (low, high) in contribution_range(
            multiplicative, ("a", "b", "c")).items():
        assert low <= values[name] <= high


def test_the_range_is_exactly_what_the_orderings_reach():
    """Every subset is some ordering's prefix, so the subset sweep is the
    exact range rather than a bound on it."""
    names = ("a", "b", "c")
    spans = contribution_range(multiplicative, names)
    seen = {name: [] for name in names}
    for order in permutations(names):
        for name, value in sequential(multiplicative, order).contributions.items():
            seen[name].append(value)
    for name in names:
        assert min(seen[name]) == pytest.approx(spans[name][0], rel=1e-12)
        assert max(seen[name]) == pytest.approx(spans[name][1], rel=1e-12)


def test_caching_runs_each_subset_once():
    calls = []

    def counted(active):
        calls.append(frozenset(active))
        return multiplicative(active)

    evaluator = cached(counted)
    shapley(evaluator, ("a", "b", "c"))
    contribution_range(evaluator, ("a", "b", "c"))
    assert len(calls) == len(set(calls)) == 8


def test_order_sensitivity_is_the_width_against_the_value():
    measured = order_sensitivity(multiplicative, ("a", "b", "c"))
    spans = contribution_range(multiplicative, ("a", "b", "c"))
    values = shapley(multiplicative, ("a", "b", "c")).contributions
    for name in ("a", "b", "c"):
        assert measured[name] == pytest.approx(
            (spans[name][1] - spans[name][0]) / abs(values[name]), rel=1e-12
        )


def test_a_driver_worth_nothing_has_infinite_order_sensitivity():
    def with_a_dead_driver(active):
        return multiplicative(set(active) - {"dead"})

    assert order_sensitivity(with_a_dead_driver,
                             ("a", "b", "dead"))["dead"] == float("inf")


@pytest.mark.parametrize("drivers, message", [
    ((), "at least one driver"),
    (("a", "a"), "must be distinct"),
    (tuple(str(i) for i in range(MAX_DRIVERS + 1)), "stops at"),
])
def test_the_methods_validate_their_drivers(drivers, message):
    with pytest.raises(ValueError, match=message):
        shapley(lambda active: 0.0, drivers)


def test_to_table_keeps_the_order_a_sequential_analysis_used():
    measured = sequential(additive, ("c", "a", "b"))
    assert np.allclose(to_table(measured), [30.0, 10.0, 20.0])


def test_the_allocation_names_are_the_ones_the_module_implements():
    assert set(ALLOCATIONS) == {"sequential", "isolated", "shapley"}


# --- swapping assumptions ---------------------------------------------------

def basis(mortality, lapse, interest, renewal):
    return Assumptions(
        mortality=MortalityTable.flat(mortality), lapse=lapse,
        interest=interest, freq=1,
        expenses=Expenses(initial=ExpenseScale(per_policy=200.0),
                          renewal=ExpenseScale(per_policy=renewal),
                          inflation=0.02),
    )


EXPECTED = basis(0.004, 0.06, 0.035, 60.0)
ACTUAL = basis(0.0052, 0.04, 0.025, 78.0)


def test_swapping_nothing_gives_the_expected_basis():
    built = swap(EXPECTED, ACTUAL, DRIVERS)(set())
    assert built.lapse == EXPECTED.lapse
    assert built.interest == EXPECTED.interest


def test_swapping_everything_gives_the_actual_basis():
    built = swap(EXPECTED, ACTUAL, DRIVERS)(set(DRIVERS))
    assert built.lapse == ACTUAL.lapse
    assert built.interest == ACTUAL.interest
    assert built.expenses is ACTUAL.expenses


def test_a_driver_changes_only_what_it_names():
    built = swap(EXPECTED, ACTUAL, DRIVERS)({"lapse"})
    assert built.lapse == ACTUAL.lapse
    assert built.interest == EXPECTED.interest
    assert built.expenses is EXPECTED.expenses


def test_swapping_the_lapse_rate_moves_both_copies_of_it():
    """``Assumptions`` stores the lapse rate twice and different templates
    read different copies — ``TermLife`` the scalar, ``UnitLinked`` the
    dynamic one. Moving one alone gives a run on the actual basis for some
    products and the expected basis for others, with nothing in any output
    to show for it."""
    built = swap(EXPECTED, ACTUAL, DRIVERS)({"lapse"})
    assert built.lapse == ACTUAL.lapse
    assert built.dynamic_lapse.base == ACTUAL.lapse
    assert built.dynamic_lapse is not EXPECTED.dynamic_lapse


def test_not_swapping_it_leaves_both_copies_expected():
    built = swap(EXPECTED, ACTUAL, DRIVERS)({"interest"})
    assert built.lapse == EXPECTED.lapse
    assert built.dynamic_lapse.base == EXPECTED.lapse


def test_swapping_expenses_moves_the_scalar_form_with_the_basis():
    built = swap(EXPECTED, ACTUAL, DRIVERS)({"expenses"})
    assert built.expenses is ACTUAL.expenses
    assert built.expense_per_policy == ACTUAL.expense_per_policy


def test_the_expected_basis_is_not_mutated():
    swap(EXPECTED, ACTUAL, DRIVERS)(set(DRIVERS))
    assert EXPECTED.lapse == 0.06


def test_a_driver_that_is_not_an_assumption_is_refused():
    """It would otherwise contribute exactly zero and look like good news."""
    with pytest.raises(ValueError, match="not an attribute"):
        swap(EXPECTED, ACTUAL, ("mortality", "weather"))


def test_activating_a_field_that_was_never_a_driver_is_refused():
    build = swap(EXPECTED, ACTUAL, ("mortality",))
    with pytest.raises(ValueError, match="not among"):
        build({"lapse"})


# --- on a real projection ---------------------------------------------------

def surplus_evaluator():
    points = from_dicts([
        {"id": "T1", "age_at_entry": 45, "term_years": 20,
         "sum_assured": 250_000.0, "annual_premium": 1400.0,
         "init_pols": 1000},
    ])
    build = swap(EXPECTED, ACTUAL, DRIVERS)

    def metric(active):
        assumptions = build(active)
        result = run(TermLife, points, assumptions, proj_len=21,
                     outputs=["profit_before_tax"])
        profit = np.array(result.aggregate("profit_before_tax"))
        v = 1.0 / (1.0 + assumptions.interest)
        return float(sum(profit[t] * v ** (t + 1) for t in range(profit.size)))

    return cached(metric)


def test_the_book_lost_what_the_decomposition_has_to_explain():
    evaluate = surplus_evaluator()
    variance = evaluate(frozenset(DRIVERS)) - evaluate(frozenset())
    assert variance == pytest.approx(-2_857_440, abs=1_000)


def test_every_exact_method_explains_the_whole_variance():
    evaluate = surplus_evaluator()
    for measured in (sequential(evaluate, DRIVERS),
                     sequential(evaluate, tuple(reversed(DRIVERS))),
                     shapley(evaluate, DRIVERS)):
        assert measured.reconciles()


def test_the_interaction_is_a_fifth_of_the_whole_variance():
    """Second order in the size of the variances, so negligible on a quiet
    year and not on the year anybody wants the analysis for."""
    evaluate = surplus_evaluator()
    measured = isolated(evaluate, DRIVERS)
    assert measured.residual / measured.total == pytest.approx(0.227, abs=0.01)


def test_the_ordering_is_worth_more_than_half_a_million_on_mortality():
    evaluate = surplus_evaluator()
    low, high = contribution_range(evaluate, DRIVERS)["mortality"]
    assert high - low == pytest.approx(646_000, abs=5_000)


def test_the_ordering_decides_the_sign_of_the_interest_result():
    """The finding. Same book, same year, same experience: an analysis of
    surplus reports interest profit or interest loss depending only on
    where interest was peeled off."""
    evaluate = surplus_evaluator()
    low, high = contribution_range(evaluate, DRIVERS)["interest"]
    assert low < 0.0 < high
    assert low == pytest.approx(-102_000, abs=5_000)
    assert high == pytest.approx(162_000, abs=5_000)


def test_interest_is_the_line_the_ordering_owns():
    """Its range is fourteen times its own Shapley value, so the attributed
    surplus is decided more by the ordering than by the experience."""
    sensitivity = order_sensitivity(surplus_evaluator(), DRIVERS)
    assert sensitivity["interest"] > 10.0
    assert sensitivity["mortality"] < 0.3


def test_the_whole_analysis_costs_sixteen_projections():
    evaluate = surplus_evaluator()
    shapley(evaluate, DRIVERS)
    contribution_range(evaluate, DRIVERS)
    isolated(evaluate, DRIVERS)
    assert len(evaluate.memo) == 2 ** len(DRIVERS)


# --- where a variance lands -------------------------------------------------

VARIANCES = {"claims": -400.0, "expenses": -150.0,
             "premiums_future": 90.0, "lapse_assumption": -260.0}
SERVICE = {"claims": "current", "expenses": "current",
           "premiums_future": "future", "lapse_assumption": "future"}


def test_current_service_variances_go_straight_to_profit():
    attributed = allocate(VARIANCES, SERVICE)
    assert attributed.profit_or_loss == pytest.approx(-550.0)


def test_future_service_variances_never_reach_the_result():
    attributed = allocate(VARIANCES, SERVICE)
    assert attributed.csm_adjustment == pytest.approx(-170.0)
    assert attributed.total == pytest.approx(-720.0)


def test_the_same_variance_lands_in_two_places_on_a_judgement():
    """The standard says where each category goes. It does not say how to
    tell an experience variance from a change in estimate on the same
    number, and that judgement moves 260 out of this year's profit without
    moving a single cashflow."""
    as_estimate = allocate(VARIANCES, SERVICE)
    as_experience = as_estimate.reclassified("lapse_assumption", "current")
    assert as_experience.profit_or_loss - as_estimate.profit_or_loss == \
        pytest.approx(-260.0)
    assert as_experience.total == pytest.approx(as_estimate.total)


def test_reclassifying_never_changes_the_total():
    attributed = allocate(VARIANCES, SERVICE)
    for name in VARIANCES:
        for period in SERVICE_PERIODS:
            assert attributed.reclassified(name, period).total == \
                pytest.approx(attributed.total)


def test_a_variance_with_no_service_period_is_refused():
    """A default here is a decision about profit dressed up as a
    convenience."""
    with pytest.raises(ValueError, match="no service period"):
        allocate({"claims": -400.0, "mystery": 10.0}, {"claims": "current"})


def test_an_unknown_service_period_is_refused():
    with pytest.raises(ValueError, match="service period must be"):
        allocate({"claims": -400.0}, {"claims": "later"})


def test_an_attribution_is_readable():
    assert "P&L" in repr(Attribution(VARIANCES, SERVICE))
