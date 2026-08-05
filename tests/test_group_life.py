"""Group life: pooled experience rating, and the refund that is an option."""

import numpy as np
import pytest

from engine.core.vector import run_vectorized
from engine.data.assumptions import Assumptions, MortalityTable
from engine.data.modelpoints import from_dicts
from engine.library.group_life import (
    GroupLife,
    binomial_pmf,
    deterministic_refund,
    expected_refund,
    refund_option_value,
)


def assumptions(**kw):
    base = dict(mortality=MortalityTable.flat(0.004), lapse=0.10,
                interest=0.03, freq=1)
    base.update(kw)
    return Assumptions(**base)


def scheme(**overrides):
    members = [
        {"id": "G1", "age_at_entry": 35, "salary": 45_000.0,
         "salary_multiple": 4.0, "unit_rate": 6.0, "init_pols": 200,
         "salary_escalation": 0.03},
        {"id": "G2", "age_at_entry": 52, "salary": 80_000.0,
         "salary_multiple": 4.0, "unit_rate": 6.0, "init_pols": 50,
         "salary_escalation": 0.03},
    ]
    return from_dicts([{**m, **overrides} for m in members])


def project(model_cls=GroupLife, points=None, proj_len=8, **kw):
    """Pooled models go through the batch executor.

    The interpreted runner builds one instance per model point, so a
    reduction there would pool a block of one — the same constraint
    :class:`WithProfitsEndowment` works under.
    """
    return run_vectorized(model_cls, points if points is not None else scheme(),
                          assumptions(**kw), proj_len=proj_len,
                          outputs=["lives_if", "sum_assured", "claims",
                                   "premiums", "scheme_margin",
                                   "surplus_carried", "experience_refund",
                                   "insurer_result", "strike", "in_cover"])


def pooled(result, name):
    """A pooled series, read once rather than summed across the block."""
    return np.array(result.per_mp[0][name])


# --- the binomial claim distribution ----------------------------------------

def test_the_claim_distribution_is_a_distribution():
    pmf = binomial_pmf(500, 0.004)
    assert pmf.sum() == pytest.approx(1.0, rel=1e-12)
    assert np.all(pmf >= 0.0)


def test_its_mean_is_the_expected_death_count():
    lives, q = 500, 0.004
    pmf = binomial_pmf(lives, q)
    assert (pmf * np.arange(lives + 1)).sum() == pytest.approx(lives * q,
                                                               rel=1e-10)


def test_it_survives_a_scheme_too_big_for_a_binomial_coefficient():
    """Log space, because a real payroll overflows the direct form."""
    pmf = binomial_pmf(5000, 0.004)
    assert pmf.sum() == pytest.approx(1.0, rel=1e-10)
    assert np.isfinite(pmf).all()


@pytest.mark.parametrize("q, where", [(0.0, 0), (1.0, 40)])
def test_the_degenerate_rates_are_handled_rather_than_logged(q, where):
    pmf = binomial_pmf(40, q)
    assert pmf[where] == 1.0
    assert pmf.sum() == 1.0


@pytest.mark.parametrize("lives, q", [(-1, 0.1), (10, 1.5), (10, -0.1)])
def test_the_distribution_validates_its_inputs(lives, q):
    with pytest.raises(ValueError):
        binomial_pmf(lives, q)


# --- the refund is an option ------------------------------------------------

def test_the_expected_refund_is_never_below_the_deterministic_one():
    """Jensen on max(., 0), which is convex. There is no calibration under
    which a deterministic projection of a profit share is right."""
    for lives in (10, 50, 200, 1000):
        for load in (0.8, 1.0, 1.25, 2.0):
            net = 200_000.0 * lives * 0.004 * load
            assert refund_option_value(net, 200_000.0, lives, 0.004,
                                       0.5) >= -1e-9


def test_certain_claims_leave_no_option_value():
    """At q = 0 the death count is not a distribution and the two agree
    exactly."""
    assert refund_option_value(100_000.0, 200_000.0, 500, 0.0, 0.5) == 0.0


def test_a_scheme_priced_at_expected_claims_shows_no_refund_and_costs_one():
    """The whole finding in one line: the deterministic answer is exactly
    zero and the real cost is 26,791 a period."""
    net = 200_000.0 * 100 * 0.004
    assert deterministic_refund(net, 200_000.0, 100, 0.004, 0.5) == 0.0
    assert expected_refund(net, 200_000.0, 100, 0.004, 0.5) == pytest.approx(
        26_791, abs=1
    )


def test_it_still_costs_something_on_a_scheme_priced_below_expected_claims():
    net = 200_000.0 * 100 * 0.004 * 0.9
    assert deterministic_refund(net, 200_000.0, 100, 0.004, 0.5) == 0.0
    assert expected_refund(net, 200_000.0, 100, 0.004, 0.5) == pytest.approx(
        24_112, abs=1
    )


def test_the_option_is_worth_far_more_per_life_on_a_small_scheme():
    """And a small scheme is exactly the one whose own experience is least
    credible. The weakest case for experience rating is the dearest."""
    per_life = {}
    for lives in (25, 50, 100, 250, 1000, 5000):
        net = 200_000.0 * lives * 0.004 * 1.25
        per_life[lives] = refund_option_value(net, 200_000.0, lives, 0.004,
                                              0.5) / lives
    assert per_life[25] == pytest.approx(352, abs=1)
    assert per_life[5000] == pytest.approx(7, abs=1)
    sizes = sorted(per_life)
    assert all(per_life[a] > per_life[b]
               for a, b in zip(sizes, sizes[1:]))


