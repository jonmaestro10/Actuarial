"""Interpreter vs vectorized executor: bitwise equivalence.

The two executors share model code but take different evaluation paths
(scalar Python floats vs NumPy float64 arrays). IEEE-754 elementwise ops
round identically, so results must agree *bitwise* — any drift means one
path is doing different arithmetic and is a bug, not noise.
"""

import pytest

from engine.core.runner import run
from engine.core.vector import run_vectorized
from engine.data.assumptions import Assumptions, MortalityTable
from engine.data.modelpoints import from_dicts, to_batch
from engine.library.fixed_annuity import FixedAnnuity
from engine.library.term_life import TermLife

QX = {age: min(1.0, 0.0008 * 1.095 ** (age - 30)) for age in range(30, 121)}

ASSUMPTIONS = Assumptions(
    mortality=MortalityTable(QX),
    lapse=0.03,
    interest=0.03,
    expense_per_policy=45.0,
    crediting_rate=0.02,
)

TERM_MPS = from_dicts(
    [
        {"id": f"T{i}", "age_at_entry": 30 + (i * 7) % 35,
         "term_years": 5 + (i * 3) % 30, "sum_assured": 50_000.0 + 10_000.0 * i,
         "annual_premium": 400.0 + 55.0 * i, "init_pols": 1 + i % 3}
        for i in range(25)
    ]
)

ANNUITY_MPS = from_dicts(
    [
        {"id": f"A{i}", "age_at_entry": 40 + (i * 5) % 25,
         "defer_years": (i * 2) % 20, "premium": 100_000.0 + 5_000.0 * i,
         "annual_payment": 6_000.0 + 250.0 * i, "init_pols": 1}
        for i in range(25)
    ]
)

TERM_VARS = ["pols_if", "claims", "premiums", "expenses", "v"]
ANNUITY_VARS = ["pols_if", "payments", "death_benefits", "fund_eoy_per_pol"]


@pytest.mark.parametrize(
    "model_cls,mps,outputs",
    [(TermLife, TERM_MPS, TERM_VARS), (FixedAnnuity, ANNUITY_MPS, ANNUITY_VARS)],
    ids=["term_life", "fixed_annuity"],
)
def test_executors_agree_bitwise(model_cls, mps, outputs):
    proj_len = 40
    interpreted = run(model_cls, mps, ASSUMPTIONS, proj_len, outputs=outputs)
    vectorized = run_vectorized(model_cls, mps, ASSUMPTIONS, proj_len, outputs=outputs)
    assert interpreted.mp_ids == vectorized.mp_ids
    for i in range(len(mps)):
        for name in outputs:
            got = vectorized.per_mp[i][name]
            want = interpreted.per_mp[i][name]
            assert got == want, f"mp {i} var {name}"  # exact, not approx


def test_scalar_vars_broadcast_across_batch():
    result = run_vectorized(TermLife, TERM_MPS, ASSUMPTIONS, 10, outputs=["v"])
    v_first = result.per_mp[0]["v"]
    for mp_result in result.per_mp[1:]:
        assert mp_result["v"] == v_first


def test_batch_rejects_heterogeneous_fields():
    mps = from_dicts([{"a": 1, "b": 2.0}, {"a": 1, "c": 3.0}])
    with pytest.raises(ValueError, match="do not match"):
        to_batch(mps)


def test_batch_preserves_integer_dtype_for_table_indexing():
    batch = to_batch(TERM_MPS)
    assert batch.age_at_entry.dtype.kind == "i"
    assert batch.sum_assured.dtype.kind == "f"
