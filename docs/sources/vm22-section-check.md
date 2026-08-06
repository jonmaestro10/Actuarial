# VM-22, section by section

*A systematic pass over chapter VM-22 of the NAIC* Valuation Manual *(1
January 2026 edition) against `engine/report/vm22.py`. Supersedes the
keyword search recorded in [`naic-vm22-2026.md`](naic-vm22-2026.md), which
found five errors and could not say whether it had found them all.*

**Method.** The chapter was extracted from the PDF (PyMuPDF), its thirteen
sections located, and each one read for normative content — the "shall"
statements, which is where a valuation manual puts its requirements. Each
is recorded below as **implemented**, **refused** (the module stops rather
than guessing), **out of scope** (stated as such), or **gap**.

**Result: three further findings**, one of them the first that made the
reserve too *small*.

## Section by section

| § | subject | status | note |
|---|---|---|---|
| 1 | Background | n/a | no normative content |
| 2.A | Scope — non-variable annuities per VM Section II | **out of scope** | the module takes the contracts it is given; nothing classifies a contract as in or out of VM-22 |
| 2.B | Effective date 1 Jan 2026; three-year transition | **out of scope** | a valuation-date rule, not a calculation. `VM22Basis` is dated, which is the hook if it is ever needed |
| 3.A | aggregate reserve = SR + DR + formulaic, over disjoint groups | **implemented** | `AggregateReserve`. Was a maximum; corrected |
| 3.B | every component determined **post- and pre-reinsurance ceded** | **implemented** | V3: `BasisPair` everywhere a float was |
| 3.C | additional SPA is disclosure-only under VM-31 | **implemented (as an omission)** | not built, and the text says it is not a floor for 2026 |
| 3.D.1 | SR excludes contracts carrying a DR or valued under VM-A/C/M/V | **partial** | `ReservingGroup` models the partition; nothing checks a contract appears in only one group |
| 3.D.2 | SR = CTE70 of the scenario reserves | **implemented** | `aggregate_stochastic_reserve` |
| 3.E | DR for groups passing the Single Scenario Test | **out of scope** | the DR is an input to `ReservingGroup`; §7.E's test is not implemented |
| **3.F.1** | **Reserving Categories may not be aggregated** | **refused** ← *new* | see below |
| **3.F.2** | payout + accumulation may combine on two criteria | **refused unless attested** ← *new* | `combined_payout_accumulation=True` |
| 3.F.3 | DR groups shall not aggregate with non-DR groups | **implemented** | V2: `ModelSegment.carries_dr`, refused in `check_segments_aggregable` |
| 3.F.4 | a category may be one model segment | **implemented** | the single-group case |
| 3.F.5.a.ii | combine PVs across segments, **then** take the greatest | **implemented** | V2: `ModelSegment` + `segment_scenario_reserves` |
| 3.H | allocate the aggregate reserve, VM-A/C/M/V seriatim | **implemented** | V4: the carve-out is a refusal |
| 4.A | project accumulated deficiencies | **implemented** | RFC-016's roll |
| 4.B.1.a | scenario reserve = starting assets − PIMR + greatest PV | **implemented** | PIMR added this pass |
| 4.B.1.a note | the greatest PV **can be negative** | **implemented** | V1: `floor_at_zero=False` for VM-22, `True` for VM-20/21 |
| 4.B.1 | scenario reserve ≥ aggregate cash surrender value | **implemented** | floor inside the CTE; corrected |
| **4.B.1** | **longevity reinsurance: ≥ 2% of next-12-months scheduled benefits** | **implemented** | V2: `ModelSegment.floor()` |
| 4.B.2 | NAER discounting | **out of scope** | earned rates are an input |
| 4.C–4.E | hedging, index credits, 20% hedge margin, two-CTE70 blend | **out of scope** | no hedge modelling; the blend is documented, not built |
| 4 (length) | project until no obligations remain | **out of scope** | the caller sets the horizon |
| 5.A.1 | formulaic post-ceded = pre-ceded − reinsurance reserve credit | **implemented** | V3: `ReservingGroup.formulaic`, the one derived basis |
| 5.A.2.a–b | the two bases are two projections | **implemented** | V3: `stochastic_group`/`segment_group` take both |
| 5.A.2.a.iii | counterparty-default margin only where impairment is known | **out of scope** | an assumption input; explicitly *not* required otherwise |
| 5.A.2.a.iv | non-qualifying treaty: **increase** the aggregate reserve | **implemented** | V3: `non_qualifying_surplus_reduction`, added post-ceded |
| 5.A.2.b.i–ii | starting assets on the ceded portion | **out of scope** | acceptable approaches documented; an input, computed nowhere |
| 5.A.3 | the bases may differ on the exclusion test's outcome | **implemented** | V3: a method per basis on `ReservingGroup` |
| 5.A.4 | pre-ceded standard projection amount | **out of scope** | follows the SPA, disclosure-only per 3.C |
| 6 | Standard Projection Amount | **out of scope** | disclosure-only per 3.C |
| 7.A–7.B | exclusion routes: ratio test, demonstration, certification | **implemented** | all three `EXCLUSION_BASES` |
| 7.C | SERT: `(b−a)/c` < lesser of 6.0% and materiality | **implemented** | corrected this pass; `c` is the PV of benefits |
| 7.D | Stochastic Exclusion Demonstration Test | **out of scope** | a documented demonstration, not a calculation |
| 7.E | Single Scenario Test | **out of scope** | the DR is an input |
| 8 | scenario generation (VM-20 App. 1.F, 16 scenarios) | **out of scope** | prescribed dated data — belongs with F2 |
| 9 | hedge modelling | **out of scope** | as 4.C–4.E |
| 10–12 | prudent estimate assumptions, margins, mortality | **out of scope** | assumption-setting, not reserve arithmetic |
| 13.A–13.D | MAV plus allocated excess reserve | **implemented** | V4: `allocate_aggregate_reserve` |
| 13.B.1 | the scenario "closest to, but not greater than the SR" | **implemented** | V4: `apv_scenario`, a prescribed selection |
| 13.B.1.a–b | NAER by the direct iteration method | **out of scope** | earned rates are an input, as at 4.B.2 |
| 13.E | worked example | n/a | two tables the extraction does not carry |

