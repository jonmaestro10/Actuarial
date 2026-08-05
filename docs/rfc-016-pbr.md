# RFC-016: US statutory principle-based reserves (VM-20 / VM-21)

Status: **implemented** — `engine/report/pbr.py`

## Summary

PLAN.md §5.3's "**US STAT**" and "**VM-20/VM-21** (VA/annuity reserves —
pairs with the VA library)". RFC-015 did the GAAP half; this is the
statutory one, and it is a third kind of overlay again.

- RFC-012's IFRS 17 **reads** a projection.
- RFC-014's Solvency II **re-runs** one on a shocked basis.
- A principle-based reserve **reduces a distribution** of them. The answer
  is not a number the projection produced; it is a statistic over a
  thousand of them, and *which statistic* is the whole design.

## The finding: a percentile can report no reserve at all

`CTE(70)` is the average of the worst 30%, not the 70th percentile. The two
are routinely conflated, and on a real block the difference is not a matter
of degree.

A guarantee that bites in fewer than 30% of scenarios puts the 70th
percentile at **exactly zero** — value at risk says hold nothing — while the
CTE over the same distribution says hold **21,298**. A percentile is a point
and knows nothing about what lies beyond it; a CTE is the mean of everything
beyond.

Where the percentile does bite, it still understates badly: on a
heavier guarantee the CTE is **6.3 times** the 70th percentile of the same
scenario reserves.

And a CTE moves when the tail moves. Multiply the single worst scenario by
ten and the percentile does not notice at all.

## The finding: value at risk is not coherent, and a CTE is

The mathematical reason the standard prescribes a CTE, demonstrated rather
than cited.

Two independent bonds, each defaulting with probability 4% for a loss of
100. At the 95% level neither alone shows any requirement — it pays 96% of
the time. Put them together and at least one defaults 7.84% of the time, so
the 95% point *is* a default:

| | A | B | A + B |
|---|---|---|---|
| VaR(95%) | 0.00 | 0.00 | **100.00** |
| CTE(95%) | 79.62 | 80.45 | 103.00 |

Value at risk says nothing, nothing, and one hundred: **the requirement
appears out of diversification**, which is the wrong direction for a risk
measure to move. The CTE is subadditive here and at every level tested, so
splitting a book in two and adding the reserves can never produce less than
reserving it whole.

## The bug: the prescribed tail was one scenario too deep on every run

`1 - 0.70` is `0.30000000000000004`. So `n * (1 - level)` lands a hair
**above** the integer at every round scenario count, and a naive ceiling
takes one scenario too many:

| scenarios | `n * (1 - 0.70)` | naive `ceil` | correct |
|---|---|---|---|
| 1,000 | 300.00000000000006 | 301 | 300 |
| 10,000 | 3000.0000000000005 | 3001 | 3000 |

It fires at exactly the counts practitioners run, on every single
valuation, and it is **invisible in the answer** — one extra scenario moves
a CTE only slightly, so nothing looks wrong. `tail_count` now snaps to an
exact integer before the ceiling, while leaving a genuinely fractional tail
alone: 1,001 scenarios still give 300.3 and still round up to 301.

Found by printing the tail count during a smoke test rather than by any
assertion, which is the argument for printing intermediate values.

## The per-scenario number is a *greatest* present value

Each scenario contributes the largest present value, over every date in the
projection, of the amount by which accumulated assets have gone negative.

The word "greatest" is the whole mechanic. A path that dips underwater in
year 12 and recovers by year 30 still needed the money in year 12, and a
terminal measure would report nothing for it. Measured on the VA block
here, **more than 60% of the paths that need a reserve at all have their
greatest deficiency before the end**.

Discounting runs along each scenario's **own** path. The deficiency and its
discounting are two halves of one path, and using a valuation rate for one
and a scenario rate for the other would price a scenario that does not
exist.

## Sampling error is about the tail, not the run

A CTE(70) over 1,000 scenarios is an average of **300**, so precision
improves like `1 / sqrt(n * (1 - level))`. Quadrupling the run halves the
error — asserted directly at 1,000, 4,000 and 16,000 scenarios — and no run
size rescues a level so deep that the tail is a handful of paths.

`tail_standard_error` returns infinity for a tail of one rather than zero.
One observation has no standard error, and reporting zero would say the
opposite of the truth.

## The three-way maximum

VM-20's reserve is the greatest of a formulaic net premium reserve, a
deterministic reserve, and the stochastic reserve. So **improving the
component that is not binding changes nothing at all** — asserted by
dropping the stochastic reserve by an order of magnitude and watching the
answer stay put. `binding` and `headroom()` report which is which, because
"our stochastic reserve fell" is a claim worth checking against this before
it is worth acting on.

A reserve with every component excluded raises rather than returning zero: a
missing calculation is not a nil requirement.

## Not in scope

- **Prescribed assumption sets** — VM-20's mortality tables with margins,
  the prescribed lapse and expense bases, and the credibility procedure for
  blending company experience.
- **The exclusion tests** (SET, DET, SERT), which decide *which* of the
  three components a block may omit. They are a projection each, and the
  shape RFC-014's stress machinery already has.
- **VM-21's Standard Projection Amount**, in either the CTE-with-prescribed-
  assumptions or company-specific-market-path form.
- **The asset model.** Starting assets and earned rates are inputs here.
  A real principle-based reserve models the assets backing the block,
  including reinvestment and defaults, and that is the ALM overlay.
- **Aggregation and the reinvestment of the tail.** Scenario reserves are
  computed per model-point set as handed over; which contracts aggregate
  before the CTE is taken changes the answer, and by the subadditivity
  above it can only reduce it.
