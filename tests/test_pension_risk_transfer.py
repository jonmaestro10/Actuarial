"""Pension risk transfer: the bulk annuity and the swap over it.

Execution plan §10, item C3. Two templates in two different equivalence
classes, and the classes are asserted rather than described:

- ``PensionBuyout`` is on the ``ValuationBasis`` chassis, so it is
  **vectorized-only** — the class ``PayoutAnnuity`` established, for the
  reason in its module docstring. Its correctness is anchored on Layer 0:
  a pensioner must reproduce ``reversionary_annuity_factor`` and a deferred
  member must reproduce ``deferred_annuity_values``, both of which are
  bitwise-parity with VPLA. Restating the annuity formula here would only
  check that it had been typed twice.
- ``LongevitySwap`` declares a ``@pool``, which puts it in RFC-061's block
  class regardless of chassis. The tests assert the refusal that class
  carries — ``run`` over a block of more than one raises ``PooledBlockError``
  — because a hedge silently struck over a pool of one is precisely the
  failure RFC-061 exists to have stopped.

What the templates add over a factor is the projection, so the rest of this
file checks the things a factor cannot: that increases land on anniversaries
and not between them, that a deferred member's spouse is paid from the
member's death rather than from a retirement the member never reached, and
that a swap struck on its own projection basis settles at exactly zero.
"""

from datetime import date

import numpy as np
import pytest

from engine.core.runner import PooledBlockError, run
from engine.core.timeaxis import TimeAxis
from engine.core.vector import run_vectorized
from engine.data.basis import ValuationBasis
from engine.data.modelpoints import ModelPoint
from engine.data.mortality import MortalityBasis
from engine.data.rates import YieldCurve
from engine.library.annuities import (
    annuity_factor,
    deferred_annuity_values,
    reversionary_annuity_factor,
)
from engine.library.longevity_swap import LongevitySwap, LongevitySwapBasis
from engine.library.pension_buyout import (
    CONTRACTS,
    PensionBuyout,
    increase_factors,
)

MIN_AGE, MAX_AGE = 18, 115
YEAR_START = 2014
INTEREST = 0.04
VALUATION = date(2021, 1, 1)
PROJ = 60

RATES = {
    sex: {
        age: min(0.0004 * 1.09 ** (age - MIN_AGE)
                 * (1.0 if sex == "M" else 0.85), 0.6)
        for age in range(MIN_AGE, MAX_AGE + 1)
    }
    for sex in ("M", "F")
}
SCALE = {sex: {age: 0.010 if sex == "M" else 0.012
               for age in range(MIN_AGE, MAX_AGE + 1)}
         for sex in ("M", "F")}
MORTALITY = MortalityBasis(RATES, year_start=YEAR_START, improvement=SCALE)

#: The fixed leg's basis: the same table with a heavier improvement scale,
#: so contracted survival is *lighter* than projected and the fixed leg is
#: the longer schedule a counterparty's margin buys.
LIGHT = MortalityBasis(
    RATES, year_start=YEAR_START,
    improvement={sex: {age: 0.030 for age in range(MIN_AGE, MAX_AGE + 1)}
                 for sex in ("M", "F")},
)


def basis(freq=1, mortality=MORTALITY):
    return ValuationBasis(mortality=mortality,
                          curve=YieldCurve([INTEREST], freq=freq))


BASE_FIELDS = {
    "id": "P1",
    "dob": date(1956, 1, 1),
    "sex": "M",
    "valuation": VALUATION,
    "annual_pension": 12_000.0,
    "init_lives": 1,
    "deferred_years": 0.0,
    "revaluation_rate": 0.0,
    "escalation_rate": 0.0,
    "spouse_percent": 0.0,
    "spouse_dob": date(1958, 6, 30),
    "spouse_sex": "F",
    "contract": "buy_out",
}


def mp(**overrides):
    unknown = set(overrides) - set(BASE_FIELDS)
    assert not unknown, f"unknown model point fields {unknown}"
    return ModelPoint(**{**BASE_FIELDS, **overrides})


