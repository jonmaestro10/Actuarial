# Working agreement

*What to know before changing anything here. Read this first; it is short on
purpose.*

This is an actuarial projection engine whose selling point is **evidence**:
that its numbers are reproducible, that its coverage is stated rather than
implied, and that it refuses rather than guesses. Most of the conventions
below exist because that was once not true somewhere, and the failure is
named where it is useful.

Deeper reading, in the order it helps:
[`docs/architecture.md`](docs/architecture.md) (how it fits together),
[`docs/developing.md`](docs/developing.md) (how to run it),
[`docs/user-guide.md`](docs/user-guide.md) (how to use it),
[`docs/competitive-execution-plan.md`](docs/competitive-execution-plan.md) §1
(the canonical version of this page, with the work items it governs).

---

## The five that matter most

**1. Bitwise, not close.** Every template must produce **bitwise-identical**
results across the executors of its class. Not "within tolerance" — the same
bits. If an operation cannot be made bitwise-reproducible, **replace the
operation, not the guarantee**.

There are three classes, and which one a template belongs to decides what is
asserted of it — per-policy, block (pooled or coupling), and scenario-bound.
[`docs/architecture.md`](docs/architecture.md) has the table.

The boundary is arithmetic, not preference:
[`engine/core/bitwise.py`](engine/core/bitwise.py) records which operations
IEEE-754 requires to be correctly rounded (a kernel may use those) and which
are implementation-defined to within an ulp (those get hoisted). Reductions
are never compiled — there is no length at which one is order-independent.

**2. Golden tests or it didn't happen.** New calculation code ships with
closed-form or hand-computed tests: exact `==` where the mathematics is
exact, `1e-12` against an independent naive implementation otherwise. A
tolerance chosen to make a test pass is not a tolerance.

**3. Assert the refusals as well as the grants.** Prefer an explicit refusal
to a silently wrong number. When a wrong reading is tempting, **compute it on
purpose** so the gap is reportable — `vm22.floor_outside_reserve`,
`takaful.surplus_if_qard_ignored`, and RFC-071's band index recomputed
against the wrong list are the worked examples.

**4. A class boundary wants a mechanism.** Where an exclusion can be enforced
in code, enforce it; where it cannot, it is a **test**, never a paragraph.
RFC-041 stated an exclusion in a docstring and it was wrong for as long as it
existed, while the evidence pack contradicted it the whole time.

**5. Dependency discipline.** `engine/core`, `engine/data`, `engine/library`
and `engine/report` keep **NumPy as their only runtime dependency**.
Everything else is an optional extra in `pyproject.toml`, imported behind a
guard, with tests that `skipif` it is absent. This is what let the engine run
unchanged on a Python that the API layer could not.

---

## Traps this repo has actually fallen into

Each of these shipped, or nearly did. They are here because they are not
obvious.

**A test that has quietly stopped asserting anything still passes.** A
parametrised test over an empty list collects nothing. A loop over
`set(A) - set(B)` asserts nothing once the sets converge — RFC-071 carried
the last table in a set and disarmed its own refusal test. If a collection
can empty out, assert the mechanism separately.

**A skipped measurement reads exactly like a passing one.** The bitwise
boundary's 39 measurement cases skip without the `[compile]` extra, so its CI
job sets `REQUIRE_COMPILE_EXTRA=1` to turn the skip into a failure.

**A generated claim can go stale while the digest keeps changing.**
`VM22_PRESCRIBED_2026.text` said two tables were carried when seven were, and
the test asserting it was *enforcing* the error. **Derive, don't restate** —
including the verb, when the count can reach one.

**Equal numbers can have an unequal contract.** Three bugs — a spurious
`(1,)` axis, a missing one, an `int64` where the array executors store
`float64` — all produced identical values and different digests. **Assert
shape, dtype and value separately.**

**A `@var` body must not branch on model-point data.** A conditional that one
batch never enters is a defect that survives every test written against that
batch. It survived two RFCs written about it.

**A safeguard can agree everywhere and look like a safeguard.** RFC-075's
worker attestation first probed nine values and could not see the difference
it existed to catch, because NumPy only dispatches SIMD kernels above a length
threshold.

---

## Conventions

