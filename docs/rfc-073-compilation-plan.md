# RFC-073: A plan a kernel can be wrong against

Status: **implemented** — `engine/core/compiled.py`,
`tests/test_compiled_plan.py`, `engine/core/bitwise.py`

**B1's first brick. It does not emit a kernel**, and that is the design
decision rather than the limitation.

## Summary

`engine/core/graph.py` already traces the `@var` graph, sorts it
topologically, and says exactly where it stops:

> It does **not** fuse anything, and calling it kernel fusion would be a
> claim the code cannot support.

RFC-072 then established what a kernel is *allowed* to contain. What was
missing between them is the answer to the question an emitter has to answer
before it writes a line:

> Which of this model's arithmetic can go inside a kernel, which values must
> be computed by NumPy and handed in, and — if the answer is none of it —
> which operation is responsible?

`plan()` answers it, per model, and returns a `CompilationPlan`.

## Why a plan and not a kernel

A plan is checkable against the model it describes. A kernel is only
checkable against its own output.

That asymmetry is the whole argument. If the emitter had come first, its
mistakes would surface as numbers that disagree with the vectorized executor
somewhere in a forty-year projection, and the debugging would start from a
diff rather than from a cause. With the plan first, the emitter has something
to be wrong *against*: a declared list of the operations it must fuse and the
values it must be handed, per variable, in topological order.

It is also the shape B1's own assessment asked for — "an array-expression
tape recorded off the vectorized executor" — with the classification RFC-072
supplied.

## Traced by running, for the same reason the graph is

The operations are recorded while the model evaluates, not parsed out of its
source. `graph.py` already made this argument and it applies unchanged: a
static scan of `TermLife.pols_if` sees a helper method, and running it sees
`q_x` and `lapse_rate` being read through two layers of assumption object.

The tracer carries **real values** alongside the record, so the model
computes what it normally would and takes the branches it would normally
take. A test asserts that tracing changes no number, because a plan that
described a model nobody runs would be worse than no plan.

## Where a trace ends is a hoist, not a failure

The first version of this tracer treated `np.asarray` as a trace break and
reported it as a refusal, which made 18 of 19 templates look uncompilable.
That reading was wrong, and the correction is the useful part: a `@var` body
that reaches a mortality table calls `np.asarray` on the way in, and the
array that comes back is ordinary data. **That is precisely the hoist
boundary** — a per-period gather the kernel should be handed — rather than
evidence the model cannot be compiled.

Recorded as `Hoist(reason="gather")`, distinct from
`Hoist(reason="operation")`, because they mean different things to an
emitter: an operation hoist is one ufunc whose result is a per-period array,
and a gather left the traced world entirely.

## The verdict, and the refusal that was information

Across the fourteen deterministic specimens: **13 plan cleanly.**

The exception was `GeneralInsurance`, refused because it uses `atleast_1d`,
which nothing had classified. The tempting fix is to add it to the
correctly-rounded set and move on. The right one was to notice that RFC-072's
three categories were missing a fourth.

**Selection and reshaping perform no arithmetic.** A kernel may contain them,
but not because IEEE-754 pins their rounding — it pins nothing about them;
they simply do not round, because a value that is copied is the value. That
is a different licence from `CORRECTLY_ROUNDED`'s and it deserves its own
name, `STRUCTURAL`, because the emitter treats them differently: a reshape is
resolved when the loop is laid out and never appears in the loop body at all.

The evidence that the set should exist was already sitting in the module.
`where` had been listed under `CORRECTLY_ROUNDED` with a comment reading
"Not an arithmetic operation: a data-movement select" — a category error
annotated rather than fixed. It has moved.

One boundary inside the new set is worth stating: `argmax` is structural,
because it moves an index; `amax` is a **reduction**, because it picks a
value out of an axis. Both are asserted.

## The refusal that makes RFC-070 unrepeatable

A `@var` body must not branch on model-point data. If one does, the recorded
tape is specialised to *this batch's values*, and a kernel built from it is
right for the trace and wrong for the next block.

That is not hypothetical. RFC-070's bug was a `setup()` branch conditional on
`np.any(joint > 0)` — so a batch with no survivor benefit never entered it,
and the defect survived RFC-068 and RFC-069 being written *about* it. A
tracer that silently specialised on the traced batch would reintroduce the
same failure with a compiler behind it.

So `_Traced.__bool__` and `__iter__` record a refusal naming the variable,
and the plan reports it rather than proceeding.

## Acceptance

`tests/test_compiled_plan.py` — 7 tests, plus one added to
`tests/test_bitwise_boundary.py` for the new category.

The plan's shape is asserted rather than only its verdict: every declared
variable appears in the topological order, a variable never precedes what it
reads, every fused operation classifies as `exact` or `structural`, and **no**
fused operation classifies as `hoist` or `reduce` — which is RFC-072's rule
stated as a property of the output rather than as an intention.

The refusals: a reduction (naming the variable and why no length is safe), an
unclassified operation (naming what to do about it), and a branch on traced
data (naming RFC-070). The catalogue verdict is asserted with a floor, and
with the check that no plan is vacuously compilable by fusing nothing.

## What is still ahead for B1

The emitter (tape → Numba kernel over preallocated slabs), the hoist slabs
themselves, a kernel cache keyed by the graph digest, and joining the
dual-executor equivalence suite as a third member. The acceptance criteria
for those are unchanged and now have a specification to meet: RFC-072's
arithmetic rule, and this module's per-model plan.
