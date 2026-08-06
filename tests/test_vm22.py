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
    METHODS,
    RESERVING_CATEGORIES,
    SERT_CAP,
    VM22_2026,
    AggregateReserve,
    AggregationGap,
    Contract,
    Exclusion,
    ReservingGroup,
    VM22Basis,
    VM22Error,
    aggregate_stochastic_reserve,
    aggregation_decomposition,
    floor_outside_reserve,
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
    assert reserve.value == 225.0                 # not max(...) == 140
    assert reserve.by_method() == {"stochastic": 140.0,
                                   "deterministic": 60.0, "formulaic": 25.0}
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
    assert group.amount == 175.0
    assert AggregateReserve([group], basis=HALF).value == 175.0


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
    assert reserve.value > 0.0
    assert reserve.by_method()["stochastic"] == reserve.value

    scenario = sum(c.scenario_reserve for c in contracts)
    floors = sum(c.cash_surrender_value for c in contracts)
    assert reserve.value == cte(np.maximum(scenario, floors), 0.70)


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
    assert reserve.value >= 50_000.0            # the floor is a floor
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
