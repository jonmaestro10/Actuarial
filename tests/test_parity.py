"""Reconciliation: the two ways a parity report can lie.

RFC-033. A parity report is the document a replatforming decision rests on,
so these tests are about the failures that would make it *look* like
agreement:

- **A report that compares nothing.** Rows that do not align, columns nobody
  mapped, a subset of the horizon — each is a way to produce an all-green
  report over almost no cells. So coverage, matched rows and unmapped
  columns are all asserted, and an unmatched row makes the report not-ok.
- **A report that is insensitive.** A tolerance too loose, or one applied to
  the wrong variable, hides exactly the difference the exercise exists to
  find. So perturbations at known cells must be flagged at those cells and
  nowhere else, and the per-variable policy is exercised in both directions.

The engine-against-itself case is the control: identical inputs must produce
a report with zero deviation everywhere, or nothing below it means anything.
"""

import json
import math

import numpy as np
import pytest

from engine.core.fingerprint import fingerprint
from engine.core.registry import (
    ArtifactConflictError,
    ArtifactRecord,
    ArtifactRegistry,
    record_run,
)
from engine.data.assumptions import Assumptions, MortalityTable
from engine.data.modelpoints import ModelPoint
from engine.library.term_life import TermLife
from engine.parity import (
    ExternalTable,
    ParityError,
    ParitySpec,
    StatisticalTolerance,
    Tolerance,
    TolerancePolicy,
    diff,
    parity_artifact,
    record_parity,
)

QX = {age: min(0.0006 * 1.095 ** (age - 20), 0.5) for age in range(20, 111)}
PROJ_LEN = 20
OUTPUTS = ["pols_if", "claims", "premiums"]

ASSUMPTIONS = Assumptions(mortality=MortalityTable(QX), lapse=0.04,
                          interest=0.03, expense_per_policy=50.0)
POINTS = [
    ModelPoint(id="T1", age_at_entry=45, term_years=20, sum_assured=250_000.0,
               annual_premium=1_100.0, init_pols=1),
    ModelPoint(id="T2", age_at_entry=55, term_years=10, sum_assured=100_000.0,
               annual_premium=900.0, init_pols=1),
]


@pytest.fixture(scope="module")
def run():
    """One registered engine run, reused by every reconciliation below."""
    return record_run(TermLife, POINTS, ASSUMPTIONS, PROJ_LEN, outputs=OUTPUTS)


def external_from(result, variables=OUTPUTS, *, source="fixture") -> ExternalTable:
    """The engine's own answer, written out in an incumbent's row shape."""
    rows = []
    for j, mp_id in enumerate(result.mp_ids):
        for t in range(result.array(variables[0]).shape[0]):
            row = {"modelpoint_id": mp_id, "t": t}
            row.update({name: float(result.array(name)[t, j])
                        for name in variables})
            rows.append(row)
    return ExternalTable.from_rows(rows, source=source)


def perturb(table: ExternalTable, column: str, row: int, by: float) -> ExternalTable:
    columns = {name: list(values) for name, values in table.columns.items()}
    columns[column][row] += by
    return ExternalTable(columns, source=table.source)


def spec_for(result, table, **options) -> ParitySpec:
    return ParitySpec.from_results(
        result, table, {name: name for name in OUTPUTS}, **options
    )


# --------------------------------------------------------------------------
# The control: the engine against itself
# --------------------------------------------------------------------------

def test_the_engine_against_itself_is_all_zero(run):
    result, record = run
    report = diff(spec_for(result, external_from(result)),
                  results_digest=record.results_digest)
    assert report.ok
    assert report.max_absolute == 0.0
    assert report.max_relative == 0.0
    assert report.n_matched_rows == report.n_external_rows
    assert report.n_external_rows == (PROJ_LEN + 1) * len(POINTS)
    assert report.coverage == 1.0
    for entry in report.variables:
        assert entry.n_within == entry.n_compared
        assert entry.n_nonfinite == 0
    assert report.results_digest == record.results_digest


def test_row_order_does_not_move_the_verdict(run):
    """Alignment is by key, not by position, so a table shuffled row-wise
    reconciles identically.

    Its *digest* does move, and should: the content digest is of the extract
    as delivered, and a file with its rows in another order is another file.
    What must not move is the finding.
    """
    result, _ = run
    table = perturb(external_from(result), "claims", 5, 1.0)
    order = list(range(table.n_rows))[::-1]
    shuffled = ExternalTable(
        {name: [values[i] for i in order]
         for name, values in table.columns.items()},
        source=table.source,
    )
    here, there = diff(spec_for(result, table)), diff(spec_for(result, shuffled))
    assert here.variables == there.variables
    assert here.external_digest != there.external_digest


