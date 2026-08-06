"""VM-22, and the two reasons where you aggregate changes the reserve.

Execution plan §5, item C1. RFC-016's CTE machinery is tested in
tests/test_pbr.py and is not retested here; what this suite is about is
what VM-22 adds — a floor that belongs to a contract sitting under a tail
statistic that belongs to a book, and the exclusion decisions that let a
component be left out.

The centrepiece is the decomposition. ``seriatim − aggregate`` splits
exactly into a **floor effect** and a **diversification effect**, both
non-negative, and the suite pins each of them in isolation with a
hand-computed miniature block: a case where diversification is everything,
a case where the floor is everything, and — the finding — a case where the
floor binds in aggregate and the diversification benefit is *exactly zero*
however uncorrelated the scenarios are.

The refusals are asserted as hard as the arithmetic, because most of what
this module protects is a reserve that would otherwise look computed: an
exclusion with no basis, a certification with nobody's name on it, a ratio
test run without the threshold the Valuation Manual prescribes, a component
that is neither computed nor excluded.
"""

from __future__ import annotations

import numpy as np
import pytest

from engine.report.pbr import cte, scenario_reserves
from engine.report.vm22 import (
    COMPONENTS,
    VM22_2026,
    AggregationGap,
    Contract,
    Exclusion,
    VM22Basis,
    VM22Error,
    VM22Reserve,
    aggregate_reserve,
    aggregate_stochastic_reserve,
    aggregation_decomposition,
    seriatim_reserve,
    seriatim_stochastic_reserve,
    stochastic_exclusion_test,
)

#: CTE(50) over four scenarios is the mean of the worst two, which is the
#: whole reason the miniature blocks below are hand-checkable.
HALF = VM22_2026.variant(cte_level=0.5)


def block(*rows, floors=None):
    """Contracts from literal scenario-reserve rows."""
    floors = floors if floors is not None else [0.0] * len(rows)
    return [Contract(id=f"C{i}", scenario_reserve=row,
                     cash_surrender_value=floor)
            for i, (row, floor) in enumerate(zip(rows, floors))]


# --------------------------------------------------------------------------
# The stochastic reserve is a statistic over a book
# --------------------------------------------------------------------------

def test_the_aggregate_reserve_is_the_cte_of_the_summed_scenarios():
    """Hand-computed. Two contracts whose bad scenarios are each other's
    good ones: summed they are [200, 100, 100, 200], and CTE(50) of that is
    the mean of 200 and 200."""
    contracts = block([0.0, 0.0, 100.0, 200.0], [200.0, 100.0, 0.0, 0.0])
    assert aggregate_stochastic_reserve(contracts, basis=HALF) == 200.0
    # Each contract's own CTE(50) is the mean of 100 and 200.
    assert seriatim_stochastic_reserve(contracts, basis=HALF) == 300.0


def test_summing_first_is_what_makes_the_cte_do_any_work():
    """A scenario bad for one contract and good for another is not in the
    tail of the sum, and that is the entire benefit of aggregating."""
    contracts = block([0.0, 0.0, 100.0, 200.0], [200.0, 100.0, 0.0, 0.0])
    assert aggregate_stochastic_reserve(contracts, basis=HALF) < \
        seriatim_stochastic_reserve(contracts, basis=HALF)


def test_perfectly_correlated_contracts_get_no_benefit_at_all():
    """The other end of the same fact: when every contract is worst in the
    same scenario there is nothing to pool, and a CTE is *additive* rather
    than merely subadditive."""
    row = [0.0, 50.0, 100.0, 200.0]
    contracts = block(row, row, row)
    assert aggregate_stochastic_reserve(contracts, basis=HALF) == \
        pytest.approx(seriatim_stochastic_reserve(contracts, basis=HALF))


# --------------------------------------------------------------------------
# The decomposition
# --------------------------------------------------------------------------

