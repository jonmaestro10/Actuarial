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

   **Amended again by RFC-068 — there are three.** A template that reads
   `self.scenarios` cannot be handed `None`, and both deterministic
   executors hand it exactly that, so it runs under the stochastic executor
   and under no other:

   - **scenario class** — for scenario-bound templates: run-to-run
     determinism plus a *single-scenario bridge*, where `ScenarioSet.single(s)`
     run alone reproduces column `s` of the `(model point × scenario)` slab
     bitwise. A template can be in this class *and* the block class —
     `VariablePayoutAnnuity` is — and the bridge is then the assertion that
     the pooled reduction sweeps the block and not the slab.

   Nothing is weakened to a tolerance. B1's acceptance criterion is read
   against whichever class a template belongs to.
3. **Golden tests or it didn't happen.** New calculation code ships with
   closed-form or hand-computed golden tests in `tests/`, exact (`==`) where
   the mathematics is exact, `1e-12` reconciliation against an independent
   naive implementation otherwise. The suite (`pytest`, currently 2,574
   tests — 2,528 of them without the `[compile]` extra, whose 45 are
   RFC-072's bitwise measurement and RFC-074's compiled executor)
   must pass on every commit.
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
9a. **When CI cannot run, say which claim you are making.** The account's
   Actions minutes ran out during RFC-071 and a $0 spending limit turns that
   into *no run object at all* rather than a failure, so six items were merged
   on one interpreter. `python scripts/local_matrix.py` (RFC-077) is the
   substitute: it reads `.github/workflows/ci.yml` and runs every job under
   every version that file names, and a version it could not check **fails**
   rather than going unmentioned. It is a strictly weaker claim than CI — one
   machine, one architecture, one libm, so it cannot see the cross-machine
   float difference CI caught once. An item verified that way is marked
   **locally verified**, not done, and stays marked until a runner has run it.

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
| Pensions / longevity as products | ✅ shipped (C3, RFC-041): `PensionBuyout` on the payout-annuity chassis (deferment, revaluation, escalation, reversion) and `LongevitySwap` as a pooled model | Buy-in/buy-out, longevity swap templates | Same — here, that the two templates land in *different* executor equivalence classes, and that the class is a property of the product rather than the chassis | C3 |
| US health / LTC | ✅ shipped (C4, RFC-042): four-state chain with per-claim-state utilization and simple/compound inflation protection | LTC template on the multi-state engine | Same — here, that the benefit pool is not expressible over states at all, because it depends on when a claimant entered one rather than that they are in it | C4 |
| Regulatory track record / evidence | 🟡 evidence pack shipped (F1, RFC-049): test inventory, run equivalence attestation, coverage, parity records, digest-identical rebuild in CI | — | Machine-generated validation **evidence pack** — the closest software can get to a track record | F1 |
| Vendor library update cadence | ✅ shipped (F2, RFC-050): `engine/report/regdiff.py` | — | Regulation-as-dated-sets diff reports — per-module deltas, per-clause forward *and* backward drivers, and a named residual, because the clauses provably do not add up | F2 |
| Exact-decimal audit mode (PLAN §3.4 promise) | ❌ | — | Decimal sign-off executor; no incumbent offers one | F3 |
| General insurance beyond chain-ladder LIC | ✅ shipped (C5, RFC-054) | Reserve variability (Mack, ODP bootstrap) + premium-liability template | Reserve *ranges* reproduced against published triangles in CI — Igloo/ResQ assert them, we prove them | C5 |
| Takaful | ✅ shipped (C6, RFC-055): `FamilyTakaful` on the hybrid wakala–mudarabah model | Wakala/mudarabah template on the with-profits chassis | Surplus distribution and qard hasan golden-tested in the open — and two findings beyond that: the mudarabah share is a **call option** rather than a fee, so volatility at a fixed mean moves money from participants to operator; and the qard's generational transfer is exactly `distribution_rate × qard_repaid`, which is why it never appears in an operator's accounts as anything but a smaller distribution | C6 |
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

### B1 — The compiled executor (RFC-037) — effort L — **done**

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

**The prior question, answered (RFC-072, F8).** That assessment took the
translation problem as the first obstacle. It is the second. The first is
whether compiled code returns the same *bits* NumPy does — because §1.2 asks
for bitwise equality, and if the answer were no then no amount of DSL
translation would produce an executor that could join the class. It was
never measured. It is now, and the answer **determines the design rather
than merely permitting it**:

- **`+ − × ÷ sqrt`, comparison, `floor`/`ceil`/`rint`/`copysign`, `where`:
  bitwise, by specification.** IEEE-754 §5 requires them correctly rounded,
  so two conforming implementations cannot disagree. This is not a property
  of Numba and will not move when the compiler is upgraded.
- **`exp`, `log`, `log1p`, `expm1`, `power` — including `x ** 3` — and
  `tan`: one ulp apart**, on ordinary finite data. §9.2 only *recommends*
  correct rounding for these and no library provides it. `sin`, `cos`,
  `arctan` happened to agree and are treated as unsafe anyway: agreement the
  standard does not require is a coincidence of two versions, and it would
  be withdrawn silently.
- **Reductions are order-dependent with no safe length.** First disagreement
  at **twelve** elements, and past that it depends on the values, not the
  length — 63 disagrees, 64 agrees, 128 disagrees. So `pool_sum` is never
  compiled, which puts every `@pool` body outside a kernel by arithmetic
  rather than by policy.

So the kernel may contain **only** correctly-rounded operations, and
everything else is hoisted into a NumPy-computed slab. That is not a
concession — it is the only arrangement under which §1.2 survives, and it is
the same architecture this item already proposed for assumption lookups, now
required rather than convenient. It also lands where the performance is: the
library's transcendentals are overwhelmingly loop-invariant along the
model-point axis (a discount factor, a period conversion) or table gathers,
both wanting evaluation once per period; what is left in the recursion is
multiplication and subtraction. A survival chain compiled as a scalar loop
over a slab was measured **bitwise-identical and 2.7× faster** — a floor,
since it fuses nothing.

`engine/core/bitwise.py` carries the classification and refuses an
unclassified op by name. B1's remaining work is the translation, and it now
has a specification to translate *into*.

**First brick laid (RFC-073).** `engine/core/compiled.py` answers, per model,
the question an emitter has to answer first: which arithmetic goes inside a
kernel, which values must be handed in, and — if none of it can — which
operation is responsible. It emits a `CompilationPlan` and **not a kernel**,
deliberately: a plan can be checked against the model it describes, a kernel
only against its own output, and getting the plan right first gives the
kernel something to be wrong against.

Verdict across the deterministic catalogue: **13 of 14 plan cleanly**, the
exception being `GeneralInsurance` — and that refusal was real information
rather than noise. It used `atleast_1d`, which was unclassified, and the fix
was not to wave it through but to notice that RFC-072's three categories were
missing a fourth. **Selection and reshaping perform no arithmetic**, so a
kernel may contain them for a different reason than IEEE-754's: not that the
standard pins their rounding, but that they do not round. `where` had been
sitting in the arithmetic set with a comment saying it was not arithmetic,
which is the observation that produced `STRUCTURAL`.

Also enforced, and worth its own line: **a `@var` body that branches on
traced data is refused rather than specialised.** The recorded tape would be
right for the batch it traced and wrong for the next block, which is RFC-070's
bug exactly — a conditional branch a particular batch never entered, surviving
three RFCs. It cannot survive a fourth.

**Shipped (RFC-074).** The emitter, the hoist slabs, the kernel cache and the
equivalence suite. **13 of the 14 deterministic templates compile and agree
bitwise** on every variable and period, shape and dtype asserted separately,
against both a chunked and an unchunked vectorized run. The fourteenth is
refused because every one of its variables is hoisted, so a kernel would be
the vectorized executor with extra steps.

