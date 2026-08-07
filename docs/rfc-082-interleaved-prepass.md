# RFC-082: The fix that was named, built, measured and refused

Status: **implemented, and the feature is off** — `engine/core/compiled.py`,
`scripts/benchmark_compiled.py`, `tests/test_compiled.py`

## Summary

The execution plan named B1's remaining work precisely:

> interleave the pre-pass with the kernel per period, so a hoisted variable is
> computed from the kernel's own slabs instead of a second full traversal that
> recomputes the fused variables as dependencies.

It was built. It is bitwise. **It does not pay**, and the machinery is kept
switched off with the measurement recorded beside it.

## The diagnosis was true only sometimes

The first thing to measure was the premise, and it does not hold uniformly.
`CompilationPlan.recomputed` is the fused work a pre-pass over the hoisted set
duplicates — the closure taken over *every* offset, because a hoisted variable
reading a fused one at `t-1` forces the model to evaluate it for every earlier
period and that edge does not appear in the same-period topological order.

| | end-to-end | fused recomputed |
|---|---|---|
| TermLife | 1.13× | 15 of 16 |
| GroupLife | 1.16× | 9 of 10 |
| UniversalLife | 3.15× | 18 of 35 |
| **PayoutAnnuity** | **1.15×** | **0 of 2** |
| **PensionBuyout** | **1.04×** | **0 of 3** |
| **GeneralInsurance** | **1.07×** | **0 of 7** |

The plan names `PayoutAnnuity` as the worst case and it recomputes **nothing**:
no hoisted variable reads a fused one at any offset. Its 3.3-second pre-pass is
not duplicated work, it *is* the model — two cheap fused variables and five
expensive hoisted ones. The three worst end-to-end templates are exactly the
three the named fix cannot touch.

## What interleaving trades away

The mechanism is the model's own `(variable, period)` memo. Each fused segment
writes its slab rows for period `t`; **those exact rows** — the same object, not
an equal one — are placed in the memo, so a hoisted variable in the next segment
reads the array the kernel wrote. There is no arithmetic between the write and
the read, which is what keeps the path bitwise rather than close.

What it costs is the fusion. `graph.order()` alternates between classes up to
eleven times on `TermLife`, so interleaving splits one kernel into one per
segment, and a period makes N passes over the slabs instead of one. That is the
thing B1 was built to buy, being given back.

## The measurement that was wrong, and why it is worth recording

The first A/B looked decisive: TermLife **2.39×**, LongTermCare **0.11×**. A
nine-fold spread invites a story about which models suit interleaving.

There is no such story. The interleaved path ran **unchunked** while the
baseline chunked. `run_vectorized` splits the block so a period's working set
stays in cache; running 50,000 policies at once discards that, and the
templates that collapsed were the ones with the largest per-period
intermediates. I was comparing two memory strategies and reading the result as
a comparison of two schedules.

With chunking added to the interleaved path, nearly every figure inverts:

| | recomputed | interleaved / whole |
|---|---|---|
| TermLife | 94% | 1.21× |
| GroupLife | 90% | 1.07× |
| PensionBuyout | 0% | 1.03× |
| WithProfitsEndowment | 71% | 0.97× |
| UniversalLife | 51% | 0.89× |
| WholeLife | 17% | 0.72× |
| CreditLife | 31% | 0.55× |
| LongTermCare | 23% | 0.50× |

Per-period dispatch, multiplied by segments and by chunks, costs more than the
recomputation it avoids. And `recomputed` — the quantity the whole hypothesis
rests on — **does not predict the outcome**: 17% gains where 23% loses. That is
the clearest statement that the answer had to be measured rather than reasoned.

## The headline was never a measurement

Found on the way. `benchmark_compiled.py` computed

```python
end_to_end_speedup = vectorized / (pre_pass + fused)
```

from two separately-timed pieces. The 1.36× in the execution plan is a **model**
of end to end, not a run of it — it assumes the two phases are all there is,
and after interleaving they are not even sequential. It now times
`run_compiled` directly, and the honest median is 1.66× rather than 1.41×
because the pieces never summed to the whole.

## What is kept, and why

`interleaves` returns `False`. The segments, the closure and the cache
injection stay, and a test drives the interleaved path and asserts it is
bitwise — code that is kept and never run is code that has already stopped
working.

What would change the answer is **fewer segments**, which means a smaller
hoisted set, which means widening what a kernel may contain. RFC-072 settled
that boundary against IEEE-754 and it does not move for a performance
argument. So the next attempt is not a better schedule; it is asking which
variables are hoisted for reasons weaker than the standard — a different item,
and one this measurement gives an opening for.
