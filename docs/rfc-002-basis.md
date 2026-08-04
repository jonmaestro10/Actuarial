# RFC-002: Layer 0 basis — time, mortality, discounting, annuity factors

Status: **implemented** — `engine/core/dates.py`, `engine/data/mortality.py`,
`engine/data/rates.py`, `engine/library/annuities.py`

## Summary

The actuarial basis is taken from the VPLA system rather than written fresh.
Its calculations have been checked against Society of Actuaries calculators,
which makes them a specification worth adopting whole; docs/vpla-review.md
is the structural review, and this RFC records what was promoted, what was
allowed to change, and how the difference is policed.

The rule adopted for this layer:

> **Reorganise the computation, never the arithmetic.** Any change that
> moves a floating-point operation must be justified as *more* accurate and
> demonstrated to be so, or it is not made.

## What was promoted

| VPLA | Engine | Fidelity |
|---|---|---|
| `RateTable` | `YieldCurve` | Discount factors match to 1e-14 (same recursion, expressed as a cumulative product) |
| `MortalityTable.mortality_lookup` | `MortalityBasis.q` | Bitwise |
| Improvement scales, 1-D and generational | `MortalityBasis` | Bitwise |
| `MortalityTable.mortality_period` (UDD / linear, actual / 30-360) | `MortalityBasis.period_mortality` | Bitwise |
| `MortalityTable.survival_factors` | `MortalityBasis.survival_curve` | Bitwise |
| `Person.annuity_factor`, certain period | `annuities.annuity_factor` | Summation order only |
| `Person.annuity_factors` | `annuities.deferred_annuity_values` | O(n) instead of O(n²) |
| `Person.joint_life_factor` | `annuities.joint_life_factor` | Summation order only |
| `Person.joint_annuity_factor` | `annuities.reversionary_annuity_factor` | Closed form |
| `CalcEngine.calculate_annuity_factors` | `annuities.block_annuity_factors` | Whole block, one pass |

"Bitwise" is not a figure of speech. `tests/test_mortality_basis.py`
compares 408,000 individual period rates for **equality**, across the cross
product of improvement kind, UDD vs linear, actual vs 30/360 day count,
payment frequencies 1/4/12, leap-day and month-end birth dates, month-end
and 29-February valuations, and a valuation before the improvement scale's
base year.

## The calendar

Everything in layer 3 of the basis depends on `dateutil.relativedelta`:
which two ages a payment period straddles, how many days each part runs,
whether a birthday falls inside it. VPLA makes one such call per period per
policy — 1,440 for a monthly 120-year curve, each allocating date objects.

`engine/core/dates.py` reproduces the three operations that matter over
integer arrays. They are pinned against `dateutil` itself, not against a
restatement of it, over every month end, both sides of the 1900/2000
leap-century rules, 29 February birth dates, and several thousand random
dates (`tests/test_dates.py`).

Two properties were easy to get wrong and are tested explicitly:

- **`relativedelta(later, earlier).years` is not a `(month, day)` tuple
  comparison.** dateutil counts whole months and then divides, which makes
  28 February an anniversary for a 29 February birth date in a common year.
- **Month addition does not compose.** `31 Jan + 1 month + 1 month` is
  28 March; `31 Jan + 2 months` is 31 March. VPLA adds the whole offset from
  the valuation date every period, so the engine must too, or every
  month-end valuation drifts.

## Two roundings that had to be matched deliberately

Both were found by a bitwise test failing, not by inspection, and both are
now regression-tested:

1. **NumPy's vectorized `power` is not libm's scalar `pow`.** They differ in
   the last bit. The constant improvement scale is a `pow`, so the engine
   evaluates it through NumPy *scalars* — once per (sex, age, calendar
   year), cached at construction — rather than as an array expression.
2. **The generational scale must accumulate one calendar year at a time.**
   Refactoring it into a cumulative product times a power of the held-flat
   tail regroups the multiplications, and floating-point multiplication is
   not associative. The engine keeps the reference's loop order and caches
   the result per calendar year.

The caching is where the speed comes from: a projection touches ~120
distinct calendar years, and VPLA redoes the improvement accumulation on
every single rate lookup.

## Where the arithmetic was allowed to move

Two places, both reductions, both justified as more accurate and measured:

- **The reversionary annuity uses the closed form.** VPLA accumulates, per
  period, the chance the primary dies in it times the value of the
  survivor's remaining payments — an O(n²) double loop. Exchanging the order
  of summation collapses it to `ä_x + j(ä_y - ä_xy)` exactly (review §3.3).
  At monthly frequency that is ~1,440 operations instead of ~1,036,800, and
  it removes roundings rather than adding them.
- **Sums are pairwise rather than left to right.** Pairwise error grows with
  the logarithm of the term count instead of the count. Measured against
  `math.fsum` over a 720-term monthly projection: engine ~1 unit in the last
  place, VPLA up to 14.

The second is a claim about an error *bound*, so it is asserted where it is
true — on the worst and average error across a block — and not as a promise
about every individual case, which would be false.

## Deliberate departures

Three, each recorded because a silent divergence from a validated basis is
worse than a loud one:

