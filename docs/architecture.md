# Technology architecture

*How the engine is put together **now**. The RFCs are a design record —
chronological, argumentative, and seventy-six documents long; this is the
orthogonal cut.*

---

## 1. The layer map

```
                    engine/api          REST, auth, approvals, the UI
                    engine/excel        workbook + live add-in
                    engine/migrate      Prophet/MoSes readers, scaffolding
                    engine/parity       reconciliation against an incumbent
                         │
                    engine/report       regulatory reporting, evidence pack
                         │
                    engine/library      the product templates
                         │
                    engine/core ⇄ engine/data
                    execution           model points, assumptions, bases
```

`core` holds the DSL, the dependency graph, the four executors, the run
registry, approvals and the audit chain. `data` holds everything a projection
is run *over*: model points, mortality and lapse bases, scenarios, yield
curves, tax and reinsurance.

### The dependency rules, and the one that is a cycle

**`core`, `data`, `library` and `report` keep NumPy as their only runtime
dependency.** This is §1.4 and it is enforced, not aspirational: pyarrow,
Numba and CuPy appear in those layers only behind guarded imports, and
`tests/test_architecture.py` fails if one reaches module level. It is what let
the engine run unchanged on a Python release the API layer could not
(2,023 tests green on 3.14 while pydantic could not import).

**`core` and `data` import each other**, and the map above draws them side by
side rather than stacked because that is the truth. `core` needs
`data.modelpoints` and `data.scenarios` to type what an executor consumes;
`data.mortality`, `rates`, `assets` and `loan` need `core.dates`. It is a
narrow, named cycle rather than a tangle, and the test pins exactly which
modules participate — so it can be paid down deliberately, and cannot grow by
accident.

**`report` reaches *up* into `api` in exactly one place**, and only behind a
guard: `evidence.py` imports the worked examples from `engine.api` because
that is where a runnable request per template already lives, and duplicating
them would create a second set to keep true. Without the `[api]` extra the
evidence pack says it attested nothing rather than inventing specimens. The
test asserts this edge is never unconditional.

---

## 2. The `@var` graph

A model is a class whose methods are decorated `@var`. Each is **one pure
formula over projection time `t`** — no I/O, no mutation, no dependence on
evaluation order. The engine owns order and caching; formulas own the
actuarial logic.

```python
class FixedAnnuity(Model):
    @var
    def pols_if(self, t):
        if t == 0:
            return self.mp.init_pols * 1.0
        return self.assumptions.decrements.split(
            self.pols_if(t - 1), self._decrements(t - 1))[1]
```

Evaluation is recursive into a memo, and the **dependency graph is recorded
by running, not by parsing**. That is not laziness: a static scan of
`pols_if` sees a helper method, and running it sees `q_x` and `lapse_rate`
being read through two layers of assumption object.

Every edge carries the **offset** between the period reading and the period
read — `0` within a period, `-1` for last period's value. That distinction is
the difference between a cycle and a recursion, and only same-period edges
are sorted and can form one.

**A `@pool` variable is one formula per *block* rather than per model point**:
it reduces across the model-point axis with `pool_sum`, so every policy sees
the same value. That is what a with-profits bonus declaration or an experience
refund needs, and what a per-policy formula cannot express.

**Indicator style is a requirement, not a convention.** A `@var` body must not
branch on model-point data — write `x * (age < 65)` rather than `if`. A
conditional that one batch never enters is a defect that survives every test
written against that batch, and it has cost this repo two RFCs.

---

## 3. The four executors, and the three classes

| executor | shape | module |
|---|---|---|
| **interpreted** | one model instance per model point | `core/runner.py` |
| **vectorized** | one instance over a `ModelPointBatch`, chunked | `core/vector.py` |
| **compiled** | the graph fused into a Numba forward loop | `core/compiled.py` |
| **stochastic** | model points × scenarios | `core/stochastic.py` |

The guarantee is **bitwise equality, never a tolerance** — but which
executors are compared depends on what a template does, and there are three
classes:

| class | membership | what is asserted |
|---|---|---|
| **per-policy** | templates that do not couple model points | interpreted ≡ vectorized ≡ compiled, bitwise |
| **block** | `@pool` or `couples_model_points` | vectorized ≡ compiled bitwise, plus chunk-invariance and a **single-model-point bridge** into the per-policy class |
| **scenario** | templates reading `self.scenarios` | run-to-run determinism plus a **single-scenario bridge** |

