"""With-profits: asset shares, bonuses, smoothing and the estate.

The first template to exercise ``@pool`` for what RFC-001 introduced it for
— a with-profits bonus and an asset share — so several of these are as much
about the DSL as about the product.
"""

from __future__ import annotations

import numpy as np
import pytest

from engine.core.vector import run_vectorized
from engine.data.assumptions import Assumptions, MortalityTable
from engine.data.expenses import ExpenseScale, Expenses
from engine.data.modelpoints import ModelPoint
from engine.library import reserves
from engine.library.with_profits import (
    BONUS_BASES, WithProfitsEndowment, reversionary_bonus_cost,
)

BASIS = MortalityTable(
    {age: min(0.0004 * 1.09 ** (age - 30), 1.0) for age in range(0, 121)}
)
RATE, TERM = 0.05, 25


def assumptions(lapse=0.03, expense=40.0):
    return Assumptions(mortality=BASIS, lapse=lapse, interest=RATE,
                       expenses=Expenses(
                           renewal=ExpenseScale(per_policy=expense)))


def block():
    """Two model points, so a pooled variable has something to pool."""
    return [
        ModelPoint(id=1, age_at_entry=40, term_years=TERM,
                   sum_assured=100_000.0, annual_premium=3800.0,
                   init_pols=1000.0),
        ModelPoint(id=2, age_at_entry=55, term_years=TERM,
                   sum_assured=50_000.0, annual_premium=2600.0,
                   init_pols=400.0),
    ]


OUTPUTS = ["asset_share", "guaranteed_benefit", "maturity_payout",
           "terminal_bonus", "mortality_profit_rate", "aggregate_asset_share",
           "smoothing_cost", "maturities", "death_claims", "pols_if"]


def projected(model_cls=WithProfitsEndowment, points=None, **kw):
    return run_vectorized(model_cls, points or block(),
                          assumptions(**kw), TERM + 1, outputs=OUTPUTS)


# --- the pooled variable -------------------------------------------------


def test_the_mortality_profit_is_the_same_for_every_policy_in_the_block():
    """Which is what ``@pool`` means, and why the term needs one.

    When a policyholder dies the fund pays the guarantee and releases that
    life's asset share; the difference falls on everybody else. It is a
    transfer *between* policies, and a per-policy formula cannot see one.
    """
    result = projected()
    rate = result.array("mortality_profit_rate")
    assert rate.shape[1] == 2
    assert (rate[:, 0] == rate[:, 1]).all()


def test_the_pooled_template_is_recognised_as_pooled():
    assert "mortality_profit_rate" in WithProfitsEndowment.pooled_names()
    assert "aggregate_asset_share" in WithProfitsEndowment.pooled_names()
    assert "asset_share" not in WithProfitsEndowment.pooled_names()


def test_deaths_cost_the_fund_early_and_pay_it_late():
    """The sign of the mortality profit tracks one crossing: whether a
    policy has yet earned more than it was promised.

    Early on the guarantee is far above the asset share, so every death
    costs the survivors. Late on the asset share has overtaken it and the
    deaths release money instead. Nothing in the code knows about the
    crossing; it falls out of the arithmetic.
    """
    result = projected()
    rate = result.array("mortality_profit_rate")[:, 0]
    assert rate[5] < 0.0
    assert rate[TERM - 1] > 0.0
    assert rate.min() < -100.0


def test_the_aggregate_asset_share_is_the_blocks_and_not_a_policys():
    result = projected()
    aggregate = result.array("aggregate_asset_share")[:, 0]
    per_policy = result.array("asset_share")
    in_force = result.array("pols_if")
    for t in (5, 10, 20):
        assert aggregate[t] == pytest.approx(
            float((per_policy[t] * in_force[t]).sum()), rel=1e-12)
    assert aggregate[0] == 0.0


# --- the guarantee -------------------------------------------------------


def test_a_reversionary_bonus_can_never_be_taken_back():
    """What makes it a guarantee rather than a hope. The series is monotone
    by construction — the formula adds and never subtracts — rather than by
    an assumption about the bonus rate."""
    guaranteed = projected().array("guaranteed_benefit")
    assert (np.diff(guaranteed[:TERM, 0]) > 0.0).all()
    assert guaranteed[0, 0] == 100_000.0


