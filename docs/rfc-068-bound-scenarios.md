# RFC-068: The scenario set, and the two things a seed does not pin

Status: **implemented** — `engine/api/catalogue.py`, `engine/api/examples.py`,
`engine/report/evidence.py`, `tests/test_api_scenarios.py`,
`tests/test_api_demo.py`

## Summary

The last four templates with no worked example. RFC-066 closed the
valuation-basis and transition-matrix gaps and left one reason standing, in
`engine/api/examples.py`:

> What is left is **one** reason rather than three: `UnitLinkedGMDB`,
> `UnitLinkedGMxB` and `VariablePayoutAnnuity` need a bound scenario set,
> and `FixedIndexedAnnuity` an index-crediting rule that itself reads index
> returns from one.

```json
"assumptions": {
  "mortality": {…}, "lapse": 0.05, "interest": 0.035, "glwb_fee": 0.0105,
  "index_credit": {"kind": "AnnualPointToPoint", "cap": 0.06,
                   "participation": 1.0, "spread": 0.0, "floor": 0.0}
},
"scenarios": {"kind": "lognormal", "n_scenarios": 64, "horizon": 46,
              "drift": 0.03922071, "vol": 0.18, "seed": 20260101}
```

Eighteen specimens, up from fourteen — nineteen once C6 (RFC-055) landed on
the same key. `UNAVAILABLE` is empty for the first time since RFC-032 wrote
it, and is kept empty rather than deleted.

## Whether the original reasoning still applies, which was the question

`engine/api/catalogue.py` declined to invent this format three times, on the
grounds that "a format invented here would be wrong the moment any of those
classes changed". RFC-066 narrowed that argument twice — for `ValuationBasis`
and then for `TransitionMatrix` — on the observation that a limit which
*grows with the codebase* is a different thing from a fixed one.

The question is about the class, not about the schema, and it has three
parts.

**Is the shape settled?** `engine/data/scenarios.py` has not been touched
since `e1b157a` — the windowed forward loop, PR #17, which is where
`ValuationBasis` also comes from. `engine/data/index_credit.py` has not been
touched since PR #23. Fifty-odd merges have gone past both. They are as
settled as the two classes RFC-066 carried, and settled for longer than one
of them.

**What do the four templates actually bind?** One rectangle each. Every
scenario-reading template in the library — `unit_linked`, `universal_life`,
`fixed_indexed_annuity`, `variable_payout_annuity`, `with_profits` — reads
`self.scenarios.ret(t)`, the primary series, and nothing else. Not one calls
`at(name, t)`. So the thing that has to be expressible is a single series of
returns; the multi-series axis is carried anyway, because `ScenarioSet` has
it and dropping it would make the schema quietly narrower than the class,
but no template needs it today.

**Is the plumbing there?** Entirely.
`engine/core/registry.py:record_run` already takes `scenarios`, already
resolves `executor="auto"` to `"stochastic"` when they are present, and
already fingerprints them; `RunRecord` already carries `scenarios_digest`,
`n_scenarios` and `scenario_horizon`. The run side was built in RFC-005. The
only thing missing was the request side.

So it goes in. What did *not* go in is
`engine/data/account.py`'s `AccountBasis`, and that refusal now has a reason
of its own rather than standing under the general heading: it is not one
settled class but five, and a request key spelled `account` carrying only
the surrender-charge schedule would name the whole basis and mean a fifth of
one. The `FixedIndexedAnnuity` specimen therefore has no surrender charge,
and its note says so — a cash value equal to the account value is a thing a
reader should be told, not left to infer.

## A basis is a kind, a field is a field — and a scenario set is neither

RFC-066 left a rule behind for the next object: `ValuationBasis` replaces the
whole assumptions object and got a `kind`; `TransitionMatrix` sits on the
ordinary `Assumptions` next to `interest` and got `assumptions.transitions`.

The two things this RFC carries land on opposite sides of it, and the rule
that decides each is *where the engine already puts them*.

`IndexCredit` is a constructor argument of `Assumptions`, alongside
`interest` and `transitions`. So it is a field: `assumptions.index_credit`.
It needs no discriminator of the schema's invention either, because
`IndexCredit.__fingerprint__` already publishes `{"kind": type(self).__name__,
…}` — the request says the name the class already says about itself, and the
three designs are *discovered* from the module the way `catalogue()`
discovers templates. A fourth design would be exposed by existing.

