# Actuarial Engine

An open actuarial projection platform: a Prophet / MoSes competitor built on
declarative models-as-code, a vectorizing executor, and accuracy enforced by
golden tests. Full plan: [PLAN.md](PLAN.md). DSL spec:
[docs/rfc-001-dsl.md](docs/rfc-001-dsl.md).

## Quick start

```bash
pip install -e ".[test]"
pytest
```

Define a product as time-indexed variables; the engine owns evaluation:

```python
from engine import Model, var, run
from engine.data.assumptions import Assumptions, MortalityTable
from engine.data.modelpoints import from_dicts
from engine.library.term_life import TermLife

assumptions = Assumptions(
    mortality=MortalityTable.flat(0.01), lapse=0.04, interest=0.025
)
modelpoints = from_dicts([
    {"id": "T1", "age_at_entry": 40, "term_years": 25,
     "sum_assured": 250_000.0, "annual_premium": 900.0, "init_pols": 1},
])

result = run(TermLife, modelpoints, assumptions, proj_len=30,
             outputs=["pols_if", "claims", "premiums"])
result.aggregate("claims")   # deterministic per-time-step totals
```

## Layout

| Path | Contents |
|---|---|
| `engine/core/` | `@var` DSL, model base, executors, calendar, deterministic aggregation |
| `engine/data/` | Assumptions, mortality basis, yield curves, model points, scenarios |
| `engine/library/` | Product templates — each ships with golden tests |
| `engine/report/` | Reporting overlays (products × frameworks) |
| `tests/` | DSL mechanics, closed-form golden tests, reference reconciliation |
| `scripts/` | Benchmarks and the VPLA parity harness |
| `docs/` | RFCs, design notes, and prior-art reviews |

## Status

