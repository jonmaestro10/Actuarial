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
| `engine/core/` | `@var` DSL, model base, runner, deterministic aggregation |
| `engine/data/` | Assumptions and model points (Parquet/Arrow I/O: Phase 1) |
| `engine/library/` | Product templates — each ships with golden tests |
| `tests/` | DSL mechanics, closed-form golden tests, reference reconciliation |
| `docs/` | RFCs and design notes |

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
  no template changes. First VA-family template: unit-linked with GMDB
  rider (guarantee strain, fee income, maturity values). Golden layers:
  zero-vol closed forms, bitwise slab-vs-single-scenario consistency, a
  risk-neutral martingale test, and pinned-seed determinism.

Next: kernel fusion/compilation (stochastic runs are memory-bound in
pure NumPy), GMAB/GMWB riders and dynamic lapse for the VA family,
scenario-set file adapters, and the run registry.
