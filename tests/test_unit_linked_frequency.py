"""Sub-annual projection for the unit-linked family.

The last of the templates to become frequency-aware, and the one that
needed a modelling decision rather than only plumbing: a unit-linked
contract carries an annual management charge, annual rider fees, an annual
guaranteed withdrawal and an annual ratchet, and each of the four converts
to a finer step differently.

Two claims, and the file is organised around them.

**1. ``freq = 1`` is the identity, bit for bit.** Every per-period view
returns the annual assumption unchanged, so the whole existing GMDB/GMxB
golden suite stands as the proof that this moved nothing. The tests here
pin the conversions themselves.

**2. Each conversion preserves the annual quantity it is a conversion of.**
The AMC leaves the fund where an annual deduction would; the rider fees and
the guaranteed withdrawal pay the same total across the year; the decrements
leave the same policies in force at each anniversary. What changes is *when*
inside the year money moves — which is the entire point of running monthly.
"""

from __future__ import annotations

import numpy as np
import pytest

from engine.core.stochastic import run_stochastic
from engine.data.assumptions import Assumptions, DynamicLapse, MortalityTable
from engine.data.modelpoints import ModelPoint, from_dicts
from engine.data.scenarios import ScenarioSet
from engine.library.unit_linked import UnitLinkedGMDB, UnitLinkedGMxB

AMC = 0.012
LAPSE = 0.06
Q = 0.011
INTEREST = 0.03
TERM = 10
PREMIUM = 100_000.0


def scalar(value) -> float:
    """One number out of a model variable.

    Every read here is one policy in one scenario, but the stochastic shape
    is still ``(1,)`` and ``float()`` refuses a size-1 array that is not
    0-dimensional.
    """
    return float(np.asarray(value).reshape(-1)[0])


def gmdb_point(**kw):
    row = {"id": "U1", "age_at_entry": 55, "term_years": TERM,
           "premium": PREMIUM, "gmdb_guarantee": PREMIUM, "init_pols": 1}
    row.update(kw)
    return ModelPoint(**row)


def gmxb_point(**kw):
    row = {"id": "X1", "age_at_entry": 55, "term_years": TERM,
           "premium": PREMIUM, "gmdb_guarantee": PREMIUM,
           "gmab_guarantee": 110_000.0, "gmwb_base": PREMIUM,
           "gmwb_rate": 0.05, "gmwb_ratchet": 1.0, "init_pols": 1}
    row.update(kw)
    return ModelPoint(**row)


def flat_assumptions(freq=1, **kw):
    row = dict(mortality=MortalityTable.flat(Q), lapse=LAPSE,
               interest=INTEREST, amc=AMC, freq=freq)
    row.update(kw)
    return Assumptions(**row)


def rider_assumptions(freq=1, sensitivity=0.0, **kw):
    row = dict(mortality=MortalityTable.flat(Q), interest=INTEREST, amc=AMC,
               dynamic_lapse=DynamicLapse(LAPSE, sensitivity=sensitivity),
               gmdb_fee=0.004, gmab_fee=0.003, gmwb_fee=0.005, freq=freq)
    row.update(kw)
    return Assumptions(**row)


def zero_returns(freq, years=TERM + 2):
    """A scenario earning nothing, so charge and withdrawal mechanics can
    be read off the fund without a return path in the way."""
    return ScenarioSet.flat(0.0, 1, years * freq)


def model(cls, point, assumptions, scenarios, years=TERM + 1):
    return cls(point, assumptions, assumptions.periods(years), scenarios)


# --- the identity --------------------------------------------------------


def test_at_freq_one_the_template_is_the_annual_arithmetic_it_replaced():
    """Written out longhand rather than compared against the template's own
    output, so the assertion has something to disagree with.

    Every operand is the annual assumption itself — no conversion, no
    power — which is what makes the existing GMDB/GMxB golden suite a valid
    regression test for the frequency work.
    """
    scenarios = ScenarioSet.lognormal(1, 20, drift=0.05, vol=0.17, seed=5)
    a = flat_assumptions(freq=1)
    m = model(UnitLinkedGMDB, gmdb_point(), a, scenarios)
    for t in range(TERM):
        grown = m.fund_boy(t) * (1.0 + m.fund_ret(t)) * m.in_term(t)
        assert np.array_equal(m.fund_grown(t), grown)
        assert np.array_equal(m.charges_per_pol(t), grown * AMC)
        assert np.array_equal(m.fund_eoy(t), grown - grown * AMC)
        assert scalar(m.q_x(t)) == Q
        assert scalar(m.lapse_rate(t)) == LAPSE
        assert scalar(m.v(t)) == (1.0 + INTEREST) ** -t
        assert scalar(m.age(t)) == 55 + t
        assert np.array_equal(
            m.pols_if(t + 1),
            m.pols_if(t) * (1.0 - m.q_x(t)) * (1.0 - m.lapse_rate(t))
            * (t + 1 <= TERM - 1),
        )