def test_the_external_digest_is_content_not_path(run):
    result, _ = run
    here = external_from(result, source="/one/path.csv")
    there = external_from(result, source="/another/path.csv")
    assert here.digest == there.digest


# --------------------------------------------------------------------------
# Sensitivity: exactly the perturbed cells, and no others
# --------------------------------------------------------------------------

def test_a_perturbed_cell_is_flagged_and_only_that_cell(run):
    result, _ = run
    table = external_from(result)
    # Row 5 is (T1, t=5); row 30 is (T2, t=9).
    table = perturb(table, "claims", 5, 1.0)
    table = perturb(table, "premiums", 30, -0.5)
    report = diff(spec_for(result, table))

    assert not report.ok
    claims = report.variable("claims")
    premiums = report.variable("premiums")
    assert claims.n_outside == 1
    assert premiums.n_outside == 1
    assert report.variable("pols_if").ok
    assert claims.max_absolute == pytest.approx(1.0)
    assert claims.worst_modelpoint == "T1"
    assert claims.worst_t == 5
    assert premiums.worst_modelpoint == "T2"
    assert premiums.worst_t == 9
    assert premiums.max_absolute == pytest.approx(0.5)

    worst = report.cells("claims")[0]
    assert (worst.modelpoint, worst.t) == ("T1", 5)
    assert not worst.within
    assert worst.external == pytest.approx(worst.engine + 1.0)


def test_a_perturbation_below_the_relative_bound_is_agreement(run):
    """The default bound is relative, so what counts as a difference scales
    with the number — one part in 1e12 of a claim is not a difference."""
    result, _ = run
    table = external_from(result)
    claims = table.column("claims")
    big = max(range(len(claims)), key=lambda i: claims[i])
    table = perturb(table, "claims", big, claims[big] * 1e-12)
    assert diff(spec_for(result, table)).ok


def test_tolerance_is_per_variable_in_both_directions(run):
    result, _ = run
    table = perturb(external_from(result), "claims", 5, 1.0)
    table = perturb(table, "premiums", 5, 1.0)
    policy = TolerancePolicy(per_variable={"claims": Tolerance(absolute=2.0)})
    report = diff(spec_for(result, table, tolerance=policy))
    assert report.variable("claims").ok           # 1.0 inside its own bound
    assert not report.variable("premiums").ok     # and outside the default
    assert "2" in report.variable("claims").tolerance.describe()


def test_a_statistical_tolerance_judges_against_sampling_error(run):
    result, _ = run
    table = perturb(external_from(result), "claims", 5, 40.0)
    inside = TolerancePolicy(
        per_variable={"claims": StatisticalTolerance(standard_error=20.0)}
    )
    outside = TolerancePolicy(
        per_variable={"claims": StatisticalTolerance(standard_error=1.0)}
    )
    assert diff(spec_for(result, table, tolerance=inside)).variable("claims").ok
    assert not diff(
        spec_for(result, table, tolerance=outside)
    ).variable("claims").ok


def test_a_non_finite_external_value_is_a_difference_not_a_pass(run):
    """NaN compares false against every bound; the risk is that it also
    poisons the max-deviation statistics into meaninglessness."""
    result, _ = run
    columns = {name: list(values)
               for name, values in external_from(result).columns.items()}
    columns["claims"][3] = float("nan")
    report = diff(spec_for(result, ExternalTable(columns)))
    claims = report.variable("claims")
    assert claims.n_outside == 1
    assert claims.n_nonfinite == 1
    assert math.isfinite(claims.max_absolute)
    assert claims.max_absolute == 0.0        # every *comparable* cell agreed
    first = claims.deviations[0]             # and the NaN heads the drill-down
    assert math.isnan(first.external)
    assert not first.within


# --------------------------------------------------------------------------
# What the report refuses to hide
# --------------------------------------------------------------------------

def test_an_unmatched_row_is_reported_and_makes_the_report_not_a_parity(run):
    result, _ = run
    table = external_from(result)
    columns = {name: list(values) for name, values in table.columns.items()}
    columns["modelpoint_id"][7] = "T99"
    columns["t"][8] = PROJ_LEN + 5
    report = diff(spec_for(result, ExternalTable(columns)))

    assert not report.ok
    assert report.n_unmatched_rows == 2
    reasons = {row["reason"] for row in report.unmatched_rows}
    assert reasons == {"no such model point in the run",
                       f"t outside the projection (0..{PROJ_LEN})"}
    # Every mapped cell that *was* compared still agreed — the failure is
    # coverage, and the report says which.
    assert all(entry.ok for entry in report.variables)