Into Phase 1 of the [roadmap](PLAN.md#8-roadmap):

- Interpreted executor **and** vectorized NumPy executor behind the same
  `run()` contract; the golden suite asserts they agree **bitwise** on
  every template. Templates are written in indicator style so identical
  model code runs per policy or across a whole batch
  (`scripts/benchmark.py`: 100k policies × 60 years in seconds, ~40× the
  interpreter).
- Product templates: term assurance, whole life and endowment (with net,
  gross, Zillmerised and full-preliminary-term reserves), with-profits
  endowment on the pooled executor, single-premium
  deferred fixed annuity, payout and variable-payout annuities, unit-linked
  with GMxB riders, income protection on the multi-state engine, universal
  life with a §7702 corridor and a no-lapse guarantee, and fixed-indexed
  annuities with a lifetime withdrawal rider — each with golden tests.
- Model points round-trip through Parquet (`pip install -e ".[data]"`).
- **Stochastic executor**: `run_stochastic()` broadcasts model points
  against a `ScenarioSet` into `(time, model point, scenario)` slabs with
  no template changes. Golden layers: zero-vol closed forms, bitwise
  slab-vs-single-scenario consistency, a risk-neutral martingale test, and
  pinned-seed determinism.
- **VA/unit-linked library**: `UnitLinkedGMDB` (the seed) and
  `UnitLinkedGMxB` — GMDB, GMAB and GMWB on one contract, with a
  ratcheting benefit base, fund-capped rider charges, and **dynamic lapse**
  driven by how well funded the guarantees are. Turning every rider off
  makes the two templates bitwise identical. Closed forms cover the GMWB
  account run-down and its exact exhaustion year, the ratchet's running
  maximum, and the GMAB maturity payment; an independent forward-loop
  reference with every rider on reconciles at 1e-12.
- **Layer 0 basis, taken from VPLA** ([RFC-002](docs/rfc-002-basis.md)):
  `MortalityBasis` (fractional-age UDD/linear splits with actual or 30/360
  day count, 1-D and generational improvement scales, a limiting age),
  `YieldCurve` (term structure at any payment frequency), and annuity
  factors — single life, life-and-certain, joint life, reversionary — over a
  whole block at once. The VPLA calculations were validated against Society
  of Actuaries calculators, so they were promoted whole rather than
  rewritten: **408,000 period mortality rates are compared for bitwise
  equality** against a literal transcription of the original, and
  `scripts/vpla_parity.py` reruns that against a real VPLA checkout on the
  actual CPM2014/CPM2014B tables. Same numbers, ~400x faster per life.
- **Any payment frequency in the projection loop**: `t` counts payment
  periods, not years. A `TimeAxis` places each period on a real calendar
  date from each policy's own valuation date, and a `setup()` hook lets a
  template build survival curves and discount vectors for the whole axis in
  one call before the loop starts ([RFC-001](docs/rfc-001-dsl.md)). First
  template on it: `PayoutAnnuity` — monthly, with certain periods and
  reversionary benefits projected as cashflows, its terms reconciled
  **bitwise** to the Layer 0 annuity factor.
- **Chunked execution**: the vectorized executor splits a block so the
  working set stays in cache — ~3.6x on a monthly block, and bitwise
  identical, because model points are independent. 100,000 annuitants x 720
  monthly periods on the full basis runs in under two minutes
  (`scripts/benchmark_monthly.py`).
- **VPLA review and reconciliation**:
  [docs/vpla-review.md](docs/vpla-review.md) is the structural review the
  above came from, with the defects found and the architectural gap the
  pooled variable-payment product opens up in the DSL.
- **Pooled products**: a `@pool` variable reduces across the model-point
  axis inside the time loop, which is what a variable-payment adjustment, a
  with-profits bonus or an asset share needs and a per-policy formula cannot
  express. `VariablePayoutAnnuity` is the product built on it — the pool
  balances exactly after every revaluation, and is neutral to machine
  precision when the fund earns the valuation rate and mortality runs to
  assumption. The executor stops chunking a pooled model automatically,
  since a reduction over a chunk reduces over the wrong population.
- **One mortality lookup**: the engine had two implementations of "read a
  rate out of a table" — the validated VPLA basis, and a separate integer-age
  table three of the templates used. There is now one. `MortalityTable` is a
  unisex, non-improving view over `MortalityBasis`, and every template reads
  mortality through `Assumptions.annual_q`. No golden value moved, and the
  annual templates gained sex-distinct rates and generational improvement
  without being rewritten.
- **Sub-annual projection for the age-indexed templates**: term life and the
  deferred annuity now run at any frequency dividing 12. A year of age is
  split by `MortalityBasis.periodic_rate` — the dateless counterpart to the
  date-driven split, for products priced by entry age rather than valued from
  a date of birth — and every annual assumption has a per-period view.
  `freq = 1` is the **identity, bit for bit**, so the annual golden suite is
  the regression test. A finer step leaves the same policies in force at
  every anniversary but shifts exits from mortality to lapse, converging on
  the continuous multi-decrement answer.

- **Select-and-ultimate mortality**: the basis takes a select table in the
  published layout — one row per age at selection, one column per year
  since — and falls through to the ultimate table when the select period
  runs out. `TermLife` reads it, because term assurance is priced on select
  rates, and its model points take a `duration_in_force` for a block already
  part way through. Duration is an optional argument rather than part of the
  age index, so an ultimate-only lookup evaluates the same expression it
  always did: the identity is asserted with `==` on floats, and the VPLA
  parity harness still reports bitwise on every rate.
- **Embedded value and ALM** ([RFC-020](docs/rfc-020-embedded-value.md)):
  PLAN §5.3's last line, and where four earlier RFCs converge — Solvency
  II's missing market modules, the principle-based reserve's earned rates,
  the with-profits estate, the FIA's unpriced hedge all stopped at the asset
  side. Embedded value is where the option this engine keeps measuring gets
  a **line in a report**: `TVOG = deterministic PVFP − mean stochastic
  PVFP`. On a universal-life account with a 1% minimum crediting rate at 6%
  volatility, the deterministic PVFP is **+7.78m** and the stochastic mean is
  **−0.82m** — a time value of **8.60m, 110% of the whole deterministic
  value**. A traditional EV reports a positive value of in-force where a
  market-consistent one reports a negative one. Not a mis-calibration: it is
  RFC-010's "strip of annual options" seen at portfolio scale. The ALM half
  demonstrates that **matching duration does not immunise** — two portfolios
  matching value and duration to machine precision (gaps of 1.8e-15 and
  0.0) move in **opposite directions** under the same shift, because one
  holds more convexity than the liability and one less. The second-order
  claim took two attempts: a fixed ratio is wrong by 18% at 200bp because
  the third derivative is in it too, so it is asserted as a convergence —
  halving the shift divides the error by 3.12 at 400bp and 3.97 at 12.5bp.
- **With-profits: asset shares, bonuses and the estate**
  ([RFC-019](docs/rfc-019-with-profits.md)): PLAN §5.2's "later" line, and
  the first template to use `@pool` for what RFC-001 introduced it for — that
  decorator was written for "a variable-payment adjustment, **a with-profits
  bonus or an asset share**", and only the first had ever exercised it. Every
  other template computes what the office *owes*; this one computes what each
  policy has **earned**. The mortality profit is the pooled term and not
  incidentally so: when a policyholder dies the fund pays the guarantee and
  releases that life's asset share, and the difference falls on everybody
  else — a transfer *between* policies, which a per-policy formula cannot
  see. Its sign tracks one crossing nothing in the code knows about: −202 per
  policy at duration 10 (the guarantee is dear), +176 at duration 24 (the
  asset share has overtaken it). **A bonus declaration is cheapest at issue
  and dearest at maturity**, which is the opposite of the first guess:
  declaring 2% costs the *present value* of raising every future payment, so
  31% of its nominal amount at duration 0 and 95% at duration 24 — three
  times as much for the identical announcement. A real bug found on the way:
  masking the asset share with `in_term` zeroed it at exactly the date the
  maturity payout is struck against, so every policy silently got its
  guarantee and **no terminal bonus at all** — the same lesson
  `IncomeProtection` records as "the chain outlives the contract".
- **Policy reserves, whole life and endowment**
  ([RFC-018](docs/rfc-018-reserves.md)): PLAN §5.2's *first* bullet, and a
  structural gap as well as a product one — every template here projects
  cashflows and lets the overlays build a liability, but a traditional
  assurance is valued on a **reserve**, which is a property of the contract
  rather than of a reporting framework. Almost everything in it is an
  **exact identity**: prospective equals retrospective; an endowment *is* a
  term assurance plus a pure endowment (`==` on floats); the reserve is
  self-financing under `(V+P)(1+i) = qS + pV'` to 1e-8. And the two layers
  check each other — the closed-form premium and the year-by-year
  projection share no code, yet the projected PV of premiums equals that of
  benefits to nine significant figures. **What the net premium reserve
  leaves out is expenses**: on the same contract it opens at exactly 0.00
  while the gross premium reserve opens at −3,178, and the sign runs the
  *other* way from the obvious guess — charging more makes the gross reserve
  more negative, because the valuation capitalises the profit the net basis
  refuses to see (−737 at a 5% loading, −17,828 at 40%). The strain is the
  gap between the bases, not the sign of either. Zillmer and full
  preliminary term rank `FPT ≤ Zillmer ≤ net` at every duration and converge
  on the same maturity value. A real bug found on the way: the retrospective
  reserve subtracted an endowment's maturity at the duration it *falls due*
  rather than after, so the two definitions disagreed by exactly the sum
  assured — invisible on the term assurance the first smoke test used.
- **IFRS 17, the premium allocation approach** ([RFC-017](docs/rfc-017-paa.md)):
  the last of the three models §5.3 names, so **GMM, VFA and PAA are now all
  in place**. It is the only one that is a *simplification* rather than a
  measurement, and the absence of a CSM is the whole of it — profit emerges
  purely as premium is earned, so RFC-012's coverage-unit finding has no
  counterpart because there is no driver to choose. The finding: **the
  divergence from the general model is the time value of money and nothing
  else**. At a zero discount rate the PAA *is* the general model, exactly, at
  every term tested (1e-12 at 1, 5, 20 and 30 periods); at 4% the gap runs
  0.00% / 3.4% / 9.2% / 30.3% at 1 / 5 / 12 / 30 periods. That is precisely
  why §53(a) exempts one-year contracts and §56 requires accretion where
  there is a financing component — and the accretion **roughly doubles the
  eligible term**, from seven periods to fifteen at a 5% materiality
  threshold. §53(b)'s second limb is implemented honestly: proving you may
  use the simplification costs a full run of the thing it simplifies, so
  `eligibility` runs both models and records *which* limb was relied on.
  Four errors were caught by measurement — the comparison scale (dividing by
  a liability that is near-zero by construction called a 0.8% agreement a
  100% difference), the onerous test's sign (which manufactured a loss on a
  30%-margin group), the level revenue solve (the liability closed at minus
  the acquisition cost), and the day-one loss telescoping out of income.