def test_at_freq_one_the_gmxb_riders_are_the_annual_arithmetic_too():
    scenarios = ScenarioSet.lognormal(1, 20, drift=0.05, vol=0.17, seed=5)
    a = rider_assumptions(freq=1, sensitivity=0.9)
    m = model(UnitLinkedGMxB, gmxb_point(), a, scenarios)
    for t in range(TERM):
        assert np.array_equal(
            m.charges_due(t),
            (m.fund_grown(t) * AMC
             + PREMIUM * 0.004 + 110_000.0 * 0.003
             + m.benefit_base(t) * 0.005) * m.in_term(t),
        )
        assert np.array_equal(
            m.gaw(t), m.benefit_base(t) * 0.05 * m.in_term(t)
        )
        assert np.array_equal(
            m.lapse_rate(t),
            a.dynamic_lapse.rate(m.guarantee_value(t), m.fund_eoy(t))
            * m.in_term(t),
        )
        if t > 0:
            # The ratchet fires every period, because every period is an
            # anniversary at freq = 1.
            assert np.array_equal(
                m.benefit_base(t),
                np.maximum(m.benefit_base(t - 1), m.fund_eoy(t - 1))
                * m.in_term(t),
            )


def test_the_periodic_views_are_the_annual_assumptions_at_freq_one():
    a = flat_assumptions(freq=1)
    assert a.periodic_amc() == AMC
    assert a.periodic_lapse() == LAPSE
    assert a.to_periodic(0.1234) == 0.1234
    assert a.per_period(1_234.5) == 1_234.5
    assert a.discount(7) == (1.0 + INTEREST) ** -7


# --- the AMC conversion --------------------------------------------------


def test_twelve_monthly_charges_leave_the_fund_where_one_annual_charge_would():
    """The property the geometric conversion exists to have: splitting the
    year cannot change how much fund survives it."""
    monthly = flat_assumptions(freq=12).periodic_amc()
    assert (1.0 - monthly) ** 12 == pytest.approx(1.0 - AMC, rel=1e-15)


@pytest.mark.parametrize("freq", [1, 2, 3, 4, 6, 12])
def test_the_fund_lands_in_the_same_place_at_every_anniversary(freq):
    a = flat_assumptions(freq=freq)
    m = model(UnitLinkedGMDB, gmdb_point(), a, zero_returns(freq))
    # With no return, the fund is the premium net of whole years of AMC.
    for year in range(1, TERM):
        assert m.fund_eoy(year * freq - 1) == pytest.approx(
            PREMIUM * (1.0 - AMC) ** year, rel=1e-13
        )


def test_the_amc_is_not_converted_as_if_it_accumulated():
    """The distinction the conversion turns on, quantified.

    ``(1 + amc) ** (1/m) - 1`` is the right conversion for a rate that
    *accumulates* — an interest rate. An AMC removes a proportion of the
    fund, and converting it that way leaves twelve monthly deductions short
    of the annual charge: on 1.2% a year it collects 1.1869%, a leak of
    1.31 basis points that compounds over a contract's life.
    """
    deduction = flat_assumptions(freq=12).periodic_amc()
    accumulation = (1.0 + AMC) ** (1.0 / 12) - 1.0
    assert deduction > accumulation
    collected = 1.0 - (1.0 - accumulation) ** 12
    assert collected == pytest.approx(0.01186944, abs=1e-8)
    assert AMC - collected == pytest.approx(1.306e-4, rel=1e-3)
    # And the one actually used loses nothing.
    assert 1.0 - (1.0 - deduction) ** 12 == pytest.approx(AMC, rel=1e-14)


def test_the_same_fee_income_is_collected_over_a_year():
    """Undiscounted, the AMC collected across a policy year is the same at
    any frequency — the money moves earlier, not in greater quantity."""
    totals = []
    for freq in (1, 2, 4, 12):
        a = flat_assumptions(freq=freq)
        m = model(UnitLinkedGMDB, gmdb_point(), a, zero_returns(freq))
        totals.append(sum(scalar(m.charges_per_pol(t)) for t in range(freq)))
    assert totals[0] == pytest.approx(PREMIUM * AMC, rel=1e-14)
    for total in totals[1:]:
        assert total == pytest.approx(totals[0], rel=1e-13)


# --- rider fees and the guaranteed withdrawal ----------------------------


