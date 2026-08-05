"""Policy reserves, whole life and endowment.

Most of what a reserve has to satisfy is an **exact identity**, so most of
these assert equality rather than closeness. Where a tolerance appears it
is floating-point accumulation over a long term, not modelling slack.
"""

from __future__ import annotations

import numpy as np
import pytest

from engine.core.runner import run
from engine.data.assumptions import Assumptions, MortalityTable
from engine.data.expenses import ExpenseScale, Expenses
from engine.data.modelpoints import ModelPoint
from engine.library import reserves
from engine.library.endowment import Endowment, WholeLife

BASIS = MortalityTable(
    {age: min(0.0004 * 1.09 ** (age - 30), 1.0) for age in range(0, 121)}
)
RATE, SUM_ASSURED, AGE, TERM = 0.03, 100_000.0, 40, 25


def priced(product, age=AGE, term=TERM, rate=RATE, **kw):
    premium = reserves.net_premium(BASIS, age, term, rate, product=product,
                                   sum_assured=SUM_ASSURED, **kw)
    return premium, dict(product=product, sum_assured=SUM_ASSURED, **kw)


# --- the identities ------------------------------------------------------


@pytest.mark.parametrize("product", ["term", "endowment", "pure_endowment"])
def test_the_prospective_and_retrospective_reserves_are_the_same_number(product):
    """The first structural fact about a reserve, and the thing that makes
    it well defined at all: looking forward and looking back agree,
    whenever the reserve is valued on the basis the premium was solved on.
    """
    premium, kw = priced(product)
    forward = reserves.prospective_reserve(BASIS, AGE, TERM, RATE,
                                           premium=premium, **kw)
    backward = reserves.retrospective_reserve(BASIS, AGE, TERM, RATE,
                                              premium=premium, **kw)
    assert forward == pytest.approx(backward, abs=1e-8)


def test_a_maturity_is_not_a_benefit_already_paid():
    """Regression on a real bug. A retrospective accumulation counts only
    what has *been* paid, and an endowment's maturity falls due **at**
    duration ``term`` rather than before it. Subtracting it there made the
    closing retrospective reserve zero where the prospective one correctly
    holds the sum assured — the two definitions disagreed by exactly the
    sum assured, which is as wrong as a reserve can be.
    """
    premium, kw = priced("endowment")
    backward = reserves.retrospective_reserve(BASIS, AGE, TERM, RATE,
                                              premium=premium, **kw)
    assert backward[-1] == pytest.approx(SUM_ASSURED, rel=1e-9)


@pytest.mark.parametrize("product,closing", [
    ("term", 0.0), ("endowment", SUM_ASSURED), ("pure_endowment", SUM_ASSURED),
])
def test_the_reserve_opens_at_nothing_and_closes_at_the_benefit(product, closing):
    """Nil at issue is the equivalence principle restated: the premium was
    solved so that it is."""
    premium, kw = priced(product)
    reserve = reserves.prospective_reserve(BASIS, AGE, TERM, RATE,
                                           premium=premium, **kw)
    assert reserve[0] == pytest.approx(0.0, abs=1e-9)
    assert reserve[-1] == pytest.approx(closing, rel=1e-9)


@pytest.mark.parametrize("product", ["term", "endowment"])
def test_the_reserve_is_self_financing(product):
    """``(V_t + P)(1 + i) = q·S + p·V_{t+1}`` — what the office holds after
    the year's premium and a year's interest is exactly what it needs for
    the claims it expects and the reserve it must still carry. A reserve
    that fails this is not a reserve.
    """
    premium, kw = priced(product)
    reserve = reserves.prospective_reserve(BASIS, AGE, TERM, RATE,
                                           premium=premium, **kw)
    residual = reserves.reserve_recursion_residual(
        BASIS, AGE, TERM, RATE, reserve=reserve, premium=premium,
        sum_assured=SUM_ASSURED)
    assert np.abs(residual).max() < 1e-8


def test_an_endowment_is_a_term_assurance_plus_a_pure_endowment():
    """Exactly — ``==`` on floats, not a tolerance. The identity is the
    definition of the product, which is why it is written that way rather
    than given a formula of its own."""
    shared = dict(sum_assured=SUM_ASSURED)
    death = reserves.benefit_value(BASIS, AGE, TERM, RATE, product="term",
                                   **shared)
    survival = reserves.benefit_value(BASIS, AGE, TERM, RATE,
                                      product="pure_endowment", **shared)
    both = reserves.benefit_value(BASIS, AGE, TERM, RATE, product="endowment",
                                  **shared)
    assert (both == death + survival).all()


def test_survival_starts_at_exactly_one():
    """By construction rather than by arithmetic: a life aged x has
    certainly reached age x."""
    assert reserves.survival(BASIS, AGE, TERM)[0] == 1.0


