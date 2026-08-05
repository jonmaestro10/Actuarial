# RFC-023: The liability for incurred claims

Status: **implemented** — `engine/report/incurred_claims.py`

## Summary

RFC-012 built the liability for remaining coverage under the general model,
RFC-013 under the variable fee approach and RFC-017 under the premium
allocation approach. All three named the same thing as out of scope, and
RFC-017 named it as a separate RFC:

> **The liability for incurred claims.** §59(b) permits it undiscounted
> where claims settle within a year, and requires discounting and a risk
> adjustment otherwise. It is a separate balance from the LRC and a separate
> RFC.

This is it — the other half of an insurance contract liability: what is
owed for events that have already happened, as against what is owed for
cover not yet given.

## The structural point: the LIC has no CSM

A contractual service margin is unearned profit, and a claim that has been
incurred has consumed the service it was paid for. So a claim moving from
the LRC to the LIC takes **no margin with it**:

    LIC = present value of future claim payments + risk adjustment

Two components, not three. Which means the LIC cannot absorb a change in
estimate the way a CSM can: adverse development on incurred claims goes
**straight to profit**. Under the general model the identical adverse
development on *future* claims is absorbed by the CSM and never appears in
the statements at all.

The two halves of the same balance sheet treat the same news completely
differently, and which half it lands in is a question of timing rather than
of substance.

## The invariant, and the term that is easy to leave out

    opening liability
        + insurance service expense
        + insurance finance expense
        = total claims paid

The opening balance is **not an expense of this run-off**. It was recognised
as the claims were incurred, in the period the coverage was provided; what
the LIC recognises afterwards is only the unwind of the discount and the
release of the risk adjustment.

Written without that term the invariant is not approximately right — it is
wrong by the whole liability, and the test asserts that too, with `==`
against the opening balance rather than a threshold. This is the fifth
overlay checked against a statement of cash that cannot be argued with, and
the first where the obvious statement of it was the wrong one.

## The finding: the chain ladder is additive exactly when the mix holds still

Set out to demonstrate that the chain ladder is not additive, and the first
measurement showed a gap of **exactly zero**. That is what located the real
condition.

Two segments developing on different patterns, combined cell by cell:

| | long-tail share of ultimates | combined reserve vs sum of parts |
|---|---|---|
| constant mix | 40% throughout | **0.00%** — exactly additive |
| shifting mix | 13.8% → 37.5% | **−34.0%** |
| long-tail book growing | 9% → 38% | **−39.7%** |
| short-tail book growing | 75% → 37.5% | **+59.9%** |

Two patterns blended in a **constant** proportion *are* a third pattern, so
the combined triangle is chain-ladder consistent and reserving the whole
gives exactly the sum of the parts. Non-additivity is not a property of the
chain ladder; it is a property of a **changing business mix**.

And the sign follows the direction of the shift. A growing long-tail book
leaves the development factors volume-weighted towards older, shorter-tailed
accident periods — so they are too small for the newer business and the
combined reserve **understates by 40%**. A growing short-tail book does the
same thing in reverse and **overstates by 60%**.

That makes segmentation a first-order decision rather than a presentational
one. "The reserve for the book" is not a well-defined quantity independent
of how the book was cut, and an entity whose mix is moving — which is every
entity that is growing anything — cannot reserve in aggregate and expect the
answer the segments would have given.

## The chain ladder inverts its own generating process, and that is the test

`Triangle.from_pattern` builds a triangle from ultimates and one development
pattern. `chain_ladder` recovers those ultimates to **1e-12**, and the
development factors come back as the pattern's own ratios to 1e-13. An
estimator that cannot invert the process that generated its data is not
estimating anything, and this is the closed-form golden test PLAN §3.1 asks
for.

The two averaging methods agree exactly on such a triangle, because every
individual ratio is the same number — they can only ever disagree about a
**mixture**. Put one accident period on a different pattern and volume
weighting and simple averaging part company by **37.4%** of the reserve.
Neither is more correct in the abstract; they answer different questions,
and they disagree whenever accident periods are of different sizes, which is
always.

## Development runs diagonally, so a reserve cannot be discounted off columns

Accident period `i`'s development period `j` is paid in **calendar** period
`i + j`. `future_payments` is the reduction of the completed square onto its
diagonals, and it is what the discounting consumes. Reading the payment
timing off the triangle's columns instead — the natural mistake, because the
columns are what the factors are computed from — misdates every cashflow by
the age of its accident period.

## What §59(b) is worth

The practical expedient permits an undiscounted LIC where claims are
expected to be paid within a year of being incurred, and the question it
invites is how far that stretches. Measured as the relative overstatement of
the liability, on a 4% curve:

| outstanding claims | mean term | error |
|---|---|---|
| a single payment one year out | 1.00y | **4.00%** |
| motor-like (70/90/97/99) | 1.47y | 5.90% |
| liability-like (15/35/55/72/88) | 2.28y | 9.23% |

**At the boundary the standard draws, the error is exactly one year's
interest** — which is presumably why it is drawn there. Beyond it the error
is the curve compounded over the mean term of the outstanding claims, a
little under it because a spread of payments is not a single payment at the
mean term. Both the approximation and the direction of its error are
asserted.

So the expedient is not a rounding convention. On a book with a two-year
mean settlement term it is a 9% understatement of the liability, and the
condition attached to it is doing real work.

## What is taken rather than derived

The risk adjustment is `RiskAdjustment` from RFC-012, unchanged, for the
reason recorded there: IFRS 17 says what a risk adjustment *is* and
pointedly does not say how to calculate it. Its release driver is a choice
of exactly the shape coverage units are — and on the LIC the honest driver
is the resolution of **uncertainty**, which is not the same series as the
payment pattern. A claim can be substantially settled in amount long before
it is paid.

## Not in scope

- **New incurrals.** `measure_lic` rolls a **closed** cohort forward, which
  is what makes the invariant a statement about the whole liability rather
  than about a window of it. A live reporting period needs claims arriving
  from the LRC each period, each with its own payment pattern, and that is
  a bigger object than this one.
- **Experience variance**, as in RFC-012, RFC-015 and RFC-017: expected
  against expected throughout, so nothing here develops adversely. The
  structural point about adverse development going straight to profit is
  argued and not demonstrated, because demonstrating it needs actuals.
- **Case reserves and IBNR as separate balances.** The chain ladder here
  estimates the ultimate; splitting the reserve into what has been notified
  and what has not is a second triangle on incurred rather than paid claims.
- **Bornhuetter-Ferguson, Cape Cod, and the Mack standard error.** The chain
  ladder is the one estimator here, and its non-additivity is a property it
  shares with most of them. A stochastic reserving distribution is the
  natural pairing with RFC-016's tail machinery and is a separate piece.
- **Claims handling expenses**, which are part of the fulfilment cashflows
  and are simply another payment series.
- **Reinsurance recoveries on incurred claims**, which are measured
  separately, and which RFC-012 also leaves open in the other direction.
