# RFC-061: The pool of one, and the class a pooled model is really in

Status: **implemented** — `engine/core/runner.py`, `engine/core/model.py`,
`engine/report/evidence.py`, `tests/test_pooled.py`

## Summary

RFC-049's evidence pack runs the executor-equivalence attestation rather
than quoting it, and the first time it ran it reported two failures:
`GroupLife` and `WithProfitsEndowment` disagree between the interpreted and
vectorized executors.

They are not failures, and finding out why turned up something worse than a
disagreement.

Both templates declare `@pool` variables, which reduce across the
model-point axis. The interpreted executor builds one model instance per
model point. So `pool_sum` receives a scalar, and — this is the part worth
fixing — `pool_sum` **returned it**:

```python
totals = np.asarray(values, dtype=np.float64)
return totals if totals.ndim == 0 else totals.sum(axis=0)
```

`run(GroupLife, five_thousand_members, ...)` therefore produced a complete
set of numbers in which every member's experience refund was computed
against a scheme consisting of that member alone. No error, no warning,
nothing in the output to distinguish it from the real thing. The constraint
was known — `tests/test_group_life.py` has a helper whose docstring says a
reduction in the interpreted runner "would pool a block of one" — but it was
observed by convention rather than enforced, which is the state every
silently-wrong number starts in.

## Three things, in the order they matter

**The runner refuses.** `engine/core/runner.run` now raises
`PooledBlockError` for a pooled or `couples_model_points` model over a block
of more than one, naming the pooled variables and pointing at
`run_vectorized`. This is the same posture `engine/core/parallel.py` already
took for sharding — "a reduction over a shard reduces over the wrong
population, and produces plausible numbers while doing it. That is the case
worth refusing rather than warning about" — extended to the executor that
had the same problem and no guard.

The refusal is judged on the *model*, not on the requested outputs. A
per-policy variable may read a pooled one, so "none of these outputs is
pooled" is not the same statement as "nothing pooled is evaluated", and the
difference is a graph walk whose answer nobody would check.

**One model point is still allowed, and that is the interesting part.** A
pool of one is the same reduction in both executors, bit for bit — so on a
block of one these templates are *inside* the dual-executor equivalence
class, and measured that way they are:

```
GroupLife              n=1  bitwise=True     n=2  bitwise=False
WithProfitsEndowment   n=1  bitwise=True     n=2  bitwise=False
```

Every formula in both templates is therefore still held to the full
invariant of §1.2 — the mortality, the asset share, the bonus, the claim,
all of it. What a block of one cannot express is the reduction, and that is
precisely and only what falls outside. So the attestation now carries the
single-point bridge alongside the exclusion, and a template outside the
class is not a template with a gap in its evidence. Keeping the n=1 case
legal is also what keeps `Model.trace` and RFC-030's generated
documentation working, since both evaluate a single specimen per-policy.

**What the pooled block *is* held to gets asserted rather than assumed.**
`tests/test_pooled.py` pins the three claims that survive: the block is
never chunked (observed from the batch each instance receives, not trusted
from the branch that decides it), the same question twice gets the same
answer, and the pooled variable really does take one value across the whole
block and move when the population changes — the thing a pool of one
would not do.

## What was not done, and why

The tempting fix is to make the interpreted executor block-aware: evaluate
every policy for period *t* before moving to *t + 1*, so a pooled reduction
sees the population. That would close the gap and destroy the reason the gap
is worth having.

The dual-executor invariant is evidence *because* the two executors are
structurally different — one policy at a time against one period at a time,
scalars against arrays, independent recursion against slab recursion. Two
implementations that agree are worth something in proportion to how
differently they were built. Rewriting the reference implementation to share
the vectorized executor's architecture would raise the number of templates
in the equivalence class and lower what membership means, which is
optimising the metric rather than the property.

The honest arrangement is the one here: a per-policy executor that says what
it cannot do, and a second class of claims for the models it cannot do it
for.

## Consequences for §1.2 and for B1

The execution plan's §1.2 says every template must produce bitwise-identical
results under both executors, and B1's acceptance extends that to a third.
Read literally, both are false for pooled templates and were false before
this RFC — the plan inherited a claim the library had already outgrown. §1.2
is amended to state the two classes:

- **the per-policy class**: interpreted ≡ vectorized ≡ (in future) compiled,
  bitwise, for every template that does not couple its model points;
- **the block class**: for pooled and coupling templates, vectorized ≡
  compiled bitwise, plus chunk-invariance, run-to-run determinism, and the
  single-point bridge into the per-policy class.

Nothing is weakened to a tolerance, which is what §1.2 exists to prevent.
The equivalence class is smaller than the sentence claimed, and it is now
the one the code can actually stand behind.

B1 should be read with this in mind: its acceptance criterion is
"bitwise-identical across interpreted / vectorized / compiled", and for two
of the library's templates the first of those three is not a member. The
compiled executor's target is the block class for them.
