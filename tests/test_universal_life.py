"""Universal life: account mechanics, the corridor, the crediting floor and
the secondary guarantee.

The measurements in here are the point of the file. Several of them
contradicted what the code's first docstrings claimed, which is why they
are assertions rather than comments.
"""

from __future__ import annotations

import numpy as np
import pytest

from engine.core.graph import CyclicModelError
from engine.core.model import var
from engine.core.stochastic import run_stochastic
from engine.data.account import (
    SECTION_7702, AccountBasis, CostOfInsurance, CreditingBasis, Corridor,
    NoLapseGuarantee, SurrenderCharge,
)
from engine.data.assumptions import Assumptions, MortalityTable
from engine.data.modelpoints import ModelPoint
from engine.data.scenarios import ScenarioSet
from engine.library.universal_life import (
    OPTION_INCREASING, OPTION_LEVEL, UniversalLife,
)


# Mortality that rises with age, so the cost of insurance eventually
# overtakes a level premium — which is the whole shape of the product and
# the only way the account-exhaustion path is reachable at all.
RISING = MortalityTable(
    {age: min(0.0004 * 1.09 ** (age - 30), 0.5) for age in range(0, 121)}
)


def basis(freq=1, lapse=0.04, interest=0.04, mortality=RISING, **account):
    account.setdefault("crediting", CreditingBasis(current=0.045, guaranteed=0.02))
    return Assumptions(mortality=mortality, lapse=lapse, interest=interest,
                       freq=freq, account=AccountBasis(**account))


def point(**kw):
    fields = dict(id=1, age_at_entry=45, term_years=25, face_amount=250_000.0,
                  annual_premium=4_500.0, init_pols=1000.0)
    fields.update(kw)
    return ModelPoint(**fields)


def warmed(model, periods):
    """Evaluate ascending so the memo makes the roll-forward iterative.

    The account roll-forward is a chain ``av_eop(t) -> ... -> av_eop(t-1)``
    several frames deep, so asking for a late period cold recurses once per
    period. Every executor in the engine walks ``t`` forward; a test that
    reaches straight into period 300 is the only thing that does not.
    """
    for t in range(periods):
        model.av_eop(t)
    return model


# --- the account roll-forward ------------------------------------------


def test_account_starts_at_the_stated_in_force_value():
    m = UniversalLife(mp=point(init_av=12_345.0), assumptions=basis(), proj_len=26)
    assert m.av_boy(0) == 12_345.0


def test_new_business_starts_at_zero_without_the_optional_field():
    m = UniversalLife(mp=point(), assumptions=basis(), proj_len=26)
    assert m.av_boy(0) == 0.0


def test_the_account_is_premium_less_load_less_fee_less_coi_plus_interest():
    """The whole product in one period, longhand."""
    a = basis(premium_load=0.06, policy_fee=90.0,
              coi=CostOfInsurance(loading=1.15), corridor=Corridor.off())
    mp = point()
    m = UniversalLife(mp=mp, assumptions=a, proj_len=26)

    premium = 4_500.0
    after_premium = premium * (1 - 0.06)
    after_fee = after_premium - 90.0
    coi = 1.15 * RISING.q(45) * (250_000.0 - after_fee)
    after_charges = after_fee - coi
    expected = after_charges * 1.045

    assert m.av_after_premium(0) == pytest.approx(after_premium, rel=1e-15)
    assert m.av_after_fee(0) == pytest.approx(after_fee, rel=1e-15)
    assert m.coi_due(0) == pytest.approx(coi, rel=1e-15)
    assert m.av_eop(0) == pytest.approx(expected, rel=1e-15)


def test_the_policy_fee_is_a_charge_not_an_expense():
    """It is not inflated, because the contract does not inflate it.

    The expense basis and the account basis hold different numbers for
    different things, and a projection that inflated the contractual fee
    would be charging the policyholder for the insurer's cost base.
    """
    from engine.data.expenses import Expenses

    plain = basis(policy_fee=90.0)
    inflating = Assumptions(
        mortality=RISING, lapse=0.04, interest=0.04,
        expenses=Expenses(inflation=0.05),
        account=AccountBasis(policy_fee=90.0,
                             crediting=CreditingBasis(current=0.045, guaranteed=0.02)),
    )
    mp = point()
    a = UniversalLife(mp=mp, assumptions=plain, proj_len=26)
    b = UniversalLife(mp=mp, assumptions=inflating, proj_len=26)
    assert a.policy_fee(10) == b.policy_fee(10) == 90.0


