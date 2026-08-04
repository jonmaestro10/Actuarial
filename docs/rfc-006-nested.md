# RFC-006: Nested stochastic projection

Status: **prototype implemented** — `engine/core/nested.py`,
`Model.restart_fields`, `scripts/benchmark_nested.py`

## Summary

PLAN.md §4.4 calls nested stochastic "the real killer workload — VA/VPLA
hedging, VM-21, SII internal models", and §8 makes a nested-stochastic
prototype a Phase 2 exit criterion. It is the workload that separates an
actuarial platform from a projection script, and it is where a naive
implementation stops being merely slow and becomes impossible.

An **outer** projection runs the block under real-world scenarios. At
selected dates along each outer path the guarantees have to be *valued*,
which needs a second, risk-neutral projection starting from the state that
path has reached. Outer scenarios × valuation dates × inner scenarios is a
number with three factors in it and each is in the hundreds.

## The restart is the whole thing

Everything else here is arithmetic. The load-bearing piece is being able to
start a projection from the middle of another one, exactly.

`Model.restart_fields(t)` returns the model-point fields a fresh projection
would need in order to begin precisely where this one stands at `t`. It is
exact rather than approximate for a structural reason: **the `t == 0` branch
of every stock variable reads exactly one model-point field**, so a
template's state and its model point are the same list of numbers.

```python
fields.update(
    age_at_entry=self.mp.age_at_entry + elapsed,
    term_years=self.mp.term_years - elapsed,
    premium=self.fund_boy(t),
    init_pols=self.pols_if(t),
    gmwb_base=self.benefit_base(t),
)
```

`tests/test_nested.py` restarts a GMxB contract at four different dates and
projects it forward on the tail of the same scenario. Every variable —
fourteen of them, including the ratcheting benefit base and the dynamic
lapse rate that depends on it — reproduces the straight-through projection
**bitwise**. If that failed, no number a nested run produced would be
salvageable, and no amount of inner scenarios would reveal it.

Two things the restart has to get right that a fund value alone cannot:

- **The benefit base.** Two contracts with identical account values can owe
  very different guaranteed withdrawals depending on where their funds have
  *been*. A restart that dropped it would silently reset every ratchet ever
  earned.
- **The calendar.** `Assumptions.at_year(offset)` moves the basis on with
  the block. An inner projection starting at year 10 that priced mortality
  improvement as if it were year 0 would be wrong in one direction, and
  nothing downstream would notice.

Restarts land on policy anniversaries only, and a part-year restart raises.
Attained age and remaining term are whole years; a template that pretended
otherwise would be inventing both.

## What makes the cost tractable

Not cleverness — batching. At a given valuation date every outer path has
reached *some* state, and a state is a model point. So one inner run values
all of them at once: `n_model_points × n_outer` restarted policies against
`n_inner` scenarios, in the same slab the stochastic executor already fills.

**The number of inner projections is the number of valuation dates**, not
the number of outer nodes. `NestedRun` reports it, along with the inner
policy-scenario cells, rather than leaving the size of the job to be
guessed:

```
guarantee_strain: 200 model points x 100 outer x 200 inner
                  at 5 valuation times = 20,000,000 inner cells
5 inner projections in 58.53 s
```

The inner loop accumulates the discounted measure period by period and
prunes the memo behind itself (the window from #17), so an inner run's
working set is set by the slab rather than by the projection length. A
nested job that materialised every period of every inner scenario would run
out of memory long before it ran out of patience.

## Common random numbers, deliberately

Every outer node at a given valuation date is valued against the **same**
inner scenarios. That is a choice.

The quantity of interest is usually how the guarantee cost *differs* between
outer states — a well-funded path against a poorly-funded one. Independent
inner draws would bury that difference under sampling noise that has nothing
to do with the states being compared. Different valuation dates get
independent streams, so the noise does not accumulate along a path.

## The error bar is part of the answer

An inner mean over 200 scenarios is an estimate. `NestedRun.stderr` sits
next to every value, because a reader who cannot see the error bar will read
the value as a number. The tests confirm it shrinks as `1/√n` and is exactly
zero when the inner measure is deterministic.

## What the numbers do

Measured rather than asserted, and one of them corrected an assumption:

| valuation date | mean guarantee value | spread across outer paths |
|---|---|---|
| 0 | 34,680 | **0** |
| 4 | 33,251 | 41,816 |
| 8 | 29,538 | **61,243** |
| 12 | 29,883 | 58,147 |
| 16 | 29,299 | 51,585 |

At inception nothing has happened, so every outer path holds identical state
and must be valued identically — the cleanest sanity check available, and
the first thing that would break if outer state leaked into the inner
valuation incorrectly.

The spread then **peaks in the middle** rather than increasing
monotonically, which is what I assumed before measuring. Two effects pull
against each other: outer paths keep diverging, widening the spread, while
the remaining term keeps shortening, shrinking every value towards zero and
compressing the spread with it.

## Not in scope

- **Least-squares Monte Carlo and proxy models.** PLAN §4.4 lists them as an
  *optional, clearly-labelled* acceleration with error estimates. They
  replace the inner projection with a fitted surface; this prototype is the
  thing they would have to be checked against, so it comes first.
- **Stochastic-on-stochastic beyond two levels.**
- **Path-dependent hedging.** Valuing a guarantee at a date is not the same
  as running a hedge programme along the path, which needs the hedge
  instruments as first-class model objects.
- **A measure that is not a `@var`.** The measure names a variable and the
  driver discounts it. A bespoke one means writing a `@var` for it, which is
  what the DSL is for; a Python callable would put arbitrary code inside the
  hot loop and outside the dependency graph.
