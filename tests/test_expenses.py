"""Expenses, expense inflation and commission.

PLAN §5.1 lists these among the Layer 0 primitives. The engine had one of
them — a flat annual amount per policy, never indexed — which is enough for
a closed-form golden test and not enough to price anything.

The three claims this file makes:

**Nothing moved.** A bare ``expense_per_policy`` is the renewal per-policy
loading of a basis with nothing else in it, so every projection that used
the scalar form keeps its exact numbers. Asserted with ``==`` on floats.

**Each basis is what it says.** Per policy, percent of premium and per
mille sum assured are quoted annually and divided once, so a monthly
projection collects the same annual loading; inflation indexes on the
calendar rather than on anniversaries; the initial/renewal commission
boundary is a policy-duration boundary and survives a change of frequency.

**Clawback is off unless asked for.** It changes the economics of an early
lapse, which is exactly why it cannot be on by default.
"""

from __future__ import annotations

import numpy as np
import pytest

from engine.core.runner import run
from engine.core.vector import run_vectorized
from engine.data.assumptions import Assumptions, MortalityTable
from engine.data.expenses import Commission, ExpenseScale, Expenses
from engine.data.modelpoints import ModelPoint, from_dicts
from engine.library.term_life import TermLife

Q, LAPSE, INTEREST = 0.009, 0.055, 0.031
TERM, SA, PREMIUM = 20, 250_000.0, 1_200.0


def point(**kw):
    row = {"id": "T1", "age_at_entry": 40, "term_years": TERM,
           "sum_assured": SA, "annual_premium": PREMIUM, "init_pols": 1}
    row.update(kw)
    return ModelPoint(**row)


def assumptions(freq=1, **kw):
    row = dict(mortality=MortalityTable.flat(Q), lapse=LAPSE,
               interest=INTEREST, freq=freq)
    row.update(kw)
    return Assumptions(**row)


def model(freq=1, mp=None, **kw):
    a = assumptions(freq=freq, **kw)
    return TermLife(mp or point(), a, a.periods(TERM + 2))


def scalar(value) -> float:
    return float(np.asarray(value).reshape(-1)[0])


# --- the scale itself ----------------------------------------------------


def test_the_three_bases_add_up():
    scale = ExpenseScale(per_policy=45.0, percent_premium=0.04,
                         per_mille_sum_assured=0.6)
    assert scale.amount(premium=PREMIUM, sum_assured=SA) == (
        45.0 + 0.04 * PREMIUM + 0.6 * SA / 1000.0
    )


def test_per_mille_is_per_thousand_and_the_division_lives_in_one_place():
    """Quoted per thousand of sum assured, which is how underwriting costs
    are stated. Getting the factor of 1,000 wrong is a three-orders-of-
    magnitude error that looks like a plausible expense either way."""
    scale = ExpenseScale(per_mille_sum_assured=2.0)
    assert scale.amount(sum_assured=250_000.0) == 500.0


def test_an_empty_scale_costs_nothing_and_knows_it():
    assert not ExpenseScale()
    assert ExpenseScale().amount(premium=PREMIUM, sum_assured=SA) == 0.0
    assert ExpenseScale(per_policy=1.0)
    assert not Expenses()
    assert Expenses(renewal=ExpenseScale(per_policy=1.0))


@pytest.mark.parametrize("field", ["per_policy", "percent_premium",
                                   "per_mille_sum_assured"])
def test_a_negative_loading_raises(field):
    with pytest.raises(ValueError, match="negative"):
        ExpenseScale(**{field: -1.0})


# --- nothing moved -------------------------------------------------------


def test_the_scalar_form_is_the_renewal_per_policy_loading():
    a = assumptions(expense_per_policy=60.0)
    assert a.expenses.renewal == ExpenseScale(per_policy=60.0)
    assert not a.expenses.initial
    assert not a.expenses.claim
    assert a.expenses.inflation == 0.0
    assert not a.commission


@pytest.mark.parametrize("freq", [1, 4, 12])
def test_the_scalar_form_keeps_its_exact_numbers(freq):
    """Written longhand, so the assertion has something to disagree with."""
    m = model(freq=freq, expense_per_policy=62.5)
    a = m.assumptions
    for t in range(TERM * freq):
        assert np.array_equal(
            m.expenses(t),
            m.pols_if(t) * a.per_period(62.5) * m.in_term(t),
        )
        assert scalar(m.initial_expenses(t)) == 0.0
        assert scalar(m.claim_expenses(t)) == 0.0
        assert scalar(m.commission(t)) == 0.0
        assert scalar(m.commission_clawback(t)) == 0.0