**RFC first.** One per item, written before the code. House style: a titled
essay (`# RFC-0NN: The X, and the Y`), a `Status:` line naming module and
test paths, a `## Summary` quoting the line it discharges, then the two or
three genuinely interesting design decisions. **Not a routing inventory.**
Take the next free number.

**Commit messages** are declarative sentences in the repo's voice — "The
approval that cannot drift", "The number the actuary wrote, and the number
the machine kept". One item per commit series. **Never put a model identifier
in anything pushed.**

**Tests carry prose docstrings** saying which failure mode they guard.

**`tests/` is not a package.** Never import across test modules — it resolves
locally because the editable install leaves the repo root on `sys.path`, and
fails only in CI. Shared fixture data lives in `tests/conftest.py`. A builder
function named `test_*` gets collected by pytest; name it something else.

**Published figures** go in `docs/sources/` with full provenance and get
asserted in `tests/test_published_sources.py`. Golden tests check an
implementation; they cannot catch a misreading of the method, because the
misreading reproduces perfectly across every implementation of it. Reading
VM-22's actual text found three errors that 35 passing tests all agreed with.

**Registry-first.** Any artefact a run produces is content-addressed and
recorded through `engine/core/registry.py`, never as loose files with mutable
names.

**Scoreboards move in the same commit** as the work: the execution plan's
§1.3 and `docs/competitive-landscape.md` §3.

**Don't break the docstring floor** (`modeldoc` coverage, floor recorded in
the execution plan §1.5). In `engine/report/evidence.py`, a section with
nothing to report stays `available=True`.

---

## Before you push

Three commands, every time:

```bash
python -m pytest -q
python scripts/evidence_pack.py --out /tmp/ev1
python scripts/evidence_pack.py --out /tmp/ev2 && diff -r /tmp/ev1 /tmp/ev2
find . -name __pycache__ -exec rm -rf {} + ; python -W error::SyntaxWarning -m pytest -q
```

The pack must rebuild **byte-identically**. The cache clear is not
superstition: a `SyntaxWarning` masked by a stale `.pyc` reached CI once.

Before you **merge**, one more. **CI runs only on a push to `main`** — a pull
request carries no checks — so this is the only check a change gets before it
lands:

```bash
python scripts/local_matrix.py
```

It reads `.github/workflows/ci.yml` and runs every job under every interpreter
that file names. **A version it cannot find fails the run**, because a machine
with one Python must not be able to print a report shaped like a full matrix.
It is not CI and says so: one machine cannot see a cross-machine float
difference, and the worked example of that gap is RFC-072's correction — the
bitwise boundary asserted a property of the silicon for the life of the item.
See [`docs/rfc-077-local-matrix.md`](docs/rfc-077-local-matrix.md).

**CI runs several Python versions and this container is one.** Three defects
have been invisible locally and caught only by CI: a cross-test import, a
`SyntaxWarning` behind a cached `.pyc`, and a cross-machine float difference.
Note that GitHub reports a *superseded* run as `failure` with zero failed
jobs, and a job that never got a runner as `cancelled` — check the jobs, and
check whether they ever started, before believing a red.

---

## What reproducibility means here, exactly

A run digest is an identity **on a machine**, not across machines.
`np.exp` and `**` are not bit-portable between microarchitectures — same
NumPy, same Python, different last bit. Consequences that are easy to undo by
accident:

- worked examples carry **literal** scenario values, never a seeded
  generator, and a test asserts this structurally because CI cannot catch the
  regression (it builds the pack twice on one runner);
- `engine.report.evidence.REPRODUCIBILITY_SCOPE` states the scope. **Do not
  restore any claim that the pack rebuilds identically anywhere.**

Nothing else is weakened: the executor invariant compares executors on one
machine, and dispatch refuses to reduce across workers whose arithmetic does
not attest alike.

---

## The thing that matters most

Every finding of the last several sessions came from the same move: **reading
the primary text, or the artefact's own output, and asserting what it actually
says rather than what a sensible design would contain.**

VM-22 §5.A.2.a.iv is an *additive* charge, not a netting. Table 6.7 is not
monotone, and neither is 6.11. §6.C.8.iii projects the 1983 IAM Table 'a'
from 2011, not the 2012 IAM Basic from 2012 — which no arithmetic downstream
can see. The evidence pack had been reporting five templates as failing the
central invariant and nobody had read the section.

When a document and an artefact disagree, the artefact is usually right and
the disagreement is usually the finding.
