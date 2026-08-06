"""VM-22, and where the text puts the floor.

Execution plan §5, item C1, corrected against the NAIC *Valuation Manual*,
1 January 2026 edition. RFC-016's CTE machinery is tested in
tests/test_pbr.py and is not retested here; this suite is about what VM-22
adds, and about three places the first cut of this module read the
framework the way it looked rather than the way it is written:

- **§3.A** — the aggregate reserve is the SR *plus* the DR *plus* the
  formulaic reserve over disjoint groups of contracts. A sum over a
  partition, not VM-20's maximum over components of one block.
- **§4.B.1** — "The scenario reserve for any given scenario shall not be
  less than the cash surrender value in aggregate", *then* §3.F.5.a.iii
  takes the CTE. The floor goes under the tail statistic, and flooring
  outside it understates the reserve.
- **§7.C.1** — the ratio is ``(b − a) / c`` where ``c`` is the present
  value of benefits, not the baseline reserve, and the threshold is the
  lesser of 6.0% and the company's materiality standard.

The finding survives the correction and is sharper for it: the floor's
placement matters *exactly* when the surrender value sits inside the tail,
and the diversification benefit of aggregating can be exactly zero.
"""

from __future__ import annotations

import numpy as np
import pytest

from engine.report.pbr import cte, scenario_reserves
from engine.report.vm22 import (
    LONGEVITY_FLOOR_RATE,
    MAV_RULES,
    METHODS,
    RESERVING_CATEGORIES,
    SERT_CAP,
    VM22_2026,
    AggregateReserve,
    AggregationGap,
    BasisPair,
    Contract,
    ContractRecord,
    Exclusion,
    ModelSegment,
    ReservingGroup,
    VM22Basis,
    VM22Error,
    aggregate_stochastic_reserve,
    aggregation_decomposition,
    allocate_aggregate_reserve,
    apv_scenario,
    floor_outside_reserve,
    segment_group,
    segment_scenario_reserves,
    segment_stochastic_reserve,
    seriatim_reserve,
    stochastic_exclusion_test,
    stochastic_group,
)

#: CTE(50) over four scenarios is the mean of the worst two, which is what
#: makes every miniature block below checkable by hand.
HALF = VM22_2026.variant(cte_level=0.5)


def block(*rows, floors=None):
    floors = floors if floors is not None else [0.0] * len(rows)
    return [Contract(id=f"C{i}", scenario_reserve=row,
                     cash_surrender_value=floor)
            for i, (row, floor) in enumerate(zip(rows, floors))]


# --------------------------------------------------------------------------
# §4.B.1 — the floor goes under the tail
# --------------------------------------------------------------------------

def test_the_floor_is_applied_per_scenario_before_the_cte():
    """The correction. Reserves [0, 0, 100, 200] with a surrender value of
    150: flooring each scenario gives [150, 150, 150, 200] and CTE(50) of
    that is 175. Flooring the CTE instead gives max(150, 150) = 150.

    The prescribed answer is the larger one, and a module that read §4.B.1
    the natural way would under-reserve by 25 on this block.
    """
    contracts = block([0.0, 0.0, 100.0, 200.0], floors=[150.0])
    assert aggregate_stochastic_reserve(contracts, basis=HALF) == 175.0
    assert floor_outside_reserve(contracts, basis=HALF) == 150.0


def test_the_prescribed_reserve_is_never_below_the_natural_misreading():
    """``max(F, X)`` dominates both ``F`` and ``X`` pointwise and a CTE is
    monotone, so this is structural rather than a property of the example."""
    rng = np.random.default_rng(19)
    for _ in range(40):
        rows = rng.lognormal(3.0, 1.2, (3, 30))
        floors = rng.uniform(0.0, 200.0, 3)
        contracts = [Contract(f"C{i}", row, float(f))
                     for i, (row, f) in enumerate(zip(rows, floors))]
        assert (aggregate_stochastic_reserve(contracts)
                >= floor_outside_reserve(contracts) - 1e-9)


def test_the_ordering_matters_exactly_inside_the_tail():
    """Both placements agree when the floor is below every tail scenario
    and when it is above every scenario; the gap opens only in between.

    That window is not exotic — it is where a deferred annuity's surrender
    value normally sits relative to its reserve — which is why reading
    §4.B.1 the natural way is expensive rather than merely wrong.
    """
    costs = {}
    for floor in (0.0, 50.0, 120.0, 150.0, 190.0, 250.0):
        contracts = block([0.0, 0.0, 100.0, 200.0], floors=[floor])
        gap = aggregation_decomposition(contracts, basis=HALF)
        costs[floor] = gap.ordering_cost
    assert costs[0.0] == costs[50.0] == 0.0        # below the tail
    assert costs[250.0] == 0.0                     # above every scenario
    assert costs[120.0] == 10.0
    assert costs[150.0] == 25.0
    assert costs[190.0] == 5.0


# --------------------------------------------------------------------------
# Aggregation, and the benefit a floor can eat
# --------------------------------------------------------------------------

def test_summing_first_is_what_makes_the_cte_do_any_work():
    """A scenario bad for one contract and good for another is not in the
    tail of the sum, and that is the entire benefit of aggregating."""
    contracts = block([0.0, 0.0, 100.0, 200.0], [200.0, 100.0, 0.0, 0.0])
    assert aggregate_stochastic_reserve(contracts, basis=HALF) == 200.0
    assert seriatim_reserve(contracts, basis=HALF) == 300.0


def test_perfectly_correlated_contracts_get_no_benefit_at_all():
    row = [0.0, 50.0, 100.0, 200.0]
    contracts = block(row, row, row)
    assert aggregate_stochastic_reserve(contracts, basis=HALF) == \
        pytest.approx(seriatim_reserve(contracts, basis=HALF))


def test_the_gap_is_exactly_its_two_parts():
    contracts = block([0.0, 0.0, 100.0, 200.0], [200.0, 100.0, 0.0, 0.0],
                      floors=[10.0, 10.0])
    gap = aggregation_decomposition(contracts, basis=HALF)
    assert gap.gap == pytest.approx(gap.floor_effect
                                    + gap.diversification_effect)
    assert gap.floor_outside == 200.0 and gap.seriatim == 300.0
    assert gap.diversification_effect == 100.0


def test_the_floor_can_eat_the_diversification_benefit_entirely():
    """The finding. Two perfectly anti-correlated contracts — the most
    diversifiable pair anyone can write — and the credit for pooling them
    is exactly zero, because the surrender values bind everywhere.

    "Our stochastic reserve fell when we aggregated" is worth nothing until
    somebody has looked at whether the floor binds.
    """
    contracts = block([0.0, 0.0, 100.0, 200.0], [200.0, 100.0, 0.0, 0.0],
                      floors=[250.0, 250.0])
    gap = aggregation_decomposition(contracts, basis=HALF)
    assert gap.floor_binds
    assert gap.diversification_effect == 0.0
    assert gap.floor_effect == 0.0
    assert gap.gap == 0.0
    # Every placement of the floor collapses to the same number.
    assert gap.floor_outside == gap.prescribed == gap.seriatim == 500.0

    freed = block([0.0, 0.0, 100.0, 200.0], [200.0, 100.0, 0.0, 0.0])
    assert aggregation_decomposition(freed, basis=HALF
                                     ).diversification_effect == 100.0


def test_neither_effect_is_ever_negative_on_random_blocks():
    rng = np.random.default_rng(11)
    for trial in range(40):
        rows = rng.lognormal(3.0, 1.0, (int(rng.integers(2, 6)),
                                        int(rng.integers(10, 60))))
        floors = rng.uniform(0.0, 60.0, rows.shape[0])
        contracts = [Contract(f"C{i}", row, float(f))
                     for i, (row, f) in enumerate(zip(rows, floors))]
        gap = aggregation_decomposition(contracts)
        assert gap.floor_effect >= -1e-9, trial
        assert gap.diversification_effect >= -1e-9, trial
        assert gap.ordering_cost >= -1e-9, trial
        assert gap.gap == pytest.approx(gap.floor_effect
                                        + gap.diversification_effect)
        assert gap.seriatim >= gap.prescribed - 1e-9


