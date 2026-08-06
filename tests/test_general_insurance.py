"""Premium liabilities: the reserve that is a residual.

Execution plan §10, item C5, second half. `engine/report/incurred_claims.py`
covers the liability for incurred claims and its variability; this is the
other half of a general insurer's balance sheet — the liability for
remaining coverage.

Two things this suite holds:

- **The unearned premium reserve is a residual, not a recursion.** Written
  less earned to date. A model carrying both a roll-forward and an
  accumulation has two representations of one quantity and they disagree
  eventually, so the identity is asserted rather than assumed.
- **The catastrophe load stays out of the loss ratio.** Rolling it in
  changes nothing about the expected cashflow, which is why it is tempting;
  the two have different distributions around the same mean, and every
  question worth asking downstream is about the distribution.
"""

from __future__ import annotations

import numpy as np
import pytest

from engine.core.runner import run
from engine.core.vector import run_vectorized
from engine.data.assumptions import Assumptions, MortalityTable
from engine.data.modelpoints import ModelPoint
from engine.library.general_insurance import (
    EARNING_PATTERNS,
    GeneralInsurance,
    earning_fractions,
)

BASIS = Assumptions(mortality=MortalityTable.flat(0.0), interest=0.03)

BASE = {
    "id": "G1", "written_premium": 1_000_000.0, "policy_term_years": 5,
    "expected_loss_ratio": 0.62, "cat_load_ratio": 0.05,
    "expense_ratio": 0.28, "init_pols": 1.0, "earning_pattern": "uniform",
}


def mp(**overrides):
    unknown = set(overrides) - set(BASE)
    assert not unknown, f"unknown model point fields {unknown}"
    return ModelPoint(**{**BASE, **overrides})


def project(points, proj_len=6, outputs=None):
    return run_vectorized(GeneralInsurance, points, BASIS, proj_len,
                          outputs=outputs)


# --------------------------------------------------------------------------
# The reserve is what is left
# --------------------------------------------------------------------------

def test_the_unearned_reserve_is_written_less_earned_to_date():
    """The identity everything else is checked against. Asserted period by
    period rather than at the end, because a pattern that earned the right
    total in the wrong order would pass an endpoint check."""
    result = project([mp()], outputs=["written_premium", "premium_earned",
                                      "unearned_premium"])
    written = result.array("written_premium")[:, 0]
    earned = result.array("premium_earned")[:, 0]
    unearned = result.array("unearned_premium")[:, 0]

    assert written[0] == 1_000_000.0 and np.all(written[1:] == 0.0)
    for t in range(len(unearned)):
        assert unearned[t] == pytest.approx(
            1_000_000.0 - earned[:t + 1].sum(), rel=0, abs=1e-9)


def test_the_reserve_runs_off_to_exactly_zero():
    """The property that makes the earning pattern a pattern rather than an
    approximation: the shares sum to exactly 1, so the reserve ends at zero
    and not at a rounding somebody has to explain."""
    for pattern in EARNING_PATTERNS:
        shares = earning_fractions(pattern, 5)
        assert shares.sum() == pytest.approx(1.0, rel=0, abs=1e-15)
        result = project([mp(earning_pattern=pattern)],
                         outputs=["unearned_premium"])
        unearned = result.array("unearned_premium")[:, 0]
        assert unearned[4] == pytest.approx(0.0, rel=0, abs=1e-9)
        assert np.all(np.diff(unearned) <= 1e-12)      # it only runs off


def test_a_front_loaded_pattern_earns_sooner_and_not_more():
    """The pattern moves *when* the premium is earned and never *how much*.
    A pattern that changed the total would be a rebate or a surcharge in
    disguise, and the totals are asserted equal to the last bit."""
    uniform = project([mp(earning_pattern="uniform")],
                      outputs=["premium_earned"]).array("premium_earned")[:, 0]
    front = project([mp(earning_pattern="front")],
                    outputs=["premium_earned"]).array("premium_earned")[:, 0]

    assert front[0] > uniform[0]
    assert front.sum() == pytest.approx(uniform.sum(), rel=0, abs=1e-9)
    assert front.sum() == pytest.approx(1_000_000.0, rel=0, abs=1e-9)


def test_an_unrecognised_earning_pattern_is_refused():
    """Defaulting to uniform would change every reserve in the projection
    and say nothing in the output — the silent-revaluation shape."""
    with pytest.raises(ValueError, match="not one of"):
        earning_fractions("straightline", 5)
    with pytest.raises(ValueError, match="covers nothing"):
        earning_fractions("uniform", 0)


# --------------------------------------------------------------------------
# The catastrophe load is its own thing
# --------------------------------------------------------------------------