def test_premiums_stop_when_the_model_point_says_they_do():
    m = UniversalLife(mp=point(premium_years=10), assumptions=basis(), proj_len=26)
    assert m.premium_per_pol(9) == 4_500.0
    assert m.premium_per_pol(10) == 0.0


def test_premiums_run_for_the_term_when_the_field_is_absent():
    m = UniversalLife(mp=point(), assumptions=basis(), proj_len=26)
    assert m.premium_per_pol(24) == 4_500.0
    assert m.premium_per_pol(25) == 0.0


# --- the corridor -------------------------------------------------------


def test_the_statutory_table_is_a_step_function_read_downwards():
    corridor = Corridor.section_7702()
    assert corridor.factor(0) == 2.50
    assert corridor.factor(40) == 2.50      # between breakpoints: lower one
    assert corridor.factor(41) == 2.43
    assert corridor.factor(80) == 1.05      # 75 runs to 90
    assert corridor.factor(90) == 1.05
    assert corridor.factor(91) == 1.04
    assert corridor.factor(95) == 1.00
    assert corridor.factor(120) == 1.00     # past the end: the last factor


def test_the_statutory_table_falls_monotonically_to_one():
    ages = sorted(SECTION_7702)
    factors = [SECTION_7702[age] for age in ages]
    assert factors == sorted(factors, reverse=True)
    assert factors[0] == 2.50 and factors[-1] == 1.00


def test_without_a_corridor_a_well_funded_account_carries_no_risk_at_all():
    """The reason a corridor is not optional in practice.

    A contract funded hard enough for the account to pass the face amount
    has a net amount at risk of zero from that point on — so the insurer
    charges no cost of insurance for the rest of a 40-year contract, and a
    model without the corridor shows decades of free cover.
    """
    a = basis(mortality=MortalityTable.flat(0.002), lapse=0.0,
              premium_load=0.02, policy_fee=50.0,
              crediting=CreditingBasis(current=0.06, guaranteed=0.02),
              corridor=Corridor.off())
    mp = point(age_at_entry=35, term_years=40, face_amount=100_000.0,
               annual_premium=9_000.0, init_pols=1.0)
    m = warmed(UniversalLife(mp=mp, assumptions=a, proj_len=41), 41)
    assert m.av_after_fee(10) > mp.face_amount
    assert m.nar(10) == 0.0
    assert m.coi_due(10) == 0.0
    assert all(float(m.nar(t)) == 0.0 for t in range(10, 40))


def test_the_corridor_keeps_the_risk_alive_and_multiplies_what_it_costs():
    """Measured: the same contract with and without §7702."""
    mp = point(age_at_entry=35, term_years=40, face_amount=100_000.0,
               annual_premium=9_000.0, init_pols=1.0)
    shared = dict(mortality=MortalityTable.flat(0.002), lapse=0.0,
                  premium_load=0.02, policy_fee=50.0,
                  crediting=CreditingBasis(current=0.06, guaranteed=0.02))
    off = warmed(UniversalLife(mp=mp, assumptions=basis(corridor=Corridor.off(),
                                                        **shared),
                               proj_len=41), 41)
    on = warmed(UniversalLife(mp=mp,
                              assumptions=basis(corridor=Corridor.section_7702(),
                                                **shared),
                              proj_len=41), 41)

    # The death benefit is no longer the face amount once the corridor bites.
    assert on.death_benefit(20) > 5 * mp.face_amount
    assert off.death_benefit(20) == mp.face_amount
    # And the risk it costs to carry is a multiple, not a rounding.
    assert on.pv_death_claims() > 4 * off.pv_death_claims()


def test_a_negative_account_cannot_shrink_the_death_benefit():
    """A contract whose charges have overdrawn it still pays its face."""
    a = basis(policy_fee=50_000.0, corridor=Corridor.section_7702())
    m = UniversalLife(mp=point(), assumptions=a, proj_len=26)
    assert m.av_after_fee(0) < 0.0
    assert m.death_benefit(0) == 250_000.0


