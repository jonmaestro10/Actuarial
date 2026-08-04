"""Golden tests for the GMxB template: GMDB, GMAB, GMWB and dynamic lapse.

Same layering as tests/test_stochastic.py, extended for the riders:

1. **Reduction to the seed** — switch every new rider off and
   ``UnitLinkedGMxB`` must reproduce ``UnitLinkedGMDB`` *bitwise*. The
   seed's own golden suite then keeps protecting the richer template.
2. **Zero-volatility closed forms** — the GMWB account run-down, its exact
   exhaustion year, the ratchet's running maximum, and the GMAB maturity
   payment all have closed forms with flat returns.
3. **Reference-model reconciliation** — an independent forward loop with
   every rider on at once, under real lognormal scenarios, to 1e-12.
4. **Per-scenario consistency** — an S-scenario slab must equal S
   one-scenario runs, bitwise.
5. **Structural invariants** — decrement conservation, and charges that an
   exhausted account cannot pay.
"""

import numpy as np
import pytest

from engine.core.stochastic import run_stochastic
from engine.data.assumptions import Assumptions, DynamicLapse, MortalityTable
from engine.data.modelpoints import ModelPoint, from_dicts
from engine.data.scenarios import ScenarioSet
from engine.library.unit_linked import UnitLinkedGMDB, UnitLinkedGMxB

REL = 1e-12

Q = 0.01
I = 0.03
AMC = 0.012
P = 100_000.0
TERM = 20

BASE_FIELDS = {
    "id": "V1",
    "age_at_entry": 55,
    "term_years": TERM,
    "premium": P,
    "init_pols": 1,
    "gmdb_guarantee": 0.0,
    "gmab_guarantee": 0.0,
    "gmwb_base": 0.0,
    "gmwb_rate": 0.0,
    "gmwb_ratchet": 0.0,
}


def mp(**overrides):
    """A GMxB model point with every rider off unless asked for."""
    unknown = set(overrides) - set(BASE_FIELDS)
    assert not unknown, f"unknown model point fields {unknown}"
    return ModelPoint(**{**BASE_FIELDS, **overrides})


def inert_assumptions(**overrides):
    """No decrements, no charges — the setting the closed forms are in."""
    fields = {"mortality": MortalityTable.flat(0.0), "lapse": 0.0,
              "interest": I, "amc": 0.0}
    fields.update(overrides)
    return Assumptions(**fields)


def series(result, name, t_max, i=0, s=0):
    return result.array(name)[: t_max + 1, i, s]


# --- 1. reduction to the Phase 2 seed --------------------------------------

SEED_VARS = ["pols_if", "fund_boy", "fund_eoy", "fee_income", "gmdb_claims",
             "gmdb_strain", "maturity_payments"]


def test_riders_off_reproduces_the_gmdb_seed_bitwise():
    """With GMAB and GMWB switched off, no rider fees and a flat lapse
    assumption, the full template must be indistinguishable from the seed —
    not close, identical. Dynamic lapse at zero sensitivity is exactly the
    flat rate, which is what makes this reachable."""
    assumptions = Assumptions(
        mortality=MortalityTable.flat(Q), lapse=0.02, interest=I, amc=AMC
    )
    scen = ScenarioSet.lognormal(
        n_scenarios=6, horizon=TERM + 1, drift=np.log(1 + I), vol=0.2, seed=11
    )
    seed_mps = from_dicts(
        [
            {"id": "U1", "age_at_entry": 55, "term_years": TERM, "premium": P,
             "gmdb_guarantee": P, "init_pols": 1},
            {"id": "U2", "age_at_entry": 62, "term_years": 12,
             "premium": 40_000.0, "gmdb_guarantee": 55_000.0, "init_pols": 3},
        ]
    )
    full_mps = [
        mp(id="U1", age_at_entry=55, term_years=TERM, premium=P,
           gmdb_guarantee=P, init_pols=1),
        mp(id="U2", age_at_entry=62, term_years=12, premium=40_000.0,
           gmdb_guarantee=55_000.0, init_pols=3),
    ]

    seed = run_stochastic(
        UnitLinkedGMDB, seed_mps, assumptions, scen, TERM + 1, outputs=SEED_VARS
    )
    full = run_stochastic(
        UnitLinkedGMxB, full_mps, assumptions, scen, TERM + 1, outputs=SEED_VARS
    )
    for name in SEED_VARS:
        assert np.array_equal(seed.array(name), full.array(name)), name


