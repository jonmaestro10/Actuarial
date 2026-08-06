# The analysis of surplus depends on the order you peel it

**Claim.** Attributing surplus by peeling drivers off one at a time gives a
different answer for every ordering. The drivers interact, so the split is
not a property of the book alone — and a decomposition quoted without its
order sensitivity presents a *choice* as a measurement.

**Demonstrate it:** `python scripts/findings/aos_ordering.py`
**Recorded in:** [`docs/rfc-024-experience.md`](../rfc-024-experience.md)

## Why it happens

Mortality and lapse both act on the same in-force. Improving one changes what
the other is worth, so the surplus is not additive in its drivers and there
is no order-free way to split it. This is not a modelling shortcut; it is a
property of any decomposition of a non-additive function.

Three drivers give six orderings, and every driver's attributed contribution
has a **range** across them rather than a value.

## What the engine reports instead of a single number

- `sequential` reports the order it used, so the answer is never quoted
  without the choice that produced it.
- `isolated` reports its **residual** — the interaction the one-at-a-time
  view cannot place.
- `shapley` gives the unique attribution satisfying efficiency, symmetry and
  null-driver — a theorem rather than a preference, and still one choice
  among several.
- `contribution_range` gives the exact range over all `n!` orderings out of
  `2ⁿ` evaluations, because a driver's contribution depends only on the *set*
  peeled off before it and every subset is some ordering's prefix.
- `order_sensitivity` says what the ordering was worth.

## The bug this found

`Assumptions` held the lapse rate in two places — `lapse` and
`dynamic_lapse.base` — and different templates read different copies. A
driver swap that set one and not the other would run some products on the
*actual* basis and others on the *expected* basis, with nothing in any output
to show for it. `COUPLED_FIELDS` now names the groups that must move
together, in the one place a swap consults.
