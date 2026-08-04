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
- **VPLA review and reconciliation**:
  [docs/vpla-review.md](docs/vpla-review.md) is the structural review the
  above came from, with the defects found and the architectural gap the
  pooled variable-payment product opens up in the DSL.

Next: a monthly time axis in the `@var` executor (the basis is already
frequency-aware; the projection loop is not), a second life on the model
point, the `@pool` cross-model-point reduction the VPLA product needs,
kernel fusion, scenario-set file adapters, and the run registry.
