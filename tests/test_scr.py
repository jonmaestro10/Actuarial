"""The assembled Solvency Capital Requirement — RFC-027.

Transcription first — Annex IV's matrix and Articles 203 to 207 against the
Official Journal — then the identities and the measured findings.
"""

import numpy as np
import pytest

from engine.report.scr import (
    BSCR_RISKS, DeferredTaxes, OperationalRisk, SolvencyCapitalRequirement,
    absorption_gap, basic_scr, basic_scr_correlation, deferred_tax_adjustment,
    intangible_capital, module_absorptions, solvency_capital_requirement,
    technical_provision_adjustment,
)
from engine.report.solvency2 import CorrelationMatrix

#: A life book with a with-profits fund: the balance sheet every measurement
#: below is made on.
GROSS = {"market": 400.0, "default": 60.0, "life": 300.0, "health": 40.0,
         "non_life": 0.0}
#: The same modules recomputed under Article 206(2), with each scenario
#: allowed to take the shock out of future discretionary benefits.
NET = {"market": 280.0, "default": 60.0, "life": 210.0, "health": 40.0,
       "non_life": 0.0}
OPERATIONAL = OperationalRisk(
    earned_life=500.0, prior_life=460.0, earned_life_ul=200.0,
    prior_life_ul=180.0, tp_life=6000.0, tp_life_ul=2500.0,
    unit_linked_expenses=40.0,
)


# --------------------------------------------------------------------------
# Transcription
# --------------------------------------------------------------------------

def test_annex_iv_matrix_is_the_published_one():
    """Annex IV point 1 to Directive 2009/138/EC. Everything at 0.25 except
    three cells."""
    matrix = basic_scr_correlation()
    assert matrix.risks == BSCR_RISKS
    index = matrix.index
    values = matrix.matrix
    assert values[index["life"], index["non_life"]] == 0.0
    assert values[index["health"], index["non_life"]] == 0.0
    assert values[index["default"], index["non_life"]] == 0.5
    off_diagonal = [
        values[i, j] for i in range(5) for j in range(5)
        if i != j and {BSCR_RISKS[i], BSCR_RISKS[j]} not in (
            {"life", "non_life"}, {"health", "non_life"},
            {"default", "non_life"})
    ]
    assert set(off_diagonal) == {0.25}
    assert float(np.linalg.eigvalsh(values).min()) > 0.4


def test_intangible_factor_is_eighty_percent():
    """Article 203."""
    assert intangible_capital(1_000.0) == pytest.approx(800.0)
    assert intangible_capital(0.0) == 0.0


def test_operational_factors_are_the_published_ones():
    """Article 204(3) and (4): 4% and 3% on premiums, 0.45% and 3% on
    provisions, and the growth terms at 1.2 times the prior year."""
    flat = OperationalRisk(earned_life=1_000.0, earned_non_life=1_000.0,
                           prior_life=1_000.0, prior_non_life=1_000.0)
    assert flat.premiums_basis() == pytest.approx(0.04 * 1_000 + 0.03 * 1_000)
    provisions = OperationalRisk(tp_life=1_000.0, tp_non_life=1_000.0)
    assert provisions.provisions_basis() == pytest.approx(
        0.0045 * 1_000 + 0.03 * 1_000)
    # A non-life provision costs nearly seven times a life one.
    assert 0.03 / 0.0045 == pytest.approx(6.667, abs=5e-4)


def test_unit_linked_premiums_and_provisions_are_carved_out():
    """Article 204(3)(b) and (4)(b): the unit-linked slice is excluded from
    both bases, because its risk sits with the policyholder."""
    with_ul = OperationalRisk(earned_life=1_000.0, earned_life_ul=400.0,
                              prior_life=1_000.0, prior_life_ul=400.0,
                              tp_life=5_000.0, tp_life_ul=2_000.0)
    assert with_ul.premiums_basis() == pytest.approx(0.04 * 600.0)
    assert with_ul.provisions_basis() == pytest.approx(0.0045 * 3_000.0)


def test_growth_terms_are_floored_separately():
    """Article 204(3): shrinking one line cannot offset growth in another,
    because each ``max(0, ...)`` is taken before they are added."""
    growing_life = OperationalRisk(earned_life=2_000.0, prior_life=1_000.0,
                                   earned_non_life=500.0,
                                   prior_non_life=1_000.0)
    level = 0.04 * 2_000.0 + 0.03 * 500.0
    life_growth = 0.04 * (2_000.0 - 1.2 * 1_000.0)
    assert growing_life.premiums_basis() == pytest.approx(level + life_growth)
    # The shrinking non-life book contributes no negative growth term.
    assert growing_life.premiums_basis() > level