SCHEME = [
    mp(id="P1"),
    mp(id="P2", dob=date(1946, 6, 30), sex="F", annual_pension=6_000.0,
       init_lives=3, escalation_rate=0.03),
    mp(id="P3", dob=date(1975, 2, 28), annual_pension=24_000.0,
       deferred_years=12.0, revaluation_rate=0.025, escalation_rate=0.02,
       spouse_percent=0.5),
    mp(id="P4", dob=date(1938, 12, 15), sex="F", annual_pension=9_000.0,
       spouse_percent=0.6, spouse_dob=date(1940, 3, 1), spouse_sex="M"),
]


def premiums(modelpoints, freq=1, proj=PROJ, mortality=MORTALITY):
    """Present value per model point, from a vectorized run."""
    result = run_vectorized(PensionBuyout, modelpoints,
                            basis(freq, mortality), proj,
                            outputs=["payments", "v"])
    payments = result.array("payments")
    v = result.array("v")
    return (payments * v[:, :1]).sum(axis=0) if v.ndim == 2 \
        else (payments * v[:, None]).sum(axis=0)


def curves(modelpoints, freq=1, proj=PROJ, mortality=MORTALITY):
    """Discount and survival over the same axis the template builds."""
    axis = TimeAxis(freq, proj + 1, VALUATION)
    val = basis(freq, mortality)
    discount = val.discount(axis)
    survival = val.survival(axis, [m.dob for m in modelpoints],
                            [m.sex for m in modelpoints])
    return discount, survival, axis


# --------------------------------------------------------------------------
# The buy-out, against Layer 0
# --------------------------------------------------------------------------

def test_a_pensioner_reproduces_the_reversionary_annuity_factor():
    """The anchor. A member already in payment with no increases is a
    reversionary annuity and nothing else, so the template's present value
    must equal `reversionary_annuity_factor` times the pension — the closed
    form that is bitwise-parity with VPLA.

    Anything else means the projection has grown a term the factor does not
    have, which is the failure this test exists to catch and the reason the
    formula is not restated here."""
    people = [mp(id="P1"), mp(id="P4", dob=date(1938, 12, 15), sex="F",
                              annual_pension=9_000.0, spouse_percent=0.6,
                              spouse_dob=date(1940, 3, 1), spouse_sex="M")]
    discount, survival, axis = curves(people)
    spouse = basis().survival(axis, [m.spouse_dob for m in people],
                              [m.spouse_sex for m in people])
    factor = reversionary_annuity_factor(
        discount, survival, spouse,
        np.array([m.spouse_percent for m in people]), axis.freq)
    expected = factor * np.array([m.annual_pension * m.init_lives
                                  for m in people])
    assert premiums(people) == pytest.approx(expected, rel=0, abs=1e-9)


def test_a_deferred_member_reproduces_the_deferred_annuity_value():
    """`deferred_annuity_values(...)[k]` is the time-0 value of the payments
    from period k onward. A member deferred k periods, with no increases and
    no spouse, is exactly that and divided by the frequency.

    Deferment is the term this template adds to the chassis, so it is
    checked against a closed form rather than against itself."""
    person = [mp(id="D", dob=date(1975, 2, 28), deferred_years=12.0,
                 annual_pension=24_000.0)]
    for freq in (1, 12):
        discount, survival, axis = curves(person, freq=freq,
                                          proj=PROJ * freq)
        k = int(round(12.0 * freq))
        expected = (deferred_annuity_values(discount, survival)[:, k] / freq
                    * person[0].annual_pension)
        got = premiums(person, freq=freq, proj=PROJ * freq)
        assert got == pytest.approx(expected, rel=0, abs=1e-9)


def test_a_member_in_payment_is_a_member_deferred_by_nothing():
    """The deferment gate has to vanish at zero, or every pensioner in the
    scheme is being valued by a code path with a different shape from the
    one the anchor above checks."""
    discount, survival, axis = curves([mp(id="Z")])
    expected = annuity_factor(discount, survival, axis.freq) * 12_000.0
    assert premiums([mp(id="Z", deferred_years=0.0)]) == pytest.approx(
        expected, rel=0, abs=1e-9)


def test_a_deferred_member_costs_less_than_the_same_pension_in_payment():
    """Direction, asserted because a sign or an off-by-one in the gate would
    still produce a plausible number. Deferring a pension both delays it and
    exposes it to the chance of never starting, so it must be cheaper before
    any revaluation is granted."""
    now = premiums([mp(id="A", dob=date(1975, 2, 28))])[0]
    later = premiums([mp(id="A", dob=date(1975, 2, 28),
                         deferred_years=12.0)])[0]
    assert 0.0 < later < now


