# RFC-069: The axis that was a binding, and the invariant that was only unprovable

Status: **implemented** — `engine/core/model.py`,
`tests/test_slab_binding.py`

## Summary

The smaller half of the finding RFC-068 left on the record:

> **Three of them are one bug, and it is a shape rather than a number.**
> `GeneralInsurance` and `LongTermCare` disagree between the two executors,
> and `LongevitySwap`'s single-model-point bridge reports `False`. In all
> three the numbers are identical to the last bit.

A template that precomputes a `(n_modelpoints, n_periods)` slab in
`setup()` and reads it through `Model.at` got `slab[..., t]` back — a
`(1,)` array under the interpreted executor, where the model is bound to a
single model point and every other variable is a scalar. `record_run`'s
interpreted branch stacked those into `(1, n_t, n_mp)` against the
vectorized `(n_t, n_mp)`, and `fingerprint`, which covers shape as well as
values, reported a disagreement in which every number was identical. The
evidence pack's summary line moves from **7 of 11** templates
bitwise-identical to **9 of 11**, and `LongevitySwap`'s single-point bridge
from `False` to `True`. `PayoutAnnuity` and `PensionBuyout` — the other,
real half of RFC-068's finding — still cannot run interpreted, and their
rows still say so.

## The discriminator is the binding, never the shape

RFC-068 named two candidate placements, and stated as written, both are
wrong.

**`Model.at` returning a scalar when the block is one policy** — if "one
policy" is read off the slab — cannot work, because the shape does not
carry the information. A vectorized run over a genuine one-model-point
block produces the same `(1,)` slice, and so does every chunk of a
`chunk_size=1` run; in both, the slice is *correct*, because the axis is
the block and every other variable in that executor is a per-block array.
A squeeze keyed on `shape[0] == 1` would collapse the honest case and the
spurious one alike, and the golden suite would not notice: on a block of
one the numbers agree either way. It is the shape of fix that passes every
test and changes what a value *is*.

**`record_run` squeezing the axis it knows it introduced** fails on its
own description: `record_run` did not introduce the axis. The template's
`setup()` did — `np.atleast_1d` over scalar fields manufactures a policy
axis of length one — and the stacking in `record_run` preserves whatever
arrives. So `record_run` could only squeeze on shape, which is the same
trap one level up, and it would heal the digest while leaving
`result.per_mp` inconsistent for everything else that reads it. That
inconsistency is not hypothetical: `RunResult.aggregate` runs compensated
summation over the per-policy values and was returning `(1,)` arrays where
every other variable aggregates to a float; `GeneralInsurance`'s own
`combined_ratio()` on a directly-instantiated model returned a one-element
array; `Model.trace` and the generated model documentation evaluate a
single specimen per-policy and saw the only non-scalar values in the
projection. A digest-only fix would have attested the pack and left all of
that standing.

What actually distinguishes the executors is the **binding**, and the
model already holds it: the interpreted executor — and a direct
instantiation, and `Model.trace` — binds `mp` to a single `ModelPoint`;
the array executors bind a `ModelPointBatch` (columnized for the
stochastic one). `Model.at` already keys its scenario branch off model
state (`self.scenarios is not None`) rather than off any array's shape;
this fix extends the same principle to the model-point axis. Bound to a
batch, the axis is the block and stays. Bound to a single point, the
column is returned as the scalar it is — and the per-policy result is now
scalar-valued everywhere, which is what heals `record_run`, `aggregate`,
`combined_ratio`, and the pack in one place. `engine.core.model` gains an
import of `engine.data.modelpoints` for the `isinstance` check; that
module imports nothing but NumPy, so the core's dependency rule (§1.4) and
import graph are unchanged.

## The refusal that came with it

Once `at` knows it is bound to one policy, a slab whose leading axis
carries more than one is not a case to shape-handle — it is a slab built
for a block this model is not bound to. Reading `value[0]` and continuing
would compute every period against the first policy's row: plausible
numbers, wrong population, the exact failure family RFC-061 turned
`pool_sum`'s pool-of-one into an error over. So it is refused, naming the
population found (`"the slab carries 3 policies"`), and the refusal is
asserted in `tests/test_slab_binding.py` alongside the grants. The other
standing refusal is re-asserted rather than assumed: the fix must not
widen what the interpreted executor accepts, and `LongevitySwap` over a
block of many still raises `PooledBlockError`.

## An invariant that was never breached is different from one that was

The digest was right to disagree. `fingerprint` covers dtype and shape as
well as values, and a `results_digest` that forgave shape would be a
digest that two differently-shaped answers could share — exactly what the
registry's determinism check cannot afford. So the fix makes the shapes
agree rather than teaching the digest to forgive them, and the test
asserts the two claims *separately*: shape equality, and elementwise value
equality. The second is the historical statement — §1.2 was never broken
by these three templates, only unprovable for them — and keeping it as its
own assertion means a future regression will say which of the two things
it broke.

Bitwise stability of everything already attested is the constraint the
whole fix was chosen under. The batch branch of `at` returns the identical
object it always did, so no vectorized or stochastic number — nor any
digest over one — moves; the suite's golden `==` tests pass unedited, and
the pack's vectorized digests for the three templates are byte-identical
to the ones the failing rows carried.

## What this does not close

RFC-068's other half stands: `PayoutAnnuity` and `PensionBuyout` fail
under the interpreted executor with `TypeError: 'datetime.date' object is
not iterable` — the per-policy path cannot handle a date-valued
model-point field. That is a real limitation wanting its own decision
(either the interpreted executor learns dates, or the two templates are
declared outside the per-policy class with a stated reason), and nothing
here touches it. The pack now carries exactly those two unattested rows,
and they mean what they say.