# --------------------------------------------------------------------------
# §3.A — the aggregate reserve is a sum over a partition
# --------------------------------------------------------------------------

def test_the_aggregate_reserve_adds_its_groups_rather_than_maximising():
    """§3.A: "the SR … plus the DR … plus the reserve for any contracts
    valued under VM-A, VM-C, VM-M, and VM-V". Disjoint groups, added — the
    opposite shape from VM-20's maximum over one block."""
    reserve = AggregateReserve([
        ReservingGroup("modelled", "stochastic", 140.0),
        ReservingGroup("certified", "deterministic", 60.0,
                       exclusion=Exclusion(basis="certification",
                                           certified_by="A. Actuary")),
        ReservingGroup("excluded", "formulaic", 25.0,
                       exclusion=Exclusion(basis="ratio_test", ratio=0.01,
                                           threshold=SERT_CAP)),
    ])
    assert reserve.post_ceded == 225.0            # not max(...) == 140
    assert reserve.by_method() == {
        "stochastic": BasisPair.flat(140.0),
        "deterministic": BasisPair.flat(60.0),
        "formulaic": BasisPair.flat(25.0)}
    assert reserve.largest == "modelled"
    assert set(METHODS) == {"stochastic", "deterministic", "formulaic"}


def test_a_group_not_modelled_stochastically_must_say_why():
    """§7 permits leaving the SR out only on a stated basis, and a group
    omitted after a passed test is a different reserve from one nobody
    computed."""
    with pytest.raises(VM22Error, match="only on a stated basis"):
        ReservingGroup("quiet", "formulaic", 25.0)


def test_a_stochastic_group_cannot_also_be_excluded_from_being_one():
    with pytest.raises(VM22Error, match="not a note attached"):
        ReservingGroup("both", "stochastic", 10.0,
                       exclusion=Exclusion(basis="certification",
                                           certified_by="A. Actuary"))


def test_repeated_group_names_are_refused():
    """§3.A adds one reserve per group, so two groups with one name is
    either a double count or a lost group."""
    groups = [ReservingGroup("g", "stochastic", 1.0),
              ReservingGroup("g", "stochastic", 2.0)]
    with pytest.raises(VM22Error, match="group names repeat"):
        AggregateReserve(groups)


def test_an_aggregate_reserve_with_no_groups_is_an_error_not_a_zero():
    with pytest.raises(VM22Error, match="did not happen"):
        AggregateReserve([])


def test_an_unknown_method_is_refused():
    with pytest.raises(VM22Error, match="not a VM-22 method"):
        ReservingGroup("g", "guesswork", 1.0)


def test_a_stochastic_group_is_built_from_contracts_with_the_floor_inside():
    contracts = block([0.0, 0.0, 100.0, 200.0], floors=[150.0])
    group = stochastic_group("payout", contracts, basis=HALF)
    assert group.method == "stochastic" and group.exclusion is None
    assert group.amount == BasisPair.flat(175.0)
    assert AggregateReserve([group], basis=HALF).post_ceded == 175.0


# --------------------------------------------------------------------------
# §7.C — the ratio test, and the numbers the text does state
# --------------------------------------------------------------------------

def test_the_ratio_divides_by_the_present_value_of_benefits():
    """§7.C.1's denominator ``c`` is "the present value of benefits for the
    policies, adjusted for reinsurance", not the baseline reserve. Dividing
    by the reserve instead gives a different — and larger — ratio, because
    a reserve is smaller than the benefits it funds."""
    test = stochastic_exclusion_test(100.0, [103.0], pv_benefits=1000.0)
    assert test.ratio == pytest.approx(0.003)
    assert test.pv_benefits == 1000.0
    # Against the baseline reserve it would have been 3%, ten times as big.
    assert (103.0 - 100.0) / 100.0 == pytest.approx(0.03)


def test_the_threshold_is_the_lesser_of_six_percent_and_materiality():
    """§7.C.1: "less than the lesser of 6.0% and the percentage change that
    would trigger the company's materiality standard"."""
    assert SERT_CAP == 0.06
    assert VM22_2026.sert_threshold == 0.06
    assert VM22_2026.variant(materiality_standard=0.02).sert_threshold == 0.02
    # A materiality standard looser than the cap cannot loosen the test.
    assert VM22_2026.variant(materiality_standard=0.10).sert_threshold == 0.06


def test_the_test_uses_the_most_adverse_of_the_prescribed_scenarios():
    """``b`` is the largest adjusted scenario reserve over the sixteen
    prescribed economic scenarios crossed with the mortality-improvement
    variants."""
    test = stochastic_exclusion_test(100.0, [101.0, 108.0, 102.0],
                                     pv_benefits=100.0)
    assert test.adverse == 108.0
    assert test.ratio == pytest.approx(0.08)
    assert not test.passed


def test_a_failed_test_will_not_produce_an_exclusion():
    test = stochastic_exclusion_test(100.0, [108.0], pv_benefits=100.0)
    with pytest.raises(VM22Error, match="stochastic reserve is required"):
        test.exclusion()


def test_a_passed_test_produces_an_exclusion_that_carries_its_numbers():
    test = stochastic_exclusion_test(100.0, [103.0], pv_benefits=1000.0)
    assert test.passed
    excluded = test.exclusion(note="2026 year-end")
    assert excluded.basis == "ratio_test"
    assert excluded.ratio == pytest.approx(0.003)
    assert excluded.threshold == 0.06


def test_a_non_positive_benefit_base_has_no_ratio_and_says_so():
    with pytest.raises(VM22Error, match="no ratio to compute"):
        stochastic_exclusion_test(100.0, [110.0], pv_benefits=0.0)


def test_the_test_needs_something_to_be_adverse():
    with pytest.raises(VM22Error, match="at least one adverse"):
        stochastic_exclusion_test(100.0, [], pv_benefits=100.0)


def test_every_exclusion_route_the_text_gives_is_available():
    """§7.B lists three: the ratio test, a demonstration, and a qualified
    actuary's certification."""
    assert Exclusion(basis="ratio_test", ratio=0.01).basis == "ratio_test"
    assert Exclusion(basis="demonstration").basis == "demonstration"
    assert Exclusion(basis="certification",
                     certified_by="A. Actuary").certified_by == "A. Actuary"
    with pytest.raises(VM22Error, match="name the actuary"):
        Exclusion(basis="certification")
    with pytest.raises(VM22Error, match="recheck"):
        Exclusion(basis="ratio_test")
    with pytest.raises(VM22Error, match="needs a basis"):
        Exclusion(basis="because I said so")


# --------------------------------------------------------------------------
# The dated basis
# --------------------------------------------------------------------------

def test_the_basis_carries_the_numbers_the_text_states_and_cites_them():
    assert VM22_2026.label == "VM-22 (2026)"
    assert VM22_2026.cte_level == 0.70
    assert VM22_2026.sert_cap == 0.06
    assert "§3.D.2" in VM22_2026.text and "§7.C.1" in VM22_2026.text
    # And says what it does not carry.
    assert "not carried here" in VM22_2026.text


def test_the_company_materiality_standard_is_not_invented():
    """The half of §7.C.1 only a company knows stays ``None``."""
    assert VM22_2026.materiality_standard is None


def test_an_impossible_basis_is_refused():
    with pytest.raises(VM22Error, match="outside"):
        VM22Basis(label="bad", cte_level=1.0)
    with pytest.raises(VM22Error, match="negative"):
        VM22Basis(label="bad", sert_cap=-0.01)


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
    contracts = [Contract("A", [1.0, 2.0, 3.0]), Contract("B", [1.0, 2.0])]
    with pytest.raises(VM22Error, match="different futures"):
        aggregate_stochastic_reserve(contracts)


