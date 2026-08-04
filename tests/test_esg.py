"""ESG file adapters, and the mistakes they exist to make loud.

Parsing a scenario file is easy. Every way of parsing one *wrongly*
produces a plausible number rather than an error, which is why most of this
file is about the wrong ways:

- a cumulative index read as a per-period return;
- an index converted with the wrong base — 1.0 where the generator used
  100.0, a hundredfold error that survives every downstream check;
- a period-0 column read as period 1, shifting the whole projection;
- a rectangle with a hole in it;
- a file that claims to be risk-neutral and is not.

The round-trip tests assert **bitwise** equality, because a scenario file
written and read back should be the same numbers, not nearly.
"""

from __future__ import annotations

import numpy as np
import pytest

from engine.data import esg
from engine.data.scenarios import ScenarioSet

RATE = 0.04
DRIFT = float(np.log(1.0 + RATE))
VOL = 0.16


def lognormal(n=2_000, horizon=25, drift=DRIFT, vol=VOL, seed=11):
    return ScenarioSet.lognormal(n, horizon, drift=drift, vol=vol, seed=seed)


def write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


# --- wide layout ---------------------------------------------------------


def test_a_wide_file_round_trips_bitwise(tmp_path):
    original = lognormal(n=50, horizon=10)
    path = tmp_path / "esg.csv"
    esg.to_wide_csv(original, path)
    back = esg.read_wide(path)
    assert np.array_equal(back.returns, original.returns)
    assert (back.n_scenarios, back.horizon) == (50, 10)


def test_a_wide_file_is_read_row_per_scenario_column_per_period(tmp_path):
    path = write(tmp_path, "w.csv",
                 "Scenario,1,2,3\n1,0.05,-0.01,0.07\n2,0.02,0.03,-0.04\n")
    got = esg.read_wide(path)
    assert np.array_equal(
        got.returns,
        np.array([[0.05, -0.01, 0.07], [0.02, 0.03, -0.04]]),
    )


def test_a_headerless_file_of_bare_numbers_reads(tmp_path):
    path = write(tmp_path, "bare.csv", "0.05,-0.01\n0.02,0.03\n")
    got = esg.read_wide(path, has_header=False, id_columns=0)
    assert np.array_equal(got.returns, np.array([[0.05, -0.01], [0.02, 0.03]]))


def test_a_vendor_metadata_block_can_be_skipped(tmp_path):
    path = write(tmp_path, "meta.csv",
                 "# Generated 2026-01-01\n# Model: GBM\n"
                 "Scenario,1,2\n1,0.05,-0.01\n")
    got = esg.read_wide(path, skip_lines=2)
    assert np.array_equal(got.returns, np.array([[0.05, -0.01]]))


def test_a_ragged_file_raises_rather_than_padding(tmp_path):
    path = write(tmp_path, "ragged.csv", "S,1,2\n1,0.05,-0.01\n2,0.02\n")
    with pytest.raises(ValueError, match="differing column counts"):
        esg.read_wide(path)


def test_a_non_numeric_cell_says_where_it_is(tmp_path):
    path = write(tmp_path, "bad.csv", "S,1,2\n1,0.05,n/a\n")
    with pytest.raises(ValueError, match="row 1.*value 2.*'n/a'"):
        esg.read_wide(path)


def test_a_file_with_no_value_columns_raises(tmp_path):
    path = write(tmp_path, "empty.csv", "Scenario\n1\n2\n")
    with pytest.raises(ValueError, match="no value columns"):
        esg.read_wide(path)


# --- an index is not a return -------------------------------------------


def test_an_index_converts_to_the_returns_that_generated_it(tmp_path):
    """Levels chosen so every ratio is exact in binary — 1.5/1.25 is not,
    and a tolerance here would hide the very arithmetic under test."""
    path = write(tmp_path, "ix.csv", "S,0,1,2\n1,1.0,1.25,1.5625\n")
    got = esg.read_wide(path, kind="index", starts_at=0)
    assert np.array_equal(got.returns, np.array([[0.25, 0.25]]))