def test_flat_lapse_is_the_zero_sensitivity_dynamic_lapse():
    flat = DynamicLapse(0.04)
    for guarantee, account in ((100_000.0, 100_000.0), (100_000.0, 0.0),
                               (0.0, 50_000.0), (250_000.0, 1e-9)):
        assert flat.rate(guarantee, account) == 0.04


def test_conflicting_lapse_assumptions_are_rejected():
    with pytest.raises(ValueError, match="conflicts"):
        Assumptions(
            mortality=MortalityTable.flat(Q), lapse=0.05,
            dynamic_lapse=DynamicLapse(0.02),
        )


# --- 2. zero-volatility closed forms ---------------------------------------


def test_gmwb_account_runs_down_linearly_at_a_zero_return():
    """Flat 5% of a 100k benefit base against a 0% fund: the account loses
    exactly 5,000 a year and is exhausted at t = 20 to the cent. From then
    on every guaranteed withdrawal is the insurer's, in full."""
    withdrawal_rate = 0.05
    term = 30
    point = mp(term_years=term, gmwb_base=P, gmwb_rate=withdrawal_rate)
    scen = ScenarioSet.flat(0.0, n_scenarios=1, horizon=term + 1)
    result = run_stochastic(
        UnitLinkedGMxB, [point], inert_assumptions(), scen, term + 1,
        outputs=["fund_boy", "gmwb_strain", "withdrawals"],
    )
    gaw = P * withdrawal_rate
    exhausted_at = int(P / gaw)
    assert exhausted_at == 20

    fund = series(result, "fund_boy", term)
    strain = series(result, "gmwb_strain", term)
    withdrawals = series(result, "withdrawals", term)
    for t in range(term):
        assert fund[t] == pytest.approx(max(P - gaw * t, 0.0), rel=REL, abs=1e-9)
        assert withdrawals[t] == pytest.approx(gaw, rel=REL)
        want = 0.0 if t < exhausted_at else gaw
        assert strain[t] == pytest.approx(want, rel=REL, abs=1e-9), f"strain[{t}]"


def test_gmwb_account_follows_the_annuity_certain_closed_form():
    """A withdrawal ``w`` against a flat return ``r`` gives
    ``fund(t) = (P - w/r)(1+r)^t + w/r`` exactly — the annuity-certain
    run-down. Chosen so the account never exhausts inside the term."""
    r = 0.05
    withdrawal_rate = 0.07
    point = mp(gmwb_base=P, gmwb_rate=withdrawal_rate)
    scen = ScenarioSet.flat(r, n_scenarios=2, horizon=TERM)
    result = run_stochastic(
        UnitLinkedGMxB, [point], inert_assumptions(), scen, TERM,
        outputs=["fund_boy", "gmwb_strain"],
    )
    w = P * withdrawal_rate
    fund = series(result, "fund_boy", TERM - 1)
    strain = series(result, "gmwb_strain", TERM - 1)
    for t in range(TERM):
        want = (P - w / r) * (1 + r) ** t + w / r
        assert want > 0.0
        assert fund[t] == pytest.approx(want, rel=REL), f"fund_boy[{t}]"
        assert strain[t] == 0.0


@pytest.mark.parametrize("r", [-0.10, 0.0, 0.10])
def test_ratchet_locks_in_the_running_maximum(r):
    point = mp(gmwb_base=P, gmwb_rate=0.05, gmwb_ratchet=1.0)
    scen = ScenarioSet.flat(r, n_scenarios=1, horizon=TERM)
    result = run_stochastic(
        UnitLinkedGMxB, [point], inert_assumptions(), scen, TERM,
        outputs=["benefit_base", "fund_eoy"],
    )
    base = series(result, "benefit_base", TERM - 1)
    fund_eoy = series(result, "fund_eoy", TERM - 1)
    running_max = P
    for t in range(TERM):
        assert base[t] == pytest.approx(running_max, rel=REL), f"benefit_base[{t}]"
        running_max = max(running_max, fund_eoy[t])
    assert list(base) == sorted(base)