def test_an_empty_group_and_an_empty_contract_are_refused():
    with pytest.raises(VM22Error, match="at least one contract"):
        aggregate_stochastic_reserve([])
    with pytest.raises(VM22Error, match="no scenario reserves"):
        Contract("A", [])


# --------------------------------------------------------------------------
# End to end
# --------------------------------------------------------------------------

def annuity_block(seed=3, periods=25, scenarios=500, n_contracts=4):
    """A fixed-annuity-shaped block: spread income against a guarantee."""
    rng = np.random.default_rng(seed)
    rates = rng.normal(0.035, 0.012, (periods, scenarios))
    contracts = []
    for i in range(n_contracts):
        account = 100_000.0 * (1.0 + 0.02) ** np.arange(periods)[:, None]
        net = (rates - (0.02 + 0.004 * i)) * account
        contracts.append(Contract.from_cashflows(
            f"FA{i}", net, rates, cash_surrender_value=1_000.0 * (i + 1)))
    return contracts, rates


def test_a_projected_annuity_block_reserves_end_to_end():
    contracts, _ = annuity_block()
    reserve = AggregateReserve([stochastic_group("payout", contracts)])
    assert reserve.post_ceded > 0.0
    assert reserve.by_method()["stochastic"] == reserve.value

    scenario = sum(c.scenario_reserve for c in contracts)
    floors = sum(c.cash_surrender_value for c in contracts)
    assert reserve.post_ceded == cte(np.maximum(scenario, floors), 0.70)


def test_the_contract_helper_is_rfc_016s_scenario_reserves():
    contracts, rates = annuity_block(n_contracts=1)
    net = (rates - 0.02) * (100_000.0 * (1.0 + 0.02)
                            ** np.arange(rates.shape[0])[:, None])
    assert np.array_equal(contracts[0].scenario_reserve,
                          scenario_reserves(net, rates))


def test_a_library_template_feeds_the_reserve_without_an_adapter():
    """The pairing the item asks for: a `FixedAnnuity` run is the source of
    the net cashflows. The scenario dimension is supplied here rather than
    by the template — `FixedAnnuity` is a deterministic projection — and
    saying so beats implying the template produced it.
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
    outgo = (result.array("payments")
             + result.array("death_benefits")).sum(axis=1)

    rng = np.random.default_rng(5)
    rates = rng.normal(0.035, 0.012, (outgo.size, 200))
    fund = result.array("fund_eoy_per_pol").sum(axis=1)[:, None]
    net = fund * rates - outgo[:, None]

    contract = Contract.from_cashflows("FA1", net, rates,
                                       cash_surrender_value=50_000.0)
    reserve = AggregateReserve([stochastic_group("payout", [contract])])
    assert reserve.post_ceded >= 50_000.0       # the floor is a floor
    assert np.array_equal(contract.scenario_reserve,
                          scenario_reserves(net, rates))


def test_a_real_block_still_obeys_the_decomposition():
    contracts, _ = annuity_block()
    gap = aggregation_decomposition(contracts)
    assert isinstance(gap, AggregationGap)
    assert gap.gap == pytest.approx(gap.floor_effect
                                    + gap.diversification_effect)
    assert gap.prescribed >= gap.floor_outside - 1e-9


def test_the_prescribed_reserve_can_exceed_the_sum_of_standalone_ones():
    """The finding, hand-computed.

    Reserving contract by contract is supposed to be the conservative
    thing to do, and against the prescribed ordering it is not. Each of
    these contracts has scenario reserves [0, 0, 0, 150] and a surrender
    value of 100, so each one's own CTE(50) is 75 — below its own floor —
    and seriatim reserves 100 + 100 = 200. Pooled, the scenario reserves
    are [0, 0, 0, 300], floored at the aggregate 200, and CTE(50) of
    [200, 200, 200, 300] is 250.

    The mechanism: seriatim gives each contract its own floor *and* its own
    tail; the prescribed calculation applies the summed floor to the pooled
    reserve, so the pool has a tail no individual contract had. No
    diversification is involved anywhere.
    """
    contracts = block([0.0, 0.0, 0.0, 150.0], [0.0, 0.0, 0.0, 150.0],
                      floors=[100.0, 100.0])
    gap = aggregation_decomposition(contracts, basis=HALF)
    assert gap.seriatim == 200.0
    assert gap.prescribed == 250.0
    assert gap.floor_outside == 200.0
    assert gap.prescribed > gap.seriatim

    # The old claim — "seriatim is never smaller" — was true of the natural
    # misreading and is false of the text.
    assert gap.seriatim >= gap.floor_outside


# --------------------------------------------------------------------------
# Known deviations from the text — pinned so they cannot be forgotten
# --------------------------------------------------------------------------

def test_the_pimr_is_subtracted_from_the_scenario_reserve():
    """§4.B.1.a: "The starting asset amount, less the allocated amount of
    PIMR, plus the greatest present value …". An allocated balance-sheet
    amount rather than something a projection produces, so it is an
    argument — and it defaults to zero, which is right for a block that has
    none and wrong for a block that does."""
    rates = np.full((4, 3), 0.03)
    net = np.full((4, 3), -100.0)
    without = Contract.from_cashflows("A", net, rates, starting_assets=500.0)
    with_pimr = Contract.from_cashflows("A", net, rates, starting_assets=500.0,
                                        pimr=40.0)
    assert with_pimr.scenario_reserve == pytest.approx(
        without.scenario_reserve - 40.0)


def test_the_greatest_present_value_is_reduced_before_it_is_aggregated():
    """**A known deviation from §3.F.5.a.ii**, pinned rather than hidden.

    The text says "Combine the present values for each model segment and
    take the greatest present value *in aggregate* for each scenario" —
    aggregate first, reduce second. This module reduces first (RFC-016
    maximises over dates per contract) and sums after, so it computes
    ``Σ max`` where the text asks for ``max Σ``.

    Since ``Σ max ≥ max Σ`` the deviation overstates, which is the
    conservative direction — but it is still not what the chapter says, and
    fixing it needs `Contract` to carry the deficiency path rather than the
    reduced reserve. This test exists so that change cannot land silently
    and so nobody mistakes the current behaviour for the prescribed one.
    """
    from engine.report.pbr import (
        accumulated_surplus,
        path_discount_factors,
    )

    # Two contracts whose worst dates differ, which is exactly when the two
    # orderings part company.
    rates = np.full((3, 1), 0.0)
    a_net = np.array([[-100.0], [50.0], [50.0]])
    b_net = np.array([[50.0], [-100.0], [50.0]])
    a = Contract.from_cashflows("A", a_net, rates)
    b = Contract.from_cashflows("B", b_net, rates)
    ours = float(a.scenario_reserve[0] + b.scenario_reserve[0])

    # What §3.F.5.a.ii asks for: sum the discounted deficiencies across
    # segments, then take the greatest present value of the aggregate.
    combined = None
    for net in (a_net, b_net):
        surplus = accumulated_surplus(net, rates, 0.0)
        discounted = -surplus * path_discount_factors(rates)
        combined = discounted if combined is None else combined + discounted
    prescribed = float(max(combined.max(), 0.0))

    assert ours > prescribed, "the deviation has gone — update the docs"
    # A's worst date is period 1 (100) and B's is period 2 (50), so summing
    # the reduced reserves gives 150. The aggregated path peaks at 100 in
    # period 2, which is what §3.F.5.a.ii asks for. The module reports 150.
    assert ours == 150.0
    assert prescribed == 100.0


def test_the_greatest_present_value_is_not_floored_for_vm22():
    """**V1, fixed.** §4.B.1.a's guidance note: "The greatest present value
    of accumulated deficiencies **can be negative**."

    RFC-016 floors it, on the reasoning that a surplus is not a negative
    reserve, and that is how VM-20 and VM-21 are read here. VM-22 says the
    opposite in terms, so `Contract.from_cashflows` takes the unfloored
    path by default and the shared function keeps the floor for the other
    two chapters.

    A block holding 500 of assets that only ever receives money needs
    nothing: floored it reserves its starting assets exactly, unfloored it
    reserves zero, and the difference is the whole surplus.
    """
    from engine.report.pbr import (
        greatest_present_value_of_accumulated_deficiency as gpvad,
    )

    rates = np.full((3, 1), 0.0)
    net = np.array([[100.0], [100.0], [100.0]])      # never underwater

    # With no starting assets the surplus at t=0 is zero, so the unfloored
    # greatest present value is -0.0 and the floor changes nothing. It is
    # the *funded* block where the two part company.
    assert gpvad(net, rates, 0.0)[0] == 0.0
    assert gpvad(net, rates, 500.0)[0] == 0.0                      # floored
    assert gpvad(net, rates, 500.0, floor_at_zero=False)[0] == -500.0

    vm22 = Contract.from_cashflows("A", net, rates, starting_assets=500.0)
    assert vm22.scenario_reserve[0] == 0.0

    floored = Contract.from_cashflows("A", net, rates, starting_assets=500.0,
                                      floor_at_zero=True)
    assert floored.scenario_reserve[0] == 500.0
    assert vm22.scenario_reserve[0] < floored.scenario_reserve[0]


def test_the_other_chapters_keep_the_floor_by_default():
    """The reason this is a flag and not a change of behaviour: the
    function serves three chapters and only one has been read against its
    own text. VM-20 and VM-21 are bit-for-bit unmoved."""
    from engine.report.pbr import scenario_reserves as pbr_scenario_reserves

    rates = np.full((3, 2), 0.0)
    net = np.array([[100.0, -400.0], [100.0, 50.0], [100.0, 50.0]])
    default = pbr_scenario_reserves(net, rates, 200.0)
    explicit = pbr_scenario_reserves(net, rates, 200.0, floor_at_zero=True)
    assert np.array_equal(default, explicit)
    assert default[0] == 200.0        # the well-funded scenario stays put


# --------------------------------------------------------------------------
# §3.F.1 — the categories that may not be pooled
# --------------------------------------------------------------------------

def test_pooling_across_reserving_categories_is_refused():
    """§3.F.1, and the only deviation found in VM-22 so far that made the
    reserve too *small*.

    Aggregating buys diversification, so a module that pools freely across
    Reserving Categories reports less than the chapter permits. Every other
    deviation found erred the safe way; this one did not, which is why it
    is a refusal rather than a note.
    """
    payout = Contract("P", [0.0, 0.0, 100.0, 200.0], category="payout_annuity")
    accum = Contract("A", [200.0, 100.0, 0.0, 0.0], category="accumulation")
    longevity = Contract("L", [10.0, 10.0, 10.0, 10.0],
                         category="longevity_reinsurance")

    with pytest.raises(VM22Error, match="§3.F.1 forbids aggregating"):
        aggregate_stochastic_reserve([payout, accum], basis=HALF)
    with pytest.raises(VM22Error, match="§3.F.1 forbids aggregating"):
        aggregate_stochastic_reserve([payout, longevity], basis=HALF)


def test_payout_and_accumulation_combine_only_on_the_attestation():
    """§3.F.2 permits exactly one pair, and only where the company manages
    both in an integrated risk-management process within a single portfolio
    or portfolios sharing an ALM strategy. This module cannot check either,
    so it takes the caller's word — and makes them say it."""
    payout = Contract("P", [0.0, 0.0, 100.0, 200.0], category="payout_annuity")
    accum = Contract("A", [200.0, 100.0, 0.0, 0.0], category="accumulation")
    pooled = aggregate_stochastic_reserve(
        [payout, accum], basis=HALF, combined_payout_accumulation=True)
    assert pooled == 200.0            # the diversified figure, as before


