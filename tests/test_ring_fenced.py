"""Ring-fenced funds — RFC-029.

Transcription against Articles 80, 81, 216 and 217, then the identities and
the measured findings.
"""

import pytest

from engine.report.ring_fenced import (
    RingFencedFund, merged_scr, own_funds_restriction, ring_fenced_scr,
    ring_fencing_cost, undertaking_scenarios,
)
from engine.report.scr import basic_scr

#: RFC-027's composite: a life fund and a non-life fund at one insurer.
LIFE = RingFencedFund("life", modules={
    "market": {"only": 400.0}, "default": {"only": 60.0},
    "life": {"only": 300.0}, "health": {"only": 40.0}})
NON_LIFE = RingFencedFund("non-life", modules={"non_life": {"only": 300.0}})

#: A with-profits fund and the shareholder fund whose market exposures point
#: opposite ways — the case Article 217(6) bites on.
WITH_PROFITS = RingFencedFund(
    "with-profits",
    modules={"market": {"up": 120.0, "down": 260.0}, "life": {"only": 180.0},
             "default": {"only": 40.0}},
    restricted_own_funds=700.0)
REMAINING = RingFencedFund(
    "remaining",
    modules={"market": {"up": 300.0, "down": 90.0}, "life": {"only": 120.0},
             "default": {"only": 30.0}})


# --------------------------------------------------------------------------
# Article 217: the sum, and the scenario
# --------------------------------------------------------------------------

def test_the_requirement_is_the_sum_with_no_diversification():
    """Article 217(2) and (9). The total is the sum of the notional
    requirements, exactly — not an aggregation of them."""
    position = ring_fenced_scr([LIFE, NON_LIFE])
    assert position.scr == pytest.approx(sum(position.notional.values()))
    assert position.notional["life"] == pytest.approx(592.790, abs=5e-3)
    assert position.notional["non-life"] == pytest.approx(300.0)
    assert position.scr == pytest.approx(892.790, abs=5e-3)
    assert position.reconciles()


def test_each_fund_is_aggregated_normally_inside_itself():
    """Article 217(8): within a fund the modules aggregate through Annex IV
    as usual. Only the step *between* funds is denied."""
    assert LIFE.standalone_scr() == pytest.approx(
        basic_scr({"market": 400.0, "default": 60.0, "life": 300.0,
                   "health": 40.0, "non_life": 0.0}))


def test_the_scenario_is_chosen_for_the_undertaking_not_the_fund():
    """Article 217(6) and (7).

    The with-profits fund's own worst market scenario is rates down (260
    against 120); the remaining part's is rates up (300 against 90). Summed
    across the undertaking as paragraph 7 requires, *up* is the worse total
    — 420 against 350 — so the with-profits fund's notional requirement is
    measured under a scenario it would not have chosen, and comes out
    **below** its standalone requirement.
    """
    assert WITH_PROFITS.worst_scenarios()["market"] == "down"
    assert REMAINING.worst_scenarios()["market"] == "up"
    choices = undertaking_scenarios([WITH_PROFITS, REMAINING])
    assert choices["market"] == "up"
    position = ring_fenced_scr([WITH_PROFITS, REMAINING])
    assert position.notional["with-profits"] == pytest.approx(255.343,
                                                              abs=5e-3)
    assert WITH_PROFITS.standalone_scr() == pytest.approx(365.787, abs=5e-3)
    assert position.notional["with-profits"] < WITH_PROFITS.standalone_scr()
    # The fund that got its own way is unaffected.
    assert position.notional["remaining"] == pytest.approx(
        REMAINING.standalone_scr())


def test_a_fund_without_a_risk_contributes_nothing_to_that_scenario():
    """A fund with no market risk is not affected by the market scenario the
    undertaking picked, and does not get a vote on it either."""
    quiet = RingFencedFund("quiet", modules={"life": {"only": 50.0}})
    choices = undertaking_scenarios([WITH_PROFITS, REMAINING, quiet])
    assert choices["market"] == "up"
    assert quiet.capital_under(choices) == {"life": 50.0}


def test_duplicate_fund_names_are_refused():
    with pytest.raises(ValueError, match="distinct"):
        ring_fenced_scr([LIFE, LIFE])


# --------------------------------------------------------------------------
# The finding: ring-fencing costs exactly the diversification it removes
# --------------------------------------------------------------------------

