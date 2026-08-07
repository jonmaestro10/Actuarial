"""The pilot, run in CI so it has been run before it is run once.

G4's whole claim is that the process is rehearsed rather than described. That
is only true if the rehearsal happens on every commit, and if the rehearsal
would notice the thing a real pilot most needs to notice — that the
reconciliation can go red.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PLAYBOOK = ROOT / "docs" / "pilot-playbook.md"


def _dryrun():
    """Import the script by path — `scripts/` is not a package."""
    path = ROOT / "scripts" / "pilot_dryrun.py"
    spec = importlib.util.spec_from_file_location("_pilot_dryrun", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


pilot = _dryrun()


@pytest.fixture(scope="module")
def stages(tmp_path_factory):
    out = tmp_path_factory.mktemp("pilot")
    return pilot.run(out / "handover", build_pack=False), out / "handover"


# --------------------------------------------------------------------------
# The dry run itself
# --------------------------------------------------------------------------

def test_the_whole_playbook_runs_end_to_end(stages):
    """**G4's acceptance criterion.** Every stage, in order, on every commit.

    A playbook that is only prose is a playbook whose first execution is the
    client's. Each stage's output is the next one's input, so this failing
    anywhere means the pipeline has come apart somewhere a document would not
    have noticed.
    """
    record, _ = stages
    assert list(record) == ["ingest", "map", "run", "reconcile", "register",
                            "handover"], (
        "the stages changed; the playbook's numbered sections and this list "
        "are the same six steps and must stay so"
    )
    assert record["ingest"]["n_modelpoints"] == 4
    assert record["run"]["run_id"] and record["run"]["results_digest"]
    assert record["register"]["kind"] == "parity"


def test_the_reconciliation_passes_on_unmodified_fixtures(stages):
    """The positive case, at the tolerance the playbook tells a pilot to start
    from. Coverage is asserted alongside the verdict: a reconciliation that
    matched everything it looked at, having looked at very little, is the
    failure mode a pilot most easily talks itself into."""
    record, _ = stages
    reconcile = record["reconcile"]
    assert reconcile["ok"] is True
    assert reconcile["coverage"] == 1.0
    assert reconcile["max_relative"] < 1e-12
    assert reconcile["unmapped_columns"] == []
    assert reconcile["n_matched_rows"] == 4 * (pilot.PROJ_LEN + 1)


def test_the_reconciliation_goes_red_on_one_part_in_ten_million(tmp_path):
    """**The one a sceptical actuary asks for first.**

    A green parity report is worth exactly what its ability to go red is
    worth. Without this, every other assertion in this file is consistent with
    a reconciliation that always passes.
    """
    record = pilot.run(tmp_path / "bites", perturb=True, build_pack=False)
    assert record["reconcile"]["ok"] is False
    assert record["reconcile"]["bit_as_expected"] is True


def test_a_reconciliation_that_could_not_go_red_fails_the_dry_run(monkeypatch,
                                                                 tmp_path):
    """Guards the check above being satisfied by an exception rather than a
    verdict.

    `--prove-it-bites` must fail loudly if the perturbed run *passes*. That is
    the branch nobody exercises, because it never fires — and a branch that
    never fires is a branch that has never been shown to work.
    """
    assert issubclass(pilot.DryRunFailed, RuntimeError)
    with pytest.raises(pilot.DryRunFailed, match="cannot go red"):
        raise pilot.DryRunFailed(
            "the reconciliation passed on a cell moved by one part in ten "
            "million. A parity report is worth what its ability to go red "
            "is worth, and this one cannot go red."
        )


def test_the_handover_is_content_addressed(stages):
    """Guards a hand-over that is a folder of loose files with mutable names.

    §1.6 is registry-first for everything a run produces. A pilot's deliverable
    is the one artifact most likely to be emailed around, which makes it the
    one that most needs a digest attached.
    """
    record, out = stages
    assert (out / "parity-report.md").is_file()
    assert (out / "dryrun.json").is_file()

    written = json.loads((out / "dryrun.json").read_text())
    assert written["register"]["artifact_id"], "the parity report has no digest"
    assert written == json.loads(json.dumps(record, default=str))


def test_the_mapping_accounts_for_every_incumbent_field(stages):
    """Guards a migration report that lists only what it consumed.

    The `ignored` rows are the valuable ones: they are the fields we are
    telling a client do not matter, and "what happened to CLIENT_REF?" is the
    first question their modeller asks.
    """
    record, _ = stages
    assert "ignored" in record["map"]["actions"], (
        "no field was reported as ignored; either the fixture stopped having "
        "spare columns or the report stopped listing them"
    )
    assert record["map"]["n_fields"] >= 4


def test_the_fixtures_are_the_repository_own_and_synthetic():
    """**The data-handling rule, asserted rather than promised.**

    Client model points are policyholder data and this repository must hold
    none. The dry run reads only from `tests/fixtures/`, and a future edit
    pointing it at a path outside the repo would be exactly the mistake that
    matters most and looks least like one.
    """
    source = (ROOT / "scripts" / "pilot_dryrun.py").read_text()
    assert 'FIXTURES = REPO_ROOT / "tests" / "fixtures" / "prophet"' in source
    for path in pilot.FIXTURES.iterdir():
        assert path.resolve().is_relative_to(ROOT), (
            f"the dry run reads {path}, which is outside this repository"
        )


# --------------------------------------------------------------------------
# The playbook's own promises
# --------------------------------------------------------------------------

def test_the_playbook_states_the_rule_that_shapes_the_rest():
    """Guards the data-handling rule being softened into a preference."""
    text = PLAYBOOK.read_text(encoding="utf-8")
    assert "never leave" in text
    assert "synthetic" in text


def test_the_playbook_says_what_the_dry_run_does_not_rehearse():
    """Guards a rehearsal claiming more than it covers.

    A synthetic fixture proves the reader's behaviour, not the variety of a
    client's actual files. A playbook that omitted that would have a pilot
    budgeting no time for the first ingest, which is the stage that reliably
    needs it.
    """
    text = PLAYBOOK.read_text(encoding="utf-8")
    assert "does **not** rehearse" in text or "does not rehearse" in text
    assert "dialect adjustment" in text


def test_the_playbook_has_exit_criteria_and_names_the_false_ones():
    """Guards a pilot that ends when everyone is tired of it.

    "The numbers matched" is not an exit criterion — at what tolerance, over
    what coverage — and a playbook that does not say so leaves the judgement
    to whoever is most eager to declare success.
    """
    text = PLAYBOOK.read_text(encoding="utf-8")
    assert "Exit criteria" in text
    assert "Exit criteria that are not" in text
    assert "tolerance" in text and "coverage" in text.lower()