A scenario set is not on `Assumptions` at all. `record_run` takes it as a
sibling of `assumptions`, and `RunRecord` carries `scenarios_digest` *beside*
`assumptions_digest` rather than inside it. So it is a **top-level request
key**, next to `modelpoints`. Filing it under `assumptions` would have been a
category error the run record already refuses to make — and it would have
merged two digests the registry deliberately keeps apart, which is what a
reconciliation reads when it needs to say whether the assumptions or the
economics moved.

## Two identities, and only one of them is over the numbers

This is the part that made the format worth thinking about rather than
worth typing.

`ScenarioSet.__fingerprint__` covers the values and the primary name.
`RunStore.identify` covers the *request*. For an explicit set those are the
same question asked twice. For a generated one they are not: the request
holds `{"kind": "lognormal", …, "seed": 20260101}`, which is a **recipe**,
and the recipe is only as stable as the generator.

NumPy's own compatibility policy freezes the stream of the legacy
`RandomState` and explicitly does not freeze `Generator` — `default_rng`
methods may produce a different stream in a feature release. So a request
that names a seed can mean different numbers under a different NumPy while
the request digest, and therefore `RunStore`'s idempotency collision, stays
exactly the same.

Three consequences, and all three are load-bearing:

1. The identity that is safe to cite for a generated set is the run record's
   `scenarios_digest`, which is over the values. The request digest is over
   the recipe. They answer different questions and the run record is the one
   a reconciliation should quote.
2. `tests/test_api_scenarios.py` pins the digest of the exact set the worked
   examples build. A NumPy upgrade that moved the stream would otherwise
   revalue five templates with every other test in the suite still green —
   the request is unchanged, the shapes are unchanged, and *nothing else in
   the repository looks at the numbers*. This is the RFC-066 move applied to
   a different failure: assert the fingerprint, because a type check passes
   against a default that quietly changed the tables underneath.
3. `kind` has **no default**, which is where this schema differs from
   `assumptions.kind`. RFC-066 defaulted to `"scalar"` because every request
   written before the union existed already meant that, and the default was
   load-bearing precisely for preserving it. No request ever carried a
   scenario set, so there is no prior meaning to preserve — and since the
   three kinds are identified differently, a default would be the schema
   choosing an identity on the caller's behalf.

`source` is deliberately not carried for the same reason read backwards. It
is outside `ScenarioSet.__fingerprint__` on purpose — two sets holding the
same numbers are the same set whatever file they came from — so admitting it
would let two requests with different digests build one run.

## An index is not a return, and the schema can be told which

`engine/data/esg.py` already knows the worst silent failure in this area: a
generator that publishes a cumulative total-return index rather than
per-period returns, fed to a template that compounds it. It is a factor of a
hundred and it passes every check `ScenarioSet` has, because 100.0 is a
perfectly legal return of 9,900%.

The explicit form therefore takes `values_are`, `"return"` by default, and
`"index"` converts through `esg.returns_from_index` — which refuses to guess
the level at time zero rather than defaulting it. The schema adds no
validation of its own; it hands the numbers to the function that already
knows.

It is spelled `values_are` and not `kind` because `kind` is this object's
union discriminator. `esg` calls the same distinction `kind` and has no
union to collide with. One word answering two questions in the same object
is how a schema starts lying about which one it answered.

## A third executor equivalence class

§1.2 of the execution plan named one class; RFC-061 amended it to two —
per-policy and block. A scenario-bound template is in neither, and for a
blunter reason than pooling:

- **per-policy class** — interpreted ≡ vectorized ≡ compiled, bitwise;
- **block class** (RFC-061) — a `@pool` variable reduces across the
  model-point axis, and the interpreted executor sees one policy at a time,
  so it is excluded *by construction*; the bridge is a **pool of one**,
  where the reduction is the same either way;
- **scenario class** (this RFC) — a template that reads `self.scenarios`
  cannot be handed `None`, and both deterministic executors hand it exactly
  that. It runs under the stochastic executor and under no other. The bridge
  is a **set of one**: `ScenarioSet.single(s)` run alone must reproduce
  column `s` of the slab, bitwise.

