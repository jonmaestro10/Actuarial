"""Long-term care: two claim states, and the benefit that inflates.

Execution plan §10, item C4. The multi-state machinery is tested in
tests/test_multistate.py and the three-state pattern in
tests/test_income_protection.py; neither is retested here. This suite is
about what LTC adds:

- **Two claim states**, and the ``home_care → facility_care`` progression
  that a single-claim-state model cannot produce.
- **Benefit utilization**, which is per claim state because the asymmetry is
  the real structure — home-care claimants draw less than the cap, facility
  claimants draw all of it.
- **Inflation protection**, where simple and compound are nearly the same
  for a decade and nothing like each other over the life of a policy.

The invariant underneath all of it is the one a multi-state model is
checkable against: occupancy is conserved across all four states, exactly,
for the whole projection — including after premiums stop, because the person
does not cease to exist when the premium does.
"""

from __future__ import annotations

import numpy as np
import pytest

from engine.core.vector import run_vectorized
from engine.data.assumptions import Assumptions, MortalityTable
from engine.data.modelpoints import ModelPoint
from engine.data.multistate import StateSpace, TransitionMatrix
from engine.library.long_term_care import (
    INFLATION_MODES,
    LTC_STATES,
    LongTermCare,
    inflation_factors,
)

STATES = StateSpace(LTC_STATES, absorbing=["dead"])

#: Annual transitions. Deliberately alive in every cell the template reads:
#: incidence into both claim states, progression home → facility, recovery
#: from both, and mortality that rises with the level of care.
MATRIX = TransitionMatrix(
    [
        # active, home,  facility, dead
        [0.9500, 0.0300, 0.0100, 0.0100],   # active
        [0.0800, 0.7500, 0.1200, 0.0500],   # home care
        [0.0200, 0.0300, 0.8500, 0.1000],   # facility care
        [0.0000, 0.0000, 0.0000, 1.0000],   # dead
    ],
    STATES,
)


def basis(**overrides):
    return Assumptions(**{"mortality": MortalityTable.flat(0.001),
                          "interest": 0.03, "transitions": MATRIX,
                          **overrides})


BASE = {
    "id": "L1", "age_at_entry": 60, "premium_years": 20,
    "annual_premium": 2_400.0, "annual_benefit_maximum": 60_000.0,
    "init_pols": 1_000.0, "home_care_percent": 0.5,
    "home_care_utilization": 0.7, "facility_utilization": 1.0,
    "inflation_rate": 0.0, "inflation_mode": "none",
}


def mp(**overrides):
    unknown = set(overrides) - set(BASE)
    assert not unknown, f"unknown model point fields {unknown}"
    return ModelPoint(**{**BASE, **overrides})


def project(points, proj_len=40, outputs=None, **basis_kw):
    return run_vectorized(LongTermCare, points, basis(**basis_kw), proj_len,
                          outputs=outputs)


# --------------------------------------------------------------------------
# The chain itself
# --------------------------------------------------------------------------

def test_occupancy_is_conserved_across_all_four_states():
    """The invariant that makes a multi-state model checkable. Everybody is
    somewhere at the end of every period, including still where they were —
    and including after premiums stop, because the chain is not masked by
    the contract."""
    result = project([mp()], outputs=["active", "home_care",
                                      "facility_care", "dead", "lives"])
    total = result.array("lives")[:, 0]
    assert np.allclose(total, 1_000.0, rtol=0, atol=1e-9)

    parts = sum(result.array(s)[:, 0]
                for s in ("active", "home_care", "facility_care", "dead"))
    assert np.allclose(parts, total, rtol=0, atol=1e-12)


def test_the_dead_only_accumulate_and_the_claim_states_do_not():
    """`dead` is absorbing, so it is monotone — which makes it the easiest
    check that the rest is *not*. A claim state that only ever grew would be
    a decrement model wearing a Markov chain's clothes."""
    result = project([mp()], outputs=["home_care", "facility_care", "dead"])
    dead = result.array("dead")[:, 0]
    assert np.all(np.diff(dead) >= -1e-12)

    home = result.array("home_care")[:, 0]
    assert home[1] > 0.0
    # Recovery and progression both drain it, so it turns over rather than
    # accumulating: somewhere it must fall.
    assert np.any(np.diff(home) < 0.0)


def test_a_life_can_return_to_active_from_either_kind_of_care():
    """Recovery is what makes this multi-state rather than multi-decrement,
    and LTC has two states to recover *from*. Asserted on the flow, because
    a chain with the arrows present but never traversed would still conserve
    occupancy."""
    result = project([mp()], outputs=["recoveries", "home_care",
                                      "facility_care"])
    assert np.all(result.array("recoveries")[1:, 0] > 0.0)


