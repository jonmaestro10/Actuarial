# Open Actuarial Modeling Platform — Build Plan

A plan for building a Prophet / MoSes (RAFM) competitor: an actuarial projection
platform whose goals, in priority order, are **accuracy**, **speed**, and
**universal product coverage**, designed API-first so it integrates cleanly with
the rest of an actuarial team's toolchain (data warehouses, ESGs, reporting
engines, Excel, Python notebooks, existing Prophet/MoSes estates).

Working name used below: **the Engine**.

---

## 1. Why this can win

Prophet and MoSes are closed, desktop-era, license-heavy, and hard to integrate.
Their weaknesses are the design pillars here:

| Incumbent pain | Our answer |
|---|---|
| Proprietary IDE + binary model formats | Models are **plain code in git** — diffable, reviewable, CI-testable |
| Hard to integrate (file drops, manual runs) | **API-first**: every run is a REST/gRPC call or a Python function call |
| Slow single-threaded runs; grid licenses cost extra | Vectorized + compiled kernels, horizontal scale-out included |
| Vendor-gated product libraries | Open, layered **component library** + escape hatch to raw code for anything novel |
| Black-box results | Full lineage: every number traceable to formula, assumption version, and input row |

Prior art to learn from (and selectively reuse): `lifelib`/`modelx` (Python,
declarative cell graphs), JuliaActuary, `actxps`. None of these are a full
platform (distribution, governance, reporting, product breadth) — that gap is
the product.

## 2. Core design decisions

### 2.1 Model paradigm: declarative variable graph (Prophet's idea, done as code)

The winning abstraction — proven by Prophet — is *declarative time-indexed
variables*: each variable is one formula over projection time `t`; the engine
resolves calculation order from the dependency graph. This gives auditability
and lets us optimize execution freely. MoSes-style free-form procedural code is
the **escape hatch**, not the default.

A model is Python source using a thin DSL:

```python
class TermLife(Model):
    @var
    def lives_if(self, t):            # in-force lives
        if t == 0: return self.mp.init_lives
        return self.lives_if(t-1) * (1 - self.q_x(t-1)) * (1 - self.lapse(t-1))

    @var
    def death_claims(self, t):
        return self.lives_if(t) * self.q_x(t) * self.mp.sum_assured

    @var(assumption="mortality")
    def q_x(self, t): ...
```

Key properties:
- **Python as the surface syntax** — no bespoke language to learn, instant
  editor/tooling support, huge hiring pool. The DSL restricts what a `@var`
  body may do (pure, time-indexed, typed) so it stays compilable.
- The engine **traces/compiles** the graph; it does not naively interpret
  Python per model point (see §4).
- `@var` metadata declares assumption bindings, units, and output tags — this
  is what makes lineage and reporting automatic.

### 2.2 Execution model: vectorize across model points and scenarios

The unit of work is a **batch**: `(product model, model-point block, scenario
block, time axis)`. Formulas are compiled into array kernels operating on
`[n_modelpoints × n_scenarios]` slabs per time step. This is the single biggest
speed lever — Prophet/MoSes largely loop model point by model point.

### 2.3 Data layer: Arrow everywhere

- Model points, assumption tables, scenario files, and results are
  **Apache Arrow** in memory and **Parquet** at rest.
- Assumption sets are **versioned, immutable snapshots** (content-addressed).
  A run pins exact versions of model code + assumptions + inputs → perfect
  reproducibility, which is the backbone of the accuracy story.

### 2.4 Language/stack

| Layer | Choice | Rationale |
|---|---|---|
| Model authoring | Python DSL | Familiarity, ecosystem, hiring |
| Compiler + kernels v1 | Python → NumPy/Numba | Fastest path to a correct MVP |
| Kernels v2 (hot paths) | Rust (PyO3) or JAX/GPU | 10–50× on stochastic/nested runs; decide after profiling v1 |
| Orchestration / scale-out | Ray (or plain k8s jobs) | Shard by model-point × scenario blocks; embarrassingly parallel |
| Storage | Parquet + object store; Postgres for metadata/run registry | Boring and correct |
| API | FastAPI (REST) + Python SDK; gRPC later | Integration-first mandate |
| UI (later) | Web app for run monitoring, results explorer, assumption diffing | Not needed for MVP |

Rule: **make it correct in Python first, make it fast in Rust second.** Never
port an unvalidated formula.

## 3. Accuracy strategy (pillar #1)