def test_option_b_pays_the_account_on_top_of_the_face():
    mp_a = point(db_option=OPTION_LEVEL, init_av=50_000.0)
    mp_b = point(db_option=OPTION_INCREASING, init_av=50_000.0)
    a = basis(corridor=Corridor.off())
    level = UniversalLife(mp=mp_a, assumptions=a, proj_len=26)
    rising = UniversalLife(mp=mp_b, assumptions=a, proj_len=26)
    assert level.death_benefit(0) == 250_000.0
    assert rising.death_benefit(0) == pytest.approx(
        250_000.0 + level.av_after_fee(0), rel=1e-15
    )
    assert rising.nar(0) == pytest.approx(250_000.0, rel=1e-12)


def test_the_option_defaults_to_level_when_the_field_is_absent():
    a = basis(corridor=Corridor.off())
    without = UniversalLife(mp=point(init_av=50_000.0), assumptions=a, proj_len=26)
    stated = UniversalLife(mp=point(init_av=50_000.0, db_option=OPTION_LEVEL),
                           assumptions=a, proj_len=26)
    assert without.death_benefit(0) == stated.death_benefit(0)


# --- the crediting floor ------------------------------------------------


def test_a_declared_rate_cannot_be_set_below_its_own_guarantee():
    with pytest.raises(ValueError, match="cannot credit less"):
        CreditingBasis(current=0.01, guaranteed=0.02)


def test_the_two_crediting_modes_coincide_at_the_break_even_return():
    """Portfolio mode credits what a declared basis would when the assets
    earn exactly the declared rate plus the spread."""
    declared = CreditingBasis(current=0.04, guaranteed=0.02)
    portfolio = CreditingBasis(guaranteed=0.02, spread=0.01, mode="portfolio")
    assert portfolio.credited(0.05) == pytest.approx(declared.credited(0.05))


def test_the_floor_is_worth_exactly_nothing_deterministically_above_it():
    a = basis(interest=0.05, premium_load=0.05, policy_fee=60.0,
              coi=CostOfInsurance(loading=1.1),
              crediting=CreditingBasis(guaranteed=0.02, spread=0.01,
                                       mode="portfolio"),
              corridor=Corridor.section_7702())
    m = warmed(UniversalLife(mp=point(), assumptions=a, proj_len=26), 26)
    assert m.credited_rate(0) == pytest.approx(0.04)
    assert m.pv_guarantee_cost() == 0.0


def test_the_floor_is_a_strip_of_annual_options_and_costs_real_money():
    """The headline of this module.

    A minimum guaranteed *crediting rate* is not one option over the life of
    the contract; it is one option per period, and it resets every period
    whatever the account has already earned. Deterministically it is worth
    zero. Across a distribution it adds hundreds of basis points a year to
    what the policyholder receives, and rising in volatility.
    """
    a = basis(interest=0.05, premium_load=0.05, policy_fee=60.0,
              coi=CostOfInsurance(loading=1.1),
              crediting=CreditingBasis(guaranteed=0.02, spread=0.01,
                                       mode="portfolio"),
              corridor=Corridor.section_7702())
    mp = point(face_amount=200_000.0, annual_premium=5_000.0, term_years=30)
    uplifts = []
    for vol in (0.05, 0.10, 0.20):
        scenarios = ScenarioSet.lognormal(400, 31, drift=np.log(1.05),
                                          vol=vol, seed=11)
        res = run_stochastic(UniversalLife, [mp], a, scenarios, 31,
                             outputs=["credited_rate", "credited_rate_unfloored"])
        floored = res.array("credited_rate")[:31, 0, :]
        plain = res.array("credited_rate_unfloored")[:31, 0, :]
        uplifts.append(floored.mean() - plain.mean())
        assert (floored >= plain - 1e-15).all()

    # Worth real money at every volatility, and monotone in it.
    assert uplifts[0] > 0.008                 # >80 bp even at 5% vol
    assert uplifts == sorted(uplifts)
    assert uplifts[2] > 5 * uplifts[0]        # 20% vol costs 5x what 5% does