def test_the_gap_is_exactly_its_two_parts():
    contracts = block([0.0, 0.0, 100.0, 200.0], [200.0, 100.0, 0.0, 0.0],
                      floors=[10.0, 10.0])
    gap = aggregation_decomposition(contracts, basis=HALF)
    assert gap.gap == pytest.approx(gap.floor_effect
                                    + gap.diversification_effect)
    assert gap.aggregate == 200.0 and gap.seriatim == 300.0
    assert gap.floor_effect == 0.0
    assert gap.diversification_effect == 100.0


def test_the_floor_effect_alone_when_there_is_nothing_to_diversify():
    """Perfectly correlated scenario reserves, so the tail term is zero and
    every penny of the gap is the floor being applied per contract.

    One contract reserves 100 in every scenario and has no surrender value;
    the other reserves nothing and has a surrender value of 100. Seriatim
    that is 100 + 100; in aggregate it is max(100, 100).
    """
    contracts = block([100.0] * 4, [0.0] * 4, floors=[0.0, 100.0])
    gap = aggregation_decomposition(contracts, basis=HALF)
    assert gap.seriatim == 200.0
    assert gap.aggregate == 100.0
    assert gap.floor_effect == 100.0
    assert gap.diversification_effect == 0.0


def test_the_floor_can_eat_the_diversification_benefit_entirely():
    """The finding. These two contracts are perfectly anti-correlated — the
    most diversifiable block that can be written — and the credit for
    pooling them is exactly zero, because the surrender values bind on both
    sides of the comparison.

    "Our stochastic reserve fell when we aggregated" is worth nothing until
    somebody has looked at which component binds.
    """
    contracts = block([0.0, 0.0, 100.0, 200.0], [200.0, 100.0, 0.0, 0.0],
                      floors=[250.0, 250.0])
    gap = aggregation_decomposition(contracts, basis=HALF)
    assert gap.floor_binds
    assert gap.diversification_effect == 0.0
    assert gap.floor_effect == 0.0
    assert gap.gap == 0.0
    # And the reserve is the floor, on both routes.
    assert gap.aggregate == gap.seriatim == 500.0

    # Drop the floors and the same block diversifies by a hundred.
    freed = block([0.0, 0.0, 100.0, 200.0], [200.0, 100.0, 0.0, 0.0],
                  floors=[0.0, 0.0])
    assert aggregation_decomposition(freed, basis=HALF
                                     ).diversification_effect == 100.0


def test_neither_effect_is_ever_negative_on_random_blocks():
    """The inequality is structural — a CTE is subadditive and a sum of
    maxima dominates a maximum of sums — so it should hold on anything."""
    rng = np.random.default_rng(11)
    for trial in range(40):
        n_contracts = int(rng.integers(2, 6))
        n_scenarios = int(rng.integers(10, 60))
        rows = rng.lognormal(3.0, 1.0, (n_contracts, n_scenarios))
        floors = rng.uniform(0.0, 60.0, n_contracts)
        contracts = [Contract(f"C{i}", row, float(f))
                     for i, (row, f) in enumerate(zip(rows, floors))]
        gap = aggregation_decomposition(contracts)
        assert gap.floor_effect >= -1e-9, trial
        assert gap.diversification_effect >= -1e-9, trial
        assert gap.gap == pytest.approx(gap.floor_effect
                                        + gap.diversification_effect)
        assert gap.seriatim >= gap.aggregate - 1e-9


def test_seriatim_reserve_matches_the_decompositions_own_figure():
    contracts = block([10.0, 90.0], [80.0, 20.0], floors=[30.0, 5.0])
    basis = VM22_2026.variant(cte_level=0.5)
    assert seriatim_reserve(contracts, basis=basis) == \
        aggregation_decomposition(contracts, basis=basis).seriatim


# --------------------------------------------------------------------------
# The reserve object
# --------------------------------------------------------------------------

def test_the_reserve_is_the_greatest_component_and_names_it():
    reserve = VM22Reserve(cash_surrender_value=90.0, deterministic=110.0,
                          stochastic=140.0)
    assert reserve.value == 140.0
    assert reserve.binding == "stochastic"
    assert reserve.headroom() == {"cash_surrender_value": 50.0,
                                  "deterministic": 30.0, "stochastic": 0.0}
    assert "stochastic" in repr(reserve)


