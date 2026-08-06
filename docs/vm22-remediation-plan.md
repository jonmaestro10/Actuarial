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
| **V2** model segments, aggregation order, category floors | 3.F.5.a.ii, 4.B.1, 3.F.3 | overstates (order), understates (floor) | M | — |
| **V3** the ceded basis | 3.B, 5 | not comparable | M | V2 (segments are what get two bases) |
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

## V2 — model segments, and the order of the reduction

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

---

## V3 — every component, both ceded and not

**The text.** §3.B: "All components in the aggregate reserve shall be
determined **post-reinsurance ceded and pre-reinsurance ceded** as outlined
in Section 5."

**What is here.** One reserve. No ceded dimension anywhere in the module.

**Build.** A segment carries both cashflow sets; `AggregateReserve` reports
a pair rather than a scalar, and `to_dict` grows both. The interesting
design question — and the reason this is its own item rather than a field
on V2 — is whether the *exclusion tests* and the *floors* apply to the
gross or the net basis, which §5 has to be read for before anything is
built. **Do not start V3 without reading §5**; it is 177 characters in the
extraction, which almost certainly means the extraction lost it and the
real section has to be found in the PDF.

**Accept.** Both bases reported for every component; the RFC states which
basis each floor and each test applies to, with the section reference.

**Risk.** Medium-high, because it doubles every output and touches the
reserve's public shape.

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
