"""Reference-model reconciliation: engine vs an independent naive projection.

The reference below is written as a deliberately plain forward loop with its
own state — no engine imports beyond input objects — so an error in the DSL
machinery cannot cancel itself out. Age-varying mortality, non-zero lapses
and expenses; every cashflow must reconcile at every time step to 1e-12.
"""

import pytest

from engine.core.runner import run
from engine.data.assumptions import Assumptions, MortalityTable
from engine.data.modelpoints import from_dicts
from engine.library.term_life import TermLife

REL = 1e-12

# Made-up but age-increasing table, ages 40-70.
QX = {age: 0.001 * 1.09 ** (age - 40) for age in range(40, 71)}

ASSUMPTIONS = Assumptions(
    mortality=MortalityTable(QX),
    lapse=0.04,
    interest=0.025,
    expense_per_policy=50.0,
)

MODELPOINTS = from_dicts(
    [
        {"id": "T1", "age_at_entry": 40, "term_years": 25, "sum_assured": 250_000.0,
         "annual_premium": 900.0, "init_pols": 1},
        {"id": "T2", "age_at_entry": 55, "term_years": 10, "sum_assured": 100_000.0,
         "annual_premium": 1_400.0, "init_pols": 1},
        {"id": "T3", "age_at_entry": 45, "term_years": 20, "sum_assured": 500_000.0,
         "annual_premium": 2_100.0, "init_pols": 3},
    ]
)

PROJ_LEN = 30


def naive_projection(mp, a, proj_len):
    """Independent forward-loop implementation of the same product spec."""
    out = {"pols_if": [], "claims": [], "premiums": [], "expenses": []}
    in_force = float(mp.init_pols)
    for t in range(proj_len + 1):
        active = t < mp.term_years
        q = a.mortality.q(mp.age_at_entry + t) if active else 0.0
        out["pols_if"].append(in_force)
        out["claims"].append(in_force * q * mp.sum_assured)
        out["premiums"].append(in_force * mp.annual_premium if active else 0.0)
        out["expenses"].append(in_force * a.expense_per_policy if active else 0.0)
        in_force = in_force * (1 - q) * (1 - a.lapse) if t + 1 < mp.term_years else 0.0
    return out


VARS = ["pols_if", "claims", "premiums", "expenses"]


@pytest.mark.parametrize("mp", MODELPOINTS, ids=lambda mp: mp.id)
def test_engine_reconciles_to_reference(mp):
    engine_model = TermLife(mp=mp, assumptions=ASSUMPTIONS, proj_len=PROJ_LEN)
    reference = naive_projection(mp, ASSUMPTIONS, PROJ_LEN)
    for name in VARS:
        engine_series = engine_model.series(name)
        for t, (got, want) in enumerate(zip(engine_series, reference[name])):
            assert got == pytest.approx(want, rel=REL), f"{name}[{t}]"


def test_runner_aggregate_matches_sum_of_references():
    result = run(TermLife, MODELPOINTS, ASSUMPTIONS, proj_len=PROJ_LEN, outputs=VARS)
    references = [naive_projection(mp, ASSUMPTIONS, PROJ_LEN) for mp in MODELPOINTS]
    for name in VARS:
        aggregate = result.aggregate(name)
        for t in range(PROJ_LEN + 1):
            want = sum(ref[name][t] for ref in references)
            assert aggregate[t] == pytest.approx(want, rel=REL), f"{name}[{t}]"


def test_run_is_deterministic_across_repeats():
    a = run(TermLife, MODELPOINTS, ASSUMPTIONS, proj_len=PROJ_LEN, outputs=VARS)
    b = run(TermLife, MODELPOINTS, ASSUMPTIONS, proj_len=PROJ_LEN, outputs=VARS)
    for name in VARS:
        assert a.aggregate(name) == b.aggregate(name)  # bitwise equality
