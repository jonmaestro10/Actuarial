# RFC-028: Counterparty default, and the cliff at seven per cent

Status: **implemented** — `engine/report/counterparty.py`, `tests/test_counterparty.py`

## Summary

RFC-027 assembled `SCR = BSCR + SCR_op + Adj` and took every Basic SCR
module as an input. RFC-014 built life underwriting, RFC-026 market risk,
RFC-027 the operational charge. This is the last one PLAN §5.3 names:

> **Counterparty default and operational risk**, neither of which is a
> projection.

"Not a projection" is right, and counterparty default is not a factor table
either. It is a **variance calculation on a list of exposures** — the only
Basic SCR module that builds an explicit loss distribution and reads a
capital requirement off its standard deviation. Which turns out to be why
it behaves the way it does.

Articles 189 to 202 of Commission Delegated Regulation (EU) 2015/35,
consolidated version `02015R0035 — EN — 30.07.2020 — 007.001`. 2026/269
does not amend them.

## The finding: Article 200's lower boundary is a cliff

Article 200 turns the standard deviation of the loss distribution into
capital in three bands:

    σ ≤ 7%  · ΣLGD   ->  SCR_def,1 = 3σ
    σ ≤ 20% · ΣLGD   ->  SCR_def,1 = 5σ
    σ >  20% · ΣLGD  ->  SCR_def,1 = ΣLGD

The **upper** boundary is continuous by construction: `5 × 20% = 100%` of
the total loss-given-default, which is exactly what the third band gives.
Somebody chose 5 and 20% so that they would meet.

The **lower** one is not. `3 × 7% = 21%` against `5 × 7% = 35%`, so an
arbitrarily small change in the portfolio moves the requirement by **14
percentage points of ΣLGD** — a **66.7% increase**. RFC-026 reported a 10
basis point discontinuity in Article 176(3)'s spread table as a defect
worth naming. This one is 140 times larger, and unlike that one it is
load-bearing rather than a typographical residue.

### Walked across on a real book

Thirty-seven equal credit quality step 4 counterparties sharing a thousand
of loss-given-default:

| counterparties | σ / ΣLGD | band | capital |
|---|---|---|---|
| 37 | 7.00091% | 5σ | **350.05** |
| 38 | 6.99728% | 3σ | **209.92** |

The thirty-eighth counterparty is identical to the other thirty-seven. The
total exposure is the same, the credit quality is the same, and the standard
deviation moves by **0.0036 percentage points**. The requirement falls by
**40.03%**.

That is not a diversification benefit. Diversification accounts for 0.11 of
the 140.13; the other 140.02 is the multiplier changing.

## The finding: the third band is calibrated to one unrated counterparty

A book consisting of a single 4.2% counterparty — Article 199(9)'s residual
probability of default, which is what an unrated, unregulated counterparty
gets — has σ at **20.0589%** of its loss-given-default.

That clears Article 200(3)'s twenty per cent boundary by **0.0589
percentage points**, so the capital is the entire exposure. The third band
exists to catch exactly that case and does so by a margin thinner than any
rounding in the published tables.

Split the same exposure across two counterparties and σ falls to 16.72% —
the 5σ band, capital 836.05, **16.4% less for the identical total
exposure**. RFC-026 found the concentration sub-module rewarding
subdivision through a Euclidean norm; here the reward comes through a band
boundary instead, and it is larger and more abrupt.

## The finding: a solvency ratio *is* a credit quality step

Article 199(3) maps an unrated insurer's own solvency ratio to a probability
of default. RFC-026 implemented Article 186(2), which maps the same quantity
to the concentration sub-module's risk factor. The two tables have different
grids — eight points against five — and on the five ratios they share they
agree **exactly**, each one landing on a credit quality step's parameter in
*both* sub-modules:

| solvency ratio | Art 199 PD | Art 186 factor | credit quality step |
|---|---|---|---|
| 196% | 0.01% | 12% | 1 |
| 175% | 0.05% | 21% | 2 |
| 122% | 0.24% | 27% | 3 |
| 95% | 1.2% | 73% | 4 |
| 75% | 4.2% | — | 5 and 6 |

So "122% covered" is not a number somebody picked. It is the standard
formula's definition of a credit quality step 3 counterparty, and it says
the same thing in both places it appears. The extra points in each table —
Article 199's 150%, 125% and 100%, Article 186's 100% — sit between credit
quality steps and only affect the interpolation.

This is a cross-check that was not available before RFC-026: two
independently transcribed tables from different sections of the regulation,
agreeing on a structure neither of them states.

## The variance splits cleanly, and the split is the book

Article 201 gives `V = V_inter + V_intra`. Measured on a thousand of credit
quality step 3 exposure:

| counterparties | V_inter | V_intra | concentration share |
|---|---|---|---|
| 1 | 956.32 | 1,437.92 | **60.1%** |
| 5 | 956.32 | 287.58 | 23.1% |
| 50 | 956.32 | 28.76 | 2.9% |

`V_inter` depends only on the total loss-given-default at each probability
of default, so spreading a book over more names does not move it **at all**
— identical to the last digit in all three rows. `V_intra` is `Σ LGD²` and
collapses. The whole of the diversification in this module lives in one
term, and it is the term Article 201(3) writes as a sum over probability
groups. That grouping is exact rather than an approximation — summing
`LGD²` within each group and then over groups is summing over every
exposure — so the implementation evaluates it directly.

A counterparty with a zero probability of default under Article 199(8)
contributes nothing to either term. The coefficient in Article 201(2) is
`0/0` there, which is a removable singularity and not an error, and the
test asserts the book is identical with and without such a name.

## Two more sharp edges

**A receivable crossing three months costs six times as much.** Article 202
charges 90% on receivables from intermediaries due for more than three
months and 15% on everything else. Same money, same intermediary, one day
later, six times the capital, no transition of any kind.

**A heavily collateralised reinsurer is treated as worse, not better.**
Article 192(2)'s second subparagraph replaces the 50% share with **90%**
where the counterparty is an insurer with 60% or more of its assets subject
to collateral arrangements — 1.8 times the loss-given-default. The logic is
sound and worth stating because it reads backwards: the collateral that
reinsurer's *other* cedants hold is exactly what will not be available to
this one in an insolvency.

## Article 189's coefficient is twice a correlation

    SCR_def = sqrt(SCR_def,1² + 1.5 · SCR_def,1 · SCR_def,2 + SCR_def,2²)

is an ordinary two-risk aggregation at a correlation of **0.75**, written
with the 2 of `2ρ` already multiplied in. A reader who takes 1.5 for the
correlation itself gets 1,410.67 where the sum of the legs is 1,300 — an
aggregate above the undiversified total, which is not an aggregation at
all. `reconciles()` bounds the result between the larger leg and the sum,
which is the cheapest possible guard against exactly that reading.

## Not in scope

- **Pool exposures** (Articles 193 to 195), types A, B and C, which need
  the structure of the pooling arrangement.
- **The risk-adjusted value of collateral** (Article 197) and **of a
  mortgage** (Article 198). The second needs a *hypothetical recomputation
  of the market risk module* with and without the property in it, which is
  a bigger piece than it looks and belongs with RFC-026's module.
- **Article 192a's derivative classification**, which decides between the
  18%, 16% and 90% shares. The share is an argument here and the
  classification is the caller's.
- **Article 196's risk-mitigating effect**, which is the difference between
  a capital requirement with and without the arrangement — again a
  recomputation rather than a formula, and again an input here.
- **Netting sets** (Article 192(1) second subparagraph and 192(3d)), where
  several derivatives with one counterparty are combined before the
  loss-given-default is taken.
