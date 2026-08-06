# Solvency II — the market risk correlation matrix (Article 164)

**Source.** Commission Delegated Regulation (EU) 2015/35, Article 164(3) and
its correlation table. Produced by the **European Commission**; calibration
work by **EIOPA** (and its predecessor CEIOPS).

## Status: **verified**

This entry was recorded *unverified* for one release, because EUR-Lex returns
an empty body to an automated request — it still does, on both the CELEX
consolidated URL and the ELI URL. The matrix has now been read from two
independent published reproductions of the primary text instead:

1. **legislation.gov.uk**, the UK Government's official reproduction of the
   Regulation as adopted, which carries Article 164(3) in full including the
   table.
   <https://www.legislation.gov.uk/eur/2015/35/title/I/chapter/V/section/5/adopted/data.xht?view=snippet&wrap=true>
2. **EIOPA's Solvency II Single Rulebook**, Article 164 ("Correlation
   coefficients"), which reproduces the definition of parameter A verbatim
   but defers to EUR-Lex for the table itself.
   <https://www.eiopa.europa.eu/rulebook/solvency-ii-single-rulebook/article-5784_en>

Source 1 supplies every cell; source 2 independently confirms the article's
identity and the parameter that makes the matrix direction-dependent. Both
agree with the module in every cell.

**What is verified, and what is not.** Source 1 is the text *as adopted*
(10 October 2014); `engine/report/market_risk.py` cites the consolidated
version `02015R0035 — EN — 30.07.2020 — 007.001`. The check therefore
confirms the matrix as enacted. It does **not** independently confirm that no
consolidation between 2015 and 2020 touched Article 164(3) — legislation.gov.uk's
post-adoption view of this Regulation is revoked (Financial Services and
Markets Act 2023) and shows the article elided. That residual is stated
rather than papered over.

## The matrix

`engine/report/market_risk.market_correlation(DELEGATED_2015,
interest_direction=…)`, order `(interest, equity, property, spread,
concentration, currency)`:

| | interest | equity | property | spread | concentration | currency |
|---|---|---|---|---|---|---|
| interest | 1 | *A* | *A* | *A* | 0 | 0.25 |
| equity | *A* | 1 | 0.75 | 0.75 | 0 | 0.25 |
| property | *A* | 0.75 | 1 | 0.5 | 0 | 0.25 |
| spread | *A* | 0.75 | 0.5 | 1 | 0 | 0.25 |
| concentration | 0 | 0 | 0 | 0 | 1 | 0 |
| currency | 0.25 | 0.25 | 0.25 | 0.25 | 0 | 1 |

with, quoting Article 164(3) as reproduced by both sources:

> "The parameter A shall be equal to 0 where the capital requirement for
> interest rate risk set out in Article 165 is the capital requirement
> referred to in point (a) of that Article. In all other cases, the
> parameter A shall be equal to 0,5."

Point (a) of Article 165 is the **upward** shock, so **A = 0 when the up
shock binds and 0.5 when the down shock does** — the feature
`engine/report/market_risk.py` documents at length as the one correlation
matrix in the library that is not a constant.

## The amendment, corroborated

`engine/report/market_risk.py` also carries `DELEGATED_2026` (Commission
Delegated Regulation (EU) 2026/269), in which Article 164(3)'s spread cell is
split out of *A* as a separate parameter **B, at 0 or 0.25**. Independent
published descriptions of the amending act — adopted by the Commission on 29
October 2025 as C(2025) 7206, applying from 30 January 2027 — describe it as
reducing the spread-to-interest-rate correlation **from 50% to 25% in the
interest rate downward scenario, and to zero in certain cases**. That is the
module's `interest_spread_correlation` of 0.25 with A unchanged at 0.5, and it
corroborates both the value and the fact that only the spread cell moved.

`tests/test_regdiff.py` asserts that last point directly:
`interest_correlation` does **not** appear among the divergent clauses
between the two texts, because parameter A against equity and property is 0.5
under both.

## What checks it

`tests/test_published_sources.py` now asserts the matrix cell by cell against
`market_correlation(DELEGATED_2015, …)` in both interest directions, which is
the check this file previously could not support.

`tests/test_market_risk.py` continues to assert the aggregation identity
`max_i SCR_i ≤ SCR_market ≤ Σ_i SCR_i` and the direction dependence — those
are properties of any admissible matrix, and this file is what pins the
particular one.
