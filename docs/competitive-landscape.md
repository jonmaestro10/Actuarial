# Competitive Landscape: This Engine vs. Prophet, MoSes and the Field

*A review of the capabilities of the incumbent actuarial modelling platforms —
FIS Prophet, the MoSes lineage (WTW RiskAgility FM, RNA Analytics R³S),
Moody's AXIS, Milliman MG-ALFA/Integrate — and the newer cloud-native and
open-source entrants, compared against what this repository has actually
built. Written August 2026, against the repo at RFC-032 (commit `9e986c1`).*

---

## 1. Executive summary

This repository set out (PLAN.md) to build "a Prophet / MoSes competitor"
prioritising **accuracy, speed, universal product coverage**, API-first. As of
today it is a genuinely capable **modelling engine** — the calculation core is
competitive with, and in several respects ahead of, what the incumbents offer:

- **The modelling paradigm matches Prophet's winning abstraction** (declarative
  time-indexed variables with an engine-resolved dependency graph) while fixing
  Prophet's biggest structural weakness: models here are plain Python in git —
  diffable, reviewable, CI-tested — instead of binary workspaces in a
  proprietary IDE.
- **Validation rigour exceeds industry practice.** 1,326 test functions across
  55 files; closed-form golden tests; two executors (interpreted and
  vectorized) required to agree **bitwise** on every template; a 408,000-rate
  bitwise parity harness against the validated VPLA implementation; a
  content-addressed run registry that refuses a determinism failure. No
  incumbent ships anything like this level of machine-checked accuracy
  evidence to its users.
- **Reporting-overlay breadth is unusually deep for the engine's age**: IFRS 17
  in all three measurement models (GMM/VFA/PAA), Solvency II standard formula
  including market risk (both the 2015/35 and the 2026/269 texts), counterparty
  default, operational risk, the LAC TP/DT adjustment, ring-fenced funds and
  the risk margin, US GAAP LDTI, US statutory VM-20/VM-21 (CTE machinery),
  embedded value with TVOG, and an experience-analysis module with
  Shapley-value attribution — the last of which goes beyond what any incumbent
  offers out of the box.
- **The platform and enterprise layer is where the gap is.** No cross-machine
  scale-out, no compiled kernels yet, no governance workflows (RBAC, approval),
  no assumption-management or production UI, no Excel integration, and — most
  commercially important — **no Prophet/MoSes migration tooling** (format
  readers + parity reports), which PLAN correctly identifies as the sales
  on-ramp and which remains unbuilt.

In short: the engine is roughly where PLAN's Phase 3 says it should be, and
the honest comparison is "a superior calculation kernel wrapped in a fraction
of the incumbents' platform." The moat the incumbents actually defend —
installed base, regulatory track record, vendor-maintained libraries, support
organisations — is not a technical artifact and is not yet addressed.

---

## 2. The incumbents

### 2.1 FIS Prophet

The market leader outside North America (dominant in UK, Europe, Asia).
Originally developed by Bacon & Woodrow, sold through SunGard to FIS.

