# RFC-081: A pilot run a thousand times before it is run once

Status: **implemented** — `docs/pilot-playbook.md`, `scripts/pilot_dryrun.py`,
`tests/test_pilot.py`

## Summary

§9's G4 states its own purpose better than a summary would:

> the A-workstream builds the tools; this makes the *process* a rehearsed,
> reproducible artifact … so the pilot has been run a thousand times before it
> is run once.

The tools all existed. What did not was any assurance that stage 3's output is
stage 4's input, and a document asserting so is a document whose first
execution is a client's.

## The rule that shapes the rest, and what it costs

**Client files never leave the client's environment.** Model points are
policyholder data; this repository holds none and must not. The dry run is
built on hand-authored fixtures precisely so that stays true, and a test
asserts the script reads only from inside the repository — a future edit
pointing it at a real extract is the mistake that matters most and looks least
like one.

Two consequences the playbook plans around rather than discovers. The engine
runs where the data is, which is what `deploy/` is for. And when a
reconciliation disagrees, the **client sends the cell, not the file** — a
parity report names the model point, the period and the variable, which is
enough to ask a precise question without moving a book.

## The reconciliation has to be able to fail

`--prove-it-bites` perturbs one cell by one part in ten million and requires
stage 4 to fail. That is the first thing a sceptical actuary asks, and without
it every other assertion in the test file is consistent with a reconciliation
that always passes.

The negative case is asserted alongside coverage, deliberately. A
reconciliation that matched everything it looked at, having looked at very
little, is the failure a pilot most easily talks itself into — so
`coverage == 1.0` and `n_matched_rows` are checked next to `ok is True`.

## The stage list is asserted as a list

`test_the_whole_playbook_runs_end_to_end` asserts the six stage names in order
rather than checking that each key exists. That is stricter on purpose: the
playbook's six numbered sections and the script's six stages are the same six
steps, and a stage added to one without the other is the drift that turns a
rehearsed process back into a described one.

## What it does not rehearse, said out loud

Not that any particular client's file parses. Format coverage is a property of
their files; the honest limit of a synthetic fixture is that it proves the
*reader's behaviour* and not the format's variety. The playbook says to expect
the first real ingest to need a dialect adjustment and to budget for it, and a
test asserts that sentence is still there — a rehearsal claiming more than it
covers would have a pilot budgeting nothing for the stage that reliably needs
it.

## Exit criteria, and the ones that are not

Five criteria, of which the fifth — **state the coverage**: which products,
which variables, which periods, and which were *not* in scope — is the one that
gets skipped and the one that decides whether the result generalises. A
reconciliation over one product's four model points is evidence about one
product's four model points.

The playbook also names three non-criteria, because each is a real sentence
somebody says at the end of a pilot: *"the numbers matched"* (at what
tolerance, over what coverage), *"the actuary was happy"* (with the mapping, in
writing, or it did not happen), and *"it ran fast"* (speed is not the pilot's
question; correctness is). A test asserts the section listing them still
exists, because it is the section most likely to be trimmed as negative.

## The mapping is signed by the client, not by us

RFC-034's reader refuses to infer a field mapping, and the dry run's mapping
stage checks that the report still contains `ignored` rows. Those are the
valuable ones: they are the fields we are telling a client do not matter, and
"what happened to `CLIENT_REF`?" is the first question their modeller asks. A
migration report listing only what it consumed cannot answer it.
