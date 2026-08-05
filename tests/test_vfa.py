"""IFRS 17: the variable fee approach.

The comparisons against RFC-012's general measurement model are the point.
Both models are given the same group and the same events, and the difference
is measured rather than described.
"""

from __future__ import annotations

import numpy as np
import pytest

from engine.data.rates import YieldCurve
from engine.report.ifrs17 import (
    CoverageUnits, Group, RiskAdjustment, measure,
)
from engine.report.vfa import (
    ELIGIBILITY_CONDITIONS, Eligibility, UnderlyingItems, measure_vfa,
)

FLAT4 = YieldCurve.flat(0.04, freq=1, horizon_years=60)
N = 15


def group():
    return Group(np.full(N, 1000.0), np.full(N, 700.0), acquisition=400.0)


def risk():
    return RiskAdjustment.percent_of(np.full(N, 700.0), 0.05)


def units():
    return CoverageUnits(np.full(N, 1.0))


def net_cash(g: Group) -> float:
    return float(g.inflows.sum() - g.outflows.sum() - g.acquisition)


def pool(growth=1.08, crash_at=None, crash=0.35, recover_at=None,
         start=500_000.0, share=0.012):
    values = start * growth ** np.arange(N + 1)
    if crash_at is not None:
        calm = values.copy()
        values[crash_at + 1:] *= crash
        if recover_at is not None:
            values[recover_at + 1:] = calm[recover_at + 1:]
    return UnderlyingItems(values, entity_share=share)


def gmm(**kw):
    return measure(group(), coverage=units(), risk_adjustment=risk(),
                   current=FLAT4, **kw)


def vfa(**kw):
    kw.setdefault("underlying", pool())
    return measure_vfa(group(), coverage=units(), risk_adjustment=risk(),
                       current=FLAT4, **kw)


# --- eligibility ---------------------------------------------------------


def test_all_three_conditions_are_needed():
    assert Eligibility.direct_participating()
    for missing in ELIGIBILITY_CONDITIONS:
        answers = {name: True for name in ELIGIBILITY_CONDITIONS}
        answers[missing] = False
        assert not Eligibility(**answers)
        assert Eligibility(**answers).failures() == (missing,)


def test_an_ineligible_group_is_refused_rather_than_measured():
    ineligible = Eligibility(clearly_identified_pool=False,
                             substantial_share_of_returns=True,
                             substantial_proportion_varies=True)
    with pytest.raises(ValueError, match="does not meet"):
        vfa(eligibility=ineligible)


def test_eligibility_is_optional():
    """It is the entity's judgement from the contract terms, not something
    a cashflow table can be asked. A caller who has made it elsewhere is
    not forced to restate it here."""
    assert vfa().csm[0] > 0.0


# --- the underlying items ------------------------------------------------


def test_the_fee_change_is_derived_from_the_pool_not_supplied_beside_it():
    items = UnderlyingItems([100.0, 110.0, 121.0], entity_share=0.1)
    assert items.share() == pytest.approx([10.0, 11.0, 12.1])
    assert items.fee_change() == pytest.approx([1.0, 1.1])


def test_an_entity_cannot_own_the_whole_pool():
    with pytest.raises(ValueError, match="does not own the pool"):
        UnderlyingItems([100.0, 110.0], entity_share=1.0)


def test_a_negative_pool_is_refused():
    with pytest.raises(ValueError, match="cannot be negative"):
        UnderlyingItems([100.0, -1.0], entity_share=0.01)


def test_a_pool_shorter_than_the_coverage_is_refused():
    short = UnderlyingItems(500_000.0 * np.ones(N - 2), entity_share=0.012)
    with pytest.raises(ValueError, match="reach the end of the coverage"):
        vfa(underlying=short)


def test_the_pool_can_come_straight_off_a_run():
    class Fake:
        def aggregate(self, name):
            return list(np.linspace(100.0, 200.0, N + 3))

    items = UnderlyingItems.from_run(Fake(), "av_eop", 0.01, periods=N)
    assert items.n_periods == N


# --- the invariant survives ----------------------------------------------


@pytest.mark.parametrize("underlying", [
    pool(),
    pool(growth=0.95),
    pool(crash_at=5),
    pool(crash_at=5, recover_at=8),
])
def test_total_profit_is_still_the_groups_net_cash(underlying):
    """Whatever the pool does. The variable fee is a re-measurement, not
    new money — the fee itself is already in the group's cashflows — so it
    moves profit between periods and cannot change the total."""
    m = vfa(underlying=underlying)
    assert m.total_profit() == pytest.approx(net_cash(group()), abs=1e-8)


def test_the_two_models_agree_on_the_opening_csm():
    """Nothing about initial recognition differs. The models part company
    on what happens afterwards."""
    assert vfa().csm[0] == pytest.approx(gmm().csm[0], rel=1e-12)


def test_there_is_no_locked_in_accretion():
    """The CSM's growth *is* the entity's share of the pool. Offering a
    locked-in curve that did nothing would be worse than not offering
    one, so the argument does not exist."""
    with pytest.raises(TypeError):
        measure_vfa(group(), coverage=units(), underlying=pool(),
                    current=FLAT4, locked_in=FLAT4)


def test_a_rising_pool_grows_the_margin_and_a_falling_one_shrinks_it():
    rising = vfa(underlying=pool(growth=1.08))
    falling = vfa(underlying=pool(growth=0.95))
    assert rising.csm[5] > rising.csm[0]
    assert falling.csm[5] < falling.csm[0]
    assert (rising.variable_fee > 0.0).all()
    assert (falling.variable_fee < 0.0).all()


# --- the finding: a market move stops being a market move ----------------