def test_ratchet_off_holds_the_benefit_base_flat():
    point = mp(gmwb_base=P, gmwb_rate=0.05, gmwb_ratchet=0.0)
    scen = ScenarioSet.lognormal(
        n_scenarios=1, horizon=TERM, drift=np.log(1 + I), vol=0.25, seed=5
    )
    result = run_stochastic(
        UnitLinkedGMxB, [point], inert_assumptions(), scen, TERM,
        outputs=["benefit_base"],
    )
    base = series(result, "benefit_base", TERM - 1)
    assert np.array_equal(base, np.full(TERM, P))


def test_ratchet_tracks_the_running_maximum_under_real_scenarios():
    point = mp(gmwb_base=P, gmwb_rate=0.05, gmwb_ratchet=1.0)
    scen = ScenarioSet.lognormal(
        n_scenarios=4, horizon=TERM, drift=np.log(1 + I), vol=0.25, seed=99
    )
    result = run_stochastic(
        UnitLinkedGMxB, [point], inert_assumptions(), scen, TERM,
        outputs=["benefit_base", "fund_eoy"],
    )
    for s in range(scen.n_scenarios):
        base = series(result, "benefit_base", TERM - 1, s=s)
        fund_eoy = series(result, "fund_eoy", TERM - 1, s=s)
        want = P
        for t in range(TERM):
            assert base[t] == pytest.approx(want, rel=REL), f"scenario {s}, t={t}"
            want = max(want, fund_eoy[t])


@pytest.mark.parametrize("r,in_the_money", [(-0.05, True), (0.06, False)])
def test_gmab_maturity_payment_closed_form(r, in_the_money):
    """At maturity survivors take the greater of the fund and the GMAB
    guarantee. With a flat return both the fund and the survivor count have
    exact forms, so the payment and the guarantee cost do too."""
    lapse = 0.02
    assumptions = Assumptions(
        mortality=MortalityTable.flat(Q), lapse=lapse, interest=I, amc=AMC
    )
    point = mp(gmab_guarantee=P)
    scen = ScenarioSet.flat(r, n_scenarios=1, horizon=TERM + 1)
    result = run_stochastic(
        UnitLinkedGMxB, [point], assumptions, scen, TERM + 1,
        outputs=["maturity_payments", "gmab_strain"],
    )
    growth = (1 + r) * (1 - AMC)
    fund_at_maturity = P * growth**TERM
    survivors = ((1 - Q) * (1 - lapse)) ** TERM
    assert (fund_at_maturity < P) is in_the_money

    payments = series(result, "maturity_payments", TERM + 1)
    strain = series(result, "gmab_strain", TERM + 1)
    for t in range(TERM + 2):
        want_payment = (
            survivors * max(fund_at_maturity, P) if t == TERM else 0.0
        )
        want_strain = (
            survivors * max(P - fund_at_maturity, 0.0) if t == TERM else 0.0
        )
        assert payments[t] == pytest.approx(want_payment, rel=REL, abs=1e-9)
        assert strain[t] == pytest.approx(want_strain, rel=REL, abs=1e-9)


# --- dynamic lapse ---------------------------------------------------------


def test_dynamic_lapse_follows_the_funded_ratio_of_the_guarantee():
    dynamic = DynamicLapse(0.05, sensitivity=0.8, floor=0.4, cap=1.6)
    assumptions = Assumptions(
        mortality=MortalityTable.flat(Q), interest=I, amc=AMC,
        dynamic_lapse=dynamic,
    )
    point = mp(gmab_guarantee=P)
    scen = ScenarioSet.lognormal(
        n_scenarios=3, horizon=TERM, drift=np.log(1 + I), vol=0.22, seed=3
    )
    result = run_stochastic(
        UnitLinkedGMxB, [point], assumptions, scen, TERM,
        outputs=["lapse_rate", "fund_eoy"],
    )
    for s in range(scen.n_scenarios):
        rates = series(result, "lapse_rate", TERM - 1, s=s)
        fund_eoy = series(result, "fund_eoy", TERM - 1, s=s)
        for t in range(TERM):
            funded = fund_eoy[t] / P
            want = 0.05 * min(max(1 + 0.8 * (funded - 1), 0.4), 1.6)
            assert rates[t] == pytest.approx(want, rel=REL), f"scenario {s}, t={t}"