- **US statutory principle-based reserves — VM-20 / VM-21**
  ([RFC-016](docs/rfc-016-pbr.md)): a third kind of overlay. IFRS 17 *reads*
  a projection, Solvency II *re-runs* one, and a principle-based reserve
  **reduces a distribution** of them — the answer is a statistic over a
  thousand projections, and which statistic is the whole design.
  **A percentile can report no reserve at all**: a guarantee biting in under
  30% of scenarios puts the 70th percentile at exactly zero while CTE70 over
  the same distribution says hold 21,298; where the percentile does bite, the
  CTE is **6.3×** it. And **value at risk is not coherent**: two independent
  bonds each defaulting at 4% show VaR95 of 0, 0 and **100** — the
  requirement appears out of diversification — where CTE95 is subadditive at
  every level tested. That is the reason the standard prescribes a CTE, and
  it is demonstrated rather than cited. A smoke-test print caught a real bug
  in the tail count: `1 - 0.70` is `0.30000000000000004`, so a naive ceiling
  takes **301 scenarios out of 1,000** and 3,001 out of 10,000 — on every
  run, invisibly, because one extra scenario barely moves a CTE. The
  per-scenario number is a *greatest* present value of accumulated
  deficiency, and on this block **over 60% of the paths needing a reserve
  need it before the end** — which is the argument for a maximum over a
  terminal measure.