def test_moving_a_component_that_does_not_bind_changes_nothing():
    before = VM22Reserve(cash_surrender_value=90.0, deterministic=110.0,
                         stochastic=140.0).value
    after = VM22Reserve(cash_surrender_value=10.0, deterministic=110.0,
                        stochastic=140.0).value
    assert before == after == 140.0


def test_a_tie_resolves_to_the_floor_and_stays_there():
    """A tie broken by dictionary order would move with the input. The
    floor is the conservative reading and a stable one."""
    reserve = VM22Reserve(cash_surrender_value=100.0, deterministic=100.0,
                          stochastic=100.0)
    assert reserve.binding == "cash_surrender_value"
    assert COMPONENTS[0] == "cash_surrender_value"


def test_a_component_that_is_neither_computed_nor_excluded_is_refused():
    """Leaving one out is a decision with a basis. A reserve that simply
    lacks a component looks exactly like one whose test was passed."""
    with pytest.raises(VM22Error, match="neither computed nor excluded"):
        VM22Reserve(cash_surrender_value=90.0, stochastic=140.0)


def test_a_component_cannot_be_both_computed_and_excluded():
    excluded = Exclusion(component="stochastic", basis="certification",
                         certified_by="A. Actuary")
    with pytest.raises(VM22Error, match="both computed and excluded"):
        VM22Reserve(cash_surrender_value=90.0, deterministic=110.0,
                    stochastic=140.0, exclusions=[excluded])


def test_a_reserve_with_nothing_in_it_is_an_error_not_a_zero():
    everything = [Exclusion(component=name, basis="certification",
                            certified_by="A. Actuary")
                  for name in COMPONENTS]
    with pytest.raises(VM22Error, match="did not happen"):
        VM22Reserve(exclusions=everything)


def test_an_excluded_component_is_reported_with_its_basis():
    """A reserve that can say *why* the stochastic component is missing is
    a different artifact from one that is silent about it."""
    excluded = Exclusion(component="stochastic", basis="ratio_test",
                         ratio=0.031, threshold=0.06)
    reserve = VM22Reserve(cash_surrender_value=90.0, deterministic=110.0,
                          exclusions=[excluded])
    assert reserve.value == 110.0
    assert reserve.to_dict()["excluded"] == {"stochastic": "ratio_test"}
    assert reserve.exclusions["stochastic"].ratio == 0.031


# --------------------------------------------------------------------------
# Exclusion, and what it must carry
# --------------------------------------------------------------------------

def test_a_certification_without_a_name_is_not_a_certification():
    with pytest.raises(VM22Error, match="name the actuary"):
        Exclusion(component="stochastic", basis="certification")


def test_a_ratio_test_exclusion_carries_the_ratio_it_computed():
    """An exclusion nobody can recheck is an assertion."""
    with pytest.raises(VM22Error, match="recheck"):
        Exclusion(component="stochastic", basis="ratio_test")


def test_an_exclusion_needs_a_known_component_and_a_known_basis():
    with pytest.raises(VM22Error, match="not a VM-22 component"):
        Exclusion(component="net_premium", basis="certification",
                  certified_by="A. Actuary")
    with pytest.raises(VM22Error, match="needs a basis"):
        Exclusion(component="stochastic", basis="because I said so")


# --------------------------------------------------------------------------
# The exclusion test, and the number this module will not invent
# --------------------------------------------------------------------------

def test_the_ratio_test_refuses_to_pick_its_own_threshold():
    """The threshold is prescribed by the text being valued under. A
    default would make an exclusion decision that looks computed and is
    not."""
    assert VM22_2026.stochastic_exclusion_ratio is None
    with pytest.raises(VM22Error, match="will not pick one"):
        stochastic_exclusion_test(100.0, [103.0], basis=VM22_2026)