def test_an_index_starting_at_period_one_needs_its_base(tmp_path):
    path = write(tmp_path, "ix.csv", "S,1,2\n1,1.25,1.5625\n")
    with pytest.raises(ValueError, match="needs the level at time zero"):
        esg.read_wide(path, kind="index")
    got = esg.read_wide(path, kind="index", index_base=1.0)
    assert np.array_equal(got.returns, np.array([[0.25, 0.25]]))


def test_the_base_and_a_period_zero_column_are_mutually_exclusive(tmp_path):
    path = write(tmp_path, "ix.csv", "S,0,1\n1,1.0,1.25\n")
    with pytest.raises(ValueError, match="redundant"):
        esg.read_wide(path, kind="index", starts_at=0, index_base=1.0)


def test_index_base_is_meaningless_for_a_return_file(tmp_path):
    path = write(tmp_path, "r.csv", "S,1\n1,0.05\n")
    with pytest.raises(ValueError, match="only applies to kind='index'"):
        esg.read_wide(path, index_base=100.0)


def test_the_index_base_only_has_to_be_the_right_scale():
    """A generator publishing on 100.0 and one publishing on 1.0 give the
    same returns — provided you tell the reader which. Telling it the wrong
    one is a hundredfold error in the first period only, which is exactly
    why there is no default."""
    levels = np.array([[125.0, 156.25]])
    right = esg.returns_from_index(levels, index_base=100.0)
    wrong = esg.returns_from_index(levels, index_base=1.0)
    assert np.array_equal(right, np.array([[0.25, 0.25]]))
    assert wrong[0, 0] == 124.0
    assert wrong[0, 1] == right[0, 1]      # only the first period is hit


def test_a_non_positive_index_raises():
    with pytest.raises(ValueError, match="strictly positive"):
        esg.returns_from_index(np.array([[1.0, 0.0, 1.2]]), starts_at=0)


def test_an_unknown_kind_raises(tmp_path):
    path = write(tmp_path, "r.csv", "S,1\n1,0.05\n")
    with pytest.raises(ValueError, match="kind must be one of"):
        esg.read_wide(path, kind="level")


def test_an_out_of_range_starts_at_raises():
    with pytest.raises(ValueError, match="starts_at must be 0 or 1"):
        esg.returns_from_index(np.array([[1.0, 1.25]]), starts_at=2)


def test_returns_and_the_index_they_imply_agree(tmp_path):
    """Both readers on the same underlying path: write a set as returns and
    as the index those returns build, and get the same numbers back."""
    original = lognormal(n=20, horizon=8)
    levels = np.cumprod(1.0 + original.returns, axis=1)
    rows = ["S," + ",".join(str(i) for i in range(1, 9))]
    rows += [f"{s + 1}," + ",".join(repr(float(v)) for v in row)
             for s, row in enumerate(levels)]
    path = write(tmp_path, "levels.csv", "\n".join(rows) + "\n")
    got = esg.read_wide(path, kind="index", index_base=1.0)
    assert np.allclose(got.returns, original.returns, rtol=0, atol=1e-15)


# --- long layout ---------------------------------------------------------


LONG = """scenario,period,series,value
1,1,equity,0.05
1,1,bond,0.02
1,2,equity,-0.01
1,2,bond,0.021
2,1,equity,0.03
2,1,bond,0.019
2,2,equity,0.04
2,2,bond,0.02
"""


def test_a_long_file_carries_several_series(tmp_path):
    got = esg.read_long(write(tmp_path, "l.csv", LONG),
                        series_column="series", primary="equity")
    assert got.names == ("bond", "equity")
    assert got.primary == "equity"
    assert np.array_equal(got.series("equity"),
                          np.array([[0.05, -0.01], [0.03, 0.04]]))
    assert np.array_equal(got.series("bond"),
                          np.array([[0.02, 0.021], [0.019, 0.02]]))
    assert np.array_equal(got.ret(0), np.array([0.05, 0.03]))


def test_several_series_need_one_named_as_primary(tmp_path):
    with pytest.raises(ValueError, match="name one of them"):
        esg.read_long(write(tmp_path, "l.csv", LONG), series_column="series")


