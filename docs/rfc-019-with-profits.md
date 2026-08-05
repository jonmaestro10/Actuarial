# RFC-019: With-profits — asset shares, bonuses and the estate

Status: **implemented** — `engine/library/with_profits.py`

## Summary

PLAN.md §5.2 lists "with-profits/par funds (asset shares, bonus
mechanisms)" under *later*. This is it, and it is also the first template to
use `@pool` for what RFC-001 introduced it for: that decorator was written
for "a variable-payment adjustment, **a with-profits bonus or an asset
share**", and only the first of the three had ever exercised it.

A with-profits policy is the opposite of everything else in this library.
Every other template computes what the office **owes**; this one computes
what each policy has **earned**, and then decides how much of that to hand
back. The two questions have different answers and the gap between them is
the business.

## The asset share needs a pooled variable, and that is not incidental

    asset share(t+1) = (asset share(t) + premium − expenses)·(1 + earned)
                       − cost of cover
                       + share of the profit on the lives who left

The last term is the one a per-policy formula cannot express. When a
policyholder dies the fund pays the guaranteed sum assured and releases that
life's asset share; the difference falls on **everybody else**. It is a
transfer *between* policies, so it is a `@pool`, and the tests assert that
every model point in the block sees the identical value.

Its sign tracks one crossing — whether a policy has yet earned more than it
was promised — and nothing in the code knows about the crossing. Early on
the guarantee is far above the asset share and every death costs the
survivors (−202 per policy at duration 10); late on the asset share has
overtaken it and deaths release money instead (+176 at duration 24). It
falls out of the arithmetic.

## The finding: a bonus declaration is cheapest at issue and dearest at maturity

And the direction runs the **opposite** way from the obvious guess, which is
what the first version of the docstring claimed.

Declaring 2% of the sum assured does not cost 2% of anything current: it
costs the present value of raising *every* future death and maturity payment
by that amount. A present value of something payable in twenty-five years is
a **fraction** of its face:

| duration | cost of declaring 2% | as a share of the 2,000 nominal |
|---|---|---|
| 0 | 616 | **31%** |
| 5 | 778 | 39% |
| 10 | 983 | 49% |
| 20 | 1,572 | 79% |
| 24 | 1,905 | **95%** |

The identical announcement costs **three times as much** near maturity as at
issue. An office declaring a flat rate is not making a flat commitment, and
a bonus decision is not the same decision at every duration.

## Two bonuses, and only one of them is a promise

**Reversionary bonus** is added to the sum assured and, once declared, is
guaranteed — it cannot be taken back. The guaranteed benefit series is
therefore monotone *by construction*: the formula adds and never subtracts,
which is asserted rather than assumed about the bonus rate.

**Terminal bonus** is declared at the moment of payment and guarantees
nothing until then. It is the office's shock absorber, which is why a fund
under pressure cuts terminal bonus first and reversionary bonus last.

Compound and simple bases part company over a long contract exactly as the
closed forms say: 100,000 × 1.02²⁵ against 100,000 × (1 + 0.02 × 25).

## The bug: an asset share that vanished at the moment it was needed

Masking the asset share with `in_term` zeroed it at `t == term` — precisely
the date the maturity payout is struck against. Every policy was paid its
guarantee and **no terminal bonus at all**, silently, because
`max(0 − guarantee, 0)` is zero and looks like a contract that simply had no
excess to share.

The flows *into* the asset share are already masked, so nothing accrues past
the term and the mask on the balance bought nothing. This is the same lesson
`IncomeProtection` records as "the chain outlives the contract", found a
second time in a different shape.

## Smoothing, and who pays for it

Payouts are smoothed towards the asset share rather than set equal to it.
`smoothing = 1.0` pays the asset share exactly and smooths nothing; `0.0`
pays the guarantee and nothing more. **The payout never falls below the
guarantee** at any smoothing level — that is the promise, and it is asserted
at all four.

The difference falls into the estate, and `smoothing_cost` reports it: on
this block at 0.75 the fund keeps a quarter of the excess and the estate
*gains*. At 1.0 the cost is exactly zero, which is the check that the two
ends of the dial mean what they say.

Deaths are paid the guarantee and no terminal bonus — the ordinary
convention, and part of why the fund can afford to smooth maturities at all.

## Asset share against reserve

Worth setting side by side because they are so often confused. RFC-018's
prospective reserve values what is still owed on a basis chosen in advance;
the asset share accumulates what happened at what the fund actually earned.
On the same policy at duration 10 they differ by more than 10%, and an
office that pays one when it means the other pays the wrong amount.

## A bonus rule is a management action

`declared_bonus` is a `@var` precisely so a real rule — smoothed towards the
fund's return, cut when the estate thins — is an override rather than a
rewrite. The tapering subclass in the tests changes one method and
everything downstream follows.

The bonus rate and the smoothing level are **class attributes**, not
assumptions, because a bonus is something the office decides rather than
something given to it. That is a different kind of input from a mortality
table and it is typed differently.

## Not in scope

- **The estate as a projected balance.** `aggregate_asset_share` and
  `smoothing_cost` are the pieces; a full estate needs the asset side —
  what the fund actually holds — which is the ALM overlay.
- **Bonus smoothing rules with memory**, where this year's declaration
  depends on the last several years' returns. Expressible as a `@var` over
  earlier periods; not a mechanism, a rule.
- **Surrender value scales.** Surrenders are paid their asset share here,
  which is the neutral treatment and leaves the estate untouched by lapses.
  A real scale is contractual and varies by office.
- **Unitised with-profits**, where the guarantee attaches to a unit price
  rather than a sum assured. The account-value machinery from RFC-010 is
  what that would build on.
- **Takaful**, which PLAN lists beside this one and which shares the pooling
  but not the discretion.
