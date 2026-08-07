# The pilot playbook

*The client pilot as a rehearsed procedure, not a document read on the morning
of the kick-off.*

`scripts/pilot_dryrun.py` executes this end to end against synthetic fixtures,
and `tests/test_pilot.py` asserts it in CI. **The pilot has been run a thousand
times before it is run once.**

---

## The rule that shapes everything else

> **Client files never leave the client's environment.**

Model points are policyholder data. This repository holds none, and the dry run
is built on hand-authored fixtures precisely so it can stay that way. What we
keep are *dialect* fixtures — invented files in the incumbent's documented
layout — which is what lets the reader be tested without anyone's book.

Two consequences a pilot has to plan around:

- The engine runs where the data is. That is what `deploy/`'s compose profile
  and Helm chart exist for.
- When a reconciliation disagrees and we need to see a number, the **client**
  sends the cell, not the file. A parity report names the model point, the
  period and the variable, which is enough to ask a precise question.

---

## Roles

| Role | Side | Owns |
|---|---|---|
| Pilot lead | ours | Scope, the exit criteria, the go/no-go |
| Migration engineer | ours | The dialect, the field mapping, the scaffold |
| Reviewing actuary | client | The product definition, the basis, sign-off on the mapping |
| Modeller | client | The incumbent extract and what its columns mean |
| Platform | client | Where it runs, who has a token, which tenant |

The **reviewing actuary signs the mapping**, not us. Every guess we make about
what a column means is a guess about their product, and RFC-034's reader
refuses to infer one — the mapping is written out, and the mapping report lists
every incumbent field including the ones we dropped, so "what happened to
`CLIENT_REF`?" has an answer.

---

## The six stages

Each is a stage of `scripts/pilot_dryrun.py`, in order.

### 1. Ingest

Read the model point file with the documented dialect. A field the dialect does
not describe is an error, not a warning — a silently dropped column is a
policy attribute that stops affecting the answer.

### 2. Map

Produce the mapping report and **have the client's actuary sign it**. Its value
is the `ignored` rows: those are the fields we are telling them do not matter.

### 3. Run

Through the registry, so the run is content-addressed. The same request
returns the same run and does no second computation, which is what makes a
re-run after a question verifiable rather than hopeful.

### 4. Reconcile

RFC-033's parity core against their results extract, at a stated tolerance.
Start at `1e-12` and let the disagreements argue for a looser one — a
tolerance chosen to make the reconciliation pass is not a tolerance.

Expect this to fail first time. It usually finds one of:

- a **decrement order** difference (mortality then lapse, or simultaneous);
- a **timing convention** — claims at end of period against mid-period;
- an **expense basis** applied per policy against per premium;
- a genuinely different product definition, which is the finding worth having.

### 5. Register

The parity report goes into the artifact registry, content-addressed. Not a
PDF in an email: the comparison has a digest, and the digest is what gets
quoted in the steering meeting.

### 6. Hand over

The parity report **and** the validation evidence pack. The pack is the part
incumbents cannot match — it rebuilds byte-identically from the same source, so
the client's own engineers can regenerate it rather than take it on trust.

---

## Exit criteria

A pilot has succeeded when **all** of these hold:

1. Every model point in the extract reconciles within the agreed tolerance, or
   the difference is explained and the explanation is written down.
2. The mapping report is signed by the client's reviewing actuary.
3. The parity report and the evidence pack are registered artifacts, and the
   client has rebuilt the pack themselves at least once.
4. The client has run a projection through their own deployment, with their own
   token, in their own tenant.
5. Coverage is stated. Which products, which variables, which periods — and
   which were **not** in scope.

Point 5 is the one that gets skipped, and it is the one that decides whether
the pilot's result generalises. A reconciliation over one product's four model
points is evidence about one product's four model points.

## Exit criteria that are not

- *"The numbers matched."* At what tolerance, over what coverage.
- *"The actuary was happy."* With the mapping, in writing, or it did not
  happen.
- *"It ran fast."* Speed is not the pilot's question. Correctness is; the
  benchmark can come after.

---

## What the dry run rehearses, and what it does not

It rehearses the **process**: that each stage's output is the next stage's
input, that the reconciliation genuinely bites, and that the hand-over is
content-addressed. `--prove-it-bites` perturbs one cell by one part in ten
million and requires stage 4 to fail — a parity report is worth exactly what
its ability to go red is worth, and that is the first thing a sceptical
actuary asks.

It does **not** rehearse that any particular client's file parses. Format
coverage is a property of their files, and the honest limit of a synthetic
fixture is that it proves the reader's behaviour and not the format's variety.
Expect the first real ingest to need a dialect adjustment, and budget for it.
