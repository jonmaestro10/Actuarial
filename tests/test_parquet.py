"""Parquet round trip: model points survive storage bit-for-bit, and a run
from reloaded model points equals a run from the originals."""

import pytest

pa = pytest.importorskip("pyarrow")

from engine.core.runner import run
from engine.data.assumptions import Assumptions, MortalityTable
from engine.data.modelpoints import from_dicts, from_parquet, to_parquet
from engine.library.term_life import TermLife

ROWS = [
    {"id": "T1", "age_at_entry": 40, "term_years": 25, "sum_assured": 250_000.0,
     "annual_premium": 900.0, "init_pols": 1},
    {"id": "T2", "age_at_entry": 55, "term_years": 10, "sum_assured": 100_000.0,
     "annual_premium": 1_400.0, "init_pols": 2},
]


def test_roundtrip_preserves_fields(tmp_path):
    path = tmp_path / "mps.parquet"
    to_parquet(from_dicts(ROWS), path)
    reloaded = from_parquet(path)
    assert [mp.__dict__ for mp in reloaded] == ROWS


def test_run_from_reloaded_modelpoints_is_identical(tmp_path):
    path = tmp_path / "mps.parquet"
    original = from_dicts(ROWS)
    to_parquet(original, path)
    reloaded = from_parquet(path)

    assumptions = Assumptions(
        mortality=MortalityTable.flat(0.01), lapse=0.02, interest=0.03
    )
    outputs = ["pols_if", "claims", "premiums"]
    a = run(TermLife, original, assumptions, 30, outputs=outputs)
    b = run(TermLife, reloaded, assumptions, 30, outputs=outputs)
    for name in outputs:
        assert a.aggregate(name) == b.aggregate(name)
