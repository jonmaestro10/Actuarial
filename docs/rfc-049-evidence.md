# RFC-049: The evidence pack, and the two ways a validation file lies

Status: **implemented** — `engine/report/evidence.py`,
`scripts/evidence_pack.py`, `tests/test_evidence.py`

## Summary

Execution plan §8, item F1, against the landscape report's §6 strategic risk:

> This is the direct counter to "the incumbents are audited and
> regulator-familiar": a machine-generated, re-runnable evidence base for
> SII internal-model validation and VM-G governance.

One command emits a content-addressed directory: the test inventory
collected live from pytest, the closed-form identity list, an executor
equivalence attestation, docstring coverage, the reconciliations on record
(RFC-033), any benchmark numbers supplied, an `index.md` written from all of
it, and a manifest digesting the lot.

The incumbents' equivalent exists — it is a Word document, compiled by
consultants, per client, once. The difference worth having is not that this
one is prettier. It is that it can be rebuilt, and that rebuilding it from
the same source produces the same digest.

## Lie one: quoting a claim instead of making it

The obvious way to write the equivalence section is to state that the golden
suite asserts bitwise equality across executors — that is true, it is even
in the README, and it is worth nothing in a validation file, because it is a
sentence about tests rather than a check anybody ran.

So the section *runs* it. Every specimen is projected under both executors
through `record_run`, and what the pack records is the `results_digest`
each produced — the same digest the run registry uses to detect a
non-deterministic engine. A reader who does not trust the pack can rerun the
same specimen and compare the hex string.

Doing that immediately produced the finding that shaped the section. Run
naively over the worked examples, the attestation reports **two failures**:
`GroupLife` and `WithProfitsEndowment` disagree between the interpreted and
vectorized executors. Both are correct behaviour. Both declare `@pool`
variables, which reduce across the model-point axis, and the interpreted
executor evaluates one policy at a time — so its "pool" is a pool of one.
They are not in breach of the equivalence class; they are outside it by
construction.

A pack that reported that as a failure would be wrong. A pack that quietly
dropped them from the denominator would be worse. So the attestation carries
three states rather than two — in the class and bitwise, in the class and
disagreeing, and outside the class with the reason printed in the row — and
a template outside it gets the weaker claim it *can* support: run twice,
same digest. The counts in the summary line are stated against the
in-class population, with the excluded ones named.

The same instinct covers a specimen that fails to run at all: the row says
`NOT RUN` with the exception, in the index, not in a JSON file the reader
may never open. A template that could not be projected is not a template
that agreed.

## Lie two: a digest that means less than the reader thinks

The pack is content-addressed, and the acceptance criterion is that
rebuilding without code changes is digest-identical. That is only achievable
if the pack is careful about what a digest is *for*.

**Context is not evidence.** The machine, the interpreter version, the
platform string — recorded, written to `environment.json`, and deliberately
outside the digest. A validation pack rebuilt on a colleague's laptop from
the same commit should be recognisably the same document, and it is.

**Benchmarks are evidence, and they are machine-specific.** A performance
number is a claim, so it goes inside the digest — with the consequence that
a pack carrying benchmarks is reproducible only on the machine that built
it. Rather than choose one horn, the pack states which posture it has, in
the index, in the second paragraph: either "carries no machine-specific
claim: rebuilt from the same source anywhere, it digests identically" or
"its digest is reproducible only on the machine that built it". Benchmarks
are off by default.

The registry record follows from that. `artifact_id` digests the derivation
— the commit, the section list, and *which sections could be built* —
because a pack built with pytest unavailable is the answer to a different
question than one built with it, and filing them under the same identifier
would make `ArtifactRegistry` refuse the pair as a contradiction. What it
*will* refuse, and should, is two packs claiming the same commit and the
same configuration with different content: that is the signal that the tree
one of them was built from was not the commit it claims.

## What the pack will not do

It reports absences. `collect_tests=False`, a pytest that will not collect,
an `[api]` extra that is missing and therefore no worked specimens — each
produces a section that says so in one line and a registry record marked
incomplete, rather than a section quietly missing from an index whose reader
would have to notice the gap. The script exits non-zero on an incomplete
pack for the same reason.

Every number in it is computed at build time from the library, the registry
or pytest. Nothing is transcribed, so nothing can go stale — which is the
one property the hand-compiled version can never have.

## What is next

The pack compounds: every later item adds a section or enriches one. B1's
compiled executor joins the equivalence attestation as a third member; D2's
approvals and D3's audit log become the compliance section G2 asks for; G4's
pilot dry-run produces a pack as one of its outputs.
