"""Loan amortisation, refund bases, and single-premium credit life."""

import numpy as np
import pytest

from engine.core.runner import run
from engine.core.vector import run_vectorized
from engine.data.assumptions import Assumptions, MortalityTable
from engine.data.loan import (
    AMORTISATION_METHODS,
    Loan,
    UNEARNED_BASES,
    annuity_certain,
    exposure_unearned,
    pro_rata_excess,
    pro_rata_unearned,
    rule_of_78_shortfall,
    rule_of_78_unearned,
    worst_shortfall,
)
from engine.data.modelpoints import from_dicts
from engine.library.credit_life import CreditLife


class ProRataRefund(CreditLife):
    refund_basis = "pro_rata"


class RuleOf78Refund(CreditLife):
    refund_basis = "rule_of_78"


def assumptions(**kw):
    base = dict(mortality=MortalityTable.flat(0.01), lapse=0.15,
                interest=0.03, freq=1)
    base.update(kw)
    return Assumptions(**base)


def points(**kw):
    fields = {"id": "C1", "age_at_entry": 40, "loan_principal": 20_000.0,
              "loan_rate": 0.12, "loan_term_years": 5,
              "single_premium": 700.0, "init_pols": 1000}
    fields.update(kw)
    return from_dicts([fields])


# --- annuities and schedules ------------------------------------------------

def test_annuity_certain_takes_the_zero_rate_limit():
    """A 0% loan is a real product and the naive expression is 0/0."""
    assert annuity_certain(60, 0.0) == 60.0
    assert annuity_certain([1, 5, 12], 0.0).tolist() == [1.0, 5.0, 12.0]


def test_annuity_certain_matches_the_closed_form():
    assert annuity_certain(10, 0.05) == pytest.approx(
        (1 - 1.05 ** -10) / 0.05, rel=1e-15
    )


def test_annuity_certain_refuses_a_negative_term():
    with pytest.raises(ValueError, match="cannot be negative"):
        annuity_certain(-1, 0.05)


def test_a_level_instalment_schedule_closes_exactly():
    """The principal in and zero out, both exactly — a schedule that does
    not close is a failure rather than a rounding."""
    loan = Loan(principal=20_000.0, rate=0.12, term=60, freq=12)
    balances = loan.balances()
    assert balances.size == 61
    assert balances[0] == pytest.approx(20_000.0, rel=1e-12)
    assert balances[-1] == pytest.approx(0.0, abs=1e-9)


def test_the_instalment_is_the_annuity_formula():
    loan = Loan(principal=20_000.0, rate=0.12, term=60, freq=12)
    assert loan.instalment == pytest.approx(444.89, abs=0.01)
    assert loan.instalment == pytest.approx(
        20_000.0 / annuity_certain(60, 0.01), rel=1e-14
    )


def test_principal_repaid_and_interest_add_to_the_instalment():
    loan = Loan(principal=20_000.0, rate=0.12, term=60, freq=12)
    total = loan.principal_repaid() + loan.interest()
    assert np.allclose(total, loan.instalment, atol=1e-9)


def test_a_zero_rate_loan_amortises_in_a_straight_line():
    loan = Loan(principal=1200.0, rate=0.0, term=12, freq=12)
    assert loan.instalment == pytest.approx(100.0, rel=1e-14)
    assert np.allclose(loan.balances(), np.arange(12, -1, -1) * 100.0)


def test_an_interest_only_loan_keeps_its_balance_to_the_end():
    loan = Loan(principal=1000.0, rate=0.06, term=5, freq=1,
                method="interest_only")
    assert loan.balances().tolist() == [1000.0] * 5 + [0.0]
    assert loan.instalment == pytest.approx(60.0)


def test_a_straight_line_loan_has_no_level_instalment():
    loan = Loan(principal=1000.0, rate=0.06, term=5, freq=1,
                method="straight_line")
    with pytest.raises(ValueError, match="no level instalment"):
        loan.instalment


def test_the_sum_at_risk_is_the_balance_before_the_instalment():
    """Covering the balance *after* it pays off a debt the estate has
    already serviced."""
    loan = Loan(principal=1000.0, rate=0.06, term=5, freq=1)
    assert np.array_equal(loan.sum_at_risk(), loan.balances()[:-1])
    assert loan.sum_at_risk()[0] == 1000.0


