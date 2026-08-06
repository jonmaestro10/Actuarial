"""The evidence pack, and the ways a validation file flatters its author.

RFC-049. A pack of self-assessments is worth nothing unless it fails in the
ways a sceptic would check for, so that is what these tests hold it to:

- **It must not go stale.** Every section is recomputed, and a rebuild from
  the same source digests identically — which is also what makes the digest
  worth quoting.
- **It must not omit.** A section that could not be built says so, in the
  index, and the pack's registry record is marked incomplete.
- **It must not overclaim.** The equivalence attestation is *run*, not
  quoted; a template outside the equivalence class is reported as outside
  it rather than as agreeing or as failing; a template that errors is
  reported as not run.
- **Its digest must mean one thing.** The machine is context and stays out
  of the digest; benchmark numbers are claims and go in — and the index says
  which of those two a given pack is.
"""

import json

import pytest

from engine.core.registry import ArtifactRegistry, record_run
from engine.data.assumptions import Assumptions, MortalityTable
from engine.data.modelpoints import ModelPoint
from engine.library.term_life import TermLife
from engine.parity import ExternalTable, ParitySpec, diff, record_parity
from engine.report.evidence import (
    EvidencePack,
    Section,
    build_pack,
    closed_form_identities,
    docstring_coverage,
    executor_equivalence,
    parity_reports,
)
# Imported under another name: pytest would collect a module-level
# ``test_inventory`` as a test case, and it is a builder.
from engine.report.evidence import test_inventory as collect_tests

QX = {age: min(0.0006 * 1.095 ** (age - 20), 0.5) for age in range(20, 111)}
ASSUMPTIONS = Assumptions(mortality=MortalityTable(QX), lapse=0.04,
                          interest=0.03, expense_per_policy=50.0)
POINTS = [ModelPoint(id="T1", age_at_entry=45, term_years=20,
                     sum_assured=250_000.0, annual_premium=1_100.0,
                     init_pols=1)]
OUTPUTS = ["pols_if", "claims", "premiums"]

SPECIMEN = {"name": "TermLife", "model_cls": TermLife, "modelpoints": POINTS,
            "assumptions": ASSUMPTIONS, "proj_len": 20, "outputs": OUTPUTS}


@pytest.fixture(scope="module")
def pack():
    """A pack over one specimen, without the pytest collection step.

    Collecting the suite from inside the suite would be a recursion, and the
    collection is exercised separately on a temporary directory below.
    """
    return build_pack(specimens=[SPECIMEN], collect_tests=False)


# --------------------------------------------------------------------------
# It must not go stale
# --------------------------------------------------------------------------

def test_a_rebuild_from_the_same_source_digests_identically(pack):
    """F1's acceptance criterion, and the reason the digest is quotable."""
    again = build_pack(specimens=[SPECIMEN], collect_tests=False)
    assert again.digest == pack.digest
    assert again.to_markdown() == pack.to_markdown()


def test_the_machine_is_context_and_stays_out_of_the_digest(pack):
    here = EvidencePack(pack.sections, context={"machine": "a"})
    there = EvidencePack(pack.sections, context={"machine": "b"})
    assert here.digest == there.digest == pack.digest
    assert here.manifest()["environment_in_digest"] is False
    assert "outside the digest" in here.to_markdown()


def test_a_benchmark_is_a_claim_and_does_go_in(pack):
    measured = build_pack(
        specimens=[SPECIMEN], collect_tests=False,
        benchmark_records=[{"benchmark": "100k x 60y", "seconds": 12.5}],
    )
    assert measured.digest != pack.digest
    assert measured.machine_specific
    assert not pack.machine_specific
    assert "only on the machine that built it" in measured.to_markdown()
    assert "no machine-specific claim" in pack.to_markdown()
    assert "100k x 60y" in measured.to_markdown()


def test_the_digest_moves_when_the_evidence_does(pack):
    weaker = EvidencePack(
        tuple(s for s in pack.sections if s.name != "coverage"),
        context=dict(pack.context),
    )
    assert weaker.digest != pack.digest


# --------------------------------------------------------------------------
# It must not omit
# --------------------------------------------------------------------------