def test_longevity_reinsurance_never_combines():
    """§3.F.2's exception names payout and accumulation. Longevity
    reinsurance is not in it, so the attestation does not unlock it."""
    longevity = Contract("L", [0.0, 0.0, 100.0, 200.0],
                         category="longevity_reinsurance")
    accum = Contract("A", [200.0, 100.0, 0.0, 0.0], category="accumulation")
    with pytest.raises(VM22Error, match="§3.F.1 forbids"):
        aggregate_stochastic_reserve([longevity, accum], basis=HALF,
                                     combined_payout_accumulation=True)


def test_one_category_and_unclassified_contracts_both_aggregate():
    """A single declared category is fine, and so is a wholly unclassified
    pool — which is backwards compatible and, as the docstring says, not a
    VM-22 reserve, because nothing has held it to §3.F.1."""
    same = [Contract("P1", [0.0, 0.0, 100.0, 200.0], category="payout_annuity"),
            Contract("P2", [200.0, 100.0, 0.0, 0.0], category="payout_annuity")]
    assert aggregate_stochastic_reserve(same, basis=HALF) == 200.0

    unclassified = block([0.0, 0.0, 100.0, 200.0], [200.0, 100.0, 0.0, 0.0])
    assert aggregate_stochastic_reserve(unclassified, basis=HALF) == 200.0
    assert all(c.category is None for c in unclassified)


def test_an_unknown_reserving_category_is_refused():
    with pytest.raises(VM22Error, match="§3.F.1 has"):
        Contract("X", [1.0, 2.0], category="whatever")
    assert set(RESERVING_CATEGORIES) == {"payout_annuity",
                                         "longevity_reinsurance",
                                         "accumulation"}


# --------------------------------------------------------------------------
# V2 — §3.F.5.a: combine, then take the greatest
# --------------------------------------------------------------------------

def segments_from(rows, **kw):
    """Segments straight from discounted deficiency paths."""
    return [ModelSegment(name=f"S{i}", deficiency_path=np.array(r, float),
                         **kw)
            for i, r in enumerate(rows)]


def test_the_greatest_present_value_is_taken_after_combining():
    """**V2, fixed.** §3.F.5.a.ii: "Combine the present values for each
    model segment and take the greatest present value in aggregate for each
    scenario."

    Two segments each peaking at 100, on different dates — the case where
    the two orders part company. Reducing first gives 100 + 100 = 200;
    combining first gives a path that never exceeds 100. The chapter asks
    for 100, and the difference is a whole segment's worth of reserve.
    """
    a = ModelSegment("A", [[100.0], [0.0], [0.0]])
    b = ModelSegment("B", [[0.0], [100.0], [0.0]])
    combined_first = segment_scenario_reserves([a, b])
    assert combined_first[0] == 100.0

    # Reducing first — what summing segment maxima would give.
    reduced_first = sum(float(s.deficiency_path.max()) for s in (a, b))
    assert reduced_first == 200.0
    assert combined_first[0] < reduced_first


def test_one_segment_agrees_with_the_contract_path():
    """The change has to be invisible where the two orders coincide, or it
    is not a reordering but a different calculation."""
    rates = np.full((4, 1), 0.02)
    net = np.array([[-100.0], [30.0], [-40.0], [60.0]])
    segment = ModelSegment.from_cashflows("S", net, rates,
                                          starting_assets=25.0)
    contract = Contract.from_cashflows("C", net, rates, starting_assets=25.0)
    assert segment_scenario_reserves([segment]) == pytest.approx(
        contract.scenario_reserve, rel=0, abs=1e-12)


def test_the_starting_assets_and_pimr_land_where_the_text_puts_them():
    """§3.F.5.a.ii: "the sum of the initial assets of each model segment and
    the greatest present value of the aggregated deficiencies, less the
    aggregate PIMR"."""
    a = ModelSegment("A", [[100.0]], starting_assets=500.0, pimr=40.0)
    b = ModelSegment("B", [[-30.0]], starting_assets=200.0, pimr=10.0)
    # assets 700, aggregated greatest 70, PIMR 50.
    assert segment_scenario_reserves([a, b])[0] == 720.0