- **Blending across sexes.** VPLA blends the base rates and then applies the
  improvement scale of whichever sex was asked for, so a supposedly unisex
  rate depends on the sex requested. The default here blends the improved
  rates; `blend="base"` reproduces the original exactly. With sex-specific
  rates — the configuration VPLA actually runs — the two are identical.
- **Ages below the table raise** instead of failing on a dict lookup.
  Extrapolating mortality downwards is never the right silent default.
  Ages *above* the table are held flat, as VPLA does.
- **Money rounding is not reproduced.** VPLA applies `np.around(..., 2)`
  inside its roll-forward. Rounding is an output policy; in a recursion it
  makes results depend on payment frequency and destroys reconciliation.
- **A survival probability stays a probability.** `period_mortality` is
  reproduced exactly, including the additive fractional-age split that
  exceeds 1 above roughly `q = 0.8` (review §6.15). `survival_curve` clips
  the per-period factor into `[0, 1]` before accumulating, so the curve
  cannot go negative and stay there. On a well-formed table the clip never
  engages, which is why every bitwise comparison in the suite still holds;
  on CPM2014 above age 115 it does.

  The clip is hygiene, not a correction. Measured on CPM2014, the survival
  it guards has already decayed to ~1e-8 by the time the split can
  overflow, and clipping moves an annuity factor by ~1e-9 relative at worst
  — below the summation-order differences already accepted. On an
  on-anniversary valuation `q = 1` drives survival to exactly zero and the
  overflow never occurs at all. **The split itself is deliberately left
  alone**: correcting it would move SOA-validated numbers for no material
  gain, and would make the parity harness stop reporting the original's
  behaviour.

The constant improvement scale extrapolates backwards for a valuation before
`year_start` while the generational scale does not — an asymmetry in the
original that is reproduced rather than tidied, since either behaviour would
otherwise be a silent change to a validated number.

## Select and ultimate — an addition, not a promotion

Everything above is VPLA's arithmetic reorganised. Select rates are the one
piece with no counterpart to promote, because VPLA values payout annuities
in payment: every life it touches passed any select period decades ago, so
an ultimate table is the whole story. Term assurance is not priced that way
— a life underwritten last year is a materially better risk than one of the
same age underwritten twenty years ago — and PLAN §5.1 lists it as a Layer 0
gap.

The basis takes `select={sex: {duration: {age: q}}}`, the published layout:
one row per **age at selection**, one column per year since selection.
Durations run `0 .. n-1` and set the select period; from `n` onwards the
ultimate table applies.

Adding a dimension to the one lookup every number in the engine passes
through is exactly the kind of change that moves a golden value by an ulp
and is never noticed. It is threaded so that it cannot:

- `duration` is an **optional argument**, not part of the age index. Omit it
  — the default at every call site that predates select rates — and
  `_table_q` returns `self._q[sex_index, clipped_age - self.min_age]`, the
  expression the class has always evaluated, character for character.
- A basis carrying select rates, asked *without* a duration, still returns
  the ultimate table. Omitting the duration is not "duration 0".
- The select branch is element-wise, so a batch mixing new business and
  seasoned policies falls through per policy rather than per array.
- Improvement is untouched: the select dimension chooses which base rate
  applies, and the scale is a function of attained age and calendar year
  either way. Same for the fractional-age split — selection picks the year
  of mortality, the split divides that year, and the two commute.

`tests/test_select_mortality.py` asserts the first two with `==` on floats
rather than `approx`, and the VPLA parity harness is unchanged: bitwise on
every rate across all five configurations at both frequencies.

Two behaviours are worth stating out loud because they are choices:

- **The date-driven path reads duration at the start of each piece.** A
  payment period can straddle both a birthday and a policy anniversary, and
  they need not be the same date. The piece before the birthday takes the
  duration at the period start; the piece after it takes the duration at the
  birthday — the same convention the age already follows, so each rate
  matches the span it actually covers.
- **A selection age outside the select table is clipped, not raised.** At
  the bottom of the table the nearest row belongs to an older attained age,
  so a clipped rate can come out *heavier* than the ultimate one. That is
  pinned by a test rather than left to be discovered. Clipping is what keeps
  the lookup total for ages a template masks out and never actually uses;
  the cure for a real block is a select table covering its selection ages.

## How it is policed

Three layers, in increasing strength:

1. `tests/vpla_reference.py` — a literal transcription of the original,
   plain Python, one `relativedelta` call per period, imported by the
   engine's tests and by nothing in the engine itself.
2. `tests/test_mortality_basis.py`, `tests/test_annuity_factors.py`,
   `tests/test_dates.py` — the engine against that transcription, and the
   transcription's calendar against `dateutil`. These run in CI.
3. `scripts/vpla_parity.py` — the engine against **a real VPLA checkout**,
   on the real CPM2014 and CPM2014B tables, plus its committed golden
   factors. Not in CI, because it needs the checkout; run it whenever the
   basis changes.

Latest parity run (25 lives, 60 years, `jonmaestro10/VPLA` at `fe8b47f`):