def test_the_guarantee_cost_is_zero_wherever_the_floor_did_not_bite():
    a = basis(interest=0.05,
              crediting=CreditingBasis(guaranteed=0.02, spread=0.0,
                                       mode="portfolio"))
    scenarios = ScenarioSet(np.array([[0.10] * 11, [-0.10] * 11]))
    res = run_stochastic(UniversalLife, [point()], a, scenarios, 10,
                         outputs=["guarantee_cost_per_pol", "credited_rate"])
    good = res.array("credited_rate")[:10, 0, 0]
    bad = res.array("credited_rate")[:10, 0, 1]
    assert (good == 0.10).all()
    assert (bad == 0.02).all()
    assert (res.array("guarantee_cost_per_pol")[:10, 0, 0] == 0.0).all()
    assert (res.array("guarantee_cost_per_pol")[1:10, 0, 1] > 0.0).all()


def test_a_declared_basis_prices_no_option_in_any_scenario():
    a = basis(crediting=CreditingBasis(current=0.045, guaranteed=0.02))
    scenarios = ScenarioSet.lognormal(50, 11, drift=np.log(1.05), vol=0.25, seed=3)
    res = run_stochastic(UniversalLife, [point()], a, scenarios, 10,
                         outputs=["guarantee_cost", "credited_rate"])
    assert (res.array("credited_rate")[:10] == 0.045).all()
    assert (res.array("guarantee_cost")[:10] == 0.0).all()


# --- lapse for non-payment ----------------------------------------------


def test_a_level_premium_contract_eventually_cannot_pay_for_itself():
    """Rising mortality against a level premium: the account funds the gap
    until it cannot, and the date it fails is an output."""
    a = basis(premium_load=0.06, policy_fee=90.0, lapse=0.03,
              coi=CostOfInsurance(loading=1.15),
              crediting=CreditingBasis(current=0.035, guaranteed=0.02),
              corridor=Corridor.section_7702())
    mp = point(age_at_entry=55, term_years=40, face_amount=500_000.0,
               annual_premium=6_000.0)
    m = warmed(UniversalLife(mp=mp, assumptions=a, proj_len=41), 41)
    dry = [t for t in range(41) if float(m.av_exhausted(t))]
    assert dry, "the contract should run out of account"
    assert 20 < dry[0] < 30
    assert float(m.pols_if(dry[0] + 1)) == 0.0


def test_lapse_for_non_payment_is_absorbing():
    """A contract off the book stays off it.

    Written as a running product rather than a per-period test, so a later
    period in which the account happens to look solvent cannot resurrect a
    policy that has already gone.
    """
    a = basis(premium_load=0.06, policy_fee=90.0, lapse=0.03,
              coi=CostOfInsurance(loading=1.15),
              crediting=CreditingBasis(current=0.035, guaranteed=0.02),
              corridor=Corridor.section_7702())
    mp = point(age_at_entry=55, term_years=40, face_amount=500_000.0,
               annual_premium=6_000.0)
    m = warmed(UniversalLife(mp=mp, assumptions=a, proj_len=41), 41)
    flags = [float(m.in_force_av(t)) for t in range(41)]
    assert flags[0] == 1.0
    assert 0.0 in flags
    first_zero = flags.index(0.0)
    assert all(f == 0.0 for f in flags[first_zero:])


def test_voluntary_lapse_and_lapse_for_non_payment_are_different_events():
    """One surrenders an account and is paid its cash value; the other walks
    away from an account that is already empty. Merging them would pay a
    cash value that does not exist."""
    a = basis(premium_load=0.06, policy_fee=90.0, lapse=0.03,
              coi=CostOfInsurance(loading=1.15),
              crediting=CreditingBasis(current=0.035, guaranteed=0.02),
              corridor=Corridor.section_7702())
    mp = point(age_at_entry=55, term_years=40, face_amount=500_000.0,
               annual_premium=6_000.0)
    m = warmed(UniversalLife(mp=mp, assumptions=a, proj_len=41), 41)
    dry = next(t for t in range(41) if float(m.av_exhausted(t)))
    assert float(m.surrenders(dry - 1)) > 0.0      # voluntary, paid
    assert float(m.surrenders(dry)) == 0.0         # nothing left to pay


# --- surrender charges ---------------------------------------------------