def test_the_longevity_category_carries_its_own_floor():
    """§4.B.1: "For the Longevity Reinsurance Reserving Category, the
    scenario reserve for any given scenario shall not be less than 2% of
    the scheduled longevity benefits payable … within the next 12 months."

    Which floor applies is decided by the Reserving Category, which is what
    makes the category a calculation input rather than a label."""
    assert LONGEVITY_FLOOR_RATE == 0.02
    lean = ModelSegment("L", [[10.0]], category="longevity_reinsurance",
                        cash_surrender_value=5.0,
                        longevity_benefits_12m=1_000.0)
    assert lean.floor() == 20.0                      # 2% of 1,000 beats 5
    assert segment_scenario_reserves([lean])[0] == 20.0

    # The general floor still wins where it is larger.
    rich = ModelSegment("L2", [[10.0]], category="longevity_reinsurance",
                        cash_surrender_value=90.0,
                        longevity_benefits_12m=1_000.0)
    assert rich.floor() == 90.0


def test_a_twelve_month_benefit_outside_the_longevity_category_is_refused():
    """§4.B.1's floor belongs to one category. A segment carrying the input
    without the category has either mislabelled itself or is about to get a
    floor it is not entitled to."""
    with pytest.raises(VM22Error, match="belongs to 'longevity_reinsurance'"):
        ModelSegment("X", [[1.0]], category="accumulation",
                     longevity_benefits_12m=100.0)


def test_segments_obey_the_category_rules_too():
    payout = ModelSegment("P", [[1.0]], category="payout_annuity")
    accum = ModelSegment("A", [[1.0]], category="accumulation")
    longevity = ModelSegment("L", [[1.0]], category="longevity_reinsurance")

    with pytest.raises(VM22Error, match="§3.F.1 forbids"):
        segment_scenario_reserves([payout, accum])
    assert segment_scenario_reserves(
        [payout, accum], combined_payout_accumulation=True) is not None
    with pytest.raises(VM22Error, match="§3.F.1 forbids"):
        segment_scenario_reserves([payout, longevity],
                                  combined_payout_accumulation=True)


def test_a_dr_segment_cannot_be_pooled_with_a_non_dr_segment():
    """§3.F.3 — the rule `Contract` could not express, because a contract
    never knew whether its group carried a DR."""
    with_dr = ModelSegment("D", [[1.0]], carries_dr=True)
    without = ModelSegment("N", [[1.0]], carries_dr=False)
    with pytest.raises(VM22Error, match="§3.F.3"):
        segment_scenario_reserves([with_dr, without])
    assert segment_scenario_reserves([with_dr, with_dr]) is not None


def test_the_segment_reserve_is_cte70_of_the_scenario_reserves():
    rows = [[[0.0, 0.0, 100.0, 200.0]]]
    segment = ModelSegment("S", rows[0])
    got = segment_stochastic_reserve([segment], basis=HALF)
    assert got == cte(segment_scenario_reserves([segment]), 0.5) == 150.0


def test_segments_that_disagree_about_the_projection_are_refused():
    with pytest.raises(VM22Error, match="different futures"):
        segment_scenario_reserves([ModelSegment("A", [[1.0, 2.0]]),
                                   ModelSegment("B", [[1.0]])])
    with pytest.raises(VM22Error, match="projection length"):
        segment_scenario_reserves([ModelSegment("A", [[1.0], [2.0]]),
                                   ModelSegment("B", [[1.0]])])
    with pytest.raises(VM22Error, match="at least one model segment"):
        segment_scenario_reserves([])


# --------------------------------------------------------------------------
# V3 — §3.B and §5: every component on both bases
# --------------------------------------------------------------------------

def test_a_block_that_cedes_nothing_reports_one_number_twice():
    """§3.B requires both bases of every component. Where there is no
    treaty the two coincide, and the pair has to collapse — a module whose
    pre- and post-ceded figures differed on a block with no reinsurance
    would be reporting an artefact of its own plumbing."""
    contracts = block([0.0, 0.0, 100.0, 200.0], floors=[150.0])
    group = stochastic_group("payout", contracts, basis=HALF)
    assert group.amount == BasisPair(pre_ceded=175.0, post_ceded=175.0)
    assert group.amount.collapsed and group.amount.ceded_credit == 0.0

    reserve = AggregateReserve([group], basis=HALF)
    assert reserve.post_ceded == reserve.pre_ceded == 175.0


def test_the_two_bases_are_two_projections_and_not_an_adjustment():
    """§5.A.2.a determines the post-ceded SR "reflecting the effects of
    reinsurance treaties … including … all projected reinsurance premiums
    or other costs and all reinsurance recoveries"; §5.A.2.b determines the
    pre-ceded one "ignoring the effects of reinsurance ceded within the
    projections".

    So the pre-ceded figure is *run*, not derived. This asserts the module
    takes a second projection and reports what it computed, rather than
    scaling or grossing up the first."""
    ceded = block([0.0, 0.0, 100.0, 200.0])          # net of recoveries
    gross = block([0.0, 0.0, 260.0, 400.0])          # the same block, gross
    group = stochastic_group("annuity", ceded, pre_ceded_contracts=gross,
                             basis=HALF)
    assert group.amount.post_ceded == 150.0          # CTE(50) of the ceded
    assert group.amount.pre_ceded == 330.0           # and of the gross
    assert group.amount.ceded_credit == 180.0
    assert not group.amount.collapsed


def test_the_segment_path_carries_both_bases_too():
    """The prescribed order (§3.F.5.a) and the two bases (§3.B) are
    independent requirements, and a module that had one without the other
    would satisfy neither section fully."""
    ceded = [ModelSegment("S", [[0.0, 0.0, 100.0, 200.0]])]
    gross = [ModelSegment("S", [[0.0, 0.0, 150.0, 300.0]])]
    group = segment_group("seg", ceded, pre_ceded_segments=gross, basis=HALF)
    assert group.amount.post_ceded == 150.0
    assert group.amount.pre_ceded == 225.0
    assert group.method == "stochastic"


def test_the_formulaic_component_subtracts_the_reinsurance_credit():
    """§5.A.1: "for the reserve amount valued using requirements in VM-A,
    VM-C, VM-M, and VM-V, the post-reinsurance ceded reserve is determined
    by subtracting the reinsurance reserve credit."

    The one component where the bases *are* a number and an adjustment —
    and §5.A.3 says which way round: the methodology "produces reserves on
    a pre-reinsurance ceded basis", so the stated amount is the gross one
    and the credit comes off it."""
    excluded = Exclusion(basis="ratio_test", ratio=0.01, threshold=SERT_CAP)
    group = ReservingGroup.formulaic("vm-a", 500.0, exclusion=excluded,
                                     reinsurance_reserve_credit=120.0)
    assert group.amount == BasisPair(pre_ceded=500.0, post_ceded=380.0)
    assert group.amount.ceded_credit == 120.0
    assert group.method == "formulaic"


def test_a_credit_bigger_than_the_reserve_it_relieves_is_refused():
    """A reserve credit cannot exceed the reserve ceded. Subtracting one
    that does would report a negative statutory reserve, which is the
    silently-wrong number this module exists to refuse."""
    excluded = Exclusion(basis="ratio_test", ratio=0.01, threshold=SERT_CAP)
    with pytest.raises(VM22Error, match="cannot exceed the reserve ceded"):
        ReservingGroup.formulaic("vm-a", 100.0, exclusion=excluded,
                                 reinsurance_reserve_credit=150.0)
    with pytest.raises(VM22Error, match="is not negative"):
        ReservingGroup.formulaic("vm-a", 100.0, exclusion=excluded,
                                 reinsurance_reserve_credit=-10.0)
    # The boundary is allowed: a fully ceded block reserves zero, not less.
    full = ReservingGroup.formulaic("vm-a", 100.0, exclusion=excluded,
                                    reinsurance_reserve_credit=100.0)
    assert full.amount.post_ceded == 0.0