@pytest.mark.parametrize("kwargs, message", [
    ({"principal": 0.0, "rate": 0.1, "term": 5}, "must be positive"),
    ({"principal": 100.0, "rate": 0.1, "term": 0}, "at least one period"),
    ({"principal": 100.0, "rate": -0.1, "term": 5}, "is negative"),
    ({"principal": 100.0, "rate": 0.1, "term": 5, "method": "balloon"},
     "amortisation method"),
])
def test_loan_validates_its_terms(kwargs, message):
    with pytest.raises(ValueError, match=message):
        Loan(**kwargs)


def test_the_amortisation_methods_are_all_reachable():
    for method in AMORTISATION_METHODS:
        loan = Loan(1000.0, 0.06, 5, freq=1, method=method)
        assert loan.balances()[0] == pytest.approx(1000.0, rel=1e-12)
        assert loan.balances()[-1] == pytest.approx(0.0, abs=1e-9)


# --- the three refund bases -------------------------------------------------

def test_every_basis_starts_at_one_and_ends_at_nothing():
    loan = Loan(20_000.0, 0.12, 60, freq=12)
    for basis in UNEARNED_BASES:
        unearned = loan.unearned(basis)
        assert unearned[0] == pytest.approx(1.0, rel=1e-14)
        assert unearned[-1] == pytest.approx(0.0, abs=1e-14)


def test_the_bases_are_ordered_rule_of_78_below_sum_at_risk_below_pro_rata():
    """Provable for any declining balance, and the reason pro rata is not
    the fair alternative it is usually presented as."""
    loan = Loan(20_000.0, 0.12, 60, freq=12)
    r78 = loan.unearned("rule_of_78")
    sar = loan.unearned("sum_at_risk")
    pro = loan.unearned("pro_rata")
    assert np.all(r78 <= sar + 1e-15)
    assert np.all(sar <= pro + 1e-15)


def test_the_rule_of_78_is_exactly_right_on_an_interest_free_loan():
    """The rule was written for a flat, interest-free loan and is correct on
    precisely that product. Algebraically identical, so the two agree to one
    ulp — and bit for bit on a term short enough not to accumulate one."""
    for method in ("straight_line", "level_instalment"):
        loan = Loan(20_000.0, 0.0, 60, freq=12, method=method)
        assert np.abs(rule_of_78_shortfall(loan)).max() <= 2e-16
    short = Loan(20_000.0, 0.0, 5, freq=1, method="straight_line")
    assert np.array_equal(short.unearned("rule_of_78"),
                          short.unearned("sum_at_risk"))


def test_the_run_off_reaches_exactly_nothing():
    """Accumulated from the far end rather than subtracted from a total, so
    the one duration whose answer is known in advance is exact."""
    for rate in (0.0, 0.12):
        unearned = Loan(20_000.0, rate, 60, freq=12).unearned("sum_at_risk")
        assert unearned[0] == 1.0
        assert unearned[-1] == 0.0


def test_the_shortfall_grows_with_the_interest_rate():
    """The balance runs off more slowly than a straight line, so more risk
    is left than the rule admits, and the gap widens with the rate."""
    measured = {rate: worst_shortfall(Loan(20_000.0, rate, 60, freq=12))
                for rate in (0.12, 0.24)}
    assert measured[0.12][0] == pytest.approx(0.0282, abs=1e-4)
    assert measured[0.24][0] == pytest.approx(0.0531, abs=1e-4)
    assert measured[0.12][1] == measured[0.24][1] == 21


def test_the_worst_shortfall_is_not_at_the_midpoint():
    """Measured, not assumed: the two curves are not symmetric about it."""
    _, where = worst_shortfall(Loan(20_000.0, 0.12, 60, freq=12))
    assert where != 30


def test_pro_rata_beats_the_rule_of_78_by_exactly_n_over_four_n_plus_one():
    """The comparison usually quoted — and the one that overstates the case,
    because pro rata is not the right answer either."""
    for n in (12, 60, 120):
        excess = pro_rata_excess(n)
        assert excess.max() == pytest.approx(n / (4 * (n + 1)), rel=1e-12)
        assert int(np.argmax(excess)) == n // 2


def test_pro_rata_and_rule_of_78_have_their_textbook_forms():
    n = 12
    assert pro_rata_unearned(n)[3] == pytest.approx(9 / 12)
    assert rule_of_78_unearned(n)[3] == pytest.approx(9 * 10 / (12 * 13))
    assert rule_of_78_unearned(n)[0] == pytest.approx(1.0)


