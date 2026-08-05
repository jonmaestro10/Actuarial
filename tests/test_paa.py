"""IFRS 17: the premium allocation approach.

The measurements are about *when* the simplification stops being one, which
is exactly the question §53(b) asks and does not answer.
"""

from __future__ import annotations

import numpy as np
import pytest

from engine.data.rates import YieldCurve
from engine.report.ifrs17 import (
    CoverageUnits, Group, RiskAdjustment, measure as measure_gmm,
)
from engine.report.paa import (
    AUTOMATIC_ELIGIBILITY_PERIODS, DEFAULT_MATERIALITY, Eligibility,
    eligibility, measure_paa, relative_difference,
)

FLAT4 = YieldCurve.flat(0.04, freq=1, horizon_years=60)
FLAT0 = YieldCurve.flat(0.0, freq=1, horizon_years=60)


def single_premium(n, premium=1000.0, claims=700.0, acquisition=0.0):
    """The PAA's actual shape: premium up front, coverage spread over the
    periods it buys. A group whose premiums arrive exactly as they are
    earned has no unearned premium for the model to be about."""
    inflows = np.zeros(n)
    inflows[0] = premium
    return Group(inflows, np.full(n, claims / n), acquisition=acquisition)


def flat_units(n):
    return CoverageUnits(np.full(n, 1.0))


def net_cash(group: Group) -> float:
    return float(group.inflows.sum() - group.outflows.sum() - group.acquisition)


def gmm(group, current=FLAT4, **kw):
    return measure_gmm(group, coverage=flat_units(group.n_periods),
                       current=current, **kw)


def gap(n, discount_lrc=False, rate=0.04):
    curve = YieldCurve.flat(rate, freq=1, horizon_years=60)
    group = single_premium(n)
    return relative_difference(
        measure_paa(group, current=curve, discount_lrc=discount_lrc).liability,
        gmm(group, current=curve).liability, group.inflows)


# --- the reconciliation --------------------------------------------------


@pytest.mark.parametrize("group,kw", [
    (single_premium(6), {}),
    (single_premium(6, claims=1400.0), {}),                    # onerous
    (single_premium(6, acquisition=150.0), {}),
    (single_premium(6, claims=1400.0, acquisition=150.0), {}),
    (single_premium(6, acquisition=150.0), dict(acquisition_periods=0)),
    (Group(np.full(6, 200.0), np.full(6, 120.0), acquisition=90.0), {}),
])
@pytest.mark.parametrize("discount_lrc", [False, True])
def test_total_profit_is_the_groups_net_cash(group, kw, discount_lrc):
    """The same discipline RFC-012 and RFC-015 are held to, and it earned
    its keep three times here."""
    m = measure_paa(group, current=FLAT4, discount_lrc=discount_lrc, **kw)
    assert m.total_profit() == pytest.approx(net_cash(group), abs=1e-8)


@pytest.mark.parametrize("discount_lrc", [False, True])
def test_the_unearned_premium_runs_to_exactly_zero(discount_lrc):
    """Two things drain the same balance and a division knows about only
    one of them.

    Dividing the premium by the number of periods left the liability
    closing at *minus the acquisition cost* undiscounted, and short by the
    accreted acquisition when discounted — an unearned premium that is
    never earned. The level revenue is solved against both drains.
    """
    for group in (single_premium(6),
                  single_premium(6, acquisition=150.0),
                  Group(np.full(6, 200.0), np.full(6, 120.0),
                        acquisition=90.0)):
        m = measure_paa(group, current=FLAT4, discount_lrc=discount_lrc)
        assert m.unearned_premium[-1] == pytest.approx(0.0, abs=1e-8)
        assert m.unearned_premium[0] == 0.0


def test_the_acquisition_recovery_nets_out_of_the_service_result():
    """§B126 reports it gross — in revenue and in expenses at the same
    amount — so it cannot flatter the result it passes through."""
    group = single_premium(6, acquisition=150.0)
    m = measure_paa(group, current=FLAT4)
    assert (m.acquisition_amortised > 0.0).all()
    assert m.insurance_revenue == pytest.approx(
        m.premium_revenue + m.acquisition_amortised)
    assert m.insurance_service_result == pytest.approx(
        m.premium_revenue - group.outflows - m.loss_recognised)


def test_expensing_acquisition_costs_immediately_charges_them_at_once():
    """§59(a), for coverage of a year or less."""
    group = single_premium(6, acquisition=150.0)
    m = measure_paa(group, current=FLAT4, acquisition_periods=0)
    assert m.acquisition_amortised[0] == 150.0
    assert (m.acquisition_amortised[1:] == 0.0).all()
    assert m.total_profit() == pytest.approx(net_cash(group), abs=1e-8)


