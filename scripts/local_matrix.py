#!/usr/bin/env python3
r"""The matrix CI would have run, run here — and told what it could not check.

GitHub Actions stopped scheduling runs for this repository partway through the
work that produced RFC-071 to RFC-076, because a free-plan account exhausted
its included minutes and a $0 spending limit turns that into *no run object at
all* rather than a failure. Everything merged after that point was verified on
one interpreter, and the repository had no way to say so.

This script closes that gap, and the interesting part is not that it runs the
suite under several Pythons. It is the second half of the sentence.

Silence is the failure mode
---------------------------
A machine with only ``python3.11`` installed can run this and get a clean exit,
a green summary, and a report that looks exactly like one from a machine that
checked the whole matrix. That is the same shape of defect as a parametrised
test over an empty list, or the 39 measurement cases in
``tests/test_bitwise_boundary.py`` that skip when the compiler is absent — and
it is the reason the CI job for those sets ``REQUIRE_COMPILE_EXTRA=1``.

So **a version this script cannot check is a failure, not a footnote.** Missing
interpreters are named in the summary and set the exit status. ``--allow-uncovered``
exists for the case where one genuinely is unavailable, and when it is used the
summary says so in the same breath as the result, because a coverage caveat
that can be read separately from the verdict will be.

Derived, not restated
---------------------
The versions, the steps, and the per-step environment are **read out of
``.github/workflows/ci.yml``**. None of them is written down here.

That is not tidiness. A local matrix that carries its own copy of the workflow
is a second source of truth that agrees with the first exactly until someone
adds a Python version, and then reports a full green while checking one fewer
than it claims. The reader refuses on anything it does not recognise rather
than skipping it — a step it cannot parse is a step that would not run, and a
job with no recognisable interpreter is an error rather than an empty loop.

Each step runs the workflow's own command string through ``bash -e``, which is
what the Actions runner does with ``run:`` on Linux, inside a virtualenv whose
``bin`` is first on ``PATH`` — so ``pip install -e ".[test,data,api,excel]"``
and ``python scripts/evidence_pack.py`` need no rewriting to land in the right
place. The commands are not adapted, which is the point: an adapted command is
a different command.

What this is not
----------------
It is not CI. It runs on one machine, one architecture, one libm, so it cannot
see the cross-machine float difference that CI caught once — ``np.exp`` and
``**`` are not bit-portable, and a pack digest is an identity *on a machine*.
It is the strongest available substitute while the minutes are gone, and the
execution plan records the distinction rather than quietly treating a local
green as the definition of done.

Usage::

    python scripts/local_matrix.py                 # every job in ci.yml
    python scripts/local_matrix.py --job test      # one job
    python scripts/local_matrix.py --allow-uncovered
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"

#: The action whose ``with.python-version`` names a fixed-version job's
#: interpreter. Matched by prefix so a version bump of the action does not
#: silently stop finding it — which would turn a checked job into an
#: unrecognised one, and this module's whole argument is that those must differ.
SETUP_PYTHON = "actions/setup-python"


class WorkflowUnreadable(RuntimeError):
    """The workflow says something this reader does not understand.

    Raised rather than skipped. A job whose interpreter or steps cannot be
    found is not a job with nothing to do, and the difference between those two
    readings is the entire reason this file exists.
    """


@dataclass(frozen=True)
class Step:
    """One ``run:`` step, with the environment the workflow gives it."""

    name: str
    run: str
    env: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Job:
    """A workflow job, the interpreters it is defined over, and where it runs.

    ``runs_on`` is carried because a job pinned to a different architecture is
    one this machine **cannot** stand in for. Running its steps here anyway
    would produce a green line for a job that was never really run, which is
    the failure this module exists to prevent, arrived at from the other side.
    """

    name: str
    python_versions: tuple[str, ...]
    steps: tuple[Step, ...]
    runs_on: str = ""

    @property
    def architecture(self) -> str:
        """``arm64`` or ``x64``, from the runner label.

        GitHub spells the architecture in the label suffix — `ubuntu-24.04-arm`
        against `ubuntu-latest`. Anything without the suffix is x64, which is
        the same default GitHub applies.
        """
        return "arm64" if "-arm" in self.runs_on else "x64"


def host_architecture() -> str:
    """This machine's architecture, in the workflow's vocabulary."""
    machine = platform.machine().lower()
    return "arm64" if machine in {"aarch64", "arm64"} else "x64"


def _load(path: Path = WORKFLOW) -> dict:
    """Parse the workflow, refusing clearly if PyYAML is not installed."""
    try:
        import yaml
    except ModuleNotFoundError as exc:  # pragma: no cover - depends on install
        raise WorkflowUnreadable(
            "PyYAML is required to read the workflow. It is in the `test` "
            "extra: pip install -e '.[test]'. This script will not fall back "
            "to a built-in copy of the matrix — a second source of truth for "
            "the version list is the defect it exists to prevent."
        ) from exc
    if not path.is_file():
        raise WorkflowUnreadable(f"no workflow at {path}")
    return yaml.safe_load(path.read_text())


def read_jobs(path: Path = WORKFLOW) -> tuple[Job, ...]:
    """Every job in the workflow, with its interpreters and its ``run:`` steps.

    A job's Python versions come from ``strategy.matrix.python-version`` when
    it is a matrix job and from the ``setup-python`` step's ``with`` block when
    it pins one. A job with neither is an error: silently returning no versions
    for it would drop it from the run and from the summary at once.
    """
    document = _load(path)
    jobs_block = document.get("jobs")
    if not isinstance(jobs_block, dict) or not jobs_block:
        raise WorkflowUnreadable(f"{path} declares no jobs")

    jobs = []
    for name, body in jobs_block.items():
        raw_steps = body.get("steps")
        if not isinstance(raw_steps, list):
            raise WorkflowUnreadable(f"job {name!r} has no steps list")

        versions: tuple[str, ...] = ()
        matrix = body.get("strategy", {}).get("matrix", {})
        if "python-version" in matrix:
            listed = matrix["python-version"]
            if not isinstance(listed, list) or not listed:
                raise WorkflowUnreadable(
                    f"job {name!r} has a matrix python-version that is not a "
                    f"non-empty list: {listed!r}"
                )
            versions = tuple(str(v) for v in listed)
        else:
            for step in raw_steps:
                uses = str(step.get("uses", ""))
                if uses.startswith(SETUP_PYTHON):
                    pinned = step.get("with", {}).get("python-version")
                    if pinned is None:
                        raise WorkflowUnreadable(
                            f"job {name!r} uses {uses} without a python-version"
                        )
                    versions = (str(pinned),)
                    break
        if not versions:
            raise WorkflowUnreadable(
                f"job {name!r} names no Python version, in a matrix or in a "
                f"{SETUP_PYTHON} step. This reader will not guess one."
            )

        steps = tuple(
            Step(name=str(s.get("name") or s["run"].strip().splitlines()[0]),
                 run=s["run"],
                 env={str(k): str(v) for k, v in (s.get("env") or {}).items()})
            for s in raw_steps if "run" in s
        )
        if not steps:
            raise WorkflowUnreadable(f"job {name!r} has no run steps")

        runs_on = body.get("runs-on")
        if not isinstance(runs_on, str) or not runs_on:
            raise WorkflowUnreadable(
                f"job {name!r} has no plain `runs-on` label. The reader needs "
                f"it to tell whether this machine can stand in for the job at "
                f"all, and will not assume it can."
            )
        jobs.append(Job(name=str(name), python_versions=versions, steps=steps,
                        runs_on=runs_on))
    return tuple(jobs)


def interpreter_for(version: str) -> str | None:
    """The ``pythonX.Y`` on PATH for ``version``, or None if there is not one.

    None is a first-class answer here and the caller must render it. It is
    never folded into "nothing to do".
    """
    found = shutil.which(f"python{version}")
    if found:
        return found
    current = f"{sys.version_info.major}.{sys.version_info.minor}"
    return sys.executable if current == version else None


@dataclass
class Outcome:
    """What happened to one (job, version) pair — including "nothing".

    ``kind`` separates the two ways a pair goes unchecked, and they are not the
    same problem:

    ``"interpreter"``
        A gap in *this machine's setup*. Fixable — install the Python — so it
        fails the run, because a matrix that quietly skipped a version it could
        have run is the thing this module was written against.

    ``"architecture"``
        Not fixable here, ever. An x86 box cannot stand in for the arm64 job at
        any point. Failing on it would mean the local gate can *never* pass,
        which trains everyone to reach for ``--allow-uncovered`` as a matter of
        routine — and that flag also waives the interpreter case. Conflating
        them would make the gate weaker in exactly the place it is strong. So
        this one does not fail; it is named in the verdict line instead, every
        run, where it cannot be read separately from the result.
    """

    job: str
    version: str
    checked: bool
    passed: bool = False
    failing_step: str = ""
    seconds: float = 0.0
    detail: str = ""
    kind: str = ""

    @property
    def verdict(self) -> str:
        if not self.checked:
            return "CI ONLY" if self.kind == "architecture" else "NOT CHECKED"
        return "pass" if self.passed else "FAIL"


def run_job(job: Job, version: str, *, echo: bool = True) -> Outcome:
    """Run one job's steps under one interpreter, in a throwaway virtualenv."""
    # Architecture first, because this is the check that must not be answered
    # by running the steps anyway. A job pinned to arm64 executed on x86 would
    # pass, and would mean nothing — the whole reason that job exists is that
    # the architecture decides the answer.
    if job.architecture != host_architecture():
        return Outcome(job.name, version, checked=False, kind="architecture",
                       detail=f"needs {job.architecture} ({job.runs_on}), "
                              f"this machine is {host_architecture()}")

    interpreter = interpreter_for(version)
    if interpreter is None:
        return Outcome(job.name, version, checked=False, kind="interpreter",
                       detail=f"no python{version} on PATH")

    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix=f"matrix-{job.name}-{version}-") as tmp:
        venv = Path(tmp) / "venv"
        created = subprocess.run([interpreter, "-m", "venv", str(venv)],
                                 capture_output=True, text=True)
        if created.returncode != 0:
            return Outcome(job.name, version, checked=False,
                           kind="interpreter",
                           detail=f"could not create a venv: {created.stderr.strip()[:200]}")

        # The venv's bin first on PATH is what lets the workflow's own command
        # strings run unmodified: `pip`, `python` and `pytest` all resolve into
        # it. VIRTUAL_ENV and the cleared PYTHONHOME keep pip from reaching the
        # ambient install.
        environment = dict(os.environ)
        environment["VIRTUAL_ENV"] = str(venv)
        environment["PATH"] = f"{venv / 'bin'}{os.pathsep}{environment['PATH']}"
        environment.pop("PYTHONHOME", None)
        environment["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"

        for step in job.steps:
            if echo:
                print(f"    · {step.name}", flush=True)
            # `bash -e` is what the Actions runner gives a `run:` block on
            # Linux, so a multi-line step stops at its first failing line here
            # exactly as it would there.
            result = subprocess.run(
                ["bash", "-e", "-c", step.run],
                cwd=REPO_ROOT, env={**environment, **step.env},
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                tail = (result.stdout + result.stderr).strip().splitlines()
                return Outcome(
                    job.name, version, checked=True, passed=False,
                    failing_step=step.name,
                    seconds=time.monotonic() - started,
                    detail="\n".join(tail[-25:]),
                )
    return Outcome(job.name, version, checked=True, passed=True,
                   seconds=time.monotonic() - started)


def summarise(outcomes: list[Outcome], *, allow_uncovered: bool) -> str:
    """The report, with coverage and verdict in the same sentence."""
    width = max((len(f"{o.job} / {o.version}") for o in outcomes), default=20)
    lines = ["", "=" * (width + 26), "local matrix", "=" * (width + 26)]
    for o in outcomes:
        label = f"{o.job} / {o.version}".ljust(width)
        timing = f"{o.seconds:6.1f}s" if o.checked else "      –"
        lines.append(f"  {label}  {o.verdict:<11} {timing}"
                     + (f"  ({o.detail})" if not o.checked else "")
                     + (f"  at {o.failing_step!r}" if o.checked and not o.passed else ""))

    failed = [o for o in outcomes if o.checked and not o.passed]
    uncovered = [o for o in outcomes if not o.checked and o.kind != "architecture"]
    foreign = [o for o in outcomes if o.kind == "architecture"]
    lines.append("-" * (width + 26))

    # Named pair by pair rather than rolled into a version list, because the
    # two reasons call for different responses: a missing interpreter is
    # something you can install, and a foreign architecture is not.
    if uncovered:
        missing = ", ".join(sorted(f"{o.job}/{o.version}" for o in uncovered))
        if allow_uncovered:
            lines.append(f"  INCOMPLETE, and passed anyway by "
                         f"--allow-uncovered: nothing here ran {missing}.")
        else:
            lines.append(f"  INCOMPLETE: could not run {missing}. A matrix "
                         f"that skipped a job is not a matrix that passed it.")
        for o in uncovered:
            lines.append(f"    {o.job}/{o.version}: {o.detail}")

    # Always printed, never fatal, and never below the verdict — an x86 machine
    # cannot stand in for the arm64 job at any point, so failing on it would
    # only teach everyone to pass --allow-uncovered by reflex, which would
    # waive the interpreter case above along with it.
    if foreign:
        elsewhere = ", ".join(sorted(f"{o.job}/{o.version}" for o in foreign))
        lines.append(f"  Not runnable on this {host_architecture()} machine at "
                     f"all: {elsewhere}. **Only CI covers that**, and it is "
                     f"where a test asserting a property of the silicon shows "
                     f"up — RFC-072's correction is the worked example.")
    if failed:
        lines.append(f"  {len(failed)} job/version pair(s) FAILED.")
    if not failed and not uncovered:
        lines.append(f"  every job in ci.yml this {host_architecture()} machine "
                     f"can run, ran on every version it names, and passed."
                     if foreign else
                     "  every job in ci.yml ran on every version it names, and passed.")
    lines.append("  This is one machine. It cannot see a cross-machine float "
                 "difference; CI still can.")
    lines.append("=" * (width + 26))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--job", action="append", default=None,
                        help="only this job (repeatable); default is all of them")
    parser.add_argument("--allow-uncovered", action="store_true",
                        help="exit 0 even when a version had no interpreter — "
                             "the summary says so either way. Does not apply "
                             "to a job on another architecture, which never "
                             "fails the run and is always reported")
    parser.add_argument("--list", action="store_true",
                        help="print what would run, and stop")
    args = parser.parse_args(argv)

    jobs = read_jobs()
    if args.job:
        wanted = set(args.job)
        unknown = wanted - {j.name for j in jobs}
        if unknown:
            parser.error(f"no such job(s): {', '.join(sorted(unknown))}. "
                         f"ci.yml has: {', '.join(j.name for j in jobs)}")
        jobs = tuple(j for j in jobs if j.name in wanted)

    if args.list:
        print(f"this machine: {host_architecture()}")
        for job in jobs:
            for version in job.python_versions:
                if job.architecture != host_architecture():
                    why = f"NEEDS {job.architecture.upper()} ({job.runs_on})"
                else:
                    why = interpreter_for(version) or "NO INTERPRETER"
                print(f"{job.name} / {version}: {why}  ({len(job.steps)} steps)")
        return 0

    outcomes = []
    for job in jobs:
        for version in job.python_versions:
            print(f"  {job.name} / {version} …", flush=True)
            outcome = run_job(job, version)
            outcomes.append(outcome)
            if outcome.checked and not outcome.passed:
                print(outcome.detail, file=sys.stderr)

    print(summarise(outcomes, allow_uncovered=args.allow_uncovered))
    failed = any(o.checked and not o.passed for o in outcomes)
    # `kind != "architecture"` deliberately: a job on another architecture is
    # not a gap this machine could ever close, so it is reported rather than
    # failed. See Outcome's docstring for why conflating the two would make
    # the gate weaker.
    uncovered = any(not o.checked and o.kind != "architecture" for o in outcomes)
    return 1 if failed or (uncovered and not args.allow_uncovered) else 0


if __name__ == "__main__":
    raise SystemExit(main())
