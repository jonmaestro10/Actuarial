# RFC-062: Two reserves, and the charge that is not a netting

Status: **implemented** — `engine/report/vm22.py`, `tests/test_vm22.py`

## Summary

`docs/vm22-remediation-plan.md`, item V3, discharging §3.B:

> "All components in the aggregate reserve shall be determined
> **post-reinsurance ceded and pre-reinsurance ceded** as outlined in
> Section 5."

The module produced one figure and had no ceded dimension. It now reports a
`BasisPair` everywhere a number used to be, and `AggregateReserve.value` is
that pair rather than a float.

```python
group = segment_group("annuity", ceded_segments,
                      pre_ceded_segments=gross_segments)
reserve = AggregateReserve([group, formulaic_group],
                           non_qualifying_surplus_reduction=75_000.0)
reserve.post_ceded, reserve.pre_ceded    # → the held reserve, and §5.A.2.b's
```

## The pair is two valuations, not two numbers

The plan warned that "the design it specifies is not the one anybody would
guess", and this is the part it meant. The obvious design is a reserve and a
ceded adjustment — one projection, one subtraction. §5 does not permit it.
§5.A.2.a determines the post-ceded DR/SR "reflecting the effects of
reinsurance treaties … including, where appropriate, all projected
reinsurance premiums or other costs and all reinsurance recoveries … using
prudent estimate assumptions"; §5.A.2.b determines the pre-ceded ones
"ignoring the effects of reinsurance ceded **within the projections**".
Those are two runs of the model, and no arithmetic on the first produces the
second. So `stochastic_group` and `segment_group` take a second set of
contracts or segments, and where the caller supplies none the pair collapses
— which is a statement about the block, not a default: a group that cedes
nothing genuinely has one number.

The exception proves the shape. §5.A.1: "for the reserve amount valued using
requirements in VM-A, VM-C, VM-M, and VM-V, the post-reinsurance ceded
reserve is determined by subtracting the reinsurance reserve credit", with
§5.A.3 supplying the direction — that methodology "produces reserves on a
pre-reinsurance ceded basis". `ReservingGroup.formulaic` is the one place in
the module where one basis is computed from the other, and it takes the
**pre**-ceded amount because that is the one the method produces. A credit
larger than the reserve it relieves is refused rather than reported as a
negative statutory reserve.

## §5.A.2.a.iv adds; it does not net

> "If a reinsurance agreement or amendment does not qualify for credit for
> reinsurance but treating the reinsurance agreement or amendment as if it
> did so qualify would result in a reduction to the company's surplus, then
> the company shall **increase the aggregate reserve by the absolute value
> of such reductions in surplus**."

This is the term the plan predicted no first-principles design would
contain, and it is worth being precise about why it exists. §5.A.2.a.iv's
first sentence keeps a non-qualifying treaty's cash flows **out** of the
projections. That omission is safe when the treaty is worth something and
flattering when it is not — a treaty that would have cost the company
surplus is one whose absence understates the reserve. The charge puts back
exactly what the omission removed, which is why it is additive and why it
attaches to the aggregate reserve rather than to any projection.

`AggregateReserve` takes it as `non_qualifying_surplus_reduction`, refuses a
negative one, and applies it **post-ceded only**. The text says only
"increase the aggregate reserve" and does not name a basis; the reading here
is that a basis defined by §5.A.2.b as ignoring reinsurance ceded cannot
also carry a reinsurance charge. That is a reading, and it is stated in the
module docstring rather than left to be inferred from the arithmetic.

The charge has no §3.A method to belong to, so `by_method()` sums to
`group_total` and not to `value`. That gap is asserted, because an
unexplained gap between a total and its split is what a reconciliation
spends an afternoon on.

## §5.A.3: the bases can disagree about the method

The reading turned up one more thing the plan had not carried:

> "It is possible that the pre-reinsurance-ceded reserves would pass the
> relevant exclusion test (and allow the use of VM-A, VM-C, VM-M, and VM-V
> or a DR, respectively) while the post-reinsurance-ceded reserves might
> not, or vice versa."

A group can therefore be formulaic pre-ceded and stochastic post-ceded. That
settles a question the pair design would otherwise have got wrong: if
`ReservingGroup` carried one `method` and two amounts, `by_method()` would
report a group under a method it was not valued by on one of the two bases.
So the group carries a method **per basis** — `method`/`exclusion` describe
the post-ceded valuation, the balance-sheet one, and `pre_ceded_method`
overrides where §5.A.3 bites. §7's rule that a non-stochastic component
states its reason is then enforced on each basis independently; a pre-ceded
formulaic valuation with no exclusion behind it is the same silent omission
the post-ceded check already refused.

## What is not here, and stays the actuary's

**The starting assets on the ceded portion** (§5.A.2.b.i–ii). The text gives
acceptable approaches — assets similar to those supporting the retained
portion, scaling up each retained asset, or modelling an identifiable
portfolio where a funds-withheld, modified-coinsurance or trust arrangement
has one, with over-collateralisation limited — and choosing among them is a
modelling decision made before the projection reaches this module. It is an
input to the pre-ceded run, and the module computes none of the approaches.

**§5.A.2.a.iii's counterparty-default margin.** Required *only* where "the
company has knowledge that a counterparty is financially impaired", and
explicitly not required otherwise. Charging one always is the natural
instinct and is not what the text says, so it is recorded here and left as
an assumption input.

**§5.A.4's pre-ceded standard projection amount**, which follows the SPA
itself in being disclosure-only for 2026 (§3.C) and unbuilt.

## Acceptance

`tests/test_vm22.py` — 13 tests for this item. A treaty-free block reports
one number twice and the pair collapses; a ceded and a gross projection give
two different figures and the module reports what each computed rather than
scaling one; the segment path carries both bases, because §3.F.5.a's order
and §3.B's pair are independent requirements and a module with one and not
the other satisfies neither section; the formulaic credit is subtracted from
the pre-ceded amount per §5.A.1; §5.A.2.a.iv's charge is asserted to
*increase* the post-ceded reserve and to leave the pre-ceded one alone.

The refusals are asserted as hard as the arithmetic: a reinsurance credit
larger than the reserve it relieves, a negative credit, a negative surplus
charge, a pre-ceded method with no exclusion behind it, a pre-ceded
exclusion with no method to attach to, and an unknown pre-ceded method. The
sign of `ceded_credit` is deliberately unconstrained and there is a test
saying so — a treaty that costs more than it recovers is the case
§5.A.2.a.iv legislates for, and a module asserting `pre ≥ post` would refuse
it.