def test_a_threshold_supplied_either_way_gives_the_same_test():
    direct = stochastic_exclusion_test(100.0, [103.0, 101.0],
                                       basis=VM22_2026, threshold=0.06)
    dated = stochastic_exclusion_test(
        100.0, [103.0, 101.0],
        basis=VM22_2026.variant(stochastic_exclusion_ratio=0.06))
    assert direct.ratio == dated.ratio == pytest.approx(0.03)
    assert direct.passed and dated.passed


def test_the_test_uses_the_most_adverse_scenario():
    test = stochastic_exclusion_test(100.0, [101.0, 108.0, 102.0],
                                     basis=VM22_2026, threshold=0.06)
    assert test.adverse == 108.0
    assert test.ratio == pytest.approx(0.08)
    assert not test.passed


def test_a_failed_test_will_not_produce_an_exclusion():
    test = stochastic_exclusion_test(100.0, [108.0], basis=VM22_2026,
                                     threshold=0.06)
    with pytest.raises(VM22Error, match="stochastic reserve is required"):
        test.exclusion()


def test_a_passed_test_produces_an_exclusion_that_carries_its_numbers():
    test = stochastic_exclusion_test(100.0, [103.0], basis=VM22_2026,
                                     threshold=0.06)
    excluded = test.exclusion(note="2026 year-end")
    assert excluded.component == "stochastic"
    assert excluded.basis == "ratio_test"
    assert excluded.ratio == pytest.approx(0.03)
    assert excluded.threshold == 0.06
    assert excluded.note == "2026 year-end"


def test_a_zero_baseline_has_no_ratio_and_says_so():
    """Rather than dividing and reporting an infinity that would pass or
    fail depending on the sign of the numerator."""
    with pytest.raises(VM22Error, match="no ratio to compute"):
        stochastic_exclusion_test(0.0, [10.0], basis=VM22_2026,
                                  threshold=0.06)


def test_the_test_needs_something_to_be_adverse():
    with pytest.raises(VM22Error, match="at least one adverse"):
        stochastic_exclusion_test(100.0, [], basis=VM22_2026, threshold=0.06)


# --------------------------------------------------------------------------
# The dated basis
# --------------------------------------------------------------------------

def test_the_basis_is_dated_and_names_its_text():
    assert VM22_2026.label == "VM-22 (2026)"
    assert "2026" in VM22_2026.text
    assert VM22_2026.cte_level == 0.70


def test_a_variant_changes_one_parameter_and_nothing_else():
    other = VM22_2026.variant(cte_level=0.90)
    assert other.cte_level == 0.90
    assert other.label == VM22_2026.label
    assert VM22_2026.cte_level == 0.70          # frozen; the original stands


def test_an_impossible_basis_is_refused():
    with pytest.raises(VM22Error, match="outside"):
        VM22Basis(label="bad", cte_level=1.0)
    with pytest.raises(VM22Error, match="negative"):
        VM22Basis(label="bad", stochastic_exclusion_ratio=-0.01)


def test_a_stricter_tail_never_gives_a_smaller_reserve():
    contracts = block([0.0, 10.0, 50.0, 400.0], [5.0, 5.0, 20.0, 90.0])
    values = [aggregate_stochastic_reserve(
        contracts, basis=VM22_2026.variant(cte_level=level))
        for level in (0.0, 0.25, 0.5, 0.75)]
    assert values == sorted(values)


# --------------------------------------------------------------------------
# Groups, and what cannot be added
# --------------------------------------------------------------------------

def test_contracts_that_disagree_on_scenario_count_cannot_be_added():
    """Adding them would be adding across different futures."""
    contracts = [Contract("A", [1.0, 2.0, 3.0]), Contract("B", [1.0, 2.0])]
    with pytest.raises(VM22Error, match="different futures"):
        aggregate_stochastic_reserve(contracts)


def test_an_empty_group_and_an_empty_contract_are_refused():
    with pytest.raises(VM22Error, match="at least one contract"):
        aggregate_stochastic_reserve([])
    with pytest.raises(VM22Error, match="no scenario reserves"):
        Contract("A", [])


# --------------------------------------------------------------------------
# End to end, on the machinery RFC-016 already had
# --------------------------------------------------------------------------

