"""US statutory reserves: the cap that bites, and the roll that is not new.

Execution plan §5, item C2. Two claims are under test.

**The modified-reserve family is one function of one number.** Net level,
CRVM and full preliminary term differ only in the expense allowance they
grant, so the suite pins the two extremes against code that already
existed — ``E = 0`` must reproduce RFC-018's net premium reserve, and the
full preliminary term allowance must reproduce its ``full_preliminary_term``
— and then measures what CRVM does between them.

**The finding, asserted rather than argued.** The first-year CRVM reserve
has a closed form: ``(E_fpt − E) · ä_{x+1:n−1} / ä_{x:n}``, the cap's bite
scaled by the renewal share of the annuity. It is zero for every plan where
the cap does not bind and strictly positive where it does, and the
crossover is a kink — so a sensitivity computed on one side mispredicts the
other, which the suite demonstrates with numbers rather than warning about.

The asset-adequacy half is mostly a claim that nothing was reinvented: the
deficiency it reports must be RFC-016's, to the bit.
"""

from __future__ import annotations

import numpy as np
import pytest

from engine.data.assumptions import MortalityTable
from engine.library.reserves import (
    annuity_due,
    assurance,
    full_preliminary_term,
    net_premium,
    prospective_reserve,
)
from engine.report.pbr import (
    cte,
    greatest_present_value_of_accumulated_deficiency,
)
from engine.report.statutory import (
    CRVM_REFERENCE_PAYMENTS,
    AdequacyResult,
    ExpenseAllowance,
    StatutoryError,
    StatutoryReserve,
    asset_adequacy,
    crvm_allowance,
    crvm_reserve,
    first_year_strain,
    fpt_allowance,
    modified_premiums,
    modified_reserve,
    statutory_reserve,
)

QX = {age: min(0.0006 * 1.09 ** (age - 20), 0.5) for age in range(20, 121)}
BASIS = MortalityTable(QX)
RATE = 0.03
#: How far "whole life" runs for the reference plan. Supplied rather than
#: guessed — see ``crvm_allowance``.
HORIZON = 80


def crvm(age=40, term=20, product="term", **kw):
    return crvm_reserve(BASIS, age, term, RATE, product=product,
                        whole_life_term=HORIZON, **kw)


def allowance(age=40, term=20, product="term", **kw):
    return crvm_allowance(BASIS, age, term, RATE, product=product,
                          whole_life_term=HORIZON, **kw)


# --------------------------------------------------------------------------
# One family, one number
# --------------------------------------------------------------------------

def test_a_zero_allowance_is_the_net_premium_reserve_that_already_existed():
    """The bottom of the family. If this differs from RFC-018's reserve,
    the modified machinery has a bug in it and every method above is
    wrong too."""
    level = net_premium(BASIS, 40, 20, RATE)
    expected = prospective_reserve(BASIS, 40, 20, RATE, premium=level)
    got = modified_reserve(BASIS, 40, 20, RATE, allowance=0.0)
    assert got[1:] == pytest.approx(expected[1:], rel=0, abs=1e-15)
    assert got[0] == 0.0


def test_the_full_preliminary_term_allowance_reproduces_that_method():
    """The top of the family, against code that was already tested."""
    granted = fpt_allowance(BASIS, 40, 20, RATE)
    got = modified_reserve(BASIS, 40, 20, RATE, allowance=granted)
    expected = full_preliminary_term(BASIS, 40, 20, RATE)
    assert got == pytest.approx(expected, rel=0, abs=1e-15)
    assert got[1] == 0.0


def test_the_modified_premiums_are_what_the_method_says_they_are():
    """``alpha`` is the one-year term cost under full preliminary term, and
    ``alpha == beta`` when nothing is allowed."""
    level = net_premium(BASIS, 40, 20, RATE)
    alpha, beta = modified_premiums(BASIS, 40, 20, RATE, allowance=0.0)
    assert alpha == pytest.approx(beta) == pytest.approx(level)

    granted = fpt_allowance(BASIS, 40, 20, RATE)
    alpha, beta = modified_premiums(BASIS, 40, 20, RATE, allowance=granted)
    cost = float(assurance(BASIS, 40, 1, RATE)[0])
    assert alpha == pytest.approx(cost, rel=1e-12)
    assert beta - alpha == pytest.approx(granted)
    assert beta > level > alpha


def test_a_larger_allowance_lowers_the_first_year_and_raises_the_rest():
    """What a modified basis is *for*: less reserve when the money was
    spent, more later."""
    small = modified_reserve(BASIS, 40, 20, RATE, allowance=0.001)
    large = modified_reserve(BASIS, 40, 20, RATE, allowance=0.004)
    assert large[1] < small[1]
    assert large[10] < small[10]        # the renewal premium is higher
    assert small[0] == large[0] == 0.0


# --------------------------------------------------------------------------
# The cap, and the finding
# --------------------------------------------------------------------------