@pytest.mark.parametrize("r,expect", [(-0.35, "floor"), (0.30, "cap")])
def test_dynamic_lapse_saturates_at_the_floor_and_the_cap(r, expect):
    """Deeply in-the-money guarantees make policyholders maximally sticky;
    deeply out-of-the-money ones make them maximally lapse-prone. Both are
    bounded, which is what stops a dynamic assumption running away."""
    dynamic = DynamicLapse(0.05, sensitivity=0.8, floor=0.4, cap=1.6)
    assumptions = Assumptions(
        mortality=MortalityTable.flat(Q), interest=I, amc=AMC,
        dynamic_lapse=dynamic,
    )
    point = mp(gmab_guarantee=P)
    scen = ScenarioSet.flat(r, n_scenarios=1, horizon=TERM)
    result = run_stochastic(
        UnitLinkedGMxB, [point], assumptions, scen, TERM, outputs=["lapse_rate"]
    )
    rates = series(result, "lapse_rate", TERM - 1)
    saturated = 0.05 * (0.4 if expect == "floor" else 1.6)
    assert rates[TERM - 1] == pytest.approx(saturated, rel=REL)
    assert rates[0] == pytest.approx(0.05, rel=0.35)  # still near the base early on


def test_dynamic_lapse_is_monotone_in_the_funded_ratio():
    dynamic = DynamicLapse(0.05, sensitivity=0.8, floor=0.4, cap=1.6)
    accounts = np.array([0.0, 25_000.0, 100_000.0, 175_000.0, 400_000.0])
    rates = dynamic.rate(np.full_like(accounts, P), accounts)
    assert list(rates) == sorted(rates)
    assert rates.min() == pytest.approx(0.05 * 0.4, rel=REL)
    assert rates.max() == pytest.approx(0.05 * 1.6, rel=REL)
    assert dynamic.rate(P, P) == pytest.approx(0.05, rel=REL)


def test_dynamic_lapse_rejects_impossible_shapes():
    with pytest.raises(ValueError, match="sensitivity"):
        DynamicLapse(0.05, sensitivity=-0.1)
    with pytest.raises(ValueError, match="floor <= 1 <= cap"):
        DynamicLapse(0.05, floor=1.2, cap=1.5)
    with pytest.raises(ValueError, match="100%"):
        DynamicLapse(0.8, cap=1.5)


# --- 3. reference-model reconciliation -------------------------------------

REF_VARS = ["pols_if", "fund_boy", "fund_eoy", "benefit_base", "lapse_rate",
            "fee_income", "withdrawals", "gmwb_strain", "gmdb_claims",
            "gmdb_strain", "surrenders", "maturity_payments", "gmab_strain"]


def naive_gmxb(point, a, returns, proj_len):
    """Independent forward-loop implementation of the same product spec.

    Written from the order of operations in the ``UnitLinkedGMxB``
    docstring, with its own state and no engine imports beyond the input
    objects, so a defect in the DSL machinery cannot cancel itself out.
    """
    table = a.mortality
    dl = a.dynamic_lapse
    out = {name: [] for name in REF_VARS}
    pols = float(point.init_pols)
    fund = float(point.premium)
    base = float(point.gmwb_base)
    prev_survivors = 0.0
    prev_fund_eoy = 0.0

    for t in range(proj_len + 1):
        active = t < point.term_years
        q = table.q(min(point.age_at_entry + t, table.max_age)) if active else 0.0

        grown = fund * (1 + returns[min(t, len(returns) - 1)]) if active else 0.0
        due = (
            grown * a.amc
            + point.gmdb_guarantee * a.gmdb_fee
            + point.gmab_guarantee * a.gmab_fee
            + base * a.gmwb_fee
        ) if active else 0.0
        charges = min(due, grown)
        after_charges = grown - charges
        gaw = base * point.gmwb_rate if active else 0.0
        from_fund = min(gaw, after_charges)
        fund_eoy = after_charges - from_fund

        guarantee = max(point.gmdb_guarantee, point.gmab_guarantee, base)
        funded = fund_eoy / guarantee if guarantee > 0 else 1.0
        multiplier = min(max(1 + dl.sensitivity * (funded - 1), dl.floor), dl.cap)
        lapse = dl.base * multiplier if active else 0.0

        deaths = pols * q
        lapses = pols * (1 - q) * lapse
        maturing = prev_survivors if t == point.term_years else 0.0

        out["pols_if"].append(pols)
        out["fund_boy"].append(fund)
        out["fund_eoy"].append(fund_eoy)
        out["benefit_base"].append(base)
        out["lapse_rate"].append(lapse)
        out["fee_income"].append(pols * charges)
        out["withdrawals"].append(pols * gaw)
        out["gmwb_strain"].append(pols * (gaw - from_fund))
        out["gmdb_claims"].append(deaths * max(point.gmdb_guarantee, fund_eoy))
        out["gmdb_strain"].append(deaths * max(point.gmdb_guarantee - fund_eoy, 0.0))
        out["surrenders"].append(lapses * fund_eoy)
        out["maturity_payments"].append(
            maturing * max(prev_fund_eoy, point.gmab_guarantee)
        )
        out["gmab_strain"].append(
            maturing * max(point.gmab_guarantee - prev_fund_eoy, 0.0)
        )

        prev_survivors = pols * (1 - q) * (1 - lapse)
        prev_fund_eoy = fund_eoy
        carries_on = t + 1 < point.term_years
        pols = prev_survivors if carries_on else 0.0
        fund = fund_eoy if carries_on else 0.0
        stepped = max(base, fund_eoy) if point.gmwb_ratchet else base
        base = stepped if carries_on else 0.0
    return out