def test_exposure_unearned_is_the_share_of_exposure_still_to_run():
    assert np.allclose(exposure_unearned([3.0, 2.0, 1.0]),
                       [1.0, 0.5, 1 / 6, 0.0])


@pytest.mark.parametrize("exposure, message", [
    ([], "nothing to earn"),
    ([1.0, -1.0], "not an amount at risk"),
    ([0.0, 0.0], "no unearned share"),
])
def test_exposure_unearned_validates(exposure, message):
    with pytest.raises(ValueError, match=message):
        exposure_unearned(exposure)


def test_refund_reads_off_the_chosen_basis():
    loan = Loan(20_000.0, 0.12, 60, freq=12)
    assert loan.refund(700.0, 30, "pro_rata") == pytest.approx(350.0)
    assert loan.refund(700.0, 0) == pytest.approx(700.0, rel=1e-14)
    assert loan.refund(700.0, 60) == pytest.approx(0.0, abs=1e-11)


def test_refund_refuses_a_duration_outside_the_loan():
    with pytest.raises(ValueError, match="outside the loan"):
        Loan(20_000.0, 0.12, 60, freq=12).refund(700.0, 61)


def test_unearned_refuses_an_unknown_basis():
    with pytest.raises(ValueError, match="unearned basis"):
        Loan(20_000.0, 0.12, 60, freq=12).unearned("whatever_is_left")


# --- the template agrees with the schedule ----------------------------------

def test_the_projected_balance_is_the_loan_schedule():
    """Two implementations sharing no code: the template's closed form in
    ``t`` and the module's vector recursion."""
    result = run(CreditLife, points(), assumptions(), proj_len=7,
                 outputs=["outstanding_balance"])
    projected = np.array(result.per_mp[0]["outstanding_balance"])
    schedule = Loan(20_000.0, 0.12, 5, freq=1).sum_at_risk()
    assert np.allclose(projected[:5], schedule, rtol=1e-12)
    assert np.all(projected[5:] == 0.0)


@pytest.mark.parametrize("model_cls, basis", [
    (CreditLife, "sum_at_risk"),
    (ProRataRefund, "pro_rata"),
    (RuleOf78Refund, "rule_of_78"),
])
def test_every_refund_basis_matches_the_module(model_cls, basis):
    result = run(model_cls, points(), assumptions(), proj_len=6,
                 outputs=["unearned_fraction"])
    projected = np.array(result.per_mp[0]["unearned_fraction"])
    assert np.allclose(projected[:6], Loan(20_000.0, 0.12, 5, freq=1)
                       .unearned(basis), rtol=1e-12)


def test_a_zero_rate_loan_projects_without_dividing_by_zero():
    result = run(CreditLife, points(loan_rate=0.0), assumptions(), proj_len=6,
                 outputs=["outstanding_balance", "unearned_fraction"])
    balances = np.array(result.per_mp[0]["outstanding_balance"])
    assert np.allclose(balances[:5], [20_000.0, 16_000.0, 12_000.0,
                                      8_000.0, 4_000.0])
    unearned = np.array(result.per_mp[0]["unearned_fraction"])
    assert np.allclose(unearned[:6], rule_of_78_unearned(5), atol=1e-14)


def test_an_unknown_refund_basis_is_refused_at_class_definition():
    with pytest.raises(ValueError, match="refund basis"):
        class Broken(CreditLife):
            refund_basis = "whatever_the_lender_likes"


# --- the premium is either earned or refunded -------------------------------

def test_every_unit_of_premium_is_either_earned_or_given_back():
    """The reconciliation invariant for this template. Nothing else may
    consume a single premium."""
    result = run(CreditLife, points(), assumptions(), proj_len=6,
                 outputs=["earned_premium", "refunds", "premiums"])
    earned = np.array(result.aggregate("earned_premium")).sum()
    refunded = np.array(result.aggregate("refunds")).sum()
    collected = np.array(result.aggregate("premiums")).sum()
    assert earned + refunded == pytest.approx(collected, rel=1e-12)


@pytest.mark.parametrize("model_cls", [CreditLife, ProRataRefund,
                                       RuleOf78Refund])
def test_the_invariant_holds_on_every_basis(model_cls):
    result = run(model_cls, points(), assumptions(), proj_len=6,
                 outputs=["earned_premium", "refunds", "premiums"])
    total = (np.array(result.aggregate("earned_premium")).sum()
             + np.array(result.aggregate("refunds")).sum())
    assert total == pytest.approx(700_000.0, rel=1e-12)


