# RFC-079: The binder that regenerates, and the controls it declines to claim

Status: **implemented** — `docs/compliance/soc2-controls.md`,
`engine/report/compliance.py`, `engine/api/hardening.py`,
`engine/report/evidence.py`, `engine/api/app.py`,
`.github/workflows/ci.yml`, `tests/test_compliance.py`

## Summary

§9's G2 makes an observation before it asks for anything:

> Certification is organizational, but the technical substrate an auditor asks
> for is code, and most of it already exists — this item joins it up.

That is right, and it decides the shape. Nothing here implements a control.
What is added is the **joining up**: a control map an auditor can argue with, a
generated section of the evidence pack, and one mechanism that stops the map
becoming the thing compliance documentation usually becomes.

## The document is the source, and that was the harder call

The obvious design is a dict of controls in Python that *generates* the
Markdown, making drift impossible. It was rejected.

The control map is the artifact an auditor reads, annotates and disputes, and a
generated file is one nobody edits. More to the point, the mapping from a Trust
Services criterion to a mechanism is a **judgement** — "CC7.2 anomalies are
monitored" is satisfied by a digest-chained audit log only if you accept that
argument — and judgements belong in prose that a person signed, not in a data
structure that reads as though the machine decided.

What replaces generation is narrower and sufficient. Every row names a pytest
node id; `unresolved_evidence` reports any the suite no longer collects; a
control whose test was renamed **fails the build**. Drift stays possible in the
direction that matters least — prose going stale about a mechanism that still
exists — and is impossible in the direction that matters most, a control citing
evidence that has quietly gone. That is the ordinary fate of compliance
documentation and the reason auditors discount it.

## The gaps are part of the evidence

Three of nineteen rows say *not claimed*: business continuity (CC9.1), restore
drills (A1.3), and data disposal (C1.2). They are counted and reported rather
than filtered out, and a test asserts that **at least one** row is unsatisfied.

A control map with no gaps is a map nobody audited. Its totals would be a
function of what somebody chose to write down, and the first question a good
auditor asks is what is missing — a document that cannot answer it has answered
it.

## Not checked is not the same as all missing

Found by its own test, and worth recording because the wrong version looked
right. `compliance()` originally computed unresolved evidence unconditionally.
Built without a test inventory — which is how the pack's own test builds it —
every reference is trivially absent, so the section's summary announced *"19
controls, and 16 cite evidence the suite no longer collects"* about a suite it
had never asked. Alarming, and false.

The fix is that an unchecked build reports `{}` and `evidence_checked: False`,
and the summary says which it is. Same family as a skipped measurement reading
like a passing one, arrived at from the opposite side: an unperformed check
reading like a failed one.

`available` stays `True` throughout, per §1.5 — that flag means "this pack
could not look", not "this pack looked and found gaps", and collapsing the two
would let a build that failed to read the map resemble a system with nothing to
report.

## Hardening: both controls are narrower than their names

A control believed to do more than it does is worse than no control, because it
stops anyone looking.

**The rate limit is a fair-use bound, not a defence.** An in-process token
bucket keyed by principal, so it stops a looping client or a tenant's batch job
taking every projection thread. It does **not** survive a second replica — N
replicas admit N times the rate — and it is not a DoS control, because an
attacker without a token is rejected by authentication before reaching it. Both
sentences live in `RATE_LIMIT_SCOPE`, the compliance section quotes it, and a
test asserts the string still says them. A sliding window rather than a
counter-with-reset, because a fixed window admits twice the rate across its
boundary.

Two details that are not decoration: a 429 carries the security headers, because
it is generated on an early-return path that is exactly where headers get lost;
and `/health` is never limited, because it is the one route with no role
requirement and a 429 there reads to an orchestrator as unhealthy — a noisy
client would restart a pod that is fine.

**The headers are the ones that mean something for a JSON API.** `nosniff`
first: without it a browser may sniff a JSON response as HTML and execute it,
which is the path from "returns user data" to "runs script in the API's
origin". `no-referrer` because run ids are request fingerprints and a referrer
leaks them onward. `no-store` because results are a tenant's numbers and a
shared cache holding them is a cross-tenant read no route check can see. The
CSP is `default-src 'none'` on API routes and a narrower `'self'` policy on the
UI, which needs its two assets — handing the UI the API's policy would render a
blank page while every header looked correct.

**HSTS is deliberately absent**, and there is a test for its absence. It is a
promise about a *scheme*, and this process does not know whether it sits behind
TLS. Emitted from plain HTTP it does nothing; where TLS terminates it belongs
on the proxy holding the certificate. A header that is present and meaningless
is worse than one that is absent, because it is read as coverage.

## pip-audit is advisory, and the reason is stated

`engine/core`, `data`, `library` and `report` have NumPy as their only runtime
dependency (§1.4), so an advisory almost always lands in an optional extra or a
test tool. A blocking gate on a feed that changes daily becomes a gate people
disable, and a disabled gate reports nothing at all — whereas an advisory one
leaves the finding in the build record, which is what an auditor asks for. It
uses `continue-on-error` rather than `|| true`, so the step's own conclusion
stays visible in the run summary instead of being painted green.

That produced a second finding. `scripts/local_matrix.py` reads the workflow,
so it picked the new job up automatically — and would have **blocked** on it,
because it did not read `continue-on-error`. A local gate stricter than the CI
it claims to mirror is not a stricter gate; it is one that disagrees with the
thing it mirrors, and the fix everyone reaches for is to stop running it. The
reader now carries the flag, an advisory failure is reported as `advisory` and
does not set the exit status, and a test asserts `ci.yml` still contains one
job of each kind so the distinction is not being checked against a set where it
does not appear.
