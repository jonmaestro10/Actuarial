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


def test_a_job_pinned_to_another_architecture_is_not_run_here(tmp_path):
    """Guards a green line for a job that was never really run.

    The arm64 job exists precisely because the architecture decides the answer
    — RFC-072's correction is a test that asserted a property of the silicon.
    Executing its steps on x86 would pass and would mean nothing, which is this
    module's own failure mode reached from the other side: not a check that was
    skipped and read as passing, but a check that ran on the wrong thing and
    read as passing.

    The step here is one that *would* fail if it ran, so a pass could only come
    from the architecture guard working.
    """
    foreign = "x64" if lm.host_architecture() == "arm64" else "arm64"
    label = "ubuntu-24.04-arm" if foreign == "arm64" else "ubuntu-latest"
    here = f"{sys.version_info.major}.{sys.version_info.minor}"
    job = lm.Job(name="elsewhere", python_versions=(here,),
                 steps=(lm.Step(name="would fail if it ran", run="exit 3"),),
                 runs_on=label)

    outcome = lm.run_job(job, here, echo=False)
    assert not outcome.checked, "a foreign-architecture job was run anyway"
    assert outcome.kind == "architecture"
    assert outcome.verdict == "CI ONLY"
    assert foreign in outcome.detail and lm.host_architecture() in outcome.detail


def test_another_architecture_is_reported_but_does_not_fail_the_run(monkeypatch):
    """Guards the gate being made useless by being made stricter.

    An x86 machine cannot stand in for the arm64 job at *any* point, so if that
    failed the run, the documented pre-merge command could never pass. Everyone
    would then pass `--allow-uncovered` by reflex — and that flag also waives a
    missing interpreter, which is a real, fixable gap. Being strict here would
    buy nothing and spend the strictness that matters.

    So: exit 0, and the fact is printed in the verdict rather than a footnote.
    The companion test below keeps the interpreter case fatal.
    """
    here = f"{sys.version_info.major}.{sys.version_info.minor}"
    foreign_label = ("ubuntu-latest" if lm.host_architecture() == "arm64"
                     else "ubuntu-24.04-arm")
    job = lm.Job(name="test-elsewhere", python_versions=(here,),
                 steps=(lm.Step(name="would fail if it ran", run="exit 3"),),
                 runs_on=foreign_label)
    monkeypatch.setattr(lm, "read_jobs", lambda *a, **k: (job,))

    assert lm.main([]) == 0, (
        "a job on another architecture failed the run; the local gate can "
        "then never pass and --allow-uncovered becomes reflexive"
    )
    text = lm.summarise([lm.run_job(job, here, echo=False)],
                        allow_uncovered=False)
    assert "Not runnable on this" in text and "test-elsewhere" in text
    assert "Only CI covers that" in text


def test_a_missing_interpreter_still_fails_even_beside_another_architecture():
    """Guards the architecture exemption leaking onto the fixable case.

    These two are adjacent in the code and it would be easy to widen one into
    the other. A missing interpreter is a gap in this machine's setup that the
    developer can close by installing a Python, and it must keep failing.
    """
    absent = lm.Outcome("test", "3.99", checked=False, kind="interpreter",
                        detail="no python3.99 on PATH")
    elsewhere = lm.Outcome("test-arm64", "3.12", checked=False,
                           kind="architecture", detail="needs arm64")
    text = lm.summarise([absent, elsewhere], allow_uncovered=False)

    assert "INCOMPLETE" in text, "the fixable gap stopped being reported"
    assert "3.99" in text
    assert absent.verdict == "NOT CHECKED" and elsewhere.verdict == "CI ONLY", (
        "the two kinds of unchecked render identically, so a reader cannot "
        "tell which one they can do something about"
    )


def test_the_architecture_comes_from_the_runner_label():
    """Guards the arm64 job silently becoming an x64 one.

    GitHub spells the architecture in the label suffix. If a rename made
    `-arm` stop matching, the job would be treated as runnable here and the
    guard above would never fire — the failure would be invisible rather than
    loud, which is the shape this whole module is written against.
    """
    assert lm.Job("j", ("3.12",), (), "ubuntu-24.04-arm").architecture == "arm64"
    assert lm.Job("j", ("3.12",), (), "ubuntu-22.04-arm").architecture == "arm64"
    assert lm.Job("j", ("3.12",), (), "ubuntu-latest").architecture == "x64"

    # And the real workflow still contains a job on each, so the guard has
    # something to guard. A workflow that lost its arm64 job would leave the
    # tests above passing over a matrix that no longer crosses architectures.
    architectures = {j.architecture for j in lm.read_jobs()}
    assert architectures == {"x64", "arm64"}, (
        f"ci.yml covers only {architectures}; the second architecture is what "
        f"catches a test asserting a property of the silicon"
    )


def test_a_job_without_a_runs_on_label_is_an_error(tmp_path):
    """Guards the reader assuming a job can run here when it cannot say."""
    path = _workflow(
        "name: CI\non:\n  pull_request:\njobs:\n"
        "  test:\n"
        "    steps:\n      - uses: actions/setup-python@v5\n"
        "        with:\n          python-version: '3.11'\n"
        "      - run: pytest -q\n",
        tmp_path,
    )
    with pytest.raises(lm.WorkflowUnreadable, match="runs-on"):
        lm.read_jobs(path)


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