def test_a_section_that_could_not_be_built_says_so(pack):
    """``collect_tests=False`` is the benign case of a section that is not
    there, and it must read the same as the hostile one."""
    tests = pack.section("tests")
    assert tests.content["available"] is False
    assert "Not collected" in tests.summary
    assert "Not collected" in pack.to_markdown()
    identities = pack.section("identities")
    assert identities.content["available"] is False


def test_an_incomplete_pack_is_recorded_as_incomplete(pack):
    registry = ArtifactRegistry()
    record = pack.record(registry)
    assert record.kind == "evidence"
    assert record.ok is False              # the test inventory is missing
    assert record.content_digest == pack.digest
    assert pack.record(registry) is record  # recorded twice, filed once
    assert len(registry) == 1


def test_a_pack_built_two_different_ways_is_two_different_derivations(pack):
    """A pack that skipped a section and one that did not are different
    questions; the registry must not be asked to call them contradictory."""
    complete = EvidencePack(
        tuple(Section(s.name, s.title, s.summary,
                      {**dict(s.content), "available": True})
              for s in pack.sections),
        context=dict(pack.context),
    )
    registry = ArtifactRegistry()
    first = pack.record(registry)
    second = complete.record(registry)
    assert first.artifact_id != second.artifact_id
    assert len(registry) == 2


# --------------------------------------------------------------------------
# It must not overclaim
# --------------------------------------------------------------------------

def test_the_equivalence_attestation_is_run_not_quoted(pack):
    section = pack.section("equivalence")
    row = section.content["templates"][0]
    assert row["template"] == "TermLife"
    assert row["bitwise_identical"] is True
    assert row["in_equivalence_class"] is True
    assert set(row["digests"]) == {"interpreted", "vectorized"}
    # The digest in the pack is the registry's own, so a reader can rerun it.
    _, record = record_run(TermLife, POINTS, ASSUMPTIONS, 20, outputs=OUTPUTS,
                           executor="vectorized")
    assert row["results_digest"] == record.results_digest


def test_a_pooled_template_is_outside_the_class_not_in_breach_of_it():
    """The pack's first chance to lie: a pooled model reduces across the
    block and the interpreted executor sees one policy, so 'disagreed' would
    be the wrong word for it."""
    from engine.library.with_profits import WithProfitsEndowment

    specimen = {
        "name": "WithProfitsEndowment", "model_cls": WithProfitsEndowment,
        "modelpoints": [
            ModelPoint(id="W1", age_at_entry=40, term_years=20,
                       sum_assured=100_000.0, annual_premium=4_000.0,
                       init_pols=100),
            ModelPoint(id="W2", age_at_entry=50, term_years=15,
                       sum_assured=50_000.0, annual_premium=2_500.0,
                       init_pols=50),
        ],
        "assumptions": ASSUMPTIONS, "proj_len": 21,
        "outputs": ["pols_if", "asset_share", "aggregate_asset_share"],
    }
    section = executor_equivalence([specimen])
    row = section.content["templates"][0]
    assert row["in_equivalence_class"] is False
    assert row["bitwise_identical"] is False
    assert "pooled variables" in row["excluded_because"]
    assert row["repeats_deterministically"] is True
    # RFC-061's bridge: the formulas are still in the class, and the pack
    # records that rather than leaving the row looking like a gap.
    assert row["bitwise_on_one_modelpoint"] is True
    assert row["executors"] == ["vectorized"]
    assert section.content["n_in_class"] == 0
    assert section.content["n_outside_class"] == 1
    assert "outside it by construction" in section.summary
    assert "block of one" in section.summary


def test_a_template_that_cannot_run_is_reported_as_not_run():
    broken = {**SPECIMEN, "name": "Broken",
              "modelpoints": [ModelPoint(id="X", age_at_entry=40)]}
    section = executor_equivalence([broken])
    row = section.content["templates"][0]
    assert row["error"]
    assert row["bitwise_identical"] is False
    assert row["results_digest"] is None
    pack = EvidencePack((section,))
    assert "NOT RUN" in pack.to_markdown()


def test_coverage_is_measured_off_the_library():
    section = docstring_coverage()
    documented, total = (section.content["documented"], section.content["total"])
    assert 0 < documented <= total
    assert section.content["per_template"]["TermLife"][1] > 0
    assert f"{documented:,}" in section.summary