def test_amortising_over_more_periods_than_the_group_has_is_refused():
    with pytest.raises(ValueError, match="does not fit"):
        measure_paa(single_premium(5, acquisition=100.0), current=FLAT4,
                    acquisition_periods=9)


# --- the finding: where the simplification stops simplifying -------------


def test_the_two_models_agree_exactly_over_a_single_period():
    """Which is why §53(a) needs no test at all: over one period there is
    nothing for a CSM to defer and nothing to discount."""
    assert gap(1) == 0.0


def test_the_divergence_grows_monotonically_with_the_coverage_period():
    """Measured, and it is what makes §53(b) answerable at all."""
    gaps = [gap(n) for n in (1, 2, 3, 5, 8, 12, 20, 30)]
    assert gaps == sorted(gaps)
    assert gaps[0] == 0.0
    assert gap(5) == pytest.approx(0.034, abs=0.005)
    assert gap(20) == pytest.approx(0.173, abs=0.010)


def test_the_divergence_is_the_time_value_of_money_and_nothing_else():
    """The cleanest statement of what the PAA gives up. At a zero discount
    rate the simplification is not a simplification — it is the general
    model, exactly, at **every** coverage period.

    Which is precisely why §53(a) exempts one-year contracts (no material
    financing component) and §56 requires accretion where there is one.
    """
    for n in (1, 5, 20, 30):
        assert gap(n, rate=0.0) == pytest.approx(0.0, abs=1e-12)
    # And it grows with the rate, at a fixed term.
    rates = [gap(20, rate=r) for r in (0.0, 0.01, 0.02, 0.04, 0.08)]
    assert rates == sorted(rates)
    assert rates[-1] > 5 * rates[1]


def test_accreting_the_liability_roughly_doubles_the_eligible_term():
    """§56's financing adjustment is not cosmetic. Undiscounted, this group
    passes a 5% materiality test out to **seven** periods; with the
    liability accreted it passes out to **fifteen**."""
    undiscounted = next(n for n in range(1, 80) if gap(n) > DEFAULT_MATERIALITY)
    accreted = next(n for n in range(1, 80)
                    if gap(n, discount_lrc=True) > DEFAULT_MATERIALITY)
    assert undiscounted == 8
    assert accreted == 16
    for n in (2, 5, 12, 20, 30):
        assert gap(n, discount_lrc=True) < gap(n)


# --- eligibility ---------------------------------------------------------


def test_a_group_of_a_year_or_less_qualifies_without_any_measurement():
    result = eligibility(single_premium(1), coverage=flat_units(1),
                         current=FLAT4)
    assert result
    assert result.ground == "coverage_period"
    assert result.relative_difference is None
    assert "one year or less" in result.explain()


def test_a_longer_group_qualifies_only_by_running_the_model_it_avoids():
    """The irony, made explicit: proving you may use the simplification
    costs a full run of the thing it simplifies. It is free only for the
    contracts that never needed it."""
    result = eligibility(single_premium(3), coverage=flat_units(3),
                         current=FLAT4)
    assert result
    assert result.ground == "not_materially_different"
    assert result.relative_difference == pytest.approx(0.018, abs=0.005)
    assert "§53(b)" in result.explain()


def test_a_group_too_long_to_simplify_is_told_so_with_the_number():
    result = eligibility(single_premium(10), coverage=flat_units(10),
                         current=FLAT4)
    assert not result
    assert result.ground is None
    assert result.relative_difference > DEFAULT_MATERIALITY
    assert "beyond" in result.explain()


def test_the_materiality_threshold_is_the_entitys_and_is_stated():
    """The standard gives no number at all, so a default has to be
    overridable and has to appear wherever it is used."""
    lenient = eligibility(single_premium(10), coverage=flat_units(10),
                          current=FLAT4, materiality=0.10)
    assert lenient
    assert "10%" in lenient.explain()


def test_a_sub_annual_frequency_counts_years_not_periods():
    twelve = eligibility(single_premium(12), coverage=flat_units(12),
                         current=FLAT4, freq=12)
    assert twelve.years == 1.0
    assert twelve.ground == "coverage_period"


def test_materiality_cannot_be_claimed_without_a_comparison():
    stated = Eligibility(periods=10, freq=1)
    assert not stated
    assert not stated.by_materiality
    assert "cannot be relied on" in stated.explain()