**Capabilities:**
- **Paradigm:** declarative time-indexed variables — each variable one formula
  over projection time, calculation order resolved by the system. This is the
  abstraction this repo deliberately adopted (PLAN §2.1: "Prophet's idea, done
  as code").
- **Products & libraries:** the core commercial asset. Vendor-maintained
  libraries for conventional life, unit-linked, annuities, health, pensions
  and general-insurance conversion, plus regulatory solution libraries
  (IFRS 17, Solvency II, US GAAP/LDTI, MCEV). Clients get quarterly library
  updates tracking regulation.
- **Execution & scale:** Prophet Professional (desktop IDE with the formula
  browser this repo's RFC-030 explicitly replaces) and Prophet Enterprise
  (server/grid execution, job scheduling, distributed runs across large
  on-prem or cloud grids; a Managed Cloud Service exists). Historically
  per-policy execution compiled to C; grid licences are a separate,
  significant cost line.
- **Governance:** run control, version comparison, and audit within the
  proprietary environment; a formula browser and dependency views for model
  documentation.

**Weaknesses this repo targets:** proprietary binary model format (not
diffable, not CI-testable), IDE-era ergonomics, integration by file drops,
per-core/grid licence economics, vendor-gated libraries, and results whose
lineage is hard to trace mechanically.

### 2.2 The MoSes lineage: WTW RiskAgility FM and RNA Analytics R³S

MoSes (Tillinghast / Towers Perrin, later Towers Watson) was Prophet's
traditional rival: **free-form procedural modelling** compiled to C++ —
maximum flexibility, at the cost of auditability (every model a bespoke
program). Two successors carry it forward:

- **WTW RiskAgility FM** — Towers Watson's replacement for MoSes: faster
  runtime, better structure, strong in with-profits and UK/European business;
  paired with **WTW Unify** for automation/orchestration/governance around
  model runs. Same fundamental trade: procedural flexibility over declarative
  auditability.
- **RNA Analytics R³S Modeler** — the MoSes codebase itself, acquired from
  WTW, continued and modernised (R³S cloud runs, IFRS 17 solution libraries).
  Installed base mostly ex-MoSes shops, notably in Asia and the Middle East.

This repo's PLAN takes the opposite bet — declarative by default, "MoSes-style
free-form procedural code is the escape hatch, not the default" — and the
escape hatch (plain-Python `@var` bodies, still traced for lineage) exists.

### 2.3 Moody's AXIS (formerly GGY AXIS)

The North American leader for life insurance. The design philosophy is the
inverse of both Prophet and MoSes: **users do not write model code at all**.
AXIS is a single vendor-maintained calculation codebase that users
*configure* — products, assumptions, switches — so every client runs the same
audited formulas.

- **Strengths:** consistency and vendor-warranted correctness; deep US
  statutory coverage (VM-20/VM-21 stochastic reserves, cash-flow testing,
  PBR), US GAAP/LDTI; DataLink for data feeds; GridLink/EnterpriseLink for
  distributed and scheduled production runs; Moody's ESG integration.
- **Weaknesses:** a product the vendor hasn't parameterised is hard to model;
  the black-box trade is total — you cannot read the formula, only the manual.

Relevant contrast: this repo gets AXIS-style "everyone runs the same audited
code" not by hiding the code but by shipping it with golden tests — the
`modelpoint_fields`/catalogue work (RFC-032) is the same instinct (the system
must *state* what a template needs) executed in the open.

### 2.4 Milliman MG-ALFA / Integrate

The other major US platform. MG-ALFA is the modelling engine (strong in US
statutory, GAAP/LDTI, VM-20/21, hedging of VA blocks); **Integrate** wraps it
into a managed Azure cloud production platform — orchestration, governance,
elastic grid — sold with Milliman consulting. The pitch is
"industrialised production actuarial function as a service." Notable for
having moved the market's expectation toward cloud elasticity and managed
operations rather than modelling-paradigm innovation.

### 2.5 Others worth naming

- **PolySystems** (US statutory valuation), **WTW Igloo / ResQ / Tyche (Aon)**
  (general-insurance capital and reserving — a market this repo does not
  address beyond the chain-ladder LIC module), **SS&C PALMS**, **OAC Mo.net**
  (UK; C#-based modelling, positions itself on openness much as this repo
  does).
- **Cloud-native newcomers:** **Slope Software (SLOPE)** — SaaS, API-driven,
  modern UI, subscription pricing, growing US mid-market adoption; the
  closest commercial analogue to this repo's API-first thesis. **Montoux** —
  model conversion/decision platform, now AI-forward. **Coherent Spark** —
  converts Excel logic to APIs; competes for the "escape from spreadsheets"
  budget rather than the heavy-modelling budget.
- **Open source:** **lifelib / modelx** (Python; declarative cell graphs —
  the nearest open relative of this repo's DSL, but interpreted cell-by-cell
  with vectorisation as a per-model rewrite rather than an executor
  guarantee, and no reporting overlays, registry, or API layer),
  **JuliaActuary** (high-performance primitives, no platform),
  **cashflower**, **heavylight**, **actxps** (R, experience studies),
  **chainladder-python** (P&C). PLAN's judgement — "none of these are a full
  platform... that gap is the product" — remains accurate in 2026.

---

## 3. Capability comparison

Legend: ✅ shipped · 🟡 partial / demonstrable but not production-grade ·
❌ absent. Incumbent columns are the *category-leading* incumbent capability.

| Dimension | Prophet | MoSes lineage (RAFM/R³S) | AXIS | This repo |
|---|---|---|---|---|
| Modelling paradigm | Declarative vars, proprietary IDE | Procedural, compiled | Configured, closed code | ✅ Declarative `@var` graph in plain Python, procedural escape hatch |
| Models in version control / CI | ❌ binary formats | 🟡 partial | ❌ n/a | ✅ git-native, CI on every commit |
| Machine-checked accuracy evidence | 🟡 vendor QA, opaque | 🟡 | 🟡 vendor-audited | ✅ 1,326 tests, closed forms, bitwise dual-executor equivalence, parity harness |
| Vectorised execution across policies × scenarios | ❌ largely per-policy | ❌ | 🟡 | ✅ core design; ~40× interpreter, 100k×60y in seconds |
| Compiled kernels / GPU | ✅ compiled C | ✅ C++ | ✅ | ❌ planned (graph + forward loop in place, "nothing is compiled yet") |
| Grid / cross-machine scale-out | ✅ Enterprise grid | ✅ | ✅ GridLink | 🟡 multi-core sharding with bitwise guarantee; no cross-machine dispatch |
| Stochastic / nested stochastic | ✅ | ✅ | ✅ | ✅ incl. exact mid-life restart (bitwise) and batched inner runs |
| Proxy models (LSMC) with error estimates | 🟡 add-ons | 🟡 | 🟡 | ✅ with the honest finding that in-sample fit statistics cannot license a proxy |
| Life savings/protection product library | ✅ broad, vendor-maintained | ✅ | ✅ | ✅ term, WL/endowment, UL (§7702, NLG), FIA (index crediting, GLWB), unit-linked GMxB, payout & variable-payout annuities, income protection (multi-state), with-profits, group & credit life — each with golden tests |
| Health (US), pensions, takaful | ✅/🟡 | 🟡 | ✅ health | ❌ |
| General insurance / P&C | 🟡 conversion libs | ❌ | ❌ | 🟡 chain-ladder LIC only |
| IFRS 17 (GMM/VFA/PAA) | ✅ solution library | ✅ | ✅ | ✅ all three models, one net-cash invariant across every option |
| Solvency II (BEL, SCR, RM) | ✅ | ✅ | 🟡 | ✅ stresses, market risk (2015/35 **and** 2026/269 as dated sets), counterparty, op risk, LAC adjustment, ring-fenced funds, risk margin |
| US STAT/GAAP: LDTI, VM-20/21 | 🟡 | 🟡 | ✅ deepest | ✅ LDTI + CTE machinery; ❌ VM-22, full statutory formulaic reserves |
| EV / ALM / asset side | ✅ ALS library | ✅ | ✅ | ✅ EV with TVOG, portfolio projection, defaults, forced-sale ordering, rebalancing strategies |
| Experience analysis / AvE | 🟡 | 🟡 | 🟡 | ✅ incl. Shapley attribution (order-independent, adds up) — ahead of the field |
| Reproducibility / run registry | 🟡 run logs | 🟡 Unify | 🟡 | ✅ content-addressed question+answer digests, cross-process verified |
| Model documentation / lineage | ✅ formula browser | 🟡 | 🟡 manual is the doc | ✅ generated Markdown + dependency graph with time offsets; docstring coverage measured and asserted (80.3% floor) |
| API-first integration | ❌ file drops | 🟡 | 🟡 | ✅ Python SDK + REST (202/fingerprint/event stream), Parquet I/O |
| Production UI (runs, results explorer, assumption diffing) | ✅ | ✅ | ✅ | 🟡 demo UI only |
| Governance: RBAC, approval workflows, multi-user | ✅ | ✅ Unify | ✅ | ❌ |
| Excel integration | ✅ | ✅ | ✅ | ❌ |
| Incumbent migration tooling (readers + parity reports) | n/a | n/a | n/a | 🟡 parity core (RFC-033: reusable diff engine, Markdown report, registered against both content digests) + Prophet MPF/results readers (RFC-034, dialect-driven, mapping report); ❌ MoSes readers, conversion scaffold |
| Licence / cost model | Heavy, per-core/grid | Heavy | Heavy | Open code; one runtime dependency (NumPy); optional extras |
| Regulatory track record & support organisation | ✅ decades | ✅ | ✅ | ❌ none |

---

## 4. Where this repo is ahead

1. **Accuracy as an enforced property, not a claim.** The incumbents assert
   correctness through vendor QA and user acceptance testing; this repo
   *enforces* it — bitwise agreement between two independent executors on
   every template, closed-form identities asserted with `==` on floats where
   exact, an independent forward-loop reference reconciling at 1e-12, and a
   registry that treats "same question, different answer" as a refusable
   determinism failure. For regulatory model validation (SII internal model
   standards, VM-G governance), this is a stronger evidence base than any
   incumbent hands its clients.

2. **Vectorisation as the executor's job, not the modeller's.** Identical
   template code runs per-policy interpreted or across a whole block
   vectorized; chunking, memo windowing and pooled-model safety are engine
   concerns. Prophet/MoSes modellers never got this; lifelib gets it only by
   rewriting each model. The published numbers (100k policies × 60 years in
   seconds; 100k annuitants × 720 monthly periods on the full basis under two
   minutes on one machine; 20M nested inner cells in 59 s) are already inside
   the range where a mid-size office's production blocks are feasible without
   a grid.

3. **Reporting overlays that read (or re-run) the same projection.** The
   architectural split — library answers *what will happen*, overlays answer
   *what the accounting says happened* — with one invariant (total profit =
   undiscounted net cash) pinning IFRS 17 across every permitted choice, is
   cleaner than the incumbent pattern of separate regulatory libraries whose
   reconciliation to the projection model is the client's problem.

4. **Findings the incumbents don't surface.** The RFCs document behaviour a
   black-box platform cannot show its users: the SII counterparty band cliff
   (40% of the requirement on one added counterparty), duration-matched
   portfolios carrying interest SCR from 15.36 to zero, the ordering
   dependence of analysis-of-surplus (range fourteen times the value),
   LDTI vs IFRS 17 timing 4.24× apart on the identical event. A platform that
   *demonstrates* regulation's sharp edges is a differentiated audit and
   review tool, not just a runner.

5. **Integration economics.** One runtime dependency, optional extras for
   API/data, REST with idempotent fingerprint identifiers and an event
   stream, Parquet round-trips. Against Prophet's file-drop integration and
   grid licence economics, the pilot cost of this engine is near zero.

## 5. Where the incumbents are ahead

1. **Scale-out and compilation.** Every incumbent runs distributed
   production grids; AXIS GridLink and Prophet Enterprise schedule thousands
   of cores. This repo shards across the cores of one machine (with a
   bitwise guarantee the incumbents don't make) and explicitly has no
   cross-machine dispatch; the compilation step (Numba/Rust kernels, GPU) that
   PLAN §4 plans is not started. For nested-stochastic hedging blocks at
   production scale this is the binding constraint.

2. **Breadth outside life savings/protection.** No US health, pensions,
   longevity swaps/buy-ins as a product line, takaful, or general insurance
   (beyond the chain-ladder LIC). AXIS's US statutory coverage (full
   formulaic reserves, cash-flow testing, VM-22 when it lands) is deeper than
   the LDTI + VM-20/21 CTE machinery here.

3. **Enterprise governance.** RBAC, 4-eyes assumption approval, segregation
   of duties, production run calendars, SOC 2 — table stakes for an insurer's
   model-risk function, entirely absent here (PLAN defers them, correctly,
   but they gate any real deployment).

4. **The production UI.** Results explorers, assumption-diff screens,
   drill-down for the 80% of actuarial users who will not read Python. The
   demo UI proves the API; it is not that product.

5. **Migration tooling — the commercially decisive gap.** PLAN §3.3/§6
   correctly identifies the Prophet/MoSes parity harness and format readers
   as *the* sales tool: nobody replatforms without a reconciliation report.
   Only the VPLA parity harness exists. Until an incumbent's model-point and
   results files can be read and diffed automatically, the engine cannot run
   the low-risk pilot story PLAN §9 relies on.

6. **Trust assets that aren't software.** Vendor-maintained regulatory
   library updates on a contractual cadence, decades of regulator
   familiarity, reference clients, and a support organisation. These are
   bought with time and customers, not commits — and they are the actual
   moat.

## 6. Assessment against the repo's own plan

The repo is where PLAN §8's Phase 3 says it should be, with Phase 1–2 exit
criteria met and exceeded (both executors, stochastic + nested + LSMC,
multi-core scale-out, the VA/GMxB family, and far more of §5.3's reporting
than Phase 3 requires). The items PLAN itself lists as next remain the right
ones, in roughly this priority for competitive impact:

1. **Prophet/MoSes reader + parity harness** (PLAN §3.3, §6) — the migration
   on-ramp; highest commercial leverage per unit of work, and the VPLA parity
   harness is the existing pattern to generalise.
2. **Kernel compilation** (PLAN §4.2) — the graph and forward loop exist;
   this is the multiplier that makes single-machine performance an
   unanswerable benchmark against per-policy incumbents.
3. **Cross-machine dispatch** (PLAN §4.3) — the sharding and its safety
   argument are done; production nested-stochastic needs the grid.
4. **Governance layer** (PLAN §7) — RBAC and approval workflows; the run
   registry already provides the audit substrate the incumbents lack.
5. **Results warehouse + Excel surface** (PLAN §6) — meets actuarial teams
   where they are.

The one strategic risk the plan understates: **the buyer of an actuarial
platform is a model-risk-averse institution, and the incumbents' weakness
(closed, expensive, slow) is also their pitch (audited, supported,
regulator-familiar).** The counter is exactly the repo's existing habit —
machine-checked evidence, published benchmarks, parity reports — which is why
the migration/parity tooling should come before any further product breadth.

---

*Sources: repository (README.md, PLAN.md, docs/rfc-001 – rfc-032,
engine/ and tests/ at commit `9e986c1`); market knowledge of FIS Prophet,
WTW RiskAgility FM/Unify, RNA Analytics R³S, Moody's AXIS, Milliman
MG-ALFA/Integrate, Slope, Montoux, Coherent, lifelib/modelx, JuliaActuary
current to early 2026. Vendor capabilities move; treat incumbent columns as
directional rather than contractual.*
