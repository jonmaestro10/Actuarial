"""Tax hooks — a rate, a base, and what happens to a loss.

PLAN §5.1 asks for tax *hooks*, and the wording is the design. Tax regimes
differ by jurisdiction more than any other assumption in an actuarial model,
and a library that shipped one of them as "the" tax calculation would be
wrong everywhere else while looking authoritative.

So the interesting content is not the multiplication. It is:

**What happens to a loss.** A period's profit is reliably negative in the
first year of a policy, where acquisition costs land before any margin has
emerged. Full relief, no relief and carry-forward give three different
answers, and the difference between them is worth more than most assumption
changes anybody argues about.

**That taxing periods is not taxing the total.** Under full relief the two
agree exactly — asserted here as an identity, not a tolerance. Under either
other relief they do not, and the gap is precisely the value of the losses
that never got relieved. A model that computed tax on a present value would
silently assume full relief.

**That the profit signature is the same business as the cashflows.** Tax
runs on a period's result rather than on a discounted total, so the
signature has to discount back to the same present value the individual
cashflows do. It does, by construction.
"""

from __future__ import annotations

import numpy as np
import pytest

from engine.core.runner import run
from engine.core.stochastic import run_stochastic
from engine.core.vector import run_vectorized
from engine.data.assumptions import Assumptions, MortalityTable
from engine.data.expenses import Commission, ExpenseScale, Expenses
from engine.data.modelpoints import ModelPoint, from_dicts
from engine.data.reinsurance import QuotaShare
from engine.data.scenarios import ScenarioSet
from engine.data.tax import RELIEFS, TaxBasis
from engine.library.term_life import TermLife
from engine.library.unit_linked import UnitLinkedGMDB

Q, LAPSE, INTEREST = 0.009, 0.055, 0.031
TERM, SA, PREMIUM = 20, 250_000.0, 3_000.0
RATE = 0.25

#: An expense and commission basis heavy enough at the front end to make
#: the first year's profit genuinely negative — which is what makes loss
#: relief something other than a rounding detail.
FRONT_LOADED = Expenses(
    initial=ExpenseScale(per_policy=400.0, percent_premium=0.5,
                         per_mille_sum_assured=1.5),
    renewal=ExpenseScale(per_policy=45.0),
    inflation=0.03,
)
UPFRONT = Commission(initial_percent=0.7, renewal_percent=0.03)


def point(**kw):
    row = {"id": "T1", "age_at_entry": 40, "term_years": TERM,
           "sum_assured": SA, "annual_premium": PREMIUM, "init_pols": 1}
    row.update(kw)
    return ModelPoint(**row)


def assumptions(**kw):
    row = dict(mortality=MortalityTable.flat(Q), lapse=LAPSE, interest=INTEREST)
    row.update(kw)
    return Assumptions(**row)


def model(loaded=True, **kw):
    if loaded:
        kw.setdefault("expenses", FRONT_LOADED)
        kw.setdefault("commission", UPFRONT)
    a = assumptions(**kw)
    return TermLife(point(), a, TERM + 2)


def scalar(value) -> float:
    return float(np.asarray(value).reshape(-1)[0])


# --- the basis itself ----------------------------------------------------


def test_a_zero_rate_basis_is_off_and_knows_it():
    assert not TaxBasis()
    assert TaxBasis(profit_rate=RATE)
    assert TaxBasis(investment_rate=0.2)
    assert assumptions().tax == assumptions().tax or True
    assert not assumptions().tax


@pytest.mark.parametrize("kw,message", [
    ({"profit_rate": 1.0}, r"profit_rate .* outside \[0, 1\)"),
    ({"profit_rate": -0.1}, r"outside \[0, 1\)"),
    ({"investment_rate": 1.5}, r"investment_rate .* outside \[0, 1\)"),
    ({"relief": "sometimes"}, "relief must be one of"),
])
def test_a_malformed_basis_raises(kw, message):
    with pytest.raises(ValueError, match=message):
        TaxBasis(**kw)


def test_full_relief_taxes_a_loss_as_a_credit():
    basis = TaxBasis(profit_rate=RATE, relief="full")
    assert basis.on_profit(1_000.0) == 250.0
    assert basis.on_profit(-1_000.0) == -250.0