def test_progression_is_the_flow_a_single_claim_state_cannot_produce():
    """`home_care → facility_care`. The variable that justifies the second
    claim state: it is what turns a claimant drawing a fraction of a
    fraction into one drawing the whole maximum."""
    result = project([mp()], outputs=["progression", "home_care",
                                      "incidence", "active"])
    progression = result.array("progression")[:, 0]
    home = result.array("home_care")[:, 0]
    assert progression[0] == 0.0                     # nobody is on claim yet
    assert np.all(progression[1:] > 0.0)
    assert np.allclose(progression, home * 0.12, rtol=0, atol=1e-12)

    incidence = result.array("incidence")[:, 0]
    active = result.array("active")[:, 0]
    assert np.allclose(incidence, active * 0.04, rtol=0, atol=1e-12)


# --------------------------------------------------------------------------
# Benefit utilization
# --------------------------------------------------------------------------

def test_utilization_scales_each_claim_state_independently():
    """The asymmetry is the real structure: home-care claimants use fewer
    hours than the cap allows, facility costs exceed the cap so the maximum
    binds. One utilization rate for both would price a product nobody
    sells."""
    result = project([mp()], outputs=["home_care_benefits",
                                      "facility_benefits", "home_care",
                                      "facility_care"])
    home_paid = result.array("home_care_benefits")[:, 0]
    facility_paid = result.array("facility_benefits")[:, 0]
    home = result.array("home_care")[:, 0]
    facility = result.array("facility_care")[:, 0]

    # 60,000 maximum × 50% home-care percentage × 70% utilization.
    assert np.allclose(home_paid, home * 60_000.0 * 0.5 * 0.7,
                       rtol=0, atol=1e-9)
    # Facility draws the whole maximum.
    assert np.allclose(facility_paid, facility * 60_000.0, rtol=0, atol=1e-9)


def test_full_utilization_everywhere_is_the_undiscounted_maximum():
    """The control. Set both rates to 1 and the home-care percentage to 1
    and the benefit is occupancy times the maximum — so utilization is
    genuinely switched off at 1 rather than contributing something small."""
    full = mp(home_care_utilization=1.0, facility_utilization=1.0,
              home_care_percent=1.0)
    result = project([full], outputs=["benefits", "in_claim"])
    # Relative, not absolute: the template multiplies occupancy by the
    # maximum and then by two factors of 1.0, and this side does not, so the
    # two agree to the last bit rather than to a nanounit of six million.
    assert np.allclose(result.array("benefits")[:, 0],
                       result.array("in_claim")[:, 0] * 60_000.0,
                       rtol=1e-12, atol=0.0)


def test_utilization_above_one_is_refused():
    """It would pay more than the policy maximum, which no policy does — and
    it is exactly what a cost-inflation factor mistaken for a utilization
    rate looks like."""
    for field in ("home_care_utilization", "facility_utilization"):
        with pytest.raises(ValueError, match="lies in \\[0, 1\\]"):
            project([mp(**{field: 1.2})], proj_len=5)
        with pytest.raises(ValueError, match="lies in \\[0, 1\\]"):
            project([mp(**{field: -0.1})], proj_len=5)


def test_a_home_care_percentage_above_the_facility_maximum_is_refused():
    """Home care is written as a fraction of the facility maximum. A policy
    paying more at home is expressible — as a larger maximum — and a
    percentage above 1 is more likely a rate entered as a multiple."""
    with pytest.raises(ValueError, match="lies in \\[0, 1\\]"):
        project([mp(home_care_percent=1.5)], proj_len=5)


# --------------------------------------------------------------------------
# Inflation protection
# --------------------------------------------------------------------------

def test_simple_and_compound_diverge_over_the_life_of_a_policy():
    """**The finding this template exists to make checkable.** A policy
    issued at 55 and claimed on at 85 is thirty years of rider. At 5%,
    simple reaches 2.50× and compound 4.32× — nearly double the benefit for
    the same stated rate.

    A module offering one of these and calling it inflation protection would
    be pricing a different product, which is why both are here and why the
    gap is asserted rather than described."""
    simple = inflation_factors(["simple"], [0.05], freq=1, n_periods=31)[0]
    compound = inflation_factors(["compound"], [0.05], freq=1,
                                 n_periods=31)[0]
    assert simple[30] == pytest.approx(2.50)
    assert compound[30] == pytest.approx(1.05 ** 30)
    assert compound[30] == pytest.approx(4.3219, abs=5e-4)
    assert compound[30] / simple[30] == pytest.approx(1.729, abs=1e-3)

    # Barely 2% apart after five years, which is why the choice looks cheap
    # at the point of sale and is worth 73% of the benefit at the point of
    # claim.
    assert compound[5] / simple[5] == pytest.approx(1.021, abs=1e-3)
    assert np.all(compound >= simple - 1e-12)