def test_the_operational_basis_is_the_worse_of_two_never_their_sum():
    """Article 204(2)."""
    both = OperationalRisk(earned_life=1_000.0, prior_life=1_000.0,
                           tp_life=1_000.0)
    assert both.basic == max(both.premiums_basis(), both.provisions_basis())
    assert both.basic < both.premiums_basis() + both.provisions_basis()


# --------------------------------------------------------------------------
# The sign
# --------------------------------------------------------------------------

def test_both_adjustments_are_negative_or_zero():
    """Article 206(1) carries a leading minus and Article 207(3) makes a
    released liability a negative adjustment, so that Article 103's *sum*
    reduces the requirement. This is the one thing here worth checking
    rather than assuming: dropping the minus raises nothing, it just
    reports an SCR wrong by twice the adjustment, in the direction that
    looks prudent."""
    adj_tp = technical_provision_adjustment(600.0, 450.0, 1_000.0)
    assert adj_tp == pytest.approx(-150.0)
    adj_dt = deferred_tax_adjustment(400.0, DeferredTaxes(rate=0.25,
                                                          net_liability=1e9))
    assert adj_dt == pytest.approx(-100.0)
    position = SolvencyCapitalRequirement(bscr=600.0, operational=30.0,
                                          adjustment_tp=adj_tp,
                                          adjustment_dt=adj_dt)
    assert position.scr == pytest.approx(600.0 + 30.0 - 150.0 - 100.0)
    assert position.reconciles()


def test_a_positive_adjustment_fails_the_reconciliation():
    """The guard with teeth: an implementation that lost the minus sign."""
    wrong = SolvencyCapitalRequirement(bscr=600.0, operational=30.0,
                                       adjustment_tp=+150.0)
    assert not wrong.reconciles()


def test_the_adjustment_is_the_sum_of_its_two_halves():
    """Article 205."""
    position = SolvencyCapitalRequirement(bscr=500.0, operational=20.0,
                                          adjustment_tp=-80.0,
                                          adjustment_dt=-30.0)
    assert position.adjustment == pytest.approx(-110.0)
    assert position.scr == pytest.approx(410.0)
    assert position.relief == pytest.approx(110.0 / 520.0)


# --------------------------------------------------------------------------
# Article 206: the two clamps
# --------------------------------------------------------------------------

def test_a_fund_with_no_discretionary_benefits_gets_no_relief():
    """The cap at FDB is the whole character of the sub-module: relief is
    limited to what the fund could actually take away from policyholders,
    so a fund with nothing discretionary gets nothing however far its
    liabilities would move."""
    assert technical_provision_adjustment(600.0, 400.0, 0.0) == 0.0
    assert technical_provision_adjustment(600.0, 400.0, 50.0) == -50.0
    assert technical_provision_adjustment(600.0, 400.0, 500.0) == -200.0


def test_absorption_beyond_the_discretionary_benefits_is_free_to_no_one():
    """Once absorption exceeds FDB the relief stops moving, so a fund that
    absorbs more gets nothing further for it. The adjustment is a clamped
    difference, and both kinks are real."""
    absorbed = [technical_provision_adjustment(600.0, 600.0 - a, 120.0)
                for a in (0.0, 60.0, 120.0, 180.0, 400.0)]
    assert absorbed == [0.0, -60.0, -120.0, -120.0, -120.0]


def test_absorption_cannot_make_the_requirement_worse():
    """The floor at zero: a net BSCR *above* the gross one — which Article
    206(2)'s re-aggregation can produce — gives no adjustment rather than a
    positive one."""
    assert technical_provision_adjustment(600.0, 650.0, 1_000.0) == 0.0


def test_negative_discretionary_benefits_are_refused():
    with pytest.raises(ValueError, match="cannot be negative"):
        technical_provision_adjustment(600.0, 400.0, -1.0)


# --------------------------------------------------------------------------
# The finding: absorption does not survive re-aggregation
# --------------------------------------------------------------------------

