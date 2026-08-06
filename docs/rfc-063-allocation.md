# RFC-063: The allocation, and the promise it cannot keep

Status: **implemented** — `engine/report/vm22.py`, `tests/test_vm22.py`

## Summary

`docs/vm22-remediation-plan.md`, item V4, discharging §3.H:

> "The aggregate reserve shall be allocated to the contracts falling within
> the scope of these requirements using the method outlined in Section 13,
> with the exception of contracts valued under VM-A, VM-C, VM-M, and VM-V
> following Section 3.G which are to be calculated on a seriatim basis."

The plan said not to guess at §13 and it was right to. The method is fully
prescribed, and two of its properties are not what a sensible person would
design.

```python
records = [ContractRecord(mp.id, scenario_apv=apv, category="accumulation",
                          cash_surrender_value=csv)
           for mp, apv, csv in book]
allocation = allocate_aggregate_reserve(records, reserve.post_ceded)
allocation.amounts, allocation.rule, allocation.reconciles
```

§13.A's contract-level reserve is a **minimum allocation value** (§13.C,
one rule per Reserving Category) plus an **allocated excess reserve**
(§13.D, the group's excess over its aggregate MAV shared out in proportion
to each contract's excess Scenario APV). The excess is allocated on a risk
measure by design: the section expects "an indexed annuity contract with a
high benefit GLWB [to] typically have a larger allocated excess reserve
than an otherwise identical indexed annuity contract with a low benefit
GLWB or no GLWB".

## §13.D.1 can never reach a Payout Annuity group

§13.C.1 sets a payout contract's MAV to "the greater of … the Scenario APV
for the contract, or … the cash surrender value provided under the
contract". §13.D.1 then allocates "in proportion to the excess of the
Scenario APV over the MAV", and §13.D.2 floors that excess at zero.

For a payout contract the MAV is *defined* as at least the Scenario APV, so
the excess is zero by construction — every time, for every payout contract.
§13.D.3's "if all contracts in the group have an excess Scenario APV that
is floored at zero, then use the MAV to allocate" is therefore not a
fallback for that category; it is the operative rule, and §13.D.1 is
unreachable there.

This matters beyond being a curiosity. Reading §13.D top to bottom, D.1 is
the method and D.3 is a degenerate case, and a suite exercising only an
accumulation group would confirm that impression forever. In fact §13.D.1's
risk-proportional allocation is an **accumulation-category mechanism** —
which is consistent, because the preamble's GLWB example is an accumulation
contract, and because §13.C.2 gives that category the only MAV (the cash
surrender value alone) that leaves the Scenario APV room to stick out above
it. `allocate_aggregate_reserve` reports which rule ran, and the test that a
payout group always lands on §13.D.3 exists so the finding cannot be
quietly lost.

## §13's preamble promises what §13.D's arithmetic does not deliver

The section opens with two guarantees about its own method:

> "the reserve held for any contract will be no less than the cash
> surrender value provided under that contract, after consideration of any
> reinsurance. Additionally, the reserve held for a Payout Annuity contract
> (whether life-contingent or not) will be no less than the present value
> of the liability cash flows provided under the contract … discounted
> using the NAER"

Under §13.D.1 and §13.D.3 both hold, because the allocation only ever adds
a non-negative share to a MAV that already dominates both quantities. Under
§13.D.4 — where the group's aggregate reserve falls *short* of its
aggregate MAV — neither survives intact:

- the shortfall is spread over life-contingent contracts, and a payout
  contract absorbing it finishes **below its Scenario APV**, contradicting
  the second guarantee outright;
- the first guarantee is rescued, but by an explicit floor applied *after*
  the allocation — "All contracts are floored at their cash surrender
  value" — which means the allocated amounts **no longer sum to the
  aggregate reserve**.

And §13.C.3's longevity MAV is 2% of the next twelve months' scheduled
benefits with no "greater of" at all, so a longevity contract with a
surrender value above that finishes below the first guarantee too. Such
contracts usually have no surrender value, which is exactly why the case is
easy to miss.

The tempting fix is to add the floors the preamble implies, or to scale the
§13.D.4 result back down so it reconciles. Both would be wrong: the first
invents a reserve §13.D does not prescribe, and the second breaks the
guarantee the floor was added to keep. So `Allocation` implements the
prescribed arithmetic and **reports** — `rule`, `reconciles`,
`below_cash_surrender_value`, `below_scenario_apv`. A break that shows up in
the return value is a finding; the same break discovered in a
reconciliation three months later is an incident.

## §13.B's scenario is chosen, not supplied

> "the Scenario APV for each contract is equal to the discounted liability
> cash flows at the NAER … for the scenario that produces the aggregate
> scenario reserve for the group that is **closest to, but not greater
> than** the SR defined in Section 3.D."

That is a prescribed *selection* over quantities the module already has —
`segment_scenario_reserves` produced the aggregate scenario reserves and
their CTE is the SR — so `apv_scenario` makes it rather than taking the
Scenario APV wholly on trust. The "not greater than" half is the part an
`argmin` on absolute distance would get wrong, and there is a test with a
nearer scenario sitting just above the SR to catch exactly that. Ties take
the lowest index, so the choice does not depend on a sort order. §13.B.2's
DR case needs none of it: there the single scenario used to calculate the
reserve is the scenario.

## Separations, and the term the chapter never defines

§13 requires four separations, all in one sentence, and all refused rather
than silently pooled: contracts passing §7.A's stochastic exclusion test are
out of the allocation entirely (§3.H calculates them seriatim), DR and SR
groups allocate separately, unaggregated Reserving Categories allocate
separately, and where aggregation spans model segments each segment
allocates separately. Contracts passing §7.E's Single Scenario Test are
explicitly *in*, which is easy to get backwards — one exclusion test keeps a
contract out and the other does not.

§13.C.2's "**Account Value Based Annuity**" appears nowhere else in VM-22
and is not defined in it. The identification with the Accumulation Reserving
Category is safe rather than convenient: §13.C's three cases are §3.F.1's
three categories, and §3.F.1.c defines Accumulation as "all annuities within
scope of VM-22 that are not in the Payout … or Longevity Reinsurance"
category — which is what an account-value-based annuity is. `MAV_RULES`
records the mapping and a test pins it to `RESERVING_CATEGORIES`, so an
unclassified contract is refused: there is no MAV rule to apply, and §3.F.1's
latitude for unclassified pools has no counterpart here.

## Acceptance

`tests/test_vm22.py` — 20 tests for this item. The allocation sums to the
aggregate reserve exactly, asserted on thirds rather than round numbers; a
payout group always lands on §13.D.3; the §13.D.2 floor stops a weak
contract taking a negative share; §13.D.4's shortfall reaches only the
life-contingent contracts; and §13.B.1's selection is made against the
numbers `segment_scenario_reserves` produced rather than a second
computation of them.

The refusals: an excluded contract in the allocation, a DR contract pooled
with an SR one, unaggregated categories pooled without §3.F.2's attestation,
two model segments in one allocation, an unclassified contract, a longevity
benefit outside the longevity category, a shortfall with no life-contingent
contract to carry it, a group with no proportion to allocate on, repeated
contract ids, an empty group, an SR that cannot have come from the scenario
reserves it is offered with, and a `BasisPair` where one basis was required
— §13 allocates "for both the pre- and post-reinsurance ceded reserves", and
since §13.C's inputs are themselves "after consideration of any
reinsurance", the records differ by basis as well as the reserve.