def test_the_comparison_is_scaled_by_premium_and_not_by_the_liability():
    """The first version divided by the general model's own liability, and
    a level-premium group shows why that is unusable.

    Premiums arriving exactly as they are earned leave the PAA with no
    unearned premium at all, and leave the general model holding a
    liability that peaks at **0.8% of the premium** — nil at issue by
    construction, near-nil throughout, nil again at run-off. The two models
    agree to within 0.8% of the group's size and the old scale called that
    a **100%** difference. On the single-premium shape it reported 120% to
    200%, and infinity over one period.
    """
    level = Group(np.full(5, 1000.0), np.full(5, 700.0))
    simplified = measure_paa(level, current=FLAT4).liability
    general = gmm(level).liability
    assert np.abs(general).max() / level.inflows.sum() < 0.01
    # On the honest scale the two agree; on the old one they differ by all
    # of it, because the denominator is the thing that is nearly zero.
    assert relative_difference(simplified, general, level.inflows) < 0.01
    old_scale = np.abs(simplified - general).max() / np.abs(general).max()
    assert old_scale == pytest.approx(1.0)


def test_a_group_with_no_premium_has_nothing_to_judge_materiality_against():
    with pytest.raises(ValueError, match="no scale"):
        relative_difference(np.ones(3), np.ones(3), np.zeros(3))


# --- the onerous test does not go away -----------------------------------


def test_a_group_is_onerous_when_fulfilment_exceeds_the_unearned_premium():
    """The sign is the whole test, and the first version had it backwards.

    A profitable group's fulfilment cashflows are *negative*; adding a
    positive unearned premium to them rather than subtracting it flipped
    the comparison and manufactured a loss component on a group with a 30%
    margin.
    """
    healthy = measure_paa(single_premium(6), current=FLAT4)
    assert not healthy.onerous
    assert (healthy.loss_component == 0.0).all()

    sick = measure_paa(single_premium(6, claims=1400.0), current=FLAT4)
    assert sick.onerous
    assert sick.loss_component[0] > 0.0
    assert sick.loss_component[-1] == pytest.approx(0.0, abs=1e-9)


def test_the_day_one_loss_reaches_income_in_the_period_it_arises():
    """``diff`` alone telescopes the opening balance out of the income
    statement entirely — the same fault RFC-015's day-one loss had, in the
    same shape, caught by the same reconciliation."""
    group = single_premium(6, claims=1400.0)
    m = measure_paa(group, current=FLAT4)
    assert m.loss_recognised.sum() == pytest.approx(0.0, abs=1e-9)
    assert m.insurance_service_result[0] < m.insurance_service_result[1:].min()
    assert m.total_profit() == pytest.approx(net_cash(group), abs=1e-8)


def test_switching_the_onerous_test_off_isolates_the_simplification():
    """Available for a test, not for a report."""
    group = single_premium(6, claims=1400.0)
    m = measure_paa(group, current=FLAT4, onerous_test=False)
    assert (m.loss_component == 0.0).all()
    assert not m.onerous


def test_a_risk_adjustment_makes_a_marginal_group_onerous():
    """§57 measures the shortfall on the fulfilment cashflows *and* the
    risk adjustment, so a group that just clears without one need not with
    it."""
    group = single_premium(6, claims=980.0)
    bare = measure_paa(group, current=FLAT4)
    loaded = measure_paa(
        group, current=FLAT4,
        risk_adjustment=RiskAdjustment.percent_of(group.outflows, 0.20))
    assert not bare.onerous
    assert loaded.onerous


# --- what is absent ------------------------------------------------------


def test_there_is_no_contractual_service_margin_anywhere():
    """The absence is the model. Under the general approach the CSM is
    where unearned profit lives and where every change in estimate lands;
    here profit emerges purely as premium is earned."""
    m = measure_paa(single_premium(6), current=FLAT4)
    assert not hasattr(m, "csm")
    assert not hasattr(m, "csm_release")


def test_revenue_is_level_on_the_passage_of_time():
    """No coverage units, no release pattern to choose — which is the
    simplification, and the reason RFC-012's coverage-unit finding has no
    counterpart here."""
    m = measure_paa(single_premium(8), current=FLAT4)
    assert m.premium_revenue == pytest.approx(np.full(8, 125.0))


def test_the_repr_flags_an_onerous_group():
    assert "onerous" in repr(measure_paa(single_premium(6, claims=1400.0),
                                         current=FLAT4))
    assert "onerous" not in repr(measure_paa(single_premium(6),
                                             current=FLAT4))


def test_the_automatic_threshold_is_one_year():
    assert AUTOMATIC_ELIGIBILITY_PERIODS == 1
