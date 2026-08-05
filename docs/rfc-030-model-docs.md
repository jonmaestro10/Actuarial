# RFC-030: The formula browser, and the trace that is too short

Status: **implemented** — `engine/core/modeldoc.py`, `scripts/model_docs.py`,
`tests/test_modeldoc.py`

## Summary

PLAN §7 asks for

> Auto-generated model documentation from `@var` docstrings + dependency
> graph visualizer (this replaces Prophet's formula browser).

The graph half has existed since RFC-001: `DependencyGraph` renders Mermaid,
describes evaluation order, and answers what reads what. This is the other
half — pull every `@var`'s docstring, its declared assumption and its
**source**, join it to the graph, and write out Markdown.

## Markdown, not a viewer

A formula browser you have to launch is a thing you consult when you already
suspect a problem. A generated Markdown file is diffable, reviewable in a
pull request, and greppable — and §7's *first* bullet asks for git-native
model versioning, which a generated document only participates in if git can
read it. Change a formula and the diff shows up next to the code change that
caused it. GitHub renders the Mermaid block natively, so the diagram is free.

`scripts/model_docs.py` writes one file per template. A variable's entry
carries its docstring, the assumption it declares, what it reads (with the
time offset), what reads it, and the formula itself:

```
### `commission_clawback`

**Assumption:** `commission`

Initial commission recovered from policies lapsing in period t.

A negative cashflow to the insurer's outgo — recorded positive here
and subtracted where it is used, so the sign is stated at the point
of use rather than carried around.

**Reads:** `duration`, `pols_lapse`

**Read by:** `profit_before_tax`
```

The time offset is kept rather than flattened, because **the recursion a
projection is built on is invisible in the source of any single variable**.
`pols_if` reading `pols_if [t-1]` is what makes a projection a projection,
and a browser that showed only "reads `pols_if`" would have lost the one
thing worth showing.

## The finding: a three-period trace can document a recursion as a constant

The dependency graph is discovered by **running** the model, not by parsing
it — a `@var` body is ordinary Python and there is no other way to know what
it touched. `Model.trace` runs three periods by default, and its docstring
says:

> Three periods by default, which is the fewest that exercises both a
> variable's `t == 0` branch and its recursive one. **A longer trace cannot
> find new edges in a well-formed model** — a `@var` body may not branch on
> model-point data — but it costs more.

That is true of every template in the library and **false in general**. A
`@var` may branch on `t`, and `t` is not model-point data. Take a variable
that first reaches back six periods at `t = 6`:

```python
@var
def slow(self, t):
    """One until period six, then whatever it was six periods ago."""
    return 1.0 if t < 6 else self.slow(t - 6)
```

| trace length | edges found | horizon |
|---|---|---|
| 3 | **none** | 0 |
| 5 | **none** | 0 |
| 6 | `slow [t-6]` | 6 |
| 20 | `slow [t-6]` | 6 |

At the default trace length this variable is reported with **no
dependencies at all**. A document generated from it describes a recursion as
a constant, and nothing raises — the graph is not wrong about what it saw,
it simply did not see far enough.

And the edge does not appear gradually. It is invisible at five periods and
complete at six, because six is the period the branch first fires in. There
is no partial signal to notice.

## What follows from it

Two things, and they are the design of the module rather than a caveat on
it.

**The document records how far it looked.** `document(..., trace_length=3)`
puts *"Dependency graph traced over 3 periods"* in the output. A document
that does not say how far it looked is not evidence of anything.

**`graph_is_settled` re-traces and compares.** It runs a short trace and a
long one and reports whether they agree, so the generator can say *settled*
or **not settled** rather than assume. On `slow` it returns `False`; on
every template that can be traced from a generic model point it returns
`True`.

This is the repo's own habit applied to the repo: `Model.trace`'s docstring
made a claim, and the claim is now measured rather than asserted. The claim
happens to hold for everything shipped — which is exactly why it would have
survived indefinitely without being checked.

## The finding: coverage is 80.3%, and the shortfall is concentrated

Generating documentation makes the gap visible for the first time. Across
every template the library ships — 290 variables — **233 carry a docstring,
80.3%**. The shortfall is not spread evenly:

| template | documented |
|---|---|
| `GroupLife` | 8/16 — **50.0%** |
| `Endowment`, `WholeLife`, `_TraditionalAssurance` | 8/15 — 53.3% |
| `WithProfitsEndowment` | 13/23 — 56.5% |
| `CreditLife` | 14/21 — 66.7% |
| `FixedIndexedAnnuity` | 22/29 — 75.9% |
| `UniversalLife` | 42/45 — 93.3% |
| `TermLife` | 23/24 — 95.8% |
| six others | **100%** |

Six templates are complete and the tail is the older annual-frequency
family. The test asserts a **floor** rather than the number, so adding a
template cannot break the suite and the figure can only be moved upward.

Coverage is measured statically, so it covers all fifteen templates —
including the ten whose model points need product-specific fields and which
therefore cannot be traced from a generic point. Those ten are skipped by
the generator and reported as skipped, rather than silently omitted.

## Not in scope

- **A model point per template.** Ten of the fifteen need product-specific
  fields, so the generator writes documents for five and counts docstrings
  for all fifteen. Fixing that means a fixture registry, which is a
  different piece of work and arguably belongs with the golden tests.
- **Assumption lineage.** `@var(assumption=...)` is declared, not derived,
  so the document reports what a variable *says* it reads from the basis
  rather than what it actually touched. Deriving it would need the tracer
  to record assumption access as well as variable access.
- **Rendering the graph as anything but Mermaid.** `DependencyGraph` already
  emits it and GitHub renders it; anything richer is a viewer, and the
  argument above is that a viewer is the wrong artefact.
- **§7's other two bullets** — role-based access on assumptions and the
  four-eyes approval flow — which PLAN itself marks as a later phase.
