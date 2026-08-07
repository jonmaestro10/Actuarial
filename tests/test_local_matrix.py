"""What `scripts/local_matrix.py` must refuse to do quietly.

The script substitutes for CI while the repository has no Actions minutes, and
every test here guards the same class of failure: a run that covered less than
it appears to have covered. A local matrix that silently checks one Python and
prints a green summary is worse than no local matrix, because it is indexed as
evidence.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

yaml = pytest.importorskip(
    "yaml",
    reason="PyYAML is in the `test` extra; the workflow reader needs it",
)

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "local_matrix.py"


def _load_script():
    """Import the script by path — `scripts/` is not a package.

    Registered in `sys.modules` before execution because `@dataclass` resolves
    a field's type by looking its own module up there, and a module that is
    mid-import is not yet listed.
    """
    spec = importlib.util.spec_from_file_location("_local_matrix", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


lm = _load_script()


def _workflow(text: str, tmp_path: Path) -> Path:
    path = tmp_path / "ci.yml"
    path.write_text(text)
    return path


# --------------------------------------------------------------------------
# The real workflow
# --------------------------------------------------------------------------

def test_every_job_in_the_real_workflow_is_read():
    """Guards the reader falling out of step with the workflow it reads.

    If a job stops being recognised — a renamed action, a restructured matrix —
    the honest outcome is an exception. The dangerous one is a shorter job list
    that still passes, because the summary would then describe a matrix that
    was never run.
    """
    jobs = lm.read_jobs()
    document = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    assert {j.name for j in jobs} == set(document["jobs"])
    for job in jobs:
        assert job.python_versions, f"{job.name} named no interpreter"
        assert job.steps, f"{job.name} produced no run steps"


def test_the_version_list_is_derived_from_the_workflow_not_restated():
    """Guards the second source of truth this script exists to avoid.

    A local matrix carrying its own copy of the version list agrees with CI
    exactly until someone adds a version to CI — and then reports a full green
    while checking one fewer than it claims. The mechanism is that changing the
    workflow changes what the reader returns, so this changes it and looks.
    """
    original = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    document = yaml.safe_load(original)
    matrix_job = next(
        name for name, body in document["jobs"].items()
        if "python-version" in body.get("strategy", {}).get("matrix", {})
    )
    document["jobs"][matrix_job]["strategy"]["matrix"]["python-version"].append("3.99")

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "ci.yml"
        path.write_text(yaml.safe_dump(document))
        jobs = {j.name: j for j in lm.read_jobs(path)}
    assert "3.99" in jobs[matrix_job].python_versions


def test_a_pinned_step_environment_survives_into_the_local_run():
    """Guards the local run skipping what the CI job forces to execute.

    `bitwise-boundary` sets REQUIRE_COMPILE_EXTRA=1 precisely so that a missing
    compiler fails instead of skipping 39 measurement cases. If the reader
    dropped step `env`, the local run would skip them and report green — the
    exact failure the environment variable was introduced to prevent, silently
    reintroduced one layer out.
    """
    jobs = {j.name: j for j in lm.read_jobs()}
    boundary = jobs["bitwise-boundary"]
    forced = [s for s in boundary.steps if s.env.get("REQUIRE_COMPILE_EXTRA") == "1"]
    assert forced, (
        "no step in bitwise-boundary carries REQUIRE_COMPILE_EXTRA=1 into the "
        "local run; the measurement cases would skip and read as passing"
    )


# --------------------------------------------------------------------------
# Refusals
# --------------------------------------------------------------------------

def test_a_job_naming_no_interpreter_is_an_error_not_an_empty_loop(tmp_path):
    """Guards the empty-collection trap, at the level of a whole job.

    A job whose Python version cannot be found yields no (job, version) pairs.
    Iterating over nothing asserts nothing and exits zero, which is how a
    matrix that ran no jobs at all comes to look like one that passed them.
    """
    path = _workflow(
        "name: CI\non:\n  push:\njobs:\n"
        "  test:\n    runs-on: ubuntu-latest\n"
        "    steps:\n      - run: pytest -q\n",
        tmp_path,
    )
    with pytest.raises(lm.WorkflowUnreadable, match="names no Python version"):
        lm.read_jobs(path)


def test_a_job_with_no_run_steps_is_an_error(tmp_path):
    """Guards a job that would 'pass' by executing nothing."""
    path = _workflow(
        "name: CI\non:\n  push:\njobs:\n"
        "  test:\n    runs-on: ubuntu-latest\n"
        "    steps:\n      - uses: actions/setup-python@v5\n"
        "        with:\n          python-version: '3.11'\n",
        tmp_path,
    )
    with pytest.raises(lm.WorkflowUnreadable, match="no run steps"):
        lm.read_jobs(path)


def test_an_empty_matrix_list_is_an_error(tmp_path):
    """Guards a matrix that collects nothing, the parametrised-over-[] shape."""
    path = _workflow(
        "name: CI\non:\n  push:\njobs:\n"
        "  test:\n    runs-on: ubuntu-latest\n"
        "    strategy:\n      matrix:\n        python-version: []\n"
        "    steps:\n      - run: pytest -q\n",
        tmp_path,
    )
    with pytest.raises(lm.WorkflowUnreadable, match="non-empty list"):
        lm.read_jobs(path)


# --------------------------------------------------------------------------
# The verdict must carry its own coverage
# --------------------------------------------------------------------------

def test_a_missing_interpreter_is_not_checked_and_is_not_a_pass():
    """Guards the headline failure: a partial matrix reported as a full one.

    `NOT CHECKED` and `pass` must be distinguishable in the outcome, in the
    summary, and in the exit status. A machine missing an interpreter is the
    common case, not the exotic one, which is what makes it dangerous.
    """
    outcome = lm.Outcome(job="test", version="3.99", checked=False,
                         detail="no python3.99 on PATH")
    assert outcome.verdict == "NOT CHECKED"
    assert not outcome.passed

    text = lm.summarise([outcome], allow_uncovered=False)
    assert "NOT CHECKED" in text
    assert "INCOMPLETE" in text
    assert "3.99" in text


def test_failure_and_absence_are_reported_as_different_things():
    """Guards the two collapsing into one 'did not pass'.

    They call for opposite responses: a FAIL is a defect in the code, a NOT
    CHECKED is a gap in the machine. A summary that renders them the same way
    sends the reader to the wrong place.
    """
    failed = lm.Outcome("test", "3.11", checked=True, passed=False,
                        failing_step="pytest -q")
    absent = lm.Outcome("test", "3.99", checked=False, detail="no python3.99 on PATH")
    text = lm.summarise([failed, absent], allow_uncovered=False)
    assert "FAIL" in text and "NOT CHECKED" in text
    assert failed.verdict != absent.verdict


def test_allow_uncovered_still_prints_the_gap_alongside_the_verdict():
    """Guards a coverage caveat that can be read separately from the result.

    The escape hatch is legitimate — an interpreter genuinely may be absent —
    but a run that passed *because* the check was waived must say so in the
    same breath, or the caveat is lost the first time someone quotes the
    summary's last line.
    """
    absent = lm.Outcome("test", "3.99", checked=False, detail="no python3.99 on PATH")
    text = lm.summarise([absent], allow_uncovered=True)
    assert "INCOMPLETE" in text
    assert "--allow-uncovered" in text
    assert "3.99" in text


def test_an_uncovered_version_sets_a_failing_exit_status(monkeypatch):
    """Guards the mechanism, not the prose that describes it.

    The summary saying INCOMPLETE is worth nothing if the process exits zero:
    a caller in a shell script, or a future CI step, reads the status and never
    the text. This drives `main` with an interpreter that cannot be found and
    asserts on what the shell would see.
    """
    job = lm.Job(name="test", python_versions=("3.99",),
                 steps=(lm.Step(name="noop", run="true"),))
    monkeypatch.setattr(lm, "read_jobs", lambda *a, **k: (job,))
    monkeypatch.setattr(lm, "interpreter_for", lambda version: None)

    assert lm.main([]) == 1, "an unchecked version exited zero"
    assert lm.main(["--allow-uncovered"]) == 0, "the escape hatch did not open"


def test_a_failing_step_sets_a_failing_exit_status(monkeypatch):
    """Guards a red run reported green, via the status the caller reads.

    Paired with the test above so that the two ways of not passing are each
    pinned to the exit code separately — a `main` that returned 1 only for
    absent interpreters would satisfy the other test alone.

    The version is the *running* interpreter's rather than a literal, because
    a literal is an assumption about the machine: this test first ran on a CI
    job that had only 3.12 on PATH, where a hard-coded "3.11" made the step
    NOT CHECKED, so the failing command never executed and the run this test
    exists to catch exited zero. A test about unnoticed absence should not
    have been the thing that assumed presence.
    """
    here = f"{sys.version_info.major}.{sys.version_info.minor}"
    job = lm.Job(name="test", python_versions=(here,),
                 steps=(lm.Step(name="deliberate failure", run="exit 3"),))
    monkeypatch.setattr(lm, "read_jobs", lambda *a, **k: (job,))

    assert lm.main([]) == 1, "a failing step exited zero"
    assert lm.main(["--allow-uncovered"]) == 1, (
        "--allow-uncovered waived a real failure; it may only waive absence"
    )


def test_the_summary_never_claims_more_than_one_machine():
    """Guards the claim CLAUDE.md forbids restoring.

    A pack digest is an identity on a machine. `np.exp` and `**` are not
    bit-portable, so no number of local interpreters substitutes for CI's
    second machine — and the summary of a green run is exactly where an
    over-broad reproducibility claim would get reintroduced.
    """
    passing = lm.Outcome("test", "3.11", checked=True, passed=True, seconds=1.0)
    text = lm.summarise([passing], allow_uncovered=False)
    assert "one machine" in text
    assert "cross-machine" in text
