# RFC-070: The exclusion that was a bug, and the dtype it was hiding

Status: **implemented** — `engine/data/modelpoints.py`, `engine/core/runner.py`,
`engine/library/payout_annuity.py`, `engine/library/pension_buyout.py`,
`engine/library/longevity_swap.py`, `tests/test_spouse_binding.py`

## Summary

The last two rows the evidence pack could not attest. RFC-068 named them and
RFC-069 left them standing:

> **Two of them are a real limitation, and still are.** `PayoutAnnuity` and
> `PensionBuyout` fail under the interpreted executor with
> `TypeError: 'datetime.date' object is not iterable` — the per-policy path
> cannot handle a date-valued model-point field. That is a genuine gap in
> the per-policy class's coverage and wants its own decision: either the
> interpreted executor learns dates, or the two templates are declared
> outside the class the way pooled ones are, with a stated reason.

The decision was to keep the class broad. It turned out to cost six lines,
because the premise of the question was wrong.

The pack's equivalence section now reads **11 of 11 templates
bitwise-identical**, with no unexplained rows for the first time.

## The premise was wrong, and the wrongness is the finding

The interpreted executor handles dates perfectly well. It never touched one.
What failed was three templates' `setup()`:

```python
zip(self.mp.spouse_dob, self.mp.dob, joint)
```

Model-point fields zipped **directly**. Over a `ModelPointBatch` those are
object arrays and the zip is fine; bound to a lone `ModelPoint` they are a
bare `date` and a bare `str`, and iterating a `date` raises. Dates were
incidental — `self.mp.sex` fails on the same line for the same reason, and
would have produced the identical bug in a template with no dates in it at
all.

It survived because it is **conditional**. The branch is only entered when
`np.any(joint > 0.0)`, so a policy with no survivor benefit never reaches
it. `PayoutAnnuity`'s A1 and A2 have run correctly under the interpreted
executor for as long as the template has existed; only A3, with its 60%
reversion, failed. A per-template smoke test that took the first model point
would have been green throughout.

**The repo already had the right idiom and had applied it once.**
`general_insurance.py` wraps its object-valued field before zipping it:

```python
patterns = np.atleast_1d(np.asarray(getattr(self.mp, "earning_pattern", "uniform"), dtype=object))
```

and every *numeric* field in all three spouse templates already got the same
treatment through a local `_field` helper. Only the object-valued ones were
left bare — in three separate modules, each with its own copy of a helper
that covered the numeric case and not the other.

That is why the fix is not three inline wrappers. `per_policy_field` now
lives in `engine/data/modelpoints.py`, the module that owns model-point
access, and the three local `_field` helpers are names for it. One function
rather than two, with `dtype` keyword-only and no safe default for the
non-numeric case: a caller cannot reach for the numeric one out of habit
without seeing that the other exists. Three copies of a helper is how this
bug happened, and a fourth spouse-bearing template would have repeated it.

## An exclusion asserted by a docstring and by nothing else

Both templates' module docstrings *stated* the exclusion as a design
decision. `pension_buyout.py` was explicit:

> **Vectorized and stochastic only**, like every template on this chassis
> and for the same reason: the interpreted executor evaluates one model
> point at a time, which is the per-policy loop the basis exists to avoid.
> This is a stated class, not an omission — execution plan §1.2's bitwise
> equivalence rule applies to the annual-step templates, and this one is not
> in that group.

Every clause of that is defensible and the conclusion is false. The
per-policy loop *is* what the basis exists to avoid — for **speed**. RFC-041
read a performance argument as a correctness one and wrote an executor class
out of it. Nothing about a `ValuationBasis` prevents evaluating one policy
at a time; what prevented it was a missing `np.atleast_1d`.

The evidence pack disagreed with the docstring the whole time — it placed
both templates *in* the class and tried them, and reported the failure. The
docstring said "outside the class"; the pack said "in the class and failing".
Only one of those can be true, and the one backed by a run was right.

**An executor class asserted by prose and by nothing else is not asserted.**
RFC-061 and RFC-068 both got this right by construction — a pooled template
*raises* `PooledBlockError`, a scenario-bound one cannot be handed `None` —
so their classes are enforced rather than described. This one was described.
The lesson is not "write better docstrings"; it is that a class boundary
wants a mechanism, and where there is no mechanism the claim should be a
test.

## The dtype underneath, which only became visible once the exception went

Removing the `TypeError` exposed a second disagreement on `PayoutAnnuity`
that had been hiding behind it: the block digests still differed, with every
requested output equal.

`age` was `int64` under the interpreted executor and `float64` under the
vectorized one. `engine/core/vector.py` stores into a `float64` slab and
coerces every value on the way in, so any variable whose formula returns an
integer — an attained age, a duration count — is a float by the time a
caller sees it. `engine/core/runner.py` kept whatever the formula produced.

Equal numbers, different dtype, different `results_digest`. That is
**RFC-069's failure mode one layer down**: the two executors disagreeing
about a *contract* rather than about arithmetic, with the invariant intact
and unprovable. RFC-069 found it in the array's shape; this is the same
thing in its dtype, and the two were stacked on the same template, which is
why neither was visible until the other moved.

Coerced in `runner.py` rather than in `Model.series`, for the reason
RFC-069 gives about placement: `vector.py` states its contract in the
executor, so the interpreted executor should state the same contract in the
same place. `Model.series` stays honest about what the formulas actually
return, which is what `trace` and modeldoc read.

Worth noting what this does *not* claim. The coercion is lossless here
because the values genuinely are whole numbers — asserted, not assumed
(`np.array_equal(got, np.round(got))`). A template whose formula returned a
non-numeric value could not run under the array executors at all, so there
is nothing this narrows.

## What is now true, and the one thing to watch

Every template the evidence pack places in §1.2's per-policy class is
bitwise-identical across both deterministic executors. The section carries
no unexplained row. The three classes are:

- **per-policy** — 11 templates, interpreted ≡ vectorized, bitwise;
- **block** (RFC-061) — 3, excluded by a raised `PooledBlockError`;
- **scenario** (RFC-068) — 5, excluded by needing a `ScenarioSet`.

Both exclusions are *enforced by a mechanism*. That is now the standard, and
this RFC is the argument for it: the one class boundary that rested on prose
was the one that was wrong for as long as it existed.

The thing to watch is the shape of all three bugs — RFC-069's spurious
axis, this RFC's missing axis, and the dtype. Every one is a `setup()` or an
executor written for the batch case and run against the single-policy one,
and every one produced *equal numbers with an unequal contract*. That
failure mode is invisible to any test that compares values, which is most of
them. `tests/test_spouse_binding.py` and `tests/test_slab_binding.py` both
assert shape, dtype and value **separately** for that reason: a test that
had only compared values would have been green through every one of these.