def test_the_non_qualifying_treaty_charge_is_added_and_not_netted():
    """§5.A.2.a.iv: where a treaty "does not qualify for credit for
    reinsurance but treating [it] as if it did so qualify would result in a
    reduction to the company's surplus, then the company shall increase the
    aggregate reserve by the absolute value of such reductions in surplus."

    **Additive**, and on the aggregate reserve rather than inside a
    projection — no first-principles design would have contained this term,
    which is why §5 had to be read rather than paraphrased. It lands
    post-ceded only: the pre-ceded basis ignores reinsurance ceded by
    construction (§5.A.2.b), so a reinsurance charge there would contradict
    the basis's own definition."""
    group = ReservingGroup("g", "stochastic", 1_000.0)
    plain = AggregateReserve([group])
    charged = AggregateReserve([group], non_qualifying_surplus_reduction=75.0)

    assert charged.post_ceded == plain.post_ceded + 75.0
    assert charged.pre_ceded == plain.pre_ceded == 1_000.0
    # It increases the reserve. A netting would have reduced it.
    assert charged.post_ceded > charged.pre_ceded


def test_the_surplus_charge_will_not_be_taken_as_a_relief():
    """"the absolute value of such reductions" is a charge. A negative one
    would relieve the reserve, which is the direction the text forbids and
    the direction a sign error takes."""
    with pytest.raises(VM22Error, match="§5.A.2.a.iv"):
        AggregateReserve([ReservingGroup("g", "stochastic", 10.0)],
                         non_qualifying_surplus_reduction=-5.0)


def test_the_charge_is_not_attributed_to_any_method():
    """§5.A.2.a.iv's charge is not a group's reserve and has no §3.A method
    to belong to, so `by_method` sums to the group total and `value` sits
    above it. Asserted because the gap is exactly the sort of thing a
    reconciliation would otherwise chase."""
    reserve = AggregateReserve(
        [ReservingGroup("g", "stochastic", 200.0)],
        non_qualifying_surplus_reduction=30.0)
    split = reserve.by_method()
    assert sum(p.post_ceded for p in split.values()) == 200.0
    assert reserve.group_total.post_ceded == 200.0
    assert reserve.value.post_ceded == 230.0


def test_the_two_bases_may_disagree_about_the_method():
    """§5.A.3: "it is possible that the pre-reinsurance-ceded reserves would
    pass the relevant exclusion test … while the post-reinsurance-ceded
    reserves might not, or vice versa."

    So the pair is not two numbers from one valuation — it is two
    valuations, and `by_method` has to split the bases independently or it
    reports a group under a method it was not valued by on that basis."""
    excluded = Exclusion(basis="ratio_test", ratio=0.01, threshold=SERT_CAP)
    group = ReservingGroup(
        "mixed", "stochastic", BasisPair(pre_ceded=90.0, post_ceded=140.0),
        pre_ceded_method="formulaic", pre_ceded_exclusion=excluded)
    assert group.methods == {"post_ceded": "stochastic",
                             "pre_ceded": "formulaic"}

    split = AggregateReserve([group]).by_method()
    assert split["stochastic"] == BasisPair(pre_ceded=0.0, post_ceded=140.0)
    assert split["formulaic"] == BasisPair(pre_ceded=90.0, post_ceded=0.0)


def test_each_basis_must_state_its_own_reason_for_leaving_the_sr_out():
    """The §7 rule — a component omitted on a stated basis, or computed —
    applies to each basis separately once §5.A.3 lets them differ. A
    pre-ceded formulaic valuation with nobody's exclusion behind it is the
    same silent omission the post-ceded check already refuses."""
    with pytest.raises(VM22Error, match="on the pre-ceded basis"):
        ReservingGroup("g", "stochastic", 10.0, pre_ceded_method="formulaic")
    with pytest.raises(VM22Error, match="name the method it applies to"):
        ReservingGroup("g", "stochastic", 10.0,
                       pre_ceded_exclusion=Exclusion(
                           basis="certification", certified_by="A. Actuary"))
    with pytest.raises(VM22Error, match="not a VM-22 method"):
        ReservingGroup("g", "stochastic", 10.0, pre_ceded_method="guesswork")


def test_the_pair_adds_basis_by_basis_across_groups():
    """§3.A's sum happens twice, once per basis, and never mixes them."""
    excluded = Exclusion(basis="ratio_test", ratio=0.01, threshold=SERT_CAP)
    reserve = AggregateReserve([
        ReservingGroup("modelled", "stochastic",
                       BasisPair(pre_ceded=300.0, post_ceded=140.0)),
        ReservingGroup.formulaic("excluded", 100.0, exclusion=excluded,
                                 reinsurance_reserve_credit=25.0),
    ])
    assert reserve.value == BasisPair(pre_ceded=400.0, post_ceded=215.0)
    assert reserve.value.ceded_credit == 185.0
    assert BasisPair(1.0, 2.0) + BasisPair(10.0, 20.0) == BasisPair(11.0, 22.0)


def test_the_ceded_credit_may_be_negative_because_a_treaty_can_cost():
    """Nothing constrains the sign of `ceded_credit`, and it would be wrong
    to: §5.A.2.a.iv exists precisely because a treaty can reduce surplus
    rather than relieve it. A module that asserted pre ≥ post would refuse
    the case the text legislates for."""
    costly = BasisPair(pre_ceded=100.0, post_ceded=130.0)
    assert costly.ceded_credit == -30.0
    assert AggregateReserve(
        [ReservingGroup("g", "stochastic", costly)]).post_ceded == 130.0


def test_the_reported_dictionary_carries_both_bases_everywhere():
    """A report that gave one figure would leave the reader to guess which
    basis they had, which §3.B makes an unanswerable question."""
    excluded = Exclusion(basis="ratio_test", ratio=0.01, threshold=SERT_CAP)
    reserve = AggregateReserve(
        [ReservingGroup.formulaic("vm-a", 100.0, exclusion=excluded,
                                  reinsurance_reserve_credit=40.0)],
        non_qualifying_surplus_reduction=5.0)
    out = reserve.to_dict()
    assert out["value"] == {"pre_ceded": 100.0, "post_ceded": 65.0}
    assert out["non_qualifying_surplus_reduction"] == 5.0
    assert out["groups"][0]["ceded_credit"] == 40.0
    assert out["groups"][0]["methods"] == {"post_ceded": "formulaic",
                                           "pre_ceded": "formulaic"}
    assert out["groups"][0]["excluded"] == {"post_ceded": "ratio_test",
                                            "pre_ceded": "ratio_test"}
    for pair in out["by_method"].values():
        assert set(pair) == {"pre_ceded", "post_ceded"}


# --------------------------------------------------------------------------
# V4 — §13: the aggregate reserve, back down to the contracts
# --------------------------------------------------------------------------

def account_value(id, apv, csv, **kw):
    return ContractRecord(id=id, scenario_apv=apv, category="accumulation",
                          cash_surrender_value=csv, **kw)


def test_the_contract_reserve_is_the_mav_plus_the_allocated_excess():
    """§13.A: "The contract-level reserve for each contract shall be the sum
    of … the contract's minimum allocation value (MAV) … [and] the
    contract's allocated excess reserve (AER)", with §13.D.1 sharing the
    excess "in proportion to the excess of the Scenario APV over the MAV".

    Two account-value contracts with equal surrender values and unequal
    Scenario APVs: the MAVs tie and the excess splits on the risk measure,
    which is the whole design intent — "an indexed annuity contract with a
    high benefit GLWB will typically have a larger allocated excess
    reserve"."""
    records = [account_value("A", apv=140.0, csv=100.0),
               account_value("B", apv=120.0, csv=100.0)]
    # MAVs are the surrender values (§13.C.2): 200 in aggregate.
    # Excess 60 splits on excess APV 40 : 20.
    got = allocate_aggregate_reserve(records, 260.0)
    assert got.rule == "13.D.1"
    assert got.amounts == {"A": 140.0, "B": 120.0}
    assert got.aggregate_mav == 200.0 and got.excess == 60.0
    assert got.total == 260.0 and got.reconciles