def test_no_surrender_charge_makes_the_cash_value_the_account_exactly():
    m = UniversalLife(mp=point(init_av=40_000.0), assumptions=basis(), proj_len=26)
    assert m.cash_value(0) == m.av_eop(0)


def test_a_declining_schedule_runs_off_to_nothing():
    schedule = SurrenderCharge.declining(0.10, 10)
    assert schedule.factor(0) == pytest.approx(0.10)
    assert schedule.factor(9) == pytest.approx(0.01)
    assert schedule.factor(10) == 0.0
    assert schedule.factor(30) == 0.0


def test_the_surrender_charge_bites_early_and_not_late():
    a = basis(surrender_charge=SurrenderCharge.declining(0.10, 10))
    m = warmed(UniversalLife(mp=point(init_av=40_000.0), assumptions=a,
                             proj_len=26), 26)
    assert m.cash_value(0) == pytest.approx(0.90 * m.av_eop(0), rel=1e-15)
    assert m.cash_value(12) == m.av_eop(12)


def test_a_surrender_charge_above_the_account_is_refused():
    with pytest.raises(ValueError, match="outside"):
        SurrenderCharge([0.1, 1.5])


# --- the secondary guarantee --------------------------------------------


def _nlg_pair():
    shared = dict(premium_load=0.06, policy_fee=90.0, lapse=0.03,
                  coi=CostOfInsurance(loading=1.15),
                  crediting=CreditingBasis(current=0.035, guaranteed=0.02),
                  corridor=Corridor.section_7702())
    guarantee = NoLapseGuarantee(
        years=40, premium_load=0.0, policy_fee=0.0,
        coi=CostOfInsurance(loading=0.55),
        crediting=CreditingBasis(current=0.06, guaranteed=0.06),
    )
    mp = point(age_at_entry=55, term_years=40, face_amount=500_000.0,
               annual_premium=6_000.0)
    off = warmed(UniversalLife(mp=mp, assumptions=basis(**shared), proj_len=41), 41)
    on = warmed(UniversalLife(
        mp=mp, assumptions=basis(no_lapse_guarantee=guarantee, **shared),
        proj_len=41), 41)
    return off, on


def test_with_the_guarantee_off_the_shadow_account_is_identically_zero():
    off, _ = _nlg_pair()
    assert all(float(off.shadow_eop(t)) == 0.0 for t in range(41))
    assert all(float(off.guarantee_holding(t)) == 0.0 for t in range(41))
    assert off.pv_nlg_claims() == 0.0


def test_the_guarantee_keeps_the_contract_alive_after_the_account_is_gone():
    off, on = _nlg_pair()
    dry = next(t for t in range(41) if float(on.av_exhausted(t)))
    assert float(off.in_force_av(dry + 1)) == 0.0
    assert float(on.in_force_av(dry + 1)) == 1.0
    assert float(on.in_force_av(40)) == 1.0
    assert float(on.av_eop(dry)) == 0.0          # alive on an empty account
    assert float(on.shadow_eop(dry)) > 0.0


def test_the_guarantee_is_worth_most_where_the_cover_is_worth_most():
    """Measured, and the reason a no-lapse guarantee is priced separately.

    The lapse it prevents does not happen at a random time: rising mortality
    against a level premium exhausts the account precisely at the ages the
    death benefit is most likely to be claimed. So the guarantee is not a
    marginal extension of a contract — it restores the half of the cover
    that the product's own arithmetic was about to destroy.
    """
    off, on = _nlg_pair()
    base, guaranteed = float(off.pv_death_claims()), float(on.pv_death_claims())
    assert guaranteed > 1.4 * base
    # And the claims paid on empty accounts are the bulk of the difference.
    assert float(on.pv_nlg_claims()) > 0.9 * (guaranteed - base)


def test_the_guarantee_only_counts_as_holding_when_the_account_could_not_pay():
    """A positive shadow account beside a healthy real one is doing nothing,
    and must not be reported as doing something."""
    _, on = _nlg_pair()
    assert float(on.shadow_eop(5)) > 0.0
    assert float(on.av_exhausted(5)) == 0.0
    assert float(on.guarantee_holding(5)) == 0.0