def test_the_modules_give_up_more_than_the_basic_scr_does():
    """Article 206(2) recomputes each module net and then **re-aggregates**,
    and a correlation matrix is not linear in its inputs.

    On this fund the market and life modules between them absorb 210. The
    Basic SCR falls by only 165.24. The missing **44.76 — 21.3% of the
    absorption — is eaten by the aggregation**, because diversification had
    already discounted those modules and removing risk from them removes
    less than its face value from the total.

    RFC-026 found the same shape one level down, in Article 164(3). A
    reviewer handed the module-level absorptions cannot reproduce the
    adjustment from them.
    """
    absorptions = module_absorptions(GROSS, NET)
    assert absorptions == {"market": 120.0, "default": 0.0, "life": 90.0,
                           "health": 0.0, "non_life": 0.0}
    assert sum(absorptions.values()) == pytest.approx(210.0)
    gross, net = basic_scr(GROSS), basic_scr(NET)
    assert gross == pytest.approx(592.790, abs=5e-3)
    assert net == pytest.approx(427.551, abs=5e-3)
    assert gross - net == pytest.approx(165.239, abs=5e-3)
    gap = absorption_gap(GROSS, NET)
    assert gap == pytest.approx(44.761, abs=5e-3)
    assert gap / sum(absorptions.values()) == pytest.approx(0.2131, abs=5e-4)


def test_the_gap_is_zero_when_only_one_module_carries_risk():
    """It is an aggregation effect and nothing else: with a single non-zero
    module there is no diversification to discount, and the absorption
    passes through exactly."""
    gross = {"market": 400.0, "default": 0.0, "life": 0.0, "health": 0.0,
             "non_life": 0.0}
    net = {**gross, "market": 250.0}
    assert absorption_gap(gross, net) == pytest.approx(0.0, abs=1e-9)
    assert basic_scr(gross) - basic_scr(net) == pytest.approx(150.0)


def test_an_unknown_net_module_is_refused():
    with pytest.raises(ValueError, match="not gross modules"):
        module_absorptions(GROSS, {**NET, "operational": 5.0})


# --------------------------------------------------------------------------
# The finding: the two adjustments compete
# --------------------------------------------------------------------------

def test_each_unit_of_provision_relief_buys_only_one_minus_the_tax_rate():
    """Article 207(1) makes the deferred tax loss ``BSCR + Adj_TP +
    SCR_op``, and ``Adj_TP`` is negative — so relief already taken in the
    with-profits fund shrinks the loss the tax line is allowed to absorb.

    At a 25% rate every unit of technical-provision absorption reduces the
    SCR by exactly **0.75**, measured to the digit across the whole range
    where neither clamp binds. The two halves of Article 205 are not
    additive: they compete for the same loss.
    """
    taxes = DeferredTaxes(rate=0.25, net_liability=1e9)
    previous = None
    ratios = []
    for fdb in (0.0, 20.0, 40.0, 60.0, 80.0, 100.0, 120.0):
        position = solvency_capital_requirement(
            GROSS, net_modules=NET, operational=OPERATIONAL,
            future_discretionary_benefits=fdb, taxes=taxes)
        if previous is not None:
            d_adj = position.adjustment_tp - previous[0]
            ratios.append((position.scr - previous[1]) / d_adj)
        previous = (position.adjustment_tp, position.scr)
    assert ratios == pytest.approx([0.75] * len(ratios))
    for rate in (0.0, 0.10, 0.19, 0.25, 0.40):
        taxes = DeferredTaxes(rate=rate, net_liability=1e9)
        a = solvency_capital_requirement(
            GROSS, net_modules=NET, operational=OPERATIONAL,
            future_discretionary_benefits=40.0, taxes=taxes)
        b = solvency_capital_requirement(
            GROSS, net_modules=NET, operational=OPERATIONAL,
            future_discretionary_benefits=60.0, taxes=taxes)
        assert ((b.scr - a.scr) / (b.adjustment_tp - a.adjustment_tp)
                == pytest.approx(1.0 - rate))


