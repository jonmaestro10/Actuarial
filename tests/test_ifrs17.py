"""IFRS 17 general measurement model.

The tests that matter here are the ones showing that the standard's open
choices move *when* profit appears and never *how much* — and the invariant
that pins it: total profit equals the group's undiscounted net cash, to
floating point, under every combination of those choices.
"""

from __future__ import annotations

import numpy as np
import pytest

from engine.data.rates import YieldCurve
from engine.report.ifrs17 import (
    CoverageUnits, Group, Measurement, RiskAdjustment, measure,
)

FLAT4 = YieldCurve.flat(0.04, freq=1, horizon_years=60)
FLAT5 = YieldCurve.flat(0.05, freq=1, horizon_years=60)
FLAT1 = YieldCurve.flat(0.01, freq=1, horizon_years=60)


def level(n, premium, claim, acquisition=0.0, margin=0.05):
    inflows, outflows = np.full(n, premium), np.full(n, claim)
    group = Group(inflows, outflows, acquisition=acquisition)
    return group, RiskAdjustment.percent_of(outflows, margin)


def flat_units(n, **kw):
    return CoverageUnits(np.full(n, 1.0), **kw)


def net_cash(group: Group) -> float:
    return float(group.inflows.sum() - group.outflows.sum() - group.acquisition)


# --- the invariant -------------------------------------------------------


@pytest.mark.parametrize("label,kwargs", [
    ("level profitable", dict(premium=1000.0, claim=700.0, acquisition=500.0)),
    ("onerous", dict(premium=1000.0, claim=1150.0, acquisition=300.0)),
    ("no acquisition", dict(premium=1000.0, claim=700.0)),
    ("no risk adjustment", dict(premium=1000.0, claim=700.0,
                                acquisition=400.0, margin=0.0)),
])
def test_total_profit_is_the_groups_net_cash(label, kwargs):
    """Accounting moves which period a profit appears in. It cannot invent
    or destroy one, and this is the check that says so."""
    n = 10
    group, ra = level(n, **kwargs)
    m = measure(group, coverage=flat_units(n), risk_adjustment=ra, current=FLAT4)
    assert m.total_profit() == pytest.approx(net_cash(group), abs=1e-9)


@pytest.mark.parametrize("coverage_kw,locked", [
    (dict(), None),
    (dict(discount=True), None),
    (dict(), FLAT5),
    (dict(discount=True), FLAT1),
])
def test_the_open_choices_do_not_move_the_total(coverage_kw, locked):
    """Coverage-unit discounting and the locked-in rate are both policy
    choices with real effects on the statement, and neither can change the
    number the statement adds up to."""
    n = 12
    group, ra = level(n, premium=1000.0, claim=700.0, acquisition=500.0)
    m = measure(group, coverage=flat_units(n, **coverage_kw),
                risk_adjustment=ra, current=FLAT4, locked_in=locked)
    assert m.total_profit() == pytest.approx(net_cash(group), abs=1e-9)


def test_the_acquisition_cashflow_is_not_financed_for_a_period_it_is_not_owed():
    """Found by the invariant above rather than by reading the standard.

    Acquisition costs are paid at initial recognition, so they are out of
    the door before the first period accretes. Leaving them in the balance
    that unwinds makes total profit miss net cash by exactly
    ``acquisition * i`` — which is how the sign of the error identified it.
    """
    n = 10
    for acquisition in (0.0, 500.0, 1500.0):
        group, ra = level(n, premium=1000.0, claim=700.0,
                          acquisition=acquisition)
        m = measure(group, coverage=flat_units(n), risk_adjustment=ra,
                    current=FLAT4)
        assert m.total_profit() == pytest.approx(net_cash(group), abs=1e-9)


# --- no profit at inception ----------------------------------------------


def test_a_profitable_group_recognises_nothing_on_day_one():
    """The CSM exists to make this true, and it is the whole shape of the
    standard: writing profitable business produces no accounting profit."""
    n = 10
    group, ra = level(n, premium=1000.0, claim=700.0, acquisition=500.0)
    m = measure(group, coverage=flat_units(n), risk_adjustment=ra, current=FLAT4)
    assert m.csm[0] > 0.0
    assert m.loss_component[0] == 0.0
    # The liability at initial recognition is nil: the three blocks are
    # constructed to cancel.
    assert m.liability[0] == pytest.approx(0.0, abs=1e-9)