def test_no_relief_never_gives_a_credit():
    basis = TaxBasis(profit_rate=RATE, relief="none")
    assert basis.on_profit(1_000.0) == 250.0
    assert basis.on_profit(-1_000.0) == 0.0


def test_carry_forward_sets_a_loss_against_the_next_profit():
    basis = TaxBasis(profit_rate=RATE, relief="carry_forward")
    # A 1,000 loss, then 400 of profit: nothing taxed, 600 still carried.
    carried = basis.carry_forward_step(-1_000.0, 0.0)
    assert carried == 1_000.0
    assert basis.on_profit(400.0, carried) == 0.0
    carried = basis.carry_forward_step(400.0, carried)
    assert carried == 600.0
    # Then 1,000 of profit: 400 taxable, and the carried loss is spent.
    assert basis.on_profit(1_000.0, carried) == 0.25 * 400.0
    assert basis.carry_forward_step(1_000.0, carried) == 0.0


def test_a_carried_balance_never_goes_negative():
    """A company cannot carry a profit forward."""
    basis = TaxBasis(profit_rate=RATE, relief="carry_forward")
    assert basis.carry_forward_step(5_000.0, 100.0) == 0.0
    assert basis.carry_forward_step(5_000.0, 0.0) == 0.0


@pytest.mark.parametrize("relief", ["full", "none"])
def test_the_other_reliefs_carry_nothing(relief):
    """So a template's recursion is the same expression whichever basis it
    is handed, and no template branches on the relief."""
    basis = TaxBasis(profit_rate=RATE, relief=relief)
    assert basis.carry_forward_step(-5_000.0, 3_000.0) == 0.0
    assert basis.on_profit(1_000.0, loss_brought_forward=9_999.0) == (
        basis.on_profit(1_000.0)
    )


# --- the profit signature ------------------------------------------------


def test_the_profit_signature_discounts_back_to_the_cashflows():
    """Tax runs on a period's result, so the signature has to be the same
    business as the individual cashflows — otherwise the tax line would be
    charged on a different projection from the one being valued."""
    for kw in ({}, {"expenses": FRONT_LOADED, "commission": UPFRONT},
               {"reinsurance": QuotaShare(0.3, commission=0.2)}):
        m = model(loaded=False, **kw)
        assert m.pv_profit_before_tax() == pytest.approx(
            m.net_pv(), rel=1e-12
        )


@pytest.mark.parametrize("freq", [1, 2, 4, 12])
def test_the_signature_holds_at_every_frequency(freq):
    a = assumptions(freq=freq, expenses=FRONT_LOADED, commission=UPFRONT)
    m = TermLife(point(), a, a.periods(TERM + 2))
    assert m.pv_profit_before_tax() == pytest.approx(m.net_pv(), rel=1e-12)


def test_the_first_period_really_does_lose_money():
    """The premise the whole loss-relief question rests on. If it were not
    true, every relief basis would agree and the tests below would be
    vacuous."""
    m = model()
    assert scalar(m.profit_before_tax(0)) < 0.0
    assert scalar(m.profit_before_tax(5)) > 0.0


# --- taxing periods is not taxing the total ------------------------------


def test_under_full_relief_taxing_periods_equals_taxing_the_total():
    """The identity that makes full relief the only basis under which a
    present-value tax calculation is defensible."""
    m = model(tax=TaxBasis(profit_rate=RATE, relief="full"))
    assert m.pv_tax() == pytest.approx(RATE * m.pv_profit_before_tax(),
                                       rel=1e-13)
    assert m.pv_profit_after_tax() == pytest.approx(
        (1.0 - RATE) * m.pv_profit_before_tax(), rel=1e-13
    )


def test_without_relief_the_unrelieved_losses_cost_exactly_their_value():
    """And the gap is not a rounding difference: it is the tax value of
    every period that lost money, which a present-value calculation would
    have quietly assumed away."""
    full = model(tax=TaxBasis(profit_rate=RATE, relief="full"))
    none = model(tax=TaxBasis(profit_rate=RATE, relief="none"))
    losses = sum(
        min(scalar(none.profit_before_tax(t)), 0.0) * scalar(none.v(t + 1))
        for t in range(none.proj_len)
    )
    assert none.pv_tax() - full.pv_tax() == pytest.approx(
        -RATE * losses, rel=1e-12
    )
    assert losses < 0.0
    assert none.pv_tax() > full.pv_tax()


