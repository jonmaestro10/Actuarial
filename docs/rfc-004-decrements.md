# RFC-004: Multiple decrements — independent rates in, dependent rates out

Status: **implemented** — `engine/data/decrements.py`

## Summary

An assumption basis states each decrement on its own. The mortality table
says what fraction of lives die *if nothing else can remove them*; the lapse
assumption says what fraction surrender *if nobody dies*. Those are
**independent** (single-decrement) rates.

A projection needs **dependent** (multiple-decrement) rates: how many
actually leave by each cause when the causes compete for the same lives
during the same period. PLAN §5.1 lists multi-decrement tables as a Layer 0
requirement, and until now the engine answered the question implicitly —
every template applied mortality to everyone, then lapse to the survivors.

That is one answer among several, and it is the only one that depends on the
order the multiplications happen to be written in.

## The finding that prompted it

`tests/test_frequency.py`, from the sub-annual work, established something
that looks paradoxical at first: running the same assumptions monthly
instead of annually leaves the **same policies in force at every
anniversary** — the sub-period split telescopes exactly — but moves exits
from mortality to lapse. On a 20-year term block the present value of claims
fell from 0.13795 to 0.13411 going from annual to monthly.

Nothing about the block changed. What changed is that a finer step
interleaves the decrements instead of applying them whole in sequence, so
mortality no longer gets first claim on a whole year of exposure. The test
recorded the movement and observed that it converged, but it had no way to
say *what it converged to*.

It converges to a constant hazard for each cause. That is now something the
basis can state directly, in one step, at any frequency.

## Three methods

Each is **exact** under its own statement about when in the period people
leave. None is an approximation of another.

### `sequential` — the default

`q_j = q'_j Π_{k<j} (1 - q'_k)`. Decrement `j` acts on whoever the earlier
decrements left behind. Order-dependent.

It is the default for one reason: it is what every template already did, and
reproducing it operand for operand is what lets the existing golden suite
stand as the proof that this layer moved nothing.

### `udd`

Uniform distribution of decrement `j` in its own single-decrement table.
Exits by cause `j` accrue evenly through the period while the other
decrements thin the population continuously:

    q_j = q'_j ∫₀¹ Π_{k≠j} (1 - s q'_k) ds

For two decrements that integral is `q'_1 (1 - q'_2 / 2)`; for three it is
the Bowers formula. The implementation expands the product in `s` by
repeated convolution and integrates term by term, so it is exact for any
number of decrements rather than a quoted special case — checked against
both textbook forms and, for four decrements, against numerical integration
of the integral it claims to evaluate.

Order-independent. With two decrements it lands exactly on the midpoint of
the two sequential orderings, which is a satisfying way to see what the
ordering argument was groping at.

### `constant_force`

A constant hazard per cause through the period. Forces add, and total exits
split in proportion to them:

    μ_j = -ln(1 - q'_j),   q_j = (μ_j / Σ μ) (1 - Π_k (1 - q'_k))

Order-independent, and the limit the sequential method converges to as the
projection frequency rises.

## The invariant

Every method agrees on total survival, `Π_k (1 - q'_k)`. They disagree about
*who* left, never about *how many*.

Two consequences, both asserted directly:

- **Switching method cannot move an in-force count.** The block runs off
  identically; only the attribution of exits changes.
- **`Σ_j q_j = 1 - Π_k (1 - q'_k)` exactly**, for any number of decrements
  and any method. If that failed, the in-force roll-forward and the
  cause-by-cause exits would tell different stories about the same block.

## Closing the loop

`tests/test_decrements.py` runs a year of the sequential method by hand at
1, 10, 100, 1,000, 10,000 and 100,000 steps and shows the gap to the
`constant_force` answer closing **first order in 1/m** — each tenfold
refinement cuts it tenfold, to 2.5e-08 at 100,000 steps. The same approach
is then shown through the real projection over the frequencies `Assumptions`
admits (those dividing 12), where the gap scaled by frequency is stable to
within 2%.

That is the answer `test_frequency.py` was converging on, available in one
annual step.

## Three things worth arguing with

**Total exits are `1 - Π (1 - q'_k)`, not the more accurate
`-expm1(-Σ μ_k)`.** The two are algebraically equal and the second is better
conditioned for small rates. The first is used anyway, because the
projection rolls its in-force count forward by `Π (1 - q'_k)` and exits
computed from a different expression for the same quantity would leave the
block failing to balance by an ulp. Balancing exactly beats a relative
improvement on a quantity whose absolute error is one ulp of the whole
population.

**A single decrement short-circuits.** With nothing to compete against every
method is the identity, and saying so explicitly keeps it *bitwise* — the
`constant_force` formula would otherwise round-trip through `log1p` and
`exp`. This is what makes the deferred annuity, which has only mortality,
provably invariant to the decrement basis.

**Two certain decrements split equally.** A mortality table reaches `q = 1`
at its limiting age, so an infinite force is a real input rather than a
pathological one. One certain decrement takes everything. Two is genuinely
ambiguous — the answer depends on how the limit is approached — and the
symmetric split is chosen because a `nan` is not an answer.

## How templates use it

The assumption set owns the choice; templates never branch on it. Each
declares which rates compete, in the order the sequential method applies
them, and asks for the split:

```python
def _decrements(self, t):
    return {"mortality": self.q_x(t), "lapse": self.lapse_rate(t)}
```

`Decrements.split` works in **counts**, not rates, and that is deliberate:
under `sequential` it reduces the in-force figure one decrement at a time,
which is the identical chain of multiplications the templates already
evaluated. Going through a dependent rate and multiplying afterwards would
re-associate the product and move golden values by an ulp.

Term life, the deferred annuity and both unit-linked templates are on it.
Every projection they produce is **bitwise identical** to the code that
preceded this RFC, across both executors — verified by dumping thirteen
output series from four templates before and after and comparing for
equality, not tolerance.

## Not in scope

- **Decrement rates that depend on each other's outcome** — a lapse
  assumption that varies with whether a disability claim is in payment
  needs a state model, not a decrement table.
- **Central rates and exposure-based estimation.** This layer converts
  independent rates to dependent ones; deriving either from experience data
  is the assumption-setting problem, which belongs upstream of the engine.
- **A decrement basis that varies by duration or cause within a projection.**
  The method is a property of the assumption set, not of `t`. Nothing
  prevents that later; nothing needs it yet.
