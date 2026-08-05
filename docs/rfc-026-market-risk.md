# RFC-026: Solvency II market risk, and the shock that duration cannot see

Status: **implemented** — `engine/report/market_risk.py`, `tests/test_market_risk.py`

## Summary

RFC-014 built Solvency II's technical provisions and life underwriting
stresses and scoped market risk out in as many words:

> **Market risk**: interest up and down are expressible as a shift in the
> valuation rate and are supported, but equity, property, spread,
> concentration and currency need an asset model. That is the ALM overlay.

RFC-021 built the asset model and RFC-025 a trading strategy on top of it,
so the only thing left was the **prescribed data**. Two earlier sessions
stopped there rather than type twenty-one regulatory constants out of
memory. This one read the source.

The reading changed the design before a line was written. Commission
Delegated Regulation (EU) **2026/269** was published in the Official
Journal on 18 February 2026 and entered into force on 10 March 2026 — but
it **applies from 30 January 2027**. Both texts are live, for different
reporting dates, so the module ships both as dated, named sets and picks by
reporting date. A 2026 year-end reports on 2015/35; a 2027 year-end does
not.

## Where the numbers came from

`eur-lex.europa.eu` serves an AWS WAF JavaScript challenge to anything
without a browser, which is what stopped the previous attempts. Two routes
around it, both primary:

- **legislation.gov.uk** hosts the EUR-Lex *consolidated* PDFs of 2015/35
  at each amendment date — `02015R0035 — EN — 30.07.2020 — 007.001` is the
  last one, and Articles 164 to 188 carry no amendment marker after it.
  (Its own HTML and XML of the regulation are useless here: every
  `<Tabular>` element is **empty**, so the maturity tables — the entire
  point — are absent from the machine-readable version.)
- **The Publications Office Cellar**,
  `http://publications.europa.eu/resource/celex/32026R0269` with
  `Accept: application/xhtml+xml`, returns the authentic OJ text of
  2026/269. Its formulas are base64 JPEGs with `alt="Formula"`, which had
  to be decoded and read as images.

EIOPA's Solvency II Single Rulebook (last updated 27 March 2024) was used
as a third check that Articles 164 to 188 still read as the 2020
consolidation says, and it does — including Article 166(2)'s one
percentage point minimum and Article 167(2)'s "for negative rates the
decrease shall be nil", both of which 2026/269 removes.

## What 2026/269 does to the interest sub-module

The search snippet that prompted the caution was right on all three counts,
and understated the change. Under 2015/35 the shock is purely
multiplicative; under 2026/269 it is multiplicative **plus a parallel
shift**, with the tables restated at every integer maturity to fifty years:

    up:    r*(m) = r(m)·(1 + s_up(m)) + b_up(m)
    down:  r*(m) = r(m)·(1 − s_down(m)) − b_down(m)

The one percentage point minimum on the increase is gone. Article 167(2)'s
"nil where the rate is negative" is replaced by a floor on the *level* of
the decreased rate: −1.25% out to seven years, −0.893% from twenty,
linearly interpolated between. Beyond the first smoothing point both
articles hand off to the Article 77a extrapolation, which this module does
not implement (see Not in scope).

**The shape inverts.** The 2015 downward factor falls monotonically from
75% at one year to 27% at fifteen and sits near 29% thereafter. The 2026
one falls to 37% at seven years and then **rises all the way to 65% at
fifty**:

| maturity | 2015 down | 2026 down | 2015 up | 2026 up |
|---|---|---|---|---|
| 1y | 75.0% | 58.0% + 1.160pp | 70.0% | 61.0% + 2.140pp |
| 7y | 39.0% | 37.0% + 0.630pp | 49.0% | 37.0% + 1.300pp |
| 20y | 29.0% | 50.0% + 0.500pp | 26.0% | 25.0% + 0.880pp |
| 50y | 25.1% | 65.0% + 0.180pp | 23.4% | 21.0% + 0.730pp |

So the new calibration bites hardest exactly where a long annuity book
lives, and on the balance sheet below it more than doubles the interest
capital requirement — 5.15 becomes 11.64.

## Every divergence is also a setting

