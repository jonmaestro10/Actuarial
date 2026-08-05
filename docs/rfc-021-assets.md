# RFC-021: The asset side — earned rates, defaults, and forced sales

Status: **implemented** — `engine/data/assets.py`

## Summary

Five RFCs stopped at the same edge, and RFC-020 named it in its own
*Not in scope*:

> **A real asset model.** Cashflows are supplied; there is no projection of
> reinvestment, defaults, or trading. That is the piece the four RFCs above
> actually want, and it is larger than this one.

This is that piece. It projects a portfolio of fixed-income holdings
alongside a liability cashflow — accruing income, taking defaults, buying
with spare cash and selling to cover a shortfall — and reports what the
fund **earned**. The earned rate is the input the rest of the platform has
been asking for by name:

| RFC | What it took as given | Where it now comes from |
|---|---|---|
| RFC-010 universal life | the `"portfolio"` crediting rate | `AssetProjection.earned_rate` |
| RFC-011 fixed-indexed | an option budget | `book_yield` less the guarantee |
| RFC-014 Solvency II | no market-risk module at all | a portfolio to shock |
| RFC-016 PBR | starting assets *and* earned rates | `earned_rates(projection, freq)` |
| RFC-019 with-profits | asset shares, but not the assets | the fund side of the estate |

## The identity everything is checked against

Every roll-forward in this library is verified against a statement of cash
that cannot be argued with, and this one is no exception:

    closing book = opening book
                 + investment income
                 − default loss
                 + realised gain on sales
                 + net liability cashflow
                 + shortfall

Purchases, sales, coupons and maturities are **transfers** — they change
what the fund holds without changing what it is worth — and they cancel out
of the identity exactly. `reconciles()` asserts it period by period.

`shortfall` is in there deliberately. It is cash the fund was asked for and
could not raise, and putting it in the residual instead would make an
insolvent projection indistinguishable from a broken one.

## Book income is not the coupon

Holdings are carried at amortised cost on a constant yield:

    income[t] = book[t] · ((1 + y)^(1/freq) − 1)
    book[t+1] = book[t] + income[t] − coupon[t]

A 6% bond bought to yield 4% pays a **60** coupon and earns **46.49** of
income. The other 13.51 is return of capital. An office booking the coupon
as earnings reports a rate it did not earn *and* runs its assets down while
the accounts say otherwise — the two errors are the same error and they
point in the same direction.

The recursion cannot make that mistake: the book value of a holding is
exactly zero once its last payment has been received, by construction of
its yield rather than by a write-off convention. That is asserted, on a
premium bond, to 1e-9.

## The finding: the portfolio rate lags the market for years

The number RFC-010 has been taking as an input, finally measured. A par
ladder at 3%, the whole curve moves to 7% at time zero, reinvestment at the
ladder's own term:

| | t=0 | t=1 | t=3 | t=5 | t=10 | t=15 | t=20 |
|---|---|---|---|---|---|---|---|
| 10-year ladder | 3.00% | 3.50% | 4.47% | 5.35% | **7.00%** | 7.00% | 7.00% |
| 20-year ladder | 3.00% | 3.31% | 3.92% | 4.49% | 5.69% | 6.51% | **7.00%** |

Half the gap closes in **5 years** on the short ladder and **7** on the
long one. A fund does not adopt a new interest rate; it converges on one as
its old holdings mature, and the speed is a property of the portfolio, not
of the market.

### Why that matters more falling than rising

Run it the other way — 5% to 1%, against a 3% crediting guarantee. New
money is under the floor from **day one**. The portfolio does not fall
through it until **period 5**.

An office pricing its minimum crediting guarantee off new-money rates gets
the cost right and the *timing* wrong by half a decade, in the direction
that reports the guarantee as free during the years it is quietly becoming
expensive. RFC-010 measured what the floor is worth; this measures when it
starts.

## The finding: a credit spread equal to expected loss loses money

The obvious calibration — set the spread to the expected default loss — is
wrong, and wrong by an amount with a closed form.

A holding that defaults at the start of a period pays neither its principal
nor the coupon that principal would have paid. So the spread has to cover
the lost income as well as the lost capital:

    s = freq · d · (i + 1 − recovery) / (1 − d)

