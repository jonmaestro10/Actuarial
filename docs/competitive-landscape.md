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
  vectorized) required to agree **bitwise** on every template that does not
  pool across model points, and a stated second class of claims for the two
  that do (RFC-061); a 408,000-rate
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
- **RNA Analytics R³S Modeler** — *correction (Aug 2026): R³S is not the
  MoSes codebase.* It is **IBM Algo Financial Modeler**, acquired by RNA
  Analytics from IBM on 30 June 2017 and rebranded R³S. RNA competes in the
  same free-form-modelling segment and actively markets MoSes-to-R³S
  migration, which is how it earns its place in this subsection, but the
  lineage is Algorithmics, not Tillinghast. Continued and modernised (R³S
  cloud runs, IFRS 17 solution libraries); installed base notably in Asia
  and the Middle East.

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
| Machine-checked accuracy evidence | 🟡 vendor QA, opaque | 🟡 | 🟡 vendor-audited | ✅ 2,630 tests, closed forms, bitwise dual-executor equivalence, parity harness, and checks against **published** figures (docs/sources/ — Mack's Taylor–Ashe reserves reproduced to the rounding he printed) |
| Vectorised execution across policies × scenarios | ❌ largely per-policy | ❌ | 🟡 | ✅ core design; ~40× interpreter, 100k×60y in seconds |
| Compiled kernels / GPU | ✅ compiled C | ✅ C++ | ✅ | 🟡 compiled CPU kernels (RFC-074) — bitwise-identical to the array executor, 13 of 14 templates, kernel median 14.6×. GPU: the two guarantees and the reconciliation bound are built and tested (RFC-076), device unmeasured — no incumbent publishes a reproducibility statement at all |
| Grid / cross-machine scale-out | ✅ Enterprise grid | ✅ | ✅ GridLink | 🟡 dispatch to remote engine instances (RFC-075), bitwise across workers that **attest the same arithmetic** — and refused otherwise, which no incumbent checks at all |
| Stochastic / nested stochastic | ✅ | ✅ | ✅ | ✅ incl. exact mid-life restart (bitwise) and batched inner runs |
| Proxy models (LSMC) with error estimates | 🟡 add-ons | 🟡 | 🟡 | ✅ with the honest finding that in-sample fit statistics cannot license a proxy |
| Life savings/protection product library | ✅ broad, vendor-maintained | ✅ | ✅ | ✅ term, WL/endowment, UL (§7702, NLG), FIA (index crediting, GLWB), unit-linked GMxB, payout & variable-payout annuities, income protection (multi-state), with-profits, group & credit life — each with golden tests |
| Health (US), pensions, takaful | ✅/🟡 | 🟡 | ✅ health | ✅ long-term care on the multi-state engine (C4, RFC-042), pension buy-in/buy-out and longevity swaps (C3, RFC-041), and family takaful on the hybrid wakala–mudarabah model with surplus distribution and qard hasan (C6, RFC-055) — each shipped with its sharp-edge finding |
| General insurance / P&C | 🟡 conversion libs | ❌ | ❌ | ✅ chain-ladder LIC plus Mack and ODP-bootstrap reserve **ranges** reproduced against published triangles in CI, and a premium-liability template on the same chassis (C5, RFC-054) |
| IFRS 17 (GMM/VFA/PAA) | ✅ solution library | ✅ | ✅ | ✅ all three models, one net-cash invariant across every option |
| Solvency II (BEL, SCR, RM) | ✅ | ✅ | 🟡 | ✅ stresses, market risk (2015/35 **and** 2026/269 as dated sets), counterparty, op risk, LAC adjustment, ring-fenced funds, risk margin |
| US STAT/GAAP: LDTI, VM-20/21/22 | 🟡 | 🟡 | ✅ deepest | ✅ LDTI + CTE machinery, and **VM-22** for non-variable annuities (RFC-039): CTE stochastic reserve, cash-surrender-value floor, exclusions recorded with their basis, and a dated parameter set that refuses to invent the text's thresholds — corrected against the 1 Jan 2026 text (§3.A's sum over groups, §4.B.1's floor inside the CTE, §7.C.1's ratio over the PV of benefits) and shipped with the finding that the prescribed floor placement is not bracketed by the two obvious ones, so contract-by-contract reserving can be less conservative than aggregating; and **formulaic statutory reserves with asset adequacy** (RFC-040): the modified-premium family as one parameter, CRVM's cap computed rather than tabulated, and cash-flow testing on the same deficiency roll a principle-based reserve uses — shipped with the finding that first-year strain is exactly the cap's bite and its slope is discontinuous where the cap stops binding |
| EV / ALM / asset side | ✅ ALS library | ✅ | ✅ | ✅ EV with TVOG, portfolio projection, defaults, forced-sale ordering, rebalancing strategies |
| Experience analysis / AvE | 🟡 | 🟡 | 🟡 | ✅ incl. Shapley attribution (order-independent, adds up) — ahead of the field |
| Reproducibility / run registry | 🟡 run logs | 🟡 Unify | 🟡 | ✅ content-addressed question+answer digests, cross-process verified |
| Model documentation / lineage | ✅ formula browser | 🟡 | 🟡 manual is the doc | ✅ generated Markdown + dependency graph with time offsets; docstring coverage measured and asserted (80.3% floor) |
| API-first integration | ❌ file drops | 🟡 | 🟡 | ✅ Python SDK + REST (202/fingerprint/event stream), Parquet I/O, and a star-schema results warehouse whose every fact row carries the run fingerprint (RFC-046) |
| Production UI (runs, results explorer, assumption diffing) | ✅ | ✅ | ✅ | ✅ runs list with prefix-matched digest search, seriatim drill-down (aggregate → variable → model point), a **semantic** assumption diff that names the component that moved rather than the line (RFC-048), and artifact and evidence-pack views — plus the property no incumbent's console has: every view's state is in the URL and the run identifier is a content digest, so a pasted link is a citation that cannot rot into different numbers; ❌ interactive trace/graph explorer (calculation-ux-plan) |
| Governance: RBAC, approval workflows, multi-user | ✅ | ✅ Unify | ✅ | 🟡 token authentication and four roles on every route (RFC-043), off by default so the library is unchanged, plus 4-eyes assumption approval bound to the content digest rather than to a label (RFC-044) — an approval that cannot silently drift, which no incumbent's label-based workflow offers — and a digest-chained audit log plus a declarative run calendar whose entries freeze the request fingerprint (RFC-045) |
| Excel integration | ✅ | ✅ | ✅ | ✅ audit workbook writer (RFC-047): run summary, aggregates, per-row-digested assumption snapshot and the parity report, with the run fingerprint and assumption digests stamped on **every** sheet because tabs get copied — plus the finding no vendor's workbook states, that a spreadsheet carries 16 significant digits and Excel parses 15, so non-finite values are written as text rather than the blank cells openpyxl produces and the exact record stays the run's Parquet; plus a **live add-in** (RFC-056) that makes the sheet a client of the API — request from named ranges, idempotent Refresh, every pulled block stamped, and a block that clears the extent its predecessor recorded so a shorter refresh cannot leave the tail of the previous run under the new run's fingerprint |
| Incumbent migration tooling (readers + parity reports) | n/a | n/a | n/a | 🟡 parity core (RFC-033: reusable diff engine, Markdown report, registered against both content digests), Prophet MPF/results readers (RFC-034, dialect-driven, mapping report) and conversion scaffold (RFC-036); ❌ MoSes readers |
| Licence / cost model | Heavy, per-core/grid | Heavy | Heavy | Open code; one runtime dependency (NumPy); optional extras |
| Regulatory track record & support organisation | ✅ decades | ✅ | ✅ | ❌ none — but a machine-generated validation evidence pack (RFC-049): live test inventory, run-level executor-equivalence attestation, coverage, reconciliations on record, digest-identical rebuild asserted in CI |

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

