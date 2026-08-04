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

The constant improvement scale extrapolates backwards for a valuation before
`year_start` while the generational scale does not — an asymmetry in the
original that is reproduced rather than tidied, since either behaviour would
otherwise be a silent change to a validated number.

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

- The `@var` executor is still annual. `MortalityBasis` and `YieldCurve` are
  frequency-aware, but nothing wires a monthly time axis into the projection
  loop; that is the next piece of work, and the templates depend on it.
- Model points carry no second life, so joint benefits are available as
  factors but not yet as projected cashflows.
- The pool adjustment still has no home in the DSL (review §7.1).
- Mortality is unisex-or-blended; no select-and-ultimate period, and no
  multi-decrement tables.