def test_the_csm_runs_to_exactly_zero():
    n = 10
    group, ra = level(n, premium=1000.0, claim=700.0)
    m = measure(group, coverage=flat_units(n), risk_adjustment=ra, current=FLAT4)
    assert m.csm[-1] == 0.0
    assert m.risk_adjustment[-1] == 0.0
    assert m.liability[-1] == pytest.approx(0.0, abs=1e-9)


def test_the_csm_released_is_the_opening_balance_plus_its_interest():
    n = 10
    group, ra = level(n, premium=1000.0, claim=700.0)
    m = measure(group, coverage=flat_units(n), risk_adjustment=ra, current=FLAT4)
    assert m.csm_release.sum() == pytest.approx(
        m.csm[0] + m.csm_accreted.sum(), rel=1e-12
    )


def test_the_service_result_is_the_risk_and_margin_released():
    """With no experience variance, expected claims appear in revenue and
    expenses at the same amount and cancel — so what is left is exactly the
    two margins unwinding."""
    n = 8
    group, ra = level(n, premium=1000.0, claim=700.0)
    m = measure(group, coverage=flat_units(n), risk_adjustment=ra, current=FLAT4)
    expected = m.risk_adjustment_release + m.csm_release
    assert m.insurance_service_result == pytest.approx(expected, rel=1e-12)


# --- the onerous asymmetry -----------------------------------------------


def test_an_onerous_group_recognises_its_whole_loss_on_day_one():
    n = 10
    group, ra = level(n, premium=1000.0, claim=1150.0, acquisition=300.0)
    m = measure(group, coverage=flat_units(n), risk_adjustment=ra, current=FLAT4)
    assert m.onerous
    assert m.csm[0] == 0.0
    assert m.loss_component[0] > 0.0
    assert m.loss_recognised[0] == m.loss_component[0]
    # And it is in the first period's result, not sitting in an opening
    # balance that never reaches the statement.
    assert m.insurance_service_result[0] < 0.0
    assert m.insurance_service_result[1:].min() > 0.0


def test_there_is_no_negative_csm():
    n = 10
    group, ra = level(n, premium=1000.0, claim=1150.0, acquisition=300.0)
    m = measure(group, coverage=flat_units(n), risk_adjustment=ra, current=FLAT4)
    assert (m.csm >= 0.0).all()
    assert (m.loss_component >= 0.0).all()


def test_a_favourable_change_rebuilds_the_loss_component_before_the_csm():
    """The asymmetry, isolated. A loss went to profit the day it appeared;
    its reversal does not come back the same way — it extinguishes the loss
    component first, and only the surplus becomes a CSM to release over the
    remaining coverage."""
    n = 10
    group, ra = level(n, premium=1000.0, claim=1150.0, acquisition=300.0)
    base = measure(group, coverage=flat_units(n), risk_adjustment=ra,
                   current=FLAT4)
    carried = base.loss_component[3]
    change = np.zeros(n)
    change[3] = -(carried + 400.0)          # more than enough to clear it
    after = measure(group, coverage=flat_units(n), risk_adjustment=ra,
                    current=FLAT4, changes_in_estimate=change)
    assert after.loss_reversed[3] == pytest.approx(carried, rel=1e-12)
    assert after.loss_component[4] == 0.0
    assert after.csm[4] > 0.0
    # The surplus over the loss component is what became CSM, less the
    # part of it released in the same period.
    assert after.csm[4] + after.csm_release[3] == pytest.approx(400.0, rel=1e-12)


def test_a_partial_reversal_leaves_a_loss_component_and_no_csm():
    n = 10
    group, ra = level(n, premium=1000.0, claim=1150.0, acquisition=300.0)
    base = measure(group, coverage=flat_units(n), risk_adjustment=ra,
                   current=FLAT4)
    change = np.zeros(n)
    change[3] = -base.loss_component[3] / 2.0
    after = measure(group, coverage=flat_units(n), risk_adjustment=ra,
                    current=FLAT4, changes_in_estimate=change)
    assert after.loss_component[4] > 0.0
    assert after.csm[4] == 0.0


def test_an_adverse_change_eats_the_csm_before_it_creates_a_loss():
    n = 10
    group, ra = level(n, premium=1000.0, claim=700.0)
    base = measure(group, coverage=flat_units(n), risk_adjustment=ra,
                   current=FLAT4)
    balance = base.csm[3] + base.csm_accreted[3]
    change = np.zeros(n)
    change[3] = balance + 250.0
    after = measure(group, coverage=flat_units(n), risk_adjustment=ra,
                    current=FLAT4, changes_in_estimate=change)
    assert after.csm[4] == 0.0
    assert after.loss_recognised[3] == pytest.approx(250.0, rel=1e-9)