**The speed result is two numbers, and both are published.** The kernel alone
is a median **14.6x** (range 5.4x to 261x), clearing the >=5x target. End to
end the median is **1.36x** (range 0.92x to 9.94x), and the reason is Amdahl's
law rather than a defect in the fusion: the hoist pre-pass is a median 55% of
the vectorized runtime and the kernel cannot remove it. On `PayoutAnnuity` the
pre-pass is slower than the entire vectorized run and the compiled path is a
net loss. Reported rather than tuned away — the honest claim is that the fused
arithmetic is worth an order of magnitude and most templates today spend more
time outside it than in it.

**The next piece of work is named by the measurement**, not guessed:
interleave the pre-pass with the kernel per period, so a hoisted variable is
computed from the kernel's own slabs rather than from a second traversal that
recomputes the fused variables as dependencies.

Two findings worth carrying forward. **A variable is hoisted whole, never in
part** — a sub-expression is an anonymous intermediate with no name that
survives to run time, and fixing that alone took coverage from 2 of 14 to 13
of 14. And **pooled models are hoisted, not refused**: this is an array
executor, `pool_sum` classifies as a reduction and is hoisted, so the
reduction happens in the vectorized executor over the real block. Pooling
costs fusion, not correctness.

**Accept:** every template in `engine/library/` bitwise-identical across
interpreted / vectorized / compiled — read against §1.2's two classes, so
for the two pooled templates the target is vectorized ≡ compiled plus the
single-point bridge (RFC-061); `scripts/benchmark_compiled.py` extends
the benchmark family; target ≥5× over the vectorized executor on the
100k × 60y benchmark, actual numbers published in the RFC (per PLAN:
marketing = engineering). If any op resists bitwise reproduction under Numba,
the RFC documents the op and the replacement chosen — the tolerance does not
move (§1.2).

### B2 — Cross-machine dispatch (RFC-038) — effort L — **done**

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

**Outcome (RFC-075).** Built, and the claim above needed correcting. A
dispatched run is bitwise identical to an undispatched one at every shard
count — tested at 1, 2, 3, 5, 8 and 37 shards over 37 model points. But
"**any topology**" is wrong: RFC-072 measured that the transcendental library
is implementation-defined to within an ulp, so a shard on an AVX-512 worker
and one on an older core can disagree in the last bit, and the concatenated
answer would then depend on which worker got which shard.

So the guarantee is now stated where it holds — **bitwise across workers that
attest the same arithmetic** — and enforced rather than caveated. Every worker
digests what its floating-point unit does to a fixed probe, and the
coordinator compares before reducing; unlike workers raise
`ArithmeticMismatch` naming the shards. Two digests, not one, because they
fail differently: `exact` (IEEE-754 §5, must agree) and `transcendental`
(§9.2, does not). Reproduced end to end with AVX-512 dispatch disabled: the
exact digest is identical and the transcendental one is not.

**The bug it nearly shipped with is the finding.** The first probe was nine
values and agreed with AVX-512 on and off — because NumPy dispatches its SIMD
kernels only above a length threshold, and the scalar path below it is the one
that does *not* vary. An attestation that agrees everywhere is the same as no
attestation, and it would have shipped looking like a safeguard. `PROBE_LENGTH`
is now 4096 with a test holding it above 1024.

**The shard tree is recorded, and its `run_id` is the undispatched one.**
That is the claim rather than an oversight: where a shard ran cannot move a
number, so a run split five ways and the same run split eight ways are the
same run and must share an identifier — putting the topology into the identity
would make a correctly reproduced answer look like a different one. The tree
sits beside the identity as the *evidence*, and a run made with
`require_matching_arithmetic=False` records `arithmetic="mixed"` because the
record is the one place that fact could otherwise not be recovered.

**Milestone M2 — "the unanswerable benchmark":** B1 + B2. Publish the
nested-stochastic numbers (the 20M-inner-cell benchmark, compiled, across
N workers) with the bitwise-reproducibility statement no incumbent can make.
**Not claimed yet** — B2's registry half is outstanding, and the statement
itself has changed shape: it is now "bitwise across workers that attest
alike", which is still a claim no incumbent makes and should be published in
those words rather than the original ones.

### B3 — GPU kernels (RFC-053) — effort L — **machinery built, device unmeasured**

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

**Outcome (RFC-076).** The backend seam, both guarantees as executable
contracts, and the reconciliation machinery are built and tested. **No CUDA
device was available**, so the CuPy path is unexercised — recorded at
`engine.core.gpu.DEVICE_STATUS` rather than left for a reader to infer from
skip markers, and printed first by the benchmark.

**The bound is measured without silicon, and that is principled rather than a
substitute.** What separates a device answer from a CPU answer is the order
partial sums are combined in, and that order is reproducible in NumPy:
`DeviceReduction` reduces in the block-wise tree a device uses, block width 32
so the tree has a warp's real shape. On the stochastic slabs B3 targets — up
to 200 million cells — the worst spread against NumPy's pairwise reduction is
**1.8e-15**, giving the 1e-12 target about **560x** headroom. The target is
safe, and a device run that missed it would be evidence of a defect rather
than of floating point.

**It corrects an intuition.** A device-shaped tree is *closer* to NumPy than a
naive sequential CPU loop is, because both are trees — the loop disagrees by
an order of magnitude more. Asserted, so the reasoning behind the bound cannot
quietly invert.

**And a benchmark that reported nothing looked like a strong result.** The
first version used a uniform-positive slab, which reduces to the same bits in
any order, and printed 0.00e+00 on every workload. Cancellation between large
opposite-signed partials is where reduction order actually shows.

**Still owed:** the device kernels, and the stochastic-slab profile this RFC
was meant to open with. RFC-074 profiled the deterministic path; the
equivalent for the scenario dimension is what a device would accelerate, and
measuring it needs a device to be worth anything.

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

### C3 — Pension risk transfer (RFC-041) — effort M — **done**
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

**Outcome (RFC-041).** Both choices made, and they went different ways —
which is the useful result, because it shows the class is a property of the
product rather than of the chassis. `PensionBuyout` is on the
`ValuationBasis` chassis and is therefore **vectorized-only**: the members
whose value dominates a scheme are the deferreds, whose value moves on the
improvement scale and the fractional-age split the annual-step templates do
not carry, so writing it on annual steps to buy membership of the bitwise
class would have been choosing the test over the product. `LongevitySwap`
is an **indemnity** swap — its floating leg is the scheme's own experience —
so `net_settlement` is a `@pool` and it lands in RFC-061's block class,
with `PooledBlockError` asserted rather than described. An index-based swap
settling per life would not be pooled; that is a different contract and
would be a different template, because a flag would put two equivalence
classes in one file.

Both consequences priced in as predicted. Neither template can carry an
`EXAMPLES` entry, so both are in `UNAVAILABLE` with reasons. That takes the
count to **eight of sixteen templates invisible to the evidence pack's
specimen set**, every one of them because the RFC-032 request schema cannot
express an assumption object — which is now the largest single gap in the
pack's coverage, and a schema item rather than a library one.

### C4 — US health / LTC (RFC-042) — effort M — **done**
`engine/library/long_term_care.py` on the multi-state engine
(`engine/data/multistate.py`, `engine/library/income_protection.py` is the
pattern): active → claim (home/facility) → dead, benefit-utilization and
inflation-protection mechanics.

**Outcome (RFC-042).** Shipped, and it arrived with a worked example on its
first commit because RFC-066's `assumptions.transitions` was built first —
which is what the sequencing was for. Four states, `active`/`home_care`/
`facility_care`/`dead`, with `progression` the flow that justifies the second
claim state.

Utilization is **per claim state**, because the asymmetry is the structure:
home-care claimants draw less than the cap, facility costs exceed it so the
maximum binds. Above 1 is refused — it would pay more than the policy
maximum, and it is what a cost-inflation factor mistaken for a utilization
rate looks like.

Simple and compound inflation protection are both carried, because they are
not a formatting choice: at 5% over thirty years, simple reaches 2.50× and
compound 4.32×, and they are 2% apart after five. Nearly double the benefit
for the same stated rate.

