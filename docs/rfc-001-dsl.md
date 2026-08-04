# RFC-001: Model definition DSL

Status: **implemented (Phase 0 interpreter)** — engine/core/model.py

## Summary

A model is a Python class; each actuarial quantity is one method decorated
with `@var`, defining a pure formula over projection time `t`. The engine —
not the author — owns evaluation order, caching, and (later) vectorization
and compilation. This is Prophet's proven declarative-variable paradigm with
plain Python as the surface syntax, so models live in git, diff cleanly, and
run under pytest.

## The contract

A `@var` body MUST:

1. Be a **pure function** of: `t`, `self.mp` (the model point),
   `self.assumptions`, and other `@var`s of the same model. Same inputs,
   same output, always.
2. Reference other variables only by **direct call** — `self.other(t)`,
   `self.other(t - 1)` — with an integer argument in `[0, proj_len]`.
3. Contain **no I/O, no mutation, no randomness, no dependence on
   evaluation order**. Stochastic inputs arrive as scenario data (Phase 2),
   never as `random()` calls inside formulas.

A `@var` MAY:

- Recurse on earlier time steps (`self.pols_if(t - 1)`); cycles at the same
  `t` are a model error (currently a `RecursionError`; the tracer will
  report the cycle path).
- Declare metadata: `@var(assumption="mortality")` binds the variable to a
  named assumption for lineage and reporting. Units and output tags will be
  added here, not in a separate registry.

Scalar whole-projection results (present values, reserves at issue) are
plain methods, not `@var`s — the time axis is what `@var` is for.

## `setup()` — precomputation

Some inputs are cheapest to build for the whole time axis in one call
rather than a period at a time: survival curves off a fractional-age basis,
discount vectors off a yield curve. A model may override `setup()`, which
the engine calls **once per instance, before any `@var` is evaluated**, and
store the result on `self`.

```python
class PayoutAnnuity(Model):
    def setup(self):
        axis = TimeAxis(self.assumptions.freq, self.proj_len + 1,
                        self.mp.valuation)
        self._survival = self.assumptions.survival(axis, self.mp.dob,
                                                   self.mp.sex)

    @var(assumption="mortality")
    def survival(self, t):
        return self.at(self._survival, t)
```

The same rules apply as to a `@var` body: pure, no I/O, no dependence on
evaluation order. Nothing set here may depend on a `@var`, because none have
been evaluated yet — which is what keeps the graph acyclic and `setup()`
from becoming a back door into imperative modelling.

`Model.at(slab, t)` takes one period out of a `(policies, periods)` array
and shapes it for whichever executor is running, so a template written once
runs deterministically or against a scenario slab unchanged.

## Time axes

`t` indexes **payment periods**, not necessarily years. A
`TimeAxis(freq, n_periods, valuation)` gives each policy its own valuation
date and places period `k` exactly `k * 12 / freq` months after it. Templates
built on `engine.data.basis.ValuationBasis` therefore run at any frequency
that divides 12, with mortality split across the two ages each period
straddles and discounting at the period rate.

The annual templates predate this and still assume `t` is a year. That is a
property of those templates, not of the DSL.

## Time conventions

- `t` runs `0 .. proj_len` **inclusive**, and counts payment periods. For a
  model over a `TimeAxis`, `proj_len` is `axis.n_periods - 1`.
- **Stocks** (in-force counts, fund values) are measured at the *start* of
  period `t`. **Flows** (premiums, claims, expenses) arise *during* period
  `t`; the product template documents whether each flow is paid at start or
  end of period, which fixes its discounting (`v(t)` vs `v(t + 1)`).
- Period length is a property of the model's time axis — annual for the
  Phase 0 templates, any frequency dividing 12 for those on a
  `ValuationBasis`.

## Why these restrictions

Purity + explicit time indexing means the variable graph can be **traced
once** per model shape: topologically sorted, recursion over `t` unrolled
into forward array loops, and the whole thing compiled to vectorized
kernels over `[model points × scenarios]` slabs (Phase 1, NumPy/Numba). The
interpreter and the compiled executor must agree to bitwise tolerance —
that equivalence test is part of the golden suite, and it is only possible
because formulas cannot observe how they are evaluated.

## Escape hatch

Anything expressible as a pure `t`-indexed function is a legal `@var`, so
novel product logic never waits on the engine team. Code that genuinely
cannot fit (path-dependent algorithms better written as loops) will get an
explicit `@procedural` marker: still traced for lineage, excluded from
kernel fusion, flagged in generated model documentation.

## `@pool` — reductions across the model-point axis

A `@var` is one formula per model point. A `@pool` is one formula per
*block*: its body reduces across the model-point axis, so every policy sees
the same value at a given `t`.

```python
@pool
def adjustment(self, t):
    return self.pool_sum(self.assets(t)) / self.pool_sum(self.liability(t)) - 1.0

@var
def pension(self, t):
    return self.pension_carried(t) * (1.0 + self.adjustment(t))
```

This is what a pooled variable-payment adjustment, a with-profits bonus
declaration or an asset share needs, and what a per-policy formula cannot
express — no member's pension can be computed from that member's own data
alone.

The contract is otherwise unchanged, and the graph must still be acyclic. A
pooled variable may read per-policy variables at the same `t`, and per-policy
variables may read it; what it may not do is depend on a per-policy variable
that in turn depends on it. In a variable-payment pool the liability being
valued is the one carried *into* the period, not the one the adjustment
produces — which is what keeps that acyclic.

Reduce with `self.pool_sum`, which sums the model-point axis and leaves any
scenario axis alone, so the same formula reduces within each scenario rather
than across them. It uses NumPy's pairwise summation: deterministic for a
fixed block order and length, which is what reproducibility needs.

Declaring one has a real consequence for execution. The vectorized executor
normally chunks a block for cache locality — safe precisely because model
points are independent — and it stops doing so for a model with pooled
variables, because a reduction over a chunk is a reduction over the wrong
population. That is detected from the variable kind, not from a flag anyone
has to remember to set.

## Open questions (deliberately deferred)

- Reductions other than a sum (a pooled maximum for a guarantee floor, a
  weighted median for a smoothing rule). `pool_sum` covers the products in
  the library today; the general shape is a reduction registry with a stated
  determinism guarantee per reduction.
- ~~Cycle detection with a readable error path (needs the tracer).~~ Done:
  `engine/core/graph.py`. The tracer records edges while the model runs, so
  it sees through helper methods a static scan would miss, and every edge
  carries the offset between the reading period and the period read — which
  is what separates a cycle from the recursion a projection is built on. A
  same-period cycle now raises `CyclicModelError` at depth two with the
  chain that closed it, instead of exhausting the stack a thousand frames
  later. Always on; measured at ~5% of the per-policy interpreter and
  nothing measurable on the vectorized executor.
- Typed variables (`NUM`/`BOOL`/array) — revisit when the compiler needs
  dtype information.
- Kernel fusion itself (PLAN §4.2). The graph now supplies what a compiler
  needs first — a traced dependency graph, a deterministic topological order
  over the same-period edges, and the look-back window a forward loop would
  have to keep alive — but nothing is compiled yet, and calling the tracer
  fusion would be a claim the code cannot support.
- Multi-entity models (policy + fund + reinsurance treaty interacting) —
  Phase 2, driven by the VA/VPLA library's needs.
