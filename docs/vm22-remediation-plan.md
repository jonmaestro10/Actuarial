# VM-22 remediation plan

*Four items closing what `docs/sources/vm22-section-check.md` left open
after the section-by-section read. Ordered so each is independently
shippable and so the cheap, low-risk one lands before the one that moves an
abstraction. Same conventions as the execution plan's §1 — RFC first, one
commit per item, tests asserting refusals as well as grants.*

## Ordering, and why

| item | § | direction of the error | effort | depends on |
|---|---|---|---|---|
| ~~**V1** unfloored greatest present value~~ | 4.B.1.a note | overstates | S | **done** |
| ~~**V2** model segments, aggregation order, category floors~~ | 3.F.5.a.ii, 4.B.1, 3.F.3 | overstates (order), understates (floor) | M | **done** |
| ~~**V3** the ceded basis~~ | 3.B, 5 | not comparable | M | **done** |
| **V4** allocation to contracts | 13 | n/a | M | V2 |

V1 first because it is small, independent, and touches shared code — doing
it while the reasoning is fresh is cheaper than doing it after V2 has moved
things around. V2 next because V3 and V4 both need its abstraction.

---

## V1 — the greatest present value can be negative — **done**

**The text.** §4.B.1.a, guidance note: "The greatest present value of
accumulated deficiencies **can be negative**."

**What is here.** `engine/report/pbr.py`'s
`greatest_present_value_of_accumulated_deficiency` floors at zero, on
RFC-016's reasoning that "a *surplus* is not a negative reserve". That is
right for VM-20 and VM-21 as this repo reads them and wrong for VM-22.

**Why it is not simply a bug fix.** The function is shared by three
chapters. Removing the floor outright would revalue VM-20 and VM-21 on the
strength of VM-22's text, which is exactly the error this plan exists to
stop repeating.

**Build.** A keyword-only `floor_at_zero: bool = True` on the shared
function and on `scenario_reserves`. VM-22 passes `False`; every existing
caller is bit-for-bit unchanged because the default is the current
behaviour. `Contract.from_cashflows` gains the same flag, defaulting
**`False`** — VM-22's own default, and the one place the two chapters part.

**Accept.** A well-funded scenario reserves *less* than its starting assets
under VM-22 and exactly its starting assets under the floored path; the
existing pbr suite is untouched and still green; a test asserts the two
paths differ on a block that never goes underwater, so the flag cannot
quietly stop mattering.

**Risk.** Low. The one thing to watch is that a negative greatest present
value can drag an aggregate scenario reserve below the cash surrender
value — which is precisely why §4.B.1's floor exists and is applied after.

**Outcome.** Shipped. `floor_at_zero` is keyword-only on both shared
functions, defaulting `True`; `Contract.from_cashflows` defaults it
`False`. The measured difference on a funded block that never goes
underwater is the whole surplus: 500 of starting assets reserves 500
floored and **0** unfloored. VM-20 and VM-21 are bit-for-bit unmoved, which
is asserted rather than assumed.

---

## V2 — model segments, and the order of the reduction — **done**

**The text.** §3.F.4–5. The reserve "may be determined in aggregate across
various groups of contracts within each Reserving Category … as a single
**model segment**", and where there is more than one segment: "Project the
accumulated deficiencies … for each model segment. **Combine the present
values** for each model segment and **take the greatest present value in
aggregate** for each scenario."

**What is here.** `Contract` carries a *reduced* scenario reserve — the
greatest present value already taken, per contract — and aggregation sums
those. That computes `Σ max` where the chapter asks for `max Σ`, and it
overstates.

**The design decision, and why the memory objection dissolves.** Fixing the
order needs the discounted deficiency *path* — `(t, scenario)` — rather than
one number per scenario. Per contract that is impossible: sixty periods by
ten thousand scenarios by a hundred thousand policies is not a thing to
hold. But **the chapter does not aggregate contracts, it aggregates model
segments**, and a segment is already a pooled block. One path per segment
is a handful of arrays.

That the memory problem disappears when the module adopts the text's own
vocabulary is the strongest available signal that `ModelSegment` is the
right cut — and it is the same lesson as every other finding in this
chapter: the structure the text describes is load-bearing, and paraphrasing
it is where the errors came from.

**Build.**
- `ModelSegment`: a Reserving Category, a discounted deficiency path, its
  starting assets, its PIMR, and the cash surrender value of the contracts
  in it. Built from cashflows the way `Contract` is today.
- `aggregate_stochastic_reserve` over segments: sum the paths, take the
  greatest present value of the aggregate per scenario, add the summed
  starting assets, subtract the aggregate PIMR, floor per scenario, then
  CTE 70 — §3.F.5.a in order.
- `Contract` stays, for the cash-surrender floor and for the single-segment
  case, and its docstring says which of the two orderings it gives.
- **§4.B.1's category floors** move onto the segment, because that is where
  the Reserving Category lives: the cash surrender value floor generally,
  and for longevity reinsurance the greater of that and **2% of the
  scheduled longevity benefits payable within the next 12 months**.
- **§3.F.3**: a segment carrying a DR may not be aggregated with one that
  does not. Same shape as the §3.F.1 refusal already shipped.

**Accept.** The `Σ max` / `max Σ` deviation test flips from *pinned* to
*fixed* and asserts the prescribed 100 rather than the module's 150; a
longevity segment reserves at least its 2% floor; a DR/non-DR mix is
refused; the existing category refusals still hold; and a single segment
containing one contract reproduces today's number exactly, so the change is
visible only where it should be.

**Risk.** Medium. It is an API change — `Contract`-based callers keep
working, but the *correct* path becomes the segment one, and the RFC has to
say plainly that a `Contract`-only aggregation is the overstating order.