REF_ASSUMPTIONS = Assumptions(
    mortality=MortalityTable({age: 0.0008 * 1.08 ** (age - 40)
                              for age in range(40, 101)}),
    interest=I,
    amc=AMC,
    gmdb_fee=0.003,
    gmab_fee=0.004,
    gmwb_fee=0.009,
    dynamic_lapse=DynamicLapse(0.06, sensitivity=0.9, floor=0.35, cap=1.4),
)

REF_MPS = [
    mp(id="R1", age_at_entry=55, term_years=TERM, premium=P,
       gmdb_guarantee=P, gmab_guarantee=P, gmwb_base=P, gmwb_rate=0.05,
       gmwb_ratchet=1.0),
    mp(id="R2", age_at_entry=48, term_years=15, premium=250_000.0, init_pols=4,
       gmdb_guarantee=250_000.0, gmab_guarantee=200_000.0, gmwb_base=250_000.0,
       gmwb_rate=0.06, gmwb_ratchet=0.0),
    mp(id="R3", age_at_entry=62, term_years=10, premium=60_000.0, init_pols=2,
       gmdb_guarantee=75_000.0, gmwb_base=60_000.0, gmwb_rate=0.08,
       gmwb_ratchet=1.0),
]

REF_PROJ_LEN = TERM + 2


def test_engine_reconciles_to_the_reference_with_every_rider_on():
    scen = ScenarioSet.lognormal(
        n_scenarios=5, horizon=REF_PROJ_LEN, drift=np.log(1 + I), vol=0.24,
        seed=2024,
    )
    result = run_stochastic(
        UnitLinkedGMxB, REF_MPS, REF_ASSUMPTIONS, scen, REF_PROJ_LEN,
        outputs=REF_VARS,
    )
    for i, point in enumerate(REF_MPS):
        for s in range(scen.n_scenarios):
            reference = naive_gmxb(
                point, REF_ASSUMPTIONS, scen.returns[s], REF_PROJ_LEN
            )
            for name in REF_VARS:
                got = result.array(name)[:, i, s]
                for t, want in enumerate(reference[name]):
                    assert got[t] == pytest.approx(want, rel=REL, abs=1e-9), (
                        f"{point.id} scenario {s} {name}[{t}]"
                    )


def rider_pvs(r):
    scen = ScenarioSet.flat(r, n_scenarios=1, horizon=REF_PROJ_LEN)
    model = UnitLinkedGMxB(
        mp=REF_MPS[0], assumptions=REF_ASSUMPTIONS, proj_len=REF_PROJ_LEN,
        scenarios=scen,
    )
    return (
        float(model.pv_fee_income()[0]),
        float(model.pv_guarantee_strain()[0]),
        float(model.pv_rider_result()[0]),
    )


def test_pv_rider_result_is_fees_less_strain():
    fees, strain, net = rider_pvs(0.04)
    assert net == pytest.approx(fees - strain, rel=REL)


