"""Golden tests for the stochastic executor and the unit-linked GMDB template.

Four layers of defence:

1. **Zero-volatility closed forms** — with flat returns the fund and charges
   have exact formulas; the stochastic machinery must reproduce them.
2. **Per-scenario consistency** — running S scenarios in one slab must equal
   running each scenario alone, bitwise. Catches cross-scenario leakage.
3. **Martingale test** — under risk-neutral lognormal returns with no
   decrements or charges, the discounted expected fund equals the premium
   (statistical tolerance, pinned seed).
4. **Seed determinism** — same seed, same scenario set, same results, bitwise.
"""

import numpy as np
import pytest

from engine.core.stochastic import run_stochastic
from engine.data.assumptions import Assumptions, MortalityTable
from engine.data.modelpoints import ModelPoint, from_dicts
from engine.data.scenarios import ScenarioSet
from engine.library.unit_linked import UnitLinkedGMDB

REL = 1e-12

Q = 0.01
I = 0.03
AMC = 0.012
N = 20
P = 100_000.0

MPS = from_dicts(
    [
        {"id": "U1", "age_at_entry": 45, "term_years": N, "premium": P,
         "gmdb_guarantee": P, "init_pols": 1},
        {"id": "U2", "age_at_entry": 55, "term_years": 10, "premium": 50_000.0,
         "gmdb_guarantee": 60_000.0, "init_pols": 2},
    ]
)

ASSUMPTIONS = Assumptions(
    mortality=MortalityTable.flat(Q), lapse=0.02, interest=I, amc=AMC
)

VARS = ["pols_if", "fund_boy", "fund_eoy", "fee_income", "gmdb_claims", "gmdb_strain"]


def test_zero_vol_fund_closed_form():
    r = 0.05
    scen = ScenarioSet.flat(r, n_scenarios=3, horizon=N)
    result = run_stochastic(UnitLinkedGMDB, MPS[:1], ASSUMPTIONS, scen, N, outputs=VARS)
    fund_boy = result.array("fund_boy")[:, 0, 0]
    growth = (1 + r) * (1 - AMC)
    for t in range(N):
        assert fund_boy[t] == pytest.approx(P * growth**t, rel=REL)


def test_zero_vol_gmdb_strain_matches_guarantee_shortfall():
    # A -20%/year fund collapses below the return-of-premium guarantee, so
    # the strain per death must equal guarantee minus fund exactly.
    r = -0.20
    scen = ScenarioSet.flat(r, n_scenarios=1, horizon=N)
    result = run_stochastic(UnitLinkedGMDB, MPS[:1], ASSUMPTIONS, scen, N, outputs=VARS)
    fund_eoy = result.array("fund_eoy")[:, 0, 0]
    strain = result.array("gmdb_strain")[:, 0, 0]
    claims = result.array("gmdb_claims")[:, 0, 0]
    pols_death = result.array("pols_if")[:, 0, 0] * Q
    for t in range(N):
        assert fund_eoy[t] < P
        assert strain[t] == pytest.approx(pols_death[t] * (P - fund_eoy[t]), rel=REL)
        assert claims[t] == pytest.approx(pols_death[t] * P, rel=REL)


def test_slab_equals_per_scenario_runs_bitwise():
    scen = ScenarioSet.lognormal(
        n_scenarios=8, horizon=25, drift=np.log(1 + I), vol=0.18, seed=42
    )
    slab = run_stochastic(UnitLinkedGMDB, MPS, ASSUMPTIONS, scen, 25, outputs=VARS)
    for s in range(scen.n_scenarios):
        alone = run_stochastic(
            UnitLinkedGMDB, MPS, ASSUMPTIONS, scen.single(s), 25, outputs=VARS
        )
        for name in VARS:
            assert np.array_equal(
                slab.array(name)[:, :, s], alone.array(name)[:, :, 0]
            ), f"scenario {s} var {name}"


def test_discounted_fund_is_martingale_under_risk_neutral_scenarios():
    # No decrements, no charges, term = horizon: E[v^t * fund(t)] = premium.
    n_scen = 20_000
    horizon = 15
    mp = ModelPoint(
        age_at_entry=45, term_years=horizon, premium=P, gmdb_guarantee=0.0,
        init_pols=1,
    )
    assumptions = Assumptions(
        mortality=MortalityTable.flat(0.0), lapse=0.0, interest=I, amc=0.0
    )
    scen = ScenarioSet.lognormal(
        n_scenarios=n_scen, horizon=horizon, drift=np.log(1 + I), vol=0.15, seed=7
    )
    result = run_stochastic(
        UnitLinkedGMDB, [mp], assumptions, scen, horizon, outputs=["fund_boy"]
    )
    v = 1 / (1 + I)
    for t in (1, 5, 10, horizon - 1):
        discounted_mean = result.array("fund_boy")[t, 0, :].mean() * v**t
        assert discounted_mean == pytest.approx(P, rel=0.02)


def test_same_seed_same_results_bitwise():
    make = lambda: ScenarioSet.lognormal(
        n_scenarios=50, horizon=20, drift=0.02, vol=0.2, seed=123
    )
    a, b = make(), make()
    assert np.array_equal(a.returns, b.returns)
    ra = run_stochastic(UnitLinkedGMDB, MPS, ASSUMPTIONS, a, 20, outputs=VARS)
    rb = run_stochastic(UnitLinkedGMDB, MPS, ASSUMPTIONS, b, 20, outputs=VARS)
    for name in VARS:
        assert np.array_equal(ra.array(name), rb.array(name))


def test_zero_vol_maturity_payment_closed_form():
    r = 0.05
    scen = ScenarioSet.flat(r, n_scenarios=1, horizon=N + 1)
    result = run_stochastic(
        UnitLinkedGMDB, MPS[:1], ASSUMPTIONS, scen, N + 1,
        outputs=["maturity_payments"],
    )
    payments = result.array("maturity_payments")[:, 0, 0]
    growth = (1 + r) * (1 - AMC)
    lapse = ASSUMPTIONS.lapse
    survivors = ((1 - Q) * (1 - lapse)) ** N
    expected_at_maturity = survivors * P * growth**N
    for t in range(N + 2):
        want = expected_at_maturity if t == N else 0.0
        assert payments[t] == pytest.approx(want, rel=REL)


def test_horizon_shorter_than_projection_rejected():
    scen = ScenarioSet.flat(0.05, n_scenarios=2, horizon=10)
    with pytest.raises(ValueError, match="horizon"):
        run_stochastic(UnitLinkedGMDB, MPS, ASSUMPTIONS, scen, 20)


def test_aggregate_and_scenario_mean_shapes():
    scen = ScenarioSet.flat(0.05, n_scenarios=4, horizon=N)
    result = run_stochastic(UnitLinkedGMDB, MPS, ASSUMPTIONS, scen, N, outputs=VARS)
    assert result.array("fund_boy").shape == (N + 1, len(MPS), 4)
    assert result.aggregate("fee_income").shape == (N + 1, 4)
    assert result.scenario_mean("fee_income").shape == (N + 1,)