def test_the_reserve_roll_closes_into_the_next_period():
    result = run(CreditLife, points(), assumptions(), proj_len=6,
                 outputs=["unearned_premium_reserve",
                          "closing_unearned_reserve"])
    opening = np.array(result.aggregate("unearned_premium_reserve"))
    closing = np.array(result.aggregate("closing_unearned_reserve"))
    assert np.allclose(closing[:-1], opening[1:], rtol=1e-12)


def test_the_reserve_opens_at_the_whole_premium_and_runs_to_nothing():
    result = run(CreditLife, points(), assumptions(), proj_len=6,
                 outputs=["unearned_premium_reserve"])
    reserve = np.array(result.aggregate("unearned_premium_reserve"))
    assert reserve[0] == pytest.approx(700_000.0, rel=1e-12)
    assert reserve[5] == pytest.approx(0.0, abs=1e-6)


def test_a_death_earns_the_rest_of_the_premium():
    """No refund on top of a claim, so the whole unearned balance on a
    dying life falls into income at once — the reverse of a settlement."""
    no_deaths = run(CreditLife, points(), assumptions(mortality=
                    MortalityTable.flat(0.0)), proj_len=6,
                    outputs=["earned_premium", "refunds"])
    with_deaths = run(CreditLife, points(), assumptions(), proj_len=6,
                      outputs=["earned_premium", "refunds"])
    assert (np.array(with_deaths.aggregate("earned_premium")).sum()
            > np.array(no_deaths.aggregate("earned_premium")).sum())
    assert (np.array(with_deaths.aggregate("refunds")).sum()
            < np.array(no_deaths.aggregate("refunds")).sum())


# --- what the basis is worth ------------------------------------------------

def test_the_rule_of_78_keeps_a_measurable_share_of_the_premium():
    """On this book, one point of gross premium, and a tenth of the net
    cash the business generates."""
    refunds = {}
    for model_cls in (RuleOf78Refund, CreditLife, ProRataRefund):
        result = run(model_cls, points(), assumptions(), proj_len=6,
                     outputs=["refunds", "net_cashflow"])
        refunds[model_cls.refund_basis] = (
            np.array(result.aggregate("refunds")).sum(),
            np.array(result.aggregate("net_cashflow")).sum(),
        )
    assert refunds["rule_of_78"][0] == pytest.approx(123_141, abs=1)
    assert refunds["sum_at_risk"][0] == pytest.approx(130_333, abs=1)
    assert refunds["pro_rata"][0] == pytest.approx(177_477, abs=1)
    kept = refunds["sum_at_risk"][0] - refunds["rule_of_78"][0]
    assert kept / 700_000.0 == pytest.approx(0.0103, abs=1e-4)


def test_pro_rata_is_not_the_neutral_alternative():
    """It removes 91% of the book's net cash, which is not a correction of
    an unfairness — it is a different and larger error."""
    cash = {}
    for model_cls in (RuleOf78Refund, CreditLife, ProRataRefund):
        result = run(model_cls, points(), assumptions(), proj_len=6,
                     outputs=["net_cashflow"])
        cash[model_cls.refund_basis] = np.array(
            result.aggregate("net_cashflow")).sum()
    assert cash["rule_of_78"] > cash["sum_at_risk"] > cash["pro_rata"]
    assert cash["pro_rata"] / cash["rule_of_78"] == pytest.approx(0.094,
                                                                  abs=0.005)


def test_the_vectorized_executor_agrees_with_the_interpreter():
    block = from_dicts([
        {"id": "C1", "age_at_entry": 40, "loan_principal": 20_000.0,
         "loan_rate": 0.12, "loan_term_years": 5, "single_premium": 700.0,
         "init_pols": 1000},
        {"id": "C2", "age_at_entry": 55, "loan_principal": 5_000.0,
         "loan_rate": 0.0, "loan_term_years": 3, "single_premium": 90.0,
         "init_pols": 400},
    ])
    names = ["outstanding_balance", "unearned_fraction", "claims", "refunds",
             "unearned_premium_reserve", "earned_premium", "net_cashflow"]
    interpreted = run(CreditLife, block, assumptions(), proj_len=6,
                      outputs=names)
    vectorized = run_vectorized(CreditLife, block, assumptions(), proj_len=6,
                                outputs=names)
    for name in names:
        assert np.allclose(interpreted.aggregate(name),
                           vectorized.aggregate(name), rtol=1e-12,
                           atol=1e-9), name