def test_an_unmapped_column_is_reported_not_dropped(run):
    result, _ = run
    columns = {name: list(values)
               for name, values in external_from(result).columns.items()}
    columns["surrender_value"] = [0.0] * len(columns["t"])
    spec = ParitySpec.from_results(
        result, ExternalTable(columns), {"claims": "claims"}
    )
    report = diff(spec)
    assert set(report.unmapped_columns) == {"pols_if", "premiums",
                                            "surrender_value"}
    assert "surrender_value" in report.to_markdown()


def test_partial_coverage_is_a_number_not_a_verdict(run):
    """An extract covering half the horizon is a legitimate reconciliation of
    half the horizon, and must not be able to pass as more than that."""
    result, _ = run
    table = external_from(result)
    keep = [i for i, t in enumerate(table.column("t")) if t <= 10]
    trimmed = ExternalTable({name: [values[i] for i in keep]
                             for name, values in table.columns.items()})
    report = diff(spec_for(result, trimmed))
    assert report.ok
    assert report.coverage == pytest.approx(11 / (PROJ_LEN + 1))
    assert "52.4%" in report.to_markdown()


def test_a_time_offset_is_declared_in_the_spec(run):
    """An extract that calls the first period 1 reconciles — but only because
    somebody wrote the offset down."""
    result, _ = run
    table = external_from(result)
    shifted = ExternalTable({
        **{name: list(values) for name, values in table.columns.items()},
        "t": [t - 1 for t in table.column("t")],
    })
    assert not diff(spec_for(result, shifted)).ok
    assert diff(spec_for(result, shifted, time_offset=1)).ok


# --------------------------------------------------------------------------
# Setup mistakes raise rather than reconcile
# --------------------------------------------------------------------------

def test_a_mapping_to_a_variable_the_run_lacks_raises(run):
    result, _ = run
    with pytest.raises(ParityError, match="reserve"):
        ParitySpec.from_results(result, external_from(result),
                                {"claims": "reserve"})


def test_a_missing_key_column_raises(run):
    result, _ = run
    with pytest.raises(ParityError, match="policy_id"):
        ParitySpec.from_results(result, external_from(result),
                                {"claims": "claims"}, id_column="policy_id")


def test_an_empty_mapping_raises(run):
    result, _ = run
    with pytest.raises(ParityError, match="at least one"):
        ParitySpec.from_results(result, external_from(result), {})


def test_a_text_column_cannot_be_reconciled_with_a_number(run):
    result, _ = run
    columns = {name: list(values)
               for name, values in external_from(result).columns.items()}
    columns["claims"] = ["n/a"] * len(columns["t"])
    spec = ParitySpec.from_results(result, ExternalTable(columns),
                                   {"claims": "claims"})
    with pytest.raises(ParityError, match="not numeric"):
        diff(spec)


def test_ragged_input_raises(run):
    with pytest.raises(ParityError, match="unequal lengths"):
        ExternalTable({"a": [1, 2], "b": [1]})
    with pytest.raises(ParityError, match="no rows"):
        ExternalTable.from_rows([])


def test_a_three_dimensional_result_is_refused():
    """A stochastic result has no ``(t, model point)`` view until somebody
    chooses one, and choosing it silently would be the wrong reconciliation
    quietly done."""

    class Cube:
        mp_ids = ["A"]

        def array(self, name):
            return np.zeros((3, 1, 4))

    with pytest.raises(ParityError, match="3-dimensional"):
        ParitySpec.from_results(Cube(), ExternalTable({"modelpoint_id": ["A"],
                                                       "t": [0], "x": [0.0]}),
                                {"x": "x"})


# --------------------------------------------------------------------------
# Reading an extract
# --------------------------------------------------------------------------

def test_csv_round_trips_with_types_inferred(run, tmp_path):
    result, _ = run
    table = external_from(result)
    path = tmp_path / "extract.csv"
    lines = [",".join(table.names)]
    for i in range(table.n_rows):
        lines.append(",".join(repr(table.columns[name][i])
                              if isinstance(table.columns[name][i], float)
                              else str(table.columns[name][i])
                              for name in table.names))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    read = ExternalTable.read_csv(path)
    assert read.column("t")[:3] == [0, 1, 2]            # integers stay integers
    assert read.column("modelpoint_id")[0] == "T1"      # and text stays text
    assert read.digest == table.digest                  # repr round-trips float64
    assert diff(spec_for(result, read)).ok