# --------------------------------------------------------------------------
# Increases fall on anniversaries
# --------------------------------------------------------------------------

def test_increases_land_on_anniversaries_and_not_between_them():
    """`(1 + rate) ** years` with a fractional exponent pays a member three
    days past their anniversary three days' worth of a rise, and no scheme
    pays that way.

    On a monthly axis the pension must be flat for eleven months and then
    step. Asserted on the step *and* on the flat stretch, because a smooth
    curve passes through the same anniversary values."""
    factors = increase_factors(12, 37, deferred_years=[0.0],
                               revaluation_rate=[0.0],
                               escalation_rate=[0.03])[0]
    assert factors[0] == 1.0
    assert np.all(factors[:12] == 1.0)                 # flat, not creeping
    assert factors[12] == pytest.approx(1.03)
    assert np.all(factors[12:24] == factors[12])
    assert factors[24] == pytest.approx(1.03 ** 2)
    assert factors[36] == pytest.approx(1.03 ** 3)


def test_revaluation_stops_at_retirement_and_escalation_starts_there():
    """Two rates act on one pension and they must not overlap: a member
    revalued for ten years and then escalated is not the same as one
    compounding both throughout, and the difference grows with the term."""
    factors = increase_factors(1, 21, deferred_years=[10.0],
                               revaluation_rate=[0.05],
                               escalation_rate=[0.02])[0]
    assert factors[9] == pytest.approx(1.05 ** 9)
    assert factors[10] == pytest.approx(1.05 ** 10)     # at retirement
    assert factors[15] == pytest.approx(1.05 ** 10 * 1.02 ** 5)
    assert factors[20] == pytest.approx(1.05 ** 10 * 1.02 ** 10)
    # Not both rates over the whole term, which is the plausible wrong answer.
    assert factors[20] != pytest.approx(1.05 ** 20)


def test_a_pension_with_no_increases_never_moves():
    """The zero case has to be exactly one, not one within tolerance, or
    every level pension in the scheme picks up rounding it should not."""
    factors = increase_factors(12, 100, deferred_years=[5.0],
                               revaluation_rate=[0.0],
                               escalation_rate=[0.0])
    assert np.all(factors == 1.0)


def test_escalation_raises_the_premium_and_the_flat_case_matches_layer_zero():
    """An increasing pension costs more than a level one — and the level one
    still equals the closed form, so the escalation term is genuinely
    switched off at zero rather than contributing something small."""
    level = premiums([mp(id="L")])[0]
    rising = premiums([mp(id="L", escalation_rate=0.03)])[0]
    assert rising > level
    discount, survival, axis = curves([mp(id="L")])
    assert level == pytest.approx(
        annuity_factor(discount, survival, axis.freq)[0] * 12_000.0,
        rel=0, abs=1e-9)


# --------------------------------------------------------------------------
# The spouse's pension does not wait for a retirement that never came
# --------------------------------------------------------------------------

def test_the_spouse_of_a_deferred_member_is_paid_from_the_death():
    """A member who dies at 50 leaves a spouse's pension payable straight
    away. Gating the reversionary term on the member's deferment — the
    obvious thing to do, since the member's own pension is gated that way —
    would value that spouse at nothing for twelve years.

    Asserted by putting cashflow inside the deferment, where a member-only
    projection has none at all."""
    deferred = mp(id="S", dob=date(1975, 2, 28), deferred_years=12.0,
                  annual_pension=24_000.0, spouse_percent=0.5)
    result = run_vectorized(PensionBuyout, [deferred], basis(), PROJ,
                            outputs=["member_payments", "spouse_payments"])
    member = result.array("member_payments")[:, 0]
    spouse = result.array("spouse_payments")[:, 0]

    assert np.all(member[:12] == 0.0)          # nothing in deferment
    assert np.all(spouse[1:12] > 0.0)          # but the spouse is paid
    assert spouse[0] == 0.0                    # nobody has died at t=0
    assert member[12] > 0.0


