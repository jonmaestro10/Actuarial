# RFC-075: Bitwise across N machines, and the word "machines" is the problem

Status: **implemented** — `engine/core/dispatch.py`, `engine/api/worker.py`,
`tests/test_dispatch.py`

## Summary

B2. A run is already an idempotent, content-addressed question, so
dispatching one needs no job model: it is *submitting sub-runs and reducing*.
The split reuses `engine.core.parallel.shard_bounds` and the reduction is by
**shard index, never arrival order**.

A dispatched run is **bitwise identical to an undispatched one** at every
shard count — tested at 1, 2, 3, 5, 8 and 37 shards over 37 model points, the
last being one policy each and the first being no split at all.

The interesting part is the claim B2 was written with:

> the answer is bitwise-identical for 1 machine or N, **any topology**

On one machine that is true. **Across machines it is not**, and RFC-072
already measured why.

## The correction, and why it is a mechanism rather than a caveat

`np.exp`, `log` and `**` are implementation-defined to within an ulp — IEEE-754
§9.2 recommends correct rounding and no library provides it. The repo had
already reproduced a last-bit difference between microarchitectures on the
*same* NumPy. So a shard evaluated on an AVX-512 worker and one evaluated on
an older core can disagree in the last bit, and then the concatenated answer
depends on which worker happened to receive which shard.

That is worse than a slow answer. It is a reproducibility claim that fails
silently and only sometimes.

So the guarantee is stated at the resolution it actually holds — **bitwise
across workers that attest the same arithmetic, and refused otherwise** — and
RFC-070's rule applies: where a class boundary *can* be enforced, enforce it.
Every worker computes `attest()`, and the coordinator compares digests
**before** reducing. Unlike workers raise `ArithmeticMismatch` naming which
shards came from where, rather than producing a number nobody can reproduce.

There is an escape hatch, `require_matching_arithmetic=False`, because a
caller may knowingly prefer speed to reproducibility. They have to say so.

## Two digests, because they fail differently

`attest()` returns `exact` and `transcendental` separately.

`exact` covers the operations IEEE-754 §5 *requires* to be correctly rounded.
If that differs between two workers, one of them is non-conforming and the
problem is much larger than a dispatch. `transcendental` covers §9.2's
recommendations, which is where real machines differ.

Reproduced end to end, same machine, same NumPy, AVX-512 dispatch disabled:

| | baseline | `X86_V4` disabled |
|---|---|---|
| `exact` | `cbb6fe6196f932b1` | `cbb6fe6196f932b1` — **identical** |
| `transcendental` | `1c1c8332b6d13fdb` | `9352d817ecbe362e` — **differs** |

RFC-072's split, visible in a dispatch attestation. A single combined digest
would have told you something was wrong and not which thing.

## The bug this nearly shipped with

The first probe was **nine values**, and its digest was identical with
AVX-512 enabled and disabled. An attestation that agrees everywhere is the
same as no attestation at all, and it would have shipped looking like a
safeguard.

The cause is worth keeping: **NumPy dispatches its hand-written SIMD kernels
only above a length threshold** and falls back to scalar code below it — and
the scalar path is the one that does not vary. A short probe therefore
attests the wrong path.

`PROBE_LENGTH` is 4096 and a test asserts it stays above 1024, because a
future tidy-up that shortened the probe would silently disarm the check.

A second, smaller trap on the way: `NPY_DISABLE_CPU_FEATURES=AVX512F` is a
**no-op** on this build, because `AVX512F` is not among the features NumPy
reports finding (`X86_V3`, `X86_V4`, `AVX512_ICL`, `AVX512_SPR` are). Disabling
a feature that was never dispatched changes nothing and looks exactly like
"no difference exists".

## Retry is free, not merely safe

A shard is a pure function of (model, model points, assumptions, horizon), so
re-running it anywhere returns the same bits. A worker that dies mid-shard
costs the shard and not the run, and the retry needs no bookkeeping beyond
"submit it again" — there is no partial state to reconcile because there is
no state. Tested: a worker that fails twice, then succeeds, produces a run
bitwise identical to one where it never failed.

## The transport is injected, and the wire carries no pickle

`dispatch()` takes `submit` as an argument, so `engine/core` stays NumPy-only
(§1.4) and knows nothing about HTTP. `engine/api/worker.py` supplies one
transport that POSTs to a remote instance and one that evaluates in-process —
and the in-process one is **not a mock**, it is the one-worker topology.

The wire form carries the model as a catalogue **name**, never as a pickle. A
worker that could be made to unpickle whatever a coordinator sent it would be
a remote code execution endpoint wearing a projection engine's clothes.

## What is not built

The registry's shard tree. `DispatchReport` records the shard digests, the
attempt counts and the attestations, which is the content such a record
needs, but it is not yet written under a parent run record. That is the
remaining half of B2's acceptance and it is a small piece of work against
`engine/core/registry.py`.

**Milestone M2 is therefore not claimed.** B1 is done and B2 is most of the
way, but M2 is "the unanswerable benchmark" — the nested-stochastic numbers
across N workers *with* the reproducibility statement — and the honest
statement has changed shape since it was written. It is now "bitwise across
workers that attest alike", which is still a claim no incumbent makes, and it
should be published in those words rather than the plan's original ones.

## Acceptance

`tests/test_dispatch.py` — 16 tests.

Bitwise equality at six shard counts including both degenerate ones, with
shape and dtype asserted separately. The reduction is shown to be by index
rather than arrival. The attestation is shown to be deterministic, to exclude
its own labels from its identity, and to **catch a real microarchitecture
difference** in a subprocess with CPU features disabled — skipping honestly
if the CPU has nothing to disable.

The refusals: workers that do not attest alike, a shard that never succeeds
(named by its model-point range), and a pooled model, which cannot be sharded
at all because each shard would see a pool of itself.
