# RFC-011: Index crediting and the lifetime guarantee

Status: **implemented** — `engine/data/index_credit.py`,
`engine/library/fixed_indexed_annuity.py`

## Summary

PLAN.md §5.2 asks for **fixed & fixed-indexed annuities (deferred/immediate,
GLWB riders, index crediting)**. RFC-010 built the account value; this RFC
covers what makes an FIA a different product rather than a universal-life
contract with a different name — the crediting rule, and the word
**lifetime**.

Two findings came out of building it, and both are the kind that only a
projection can produce.

## An FIA credits at anniversaries and nowhere else

Between anniversaries the account does not move with the index at all. At
each anniversary it credits a rate derived from what the index did over the
year, **floored at zero**, so the account is a ratchet and every year locks
in.

That floor is why an FIA cannot be valued by averaging returns and applying a
formula at the end. A bad year costs the policyholder nothing, so the
distribution of what gets *credited* is not the distribution of what the
index *did*, and the path matters in a direction that averaging destroys.

## The finding: a 2% monthly cap is not a 24% cap

Three crediting designs, measured on **one shared index path** — the same
monthly scenarios, compounded to annual steps for the annual designs, so
what separates them is the design and not two draws of a random number
generator. 6% expected return, 18% volatility, twenty years, 600 scenarios:

| design | advertised | mean credit delivered | credits nothing |
|---|---|---|---|
| annual point-to-point, 6% cap | 6% | **3.2%** | 41% of years |
| annual point-to-point, 7% cap | 7% | 3.6% | 41% |
| annual point-to-point, 45% participation | uncapped | 4.9% (median 1.9%) | 41% |
| monthly average, 6% cap | 6% | 2.9% | 41% |
| monthly sum, 2% monthly cap | **24%** | **0.9%** | **82%** |
| monthly sum, 3% monthly cap | 36% | 2.3% | 70% |

A monthly-sum design with a 2% monthly cap quotes **four times** the headline
of a 6% annual cap and delivers **less than a third** of it, while crediting
nothing in four years out of five. Even a 3% monthly cap — 36% advertised —
loses to a 6% annual one.

The mechanism is one line of arithmetic: **the cap truncates the good months
and the bad months come through in full.** Eleven months at +3% capped to 2%
each, plus one month at −8%, credits 14% rather than the 25% the uncapped
path would have given. The annual floor cannot help until the *whole year*
is negative.

Monthly averaging is a smaller version of the same thing, and quieter: the
average of a path that ends higher than it started is below its endpoint,
always, so it credits **10% less than a point-to-point design at the
identical cap** with no change to any number the contract quotes.

## Two accumulators, not twelve reads back

Each design is written as a pair of running accumulators reset at every
anniversary, rather than as a function of the year's twelve returns. Not a
stylistic choice: the windowed forward loop from RFC-001 prunes the memo
behind a look-back window **discovered from the first three traced periods**,
and a formula reading twelve periods back has no twelve-period edge to
discover at period three. Two state variables with a one-period look-back are
correct, cheap, and the shape the executor is built for.

`index_level` runs the index forward within the year; `index_total`
accumulates whatever the design adds up — capped monthly returns for a
monthly-sum contract, index levels for an averaging one, nothing at all for a
point-to-point one.

## A monthly design cannot be run on annual scenarios

`MonthlySum` and `MonthlyAverage` declare `min_freq = 12`, and the assumption
set refuses a coarser projection **at construction** rather than at the first
anniversary. Splitting an annual scenario into twelve would have to invent
the intra-year path, and inventing volatility is not a conversion — the same
rule the unit-linked template states about scenario returns, arriving here
as a hard failure rather than a docstring.

## The finding: stopping early values the guarantee at nothing

A GLWB differs from the GMWB in `unit_linked.py` by one word — the
withdrawal is **for life** — and it is worth more than the rest of the rider
put together. `glwb_strain`, the part of the guaranteed withdrawal the
insurer funds once the account is empty, has no end date in the contract.

On a 60-year-old with a 7% ten-year roll-up drawing 5% from age 70, the
account survives a **median of 22 years**. So:

| projection cut off at | share of the lifetime guarantee captured |
|---|---|
| 20 years | **0.3%** |
| 25 years | 27% |
| 30 years | 63% |
| 35 years | 85% |
| 50 years | 100% |

**Every penny of the guarantee is in the tail** — which is exactly the part a
term-limited model throws away. Over the full run the strain is 32% of the
present value of the income promised: a third of the payments come from the
insurer rather than the policyholder's own account.

The same point in a second place: leaving a flat 5% lapse running through the
withdrawal phase cuts the cost of the guarantee by **about 60%**. A
policyholder drawing a lifetime income does not surrender it for a cash value
worth less than the income, and a model that lapses them anyway is not being
prudent — it is producing a different answer. `lapse_rate` is multiplied by
`deferring(t)`, which is the whole fix.

## The off-by-one worth a sentence

The benefit base takes its last step **on** the anniversary withdrawals
begin, not the one before it, so a ten-year roll-up against a tenth-year
start compounds ten times. Written as `deferring(t - 1)` rather than
`deferring(t)`; the difference is one factor of 1.07, which is 7% of the
guarantee for the rest of the annuitant's life.

## What the base does, and stops doing

During deferral it rolls up at the contractual rate for a stated number of
years, and **ratchets** to the account value whenever the account is higher —
which is what turns a good year into a permanently larger guarantee. Once
withdrawals start it freezes. A contract that keeps ratcheting in payment is
a different product and a different formula, not a parameter of this one.

## Switching the rider off

`glwb_rate = 0` is off, with no branch anywhere: a zero withdrawal rate
guarantees a zero withdrawal, a zero withdrawal draws nothing from the
account, and a zero base charges no fee. Asserted rather than assumed.

## Not in scope

- **Withdrawal rates banded by age at first withdrawal**, which every real
  GLWB carries. A lookup on the model point rather than a change to the
  mechanics, and it belongs beside the corridor table rather than in the
  roll-forward.
- **Hedging the crediting option.** The insurer buys a call spread to back
  an annual point-to-point cap, and the cost of that spread is what sets the
  cap each year. This module projects the liability; the asset side is the
  ALM overlay in PLAN §5.3.
- **Renewal cap setting.** Caps are declared annually and are the insurer's
  lever, so a contract's future caps are a management action rather than an
  assumption. Modelling that is a dynamic-management-action problem, and it
  is the same shape as the declared-rate problem in RFC-010.
- **Multi-index and volatility-controlled allocations**, where the account is
  split across several crediting designs. The designs here compose; the
  allocation logic is a template that holds several accounts.
- **Immediate (payout) indexed annuities.** `payout_annuity.py` is the
  payout mechanics; combining them with this crediting rule is a template,
  not new machinery.