- **US GAAP for long-duration contracts — LDTI**
  ([RFC-015](docs/rfc-015-usgaap-ldti.md)): ASU 2018-12, and the same
  economics as IFRS 17 measured a second way. Both insist that writing
  profitable business produces no day-one profit; the three caps here — net
  premium ratio at 100%, attributed fee ratio at 100%, reserve at zero — all
  say what RFC-012's loss component says. Then they disagree about **time**,
  and by a lot: a 25% deterioration discovered in year 8 of a fifteen-year
  cohort puts **1,154.92** through LDTI's income (re-derive the net premium
  ratio *from issue*, restate the whole history) against **272.46** through
  IFRS 17's (adjust the CSM, release it over the coverage that remains) —
  **4.24× apart on the identical event**. A 200 bp rate fall puts 11.4% of
  the peak reserve into AOCI with net income unchanged to 1e-9, because the
  liability accretes at the locked-in rate and is carried at the current one.
  **DAC is insensitive to profitability** — a wildly profitable cohort and a
  deeply onerous one amortize identically, asserted with `==` on the whole
  array. And a market risk benefit's fair-value change goes **straight to
  income**, where RFC-013's variable fee approach defers the identical move
  and reaches profit at a tenth of the speed. Total income equals the
  cohort's net cash to floating point, and that check caught three real
  errors: acquisition costs charged twice, interest deducted after it was
  already inside the change in reserve, and a capped cohort's day-one loss
  never reaching income at all.
- **Solvency II** ([RFC-014](docs/rfc-014-solvency2.md)): PLAN §5.3's second
  framework, and a different kind of overlay — **IFRS 17 reads a projection;
  Solvency II re-runs one**. The SCR is the fall in own funds under a shock,
  and a shock is a change of *assumption*, so there is no formula for what it
  does to a liability: the only way to find out is to project again on the
  stressed basis. This module therefore drives the engine rather than
  consuming its output, which is exactly why PLAN §4 puts speed ahead of
  product breadth. **Which lapse stress bites is the product's business, not
  the standard's**: lapse *up* releases capital on a term book and costs it
  on a unit-linked one, and lapse *down* does the opposite — the same shock,
  opposite signs, on two books at the same insurer. The module is the worst of
  the three and never their sum; adding them would overstate it by more than
  half here. And a **plausible correlation matrix can produce no capital at
  all**: symmetric, unit diagonal, every entry inside [−1, 1], smallest
  eigenvalue −0.8, and three modules of 100 give `v' C v = −24,000` — so any
  floor at zero reports an SCR of nothing for a book with three material
  risks. Checked on construction. The mortality shock scales the *annual*
  rate and the sub-annual split then divides the stressed year; the two orders
  coincide exactly at the first sub-period (which is why the first version of
  that test proved nothing) and differ by 66 bp of themselves by the twelfth
  month at age 85.
