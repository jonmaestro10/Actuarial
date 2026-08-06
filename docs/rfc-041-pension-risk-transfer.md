# RFC-041: The scheme as one policy, and the hedge as one number

Status: **implemented** — `engine/library/pension_buyout.py`,
`engine/library/longevity_swap.py`, `tests/test_pension_risk_transfer.py`

## Summary

Execution plan §10, item C3:

> `engine/library/pension_buyout.py` (buy-in/buy-out on the payout-annuity
> chassis, joint-life, deferred members) and
> `engine/library/longevity_swap.py` (fixed-leg vs floating-leg on a
> survival index). Golden tests with closed-form joint-life annuity values.

```python
premium = run_vectorized(PensionBuyout, scheme, basis, 60,
                         outputs=["payments", "v"])
swap = run_vectorized(LongevitySwap, scheme,
                      LongevitySwapBasis(projection=best_estimate,
                                         fixed=contracted), 60,
                      outputs=["net_settlement"])
```

## The equivalence classes, settled before the code

The item's own note asked for this to be decided rather than discovered, and
the two templates land in **two different classes** — which is the useful
outcome, because it shows the classification is a property of the product
rather than of the file it lives in.

**`PensionBuyout` is vectorized-only**, the class `PayoutAnnuity`
established. It is on the `ValuationBasis` chassis because the members whose
value dominates a scheme are the *deferreds* — people twenty years from
retirement, whose value moves on the improvement scale and the fractional-age
split the annual-step templates do not carry. Writing it on annual steps and
a flat table to buy membership of §1.2's bitwise class would have been
choosing the test over the product. The RFC states the class and the tests
assert against it, exactly as §1.2 requires and RFC-061 did for the pooled
pair.

**`LongevitySwap` is in RFC-061's block class**, and not by chassis. It
declares a `@pool`, so `run` refuses a block of more than one with
`PooledBlockError` and the vectorized executor stops chunking. That is
asserted, not described — a hedge silently struck over a pool of one is
precisely the failure RFC-061 exists to have stopped, and a test that only
ran the happy path would pass against a swap that hedged one member and
reported it as a scheme.

Both consequences the note asked to be priced in have landed. Neither
template can carry an `EXAMPLES` entry, so neither reaches the evidence
pack's specimen set; both are in `UNAVAILABLE` with the reason, and
`engine/api/examples.py` now says plainly that the whole basis chassis is
invisible to the specimen set and that closing it means teaching the request
schema to express an assumption object — a schema change, not a library one.
Half the catalogue is now in that position, which is a better argument for
making the change than any of the individual rows.

## Why the swap's settlement is a `@pool` even without feedback

The obvious objection: a vanilla swap has no feedback loop, so nothing forces
the reduction inside the time loop, and per-member cashflows summed by the
caller would give the same number.

They would — until somebody forgets, or sums the wrong subset, or runs the
block in two chunks. The settlement is **one payment on one contract covering
the whole membership**; a per-member figure is not a smaller version of it but
a share of it, and a share only means anything once the total exists. Putting
the reduction where nothing enforces it is the shape of error RFC-061 found in
`GroupLife`: a pooled quantity evaluated per policy produced a complete set of
numbers computed against a scheme of one, with nothing in the output to say
so. `@pool` is what makes the block the unit, and `PooledBlockError` is what
makes it not merely conventional.

An index-based swap settling per life against an external published survival
index would *not* be pooled. That is a different contract, and it would be a
different template rather than a flag on this one — a flag would put two
equivalence classes in one file, which is the thing §1.2 exists to prevent.

## Two rates, and where they stop

A pension carries **revaluation** in deferment and **escalation** in payment,
and they must not overlap: a member revalued for ten years and then escalated
is not one compounding both throughout, and the gap grows with the term.

Both compound over **completed years**. `(1 + rate) ** years` with a
fractional exponent is the natural vectorized expression and it pays a member
three days past their anniversary three days' worth of a rise, which no scheme
does. On a monthly axis the pension is flat for eleven months and then steps,
and the test asserts the *flat stretch* as well as the step — a smooth curve
passes through the same anniversary values, so checking only the anniversaries
would not tell the two apart.

## The spouse does not wait for a retirement that never came

The member's own pension is gated by deferment, so gating the reversionary
term the same way is the obvious move — and it values the spouse of a member
who dies at 50 at nothing for twelve years. A deferred member's death leaves a
spouse's pension payable immediately, so the reversionary term is ungated and
its amount tracks the same revaluation-then-escalation path the member's
pension would have followed.

That amount has to be a function of `t` alone, which is what lets the closed
form `ₖp_y (1 − ₖp_x)` stand. A spouse's pension escalating from the *date of
death* would depend on when the death occurred and needs a convolution, not a
product; that is a different benefit and it is stated as a limit rather than
approximated silently.

## Buy-in and buy-out price the same benefits

`contract` records which transaction this is and changes no number. A buy-in
is an asset of the scheme, a buy-out discharges the obligation to the member;
the benefit cashflows the insurer prices are identical, and what differs is
whose balance sheet holds the policy and which residual risks stay behind —
data, expenses, covenant. None of those is a projection term, and a template
that quietly moved a cashflow when the flag changed would be asserting an
actuarial difference that does not exist.

Since it changes no number, a typo in it would never show up in the output —
so an unrecognised value is refused. That is the only defence a field with no
arithmetic behind it has.

## Acceptance

`tests/test_pension_risk_transfer.py` — 21 tests. The buy-out is anchored on
Layer 0 rather than restating it: a pensioner reproduces
`reversionary_annuity_factor` and a deferred member reproduces
`deferred_annuity_values` at the retirement period, at both annual and monthly
frequency, both of which are bitwise-parity with VPLA. Deferment is asserted
to be *cheaper* than the same pension in payment, because an off-by-one in the
gate produces a plausible number either way. A scheme priced as a batch equals
its members priced singly, since this template has no pooled term.

The swap: struck on its own projection basis it settles at **exactly** zero in
every period — both legs being one formula on one survival curve, any residue
would be a term one leg has and the other does not. The first period settles at
zero on any pair of bases, because everyone alive at the valuation date is
alive at the valuation date and the first payment is certain. The sign is
asserted in both directions, since a sign error leaves every magnitude
plausible. And the floating leg over a book equals `PensionBuyout`'s total
payments over the same book, period by period — two templates, one number,
because a hedge against the wrong cashflow is worse than no hedge.

The refusals: `run` over a block for a pooled model, an unrecognised
`contract`, a negative deferment, and two legs at different frequencies.