def test_guarantee_cost_falls_as_the_fund_does_better():
    """The rider block's economics, stated as a shape rather than a number:
    guarantee cost is non-increasing in the fund return, vanishes once the
    account outruns every guarantee, and the net result crosses from loss to
    profit somewhere in between. A sign error anywhere in the strain
    variables breaks the ordering."""
    returns = [-0.20, -0.10, -0.02, 0.04, 0.08, 0.12]
    strains = [rider_pvs(r)[1] for r in returns]
    nets = [rider_pvs(r)[2] for r in returns]

    assert strains == sorted(strains, reverse=True)
    assert nets == sorted(nets)
    assert strains[0] > 0.0
    assert strains[-1] == 0.0  # every guarantee out of the money
    assert nets[0] < 0.0 < nets[-1]


# --- 4. per-scenario consistency -------------------------------------------


def test_slab_equals_per_scenario_runs_bitwise():
    scen = ScenarioSet.lognormal(
        n_scenarios=6, horizon=REF_PROJ_LEN, drift=np.log(1 + I), vol=0.2,
        seed=77,
    )
    slab = run_stochastic(
        UnitLinkedGMxB, REF_MPS, REF_ASSUMPTIONS, scen, REF_PROJ_LEN,
        outputs=REF_VARS,
    )
    for s in range(scen.n_scenarios):
        alone = run_stochastic(
            UnitLinkedGMxB, REF_MPS, REF_ASSUMPTIONS, scen.single(s),
            REF_PROJ_LEN, outputs=REF_VARS,
        )
        for name in REF_VARS:
            assert np.array_equal(
                slab.array(name)[:, :, s], alone.array(name)[:, :, 0]
            ), f"scenario {s} var {name}"


# --- 5. structural invariants ----------------------------------------------


def test_decrements_conserve_policies():
    scen = ScenarioSet.lognormal(
        n_scenarios=3, horizon=REF_PROJ_LEN, drift=np.log(1 + I), vol=0.2,
        seed=8,
    )
    result = run_stochastic(
        UnitLinkedGMxB, REF_MPS, REF_ASSUMPTIONS, scen, REF_PROJ_LEN,
        outputs=["pols_if", "pols_death", "pols_lapse", "pols_maturity"],
    )
    for i, point in enumerate(REF_MPS):
        for s in range(scen.n_scenarios):
            pols = result.array("pols_if")[:, i, s]
            deaths = result.array("pols_death")[:, i, s]
            lapses = result.array("pols_lapse")[:, i, s]
            maturities = result.array("pols_maturity")[:, i, s]
            for t in range(point.term_years - 1):
                assert pols[t + 1] == pytest.approx(
                    pols[t] - deaths[t] - lapses[t], rel=REL
                ), f"{point.id} scenario {s} t={t}"
            # The final in-force cohort leaves as deaths, lapses and maturities.
            last = point.term_years - 1
            assert maturities[point.term_years] == pytest.approx(
                pols[last] - deaths[last] - lapses[last], rel=REL
            )


def test_an_exhausted_account_stops_paying_for_its_riders():
    """Charges are capped at the fund, so a contract drawn to zero by its
    GMWB never goes negative and never books fee income that was not
    collected."""
    assumptions = Assumptions(
        mortality=MortalityTable.flat(0.0), lapse=0.0, interest=I, amc=AMC,
        gmwb_fee=0.02, gmdb_fee=0.01,
    )
    term = 30
    point = mp(term_years=term, gmdb_guarantee=P, gmwb_base=P, gmwb_rate=0.10)
    scen = ScenarioSet.flat(0.0, n_scenarios=1, horizon=term)
    result = run_stochastic(
        UnitLinkedGMxB, [point], assumptions, scen, term,
        outputs=["fund_boy", "fund_eoy", "charges_due", "charges_taken",
                 "fee_income"],
    )
    fund_boy = series(result, "fund_boy", term - 1)
    fund_eoy = series(result, "fund_eoy", term - 1)
    due = series(result, "charges_due", term - 1)
    taken = series(result, "charges_taken", term - 1)

    assert (fund_boy >= 0.0).all()
    assert (fund_eoy >= 0.0).all()
    assert (taken <= due + 1e-12).all()
    assert fund_boy[-1] == 0.0
    # Once the account is empty the charges are still due but uncollectable.
    assert due[-1] > 0.0
    assert taken[-1] == 0.0
    assert series(result, "fee_income", term - 1)[-1] == 0.0