def test_a_single_series_long_file_needs_no_primary(tmp_path):
    path = write(tmp_path, "one.csv",
                 "scenario,period,value\n1,1,0.05\n1,2,-0.01\n")
    got = esg.read_long(path)
    assert got.names == ("return",)
    assert np.array_equal(got.returns, np.array([[0.05, -0.01]]))


def test_row_order_in_the_file_does_not_matter(tmp_path):
    shuffled = "\n".join(
        [LONG.splitlines()[0]] + list(reversed(LONG.splitlines()[1:]))
    ) + "\n"
    ordered = esg.read_long(write(tmp_path, "a.csv", LONG),
                            series_column="series", primary="equity")
    jumbled = esg.read_long(write(tmp_path, "b.csv", shuffled),
                            series_column="series", primary="equity")
    assert np.array_equal(ordered.series("equity"), jumbled.series("equity"))
    assert np.array_equal(ordered.series("bond"), jumbled.series("bond"))


def test_a_hole_in_the_rectangle_is_named(tmp_path):
    path = write(tmp_path, "hole.csv",
                 "scenario,period,value\n1,1,0.05\n1,2,-0.01\n2,1,0.03\n")
    with pytest.raises(ValueError, match=r"missing \(scenario, period\) \(2.0, 2.0\)"):
        esg.read_long(path)


def test_a_duplicated_cell_raises(tmp_path):
    path = write(tmp_path, "dup.csv",
                 "scenario,period,value\n1,1,0.05\n1,1,0.06\n")
    with pytest.raises(ValueError, match="duplicate row"):
        esg.read_long(path)


def test_a_missing_column_lists_what_the_file_actually_has(tmp_path):
    path = write(tmp_path, "cols.csv", "trial,step,val\n1,1,0.05\n")
    with pytest.raises(ValueError, match=r"no column\(s\).*has \['step', 'trial', 'val'\]"):
        esg.read_long(path)
    got = esg.read_long(path, scenario_column="trial", period_column="step",
                        value_column="val")
    assert np.array_equal(got.returns, np.array([[0.05]]))


def test_non_numeric_scenario_ids_still_order_sensibly(tmp_path):
    path = write(tmp_path, "ids.csv",
                 "scenario,period,value\nS2,1,0.03\nS10,1,0.07\nS1,1,0.05\n")
    got = esg.read_long(path)
    # Lexical, because "S10" has no numeric reading — but stable and stated,
    # which beats depending on the order rows happened to be written in.
    assert np.array_equal(got.returns, np.array([[0.05], [0.07], [0.03]]))


def test_periods_are_ordered_by_value_not_by_appearance(tmp_path):
    path = write(tmp_path, "p.csv",
                 "scenario,period,value\n1,10,0.10\n1,2,0.02\n1,1,0.01\n")
    got = esg.read_long(path)
    assert np.array_equal(got.returns, np.array([[0.01, 0.02, 0.10]]))


def test_a_long_parquet_extract_reads(tmp_path):
    pytest.importorskip("pyarrow")
    import pyarrow as pa
    import pyarrow.parquet as pq

    rows = [{"scenario": s, "period": p, "series": name, "value": v}
            for s in (1, 2) for p in (1, 2)
            for name, v in (("equity", 0.01 * (s + p)),
                            ("bond", 0.001 * (s + p)))]
    path = tmp_path / "esg.parquet"
    pq.write_table(pa.Table.from_pylist(rows), path)
    got = esg.read_parquet_long(path, series_column="series", primary="equity")
    assert got.names == ("bond", "equity")
    assert np.allclose(got.series("equity"),
                       np.array([[0.02, 0.03], [0.03, 0.04]]))
    assert got.source["layout"] == "long-parquet"


# --- provenance ----------------------------------------------------------


def test_the_same_numbers_from_different_files_are_the_same_set(tmp_path):
    """Identity is the values, not the path — the split RFC-003 makes
    between what was asked and the context it was asked in."""
    from engine.core.fingerprint import fingerprint

    original = lognormal(n=20, horizon=8)
    a, b = tmp_path / "a.csv", tmp_path / "b.csv"
    esg.to_wide_csv(original, a)
    esg.to_wide_csv(original, b)
    first, second = esg.read_wide(a), esg.read_wide(b)
    assert fingerprint(first) == fingerprint(second) == fingerprint(original)
    assert first.source["path"] != second.source["path"]
    assert first.source["digest"] == second.source["digest"]