def test_the_cat_load_is_reported_apart_from_the_attritional_claims():
    """Rolling the cat load into the loss ratio changes nothing about the
    expected cashflow — which is why it is tempting — and destroys the only
    information that distinguishes two costs with the same mean and
    different distributions.

    Asserted both ways: the totals agree, and the split does not."""
    split = project([mp(expected_loss_ratio=0.62, cat_load_ratio=0.05)],
                    outputs=["attritional_claims", "cat_load", "claims"])
    rolled = project([mp(expected_loss_ratio=0.67, cat_load_ratio=0.0)],
                     outputs=["attritional_claims", "cat_load", "claims"])

    # Same expected cashflow, period by period.
    assert np.allclose(split.array("claims")[:, 0],
                       rolled.array("claims")[:, 0], rtol=1e-12, atol=0)
    # And the split survives only in the first.
    assert np.all(split.array("cat_load")[:5, 0] > 0.0)
    assert np.all(rolled.array("cat_load")[:, 0] == 0.0)
    assert not np.allclose(split.array("attritional_claims")[:, 0],
                           rolled.array("attritional_claims")[:, 0])


def test_claims_follow_the_premium_earned_and_not_the_premium_written():
    """A claim arises against cover provided, and cover is provided as the
    premium earns. Applying the loss ratio to written premium would put the
    whole expected cost in period 0, against cover that has not been given
    yet."""
    result = project([mp()], outputs=["premium_earned", "attritional_claims",
                                      "written_premium"])
    earned = result.array("premium_earned")[:, 0]
    claims = result.array("attritional_claims")[:, 0]
    assert np.allclose(claims, earned * 0.62, rtol=1e-12, atol=0)
    assert claims[0] < result.array("written_premium")[0, 0] * 0.62


def test_the_combined_ratio_is_the_number_the_book_is_judged_on():
    """62% attritional, 5% cat, 28% expenses — a combined ratio of 95%, an
    underwriting profit of five points. Above 1 is a loss, and the sign is
    what the ratio exists to make obvious."""
    model = GeneralInsurance(mp(), BASIS, proj_len=6)
    assert model.combined_ratio() == pytest.approx(0.95)

    lossmaking = GeneralInsurance(mp(expected_loss_ratio=0.75), BASIS,
                                  proj_len=6)
    assert lossmaking.combined_ratio() > 1.0
    assert lossmaking.pv_underwriting_result() < 0.0


# --------------------------------------------------------------------------
# The equivalence class
# --------------------------------------------------------------------------

def test_the_interpreted_and_vectorized_executors_agree_bitwise():
    """**The class, asserted.** Annual steps, scalar assumptions, no pooled
    term — so unlike `pension_buyout` and `long_term_care` this template is
    in §1.2's per-policy bitwise class and owes the equivalence check rather
    than a statement of which executors it supports.

    Bitwise, not approximately: the two executors evaluate the same formulas
    in the same order, and anything less than exact agreement means one of
    them is not doing that."""
    points = [mp(id="G1"), mp(id="G2", policy_term_years=3,
                              earning_pattern="front",
                              written_premium=250_000.0,
                              expected_loss_ratio=0.70, cat_load_ratio=0.0)]
    outputs = ["premium_earned", "unearned_premium", "claims", "expenses",
               "underwriting_result"]
    looped = run(GeneralInsurance, points, BASIS, 6, outputs=outputs)
    batched = run_vectorized(GeneralInsurance, points, BASIS, 6,
                             outputs=outputs)
    for i, _ in enumerate(points):
        for name in outputs:
            per_policy = np.array(
                [float(np.ravel(v)[0]) for v in looped.per_mp[i][name]])
            assert np.array_equal(per_policy, batched.array(name)[:, i]), \
                (name, i)


def test_a_mixed_book_of_terms_and_patterns_projects_together():
    """Model points with different terms and different earning patterns have
    to share one projection, or a real portfolio needs one run per policy —
    and each policy's reserve still has to run off on its own term."""
    points = [mp(id="G1", policy_term_years=5),
              mp(id="G2", policy_term_years=2, earning_pattern="front"),
              mp(id="G3", policy_term_years=1)]
    unearned = project(points, outputs=["unearned_premium"]).array(
        "unearned_premium")
    assert unearned[0, 2] == pytest.approx(0.0, abs=1e-9)   # one-year policy
    assert unearned[1, 1] == pytest.approx(0.0, abs=1e-9)   # two-year policy
    assert unearned[4, 0] == pytest.approx(0.0, abs=1e-9)   # five-year policy
    assert unearned[1, 0] > 0.0                             # still running