2. **Breadth outside life savings/protection.** ~~No US health, pensions,
   longevity swaps/buy-ins as a product line, takaful, or general insurance
   (beyond the chain-ladder LIC). AXIS's US statutory coverage (full
   formulaic reserves, cash-flow testing, VM-22 when it lands) is deeper than
   the LDTI + VM-20/21 CTE machinery here.~~ **Closed** by C1–C6: VM-22 and
   formulaic statutory reserves with asset adequacy (RFC-039, RFC-040),
   pensions and longevity (RFC-041), long-term care (RFC-042), reserve
   variability and a premium-liability template (RFC-054), and takaful
   (RFC-055). Milestone M5.

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

## 7. Discovery: how the field exposes its engines — outputs and UX

*Added August 2026. The sections above compare what the engines calculate;
this one compares how anyone gets at them — the surfaces through which models
are authored, runs are launched and watched, and results reach the people who
never open the modelling tool. This is the terrain the execution plan's E1–E3
items (warehouse, Excel, production UI) will be judged against, so it is
worth knowing precisely.*

### 7.1 Vendor by vendor

**FIS Prophet — now "FIS Insurance Risk Suite."** The components have been
renamed: Prophet Professional is **Model Developer**, Prophet Enterprise is
**Enterprise Manager**, Glean is **Experience and Rating Manager**, and the
Prophet Results Database is the **SQL Connector**. The authoring surface is
still a Windows desktop IDE — workspaces holding libraries, products, run
structures and model-point files; a **Diagram View** that navigates
precedent/dependent variables and shows computed values after a run; a
Schedule grid of variables across products. The engine generates C++
compiled with MSVC or GCC (Linux workers supported since the 2024 Q4
release). Runs go through Enterprise Manager **jobs** with a scheduler and a
**Runlog**; classic Enterprise has no browser front end. Outputs are
proprietary results files plus **`.rpt` policy-level files** (user-defined
"variable groups" select what is written), with model points as
comma-delimited text. Getting results *out* is an explicit product layer:
the SQL Connector ETLs results plus the Runlog into Microsoft SQL Server at
the end of a job; a newer **Flexible Results API** queries Enterprise
Manager directly (no staging database) and feeds an **Insurance Data
Repository** built for BI tools. A **Quality Assurance** module automates
run-vs-run comparisons and regression testing. The 2024-era SaaS tier,
**Production Manager**, is the first browser-based surface — pay-as-you-go
execution with GPU/AVX options and spend-monitoring dashboards; it is a run
console, not a modelling environment. Orchestration (Control Centre /
**Process Orchestrator**) is REST-startable. Results consumers live in SQL
Server, Excel extraction templates and BI — never in the IDE.