def test_the_source_digest_is_of_the_bytes_that_were_read(tmp_path):
    path = write(tmp_path, "s.csv", "S,1\n1,0.05\n")
    before = esg.read_wide(path).source["digest"]
    write(tmp_path, "s.csv", "S,1\n1,0.06\n")
    assert esg.read_wide(path).source["digest"] != before


# --- diagnostics ---------------------------------------------------------


def test_describe_flags_an_index_column_read_as_a_return(tmp_path):
    """What the mistake looks like: a first period identical in every
    scenario, because it was really the index base."""
    path = write(tmp_path, "shift.csv",
                 "S,0,1,2\n1,1.0,1.25,1.5\n2,1.0,1.1,1.3\n")
    as_returns = esg.read_wide(path)
    assert esg.describe(as_returns)["constant_first_period"] is True
    as_index = esg.read_wide(path, kind="index", starts_at=0)
    assert esg.describe(as_index)["constant_first_period"] is False


def test_describe_summarises_a_real_set():
    got = esg.describe(lognormal(n=500, horizon=12))
    assert got["n_scenarios"] == 500
    assert got["horizon"] == 12
    assert got["mean_by_period"].shape == (12,)
    assert got["min"] > -1.0
    assert got["constant_first_period"] is False


# --- the martingale property --------------------------------------------


def test_a_risk_neutral_set_prices_its_own_numeraire():
    rows = esg.martingale_error(lognormal(), RATE)
    assert len(rows) == 25
    assert all(row["sigmas"] < 5.0 for row in rows)
    esg.check_risk_neutral(lognormal(), RATE)


def test_the_error_bar_is_the_point_not_the_deviation():
    """The same absolute deviation means different things at different
    sample sizes, which is why the check is stated in standard errors.

    More scenarios shrink the standard error roughly as 1/sqrt(n) — so a
    deviation that is comfortable at 500 scenarios would be damning at
    50,000, and a fixed basis-point tolerance cannot tell those apart."""
    stderrs = [
        esg.martingale_error(lognormal(n=n), RATE)[-1]["stderr"]
        for n in (500, 5_000, 50_000)
    ]
    assert stderrs[0] > stderrs[1] > stderrs[2]
    for coarse, fine in zip(stderrs, stderrs[1:]):
        assert 2.5 < coarse / fine < 4.0     # ~sqrt(10)


def test_a_set_that_is_not_risk_neutral_is_caught_and_named():
    drifted = ScenarioSet.lognormal(
        2_000, 25, drift=float(np.log(1.08)), vol=VOL, seed=11
    )
    with pytest.raises(ValueError, match="not risk-neutral"):
        esg.check_risk_neutral(drifted, RATE)
    rows = esg.martingale_error(drifted, RATE)
    assert rows[0]["sigmas"] < rows[-1]["sigmas"]     # compounds with horizon
    assert rows[-1]["error"] > 1.0


def test_the_martingale_check_takes_a_term_structure():
    rates = np.linspace(0.02, 0.05, 20)
    curve = ScenarioSet(np.tile(rates, (3, 1)))
    rows = esg.martingale_error(curve, rates)
    assert all(abs(row["error"]) < 1e-14 for row in rows)


def test_a_deterministic_set_has_no_error_bar():
    flat = ScenarioSet.flat(RATE, 1, 5)
    rows = esg.martingale_error(flat, RATE)
    assert all(row["stderr"] == 0.0 and row["sigmas"] == 0.0 for row in rows)
    assert all(abs(row["error"]) < 1e-15 for row in rows)


# --- the scenario set itself ---------------------------------------------


def test_a_single_series_set_behaves_as_it_always_did():
    """Every template and test that predates named series passes a bare
    array and reads ``ret(t)``. That has to keep working unchanged."""
    returns = np.array([[0.05, -0.01], [0.02, 0.03]])
    s = ScenarioSet(returns)
    assert s.primary == "return"
    assert s.names == ("return",)
    assert np.array_equal(s.returns, returns)
    assert np.array_equal(s.ret(1), np.array([-0.01, 0.03]))
    assert np.array_equal(s.single(1).returns, returns[1:2])


