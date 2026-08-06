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

**What is verified, including the version.** This was recorded for two
releases with a residual: source 1 is the text *as adopted* (10 October 2014),
while `engine/report/market_risk.py` cites the consolidated version
`02015R0035 — EN — 30.07.2020 — 007.001`, and nothing independently confirmed
that no amendment in between had touched Article 164(3). legislation.gov.uk's
post-adoption view of this Regulation is revoked (Financial Services and
Markets Act 2023) and shows the article elided, so it could not close it.

**The consolidated text has now been read directly**, and the residual is
closed. EUR-Lex's HTML front end still returns an empty body to an automated
request; its **Cellar** back end does not:

```
curl -L -H "Accept: application/xhtml+xml" -H "Accept-Language: eng" \
  http://publications.europa.eu/resource/celex/02015R0035-20200730
```

38,429,518 bytes, titled `Consolidated TEXT: 32015R0035 — EN — 30.07.2020`,
first body line `02015R0035 — EN — 30.07.2020 — 007.001` — the cited version
exactly. (Cellar answers `503` intermittently; retrying succeeds.)

In it, **Article 164 carries no amendment marker at all**. Consolidated
EUR-Lex texts mark amended passages `▼M`*n*, corrigenda `►C`*n*, and original
base text `▼B`. The nearest marker preceding Article 164 is `▼B`; the next
marker of any kind is the `▼M1` opening *Subsection 1a — Qualifying
infrastructure investments* 55 characters after Article 164(3)'s final
sentence, i.e. at Article 164a and outside it. The article is therefore base
text, unamended, as at 30 July 2020. Its correlation table and its
parameter-A sentence are byte-identical to the as-adopted text reproduced
below, and cell-for-cell identical to
`market_correlation(DELEGATED_2015, …)` in both interest directions.

**Corroborated the other way, by enumeration.** The consolidated header's
"Amended by" table lists nine acts and three corrigenda — `007.001` is a
consolidation ordinal, not an amendment count. Each was retrieved and read;
none amends Article 164:

| act | what it amends |
|---|---|
| 2016/467 | inserts Subsection 1a and Article 164**a** — an insertion *after* 164 |
| 2016/2283 | "Concerns only the German language version." |
| 2017/669 | "(does not concern the English language)" |
| 2017/1542 | Articles 164**a** and 164**b** only |
| 2018/1221 | Articles 1, 4, 177, 178, 178a, 180, 257 |
| 2019/981 (the 2019 review) | 81 instructions; market risk at Articles 168, 168a, 169, 171a, 172, 176, 176a–c, 180, 182, 184, 186–189. "Article 164" does not occur in the text. |
| 2019/1865 | "(does not concern the English language)" |
| 2020/442 | Article 84(4) introductory wording; Annex X flood-risk weights |
| 2020/988 | "(does not concern the English language)" |

That enumeration is worth more than the consolidated document on its own,
because a reader can re-check it act by act without a 38 MB fetch.

**Checked once further forward.** `DELEGATED_2015` governs every reporting
date before 30 January 2027, six years past the cited consolidation, so
`02015R0035 — EN — 14.11.2024` was retrieved as well: three more acts
(2021/526, 2021/1256, 2024/2765) and a fourth corrigendum later, Article 164
is still `▼B` and still byte-identical. The matrix is right for the whole
window it is used in.

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