def _shock(size=3000.0, at=5):
    series = np.zeros(N)
    series[at] = size
    return series


def test_a_financial_change_hits_profit_at_once_under_the_gmm_and_is_deferred_under_the_vfa():
    """The difference the approach exists to make, on one line of code.

    A 3,000 worsening from a financial variable — a guarantee getting more
    expensive as markets fall. Under the GMM it goes to insurance finance
    expense in the period it happens, in full. Under the VFA it adjusts
    the unearned margin, so what reaches the year is the change times that
    period's coverage-unit fraction and nothing more: with ten years of
    cover left, **a tenth**.
    """
    shock = _shock()
    gmm_impact = (gmm(financial_changes=shock).profit[5] - gmm().profit[5])
    vfa_impact = (vfa(financial_changes=shock).profit[5] - vfa().profit[5])
    fraction = units().release_fractions(N, FLAT4)[5]

    assert gmm_impact == pytest.approx(-3000.0, rel=1e-9)
    assert fraction == pytest.approx(0.1)
    assert vfa_impact == pytest.approx(-3000.0 * fraction, rel=1e-9)
    assert abs(vfa_impact) == pytest.approx(abs(gmm_impact) / 10.0, rel=1e-9)

    # And the money is not lost, only moved: both totals fall by the change.
    baseline = net_cash(group())
    assert gmm(financial_changes=shock).total_profit() == pytest.approx(
        baseline - 3000.0, abs=1e-8)
    assert vfa(financial_changes=shock).total_profit() == pytest.approx(
        baseline - 3000.0, abs=1e-8)


def test_the_risk_mitigation_election_puts_it_back_where_the_gmm_had_it():
    """§B115 is one flag and not a third model: electing it sends hedged
    financial changes down the same route the GMM sends them."""
    shock = _shock()
    elected = measure_vfa(group(), coverage=units(), underlying=pool(),
                          risk_adjustment=risk(), current=FLAT4,
                          financial_changes=shock, risk_mitigation=True)
    impact = elected.profit[5] - vfa().profit[5]
    gmm_impact = gmm(financial_changes=shock).profit[5] - gmm().profit[5]
    assert impact == pytest.approx(gmm_impact, rel=1e-9)
    assert (elected.csm == vfa().csm).all()          # the CSM never heard
    assert elected.risk_mitigation


def test_a_non_financial_change_adjusts_the_csm_under_both_models():
    """The approaches differ on financial variables only. A mortality
    deterioration is the CSM's business either way."""
    change = _shock()
    assert (gmm(changes_in_estimate=change).csm
            != gmm().csm).any()
    assert (vfa(changes_in_estimate=change).csm != vfa().csm).any()


# --- the finding: the VFA's CSM is not safe ------------------------------


def test_a_crash_can_exhaust_the_csm_and_make_a_profitable_group_onerous():
    """What the GMM cannot do, because its CSM never hears about markets.

    A 65% fall wipes out a margin of 4,122 and puts the excess through
    profit and loss immediately — the RFC-012 asymmetry, triggered by a
    market rather than by an estimate.
    """
    calm = vfa()
    crashed = vfa(underlying=pool(crash_at=5))

    assert calm.csm[5] > 4000.0
    assert crashed.csm[5] == pytest.approx(calm.csm[5], rel=1e-12)
    assert crashed.csm[6] == 0.0
    assert crashed.loss_recognised[5] > 1000.0
    assert crashed.loss_component[6] > 0.0
    # The general model, given the identical group, does not notice at all.
    assert gmm().loss_recognised.sum() == 0.0
    assert (gmm().csm > 0.0)[:-1].all()


def test_a_recovery_clears_the_loss_component_before_it_rebuilds_the_margin():
    """The asymmetry has to hold for a market recovery exactly as it does
    for a favourable estimate. Letting a rising pool rebuild the CSM past
    a loss component still sitting there was a real bug, and the two paths
    are one rule because of it.
    """
    crashed = vfa(underlying=pool(crash_at=5))
    recovered = vfa(underlying=pool(crash_at=5, recover_at=8))

    carried = crashed.loss_component[8]
    assert carried > 0.0
    assert recovered.loss_reversed[8] == pytest.approx(carried, rel=1e-9)
    assert recovered.loss_component[9] == 0.0
    assert recovered.csm[9] > 0.0
    # Nothing was created by the round trip.
    assert recovered.total_profit() == pytest.approx(net_cash(group()), abs=1e-8)


def test_the_service_result_moves_with_the_pool_under_the_vfa_and_not_under_the_gmm():
    calm = vfa()
    crashed = vfa(underlying=pool(crash_at=5))
    assert crashed.insurance_service_result[5] < 0.0
    assert calm.insurance_service_result[5] > 0.0
    # The GMM's service result is the same whatever the pool did, because
    # the pool is not one of its inputs.
    assert gmm().insurance_service_result[5] > 0.0


# --- inputs --------------------------------------------------------------


def test_a_financial_series_of_the_wrong_length_is_refused():
    with pytest.raises(ValueError, match="financial_changes covers"):
        vfa(financial_changes=np.zeros(3))


def test_a_change_series_of_the_wrong_length_is_refused():
    with pytest.raises(ValueError, match="changes_in_estimate covers"):
        vfa(changes_in_estimate=np.zeros(3))


def test_a_csm_growth_series_of_the_wrong_length_is_refused():
    with pytest.raises(ValueError, match="csm_growth covers"):
        measure(group(), coverage=units(), current=FLAT4,
                csm_growth=np.zeros(3))


def test_the_pool_needs_an_opening_and_a_closing_value():
    with pytest.raises(ValueError, match="opening and closing"):
        UnderlyingItems([100.0], entity_share=0.01)