**The finding is what is *not* there.** The benefit pool — the lifetime cap
most LTC policies carry — depends on how long *this* claimant has been
claiming, not on the state they occupy, and occupancy is a headcount. That is
the second time this shape has come up: RFC-041 hit it with a spouse's
pension escalating from the date of death. Twice is a pattern worth naming,
and it is the multi-state counterpart of VM-22's reduce-then-aggregate. Both
honest workarounds are documented with their costs; neither is chosen, and
elimination periods are out for the same reason.

### C5 — General insurance beyond the chain-ladder LIC (RFC-054) — effort L — **done**
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

**Outcome, first half (RFC-054).** Shipped. Every figure in Mack's Table 3
reproduces exactly — `(80, 26, 19, 27, 29, 26, 22, 23, 29)` by accident year
and 13% in total — and the bootstrap's over-dispersion comes out at
φ = 52,601, the value the England–Verrall literature quotes. The check ran
the right way round: those targets were transcribed into
`tests/test_published_sources.py` *before* anything could compute them, with
a test asserting `mack_standard_error` did not exist, so there was no
implementation to tune the transcription toward.

The design point is that **estimation error and prediction error are
different numbers** and are close enough here to be mistaken for agreement:
the bootstrap gives 15% (estimation), Mack 13% (prediction), and the
bootstrap with its process step 16% (prediction). `process_variance`
defaults to `False` and the docstring says which number each setting
returns, because a process step added silently would let an estimation error
be quoted against a published prediction error and pass inspection. The
decomposition `15.4 ⊕ 5.3 = 16.3` is asserted, which is what distinguishes a
real process step from extra noise of about the right size. Mack's 13% and
the bootstrap's 16% are then left disagreeing: two models, not two estimates
of one number, and averaging them would make a modelling choice on the
user's behalf.

Also reported rather than assumed: the total error exceeds the accident
periods added in quadrature by 20%, because they share their development
factors. `quadrature_total` computes the wrong figure on purpose, the same
posture as `vm22.floor_outside_reserve`.

**Outcome, second half.** Shipped. `engine/library/general_insurance.py`
carries the liability for remaining coverage: written premium earning over
the cover period, the unearned premium reserve as a **residual** rather than
a second recursion, and the catastrophe load kept out of the attritional loss
ratio — rolling it in changes nothing about the expected cashflow, which is
why it is tempting, and destroys the only thing distinguishing two costs with
the same mean. Annual steps and scalar assumptions put it in §1.2's per-policy
**bitwise** class, so unlike C3 and C4 it owes the executor-equivalence check
and gets one, asserted with `np.array_equal`.

**Accept:** `tests/test_gi_reserving.py` reproduces published Mack/ODP
results; `tests/test_general_insurance.py` golden-tests the template and
passes the dual-executor equivalence suite; a PAA measurement of a GI block
appears in the worked examples.

### C6 — Takaful (RFC-055) — effort M — **done**
`engine/library/takaful.py` on the with-profits chassis
(`engine/library/with_profits.py` is the structural pattern: two funds and a
distribution rule). Model the participants' risk fund vs the shareholder
fund, wakala fee and/or mudarabah share as declared `@var`s, surplus
distribution to participants, and the qard hasan facility (shareholder loan
to a deficit fund, repaid from future surplus). Golden tests from
hand-computed miniature funds (`tests/test_takaful.py`); the sharp-edge
finding to look for: how the qard repayment ordering changes the split of
surplus between generations of participants.

**Outcome.** `FamilyTakaful` on the hybrid wakala–mudarabah model, and three
findings rather than the one asked for.

The **mudarabah share is a call option**, not a fee. A mudarib shares profit
and not loss, so the operator's take is `share × max(earned, 0)`: hold the
mean return fixed, raise the volatility, and the operator earns strictly
more while the participants earn strictly less by exactly as much. Measured
on a two-point set whose mean is exactly zero — at ±40% the operator takes
eight times what it takes at ±5%, on unchanged contract terms. Same shape as
`MonthlySum`'s cap, and invisible in any deterministic projection.

The **qard's generational transfer** was the finding asked for, and the
interesting part is what it collapses to:
`surplus_if_qard_ignored − distributable_surplus` is
`distribution_rate × qard_repaid` identically. So the transfer never appears
in an operator's accounts as a payment between generations — it appears as
a slightly smaller distribution, which is why nobody measures it. Computed
on purpose and fed to nothing, in the `floor_outside_reserve` habit.

**The third instance of the when-versus-what limit, and a fourth.** RFC-041
and RFC-042 both found that a quantity depending on *when* a life entered a
state is not expressible over states; §10 asked the next agent to watch for
a third. It is here and it is not in a state chain: **the qard cannot be
attributed to the cohorts whose claims drew it** — the loan is a property of
the fund, and a repayment at `t` cannot be traced to the deficit at `s`.
That it appeared in a pooled fund settles what the limit is a property of:
the *question*, not the multi-state engine. The fourth turned up at the
other end of the run — the risk fund outlives the participants, still
distributes, and has nobody to distribute to; `unallocated_surplus` reports
the residual and nothing allocates it, because the contract does not say who
gets it and practice does not agree.

Executor class, checked rather than assumed: `@pool` variables put it in
RFC-061's block class (`PooledBlockError` over a block, block-of-one bridge,
chunk-invariance) *and*, because the worked example binds scenarios, in
RFC-068's scenario class. Second template in both, after
`VariablePayoutAnnuity`. A deterministic fund is either always in surplus or
always in deficit and never draws a qard at all, which is why the specimen
is stochastic.

**Milestone M5 — "deeper than AXIS where it counts, wider than the field":**
C1–C6 shipped, each with its sharp-edge finding documented. **Reached.**

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

### F2 — Regulation diff reports (RFC-050) — effort S — **done**
Generalize the dated-sets pattern (market risk already carries 2015/35 *and*
2026/269): `engine/report/regdiff.py` runs one block under two dated texts
and reports per-module SCR deltas with drivers. The open answer to the
vendors' quarterly-library-update moat: regulation changes become a diffable,
testable artifact.

**Outcome (RFC-050).** Shipped, pulled forward from §8 after VM-22's
remediation raised it a third time. `regulation_diff` runs one block under
two dated texts and attributes the movement clause by clause — and the
design point is that **it does not add up**. RFC-026's measurement survives:
2026/269's clauses move a block by +1.35 one at a time and +6.49 together,
so `interaction` is a first-class number, `reconciles()` asserts clauses plus
interaction equal the total exactly, and `driver` returns `None` where the
interaction exceeds every clause — because naming the largest row there
reports an artefact of the decomposition as a fact about the text. Forward
and backward one-at-a-time effects are both reported, since their gap is a
clause's interaction with the rest and the case worth surfacing is the clause
that is inert alone and material in company.

**It answered half of the open question it was pulled forward for, in the
negative.** VM-20 Appendix 1.F was read: it does not give 16 scenarios, it
gives 16 descriptions of shocks to the **prescribed economic scenario
generator**, every one a function of the valuation-date yield curve and of
that generator's own state variables and standard errors. There is no table
to carry as a dated set; carrying them means implementing the generator,
which is a different and much larger item. Recorded in
`docs/sources/vm20-appendix-1f-scenarios.md`.
`engine/report/vm22.stochastic_exclusion_test` is unchanged and was already
the right shape — it takes the baseline and the adverse set as inputs.

**The other half stays open, and is the buildable one.** The question named
the prescribed *assumption sets* as well as the scenarios, and those are the
opposite answer: VM-22 §6.C prescribes eleven numeric tables and a
closed-form mortality basis, all dated and all exactly the shape
`DELEGATED_2015`/`DELEGATED_2026` already has. See
`docs/sources/vm22-section-6-prescribed-assumptions.md`. Two constraints for
whoever builds it: the NAIC's own square brackets around `[1.025]` and
`[2.5%]` mark figures still under discussion, so the dated-set pattern needs
a way to say *provisional*; and §3.C makes the standard projection amount
disclosure-only for year-end 2026, which is why it sequences behind reserve
arithmetic.

