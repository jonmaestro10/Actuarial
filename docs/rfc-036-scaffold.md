# RFC-036: The scaffold, and where a guess is allowed to live

Status: **implemented** — `engine/migrate/scaffold.py`,
`tests/test_scaffold.py`

## Summary

Execution plan §3, item A4:

> The scaffold is a *starting point that tells the truth about what it
> doesn't know*, not a converter that pretends.

Input: the variable list off an incumbent results extract (RFC-034), plus
optionally the client's model points. Output: an importable Python module
with one `@var` stub per incumbent variable, each carrying the nearest
library variable as a labelled suggestion, plus `MAPPING`, `ID_COLUMN`,
`TIME_COLUMN` and a `parity_spec()` function wired for RFC-033 — and, beside
it, a mapping table with every input variable in it.

With A1 and A2 this completes milestone M1. A pilot can now ingest a client's
model points, generate the skeleton, port the formulas, and reconcile — with
a registered parity report at the end of it.

## Guessing is allowed here, and forbidden thirty lines away

RFC-034 refuses to map a model-point field by name similarity. This module's
entire suggestion mechanism *is* name similarity. Both are right, and the
distinction is worth stating because it is the rule for every tool of this
kind we build later.

A guess is safe in proportion to how hard it is to act on without noticing.
The reader's guess would have been **inert to the eye and live to the
arithmetic**: `DURATION_IF_M` silently becoming `duration_in_force` changes
every projection and nobody reads a line of code to make it happen. The
scaffold's guess is the opposite. It lands in generated source where every
stub `raise`s, so the module computes nothing at all until a human has opened
it, read the suggestion, and replaced the body. The suggestion is labelled
with its confidence twice — in the stub's docstring and in the mapping
table — and the table sorts *weakest first*, because the reviewer's attention
belongs on the guesses rather than on the four obvious ones.

So: guess freely into things that must be read before they can matter; never
into things that take effect on their own.

## Aliases before similarity, and a name with no answer stays unanswered

Two failures of pure string matching show up immediately in the fixture.

`DEATHS` and `pols_death` share almost no characters, while `DEATHS` against
`pols_lapse` scores better than it deserves. So `DEFAULT_VARIABLE_ALIASES`
carries the conventional Prophet result names — `POLS_IF`, `DEATHS`,
`DTH_CLAIM`, `PREM_INCOME` — and is consulted first; similarity is the
fallback, not the mechanism.

The other failure is the one that makes a mapping table worthless: every
name gets *some* nearest neighbour, so a table with no empty rows looks like
a table where everything matched. `HOUSE_CODE_XQ` has no answer in a life
library, and the honest output is "none" plus a line in the report saying the
library ships nothing resembling it — either the product's own mechanics or a
house naming convention. Below 0.5 similarity the suggestion is dropped
entirely rather than reported at low confidence, because a suggestion nobody
should follow is noise in the one document that has to be read.

## Names that cannot be Python, and names that already are

Incumbent variable lists contain `SPECIAL RIDER (2)`, `1ST_YEAR`, and — the
one that would be a genuinely confusing failure — names that collide with
`Model`'s own API, like `trace`. All three are renamed on the way in
(`special_rider_2`, `v_1st_year`, `trace_`), collisions between two incumbent
names that normalise to the same identifier get a numeric suffix, and every
rename appears in the mapping table. A variable whose name changed is a
variable somebody has to be able to find again.

The two key columns — the model point identifier and the time step — are
excluded from the variable list rather than stubbed. A `@var` called `t`
would be a model that cannot run, and finding that out at class-creation time
is a bad first impression for a tool whose job is to be trusted.

## The acceptance test ports nothing and proves the wiring anyway

A4's acceptance asks that the emitted `ParitySpec` runs under A1. The
scaffold's stubs cannot run, so the test stands in for the porting work in
the most direct way available: it takes each stub to *be* the variable the
scaffold suggested for it, wraps a real `TermLife` run in an object that
answers to the stub names, and calls the generated `parity_spec()` exactly as
a converter would. The reconciliation against the RFC-034 fixture extract
comes out clean at 1e-12.

That is a statement about the suggestions as well as the plumbing: for this
fixture, the scaffold's guesses were all correct, and if a future change to
the alias table broke one of them the reconciliation would fail rather than
the mapping table quietly changing.

## What is next

Milestone M1 is complete. A3 (MoSes readers) is the same shape as A2 and is
scheduled on pilot demand. The next item on the critical path is B1, the
compiled executor.