@pytest.mark.parametrize("freq", [1, 2, 4, 12])
def test_rider_fees_spread_rather_than_repeat(freq):
    """A rider fee is a proportion of a guaranteed amount the fee does not
    erode, so it is an annual monetary sum: ``freq`` payments of a
    ``freq``-th, not ``freq`` payments of the whole."""
    a = rider_assumptions(freq=freq)
    m = model(UnitLinkedGMxB, gmxb_point(gmwb_rate=0.0), a, zero_returns(freq))
    fees = sum(
        scalar(m.charges_due(t) - m.fund_grown(t) * a.periodic_amc())
        for t in range(freq)
    )
    expected = PREMIUM * 0.004 + 110_000.0 * 0.003 + PREMIUM * 0.005
    assert fees == pytest.approx(expected, rel=1e-13)


@pytest.mark.parametrize("freq", [1, 2, 4, 12])
def test_the_guaranteed_withdrawal_is_an_annual_entitlement(freq):
    a = rider_assumptions(freq=freq)
    m = model(UnitLinkedGMxB, gmxb_point(gmwb_ratchet=0.0), a,
              zero_returns(freq))
    paid = sum(scalar(m.gaw(t)) for t in range(freq))
    assert paid == pytest.approx(PREMIUM * 0.05, rel=1e-14)


# --- the ratchet is an anniversary event ---------------------------------


