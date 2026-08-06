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
3. **Golden tests or it didn't happen.** New calculation code ships with
   closed-form or hand-computed golden tests in `tests/`, exact (`==`) where
   the mathematics is exact, `1e-12` reconciliation against an independent
   naive implementation otherwise. The suite (`pytest`, currently 1,326
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
7. **Scoreboard maintenance.** When an item flips a ❌/🟡 in
   `competitive-landscape.md` §3 to ✅/🟡, update that table row and this
   document's §2 inventory in the same commit.
8. **Commit style.** One item per commit series; messages in the repo's
   declarative-sentence style (e.g. "The parity report, and what a
   reconciliation owes a sceptic").
9. **Ship in the order of §5 unless blocked.** Items are independently
   shippable; do not start a second item before the first's RFC is
   `implemented` and CI is green.

---

## 2. Deficiency inventory

Every ❌/🟡 in the landscape doc's capability table, mapped to the work item
that discharges it — and, where we choose to, the move that goes *beyond*
what any incumbent ships rather than merely reaching parity.

| Deficiency (landscape §3/§5) | Today | Parity target | Beyond-parity move | Item |
|---|---|---|---|---|
| Incumbent migration tooling | ❌ only VPLA harness | Prophet/MoSes readers + parity reports | Reconciliation report as a content-addressed, registry-verified artifact — a *signed* pilot deliverable no vendor produces | A1–A4 |
| Compiled kernels | ❌ | Numba forward-loop kernels | Compiled executor joins the **bitwise** equivalence class — incumbents compile but never prove equivalence | B1 |
| Cross-machine scale-out | 🟡 one machine | Multi-machine dispatch | Bitwise-identical results regardless of grid topology, verified by the registry | B2 |
| Governance: RBAC, approvals | ❌ | Roles + 4-eyes assumption approval | Approvals bind to content digests, not labels — an approval can never silently drift | D1–D2 |
| Production run operations | ❌ | Audit log + run calendar | Append-only audit log digest-chained like the registry | D3 |
| Results warehouse | ❌ | Star schema in Parquet | Warehouse rows carry run fingerprints — every BI number traceable to a registered run | E1 |
| Excel integration | ❌ | Workbook writer | Workbooks embed the run fingerprint and assumption digests on every sheet | E2 |
| Production UI | 🟡 demo only | Runs list, results explorer, assumption diff | Parity-report and lineage views the incumbents' UIs don't have | E3 |
| VM-22 | ❌ | 2026 VM-22 SRA for non-variable annuities | Ships with a documented sharp-edge finding, per the RFC-026/028 habit | C1 |
| US statutory formulaic reserves + AAT | ❌ | CRVM/net-premium + asset adequacy runner | Same | C2 |
| Pensions / longevity as products | ❌ | Buy-in/buy-out, longevity swap templates | Same | C3 |
| US health / LTC | ❌ | LTC template on the multi-state engine | Same | C4 |
| Regulatory track record / evidence | ❌ (not software) | — | Machine-generated validation **evidence pack** — the closest software can get to a track record | F1 |
| Vendor library update cadence | ❌ (not software) | — | Regulation-as-dated-sets diff reports (generalize the 2015/35 vs 2026/269 pattern) | F2 |
| Exact-decimal audit mode (PLAN §3.4 promise) | ❌ | — | Decimal sign-off executor; no incumbent offers one | F3 |

Explicitly **out of scope** for this plan (deferred, revisit after M5):
general insurance beyond the chain-ladder LIC, takaful, an Excel *add-in*
(the workbook writer ships first), GPU kernels (gated on B1 profiling),
multi-tenant SaaS packaging, SOC 2 certification (organizational, not code —
but D1–D3 build the technical substrate an auditor would ask for).

---

## 3. Workstream A — Migration & parity (the commercially decisive gap)

Landscape §5.5 and §6.1: nobody replatforms without a reconciliation report,
and only the VPLA harness (`scripts/vpla_parity.py`) exists. This workstream
is first because it has the highest commercial leverage per unit of work and
because every later benchmark claim (B) is more credible with a parity story.

### A1 — The parity core (RFC-033) — effort M

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

### A2 — Prophet readers (RFC-034) — effort M

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

### A4 — Conversion scaffold (RFC-036) — effort M

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

**Milestone M1 — "the pilot story":** A1 + A2 + A4 shipped. The landscape
doc's migration row flips ❌ → 🟡 (✅ once a real estate has been through it).

---

## 4. Workstream B — Performance: compilation and the grid

Landscape §5.1: the binding constraint for production nested-stochastic. The
graph and forward loop exist (`engine/core/graph.py`, `vector.py`); PLAN §4.2
and §4.3 are designed but unbuilt.

### B1 — The compiled executor (RFC-037) — effort L

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

**Accept:** every template in `engine/library/` bitwise-identical across
interpreted / vectorized / compiled; `scripts/benchmark_compiled.py` extends
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
  adapter can come later if demand appears — deferred list).
- Registry records the shard digests under the parent run.

**Accept:** `tests/test_dispatch.py` — bitwise equality of a single-process
run vs the same run dispatched across ≥2 local worker processes (spawned in
the test, marked slow); a killed worker's shard is retried and the final
digest is unchanged; registry shows the shard tree.

**Milestone M2 — "the unanswerable benchmark":** B1 + B2. Publish the
nested-stochastic numbers (the 20M-inner-cell benchmark, compiled, across
N workers) with the bitwise-reproducibility statement no incumbent can make.

---

## 5. Workstream C — Breadth where AXIS is deeper

Landscape §5.2. Each item follows the established overlay/template pattern:
RFC + module + golden tests + one documented sharp-edge finding (the RFC-026
counterparty-cliff habit is a product feature; keep it). These are
independent of A/B and can interleave; C1 is the most time-sensitive
(VM-22 is effective for 2026 valuations).

### C1 — VM-22 (RFC-039) — effort M
`engine/report/vm22.py`: the 2026 VM-22 framework for non-variable annuities
— stochastic reserve on the CTE machinery already in `engine/report/pbr.py`,
deterministic certification option, exclusion tests. Golden tests from
hand-computed miniature blocks (`tests/test_vm22.py`). Pairs with the FIA and
payout-annuity templates already in `engine/library/`.

### C2 — US statutory formulaic reserves + AAT (RFC-040) — effort M
`engine/report/statutory.py`: CRVM / net-premium formulaic reserves (build on
`engine/library/reserves.py`), plus an asset-adequacy-testing runner joining
the liability projection to the existing asset side
(`engine/data/assets.py`, `engine/report/embedded_value.py` patterns).

### C3 — Pension risk transfer (RFC-041) — effort M
`engine/library/pension_buyout.py` (buy-in/buy-out on the payout-annuity
chassis, joint-life, deferred members) and
`engine/library/longevity_swap.py` (fixed-leg vs floating-leg on a survival
index). Golden tests with closed-form joint-life annuity values.

### C4 — US health / LTC (RFC-042) — effort M
`engine/library/long_term_care.py` on the multi-state engine
(`engine/data/multistate.py`, `engine/library/income_protection.py` is the
pattern): active → claim (home/facility) → dead, benefit-utilization and
inflation-protection mechanics.

**Milestone M5 — "deeper than AXIS where it counts":** C1–C4 shipped, each
with its sharp-edge finding documented.

---

## 6. Workstream D — Governance (what gates real deployment)

Landscape §5.3. The run registry already provides the audit substrate; this
workstream adds the human-workflow layer. All of it lives behind the `[api]`
extra — the core stays a library.

### D1 — AuthN and roles (RFC-043) — effort M
`engine/api/auth.py`: token-based authentication (hashed tokens in a config
file; no new runtime dependency), four roles — *viewer* (read runs/results),
*runner* (submit), *approver* (D2), *admin* (principals). Every existing
route gains a role requirement; `tests/test_auth.py` exercises allowed and
denied per role. Unauthenticated mode remains the default for library/local
use — auth activates when a principals file is configured.

### D2 — Assumption approval, 4-eyes (RFC-044) — effort M
`engine/core/approvals.py` + API routes. An approval is a content-addressed
record `(assumption digest, approver, timestamp, note)` in the registry.
A run submitted in **approved mode** refuses any assumption set whose digest
lacks an approval; approver must differ from submitter (4-eyes). The design
point to state in the RFC: approval binds to the *digest*, so an identical
re-derived assumption set stays approved and any change — however small —
un-approves. That is stronger than every incumbent's label-based workflow.

### D3 — Audit log and run calendar (RFC-045) — effort S
Append-only, digest-chained audit log of API mutations (submit, approve,
principal change) — same tamper-evidence discipline as the registry. A
production run calendar: scheduled runs defined declaratively (cron
expression + frozen request fingerprint) executed by a worker script, not
by core.

**Milestone M3 — "deployable":** D1–D3 + E1. An insurer's model-risk
function can point at RBAC, 4-eyes, an audit log, and the registry.

---

## 7. Workstream E — Meeting actuaries where they are

Landscape §5.4 and §6.5.

### E1 — Results warehouse (RFC-046) — effort M
`engine/data/warehouse.py`: a star schema in partitioned Parquet —
`fact_cashflow(run_fingerprint, modelpoint_id, scenario, t, variable, value)`
with dimension tables for runs (fingerprint, model, assumption digests,
engine version), model points, and variables (units, tags from `@var`
metadata). A writer from `Results`, a documented DuckDB/Power BI/Tableau
consumption path. The beyond-parity move: every fact row carries the run
fingerprint, so any number in any downstream dashboard traces to a
registered, reproducible run.

### E2 — The Excel surface (RFC-047) — effort M
`engine/excel/workbook.py` behind a `[excel]` extra (openpyxl): a workbook
writer — run summary, per-variable aggregates, assumption snapshot sheet,
parity-report sheet (A1) — with the run fingerprint and assumption digests
stamped on every sheet. The add-in (live submit/pull from Excel) stays on
the deferred list; the workbook is what audit files actually contain.

### E3 — Production UI (RFC-048) — effort L
Grow `engine/api/ui` from demo to product: a runs list with filter/search
over the registry; a results explorer (aggregate → variable → model-point
drill-down); an assumption diff screen (two snapshot digests → semantic
per-table diff, not a text diff); parity-report and evidence-pack views.
Same architecture rule as RFC-032: everything on the page is a call to the
documented REST API.

**Milestone M4:** E2 + E3.

---

## 8. Workstream F — Extending the lead

Landscape §4 lists five places the repo is already ahead. These items widen
those leads into things no incumbent can answer. F1 should land early (after
A1 and B1) because it compounds: every subsequent item enriches the pack.

### F1 — The validation evidence pack (RFC-049) — effort M
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
matching, AoS ordering dependence, LDTI vs IFRS 17 timing) are currently
scattered through RFCs. Collect them: `docs/findings/` with one page per
finding, each backed by a runnable script under `scripts/findings/` asserted
in CI — a demonstrable audit-and-review capability, per landscape §4.4, and
sales collateral that is also a regression suite.

