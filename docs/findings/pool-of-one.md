# The pool of one

**Claim.** A `@pool` variable reduces across the block. Evaluated one policy
at a time it reduces over *one policy*, so every policy sees a pool
consisting of itself — and the run **completes**, returning plausible
numbers that nothing downstream would question.

**Demonstrate it:** `python scripts/findings/pool_of_one.py`
**Recorded in:** [`docs/rfc-061-pooled-equivalence.md`](../rfc-061-pooled-equivalence.md)

## The shape of it

The finding is not that the wrong reading errors. It is that it does not.

On `GroupLife`'s worked example, the pooled experience refund comes to
**42,103** when the block is reduced correctly and **25,005** when each
policy is evaluated alone — a 40.6% error in a number of entirely ordinary
magnitude. No exception, no warning, no `NaN`; just a smaller refund.

That is the failure mode this repository is most concerned with, and the one
a test comparing values to a tolerance cannot catch, because both answers are
inside any tolerance you would set for a refund.

## What was done about it

A class boundary that rests on a docstring is not a boundary. `run()` now
raises `PooledBlockError` when asked to evaluate a pooled or coupled model
per policy over a block of more than one, naming the pooled variables:

> `GroupLife` declares pooled variable(s) `['experience_refund', …]`, which
> reduce across the block — and the interpreted executor evaluates one policy
> at a time, so each of these 2 policies would see a pool of itself.

A block of exactly **one** is permitted, because there a pool of one is the
same reduction either way, bit for bit. That is what keeps the pooled
templates inside the dual-executor equivalence class for everything except
the reduction itself.

## The general rule it earned

**A class boundary wants a mechanism.** RFC-070 found the other half of this
lesson: an exclusion that rested on a docstring sentence had been wrong for
as long as it existed, and the evidence pack had been contradicting it the
whole time. Where an exclusion cannot be enforced in code, it is a test —
never a paragraph.