## The three new findings

### 1. §3.F.1 — categories that may not be pooled *(fixed this pass)*

> "Groups of contracts within different Reserving Categories may not be
> aggregated together in determining the SR or DR."

Three categories: **Payout Annuity**, **Longevity Reinsurance**,
**Accumulation**. §3.F.2 permits payout + accumulation together, and only
where the company manages both in an integrated risk-management process and
within a single portfolio or portfolios sharing an ALM strategy.

The module pooled anything handed to it. **This is the first deviation
found in VM-22 that made the reserve too small** — aggregating buys
diversification, so free pooling across categories reports less than the
chapter permits. Every other deviation erred the safe way.

Now refused: `Contract.category` carries the classification and
`check_aggregable` stops a mixed pool. §3.F.2's exception is an
*attestation* — the module cannot check either criterion, so it takes the
caller's word and makes them say it. Longevity reinsurance never combines.
Unclassified contracts still aggregate freely, which keeps existing callers
working and is documented as *not a VM-22 reserve*, because nothing has
held such a pool to §3.F.1.

### 2. §4.B.1 — the longevity reinsurance floor *(gap)*

> "For the Longevity Reinsurance Reserving Category, the scenario reserve
> for any given scenario shall not be less than 2% of the scheduled
> longevity benefits payable by the benefit provider within the next 12
> months from the date of valuation."

A **second, category-specific floor**, and the module knows only the cash
surrender value one. It needs a per-category floor rule and a model-point
field for the next-twelve-months scheduled benefit. Not built; recorded.

This reinforces the finding above — the Reserving Category is not a
label, it decides which floor applies.

### 3. §3.B and §5 — reinsurance *(fixed, V3)*

> "All components in the aggregate reserve shall be determined
> post-reinsurance ceded and pre-reinsurance ceded."

Two reserves, not one. The module produced a single figure and had no ceded
dimension at all — recorded as a gap rather than a deviation, because
nothing in the module claimed otherwise.

Now built (RFC-062). §5 was read in full, and it settled the design: the two
bases are **two projections** (§5.A.2.a and §5.A.2.b), not a number and an
adjustment, so nothing derives one from the other except the formulaic
component, where §5.A.1 says to subtract the reinsurance reserve credit.
Two things the reading added that no first-principles design would have
had: §5.A.2.a.iv's **additive** charge where a non-qualifying treaty would
reduce surplus, and §5.A.3's warning that the two bases may reach different
outcomes on the exclusion test — so a group carries a method per basis.

## Where this leaves the chapter

Of thirteen sections: **six are out of scope by design** and stated as such
(scenario generation, hedge modelling, assumption-setting, the standard
projection method, the DR's own tests); **seven carry reserve arithmetic**,
and all seven have now been read line by line. §5 and §13 joined that count
when the remediation plan read them — both had been assumed to be out of
scope, and neither was.

Remaining open against the sections the module *does* implement:

| item | direction | effort |
|---|---|---|
| ~~§4.B.1 longevity 2% floor~~ | understates for that category | **done (V2)** |
| ~~§3.B/§5 pre- and post-ceded~~ | not comparable | **done (V3)** |
| ~~§13 allocation to contracts~~ | n/a | **done (V4)** |

Nothing else in the arithmetic sections disagrees with the module, and
every gap this read opened is now closed.

## What §13 added, once read

§13 was the last unread section and the plan refused to guess at it. Two
things justified that.

**§13.D.1 is unreachable for a Payout Annuity group.** §13.C.1 makes that
category's MAV the greater of the Scenario APV and the surrender value, so
the excess Scenario APV §13.D.1 allocates on is zero by construction and
§13.D.3 is the operative rule rather than a fallback.

**§13's preamble states two guarantees its own arithmetic does not always
keep** — no contract below its surrender value, no payout contract below
its Scenario APV. §13.D.4 breaks the second and rescues the first with a
floor applied after the allocation, which costs the reconciliation to the
aggregate reserve. Recorded and reported by `Allocation` rather than
patched, because the patch would break the guarantee the floor exists for.