- **IFRS 17, the variable fee approach** ([RFC-013](docs/rfc-013-vfa.md)):
  the measurement model for direct participating contracts, which is most of
  what this engine models — unit-linked with an AMC, universal life, the
  fixed-indexed annuity. The GMM gets them visibly wrong: its CSM is a
  historic-cost balance, so a market move goes straight to the year's profit
  for a contract whose profit *is* a share of the market. One change fixes
  it — the CSM absorbs the entity's share of the underlying items and the
  financial changes. A 3,000 financial worsening with ten years of cover left
  hits year-5 profit by **−3,000 under the GMM and −300 under the VFA**,
  exactly the change times that period's coverage-unit fraction; the §B115
  risk-mitigation election puts it back to −3,000 to the last digit, which is
  why it is one flag and not a third model. The consequence running the other
  way is the one that surprises: **the VFA's CSM is not safe**. A 65% fall
  wipes a margin of 4,122 and puts 1,362 through profit immediately, where the
  GMM's CSM never hears about the market at all. The recovery case found a
  real bug — a rising pool rebuilt the CSM straight past a loss component
  still sitting there — so the growth and the change in estimate are now one
  signed adjustment under one rule. Total profit is still the group's net
  cash, whatever the pool does, and that invariant caught the module's
  conceptual error: the first version counted the variable fee as new money
  when the group's cashflows already contained it.
- **IFRS 17, the general measurement model**
  ([RFC-012](docs/rfc-012-ifrs17.md)): PLAN §5.3's first reporting overlay,
  and the first thing here that is not a projection — `engine/library`
  answers *what will happen*, this answers *what the accounting says
  happened*. An **overlay, not a calculator**: `Group.from_run` builds a
  group from a projection's own output series, so nothing in `TermLife`
  knows IFRS 17 exists and nothing here re-derives a cashflow. One invariant
  pins the rest — **total profit equals the group's undiscounted net cash**,
  to floating point, under every combination of the standard's choices — and
  it found the module's one real bug, a reconciliation gap of exactly
  `acquisition × i` from financing a day-zero outflow for a period it was
  never outstanding. What the choices *do* move is timing, and by a lot.
  **Grouping**: the same business with identical lifetime cash reports a
  year-1 service result of **−1,393** split by profitability as the standard
  requires, against **+311** measured as one group — a 1,704 swing from where
  a line is drawn. **Coverage units**: releasing on policy count puts 25% of
  the margin in the first five years, on sum assured 43%, and discounting
  the future units (permitted either way) is worth another 8 points.
  **The locked-in rate**: with rates down from 5% to 1% the CSM picks up six
  times the interest it would at today's, and total profit does not move a
  penny — it is all a transfer between the service result and the finance
  line.
- **Index crediting and the lifetime guarantee**
  ([RFC-011](docs/rfc-011-fixed-indexed.md)): PLAN §5.2's fixed-indexed
  annuities, on RFC-010's account. An FIA credits at anniversaries and
  nowhere else, floored at zero, so the account is a ratchet and the *path*
  matters — a bad year costs the policyholder nothing, so the distribution of
  what gets credited is not the distribution of what the index did.
  Measured on **one shared index path**, so what separates the designs is the
  design: an annual point-to-point cap of 6% delivers a **3.2%** mean credit;
  a monthly-sum design with a 2% monthly cap advertises **24%** and delivers
  **0.9%**, crediting nothing in four years out of five. The cap truncates the
  good months and the bad months come through in full. Monthly averaging is
  the quiet version — **10% less** at an identical cap, with no change to any
  quoted number. A monthly design **cannot** run on annual scenarios and the
  basis refuses one at construction. On the rider side, a GLWB differs from
  the unit-linked GMWB by one word — *lifetime* — and it is worth more than
  the rest: the account survives a median 22 years, so a projection cut off
  at 20 captures **0.3%** of the guarantee and one cut off at 30 captures
  63%. Every penny of it is in the tail. Letting a flat lapse run through the
  withdrawal phase cuts the guarantee's cost **60%**, which is not prudence
  but a different answer.
- **Universal life and the account-value family**
  ([RFC-010](docs/rfc-010-universal-life.md)): PLAN §5.2's interest-sensitive
  products, and the first template whose *benefit* is a projected number
  rather than a policy-data field. Two things follow that nothing before it
  needed. **A contract can lapse from arithmetic** — when the account cannot
  meet its own deductions the policy leaves the book on a date the
  projection produces, so the in-force indicator is a running product and
  therefore absorbing, and lapse-for-non-payment is kept strictly apart from
  voluntary surrender (one is paid a cash value, the other walks away from an
  empty account). And **the crediting floor is a written option** — worth
  *exactly* zero deterministically above it, and worth **+323 bp a year at
  10% volatility**, because a minimum crediting rate is not one option over
  the contract but one per period, resetting whatever the account already
  earned. The §7702 **corridor** turns out not to be optional either: without
  it a well-funded account carries no net amount at risk from year 10 and the
  model shows three decades of free cover, while with it the present value of
  death claims is **4.2×** larger. Striking the death benefit after the COI
  instead of before is not an alternative convention but a cycle, and
  RFC-001's detector names the loop rather than iterating to a fixed point —
  the first time it has caught something an actuary might plausibly write.
  The **no-lapse guarantee** runs as a shadow account and is worth more than
  "the contract lasts longer": the lapse it prevents lands precisely at the
  ages the cover is most likely to be claimed, so it lifts the present value
  of death claims **44%**, and it can fail on its own terms — an underfunded
  shadow account drains in year 4 while the real one lasts to year 25.