A template can be in two classes at once — `VariablePayoutAnnuity` is — and
the bridge is then the assertion that the pooled reduction sweeps the block
and not the scenario slab.

**The boundaries are mechanisms.** The interpreted executor *raises*
`PooledBlockError` rather than returning a pool of one; a scenario template
cannot be handed `None`. An exclusion that rested only on a docstring was
wrong for as long as it existed.

### What a kernel may contain

`core/bitwise.py` is the contract, and it is IEEE-754's rather than a
preference:

- **correctly rounded** (§5) — `+ − × ÷ √`, comparison, rounding and sign
  manipulation: a kernel may use these, because two conforming
  implementations cannot disagree;
- **structural** — selection, reshaping, indexing: allowed because they
  perform no arithmetic at all;
- **implementation-defined** (§9.2) — `exp`, `log`, `pow`, the trigonometric
  functions: **hoisted**, computed by NumPy and passed in;
- **order-dependent** — every reduction. Never compiled; there is no length
  at which one is order-independent.

The compiled executor therefore hoists a variable **whole** if any part of it
cannot be fused, and the generated loop is kept as readable source.

### Scaling out

`core/parallel.py` shards across cores; `core/dispatch.py` submits shards to
remote engine instances. Both reduce **by shard index, never arrival order**,
which is what makes the answer independent of who finished first.

Dispatch adds an **arithmetic attestation**: each worker digests what its
floating-point unit does to a fixed probe, and the coordinator refuses to
reduce across workers that disagree. The guarantee is bitwise *across workers
that attest alike* — because `exp` is not bit-portable between
microarchitectures, and a claim that ignored that would fail silently and only
sometimes.

`core/gpu.py` is a fourth destination that deliberately **does not join the
bitwise class**: a device reduces in a different order. It offers run-to-run
determinism and a published reconciliation bound instead.

---

## 4. Dated data, and why it is dated

Regulatory data is not configuration. A valuation is performed *under a text*,
texts are amended, and a module that bakes one in silently revalues last
year's business the moment it is upgraded.

So a prescribed set is a **dated object** carrying its own provenance —
`DELEGATED_2015`/`DELEGATED_2026` in `report/market_risk.py`,
`VM22_PRESCRIBED_2026` in `report/vm22_prescribed.py`. Two rules travel with
them:

- **A figure the text brackets stays identifiable.** NAIC drafting brackets a
  number still under discussion; `Provisional` is a `float` subclass so the
  arithmetic is unchanged and the standing travels with the value.
- **Coverage is derived, never restated.** The provenance string is built from
  the table inventory, because it had said "two tables carried" while seven
  were — and the test asserting it was enforcing the error.

Published figures live in `docs/sources/` with full provenance and are
asserted in `tests/test_published_sources.py`. Golden tests check an
implementation; they cannot catch a misreading of the method, because the
misreading reproduces perfectly across every implementation of it.

---

## 5. Evidence: registry, approvals, audit

Every run is an **idempotent, content-addressed question**. `RunRecord.run_id`
fingerprints the model source, the assumptions, the model points and the
horizon; `results_digest` fingerprints the answer. Two runs with the same
`run_id` and different digests are a non-deterministic engine, and the
registry says so.

What is deliberately **not** in the identity is as informative as what is:
chunk size, and the dispatch topology. Both provably cannot change a number,
so a run split five ways and the same run split eight ways must share an
identifier — putting topology into identity would make a correctly reproduced
answer look like a different one. The shard tree is recorded *beside* the
identity as the evidence.

`core/approvals.py` holds four-eyes sign-off keyed by assumption digest;
`core/audit.py` an append-only chain. `report/evidence.py` assembles the
validation pack, which reports only what CI actually asserts.

**A pack digest is an identity on a machine, not across machines** —
`REPRODUCIBILITY_SCOPE` says so, and it is why worked examples carry literal
scenario values rather than a seeded generator.

---

## 6. Surfaces

`engine/api` serves REST with auth, roles and the demo UI. `engine/excel`
produces workbooks and a live add-in. `engine/migrate` reads Prophet and
MoSes models; `engine/parity` reconciles against an incumbent's own output.
`engine/data/warehouse.py` writes results for BI tools.

All four are optional extras. None of them can be reached from the layers a
projection actually runs in — which is the point of §1.4, and the reason the
engine outlives its dependencies.