def test_a_shadow_account_that_cannot_fund_itself_guarantees_nothing():
    """The guarantee is met by a stated premium.

    Charge the shadow account more than the premium supports and it drains
    long before the real account does — here it dies in year 4 while the
    real one lasts to year 25, so by the time the guarantee is needed there
    is nothing left of it. That is the correct answer rather than a failure,
    and it is the mechanism by which a no-lapse guarantee lapses.
    """
    guarantee = NoLapseGuarantee(years=40, coi=CostOfInsurance(loading=3.0),
                                 crediting=CreditingBasis(current=0.06,
                                                          guaranteed=0.06))
    a = basis(premium_load=0.06, policy_fee=90.0, lapse=0.03,
              coi=CostOfInsurance(loading=1.15),
              crediting=CreditingBasis(current=0.035, guaranteed=0.02),
              corridor=Corridor.section_7702(),
              no_lapse_guarantee=guarantee)
    mp = point(age_at_entry=55, term_years=40, face_amount=500_000.0,
               annual_premium=6_000.0)
    m = warmed(UniversalLife(mp=mp, assumptions=a, proj_len=41), 41)
    exhausted = next(t for t in range(41) if float(m.shadow_eop(t)) == 0.0)
    dry = next(t for t in range(41) if float(m.av_exhausted(t)))
    assert exhausted < dry / 4
    assert m.pv_nlg_claims() == 0.0
    assert float(m.in_force_av(dry + 1)) == 0.0


def test_a_guaranteed_coi_loading_below_the_current_one_is_refused():
    with pytest.raises(ValueError, match="ceiling"):
        CostOfInsurance(loading=1.2, guaranteed_loading=1.0)


# --- order of operations -------------------------------------------------


def test_striking_the_death_benefit_after_the_coi_is_circular_and_says_so():
    """The one alternative order that is not a convention but a mistake.

    The engine's cycle detector names the loop rather than iterating to a
    fixed point nobody asked for — which is what makes "fee, then benefit,
    then COI" a checkable statement about this template instead of a
    comment in it.
    """

    class Circular(UniversalLife):
        @var
        def death_benefit(self, t):
            account = np.maximum(self.av_after_charges(t), 0.0)
            return np.maximum(self.mp.face_amount,
                              self.corridor_factor(t) * account)

    m = Circular(mp=point(), assumptions=basis(corridor=Corridor.section_7702()),
                 proj_len=5)
    with pytest.raises(CyclicModelError, match="depends on itself"):
        m.av_eop(0)


# --- running sub-annually -------------------------------------------------


def test_freq_one_is_the_crediting_conversion_unchanged_bit_for_bit():
    crediting = CreditingBasis(current=0.045, guaranteed=0.02)
    assert crediting.credited(0.0, freq=1) == 0.045
    portfolio = CreditingBasis(guaranteed=0.02, spread=0.01, mode="portfolio")
    assert portfolio.credited(0.07, freq=1) == 0.07 - 0.01


def test_the_periodic_crediting_rate_compounds_back_to_the_annual_one():
    """To within the round trip's own floating point, which is not zero.

    ``(1 + g) ** (1/12)`` compounded twelve times misses ``1 + g`` by about
    five ulps. It is fifteen orders of magnitude inside anything that
    matters and it is still not the bitwise identity that ``freq = 1``
    gives, so the test says which one it is holding to.
    """
    crediting = CreditingBasis(current=0.045, guaranteed=0.0)
    monthly = float(crediting.credited(0.0, freq=12))
    assert (1.0 + monthly) ** 12 != 1.045
    assert (1.0 + monthly) ** 12 == pytest.approx(1.045, rel=5e-15)


def test_the_spread_is_a_quoted_annual_deduction_taken_in_slices():
    portfolio = CreditingBasis(guaranteed=0.0, spread=0.012, mode="portfolio")
    assert portfolio.credited(0.05, freq=12) == pytest.approx(0.05 - 0.001)


