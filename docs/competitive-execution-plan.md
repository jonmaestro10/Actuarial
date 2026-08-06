# Competitive Execution Plan: Closing the Gaps, Extending the Lead

*The working plan derived from [competitive-landscape.md](competitive-landscape.md)
(written against commit `9e986c1`, RFC-032). It turns that report's §5 ("where
the incumbents are ahead") and §4 ("where this repo is ahead") into an ordered
set of executable work items. Each item is scoped so an implementing agent can
pick it up cold: claim the RFC number, follow the conventions in §1, satisfy
the acceptance criteria, ship.*

---

## 1. Execution protocol (read first, applies to every item)

These are the repo's standing conventions. Every work item below inherits
them; the acceptance criteria assume them.

1. **One RFC per item, written first.** Claim the RFC number given in the
   item's header (numbers here continue from RFC-032; if a number is taken by
   the time you start, take the next free one and note it in the item's RFC).
   Match the house style: a titled essay (`# RFC-0NN: The X, and the Y`), a
   `Status:` line (`proposed` → `implemented` with module and test paths on
   completion), a `## Summary` quoting the PLAN.md or landscape-doc line the
   item discharges, then the two or three genuinely interesting design
   decisions — not a routing inventory.
2. **The bitwise dual-executor invariant is load-bearing.** Every product
   template must produce bitwise-identical results under the interpreted and
   vectorized executors (`engine/core/runner.py`, `engine/core/vector.py`).
   New executors (B1) join that equivalence class; new templates (C-items)
   must pass it; nothing may weaken it to a tolerance. If an operation cannot
   be made bitwise-reproducible, replace the operation, not the guarantee.

   **Amended by RFC-061 — there are two classes, not one.** A template
   declaring `@pool` variables (or setting `couples_model_points`) reduces
   across the model-point axis, and the interpreted executor evaluates one
   policy at a time, so it cannot make that reduction: `GroupLife` and
   `WithProfitsEndowment` are outside the per-policy class *by
   construction*, not in breach of it. The interpreted executor now refuses
   such a block rather than returning a pool of one, and the invariant reads:

   - **per-policy class** — interpreted ≡ vectorized ≡ (B1) compiled,
     bitwise, for every template that does not couple its model points;
   - **block class** — for pooled and coupling templates: vectorized ≡ (B1)
     compiled bitwise, plus chunk-invariance, run-to-run determinism, and a
     *single-model-point bridge* into the per-policy class, where a pool of
     one is the same reduction either way and both executors agree bitwise.

   Nothing is weakened to a tolerance. B1's acceptance criterion is read
   against whichever class a template belongs to.
3. **Golden tests or it didn't happen.** New calculation code ships with
   closed-form or hand-computed golden tests in `tests/`, exact (`==`) where
   the mathematics is exact, `1e-12` reconciliation against an independent
   naive implementation otherwise. The suite (`pytest`, currently 2,088
   tests) must pass on every commit.
4. **Dependency discipline.** `engine/core`, `engine/data`, `engine/library`,
   `engine/report` keep NumPy as the only runtime dependency. Anything else
   is an optional extra in `pyproject.toml` (`[api]` fastapi, `[data]`
   pyarrow; new: `[compile]`, `[excel]`) with import guarded at the module
   boundary and tests `skipif` the extra is absent.
5. **Docstring floor.** `modeldoc` coverage is measured and asserted (floor
   80.3%). New modules must not drop it; write docstrings as you go.
6. **Registry-first.** Any new artifact a run produces (parity report,
   approval record, evidence pack) is content-addressed and recorded through
   `engine/core/registry.py`, never as loose files with mutable names.
6a. **Check against somebody else's arithmetic where you can.** Golden
   tests written here are checks on an implementation; they cannot catch a
   misreading of the method, because the misreading reproduces perfectly
   across every implementation of it. `docs/sources/` logs published
   material with numbers in it — provenance, contents, and what each one
   holds up — and `tests/test_published_sources.py` asserts against those
   figures. Reading VM-22's actual text found three errors in RFC-039 that
   the item's own 35 tests all agreed with. Where a source is recorded but
   its primary text could not be retrieved, the log marks it *unverified*
   and nothing asserts against it.
7. **Scoreboard maintenance.** When an item flips a ❌/🟡 in
   `competitive-landscape.md` §3 to ✅/🟡, update that table row and this
   document's §2 inventory in the same commit.
