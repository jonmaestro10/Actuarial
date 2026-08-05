# RFC-029: Ring-fenced funds, and where the diversification goes

Status: **implemented** — `engine/report/ring_fenced.py`, `tests/test_ring_fenced.py`

## Summary

RFC-027 measured what Annex IV's two zeros are worth to a composite
insurer — a life book and a non-life book at the same undertaking are
assumed to share no risk, and putting them under one roof saves **19.28%**.
RFC-027 also named the restriction that takes it back and scoped it out:

> **Ring-fenced funds and matching adjustment portfolios** (Article 81 and
> Article 217), where the notional SCR of each fund is computed separately
> and diversification between them is not recognised. That is a real and
> material restriction on everything above, and it is its own piece.

This is that piece. Articles 80, 81, 216 and 217 of Commission Delegated
Regulation (EU) 2015/35.

## The finding: it costs exactly what RFC-027 measured

Ring-fence RFC-027's life fund away from its non-life fund and Article
217(9) takes back precisely the benefit Annex IV granted:

| | |
|---|---|
| merged, as RFC-027 computed it | **720.69** |
| two notional requirements, summed | **892.79** |
| lost diversification | **172.10** — 19.28% |

One RFC measures the benefit and the next measures the mechanism that
removes it, and the number is the same to the last digit reported. That is
worth stating plainly because the two were computed from different
articles, in different modules, a session apart.

## The finding: ring-fencing identical funds costs nothing

The bound is the triangle inequality on the Annex IV norm, and it is an
**equality** when the funds' module mixes are parallel. Measured on two
funds carrying the same risks in the same proportions, at the same size, at
double and at half: the lost diversification is **zero to 2.3e-13**.

Holding fund A fixed and varying fund B:

| fund B | merged | ring-fenced | lost |
|---|---|---|---|
| identical to A | 1,113.55 | 1,113.55 | **0.00%** |
| tilted to market | 1,186.91 | 1,221.04 | 2.88% |
| all market | 1,210.37 | 1,256.78 | 3.83% |
| all non-life (orthogonal) | 969.54 | 1,256.78 | **29.63%** |

So the intuition runs backwards. Ring-fencing is free where the funds carry
the same risks and ruinous where they carry complementary ones — which is
exactly where an insurer would most want to pool them. **Ring-fencing only
costs the diversification you actually had**, and two identical funds never
had any.

## The finding: Article 217(6) can hand back more than 217(9) takes

A fund's notional SCR is **not** its standalone SCR. Paragraph 6 requires
it to be calculated "using the scenario-based calculations under which basic
own funds **for the undertaking as a whole** are most negatively affected",
and paragraph 7 gives the arithmetic: sum each scenario's impact across
every fund and the remaining part, then take the worst total.

So a fund does not choose the scenario that hurts it most. On a
with-profits fund whose market risk is worst when rates *fall* (260 against
120) sitting beside a shareholder fund whose is worst when they *rise* (300
against 90):

- summed across the undertaking, **up** is the worse total — 420 against 350;
- the with-profits fund is therefore measured under rates rising, and its
  notional requirement is **255.34** against a standalone **365.79**.

Netted out, Article 217(9) costs **15.76** of lost diversification and
Article 217(6) hands back **110.44**. Ring-fencing that fund is worth
having.

Point both funds the same way and the relief is exactly zero. So the sign
of the whole regime, for a given fund, is decided by whether its risks
offset the rest of the undertaking's — and no part of the reported figures
says which.

This is the same shape RFC-026 found in Article 164(3), and in Articles
165(2) and 188(7): the standard formula repeatedly makes a capital number
depend on *which scenario bound somewhere else*, and then reports the
pieces without it.

## The finding: the own-funds side is much the larger cost

Article 81(1) compares the restricted own-fund items inside a fund with
that fund's notional SCR and removes the excess from the reconciliation
reserve. Capital trapped above what the fund itself needs cannot cover
losses anywhere else, so it does not count.

Decomposed on the with-profits pair with the funds pointing the same way,
so that ring-fencing is doing its worst:

| | SCR | own funds | ratio |
|---|---|---|---|
| not ring-fenced | 722.91 | 1,100.00 | **152.16%** |
| requirement effect only | 725.79 | 1,100.00 | 151.56% |
| and the own-funds restriction | 725.79 | 765.79 | **105.51%** |

The requirement rises by 2.88 and costs **0.60 percentage points** of
solvency ratio. The own funds fall by 334.21 — trapped above what the
with-profits fund needs — and cost **46.05 percentage points**, seventy-six
times as much.

So Article 216(2)'s escape hatch, which exempts a fund with Article 304
approval from Article 217 entirely and calculates "on the assumption of
full diversification between the assets and liabilities of the ring-fenced
funds and the rest of the undertaking", is almost entirely an **own-funds**
question rather than a capital-requirement one. The literature treats
ring-fencing as an SCR problem; on these numbers it is not.

## What is checkable here

Two statements the standard makes, both asserted rather than trusted:

- **Article 217(2) and (9)**: the total is the sum of the notional
  requirements, *exactly* — not an aggregation of them, and never below the
  largest.
- **Article 81(1)**: no fund's restriction exceeds its own restricted
  own-fund items, so the reduction can never remove capital that was not
  trapped in the first place.

## Not in scope

- **What makes a fund ring-fenced.** Article 80(1) turns on whether
  own-fund items "have a reduced capacity to fully absorb losses on a
  going-concern basis due to their lack of transferability", which is a
  legal question about the fund's terms. The module takes the restricted
  own-fund items as an input, as Article 80(2) effectively does when it
  excludes the value of future transfers attributable to shareholders.
- **Article 81(2)'s materiality derogation** is available by passing a
  notional requirement of zero, which is what it amounts to, but the
  judgement of whether a fund is immaterial is not made here.
- **Article 217(5)'s profit participation adjustment**, which changes how
  the scenario impact is measured inside a with-profits ring-fenced fund
  and interacts with RFC-027's Article 206(2) net Basic SCR. Getting that
  right needs the with-profits projection, not the aggregation.
- **Matching adjustment portfolios** are treated identically to ring-fenced
  funds by Articles 216 and 217 and so are covered by the same code, but
  the matching adjustment itself — the curve modification that makes a
  matching adjustment portfolio worth having — is still not built.