Two dated bundles are the right default and the wrong only option. An
amendment is rarely one thing: 2026/269 moves the interest tables, deletes
Article 166(2)'s minimum, replaces Article 167(2)'s negative-rate rule,
widens Article 172(4)'s corridor and splits Article 164(3)'s spread
correlation out — five changes arriving on one date. So each divergence is
a named setting and `MarketRiskCalibration.variant` throws any combination
of them:

```python
DELEGATED_2015.variant(interest_tables="2026/269")
DELEGATED_2026.variant("house view", minimum_increase=0.01)
```

`options()` is the inverse — a regime expressed as the switches it has
thrown — so `DELEGATED_2015.variant(**DELEGATED_2026.options())` computes
2026/269's numbers exactly, from either starting point. An unknown setting
raises rather than being ignored, because the failure this exists to
prevent is a run that quietly used the regime it was not asked for, and a
variant is a new frozen object, so the two published regimes stay exactly
what the Official Journal says they are.

There is a third value on one of them that is in no regime at all:
`negative_rates="unrestricted"` applies Article 167's formula with neither
the enacted nil rule nor 2026/269's floor. It is there because it is the
only way to see what the other two are worth — on a flat −1% curve the
one-year shocked rate is −1.00% under the enacted rule (no shock at all),
−1.25% under the floor, and −1.58% with neither.

## The finding: an amendment is not the sum of its clauses

This is what the settings bought, and it was not the intended reason for
building them.

Throw 2026/269's five clauses **one at a time** on the (5, 20) fund at 3%:

| clause | interest SCR | change |
|---|---|---|
| 2015/35 as it stands | 5.1464 | — |
| + the 2026 maturity tables | 11.6412 | **+6.4949** |
| + deleting Article 166(2)'s minimum | 0.0000 | **−5.1464** |
| + Article 167's term-dependent floor | 5.1464 | 0 |
| + the ±13% symmetric corridor | 5.1464 | 0 |
| + parameter B on the spread cell | 5.1464 | 0 |
| **all five together** | **11.6412** | **+6.4949** |

The one-at-a-time effects sum to **+1.35**. Applied together they are
**+6.49**. The decomposition is not additive, and the reason is specific:
under 2015/35 on a 3% curve the upward shock is Article 166(2)'s one
percentage point at 76 of the first 90 maturities, so deleting the minimum
does not trim the shock — it *is* the shock, and the requirement goes to
zero. Under the 2026 tables the upward shock already exceeds a percentage
point everywhere, so deleting the same minimum is a **no-op**. Adding it
back to the 2026 regime changes nothing at all.

So "2026/269 deleted the one percentage point floor" is a true statement
about the text whose effect, given the rest of the same amendment, is
exactly zero. Every unit of the relief a reader would attribute to that
deletion is really the tables. This is RFC-024's finding in a new place:
peeling drivers off one at a time does not decompose an interaction, it
hides it in the order.

**A larger shock is not always a larger capital requirement.** Put Article
166(2)'s minimum *back* into the 2026 regime on a 0.5% curve, where the new
upward shock reaches only 10bp at the ninety-year point. Every maturity now
moves at least a percentage point — a strictly larger shock at every point
of the curve — and the capital **halves**, 8.86 to 4.03. The minimum bites
at the long end, and this fund's liability is longer than its assets, so
raising the long end alone moves the liability down further than the
assets. The module is a shape, not a level.

**And the settings can rescue a change the bundle hides.** On a
down-binding book — assets at duration 5 against the twenty-five year
annuity — 2026/269's parameter B is worth **9.46** of market SCR, 175.80
falling to 166.34, a 5.4% reduction with every sub-module capital
unchanged. In the full bundle the same amendment's interest tables add 72.3
and the SCR goes *up*, to 248.11. Anyone comparing the regimes as bundles
would report point (41) as a capital increase. Only the switch separates
them.

## The finding: matching duration leaves the interest SCR undetermined

Stated as the hypothesis was: a Solvency II interest shock is a
term-dependent relative shock, so a duration-matched balance sheet should
still carry capital. Measured, it is stronger and less comfortable than
that.

The book is a twenty-five year level annuity in payment, worth 1,741.31 at
duration 11.4768 on a flat 3% curve. Every asset portfolio below is a
barbell worth **exactly** the liability with **exactly** its duration — so
the dollar-duration gap RFC-025 identified as the thing that actually
matters is zero (to 4e-12) in all of them. Only the *shape* differs:

