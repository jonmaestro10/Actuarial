"""The warehouse, and the number a dashboard cannot trace.

RFC-046. The claim is that any figure in a BI tool joins back to a
registered, reproducible run, so the tests are about the join and about the
values rather than about Parquet:

- **The values are the run's, bit for bit.** The arrays are rebuilt out of
  Parquet and fingerprinted against the run's own ``results_digest``. A
  warehouse whose numbers merely look right is what this replaces.
- **Every fact row carries the fingerprint**, in a column, so a file copied
  out of the tree still knows where it came from.
- **Re-loading a run replaces it**, rather than doubling it — the failure
  every warehouse load script has had at least once.
- **A stochastic run must say how many scenarios it means**, because the
  row count is a decision somebody should make on purpose.
"""

import json

import numpy as np
import pytest

pytest.importorskip("pyarrow", reason="needs the [data] extra")

from engine.core.fingerprint import fingerprint  # noqa: E402
from engine.core.registry import record_run  # noqa: E402
from engine.data.assumptions import Assumptions, MortalityTable  # noqa: E402
from engine.data.modelpoints import ModelPoint  # noqa: E402
from engine.data.scenarios import ScenarioSet  # noqa: E402
from engine.data.warehouse import (  # noqa: E402
    Warehouse,
    WarehouseError,
    write_run,
)
from engine.library.term_life import TermLife  # noqa: E402
from engine.library.unit_linked import UnitLinkedGMDB  # noqa: E402

QX = {age: min(0.0006 * 1.095 ** (age - 20), 0.5) for age in range(20, 111)}
ASSUMPTIONS = Assumptions(mortality=MortalityTable(QX), lapse=0.04,
                          interest=0.03, expense_per_policy=50.0)
POINTS = [
    ModelPoint(id="T1", age_at_entry=45, term_years=20, sum_assured=250_000.0,
               annual_premium=1_100.0, init_pols=1),
    ModelPoint(id="T2", age_at_entry=55, term_years=10, sum_assured=100_000.0,
               annual_premium=900.0, init_pols=1),
]
PROJ_LEN = 20
OUTPUTS = ["pols_if", "claims", "premiums"]


@pytest.fixture(scope="module")
def run():
    return record_run(TermLife, POINTS, ASSUMPTIONS, PROJ_LEN, outputs=OUTPUTS)


@pytest.fixture
def warehouse(tmp_path, run):
    result, record = run
    store = Warehouse(tmp_path / "warehouse")
    store.write_run(result, record)
    return store, result, record


# --------------------------------------------------------------------------
# The numbers
# --------------------------------------------------------------------------

def test_the_arrays_come_back_out_bit_for_bit(warehouse):
    """The whole claim, made checkable: what the warehouse holds fingerprints
    to the digest the run registry recorded."""
    store, result, record = warehouse
    rebuilt = {name: store.array(record.run_id, name) for name in OUTPUTS}
    assert fingerprint(rebuilt) == record.results_digest
    for name in OUTPUTS:
        assert np.array_equal(rebuilt[name], result.array(name))


def test_the_fact_table_is_long_and_complete(warehouse):
    store, _, record = warehouse
    facts = store.facts(record.run_id)
    assert facts.num_rows == len(OUTPUTS) * (PROJ_LEN + 1) * len(POINTS)
    assert set(facts.column_names) == {"run_id", "modelpoint_id", "scenario",
                                       "t", "variable", "value"}
    assert store.facts(record.run_id, "claims").num_rows == \
        (PROJ_LEN + 1) * len(POINTS)


def test_every_fact_row_carries_the_run_fingerprint(warehouse):
    """In a column, not only in the path: a file copied out of the tree
    still knows which run it came from."""
    store, _, record = warehouse
    facts = store.facts(record.run_id)
    assert set(facts.column("run_id").to_pylist()) == {record.run_id}

    import pyarrow.parquet as pq

    lone_file = next((store.path_for("fact_cashflow", record.run_id)
                      ).glob("*.parquet"))
    copied = pq.read_table(lone_file)
    assert set(copied.column("run_id").to_pylist()) == {record.run_id}


def test_a_deterministic_run_has_no_scenario(warehouse):
    store, _, record = warehouse
    assert set(store.facts(record.run_id).column("scenario").to_pylist()) == \
        {None}


# --------------------------------------------------------------------------
# The dimensions
# --------------------------------------------------------------------------

def test_the_run_dimension_carries_the_provenance_a_reviewer_asks_for(
        warehouse):
    store, _, record = warehouse
    row = store.run(record.run_id)
    assert row["model"] == "engine.library.term_life.TermLife"
    assert row["assumptions_digest"] == record.assumptions_digest
    assert row["modelpoints_digest"] == record.modelpoints_digest
    assert row["results_digest"] == record.results_digest
    assert row["engine_version"] == record.engine_version
    assert row["executor"] == record.executor
    assert row["outputs"] == OUTPUTS
    assert store.run("nope") is None


def test_the_variable_dimension_carries_the_docstring_and_the_assumption(
        warehouse):
    store, _, record = warehouse
    by_name = {row["variable"]: row for row in store.variables(record.run_id)}
    assert set(by_name) == set(OUTPUTS)
    assert by_name["claims"]["documented"] is True
    assert "Death claims" in by_name["claims"]["doc"]
    assert by_name["claims"]["pooled"] is False


