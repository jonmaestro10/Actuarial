# Calculation UX Plan: Exposing the Calculations Themselves

*The implementation plan for the user experience that exposes this engine's
calculations — not just its results. Derived from
[competitive-landscape.md](competitive-landscape.md) §7 (the discovery of how
the field exposes its engines) and slotted into
[competitive-execution-plan.md](competitive-execution-plan.md)'s E-workstream:
this document is the detailed design for E3 (RFC-048) plus four items that go
beyond E3's scope, claiming RFC-061 onward. Written August 2026 against the
repo at RFC-032.*

---

## 1. The thesis: transparency is the product, so the UX is a microscope

Every platform surveyed in landscape §7 hides its calculation behind its UX.
Prophet's Diagram View shows the variable graph but the engine is compiled
C++ from a proprietary workspace; AXIS shows switches and 26,000 help texts
because users *cannot* read the formulas; Integrate's Power BI dashboards
show outputs whose lineage stops at the run that produced them. The field's
results UX answers "what is the number?"; none of it answers **"why is the
number?"** below the run level.

This repo's structural advantage is that the calculation is plain Python in
git, traced into a dependency graph with time offsets (RFC-030), executed
under a bitwise dual-executor guarantee, and every run is a content-addressed
question with a content-addressed answer (RFC-003, RFC-031). The UX this
plan builds is therefore not a dashboard bolted onto a black box — it is a
**microscope over a transparent one**: every number on every screen can be
opened, and opening it shows the formula, its documentation, its inputs, and
their values, down to the source line.

One sentence of scope discipline, learned from SLOPE's counter-example: this
plan builds **inspection, operation and consumption surfaces — not a web
authoring surface**. Authoring stays in Python, in git, in CI; that is the
repo's thesis (landscape §2.1), and a browser formula-builder would compete
with the repo's own best property. The 80% of users who will never read
Python (landscape §5.4) are served by making the *reading* unnecessary:
docstrings, rendered formulas, traced values and plain-language lineage do
the reading for them.

## 2. What we take from each competitor, and what we decline

The best aspects found in the §7 discovery, each mapped to a design decision
here:

| Source (landscape §7) | What they got right | What we build |
|---|---|---|
| Prophet **Diagram View** | Precedent/dependent navigation per variable, with computed values visible after a run | The **graph explorer** (§4.3): the RFC-030 graph rendered live, values overlaid from a selected run — plus time-offset edges (`t-1`) drawn as such, which Prophet's view does not distinguish |
| Prophet **`.rpt` variable groups** / AXIS seriatim output / SLOPE **drill-to-model-point** | Policy-level drill-down is the feature vendors lead with | The **results explorer** (§4.2) drills aggregate → variable → model point → time step with no configuration; the engine already computes seriatim |
| Prophet **QA module** / Coherent Spark **Testing Center** | Run-vs-run comparison and regression as a first-class product surface | The **compare screen** (§4.4): two fingerprints → per-variable deltas, worst cells, drill to the exact disagreeing cell; parity reports (A1) render in the same view |
| AXIS **26,000 help texts** + **Navigator** | Documentation at industrial scale, now AI-fronted, because the engine is opaque | Docstrings (coverage measured, floor 80.3%) surfaced **in place** next to every variable on every screen — the help text lives one click from the number, generated not written; the MCP surface (§4.6) is our Navigator, minus the need for it |
| Integrate **Power BI Embedded** / SLOPE **Snowflake** / R³S **QuickSight** | The real results UX is a queryable store plus the BI tool the insurer already owns | The UI reads big runs through the **E1 warehouse** and never invents a second store; the fingerprint-on-every-row rule means the UI and the client's own BI show provably the same numbers (§5) |
| R³S **Workflow Manager** | A genuinely web-based run-operations surface: submission, scheduling, approvals, audit, roles | The **runs surface** (§4.1) plus D1–D3 integration: role-scoped views, 4-eyes approval state, audit trail — but bound to digests, not labels |
| WTW **Unify** sign-off gates and input snapshots | Reproducibility and review gates around the run | Superseded by construction: the registry makes the run identifier the evidence. The UX job is to *show* it — determinism badges, digest chips, "same question ⇒ same answer" made visible (§5) |
| Integrate **sandboxed what-if** | Non-modellers safely rerunning production models with changed assumptions | The **what-if screen** (§4.5): clone a run's request, edit assumptions in a schema-driven form, submit, land in Compare against the base — idempotency makes this nearly free |
| SLOPE **Relationship View** / auto-versioned assumptions | Lineage and versioning visible to the user, not the auditor | Assumption snapshots as digest-addressed objects with semantic diffs (E3 scope), linked from every run that used them |
| Spark **MCP endpoint**, RAFM/Montoux **LLM assistants** | AI agents as first-class consumers of the modelling platform | An **MCP server** over the documented REST API (§4.6) — agents get the same catalogue, runs, results and lineage the UI gets; plain-Python models make the engine maximally legible to them |