def test_the_uplift_over_the_deterministic_cost_falls_with_scheme_size():
    uplifts = {}
    for lives in (25, 1000, 5000):
        net = 200_000.0 * lives * 0.004 * 1.25
        uplifts[lives] = (expected_refund(net, 200_000.0, lives, 0.004, 0.5)
                          / deterministic_refund(net, 200_000.0, lives,
                                                 0.004, 0.5) - 1.0)
    assert uplifts[25] == pytest.approx(3.52, abs=0.01)
    assert uplifts[1000] == pytest.approx(0.409, abs=0.005)
    assert uplifts[5000] == pytest.approx(0.066, abs=0.005)


def test_the_share_scales_the_whole_thing():
    args = (200_000.0 * 100 * 0.004 * 1.25, 200_000.0, 100, 0.004)
    assert expected_refund(*args, 1.0) == pytest.approx(
        2.0 * expected_refund(*args, 0.5), rel=1e-12
    )


# --- the pooled projection --------------------------------------------------

def test_every_member_sees_the_same_scheme_figures():
    """Which is what @pool means, and why an experience refund needs one."""
    result = project()
    for name in ("scheme_margin", "surplus_carried", "experience_refund",
                 "insurer_result"):
        assert result.per_mp[0][name] == result.per_mp[1][name], name


def test_the_pooled_variables_are_declared_as_such():
    assert set(GroupLife.pooled_names()) == {
        "scheme_margin", "surplus_carried", "experience_refund",
        "insurer_result",
    }
    assert "claims" not in GroupLife.pooled_names()


def test_the_refund_falls_only_at_the_end_of_a_rating_period():
    result = project()
    refund = pooled(result, "experience_refund")
    paid = np.flatnonzero(refund > 0.0)
    assert paid.tolist() == [2, 5, 8]


def test_the_surplus_resets_after_it_is_paid_away():
    """The balance is multiplied by 1 − strike at the *previous* period, so
    the period after a refund starts from that period's margin alone."""
    result = project()
    carried = pooled(result, "surplus_carried")
    margin = pooled(result, "scheme_margin")
    assert carried[3] == pytest.approx(margin[3], rel=1e-12)
    assert carried[6] == pytest.approx(margin[6], rel=1e-12)
    assert carried[1] > margin[1]


def test_the_refund_is_the_share_of_the_surplus_struck():
    result = project()
    carried = pooled(result, "surplus_carried")
    refund = pooled(result, "experience_refund")
    assert refund[2] == pytest.approx(0.5 * carried[2], rel=1e-12)


def test_the_insurer_keeps_the_margin_less_whatever_it_gave_back():
    result = project()
    assert np.allclose(pooled(result, "insurer_result"),
                       pooled(result, "scheme_margin")
                       - pooled(result, "experience_refund"), rtol=1e-12)


def test_a_deficit_scheme_is_refunded_nothing_and_charged_nothing():
    """The floor, which is the option. A bad rating period is the insurer's,
    and it is not carried into the next one either."""
    result = project(points=scheme(unit_rate=2.0))
    assert np.all(pooled(result, "surplus_carried") < 0.0)
    assert np.all(pooled(result, "experience_refund") == 0.0)
    assert np.allclose(pooled(result, "insurer_result"),
                       pooled(result, "scheme_margin"))


def test_the_retained_margin_is_taken_before_the_pot_is_struck():
    class NoMargin(GroupLife):
        retained_margin = 0.0

    kept = pooled(project(), "surplus_carried")[0]
    everything = pooled(project(NoMargin), "surplus_carried")[0]
    assert everything > kept
    assert everything - kept == pytest.approx(
        0.10 * np.array(project().aggregate("premiums"))[0], rel=1e-12
    )


def test_a_longer_rating_period_pays_later_and_larger():
    class FiveYear(GroupLife):
        rating_period = 5

    three = pooled(project(), "experience_refund")
    five = pooled(project(FiveYear), "experience_refund")
    assert np.flatnonzero(five > 0.0).tolist() == [4]
    assert five[4] > three[2]


# --- cover follows salary ---------------------------------------------------

def test_cover_escalates_without_anybody_underwriting_it():
    result = project()
    cover = np.array(result.per_mp[0]["sum_assured"])
    assert cover[0] == pytest.approx(180_000.0)
    assert cover[3] == pytest.approx(180_000.0 * 1.03 ** 3, rel=1e-12)


def test_cover_ceases_at_the_scheme_terminal_age():
    result = project(points=from_dicts([
        {"id": "G3", "age_at_entry": 63, "salary": 50_000.0,
         "salary_multiple": 3.0, "unit_rate": 6.0, "init_pols": 10},
    ]), proj_len=5)
    in_cover = np.array(result.per_mp[0]["in_cover"])
    assert in_cover.tolist() == [1.0, 1.0, 0.0, 0.0, 0.0, 0.0]
    assert np.array(result.aggregate("claims"))[2] == 0.0


def test_claims_and_premiums_are_both_struck_on_the_escalating_cover():
    result = project()
    claims = np.array(result.aggregate("claims"))
    premiums = np.array(result.aggregate("premiums"))
    # Same cover, same lives: the ratio is the unit rate against mortality.
    assert np.allclose(claims / premiums, 0.004 / 0.006, rtol=1e-12)


def test_a_scheme_running_at_expected_claims_generates_no_refund():
    """Because the retained margin comes out first — which is the whole
    reason the insurer is willing to write the profit share."""
    result = project(points=scheme(unit_rate=4.0))
    assert np.all(pooled(result, "experience_refund") == 0.0)