def test_compound_and_simple_bonuses_part_company_over_a_long_contract():
    class Simple(WithProfitsEndowment):
        bonus_basis = "simple"

    compound = projected().array("guaranteed_benefit")[TERM, 0]
    simple = projected(Simple).array("guaranteed_benefit")[TERM, 0]
    assert simple == pytest.approx(100_000.0 * (1 + 0.02 * TERM))
    assert compound == pytest.approx(100_000.0 * 1.02 ** TERM, rel=1e-9)
    assert compound > simple


def test_an_unknown_bonus_basis_is_refused_at_class_definition():
    with pytest.raises(ValueError, match="bonus basis must be one of"):
        class Broken(WithProfitsEndowment):
            bonus_basis = "geometric-ish"


def test_the_two_bonus_bases_are_the_only_ones():
    assert BONUS_BASES == ("simple", "compound")


# --- the finding: what a bonus declaration costs -------------------------


def test_a_bonus_declaration_is_cheapest_at_issue_and_dearest_at_maturity():
    """And the direction runs the opposite way from the first guess.

    The cost of declaring 2% is the present value of raising *every* future
    payment by 2% of the sum assured — and a present value of something
    payable in twenty-five years is a fraction of its face. So the same
    declaration costs 31% of its nominal amount at issue and 95% near
    maturity: three times as much for the identical announcement.
    """
    nominal = 0.02 * 100_000.0
    costs = [
        reversionary_bonus_cost(BASIS, 40, TERM, RATE, sum_assured=100_000.0,
                                bonus_rate=0.02, duration=d)
        for d in (0, 5, 10, 20, 24)
    ]
    assert costs == sorted(costs)
    assert costs[0] / nominal == pytest.approx(0.31, abs=0.02)
    assert costs[-1] / nominal == pytest.approx(0.95, abs=0.02)
    assert costs[-1] > 3 * costs[0]
    assert all(cost < nominal for cost in costs)


def test_a_declaration_past_the_end_of_the_contract_costs_nothing():
    assert reversionary_bonus_cost(BASIS, 40, TERM, RATE,
                                   sum_assured=100_000.0, bonus_rate=0.02,
                                   duration=TERM) == 0.0


# --- the asset share -----------------------------------------------------


def test_the_asset_share_survives_to_the_maturity_it_is_measured_against():
    """Regression on a real bug. Masking the asset share with ``in_term``
    zeroed it at ``t == term`` — precisely the date the maturity payout is
    struck against — so every policy was paid its guarantee and **no
    terminal bonus at all**, silently. The same lesson ``IncomeProtection``
    records as "the chain outlives the contract".
    """
    result = projected()
    assert result.array("asset_share")[TERM, 0] > 0.0
    assert result.array("terminal_bonus")[TERM, 0] > 0.0


def test_the_asset_share_accumulates_at_what_the_fund_earned():
    """Longhand for the first year, and it is a *retrospective* quantity —
    it accumulates what happened, where RFC-018's reserve values what is
    still to come on a basis fixed in advance."""
    points = [ModelPoint(id=1, age_at_entry=40, term_years=TERM,
                         sum_assured=100_000.0, annual_premium=3800.0,
                         init_pols=1000.0)]
    result = projected(points=points, lapse=0.0)
    opening = 0.0 + 3800.0 - 40.0
    grown = opening * (1.0 + RATE)
    at_risk = 100_000.0 - 0.0
    cover = float(BASIS.q_at(40)) * at_risk
    profit = float(result.array("mortality_profit_rate")[0, 0])
    assert result.array("asset_share")[1, 0] == pytest.approx(
        grown - cover + profit, rel=1e-12)


def test_the_asset_share_and_the_prospective_reserve_answer_different_questions():
    """Set side by side because they are so often confused. One accumulates
    what a policy has earned at what the fund made; the other values what
    is still owed on a basis chosen in advance. They are not close, and an
    office that pays one when it means the other pays the wrong amount."""
    result = projected()
    share = result.array("asset_share")[10, 0]
    premium = reserves.net_premium(BASIS, 40, TERM, RATE, product="endowment",
                                   sum_assured=100_000.0)
    reserve = reserves.prospective_reserve(
        BASIS, 40, TERM, RATE, premium=premium, product="endowment",
        sum_assured=100_000.0)[10]
    assert share > 0.0 and reserve > 0.0
    assert abs(share / reserve - 1.0) > 0.10