### F3 — Exact-decimal audit mode (RFC-051) — effort M — **done**
PLAN §3.4's unbuilt promise: the interpreted executor over
`decimal.Decimal` with a configured context, opt-in and slow, for sign-off
runs; the RFC documents the agreement bound against the float executors and
why it is what it is. No incumbent offers an exact-arithmetic mode at all.

**Outcome (RFC-051).** Built, and the bound the plan asked for now exists:
across the nine templates the mode can audit, the interpreted float executor
agrees with 34-digit decimal to between **1.1e-15 and 1.0e-13** relative,
worst case over every variable and every period. Roughly thirteen
trustworthy significant digits, measured rather than assumed.

Four things worth carrying forward.

**The conversion is the whole feature.** `0.035` is stored as
`0.03500000000000000333…`, and that error exists before any arithmetic
happens. Conversion goes through `Decimal(repr(x))`, which recovers what the
actuary wrote — not `Decimal(x)`, which preserves the binary value and would
defeat the point while still completing, still using decimal arithmetic and
still reporting 34 digits. Both readings are offered (`as_written`,
`as_stored`) because the gap between two such runs *is* the representation
error with arithmetic error held constant, and without both a discrepancy
has two candidate causes and no way to separate them.

**"Exact" has a horizon that depends on the inputs.** `(1 − q)^t` with
`q = 0.015` is genuinely exact — equal to an independently computed closed
form — only while `3t` fits the 34 digits of decimal128, i.e. `t <= 11`. At
`t = 13` the chain and the closed form part company. Neither is wrong, and
34 digits against double's 16 is still the point; but a sign-off pack is
exactly the document where a reader takes a label at face value, so the
limit is asserted rather than described.

**A bug this nearly shipped with, caught by asserting equality rather than a
tolerance.** The proxy first converted every argument down to float on its
way into the assumption layer. `Decrements.split` takes the *in-force count*,
so that put the whole survival chain back into float arithmetic while the
answer still arrived wearing `Decimal` — the run completed, the types were
right, the numbers were the float numbers. The rule now distinguishes by
value: integral arguments are lookup keys and go down to `int`; non-integral
arguments are quantities and pass through. It works because
`Decrements.split` is "deliberately uncoerced", a property RFC-004 chose for
an unrelated reason.

**Coverage is a partition, asserted in CI.** 9 audited, 8 outside (5 bind a
scenario set, 3 pooled or coupled — the interpreted executor cannot run
those either), 2 refused (`PayoutAnnuity`, `PensionBuyout`: the assumption
layer hands back arrays and a one-policy-at-a-time decimal run has nothing
to apply them to). Falling back to float there would produce a sign-off run
that was a float run wearing a label. The test also asserts **none of the
three buckets may empty out**, which is RFC-071's trap met from the other
direction.