**Declined**, with reasons: SLOPE's no-code authoring (contradicts the
code-first thesis, §1); Prophet/AXIS-style thick clients (the API is the
product boundary; RFC-032's rule stands); a bespoke BI/charting suite
(landscape §7.3.2 — the industry converged on "land it queryable, let BI
look at it"; we ship the warehouse path and a competent explorer, not a
Tableau); Unify-style snapshot machinery (the registry already does this by
construction).

## 3. Personas and the architecture rules

Three personas, mapped to D1's roles:

- **The modeller** (runner): lives in the graph explorer and trace panel;
  wants "why is this cell this value" answered in seconds.
- **The reviewer/approver** (approver): lives in Compare, the assumption
  diff, and approval screens; wants deltas explained and evidence linked.
- **The consumer** (viewer): lives in the results explorer and the workbook
  downloads; wants numbers with provenance, never the machinery.

Five architecture rules, inherited and extended from RFC-032:

1. **Every pixel is a documented REST call.** The UI has no privileged
   channel; anything it shows, a client script or MCP agent can fetch
   identically. New screens force new endpoints, and the endpoints are the
   deliverable as much as the screens.
2. **No build toolchain.** Static assets served by FastAPI as today
   (`engine/api/ui/`): vanilla JS, hand-rolled SVG for charts and the graph
   layout, zero npm, nothing compiled. The repo's dependency discipline
   (execution plan §1.4) applies to the front end too.
3. **The URL is a citation.** Every view's full state — run fingerprint,
   variable, model point, time step, comparison pair — lives in the URL, so
   any screen can be pasted into a review comment and reproduces exactly.
   Content-addressed runs make this *permanent*: the link cannot rot into
   showing different numbers.
4. **The UI holds no state of its own.** Reads come from the registry, the
   run store, and (for big runs) the E1 warehouse; writes are the existing
   documented mutations (submit, approve). No UI database, ever.
5. **Provenance is ambient.** The run fingerprint, results digest and
   assumption digests appear on every screen as copyable chips — the same
   stamping rule as the E2 workbooks — and a determinism badge states which
   executor(s) produced and verified the numbers.

## 4. The surfaces

### 4.1 Runs — the operations surface

The entry screen: a filterable, searchable list over the registry — model,
state, submitter, date, fingerprint prefix, approval state (D2), with live
updates from the existing `/events` SSE stream. A run's detail view shows
the question (request, canonicalised), the answer (results digest), timing,
executor, shard tree (B2, when it lands), and approval/audit history
(D2/D3). What R³S's Workflow Manager sells as a product tier, rendered from
data the registry already holds.

### 4.2 Results explorer — drill-down as the default

Three levels, each one click apart, none requiring configuration:

- **Aggregate:** the run's variables over projection time — table plus SVG
  line/area charts; variables grouped by the `@var` metadata tags E1 puts in
  its dimension tables.
- **Distribution:** pick a variable and time step → its distribution across
  model points (and scenarios, for stochastic runs): histogram, quantiles,
  the worst/best model points named and clickable.
- **Seriatim:** pick a model point → its full time series for every
  variable, the model-point record itself displayed alongside (the
  `modelpoint_fields` catalogue labels each field).

Every value at every level is a link into the trace panel (§4.3). Small
runs stream from the run store as today; runs beyond a size threshold read
through the E1 warehouse via a paged query endpoint — same numbers, same
fingerprint, by construction.

### 4.3 The trace panel and graph explorer — the flagship

The screen no competitor can build, because it requires a transparent
engine. Two connected views:

- **Trace ("explain this number"):** click any value `v(p, t)` anywhere in
  the explorer and a panel opens showing: the variable's docstring; its
  formula **source** (the actual `@var` body, syntax-highlighted, linked to
  file and line); its precedents *with their values at the referenced
  offsets* (`premium(t)`, `reserve(t-1)` — each value itself clickable,
  so the reviewer walks the recursion the way the engine did); and its
  dependents. Recomputing nothing: values come from the stored results, the
  structure from the RFC-030 graph. This is Prophet's Diagram View plus
  AXIS's help texts plus the source code none of them will show — in one
  panel.
- **Graph explorer:** the whole model's dependency graph, laid out layered
  (hand-rolled Sugiyama-style layout; the graphs are hundreds of nodes, not
  millions), time-offset edges drawn distinctly, filterable by tag/
  subgraph/search. With a run selected, node badges show aggregate values
  at a chosen `t`; without one, it is the model's documentation diagram —
  the same data `GET /models/{name}/graph` already serves, made navigable.

New endpoints: `GET /runs/{id}/trace?var=&mp=&t=` (values + precedent
values + source + doc, one call per panel) and a seriatim slice endpoint.
Both are exactly what the MCP surface (§4.6) wants too — rule 1 pays for
itself here.

### 4.4 Compare — regression and reconciliation as a screen

Two fingerprints (or a fingerprint and a parity report): per-variable delta
summary (max abs/rel, count differing, worst cell), sortable, each row
expanding to the disagreeing cells, each cell opening *two trace panels
side by side* — the same variable, same policy, same `t`, both runs — so
"why did the number move" is answered by inspection, not archaeology. The
assumption diff (semantic, per-table, from the two runs' assumption
digests) renders above the numeric deltas: cause above effect. A1's
`ParityReport` renders in the same view with the external side read-only.
This is Prophet's QA module and Spark's Testing Center, with the drill-down
neither has.

### 4.5 What-if — the sandbox

From any run: clone the request into a schema-driven form (the catalogue's
`example`/`modelpoint_fields` machinery already describes the shape), edit
assumptions, submit — role-permitting (D1) — and land in Compare against
the base run. Unchanged questions dedupe to the base run by fingerprint;
the sandbox is safe *and* cheap by construction, where Integrate needs a
managed service to promise the same.

### 4.6 The MCP surface — the field's direction, met early

`engine/api/mcp.py` (behind `[api]`): an MCP server exposing the documented
REST surface as typed tools — list/describe models, submit runs, fetch
results slices, trace values, compare runs, read parity reports. Landscape
§7.3.8's observation is that every vendor is bolting AI assistants onto
opaque engines; a transparent engine plus MCP needs no assistant product at
all — the agent reads the same docstrings, sources and traces the human
does. Ships last; costs little because rules 1 and 5 made every capability
an endpoint already.

## 5. Provenance UX — the beyond-parity layer

The discovery's sharpest conclusion (§7.3.7): the field audits
reproducibility by *snapshot*; this repo has it by *construction*. The UX
makes that visible everywhere rather than in one place:

- **Digest chips** on every screen (run fingerprint, results digest,
  assumption digests) — copyable, and pasteable into the search box.
- **A determinism badge** per run: which executors ran, whether the bitwise
  cross-check ran, verified-on-read status from the registry. Green is not
  decoration; it names the guarantee.
- **Deep links as citations** (rule 3): review comments, audit findings and
  evidence packs (F1) cite screens by URL, and the URL provably shows the
  same numbers forever.
- **Warehouse concordance:** any figure in the client's own BI carries a
  run fingerprint (E1); entering it in the UI lands on the same number's
  trace. The story "your dashboard, our microscope, one digest" is the
  sales demo.

## 6. Work items

Continues the execution plan's protocol (§1) and numbering; RFC-048 remains
E3's number, new items claim RFC-061+ (RFC-053–060 are taken).

### UX1 — E3 core (RFC-048, unchanged scope) — effort L
The runs surface (§4.1), results explorer (§4.2), assumption diff, parity
and evidence-pack views — as the execution plan already defines, with this
document as the design. Depends on D1 (auth) and E1 (warehouse reads).
**Accept** (in addition to E3's): URL round-trip test (every view's state
survives reload from URL alone); warehouse-vs-store concordance test (same
run rendered from both paths yields identical JSON); role-scoping tests per
screen.

### UX2 — Trace panel + graph explorer (RFC-061) — effort L
§4.3. New endpoints `GET /runs/{id}/trace` and the seriatim slice; SVG
graph layout; source/docstring rendering via `modeldoc`. **No hard
dependency on D1/E1** — it extends the demo UI and the existing graph/doc
endpoints, and may therefore ship *before* UX1 (see §7).
**Accept:** `tests/test_api_trace.py` — trace of a known template cell
returns the exact stored values for the cell and every precedent at the
correct offsets (asserted `==` against a direct engine run); source and
docstring match `modeldoc` output; a recursive variable's trace terminates
and links `t-1` correctly; graph endpoint output renders every template in
the library (snapshot test on node/edge counts).

### UX3 — Compare + what-if (RFC-062) — effort M
§4.4–4.5. Compare endpoint (`GET /compare?a=&b=`) computing per-variable
deltas server-side from stored results; what-if clone/edit/submit reusing
the existing submission path unchanged.
**Accept:** compare of a run with itself is all-zero; compare of two runs
differing in one assumption localises every delta to the affected
variables (golden case); a what-if edited back to the original assumptions
dedupes to the base fingerprint (idempotency visible in the UX); side-by-
side trace renders both runs' values for the same cell.

### UX4 — Approvals & role-scoped views (RFC-063) — effort S
D2's approval state and actions rendered into the runs surface and
assumption views; per-role navigation (viewer sees explorer/downloads;
approver sees compare/approvals; runner sees what-if/submit). Depends on
D1, D2. **Accept:** route-level and UI-level role tests; an approval
performed in the UI round-trips to the D2 registry record and back.

### UX5 — MCP surface (RFC-064) — effort S
§4.6. **Accept:** an MCP client (test harness) lists models, submits the
worked example, traces a cell and gets values identical to the REST path;
the tool schemas are generated from, and asserted against, the same
catalogue the UI uses.

## 7. Sequencing

Within the execution plan's §10 order, E-workstream slot (`E2 → E3 → E4`),
amended as follows and for these reasons:

```
UX2 (trace + graph)      — may interleave any time after A1; no D1/E1
                           dependency, and it is the differentiator: the
                           demo that no incumbent can copy. Pull it forward
                           to the first slack in the B/D sequence.
UX1 (= E3 core)          — after D1 and E1, per the existing plan.
UX3 (compare + what-if)  — after UX1; Compare's parity view wants A1,
                           which precedes it in any ordering.
UX4 (approvals/roles)    — after D2.
UX5 (MCP)                — any time after UX2; trivial once endpoints exist.
```

Milestone M4 (execution plan §7) grows to `E2 + UX1 + UX2 + UX3 + E4`;
UX4/UX5 attach to M3 and M6 respectively without gating them.

## 8. Risks

| Risk | Mitigation |
|---|---|
| Hand-rolled SVG graph layout becomes a project of its own | The graphs are library-template-sized (10²–10³ nodes); layered layout with a filter-first UX is enough. If a template exceeds usable size, the *filter* is the feature — never adopt a JS graph framework to solve it (rule 2) |
| Trace endpoint tempts recomputation | The rule is stated in the RFC: trace *reads* stored results and the static graph; it never executes model code. A cell absent from stored output (windowed memo) renders as "not retained — rerun with retention", honestly |
| Seriatim endpoints leak PII-shaped model-point data | D1 role-gates seriatim and trace routes (viewer sees aggregates only, configurable); the warehouse path inherits E1's partitioning; the RFC documents the posture |
| The UI drifts from the API (screens with no endpoint) | Rule 1 enforced in CI: the UI asset test greps for fetch targets and asserts each appears in the OpenAPI schema |
| What-if becomes shadow production | What-if runs are registry-recorded like any run (there is no off-the-books mode), visibly tagged with their base fingerprint, and excluded from approved-mode reporting unless approved (D2) |
| Scope creep toward a BI suite | §2's "declined" list is binding: explorer + warehouse path, no dashboard builder. A request for a custom dashboard is answered with the E1 DuckDB/Power BI recipe |

---

*Sources: competitive-landscape.md §7 (discovery of competitor engine
exposure, August 2026); competitive-execution-plan.md (E-workstream,
protocol §1, sequencing §10); RFC-003 (registry), RFC-030 (model docs and
dependency graph), RFC-031 (REST API), RFC-032 (demo UI and catalogue);
`engine/api/app.py` route inventory at commit `ebd8622`.*