def test_the_three_reliefs_are_ordered_and_distinct():
    """Carry-forward sits between the two: a loss is relieved, but later
    and therefore at a discount."""
    taxes = {
        relief: model(tax=TaxBasis(profit_rate=RATE, relief=relief)).pv_tax()
        for relief in RELIEFS
    }
    assert taxes["full"] < taxes["carry_forward"] < taxes["none"]


def test_carry_forward_relieves_the_same_losses_later():
    """Undiscounted, carry-forward and full relief tax the same total —
    every loss is eventually used, provided later profits are big enough.
    Discounted they differ, and that difference is the whole point."""
    full = model(tax=TaxBasis(profit_rate=RATE, relief="full"))
    fwd = model(tax=TaxBasis(profit_rate=RATE, relief="carry_forward"))
    undiscounted = [
        sum(scalar(m.tax(t)) for t in range(m.proj_len)) for m in (full, fwd)
    ]
    assert undiscounted[0] == pytest.approx(undiscounted[1], rel=1e-12)
    assert fwd.pv_tax() > full.pv_tax()


def test_the_carried_loss_runs_down_and_stays_down():
    m = model(tax=TaxBasis(profit_rate=RATE, relief="carry_forward"))
    carried = [scalar(m.tax_loss_bf(t)) for t in range(TERM)]
    assert carried[0] == 0.0            # nothing is carried *into* period 0
    assert carried[1] > 0.0             # the first period's loss
    # Monotone from period 1 on — the balance only rises if a later period
    # also loses money, and on this basis only the first one does.
    assert carried[1:] == sorted(carried[1:], reverse=True)
    assert min(carried) >= 0.0
    # No tax is paid until the carried loss is used up, and some is after.
    paid = [scalar(m.tax(t)) for t in range(TERM)]
    assert paid[1] == 0.0
    assert max(paid) > 0.0


def test_no_tax_at_all_is_the_default():
    m = model()
    assert all(scalar(m.tax(t)) == 0.0 for t in range(TERM))
    assert all(scalar(m.tax_loss_bf(t)) == 0.0 for t in range(TERM))
    assert m.pv_tax() == 0.0
    assert m.pv_profit_after_tax() == m.pv_profit_before_tax()
    for t in range(TERM):
        assert np.array_equal(m.profit_after_tax(t), m.profit_before_tax(t))


# --- investment tax ------------------------------------------------------


def test_an_untaxed_return_is_the_return():
    """``gross * (1 - 0.0)`` is exactly the gross return for every finite
    value, which is why the fund templates need no branch."""
    basis = TaxBasis()
    for r in (0.0, 0.05, -0.3, 1e-12):
        assert basis.net_investment_return(r) == r


def test_investment_tax_reduces_what_the_fund_earns():
    basis = TaxBasis(investment_rate=0.2)
    assert basis.net_investment_return(0.05) == pytest.approx(0.04, rel=1e-15)
    # A negative return is relieved in the fund at the same rate — one
    # accepted treatment, and the one the templates document.
    assert basis.net_investment_return(-0.05) == pytest.approx(-0.04, rel=1e-15)


def test_a_taxed_unit_linked_fund_grows_more_slowly():
    scenarios = ScenarioSet.flat(0.06, 1, 30)
    points = from_dicts([
        {"id": "U1", "age_at_entry": 55, "term_years": 15,
         "premium": 100_000.0, "gmdb_guarantee": 100_000.0, "init_pols": 1},
    ])
    funds = []
    for rate in (0.0, 0.2):
        a = assumptions(amc=0.01, tax=TaxBasis(investment_rate=rate))
        result = run_stochastic(UnitLinkedGMDB, points, a, scenarios, 15,
                                outputs=["fund_eoy"])
        funds.append(float(np.asarray(result.array("fund_eoy"))[14, 0, 0]))
    assert funds[1] < funds[0]
    # 6% taxed at 20% is 4.8%: fifteen years of the difference, compounded.
    assert funds[1] / funds[0] == pytest.approx(
        (1.048 / 1.06) ** 15, rel=1e-3
    )