def test_the_spouse_pension_tracks_the_revaluation_the_member_earned():
    """§ the module docstring: the spouse's amount follows the same
    revaluation-then-escalation path the member's pension would have. A
    spouse's pension frozen at the pension as at the valuation date would
    be a different, cheaper benefit."""
    revalued = premiums([mp(id="S", dob=date(1975, 2, 28),
                            deferred_years=12.0, spouse_percent=0.5,
                            revaluation_rate=0.025)])[0]
    frozen = premiums([mp(id="S", dob=date(1975, 2, 28),
                          deferred_years=12.0, spouse_percent=0.5)])[0]
    assert revalued > frozen


def test_no_spouse_percentage_means_no_reversionary_cashflow():
    """The masking trick that keeps the batch rectangular — a member with no
    survivor benefit standing in for their own spouse — must contribute
    exactly zero, not a small number from a spouse who is really the member.
    """
    result = run_vectorized(PensionBuyout, [mp(id="N")], basis(), PROJ,
                            outputs=["spouse_payments"])
    assert np.all(result.array("spouse_payments") == 0.0)


# --------------------------------------------------------------------------
# Buy-in and buy-out, and what the flag is not
# --------------------------------------------------------------------------

def test_a_buy_in_and_a_buy_out_price_the_same_benefits():
    """The flag records which transaction this is and changes no number.
    The difference is whose balance sheet holds the policy and which
    residual risks stay with the scheme, and neither is a projection term —
    so a template that moved a cashflow when the flag changed would be
    asserting an actuarial difference that does not exist."""
    inside = premiums([m for m in [mp(id=f"P{i}", contract="buy_in")
                                   for i in range(1)]])
    outside = premiums([mp(id="P0", contract="buy_out")])
    assert inside == pytest.approx(outside, rel=0, abs=0.0)
    assert set(CONTRACTS) == {"buy_in", "buy_out"}


def test_an_unrecognised_contract_is_refused():
    """It changes no number, which is exactly why a typo in it would never
    show up in the output. A caller who wrote something else believes
    something about the run that is not true."""
    with pytest.raises(ValueError, match="not one of"):
        run_vectorized(PensionBuyout, [mp(id="X", contract="buyout")],
                       basis(), 5)


def test_a_negative_deferment_is_refused():
    """A member already in payment has zero deferment, not a past one; their
    pension's history is in `annual_pension`. A negative would silently
    start escalation before the valuation date."""
    with pytest.raises(ValueError, match="already in payment"):
        run_vectorized(PensionBuyout, [mp(id="X", deferred_years=-3.0)],
                       basis(), 5)


def test_a_whole_scheme_prices_as_the_sum_of_its_members():
    """A bulk annuity is one premium over a book, and the book is priced as
    a batch. Members must not interact — this template has no pooled term —
    so the batch has to equal the members run one at a time."""
    together = premiums(SCHEME)
    apart = np.array([premiums([m])[0] for m in SCHEME])
    assert together == pytest.approx(apart, rel=0, abs=1e-9)


# --------------------------------------------------------------------------
# The swap: the block class, asserted rather than described
# --------------------------------------------------------------------------

def swap_basis(freq=1):
    return LongevitySwapBasis(projection=basis(freq),
                              fixed=basis(freq, LIGHT))


def test_the_swap_refuses_the_interpreted_executor_over_a_block():
    """**The equivalence class, asserted.** `net_settlement` is a `@pool`,
    so the swap belongs to RFC-061's block class: `run` builds one model per
    model point, `pool_sum` would receive a scalar, and the hedge would be
    struck over a scheme of one member with nothing in the output to say so.

    RFC-061 made that a refusal. A block of one is still allowed, because a
    pool of one is what it actually is."""
    with pytest.raises(PooledBlockError, match="net_settlement"):
        run(LongevitySwap, SCHEME, swap_basis(), PROJ)
    assert run(LongevitySwap, [SCHEME[0]], swap_basis(), PROJ) is not None
    assert "net_settlement" in LongevitySwap.pooled_names()
    assert "floating_leg" in LongevitySwap.pooled_names()