### F4 — The findings catalogue (RFC-052) — effort S — **done**
The sharp-edge findings (counterparty band cliff, interest-SCR duration
matching, AoS ordering dependence, LDTI vs IFRS 17 timing, and RFC-061's
pool of one — a pooled model run per policy returned plausible numbers in
which every policy's pool was itself) are currently scattered through RFCs. Collect them: `docs/findings/` with one page per
finding, each backed by a runnable script under `scripts/findings/` asserted
in CI — a demonstrable audit-and-review capability, per landscape §4.4, and
sales collateral that is also a regression suite.

**Outcome (RFC-052).** Six findings catalogued, each a page in
`docs/findings/` and a script in `scripts/findings/`, with
`tests/test_findings.py` asserting the correspondence both ways and running
every demonstration. Two of F4's named findings — interest-SCR duration
matching (RFC-026) and LDTI versus IFRS 17 timing (RFC-015) — are **not**
catalogued, and the README says so rather than leaving a reader to notice.

Three things worth carrying forward.

**The claim is asserted in the test, not in the script.** A script that
asserted its own claim would pass in CI while proving nothing about the
engine, because the demonstration and the check would share every
assumption including the wrong ones. So `demonstrate()` computes and returns
numbers, and the test judges them. The scripts print for a human as well,
which is what makes them collateral rather than only tests.

**The correspondence fails in both directions.** A page without a script is
an unbacked claim — the state the catalogue exists to leave — and a script
without a page is a demonstration nobody can read. `CATALOGUED` pins the
slug set, because the parametrised cases would otherwise silently stop
running over anything: RFC-071's emptied-out refusal, met a third time.

**It caught a bug while being built, and the existing test had not.**
`representation_error.py` failed on its first run because RFC-051's
`as_stored` returned a bare `Decimal` rather than an `Exact`, so values read
that way could not meet the float literals in a `@var` body.
`tests/test_exact.py` covered that path and passed, because it asked only
for a variable whose body never meets one. The test checked the feature the
way its author was thinking about it; the demonstration used it the way a
user would.

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

## 9a. Workstream H — The documentation a buyer expects

The landscape doc's §5.6 point about trust assets applies to documentation as
directly as to support organisations: an incumbent arrives with an
installation guide, an architecture description, a user manual and a training
course, and a repository that arrives with an execution plan and 72 RFCs is
not obviously the same kind of artefact — however much better the RFCs are.

The RFCs are a *design record*, which is the right thing for them to be and
the wrong thing to hand a new developer or an evaluating actuary. They are
chronological, they argue with each other on purpose, and finding out how the
engine works from them means reading seventy documents in order. What is
missing is the orthogonal cut: how it is put together *now*, how to run it,
and how to use it.

Four items, each self-contained. None depends on any B, C, E, F or G item, so
they can be taken whenever a session suits them — and H1 is worth doing before
the next long piece of work rather than after.

### H1 — `CLAUDE.md`, the working agreement in the repo — effort S — **done**
The conventions an agent or a new developer needs *before* touching
anything, currently spread across §1 of this plan, the RFC house style, and
tribal knowledge that only exists in session prompts: the dependency
discipline (§1.4), the bitwise invariant (§1.2), the golden-test rule
(§1.3), the docstring floor, the "assert the refusals as well as the grants"
convention, the `tests/` layout rules (not a package; never import across
modules), the evidence-pack verification steps, and the commit-message
voice. **Build:** `CLAUDE.md` at the repo root.

**Accept:** a test asserts the file names every checked convention it claims
to cover — the same derivation-over-restatement rule the prescribed-assumption
provenance string earned, applied to a document that will otherwise drift the
moment §1 changes.

### H2 — Technology architecture — effort M — **done**
**Build:** `docs/architecture.md`. The layer map (`engine/core`, `data`,
`library`, `report`, `api`, `migrate`, `parity`) with the dependency rules
that hold between them and *why*; the three executors and the classes §1.2
splits them into; the `@var` graph and its evaluation model; where dated
regulatory data lives and how a dated set is fingerprinted; the run registry,
approvals and audit chain; and the deployment surfaces (REST, Excel add-in,
warehouse). With diagrams that are generated from the code where they can be
— an architecture diagram maintained by hand is a diagram that is wrong.

**Accept:** the layer-dependency claims are asserted by an import-graph test,
so the document cannot describe a boundary the code has stopped keeping.

### H3 — Developer README — effort S — **done**
The current `README.md` is 969 lines and is doing three jobs at once. Split
the developer half out: install, the extras and what each is for, running the
suite (including the `[compile]` extra's separate CI job), the benchmark
family, how to add a template, how to add a dated regulatory set, how to add
a finding to the catalogue, and the pre-push verification sequence.
**Build:** `docs/developing.md`, with `README.md` reduced to the front door.

**Accept:** every command the document quotes is executed in CI, so a stale
instruction fails the build rather than a new developer's afternoon.

### H4 — User guide — effort M — **done**
For the actuary rather than the developer. **Build:** `docs/user-guide.md`:
the model catalogue and what each template is for, assumption objects and
bases, running a valuation through the API and through Excel, reading a run
record and an evidence pack, the approval workflow, and — the section no
incumbent ships — **what the engine refuses to do and why**, drawn from the
findings catalogue (F4) and the prescribed-assumption refusals.

**Accept:** every worked example in the guide is one of the specimens the
evidence pack already runs, so the guide cannot show an example that does not
work.

---

**Outcome (workstream H).** All four shipped, and each acceptance criterion is
a test rather than a promise. `CLAUDE.md`, `docs/architecture.md`,
`docs/developing.md`, `docs/user-guide.md`, with `README.md` reduced to a
front door that routes by what the reader is trying to do.

Three things worth carrying forward.

**A document nothing checks is a document that drifts, and this repo has the
receipts.** A provenance string claimed two prescribed tables when seven were
carried; a docstring stated an exclusion the evidence pack had been
contradicting for months. Both were *enforcing* the error, because a test
asserted the stale text. So `tests/test_working_agreement.py` asserts that
every convention `CLAUDE.md` names is one something actually enforces — and
that the file contains **no figure that drifts**: no test count, no coverage
percentage. Those are cited, never copied.

**The architecture test found the layer map was untidier than the diagram
would have been.** `core` and `data` import each other — `core` needs
`data.modelpoints` to type an executor, `data.mortality` needs `core.dates` —
so the map draws them side by side rather than stacked, and the test pins
exactly which modules participate. The cycle can now be paid down
deliberately and cannot grow by accident. §1.4 itself came out clean: all
four NumPy-only layers import nothing else unconditionally, with numba, cupy
and pyarrow reached only behind guards.

**The user guide's best section is the one no incumbent ships**: what the
engine refuses to do and why, drawn from the findings catalogue and the
prescribed-assumption refusals — and every refusal it promises is asserted to
still happen.

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

*Next action for the implementing agent: **take one of the two measured items
below.** Workstream B (B1, B2, B3) and workstream H (H1–H4) have both landed.
What is left is G (platform operations), A3 (MoSes/RAFM readers, deliberately
gated on pilot demand), and two pieces of engineering that measurement has
already pointed at.

**CI is running again, and the first thing it did was find two defects.** The
outage's cause was settled and was never a repository fault: a free GitHub
plan, its included Actions minutes spent, and a default $0 spending limit that
turns exhaustion into *no run object created at all* rather than a failed run.
It resolved on the monthly reset. RFC-077 halved what a run costs — the
duplicate `push` + `pull_request` pair is gone and superseded runs are
cancelled — so the allowance now goes about twice as far. If it lapses again,
the options are a spending limit or making the repository public, where free
plans get unlimited Actions minutes.

**What the first green run changes.** Every item through F9 is now verified on
CI across 3.11, 3.12 and 3.13 — the six that were merged on one interpreter no
longer carry §1.9a's weaker claim. What the outage cost is recorded in F8's
correction and is worth reading before trusting any single-machine
measurement: the bitwise boundary had asserted that §9.2's operations *do*
differ from the compiler, which is true on an AVX-512 machine and false on the
runner, and it had been measured in exactly one place for the item's whole
life.

`python scripts/local_matrix.py` remains the pre-merge check — it runs every
job in `ci.yml` under every version it names and **fails on a version it could
not check**. It is a strictly weaker claim than CI, and F8's correction is the
worked example of the gap: one machine cannot see a difference that is a
property of the silicon.

Two ways a red is not a red: a superseded run reports `failure` with zero
failed jobs, and a job that never got a runner reports `cancelled` — check
whether a job ever *started*, and note that matrix fail-fast also cancels
siblings, which looks the same and is not.

Two pieces of engineering are named by measurement rather than guessed, and
either is a good first item. **B1's remaining order of magnitude**: the
kernel is a median 14.6x but end-to-end is 1.36x, because the hoist pre-pass
is a median 55% of the runtime — interleave the pre-pass with the kernel per
period so a hoisted variable is computed from the kernel's own slabs instead
of a second traversal. **B3's device measurement**: the machinery and both
guarantees are built and tested, and nothing has run on silicon.

After those, workstream G is the largest untouched block: G1 multi-tenant
packaging, G2 the SOC 2 substrate, G3 release cadence, G4 the pilot playbook.
A3 stays deliberately parked until a pilot asks for it.

For history, the item that unblocked the speed workstream: F8 — **read that assessment
before designing anything**, because the kernel's contents are now determined
by IEEE-754 rather than open: correctly-rounded operations only, everything
else hoisted into a NumPy-computed slab, `fastmath` off, no reduction at any
length. What remains is the DSL translation, unchanged in size but with a
specification to translate into.

Milestone M5 is reached — C1–C6 are all shipped, each with its sharp-edge
finding documented, the catalogue's nineteen templates all have a worked
example for the first time since RFC-032, and the evidence pack's
equivalence section is fully attested. F7 (RFC-071) closed the last §6.C item
that was an implementation task, so what remains on that thread is one letter
to the NAIC. Three things are open, plus the standing rules the last five
RFCs earned — and **two of those rules belong in B1's acceptance criteria**,
not merely in its RFC.

**1. The evidence pack's equivalence section is clean — keep it that way.**
This entry used to name the open work; there is none. RFC-068 read the
section closely and found five unattested templates that were two findings,
RFC-069 (F5) discharged the shape bug behind three of them, and RFC-070 (F6)
discharged the last two — which turned out not to be the "real limitation"
both earlier RFCs had recorded. **11 of 11 bitwise-identical, no unattested
row.**

What replaces it is a standing rule the three bugs earned. All of them were
a `setup()` or an executor written for the batch case and run against the
single-policy one, and every one produced **equal numbers with an unequal
contract** — a spurious `(1,)` axis, a missing one, an `int64` where the
array executors store `float64`. That failure mode is invisible to any test
that compares values, which is most of them. New per-policy tests should
assert shape, dtype and value **separately**; `tests/test_slab_binding.py`
and `tests/test_spouse_binding.py` are the pattern.

And the harder-won rule, from RFC-070: **a class boundary wants a
mechanism.** RFC-061's pooled class raises `PooledBlockError` and RFC-068's
scenario class cannot be handed `None`, so both are enforced. The one
boundary that rested on a docstring sentence — RFC-041's "vectorized and
stochastic only … a stated class, not an omission" — was wrong for as long
as it existed, and the pack had been contradicting it the whole time. Where
a class exclusion cannot be enforced, it is a test, not a paragraph.

RFC-071 adds a third to the pair, from the same family: **a refusal whose
condition has emptied out stops being asserted, silently.** Its category
refusal was tested by looping over `set(FX_CATEGORIES) -
set(FX_CATEGORIES_CARRIED)`; carrying the last three tables made that set
empty, and the loop would have gone on passing while asserting nothing. The
fix is the `test_the_reason_mechanism_still_answers_with_nothing_to_report`
pattern one level up: narrow the state inside the test so the mechanism has
to fire. Worth checking wherever a test iterates a difference of two sets
that the work is closing.

**2. One §6.C table remains uncarried, and it needs a human.** F7 (RFC-071)
carried the three structured-settlement *F<sub>x</sub>* sets, so §6.C is at
**ten of eleven**. What is left is **Table 6.5**, and it is not an
implementation task: it fails one of its own three worked examples under a
reading that reproduces the other two exactly, the reading is **exonerated**
(1.0% appears in no at-or-after-expiry row, so Example 3 cannot be produced
by any cell of the table), and 144 parameterised readings reproduce none of
it. The evidence is complete in `docs/sources/vm22-table-6-5-reading.md` and
`-published-record.md`, including the APF route and the sharpest form of the
question — *"under your intended rule, when does column B's printed 2.0%
after-expiry block ever apply?"* **Do not re-investigate it.** File the APF.

**3. B1, B2 and B3 have all landed** (RFC-074, RFC-075, RFC-076), and each
left a named next step rather than a tidy ending — B1's hoist-pre-pass
interleaving, B2's milestone M2 wording, B3's device measurement. The
paragraph below is kept because its two rules are what the work was held to.

The original entry: F8 (RFC-072) measured the thing its design turns on and the answer
is a specification: a kernel may contain **only** IEEE-754-correctly-rounded
operations, everything else hoisted into a NumPy-computed slab, `fastmath`
off, and no reduction at any length — which puts every `@pool` body outside a
kernel by arithmetic rather than by policy. `engine/core/bitwise.py` carries
the classification and refuses an unclassified op by name. What is left is
the DSL translation, unchanged in size.

Two standing rules belong in its **acceptance criteria** rather than its RFC.
RFC-070's: a compiled executor joining the bitwise class needs the class
boundary enforced by a mechanism, not asserted in a docstring. And
RFC-069/070's: its equality checks want shape and dtype asserted alongside
value — all three of F5/F6's bugs would have passed a value-only check, and
so would a kernel that returned the right numbers in the wrong dtype.

Three notes the last two runs put on the record and this one closes or
extends.

The **executor classification note** is now exercised three times and has
grown a class. §1.2 reads: per-policy (bitwise across both deterministic
executors), block (RFC-061 — pooled or coupling, bridged by a pool of one),
and **scenario** (RFC-068 — a template reading `self.scenarios` cannot be
handed `None`, bridged by a set of one). `VariablePayoutAnnuity` and
`FamilyTakaful` are in two classes each, which is what the bridges are for:
the single-scenario check is precisely the assertion that a pooled reduction
sweeps the block and not the slab.

The **request-schema gap** is closed. It was eight of sixteen templates
invisible to the evidence pack at C3, twelve of eighteen after E5 (RFC-066),
and is now nineteen of nineteen after E6 (RFC-068). `UNAVAILABLE` is empty
and is kept, empty, so the next template to outgrow the schema has somewhere
to say so — a partition the tests assert, rather than a count.

The **when-versus-what limit** now has four instances and its shape is
settled. RFC-041 (a spouse pension escalating from the date of death) and
RFC-042 (the LTC benefit pool) made it look like a property of the
multi-state engine. RFC-055 found it twice in a pooled fund with no state
chain in sight — the qard cannot be attributed to the cohorts whose claims
drew it, and the risk fund outlives the participants and has nobody left to
distribute to. So it is a property of the **question**: anything that asks
"when did this arise" of a state that records only "what is true now". Watch
for it as a question, not as an engine.

The dated-set gap that C1 and C2 both left on the record is **half answered
and half built** (RFC-067). F2 is built (RFC-050). VM-20 Appendix 1.F was
read and does not contain scenario data at all — it prescribes shocks to a
generator, so carrying it means building the generator. The prescribed
**assumption sets** were the carryable half, and
`engine/report/vm22_prescribed.py` now carries **ten of §6.C's eleven
tables** — 6.1 to 6.4 and 6.6 to 6.11 — with §6.C.2's expense rule and
§6.C.8's mortality formula over both of its base tables. `Provisional` is the
mechanism RFC-050 said the dated-set pattern lacked: the NAIC's own square
brackets around `[1.025]` and `[2.5%]` mark figures still under discussion,
and the flag is *derived* from the values rather than listed beside them.
The standard projection amount itself is still unbuilt — §3.C makes it
disclosure-only for 2026, which is why the assumptions land before the
calculation.

**Workstream H (documentation) is new and unstarted.** H1 (`CLAUDE.md`), H2
(architecture), H3 (developer README), H4 (user guide). None gates on
anything, and H1 is worth taking before the next long piece of work rather
than after — the conventions an agent needs before touching the repo
currently live in §1 of this plan and in session prompts, which is not where
a new developer looks.

Shipped so far: A1 (RFC-033), A2 (RFC-034), A4 (RFC-036) — milestone M1 —
F1 (RFC-049), D1–D3 + E1 (RFC-043, RFC-044, RFC-045, RFC-046) — milestone
M3 — E2, E3, E4 (RFC-047, RFC-048, RFC-056) — milestone M4 — C1–C6
(RFC-039, RFC-040, RFC-041, RFC-042, RFC-054, RFC-055) — **milestone M5** —
and F2 (RFC-050), with VM-22's remediation V1–V4 (RFC-062, RFC-063), the
two unplanned schema items E5 and E6 (RFC-066, RFC-068), the two
equivalence-attestation items F5 and F6 (RFC-069, RFC-070), and F7 and
F8 (RFC-071, RFC-072).*

### E5 — Assumption objects in the request schema (RFC-066) — effort S — **done**
Unplanned, and raised by C3. The RFC-032 request schema carried scalars and
a flat mortality table, which kept the whole `ValuationBasis` chassis — half
the catalogue — off the API and out of the evidence pack's specimen set.
`assumptions` is now a discriminated union on `kind`, defaulting to
`"scalar"` so no existing request changes meaning; that default is asserted
by fingerprint rather than by type, because a silent revaluation is what it
exists to prevent. Dates are coerced at the HTTP boundary on an exact
ISO-8601 match, and a string in that shape which is not a valid date is
refused rather than passed through.

**Extended before C4**, which would otherwise have added a ninth invisible
template. `IncomeProtection` binds a `TransitionMatrix` on the ordinary
`Assumptions`, so it needed no new `kind` — only `assumptions.transitions`,
an object-valued *field*. That distinction decides where the next one goes:
a basis is a kind, a field is a field. Twelve specimens now, up from eight,
and every remaining exclusion is the **same** reason — a bound scenario set,
or an index-crediting rule that reads one — asserted by a test so the list
cannot be padded with new excuses while staying the same length. C4's LTC
template lands on the multi-state engine with an example on day one.

### E6 — The bound scenario set in the request schema (RFC-068) — effort S — **done**
Unplanned, and the last of the reasons E5 left standing. `scenarios` is now
a **top-level request key** — a discriminated union on `kind` over
`explicit`, `flat` and `lognormal` — and `assumptions.index_credit` a second
object-valued field. Eighteen specimens, up from fourteen — nineteen once C6 landed; `UNAVAILABLE`
is empty for the first time since RFC-032 wrote it, and is kept rather than
deleted so the next template to outgrow the schema has somewhere to say so.

**Outcome.** Three things came out of it that the schema work itself did
not.

First, *where* it goes was the interesting question, and the engine already
answered it: `record_run` takes `scenarios` as a sibling of `assumptions`
and `RunRecord` carries `scenarios_digest` beside `assumptions_digest`, so a
scenario set is neither a `kind` nor an assumption field but a third thing.
E5's rule extends: a basis is a kind, a field is a field, and a run input
that is not an assumption is a request key.

Second, **a seed pins less than it looks like it pins**. `ScenarioSet`
fingerprints its values; `RunStore.identify` fingerprints the request. For a
generated set those diverge, because NumPy freezes only the legacy
`RandomState` stream and not `default_rng`'s — so the request digest
identifies a *recipe*. The run record's `scenarios_digest` is the identity
safe to cite, and the digest of the specimen set is pinned by a test,
because a moved stream would revalue five templates with the whole suite
still green. `scenarios.kind` therefore has **no default**, which is exactly
where it differs from E5's `assumptions.kind`: there was no prior meaning to
preserve, and a default would have chosen an identity for the caller.

Third, §1.2 gained a **third equivalence class** (see above). The
single-scenario bridge was already the test idiom in four modules; RFC-068
names it and the evidence pack now performs it, reporting eight of the
scenarios and saying so, rather than reporting the run as an error.

**And it read the pack's equivalence section closely**, which nothing had.
Five templates it cannot attest turn out to be two findings: three
(`GeneralInsurance`, `LongTermCare`, `LongevitySwap`'s bridge) are one shape
bug — a `setup()` slab read through `Model.at` gives a `(1,)` array under
the interpreted executor and a scalar elsewhere, so the digests differ with
every number identical — and two (`PayoutAnnuity`, `PensionBuyout`) are a
real limitation, the interpreted executor not handling a date-valued
model-point field. Both are pre-existing, neither is fixed here, and both
are named in RFC-068's last section. The shape bug became F5 (RFC-069),
below; the limitation is open item 1 above.

### F5 — The slab read per policy (RFC-069) — effort S — **done**
Unplanned, and raised by RFC-068's reading of the evidence pack. Three of
the five templates the pack could not attest were one shape bug: a
`(n_mp, n_periods)` slab built in `setup()` and read through `Model.at`
returned a `(1,)` array under the interpreted executor — where the model is
bound to a single model point and every other variable is a scalar — so
`record_run` digested `(1, n_t, n_mp)` against the vectorized
`(n_t, n_mp)` with every number identical.

**Outcome (RFC-069).** Fixed in `Model.at`, and the design point is that
**the discriminator is the binding, never the shape**. Both placements
RFC-068 named were, as stated, wrong: squeezing when the leading axis is
length one collapses the vectorized block of one and the `chunk_size=1`
chunk — where the `(1,)` slice is *correct*, because the axis is the block
— and `record_run` never introduced the axis, so it could only squeeze on
shape (the same trap one level up) while leaving `result.per_mp`
inconsistent for everything else that reads it, which turned out to be
real: `RunResult.aggregate` was returning one-element arrays, and so was
`GeneralInsurance.combined_ratio()` on a directly-built model. `Model.at`
already keys its scenario branch off model state rather than shape;
now the model-point branch does too — bound to a `ModelPointBatch` the
axis stays, bound to a single `ModelPoint` the column comes back as the
scalar it is, and a slab carrying more than one policy under a
single-point binding is *refused* with the population named rather than
read at its first row. No vectorized or stochastic bit moved: the batch
branch returns the identical object, and the suite's golden `==` tests
pass unedited. The pack's equivalence line moves from 7 of 11 to **9 of
11** bitwise, `LongevitySwap`'s single-point bridge from `False` to
`True`, and `tests/test_slab_binding.py` pins the grant, the trap not
taken, and both refusals — with value equality asserted separately from
shape equality, because §1.2 was never breached here, only unprovable.

### F6 — The exclusion that was a bug (RFC-070) — effort S — **done**
Unplanned, and the last two rows of the same reading. RFC-068 and RFC-069
both recorded `PayoutAnnuity` and `PensionBuyout` as a **real limitation** —
`TypeError: 'datetime.date' object is not iterable` under the interpreted
executor — wanting a decision between teaching the executor dates and
declaring the two outside the per-policy class. The decision taken was to
keep the class broad, and the premise turned out to be wrong.

**Outcome (RFC-070).** The interpreted executor handles dates and never
touched one. Three templates' `setup()` zipped model-point fields directly —
`zip(self.mp.spouse_dob, self.mp.dob, joint)` — which is an object array over
a batch and a bare `date` bound to one model point. Dates were incidental;
`sex` fails on the same line. It survived because the branch is
**conditional** on `np.any(joint > 0)`, so a policy with no survivor benefit
never reached it: `PayoutAnnuity`'s A1 and A2 have always run interpreted and
only A3, with its 60% reversion, failed.

Three things are worth carrying forward.

**An exclusion asserted by prose and by nothing else was not asserted.** Both
docstrings *stated* the class boundary as a design decision — RFC-041's
"vectorized and stochastic only … a stated class, not an omission" — and the
evidence pack disagreed with them the whole time, placing both templates in
the class and reporting the failure. RFC-041 read a performance argument (the
per-policy loop is what a `ValuationBasis` exists to avoid) as a correctness
one. RFC-061's and RFC-068's class boundaries are enforced by a mechanism —
a raised `PooledBlockError`, a `ScenarioSet` that cannot be `None` — and are
right; the one that rested on a sentence was wrong for as long as it existed.
That is now the standard: **a class boundary wants a mechanism, and where
there is none the claim is a test.**

**One helper, not three.** The repo already had the object-valued idiom in
`general_insurance.py` and the numeric one in three separate local `_field`
copies; only the object case was missed, in all three. `per_policy_field`
now lives in `engine/data/modelpoints.py` with `dtype` keyword-only and no
safe default for the non-numeric case, so a caller cannot reach for the
numeric one out of habit without seeing the other exists.

**A dtype was hiding underneath.** Removing the `TypeError` left
`PayoutAnnuity`'s block digests still unequal with every output equal:
`age` was `int64` interpreted and `float64` vectorized, because
`engine/core/vector.py` coerces into a `float64` slab and `runner.py` kept
whatever the formula returned. RFC-069's failure mode one layer down — the
executors disagreeing about a *contract* rather than arithmetic — and stacked
on the same template, which is why neither was visible until the other moved.
Coerced in `runner.py`, where `vector.py` states the same contract.

The pack's equivalence section now reads **11 of 11 bitwise-identical with no
unattested row**, for the first time. All three of these bugs have the same
shape — a `setup()` or an executor written for the batch and run against one
policy, producing *equal numbers with an unequal contract* — which is
invisible to any test that compares values. `tests/test_spouse_binding.py`
and `tests/test_slab_binding.py` both assert shape, dtype and value
separately for that reason.

### F7 — The three structured-settlement *F<sub>x</sub>* tables (RFC-071) — effort M — **done**
The last uncarried §6.C work that was an implementation task rather than a
question for the NAIC. RFC-067 carried seven of the eleven prescribed tables
and left four; three of those were simply unread, and the sentence it left
behind said why they were delicate rather than why they were absent.

**Outcome (RFC-071).** Tables 6.9, 6.10 and 6.11 are carried — 312 rows read,
309 transcribed, all 2,266 printed cells reproduced through `fx_factor`.
§6.C is at **ten of eleven**, and the remaining one is Table 6.5, which is
waiting on an APF and not on effort. The provenance string needed no editing:
RFC-067 made it derived, so moving three entries between `TABLES_CARRIED` and
`TABLES_NOT_CARRIED` re-derived "10 of 11" on its own. Its *verb* did need
deriving — with the absent count down to one, "the other 1 (Table 6.5) **are**
recorded" reads as a broken sentence, and a generated sentence that reads
wrong is one a reader stops trusting.

Four things are worth carrying forward.

**The second dimension is banded differently in the two tables, and the
boundaries they share are the trap.** Table 6.9 bands contract years
1–5/6–10/≥11; Tables 6.10 and 6.11 band them 1–10/11–20/21–30/≥31. They share
1 and **11**, and contract year 11 opens Table 6.9's *third* band and the
substandard tables' *second*. A band index computed against the wrong list is
in range and reads a real cell — 170% instead of 225% for a female aged 62 —
so `contract_year` is required for these categories and refused for the two
that have no such axis, and the wrong reading is **computed on purpose** in
the test rather than described.

**§6.C.8.iii projects a different base table, from a different year.**
§6.C.8.i and .ii project the 2012 IAM Basic Mortality Table from 2012;
§6.C.8.iii projects the **1983 IAM Table 'a'** (VM-M §1.M) from **2011**. At
a 2026 valuation *n* is 14 for one and 15 for the other, and `q (1 − G2)^n ×
F` returns an ordinary number under either. Neither table belongs here, but
*which* of them a category calls for does, so `FX_MORTALITY_BASIS` carries
the pairing and `projection_offset` derives the offset instead of leaving the
subtraction to the call site. This came from reading the prose above the
grid, which is where the last three sessions' findings have all come from.

**The substandard factors are lower than the standard ones, and Table 6.11 is
not monotone.** 55% against 300% at the youngest ages, because §6.C.8.iii
applies Actuarial Guideline IX-A's Constant Extra Death loading *before* the
factor — so the two multiply different rates and are not comparable, the
RFC-055 rule again. And 6.11's *male* columns fall from the 21–30 band to the
≥31 band at attained ages 2 to 6 and nowhere else in any of the three tables.
Both are the shape of finding that gets a correct transcription "corrected"
until it agrees with the intuition; both are asserted.

**A refusal whose difference has emptied out needs its mechanism asserted
directly.** `fx_factor` refuses a category §6.C.8 covers and this module has
not transcribed, and the test looped over `set(FX_CATEGORIES) -
set(FX_CATEGORIES_CARRIED)`. That set is now empty and the loop asserts
nothing while continuing to pass — the same trap as a parametrised test over
an empty list. The carried set is now narrowed inside the test and the
refusal has to still fire, with the "category the section does not have at
all" case asserted alongside so the narrowing cannot collapse the two errors
into one.

**On method.** The coordinate reader was calibrated against Tables 6.7 and
6.8 before it was trusted on anything uncarried — it reproduces
`_FX_ACCUMULATION` and `_FX_PAYOUT` cell for cell — and every page was
required to repeat its own banner and band headers, because each of these
tables spans four PDF pages and two of those pages carry the tail of one
table and the head of the next. A second, independent read by word
x-position agrees on all 312 rows; its first, naive version disagreed on 25,
every one of them on those two shared pages.

### F8 — The bitwise boundary, measured (RFC-072) — effort S — **done**
Unplanned, and the prior question B1 had never asked. B1's assessment named
the DSL-translation problem as the obstacle that kept it unstarted through
six RFCs. That is the *second* obstacle. The first is whether a compiled
executor could join the bitwise class at all — §1.2 asks for bitwise
equality, not closeness — and nobody had measured it.

**Outcome (RFC-072).** Measured, and the answer splits by **IEEE-754** rather
than by compiler. §5 requires `+ − × ÷ sqrt`, comparison and the rounding and
sign manipulations to be correctly rounded, so two conforming implementations
cannot disagree; §9.2 only *recommends* it for `exp`, `log`, `pow` and the
trigonometric functions, and no library provides it. NumPy 2.4.6 against
Numba 0.66.0 on one machine, on ordinary finite data: the first group is
bitwise, the second is one ulp apart. Reductions are a third case with no
safe length.

**Corrected by F9's first CI run.** "The second is one ulp apart" is what an
AVX-512 machine measures. NumPy dispatches SIMD kernels for the
transcendentals while the compiler calls libm; on a CPU without AVX-512 NumPy
falls back to the same scalar path and all seven **agree exactly**. The
assertion that they differ failed the first time CI ever ran the job, having
been measured in only one place for the item's whole life. The category is
justified by §9.2 — a specification — and not by the measurement, which is now
recorded rather than asserted; what the tests pin is that `compilable()`
refuses these operations however the measurement comes out. It is the same
fact as `REPRODUCIBILITY_SCOPE`'s met a third time: a *measurement* of bitwise
agreement is no more portable across microarchitectures than `np.exp` is.

Four things worth carrying forward.

**The design is now determined, not open.** A kernel may contain only
correctly-rounded operations; everything else is hoisted into a
NumPy-computed slab. That is the only arrangement under which §1.2 survives,
and it is the same architecture B1 already proposed for assumption lookups —
two independent arguments landing on one design. It also lands where the
performance is: the library's transcendentals are overwhelmingly
loop-invariant along the model-point axis or table gathers, and what is left
in the recursion is multiplication and subtraction. A survival chain compiled
as a scalar loop over a slab is bitwise-identical and 2.7× faster, which is a
floor rather than a result — it fuses nothing.

**It is one fact, met twice.** `np.exp` and `**` were already known not to be
bit-portable *across CPUs*, which is why `REPRODUCIBILITY_SCOPE` limits a
pack digest to one machine and why the worked examples carry literal scenario
values. The same gap in the same standard makes them non-portable across
*implementations* on one machine. Naming it as one fact is worth more than
two separate cautions, because it is the same operations both times.

**The tempting mitigations are the dangerous ones.** "Reduce only small
blocks in the kernel" has no threshold — the first disagreement is at twelve
elements and past that it depends on the values, not the length (63 differs,
64 agrees, 128 differs). And `x ** 3` looks exact and is not: NumPy
special-cases small integer exponents into repeated multiplication and a
compiler calls `pow`. Both would be right most of the time.

**A skipped measurement reads exactly like a passing one.** The 39
measurement cases need a compiler, and the main matrix deliberately does not
install one — an llvmlite download in front of every test run, blocking a
suite that does not depend on it. A separate `bitwise-boundary` job installs
`[compile]` and sets `REQUIRE_COMPILE_EXTRA`, which turns the skip into a
failure. Without it, an install step that half-succeeded would skip all 39
and report green. Same family as RFC-071's emptied-out refusal, and the same
answer: assert the mechanism, do not trust the condition.

**What this does not do.** It does not build the compiled executor. B1 stays
**not started**, with its remaining work — the DSL translation — unchanged in
size but now with a specification to translate into.

---

### F9 — The matrix that ran, and the versions nobody checked (RFC-077) — effort S — **done**
Unplanned, and forced. §1's definition of done opens with "full suite green
**on CI, every Python version**", and for six merged items that clause was
false with nothing in the repository saying so. The account's Actions minutes
ran out during RFC-071; a $0 spending limit turns exhaustion into *no run
object at all* rather than a failure; the only record was a paragraph in a
handoff note.

**Outcome (RFC-077).** Two things, one cheap and one that needed thought.

**The workflow was buying nothing with half its minutes.** It triggered on
`push: branches: ["**"]` *and* `pull_request`, so every push to a branch with
an open PR ran all four jobs twice against one commit. Restricting `push` to
`main` removes the duplicate and narrows coverage by zero commits, because
every branch here reaches `main` through a PR. A `concurrency` group keyed by
ref stops a force-pushed branch leaving superseded matrices running to
completion — exempting `main`, where cancelling would discard the only
evidence that a merged commit is green. What was deliberately *not* added is a
`paths-ignore` for documentation: `test_working_agreement.py`,
`test_documentation.py` and `test_architecture.py` assert against `CLAUDE.md`
and `docs/`, so a docs-only diff is not a no-op here and skipping CI on one
would skip exactly the tests it can break.

**The substitute's design problem is the silence, not the matrix.** Running
the suite under three interpreters is a shell loop. What makes
`scripts/local_matrix.py` worth an RFC is that a machine with only one of them
installed must not be able to produce a green report — the same shape as a
parametrised test over an empty list, and more dangerous, because one Python
is the normal state of a developer machine. So an unchecked version fails,
`--allow-uncovered` prints the gap in the same sentence as the verdict, and
two tests pin both to the **exit status** rather than the prose, because a
caller in a shell script reads the status and never the text. Versions, steps
and per-step `env` are read out of `ci.yml`, never restated: had `env` been
dropped, the local `bitwise-boundary` job would run without
`REQUIRE_COMPILE_EXTRA`, skip its measurement cases and report green —
reintroducing one layer out the precise failure that variable exists to
prevent.

**What it found, on first use.** Python 3.12 and 3.13 are green, evidence pack
byte-identical under each; nothing merged since RFC-071 had run on either. The
`bitwise-boundary` job is green with **zero skips** and `REQUIRE_COMPILE_EXTRA`
genuinely set — it had never executed anywhere. And the pack digest is
**identical across all three interpreters** despite 3.11 resolving numpy 2.4.6
and the others 2.5.1, the only byte differences being the recorded interpreter
version in `environment.json` and `index.md`. That corroborates by a wholly
different route the earlier finding that those two NumPy releases produce
byte-identical transcendental bit patterns. The heterogeneous-NumPy matrix is
no longer believed safe; it has been run.

**What it is not.** Not CI, and the summary says so on every run: one machine,
one architecture, one libm. `np.exp` and `**` are not bit-portable across
microarchitectures and a pack digest is an identity *on a machine*, so no
number of local interpreters substitutes for a second machine. A test asserts
the summary keeps saying it, because a green report is exactly where an
over-broad reproducibility claim would come back.