def test_views_carry_every_series(tmp_path):
    full = esg.read_long(write(tmp_path, "l.csv", LONG),
                         series_column="series", primary="equity")
    assert full.single(0).names == ("bond", "equity")
    assert full.truncate(1).names == ("bond", "equity")
    assert full.truncate(1).horizon == 1
    bonds = full.with_primary("bond")
    assert bonds.primary == "bond"
    assert np.array_equal(bonds.ret(0), full.at("bond", 0))
    assert bonds.names == full.names


def test_asking_for_a_series_that_is_not_there_lists_the_ones_that_are():
    with pytest.raises(KeyError, match=r"\['return'\]"):
        ScenarioSet(np.zeros((2, 2))).series("equity")


def test_series_must_agree_on_shape():
    with pytest.raises(ValueError, match="same scenarios and periods"):
        ScenarioSet(series={"a": np.zeros((2, 3)), "b": np.zeros((2, 4))})


def test_the_primary_must_be_one_of_the_series():
    with pytest.raises(ValueError, match="not among"):
        ScenarioSet(series={"a": np.zeros((2, 3))}, primary="b")


def test_a_non_finite_value_raises():
    with pytest.raises(ValueError, match="nan or inf"):
        ScenarioSet(np.array([[0.05, np.nan]]))


def test_only_the_primary_is_checked_as_a_return():
    """A short rate or an inflation series is not compounded by anything
    here, so bounding it would reject legitimate files. The primary is."""
    ok = ScenarioSet(series={"return": np.array([[0.05]]),
                             "spread": np.array([[-2.0]])})
    assert ok.at("spread", 0)[0] == -2.0
    with pytest.raises(ValueError, match="at or below -100%"):
        ScenarioSet(series={"return": np.array([[-1.5]])})


def test_returns_and_series_are_mutually_exclusive():
    with pytest.raises(ValueError, match="exactly one"):
        ScenarioSet(np.zeros((1, 1)), series={"a": np.zeros((1, 1))})
    with pytest.raises(ValueError, match="exactly one"):
        ScenarioSet()


def test_truncate_rejects_a_horizon_it_cannot_serve():
    with pytest.raises(ValueError, match=r"outside \(0, 5\]"):
        ScenarioSet.flat(0.04, 2, 5).truncate(6)


# --- end to end ----------------------------------------------------------


def test_a_file_on_disk_drives_a_projection_and_is_recorded(tmp_path):
    from engine.core.registry import record_run
    from engine.data.assumptions import Assumptions, MortalityTable
    from engine.data.modelpoints import from_dicts
    from engine.library.unit_linked import UnitLinkedGMDB

    path = tmp_path / "esg.csv"
    esg.to_wide_csv(lognormal(n=64, horizon=20), path)
    scenarios = esg.read_wide(path)

    points = from_dicts([
        {"id": "U1", "age_at_entry": 60, "term_years": 15, "premium": 100_000.0,
         "gmdb_guarantee": 100_000.0, "init_pols": 1},
    ])
    assumptions = Assumptions(mortality=MortalityTable.flat(0.008),
                              lapse=0.05, interest=RATE, amc=0.012)
    result, record = record_run(
        UnitLinkedGMDB, points, assumptions, 20, scenarios=scenarios,
        outputs=["pols_if", "gmdb_claims"],
    )
    # proj_len + 1 time steps: a series runs t = 0 .. proj_len inclusive.
    assert result.array("gmdb_claims").shape == (21, 1, 64)
    assert record.executor == "stochastic"
    assert record.n_scenarios == 64
    assert record.scenarios_digest is not None

    # Reading the same file again is the same question, so the same run id.
    _, again = record_run(
        UnitLinkedGMDB, points, assumptions, 20,
        scenarios=esg.read_wide(path), outputs=["pols_if", "gmdb_claims"],
    )
    assert again.run_id == record.run_id
    assert again.results_digest == record.results_digest
