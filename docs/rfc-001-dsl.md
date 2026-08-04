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

## Open questions (deliberately deferred)

- **Reductions across the model-point axis.** Every `@var` today is a
  function of *one* model point, which is what lets the vectorized executor
  chunk a block for cache locality without moving a number. A pooled
  variable-payment adjustment, a with-profits bonus declaration or an asset
  share is a reduction over the whole population inside the time loop, and
  has no spelling. `Model.couples_model_points` marks a model that would
  need one, so the runner stops chunking it; the variable kind itself is
  still to be designed. See docs/vpla-review.md §7.1.
- Cycle detection with a readable error path (needs the tracer).
- Typed variables (`NUM`/`BOOL`/array) — revisit when the compiler needs
  dtype information.
- Multi-entity models (policy + fund + reinsurance treaty interacting) —
  Phase 2, driven by the VA/VPLA library's needs.