def test_the_deferred_tax_demonstration_is_worth_a_quarter_of_the_scr():
    """Article 207(2a) lets an undertaking use an *increase* in deferred tax
    assets only where it can demonstrate probable future taxable profit.
    That is a judgement with no arithmetic in it, and on this fund it moves
    the SCR from 608.54 to 456.41 — **25% of the requirement**, decided by
    a projection the regulation describes conditions for and does not
    specify.

    So it is an input here, in the same way RFC-014 takes the risk margin's
    run-off driver.
    """
    operational = OperationalRisk(tp_life=6_000.0, tp_life_ul=2_500.0)
    outcomes = {}
    for label, taxes in (
        ("none", DeferredTaxes(rate=0.25, net_liability=0.0,
                               recognisable_asset=0.0)),
        ("partial", DeferredTaxes(rate=0.25, net_liability=0.0,
                                  recognisable_asset=75.0)),
        ("full", DeferredTaxes(rate=0.25, net_liability=0.0,
                               recognisable_asset=None)),
        ("liability only", DeferredTaxes(rate=0.25, net_liability=40.0,
                                         recognisable_asset=0.0)),
    ):
        outcomes[label] = solvency_capital_requirement(
            GROSS, operational=operational, taxes=taxes)
    assert outcomes["none"].adjustment_dt == 0.0
    assert outcomes["partial"].adjustment_dt == pytest.approx(-75.0)
    assert outcomes["full"].adjustment_dt == pytest.approx(-152.135, abs=5e-3)
    assert outcomes["liability only"].adjustment_dt == pytest.approx(-40.0)
    assert outcomes["none"].scr == pytest.approx(608.540, abs=5e-3)
    assert outcomes["full"].scr == pytest.approx(456.405, abs=5e-3)
    saved = outcomes["none"].scr - outcomes["full"].scr
    assert saved / outcomes["none"].scr == pytest.approx(0.250, abs=5e-4)


def test_a_deferred_tax_liability_absorbs_without_any_demonstration():
    """The liability is already on the balance sheet; releasing it needs
    nobody's opinion about future profit. Only the excess creates an asset
    and only the excess is clamped."""
    taxes = DeferredTaxes(rate=0.25, net_liability=30.0,
                          recognisable_asset=0.0)
    assert taxes.utilised(400.0) == (30.0, 0.0)
    assert deferred_tax_adjustment(400.0, taxes) == pytest.approx(-30.0)
    assert deferred_tax_adjustment(80.0, taxes) == pytest.approx(-20.0)


def test_a_gain_produces_no_deferred_tax_adjustment():
    """Article 207(4): a positive change in deferred taxes gives a nil
    adjustment, never a positive one."""
    taxes = DeferredTaxes(rate=0.25, net_liability=1e9)
    assert deferred_tax_adjustment(0.0, taxes) == 0.0
    assert deferred_tax_adjustment(-500.0, taxes) == 0.0


def test_an_impossible_tax_rate_is_refused():
    with pytest.raises(ValueError, match="must be in"):
        DeferredTaxes(rate=1.0)
    with pytest.raises(ValueError, match="cannot be negative"):
        DeferredTaxes(rate=0.25, recognisable_asset=-1.0)


# --------------------------------------------------------------------------
# The finding: two things sit outside the aggregation
# --------------------------------------------------------------------------

def test_intangible_risk_receives_no_diversification_at_all():
    """Annex IV point 1 adds Article 203's charge **outside** the square
    root. A hundred of intangible charge adds exactly a hundred to the
    Basic SCR; a hundred added to the health module adds 45.49 — so the
    same capital costs 2.2 times as much when it is intangible risk."""
    base = basic_scr(GROSS)
    with_intangible = basic_scr(GROSS, intangible=intangible_capital(125.0))
    assert intangible_capital(125.0) == pytest.approx(100.0)
    assert with_intangible - base == pytest.approx(100.0)
    grown = {**GROSS, "health": GROSS["health"] + 100.0}
    assert basic_scr(grown) - base == pytest.approx(45.489, abs=5e-3)
    assert 100.0 / (basic_scr(grown) - base) == pytest.approx(2.198, abs=5e-3)


def test_the_unit_linked_expense_term_escapes_the_operational_cap():
    """Article 204(1) caps the operational charge at 30% of the Basic SCR
    and then adds ``0.25 · Exp_ul`` **outside** the cap.

    On a small balance sheet that is not a detail: a Basic SCR of 20 caps
    the charge at 6, and 400 of unit-linked expenses take it to 106 — more
    than five times the Basic SCR the cap was measured against.
    """
    operational = OperationalRisk(tp_non_life=2_000.0)
    assert operational.basic == pytest.approx(60.0)
    assert operational.capital(400.0) == pytest.approx(60.0)
    assert not operational.capped(400.0)
    assert operational.capital(20.0) == pytest.approx(6.0)
    assert operational.capped(20.0)
    with_ul = OperationalRisk(tp_non_life=2_000.0,
                              unit_linked_expenses=400.0)
    assert with_ul.capital(20.0) == pytest.approx(106.0)
    assert with_ul.capital(20.0) > 5.0 * 20.0


