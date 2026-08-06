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
| 3.B | every component determined **post- and pre-reinsurance ceded** | **gap** | the module has no reinsurance dimension. See below |
| 3.C | additional SPA is disclosure-only under VM-31 | **implemented (as an omission)** | not built, and the text says it is not a floor for 2026 |
| 3.D.1 | SR excludes contracts carrying a DR or valued under VM-A/C/M/V | **partial** | `ReservingGroup` models the partition; nothing checks a contract appears in only one group |
| 3.D.2 | SR = CTE70 of the scenario reserves | **implemented** | `aggregate_stochastic_reserve` |
| 3.E | DR for groups passing the Single Scenario Test | **out of scope** | the DR is an input to `ReservingGroup`; §7.E's test is not implemented |
| **3.F.1** | **Reserving Categories may not be aggregated** | **refused** ← *new* | see below |
| **3.F.2** | payout + accumulation may combine on two criteria | **refused unless attested** ← *new* | `combined_payout_accumulation=True` |
| 3.F.3 | DR groups shall not aggregate with non-DR groups | **gap** | groups are separate objects, but nothing enforces the rule across them |
| 3.F.4 | a category may be one model segment | **implemented** | the single-group case |
| 3.F.5.a.ii | combine PVs across segments, **then** take the greatest | **known deviation** | `Σ max` vs `max Σ`; pinned |
| 4.A | project accumulated deficiencies | **implemented** | RFC-016's roll |
| 4.B.1.a | scenario reserve = starting assets − PIMR + greatest PV | **implemented** | PIMR added this pass |
| 4.B.1.a note | the greatest PV **can be negative** | **implemented** | V1: `floor_at_zero=False` for VM-22, `True` for VM-20/21 |
| 4.B.1 | scenario reserve ≥ aggregate cash surrender value | **implemented** | floor inside the CTE; corrected |
| **4.B.1** | **longevity reinsurance: ≥ 2% of next-12-months scheduled benefits** | **gap** ← *new* | see below |
| 4.B.2 | NAER discounting | **out of scope** | earned rates are an input |
| 4.C–4.E | hedging, index credits, 20% hedge margin, two-CTE70 blend | **out of scope** | no hedge modelling; the blend is documented, not built |
| 4 (length) | project until no obligations remain | **out of scope** | the caller sets the horizon |
| 5 | reinsurance | **gap** | see 3.B |
| 6 | Standard Projection Amount | **out of scope** | disclosure-only per 3.C |
| 7.A–7.B | exclusion routes: ratio test, demonstration, certification | **implemented** | all three `EXCLUSION_BASES` |
| 7.C | SERT: `(b−a)/c` < lesser of 6.0% and materiality | **implemented** | corrected this pass; `c` is the PV of benefits |
| 7.D | Stochastic Exclusion Demonstration Test | **out of scope** | a documented demonstration, not a calculation |
| 7.E | Single Scenario Test | **out of scope** | the DR is an input |
| 8 | scenario generation (VM-20 App. 1.F, 16 scenarios) | **out of scope** | prescribed dated data — belongs with F2 |
| 9 | hedge modelling | **out of scope** | as 4.C–4.E |
| 10–12 | prudent estimate assumptions, margins, mortality | **out of scope** | assumption-setting, not reserve arithmetic |
| 13 | allocation of the aggregate reserve to contracts | **gap** | `AggregateReserve` reports composition by group; nothing allocates to contracts |

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

### 3. §3.B and §5 — reinsurance *(gap)*

> "All components in the aggregate reserve shall be determined
> post-reinsurance ceded and pre-reinsurance ceded."

Two reserves, not one. The module produces a single figure and has no ceded
dimension at all. Recorded as a gap rather than a deviation, because
nothing in the module claims otherwise.

## Where this leaves the chapter

Of thirteen sections: **eight are out of scope by design** and stated as
such (scenario generation, hedge modelling, assumption-setting, the
standard projection method, the DR's own tests); **five carry reserve
arithmetic**, and those five have now been read line by line.

Remaining open against the sections the module *does* implement:

| item | direction | effort |
|---|---|---|
| §4.B.1 longevity 2% floor | understates for that category | small |
| §3.F.5.a.ii `max Σ` ordering | overstates | medium — needs deficiency paths |
| §3.B/§5 pre- and post-ceded | not comparable | medium |
| §3.F.3 DR/non-DR aggregation | understates if violated | small |
| §13 allocation to contracts | n/a | medium |

Nothing else in the five arithmetic sections disagrees with the module.