| barbell | parallel +100bp | parallel −100bp | 2015 SCR | binds | 2026 SCR | binds |
|---|---|---|---|---|---|---|
| (10, 13) | −3.4991 | −4.5137 | **15.3578** | down | **44.2223** | down |
| (9, 15) | −3.0300 | −3.9106 | 12.3545 | down | 39.5552 | down |
| (8, 18) | −2.0286 | −2.5987 | 11.1653 | **up** | 21.9438 | **down** |
| (5, 20) | **+0.3285** | **+0.4310** | **5.1464** | up | 11.6412 | up |
| (3, 25) | +4.5499 | +6.0895 | 0.0000 | up | 0.0000 | up |
| (1, 40) | +16.9133 | +24.7468 | 0.0000 | up | 0.0000 | up |

Three things fall out of one table.

**The prescribed capital ranges from 15.36 to nothing on balance sheets
that a duration report cannot tell apart.** The hypothesis said a matched
book should carry a non-zero SCR; the truth is that matching says nothing
either way. A duration match is one number and the shock is a shape.

**The (5, 20) row is the sharp version.** That fund has more convexity than
the annuity, so a parallel move of *any* size in *either* direction
increases its surplus — checked at ±50, ±100, ±200 and ±300bp. There is no
parallel-rate loss to hedge, and Article 166 still takes 5.15, because it
moves the five-year point 165bp and the twenty-year point 100bp. This is
the direct sequel to RFC-020's convexity result: RFC-020 showed that
matching duration does not immunise against a *parallel* shift because of
the second moment; this shows it does not immunise against the *prescribed*
shift because of the first.

**The (8, 18) row binds up under 2015/35 and down under 2026/269.** One
balance sheet, one date, two regimes — and not merely two numbers but two
*correlation matrices*, for the reason in the next section.

The control is worth stating: a **cashflow-matched** fund, assets equal to
the liability payment for payment, has an interest SCR of exactly `0.0`
under both regimes, asserted with `==`. Nothing but cashflow matching gets
that.

## The finding: interest-down binds when the assets are shorter

Second hypothesis, and it holds. Same twenty-five year liability, same
1,741.31 of assets, all of them a one-year/forty-year barbell reweighted to
hit each duration — so the 11.477 row is the last row of the table above,
and the shape is held constant while only the duration moves:

| asset duration | parallel +100bp | parallel −100bp | 2015 SCR | binds | 2026 SCR | binds |
|---|---|---|---|---|---|---|
| 3.0 | +134.60 | −152.21 | 126.35 | down | 292.67 | down |
| 5.0 | +106.83 | −110.46 | 96.00 | down | 188.13 | down |
| 7.0 | +79.07 | −68.71 | 65.65 | down | 83.59 | down |
| 9.0 | +51.30 | −26.96 | 35.30 | down | 0.00 | up |
| 11.477 | +16.91 | +24.75 | 0.00 | up | 0.00 | up |
| 14.0 | −18.12 | +77.42 | 6.71 | up | 25.88 | up |

Falling rates raise bond prices, and the downward scenario is still the
binding one: the assets are worth more after the shock — asserted directly
on the `ShockResult` — and the liability is worth more still, because it is
longer. The capital is the *difference* of two rises.

## The finding: the market SCR is not a function of the module capitals

Article 164(3) prints a correlation matrix that contains a **symbol**. The
cell where interest rate risk meets equity, property and spread is `A`,
which is 0 when the upward scenario is the binding one and 0.5 otherwise.
2026/269 splits the spread cell out as `B` — 0 or 0.25.

Take six sub-module capitals — interest 100, equity 100, property 50,
spread 80, concentration 20, currency 30 — and aggregate them:

| | up binds | down binds |
|---|---|---|
| 2015/35 | 242.178 | **285.745** (+17.99%) |
| 2026/269 | 242.178 | 278.658 (+15.06%) |

Same six numbers, an 18% difference in the total. A reviewer handed a
standard sub-module breakdown **cannot reproduce the SCR**; they also need
to be told which interest scenario bound, which no module capital reveals.
Every other correlation matrix in this library is a constant that can live
in a module-level table. This one has to be assembled per balance sheet,
and `MarketRiskPosition` carries the direction for that reason.

