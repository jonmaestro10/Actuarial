# RFC-015: US GAAP for long-duration contracts (LDTI)

Status: **implemented** — `engine/report/usgaap.py`

## Summary

PLAN.md §5.3 asks for "**US STAT/GAAP-LDTI**". This is the GAAP half: ASU
2018-12, the Targeted Improvements to the Accounting for Long-Duration
Contracts, which replaced a forty-year-old model in 2023 and rebuilt three
things.

- **The liability for future policy benefits** on traditional and
  limited-payment contracts, measured with a **net premium ratio** updated
  as assumptions change rather than locked at issue.
- **Market risk benefits** — the GMxB family — pulled out of the host
  contract and measured at **fair value**.
- **Deferred acquisition costs**, amortized on a constant level basis: no
  interest, no shadow balance, and no sensitivity to profitability at all.

## Read against RFC-012, because it is the same economics twice

IFRS 17 and LDTI both start from a projection of the same contract, both
insist that writing profitable business produces no day-one profit, and both
treat a shortfall asymmetrically. The three caps in this module —
the net premium ratio at 100%, the attributed fee ratio at 100%, and the
reserve at zero — all say the same thing RFC-012's loss component says: a
contract sold too cheaply reports it immediately and cannot spread it.

Then they disagree, and the sharpest disagreement is about **time**.

## The finding: retrospective against prospective, 4.2× apart

A 25% deterioration in benefits, discovered in year 8 of a fifteen-year
cohort:

| | recognised in year 8 |
|---|---|
| **LDTI** — re-derive the net premium ratio *from the issue date*, restate the whole history, put the difference in this period's income | **1,154.92** |
| **IFRS 17** — adjust the CSM, release it over the seven years of coverage that remain | **272.46** |

Same event. Same cashflows. Same discount curve. **LDTI's hit is 4.24 times
IFRS 17's**, because one framework restates a past it has already reported
and the other only ever looks forward.

The retrospective mechanic is visible in a second way: the same change
applied at issue and applied in year 12 produces *different* ratios, because
a different amount of history has been fixed by the time it is applied.

## The finding: a rate move that changes the balance sheet and not earnings

LDTI accretes interest on the liability at the rate **locked in at issue**
and carries the balance sheet at the **current upper-medium-grade rate**,
with the whole difference in other comprehensive income.

On the illustration here, a 200 basis point fall in the current rate puts
**11.4%** of the peak reserve into AOCI while net income is unchanged to
1e-9 in every period. And it unwinds to nothing: the two curves value the
same run-off, so once there is nothing left the difference is zero.

This is the same idea as RFC-012's locked-in CSM inside a current-value
liability, arrived at from the other end and presented in a different place.

## The reconciliation earned its keep twice

**Total income equals the cohort's undiscounted net cash**, to floating
point, capped or not, with or without deferred acquisition costs. The same
discipline RFC-012 is held to, and it caught two real errors here:

- **Acquisition costs charged twice** — once when paid and again as they
  amortized. They are capitalized: cash out, an asset up, and they reach
  income *only* through amortization. `measure` no longer takes them at all,
  which makes the mistake unavailable rather than merely fixed.
- **Interest deducted after it had already been counted.** The accretion is
  inside the change in the reserve; subtracting it again double-counted it.
  It is now derived from the reserve's own roll —
  `reserve[t+1] = (reserve[t] + net premium) × (1 + i) − outflow` — and
  reported as a disclosure line rather than a deduction.
- And a third, found by the same check on an onerous cohort: **the day-one
  loss never reached income.** When the cap binds the reserve opens above
  zero, that opening balance telescopes out of the change-in-reserve line,
  and total income overstated net cash by exactly `reserve[0]`.

## DAC, and the thing that is *not* sensitive to anything

Before LDTI, DAC amortized in proportion to estimated gross profits: a
function of investment returns, unlocked every period, with a shadow balance
for unrealised gains. Now it is straight-line over an in-force driver.

**A wildly profitable cohort and a deeply onerous one amortize
identically** — asserted with `==` on the whole array. The only thing that
moves it is how fast contracts terminate, because that is how many are left
to spread over. In a module where everything else is a function of
profitability, that is worth stating plainly.

## Market risk benefits

Measured at fair value: the guarantee's expected cost less an **attributed
fee**, a fixed share of the contract's total fees solved at inception so the
benefit is exactly zero on day one — the same construction as the net
premium ratio and as the CSM.

Changes in fair value go **straight to income**, with no deferral of any
kind. That is the contrast with RFC-013's variable fee approach, where the
identical market move adjusts the CSM and reaches profit at a tenth of the
speed. An insurer writing the same variable annuity reports it two very
different ways.

The exception is the portion attributable to a change in the entity's own
credit standing, which goes to OCI so that an insurer's own distress cannot
flatter its earnings. Taken as an input rather than derived: own credit is a
market observation, not a projection.

## Not in scope

- **US statutory** — CRVM, VM-20 and VM-21. A different measurement
  basis from GAAP with its own prescribed assumptions, and the other half of
  what PLAN §5.3 asks for.
- **Experience variance**, as in RFC-012: everything here is expected
  against expected, so the remeasurement is a change in expectation rather
  than a difference between actual and expected.
- **Transition**, and the modified-retrospective and full-retrospective
  elections.
- **Cohort aggregation.** LDTI's unit of account is an issue-year cohort;
  which policies belong in which is the entity's grouping policy, and this
  module measures whatever cohort it is handed.
- **The fair value technique for market risk benefits.** The cost series is
  an input; producing it is a risk-neutral valuation, which is RFC-006's
  nested machinery over the GMxB templates.
