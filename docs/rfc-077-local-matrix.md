# RFC-077: The matrix that ran, and the versions nobody checked

Status: **implemented** — `scripts/local_matrix.py`,
`tests/test_local_matrix.py`, `.github/workflows/ci.yml`, `pyproject.toml`

## Summary

The execution plan's definition of done opens with "full suite green **on CI,
every Python version**". For six merged items that clause has been false, and
the repository had no way to say so.

GitHub Actions stopped scheduling runs partway through RFC-071. The account is
on a free plan; the included minutes ran out; the spending limit is the default
$0. That combination does not fail a run — it declines to create one. So
RFC-071 through RFC-076 were merged on the evidence of a single interpreter,
and the only record of that was a paragraph in a handoff note.

This RFC does two things. It makes the workflow cost about half of what it
cost, so the same allowance goes twice as far. And it builds the substitute
that can be run without an allowance at all — whose entire design problem is
not running the suite several times, but **reporting what it did not run**.

## The three signatures, because they read as different faults

Worth recording, because each one invites a wrong diagnosis:

1. Run `31126492175`: both matrix jobs ended `cancelled`, with `runner_id: 0`
   and an empty `runner_name`, killed at *exactly* the fifteen-minute mark. A
   job that ran and was cancelled has a runner id. These never got one.
2. Run `31124688869` on `main`: `test (3.12)` **succeeded in 109 seconds**
   while `test (3.11)` sat unassigned and died at the same mark. So Actions was
   not disabled and the workflow was not malformed — one job proved both.
3. Four later commits produced **no run object at all**. Not queued, not
   failed. Absent.

Read singly, (1) looks like runner capacity, (2) like a flaky matrix, and (3)
like a trigger bug. Read together they are one thing: an entitlement consumed
mid-window. The lesson worth keeping is in the plan already — a superseded run
reports `failure` with zero failed jobs, and a job that never got a runner
reports `cancelled`. **Check whether a job ever started before believing it
ran.** Both of the account-level facts that explain all three live on a billing
page that no run, and no agent, can see.

## Half the minutes were buying nothing

The workflow triggered on `push: branches: ["**"]` *and* on `pull_request`.
Every push to a branch with an open PR therefore fired both events, and ran all
four jobs twice against one commit. Restricting `push` to `main` removes the
duplicate without narrowing coverage by a single commit, because every branch
here reaches `main` through a PR.

The second leak was that a superseded run costs what a live one costs. A
`concurrency` group keyed by ref, with `cancel-in-progress`, stops a
force-pushed branch from leaving three matrices running against commits nobody
will merge. `main` is exempt by construction: cancelling an in-progress run
there would discard the only evidence that a merged commit is green.

What was **not** added is a `paths-ignore` for documentation, which is the
obvious third saving and is wrong here. `tests/test_working_agreement.py`,
`test_documentation.py` and `test_architecture.py` assert against `CLAUDE.md`
and `docs/`, and the evidence pack embeds documented figures. A docs-only diff
is not a no-op in this repository; skipping CI on one would skip precisely the
tests that diff can break.

### Then the other half: `pull_request` removed as well

Measured after the first change, one run bills **seven minutes** — GitHub
rounds each *job* up to the whole minute, so 24s of `bitwise-boundary` and
three test jobs of 81–101s cost 1 + 2 + 2 + 2. Of the thirty most recent runs
before the fix, **fourteen were `push` on a non-main branch**: 47% of all
billing, buying a second copy of a result that already existed.

The trigger is now `push: branches: ["main"]` and nothing else. A branch can be
pushed and re-pushed as often as the work needs and nothing runs until it
lands. The `concurrency` block went with it — it existed to cancel superseded
*branch* runs, and on `main` it was already exempt, because cancelling there
would discard the only evidence that a merged commit is green.

**What this trades.** A pull request carries no checks, so `main` is where a
failure is found: verify locally, merge, confirm, fix forward. Coverage of what
*lands* is unchanged — every commit reaching `main` still meets the whole
matrix — but the failure arrives after the merge rather than before it, and
`main` can be red in between.

That trade is only honest if the local check is real, which is what the rest of
this RFC is about, and it raises `scripts/local_matrix.py` from a second
opinion to **the only opinion a change gets before it lands**. Two consequences
follow directly. Its refusal to pass a version it could not check stops being a
nicety and becomes the thing standing between an unverified interpreter and
`main`. And its one genuine blind spot — one machine, one architecture, one
libm — is now unguarded until after a merge; RFC-072's correction is the worked
example, an assertion about the silicon that survived the entire life of an
item because it was only ever measured in one place.