def test_a_duration_nobody_reaches_has_no_value_per_survivor():
    """Dividing straight through gives ``inf``, which is the bug
    ``prospective_annuity_factors`` already fixed once in this library."""
    certain = MortalityTable({age: 1.0 for age in range(0, 121)})
    factors = reserves.annuity_due(certain, 40, 5, RATE)
    assert factors.size == 6                       # durations 0 .. term
    assert np.isfinite(factors).all()
    # Everyone dies in the first year, so no later duration is reachable.
    assert factors[1:] == pytest.approx(np.zeros(5))


# --- what the closed form and the projection say to each other -----------


def basis_assumptions(lapse=0.0, **expense_kw):
    return Assumptions(mortality=BASIS, lapse=lapse, interest=RATE,
                       expenses=Expenses(**expense_kw) if expense_kw
                       else Expenses())


def test_the_projection_confirms_the_closed_form_premium():
    """Two layers that share no code agreeing to nine significant figures.

    ``reserves.net_premium`` solves a closed form off the mortality table;
    the template projects policies forward year by year through the
    decrement machinery. If the premium is right, the projected present
    value of premiums equals that of the benefits — which is the
    equivalence principle, arrived at from the other end.
    """
    premium = reserves.net_premium(BASIS, AGE, TERM, RATE, product="endowment",
                                   sum_assured=SUM_ASSURED)
    point = ModelPoint(id=1, age_at_entry=AGE, term_years=TERM,
                       sum_assured=SUM_ASSURED, annual_premium=premium,
                       init_pols=1000.0)
    model = Endowment(mp=point, assumptions=basis_assumptions(),
                      proj_len=TERM + 1)
    benefits = model.pv_claims() + model.pv_maturities()
    assert model.pv_premiums() == pytest.approx(benefits, rel=1e-9)


def test_a_whole_life_never_matures_and_an_endowment_does():
    """One class attribute apart, and no branch in any formula."""
    point = ModelPoint(id=1, age_at_entry=AGE, term_years=TERM,
                       sum_assured=SUM_ASSURED, annual_premium=2000.0,
                       init_pols=1000.0)
    assumptions = basis_assumptions()
    whole = WholeLife(mp=point, assumptions=assumptions, proj_len=TERM + 1)
    endow = Endowment(mp=point, assumptions=assumptions, proj_len=TERM + 1)
    assert whole.pv_maturities() == 0.0
    assert endow.pv_maturities() > 0.0
    # Everything else about them is identical.
    for t in range(TERM):
        assert float(whole.claims(t)) == float(endow.claims(t))
        assert float(whole.premiums(t)) == float(endow.premiums(t))


def test_the_maturity_falls_at_the_end_of_the_term_and_nowhere_else():
    premium = reserves.net_premium(BASIS, AGE, TERM, RATE, product="endowment",
                                   sum_assured=SUM_ASSURED)
    point = ModelPoint(id=1, age_at_entry=AGE, term_years=TERM,
                       sum_assured=SUM_ASSURED, annual_premium=premium,
                       init_pols=1000.0)
    result = run(Endowment, [point], basis_assumptions(), TERM + 1,
                 outputs=["maturities"])
    paid = np.asarray(result.aggregate("maturities"))
    assert int(np.argmax(paid)) == TERM
    assert (paid[:TERM] == 0.0).all()


def test_a_different_maturity_value_is_paid_on_survival_only():
    point = ModelPoint(id=1, age_at_entry=AGE, term_years=TERM,
                       sum_assured=SUM_ASSURED, maturity_value=50_000.0,
                       annual_premium=2000.0, init_pols=1000.0)
    model = Endowment(mp=point, assumptions=basis_assumptions(),
                      proj_len=TERM + 1)
    survivors = float(model.pols_if(TERM - 1))
    assert float(model.maturities(TERM)) < survivors * SUM_ASSURED
    assert float(model.claims(0)) > 0.0          # death benefit untouched


def test_the_template_reserve_matches_the_closed_form_it_is_built_on():
    premium = reserves.net_premium(BASIS, AGE, TERM, RATE, product="endowment",
                                   sum_assured=SUM_ASSURED)
    point = ModelPoint(id=1, age_at_entry=AGE, term_years=TERM,
                       sum_assured=SUM_ASSURED, annual_premium=premium,
                       init_pols=1000.0)
    model = Endowment(mp=point, assumptions=basis_assumptions(),
                      proj_len=TERM + 1)
    direct = reserves.prospective_reserve(
        BASIS, AGE, TERM, RATE, premium=premium, product="endowment",
        sum_assured=SUM_ASSURED)
    assert model.reserve_series(BASIS, RATE) == pytest.approx(direct)
    assert model.reserve_held(BASIS, RATE)[0] == pytest.approx(0.0, abs=1e-6)