def rising_then_falling(freq, years):
    """A fund that peaks mid-year and gives it back by the anniversary."""
    steps = []
    for _ in range(years):
        steps += [0.08] * (freq // 2)
        steps += [-(1.0 - 1.0 / 1.08 ** (freq // 2)) ** (1.0)] * (freq - freq // 2)
    return ScenarioSet(np.array([steps], dtype=np.float64))


def test_the_ratchet_does_not_lock_in_twelve_high_water_marks_a_year():
    """A monthly projection of a contract with an annual ratchet must step
    the benefit base only at anniversaries. Ratcheting every period would
    capture the mid-year peak, which the policyholder was never entitled
    to."""
    freq, years = 12, 4
    a = rider_assumptions(freq=freq, amc=0.0)
    scenarios = rising_then_falling(freq, years + 2)
    m = model(UnitLinkedGMxB, gmxb_point(gmwb_rate=0.0, gmab_guarantee=0.0),
              a, scenarios, years=years)
    base = [scalar(m.benefit_base(t)) for t in range(years * freq)]
    for year in range(years):
        window = base[year * freq : (year + 1) * freq]
        assert len(set(window)) == 1, f"base moved inside policy year {year}"
    # The peak really was above the anniversary value, so this test had
    # something to catch.
    peak = max(scalar(m.fund_eoy(t)) for t in range(freq))
    assert peak > scalar(m.fund_eoy(freq - 1))
    assert base[freq] == pytest.approx(
        max(base[0], scalar(m.fund_eoy(freq - 1))), rel=1e-14
    )
    assert base[freq] < peak


def test_the_ratchet_still_steps_up_across_years():
    freq, years = 12, 4
    a = rider_assumptions(freq=freq, amc=0.0)
    scenarios = ScenarioSet.flat(0.02, 1, (years + 2) * freq)
    m = model(UnitLinkedGMxB, gmxb_point(gmwb_rate=0.0), a, scenarios,
              years=years)
    anniversaries = [scalar(m.benefit_base(y * freq)) for y in range(years)]
    assert anniversaries == sorted(anniversaries)
    assert anniversaries[-1] > anniversaries[0]


def test_a_switched_off_ratchet_never_moves():
    freq = 12
    a = rider_assumptions(freq=freq)
    scenarios = ScenarioSet.flat(0.02, 1, 15 * freq)
    m = model(UnitLinkedGMxB, gmxb_point(gmwb_ratchet=0.0), a, scenarios,
              years=TERM)
    for t in range(TERM * freq):
        assert scalar(m.benefit_base(t)) == PREMIUM


# --- decrements ----------------------------------------------------------


@pytest.mark.parametrize("freq", [1, 2, 3, 4, 6, 12])
def test_the_same_policies_are_in_force_at_every_anniversary(freq):
    """Both decrements telescope, so a finer step cannot change how many
    policies reach an anniversary — only how their exits split, which is
    RFC-004's subject rather than this one's."""
    a = flat_assumptions(freq=freq)
    m = model(UnitLinkedGMDB, gmdb_point(), a, zero_returns(freq))
    annual = 1.0
    for year in range(TERM):
        assert scalar(m.pols_if(year * freq)) == pytest.approx(annual, rel=1e-13)
        annual *= (1.0 - Q) * (1.0 - LAPSE)


@pytest.mark.parametrize("freq", [1, 4, 12])
def test_the_dynamic_multiplier_is_applied_to_the_annual_rate(freq):
    """The moneyness multiplier is defined against an annual assumption, so
    it is applied before the conversion, not after. The two differ, and the
    order is a stated choice rather than an accident of where the call
    happens to sit."""
    a = rider_assumptions(freq=freq, sensitivity=1.2)
    m = model(UnitLinkedGMxB, gmxb_point(), a,
              ScenarioSet.flat(0.06, 1, 15 * freq), years=TERM)
    for t in (0, freq, 3 * freq + 1):
        annual = a.dynamic_lapse.rate(
            m.guarantee_value(t), m.fund_eoy(t)
        )
        assert scalar(m.lapse_rate(t)) == pytest.approx(
            scalar(a.to_periodic(annual)), rel=1e-15
        )
        if freq > 1:
            wrong_order = a.dynamic_lapse.multiplier(
                m.guarantee_value(t), m.fund_eoy(t)
            ) * a.periodic_lapse()
            assert scalar(m.lapse_rate(t)) != pytest.approx(
                scalar(wrong_order), rel=1e-9
            )


# --- the two templates stay one template ---------------------------------


@pytest.mark.parametrize("freq", [1, 4, 12])
def test_switching_every_rider_off_is_still_bitwise_the_seed(freq):
    """The invariant tests/test_gmxb.py rests on, carried to sub-annual: a
    GMxB with no rider and no dynamic sensitivity *is* the GMDB seed."""
    scenarios = ScenarioSet.lognormal(6, 15 * freq, drift=0.05 / freq,
                                      vol=0.17 / np.sqrt(freq), seed=9)
    points = [{"id": "P", "age_at_entry": 55, "term_years": TERM,
               "premium": PREMIUM, "init_pols": 1}]
    seed = from_dicts([{**points[0], "gmdb_guarantee": PREMIUM}])
    full = from_dicts([{**points[0], "gmdb_guarantee": PREMIUM,
                        "gmab_guarantee": 0.0, "gmwb_base": 0.0,
                        "gmwb_rate": 0.0, "gmwb_ratchet": 0.0}])
    proj = TERM * freq
    a = flat_assumptions(freq=freq)
    # Every rider fee off too: a GMxB still charging for its GMDB is not
    # the seed, whatever its guaranteed amounts are.
    b = rider_assumptions(freq=freq, sensitivity=0.0, gmdb_fee=0.0,
                          gmab_fee=0.0, gmwb_fee=0.0)
    shared = ["pols_if", "pols_death", "fund_boy", "fund_eoy", "fee_income",
              "gmdb_claims", "gmdb_strain", "maturity_payments"]
    left = run_stochastic(UnitLinkedGMDB, seed, a, scenarios, proj,
                          outputs=shared)
    right = run_stochastic(UnitLinkedGMxB, full, b, scenarios, proj,
                           outputs=shared)
    for name in shared:
        assert np.array_equal(
            np.asarray(left.array(name)), np.asarray(right.array(name))
        ), f"{name} at freq={freq}"


# --- what a finer step actually changes ----------------------------------


def test_a_finer_step_collects_less_in_charges_from_a_thinning_block():
    """The one thing that *should* move, and it is not the timing.

    A surviving policy pays exactly the same charges over its life at any
    frequency — the conversion guarantees that, and the first assertion
    below is it. What changes is how many policies are there to pay: at
    annual steps the in-force count holds flat all year and drops at the
    anniversary, so every policy pays a full year's AMC before it can
    leave. Monthly, a policy that lapses in March stops paying in March.

    So fee income *falls* with frequency — by 3.3% here from annual to
    monthly — and that is the more correct answer, not a discretisation
    artefact. Discounting the charges earlier pushes the other way and
    loses.
    """
    per_policy, collected = [], []
    for freq in (1, 2, 4, 12):
        a = flat_assumptions(freq=freq)
        m = model(UnitLinkedGMDB, gmdb_point(), a, zero_returns(freq))
        n = TERM * freq
        per_policy.append(sum(scalar(m.charges_per_pol(t)) for t in range(n)))
        collected.append(sum(scalar(m.fee_income(t)) for t in range(n)))

    for total in per_policy[1:]:
        assert total == pytest.approx(per_policy[0], rel=1e-12)
    assert collected == sorted(collected, reverse=True)
    assert collected[-1] / collected[0] == pytest.approx(0.967, abs=1e-3)
    # Converging rather than running away: each refinement moves it less.
    gaps = [collected[i] - collected[i + 1] for i in range(3)]
    assert all(g > 0 for g in gaps)
    assert gaps[-1] < gaps[0]