def test_a_swap_struck_on_its_own_projection_basis_settles_at_zero():
    """The zero point of the whole contract: if the fixed leg is written on
    the basis the scheme projects, there is nothing to settle in any period.

    Exactly zero rather than nearly — both legs are the same formula on the
    same survival curve, so any residue would be a term one leg has and the
    other does not."""
    at_market = LongevitySwapBasis(projection=basis(), fixed=basis())
    result = run_vectorized(LongevitySwap, SCHEME, at_market, PROJ,
                            outputs=["net_settlement", "floating_leg",
                                     "fixed_leg"])
    assert np.all(result.array("net_settlement") == 0.0)
    assert np.all(result.array("floating_leg")
                  == result.array("fixed_leg"))


def test_the_swap_pays_the_hedger_when_the_members_outlive_the_schedule():
    """The direction the scheme bought it for, and the one a sign error
    would reverse while leaving every magnitude plausible.

    The fixed leg here is written on *lighter* mortality than projected, so
    the contracted schedule runs longer than the expected benefits: the
    hedger pays more than it receives and the swap values negative at
    inception. That is the price of the hedge, not a mispricing — and the
    settlements have to be negative in the periods that drive it.

    The first period settles at exactly zero on any pair of bases, and that
    is not an edge case to skip past: everyone alive at the valuation date
    is alive at the valuation date, so the first payment is certain and
    there is nothing about it to hedge. A swap that settled at t=0 would be
    charging for a cashflow neither basis is uncertain about."""
    result = run_vectorized(LongevitySwap, SCHEME, swap_basis(), PROJ,
                            outputs=["net_settlement", "floating_leg",
                                     "fixed_leg", "v"])
    net = result.array("net_settlement")[:, 0]
    floating = result.array("floating_leg")[:, 0]
    fixed = result.array("fixed_leg")[:, 0]
    assert net[0] == 0.0 and fixed[0] == floating[0]

    paying = floating[1:] > 0.0
    assert np.all(fixed[1:][paying] > floating[1:][paying])
    assert np.all(net[1:][paying] < 0.0)

    v = result.array("v")[:, 0]
    assert float((net * v).sum()) < 0.0

    # And the other way round: a fixed leg on heavier mortality than
    # projected is a schedule the members outlive, and the swap pays.
    cheap = LongevitySwapBasis(projection=basis(freq=1, mortality=LIGHT),
                               fixed=basis())
    other = run_vectorized(LongevitySwap, SCHEME, cheap, PROJ,
                           outputs=["net_settlement", "floating_leg"])
    settle = other.array("net_settlement")[:, 0]
    live = other.array("floating_leg")[:, 0] > 0.0
    assert settle[0] == 0.0
    assert np.all(settle[1:][live[1:]] > 0.0)


def test_the_floating_leg_is_the_buy_outs_benefits_over_the_same_book():
    """Two templates, one number. The swap hedges the cashflows the bulk
    annuity prices, so the floating leg over a book must equal
    `PensionBuyout`'s total payments over that book, period by period.

    If they disagree, one of them is modelling a benefit the other is not,
    and a hedge against the wrong cashflow is worse than none."""
    swap = run_vectorized(LongevitySwap, SCHEME,
                          LongevitySwapBasis(projection=basis(),
                                             fixed=basis()),
                          PROJ, outputs=["floating_leg"])
    buyout = run_vectorized(PensionBuyout, SCHEME, basis(), PROJ,
                            outputs=["payments"])
    assert swap.array("floating_leg")[:, 0] == pytest.approx(
        buyout.array("payments").sum(axis=1), rel=0, abs=1e-9)


def test_the_two_legs_must_share_an_axis():
    """The legs settle against each other period by period. Two frequencies
    would be netting a monthly payment against an annual one, which is a
    number that looks like a settlement and is not."""
    with pytest.raises(ValueError, match="settle against each other"):
        LongevitySwapBasis(projection=basis(freq=1), fixed=basis(freq=12))


def test_the_swap_over_one_member_is_that_member_and_no_pooling_magic():
    """A pool of one is a legitimate block, and its settlement has to be the
    member's own — otherwise the reduction is doing something other than
    adding up."""
    one = [SCHEME[1]]
    result = run_vectorized(LongevitySwap, one, swap_basis(), PROJ,
                            outputs=["floating_leg", "expected_payment"])
    assert result.array("floating_leg")[:, 0] == pytest.approx(
        result.array("expected_payment")[:, 0], rel=0, abs=0.0)