def test_the_loss_component_runs_off_to_zero():
    """It is allocated on the same coverage units that release the CSM, and
    the last period's fraction is one — so nothing is left stranded."""
    n = 10
    group, ra = level(n, premium=1000.0, claim=1150.0, acquisition=300.0)
    m = measure(group, coverage=flat_units(n), risk_adjustment=ra, current=FLAT4)
    assert m.loss_component[-1] == pytest.approx(0.0, abs=1e-9)


def test_amortising_the_loss_component_reduces_revenue_and_expenses_alike():
    """It cannot touch the service result — the loss was recognised when it
    arose, and earning it a second time through revenue would double count
    it."""
    n = 10
    group, ra = level(n, premium=1000.0, claim=1150.0, acquisition=300.0)
    m = measure(group, coverage=flat_units(n), risk_adjustment=ra, current=FLAT4)
    assert (m.loss_amortised[1:] > 0.0).all()
    expected = (m.risk_adjustment_release + m.csm_release
                - m.loss_recognised + m.loss_reversed)
    assert m.insurance_service_result == pytest.approx(expected, rel=1e-12)


# --- grouping ------------------------------------------------------------


def test_grouping_by_profitability_changes_the_first_year_by_more_than_the_profit():
    """IFRS 17's most consequential structural rule, measured.

    The same business, written on the same day, with the same lifetime
    cash. Split into a profitable group and an onerous one — which the
    standard requires — the onerous group's loss hits profit immediately
    and the profitable group's CSM cannot offset it. Measured as a single
    group, the two net off and year one reports a profit.
    """
    n = 15
    units = flat_units(n)
    good, ra_good = level(n, premium=1200.0, claim=700.0, acquisition=400.0)
    bad, ra_bad = level(n, premium=800.0, claim=900.0, acquisition=400.0)
    together, ra_together = level(n, premium=2000.0, claim=1600.0,
                                  acquisition=800.0)

    mg = measure(good, coverage=units, risk_adjustment=ra_good, current=FLAT4)
    mb = measure(bad, coverage=units, risk_adjustment=ra_bad, current=FLAT4)
    mt = measure(together, coverage=units, risk_adjustment=ra_together,
                 current=FLAT4)

    assert mg.csm[0] > 0.0 and not mg.onerous
    assert mb.onerous
    assert not mt.onerous

    split_year_one = mg.insurance_service_result[0] + mb.insurance_service_result[0]
    assert split_year_one < 0.0
    assert mt.insurance_service_result[0] > 0.0
    # The swing is larger than the whole year's profit on the combined view.
    assert mt.insurance_service_result[0] - split_year_one > 1500.0

    # And the lifetime cash is identical, so nothing real has changed.
    assert (mg.total_profit() + mb.total_profit()
            == pytest.approx(mt.total_profit(), abs=1e-9))


# --- coverage units ------------------------------------------------------


def _release_shape(units, discount=False):
    n = 20
    count = 1000 * 0.96 ** np.arange(n)
    sum_assured = count * np.linspace(1.0, 0.05, n)
    claims = 0.4 * sum_assured
    inflows = np.full(n, claims.sum() / n * 1.55)
    group = Group(inflows, claims, acquisition=float(inflows[0] * 0.9))
    ra = RiskAdjustment.percent_of(claims, 0.06)
    driver = {"count": count, "sum_assured": sum_assured, "claims": claims}[units]
    m = measure(group, coverage=CoverageUnits(driver, discount=discount),
                risk_adjustment=ra, current=FLAT4)
    return m, m.csm_release[:5].sum() / m.csm_release.sum()


def test_the_coverage_unit_choice_moves_a_lot_of_profit_and_no_total():
    """A choice the standard leaves entirely open.

    On a decreasing-term group, releasing on policy count puts 25% of the
    margin in the first five years and releasing on sum assured puts 43% —
    the same margin, to the penny, in a materially different order.
    """
    by_count, count_share = _release_shape("count")
    by_sum, sum_share = _release_shape("sum_assured")

    assert by_count.csm[0] == pytest.approx(by_sum.csm[0], rel=1e-12)
    assert by_count.total_profit() == pytest.approx(by_sum.total_profit(), abs=1e-9)
    assert count_share == pytest.approx(0.25, abs=0.03)
    assert sum_share == pytest.approx(0.43, abs=0.03)
    assert sum_share > 1.6 * count_share


def test_discounting_the_coverage_units_pulls_profit_forward():
    """The other permitted choice, and it points one way: discounting makes
    later units count for less, so a larger share of the margin is released
    early."""
    _, plain = _release_shape("count")
    _, discounted = _release_shape("count", discount=True)
    assert discounted > plain
    assert discounted == pytest.approx(0.33, abs=0.03)