def test_where_the_cap_does_not_bind_crvm_is_full_preliminary_term():
    """Not approximately — the same numbers. A term assurance's allowance
    is nowhere near a twenty-payment whole life's, so the cap is inert and
    CRVM has nothing to do."""
    granted = allowance()
    assert not granted.binds
    assert granted.granted == granted.full_preliminary_term
    assert granted.bite() == 0.0
    assert crvm() == pytest.approx(full_preliminary_term(BASIS, 40, 20, RATE),
                                   rel=0, abs=1e-15)
    assert crvm()[1] == 0.0


def test_on_an_endowment_the_cap_bites_and_the_first_year_is_not_zero():
    """An endowment's premium is mostly savings, so full preliminary term
    would defer far more than a whole life ever earns. This is what the cap
    exists for."""
    granted = allowance(term=20, product="endowment")
    assert granted.binds
    assert granted.cap < granted.full_preliminary_term
    assert granted.bite() > 0.0
    reserve = crvm(term=20, product="endowment")
    assert reserve[1] > 0.0
    assert reserve[1] > full_preliminary_term(BASIS, 40, 20, RATE,
                                              product="endowment")[1]


def test_the_first_year_reserve_is_exactly_the_caps_bite_scaled():
    """The finding, to the last bits.

    ``V_1 = (E_fpt − E) · ä_{x+1:n−1} / ä_{x:n}``. The closed form is
    computed from the allowance alone and the reserve from the whole
    prospective expression, so their agreeing is the identity rather than a
    restatement of it.
    """
    for term in (10, 15, 20, 25, 30):
        reserve = crvm(term=term, product="endowment")
        closed = first_year_strain(BASIS, 40, term, RATE, product="endowment",
                                   whole_life_term=HORIZON)
        assert reserve[1] == pytest.approx(closed, rel=1e-12, abs=1e-15), term


def test_the_first_year_is_zero_exactly_when_the_cap_does_not_bind():
    """Two ways of saying the same thing, and they must never disagree:
    a plan has first-year strain if and only if the cap took something."""
    for term in range(5, 41, 5):
        for product in ("term", "endowment"):
            granted = allowance(term=term, product=product)
            strain = first_year_strain(BASIS, 40, term, RATE, product=product,
                                       whole_life_term=HORIZON)
            assert (strain > 0.0) == granted.binds, (term, product)
            if not granted.binds:
                assert strain == 0.0


def test_the_crossover_is_a_kink_so_a_one_sided_sensitivity_mispredicts():
    """The consequence worth stating. First-year strain is flat at zero on
    one side of the cap and rising on the other, so the slope measured
    where the cap is inert predicts nothing about where it bites — the same
    trap as RFC-026's counterparty band cliff, in a different regime.
    """
    strains = {term: first_year_strain(BASIS, 40, term, RATE,
                                       product="endowment",
                                       whole_life_term=HORIZON)
               for term in range(10, 41)}
    binding = [t for t, s in strains.items() if s > 0.0]
    inert = [t for t, s in strains.items() if s == 0.0]
    assert binding and inert
    # The cap bites on the short plans and lets go on the long ones.
    assert max(binding) < min(inert)

    boundary = min(inert)
    # Flat on the far side: the slope there is exactly zero...
    assert strains[boundary + 1] - strains[boundary] == 0.0
    # ...and emphatically not zero on the near side.
    near = strains[boundary - 2] - strains[boundary - 1]
    assert near > 0.0
    # So extrapolating the flat side across the boundary misses the strain
    # entirely — it predicts zero and the answer is not.
    assert strains[boundary - 1] > 0.0


def test_the_reference_plan_is_a_parameter_and_moving_it_moves_everything():
    """A regime that changes the reference plan changes every first-year
    reserve in the book, which is why it is named rather than inlined."""
    assert CRVM_REFERENCE_PAYMENTS == 20
    tighter = crvm_allowance(BASIS, 40, 20, RATE, product="endowment",
                             whole_life_term=HORIZON, reference_payments=10)
    looser = allowance(term=20, product="endowment")
    # Fewer premiums to spread the reference allowance over raises the cap.
    assert tighter.cap > looser.cap
    assert tighter.granted >= looser.granted


# --------------------------------------------------------------------------
# The statement figure
# --------------------------------------------------------------------------

def test_the_statutory_reserve_is_floored_by_the_cash_value():
    reserve = statutory_reserve(BASIS, 40, 20, RATE, product="endowment",
                                cash_value=0.05, whole_life_term=HORIZON)
    assert isinstance(reserve, StatutoryReserve)
    assert np.all(reserve.value >= 0.05 - 1e-15)
    assert reserve.binding[0] == "cash_value"
    crossing = reserve.crossover()
    assert crossing is not None and crossing > 0
    assert reserve.binding[crossing] == "reserve"
    assert reserve.allowance.binds