- **Multi-state Markov models** ([RFC-009](docs/rfc-009-multistate.md)):
  PLAN §5.2's health and protection engine, and the step past multiple
  decrements. The difference is one word — **recovery**: a decrement model
  can express falling ill but not getting better, because its populations
  only ever shrink. What replaces survivorship is one matrix multiply per
  period, and the **DSL needed nothing new** — a template writes one `@var`
  per state and the forward equation falls out as ordinary formulas. Rows
  summing to one is checked rather than assumed, so total occupancy is
  conserved for the whole projection (8.9e-16), and a two-state chain
  reproduces `(1-q)^t` survivorship to the last bit, so this *contains* the
  decrement engine rather than sitting beside it. Running monthly needs the
  twelfth **matrix root**, not the matrix over twelve — the naive version
  misses the annual matrix by **5.6 percentage points**. And a valid annual
  matrix need not have a valid monthly one at all: at 85% annual recovery
  the root has a negative probability and at 98% it is complex. That is the
  **embedding problem**, a property of the data rather than the arithmetic,
  and it is refused rather than clipped. First template: `IncomeProtection`,
  where waiver of premium is not a rider but the model.
- **Scale-out across cores** ([RFC-008](docs/rfc-008-scale-out.md)): the
  last Phase 2 exit. Sharding is safe for the same reason chunking is —
  model points are independent — so per-policy results are **bitwise**
  identical for any worker count, and a pooled model is *refused* rather
  than sharded into a reduction over the wrong population. The measurement
  decided the design: shipping per-policy series back is a **loss at every
  size** (0.25× at 100k policies, and worse as the block grows) because the
  results are the payload — 200 MB through pipes against 1.4 s of
  arithmetic. Reducing *in the worker*, which is what PLAN §4.3's "results
  reduce as streaming aggregations" actually asks for, is **2.5× on four
  cores**. One caveat stated rather than discovered: block totals regroup
  the summation per shard, so a worker-count change can move a total by an
  ulp and belongs recorded beside the run id. Cross-machine dispatch is not
  here; the sharding, the safety argument and the reduction are what it
  would need first.
- **LSMC proxies, with the error estimate that licenses them**
  ([RFC-007](docs/rfc-007-lsmc.md)): PLAN §4.4 admits proxy models "as an
  optional, clearly-labeled acceleration with error estimates", so this
  arrives *after* the exact nested valuation rather than instead of it —
  that is what a proxy has to be checked against. Value every outer node
  with five inner scenarios instead of a thousand, regress the noisy results
  on the state the template declares, and the surface lands within **2% at
  200× less inner work**. The finding worth carrying away is that **no
  in-sample statistic of the fit can tell you whether it worked**: the
  residual describes how far the noisy node values sit from the surface, not
  how far the surface sits from the truth, and across settings the ratio ran
  from 0.11 to 1.84 with no pattern. At degree 3, two inner scenarios per
  node give a *lower* residual than five while being five times further out
  — the flattering direction, pinned by a test. And a proxy cannot be
  measured better than its reference: two independent 1,000-inner references
  differ by 1.00%, which is what a 2% proxy error is quoted against.
- **Nested stochastic** ([RFC-006](docs/rfc-006-nested.md)): PLAN §4.4's
  "real killer workload", and the last Phase 2 exit criterion. An outer
  projection runs the block under real-world scenarios; at dates along each
  outer path the guarantees are valued by a risk-neutral projection starting
  from the state that path has reached. The load-bearing piece is that
  restarting is **exact** — `Model.restart_fields(t)` works because the
  `t == 0` branch of every stock variable reads one model-point field, so a
  template's state and its model point are the same list of numbers, and a
  contract restarted mid-life reproduces the straight-through projection
  **bitwise** across fourteen variables including the ratcheting benefit
  base. What makes the cost tractable is batching rather than cleverness:
  the outer states at one date are model points, so **one** inner projection
  values all of them, and the number of inner runs is the number of
  valuation dates rather than the number of outer nodes
  (`scripts/benchmark_nested.py`: 200 policies × 100 outer × 200 inner at 5
  dates = 20M inner cells in 59 s). Every value carries its Monte Carlo
  standard error, and outer nodes at a date share inner scenarios on purpose
  — the interesting quantity is how guarantee cost *differs* between states,
  and independent draws would bury that under noise about nothing.