def test_ring_fencing_the_composite_costs_rfc_027s_measured_benefit():
    """RFC-027 measured Annex IV's two zeros — life against non-life, health
    against non-life — as worth **19.28%** to a composite insurer. Ring-fence
    the life fund and Article 217(9) takes back exactly that:

    ``720.69`` merged becomes ``892.79`` as two notional requirements, and
    ``172.10`` of it is the diversification that Annex IV had granted.

    One RFC measures the benefit and the next measures the mechanism that
    removes it, and the number is the same to the last digit reported.
    """
    cost = ring_fencing_cost([LIFE, NON_LIFE])
    assert cost["merged"] == pytest.approx(720.694, abs=5e-3)
    assert cost["ring_fenced"] == pytest.approx(892.790, abs=5e-3)
    assert cost["lost_diversification"] == pytest.approx(172.096, abs=5e-3)
    assert cost["scenario_relief"] == 0.0
    share = cost["lost_diversification"] / cost["ring_fenced"]
    assert share == pytest.approx(0.1928, abs=5e-4)


def test_ring_fencing_identical_funds_costs_nothing_at_all():
    """The bound is the triangle inequality, and it is an *equality* when
    the funds' module mixes are parallel: two funds carrying the same risks
    in the same proportions had no diversification between them to lose.

    So the intuition runs backwards. Ring-fencing hurts most where the funds
    are most different, which is exactly where an insurer would most want to
    pool them.
    """
    a = RingFencedFund("A", modules={"market": {"only": 400.0},
                                     "life": {"only": 300.0}})
    for label, scale in (("identical", 1.0), ("double", 2.0), ("half", 0.5)):
        b = RingFencedFund("B", modules={"market": {"only": 400.0 * scale},
                                         "life": {"only": 300.0 * scale}})
        cost = ring_fencing_cost([a, b])
        assert cost["lost_diversification"] == pytest.approx(0.0, abs=1e-9), \
            label
        assert cost["merged"] == pytest.approx(cost["ring_fenced"])


def test_the_cost_rises_with_how_different_the_funds_are():
    """Same fund A throughout, and a fund B that starts identical to it and
    ends carrying nothing A carries."""
    a = RingFencedFund("A", modules={"market": {"only": 400.0},
                                     "life": {"only": 300.0}})
    profiles = (
        {"market": {"only": 400.0}, "life": {"only": 300.0}},
        {"market": {"only": 650.0}, "life": {"only": 50.0}},
        {"market": {"only": 700.0}},
        {"non_life": {"only": 700.0}},
    )
    shares = []
    for modules in profiles:
        cost = ring_fencing_cost([a, RingFencedFund("B", modules=modules)])
        shares.append(cost["lost_diversification"] / cost["merged"])
    assert shares[0] == pytest.approx(0.0, abs=1e-12)
    assert shares[-1] == pytest.approx(0.2963, abs=5e-4)
    assert shares == sorted(shares)


# --------------------------------------------------------------------------
# The finding: Article 217(6) can hand back more than 217(9) takes
# --------------------------------------------------------------------------

def test_opposed_scenarios_return_more_than_the_lost_diversification():
    """On the with-profits pair, Article 217(9) costs **15.76** of lost
    diversification and Article 217(6) hands back **110.44**, because the
    two funds' market exposures point opposite ways and only one of them can
    have its worst case.

    So ring-fencing is not simply "lose the diversification". It is a
    package, and on a fund whose risks offset the rest of the undertaking's
    the package can be worth having.
    """
    cost = ring_fencing_cost([WITH_PROFITS, REMAINING])
    assert cost["lost_diversification"] == pytest.approx(15.760, abs=5e-3)
    assert cost["scenario_relief"] == pytest.approx(110.444, abs=5e-3)
    assert cost["scenario_relief"] > cost["lost_diversification"]
    assert cost["ring_fenced"] < cost["standalone_sum"]


def test_aligned_scenarios_give_no_relief_at_all():
    """Point both funds the same way and Article 217(6) has nothing to
    choose — the relief is exactly zero and only the lost diversification
    remains."""
    aligned = RingFencedFund(
        "remaining",
        modules={"market": {"up": 90.0, "down": 300.0},
                 "life": {"only": 120.0}, "default": {"only": 30.0}})
    cost = ring_fencing_cost([WITH_PROFITS, aligned])
    assert undertaking_scenarios([WITH_PROFITS, aligned])["market"] == "down"
    assert cost["scenario_relief"] == pytest.approx(0.0, abs=1e-9)
    assert cost["lost_diversification"] > 0.0