def test_the_modelpoint_dimension_keeps_input_order(warehouse):
    store, _, record = warehouse
    rows = sorted(store.modelpoints(record.run_id), key=lambda r: r["ordinal"])
    assert [row["modelpoint_id"] for row in rows] == ["T1", "T2"]


# --------------------------------------------------------------------------
# Loading twice, and loading two things
# --------------------------------------------------------------------------

def test_re_loading_a_run_replaces_it_rather_than_doubling_it(warehouse):
    """The failure every warehouse load script has had at least once."""
    store, result, record = warehouse
    before = store.facts(record.run_id).num_rows
    store.write_run(result, record)
    assert store.facts(record.run_id).num_rows == before
    assert len(store.runs()) == 1
    assert len(store.variables()) == len(OUTPUTS)


def test_two_runs_coexist_and_stay_separable(tmp_path, run):
    result, record = run
    other_result, other_record = record_run(
        TermLife, POINTS, ASSUMPTIONS, PROJ_LEN, outputs=OUTPUTS,
        executor="interpreted",
    )
    store = Warehouse(tmp_path / "warehouse")
    store.write_run(result, record)
    store.write_run(other_result, other_record)

    assert {row["run_id"] for row in store.runs()} == {record.run_id,
                                                       other_record.run_id}
    assert store.facts(record.run_id).num_rows == \
        len(OUTPUTS) * (PROJ_LEN + 1) * len(POINTS)
    assert store.facts().num_rows == 2 * store.facts(record.run_id).num_rows
    # Same question, different executor: same answer, different run id.
    assert record.results_digest == other_record.results_digest
    assert record.run_id != other_record.run_id


def test_an_empty_warehouse_answers_rather_than_raising(tmp_path):
    store = Warehouse(tmp_path / "nothing")
    assert store.runs() == []
    assert store.variables() == []
    assert store.modelpoints() == []
    assert store.facts() is None


def test_the_module_function_writes_the_same_thing(tmp_path, run):
    result, record = run
    written = write_run(tmp_path / "warehouse", result, record)
    assert written.n_facts == len(OUTPUTS) * (PROJ_LEN + 1) * len(POINTS)
    assert written.n_scenarios is None
    assert Warehouse(tmp_path / "warehouse").run(record.run_id) is not None


# --------------------------------------------------------------------------
# Stochastic runs say how big they are
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def stochastic():
    points = [ModelPoint(id="U1", age_at_entry=55, term_years=10,
                         premium=50_000.0, gmdb_guarantee=50_000.0,
                         init_pols=1)]
    scenarios = ScenarioSet.lognormal(n_scenarios=4, horizon=11, drift=0.03,
                                      vol=0.15, seed=7)
    assumptions = Assumptions(mortality=MortalityTable(QX), lapse=0.02,
                              interest=0.03, amc=0.01)
    return record_run(UnitLinkedGMDB, points, assumptions, 10,
                      outputs=["fund_eoy", "pols_if"], scenarios=scenarios)


def test_a_stochastic_run_refuses_to_guess_its_row_count(tmp_path, stochastic):
    result, record = stochastic
    store = Warehouse(tmp_path / "warehouse")
    with pytest.raises(WarehouseError, match="name them"):
        store.write_run(result, record)


def test_a_stochastic_run_writes_the_scenarios_it_is_told_to(tmp_path,
                                                             stochastic):
    result, record = stochastic
    store = Warehouse(tmp_path / "warehouse")
    written = store.write_run(result, record, scenarios=[0, 2])
    assert written.n_scenarios == 2
    facts = store.facts(record.run_id)
    assert sorted(set(facts.column("scenario").to_pylist())) == [0, 2]
    assert facts.num_rows == 2 * 2 * 11 * 1        # scenarios x vars x t x mp

    all_of_it = store.write_run(result, record, scenarios="all")
    assert all_of_it.n_scenarios == 4


def test_an_out_of_range_scenario_is_named(tmp_path, stochastic):
    result, record = stochastic
    store = Warehouse(tmp_path / "warehouse")
    with pytest.raises(WarehouseError, match=r"\[9\] outside 0\.\.3"):
        store.write_run(result, record, scenarios=[0, 9])


def test_a_deterministic_run_refuses_a_scenario_selection(tmp_path, run):
    result, record = run
    store = Warehouse(tmp_path / "warehouse")
    with pytest.raises(WarehouseError, match="no scenario axis"):
        store.write_run(result, record, scenarios="all")


# --------------------------------------------------------------------------
# The consumption path
# --------------------------------------------------------------------------

def test_the_files_are_plain_parquet_a_bi_tool_can_read(warehouse):
    """Nothing in the layout needs this repo to read it."""
    import pyarrow.parquet as pq

    store, _, record = warehouse
    facts = sorted((store.root / "fact_cashflow").rglob("*.parquet"))
    dims = sorted((store.root / "dim_run").rglob("*.parquet"))
    assert facts and dims
    table = pq.read_table(facts[0])
    assert table.schema.field("value").type == "double"
    assert table.schema.field("t").type == "int32"
    run_row = pq.read_table(dims[0]).to_pylist()[0]
    assert run_row["run_id"] == record.run_id
    # ``outputs`` is a list, and a list in a dimension row is JSON so that
    # every BI connector reads the column the same way.
    assert json.loads(run_row["outputs"]) == OUTPUTS