It is also the one matrix here whose positive semi-definiteness is not
given by inspection, because the published object is not a matrix but a
family of them. RFC-014 showed what a matrix that fails it buys: an
aggregate below the largest module it aggregates, reported as a number
rather than an error. All four substitutions — two regimes, two directions
— pass, with the smallest eigenvalue never below 0.15. Worth checking; the
text does not say so anywhere.

## The finding: spread risk is unhedged by construction

Third hypothesis, and it holds by a wider margin than expected. Take the
(5, 20) fund — value-matched, duration-matched, dollar-duration-matched —
and hold it as corporate paper instead of government paper. Nothing on the
liability side moves when spreads widen, so the whole of the asset move is
a loss:

| the assets held as | spread SCR | as % of the liability |
|---|---|---|
| credit quality step 0 | 134.75 | 7.74% |
| step 1 | 155.92 | 8.95% |
| step 2 | 185.80 | 10.67% |
| step 3 | 349.24 | 20.06% |
| step 4 | 572.25 | 32.86% |
| step 5 or 6 | 848.48 | 48.73% |
| unrated (Art 176(4)) | 415.33 | 23.85% |
| *interest, same fund* | *5.15* | *0.30%* |

At credit quality step 2 the spread module is **36 times** the interest
module on the same assets. All the matching bought was a hedge against 3%
of the problem. A matching adjustment or a volatility adjustment is the
answer the framework gives, and neither is in this module.

Note the unrated row: Article 176(4) is **not** a conservative fallback. It
charges less than credit quality step 4 at every duration from one to
fifty, and more than step 3 — so a bond gains capital relief by losing its
ECAI coverage.

## The finding: the 2016 amendment moved a discontinuity, it did not remove one

Set out to check Article 176(3)'s factor is continuous where its duration
bands meet — two otherwise identical bonds a day apart in duration should
not attract different capital. It is not, and the history is the
interesting part.

The table as first published in **OJ L 12, 17.1.2015** is continuous at
every band edge except one: credit quality step 1 **drops 10 basis points**
at ten years, because the `a` entries for that column read 8.4%, 10.9% and
13.4% where continuity requires 8.5%, 11.0% and 13.5%.

Commission Delegated Regulation (EU) **2016/467** replaced the whole table.
Its version has 8.5%, 11.0% and 13.5% — the step 1 jump is gone, exactly.
In the same replacement, step 4's twenty-year entry moved from 46.5% to
**46.6%**, which *creates* a discontinuity of the same size at twenty
years.

So the amendment did not tidy the table. It moved the discontinuity from
credit quality step 1 to step 4, and it is still there in the text that
applies today and in the text that applies from 2027 — 2026/269 leaves
Article 176(3) untouched. The test asserts the count both ways: exactly one
jump in the original, exactly one in the current text, at different
coordinates.

## The finding: Article 166(2)'s minimum *is* the shock at low rates

"In any case, the increase of basic risk-free interest rates at any
maturity shall be at least one percentage point" reads like a backstop. It
binds whenever `r · s(m) < 1%`, and `s` runs from 70% down to 20%, so it
binds everywhere once the curve falls below **1.43%**:

| flat curve | maturities of the first 90 where the floor binds |
|---|---|
| 0.1% | 90 |
| 1.0% | 90 |
| 2.0% | 84 |
| 3.0% | 76 |
| 5.0% | 1 |

Through the whole of the period the standard formula has been in force in
euro, the calibrated maturity table has been largely **inoperative** and
the upward interest shock has been a flat +100bp parallel shift. That is
not a curiosity — it is why 2026/269 replaces the rule with a parallel term
`b_up(m)` that is tabulated by maturity rather than constant, and it is why
the two calibrations are worth keeping side by side rather than
overwriting.

## The finding: the concentration sub-module rewards subdivision

Article 183(1) aggregates single-name concentrations as `sqrt(Σ Conc_i²)`,
treating names as independent. Five hundred of exposure against a 1,000
calculation base at credit quality step 2:

| held against | capital |
|---|---|
| 1 name | 98.70 |
| 2 names | 65.34 |
| 5 names | 32.87 |
| 10 names | 13.28 |

The same total exposure, the same total assets, and **86.5% less capital** for
holding it against ten counterparties rather than one. That is the
sub-module doing its job — but it means the definition of a "name" is
carrying the entire calibration, which is why Article 182(1) has to say in
terms that a corporate group is one name and that properties in one
building are one property.

