# Solvency II — the market risk correlation matrix (Article 164)

**Source.** Commission Delegated Regulation (EU) 2015/35, Article 164(3)
and its correlation table. Produced by the **European Commission**;
calibration work by **EIOPA** (and its predecessor CEIOPS). Consolidated
text on EUR-Lex, CELEX 02015R0035.

<https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:02015R0035-20200101>

## ⚠ Status: recorded, **not verified in this session**

This is the one entry in `docs/sources/` that is *not* backed by a machine
read of its primary text. Two fetch routes were tried and both failed:
EUR-Lex returned an empty body to an automated request, and the EIOPA
single-rulebook page for the article defers to EUR-Lex for the table rather
than reproducing it. A third candidate — Lloyd's *2023YE Standard Formula
Guidance* — turned out to be a form-completion guide with no correlation
matrix in it.

So the values below are **the values the module already holds**, recorded
here against the Article references the module itself cites, and they are
*not* independent confirmation. Nothing in
`tests/test_published_sources.py` asserts against them, deliberately: an
unverified number dressed as a passing test is worse than no test.

**To close this**, open the EUR-Lex link by hand, check the table against
what follows, and change this heading.

## The matrix as the module holds it

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

with the direction-dependent parameter **A = 0 when the interest rate *up*
shock is the binding one, and A = 0.5 when the *down* shock is** — which is
the feature `engine/report/market_risk.py` documents at length as the one
correlation matrix in the library that is not a constant.

## What would check it

`tests/test_market_risk.py` already asserts the aggregation identity
`max_i SCR_i ≤ SCR_market ≤ Σ_i SCR_i` and the direction dependence. What
is missing is an assertion that the *coefficients* are the regulation's,
which is exactly the check this file cannot yet support.