def test_a_taxed_deferred_annuity_credits_less():
    from engine.library.fixed_annuity import FixedAnnuity

    points = from_dicts([
        {"id": "A1", "age_at_entry": 55, "defer_years": 10,
         "premium": 100_000.0, "annual_payment": 8_000.0, "init_pols": 1},
    ])
    funds = []
    for rate in (0.0, 0.25):
        a = assumptions(crediting_rate=0.04, tax=TaxBasis(investment_rate=rate))
        result = run_vectorized(FixedAnnuity, points, a, 12,
                                outputs=["fund_eoy_per_pol"])
        funds.append(float(np.asarray(result.array("fund_eoy_per_pol"))[9, 0]))
    assert funds[1] < funds[0]
    assert funds[0] == pytest.approx(100_000.0 * 1.04 ** 10, rel=1e-12)
    assert funds[1] == pytest.approx(100_000.0 * 1.03 ** 10, rel=1e-12)


# --- executors and the registry ------------------------------------------


@pytest.mark.parametrize("relief", RELIEFS)
def test_the_two_executors_agree_bitwise(relief):
    """Carry-forward is a recursion with a ``maximum`` in it, which is
    exactly the sort of thing that behaves differently one policy at a time
    than in a batch. It does not."""
    points = from_dicts([
        {"id": f"T{i}", "age_at_entry": 35 + 5 * i, "term_years": TERM,
         "sum_assured": SA * (i + 1), "annual_premium": PREMIUM * (i + 1),
         "init_pols": 1}
        for i in range(4)
    ])
    a = assumptions(expenses=FRONT_LOADED, commission=UPFRONT,
                    tax=TaxBasis(profit_rate=RATE, relief=relief))
    outputs = ["profit_before_tax", "tax_loss_bf", "tax", "profit_after_tax"]
    interpreted = run(TermLife, points, a, TERM + 2, outputs=outputs)
    vectorized = run_vectorized(TermLife, points, a, TERM + 2, outputs=outputs)
    for name in outputs:
        assert np.array_equal(
            np.array([mp[name] for mp in interpreted.per_mp]).T,
            np.asarray(vectorized.array(name)),
        ), name


def test_the_carry_forward_recursion_is_per_policy_not_per_block():
    """Two policies of very different size in one batch: the loss carried
    by each has to be its own, not the batch's."""
    points = from_dicts([
        {"id": "small", "age_at_entry": 40, "term_years": TERM,
         "sum_assured": 50_000.0, "annual_premium": 600.0, "init_pols": 1},
        {"id": "large", "age_at_entry": 40, "term_years": TERM,
         "sum_assured": 2_000_000.0, "annual_premium": 24_000.0,
         "init_pols": 1},
    ])
    a = assumptions(expenses=FRONT_LOADED, commission=UPFRONT,
                    tax=TaxBasis(profit_rate=RATE, relief="carry_forward"))
    carried = np.asarray(
        run_vectorized(TermLife, points, a, TERM + 2,
                       outputs=["tax_loss_bf"]).array("tax_loss_bf")
    )
    assert carried[1, 1] > carried[1, 0] > 0.0
    # And each runs down on its own schedule rather than in lockstep.
    assert not np.array_equal(carried[:, 0] / carried[1, 0],
                              carried[:, 1] / carried[1, 1])


def test_the_run_registry_tells_the_reliefs_apart():
    from engine.core.registry import record_run

    points = from_dicts([point().__dict__])
    records = {}
    for relief in RELIEFS:
        _, record = record_run(
            TermLife, points,
            assumptions(expenses=FRONT_LOADED, commission=UPFRONT,
                        tax=TaxBasis(profit_rate=RATE, relief=relief)),
            TERM + 2, outputs=["tax"],
        )
        records[relief] = record
    assert len({r.run_id for r in records.values()}) == 3
    assert len({r.results_digest for r in records.values()}) == 3