**WTW RiskAgility FM + Unify.** The IDE is organised into four managers —
**Code, Input, Run, Output** — around C++ model code, with step-through
debugging and a visual **Dependency Viewer**; the Team edition adds version
control via Git or Azure DevOps. Execution scales from desktop to
Microsoft-HPC grids to **vGrid** (WTW's managed pay-per-use Azure grid);
GPU execution shipped in June 2026 with a per-run CPU/GPU choice. The
striking fact is the output story: the **standard output of a run is text
files**, with custom formats possible and **reporting via an Excel add-in**.
There is no documented public API for RAFM itself; the automation and
governance story is **Unify**, a separate platform that drives RAFM (and any
third-party tool with an API or a PowerShell hook), runs user-defined
workflows that can pause for review/sign-off, and snapshots input data so a
result can be traced to source and reruns reproduced. The cloud offer
(**vPlace**) is a hosted virtual desktop bundling RAFM + vGrid + Unify — a
managed workplace, not a web application. 2026 releases added LLM
assistants inside the IDE for writing and explaining model code.

**RNA Analytics R³S** (Algo Financial Modeler lineage — see the §2.2
correction). Desktop **Modeler** with a **Profiler** for hot-spot analysis
and a **Development Manager** for multi-user source control. The production
surface is genuinely web-based and ahead of the other incumbents here:
**Workflow Manager** submits and manages executions via GUI *or external
API calls*, schedules recurring runs, invalidates downstream artifacts when
inputs change, and carries approvals, Active Directory roles and an
action-level audit trail. A **Toolkit** of APIs runs a model from a run
archive file — the basis of **Process Manager**, which lets non-modellers
execute models in a controlled frame, and of customer-built SQL reporting
databases and embedded execution inside other applications. Outputs are
Excel reports and data exports managed end-to-end by Workflow Manager, plus
SQL databases; the AWS "R3S Cloud" deployment wires results into
**QuickSight** for reporting.

**Moody's AXIS.** The **dataset is the model** — effectively an archive of
proprietary objects (Batches, Cells, DataLink tables, Formula Tables)
edited through menus and switches rather than code; **FormulaLink** adds a
real script editor for the escape-hatch cases, and "turnaround documents"
(export, edit, re-import) let users generate AXIS objects programmatically.
The enterprise surface, **EnterpriseLink**, is a Windows client/server
application — role-customised dashboards, script jobs checked into a
version-control project, a scheduler, and monitoring of **GridLink** jobs
(command-line or workstation submission, up to 512 cores per job, three
compute tiers up to Moody's pay-per-core-hour GLaaS, watched through a "C4"
Cloud Control Center client). Two facts are worth holding onto. First,
**an EnterpriseLink REST API does not exist yet** — as of the November 2025
user forum it is in discovery/beta. Second, the legacy results format is
**Microsoft Access .MDB files**, with Moody's actively pushing customers to
SQL Server as 32-bit components retire in 2027. Downstream, AXIS feeds the
RiskIntegrity subledger products for IFRS 17/LDTI. The UX for
understanding the closed engine is documentation at industrial scale —
26,000+ help texts — now fronted by **AXIS Navigator**, a GenAI assistant
launched September 2025. DataLink, the input mapper, is single-threaded
and welded into the AXIS executables; a "DataLink as a Service" with its
own config UI and API layer is itself only a discovery project.

**Milliman MG-ALFA / Integrate.** MG-ALFA is a Windows desktop application
whose business logic is open to the user in an intuitive syntax (more
transparent than AXIS, less free-form than MoSes was); runs execute via a
generated command file through a bridge DLL, with **Seamless Distributed
Processing** fanning work across grids and later **MG-ALFA Compute for
Azure**. **Integrate** wraps this in the most consumer-finished surface any
incumbent ships: tiered offerings culminating in end-to-end automation,
with **Power BI Embedded dashboards inside the platform**, near-real-time
run and cost telemetry, sandboxed what-if analysis exposed to
non-modelling teams, and an API-first architecture claim (Microsoft's 2025
case study) — all delivered as a managed service with Milliman operators
watching production. Model development remains a thick-client activity;
the web surface is for operating and consuming.

**The cloud-native entrants** are defined by their exposure choices as much
as their engines. **SLOPE** is the only platform where *authoring* is
browser-native: a no-code formula builder with real-time validation, a
"Relationship View" tracing assumptions to outputs, auto-versioned
assumption changes, dynamic reports with drill-down to individual
model-point output, multi-format export, a REST API, and a **Snowflake
integration** so actuarial output sits next to corporate data in whatever
BI tool the insurer already owns. **Coherent Spark** inverts the problem:
authoring stays in Excel, and the product is the exposure layer — named
ranges become a governed **REST API** with SDKs, a Testing Center that
auto-generates regression testbeds, git-style versioning with dual
approval, and (new) an MCP surface for AI agents; consumers are systems,
not people. **Montoux** is code-centric (a proprietary language plus
Python, developed in VSCode/Git/Jupyter) with an "Output Studio" for
drill-down and APIs into Power BI. **OAC Mo.net** models in VB.NET in a
desktop studio, compiles models to DLL/EXE, and — its distinctive move —
**publishes models as services**: surrender-value quotes callable from
policy administration, "modelling as a service" over public or private
APIs. **Open source** exposes only code: modelx offers Spyder-plugin
widgets (object tree, DataFrame viewer, precedent/dependent tracer) and
pandas/Excel outputs; cashflower writes CSVs from a command-line run;
heavylight and JuliaActuary return DataFrames to whoever is driving the
REPL or notebook.

### 7.2 Exposure surfaces at a glance

| Platform | Authoring surface | Run submission & monitoring | Results store / format | Results-consumer UX | Programmatic access |
|---|---|---|---|---|---|
| Prophet / IRS | Windows IDE (Diagram View) | Enterprise Manager jobs + Runlog; SaaS Production Manager (browser, GPU, spend dashboards) | Proprietary + `.rpt` policy files → SQL Server (SQL Connector), IDR | SQL/Excel/BI; QA run-comparison module | Flexible Results API; REST via Process Orchestrator |
| RAFM + Unify | Windows IDE (4 managers, Dependency Viewer) | Run Manager; HPC/vGrid; Unify workflows w/ sign-off gates | **Text files**; custom formats | Excel add-in; Unify dashboards/audit | None public for RAFM; Unify scripting/PowerShell |
| R³S | Windows Modeler + Profiler | **Web Workflow Manager** (GUI or API), scheduling, approvals | Excel exports; SQL reporting DBs | Workflow dashboards; QuickSight (AWS) | Toolkit APIs (run from archive); Process Manager |
| AXIS | Windows app; dataset-as-model, menu/switch config | E-Link (Windows client) scheduler; GridLink/GLaaS via C4 client | **.MDB → SQL Server** (transition underway); report batches | Role dashboards (E-Link); RiskIntegrity downstream; 26k help texts + GenAI Navigator | E-Link scripting/CLI; **REST API in beta (Nov 2025)** |
| MG-ALFA / Integrate | Windows desktop, open logic syntax | Integrate portal, managed ops, run/cost telemetry | Client warehouses; platform output capture | **Power BI Embedded in-platform**; sandboxed what-if for non-modellers | API-first claim (Integrate) |
| SLOPE | **Browser, no-code** formula builder | In-app cloud runs ("High Performance Mode") | Multi-format export; **Snowflake** | In-app reports w/ model-point drill-down; BI via Snowflake | REST API |
| Coherent Spark | Excel (named ranges) | n/a — services, not runs | JSON API responses | None — consumers are systems | **REST + SDKs + MCP**; Testing Center |
| Mo.net | .NET desktop studio | Execution/Quotation services | Excel; BI connectors | Excel | Models compiled to DLL/EXE, **published as services** |
| lifelib/modelx | Python / Spyder widgets | Python calls | pandas / Excel | Whatever the actuary builds | It *is* the API |
| This repo | Python `@var` in git | REST `POST /runs` → 202 + fingerprint, event stream; runs list w/ digest search | Parquet star schema (RFC-046), fingerprint-stamped workbooks (RFC-047); content-addressed registry | ✅ seriatim drill-down, semantic assumption diff, evidence views; URLs are citations | ✅ Python SDK + REST, idempotent by digest |

### 7.3 The patterns

1. **Authoring and consuming are different products everywhere.** Every
   incumbent splits a thick-client development environment from a
   production/run-operations layer (Enterprise Manager, Unify, Workflow
   Manager, EnterpriseLink, Integrate), and the results consumer touches
   neither — they get a database, a workbook, or a BI dashboard. The
   "actuarial platform UI" that E3 contemplates is, in the field, mostly a
   *run-operations* UI; nobody's modelling surface is web-native except
   SLOPE's.

2. **The real results UX is somebody else's BI tool.** Prophet lands in SQL
   Server and an "Insurance Data Repository built for BI"; AXIS is
   mid-migration from Access .MDB to SQL Server; Integrate embeds Power BI;
   R³S Cloud wires up QuickSight; SLOPE ships a Snowflake integration. The
   industry has converged on *land the numbers in a queryable store and let
   BI do the looking* — which is precisely E1's star-schema-in-Parquet
   design, and validates putting the warehouse before any bespoke results
   screens. The beyond-parity move stands: none of those stores carries a
   run fingerprint on every row.

3. **Excel is the universal delivery vehicle, still.** RAFM's standard
   output is text files plus an Excel reporting add-in; R³S's Workflow
   Manager manages Excel reports end-to-end; Prophet teaches result
   extraction templates; AXIS/Integrate both meet Excel on the way out. E2
   is not a nice-to-have; it is how every incumbent's numbers actually
   reach sign-off meetings.

4. **APIs are arriving late and partially — except among the newcomers.**
   The most instructive single fact of this discovery: **AXIS's
   EnterpriseLink REST API was still in beta in November 2025**, four
   decades into the product's life. Prophet's REST surface exists only at
   the orchestration and results-extraction edges; RAFM has no public API
   at all, hiding automation behind Unify. Meanwhile Spark, Mo.net and
   SLOPE treat the API *as the product*. This repo's RFC-031 surface —
   202-with-fingerprint submission, idempotency by content digest, an event
   stream — is at the newcomers' edge of the field, ahead of every
   incumbent, and the fingerprint-idempotent identifier has no equivalent
   anywhere in the survey.

5. **Policy-level drill-down is the output feature vendors lead with.**
   Prophet's `.rpt` variable groups, AXIS's seriatim valuation output,
   SLOPE's drill-to-model-point debugging, Montoux's Output Studio. The
   engine here already computes per-policy; the warehouse and UI items
   should treat *seriatim-with-lineage* as the demo moment, not an option.

6. **Lineage tracing in the authoring UX is table stakes.** Prophet's
   Diagram View, RAFM's Dependency Viewer, modelx's MxAnalyzer, SLOPE's
   Relationship View — every platform gives modellers a precedent/dependent
   navigator. The repo's generated Markdown + dependency graph (RFC-030)
   covers the documentation half; an *interactive* trace view in E3 would
   meet the field's baseline expectation.

7. **Run reproducibility is audited by snapshot, not by construction.**
   Unify snapshots input files so reruns can be reproduced; Prophet keeps a
   Runlog; E-Link keeps job logs and version-control projects. All of it is
   *evidence collected around* the run. The registry here makes the run
   identifier itself the evidence — same question, same digest, refusal on
   determinism failure — which remains the sharpest differentiator this
   discovery found, and one no surveyed surface replicates.

8. **AI assistants are appearing on every surface at once** — AXIS
   Navigator over 26,000 help texts, LLM assistants inside RAFM, Montoux's
   Model Copilot, Spark's MCP endpoint. The incumbents need these layers
   because their engines are opaque to language models. Plain-Python
   models in git are maximally legible to the same tools with no
   intermediary product — worth stating in any pitch, and worth an MCP
   surface here eventually (a natural E3-adjacent item).

*Discovery sources: FIS product pages and Prophet community/training
material; WTW RAFM brochure, Unify pages, vGrid/vPlace listings and the
June 2026 GPU release; RNA Analytics software pages, Workflow Manager
brochure and AWS/Azure marketplace listings; Moody's AXIS module pages, the
November 2025 AXIS user-forum deck, and SOA "The Modeling Platform" (June
2020); Milliman Integrate pages, the Power BI press release and Microsoft's
2025 case study; slopesoftware.com, montoux.com, coherent.global/docs,
softwarealliance.net; lifelib/modelx, cashflower and heavylight
documentation and repositories. Vendor performance and adoption claims are
marketing figures, unverified. A small number of well-supported inferences
remain (e.g. that classic Enterprise Manager and EnterpriseLink have no
browser front end — consistent across every source, asserted by none);
weaker inferences were dropped.*

---

*Sources: repository (README.md, PLAN.md, docs/rfc-001 – rfc-032,
engine/ and tests/ at commit `9e986c1`); market knowledge of FIS Prophet,
WTW RiskAgility FM/Unify, RNA Analytics R³S, Moody's AXIS, Milliman
MG-ALFA/Integrate, Slope, Montoux, Coherent, lifelib/modelx, JuliaActuary
current to early 2026. Vendor capabilities move; treat incumbent columns as
directional rather than contractual.*