def test_a_single_premium_account_lands_within_a_rounding_of_annual():
    """No premium timing and no exits, so only the crediting conversion is
    left — and it costs a few parts in 1e14 over 25 years."""
    a_year, a_month = (
        Assumptions(mortality=MortalityTable.flat(0.0), lapse=0.0, interest=0.04,
                    freq=f,
                    account=AccountBasis(crediting=CreditingBasis(current=0.045)))
        for f in (1, 12)
    )
    mp = point(annual_premium=0.0, init_av=100_000.0)
    annual = warmed(UniversalLife(mp=mp, assumptions=a_year, proj_len=26), 26)
    monthly = warmed(UniversalLife(mp=mp, assumptions=a_month, proj_len=301), 301)
    assert monthly.av_eop(299) == pytest.approx(annual.av_eop(24), rel=1e-13)


def test_an_annual_step_invests_the_whole_years_premium_on_day_one():
    """Measured: the premium-timing half of the frequency effect.

    A regular-premium account run annually receives twelve months of
    premium at the start of the year and earns a year's interest on all of
    it. Monthly, most of it arrives later. Nothing here is an approximation
    of the other — they are different contracts, and the annual one is the
    one that does not exist.
    """
    def account(freq):
        a = Assumptions(mortality=MortalityTable.flat(0.0), lapse=0.0,
                        interest=0.04, freq=freq,
                        account=AccountBasis(
                            crediting=CreditingBasis(current=0.045)))
        m = warmed(UniversalLife(mp=point(), assumptions=a,
                                 proj_len=25 * freq + 1), 25 * freq + 1)
        return float(m.av_eop(25 * freq - 1))

    annual, monthly = account(1), account(12)
    assert monthly < annual
    assert 0.015 < (annual - monthly) / annual < 0.025      # about 2%


def test_a_finer_step_collects_less_from_policies_that_leave_mid_year():
    """The other half, and the same finding the unit-linked template made.

    An annual step charges a full year to a policy that surrendered in
    March. A monthly one stops when the policy does.
    """
    def charges(freq):
        a = Assumptions(mortality=MortalityTable.flat(0.0), lapse=0.08,
                        interest=0.04, freq=freq,
                        account=AccountBasis(
                            premium_load=0.06,
                            crediting=CreditingBasis(current=0.045)))
        m = UniversalLife(mp=point(), assumptions=a, proj_len=25 * freq + 1)
        for t in range(25 * freq + 1):
            m.charge_income(t)
        return float(m.pv_charge_income())

    annual, monthly = charges(1), charges(12)
    assert monthly < annual
    assert 0.04 < (annual - monthly) / annual < 0.07       # about 5%


# --- executors and plumbing ----------------------------------------------


def test_the_interpreted_and_stochastic_executors_agree_on_a_flat_scenario():
    a = basis(interest=0.05, premium_load=0.05, policy_fee=60.0,
              coi=CostOfInsurance(loading=1.1),
              crediting=CreditingBasis(guaranteed=0.0, spread=0.0,
                                       mode="portfolio"),
              corridor=Corridor.section_7702())
    mp = point()
    interpreted = warmed(UniversalLife(mp=mp, assumptions=a, proj_len=26), 26)
    scenarios = ScenarioSet.flat(a.period_accumulation() - 1.0, 3, 26)
    res = run_stochastic(UniversalLife, [mp], a, scenarios, 25,
                         outputs=["av_eop", "death_claims", "charge_income"])
    for name, series in (("av_eop", interpreted.av_eop),
                         ("death_claims", interpreted.death_claims),
                         ("charge_income", interpreted.charge_income)):
        stacked = res.array(name)[:25, 0, :]
        for t in range(25):
            assert stacked[t] == pytest.approx(float(series(t)), rel=1e-13)


def test_the_basis_fingerprints_everything_that_can_move_a_number():
    from engine.core.fingerprint import fingerprint

    quiet = basis()
    loud = basis(premium_load=0.07)
    assert fingerprint(quiet) != fingerprint(loud)
    assert fingerprint(quiet) == fingerprint(basis())


def test_a_corridor_needs_at_least_one_factor():
    with pytest.raises(ValueError, match="at least one"):
        Corridor({})


def test_a_negative_corridor_factor_is_refused():
    with pytest.raises(ValueError, match="negative"):
        Corridor({0: -1.0})


def test_an_unknown_crediting_mode_is_refused():
    with pytest.raises(ValueError, match="crediting mode"):
        CreditingBasis(mode="whatever")
