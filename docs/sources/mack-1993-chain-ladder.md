# Mack (1993) — the Taylor–Ashe triangle and its chain-ladder reserves

**Source.** Thomas Mack, *Distribution-Free Calculation of the Standard
Error of Chain Ladder Reserve Estimates*, ASTIN Bulletin 23(2), 1993,
pp. 213–225. ASTIN is a section of the International Actuarial Association;
the paper is hosted open-access by the Casualty Actuarial Society at
<https://www.casact.org/sites/default/files/2021-02/library_astin_vol23no2_213.pdf>.

The data is the **Taylor–Ashe** run-off triangle, from G. C. Taylor and
F. R. Ashe, *Second Moments of Estimates of Outstanding Claims*, Journal of
Econometrics 23, 1983. It is the most reproduced triangle in general
insurance reserving — it is the `GenIns` dataset in R's `ChainLadder`
package, whose own documentation says it exists to verify implementations
against Mack's Tables 2 and 3.

**How the figures here were obtained.** The PDF was fetched and its text
extracted mechanically (PyMuPDF); the triangle is Table 1 on p. 221 and the
results are Tables 2 and 3 on pp. 221–222.

## Table 1 — the triangle (cumulative paid)

| i | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 357 848 | 1 124 788 | 1 735 330 | 2 218 270 | 2 745 596 | 3 319 994 | 3 466 336 | 3 606 286 | 3 833 515 | 3 901 463 |
| 2 | 352 118 | 1 236 139 | 2 170 033 | 3 353 322 | 3 799 067 | 4 120 063 | 4 647 867 | 4 914 039 | 5 339 085 | |
| 3 | 290 507 | 1 292 306 | 2 218 525 | 3 235 179 | 3 985 995 | 4 132 918 | 4 628 910 | 4 909 315 | | |
| 4 | 310 608 | 1 418 858 | 2 195 047 | 3 757 447 | 4 029 929 | 4 381 982 | 4 588 268 | | | |
| 5 | 443 160 | 1 136 350 | 2 128 333 | 2 897 821 | 3 402 672 | 3 873 311 | | | | |
| 6 | 396 132 | 1 333 217 | 2 180 715 | 2 985 752 | 3 691 712 | | | | | |
| 7 | 440 832 | 1 288 463 | 2 419 861 | 3 483 130 | | | | | | |
| 8 | 359 480 | 1 421 128 | 2 864 498 | | | | | | | |
| 9 | 376 686 | 1 363 294 | | | | | | | | |
| 10 | 344 014 | | | | | | | | | |

## Published age-to-age factors (volume-weighted, p. 221)

`3.49, 1.75, 1.46, 1.174, 1.104, 1.086, 1.054, 1.077, 1.018`

Quoted to three or four significant figures in the paper, which is the
tolerance any check against them inherits.

## Table 2 — chain-ladder reserves, in thousands

| i | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | overall |
|---|---|---|---|---|---|---|---|---|---|---|
| R̂ᵢ | 95 | 470 | 710 | 985 | 1 419 | 2 178 | 3 920 | 4 279 | 4 626 | **18 681** |

(The paper's Table 2 gives five other methods alongside — Verrall 1991,
Renshaw/Christofides, Zehnwirth 1991, Mack, Taylor/Ashe — whose overall
reserves range from 16 652 to 22 301. That spread is itself worth knowing:
six defensible methods on one triangle differ by a third.)

## Table 3 — standard error as a percentage of each reserve

| i | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | overall |
|---|---|---|---|---|---|---|---|---|---|---|
| s.e./R̂ᵢ | 80 % | 26 % | 19 % | 27 % | 29 % | 26 % | 22 % | 23 % | 29 % | **13 %** |

## What this checks here, and the result

`engine/report/incurred_claims.py` — `development_factors(method="volume")`
and `ChainLadder`. Asserted in `tests/test_published_sources.py`.

**It passes.** Every factor agrees with the published value to the
precision the paper prints; every reserve agrees to better than 0.4 %, and
the overall reserve comes out at **18 680.9** against a published **18 681**
— a difference of 0.0008 %, which is the paper's own rounding to the
nearest thousand.

The one accident year with a visibly larger relative gap (i = 2, 0.39 %) is
the smallest reserve in the triangle, where rounding to a whole thousand is
worth 0.5 % on its own.

## What it does not check

Table 3. The repo has **no Mack standard errors** — reserve variability is
execution-plan item C5 and is unstarted. The figures are recorded above so
that when C5 is built it has a published target to hit rather than a
self-consistent one, and the plan's acceptance criterion for C5 already
says "reproduces published Mack/ODP results".