def test_factor_only_modules_leave_nothing_for_article_217_6_to_choose():
    cost = ring_fencing_cost([LIFE, NON_LIFE])
    assert cost["scenario_relief"] == 0.0
    assert cost["ring_fenced"] == pytest.approx(cost["standalone_sum"])


# --------------------------------------------------------------------------
# Article 81: the own-funds side, which is the larger of the two costs
# --------------------------------------------------------------------------

def test_only_the_surplus_over_the_notional_requirement_is_trapped():
    """Article 81(1): restricted own funds count up to what the fund itself
    needs, and the excess is removed from the reconciliation reserve."""
    fund = RingFencedFund("f", restricted_own_funds=700.0)
    assert own_funds_restriction(fund, 500.0) == pytest.approx(200.0)
    assert own_funds_restriction(fund, 700.0) == 0.0
    assert own_funds_restriction(fund, 900.0) == 0.0
    # Article 81(2)'s materiality derogation: the whole amount comes out.
    assert own_funds_restriction(fund, 0.0) == pytest.approx(700.0)


def test_a_fund_with_no_restricted_own_funds_is_never_restricted():
    assert own_funds_restriction(RingFencedFund("f"), 0.0) == 0.0


def test_the_own_funds_restriction_is_the_larger_cost():
    """Decomposed on the with-profits pair with the funds pointing the same
    way, so that ring-fencing is doing its worst.

    The requirement rises from 722.91 to 725.79 — worth 0.60 percentage
    points of solvency ratio. The own funds fall from 1,100 to 765.79
    because 334.21 is trapped above what the with-profits fund needs —
    worth **46.05** percentage points. The Article 216(2) exemption is
    almost entirely an own-funds question.
    """
    aligned = RingFencedFund(
        "remaining",
        modules={"market": {"up": 90.0, "down": 300.0},
                 "life": {"only": 120.0}, "default": {"only": 30.0}})
    funds = [WITH_PROFITS, aligned]
    position = ring_fenced_scr(funds, unrestricted_own_funds=400.0)
    merged = merged_scr(funds)
    full_own_funds = 400.0 + WITH_PROFITS.restricted_own_funds

    assert merged == pytest.approx(722.911, abs=5e-3)
    assert position.scr == pytest.approx(725.787, abs=5e-3)
    assert position.restriction == pytest.approx(334.213, abs=5e-3)
    assert position.own_funds == pytest.approx(765.787, abs=5e-3)

    unfenced_ratio = full_own_funds / merged
    scr_only = full_own_funds / position.scr
    assert unfenced_ratio == pytest.approx(1.5216, abs=5e-4)
    assert scr_only == pytest.approx(1.5156, abs=5e-4)
    assert position.solvency_ratio == pytest.approx(1.0551, abs=5e-4)
    assert unfenced_ratio - scr_only == pytest.approx(0.0060, abs=5e-4)
    assert scr_only - position.solvency_ratio == pytest.approx(0.4605,
                                                               abs=5e-4)
    assert position.reconciles()


def test_article_216_2_is_the_merged_calculation():
    """A fund with Article 304 approval is not adjusted under Article 217 at
    all: the calculation assumes "full diversification between the assets
    and liabilities of the ring-fenced funds and the rest of the
    undertaking", which is the merged aggregate."""
    funds = [LIFE, NON_LIFE]
    assert merged_scr(funds) == pytest.approx(
        basic_scr({"market": 400.0, "default": 60.0, "life": 300.0,
                   "health": 40.0, "non_life": 300.0}))
    assert merged_scr(funds) < ring_fenced_scr(funds).scr


def test_a_position_reports_its_ratio_and_reconciles():
    position = ring_fenced_scr([WITH_PROFITS, REMAINING],
                               unrestricted_own_funds=400.0)
    assert position.reconciles()
    assert "SCR=" in repr(position)
    assert "ratio=" in repr(position)
    empty = ring_fenced_scr([RingFencedFund("f")], unrestricted_own_funds=10.0)
    assert empty.scr == 0.0
    assert empty.solvency_ratio == float("inf")