def test_every_method_is_reachable_and_an_unknown_one_is_refused():
    for method in ("crvm", "net_level", "full_preliminary_term"):
        got = statutory_reserve(BASIS, 40, 20, RATE, method=method,
                                whole_life_term=HORIZON)
        assert got.method == method
        assert got.reserve[0] == 0.0
    with pytest.raises(StatutoryError, match="unknown method"):
        statutory_reserve(BASIS, 40, 20, RATE, method="prudent guess")


def test_the_uncapped_method_reports_a_cap_that_cannot_bind():
    got = statutory_reserve(BASIS, 40, 20, RATE,
                            method="full_preliminary_term")
    assert isinstance(got.allowance, ExpenseAllowance)
    assert got.allowance.cap == float("inf")
    assert not got.allowance.binds


def test_a_term_too_short_for_a_modified_method_is_refused():
    """A one-year contract has no renewal period, so there is nothing to
    spread an allowance over and no modified basis to speak of."""
    with pytest.raises(StatutoryError, match="at least two years"):
        fpt_allowance(BASIS, 40, 1, RATE)


# --------------------------------------------------------------------------
# Asset adequacy: the same roll, a different reduction
# --------------------------------------------------------------------------

def cashflow_block(seed=5, periods=20, scenarios=7, shortfall=0.0):
    """Assets and liabilities over a handful of prescribed-looking paths."""
    rng = np.random.default_rng(seed)
    rates = rng.normal(0.04, 0.015, (periods, scenarios))
    liabilities = np.full((periods, scenarios), 100.0)
    assets = np.full((periods, scenarios), 100.0 - shortfall)
    return assets, liabilities, rates


def test_a_block_whose_assets_last_reports_no_deficiency():
    assets, liabilities, rates = cashflow_block()
    result = asset_adequacy(assets, liabilities, rates, starting_assets=50.0)
    assert np.all(result.adequate)
    assert result.n_inadequate == 0
    assert result.additional_reserve == 0.0


def test_a_block_that_runs_out_needs_the_deficiency_as_extra_reserve():
    assets, liabilities, rates = cashflow_block(shortfall=8.0)
    result = asset_adequacy(assets, liabilities, rates)
    assert result.n_inadequate == assets.shape[1]
    assert result.additional_reserve > 0.0
    assert result.additional_reserve == result.deficiency.max()
    assert 0 <= result.worst_scenario < assets.shape[1]


def test_the_deficiency_is_rfc_016s_and_not_a_second_implementation():
    """The claim the module is built on: cash-flow testing and a
    principle-based reserve ask the same arithmetic a different question."""
    assets, liabilities, rates = cashflow_block(shortfall=8.0)
    result = asset_adequacy(assets, liabilities, rates, starting_assets=5.0)
    expected = greatest_present_value_of_accumulated_deficiency(
        assets - liabilities, rates, 5.0)
    assert np.array_equal(result.deficiency, expected)


def test_the_reduction_is_recorded_and_a_cte_is_never_above_the_maximum():
    """A maximum over seven prescribed paths and a CTE over thousands are
    different statements about the same block, so which was used is part of
    the answer."""
    assets, liabilities, rates = cashflow_block(shortfall=8.0, scenarios=40)
    worst = asset_adequacy(assets, liabilities, rates)
    tail = asset_adequacy(assets, liabilities, rates, reduction="cte",
                          level=0.70)
    assert worst.reduction == "maximum" and worst.level is None
    assert tail.reduction == "cte" and tail.level == 0.70
    assert tail.additional_reserve == cte(tail.deficiency, 0.70)
    assert tail.additional_reserve <= worst.additional_reserve


def test_a_cte_reduction_without_a_level_is_refused():
    """The maximum is the reduction that needs no parameter; a tail measure
    that picked its own level would be answering a question nobody asked."""
    assets, liabilities, rates = cashflow_block()
    with pytest.raises(StatutoryError, match="needs a level"):
        asset_adequacy(assets, liabilities, rates, reduction="cte")
    with pytest.raises(StatutoryError, match="reduction must be"):
        asset_adequacy(assets, liabilities, rates, reduction="average")


def test_mismatched_projections_are_refused_rather_than_broadcast():
    assets, liabilities, rates = cashflow_block()
    with pytest.raises(StatutoryError, match="different projections"):
        asset_adequacy(assets, liabilities[:, :3], rates)
    with pytest.raises(StatutoryError, match="at least one scenario"):
        asset_adequacy(np.zeros((0, 0)), np.zeros((0, 0)), np.zeros((0, 0)))


def test_the_worst_date_is_reported_and_can_be_interior():
    """The reason cash-flow testing looks at a maximum over dates at all:
    a block that recovers by the end still needed the money when it did."""
    periods, scenarios = 12, 3
    rates = np.full((periods, scenarios), 0.03)
    liabilities = np.zeros((periods, scenarios))
    liabilities[4] = 500.0                    # one big outflow mid-life
    assets = np.full((periods, scenarios), 30.0)
    result = asset_adequacy(assets, liabilities, rates, starting_assets=100.0)
    assert result.additional_reserve > 0.0
    assert 4 <= result.worst_date < periods
    assert isinstance(result, AdequacyResult)