On a 2.5% default rate at 40% recovery over a 3% curve that is **161.5 bp**
against an expected loss of **150 bp**. Set the spread to expected loss and
the portfolio nets **288.75 bp** — short of the risk-free rate by exactly
`d × y`, the default rate times the book yield, every year. Set it to the
formula and the projection returns 3.0000% to 1e-9, monthly as well as
annually.

`breakeven_spread` exists so the correct number has a name. The
`DefaultBasis.expected_loss` property exists next to it so the two can be
compared rather than confused.

The convention this rests on — **defaults before the period's payments** —
is stated in the module docstring so it can be argued with. Written the
other way round a portfolio collects income from bonds that did not survive
to pay it.

## The finding: liquidation order is timing, and nothing else

Rates rise 400 bp, and a surrender spike forces the fund to raise 260 of
cash it does not have in coupons. Nothing has defaulted; every bond is
performing. The loss booked that year:

| order sold in | realised in year 0 | book yield at t=10 |
|---|---|---|
| shortest first | **−34.12** | 3.00% |
| pro rata | −91.63 | 3.75% |
| longest first | **−150.74** | 5.95% |

Same portfolio, same 260 raised, and a **factor of 4.4** between the best
and worst line in the accounts.

And then it washes out. Cumulative net investment income across the three
orders differs by 107.5 in year 0 and is **identical to floating point** —
4.5e-12 — from period 19 on, the period the pre-existing ladder finishes
running off:

| cumulative NII to | shortest | pro rata | longest |
|---|---|---|---|
| t=0 | 5.13 | −47.53 | −102.39 |
| t=4 | 99.06 | 48.01 | −10.22 |
| t=14 | 569.71 | 561.66 | 544.00 |
| **t=19** | **1044.777718** | **1044.777718** | **1044.777718** |

Selling short first books the smaller loss now and leaves the fund holding
old 3% paper that earns less for a decade. Selling long first books the
larger loss now and leaves the fund reinvested at 7%. **An office choosing
a sale order is choosing which year to report the loss in.** It is a
presentation decision wearing an investment decision's clothes, and the
engine says so by measuring it to twelve significant figures.

That result holds under a curve that stays put after the shock. It is not a
claim that the choice never matters — a fund that expects to be forced out
again, or that has a duration target, is deciding something real. It is a
claim that the *realised loss on its own* is not the thing being decided.

## The finding: book solvency and market solvency are different questions

The same +400 bp on the 20-year ladder leaves the fund carrying **1030.00**
at book and **782.45** at market — an unrealised loss of 247.55, a quarter
of the fund, invisible on a book basis and fully visible on a market one.
Book value went *up* over the year the market fell.
`unrealised_gain()` reports the difference, and it closes to zero on its
own as the portfolio turns over.

This is why RFC-014's Solvency II balance sheet and RFC-016's book-basis
accumulated surplus can disagree about the same company on the same day
without either being wrong.

## What is deliberately not a bond

`Holding` is not a `Bond`, and the split is load-bearing. A bond is a
contract: face, coupon, term. A holding is a position that gets **partly
sold, partly defaulted and steadily amortised**, and none of those three
operations leave a bond behind. Modelling a portfolio as a list of bonds
forces every one of them into a fiction about units.

## Not in scope

- **Stochastic defaults.** `DefaultBasis` spreads the expected loss evenly
  across periods, which is the right shape for a best-estimate projection
  and the wrong shape for capital, where the whole question is the tail.
  RFC-016's machinery is where that belongs.
- **Equities and property.** Everything here has a contractual cashflow.
  A total-return asset is a different roll-forward and RFC-005's scenario
  sets already carry the returns for one.
- **Derivatives**, so RFC-011's call spread still has no price and RFC-013's
  §B115 risk mitigation still takes its hedge result as given.
- **Rating transitions.** A holding's spread and default rate are fixed at
  purchase; there is no migration matrix. RFC-009's multi-state engine is
  the machinery, if it is wanted.
- **A trading strategy.** Sales happen only to meet cash, never to
  rebalance to a duration target — which means this module can *measure*
  RFC-020's duration gap and cannot yet close one.
- **Calls, puts and prepayment.** Bullet maturities only; an amortising or
  callable holding changes the cashflow, not the machinery.
