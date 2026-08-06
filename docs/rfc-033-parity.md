# RFC-033: The parity report, and what a reconciliation owes a sceptic

Status: **implemented** — `engine/parity/`, `engine/core/registry.py`
(`ArtifactRecord`), `tests/test_parity.py`

## Summary

The execution plan's first item (§3, A1), discharging the landscape report's
§5.5:

> Generalize the VPLA harness into a reusable diff engine.

Nobody replatforms an actuarial model on a benchmark. They replatform on a
reconciliation: the incumbent's numbers on one side, the new engine's on the
other, and a document that says where they differ and by how much. Before
this RFC the repo had exactly one such document — `scripts/vpla_parity.py`,
which knows how to load a VPLA checkout and owns its own diff loop. This
splits the diff out (`engine/parity/diff.py`), gives it a deliverable
(`engine/parity/report.py`), and puts the deliverable in the registry.

Three things are interesting: what the report refuses to do quietly, why the
tolerance is a policy rather than a number, and what makes the report itself
checkable.

## The failure mode is an all-green report over almost nothing

A diff engine's obvious job is finding differences. Its real job is not
producing false agreement, and there are four ways to produce it without
writing a wrong number anywhere:

- **Match columns by name similarity.** `RESERVE` against `reserve` is
  probably right; `DTH_BEN` against `death_benefit` might be the gross
  figure against the net one. So `ParitySpec.mapping` goes external column →
  engine variable, written by a human, and construction *raises* if it names
  a variable the run does not carry. Nothing is inferred.
- **Drop the columns nobody mapped.** An extract carries thirty columns and
  the mapping covers eight; the missing twenty-two are the ones nobody has
  looked at. They are listed in the report under "unmapped external
  columns" — the header says who reconciled them: nobody.
- **Drop the rows that do not align.** An external row whose `(model point,
  t)` has no engine cell — a policy that did not make it into the run, a
  time step past the horizon — is not an omission, it is an unreconciled
  row. Each one is kept, with the reason it did not match, and any of them
  makes `report.ok` false.
- **Reconcile a tenth of the horizon and call it parity.** Coverage in the
  other direction (engine cells the extract says nothing about) is a
  legitimate state — an incumbent extract often carries five years of a
  sixty-year projection — so it is reported as a number,
  `n_covered_cells / n_engine_cells`, printed in the report header as a
  percentage. Legitimate, but never invisible.

Alignment is by key and by nothing else: the same extract with its rows
shuffled produces the same finding (`tests/test_parity.py`). Its *content
digest* changes, and should — a file with its rows in another order is
another file — but the verdict does not move. The one place row order was
still able to leak into the output was the "worst cell" of a variable that
agrees everywhere, where every candidate deviation is zero and the first row
wins. A variable that agrees everywhere now reports no worst cell at all,
which is also the truer statement.

## The tolerance is a policy, and its default is not a materiality threshold

`TolerancePolicy` is a default plus per-variable overrides, and the default
is `|Δ| ≤ 0 + 1e-10·max(|engine|, |external|)`.

That is far tighter than any materiality threshold an actuary would use on a
result, deliberately. Materiality is a judgement about whether a *number*
matters. A parity report is a judgement about whether an *implementation* is
the same one. Two float64 implementations of the same deterministic formula
agree to about 1e-10 relative; a deviation above that is a difference in the
model, not in the arithmetic, and the reconciliation exists to find exactly
those. Loosening it is a per-variable decision somebody writes down — and
the tolerance is inside the spec digest, so a report reconciled at a looser
bound is a different reconciliation, and the registry will not conflate the
two.

Stochastic outputs cannot be held to that, because two Monte Carlo
implementations agree only within sampling error. `StatisticalTolerance`
takes the standard error from the caller rather than deriving one: only the
caller knows how many scenarios produced the number, and a tolerance nobody
can reconstruct is a tolerance nobody can dispute.

Non-finite values get the conservative treatment throughout. A NaN compares
false against every bound, so it counts as a difference; it is excluded from
the max-deviation statistics (which it would otherwise render meaningless)
and counted separately as `n_nonfinite`; and it sorts to the *front* of the
per-variable drill-down, because a NaN is a difference no magnitude can rank
and burying it under ten large deviations is precisely the report this
module refuses to write.

## A reconciliation that cannot itself be reconciled is a screenshot

The report is Markdown — the format a pilot deliverable actually travels in,
diffable in a pull request, readable without a viewer. But a Markdown file
someone emails you is evidence of nothing in particular. So the artifact is
content-addressed, and the registry grows a second record type to hold it.

`ArtifactRecord` (in `engine/core/registry.py`, alongside `RunRecord`) makes
the same pair of assertions RFC-003 makes about runs: `artifact_id` digests
what the artifact was derived *from*, `content_digest` digests what came
out, and `ArtifactRegistry.add` refuses a second record with the same
derivation and a different content. For a parity report the derivation is
both digests — the engine's `results_digest` and the external extract's
content digest — plus the mapping and the tolerance policy. So a recorded
reconciliation names the exact two things it compared, rather than a file
path that can be repointed at a friendlier extract, and re-running it must
produce the same report or the registry says so out loud.

The external digest is of the *columns*, not the file bytes and not the
path: the same extract read from two directories, or through CSV in one
place and Parquet in another, is the same evidence and digests the same.
`source` rides along for the report header and stays outside the digest.

That is the beyond-parity move in §2 of the execution plan, and it is
small: a vendor's reconciliation is a spreadsheet with a date on it. This
one is a document whose provenance can be recomputed by somebody who does
not trust you.

## What it cost, and what is next

`ExternalTable` reads plain CSV with no third-party dependency (type
inference: integers stay integers so an integer identifier column still
compares equal; a blank cell becomes `None`, never zero) and Parquet behind
the existing `[data]` extra. `engine/parity` therefore adds no runtime
dependency, per the execution plan's §1.4.

`scripts/vpla_parity.py` now delegates its rate comparison to the core at
zero tolerance — the harness has never accepted a difference, and the core
is where that claim gets written down instead of implied by a `!=`. Its
printed output is unchanged. Verified manually against a checkout rather
than in CI, which has no VPLA to point at; the three numbers it prints per
configuration are now `n_compared`, `n_outside` and `max_absolute` off a
`VariableParity`.

RFC-034 (A2) supplies the other side of a real reconciliation: Prophet
readers, whose `read_results` returns exactly the `ExternalTable` this
module consumes.
