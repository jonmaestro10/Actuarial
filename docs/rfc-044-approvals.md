# RFC-044: The approval that cannot drift

Status: **implemented** — `engine/core/approvals.py`, `engine/api/app.py`,
`tests/test_approvals.py`

## Summary

Execution plan §6, item D2:

> An approval is a content-addressed record `(assumption digest, approver,
> timestamp, note)` in the registry. A run submitted in **approved mode**
> refuses any assumption set whose digest lacks an approval; approver must
> differ from submitter.

`create_app(principals=..., approvals=..., require_approval=True)` turns it
on. `POST /assumptions/digest` tells anyone what a basis digests to,
`POST /approvals/{digest}` signs for it, `DELETE /approvals/{digest}` takes
that back, and `POST /runs` refuses anything nobody else has signed.

## Every incumbent approves a label

The workflow in Prophet's or AXIS's governance layer approves a *named*
assumption set: somebody signs off "2026 Q1 mortality basis", and from that
moment the name is approved. What the name points at is a row in a database,
a file on a share, a table in a workspace — and it can move. A corrected
table, a re-extraction, a fat finger in the spreadsheet upstream. The
approval does not notice, because the approval was never about the numbers.

An approval here names `fingerprint(assumptions)` — the same value
`RunRecord.assumptions_digest` records, asserted in the tests, so what was
approved and what ran are the same string or the check fails. Two properties
fall out, and both are the reason to do it this way:

- **Re-derivation is free.** The identical basis rebuilt from scratch, in
  another process, on another machine, digests the same and is still
  approved. Content addressing turns "is this the thing you approved?" into
  a string comparison.
- **There is no "basically the same".** A lapse rate moved by one basis
  point is a different digest and is not approved, and there is no argument,
  threshold or override that reaches a different answer. The check cannot be
  talked into anything, because it does not know what an assumption *is*.

The refusal carries the digest, because the first thing the refused
submitter needs is the string to hand an approver.

## Four eyes is a property of the decision, not of the transport

`engine/core/approvals.py` knows nothing about HTTP. It holds the log, the
query "who currently approves this digest", and `check_approved`, which is
the whole rule: an approval by somebody who is not the submitter. The API
supplies the identities.

That split is what makes the awkward case obvious. A principal holding both
`runner` and `approver` — a small team, one person wearing two hats — can
approve a basis and can submit a run, and still cannot do both to the same
basis. The roles are separable but the *decision* is not: `check_approved`
takes the submitter's name and discounts their own signature. The test that
matters says exactly this, and it is the one an auditor would write.

The corollary is enforced at construction: `require_approval=True` without
`principals` raises. Four-eyes over anonymous callers is one pair of eyes
with extra steps, and a deployment that asked for it has misconfigured
something it believes is protecting it.

Two smaller refusals in the same spirit. The approver is the authenticated
principal and cannot be supplied in the request body — an approval whose
signatory is a request field is an approval anyone can forge. And you may
revoke your own signature and nobody else's: a signature is not
transferable, so "revoke somebody else's approval" is not an operation this
module can express.

## A revocation is an entry, not a deletion

The obvious model is a set of approvals with `revoked` flipping a row.
`ApprovalRegistry` is append-only instead, and the current state is a query
over the log — last action per approver wins.

The reason is that the interesting half of the history is the half a
mutable row throws away. "Who approved this and later took it back, and what
did they say when they did" is precisely the question a model-risk function
asks after a bad number reaches a report. A revocation that erased its
approval would leave the log saying nothing happened.

## What is next

RFC-045 (D3) chains this into an append-only audit log covering every API
mutation — submit, approve, revoke, principal change — with the same
digest discipline the run registry uses, so the sequence of governance
events becomes tamper-evident rather than merely recorded.