def test_the_reserve_basis_is_required_and_not_the_projections():
    """An office projects on its best estimate and reserves on something
    more prudent. Defaulting to the projection's own basis would make the
    reserve a restatement of the projection rather than a check on it.
    """
    point = ModelPoint(id=1, age_at_entry=AGE, term_years=TERM,
                       sum_assured=SUM_ASSURED, annual_premium=2000.0,
                       init_pols=1000.0)
    model = Endowment(mp=point, assumptions=basis_assumptions(),
                      proj_len=TERM + 1)
    with pytest.raises(TypeError):
        model.reserve_series()
    prudent = MortalityTable({age: min(q * 1.25, 1.0)
                              for age, q in
                              ((a, min(0.0004 * 1.09 ** (a - 30), 1.0))
                               for a in range(0, 121))})
    assert (model.reserve_series(prudent, 0.02)[5]
            > model.reserve_series(BASIS, RATE)[5])


# --- whole life ----------------------------------------------------------


def test_a_whole_life_reserve_climbs_towards_the_sum_assured():
    """Death is certain, so the reserve must approach what will be paid.

    It climbs monotonically to **94% of the sum assured at age 116** and
    then turns back down — which is a property of the *horizon* and not of
    the product. Running a whole life to a finite table makes the last few
    years a shortening term assurance, and a shortening term is worth less.
    A genuine whole life has no such tail; the projection does.
    """
    term = 120 - AGE
    premium = reserves.net_premium(BASIS, AGE, term, RATE,
                                   sum_assured=SUM_ASSURED)
    reserve = reserves.prospective_reserve(BASIS, AGE, term, RATE,
                                           premium=premium,
                                           sum_assured=SUM_ASSURED)
    peak = int(np.argmax(reserve))
    assert reserve[0] == pytest.approx(0.0, abs=1e-9)
    assert (np.diff(reserve[:peak + 1]) > 0.0).all()
    assert AGE + peak == 116
    assert reserve[peak] == pytest.approx(0.94 * SUM_ASSURED, rel=0.01)


def test_a_limited_payment_contract_charges_more_for_the_same_benefit():
    full = reserves.net_premium(BASIS, AGE, 60, RATE, sum_assured=SUM_ASSURED)
    limited = reserves.net_premium(BASIS, AGE, 60, RATE,
                                   sum_assured=SUM_ASSURED, premium_term=20)
    # Not the 3x a third of the payments might suggest: the premiums that
    # are dropped are the late, heavily discounted, heavily decremented
    # ones, so the ratio is the annuity factors' and comes to 1.65.
    assert limited / full == pytest.approx(1.648, abs=0.01)
    reserve = reserves.prospective_reserve(
        BASIS, AGE, 60, RATE, premium=limited, sum_assured=SUM_ASSURED,
        premium_term=20)
    assert reserve[0] == pytest.approx(0.0, abs=1e-9)
    residual = reserves.reserve_recursion_residual(
        BASIS, AGE, 60, RATE, reserve=reserve, premium=limited,
        sum_assured=SUM_ASSURED, premium_term=20)
    assert np.abs(residual).max() < 1e-8


def test_a_premium_term_of_nothing_is_refused():
    with pytest.raises(ValueError, match="pays nothing"):
        reserves.net_premium(BASIS, AGE, TERM, RATE, premium_term=0)


def test_an_unknown_product_is_refused():
    with pytest.raises(ValueError, match="product must be one of"):
        reserves.benefit_value(BASIS, AGE, TERM, RATE, product="annuity")


# --- the finding: what the net premium reserve leaves out ---------------


ACQUISITION, RENEWAL, CLAIM_COST = 900.0, 45.0, 300.0


def test_the_net_premium_reserve_is_nil_at_issue_and_the_gross_one_is_not():
    """The new business strain, in one comparison.

    The net premium is solved to fund the benefits, so it funds nothing
    else — and a policy sold with acquisition costs has spent real money by
    the end of its first year that no part of that premium was ever going
    to recover. The net premium reserve reports nil; the gross premium
    reserve reports the hole.
    """
    net = reserves.net_premium(BASIS, AGE, TERM, RATE, product="endowment",
                               sum_assured=SUM_ASSURED)
    office = net * 1.10
    net_reserve = reserves.prospective_reserve(
        BASIS, AGE, TERM, RATE, premium=net, product="endowment",
        sum_assured=SUM_ASSURED)
    gross_reserve = reserves.gross_premium_reserve(
        BASIS, AGE, TERM, RATE, premium=office, product="endowment",
        sum_assured=SUM_ASSURED, initial_expense=ACQUISITION,
        renewal_expense=RENEWAL, claim_expense=CLAIM_COST)

    assert net_reserve[0] == pytest.approx(0.0, abs=1e-9)
    # The gross basis capitalises the loading net of the acquisition cost,
    # so it opens *below* zero; the net basis declines to recognise either
    # and opens at exactly nil while the money has already gone out.
    assert gross_reserve[0] < -3000.0
    assert net_reserve[0] - gross_reserve[0] > ACQUISITION