# --------------------------------------------------------------------------
# The sections that read other things
# --------------------------------------------------------------------------

def test_the_test_inventory_is_collected_from_pytest(tmp_path):
    """Collected rather than counted: a number typed into a document is a
    number that drifts."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_specimen.py").write_text(
        "def test_one():\n    assert True\n\n\n"
        "def test_two():\n    assert True\n", encoding="utf-8")
    (tmp_path / "pytest.ini").write_text(
        "[pytest]\ntestpaths = tests\n", encoding="utf-8")

    section = collect_tests(tmp_path)
    assert section.content["available"] is True
    assert section.content["n_tests"] == 2
    assert section.content["files"]["tests/test_specimen.py"] == [
        "test_one", "test_two"]
    assert "2 tests collected" in section.summary


def test_the_inventory_reports_a_failed_collection_rather_than_zero(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_broken.py").write_text(
        "this is not python(\n", encoding="utf-8")
    (tmp_path / "pytest.ini").write_text(
        "[pytest]\ntestpaths = tests\n", encoding="utf-8")
    section = collect_tests(tmp_path)
    assert section.content["available"] is False
    assert "claims nothing" in section.summary


def test_identities_are_derived_from_the_inventory_and_report_absences():
    inventory = Section(
        "tests", "Test inventory", "2 collected",
        {"available": True, "files": {"tests/test_closed_form.py": ["a", "b"],
                                      "tests/test_other.py": ["c"]}},
    )
    section = closed_form_identities(
        inventory, files=("tests/test_closed_form.py", "tests/test_gone.py"))
    assert section.content["n_identities"] == 2
    assert section.content["not_collected"] == ["tests/test_gone.py"]
    assert "not collected" in section.summary


def test_reconciliations_on_record_carry_both_digests():
    result, record = record_run(TermLife, POINTS, ASSUMPTIONS, 20,
                                outputs=OUTPUTS)
    rows = []
    for j, mp_id in enumerate(result.mp_ids):
        for t in range(21):
            rows.append({"modelpoint_id": mp_id, "t": t,
                         **{name: float(result.array(name)[t, j])
                            for name in OUTPUTS}})
    spec = ParitySpec.from_results(
        result, ExternalTable.from_rows(rows),
        {name: name for name in OUTPUTS}, label="TermLife self-check",
    )
    report = diff(spec, results_digest=record.results_digest)
    registry = ArtifactRegistry()
    record_parity(report, registry)

    section = parity_reports(registry)
    assert section.content["n_reports"] == 1
    row = section.content["reports"][0]
    assert row["results_digest"] == record.results_digest
    assert row["external_digest"] == report.external_digest
    assert row["ok"] is True
    assert "TermLife self-check" in EvidencePack((section,)).to_markdown()


def test_no_reconciliation_on_record_is_stated_not_implied():
    section = parity_reports(ArtifactRegistry())
    assert section.content["n_reports"] == 0
    assert "makes no reconciliation claim" in section.summary


# --------------------------------------------------------------------------
# What lands on disk
# --------------------------------------------------------------------------

def test_the_pack_is_written_content_addressed(pack, tmp_path):
    directory = pack.write(tmp_path)
    assert directory.name == pack.digest
    written = {path.name for path in directory.iterdir()}
    assert written == {"index.md", "manifest.json", "environment.json",
                       "tests.json", "identities.json", "equivalence.json",
                       "coverage.json", "parity.json", "audit.json",
                       "benchmarks.json"}

    manifest = json.loads((directory / "manifest.json").read_text())
    assert manifest["pack_digest"] == pack.digest
    assert set(manifest["sections"]) == {s.name for s in pack.sections}
    for section in pack.sections:
        assert manifest["sections"][section.name] == section.digest
        assert json.loads(
            (directory / f"{section.name}.json").read_text()
        ) == json.loads(json.dumps(dict(section.content), default=str))

    index = (directory / "index.md").read_text()
    assert index.startswith("# Validation evidence pack")
    assert pack.digest in index

    # Written twice, same bytes: the pack is the pack.
    before = (directory / "index.md").read_text()
    pack.write(tmp_path)
    assert (directory / "index.md").read_text() == before