## The design problem is the silence, not the matrix

Running the suite under three interpreters is a shell loop. The thing that
makes `scripts/local_matrix.py` worth an RFC is that **a machine with only one
of them installed must not be able to produce a green report.**

That is the repository's oldest failure shape wearing new clothes. A
parametrised test over an empty list collects nothing and passes. A loop over
`set(A) - set(B)` asserts nothing once the sets converge. The thirty-nine
measurement cases in `tests/test_bitwise_boundary.py` skip when the compiler is
absent, and a skipped measurement reads exactly like a passing one — which is
why that CI job sets `REQUIRE_COMPILE_EXTRA=1`. A version with no interpreter
is the same defect at the level of the whole matrix, and it is *more* dangerous
because the common case is a developer machine with one Python.

So absence is a failure, not a footnote. It sets the exit status, it is named
in the summary, and `--allow-uncovered` — which is legitimate, since an
interpreter genuinely may be missing — prints the gap **in the same sentence as
the verdict**. A caveat that can be read separately from the result will be.
Two tests pin this to the exit code rather than to the prose, because a caller
in a shell script reads the status and never the text; and they are separate
tests, because a `main` that returned 1 only for absent interpreters would
satisfy either one alone.

## Derived, not restated — including the step environment

The versions, the steps and the per-step environment are read out of
`.github/workflows/ci.yml`. None is written down in the script.

A local matrix carrying its own copy of the version list agrees with CI exactly
until someone adds a version to CI, and then reports a full green while
checking one fewer than it claims. The test for this does not inspect the
source for literals — it appends a version to a copy of the real workflow and
asserts the reader returns it.

The step **environment** matters more than it looks. If the reader dropped
`env`, the local `bitwise-boundary` job would run without
`REQUIRE_COMPILE_EXTRA`, skip its thirty-nine measurement cases, and report
green — silently reintroducing, one layer out, the exact failure that variable
was introduced to prevent. There is a test named for that.

Each step is executed through `bash -e`, which is what the Actions runner gives
a `run:` block on Linux, inside a virtualenv whose `bin` is first on `PATH`. So
`pip install -e ".[test,data,api,excel]"` and `python scripts/evidence_pack.py`
need no rewriting. The commands are not adapted, which is the point: an adapted
command is a different command, and a substitute that runs a different command
is not evidence about the original.

Reading the workflow needs PyYAML, which is now in the `test` extra and
imported behind a guard that refuses with an actionable message. §1.4's rule
for anything outside `engine/core`, `data`, `library` and `report` prescribes
exactly this shape. The guard does not fall back to a built-in copy of the
matrix, because that fallback is the defect the whole section is about.

## What it found

Run here, against the four job/version pairs the workflow names:

- **Python 3.12 and 3.13 are green** — 2,516 passed, 48 skipped, and the
  evidence pack rebuilding byte-identically under each. Nothing merged since
  RFC-071 had ever run on either.
- **The `bitwise-boundary` job is green** — 47 passed, **zero skipped**, with
  `REQUIRE_COMPILE_EXTRA=1` actually set, against numba 0.66.0, llvmlite 0.48.0
  and numpy 2.4.6. That job had never executed anywhere, so its measurement
  cases had only ever skipped.
- **The pack digest is identical across all three interpreters** —
  `6d31f40a51cef120f0edddef7b05d1b7` — even though 3.11 resolves numpy 2.4.6
  and the others resolve 2.5.1. The only byte differences are the recorded
  interpreter version in `environment.json` and `index.md`, which is provenance
  being captured correctly rather than drift. This corroborates, by a
  completely different route, the earlier finding that the bit patterns of
  `exp`, `log`, `log1p`, `expm1`, `power`, `sqrt` and `sin` are byte-identical
  between those two NumPy releases.

The heterogeneous-NumPy matrix is therefore not merely believed to be safe. It
has been run.

## What this is not

It is not CI, and the summary says so on every run. One machine, one
architecture, one libm. `np.exp` and `**` are not bit-portable across
microarchitectures and a pack digest is an identity *on a machine* — so no
number of local interpreters substitutes for a second machine, and the
cross-machine float difference that CI caught once remains invisible here. A
test asserts that the summary keeps saying it, because a green report is
exactly where an over-broad reproducibility claim would get reintroduced.

The execution plan records local verification as its own status rather than
folding it into done. Six items are marked that way and stay marked until a
runner has actually run them.