def test_the_operational_cap_makes_it_a_function_of_the_other_risks():
    """A firm that de-risks its balance sheet cuts an operational charge
    that has not changed — the same volumes, the same processes, a smaller
    number."""
    operational = OperationalRisk(tp_non_life=2_000.0)
    charges = [operational.capital(bscr) for bscr in (400.0, 200.0, 100.0,
                                                      50.0)]
    assert charges == pytest.approx([60.0, 60.0, 30.0, 15.0])


# --------------------------------------------------------------------------
# The finding: what Annex IV's two zeros are worth
# --------------------------------------------------------------------------

def test_a_composite_gets_a_fifth_of_its_capital_from_two_zeros():
    """Annex IV point 1 says a life book and a non-life book at the same
    insurer share **no** risk. Written down as a number: the life fund
    needs 592.79 and the non-life fund 300.00, and together they need
    720.69 rather than 892.79 — a **19.3% saving** for putting them under
    one roof.

    Set those two cells to 0.25, the value every other off-diagonal cell
    takes, and the composite would need 34.55 more. The two zeros are the
    single largest diversification benefit in the standard formula and they
    are an assertion, not a calibration anyone can see.
    """
    life_only = {**GROSS, "non_life": 0.0}
    non_life_only = {"market": 0.0, "default": 0.0, "life": 0.0,
                     "health": 0.0, "non_life": 300.0}
    composite = {**GROSS, "non_life": 300.0}
    separate = basic_scr(life_only) + basic_scr(non_life_only)
    together = basic_scr(composite)
    assert basic_scr(life_only) == pytest.approx(592.790, abs=5e-3)
    assert basic_scr(non_life_only) == pytest.approx(300.0)
    assert together == pytest.approx(720.694, abs=5e-3)
    assert 1.0 - together / separate == pytest.approx(0.1928, abs=5e-4)

    uniform = CorrelationMatrix(BSCR_RISKS, [
        [1.00, 0.25, 0.25, 0.25, 0.25],
        [0.25, 1.00, 0.25, 0.25, 0.50],
        [0.25, 0.25, 1.00, 0.25, 0.25],
        [0.25, 0.25, 0.25, 1.00, 0.25],
        [0.25, 0.50, 0.25, 0.25, 1.00],
    ])
    assert uniform.aggregate(composite) - together == pytest.approx(
        34.554, abs=5e-3)


# --------------------------------------------------------------------------
# The whole thing
# --------------------------------------------------------------------------

def test_the_assembled_requirement_reconciles():
    position = solvency_capital_requirement(
        GROSS, net_modules=NET, operational=OPERATIONAL,
        future_discretionary_benefits=120.0,
        taxes=DeferredTaxes(rate=0.25, net_liability=1e9))
    assert position.reconciles()
    assert position.bscr == pytest.approx(592.790, abs=5e-3)
    assert position.operational == pytest.approx(25.75)
    assert position.adjustment_tp == pytest.approx(-120.0)
    assert position.adjustment_dt == pytest.approx(-124.635, abs=5e-3)
    assert position.scr == pytest.approx(373.905, abs=5e-3)
    assert position.relief == pytest.approx(0.3955, abs=5e-4)
    assert position.undiversified == pytest.approx(800.0)
    assert "BSCR=592.79" in repr(position)


def test_a_fund_with_nothing_to_absorb_with_reports_no_adjustment():
    """No ``net_modules`` means no future discretionary benefits and no
    absorption — the right answer for a fund without them, which is why it
    is the default rather than an error."""
    position = solvency_capital_requirement(GROSS, operational=OPERATIONAL)
    assert position.adjustment_tp == 0.0
    assert position.adjustment_dt == 0.0
    assert position.scr == pytest.approx(position.bscr + position.operational)
    assert position.reconciles()


def test_the_operational_charge_is_measured_against_the_gross_basic_scr():
    """Article 204(1)(a) says BSCR, and Article 206's net figure is a
    device internal to the adjustment — so absorption does not quietly
    shrink the operational charge as well."""
    with_absorption = solvency_capital_requirement(
        GROSS, net_modules=NET, operational=OPERATIONAL,
        future_discretionary_benefits=1_000.0)
    without = solvency_capital_requirement(GROSS, operational=OPERATIONAL)
    assert with_absorption.operational == without.operational