def test_the_added_pv_lines_are_zero_without_a_basis():
    m = model(expense_per_policy=62.5)
    assert m.pv_initial_expenses() == 0.0
    assert m.pv_claim_expenses() == 0.0
    assert m.pv_commission() == 0.0
    assert m.net_pv() == m.pv_premiums() - m.pv_claims() - m.pv_expenses()


def test_supplying_both_forms_raises_rather_than_picking_one():
    with pytest.raises(ValueError, match="conflicts with the `expenses` basis"):
        assumptions(expense_per_policy=60.0,
                    expenses=Expenses(renewal=ExpenseScale(per_policy=60.0)))


def test_an_unindexed_basis_cannot_move_a_number():
    """``1.0 ** years`` is exactly 1.0 for every finite exponent, which is
    why the templates need no branch on whether inflation was supplied."""
    a = assumptions(freq=12, expenses=Expenses(
        renewal=ExpenseScale(per_policy=60.0)
    ))
    for t in (0, 1, 7, 143, 240):
        assert a.inflation_index(t) == 1.0


# --- the loadings through a projection -----------------------------------


def test_initial_expenses_fall_once_at_inception():
    basis = Expenses(initial=ExpenseScale(per_policy=300.0,
                                          percent_premium=0.5,
                                          per_mille_sum_assured=1.5))
    m = model(expenses=basis)
    expected = 300.0 + 0.5 * PREMIUM + 1.5 * SA / 1000.0
    assert scalar(m.initial_expenses(0)) == expected
    for t in range(1, TERM):
        assert scalar(m.initial_expenses(t)) == 0.0
    assert m.pv_initial_expenses() == expected     # paid at t = 0, undiscounted


@pytest.mark.parametrize("freq", [1, 2, 4, 12])
def test_the_same_renewal_loading_is_charged_over_a_year(freq):
    """Quoted annually and divided once, so a monthly projection collects
    the annual loading rather than twelve of them."""
    basis = Expenses(renewal=ExpenseScale(per_policy=48.0,
                                          percent_premium=0.03,
                                          per_mille_sum_assured=0.2))
    m = model(freq=freq, expenses=basis)
    charged = sum(scalar(m.expenses(t)) / scalar(m.pols_if(t))
                  for t in range(freq))
    expected = 48.0 + 0.03 * PREMIUM + 0.2 * SA / 1000.0
    assert charged == pytest.approx(expected, rel=1e-13)


def test_claim_expenses_are_charged_per_claim():
    basis = Expenses(claim=ExpenseScale(per_policy=250.0,
                                        per_mille_sum_assured=0.1))
    m = model(expenses=basis)
    per_claim = 250.0 + 0.1 * SA / 1000.0
    for t in range(TERM):
        assert scalar(m.claim_expenses(t)) == pytest.approx(
            scalar(m.pols_death(t)) * per_claim, rel=1e-14
        )


# --- inflation -----------------------------------------------------------


def test_renewal_expenses_are_indexed_from_projection_time_zero():
    basis = Expenses(renewal=ExpenseScale(per_policy=100.0), inflation=0.03)
    m = model(expenses=basis)
    for t in range(TERM):
        assert scalar(m.expenses(t)) == pytest.approx(
            scalar(m.pols_if(t)) * 100.0 * 1.03 ** t, rel=1e-14
        )


def test_inflation_runs_on_the_calendar_not_on_anniversaries():
    """A monthly projection indexes monthly. Indexing on whole policy years
    would leave twelve months of expense at the same price and then step,
    which is not what inflation does."""
    a = assumptions(freq=12, expenses=Expenses(
        renewal=ExpenseScale(per_policy=100.0), inflation=0.03
    ))
    assert a.inflation_index(1) == pytest.approx(1.03 ** (1 / 12), rel=1e-15)
    assert a.inflation_index(12) == pytest.approx(1.03, rel=1e-14)
    assert a.inflation_index(6) < a.inflation_index(7)


def test_the_annual_indexation_is_the_same_whatever_the_frequency():
    """Twelve monthly index factors span exactly one year of inflation, so
    a finer step changes when expenses rise, not by how much over a year."""
    for freq in (1, 2, 4, 12):
        a = assumptions(freq=freq, expenses=Expenses(
            renewal=ExpenseScale(per_policy=100.0), inflation=0.04
        ))
        assert a.inflation_index(freq) == pytest.approx(1.04, rel=1e-14)
        assert a.inflation_index(5 * freq) == pytest.approx(1.04 ** 5, rel=1e-13)