def test_a_policy_that_has_out_earned_its_guarantee_pays_no_rebate():
    """``cost_of_cover`` is floored at zero: being over-funded is not a
    reason for the fund to owe the policy money for the cover."""
    result = projected()
    share = result.array("asset_share")[:, 0]
    guaranteed = result.array("guaranteed_benefit")[:, 0]
    crossed = np.argmax(share > guaranteed)
    assert crossed > 0                       # it does cross, late on
    assert (share[crossed:TERM + 1] > 0.0).all()


def test_in_force_business_starts_from_the_asset_share_it_brought():
    points = [ModelPoint(id=1, age_at_entry=50, term_years=15,
                         sum_assured=100_000.0, annual_premium=3800.0,
                         init_pols=1000.0, initial_asset_share=42_000.0)]
    result = run_vectorized(WithProfitsEndowment, points, assumptions(), 16,
                            outputs=["asset_share"])
    assert result.array("asset_share")[0, 0] == 42_000.0


# --- payouts and smoothing ----------------------------------------------


def test_a_payout_never_falls_below_the_guarantee():
    """The promise, and it holds at every smoothing level including none."""
    for smoothing in (0.0, 0.5, 0.75, 1.0):
        variant = type("Smoothed", (WithProfitsEndowment,),
                       {"smoothing": smoothing})
        result = projected(variant)
        payout = result.array("maturity_payout")[TERM]
        guaranteed = result.array("guaranteed_benefit")[TERM]
        assert (payout >= guaranteed - 1e-9).all()


def test_full_smoothing_pays_the_asset_share_and_none_pays_the_guarantee():
    class Unsmoothed(WithProfitsEndowment):
        smoothing = 1.0

    class Minimal(WithProfitsEndowment):
        smoothing = 0.0

    full = projected(Unsmoothed)
    none = projected(Minimal)
    assert full.array("maturity_payout")[TERM, 0] == pytest.approx(
        full.array("asset_share")[TERM, 0], rel=1e-12)
    assert none.array("maturity_payout")[TERM, 0] == pytest.approx(
        none.array("guaranteed_benefit")[TERM, 0], rel=1e-12)


def test_smoothing_below_one_leaves_money_in_the_estate():
    """The fund keeps the part of the excess it does not hand over, and
    ``smoothing_cost`` reports it — negative where the estate gained."""
    result = projected()
    cost = result.array("smoothing_cost")[TERM, 0]
    assert cost < 0.0
    full = projected(type("Full", (WithProfitsEndowment,),
                          {"smoothing": 1.0}))
    assert full.array("smoothing_cost")[TERM, 0] == pytest.approx(0.0, abs=1e-6)


def test_a_death_claim_gives_up_the_discretionary_upside():
    """Deaths are paid the guarantee and no terminal bonus — the ordinary
    convention, and part of why the fund can afford to smooth maturities at
    all."""
    result = run_vectorized(WithProfitsEndowment, block(), assumptions(),
                            TERM + 1,
                            outputs=OUTPUTS + ["pols_death", "asset_share"])
    guaranteed = result.array("guaranteed_benefit")
    deaths = result.array("pols_death")
    # Exactly the guarantee, with nothing added, at every duration.
    for t in (1, 10, TERM - 1):
        assert result.array("death_claims")[t] == pytest.approx(
            deaths[t] * guaranteed[t], rel=1e-12)
    # And the upside given up is real: a survivor to maturity is paid more
    # than the guarantee, where a death at any point is not.
    assert (result.array("maturity_payout")[TERM] > guaranteed[TERM]).all()


def test_the_maturity_falls_at_the_end_of_the_term_and_nowhere_else():
    matured = projected().array("maturities")[:, 0]
    assert int(np.argmax(matured)) == TERM
    assert (matured[:TERM] == 0.0).all()


# --- a bonus rule is a management action --------------------------------


def test_a_subclass_can_make_the_bonus_a_rule_rather_than_a_number():
    """``declared_bonus`` is a ``@var`` precisely so that a real bonus
    rule — smoothed towards the fund's return, cut when the estate thins —
    is an override and not a rewrite. Everything downstream follows without
    change."""

    from engine.core.model import var

    class Tapering(WithProfitsEndowment):
        @var
        def declared_bonus(self, t):
            # Half the rate once the contract is past half its term.
            late = (t >= self.assumptions.periods(TERM // 2)) * 1.0
            return self.bonus_rate * (1.0 - 0.5 * late) * self.in_term(t)

    flat = projected().array("guaranteed_benefit")[TERM, 0]
    tapered = projected(Tapering).array("guaranteed_benefit")[TERM, 0]
    assert tapered < flat
    assert tapered > 100_000.0