That bridge is not new — it was already the test idiom in four modules
(`test_stochastic.py`, `test_gmxb.py`, `test_windowed.py`,
`test_variable_payout_annuity.py`). What is new is naming it as the class's
defining claim and having the evidence pack *perform* it. Before this,
`engine/report/evidence.py` ran every specimen under the two deterministic
executors, so a scenario-bound specimen would have landed in the pack as
`error: ValueError`, which reads as a broken engine rather than as a
template outside the class. The section now reports
`bitwise_on_one_scenario` beside `bitwise_on_one_modelpoint`.

The pack samples eight scenarios rather than all of them, and **says how
many**: the claim is a property of the formulas, so it holds everywhere or
fails on the first column, but a bounded check that reads as an exhaustive
one is the overclaim the pack exists to avoid. `tests/test_api_scenarios.py`
checks every scenario of every scenario-bound specimen; the pack, which runs
on every build, checks eight and reports the denominator.

`VariablePayoutAnnuity` is the specimen that makes the bridge non-trivial.
It is in *both* the block class and the scenario class — it reduces across
the model-point axis every period *and* reads a scenario return — so the
assertion is precisely that the pooled reduction does not reach across the
scenario axis as well. A `pool_sum` that swept the slab instead of the block
would break here and nowhere else in the pack.

## What the four specimens are

Not a calibration. Nothing here is anybody's assumption basis; the claims
made for these numbers are that they parse, that they run, and that they
exercise the template's shape.

- **`UnitLinkedGMDB`** — 64 lognormal paths at 18% vol against a
  return-of-premium death guarantee. The guarantee costs nothing on most
  paths and everything on a few, which is why it is priced against a
  distribution rather than a projection.
- **`UnitLinkedGMxB`** — the same chassis with death, maturity and
  withdrawal guarantees all switched on, so the three fees and the three
  strains read side by side. All three strains are non-zero, which is the
  point of turning them all on.
- **`FixedIndexedAnnuity`** — annual point-to-point crediting at a 6% cap,
  floored at zero. Withdrawals start in year ten and run for life, so the
  projection goes to age 105 rather than to a term: the account is exhausted
  on every path by the fortieth year and the GLWB strain runs on after it,
  which is the number the rider is priced on. `MonthlyAverage` and
  `MonthlySum` are expressible and are not used, because they need a monthly
  projection and the specimen would be twelve times the size to show a
  distinction that `tests/test_fixed_indexed.py` already measures.
- **`VariablePayoutAnnuity`** — each member's account set to their own
  liability at outset, so the pool starts balanced and every later adjustment
  is experience rather than an opening mismatch. Risk-neutral against the
  basis's own 4%.

The generated set's `drift` is `round(log(1.04), 8)`, which makes it
risk-neutral against that 4% to eight places and **not** exactly. The
rounding is for readability — these are shown as JSON in a form — and it
costs the martingale property in the eighth decimal. Stated in
`engine/api/examples.py` beside the constant, because a reconciliation that
misses by 3e-9 should find the reason written down rather than have to
derive it.

## What this does not close

The evidence pack's equivalence section carried five templates it could not
attest, from before this RFC. Reading them — which is what adding a fourth
kind of row to that section forced — they were **two** findings, not five.

**Three of them were one bug, and it was a shape rather than a number** —
`GeneralInsurance`, `LongTermCare`, and `LongevitySwap`'s
single-model-point bridge, in all three of which the numbers were identical
to the last bit and only the array shape differed: a `setup()` slab read
through `Model.at` gave a `(1,)` array under the interpreted executor and a
scalar everywhere else. That half is discharged by **RFC-069**, which keys
`Model.at` off the model's *binding* — a single model point against a
`ModelPointBatch` — rather than off any array's shape, and says why both of
the placements this section originally named were, as stated, wrong.

**Two of them are a real limitation, and still are.** `PayoutAnnuity` and
`PensionBuyout` fail under the interpreted executor with
`TypeError: 'datetime.date' object is not iterable` — the per-policy path
cannot handle a date-valued model-point field. That is a genuine gap in the
per-policy class's coverage and wants its own decision: either the
interpreted executor learns dates, or the two templates are declared outside
the class the way pooled ones are, with a stated reason.

Neither was caused by this RFC and neither was fixed by it. They were
recorded here because §1.2's invariant is load-bearing enough that five
unexplained reds in the repo's own evidence pack should not sit unnamed —
and naming them was what shrank the fix: "three of these five are one shape
bug" turned out to be one RFC's worth of work, not five templates' worth.