8. **Commit style.** One item per commit series; messages in the repo's
   declarative-sentence style (e.g. "The parity report, and what a
   reconciliation owes a sceptic").
9. **Ship in the order of §10 unless blocked.** Items are independently
   shippable; do not start a second item before the first's RFC is
   `implemented` and CI is green.

---

## 2. Deficiency inventory

Every ❌/🟡 in the landscape doc's capability table, mapped to the work item
that discharges it — and, where we choose to, the move that goes *beyond*
what any incumbent ships rather than merely reaching parity.

| Deficiency (landscape §3/§5) | Today | Parity target | Beyond-parity move | Item |
|---|---|---|---|---|
| Incumbent migration tooling | 🟡 M1 shipped: parity core (A1, RFC-033), Prophet readers (A2, RFC-034), scaffold (A4, RFC-036); MoSes readers (A3) on pilot demand | Prophet/MoSes readers + parity reports | Reconciliation report as a content-addressed, registry-verified artifact — a *signed* pilot deliverable no vendor produces | A1–A4 |
| Compiled kernels | ❌ | Numba forward-loop kernels | Compiled executor joins the **bitwise** equivalence class — incumbents compile but never prove equivalence | B1 |
| Cross-machine scale-out | 🟡 one machine | Multi-machine dispatch | Bitwise-identical results regardless of grid topology, verified by the registry | B2 |
| Governance: RBAC, approvals | 🟡 token auth + four roles (D1, RFC-043) and digest-bound 4-eyes approval (D2, RFC-044) shipped | Roles + 4-eyes assumption approval | Approvals bind to content digests, not labels — an approval can never silently drift | D1–D2 |
| Production run operations | 🟡 digest-chained audit log + declarative run calendar shipped (D3, RFC-045) | Audit log + run calendar | Append-only audit log digest-chained like the registry | D3 |
| Results warehouse | ✅ star schema in partitioned Parquet with the run fingerprint on every fact row (E1, RFC-046) | Star schema in Parquet | Warehouse rows carry run fingerprints — every BI number traceable to a registered run | E1 |
| Excel integration | ✅ audit workbook writer (E2, RFC-047) and live add-in (E4, RFC-056) shipped | Workbook writer | Workbooks embed the run fingerprint and assumption digests on every sheet — and state, in the workbook, the precision the format costs, which no vendor's does | E2 |
| Production UI | ✅ runs list, seriatim drill-down, semantic assumption diff, artifact and evidence views shipped (E3, RFC-048) | Runs list, results explorer, assumption diff | Parity-report and lineage views the incumbents' UIs don't have — plus a diff that names the component that moved, and URLs that are citations because the run id is a content digest | E3 |
| VM-22 | ✅ shipped (C1, RFC-039), corrected against the 1 Jan 2026 text: §3.A sum over groups, §4.B.1 floor inside the CTE, §7.C.1 ratio over PV of benefits, CTE 70 and the 6.0% SERT cap carried with citations | 2026 VM-22 SRA for non-variable annuities | Ships with a documented sharp-edge finding, per the RFC-026/028 habit — here, that the prescribed floor placement is not bracketed by the two obvious ones, so seriatim reserving can be *less* conservative than aggregating | C1 |
| US statutory formulaic reserves + AAT | ✅ shipped (C2, RFC-040): the modified-premium family as one parameter, CRVM's cap, and cash-flow testing on RFC-016's deficiency roll | CRVM/net-premium + asset adequacy runner | Same — here, that first-year strain is exactly the cap's bite and vanishes discontinuously in slope where the cap stops binding | C2 |
| Pensions / longevity as products | ❌ | Buy-in/buy-out, longevity swap templates | Same | C3 |
| US health / LTC | ❌ | LTC template on the multi-state engine | Same | C4 |
| Regulatory track record / evidence | 🟡 evidence pack shipped (F1, RFC-049): test inventory, run equivalence attestation, coverage, parity records, digest-identical rebuild in CI | — | Machine-generated validation **evidence pack** — the closest software can get to a track record | F1 |
| Vendor library update cadence | ❌ (not software) | — | Regulation-as-dated-sets diff reports (generalize the 2015/35 vs 2026/269 pattern) | F2 |
| Exact-decimal audit mode (PLAN §3.4 promise) | ❌ | — | Decimal sign-off executor; no incumbent offers one | F3 |
| General insurance beyond chain-ladder LIC | 🟡 | Reserve variability (Mack, ODP bootstrap) + premium-liability template | Reserve *ranges* reproduced against published triangles in CI — Igloo/ResQ assert them, we prove them | C5 |
| Takaful | ❌ | Wakala/mudarabah template on the with-profits chassis | Surplus distribution and qard hasan golden-tested in the open | C6 |
| GPU kernels | ❌ | Stochastic slabs on GPU behind a `[gpu]` extra | Run-to-run determinism on-device plus an *asserted* CPU reconciliation bound — a stated posture no vendor gives | B3 |
| Live Excel add-in | ❌ | Submit runs and pull results from inside Excel | Every pulled block stamped with the run fingerprint — a spreadsheet that can prove where its numbers came from | E4 |
| Multi-tenant SaaS packaging | ❌ | Container images, per-tenant isolation, deploy blueprint | Tenant isolation asserted by tests, not by policy document | G1 |
| SOC 2 substrate | ❌ (organizational) | Control mapping + automated evidence collection | Audit evidence *generated* from the registry, audit log and evidence pack — not compiled by hand | G2 |
| Vendor support / update cadence | ❌ (not software) | Versioned releases, changelog discipline, regulatory-update calendar | Dated-set regulation tracking in the open (F2) on a published cadence | G3 |
| A rehearsed pilot process | ❌ | Documented pilot playbook, dry-run asserted in CI | The pilot itself is a reproducible artifact | G4 |

Nothing from the landscape report is deferred. The items previously parked —
general insurance, takaful, GPU kernels, the live Excel add-in, SaaS
packaging, the SOC 2 substrate — are in scope as B3, C5–C6, E4 and
workstream G (§9). The control against scope explosion is no longer
*exclusion* but *ordering*: §10's milestone gates place each of them after
the migration, performance and governance work they depend on.

---

## 3. Workstream A — Migration & parity (the commercially decisive gap)

Landscape §5.5 and §6.1: nobody replatforms without a reconciliation report,
and only the VPLA harness (`scripts/vpla_parity.py`) exists. This workstream
is first because it has the highest commercial leverage per unit of work and
because every later benchmark claim (B) is more credible with a parity story.

### A1 — The parity core (RFC-033) — effort M — **shipped**

Generalize the VPLA harness into a reusable diff engine.

**Build:** `engine/parity/` — `diff.py`, `report.py`, `__init__.py`.

- `ParitySpec`: pairs an engine `Results` object with an external results
  table (any columnar source; pyarrow behind the `[data]` guard, plain CSV
  without it), an explicit `mapping` (external column → engine variable —
  never guessed silently; unmapped columns are *reported*, not dropped), and
  a `TolerancePolicy` (per-variable absolute/relative, default `1e-10`
  deterministic, statistical for stochastic outputs).
- `diff(spec) -> ParityReport`: per-variable summary (max abs/rel deviation,
  worst model point, worst time step, count within tolerance) plus drill-down
  to individual cells.
- `ParityReport.to_markdown()` — the pilot deliverable — and registration in
  the run registry keyed by *both* digests (engine `results_digest`, external
  file content hash), so a reconciliation is itself reproducible and
  tamper-evident.
- Refactor `scripts/vpla_parity.py` onto the core, output unchanged.

**Accept:** `tests/test_parity.py` — engine-vs-itself yields an all-zero
report; a copy perturbed at known cells flags exactly those cells and no
others; tolerance policy honoured per variable; markdown renders; registry
entry round-trips. VPLA harness still passes against a checkout (manual,
documented in the RFC).

### A2 — Prophet readers (RFC-034) — effort M — **shipped**

**Build:** `engine/migrate/prophet.py` (new package `engine/migrate/`).

- `read_modelpoints(path, dialect) -> ModelPoints`: Prophet model-point files
  are delimited text with header metadata; real estates vary, so the reader
  is **dialect-driven** — a `ProphetDialect` dataclass (delimiter, header
  convention, type map, missing-value markers) with a default matching the
  publicly documented layout. Field names map onto the RFC-032
  `modelpoint_fields` catalogue; the mapping report lists every incumbent
  field consumed, renamed, or ignored — the same "state what a template
  needs" instinct, applied to ingestion.
- `read_results(path, dialect) -> table` suitable as the external side of a
  `ParitySpec`.
- Fixtures in `tests/fixtures/prophet/` are hand-authored to the documented
  format (we hold no proprietary files; the dialect mechanism is exactly what
  absorbs a real client's variant during a pilot).

**Accept:** `tests/test_migrate_prophet.py` — fixtures round-trip to
`ModelPoints` with correct dtypes; a fixture results file feeds A1 and
reconciles against an engine run of the matching template; malformed input
fails with a diagnostic naming line and column, never a silent skip.

### A3 — MoSes/RAFM readers (RFC-035) — effort M

Same shape as A2: `engine/migrate/moses.py`, dialect-driven, fixture-tested,
feeding the same `ParitySpec`. Lower priority than A2 (Prophet has the larger
installed base); do after A4 if pilot demand says so.

### A4 — Conversion scaffold (RFC-036) — effort M — **shipped**

**Build:** `engine/migrate/scaffold.py`.

- Input: an A2/A3 model-point read plus an incumbent results file's variable
  list. Output: an importable Python module containing a `Model` subclass
  skeleton — `@var` stubs for each incumbent variable, pre-mapped by
  name-similarity to the nearest existing template variable
  (`engine/library/*`), every mapping explicit in an emitted table with
  unmapped variables prominently listed — plus a ready-to-run `ParitySpec`
  config. The scaffold is a *starting point that tells the truth about what
  it doesn't know*, not a converter that pretends.

**Accept:** `tests/test_scaffold.py` — scaffold on the A2 fixtures produces a
module that imports, subclasses `Model`, and whose mapping table covers every
input variable; the emitted `ParitySpec` runs under A1.

**Milestone M1 — "the pilot story":** A1 + A2 + A4 shipped ✅ (RFC-033,
RFC-034, RFC-036). The landscape doc's migration row is 🟡 (✅ once a real
estate has been through it).

---

## 4. Workstream B — Performance: compilation and the grid

Landscape §5.1: the binding constraint for production nested-stochastic. The
graph and forward loop exist (`engine/core/graph.py`, `vector.py`); PLAN §4.2
and §4.3 are designed but unbuilt.

### B1 — The compiled executor (RFC-037) — effort L — **not started**

**Build:** `engine/core/compiled.py`; optional extra `[compile]` (Numba).

- Trace the `@var` graph (existing tracer), topologically sort, emit one
  Numba-jitted forward loop over preallocated `[n_mp × n_scen]` slabs per
  model — the same op *order* as the vectorized executor, `fastmath` off, so
  results are **bitwise** equal, not merely close. The compiled executor
  joins the dual-executor equivalence tests as a third member
  (parametrize the existing template-equivalence suite over executors;
  `skipif` Numba absent).
- Cache compiled kernels per (model class, time structure) keyed by the
  graph digest.

**Assessment (2026-08, before starting):** this item is larger than its
effort marker. Numba cannot compile a `@var` body as written — the bodies
call into assumption objects (`periodic_q`, `Decrements.split`, expense and
treaty bases), which are ordinary Python over tables and objects. Emitting a
jitted forward loop with *the same op order* therefore means translating the
DSL into a compilable form: an array-expression tape recorded off the
vectorized executor, or an AST transpiler over `@var` bodies with the
assumption lookups hoisted into precomputed slabs. Either is a project in
its own right, and neither can be half-shipped without weakening §1.2. It
was left unstarted rather than begun badly; F1 (whose dependency A1 was
already met) was taken next, per §10's own note that B2 does not require B1.

**Accept:** every template in `engine/library/` bitwise-identical across
interpreted / vectorized / compiled — read against §1.2's two classes, so
for the two pooled templates the target is vectorized ≡ compiled plus the
single-point bridge (RFC-061); `scripts/benchmark_compiled.py` extends
the benchmark family; target ≥5× over the vectorized executor on the
100k × 60y benchmark, actual numbers published in the RFC (per PLAN:
marketing = engineering). If any op resists bitwise reproduction under Numba,
the RFC documents the op and the replacement chosen — the tolerance does not
move (§1.2).

### B2 — Cross-machine dispatch (RFC-038) — effort L

**Build:** `engine/core/dispatch.py`, `engine/api/worker.py`.

- The insight that makes this small: runs are already idempotent,
  content-addressed questions (RFC-031's fingerprint identifiers).
  Cross-machine dispatch is therefore *submitting sub-runs to remote engine
  API instances and reducing* — no new job model. A coordinator splits the
  batch using the existing shard logic (`engine/core/parallel.py`), POSTs
  shards to registered worker URLs, and reduces results with the existing
  deterministic reduction **keyed by shard index, not arrival order** — so
  the answer is bitwise-identical for 1 machine or N, any topology, worker
  failures retried anywhere (idempotency makes retry safe by construction).
- Workers are just `engine.api` processes; no Ray, no k8s dependency (a Ray
  adapter can come later if demand appears; it is not on this plan's path).
- Registry records the shard digests under the parent run.

**Accept:** `tests/test_dispatch.py` — bitwise equality of a single-process
run vs the same run dispatched across ≥2 local worker processes (spawned in
the test, marked slow); a killed worker's shard is retried and the final
digest is unchanged; registry shows the shard tree.

**Milestone M2 — "the unanswerable benchmark":** B1 + B2. Publish the
nested-stochastic numbers (the 20M-inner-cell benchmark, compiled, across
N workers) with the bitwise-reproducibility statement no incumbent can make.

### B3 — GPU kernels (RFC-053) — effort L

Starts only after B1 ships: the profiling data from the compiled executor
decides which stochastic slabs justify a device (PLAN §4.6's original
gating), and the RFC opens with that data.

**Build:** `engine/core/gpu.py` behind a `[gpu]` extra. CuPy first — it
mirrors the NumPy op set the vectorized executor already emits — with JAX
recorded as the alternative and the choice justified from the B1 profile.
Target workloads: stochastic and nested-stochastic slabs (the ESG scenario
dimension in `engine/core/stochastic.py` / `nested.py`), not the
deterministic single-scenario path, which B1 already serves.

**The reproducibility posture, stated honestly:** GPU reductions do not in
general reproduce CPU float results bitwise, and §1.2 forbids weakening the
bitwise class — so the GPU executor does **not** join it. Instead it makes
two weaker guarantees, both asserted by tests rather than claimed: (a)
**run-to-run bitwise determinism on the same device** (fixed reduction
orders, pinned RNG streams per (scenario, model point) — no atomics-order
nondeterminism); (b) a **per-variable reconciliation bound against the
compiled CPU executor** (target 1e-12 relative on aggregates, the actual
achieved bound published in the RFC). The docs state plainly which
guarantee applies where. That stated posture is itself the beyond-parity
move: incumbents' grids publish no reproducibility statement at all.

**Accept:** `tests/test_gpu.py` (`skipif` no device; a CPU-fallback CuPy
path keeps the code imported and unit-tested in CI); the two guarantees
asserted; `scripts/benchmark_gpu.py` added to the benchmark family with
published numbers on the nested-stochastic workload vs B1.

---

## 5. Workstream C — Breadth where AXIS is deeper

Landscape §5.2. Each item follows the established overlay/template pattern:
RFC + module + golden tests + one documented sharp-edge finding (the RFC-026
counterparty-cliff habit is a product feature; keep it). These are
independent of A/B and can interleave; C1 is the most time-sensitive
(VM-22 is effective for 2026 valuations).

### C1 — VM-22 (RFC-039) — effort M — **shipped**
`engine/report/vm22.py`: the 2026 VM-22 framework for non-variable annuities
— stochastic reserve on the CTE machinery already in `engine/report/pbr.py`,
deterministic certification option, exclusion tests. Golden tests from
hand-computed miniature blocks (`tests/test_vm22.py`). Pairs with the FIA and
payout-annuity templates already in `engine/library/`.

**Shipped, then corrected against the text (RFC-039).** The first cut was
written without the Valuation Manual to hand and got three things wrong,
all now fixed and all more interesting than the original design: §3.A
makes the aggregate reserve a **sum over disjoint groups** (SR + DR +
formulaic), not VM-20's maximum over components of one block; §4.B.1 puts
the cash-surrender-value floor **inside** the tail — each scenario reserve
is floored *before* CTE 70 — so flooring outside understates; and §7.C.1's
ratio divides by the **present value of benefits**, not the baseline
reserve. CTE 70 (§3.D.2) and the 6.0% SERT cap (§7.C.1) are now carried
with citations; the company's materiality standard, which §7.C.1 takes the
lesser of, is still refused.

**The finding, sharpened by the correction: the prescribed ordering is not
bracketed by the two obvious ones.** Only `floor outside ≤ prescribed`
holds in general. Seriatim reserving — supposedly the conservative thing —
can come out *below* the prescribed aggregate: two contracts with scenario
reserves `[0,0,0,150]` and surrender value 100 each reserve 200 standalone
and 250 pooled, because the summed floor applied to the pooled reserve
creates a tail no individual contract had. The original finding survives
underneath: the floor can still eat the diversification benefit entirely,
so a block whose surrender value binds gets exactly zero credit for being
pooled however uncorrelated it is.

### C2 — US statutory formulaic reserves + AAT (RFC-040) — effort M — **shipped**
`engine/report/statutory.py`: CRVM / net-premium formulaic reserves (build on
`engine/library/reserves.py`), plus an asset-adequacy-testing runner joining
the liability projection to the existing asset side
(`engine/data/assets.py`, `engine/report/embedded_value.py` patterns).

**Shipped with a finding (RFC-040).** Writing a modified method as a pair
of net premiums collapses the whole family to one parameter — the expense
allowance — so net level, CRVM and full preliminary term are one reserve
function and three ways of choosing its argument, and the two extremes are
pinned against RFC-018's already-tested code rather than reimplemented.
CRVM is full preliminary term with that allowance capped, and the first
year's statutory strain then has a closed form: `V_1 = (E_fpt − E) ·
ä_{x+1:n−1} / ä_{x:n}` — **exactly the cap's bite**, and exactly zero for
every plan where the cap does not bind. The reserve is continuous across
that boundary but not differentiable, so a sensitivity measured where the
cap is inert predicts zero strain for a plan that has plenty; the suite
measures the slope on both sides rather than warning about it. Third
member of the counterparty-cliff family, after RFC-026 and RFC-039.

The asset-adequacy half reinvents nothing: cash-flow testing and a
principle-based reserve are the same accumulated-deficiency object, and
what differs is the reduction across scenarios — a maximum over a handful
of prescribed paths against a CTE over thousands. So the reduction is an
argument, both are available, and the result records which was used.

### C3 — Pension risk transfer (RFC-041) — effort M
`engine/library/pension_buyout.py` (buy-in/buy-out on the payout-annuity
chassis, joint-life, deferred members) and
`engine/library/longevity_swap.py` (fixed-leg vs floating-leg on a survival
index). Golden tests with closed-form joint-life annuity values.

**Which equivalence class these belong to — settle it first (noted after
C2, 2026-08).** §1.2 reads as though every new template must be bitwise
identical under the interpreted *and* vectorized executors. That is the
rule for the annual-step templates, and it is not automatically the rule
here, because "on the payout-annuity chassis" means inheriting
`PayoutAnnuity`'s design — a `TimeAxis` at a payment frequency, a
`ValuationBasis`, a `YieldCurve` — and that template **explicitly does not
support the interpreted executor** (see its module docstring: the
interpreted path is the per-policy loop the basis exists to avoid). It has
no worked example either, so it is not in the evidence pack's specimen set
(`default_specimens` walks `EXAMPLES`, which covers 8 of the 14
templates) and it is not among the 6 the attestation reports as bitwise.

So there are three groups in the library today, not two: the per-policy
bitwise class, RFC-061's block class (pooled/coupling), and the
basis-chassis templates that are vectorized-only by construction. A buy-out
built on that chassis joins the third; one written on annual steps and a
plain mortality table could join the first. **That is a design choice, not
a discovery**, and it decides whether C3 owes a bitwise equivalence test or
a documented statement of which executors it supports.

Two consequences worth pricing in either way. A template with no worked
example is invisible to the evidence pack, so if C3 wants its attestation
it needs an `EXAMPLES` entry — which the RFC-032 catalogue can only carry
if the request schema reaches its assumption objects. And a longevity swap
whose floating leg reduces across a *book's* realised survival is a pooled
template (`@pool`), which puts it in the block class regardless of chassis;
one that settles per life on an external published index is not.

Whichever is chosen, §1.2 is not weakened — the RFC states the class and
the tests assert against that class, exactly as RFC-061 did for the pooled
pair.

### C4 — US health / LTC (RFC-042) — effort M
`engine/library/long_term_care.py` on the multi-state engine
(`engine/data/multistate.py`, `engine/library/income_protection.py` is the
pattern): active → claim (home/facility) → dead, benefit-utilization and
inflation-protection mechanics.

### C5 — General insurance beyond the chain-ladder LIC (RFC-054) — effort L
The landscape doc names the market this repo doesn't address (Igloo, ResQ,
Tyche); the chain-ladder LIC (`engine/report/incurred_claims.py`) is the
seed. Two halves:
- **Reserve variability:** extend `incurred_claims.py` with Mack standard
  errors and an over-dispersed-Poisson bootstrap — golden-tested against the
  published Taylor–Ashe triangle results that every P&C text reproduces, so
  the reserve *ranges* (not just the point estimates) are machine-checked.
  Bootstrap RNG follows the engine's pinned-stream discipline, so the range
  itself is reproducible — which no reserving tool asserts.
- **Premium liabilities:** `engine/library/general_insurance.py` — a policy
  template for earned/unearned premium, earning patterns, expected loss and
  cat-load cashflows — pairing naturally with the existing PAA overlay
  (`engine/report/paa.py`), which was built for exactly these contracts.

**Accept:** `tests/test_gi_reserving.py` reproduces published Mack/ODP
results; `tests/test_general_insurance.py` golden-tests the template and
passes the dual-executor equivalence suite; a PAA measurement of a GI block
appears in the worked examples.

### C6 — Takaful (RFC-055) — effort M
`engine/library/takaful.py` on the with-profits chassis
(`engine/library/with_profits.py` is the structural pattern: two funds and a
distribution rule). Model the participants' risk fund vs the shareholder
fund, wakala fee and/or mudarabah share as declared `@var`s, surplus
distribution to participants, and the qard hasan facility (shareholder loan
to a deficit fund, repaid from future surplus). Golden tests from
hand-computed miniature funds (`tests/test_takaful.py`); the sharp-edge
finding to look for: how the qard repayment ordering changes the split of
surplus between generations of participants.

**Milestone M5 — "deeper than AXIS where it counts, wider than the field":**
C1–C6 shipped, each with its sharp-edge finding documented.

---

## 6. Workstream D — Governance (what gates real deployment)

Landscape §5.3. The run registry already provides the audit substrate; this
workstream adds the human-workflow layer. All of it lives behind the `[api]`
extra — the core stays a library.

### D1 — AuthN and roles (RFC-043) — effort M — **shipped**
`engine/api/auth.py`: token-based authentication (hashed tokens in a config
file; no new runtime dependency), four roles — *viewer* (read runs/results),
*runner* (submit), *approver* (D2), *admin* (principals). Every existing
route gains a role requirement; `tests/test_auth.py` exercises allowed and
denied per role. Unauthenticated mode remains the default for library/local
use — auth activates when a principals file is configured.

### D2 — Assumption approval, 4-eyes (RFC-044) — effort M — **shipped**
`engine/core/approvals.py` + API routes. An approval is a content-addressed
record `(assumption digest, approver, timestamp, note)` in the registry.
A run submitted in **approved mode** refuses any assumption set whose digest
lacks an approval; approver must differ from submitter (4-eyes). The design
point to state in the RFC: approval binds to the *digest*, so an identical
re-derived assumption set stays approved and any change — however small —
un-approves. That is stronger than every incumbent's label-based workflow.

### D3 — Audit log and run calendar (RFC-045) — effort S — **shipped**
Append-only, digest-chained audit log of API mutations (submit, approve,
principal change) — same tamper-evidence discipline as the registry. A
production run calendar: scheduled runs defined declaratively (cron
expression + frozen request fingerprint) executed by a worker script, not
by core.

**Milestone M3 — "deployable":** D1–D3 + E1 — **shipped** (RFC-043,
RFC-044, RFC-045, RFC-046). An insurer's model-risk function can point at
RBAC, 4-eyes, an audit log, and the registry.

---

## 7. Workstream E — Meeting actuaries where they are

Landscape §5.4 and §6.5.

### E1 — Results warehouse (RFC-046) — effort M — **shipped**
`engine/data/warehouse.py`: a star schema in partitioned Parquet —
`fact_cashflow(run_fingerprint, modelpoint_id, scenario, t, variable, value)`
with dimension tables for runs (fingerprint, model, assumption digests,
engine version), model points, and variables (units, tags from `@var`
metadata). A writer from `Results`, a documented DuckDB/Power BI/Tableau
consumption path. The beyond-parity move: every fact row carries the run
fingerprint, so any number in any downstream dashboard traces to a
registered, reproducible run.

### E2 — The Excel surface (RFC-047) — effort M — **shipped**
`engine/excel/workbook.py` behind a `[excel]` extra (openpyxl): a workbook
writer — run summary, per-variable aggregates, assumption snapshot sheet,
parity-report sheet (A1) — with the run fingerprint and assumption digests
stamped on every sheet. The workbook is what audit files actually contain;
it ships before the live add-in (E4) for that reason.

**Shipped with a finding (RFC-047).** A spreadsheet cannot carry a float64:
openpyxl serialises 16 significant digits where a round-trip needs 17,
Excel itself parses 15, and non-finite values are written as *empty cells*
— a blank in a claims column that reads as zero. So non-finite values are
written as text and counted, the precision limit is stated on the summary
sheet rather than left to be discovered, `as_written()` is public so a
caller can tell a serialisation artefact from a wrong number, and the
bit-exact record stays E1's Parquet, which the workbook points at. The
snapshot sheet digests per row, so a basis that differs differs in a *row*;
a parity report for another run, a snapshot of another basis, a detail
block wider than the Excel grid and two sheet names colliding at Excel's
31-character limit are all refused rather than written.

### E3 — Production UI (RFC-048) — effort L — **shipped**
Grow `engine/api/ui` from demo to product: a runs list with filter/search
over the registry; a results explorer (aggregate → variable → model-point
drill-down); an assumption diff screen (two snapshot digests → semantic
per-table diff, not a text diff); parity-report and evidence-pack views.
Same architecture rule as RFC-032: everything on the page is a call to the
documented REST API.

**Shipped (RFC-048), and it was mostly API.** Six new routes — the
filtered runs list, the single run with its request, the
variable/model-point drill-down, `POST /assumptions/diff`, `/artifacts` and
`/evidence` — plus four screens on them. The flattener moved into NumPy-only
`engine/core/snapshot.py`, so the workbook's snapshot sheet and the diff
route share one walker and cannot disagree about what a basis contains.

Three things worth carrying forward. **The diff is a join over subtree
digests**, so an unchanged mortality basis contributes nothing and a changed
rate is reported at `dynamic_lapse.base` — with the verdict taken from the
root digests rather than from the change list, so a bounded walk that finds
nothing still answers "they differ, and I cannot say where". **A selection
is not the run**: the drill-down response carries `partial`, because the
digest covers the whole block and a client checking one policy's column
against it would blame the engine. **The URL is a citation** — every view's
state is in the hash, applied on `hashchange` as well as on load, and a run
identifier is a content digest, so a pasted link cannot rot into different
numbers. Landscape §7.3 shaped the scope: the field's platform UI is a
*run-operations* UI (§7.3.1), the real results UX is the customer's own BI
tool over E1's warehouse rather than a charting product built here
(§7.3.2), and seriatim drill-down is what every vendor leads with, so it
populates the moment there is a run rather than waiting to be found
(§7.3.5).

### E4 — The live Excel add-in (RFC-056) — effort M — **shipped**
The tool actuaries will never give up, made a first-class client of the API
(PLAN §6's "Excel add-in (later)" — now). **Build:** `engine/excel/addin.py`
on xlwings, behind the same `[excel]` extra: submit a run from a sheet
(request built from named ranges), poll by fingerprint, pull aggregates and
model-point drill-downs into sheets. Authentication uses D1 tokens; every
pulled block is stamped with the run fingerprint and assumption digests in
adjacent cells, exactly as the E2 workbooks are — a live spreadsheet that
can still prove where its numbers came from. Depends on E2 (shared
formatting/stamping code) and D1 (auth).

**Accept:** `tests/test_excel_addin.py` exercises the request-building,
polling and stamping logic against a test API instance without requiring a
running Excel (xlwings mocked at the boundary); a manual smoke procedure
against real Excel is documented in the RFC.

**Shipped with a finding (RFC-056).** The failure a live sheet makes easy
is not a stale stamp but a *partially* stale block: refresh a 40-period
pull with a 10-period one and rows 12–42 of the old run stay where they
were, under the new run's heading and below the new run's fingerprint, with
nothing on screen to show it. So a block records its own extent in its own
stamp, and a write clears the extent the sheet says the previous block
occupied — driven by what is in the workbook, not by what the process
remembers, so it survives closing Excel and refreshing from another
machine. An unreadable extent stops rather than guessing a rectangle to
clear. The add-in imports no executor, no template and no assumption
object — asserted by a test over its own source — so a number it wrote came
from a registered run because there is nowhere else it could have come
from; and the transport is `urllib`, so `[excel]` gains no client
dependency beyond xlwings itself.

**Milestone M4 — "meeting actuaries where they are":** E2 + E3 + E4 —
**shipped** (RFC-047, RFC-048, RFC-056).

---

## 8. Workstream F — Extending the lead

Landscape §4 lists five places the repo is already ahead. These items widen
those leads into things no incumbent can answer. F1 should land early (after
A1 and B1) because it compounds: every subsequent item enriches the pack.

### F1 — The validation evidence pack (RFC-049) — effort M — **shipped**
`engine/report/evidence.py` + `scripts/evidence_pack.py`: one command emits a
content-addressed directory — the test inventory (collected live from
pytest), the closed-form identity list, the executor-equivalence attestation
(which executors, which templates, bitwise), docstring coverage, benchmark
provenance (machine, versions, numbers), parity reports on record, and the
registry digests that pin it all — with a generated Markdown index. This is
the direct counter to "the incumbents are audited and regulator-familiar"
(landscape §6's strategic risk): a machine-generated, re-runnable evidence
base for SII internal-model validation and VM-G governance. Accept: pack
builds in CI; rebuilding without code changes is digest-identical.

### F2 — Regulation diff reports (RFC-050) — effort S
Generalize the dated-sets pattern (market risk already carries 2015/35 *and*
2026/269): `engine/report/regdiff.py` runs one block under two dated texts
and reports per-module SCR deltas with drivers. The open answer to the
vendors' quarterly-library-update moat: regulation changes become a diffable,
testable artifact.

### F3 — Exact-decimal audit mode (RFC-051) — effort M
PLAN §3.4's unbuilt promise: the interpreted executor over
`decimal.Decimal` with a configured context, opt-in and slow, for sign-off
runs; the RFC documents the agreement bound against the float executors and
why it is what it is. No incumbent offers an exact-arithmetic mode at all.

### F4 — The findings catalogue (RFC-052) — effort S
The sharp-edge findings (counterparty band cliff, interest-SCR duration
matching, AoS ordering dependence, LDTI vs IFRS 17 timing, and RFC-061's
pool of one — a pooled model run per policy returned plausible numbers in
which every policy's pool was itself) are currently scattered through RFCs. Collect them: `docs/findings/` with one page per
finding, each backed by a runnable script under `scripts/findings/` asserted
in CI — a demonstrable audit-and-review capability, per landscape §4.4, and
sales collateral that is also a regression suite.

---

## 9. Workstream G — Platform operations & trust

The landscape doc's closing point (§5.6): the incumbents' actual moat is
trust assets that aren't software — support organisations, update cadences,
regulator familiarity, reference clients. Most of that is bought with time
and customers; this workstream builds every part of it that *can* be built
with commits, so that when the first customer arrives, the operational story
is already true.

### G1 — Multi-tenant SaaS packaging (RFC-057) — effort L
**Build:** a `deploy/` directory — Dockerfile for the API/worker images, a
docker-compose profile for single-box deployment, and a Helm chart for
k8s — plus the tenancy model in code: a tenant is a namespace prefix over
the registry, the warehouse partitioning (E1), and a principals file (D1);
`engine/api/tenancy.py` resolves the tenant from the authenticated principal
and scopes every route. The design rule to state in the RFC: **isolation is
asserted by tests, not by a policy document** — a tenant-A token can never
enumerate, read, or collide with tenant-B runs, even when both submit the
identical fingerprint (the fingerprint stays global and content-true; the
*visibility* of the run is what tenancy scopes). Depends on D1, E1.

**Accept:** `tests/test_tenancy.py` — cross-tenant reads denied per route;
identical submissions from two tenants deduplicate compute but not
visibility; the compose profile boots and serves a run end-to-end in a
smoke test (marked slow).

### G2 — SOC 2 substrate (RFC-058) — effort M
Certification is organizational, but the technical substrate an auditor asks
for is code, and most of it already exists — this item joins it up.
**Build:** `docs/compliance/soc2-controls.md` mapping each Trust Services
control to the mechanism that satisfies it (change management → CI +
golden-test drift gate; access control → D1; integrity → registry digests;
audit → D3's chained log); `engine/report/evidence.py` (F1) grows a
compliance section that *generates* the evidence — principal list and role
changes from the audit log, run approvals from D2, dependency-audit output —
so the audit binder is a build artifact, regenerable and digest-pinned. Add
an API hardening pass (rate limiting, security headers, `pip-audit` in CI).
Depends on D1–D3, F1.

**Accept:** the compliance section builds in CI; every control row in the
mapping names a test or generated artifact, and a CI check fails if a named
test disappears.

### G3 — Release & support cadence (RFC-059) — effort S
The open answer to "quarterly vendor library updates on a contractual
cadence." **Build:** semantic-versioned releases with a maintained
`CHANGELOG.md` in which every numeric-result change carries the
expected-change note PLAN §3.5 requires (the CI drift gate already forces
the note to exist — the changelog makes it public); a documented deprecation
policy; and a **regulatory-update calendar** (`docs/regulatory-calendar.md`)
listing the dated regulation sets carried (2015/35, 2026/269, VM-22 2026, …)
with their review dates — each future update landing as a new dated set plus
an F2 diff report, so "what changed and what it does to your numbers" is a
published artifact, not a support ticket.

**Accept:** first tagged release cut with the changelog; a CI check asserts
the changelog gained an entry whenever the golden-test expected values
changed; the calendar cross-references every dated set present in
`engine/report/`.

### G4 — The pilot playbook (RFC-060) — effort S
The A-workstream builds the tools; this makes the *process* a rehearsed,
reproducible artifact. **Build:** `docs/pilot-playbook.md` — the
step-by-step client pilot: ingest their model points (A2/A3), scaffold
(A4), reconcile (A1), hand over the parity report and the evidence pack
(F1), stand up the workbook/warehouse feeds (E1/E2) — with the roles, the
data-handling rules (client files never leave their environment; the
dialect fixtures we keep are synthetic), and the exit criteria for a pilot.
Plus `scripts/pilot_dryrun.py`: the whole playbook executed end-to-end
against the synthetic Prophet fixtures, asserted in CI — so the pilot has
been run a thousand times before it is run once. Depends on M1, F1.

**Accept:** the dry-run script passes in CI and its outputs (parity report,
evidence pack, workbook) are produced into a content-addressed directory.

**Milestone M6 — "operable, auditable, sellable":** G1–G4 shipped.

---

## 10. Sequencing

Priority order (from landscape §6, refined by dependency):

```
A1 → A2 → A4 → [M1]                        (migration: the sales on-ramp)
B1 → [F1] → B2 → [M2]                      (kernels, evidence pack, grid)
D1 → D2 → D3 → E1 → [M3]                   (governance + warehouse)
E2 → E3 → E4 → [M4]                        (Excel, UI, live add-in)
C1 → C2 → C3 → C4 → C5 → C6 → [M5]         (breadth; C1 may pull forward
                                            — VM-22 is effective 2026)
B3                                          (GPU: gated on B1's profile,
                                            schedule any time after M2)
G1 → G2 → G3 → G4 → [M6]                   (operations & trust)
F2, F3, F4                                  (interleave as slack allows)
A3 (MoSes readers)                          (schedule on pilot demand,
                                            no later than G4)
```

Hard dependencies only: A2/A3/A4 need A1; B2 benefits from B1 but does not
require it; B3 needs B1 (its RFC opens with B1's profiling data); F1 needs
A1 (parity reports) and is enriched by B1's attestation; E2's parity sheet
needs A1; E3's views need D1 (auth) and E1 (warehouse queries); E4 needs E2
and D1; G1 needs D1 and E1; G2 needs D1–D3 and F1; G4 needs M1 and F1.
Everything else is parallelizable — but per §1.9, execute serially in this
order unless there is a concrete reason not to.

## 11. Risks

| Risk | Mitigation |
|---|---|
| Numba cannot reproduce vectorized results bitwise for some op | §1.2: replace the op, not the guarantee; the RFC documents each case. Worst case the compiled executor ships covering the templates that do pass, with the rest interpreted — coverage stated, never fudged |
| Real Prophet/MoSes files diverge from fixtures | The dialect mechanism is the absorber; the first pilot's variant becomes a new fixture. Never claim format coverage beyond what fixtures prove |
| Governance layer grows a database | Resist: principals file + registry + append-only log. Postgres stays optional metadata (PLAN §2.4) until multi-user contention is real |
| Breadth (C) starves the platform work (A/B/D/E) | The milestone gates are the control: C-items other than VM-22 wait for M1–M3 unless a prospect requires one |
| Everything in scope → scope explosion by another name | Ordering replaces exclusion as the control (§2): B3, C5–C6, E4 and workstream G each gate on a named earlier milestone, and §1.9 forbids starting an item before its predecessor's RFC is `implemented` |
| GPU cannot join the bitwise class | Accepted and stated up front (B3): the bitwise class stays CPU-only; the GPU executor ships with on-device run-to-run determinism and an asserted CPU reconciliation bound, both tested — an honest posture, not a weakened guarantee |
| Tenancy bug leaks one client's runs to another | G1's rule — isolation asserted by per-route tests including the identical-fingerprint case — plus D3's audit log making any access visible after the fact |
| The cadence (G3) is promised but not kept | The CI checks make the cadence self-enforcing: drift without a changelog entry fails the build; a dated set without a calendar entry fails the cross-reference check |
| Evidence pack overclaims | The pack only reports what CI actually asserts; every line in it is generated from a passing test or a registered digest, never hand-written |

---

*Next action for the implementing agent: C3 (§5, pension risk transfer,
RFC-041) is the next item — `engine/library/pension_buyout.py` on the
payout-annuity chassis (joint-life, deferred members) and
`engine/library/longevity_swap.py` (fixed leg against a floating survival
index), with closed-form joint-life annuity values as the goldens. Read the
classification note below before writing the first `@var`. One dated-set gap
is on record from C1 and C2 and neither closed it: the prescribed
assumption sets and scenario paths (VM-22's Standard Projection Amount, the
prescribed cash-flow-testing scenarios). B1 (§4) remains unstarted and
carries a written assessment of why. Shipped so far: A1 (RFC-033), A2
(RFC-034), A4 (RFC-036) — milestone M1 — F1 (RFC-049), D1–D3 + E1
(RFC-043, RFC-044, RFC-045, RFC-046) — milestone M3 — E2, E3, E4
(RFC-047, RFC-048, RFC-056) — milestone M4 — and C1, C2 (RFC-039,
RFC-040).*
