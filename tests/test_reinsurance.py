"""Reinsurance: quota share, surplus and excess of loss.

The last of the PLAN §5.1 Layer 0 primitives. All three treaties answer the
same two questions — how much of each claim does the reinsurer pay, and what
does that cost — and they answer the first one differently enough that
conflating them is a real source of error.

What this file pins:

**The proportional invariant.** Retained plus ceded is the whole sum
assured, exactly, for every policy and every treaty that claims to be
proportional. The same shape of statement multiple decrements make about
survival, and asserted the same way.

**The surplus cap.** A four-line treaty on a retention of 50,000 takes at
most 200,000, so a 500,000 policy leaves the cedant carrying 300,000 — not
50,000. Writing a surplus treaty as `max(0, SA - retention)` and concluding
the cedant never keeps more than its retention is false for exactly the
policies where being wrong matters most.

**That excess of loss is not proportional and does not pretend to be.** The
layer is a function of the claim, and above the limit the excess comes back.

**That nothing moved.** No treaty is the default, and a projection without
one keeps its numbers bit for bit.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from engine.core.runner import run
from engine.core.vector import run_vectorized
from engine.data.assumptions import Assumptions, MortalityTable
from engine.data.modelpoints import ModelPoint, from_dicts
from engine.data.reinsurance import (
    ExcessOfLoss,
    NoReinsurance,
    QuotaShare,
    Surplus,
)
from engine.library.term_life import TermLife

Q, LAPSE, INTEREST = 0.009, 0.055, 0.031
TERM, SA, PREMIUM = 20, 250_000.0, 3_000.0
SIZES = np.array([0.0, 10_000.0, 50_000.0, 250_000.0, 1_000_000.0, 5e6])


def point(**kw):
    row = {"id": "T1", "age_at_entry": 40, "term_years": TERM,
           "sum_assured": SA, "annual_premium": PREMIUM, "init_pols": 1}
    row.update(kw)
    return ModelPoint(**row)


def assumptions(**kw):
    row = dict(mortality=MortalityTable.flat(Q), lapse=LAPSE, interest=INTEREST)
    row.update(kw)
    return Assumptions(**row)


def model(mp=None, **kw):
    a = assumptions(**kw)
    return TermLife(mp or point(), a, TERM + 2)


def scalar(value) -> float:
    return float(np.asarray(value).reshape(-1)[0])


PROPORTIONAL = [
    QuotaShare(0.4),
    QuotaShare(1.0),
    QuotaShare(0.0),
    Surplus(50_000.0),
    Surplus(50_000.0, lines=4),
    Surplus(0.0),
    Surplus(1e9),
    NoReinsurance(),
]


# --- the proportional invariant ------------------------------------------


@pytest.mark.parametrize("treaty", PROPORTIONAL, ids=repr)
def test_retained_plus_ceded_is_the_whole_risk(treaty):
    """If this failed, the gross and net views of one block would disagree
    about how much risk exists."""
    assert treaty.proportional
    recovered = treaty.recovery_per_claim(SIZES)
    retained = treaty.retained_per_claim(SIZES)
    assert np.array_equal(recovered + retained, SIZES)
    assert np.all(recovered >= 0.0)
    assert np.all(retained >= 0.0)


@pytest.mark.parametrize("treaty", PROPORTIONAL, ids=repr)
def test_a_proportional_treaty_cedes_a_fraction_of_the_sum_assured(treaty):
    fraction = treaty.ceded_fraction(SIZES)
    assert np.all((fraction >= 0.0) & (fraction <= 1.0))
    assert np.allclose(treaty.recovery_per_claim(SIZES), fraction * SIZES)


# --- quota share ---------------------------------------------------------


def test_quota_share_cedes_the_same_fraction_of_every_policy():
    """The point of a quota share, and its limitation: it cedes as much of a
    small policy as of a large one, so it scales the block without changing
    its shape."""
    treaty = QuotaShare(0.4)
    assert np.array_equal(treaty.ceded_fraction(SIZES), np.full(SIZES.shape, 0.4))
    assert np.array_equal(treaty.recovery_per_claim(SIZES), 0.4 * SIZES)


def test_a_full_quota_share_cedes_everything_and_an_empty_one_nothing():
    assert np.array_equal(QuotaShare(1.0).recovery_per_claim(SIZES), SIZES)
    assert np.array_equal(QuotaShare(0.0).retained_per_claim(SIZES), SIZES)
    assert QuotaShare(0.5)
    assert not QuotaShare(0.0)


def test_quota_share_takes_its_share_of_the_office_premium():
    treaty = QuotaShare(0.4, commission=0.25)
    assert treaty.annual_premium(sum_assured=SA, office_premium=PREMIUM) == (
        0.4 * PREMIUM
    )
    assert treaty.annual_commission(
        sum_assured=SA, office_premium=PREMIUM
    ) == 0.25 * 0.4 * PREMIUM


def test_a_risk_premium_basis_charges_per_mille_of_ceded_sum_assured():
    treaty = QuotaShare(0.4, premium_basis="risk", risk_rate_per_mille=1.5)
    # 40% of 250,000 is 100,000 ceded; at 1.5 per mille that is exactly 150.
    # Written in the order the code evaluates it: `1.5 * 0.4 * SA / 1000`
    # associates the other way and lands on 150.00000000000003.
    assert treaty.annual_premium(sum_assured=SA, office_premium=PREMIUM) == 150.0
    assert treaty.annual_commission(sum_assured=SA, office_premium=PREMIUM) == 0.0


# --- surplus -------------------------------------------------------------


def test_surplus_retains_up_to_the_retention_and_cedes_the_rest():
    treaty = Surplus(50_000.0)
    assert np.array_equal(
        treaty.recovery_per_claim(np.array([10_000.0, 50_000.0, 250_000.0])),
        np.array([0.0, 0.0, 200_000.0]),
    )
    assert np.array_equal(
        treaty.retained_per_claim(np.array([10_000.0, 50_000.0, 250_000.0])),
        np.array([10_000.0, 50_000.0, 50_000.0]),
    )


def test_the_lines_cap_leaves_the_cedant_carrying_the_excess():
    """The trap this class exists to make visible. A four-line treaty on a
    retention of 50,000 takes at most 200,000, so on a 500,000 policy the
    cedant keeps 300,000 — six retentions, not one."""
    capped = Surplus(50_000.0, lines=4)
    assert capped.recovery_per_claim(500_000.0) == 200_000.0
    assert capped.retained_per_claim(500_000.0) == 300_000.0
    # Uncapped, the same policy would leave the cedant with its retention.
    assert Surplus(50_000.0).retained_per_claim(500_000.0) == 50_000.0
    # Below the cap the two treaties are the same treaty.
    for size in (10_000.0, 50_000.0, 250_000.0):
        assert capped.recovery_per_claim(size) == Surplus(
            50_000.0
        ).recovery_per_claim(size)


def test_a_zero_retention_surplus_is_a_full_quota_share():
    assert np.array_equal(
        Surplus(0.0).recovery_per_claim(SIZES),
        QuotaShare(1.0).recovery_per_claim(SIZES),
    )


def test_a_retention_above_every_policy_cedes_nothing():
    assert np.array_equal(Surplus(1e9).recovery_per_claim(SIZES),
                          np.zeros_like(SIZES))


def test_a_zero_sum_assured_cedes_nothing_rather_than_dividing_by_zero():
    treaty = Surplus(50_000.0)
    assert treaty.ceded_fraction(0.0) == 0.0
    assert treaty.recovery_per_claim(0.0) == 0.0


def test_the_ceded_fraction_rises_with_size_under_surplus():
    """What distinguishes it from a quota share: bigger policies are ceded
    more heavily, which is what a cedant worried about single large
    exposures actually wants."""
    fractions = Surplus(50_000.0).ceded_fraction(
        np.array([50_000.0, 100_000.0, 250_000.0, 1e6])
    )
    assert list(fractions) == sorted(fractions)
    assert fractions[0] == 0.0
    assert fractions[-1] > 0.9


# --- excess of loss ------------------------------------------------------


def test_excess_of_loss_pays_a_layer_of_each_claim():
    treaty = ExcessOfLoss(excess=100_000.0, limit=300_000.0)
    assert not treaty.proportional
    claims = np.array([50_000.0, 100_000.0, 250_000.0, 400_000.0, 1e6])
    assert np.array_equal(
        treaty.recovery_per_claim(claims),
        np.array([0.0, 0.0, 150_000.0, 300_000.0, 300_000.0]),
    )


def test_nothing_is_recovered_below_the_excess_point():
    treaty = ExcessOfLoss(excess=100_000.0)
    assert treaty.recovery_per_claim(99_999.99) == 0.0
    assert treaty.recovery_per_claim(100_000.0) == 0.0
    assert treaty.recovery_per_claim(100_001.0) == 1.0


def test_above_the_limit_the_excess_comes_back_to_the_cedant():
    """A limit is not a detail — it is the difference between a cover that
    caps the cedant's loss and one that caps the reinsurer's."""
    treaty = ExcessOfLoss(excess=100_000.0, limit=300_000.0)
    assert treaty.retained_per_claim(1e6) == 700_000.0
    assert ExcessOfLoss(excess=100_000.0).retained_per_claim(1e6) == 100_000.0


def test_excess_of_loss_charges_a_share_of_the_office_premium():
    treaty = ExcessOfLoss(excess=100_000.0, premium_percent=0.02)
    assert treaty.annual_premium(sum_assured=SA, office_premium=PREMIUM) == (
        0.02 * PREMIUM
    )
    assert treaty.annual_commission(sum_assured=SA, office_premium=PREMIUM) == 0.0


# --- validation ----------------------------------------------------------


@pytest.mark.parametrize("build,message", [
    (lambda: QuotaShare(1.5), r"outside \[0, 1\]"),
    (lambda: QuotaShare(-0.1), r"outside \[0, 1\]"),
    (lambda: Surplus(-1.0), "retention .* negative"),
    (lambda: Surplus(1.0, lines=0), "lines .* positive"),
    (lambda: QuotaShare(0.5, premium_basis="net"), "must be 'original' or 'risk'"),
    (lambda: QuotaShare(0.5, commission=1.5), r"commission .* outside \[0, 1\]"),
    (lambda: QuotaShare(0.5, premium_basis="risk", commission=0.2),
     "only applies on original terms"),
    (lambda: QuotaShare(0.5, risk_rate_per_mille=1.0),
     "only applies on a risk premium basis"),
    (lambda: ExcessOfLoss(excess=-1.0), "excess point .* negative"),
    (lambda: ExcessOfLoss(excess=1.0, limit=0.0), "limit .* positive"),
    (lambda: ExcessOfLoss(excess=1.0, premium_percent=2.0), r"outside \[0, 1\]"),
])
def test_a_malformed_treaty_raises(build, message):
    with pytest.raises(ValueError, match=message):
        build()


def test_a_ceding_commission_on_a_risk_basis_is_an_error_not_a_no_op():
    """It would silently value a treaty nobody wrote: on a risk premium
    basis there is no office premium being shared, so there is nothing to
    hand back."""
    with pytest.raises(ValueError, match="nothing to hand back"):
        Surplus(50_000.0, premium_basis="risk", commission=0.25)


# --- nothing moved -------------------------------------------------------


def test_no_treaty_is_the_default():
    a = assumptions()
    assert isinstance(a.reinsurance, NoReinsurance)
    assert not a.reinsurance


def test_a_projection_without_a_treaty_keeps_its_numbers():
    m = model()
    for t in range(TERM):
        assert scalar(m.reinsurance_recovery(t)) == 0.0
        assert scalar(m.reinsurance_premium(t)) == 0.0
        assert scalar(m.reinsurance_commission(t)) == 0.0
        assert np.array_equal(m.net_claims(t), m.claims(t))
    assert m.pv_reinsurance() == 0.0


# --- through a projection ------------------------------------------------


def test_a_quota_share_scales_claims_and_premium_together():
    treaty = QuotaShare(0.4, commission=0.25)
    m = model(reinsurance=treaty)
    for t in range(TERM):
        assert scalar(m.reinsurance_recovery(t)) == pytest.approx(
            0.4 * scalar(m.claims(t)), rel=1e-14
        )
        assert scalar(m.net_claims(t)) == pytest.approx(
            0.6 * scalar(m.claims(t)), rel=1e-14
        )
        assert scalar(m.reinsurance_premium(t)) == pytest.approx(
            0.4 * scalar(m.premiums(t)), rel=1e-14
        )
        assert scalar(m.reinsurance_commission(t)) == pytest.approx(
            0.25 * scalar(m.reinsurance_premium(t)), rel=1e-14
        )


def test_a_surplus_treaty_treats_two_policies_differently():
    """One treaty, one block, two answers — which is the thing a treaty
    object has to get right and a scalar assumption cannot express."""
    treaty = Surplus(50_000.0)
    small = model(mp=point(sum_assured=40_000.0), reinsurance=treaty)
    large = model(mp=point(sum_assured=500_000.0), reinsurance=treaty)
    assert scalar(small.reinsurance_recovery(3)) == 0.0
    assert scalar(large.reinsurance_recovery(3)) > 0.0
    assert scalar(large.net_claims(3)) == pytest.approx(
        scalar(large.pols_death(3)) * 50_000.0, rel=1e-13
    )


def test_the_layer_bites_only_on_the_policies_that_reach_it():
    treaty = ExcessOfLoss(excess=100_000.0, limit=300_000.0,
                          premium_percent=0.02)
    below = model(mp=point(sum_assured=80_000.0), reinsurance=treaty)
    inside = model(mp=point(sum_assured=250_000.0), reinsurance=treaty)
    above = model(mp=point(sum_assured=1e6), reinsurance=treaty)
    assert scalar(below.reinsurance_recovery(3)) == 0.0
    assert scalar(inside.reinsurance_recovery(3)) == pytest.approx(
        scalar(inside.pols_death(3)) * 150_000.0, rel=1e-13
    )
    assert scalar(above.reinsurance_recovery(3)) == pytest.approx(
        scalar(above.pols_death(3)) * 300_000.0, rel=1e-13
    )
    # The premium is charged whether or not the layer ever pays out.
    assert scalar(below.reinsurance_premium(3)) > 0.0


@pytest.mark.parametrize("freq", [1, 2, 4, 12])
def test_the_same_reinsurance_premium_is_ceded_over_a_year(freq):
    treaty = QuotaShare(0.4)
    a = assumptions(freq=freq, reinsurance=treaty)
    m = TermLife(point(), a, a.periods(TERM + 2))
    ceded = sum(scalar(m.reinsurance_premium(t)) / scalar(m.pols_if(t))
                for t in range(freq))
    assert ceded == pytest.approx(0.4 * PREMIUM, rel=1e-13)


def test_reinsurance_costs_money_on_a_profitable_block():
    """A treaty transfers risk *and* margin. On a block that expects to make
    money, ceding 40% of it on original terms with a 25% ceding commission
    gives away more profit than it saves in claims — which is why the
    commission is negotiated and not a rounding detail."""
    gross = model()
    assert gross.net_pv() > 0.0
    thin = model(reinsurance=QuotaShare(0.4, commission=0.0))
    fat = model(reinsurance=QuotaShare(0.4, commission=0.5))
    assert thin.net_pv() < gross.net_pv()
    assert fat.net_pv() > thin.net_pv()
    assert thin.pv_reinsurance() > 0.0


def test_a_full_quota_share_leaves_the_cedant_with_nothing_either_way():
    """Ceding 100% on original terms with no commission passes the whole
    contract across: no claims retained, and the entire premium gone."""
    m = model(reinsurance=QuotaShare(1.0))
    for t in range(TERM):
        assert scalar(m.net_claims(t)) == 0.0
        assert np.array_equal(m.reinsurance_premium(t), m.premiums(t))
    assert m.pv_reinsurance() == pytest.approx(
        m.pv_premiums() - m.pv_claims(), rel=1e-13
    )


# --- executors and the registry ------------------------------------------


@pytest.mark.parametrize("treaty", [
    QuotaShare(0.4, commission=0.25),
    Surplus(50_000.0, lines=4),
    ExcessOfLoss(excess=100_000.0, limit=300_000.0, premium_percent=0.02),
], ids=["quota_share", "surplus", "xol"])
def test_the_two_executors_agree_bitwise(treaty):
    """A surplus treaty makes the ceded fraction depend on the model point,
    so the batch path really is doing something the per-policy path is
    not — and they still have to agree."""
    points = from_dicts([
        {"id": f"T{i}", "age_at_entry": 35 + 5 * i, "term_years": TERM,
         "sum_assured": 40_000.0 * 4 ** i, "annual_premium": PREMIUM,
         "init_pols": 1}
        for i in range(4)
    ])
    a = assumptions(reinsurance=treaty)
    outputs = ["claims", "net_claims", "reinsurance_recovery",
               "reinsurance_premium", "reinsurance_commission"]
    interpreted = run(TermLife, points, a, TERM + 2, outputs=outputs)
    vectorized = run_vectorized(TermLife, points, a, TERM + 2, outputs=outputs)
    for name in outputs:
        assert np.array_equal(
            np.array([mp[name] for mp in interpreted.per_mp]).T,
            np.asarray(vectorized.array(name)),
        ), name


def test_the_run_registry_tells_the_treaties_apart():
    from engine.core.registry import record_run

    points = from_dicts([point(sum_assured=500_000.0).__dict__])
    ids, digests = set(), set()
    # 0.5 rather than 0.4 on purpose: at 0.4 a quota share retains exactly
    # 300,000 of a 500,000 policy, which is what the four-line surplus
    # retains too, and the two would collide. See the test below.
    for treaty in (None, QuotaShare(0.5), Surplus(50_000.0),
                   Surplus(50_000.0, lines=4),
                   ExcessOfLoss(excess=100_000.0)):
        _, record = record_run(
            TermLife, points, assumptions(reinsurance=treaty), TERM + 2,
            outputs=["net_claims"],
        )
        ids.add(record.run_id)
        digests.add(record.results_digest)
    assert len(ids) == 5
    # The two surplus treaties differ only in their cap, and on a 500,000
    # policy that cap is exactly what changes the answer.
    assert len(digests) == 5


def test_different_treaties_can_agree_on_one_policy_and_not_on_a_block():
    """Worth knowing before trusting a single-policy check: on a 500,000
    sum assured, a 40% quota share and a four-line surplus on a 50,000
    retention both leave the cedant carrying exactly 300,000. They are not
    the same treaty — they diverge on every other size — but one model
    point cannot tell them apart."""
    quota, surplus = QuotaShare(0.4), Surplus(50_000.0, lines=4)
    assert quota.retained_per_claim(500_000.0) == surplus.retained_per_claim(
        500_000.0
    ) == 300_000.0
    other = np.array([100_000.0, 250_000.0, 1e6])
    assert not np.array_equal(
        quota.retained_per_claim(other), surplus.retained_per_claim(other)
    )


def test_an_unlimited_surplus_and_a_generous_one_agree_where_they_should():
    """The capped and uncapped treaties are the same treaty below the cap,
    so a block of small policies cannot tell them apart — which is what
    makes the cap easy to get wrong in the first place."""
    from engine.core.registry import record_run

    points = from_dicts([point(sum_assured=150_000.0).__dict__])
    digests = set()
    for lines in (4, math.inf):
        _, record = record_run(
            TermLife, points,
            assumptions(reinsurance=Surplus(50_000.0, lines=lines)),
            TERM + 2, outputs=["net_claims"],
        )
        digests.add(record.results_digest)
    assert len(digests) == 1
