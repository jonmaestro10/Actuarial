# A sum has no safe length

**Claim.** Floating-point addition is not associative. NumPy sums in pairwise
blocks; a sequential loop sums in order. They first disagree at **twelve**
elements, and past that which lengths agree depends on the *values* rather
than on the length — so "reduce only small blocks" has no threshold to use.

**Demonstrate it:** `python scripts/findings/reduction_order.py`
**Recorded in:** [`docs/rfc-072-bitwise-boundary.md`](../rfc-072-bitwise-boundary.md)

## The measurement

Over lengths 1 to 399, with one array per length:

- first disagreement at **12** elements;
- many longer lengths still agree — 63 disagrees, 64 agrees, 127 agrees, 128
  disagrees;
- at 100,000 elements they always disagree.

The pattern is not a threshold. It is the interaction between NumPy's
pairwise block size and where the partial sums happen to lose bits for these
particular values.

## Why the tempting mitigation is the dangerous one

The obvious response to "reductions are order-dependent" is to keep them
small — reduce inside a kernel when the block is short, and fall back
otherwise. There is no length at which that is safe, and the rule would be
right *most* of the time, which is the worst available property for a
correctness guarantee.

So `engine/core/bitwise.py` classifies every reduction as `"reduce"` and a
compiled kernel may never contain one. That is what puts every `@pool` body
outside a kernel — by arithmetic, rather than by policy.

## The wider fact

This is the same gap in IEEE 754 that makes `np.exp` and `**` non-portable
across CPUs — the reason an evidence-pack digest is scoped to one machine.
The standard mandates correct rounding for `+ − × ÷ √` and comparison, and
declines to mandate it for the transcendental library or for any particular
association order. Both consequences follow from one sentence in §5 and one
absent sentence in §9.2.
