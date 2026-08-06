# RFC-074: The kernel is fast; the pre-pass is the ceiling

Status: **implemented** — `engine/core/compiled.py`, `tests/test_compiled.py`,
`scripts/benchmark_compiled.py`

## Summary

B1's executor. The `@var` graph is traced, topologically sorted and fused
into a native forward loop, and the loop produces results **bitwise identical**
to the vectorized executor — §1.2's guarantee, not a weakened version.

**13 of the 14 deterministic templates compile and agree bit for bit**, on
every variable and every period, with shape and dtype asserted separately.
The fourteenth is refused because every one of its variables is hoisted, so a
kernel would be the vectorized executor with extra steps.

The speed result is more interesting than a single number, and both halves
are published because quoting either alone would mislead:

| | median | range |
|---|---|---|
| **kernel alone** | **14.6×** | 5.4× – 261× |
| **end to end** | 1.36× | 0.92× – 9.94× |

The kernel clears B1's ≥5× target comfortably. The end-to-end figure does
not, and the reason is Amdahl's law rather than a defect in the fusion: the
hoist pre-pass is a median **55%** of the vectorized runtime and the kernel
cannot remove it. On `PayoutAnnuity` the pre-pass is *slower than the entire
vectorized run*, and the compiled path is a net loss at 0.92×.

That is reported rather than tuned away. PLAN's rule is that marketing equals
engineering; the honest claim is "the fused arithmetic is worth an order of
magnitude, and today most templates spend more time outside it than in it".

## Three decisions carry the design

**A variable is hoisted whole, never in part.** A `@var` body that reaches a
mortality table or evaluates `exp` cannot be fused. The tempting move is to
hoist that sub-expression and fuse the rest — and it cannot be done. A
sub-expression is an anonymous intermediate with no name that survives to run
time, so nothing could compute it for the *next* block. A `@var` has a name
the vectorized executor can evaluate for any batch, which is exactly what a
hoist slab has to be.

So tracing runs to a fixed point: each pass discovers variables that cannot
be fully traced, adds them to the hoisted set, and re-traces. Coverage went
from **2 of 14 to 13 of 14** on that change alone.

**Every cross-variable reference is `ref(variable, offset)`.** Reading
`pols_if(t − 1)` and reading `q_x(t)` are the same kind of edge at different
offsets, and whether the answer comes from a kernel slab or a hoist slab is a
property of the *variable*, not of the reference. Unifying them is what makes
the tape stable; keeping them apart is what made an earlier version drift,
because a hoisted value read at `t − 1` interned identically to one read at
`t`.

**A scalar is keyed by position, not by value.** `years_elapsed(t)` is a
different number every period. Interning by value gives a different tape each
period and nothing ever stabilises; interning by *where it appears* gives a
stable tape and a per-period input vector. Scalars constant across every
period are folded into the source as literals.

A subtlety that cost a debugging pass: a scalar can be **absent** in some
periods, because `pols_if`'s `t == 0` form has different operands from its
recursion. Absent is not the same as constant — those stay per-period vectors
and the periods that never read them are filled with zero.

## The tape must stabilise, and that is checked

Period 0 gets its own expression, because every stock variable branches on
`t == 0`. From period 1 the tape must be structurally identical at every
traced period, and `plan()` refuses a model where it is not. A tape that kept
changing would mean the kernel had been specialised to the periods that
happened to be traced.

For the same reason a `@var` body that branches on model-point data is
refused rather than specialised: the tape would be right for the traced batch
and wrong for the next block. **That is RFC-070's bug with a compiler behind
it** — a conditional branch a particular batch never entered, which survived
two RFCs written about it.

## Pooled models are hoisted, not refused

This is an *array* executor, like the vectorized one, so it does not apply
`check_per_policy`. A `@pool` body reduces with `pool_sum`, which classifies
as a reduction (RFC-072: no length is safe) and is therefore hoisted whole —
the reduction is performed by the vectorized executor over the real block,
exactly as it would be without a kernel.

**Pooling costs fusion, not correctness.** `GroupLife` and
`WithProfitsEndowment` both compile and both agree bitwise; they simply hoist
more than most.

## The generated source is kept, and it is readable

```python
o_pols_if[0, j] = (f_init_pols[j] * 1.0)
...
o_pols_if[t, j] = (o_pols_if[t - 1, j] * (1.0 - h_q_x[t - 1, j]))
```

An auditor can read the loop the engine actually ran. That is worth more here
than in most compilers, because the thing being compiled is a regulatory
calculation. A test asserts that `np.exp`, `np.log`, `np.sum`, `**` and
`fastmath` appear nowhere in it — RFC-072's rule stated as a property of the
emitted artefact rather than as an intention.

`fastmath` is off and must stay off: it licenses reassociation and
contraction, which is precisely the permission to turn `a * b + c` into a
fused multiply-add — more accurate, and a different number.

## What the benchmark says to do next

The pre-pass and the kernel scale differently, so the benchmark reports three
numbers per template rather than one. The next piece of work is named by the
measurement rather than guessed: **interleave the pre-pass with the kernel per
period**, so a hoisted variable is computed from the kernel's own slabs
instead of from a second full traversal that recomputes the fused variables
as dependencies. That is a real piece of work, not a tuning knob, and it is
where the remaining order of magnitude is.

Until it is done, the compiled executor is a win where the fused fraction is
high (`IncomeProtection` 9.94×, `Endowment` 4.35×) and a wash or a small loss
where it is not (`PayoutAnnuity` 0.92×). The plan's own risk row anticipated
exactly this posture: coverage stated, never fudged.

## Acceptance

`tests/test_compiled.py` — 9 tests, 6 of them needing the `[compile]` extra.

There is **no tolerance anywhere in the suite**. Equality is compared as bit
patterns with shape and dtype asserted separately, on a per-template basis
and across the whole catalogue, and against a *chunked* vectorized run as
well as an unchunked one — agreeing with both is a stronger statement than
agreeing with either.

The refusals: a model with nothing to fuse, a variable the kernel does not
produce, a tape that does not stabilise, and a body that branches on traced
data. Coverage is computed rather than claimed, with a floor so that a change
which silently stopped compiling everything fails in the suite rather than
showing up as an unexplained benchmark.
