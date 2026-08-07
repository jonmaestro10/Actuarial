# RFC-080: The note a diff cannot write, and the sets that never go away

Status: **implemented** — `CHANGELOG.md`, `docs/regulatory-calendar.md`,
`scripts/changelog_gate.py`, `.github/workflows/ci.yml`,
`tests/test_release.py`

## Summary

§9's G3 is the open answer to what incumbents sell as "quarterly vendor library
updates on a contractual cadence". The interesting part is that **the cadence
is not the answer**. A quarterly promise is a promise about timing; what a
client actually needs is that an update does not disturb the numbers they have
already filed, and that when something does move, somebody says what it did.

Two mechanisms, and both are refusals.

## A dated set is never removed, and that is what dating them buys

Each regulation update lands as a **new dated constant beside the old one** —
`DELEGATED_2026` arrives, `DELEGATED_2015` stays — with an RFC-050 diff report
between them registered as an artifact. A client reproducing last year's
valuation gets last year's numbers because the basis that produced them is
still in the package.

That has a versioning consequence worth stating outright: **a new dated set is
MINOR, not MAJOR.** If shipping `DELEGATED_2026` were a major release, every
client would face an upgrade decision over a regulation that does not apply to
them yet. It is minor precisely because the set they are on does not move.

And it has a deprecation consequence: dated sets are **exempt from the
deprecation policy entirely**. Everything else public gets two minor releases
of warning before removal. A dated set gets none, because it is never removed —
reproducibility of a prior period is not a feature that expires. They
accumulate, and that is the design rather than a leak.

`docs/regulatory-calendar.md` carries every set with a review date, and
`tests/test_release.py` asserts that every dated constant in `engine/` has a
row. Without that the calendar is a list of the sets somebody remembered —
which is the same document as a complete one right until it is not, and the
moment it stops being complete is the moment a client asks which basis their
prior period was on. The scan has its own guard: a test asserts it finds at
least three sets, because a regex that stopped matching would find none, report
none missing, and pass while asserting that a document lists all of nothing.

## The expected-change note, and why a diff cannot produce it

§3.5 asks that every change moving a numeric result carry a note saying what it
does to the numbers. The reason is not ceremony. A reviewer looking at
`rate = 0.0415` where `0.0410` used to be can see the edit and **cannot see
what it did to a reserve**. Only the author knows that, and only at the moment
of making it. The CI drift gate already forces the note to exist in the commit;
the changelog is where it becomes public.

`scripts/changelog_gate.py` asks one question: did a golden expected value move
without `CHANGELOG.md` moving too?

**What counts as a golden value** is defined narrowly and stated as a
heuristic. A numeric literal with a decimal point or an exponent, on a changed
line, in `tests/`. Not "a changed test" — the suite is edited constantly for
reasons that move nothing. Not a changed line in `engine/` — the engine
changing is the ordinary case, and the question is whether the *answers*
changed. Not a bare integer: `range(5)`, `proj_len=20` and an index are
integers, and a gate that fired on each of them would fire on every diff, and a
gate that fires on every diff is one nobody reads.

Exponent literals **do** count, deliberately. Loosening `1e-12` to `1e-9` moves
what the suite guarantees without touching a single expected value, and a
tolerance chosen to make a test pass is not a tolerance — that is exactly the
change that most wants a sentence explaining itself.

Both sides of the diff count. Deleting the assertion that pinned a reserve
changes what the suite guarantees as much as changing it does.

**It errs toward asking, on purpose.** Renaming a variable on a line that
happens to contain a tolerance will trip it. That is the right direction, and
the escape costs a sentence — including `No expected change`, which is a
legitimate and useful entry, because it records that the author considered the
question. It proved the point on its first real run: it flagged model-point
fixtures and rate-limit windows in this item's own branch, and the response was
to write the note rather than to loosen the rule.

What it does **not** catch is a golden value that moves without any test file
being touched, which happens when a fixture feeds a computed expectation.
Nothing textual can catch that; the suite failing is what catches it, and the
fix then touches a test line and this gate sees it.

## Could-not-run is not a pass

The gate exits **2**, distinct from both pass and fail, when it cannot reach
the base ref. That is the whole reason it is worth having in CI: a shallow
clone has no base to diff against, and the natural implementation would report
"no golden expected value changed" about a comparison it never made —
indistinguishable from a clean run, and this repository's oldest failure shape.
`actions/checkout` therefore takes `fetch-depth: 0`, and a test drives the gate
against a ref that does not exist and asserts the third status.