def test_increases_land_on_anniversaries_and_not_between_them():
    """The same rule as `pension_buyout`: a rider grants its rise on a policy
    anniversary. On a monthly axis the maximum is flat for eleven months and
    then steps, and the flat stretch is asserted as well as the step —
    a smooth curve passes through the same anniversary values."""
    factors = inflation_factors(["compound"], [0.03], freq=12,
                                n_periods=37)[0]
    assert np.all(factors[:12] == 1.0)
    assert factors[12] == pytest.approx(1.03)
    assert np.all(factors[12:24] == factors[12])
    assert factors[36] == pytest.approx(1.03 ** 3)


def test_no_rider_leaves_the_maximum_exactly_alone():
    """A policy without inflation protection is a real product, not an
    omission — and its factor has to be exactly one, or every level policy
    picks up rounding it should not."""
    assert np.all(inflation_factors(["none"], [0.05], 12, 200) == 1.0)
    assert np.all(inflation_factors(["compound"], [0.0], 12, 200) == 1.0)
    assert set(INFLATION_MODES) == {"none", "simple", "compound"}


def test_the_rider_raises_the_benefit_and_not_the_premium():
    """Inflation protection grows what the policy pays. A module that grew
    the premium with it would be modelling a different rider, and the
    premium is asserted unmoved rather than assumed to be."""
    level = project([mp()], outputs=["benefits", "premiums"])
    rising = project([mp(inflation_rate=0.05, inflation_mode="compound")],
                     outputs=["benefits", "premiums"])
    assert rising.array("benefits")[20, 0] > level.array("benefits")[20, 0]
    assert np.allclose(rising.array("premiums")[:, 0],
                       level.array("premiums")[:, 0], rtol=0, atol=1e-12)


def test_an_unknown_or_negative_rider_is_refused():
    """A negative rate is not protection, and a misspelt mode would
    otherwise fall through to no rider at all — silently pricing a policy
    without the benefit its holder paid for."""
    with pytest.raises(ValueError, match="is not one of"):
        inflation_factors(["comnpound"], [0.03], 1, 10)
    with pytest.raises(ValueError, match="does not reduce the maximum"):
        inflation_factors(["compound"], [-0.02], 1, 10)


# --------------------------------------------------------------------------
# Premiums stop; benefits do not
# --------------------------------------------------------------------------

def test_premiums_stop_at_the_paying_term_and_benefits_run_on():
    """`premium_years` is the premium-paying term, not the cover term. An
    LTC policy is guaranteed renewable and a limited-pay one stops
    collecting long before it stops paying — so one field for both, as
    `IncomeProtection` has, would make it inexpressible."""
    result = project([mp(premium_years=20)], proj_len=40,
                     outputs=["premiums", "benefits", "paying_premium"])
    premiums = result.array("premiums")[:, 0]
    benefits = result.array("benefits")[:, 0]

    assert np.all(premiums[:20] > 0.0)
    assert np.all(premiums[20:] == 0.0)
    assert np.all(benefits[21:] > 0.0)           # still paying claims


def test_a_life_on_claim_pays_no_premium():
    """Waiver of premium is the model rather than a rider on it: premiums
    are a cashflow of the active state, so a claimant stops paying by
    construction."""
    result = project([mp()], outputs=["premiums", "active"])
    assert np.allclose(result.array("premiums")[:20, 0],
                       result.array("active")[:20, 0] * 2_400.0,
                       rtol=0, atol=1e-9)


def test_the_block_prices_as_the_sum_of_its_policies():
    """No pooled term here, so a batch must equal the policies run singly —
    the check that keeps a template out of RFC-061's block class by
    accident."""
    book = [mp(id="L1"), mp(id="L2", age_at_entry=70, premium_years=10,
                            annual_benefit_maximum=90_000.0,
                            inflation_rate=0.03,
                            inflation_mode="compound"),
            mp(id="L3", home_care_utilization=0.4, home_care_percent=1.0)]
    together = project(book, outputs=["benefits"]).array("benefits")
    apart = np.column_stack([project([one], outputs=["benefits"])
                             .array("benefits")[:, 0] for one in book])
    assert np.allclose(together, apart, rtol=0, atol=1e-9)


def test_the_states_are_the_ones_the_template_names():
    """A transition matrix over different states raises on the first lookup
    rather than silently mapping one kind of care onto another."""
    assert LTC_STATES == ("active", "home_care", "facility_care", "dead")
    wrong = TransitionMatrix(
        [[0.99, 0.01], [0.0, 1.0]],
        StateSpace(["healthy", "dead"], absorbing=["dead"]))
    with pytest.raises(KeyError, match="no state"):
        run_vectorized(LongTermCare, [mp()], basis(transitions=wrong), 5,
                       outputs=["active"])
