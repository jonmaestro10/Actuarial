# RFC-013: IFRS 17, the variable fee approach

Status: **implemented** — `engine/report/vfa.py`

## Summary

The measurement model for **direct participating contracts** — which is most
of what this engine models. A unit-linked contract with an annual management
charge, a universal-life account value, a fixed-indexed annuity: in each the
policyholder holds a share of a pool of assets and the insurer takes a fee
out of it. The insurer's profit is a **variable fee**, and it moves with the
pool.

RFC-012's general measurement model gets that wrong for these contracts, and
visibly so. Under the GMM the CSM is a historic-cost balance accreting at a
locked-in rate, and a change in fulfilment cashflows caused by a financial
variable goes straight to profit in the period it happens. For a contract
whose profit *is* a share of the market, that reports volatility the insurer
does not experience.

One change of substance fixes it: **the CSM absorbs the change in the
entity's share of the fair value of the underlying items, and the changes in
fulfilment cashflows that financial variables cause.**

## The finding: a market move stops being a market move

A 3,000 worsening from a financial variable at year 5 of a fifteen-year
group, with ten years of cover left:

| | impact on year-5 profit |
|---|---|
| general measurement model | **−3,000.00** — all of it, at once |
| variable fee approach | **−300.00** — a tenth |
| VFA with the §B115 election | −3,000.00 |

Under the VFA what reaches the year is the change times that period's
coverage-unit fraction and nothing more. Both models lose the same 3,000
over the group's life; one puts it in a year and the other spreads it over
the coverage that remains.

§B115's risk mitigation election is **one flag and not a third model**: it
sends hedged financial changes down exactly the route the GMM sends them,
which is what stops a hedge's fair value moving through profit while the
thing it hedges sits in the CSM. Asserted by equality against the GMM
figure, not by resemblance.

## The finding: the VFA's CSM is not safe

The other consequence runs the opposite way, and it is the one that
surprises.

A 65% fall in the pool at year 5 wipes out a margin of **4,122** and puts the
excess — **1,362** — through profit and loss immediately, leaving a loss
component of 1,225 to run off over the remaining coverage. RFC-012's
asymmetry, triggered by a market rather than by an estimate.

The general model, given the identical group, does not notice at all: its
CSM stays positive for the whole projection, because the pool is not one of
its inputs.

So the VFA is not simply "the smoothed one". It defers ordinary market noise
and then fails discontinuously when a fall is large enough to exhaust the
margin.

## The bug the recovery case found

A pool that recovers after a crash must extinguish the loss component
**before** it rebuilds a CSM — the asymmetry has to hold for a market
recovery exactly as it does for a favourable estimate.

The first implementation let it straight past. `csm_growth` and
`changes_in_estimate` went through separate paths, and a rising pool
rebuilt the CSM while a loss component of 1,225 was still sitting beside it.
The two are now **one rule** over a single signed adjustment, because the
loss component does not care what made a group better or worse off. Under
the GMM the unified path is arithmetically identical to the old one — the
accretion of a zero CSM is zero — and all 38 of RFC-012's tests pass
unchanged.

## No locked-in accretion

There is no `locked_in` argument. The CSM's growth *is* the entity's share
of the underlying items' return, which is a current-value number by
construction — so the historic-cost balance inside a current-value liability
that RFC-012 measured at six times the interest does not exist here at all.
Offering a curve that did nothing would be worse than not offering one, so
the argument is absent and passing it raises.

## The invariant holds

**Total profit is still the group's undiscounted net cash**, whatever the
pool does — rising, falling, crashing, recovering. The variable fee is a
re-measurement and not new money: the fee itself is already in the group's
cashflows.

That invariant caught the module's conceptual error. The first version
reported the entity's share of the pool's return as investment income *and*
deferred it into the CSM *and* released it as revenue — the same money three
times, two of which were real. Total profit missed net cash by exactly the
change in the entity's share, which named the fault immediately.

## Eligibility is recorded, not computed

§B101 is three judgements — is there a clearly identified pool, does the
entity expect to pay a substantial share of the fair value returns on it,
does a substantial proportion of the cashflows vary with it — made from the
contract terms and assessed **at inception and never again**. A contract does
not stop being a participating contract because a market fell.

A library that inferred them from a cashflow table would be guessing at the
contract, so `Eligibility` records the answers and `measure_vfa` refuses a
group that does not have all three. "Substantial" is not defined in the
standard; that is the entity's judgement and is left as one rather than
resolved by a threshold nobody wrote down.

## What is reported, and what is not

`Measurement.profit` is the **insurance result** — service result less
insurance finance expense. The investment return on the underlying items the
entity holds is an asset-side number and sits outside it, so in a period
when the pool moves sharply the line here moves with the liability and not
with the entity's bottom line. Stated because it would otherwise read as a
bottom line, and in a crash year it is emphatically not one.

## Not in scope

- **PAA**, the premium allocation approach, and its eligibility test. The
  last of the three models §5.3 names.
- **Experience variance**, as in RFC-012: everything is expected against
  expected.
- **The asset side.** An entity applying the VFA holds the underlying items,
  and a complete statement needs their return beside this. That is the ALM
  overlay, not the liability measurement.
- **Indirect participating contracts**, which fail §B101 and are measured
  under the GMM with an OCI option this module does not model.