def test_the_allocation_sums_to_the_aggregate_reserve_exactly():
    """The property §3.H's allocation exists to have: what is allocated is
    the reserve, not a number near it. Asserted on an awkward split rather
    than a round one, because thirds are where a proportional allocation
    would show its drift."""
    records = [account_value("A", apv=317.0, csv=11.0),
               account_value("B", apv=53.0, csv=7.0),
               account_value("C", apv=101.0, csv=3.0)]
    got = allocate_aggregate_reserve(records, 1_000.0 / 3.0)
    assert got.rule == "13.D.1"
    assert got.total == pytest.approx(1_000.0 / 3.0, rel=0, abs=1e-12)
    assert got.reconciles


def test_a_payout_group_can_never_reach_the_proportional_rule():
    """**The structural finding.** §13.C.1 makes a payout contract's MAV
    "the greater of … the Scenario APV … or … the cash surrender value", so
    its excess Scenario APV is zero by construction and §13.D.1 can never
    bind. §13.D.3 — "if all contracts in the group have an excess Scenario
    APV that is floored at zero, then use the MAV to allocate" — is not a
    fallback for this category; it is the rule.

    A test that only exercised an accumulation group would never see it,
    and a reader would reasonably assume §13.D.3 was an edge case."""
    records = [ContractRecord("P1", scenario_apv=300.0,
                              category="payout_annuity"),
               ContractRecord("P2", scenario_apv=100.0,
                              category="payout_annuity")]
    assert all(r.excess_apv == 0.0 for r in records)

    got = allocate_aggregate_reserve(records, 480.0)
    assert got.rule == "13.D.3"
    # MAVs 300 and 100; excess 80 split 3:1 on the MAVs.
    assert got.amounts == {"P1": 360.0, "P2": 120.0}
    assert got.total == 480.0


def test_the_mav_is_a_different_rule_for_each_reserving_category():
    """§13.C's three cases are §3.F.1's three Reserving Categories, with
    §13.C.2's "Account Value Based Annuity" the chapter's undefined name for
    the Accumulation category. Getting the rule from the category is what
    makes the classification a calculation input a third time — after
    §3.F.1's pooling rule and §4.B.1's floor."""
    payout = ContractRecord("P", scenario_apv=90.0, category="payout_annuity",
                            cash_surrender_value=70.0)
    assert payout.mav == 90.0                      # §13.C.1: the greater
    lean = ContractRecord("P2", scenario_apv=50.0, category="payout_annuity",
                          cash_surrender_value=70.0)
    assert lean.mav == 70.0

    accum = account_value("A", apv=500.0, csv=80.0)
    assert accum.mav == 80.0                       # §13.C.2: the CSV alone

    longevity = ContractRecord("L", scenario_apv=10.0,
                               category="longevity_reinsurance",
                               longevity_benefits_12m=1_000.0)
    assert longevity.mav == 20.0                   # §13.C.3: 2% of benefits
    assert set(MAV_RULES) == set(RESERVING_CATEGORIES)


def test_the_scenario_apv_is_floored_at_the_mav_before_it_is_a_weight():
    """§13.D.2: "If the Scenario APV for any contract is less than the MAV,
    then the excess Scenario APV to be used for allocating the excess
    aggregate reserve to that contract shall be floored at zero."

    Without the floor a contract whose APV sits below its surrender value
    would take a *negative* share of the excess, which is a transfer of
    reserve from a weak contract to a strong one."""
    records = [account_value("rich", apv=200.0, csv=100.0),
               account_value("poor", apv=20.0, csv=100.0)]
    assert records[1].excess_apv == 0.0
    got = allocate_aggregate_reserve(records, 300.0)
    assert got.amounts == {"rich": 200.0, "poor": 100.0}


def test_a_group_with_no_excess_apv_anywhere_allocates_on_the_mav():
    """§13.D.3, reached by an accumulation group this time: every contract
    is under water against its surrender value, so there is no risk measure
    to allocate on and the MAV stands in for one."""
    records = [account_value("A", apv=10.0, csv=100.0),
               account_value("B", apv=20.0, csv=300.0)]
    got = allocate_aggregate_reserve(records, 480.0)
    assert got.rule == "13.D.3"
    assert got.amounts == {"A": 120.0, "B": 360.0}   # 80 split 1:3


def test_the_shortfall_goes_to_the_life_contingent_contracts_alone():
    """§13.D.4: "If a group's aggregate reserve is less than the group's
    aggregate MAV, that difference should be allocated to life contingent
    contracts in proportion to each life contingent contract's MAV to the
    sum of the life contingent contracts MAV."

    The term-certain contract keeps its MAV untouched; the whole shortfall
    lands on the contracts whose obligation runs on a life."""
    records = [
        ContractRecord("LC1", scenario_apv=200.0, category="payout_annuity",
                       life_contingent=True),
        ContractRecord("LC2", scenario_apv=100.0, category="payout_annuity",
                       life_contingent=True),
        ContractRecord("TC", scenario_apv=100.0, category="payout_annuity"),
    ]
    got = allocate_aggregate_reserve(records, 340.0)   # aggregate MAV is 400
    assert got.rule == "13.D.4" and got.excess == -60.0
    assert got.amounts == {"LC1": 160.0, "LC2": 80.0, "TC": 100.0}
    assert got.total == 340.0 and got.reconciles


def test_a_shortfall_with_nothing_life_contingent_to_carry_it_is_refused():
    """§13.D.4 names the contracts the shortfall goes to and this group has
    none of them. Spreading it over the term-certain contracts instead
    would be a reserve nobody prescribed, which is worse than stopping."""
    records = [ContractRecord("TC", scenario_apv=100.0,
                              category="payout_annuity")]
    with pytest.raises(VM22Error, match="nowhere the text puts it"):
        allocate_aggregate_reserve(records, 40.0)


def test_the_surrender_floor_under_a_shortfall_breaks_the_reconciliation():
    """§13.D.4 ends "All contracts are floored at their cash surrender
    value" — applied *after* the shortfall is allocated, so the amounts
    need no longer sum to the aggregate reserve. §13's preamble promises
    the allocation holds every contract at its surrender value, and the
    only way to keep that promise is to stop reconciling.

    Reported rather than reconciled away: a module that scaled the result
    back down would be breaking the guarantee the floor exists to keep, and
    one that stayed silent would leave the break to be found in a
    reconciliation."""
    records = [
        ContractRecord("LC", scenario_apv=200.0, category="payout_annuity",
                       cash_surrender_value=190.0, life_contingent=True),
        ContractRecord("TC", scenario_apv=100.0, category="payout_annuity",
                       cash_surrender_value=100.0),
    ]
    # Aggregate MAV 300, reserve 250: the 50 shortfall all falls on LC,
    # taking it to 150 — below its 190 surrender value, which floors it.
    got = allocate_aggregate_reserve(records, 250.0)
    assert got.rule == "13.D.4"
    assert got.amounts == {"LC": 190.0, "TC": 100.0}
    assert got.total == 290.0
    assert not got.reconciles                    # and the module says so
    assert got.below_cash_surrender_value == ()  # the floor did its job


def test_a_payout_contract_can_finish_below_the_apv_the_preamble_promises():
    """§13's preamble: "the reserve held for a Payout Annuity contract
    (whether life-contingent or not) will be no less than the present value
    of the liability cash flows provided under the contract … discounted
    using the NAER" — its Scenario APV.

    §13.D.4's arithmetic floors at the cash surrender value and nothing
    else, so a payout contract absorbing a shortfall lands under its APV.
    The preamble and the prescribed method disagree, and this module
    implements the method and reports the disagreement."""
    records = [
        ContractRecord("LC", scenario_apv=200.0, category="payout_annuity",
                       life_contingent=True),
        ContractRecord("TC", scenario_apv=100.0, category="payout_annuity"),
    ]
    got = allocate_aggregate_reserve(records, 240.0)
    assert got.amounts["LC"] == 140.0            # against a 200 APV
    assert got.below_scenario_apv == ("LC",)
    assert got.below_cash_surrender_value == ()