## Rates are shocked as spot rates

`YieldCurve` stores one rate per period — a forward. The Delegated
Regulation shocks "basic risk-free interest rates for that currency at
different maturities", which are the zero-coupon spot rates EIOPA
publishes. `spot_rates` and `curve_from_spot_rates` are exact inverses
(discount factors agree to 6e-15 relative on a sloped curve), and
`stressed_curve` goes out to spots, applies the table, and comes back.

Applying the table to the stored forwards instead is not a shortcut with a
small error. On the (5, 20) fund the correctly shocked upward scenario is a
loss of 5.15 and the forward-shocked one is a **gain** of 0.67 — the sign
is wrong, not just the magnitude.

## The invariants

Two, and they are not the same one twice.

**The capital is a fall in own funds.** Article 105(5) defines every market
sub-module as "the loss in the basic own funds that would result from" a
stated instantaneous change, so `ShockResult` carries the base and stressed
value of *both* sides and `reconciles()` checks the reported capital
against them. A module that priced the asset move with a duration
sensitivity, or charged an average spread factor against a portfolio total
instead of each holding separately, fails it — both are asserted as
failures rather than assumed to be impossible.

**Aggregation lies between the largest module and the sum.**
`max_i SCR_i ≤ SCR_market ≤ Σ_i SCR_i` holds for any positive
semi-definite matrix with entries in [0, 1] and fails loudly for one that
is not. RFC-014 found the failure mode; here the matrix is assembled from a
symbol per balance sheet, so the bound is checked on the position actually
reported rather than on the matrix in the abstract.

## Smaller things the reading turned up

- **The symmetric adjustment is negative when the index is on its average.**
  Article 172(2) is `½·((CI − AI)/AI − 8%)`, so `SA = −4%` at `CI = AI`
  and it only reaches zero when the index is 8% *above* its own three-year
  average. 2026/269 widens the corridor from ±10% to ±13%, which on 1,000
  of type 1 equity moves the extremes from 290/490 to 260/520 and changes
  nothing at all in between.
- **Article 168(4) sums before it correlates.** Type 1 is correlated at
  0.75 with the *sum* of type 2, infrastructure and infrastructure
  corporate, not with each separately. The two readings give different
  numbers and the block reading is larger.
- **Currency is the only market sub-module that does not aggregate.**
  Article 188(1) sums over currencies, so three currencies of 100 cost
  three times one currency of 100, and a long dollar against a short yen of
  the same size costs 250 rather than nothing.
- **Article 186(2) buys almost all its relief in twenty-two points.** The
  factor for an unrated insurer falls 37.5 percentage points as its
  solvency ratio goes from 100% to 122%, and only 15 more over the whole
  run from 122% to 196%.

## Not in scope

- **Article 166(2a) and 167(2a)**: beyond the first smoothing point,
  2026/269 derives the stressed curve by re-running the Article 77a
  extrapolation on a stressed ultimate forward rate (±15bp) and stressed
  last liquid forwards. This module has no Smith–Wilson extrapolation, so
  it applies paragraph 2's table at every maturity. The tables run to 90
  years, which is what 2015/35 does across the whole curve, so the
  behaviour is the old regime's rather than an invention — but it is a
  deviation and it is stated rather than buried.
- **Articles 177 to 179**: securitisation positions and credit derivatives.
  `SCR_spread = SCR_bonds + SCR_securitisation + SCR_cd` and only the first
  term is here.
- **Article 176(5)(b) and (c)**: partial collateral. Only 176(5)(a) — full
  cover, factor halved — is implemented, because the other two need the
  risk-adjusted collateral value of Articles 112, 197 and 198.
- **Article 170**: the duration-based equity sub-module, which needs
  Article 304 supervisory approval and, from 2027, only survives for
  approvals granted before 29 January 2027.
- **Article 181**: applying the spread scenario inside a matching
  adjustment portfolio — which is exactly what would take the sting out of
  the spread finding above, and needs the matching adjustment first.
- **The basic SCR.** This produces `SCR_market`. Combining it with life
  underwriting through Annex IV, and then the loss-absorbing capacity of
  technical provisions and deferred tax, is the next piece and is on
  PLAN §5.3's list already.