Accuracy is a *process*, not a property. Build this in from day one:

1. **Closed-form golden tests.** Every primitive (annuity factors, `A_x`,
   `ä_x`, reserves under net-premium, unit-fund roll-forward) tested against
   textbook closed forms and published SOA/IFoA exam values.
2. **Reference-model reconciliation.** For each product template, maintain a
   small independent implementation (naive, slow, obviously-correct Python or
   spreadsheet) and require engine output = reference to tolerance (1e-10
   deterministic; statistical tests for stochastic).
3. **Incumbent parity harness.** Ingest result files from a client's existing
   Prophet/MoSes runs and produce automated reconciliation reports
   (per-variable, per-time-step, per-model-point diffs). This doubles as the
   **migration/sales tool**.
4. **Determinism guarantees.** Fixed reduction orders (pairwise/Kahan summation
   for large aggregations), pinned RNG streams per (scenario, model point),
   identical results across 1 core or 1,000 cores. Document float behavior;
   offer a slow exact-decimal audit mode for regulatory sign-off runs.
5. **CI regression.** Every commit re-runs the full golden suite; any numeric
   drift > tolerance fails the build and requires an explicit, reviewed
   "expected change" note (this is how model-change governance falls out for
   free).

## 4. Speed strategy (pillar #2)

Layered, in order of payoff:

1. **Vectorization** across model points × scenarios (§2.2) — typically 100×+
   over per-policy loops.
2. **Compilation**: trace the `@var` graph once per (model, time-structure),
   topologically sort, fuse into Numba/Rust kernels. Recursion over `t`
   becomes a forward loop over preallocated arrays.
3. **Parallelism**: shard batches across cores/nodes; results reduce as
   streaming aggregations. Target: linear scaling to hundreds of cores.
4. **Nested stochastic tactics** (the real killer workload — VA/VPLA hedging,
   VM-21, SII internal models): inner-loop vectorization, scenario reuse,
   proxy models (LSMC / neural surrogates) as an *optional, clearly-labeled*
   acceleration with error estimates.
5. **Model-point compression** (clustering) as an opt-in with quantified
   grouping error — never silently.
6. **GPU** for stochastic slabs once Rust/JAX kernels exist.

Benchmarks to publish (marketing = engineering here): e.g. "100k model points ×
1,000 scenarios × 60-year monthly projection in N minutes on M cores," with
Prophet-equivalent runtimes where clients can share them.

## 5. Product coverage (pillar #3)

"Anything an actuarial team throws at us" = **layered library + escape hatch**,
not an enumeration of products.

### 5.1 Layer 0 — primitives
Time axes (monthly/annual, policy/calendar), decrement tables & multi-decrement
math, select & ultimate mortality, improvement scales, lapse/dynamic lapse,
interest/discount curves, fund roll-forwards, expense & inflation, commission,
tax hooks, reinsurance (quota share, surplus, XoL), Kahan-safe aggregation.

### 5.2 Layer 1 — product templates (each ships with golden tests + docs)
- **Term / whole life / endowment** (net & gross premium reserves)
- **Universal life / interest-sensitive** (account-value mechanics, secondary guarantees)
- **Fixed & fixed-indexed annuities** (deferred/immediate, GLWB riders, index crediting)
- **Variable annuities / VPLA / unit-linked** — GMxB riders (GMDB/GMAB/GMIB/GMWB),
  fund mapping, dynamic hedging cashflows. *Seed this from the existing VPLA
  work — it's the hardest family and our differentiator credential.*
- **Payout annuities & pensions** (life-contingent, joint-life, buy-in/buy-out)
- **Health/protection riders** (CI, disability, waiver) — multi-state Markov engine
- **Group & credit life**
- Later: with-profits/par funds (asset shares, bonus mechanisms), takaful.

### 5.3 Layer 2 — reporting & regulatory overlays (products × frameworks)
- **IFRS 17** (GMM/VFA/PAA: CSM roll-forward, risk adjustment, coverage units) —
  potential tie-in with the existing `IFRSTool` repo
- **Solvency II** (BEL, risk margin, SCR standard formula stresses)
- **US STAT/GAAP-LDTI**, **VM-20/VM-21/VM-22** (VA/annuity reserves — pairs with
  the VA library)
- **Embedded value / ALM** overlays (asset models, liability-driven runs)

### 5.4 The escape hatch
Any `@var` may drop to plain Python (still traced for lineage, flagged
"custom") and custom products subclass templates or start from Layer 0. The
test: *a competent actuary can model a product we've never seen without waiting
for us to ship a library.*