def test_a_longevity_mav_carries_no_surrender_floor_and_the_module_says_so():
    """§13.C.3 sets the MAV to 2% of the next twelve months' scheduled
    benefits full stop — no "greater of", unlike §13.C.1. A longevity
    contract with a surrender value above that finishes below the preamble's
    first guarantee, which is worth reporting because such contracts
    normally have no surrender value at all and the case is easy to miss."""
    record = ContractRecord("L", scenario_apv=5.0,
                            category="longevity_reinsurance",
                            longevity_benefits_12m=1_000.0,
                            cash_surrender_value=50.0)
    assert record.mav == 20.0 < 50.0
    got = allocate_aggregate_reserve([record], 20.0)
    assert got.amounts == {"L": 20.0}
    assert got.below_cash_surrender_value == ("L",)


def test_the_excluded_contracts_are_kept_out_of_the_allocation():
    """§13: contracts passing §7.A's stochastic exclusion test "will not be
    included in the allocation of the aggregate reserve"; §3.H has them
    "calculated on a seriatim basis" instead. Including one would allocate
    a reserve it is not part of to a contract that already has its own."""
    records = [account_value("A", apv=140.0, csv=100.0),
               account_value("X", apv=50.0, csv=40.0,
                             stochastically_excluded=True)]
    with pytest.raises(VM22Error, match="seriatim basis"):
        allocate_aggregate_reserve(records, 300.0)


def test_a_dr_contract_is_allocated_but_not_alongside_an_sr_one():
    """§13 keeps §7.E's DR contracts in — "contracts that have passed the
    Single Scenario Test … are subject to the allocation methodology
    described in this section" — while requiring that "allocation
    calculations shall be done separately for the DR and SR"."""
    dr = [account_value("D1", apv=140.0, csv=100.0, carries_dr=True),
          account_value("D2", apv=120.0, csv=100.0, carries_dr=True)]
    assert allocate_aggregate_reserve(dr, 260.0).total == 260.0

    mixed = [dr[0], account_value("S1", apv=120.0, csv=100.0)]
    with pytest.raises(VM22Error, match="separately for the DR and SR"):
        allocate_aggregate_reserve(mixed, 260.0)


def test_the_allocation_is_separated_by_category_and_by_model_segment():
    """§13: "separately … for different reserving categories that have not
    been aggregated pursuant to Section 3.F.2. To the extent that
    aggregation is done across multiple model segments, the allocation
    calculations shall be done separately for each model segment."

    Both refusals, and §3.F.2's attestation opening the one exception the
    text allows — the same rule §3.F.1 applies one level up."""
    payout = ContractRecord("P", scenario_apv=100.0,
                            category="payout_annuity")
    accum = account_value("A", apv=100.0, csv=50.0)
    longevity = ContractRecord("L", scenario_apv=1.0,
                               category="longevity_reinsurance",
                               longevity_benefits_12m=100.0)
    with pytest.raises(VM22Error, match="different reserving categories"):
        allocate_aggregate_reserve([payout, accum], 200.0)
    assert allocate_aggregate_reserve(
        [payout, accum], 200.0, combined_payout_accumulation=True).total \
        == 200.0
    with pytest.raises(VM22Error, match="different reserving categories"):
        allocate_aggregate_reserve([payout, longevity], 200.0,
                                   combined_payout_accumulation=True)

    with pytest.raises(VM22Error, match="each model segment"):
        allocate_aggregate_reserve(
            [account_value("A", apv=100.0, csv=50.0, model_segment="one"),
             account_value("B", apv=100.0, csv=50.0, model_segment="two")],
            300.0)


def test_the_allocation_runs_once_per_basis_and_not_once_on_a_pair():
    """§13 allocates "for both the pre- and post-reinsurance ceded
    reserves", and §13.C's inputs are themselves stated "after
    consideration of any reinsurance" — so the records differ by basis too.
    Running one set of contracts against both halves of a `BasisPair` would
    allocate the pre-ceded reserve over post-ceded contracts."""
    records = [account_value("A", apv=140.0, csv=100.0)]
    with pytest.raises(VM22Error, match="once per basis"):
        allocate_aggregate_reserve(records,
                                   BasisPair(pre_ceded=300.0,
                                             post_ceded=200.0))


def test_an_unclassified_contract_has_no_mav_rule_and_is_refused():
    """§13.C gives one rule per Reserving Category and none for anything
    else. Unclassified contracts aggregate freely for the *reserve* (§3.F.1
    documents that as not a VM-22 reserve); for the allocation there is no
    such latitude, because there would be no MAV to compute."""
    with pytest.raises(VM22Error, match="§13.C"):
        ContractRecord("X", scenario_apv=1.0, category=None)
    with pytest.raises(VM22Error, match="belongs to 'longevity_reinsurance'"):
        ContractRecord("Y", scenario_apv=1.0, category="accumulation",
                       longevity_benefits_12m=10.0)


def test_an_allocation_with_nothing_to_allocate_on_is_refused():
    """A group of zero-MAV contracts with no excess Scenario APV gives
    §13.D.3 no proportion to work with. Spreading the excess evenly is not
    what §13.D says, so the module stops rather than inventing a rule."""
    records = [ContractRecord("A", scenario_apv=0.0, category="accumulation"),
               ContractRecord("B", scenario_apv=0.0, category="accumulation")]
    with pytest.raises(VM22Error, match="no proportion to allocate on"):
        allocate_aggregate_reserve(records, 100.0)
    with pytest.raises(VM22Error, match="there are no contracts"):
        allocate_aggregate_reserve([], 100.0)
    with pytest.raises(VM22Error, match="contract ids repeat"):
        allocate_aggregate_reserve([account_value("A", apv=1.0, csv=1.0),
                                    account_value("A", apv=1.0, csv=1.0)],
                                   10.0)


def test_the_apv_scenario_is_the_closest_one_not_above_the_reserve():
    """§13.B.1: "the scenario that produces the aggregate scenario reserve
    for the group that is closest to, but not greater than the SR defined
    in Section 3.D."

    A prescribed selection, so the module makes it rather than taking the
    Scenario APV entirely on faith. Scenario 2 at 140 is the closest below
    the SR of 150; scenario 3 at 200 is closer in absolute terms and is not
    eligible, which is the half of the rule an argmin would get wrong."""
    reserves = np.array([50.0, 90.0, 140.0, 200.0])
    assert apv_scenario(reserves, 150.0) == 2
    assert apv_scenario(reserves, 140.0) == 2      # "not greater than"
    assert apv_scenario(reserves, 139.0) == 1
    # Ties take the lowest index, so the choice does not depend on a sort.
    assert apv_scenario(np.array([90.0, 90.0, 200.0]), 150.0) == 0


def test_the_apv_scenario_and_the_reserve_have_to_belong_to_each_other():
    """A CTE is never below the minimum it averages over, so an SR below
    every scenario reserve did not come from those reserves. Refused rather
    than clamped to the smallest, which would silently pick a scenario the
    text did not choose."""
    reserves = np.array([100.0, 200.0, 300.0])
    with pytest.raises(VM22Error, match="not the reserves that SR came from"):
        apv_scenario(reserves, 50.0)
    with pytest.raises(VM22Error, match="there are none"):
        apv_scenario([], 10.0)


def test_the_selected_scenario_is_the_one_the_prescribed_path_produces():
    """End to end against §3.F.5.a: the aggregate scenario reserves come
    from `segment_scenario_reserves` and the SR is their CTE, so §13.B.1's
    selection is made against the same numbers the reserve was made from
    rather than a second computation of them."""
    segment = ModelSegment("S", [[0.0, 40.0, 100.0, 200.0]])
    reserves = segment_scenario_reserves([segment])
    sr = segment_stochastic_reserve([segment], basis=HALF)
    assert sr == 150.0                              # CTE(50) of 100 and 200
    assert apv_scenario(reserves, sr) == 2          # the 100 scenario