def test_a_short_csv_row_names_its_line(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("a,b,c\n1,2,3\n4,5\n", encoding="utf-8")
    with pytest.raises(ParityError, match=r"bad\.csv:3"):
        ExternalTable.read_csv(path)


def test_a_blank_cell_is_missing_not_zero(tmp_path):
    path = tmp_path / "gaps.csv"
    path.write_text("id,t,claims\nA,0,1.5\nA,1,\n", encoding="utf-8")
    table = ExternalTable.read_csv(path)
    assert table.column("claims") == [1.5, None]


@pytest.mark.skipif(
    pytest.importorskip is None, reason="unreachable"
)
def test_parquet_reads_the_same_table(run, tmp_path):
    pq = pytest.importorskip("pyarrow.parquet")
    pa = pytest.importorskip("pyarrow")
    result, _ = run
    table = external_from(result)
    path = tmp_path / "extract.parquet"
    pq.write_table(
        pa.table({name: list(values) for name, values in table.columns.items()}),
        path,
    )
    assert ExternalTable.read_parquet(path).digest == table.digest


# --------------------------------------------------------------------------
# The deliverable, and its place in the registry
# --------------------------------------------------------------------------

def test_the_markdown_states_the_verdict_and_names_the_worst_cell(run):
    result, record = run
    table = perturb(external_from(result), "claims", 5, 1.0)
    markdown = diff(spec_for(result, table),
                    results_digest=record.results_digest).to_markdown()
    assert markdown.startswith("# Parity report")
    assert "**DIFFERENCES FOUND.**" in markdown
    assert record.results_digest in markdown
    assert "Cells outside tolerance" in markdown
    assert "`claims`" in markdown
    assert "| T1 | 5 |" in markdown

    clean = diff(spec_for(result, external_from(result))).to_markdown()
    assert "**PARITY**" in clean
    assert "Cells outside tolerance" not in clean


def test_a_reconciliation_is_recorded_against_both_digests(run):
    result, record = run
    report = diff(spec_for(result, external_from(result)),
                  results_digest=record.results_digest)
    registry = ArtifactRegistry()
    entry = record_parity(report, registry)

    assert entry.kind == "parity"
    assert entry.ok is True
    assert entry.inputs["results_digest"] == record.results_digest
    assert entry.inputs["external_digest"] == report.external_digest
    assert entry.content_digest == report.digest
    # Recording the same reconciliation twice is one entry, not two.
    assert record_parity(report, registry) is entry
    assert len(registry) == 1
    assert registry.find(entry.artifact_id) is entry
    assert registry.of_kind("parity") == [entry]


def test_a_reconciliation_that_changed_its_mind_is_refused(run):
    result, record = run
    report = diff(spec_for(result, external_from(result)),
                  results_digest=record.results_digest)
    registry = ArtifactRegistry()
    record_parity(report, registry)
    forged = ArtifactRecord(
        artifact_id=parity_artifact(report).artifact_id, kind="parity",
        content_digest="0" * 32, inputs={}, ok=True,
    )
    with pytest.raises(ArtifactConflictError, match="has now produced"):
        registry.add(forged)


def test_the_artifact_registry_round_trips_through_json(run, tmp_path):
    result, record = run
    report = diff(spec_for(result, external_from(result)),
                  results_digest=record.results_digest)
    registry = ArtifactRegistry()
    entry = record_parity(report, registry)
    path = tmp_path / "artifacts.json"
    registry.to_json(path)
    back = ArtifactRegistry.from_json(path)
    assert len(back) == 1
    assert back.records[0] == entry
    assert json.loads(path.read_text())[0]["kind"] == "parity"


def test_the_same_report_digests_the_same_and_a_different_one_does_not(run):
    """The registry's assertion only means something if the content digest
    moves when the content does."""
    result, record = run
    clean = diff(spec_for(result, external_from(result)),
                 results_digest=record.results_digest)
    again = diff(spec_for(result, external_from(result)),
                 results_digest=record.results_digest)
    perturbed = diff(spec_for(result, perturb(external_from(result),
                                              "claims", 5, 1e-6)),
                     results_digest=record.results_digest)
    assert clean.digest == again.digest
    assert clean.digest != perturbed.digest
    assert (parity_artifact(clean).artifact_id
            != parity_artifact(perturbed).artifact_id)
    assert parity_artifact(perturbed).ok is False


def test_the_spec_digest_moves_with_the_tolerance(run):
    """Two reconciliations of the same numbers under different tolerances are
    different reconciliations, and the registry must not conflate them."""
    result, _ = run
    table = external_from(result)
    strict = diff(spec_for(result, table))
    loose = diff(spec_for(result, table,
                          tolerance=TolerancePolicy(Tolerance(absolute=1.0))))
    assert strict.ok and loose.ok
    assert strict.spec_digest != loose.spec_digest
    assert fingerprint(Tolerance()) != fingerprint(Tolerance(absolute=1.0))