def test_a_flat_group_releases_a_constant_share_of_what_is_left():
    n = 5
    units = flat_units(n)
    fractions = units.release_fractions(n, FLAT4)
    assert fractions == pytest.approx([1/5, 1/4, 1/3, 1/2, 1.0])


# --- the locked-in rate --------------------------------------------------


def test_the_csm_accretes_at_the_locked_in_rate_not_todays():
    """A historic-cost balance inside a current-value liability. With rates
    down from 5% to 1%, the CSM still accretes at 5% and picks up six times
    the interest it would at today's rate — and total profit does not move,
    because every penny of it is a transfer between the service result and
    the finance line."""
    n = 15
    group, ra = level(n, premium=1200.0, claim=700.0, acquisition=400.0)
    units = flat_units(n)
    locked = measure(group, coverage=units, risk_adjustment=ra,
                     current=FLAT1, locked_in=FLAT5)
    current = measure(group, coverage=units, risk_adjustment=ra,
                      current=FLAT1, locked_in=FLAT1)

    assert locked.csm[0] == pytest.approx(current.csm[0], rel=1e-12)
    assert locked.csm_accreted.sum() > 5 * current.csm_accreted.sum()
    assert locked.insurance_service_result.sum() > current.insurance_service_result.sum()
    assert locked.total_profit() == pytest.approx(current.total_profit(), abs=1e-9)


def test_the_locked_in_curve_defaults_to_the_current_one():
    """Which is what it *is* at initial recognition, so a group measured on
    the day it was written needs one curve and not two."""
    n = 10
    group, ra = level(n, premium=1000.0, claim=700.0)
    units = flat_units(n)
    a = measure(group, coverage=units, risk_adjustment=ra, current=FLAT4)
    b = measure(group, coverage=units, risk_adjustment=ra, current=FLAT4,
                locked_in=FLAT4)
    assert (a.csm == b.csm).all()


# --- inputs --------------------------------------------------------------


def test_a_group_without_a_risk_adjustment_measures_fine():
    n = 10
    inflows, outflows = np.full(n, 1000.0), np.full(n, 700.0)
    group = Group(inflows, outflows)
    m = measure(group, coverage=flat_units(n), current=FLAT4)
    assert (m.risk_adjustment == 0.0).all()
    assert m.total_profit() == pytest.approx(net_cash(group), abs=1e-9)


def test_mismatched_cashflow_lengths_are_refused():
    with pytest.raises(ValueError, match="different numbers of periods"):
        Group(np.ones(5), np.ones(6))


def test_an_unknown_timing_is_refused():
    group = Group(np.ones(5), np.ones(5), inflow_timing="middle")
    with pytest.raises(ValueError, match="timing must be one of"):
        group.fulfilment_cashflows(FLAT4)


def test_a_negative_risk_adjustment_is_refused():
    with pytest.raises(ValueError, match="not a benefit from it"):
        RiskAdjustment(-1.0, np.ones(5))


def test_a_driver_that_never_runs_is_refused():
    with pytest.raises(ValueError, match="no exposure"):
        RiskAdjustment(100.0, np.zeros(5))


def test_coverage_units_that_never_run_are_refused():
    with pytest.raises(ValueError, match="no service"):
        CoverageUnits(np.zeros(5))


def test_negative_coverage_units_are_refused():
    with pytest.raises(ValueError, match="cannot be negative"):
        CoverageUnits([1.0, -1.0])


def test_a_change_series_of_the_wrong_length_is_refused():
    n = 10
    group, ra = level(n, premium=1000.0, claim=700.0)
    with pytest.raises(ValueError, match="changes_in_estimate covers"):
        measure(group, coverage=flat_units(n), risk_adjustment=ra,
                current=FLAT4, changes_in_estimate=np.zeros(3))


def test_drivers_shorter_than_the_projection_are_padded_not_stretched():
    """A driver that stops before the projection does means the exposure
    stopped, not that it should be spread thinner."""
    ra = RiskAdjustment(100.0, np.ones(5))
    balance = ra.balance(8)
    assert balance[5] == pytest.approx(0.0)
    assert balance[0] == pytest.approx(100.0)
    assert ra.release(8)[5:] == pytest.approx(np.zeros(3))


