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
- Product templates: level-premium term assurance, single-premium deferred
  fixed annuity — each with closed-form golden tests.
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

Next: the unit-linked family sub-annually (its charges and scenario returns
are annual-shaped and need a modelling decision first), reductions beyond a
sum, kernel fusion, and scenario-set file adapters.