def annuity_block(seed=3, periods=25, scenarios=500, n_contracts=4):
    """A fixed-annuity-shaped block: spread income, guaranteed crediting,
    and a shortfall when earned rates fall below the guarantee."""
    rng = np.random.default_rng(seed)
    rates = rng.normal(0.035, 0.012, (periods, scenarios))
    contracts = []
    for i in range(n_contracts):
        account = 100_000.0 * (1.0 + 0.02) ** np.arange(periods)[:, None]
        guarantee = 0.02 + 0.004 * i
        net = (rates - guarantee) * account
        contracts.append(Contract.from_cashflows(
            f"FA{i}", net, rates, cash_surrender_value=1_000.0 * (i + 1)))
    return contracts, rates


def test_a_projected_annuity_block_reserves_end_to_end():
    contracts, rates = annuity_block()
    reserve = aggregate_reserve(contracts, deterministic=5_000.0)
    assert reserve.value > 0.0
    assert reserve.binding in COMPONENTS
    assert set(reserve.to_dict()["components"]) == set(COMPONENTS)

    # The stochastic component is the CTE of the summed scenario reserves,
    # and it is RFC-016's number rather than a second implementation.
    summed = sum(c.scenario_reserve for c in contracts)
    assert reserve.components["stochastic"] == cte(summed, 0.70)


def test_the_contract_helper_is_rfc_016s_scenario_reserves():
    """``Contract.from_cashflows`` must not be a second implementation of
    the accumulated-deficiency roll."""
    contracts, rates = annuity_block(n_contracts=1)
    net = (rates - 0.02) * (100_000.0 * (1.0 + 0.02)
                            ** np.arange(rates.shape[0])[:, None])
    assert np.array_equal(contracts[0].scenario_reserve,
                          scenario_reserves(net, rates))


def test_a_library_template_feeds_the_reserve_without_an_adapter():
    """The pairing the item asks for: a `FixedAnnuity` run is the source of
    the net cashflows, so the reserve is over numbers a template produced
    rather than over a shape invented for the test.

    The scenario dimension is supplied here rather than by the template —
    `FixedAnnuity` is a deterministic projection, and what VM-22 needs is
    one net-cashflow path per economic scenario. Making the template itself
    stochastic is a separate piece of work and is not pretended at.
    """
    from engine.core.vector import run_vectorized
    from engine.data.assumptions import Assumptions, MortalityTable
    from engine.data.modelpoints import ModelPoint
    from engine.library.fixed_annuity import FixedAnnuity

    points = [ModelPoint(id="FA1", age_at_entry=60, defer_years=5,
                         premium=100_000.0, annual_payment=8_000.0,
                         init_pols=1)]
    assumptions = Assumptions(mortality=MortalityTable.flat(0.012),
                              interest=0.03, crediting_rate=0.025)
    result = run_vectorized(FixedAnnuity, points, assumptions, 30,
                            outputs=["payments", "death_benefits",
                                     "fund_eoy_per_pol"])
    outgo = (result.array("payments") + result.array("death_benefits")
             ).sum(axis=1)

    rng = np.random.default_rng(5)
    rates = rng.normal(0.035, 0.012, (outgo.size, 200))
    # Income is the fund earning the scenario rate; outgo is the template's.
    fund = result.array("fund_eoy_per_pol").sum(axis=1)[:, None]
    net = fund * rates - outgo[:, None]

    contract = Contract.from_cashflows("FA1", net, rates,
                                       cash_surrender_value=50_000.0)
    reserve = aggregate_reserve([contract], deterministic=10_000.0)
    assert reserve.value >= 50_000.0            # the floor is a floor
    assert reserve.binding in COMPONENTS
    assert np.array_equal(contract.scenario_reserve,
                          scenario_reserves(net, rates))


def test_a_real_block_still_obeys_the_decomposition():
    contracts, _ = annuity_block()
    gap = aggregation_decomposition(contracts)
    assert isinstance(gap, AggregationGap)
    assert gap.gap == pytest.approx(gap.floor_effect
                                    + gap.diversification_effect)
    assert gap.seriatim >= gap.aggregate