- **The windowed forward loop**: PLAN §4.2 asks for the recursion over `t`
  to become "a forward loop over preallocated arrays", and both array
  executors now are one — periods written straight into their row of the
  output slab, and, because the dependency graph says how far back a model
  can reach, everything older dropped from the memo as the loop advances. A
  100,000-policy 60-year projection was holding hundreds of megabytes of
  arrays nothing would read again; freeing them is worth more than any
  arithmetic in the loop. **1.5x** on a 40k–100k block at the default chunk
  size, **2.3x** unchunked, **1.9x** on a stochastic slab — and honestly
  **nothing** on the monthly payout-annuity benchmark, where chunking had
  already made the memo small. Bitwise identical across 136 output series,
  five templates and both executors. Correctness does not rest on the traced
  window being right: a value asked for after it was dropped **raises**,
  naming the variable and period, rather than being silently recomputed in
  a way that could cascade.
- **The variable dependency graph** ([RFC-001](docs/rfc-001-dsl.md)):
  traced by *running* the model rather than by reading it, which is the only
  approach that works — `TermLife.pols_if` reaches `q_x` through two helper
  methods that a static scan would see straight past. Every edge carries the
  offset between the reading period and the period read, and that distinction
  is the whole thing: `pols_if` reading `pols_if(t-1)` is what a projection
  *is*, while reading it at `t` is a model that cannot be evaluated. A
  same-period cycle now raises with the chain that closed it, at depth two,
  instead of exhausting the stack a thousand frames later. The graph answers
  lineage both ways — what could have moved this number, and what will I
  change if I touch this formula — and renders itself as a Mermaid diagram.
  It also supplies what PLAN §4.2's compilation step needs *first*: a
  deterministic topological order over the same-period edges and the
  look-back window a forward loop must keep alive. Nothing is compiled yet.
  Recording is opt-in because it costs ~16% of the per-policy interpreter;
  cycle detection is always on and costs ~5% there and nothing measurable on
  the vectorized executor.
- **Tax hooks** (PLAN §5.1 Layer 0 — the last one): deliberately small,
  because tax regimes differ by jurisdiction more than any other assumption
  and a library that shipped one as *the* tax calculation would be wrong
  everywhere else while looking authoritative. A rate, a base, and an
  explicit statement of **what happens to a loss** — relieved in full,
  not at all, or carried forward against later profits. The first year of a
  policy reliably loses money, so that choice is worth more than most
  assumption changes anybody argues about, and the three are ordered:
  `full < carry_forward < none`. Tax runs on a **profit signature**, not on
  a present value: under full relief the two agree exactly (asserted as an
  identity), and under either other relief the gap is precisely the value of
  the losses that never got relieved — which a present-value calculation
  would quietly assume away. Investment tax reduces what a fund earns, on
  the unit-linked and deferred-annuity templates.
- **Reinsurance** (PLAN §5.1 Layer 0, and the last of them): quota share,
  surplus and per-risk excess of loss, on original or risk-premium terms
  with ceding commission. The proportional treaties satisfy an invariant
  asserted directly — **retained plus ceded is the whole sum assured,
  exactly, for every policy** — and excess of loss makes no such promise
  because a layer is a function of the claim, not a partition of the risk.
  The `lines` cap on a surplus treaty is the trap the class exists to make
  visible: a four-line treaty on a 50,000 retention takes at most 200,000,
  so a 500,000 policy leaves the cedant carrying 300,000, not 50,000.
  Aggregate and catastrophe covers are **deliberately absent** — they are
  statements about a portfolio, and modelling one as if it were per-risk
  would understate the cedant's exposure.