```
mortality rates      36,000 compared across 5 table/convention
                     configurations x 2 frequencies — 0 mismatches, bitwise
annuity factors      agree to 3.2e-14 (summation order only; the survival
                     curves going in are identical)
speed                ~830 ms per life (VPLA) vs ~0.6-2 ms (engine) for a
                     monthly 120-year factor
published goldens    72 cases: 36 agree with VPLA as it runs (0
                     disagreements), 42 reproduce the committed constant,
                     14 constants stale, 16 joint constants shadowed
```

The last two rows are findings about the checkout, not about the engine, and
the harness reports them separately for exactly that reason:

- **14 stale constants.** Twelve single-life monthly goldens no longer
  reproduce from VPLA's own code. The engine matches VPLA to the last bit on
  every one, so it is the committed constants that have drifted.
- **16 shadowed joint constants.** VPLA cannot evaluate a joint factor at
  all today — the `joint_survivor_percent` validator turns every non-zero
  percentage into `None` (review §6.1), and the guard `== 0` then lets a
  `None` through into arithmetic. Sixteen of the eighteen *monthly* joint
  constants are byte-identical to the corresponding single-life constants,
  which is the signature of having been recorded while that fall-through was
  active.

That leaves the **18 annual joint constants** as the only live evidence for
the reversionary factor, and the engine's closed form reproduces all
eighteen — a calculation VPLA itself can no longer perform.

## What this layer still does not do

Named here so the next phase has a list rather than a memory:

- ~~The `@var` executor is still annual.~~ Done: `engine/core/timeaxis.py`
  and the `setup()` hook (RFC-001) carry the basis into the projection loop,
  and `engine/library/payout_annuity.py` is the first template on it —
  monthly, on calendar dates, with joint benefits, reconciled to the Layer 0
  factor term by term.
- ~~Model points carry no second life.~~ Done: `PayoutAnnuity` takes
  `spouse_dob` / `spouse_sex` / `joint_percent`, and projects the
  reversionary benefit as a cashflow rather than only as a factor.
- ~~The pool adjustment still has no home in the DSL.~~ Done: the `@pool`
  variable kind (RFC-001) and `VariablePayoutAnnuity`, which is the full
  VPLA product — pooled adjustment, revaluation frequency, cohort mortality
  release — reconciled against the reference `valuation_step`.
- ~~Only the payout annuity is on the new basis.~~ Done, and without moving
  a golden value: `MortalityTable` is now a unisex, non-improving view over
  `MortalityBasis` rather than a second lookup, and every template reads
  mortality through `Assumptions.annual_q`. The annual templates therefore
  accept sex-distinct rates and improvement scales unchanged
  (`tests/test_one_mortality_basis.py`).
- ~~The annual templates get the table from the basis but not its
  fractional-age splits.~~ Done, for the two age-indexed templates and
  without moving a golden value: `MortalityBasis.periodic_rate` splits a
  year of age into `freq` sub-periods — the dateless counterpart, needing
  only an age — and `Assumptions` grew per-period views of every annual
  assumption, each an exact identity at `freq = 1`.

  These stay *age*-indexed rather than date-indexed on purpose. Pricing work
  has an entry age, not a date of birth; valuation work has both, and uses
  the date-driven `period_mortality` through a `TimeAxis`. Two entry points
  to one basis, not two bases.
- ~~The unit-linked family is still annual.~~ Done, and the modelling
  decision it was waiting on is recorded rather than buried: the AMC removes
  a proportion of the fund, so it converts geometrically as a **deduction**,
  `1 - (1 - amc) ** (1/freq)` — not `(1 + amc) ** (1/freq) - 1`, which is
  the conversion for a rate that accumulates and leaks 1.31 bp a year on a
  1.2% charge. Rider fees and the guaranteed withdrawal spread instead,
  being annual monetary entitlements on amounts they do not erode, and the
  GMWB ratchet keeps stepping annually because it is an anniversary event.
  `freq = 1` is the identity across all 48 output series of both templates.

  Scenario returns are **per period**, which is the engine's existing
  convention rather than a new decision: `ScenarioSet.horizon` counts
  projection periods and `run_stochastic` checks it against `proj_len`.
  Nothing converts an annual scenario file to a monthly one, because that
  would have to invent the intra-year path.
- ~~No select-and-ultimate period.~~ Done, and without moving a bit: an
  optional select table keyed by `(sex, duration, age at selection)`, with
  the duration threaded through both the age-indexed and the date-indexed
  lookups. `TermLife` reads it, since term assurance is priced on select
  rates; its model points take a `duration_in_force` for a block already
  part way through the select period.
- ~~No multi-decrement tables~~ (PLAN §5.1). Done, and again without moving
  a bit: [RFC-004](rfc-004-decrements.md). Independent rates become
  dependent ones by a stated method rather than by the order the
  multiplications were written in, and `constant_force` states directly the
  answer the frequency work was converging on.
- Select rates are **not** yet available to the pooled and payout templates
  through `ValuationBasis`. The plumbing is there — `survival(axis, dob,
  sex, entry)` takes a date of selection — but no annuity template passes
  one, because an annuity in payment has no select period to speak of.