def test_claim_expenses_are_indexed_to_the_end_of_the_period():
    basis = Expenses(claim=ExpenseScale(per_policy=200.0), inflation=0.03)
    m = model(expenses=basis)
    for t in range(5):
        assert scalar(m.claim_expenses(t)) == pytest.approx(
            scalar(m.pols_death(t)) * 200.0 * 1.03 ** (t + 1), rel=1e-14
        )


def test_inflation_at_or_below_minus_one_hundred_percent_raises():
    with pytest.raises(ValueError, match="at or below -100%"):
        Expenses(inflation=-1.0)


# --- commission ----------------------------------------------------------


def test_commission_steps_down_after_the_initial_period():
    c = Commission(initial_percent=0.6, renewal_percent=0.025,
                   initial_years=2)
    m = model(commission=c)
    for t in range(TERM):
        rate = 0.6 if t < 2 else 0.025
        assert scalar(m.commission(t)) == pytest.approx(
            scalar(m.premiums(t)) * rate, rel=1e-14
        )


@pytest.mark.parametrize("freq", [1, 2, 4, 12])
def test_the_initial_period_is_a_policy_duration_boundary(freq):
    """It sits where it sits whatever the projection frequency: two policy
    years of high commission, not two periods of it."""
    c = Commission(initial_percent=0.6, renewal_percent=0.025,
                   initial_years=2)
    m = model(freq=freq, commission=c)
    high = [t for t in range(TERM * freq)
            if scalar(m.commission(t)) / scalar(m.premiums(t)) > 0.3]
    assert high == list(range(2 * freq))


@pytest.mark.parametrize("freq", [1, 2, 4, 12])
def test_the_same_commission_is_paid_over_the_initial_years(freq):
    c = Commission(initial_percent=0.6, initial_years=2)
    m = model(freq=freq, commission=c)
    paid = sum(scalar(m.commission(t)) / scalar(m.pols_if(t))
               for t in range(2 * freq))
    assert paid == pytest.approx(2.0 * PREMIUM * 0.6, rel=1e-13)


def test_commission_is_off_by_default():
    assert Commission().rate(0) == 0.0
    assert not Commission()
    m = model()
    assert all(scalar(m.commission(t)) == 0.0 for t in range(TERM))


@pytest.mark.parametrize("kw,message", [
    ({"initial_percent": 1.5}, r"outside \[0, 1\]"),
    ({"renewal_percent": -0.1}, r"outside \[0, 1\]"),
    ({"initial_years": -1}, "initial_years .* negative"),
    ({"clawback_years": -1}, "clawback_years .* negative"),
])
def test_a_malformed_commission_basis_raises(kw, message):
    with pytest.raises(ValueError, match=message):
        Commission(**kw)


# --- clawback ------------------------------------------------------------


def test_clawback_is_off_unless_asked_for():
    """It changes the economics of an early lapse, which is exactly why it
    cannot be on by default."""
    c = Commission(initial_percent=0.6)
    assert c.clawback_years == 0.0
    assert np.all(c.clawback_fraction(np.arange(5)) == 0.0)
    m = model(commission=c)
    assert all(scalar(m.commission_clawback(t)) == 0.0 for t in range(TERM))


def test_clawback_runs_off_in_a_straight_line():
    c = Commission(initial_percent=0.6, clawback_years=4)
    assert c.clawback_fraction(0) == 1.0
    assert c.clawback_fraction(1) == 0.75
    assert c.clawback_fraction(2) == 0.5
    assert c.clawback_fraction(4) == 0.0
    assert c.clawback_fraction(9) == 0.0        # clipped, never negative


def test_clawback_recovers_from_the_policies_that_actually_lapsed():
    c = Commission(initial_percent=0.6, clawback_years=4)
    m = model(commission=c)
    for t in range(6):
        assert scalar(m.commission_clawback(t)) == pytest.approx(
            scalar(m.pols_lapse(t)) * PREMIUM * 0.6 * max(0.0, 1.0 - t / 4),
            rel=1e-14,
        )


def test_clawback_reduces_the_cost_of_commission():
    without = model(commission=Commission(initial_percent=0.6))
    with_ = model(commission=Commission(initial_percent=0.6, clawback_years=4))
    assert with_.pv_commission() < without.pv_commission()
    assert with_.net_pv() > without.net_pv()


def test_a_longer_clawback_period_recovers_more():
    values = [
        model(commission=Commission(initial_percent=0.6, clawback_years=y)
              ).pv_commission()
        for y in (0, 2, 5, 10)
    ]
    assert values == sorted(values, reverse=True)


