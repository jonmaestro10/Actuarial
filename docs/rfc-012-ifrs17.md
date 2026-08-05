# RFC-012: IFRS 17, the general measurement model

Status: **implemented (GMM)** — `engine/report/ifrs17.py`

## Summary

PLAN.md §5.3 asks for reporting overlays *(products × frameworks)*, IFRS 17
first: "GMM/VFA/PAA: CSM roll-forward, risk adjustment, coverage units". This
RFC is the GMM — the model the other two are defined against — and it opens
Layer 2, which until now was empty.

It is the first thing in the engine that is not a projection. Everything in
`engine/library` answers *what will happen*; this answers *what does the
accounting say happened*, and they are different questions with different
right answers.

## An overlay, not a calculator

`Group.from_run` builds a group from a projection's own output series:

```python
group = Group.from_run(result, inflows=["premiums"],
                       outflows=["claims", "expenses"],
                       acquisition=result.aggregate("initial_expenses")[0])
```

Nothing in `TermLife` knows IFRS 17 exists, and nothing in this module
re-derives a cashflow. That is the whole *products × frameworks* idea: one
projection, several reporting bases over it.

## The invariant that pins everything else

**Total profit equals the group's undiscounted net cash**, to floating
point, under every combination of the standard's choices — coverage-unit
driver, coverage-unit discounting, locked-in rate, onerous or not, with or
without a risk adjustment.

Accounting moves *which period* a profit appears in. It cannot invent or
destroy one. Everything below is a statement about timing, and this is the
check that keeps it honest.

It also found the one real bug in the module. Total profit missed net cash
by exactly `acquisition × i`: the acquisition cashflow is paid at initial
recognition, so it is out of the door before the first period accretes, and
leaving it in the balance that unwinds financed it for a period it was never
outstanding. The size and sign of the error identified it immediately.

## No profit at inception, and the asymmetry underneath

The CSM exists to stop profit being recognised when a contract is written:
whatever a group is worth on day one goes into the CSM and is released as
service is provided. The liability at initial recognition is therefore
exactly nil — the three blocks are constructed to cancel.

That construction runs one way only. A group whose fulfilment cashflows and
risk adjustment exceed the premium is **onerous**, and there is no negative
CSM to hold the loss: it goes to profit and loss, in full, on day one.

A later improvement does not reverse the same way. It must first extinguish
the loss component, and only the surplus becomes a CSM to be released over
the remaining coverage. Both directions are asserted rather than described:
an adverse change eats the CSM before it creates a loss; a favourable one
clears the loss component before it rebuilds a CSM.

Amortising the loss component reduces insurance revenue and insurance
service expenses by the same amount and cannot touch the service result —
the loss was recognised when it arose, and earning it a second time through
revenue would double count it.

The loss amortises on **its own basis** — each period's service expenses as
a share of all that remain — not on the coverage units that release the CSM.
The first implementation used the coverage units, capped by each period's
outflows, and a post-merge review found what that does when claims and
coverage part company: a group whose claims land early froze the unamortised
remainder the day its outflows stopped, carrying 70% of the loss component
forever inside a fulfilment-cashflow balance of zero. On its own basis the
loss telescopes to nothing with the last service expense. Profit was never
affected — the allocation nets out of the service result — which is exactly
why the reconciliation invariant could not catch this one and a targeted
probe was needed.

One strand remains, and it is stated rather than silent: a day-one loss
larger than *every* service expense the group will ever incur can only come
from acquisition cashflows, whose recovery is B125's separate revenue
gross-up — see the scope note below. The allocation takes everything it
lawfully can and the residue equals the un-allocatable excess.

## The finding: grouping moves more than a year's profit

IFRS 17 requires contracts to be grouped by profitability, and the effect is
larger than most of what the standard says about measurement.

The same business, written on the same day, with identical lifetime cash —
a well-priced cohort and a badly-priced one:

| | year-1 insurance service result | lifetime profit |
|---|---|---|
| split by profitability (required) | **−1,392.74** | 5,200.00 |
| measured as one group | **+311.35** | 5,200.00 |

A swing of **1,704** — larger than any single year's profit on the combined
view — from where the line between groups is drawn. The lifetime figures
agree to 3.6e-12. Nothing real has changed.

## The finding: the coverage-unit choice moves a quarter of the margin

The standard says the CSM is released in proportion to "the quantity of
benefits provided and the expected coverage duration" and leaves the reading
open. On a decreasing-term group:

| coverage units | share of the CSM released in the first 5 years | first year |
|---|---|---|
| policy count | 25.3% | 5.1% |
| policy count, future units discounted | 33.1% | 7.2% |
| sum assured in force | 43.2% | 9.6% |
| expected claims | 43.2% | 9.6% |

Identical opening CSM, identical lifetime profit, and **43% against 25%** in
the first five years. Discounting the future units — the other choice the
standard permits either way — is worth another 8 points on its own, and it
points one way: later units count for less, so more of the margin is
released early.

(Sum assured and expected claims coincide here because the test's claims are
proportional to sum assured. On a product where they are not, they separate.)

## The locked-in rate

The CSM accretes at the rate that applied when the group was recognised and
never at today's, while the fulfilment cashflows are discounted at today's.
The two curves drift apart for the whole life of the group, on purpose: the
CSM is a historic-cost balance inside a current-value liability.

With rates down from 5% to 1%, the same opening CSM picks up **six times**
the interest it would at today's rate. Total profit does not move by a
penny, because every bit of that is a transfer between the insurance service
result and the insurance finance line.

`locked_in` defaults to `current`, which is what it *is* at initial
recognition — so a group measured on the day it was written needs one curve,
and supplying two is how a later reporting date is expressed.

## What the risk adjustment is here

IFRS 17 says what a risk adjustment *is* — compensation for bearing
non-financial risk — and pointedly does not say how to calculate it. A
library shipping one method as "the" risk adjustment would be wrong for
every entity that chose another, so this takes the **answer**: a total
amount and a driver saying how it runs off. `percent_of` is the common
simple parameterisation and the thing a confidence-level technique
calibrates *to*.

It carries no unwind of discount, because it is an amount allocated by a
driver rather than a discounted balance. An entity that discounts its risk
adjustment supplies the discounted series and gets the same treatment the
cashflows get.

## Not in scope

Each of these is its own RFC rather than a flag on this one:

- **VFA**, the variable fee approach, for direct participating contracts —
  where changes in the entity's share of the fair value of underlying items
  adjust the CSM rather than passing through profit. It is the right
  measurement model for the unit-linked and account-value templates, which
  makes it the natural next one.
- **PAA**, the premium allocation approach, and its eligibility test.
- **Experience variance.** Everything here is expected against expected, so
  revenue and expenses cancel on claims and the service result is exactly
  the two margins unwinding. Splitting actual from expected is what turns
  this into a reporting run rather than a projection of one.
- **Reinsurance contracts held**, which are measured separately and have
  their own rules about loss-recovery components.
- **Transition** — full retrospective, modified retrospective, fair value.
- **Subsequent recognition of new contracts into an existing group**, which
  needs a weighted-average locked-in rate.
- **B125's acquisition-cost gross-up** — the portion of premium that
  recovers acquisition cashflows, recognised in revenue with the same amount
  in expenses. Its absence means an acquisition-driven loss component larger
  than all future service expenses cannot fully amortise here; the residue
  is bounded, tested, and harmless to profit, but a group with that shape
  wants the gross-up modelled first.
