# RFC-025: Trading to a target, and what immunisation actually costs

Status: **implemented** — `engine/data/rebalance.py`, with a `strategy`
argument added to `engine/data/assets.py`

## Summary

RFC-020 demonstrated that **matching duration does not immunise**. RFC-021
then built a real portfolio and scoped the follow-up out in as many words:

> **A trading strategy.** Sales happen only to meet cash, never to
> rebalance to a duration target — which means this module can *measure*
> RFC-020's duration gap and cannot yet close one.

This closes one, and then finds that closing it was the wrong thing to do.

## The trade is a single equation

Selling a notional `x` of a portfolio worth `A` at duration `D` and buying
`x` at duration `d` moves the whole portfolio to `D + (x/A)(d − D)`, so
hitting a target `D*` needs

    x = A · (D* − D) / (d − D)

and no search. The result is exact — `execute_trade` puts the duration on
target to 1e-10 — and the minimum notional that does it, which matters
because every trade crosses a spread.

## The finding: a target fixed at inception is not a hedge

A liability's duration **falls as it runs off**: the payments that remain
are the later ones, and the average time to them shortens. On the
twenty-five year annuity book measured here it goes 12.76 → 7.81 → 2.97
between years 0, 10 and 20.

A fund that targets the liability's duration *at inception* and holds it
therefore drifts away from the liability every year, and does so **while
trading hard to stay put**:

| year | liability duration | fixed target holds | gap |
|---|---|---|---|
| 1 | 12.25 | 12.76 | +0.52 |
| 5 | 10.24 | 12.76 | +2.52 |
| 10 | 7.81 | 12.76 | +4.96 |
| 20 | 2.97 | 12.76 | **+9.79** |

`matched_target_path` builds the moving target; `matched_target` returns
only its first entry and says so, because that first entry is exactly the
trap.

## The finding: matching duration is not matching exposure

Fix the target so it tracks the liability, and the fund hits it exactly —
gap 0.000 at every date. It is still barely hedged:

| | assets | liabilities |
|---|---|---|
| value at year 10 | 1,776.6 | 635.1 |
| duration | 7.807 | 7.807 |
| **dollar duration** | **13,871** | **4,958** |

A dollar-duration gap of **8,912**, nearly three times the liability's own.
Duration is a *time*; matching it only matches exposure when the two sides
are worth the same amount, and they never are — a fund with surplus has, by
construction, more money moving with rates than its liabilities do.

RFC-020 states the principle in passing — "`duration_gap` is value-weighted,
because the unweighted difference is the wrong number whenever the two sides
are worth different amounts" — and this is that principle as a strategy.
`SurplusTarget` solves

    D* = D_liability · (liability value / asset value)

which on the fund above is **2.79 years**, not 7.81. It collapses to plain
duration matching exactly when the fund holds no surplus, which is the one
case the naive rule is right in.

## What each policy actually buys

Surplus swing under a ±200bp parallel shift, measured at every year from 3
to 20 so the answer is not an artefact of how recently the fund happened to
trade:

| policy | worst swing | mean swing | turnover | cost at 20bp |
|---|---|---|---|---|
| never trade | 498.7 | 314.8 | 0 | — |
| duration matching (rolling) | 389.3 | **317.7** | 9,839 | 19.68 |
| **surplus matching** | 102.8 | **19.0** | 14,752 | 29.50 |
| surplus, every 5th period | **769.0** | 296.1 | 5,982 | 11.96 |
| surplus, 0.5-year band | 99.8 | 23.1 | 14,391 | 28.78 |

Three results, in order of how uncomfortable they are.

**Duration matching buys almost nothing.** Mean swing 317.7 against 314.8
for doing nothing — *worse on average* — in exchange for 9,839 of turnover.
It improves the worst case by a fifth and the average not at all. The
classic prescription, measured, is close to a pure cost.

**Surplus matching removes 94% of it.** Mean swing 19.0, for about twice the
turnover — roughly 10 basis points a year of the fund at a 20bp spread.
What is left is second order and **positive**: the surplus rises under a
shift in either direction, which is RFC-020's convexity result reappearing
as a residual rather than as the whole effect.

**A calendar does not know when the market moved.** Rebalancing every fifth
period saves 59% of the turnover and produces a worst case of 769 — *worse
than never rebalancing at all*, because between visits the fund holds
whatever the short bonds it last bought have decayed into. A no-trade band
on the same strategy holds the mean swing at 23.1 for 2.4% less turnover.
A band and a schedule are not two dials on the same instrument.

## The counterintuitive one: a wider band can trade more

Widening the band from 0 to 1.0 years **increases** turnover by 1.8%. A band
trades less *often* and each trade is larger, because the duration was
allowed to drift further before anything happened, and the two effects do
not cancel in a fixed direction. Measured, and asserted, because the obvious
guess is wrong.

## The identity still holds

RFC-021's statement of cash gains one term:

    closing book = opening + income − default loss + realised gain
                   − trading cost + net liability cashflow + shortfall

A rebalance realises a gain or loss on the part sold exactly as a forced
sale does, and the spread leaves the fund for good — so both land in the
identity rather than in a valuation, and the trading cost shows up in the
**earned rate** where an office would feel it.

The `strategy` argument is added under a branch, so a projection without one
evaluates the identical expression it always did: verified bitwise across
**2,534** arrays covering three rate environments, two ladder lengths, five
liability shapes, all three liquidation orders and both default settings.

## Not in scope

- **Convexity as a target.** This trades one instrument against one other
  and can therefore pin one derivative. Matching the second needs at least
  three, and a solve rather than a division.
- **Non-parallel shifts**, as in RFC-020. Key-rate targets are the same
  machinery on a longer vector of exposures.
- **Anything but bonds.** RFC-021's scope note stands: no equities, no
  derivatives, so the cheapest way to buy duration in practice — a swap —
  cannot be expressed here.
- **A transaction-cost model with depth.** One spread on the notional, which
  is what a dealer quotes and not what a large trade pays.
- **Optimising the policy.** The table above is a comparison of policies
  supplied, not a search over them; the band and the schedule are inputs
  because what they are worth depends on a spread the entity knows and this
  module does not.