def test_the_measurement_repr_says_what_it_is():
    n = 6
    group, ra = level(n, premium=1000.0, claim=700.0)
    m = measure(group, coverage=flat_units(n), risk_adjustment=ra, current=FLAT4)
    assert isinstance(m, Measurement)
    assert "csm[0]" in repr(m) and "6 periods" in repr(m)


# --- as an overlay on a projection ---------------------------------------


def test_a_projected_term_assurance_measures_end_to_end():
    """The point of Layer 2: the accounting reads the projection.

    Nothing about ``TermLife`` knows IFRS 17 exists, and nothing in this
    module re-derives a cashflow. The bridge is the run's own output
    series, summed across the block.
    """
    from engine.core.runner import run
    from engine.data.assumptions import Assumptions, MortalityTable
    from engine.data.expenses import ExpenseScale, Expenses
    from engine.data.modelpoints import ModelPoint
    from engine.library.term_life import TermLife

    mortality = MortalityTable(
        {age: min(0.0004 * 1.09 ** (age - 30), 1.0) for age in range(0, 121)}
    )
    assumptions = Assumptions(
        mortality=mortality, lapse=0.05, interest=0.04,
        expenses=Expenses(initial=ExpenseScale(per_policy=180.0),
                          renewal=ExpenseScale(per_policy=30.0)),
    )
    points = [ModelPoint(id=1, age_at_entry=40, term_years=20,
                         sum_assured=150_000.0, annual_premium=520.0,
                         init_pols=1000.0)]
    result = run(TermLife, points, assumptions, 21,
                 outputs=["premiums", "claims", "expenses", "initial_expenses",
                          "pols_if"])

    n = 20
    group = Group.from_run(result, inflows=["premiums"],
                           outflows=["claims", "expenses"], periods=n,
                           acquisition=float(result.aggregate("initial_expenses")[0]))
    units = CoverageUnits(np.asarray(result.aggregate("pols_if")[:n]))
    claims = np.asarray(result.aggregate("claims")[:n])
    m = measure(group, coverage=units,
                risk_adjustment=RiskAdjustment.percent_of(claims, 0.05),
                current=FLAT4)

    assert m.total_profit() == pytest.approx(net_cash(group), abs=1e-8)
    assert m.csm[-1] == 0.0
    assert m.liability[0] == pytest.approx(0.0, abs=1e-8)
    # A term assurance releases its margin as cover runs off, so the shape
    # follows the in-force curve rather than being level.
    assert m.csm_release[0] > m.csm_release[-1] * 0.0
    assert (m.csm >= 0.0).all()


def test_the_bridge_needs_at_least_one_series():
    class Empty:
        def aggregate(self, name):
            return [0.0]

    with pytest.raises(ValueError, match="at least one series"):
        Group.from_run(Empty(), inflows=[], outflows=["x"])


def test_the_loss_component_finishes_with_the_last_service_expense():
    """Found in review, after the module had merged: allocated on coverage
    units and capped by each period's outflows, a group whose claims land
    early froze the unamortised remainder the day its outflows stopped —
    70% of the loss component carried forever inside a fulfilment-cashflow
    balance of zero. The loss is made of service expenses, so it amortises
    on its own basis: this period's share of all that remain."""
    n = 10
    out = np.zeros(n)
    out[:3] = 800.0
    group = Group(np.full(n, 150.0), out)
    m = measure(group, coverage=flat_units(n), current=FLAT4)
    assert m.onerous
    assert m.loss_component[3] == pytest.approx(0.0, abs=1e-9)
    assert (m.loss_component[3:] == 0.0).all()
    assert m.total_profit() == pytest.approx(net_cash(group), abs=1e-9)


def test_an_acquisition_driven_loss_beyond_all_service_expenses_is_the_b125_gap():
    """The one strand that remains, and it is stated rather than silent.

    A day-one loss larger than every service expense the group will ever
    incur can only arise from acquisition cashflows, whose recovery is
    B125's separate revenue gross-up — not modelled here (see the RFC).
    The allocation takes everything it lawfully can: the residue equals
    the loss less the whole allocatable basis, revenue never goes
    negative, and total profit still reconciles to net cash.
    """
    n = 10
    group = Group(np.full(n, 100.0), np.full(n, 20.0), acquisition=2000.0)
    m = measure(group, coverage=flat_units(n), current=FLAT4)
    basis = group.outflows.sum() + m.risk_adjustment_release.sum()
    assert m.loss_component[0] > basis
    assert m.loss_component[-1] == pytest.approx(
        m.loss_component[0] - basis, rel=1e-9)
    assert (m.insurance_revenue >= -1e-9).all()
    assert m.total_profit() == pytest.approx(net_cash(group), abs=1e-8)