def test_a_bigger_loading_capitalises_more_future_profit():
    """And the sign runs the other way from the first guess.

    A gross premium reserve is benefits and expenses *less* the office
    premium, so charging more makes it **more negative** — the valuation
    recognises the extra profit as an asset immediately. Measured: −737 at
    a 5% loading against −17,828 at 40%.

    That is precisely what a net premium basis refuses to do, and why it
    holds nil at issue while the office is already out of pocket for the
    acquisition cost. The strain is the gap between the two, not the sign
    of either.
    """
    net = reserves.net_premium(BASIS, AGE, TERM, RATE, product="endowment",
                               sum_assured=SUM_ASSURED)
    recognised = [
        reserves.gross_premium_reserve(
            BASIS, AGE, TERM, RATE, premium=net * loading, product="endowment",
            sum_assured=SUM_ASSURED, initial_expense=ACQUISITION,
            renewal_expense=RENEWAL, claim_expense=CLAIM_COST)[0]
        for loading in (1.05, 1.10, 1.20, 1.40)
    ]
    assert recognised == sorted(recognised, reverse=True)
    assert recognised[0] == pytest.approx(-737.0, abs=5.0)
    assert recognised[-1] == pytest.approx(-17828.0, abs=20.0)


# --- the modified bases --------------------------------------------------


def test_zillmerising_takes_the_whole_allowance_at_issue_and_none_at_maturity():
    premium, kw = priced("endowment")
    reserve = reserves.prospective_reserve(BASIS, AGE, TERM, RATE,
                                           premium=premium, **kw)
    annuity = reserves.annuity_due(BASIS, AGE, TERM, RATE)
    zillmered = reserves.zillmerised_reserve(reserve, zillmer=ACQUISITION,
                                             annuity=annuity, term=TERM)
    assert zillmered[0] == pytest.approx(-ACQUISITION, rel=1e-12)
    assert zillmered[-1] == pytest.approx(reserve[-1], rel=1e-12)
    assert (zillmered <= reserve + 1e-9).all()


def test_a_negative_zillmer_allowance_is_refused():
    annuity = reserves.annuity_due(BASIS, AGE, TERM, RATE)
    with pytest.raises(ValueError, match="negative"):
        reserves.zillmerised_reserve(np.zeros(TERM + 1), zillmer=-1.0,
                                     annuity=annuity, term=TERM)


def test_full_preliminary_term_is_exactly_zero_for_the_first_year():
    """The limiting modified basis, and "exactly" is the point — the
    first-year reserve is zero by construction, not by being small."""
    fpt = reserves.full_preliminary_term(BASIS, AGE, TERM, RATE,
                                         product="endowment",
                                         sum_assured=SUM_ASSURED)
    assert fpt[0] == 0.0
    assert fpt[1] == pytest.approx(0.0, abs=1e-9)
    assert fpt[-1] == pytest.approx(SUM_ASSURED, rel=1e-9)


def test_the_three_bases_rank_the_way_the_capital_they_need_does():
    """Net premium holds the most, full preliminary term the least, and
    Zillmer sits between — which is the whole reason the modified bases
    exist and the order a regulator cares about."""
    premium, kw = priced("endowment")
    net = reserves.prospective_reserve(BASIS, AGE, TERM, RATE,
                                       premium=premium, **kw)
    annuity = reserves.annuity_due(BASIS, AGE, TERM, RATE)
    zillmered = reserves.zillmerised_reserve(net, zillmer=ACQUISITION,
                                             annuity=annuity, term=TERM)
    fpt = reserves.full_preliminary_term(BASIS, AGE, TERM, RATE,
                                         product="endowment",
                                         sum_assured=SUM_ASSURED)
    for t in range(1, TERM):
        assert fpt[t] <= zillmered[t] + 1e-9 <= net[t] + 1e-8
    # And all three converge on the same maturity value.
    assert fpt[-1] == pytest.approx(net[-1], rel=1e-9)
    assert zillmered[-1] == pytest.approx(net[-1], rel=1e-9)


def test_full_preliminary_term_needs_a_second_year_to_defer_into():
    with pytest.raises(ValueError, match="at least two years"):
        reserves.full_preliminary_term(BASIS, AGE, 1, RATE)