---

## 9. Sequencing

Priority order (from landscape §6, refined by dependency):

```
A1 → A2 → A4 → [M1]                        (migration: the sales on-ramp)
B1 → [F1] → B2 → [M2]                      (kernels, evidence pack, grid)
D1 → D2 → D3 → E1 → [M3]                   (governance + warehouse)
E2 → E3 → [M4]                             (Excel + UI)
C1 → C2 → C3 → C4 → [M5]                   (breadth; C1 may pull forward
                                            — VM-22 is effective 2026)
F2, F3, F4                                  (interleave as slack allows)
A3 (MoSes readers)                          (schedule on pilot demand)
```

Hard dependencies only: A2/A3/A4 need A1; B2 benefits from B1 but does not
require it; F1 needs A1 (parity reports) and is enriched by B1's attestation;
E2's parity sheet needs A1; E3's views need D1 (auth) and E1 (warehouse
queries). Everything else is parallelizable — but per §1.9, execute serially
in this order unless there is a concrete reason not to.

## 10. Risks

| Risk | Mitigation |
|---|---|
| Numba cannot reproduce vectorized results bitwise for some op | §1.2: replace the op, not the guarantee; the RFC documents each case. Worst case the compiled executor ships covering the templates that do pass, with the rest interpreted — coverage stated, never fudged |
| Real Prophet/MoSes files diverge from fixtures | The dialect mechanism is the absorber; the first pilot's variant becomes a new fixture. Never claim format coverage beyond what fixtures prove |
| Governance layer grows a database | Resist: principals file + registry + append-only log. Postgres stays optional metadata (PLAN §2.4) until multi-user contention is real |
| Breadth (C) starves the platform work (A/B/D/E) | The milestone gates are the control: C-items other than VM-22 wait for M1–M3 unless a prospect requires one |
| Evidence pack overclaims | The pack only reports what CI actually asserts; every line in it is generated from a passing test or a registered digest, never hand-written |

---

*Next action for the implementing agent: claim RFC-033 and begin A1 (§3),
following the protocol in §1.*