**Outcome.** Shipped. `ModelSegment` carries the discounted deficiency path
and `segment_scenario_reserves` performs §3.F.5.a in the prescribed order —
combine, take the greatest, add assets, subtract PIMR, floor, then CTE 70.
Two segments each peaking at 100 on different dates reserve **100**
combined-first against 200 reduced-first: a whole segment's worth. One
segment reproduces the `Contract` figure to 1e-12, so the change is visible
only where the orders genuinely differ. §4.B.1's longevity floor and
§3.F.3's DR rule both landed on the segment, as predicted — they were
blocked on the same abstraction.

---

## V3 — every component, both ceded and not — **done**

**The text.** §3.B: "All components in the aggregate reserve shall be
determined **post-reinsurance ceded and pre-reinsurance ceded** as outlined
in Section 5."

**What is here.** One reserve. No ceded dimension anywhere in the module.

### §5, now read (the plan said not to start without it)

The earlier note that §5 was "177 characters in the extraction" was wrong —
that was a cross-reference match, not the section. §5 is **8,853
characters** and has been read. It changes this item's design in one
important way and adds a term nobody would have guessed.

**The two bases are two projections, not a number and an adjustment.**
§5.A.2.a: the post-ceded DR/SR are "determined reflecting the effects of
reinsurance treaties … including, where appropriate, all projected
reinsurance premiums or other costs and all reinsurance recoveries … using
prudent estimate assumptions". §5.A.2.b: the pre-ceded DR/SR are determined
"ignoring the effects of reinsurance ceded within the projections". So the
module cannot derive one basis from the other for the stochastic and
deterministic components — it needs **two sets of segments**, and the
public shape becomes a pair.

**The formulaic component is the exception**, and it *is* an adjustment.
§5.A.1: "for the reserve amount valued using requirements in VM-A, VM-C,
VM-M, and VM-V, the post-reinsurance ceded reserve is determined by
subtracting the reinsurance reserve credit."

**A term that has to be added, not netted.** §5.A.2.a.iv: where a treaty
does not qualify for credit for reinsurance but treating it as if it did
"would result in a reduction to the company's surplus, then the company
shall increase the aggregate reserve by the absolute value of such
reductions in surplus." That is an additive charge on the aggregate
reserve, not a change to a projection.

**Counterparty default margin, conditionally.** §5.A.2.a.iii: a margin for
counterparty default is required only where "the company has knowledge that
a counterparty is financially impaired", and is explicitly *not* required
otherwise. An assumption input, not engine arithmetic — but worth recording
because the natural instinct is to charge one always.

**Build.**
- `ModelSegment` gains no reinsurance field; instead the reserve takes two
  segment sets, `gross` and `ceded`, and `AggregateReserve` reports a pair.
- `ReservingGroup` for the formulaic method carries both, with post-ceded
  derived by subtracting a stated reinsurance reserve credit.
- A `non_qualifying_surplus_reduction` term added to the aggregate reserve
  per §5.A.2.a.iv, refused as negative.
- Starting assets on the ceded portion (§5.A.2.b.i–ii) are an **input**;
  the module documents the acceptable approaches and computes none of them.

**Accept.** Both bases reported for every component; the formulaic
adjustment asserted against a stated credit; the §5.A.2.a.iv charge
asserted to increase rather than net; a treaty-free block reports the same
number on both bases, so the pair collapses where it should.

**Risk.** Medium-high — it doubles every output and changes the reserve's
public shape. Worth doing deliberately rather than at the end of a session.

**Outcome.** Shipped, RFC-062. `BasisPair` is the shape everywhere a float
used to be, and `AggregateReserve.value` is that pair; `.post_ceded` is the
held reserve. `stochastic_group` and the new `segment_group` take a second
projection rather than deriving one basis from the other, and a caller who
supplies none has said the group cedes nothing — the pair collapses, which
is the acceptance criterion and not a fallback. `ReservingGroup.formulaic`
is the single place one basis is computed from the other, per §5.A.1, and it
takes the **pre**-ceded amount because §5.A.3 says that is what the VM-A/C/M/V
methodology produces.

The reading turned up one thing this plan had not carried, and it changed
the design: **§5.A.3 lets the two bases disagree about the method** — a
group can pass the exclusion test pre-ceded and fail it post-ceded. So a
group carries a method *per basis*, not one method and two numbers, and
`by_method()` splits the bases independently. Without that, a group would be
reported under a method it was not valued by on one of its two bases.

§5.A.2.a.iv's charge lands post-ceded only, on the reading that a basis
defined as ignoring reinsurance ceded cannot also carry a reinsurance
charge. The text does not name a basis; the reading is stated in the module
docstring rather than inferred from the arithmetic.

---

## V4 — allocation of the aggregate reserve to contracts

**The text.** §13, "Allocation of Aggregate Reserves to the Contract
Level", with §3.H requiring it and carving out the VM-A/C/M/V contracts,
"which are to be calculated on a seriatim basis".

**What is here.** `AggregateReserve` reports composition by group. Nothing
allocates down to a contract.

**Build.** Read §13 first — 11,361 characters of it, none of it yet read.
The allocation method is prescribed and this plan does not guess at it.

**Accept.** Allocated amounts sum to the aggregate reserve exactly; the
seriatim carve-out is respected; the method matches §13 with quotations.

**Risk.** Low technically, unknown until §13 is read.

---

## What this plan does not cover

The sections the section-check marked out of scope by design — scenario
generation, hedge modelling, assumption-setting, the standard projection
method, the deterministic reserve's own tests. Those are not gaps against
the module's claims; the module says it does not do them. Building any of
them is new scope, not remediation.