# --- the whole basis -----------------------------------------------------


FULL = Expenses(
    initial=ExpenseScale(per_policy=300.0, percent_premium=0.4,
                         per_mille_sum_assured=1.2),
    renewal=ExpenseScale(per_policy=48.0, percent_premium=0.03,
                         per_mille_sum_assured=0.2),
    claim=ExpenseScale(per_policy=250.0),
    inflation=0.03,
)
FULL_COMMISSION = Commission(initial_percent=0.55, renewal_percent=0.025,
                             initial_years=2, clawback_years=4)


def break_even_premium(**kw):
    """The premium at which ``net_pv`` is zero.

    ``net_pv`` is linear in the premium — premiums, percent-of-premium
    loadings and commission all scale with it, while claims and the fixed
    loadings do not — so two points determine it exactly rather than
    approximately, and the assertion below is about the answer rather than
    about a search converging.
    """
    lo, hi = 1_000.0, 2_000.0
    a, b = (model(mp=point(annual_premium=p), **kw).net_pv() for p in (lo, hi))
    return lo + (hi - lo) * a / (a - b)


def test_a_full_expense_basis_is_worth_a_third_on_the_premium():
    """What an expense basis is *for*, stated as a shape rather than a
    guessed number: the premium that covers the risk alone does not cover
    the cost of writing and keeping the policy, and the gap between the two
    is the loading a pricing basis exists to determine.

    Here it is 37% — a £250,000 twenty-year term policy breaks even on
    claims at about £2,182 a year and on the full basis at about £2,981.
    """
    risk_only = break_even_premium()
    loaded = break_even_premium(expenses=FULL, commission=FULL_COMMISSION)
    assert risk_only == pytest.approx(2_182.0, abs=2.0)
    assert loaded == pytest.approx(2_981.0, abs=2.0)
    assert loaded / risk_only == pytest.approx(1.37, abs=0.01)

    # And the premium between the two is profitable on risk and loss-making
    # once loaded, which is the failure mode the basis exists to catch.
    between = point(annual_premium=2_400.0)
    assert model(mp=between).net_pv() > 0.0
    assert model(mp=between, expenses=FULL,
                 commission=FULL_COMMISSION).net_pv() < 0.0


def test_every_added_line_costs_something():
    loaded = model(expenses=FULL, commission=FULL_COMMISSION)
    assert loaded.pv_initial_expenses() > 0.0
    assert loaded.pv_claim_expenses() > 0.0
    assert loaded.pv_commission() > 0.0
    assert loaded.net_pv() < model().net_pv()


def test_the_pv_lines_add_up_to_the_net():
    m = model(expenses=FULL, commission=FULL_COMMISSION)
    assert m.net_pv() == pytest.approx(
        m.pv_premiums() - m.pv_claims() - m.pv_expenses()
        - m.pv_initial_expenses() - m.pv_claim_expenses() - m.pv_commission(),
        rel=1e-15,
    )


def test_the_two_executors_agree_bitwise_on_a_full_basis():
    points = from_dicts([
        {"id": f"T{i}", "age_at_entry": 35 + 5 * i, "term_years": TERM,
         "sum_assured": SA * (i + 1), "annual_premium": PREMIUM * (i + 1),
         "init_pols": 1}
        for i in range(4)
    ])
    a = assumptions(expenses=FULL, commission=FULL_COMMISSION)
    outputs = ["expenses", "initial_expenses", "claim_expenses",
               "commission", "commission_clawback", "pols_if"]
    interpreted = run(TermLife, points, a, TERM + 2, outputs=outputs)
    vectorized = run_vectorized(TermLife, points, a, TERM + 2, outputs=outputs)
    for name in outputs:
        assert np.array_equal(
            np.array([mp[name] for mp in interpreted.per_mp]).T,
            np.asarray(vectorized.array(name)),
        ), name


def test_the_run_registry_separates_expense_bases():
    from engine.core.registry import record_run

    points = from_dicts([point().__dict__])
    _, bare = record_run(TermLife, points, assumptions(), TERM + 2,
                         outputs=["expenses"])
    _, loaded = record_run(TermLife, points,
                           assumptions(expenses=FULL,
                                       commission=FULL_COMMISSION),
                           TERM + 2, outputs=["expenses"])
    assert bare.run_id != loaded.run_id
    assert bare.assumptions_digest != loaded.assumptions_digest
    assert bare.results_digest != loaded.results_digest