- **Expenses, inflation and commission** (PLAN §5.1 Layer 0): an expense
  loading is quoted on three bases at once — per policy, percent of premium,
  per mille sum assured — and falls in three lines that a pricing basis
  argues about separately: acquisition once at inception, renewal every
  period and indexed for inflation, claim costs per death settled.
  Commission runs at an initial rate for the first policy years and a
  renewal rate after, with optional straight-line clawback from early
  lapses. Everything is quoted annually and divided once, so `freq = 1`
  stays an exact identity and inflation indexes on the calendar rather than
  on anniversaries. A bare `expense_per_policy` is the renewal per-policy
  loading of a basis with nothing else in it — every projection that used
  the scalar form keeps its numbers **bit for bit**, across three
  frequencies. What it buys: a £250,000 twenty-year term policy breaks even
  on claims alone at about £2,182 a year and on the full basis at £2,981 —
  a **37% loading** the engine previously had no way to express.
- **The unit-linked family sub-annually**: `UnitLinkedGMDB` and
  `UnitLinkedGMxB` run at any frequency dividing 12, which needed a
  modelling decision per charge rather than only plumbing. The **AMC
  converts geometrically** — `1 - (1 - amc)^(1/freq)`, so `freq` deductions
  leave the fund exactly where one annual deduction would, and *not*
  `(1 + amc)^(1/freq) - 1`, which is the conversion for a rate that
  accumulates and leaks 1.31 bp a year on a 1.2% charge. **Rider fees and
  the guaranteed withdrawal spread**, being annual entitlements on amounts
  they do not erode. **The GMWB ratchet still steps annually**, because it
  is an anniversary event and a monthly projection must not lock in twelve
  high-water marks a year. `freq = 1` is the identity bit for bit across all
  48 output series of both templates. What a finer step does change is worth
  the run: a surviving policy pays identical total charges, but fee income
  falls 3.3% from annual to monthly, because a policy that lapses in March
  stops paying then instead of after a full year's AMC.
- **ESG file adapters** ([RFC-005](docs/rfc-005-esg.md)): scenarios can come
  from a generator's output file rather than only from an in-process
  generator, in either of the two layouts every vendor's output reduces to —
  wide (row per scenario, column per period) or long (a tidy
  `scenario, period, series, value` table, in CSV or Parquet). A
  `ScenarioSet` now carries **named series**, so equity, bond, short-rate and
  inflation travel together and a template written against `ret(t)` can be
  pointed at any of them. The value is in what the readers refuse to guess: a
  cumulative index converted without its base **raises**, because a
  generator publishing on 100.0 and one publishing on 1.0 give
  identical-looking files; a first period identical across every scenario is
  reported, because that is what reading an index column as a return looks
  like; and `check_risk_neutral` states the martingale test in **standard
  errors** rather than basis points, since the same deviation is sampling
  noise at 1,000 scenarios and damning at 1,000,000.
- **Multiple decrements** ([RFC-004](docs/rfc-004-decrements.md)): the
  assumption basis states each decrement on its own — mortality *if nothing
  else removed lives*, lapse *if nobody died*. Turning those into who
  actually leaves by each cause is now an assumption rather than an artefact
  of the order the multiplications were written in. Three methods, each
  exact under its own statement about when in the period people leave:
  `sequential` (the default, and the old behaviour operand for operand),
  `udd`, and `constant_force`. Every method agrees on total survival to the
  bit, so switching one cannot move an in-force count — only the attribution
  of exits. This closes the loop the frequency work left open: the gap
  between `sequential` at frequency *m* and `constant_force` at frequency 1
  closes first order in 1/m, so the answer a monthly projection was
  converging on is now available in one annual step.
- **Reproducibility** ([RFC-003](docs/rfc-003-run-registry.md)): a run
  records two digests — one of the question (model source, assumptions,
  model points, scenarios, projection length, outputs) and one of the
  answer. Same question with a different answer is a determinism failure and
  the registry refuses it. The digest is content-addressed and checked from
  a **subprocess with a different `PYTHONHASHSEED`**, because a digest that
  is not stable across processes certifies nothing; anything it cannot
  encode raises rather than being skipped. The two executors produce
  different run ids and the same results digest, which is the
  bitwise-equivalence claim stated as an audit trail.

Layer 0 in PLAN §5.1 is complete, and so are the Phase 2 exit criteria;
this is into Phase 3 breadth. Next: more of §5.2 (whole life and endowment,
universal life, fixed-indexed annuities, a deferred-period income protection
on the multi-state engine), the §5.3 reporting overlays, kernel fusion (the
graph and the forward loop are in place; nothing is compiled yet), and
cross-machine dispatch on top of the sharding.