## 6. Integration surface (the "competitor you can integrate" part)

- **Python SDK first-class**: `engine.run(model, modelpoints, assumptions,
  scenarios)` returns Arrow tables — usable from a notebook on day 1.
- **REST API** for run submission, status, results retrieval; webhook/event
  stream for orchestration tools (Airflow/Dagster/Prefect operators provided).
- **File-format adapters**: model-point CSV/Parquet, ESG scenario formats
  (Moody's/Conning-style tables), Prophet model-point & results file readers,
  MoSes I/O readers — *reading* incumbent formats is legal and is the migration
  on-ramp; we never need to write them.
- **Excel add-in** (later): submit runs & pull results from the tool actuaries
  will never give up.
- **Results warehouse**: standard star schema in Parquet/DB so BI tools
  (Power BI/Tableau) and the IFRS 17 tool consume outputs directly.

## 7. Governance & audit (enterprise table stakes)

- Git-native model versioning; run registry records (model commit, assumption
  snapshot, input hashes, engine version, seed) per run.
- Role-based access on assumptions/runs; 4-eyes approval flow for assumption
  changes (later phase).
- Auto-generated model documentation from `@var` docstrings + dependency graph
  visualizer (this replaces Prophet's formula browser).

## 8. Roadmap

### Phase 0 — Foundations (weeks 1–4)
- DSL spec (`@var`, model points, assumptions, time axes), engine architecture doc.
- Repo scaffold, CI, golden-test harness skeleton.
- Port/adapt the existing VPLA projection logic as the first *reference model*.
- **Exit:** term-life toy model runs end-to-end interpreted (slow) with golden tests passing.

### Phase 1 — Correct MVP (weeks 5–12)
- Graph tracer + NumPy/Numba vectorized executor; deterministic aggregation.
- Layer 0 primitives + templates: term life, whole life, fixed deferred annuity.
- Model-point/assumption/scenario Parquet I/O; run registry; Python SDK.
- **Exit:** 100k model points, deterministic 60y monthly projection, minutes on a laptop; results reconcile to reference models at 1e-10.

### Phase 2 — Stochastic + VA/VPLA (weeks 13–24)
- Scenario dimension in the executor; RNG discipline; ESG file adapters.
- VA/unit-linked library with GMxB riders (built on the VPLA work); dynamic lapse.
- Multi-node scale-out (Ray); benchmark suite published.
- **Exit:** VA block, 1k scenarios, nested-stochastic prototype; parity harness reconciling against an incumbent's run on sample data.

### Phase 3 — Breadth + reporting (months 7–12)
- UL/FIA, payout annuities, health multi-state, reinsurance.
- IFRS 17 overlay (integrate IFRSTool), SII BEL/stresses, VM-21 for the VA line.
- REST API + orchestrator operators; results warehouse schema; Rust/GPU hot paths where profiling justifies.
- **Exit:** a mid-size life office could run its main blocks and produce IFRS 17 inputs.

### Phase 4 — Platform & migration (year 2)
- Web UI (runs, results explorer, assumption diffs, graph visualizer).
- Prophet/MoSes migration tooling (readers + parity reports + conversion assist).
- Governance workflows, multi-tenant SaaS deployment, Excel add-in.

## 9. Risks

| Risk | Mitigation |
|---|---|
| Accuracy bug destroys credibility | Golden tests + parity harness before any marketing claim; determinism by construction |
| DSL too restrictive → users fight it | Escape hatch from day 1; dogfood on VA (the hardest product) early |
| Scope explosion ("all products") | Layered library; ship templates only with golden tests; say "custom via Layer 0" otherwise |
| Python too slow even vectorized | Numba first; Rust kernel port is planned, not a rescue |
| Incumbent lock-in (data formats, IT inertia) | Read-their-formats adapters + parity reports = low-risk pilot story |
| Regulatory acceptance | Reproducibility + auto-docs + audit trail are stronger than incumbents' — lead with it |

## 10. Immediate next steps

1. Locate/import the actual VPLA projection code (not in `VPLA_website`, which
   is only the auth front-end) — it becomes reference model #1.
2. Write the DSL spec as a short RFC in this repo (`docs/rfc-001-dsl.md`).
3. Scaffold the engine package (`engine/`), golden-test harness, CI.
4. Implement the term-life toy model end-to-end to force every architectural
   decision early.
